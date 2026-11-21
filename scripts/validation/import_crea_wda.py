#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Importa SOLO le etichette 'Hail' esatte da CREA WDA (Dataset_Weather_related_disasters_agriculture).

Fonte: Pontrandolfi & Pepe, CREA, Zenodo 10.5281/zenodo.14499762 (v1.2,
DOI dato 10.5281/zenodo.20134419 per v1.3, CSV byte-identico), CC BY 4.0.

ONESTA': il dataset ha SOLO la data (giorno/mese/anno), NESSUNA coordinate e
NESSUNA ora. Ogni campo mancante viene scritto come null con un *_null_reason
esplicito. Nessun valore e' inventato: lat/lon/ora/intensita' restano null.

Uso:
    python scripts/validation/import_crea_wda.py --csv <file> --out <jsonl> [--selftest]

Il CSV viene letto con encoding cp1252, separatore ';' e CRLF. Lo sha256 del
file viene verificato contro il valore atteso: se differisce il programma
esce con errore e NON scrive nulla.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

CSV_EXPECTED_SHA256 = "846AA6310CD25F1661D825AE8EF30AAA1749C0029EBED181F0FFEDE0A8CB4B6F"
CSV_EXPECTED_BYTES = 92394
CSV_EXPECTED_RECORDS = 1159  # righe dati, escluso l'header

SOURCE = "CREA WDA (Weather related disasters in agriculture)"
SOURCE_URL = "https://doi.org/10.5281/zenodo.14499762"
SOURCE_URL_V13 = "https://doi.org/10.5281/zenodo.20134419"
LICENSE = "CC BY 4.0"
ATTRIBUTION = (
    "Pontrandolfi G., Pepe M. (CREA), Dataset of Weather related disasters in "
    "agriculture, Zenodo 10.5281/zenodo.14499762; Data in Brief "
    "10.1016/j.dib.2025.111323; licence CC BY 4.0"
)
DATASET_VERSION = "1.2 (v1.3 DOI 10.5281/zenodo.20134419: CSV byte-identico)"

HAIL_EXACT = "Hail"  # etichetta ESATTA: 'Hail and tornado' NON e' importato

# Motivi di null, dichiarati una volta per tutte.
HOUR_NULL_REASON = "source_has_only_calendar_date_no_hour"
GEO_NULL_REASON = "source_has_no_lat_lon_only_province_name_and_code"
INTENSITY_NULL_REASON = "source_has_no_severity_or_intensity_field"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def to_iso_date(value: str) -> Optional[str]:
    """'gg/mm/aaaa' -> 'aaaa-mm-dd'. Ritorna None se vuoto."""
    value = (value or "").strip()
    if not value:
        return None
    d = dt.datetime.strptime(value, "%d/%m/%Y").date()
    return d.isoformat()


