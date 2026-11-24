#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REPLAY dei salti del tracker sui frame di CELLE ARCHIVIATI.

Legge i frame di celle realmente archiviati dal workflow radar
(`data/radar/history/cells/<slot>.json` sul branch `radar-history`, oppure una
copia locale con `--cells-dir`) e riesegue il TRACKER DI PRODUZIONE su quelli:
stessa config (`CONFIG["tracking"]`), stesso coasting (`CONFIG["coasting"]`),
stessi timestamp di slot. Non inventa celle e non sintetizza frame.

Serve a misurare PRIMA/DOPO la riparazione dei salti:
  * `id_switch_jumps`    segmenti osservati sopra 110 km/h (deve restare 0);
  * `id_switch_reids`    re-ID inspiegati (deve scendere);
  * `continuity_rejections` associazioni finali rifiutate dal gate 110 km/h;
  * `median_duration_min`, `n_frames_ge_3`;
  * `kept_by_overlap`    associazioni tenute SOLO grazie all'IoU dei dischi;
  * `reids_stitched`     re-ID cuciti dentro il run.

Per il confronto si lancia due volte con `--engine-root` diverso (copia
pristina vs codice nuovo) e si confrontano i due JSON.

LIMITI DICHIARATI (non sono omissioni, sono cio' che il replay NON puo' fare):
  * input = CELLE gia' rilevate. Il replay non ri-esegue detect/preprocess e
    non rilegge i GeoTIFF: misura l'algoritmo di tracking, non il run completo;
  * il contesto di copertura per frame non e' nell'archivio: tutti i frame sono
    passati come validi/coperti (come `coast_acceptance.py`). Quindi il
    coasting non viene MAI vietato da `insufficient_coverage` in replay;
  * il seed dall'archivio fra run e' disattivato (`history_dir=None`): qui si
    misura il solo comportamento DENTRO il run;
  * `--frames` di default e' 6 (la finestra del run di produzione); `0` usa
    tutti gli slot archiviati (orizzonte piu' lungo, non confrontabile con la
    metrica pubblicata di un run).

Uso:
    python scripts/validation/tracker_jumps_replay.py --fetch
    python scripts/validation/tracker_jumps_replay.py --json
    python scripts/validation/tracker_jumps_replay.py \
        --engine-root <copia_pristina>/scripts --json
