# -*- coding: utf-8 -*-
"""Contratto del parametro OPZIONALE `visibility` (metri) nella pipeline punti.

Perimetro (2026-10-27): `visibility` serve SOLO al badge nebbia delle province.
NON e' una variabile delle mappe previsionali: non deve comparire ne' in
`forecast_v2_engine.V2_RISK_VARS` ne' in `color_scales.json` (il test
`test_visibility_non_entra_nelle_mappe` lo blinda).

Cosa si verifica qui:
  * `visibility` e' in coda a HOURLY_PARAMS (ordine storico dei campi intatto)
    ed e' dichiarata OPZIONALE, quindi NON obbligatoria;
  * `hourly_record_fields` tiene il campo solo se ha almeno un valore non nullo
    e unita' "m" (un [null x72] con unit "undefined" e' cio' che l'API
    restituisce per i modelli che non ce l'hanno: non e' un dato);
  * un HTTP 400 che cita il parametro opzionale fa ripetere la richiesta SENZA
    il campo e il batch prosegue (fallback silenzioso); un 400 su un campo
    obbligatorio NON viene ritentato.
"""

import io
import json
import os
import sys
import urllib.error
import urllib.parse

from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# Ordine storico dei campi hourly (prima di `visibility`): il contratto e' che
# l'opzionale sia in CODA, cosi' la stringa dei campi obbligatori resta quella
# di sempre.
STORICO = [
    "temperature_2m", "relativehumidity_2m", "dew_point_2m", "pressure_msl",
    "cape", "precipitation_probability", "windspeed_10m", "winddirection_10m",
    "windspeed_100m", "winddirection_100m", "windgusts_10m", "weathercode",
    "precipitation", "showers", "freezing_level_height",
]


def _hourly_payload(**over):
    """Blocco `hourly` minimo: tutti i campi obbligatori + l'opzionale."""
    arr = [1.0] * 72
    h = {f: list(arr) for f in common.HOURLY_FIELDS}
    h.update(over)
    return h


# ---------------------------------------------------------------------------
# 1. Dichiarazione del parametro
# ---------------------------------------------------------------------------
def test_visibility_e_in_coda_e_non_obbligatoria():
    assert common.HOURLY_FIELDS[-1] == "visibility"
    assert common.HOURLY_PARAMS.rstrip().endswith("visibility")
    assert common.HOURLY_OPTIONAL_PARAMS == ("visibility",)
    assert "visibility" not in common.HOURLY_FIELDS_REQUIRED
    assert "visibility" not in common.HOURLY_PARAMS_REQUIRED


def test_ordine_storico_dei_campi_obbligatori_preservato():
    # I primi campi (misurati sul main prima di questa modifica) restano nella
    # stessa posizione: l'opzionale e' l'ULTIMO, non sposta nulla.
    assert common.HOURLY_FIELDS[:len(STORICO)] == STORICO
    assert common.HOURLY_FIELDS_REQUIRED == common.HOURLY_FIELDS[:-1]
    assert common.HOURLY_PARAMS_REQUIRED == ",".join(common.HOURLY_FIELDS[:-1])


def test_unita_ammessa_dichiarata_in_metri():
    assert common.HOURLY_OPTIONAL_UNITS["visibility"] == ("m",)


# ---------------------------------------------------------------------------
# 2. Selezione dei campi del record
# ---------------------------------------------------------------------------
def test_visibility_popolata_in_metri_viene_pubblicata():
    h = _hourly_payload(visibility=[300.0] * 72)
    fields, dropped = common.hourly_record_fields(h, {"visibility": "m"})
    assert fields["visibility"] == [300.0] * 72
    assert dropped == []
    # l'ordine dei campi e' quello di HOURLY_FIELDS (visibility in coda)
    assert list(fields) == list(common.HOURLY_FIELDS)


def test_visibility_tutta_null_viene_scartata():
    h = _hourly_payload(visibility=[None] * 72)
    fields, dropped = common.hourly_record_fields(h, {"visibility": "undefined"})
    assert "visibility" not in fields
    assert dropped == ["visibility"]


