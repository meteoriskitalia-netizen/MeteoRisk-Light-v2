# -*- coding: utf-8 -*-
"""Test dell'OVERLAY (isolinee con valori + frecce vento) di render_fm_webp.py.

1.2.4.13: le isolinee (con il VALORE scritto sull'immagine) e le frecce del
vento sono INCAPSULATE nel WebP pre-renderizzato. Il raster base NON viene
risamplato: l'overlay e' un layer RGBA separato (sovracampionato, ridimensionato
con LANCZOS) composto per alpha.

Il modulo viene caricato via importlib per percorso (scripts/forecast_v2 non e'
un package), con la stessa convenzione di test_render_fm_webp_index.py.
NESSUN mock del sistema sotto test: marching squares, etichette, frecce e
composizione sono le funzioni REALI del renderer; i frame .om usati nel test
end-to-end sono scritti con l'OmFileWriter REALE.
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
    spec = importlib.util.spec_from_file_location("render_fm_webp_overlay_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()


# --------------------------------------------------------------------------- #
# marching squares
# --------------------------------------------------------------------------- #
def test_contour_segments_linear_ramp_along_x():
    """Rampa lineare lungo le colonne: la isolinea e' la verticale x = 0.5."""
    ny, nx = 5, 6
    z = np.tile(np.arange(nx, dtype=np.float64), (ny, 1))
    segs = mod.contour_segments(z, 0.5)
    assert segs.shape == (ny - 1, 4)
    for j in range(ny - 1):
        x0, y0, x1, y1 = segs[j]
        assert (x0, x1) == (0.5, 0.5)          # interpolazione lineare esatta
        assert sorted((float(y0), float(y1))) == [float(j), float(j + 1)]


def test_contour_segments_linear_ramp_along_y():
    """Rampa lineare lungo le righe (row 0 = SUD): isolinea orizzontale."""
    ny, nx = 6, 4
    z = np.repeat(np.arange(ny, dtype=np.float64)[:, None], nx, axis=1)
    segs = mod.contour_segments(z, 0.5)
    assert segs.shape == (nx - 1, 4)
    for i in range(nx - 1):
        x0, y0, x1, y1 = segs[i]
        assert (y0, y1) == (0.5, 0.5)
        assert (x0, x1) == (float(i), float(i + 1))


def test_contour_segments_closed_loop():
    """Campo radiale: la isolinea e' un anello chiuso (4 segmenti, diamante).

    Il livello -1 passa esattamente per i 4 vertici a distanza 1 dal centro:
    i segmenti DEGENERI (estremi coincidenti) vengono scartati, resta il
    poligono.
    """
    ny = nx = 5
    ii, jj = np.meshgrid(np.arange(nx, dtype=np.float64),
                          np.arange(ny, dtype=np.float64))
    z = -((ii - 2.0) ** 2 + (jj - 2.0) ** 2)
    segs = mod.contour_segments(z, -1.0)
    assert len(segs) == 4
    # tutti i punti estremi sono sul cerchio di raggio 1 intorno a (2, 2)
    ends = set()
    for x0, y0, x1, y1 in segs:
        for (x, y) in ((x0, y0), (x1, y1)):
            assert math.hypot(x - 2.0, y - 2.0) == pytest.approx(1.0, abs=1e-9)
            ends.add((round(x, 9), round(y, 9)))
    assert ends == {(1.0, 2.0), (2.0, 1.0), (3.0, 2.0), (2.0, 3.0)}
    # la polilinea concatenata e' chiusa
    lines = mod._chain_segments(segs)
    assert len(lines) == 1
    assert lines[0][0] == pytest.approx(lines[0][-1])


def test_contour_segments_ignores_nan_and_non_finite():
    z = np.zeros((4, 4), dtype=np.float64)
    z[1, 1] = np.nan
    segs = mod.contour_segments(z, 0.5)
    # Il campo e' tutto 0 (sotto il livello) tranne il NaN: nessuna cella
    # valida attraversa il livello -> nessun segmento.
    assert len(segs) == 0
    assert mod.contour_segments(np.zeros((1, 1)), 1.0).shape == (0, 4)


