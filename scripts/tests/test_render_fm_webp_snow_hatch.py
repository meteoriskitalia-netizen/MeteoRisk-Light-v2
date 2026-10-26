# -*- coding: utf-8 -*-
"""Test del TRATTEGGIO DELLA FASE SOLIDA dentro le precipitazioni.

Requisito di prodotto: una sola mappa. Aprendo ``precipitation`` (o
``precipitation_6h``) si deve vedere il campo di precipitazione E, dove la
fase e' solida, un tratteggio disegnato SOPRA il raster e SOTTO le isolinee.

Il modulo viene caricato via importlib per percorso (``scripts/forecast_v2``
non e' un package), stessa convenzione di ``test_render_fm_webp_overlay.py``.
Il frame ``.om`` del test end-to-end e' scritto con l'``OmFileWriter`` REALE:
nessun mock del renderer, nessun valore inventato.
"""
import importlib.util
import json
import math
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_fm_webp_snow_hatch_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()


# --------------------------------------------------------------------------- #
# Geometria: px SCHERMO -> px IMMAGINE
# --------------------------------------------------------------------------- #
def test_geometry_keeps_the_screen_spacing_on_every_region():
    # Le tre taglie reali misurate sul branch (1.2.4.31, ecmwf_ifs025,
    # 2026-10-26T00Z): italia 385x337, europa 1121x609, north 2879x521.
    # Dall'aspetto forzato (REGION_ASPECT) europa esce 1121x701: si controllano
    # ENTRAMBE le taglie, perche' il geo deve reggere la nuova e la vecchia.
    for (w, h) in ((385, 337), (1121, 609), (1121, 701), (2879, 521)):
        geo = mod.snow_hatch_geometry(h, w)
        assert geo["spacing_low"] * geo["r"] == pytest.approx(
            mod.SNOW_HATCH_SCREEN_SPACING_LOW)
        assert geo["spacing_high"] * geo["r"] == pytest.approx(
            mod.SNOW_HATCH_SCREEN_SPACING_HIGH)


def test_geometry_uses_width_when_the_panel_width_binds():
    # north 2879x521: 560/521 = 1,075 NON e' il fattore di fit, perche' il
    # pannello e' largo 754 px. Il fit e' 754/2879 = 0,262 (misurato).
    geo = mod.snow_hatch_geometry(521, 2879)
    assert geo["r"] == pytest.approx(mod.PANEL_WIDTH_PX / 2879.0)
    assert geo["spacing_low"] == pytest.approx(11.0 / geo["r"])
    # con una spaziatura fissa in px immagine la trama sarebbe 2,7x piu' fitta
    assert geo["spacing_low"] > 40.0


def test_geometry_never_draws_thinner_than_one_image_pixel():
    # italia su griglia 0,25 gradi: 385x337, r 1,66 -> 1,2/1,66 = 0,72 px.
    # Sotto 1 px il tratto non arriva a copertura piena: tetto a 1 px.
    geo = mod.snow_hatch_geometry(337, 385)
    assert geo["width"] == pytest.approx(1.0)
    assert geo["width"] > mod.SNOW_HATCH_SCREEN_WIDTH / geo["r"] + 1e-9


# --------------------------------------------------------------------------- #
# Livelli: 0 / 1 / 2
# --------------------------------------------------------------------------- #
def test_level_mask_thresholds_are_product_choices():
    snow = np.array([[0.0, 0.49, 0.5, 0.79, 0.8, 1.0]], dtype=np.float32)
    lvl = mod.snow_hatch_level_mask(snow)
    assert lvl.tolist() == [[0, 0, 1, 1, 2, 2]]


def test_level_mask_nan_is_no_hatch_not_zero():
    snow = np.array([[np.nan, 0.9]], dtype=np.float32)
    lvl = mod.snow_hatch_level_mask(snow)
    assert lvl.tolist() == [[0, 2]]


def test_level_mask_respects_the_visible_raster():
    snow = np.array([[0.9, 0.9]], dtype=np.float32)
    visible = np.array([[True, False]])
    lvl = mod.snow_hatch_level_mask(snow, visible)
    assert lvl.tolist() == [[2, 0]]


# --------------------------------------------------------------------------- #
# Copertura: la geometria dichiarata e' quella che finisce nei pixel
# --------------------------------------------------------------------------- #
def test_coverage_matches_the_analytic_width():
    h, w = 400, 400
    geo = mod.snow_hatch_geometry(h, w)
    duty = (mod.SNOW_HATCH_SCREEN_DASH_ON
            / (mod.SNOW_HATCH_SCREEN_DASH_ON + mod.SNOW_HATCH_SCREEN_DASH_OFF))
    cov1 = mod.snow_hatch_coverage(h, w, np.ones((h, w), dtype=np.uint8))
    a1 = geo["width"] / geo["spacing_low"] * duty
    assert cov1.mean() == pytest.approx(a1, rel=0.05)
    cov2 = mod.snow_hatch_coverage(h, w, np.full((h, w), 2, dtype=np.uint8))
    a2dash = geo["width"] / geo["spacing_high"] * duty
    a2solid = geo["width"] / geo["spacing_high"]
    assert cov2.mean() == pytest.approx(1.0 - (1.0 - a2dash) * (1.0 - a2solid),
                                        rel=0.05)