def test_visibility_con_unita_sbagliata_viene_scartata():
    # Il campo ha valori ma l'unita' non e' metri: non e' la grandezza che il
    # badge sa leggere, quindi non si pubblica (meglio niente che un numero
    # interpretato male).
    h = _hourly_payload(visibility=[50.0] * 72)
    fields, dropped = common.hourly_record_fields(h, {"visibility": "km"})
    assert "visibility" not in fields
    assert dropped == ["visibility"]


def test_senza_visibility_il_record_resta_completo():
    h = {f: [1.0] * 72 for f in common.HOURLY_FIELDS_REQUIRED}
    fields, dropped = common.hourly_record_fields(h, {})
    assert fields == h
    assert dropped == []


def test_un_solo_valore_non_null_basta():
    vals = [None] * 72
    vals[7] = 180.0
    fields, _ = common.hourly_record_fields(_hourly_payload(visibility=vals),
                                            {"visibility": "m"})
    assert fields["visibility"][7] == 180.0


# ---------------------------------------------------------------------------
# 3. Fallback silenzioso sull'HTTP 400
# ---------------------------------------------------------------------------
def _http_error(code, message):
    return urllib.error.HTTPError("https://api.open-meteo.com/v1/forecast",
                                  code, "err", {}, io.BytesIO(
                                      message.encode("utf-8")))


def test_400_sul_parametro_opzionale_ripete_senza_visibility():
    calls = []

    def fake(url, timeout_s=None):
        calls.append(url)
        if len(calls) == 1:
            raise _http_error(
                400, "Cannot initialize Parameter 'visibility' from invalid "
                     "String value visibility for key hourly")
        return {"latitude": [41.0], "hourly": {"time": []}}

    with mock.patch.object(common, "_http_json", side_effect=fake), \
         mock.patch.object(common, "_pace_next_request", lambda: None), \
         mock.patch.object(common.time, "sleep", lambda s: None):
        data = common.fetch_source_batch([41.0], [12.0], models="best_match")

    assert data["latitude"] == [41.0]
    assert len(calls) == 2, calls
    q1 = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)
    q2 = urllib.parse.parse_qs(urllib.parse.urlparse(calls[1]).query)
    assert "visibility" in q1["hourly"][0]
    assert "visibility" not in q2["hourly"][0]
    assert q2["hourly"][0] == common.HOURLY_PARAMS_REQUIRED
    # il resto della richiesta non cambia
    assert q1["daily"] == q2["daily"]
    assert q1["models"] == q2["models"]


def test_400_su_campo_obbligatorio_non_viene_ritentato():
    calls = []

    def fake(url, timeout_s=None):
        calls.append(url)
        raise _http_error(400, "Parameter 'temperature_2m' is not available")

    with mock.patch.object(common, "_http_json", side_effect=fake), \
         mock.patch.object(common, "_pace_next_request", lambda: None), \
         mock.patch.object(common.time, "sleep", lambda s: None):
        with pytest.raises(urllib.error.HTTPError):
            common.fetch_source_batch([41.0], [12.0], models="best_match")
    assert len(calls) == 1


def test_riconoscimento_del_parametro_nel_messaggio():
    assert common._optional_params_in_error(
        "Cannot initialize Parameter 'visibility' from invalid String value") \
        == ("visibility",)
    assert common._optional_params_in_error(
        "hourly: invalid parameter combination") == ("visibility",)
    assert common._optional_params_in_error("Parameter 'foo' is not available") == ()


# ---------------------------------------------------------------------------
# 4. Perimetro: la visibilita' NON e' una variabile delle mappe previsionali
# ---------------------------------------------------------------------------
def test_visibility_non_entra_nelle_mappe():
    engine = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                          "forecast_v2_engine.py")
    scales = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                          "color_scales.json")
    src = open(engine, encoding="utf-8").read()
    assert '"visibility"' not in src, (
        "visibility non deve essere in V2_RISK_VARS: il badge nebbia usa la "
        "pipeline punti, non i frame .om")
    data = json.load(open(scales, encoding="utf-8"))
    assert "visibility" not in data["scales"], (
        "nessuna scala visibility nel renderer: nessun layer nebbia sulle "
        "mappe previsionali")
