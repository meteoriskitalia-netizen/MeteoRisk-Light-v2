#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sweep della sezione «Struttura convettiva» (classi + geometria + SOI).

PERCHE' QUESTO MODULO
---------------------
La sezione pubblica oggi 7 classi morfologiche ma ne produce 2-3, cambia classe
su ~37% delle track e pubblica poligoni che sono l'inviluppo 20 dBZ (area
totale dell'ordine di 10^5 km2 su una singola ora). Non esistono etichette
della morfologia: l'unico proxy disponibile sono gli eventi al suolo
METAR/SPECI. Questo modulo misura, su giornate dichiarate PRIMA:

  * POD degli eventi severi dentro le strutture severe pubblicate;
  * frazione d'area del dominio radar marcata severa (proxy del falso
    positivo, NON un FAR: 99 stazioni non coprono il territorio);
  * stabilita' della classe frame-per-frame, con isteresi;
  * entropia delle classi (impedisce di "stabilizzare" fondendo tutto).

Non e' una taratura: con 15 eventi A/B totali (6 giornate di screening, 8 di
verifica) qualunque soglia e' un'ipotesi. Il modulo serve a scegliere una
combinazione difendibile e a misurare quanto cambia il prodotto.

COSA RICALCOLA DAL PRODOTTO
---------------------------
* detection: `radar_engine.detect.detect_cells` (stessa funzione, stessi
  parametri letti da config, sovrascritti dallo sweep);
* tracking cella e storm object: `Tracker` / `StormObjectTracker`, stessi
  finalize;
* SOI e gate: `convective_structure._integral_soi`, `_candidate_dict`,
  `dedup_records`, `split_publishable` (le stesse funzioni del prodotto);
* maschera: componenti connesse della VMI alla soglia di detection; il core
  e' la componente >= `core_dbz` piu' vicina alla posizione del record, con
  buffer di rappresentazione. `core_dbz = soglia di detection` e
  `buffer_km = 0` riproducono l'inviluppo attuale.

COSA NON RICALCOLA
------------------
* echo top / VIL / overhang: richiedono i CAPPI. Restano `None`, quindi la
  regola 2D non li usa. La variante MCS 2D e' dichiarata (`mcs_require_echo_top`
  false) e nel prodotto va marcata come candidato, non come MCS confermato.
* BOW_ECHO: `bow_score` non e' calcolato. Resta non producibile e viene
  dichiarato nel report; non lo si inventa.
* supercell: richiede VRADH, resta null (nessun Doppler nella VMI).

USO
---
    python scripts/validation/structure_sweep.py --precache
    python scripts/validation/structure_sweep.py --screening --shard 0 --shards 4
    python scripts/validation/structure_sweep.py --merge
    python scripts/validation/structure_sweep.py --final --combo-file <file>
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import math
import os
import pickle
import random
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from radar_engine import aggregation as agg_mod  # noqa: E402
from radar_engine import advanced as adv_mod  # noqa: E402
from radar_engine import clock as clock_mod  # noqa: E402
from radar_engine import convective_structure as cs_mod  # noqa: E402
from radar_engine import detect as detect_mod  # noqa: E402
from radar_engine import fetch as fetch_mod  # noqa: E402
from radar_engine import models  # noqa: E402
from radar_engine import storm_tracking as storm_mod  # noqa: E402
from radar_engine import tracking as track_mod  # noqa: E402
from radar_engine.config import load_config  # noqa: E402

OUT = os.path.join(ROOT, "docs", "evidence", "structure_sweep_20261127")
CACHE = os.path.join(OUT, "cache")
RESULTS = os.path.join(OUT, "results")

# ---------------------------------------------------------------------------
# GIORNATE DICHIARATE PRIMA (docs/evidence/structure_sweep_20261127/
# DECLARATION.md). Non si aggiungono ne' si tolgono dopo aver visto i numeri.
# ---------------------------------------------------------------------------
SCREENING = [
    {"id": "20260528T1455", "iso": "2026-05-28T14:55:00Z",
     "events": [("LIPL", 45.4206, 10.2814, "hail", "B", "2026-05-28T14:55:00Z")]},
    {"id": "20260610T1555", "iso": "2026-06-10T15:55:00Z",
     "events": [("LIPI", 45.9756, 13.0492, "hail", "B", "2026-06-10T15:55:00Z")]},
    {"id": "20260701T1320", "iso": "2026-07-01T13:20:00Z",
     "events": [("LIMF", 45.2025, 7.6494, "hail", "B", "2026-07-01T13:20:00Z")]},
    {"id": "20260701T1450", "iso": "2026-07-01T14:50:00Z",
     "events": [("LIMP", 44.8245, 10.2964, "hail", "B", "2026-07-01T14:50:00Z")]},
    {"id": "20260818T0550", "iso": "2026-08-18T05:50:00Z",
     "events": [("LIPR", 44.0244, 12.6128, "funnel_cloud", "B", "2026-08-18T05:50:00Z")]},
    {"id": "20260827T2055", "iso": "2026-08-27T20:55:00Z",
     "events": [("LIRS", 42.7481, 11.0589, "severe_wind", "B", "2026-08-27T20:55:00Z")]},
    {"id": "20261011T2350", "iso": "2026-10-11T23:50:00Z",
     "events": [("LIPX", 45.3875, 10.8723, "hail", "B", "2026-10-11T23:50:00Z")]},
]

