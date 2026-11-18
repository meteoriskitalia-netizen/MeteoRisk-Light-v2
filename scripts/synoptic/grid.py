#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lettura array/scalari da un frame .om e mappa indice <-> lat/lon.

Convenzione verificata sui frame reali fm2-* (2026-11-18):
  * array 2D (ny, nx) float32, riga 0 = latMin (sud), colonna 0 = lonMin (ovest);
  * i valori restituiti da ``read_array`` sono GIA' dequantizzati (unità reali);
  * gli scalari ``valid_time`` / ``forecast_reference_time`` sono epoch secondi.
"""

from __future__ import annotations

import numpy as np


def read_array(reader, name: str) -> np.ndarray:
    child = reader.get_child_by_name(name)
    if child is None or child.is_scalar:
        raise KeyError(name)
    arr = child.read_array((slice(None), slice(None)))
    return np.ascontiguousarray(np.asarray(arr, dtype=np.float32))


def read_scalar(reader, name: str):
    child = reader.get_child_by_name(name)
    if child is None or not child.is_scalar:
        raise KeyError(name)
    return child.read_scalar()


def array_names(reader) -> list[str]:
    out = []
    for i in range(int(reader.num_children)):
        child = reader.get_child_by_index(i)
        if not child.is_scalar:
            out.append(child.name)
    return out


def index_for_coord(value: float, origin: float, step: float, size: int, what: str) -> int:
    """Indice esatto di una coordinata su una griglia regolare.

    Solleva ValueError se la coordinata non e' un nodo della griglia o e' fuori:
    non facciamo interpolazione silenziosa (sarebbe un dato inventato).
    """
    raw = (float(value) - float(origin)) / float(step)
    idx = int(round(raw))
    if abs(raw - idx) > 1e-6 or idx < 0 or idx >= int(size):
        raise ValueError(
            f"{what}={value} non e' un nodo della griglia "
            f"(origin={origin}, step={step}, size={size})"
        )
    return idx


def row_for_lat(lat: float, grid: dict) -> int:
    return index_for_coord(lat, grid["latMin"], grid["dy"], grid["ny"], "lat")


def col_for_lon(lon: float, grid: dict) -> int:
    return index_for_coord(lon, grid["lonMin"], grid["dx"], grid["nx"], "lon")


def lat_for_row(row: int, grid: dict) -> float:
    return float(grid["latMin"]) + int(row) * float(grid["dy"])


def lon_for_col(col: int, grid: dict) -> float:
    return float(grid["lonMin"]) + int(col) * float(grid["dx"])
