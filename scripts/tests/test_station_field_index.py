# -*- coding: utf-8 -*-
"""COMPLETEZZA DEI CAMPI DEL «CAMPO OSSERVATO DALLE CENTRALINE».

PERCHE' ESISTE. Il client del pannello LIVE legge `data/stations/index.json`
come fonte PRIMARIA della superficie interpolata. L'indice e' un SOTTOINSIEME
delle colonne di `latest.json` (deve stare nel budget gzip), quindi un campo
selezionabile dal client ma assente dall'indice resta `undefined` su TUTTE le
stazioni: la mappa si svuota senza errore e la nota dice «non pubblicata dal
server» anche quando il valore esiste. MISURATO il 2026-11-24 sulla 1.3.0.68:
`rain_3h_mm` non era nell'indice (46 `station_fields`) e la combinazione
«Pioggia cumulata + 3 ore» dava `0 stazioni usate su 0` mentre `latest.json`
aveva `rain_3h_mm` su 3.827 stazioni.

COSA VERIFICA, con le fonti reali (niente tabelle copiate a mano):
 1. estrae `STATION_FIELD_FIELDS` e `STATION_FIELD_DAILY` DALL'HTML attivo
    (APP_FILE di VERSION) e raccoglie ogni campo `raw` che il client chiede;
 2. costruisce l'indice con la STESSA funzione del server
    (`fetch_stations.build_index`, sul `data/stations/latest.json` in repo:
    nessuna rete) e pretende che ogni campo `raw` NON-giornaliero sia fra le
    `station_fields` dell'indice;
 3. i campi della GIORNATA (`temp_max_day_c`, `temp_min_day_c`, le ore del
    picco, `temp_day_partial`) e le finestre 6/24 h NON possono stare
    nell'indice: non esistono in `latest.json` e vivono nel blob
    `daily_series.json`. Per loro il test pretende che
    `scripts/stations/build_daily_series.py` li PRODUCA (ancore di scrittura
    nel sorgente, comprese le famiglie generate con f-string);
 4. il budget gzip misurato resta sotto `INDEX_MAX_GZIP_BYTES`.
"""

import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATIONS_DIR = os.path.join(ROOT, "scripts", "stations")
if STATIONS_DIR not in sys.path:
    sys.path.insert(0, STATIONS_DIR)


def _app_file():
    path = os.path.join(ROOT, "VERSION")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("APP_FILE="):
                return line.split("=", 1)[1].strip()
    return None


