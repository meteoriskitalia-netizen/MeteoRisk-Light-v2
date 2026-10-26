# -*- coding: utf-8 -*-
"""Test del ricampionamento NEAREST-NEIGHBOUR dei campi CATEGORICI.

Difetto misurato il 2026-10-26 sul frame reale ``dwd_icon_d2`` 2026-10-26T06Z:
``weather_code`` (45 bande discrete, codici WMO) veniva interpolato come una
grandezza (due tap sulle righe a ``x1``, bilineare a ``x3``): il 9,98 % dei pixel
in ``europa`` e il 19,80 % in ``italia`` aveva un valore NON intero, cioe' un
codice che nessun modello ha emesso, e 63.395 pixel finivano nel grigio
"non definito" (148,148,148) fra due zone di colore.

Questi test usano il modulo VERO (caricato per percorso: ``scripts/forecast_v2``
non e' un package) e un ORACOLO INDIPENDENTE per il nearest: il campione scelto
e' quello con la LATITUDINE (o LONGITUDINE) piu' vicina al centro del pixel di
output, cercato con ``argmin`` sulle coordinate della griglia sorgente. Non
riusa la formula dell'implementazione (``floor(pos + 0.5)``): se le due
coincidono, la mappa Mercator dei pixel e' quella giusta.

Nessun mock del sistema sotto test; nessuna rete; nessun ``.om``.
"""
import importlib.util
import os

import pytest

np = pytest.importorskip("numpy")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_fm_webp_categorical_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()

# Griglia sintetica con latitudini 35..42.5: il remap Mercator sposta le righe
# in modo non banale (altrimenti "nearest" e "due tap" coinciderebbero).
GRID = {"nx": 8, "ny": 16, "lonMin": 6.5, "latMin": 35.0, "dx": 0.5, "dy": 0.5}


def _code_frame():
    """Un frame di CODICI: valori interi e distinti per cella.

    ``value = 10 * row + col`` rende ogni cella riconoscibile, cosi' un valore
    interpolato (non intero o non presente nella sorgente) e' visibile subito.
    """
    ny, nx = GRID["ny"], GRID["nx"]
    return np.array([[10 * r + c for c in range(nx)] for r in range(ny)],
                    dtype=np.float32)


def _oracle_nearest(grid, arr, scale):
    """ORACOLO indipendente: i campioni con lat/lon MINIMAMENTE distanti.

    Le coordinate di output sono quelle che il renderer usa (Mercator sulle
    righe, lineare in longitudine sulle colonne); il campione scelto e' quello
    la cui distanza geometrica e' MINIMA, non ``floor(pos + 0.5)``. A meta'
    esatto la scelta nearest non e' unica (due campioni equidistanti): si
    accetta QUALUNQUE campione a distanza minima, cosi' il test non dipende
    dalla convenzione di tie-break.
    """
    ny = int(grid["ny"])
    nx = int(grid["nx"])
    dy = float(grid["dy"])
    dx = float(grid["dx"])
    lat_min = float(grid["latMin"])
    lon_min = float(grid["lonMin"])
    lat_north = lat_min + (ny - 1) * dy
    my_south = mod._merc_y(lat_min)
    my_north = mod._merc_y(lat_north)
    span = my_north - my_south

    out_ny = ny if scale <= 1 else (ny - 1) * scale + 1
    out_nx = nx if scale <= 1 else (nx - 1) * scale + 1
    lat_src = lat_min + np.arange(ny) * dy
    lon_src = lon_min + np.arange(nx) * dx
    allowed = []
    for r in range(out_ny):
        t = r / (out_ny - 1)
        lat = mod._inv_merc_y(my_north - t * span)
        dlat = np.abs(lat_src - lat)
        rows = np.flatnonzero(dlat <= dlat.min() + 1e-9)
        row_ok = []
        for c in range(out_nx):
            lon = lon_min + (c / max(1, scale)) * dx
            dlon = np.abs(lon_src - lon)
            cols = np.flatnonzero(dlon <= dlon.min() + 1e-9)
            row_ok.append({float(arr[ri, ci]) for ri in rows for ci in cols})
        allowed.append(row_ok)
    return allowed


def _assert_matches_oracle(out, allowed):
    ny, nx = out.shape
    assert len(allowed) == ny and len(allowed[0]) == nx
    bad = [(r, c, float(out[r, c]), sorted(allowed[r][c]))
           for r in range(ny) for c in range(nx)
           if float(out[r, c]) not in allowed[r][c]]
    assert not bad, ("pixel non-nearest: %r" % bad[:5])


