# -*- coding: utf-8 -*-
"""Test della regione ``europa`` ESTESA ALL'ATLANTICO (render_fm_webp.py, 1.2.4.52).

Contesto MISURATO il 2026-10-27 sui manifest reali dei branch dati:

  * ``ecmwf_ifs025`` (fm2-ecmwf: 1440x281 a 0,25 gradi, lonMin -180, latMin 20):
    crop **461x181** su 30..75N / 70W..45E -> **921x679**, rapporto 1,3564 contro
    la scatola Mercatore 1,3577 (0,10% di differenza, nessuno stiramento);
  * ``dwd_icon`` (fm2-dwdicon: 2879x561 a 0,125 gradi, lonMin -180, latMin 20):
    crop **921x361** -> **921x679**. Il fattore automatico sarebbe 2 (1841 px):
    il tetto ``REGION_MAX_OUT_W`` lo porta a 1.

Perche' il tetto, misurato sullo STESSO frame ecmwf (32 variabili, seriale):
1121x1111 di oggi = 5.620.428 B; 921x679 = 4.089.440 B (**-27,2%**);
1381x1017 = 7.452.438 B (**+32,6%**). La regola di prodotto era "<= +15%",
quindi si pubblica 921 (che e' anche >= 754 px, il pannello: il browser non
ingrandisce nulla al fit).

I 3 domini REGIONALI non sono toccati: il bbox nuovo e' un limite di MASSIMA e
``region_crop`` lo interseca con la copertura del dominio, quindi dwd_icon_eu
resta 881x449 -> 1761x1390 e dwd_icon_d2 / ncep_gfs013 idem.
"""
import importlib.util
import math
import os
import types

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")


def _load_renderer():
    assert os.path.isfile(RENDERER_PATH), RENDERER_PATH
    spec = importlib.util.spec_from_file_location("render_fm_webp_atl_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()

# Copertura dei due domini globali (manifest del branch dati, 2026-10-27).
GLOBAL_BBOX = {"lat_min": 20.0, "lon_min": -180.0,
               "lat_max": 90.0, "lon_max": 180.0}
GLOBAL_GRIDS = {
    "ecmwf_ifs025": dict(nx=1440, ny=281, lonMin=-180.0, latMin=20.0,
                         dx=0.25, dy=0.25),
    "dwd_icon": dict(nx=2879, ny=561, lonMin=-180.0, latMin=20.0,
                     dx=0.125, dy=0.125),
}
# Copertura di dwd_icon_eu: 34..62N / 15W..40E a 0,0625 gradi.
EU_BBOX = {"lat_min": 34.0, "lon_min": -15.0, "lat_max": 62.0, "lon_max": 40.0}
EU_GRID = dict(nx=881, ny=449, lonMin=-15.0, latMin=34.0, dx=0.0625, dy=0.0625)

EXPECTED = {"ecmwf_ifs025": (921, 679), "dwd_icon": (921, 679)}

# Labrador, Groenlandia meridionale, Islanda, Azzorre.
CONTROL_POINTS = [("Labrador", 54.0, -60.0), ("Groenlandia sud", 60.0, -45.0),
                  ("Islanda", 65.0, -18.0), ("Azzorre", 38.0, -27.0)]


def _merc(lat):
    return math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))


def _merc_box_aspect(cg):
    lat_min = float(cg["latMin"])
    lat_max = lat_min + (int(cg["ny"]) - 1) * float(cg["dy"])
    lon_min = float(cg["lonMin"])
    lon_max = lon_min + (int(cg["nx"]) - 1) * float(cg["dx"])
    return math.radians(lon_max - lon_min) / (_merc(lat_max) - _merc(lat_min))


def _crop(grid, bbox=None):
    cr = mod.region_crop(bbox or GLOBAL_BBOX, grid, mod.REGIONS["europa"])
    assert cr is not None, "la regione europa non interseca il dominio"
    return cr


def _published(grid, bbox=None, capped=True):
    """Replica il calcolo di ``render_domain``: scala, tetto, righe Mercatore."""
    _r0, _r1, _c0, _c1, cg = _crop(grid, bbox)
    args = types.SimpleNamespace(upsample="auto",
                                 upsample_max=mod.UPSCALE_MAX_DEFAULT)
    scale = mod.resolve_upsample(args, cg["nx"], cg["ny"])
    cap = mod.REGION_MAX_OUT_W.get("europa") if capped else None
    if cap:
        scale = mod.fit_scale_to_width(cg["nx"], scale, cap)
    rows = mod.mercator_out_rows(cg, scale)
    out_w = (int(cg["nx"]) - 1) * scale + 1
    return cg, scale, out_w, rows


# --------------------------------------------------------------------------- #
# Il bbox
# --------------------------------------------------------------------------- #
def test_europa_reaches_the_labrador_and_southern_greenland():
    assert mod.REGIONS["europa"] == (30.0, -70.0, 75.0, 45.0)
    lat_min, lon_min, lat_max, lon_max = mod.REGIONS["europa"]
    # I quattro riferimenti di prodotto stanno dentro il bbox, non sul bordo.
    for name, lat, lon in CONTROL_POINTS:
        assert lat_min < lat < lat_max, name
        assert lon_min < lon < lon_max, name


