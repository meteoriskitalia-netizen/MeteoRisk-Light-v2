#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Costruzione volume 3D (z,y,x) dai CAPPI del motore radar.

Generalizza la logica di build_volume.py per lavorare con i RasterData del motore
(senza scaricare da rete). Degradazione pulita se < 4 livelli validi.
"""
import numpy as np


def build_volume_from_cappi(cappi, ref_rd):
    """Costruisce un volume (10, H, W) float32 dai CAPPI del motore.

    cappi: dict {quota_km: RasterData | None}
    ref_rd: RasterData di riferimento (VMI) per griglia/CRS.
    Ritorna np.ndarray o None se < 4 livelli validi.
    """
    if ref_rd is None:
        return None
    H = int(ref_rd.rows)
    W = int(ref_rd.cols)
    vol = np.full((10, H, W), np.nan, dtype=np.float32)
    valid_levels = 0
    for level in range(1, 11):
        rd = None
        if isinstance(cappi, dict):
            rd = cappi.get(level)
            if rd is None:
                rd = cappi.get(float(level))
        if rd is None:
            continue
        try:
            arr = np.asarray(rd.data, dtype=np.float32)
        except Exception:
            continue
        if arr.shape != (H, W):
            continue
        arr = np.where((arr == -9999.0) | (arr == -9998.0), np.nan, arr).astype(np.float32)
        vol[level - 1] = arr
        valid_levels += 1
    if valid_levels < 4:
        return None
    # interpolazione verticale (VI scheme, Zhang 2005) su buchi singoli adiacenti
    elevations = np.array([float(i) for i in range(1, 11)], dtype=np.float64)
    vol_before = vol.copy()
    interp_mask = np.zeros(vol.shape, dtype=bool)
    for k in range(1, 9):
        missing = np.isnan(vol_before[k])
        above_ok = ~np.isnan(vol_before[k - 1])
        below_ok = ~np.isnan(vol_before[k + 1])
        cand = missing & above_ok & below_ok
        if not np.any(cand):
            continue
        z0 = elevations[k - 1]; z1 = elevations[k]; z2 = elevations[k + 1]
        vlow = vol_before[k - 1]; vhigh = vol_before[k + 1]
        interp_vals = vlow + (vhigh - vlow) * ((z1 - z0) / (z2 - z0))
        vol[k] = np.where(cand, interp_vals, vol[k]).astype(np.float32)
        interp_mask[k] = cand
    return vol.astype(np.float32)
