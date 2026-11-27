#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Erosione del nucleo intenso dal bordo di uscita (V4, 2026-11-27).

Perche' esiste
--------------
Il nowcast trasporta il campo ma il nucleo >5 mm/h resta troppo esteso e
troppo pieno ai lead lunghi. Il decadimento globale (V2) e quello solo sopra
5 mm/h (V3) sono stati misurati e RESPINTI: a +60 l'area a 5 mm/h scende a
0,46 e a 10 mm/h a 0,41 dell'osservato. La contrazione direzionale del solo
nucleo >5 (V4) invece passa:

  area 5/10/20 a +30: 1,012 / 1,075 / 1,003
  area 5/10/20 a +60: 0,912 / 1,001 / 0,910
  area 0,5: 0,952 / 0,929 (invariata: l'erosione tocca solo >5)
  CSI5: +0,0004 a +30, +0,0005 a +60 (non peggiora)
  FSS5: -0,0005 a +30, -0,0027 a +60 (dentro 0,01)

Come funziona
-------------
Per ogni componente connessa del nucleo > `threshold_mmh`, si proietta la
posizione sul versore del moto (mediana u,v del nucleo) e si abbassa a
`threshold - 0.1` la banda con proiezione minima (il RETRO, lato opposto al
moto). I pixel erosi NON diventano asciutti: restano a 4,9 mm/h, quindi la
copertura a 0,5/2 mm/h e' invariata. Lo spessore cresce col lead:
`r = round(lead/30)` px (0 sotto +15, 1 a +15/+30, 2 a +45/+60, 3 a +75/+90).
E' una contrazione NUMERICA dichiarata, non una legge fisica.
"""

import numpy as np

SCHEMA = "meteorisk.nowcast_decay/1"
METHOD = "rear_erosion_strong_core"
ROLE = "core_contraction"
THRESHOLD_MMH = 5.0
MIN_REGION_PX = 20
PX_PER_30MIN = 1.0
REFERENCE = (
    "A/B su 8 ancore pesanti (2026-11-27), codice di produzione: area "
    "5/10/20 a +30 1,012/1,075/1,003 e a +60 0,912/1,001/0,910; area 0,5 "
    "invariata; CSI5 +0,0004/+0,0005; FSS5 -0,0005/-0,0027. V2 (globale) e "
    "V3 (solo >5 con fattore) respinti: area 5 a +60 0,46. L'erosione e' "
    "direzionale (retro rispetto al moto), non isotropa."
)


def _radius(lead_min, px_per_30min=PX_PER_30MIN):
    # +0.5 esplicito: round(0.5) in Python e' 0 (banker's rounding), qui
    # +15 deve valere 1 px come nella misura V4.
    return int(float(lead_min) / 30.0 * float(px_per_30min) + 0.5)


def apply(field, lead_min, u, v, domain, config=None):
    """Erode il retro del nucleo > soglia. Ritorna (campo, info)."""
    from scipy import ndimage as ndi
    cfg = dict(config or {})
    thr = float(cfg.get("threshold_mmh") or THRESHOLD_MMH)
    min_px = int(cfg.get("min_region_px") or MIN_REGION_PX)
    r = _radius(lead_min, cfg.get("px_per_30min") or PX_PER_30MIN)
    info = {
        "schema": SCHEMA, "method": METHOD, "role": ROLE,
        "threshold_mmh": round(thr, 3), "radius_px": r,
        "lead_min": int(lead_min), "applied": False,
        "pixels_changed": 0, "regions": 0,
        "reference": REFERENCE,
    }
    src = np.asarray(field, np.float32)
    if r <= 0:
        info["null_reason"] = "radius_zero"
        return src, info
    dom = np.asarray(domain, bool)
    mask = dom & np.isfinite(src) & (src > thr)
    if not bool(mask.any()):
        info["null_reason"] = "no_core_above_threshold"
        return src, info
    lab, n = ndi.label(mask)
    if n == 0:
        info["null_reason"] = "no_core_above_threshold"
        return src, info
    out = src.copy()
    yy, xx = np.mgrid[0:src.shape[0], 0:src.shape[1]]
    changed = 0
    regions = 0
    for i in range(1, n + 1):
        m = lab == i
        area = int(m.sum())
        if area < min_px:
            continue
        us = float(np.median(u[m])) if bool(m.any()) else 0.0
        vs = float(np.median(v[m])) if bool(m.any()) else 0.0
        norm = float(np.hypot(us, vs))
        if norm < 1e-6:
            us, vs, norm = 1.0, 0.0, 1.0
        s = (xx * (us / norm) + yy * (vs / norm))
        cut = float(s[m].min()) + float(r)
        band = m & (s <= cut)
        if not bool(band.any()):
            continue
        out[band] = np.minimum(out[band], np.float32(thr - 0.1))
        changed += int(band.sum())
        regions += 1
    if changed:
        info["applied"] = True
        info["pixels_changed"] = changed
        info["regions"] = regions
    else:
        info["null_reason"] = "no_region_eroded"
    return out.astype(np.float32), info


def summarize(lead_info, enabled=True, config=None):
    """Blocco `decay` del payload: pixel cambiati e motivo se non applicato."""
    cfg = dict(config or {})
    applied = any(bool(v.get("applied")) for v in (lead_info or {}).values())
    return {
        "schema": SCHEMA, "method": METHOD, "role": ROLE,
        "enabled": bool(enabled), "applied": bool(applied),
        "null_reason": (None if applied else
                        ("disabled_by_config" if not enabled
                         else "no_lead_applied")),
        "threshold_mmh": float(cfg.get("threshold_mmh") or THRESHOLD_MMH),
        "min_region_px": int(cfg.get("min_region_px") or MIN_REGION_PX),
        "px_per_30min": float(cfg.get("px_per_30min") or PX_PER_30MIN),
        "per_lead": {str(k): v for k, v in sorted((lead_info or {}).items())},
        "reference": REFERENCE,
    }


def disabled_info(reason="disabled_by_config", config=None):
    cfg = dict(config or {})
    return {
        "schema": SCHEMA, "method": METHOD, "role": ROLE,
        "enabled": False, "applied": False, "null_reason": reason,
        "threshold_mmh": float(cfg.get("threshold_mmh") or THRESHOLD_MMH),
        "min_region_px": int(cfg.get("min_region_px") or MIN_REGION_PX),
        "px_per_30min": float(cfg.get("px_per_30min") or PX_PER_30MIN),
        "reference": REFERENCE,
    }
