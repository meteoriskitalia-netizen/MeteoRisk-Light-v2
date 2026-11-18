#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crea una fixture REALE RIDOTTA da un frame .om pubblicato su fm2-ncep.

Non inventa valori: croppa e decima il frame reale, conservando i valori
dequantizzati e gli scale_factor originali. Serve solo ai test.
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
from omfiles import OmFileWriter

from .grid import array_names, read_array, read_scalar
from .om_http import open_frame

LAT0, LAT1, LON0, LON1 = 35.0, 85.0, -20.0, 60.0
STEP = 2  # 0.25 deg -> 0.5 deg


def make(src: str, src_manifest: str, dst_om: str, dst_manifest: str,
         lat_range=(LAT0, LAT1), lon_range=(LON0, LON1)) -> dict:
    global LAT0, LAT1, LON0, LON1
    LAT0, LAT1 = lat_range
    LON0, LON1 = lon_range
    r, _ = open_frame(src)
    with open(src_manifest, "r", encoding="utf-8") as fh:
        g = json.load(fh)
    grid = g["grid"]
    r0 = int(round((LAT0 - grid["latMin"]) / grid["dy"]))
    r1 = int(round((LAT1 - grid["latMin"]) / grid["dy"])) + 1
    c0 = int(round((LON0 - grid["lonMin"]) / grid["dx"]))
    c1 = int(round((LON1 - grid["lonMin"]) / grid["dx"])) + 1
    rows = slice(r0, r1, STEP)
    cols = slice(c0, c1, STEP)

    w = OmFileWriter.at_path(dst_om)
    children = []
    kept = []
    for name in array_names(r):
        child = r.get_child_by_name(name)
        a = read_array(r, name)[rows, cols]
        c = w.write_array(
            np.ascontiguousarray(a), chunks=[min(32, a.shape[0]), min(64, a.shape[1])],
            scale_factor=float(child.scale_factor), add_offset=float(child.add_offset),
            name=name)
        children.append(c)
        kept.append(name)
    for sname in ("crs_wkt", "coordinates", "valid_time", "forecast_reference_time"):
        children.append(w.write_scalar(read_scalar(r, sname), name=sname))
    root = w.write_group(name="", children=children)
    w.close(root)

    ny = len(range(r0, r1, STEP))
    nx = len(range(c0, c1, STEP))
    new_grid = {"nx": nx, "ny": ny, "lonMin": LON0, "latMin": LAT0,
                "dx": grid["dx"] * STEP, "dy": grid["dy"] * STEP}
    vt = read_scalar(r, "valid_time")
    ref = read_scalar(r, "forecast_reference_time")
    from datetime import datetime, timezone
    fmt = lambda t: datetime.fromtimestamp(float(t), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    man = {
        "version": "fixture-real-reduced-1",
        "domain": g["domain"], "reference_time": fmt(ref),
        "generated_at": fmt(ref),
        "grid": new_grid,
        "bbox": {"lat_min": LAT0, "lon_min": LON0, "lat_max": LAT1, "lon_max": LON1},
        "var_order": kept,
        "frames": [{"valid_time": fmt(vt), "file": os.path.basename(dst_om),
                    "bytes": os.path.getsize(dst_om), "variables": len(kept),
                    "source_mode": "real-crop-decimate"}],
    }
    with open(dst_manifest, "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return man


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--src-manifest", required=True)
    ap.add_argument("--om", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--lat-range", nargs=2, type=float, default=(LAT0, LAT1))
    ap.add_argument("--lon-range", nargs=2, type=float, default=(LON0, LON1))
    a = ap.parse_args()
    m = make(a.src, a.src_manifest, a.om, a.manifest,
             tuple(a.lat_range), tuple(a.lon_range))
    print(json.dumps({"grid": m["grid"], "variables": len(m["var_order"]),
                      "bytes": m["frames"][0]["bytes"]}))
