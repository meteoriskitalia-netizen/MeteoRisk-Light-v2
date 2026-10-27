# -*- coding: utf-8 -*-
"""Stile del COMPOSITO sul DISCO polare: nero su bianco (render_fm_webp.py).

Il prodotto: la carta emisferica ``__pressure_geopot`` (raster Z500 + isobare di
pressione con il valore scritto) sul disco polare ``north`` non usa piu' il
bianco-su-scuro storico, ma isobare NERE su casing BIANCO ed etichette NERE con
alone BIANCO, come la carta di riferimento (Meteociel):
l'utente l'ha segnalata come "numeri troppo grandi", "si fondono con i colori",
"le coste si fondono con le isobare".

Numeri MISURATI il 2026-10-27 sul frame reale ``dwd_icon``
2026-10-27T12:00Z, disco 1000 px, 58 etichette:

  * prima: font 29 px -> 20 px d'inchiostro = 2,0% del diametro; dopo: font
    20 px -> 14 px = 1,40%; a 614 px di diametro l'inchiostro passa da 12,3 a
    8,6 px (soglia di prodotto 8 px);
  * prima: coppia isobara/casing = bianco 242 + scuro (15,23,42) alpha 150;
    dopo: nero (17,17,17) 242 + bianco alpha 150. Contrasto WCAG locale minimo
    sulle 31 bande Z500: 5,02 -> 6,38 (mediana 11,75 -> 9,60);
  * prima: inchiostro delle etichette ADATTIVO (bianco sotto luminanza 110);
    dopo: sempre nero, alone bianco. Il test negativo e' sulla carta STRIP, che
    l'adattivo lo tiene ancora (e resta byte-identica).

Il file non e' un package: si importa il modulo con la stessa convenzione degli
altri ``test_render_fm_webp_*``. Nessun mock del disegno: si usa una vera
``ImageDraw`` e si contano i pixel del layer RGBA che il renderer produce.
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

# Griglia GEOGCRS come quella vera (0,25 gradi), la stessa del test del
# composito: abbastanza grande perche' le polilinee superino
# ``LABEL_MIN_POLYLINE_PX`` e i livelli chiusi ricevano un'etichetta.
GRID = {"nx": 400, "ny": 320, "lonMin": 6.5, "latMin": 36.0,
        "dx": 0.25, "dy": 0.16}


def _load_renderer():
    assert os.path.isfile(RENDERER_PATH), RENDERER_PATH
    spec = importlib.util.spec_from_file_location("render_fm_webp_disc_style_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()


def _field(sigma=30.0, depth=45.0):
    """Pressione con una bassa chiusa: anelli veri, piu' livelli etichettabili."""
    ys, xs = np.mgrid[0:GRID["ny"], 0:GRID["nx"]].astype("float64")
    r2 = (xs - GRID["nx"] / 2.0) ** 2 + (ys - GRID["ny"] / 2.0) ** 2
    return 1010.0 - depth * np.exp(-r2 / (2.0 * sigma ** 2)) + 0.02 * (xs - GRID["nx"] / 2.0)


def _variant():
    return mod._scale_variant(mod.load_color_scales(), RASTER_VAR, "light")


def _weave(amp=5.0, kx=8.0, ky=8.0, nx=400, ny=320):
    """Campo a trama: molti anelli di ~100 px di perimetro, quindi molte etichette."""
    ys, xs = np.mgrid[0:ny, 0:nx].astype("float64")
    return 1010.0 + amp * np.sin(xs / kx) * np.cos(ys / ky)


