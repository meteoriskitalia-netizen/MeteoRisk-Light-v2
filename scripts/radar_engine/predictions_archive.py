#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — predictions_archive.py (archivio delle predizioni).

PUBLISH DEL BRANCH CACHE `predictions-archive`. Il registro su main resta a
30 giorni (consumatori attuali invariati); qui si accumulano le STESSE righe
in blob GIORNALIERI `data/state/predictions/YYYY/MM/DD.jsonl` (giorno UTC del
`ts`), al massimo 365 blob vivi. I blob sono publici e leggibili via raw:

  https://raw.githubusercontent.com/meteoriskitalia-netizen/\
MeteoRisk-Light-v2/predictions-archive/data/state/predictions/YYYY/MM/DD.jsonl

Il commit sul branch e' ORPHAN (senza parent) con `--force-with-lease`, nello
step del workflow: la storia raggiungibile resta 1 commit e la GC recupera i
blob temporanei. NON si accumula mai un unico file crescente su main.

Questo modulo fa SOLO la parte filesystem (merge + conteggio righe corrotte +
retention); non esegue git. Due modi per produrre le righe nuove:
  --new-dir DIR     directory con i blob del giorno scritti dal motore
  --backfill-from F semi del registro corrente (operazione UNA TANTUM,
                    non usata dal workflow)
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys

# Bootstrap identico a main.py: eseguibile sia come script sia come modulo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar_engine import severe_predictions as sp  # noqa: E402

_DAY_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})\.jsonl$")


def _as_utc(now):
    if now is None:
        return _dt.datetime.now(_dt.timezone.utc)
    if isinstance(now, str):
        now = _dt.datetime.strptime(now[:19], "%Y-%m-%dT%H:%M:%S")
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc)


def _read_jsonl(path):
    """Ritorna (righe_valide_strip, n_corrotte).

    Righe vuote: ignorate (non contate), come in `rotate()`. Una riga e' valida
    solo se e' JSON, e' un dict e ha un `ts` ISO8601 interpretabile. NON viene
    corretta ne' riscritta: viene scartata e contata."""
    rows, invalid = [], 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    invalid += 1
                    continue
                if not isinstance(obj, dict) or sp._parse_iso(obj.get("ts")) is None:
                    invalid += 1
                    continue
                rows.append(line)
    except OSError:
        return [], 0
    return rows, invalid


def _day_files(root):
    """Mappa `YYYY-MM-DD` -> path per i file `YYYY/MM/DD.jsonl` sotto root."""
    out = {}
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        for name in files:
            relpath = (rel + "/" + name) if rel != "." else name
            m = _DAY_RE.match(relpath)
            if m:
                out["-".join(m.groups())] = os.path.join(dirpath, name)
    return out


def _merge_groups(archive_dir, groups, invalid_new, retention_days, now,
                  report_path=None):
    """Unisce i gruppi {giorno: [righe nuove]} con i blob gia' pubblicati.

    Ordine: righe gia' presenti prima, poi le nuove; dedup per riga esatta
    (`!seen[$0]++`, identico al registro su main). Le righe corrotte dei blob
    esistenti e del nuovo arrivo sono CONTATE e scartate, mai riscritte.
    """
    now_dt = _as_utc(now)
    cutoff_day = (now_dt - _dt.timedelta(days=int(retention_days))).strftime(
        "%Y-%m-%d")
    existing = _day_files(archive_dir)
    touched = set(existing) | set(groups)
    dropped_invalid = int(invalid_new)
    rows_added = 0
    rows_after = 0
    written = 0
    for day in sorted(touched):
        old_path = existing.get(day)
        old_rows, old_invalid = _read_jsonl(old_path) if old_path else ([], 0)
        dropped_invalid += old_invalid
        new_rows = groups.get(day, [])
        seen = set()
        merged = []
        for line in old_rows + new_rows:
            if line in seen:
                continue
            seen.add(line)
            merged.append(line)
        rows_added += len(new_rows)
        if new_rows or old_invalid:
            sp._atomic_write(old_path or _day_blob_path(archive_dir, day),
                             "".join(l + "\n" for l in merged))
            written += 1
        rows_after += len(merged)

    removed = []
    for day, path in existing.items():
        if day < cutoff_day:
            try:
                os.unlink(path)
                removed.append(day)
            except OSError:
                pass

    report = {
        "archive_dir": os.path.abspath(archive_dir),
        "retention_days": int(retention_days),
        "cutoff_day": cutoff_day,
        "now": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days_written": written,
        "rows_added": rows_added,
        "rows_total_after": rows_after,
        "dropped_invalid": dropped_invalid,
        "removed_old_days": sorted(removed),
        "blobs_alive": len(_day_files(archive_dir)),
        "unit": "blobs = file data/state/predictions/YYYY/MM/DD.jsonl",
    }
    if report_path:
        sp._atomic_write(report_path, json.dumps(report, ensure_ascii=False,
                                                 sort_keys=True) + "\n")
    return report


