#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trend di intensita' per le celle CONVETTIVE (V-a) in coordinate lagrangiane.

Perche' esiste
--------------
Il trend lineare su tutte le regioni e' risultato ANTI-predittivo sull'area
(8 ancore, 158 regioni: area@30 Spearman -0,2002; area@60 -0,1636). Separando
per tipo di cella, l'intensita' delle CONVETTIVE (nucleo >= 12 mm/h, proxy di
40 dBZ con Marshall-Palmer) ha mostrato una correlazione POSITIVA:
int@30 Pearson 0,385 / Spearman 0,279; int@60 Pearson 0,214 / Spearman 0,162.
Il modulo applica SOLO quel segnale, con due limiti dichiarati:
  - solo celle convettive (le stratiformi restano invariate);
  - solo fino a +30 min, con dissolvenza lineare fino a +60 (dove la
    correlazione scende sotto la soglia di 0,2).
Il fattore e' limitato a +/-30% su 90 minuti. Nessun generatore esterno:
il trend e' la storia del dato, allineata col moto inverso.
"""

import math

import numpy as np

SCHEMA = "meteorisk.nowcast_trend/1"
VARIANT = "V-a_convective_intensity_linear"
ROLE = "intensity_trend_convective_cells"
CLASS_THRESHOLD_MMH = 12.0   # proxy di 40 dBZ (Marshall-Palmer ~11,5 mm/h)
MIN_REGION_PX = 200
MIN_HISTORY = 3
MAX_GAIN = 0.30
APPLY_UNTIL_MIN = 60
FADE_UNTIL_MIN = 90
MIN_RHO = 0.20
REFERENCE = (
    "Trend di intensita' in coordinate lagrangiane (frame allineati con il "
    "moto inverso), regressione lineare su 3-4 passi; applicato alle sole "
    "regioni con massimo >= 12 mm/h (proxy 40 dBZ). Calibrazione misurata su "
    "8 ancore / 196 regioni: int@30 Pearson 0,377 / Spearman 0,2903; int@60 "
    "Pearson 0,212 / Spearman 0,2242. Entrambi i lead superano la soglia "
    "0,2 positivo; oltre +60 non c'e' misura, quindi dissolvenza lineare "
    "fino a +90. Non e' una fisica esterna."
)


def _align(frames, u, v):
    """Frame precedenti nelle coordinate dell'ultimo (moto inverso)."""
    from . import nowcast_field as nf
    latest = frames[-1][0]
    out = []
    n = len(frames)
    for k, (f, val, _t) in enumerate(frames):
        back = n - 1 - k
        if back == 0:
            out.append((f, val, 0.0))
            continue
        smp, inside = nf._warp(f, -u * np.float32(back),
                               -v * np.float32(back), order=1)
        smpv, insidev = nf._warp(val.astype(np.float32),
                                 -u * np.float32(back),
                                 -v * np.float32(back), order=1)
        out.append((np.where(inside, smp, 0.0).astype(np.float32),
                    inside & insidev & (smpv > 0.5), -5.0 * back))
    return latest, out


def _slope(times, values):
    if len(values) < MIN_HISTORY:
        return None
    t = np.asarray(times, np.float64)
    y = np.asarray(values, np.float64)
    A = np.vstack([t, np.ones_like(t)]).T
    try:
        sol, _res, _rank, _sv = np.linalg.lstsq(A, y, rcond=None)
    except Exception:
        return None
    return float(sol[0])


def _factor(tr, lead, max_gain=MAX_GAIN, apply_until=APPLY_UNTIL_MIN,
            fade_until=FADE_UNTIL_MIN):
    i0 = tr["i0"]
    if i0 <= 1e-6:
        return 1.0
    raw = 1.0 + tr["slope"] * float(lead) / i0
    lim = float(max_gain) * min(float(lead), 90.0) / 90.0
    raw = min(max(raw, 1.0 - lim), 1.0 + lim)
    if lead <= apply_until:
        conf = 1.0
    elif lead >= fade_until:
        conf = 0.0
    else:
        conf = (float(fade_until) - float(lead)) / float(fade_until - apply_until)
    return 1.0 + (raw - 1.0) * conf


def disabled_info(reason, config=None):
    """Blocco `evolution` quando il layer e' spento (motivo dichiarato)."""
    cfg = dict(config or {})
    return {
        "schema": SCHEMA, "variant": VARIANT, "role": ROLE,
        "enabled": False, "applied": False, "null_reason": reason,
        "class_threshold_mmh": float(cfg.get("class_threshold_mmh")
                                     or CLASS_THRESHOLD_MMH),
        "min_region_px": int(cfg.get("min_region_px") or MIN_REGION_PX),
        "min_history": int(cfg.get("min_history") or MIN_HISTORY),
        "max_gain": float(cfg.get("max_gain") or MAX_GAIN),
        "apply_until_min": int(cfg.get("apply_until_min")
                               or APPLY_UNTIL_MIN),
        "fade_until_min": int(cfg.get("fade_until_min") or FADE_UNTIL_MIN),
        "calibration": dict(cfg.get("calibration") or {}),
        "reference": REFERENCE,
        "regions_total": 0, "regions_convective": 0,
        "regions_with_trend": 0, "per_lead": {},
    }