def _draw(style=True, pad=None, bg=None, interval=2.0, font_px=None, gap=None,
          field=None):
    """One real draw. ``style=False`` = coppia storica (bianco + casing scuro)."""
    from PIL import Image, ImageDraw
    W, H = int(GRID["nx"]), int(GRID["ny"])
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    z = _field() if field is None else field
    if style:
        res = mod._draw_contours(
            draw, z, GRID, 1, _variant(), interval,
            strict_labels=True,
            font_px=font_px if font_px is not None
            else mod.composite_disc_font_px(1000),
            color=mod.COMPOSITE_ISOBAR_INK,
            casing_color=mod.COMPOSITE_ISOBAR_CASING,
            casing_px=mod.COMPOSITE_ISOBAR_CASING_PX,
            label_ink=mod.COMPOSITE_LABEL_INK,
            label_halo=mod.COMPOSITE_LABEL_HALO,
            label_halo_px=mod.COMPOSITE_LABEL_HALO_PX,
            smooth_joins=True, label_pad_px=pad, bg_rgba=bg,
            label_gap_px=gap)
    else:
        res = mod._draw_contours(
            draw, z, GRID, 1, _variant(), interval,
            strict_labels=True, font_px=mod.composite_disc_font_px(1000),
            color=(255, 255, 255, int(round(mod.CONTOUR_ALPHA * 255.0))),
            casing_color=(15, 23, 42, 150), bg_rgba=bg)
    return res, np.asarray(layer)


def _count(arr, rgba, alpha=None):
    a = arr if arr.ndim == 3 else np.asarray(arr).reshape(-1, 1, arr.shape[-1])
    m = ((a[:, :, 0] == rgba[0]) & (a[:, :, 1] == rgba[1])
         & (a[:, :, 2] == rgba[2]))
    if alpha is None:
        m &= a[:, :, 3] > 0
    else:
        m &= a[:, :, 3] == alpha
    return int(m.sum())


def _label_boxes(res, interval, font_px, pad=0.0):
    font = mod._font(max(1, int(round(font_px))))
    out = []
    for level, x, y in res["labels"]:
        bb = mod._label_bbox_px(font, mod._format_level(level, interval), 1.0)
        out.append((x + bb[0] - pad, y + bb[1] - pad,
                    x + bb[2] + pad, y + bb[3] + pad))
    return out


def _label_gap_mask(shape, res, interval, font_px, pad=6):
    """True FUORI dai riquadri delle etichette (``pad`` px di margine)."""
    keep = np.ones(shape[:2], dtype=bool)
    for x0, y0, x1, y1 in _label_boxes(res, interval, font_px):
        keep[max(0, int(y0) - pad):int(y1) + pad,
             max(0, int(x0) - pad):int(x1) + pad] = False
    return keep


def _dilate(mask, r):
    """Boolean dilation by ``r`` px (numpy only: the tests must not need scipy)."""
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            shifted = np.zeros_like(mask)
            ys = slice(max(0, dy), mask.shape[0] + min(0, dy))
            xs = slice(max(0, dx), mask.shape[1] + min(0, dx))
            ys2 = slice(max(0, -dy), mask.shape[0] + min(0, -dy))
            xs2 = slice(max(0, -dx), mask.shape[1] + min(0, -dx))
            shifted[ys, xs] = mask[ys2, xs2]
            out |= shifted
    return out


# --------------------------------------------------------------------------- #
# Le costanti: la coppia e' invertita, e vale SOLO per il disco
# --------------------------------------------------------------------------- #
def test_the_isobar_pair_is_black_over_white():
    assert tuple(mod.COMPOSITE_ISOBAR_INK[:3]) == (17, 17, 17)
    assert mod.COMPOSITE_ISOBAR_INK[3] == 255
    assert tuple(mod.COMPOSITE_ISOBAR_CASING[:3]) == (255, 255, 255)
    assert mod.COMPOSITE_ISOBAR_CASING[3] == 150
    # larghezze: linea 1,0-1,2 px su casing piu' largo (2,5 px TOTALE)
    assert 1.0 <= mod.CONTOUR_LINE_WIDTH <= 1.2
    assert 2.0 <= mod.COMPOSITE_ISOBAR_CASING_PX <= 3.0
    assert mod.COMPOSITE_ISOBAR_CASING_PX > mod.CONTOUR_LINE_WIDTH


