# -*- coding: utf-8 -*-
"""Font delle etichette del DISCO polare (1.2.4.45).

Perche' esiste questo test
--------------------------
Il disco non si legge con il modello dello strip Mercator. Il client
(``fmPolarPaintProjectedDisc``, in ``mri-light-*.html``) disegna il quadrato
del disco con raggio ``0.48 * min(w, h)`` del pannello e lato
``2*raggio*N/(N-1)``: con il pannello desktop MISURATO (754x560 -> min 560) e
N=1000 il fattore immagine->schermo e' ``0.96*560/999 = 0,538138``. Il modello
storico usava 0,56 (``min(1008/N, 560/N)``, il fit dello strip) e sceglieva
18 px = 13 px d'inchiostro = 6,99 px a schermo.

Misure sul frame reale (``north/dwd_icon`` 2026-10-26T1200Z, WebP pubblicato
sul branch ``fm2-rendered``, riprodotto byte-identico in locale con
``--north-projection disc --north-disc-size 1000 --method 4``):

===========================  ==========  ===============  ===============
font (px di file)            inchiostro  a schermo        etichette
===========================  ==========  ===============  ===============
18 (storico)                 13 px       6,99 px          58
27                           19 px       10,22 px         51
29 (scelto)                  20 px       10,76 px         51
31                           22 px       11,83 px         50
===========================  ==========  ===============  ===============

Le cifre a schermo misurate sull'immagine finale (non dal font) sono 22 px di
inchiostro nel file x 0,53814 = 11,84 px: il supersampling 1,5x aggiunge ~1 px
di antialiasing. Sovrapposizioni fra etichette: 0 prima e 0 dopo (il
composito e' ``strict``); le etichette perse sono 7 su 58 (-12%).

Il test non tocca il rendering: verifica la SCELTA del font e la NON
REGRESSIONE delle altre regioni (che devono continuare a uscire byte-identiche,
e quindi a ricevere esattamente il font di prima).
"""
import importlib.util
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")

# Pannello CSS misurato (#leaflet-map: width 100% di una colonna da 754 px,
# height 560 px) e lato del disco pubblicato.
PANEL_W = 754.0
PANEL_H = 560.0
DISC_N = 1000

# Valori STORICI (pre-1.2.4.45) delle regioni che non devono cambiare. Sono
# misurati sul renderer di `main` (194.570 byte, sha256 753E5831...4F128):
# ogni riga e' (out_w, out_h, region, font atteso).
HISTORICAL = [
    (2879, 521, "north", 29),     # strip Mercator: il modello a 0,35
    (900, 700, "italia", 12),
    (2000, 1000, "domain", 20),
    (1440, 281, "domain", 14),
    (5000, 4000, "italia", 48),
]

# 1.2.4.52 - ``europa`` non e' piu' una regione "storica": il suo pannello e' 754
# px CSS (``PANEL_WIDTH_PX``) e il target di prodotto e' 8 px di cifra, non i 7 px
# su 1008 del modello nato per la striscia north. Le taglie sono MISURATE su
# Pillow 12.3 (9 px d'inchiostro a 12 px, 20 a 29 px). 1121x701 e 1761x897 non
# sono piu' pubblicate (erano le forme stirate), restano come vettori del modello.
EUROPA = [
    (1121, 701, 18),
    (1121, 1111, 23),
    (1761, 897, 27),
    (1761, 1390, 29),
]


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_fm_webp_discfont", RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()