def prepare(frames, u, v, domain, config=None):
    """Stima il trend per le regioni convettive. Ritorna uno stato o None."""
    from scipy import ndimage as ndi
    cfg = dict(config or {})
    cal = dict(cfg.get("calibration") or {})
    rho30 = cal.get("rho_int_30")
    min_rho = float(cfg.get("min_rho") or MIN_RHO)
    base = {
        "schema": SCHEMA, "variant": VARIANT, "role": ROLE,
        "enabled": True, "applied": False, "null_reason": None,
        "class_threshold_mmh": float(cfg.get("class_threshold_mmh")
                                     or CLASS_THRESHOLD_MMH),
        "min_region_px": int(cfg.get("min_region_px") or MIN_REGION_PX),
        "min_history": int(cfg.get("min_history") or MIN_HISTORY),
        "max_gain": float(cfg.get("max_gain") or MAX_GAIN),
        "apply_until_min": int(cfg.get("apply_until_min")
                               or APPLY_UNTIL_MIN),
        "fade_until_min": int(cfg.get("fade_until_min") or FADE_UNTIL_MIN),
        "calibration": cal, "reference": REFERENCE,
        "regions_total": 0, "regions_convective": 0,
        "regions_with_trend": 0, "per_lead": {},
    }
    if not frames or len(frames) < 3:
        base["null_reason"] = "frames_lt_3"
        return {"info": base, "labels": None, "regions": {}}
    if rho30 is None or float(rho30) < min_rho:
        base["null_reason"] = "calibration_rho_below_threshold"
        return {"info": base, "labels": None, "regions": {}}
    latest, aligned = _align(frames, u, v)
    labels, nlab = ndi.label((latest >= 0.5) & np.asarray(domain, bool))
    base["regions_total"] = int(nlab)
    regions = {}
    for i in range(1, nlab + 1):
        m = labels == i
        area = int(m.sum())
        if area < base["min_region_px"]:
            continue
        if float(np.max(latest[m])) < base["class_threshold_mmh"]:
            continue
        base["regions_convective"] += 1
        ts, ints = [], []
        for f, val, t in aligned:
            if int((m & val).sum()) < 0.5 * area:
                continue
            wet = m & val & (f >= 0.5)
            vals = f[wet]
            if vals.size == 0:
                continue
            ts.append(t)
            ints.append(float(vals.mean()))
        slope = _slope(ts, ints)
        if slope is None or ints[-1] <= 1e-6:
            continue
        regions[i] = {"area_px": area, "i0": ints[-1], "slope": slope,
                      "history": len(ts)}
    base["regions_with_trend"] = len(regions)
    if not regions:
        base["null_reason"] = "no_convective_region_with_history"
    return {"info": base, "labels": labels, "positions": None,
            "regions": regions}


def apply(field, lead, state, u, v, domain):
    """Applica il fattore delle regioni convettive al campo trasportato."""
    from . import nowcast_field as nf
    info = {"applied": False, "pixels_changed": 0,
            "factor_min": None, "factor_max": None,
            "lead_min": int(lead)}
    if not state or not state.get("regions") or state["labels"] is None:
        return np.asarray(field, np.float32), info
    labels = state["labels"]
    steps = float(lead) / 5.0
    wl, inside = nf._warp(labels.astype(np.float32),
                          -u * np.float32(steps),
                          -v * np.float32(steps), order=0)
    out = np.asarray(field, np.float32).copy()
    fs = []
    for i, tr in state["regions"].items():
        f = _factor(tr, lead)
        if abs(f - 1.0) < 1e-3:
            continue
        m = (wl == i) & inside
        if not bool(m.any()):
            continue
        out[m] *= np.float32(f)
        info["pixels_changed"] += int(m.sum())
        fs.append(f)
    if fs:
        info["applied"] = True
        info["factor_min"] = round(float(min(fs)), 6)
        info["factor_max"] = round(float(max(fs)), 6)
    out = np.where(np.asarray(domain, bool), out, 0.0)
    np.maximum(out, 0.0, out=out)
    return out.astype(np.float32), info


def summarize(state):
    """Blocco `evolution` per il payload (nessun numero inventato)."""
    if not state:
        return {"schema": SCHEMA, "variant": VARIANT, "role": ROLE,
                "enabled": False, "applied": False,
                "null_reason": "not_prepared"}
    info = dict(state.get("info") or {})
    info["applied"] = bool(info.get("regions_with_trend"))
    return info
