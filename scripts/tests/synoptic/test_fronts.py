#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del motore fronti — caso analitico, determinismo, schema, simbologia."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from synoptic import fronts  # noqa: E402


GRID = {"nx": 61, "ny": 81, "lonMin": 10.0, "latMin": 40.0, "dx": 0.2, "dy": 0.2}


def analytic_front_arrays(front_lat: float = 48.0) -> dict:
    """Salto termico di 10 K su ~100 km (tanh, scala 30 km), RH uniforme."""
    lat = GRID["latMin"] + np.arange(GRID["ny"]) * GRID["dy"]
    lon = GRID["lonMin"] + np.arange(GRID["nx"]) * GRID["dx"]
    L, O = np.meshgrid(lat, lon, indexing="ij")
    T = 10.0 - 10.0 * np.tanh((L - front_lat) * 111320.0 / 30000.0)
    RH = np.full_like(T, 80.0)
    return {"temperature_850hPa": T.astype(np.float32),
            "relative_humidity_850hPa": RH.astype(np.float32),
            "geopotential_height_1000hPa": np.zeros_like(T),
            "wind_speed_850hPa": np.full_like(T, 10.0),
            "wind_direction_850hPa": np.full_like(T, 270.0)}


def permissive(**kw) -> fronts.DetectionConfig:
    base = dict(S_km=30.0, thresh_t=-1e-13, thresh_g=1e-8,
                min_length_km=0.0, searchrad_km=50.0, status="calibrated")
    base.update(kw)
    return fronts.DetectionConfig(**base)


def gen() -> datetime:
    return datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
def test_bolton_thetae_finite_and_monotonic():
    T = np.array([-20.0, 0.0, 20.0, 35.0])
    RH = np.array([50.0, 80.0, 80.0, 95.0])
    te = fronts.bolton_thetae(T, RH)
    assert np.all(np.isfinite(te))
    assert np.all(np.diff(te) > 0), te
    # theta_e non e' indipendente da T: a RH uguale cresce con T
    te2 = fronts.bolton_thetae(np.array([10.0, 10.0]), np.array([50.0, 95.0]))
    assert te2[1] > te2[0]
    # valori non fisici -> NaN, mai 0
    bad = fronts.bolton_thetae(np.array([np.nan]), np.array([80.0]))
    assert np.isnan(bad[0])


def test_analytic_front_within_10km_of_max_gradient():
    arrays = analytic_front_arrays(48.0)
    obj = fronts.analyze_frame(arrays, GRID, permissive(), gen(), "2026-11-18T12:00Z")
    fronts_out = [f for f in obj["features"] if f["properties"]["kind"] == "front"]
    assert fronts_out, "nessun fronte rilevato sul caso analitico"
    pts = [p for f in fronts_out for p in f["geometry"]["coordinates"]]
    lats = np.array([p[1] for p in pts])
    # massimo gradiente della T analitica e' a lat=48
    err_km = np.abs(lats - 48.0) * 111.32
    assert float(err_km.min()) <= 10.0, f"distanza minima dal massimo gradiente: {err_km.min()} km"
    # nessuna posizione inventata: tutti i punti dentro la griglia
    assert np.all((lats >= GRID["latMin"]) & (lats <= GRID["latMin"] + 80 * GRID["dy"]))
    lons = np.array([p[0] for p in pts])
    assert np.all((lons >= GRID["lonMin"]) & (lons <= GRID["lonMin"] + 60 * GRID["dx"]))


def test_rfc7946_coordinate_order_lon_lat():
    arrays = analytic_front_arrays(48.0)
    obj = fronts.analyze_frame(arrays, GRID, permissive(), gen())
    assert obj["coordinate_order"] == "RFC7946 [lon, lat]"
    for f in obj["features"]:
        for p in f["geometry"]["coordinates"]:
            assert len(p) == 2
            lon, lat = p
            assert 10.0 <= lon <= 22.0, f"prima coordinata non e' longitudine: {p}"
            assert 40.0 <= lat <= 56.0, f"seconda coordinata non e' latitudine: {p}"