def _historical_strip_model(out_w, out_h):
    """Modello dello strip PRIMA della 1.2.4.45, ricopiato dalla formula.

    Serve come baseline: se il disco non passa di qui, ogni regione deve
    restituire ESATTAMENTE questo numero, altrimenti la promessa
    "italia/domain byte-identiche" e' falsa. ``europa`` NON e' piu' in questa
    lista dal 1.2.4.52: ha un target proprio (8 px sul pannello da 754 px).
    """
    factor = min(mod.PANEL_WIDTH_PX_MIN / float(out_w),
                 mod.PANEL_HEIGHT_PX / float(out_h))
    need = float(mod.MIN_SCREEN_DIGIT_PX) / factor
    if need <= float(mod._font_digit_height(mod.LABEL_FONT_PX)):
        return mod.LABEL_FONT_PX
    for px in range(mod.LABEL_FONT_PX + 1, mod.LABEL_FONT_MAX_PX + 1):
        if float(mod._font_digit_height(px)) >= need - 1e-9:
            return px
    return mod.LABEL_FONT_MAX_PX


# --------------------------------------------------------------------------- #
# 1. La geometria dello schermo (fattore del client, non quello dello strip)
# --------------------------------------------------------------------------- #
def test_disc_screen_factor_is_the_client_geometry():
    """0,538138 e NON 0,56 (strip) ne' 0,5376 (senza il fattore N/(N-1))."""
    factor = mod.disc_screen_factor(DISC_N)
    expected = 0.96 * min(PANEL_W, PANEL_H) / (DISC_N - 1)
    assert factor == pytest.approx(expected, rel=1e-12)
    assert factor == pytest.approx(0.538138, abs=1e-6)
    # I due modelli sbagliati, per documentare di quanto sbagliavano.
    assert abs(factor - 0.56) > 0.02
    assert abs(factor - 0.5376) > 1e-4


def test_disc_screen_factor_uses_the_disc_side_not_the_width():
    """Il pannello e' 754x560: il MIN e' l'altezza, il fattore scende con N."""
    big = mod.disc_screen_factor(2000)
    small = mod.disc_screen_factor(1000)
    assert big < small
    assert big == pytest.approx(0.96 * 560.0 / 1999.0, rel=1e-12)
    assert mod.disc_screen_factor(1) == 0.0


# --------------------------------------------------------------------------- #
# 2. La scelta del font: >= 10,5 px a schermo, e il PIU' PICCOLO che ci arriva
# --------------------------------------------------------------------------- #
def test_disc_font_reaches_the_screen_target():
    px = mod.label_font_px(DISC_N, DISC_N, 0, region="north", disc=True)
    ink = float(mod._font_digit_height(px))
    screen = ink * mod.disc_screen_factor(DISC_N)
    assert px == 29, "misurato: 29 px e' il minimo che raggiunge 10,5 px"
    assert ink == 20.0
    assert screen >= mod.DISC_MIN_SCREEN_DIGIT_PX, (px, ink, screen)
    assert screen >= 10.0, "obiettivo di prodotto: almeno 10 px a schermo"
    assert screen == pytest.approx(10.76, abs=0.02)


def test_disc_font_is_the_smallest_that_reaches_the_target():
    """Nessun sovradimensionamento: 28 px NON basta, 29 si'.

    Impedisce che un domani una costante "di comodo" (es. 36) passi il test
    solo perche' e' grande: il modello deve restare l'inverso esatto della
    metrica misurata.
    """
    need = (mod.DISC_MIN_SCREEN_DIGIT_PX
            / mod.disc_screen_factor(DISC_N))
    assert float(mod._font_digit_height(28)) < need
    assert float(mod._font_digit_height(29)) >= need
    assert mod.label_font_px(DISC_N, DISC_N, 0, region="north", disc=True) == 29


def test_disc_font_scales_with_the_disc_size():
    """Disco piu' piccolo = piu' px di font (stesso schermo), fino al tetto."""
    assert mod.label_font_px(600, 600, 0, region="north", disc=True) == 18
    assert mod.label_font_px(1000, 1000, 0, region="north", disc=True) == 29
    assert mod.label_font_px(2000, 2000, 0, region="north", disc=True) == 48
    values = [mod.label_font_px(n, n, 0, region="north", disc=True)
              for n in (600, 800, 1000, 1500, 2000)]
    assert values == sorted(values), values   # disco piu' grande -> font piu' piccolo


