#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Coasting di 1 frame (2026-11-21) — TEST UNITARI SINTETICI DICHIARATI.

Tutti i casi qui sotto usano celle/oggetti e timestamp SINTETICI: servono a
verificare la MECCANICA (sopravvivenza, morte, null_reason, invarianza del
fit), NON a dimostrare un guadagno meteorologico. L'accettazione su dati REALI
e' descritta in REPORT.md e usa i JSON pubblicati in data/radar/.
"""

import math

from radar_engine import models
from radar_engine import output
from radar_engine import tracking as trk
from radar_engine import storm_tracking as st
from radar_engine.config import CONFIG

from conftest import make_cell

T0 = 1_777_027_200_000
STEP = 300_000
COAST_CFG = dict(CONFIG.get("coasting") or {})
VALID = {"valid": True, "coverage_ok": True}


def _tracker():
    try:
        return trk.Tracker(dict(CONFIG["tracking"]), coasting_cfg=COAST_CFG)
    except TypeError:                      # pristino: firma senza coasting_cfg
        return trk.Tracker(dict(CONFIG["tracking"]))


def _storm_tracker():
    try:
        return st.StormObjectTracker(CONFIG["storm"], coasting_cfg=COAST_CFG)
    except TypeError:                      # pristino
        return st.StormObjectTracker(CONFIG["storm"])


def _cell(i, lon, ts=None):
    return make_cell(lon, 41.0, T0 + i * STEP if ts is None else ts,
                     cell_id="c{}".format(i), frame_index=i)


def _obj(i, lon):
    cells = [_cell(i, lon), _cell(i, lon + 0.005)]
    area = sum(c.area_km2 for c in cells)
    o = models.StormObject(
        storm_object_id="o{}".format(i), timestamp_ms=T0 + i * STEP,
        timestamp_iso=cells[0].timestamp_iso, frame_index=i, cells=cells,
        centroid_lonlat=(lon, 41.0), area_km2=area,
        equiv_radius_km=math.sqrt(area / math.pi),
        bbox_lonlat=(lon, 41.0, lon + 0.01, 41.01),
        convex_hull_lonlat=[], max_dbz=55.0, mean_dbz=50.0, p90_dbz=52.0,
        cell_density=2)
    o.tracking_ambiguity = "low"
    o.ambiguity_reason = {"merge_split": False, "competing": 1}
    return o


# (a) -----------------------------------------------------------------
def test_one_missing_frame_keeps_track_with_coasted_point():
    t = _tracker()
    t.update([_cell(0, 12.00)], 0, frame_ts_ms=T0, frame_context=VALID)
    t.update([_cell(1, 12.10)], 1, frame_ts_ms=T0 + STEP, frame_context=VALID)
    t.update([], 2, frame_ts_ms=T0 + 2 * STEP, frame_context=VALID)
    track = t.tracks[0]
    assert track.status == models.STATUS_ACTIVE
    assert len(track.points) == 3
    pt = track.points[-1]
    assert pt.position_source == "coasted" and pt.observed is False
    assert pt.cell_id is None and pt.frame_index == 2
    # 0.1 deg lon / 5 min propagato di un altro passo -> ~12.20
    assert 12.19 <= pt.lonlat[0] <= 12.21
    assert t.coasting["coasted_points"] == 1
    # riassociazione al frame 3: il punto stimato NON diventa origine del gate
    t.update([_cell(3, 12.30)], 3, frame_ts_ms=T0 + 3 * STEP,
             frame_context=VALID)
    assert track.status == models.STATUS_ACTIVE
    assert t.coasting["coasted_tracks_recovered"] == 1
    assert track.points[-1].observed is True


def test_one_missing_frame_storm_layer():
    s = _storm_tracker()
    s.update([_obj(0, 12.00)], 0, frame_ts_ms=T0, frame_context=VALID)
    s.update([_obj(1, 12.10)], 1, frame_ts_ms=T0 + STEP, frame_context=VALID)
    s.update([], 2, frame_ts_ms=T0 + 2 * STEP, frame_context=VALID)
    track = s.tracks[0]
    assert track.status == models.STATUS_ACTIVE
    assert track.points[-1].position_source == "coasted"
    assert track.points[-1].observed is False
    assert s.coasting["coasted_points"] == 1


# (b) -----------------------------------------------------------------
def test_two_missing_frames_kill_track():
    t = _tracker()
    t.update([_cell(0, 12.00)], 0, frame_ts_ms=T0, frame_context=VALID)
    t.update([_cell(1, 12.10)], 1, frame_ts_ms=T0 + STEP, frame_context=VALID)
    t.update([], 2, frame_ts_ms=T0 + 2 * STEP, frame_context=VALID)
    t.update([], 3, frame_ts_ms=T0 + 3 * STEP, frame_context=VALID)
    track = t.tracks[0]
    assert track.status == models.STATUS_DEAD
    assert models.EVENT_DEATH in track.events
    assert len(track.points) == 3          # 2 osservati + 1 stimato, poi morte
    assert t.coasting["coasted_points"] == 1
    assert t.coasting["coasted_tracks_recovered"] == 0


# (c) -----------------------------------------------------------------
def test_insufficient_coverage_forbids_coasting_with_null_reason():
    bad = {"valid": True, "coverage_ok": False, "coverage_fraction": 0.01}
    t = _tracker()
    t.update([_cell(0, 12.00)], 0, frame_ts_ms=T0, frame_context=VALID)
    t.update([_cell(1, 12.10)], 1, frame_ts_ms=T0 + STEP, frame_context=VALID)
    t.update([], 2, frame_ts_ms=T0 + 2 * STEP, frame_context=bad)
    track = t.tracks[0]
    assert track.status == models.STATUS_DEAD
    assert track.coasting_null_reason == "insufficient_coverage"
    assert t.coasting["coasted_points"] == 0
    assert t.coasting["null_reasons"]["insufficient_coverage"] == 1


def test_no_valid_frame_and_stale_forbid_coasting():
    for ctx, reason in (
            ({"valid": False, "null_reason": "no_valid_frame"}, "no_valid_frame"),
            ({"valid": True, "stale": True}, "stale_data"),
            ({"valid": True, "coverage_fraction": 0.0,
              "min_coverage_fraction": 0.05}, "insufficient_coverage")):
        allowed, got = trk.coast_frame_context(ctx)
        assert allowed is False and got == reason


# (d) -----------------------------------------------------------------
def test_motion_fit_identical_with_and_without_coasted_point():
    # 5 osservazioni su 6 frame; il frame 2 e' vuoto in `with_coast`.
    frames = [(0, 12.00), (1, 12.10), (3, 12.30), (4, 12.40), (5, 12.50)]
    base = models.Track(1, T0)
    coasted = models.Track(1, T0)
    for i, lon in frames:
        c = _cell(i, lon)
        base.points.append(models.TrackPoint(1, i, c))
        coasted.points.append(models.TrackPoint(1, i, c))
    gap = models.TrackPoint(1, 2, _cell(2, 12.20))
    gap.position_source, gap.observed = "coasted", False
    coasted.points.insert(2, gap)
    trk.Tracker(dict(CONFIG["tracking"])).finalize()  # no-op (tracks vuote)
    t1 = trk.Tracker(dict(CONFIG["tracking"]))
    t2 = trk.Tracker(dict(CONFIG["tracking"]))
    t1.tracks, t2.tracks = [base], [coasted]
    t1.finalize()
    t2.finalize()
    for key in ("velocity_kmh", "direction_toward_deg", "direction_from_deg",
                "distance_km", "duration_min", "duration_observed_min",
                "motion_quality", "motion_null_reason"):
        assert base.motion[key] == coasted.motion[key], key
    assert base.tracking_confidence == coasted.tracking_confidence
    assert base.to_dict()["duration_observed_min"] == \
        coasted.to_dict()["duration_observed_min"]


# config / output ------------------------------------------------------
def test_config_enables_only_new_block_and_keeps_110kmh_gate():
    assert CONFIG["coasting"]["max_coast_frames"] == 1
    assert CONFIG["tracking"]["max_coast_frames"] == 0
    assert CONFIG["storm"]["tracking"]["max_coast_frames"] == 0
    assert CONFIG["storm"]["tracking"]["max_storm_speed_kmh"] == 110.0
    assert "SCELTA INGEGNERISTICA" in CONFIG["coasting"]["reference"]


def test_latest_publishes_counters_without_raw_points(tmp_path):
    b = models.EngineBundle("ok", "2026-04-30T10:00:00Z", output.SOURCE_LABEL)
    b.radar_timestamp_iso = "2026-04-30T10:00:00Z"
    b.radar_timestamp_ms = T0
    b.cells_by_frame = [[], []]
    b.tracks = []
    b.tracking_metrics = trk.build_tracking_metrics(
        [], [], cell_coasting={"coasted_points": 3,
                               "coasted_tracks_recovered": 2,
                               "null_reasons": {"stale_data": 1}},
        storm_coasting={"coasted_points": 1,
                        "coasted_tracks_recovered": 0,
                        "null_reasons": {"no_valid_frame": 2}})
    out = str(tmp_path / "radar")
    paths = output.write_outputs(b, out)
    import json
    with open(paths["latest.json"], encoding="utf-8") as fh:
        latest = json.load(fh)
    assert latest["coasted_points"] == 4
    assert latest["coasted_tracks_recovered"] == 2
    assert latest["coasting_null_reasons"] == {
        "stale_data": 1, "no_valid_frame": 2}
    assert "points" not in latest and "positions" not in latest
