#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del modulo nwp_motion (triangolazione moto osservato + NWP).

Tutti i test sono ERMETICI: nessun file da 9.8 MB, nessuna rete. Il point
dataset sintetico ha 4 punti e 4 ore; i valori di vento sono scelti per
verificare a mano la media pesata per massa.
"""

import json
import math
import os
import sys

import numpy as np
import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from radar_engine import nwp_motion as nm      # noqa: E402
from radar_engine import forecast             # noqa: E402


# ---------------------------------------------------------------------------
# Dataset sintetico
# ---------------------------------------------------------------------------

def _dataset(levels=(850, 700, 600, 500), n_hours=24, u_by_level=None,
             points=None):
    """PointDataset con vento costante per livello (u, v=0 -> direzione 90)."""
    pts = points or [(12.0, 42.0), (12.5, 42.0), (12.0, 42.5), (12.5, 42.5)]
    n = len(pts)
    u_by_level = u_by_level or {850: 10.0, 700: 20.0, 600: 30.0, 500: 40.0}
    wind = {}
    for lvl in levels:
        u = np.full((n, n_hours), u_by_level.get(lvl, np.nan))
        v = np.zeros((n, n_hours))
        wind[lvl] = (u, v)
    geo = {lvl: np.full((n, n_hours), 1500.0 + (1000 - lvl) * 10.0)
           for lvl in (850, 700, 500)}
    return nm.PointDataset(
        path="synthetic", generated_at="2026-11-11T10:00:00+00:00",
        time_base_utc=nm._parse_iso_utc("2026-11-10T23:00:00Z"),
        lats=[p[1] for p in pts], lons=[p[0] for p in pts],
        wind=wind, geo=geo, model="best_match", n_hours=n_hours)


def _ms(iso):
    return int(nm._parse_iso_utc(iso) * 1000.0)


# ---------------------------------------------------------------------------
# Pesi e blend
# ---------------------------------------------------------------------------

def test_weight_rules_dichiarate():
    assert nm.weight_for_fit(True, 1.0) == (0.8, "fit_used_resid_lt_2km")
    assert nm.weight_for_fit(True, 3.0) == (0.5, "fit_used_resid_2_6km")
    assert nm.weight_for_fit(True, 9.0) == (0.4, "fit_used_resid_ge_6km")
    assert nm.weight_for_fit(False, 1.0) == (0.3, "fit_not_used")


def test_blend_modulo_e_direzione():
    obs = {"speed_kmh": 10.0, "direction_toward_deg": 90.0}
    nwp = {"speed_kmh": 20.0, "direction_toward_deg": 90.0}
    blend, weights, comps = nm.blend_motion(obs, nwp, True, 1.0)
    assert blend["speed_kmh"] == pytest.approx(12.0)   # 0.8*10 + 0.2*20
    assert blend["direction_toward_deg"] == pytest.approx(90.0)
    assert weights["obs"] == 0.8 and weights["nwp"] == 0.2
    assert comps["obs_speed_kmh"] == 10.0 and comps["nwp_speed_kmh"] == 20.0


def test_blend_vettoriale_non_media_scalare():
    # nord (0 deg) e est (90 deg) a pari peso -> 45 deg, non 45 in modulo
    obs = {"speed_kmh": 10.0, "direction_toward_deg": 0.0}
    nwp = {"speed_kmh": 10.0, "direction_toward_deg": 90.0}
    blend, _w, _c = nm.blend_motion(obs, nwp, True, 3.0)   # w = 0.5
    assert blend["direction_toward_deg"] == pytest.approx(45.0, abs=0.2)
    assert blend["speed_kmh"] == pytest.approx(10.0 * math.sqrt(2.0) / 2.0,
                                               rel=0.01)


def test_divergence():
    obs = {"speed_kmh": 10.0, "direction_toward_deg": 350.0}
    nwp = {"speed_kmh": 16.0, "direction_toward_deg": 10.0}
    dspeed, ddir = nm.divergence(obs, nwp)
    assert dspeed == pytest.approx(6.0)
    assert ddir == pytest.approx(20.0)      # wrap 350 -> 10


# ---------------------------------------------------------------------------
# Steering
# ---------------------------------------------------------------------------

def test_steering_media_pesata_per_massa():
    ds = _dataset()
    out = nm.steering_for_position(ds, 12.25, 42.25,
                                   _ms("2026-11-11T12:00:00Z"))
    assert out["null_reason"] is None
    expected = (150 * 10 + 125 * 20 + 100 * 30 + 50 * 40) / 425.0
    assert out["value"]["speed_kmh"] == pytest.approx(expected, abs=0.01)
    assert out["value"]["direction_toward_deg"] == pytest.approx(90.0, abs=0.1)
    assert out["levels_used_hpa"] == [850, 700, 600, 500]
    assert out["layer_top_m"] is not None
    assert "proxy" in out["reference"]


def test_steering_livelli_mancanti_dichiarati():
    ds = _dataset(levels=(850, 700))
    out = nm.steering_for_position(ds, 12.25, 42.25,
                                   _ms("2026-11-11T12:00:00Z"))
    assert out["value"] is not None
    assert out["levels_used_hpa"] == [850, 700]
    assert 600 in out["levels_missing_hpa"]
    assert 500 in out["levels_missing_hpa"]


def test_steering_null_oltre_80_km():
    ds = _dataset(points=[(12.0, 42.0)])
    out = nm.steering_for_position(ds, 12.0, 45.0,
                                   _ms("2026-11-11T12:00:00Z"))
    assert out["value"] is None
    assert out["null_reason"].startswith("no_point_within_")
    assert out["nearest_km"] > 80.0


def test_steering_dataset_unavailable():
    out = nm.steering_for_position(None, 12.0, 42.0, _ms("2026-11-11T12:00:00Z"))
    assert out["value"] is None
    assert out["null_reason"] == "dataset_unavailable"


def test_steering_fuori_finestra_temporale():
    ds = _dataset(n_hours=2)
    out = nm.steering_for_position(ds, 12.25, 42.25,
                                   _ms("2026-11-20T12:00:00Z"))
    assert out["value"] is None
    assert out["null_reason"] == "valid_time_outside_dataset"


def test_steering_dataset_stantio():
    ds = _dataset()
    ds.generated_at = "2026-11-09T00:00:00+00:00"   # 2 giorni prima
    out = nm.steering_for_position(ds, 12.25, 42.25,
                                   _ms("2026-11-11T12:00:00Z"))
    assert out["value"] is None
    assert out["null_reason"] == "dataset_stale"


# ---------------------------------------------------------------------------
# Tempo locale -> UTC
# ---------------------------------------------------------------------------

def test_offset_roma_inverno_estate():
    winter = nm._dt.datetime(2026, 11, 11, 12, 0)
    summer = nm._dt.datetime(2026, 7, 1, 12, 0)
    assert nm._rome_utc_offset_hours(winter) == 1.0
    assert nm._rome_utc_offset_hours(summer) == 2.0


def test_load_points_time_base_mezzanotte_locale(tmp_path):
    payload = {
        "generated_at": "2026-11-11T10:00:00+00:00",
        "day0": "2026-11-11",
        "timezone": "Europe/Rome",
        "points": [],
    }
    hourly = {}
    for lvl in (850, 700, 600, 500):
        hourly["wind_speed_%dhPa" % lvl] = [10.0, 20.0, 30.0, 40.0]
        hourly["wind_direction_%dhPa" % lvl] = [270.0, 270.0, 270.0, 270.0]
    for lvl in (850, 700, 500):
        hourly["geopotential_height_%dhPa" % lvl] = [1500.0] * 4
    payload["points"] = [
        {"lat": 42.0, "lon": 12.0, "models": {"best_match": {"hourly": hourly}}},
        {"lat": 42.5, "lon": 12.5, "models": {"best_match": {"hourly": hourly}}},
    ]
    path = tmp_path / "points.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    ds = nm.load_points(str(path), force=True)
    assert ds is not None
    assert ds.n_points == 2
    assert ds.n_hours == 4
    # day0 2026-11-11 (CET, UTC+1) -> time_base 2026-11-10T23:00Z
    assert ds.time_base_utc == pytest.approx(
        nm._parse_iso_utc("2026-11-10T23:00:00Z"))
    # direzione 270 = vento DA ovest -> moto VERSO est (u>0)
    u, v = ds.wind[850]
    assert u[0, 0] == pytest.approx(10.0, abs=0.01)
    assert v[0, 0] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------

def test_ensemble_deterministico_e_fascio():
    blend = {"speed_kmh": 30.0, "direction_toward_deg": 60.0}
    e1, b1 = nm.build_ensemble(blend, 10.0, 12.0, (12.0, 42.0),
                               1794398400000, structure_id="CS-X")
    e2, _b2 = nm.build_ensemble(blend, 10.0, 12.0, (12.0, 42.0),
                                1794398400000, structure_id="CS-X")
    assert json.dumps(e1, sort_keys=True) == json.dumps(e2, sort_keys=True)
    assert e1["n"] == 200
    assert [s["offset_min"] for s in e1["steps"]] == [30, 60, 90, 120]
    # 1.2.4.81: niente piu' isolinee di densita', solo il fascio.
    for step in e1["steps"]:
        assert "density_levels" not in step
    members = e1["members"]
    assert len(members) == 24
    for path in members:
        assert len(path) == 5
        for lon, lat in path:
            assert lon == round(lon, 3)
            assert lat == round(lat, 3)
    assert len(e1["mean"]) == 5
    assert e1["mean"][0] == members[0][0]
    assert set(e1["spread_km"]) == {"60", "120"}
    for key in ("60", "120"):
        spread = e1["spread_km"][key]
        assert spread["n"] == 200
        assert spread["along_std_km"] >= 0.0
        assert spread["cross_std_km"] >= 0.0
        assert spread["cross_p90_km"] >= spread["cross_std_km"]
    assert b1["probability_calibrated"] is False
    assert b1["probability_null_reason"]
    assert b1["n"] == 200
    assert b1["members_published"] == 24
    assert b1["member_selection"]


def test_ensemble_spread_cresce_con_il_tempo():
    blend = {"speed_kmh": 35.0, "direction_toward_deg": 70.0}
    ens, _b = nm.build_ensemble(blend, 20.0, 20.0, (12.0, 42.0),
                                1794398400000, structure_id="CS-Y")
    s60 = ens["spread_km"]["60"]
    s120 = ens["spread_km"]["120"]
    assert s120["cross_std_km"] > s60["cross_std_km"]
    assert s120["along_std_km"] > s60["along_std_km"]
    assert s120["cross_p90_km"] > s60["cross_p90_km"]


def test_fascio_non_e_una_macchia():
    """24 percorsi distinti e larghezza trasversale visibile a 60 min.

    Se i membri stessero tutti entro ~1 km la polilinea sarebbe
    indistinguibile da una linea sola (e il fascio inutile): il test
    fallisce in quel caso.
    """
    blend = {"speed_kmh": 35.0, "direction_toward_deg": 70.0}
    ens, _b = nm.build_ensemble(blend, 20.0, 20.0, (12.0, 42.0),
                                1794398400000, structure_id="CS-F")
    ends = [tuple(path[-1]) for path in ens["members"]]
    # Le coppie speculari possono coincidere quando z2 ~ 0 (stessa direzione
    # a 3 decimali): il requisito e' che il fascio NON sia una linea sola.
    assert len(set(ends)) >= 20
    assert ens["spread_km"]["60"]["cross_p90_km"] > 5.0


def test_ensemble_centroide_sul_punto_blend():
    blend = {"speed_kmh": 30.0, "direction_toward_deg": 90.0}
    ens, _b = nm.build_ensemble(blend, 8.0, 8.0, (12.0, 42.0),
                                1794398400000, structure_id="CS-Z")
    step = ens["steps"][1]      # 60 min
    lon, lat = step["centroid"]
    # 30 km/h * 1 h = 30 km verso est a 42N (~0.36 deg lon)
    assert lon == pytest.approx(12.0 + 30.0 / (111.19 * math.cos(
        math.radians(42.0))), abs=0.02)
    assert lat == pytest.approx(42.0, abs=0.02)


def test_seed_diverso_per_struttura_diversa():
    blend = {"speed_kmh": 30.0, "direction_toward_deg": 60.0}
    a, _ = nm.build_ensemble(blend, 10.0, 10.0, (12.0, 42.0), 1,
                             structure_id="A")
    b, _ = nm.build_ensemble(blend, 10.0, 10.0, (12.0, 42.0), 1,
                             structure_id="B")
    assert a["members"] != b["members"]


def test_area_anello_quadrato():
    ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
    area = nm._ring_area_km2(ring)
    assert area == pytest.approx(111.19 * 111.19, rel=0.01)


# ---------------------------------------------------------------------------
# Blocco completo
# ---------------------------------------------------------------------------

def _fit(speed=30.0, direction=70.0, used=True, resid=1.5):
    return {"velocity_kmh": speed, "direction_toward_deg": direction,
            "fit_used": used, "resid_std_km": resid}


def test_build_motion_block_con_nwp():
    ds = _dataset()
    rec = {"position": [12.25, 42.25], "structure_id": "CS-T"}
    out = nm.build_motion_block(rec, None, _fit(), _ms("2026-11-11T12:00:00Z"),
                                dataset=ds)
    assert out["nwp_motion"] is not None
    assert out["nwp_motion_null_reason"] is None
    assert out["nwp_motion_detail"]["points_used"]
    assert out["motion_weights"]["obs"] == 0.8
    assert out["motion_blend"] is not None
    assert out["ensemble"] is not None
    assert out["ensemble_basis"]["probability_calibrated"] is False
    assert out["ensemble_basis"]["sigma_components"]["tracker_speed_kmh"] == \
        nm.TRACKER_SIGMA_SPEED_KMH


def test_build_motion_block_senza_nwp_solo_osservato():
    ds = _dataset(points=[(12.0, 42.0)])
    rec = {"position": [12.0, 45.0], "structure_id": "CS-T"}   # >80 km
    out = nm.build_motion_block(rec, None, _fit(), _ms("2026-11-11T12:00:00Z"),
                                dataset=ds)
    assert out["nwp_motion"] is None
    assert out["nwp_motion_null_reason"].startswith("no_point_within_")
    assert out["motion_weights"]["rule"] == "nwp_unavailable"
    assert out["motion_weights"]["obs"] == 1.0
    assert out["ensemble"] is not None


def test_build_motion_block_senza_moto():
    ds = _dataset(points=[(12.0, 42.0)])
    rec = {"position": [12.0, 45.0], "structure_id": "CS-T"}
    out = nm.build_motion_block(rec, None, _fit(speed=0.0, used=False),
                                _ms("2026-11-11T12:00:00Z"), dataset=ds)
    assert out["ensemble"] is None
    assert out["ensemble_null_reason"] == "no_motion_source"


def test_build_motion_block_posizione_mancante():
    out = nm.build_motion_block({}, None, _fit(), 0, dataset=_dataset())
    assert out["nwp_motion_null_reason"] == "position_missing"
    assert out["ensemble_null_reason"] == "position_missing"


def test_build_motion_block_ancora_mancante():
    out = nm.build_motion_block({"position": [12.0, 42.0]}, None, _fit(), None,
                                dataset=_dataset())
    assert out["nwp_motion_null_reason"] == "valid_time_missing"
    assert out["ensemble_null_reason"] == "valid_time_missing"


# ---------------------------------------------------------------------------
# Integrazione con forecast.py (chiavi additive)
# ---------------------------------------------------------------------------

def test_forecast_espone_i_campi_nwp():
    """Sul forecast.py SENZA patch questo test fallisce (chiavi assenti)."""
    from radar_engine import models

    def cell(lon, lat, ts, i):
        return models.DetectedCell(
            cell_id="c%d" % i, timestamp_ms=ts,
            timestamp_iso=models.utcnow_iso(), area_km2=100.0,
            centroid_raster=(0.0, 0.0), centroid_lonlat=(lon, lat),
            bbox_raster=(0, 0, 1, 1), max_dbz=50.0, mean_dbz=40.0,
            p90_dbz=45.0, eccentricity=0.4, solidity=0.9,
            compactness=2.0, pixel_count=100, frame_index=i)

    base = 1794398400000
    cells = [cell(12.0 + 0.05 * i, 42.0 + 0.05 * i, base + i * 300000, i)
             for i in range(6)]
    track = models.Track(1, cells[0].timestamp_ms)
    for i, c in enumerate(cells):
        track.points.append(models.TrackPoint(1, i, c))
    track.motion = {"velocity_kmh": 30.0, "direction_toward_deg": 45.0,
                    "motion_quality": "reliable"}
    bundle = models.EngineBundle("ok", "2026-11-11T12:40:00Z", "test")
    bundle.radar_timestamp_ms = base + 5 * 300000
    bundle.cells_by_frame = [[c] for c in cells]
    bundle.tracks = [track]
    rec = {"position": [cells[-1].centroid_lonlat[0],
                        cells[-1].centroid_lonlat[1]],
           "track": track, "on_latest_frame": True, "soi": 60,
           "structure_id": "CS-TEST"}
    block = forecast.build_forecast(rec, bundle)
    assert block is not None
    for key in ("nwp_motion", "nwp_motion_null_reason", "nwp_motion_detail",
                "motion_blend", "motion_weights", "motion_blend_components",
                "ensemble", "ensemble_basis", "ensemble_null_reason",
                "ensemble_members", "ensemble_mean", "ensemble_spread_km"):
        assert key in block