def test_contour_levels_respects_interval():
    levels = mod.contour_levels(1.0, 0.0, 20.0)
    assert levels == [float(v) for v in range(0, 21)]
    assert all(float(v).is_integer() for v in levels)
    # non-multipli del livello non entrano
    assert mod.contour_levels(5.0, 3.0, 17.0) == [5.0, 10.0, 15.0]
    # il livello e' il valore "pulito" (quello scritto come etichetta)
    assert mod.contour_levels(0.5, 0.0, 2.0) == [0.0, 0.5, 1.0, 1.5, 2.0]


def test_contour_levels_caps_at_64_levels():
    levels = mod.contour_levels(1.0, 0.0, 500.0)
    assert len(levels) <= mod.MAX_CONTOUR_LEVELS
    # l'intervallo e' stato ALLARGATO (raddoppiato: 1 -> 2 -> 4 -> 8) per
    # stare nel cap: 500/8 = 62 intervalli -> 63 livelli.
    assert len(levels) == 63
    assert levels[1] - levels[0] == pytest.approx(8.0)
    assert levels[0] >= 0.0 and levels[-1] <= 500.0
    # un campo enorme non supera mai il cap
    assert len(mod.contour_levels(0.1, 0.0, 1e6)) <= mod.MAX_CONTOUR_LEVELS
    # i livelli restano multipli dell'intervallo ORIGINALE
    assert all(abs(round(v / 1.0) - v) < 1e-9 for v in mod.contour_levels(2.0, 0.0, 40.0))


def test_contour_levels_empty_and_invalid():
    assert mod.contour_levels(1.0, 5.0, 1.0) == []
    assert mod.contour_levels(0.0, 0.0, 1.0) == []
    assert mod.contour_levels(1.0, float("nan"), 1.0) == []


# --------------------------------------------------------------------------- #
# colori ed etichette
# --------------------------------------------------------------------------- #
def test_level_color_uses_band_rgb_with_forced_alpha():
    scales = mod.load_color_scales()
    variant = mod._scale_variant(scales, "temperature_2m", "light")
    bps = variant["breakpoints"]
    cols = variant["colors"]
    for level in (min(bps) + 0.5, 0.0, 10.0, max(bps) - 0.5):
        rgba = mod._level_color(variant, level)
        assert len(rgba) == 4
        assert rgba[3] == int(round(mod.CONTOUR_ALPHA * 255.0))
        # l'alpha NON e' quello della scala (molte scale hanno colors[0] alpha 0)
        assert rgba[:3] != (0, 0, 0) or rgba[3] > 0
    # fallback su scala assente
    assert mod._level_color(None, 0.0) == mod.CONTOUR_FALLBACK_COLOR


def test_format_level_integer_or_one_decimal():
    assert mod._format_level(12.0, 1.0) == "12"
    assert mod._format_level(1010.0, 5.0) == "1010"
    assert mod._format_level(5040.0, 40.0) == "5040"
    assert mod._format_level(8.5, 0.5) == "8.5"


# --------------------------------------------------------------------------- #
# frecce del vento (convenzione)
# --------------------------------------------------------------------------- #
def test_wind_flow_vector_convention():
    """dir = direzione DA cui soffia (0=N, 90=E, 180=S, 270=O).

    In pixel: +x = EST, +y = SUD.
      dir=0   -> il vento va verso SUD   -> dy > 0
      dir=180 -> il vento va verso NORD  -> dy < 0
      dir=90  -> il vento va verso OVEST -> dx < 0
    """
    ux, uy = mod._wind_flow_vector(np.array([0.0, 180.0, 90.0]))
    assert ux[0] == pytest.approx(0.0, abs=1e-12)
    assert uy[0] > 0.0
    assert uy[1] < 0.0
    assert ux[2] < 0.0
    assert uy[2] == pytest.approx(0.0, abs=1e-12)
    # unione modulare: 360 gradi = 0 gradi
    ux2, uy2 = mod._wind_flow_vector(np.array([360.0]))
    assert ux2[0] == pytest.approx(ux[0], abs=1e-9)
    assert uy2[0] == pytest.approx(uy[0], abs=1e-9)


