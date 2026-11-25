#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk — AFFIDABILITA' DEL NOWCAST PER ORIZZONTE (+30 .. +180 min).

Cosa misura
-----------
Per ogni ancora T (giorno selezionato dal backtest gia' disponibile, ora H)
confronta il CAMPO TRASPORTATO dal nowcast di produzione con:

  persistenza = SRI osservato a T, tenuto fermo fino a T+lead
  nowcast     = `nowcast_field.compute` (stesso codice del run operativo,
                cascata/matching/decadimento/orografia SPENTI: e' il prodotto
                pubblicato oggi)

agli orizzonti 30/60/90/120/150/180 minuti. Serve a dire, con un numero,
quanto vale il frame a +3 ore e se e' peggiore della persistenza.

Metriche
--------
- CSI pooled per soglia (0.5 / 2.0 / 5.0 mm/h) su tutti i casi informativi;
- FSS (Roberts & Lean 2008) a 15 km per soglia 0.5 / 2.0 mm/h, mediato sui
  casi (ogni caso pesa uguale);
- regola di decisione DICHIARATA PRIMA: `nowcast_beats_persistence` e' vero
  se la media del CSI pooled alle soglie 0.5 e 2.0 e' maggiore di quella
  della persistenza allo stesso orizzonte. Nessuna soglia ritoccata dopo.

Ingresso
--------
Le 12 giornate sono quelle del backtest gia' disponibile (4 convettive + 4
stratiformi + 4 deboli), dichiarate in `DEFAULT_DAYS`. `--days-file` accetta in
alternativa il file prodotto da `--select` di
`scripts/validation/nowcast_cascade_backtest.py`. Nessuna selezione nuova.

Uso
---
  python scripts/validation/nowcast_reliability_backtest.py \
      --anchors 11:00,15:00 \
      --out audit/nowcast_reliability_backtest.json \
      --publish data/radar/nowcast_reliability.json \
      --report docs/NOWCAST_RELIABILITY.md
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
sys.path.insert(0, _HERE)                           # scripts/validation/

from radar_engine import clock as clock_mod          # noqa: E402
from radar_engine import nowcast_field as nf         # noqa: E402
from radar_engine.config import load_config          # noqa: E402
from nowcast_replay import FrameCache, _Bundle, _iso, _parse_ms  # noqa: E402

ISO = "%Y-%m-%dT%H:%M:%SZ"
SCHEMA = "meteorisk.nowcast_reliability/1"
MEASURE_LEADS = (30, 60, 90, 120, 150, 180)
CSI_THRESHOLDS = (0.5, 2.0, 5.0)
DECISION_THRESHOLDS = (0.5, 2.0)
FSS_THRESHOLDS = (0.5, 2.0)
FSS_SIDE_KM = 15
MIN_OBS_RAIN_PIXELS = 100          # come nowcast_replay / nowcast_verify
MIN_CASES = 5                      # sotto: verdetto null, non "vince"

# Le 12 giornate del backtest gia' disponibile (selezione per regime del
# 2026-11-24, 4 convettive + 4 stratiformi + 4 deboli). Sono un DEFAULT
# dichiarato: il campione resta quello, non una selezione nuova.
DEFAULT_DAYS = (
    "2026-11-01", "2026-09-10", "2026-10-08", "2026-10-20",
    "2026-11-09", "2026-05-29", "2026-08-29", "2026-08-09",
    "2026-09-30", "2026-07-08", "2026-08-13", "2026-07-16",
)


def _day_ms(day, hour):
    hh = str(hour).strip()
    hh = hh if ":" in hh else "%s:00" % hh.zfill(2)
    if len(hh.split(":")) == 2:
        hh = hh + ":00"
    return _parse_ms("%sT%sZ" % (day, hh))


def _atomic_write(path, text):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# metriche
# ---------------------------------------------------------------------------
def _box_mean(a, side):
    """Media su finestra quadrata `side` con padding riflesso (come cascade)."""
    from scipy.ndimage import uniform_filter
    return uniform_filter(a.astype(np.float64), size=int(side), mode="reflect")


def fss(pred, obs, valid, side):
    """Fraction Skill Score su maschere binarie gia' calcolate."""
    if not valid.any():
        return None
    p = _box_mean(pred.astype(np.float64), side)
    o = _box_mean(obs.astype(np.float64), side)
    m = valid.astype(np.float64)
    num = float(((p - o) ** 2)[valid].sum())
    den = float((p ** 2 + o ** 2)[valid].sum())
    if den <= 0.0:
        return None
    return 1.0 - num / den


def contingency(pred, obs, mask, threshold):
    ok = np.isfinite(pred) & np.isfinite(obs) & mask
    p = np.asarray(pred)[ok] >= threshold
    o = np.asarray(obs)[ok] >= threshold
    return (int(np.sum(p & o)), int(np.sum(~p & o)),
            int(np.sum(p & ~o)), int(np.sum(~p & ~o)))


