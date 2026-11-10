# -*- coding: utf-8 -*-
"""Contratto dei DUE campi AGGIUNTIVI del dataset punti (2026-11-10).

Obiettivo: una sintesi meteo in stile bollettino ha bisogno del CIELO
(`cloud_cover`, %) e della NEVE CADUTA (`snowfall_water_equivalent`, mm di
acqua equivalente).

REGOLA ASSOLUTA (incidente precedente): i 49 campi orari gia' pubblicati NON
si toccano. Nessuno rimosso, nessuno rinominato, nessuno riordinato. I due
nuovi entrano SOLO in coda, come opzionali, con lo stesso meccanismo che la
pipeline usa gia' per `visibility`:
  * `hourly_record_fields` tiene il campo solo se ha almeno un valore non
    nullo e l'unita' ammessa (altrimenti lo OMETTE dal record e lo dichiara in
    `dropped` -> `hourly_optional`);
  * un HTTP 400 che cita un opzionale fa ripetere UNA volta la richiesta senza
    opzionali e il batch prosegue (il fetch non fallisce mai per questo).

Cosa si verifica qui:
  1. i 49 campi storici ci sono tutti, nello stesso ordine, nelle stesse
     posizioni 0..48; la stringa dei campi OBBLIGATORI (quella che parte
     sempre) e' byte-identica a prima;
  2. i 2 nuovi sono in coda (posizioni 49 e 50), dichiarati opzionali con le
     unita' "%" e "mm";
  3. null totali (o unita' sbagliata) -> campo omesso dal record;
  4. HTTP 400 sul nome nuovo -> retry senza i nuovi e dataset ancora valido
     (build + validate end-to-end).
"""

import io
import json
import os
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, TESTS_DIR)

import common  # noqa: E402
import build_meteorisk_dataset as builder  # noqa: E402
import fetch_source_data  # noqa: E402
import gen_fixture_raw  # noqa: E402
import validate_dataset  # noqa: E402

# ---------------------------------------------------------------------------
# BASELINE CONGELATA: i 49 campi orari del dataset pubblicato su `main` PRIMA
# di questa modifica, nell'ordine ESATTO letto da
# data/latest/meteorisk-points.json (257 punti, best_match + ecmwf_ifs):
#   n_fields = 49, tutti i punti e i due leg con la stessa sequenza di chiavi.
# Questo elenco NON deve cambiare: e' il contratto di non-regressione.
# ---------------------------------------------------------------------------
CAMPI_STORICI_49 = [
    "temperature_2m",
    "relativehumidity_2m",
    "dew_point_2m",
    "pressure_msl",
    "cape",
    "precipitation_probability",
    "windspeed_10m",
    "winddirection_10m",
    "windspeed_100m",
    "winddirection_100m",
    "windgusts_10m",
    "weathercode",
    "precipitation",
    "showers",
    "freezing_level_height",
    "wind_speed_1000hPa",
    "wind_direction_1000hPa",
    "wind_speed_975hPa",
    "wind_direction_975hPa",
    "wind_speed_950hPa",
    "wind_direction_950hPa",
    "wind_speed_925hPa",
    "wind_direction_925hPa",
    "wind_speed_900hPa",
    "wind_direction_900hPa",
    "wind_speed_850hPa",
    "wind_direction_850hPa",
    "wind_speed_800hPa",
    "wind_direction_800hPa",
    "wind_speed_700hPa",
    "wind_direction_700hPa",
    "wind_speed_600hPa",
    "wind_direction_600hPa",
    "wind_speed_500hPa",
    "wind_direction_500hPa",
    "temperature_850hPa",
    "temperature_700hPa",
    "temperature_500hPa",
    "relative_humidity_850hPa",
    "relative_humidity_700hPa",
    "dew_point_850hPa",
    "dew_point_700hPa",
    "geopotential_height_850hPa",
    "geopotential_height_700hPa",
    "geopotential_height_500hPa",
    "convective_inhibition",
    "lifted_index",
    "k_index",
    "visibility",
]

NUOVI = ["cloud_cover", "snowfall_water_equivalent"]
NUOVI_SET = frozenset(NUOVI)
N_STORICI = len(CAMPI_STORICI_49)          # 49
N_OPZIONALI_STORICI = 1                    # visibility