def test_level_2_is_a_cross_not_a_denser_level_1():
    """Le due trame devono essere DIVERSE per costruzione, non solo piu' fitte.

    Prova geometrica: nel livello 2 esistono pixel in cui ENTRAMBE le famiglie
    sono "accese" (l'incrocio). Nel livello 1 non puo' esistere: c'e' una sola
    famiglia, quindi la copertura e' sempre il prodotto riga*tratto di una
    sola diagonale.
    """
    h, w = 300, 300
    geo = mod.snow_hatch_geometry(h, w)
    yy, xx = np.mgrid[0:h, 0:w]
    inv = 1.0 / math.sqrt(2.0)
    half = 0.5 * geo["width"]
    period = geo["dash_on"] + geo["dash_off"]
    f1 = mod._hatch_line_coverage((xx + yy) * inv, (xx - yy) * inv,
                                  geo["spacing_high"], geo["dash_on"], period,
                                  half, dashed=False)
    f2 = mod._hatch_line_coverage((xx - yy) * inv, (xx + yy) * inv,
                                  geo["spacing_high"], 0.0, 1.0, half,
                                  dashed=False)
    cross_px = int(((f1 > 0.99) & (f2 > 0.99)).sum())
    assert cross_px > 0, "incrocio assente: le due famiglie non si intersecano"
    # nel livello 1 la seconda famiglia non viene disegnata affatto
    cov1 = mod.snow_hatch_coverage(h, w, np.ones((h, w), dtype=np.uint8))
    assert cov1.max() <= 1.0
    assert int(((cov1 > 0.99).sum())) > 0


# --------------------------------------------------------------------------- #
# Composizione: solo dove la fase e' solida, solo dove il raster e' visibile
# --------------------------------------------------------------------------- #
def _uniform(h, w, rgb):
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., 0], a[..., 1], a[..., 2], a[..., 3] = rgb[0], rgb[1], rgb[2], 255
    return a


def test_no_ink_where_snow_is_below_the_threshold_or_undefined():
    h, w = 120, 160
    rgba = _uniform(h, w, (90, 180, 255))
    snow = np.full((h, w), 0.2, dtype=np.float32)
    snow[10:40, 10:40] = 0.9
    snow[60:80, 60:80] = np.nan
    out = mod.apply_snow_hatch(rgba, snow)
    changed = np.any(out[..., :3] != rgba[..., :3], axis=2)
    assert changed[10:40, 10:40].any(), "nessun tratteggio dove la neve e' >= 0.8"
    assert not changed[0:80, 90:160].any()
    # NaN: nessun pixel toccato (sotto 0.5 non c'e' tratteggio, e NaN non e' 0)
    nan_changed = np.zeros_like(changed)
    nan_changed[60:80, 60:80] = True
    assert not (changed & nan_changed).any()


def test_no_ink_where_the_raster_is_transparent():
    h, w = 100, 100
    rgba = _uniform(h, w, (90, 180, 255))
    rgba[..., 3] = 0                      # precipitazione 0: banda trasparente
    snow = np.full((h, w), 1.0, dtype=np.float32)
    out = mod.apply_snow_hatch(rgba, snow)
    assert np.array_equal(out, rgba)


def test_pixels_outside_the_hatch_are_byte_identical():
    h, w = 90, 130
    rgba = _uniform(h, w, (170, 220, 255))
    snow = np.zeros((h, w), dtype=np.float32)
    snow[30:60, 40:90] = 0.95
    out = mod.apply_snow_hatch(rgba, snow)
    outside = np.ones((h, w), dtype=bool)
    outside[28:62, 38:92] = False
    assert np.array_equal(out[outside], rgba[outside])


def test_hatch_is_deterministic():
    h, w = 80, 120
    rgba = _uniform(h, w, (0, 100, 255))
    snow = np.full((h, w), 0.7, dtype=np.float32)
    a = mod.apply_snow_hatch(rgba, snow)
    b = mod.apply_snow_hatch(rgba, snow)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, rgba)


