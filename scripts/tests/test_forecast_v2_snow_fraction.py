# -*- coding: utf-8 -*-
"""Fase neve/pioggia del motore V2 (2026-10-25): `snowfall_water_equivalent` + `snow_fraction`.

Il motore pubblica DUE voci nuove, con statuti DIVERSI (dichiarati nel codice e
qui verificati):

  - ``snowfall_water_equivalent``  DATO DEL MODELLO (diretto, mm di acqua
    equivalente caduta come neve). Non esiste in nessun ``.om`` un campo
    chiamato ``snowfall``: il nome del child sorgente e' esattamente questo.
  - ``snow_fraction``              DERIVATA NOSTRA (``swe / precipitation``),
    numero CONTINUO in [0,1]: NON e' una classificazione del modello e nel
    motore NON c'e' alcuna soglia neve/pioggia (una eventuale soglia tipo
    "neve se frazione >= 0.5" e' una decisione di resa, fuori dal motore).

Allowlist ESPLICITA per dominio (``VAR_SWE_DOMAINS``), come per il livello
10 hPa e non "se il file ce l'ha". MISURATO sui child dei frame reali S3:
presente in ``dwd_icon``, ``dwd_icon_eu``, ``dwd_icon_d2``, ``ecmwf_ifs025``,
``ncep_gfs013``, ``meteofrance_arpege_world025``; ASSENTE in ``ncep_gfs025``,
che non ha alcuna variabile di superficie.

Misure che motivano i casi limite (frame reali, 2026-10-25):
  - a T+0 i campi ACCUMULATI non esistono (111 array) e dal T+1h sono 121:
    ``snowfall_water_equivalent`` compare solo dal secondo frame, come
    ``precipitation``. Il motore non deve fallire: omette la variabile e lo
    dichiara;
  - su ECMWF il campo e' una STIMA DEL FORNITORE e puo' superare la
    precipitazione (``precipitation - showers - swe`` negativo in 40 celle su
    661): la frazione e' limitata a 1.0 e NON si deriva mai ``pioggia =
    precip - swe``.

Il modulo del motore viene caricato via ``importlib`` per percorso
(``scripts/forecast_v2`` non e' un package), stesso pattern di
``test_forecast_v2_derived.py``. Se ``numpy``/``omfiles`` mancano i test sono
SKIPPED.
"""
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
        "forecast_v2_engine_snow_ut", ENGINE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENGINE = _load_engine()

# Allowlist MISURATA (child reali dei frame S3), non dedotta.
ALLOWED_SWE = ("dwd_icon", "dwd_icon_eu", "dwd_icon_d2", "ecmwf_ifs025",
               "ncep_gfs013", "meteofrance_arpege_world025")
NOT_ALLOWED_SWE = ("ncep_gfs025",)


# ===========================================================================
# 1) DICHIARAZIONI: allowlist esplicita, nomi, statuto dato/derivata
# ===========================================================================
def test_var_swe_domains_is_an_explicit_frozenset():
    """L'allowlist e' un frozenset ESPLICITO con esattamente i 6 domini misurati."""
    assert isinstance(ENGINE.VAR_SWE_DOMAINS, frozenset)
    assert ENGINE.VAR_SWE_DOMAINS == frozenset(ALLOWED_SWE), (
        sorted(ENGINE.VAR_SWE_DOMAINS))
    assert len(ENGINE.VAR_SWE_DOMAINS) == 6


def test_domain_supports_swe():
    for d in ALLOWED_SWE:
        assert ENGINE.domain_supports_swe(d) is True, d
    for d in NOT_ALLOWED_SWE:
        assert ENGINE.domain_supports_swe(d) is False, d
    # dominio sconosciuto -> non ammesso (default sicuro)
    assert ENGINE.domain_supports_swe("dominio_inesistente") is False


def test_vars_swe_names_and_status():
    """2 voci: `swe` DIRETTA (ha i sorgenti), `snow_fraction` DERIVATA (non li ha)."""
    assert ENGINE.VARS_SWE == ("snowfall_water_equivalent", "snow_fraction")
    assert ENGINE.VARS_SWE_SET == frozenset(ENGINE.VARS_SWE)

    by_name = {rv["name"]: rv for rv in ENGINE.V2_RISK_VARS}
    assert by_name["snowfall_water_equivalent"]["sources"] == [
        "snowfall_water_equivalent"]
    assert by_name["snow_fraction"]["sources"] == []
    # la derivata e' nel registro, con le DUE sorgenti dichiarate
    assert "snow_fraction" in ENGINE.DERIVED_VARS
    assert ENGINE.DERIVED_VARS["snow_fraction"]["sources"] == [
        "snowfall_water_equivalent", "precipitation"]
    assert ENGINE.DERIVED_VARS["snow_fraction"]["func"] is not None
    # `snowfall` NON esiste come nome: non deve comparire da nessuna parte
    assert "snowfall" not in ENGINE.VARS_SWE_SET
    assert "snowfall" not in {rv["name"] for rv in ENGINE.V2_RISK_VARS}


