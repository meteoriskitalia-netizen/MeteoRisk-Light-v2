#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Motore di ricerca del nowcast (screening + hill climbing).

Spazio SUPPORTATO dal motore attuale:
  motion_speed_factor   0.7 0.8 0.9 1.0 1.1 1.2
  transport_coarse_factor 1 2 3 5
  decay                 off / rear_erosion 5 / rear_erosion 10
  hf_restore            off / 0.5 / 0.7

ASSI NON ANCORA IMPLEMENTATI (dichiarati, non simulati):
  motion_inflation, dispersion_growth_per_lead, evolution cascade/
  geostatistico, ensemble_members. Non vengono fatti passare per parametri:
  il registro li marca `supported: false`.

Uso:
  python scripts/validation/nowcast_sweep.py --precache
  python scripts/validation/nowcast_sweep.py --shard 0 --shards 4
  ...
  python scripts/validation/nowcast_sweep.py --merge

Le 6 giornate dello screening sono DICHIARATE PRIMA in `SCREEN_ANCHORS`.
Le 12 giornate della verifica finale sono in `FINAL_ANCHORS` (usate dallo
stesso motore con `--final`).
"""
import argparse
import glob
import json
import os
import random
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from radar_engine import clock as clock_mod  # noqa: E402
from radar_engine import fetch as fetch_mod  # noqa: E402
from radar_engine import nowcast_field as nf  # noqa: E402
from radar_engine.config import load_config  # noqa: E402
import nowcast_reliability_90 as R  # noqa: E402

OUT = os.path.join(ROOT, "docs", "evidence", "nowcast_sweep_20261127")
LEADS = (15, 90)

SCREEN_ANCHORS = [
    ("2026-11-01T16:00:00Z", "convettivo veloce"),
    ("2026-10-20T16:00:00Z", "convettivo veloce"),
    ("2026-09-10T16:00:00Z", "convettivo"),
    ("2026-11-09T16:00:00Z", "stratiforme veloce"),
    ("2026-05-29T12:00:00Z", "stratiforme"),
    ("2026-07-27T12:00:00Z", "debole"),
]
FINAL_ANCHORS = [x["anchor"] for x in R.ANCHORS]

SPEEDS = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2)
COARSE = (1, 2, 3, 5)
DECAY = (0, 5.0, 10.0)
HF = (0.0, 0.5, 0.7)
BASELINE = {"speed": 0.8, "coarse": 3, "decay": 5.0, "hf": 0.7}


class Bundle(object):
    def __init__(self, rasters):
        self.dpc_bundle = {"entries": {
            int(rd.time_ms): {"sri": {"raster": rd}} for rd in rasters}}
        self.warnings = []
        self._nowcast_verification = {}


def combos(n=48):
    allc = [(s, c, d, h) for s in SPEEDS for c in COARSE
            for d in DECAY for h in HF]
    base = (BASELINE["speed"], BASELINE["coarse"], BASELINE["decay"],
            BASELINE["hf"])
    rng = random.Random(20261127)
    rest = [x for x in allc if x != base]
    rng.shuffle(rest)
    out = [base] + rest[:n - 1]
    return out


def cfg_for(combo, base_cfg):
    speed, coarse, decay, hf = combo
    cfg = json.loads(json.dumps(base_cfg))
    cfg["nowcast_motion_speed_factor"] = float(speed)
    cfg["nowcast_transport_coarse"] = int(coarse)
    cfg["nowcast_decay"] = {
        "enabled": bool(decay > 0),
        "threshold_mmh": float(decay if decay > 0 else 5.0),
        "min_region_px": 20,
        "px_per_30min": 1.0,
    }
    cfg["nowcast_hf_restore"] = {
        "enabled": bool(hf > 0),
        "sigma_px": 4.0,
        "level": float(hf if hf > 0 else 0.7),
        "growth_per_90min": 0.20,
    }
    return cfg


def _base_cfg():
    cfg = load_config()
    cfg["ensemble"] = dict(cfg.get("ensemble") or {}, enabled=False)
    cfg["nowcast_trend"] = {"enabled": False}
    cfg["nowcast_clouds"] = {"enabled": False}
    cfg["nowcast_leads_min"] = list(LEADS)
    return cfg


def load_anchor(cfg, anchor):
    ms, _ = clock_mod.parse_iso8601_utc(anchor)
    for attempt in range(3):
        try:
            rasters, _w, _ts = fetch_mod.fetch_frames(
                cfg, product="SRI", max_frames=6, anchor_ms=int(ms))
        except Exception as exc:
            print(anchor, "FETCH", attempt, exc)
            rasters = []
        if len(rasters) >= 4:
            return rasters, int(ms)
        time.sleep(5)
    return [], int(ms)


def evaluate(cfg, rasters, ms, anchor):
    bundle = Bundle(rasters)
    payload, native = nf.compute(
        bundle, cfg, clock_mod.Clock.at_ms(ms), bundle.warnings, out_dir=None)
    if not payload.get("available"):
        return {"error": payload.get("null_reason")}
    dom = np.asarray(native["_domain"], bool)
    pk = float(nf._pixel_km(native["_rd"]))
    out = {"leads": {}}
    for lead in LEADS:
        o, ov = R.observed(cfg, anchor, lead)
        if o is None:
            out["leads"][str(lead)] = {"error": "obs_unavailable"}
            continue
        m = R.metrics(native[lead], dom, o, ov, pk)
        out["leads"][str(lead)] = {
            "coverage_0p5": m["overlap"]["0.5"]["coverage"],
            "coverage_5": m["overlap"]["5.0"]["coverage"],
            "iou_0p5": m["overlap"]["0.5"]["iou"],
            "iou_5": m["overlap"]["5.0"]["iou"],
            "bias_0p5": m["overlap"]["0.5"]["bias"],
            "bias_5": m["overlap"]["5.0"]["bias"],
            "corr_0p5": m.get("corr_0p5"),
        }
    return out


def mean_rows(rows):
    keys = ("coverage_0p5", "coverage_5", "iou_0p5", "iou_5",
            "bias_0p5", "bias_5", "corr_0p5")
    out = {}
    for k in keys:
        xs = [r[k] for r in rows if k in r and r[k] is not None]
        out[k] = round(float(np.mean(xs)), 4) if xs else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--precache", action="store_true")
    ap.add_argument("--final", action="store_true",
                    help="usa le 12 giornate finali (hill climbing)")
    ap.add_argument("--combo-file", default=None,
                    help="JSON con la lista di combinazioni da provare")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    base = _base_cfg()
    allc = combos()
    if args.combo_file:
        with open(args.combo_file, encoding="utf-8") as fh:
            allc = [tuple(x) for x in json.load(fh)]
    if args.limit:
        allc = allc[:args.limit]
    anchors = (FINAL_ANCHORS if args.final else
               [a for a, _reg in SCREEN_ANCHORS])
    if args.precache:
        for anchor in anchors:
            for lead in LEADS:
                R.observed(base, anchor, lead)
        print("precache done", len(anchors) * len(LEADS))
        return
    if args.merge:
        files = sorted(glob.glob(os.path.join(OUT, "sweep_shard_*.json")))
        rows = []
        for f in files:
            with open(f, encoding="utf-8") as fh:
                rows.extend(json.load(fh))
        with open(os.path.join(OUT, "sweep_all.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        print("merged", len(rows), "rows from", len(files), "shards")
        return
    # baseline per ancora (per il test di sanita' a +15)
    base_rows = {}
    for anchor in anchors:
        rasters, ms = load_anchor(base, anchor)
        if not rasters:
            base_rows[anchor] = {"error": "frames"}
            continue
        base_rows[anchor] = evaluate(cfg_for(
            (BASELINE["speed"], BASELINE["coarse"], BASELINE["decay"],
             BASELINE["hf"]), base), rasters, ms, anchor)
        print("baseline", anchor)
    mine = [c for i, c in enumerate(allc) if i % args.shards == args.shard]
    rows = []
    for combo in mine:
        speed, coarse, decay, hf = combo
        rec = {"combo": {"speed": speed, "coarse": coarse, "decay": decay,
                         "hf": hf},
               "anchors": {}, "supported": True,
               "unsupported_axes": ["motion_inflation",
                                    "dispersion_growth_per_lead",
                                    "evolution_cascade_geostat",
                                    "ensemble_members"]}
        per = {15: [], 90: []}
        for anchor in anchors:
            rasters, ms = load_anchor(base, anchor)
            if not rasters:
                rec["anchors"][anchor] = {"error": "frames"}
                continue
            res = evaluate(cfg_for(combo, base), rasters, ms, anchor)
            rec["anchors"][anchor] = res
            if "leads" in res:
                for lead in LEADS:
                    if "error" not in res["leads"].get(str(lead), {}):
                        per[lead].append(res["leads"][str(lead)])
        rec["mean15"] = mean_rows(per[15]) if per[15] else {}
        rec["mean90"] = mean_rows(per[90]) if per[90] else {}
        # sanity +15: non peggiorare di >0.02 rispetto al baseline
        b15 = base_rows.get(anchors[0], {}).get("leads", {}).get("15", {})
        cov15 = rec["mean15"].get("coverage_0p5")
        b_cov15 = None
        # media baseline su tutte le ancore
        bvals = []
        for a in anchors:
            x = base_rows.get(a, {}).get("leads", {}).get("15", {})
            if x.get("coverage_0p5") is not None:
                bvals.append(x["coverage_0p5"])
        b_cov15 = float(np.mean(bvals)) if bvals else None
        rec["baseline_cov15"] = round(b_cov15, 4) if b_cov15 is not None else None
        rec["suspicious_widening"] = bool(
            cov15 is not None and b_cov15 is not None and
            cov15 < b_cov15 - 0.02)
        bias90 = rec["mean90"].get("bias_0p5")
        cov90 = rec["mean90"].get("coverage_0p5")
        score = None
        if cov90 is not None:
            score = cov90 if (bias90 is None or bias90 <= 1.3) else \
                cov90 - 1.5 * (bias90 - 1.3)
        rec["objective"] = (round(float(score), 4)
                            if score is not None else None)
        rows.append(rec)
        print("combo", combo, "cov90", cov90, "bias90", bias90,
              "score", rec["objective"])
    path = os.path.join(OUT, "sweep_shard_%d.json" % args.shard)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    print("saved", path, len(rows))


if __name__ == "__main__":
    main()
