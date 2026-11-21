#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blocco ADDITIVO `tracking_events/1` nei record pubblicati.

Questi test FALLISCONO sul pristino: `build_tracking_events` non esiste e i
record di tracks.json / storm_tracks.json non contengono `tracking_events`.
Nessun evento viene inventato: l'assenza di evidenza produce value=null con
null_reason esplicito e timestamp = radar_timestamp del run.
"""
import math
import json

from radar_engine import models, output, tracking as trk
from radar_engine import storm_tracking as st
from radar_engine.config import CONFIG
from conftest import make_cell

T0 = 1_777_027_200_000
STEP = 300_000
RADAR_TS = "2026-05-03T08:00:00Z"
KINDS = {"birth", "continuation", "death", "temporary_non_observation",
         "split", "merge", "ambiguous_association"}


def _track(events=(), lineage=None, parent_ids=None, child_ids=None,
           ambiguity=None, points=True):
    t = models.Track(1, T0)
    t.events = list(events)
    t.lineage = lineage
    t.parent_ids = list(parent_ids or [])
    t.child_ids = list(child_ids or [])
    t.tracking_ambiguity = ambiguity
    if points:
        c = make_cell(12.0, 41.0, T0)
        t.points.append(models.TrackPoint(1, 0, c))
    return t


def test_block_schema_and_all_seven_event_types():
    b = output.build_tracking_events(_track(), RADAR_TS)
    assert b["schema"] == "meteorisk.tracking_events/1"
    kinds = [e["type"] for e in b["events"]]
    assert set(kinds) == KINDS and len(kinds) == 7
    for e in b["events"]:
        for key in ("type", "timestamp", "value", "null_reason",
                    "source", "reference"):
            assert key in e, (e["type"], key)


def test_absent_events_are_null_with_reason_and_run_timestamp():
    b = output.build_tracking_events(_track(), RADAR_TS)
    for e in b["events"]:
        if e["value"] is None:
            assert e["null_reason"]
            # timestamp di VALUTAZIONE, non un orario inventato
            assert e["timestamp"] == RADAR_TS


def test_birth_evidence_from_real_track_point():
    b = output.build_tracking_events(_track(events=[models.EVENT_BIRTH]), RADAR_TS)
    birth = next(e for e in b["events"] if e["type"] == "birth")
    assert birth["null_reason"] is None
    assert birth["value"]["track_id"] == 1
    assert birth["timestamp"] != RADAR_TS     # timestamp del punto reale


def test_death_null_when_track_still_active():
    b = output.build_tracking_events(_track(), RADAR_TS)
    death = next(e for e in b["events"] if e["type"] == "death")
    assert death["value"] is None
    assert death["null_reason"] == "no_death_observed_track_still_active"


def test_death_evidence_when_recorded():
    t = _track(events=[models.EVENT_DEATH])
    t.death_ms = T0 + STEP
    b = output.build_tracking_events(t, RADAR_TS)
    death = next(e for e in b["events"] if e["type"] == "death")
    assert death["value"]["death_ms"] == T0 + STEP
    assert death["null_reason"] is None


def test_split_merge_and_continuation_evidence():
    t = _track(events=["lineage_split", "lineage_merge"],
               lineage="split", parent_ids=[7], child_ids=[9])
    b = output.build_tracking_events(t, RADAR_TS)
    split = next(e for e in b["events"] if e["type"] == "split")
    merge = next(e for e in b["events"] if e["type"] == "merge")
    assert split["value"]["child_ids"] == [9]
    assert merge["value"]["parent_ids"] == [7]
    c = _track(events=["continued_from_history"], lineage="continuation")
    c.continued_from_history = True
    bc = output.build_tracking_events(c, RADAR_TS)
    cont = next(e for e in bc["events"] if e["type"] == "continuation")
    assert cont["value"]["continued_from_history"] is True


def test_ambiguous_association_evidence_and_null():
    b0 = output.build_tracking_events(_track(), RADAR_TS)
    amb0 = next(e for e in b0["events"] if e["type"] == "ambiguous_association")
    assert amb0["value"] is None and amb0["null_reason"]
    t = _track(events=[models.EVENT_AMBIGUOUS], ambiguity="high")
    b1 = output.build_tracking_events(t, RADAR_TS)
    amb1 = next(e for e in b1["events"] if e["type"] == "ambiguous_association")
    assert amb1["value"]["tracking_ambiguity"] == "high"


def test_temporary_non_observation_from_coasted_point():
    t = _track()
    c = make_cell(12.0, 41.0, T0 + STEP)
    p = models.TrackPoint(1, 1, c)
    p.position_source, p.observed = "coasted", False
    t.points.append(p)
    b = output.build_tracking_events(t, RADAR_TS)
    ev = next(e for e in b["events"] if e["type"] == "temporary_non_observation")
    assert ev["value"]["coasted_points"] == 1
    assert ev["value"]["observed"] is False
    assert ev["value"]["position_source"] == "coasted"


def test_published_tracks_json_contains_block_without_touching_existing_fields():
    # Confronto con il record pristino: solo la chiave `tracking_events` e'
    # aggiunta, tutti gli altri valori restano identici.
    b = models.EngineBundle("ok", RADAR_TS, output.SOURCE_LABEL)
    b.radar_timestamp_iso = RADAR_TS
    b.radar_timestamp_ms = T0
    b.cells_by_frame = [[]]
    t = _track()
    t.points.append(models.TrackPoint(1, 1, make_cell(12.05, 41.0, T0 + STEP)))
    b.tracks = [t]
    payload = output.build_tracks(b)
    rec = payload["tracks"][0]
    assert rec["tracking_events"]["schema"] == "meteorisk.tracking_events/1"
    pristine = t.to_dict()
    for key, value in pristine.items():
        assert rec[key] == value, key
    assert set(rec.keys()) - set(pristine.keys()) == {"tracking_events"}


def test_published_storm_tracks_json_contains_block():
    b = models.EngineBundle("ok", RADAR_TS, output.SOURCE_LABEL)
    b.radar_timestamp_iso = RADAR_TS
    b.radar_timestamp_ms = T0
    cells = [make_cell(12.0, 41.0, T0)]
    o = models.StormObject("o1", T0, RADAR_TS, 0, cells, (12.0, 41.0),
                           100.0, 5.0, (12.0, 41.0, 12.0, 41.0), [],
                           45.0, 40.0, 42.0, 1)
    s = st.StormObjectTracker(CONFIG["storm"])
    s.update([o], 0, frame_ts_ms=T0, frame_context={"valid": True, "coverage_ok": True})
    b.storm_tracks = s.tracks[:1]
    payload = output.build_storm_tracks(b)
    if payload["tracks"]:
        rec = payload["tracks"][0]
        assert rec["tracking_events"]["schema"] == "meteorisk.tracking_events/1"