def _html():
    app = _app_file()
    assert app, "VERSION senza APP_FILE"
    path = os.path.join(ROOT, app)
    assert os.path.exists(path), "APP_FILE non trovato: %s" % app
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _object_body(source, name):
    """Corpo di `var NAME = { ... };` con bilanciamento delle graffe."""
    m = re.search(r"var\s+%s\s*=\s*\{" % re.escape(name), source)
    assert m, "variabile non trovata nell'html: %s" % name
    start = m.end() - 1
    depth = 0
    for i in range(start, len(source)):
        c = source[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError("graffe non bilanciate per %s" % name)


def _client_raw_fields(html):
    """Campi `raw: '...'` dichiarati dal client, divisi fra indice e giornata."""
    fields = _object_body(html, "STATION_FIELD_FIELDS")
    raw = re.findall(r"raw:\s*'([^']+)'", fields)
    assert raw, "nessun campo `raw:` in STATION_FIELD_FIELDS"
    day_markers = re.findall(r"raw:\s*'(temp_(?:max|min)_day_c)'", fields)
    # Campi che il client stesso dichiara NON pubblicati (`strictAbsence`): la
    # finestra si disabilita con il motivo, non si finge un valore. Oggi e'
    # `temp_mean_day_c` (la media della giornata: il server pubblica max e min).
    strict = re.findall(
        r"raw:\s*'([^']+)'[^}]*strictAbsence:\s*true", fields)
    index_fields = [f for f in raw if f not in day_markers and f not in strict]
    return sorted(set(index_fields)), sorted(set(day_markers))


def _client_daily_fields(html):
    daily = _object_body(html, "STATION_FIELD_DAILY")
    return sorted(set(re.findall(r"'(?:6h|24h)'\s*:\s*'([^']+)'", daily)))


def _client_day_hour_fields(html):
    """Ore del picco e flag di parzialita' della giornata (nomi del BLOB).

    Nel client `STATION_FIELD_FIELDS` porta chiavi normalizzate camelCase
    (tempMaxDayHour), ma il ramo della giornata le SOSTITUISCE con i nomi
    snake_case del blob: qui si prendono quelli, che sono i nomi del file.
    """
    fields = _object_body(html, "STATION_FIELD_FIELDS")
    snake = set(re.findall(r"'(temp_(?:max|min)_day_hour|temp_day_partial)'", fields))
    return sorted(snake)


@pytest.fixture(scope="module")
def index_fields():
    latest_path = os.path.join(ROOT, "data", "stations", "latest.json")
    if not os.path.exists(latest_path):
        pytest.skip("data/stations/latest.json assente: nessun dato reale da indicizzare")
    import json
    from pathlib import Path

    import fetch_stations as F

    with open(latest_path, "r", encoding="utf-8") as fh:
        latest = json.load(fh)
    networks = F.load_networks(Path(STATIONS_DIR) / "networks.json")
    index = F.build_index(latest, networks=networks, extra_fields=F.INDEX_EXTRA_FIELDS)
    gz = F.index_gzip_bytes(index)
    return {
        "fields": list(index.get("station_fields") or []),
        "gzip": gz,
        "budget": F.INDEX_MAX_GZIP_BYTES,
        "stations": len(index.get("stations") or []),
        "problems": F.validate_index(index, latest),
    }


def test_every_client_field_is_in_the_index(index_fields):
    """Un campo selezionabile che non sta nell'indice svuota la mappa."""
    html = _html()
    index_raw, day_raw = _client_raw_fields(html)
    missing = [f for f in index_raw if f not in index_fields["fields"]]
    assert not missing, (
        "campi del client ASSENTI da index.json (la mappa resterebbe vuota): %s\n"
        "station_fields=%d" % (", ".join(missing), len(index_fields["fields"]))
    )
    # e i campi della giornata NON devono stare nell'indice: non esistono in
    # latest.json, sarebbero colonne di soli null a ogni stazione.
    wrong = [f for f in day_raw if f in index_fields["fields"]]
    assert not wrong, (
        "campi della giornata finiti nell'indice (in latest.json non esistono): %s"
        % ", ".join(wrong)
    )


def test_day_fields_are_produced_by_the_daily_blob():
    """I campi che l'indice non puo' portare devono esistere nel blob."""
    html = _html()
    _, day_raw = _client_raw_fields(html)
    daily = _client_daily_fields(html)
    hours = _client_day_hour_fields(html)
    blob = set(day_raw) | set(daily) | set(hours)
    assert blob, "nessun campo del blob dichiarato dal client"

    with open(os.path.join(STATIONS_DIR, "build_daily_series.py"),
              "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()

    # Scritture letterali: rain_6h_mm, wind_24h_ms, gust_max_6h_ms, ...
    literal = set(re.findall(r'record\["([a-z0-9_]+)"\]\s*=', src))
    # Famiglie generate con f-string (misurate nel sorgente, non indovinate):
    #   record[f"{label}_{hours}h_{unit}"]      -> temp_6h_c, rh_24h_pct, dew_6h_c...
    #   record[f"{label}_{which}_day_{unit}"]   -> temp_max_day_c, temp_min_day_c...
    #   record[f"{label}_{which}_day_hour"]     -> temp_max_day_hour...
    #   record[f"{label}_day_partial"]          -> temp_day_partial
    families = []
    if re.search(r'record\[f"\{label\}_\{hours\}h_\{unit\}"\]', src):
        for label, unit in (("temp", "c"), ("rh", "pct"), ("dew", "c")):
            for hours_w in ("6h", "24h"):
                families.append("%s_%s_%s" % (label, hours_w, unit))
    # Il codice scrive le due famiglie separatamente (max e min), quindi si
    # verificano DUE ancore distinte invece di indovinare un `{which}`.
    if re.search(r'record\[f"\{label\}_max_day_\{unit\}"\]', src) and \
       re.search(r'record\[f"\{label\}_min_day_\{unit\}"\]', src):
        for label, unit in (("temp", "c"), ("rh", "pct")):
            for which in ("max", "min"):
                families.append("%s_%s_day_%s" % (label, which, unit))
    if re.search(r'record\[f"\{label\}_max_day_hour"\]', src) and \
       re.search(r'record\[f"\{label\}_min_day_hour"\]', src):
        for label in ("temp", "rh"):
            for which in ("max", "min"):
                families.append("%s_%s_day_hour" % (label, which))
    if re.search(r'record\[f"\{label\}_day_partial"\]', src):
        for label in ("temp", "rh"):
            families.append("%s_day_partial" % label)
    produced = literal | set(families)

    not_produced = sorted(blob - produced)
    assert not not_produced, (
        "campi chiesti dal client che NESSUNO produce nel blob giornaliero: %s"
        % ", ".join(not_produced)
    )


def test_index_is_coherent_and_within_budget(index_fields):
    assert not index_fields["problems"], "indice incoerente: %s" % index_fields["problems"][:5]
    assert index_fields["gzip"] <= index_fields["budget"], (
        "indice %d B gzip > tetto %d B (%d stazioni)"
        % (index_fields["gzip"], index_fields["budget"], index_fields["stations"])
    )


def test_rain_3h_is_the_case_that_broke():
    """Regressione esplicita del difetto misurato il 2026-11-24."""
    html = _html()
    index_raw, _ = _client_raw_fields(html)
    for name in ("rain_3h_mm", "wind_mean_3h_ms", "gust_max_3h_ms",
                 "temp_max_5m_c", "temp_min_5m_c", "temp_max_30m_c",
                 "temp_min_30m_c", "temp_max_1h_c", "temp_min_1h_c",
                 "temp_max_3h_c", "temp_min_3h_c"):
        assert name in index_raw, "il client non chiede piu' %s" % name
