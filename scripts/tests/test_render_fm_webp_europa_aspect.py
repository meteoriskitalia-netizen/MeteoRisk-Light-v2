# -*- coding: utf-8 -*-
"""Test del RAPPORTO forzato della regione ``europa`` in render_fm_webp.py.

Contesto MISURATO il 2026-10-26 sui frame reali del branch dati:

  * ``ecmwf_ifs025`` 2026-10-26T00Z: crop 281x153 (0,25 gradi), upsampling x4
    -> ``1121x609`` = rapporto **1,8407**;
  * ``dwd_icon_eu`` 2026-10-26T09Z: crop 881x449 (0,0625 gradi), x2
    -> ``1761x897`` = rapporto **1,9632**.

La stessa regione usciva quindi con due forme diverse: il rapporto era un
effetto collaterale di ``ceil(512 / lato corto)``, non una scelta. Con
``REGION_ASPECT`` la forma e' una sola (1,6:1) e la larghezza resta quella
prodotta dal dato: l'altezza si RICALCOLA, mai la larghezza, perche' le colonne
hanno un upsampling intero esatto (``col_of[i] = i*scale``) e ridurle
butterebbe via risoluzione orizzontale.

Le altre regioni NON hanno un aspetto: ``italia``, ``north`` e ``domain``
devono restare byte-identiche (verificato a parte con sha256 sui WebP reali).

Nessun mock del sistema sotto test: si importa il modulo con la stessa
convenzione di ``test_render_fm_webp_overlay.py`` (il file non e' un package).
"""
import importlib.util
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


# --------------------------------------------------------------------------- #
# La tabella: una regione sola, e il numero e' quello dichiarato
# --------------------------------------------------------------------------- #
def test_only_europa_has_a_forced_aspect():
    assert mod.REGION_ASPECT == {"europa": 1.6}


def test_the_other_regions_keep_the_historical_height():
    # Il valore storico e' (ny-1)*scale+1: per queste regioni la funzione deve
    # restituire l'argomento, non un numero ricalcolato.
    for region in ("italia", "north", "domain", None):
        assert mod.region_out_height(region, 385, 337) == 337
        assert mod.region_out_height(region, 2879, 521) == 521


def test_europa_height_comes_from_the_width():
    # ecmwf_ifs025: 1121/1,6 = 700,6 -> 701 (rapporto 1,5991).
    assert mod.region_out_height("europa", 1121, 609) == 701
    assert abs(1121.0 / 701.0 - 1.6) < 0.001
    # dwd_icon_eu: 1761/1,6 = 1100,6 -> 1101 (rapporto 1,5995).
    assert mod.region_out_height("europa", 1761, 897) == 1101
    assert abs(1761.0 / 1101.0 - 1.6) < 0.001


def test_europa_never_loses_rows():
    # L'aspetto allunga: se il rapporto del dato e' gia' piu' stretto di 1,6
    # (altezza maggiore del necessario) non si taglia, si tiene l'altezza data.
    assert mod.region_out_height("europa", 1000, 900) == 900
    # E un aspetto degenere non produce mai un'immagine da 0/1 righe.
    assert mod.region_out_height("europa", 10, 1) >= 2
    assert mod.region_out_height("europa", 0, 5) == 5


# --------------------------------------------------------------------------- #
# Le due taglie reali, con il fattore di upsampling vero
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("nx,ny,bounds,expected", [
    # (crop della regione, come esce da region_crop su ecmwf_ifs025)
    (281, 153, (1121, 701), "ecmwf_ifs025 2026-10-26T00Z"),
    (881, 449, (1761, 1101), "dwd_icon_eu 2026-10-26T09Z"),
])
def test_europa_real_crop_sizes(nx, ny, bounds, expected):
    args = type("A", (), {"upsample": "auto",
                          "upsample_max": mod.UPSCALE_MAX_DEFAULT})()
    scale = mod.resolve_upsample(args, nx, ny)
    out_w = (nx - 1) * scale + 1
    out_h = (ny - 1) * scale + 1
    got_h = mod.region_out_height("europa", out_w, out_h)
    assert (out_w, got_h) == bounds, expected
    # Il rapporto pubblicato resta entro 0,15% da 1,6.
    assert abs(out_w / float(got_h) - 1.6) < 0.0015


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
    fit["out_ny"] = 701
    row_of, col_of, H, W = mod._grid_to_pixel(fit, scale)
    assert (H, W) == (701, 1121)
    # Le colonne NON cambiano: l'aspetto tocca solo le righe.
    assert np.all(col_of == np.arange(grid["nx"], dtype=np.float64) * scale)
    # Le righe coprono TUTTA l'altezza e restano monotone verso il nord.
    assert row_of[0] == pytest.approx(H - 1) and row_of[-1] == pytest.approx(0.0)
    assert np.all(np.diff(row_of) < 0.0)


@pytest.mark.parametrize("out_ny", [None, 701])
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
