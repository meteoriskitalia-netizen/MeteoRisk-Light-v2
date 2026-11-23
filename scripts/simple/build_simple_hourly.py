#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Payload orario COMPATTO per la vista "Simple" — file AGGIUNTIVO.

PERCHE' UN FILE SEPARATO (stesso vincolo di build_weekly.py)
-----------------------------------------------------------
`data/latest/meteorisk-points.json` (schema 4, 257 punti, 51 campi orari per
punto) alimenta la Pro e NON va assottigliato. La Simple ha bisogno di 168 ore
(7 giorni) x 107 province x 5 campi: leggerlo dal client costerebbe ~9,7 MB.
Questo modulo produce `data/latest/simple_hourly.json` con i SOLI cinque campi
richiesti, prendendoli dal punto GIA' SCELTO per la provincia
(`selected_point.id` in `meteorisk-provinces.json`).

FONTI DELLE 168 ORE (due file, nessuna cucitura inventata)
----------------------------------------------------------
  - ore 0..71  -> `meteorisk-points.json` (dataset BASE, 72 ore);
  - ore 72..167 -> `hourly_extended.json` (schema `meteorisk.hourly-ext/1`,
    96 ore), SOLO per lo STESSO `point_id` del `selected_point` e SOLO per il
    modello `best_match` (il modello dichiarato in `model`).
Se `hourly_extended.json` manca, il payload resta a 72 ore con `hours` REALE
(72 etichette, 72 righe): nessun buco riempito a mano. Se l'estensione c'e' ma
una provincia non vi compare, le sue ore 72..167 sono righe `[null x5]` con
`extended_reason` dichiarato nell'entry (valori null, non inventati).

COSA PRODUCE (schema `simple-hourly/1`)
---------------------------------------
  {
    "schema": "simple-hourly/1",
    "generated_at": "...",            # copiato da meteorisk-provinces.json
    "day0": "2026-11-23",
    "timezone": "Europe/Rome",
    "model": "best_match",
    "hours": ["2026-11-23T00:00", ... 168 ...],
    "fields": ["t", "p", "pp", "w", "c"],
    "units": {"t": "C", "p": "mm", "pp": "%", "w": "km/h", "c": "wmo"},
    "provinces": {
      "RM": {"id": 91, "h": [[16.9, 0.0, 0, 5.0, 2], ... 168 righe ...]},
      ...
    }
  }

  t  = temperature_2m              (gradi C, 1 decimale)
  p  = precipitation               (mm, 1 decimale)
  pp = precipitation_probability   (% intero)
  w  = windspeed_10m               (km/h, 1 decimale)
  c  = weathercode                 (WMO, intero)

REGOLE DI ONESTA' (vincolo #207)
--------------------------------
- Nessun valore inventato: un `None` del dataset resta `null` nel payload.
- Nessuna aggregazione: si prende il punto di `selected_point` della provincia,
  esattamente quello che la Pro usa per i riepiloghi giornalieri.
- Se un campo orario manca del tutto nel dataset, il payload NON viene scritto
  con zeri: il processo esce 2 e lo dice (il workflow ha `continue-on-error`).