# ---------------------------------------------------------------------------
# 1. I 49 campi storici: presenti, stessi nomi, stesso ordine, stesse posizioni
# ---------------------------------------------------------------------------
def test_i_49_campi_storici_sono_presenti_e_nell_ordine_di_prima():
    assert len(CAMPI_STORICI_49) == 49, "baseline corrotta: devono essere 49"
    assert common.HOURLY_FIELDS[:N_STORICI] == CAMPI_STORICI_49


def test_nessun_campo_storico_e_stato_rimosso_o_rinominato():
    # Differenza di INSIEMI: i nuovi in piu' sono esattamente i 2 attesi.
    assert set(common.HOURLY_FIELDS) - set(CAMPI_STORICI_49) == NUOVI_SET
    assert set(CAMPI_STORICI_49) - set(common.HOURLY_FIELDS) == set()


def test_la_stringa_dei_campi_obbligatori_non_cambia():
    """La richiesta che parte SEMPRE (`HOURLY_PARAMS_REQUIRED`) resta identica:
    i 48 obbligatori storici, nello stesso ordine, senza `visibility` (che era
    ed e' opzionale) e senza i 2 nuovi."""
    obbligatori_storici = [f for f in CAMPI_STORICI_49
                           if f not in common.HOURLY_OPTIONAL_SET]
    assert len(obbligatori_storici) == 48
    assert common.HOURLY_FIELDS_REQUIRED == obbligatori_storici
    assert common.HOURLY_PARAMS_REQUIRED == ",".join(obbligatori_storici)
    for nome in NUOVI:
        assert nome not in common.HOURLY_PARAMS_REQUIRED


# ---------------------------------------------------------------------------
# 2. I 2 nuovi campi: in coda, opzionali, con le unita' dichiarate
# ---------------------------------------------------------------------------
def test_i_due_nuovi_campi_sono_in_coda():
    assert common.HOURLY_FIELDS[N_STORICI:] == NUOVI
    assert len(common.HOURLY_FIELDS) == N_STORICI + 2 == 51
    assert common.HOURLY_PARAMS.rstrip().endswith("snowfall_water_equivalent")


def test_i_due_nuovi_campi_sono_opzionali_con_unita_dichiarata():
    for nome in NUOVI:
        assert nome in common.HOURLY_OPTIONAL_SET
        assert nome in common.HOURLY_OPTIONAL_PARAMS
        assert nome not in common.HOURLY_FIELDS_REQUIRED
    assert common.HOURLY_OPTIONAL_UNITS["cloud_cover"] == ("%",)
    assert common.HOURLY_OPTIONAL_UNITS["snowfall_water_equivalent"] == ("mm",)
    # l'opzionale storico resta il primo della coda: nessun riordino
    assert common.HOURLY_OPTIONAL_PARAMS[N_OPZIONALI_STORICI - 1] == "visibility"
    assert common.HOURLY_OPTIONAL_PARAMS[N_OPZIONALI_STORICI:] == tuple(NUOVI)


def test_nomi_nuovi_entrano_in_richiesta():
    for nome in NUOVI:
        assert nome in common.HOURLY_PARAMS.split(",")


# ---------------------------------------------------------------------------
# 3. Selezione dei campi del record (meccanismo `visibility`, riusato)
# ---------------------------------------------------------------------------
def _hourly_payload(**over):
    arr = [1.0] * 72
    h = {f: list(arr) for f in common.HOURLY_FIELDS}
    h.update(over)
    return h


def _units(**over):
    out = {f: u[0] for f, u in common.HOURLY_OPTIONAL_UNITS.items()}
    out.update(over)
    return out


def test_i_nuovi_campi_popolati_vengono_pubblicati_in_coda():
    h = _hourly_payload(cloud_cover=[42] * 72,
                        snowfall_water_equivalent=[0.4] * 72)
    fields, dropped = common.hourly_record_fields(h, _units())
    assert dropped == []
    assert list(fields) == list(common.HOURLY_FIELDS)   # opzionali in coda
    assert fields["cloud_cover"][:3] == [42, 42, 42]
    assert fields["snowfall_water_equivalent"][:3] == [0.4, 0.4, 0.4]


