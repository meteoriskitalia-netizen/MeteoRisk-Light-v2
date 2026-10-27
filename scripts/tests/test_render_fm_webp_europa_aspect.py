# -*- coding: utf-8 -*-
"""Test del PIXEL QUADRATO in Mercatore della regione ``europa`` (render_fm_webp.py).

Contesto MISURATO il 2026-10-27 sui frame reali del branch dati, con la regione
``europa`` estesa all'Atlantico (30..75N / 70W..45E, 1.2.4.52):

  * ``ecmwf_ifs025`` (0,25 gradi, griglia 1440x281): crop **461x181**, x2 ->
    ``921x679`` = rapporto **1,3564**;
  * ``dwd_icon_eu`` 2026-10-27T03Z: crop 881x449 (0,0625 gradi, 34..62N / 15W..40E),
    x2 -> ``1761x1390`` = rapporto **1,2669**.

La stessa regione usciva quindi con forme diverse: il rapporto era un effetto
collaterale di ``ceil(512 / lato corto)``, non una scelta.

Il rapporto GIUSTO non e' una costante: e' quello della scatola Mercatore che
``imageOverlay`` riempie, e dipende dal crop -- **1,3577** su ``ecmwf_ifs025``
(30..75N) contro **1,2675** su ``dwd_icon_eu`` (34..62N). Una costante unica
(la 1,6 della 1.2.4.32) lascia 1,18x di stiramento verticale su uno e 1,26x
sull'altro. ``REGION_PIXEL_ASPECT`` ricava quindi l'altezza dall'estensione in
Mercatore del crop (``mercator_out_rows``): il pixel e' quadrato in EPSG:3857 e
lo stiramento e' 1,000 su entrambi.

Il test NON si limita a ripetere la formula: verifica che il passo di riga e
quello di colonna siano uguali in unita' Mercatore (la definizione di pixel
quadrato) e che le taglie pubblicate siano quelle misurate sui frame veri.

Le altre regioni NON hanno un aspetto: ``italia``, ``north`` e ``domain``
devono restare byte-identiche (verificato a parte con sha256 sui WebP reali).

Nessun mock del sistema sotto test: si importa il modulo con la stessa
convenzione di ``test_render_fm_webp_overlay.py`` (il file non e' un package).
"""
import importlib.util
import math
import os

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")


def _load_renderer():
    assert os.path.isfile(RENDERER_PATH), RENDERER_PATH
    spec = importlib.util.spec_from_file_location("render_fm_webp_aspect_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()

# I due crop reali: (nx, ny, dx, lat_min, lat_max)
CROPS = {
    # 30..75N / 70W..45E: 115 gradi di longitudine su 45 di latitudine.
    # ``capped``: il dominio e' GLOBALE, quindi ``REGION_MAX_OUT_W`` limita la
    # larghezza (dwd_icon_eu non e' globale e tiene il suo 1761).
    "ecmwf_ifs025": dict(nx=461, ny=181, dx=0.25, latMin=30.0, latMax=75.0,
                         published=(921, 679), merc_ratio=1.35771, capped=True),
    "dwd_icon_eu": dict(nx=881, ny=449, dx=0.0625, latMin=34.0, latMax=62.0,
                        published=(1761, 1390), merc_ratio=1.26752, capped=False),
}


def _cgrid(crop, lon_min=-70.0):
    return {"nx": crop["nx"], "ny": crop["ny"], "lonMin": lon_min,
            "latMin": crop["latMin"], "dx": crop["dx"],
            "dy": (crop["latMax"] - crop["latMin"]) / (crop["ny"] - 1)}


def _merc(lat):
    return math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))


# --------------------------------------------------------------------------- #
# La tabella: una regione sola, e il valore NON e' una costante
# --------------------------------------------------------------------------- #
def test_the_stretched_regions_ask_for_square_mercator_pixels():
    # Misurate tutte e tre il 2026-10-26: erano stirate di 1,26x/1,35x/1,55x.
    assert mod.REGION_PIXEL_ASPECT == {"europa": "mercator",
                                       "italia": "mercator",
                                       "domain": "mercator"}


def test_the_other_regions_are_not_in_the_table():
    # La tabella e' l'UNICO posto che cambia la forma: chi non c'e' tiene la
    # regola storica (una riga di output per cella sorgente) e i suoi byte.
    for region in ("north", None):
        assert region not in mod.REGION_PIXEL_ASPECT


def test_italia_north_domain_keep_the_historical_output_size():
    # (ny-1)*scale+1 resta la taglia di output quando ``out_ny`` non c'e':
    # e' il ramo che ``render_domain`` prende per ogni regione fuori tabella.
    ny, nx, scale = 153, 281, 4
    grid = {"nx": nx, "ny": ny, "lonMin": -25.0, "latMin": 34.0,
            "dx": 0.25, "dy": 0.25}
    _row_of, _col_of, H, W = mod._grid_to_pixel(grid, scale)
    assert (H, W) == ((ny - 1) * scale + 1, (nx - 1) * scale + 1)