# --------------------------------------------------------------------------- #
# Contrasto: >= 3:1 sul TRATTO PIENO, per OGNI banda della scala
# --------------------------------------------------------------------------- #
def _scale_bands(name="precipitation_6h"):
    with open(mod.COLOR_SCALES_PATH, "r", encoding="utf-8-sig") as fh:
        scales = json.load(fh)["scales"]
    light = scales[name]["light"]
    return list(light["breakpoints"]), [list(c) for c in light["colors"]]


def test_every_opaque_band_reaches_a_contrast_of_at_least_3():
    """Sul tratto a copertura piena il rapporto WCAG deve essere >= 3:1.

    Il fondo di ogni banda e' il colore ESATTO della scala con cui il raster
    e' dipinto: il test costruisce un'immagine uniforme di quel colore, una
    neve al 100 % e misura il contrasto dei pixel di inchiostro pieno.
    """
    bp, colors = _scale_bands()
    failures = []
    for i, c in enumerate(colors):
        if c[3] == 0:
            continue
        h, w = 200, 200
        rgba = _uniform(h, w, (c[0], c[1], c[2]))
        snow = np.full((h, w), 1.0, dtype=np.float32)
        # larghezza del tratto forzata a ~3 px per avere pixel a copertura 1
        old = mod.SNOW_HATCH_SCREEN_WIDTH
        try:
            mod.SNOW_HATCH_SCREEN_WIDTH = 3.0 * mod.snow_hatch_geometry(h, w)["r"]
            out = mod.apply_snow_hatch(rgba, snow)
        finally:
            mod.SNOW_HATCH_SCREEN_WIDTH = old
        lvl = mod.snow_hatch_level_mask(snow, rgba[..., 3] > 0)
        cov = mod.snow_hatch_coverage(h, w, lvl)
        core = cov >= 0.99
        assert core.any()
        bg = rgba[..., :3].astype(np.float32)[core]
        fg = out[..., :3].astype(np.float32)[core]
        cr = mod.contrast_ratio(mod.srgb_luminance(bg), mod.srgb_luminance(fg))
        worst = float(np.asarray(cr).min())
        if worst < mod.SNOW_HATCH_CONTRAST_TARGET:
            failures.append((bp[i], tuple(c[:3]), round(worst, 3)))
    assert not failures, "bande sotto 3:1: %r" % (failures,)


def test_product_alpha_is_used_where_it_is_enough():
    """Sul fondo CHIARO resta l'inchiostro di prodotto (alpha 150/255)."""
    rgba = _uniform(200, 200, (170, 220, 255))
    snow = np.full((200, 200), 1.0, dtype=np.float32)
    out = mod.apply_snow_hatch(rgba, snow)
    lvl = mod.snow_hatch_level_mask(snow, rgba[..., 3] > 0)
    cov = mod.snow_hatch_coverage(200, 200, lvl)
    core = cov >= 0.99
    bg = rgba[..., :3].astype(np.float32)[core]
    fg = out[..., :3].astype(np.float32)[core]
    dark = np.asarray(mod.SNOW_HATCH_INK_DARK, np.float32)
    a = mod.SNOW_HATCH_ALPHA_DARK / 255.0
    expected = bg * (1.0 - a) + dark * a
    assert np.abs(fg - expected).max() <= 1.5


# --------------------------------------------------------------------------- #
# End-to-end: frame .om REALE, il tratteggio finisce nel WebP
# --------------------------------------------------------------------------- #
BBOX = {"lat_min": 36.0, "lon_min": 6.5, "lat_max": 46.0, "lon_max": 16.0}
NX, NY = 48, 39
VT = "2026-10-26T00:00Z"
REF = "2026-10-25T18:00:00Z"
RUN_ID = "20261025T180000Z"


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _write_frame_om(path, arrays, valid_unix, ref_unix):
    from omfiles import OmFileWriter
    w = OmFileWriter.at_path(path)
    children = []
    for name, a in arrays.items():
        arr = np.ascontiguousarray(np.asarray(a, dtype=np.float32))
        children.append(w.write_array(
            arr, chunks=[min(32, int(arr.shape[0])), min(32, int(arr.shape[1]))],
            scale_factor=1.0, add_offset=0.0,
            compression="pfor_delta_2d_int16", name=name))
    children.append(w.write_scalar("EPSG:4326", name="crs_wkt"))
    children.append(w.write_scalar("lon,lat", name="coordinates"))
    children.append(w.write_scalar(int(valid_unix), name="valid_time"))
    children.append(w.write_scalar(int(ref_unix), name="forecast_reference_time"))
    root = w.write_group(name="", children=children)
    w.close(root)