def test_unwrap_direction_removes_the_0_360_discontinuity():
    deg = np.array([[350.0, 355.0, 0.0, 5.0, 10.0]])
    out = mod._unwrap_direction(deg)
    assert out.shape == deg.shape
    # nessun salto > 180 gradi fra celle adiacenti
    assert np.abs(np.diff(out[0])).max() < 180.0
    # il campo e' continuo: 350 -> 355 -> 360 -> 365 -> 370
    assert out[0, 2] == pytest.approx(360.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# overlay: il raster base non viene toccato
# --------------------------------------------------------------------------- #
def _base_rgba(h, w):
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, size=(h, w, 4), dtype=np.uint8)
    arr[..., 3] = 255
    return arr


def test_overlay_with_empty_drawer_is_byte_identical_to_base():
    base = _base_rgba(40, 60)
    out = mod._apply_overlay(base, lambda draw, ss: None)
    assert np.array_equal(out, base)


def test_overlay_keeps_base_untouched_where_transparent():
    base = _base_rgba(80, 120)
    h, w = base.shape[0], base.shape[1]

    def _drawer(draw, ss):
        draw.line([(10 * ss, 10 * ss), (100 * ss, 60 * ss)],
                  fill=(255, 0, 0, 255), width=2)

    ss = mod._overlay_scale(h, w)
    layer = mod._make_overlay(h, w, ss, _drawer)
    alpha = np.asarray(layer)[:, :, 3]
    out = mod._apply_overlay(base, _drawer)
    transparent = alpha == 0
    assert transparent.any(), "il test non avrebbe senso senza pixel coperti"
    # i pixel NON coperti dall'overlay sono byte-identici al raster base
    assert np.array_equal(out[transparent], base[transparent])
    # qualcosa e' stato effettivamente disegnato
    assert (out != base).any()
    # il raster base NON e' stato risamplato: la forma e' invariata
    assert out.shape == base.shape


def test_overlay_scale_is_capped_for_big_images():
    assert mod._overlay_scale(100, 100) == 2.0
    assert mod._overlay_scale(2000, 2000) == 1.5
    assert mod._overlay_scale(4000, 4000) == 1.0


# --------------------------------------------------------------------------- #
# rendering dei livelli/etichette sul campo reale
# --------------------------------------------------------------------------- #
GRID = {"nx": 40, "ny": 31, "lonMin": 6.5, "latMin": 36.0, "dx": 0.25, "dy": 0.333}


def _ramp_field(ny, nx):
    """Temperatura da 0 a 20 gradi, crescente verso NORD (row 0 = SUD)."""
    return (20.0 * np.arange(ny, dtype=np.float64)[:, None]
            / (ny - 1) * np.ones((1, nx), dtype=np.float64))


def test_draw_contours_produces_levels_and_labels():
    from PIL import Image, ImageDraw
    field = _ramp_field(GRID["ny"], GRID["nx"])
    scale = 8
    H = (GRID["ny"] - 1) * scale + 1
    W = (GRID["nx"] - 1) * scale + 1
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    scales = mod.load_color_scales()
    variant = mod._scale_variant(scales, "temperature_2m", "light")
    res = mod._draw_contours(draw, field, GRID, scale, variant, 1.0)
    # i livelli disegnati sono tutti i multipli di 1 dentro [0, 20] tranne
    # quello coincidente con il MINIMO del campo (nessun attraversamento).
    assert res["levels"] == [float(v) for v in range(1, 21)]
    # almeno un'etichetta scritta (il requisito "i valori sull'immagine")
    assert len(res["labels"]) >= 1
    for level, x, y in res["labels"]:
        assert level in res["levels"]
        assert 0 <= x <= W and 0 <= y <= H
    # il livello con il valore minimo non ha segmenti (campo tutto >= minimo)
    assert mod.contour_segments(field, 0.0).shape[0] == 0
    # l'immagine ha pixels non trasparenti (le isolinee sono state disegnate)
    assert np.asarray(layer)[:, :, 3].max() > 0


