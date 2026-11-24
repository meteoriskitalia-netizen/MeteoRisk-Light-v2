#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evoluzione dei sistemi: nascite, morti, crescita, fusioni (2026-11-24).

Copre i difetti misurati sul payload reale:
- `tracking_events.birth` era valorizzato su OGNI track (22/22) perche'
  bastava avere un punto; le nascite reali erano 11/22.
- `birth_kind`/`death_kind`/`growth` non esistevano.
- Una track nata sull'ultimo frame (1 punto) era invisibile per il filtro
  `len(points)>=2`: ora e' in `births_latest_frame`.
- `derive_lifecycle` etichettava WEAKENING un sistema con area +83,9%.
"""

import pytest

from radar_engine import models, output, tracking as trk
from radar_engine.config import CONFIG
from radar_engine.tracking import Tracker
from conftest import make_cell

T0 = 1_777_027_200_000
STEP = 300_000


def _cfg(**over):
    cfg = dict(CONFIG["tracking"])
    cfg.update(over)
    return cfg


def _track(points, events=(), lineage=None, parent_ids=None, child_ids=None,
           track_id=1, status=models.STATUS_ACTIVE, death_kind=None,
           continued=False):
    track = models.Track(track_id, points[0].timestamp_ms)
    for i, cell in enumerate(points):
        track.points.append(models.TrackPoint(track_id, i, cell))
    track.events = list(events)
    track.lineage = lineage
    track.parent_ids = list(parent_ids or [])
    track.child_ids = list(child_ids or [])
    track.status = status
    track.death_kind = death_kind
    track.continued_from_history = continued
    return track


# ---------------------------------------------------------------------------
# 1) Fix del blocco eventi: punto != nascita
# ---------------------------------------------------------------------------
def test_tracking_events_birth_only_with_explicit_event():
    c = make_cell(12.0, 41.0, T0)
    no_event = _track([c], events=[])
    born = _track([c], events=[models.EVENT_BIRTH])
    seeded = _track([c], events=["continued_from_history"], continued=True)
    for track, expected_present, expected_reason in (
            (no_event, False, "not_born_in_this_run"),
            (seeded, False, "not_born_in_this_run"),
            (born, True, None)):
        block = output.build_tracking_events(track, "2026-05-03T08:00:00Z")
        birth = next(e for e in block["events"] if e["type"] == "birth")
        assert (birth["value"] is not None) is expected_present
        assert birth["null_reason"] == expected_reason


def test_tracking_events_death_for_admin_close():
    """Una track chiusa a fine run (status dead, senza EVENT_DEATH) non e'
    'still active': il blocco eventi deve riportare la morte e il motivo."""
    track = _track([make_cell(12.0, 41.0, T0)], events=[models.EVENT_BIRTH])
    track.status = models.STATUS_DEAD
    track.death_ms = T0 + STEP
    track.death_kind = models.DEATH_KIND_LOST
    block = output.build_tracking_events(track, "2026-05-03T08:00:00Z")
    death = next(e for e in block["events"] if e["type"] == "death")
    assert death["value"]["death_ms"] == T0 + STEP
    assert death["value"]["death_kind"] == models.DEATH_KIND_LOST
    assert death["null_reason"] is None


# ---------------------------------------------------------------------------
# 2) birth_kind: 5 casi + seed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("events,lineage,continued,expected,physical", [
    ([models.EVENT_BIRTH], None, False,
     models.BIRTH_KIND_FIRST_OBSERVATION, True),
    ([models.EVENT_BIRTH, models.EVENT_BIRTH_AFTER_GATE_REJECT], None, False,
     models.BIRTH_KIND_AFTER_REJECT, True),
    ([models.EVENT_BIRTH, "lineage_split"], "split", False,
     models.BIRTH_KIND_SPLIT_CHILD, True),
    ([models.EVENT_BIRTH, "lineage_merge"], "merge", False,
     models.BIRTH_KIND_MERGE_RESULT, True),
    ([models.EVENT_BIRTH, trk.EVENT_REID_CONTINUATION], "continuation", False,
     models.BIRTH_KIND_REID_CONTINUATION, False),
    (["continued_from_history"], "continuation", True, None, None),
])
def test_birth_kind_cases(events, lineage, continued, expected, physical):
    track = _track([make_cell(12.0, 41.0, T0)], events=events,
                   lineage=lineage, continued=continued)
    trk.finalize_lifecycle_fields(track)
    assert track.birth_kind == expected
    assert track.physical_birth == physical
    if expected is None:
        assert track.birth_null_reason == "continuation_from_history"


def test_birth_kind_lineage_event_on_parent_is_not_child():
    """`lineage_split`/`lineage_merge` sono su entrambi i lati: il genitore
    non e' un figlio. Solo chi ha parent_ids e' classificato come child."""
    parent = _track([make_cell(12.0, 41.0, T0)],
                    events=[models.EVENT_BIRTH, "lineage_split"],
                    lineage="continuation", child_ids=[2])
    child = _track([make_cell(12.0, 41.0, T0)],
                   events=[models.EVENT_BIRTH, "lineage_split"],
                   lineage="split", parent_ids=[1])
    assert trk.compute_birth_kind(parent)[0] == \
        models.BIRTH_KIND_FIRST_OBSERVATION
    assert trk.compute_birth_kind(child)[0] == models.BIRTH_KIND_SPLIT_CHILD
    # Stessa guardia per merge: il genitore assorbito non e' merge_result.
    absorbed = _track([make_cell(12.0, 41.0, T0)],
                      events=[models.EVENT_BIRTH, "lineage_merge"],
                      lineage="continuation", child_ids=[3])
    assert trk.compute_birth_kind(absorbed)[0] == \
        models.BIRTH_KIND_FIRST_OBSERVATION


def test_birth_kind_after_reject_from_real_gate():
    """Colonna nuova con l'unico candidato rifiutato dal gate 110 km/h."""
    tracker = Tracker(_cfg())
    tracker.update([make_cell(12.0, 41.0, T0)], 0)
    # ~253 km in 5 min: il gate rifiuta la coppia, la cella rinasce nuova.
    tracker.update([make_cell(15.0, 41.0, T0 + STEP)], 1)
    tracker.finalize()
    born = [t for t in tracker.tracks if models.EVENT_BIRTH in t.events]
    assert len(born) == 2
    after = [t for t in born if t.birth_kind == models.BIRTH_KIND_AFTER_REJECT]
    assert len(after) == 1
    assert models.EVENT_BIRTH_AFTER_GATE_REJECT in after[0].events
    assert after[0].physical_birth is True
    # La vecchia track muore perche' l'unico candidato era fuori gate.
    dead = [t for t in tracker.tracks if t.status == models.STATUS_DEAD]
    assert dead and dead[0].death_kind == models.DEATH_KIND_LOST