FINAL = [
    {"id": "20260613T2055", "iso": "2026-06-13T20:55:00Z",
     "events": [("LIRS", 42.7481, 11.0589, "severe_wind", "B", "2026-06-13T20:55:00Z")]},
    {"id": "20260629T1350", "iso": "2026-06-29T13:50:00Z",
     "events": [("LIQN", 42.4236, 12.1000, "severe_wind", "B", "2026-06-29T13:50:00Z")]},
    {"id": "20260718T1950", "iso": "2026-07-18T19:50:00Z",
     "events": [("LIML", 45.4614, 9.2626, "hail", "B", "2026-07-18T19:50:00Z")]},
    {"id": "20260805T1555", "iso": "2026-08-05T15:55:00Z",
     "events": [("LIRH", 41.6450, 13.2994, "severe_wind", "B", "2026-08-05T15:55:00Z")]},
    {"id": "20260826T0850", "iso": "2026-08-26T08:50:00Z",
     "events": [("LIMJ", 44.4133, 8.8375, "funnel_cloud", "B", "2026-08-26T08:50:00Z")]},
    {"id": "20260919T0025", "iso": "2026-09-19T00:25:00Z",
     "events": [("LIRE", 41.6547, 12.4449, "severe_wind", "B", "2026-09-19T00:28:00Z")]},
    {"id": "20261020T1555", "iso": "2026-10-20T15:55:00Z",
     "events": [("LIVC", 44.1958, 9.5578, "severe_wind", "B", "2026-10-20T15:55:00Z")]},
    {"id": "20261021T1655", "iso": "2026-10-21T16:55:00Z",
     "events": [("LIVC", 44.1958, 9.5578, "severe_wind", "B", "2026-10-21T16:55:00Z")]},
]

# ---------------------------------------------------------------------------
# SPAZIO DELLE COMBINAZIONI
# ---------------------------------------------------------------------------
DBZ_GRID = (20.0, 25.0, 30.0)
MIN_AREA_GRID = (30.0, 50.0, 100.0, 200.0)   # 30 = baseline di produzione
PERSISTENCE_GRID = (2, 3, 4)
SOI_GRID = (55.0, 65.0, 75.0)
HYSTERESIS_GRID = (1, 2, 3)
ELONGATION_GRID = (2.5, 3.0, 3.5)
MCS_AREA_GRID = (2500.0, 5000.0, 8000.0)
MCS_DURATION_GRID = (20.0, 40.0, 60.0)
CORE_DBZ_GRID = (20.0, 25.0, 30.0, 35.0, 40.0, 45.0)
BUFFER_GRID = (0.0, 5.0, 10.0, 15.0)         # 0 = nessun buffer = baseline
CORE_MODE_GRID = ("nearest", "all")          # tutti i nuclei (cap 8) o il piu' vicino
MAX_CORES_PUBLISHED = 8

BASELINE = {
    "dbz": 20.0, "min_area": 30.0, "persistence": 2,
    "candidate_soi": 65.0, "hysteresis": 1, "elongation": 3.0,
    "mcs_area": 5000.0, "mcs_duration": 60.0,
    "core_dbz": 20.0, "buffer_km": 0.0, "core_mode": "nearest",
    # verita' 2D: la produzione NON ha echo_top nei record 2D; con True la
    # regola MCS resta irraggiungibile (comportamento reale attuale).
    "mcs_require_echo_top": True,
}

# Soglie fisse (non in sweep: sono il contratto corrente della regola 2D).
MULTICELL_MIN_AREA_KM2 = 150.0
SINGLE_CELL_MAX_DURATION_MIN = 60.0
SQUALL_MIN_CELLS = 3
MCS_MIN_CELLS = 4
MCS_MIN_DBZ = 45.0
MCS_MIN_ECHO_TOP_M = 6000.0
MIN_CORE_AREA_KM2 = 4.0

# Test di sanita' dichiarati in DECLARATION.md (prima dei risultati).
MAX_AREA_FRAC = 0.25
MAX_CLASS_SHARE = 0.90
MIN_RECORDS_FOR_SHARE_GATE = 20


def _deep(d):
    return json.loads(json.dumps(d))


def anchors(which):
    return SCREENING if which == "screening" else FINAL


def _anchor_cfg():
    cfg = load_config()
    cfg["ensemble"] = dict(cfg.get("ensemble") or {}, enabled=False)
    cfg["nowcast_trend"] = {"enabled": False}
    cfg["nowcast_clouds"] = {"enabled": False}
    cfg["opera"] = dict(cfg.get("opera") or {}, enabled=False)
    return cfg


def _iso_to_ms(iso):
    ms, _ = clock_mod.parse_iso8601_utc(iso)
    return int(ms)


# ---------------------------------------------------------------------------
# Cache dei frame VMI (solo VMI: lo sweep non usa CAPPI/VIL/ETM)
# ---------------------------------------------------------------------------
def _cache_path(anchor_id, frames):
    return os.path.join(CACHE, "%s_f%d.pkl" % (anchor_id, int(frames)))


def precache(which, frames, only=None):
    os.makedirs(CACHE, exist_ok=True)
    cfg = _anchor_cfg()
    for a in anchors(which):
        if only and a["id"] != only:
            continue
        path = _cache_path(a["id"], frames)
        if os.path.exists(path):
            print("cache ok", a["id"])
            continue
        ms = _iso_to_ms(a["iso"])
        t0 = time.time()
        rasters, warn, _ts = fetch_mod.fetch_frames(
            cfg, product="VMI", max_frames=int(frames), anchor_ms=ms)
        if len(rasters) < 2:
            print("FAIL", a["id"], "frames", len(rasters), warn)
            continue
        payload = {
            "anchor": a,
            "rasters": [{
                "data": np.asarray(rd.data, dtype="float32"),
                "valid_mask": np.asarray(rd.valid_mask, dtype=bool),
                "transform": rd.transform,
                "crs_wkt": rd.crs_wkt,
                "pixel_area_km2": float(rd.pixel_area_km2),
                "time_ms": int(rd.time_ms),
                "time_iso": rd.time_iso,
                "rows": int(rd.rows),
                "cols": int(rd.cols),
            } for rd in rasters],
        }
        with open(path, "wb") as fh:
            pickle.dump(payload, fh, protocol=4)
        print("cached", a["id"], len(rasters), "frames",
              round(time.time() - t0, 1), "s", os.path.getsize(path), "B")