def test_the_label_is_black_with_a_white_halo_of_2_5_px():
    assert tuple(mod.COMPOSITE_LABEL_INK[:3]) == (17, 17, 17)
    assert tuple(mod.COMPOSITE_LABEL_HALO[:3]) == (255, 255, 255)
    assert mod.COMPOSITE_LABEL_HALO_PX == 2.5


def test_the_style_is_applied_only_to_the_polar_disc():
    """``italia``/``europa``/``domain`` e il composito strip restano com'erano.

    ``composite_style(False) is None`` e' la garanzia di byte-identita': il
    percorso storico non riceve NESSUN parametro nuovo.
    """
    assert mod.composite_style(False) is None
    disc = mod.composite_style(True)
    assert disc is not None
    assert tuple(disc["isobar_color"][:3]) == (17, 17, 17)
    assert tuple(disc["isobar_casing"][:3]) == (255, 255, 255)
    assert disc["adaptive_labels"] is False
    assert disc["smooth_joins"] is True
    # la carta strip (COMPOSITE_VARS) NON e' stata toccata
    comp = mod.COMPOSITE_VARS[COMPOSITE_ID]
    assert tuple(comp["isobar_color"][:3]) == (255, 255, 255)
    assert tuple(comp["isobar_casing"][:3]) == (15, 23, 42)


# --------------------------------------------------------------------------- #
# Font: 1,4% del diametro e >= 8 px d'inchiostro a 614 px
# --------------------------------------------------------------------------- #
def test_the_disc_font_puts_the_ink_at_1_4_percent_of_the_diameter():
    """MISURATO: 20 px di font -> 14 px d'inchiostro su un disco di 1000 px."""
    font = mod.composite_disc_font_px(1000)
    ink = mod._font_digit_height(font)
    assert (font, ink) == (20, 14), (font, ink)
    assert abs(ink / 1000.0 - 0.014) < 1e-9
    # a 614 px di diametro (misura di prodotto) restano 8,6 px: sopra gli 8
    assert ink * 614.0 / 1000.0 >= 8.0
    # CONTROLLO NEGATIVO: 18 px (13 px d'inchiostro) scenderebbero a 7,98 px
    assert mod._font_digit_height(18) * 614.0 / 1000.0 < 8.0
    # e il font storico del disco (29 px, 20 px d'inchiostro) era il 2,0%
    assert mod._font_digit_height(29) / 1000.0 >= 0.0199


@pytest.mark.parametrize("side,expected", [(614, 12), (1500, 31), (2000, 38)])
def test_the_disc_font_scales_with_the_disc(side, expected):
    font = mod.composite_disc_font_px(side)
    assert font == expected, (side, font)
    ink = mod._font_digit_height(font)
    assert ink / float(side) <= 0.0155, (side, font, ink)


def test_the_disc_font_is_capped_by_the_global_bound():
    """Oltre il tetto ``LABEL_FONT_MAX_PX`` il rapporto scende: limite DICHIARATO.

    Nessun disco pubblicato supera 1000 px (le workflow passano 1000); se un
    giorno ne servisse uno da 4000, il tetto a 48 px darebbe 0,875% invece di
    1,40% e il tetto andrebbe alzato di proposito, non per caso.
    """
    assert mod.composite_disc_font_px(4000) == mod.LABEL_FONT_MAX_PX
    assert mod._font_digit_height(mod.LABEL_FONT_MAX_PX) / 4000.0 < 0.010


def test_a_forced_font_wins_over_the_disc_ratio():
    assert mod.composite_disc_font_px(1000, 29) == 29
    assert mod.composite_disc_font_px(1000, 12) == 12


def test_the_other_disc_variables_keep_the_10_5_px_floor():
    """``DISC_MIN_SCREEN_DIGIT_PX`` non e' stata toccata: vale per le altre 34.

    Solo il composito ha un modello suo (1,4% del diametro, 8 px a 614): il
    disco delle altre variabili continua a scegliere 29 px.
    """
    assert mod.DISC_MIN_SCREEN_DIGIT_PX == 10.5
    assert mod.label_font_px(1000, 1000, 0, region="north", disc=True) == 29