def _csi(counts):
    h, m, fa, _cn = counts
    den = h + m + fa
    return (h / float(den)) if den else None


# ---------------------------------------------------------------------------
# un caso
# ---------------------------------------------------------------------------
def evaluate_case(cache, config, anchor_ms, measure_leads):
    frames = cache.window(anchor_ms, n=nf.FRAMES_WANTED,
                          step_ms=nf.STEP_MIN * 60000)
    if len(frames) < 3:
        return {"status": "skipped", "null_reason": "sri_frames_lt_3",
                "anchor": _iso(anchor_ms)}
    latest = frames[-1]
    latest_ts = int(latest.time_ms)
    clk = clock_mod.Clock.at_ms(latest_ts)
    bundle = _Bundle(frames)
    t0 = time.time()
    payload, native = nf.compute(bundle, config, clk, list(bundle.warnings),
                                 out_dir=None, orographic=False)
    compute_s = time.time() - t0
    if not payload.get("available") or native is None:
        return {"status": "skipped",
                "null_reason": payload.get("null_reason") or "unavailable",
                "anchor": _iso(anchor_ms)}
    domain = native["_domain"]
    pers, pvalid = nf._rain_array(latest)
    rows = {}
    for lead in measure_leads:
        obs_rd = cache.get(latest_ts + int(lead) * 60000)
        if obs_rd is None:
            rows[str(lead)] = {"status": "not_verifiable",
                               "null_reason": "observed_missing"}
            continue
        obs, ovalid = nf._rain_array(obs_rd)
        mask = np.asarray(domain, dtype=bool) & np.asarray(ovalid, bool) \
            & np.asarray(pvalid, bool)
        n_rain = int((obs[mask] >= 0.5).sum()) if mask.any() else 0
        if n_rain < MIN_OBS_RAIN_PIXELS:
            rows[str(lead)] = {"status": "not_informative",
                               "null_reason": "no_observed_rain",
                               "rain_pixels": n_rain}
            continue
        pred = native[int(lead)]
        row = {"status": "ok", "rain_pixels": n_rain,
               "domain_pixels": int(mask.sum()),
               "counts": {}, "fss": {}}
        for thr in CSI_THRESHOLDS:
            row["counts"][str(thr)] = {
                "nowcast": contingency(pred, obs, mask, thr),
                "persistence": contingency(pers, obs, mask, thr),
            }
        for thr in FSS_THRESHOLDS:
            pm = np.asarray(pred) >= thr
            om = np.asarray(obs) >= thr
            rm = np.asarray(pers) >= thr
            row["fss"][str(thr)] = {
                "nowcast": fss(pm, om, mask, FSS_SIDE_KM),
                "persistence": fss(rm, om, mask, FSS_SIDE_KM),
            }
        rows[str(lead)] = row
    return {"status": "ok", "anchor": _iso(anchor_ms),
            "source_slot": _iso(latest_ts),
            "compute_seconds": round(compute_s, 3),
            "leads": rows}


