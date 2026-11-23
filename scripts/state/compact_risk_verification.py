#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compattazione del registro di verifica (rotazione + aggregato + skill).

Cosa fa, in ordine:
  1. legge ``risk_verification.jsonl`` (dettaglio, una riga per unita');
  2. conserva il dettaglio degli ultimi ``--keep-days`` giorni (default 30);
  3. aggrega in ``risk_verification_daily.jsonl`` i giorni fra ``keep-days`` e
     ``--aggregate-days`` (default 90) con i 5 contatori
     ``tp/fp/fn/tn/no`` per (giorno, provincia, pericolo);
  4. scarta il dettaglio piu' vecchio di ``--aggregate-days`` (l'aggregato
     resta, con retention ``--aggregate-retention-days`` = 365);
  5. ricalcola ``risk_skill.json`` dai contatori (stessa funzione di JOB B:
     ``build_skill_payload``) — nessuna formula duplicata.

IDEMPOTENTE: dopo l'aggregazione le righe di dettaglio vengono rimosse, quindi
una seconda esecuzione non somma due volte gli stessi esiti. Il file di
dettaglio e l'aggregato sono riscritti in modo atomico (pattern
``_atomic_write`` di ``scripts/radar_engine/severe_predictions.py``).

Uso:
  python scripts/state/compact_risk_verification.py \
      --detail data/state/risk_verification.jsonl \
      --daily data/state/risk_verification_daily.jsonl \
      --skill data/state/risk_skill.json \
      --keep-days 30 --aggregate-days 90
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from verification.risk_verification import (  # noqa: E402
    DEFAULT_AGGREGATE_DAYS, DEFAULT_AGGREGATE_RETENTION, DEFAULT_KEEP_DAYS,
    HAZARDS, _atomic_write, build_skill_payload, counters_from_daily,
    counters_from_rows, coverage_from_rows, merge_counts, read_json_optional,
    read_jsonl,
)
from verification.timeutil import format_utc, local_day, now_utc  # noqa: E402

SCHEMA_VERSION = 1
RESULT_KEYS = {"hit": "tp", "false_alarm": "fp", "miss": "fn",
               "correct_negative": "tn", "no_observation": "no"}


def _daily_map(rows):
    """(d,p,h) -> contatori, dall'aggregato giornaliero."""
    out = {}
    for row in rows or []:
        day = str(row.get("d") or "")
        sigla = str(row.get("p") or "")
        hazard = str(row.get("h") or "")
        if not day or not sigla or hazard not in HAZARDS:
            continue
        key = (day, sigla, hazard)
        dst = out.setdefault(key, {"n": 0, "tp": 0, "fp": 0, "fn": 0,
                                   "tn": 0, "no": 0})
        for field in ("n", "tp", "fp", "fn", "tn", "no"):
            dst[field] += int(row.get(field) or 0)
    return out


def _bump(dst, row):
    key = RESULT_KEYS.get(row.get("r"))
    dst["n"] += 1
    if key:
        dst[key] += 1