def test_cloud_cover_tutto_null_viene_omesso():
    h = _hourly_payload(cloud_cover=[None] * 72,
                        snowfall_water_equivalent=[0.0] * 72)
    fields, dropped = common.hourly_record_fields(h, _units())
    assert "cloud_cover" not in fields
    assert dropped == ["cloud_cover"]
    # l'altro nuovo campo e i 49 storici restano intatti
    assert "snowfall_water_equivalent" in fields
    assert list(fields) == [f for f in common.HOURLY_FIELDS
                            if f != "cloud_cover"]


def test_snowfall_tutto_null_viene_omesso():
    h = _hourly_payload(cloud_cover=[10] * 72,
                        snowfall_water_equivalent=[None] * 72)
    fields, dropped = common.hourly_record_fields(h, _units())
    assert "snowfall_water_equivalent" not in fields
    assert dropped == ["snowfall_water_equivalent"]
    assert "cloud_cover" in fields


def test_entrambi_null_vengono_omessi_senza_toccare_gli_altri():
    h = _hourly_payload(cloud_cover=[None] * 72,
                        snowfall_water_equivalent=[None] * 72)
    fields, dropped = common.hourly_record_fields(h, _units())
    assert dropped == NUOVI
    assert list(fields) == CAMPI_STORICI_49


def test_unita_sbagliata_viene_omessa():
    # "undefined" e' cio' che l'API dichiara per i modelli che non hanno il
    # campo: nessun valore va interpretato con l'unita' sbagliata.
    h = _hourly_payload(cloud_cover=[50] * 72,
                        snowfall_water_equivalent=[0.2] * 72)
    fields, dropped = common.hourly_record_fields(
        h, {"cloud_cover": "undefined", "snowfall_water_equivalent": "undefined"})
    assert "cloud_cover" not in fields
    assert "snowfall_water_equivalent" not in fields
    assert sorted(dropped) == sorted(NUOVI)


def test_zero_e_un_valore_non_una_assenza():
    """0 mm di neve e' un DATO (nessuna neve caduta), non un null: il campo
    deve restare nel record. Solo i null vengono scartati."""
    h = _hourly_payload(cloud_cover=[0] * 72,
                        snowfall_water_equivalent=[0.0] * 72)
    fields, dropped = common.hourly_record_fields(h, _units())
    assert dropped == []
    assert fields["snowfall_water_equivalent"] == [0.0] * 72
    assert fields["cloud_cover"] == [0] * 72


# ---------------------------------------------------------------------------
# 4. Consumatore reale: fetch_source_data.fetch_batch
# ---------------------------------------------------------------------------
def _response_elements(n, **hourly_over):
    out = []
    for _ in range(n):
        h = {f: [1.0] * 72 for f in common.HOURLY_FIELDS}
        h["time"] = ["2026-11-10T%02d:00" % (i % 24) for i in range(72)]
        h.update(hourly_over)
        daily = {"time": ["2026-11-10", "2026-11-11", "2026-11-12"]}
        for f in common.DAILY_FIELDS:
            daily[f] = [1.0, 1.0, 1.0]
        out.append({"elevation": 120.0, "hourly": h, "daily": daily,
                    "hourly_units": _units()})
    return out


def test_fetch_batch_omette_i_campi_tutti_null():
    batch = {"batch": 0, "global_start": 0, "locations": 2,
             "lats": [41.9, 46.5], "lons": [12.5, 11.35]}
    risposta = _response_elements(2, cloud_cover=[None] * 72,
                                  snowfall_water_equivalent=[0.0] * 72)

    with mock.patch.object(common, "fetch_source_batch",
                           lambda *a, **k: risposta):
        records, err = fetch_source_data.fetch_batch(batch)

    assert err is None
    assert len(records) == 2
    for rec in records:
        for model in ("best_match", "ecmwf_ifs"):
            h = rec[model]["hourly"]
            assert "cloud_cover" not in h, "campo tutto null pubblicato"
            assert "snowfall_water_equivalent" in h
            assert list(h) == [f for f in common.HOURLY_FIELDS
                               if f != "cloud_cover"]


