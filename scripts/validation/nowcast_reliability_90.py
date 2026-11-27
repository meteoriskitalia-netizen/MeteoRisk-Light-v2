#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Affidabilita' del nowcast a +30/+60/+90 (criterio fissato dall'utente).

Definizione operativa (NON modificabile dopo la misura):
  Un'ancora a un lead e' AFFIDABILE se valgono TUTTE e tre:
    1. area prevista / area osservata in [0.6, 1.4] a 0,5 mm/h E a 5 mm/h;
    2. centroide previsto entro 20 km dall'osservato (maschera >= 0,5 mm/h);
    3. copertura >= 50% dell'area osservata >= 5 mm/h da parte della
       previsione >= 5 mm/h.
  Affidabilita' = frazione di ancore che passano tutte e tre.

Uso:
  python scripts/validation/nowcast_reliability_90.py --out docs/evidence/...
  python scripts/validation/nowcast_reliability_90.py --limit 2   # prova

Solo frame archiviati: le ancore sono elencate PRIMA della misura in
`ANCHORS` (12 giornate, regimi diversi). Nessuna soglia viene adattata.
"""
import argparse
import json
import os
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

LEADS = (30, 60, 90)
THRESHOLDS = (0.5, 5.0)
AREA_BAND = (0.6, 1.4)
CENTROID_MAX_KM = 20.0
COVERAGE_MIN = 0.50

# DICHIARATE PRIMA DELLA MISURA (12 giornate, ore e regimi diversi).
ANCHORS = [
    {"anchor": "2026-11-01T16:00:00Z", "regime": "convettivo veloce"},
    {"anchor": "2026-10-20T16:00:00Z", "regime": "convettivo veloce"},
    {"anchor": "2026-09-10T16:00:00Z", "regime": "convettivo"},
    {"anchor": "2026-10-08T16:00:00Z", "regime": "convettivo"},
    {"anchor": "2026-11-26T13:25:00Z", "regime": "convettivo veloce"},
    {"anchor": "2026-11-09T16:00:00Z", "regime": "stratiforme veloce"},
    {"anchor": "2026-05-29T12:00:00Z", "regime": "stratiforme"},
    {"anchor": "2026-08-29T16:00:00Z", "regime": "stratiforme"},
    {"anchor": "2026-07-27T12:00:00Z", "regime": "debole"},
    {"anchor": "2026-09-30T12:00:00Z", "regime": "debole"},
    {"anchor": "2026-07-08T12:00:00Z", "regime": "debole"},
    {"anchor": "2026-08-13T16:00:00Z", "regime": "debole"},
]

CACHE = os.path.join(ROOT, "_diag", "reliability90_cache")


class Bundle(object):
    def __init__(self, rasters):
        self.dpc_bundle = {"entries": {
            int(rd.time_ms): {"sri": {"raster": rd}} for rd in rasters}}
        self.warnings = []
        self._nowcast_verification = {}


def _cache_path(anchor, lead):
    tag = anchor.replace(":", "").replace("-", "")
    return os.path.join(CACHE, "%s_o%03d.npz" % (tag, lead))


def observed(cfg, anchor, lead):
    p = _cache_path(anchor, lead)
    if os.path.exists(p):
        z = np.load(p)
        return z["d"].astype(np.float32), z["v"].astype(bool)
    ms, _ = clock_mod.parse_iso8601_utc(anchor)
    frames, _w, _t = fetch_mod.fetch_frames(
        cfg, product="SRI", max_frames=1,
        anchor_ms=int(ms) + lead * 60000)
    if not frames:
        return None, None
    d, v = nf._rain_array(frames[-1])
    d = np.asarray(d, np.float32)
    v = np.asarray(v, bool)
    os.makedirs(CACHE, exist_ok=True)
    np.savez_compressed(p, d=d, v=v)
    return d, v


def centroid(mask, valid):
    m = np.asarray(mask, bool) & np.asarray(valid, bool)
    n = int(m.sum())
    if n == 0:
        return None
    rr, cc = np.where(m)
    return float(rr.mean()), float(cc.mean())


def metrics(pred, pvalid, obs, ovalid, pixel_km):
    dom = np.asarray(pvalid, bool) & np.asarray(ovalid, bool)
    out = {"area": {}, "pass": {}, "fail": [], "overlap": {}}
    p = np.asarray(pred, np.float32)
    o = np.asarray(obs, np.float32)
    for thr in THRESHOLDS:
        pm = (p >= thr) & dom
        om = (o >= thr) & dom
        ap = int(pm.sum())
        ao = int(om.sum())
        inter = int((pm & om).sum())
        union = int((pm | om).sum())
        ratio = (ap / float(ao)) if ao else None
        cov = (inter / float(ao)) if ao else None
        iou = (inter / float(union)) if union else None
        out["area"][str(thr)] = {"pred_px": ap, "obs_px": ao,
                                 "ratio": (round(ratio, 3)
                                           if ratio is not None else None)}
        out["overlap"][str(thr)] = {
            "coverage": round(cov, 4) if cov is not None else None,
            "iou": round(iou, 4) if iou is not None else None,
            "bias": round(ratio, 4) if ratio is not None else None,
            "inter_px": inter, "union_px": union}
        ok = (ratio is not None and AREA_BAND[0] <= ratio <= AREA_BAND[1])
        out["pass"]["area_%s" % thr] = bool(ok)
        if not ok:
            out["fail"].append("area_%s" % thr)
    # correlazione spaziale sulle maschere binarie >=0,5 (evita che un
    # singolo picco intenso domini il coefficiente)
    b1 = ((p >= 0.5) & dom).astype(np.float32).ravel()
    b2 = ((o >= 0.5) & dom).astype(np.float32).ravel()
    if b1.size > 1 and b1.std() > 0 and b2.std() > 0:
        out["corr_0p5"] = round(float(np.corrcoef(b1, b2)[0, 1]), 4)
    else:
        out["corr_0p5"] = None
    cp = centroid(pred >= 0.5, dom)
    co = centroid(obs >= 0.5, dom)
    if cp is None or co is None:
        dist = None
    else:
        dist = float(np.hypot(cp[0] - co[0], cp[1] - co[1]) * pixel_km)
    out["centroid_km"] = (round(dist, 2) if dist is not None else None)
    out["pass"]["centroid"] = bool(dist is not None and
                                   dist <= CENTROID_MAX_KM)
    if not out["pass"]["centroid"]:
        out["fail"].append("centroid")
    op = int(((obs >= 5.0) & dom).sum())
    hit = int(((obs >= 5.0) & (pred >= 5.0) & dom).sum())
    cov = (hit / float(op)) if op else None
    out["coverage_ge5"] = round(cov, 3) if cov is not None else None
    out["pass"]["coverage_ge5"] = bool(cov is not None and
                                       cov >= COVERAGE_MIN)
    if not out["pass"]["coverage_ge5"]:
        out["fail"].append("coverage_ge5")
    out["reliable"] = bool(all(out["pass"].values()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        ROOT, "docs", "evidence", "nowcast_reliability_90_20261127"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cfg = load_config()
    cfg["ensemble"] = dict(cfg.get("ensemble") or {}, enabled=False)
    cfg["nowcast_trend"] = {"enabled": False}
    cfg["nowcast_clouds"] = {"enabled": False}
    cfg["nowcast_decay"] = dict(cfg.get("nowcast_decay") or {},
                                enabled=True)
    anchors = ANCHORS[:args.limit] if args.limit else ANCHORS
    report = {"declared_before_measure": True,
              "criteria": {"area_band": list(AREA_BAND),
                           "centroid_max_km": CENTROID_MAX_KM,
                           "coverage_min": COVERAGE_MIN,
                           "leads": list(LEADS)},
              "anchors_declared": ANCHORS, "anchors": [], "summary": {}}
    per_lead = {l: [] for l in LEADS}
    for item in anchors:
        anchor = item["anchor"]
        ms, _ = clock_mod.parse_iso8601_utc(anchor)
        rasters = []
        for attempt in range(3):
            try:
                rasters, _w, _ts = fetch_mod.fetch_frames(
                    cfg, product="SRI", max_frames=6, anchor_ms=int(ms))
            except Exception as exc:
                print(anchor, "FETCH", attempt, exc)
            if len(rasters) >= 4:
                break
            time.sleep(5)
        if len(rasters) < 4:
            report["anchors"].append({"anchor": anchor,
                                      "regime": item["regime"],
                                      "error": "frames_lt_4"})
            continue
        bundle = Bundle(rasters)
        try:
            payload, native = nf.compute(
                bundle, cfg, clock_mod.Clock.at_ms(int(ms)),
                bundle.warnings, out_dir=None)
        except Exception as exc:
            report["anchors"].append({"anchor": anchor,
                                      "regime": item["regime"],
                                      "error": "compute:" + str(exc)})
            continue
        if not payload.get("available"):
            report["anchors"].append({"anchor": anchor,
                                      "regime": item["regime"],
                                      "error": payload.get("null_reason")})
            continue
        rd = native["_rd"]
        pixel_km = float(nf._pixel_km(rd))
        dom = np.asarray(native["_domain"], bool)
        row = {"anchor": anchor, "regime": item["regime"],
               "source_slot": payload.get("source_slot"),
               "pixel_km": round(pixel_km, 4), "leads": {}}
        for lead in LEADS:
            o, ov = observed(cfg, anchor, lead)
            if o is None:
                row["leads"][str(lead)] = {"error": "obs_unavailable"}
                continue
            m = metrics(native[lead], dom, o, ov, pixel_km)
            row["leads"][str(lead)] = m
            per_lead[lead].append(m)
        report["anchors"].append(row)
        print(anchor, item["regime"],
              {l: row["leads"].get(str(l), {}).get("reliable")
               for l in LEADS})
    for lead in LEADS:
        vals = per_lead[lead]
        n = len(vals)
        rel = sum(1 for x in vals if x.get("reliable"))
        def _mean(key):
            xs = [x["overlap"][key]["coverage"] for x in vals
                  if x.get("overlap", {}).get(key, {}).get("coverage") is not None]
            return round(float(np.mean(xs)), 4) if xs else None
        def _mean_iou(key):
            xs = [x["overlap"][key]["iou"] for x in vals
                  if x.get("overlap", {}).get(key, {}).get("iou") is not None]
            return round(float(np.mean(xs)), 4) if xs else None
        def _mean_bias(key):
            xs = [x["overlap"][key]["bias"] for x in vals
                  if x.get("overlap", {}).get(key, {}).get("bias") is not None]
            return round(float(np.mean(xs)), 4) if xs else None
        cors = [x.get("corr_0p5") for x in vals
                if x.get("corr_0p5") is not None]
        report["summary"][str(lead)] = {
            "cases": n,
            "reliable": int(rel),
            "fraction": (round(rel / float(n), 3) if n else None),
            "coverage_0p5": _mean("0.5"),
            "coverage_5": _mean("5.0"),
            "iou_0p5": _mean_iou("0.5"),
            "iou_5": _mean_iou("5.0"),
            "bias_0p5": _mean_bias("0.5"),
            "bias_5": _mean_bias("5.0"),
            "corr_0p5": (round(float(np.mean(cors)), 4) if cors else None),
        }
    path = os.path.join(args.out, "reliability90.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    lines = ["# Affidabilita' a +30/+60/+90 - criterio fissato prima",
             "",
             "Criterio: area 0,5 e 5 mm/h in [0,6;1,4]; centroide <=20 km; "
             "copertura >=50% dell'osservato >=5 mm/h.",
             "",
             "| ancora | regime | +30 | +60 | +90 |",
             "| :--- | :--- | :---: | :---: | :---: |"]
    for row in report["anchors"]:
        if "leads" not in row:
            lines.append("| %s | %s | errore: %s | | |" % (
                row["anchor"], row["regime"], row.get("error")))
            continue
        cells = []
        for lead in LEADS:
            m = row["leads"].get(str(lead), {})
            cells.append("OK" if m.get("reliable") else
                         ("no: " + ",".join(m.get("fail") or ["errore"])))
        lines.append("| %s | %s | %s |" % (row["anchor"], row["regime"],
                                            " | ".join(cells)))
    lines += ["", "## Percentuale affidabile", "",
              "| lead | casi | affidabili | frazione |", "| ---: | ---: | ---: | ---: |"]
    for lead in LEADS:
        s = report["summary"][str(lead)]
        lines.append("| +%d | %d | %d | %s |" % (
            lead, s["cases"], s["reliable"],
            ("%.1f%%" % (100.0 * s["fraction"]))
            if s["fraction"] is not None else "n/d"))
    lines += ["", "## Copertura / IoU / bias / correlazione (media)", "",
              "| lead | cop 0,5 | IoU 0,5 | bias 0,5 | cop 5 | IoU 5 | bias 5 | corr 0,5 |",
              "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for lead in LEADS:
        s = report["summary"][str(lead)]
        lines.append("| +%d | %s | %s | %s | %s | %s | %s | %s |" % (
            lead, s["coverage_0p5"], s["iou_0p5"], s["bias_0p5"],
            s["coverage_5"], s["iou_5"], s["bias_5"], s["corr_0p5"]))
    with open(os.path.join(args.out, "reliability90.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(json.dumps(report["summary"], indent=1))
    print("saved", path)


if __name__ == "__main__":
    main()
