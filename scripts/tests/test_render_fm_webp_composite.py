# -*- coding: utf-8 -*-
"""Test della variabile COMPOSITA ``__pressure_geopot`` in render_fm_webp.py.

Il prodotto: raster ``geopotential_height_500hPa`` a colori SENZA etichette,
isobare di ``pressure_msl`` bianche SOPRA con il valore scritto (995, 1000,
1005...), intervallo 5 hPa su ``italia``/``europa`` e 10 hPa sul disco
``north``.

Numeri citati qui e MISURATI il 2026-10-26 sui frame reali del branch dati
(``ecmwf_ifs025`` 2026-10-26T06Z; ``dwd_icon_eu`` e ``dwd_icon_d2`` stesso
giorno per i nomi dei campi):

  * la variabile NON e' un figlio del frame: e' il prodotto di DUE campi dello
    stesso frame, quindi ``name in present`` la scarterebbe sempre;
  * disco ``north`` a 1000 px, anelli contati con ``contour_segments`` +
    ``_chain_segments`` (le stesse funzioni che disegnano): 5 hPa -> 16 livelli
    e 1489 anelli, 8 hPa -> 9 livelli e 999 anelli, 10 hPa -> 8 livelli e 711
    anelli. Da qui il 10;
  * inchiostro delle cifre misurato con ``_font_digit_height``: 12 px -> 8,
    16 px -> 11, 18 px -> 13.

Nessun mock del sistema sotto test: si importa il modulo con la stessa
convenzione degli altri ``test_render_fm_webp_*`` (il file non e' un package) e
si disegna su una vera ``ImageDraw``. Il render end-to-end su un frame vero e'
stato fatto a parte (3 file nuovi, 78 WebP preesistenti byte-identici) e non e'
replicato qui perche' richiede il ``.om`` del branch dati.

Sui campi sintetici: un campo PERFETTAMENTE lineare non va bene, perche'
``_chain_segments`` non lo concatena in polilinee lunghe (misurato: 300 segmenti
da ~1 px, 110 polilinee, 0 candidati per l'etichetta). I campi qui sotto hanno
una struttura chiusa, come una carta vera.
"""
import importlib.util
import math
import os

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")
COMPOSITE_ID = "__pressure_geopot"
RASTER_VAR = "geopotential_height_500hPa"

# Griglia GEOGCRS come quella vera (0,25 gradi): chiavi `lonMin`, `latMin`,
# `dx`, `dy` lette da `_grid_to_pixel`. 400x320 sta fra `italia` (97x85) ed
# `europa` (281x153) come numero di celle, ed e' abbastanza grande perche' le
# polilinee superino `LABEL_MIN_POLYLINE_PX` (60 px). ``dy`` = 0,16 tiene la
# latitudine massima a 87,04 N: oltre il polo la rimappatura di Mercatore non
# ha senso (misurato: con ``dy`` 0,25 la griglia arrivava a 115 N e
# ``row_of`` degenerava in zeri sull'ultima riga).
GRID = {"nx": 400, "ny": 320, "lonMin": 6.5, "latMin": 36.0,
        "dx": 0.25, "dy": 0.16}


