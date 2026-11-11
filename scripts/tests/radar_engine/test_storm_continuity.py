#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del gate di continuita' degli storm object (2026-11-11).

Coprono il difetto MISURATO sul run 2026-11-10T22:55:36Z (20 storm track su
37 con almeno un segmento >110 km/h, massimo 12.703 km/h; 5 delle 7 strutture
pubblicate costruite su quei salti):

    (a) un salto impossibile spezza lo storm object in due track e nessuna
        track pubblicata lo attraversa;
    (b) `cell_count` e `area_km2` sono presenti e coerenti con la maschera
        dell'oggetto (celle dell'ultimo frame);
    (c) `track_coherent=false` -> `motion_quality != "reliable"` e velocity
        null con `motion_null_reason="track_not_coherent"`.
"""

import math

from radar_engine import aggregation as agg
from radar_engine import convective_structure as cs
from radar_engine import forecast
from radar_engine import models
from radar_engine import output
from radar_engine import storm_tracking as st
from radar_engine.config import CONFIG

from conftest import make_cell

T0 = 1_777_027_200_000
STEP_MS = 300_000  # 5 min VMI


def _pipeline(lons):
    """Aggregazione + StormObjectTracker su una cella per frame."""
    cfg = CONFIG["storm"]
    objs_by_frame = []
    for f, lon in enumerate(lons):
        cells = [make_cell(lon, 41.0, T0 + f * STEP_MS, frame_index=f,
                           cell_id="c{}-0".format(f))]
        objs_by_frame.append(agg.aggregate_frame(cells, f, cfg))
    tracker = st.StormObjectTracker(cfg)
    for f, objs in enumerate(objs_by_frame):
        tracker.update(objs, f)
    tracker.complete_cycles()
    return objs_by_frame, tracker.finalize()


def _storm_object(idx, lon, ts_ms, frame_index, n_cells=2, cell_area=120.0):
    """StormObject con una maschera di `n_cells` celle e area nota."""
    cells = [
        make_cell(lon + 0.005 * j, 41.0, ts_ms, area_km2=cell_area,
                  max_dbz=55.0, mean_dbz=52.0, frame_index=frame_index,
                  cell_id="o{}-c{}".format(idx, j))
        for j in range(n_cells)
    ]
    area = sum(c.area_km2 for c in cells)
    obj = models.StormObject(
        storm_object_id="o{}".format(idx), timestamp_ms=ts_ms,
        timestamp_iso=cells[0].timestamp_iso, frame_index=frame_index,
        cells=cells, centroid_lonlat=(lon, 41.0), area_km2=area,
        equiv_radius_km=math.sqrt(area / math.pi),
        bbox_lonlat=(lon, 41.0, lon + 0.01, 41.01),
        convex_hull_lonlat=[], max_dbz=55.0, mean_dbz=52.0, p90_dbz=52.0,
        cell_density=n_cells)
    obj.tracking_ambiguity = "low"
    obj.ambiguity_reason = {"merge_split": False, "competing": 1}
    return obj


def _storm_track(lons, track_id=1):
    """StormTrack con Organization Score alto e motion dichiarato reliable."""
    track = models.StormTrack(track_id, T0)
    for i, lon in enumerate(lons):
        track.points.append(models.StormTrackPoint(
            track_id, i, _storm_object(i, lon, T0 + i * STEP_MS, i)))
    dist = sum(st.haversine_km(a.lonlat, b.lonlat)
               for a, b in zip(track.points, track.points[1:]))
    track.motion = {
        "organization_score": 90,
        "classification": "Organized Convective Cell",
        "velocity_kmh": 60.0,
        "motion_quality": "reliable",
        "motion_null_reason": None,
        "distance_km": round(dist, 2),
        "duration_min": round((len(lons) - 1) * STEP_MS / 60000.0, 1),
        "_avg_solidity": 0.92,
        "_avg_compactness": 2.5,
    }
    return track


def _bundle(track, frames=5):
    b = models.EngineBundle("ok", "2026-11-10T22:55:36Z", output.SOURCE_LABEL)
    b.radar_timestamp_iso = "2026-11-10T22:55:36Z"
    b.cells_by_frame = [[] for _ in range(frames)]
    b.tracks = []
    b.storm_tracks = [track]
    return b


def test_gate_splits_storm_object_with_impossible_jump():
    """(a) 10.0 -> 10.06 -> 20.0: il salto (~830 km in 5 min) spezza la track."""
    objs, tracks = _pipeline([10.0, 10.06, 20.0, 20.06])
    assert [len(o) for o in objs] == [1, 1, 1, 1]
    assert len(tracks) == 2
    assert sorted(len(t.points) for t in tracks) == [2, 2]
    # nessuna track (ne' la vecchia ne' la nuova) attraversa il salto
    for t in tracks:
        assert all(v <= forecast.SPEED_RANGE_KMH[1]
                   for v in st.segment_velocities_kmh(t.points))
        assert "motion_null_reason" in t.motion


def test_record_composition_matches_object_mask():
    """(b) cell_count/area_km2 del record = maschera dell'ultimo frame."""
    track = _storm_track([10.0 + 0.06 * i for i in range(5)])
    b = _bundle(track)
    cs.evaluate(b, CONFIG["convective_structure"])
    assert len(b.convective_structures) == 1
    rec = b.convective_structures[0]
    last_obj = track.points[-1]._obj
    assert rec["cell_count"] == len(last_obj.cells) == 2
    assert rec["area_km2"] == round(sum(c.area_km2
                                        for c in last_obj.cells), 2)
    assert rec["track_points"] == len(track.points) == 5
    assert rec["distance_km"] == track.motion["distance_km"]
    assert rec["track_coherent"] is True
    assert rec["motion_reliable"] is True
    assert rec["motion_null_reason"] is None
    assert rec["motion"]["motion_quality"] == "reliable"
    assert rec["motion"]["velocity_kmh"] == 60.0


