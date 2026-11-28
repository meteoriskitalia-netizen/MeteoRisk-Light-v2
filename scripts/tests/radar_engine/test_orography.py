#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test della correzione orografica del nowcast (orography.py).

Coprono: caricamento e ORIENTAMENTO della griglia pubblicata, costante di e_s,
ordine di grandezza della formula, gate dichiarati, tetto del termine positivo
e pavimento dell'attenuazione sottovento. Nessun download: se la griglia non
c'e' il test che la usa viene saltato con un motivo esplicito.
"""

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from radar_engine import orography as orog  # noqa: E402


GRID = os.path.join(ROOT, "data", "geography", "orography_it_0p05.json")


def test_sat_vapour_pressure_reference_points():
    # Magnus/Tetens (Alduchov & Eskridge 1996): 611 Pa a 0 C, 2339 Pa a 20 C
    es = orog.sat_vapour_pressure_pa(np.array([0.0, 20.0]))
    assert abs(es[0] - 611.2) < 1.5
    assert abs(es[1] - 2339.0) < 8.0


def test_formula_order_of_magnitude():
    """RH 0,9, T 280 K (6,85 C), v.gradh 0,1 1/s -> ~2,2-2,5 mm/h."""
    cfg = dict(orog.DEFAULTS)
    e_pos, e_neg, gate = orog.enhancement_fields(
        mean3=np.array([500.0]), t_c=np.array([6.85]), rh=np.array([0.9]),
        upslope=np.array([0.1]), up_max=np.array([0.1]), cfg=cfg)
    assert gate[0]
    assert 2.0 < e_pos[0] < 2.8
    assert e_neg[0] == 0.0


def test_gate_low_elevation_and_dry_air():
    cfg = dict(orog.DEFAULTS)
    mean3 = np.array([5.0, 500.0, 500.0])
    t_c = np.array([5.0, 5.0, 5.0])
    rh = np.array([0.9, 0.5, 0.9])
    up = np.array([0.1, 0.1, 0.1])
    e_pos, e_neg, gate = orog.enhancement_fields(mean3, t_c, rh, up, up, cfg)
    assert not gate[0]          # quota media 3x3 < 20 m
    assert not gate[1]          # RH < 0,80
    assert gate[2]
    assert e_pos[0] == 0.0 and e_pos[1] == 0.0


def test_gate_min_upslope():
    cfg = dict(orog.DEFAULTS)
    e_pos, _e_neg, _g = orog.enhancement_fields(
        np.array([500.0]), np.array([5.0]), np.array([0.9]),
        np.array([0.0001]), np.array([0.0001]), cfg)
    assert e_pos[0] == 0.0      # sotto la soglia 0,0005 m/s


def test_cap_and_lee_floor():
    """Tetto: +100% del campo. Sottovento: mai sotto il 50%."""
    cfg = dict(orog.DEFAULTS)
    field = np.array([[1.0, 1.0, 0.0]])
    ctx = {"applied": True,
           "e_pos": np.array([[10.0, 0.0, 10.0]]),   # 10 mm/h su 1 mm/h
           "e_neg": np.array([[0.0, 9.0, 0.0]])}     # -9 mm/h su 1 mm/h
    out, st = orog.apply(field, ctx, cfg)
    assert out[0, 0] == pytest.approx(2.0)           # +100%, non +1000%
    assert out[0, 1] == pytest.approx(0.5)           # pavimento 50%
    assert out[0, 2] == pytest.approx(0.0)           # niente pioggia: nessuna
    assert st["capped_pixels"] == 1
    assert st["pixels_changed"] == 2


def test_no_rain_no_correction():
    cfg = dict(orog.DEFAULTS)
    field = np.zeros((3, 3))
    ctx = {"applied": True, "e_pos": np.full((3, 3), 5.0),
           "e_neg": np.zeros((3, 3))}
    out, st = orog.apply(field, ctx, cfg)
    assert np.all(out == 0.0)
    assert st["delta_max_mm_h"] == 0.0


@pytest.mark.skipif(not os.path.exists(GRID), reason="griglia non presente")
def test_grid_orientation_and_values():
    g = orog.load_grid(GRID)
    assert g is not None
    assert g["width"] == 800 and g["height"] == 650
    assert g["step_x"] == pytest.approx(0.02) and g["step_y"] == pytest.approx(0.02)
    assert g["encoding"]["codec"] == "lzma2_delta1"
    assert g["encoding"]["scale_m"] == pytest.approx(20.0)
    # riga 0 = NORD (lat massima): la prima riga deve essere alpina o mare,
    # l'ultima (lat 35, mare/Libia) non deve essere piu' alta della prima.
    assert np.nanmean(g["elev"][0, :]) > 200.0
    # Milano ~120 m, Adriatico 0 m, Monte Bianco > 3500 m (cella 0,02 deg)
    lat = np.array([45.4642, 43.0, 45.85])
    lon = np.array([9.19, 14.5, 6.85])
    v = orog.sample_bilinear(g, lon, lat)
    assert 80.0 < v[0] < 200.0
    assert v[1] == 0.0
    assert v[2] > 3500.0
    # controllo di ORIENTAMENTO contro i campioni dichiarati NEL file
    # (iy=0 -> lat0=35 nel file; qui l'array e' girato north-up)
    samples = g["doc"]["validation_samples"]
    for name in ("monte_bianco_vetta", "pianura_padana", "adriatico_medio"):
        s = samples[name]
        # nel file `cell` e' [ix, iy] con iy contato in ordine lat CRESCENTE
        ix, iy = s["cell"]
        row = g["height"] - 1 - iy
        assert abs(float(g["elev"][row, ix]) - float(s["elevation_m"])) < 1.0, name
    # le due vette che il prodotto deve rappresentare (media di cella)
    assert samples["etna_vetta"]["elevation_m"] >= 2900
    assert samples["monte_bianco_vetta"]["elevation_m"] >= 3150
    assert samples["gran_sasso_vetta"]["elevation_m"] >= 2200


@pytest.mark.skipif(not os.path.exists(GRID), reason="griglia non presente")
def test_sample_outside_grid_is_nan():
    g = orog.load_grid(GRID)
    v = orog.sample_bilinear(g, np.array([0.0]), np.array([0.0]))
    assert np.isnan(v[0])


def test_load_grid_legacy_int16_list(tmp_path):
    """Il loader deve continuare a leggere il vecchio vettore int16."""
    import json
    doc = {
        "schema": "meteorisk.orography/1",
        "grid": {"registration": "cell_centered", "lon0": 4.0, "lat0": 35.0,
                 "lon1": 4.2, "lat1": 35.2, "dlon": 0.1, "dlat": 0.1,
                 "nx": 2, "ny": 2,
                 "ordering": "row-major, lat crescente (iy=0 -> lat0), poi lon"},
        "units": "m", "dtype": "int16",
        "method": {"nodata": -32768},
        "elevations_m": [0, 100, 200, -32768],
    }
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    g = orog.load_grid(str(p))
    assert g is not None
    assert (g["width"], g["height"]) == (2, 2)
    assert g["step_x"] == pytest.approx(0.1) and g["step_y"] == pytest.approx(0.1)
    # file lat crescente -> north-up: riga 0 = [200, NaN], riga 1 = [0, 100]
    assert g["elev"][0, 0] == 200.0
    assert np.isnan(g["elev"][0, 1])
    assert g["elev"][1, 0] == 0.0
    assert g["elev"][1, 1] == 100.0


def test_env_hour_index_is_local_plus_one():
    """`time_base` e' ora LOCALE (+1 in inverno): 00:00Z -> indice 1."""
    import datetime as _dt
    meta = {"time_base": "2026-11-24T00:00"}
    ms = int(_dt.datetime(2026, 11, 24, 0, 0, tzinfo=_dt.timezone.utc)
             .timestamp() * 1000)
    idx, reason = orog._env_hour_index(meta, ms)
    assert reason is None
    assert idx == 1
    ms2 = int(_dt.datetime(2026, 11, 23, 23, 0, tzinfo=_dt.timezone.utc)
              .timestamp() * 1000)
    idx2, _ = orog._env_hour_index(meta, ms2)
    assert idx2 == 0
    idx3, reason3 = orog._env_hour_index({}, ms)
    assert idx3 is None and reason3 == "env_time_base_missing"


