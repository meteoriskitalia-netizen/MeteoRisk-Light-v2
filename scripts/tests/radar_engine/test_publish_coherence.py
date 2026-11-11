#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del filtro di PUBBLICAZIONE per track non coerente (2026-11-11).

Contratto: `convective_structures.json` pubblica SOLO le strutture con
`track_coherent=True` (almeno 4 punti e nessun segmento oltre 110 km/h). Le
altre restano in `bundle.convective_structures` e quindi in
`storm_tracks.json`/`tracks.json` per l'audit, e vengono CONTATE in
`summary.skipped_incoherent` / `latest.json.skipped_incoherent`.

Il caso richiesto: track salterino da 2 punti distanti ~500 km. Sul codice
precedente la struttura veniva pubblicata (nessun filtro, nessun conteggio):
questi test falliscono.
"""

import json
import math

from radar_engine import convective_structure as cs
from radar_engine import models
from radar_engine import output
from radar_engine.config import CONFIG

from conftest import make_cell

T0 = 1_777_027_200_000
STEP_MS = 300_000  # 5 min VMI


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
    from radar_engine.storm_tracking import haversine_km
    dist = sum(haversine_km(a.lonlat, b.lonlat)
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


def _bundle(tracks, frames=5):
    b = models.EngineBundle("ok", "2026-11-10T22:55:36Z", output.SOURCE_LABEL)
    b.radar_timestamp_iso = "2026-11-10T22:55:36Z"
    b.cells_by_frame = [[] for _ in range(frames)]
    b.tracks = []
    b.storm_tracks = list(tracks)
    return b


def test_incoherent_track_not_published_and_counted():
    """2 punti a ~500 km: SOI >=65 ma track_coherent=False -> NON pubblicata.

    Il record resta nel bundle (audit), il payload pubblicato ha
    `structures: []` e `skipped_incoherent: 1`; latest.json conta le stesse
    due grandezze. Sul codice precedente la struttura usciva nel payload e il
    campo `skipped_incoherent` non esisteva."""
    track = _storm_track([10.0, 16.0])          # ~504 km in 5 min
    b = _bundle([track])
    cs.evaluate(b, CONFIG["convective_structure"])

    # il calcolo NON filtra: il record esiste ancora (audit)
    assert len(b.convective_structures) == 1
    rec = b.convective_structures[0]
    assert rec["soi"] >= 65
    assert rec["track_points"] == 2
    assert rec["track_coherent"] is False
    assert rec["motion_reliable"] is False

    payload = output.build_convective_structures(b)
    assert payload["structures"] == []
    assert payload["summary"]["structures"] == 0
    assert payload["summary"]["skipped_incoherent"] == 1

    latest = output.build_latest(b)
    assert latest["structure_candidates"] == 0
    assert latest["skipped_incoherent"] == 1


def test_coherent_candidate_is_published_without_skips():
    """Track regolare (5 punti, ~60 km/h): pubblicata, skipped=0."""
    track = _storm_track([10.0 + 0.06 * i for i in range(5)])
    b = _bundle([track])
    cs.evaluate(b, CONFIG["convective_structure"])
    payload = output.build_convective_structures(b)
    assert len(payload["structures"]) == 1
    assert payload["structures"][0]["track_coherent"] is True
    assert payload["summary"]["skipped_incoherent"] == 0
    latest = output.build_latest(b)
    assert latest["structure_candidates"] == 1
    assert latest["skipped_incoherent"] == 0


def test_mixed_only_incoherent_is_skipped():
    """Coerente + salterina: si pubblica solo la coerente, scartata 1."""
    coherent = _storm_track([10.0 + 0.06 * i for i in range(5)], track_id=1)
    incoherent = _storm_track([10.0, 16.0], track_id=2)
    b = _bundle([coherent, incoherent])
    cs.evaluate(b, CONFIG["convective_structure"])
    assert len(b.convective_structures) == 2

    payload = output.build_convective_structures(b)
    assert len(payload["structures"]) == 1
    assert payload["structures"][0]["track_id"] == 1
    assert payload["structures"][0]["track_coherent"] is True
    assert payload["summary"]["skipped_incoherent"] == 1


def test_all_skipped_writes_valid_empty_file_and_keeps_audit(tmp_path):
    """Se TUTTE le strutture sono scartate il file resta valido con
    `structures: []` (mai un 404) e il track resta in storm_tracks.json."""
    track = _storm_track([10.0, 16.0])
    b = _bundle([track])
    cs.evaluate(b, CONFIG["convective_structure"])
    out = str(tmp_path / "radar")
    paths = output.write_outputs(b, out)

    with open(paths["convective_structures.json"], encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["structures"] == []
    assert payload["summary"]["skipped_incoherent"] == 1
    assert output.validate_outputs(out) == []

    # audit: la storm track (2 punti) resta pubblicata in storm_tracks.json
    with open(paths["storm_tracks.json"], encoding="utf-8") as fh:
        storm_tracks = json.load(fh)
    assert len(storm_tracks["tracks"]) == 1