# --------------------------------------------------------------------------- #
# I pixel: la coppia e' davvero invertita e il casing e' piu' largo del core
# --------------------------------------------------------------------------- #
def test_the_rendered_isobars_are_black_with_a_white_casing():
    res, arr = _draw(pad=None)
    assert _count(arr, (17, 17, 17, 255), alpha=255) > 500
    assert _count(arr, (255, 255, 255, 150), alpha=150) > 500
    # La coppia STORICA non compare FUORI dalle etichette. Nelle etichette lo
    # stroke di Pillow produce pixel d'alone antialiasati con alpha qualunque
    # (misurato: 3 px a 255,255,255,242 su 128.000): non sono la linea storica.
    keep = _label_gap_mask(arr.shape, res, 2.0, 20)
    assert _count(arr[keep], (255, 255, 255, 242), alpha=242) == 0
    assert _count(arr[keep], (15, 23, 42, 150), alpha=150) == 0


def test_the_rendered_historical_pair_is_the_negative_control():
    """Controllo NEGATIVO: con lo stile strip la coppia bianco/scuro c'e'."""
    _res, arr = _draw(style=False, pad=None)
    assert _count(arr, (255, 255, 255, 242), alpha=242) > 500
    assert _count(arr, (15, 23, 42, 150), alpha=150) > 500
    assert _count(arr, (17, 17, 17, 255), alpha=255) == 0


def test_the_casing_is_wider_than_the_core():
    """Il bianco (casing, 2,5 px) copre piu' pixel del nero (core, 1,2 px)."""
    _res, arr = _draw(pad=None)
    core = _count(arr, (17, 17, 17, 255), alpha=255)
    casing = _count(arr, (255, 255, 255, 150), alpha=150)
    assert casing > core, (casing, core)


def test_the_label_ink_does_not_invert_on_a_dark_background():
    """Il difetto che l'adattivo creava: inchiostro BIANCO sulle bande scure.

    Con lo stile del disco l'inchiostro e' lo stesso su un fondo scuro e su uno
    chiaro; sulla carta strip lo stesso fondo scuro lo fa ancora diventare
    bianco (controllo positivo che il ramo adattivo esiste ed e' solo spento
    qui).
    """
    W, H = int(GRID["nx"]), int(GRID["ny"])
    dark = np.zeros((H, W, 4), dtype="uint8")
    dark[:, :, 0] = 12
    dark[:, :, 1] = 20
    dark[:, :, 2] = 90
    dark[:, :, 3] = 255
    bright = np.zeros((H, W, 4), dtype="uint8")
    bright[:, :, 0] = 255
    bright[:, :, 1] = 255
    bright[:, :, 2] = 134
    bright[:, :, 3] = 255

    _r1, a_dark = _draw(bg=dark)
    _r2, a_bright = _draw(bg=bright)
    for arr in (a_dark, a_bright):
        assert _count(arr, mod.COMPOSITE_LABEL_INK, alpha=255) > 50
        assert _count(arr, mod.COMPOSITE_LABEL_INK_ON_DARK) == 0

    _r3, a_strip = _draw(style=False, bg=dark)
    assert _count(a_strip, mod.COMPOSITE_LABEL_INK_ON_DARK) > 50


def test_the_label_halo_is_a_2_5_px_stroke_not_a_1_px_ring():
    """L'alone arriva a 2-3 px dal glifo (gli 8 offset storici a 1 px).

    Si prende l'etichetta piu' in alto e si misura quanti pixel d'alone (bianco
    pieno) stanno a distanza 2-3 px dall'inchiostro: con l'alone storico (8
    copie a 1 px) quella corona e' vuota.
    """
    res, arr = _draw(pad=None)
    assert res["labels"]
    ink = (arr[:, :, 0] == 17) & (arr[:, :, 1] == 17) & (arr[:, :, 2] == 17) \
        & (arr[:, :, 3] == 255)
    white = (arr[:, :, 0] == 255) & (arr[:, :, 1] == 255) \
        & (arr[:, :, 2] == 255) & (arr[:, :, 3] == 255)
    near1 = _dilate(ink, 1)
    near3 = _dilate(ink, 3)
    corona = white & near3 & ~near1
    assert int(corona.sum()) > 20, int(corona.sum())


