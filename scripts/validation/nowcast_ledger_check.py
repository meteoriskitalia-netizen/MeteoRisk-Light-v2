#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verifica del LEDGER COMPATTO su dati REALI (`nowcast_ledger.py`).

Per ogni ancora reale SRI (default: 15 ancore orarie):
  1) ricalcola il nowcast dallo stesso slot (stesso codice del run);
  2) scarica l'osservato ai 3 lead ufficiali;
  3) scrive il record binario da 1102 byte nell'archivio giornaliero;
  4) RILEGGE il record dal file compresso e ricostruisce dall'istogramma la
     contingenza a TUTTI i 7 bordi finiti, confrontandola col calcolo diretto
     sul raster (315 righe con 15 ancore x 3 lead x 7 soglie);
  5) confronta il FSS archiviato col ricalcolo sul raster;
  6) verifica che una soglia NON di bordo (3.0 mm/h) restituisca un intervallo
     che contiene il valore diretto;
  7) misura i byte/anno sul formato implementato (zstd e gzip) e il tempo/slot.

Nessun dato inventato: se l'osservato manca, l'ancora e' `skipped` con motivo.

Uso tipico:
  python scripts/validation/nowcast_ledger_check.py \
    --from 2026-11-23T00:25:00Z --to 2026-11-23T14:25:00Z \
    --step-min 60 --max-slots 15 \
    --ledger-dir "$RUNNER_TEMP/nowcast_ledger" \
    --out audit/nowcast_ledger_check.json
"""

import argparse
import datetime as _dt
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))          # scripts/
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "radar_engine"))

from radar_engine import clock as clock_mod          # noqa: E402
from radar_engine import nowcast_field as nf         # noqa: E402
from radar_engine import nowcast_ledger as nl        # noqa: E402
from radar_engine import nowcast_verify as nv        # noqa: E402
from radar_engine.config import load_config          # noqa: E402

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _parse_ms(value):
    return int(_dt.datetime.fromisoformat(
        value.replace("Z", "+00:00")).timestamp() * 1000)


def _iso(ms):
    return _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).strftime(
        _ISO)


def _anchors(start_ms, end_ms, step_min):
    step = int(step_min) * 60000
    out = []
    t = int(start_ms)
    while t <= int(end_ms):
        out.append(t)
        t += step
    return out


def check_slot(fetcher, config, anchor_ms, slot_offset_ms=0):
    """Un'ancora: ritorna (record, preds, obss, doms, stato, motivo)."""
    slot_ms = int(anchor_ms) + int(slot_offset_ms)
    frames = fetcher.window(slot_ms, n=nf.FRAMES_WANTED)
    if len(frames) < 3:
        return None, "skipped", "sri_frames_lt_3"
    if int(frames[-1].time_ms) != slot_ms:
        return None, "skipped", "source_slot_unavailable"
    clk = clock_mod.Clock.at_ms(int(frames[-1].time_ms))
    bundle = nv._Bundle(frames)
    payload, native = nf.compute(bundle, config, clk,
                                 list(bundle.warnings), out_dir=None)
    if not payload.get("available") or native is None:
        return None, "skipped", str(payload.get("null_reason")
                                    or "nowcast_unavailable")
    domain = native["_domain"]
    pers, pvalid = nf._rain_array(frames[-1])
    preds, obss, doms, valid = {}, {}, {}, {}
    for lead in nf.OFFICIAL_LEADS:
        pred = native.get(lead)
        if pred is None:
            continue
        valid_ms = int(frames[-1].time_ms) + int(lead) * 60000
        obs_rd = fetcher.get(valid_ms)
        if obs_rd is None:
            continue
        obs, ovalid = nf._rain_array(obs_rd)
        preds[lead] = pred
        obss[lead] = obs
        doms[lead] = domain & ovalid & pvalid
        valid[lead] = valid_ms
    if not preds:
        return None, "skipped", "no_lead_with_observed"
    return (preds, obss, doms, valid, slot_ms), "ok", None