def test_variables_present_only_for_allowed_domains():
    """Le 2 voci compaiono per i 6 domini misurati e per NESSUN altro."""
    for d in ALLOWED_SWE:
        names = [rv["name"] for rv in ENGINE.risk_vars_for(d)]
        assert set(ENGINE.VARS_SWE) <= set(names), d
        read = [rv["name"] for rv in ENGINE.read_vars_for(d)]
        assert set(ENGINE.VARS_SWE) <= set(read), d
    for d in NOT_ALLOWED_SWE:
        names = [rv["name"] for rv in ENGINE.risk_vars_for(d)]
        assert not (set(ENGINE.VARS_SWE) & set(names)), d
        read = [rv["name"] for rv in ENGINE.read_vars_for(d)]
        assert not (set(ENGINE.VARS_SWE) & set(read)), d


def test_ncep_gfs025_var_list_is_byte_identical_to_before():
    """INVARIANTE: `ncep_gfs025` non riceve nulla -> `var_order` non cambia.

    Il confronto non e' "una lista qualsiasi": e' la lista STORICA (31 voci,
    ordine compreso) + le 6 voci 10 hPa, cioe' ESATTAMENTE il `var_order`
    pubblicato da `main` prima di questa modifica.
    """
    historical = (
        "temperature_2m", "dew_point_2m", "temperature_850hPa",
        "temperature_500hPa", "relative_humidity_2m", "relative_humidity_850hPa",
        "relative_humidity_700hPa", "pressure_msl",
        "geopotential_height_500hPa", "geopotential_height_850hPa",
        "geopotential_height_1000hPa", "surface_pressure",
        "wind_speed_10m", "wind_direction_10m", "wind_speed_850hPa",
        "wind_direction_850hPa", "wind_speed_300hPa", "wind_direction_300hPa",
        "wind_gusts_10m", "cloud_cover", "cloud_cover_low", "cloud_cover_mid",
        "cloud_cover_high", "cape", "snow_depth", "precipitation",
        "precipitation_cum", "rain", "showers", "weather_code",
        "freezing_level_height",
    )
    expected = list(historical) + list(ENGINE.VARS_10HPA)
    got = [rv["name"] for rv in ENGINE.risk_vars_for("ncep_gfs025")]
    assert got == expected, got
    assert len(got) == 37


def test_variable_steps_for_snow():
    steps = ENGINE.VARIABLE_STEP
    assert steps["snowfall_water_equivalent"] == pytest.approx(0.1)   # 0.1 mm
    assert steps["snow_fraction"] == pytest.approx(0.01)              # 1 %
    assert ENGINE.SNOW_FRACTION_STEP == pytest.approx(0.01)


# ===========================================================================
# 2) DERIVATA `snow_fraction`: casi limite
# ===========================================================================
def test_snow_fraction_limit_cases_scalar():
    f = ENGINE.snow_fraction
    # precipitation == 0 -> null (NaN), NON 0: la fase non e' definibile
    v = f(np.array([[0.0]], dtype=np.float32), np.array([[0.0]], dtype=np.float32))
    assert np.isnan(v[0, 0])
    # precipitation > 0 e swe == 0 -> 0.0 (pioggia)
    v = f(np.array([[0.0]], dtype=np.float32), np.array([[2.5]], dtype=np.float32))
    assert float(v[0, 0]) == 0.0
    # swe == precipitation -> 1.0 (neve)
    v = f(np.array([[2.5]], dtype=np.float32), np.array([[2.5]], dtype=np.float32))
    assert float(v[0, 0]) == 1.0
    # swe < precipitation -> rapporto
    v = f(np.array([[0.5]], dtype=np.float32), np.array([[2.0]], dtype=np.float32))
    assert float(v[0, 0]) == pytest.approx(0.25, abs=1e-6)
    # swe > precipitation (ECMWF, stima del fornitore) -> LIMITATA a 1.0
    v = f(np.array([[3.5]], dtype=np.float32), np.array([[2.0]], dtype=np.float32))
    assert float(v[0, 0]) == 1.0
    # swe NaN con precipitazione > 0 -> NaN (dato assente, non "0 neve")
    v = f(np.array([[np.nan]], dtype=np.float32),
          np.array([[2.0]], dtype=np.float32))
    assert np.isnan(v[0, 0])
    # precipitazione NaN -> NaN
    v = f(np.array([[0.0]], dtype=np.float32),
          np.array([[np.nan]], dtype=np.float32))
    assert np.isnan(v[0, 0])
    # precipitazione NEGATIVA -> NaN (nessuna precipitazione)
    v = f(np.array([[0.0]], dtype=np.float32),
          np.array([[-0.1]], dtype=np.float32))
    assert np.isnan(v[0, 0])
    # nessun valore fuori [0,1] e nessun inf
    assert not np.any(np.isinf(v))