"""

import argparse
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

REPO = "meteoriskitalia-netizen/MeteoRisk-Light-v2"
BRANCH = "radar-history"
CELLS_PREFIX = "data/radar/history/cells"


def _import_engine(engine_root):
    """Importa `radar_engine` dalla radice indicata (pristina o corrente)."""
    if engine_root:
        engine_root = os.path.abspath(engine_root)
        if engine_root not in sys.path:
            sys.path.insert(0, engine_root)
    elif os.path.join(ROOT, "scripts") not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from radar_engine import aggregation, config, models, storm_tracking, tracking
    return aggregation, config, models, storm_tracking, tracking


# ---------------------------------------------------------------------------
# Archivi
# ---------------------------------------------------------------------------
def _api(url):
    with urllib.request.urlopen(url, timeout=60) as fh:
        return json.load(fh)


def _raw(path):
    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        REPO, BRANCH, path)
    with urllib.request.urlopen(url, timeout=60) as fh:
        return json.loads(fh.read().decode("utf-8"))


def fetch_cells(dest):
    """Scarica i frame di celle archiviati (repo pubblico, nessun token)."""
    tree = _api("https://api.github.com/repos/{}/git/trees/{}?recursive=1"
                .format(REPO, BRANCH))
    if tree.get("truncated"):
        raise SystemExit("albero del branch troncato: ripetere con --cells-dir")
    paths = sorted(item["path"] for item in tree["tree"]
                   if item["type"] == "blob"
                   and item["path"].startswith(CELLS_PREFIX + "/")
                   and item["path"].endswith(".json"))
    if not paths:
        raise SystemExit("nessun frame di celle nel branch " + BRANCH)
    os.makedirs(dest, exist_ok=True)
    for path in paths:
        with open(os.path.join(dest, os.path.basename(path)), "w",
                  encoding="utf-8") as fh:
            json.dump(_raw(path), fh, ensure_ascii=False)
    return len(paths)


def load_slots(cells_dir):
    slots = []
    for name in sorted(os.listdir(cells_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(cells_dir, name), encoding="utf-8") as fh:
            payload = json.load(fh)
        cells = payload.get("cells") or []
        if not cells:
            continue
        ts = int(cells[0]["timestamp_ms"])
        slots.append({"slot": payload.get("slot") or name,
                      "timestamp_ms": ts, "cells": cells})
    slots.sort(key=lambda s: s["timestamp_ms"])
    if not slots:
        raise SystemExit("nessun frame con celle in " + cells_dir)
    # Scarta i timestamp duplicati (un solo frame per istante).
    out, seen = [], set()
    for s in slots:
        if s["timestamp_ms"] in seen:
            continue
        seen.add(s["timestamp_ms"])
        out.append(s)
    return out


def _params(func):
    """Nomi dei parametri accettati da una funzione (compatibilita' pristina)."""
    import inspect
    return inspect.signature(func).parameters


def _cell(models_mod, payload, frame_index):
    return models_mod.DetectedCell(
        cell_id=payload["cell_id"],
        timestamp_ms=int(payload["timestamp_ms"]),
        timestamp_iso=payload["timestamp_iso"],
        area_km2=float(payload["area_km2"]),
        centroid_raster=tuple(payload.get("centroid_raster") or (0.0, 0.0)),
        centroid_lonlat=tuple(payload["centroid_lonlat"]),
        bbox_raster=tuple(payload.get("bbox") or (0, 0, 1, 1)),
        max_dbz=float(payload.get("max_dbz") or 0.0),
        mean_dbz=float(payload.get("mean_dbz") or 0.0),
        p90_dbz=float(payload.get("p90_dbz") or payload.get("max_dbz") or 0.0),
        eccentricity=payload.get("eccentricity"),
        solidity=(payload.get("solidity")
                  if payload.get("solidity") is not None else 1.0),
        compactness=(payload.get("compactness")
                     if payload.get("compactness") is not None else 1.0),
        pixel_count=int(payload.get("pixel_count") or 1),
        frame_index=frame_index,
    )

# ---------------------------------------------------------------------------
# Controlli indipendenti sul risultato
# ---------------------------------------------------------------------------
def _controls(tracks, tracker):
    """Controlli espliciti: nessuna fusione di celle distinte e vive."""
    same_frame_multi = 0
    observed_frames = []
    observed_spans = []
    observed_spans_ge3 = []
    published = [t for t in tracks if len(t.points) >= 2]
    for track in published:
        by_frame = {}
        for p in track.points:
            if getattr(p, "frame_index", -1) < 0:
                continue
            by_frame.setdefault(p.frame_index, set()).add(p.cell_id)
        same_frame_multi += sum(1 for ids in by_frame.values() if len(ids) > 1)
        obs = [p for p in track.points if getattr(p, "observed", True)
               and getattr(p, "frame_index", 0) >= 0]
        observed_frames.append(len({p.frame_index for p in obs}))
        if len(obs) >= 2:
            observed_spans.append(
                (obs[-1].timestamp_ms - obs[0].timestamp_ms) / 60000.0)
            if len(obs) >= 3:
                observed_spans_ge3.append(
                    (obs[-1].timestamp_ms - obs[0].timestamp_ms) / 60000.0)
    import statistics
    dur_obs = [(track.motion or {}).get("duration_observed_min")
               for track in published]
    dur_obs = [d for d in dur_obs if d is not None]
    max_kept_speed = None
    kept_speeds = []
    jumps_by_track = 0
    for track in tracks:
        obs = [p for p in track.points if getattr(p, "observed", True)]
        flagged = [p for p in obs if getattr(p, "centroid_jump", False)]
        if flagged:
            jumps_by_track += 1
        for a, b in zip(obs, obs[1:]):
            if not getattr(b, "centroid_jump", False):
                continue
            dt_h = (b.timestamp_ms - a.timestamp_ms) / 3600000.0
            if dt_h > 0:
                kept_speeds.append(round(
                    _haversine(a.lonlat, b.lonlat) / dt_h, 1))
    if kept_speeds:
        max_kept_speed = max(kept_speeds)
    stitches = list(getattr(tracker, "reid_stitches", None) or [])
    return {
        # Due celle distinte dello STESSO frame non possono stare nella stessa
        # track: se questo valore non e' 0, due celle vive sono state fuse.
        "same_frame_multi_cell_tracks": same_frame_multi,
        "live_to_live_merges": sum(
            1 for s in stitches
            if not (s.get("parent_was_dead") and s.get("child_was_newborn"))),
        "reid_stitches": len(stitches),
        "reid_max_distance_km": (max((s["distance_km"] for s in stitches),
                                     default=None)),
        "reid_max_gap_min": (max((s["gap_min"] for s in stitches),
                                 default=None)),
        "reid_max_speed_kmh": (max((s["speed_kmh"] for s in stitches),
                                   default=None)),
        # `n_frames_ge_3` PUBBLICATO conta anche i frame con punto coasted
        # (comportamento preesistente, non toccato): qui il conteggio ONESTO
        # sulle sole OSSERVAZIONI, cosi' il guadagno non e' attribuibile a
        # posizioni stimate.
        "n_frames_ge_3_observed_only": sum(1 for n in observed_frames if n >= 3),
        "median_observed_frames": (round(statistics.median(observed_frames), 1)
                                   if observed_frames else None),
        "median_observed_span_min": (round(statistics.median(observed_spans), 1)
                                     if observed_spans else None),
        # `median_duration_min` PUBBLICATO e' lo span primo-ultimo punto (anche
        # attraverso i buchi di una cucitura). Qui la mediana della durata
        # OSSERVATA, che e' la misura onesta di quanto una track e' stata vista.
        "median_duration_observed_min": (round(statistics.median(dur_obs), 1)
                                         if dur_obs else None),
        # Popolazione stabile (>= 3 osservazioni): la mediana qui non e'
        # trascinata dalle tracce corte che la cucitura rende pubblicabili.
        "median_observed_span_ge3_min": (
            round(statistics.median(observed_spans_ge3), 1)
            if observed_spans_ge3 else None),
        "tracks_with_centroid_jump": jumps_by_track,
        "max_kept_jump_speed_kmh": max_kept_speed,
        "kept_jump_speeds_kmh": sorted(kept_speeds),
    }


def _haversine(a, b):
    import math
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def replay(slots, engine_root=None, with_storm=True):
    agg, cfg_mod, models_mod, storm_mod, track_mod = _import_engine(engine_root)
    config = cfg_mod.CONFIG
    coast_cfg = dict(config.get("coasting") or {})
    tracker = track_mod.Tracker(dict(config["tracking"]),
                                cfg_scoring=config.get("scoring"),
                                history_dir=None, coasting_cfg=coast_cfg)
    cells_by_frame = []
    for idx, slot in enumerate(slots):
        cells = [_cell(models_mod, payload, idx) for payload in slot["cells"]]
        cells_by_frame.append(cells)
        tracker.update(cells, idx, frame_ts_ms=slot["timestamp_ms"],
                       frame_context={"valid": True, "coverage_ok": True})
    tracker.complete_cycles()
    tracks = tracker.finalize(cfg_scoring=config.get("scoring"))

    storm_tracks, storm_rejections = [], 0
    if with_storm and config.get("storm", {}).get("enabled"):
        storm_tracker = storm_mod.StormObjectTracker(
            config["storm"], cfg_scoring=config.get("scoring"),
            history_dir=None, coasting_cfg=coast_cfg)
        for idx, cells in enumerate(cells_by_frame):
            objs = agg.aggregate_frame(cells, idx, config["storm"])
            storm_tracker.update(objs, idx,
                                 frame_ts_ms=slots[idx]["timestamp_ms"],
                                 frame_context={"valid": True,
                                                "coverage_ok": True})
        storm_tracker.complete_cycles()
        storm_tracks = storm_tracker.finalize(cfg_scoring=config.get("scoring"))
        storm_rejections = int(getattr(storm_tracker,
                                       "continuity_rejections", 0) or 0)

    metrics_kwargs = dict(
        cell_continuity_rejections=getattr(tracker, "continuity_rejections", 0),
        storm_continuity_rejections=storm_rejections,
        cell_coasting=getattr(tracker, "coasting", None),
        storm_coasting=(getattr(storm_tracker, "coasting", None)
                        if with_storm else None))
    # I due contatori della riparazione dei salti esistono solo dal 2026-11-24:
    # su una copia PRISTINA la funzione non li accetta (e valgono 0).
    if "cell_kept_by_overlap" in _params(track_mod.build_tracking_metrics):
        metrics_kwargs.update(
            cell_kept_by_overlap=getattr(tracker, "kept_by_overlap", 0),
            cell_reids_stitched=getattr(tracker, "reids_stitched", 0))
    metrics = track_mod.build_tracking_metrics(
        tracks, storm_tracks, **metrics_kwargs)
    cell = metrics["cell"]
    out = {
        "cells": sum(len(c) for c in cells_by_frame),
        "frames": len(cells_by_frame),
        "window": {"first_slot": slots[0]["slot"], "last_slot": slots[-1]["slot"]},
        "cell": {
            "n_tracks": cell["n_tracks"],
            "n_active_tracks": cell["n_active_tracks"],
            "id_switch_jumps": cell["id_switch_jumps"],
            "id_switch_reids": cell["id_switch_reids"],
            "id_switch_rate": cell["id_switch_rate"],
            "continuity_rejections": cell["continuity_rejections"],
            "median_duration_min": cell["median_duration_min"],
            "median_duration_current_min": cell["median_duration_current_min"],
            "n_frames_ge_3": cell["n_frames_ge_3"],
            "centroid_jump_segments": cell.get("centroid_jump_segments", 0),
            "kept_by_overlap": cell.get("kept_by_overlap", 0),
            "reids_stitched": cell.get("reids_stitched", 0),
            "coasted_points": cell["coasted_points"],
            "pct_reliable": cell["pct_reliable"],
        },
        "combined": {
            "n_tracks": metrics["n_tracks"],
            "id_switch_jumps": metrics["id_switch_jumps"],
            "id_switch_reids": metrics["id_switch_reids"],
            "continuity_rejections": metrics["continuity_rejections"],
            "median_duration_min": metrics["median_duration_min"],
            "n_frames_ge_3": metrics["n_frames_ge_3"],
            "centroid_jump_segments": metrics.get("centroid_jump_segments", 0),
            "kept_by_overlap": metrics.get("kept_by_overlap", 0),
            "reids_stitched": metrics.get("reids_stitched", 0),
        },
        "storm_object": {
            "n_tracks": metrics["storm_object"]["n_tracks"],
            "id_switch_jumps": metrics["storm_object"]["id_switch_jumps"],
            "id_switch_reids": metrics["storm_object"]["id_switch_reids"],
            "continuity_rejections": metrics["storm_object"]["continuity_rejections"],
        },
        "controls": _controls(tracks, tracker),
        "stitches": list(getattr(tracker, "reid_stitches", None) or []),
    }
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cells-dir", default=os.path.join(
        ROOT, "data", "radar", "history", "cells"))
    parser.add_argument("--fetch", action="store_true",
                        help="scarica i frame archiviati dal branch dati")
    parser.add_argument("--frames", type=int, default=6,
                        help="ultimi N slot (0 = tutti)")
    parser.add_argument("--until", default=None,
                        help="slot finale incluso (es. 2026-11-24T09-55-00Z)")
    parser.add_argument("--engine-root", default=None,
                        help="radice che contiene radar_engine/ (copia pristina)")
    parser.add_argument("--no-storm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cells_dir = os.path.abspath(args.cells_dir)
    if args.fetch:
        n = fetch_cells(cells_dir)
        print("scaricati {} frame di celle in {}".format(n, cells_dir))
    slots = load_slots(cells_dir)
    if args.until:
        keep = [s for s in slots if s["slot"] <= args.until]
        if not keep:
            raise SystemExit("nessuno slot <= " + args.until)
        slots = keep
    if args.frames and args.frames > 0:
        slots = slots[-args.frames:]
    result = replay(slots, engine_root=args.engine_root,
                    with_storm=not args.no_storm)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return result
    print("REPLAY  finestra {} -> {} | {} frame, {} celle".format(
        result["window"]["first_slot"], result["window"]["last_slot"],
        result["frames"], result["cells"]))
    for layer in ("cell", "storm_object", "combined"):
        print(" {:<13}: {}".format(layer, json.dumps(result[layer],
                                                     sort_keys=True)))
    print(" controlli    : {}".format(json.dumps(result["controls"],
                                                 sort_keys=True)))
    return result


if __name__ == "__main__":
    main()