# --------------------------------------------------------------------------- #
# politica: quale variabile e' categorica
# --------------------------------------------------------------------------- #
def test_weather_code_is_the_declared_categorical_field():
    assert "weather_code" in mod.CATEGORICAL_VARS
    assert mod.is_categorical("weather_code") is True
    assert mod.is_categorical("temperature_2m") is False
    assert mod.is_categorical("pressure_msl") is False
    assert mod.is_categorical("snowfall_water_equivalent") is False


def test_no_isolines_are_drawn_on_a_code():
    """Le isolinee su un codice non hanno senso: weather_code non ne ha.

    Il renderer disegna l'overlay SOLO per le variabili in ``CONTOUR_INTERVALS``
    o in ``ARROW_VARS``; se un giorno weather_code entrasse in una delle due
    tabelle, questo test cade (e la regressione sarebbe visibile a schermo).
    """
    assert "weather_code" not in mod.CONTOUR_INTERVALS
    assert "weather_code" not in mod.CONTOUR_VARS
    assert "weather_code" not in mod.ARROW_VARS


# --------------------------------------------------------------------------- #
# nearest sulle RIGHE (scale == 1, il percorso storico a due tap)
# --------------------------------------------------------------------------- #
def test_scale1_categorical_is_nearest_and_has_no_new_values():
    arr = _code_frame()
    out = mod.build_row_resampler(GRID, 1)(arr, categorical=True)
    assert out.shape == arr.shape == (GRID["ny"], GRID["nx"])
    # Nessun valore nuovo: ogni pixel e' un codice della sorgente.
    assert set(np.unique(out).tolist()) <= set(np.unique(arr).tolist())
    _assert_matches_oracle(out, _oracle_nearest(GRID, arr, 1))


def test_scale1_continuous_path_still_interpolates():
    """Controprova: SENZA il flag il difetto c'e' ancora (valori non interi).

    Non e' un test del difetto fine a se stesso: prova che il ramo categorical
    e' l'unica differenza e che il default non e' cambiato.
    """
    arr = _code_frame()
    out = mod.build_row_resampler(GRID, 1)(arr)
    fractional = np.abs(out - np.rint(out)) > 1e-6
    assert fractional.any(), "atteso almeno un pixel interpolato sul ramo continuo"
    # ... e i valori inventati dall'interpolazione NON esistono nella sorgente.
    assert set(np.unique(out).tolist()) - set(np.unique(arr).tolist())


# --------------------------------------------------------------------------- #
# nearest sull'UPSAMPLE (scale > 1: il ramo bilineare x3 / x8)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scale", [3, 8])
def test_upsample_categorical_is_nearest_on_both_axes(scale):
    arr = _code_frame()
    out = mod.build_row_resampler(GRID, scale)(arr, categorical=True)
    assert out.shape == ((GRID["ny"] - 1) * scale + 1,
                         (GRID["nx"] - 1) * scale + 1)
    assert set(np.unique(out).tolist()) <= set(np.unique(arr).tolist())
    _assert_matches_oracle(out, _oracle_nearest(GRID, arr, scale))


def test_upsample_continuous_path_still_interpolates():
    arr = _code_frame()
    out = mod.build_row_resampler(GRID, 3)(arr)
    fractional = np.abs(out - np.rint(out)) > 1e-6
    assert fractional.mean() > 0.5


# --------------------------------------------------------------------------- #
# default invariato (nessuna regressione sul percorso continuo)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scale", [1, 3])
def test_default_equals_explicit_false(scale):
    arr = _code_frame() + 0.25
    resample = mod.build_row_resampler(GRID, scale)
    assert np.array_equal(resample(arr), resample(arr, categorical=False),
                          equal_nan=True)


def test_single_row_grid_accepts_the_flag():
    grid = dict(GRID, ny=1)
    resample = mod.build_row_resampler(grid, 1)
    arr = np.arange(GRID["nx"], dtype=np.float32).reshape(1, -1)
    assert np.array_equal(resample(arr, categorical=True), arr)