def test_fetch_batch_pubblica_i_campi_popolati_in_coda():
    batch = {"batch": 0, "global_start": 0, "locations": 1,
             "lats": [45.46], "lons": [9.19]}
    risposta = _response_elements(1, cloud_cover=[75] * 72,
                                  snowfall_water_equivalent=[1.2] * 72)

    with mock.patch.object(common, "fetch_source_batch",
                           lambda *a, **k: risposta):
        records, err = fetch_source_data.fetch_batch(batch)

    assert err is None
    h = records[0]["best_match"]["hourly"]
    assert list(h) == list(common.HOURLY_FIELDS)
    assert h["cloud_cover"][0] == 75
    assert h["snowfall_water_equivalent"][0] == 1.2
    assert list(h)[:N_STORICI] == CAMPI_STORICI_49


# ---------------------------------------------------------------------------
# 5. HTTP 400 sul nome nuovo -> retry senza i nuovi, batch che prosegue
# ---------------------------------------------------------------------------
def _http_error(code, message):
    return urllib.error.HTTPError("https://api.open-meteo.com/v1/forecast",
                                  code, "err", {},
                                  io.BytesIO(message.encode("utf-8")))


@pytest.mark.parametrize("nome", NUOVI)
def test_400_sul_campo_nuovo_ripete_senza_gli_opzionali(nome):
    calls = []

    def fake(url, timeout_s=None):
        calls.append(url)
        if len(calls) == 1:
            raise _http_error(
                400, "Cannot initialize Parameter '%s' from invalid String "
                     "value %s for key hourly" % (nome, nome))
        return {"latitude": [41.0], "hourly": {"time": []}}

    with mock.patch.object(common, "_http_json", side_effect=fake), \
         mock.patch.object(common, "_pace_next_request", lambda: None), \
         mock.patch.object(common.time, "sleep", lambda s: None):
        data = common.fetch_source_batch([41.0], [12.0], models="best_match")

    assert data["latitude"] == [41.0]
    assert len(calls) == 2, "atteso UN retry (fallback), non un loop"
    q1 = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)
    q2 = urllib.parse.parse_qs(urllib.parse.urlparse(calls[1]).query)
    h1 = q1["hourly"][0].split(",")
    h2 = q2["hourly"][0].split(",")
    for nuovo in NUOVI:
        assert nuovo in h1, "la richiesta iniziale deve chiedere i nuovi campi"
        assert nuovo not in h2, "il retry deve escludere i nuovi campi"
    # il fallback e' quello storico: TUTTI gli opzionali fuori, obbligatori intatti
    assert h2 == common.HOURLY_FIELDS_REQUIRED
    assert q2["hourly"][0] == common.HOURLY_PARAMS_REQUIRED
    assert q1["daily"] == q2["daily"] and q1["models"] == q2["models"]


def test_400_sul_nome_nuovo_non_fa_fallire_il_fetch_completo():
    """Il batch dopo il fallback produce un record COMPLETO dei 48 obbligatori
    (+ visibility) e senza i nuovi: nessuna eccezione, nessun buco."""
    batch = {"batch": 0, "global_start": 0, "locations": 1,
             "lats": [41.9], "lons": [12.5]}
    senza_nuovi = _response_elements(1)
    for el in senza_nuovi:
        for f in NUOVI:
            el["hourly"].pop(f, None)

    chiamate = {"n": 0}

    def fake_fetch(lats, lons, models=None, forecast_days=None, hourly=None):
        chiamate["n"] += 1
        return senza_nuovi

    with mock.patch.object(common, "fetch_source_batch", side_effect=fake_fetch):
        records, err = fetch_source_data.fetch_batch(batch)

    assert err is None
    assert chiamate["n"] == 2          # un leg per modello
    h = records[0]["best_match"]["hourly"]
    assert set(NUOVI).isdisjoint(h)
    assert list(h) == CAMPI_STORICI_49


