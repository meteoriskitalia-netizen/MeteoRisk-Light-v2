#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del cono ANISOTROPO, del gate sul moto e del decay legato al SOI.

Comportamenti introdotti il 2026-11-11 (ognuno FALLISCE sul codice
precedente: cono circolare senza poligono; forecast emesso anche con
`motion_quality="unreliable"`; unica costante di decadimento):
  (a) cono ellittico: semiassi along/cross, poligono GeoJSON di 32 vertici
      orientato lungo il moto, area = pi*along*cross;
  (b) forecast emesso SOLO su moto affidabile: `forecast: null` con
      `forecast_null_reason` = motion_unreliable / fit_not_used /
      velocity_missing;
  (c) decadimento dBZ legato al SOI: SOI < 70 -> tau 21 min, >= 70 -> 39 min;
  (d) `convective_structures.json` dichiara nel summary quante strutture
      hanno il cono ellittico e quanti null con il motivo.

Nessun dato reale: celle sintetiche e bundle minimo (stessa convenzione di
test_forecast.py)."""

import math

from radar_engine import convective_structure as cs_mod
from radar_engine import forecast
from radar_engine import models
from radar_engine import output
from radar_engine.config import CONFIG
from radar_engine.tracking import bearing_deg, haversine_km

from conftest import make_cell, make_track

T0 = 1_777_027_200_000
STEP_S = 300
KM_PER_DEG = 111.1949


def _km_per_deg_lon(lat_deg):
    return KM_PER_DEG * math.cos(math.radians(lat_deg))


def _reliable_motion(**over):
    motion = {
        "organization_score": 80,
        "classification": "Organized Convective Cell",
        "velocity_kmh": 60.0,
        "direction_toward_deg": 90.0,
        "motion_quality": "reliable",
        "motion_source": "weighted_fit",
        "motion_null_reason": None,
        "distance_km": 40.0,
        "duration_min": 35.0,
        "area_growth_pct": 20.0,
        "intensity_delta_dbz": 0.0,
    }
    motion.update(over)
    return motion


def _track(n=8, v_kmh=60.0, heading_deg=90.0, lon0=12.0, lat0=41.0,
           track_id=1, motion=None, max_dbz=54.0, mean_dbz=48.0):
    km_step = v_kmh * STEP_S / 3600.0
    dlat = km_step * math.cos(math.radians(heading_deg)) / KM_PER_DEG
    dlon = km_step * math.sin(math.radians(heading_deg)) / _km_per_deg_lon(lat0)
    cells = [make_cell(lon0 + i * dlon, lat0 + i * dlat,
                       T0 + i * STEP_S * 1000, area_km2=300.0,
                       max_dbz=max_dbz, mean_dbz=mean_dbz, frame_index=i,
                       cell_id="an-{}-{}".format(track_id, i))
             for i in range(n)]
    trk = make_track(cells, track_id)
    trk.motion = dict(motion if motion is not None else _reliable_motion())
    return trk


def _bundle(tracks):
    bundle = models.EngineBundle("ok", "2026-11-11T10:00:00Z",
                                 output.SOURCE_LABEL)
    bundle.tracks = list(tracks)
    bundle.storm_tracks = []
    bundle.radar_timestamp_ms = max(t.points[-1].timestamp_ms for t in tracks)
    bundle.radar_timestamp_iso = tracks[0].points[-1].timestamp_iso
    bundle.cells_by_frame = [[c] for c in tracks[0].points]
    return bundle


def _ctx(track, **over):
    ctx = {"track": track, "track_id": track.track_id, "track_type": "cell",
           "on_latest_frame": True, "soi": 80}
    ctx.update(over)
    return ctx


def _local_km(lon, lat, lon0, lat0):
    return ((lon - lon0) * KM_PER_DEG * math.cos(math.radians(lat0)),
            (lat - lat0) * KM_PER_DEG)


def _ring_area_km2(ring, lon0, lat0):
    pts = [_local_km(p[0], p[1], lon0, lat0) for p in ring]
    acc = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        acc += x0 * y1 - x1 * y0
    return abs(acc) / 2.0


def _ang_diff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ---------------------------------------------------------------------------
# (a) cono ellittico
# ---------------------------------------------------------------------------
def test_cono_ellittico_con_poligono_e_semiassi():
    trk = _track()
    block = forecast.build_forecast(_ctx(trk), _bundle([trk]))
    assert block["cone_shape"] == "ellipse"
    assert block["cone_basis"] == "isotropic_scaled_0.6_cross"
    assert block["cone_cross_ratio"] == 0.6
    assert block["cone_vertices"] == 32
    step = block["steps"][0]
    assert step["cone_km_along"] == 45.0
    assert step["cone_km_cross"] == 27.0
    # retro-compatibilita': cone_km resta il semiasse lungo
    assert step["cone_km"] == step["cone_km_along"]
    ring = step["cone_polygon"]["coordinates"][0]
    assert len(ring) == 33 and ring[0] == ring[-1]
    lon0, lat0 = step["position"]
    # vertice 0 = estremo LUNGO il moto (bearing 90 -> est), distanza = along
    assert abs(haversine_km((lon0, lat0), tuple(ring[0])) - 45.0) <= 0.2
    assert _ang_diff(bearing_deg((lon0, lat0), tuple(ring[0])), 90.0) <= 1.0
    # vertice 8 (theta=90) = estremo TRASVERSALE destro (sud per moto a est)
    assert abs(haversine_km((lon0, lat0), tuple(ring[8])) - 27.0) <= 0.2
    assert _ang_diff(bearing_deg((lon0, lat0), tuple(ring[8])), 180.0) <= 1.0
    # area del poligono ~ pi * along * cross (ellisse, non cerchio)
    area = _ring_area_km2(ring, lon0, lat0)
    expected = math.pi * 45.0 * 27.0
    assert abs(area - expected) / expected < 0.02


def test_cono_ellittico_piu_stretto_di_lato_in_ogni_step():
    trk = _track()
    block = forecast.build_forecast(_ctx(trk), _bundle([trk]))
    for step in block["steps"]:
        along = step["cone_km_along"]
        cross = step["cone_km_cross"]
        assert abs(cross - 0.6 * along) <= 1e-9
        assert cross < along
        # area ellisse = 0.6 * area del cerchio con lo stesso `cone_km`
        circle = math.pi * along * along
        ellipse = math.pi * along * cross
        assert abs(ellipse / circle - 0.6) <= 1e-9


def test_cono_ellittico_ruotato_con_il_moto():
    """Con moto verso nord il semiasse lungo deve puntare a nord."""
    trk = _track(heading_deg=0.0)
    block = forecast.build_forecast(_ctx(trk), _bundle([trk]))
    step = block["steps"][0]
    lon0, lat0 = step["position"]
    ring = step["cone_polygon"]["coordinates"][0]
    assert abs(haversine_km((lon0, lat0), tuple(ring[0])) - 45.0) <= 0.2
    assert _ang_diff(bearing_deg((lon0, lat0), tuple(ring[0])), 0.0) <= 1.0
    assert abs(haversine_km((lon0, lat0), tuple(ring[8])) - 27.0) <= 0.2
    assert _ang_diff(bearing_deg((lon0, lat0), tuple(ring[8])), 90.0) <= 1.0


# ---------------------------------------------------------------------------
# (b) forecast solo su moto affidabile
# ---------------------------------------------------------------------------
def test_forecast_null_motion_unreliable():
    trk = _track(motion=_reliable_motion(motion_quality="unreliable"))
    block, reason = forecast.build_forecast_with_reason(_ctx(trk),
                                                        _bundle([trk]))
    assert block is None
    assert reason == "motion_unreliable"


def test_forecast_null_fit_not_used():
    trk = _track(n=2)
    block, reason = forecast.build_forecast_with_reason(_ctx(trk),
                                                        _bundle([trk]))
    assert block is None
    assert reason == "fit_not_used"


def test_forecast_null_velocity_missing():
    trk = _track(motion=_reliable_motion(velocity_kmh=None))
    block, reason = forecast.build_forecast_with_reason(_ctx(trk),
                                                        _bundle([trk]))
    assert block is None
    assert reason == "velocity_missing"


def test_moto_affidabile_emette_forecast_con_motivo_null():
    trk = _track()
    block, reason = forecast.build_forecast_with_reason(_ctx(trk),
                                                        _bundle([trk]))
    assert block is not None
    assert reason is None


def test_evaluate_pubblica_forecast_null_reason():
    good = _track(track_id=1)
    bad = _track(track_id=2, lon0=9.0,
                 motion=_reliable_motion(motion_quality="unreliable"))
    bundle = _bundle([good])
    bundle.tracks = [good, bad]
    records = cs_mod.evaluate(bundle, CONFIG["convective_structure"])
    by_id = {rec["track_id"]: rec for rec in records}
    assert by_id[1]["forecast"] is not None
    assert by_id[1]["forecast_null_reason"] is None
    assert by_id[2]["forecast"] is None
    assert by_id[2]["forecast_null_reason"] == "motion_unreliable"


# ---------------------------------------------------------------------------
# (c) decadimento legato al SOI
# ---------------------------------------------------------------------------
def test_decay_due_classi_dichiarate():
    weak = _track(track_id=1, max_dbz=54.0, mean_dbz=48.0)
    strong = _track(track_id=2, lon0=9.0, max_dbz=54.0, mean_dbz=48.0)
    bg = _track(track_id=3, lon0=15.0, max_dbz=20.0, mean_dbz=15.0)
    bundle = _bundle([weak])
    bundle.tracks = [weak, strong, bg]
    fast = forecast.build_forecast(_ctx(weak, soi=50), bundle)
    slow = forecast.build_forecast(_ctx(strong, soi=80), bundle)
    assert fast["decay_class"] == "fast"
    assert fast["decay_lambda_min"] == 21.0
    assert slow["decay_class"] == "slow"
    assert slow["decay_lambda_min"] == 39.0
    # a 120 min la cella "lenta" e' piu' vicina alla propria intensita'
    assert slow["steps"][-1]["max_dbz"] > fast["steps"][-1]["max_dbz"]


def test_decay_default_senza_soi_ne_organization_score():
    trk = _track(motion={"velocity_kmh": 60.0, "motion_quality": "reliable"})
    block = forecast.build_forecast(_ctx(trk, soi=None), _bundle([trk]))
    assert block["decay_class"] == "default"
    assert block["decay_lambda_min"] == 30.0


# ---------------------------------------------------------------------------
# (d) summary pubblicato
# ---------------------------------------------------------------------------
def test_output_summary_conta_ellisse_e_motivi_null():
    good = _track(track_id=1)
    bad = _track(track_id=2, lon0=9.0,
                 motion=_reliable_motion(motion_quality="unreliable"))
    bundle = _bundle([good])
    bundle.tracks = [good, bad]
    cs_mod.evaluate(bundle, CONFIG["convective_structure"])
    payload = output.build_convective_structures(bundle)
    summary = payload["summary"]
    assert summary["structures"] == 2
    assert summary["cone_ellipse"] == 1
    assert summary["forecast_null"] == 1
    assert summary["forecast_null_reasons"] == {"motion_unreliable": 1}
    # il record senza forecast e' pubblicato comunque (il filtro e' sul moto)
    rec = [r for r in payload["structures"] if r["track_id"] == 2][0]
    assert rec["forecast"] is None
    assert rec["forecast_null_reason"] == "motion_unreliable"