QUANDO NON SCRIVE NULLA
-----------------------
- `meteorisk-points.json` o `meteorisk-provinces.json` assenti: exit 0 con
  avviso (il dataset non c'e' ancora; non e' un guasto del builder).
- Schema inatteso (manca `points`/`provinces`, campi orari assenti): exit 2,
  nessun file toccato (scrittura atomica via file temporaneo).

USO
---
  python scripts/simple/build_simple_hourly.py
  python scripts/simple/build_simple_hourly.py --points <path> \
      --provinces <path> --out <path> --json-report
"""

import argparse
import datetime as _dt
import gzip
import io
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_DEFAULT_POINTS = os.path.join(_ROOT, "data", "latest", "meteorisk-points.json")
_DEFAULT_PROVINCES = os.path.join(_ROOT, "data", "latest", "meteorisk-provinces.json")
_DEFAULT_EXTENDED = os.path.join(_ROOT, "data", "latest", "hourly_extended.json")
_DEFAULT_OUT = os.path.join(_ROOT, "data", "latest", "simple_hourly.json")

SCHEMA = "simple-hourly/1"
EXT_SCHEMA = "meteorisk.hourly-ext/1"
MODEL = "best_match"
BASE_HOURS = 72
EXTENDED_HOURS = 96
# Orizzonte TARGET del payload: 168 ore (7 giorni). Effettivo = 72 se
# l'estensione non e' disponibile (allora `hours` ha 72 etichette reali).
HOURS = BASE_HOURS + EXTENDED_HOURS
# (chiave compatta, campo orario del dataset, decimali)
FIELDS = (
    ("t", "temperature_2m", 1),
    ("p", "precipitation", 1),
    ("pp", "precipitation_probability", 0),
    ("w", "windspeed_10m", 1),
    ("c", "weathercode", 0),
)
FIELD_KEYS = [f[0] for f in FIELDS]
UNITS = {"t": "C", "p": "mm", "pp": "%", "w": "km/h", "c": "wmo"}
# Budget dichiarato: oltre questo limite il payload NON e' "compatto" e la
# build si ferma invece di pubblicare un blob che il client non puo' caricare.
# 168 ore x 107 province x 5 campi stanno in ~340 KB raw; il gzip compresso
# (misurato sul dataset reale) resta intorno ai 30 KB perche' le serie orarie
# sono altamente ridondanti: il tetto e' raw, il gzip e' solo dichiarato.
RAW_BUDGET_BYTES = 400 * 1024


class SimpleHourlyError(RuntimeError):
    """Errore di contratto che NON deve produrre un file."""


def _load_json(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _round(value, decimals):
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if decimals == 0:
        return int(round(num))
    return round(num, decimals)


def build_hours(day0, hours=HOURS):
    """Etichette ISO locali delle `hours` ore a partire da day0 (00:00 -> h-1)."""
    try:
        start = _dt.datetime.strptime(day0, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise SimpleHourlyError("day0 non valido: %r" % (day0,))
    return [(start + _dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
            for i in range(hours)]


def extended_rows_for(extended_doc, sigla, point_id, model=MODEL):
    """Righe 72..167 dell'estensione per (provincia, point_id, modello).

    Ritorna (rows, motivo): `rows` e' la lista di 96 righe oppure None con il
    motivo dichiarato. Il `point_id` DEVE coincidere con il `selected_point`
    della provincia: un'estensione costruita su un altro punto non si usa."""
    if not isinstance(extended_doc, dict):
        return None, "extended_absent"
    ext_provs = extended_doc.get("provinces") or {}
    records = (ext_provs.get(sigla) or {}).get("points") or []
    for rec in records:
        if rec.get("model") != model:
            continue
        if rec.get("point_id") != point_id:
            return None, "extended_point_mismatch"
        rows = rec.get("h")
        if not isinstance(rows, list) or len(rows) != EXTENDED_HOURS:
            return None, rec.get("null_reason") or "extended_rows_missing"
        return rows, None
    return None, "extended_model_missing"


def build_payload(points_doc, provinces_doc, extended_doc=None):
    points = points_doc.get("points")
    provinces = provinces_doc.get("provinces")
    if not isinstance(points, list) or not points:
        raise SimpleHourlyError("meteorisk-points.json senza lista `points`")
    if not isinstance(provinces, list) or not provinces:
        raise SimpleHourlyError("meteorisk-provinces.json senza lista `provinces`")

    by_id = {}
    for point in points:
        if isinstance(point, dict) and point.get("id") is not None:
            by_id[point["id"]] = point

    day0 = points_doc.get("day0") or provinces_doc.get("day0")
    if not day0:
        # day0 e' indispensabile per le etichette orarie: non lo si inventa.
        raise SimpleHourlyError("day0 assente sia in points sia in provinces")

    # L'estensione si usa SOLO se e' dello stesso asse temporale: la sua ora 0
    # deve essere l'ora 72 del payload (day0 + 72 h). Un file di un altro
    # giorno (o un `hours` non allineato) NON si cuce: si resta a 72 ore.
    extended_used = False
    extended_reason = None
    full_hours = build_hours(day0, HOURS)
    if isinstance(extended_doc, dict) and (extended_doc.get("provinces") or {}):
        ext_hours = extended_doc.get("hours") or []
        if len(ext_hours) != EXTENDED_HOURS:
            extended_reason = "extended_hours_length"
        elif ext_hours[0] != full_hours[BASE_HOURS]:
            extended_reason = "extended_time_misaligned"
        else:
            extended_used = True
    else:
        extended_reason = "extended_absent"
    total_hours = HOURS if extended_used else BASE_HOURS
    hours = full_hours[:total_hours]

    out = {}
    missing_points = []
    missing_fields = set()
    ext_reasons = {}
    ext_provinces = 0
    for prov in provinces:
        if not isinstance(prov, dict):
            continue
        sigla = prov.get("sigla")
        sel = prov.get("selected_point") or {}
        pid = sel.get("id")
        if not sigla or pid is None:
            missing_points.append(sigla or "?")
            continue
        point = by_id.get(pid)
        if point is None:
            missing_points.append(sigla)
            continue
        models = point.get("models") or {}
        model = models.get(MODEL) or {}
        hourly = model.get("hourly") or {}
        series = {}
        for _key, src, _dec in FIELDS:
            values = hourly.get(src)
            if not isinstance(values, list) or len(values) < BASE_HOURS:
                missing_fields.add(src)
                continue
            series[src] = values
        if len(series) != len(FIELDS):
            # Campo strutturalmente assente: non si scrive una riga di zeri.
            missing_points.append(sigla)
            continue

        rows = []
        for i in range(BASE_HOURS):
            rows.append([_round(series[src][i], dec) for _k, src, dec in FIELDS])
        reason = None
        if extended_used:
            ext_rows, reason = extended_rows_for(extended_doc, sigla, pid)
            if isinstance(ext_rows, list):
                for row in ext_rows:
                    rows.append([_round(row[k], FIELDS[k][2]) if k < len(row) else None
                                 for k in range(len(FIELDS))])
                ext_provinces += 1
                reason = None
            else:
                # Nessun dato esteso per QUESTA provincia: righe null (mai
                # valori inventati) e motivo dichiarato nell'entry.
                rows.extend([[None] * len(FIELDS) for _ in range(EXTENDED_HOURS)])
                ext_reasons[reason] = ext_reasons.get(reason, 0) + 1
        entry = {"id": int(pid), "h": rows}
        if reason:
            entry["extended_reason"] = reason
        out[sigla] = entry

    if missing_fields:
        raise SimpleHourlyError(
            "campi orari assenti nel dataset: %s" % ", ".join(sorted(missing_fields)))
    if not out:
        raise SimpleHourlyError("nessuna provincia risolta sul dataset")

    payload = {
        "schema": SCHEMA,
        "generated_at": provinces_doc.get("generated_at") or points_doc.get("generated_at"),
        "day0": day0,
        "timezone": points_doc.get("timezone") or provinces_doc.get("timezone") or "Europe/Rome",
        "model": MODEL,
        "hours": hours,
        "fields": FIELD_KEYS,
        "units": UNITS,
        "provinces": out,
    }
    return payload, {"provinces": len(out), "missing_points": missing_points,
                     "hours": total_hours, "extended": extended_used,
                     "extended_provinces": ext_provinces,
                     "extended_reason": extended_reason,
                     "extended_reasons": ext_reasons}


def write_atomic(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    fd, tmp = tempfile.mkstemp(prefix=".simple_hourly-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Payload orario compatto per la vista Simple")
    parser.add_argument("--points", default=_DEFAULT_POINTS)
    parser.add_argument("--provinces", default=_DEFAULT_PROVINCES)
    parser.add_argument("--extended", default=_DEFAULT_EXTENDED,
                        help="hourly_extended.json (ore 72..167); se assente si resta a 72 ore")
    parser.add_argument("--out", default=_DEFAULT_OUT)
    parser.add_argument("--json-report", action="store_true",
                        help="stampa un report JSON su stdout (per il workflow)")
    args = parser.parse_args(argv)

    if not os.path.exists(args.points) or not os.path.exists(args.provinces):
        missing = [p for p in (args.points, args.provinces) if not os.path.exists(p)]
        msg = "input assente: %s (nessun file scritto, dataset non ancora pronto)" % ", ".join(missing)
        print("[simple_hourly] ATTENZIONE: " + msg, file=sys.stderr)
        if args.json_report:
            print(json.dumps({"ok": False, "skipped": True, "reason": msg}))
        return 0

    extended_doc = None
    if os.path.exists(args.extended):
        try:
            extended_doc = _load_json(args.extended)
        except (ValueError, OSError) as exc:
            print("[simple_hourly] ATTENZIONE: estensione illeggibile (%s): resto a 72 ore."
                  % exc, file=sys.stderr)
            extended_doc = None
    else:
        print("[simple_hourly] ATTENZIONE: %s assente: payload a 72 ore (nessuna cucitura)."
              % args.extended, file=sys.stderr)

    try:
        points_doc = _load_json(args.points)
        provinces_doc = _load_json(args.provinces)
        payload, stats = build_payload(points_doc, provinces_doc, extended_doc)
    except (SimpleHourlyError, ValueError, OSError) as exc:
        print("[simple_hourly] ERRORE: %s" % exc, file=sys.stderr)
        return 2

    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False,
                      sort_keys=False)
    raw_bytes = len(text.encode("utf-8"))
    gz_bytes = len(gzip.compress(text.encode("utf-8"), 9))
    if raw_bytes > RAW_BUDGET_BYTES:
        print("[simple_hourly] ERRORE: payload %d byte > budget %d byte: "
              "ridurre ore o campi (scelta da dichiarare, non silenziosa)."
              % (raw_bytes, RAW_BUDGET_BYTES), file=sys.stderr)
        return 2

    write_atomic(args.out, text)
    report = {
        "ok": True,
        "schema": SCHEMA,
        "out": args.out,
        "provinces": stats["provinces"],
        "hours": stats["hours"],
        "hours_target": HOURS,
        "extended": stats["extended"],
        "extended_provinces": stats["extended_provinces"],
        "extended_reason": stats["extended_reason"],
        "extended_reasons": stats["extended_reasons"],
        "fields": len(FIELD_KEYS),
        "raw_bytes": raw_bytes,
        "gzip_bytes": gz_bytes,
        "missing_points": stats["missing_points"],
    }
    print("[simple_hourly] scritto %s: %d province x %d ore x %d campi, "
          "%d byte raw / %d byte gzip (estensione: %s)"
          % (args.out, report["provinces"], report["hours"], len(FIELD_KEYS),
             raw_bytes, gz_bytes,
             ("si', %d province" % report["extended_provinces"])
             if report["extended"] else "no (%s)" % report["extended_reason"]))
    if stats["missing_points"]:
        print("[simple_hourly] ATTENZIONE: province senza punto risolto: %s"
              % ", ".join(stats["missing_points"]), file=sys.stderr)
    if args.json_report:
        print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