def test_incoherent_track_cannot_publish_reliable_motion():
    """(c) track con salto: track_coherent=false e nessun moto reliable."""
    track = _storm_track([10.0, 20.0, 20.06, 20.12, 20.18])
    # il motion dichiarato a monte e' "reliable": il record NON deve fidarsi
    assert track.motion["motion_quality"] == "reliable"
    b = _bundle(track)
    cs.evaluate(b, CONFIG["convective_structure"])
    assert len(b.convective_structures) == 1
    rec = b.convective_structures[0]
    assert rec["track_coherent"] is False
    assert rec["motion_reliable"] is False
    assert rec["motion_null_reason"] == "track_not_coherent"
    assert rec["motion"]["motion_quality"] != "reliable"
    assert rec["motion"]["velocity_kmh"] is None
    assert rec["motion"]["motion_null_reason"] == "track_not_coherent"
    assert rec["rotation_measurable"] is False


def test_coherent_track_without_velocity_propagates_null_reason():
    """Moto nullo su track coerente: il motivo della track arriva in chiaro."""
    track = _storm_track([10.0 + 0.06 * i for i in range(5)])
    track.motion["velocity_kmh"] = None
    track.motion["motion_quality"] = "unreliable"
    track.motion["motion_null_reason"] = "fit_residual_too_high"
    b = _bundle(track)
    cs.evaluate(b, CONFIG["convective_structure"])
    assert len(b.convective_structures) == 1
    rec = b.convective_structures[0]
    assert rec["track_coherent"] is True
    assert rec["motion_reliable"] is False
    assert rec["motion_null_reason"] == "fit_residual_too_high"


def test_short_track_is_not_coherent_even_if_slow():
    """Meno di 4 punti: track_coherent False e moto non pubblicabile."""
    track = _storm_track([10.0 + 0.06 * i for i in range(3)])
    b = _bundle(track)
    cs.evaluate(b, CONFIG["convective_structure"])
    assert len(b.convective_structures) == 1
    rec = b.convective_structures[0]
    assert rec["track_points"] == 3
    assert rec["track_coherent"] is False
    assert rec["motion_reliable"] is False
    assert rec["motion_null_reason"] == "track_not_coherent"
    assert rec["motion"]["velocity_kmh"] is None
    assert rec["motion"]["motion_quality"] == "unreliable"


def test_storm_gate_value_is_the_forecast_speed_range():
    """Il gate 110 non e' una seconda costante: viene da forecast."""
    assert CONFIG["storm"]["tracking"]["max_storm_speed_kmh"] == \
        forecast.SPEED_RANGE_KMH[1] == 110.0