# ---------------------------------------------------------------------------
# 6. End-to-end: build -> validate, con e senza i nuovi campi
# ---------------------------------------------------------------------------
def _raw_payload(points, con_nuovi):
    out = {"fetched_at": "2026-11-10T06:00:00Z", "forecast_days": 3,
           "timezone": "Europe/Rome", "driver_model": "ecmwf_ifs",
           "models": ["best_match", "ecmwf_ifs"],
           "hourly_optional": {
               "params": list(common.HOURLY_OPTIONAL_PARAMS),
               "units": {k: list(v) for k, v in common.HOURLY_OPTIONAL_UNITS.items()},
               "coverage": {},
           },
           "points": []}
    for p in points:
        elev = 0 + (abs(p["lat"] - 46.5) * 120) + (abs(p["lon"] - 11.3) * 60)
        rec = gen_fixture_raw.synth_point(p["lat"], p["lon"], elev)
        if not con_nuovi:
            for model in ("best_match", "ecmwf_ifs"):
                for f in NUOVI:
                    rec[model]["hourly"].pop(f, None)
        out["points"].append([p["index"], rec])
    return out


def _build_e_valida(tmp_path, con_nuovi):
    """Esegue build + validate nella catena reale, su una directory temporanea
    (il repo non viene toccato). Ritorna (rc_build, rc_validate, points)."""
    root = Path(tmp_path) / ("con" if con_nuovi else "senza")
    staging = root / "staging"
    work = root / "workdir"
    staging.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    points = common.generate_real_points()
    raw = _raw_payload(points, con_nuovi)
    raw_path = work / "raw.json"
    raw_path.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
    points_path = work / "points.json"
    points_path.write_text(json.dumps(points), encoding="utf-8")
    state_path = work / "last_model_run.json"
    state_path.write_text(json.dumps({
        "run_key": "test_additive_fields",
        "driver_model": "ecmwf_ifs",
        "run_init_ts": 1762750800,
        "run_available_ts": 1762754400,
        "checked_at": "2026-11-10T05:00:00Z",
        "status": "live",
    }), encoding="utf-8")

    with mock.patch.object(common, "DATA_STAGING", staging), \
         mock.patch.object(common, "DATA_WORK", work), \
         mock.patch.object(common, "LAST_MODEL_RUN", state_path), \
         mock.patch.object(sys, "argv", [
             "build_meteorisk_dataset.py",
             "--raw-json", str(raw_path),
             "--points-json", str(points_path)]):
        rc_build = builder.main()

    with mock.patch.object(common, "DATA_STAGING", staging), \
         mock.patch.object(common, "DATA_WORK", work), \
         mock.patch.object(sys, "argv", [
             "validate_dataset.py", "--dir", str(staging)]):
        rc_validate = validate_dataset.main()

    dataset = json.loads((staging / "meteorisk-points.json").read_text(encoding="utf-8"))
    return rc_build, rc_validate, dataset


def test_end_to_end_dataset_valido_con_i_nuovi_campi(tmp_path):
    rc_build, rc_validate, dataset = _build_e_valida(tmp_path, con_nuovi=True)
    assert rc_build == 0
    assert rc_validate == 0, "dataset con i 2 campi nuovi non valido"

    assert dataset["hourly_optional"]["params"] == list(common.HOURLY_OPTIONAL_PARAMS)
    for punto in dataset["points"]:
        for model in ("best_match", "ecmwf_ifs"):
            h = punto["models"][model]["hourly"]
            # (a) i 49 storici: stessi nomi, stesso ordine, posizioni 0..48
            assert list(h)[:N_STORICI] == CAMPI_STORICI_49
            # (b) i 2 nuovi: esattamente in coda
            assert list(h)[N_STORICI:] == NUOVI
            assert len(h) == 51
            # i nuovi hanno davvero dei valori nel fixture (non tutti null)
            assert any(v is not None for v in h["cloud_cover"])
            assert any(v is not None for v in h["snowfall_water_equivalent"])


def test_end_to_end_dataset_valido_senza_i_nuovi_campi(tmp_path):
    """Simula l'esito del fallback 400: raw senza i 2 nuovi -> il dataset e'
    comunque VALIDO, i 49 storici sono intatti e i nuovi semplicemente non ci
    sono (omessi, non messi a null)."""
    rc_build, rc_validate, dataset = _build_e_valida(tmp_path, con_nuovi=False)
    assert rc_build == 0
    assert rc_validate == 0, "dataset senza i 2 campi nuovi non valido"

    for punto in dataset["points"]:
        for model in ("best_match", "ecmwf_ifs"):
            h = punto["models"][model]["hourly"]
            assert set(NUOVI).isdisjoint(h)
            assert list(h) == CAMPI_STORICI_49