def test_determinism_byte_identical():
    arrays = analytic_front_arrays()
    a = fronts.analyze_frame(arrays, GRID, permissive(), gen(), "2026-11-18T12:00Z")
    b = fronts.analyze_frame(arrays, GRID, permissive(), gen(), "2026-11-18T12:00Z")
    sa = json.dumps(a, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    sb = json.dumps(b, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    assert sa == sb
    assert len(sa) == len(sb)


def test_missing_temperature_or_humidity_gives_null_reason():
    arrays = analytic_front_arrays()
    for drop in ("temperature_850hPa", "relative_humidity_850hPa"):
        a = dict(arrays)
        del a[drop]
        obj = fronts.analyze_frame(a, GRID, permissive(), gen())
        diag = [f for f in obj["features"] if f["properties"]["kind"] == "front_diagnostics"]
        assert diag, f"nessun null_reason per {drop}"
        assert drop in diag[0]["properties"]["null_reason"]
        assert diag[0]["properties"]["speed_ms"] is None


def test_orography_proxy_mask():
    Z = np.zeros((GRID["ny"], GRID["nx"]), dtype=np.float32)
    Z[30:35, 20:25] = -300.0          # "montagna": 1000 hPa sotto il livello del mare
    mask = fronts.orography_mask_proxy(Z)
    assert bool(mask[32, 22]) is True
    assert bool(mask[0, 0]) is False
    arrays = analytic_front_arrays()
    arrays["geopotential_height_1000hPa"] = Z
    fld = fronts.compute_tau_fields(arrays["temperature_850hPa"],
                                    arrays["relative_humidity_850hPa"], GRID, 30.0, mask)
    assert np.isnan(fld.tau[32, 22])
    assert np.isfinite(fld.tau[40, 22])


def test_uncalibrated_has_null_reason_and_no_fronts():
    arrays = analytic_front_arrays()
    obj = fronts.analyze_frame(arrays, GRID, fronts.DetectionConfig(), gen())
    assert obj["status"] == "uncalibrated"
    assert obj["null_reason"] and "thresholds_not_calibrated" in obj["null_reason"]
    assert not [f for f in obj["features"] if f["properties"]["kind"] == "front"]


def test_symbology_is_fixed_not_recalibrated():
    S = fronts.SYMBOLOGY
    assert S["cold"] == {"line": "solid", "color": "blue", "symbol": "triangles",
                         "side": "toward_motion", "placement": "every_segment"}
    assert S["warm"]["color"] == "red" and S["warm"]["symbol"] == "semicircles"
    assert S["stationary"]["symbol"] == "triangles_semicircles_opposite"
    assert S["occluded"]["color"] == "purple" and S["occluded"]["symbol"] == "triangles_semicircles_same"
    assert S["frontogenesis"]["line"] == "dashed" and S["frontogenesis"]["placement"] == "every_segment"
    assert S["frontolysis"]["line"] == "dashed" and S["frontolysis"]["placement"] == "every_second_segment"
    # velocita' non calcolabile -> tratteggiata SENZA simboli (nessun tipo inventato)
    assert S[None] == {"line": "dashed", "color": "neutral", "symbol": "none",
                       "side": None, "placement": "none"}


def test_classification_convention():
    c = fronts.classify
    assert c(-3.0, None, 1.5)[0] == "cold"
    assert c(3.0, None, 1.5)[0] == "warm"
    assert c(0.2, None, 1.5)[0] == "stationary"
    assert c(0.2, None, 1.5, merge_near_min=True)[0] == "occluded"
    assert c(None, 1e-6, 1.5)[0] == "frontogenesis"
    assert c(None, -1e-6, 1.5)[0] == "frontolysis"
    assert c(None, None, 1.5)[0] is None


def test_speed_not_computable_uses_dashed_without_symbols():
    arrays = analytic_front_arrays()
    del arrays["wind_speed_850hPa"]
    del arrays["wind_direction_850hPa"]
    obj = fronts.analyze_frame(arrays, GRID, permissive(), gen())
    fr = [f for f in obj["features"] if f["properties"]["kind"] == "front"]
    assert fr
    p = fr[0]["properties"]
    assert p["speed_ms"] is None
    assert p["symbology"]["line"] == "dashed"
    assert p["symbology"]["symbol"] == "none"
    assert p["null_reason"] and p["null_reason"].startswith("front_speed_")


def test_calibration_thresholds_not_copied_from_erai():
    p = ROOT.parent / "calibration.json"
    if not p.exists():
        pytest.skip("calibration.json non ancora generata in questa installazione")
    cal = json.loads(p.read_text(encoding="utf-8"))
    g = cal["global"]
    assert g["thresh_t"] < 0 and abs(g["thresh_t"]) < 1e-7
    assert g["thresh_g"] > 0 and g["thresh_g"] < 1e-3
    assert cal["status"] == "calibrated"
