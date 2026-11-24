#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Salti del tracker: sovrapposizione dei dischi e re-ID dentro il run.

Regole verificate (2026-11-24), tutte su celle SINTETICHE dichiarate:
  (A) un'associazione che il gate ~110 km/h rifiuterebbe ma con dischi
      sovrapposti almeno per `_LINEAGE_IOU` viene TENUTA: l'ID non cambia,
      il passo non entra nel fit del moto, il vettore pubblicato resta nullo
      con motivo `centroid_jump_overlapped`;
  (B) sotto la soglia di sovrapposizione il gate resta INVARIATO: nessuna
      associazione, traccia nuova;
  (C) un re-ID dentro il run (traccia morta + traccia nata, entro
      `_REID_RADIUS_KM` / `_REID_WINDOW_MIN`, velocita' implicita <= 110 km/h)
      viene cucito: stesso `track_id`, `lineage = "continuation"`, evento
      esplicito;
  (D) oltre 20 km o oltre 15 min NON viene cucito;
  (E) nessuna fusione fra tracce VIVE, e nessuna fusione fra corpi
      incompatibili per area/intensita'.

Nessun dato radar reale: la verifica su dati archiviati e' in
`scripts/validation/tracker_jumps_replay.py`.
"""

import math

from radar_engine import models
from radar_engine import tracking as trk
from radar_engine.config import CONFIG

from conftest import make_cell

T0 = 1_777_027_200_000
STEP = 300_000                       # 5 minuti
LAT = 45.0
KM_PER_DEG_LON = 111.32 * math.cos(math.radians(LAT))   # ~78,71 km/grado


def _deg(km):
    """Gradi di longitudine per `km` alla latitudine 45N."""
    return km / KM_PER_DEG_LON


def _cell(i, lon, area=120.0, dbz=40.0, cid=None):
    return make_cell(lon, LAT, T0 + i * STEP, area_km2=area, max_dbz=dbz,
                     mean_dbz=dbz - 5.0, p90_dbz=dbz - 2.0,
                     frame_index=i, cell_id=cid or "c{}".format(i))


def _tracker(cfg_over=None):
    cfg = dict(CONFIG["tracking"])
    cfg.update(cfg_over or {})
    # coasting OFF (max_coast_frames del layer = 0): i test misurano il
    # matching e la cucitura, non la sopravvivenza ai buchi di frame.
    return trk.Tracker(cfg)


def _km(a, b):
    return trk.haversine_km(a, b)


# ---------------------------------------------------------------------------
# (A) sovrapposizione >= _LINEAGE_IOU: associazione tenuta, vettore nullo
# ---------------------------------------------------------------------------
def test_overlap_keeps_association_and_nulls_the_vector():
    t = _tracker()
    prev = _cell(0, 12.0, area=2000.0)
    curr = _cell(1, 12.0 + _deg(25.0), area=2000.0, cid="jump")
    iou = trk._circle_iou(prev.centroid_lonlat, prev.area_km2,
                          curr.centroid_lonlat, curr.area_km2)
    speed = _km(prev.centroid_lonlat, curr.centroid_lonlat) / (STEP / 3600000.0)
    # Il caso che il gate da solo rifiuterebbe: sopra 110 km/h ma dischi
    # sovrapposti.
    assert iou >= trk._LINEAGE_IOU, iou
    assert speed > 110.0, speed

    t.update([prev], 0)
    t.update([curr], 1)

    assert len(t.tracks) == 1                     # nessuna traccia nuova
    track = t.tracks[0]
    assert track.track_id == 1                    # ID invariato
    assert [p.cell_id for p in track.points] == [prev.cell_id, curr.cell_id]
    assert track.points[-1].centroid_jump is True
    assert t.kept_by_overlap == 1                 # tenuta SOLO per IoU
    assert t.continuity_rejections == 0           # non e' un rigetto

    t.complete_cycles()
    t.finalize()
    motion = track.motion
    assert motion["motion_quality"] == "unreliable"
    assert motion["motion_null_reason"] == \
        trk.MOTION_NULL_CENTROID_JUMP_OVERLAPPED
    assert motion["velocity_kmh"] is None
    assert motion["direction_toward_deg"] is None
    assert motion["direction_from_deg"] is None
    assert motion["centroid_jump_points"] == 1    # audit: fuori dal fit

    m = trk.tracking_metrics([track], "cell")
    # Un passo dichiarato `centroid_jump` NON e' un ID switch: l'ID non e'
    # cambiato e i dischi si sovrappongono. Resta contato a parte.
    assert m["id_switch_jumps"] == 0
    assert m["centroid_jump_segments"] == 1


def test_overlap_kept_association_is_published_as_counter():
    t = _tracker()
    t.update([_cell(0, 12.0, area=2000.0)], 0)
    t.update([_cell(1, 12.0 + _deg(25.0), area=2000.0, cid="jump")], 1)
    t.complete_cycles()
    tracks = t.finalize()
    out = trk.build_tracking_metrics(
        tracks, [], cell_kept_by_overlap=t.kept_by_overlap,
        cell_reids_stitched=t.reids_stitched)
    assert out["kept_by_overlap"] == 1
    assert out["cell"]["kept_by_overlap"] == 1
    assert out["reids_stitched"] == 0


# ---------------------------------------------------------------------------
# (B) sotto la soglia: gate invariato, traccia nuova
# ---------------------------------------------------------------------------
def test_no_overlap_keeps_the_gate_unchanged():
    t = _tracker()
    prev = _cell(0, 12.0, area=100.0)
    curr = _cell(1, 12.0 + _deg(25.0), area=100.0, cid="far")
    assert trk._circle_iou(prev.centroid_lonlat, prev.area_km2,
                           curr.centroid_lonlat, curr.area_km2) == 0.0

    t.update([prev], 0)
    t.update([curr], 1)

    assert len(t.tracks) == 2
    assert [tr.track_id for tr in t.tracks] == [1, 2]
    assert t.kept_by_overlap == 0
    assert t.continuity_rejections == 1
    assert t.tracks[1].points[0].cell_id == "far"
    assert getattr(t.tracks[1].points[0], "centroid_jump", False) is False


# ---------------------------------------------------------------------------
# (C) re-ID dentro il run: cucito con lineage continuation
# ---------------------------------------------------------------------------
def test_reid_inside_run_is_stitched_with_continuation():
    t = _tracker()
    t.update([_cell(0, 12.0)], 0)
    t.update([], 1)                              # morte senza celle
    assert len(t.tracks) == 1
    assert t.tracks[0].status == models.STATUS_DEAD

    t.update([_cell(2, 12.0 + _deg(3.9), cid="born")], 2)

    assert len(t.tracks) == 1                    # la traccia nata non resta
    track = t.tracks[0]
    assert track.track_id == 1                   # stesso ID
    assert track.status == models.STATUS_ACTIVE
    assert track.points[-1].cell_id == "born"
    assert track.points[-1].track_id == 1
    assert track.lineage == "continuation"
    assert trk.EVENT_REID_CONTINUATION in track.events
    assert t.reids_stitched == 1
    assert t.reid_stitches[0]["child_was_newborn"] is True
    assert t.reid_stitches[0]["parent_was_dead"] is True

    t.complete_cycles()
    tracks = t.finalize()
    m = trk.tracking_metrics(tracks, "cell")
    assert m["id_switch_reids"] == 0             # il re-ID non esiste piu'
    assert m["id_switch_jumps"] == 0


def test_reid_is_greedy_one_to_one():
    """Due tracce morte e una sola nata: una sola cucitura, la piu' vicina."""
    t = _tracker()
    near = _cell(0, 12.0, cid="near")
    far = _cell(0, 12.0 + _deg(8.0), cid="far")
    t.update([near, far], 0)    # due tracce, stesso frame
    t.update([], 1)             # entrambe morte
    assert len(t.tracks) == 2
    t.update([_cell(2, 12.0 + _deg(2.0), cid="newborn")], 2)
    assert t.reids_stitched == 1
    # Il parent cucito sopravvive, l'altra traccia resta morta: nessuna fusione.
    assert len(t.tracks) == 2
    active = [tr for tr in t.tracks if tr.status == models.STATUS_ACTIVE]
    assert len(active) == 1
    assert active[0].track_id == 1
    assert active[0].points[-1].cell_id == "newborn"
    assert t.reid_stitches[0]["parent_id"] == 1          # la piu' vicina


# ---------------------------------------------------------------------------
# (D) fuori finestra: oltre 20 km / oltre 15 min nessuna cucitura
# ---------------------------------------------------------------------------
def test_reid_not_applied_beyond_radius():
    t = _tracker()
    t.update([_cell(0, 12.0)], 0)
    t.update([], 1)
    t.update([], 2)
    t.update([_cell(3, 12.0 + _deg(23.6), cid="too_far")], 3)   # 15 min, 94 km/h

    assert len(t.tracks) == 2
    assert t.reids_stitched == 0
    assert t.tracks[-1].points[0].cell_id == "too_far"


def test_reid_not_applied_beyond_window():
    t = _tracker()
    t.update([_cell(0, 12.0)], 0)
    for i in (1, 2, 3):
        t.update([], i)
    t.update([_cell(4, 12.0 + _deg(3.9), cid="too_late")], 4)   # 20 min

    assert len(t.tracks) == 2
    assert t.reids_stitched == 0


# ---------------------------------------------------------------------------
# (E) fusioni vietate
# ---------------------------------------------------------------------------
def test_reid_never_merges_two_live_tracks():
    """Due tracce VIVE non diventano una: solo un parent MORTO e' accoppiabile."""
    alive = models.Track(1, T0)
    alive.points.append(models.TrackPoint(1, 0, _cell(0, 12.0, cid="alive")))
    other = models.Track(2, T0)
    other.points.append(models.TrackPoint(2, 0, _cell(0, 12.4, cid="other")))
    born = models.Track(9, T0 + STEP)
    born.points.append(models.TrackPoint(9, 1, _cell(1, 12.02, cid="born")))
    assert alive.status == models.STATUS_ACTIVE
    assert other.status == models.STATUS_ACTIVE

    t = _tracker()
    t.tracks = [alive, other, born]
    stitched = t._stitch_reids_in_run([born])

    assert stitched == {}
    assert t.reids_stitched == 0
    assert len(t.tracks) == 3
    assert alive.status == models.STATUS_ACTIVE
    assert [p.cell_id for p in alive.points] == ["alive"]
    assert [p.cell_id for p in other.points] == ["other"]


def test_reid_refuses_incompatible_area_and_intensity():
    """Una cella di 87 km2 non diventa il seguito di un corpo di 2081 km2."""
    t = _tracker()
    t.update([_cell(0, 12.0, area=87.0, dbz=45.5, cid="small")], 0)
    t.update([], 1)
    t.update([_cell(2, 12.0 + _deg(15.0), area=2081.0, dbz=61.0,
                    cid="monster")], 2)

    assert len(t.tracks) == 2          # nessuna fusione
    assert t.reids_stitched == 0

    # Controllo di non-regressione: a parita' di distanza/ritardo, due celle
    # dello STESSO ordine di grandezza si cuciono.
    t2 = _tracker()
    t2.update([_cell(0, 12.0, area=87.0, dbz=45.5, cid="small")], 0)
    t2.update([], 1)
    t2.update([_cell(2, 12.0 + _deg(15.0), area=110.0, dbz=44.0,
                     cid="similar")], 2)
    assert t2.reids_stitched == 1
    assert len(t2.tracks) == 1


def test_reid_is_not_applied_over_an_existing_lineage_link():
    """Un legame di lineage gia' registrato non viene duplicato."""
    parent = models.Track(1, T0)
    parent.points.append(models.TrackPoint(1, 0, _cell(0, 12.0, cid="p")))
    parent.status = models.STATUS_DEAD
    parent.child_ids = [7]                       # gia' legato a un figlio
    born = models.Track(9, T0 + STEP)
    born.points.append(models.TrackPoint(9, 1, _cell(1, 12.02, cid="born")))

    t = _tracker()
    t.tracks = [parent, born]
    assert t._stitch_reids_in_run([born]) == {}
    assert len(t.tracks) == 2
    assert parent.child_ids == [7]
    assert [p.cell_id for p in parent.points] == ["p"]