def verify_record(back, preds, obss, doms):
    """Riconstruzione vs calcolo diretto. Ritorna (righe, errori, dettagli)."""
    rows = 0
    errors = []
    fss_checks = 0
    fss_errors = []
    interval_checks = 0
    interval_errors = []
    for lead in sorted(preds):
        pred, obs, dom = preds[lead], obss[lead], doms[lead]
        hist = back["hist"][lead]
        for t in nl.HIST_EDGES[1:-1]:
            rows += 1
            got = nl.contingency_from_hist(hist, t)
            direct = tuple(int(x) for x in nf.contingency(pred, obs, dom, t))
            if not got["exact"] or got["counts"] != direct:
                errors.append({"lead": lead, "threshold": float(t),
                               "hist": got["counts"], "direct": direct})
        for t in nl.THRESHOLDS:
            direct = tuple(int(x) for x in nf.contingency(pred, obs, dom, t))
            if tuple(back["counters"][lead][t]) != direct:
                errors.append({"lead": lead, "threshold": float(t),
                               "counters": list(back["counters"][lead][t]),
                               "direct": list(direct)})
            for side in nl.NEIGHBORHOODS:
                fss_checks += 1
                direct_fss = nl.fss_from_raster(pred >= t, obs >= t, dom, side)
                stored = back["fss"][lead][t][side]
                if direct_fss is None:
                    if not np.isnan(stored):
                        fss_errors.append({"lead": lead, "threshold": t,
                                           "side": side,
                                           "stored": stored,
                                           "direct": None})
                elif stored != np.float32(direct_fss):
                    fss_errors.append({"lead": lead, "threshold": t,
                                       "side": side, "stored": stored,
                                       "direct": float(direct_fss)})
        # soglia NON di bordo: intervallo, non un numero
        interval_checks += 1
        got = nl.contingency_from_hist(hist, 3.0)
        direct = tuple(int(x) for x in nf.contingency(pred, obs, dom, 3.0))
        names = ("hits", "misses", "false_alarms", "correct_negatives")
        ok = (got["exact"] is False and got["counts"] is None
              and got["scores"] is None)
        for name, value in zip(names, direct):
            lo, hi = got["bounds"][name]
            ok = ok and (lo <= value <= hi)
        if not ok:
            interval_errors.append({"lead": lead, "direct": direct,
                                    "bounds": got["bounds"]})
    return {
        "threshold_rows": rows,
        "threshold_errors": errors,
        "fss_checks": fss_checks,
        "fss_errors": fss_errors,
        "interval_checks": interval_checks,
        "interval_errors": interval_errors,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Verifica il ledger compatto su SRI reali")
    ap.add_argument("--from", dest="from_iso", required=True)
    ap.add_argument("--to", dest="to_iso", required=True)
    ap.add_argument("--step-min", type=int, default=60)
    ap.add_argument("--max-slots", type=int, default=15)
    ap.add_argument("--slot-offset-min", type=int, default=0,
                    help="scostamento dell'ancora dal passo orario")
    ap.add_argument("--min-rain-pixels", type=int, default=0)
    ap.add_argument("--ledger-dir", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    config = load_config()
    config.setdefault("source", {})
    config["source"].setdefault("fetch_workers", 4)
    fetcher = nv.SriFetcher(config)

    anchors = _anchors(_parse_ms(args.from_iso), _parse_ms(args.to_iso),
                       args.step_min)[:int(args.max_slots)]
    ledger_dir = args.ledger_dir or os.path.join(
        os.environ.get("TEMP", "."), "nowcast_ledger_check")
    t0 = time.time()
    cases = []
    total = {"threshold_rows": 0, "threshold_errors": 0, "fss_checks": 0,
             "fss_errors": 0, "interval_checks": 0, "interval_errors": 0}
    ledger_seconds = 0.0
    slots_ok = 0
    for anchor in anchors:
        got, status, reason = check_slot(
            fetcher, config, anchor, slot_offset_ms=args.slot_offset_min
            * 60000)
        if status != "ok":
            cases.append({"anchor": _iso(anchor), "status": status,
                          "reason": reason})
            continue
        preds, obss, doms, valid, slot_ms = got
        t_ledger = time.time()
        res = nl.append_slot(ledger_dir, slot_ms, preds, obss, doms, valid,
                             generated_ms=slot_ms + 600000)
        ledger_seconds += time.time() - t_ledger
        days = nl.read_day(ledger_dir, nl.day_of_slot_ms(slot_ms))
        back = None
        for rec in days:
            if int(rec["slot_ms"]) == int(slot_ms):
                back = rec
                break
        if back is None:
            cases.append({"anchor": _iso(anchor), "status": "error",
                          "reason": "record_not_read_back"})
            continue
        checks = verify_record(back, preds, obss, doms)
        total["threshold_rows"] += checks["threshold_rows"]
        total["threshold_errors"] += len(checks["threshold_errors"])
        total["fss_checks"] += checks["fss_checks"]
        total["fss_errors"] += len(checks["fss_errors"])
        total["interval_checks"] += checks["interval_checks"]
        total["interval_errors"] += len(checks["interval_errors"])
        slots_ok += 1
        cases.append({
            "anchor": _iso(anchor),
            "slot": _iso(slot_ms),
            "status": "ok",
            "leads": sorted(preds),
            "threshold_rows": checks["threshold_rows"],
            "threshold_errors": len(checks["threshold_errors"]),
            "fss_checks": checks["fss_checks"],
            "fss_errors": len(checks["fss_errors"]),
            "interval_checks": checks["interval_checks"],
            "interval_errors": len(checks["interval_errors"]),
            "ledger_day_bytes": res["compressed_bytes"],
            "ledger_records": res["records"],
            "first_errors": (checks["threshold_errors"][:3]
                             + checks["fss_errors"][:3]
                             + checks["interval_errors"][:3]),
        })

    # byte/anno sul formato implementato: si prende il file del giorno piu'
    # pieno (quello con piu' record) e si ri-comprime lo STESSO raw con i due
    # codec. Nessuna stima: la compressione gira qui.
    sizes = []
    compress_sizes = {}
    if slots_ok:
        raws = {}
        for rec in nl.iter_records(ledger_dir):
            day = nl.day_of_slot_ms(rec["slot_ms"]).isoformat()
            raws.setdefault(day, []).append(rec)
        for day, recs in raws.items():
            raw = b"".join(nl.pack(r) for r in recs)
            for codec in ("zst", "gz"):
                blob, _c = nl.compress(raw, codec)
                compress_sizes.setdefault(codec, []).append(
                    (len(recs), len(raw), len(blob), day))
    per_slot = {}
    for codec, rows in compress_sizes.items():
        # byte compressi per record, misurati sul giorno con piu' record
        rows.sort(key=lambda r: r[0])
        n, raw, blob, day = rows[-1] if rows else (0, 0, 0, None)
        per_slot[codec] = {
            "day": day,
            "records_in_day": n,
            "raw_bytes": raw,
            "compressed_bytes": blob,
            "bytes_per_record": (blob / float(n)) if n else None,
            "bytes_per_year_at_288_slots_day": (
                blob / float(n) * 288.0 * 365.0) if n else None,
        }
    # Misura PER RECORD (standalone): ogni record compresso da solo, media
    # sul campione. Il frame overhead (~13-20 B) e' incluso, quindi e' un
    # limite superiore del costo nell'archivio (dove i record condividono il
    # frame); e' pero' l'unica misura che non dipende dal numero di record
    # nel giorno.
    standalone = {}
    if slots_ok:
        recs = list(nl.iter_records(ledger_dir))
        for codec in ("zst", "gz"):
            sizes_b = []
            for rec in recs:
                blob, _c = nl.compress(nl.pack(rec), codec)
                sizes_b.append(len(blob))
            if sizes_b:
                mean = sum(sizes_b) / float(len(sizes_b))
                standalone[codec] = {
                    "records": len(sizes_b),
                    "min_bytes": min(sizes_b),
                    "max_bytes": max(sizes_b),
                    "mean_bytes_per_record": mean,
                    "bytes_per_year_at_288_slots_day": mean * 288.0 * 365.0,
                }
    # record tutto-zero: limite inferiore reale del formato (slot senza
    # pioggia, cioe' la maggioranza dei 288 slot di una giornata serena)
    dry = {}
    zero_rec = nl.empty_record(0, generated_ms=0, grid_w=1200, grid_h=1400)
    for codec in ("zst", "gz"):
        blob, _c = nl.compress(nl.pack(zero_rec), codec)
        dry[codec] = {
            "bytes": len(blob),
            "bytes_per_year_at_288_slots_day": len(blob) * 288.0 * 365.0,
        }

    elapsed = time.time() - t0
    report = {
        "schema": "meteorisk.nowcast_ledger_check/1",
        "generated_at": _iso(int(time.time() * 1000)),
        "window": {"from": args.from_iso, "to": args.to_iso,
                   "step_min": args.step_min},
        "ledger": {
            "schema": nl.SCHEMA,
            "record_bytes": nl.RECORD_BYTES,
            "hist_edges": list(nl.HIST_EDGES),
            "thresholds": list(nl.THRESHOLDS),
            "neighbourhoods": list(nl.NEIGHBORHOODS),
            "dir": os.path.abspath(ledger_dir),
            "files": sorted(os.path.relpath(p, ledger_dir)
                            for p in _ledger_files(ledger_dir)),
            "total_bytes": nl.archive_bytes(ledger_dir),
        },
        "slots_requested": len(anchors),
        "slots_ok": slots_ok,
        "downloads": fetcher.downloads,
        "sri_failures": fetcher.failures,
        "threshold_rows_checked": total["threshold_rows"],
        "threshold_rows_mismatch": total["threshold_errors"],
        "fss_checks": total["fss_checks"],
        "fss_mismatch": total["fss_errors"],
        "interval_checks": total["interval_checks"],
        "interval_mismatch": total["interval_errors"],
        "seconds_total": round(elapsed, 2),
        "seconds_per_slot": (round(elapsed / slots_ok, 3)
                             if slots_ok else None),
        "ledger_seconds_total": round(ledger_seconds, 3),
        "ledger_seconds_per_slot": (round(ledger_seconds / slots_ok, 3)
                                    if slots_ok else None),
        "compression": per_slot,
        "compression_standalone": standalone,
        "compression_dry_record": dry,
        "cases": cases,
    }
    print("[INFO] ancore %d, ok %d, download %d, %.1f s (%.2f s/slot)"
          % (len(anchors), slots_ok, fetcher.downloads, elapsed,
             elapsed / slots_ok if slots_ok else 0.0))
    print("[INFO] righe soglia ricostruite %d/%d, mismatch %d"
          % (total["threshold_rows"] - total["threshold_errors"],
             total["threshold_rows"], total["threshold_errors"]))
    print("[INFO] FSS %d/%d identici, intervalli %d/%d coerenti"
          % (total["fss_checks"] - total["fss_errors"], total["fss_checks"],
             total["interval_checks"] - total["interval_errors"],
             total["interval_checks"]))
    for codec, m in per_slot.items():
        if m["bytes_per_record"]:
            print("[INFO] %s: %d record, %d B -> %d B (%.1f B/record, "
                  "%.1f MB/anno a 288 slot/giorno)"
                  % (codec, m["records_in_day"], m["raw_bytes"],
                     m["compressed_bytes"], m["bytes_per_record"],
                     (m["bytes_per_year_at_288_slots_day"] or 0) / 1e6))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".",
                    exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print("[INFO] report scritto in %s" % args.out)
    return 0 if (total["threshold_errors"] == 0 and total["fss_errors"] == 0
                 and total["interval_errors"] == 0 and slots_ok) else 1


def _ledger_files(root):
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith((".bin.zst", ".bin.gz")):
                out.append(os.path.join(dirpath, name))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