def load_rasters(anchor_id, frames):
    path = _cache_path(anchor_id, frames)
    with open(path, "rb") as fh:
        payload = pickle.load(fh)
    from pyproj import Transformer
    out = []
    for r in payload["rasters"]:
        tr = Transformer.from_crs(r["crs_wkt"], "EPSG:4326", always_xy=True)
        rd = models.RasterData(
            data=np.asarray(r["data"], dtype="float64"),
            valid_mask=np.asarray(r["valid_mask"], dtype=bool),
            nodata_mask=~np.asarray(r["valid_mask"], dtype=bool),
            rows=r["rows"], cols=r["cols"], crs=r["crs_wkt"],
            crs_wkt=r["crs_wkt"], transform=r["transform"],
            pixel_area_km2=r["pixel_area_km2"], time_ms=r["time_ms"],
            time_iso=r["time_iso"], source_path="cache",
            declared_nodata=None, geo_transform=tr)
        out.append(rd)
    out.sort(key=lambda rd: rd.time_ms)
    return payload["anchor"], out


# ---------------------------------------------------------------------------
# Detection + tracking (una volta per variante di detection)
# ---------------------------------------------------------------------------
def _binary_labels(raster, cfg_detect):
    """Etichette ESATTE di detect_cells (stessa soglia, stesso opening)."""
    import scipy.ndimage as ndi
    valid = np.asarray(raster.valid_mask, dtype=bool)
    threshold = float(cfg_detect["dbz_threshold"])
    binary = valid & (raster.data >= threshold)
    radius = int(cfg_detect.get("morph_radius_px", 0))
    if radius > 0:
        se = detect_mod._struct(radius, cfg_detect.get("connectivity_8", True))
        binary = ndi.binary_opening(binary, structure=se)
        binary = ndi.binary_closing(binary, structure=se)
    lab, _n = ndi.label(binary, structure=detect_mod._struct(
        0, cfg_detect.get("connectivity_8", True)))
    return lab


def _pca_elongation(mask):
    """Rapporto assi PCA di una maschera (>=1); 1.0 se degenere."""
    rows, cols = np.nonzero(mask)
    if rows.size < 3:
        return 1.0
    xy = np.column_stack((cols.astype("float64"), rows.astype("float64")))
    cov = np.cov(xy.T)
    ev = np.linalg.eigvalsh(cov)
    ev = np.clip(np.sort(ev), 1e-9, None)
    return float(math.sqrt(ev[-1] / ev[0]))


def _cell_mask(lab, cell):
    """(mask cropped, r0, c0) della cella nella label map del frame."""
    try:
        idx = int(str(cell.cell_id).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None, 0, 0
    r0, c0, r1, c1 = [int(v) for v in cell.bbox]
    r0 = max(r0, 0); c0 = max(c0, 0)
    r1 = min(r1, lab.shape[0] - 1); c1 = min(c1, lab.shape[1] - 1)
    if r1 < r0 or c1 < c0:
        return None, 0, 0
    sub = lab[r0:r1 + 1, c0:c1 + 1]
    return (sub == idx), r0, c0


def _object_mask(lab, obj):
    """Unione delle maschere delle celle dell'oggetto, cropped."""
    masks = []
    r0 = c0 = 10 ** 9
    r1 = c1 = -1
    for cell in obj.cells or []:
        m, rr, cc = _cell_mask(lab, cell)
        if m is None:
            continue
        masks.append((m, rr, cc))
        r0 = min(r0, rr); c0 = min(c0, cc)
        r1 = max(r1, rr + m.shape[0] - 1)
        c1 = max(c1, cc + m.shape[1] - 1)
    if not masks:
        return None, 0, 0
    out = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=bool)
    for m, rr, cc in masks:
        out[rr - r0:rr - r0 + m.shape[0],
            cc - c0:cc - c0 + m.shape[1]] |= m
    return out, r0, c0


