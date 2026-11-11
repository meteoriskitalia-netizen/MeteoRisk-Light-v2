#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Metriche di regressione del tracking (2026-11-11).

I test FALLISCONO sul codice precedente: `tracking_metrics`/
`build_tracking_metrics` non esistono. Le definizioni sono quelle pubblicate
in `tracking_metrics` (id_switch_rate = (salti + re-ID inspiegati)/n_track).
"""

from radar_engine import models
from radar_engine.tracking import build_tracking_metrics, tracking_metrics

from conftest import make_cell, make_track

T0 = 1_777_027_200_000
STEP_MS = 300_000


def _track(cells, track_id, quality="reliable", duration=10.0,
           status=models.STATUS_ACTIVE, lineage=None, parents=None,
           children=None, continued=False):
    track = make_track(cells, track_id)
    track.status = status
    track.motion = {"motion_quality": quality, "duration_min": duration}
    track.lineage = lineage
    track.parent_ids = list(parents or [])
    track.child_ids = list(children or [])
    track.continued_from_history = continued
    return track


def _fixture_tracks():
    a = _track([
        make_cell(12.0, 41.0, T0, cell_id="a1"),
        make_cell(12.05, 41.0, T0 + STEP_MS, cell_id="a2"),
        make_cell(12.10, 41.0, T0 + 2 * STEP_MS, cell_id="a3"),
    ], 1, duration=10.0, lineage="continuation")
    b = _track([
        make_cell(12.0, 41.0, T0, cell_id="b1"),
        make_cell(12.0, 41.0, T0 + STEP_MS, cell_id="b2"),
    ], 2, quality="unreliable", duration=5.0,
        status=models.STATUS_DEAD, lineage="split")
    c = _track([
        make_cell(12.10, 41.0, T0 + 2 * STEP_MS, cell_id="c1"),
        make_cell(12.15, 41.0, T0 + 3 * STEP_MS, cell_id="c2"),
    ], 3, duration=15.0, lineage="merge", parents=[2])
    d = _track([
        make_cell(12.05, 41.0, T0 + 2 * STEP_MS, cell_id="d1"),
        make_cell(12.10, 41.0, T0 + 3 * STEP_MS, cell_id="d2"),
    ], 4, duration=10.0)
    e = _track([
        make_cell(30.0, 41.0, T0 + 2 * STEP_MS, cell_id="e1"),
        make_cell(31.0, 41.0, T0 + 3 * STEP_MS, cell_id="e2"),
    ], 5, duration=5.0)
    return [a, b, c, d, e]


def test_layer_metrics_values():
    m = tracking_metrics(_fixture_tracks(), "cell")
    assert m["n_tracks"] == 5
    assert m["n_active_tracks"] == 4
    assert m["pct_reliable"] == 80.0          # A, C, D, E su 5 (B unreliable)
    assert m["median_duration_min"] == 10.0   # attive: 10, 15, 10, 5
    assert m["id_switch_jumps"] == 1          # E: ~85 km in 5 min
    assert m["id_switch_reids"] == 1          # D riprende B senza lineage
    assert m["id_switch_rate"] == 0.4
    assert m["lineage_split"] == 1
    assert m["lineage_merge"] == 1
    assert m["lineage_continuation"] == 1
    assert m["lineage_links"] == 1
    assert m["n_frames_ge_3"] == 1
    assert m["continued_from_history"] == 0
    assert m["thresholds"]["max_speed_kmh"] == 110.0
    assert m["thresholds"]["reid_window_min"] == 15.0
    assert m["thresholds"]["reid_radius_km"] == 20.0


def test_lineage_link_suppresses_reid():
    tracks = _fixture_tracks()
    b, c, d = tracks[1], tracks[2], tracks[3]
    linked = tracking_metrics([b, c], "cell")
    assert linked["id_switch_reids"] == 0
    unlinked = tracking_metrics([b, d], "cell")
    assert unlinked["id_switch_reids"] == 1


def test_history_continuation_suppresses_reid():
    tracks = _fixture_tracks()
    b, d = tracks[1], tracks[3]
    d.continued_from_history = True
    assert tracking_metrics([b, d], "cell")["id_switch_reids"] == 0


def test_single_point_tracks_are_not_published_metrics():
    tracks = _fixture_tracks()
    one = _track([make_cell(40.0, 41.0, T0, cell_id="z1")], 6)
    m = tracking_metrics(tracks + [one], "cell")
    assert m["n_tracks"] == 5                 # n_frames >= 2
    assert m["pct_reliable"] == 80.0


def test_build_tracking_metrics_flat_keys_are_combined():
    tracks = _fixture_tracks()
    out = build_tracking_metrics(tracks, [])
    assert out["primary_layer"] == "combined"
    for key in ("pct_reliable", "median_duration_min", "id_switch_rate",
                "lineage_split", "lineage_merge", "n_tracks",
                "n_frames_ge_3", "continued_from_history"):
        assert key in out
    assert out["pct_reliable"] == 80.0
    assert out["cell"]["n_tracks"] == 5
    assert out["storm_object"]["n_tracks"] == 0
    assert out["combined"]["n_tracks"] == 5


def test_n_frames_ge_3_counts_current_frames_only():
    """Il punto di archivio (frame_index=-1) non e' un frame del ciclo."""
    track = _track([
        make_cell(12.05, 41.0, T0 + STEP_MS, cell_id="s1"),
        make_cell(12.10, 41.0, T0 + 2 * STEP_MS, cell_id="s2"),
    ], 1)
    prior = models.TrackPoint(1, -1, make_cell(12.0, 41.0, T0,
                                               cell_id="history:1"))
    track.points.insert(0, prior)
    m = tracking_metrics([track], "cell")
    assert m["n_frames_ge_3"] == 0
    assert m["n_frames_current_ge_3"] == 0
    assert track.to_dict()["n_frames"] == 2


def test_combined_does_not_count_cross_layer_reids():
    """Un cell track morto non e' il genitore di uno storm track nato vicino."""
    cell = _track([
        make_cell(12.0, 41.0, T0, cell_id="x1"),
        make_cell(12.0, 41.0, T0 + STEP_MS, cell_id="x2"),
    ], 1, status=models.STATUS_DEAD)
    storm = _track([
        make_cell(12.05, 41.0, T0 + 2 * STEP_MS, cell_id="y1"),
        make_cell(12.10, 41.0, T0 + 3 * STEP_MS, cell_id="y2"),
    ], 2)
    out = build_tracking_metrics([cell], [storm])
    assert out["cell"]["id_switch_reids"] == 0
    assert out["storm_object"]["id_switch_reids"] == 0
    assert out["combined"]["id_switch_reids"] == 0
    assert out["combined"]["id_switch_rate"] == 0.0