def test_draw_contours_skips_labels_of_too_short_polylines():
    from PIL import Image, ImageDraw
    # campo 3x3: le isoline sono disegnate ma troppo corte per essere etichettate
    field = np.array([[0.0, 1.0, 2.0],
                      [0.0, 1.0, 2.0],
                      [0.0, 1.0, 2.0]], dtype=np.float64)
    grid = {"nx": 3, "ny": 3, "lonMin": 0.0, "latMin": 0.0, "dx": 1.0, "dy": 1.0}
    layer = Image.new("RGBA", (3, 3), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    res = mod._draw_contours(draw, field, grid, 1, None, 1.0)
    # i livelli 1 e 2 hanno segmenti (il livello 0 = minimo del campo no)
    assert res["levels"] == [1.0, 2.0]
    # ma la polilinea e' troppo corta (< 60 px): nessuna etichetta scritta
    assert res["labels"] == []
    assert np.asarray(layer)[:, :, 3].max() > 0


# --------------------------------------------------------------------------- #
# end-to-end: frame .om REALE -> render_domain REALE -> WebP con overlay
# --------------------------------------------------------------------------- #
def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _write_frame_om(path, arrays, valid_unix, ref_unix):
    """Frame .om REALE (stessa API del motore)."""
    from omfiles import OmFileWriter
    w = OmFileWriter.at_path(path)
    children = []
    for name, a in arrays.items():
        arr = np.ascontiguousarray(np.asarray(a, dtype=np.float32))
        chunks = [min(32, int(arr.shape[0])), min(32, int(arr.shape[1]))]
        children.append(w.write_array(arr, chunks=chunks, scale_factor=1.0,
                                      add_offset=0.0,
                                      compression="pfor_delta_2d_int16",
                                      name=name))
    children.append(w.write_scalar("EPSG:4326", name="crs_wkt"))
    children.append(w.write_scalar("lon,lat", name="coordinates"))
    children.append(w.write_scalar(int(valid_unix), name="valid_time"))
    children.append(w.write_scalar(int(ref_unix), name="forecast_reference_time"))
    root = w.write_group(name="", children=children)
    w.close(root)


BBOX = {"lat_min": 36.0, "lon_min": 6.5, "lat_max": 46.0, "lon_max": 16.0}
NX, NY = 40, 31
VT = "2026-10-14T16:00Z"


def _setup_domain(tmp_path, name, arrays):
    d = str(tmp_path / "in" / name)
    os.makedirs(d, exist_ok=True)
    fname = mod.valid_time_to_filename(VT) + ".om"
    _write_frame_om(os.path.join(d, fname), arrays, 1792012800, 1792009200)
    _write_json(os.path.join(d, "latest.json"),
                {"domain": name, "bbox": BBOX,
                 "grid": {"nx": NX, "ny": NY, "lonMin": BBOX["lon_min"],
                          "latMin": BBOX["lat_min"],
                          "dx": (BBOX["lon_max"] - BBOX["lon_min"]) / (NX - 1),
                          "dy": (BBOX["lat_max"] - BBOX["lat_min"]) / (NY - 1)},
                 "frames": [{"file": fname, "valid_time": VT}],
                 "var_order": sorted(arrays.keys()),
                 "valid_times": [VT]})
    return d


def _render(domain_dir, out_dir, vars_list, overlay=True):
    """Render REALE (render_domain) col modulo REALE.

    ``overlay=False`` disabilita temporaneamente sia le isolinee sia le frecce
    vento (modificando le tabelle del modulo), cosi' da ottenere il raster
    BASE da confrontare con quello con overlay.
    """
    from PIL import Image
    args = mod.parse_args([])
    args.domains = mod._split_csv(args.domains)
    args.vars = mod._split_csv(",".join(vars_list))
    args.themes = ["light"]
    args.lossless = True
    scales = mod.load_color_scales()
    saved_ci = dict(mod.CONTOUR_INTERVALS)
    saved_av = dict(mod.ARROW_VARS)
    if not overlay:
        mod.CONTOUR_INTERVALS = {}
        mod.ARROW_VARS = {}
    try:
        got = mod.render_domain(domain_dir, out_dir, scales, args,
                                {"domain": None})
    finally:
        mod.CONTOUR_INTERVALS = saved_ci
        mod.ARROW_VARS = saved_av
    rendered, skipped, err, contrib, frames_done, bytes_written = got
    assert err is None, err
    assert rendered == len(vars_list), got
    path = os.path.join(out_dir, "domain", os.path.basename(domain_dir),
                        vars_list[0], mod.valid_time_to_filename(VT) + ".webp")
    assert os.path.isfile(path)
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA")), contrib


def test_end_to_end_contours_are_baked_into_the_webp(tmp_path):
    pytest.importorskip("omfiles")
    mod._require_render_deps()
    field = _ramp_field(NY, NX)
    domain_dir = _setup_domain(tmp_path, "dwd_icon_eu",
                               {"temperature_2m": field})
    with_overlay, contrib = _render(domain_dir, str(tmp_path / "out1"),
                                    ["temperature_2m"])
    # Il WebP prodotto contiene le isolinee: senza overlay la resa e' diversa.
    base_only, _ = _render(domain_dir, str(tmp_path / "out2"),
                           ["temperature_2m"], overlay=False)
    assert with_overlay.shape == base_only.shape
    changed = (with_overlay != base_only).any(axis=2)
    assert changed.any(), "le isolinee non sono state disegnate"
    # il raster base e' preservato: le isolinee coprono solo una minoranza
    assert changed.mean() < 0.50, changed.mean()
    # l'indice del dominio contiene la variabile (tema light)
    assert "temperature_2m" in contrib["domain"]["variables"]
    assert set(contrib["domain"]["variables"]["temperature_2m"]["themes"]) == {"light"}


def test_end_to_end_wind_arrows_are_baked_into_the_webp(tmp_path):
    pytest.importorskip("omfiles")
    mod._require_render_deps()
    speed = np.full((NY, NX), 12.0, dtype=np.float64)
    direction = np.full((NY, NX), 270.0, dtype=np.float64)   # da OVEST
    domain_dir = _setup_domain(tmp_path, "ncep_gfs025", {
        "wind_speed_10m": speed,
        "wind_direction_10m": direction,
    })
    with_overlay, contrib = _render(domain_dir, str(tmp_path / "out1"),
                                    ["wind_speed_10m"])
    saved_ci = dict(mod.CONTOUR_INTERVALS)
    saved_av = dict(mod.ARROW_VARS)
    try:
        mod.CONTOUR_INTERVALS = {}
        mod.ARROW_VARS = {}
        base_only, _ = _render(domain_dir, str(tmp_path / "out2"),
                               ["wind_speed_10m"])
    finally:
        mod.CONTOUR_INTERVALS = saved_ci
        mod.ARROW_VARS = saved_av
    changed = (with_overlay != base_only).any(axis=2)
    assert changed.any(), "le frecce vento non sono state disegnate"
    # La direzione NON ha color scale: nessun WebP e' prodotto per lei.
    assert "wind_direction_10m" not in contrib["domain"]["variables"]
    assert not os.path.exists(os.path.join(str(tmp_path / "out1"), "domain",
                                           "ncep_gfs025", "wind_direction_10m"))


def test_draw_wind_arrows_count_and_geometry():
    from PIL import Image, ImageDraw
    grid = {"nx": 20, "ny": 20, "lonMin": 0.0, "latMin": 0.0, "dx": 0.5, "dy": 0.5}
    scale = 2
    H = (grid["ny"] - 1) * scale + 1
    W = (grid["nx"] - 1) * scale + 1
    speed = np.full((H, W), 10.0, dtype=np.float64)
    direction = np.full((H, W), 0.0, dtype=np.float64)   # da NORD -> verso SUD
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    n = mod._draw_wind_arrows(draw, speed, direction, grid, scale, 40.0)
    assert n > 0
    assert np.asarray(layer)[:, :, 3].max() > 0
    # il campo calmo (speed < 0.5) non produce frecce
    layer2 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw2 = ImageDraw.Draw(layer2)
    assert mod._draw_wind_arrows(draw2, np.zeros((H, W)), direction, grid, scale,
                                 40.0) == 0
    assert np.asarray(layer2)[:, :, 3].max() == 0


def _arrow_layer(direction_deg):
    """Layer RGBA con le frecce e l'elenco dei centri (pixel di output)."""
    from PIL import Image, ImageDraw
    grid = {"nx": 40, "ny": 40, "lonMin": 0.0, "latMin": 36.0,
            "dx": 0.25, "dy": 0.25}
    scale = 2
    H = (grid["ny"] - 1) * scale + 1
    W = (grid["nx"] - 1) * scale + 1
    speed = np.full((H, W), 40.0, dtype=np.float64)
    direction = np.full((H, W), float(direction_deg), dtype=np.float64)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    n = mod._draw_wind_arrows(draw, speed, direction, grid, scale, 40.0)
    alpha = np.asarray(layer)[:, :, 3]
    row_of, col_of, HH, WW = mod._grid_to_pixel(grid, scale)
    step = max(mod.ARROW_STEP_PX / float(scale), 3.0)
    idx_i = np.unique(np.arange(0, grid["nx"], step).astype(np.int64))
    idx_j = np.unique(np.arange(0, grid["ny"], step).astype(np.int64))
    centres = []
    for j in idx_j:
        for i in idx_i:
            cx = int(round(i * scale))
            cy = int(round(row_of[j]))
            centres.append((cx, cy))
    return alpha, centres, n


def test_wind_arrows_geometry_points_to_the_east_for_a_west_wind():
    """dir=270 (da OVEST) -> la freccia soffia verso EST.

    Verifica GEOMETRICA (non usa la formula): i pixel disegnati a EST del
    centro della freccia sono piu' di quelli a OVEST (la punta, piena, pesa
    piu' del fusto sottile che sporge dalla coda).
    """
    alpha, centres, n = _arrow_layer(270.0)
    assert n > 0
    H, W = alpha.shape
    checked = 0
    for cx, cy in centres:
        if not (14 <= cx <= W - 15 and 6 <= cy <= H - 7):
            continue
        band = alpha[cy - 5:cy + 6, :]
        east = int(band[:, cx + 1:cx + 13].sum())
        west = int(band[:, cx - 12:cx].sum())
        assert east > west, ("freccia non orientata a est: est=%d ovest=%d"
                             % (east, west))
        checked += 1
    assert checked > 0, "nessuna freccia analizzabile"


def test_wind_arrows_geometry_points_to_the_north_for_a_south_wind():
    """dir=180 (da SUD) -> la freccia soffia verso NORD (pixel SOPRA il centro)."""
    alpha, centres, n = _arrow_layer(180.0)
    assert n > 0
    H, W = alpha.shape
    checked = 0
    for cx, cy in centres:
        if not (6 <= cx <= W - 7 and 14 <= cy <= H - 15):
            continue
        band = alpha[:, cx - 5:cx + 6]
        north = int(band[cy - 12:cy, :].sum())
        south = int(band[cy + 1:cy + 13, :].sum())
        assert north > south, ("freccia non orientata a nord: nord=%d sud=%d"
                                % (north, south))
        checked += 1
    assert checked > 0, "nessuna freccia analizzabile"


# --------------------------------------------------------------------------- #
# Tema SOLO CHIARO (spec 1)
# --------------------------------------------------------------------------- #
def test_dark_theme_is_fatal_with_exit_code_2():
    """``--themes dark`` -> FATAL esplicito e codice di uscita 2."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        rc = mod.main(["--themes", "dark", "--in", os.path.join(".", "nope"),
                       "--out", os.path.join(".", "nope2")])
    assert rc == 2
    msg = buf.getvalue()
    assert "FATAL" in msg
    assert "dark theme removed in 1.2.4.13" in msg


def test_default_themes_are_light_only():
    args = mod.parse_args([])
    assert args.themes == "light"
    assert mod._split_csv(args.themes) == ["light"]


def test_theme_var_dir_has_no_dark_subdirectory(tmp_path):
    root = str(tmp_path)
    for theme in ("light", "dark"):
        d = mod._theme_var_dir(root, "italia", "dwd_icon_eu", "temperature_2m",
                               theme)
        assert d == os.path.join(root, "italia", "dwd_icon_eu", "temperature_2m")
        assert "dark" not in os.path.normpath(d).split(os.sep)


def test_prune_legacy_theme_dirs_removes_the_dark_subtree(tmp_path):
    root = str(tmp_path)
    vdir = os.path.join(root, "italia", "dwd_icon_eu", "temperature_2m")
    dark = os.path.join(vdir, "dark")
    os.makedirs(dark)
    with open(os.path.join(dark, "x.webp"), "wb") as fh:
        fh.write(b"x")
    mod._prune_legacy_theme_dirs(root, "italia", "dwd_icon_eu", "temperature_2m")
    assert not os.path.exists(dark)
    assert os.path.isdir(vdir)


def test_grid_to_pixel_is_the_inverse_of_the_mercator_row_resampler():
    """La mappa data->pixel deve essere l'INVERSA del resampler Mercator.

    Il resampler (codice preesistente, non toccato) mappa riga di OUTPUT ->
    posizione nella riga di DATI. Il nostro `_grid_to_pixel` fa il cammino
    inverso (riga di dati -> riga di output). Il controllo e' incrociato: si
    usa il resampler vero come riferimento, senza riusare la nostra formula.
    """
    ny, nx, scale = 101, 121, 4
    lat_min, dy = 36.0, 0.1
    grid = {"nx": nx, "ny": ny, "lonMin": 6.5, "latMin": lat_min,
            "dx": 0.1, "dy": dy}
    resample = mod.build_row_resampler(grid, scale)
    # Campo sonda: il valore e' l'INDICE della riga di dati.
    probe = np.tile(np.arange(ny, dtype=np.float32)[:, None], (1, nx))
    out = np.asarray(resample(probe))
    assert out.shape == ((ny - 1) * scale + 1, (nx - 1) * scale + 1)
    row_of, col_of, H, W = mod._grid_to_pixel(grid, scale)
    assert H == out.shape[0] and W == out.shape[1]
    # Per ogni riga di dati j, la riga di output row_of[j] deve valere j.
    for j in (0, 1, 7, 25, 50, 73, ny - 2, ny - 1):
        r = float(row_of[j])
        assert 0.0 <= r <= H - 1
        # out[r] e' la posizione (float) della riga di dati alla riga di output r
        got = float(np.interp(r, np.arange(out.shape[0], dtype=np.float64),
                              out[:, 0].astype(np.float64)))
        assert abs(got - j) < 0.05, ("riga %d mappata su %d (valore %.4f)"
                                     % (j, r, got))
    # La mappa e' monotona e crescente verso il NORD (row 0 = sud -> riga alta).
    assert np.all(np.diff(row_of) < 0.0)
    assert row_of[0] > row_of[ny - 1]
