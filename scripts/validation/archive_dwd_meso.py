#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Archivio dei mesocicloni DWD (prodotto MCD) - bootstrap e aggiornamento.

PERCHE' ESISTE
--------------
Il DWD pubblica un XML per ogni scansione radar (ogni 5 minuti) in
``https://opendata.dwd.de/weather/radar/mesocyclones/``. La directory conserva
solo le ultime ~46 ore: senza un archivio il dato sparisce per sempre. Questo
script scarica i file presenti, estrae UNA riga per detection e la accoda a un
JSONL compatto, cosi' il workflow ``meso-archive.yml`` puo' conservare la
storia oltre la finestra di retention del DWD.

FONTE E LICENZA
---------------
DWD Open Data, prodotto "Mesyklonenerkennung" (MCD), CC BY 4.0, accesso
anonimo. Documentazione: DWD "Dokumentation Mesozyklonenerkennung" (07.2022).
Nessun dato viene inventato: se un campo opzionale manca il valore e' ``null``
e il motivo finisce in ``null_reasons``.

FORMATO XML (verificato su file reali)
-------------------------------------
::

    <nowcast-data>
      <radar-stations>ASB,BOO,ROS,...</radar-stations>
      <event ID="24">
        <time time-coordinate="UTC">2021-07-08T10:40:00</time>
        <location><area><ellipse><moving-point>
          <latitude>49.723716</latitude>
          <longitude>12.113914</longitude>
          <polar_motion><speed units="km/h">0.0</speed></polar_motion>
        </moving-point>
        <major_axis units="km">8.32</major_axis>
        <minor_axis units="km">8.32</minor_axis>
        <orientation units="degrees true">0</orientation>
        </ellipse></area></location>
        <nowcast-parameters>
          <mesocyclone_shear_mean units="m/s/km">3.131289</mesocyclone_shear_mean>
          ...
          <elevations><elevation site="eis" units="&#176;">3.5,8.0,17.0</elevation></elevations>
          <meso_intensity>3</meso_intensity>
        </nowcast-parameters>
      </event>
    </nowcast-data>

Un run senza detection e' la sola lista stazioni (173 byte): produce 0 righe
ma il file viene comunque marcato come "gia' visto" nel ledger, altrimenti
sarebbe riscaricato a ogni esecuzione.

ARCHIVI SCRITTI
---------------
``data/validation/dwd_mesocyclones.jsonl``
    Una riga JSON per detection. Chiave di dedup: ``(time, lat, lon)``.
``data/validation/dwd_mesocyclones_meta.json``
    Riepilogo dell'archivio: ``file_scanned``, ``files_with_detections``,
    ``detections``, ``first_time``, ``last_time``. Riscritto solo se il
    contenuto logico cambia (niente churn di commit sui run NO-OP).
``data/validation/dwd_mesocyclones_seen.json``
    Ledger dei nomi file gia' processati (anche quelli senza detection).
    Potato a 7 giorni: la finestra DWD e' ~46 ore, quindi un nome potato non
    puo' piu' ricomparire. Se ricomparisse, il dedup del JSONL lo copre.

USO
---
::

    python scripts/validation/archive_dwd_meso.py            # incrementale
    python scripts/validation/archive_dwd_meso.py --dry-run  # nessuna scrittura
    python scripts/validation/archive_dwd_meso.py --source-dir DIR  # replay locale

