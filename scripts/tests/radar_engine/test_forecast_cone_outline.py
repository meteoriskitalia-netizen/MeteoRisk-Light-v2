#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del cono DAL CONTORNO (2026-11-11): outline + dilatazione + bande.

Comportamenti che FALLISCONO sul codice precedente (cono ellittico fisso,
uguale per ogni struttura, una sola banda):
  (a) due strutture con area molto diversa (disco da ~300 km^2 e disco da
      ~30.000 km^2) hanno `cone_shape="dilated_outline"`, `outline` non nullo
      e aree del cono a 120 min DIVERSE; il rapporto area_cono/area_struttura
      non e' costante;
  (b) ogni step ha tre bande annidate 50/75/90%, aree crescenti, ogni vertice
      del 50% dentro il 75% e del 75% dentro il 90%; `cone_polygon` e' l'anello
      90%; `probability_calibrated` e' false;
  (c) senza frame il contorno non esiste: fallback ellittico dichiarato, ma le
      tre bande restano (ellissi annidate) e l'anello 90% e' il poligono
      storico;
  (d) determinismo: due chiamate identiche producono lo stesso JSON.

Nessun dato reale: raster sintetici (stessa griglia DPC di conftest.py).
"""
import json
import math

from radar_engine import detect as detect_mod
from radar_engine import forecast
from radar_engine import models
from radar_engine import output
from radar_engine import preprocess
from radar_engine.config import CONFIG

from conftest import add_blob, blank_grid, make_track, write_synthetic_tif

T0 = 1_779_000_000_000
STEP_MS = 300_000
N_FRAMES = 8
ROWS = 500
COLS = 500
# (riga, colonna, raggio) del disco piccolo e del disco grande al frame 0.
SMALL = (80, 80, 10)     # ~314 km^2
LARGE = (280, 280, 98)   # ~30.000 km^2
SHIFT_PX = 5             # 5 km / 5 min = 60 km/h verso est


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _raster(tmp_path, idx, arr):
    path = write_synthetic_tif(str(tmp_path / ("vmi_%d.tif" % idx)), arr)
    rd = preprocess.read_raster(
        path, nodata_values=CONFIG["preprocess"]["nodata_values"])
    rd.time_ms = T0 + idx * STEP_MS
    rd.time_iso = _iso(rd.time_ms)
    return rd


def _bundle(tmp_path):
    """4 frame, 2 celle per frame (piccola e grande), 2 track lineari."""
    frames = []
    cells_by_frame = []
    for i in range(N_FRAMES):
        arr = blank_grid(rows=ROWS, cols=COLS)
        add_blob(arr, SMALL[0], SMALL[1] + i * SHIFT_PX, SMALL[2], 45.0)
        add_blob(arr, LARGE[0], LARGE[1] + i * SHIFT_PX, LARGE[2], 45.0)
        rd = _raster(tmp_path, i, arr)
        frames.append(rd)
        cells = detect_mod.detect_cells(rd, CONFIG["detect"])
        for c in cells:
            c.frame_index = i
        cells_by_frame.append(sorted(cells,
                                     key=lambda c: c.centroid_lonlat[0]))
    assert all(len(c) == 2 for c in cells_by_frame), \
        "attese 2 celle per frame, trovate %s" % [len(c) for c in cells_by_frame]
    small_cells = [cells[0] for cells in cells_by_frame]
    large_cells = [cells[1] for cells in cells_by_frame]
    motion = {
        "organization_score": 90,
        "classification": "Organized Convective Cell",
        "velocity_kmh": 60.0,
        "direction_toward_deg": 90.0,
        "motion_quality": "reliable",
        "motion_source": "weighted_fit",
        "motion_null_reason": None,
        "distance_km": 15.0,
        "duration_min": 15.0,
        "area_growth_pct": 0.0,
        "intensity_delta_dbz": 0.0,
    }
    small_track = make_track(small_cells, 1)
    large_track = make_track(large_cells, 2)
    small_track.motion = dict(motion)
    large_track.motion = dict(motion)
    bundle = models.EngineBundle("ok", "2026-11-11T10:00:00Z",
                                 output.SOURCE_LABEL)
    bundle.frames = frames
    bundle.cells_by_frame = cells_by_frame
    bundle.storm_objects_by_frame = [[] for _ in frames]
    bundle.tracks = [small_track, large_track]
    bundle.storm_tracks = []
    bundle.radar_timestamp_ms = frames[-1].time_ms
    bundle.radar_timestamp_iso = frames[-1].time_iso
    return bundle, small_track, large_track


def _ctx(track, last_cell):
    return {"track": track, "track_id": track.track_id,
            "track_type": "cell", "on_latest_frame": True,
            "cell_id": last_cell.cell_id,
            "last_seen": last_cell.timestamp_iso, "soi": 80}


def _inside(pt, ring):
    x, y = pt
    ins = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        if (y0 > y) != (y1 > y):
            xin = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if xin > x:
                ins = not ins
    return ins


# ---------------------------------------------------------------------------
# (a) cono diverso per strutture di area diversa
# ---------------------------------------------------------------------------
def test_due_strutture_di_area_diversa_hanno_coni_diversi(tmp_path):
    bundle, small, large = _bundle(tmp_path)
    fc_small = forecast.build_forecast(
        _ctx(small, small.points[-1]), bundle)
    fc_large = forecast.build_forecast(
        _ctx(large, large.points[-1]), bundle)
    assert fc_small is not None and fc_large is not None
    assert fc_small["cone_shape"] == "dilated_outline"
    assert fc_large["cone_shape"] == "dilated_outline"
    assert fc_small["outline"] is not None
    assert fc_large["outline"] is not None
    assert fc_small["outline_null_reason"] is None
    assert fc_large["outline_null_reason"] is None
    # le aree del contorno riflettono i due dischi (tolleranza sul bordo)
    assert 200.0 <= fc_small["outline_area_km2"] <= 500.0
    assert 25000.0 <= fc_large["outline_area_km2"] <= 35000.0
    # il cono a 120 min NON e' lo stesso numero
    c_small = fc_small["steps"][-1]["cone_area_km2"]
    c_large = fc_large["steps"][-1]["cone_area_km2"]
    assert c_large > c_small
    assert abs(c_large - c_small) > 10000.0
    # il rapporto area_cono/area_struttura NON e' costante
    r_small = c_small / fc_small["outline_area_km2"]
    r_large = c_large / fc_large["outline_area_km2"]
    assert r_small > 5.0 * r_large
    # i semiassi dell'inviluppo non sono piu' costanti
    assert (fc_small["steps"][-1]["cone_km_along"]
            != fc_large["steps"][-1]["cone_km_along"])
    # budget: la struttura grande usa la griglia del cono a 2 km (dichiarata),
    # la piccola resta a 1 km
    assert fc_small["cone_grid_km"] == 1.0
    assert fc_large["cone_grid_km"] == 2.0


# ---------------------------------------------------------------------------
# (b) tre bande annidate 50/75/90
# ---------------------------------------------------------------------------
def test_tre_bande_annidate_e_poligono_90(tmp_path):
    bundle, small, _large = _bundle(tmp_path)
    fc = forecast.build_forecast(_ctx(small, small.points[-1]), bundle)
    assert fc["probability_calibrated"] is False
    assert fc["probability_null_reason"]
    assert fc["cone_bands_basis"]["levels"] == [0.5, 0.75, 0.9]
    assert fc["cone_bands_basis"]["scale_factors"] == [0.312, 0.742, 1.0]
    for step in fc["steps"]:
        bands = step["cone_bands"]
        assert [b["level"] for b in bands] == [0.5, 0.75, 0.9]
        areas = [b["area_km2"] for b in bands]
        assert areas[0] < areas[1] < areas[2]
        # l'anello 90% e' il `cone_polygon` (retrocompatibilita' client)
        assert step["cone_polygon"]["coordinates"][0] == bands[-1]["polygon"]
        assert step["cone_area_km2"] == areas[-1]
        r50 = bands[0]["polygon"]
        r75 = bands[1]["polygon"]
        r90 = bands[2]["polygon"]
        assert all(_inside(pt, r75) for pt in r50[:-1])
        assert all(_inside(pt, r90) for pt in r75[:-1])
        for ring in (r50, r75, r90):
            assert ring[0] == ring[-1]
            assert len(ring) >= 4


# ---------------------------------------------------------------------------
# (c) fallback ellittico dichiarato (senza frame)
# ---------------------------------------------------------------------------
def test_fallback_ellittico_dichiarato_con_tre_bande(tmp_path):
    bundle, small, _large = _bundle(tmp_path)
    bundle.frames = []
    fc = forecast.build_forecast(_ctx(small, small.points[-1]), bundle)
    assert fc["cone_shape"] == "ellipse"
    assert fc["outline"] is None
    assert fc["outline_null_reason"] == "frame_not_found"
    assert fc["cone_fallback_reason"] == "frame_not_found"
    assert fc["cone_cross_ratio"] == 0.6
    assert fc["cone_vertices"] == 32
    step = fc["steps"][0]
    assert step["cone_km_along"] == 45.0
    assert step["cone_km_cross"] == 27.0
    assert len(step["cone_polygon"]["coordinates"][0]) == 33
    assert [b["level"] for b in step["cone_bands"]] == [0.5, 0.75, 0.9]
    areas = [b["area_km2"] for b in step["cone_bands"]]
    assert areas[0] < areas[1] < areas[2]
    assert (step["cone_polygon"]["coordinates"][0]
            == step["cone_bands"][-1]["polygon"])
    # l'anello 90% e' l'ellisse storica: area ~ pi*45*27
    assert abs(areas[-1] - math.pi * 45.0 * 27.0) / (math.pi * 45.0 * 27.0) \
        < 0.02


# ---------------------------------------------------------------------------
# (d) frame ricostruito dall'archivio rolling (cell.frame_index mancante)
# ---------------------------------------------------------------------------
def test_frame_index_mancante_risolve_il_contorno(tmp_path):
    """history._cell_from_payload crea le celle ricostruite con
    frame_index=None (-1): la maschera deve essere risolta sul frame del
    record, non su un indice mancante (bug visto nel run live 13:00Z)."""
    bundle, small, _large = _bundle(tmp_path)
    for cells in bundle.cells_by_frame:
        for cell in cells:
            cell.frame_index = -1
    fc = forecast.build_forecast(_ctx(small, small.points[-1]), bundle)
    assert fc["cone_shape"] == "dilated_outline"
    assert fc["outline"] is not None
    assert fc["outline_null_reason"] is None
    assert fc["outline_area_km2"] > 200.0


# ---------------------------------------------------------------------------
# (e) determinismo del payload
# ---------------------------------------------------------------------------
def test_payload_deterministico(tmp_path):
    bundle, small, _large = _bundle(tmp_path)
    ctx = _ctx(small, small.points[-1])
    a = forecast.build_forecast(ctx, bundle)
    b = forecast.build_forecast(ctx, bundle)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