# --------------------------------------------------------------------------- #
# politica NaN del nearest: decide il campione PIU' VICINO, non i due tap
# --------------------------------------------------------------------------- #
def test_nearest_nan_policy_uses_the_nearest_sample():
    arr = _code_frame()
    # Si cerca una riga di output il cui campione piu' vicino sia la riga 5 e
    # il cui "secondo tap" sia la riga 6 (o viceversa): li' i due percorsi
    # devono divergere in modo osservabile.
    arr[5, :] = np.nan
    out_nn = mod.build_row_resampler(GRID, 1)(arr, categorical=True)
    out_cont = mod.build_row_resampler(GRID, 1)(arr)
    oracle = _oracle_nearest(GRID, arr, 1)
    nan_expected = np.array([[all(np.isnan(v) for v in s) for s in row] for row in oracle])
    assert np.array_equal(np.isnan(out_nn), nan_expected)
    # Il ramo continuo "salva" i pixel in cui un solo tap e' finito: il nearest
    # no, perche' il campione scelto e' quello e basta.
    assert np.isnan(out_nn).sum() >= np.isnan(out_cont).sum()
    assert np.isnan(out_nn).any()


def test_disc_resampler_accepts_the_flag_and_copies_codes():
    grid = {"nx": 40, "ny": 30, "lonMin": -20.0, "latMin": 20.0,
            "dx": 1.0, "dy": 1.0}
    arr = np.array([[10 * r + c for c in range(grid["nx"])]
                    for r in range(grid["ny"])], dtype=np.float32)
    disc = mod.build_polar_resampler(grid, 64)
    field_nn, mask_nn = disc(arr, categorical=True)
    field, mask = disc(arr)
    assert field_nn.shape == field.shape == (64, 64)
    assert np.array_equal(mask_nn, mask)
    finite = np.isfinite(field_nn)
    assert finite.any()
    # Nessun codice inventato: i valori del disco sono codici della sorgente.
    assert set(np.unique(field_nn[finite]).tolist()) <= set(np.unique(arr).tolist())
    # ... mentre il percorso bilineare ne inventa (controprova del difetto).
    frac = np.abs(field[np.isfinite(field)] - np.rint(field[np.isfinite(field)]))
    assert (frac > 1e-6).any()


# --------------------------------------------------------------------------- #
# WIRING: _render_var_task deve PASSARE il flag per la variabile giusta
# --------------------------------------------------------------------------- #
def _fake_state(tmp_path, calls, rname="italia"):
    """Stato worker minimo con un resampler REGISTRATORE.

    ``_render_var_task`` e' il punto in cui la variabile diventa un array: qui si
    sostituisce solo il confine di I/O (``OmFileReader``/``read_frame_array``),
    che il modulo stesso dichiara non picklabile, e si registra con quale
    ``categorical`` viene chiamato il resampler VERO della crop.
    """
    def recorder(arr, categorical=False):
        calls.append(bool(categorical))
        return np.asarray(arr, dtype=np.float32)

    crops = {
        rname: {
            "row0": 0, "row1": 2, "col0": 0, "col1": 2,
            "grid": {"nx": 2, "ny": 2, "lonMin": 6.5, "latMin": 36.5,
                     "dx": 0.5, "dy": 0.5},
            "scale": 1, "font_px": 0, "resample": recorder,
        }
    }
    mod._worker_state_init(mod.load_color_scales(), str(tmp_path), "domA",
                           "20261026T000000Z", crops, 90, True, 0)


def test_render_var_task_passes_categorical_for_a_code(tmp_path, monkeypatch):
    calls = []
    _fake_state(tmp_path, calls)
    frame = np.array([[3.0, 45.0], [3.0, 45.0]], dtype=np.float32)

    class _Reader:
        def close(self):
            pass

    monkeypatch.setattr(mod, "OmFileReader", lambda path: _Reader())
    monkeypatch.setattr(mod, "read_frame_array", lambda reader, var: frame)

    res = mod._render_var_task(("frame.om", "weather_code", "2026-10-26T06:00Z",
                                "2026-10-26T0600", None))
    assert "error" not in res
    assert calls == [True]
    webp = os.path.join(str(tmp_path), "italia", "domA", "runs",
                        "20261026T000000Z", "weather_code",
                        "2026-10-26T0600.webp")
    assert os.path.isfile(webp)


def test_render_var_task_keeps_the_continuous_path_for_other_fields(tmp_path,
                                                                    monkeypatch):
    calls = []
    _fake_state(tmp_path, calls)
    frame = np.zeros((2, 2), dtype=np.float32)

    class _Reader:
        def close(self):
            pass

    monkeypatch.setattr(mod, "OmFileReader", lambda path: _Reader())
    monkeypatch.setattr(mod, "read_frame_array", lambda reader, var: frame)

    res = mod._render_var_task(("frame.om", "cloud_cover", "2026-10-26T06:00Z",
                                "2026-10-26T0600", None))
    assert "error" not in res
    assert calls == [False]
