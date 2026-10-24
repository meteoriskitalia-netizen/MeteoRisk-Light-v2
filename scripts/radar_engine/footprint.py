#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — footprint.py (1.2.4.21)

POLIGONO (lon/lat) del FOOTPRINT di una cella e di una struttura convettiva.

Perche' esiste: fino alla 1.2.4.20 il motore pubblicava celle come PUNTO
(centroide) e storm object con la sola convessita' dei CENTROIDI delle celle
(`aggregation.convex_hull`). Un collaudo che accoppia il centroide non misura
il prodotto: un rapporto METAR che cade DENTRO una componente connessa di
4.659 km^2 puo' stare a 20 km dal centroide (e' il caso di LIMF 2026-07-01,
retro/REPORT.md §5). Questo modulo espone il footprint VERO, cioe' la
componente connessa (soglia `detect.dbz_threshold`) ricalcolata sulla VMI del
frame e poligonizzata, senza cambiare nessuna soglia, formula o peso.

Cosa NON fa:
  * non tocca le soglie (usa `config["detect"]`: `dbz_threshold`,
    `morph_radius_px`, `connectivity_8` sono gli stessi valori con cui il
    motore ha selezionato la cella);
  * non fa matching geometrico fra blob: l'identita' e' `cell_id`, risolta
    con l'etichetta del frame che ha lo STESSO `bbox` e lo STESSO
    `pixel_count` della cella (firma unica); per uno storm object si usa
    l'unione delle maschere delle celle membre;
  * non entra nell'output operativo: scrive un GeoJSON SOLO quando il CLI
    riceve `--footprints <path>`. Senza quel flag l'output del motore e'
    byte-identico a quello della 1.2.4.20.

Difetto noto e dichiarato: il pixel del centroide puo' cadere FUORI dalla
componente (componente concava). E' il motivo per cui l'overhang della
1.2.4.18-1.2.20 talvolta ricade su `candidate_window`: qui il seed del
centroide resta solo come FALLBACK, dopo l'identita' per bbox+conteggio.
L'overhang NON e' stato toccato in questa release (i suoi numeri restano
quelli della 1.2.4.20).