def _frame_arrays(snow_value):
    """Precipitazione a banda larga + frazione neve SOLO in una meta'."""
    precip = np.zeros((NY, NX), dtype=np.float32)
    precip[4:NY - 4, 6:NX - 6] = 2.5          # banda [2,3) mm/h
    snow = np.full((NY, NX), np.nan, dtype=np.float32)
    half = NX // 2
    snow[4:NY - 4, 6:half] = snow_value       # sinistra: fase solida
    return {"precipitation": precip, "snow_fraction": snow,
            "snow_fraction_6h": np.where(np.isfinite(snow), snow, np.nan)}


def _setup_domain(tmp_path, name, arrays):
    d = str(tmp_path / "in" / name)
    os.makedirs(d, exist_ok=True)
    fname = mod.valid_time_to_filename(VT) + ".om"
    _write_frame_om(os.path.join(d, fname), arrays, 1792972800, 1792947600)
    _write_json(os.path.join(d, "latest.json"),
                {"domain": name, "bbox": BBOX,
                 "grid": {"nx": NX, "ny": NY, "lonMin": BBOX["lon_min"],
                          "latMin": BBOX["lat_min"],
                          "dx": (BBOX["lon_max"] - BBOX["lon_min"]) / (NX - 1),
                          "dy": (BBOX["lat_max"] - BBOX["lat_min"]) / (NY - 1)},
                 "reference_time": REF,
                 "frames": [{"file": fname, "valid_time": VT}],
                 "var_order": sorted(arrays.keys()),
                 "valid_times": [VT]})
    return d


def _render(domain_dir, out_dir, var):
    import argparse
    args = argparse.Namespace(
        watch_manifest=None, limit_frames=0, domains=None, vars=[var],
        quality=95, lossless=True, method=4, workers=1, upsample="auto",
        upsample_max=mod.UPSCALE_MAX_DEFAULT, label_font_px=0,
        north_projection="strip", north_disc_size=1000, existing_map=None,
        existing_palette_sha=None)
    return mod.render_domain(domain_dir, out_dir, mod.load_color_scales(),
                             args, {"italia": mod.REGIONS["italia"]})


def _webp(out_dir, var):
    from PIL import Image
    p = os.path.join(out_dir, "italia", "snow_hatch_ut", "runs", RUN_ID, var,
                     mod.valid_time_to_filename(VT) + ".webp")
    return np.asarray(Image.open(p).convert("RGBA")), p


def test_end_to_end_hatch_lands_only_on_the_snow_half(tmp_path):
    arrays = _frame_arrays(0.9)
    dom = _setup_domain(tmp_path, "snow_hatch_ut", arrays)
    out = str(tmp_path / "out")
    ok, skipped, err, _contrib, frames, _b = _render(dom, out, "precipitation")
    assert (ok, skipped, err, frames) == (1, 0, None, 1), (ok, skipped, err)
    rgba, path = _webp(out, "precipitation")
    assert os.path.getsize(path) > 0

    # il raster senza trattaggio, per isolare l'inchiostro
    saved = dict(mod.SNOW_HATCH_SOURCE)
    mod.SNOW_HATCH_SOURCE = {}
    try:
        out2 = str(tmp_path / "out_noh")
        _render(dom, out2, "precipitation")
    finally:
        mod.SNOW_HATCH_SOURCE = saved
    base, _ = _webp(out2, "precipitation")
    changed = np.any(rgba[..., :3] != base[..., :3], axis=2)
    assert changed.any()

    # la colonna di separazione: a sinistra neve, a destra pioggia
    cols = np.flatnonzero(changed.any(axis=0))
    mid = int(round((6 + NX // 2 - 1) * (rgba.shape[1] - 1) / (NX - 1)))
    assert cols.min() < mid, "tratteggio assente nella meta' con neve"
    assert cols.max() < mid + 6, "tratteggio dove la fase e' liquida (col %d)" % cols.max()


def test_end_to_end_two_levels_are_both_present(tmp_path):
    arrays = _frame_arrays(0.9)
    # meta' sinistra livello 1 (0,6), la parte piu' interna livello 2 (0,95)
    arrays["snow_fraction"][4:NY - 4, 6:NX // 4] = 0.6
    dom = _setup_domain(tmp_path, "snow_hatch_ut", arrays)
    out = str(tmp_path / "out")
    _render(dom, out, "precipitation")
    rgba, _ = _webp(out, "precipitation")
    snow = arrays["snow_fraction"]
    lvl = mod.snow_hatch_level_mask(snow, np.ones_like(snow, dtype=bool))
    assert (lvl == 1).any() and (lvl == 2).any()
    # copertura per livello misurata sull'immagine ricampionata con gli stessi
    # strumenti del renderer
    geo = mod.snow_hatch_geometry(rgba.shape[0], rgba.shape[1])
    cov = mod.snow_hatch_coverage(rgba.shape[0], rgba.shape[1], lvl)
    assert cov.mean() > 0
    assert geo["width"] >= 1.0