def test_forced_font_wins_on_the_disc():
    """``--label-font-px 12`` deve ancora riprodurre i byte storici."""
    assert mod.label_font_px(DISC_N, DISC_N, 12, region="north", disc=True) == 12
    assert mod.label_font_px(DISC_N, DISC_N, 40, region="north", disc=True) == 40


# --------------------------------------------------------------------------- #
# 3. NON regressione: strip, italia, europa, domain
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("out_w,out_h,region,expected", HISTORICAL)
def test_other_regions_keep_the_historical_font(out_w, out_h, region, expected):
    assert mod.label_font_px(out_w, out_h, 0, region=region) == expected
    assert mod.label_font_px(out_w, out_h, 0, region=region) == \
        _historical_strip_model(out_w, out_h)


def _europa_strip_model(out_w, out_h):
    """Modello PROPRIO di ``europa`` (1.2.4.52): 8 px su un pannello da 754 px."""
    factor = min(mod.PANEL_WIDTH_PX / float(out_w),
                 mod.PANEL_HEIGHT_PX / float(out_h))
    need = 8.0 / factor
    if need <= float(mod._font_digit_height(mod.LABEL_FONT_PX)):
        return mod.LABEL_FONT_PX
    for px in range(mod.LABEL_FONT_PX + 1, mod.LABEL_FONT_MAX_PX + 1):
        if float(mod._font_digit_height(px)) >= need - 1e-9:
            return px
    return mod.LABEL_FONT_MAX_PX


@pytest.mark.parametrize("out_w,out_h,expected", EUROPA)
def test_europa_uses_its_own_eight_pixel_target(out_w, out_h, expected):
    """``europa`` non e' piu' "storica": 8 px sul pannello da 754, non 7 su 1008.

    Misurato su Pillow 12.3: a 1121x1111 il modello vecchio dava 20 px (9 px
    d'inchiostro x 0,5041 = 7,06 px a schermo), il nuovo 23 px (16 x 0,5041 =
    8,06). A 1761x1390: 25 -> 29 (7,25 -> 8,06).
    """
    got = mod.label_font_px(out_w, out_h, 0, region="europa")
    assert got == expected
    assert got == _europa_strip_model(out_w, out_h)
    # Il target e' piu' alto del modello storico: il font non puo' rimpicciolirsi.
    assert got >= _historical_strip_model(out_w, out_h)
    ink = float(mod._font_digit_height(got))
    factor = min(mod.PANEL_WIDTH_PX / float(out_w),
                 mod.PANEL_HEIGHT_PX / float(out_h))
    assert ink * factor >= 8.0, (out_w, out_h, got, ink * factor)


def test_strip_does_not_take_the_disc_model():
    """Il disco e lo strip condividono la REGIONE ``north``: il flag separa i
    due modelli, altrimenti lo strip cambierebbe font (e byte) senza motivo."""
    strip = mod.label_font_px(2879, 521, 0, region="north")
    disc_same_size = mod.label_font_px(2879, 521, 0, region="north", disc=True)
    assert strip == 29
    assert disc_same_size == 48, "sul disco quel lato chiederebbe il tetto"
    assert strip != disc_same_size


def test_region_gate_still_returns_12_for_untouched_regions():
    """Fuori da ``REGION_PIXEL_ASPECT`` (e fuori da ``north``) il font resta
    12 px qualunque sia la dimensione: era vero prima, deve restare vero dopo.
    ``italia`` e ``domain`` sono in ``REGION_PIXEL_ASPECT`` e seguono il modello
    storico; ``europa`` ha invece un target proprio dal 1.2.4.52."""
    assert mod.label_font_px(5000, 4000, 0, region="radar") == mod.LABEL_FONT_PX
    assert mod.label_font_px(5000, 4000, 0, region="satellite") == mod.LABEL_FONT_PX
    # e le regioni in tabella NON vengono toccate dal modello del disco
    assert mod.label_font_px(5000, 4000, 0, region="italia") == mod.LABEL_FONT_MAX_PX