Rappresentazione: MultiPolygon GeoJSON in EPSG:4326, anelli esterni con i
buchi (rasterio.features.shapes li produce gia' separati), coordinate
arrotondate a 5 decimali (~1 m). I vertici collineari vengono rimossi: la
geometria e' identica, il file e' ~4x piu' piccolo.
"""

import json
import os

import numpy as np

# Livelli dei bandi SOI che contano come "criterio severo" (convective_
# structure.classify_level): sono gli stessi nomi usati dal pannello.
SEVERE_LEVELS = ("strong", "intense")

_EPS = 1e-12
_LABELS = {}


# ---------------------------------------------------------------------------
# Componente connessa: seed per pixel (definizione storica, usata dall'overhang)
# ---------------------------------------------------------------------------
def component_mask(arr, valid, threshold, seed_rc):
    """Maschera della componente connessa di `arr >= threshold` che contiene il
    pixel (seed_rc), nello stesso array. None se il seed non e' sopra soglia.

    Stessa semantica di `main._component_containing` (8-connessi, `valid` =
    validita' dato, non soglia meteo): da qui in poi esiste UNA sola
    implementazione, usata dall'overhang."""
    import scipy.ndimage as ndi

    binary = np.asarray(valid) & (np.asarray(arr) >= float(threshold))
    if binary.size == 0:
        return None
    lab, _n = ndi.label(binary, structure=np.ones((3, 3), dtype=bool))
    r, c = int(round(seed_rc[0])), int(round(seed_rc[1]))
    if not (0 <= r < lab.shape[0] and 0 <= c < lab.shape[1]):
        return None
    lbl = int(lab[r, c])
    if lbl == 0:
        return None
    return lab == lbl


# ---------------------------------------------------------------------------
# Etichette del frame: identita' ESATTA della cella (bbox + pixel_count)
# ---------------------------------------------------------------------------
def _struct(radius, connectivity_8=True):
    """Stesso elemento strutturante di `detect._struct` (replica, non soglia)."""
    import scipy.ndimage as ndi

    if radius <= 0:
        return (np.ones((3, 3), dtype=bool) if connectivity_8
                else ndi.generate_binary_structure(2, 2))
    return np.ones((2 * radius + 1, 2 * radius + 1), dtype=bool)


def frame_labels(bundle, frame_index, cfg_detect):
    """(lab, sizes) del frame: stessa binarizzazione e stessa morfologia di
    `detect.detect_cells` (`dbz_threshold`, `morph_radius_px`,
    `connectivity_8` dalla config del motore: nessun valore nuovo).

    Cache per (bundle, frame): una sola passata di labelling per frame."""
    import scipy.ndimage as ndi

    rd = bundle.frames[int(frame_index)]
    key = (id(bundle), int(frame_index), rd.time_iso, rd.source_path)
    hit = _LABELS.get(key)
    if hit is not None:
        return hit
    thr = float(cfg_detect["dbz_threshold"])
    binary = np.asarray(rd.valid_mask) & (np.asarray(rd.data) >= thr)
    radius = int(cfg_detect.get("morph_radius_px", 0) or 0)
    conn8 = bool(cfg_detect.get("connectivity_8", True))
    if radius > 0:
        se = _struct(radius, conn8)
        binary = ndi.binary_opening(binary, structure=se)
        binary = ndi.binary_closing(binary, structure=se)
    lab, nlab = ndi.label(binary, structure=_struct(0, conn8))
    sizes = np.bincount(lab.ravel(), minlength=nlab + 1)
    _LABELS[key] = (lab, sizes)
    return _LABELS[key]


def _label_bbox(lab, value):
    rows, cols = np.nonzero(lab == value)
    if rows.size == 0:
        return None
    return (int(rows.min()), int(cols.min()), int(rows.max()), int(cols.max()))


def component_for_cell(raster, cell, cfg_detect, labels=None):
    """Componente connessa della CELLA, identita' per bbox + numero di pixel.

    Il pixel del centroide NON e' un'identita' affidabile (una componente
    concava ha il centroide fuori dai propri pixel): si usa l'etichetta del
    frame con lo STESSO `pixel_count` e lo STESSO `bbox` della cella, firma
    unica per costruzione. Fallback dichiarato: pixel del centroide.

    Ritorna (maschera, window, metodo)."""
    bmin_r, bmin_c, bmax_r, bmax_c = [int(v) for v in cell.bbox]
    rr0 = max(0, bmin_r)
    cc0 = max(0, bmin_c)
    rr1 = min(int(raster.rows) - 1, bmax_r)
    cc1 = min(int(raster.cols) - 1, bmax_c)
    win = (rr0, rr1, cc0, cc1)
    target = int(getattr(cell, "pixel_count", 0) or 0)
    if labels is not None:
        lab, sizes = labels
        sub = lab[rr0:rr1 + 1, cc0:cc1 + 1]
        present = [int(v) for v in np.unique(sub) if int(v) > 0]
        exact = [v for v in present
                 if int(sizes[v]) == target
                 and _label_bbox(lab, v) == (bmin_r, bmin_c, bmax_r, bmax_c)]
        if exact:
            out = np.zeros(sub.shape, dtype=bool)
            for v in exact:
                out |= (sub == v)
            return out, win, "label_bbox_and_pixel_count"
        same_size = [v for v in present if int(sizes[v]) == target]
        if len(same_size) == 1:
            return (sub == same_size[0]), win, "label_pixel_count"
    # fallback: semantica storica del seed (pixel del centroide)
    sub_data = raster.data[rr0:rr1 + 1, cc0:cc1 + 1]
    sub_valid = raster.valid_mask[rr0:rr1 + 1, cc0:cc1 + 1]
    seed = (float(cell.centroid_raster[0]) - rr0,
            float(cell.centroid_raster[1]) - cc0)
    comp = component_mask(sub_data, sub_valid, cfg_detect["dbz_threshold"],
                          seed)
    if comp is None:
        return None, win, "cell_mask_not_resolved"
    return comp, win, "centroid_seed"


def _window(cells, raster, pad=1):
    """Finestra raster (r0, r1, c0, c1) che contiene i bbox di tutte le celle."""
    r0 = c0 = 10 ** 9
    r1 = c1 = -1
    for cell in cells:
        bmin_r, bmin_c, bmax_r, bmax_c = cell.bbox
        r0, c0 = min(r0, bmin_r), min(c0, bmin_c)
        r1, c1 = max(r1, bmax_r), max(c1, bmax_c)
    if r1 < 0:
        return None
    r0 = max(0, r0 - pad)
    c0 = max(0, c0 - pad)
    r1 = min(int(raster.rows) - 1, r1 + pad)
    c1 = min(int(raster.cols) - 1, c1 + pad)
    if r1 < r0 or c1 < c0:
        return None
    return int(r0), int(r1), int(c0), int(c1)


def masks_for_cells(cells, bundle, cfg_detect):
    """(window, mask, used, skipped, how) — unione delle componenti delle celle.

    Ogni cella e' risolta sul SUO frame con `component_for_cell`. L'unione e'
    la maschera del footprint della struttura."""
    if not cells:
        return None, None, 0, 0, {}
    rasters = _rasters_by_frame(bundle)
    frames = []
    for cell in cells:
        idx = int(getattr(cell, "frame_index", -1))
        if rasters.get(idx) is not None:
            frames.append(idx)
    if not frames:
        return None, None, 0, len(cells), {"cell_frame_missing": len(cells)}
    rd0 = rasters[frames[0]]
    win = _window(cells, rd0)
    if win is None:
        return None, None, 0, len(cells), {"empty_window": len(cells)}
    r0, r1, c0, c1 = win
    for idx in frames:
        rd = rasters[idx]
        r1 = min(r1, int(rd.rows) - 1)
        c1 = min(c1, int(rd.cols) - 1)
    if r1 < r0 or c1 < c0:
        return None, None, 0, len(cells), {"empty_window": len(cells)}
    mask = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=bool)
    used = 0
    how = {}
    for cell in cells:
        idx = int(getattr(cell, "frame_index", -1))
        rd = rasters.get(idx)
        if rd is None:
            how["cell_frame_missing"] = how.get("cell_frame_missing", 0) + 1
            continue
        labels = frame_labels(bundle, idx, cfg_detect)
        comp, (rr0, rr1, cc0, cc1), method = component_for_cell(
            rd, cell, cfg_detect, labels=labels)
        how[method] = how.get(method, 0) + 1
        if comp is None:
            continue
        a0, b0 = max(rr0, r0), max(cc0, c0)
        a1, b1 = min(rr1, r1), min(cc1, c1)
        if a1 < a0 or b1 < b0:
            how["outside_window"] = how.get("outside_window", 0) + 1
            continue
        mask[a0 - r0:a1 - r0 + 1, b0 - c0:b1 - c0 + 1] |= \
            comp[a0 - rr0:a1 - rr0 + 1, b0 - cc0:b1 - cc0 + 1]
        used += 1
    if used == 0 or not bool(mask.any()):
        return None, None, 0, len(cells), how
    return (r0, r1, c0, c1), mask, used, len(cells) - used, how


# ---------------------------------------------------------------------------
# Poligonizzazione (rasterio.features.shapes) + pulizia dei vertici
# ---------------------------------------------------------------------------
def _dedupe(ring):
    """Rimuove i vertici collineari (geometria invariata) e i duplicati."""
    out = []
    for x, y in ring:
        if out and out[-1] == (x, y):
            continue
        out.append((x, y))
    if len(out) >= 3 and out[0] == out[-1]:
        out.pop()
    if len(out) < 3:
        return [(round(x, 5), round(y, 5)) for x, y in out]
    keep = []
    m = len(out)
    for i in range(m):
        x0, y0 = out[(i - 1) % m]
        x1, y1 = out[i]
        x2, y2 = out[(i + 1) % m]
        cross = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
        if abs(cross) <= _EPS:
            continue
        keep.append((x1, y1))
    if len(keep) < 3:
        keep = out
    ring = [(round(x, 5), round(y, 5)) for x, y in keep]
    ring.append(ring[0])
    return ring


def polygons_from_mask(mask, raster, window):
    """(MultiPolygon GeoJSON, n_vertici) della maschera, oppure None.

    `window` = (r0, r1, c0, c1) sulla griglia di `raster`. I bordi dei pixel
    sono trasformati con l'affine del GeoTIFF e poi in EPSG:4326 con il
    transformer pyproj del raster (nessuna georeferenziazione assunta)."""
    from affine import Affine
    from rasterio.features import shapes

    r0, _r1, c0, _c1 = window
    sub = np.ascontiguousarray(mask, dtype=np.uint8)
    if not sub.any():
        return None
    transform = raster.transform @ Affine.translation(int(c0), int(r0))
    polys = []
    for geom, value in shapes(sub, mask=sub.astype(bool), transform=transform,
                              connectivity=4):
        if not int(value):
            continue
        coords = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            coords = [coords]
        for rings in coords:
            conv = []
            for ring in rings:
                xs = [float(p[0]) for p in ring]
                ys = [float(p[1]) for p in ring]
                lon, lat = raster.geo_transform.transform(xs, ys)
                conv.append(_dedupe([(float(a), float(b))
                                     for a, b in zip(lon, lat)]))
            if conv and len(conv[0]) >= 4:
                polys.append(conv)
    if not polys:
        return None
    n_vertices = sum(len(r) for p in polys for r in p)
    return {"type": "MultiPolygon", "coordinates": polys}, n_vertices


def geometry_centroid(geom):
    """Centroide approssimato (media pesata dei baricentri degli anelli esterni)."""
    if not geom:
        return None
    sx = sy = sw = 0.0
    for poly in geom.get("coordinates") or []:
        if not poly:
            continue
        ring = poly[0]
        if len(ring) < 3:
            continue
        a = 0.0
        cx = cy = 0.0
        for i in range(len(ring) - 1):
            x0, y0 = ring[i]
            x1, y1 = ring[i + 1]
            cross = x0 * y1 - x1 * y0
            a += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        if abs(a) <= _EPS:
            continue
        a *= 0.5
        sx += cx / (6.0 * a) * abs(a)
        sy += cy / (6.0 * a) * abs(a)
        sw += abs(a)
    if sw <= _EPS:
        return None
    return [round(sx / sw, 5), round(sy / sw, 5)]


# ---------------------------------------------------------------------------
# Risoluzione del footprint di un record / di una cella
# ---------------------------------------------------------------------------
def _frame_index_by_iso(bundle, iso):
    if not iso:
        return None
    for idx, rd in enumerate(getattr(bundle, "frames", None) or []):
        if rd.time_iso == iso:
            return idx
    return None


def _rasters_by_frame(bundle):
    return {idx: rd for idx, rd in enumerate(getattr(bundle, "frames", None) or [])}


def _cells_for_record(bundle, record, frame_index):
    """Celle (identita' per cell_id / storm_object_id) del frame indicato."""
    storm_id = record.get("storm_object_id")
    frames = getattr(bundle, "storm_objects_by_frame", None) or []
    if storm_id is not None:
        order = []
        if frame_index is not None and 0 <= frame_index < len(frames):
            order.append(int(frame_index))
        order.extend(i for i in range(len(frames)) if i not in order)
        for idx in order:
            for obj in frames[idx] or []:
                if (str(getattr(obj, "storm_object_id", "")) == str(storm_id)
                        or str(getattr(obj, "track_id", "")) == str(storm_id)):
                    return (list(getattr(obj, "cells", None) or []),
                            "storm_object_cells")
    cell_id = record.get("cell_id")
    if cell_id is not None:
        pool = getattr(bundle, "cells_by_frame", None) or []
        if frame_index is not None and 0 <= frame_index < len(pool):
            for cell in pool[frame_index] or []:
                if cell.cell_id == cell_id:
                    return [cell], "cell_mask"
        for cells in pool:
            for cell in cells or []:
                if cell.cell_id == cell_id:
                    return [cell], "cell_mask"
    return [], "cell_not_found"


def record_footprint(bundle, record, cfg_detect):
    """Footprint GeoJSON del record convettivo AL SUO frame (last_seen).

    Ritorna dict {geometry, area_km2, ...} oppure {"null_reason": ...} —
    nessun poligono inventato."""
    threshold = float(cfg_detect["dbz_threshold"])
    frame_iso = record.get("last_seen") or record.get("first_seen")
    frame_index = _frame_index_by_iso(bundle, frame_iso)
    if frame_index is None:
        return {"null_reason": "frame_not_found", "frame_iso": frame_iso}
    cells, source = _cells_for_record(bundle, record, frame_index)
    if not cells:
        return {"null_reason": source, "frame_iso": frame_iso}
    win, mask, used, skipped, how = masks_for_cells(cells, bundle, cfg_detect)
    if win is None or mask is None:
        return {"null_reason": "cell_mask_not_resolved", "frame_iso": frame_iso,
                "resolution": how}
    raster = bundle.frames[frame_index]
    geom = polygons_from_mask(mask, raster, win)
    if geom is None:
        return {"null_reason": "polygonize_empty", "frame_iso": frame_iso,
                "resolution": how}
    geometry, n_vertices = geom
    return {
        "null_reason": None,
        "frame_iso": frame_iso,
        "frame_index": frame_index,
        "source": source,
        "cell_ids": [c.cell_id for c in cells],
        "cells_used": used,
        "cells_skipped": skipped,
        "resolution": how,
        "pixel_count": int(mask.sum()),
        "area_km2": round(float(mask.sum()) * float(raster.pixel_area_km2), 3),
        "vertices": n_vertices,
        "geometry": geometry,
        "centroid": geometry_centroid(geometry),
        "threshold_dbz": threshold,
    }


def cell_footprints(bundle, cfg_detect):
    """[(frame_index, frame_iso, cell, footprint_dict)] per OGNI cella."""
    rasters = _rasters_by_frame(bundle)
    out = []
    for idx, cells in enumerate(getattr(bundle, "cells_by_frame", None) or []):
        raster = rasters.get(idx)
        if raster is None:
            continue
        for cell in cells or []:
            win, mask, used, skipped, how = masks_for_cells([cell], bundle,
                                                            cfg_detect)
            if win is None or mask is None:
                out.append((idx, raster.time_iso, cell,
                            {"null_reason": "cell_mask_not_resolved",
                             "resolution": how}))
                continue
            geom = polygons_from_mask(mask, raster, win)
            if geom is None:
                out.append((idx, raster.time_iso, cell,
                            {"null_reason": "polygonize_empty",
                             "resolution": how}))
                continue
            geometry, n_vertices = geom
            out.append((idx, raster.time_iso, cell, {
                "null_reason": None,
                "frame_iso": raster.time_iso,
                "frame_index": idx,
                "source": "cell_mask",
                "cell_ids": [cell.cell_id],
                "cells_used": used,
                "cells_skipped": skipped,
                "resolution": how,
                "pixel_count": int(mask.sum()),
                "area_km2": round(float(mask.sum())
                                  * float(raster.pixel_area_km2), 3),
                "vertices": n_vertices,
                "geometry": geometry,
                "centroid": geometry_centroid(geometry),
            }))
    return out


# ---------------------------------------------------------------------------
# Output GeoJSON
# ---------------------------------------------------------------------------
def build_features(bundle, cfg_detect):
    """(features, stats): layer `cell` (ogni cella, ogni frame) e `structure`
    (ogni struttura convettiva, al suo frame). Deterministico: ordine di
    iterazione dei frame + cell_id + structure_id.

    Le celle/strutture senza geometria NON spariscono dal conteggio:
    finiscono in `*_without_geometry`."""
    stats = {"cells_total": 0, "cells_without_geometry": 0,
             "structures_total": 0, "structures_without_geometry": 0,
             "severe_structures": 0}
    feats = []
    for idx, iso, cell, fp in cell_footprints(bundle, cfg_detect):
        stats["cells_total"] += 1
        if fp.get("geometry") is None:
            stats["cells_without_geometry"] += 1
            continue
        feats.append({
            "type": "Feature",
            "geometry": fp["geometry"],
            "properties": {
                "layer": "cell",
                "frame_index": idx,
                "frame_iso": iso,
                "cell_id": cell.cell_id,
                "track_id": getattr(cell, "track_id", None),
                "structure_id": None,
                "level": None,
                "severe": False,
                "area_km2": fp["area_km2"],
                "pixel_count": fp["pixel_count"],
                "centroid": fp["centroid"],
                "max_dbz": round(float(cell.max_dbz), 2),
                "resolution": fp.get("resolution"),
                "threshold_dbz": float(cfg_detect["dbz_threshold"]),
                "source": "cell_mask",
            },
        })
    for rec in (getattr(bundle, "convective_structures", None) or []):
        stats["structures_total"] += 1
        level = rec.get("level")
        if str(level) in SEVERE_LEVELS:
            stats["severe_structures"] += 1
        fp = record_footprint(bundle, rec, cfg_detect)
        if fp.get("geometry") is None:
            stats["structures_without_geometry"] += 1
            feats.append({
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "layer": "structure",
                    "structure_id": rec.get("structure_id"),
                    "level": level,
                    "severe": str(level) in SEVERE_LEVELS,
                    "null_reason": fp.get("null_reason"),
                    "resolution": fp.get("resolution"),
                    "frame_iso": fp.get("frame_iso"),
                },
            })
            continue
        feats.append({
            "type": "Feature",
            "geometry": fp["geometry"],
            "properties": {
                "layer": "structure",
                "frame_index": fp["frame_index"],
                "frame_iso": fp["frame_iso"],
                "cell_id": rec.get("cell_id"),
                "cell_ids": fp["cell_ids"],
                "track_id": rec.get("track_id"),
                "track_type": rec.get("track_type"),
                "structure_id": rec.get("structure_id"),
                "level": level,
                "soi": rec.get("soi"),
                "severe": str(level) in SEVERE_LEVELS,
                "first_seen": rec.get("first_seen"),
                "last_seen": rec.get("last_seen"),
                "area_km2": fp["area_km2"],
                "pixel_count": fp["pixel_count"],
                "cells_used": fp["cells_used"],
                "cells_skipped": fp["cells_skipped"],
                "resolution": fp.get("resolution"),
                "vertices": fp["vertices"],
                "centroid": fp["centroid"],
                "position": rec.get("position"),
                "threshold_dbz": float(cfg_detect["dbz_threshold"]),
                "source": fp["source"],
            },
        })
    return feats, stats


def write_geojson(path, bundle, config=None):
    """Scrive il GeoJSON dei footprint (SOLO su richiesta esplicita del CLI).

    Ritorna un riassunto deterministico (conteggi), senza tempi di parete."""
    cfg_detect = dict((config or {}).get("detect") or {})
    cfg_detect.setdefault("dbz_threshold", 20.0)
    feats, stats = build_features(bundle, cfg_detect)
    payload = {
        "type": "FeatureCollection",
        "generator": "radar_engine.footprint (1.2.4.21)",
        "radar_timestamp": getattr(bundle, "radar_timestamp_iso", None),
        "threshold_dbz": float(cfg_detect["dbz_threshold"]),
        "features": feats,
    }
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=False,
                  separators=(",", ":"))
    os.replace(tmp, path)
    return {
        "path": os.path.basename(path),
        "cells": stats["cells_total"] - stats["cells_without_geometry"],
        "cells_without_geometry": stats["cells_without_geometry"],
        "structures": stats["structures_total"]
        - stats["structures_without_geometry"],
        "structures_without_geometry": stats["structures_without_geometry"],
        "severe_structures": stats["severe_structures"],
        "threshold_dbz": float(cfg_detect["dbz_threshold"]),
    }