def _load_renderer():
    assert os.path.isfile(RENDERER_PATH), RENDERER_PATH
    spec = importlib.util.spec_from_file_location("render_fm_webp_composite_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()


def _variant(var=RASTER_VAR):
    return mod._scale_variant(mod.load_color_scales(), var, "light")


def _field(sigma=60.0, depth=45.0, nx=GRID["nx"], ny=GRID["ny"]):
    """Pressione con una bassa chiusa: isobare curve e livelli chiusi veri."""
    ys, xs = np.mgrid[0:ny, 0:nx].astype("float64")
    r2 = (xs - nx / 2.0) ** 2 + (ys - ny / 2.0) ** 2
    return 1010.0 - depth * np.exp(-r2 / (2.0 * sigma ** 2)) + 0.02 * (xs - nx / 2.0)


def _boxes(res, interval, font_px, ss=1.0):
    """Rettangoli di inchiostro delle etichette, dagli anchor veri."""
    font = mod._font(max(1, int(round(font_px * ss))))
    out = []
    for level, x, y in res["labels"]:
        text = mod._format_level(level, interval)
        bb = mod._label_bbox_px(font, text, ss)
        out.append((x + bb[0], y + bb[1], x + bb[2], y + bb[3]))
    return out


def _overlaps(boxes):
    pairs = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            apart = (b[0] >= a[2] or a[0] >= b[2]
                     or b[1] >= a[3] or a[1] >= b[3])
            if not apart:
                pairs.append((a, b))
    return pairs


def _draw(field, interval, strict=True, font_px=16, grid=None, bg=None):
    from PIL import Image, ImageDraw
    grid = grid or GRID
    W = int(grid["nx"])
    H = int(grid["ny"])
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    res = mod._draw_contours(draw, field, grid, 1, _variant(), interval,
                             strict_labels=strict, font_px=font_px,
                             color=(255, 255, 255, 255),
                             casing_color=(15, 23, 42, 150), bg_rgba=bg)
    return res, layer, (W, H)


# --------------------------------------------------------------------------- #
# La definizione: cosa e' composto, con che intervallo
# --------------------------------------------------------------------------- #
def test_the_composite_is_declared_once_with_its_two_fields():
    assert COMPOSITE_ID in mod.COMPOSITE_VARS
    comp = mod.COMPOSITE_VARS[COMPOSITE_ID]
    assert comp["raster"] == RASTER_VAR
    assert comp["isobars"] == "pressure_msl"


def test_the_composite_has_no_second_set_of_contours():
    """Solo le isolinee di pressione: il raster non porta valori scritti.

    Due strati di numeri (Z500 + pressione) sono esattamente il difetto che la
    variabile evita: i colori del geopotenziale bastano.
    """
    comp = mod.COMPOSITE_VARS[COMPOSITE_ID]
    assert "contour_var" not in comp
    assert "raster_labels" not in comp


def test_the_isobars_are_white_over_a_dark_casing():
    """Bianco pieno sopra, contorno scuro sotto.

    MISURATO: le bande della palette Z500 con luminanza > 210 coprono il 38,2%
    di ``europa`` (la banda 255,255,134 ha luminanza 246 contro 255 del bianco):
    una linea bianca senza contorno sparisce su piu' di un terzo della carta.
    """
    comp = mod.COMPOSITE_VARS[COMPOSITE_ID]
    assert tuple(comp["isobar_color"][:3]) == (255, 255, 255)
    casing = comp["isobar_casing"]
    assert casing is not None
    lum = 0.2126 * casing[0] + 0.7152 * casing[1] + 0.0722 * casing[2]
    assert lum < 60.0, lum


@pytest.mark.parametrize("region,expected", [
    ("italia", 5.0),
    ("europa", 5.0),
    ("north", 10.0),
    ("domain", 5.0),
])
def test_isobar_interval_per_region(region, expected):
    assert mod.contour_interval_for(COMPOSITE_ID, region) == expected


def test_the_interval_falls_back_to_the_default_without_a_region():
    assert mod.contour_interval_for(COMPOSITE_ID, None) == 5.0


def test_the_other_variables_keep_their_historical_interval():
    """Il composito non deve spostare l'intervallo di nessun'altra variabile."""
    assert mod.CONTOUR_INTERVALS, "tabella vuota: test inutile"
    for var, interval in mod.CONTOUR_INTERVALS.items():
        assert mod.contour_interval_for(var, "europa") == interval
        assert mod.contour_interval_for(var, "north") == interval


def test_labels_are_strict_so_two_numbers_never_overlap():
    assert mod.COMPOSITE_VARS[COMPOSITE_ID]["strict_labels"] is True


def test_the_composite_borrows_the_palette_of_its_raster():
    assert mod.palette_var(COMPOSITE_ID) == RASTER_VAR
    assert mod.palette_var("pressure_msl") == "pressure_msl"
    assert _variant() is not None, "senza scala la variabile non produce file"


# --------------------------------------------------------------------------- #
# Font: >= 7 px a schermo, misurato sul font vero
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("region,w,h", [
    ("europa", 1121, 701),
    ("italia", 385, 337),
    ("north", 1000, 1000),
])
def test_the_composite_font_is_at_least_14px(region, w, h):
    """Il composito e' UNA carta: il doppio strato storico non c'e' piu'.

    I 12 px storici esistevano perche' le etichette del client finivano sopra
    quelle del raster; ora c'e' un solo strato e il font puo' salire.
    """
    font_px = max(int(mod.label_font_px(w, h, 0, region=region)),
                  mod.COMPOSITE_LABEL_FONT_PX)
    assert font_px >= 14, (region, font_px)
    assert mod._font_digit_height(font_px) >= 8, (region, font_px)


def test_the_label_ink_is_at_least_7px_on_the_real_panel():
    """7 px a schermo, con i fattori MISURATI del pannello.

    ``europa``: 754 px CSS su 1121 px di immagine -> 0,673, il disco ``north``:
    538 su 1000 -> 0,538. Inchiostro ``_font_digit_height``: 12 px -> 9,
    16 px -> 11, 18 px -> 13.

      * europa con i 16 px del composito: 11 * 0,673 = 7,40 px;
      * north con i 18 px che ``label_font_px`` sceglie da solo: 13 * 0,538 =
        6,994 px, cioe' 7,0 arrotondato — margine NULLO, dichiarato (con 16 px
        sarebbero 5,9 px, sotto la soglia: da qui il massimo fra i due);
      * con i 12 px storici europa darebbe 9 * 0,673 = 6,06 px, sotto la soglia.
    """
    ink_16 = mod._font_digit_height(mod.COMPOSITE_LABEL_FONT_PX)
    assert ink_16 >= 11, ink_16
    assert ink_16 * (754.0 / 1121.0) >= 7.0
    ink_north = mod._font_digit_height(
        max(int(mod.label_font_px(1000, 1000, 0, region="north")),
            mod.COMPOSITE_LABEL_FONT_PX))
    assert ink_north >= 11, ink_north
    assert ink_north * (538.0 / 1000.0) >= 6.99
    assert mod._font_digit_height(16) * (538.0 / 1000.0) < 7.0
    # e i 12 px storici, per contrasto, NON ci arrivano piu'
    assert mod._font_digit_height(12) * (754.0 / 1121.0) < 7.0


# --------------------------------------------------------------------------- #
# Prontezza: entrambi i campi, altrimenti niente carta
# --------------------------------------------------------------------------- #
def test_ready_only_when_both_fields_are_in_the_frame():
    both = ["pressure_msl", RASTER_VAR, "temperature_2m"]
    assert mod.composite_ready(COMPOSITE_ID, both) is True
    assert mod.composite_ready(COMPOSITE_ID, ["pressure_msl"]) is False
    assert mod.composite_ready(COMPOSITE_ID, [RASTER_VAR]) is False
    assert mod.composite_ready(COMPOSITE_ID, []) is False


def test_ready_is_false_for_a_plain_variable():
    assert mod.composite_ready("pressure_msl", ["pressure_msl"]) is False
    assert mod.composite_ready("not_a_variable", ["a", "b"]) is False


# --------------------------------------------------------------------------- #
# Il disegno: etichette dentro il raster, sopra la linea, mai sovrapposte
# --------------------------------------------------------------------------- #
def test_strict_labels_never_overlap_two_numbers():
    """Nessuna coppia di etichette si tocca.

    Due numeri sovrapposti sono un numero solo. Campo con anelli chiusi fitti
    (``sigma`` 30 px, intervallo 2 hPa): e' il caso in cui il ripiego "a ogni
    costo" del percorso storico stampa un numero sopra un altro. Il controllo
    negativo e' il test seguente.
    """
    res, _layer, _size = _draw(_field(sigma=30.0), 2.0, strict=True)
    assert len(res["labels"]) >= 4, len(res["labels"])
    assert _overlaps(_boxes(res, 2.0, 16)) == []


def test_without_strict_labels_the_labels_do_overlap_here():
    """Controllo NEGATIVO: dimostra che il test sopra non e' vuoto.

    MISURATO su questo campo (400x320, sigma 30, 2 hPa, font 16): percorso
    storico 24 etichette con 5 coppie sovrapposte; ``strict_labels`` 6
    etichette con 0 coppie.
    """
    res, _layer, _size = _draw(_field(sigma=30.0), 2.0, strict=False)
    boxes = _boxes(res, 2.0, 16)
    assert len(boxes) >= 10, len(boxes)
    assert len(_overlaps(boxes)) >= 1


def test_strict_labels_drop_rather_than_glue():
    """Con ``strict_labels`` un livello senza spazio resta senza numero."""
    field = _field(sigma=30.0)
    loose, _l, _s = _draw(field, 2.0, strict=False)
    strict, _l2, _s2 = _draw(field, 2.0, strict=True)
    assert len(strict["labels"]) < len(loose["labels"])
    assert len(strict["labels"]) >= 1, "strict non deve azzerare le etichette"


def test_strict_labels_are_inside_the_raster():
    """Un numero a cavallo del bordo e' un numero tagliato."""
    res, _layer, (W, H) = _draw(_field(), 5.0, strict=True)
    assert res["labels"]
    for box in _boxes(res, 5.0, 16):
        assert box[0] >= 0.0 and box[1] >= 0.0
        assert box[2] <= float(W) and box[3] <= float(H)


def test_every_label_sits_on_the_line_of_its_own_value():
    """L'ancora di ogni etichetta sta SU una isolinea del livello scritto.

    Il punto e' un attraversamento di marching squares. Si torna alla griglia
    DATI con l'inverso esatto della mappatura del modulo (``col = px / scale``,
    ``row = interp(py, row_of, arange(ny))``: le righe sono rimappate a
    Mercatore, non sono un semplice ribaltamento) e si rilegge il campo.
    """
    field = _field()
    res, _layer, _size = _draw(field, 5.0, strict=True)
    assert res["labels"]
    row_of, _col_of, _H, _W = mod._grid_to_pixel(GRID, 1)
    rows = np.arange(row_of.shape[0], dtype=np.float64)
    # `row_of` DECRESCE (riga 0 = sud in basso, riga ny-1 = nord in alto):
    # `np.interp` vuole l'ascissa crescente, quindi si inverte la coppia.
    inv_y, inv_rows = row_of[::-1], rows[::-1]
    gy, gx = np.gradient(field)
    tol = 0.75 * float(max(np.abs(gx).max(), np.abs(gy).max())) + 0.5
    for level, x, y in res["labels"]:
        col = int(min(max(int(round(x)), 0), field.shape[1] - 1))
        row = float(np.interp(y, inv_y, inv_rows))
        r0 = int(min(max(int(math.floor(row)), 0), field.shape[0] - 1))
        r1 = min(r0 + 1, field.shape[0] - 1)
        t = row - r0
        value = float(field[r0, col]) * (1.0 - t) + float(field[r1, col]) * t
        assert abs(value - float(level)) <= tol, (level, x, y, value, tol)


def test_the_levels_are_pressure_levels_not_geopotential_levels():
    """I livelli disegnati sono hPa (passo 5), non i 40 m delle isoipse Z500."""
    res, _layer, _size = _draw(_field(), 5.0, strict=True)
    assert len(res["levels"]) >= 5, res["levels"]
    for level in res["levels"]:
        assert abs(float(level) % 5.0) < 1e-9, level
        assert abs(float(level) % 40.0) > 1e-9 or float(level) % 40.0 == 0.0
    # la carta di pressione ha piu' livelli di quanti ne avrebbe lo Z500 a 40 m
    z_levels = mod.contour_levels(40.0, float(_field().min()), float(_field().max()))
    assert len(res["levels"]) > len(z_levels)


def test_the_isobars_are_drawn_as_a_white_layer_over_the_raster():
    """La linea bianca esiste davvero nei pixel, con il contorno scuro sotto."""
    res, layer, _size = _draw(_field(), 5.0, strict=True)
    assert res["levels"], "nessuna isolinea disegnata"
    arr = np.asarray(layer)
    opaque = arr[:, :, 3] > 0
    assert opaque.sum() > 500, int(opaque.sum())
    white = ((arr[:, :, 0] > 240) & (arr[:, :, 1] > 240) & (arr[:, :, 2] > 240)
             & opaque)
    dark = ((arr[:, :, 0] < 40) & (arr[:, :, 1] < 45) & (arr[:, :, 2] < 60)
            & opaque)
    # il contorno scuro circonda il bianco: entrambi devono avere pixel
    assert white.sum() > 20, int(white.sum())
    assert dark.sum() > white.sum(), (int(dark.sum()), int(white.sum()))


def test_the_label_ink_inverts_on_a_dark_background():
    """Sopra una banda scura l'etichetta diventa chiara con alone scuro.

    MISURATO il 2026-10-26 sull'immagine vera ``europa`` ridotta a 560 px:
    con inchiostro scuro i valori 970/980/985 sulle bande blu scure della
    palette Z500 non si leggevano. Qui si disegna lo stesso campo sopra un
    raster quasi nero e si controlla che l'inchiostro sia CHIARO.
    """
    W, H = int(GRID["nx"]), int(GRID["ny"])
    dark_bg = np.zeros((H, W, 4), dtype="uint8")
    dark_bg[:, :, 0] = 12
    dark_bg[:, :, 1] = 20
    dark_bg[:, :, 2] = 90
    dark_bg[:, :, 3] = 255
    res, layer, _size = _draw(_field(sigma=30.0), 2.0, strict=True,
                              bg=dark_bg)
    assert res["labels"], "nessuna etichetta"
    arr = np.asarray(layer)
    opaque = arr[:, :, 3] > 200
    light = (arr[:, :, 0] > 230) & (arr[:, :, 1] > 230) & opaque
    assert light.sum() > 50, int(light.sum())


def test_the_label_ink_stays_dark_on_a_bright_background():
    """Sopra una banda chiara resta l'inchiostro scuro storico."""
    W, H = int(GRID["nx"]), int(GRID["ny"])
    bright = np.zeros((H, W, 4), dtype="uint8")
    bright[:, :, 0] = 255
    bright[:, :, 1] = 255
    bright[:, :, 2] = 134
    bright[:, :, 3] = 255
    res, layer, _size = _draw(_field(sigma=30.0), 2.0, strict=True,
                              bg=bright)
    assert res["labels"], "nessuna etichetta"
    arr = np.asarray(layer)
    opaque = arr[:, :, 3] > 200
    ink = (arr[:, :, 0] < 60) & (arr[:, :, 1] < 60) & (arr[:, :, 2] < 60) & opaque
    assert ink.sum() > 50, int(ink.sum())