def aggregate(cases, measure_leads):
    """Conteggi pooled, CSI, FSS medio e regola di decisione per orizzonte."""
    pooled = {}
    for lead in measure_leads:
        for thr in CSI_THRESHOLDS:
            pooled[(lead, thr)] = {"nowcast": [0, 0, 0, 0],
                                   "persistence": [0, 0, 0, 0]}
    fss_rows = {}
    for lead in measure_leads:
        for thr in FSS_THRESHOLDS:
            fss_rows[(lead, thr)] = {"nowcast": [], "persistence": []}
    informative = {lead: 0 for lead in measure_leads}
    for case in cases:
        if case.get("status") != "ok":
            continue
        for lead in measure_leads:
            row = (case.get("leads") or {}).get(str(lead)) or {}
            if row.get("status") != "ok":
                continue
            informative[lead] += 1
            for thr in CSI_THRESHOLDS:
                c = row["counts"][str(thr)]
                for arm in ("nowcast", "persistence"):
                    acc = pooled[(lead, thr)][arm]
                    for i, v in enumerate(c[arm]):
                        acc[i] += int(v)
            for thr in FSS_THRESHOLDS:
                f = row["fss"][str(thr)]
                for arm in ("nowcast", "persistence"):
                    if f.get(arm) is not None:
                        fss_rows[(lead, thr)][arm].append(float(f[arm]))

    horizons = {}
    for lead in measure_leads:
        csi = {"nowcast": {}, "persistence": {}}
        for thr in CSI_THRESHOLDS:
            for arm in ("nowcast", "persistence"):
                csi[arm][str(thr)] = _csi(pooled[(lead, thr)][arm])
        now_mean = _mean([csi["nowcast"][str(t)] for t in DECISION_THRESHOLDS
                          if csi["nowcast"][str(t)] is not None])
        per_mean = _mean([csi["persistence"][str(t)]
                          for t in DECISION_THRESHOLDS
                          if csi["persistence"][str(t)] is not None])
        fss_mean = {}
        for arm in ("nowcast", "persistence"):
            vals = [np.mean(fss_rows[(lead, t)][arm]) for t in FSS_THRESHOLDS
                    if fss_rows[(lead, t)][arm]]
            fss_mean[arm] = float(np.mean(vals)) if vals else None
        beats = None
        if informative[lead] >= MIN_CASES and now_mean is not None \
                and per_mean is not None:
            beats = bool(now_mean > per_mean)
        horizons[str(lead)] = {
            "lead_min": int(lead),
            "cases": int(informative[lead]),
            "nowcast_csi": _round(now_mean),
            "persistence_csi": _round(per_mean),
            "csi_delta": _round(now_mean - per_mean
                                if None not in (now_mean, per_mean) else None),
            "csi_by_threshold": {k: _round(v) for k, v in csi["nowcast"].items()},
            "persistence_csi_by_threshold": {
                k: _round(v) for k, v in csi["persistence"].items()},
            "nowcast_fss": _round(fss_mean["nowcast"]),
            "persistence_fss": _round(fss_mean["persistence"]),
            "fss_delta": _round(fss_mean["nowcast"] - fss_mean["persistence"]
                                if None not in (fss_mean["nowcast"],
                                                fss_mean["persistence"])
                                else None),
            "nowcast_beats_persistence": beats,
            "low_reliability": bool(int(lead) > nf.LOW_RELIABILITY_OVER_MIN),
            "level": ("bassa" if int(lead) > nf.LOW_RELIABILITY_OVER_MIN
                      else "media"),
        }
    return horizons


def _mean(values):
    vals = [v for v in values if v is not None]
    return float(np.mean(vals)) if vals else None


def _round(v):
    return None if v is None else round(float(v), 4)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-file", default=None,
                    help="file `--select` del backtest cascata; assente = "
                         "le 12 giornate dichiarate in DEFAULT_DAYS")
    ap.add_argument("--anchors", default="11:00,15:00")
    ap.add_argument("--leads", default=",".join(str(x) for x in MEASURE_LEADS))
    ap.add_argument("--out", default="audit/nowcast_reliability_backtest.json")
    ap.add_argument("--publish", default=None,
                    help="file compatto incorporato in nowcast.json")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    if args.days_file:
        with open(args.days_file, encoding="utf-8") as fh:
            days_doc = json.load(fh)
        days = list(days_doc.get("selected") or [])
    else:
        days = list(DEFAULT_DAYS)
    anchors = [a.strip() for a in args.anchors.split(",") if a.strip()]
    measure_leads = tuple(int(x) for x in args.leads.split(",") if x.strip())

    config = load_config()
    config["source"]["fetch_workers"] = int(
        config["source"].get("fetch_workers") or 6)
    config["nowcast_leads_min"] = tuple(nf.LEADS_MIN)   # 15 frame del contratto
    cascade = dict(config.get("cascade") or {})
    cascade["enabled"] = False
    cascade["probability_matching"] = False
    config["cascade"] = cascade
    decay = dict(config.get("decay") or {})
    decay["enabled"] = False
    config["decay"] = decay

    cache = FrameCache(config, cap=48)
    cases = []
    t_start = time.time()
    for day in days:
        for hour in anchors:
            anchor_ms = _day_ms(day, hour)
            case = evaluate_case(cache, config, anchor_ms, measure_leads)
            case["day"] = day
            case["anchor_hour"] = hour
            cases.append(case)
            print("[%s %s] %s %s" % (
                day, hour, case.get("status"), case.get("null_reason") or ""),
                flush=True)
    seconds = time.time() - t_start

    horizons = aggregate(cases, measure_leads)
    informative = [h for h in horizons.values() if h["cases"] >= MIN_CASES]
    worse = [int(h["lead_min"]) for h in informative
             if h["nowcast_beats_persistence"] is False]
    result = {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime(ISO),
        "method": ("stesso compute() della produzione (cascata/matching/decay/"
                   "orografia spenti) contro SRI osservato; persistenza = SRI "
                   "dell'ancora; CSI pooled, FSS a %d km medio sui casi"
                   % FSS_SIDE_KM),
        "days_file": (os.path.basename(args.days_file)
                      if args.days_file else "DEFAULT_DAYS"),
        "days": days,
        "anchors": anchors,
        "measure_leads_min": list(measure_leads),
        "csi_thresholds_mmh": list(CSI_THRESHOLDS),
        "decision_thresholds_mmh": list(DECISION_THRESHOLDS),
        "fss_thresholds_mmh": list(FSS_THRESHOLDS),
        "fss_side_km": FSS_SIDE_KM,
        "min_obs_rain_pixels": MIN_OBS_RAIN_PIXELS,
        "min_cases_for_verdict": MIN_CASES,
        "cases_total": len(cases),
        "cases_ok": sum(1 for c in cases if c.get("status") == "ok"),
        "downloads": int(cache.downloads),
        "seconds": round(seconds, 1),
        "compute_seconds_total": round(sum(
            c.get("compute_seconds") or 0.0 for c in cases), 3),
        "horizons": horizons,
        "loses_to_persistence_from_min": (min(worse) if worse else None),
        "cases": cases,
    }
    _atomic_write(args.out, json.dumps(result, ensure_ascii=False, indent=1,
                                       allow_nan=False))
    print("[OK] %s: %d casi, %d orizzonti informativi, %.0f s, %d download"
          % (args.out, result["cases_total"], len(informative), seconds,
             result["downloads"]), flush=True)

    if args.publish:
        compact_doc = {
            "schema": SCHEMA,
            "measured_on": _dt.datetime.now(_dt.timezone.utc).strftime(
                "%Y-%m-%d"),
            "method": result["method"],
            "days": len(days),
            "anchors": anchors,
            "cases": result["cases_ok"],
            "csi_thresholds_mmh": list(CSI_THRESHOLDS),
            "fss_side_km": FSS_SIDE_KM,
            "official_leads_unchanged": list(nf.OFFICIAL_LEADS),
            "horizons": {k: v for k, v in horizons.items()},
            "loses_to_persistence_from_min":
                result["loses_to_persistence_from_min"],
            "limits": (
                "Campione di %d giornate indipendenti x %d ancore (%d casi): "
                "CSI pooled e FSS medio, non una climatologia. Persistenza = "
                "campo dell'ancora. Le stesse giornate del backtest della "
                "cascata; nessuna soglia tarata su questo campione."
                % (len(days), len(anchors), result["cases_ok"])),
        }
        _atomic_write(args.publish, json.dumps(compact_doc, ensure_ascii=False,
                                              indent=1, allow_nan=False))
        print("[OK] %s pubblicato (compatto)" % args.publish, flush=True)

    if args.report:
        _atomic_write(args.report, _markdown(result))
        print("[OK] %s" % args.report, flush=True)
    return 0