def run_detection(anchor, rasters, cfg, dbz, min_area):
    """Detection + tracking + storm objects. Ritorna un dict di lavoro."""
    cfg_detect = dict(cfg["detect"])
    cfg_detect["dbz_threshold"] = float(dbz)
    cfg_detect["min_area_km2"] = float(min_area)
    cells_by_frame = []
    labels_by_frame = []
    for idx, raster in enumerate(rasters):
        cells = detect_mod.detect_cells(raster, cfg_detect)
        for c in cells:
            c.frame_index = idx
        cells_by_frame.append(cells)
        labels_by_frame.append(_binary_labels(raster, cfg_detect))

    bundle = models.EngineBundle("ok", rasters[-1].time_iso, "structure_sweep")
    bundle.frames = list(rasters)
    bundle.cells_by_frame = cells_by_frame
    bundle.frame_slots_ms = [int(r.time_ms) for r in rasters]
    bundle.frame_slots_iso = [r.time_iso for r in rasters]
    bundle.raster_by_frame_index = {i: r for i, r in enumerate(rasters)}

    tracker = track_mod.Tracker(
        cfg["tracking"], cfg_scoring=cfg.get("scoring"), history_dir=None,
        coasting_cfg=cfg.get("coasting"))
    for idx, cells in enumerate(cells_by_frame):
        tracker.update(cells, idx, frame_ts_ms=rasters[idx].time_ms,
                       frame_context={"valid": True})
    tracker.complete_cycles()
    bundle.tracks = tracker.finalize(cfg_scoring=cfg.get("scoring"))

    storm_cfg = cfg["storm"]
    bundle.storm_objects_by_frame = [
        agg_mod.aggregate_frame(cells, idx, storm_cfg)
        for idx, cells in enumerate(cells_by_frame)]
    storm_tracker = storm_mod.StormObjectTracker(
        storm_cfg, cfg_scoring=cfg.get("scoring"), history_dir=None,
        coasting_cfg=cfg.get("coasting"))
    for idx, objs in enumerate(bundle.storm_objects_by_frame):
        storm_tracker.update(objs, idx, frame_ts_ms=rasters[idx].time_ms,
                             frame_context={"valid": True})
    storm_tracker.complete_cycles()
    bundle.storm_tracks = storm_tracker.finalize(cfg_scoring=cfg.get("scoring"))

    # Forma 2D per frame: PCA della maschera (non bbox).
    shape_cell = [{} for _ in rasters]
    shape_obj = [{} for _ in rasters]
    for idx in range(len(rasters)):
        lab = labels_by_frame[idx]
        for cell in cells_by_frame[idx]:
            m, _r, _c = _cell_mask(lab, cell)
            shape_cell[idx][cell.cell_id] = (
                round(_pca_elongation(m), 4) if m is not None else 1.0)
        for obj in bundle.storm_objects_by_frame[idx]:
            m, _r, _c = _object_mask(lab, obj)
            shape_obj[idx][obj.storm_object_id] = (
                round(_pca_elongation(m), 4) if m is not None else 1.0)
    return {
        "bundle": bundle, "rasters": rasters, "cells_by_frame": cells_by_frame,
        "labels_by_frame": labels_by_frame, "shape_cell": shape_cell,
        "shape_obj": shape_obj, "dbz": float(dbz),
        "min_area": float(min_area), "cfg_detect": cfg_detect,
    }


# ---------------------------------------------------------------------------
# Regola 2D (stessa precedenza di advanced.classify_morphology)
# ---------------------------------------------------------------------------
def _classify_2d(cand, shape, th):
    """(classe, regola) con la precedenza del classificatore di produzione.

    Differenza DICHIARATA: se `mcs_require_echo_top` e' False la regola MCS
    non pretende echo_top (che nel 2D e' null) ma richiede max_dbz >= 45;
    e' la variante "MCS 2D" del sweep, non il comportamento di produzione.
    """
    elong = shape.get("elongation")
    cells = cand.get("cell_count")
    area = cand.get("area_km2")
    dur = ((cand.get("motion") or {}).get("duration_min"))
    max_dbz = cand.get("max_dbz")
    echo_top = cand.get("echo_top_m")

    if (elong is not None and float(elong) >= th["elongation"]
            and cells is not None and int(cells) >= SQUALL_MIN_CELLS):
        return "SQUALL_LINE", "elongation_and_cell_count"
    mcs_echo_ok = (True if not th["mcs_require_echo_top"]
                   else (echo_top is not None
                         and float(echo_top) >= MCS_MIN_ECHO_TOP_M))
    if (area is not None and float(area) >= th["mcs_area"]
            and dur is not None and float(dur) >= th["mcs_duration"]
            and cells is not None and int(cells) >= MCS_MIN_CELLS
            and max_dbz is not None and float(max_dbz) >= MCS_MIN_DBZ
            and mcs_echo_ok):
        return "MCS", "large_area_long_duration_many_cells"
    if (cells is not None and int(cells) >= 2
            and area is not None and float(area) >= MULTICELL_MIN_AREA_KM2):
        return "MULTICELL", "cell_count_and_area"
    if (cells is not None and int(cells) <= 1
            and dur is not None and float(dur) <= SINGLE_CELL_MAX_DURATION_MIN):
        return "SINGLE_CELL", "single_cell_short_lived"
    return None, "morphology_inconclusive"


def _hysteresis(seq, n):
    """Smorza i cambi di classe: il nuovo valore vale dopo n frame consecutivi."""
    if n <= 1:
        return list(seq)
    out = []
    stable = None
    pending = None
    streak = 0
    for v in seq:
        if stable is None:
            if v is not None:
                stable = v
        elif v == stable:
            pending = None
            streak = 0
        elif v is None:
            # frame non classificabile: non e' un cambio di classe
            pass
        else:
            if v == pending:
                streak += 1
            else:
                pending = v
                streak = 1
            if streak >= n:
                stable = v
                pending = None
                streak = 0
        out.append(stable)
    return out


def class_sequence(track, track_type, data, th):
    """Sequenza di classi per frame (stessa regola su ogni prefisso)."""
    seq = []
    t0 = track.points[0].timestamp_ms
    for i, p in enumerate(track.points):
        frame = int(getattr(p, "frame_index", -1))
        if track_type == "storm_object":
            oid = getattr(p, "storm_object_id", None)
            elong = (data["shape_obj"][frame].get(oid)
                     if 0 <= frame < len(data["shape_obj"]) else None)
            cells = getattr(p, "cell_count", 1)
        else:
            cid = getattr(p, "cell_id", None)
            elong = (data["shape_cell"][frame].get(cid)
                     if 0 <= frame < len(data["shape_cell"]) else None)
            cells = 1
        cand = {
            "cell_count": cells,
            "area_km2": getattr(p, "area_km2", None),
            "max_dbz": getattr(p, "max_dbz", None),
            "n_points": i + 1,
            "motion": {"duration_min": (p.timestamp_ms - t0) / 60000.0},
            "echo_top_m": None,
        }
        value, rule = _classify_2d(
            cand, {"elongation": elong, "bow_score": None}, th)
        seq.append(value)
    return seq


def _transitions(seq):
    return sum(1 for a, b in zip(seq, seq[1:]) if a != b)


