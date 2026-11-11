#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lineage merge/split (2026-11-11): relazioni esplicite fra track.

I test FALLISCONO sul codice precedente: `parent_ids`/`child_ids`/`lineage`
non esistono e una divisione/fusione chiudeva la track senza registrare nulla.
"""

import math

from radar_engine import models
from radar_engine import storm_tracking as st
from radar_engine.config import CONFIG
from radar_engine.tracking import Tracker

from conftest import make_cell

T0 = 1_777_027_200_000
STEP_MS = 300_000


def _cfg(**over):
    cfg = dict(CONFIG["tracking"])
    cfg.update(over)
    return cfg


def _storm(idx, lon, lat, ts, area, frame_index=0):
    cells = [make_cell(lon, lat, ts, area_km2=area, frame_index=frame_index,
                       cell_id="o{}-c0".format(idx))]
    return models.StormObject(
        storm_object_id="o{}".format(idx), timestamp_ms=ts,
        timestamp_iso=cells[0].timestamp_iso, frame_index=frame_index,
        cells=cells, centroid_lonlat=(lon, lat), area_km2=area,
        equiv_radius_km=math.sqrt(area / math.pi),
        bbox_lonlat=(lon, lat, lon, lat), convex_hull_lonlat=[],
        max_dbz=42.0, mean_dbz=38.0, p90_dbz=40.0, cell_density=1)


# ---------------------------------------------------------------------------
# Layer cella
# ---------------------------------------------------------------------------
def test_cell_split_records_parent_and_child():
    tr = Tracker(_cfg())
    one = make_cell(12.0, 41.0, T0, area_km2=200.0)
    two_a = make_cell(11.98, 41.0, T0 + STEP_MS, area_km2=110.0)
    two_b = make_cell(12.03, 41.0, T0 + STEP_MS, area_km2=110.0)
    tr.update([one], 0)
    tr.update([two_a, two_b], 1)
    tr.complete_cycles()
    parent = tr.tracks[0]
    children = [t for t in tr.tracks if t.track_id != parent.track_id]
    assert len(children) == 1
    child = children[0]
    assert child.parent_ids == [parent.track_id]
    assert child.lineage == "split"
    assert parent.child_ids == [child.track_id]
    assert "lineage_split" in child.events
    d = child.to_dict()
    assert d["parent_ids"] == [parent.track_id]
    assert d["lineage"] == "split"
    assert d["child_ids"] == []


def test_cell_merge_records_absorbed_parent():
    tr = Tracker(_cfg())
    a = make_cell(12.0, 41.0, T0, area_km2=100.0)
    b = make_cell(12.15, 41.0, T0, area_km2=100.0)
    merged = make_cell(12.075, 41.0, T0 + STEP_MS, area_km2=180.0)
    tr.update([a, b], 0)
    tr.update([merged], 1)
    tr.complete_cycles()
    survivor = [t for t in tr.tracks if t.status == models.STATUS_ACTIVE][0]
    dead = [t for t in tr.tracks if t.status == models.STATUS_DEAD][0]
    assert survivor.parent_ids == [dead.track_id]
    assert survivor.lineage == "merge"
    assert dead.child_ids == [survivor.track_id]
    assert "lineage_merge" in survivor.events
    d = survivor.to_dict()
    assert d["parent_ids"] == [dead.track_id]
    assert d["lineage"] == "merge"
    assert d["child_ids"] == []


def test_cell_plain_continuation_has_no_parent():
    tr = Tracker(_cfg())
    tr.update([make_cell(12.0, 41.0, T0)], 0)
    tr.update([make_cell(12.05, 41.0, T0 + STEP_MS)], 1)
    tr.complete_cycles()
    track = tr.tracks[0]
    assert track.parent_ids == []
    assert track.child_ids == []
    assert track.lineage == "continuation"
    assert track.continued_from_history is False


# ---------------------------------------------------------------------------
# Layer storm object (stessa semantica, identita' principale del prodotto)
# ---------------------------------------------------------------------------
def test_storm_split_records_parent_and_child():
    tracker = st.StormObjectTracker(CONFIG["storm"])
    tracker.update([_storm(0, 12.0, 41.0, T0, 200.0)], 0)
    tracker.update([_storm(1, 11.98, 41.0, T0 + STEP_MS, 110.0),
                    _storm(2, 12.03, 41.0, T0 + STEP_MS, 110.0)], 1)
    parent = tracker.tracks[0]
    children = [t for t in tracker.tracks if t.track_id != parent.track_id]
    assert len(children) == 1
    child = children[0]
    assert child.parent_ids == [parent.track_id]
    assert child.lineage == "split"
    assert parent.child_ids == [child.track_id]


def test_storm_merge_records_absorbed_parent():
    tracker = st.StormObjectTracker(CONFIG["storm"])
    tracker.update([_storm(0, 12.0, 41.0, T0, 100.0),
                    _storm(1, 12.15, 41.0, T0, 100.0)], 0)
    tracker.update([_storm(2, 12.075, 41.0, T0 + STEP_MS, 180.0)], 1)
    survivor = [t for t in tracker.tracks if t.status == models.STATUS_ACTIVE][0]
    dead = [t for t in tracker.tracks if t.status == models.STATUS_DEAD][0]
    assert survivor.parent_ids == [dead.track_id]
    assert survivor.lineage == "merge"
    assert dead.child_ids == [survivor.track_id]