# ---------------------------------------------------------------------------
# 3) death_kind: 4 casi, misurati con il tracker reale
# ---------------------------------------------------------------------------
def test_death_kind_merged_into():
    tracker = Tracker(_cfg())
    a = make_cell(12.0, 41.0, T0, area_km2=100.0)
    b = make_cell(12.15, 41.0, T0, area_km2=100.0)
    merged = make_cell(12.075, 41.0, T0 + STEP, area_km2=180.0)
    tracker.update([a, b], 0)
    tracker.update([merged], 1)
    tracker.update([], 2)                 # il survivor muore DOPO il merge
    tracker.complete_cycles()
    tracker.finalize()
    dead = [t for t in tracker.tracks if t.status == models.STATUS_DEAD]
    absorbed = [t for t in dead if t.child_ids]
    survivor = [t for t in dead if t.lineage == "merge"]
    assert absorbed and absorbed[0].death_kind == models.DEATH_KIND_MERGED_INTO
    assert survivor and survivor[0].death_kind == models.DEATH_KIND_LOST
    assert survivor[0].death_kind != models.DEATH_KIND_MERGED_INTO


def test_death_kind_split_parent_is_not_merged_into():
    """Un genitore che si divide ha child_ids, ma NON e' un merge."""
    tracker = Tracker(_cfg())
    tracker.update([make_cell(12.0, 41.0, T0, area_km2=200.0)], 0)
    tracker.update([make_cell(11.98, 41.0, T0 + STEP, area_km2=110.0),
                    make_cell(12.03, 41.0, T0 + STEP, area_km2=110.0)], 1)
    tracker.update([], 2)
    tracker.finalize()
    parent = tracker.tracks[0]
    assert parent.child_ids                       # il figlio dello split
    assert parent.death_kind == models.DEATH_KIND_LOST
    assert parent.death_kind != models.DEATH_KIND_MERGED_INTO


