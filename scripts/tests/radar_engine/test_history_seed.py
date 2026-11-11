#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed del tracker dall'archivio rolling (2026-11-11).

I test FALLISCONO sul codice precedente: `Tracker`/`StormObjectTracker` non
accettano `history_dir` e una track viva nel run precedente rinasceva con un ID
nuovo. Coprono i vincoli dichiarati: 20 km, 30 min, gate 110 km/h, niente
duplicati, nessun seed per lo stesso timestamp.
"""

import datetime as _dt
import math

from radar_engine import history
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


def _iso(ms):
    return _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _entry(uid, lon, lat, track_type="cell", area=120.0, cell_count=1):
    return {
        "uid": uid, "track_id": uid, "track_type": track_type,
        "status": models.STATUS_ACTIVE, "lonlat": [lon, lat],
        "area_km2": area, "max_dbz": 42.0, "mean_dbz": 38.0,
        "velocity_kmh": 30.0, "direction_toward_deg": 90.0,
        "cell_count": cell_count, "solidity": 0.9, "compactness": 3.0,
    }


def _slot(root, ts_ms, tracks):
    iso = _iso(ts_ms)
    history.merge_slot(root, {
        "slot": history.slot_name(iso), "radar_timestamp": iso,
        "radar_timestamp_ms": ts_ms, "tracks": list(tracks),
        "structures": [],
    })


def _storm(idx, lon, lat, ts):
    cells = [make_cell(lon, lat, ts, area_km2=120.0, frame_index=0,
                       cell_id="o{}-c0".format(idx))]
    return models.StormObject(
        storm_object_id="o{}".format(idx), timestamp_ms=ts,
        timestamp_iso=cells[0].timestamp_iso, frame_index=0,
        cells=cells, centroid_lonlat=(lon, lat), area_km2=120.0,
        equiv_radius_km=math.sqrt(120.0 / math.pi),
        bbox_lonlat=(lon, lat, lon, lat), convex_hull_lonlat=[],
        max_dbz=42.0, mean_dbz=38.0, p90_dbz=40.0, cell_density=1)


def test_seed_continues_cell_track(tmp_path):
    root = str(tmp_path / "history")
    _slot(root, T0, [_entry(7, 12.0, 41.0)])
    tr = Tracker(_cfg(), history_dir=root)
    tr.update([make_cell(12.05, 41.0, T0 + STEP_MS, cell_id="c1")], 0)
    tr.complete_cycles()
    tr.finalize()
    assert len(tr.tracks) == 1
    track = tr.tracks[0]
    assert len(track.points) == 2
    assert track.points[0].frame_index == -1
    assert track.points[0].cell_id == "history:7"
    assert track.continued_from_history is True
    assert track.history_uid == 7
    assert track.lineage == "continuation"
    assert track.birth_ms == T0
    assert "continued_from_history" in track.events
    assert models.EVENT_BIRTH not in track.events
    d = track.to_dict()
    assert d["continued_from_history"] is True
    assert d["history_uid"] == 7
    assert d["n_frames"] == 2


def test_seed_rejected_beyond_20km(tmp_path):
    root = str(tmp_path / "history")
    _slot(root, T0, [_entry(7, 12.0, 41.0)])
    tr = Tracker(_cfg(), history_dir=root)
    tr.update([make_cell(12.5, 41.0, T0 + STEP_MS, cell_id="c1")], 0)
    track = tr.tracks[0]
    assert len(track.points) == 1
    assert track.continued_from_history is False
    assert track.lineage is None


def test_seed_rejected_beyond_30min(tmp_path):
    root = str(tmp_path / "history")
    _slot(root, T0, [_entry(7, 12.0, 41.0)])
    tr = Tracker(_cfg(), history_dir=root)
    tr.update([make_cell(12.05, 41.0, T0 + 35 * 60000, cell_id="c1")], 0)
    assert len(tr.tracks[0].points) == 1
    assert tr.tracks[0].continued_from_history is False


def test_seed_rejected_above_speed_gate(tmp_path):
    root = str(tmp_path / "history")
    _slot(root, T0, [_entry(7, 12.0, 41.0)])
    tr = Tracker(_cfg(), history_dir=root)
    # 0.18 deg lon a 41N ~ 15.1 km in 5 min = ~181 km/h > 110.
    tr.update([make_cell(12.18, 41.0, T0 + STEP_MS, cell_id="c1")], 0)
    assert len(tr.tracks[0].points) == 1
    assert tr.tracks[0].continued_from_history is False


def test_seed_same_timestamp_ignored(tmp_path):
    root = str(tmp_path / "history")
    _slot(root, T0, [_entry(7, 12.0, 41.0)])
    tr = Tracker(_cfg(), history_dir=root)
    tr.update([make_cell(12.0, 41.0, T0, cell_id="c1")], 0)
    assert len(tr.tracks[0].points) == 1
    assert tr.tracks[0].continued_from_history is False


def test_seed_does_not_duplicate_tracks(tmp_path):
    root = str(tmp_path / "history")
    _slot(root, T0, [_entry(7, 12.0, 41.0)])
    tr = Tracker(_cfg(), history_dir=root)
    tr.update([make_cell(12.01, 41.0, T0 + STEP_MS, cell_id="c1"),
               make_cell(12.03, 41.0, T0 + STEP_MS, cell_id="c2")], 0)
    assert len(tr.tracks) == 2
    seeded = [t for t in tr.tracks if t.continued_from_history]
    born = [t for t in tr.tracks if not t.continued_from_history]
    assert len(seeded) == 1 and len(seeded[0].points) == 2
    assert len(born) == 1 and len(born[0].points) == 1
    assert len({t.track_id for t in tr.tracks}) == 2


def test_seed_storm_object_track(tmp_path):
    root = str(tmp_path / "history")
    _slot(root, T0, [_entry(11, 12.0, 41.0, track_type="storm_object",
                            area=240.0, cell_count=3)])
    tracker = st.StormObjectTracker(CONFIG["storm"], history_dir=root)
    tracker.update([_storm(0, 12.05, 41.0, T0 + STEP_MS)], 0)
    assert len(tracker.tracks) == 1
    track = tracker.tracks[0]
    assert len(track.points) == 2
    assert track.points[0].frame_index == -1
    assert track.continued_from_history is True
    assert track.history_uid == 11
    assert track.lineage == "continuation"