def test_the_line_has_a_gap_under_each_label():
    """``label_pad_px`` cancella la linea SOTTO il valore (convenzione Meteociel).

    MISURATO su questo campo (400x320, sigma 30, 2 hPa, font 20): 312 pixel
    cancellati, 160 dei quali erano linea/casing (non alone), su 4 etichette su
    4. Senza il buco la linea attraversa il numero e, in un minimo fitto, il
    valore non si legge.
    """
    res_hist, arr_hist = _draw(pad=None)
    res_pad, arr_pad = _draw(pad=mod.COMPOSITE_DISC_STYLE["label_pad_px"])
    assert [x[:3] for x in res_hist["labels"]] == [x[:3] for x in res_pad["labels"]]
    erased = (arr_hist[:, :, 3] > 0) & (arr_pad[:, :, 3] == 0)
    assert int(erased.sum()) >= 100, int(erased.sum())
    ep = arr_hist[erased]
    not_white = ~((ep[:, 0] > 200) & (ep[:, 1] > 200) & (ep[:, 2] > 200))
    assert int(not_white.sum()) >= 50, int(not_white.sum())
    # il buco e' locale: ogni etichetta ne ha uno nel proprio riquadro
    boxes = _label_boxes(res_pad, 2.0, 20, pad=2.0)
    assert boxes
    for x0, y0, x1, y1 in boxes:
        sl = erased[max(0, int(y0)):int(y1) + 1, max(0, int(x0)):int(x1) + 1]
        assert sl.any(), (x0, y0, x1, y1)
    # e il buco non toglie pixel alle etichette: nessun pixel AGGIUNTO
    assert int(((arr_pad[:, :, 3] > 0) & ~(arr_hist[:, :, 3] > 0)).sum()) == 0

def _min_gap(boxes):
    best = float("inf")
    for i in range(len(boxes)):
        for k in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[k]
            g = math.hypot(max(b[0] - a[2], a[0] - b[2], 0.0),
                           max(b[1] - a[3], a[1] - b[3], 0.0))
            if g < best:
                best = g
    return best


def test_the_minimum_gap_keeps_the_halos_of_two_labels_apart():
    """``label_gap_px`` impone una DISTANZA, non solo l'assenza di sovrapposizione.

    MISURATO sul frame vero (dwd_icon 2026-10-27T12Z): con il font a 20 px e la
    sola regola "niente sovrapposizione" due "1015" finivano a 1,3 px e i due
    aloni bianchi (2,5 px per lato) si fondevano in una macchia sola. Con
    ``label_gap_px = 4`` il minimo misurato sul campo a trama qui sotto passa da
    1,91 a 8,93 px (etichette 35 -> 20), e sul frame vero da 1,3 a 11,95 px
    (etichette 58 -> 56).
    """
    z = _weave()
    loose, _a = _draw(pad=2.0, field=z)
    tight, _b = _draw(pad=2.0, field=z, gap=mod.COMPOSITE_DISC_STYLE["label_gap_px"])
    gap = mod.COMPOSITE_DISC_STYLE["label_gap_px"]
    assert gap == 4.0
    free = _label_boxes(loose, 2.0, 20)
    kept = _label_boxes(tight, 2.0, 20)
    assert len(free) >= 10, len(free)
    assert len(kept) >= 5, len(kept)
    assert len(kept) < len(free), (len(kept), len(free))
    assert _min_gap(free) < gap, _min_gap(free)
    assert _min_gap(kept) >= gap - 0.01, _min_gap(kept)