def test_death_kind_coasting_expired():
    tracker = Tracker(_cfg(max_coast_frames=1))
    tracker.update([make_cell(12.0, 41.0, T0)], 0)
    tracker.update([], 1)                 # coasting (1)
    tracker.update([], 2)                 # grace esaurito -> morte
    tracker.finalize()
    track = tracker.tracks[0]
    assert track.status == models.STATUS_DEAD
    assert track.death_kind == models.DEATH_KIND_COASTING_EXPIRED


def test_death_kind_lost_on_empty_frame():
    tracker = Tracker(_cfg())
    tracker.update([make_cell(12.0, 41.0, T0)], 0)
    tracker.update([], 1)                 # frame senza celle: non e' prova fisica
    tracker.finalize()
    assert tracker.tracks[0].death_kind == models.DEATH_KIND_LOST


def test_death_kind_dissipated_when_cells_exist_but_no_match():
    # Costo massimo bassissimo e cella lontana ma sotto il gate 110 km/h:
    # nessun gated pair e nessuna sovrapposizione (niente merge). L'eco non
    # e' piu' associabile -> dissipated, non "lost".
    tracker = Tracker(_cfg(max_assignment_cost=1.0))
    tracker.update([make_cell(12.0, 41.0, T0)], 0)
    tracker.update([make_cell(12.36, 41.0, T0 + 6 * STEP)], 6)  # ~30 km/30'
    tracker.finalize()
    dead = [t for t in tracker.tracks if t.status == models.STATUS_DEAD]
    assert dead and dead[0].death_kind == models.DEATH_KIND_DISSIPATED


# ---------------------------------------------------------------------------
# 4) growth: pendenza sugli ultimi 3 osservati, non cumulato
# ---------------------------------------------------------------------------
def test_growth_three_observed_points():
    cells = [
        make_cell(12.0, 41.0, T0, area_km2=100.0, max_dbz=40.0, mean_dbz=35.0),
        make_cell(12.1, 41.0, T0 + STEP, area_km2=150.0, max_dbz=45.0,
                  mean_dbz=38.0),
        make_cell(12.2, 41.0, T0 + 2 * STEP, area_km2=200.0, max_dbz=50.0,
                  mean_dbz=41.0),
    ]
    track = _track(cells, events=[models.EVENT_BIRTH])
    growth = trk.compute_growth(track)
    assert growth["null_reason"] is None
    assert growth["window_points"] == 3
    assert growth["window_min"] == 10.0
    assert growth["area_delta_km2"] == 100.0
    assert growth["area_km2_per_min"] == 10.0
    assert growth["max_dbz_delta"] == 10.0
    assert growth["max_dbz_per_min"] == 1.0
    assert growth["mean_dbz_per_min"] == 0.6
    assert growth["vil_density"] is None
    assert growth["vil_density_null_reason"] == "vil_not_available_per_point"


def test_growth_two_points_is_insufficient_and_coasted_excluded():
    c0 = make_cell(12.0, 41.0, T0, area_km2=100.0)
    c1 = make_cell(12.1, 41.0, T0 + STEP, area_km2=150.0)
    two = _track([c0, c1], events=[models.EVENT_BIRTH])
    growth = trk.compute_growth(two)
    assert growth["null_reason"] == "insufficient_points"
    assert growth["window_points"] == 2
    assert growth["area_km2_per_min"] is None
    assert growth["area_delta_km2"] is None

    # Un terzo punto COASTED non conta: restano 2 osservati.
    c2 = make_cell(12.9, 41.0, T0 + 2 * STEP, area_km2=999.0, max_dbz=80.0)
    pt = models.TrackPoint(two.track_id, 2, c2)
    pt.position_source, pt.observed = "coasted", False
    two.points.append(pt)
    growth = trk.compute_growth(two)
    assert growth["window_points"] == 2
    assert growth["null_reason"] == "insufficient_points"


# ---------------------------------------------------------------------------
# 5) lifecycle_stats: merge/split come RELAZIONI, non track
# ---------------------------------------------------------------------------
def _two_point_track(track_id, events, lineage=None, parent_ids=None,
                     status=models.STATUS_ACTIVE, death_kind=None,
                     continued=False):
    cells = [make_cell(12.0 + 0.1 * i, 41.0, T0 + i * STEP)
             for i in range(2)]
    return _track(cells, events=events, lineage=lineage,
                  parent_ids=parent_ids, track_id=track_id, status=status,
                  death_kind=death_kind, continued=continued)