def test_snow_fraction_vector_matches_rules():
    """Caso vettoriale misto: conteggi esatti di null / 0 / 1 / intermedi."""
    precip = np.array([[0.0, 0.0, 1.0, 2.0, 1.0, 0.5, 4.0, np.nan]],
                      dtype=np.float32)
    swe = np.array([[0.0, 1.0, 0.0, 2.0, 0.5, 0.1, 6.0, 0.0]],
                   dtype=np.float32)
    out = ENGINE.snow_fraction(swe, precip)
    assert out.dtype == np.float32
    assert out.shape == precip.shape
    # atteso: null, null, 0, 1, 0.5, 0.2, 1 (clamp), null
    assert np.isnan(out[0, 0]) and np.isnan(out[0, 1])
    assert float(out[0, 2]) == 0.0
    assert float(out[0, 3]) == 1.0
    assert float(out[0, 4]) == pytest.approx(0.5, abs=1e-6)
    assert float(out[0, 5]) == pytest.approx(0.2, abs=1e-6)
    assert float(out[0, 6]) == 1.0          # swe > precip: clamp, non 1.5
    assert np.isnan(out[0, 7])
    finite = out[np.isfinite(out)]
    assert np.all(finite >= 0.0) and np.all(finite <= 1.0), finite
    # NESSUN NaN dove la precipitazione e' > 0 e swe e' finito
    ok = (precip > 0) & np.isfinite(swe)
    assert np.all(np.isfinite(out[ok])), out[ok]
    # somma dei null == celle con precip <= 0 o NaN (3: indici 0, 1, 7)
    assert int(np.isnan(out).sum()) == 3


def test_snow_fraction_equals_one_when_inputs_identical():
    """`swe == precipitation` deve dare ESATTAMENTE 1.0 (caso "tutta neve")."""
    pr = np.array([[0.1, 0.3, 1.7, 12.9]], dtype=np.float32)
    out = ENGINE.snow_fraction(pr, pr)
    assert np.all(out == np.float32(1.0)), out


# ===========================================================================
# 3) Registro delle derivate: calcolo, seconda barriera, warning
# ===========================================================================
def test_derive_per_frame_computes_snow_fraction_for_allowed_domain():
    arrays = {
        "snowfall_water_equivalent": np.array([[0.5, 0.0, 2.0]],
                                              dtype=np.float32),
        "precipitation": np.array([[2.0, 1.0, 2.0]], dtype=np.float32),
    }
    meta = {}
    warns = ENGINE.derive_per_frame(arrays, meta, "dwd_icon_d2")
    got = arrays["snow_fraction"]
    assert float(got[0, 0]) == pytest.approx(0.25, abs=1e-6)
    assert float(got[0, 1]) == 0.0
    assert float(got[0, 2]) == 1.0
    assert meta["snow_fraction"] == {
        "source_domain": "dwd_icon_d2",
        "derived": True,
        "derived_from": ["snowfall_water_equivalent", "precipitation"],
    }
    assert not any("snow_fraction" in w for w in warns), warns