def _class_transitions(seq):
    return sum(1 for a, b in zip(seq, seq[1:])
               if a is not None and b is not None and a != b)


# ---------------------------------------------------------------------------
# Geometria: maschera pubblicata (core + buffer) dal dato, non dal poligono
# ---------------------------------------------------------------------------
def _disk(radius_px):
    import scipy.ndimage as ndi
    r = int(radius_px)
    if r <= 0:
        return None
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


def _component_nearest(core_crop, raster, r0, c0, lon, lat):
    """Componente connessa piu' vicina a (lon,lat). Ritorna (mask, dist_km)."""
    import scipy.ndimage as ndi
    lab, n = ndi.label(core_crop, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return None, None
    best = None
    for idx in range(1, n + 1):
        m = lab == idx
        area = int(m.sum())
        if area < 2:
            continue
        rr, cc = ndi.center_of_mass(m)
        rlon, rlat = raster.pixel_to_lonlat(r0 + rr, c0 + cc)
        dist = _haversine_km((lon, lat), (rlon, rlat))
        if best is None or dist < best[1]:
            best = (m, dist, area)
    if best is None:
        return None, None
    return best[0], best[1]


def structure_mask(record, track, data, core_dbz, buffer_km,
                   core_mode="nearest"):
    """Maschera pubblicata (crop, r0, c0) per UN record.

    * core_dbz == soglia di detection -> inviluppo (unione delle celle);
    * core_dbz > soglia, core_mode="nearest" -> componente >= core_dbz piu'
      vicina alla posizione del record (comportamento del fix "core" 1.3.0.80);
    * core_dbz > soglia, core_mode="all" -> UNIONE di tutti i nuclei
      >= core_dbz con area >= 4 km2, cap `MAX_CORES_PUBLISHED` per area
      decrescente. E' la rappresentazione MultiPolygon della struttura:
      un sistema con piu' nuclei non perde quelli lontani dal centroide.
    * fallback: UNA componente dell'inviluppo senza buffer;
    * buffer_km > 0 -> dilatazione euclidea della maschera scelta.
    """
    import scipy.ndimage as ndi
    # Il punto COASTED non e' un'osservazione: porta `storm_object_id=None` e
    # una posizione stimata. La geometria si risolve sull'ULTIMA OSSERVAZIONE
    # (stessa regola che il prodotto deve applicare in _resolve_outline).
    obs = [p for p in track.points
           if getattr(p, "observed", True)
           and int(getattr(p, "frame_index", -1)) >= 0]
    last = obs[-1] if obs else track.points[-1]
    frame = int(getattr(last, "frame_index", -1))
    if not (0 <= frame < len(data["rasters"])):
        return None
    raster = data["rasters"][frame]
    lab = data["labels_by_frame"][frame]
    cells = []
    if record.get("track_type") == "storm_object":
        oid = getattr(last, "storm_object_id", None)
        for obj in data["bundle"].storm_objects_by_frame[frame]:
            if obj.storm_object_id == oid:
                cells = list(obj.cells or [])
                break
    else:
        cid = getattr(last, "cell_id", None)
        for cell in data["cells_by_frame"][frame]:
            if cell.cell_id == cid:
                cells = [cell]
                break
    if not cells:
        # fallback per identita': cerca la cella in TUTTI i frame (stessa
        # cell_id non esiste due volte; nessuna geometria inventata)
        cid = getattr(last, "cell_id", None)
        oid = getattr(last, "storm_object_id", None)
        for fi in range(len(data["rasters"])):
            if cells:
                break
            if oid is not None:
                for obj in data["bundle"].storm_objects_by_frame[fi]:
                    if obj.storm_object_id == oid:
                        cells = list(obj.cells or [])
                        frame = fi
                        lab = data["labels_by_frame"][fi]
                        raster = data["rasters"][fi]
                        break
            if cells:
                break
            if cid is not None:
                for cell in data["cells_by_frame"][fi]:
                    if cell.cell_id == cid:
                        cells = [cell]
                        frame = fi
                        lab = data["labels_by_frame"][fi]
                        raster = data["rasters"][fi]
                        break
    if not cells:
        return None

    masks = []
    r0 = c0 = 10 ** 9
    r1 = c1 = -1
    for cell in cells:
        m, rr, cc = _cell_mask(lab, cell)
        if m is None:
            continue
        masks.append((m, rr, cc))
        r0 = min(r0, rr); c0 = min(c0, cc)
        r1 = max(r1, rr + m.shape[0] - 1)
        c1 = max(c1, cc + m.shape[1] - 1)
    if not masks:
        return None
    envelope = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=bool)
    for m, rr, cc in masks:
        envelope[rr - r0:rr - r0 + m.shape[0],
                 cc - c0:cc - c0 + m.shape[1]] |= m

    px_km = math.sqrt(max(float(raster.pixel_area_km2), 1e-9))
    _pos = getattr(last, "lonlat", None)
    if _pos and len(_pos) >= 2:
        lon, lat = float(_pos[0]), float(_pos[1])
    else:
        lon, lat = float(record["position"][0]), float(record["position"][1])
    core = None
    if float(core_dbz) > float(data["dbz"]) + 1e-9:
        valid = np.asarray(raster.valid_mask[r0:r1 + 1, c0:c1 + 1], dtype=bool)
        z = np.asarray(raster.data[r0:r1 + 1, c0:c1 + 1], dtype="float64")
        core_crop = envelope & valid & (z >= float(core_dbz))
        if core_mode == "all":
            lab, n = ndi.label(core_crop, structure=np.ones((3, 3), bool))
            comps = []
            for idx in range(1, n + 1):
                cm = lab == idx
                area_core = float(cm.sum()) * raster.pixel_area_km2
                if area_core >= MIN_CORE_AREA_KM2:
                    comps.append((area_core, cm))
            comps.sort(key=lambda x: -x[0])
            if comps:
                core = np.zeros_like(core_crop)
                for _area, cm in comps[:MAX_CORES_PUBLISHED]:
                    core |= cm
        else:
            core, _dist = _component_nearest(
                core_crop, raster, r0, c0, lon, lat)
            if core is not None:
                area_core = core.sum() * raster.pixel_area_km2
                if area_core < MIN_CORE_AREA_KM2:
                    core = None
    if core is None:
        # inviluppo: una componente connessa (quella piu' vicina), non l'unione
        if float(core_dbz) > float(data["dbz"]) + 1e-9:
            core, _dist = _component_nearest(envelope, raster, r0, c0, lon, lat)
            if core is None:
                core = envelope
            buffer_km = 0.0          # fallback: nessun buffer (dichiarato)
        else:
            core = envelope

    if float(buffer_km) > 0:
        radius = int(math.ceil(float(buffer_km) / px_km))
        disk = _disk(radius)
        if disk is not None:
            core = ndi.binary_dilation(core, structure=disk)
            r0 -= radius
            c0 -= radius
    if not bool(core.any()):
        return None
    return core, int(r0), int(c0), int(frame)


