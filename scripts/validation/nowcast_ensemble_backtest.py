#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk — BACKTEST DI CALIBRAZIONE DEL NOWCAST PROBABILISTICO.

Cosa misura
-----------
Per ogni ancora T su 12 giornate (le stesse del backtest della cascata,
`audit/cascade_days.json`: 4 convettive + 4 stratiformi + 4 deboli) e per i
lead 15/30/60 minuti:

  ensemble = `nowcast_field.compute` con `cascade.enabled=true` e
             `ensemble.enabled=true` (stesso codice della produzione);
  verita'  = SRI osservato a T+lead.

Metriche (definite PRIMA di guardare i risultati)
-------------------------------------------------
- BRIER SCORE per soglia: BS = media((p - o)^2) sui pixel del dominio con
  osservato valido. BSS = 1 - BS/BS_clim, climatologia = frequenza base del
  PERIODO (tutti i casi), leave-one-day-out per il punteggio giornaliero.
- RELIABILITY a 10 bin: per ogni bin di probabilita', frequenza osservata.
  Criterio: |media(p) - freq(osservata)| <= 0,10 nei bin con >= 30 casi.
- CRPS sull'intensita' (mm/h), stimatore fair sui 20 membri, su un
  sottocampione SISTEMATICO di `CRPS_SUBSAMPLE` pixel per caso/lead
  (dichiarato: il CRPS pieno su 945.000 pixel x 24 casi e' lo stesso numero a
  meno di errore di campionamento, ma costa ~50x). Climatologia = CDF
  empirica dei valori osservati delle ALTRE giornate (leave-one-day-out),
  rappresentata da 200 quantili; CRPS_clim = (1/K)sum|q_k-y| - costante.
- SPREAD-SKILL: RMSE dell'ensemble mean per bin di spread; correlazione di
  Spearman fra spread e |errore|. Se lo spread e' troppo piccolo (ratio
  RMSE/spread > 1) l'ensemble e' sotto-disperso e il fattore di inflazione
  necessario e' `RMSE/spread` (dichiarato nel report).

Criteri (dichiarati prima, non ritoccati)
----------------------------------------
1) BSS > 0 su >= 60% delle giornate (>= 8 su 12) per le soglie 0,5/2/5 mm/h;
2) reliability entro +-10% dalla diagonale nei bin con >= 30 casi;
3) CRPS migliore della climatologia.

Uso
---
  python scripts/validation/nowcast_ensemble_backtest.py \
      --out audit/nowcast_ensemble_backtest.json \
      --report docs/NOWCAST_ENSEMBLE_ESITO.md
  # prova rapida: --max-cases 2 --out audit/_smoke.json
