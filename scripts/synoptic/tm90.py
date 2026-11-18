#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Indice di blocco Tibaldi--Molteni (1990) — UNICO simbolo con soglia citata.

Soglia CITATA (non tarata qui, non adattata al dominio italiano):
    per Delta in {-4, 0, +4} gradi:
        phi_S = 40 + Delta, phi_0 = 60 + Delta, phi_N = 80 + Delta
        GHGS = (Z(phi_N) - Z(phi_0)) / (phi_N - phi_0)      [m / grado lat]
        GHGN = (Z(phi_0) - Z(phi_S)) / (phi_0 - phi_S)      [m / grado lat]
        blocco  <=>  GHGS > 0  E  GHGN < -10 m/grado

Riferimento bibliografico:
    Tibaldi, S., & Molteni, F. (1990). On the operational predictability of
    blocking. Tellus A, 42(3), 343--365. https://doi.org/10.3402/tellusa.v42i3.11882

REGOLA DI ONESTA': questa e' l'unica regola che produce un ``kind`` specifico
(``block``). Tutto il resto e' ``raw_field`` con soglia non calibrata.
Nessuna estensione minima in longitudine, nessuna persistenza temporale e
nessun'altra soglia viene aggiunta: sarebbero scelte locali non citate.
Il raggruppamento di longitudini contigue e' una RAPPRESENTAZIONE della geometria,
non una soglia (ogni longitudine che soddisfa la formula viene emessa).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .grid import lat_for_row, lon_for_col, row_for_lat

REFERENCE = (
    "Tibaldi, S. & Molteni, F. (1990), 'On the operational predictability of "
    "blocking', Tellus A 42(3):343-365, doi:10.3402/tellusa.v42i3.11882 — "
    "GHGS>0 and GHGN<-10 m/deg at 40/60/80N +/-4deg"
)
LAT_S, LAT_0, LAT_N = 40.0, 60.0, 80.0
DELTAS = (-4.0, 0.0, 4.0)
GHGS_MIN = 0.0
GHGN_MAX = -10.0
Z_VAR = "geopotential_height_500hPa"


@dataclass
class NotComputable(Exception):
    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


def _rows_for_delta(grid: dict, delta: float) -> tuple[int, int, int]:
    try:
        return (
            row_for_lat(LAT_S + delta, grid),
            row_for_lat(LAT_0 + delta, grid),
            row_for_lat(LAT_N + delta, grid),
        )
    except ValueError as exc:
        raise NotComputable("tm90_latitude_out_of_grid", str(exc)) from exc


def compute(z500: np.ndarray, grid: dict) -> list[dict]:
    """Calcola i segmenti di blocco. Solleva NotComputable se la griglia non basta."""
    z = np.asarray(z500, dtype=np.float64)
    if z.ndim != 2 or z.shape != (int(grid["ny"]), int(grid["nx"])):
        raise NotComputable("tm90_grid_mismatch", f"z500 {z.shape} vs grid {grid['ny']}x{grid['nx']}")

    per_lon = []  # (blocked, delta, ghgs, ghgn) per colonna
    for delta in DELTAS:
        rs, r0, rn = _rows_for_delta(grid, delta)
        if not np.all(np.isfinite(z[rs])) or not np.all(np.isfinite(z[r0])) or not np.all(np.isfinite(z[rn])):
            # Una riga interamente non valida: non e' un blocco, e' dato mancante.
            continue
        ghgs = (z[rn] - z[r0]) / (LAT_N - LAT_0)
        ghgn = (z[r0] - z[rs]) / (LAT_0 - LAT_S)
        hit = (ghgs > GHGS_MIN) & (ghgn < GHGN_MAX)
        per_lon.append((hit, delta, ghgs, ghgn))

    if not per_lon:
        raise NotComputable("tm90_thermal_levels_missing", "nessuna terna 40/60/80N+Delta con dati validi")

    nx = int(grid["nx"])
    chosen = []
    for j in range(nx):
        sel = None
        for hit, delta, ghgs, ghgn in per_lon:
            if bool(hit[j]):
                sel = (delta, float(ghgs[j]), float(ghgn[j]))
                break
        chosen.append(sel)

    features = []
    start = None
    for j in range(nx + 1):
        active = j < nx and chosen[j] is not None
        if active and start is None:
            start = j
        elif not active and start is not None:
            features.append(_feature(chosen[start:j], grid, start, j - 1))
            start = None
    return features


def _feature(points: list[tuple[float, float, float]], grid: dict, j0: int, j1: int) -> dict:
    mid = len(points) // 2
    delta, ghgs, ghgn = points[mid]
    geometry = [[round(LAT_0 + d, 4), round(lon_for_col(j, grid), 4)]
                for j, (d, _, _) in zip(range(j0, j1 + 1), points)]
    return {
        "id": f"block_{j0:04d}_{j1:04d}",
        "kind": "block",
        "geometry": geometry,
        "label": "blocco atmosferico — indice Tibaldi–Molteni 1990 (GHGS>0, GHGN<-10 m/grad)",
        "confidence": "citation_only",
        "reference": REFERENCE,
        "fields": {
            "variable": Z_VAR,
            "lat_S": LAT_S + delta,
            "lat_0": LAT_0 + delta,
            "lat_N": LAT_N + delta,
            "delta_deg": delta,
            "GHGS_m_per_deg": round(ghgs, 4),
            "GHGN_m_per_deg": round(ghgn, 4),
            "GHGS_threshold": "> 0 (Tibaldi–Molteni 1990)",
            "GHGN_threshold": "< -10 m/deg (Tibaldi–Molteni 1990)",
            "n_longitudes": len(points),
            "lon_start": round(lon_for_col(j0, grid), 4),
            "lon_end": round(lon_for_col(j1, grid), 4),
        },
        "null_reason": None,
    }