In ``--source-dir`` il ledger viene ignorato (serve per replay/test su file
gia' scaricati); il dedup contro il JSONL resta attivo.

Se il listing non risponde lo script esce 0 con un messaggio: un guasto del
server pubblico non deve far fallire il workflow (il prossimo run riprova).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

BASE_URL = "https://opendata.dwd.de/weather/radar/mesocyclones/"
USER_AGENT = "MeteoRisk-Light/1.0 (DWD open-data archiver; anonymous)"
MAX_WORKERS = 4  # server pubblico: mai piu' di 4 download in parallelo
RETRY_ATTEMPTS = 3
RETRY_BASE_SLEEP_S = 1.5
DOWNLOAD_TIMEOUT_S = 30
LISTING_TIMEOUT_S = 60
SEEN_RETENTION_DAYS = 7
EMPTY_FILE_MAX_BYTES = 200  # 173 B = sola lista stazioni, nessun <event>

JSONL_NAME = "dwd_mesocyclones.jsonl"
META_NAME = "dwd_mesocyclones_meta.json"
SEEN_NAME = "dwd_mesocyclones_seen.json"

# (chiave output, tag XML, tipo). L'ordine e' quello del documento DWD.
NUMERIC_FIELDS: tuple[tuple[str, str, type], ...] = (
    ("shear_mean", "mesocyclone_shear_mean", float),
    ("shear_max", "mesocyclone_shear_max", float),
    ("momentum_mean", "mesocyclone_momentum_mean", float),
    ("momentum_max", "mesocyclone_momentum_max", float),
    ("diameter_km", "mesocyclone_diameter", float),
    ("diameter_equivalent_km", "mesocyclone_diameter_equivalent", float),
    ("top_km", "mesocyclone_top", float),
    ("base_km", "mesocyclone_base", float),
    ("echotop_km", "mesocyclone_echotop", float),
    ("vil", "mesocyclone_vil", float),
    ("shear_vectors", "mesocyclone_shear_vectors", int),
    ("shear_features", "mesocyclone_shear_features", int),
    ("mean_dbz", "mean_dbz", float),
    ("max_dbz", "max_dbz", float),
    ("velocity_max", "mesocyclone_velocity_max", float),
    ("velocity_rotational_max", "mesocyclone_velocity_rotational_max", float),
    ("velocity_rotational_mean", "mesocyclone_velocity_rotational_mean", float),
    ("velocity_rotational_max_closest_to_ground",
     "mesocyclone_velocity_rotational_max_closest_to_ground", float),
    ("intensity", "meso_intensity", int),
)

_FILE_RE = re.compile(
    r'href="(meso_\d{8}_\d{4}\.xml)">[^<]+</a>\s+'
    r"(\d{2}-\w{3}-\d{4} \d{2}:\d{2}:\d{2})\s+(\d+)"
)
_NAME_RE = re.compile(r"meso_(\d{8})_(\d{4})\.xml$")


# ---------------------------------------------------------------------------
# utilita' di parsing
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    """Nome locale del tag (tollera un eventuale namespace)."""
    return tag.rsplit("}", 1)[-1]


def _find(parent: Any, tag: str) -> Any:
    """Primo discendente con quel nome locale, None se assente."""
    for el in parent.iter():
        if _local(el.tag) == tag:
            return el
    return None


def _text(parent: Any, tag: str) -> str | None:
    el = _find(parent, tag)
    if el is None or el.text is None:
        return None
    return el.text.strip()


def _number(parent: Any, tag: str, cast: type = float) -> tuple[Any, str | None]:
    """Valore numerico del primo tag trovato, oppure (None, motivo)."""
    el = _find(parent, tag)
    if el is None:
        return None, "missing"
    raw = (el.text or "").strip()
    if not raw:
        return None, "empty"
    try:
        return cast(raw), None
    except (TypeError, ValueError):
        return None, "invalid_number"


def _iso_utc(raw: str) -> str:
    """Normalizza un timestamp ISO (senza fuso = UTC) in ``...Z``."""
    dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_elevations(parent: Any) -> list[dict[str, Any]]:
    """``<elevation site="eis">3.5,8.0</elevation>`` -> lista strutturata."""
    out: list[dict[str, Any]] = []
    node = _find(parent, "elevations")
    if node is None:
        return out
    for el in node.iter():
        if _local(el.tag) != "elevation":
            continue
        site = (el.attrib.get("site") or "").strip().upper() or None
        deg: list[float] = []
        for chunk in (el.text or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                deg.append(float(chunk))
            except ValueError:
                continue
        out.append({"site": site, "deg": deg})
    return out


@dataclass
class ParseStats:
    """Contatori di un singolo file (per il riepilogo e l'audit)."""

    events: int = 0
    dropped: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str) -> None:
        self.dropped += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1


def parse_meso_xml(
    data: bytes | str,
    source_file: str,
    archived_at: str | None = None,
) -> tuple[list[str], list[dict[str, Any]], ParseStats]:
    """Estrae ``(radar_stations, records, stats)`` da un XML MCD.

    ``records`` ha una voce per ``<event>`` con tutti i campi numerici del
    documento; i campi opzionali mancanti sono ``None`` e il motivo e' in
    ``null_reasons``. Un evento senza ``time``/``latitude``/``longitude`` non
    e' archiviabile (non avrebbe chiave di dedup) e viene scartato, contato in
    ``stats.drop_reasons``.
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(data)

    stations: list[str] = []
    stations_el = _find(root, "radar-stations")
    if stations_el is not None and stations_el.text:
        stations = [
            s.strip().upper()
            for s in stations_el.text.split(",")
            if s.strip()
        ]

    archived_at = archived_at or utc_now_iso()
    records: list[dict[str, Any]] = []
    stats = ParseStats()

    for event in root.iter():
        if _local(event.tag) != "event":
            continue
        stats.events += 1

        raw_time = _text(event, "time")
        if not raw_time:
            stats.drop("missing_time")
            continue
        try:
            ts = _iso_utc(raw_time)
        except ValueError:
            stats.drop("invalid_time")
            continue

        lat_el = _find(event, "latitude")
        lon_el = _find(event, "longitude")
        lat_raw = (lat_el.text or "").strip() if lat_el is not None else ""
        lon_raw = (lon_el.text or "").strip() if lon_el is not None else ""
        if not lat_raw:
            stats.drop("missing_latitude")
            continue
        if not lon_raw:
            stats.drop("missing_longitude")
            continue
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
        except ValueError:
            stats.drop("invalid_latlon")
            continue

        rec: dict[str, Any] = {
            "time": ts,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
        }
        null_reasons: dict[str, str] = {}

        # L'ID e' un attributo, non un elemento: lo leggo direttamente.
        rec["event_id"] = (
            int(event.attrib["ID"]) if event.attrib.get("ID", "").isdigit() else None
        )
        if rec["event_id"] is None:
            null_reasons["event_id"] = "missing"

        for key, tag, cast in NUMERIC_FIELDS:
            value, reason = _number(event, tag, cast)
            rec[key] = value
            if reason:
                null_reasons[key] = reason

        # Geometria dell'ellisse e moto (facoltativi ma numerici).
        for key, tag, cast in (
            ("major_axis_km", "major_axis", float),
            ("minor_axis_km", "minor_axis", float),
            ("orientation_deg", "orientation", float),
            ("polar_motion_kmh", "speed", float),
        ):
            value, reason = _number(event, tag, cast)
            rec[key] = value
            if reason:
                null_reasons[key] = reason

        rec["elevations"] = _parse_elevations(event)
        rec["radar_stations"] = stations
        rec["source_file"] = source_file
        rec["archived_at"] = archived_at
        if null_reasons:
            rec["null_reasons"] = null_reasons
        records.append(rec)

    return stations, records, stats


def event_key(rec: dict[str, Any]) -> tuple[str, float, float]:
    """Chiave di dedup: stesso istante + stessa posizione = stessa detection."""
    return (rec["time"], round(float(rec["lat"]), 6), round(float(rec["lon"]), 6))


def split_new(
    records: Iterable[dict[str, Any]],
    existing_keys: set[tuple[str, float, float]],
) -> tuple[list[dict[str, Any]], int]:
    """Tiene solo i record con chiave nuova; aggiorna ``existing_keys``."""
    out: list[dict[str, Any]] = []
    duplicates = 0
    for rec in records:
        key = event_key(rec)
        if key in existing_keys:
            duplicates += 1
            continue
        existing_keys.add(key)
        out.append(rec)
    return out, duplicates


# ---------------------------------------------------------------------------
# stato su disco
# ---------------------------------------------------------------------------

@dataclass
class ArchiveState:
    detections: int = 0
    keys: set[tuple[str, float, float]] = field(default_factory=set)
    source_files: set[str] = field(default_factory=set)
    first_time: str | None = None
    last_time: str | None = None
    corrupt_lines: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_jsonl(path: str) -> ArchiveState:
    """Legge il JSONL esistente e ne calcola gli aggregati (senza tenerlo in RAM)."""
    state = ArchiveState()
    if not os.path.exists(path):
        return state
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                key = event_key(rec)
            except (ValueError, TypeError, KeyError):
                state.corrupt_lines += 1
                continue
            state.detections += 1
            state.keys.add(key)
            src = rec.get("source_file")
            if src:
                state.source_files.add(src)
            ts = rec.get("time")
            if ts:
                if state.first_time is None or ts < state.first_time:
                    state.first_time = ts
                if state.last_time is None or ts > state.last_time:
                    state.last_time = ts
    return state


def append_jsonl(path: str, records: list[dict[str, Any]]) -> int:
    """Accoda i record in ordine temporale, JSON compatto, LF, senza BOM."""
    if not records:
        return 0
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    ordered = sorted(records, key=lambda r: (r["time"], r["lat"], r["lon"]))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for rec in ordered:
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")
    return len(ordered)


def load_seen(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return set()
    files = data.get("files", []) if isinstance(data, dict) else data
    return {str(x) for x in files if isinstance(x, str)}


def prune_seen(names: set[str], now: datetime | None = None) -> set[str]:
    """Tiene i nomi entro ``SEEN_RETENTION_DAYS`` (la finestra DWD e' ~46 h)."""
    now = now or datetime.now(timezone.utc)
    kept: set[str] = set()
    for name in names:
        match = _NAME_RE.match(name)
        if not match:
            continue
        try:
            dt = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M")
        except ValueError:
            continue
        dt = dt.replace(tzinfo=timezone.utc)
        if (now - dt).total_seconds() <= SEEN_RETENTION_DAYS * 86400:
            kept.add(name)
    return kept


def _read_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def _write_json(path: str, payload: Any) -> bool:
    """Scrive solo se il contenuto (escluso ``updated_at``) cambia."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    old = _read_json(path)
    if isinstance(old, dict) and isinstance(payload, dict):
        old_cmp = {k: v for k, v in old.items() if k != "updated_at"}
        new_cmp = {k: v for k, v in payload.items() if k != "updated_at"}
        if old_cmp == new_cmp:
            return False
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return True


def build_meta(
    state: ArchiveState,
    files_scanned: int,
    source: str = BASE_URL,
    updated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "license": "CC BY 4.0 (DWD Open Data)",
        "product": "MCD - Mesozyklonenerkennung (XML ogni 5 minuti)",
        "files_scanned": files_scanned,
        "files_with_detections": len(state.source_files),
        "detections": state.detections,
        "first_time": state.first_time,
        "last_time": state.last_time,
        "corrupt_lines": state.corrupt_lines,
        "updated_at": updated_at or utc_now_iso(),
    }


# ---------------------------------------------------------------------------
# rete
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ListingEntry:
    name: str
    size: int
    mtime: str

    @property
    def timestamp(self) -> datetime | None:
        match = _NAME_RE.match(self.name)
        if not match:
            return None
        try:
            return datetime.strptime(
                match.group(1) + match.group(2), "%Y%m%d%H%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def fetch_listing(base_url: str = BASE_URL, timeout: int = LISTING_TIMEOUT_S) -> list[ListingEntry]:
    """Legge il listing HTML del DWD (nome, mtime, dimensione)."""
    req = urllib.request.Request(base_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", "replace")
    entries: dict[str, ListingEntry] = {}
    for name, mtime, size in _FILE_RE.findall(html):
        entries[name] = ListingEntry(name=name, size=int(size), mtime=mtime)
    return sorted(entries.values(), key=lambda e: e.name)


def _fetch_one(name: str, base_url: str = BASE_URL) -> tuple[str, bytes | None, str | None]:
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(base_url + name, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp:
                return name, resp.read(), None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BASE_SLEEP_S * attempt)
    return name, None, last_error


def download_all(
    names: list[str],
    workers: int = MAX_WORKERS,
    base_url: str = BASE_URL,
) -> list[tuple[str, bytes | None, str | None]]:
    """Scarica in parallelo con tetto rigido a ``MAX_WORKERS``."""
    workers = max(1, min(int(workers), MAX_WORKERS))
    if not names:
        return []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda n: _fetch_one(n, base_url), names))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _default_paths(repo_root: str) -> tuple[str, str, str]:
    base = os.path.join(repo_root, "data", "validation")
    return (
        os.path.join(base, JSONL_NAME),
        os.path.join(base, META_NAME),
        os.path.join(base, SEEN_NAME),
    )


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root = _repo_root()
    jsonl, meta, seen = _default_paths(root)
    parser = argparse.ArgumentParser(description="Archivio dei mesocicloni DWD (MCD).")
    parser.add_argument("--jsonl", default=jsonl, help="output JSONL (default: %(default)s)")
    parser.add_argument("--meta", default=meta, help="file di riepilogo (default: %(default)s)")
    parser.add_argument("--seen", default=seen, help="ledger dei file processati (default: %(default)s)")
    parser.add_argument("--source-dir", default=None,
                        help="usa XML locali invece del listing DWD (replay/test)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"download paralleli (max {MAX_WORKERS}; default %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="non scrive nulla su disco")
    return parser.parse_args(argv)


def _iter_source_dir(path: str) -> list[ListingEntry]:
    entries: list[ListingEntry] = []
    for name in sorted(os.listdir(path)):
        if not _NAME_RE.match(name):
            continue
        full = os.path.join(path, name)
        if not os.path.isfile(full):
            continue
        entries.append(ListingEntry(name=name, size=os.path.getsize(full), mtime=""))
    return entries


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_ts = utc_now_iso()

    state = scan_jsonl(args.jsonl)
    seen = load_seen(args.seen)
    existing_keys = set(state.keys)

    if args.source_dir:
        entries = _iter_source_dir(args.source_dir)
        if not entries:
            print(f"[WARN] nessun XML meso_*.xml in {args.source_dir}: niente da fare.")
            return 0
    else:
        try:
            entries = fetch_listing()
        except Exception as exc:  # listing giu': non far fallire il workflow
            print(f"[WARN] listing DWD non disponibile ({type(exc).__name__}: {exc}); "
                  f"exit 0, archivio invariato.")
            return 0
        if not entries:
            print("[WARN] listing DWD vuoto o non interpretabile; exit 0, archivio invariato.")
            return 0

    if args.source_dir:
        pending = entries  # replay locale: il ledger non si applica
    else:
        pending = [e for e in entries if e.name not in seen]

    print(f"[INFO] listing={len(entries)} file, gia' visti={len(entries) - len(pending)}, "
          f"da scaricare={len(pending)}")
    if entries:
        first, last = entries[0], entries[-1]
        if first.timestamp and last.timestamp:
            hours = (last.timestamp - first.timestamp).total_seconds() / 3600.0 + 5.0 / 60.0
            print(f"[INFO] finestra={first.name} .. {last.name} (~{hours:.2f} h)")

    if args.source_dir:
        downloaded = []
        for entry in pending:
            try:
                with open(os.path.join(args.source_dir, entry.name), "rb") as fh:
                    downloaded.append((entry.name, fh.read(), None))
            except OSError as exc:
                downloaded.append((entry.name, None, f"{type(exc).__name__}: {exc}"))
    else:
        downloaded = download_all([e.name for e in pending], workers=args.workers)
    ok_names: set[str] = set()
    new_records: list[dict[str, Any]] = []
    duplicates = 0
    parse_errors: list[str] = []
    events_total = 0
    events_dropped = 0
    files_with_detections_run = 0

    for name, payload, error in downloaded:
        if payload is None:
            parse_errors.append(f"{name}: download {error}")
            continue
        try:
            _stations, records, stats = parse_meso_xml(payload, name, archived_at=run_ts)
        except Exception as exc:  # XML malformato: conta e prosegui
            parse_errors.append(f"{name}: parse {type(exc).__name__}: {exc}")
            continue
        ok_names.add(name)
        events_total += stats.events
        events_dropped += stats.dropped
        fresh, dup = split_new(records, existing_keys)
        duplicates += dup
        new_records.extend(fresh)
        if records:
            files_with_detections_run += 1

    seen_after = prune_seen(seen | ok_names) if not args.source_dir else seen

    if args.source_dir:
        files_scanned = len(entries)
    else:
        files_scanned = sum(1 for e in entries if e.name in seen_after)

    state.detections += len(new_records)
    state.source_files.update(r["source_file"] for r in new_records)
    for rec in new_records:
        ts = rec["time"]
        if state.first_time is None or ts < state.first_time:
            state.first_time = ts
        if state.last_time is None or ts > state.last_time:
            state.last_time = ts

    meta = build_meta(state, files_scanned=files_scanned, updated_at=run_ts)

    if not args.dry_run:
        written = append_jsonl(args.jsonl, new_records)
        if not os.path.exists(args.jsonl):
            # Anche un archivio senza detection deve esistere: il workflow lo
            # committa con un glob e un file assente farebbe fallire il git add.
            os.makedirs(os.path.dirname(args.jsonl) or ".", exist_ok=True)
            with open(args.jsonl, "a", encoding="utf-8"):
                pass
        if args.source_dir:
            # Replay: non toccare il ledger (i file locali non sono la finestra DWD).
            pass
        else:
            _write_json(args.seen, {"version": 1, "files": sorted(seen_after)})
        _write_json(args.meta, meta)
    else:
        written = len(new_records)

    print(f"[INFO] eventi={events_total} scartati={events_dropped} "
          f"duplicati={duplicates} nuovi={written} "
          f"file_con_detection={files_with_detections_run}")
    for err in parse_errors[:20]:
        print(f"[WARN] {err}")
    if len(parse_errors) > 20:
        print(f"[WARN] ... e altri {len(parse_errors) - 20} errori")
    if files_scanned < len(entries) and not args.source_dir:
        print(f"[WARN] file_scanned={files_scanned} su {len(entries)}: "
              f"{len(entries) - files_scanned} file non ancora processati")
    print("[INFO] meta: " + json.dumps(meta, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