def _point(ws10, wd10, ws850=None, wd850=None, ws700=None, wd700=None):
    h = {"windspeed_10m": [ws10], "winddirection_10m": [wd10]}
    if ws850 is not None:
        h["wind_speed_850hPa"] = [ws850]
        h["wind_direction_850hPa"] = [wd850]
    if ws700 is not None:
        h["wind_speed_700hPa"] = [ws700]
        h["wind_direction_700hPa"] = [wd700]
    return {"lat": 45.0, "lon": 9.0,
            "models": {"best_match": {"hourly": h}}}


def test_wind_level_10m():
    cfg = dict(orog.DEFAULTS)
    u, v, meta = orog.wind_components([_point(5.0, 270)], 0, cfg)
    assert meta["wind_level"] == "10m"
    assert u[0] == pytest.approx(5.0, abs=1e-9)    # direzione 270 = da ovest
    assert v[0] == pytest.approx(0.0, abs=1e-9)    # -> vento verso est (u>0)


def test_wind_level_850_700_mass_weighted():
    cfg = dict(orog.DEFAULTS)
    cfg["wind_level"] = "850_700"
    # 850: 10 m/s da ovest (u=+10); 700: 0 m/s.
    # media pesata = 10*150/275 = 5.4545 m/s, NON (10+0)/2 = 5.
    u, v, meta = orog.wind_components([_point(0.0, 0.0, 10.0, 270, 0.0, 270)],
                                      0, cfg)
    assert meta["levels_hpa"] == [850, 700]
    assert u[0] == pytest.approx(150.0 / 275.0 * 10.0, abs=1e-9)
    assert v[0] == pytest.approx(0.0, abs=1e-9)


def test_wind_level_850_700_missing_level_is_nan():
    """Un livello assente NON ripiega sull'altro: il punto e' NaN."""
    cfg = dict(orog.DEFAULTS)
    cfg["wind_level"] = "850_700"
    u, v, _m = orog.wind_components([_point(5.0, 270, 10.0, 270)], 0, cfg)
    assert np.isnan(u[0]) and np.isnan(v[0])
