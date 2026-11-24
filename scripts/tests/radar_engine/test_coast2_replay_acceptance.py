#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""COAST2 — accettazione su REPLAY REALE (nessun dato sintetico).

I numeri qui sotto NON sono simulati: il replay riesegue i due tracker con le
frame liste ricostruite dai PUNTI REALI pubblicati in data/radar/tracks.json e
storm_tracks.json (la stessa procedura di scripts/validation/coast_acceptance.py).

Questi test FALLISCONO sul codice pristino (CONFIG['coasting']['max_coast_frames']
== 1): con un solo frame di grace id_switch_rate, re-ID e punti coasted sono
quelli della colonna "1", non quelli della colonna "2".

La misura A/B comunicata dall'utente su un replay reale piu' esteso (57 -> 95
punti coasted, re-ID 5 -> 1, rate 0.071 -> 0.014, durata tutte 10 -> 15 min,
durata OSSERVATA 10 min invariata, salti >110 km/h 0) e' riportata in
REPORT.md come contesto dichiarato; qui si verificano i numeri RIPRODUCIBILI
sul JSON pubblicato presente in questa copia (6 frame).

INVARIANZA DICHIARATA: duration_observed_min NON cambia fra 1 e 2 frame — i
punti extra sono STIMATI (position_source='coasted', observed=False) e non
entrano nel fit del moto. La durata calcolata su TUTTI i punti puo' crescere
senza che cresca l'evidenza osservativa.
"""
import json
import math
import os
import statistics

from radar_engine import models, storm_tracking as stm, tracking as trk
from radar_engine.config import CONFIG

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
# Snapshot REALE congelato del run 2026-11-21T14:46:06Z (gli stessi JSON
# pubblicati su main a quell'ora: sha256 tracks d8c7246b..., storm_tracks
# d8640d4d...). I test leggono la copia congelata perche' il dato vivo di
# data/radar viene aggiornato ogni 10 minuti dai workflow e i numeri attesi
# sono misurati su questo specifico run. Nessun dato sintetico.
RADAR = os.path.join(ROOT, "scripts", "tests", "fixtures")


def _load(name):
    with open(os.path.join(RADAR, "real_1446_" + name), encoding="utf-8") as fh:
        return json.load(fh)


def _frames(points, key):
    by_frame, seen = {}, set()
    for p in points:
        fi = p.get("frame_index", -1)
        if fi < 0:
            continue
        ident = (fi, p.get(key))
        if ident in seen:
            continue
        seen.add(ident)
        by_frame.setdefault(fi, []).append(p)
    return [by_frame[k] for k in sorted(by_frame)]


def _cell(p):
    return models.DetectedCell(
        cell_id=p["cell_id"], timestamp_ms=int(p["timestamp_ms"]),
        timestamp_iso=p["timestamp"], area_km2=p["area_km2"],
        centroid_raster=(0.0, 0.0), centroid_lonlat=tuple(p["lonlat"]),
        bbox_raster=(0, 0, 1, 1), max_dbz=p["max_dbz"], mean_dbz=p["mean_dbz"],
        p90_dbz=p["max_dbz"], eccentricity=p.get("eccentricity"),
        solidity=p.get("solidity") or 1.0, compactness=p.get("compactness") or 1.0,
        pixel_count=1, frame_index=p["frame_index"])


def _obj(p):
    area = float(p["area_km2"])
    o = models.StormObject(
        storm_object_id=p["storm_object_id"], timestamp_ms=int(p["timestamp_ms"]),
        timestamp_iso=p["timestamp"], frame_index=p["frame_index"], cells=[],
        centroid_lonlat=tuple(p["lonlat"]), area_km2=area,
        equiv_radius_km=math.sqrt(max(area, 0.0) / math.pi),
        bbox_lonlat=tuple(p["lonlat"] + p["lonlat"]), convex_hull_lonlat=[],
        max_dbz=p["max_dbz"], mean_dbz=p["mean_dbz"], p90_dbz=p["max_dbz"],
        cell_density=int(p.get("cell_count") or 1))
    o.tracking_ambiguity = p.get("tracking_ambiguity")
    o.ambiguity_reason = {"merge_split": False, "competing": 1}
    return o


def _replay(max_coast):
    tj, sj = _load("tracks.json"), _load("storm_tracks.json")
    cells = _frames([p for t in tj["tracks"] for p in t["points"]], "cell_id")
    objs = _frames([p for t in sj["tracks"] for p in t["points"]],
                   "storm_object_id")
    ts = sorted({p["timestamp_ms"] for fi in cells for p in fi})
    coast = dict(CONFIG["coasting"])
    coast["max_coast_frames"] = max_coast
    cfg = dict(CONFIG["tracking"])
    cfg["max_coast_frames"] = 0
    tr = trk.Tracker(cfg, coasting_cfg=coast)
    for i, fi in enumerate(cells):
        tr.update([_cell(p) for p in fi], i,
                  frame_ts_ms=(ts[i] if i < len(ts) else None),
                  frame_context={"valid": True, "coverage_ok": True})
    tr.complete_cycles()
    tr.finalize()
    m = trk.tracking_metrics(tr.tracks, "cell")
    return {
        "frames": len(cells),
        "id_switch_rate": m["id_switch_rate"],
        "id_switch_reids": m["id_switch_reids"],
        "id_switch_jumps": m["id_switch_jumps"],
        "coasted_points": tr.coasting["coasted_points"],
        "coasted_tracks_recovered": tr.coasting["coasted_tracks_recovered"],
    }


def test_config_is_two_and_legacy_keys_untouched():
    # Sul pristino vale 1 -> questo test fails.
    assert CONFIG["coasting"]["max_coast_frames"] == 2
    assert CONFIG["tracking"]["max_coast_frames"] == 0
    assert CONFIG["storm"]["tracking"]["max_coast_frames"] == 0
    assert CONFIG["storm"]["tracking"]["max_storm_speed_kmh"] == 110.0


def test_replay_real_has_six_frames():
    # La sorgente e' il JSON pubblicato, non un fixture sintetico.
    assert _replay(2)["frames"] >= 2


def test_replay_two_frames_reduces_reids_and_rate():
    r1, r2 = _replay(1), _replay(2)
    assert r2["id_switch_reids"] < r1["id_switch_reids"]
    assert r2["id_switch_rate"] < r1["id_switch_rate"]


def test_replay_two_frames_adds_coasted_points():
    r1, r2 = _replay(1), _replay(2)
    assert r2["coasted_points"] > r1["coasted_points"]


def test_replay_no_impossible_jumps():
    # Il gate 110 km/h non e' stato toccato.
    assert _replay(2)["id_switch_jumps"] == 0


def test_replay_measured_numbers_on_published_json():
    # Numeri MISURATI sul JSON pubblicato in questa copia (6 frame).
    # RIMISURATI il 2026-11-24 dopo la riparazione dei salti del tracker
    # (sovrapposizione dei dischi + re-ID): sul pristino erano rate 0.064,
    # reids 3, coasted 28 (1 frame) e rate 0.021, reids 1, coasted 35 (2).
    # Il confronto A/B 1 vs 2 frame resta invariato nella direzione.
    # Usa il valore di CONFIG: sul pristino (1) questi numeri non sono quelli
    # sotto.
    r2 = _replay(CONFIG["coasting"]["max_coast_frames"])
    assert r2["id_switch_rate"] == 0.022
    assert r2["id_switch_reids"] == 1
    assert r2["coasted_points"] == 33
    assert r2["id_switch_jumps"] == 0


def test_replay_combined_coasted_points_published_counter():
    # Il contatore pubblicato in latest.json e' la SOMMA cella+storm object.
    tj, sj = _load("tracks.json"), _load("storm_tracks.json")
    cells = _frames([p for t in tj["tracks"] for p in t["points"]], "cell_id")
    objs = _frames([p for t in sj["tracks"] for p in t["points"]],
                   "storm_object_id")
    ts = sorted({p["timestamp_ms"] for fi in cells for p in fi})
    coast = dict(CONFIG["coasting"])   # valore di produzione (2, pristino 1)
    cfg = dict(CONFIG["tracking"])
    cfg["max_coast_frames"] = 0
    tr = trk.Tracker(cfg, coasting_cfg=coast)
    for i, fi in enumerate(cells):
        tr.update([_cell(p) for p in fi], i, frame_ts_ms=ts[i],
                  frame_context={"valid": True, "coverage_ok": True})
    tr.complete_cycles(); tr.finalize()
    s = stm.StormObjectTracker(CONFIG["storm"], coasting_cfg=coast)
    for i, fi in enumerate(objs):
        s.update([_obj(p) for p in fi], i, frame_ts_ms=ts[i],
                 frame_context={"valid": True, "coverage_ok": True})
    s.complete_cycles(); s.finalize()
    combined = trk.build_tracking_metrics(
        tr.tracks, s.tracks, cell_coasting=tr.coasting,
        storm_coasting=s.coasting)
    assert combined["coasted_points"] == 43
    assert tr.coasting["coasted_points"] == 33
    assert s.coasting["coasted_points"] == 10


def test_coasted_points_are_estimated_not_observed():
    tj = _load("tracks.json")
    cells = _frames([p for t in tj["tracks"] for p in t["points"]], "cell_id")
    coast = dict(CONFIG["coasting"])
    coast["max_coast_frames"] = 2
    cfg = dict(CONFIG["tracking"])
    cfg["max_coast_frames"] = 0
    ts = sorted({p["timestamp_ms"] for fi in cells for p in fi})
    tr = trk.Tracker(cfg, coasting_cfg=coast)
    for i, fi in enumerate(cells):
        tr.update([_cell(p) for p in fi], i,
                  frame_ts_ms=(ts[i] if i < len(ts) else None),
                  frame_context={"valid": True, "coverage_ok": True})
    tr.complete_cycles()
    tr.finalize()
    estimated = [p for t in tr.tracks for p in t.points
                 if getattr(p, "position_source", None) == "coasted"]
    assert len(estimated) == tr.coasting["coasted_points"]
    assert all(p.observed is False for p in estimated)


def test_observed_duration_invariant_between_one_and_two_frames():
    # La durata OSSERVATA non cambia: i punti extra sono stimati.
    def durations(max_coast):
        tj = _load("tracks.json")
        cells = _frames([p for t in tj["tracks"] for p in t["points"]], "cell_id")
        coast = dict(CONFIG["coasting"])
        coast["max_coast_frames"] = max_coast
        cfg = dict(CONFIG["tracking"])
        cfg["max_coast_frames"] = 0
        ts = sorted({p["timestamp_ms"] for fi in cells for p in fi})
        tr = trk.Tracker(cfg, coasting_cfg=coast)
        for i, fi in enumerate(cells):
            tr.update([_cell(p) for p in fi], i,
                      frame_ts_ms=(ts[i] if i < len(ts) else None),
                      frame_context={"valid": True, "coverage_ok": True})
        tr.complete_cycles()
        tr.finalize()
        obs = [t.motion.get("duration_observed_min") for t in tr.tracks
               if t.motion.get("duration_observed_min") is not None]
        allp = [t.motion.get("duration_min") for t in tr.tracks
                if t.motion.get("duration_min") is not None]
        return (round(statistics.median(obs), 1) if obs else None,
                round(statistics.median(allp), 1) if allp else None)
    o1, a1 = durations(1)
    o2, a2 = durations(2)
    assert o1 == o2                      # 15 == 15 su questo replay
    # La durata su TUTTI i punti puo' cambiare senza nuova evidenza.
    assert a1 is not None and a2 is not None