def test_lifecycle_stats_relations_not_tracks():
    tracks = [
        _two_point_track(1, [models.EVENT_BIRTH, "lineage_merge"],
                         lineage="merge", parent_ids=[10, 11]),
        _two_point_track(2, [models.EVENT_BIRTH, "lineage_split"],
                         lineage="split", parent_ids=[12]),
        _two_point_track(3, [models.EVENT_BIRTH, models.EVENT_DEATH],
                         status=models.STATUS_DEAD,
                         death_kind=models.DEATH_KIND_DISSIPATED),
        _two_point_track(4, ["continued_from_history"],
                         lineage="continuation", continued=True),
    ]
    stats = output.lifecycle_stats(tracks, "cell")
    assert stats["births"][models.BIRTH_KIND_MERGE_RESULT] == 1
    assert stats["births"][models.BIRTH_KIND_SPLIT_CHILD] == 1
    assert stats["births"][models.BIRTH_KIND_FIRST_OBSERVATION] == 1
    assert stats["physical_births_total"] == 3
    assert stats["deaths"][models.DEATH_KIND_DISSIPATED] == 1
    # merge a 2 genitori = 2 relazioni; split su 1 genitore = 1 relazione.
    assert stats["merges"] == 2
    assert stats["splits"] == 1
    assert stats["lifetime_truncated_by_history"] == 1
    assert stats["counted_tracks"] == 4


# ---------------------------------------------------------------------------
# 6) births_latest_frame: una nascita da 1 punto e' visibile
# ---------------------------------------------------------------------------
def test_births_latest_frame_includes_single_point_birth():
    c = make_cell(12.0, 41.0, T0 + STEP, area_km2=80.0, max_dbz=44.0)
    born = models.Track(7, c.timestamp_ms)
    born.points.append(models.TrackPoint(7, 1, c))
    born.events.append(models.EVENT_BIRTH)
    # seed: stesso frame ma continua un'identita' precedente -> escluso
    seed = models.Track(8, c.timestamp_ms)
    seed.points.append(models.TrackPoint(8, 1, c))
    seed.events.append("continued_from_history")
    seed.continued_from_history = True
    # nato al frame 0: non e' una nascita dell'ultimo frame
    old = _track([make_cell(12.0, 41.0, T0), c], events=[models.EVENT_BIRTH],
                 track_id=9)
    old.points[0].frame_index = 0
    old.points[1].frame_index = 1

    bundle = models.EngineBundle("ok", "2026-05-03T08:00:00Z", output.SOURCE_LABEL)
    bundle.cells_by_frame = [[], []]
    bundle.tracks = [born, seed, old]
    trk.finalize_lifecycle_fields(born)
    out = output._births_latest_frame(bundle)
    assert len(out) == 1
    rec = out[0]
    assert rec["track_id"] == 7 and rec["track_type"] == "cell"
    assert rec["position"] == [41.0, 12.0]       # [lat, lon]
    assert rec["area_km2"] == 80.0 and rec["max_dbz"] == 44.0
    assert rec["birth_kind"] == models.BIRTH_KIND_FIRST_OBSERVATION


def test_latest_contains_lifecycle_stats_and_births_latest_frame():
    tracker = Tracker(_cfg())
    tracker.update([make_cell(12.0, 41.0, T0)], 0)
    tracker.update([make_cell(12.1, 41.0, T0 + STEP)], 1)
    tracker.complete_cycles()
    bundle = models.EngineBundle("ok", "2026-05-03T08:00:00Z",
                                 output.SOURCE_LABEL)
    bundle.radar_timestamp_iso = "2026-05-03T08:00:00Z"
    bundle.cells_by_frame = [[make_cell(12.0, 41.0, T0)],
                             [make_cell(12.1, 41.0, T0 + STEP)]]
    bundle.tracks = tracker.finalize()
    latest = output.build_latest(bundle)
    assert latest["lifecycle_stats"]["births"][
        models.BIRTH_KIND_FIRST_OBSERVATION] == 1
    assert latest["lifecycle_stats"]["median_lifetime_min"] == 5.0
    assert isinstance(latest["births_latest_frame"], list)
    for track in bundle.tracks:
        assert track.growth is not None