def test_the_bbox_is_a_maximum_not_a_crop():
    # dwd_icon_eu si ferma a 15W: il crop deve restare il suo dominio intero,
    # senza una striscia trasparente a ovest (nessun pixel inventato).
    # ``region_crop`` restituisce indici di SLICE (row1/col1 esclusi).
    row0, row1, col0, col1, cg = _crop(EU_GRID, EU_BBOX)
    assert (row0, col0) == (0, 0)
    assert (row1 - row0, col1 - col0) == (EU_GRID["ny"], EU_GRID["nx"])
    assert (cg["nx"], cg["ny"]) == (EU_GRID["nx"], EU_GRID["ny"])
    assert cg["lonMin"] == -15.0
    assert _published(EU_GRID, EU_BBOX, capped=False)[2:] == (1761, 1390)


# --------------------------------------------------------------------------- #
# Taglie e tetto
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("domain", sorted(GLOBAL_GRIDS))
def test_published_size_is_the_measured_one(domain):
    cg, scale, out_w, out_h = _published(GLOBAL_GRIDS[domain])
    assert (out_w, out_h) == EXPECTED[domain], (domain, out_w, out_h)
    # La larghezza non supera MAI il tetto della regione.
    assert out_w <= mod.REGION_MAX_OUT_W["europa"]


def test_auto_factor_on_dwd_icon_would_be_double():
    """Il tetto serve: senza, dwd_icon uscirebbe 1841 px (+100% pixel)."""
    args = types.SimpleNamespace(upsample="auto",
                                 upsample_max=mod.UPSCALE_MAX_DEFAULT)
    _r0, _r1, _c0, _c1, cg = _crop(GLOBAL_GRIDS["dwd_icon"])
    raw = mod.resolve_upsample(args, cg["nx"], cg["ny"])
    assert raw == 2
    assert (cg["nx"] - 1) * raw + 1 == 1841
    assert mod.fit_scale_to_width(cg["nx"], raw, 1000) == 1


def test_the_cap_never_goes_below_the_source_grid():
    # Sotto il fattore 1 si perderebbe il dato: il tetto non lo impone mai.
    assert mod.fit_scale_to_width(461, 3, 1000) == 2
    assert mod.fit_scale_to_width(461, 3, 1400) == 3
    assert mod.fit_scale_to_width(461, 3, 100) == 1
    assert mod.fit_scale_to_width(1, 4, 100) == 4      # griglia degenere
    assert mod.fit_scale_to_width(921, 1, 100) == 1


# --------------------------------------------------------------------------- #
# Nessuno stiramento: il rapporto dell'immagine e' quello della scatola
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("domain", sorted(GLOBAL_GRIDS))
def test_image_aspect_matches_the_bounds_box(domain):
    cg, _scale, out_w, out_h = _published(GLOBAL_GRIDS[domain])
    box = _merc_box_aspect(cg)
    err = abs((out_w / float(out_h)) / box - 1.0)
    assert err < 0.005, (domain, out_w / float(out_h), box, err)
    # Il numero misurato (non solo la tolleranza): 0,096% su 921x679.
    assert err == pytest.approx(0.00096, abs=0.0002)


# --------------------------------------------------------------------------- #
# I quattro punti di controllo, con la formula del renderer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("domain", sorted(GLOBAL_GRIDS))
def test_control_points_fall_inside_the_image(domain):
    cg, scale, out_w, out_h = _published(GLOBAL_GRIDS[domain])
    cg2 = dict(cg)
    rows = mod.mercator_out_rows(cg, scale)
    if rows != (cg["ny"] - 1) * scale + 1:
        cg2["out_ny"] = rows
    row_of, col_of, H, W = mod._grid_to_pixel(cg2, scale)
    assert (W, H) == (out_w, out_h)
    for name, lat, lon in CONTROL_POINTS:
        x = (lon - cg["lonMin"]) / cg["dx"]
        y = (lat - cg["latMin"]) / cg["dy"]
        px, py = mod._data_to_pixel(row_of, col_of, scale, x, y)
        px, py = float(px), float(py)
        margin = min(px, py, W - 1 - px, H - 1 - py)
        assert margin > 40.0, (domain, name, px, py, W, H)
        assert 0.0 <= px <= W - 1 and 0.0 <= py <= H - 1


def test_control_point_coordinates_are_the_measured_ones():
    cg, scale, out_w, out_h = _published(GLOBAL_GRIDS["dwd_icon"])
    cg2 = dict(cg)
    cg2["out_ny"] = out_h
    row_of, col_of, _H, _W = mod._grid_to_pixel(cg2, scale)
    got = {}
    for name, lat, lon in CONTROL_POINTS:
        px, py = mod._data_to_pixel(row_of, col_of, scale,
                                    (lon - cg["lonMin"]) / cg["dx"],
                                    (lat - cg["latMin"]) / cg["dy"])
        got[name] = (round(float(px), 2), round(float(py), 2))
    assert got == {"Labrador": (80.0, 414.34),
                   "Groenlandia sud": (200.0, 325.92),
                   "Islanda": (416.0, 239.01),
                   "Azzorre": (344.0, 600.64)}


# --------------------------------------------------------------------------- #
# Etichette: >= 8 px di cifra sul pannello reale da 754 px
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("size", [(921, 679), (1381, 1017), (1761, 1390)])
def test_digits_are_at_least_8px_on_the_754px_panel(size):
    w, h = size
    font = mod.label_font_px(w, h, 0, region="europa")
    ink = mod._font_digit_height(font)
    factor = min(mod.PANEL_WIDTH_PX / float(w), mod.PANEL_HEIGHT_PX / float(h))
    assert ink * factor >= 8.0, (size, font, ink, ink * factor)
    # Il target storico (7 px su 1008) avrebbe dato 7,06-7,10 px qui.
    assert font > mod.LABEL_FONT_PX