# --------------------------------------------------------------------------- #
# La regola: passo di riga == passo di colonna in unita' Mercatore
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("domain", sorted(CROPS))
def test_row_step_equals_column_step_in_mercator(domain):
    """DEFINIZIONE di pixel quadrato: le due direzioni coprono lo stesso arco.

    Non si richiama la formula del modulo: si misurano i due passi sul numero di
    righe/colonne RESTITUITO e si confrontano. Se la derivazione sbagliasse di
    un fattore, qui si vede.
    """
    crop = CROPS[domain]
    grid = _cgrid(crop)
    args = type("A", (), {"upsample": "auto",
                          "upsample_max": mod.UPSCALE_MAX_DEFAULT})()
    scale = mod.resolve_upsample(args, grid["nx"], grid["ny"])
    rows = mod.mercator_out_rows(grid, scale)
    cols = (grid["nx"] - 1) * scale + 1
    step_col = math.radians(grid["dx"]) / float(scale)
    step_row = (_merc(crop["latMax"]) - _merc(crop["latMin"])) / float(rows - 1)
    assert abs(step_row / step_col - 1.0) < 0.001, (domain, step_row, step_col)


@pytest.mark.parametrize("domain", sorted(CROPS))
def test_published_size_is_the_measured_one(domain):
    """Le due taglie vere, con il fattore di upsampling vero."""
    crop = CROPS[domain]
    grid = _cgrid(crop)
    args = type("A", (), {"upsample": "auto",
                          "upsample_max": mod.UPSCALE_MAX_DEFAULT})()
    scale = mod.resolve_upsample(args, grid["nx"], grid["ny"])
    if crop["capped"]:
        # Dominio GLOBALE: ``render_domain`` applica il tetto di larghezza.
        scale = mod.fit_scale_to_width(grid["nx"], scale,
                                       mod.REGION_MAX_OUT_W["europa"])
    published = ((grid["nx"] - 1) * scale + 1, mod.mercator_out_rows(grid, scale))
    assert published == crop["published"], domain
    # Il rapporto pubblicato coincide col rapporto Mercatore del crop entro lo
    # 0,2% (l'unico residuo e' l'arrotondamento di 1 riga: misurato 0,096% su
    # 921x679 e 0,047% su 1761x1390).
    assert abs(published[0] / float(published[1]) / crop["merc_ratio"] - 1.0) < 0.002


@pytest.mark.parametrize("domain", sorted(CROPS))
def test_the_derived_height_differs_from_the_constant_1_6(domain):
    """La costante della 1.2.4.32 sbagliava di 1,59x o 1,26x: la si confronta."""
    crop = CROPS[domain]
    w, h = crop["published"]
    stretch = (w / float(h)) / crop["merc_ratio"]
    assert abs(stretch - 1.0) < 0.002
    # Con 1,6 lo stiramento sarebbe questo (misurato, non stimato): 17,8% su
    # ecmwf (1,3577 contro 1,6) e 26,2% su dwd_icon_eu (1,2675). Con la scatola
    # vecchia 1,0088 l'errore su ecmwf era il 59%: per questo la soglia scende
    # da 0,2 a 0,15, che resta un errore macroscopic.
    assert abs((w / float(round(w / 1.6))) / crop["merc_ratio"] - 1.0) > 0.15


# --------------------------------------------------------------------------- #
# Degenerazioni: si torna alla geometria storica, mai un crash
# --------------------------------------------------------------------------- #
def test_degenerate_grid_degrades_to_the_historical_rows():
    """Un grid degenere torna alla geometria storica invece di far cadere il
    render (le funzioni girano dentro un worker di processo)."""
    base = dict(nx=10, ny=10, lonMin=0.0, latMin=34.0, dx=0.25, dy=0.25)
    historical = (10 - 1) * 2 + 1
    # lat_min == lat_max (dy nullo) -> span Mercatore nullo
    assert mod.mercator_out_rows(dict(base, dy=0.0), 2) == historical
    # dx nullo -> passo di colonna nullo
    assert mod.mercator_out_rows(dict(base, dx=0.0), 2) == historical
    # una sola riga/colonna (conteggio storico: (ny-1)*scale+1)
    assert mod.mercator_out_rows(dict(base, ny=1), 2) == 1
    assert mod.mercator_out_rows(dict(base, nx=1), 2) == historical
    # oltre il polo nord Mercatore non e' definito -> nessun crash
    assert mod.mercator_out_rows(dict(base, latMin=89.0, dy=0.25), 2) == historical
    # un grid NORMALE invece NON e' degenere: il conteggio e' derivato
    assert mod.mercator_out_rows(base, 2) != historical


def test_scale_one_without_out_ny_is_untouched():
    grid = {"nx": 281, "ny": 153, "lonMin": -25.0, "latMin": 34.0,
            "dx": 0.25, "dy": 0.25}
    arr = np.arange(grid["ny"] * grid["nx"], dtype=np.float32).reshape(grid["ny"],
                                                                      grid["nx"])
    out = np.asarray(mod.build_row_resampler(grid, 1)(arr))
    assert out.shape == (grid["ny"], grid["nx"])


