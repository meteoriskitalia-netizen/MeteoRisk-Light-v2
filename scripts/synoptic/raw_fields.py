#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Campi grezzi NON calibrati — mai simboli meteorologici specifici.

Per ciascun campo reale del frame .om si emette l'ASSE dei valori estremi
(per longitudine, la latitudine di massimo/minimo del campo). Questo e' un
descrittore del campo grezzo, non un fronte/una goccia fredda/una convergenza:
non esiste una soglia calibrata che trasformi l'asse in un simbolo.

Ogni feature ha quindi:
    kind = "raw_field"
    confidence = "uncalibrated"
    null_reason = "threshold_not_calibrated"
    label = descrizione letterale del campo grezzo

Il limite a MAX_POINTS punti e' una decimazione di RAPPRESENTAZIONE (la polilinea
resta leggibile); non e' una soglia meteorologica e non cambia i valori.
"""

from __future__ import annotations

import math

import numpy as np

from .grid import lat_for_row, lon_for_col

MAX_POINTS = 120
UNCALIBRATED = "uncalibrated"
NO_THRESHOLD = "threshold_not_calibrated"

FIELDS = (
    ("raw_t850_gradient", "temperature_850hPa",
     "campo grezzo: asse del massimo gradiente termico a 850 hPa (soglia non calibrata)"),
    ("raw_t500_cold", "temperature_500hPa",
     "campo grezzo: asse del minimo termico a 500 hPa (soglia non calibrata)"),
    ("raw_div850", "wind_speed_850hPa",
     "campo grezzo: asse della massima convergenza del vento a 850 hPa (soglia non calibrata)"),
    ("raw_rh850", "relative_humidity_850hPa",
     "campo grezzo: asse della massima umidita' relativa a 850 hPa (soglia non calibrata)"),
)


def _decimate(geometry: list) -> list:
    n = len(geometry)
    if n <= MAX_POINTS:
        return geometry
    step = math.ceil(n / MAX_POINTS)
    return geometry[::step][:MAX_POINTS]


def _locus(arr: np.ndarray, grid: dict, reducer, var: str, fid: str,
           label: str, flds: dict) -> dict | None:
    a = np.asarray(arr, dtype=np.float64)
    ny, nx = a.shape
    points = []
    for j in range(nx):
        col = a[:, j]
        if not np.any(np.isfinite(col)):
            continue
        i = int(reducer(col))
        if not np.isfinite(col[i]):
            continue
        points.append([round(lat_for_row(i, grid), 4), round(lon_for_col(j, grid), 4)])
    if not points:
        return None
    return {
        "id": fid,
        "kind": "raw_field",
        "geometry": _decimate(points),
        "label": label,
        "confidence": UNCALIBRATED,
        "reference": None,
        "fields": {
            "variable": var,
            "descriptor": "per-longitude extremum of the real field",
            "n_source_longitudes": nx,
            "n_geometry_points": len(_decimate(points)),
            **flds,
        },
        "null_reason": NO_THRESHOLD,
    }


def compute(arrays: dict, grid: dict) -> tuple[list[dict], list[str]]:
    """Ritorna (features, campi_mancanti). Nessuna eccezione per campo assente."""
    features, missing = [], []

    def need(name):
        if name not in arrays:
            missing.append(name)
            return None
        return np.asarray(arrays[name], dtype=np.float64)

    dx, dy = float(grid["dx"]), float(grid["dy"])

    t850 = need("temperature_850hPa")
    if t850 is not None:
        try:
            gy, gx = np.gradient(t850, dy, dx)
            mag = np.hypot(gy, gx)
            features.append(_locus(
                mag, grid, np.nanargmax, "temperature_850hPa",
                "raw_t850_gradient", FIELDS[0][2],
                {"quantity": "|grad(T850)|", "units": "degC/deg_lat",
                 "max_value": round(float(np.nanmax(mag)), 4)}))
        except Exception:
            missing.append("temperature_850hPa")

    t500 = need("temperature_500hPa")
    if t500 is not None:
        try:
            features.append(_locus(
                t500, grid, np.nanargmin, "temperature_500hPa",
                "raw_t500_cold", FIELDS[1][2],
                {"quantity": "T500", "units": "degC",
                 "min_value": round(float(np.nanmin(t500)), 4)}))
        except Exception:
            missing.append("temperature_500hPa")

    spd = need("wind_speed_850hPa")
    drs = need("wind_direction_850hPa")
    if spd is not None and drs is not None:
        try:
            rad = np.deg2rad(drs)
            # direzione meteorologica: da dove il vento SOFFIA.
            u = -spd * np.sin(rad)
            v = -spd * np.cos(rad)
            _, dudx = np.gradient(u, dy, dx)
            dvdy, _ = np.gradient(v, dy, dx)
            div = dudx + dvdy
            features.append(_locus(
                div, grid, np.nanargmin, "wind_speed_850hPa,wind_direction_850hPa",
                "raw_div850", FIELDS[2][2],
                {"quantity": "du/dx + dv/dy at 850 hPa", "units": "1/s",
                 "min_value": round(float(np.nanmin(div)), 10)}))
        except Exception:
            missing.extend(["wind_speed_850hPa", "wind_direction_850hPa"])

    rh = need("relative_humidity_850hPa")
    if rh is not None:
        try:
            features.append(_locus(
                rh, grid, np.nanargmax, "relative_humidity_850hPa",
                "raw_rh850", FIELDS[3][2],
                {"quantity": "RH850", "units": "%",
                 "max_value": round(float(np.nanmax(rh)), 4)}))
        except Exception:
            missing.append("relative_humidity_850hPa")

    return [f for f in features if f is not None], sorted(set(missing))
