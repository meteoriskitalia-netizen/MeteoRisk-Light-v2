#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accettazione del coasting su dati REALI (sola lettura, nessuna rete).

Legge `data/radar/tracks.json` e `storm_tracks.json` del run pubblicato,
ricostruisce le frame liste dai punti REALI (frame_index >= 0; le ancore
frame_index=-1 sono escluse) e riesegue i due tracker con coasting OFF/ON.
Stampa i numeri di frammentazione, durata e id_switch. NON scrive nulla
nella repo e NON usa celle sintetiche: i casi sintetico-stampella sono solo
gli unit test in scripts/tests/radar_engine/test_coasting.py.

Limitazioni dichiarate:
  - i JSON pubblicano solo le celle/oggetti che sono entrati in una track
    (n_frames >= 2): le nascite di un solo frame non sono ricostruibili;
  - il replay non puo' ricostruire il contesto di copertura per frame (i
    GeoTIFF DPC grezzi non sono presenti): usa frame validi per tutti;
  - non e' un run end-to-end del motore (servono i GeoTIFF), ma un replay
    dell'algoritmo di tracking esattamente come viene chiamato da main.py.
"""
import json
import math
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from radar_engine import models, storm_tracking as stm, tracking as trk
from radar_engine.config import CONFIG

RADAR = os.path.join(ROOT, "data", "radar")


def _load(name):
    with open(os.path.join(RADAR, name), encoding="utf-8") as fh:
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
        bbox_raster=(0, 0, 1, 1), max_dbz=p["max_dbz"],
        mean_dbz=p["mean_dbz"], p90_dbz=p["max_dbz"],
        eccentricity=p.get("eccentricity"), solidity=p.get("solidity") or 1.0,
        compactness=p.get("compactness") or 1.0, pixel_count=1,
        frame_index=p["frame_index"])


def _obj(p):
    area = float(p["area_km2"])
    o = models.StormObject(
        storm_object_id=p["storm_object_id"], timestamp_ms=int(p["timestamp_ms"]),
        timestamp_iso=p["timestamp"], frame_index=p["frame_index"], cells=[],
        centroid_lonlat=tuple(p["lonlat"]), area_km2=area,
        equiv_radius_km=math.sqrt(max(area, 0.0) / math.pi),
        bbox_lonlat=tuple(p["lonlat"] + p["lonlat"]),
        convex_hull_lonlat=[], max_dbz=p["max_dbz"], mean_dbz=p["mean_dbz"],
        p90_dbz=p["max_dbz"], cell_density=int(p.get("cell_count") or 1))
    o.tracking_ambiguity = p.get("tracking_ambiguity")
    o.ambiguity_reason = {"merge_split": False, "competing": 1}
    return o


def _summary(tracks):
    m = trk.tracking_metrics(tracks, "cell")
    dur = [t.motion.get("duration_min") for t in tracks
           if t.motion.get("duration_min") is not None]
    obs = [t.motion.get("duration_observed_min") for t in tracks
           if t.motion.get("duration_observed_min") is not None]
    return {
        "n_tracks": m["n_tracks"],
        "median_duration_min": m["median_duration_min"],
        "median_duration_min_all": (round(statistics.median(dur), 1)
                                    if dur else None),
        "median_duration_observed_min": (round(statistics.median(obs), 1)
                                         if obs else None),
        "id_switch_rate": m["id_switch_rate"],
        "id_switch_jumps": m["id_switch_jumps"],
        "id_switch_reids": m["id_switch_reids"],
    }


def main():
    tj, sj = _load("tracks.json"), _load("storm_tracks.json")
    cells = _frames([p for t in tj["tracks"] for p in t["points"]], "cell_id")
    objs = _frames([p for t in sj["tracks"] for p in t["points"]],
                   "storm_object_id")
    ts = sorted({p["timestamp_ms"] for fi in cells for p in fi})
    print("REAL INPUT: tracks={} celle={} frames={} | storm={} obj={} frames={}"
          .format(len(tj["tracks"]), sum(len(f) for f in cells), len(cells),
                  len(sj["tracks"]), sum(len(f) for f in objs), len(objs)))
    print(" celle/frame:", [len(f) for f in cells])
    print(" obj/frame  :", [len(f) for f in objs])
    result = {}
    for label, on in (("OFF_pristine", False), ("ON_coasting1", True)):
        cfg = dict(CONFIG["tracking"])
        cfg["max_coast_frames"] = 0           # valore storico, invariato
        tr = trk.Tracker(cfg, coasting_cfg=(CONFIG["coasting"] if on else None))
        for i, fi in enumerate(cells):
            tr.update([_cell(p) for p in fi], i,
                      frame_ts_ms=(ts[i] if i < len(ts) else None),
                      frame_context=({"valid": True, "coverage_ok": True}
                                     if on else None))
        tr.complete_cycles()
        tr.finalize()
        s = stm.StormObjectTracker(
            CONFIG["storm"], coasting_cfg=(CONFIG["coasting"] if on else None))
        for i, fi in enumerate(objs):
            s.update([_obj(p) for p in fi], i,
                     frame_ts_ms=(ts[i] if i < len(ts) else None),
                     frame_context=({"valid": True, "coverage_ok": True}
                                    if on else None))
        s.complete_cycles()
        s.finalize()
        comb = trk.build_tracking_metrics(
            tr.tracks, s.tracks,
            cell_coasting=getattr(tr, "coasting", None),
            storm_coasting=getattr(s, "coasting", None))
        result[label] = {
            "cell": _summary(tr.tracks), "storm": _summary(s.tracks),
            "coasting": {k: comb[k] for k in (
                "coasted_points", "coasted_tracks_recovered",
                "coasting_null_reasons")}}
        print("\n== {} ==".format(label))
        print(" cell   :", result[label]["cell"])
        print(" storm  :", result[label]["storm"])
        print(" coasting:", result[label]["coasting"])
    return result


if __name__ == "__main__":
    main()