def read_csv(path: str) -> Tuple[List[Dict[str, str]], Counter]:
    """Legge il CSV cp1252 con ';' e gestisce CRLF. Ritorna (righe, conteggio_eventi)."""
    with open(path, "r", encoding="cp1252", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        fieldnames = [c.strip() for c in (reader.fieldnames or [])]
        # Alcune righe possono avere spazi dopo il separatore: li rimuoviamo.
        rows: List[Dict[str, str]] = []
        events: Counter = Counter()
        for raw in reader:
            row = {}
            for key, value in raw.items():
                if key is None:
                    continue
                clean_key = key.strip()
                clean_val = value.strip() if isinstance(value, str) else ""
                row[clean_key] = clean_val
            rows.append(row)
            events[row.get("Extreme weather event leading to disaster", "")] += 1
    if fieldnames and fieldnames[0] != "ID":
        raise ValueError(f"header CSV inatteso: {fieldnames[:3]}")
    return rows, events


def build_records(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("Extreme weather event leading to disaster", "") != HAIL_EXACT:
            continue
        date_start = to_iso_date(r.get("Start date", ""))
        date_end = to_iso_date(r.get("End date", ""))
        duration_days = None
        if date_start is not None:
            end = dt.date.fromisoformat(date_end) if date_end else dt.date.fromisoformat(date_start)
            duration_days = (end - dt.date.fromisoformat(date_start)).days
        source_id = r.get("ID", "")
        province_code = r.get("CodProv", "")
        province_name = r.get("Den_prov", "")
        region_code = r.get("CodReg", "")
        damages = {
            "productions": r.get("Damages on productions", ""),
            "farm_structures": r.get("Damages on farm structures", ""),
            "infrastructures": r.get("Damages on infrastructures", ""),
        }
        dedup_key = f"crea-wda|{DATASET_VERSION.split(' ')[0]}|Hail|{date_start}|{province_code}|{source_id}"
        rec: Dict[str, Any] = {
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "source_url_v13": SOURCE_URL_V13,
            "license": LICENSE,
            "attribution": ATTRIBUTION,
            "source_id": source_id,
            "dataset_version": DATASET_VERSION,
            "event_type": HAIL_EXACT,
            "date_start": date_start,
            "date_end": date_end,
            "hour": None,
            "hour_null_reason": HOUR_NULL_REASON,
            "lat": None,
            "lon": None,
            "geo_null_reason": GEO_NULL_REASON,
            "province_code": province_code,
            "province_name": province_name,
            "region_code": region_code,
            "intensity": None,
            "intensity_null_reason": INTENSITY_NULL_REASON,
            "damages": damages,
            "duration_days": duration_days,
            "dedup_key": dedup_key,
            "year": r.get("Year", ""),
        }
        records.append(rec)
    return records


def write_jsonl(records: List[Dict[str, Any]], out_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def verify(csv_path: str, rows: List[Dict[str, str]], records: List[Dict[str, Any]]) -> List[str]:
    """Ritorna lista di errori di coerenza (vuota = tutto OK)."""
    errors: List[str] = []
    actual_sha = sha256_file(csv_path)
    if actual_sha != CSV_EXPECTED_SHA256:
        errors.append(f"sha256 CSV {actual_sha} != atteso {CSV_EXPECTED_SHA256}")
    if os.path.getsize(csv_path) != CSV_EXPECTED_BYTES:
        errors.append(f"byte CSV {os.path.getsize(csv_path)} != atteso {CSV_EXPECTED_BYTES}")
    if len(rows) != CSV_EXPECTED_RECORDS:
        errors.append(f"record CSV {len(rows)} != attesi {CSV_EXPECTED_RECORDS}")
    if len(records) != 75:
        errors.append(f"record Hail {len(records)} != attesi 75")
    if any(r["lat"] is not None or r["lon"] is not None for r in records):
        errors.append("trovate coordinate non-null (vietato)")
    if any(r["hour"] is not None for r in records):
        errors.append("trovate ore non-null (vietato)")
    if any(r["intensity"] is not None for r in records):
        errors.append("trovata intensita' non-null (vietato)")
    if any(not r["hour_null_reason"] or not r["geo_null_reason"] or not r["intensity_null_reason"] for r in records):
        errors.append("manca un *_null_reason obbligatorio")
    keys = [r["dedup_key"] for r in records]
    if len(set(keys)) != len(keys):
        errors.append("dedup_key duplicati")
    return errors


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", default=CSV_PATH_DEFAULT, help="percorso del CSV CREA WDA")
    p.add_argument("--out", default="data/validation/crea_wda_hail_labels.jsonl", help="JSONL di output")
    p.add_argument("--selftest", action="store_true", help="esegue i controlli di coerenza e stampa il report")
    args = p.parse_args(argv)

    if not os.path.isfile(args.csv):
        print(f"ERRORE: CSV non trovato: {args.csv}", file=sys.stderr)
        return 2
    actual_sha = sha256_file(args.csv)
    if actual_sha != CSV_EXPECTED_SHA256:
        print(
            f"ERRORE: sha256 del CSV non corrispondente.\n  atteso: {CSV_EXPECTED_SHA256}\n  trovato: {actual_sha}\n"
            "  Nessun output scritto.",
            file=sys.stderr,
        )
        return 3

    rows, events = read_csv(args.csv)
    records = build_records(rows)

    print(f"CSV: {args.csv}")
    print(f"sha256: {actual_sha} (verificato OK), byte: {os.path.getsize(args.csv)}, record: {len(rows)}")
    print("Conteggio eventi per tipo (13 tipi):")
    for name, count in sorted(events.items(), key=lambda kv: (-kv[1], kv[0])):
        mark = " <== IMPORTATO" if name == HAIL_EXACT else ""
        print(f"  {name}: {count}{mark}")
    print(f"Importati SOLO record con evento esatto 'Hail': {len(records)}")

    errors = verify(args.csv, rows, records)
    if args.selftest:
        print("--- SELFTEST ---")
        if errors:
            for e in errors:
                print(f"  FALLITO: {e}")
            return 1
        checks = [
            "75 record Hail importati",
            "0 coordinate inventate",
            "0 ore inventate",
            "0 intensita' inventate",
            "sha256 CSV corretto",
            "nessun dedup_key duplicato",
        ]
        for c in checks:
            print(f"  OK: {c}")
        print("SELFTEST PASS")
        return 0

    if errors:
        for e in errors:
            print(f"ERRORE: {e}", file=sys.stderr)
        return 1
    write_jsonl(records, args.out)
    print(f"Scritti {len(records)} record in {args.out}")
    return 0


CSV_PATH_DEFAULT = (
    r"C:\Users\aless\AppData\Local\Temp\opencode\crea_wda"
    r"\Dataset_Weather_related_disasters_agriculture_v1.2.csv"
)

if __name__ == "__main__":
    raise SystemExit(main())