def _day_blob_path(archive_dir, day):
    """Path canonico del blob di un giorno `YYYY-MM-DD`."""
    y, m, d = day.split("-")
    return os.path.join(archive_dir, y, m, d + ".jsonl")


def merge_daily(archive_dir, new_dir, retention_days=sp.ARCHIVE_RETENTION_DAYS,
                now=None, report_path=None):
    """Unisce i blob del giorno prodotti dal motore con quelli pubblicati.

    `new_dir` deve contenere solo file `YYYY/MM/DD.jsonl` (copia di
    `data/state/predictions/` fatta dal workflow PRIMA di ripristinare il
    branch). Nessun valore inventato: solo righe gia' valide e il loro `ts`.
    """
    groups = {}
    invalid = 0
    for day, path in sorted(_day_files(new_dir).items()):
        rows, bad = _read_jsonl(path)
        invalid += bad
        if rows:
            groups.setdefault(day, []).extend(rows)
    return _merge_groups(archive_dir, groups, invalid, retention_days, now,
                         report_path)


def backfill_from_register(register_path, archive_dir,
                           retention_days=sp.ARCHIVE_RETENTION_DAYS,
                           now=None, report_path=None):
    """Semi una tantum dell'archivio dal registro corrente (non usato dal workflow).

    Split per giorno UTC del `ts`; nessuna riga inventata, righe corrotte
    contate e scartate. Idempotente: il dedup evita duplicati su piu' esecuzioni.
    """
    rows, invalid = _read_jsonl(register_path)
    groups = {}
    for line in rows:
        obj = json.loads(line)
        day = sp._utc_day(obj["ts"])
        if day is None:          # gia' scartato da _read_jsonl, difesa
            invalid += 1
            continue
        groups.setdefault(day, []).append(line)
    return _merge_groups(archive_dir, groups, invalid, retention_days, now,
                         report_path)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Merge dei blob giornalieri delle predizioni (branch cache)")
    ap.add_argument("--archive-dir", required=True,
                    help="directory data/state/predictions ripristinata dal branch")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--new-dir", help="directory con i blob del giorno del run")
    src.add_argument("--backfill-from", help="JSONL del registro, semi una tantum")
    ap.add_argument("--retention-days", type=int,
                    default=sp.ARCHIVE_RETENTION_DAYS)
    ap.add_argument("--now", default=None,
                    help="ISO8601 UTC (solo test/backfill); default: ora reale")
    ap.add_argument("--report", default=None, help="path del report JSON")
    args = ap.parse_args(argv)
    if args.new_dir:
        report = merge_daily(args.archive_dir, args.new_dir,
                             args.retention_days, args.now, args.report)
    else:
        report = backfill_from_register(args.backfill_from, args.archive_dir,
                                        args.retention_days, args.now,
                                        args.report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
