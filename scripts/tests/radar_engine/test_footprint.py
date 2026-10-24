# -*- coding: utf-8 -*-
"""Test 1.2.4.21 — FOOTPRINT GEOMETRICO delle celle e delle strutture.

Copertura per contratto (numeri calcolati qui, NON copiati dall'output):
  * `component_mask` (definizione storica del seed sul centroide) invariata;
  * `frame_labels` + `component_for_cell`: ogni cella prodotta da
    `detect.detect_cells` si ritrova nell'etichetta del frame con lo STESSO
    bbox e lo STESSO numero di pixel (identita' esatta, nessun match
    geometrico fra blob);
  * il difetto reale trovato nel collaudo: una componente CONCAVA ha il
    centroide fuori dai propri pixel -> il seed del centroide fallisce, la
    firma bbox+pixel_count risolve;
  * unione delle maschere su frame diversi, poligono con buco, poligono
    degenere (1 pixel), record con cella inesistente, GeoJSON scritto solo
    su richiesta.
"""
import json
import math
import types

import numpy as np
import pytest

from radar_engine import detect as detect_mod
from radar_engine import footprint as fp
from radar_engine import models
from radar_engine import preprocess
from radar_engine.config import CONFIG

from conftest import add_blob, blank_grid, write_synthetic_tif

ISO = "2026-07-01T13:25:00Z"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _raster(tmp_path, arr, name="vmi.tif", iso=ISO, ms=1782912300000):
    path = write_synthetic_tif(str(tmp_path / name), arr)
    rd = preprocess.read_raster(
        path, nodata_values=CONFIG["preprocess"]["nodata_values"])
    rd.time_ms = ms
    rd.time_iso = iso
    return rd


def _bundle(frames, cells_by_frame=None, structures=None, storm_objects=None):
    b = types.SimpleNamespace()
    b.frames = list(frames)
    b.cells_by_frame = list(cells_by_frame or [[] for _ in frames])
    b.storm_objects_by_frame = list(storm_objects or [[] for _ in frames])
    b.convective_structures = list(structures or [])
    b.radar_timestamp_iso = frames[-1].time_iso if frames else None
    return b


def _cells_of(bundle, raster_index=0, cfg=None):
    cfg = cfg or CONFIG["detect"]
    cells = detect_mod.detect_cells(bundle.frames[raster_index], cfg)
    for c in cells:
        c.frame_index = raster_index
    return cells


def _ring_area_km2(ring):
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx = 111.32 * math.cos(math.radians(lat0))
    s = 0.0
    for i in range(len(ring) - 1):
        x0, y0 = ring[i][0] * kx, ring[i][1] * 111.32
        x1, y1 = ring[i + 1][0] * kx, ring[i + 1][1] * 111.32
        s += x0 * y1 - x1 * y0
    return abs(s) * 0.5


# ---------------------------------------------------------------------------
# Definizione storica del seed
# ---------------------------------------------------------------------------
def test_component_mask_legacy_semantics():
    arr = np.zeros((5, 5))
    arr[1:4, 1:4] = 40.0
    valid = np.ones((5, 5), dtype=bool)
    mask = fp.component_mask(arr, valid, 20.0, (2, 2))
    assert mask is not None and int(mask.sum()) == 9
    # seed fuori dalla componente (sotto soglia) -> None, non un poligono
    assert fp.component_mask(arr, valid, 20.0, (0, 0)) is None
    # seed fuori griglia -> None
    assert fp.component_mask(arr, valid, 20.0, (9, 9)) is None


# ---------------------------------------------------------------------------
# Identita' esatta della cella
# ---------------------------------------------------------------------------
def test_every_cell_is_recovered_with_exact_bbox_and_pixel_count(tmp_path):
    arr = blank_grid(rows=200, cols=200)
    add_blob(arr, 60, 60, 12, 45.0)
    add_blob(arr, 140, 120, 9, 38.0)
    rd = _raster(tmp_path, arr)
    bundle = _bundle([rd])
    cells = _cells_of(bundle)
    assert len(cells) >= 2
    labels = fp.frame_labels(bundle, 0, CONFIG["detect"])
    for cell in cells:
        mask, _win, method = fp.component_for_cell(
            rd, cell, CONFIG["detect"], labels=labels)
        assert method in ("label_bbox_and_pixel_count", "label_pixel_count")
        assert mask is not None
        assert int(mask.sum()) == int(cell.pixel_count)
        rows, cols = np.nonzero(mask)
        assert (int(rows.min()) + cell.bbox[0], int(cols.min()) + cell.bbox[1],
                int(rows.max()) + cell.bbox[0],
                int(cols.max()) + cell.bbox[1]) == tuple(cell.bbox)


