# -*- coding: utf-8 -*-
"""Finestra 6 ore del motore V2 (2026-10-26): `precipitation_6h` + `snow_fraction_6h`.

DUE DERIVATE STATEFUL NOSTRE (nonzero nella sorgente, non calcolabili
frame-per-frame): la finestra e' TEMPORALE, non "gli ultimi N frame".

Perche' (MISURATO il 2026-10-26 sui frame reali S3, non assunto):
  - `precipitation` NON e' "l'ora precedente": e' l'accumulo dell'intervallo fra
    il valid_time PRECEDENTE e quello corrente, e il passo CAMBIA dentro la
    stessa run (1 h -> 3 h, 3 h -> 6 h);
  - ECMWF run 2026-10-25T12Z, frame `2026-10-31T12:00Z` (primo del tratto a 6 h):
    massimo di griglia 108,6 mm = `hourly_3` dell'API, NON `hourly_6` (165,7):
    quella cornice copre solo 3 h perche' il valid_time precedente era 09Z.
    Sommando anche il frame 09Z si ottiene 165,70 mm = `hourly_6` esatto;
  - ARPEGE alla transizione 1 h -> 3 h: media 0,0857 mm (1 h) -> 0,2579 mm (3 h).
Quindi: si somma all'indietro finche' `Σ(t_i − t_{i−1})` e' ESATTAMENTE 6 h
(±1 s) -> 1 h = 6 frame, 3 h = 2 frame, 6 h = 1 frame.

Il modulo del motore viene caricato via ``importlib`` per percorso
(``scripts/forecast_v2`` non e' un package), stesso pattern di
``test_forecast_v2_derived.py``. Se ``numpy``/``omfiles`` mancano: SKIPPED.
"""
import datetime as dt
import importlib.util
import os

import pytest

np = pytest.importorskip("numpy")
omfiles = pytest.importorskip("omfiles")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ENGINE_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                           "forecast_v2_engine.py")