"""

import argparse
import datetime as _dt
import json
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from radar_engine import clock as clock_mod          # noqa: E402
from radar_engine import nowcast_field as nf         # noqa: E402
from radar_engine.config import load_config          # noqa: E402
from nowcast_replay import FrameCache, _Bundle, _iso, _parse_ms  # noqa: E402

ISO = "%Y-%m-%dT%H:%M:%SZ"
SCHEMA = "meteorisk.nowcast_ensemble_backtest/1"
LEADS = (15, 30, 60)
THRESHOLDS = (0.5, 2.0, 5.0, 10.0, 20.0)
CRITERION_THRESHOLDS = (0.5, 2.0, 5.0)
CRPS_SUBSAMPLE = 40000
MIN_OBS_RAIN_PIXELS = 100
RELIABILITY_BINS = 10
RELIABILITY_TOLERANCE = 0.10
MIN_BIN_CASES = 30
SPREAD_BINS = 10
CLIM_QUANTILES = 200
MIN_DAYS_PASS_FRACTION = 0.60

# Le 12 giornate del backtest della cascata (audit/cascade_days.json):
# 4 convettive + 4 stratiformi + 4 deboli. NON riselezionate qui.
DEFAULT_DAYS = (
    "2026-11-01", "2026-09-10", "2026-10-08", "2026-10-20",
    "2026-11-09", "2026-05-29", "2026-08-29", "2026-08-09",
    "2026-09-30", "2026-07-08", "2026-08-13", "2026-07-16",
)
DEFAULT_ANCHORS = ("11:00", "15:00")


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


def _subsample(mask, target):
    """Indici lineari SISTEMATICI dei pixel validi (nessun RNG)."""
    flat = np.flatnonzero(mask.ravel())
    if flat.size <= target:
        return flat
    step = max(1, flat.size // target)
    return flat[::step][:target]


def ensemble_crps(members, obs):
    """CRPS fair per colonna: members (M,N), obs (N,). -> (N,)."""
    m, n = members.shape
    x = np.sort(members, axis=0)
    first = np.abs(x - obs[None, :]).mean(axis=0)
    weights = (2.0 * np.arange(m, dtype=np.float64) - m + 1.0) / (m * m)
    second = (x * weights[:, None]).sum(axis=0)
    return first - second


def climatology_crps(obs, quantiles):
    """CRPS di una CDF empirica (quantili) vs obs. Costante sottratta una volta."""
    q = np.asarray(quantiles, dtype=np.float64)
    k = q.size
    first = np.abs(obs[:, None] - q[None, :]).mean(axis=1)
    qs = np.sort(q)
    const = (np.abs(qs[:, None] - qs[None, :]).sum() / (2.0 * k * k))
    return first - const


def evaluate_case(cache, config, anchor_ms, leads):
    frames = cache.window(anchor_ms, n=24, step_ms=nf.STEP_MIN * 60000)
    if len(frames) < 6:
        return {"status": "skipped", "null_reason": "sri_frames_lt_6",
                "anchor": _iso(anchor_ms)}
    bundle = _Bundle(frames)
    clk = clock_mod.Clock.at_ms(int(frames[-1].time_ms))
    warn = []
    t0 = time.time()
    payload, native = nf.compute(bundle, config, clk, warn, out_dir=None)
    compute_s = time.time() - t0
    if not payload.get("available") or native is None:
        return {"status": "skipped",
                "null_reason": payload.get("null_reason") or "unavailable",
                "anchor": _iso(anchor_ms)}
    ens = payload.get("ensemble") or {}
    if not ens.get("applied"):
        return {"status": "skipped", "null_reason": "ensemble_not_applied",
                "anchor": _iso(anchor_ms)}
    latest, pvalid = nf._rain_array(frames[-1])
    domain = np.asarray(native["_domain"], dtype=bool)
    latest_ts = int(frames[-1].time_ms)
    out = {"status": "ok", "anchor": _iso(anchor_ms),
           "source_slot": _iso(latest_ts),
           "compute_seconds": round(compute_s, 3),
           "ensemble_seconds": round(float(
               (ens.get("cost_seconds") or {}).get("total") or 0.0), 3),
           "leads": {}}
    for lead in leads:
        obs_rd = cache.get(latest_ts + int(lead) * 60000)
        if obs_rd is None:
            out["leads"][str(lead)] = {"status": "not_verifiable",
                                       "null_reason": "observed_missing"}
            continue
        obs, ovalid = nf._rain_array(obs_rd)
        mask = domain & np.asarray(ovalid, bool) & np.asarray(pvalid, bool)
        n_rain = int((obs[mask] >= 0.5).sum()) if mask.any() else 0
        if n_rain < MIN_OBS_RAIN_PIXELS:
            out["leads"][str(lead)] = {"status": "not_informative",
                                       "null_reason": "no_observed_rain",
                                       "rain_pixels": n_rain}
            continue
        fields = (native.get("_ensemble") or {}).get(int(lead)) or {}
        probs = fields.get("probabilities") or {}
        if not probs:
            out["leads"][str(lead)] = {"status": "not_verifiable",
                                       "null_reason": "probabilities_missing"}
            continue
        o = np.asarray(obs, dtype=np.float64)[mask]
        row = {"status": "ok", "rain_pixels": n_rain,
               "domain_pixels": int(mask.sum()),
               "brier": {}, "bins": {}}
        for thr in THRESHOLDS:
            key = "prob_%s" % _thr_key(thr)
            if key not in probs:
                continue
            p = np.asarray(probs[key], dtype=np.float64)[mask]
            ob = (o >= float(thr)).astype(np.float64)
            row["brier"][str(thr)] = {
                "bs": round(float(np.mean((p - ob) ** 2)), 6),
                "base_rate": round(float(ob.mean()), 6),
                "ones": int(ob.sum()),
                "n": int(ob.size)}
            idx = np.clip((p * RELIABILITY_BINS).astype(np.int64),
                          0, RELIABILITY_BINS - 1)
            cnt = np.bincount(idx, minlength=RELIABILITY_BINS)
            sp = np.bincount(idx, weights=p, minlength=RELIABILITY_BINS)
            so = np.bincount(idx, weights=ob, minlength=RELIABILITY_BINS)
            row["bins"][str(thr)] = {
                "count": cnt.tolist(),
                "sum_p": sp.tolist(),
                "sum_o": so.tolist()}
        # CRPS e spread-skill sul sottocampione sistematico
        sub = _subsample(mask, CRPS_SUBSAMPLE)
        members = fields.get("members")
        if members is None:
            out["leads"][str(lead)] = row
            continue
        rr, cc = np.unravel_index(sub, mask.shape)
        mem = np.asarray(members, dtype=np.float32)[:, rr, cc]
        obs_sub = np.asarray(obs, dtype=np.float64)[rr, cc]
        crps = ensemble_crps(mem.astype(np.float64), obs_sub)
        spread = np.asarray(fields["spread"], dtype=np.float64)[rr, cc]
        mean = np.asarray(fields["mean"], dtype=np.float64)[rr, cc]
        err = np.abs(mean - obs_sub)
        row["crps"] = {
            "mean": round(float(np.mean(crps)), 6),
            "n": int(crps.size)}
        row["spread_skill"] = {
            "rmse": round(float(np.sqrt(np.mean((mean - obs_sub) ** 2))), 6),
            "spread_mean": round(float(np.mean(spread)), 6),
            "ratio_rmse_spread": round(float(
                np.sqrt(np.mean((mean - obs_sub) ** 2))
                / max(1e-9, np.mean(spread))), 4)}
        row["_obs_sub"] = obs_sub.astype(np.float32)
        row["_spread_sub"] = spread.astype(np.float32)
        row["_err_sub"] = err.astype(np.float32)
        out["leads"][str(lead)] = row
    if native is not None:
        native.pop("_ensemble", None)
    out["warnings"] = [w for w in warn if "ensemble" in str(w)]
    return out


def _thr_key(thr):
    return ("%.1f" % float(thr)).replace(".", "p")


def _spearman(a, b):
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.size < 3:
        return None
    ra = np.argsort(np.argsort(a)).astype(np.float64)
    rb = np.argsort(np.argsort(b)).astype(np.float64)
    ra -= ra.mean()
    rb -= rb.mean()
    den = float(np.sqrt((ra * ra).sum() * (rb * rb).sum()))
    return (float((ra * rb).sum() / den) if den > 0 else None)


def aggregate(cases, days, leads):
    """Punteggi pooled, per giornata, reliability, CRPS, spread-skill."""
    periods = {}
    for thr in THRESHOLDS:
        periods[str(thr)] = {"n": 0, "ones": 0}
    for case in cases:
        if case.get("status") != "ok":
            continue
        for lead in leads:
            row = (case.get("leads") or {}).get(str(lead)) or {}
            if row.get("status") != "ok":
                continue
            for thr in THRESHOLDS:
                b = (row.get("brier") or {}).get(str(thr))
                if b:
                    periods[str(thr)]["n"] += int(b["n"])
                    periods[str(thr)]["ones"] += int(b.get("ones") or 0)
    base_rate = {k: (v["ones"] / float(v["n"]) if v["n"] else None)
                 for k, v in periods.items()}

    # BS pooled per soglia/lead + per giornata
    pooled = {}
    per_day = {}
    for thr in THRESHOLDS:
        pooled[str(thr)] = {str(l): {"n": 0, "sse": 0.0, "ones": 0}
                            for l in leads}
    for case in cases:
        if case.get("status") != "ok":
            continue
        day = case.get("day")
        per_day.setdefault(day, {t: {"n": 0, "sse": 0.0, "ones": 0}
                                 for t in [str(x) for x in THRESHOLDS]})
        for lead in leads:
            row = (case.get("leads") or {}).get(str(lead)) or {}
            if row.get("status") != "ok":
                continue
            for thr in THRESHOLDS:
                b = (row.get("brier") or {}).get(str(thr))
                if not b:
                    continue
                n = int(b["n"])
                sse = float(b["bs"]) * n
                ones = int(b.get("ones") or 0)
                pooled[str(thr)][str(lead)]["n"] += n
                pooled[str(thr)][str(lead)]["sse"] += sse
                pooled[str(thr)][str(lead)]["ones"] += ones
                d = per_day[day][str(thr)]
                d["n"] += n
                d["sse"] += sse
                d["ones"] += ones

    brier = {}
    for thr in THRESHOLDS:
        brier[str(thr)] = {}
        p0 = base_rate[str(thr)]
        for lead in leads:
            acc = pooled[str(thr)][str(lead)]
            if not acc["n"] or p0 is None:
                brier[str(thr)][str(lead)] = {
                    "bs": None, "bss": None, "n": int(acc["n"])}
                continue
            bs = acc["sse"] / acc["n"]
            bsc = p0 * (1.0 - p0)
            brier[str(thr)][str(lead)] = {
                "bs": round(bs, 6),
                "bs_clim": round(bsc, 6),
                "bss": round(1.0 - bs / bsc, 6) if bsc > 0 else None,
                "n": int(acc["n"]),
                "observed_rate": round(acc["ones"] / float(acc["n"]), 6)}

    # BSS per giornata (soglie di criterio, pooled su ancore e lead)
    day_rows = {}
    for day in days:
        d = per_day.get(day) or {}
        day_rows[day] = {}
        for thr in CRITERION_THRESHOLDS:
            acc = d.get(str(thr)) or {"n": 0, "sse": 0.0}
            p0 = base_rate[str(thr)]
            if not acc["n"] or p0 is None:
                day_rows[day][str(thr)] = None
                continue
            bs = acc["sse"] / acc["n"]
            bsc = p0 * (1.0 - p0)
            day_rows[day][str(thr)] = (
                round(1.0 - bs / bsc, 6) if bsc > 0 else None)

    pass_counts = {}
    for thr in CRITERION_THRESHOLDS:
        vals = [day_rows[d][str(thr)] for d in days
                if day_rows[d].get(str(thr)) is not None]
        n_pass = sum(1 for v in vals if v > 0.0)
        pass_counts[str(thr)] = {
            "days_evaluated": len(vals),
            "days_bss_positive": int(n_pass),
            "fraction": round(n_pass / float(len(vals)), 4) if vals else None,
            "passes": bool(vals and n_pass / float(len(vals))
                           >= MIN_DAYS_PASS_FRACTION)}

    # reliability pooled (tutti i lead e casi)
    reliability = {}
    for thr in THRESHOLDS:
        cnt = np.zeros(RELIABILITY_BINS)
        sp = np.zeros(RELIABILITY_BINS)
        so = np.zeros(RELIABILITY_BINS)
        for case in cases:
            if case.get("status") != "ok":
                continue
            for lead in leads:
                row = (case.get("leads") or {}).get(str(lead)) or {}
                if row.get("status") != "ok":
                    continue
                b = (row.get("bins") or {}).get(str(thr))
                if not b:
                    continue
                cnt += np.asarray(b["count"], dtype=np.float64)
                sp += np.asarray(b["sum_p"], dtype=np.float64)
                so += np.asarray(b["sum_o"], dtype=np.float64)
        rows = []
        inside_all = True
        for i in range(RELIABILITY_BINS):
            if cnt[i] < MIN_BIN_CASES or cnt[i] <= 0:
                rows.append({"bin": i, "count": int(cnt[i]),
                             "mean_prob": None, "observed": None,
                             "delta": None, "within_tolerance": None})
                continue
            mp = sp[i] / cnt[i]
            ob = so[i] / cnt[i]
            delta = abs(mp - ob)
            ok = bool(delta <= RELIABILITY_TOLERANCE)
            inside_all = inside_all and ok
            rows.append({"bin": i, "count": int(cnt[i]),
                         "mean_prob": round(float(mp), 4),
                         "observed": round(float(ob), 4),
                         "delta": round(float(delta), 4),
                         "within_tolerance": ok})
        worst_vals = [r["delta"] for r in rows if r["delta"] is not None]
        worst = max(worst_vals) if worst_vals else None
        reliability[str(thr)] = {
            "bins": rows,
            "max_abs_delta": (round(float(worst), 4)
                              if worst is not None else None),
            "all_informative_bins_within": (inside_all
                                            if any(r["count"] >= MIN_BIN_CASES
                                                   for r in rows) else None)}

    # CRPS: ensemble vs climatologia leave-one-day-out
    obs_by_day = {d: [] for d in days}
    for case in cases:
        if case.get("status") != "ok":
            continue
        for lead in leads:
            row = (case.get("leads") or {}).get(str(lead)) or {}
            if row.get("_obs_sub") is not None:
                obs_by_day[case["day"]].append(np.asarray(row["_obs_sub"]))
    crps_ens = []
    crps_clim = []
    crps_by_lead = {str(l): {"ens": [], "clim": []} for l in leads}
    for case in cases:
        if case.get("status") != "ok":
            continue
        day = case["day"]
        others = [a for d in days if d != day for a in obs_by_day[d]]
        if not others:
            continue
        pool = np.concatenate(others)
        if pool.size > 200000:
            pool = pool[::max(1, pool.size // 200000)]
        q = np.quantile(pool, np.linspace(0.0, 1.0, CLIM_QUANTILES))
        for lead in leads:
            row = (case.get("leads") or {}).get(str(lead)) or {}
            obs = row.get("_obs_sub")
            if obs is None:
                continue
            ens_ok = row.get("crps") is not None
            clim = climatology_crps(np.asarray(obs, dtype=np.float64), q)
            crps_clim.append(float(np.mean(clim)))
            crps_by_lead[str(lead)]["clim"].append(float(np.mean(clim)))
            if ens_ok:
                crps_ens.append(float(row["crps"]["mean"]))
                crps_by_lead[str(lead)]["ens"].append(
                    float(row["crps"]["mean"]))
    crps_block = {
        "ensemble_mean": (round(float(np.mean(crps_ens)), 6)
                          if crps_ens else None),
        "climatology_mean": (round(float(np.mean(crps_clim)), 6)
                             if crps_clim else None),
        "better_than_climatology": (bool(np.mean(crps_ens)
                                         < np.mean(crps_clim))
                                    if crps_ens and crps_clim else None),
        "n_cases": len(crps_ens),
        "by_lead": {k: {
            "ensemble": round(float(np.mean(v["ens"])), 6) if v["ens"] else None,
            "climatology": (round(float(np.mean(v["clim"])), 6)
                            if v["clim"] else None),
        } for k, v in crps_by_lead.items()},
    }

    # spread-skill pooled
    spreads = []
    errs = []
    rows_ss = []
    all_spread = []
    all_err = []
    for case in cases:
        if case.get("status") != "ok":
            continue
        for lead in leads:
            row = (case.get("leads") or {}).get(str(lead)) or {}
            if row.get("_spread_sub") is None:
                continue
            sp = np.asarray(row["_spread_sub"], dtype=np.float64)
            er = np.asarray(row["_err_sub"], dtype=np.float64)
            all_spread.append(sp)
            all_err.append(er)
            rows_ss.append((int(lead), row.get("spread_skill") or {}))
    ss_block = {}
    if all_spread:
        sp = np.concatenate(all_spread)
        er = np.concatenate(all_err)
        rmse = float(np.sqrt(np.mean(er ** 2)))
        spread_mean = float(np.mean(sp))
        ratio = rmse / max(1e-9, spread_mean)
        edges = np.quantile(sp, np.linspace(0.0, 1.0, SPREAD_BINS + 1))
        edges = np.unique(edges)
        bins = []
        for i in range(len(edges) - 1):
            sel = (sp >= edges[i]) & (sp <= edges[i + 1])
            if not sel.any():
                continue
            bins.append({
                "spread_low": round(float(edges[i]), 4),
                "spread_high": round(float(edges[i + 1]), 4),
                "n": int(sel.sum()),
                "spread_mean": round(float(sp[sel].mean()), 4),
                "rmse": round(float(np.sqrt(np.mean(er[sel] ** 2))), 4),
                "bias_ratio": round(float(
                    np.sqrt(np.mean(er[sel] ** 2))
                    / max(1e-9, sp[sel].mean())), 4)})
        ss_block = {
            "rmse": round(rmse, 6),
            "spread_mean": round(spread_mean, 6),
            "ratio_rmse_spread": round(ratio, 4),
            "spearman_spread_abserr": (round(float(_spearman(sp, er)), 4)
                                       if _spearman(sp, er) is not None
                                       else None),
            "bins": bins,
            "inflation_needed": round(max(1.0, ratio), 4),
            "verdict": ("under_dispersed" if ratio > 1.05 else
                        ("over_dispersed" if ratio < 0.95 else "calibrated")),
            "by_case_lead": [
                {"lead_min": l, **ss} for l, ss in rows_ss],
        }

    criteria = {
        "bss_positive_days": pass_counts,
        "reliability_within_10pct": {
            str(t): (reliability[str(t)]["all_informative_bins_within"])
            for t in CRITERION_THRESHOLDS},
        "crps_better_than_climatology": crps_block.get(
            "better_than_climatology"),
    }
    passed = bool(
        all(v["passes"] for v in pass_counts.values())
        and all(v is True for v in criteria["reliability_within_10pct"].values())
        and criteria["crps_better_than_climatology"] is True)
    return {"base_rate": base_rate, "brier": brier,
            "bss_by_day": day_rows, "bss_days": pass_counts,
            "reliability": reliability, "crps": crps_block,
            "spread_skill": ss_block, "criteria": criteria,
            "passes": passed}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default=",".join(DEFAULT_DAYS))
    ap.add_argument("--anchors", default=",".join(DEFAULT_ANCHORS))
    ap.add_argument("--leads", default=",".join(str(x) for x in LEADS))
    ap.add_argument("--max-cases", type=int, default=0)
    ap.add_argument("--motion-inflation", type=float, default=1.0)
    ap.add_argument("--noise-inflation", type=float, default=1.0)
    ap.add_argument("--out", default="audit/nowcast_ensemble_backtest.json")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    anchors = [a.strip() for a in args.anchors.split(",") if a.strip()]
    leads = tuple(int(x) for x in args.leads.split(",") if x.strip())

    config = load_config()
    config["source"]["fetch_workers"] = int(
        config["source"].get("fetch_workers") or 6)
    config["nowcast_leads_min"] = tuple(nf.LEADS_MIN)
    cascade = dict(config.get("cascade") or {})
    cascade["enabled"] = True
    cascade["probability_matching"] = True
    config["cascade"] = cascade
    ensemble = dict(config.get("ensemble") or {})
    ensemble["enabled"] = True
    ensemble["keep_members"] = True
    ensemble["motion_inflation"] = float(args.motion_inflation)
    ensemble["noise_inflation"] = float(args.noise_inflation)
    config["ensemble"] = ensemble

    cache = FrameCache(config, cap=64)
    cases = []
    t_start = time.time()
    total = len(days) * len(anchors)
    for day in days:
        for hour in anchors:
            if args.max_cases and len(cases) >= args.max_cases:
                break
            anchor_ms = _day_ms(day, hour)
            case = evaluate_case(cache, config, anchor_ms, leads)
            case["day"] = day
            case["anchor_hour"] = hour
            cases.append(case)
            print("[%s %s] %s %s (%.1f s, ens %.1f s)" % (
                day, hour, case.get("status"),
                case.get("null_reason") or "",
                float(case.get("compute_seconds") or 0.0),
                float(case.get("ensemble_seconds") or 0.0)), flush=True)
    seconds = time.time() - t_start

    agg = aggregate(cases, days, leads)
    result = {
        "schema": SCHEMA,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime(ISO),
        "method": ("stesso compute() della produzione con cascata+matching+"
                   "ensemble accesi; verita' = SRI osservato; Brier/reliability"
                   " sui pixel del dominio, CRPS fair su %d pixel sistematici "
                   "per caso/lead, climatologia leave-one-day-out"
                   % CRPS_SUBSAMPLE),
        "days": days,
        "anchors": anchors,
        "leads_min": list(leads),
        "thresholds_mmh": list(THRESHOLDS),
        "criterion_thresholds_mmh": list(CRITERION_THRESHOLDS),
        "reliability_bins": RELIABILITY_BINS,
        "reliability_tolerance": RELIABILITY_TOLERANCE,
        "min_bin_cases": MIN_BIN_CASES,
        "min_days_pass_fraction": MIN_DAYS_PASS_FRACTION,
        "crps_subsample": CRPS_SUBSAMPLE,
        "motion_inflation": float(args.motion_inflation),
        "noise_inflation": float(args.noise_inflation),
        "cases_total": len(cases),
        "cases_ok": sum(1 for c in cases if c.get("status") == "ok"),
        "downloads": int(cache.downloads),
        "seconds": round(seconds, 1),
        "compute_seconds_total": round(sum(
            float(c.get("compute_seconds") or 0.0) for c in cases), 3),
        "ensemble_seconds_total": round(sum(
            float(c.get("ensemble_seconds") or 0.0) for c in cases), 3),
        **agg,
        "cases": [{k: v for k, v in c.items()
                   if k not in ("leads",)} | {
                       "leads": {lk: {kk: vv for kk, vv in (lv or {}).items()
                                      if not kk.startswith("_")}
                                 for lk, lv in (c.get("leads") or {}).items()}}
                  for c in cases],
    }
    _atomic_write(args.out, json.dumps(result, ensure_ascii=False, indent=1,
                                       allow_nan=False, default=float))
    print("[OK] %s: %d casi ok, %.0f s" % (args.out, result["cases_ok"],
                                           seconds), flush=True)
    if args.report:
        _atomic_write(args.report, _markdown(result))
        print("[OK] %s" % args.report, flush=True)
    return 0


def _fmt(v, nd=3):
    return "n.d." if v is None else ("%.*f" % (nd, v))


def _markdown(result):
    lines = ["# Calibrazione del nowcast probabilistico", "",
             "Generato da `scripts/validation/nowcast_ensemble_backtest.py` "
             "il %s." % result["generated_at"], "",
             result["method"] + ".", "",
             "Campione: **%d giornate** x %d ancore = %d casi (%d ok), lead "
             "%s min." % (len(result["days"]), len(result["anchors"]),
                          result["cases_total"], result["cases_ok"],
                          "/".join(str(x) for x in result["leads_min"])), "",
             "## Brier e Brier skill score", "",
             "| soglia | lead | BS | BS clim | BSS | n pixel | freq. oss. |",
             "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for thr in result["thresholds_mmh"]:
        for lead in result["leads_min"]:
            b = result["brier"][str(thr)][str(lead)]
            lines.append("| %s | +%d | %s | %s | %s | %d | %s |" % (
                thr, lead, _fmt(b.get("bs")), _fmt(b.get("bs_clim")),
                _fmt(b.get("bss")), b.get("n") or 0,
                _fmt(b.get("observed_rate"))))
    lines += ["", "## Giornate con BSS > 0 (criterio: >= 60%)", "",
              "| soglia | giornate valutate | BSS>0 | frazione | passa |",
              "| ---: | ---: | ---: | ---: | :--- |"]
    for thr in result["criterion_thresholds_mmh"]:
        d = result["bss_days"][str(thr)]
        lines.append("| %s | %d | %d | %s | %s |" % (
            thr, d["days_evaluated"], d["days_bss_positive"],
            _fmt(d["fraction"]), "SI" if d["passes"] else "**NO**"))
    lines += ["", "## Reliability (10 bin)", ""]
    for thr in result["criterion_thresholds_mmh"]:
        r = result["reliability"][str(thr)]
        lines += ["### Soglia %s mm/h" % thr, "",
                  "| bin | n | media p | osservato | delta | entro 10% |",
                  "| ---: | ---: | ---: | ---: | ---: | :--- |"]
        for b in r["bins"]:
            if b["count"] < result["min_bin_cases"]:
                continue
            lines.append("| %d | %d | %s | %s | %s | %s |" % (
                b["bin"], b["count"], _fmt(b["mean_prob"]),
                _fmt(b["observed"]), _fmt(b["delta"]),
                "si" if b["within_tolerance"] else "**NO**"))
        lines += ["", "Delta massimo: **%s**." % _fmt(r["max_abs_delta"]), ""]
    c = result["crps"]
    lines += ["## CRPS", "",
              "| | CRPS |", "| :--- | ---: |",
              "| ensemble | %s |" % _fmt(c["ensemble_mean"]),
              "| climatologia (LODO) | %s |" % _fmt(c["climatology_mean"]),
              "| ensemble meglio? | %s |" % ("SI" if c["better_than_climatology"]
                                             else "**NO**"), ""]
    for lead in result["leads_min"]:
        d = c["by_lead"][str(lead)]
        lines.append("- +%d: ensemble %s vs clima %s" % (
            lead, _fmt(d["ensemble"]), _fmt(d["climatology"])))
    s = result["spread_skill"]
    lines += ["", "## Spread-skill", "",
              "RMSE %s, spread medio %s, **rapporto RMSE/spread %s** -> %s. "
              "Fattore di inflazione necessario: **%s**." % (
                  _fmt(s.get("rmse")), _fmt(s.get("spread_mean")),
                  _fmt(s.get("ratio_rmse_spread")), s.get("verdict"),
                  _fmt(s.get("inflation_needed"))), "",
              "| spread basso | spread alto | n | spread medio | RMSE | "
              "rapporto |", "| ---: | ---: | ---: | ---: | ---: | ---: |"]
    for b in s.get("bins") or []:
        lines.append("| %s | %s | %d | %s | %s | %s |" % (
            _fmt(b["spread_low"]), _fmt(b["spread_high"]), b["n"],
            _fmt(b["spread_mean"]), _fmt(b["rmse"]), _fmt(b["bias_ratio"])))
    lines += ["", "Spearman(spread, |errore|) = **%s**." % _fmt(
        s.get("spearman_spread_abserr")), "",
        "## Criteri dichiarati prima", "",
        "| criterio | esito |", "| :--- | :--- |"]
    for thr in result["criterion_thresholds_mmh"]:
        d = result["bss_days"][str(thr)]
        lines.append("| BSS>0 su >=60%% delle giornate (soglia %s) | %s |" % (
            thr, "PASSA" if d["passes"] else "**NON PASSA**"))
    for thr in result["criterion_thresholds_mmh"]:
        v = result["criteria"]["reliability_within_10pct"][str(thr)]
        lines.append("| Reliability +-10%% (soglia %s) | %s |" % (
            thr, "PASSA" if v else "**NON PASSA**"))
    lines.append("| CRPS meglio della climatologia | %s |" % (
        "PASSA" if c["better_than_climatology"] else "**NON PASSA**"))
    lines += ["", "**Verdetto: %s.**" % (
        "PASSA" if result["passes"] else "NON PASSA"), ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