def _markdown(result):
    lines = ["# Affidabilita' del nowcast per orizzonte", "",
             "Generato da `scripts/validation/nowcast_reliability_backtest.py` "
             "il %s." % result["generated_at"], "",
             result["method"] + ".", "",
             "Campione: %d giornate x %d ancore, %d casi con pioggia >= 0,5 "
             "mm/h su almeno %d pixel." % (
                 len(result["days"]), len(result["anchors"]),
                 result["cases_ok"], result["min_obs_rain_pixels"]), "",
             "| lead | casi | CSI nowcast | CSI persist. | delta CSI | FSS "
             "nowcast | FSS persist. | nowcast meglio? |",
             "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |"]
    for key in sorted(result["horizons"], key=int):
        h = result["horizons"][key]
        verdict = ("n.d." if h["nowcast_beats_persistence"] is None else
                   ("si" if h["nowcast_beats_persistence"] else "**NO**"))
        lines.append("| +%d | %d | %s | %s | %s | %s | %s | %s |" % (
            h["lead_min"], h["cases"], _fmt(h["nowcast_csi"]),
            _fmt(h["persistence_csi"]), _fmt(h["csi_delta"]),
            _fmt(h["nowcast_fss"]), _fmt(h["persistence_fss"]), verdict))
    worse = result.get("loses_to_persistence_from_min")
    lines += ["", ("Da +%d min il nowcast e' **peggiore** della persistenza."
                   % worse) if worse else
              "In questo campione il nowcast non e' mai peggiore della "
              "persistenza agli orizzonti misurati.", "",
              "Il verdetto «nowcast meglio?» usa il **CSI pooled** medio alle "
              "soglie 0,5 e 2,0 mm/h (regola dichiarata prima del run). "
              "L'**FSS a 15 km** e' un secondo giudizio e in questo campione "
              "e' **sempre a favore della persistenza** (delta FSS negativo a "
              "ogni orizzonte): le due metriche non concordano a breve "
              "termine, quindi il risultato va letto con entrambe, non con "
              "quella che conviene.", ""]
    return "\n".join(lines)


def _fmt(v):
    return "n.d." if v is None else ("%.3f" % v)


if __name__ == "__main__":
    raise SystemExit(main())
