#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del moto pubblicato dal fit pesato e del gate di continuita' (2026-11-10).

Coprono:
  (a) cono ricalibrato: 15 km + 1 km/min -> 45/75/105/135 km a 30/60/90/120
      minuti, con `cone_basis` misurato nel record;
  (b) velocity/direzione dal fit pesato solo con residuo < 6 km, altrimenti
      null con `motion_null_reason` esplicito e `motion_source` coerente;
  (c) gate di continuita': un salto di ~200 km in 10 min (> 110 km/h) non
      viene associato e la detection apre una nuova track;
  (d) nessuna regressione sui track sani: 60 km/h / 90 deg entro 1 km/h e 2.

Nessun dato reale: celle sintetiche e bundle minimo (stessa convenzione di
test_forecast.py).
"""

import math

import pytest

from radar_engine import forecast
from radar_engine import models
from radar_engine import output
from radar_engine.config import CONFIG
from radar_engine.tracking import Tracker, haversine_km

from conftest import make_cell, make_track

T0 = 1_777_027_200_000
STEP_S = 300
KM_PER_DEG = 111.1949


def _cfg(**over):
    cfg = dict(CONFIG["tracking"])
    cfg.update(over)
    return cfg


def _km_per_deg_lon(lat_deg):
    return KM_PER_DEG * math.cos(math.radians(lat_deg))


def _linear_cells(n=8, v_kmh=60.0, heading_deg=90.0, lon0=12.0, lat0=41.0,
                  track_id=1):
    km_step = v_kmh * STEP_S / 3600.0
    dlat = km_step * math.cos(math.radians(heading_deg)) / KM_PER_DEG
    dlon = km_step * math.sin(math.radians(heading_deg)) / _km_per_deg_lon(lat0)
    return [make_cell(lon0 + i * dlon, lat0 + i * dlat,
                      T0 + i * STEP_S * 1000, area_km2=300.0,
                      max_dbz=54.0, mean_dbz=48.0, frame_index=i,
                      cell_id="mfg-{}-{}".format(track_id, i))
            for i in range(n)]


def _bundle(track):
    bundle = models.EngineBundle("ok", "2026-11-10T10:00:00Z",
                                 output.SOURCE_LABEL)
    bundle.tracks = [track]
    bundle.storm_tracks = []
    bundle.supercells = []
    last = track.points[-1].timestamp_ms
    bundle.radar_timestamp_ms = last
    bundle.radar_timestamp_iso = track.points[-1].timestamp_iso
    bundle.cells_by_frame = [[]]
    return bundle


def _forecast_block(n=8, v_kmh=60.0, heading_deg=90.0):
    track = make_track(_linear_cells(n=n, v_kmh=v_kmh,
                                     heading_deg=heading_deg), 1)
    # Dal 2026-11-11 il forecast viene emesso solo su moto affidabile: qui il
    # test misura il CONO, non il gate (coperto da test_forecast_anisotropic).
    track.motion = {"velocity_kmh": v_kmh, "direction_toward_deg": heading_deg,
                    "motion_quality": "reliable", "motion_source": "weighted_fit",
                    "organization_score": 80}
    return forecast.build_forecast(
        {"track": track, "track_id": 1, "track_type": "cell",
         "on_latest_frame": True}, _bundle(track))


# ---------------------------------------------------------------------------
# (a) cono ricalibrato
# ---------------------------------------------------------------------------
def test_cono_ricalibrato_15km_piu_1km_per_minuto():
    block = _forecast_block(n=8, v_kmh=60.0, heading_deg=90.0)
    assert [s["offset_min"] for s in block["steps"]] == [30, 60, 90, 120]
    assert [s["cone_km"] for s in block["steps"]] == [45.0, 75.0, 105.0, 135.0]
    assert [s["cone_km_along"] for s in block["steps"]] == [45.0, 75.0, 105.0, 135.0]
    assert [s["cone_km_cross"] for s in block["steps"]] == [27.0, 45.0, 63.0, 81.0]
    assert block["cone_basis"] == "isotropic_scaled_0.6_cross"


# ---------------------------------------------------------------------------
# (b) fit pesato pubblicato solo con residuo basso
# ---------------------------------------------------------------------------
def test_fit_basso_residuo_pubblicato_come_weighted_fit():
    tracker = Tracker(_cfg())
    for i, cell in enumerate(_linear_cells(n=8, v_kmh=60.0)):
        tracker.update([cell], i)
    tracker.complete_cycles()
    tracker.finalize()
    m = tracker.tracks[0].motion
    assert m["motion_source"] == "weighted_fit"
    assert m["motion_quality"] == "reliable"
    assert m["motion_null_reason"] is None
    assert m["velocity_kmh"] == pytest.approx(60.0, abs=1.0)
    assert m["direction_toward_deg"] == pytest.approx(90.0, abs=2.0)


def test_fit_residuo_alto_non_viene_pubblicato():
    # Oscillazione laterale ampia: il fit esiste ma il residuo e' > 6 km.
    # Track costruita direttamente (make_track): il gate di continuita' non
    # e' l'oggetto di questo test.
    cells = []
    km_step = 60.0 * STEP_S / 3600.0
    dlon = km_step / _km_per_deg_lon(41.0)
    for i in range(8):
        wobble = 0.11 * math.sin(2.0 * math.pi * i / 6.0)
        cells.append(make_cell(12.0 + i * dlon, 41.0 + wobble,
                               T0 + i * STEP_S * 1000, area_km2=300.0,
                               max_dbz=54.0, mean_dbz=48.0, frame_index=i,
                               cell_id="wig-{}".format(i)))
    track = make_track(cells, 1)
    fit = forecast.fit_motion(track.points)
    assert fit["fit_used"] is True
    assert fit["resid_std_km"] > 6.0
    tracker = Tracker(_cfg())
    tracker.tracks = [track]
    tracker.finalize()
    m = track.motion
    assert m["velocity_kmh"] is None
    assert m["direction_toward_deg"] is None
    assert m["direction_from_deg"] is None
    assert m["motion_null_reason"] == "fit_residual_too_high"
    assert m["motion_source"] == "fallback"
    assert m["motion_quality"] == "unreliable"


# ---------------------------------------------------------------------------
# (c) gate di continuita' nell'associazione
# ---------------------------------------------------------------------------
def test_gate_rifiuta_salto_200km_e_apre_nuova_track():
    tracker = Tracker(_cfg())
    a = make_cell(12.0, 41.0, T0, cell_id="gate-a")
    b = make_cell(14.4, 41.0, T0 + 600000, cell_id="gate-b")  # ~200 km/10 min
    implied_kmh = haversine_km(a.centroid_lonlat, b.centroid_lonlat) / (10.0 / 60.0)
    assert implied_kmh > 110.0
    tracker.update([a], 0)
    tracker.update([b], 1)
    tracker.complete_cycles()
    tracker.finalize()
    assert len(tracker.tracks) == 2
    old, new = tracker.tracks
    assert [p.cell_id for p in old.points] == ["gate-a"]
    assert [p.cell_id for p in new.points] == ["gate-b"]
    assert models.EVENT_DEATH in old.events
    assert old.track_id != new.track_id


# ---------------------------------------------------------------------------
# (d) nessuna regressione sui track sani
# ---------------------------------------------------------------------------
def test_track_sano_mantiene_velocita_e_direzione():
    tracker = Tracker(_cfg())
    for i, cell in enumerate(_linear_cells(n=8, v_kmh=60.0, heading_deg=90.0)):
        tracker.update([cell], i)
    tracker.complete_cycles()
    tracker.finalize()
    assert len(tracker.tracks) == 1
    m = tracker.tracks[0].motion
    assert abs(m["velocity_kmh"] - 60.0) <= 1.0
    assert abs((m["direction_toward_deg"] - 90.0 + 180.0) % 360.0 - 180.0) <= 2.0