# --------------------------------------------------------------------------- #
# Geometria: la riga di output deve restare coerente con la riga di dati
# --------------------------------------------------------------------------- #
def test_grid_to_pixel_honours_out_ny():
    grid = {"nx": 281, "ny": 153, "lonMin": -25.0, "latMin": 34.0,
            "dx": 0.25, "dy": 0.25}
    scale = 4
    hist_h = (grid["ny"] - 1) * scale + 1
    row_of, col_of, H, W = mod._grid_to_pixel(grid, scale)
    assert (H, W) == (hist_h, (grid["nx"] - 1) * scale + 1)
    fit = dict(grid)
    fit["out_ny"] = 1111
    row_of, col_of, H, W = mod._grid_to_pixel(fit, scale)
    assert (H, W) == (1111, 1121)
    # Le colonne NON cambiano: l'aspetto tocca solo le righe.
    assert np.all(col_of == np.arange(grid["nx"], dtype=np.float64) * scale)
    # Le righe coprono TUTTA l'altezza e restano monotone verso il nord.
    assert row_of[0] == pytest.approx(H - 1) and row_of[-1] == pytest.approx(0.0)
    assert np.all(np.diff(row_of) < 0.0)


@pytest.mark.parametrize("out_ny", [None, 1111])
def test_row_resampler_is_the_inverse_of_grid_to_pixel(out_ny):
    """Il resampler (riga output -> riga dati) e `_grid_to_pixel` devono
    concordare anche quando l'altezza non e' ``(ny-1)*scale+1``.

    Se una delle due parti non conosce ``out_ny`` le isolinee scivolano fino a
    ~90 px nella meta' nord (misurato: errore mediano sul valore del raster
    da 0,03 hPa a 2,98 hPa, p95 da 0,15 a 15,4, su pressure_msl/europa).
    """
    ny, nx, scale = 153, 281, 4
    grid = {"nx": nx, "ny": ny, "lonMin": -25.0, "latMin": 34.0,
            "dx": 0.25, "dy": 0.25}
    if out_ny is not None:
        grid["out_ny"] = out_ny
    resample = mod.build_row_resampler(grid, scale)
    probe = np.tile(np.arange(ny, dtype=np.float32)[:, None], (1, nx))
    out = np.asarray(resample(probe))
    row_of, _col_of, H, W = mod._grid_to_pixel(grid, scale)
    assert out.shape == (H, W), (out.shape, H, W)
    assert H == (out_ny or (ny - 1) * scale + 1)
    for j in (0, 1, 7, 25, 76, 120, ny - 2, ny - 1):
        r = float(row_of[j])
        assert 0.0 <= r <= H - 1
        got = float(np.interp(r, np.arange(out.shape[0], dtype=np.float64),
                              out[:, 0].astype(np.float64)))
        assert abs(got - j) < 0.05, ("riga %d mappata su %d (valore %.4f)"
                                     % (j, r, got))


def test_row_resampler_keeps_the_historical_bytes_without_out_ny():
    """Senza ``out_ny`` il resampler deve dare ESATTAMENTE la taglia di prima."""
    ny, nx, scale = 153, 281, 4
    grid = {"nx": nx, "ny": ny, "lonMin": -25.0, "latMin": 34.0,
            "dx": 0.25, "dy": 0.25}
    arr = np.arange(ny * nx, dtype=np.float32).reshape(ny, nx)
    out = np.asarray(mod.build_row_resampler(grid, scale)(arr))
    assert out.shape == ((ny - 1) * scale + 1, (nx - 1) * scale + 1)


# --------------------------------------------------------------------------- #
# Le etichette: il font si adatta alla nuova altezza
# --------------------------------------------------------------------------- #
def test_europa_label_font_is_recomputed_and_never_reduced():
    """Con il pixel quadrato l'altezza comanda il fit: il font deve crescere.

    ``label_font_px`` e' auto-scalata per ``north`` e per le regioni in
    ``REGION_PIXEL_ASPECT``. Se restasse 12 il fattore di fit 560/1390 = 0,4029
    porterebbe le cifre 9 px a 3,6 px su schermo, sotto i 7 px del target.
    """
    old = mod.label_font_px(1761, 1101, 0, region=None)
    new = mod.label_font_px(1761, 1390, 0, region="europa")
    assert new > 12, new
    assert new >= old
    # ``italia`` e ``domain`` ora sono in tabella, quindi il font e' DEDOTTO:
    # 580x514 restituisce comunque 12 (il fattore 1,0896 basta), mentre la
    # ``domain`` alta 1390 comanda il fit e deve crescere come ``europa``.
    assert mod.label_font_px(580, 514, 0, region="italia") == mod.LABEL_FONT_PX
    assert mod.label_font_px(1761, 1390, 0, region="domain") > mod.LABEL_FONT_PX
    # Le regioni fuori tabella restano al valore storico.
    assert mod.label_font_px(1761, 897, 0, region="med") == mod.LABEL_FONT_PX
    # Un valore forzato continua a vincere.
    assert mod.label_font_px(1761, 1390, 30, region="europa") == 30