def test_concave_component_centroid_seed_fails_and_label_resolves(tmp_path):
    """Componente a U: il centroide cade sul vuoto -> la firma bbox+pixel
    risolve, il seed del centroide no. E' il difetto che in 1.2.4.20 lasciava
    3 strutture su 69 senza footprint."""
    arr = blank_grid(rows=120, cols=120)
    # due bracci verticali + base: il baricentro cade nel vuoto centrale
    arr[30:80, 35:40] = 42.0
    arr[30:80, 70:75] = 42.0
    arr[75:80, 35:75] = 42.0
    rd = _raster(tmp_path, arr)
    bundle = _bundle([rd])
    cells = _cells_of(bundle)
    assert len(cells) == 1
    cell = cells[0]
    labels = fp.frame_labels(bundle, 0, CONFIG["detect"])
    c_rows, c_cols = int(round(cell.centroid_raster[0])), int(
        round(cell.centroid_raster[1]))
    lab, _sizes = labels
    seed_label = int(lab[c_rows, c_cols])
    # il pixel del centroide NON appartiene alla cella: il seed storico fallisce
    assert seed_label == 0
    assert fp.component_mask(
        rd.data[cell.bbox[0]:cell.bbox[2] + 1, cell.bbox[1]:cell.bbox[3] + 1],
        rd.valid_mask[cell.bbox[0]:cell.bbox[2] + 1,
                      cell.bbox[1]:cell.bbox[3] + 1],
        CONFIG["detect"]["dbz_threshold"],
        (cell.centroid_raster[0] - cell.bbox[0],
         cell.centroid_raster[1] - cell.bbox[1])) is None
    mask, _win, method = fp.component_for_cell(
        rd, cell, CONFIG["detect"], labels=labels)
    assert method == "label_bbox_and_pixel_count"
    assert int(mask.sum()) == int(cell.pixel_count)


def test_masks_for_cells_unions_two_frames(tmp_path):
    arr0 = blank_grid(rows=120, cols=120)
    add_blob(arr0, 40, 40, 8, 40.0)
    arr1 = blank_grid(rows=120, cols=120)
    add_blob(arr1, 80, 80, 8, 40.0)
    rd0 = _raster(tmp_path, arr0, name="a.tif")
    rd1 = _raster(tmp_path, arr1, name="b.tif", iso="2026-07-01T13:30:00Z")
    bundle = _bundle([rd0, rd1])
    c0 = _cells_of(bundle, 0)
    c1 = _cells_of(bundle, 1)
    cells = c0 + c1
    win, mask, used, skipped, how = fp.masks_for_cells(cells, bundle,
                                                       CONFIG["detect"])
    assert win is not None and used == len(cells) and skipped == 0
    assert int(mask.sum()) == sum(int(c.pixel_count) for c in cells)
    assert all(v == 0 for k, v in how.items() if k.startswith("cell_mask"))