def test_derive_per_frame_skips_snow_fraction_for_ncep_gfs025():
    """Seconda barriera: sorgenti presenti ma dominio fuori allowlist -> niente.

    `ncep_gfs025` non ha variabili di superficie: se per un percorso diverso le
    due sorgenti arrivassero negli `arrays`, la frazione NON deve essere
    pubblicata e NON deve produrre warning (non e' richiesta per quel dominio).
    """
    arrays = {
        "snowfall_water_equivalent": np.array([[0.5]], dtype=np.float32),
        "precipitation": np.array([[2.0]], dtype=np.float32),
    }
    meta = {}
    warns = ENGINE.derive_per_frame(arrays, meta, "ncep_gfs025")
    assert "snow_fraction" not in arrays
    assert "snow_fraction" not in meta
    assert not any("snow_fraction" in w for w in warns), warns


def test_derive_per_frame_declares_missing_sources_at_t0():
    """Frame T+0 (accumulati assenti): nessuna variabile, warning esplicito."""
    arrays = {}   # ne' precipitation ne' snowfall_water_equivalent
    meta = {}
    warns = ENGINE.derive_per_frame(arrays, meta, "dwd_icon_eu")
    assert "snow_fraction" not in arrays
    mine = [w for w in warns if "snow_fraction" in w]
    assert mine, warns
    assert "snowfall_water_equivalent" in mine[0]
    assert "precipitation" in mine[0]


def test_missing_published_warnings_reports_absent_swe():
    """`swe` e' DIRETTA: se il frame non ce l'ha, lo si dichiara (non silenzio)."""
    rv = ENGINE.risk_vars_for("dwd_icon_eu")
    warns = ENGINE.missing_published_warnings(rv, {}, "dwd_icon_eu")
    assert any("snowfall_water_equivalent" in w for w in warns), warns
    # e NON segnala la derivata (gestita da derive_per_frame)
    assert not any("snow_fraction" in w for w in warns), warns


# ===========================================================================
# 4) Round-trip REALE (omfiles): quantizzazione e null conservati
# ===========================================================================
def _read_children(path):
    reader = omfiles.OmFileReader.from_path(path)
    try:
        names = ENGINE.array_child_names(reader)
        out = {}
        for n in sorted(names):
            child = reader.get_child_by_name(n)
            ny, nx = int(child.shape[0]), int(child.shape[1])
            out[n] = np.asarray(
                child.read_array((slice(0, ny), slice(0, nx))),
                dtype=np.float64)
    finally:
        reader.close()
    return names, out


def test_roundtrip_real_writer_swe_and_fraction(tmp_path):
    """`.om` scritto e RILETTO: 0.1 mm su swe, 0.01 su frazione, NaN conservato."""
    swe = np.array([[0.0, 0.14, 1.05, np.nan]], dtype=np.float32)
    precip = np.array([[0.0, 2.0, 1.05, 3.0]], dtype=np.float32)
    frac = ENGINE.snow_fraction(swe, precip)
    arrays = {
        "snowfall_water_equivalent": swe,
        "precipitation": precip,
        "snow_fraction": frac,
    }
    ordered = [rv["name"] for rv in ENGINE.risk_vars_for("dwd_icon_eu")]
    path = str(tmp_path / "snow.om")
    ENGINE.write_frame_om(path, ordered, arrays, "GEOGCRS[...]", "lat lon", 0, 0)
    names, out = _read_children(path)

    assert {"snowfall_water_equivalent", "snow_fraction"} <= set(names)
    # swe: passo 0.1 mm (0.14 -> 0.1; 1.05 -> 1.1, cioe' il multiplo piu'
    # vicino: distanza 0.05 = mezza banda, per questo la tolleranza e' 0.0501)
    assert float(out["snowfall_water_equivalent"][0, 1]) == pytest.approx(
        0.14, abs=0.0501)
    assert float(out["snowfall_water_equivalent"][0, 2]) == pytest.approx(
        1.05, abs=0.0501)
    # frazione: passo 0.01
    # precip == 0 -> null (NaN nel `.om`), NON 0.0: la fase non e' definibile
    assert np.isnan(out["snow_fraction"][0, 0])
    assert float(out["snow_fraction"][0, 1]) == pytest.approx(0.07, abs=0.01)
    assert float(out["snow_fraction"][0, 2]) == pytest.approx(1.0, abs=0.01)
    # swe NaN con precip > 0 -> NaN pubblicato come NaN (non 0, non sentinella)
    assert np.isnan(out["snow_fraction"][0, 3])
    assert np.isnan(out["snowfall_water_equivalent"][0, 3])
    # nessun valore fuori [0,1] nel file pubblicato
    finite = out["snow_fraction"][np.isfinite(out["snow_fraction"])]
    assert np.all(finite >= 0.0) and np.all(finite <= 1.0)