def _load_engine():
    spec = importlib.util.spec_from_file_location(
        "forecast_v2_engine_window6h_ut", ENGINE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENGINE = _load_engine()

T0 = dt.datetime(2026, 10, 25, 18, tzinfo=dt.timezone.utc)
REF_UNIX = int(T0.timestamp())

ALLOWED = ("dwd_icon", "dwd_icon_eu", "dwd_icon_d2", "ecmwf_ifs025",
           "ncep_gfs013", "meteofrance_arpege_world025")
NOT_ALLOWED = ("ncep_gfs025",)


def _series(steps_h, count):
    """valid_times da passi (ore) applicati in sequenza: 1,1,...,3,3,...
    `steps_h` = lista dei passi FRA valid_time consecutivi."""
    out = [T0]
    for k in range(count):
        out.append(out[-1] + dt.timedelta(hours=steps_h[min(k, len(steps_h) - 1)]))
    return out


def _entry(index, valid_time, precip=None, swe=None, ref=REF_UNIX, step_s=None,
           shape=(1, 1)):
    if precip is not None:
        precip = np.asarray(precip, dtype=np.float32).reshape(shape)
    if swe is not None:
        swe = np.asarray(swe, dtype=np.float32).reshape(shape)
    return {"index": index, "valid_time": valid_time.strftime("%Y-%m-%dT%H:%MZ"),
            "vt": valid_time, "step_s": step_s, "ref_unix": ref,
            "precipitation": precip, "snowfall_water_equivalent": swe}


def _entries_from_pairs(pairs, steps_s, ref=REF_UNIX):
    """pairs = [(precip, swe), ...] con steps_s[i] = passo del frame i."""
    out = []
    for i, (p, s) in enumerate(pairs):
        out.append(_entry(i, T0 + dt.timedelta(seconds=sum(steps_s[:i + 1])),
                          precip=p, swe=s, ref=ref,
                          step_s=float(steps_s[i]) if steps_s[i] else None))
    return out


# ===========================================================================
# 1) `window6h_plan`: la finestra e' TEMPORALE (1 h -> 6 frame, 3 h -> 2, 6 h -> 1)
# ===========================================================================
def test_plan_one_hour_step_six_frames():
    vts = _series([1], 10)
    idx, reason = ENGINE.window6h_plan(vts, 6)
    assert reason is None
    assert idx == [1, 2, 3, 4, 5, 6], idx
    assert len(idx) == 6
    # 6 passi da 1 h = esattamente 6 h, e il primo frame della run e' ESCLUSO
    # (copre l'ora PRIMA del reference_time, che non fa parte della finestra).
    assert (vts[idx[-1]] - vts[idx[0] - 1]).total_seconds() == 6 * 3600


def test_plan_first_frames_are_not_tileable():
    """I primi frame di una run non hanno 6 h di storia: null, non zeri."""
    vts = _series([1], 10)
    for i in range(6):
        assert ENGINE.window6h_plan(vts, i) == (None, "window_not_tileable"), i
    assert ENGINE.window6h_plan(vts, 0) == (None, "window_not_tileable")
    assert ENGINE.window6h_plan([], 0) == (None, "window_not_tileable")
    assert ENGINE.window6h_plan(vts, len(vts)) == (None, "window_not_tileable")


def test_plan_three_hour_step_uses_two_frames_not_six():
    """A passo 3 h la finestra sono 2 frame: 6 frame coprirebbero 18 h."""
    vts = _series([3], 6)
    idx, reason = ENGINE.window6h_plan(vts, 2)
    assert reason is None and idx == [1, 2], idx
    assert (vts[idx[-1]] - vts[idx[0] - 1]).total_seconds() == 6 * 3600
    assert ENGINE.window6h_plan(vts, 1) == (None, "window_not_tileable")


def test_plan_six_hour_step_uses_one_frame():
    vts = _series([6], 5)
    idx, reason = ENGINE.window6h_plan(vts, 1)
    assert reason is None and idx == [1], idx
    idx, reason = ENGINE.window6h_plan(vts, 4)
    assert reason is None and idx == [4], idx


def test_plan_transition_1h_to_3h_is_mixed():
    """1 h -> 3 h: la finestra al primo frame a 3 h e' 1 h + 1 h + 1 h + 3 h."""
    vts = _series([1, 1, 1, 3], 5)      # 0,1,2,3,6,9
    idx, reason = ENGINE.window6h_plan(vts, 4)   # frame delle 03Z (3 h)
    assert reason is None and idx == [1, 2, 3, 4], idx
    assert (vts[idx[-1]] - vts[idx[0] - 1]).total_seconds() == 6 * 3600
    idx, reason = ENGINE.window6h_plan(vts, 5)   # 06Z (3 h): due frame da 3 h
    assert reason is None and idx == [4, 5], idx


def test_plan_transition_3h_to_6h_ecmwf_case():
    """ECMWF 2026-10-31: 09Z(3 h) + 12Z(3 h) = 6 h; poi 18Z da solo.

    E' il caso MISURATO: la cornice 12Z (nominale 6 h) copre solo 3 h e va
    sommata al 09Z, altrimenti la pioggia e' sottostimata del 34%
    (108,6 mm invece di 165,7 mm sul massimo di griglia).
    """
    vts = _series([3, 3, 3, 3, 6], 5)    # 0,3,6,9,12,18
    idx, reason = ENGINE.window6h_plan(vts, 4)
    assert reason is None and idx == [3, 4], idx
    assert (vts[idx[-1]] - vts[idx[0] - 1]).total_seconds() == 6 * 3600
    idx, reason = ENGINE.window6h_plan(vts, 5)
    assert reason is None and idx == [5], idx


def test_plan_four_hour_step_is_not_tileable():
    """Passo 4 h: 4 + 4 = 8 > 6, nessun sottoinsieme da 6 h -> null dichiarato."""
    vts = _series([4], 5)
    assert ENGINE.window6h_plan(vts, 1) == (None, "window_not_tileable")
    assert ENGINE.window6h_plan(vts, 3) == (None, "window_not_tileable")


def test_plan_every_plan_sums_exactly_six_hours():
    """Proprieta': per OGNI piano il totale dei passi e' 6 h (±1 s)."""
    cases = [([1], 10), ([3], 6), ([6], 5), ([1, 1, 1, 3], 6),
             ([3, 3, 3, 3, 6], 6), ([1, 1, 2, 2, 3], 8)]
    checked = 0
    for steps, n in cases:
        vts = _series(steps, n)
        for i in range(len(vts)):
            idx, reason = ENGINE.window6h_plan(vts, i)
            if idx is None:
                assert reason == "window_not_tileable"
                continue
            total = (vts[idx[-1]] - vts[idx[0] - 1]).total_seconds()
            assert abs(total - 6 * 3600) <= 1, (steps, i, idx, total)
            checked += 1
    assert checked > 20


# ===========================================================================
# 2) `window6h_entries`: un frame mancante nel buffer rende la finestra nulla
# ===========================================================================
def test_entries_missing_frame_in_buffer_is_declared():
    vts = _series([1], 10)
    buf = [_entry(i, vts[i], precip=1.0) for i in range(7) if i != 3]
    entries, reason = ENGINE.window6h_entries(vts, 6, buf)
    assert entries is None and reason == "frame_missing_in_window"


def test_entries_returns_oldest_to_newest():
    vts = _series([1], 10)
    buf = [_entry(i, vts[i], precip=float(i)) for i in range(7)]
    entries, reason = ENGINE.window6h_entries(vts, 6, buf)
    assert reason is None
    assert [e["index"] for e in entries] == [1, 2, 3, 4, 5, 6]


# ===========================================================================
# 3) Calcolo: somma esatta, frazione PESATA, clamp, null con motivo
# ===========================================================================
def test_precipitation_6h_sums_the_window():
    pairs = [(1.0, 0.5), (2.0, 1.0), (3.0, 1.5), (4.0, 2.0), (5.0, 2.5),
             (6.0, 3.0)]
    entries = _entries_from_pairs(pairs, [3600.0] * 6)
    w = ENGINE.compute_window6h(entries, REF_UNIX)
    assert w["window"]["hours"] == pytest.approx(6.0)
    assert w["window"]["n_frames"] == 6
    assert float(w["precipitation_6h"]["array"][0, 0]) == pytest.approx(21.0)
    assert float(w["snow_fraction_6h"]["array"][0, 0]) == pytest.approx(0.5)
    assert w["precipitation_6h"]["null_reason"] is None
    assert w["snow_fraction_6h"]["null_reason"] is None


def test_snow_fraction_6h_is_weighted_not_mean_of_fractions():
    """Caso REALE ARPEGE 2026-10-26T12..17Z, 42,75N 31,75W.

    precip 0,1/0,2/0,2/0,5/1,6/2,7 - swe 0/0/0/0,2/1,6/2,7
    -> pesata 4,5/5,3 = 0,849 (neve) ; media delle frazioni = 0,400 (pioggia).
    La differenza non e' cosmetica: cambia il tratteggio "neve" sulla mappa.
    """
    p = [0.1, 0.2, 0.2, 0.5, 1.6, 2.7]
    s = [0.0, 0.0, 0.0, 0.2, 1.6, 2.7]
    entries = _entries_from_pairs(list(zip(p, s)), [3600.0] * 6)
    frac = ENGINE.snow_fraction_6h(entries)
    assert float(frac[0, 0]) == pytest.approx(4.5 / 5.3, abs=1e-3)
    fractions = [si / pi for pi, si in zip(p, s) if pi > 0]
    assert sum(fractions) / len(fractions) == pytest.approx(0.400, abs=1e-3)
    assert float(frac[0, 0]) > 0.5 > sum(fractions) / len(fractions)


def test_snow_fraction_6h_clamped_when_swe_exceeds_precipitation():
    """Su alcuni frame swe > precip (stima del fornitore): limite a 1.0."""
    pairs = [(0.0, 0.1), (0.0, 0.1), (0.0, 0.1), (0.1, 0.1), (0.1, 0.1),
             (0.1, 0.1)]
    entries = _entries_from_pairs(pairs, [3600.0] * 6)
    frac = ENGINE.snow_fraction_6h(entries)
    # 0,5 / 0,3 = 1,67 senza il limite
    assert float(frac[0, 0]) == pytest.approx(1.0)


def test_snow_fraction_6h_zero_precipitation_is_null():
    """Σprecip == 0 -> NaN (non 0.0): 0/0 non e' "0% neve"."""
    pairs = [(0.0, 0.0)] * 6
    entries = _entries_from_pairs(pairs, [3600.0] * 6)
    w = ENGINE.compute_window6h(entries, REF_UNIX)
    frac = w["snow_fraction_6h"]["array"]
    assert np.all(np.isnan(frac))
    assert w["snow_fraction_6h"]["null_reason"] == "zero_precipitation_in_window"
    # la precipitazione invece e' un numero: 0 mm in 6 ore, non null
    assert float(w["precipitation_6h"]["array"][0, 0]) == 0.0
    assert w["precipitation_6h"]["null_reason"] is None


def test_snow_fraction_6h_missing_swe_is_null_only_where_it_rains():
    pairs = [(1.0, 0.0), (0.0, None), (0.0, None), (0.0, None), (0.0, None),
             (0.0, None)]
    entries = _entries_from_pairs(pairs, [3600.0] * 6)
    frac = ENGINE.snow_fraction_6h(entries)
    # qui piove solo nel PRIMO frame (con swe noto) -> frazione calcolabile = 0
    assert float(frac[0, 0]) == 0.0
    pairs2 = [(0.0, 0.0), (1.0, None), (0.0, None), (0.0, None), (0.0, None),
              (0.0, None)]
    entries2 = _entries_from_pairs(pairs2, [3600.0] * 6)
    frac2 = ENGINE.snow_fraction_6h(entries2)
    assert np.isnan(frac2[0, 0]), "swe ignoto dove piove -> null, non 0"


def test_compute_window6h_all_cells_null_for_missing_swe():
    pairs = [(1.0, None)] * 6
    entries = _entries_from_pairs(pairs, [3600.0] * 6)
    w = ENGINE.compute_window6h(entries, REF_UNIX)
    assert w["snow_fraction_6h"]["null_reason"] == "swe_missing_in_window"
    assert float(w["precipitation_6h"]["array"][0, 0]) == pytest.approx(6.0)
    assert w["precipitation_6h"]["null_reason"] is None


def test_mixed_reference_time_nulls_both_variables():
    """GUARDIA OBBLIGATORIA: un frame di un'ALTRA run invalida la finestra."""
    pairs = [(1.0, 0.5)] * 6
    entries = _entries_from_pairs(pairs, [3600.0] * 6)
    entries[2]["ref_unix"] = REF_UNIX - 3 * 3600      # run precedente
    w = ENGINE.compute_window6h(entries, REF_UNIX)
    for name in ENGINE.VARS_WINDOW6H:
        assert w[name]["array"] is None
        assert w[name]["null_reason"] == "mixed_reference_time"
    assert w["window"] is None


def test_unreadable_reference_time_nulls_both_variables():
    entries = _entries_from_pairs([(1.0, 0.5)] * 6, [3600.0] * 6)
    entries[0]["ref_unix"] = None
    w = ENGINE.compute_window6h(entries, REF_UNIX)
    for name in ENGINE.VARS_WINDOW6H:
        assert w[name]["null_reason"] == "reference_time_unreadable"


def test_frame_without_precipitation_nulls_both_variables():
    """A T+0 i campi accumulati non esistono: null, non "0 mm"."""
    pairs = [(1.0, 0.5)] * 5 + [(None, None)]
    entries = _entries_from_pairs(pairs, [3600.0] * 6)
    w = ENGINE.compute_window6h(entries, REF_UNIX)
    for name in ENGINE.VARS_WINDOW6H:
        assert w[name]["array"] is None
        assert w[name]["null_reason"] == "frame_missing_in_window"


def test_window_shape_mismatch_is_declared():
    a = np.zeros((1, 1), dtype=np.float32)
    b = np.zeros((2, 2), dtype=np.float32)
    entries = [_entry(i, T0 + dt.timedelta(hours=i), precip=a, swe=a,
                      step_s=3600.0) for i in range(1, 6)]
    entries.append(_entry(6, T0 + dt.timedelta(hours=6), precip=b, swe=b,
                          step_s=3600.0, shape=(2, 2)))
    w = ENGINE.compute_window6h(entries, REF_UNIX)
    assert w["precipitation_6h"]["null_reason"] == "window_shape_mismatch"


def test_not_tileable_reason_passed_through():
    w = ENGINE.compute_window6h(None, REF_UNIX, "window_not_tileable")
    for name in ENGINE.VARS_WINDOW6H:
        assert w[name]["array"] is None
        assert w[name]["null_reason"] == "window_not_tileable"


def test_no_fill_with_zeros():
    """Non esiste alcun percorso che riempia la finestra con zeri."""
    entries = _entries_from_pairs([(2.0, 0.0)] * 6, [3600.0] * 6)
    w = ENGINE.compute_window6h(entries, REF_UNIX)
    assert float(w["precipitation_6h"]["array"][0, 0]) == pytest.approx(12.0)
    # 6 frame x 2 mm = 12 mm: nessuno zero aggiunto
    assert float(np.nansum(w["precipitation_6h"]["array"])) == pytest.approx(12.0)


# ===========================================================================
# 4) DICHIARAZIONI: allowlist, registro, passi di quantizzazione
# ===========================================================================
def test_vars_window6h_declared():
    assert ENGINE.VARS_WINDOW6H == ("precipitation_6h", "snow_fraction_6h")
    assert ENGINE.VARS_WINDOW6H_SET == frozenset(ENGINE.VARS_WINDOW6H)
    assert ENGINE.WINDOW6H_HOURS == pytest.approx(6.0)
    assert ENGINE.WINDOW6H_TOLERANCE_S == 1
    assert ENGINE.WINDOW6H_MAX_FRAMES == 6
    by_name = {rv["name"]: rv for rv in ENGINE.V2_RISK_VARS}
    for name in ENGINE.VARS_WINDOW6H:
        assert by_name[name]["sources"] == [], name


def test_window6h_registered_as_stateful():
    for name in ENGINE.VARS_WINDOW6H:
        spec = ENGINE.DERIVED_VARS[name]
        assert spec.get("stateful") is True, name
        assert spec["func"] is None, name
    assert ENGINE.DERIVED_VARS["precipitation_6h"]["sources"] == [
        "precipitation"]
    assert ENGINE.DERIVED_VARS["snow_fraction_6h"]["sources"] == [
        "snowfall_water_equivalent", "precipitation"]


def test_window6h_only_for_swe_domains():
    for d in ALLOWED:
        assert ENGINE.domain_supports_window6h(d) is True, d
        names = [rv["name"] for rv in ENGINE.risk_vars_for(d)]
        assert set(ENGINE.VARS_WINDOW6H) <= set(names), d
        assert set(ENGINE.VARS_WINDOW6H) <= set(
            rv["name"] for rv in ENGINE.read_vars_for(d)), d
    for d in NOT_ALLOWED:
        assert ENGINE.domain_supports_window6h(d) is False, d
        names = [rv["name"] for rv in ENGINE.risk_vars_for(d)]
        assert not (set(ENGINE.VARS_WINDOW6H) & set(names)), d


def test_ncep_gfs025_unchanged_by_window6h():
    """`ncep_gfs025` non ha superficie: la sua lista resta di 37 voci."""
    names = [rv["name"] for rv in ENGINE.risk_vars_for("ncep_gfs025")]
    assert len(names) == 37, len(names)
    assert not (set(ENGINE.VARS_WINDOW6H) & set(names))


def test_variable_steps_for_window6h():
    assert ENGINE.VARIABLE_STEP["precipitation_6h"] == pytest.approx(0.1)
    assert ENGINE.VARIABLE_STEP["snow_fraction_6h"] == pytest.approx(0.01)


# ===========================================================================
# 5) Round-trip reale (omfiles): passo giusto, NaN conservato
# ===========================================================================
def test_window6h_roundtrip_real_writer(tmp_path):
    path = str(tmp_path / "w6.om")
    p6 = np.array([[216.6, 0.1], [0.0, 12.3]], dtype=np.float32)
    f6 = np.array([[0.85, 1.0], [np.nan, 0.0]], dtype=np.float32)
    arrays = {"precipitation_6h": p6, "snow_fraction_6h": f6}
    nbytes, nvars = ENGINE.write_frame_om(
        path, list(ENGINE.VARS_WINDOW6H), arrays, "GEOGCRS[...]", "{}", 0, 0)
    assert nbytes > 0 and nvars == 2
    reader = omfiles.OmFileReader.from_path(path)
    try:
        cp = reader.get_child_by_name("precipitation_6h")
        cf = reader.get_child_by_name("snow_fraction_6h")
        out_p = np.asarray(cp.read_array((slice(0, 2), slice(0, 2))),
                           dtype=np.float32)
        out_f = np.asarray(cf.read_array((slice(0, 2), slice(0, 2))),
                           dtype=np.float32)
        step_p = 1.0 / float(cp.scale_factor)
        step_f = 1.0 / float(cf.scale_factor)
    finally:
        reader.close()
    assert step_p == pytest.approx(0.1, abs=1e-6)
    assert step_f == pytest.approx(0.01, abs=1e-6)
    assert float(out_p[0, 0]) == pytest.approx(216.6, abs=0.05)
    assert float(out_p[0, 1]) == pytest.approx(0.1, abs=1e-3)
    assert float(out_f[0, 0]) == pytest.approx(0.85, abs=0.01)
    assert float(out_f[0, 1]) == pytest.approx(1.0, abs=1e-6)
    assert np.isnan(out_f[1, 0]), "il NaN (null) deve sopravvivere al writer"
    assert float(out_f[1, 1]) == pytest.approx(0.0)