def _haversine_km(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = (math.sin(dp / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * r * math.asin(min(1.0, math.sqrt(h)))


# ---------------------------------------------------------------------------
# Valutazione di una combinazione su un'ancora
# ---------------------------------------------------------------------------
def evaluate_anchor(anchor, data, combo):
    cfg = data["cfg"]
    cs_cfg = _deep(cfg["convective_structure"])
    cs_cfg["candidate_soi"] = float(combo["candidate_soi"])
    cs_cfg["min_frames"] = int(combo["persistence"])
    cs_cfg["intensity_core_dbz"] = float(cs_cfg.get("intensity_core_dbz", 45.0))

    bundle = data["bundle"]
    th = {
        "elongation": float(combo["elongation"]),
        "mcs_area": float(combo["mcs_area"]),
        "mcs_duration": float(combo["mcs_duration"]),
        "mcs_require_echo_top": bool(combo["mcs_require_echo_top"]),
    }

    raw = []
    track_map = {}
    for track, track_type in ([(t, "cell") for t in bundle.tracks]
                              + [(t, "storm_object")
                                 for t in bundle.storm_tracks]):
        if len(track.points) < int(combo["persistence"]):
            continue
        soi, level, candidate, gates, comps, _w = cs_mod._integral_soi(
            track, cs_cfg)
        if not candidate:
            continue
        rec = cs_mod._candidate_dict(
            bundle, track, track_type, cs_cfg, soi, level,
            candidate, gates, comps)
        raw.append(rec)
        track_map[(track_type, track.track_id)] = track
    records = cs_mod.dedup_records(raw)
    records, skipped = cs_mod.split_publishable(records)

    # contesto geometrico comune
    last_idx = len(data["rasters"]) - 1
    domain_km2 = float(np.asarray(
        data["rasters"][last_idx].valid_mask, dtype=bool).sum()
        * data["rasters"][last_idx].pixel_area_km2)
    severe_union = np.zeros(
        (data["rasters"][last_idx].rows, data["rasters"][last_idx].cols),
        dtype=bool)
    any_union = np.zeros_like(severe_union)

    events = anchor.get("events") or []
    event_pixels = []
    raster0 = data["rasters"][last_idx]
    for ev in events:
        station, lat, lon, typ, qc, ts = ev
        try:
            r, c = raster0.lonlat_to_pixel(lon, lat)
        except Exception:
            r, c = -1, -1
        event_pixels.append((station, typ, qc, ts, int(r), int(c),
                            0 <= r < raster0.rows and 0 <= c < raster0.cols))

    per_record = []
    class_counts = {}
    transitions = class_transitions = pairs = 0
    tracks_eval = tracks_changed = 0
    records_with_geom = 0
    null_class = 0
    for rec in records:
        key = (rec.get("track_type"), rec.get("track_id"))
        track = track_map.get(key)
        if track is None:
            continue
        seq = class_sequence(track, rec.get("track_type"), data, th)
        sm = _hysteresis(seq, int(combo["hysteresis"]))
        final = sm[-1] if sm else None
        t = _transitions(sm)
        ct = _class_transitions(sm)
        transitions += t
        class_transitions += ct
        pairs += max(0, len(sm) - 1)
        tracks_eval += 1
        if t > 0:
            tracks_changed += 1
        if final is None:
            null_class += 1
        else:
            class_counts[final] = class_counts.get(final, 0) + 1
        geom = structure_mask(rec, track, data, float(combo["core_dbz"]),
                              float(combo["buffer_km"]),
                              combo.get("core_mode", "nearest"))
        severe = (str(rec.get("level")) in ("strong", "intense")
                  and final is not None)
        area_km2 = 0.0
        if geom is not None:
            m, r0, c0, frame = geom
            records_with_geom += 1
            area_km2 = float(m.sum()) * raster0.pixel_area_km2
            rr0 = max(r0, 0); cc0 = max(c0, 0)
            rr1 = min(r0 + m.shape[0], severe_union.shape[0])
            cc1 = min(c0 + m.shape[1], severe_union.shape[1])
            if rr1 > rr0 and cc1 > cc0:
                sub = m[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0]
                any_union[rr0:rr1, cc0:cc1] |= sub
                if severe:
                    severe_union[rr0:rr1, cc0:cc1] |= sub
        per_record.append({
            "structure_id": rec.get("structure_id"),
            "track_type": rec.get("track_type"),
            "track_id": rec.get("track_id"),
            "level": rec.get("level"),
            "soi": rec.get("soi"),
            "class": final,
            "class_raw_last": seq[-1] if seq else None,
            "class_seq": seq,
            "class_seq_smoothed": sm,
            "transitions": t,
            "class_transitions": ct,
            "severe": bool(severe),
            "area_km2": round(area_km2, 2),
            "geometry": geom is not None,
        })

    hits = []
    for (station, typ, qc, ts, r, c, on_grid) in event_pixels:
        hit = bool(on_grid and severe_union[r, c])
        hit_any = bool(on_grid and any_union[r, c])
        hits.append({"station": station, "type": typ, "qc": qc, "ts": ts,
                     "on_grid": bool(on_grid), "hit": hit,
                     "hit_any_structure": hit_any})
    n_events = len(hits)
    n_hits = sum(1 for h in hits if h["hit"])
    area_frac = (float(severe_union.sum()) * raster0.pixel_area_km2
                 / domain_km2) if domain_km2 > 0 else None

    # entropia normalizzata su log(3) (classi 2D producibili)
    total_cls = sum(class_counts.values())
    if total_cls:
        ent = 0.0
        for v in class_counts.values():
            p = v / total_cls
            ent -= p * math.log(p)
        entropy = ent / math.log(3.0)
        max_share = max(class_counts.values()) / total_cls
    else:
        entropy = 0.0
        max_share = 1.0

    return {
        "anchor": anchor["id"],
        "iso": anchor["iso"],
        "dbz": data["dbz"],
        "min_area": data["min_area"],
        "domain_km2": round(domain_km2, 1),
        "records": len(records),
        "records_skipped_incoherent": int(skipped),
        "records_with_geometry": records_with_geom,
        "null_class": null_class,
        "class_counts": class_counts,
        "entropy": round(entropy, 4),
        "max_share": round(max_share, 4),
        "area_frac": round(area_frac, 5) if area_frac is not None else None,
        "severe_area_km2": round(float(severe_union.sum())
                                 * raster0.pixel_area_km2, 1),
        "events": hits,
        "hits": n_hits,
        "n_events": n_events,
        "tracks": tracks_eval,
        "tracks_changed": tracks_changed,
        "transitions": transitions,
        "class_transitions": class_transitions,
        "frame_pairs": pairs,
        "transition_rate": (round(transitions / pairs, 4) if pairs else 0.0),
        "class_transition_rate": (round(class_transitions / pairs, 4)
                                  if pairs else 0.0),
        "per_record": per_record,
    }


# ---------------------------------------------------------------------------
# Combinazioni
# ---------------------------------------------------------------------------
def baseline_combo():
    return dict(BASELINE)


def sample_combos(n=48, seed=20261127):
    """Baseline + combinazioni casuali con seed fisso (screening)."""
    rng = random.Random(seed)
    out = [baseline_combo()]
    seen = {tuple(sorted(out[0].items()))}
    # copertura strutturata: per ogni coppia di detection, almeno 2 combo
    while len(out) < n:
        c = {
            "dbz": rng.choice(DBZ_GRID),
            "min_area": rng.choice(MIN_AREA_GRID),
            "persistence": rng.choice(PERSISTENCE_GRID),
            "candidate_soi": rng.choice(SOI_GRID),
            "hysteresis": rng.choice(HYSTERESIS_GRID),
            "elongation": rng.choice(ELONGATION_GRID),
            "mcs_area": rng.choice(MCS_AREA_GRID),
            "mcs_duration": rng.choice(MCS_DURATION_GRID),
            "core_dbz": rng.choice(CORE_DBZ_GRID),
            "buffer_km": rng.choice(BUFFER_GRID),
            "core_mode": rng.choice(CORE_MODE_GRID),
            "mcs_require_echo_top": rng.choice((True, False)),
        }
        k = tuple(sorted(c.items()))
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def neighbors(combo):
    """Vicini a un passo (per hill climbing), senza duplicati."""
    grids = {
        "dbz": DBZ_GRID, "min_area": MIN_AREA_GRID,
        "persistence": PERSISTENCE_GRID, "candidate_soi": SOI_GRID,
        "hysteresis": HYSTERESIS_GRID, "elongation": ELONGATION_GRID,
        "mcs_area": MCS_AREA_GRID, "mcs_duration": MCS_DURATION_GRID,
        "core_dbz": CORE_DBZ_GRID, "buffer_km": BUFFER_GRID,
        "core_mode": CORE_MODE_GRID,
        "mcs_require_echo_top": (True, False),
    }
    out = []
    for key, vals in grids.items():
        if key == "mcs_require_echo_top":
            cur = bool(combo.get(key, False))
            nv = not cur
            c = dict(combo); c[key] = nv; out.append(c)
            continue
        try:
            idx = list(vals).index(combo[key])
        except ValueError:
            continue
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(vals):
                c = dict(combo); c[key] = vals[j]; out.append(c)
    return out


# ---------------------------------------------------------------------------
# Aggregazione e obiettivo
# ---------------------------------------------------------------------------
def summarize(combo, anchor_rows):
    hits = sum(r["hits"] for r in anchor_rows)
    events = sum(r["n_events"] for r in anchor_rows)
    pod = (hits / events) if events else None
    area_vals = [r["area_frac"] for r in anchor_rows
                 if r.get("area_frac") is not None]
    area_med = float(np.median(area_vals)) if area_vals else None
    area_max = float(np.max(area_vals)) if area_vals else None
    trans = sum(r["transitions"] for r in anchor_rows)
    ctrans = sum(r["class_transitions"] for r in anchor_rows)
    pairs = sum(r["frame_pairs"] for r in anchor_rows)
    tracks = sum(r["tracks"] for r in anchor_rows)
    changed = sum(r["tracks_changed"] for r in anchor_rows)
    records = sum(r["records"] for r in anchor_rows)
    class_counts = {}
    for r in anchor_rows:
        for k, v in (r.get("class_counts") or {}).items():
            class_counts[k] = class_counts.get(k, 0) + v
    total_cls = sum(class_counts.values())
    if total_cls:
        ent = 0.0
        for v in class_counts.values():
            p = v / total_cls
            ent -= p * math.log(p)
        entropy = ent / math.log(3.0)
        max_share = max(class_counts.values()) / total_cls
    else:
        entropy = 0.0
        max_share = 1.0
    transition_rate = (trans / pairs) if pairs else 0.0
    reasons = []
    if area_med is not None and area_med > MAX_AREA_FRAC:
        reasons.append("area_frac>%.2f" % MAX_AREA_FRAC)
    if records >= MIN_RECORDS_FOR_SHARE_GATE and max_share > MAX_CLASS_SHARE:
        reasons.append("max_share>%.2f" % MAX_CLASS_SHARE)
    score = None
    if pod is not None and not reasons:
        score = pod - 0.50 * transition_rate + 0.10 * entropy
    return {
        "combo": dict(combo),
        "anchors": len(anchor_rows),
        "events": events,
        "hits": hits,
        "pod": round(pod, 4) if pod is not None else None,
        "area_frac_median": round(area_med, 5) if area_med is not None else None,
        "area_frac_max": round(area_max, 5) if area_max is not None else None,
        "records": records,
        "tracks": tracks,
        "tracks_changed": changed,
        "tracks_changed_pct": (round(100.0 * changed / tracks, 2)
                               if tracks else None),
        "transitions": trans,
        "class_transitions": ctrans,
        "frame_pairs": pairs,
        "transition_rate": round(transition_rate, 4),
        "class_counts": class_counts,
        "entropy": round(entropy, 4),
        "max_share": round(max_share, 4),
        "rejected": bool(reasons),
        "reject_reasons": reasons,
        "score": round(float(score), 4) if score is not None else None,
        "per_anchor": anchor_rows,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--precache", action="store_true")
    ap.add_argument("--screening", action="store_true")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--neighbors", default=None,
                    help="JSON con i top combo: genera i vicini")
    ap.add_argument("--out-combos", default=None)
    ap.add_argument("--combo-file", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--anchor-id", default=None,
                    help="esegue una sola ancora (A/B mirato)")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(RESULTS, exist_ok=True)

    which = "final" if args.final else "screening"
    if args.precache:
        precache(which, args.frames, args.anchor_id)
        return
    if args.neighbors:
        with open(args.neighbors, encoding="utf-8") as fh:
            rows = json.load(fh)
        out = []
        seen = set()
        for row in rows:
            c = row.get("combo") or row
            for n in [c] + neighbors(c):
                k = tuple(sorted(n.items()))
                if k in seen:
                    continue
                seen.add(k)
                out.append(n)
        dst = args.out_combos or os.path.join(RESULTS, "combos_neighbors.json")
        with open(dst, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        print("wrote", dst, len(out), "combos")
        return
    if args.merge:
        files = sorted(glob.glob(os.path.join(RESULTS, "shard_*.json")))
        rows = []
        for f in files:
            with open(f, encoding="utf-8") as fh:
                rows.extend(json.load(fh))
        dst = os.path.join(RESULTS, "sweep_%s%s_all.json" % (
            which, ("_" + args.tag) if args.tag else ""))
        with open(dst, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        print("merged", len(rows), "from", len(files), "->", dst)
        return

    combos = sample_combos(48)
    if args.combo_file:
        with open(args.combo_file, encoding="utf-8") as fh:
            combos = json.load(fh)
    if args.limit:
        combos = combos[:args.limit]
    # Raggruppa per variante di DETECTION: la stessa (dbz,min_area) viene
    # calcolata UNA volta per ancora, non una volta per combinazione.
    by_pair = {}
    for i, c in enumerate(combos):
        key = (float(c["dbz"]), float(c["min_area"]))
        by_pair.setdefault(key, []).append(i)
    pairs = sorted(by_pair)
    mine_pairs = [p for j, p in enumerate(pairs) if j % args.shards == args.shard]
    cfg = _anchor_cfg()
    acc = {i: [] for i in range(len(combos))}
    for a in anchors(which):
        if args.anchor_id and a["id"] != args.anchor_id:
            continue
        try:
            _a, rasters = load_rasters(a["id"], args.frames)
        except FileNotFoundError:
            print("MISSING CACHE", a["id"])
            continue
        for pair in mine_pairs:
            data = run_detection(a, rasters, cfg, pair[0], pair[1])
            data["cfg"] = cfg
            for i in by_pair[pair]:
                t0 = time.time()
                res = evaluate_anchor(a, data, combos[i])
                res["seconds"] = round(time.time() - t0, 2)
                acc[i].append(res)
            print("anchor", a["id"], "pair", pair, "combos",
                  len(by_pair[pair]), flush=True)
    all_rows = []
    for i, combo in enumerate(combos):
        if not acc[i]:
            continue
        summary = summarize(combo, acc[i])
        all_rows.append(summary)
        print("combo", {k: summary["combo"][k] for k in
                        ("dbz", "min_area", "persistence", "candidate_soi",
                         "hysteresis", "elongation", "core_dbz", "buffer_km",
                         "core_mode")},
              "pod", summary["pod"], "area", summary["area_frac_median"],
              "trans", summary["transition_rate"], "score", summary["score"],
              "rejected", summary["rejected"], flush=True)
    dst = os.path.join(RESULTS, "shard_%s_%d_%d%s.json" % (
        which, args.shard, args.shards,
        ("_" + args.tag) if args.tag else ""))
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(all_rows, fh, indent=1)
    print("saved", dst, len(all_rows))


if __name__ == "__main__":
    main()