def run(args) -> dict:
    now = now_utc(args.now)
    today = date.fromisoformat(local_day(now))
    keep_start = (today - timedelta(days=int(args.keep_days))).isoformat()
    agg_start = (today - timedelta(days=int(args.aggregate_days))).isoformat()
    ret_start = (today - timedelta(days=int(args.aggregate_retention_days))).isoformat()

    detail_rows, corrupt = read_jsonl(args.detail)
    kept, to_aggregate, dropped = [], [], 0
    for row in detail_rows:
        day = str(row.get("d") or "")
        if not day:
            continue
        if day >= keep_start:
            kept.append(row)
        elif day >= agg_start:
            to_aggregate.append(row)
        else:
            dropped += 1

    daily_rows, daily_corrupt = read_jsonl(args.daily)
    daily = _daily_map(daily_rows)

    aggregated_rows = 0
    for row in to_aggregate:
        day = str(row.get("d"))
        sigla = str(row.get("p") or "")
        hazard = str(row.get("h") or "")
        if not sigla or hazard not in HAZARDS:
            continue
        key = (day, sigla, hazard)
        dst = daily.setdefault(key, {"n": 0, "tp": 0, "fp": 0,
                                     "fn": 0, "tn": 0, "no": 0})
        _bump(dst, row)
        aggregated_rows += 1

    # retention dell'aggregato (365 giorni di default)
    daily = {k: v for k, v in daily.items() if k[0] >= ret_start}

    kept.sort(key=lambda r: (str(r.get("d")), str(r.get("p")), str(r.get("h"))))
    daily_list = [
        {"d": d, "p": p, "h": h, "n": c["n"], "tp": c["tp"], "fp": c["fp"],
         "fn": c["fn"], "tn": c["tn"], "no": c["no"], "v": SCHEMA_VERSION}
        for (d, p, h), c in sorted(daily.items())
    ]

    # Doppio conteggio impossibile: un giorno e' nel dettaglio O nell'aggregato.
    daily_for_skill = [r for r in daily_list if str(r.get("d")) < keep_start]
    counts = merge_counts(counters_from_rows(kept),
                          counters_from_daily(daily_for_skill))

    engine_versions = [r.get("ev") for r in kept if r.get("ev")]
    engine_version = (max(sorted(set(engine_versions)), key=engine_versions.count)
                      if engine_versions else None)
    # La copertura (stazioni/radar/AFA per pericolo) e' misurata da JOB B:
    # il compattatore la PRESERVA e aggiunge il proprio blocco. Se il file
    # skill non esiste ancora, la ricostruisce dai motivi delle righe.
    previous = read_json_optional(args.skill)
    previous_coverage = (previous.get("coverage")
                         if isinstance(previous, dict) else None)
    if isinstance(previous_coverage, dict):
        coverage = dict(previous_coverage)
    else:
        coverage = coverage_from_rows(kept, 0, 0, {}, {})
    coverage["compaction"] = {
        "detail_rows": len(kept),
        "aggregated_days": len({r["d"] for r in daily_list}),
        "aggregate_rows": len(daily_list),
        "no_observation": sum(int(c.get("no") or 0) for c in counts.values()),
    }
    skill = build_skill_payload(counts, kept, daily_for_skill, format_utc(now),
                                engine_version, coverage)

    detail_text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")) + "\n"
                          for r in kept)
    daily_text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")) + "\n"
                         for r in daily_list)
    _atomic_write(args.detail, detail_text)
    _atomic_write(args.daily, daily_text)
    _atomic_write(args.skill, json.dumps(skill, ensure_ascii=False,
                                         sort_keys=True, indent=2) + "\n")

    return {
        "now": format_utc(now),
        "keep_start": keep_start,
        "aggregate_start": agg_start,
        "detail_in": len(detail_rows),
        "detail_kept": len(kept),
        "aggregated_rows": aggregated_rows,
        "dropped_detail": dropped,
        "daily_rows": len(daily_list),
        "corrupt_detail": corrupt,
        "corrupt_daily": daily_corrupt,
        "skill": args.skill,
        "counts": {h: counts[h] for h in HAZARDS},
    }


def build_parser():
    root = _HERE.parents[2]
    ap = argparse.ArgumentParser(
        description="Compatta il registro di verifica (30/90 giorni) e ricalcola risk_skill.json.")
    ap.add_argument("--detail", default=str(root / "data/state/risk_verification.jsonl"))
    ap.add_argument("--daily", default=str(root / "data/state/risk_verification_daily.jsonl"))
    ap.add_argument("--skill", default=str(root / "data/state/risk_skill.json"))
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    ap.add_argument("--aggregate-days", type=int, default=DEFAULT_AGGREGATE_DAYS)
    ap.add_argument("--aggregate-retention-days", type=int,
                    default=DEFAULT_AGGREGATE_RETENTION)
    ap.add_argument("--now", default=None, help="istante ISO-8601 (default: adesso)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[compact_risk_verification] ERRORE: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