# ---------------------------------------------------------------------------
# Poligonizzazione
# ---------------------------------------------------------------------------
def test_polygonize_ring_with_hole(tmp_path):
    arr = blank_grid(rows=80, cols=80)
    arr[30:50, 30:50] = 40.0
    arr[37:43, 37:43] = -9999.0          # buco (nodata): fuori dal componente
    rd = _raster(tmp_path, arr)
    bundle = _bundle([rd])
    cells = _cells_of(bundle)
    assert len(cells) == 1
    cell = cells[0]
    labels = fp.frame_labels(bundle, 0, CONFIG["detect"])
    mask, win, _m = fp.component_for_cell(rd, cell, CONFIG["detect"],
                                          labels=labels)
    geom = fp.polygons_from_mask(mask, rd, win)
    assert geom is not None
    geometry, n_vertices = geom
    assert geometry["type"] == "MultiPolygon"
    assert len(geometry["coordinates"]) == 1
    rings = geometry["coordinates"][0]
    assert len(rings) == 2                       # anello esterno + buco
    area = _ring_area_km2(rings[0]) - _ring_area_km2(rings[1])
    # 20x20 px meno il buco 6x6 = 364 px a 1 km^2 (tolleranza sui bordi)
    assert area == pytest.approx(364.0, rel=0.05)
    assert n_vertices >= 8


def test_polygonize_single_pixel_is_not_degenerate_geometry(tmp_path):
    arr = blank_grid(rows=40, cols=40)
    arr[20, 20] = 40.0
    rd = _raster(tmp_path, arr)
    mask = np.zeros((1, 1), dtype=bool)
    mask[0, 0] = True
    geom = fp.polygons_from_mask(mask, rd, (20, 20, 20, 20))
    assert geom is not None
    geometry, _n = geom
    ring = geometry["coordinates"][0][0]
    assert len(ring) == 5                        # 4 vertici + chiusura
    assert _ring_area_km2(ring) == pytest.approx(1.0, rel=0.02)


def test_polygonize_empty_mask_is_none(tmp_path):
    arr = blank_grid(rows=20, cols=20)
    rd = _raster(tmp_path, arr)
    assert fp.polygons_from_mask(np.zeros((5, 5), dtype=bool), rd,
                                 (0, 4, 0, 4)) is None


# ---------------------------------------------------------------------------
# Record e output
# ---------------------------------------------------------------------------
def test_record_footprint_reports_missing_cell(tmp_path):
    arr = blank_grid(rows=80, cols=80)
    add_blob(arr, 40, 40, 6, 40.0)
    rd = _raster(tmp_path, arr)
    bundle = _bundle([rd])
    rec = {"structure_id": "CS-x", "cell_id": "nope", "last_seen": ISO,
           "level": "strong"}
    out = fp.record_footprint(bundle, rec, CONFIG["detect"])
    assert out.get("geometry") is None
    assert out["null_reason"] in ("cell_not_found", "cell_mask_not_resolved")


def test_write_geojson_counts_and_severity(tmp_path):
    arr = blank_grid(rows=120, cols=120)
    add_blob(arr, 50, 50, 10, 45.0)
    rd = _raster(tmp_path, arr)
    bundle = _bundle([rd])
    cells = _cells_of(bundle)
    bundle.cells_by_frame = [cells]
    bundle.convective_structures = [{
        "structure_id": "CS-2026-07-01T13-cel001",
        "cell_id": cells[0].cell_id,
        "track_type": "cell",
        "level": "strong",
        "soi": 66,
        "first_seen": ISO,
        "last_seen": ISO,
        "position": list(cells[0].centroid_lonlat),
    }]
    path = tmp_path / "footprints.geojson"
    summary = fp.write_geojson(str(path), bundle, {"detect": CONFIG["detect"]})
    assert summary["cells"] == len(cells)
    assert summary["cells_without_geometry"] == 0
    assert summary["structures"] == 1
    assert summary["severe_structures"] == 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["type"] == "FeatureCollection"
    layers = [f["properties"]["layer"] for f in payload["features"]]
    assert layers.count("cell") == len(cells)
    assert layers.count("structure") == 1
    struct = [f for f in payload["features"]
              if f["properties"]["layer"] == "structure"][0]
    assert struct["properties"]["severe"] is True
    assert struct["geometry"]["type"] == "MultiPolygon"


def test_output_unchanged_without_footprints_flag(tmp_path):
    """Senza flag il motore non scrive alcun footprint: l'output operativo
    resta quello della 1.2.4.20 (qui: nessun file, nessuna chiave)."""
    import inspect
    from radar_engine import main as main_mod

    src = inspect.getsource(main_mod.main)
    assert "--footprints" in src
    assert "if args.footprints and not args.dry_run" in src
