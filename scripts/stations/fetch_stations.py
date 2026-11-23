#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rete di centraline: scarica le osservazioni reali da MeteoHub e le normalizza.

FONTE
-----
MeteoHub (portale del progetto MISTRAL, Agenzia ItaliaMeteo + CINECA)
    GET https://meteohub.agenziaitaliameteo.it/api/observations
Parametri realmente richiesti dal servizio (verificati il 2026-10-27, NON tutti
presenti in /api/specs v0.5.9):
    q        query arkimet, obbligatoria, DEVE contenere la clausola di licenza
             (`license:CCBY_COMPLIANT`) e una finestra `reftime: >=A,<=B`
    last     accettato ma NON filtra (la risposta contiene comunque la serie)
    networks filtro per rete (separatore ` or `, come fa il client web)
L'endpoint e' pubblico: nessun token, nessun account (verificato: HTTP 200 senza
header Authorization). Se in futuro rispondesse 401 lo script si ferma e lo dice.

COSA PUBBLICA
-------------
`data/stations/latest.json`, schema stabile:

    {generated_at, source, source_url, licence, licence_url, attribution,
     units, counts, omitted_fields, derived_fields,
     stations: [{id, lat, lon, name?, network, obs_time,
                 temperature_2m, relative_humidity_2m,
                 wind_speed_10m, wind_direction_10m,
                 precipitation, precipitation_period_s, dew_point_2m?,
                 station_pressure_hPa?, wind_gust_10m?, snow_depth_m?,
                 fresh_snow_depth_m?, river_level_m?,

                 # 1.3.0.56: DERIVATI DALLE SERIE (additivi, retrocompatibili)
                 rain_1h_mm?, rain_3h_mm?,
                 rain_1h_coverage_frac?, rain_3h_coverage_frac?,
                 rain_1h_null_reason?, rain_3h_null_reason?,
                 rain_period_s_used?, rain_source_product?,
                 rain_3h_period_s?, rain_3h_product?, rain_window_end?,
                 rain_hourly?, rain_qc_rejected?,
                 gust_max_1h_ms?, gust_max_3h_ms?,
                 gust_max_1h_coverage_frac?, gust_max_3h_coverage_frac?,
                 gust_max_1h_null_reason?, gust_max_3h_null_reason?,
                 gust_period_s?, gust_source_product?, gust_window_end?,
                 gust_hourly?,
                 wind_mean_1h_ms?, wind_mean_3h_ms?,
                 wind_mean_1h_coverage_frac?, wind_mean_3h_coverage_frac?,
                 wind_mean_1h_null_reason?, wind_mean_3h_null_reason?,
                 wind_max_3h_ms?, wind_source?, wind_period_s?,
                 wind_source_product?, wind_window_end?, wind_hourly?,
                 wind_qc_rejected?}]}

DERIVATI DALLE SERIE (1.3.0.56)
-------------------------------
`_latest_value` prende UNA osservazione: per una cumulata di pioggia o una
media del vento serve la SERIE, che l'API gia' restituisce (166-181 valori a
60 s su 3 h per le reti DPC, 35 a 5 min, 12 a 15 min, 1 a 60 min). I campi
`rain_*`, `gust_*` e `wind_*` sono calcolati dalla serie e sono ADDITIVI: un
client che non li conosce continua a leggere gli stessi campi di prima.

Unita': pioggia in mm, vento e raffica in m/s (NESSUN km/h: le centraline
danno m/s e una conversione qui sarebbe un errore di unita' nascosto nel
payload; converte il client). Coperture in frazione 0..1 del tempo.

Regole (dichiarate anche in `derived_fields` del documento):
  * `rain_1h_mm`/`rain_3h_mm` = integrale su [ultimo dato - 1h/3h, ultimo
    dato]: la finestra segue il DATO, non `now`, perche' con ~32 min di
    latenza mediana una finestra ancorata a `now` starebbe sotto il 50% di
    copertura quasi sempre;
  * prodotto scelto per COPERTURA migliore (a pari copertura il periodo piu'
    fine), NON per `ref` piu' recente;
  * `*_null_reason = coverage_below_50pct` sotto il 50% di copertura;
  * gate fisico: pioggia oltre 300 mm/h (o negativa) scartata e contata in
    `rain_qc_rejected` / `counts.rain_points_qc_rejected`; vento oltre 60 m/s
    e raffica oltre 75 m/s idem; temperatura: al range assoluto |T| <= 60 degC
    si aggiunge il gate di PLAUSIBILITA' `scripts/stations/temp_qc.py`
    (|dT| > 3 degC E |dT|/dt > 6 degC/h rispetto all'ultimo campione
    accettato, ancora dal blob giornaliero con `--baseline`), contato in
    `temp_qc_rejected` / `counts.temp_points_qc_rejected`;
  * campo ASSENTE = grandezza non disponibile (come `visibility`); `null` =
    c'e' ma non passa il gate, e porta SEMPRE il motivo (contratto #207).

Campi aggiuntivi (verificati su /api/observations anonimo, HTTP 200, 4.455
stazioni, licenza CCBY_COMPLIANT): B10004 = pressione di STAZIONE al livello
1,0,0,0 (NON MSL), divisa per 100 e filtrata come anomalo; B11041 = raffica
massima; B13013/B13012 = neve al suolo/fresca; B13215 = livello idrometrico,
IDROLOGICO e quindi campo separato non meteo. Il client mette i tre campi
meteo nella tendina centraline e tiene il livello idrometrico fuori da essa.
Visibilita' e cloud NON esistono nella sorgente: nessun campo inventato.

`data/stations/index.json` (stesso run, `--index-out`), pensato per il client:
stesse stazioni SENZA le serie orarie, `stations` come array nell'ordine
dichiarato in `station_fields` (`id, lat, lon, name, network, obs_time,
temperature_2m, relative_humidity_2m, wind_speed_10m, wind_direction_10m,
precipitation, precipitation_period_s`), valori correnti a 1 decimale, piu'
`count`, `networks` con i conteggi, `attribution`, `bbox`, licenza e unita'.
Misurato il 2026-10-27 sulle 4.137 stazioni reali: 535.300 B / 102.098 B gzip
contro 1.045.677 B / 132.209 B gzip di `latest.json` (la forma a oggetti, con
gli stessi campi e gli stessi `null`, costava 1.213.768 B / 127.742 B gzip:
sopra il budget e piu' grossa del file completo).

Scelte dichiarate (non sono semplificazioni nascoste):
  * finestra e soglia di eta' sono lo stesso numero, 180 minuti: il file contiene
    l'ultima osservazione di ogni stazione entro 3 ore. Non e' un dettaglio: con
    una finestra da 60 minuti `mnw` (927 stazioni) e `arpafvg` (152) sparivano per
    intero, perche' pubblicano a lotti e il loro record piu' recente e' stato
    misurato a 67,6 minuti (2026-10-27 07:07 UTC). Ogni stazione porta comunque il
    suo `obs_time`: un client che vuole solo dati freschi filtra su quello.
  * `pressure_msl` NON c'e'. La sorgente ha B10004 al livello `1,0,0,0`, cioe'
    PRESSIONE DI STAZIONE, non ridotta al livello del mare; senza la quota non
    si puo' ridurre. Viene pubblicata come `station_pressure_hPa` (Pa/100),
    MAI come `pressure_msl`: pubblicarla con quell'etichetta sarebbe falso.
    Nel campione reale i valori vanno da 56.730 a 163.820 Pa (567,3-1638,2 hPa):
    entrambi gli estremi sono ANOMALI e vengono scartati e contati in
    `values_out_of_range`; la finestra accettata e' 600-1085 hPa.
  * `visibility` NON c'e' e non viene messa a null per finta: la sorgente non
    la espone. Il campo e' semplicemente assente.
  * `precipitation` e' l'ultima accumulata disponibile, ma il periodo di
    accumulo CAMBIA per rete (trange `1,0,60` = 60 s, `1,0,900` = 15 min,
    `1,0,3600` = 1 h). Per questo ogni stazione porta anche
    `precipitation_period_s`: senza quel numero il valore non e' confrontabile.
  * l'`id` e' sintetico (`<rete>:<lat>,<lon>`) perche' la sorgente non espone
    un identificatore di stazione, solo il nome (B01019). I decimali sono 5
    (~1 m) perche' a 4 decimali TRE coppie di stazioni distinte collidono
    (misurato: `boa` 44.26714,12.35751 e 44.26714,12.35752): se a 5 decimali
    comparisse una collisione lo script scala a 6 e lo dichiara.
  * `dew_point_2m` e' DERIVATO da temperatura e umidita' (Magnus, Alduchov &
    Eskridge 1996) ed e' dichiarato in `derived_fields`.
  * unita': temperatura e rugiada in degC, vento in m/s (unita' della sorgente),
    precipitazione in mm (= kg/m2 della sorgente), direzione in gradi veri.

SOLO STDLIB: urllib, gzip, json, math, argparse. Nessuna dipendenza aggiunta.

Uso:
    python -u scripts/stations/fetch_stations.py --out data/stations/latest.json
    python scripts/stations/fetch_stations.py --refresh-networks
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # eseguito come script: la cartella dello script e' su sys.path
    from temp_qc import (
        TEMP_MAX_RATE_C_PER_H,
        TEMP_MIN_ABS_DELTA_C,
        qc_temperature_points,
    )
except ImportError:  # importato come pacchetto (test/strumenti)
    from scripts.stations.temp_qc import (
        TEMP_MAX_RATE_C_PER_H,
        TEMP_MIN_ABS_DELTA_C,
        qc_temperature_points,
    )

API_BASE = "https://meteohub.agenziaitaliameteo.it"
OBS_PATH = "/api/observations"
DATASETS_PATH = "/api/datasets"
LICENSE_GROUP = "CCBY_COMPLIANT"
USER_AGENT = (
    "MeteoRiskLight-stations/1.0 "
    "(+https://github.com/meteoriskitalia-netizen/MeteoRisk-Light-v2)"
)

# Codici BUFR usati (i nomi arrivano da `descr` nella risposta, non inventati).
VAR_NAME = "B01019"      # LONG STATION OR SITE NAME
VAR_TEMP = "B12101"      # TEMPERATURE/DRY-BULB TEMPERATURE      [K]
VAR_RH = "B13003"        # RELATIVE HUMIDITY                      [%]
VAR_WSPD = "B11002"      # WIND SPEED                             [M/S]
VAR_WDIR = "B11001"      # WIND DIRECTION                         [DEGREE TRUE]
VAR_PRECIP = "B13011"    # TOTAL PRECIPITATION                    [KG/M**2]
# Campi reali aggiunti (verificati su /api/observations anonimo, HTTP 200,
# 4.455 stazioni, licenza CCBY_COMPLIANT). Nomi/descr presi dalla risposta.
VAR_PRESS = "B10004"     # PRESSURE                               [PA]
VAR_GUST = "B11041"      # MAXIMUM WIND GUST SPEED                 [M/S]
VAR_SNOW = "B13013"      # TOTAL SNOW DEPTH                        [M]
VAR_FRESH_SNOW = "B13012"  # DEPTH OF FRESH SNOW                   [M]
VAR_RIVER = "B13215"     # River level                             [M]

# B10004 e' PRESSIONE DI STAZIONE solo al livello 1,0,0,0: NON e' MSL.
# Senza quota non si puo' ridurre al livello del mare.
PRESS_LEVEL_STATION = "1,0,0,0"

# trange dell'osservazione istantanea; gli altri sono medie/accumuli.
TRANGE_INSTANT = "254,0,0"

# Range fisici di plausibilita': fuori da qui il valore viene scartato e contato.
# (Non sono soglie meteorologiche: servono solo a non pubblicare byte rotti.)
LIMITS = {
    "temperature_2m": (-130.0, 60.0),        # degC
    "relative_humidity_2m": (0.0, 100.0),    # %
    "wind_speed_10m": (0.0, 75.0),           # m/s
    "wind_direction_10m": (0.0, 360.0),      # deg
    "precipitation": (0.0, 200.0),           # mm nel periodo
    "dew_point_2m": (-90.0, 60.0),           # degC
    # hPa = Pa/100. Il campione reale va da 56.730 a 163.820 Pa
    # (567,3-1638,2 hPa): entrambi gli estremi sono ANOMALI e vanno scartati.
    # La finestra 600-1085 hPa conserva le stazioni di alta quota (≈600 hPa
    # a ~4000 m) ed esclude i valori rotti. NON e' MSL.
    "station_pressure_hPa": (600.0, 1085.0),
    "wind_gust_10m": (0.0, 75.0),            # m/s (max reale 27,3)
    "snow_depth_m": (0.0, 10.0),             # m
    "fresh_snow_depth_m": (0.0, 5.0),        # m
    "river_level_m": (-50.0, 100.0),         # m, quota idrologica locale
}

# Budget del file completo in byte. NON e' un limite della piattaforma: e' il
# tetto oltre il quale il run si ferma e l'ultimo dataset valido resta in piedi.
# MISURATO il 2026-11-23 con i campi derivati dalle serie (1.3.0.56): 2.780.971
# byte / 4.944 stazioni contro 1.219.893 della 1.3.0.55 (+1.561.078). Il costo in
# GIT e' molto minore del grezzo perche' i blob sono compressi: gzip 215.900 B
# contro 161.665 (+54.235 B per commit, ~8 MB/giorno a 144 run).
# MISURATO il 2026-11-23 21:09Z con le finestre termiche 5m/30m/1h/3h (1.3.0.61):
# 6.414.584 byte su 4.897 stazioni, +3,6 MB grezzi e ~+250 kB gzip per commit
# (~36 MB/giorno a 144 run). Il costo e' il contratto: ogni stazione pubblica 3
# grandezze x 4 finestre con copertura, motivo, estremi e i bucket orari che
# alimentano il blob giornaliero. 7.500.000 = misurato + 15% di margine. Se lo
# sfonda il run si ferma e il dataset precedente resta in piedi.
DEFAULT_MAX_BYTES = 7_500_000

# Indice compatto per il client (index.json): stesse stazioni di latest.json,
# senza le serie orarie. Misurato il 2026-10-27 sul file reale (4.137 stazioni):
# 1.045.677 B / 132.209 B gzip per latest.json -> 535.300 B / 102.098 B gzip
# per l'indice. La forma a OGGETTI con gli stessi campi costava 1.213.768 B /
# 127.742 B gzip (ogni valore assente diventa `"campo":null`, ~22 B invece dei
# 5 B di un `null` in un array): sopra il budget, quindi le stazioni sono array
# con l'ordine dichiarato in `station_fields`.
INDEX_SCHEMA = "stations-index/1"
INDEX_STATION_FIELDS = (
    "id",
    "lat",
    "lon",
    "name",
    "network",
    "obs_time",
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "precipitation_period_s",
    "station_pressure_hPa",   # B10004/100, livello 1,0,0,0 (NON MSL)
    "wind_gust_10m",          # B11041, massimo sulla finestra della sorgente
    "snow_depth_m",           # B13013
    "fresh_snow_depth_m",     # B13012
    "river_level_m",          # B13215, idrologico (separato dal meteo)
)
# Campi calcolati dalle SERIE che si aggiungono all'indice SOLO se il budget
# gzip lo consente (misurato a ogni run, non stimato). Ordine = priorita' per il
# layer LIVE: prima la pioggia, poi la raffica, poi il vento medio, poi le MEDIE
# termiche 5m/30m/1h/3h (1.3.0.61). Min/max restano in latest.json: l'indice e'
# la sorgente del CAMPO, che disegna la media, non gli estremi.
INDEX_EXTRA_FIELDS = (
    "rain_1h_mm",
    "gust_max_1h_ms",
    "wind_mean_1h_ms",
    "temp_mean_5m_c",
    "temp_mean_30m_c",
    "temp_mean_1h_c",
    "temp_mean_3h_c",
    "rh_mean_5m_pct",
    "rh_mean_30m_pct",
    "rh_mean_1h_pct",
    "rh_mean_3h_pct",
    "dew_mean_5m_c",
    "dew_mean_30m_c",
    "dew_mean_1h_c",
    "dew_mean_3h_c",
    # I MOTIVI viaggiano nell'indice: il client carica PRIMA index.json e senza
    # questi campi non potrebbe distinguere "finestra non calcolabile" da
    # "grandezza assente" (userebbe l'istantanea invece di escludere). Sono
    # stringhe, non numeri: vedi INDEX_STRING_FIELDS.
    "temp_5m_null_reason",
    "temp_30m_null_reason",
    "temp_1h_null_reason",
    "temp_3h_null_reason",
    "rh_5m_null_reason",
    "rh_30m_null_reason",
    "rh_1h_null_reason",
    "rh_3h_null_reason",
    "dew_5m_null_reason",
    "dew_30m_null_reason",
    "dew_1h_null_reason",
    "dew_3h_null_reason",
    "wind_mean_30m_null_reason",
    "gust_max_30m_null_reason",
)
# Campi dell'indice che sono STRINGHE (motivi): non si arrotondano ne' si
# confrontano numericamente.
INDEX_STRING_FIELDS = frozenset(
    field for field in INDEX_EXTRA_FIELDS if field.endswith("_null_reason")
)
# Budget dell'indice compattato in byte GZIP. NON e' un limite della piattaforma:
# e' un tetto scelto per tenere il payload leggero. Storico: 120 KiB (122.880 B)
# con 4.137 stazioni, misurato 101.258 B (margine 17%). MISURATO il 2026-11-23 su
# data/stations/latest.json (5.042-5.044 stazioni, 1.250.659-1.251.117 B):
# indice CON i nomi 130.662-130.743 B gzip (raw ~784 KiB), SENZA nomi
# 92.289-92.362 B gzip. Con il vecchio tetto ogni run degradava l'indice a
# `name_omitted` e il client mostrava "Stazione" per tutte (mri-light:
# `st.name || 'Stazione'`); 160 KiB coprono il dato reale con margine ~25%
# (163.840 - 130.743). Il tetto NON e' una garanzia: se le reti crescono ancora
# la degradazione dichiarata resta il comportamento.
# MISURATO il 2026-11-23 21:09Z con le MEDIE TERMICHE (1.3.0.61): 4.897 stazioni,
# indice con nomi e 29 campi extra (15 valori + 14 motivi) = 1.840.880 B raw /
# 175.779 B gzip, cioe' SOPRA i 160 KiB. 200 KiB (204.800 B) lasciano ~29 kB di
# margine (14%). I motivi costano solo ~9 kB gzip (stringhe ripetute), ma senza
# di loro il client che carica index.json non distingue "finestra non calcolabile"
# da "grandezza assente". Se la crescita lo sfonda, la degradazione dichiarata
# (prima i campi extra, poi i nomi) resta attiva.
INDEX_MAX_GZIP_BYTES = 204_800  # 200 KiB (misurato 178.003 B con valori e motivi)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get(url: str, attempts: int = 3, timeout: int = 180) -> tuple[bytes, dict]:
    """GET con gzip e retry. Ritorna (body decompresso, info trasporto).

    gzip non e' un dettaglio: sulla finestra da 180 minuti il corpo passa da
    17.800.239 a 964.862 byte (18x); sulla finestra da 60 minuti da 4.611.899 a
    286.837 byte (16x) e da 9,56 s a 1,06 s (misurato il 2026-10-27). Meno byte
    significa meno carico sul servizio pubblico.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        started = time.time()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in encoding:
                    raw = gzip.decompress(raw)
                info = {
                    "http_status": resp.status,
                    "wire_bytes": int(resp.headers.get("Content-Length") or 0),
                    "seconds": round(time.time() - started, 2),
                }
                return raw, info
        except urllib.error.HTTPError as exc:
            body = exc.read()[:400].decode("utf-8", "replace")
            last_err = RuntimeError(f"HTTP {exc.code} su {url}: {body}")
            # 4xx (tranne 429) non migliorano ritentando.
            if exc.code < 500 and exc.code != 429:
                raise last_err from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = RuntimeError(f"errore di rete su {url}: {exc}")
        if attempt < attempts:
            time.sleep(2 ** attempt)
    raise last_err if last_err else RuntimeError("richiesta fallita")


def build_query(start: datetime, end: datetime) -> str:
    """Query arkimet nella forma che il servizio pretende.

    Formato ricavato dal client web di MeteoHub (parseReftime) e verificato:
    `reftime: >=YYYY-MM-DD HH:MM,<=YYYY-MM-DD HH:MM` piu' la clausola di licenza.
    Senza `license:` il servizio risponde 400 "License group parameter is mandatory".
    """
    fmt = "%Y-%m-%d %H:%M"
    return (
        f"reftime: >={start.strftime(fmt)},<={end.strftime(fmt)};"
        f"license:{LICENSE_GROUP}"
    )


# --------------------------------------------------------------------------
# Rete di licenze/attribuzioni (una volta sola, poi tabella su disco)
# --------------------------------------------------------------------------
def refresh_networks(path: Path) -> dict:
    """Rigenera la tabella licenze/attribuzioni dai metadati del servizio.

    28 richieste (una per dataset OBS). NON si fa a ogni run: la tabella vive
    nel repo (`scripts/stations/networks.json`) e si rigenera solo con questo
    flag. Ogni voce e' copiata dalla risposta, non scritta a mano.
    """
    raw, _ = http_get(API_BASE + DATASETS_PATH)
    datasets = json.loads(raw)
    obs = [d for d in datasets if d.get("category") == "OBS"]
    networks: dict[str, dict] = {}
    for ds in obs:
        ds_id = ds["id"]
        params = urllib.parse.urlencode({"licenceSpecs": "true"})
        body, _ = http_get(f"{API_BASE}{DATASETS_PATH}/{ds_id}?{params}")
        spec = json.loads(body)
        networks[ds_id] = {
            "name": spec.get("name"),
            "description": spec.get("description"),
            "license": spec.get("license"),
            "license_description": spec.get("license_description"),
            "license_url": spec.get("license_url"),
            "group_license": spec.get("group_license"),
            "attribution": spec.get("attribution"),
            "attribution_description": spec.get("attribution_description"),
            "attribution_url": spec.get("attribution_url"),
        }
    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": f"{API_BASE}{DATASETS_PATH}/<id>?licenceSpecs=true",
        "note": (
            "Tabella generata da `fetch_stations.py --refresh-networks`. "
            "Non modificare a mano: i campi arrivano dai metadati MeteoHub."
        ),
        "networks": networks,
    }
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return doc


def load_networks(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"[FATAL] tabella reti mancante: {path}\n"
            "        rigenerala con --refresh-networks"
        )
    return json.loads(path.read_text(encoding="utf-8")).get("networks", {})


# --------------------------------------------------------------------------
# Parsing / normalizzazione (funzioni pure: nessuna rete, testabili)
# --------------------------------------------------------------------------
def parse_ref(ref: str) -> datetime:
    """`2026-10-27T06:30:00` -> datetime UTC. La sorgente non mette la zona."""
    dt = datetime.fromisoformat(ref)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def trange_period_seconds(trange: str) -> int | None:
    """`1,0,900` -> 900. Solo per gli accumuli (primo campo `1`)."""
    parts = (trange or "").split(",")
    if len(parts) != 3 or parts[0] != "1":
        return None
    try:
        period = int(parts[2])
    except ValueError:
        return None
    return period if period > 0 else None


def trange_offset_seconds(trange: str) -> int | None:
    """P2 del trange (`1,1800,3600` -> 1800): lo scostamento rispetto al `ref`.

    Tutti i prodotti B13011/B11002/B11041 osservati (28 reti, 2026-11-23) hanno
    P2 = 0, ma assumere P2 = 0 in generale sarebbe un'ipotesi non dichiarata: se
    comparisse un `1,1800,3600` l'integrale userebbe il tempo sbagliato.
    """
    parts = (trange or "").split(",")
    if len(parts) != 3:
        return None
    try:
        offset = int(parts[1])
    except ValueError:
        return None
    return offset if offset >= 0 else None


def trange_stat_type(trange: str) -> int | None:
    """Primo campo del trange: 0 min, 1 somma, 2 massimo, 3 media, 254 istantaneo.

    Mappatura MISURATA, non presa da un manuale: B13011 (TOTAL PRECIPITATION) e'
    un accumulo e usa `1,0,P`; B11041 (MAXIMUM WIND GUST) e' un massimo e usa
    `2,0,P`; B11002 (WIND SPEED) compare sia come istantaneo `254,0,0` sia come
    media `3,0,3600` sia come minimo `0,0,900` (2026-11-23, 5.097 record). La
    coerenza interna lo conferma: la raffica `2,0,900` di `locali` (3,5 m/s) e'
    maggiore del vento istantaneo della stessa stazione (2,8 m/s), mentre la
    lettura "2 = somma" richiederebbe raffiche elementari da 0,23 m/s.
    """
    parts = (trange or "").split(",")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Serie temporali: cumulate di pioggia (mm) e sintesi del vento (m/s)
#
# PERCHE' SERVE (misurato il 2026-11-23, non ipotizzato)
# --------------------------------------------------------------------------
# `_latest_value` prende UNA sola osservazione. Per un accumulo da 60 s quella
# osservazione copre 60 s su 3600 (1,7% della finestra): una cumulata oraria
# costruita cosi' sarebbe geograficamente falsa. L'API restituisce invece la
# SERIE: 166-181 valori a 60 s su 3 h (dpcn-sicilia/piemonte/lazio/puglia), 35
# a 5 min (dpcn-liguria), 12 a 15 min (dpcn-marche), 1 a 60 min (mnw).
#
# SEMANTICA DEL `ref` (misurata, non assunta)
# -------------------------------------------
# `ref` e' la FINE dell'intervallo dell'accumulo: `[ref + P2 - P3, ref + P2]`.
# Prova: su 20 finestre da 3 h (5 giornate, 2026-11-17..21) le stazioni con due
# prodotti dello stesso B13011 (900 s e 3600 s) danno la somma dei quattro
# quarti d'ora uguale al valore orario in **1.634 casi NON ambigui con la
# lettura "ref = fine" e in 0 casi con "ref = inizio"**. Fisicamente e' anche
# l'unica lettura possibile: un accumulo e' riferito all'istante di osservazione
# e copre il periodo PRECEDENTE (la 1.3600 a ref 12:00 copre 11:00-12:00).
REF_IS_INTERVAL_END = True

# Gate fisici (SCELTE INGEGNERISTICHE, dichiarate nei campi `derived_fields`).
# Non sono soglie meteorologiche: servono a non pubblicare record rotti. Il caso
# reale che li motiva e' `dpcn-sicilia:37.90806,15.14528` (Francavilla di
# Sicilia): 126,6 mm in 60 s = 7.596 mm/h, cioe' 126 mm in un minuto.
QC_RAIN_MAX_RATE_MM_H = 300.0   # oltre questo il punto di pioggia e' scartato
QC_WIND_MAX_MS = 60.0           # vento medio oltre questo: record rotto
QC_GUST_MAX_MS = 75.0           # raffica oltre questo: record rotto
RAIN_MIN_COVERAGE_FRAC = 0.5    # sotto questa copertura la cumulata e' null
WIND_MIN_COVERAGE_FRAC = 0.5    # idem per media/massimo del vento
GUST_MIN_INSIDE_FRAC = 0.5      # un massimo orario entra solo se e' per meta' dentro

RAIN_WINDOWS = (("1h", 3600), ("3h", 10800))
# Tipi di statistica del trange (misurati, vedi `trange_stat_type`).
STAT_MIN = 0
STAT_SUM = 1
STAT_MAX = 2
STAT_AVG = 3
STAT_INSTANT = 254
WIND_WINDOWS = (("1h", 3600), ("3h", 10800))
HOURLY_BUCKETS = 4              # ore pubblicate per i bucket (serie da 3 h)

COVERAGE_BELOW_REASON = "coverage_below_50pct"

# --- Grandezze ISTANTANEE: temperatura, umidita' relativa, dew point --------
# Stessa regola del vento: media PESATA SUL TEMPO (sample-and-hold), minimo e
# massimo dei campioni osservati nella finestra. I gate fisici non sono soglie
# meteorologiche: servono a non pubblicare record rotti (la serie reale contiene
# 99 degC e RH 150% da sensori guasti) e ogni punto scartato viene contato.
QC_TEMP_MAX_ABS_C = 60.0        # |T| > 60 degC -> punto scartato
# Gate di PLAUSIBILITA' FISICA, separato dal range assoluto: un valore di 52 degC
# e' dentro |T| <= 60 ma e' impossibile se un'ora prima la stazione era a 25.
# SCELTA INGEGNERISTICA dichiarata (vedi `temp_qc.py`): si scarta un campione se
# |dT|/dt > 6 degC/h rispetto all'ULTIMO CAMPIONE ACCETTATO. In Italia un fohn
# fa +4-5 degC/h; 6 lascia margine agli eventi reali e taglia i salti da 15-37
# degC in 30-60 minuti misurati su Palma di Montechiaro il 2026-11-23.
QC_TEMP_MAX_RATE_C_PER_H = TEMP_MAX_RATE_C_PER_H
QC_DEW_MAX_ABS_C = 60.0
QC_RH_MIN_PCT = 0.0
QC_RH_MAX_PCT = 100.0
INSTANT_MIN_COVERAGE_FRAC = 0.5

# Finestre delle grandezze istantanee. MISURATO il 2026-11-23 sul payload reale
# (4.938 stazioni, 19,9 MB JSON): le serie istantanee hanno passi mediani
# 60 s (147 stazioni: puglia 129, basilicata 11, campania 7), 300 s (19: toscana
# 16, boa 1, sicilia 1, spdsra 1), 600 s (915), 900 s, 1800 s e 3600 s.
# Le finestre CORTE hanno un gate sul PASSO della serie: una media su 5 min da
# campioni ogni 15 min non e' una media su 5 min, e' un valore tenuto per 15.
THERMAL_WINDOWS = (("5m", 300), ("30m", 1800), ("1h", 3600), ("3h", 10800))
SHORT_WINDOW_MAX_STEP_S = {"5m": 60, "30m": 300}
COARSE_REASON = {
    "5m": "series_too_coarse_for_5min",
    "30m": "series_too_coarse_for_30min",
}
# Vento a 30 min: stessa regola per la media; per la raffica vale il periodo
# DICHIARATO dalla sorgente (un massimo di 15 min sta dentro una finestra di 30,
# un massimo orario no).
WIND_30M_WINDOW = ("30m", 1800)


def _accum_interval(ref: datetime, period: int, offset: int) -> tuple[datetime, datetime]:
    """Intervallo di un accumulo/estremo con `ref` = FINE (vedi REF_IS_INTERVAL_END)."""
    end = ref + timedelta(seconds=offset)
    if REF_IS_INTERVAL_END:
        return end - timedelta(seconds=period), end
    return end, end + timedelta(seconds=period)


def _series_intervals(
    values: list[tuple[datetime, float]],
    period: int,
    offset: int,
) -> list[tuple[datetime, datetime, int, float]]:
    """[(inizio, fine, periodo, valore)] di un prodotto con periodo noto."""
    out = []
    for ref, value in values:
        start, end = _accum_interval(ref, period, offset)
        out.append((start, end, period, value))
    return out


def _instant_intervals(
    values: list[tuple[datetime, float]],
    now: datetime,
) -> list[tuple[datetime, datetime, int, float]]:
    """[(inizio, fine, 0, valore)] di una serie ISTANTANEA (trange 254,0,0).

    Un'osservazione istantanea vale dal suo `ref` fino al `ref` successivo
    (sample-and-hold): e' l'unico modo di pesare una media su una serie
    irregolare senza inventare un passo. L'ultimo campione vale fino a
    `ref + passo mediano`, mai oltre `now`; con `now` coincidente col `ref` la
    sua durata e' zero e non entra (non si inventa futuro).
    """
    points = sorted(values, key=lambda x: x[0])
    if not points:
        return []
    gaps = sorted(
        int((b[0] - a[0]).total_seconds()) for a, b in zip(points, points[1:])
    )
    step = gaps[len(gaps) // 2] if gaps else 0
    if step <= 0 or step > 7200:
        step = 0
    out = []
    for index, (ref, value) in enumerate(points):
        if index + 1 < len(points):
            end = points[index + 1][0]
        else:
            end = min(ref + timedelta(seconds=step), now) if step else ref
        # Un campione con `ref == now` vale zero secondi: l'intervallo resta in
        # lista (a zero) perche' il MASSIMO deve vederlo (`_clip` lo scarta per
        # la media, la lista dei campioni no).
        out.append((ref, max(end, ref), 0, value))
    return out


def _clip(
    intervals: list[tuple[datetime, datetime, int, float]],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime, int, float]]:
    out = []
    for start, end, period, value in intervals:
        left = max(start, window_start)
        right = min(end, window_end)
        if right > left:
            out.append((left, right, period, value))
    out.sort(key=lambda item: item[0])
    return out


def _segments(
    clipped: list[tuple[datetime, datetime, int, float]],
) -> list[tuple[datetime, datetime, float, int]]:
    """Segmenti elementari con il valore di UN solo campione (il piu' fine).

    Perche' non `somma(valore * sovrapposizione)` per campione: due campioni che
    si sovrappongono (la stessa stazione pubblicata due volte, oppure un prodotto
    con `ref` duplicati) verrebbero contati DUE volte. Lo sweep assegna ogni
    istante a un solo campione; a parita' di copertura vince il periodo piu'
    fine, poi il campione piu' recente.
    """
    if not clipped:
        return []
    positions = sorted({item[0] for item in clipped} | {item[1] for item in clipped})
    active: list[tuple[datetime, datetime, int, float]] = []
    cursor = 0
    segments: list[tuple[datetime, datetime, float, int]] = []
    for left, right in zip(positions, positions[1:]):
        while cursor < len(clipped) and clipped[cursor][0] <= left:
            active.append(clipped[cursor])
            cursor += 1
        active = [item for item in active if item[1] > left]
        if not active:
            continue
        chosen = min(active, key=lambda item: (item[2], -item[0].timestamp()))
        segments.append((left, right, chosen[3], chosen[2]))
    return segments


def _window_stats(
    intervals: list[tuple[datetime, datetime, int, float]],
    window_start: datetime,
    window_end: datetime,
    metric: str,
) -> tuple[float | None, float, float]:
    """(valore, secondi coperti, frazione coperta) su [window_start, window_end].

    metric: "sum" (cumulata), "mean" (media pesata sul tempo), "max".
    """
    window_s = (window_end - window_start).total_seconds()
    if window_s <= 0:
        return None, 0.0, 0.0
    segments = _segments(_clip(intervals, window_start, window_end))
    covered = sum((right - left).total_seconds() for left, right, _, _ in segments)
    frac = covered / window_s
    if not segments:
        return None, 0.0, 0.0
    if metric == "sum":
        # il valore e' l'accumulo del periodo del campione: mm * durata/periodo
        total = 0.0
        for left, right, value, period in segments:
            if period <= 0:
                continue
            total += value * (right - left).total_seconds() / period
        return total, covered, frac
    if metric == "mean":
        weighted = sum(
            value * (right - left).total_seconds()
            for left, right, value, _ in segments
        )
        return (weighted / covered if covered else None), covered, frac
    return max(value for _, _, value, _ in segments), covered, frac


def _window_stats_single(
    intervals: list[tuple[datetime, datetime, int, float]],
    window_start: datetime,
    window_end: datetime,
) -> tuple[float | None, float, float]:
    """Media pesata in UNA passata, per gli intervalli di UN SOLO prodotto.

    `_segments` esiste per decidere chi vince quando due prodotti si
    sovrappongono; dentro un solo prodotto gli intervalli sono consecutivi e non
    si sovrappongono, quindi lo sweep con set/sort e' costo puro. MISURATO il
    2026-11-23 sul payload reale: e' il percorso di ~8.000 stazioni x 4 finestre
    e con questa variante il tempo si riduce di oltre la meta'.
    """
    window_s = (window_end - window_start).total_seconds()
    if window_s <= 0:
        return None, 0.0, 0.0
    covered = 0.0
    weighted = 0.0
    for start, end, _period, value in intervals:
        left = start if start > window_start else window_start
        right = end if end < window_end else window_end
        if right > left:
            seconds = (right - left).total_seconds()
            covered += seconds
            weighted += value * seconds
    if covered <= 0:
        return None, 0.0, 0.0
    return weighted / covered, covered, covered / window_s


def _best_single(intervals, window_start, window_end, label):
    """Dizionario nello stesso formato di `_best_product` per un solo prodotto."""
    value, covered, frac = _window_stats_single(
        intervals, window_start, window_end
    )
    return {
        "label": label,
        "period": intervals[0][2] if intervals else 0,
        "value": value,
        "covered": covered,
        "frac": frac,
        "intervals": intervals,
    }


def _window_max(
    intervals: list[tuple[datetime, datetime, int, float]],
    window_start: datetime,
    window_end: datetime,
    min_inside: float = GUST_MIN_INSIDE_FRAC,
) -> tuple[float | None, float, float]:
    """Massimo dei campioni il cui intervallo e' ALMENO `min_inside` nella finestra.

    Perche' non "ogni campione che tocca la finestra": una raffica oraria
    `2,0,3600` che sborda di 20 minuti porta dentro un massimo che potrebbe
    essere stato raggiunto FUORI dalla finestra; con la regola del 50% il numero
    resta una stima inferiore onesta e la copertura lo dichiara.
    """
    window_s = (window_end - window_start).total_seconds()
    if window_s <= 0:
        return None, 0.0, 0.0
    kept = []
    for start, end, period, value in intervals:
        left = max(start, window_start)
        right = min(end, window_end)
        if right <= left:
            continue
        if (right - left).total_seconds() / (end - start).total_seconds() < min_inside:
            continue
        kept.append((left, right, value))
    if not kept:
        return None, 0.0, 0.0
    kept.sort(key=lambda item: item[0])
    covered = 0.0
    current_start, current_end = kept[0][0], kept[0][1]
    for left, right, _ in kept[1:]:
        if left <= current_end:
            current_end = max(current_end, right)
        else:
            covered += (current_end - current_start).total_seconds()
            current_start, current_end = left, right
    covered += (current_end - current_start).total_seconds()
    return max(item[2] for item in kept), covered, covered / window_s


def _data_end(
    products: dict[str, list[tuple[datetime, datetime, int, float]]],
    now: datetime,
) -> datetime | None:
    """Fine dell'ultimo dato disponibile, mai oltre `now` (niente futuro)."""
    ends = [item[1] for intervals in products.values() for item in intervals]
    if not ends:
        return None
    return min(max(ends), now)


def _period_seconds(trange: str) -> int | None:
    """P3 del trange, per QUALSIASI tipo di statistica (non solo le somme)."""
    parts = (trange or "").split(",")
    if len(parts) != 3:
        return None
    try:
        period = int(parts[2])
    except ValueError:
        return None
    return period if period > 0 else None


def _evaluate(
    intervals: list[tuple[datetime, datetime, int, float]],
    window_start: datetime,
    window_end: datetime,
    metric: str,
) -> tuple[float | None, float, float]:
    if metric == "max":
        return _window_max(intervals, window_start, window_end)
    return _window_stats(intervals, window_start, window_end, metric)


def _median_step(points: list[datetime]) -> int | None:
    """Passo mediano (s) di una serie istantanea, o None se non utilizzabile."""
    ordered = sorted(points)
    gaps = sorted(
        int((b - a).total_seconds()) for a, b in zip(ordered, ordered[1:])
    )
    if not gaps:
        return None
    step = gaps[len(gaps) // 2]
    if step <= 0 or step > 7200:
        return None
    return step


def _series_step_s(
    trange: str, points: list[tuple[datetime, float]]
) -> int | None:
    """Passo EFFETTIVO di un prodotto: mediano per l'istantaneo, P3 per gli altri.

    Il `period_s` di un prodotto istantaneo e' 0: non e' un passo. Per decidere se
    una finestra di 5 min e' calcolabile serve la distanza fra i campioni, non il
    periodo di integrazione (che per un istantaneo non esiste).
    """
    if trange_stat_type(trange) == STAT_INSTANT:
        return _median_step([ref for ref, _ in points])
    return _period_seconds(trange)


def _thermal_units() -> dict:
    """Unita' dichiarate dei campi termici, generate dai nomi (una sola fonte)."""
    units: dict[str, str] = {}
    for name, suffix, unit in (
        ("temp", "c", "degC"),
        ("rh", "pct", "%"),
        ("dew", "c", "degC"),
    ):
        for window, _seconds in THERMAL_WINDOWS:
            units[f"{name}_mean_{window}_{suffix}"] = unit
            units[f"{name}_{window}_coverage_frac"] = "1"
        units[f"{name}_series_step_s"] = "s"
        for stat in ("min", "max"):
            if name == "rh" or (name == "dew" and stat == "max"):
                continue
            for window, _seconds in THERMAL_WINDOWS:
                units[f"{name}_{stat}_{window}_{suffix}"] = unit
        units[f"{name}_hourly"] = unit
    return units



def _best_product(
    products: dict[str, list[tuple[datetime, datetime, int, float]]],
    window_start: datetime,
    window_end: datetime,
    metric: str,
) -> dict | None:
    """Prodotto con la copertura MIGLIORE sulla finestra (poi il periodo piu' fine).

    E' la regola richiesta ed e' quella giusta: con "vince il `ref` piu' recente"
    un prodotto da 60 s batte sempre uno da 900 s anche se la sua serie ha buchi
    e quella da 900 s e' completa. La copertura e' la grandezza che conta.
    """
    best = None
    for label in sorted(products):
        intervals = products[label]
        value, covered, frac = _evaluate(intervals, window_start, window_end, metric)
        period = intervals[0][2] if intervals else 0
        key = (round(frac, 6), -period)
        if best is None or key > best["key"]:
            best = {
                "key": key,
                "label": label,
                "period": period,
                "value": value,
                "covered": covered,
                "frac": frac,
                "intervals": intervals,
            }
    return best


def _hourly_pairs(
    intervals: list[tuple[datetime, datetime, int, float]],
    data_end: datetime,
    metric: str,
    hours: int = HOURLY_BUCKETS,
    min_coverage: float = RAIN_MIN_COVERAGE_FRAC,
    single: bool = False,
) -> list[list]:
    """[[epoch_s dell'ora, valore], ...] per le ultime `hours` ore.

    Un'ora entra solo se e' coperta almeno al 50%: l'ora CORRENTE e' parziale per
    definizione, quindi per lei il denominatore e' il tempo TRASCORSO (altrimenti
    alle 12:10 nessuna ora corrente potrebbe mai entrare). Per le ore passate il
    denominatore e' l'ora intera, cosi' la prima ora della serie (tagliata a
    sinistra) viene scartata invece di essere spacciata per completa.
    """
    last_hour = data_end.replace(minute=0, second=0, microsecond=0)
    rows: list[list] = []
    for offset in range(hours - 1, -1, -1):
        start = last_hour - timedelta(hours=offset)
        full_end = start + timedelta(hours=1)
        end = min(full_end, data_end)
        if end <= start:
            continue
        if single:
            value, covered, _ = _window_stats_single(intervals, start, end)
        else:
            value, covered, _ = _evaluate(intervals, start, end, metric)
        denominator = (
            (full_end - start).total_seconds()
            if offset > 0
            else (end - start).total_seconds()
        )
        if value is None or denominator <= 0:
            continue
        if covered / denominator < min_coverage:
            continue
        rows.append([int(start.timestamp()), _round(value, 1)])
    return rows


def _hourly_triplet(
    intervals: list[tuple[datetime, datetime, int, float]],
    data_end: datetime,
    hours: int = HOURLY_BUCKETS,
    min_coverage: float = INSTANT_MIN_COVERAGE_FRAC,
    single: bool = False,
) -> list[list]:
    """[[epoch_s, media, minimo, massimo], ...] delle ultime `hours` ore.

    La temperatura ha bisogno di TRE numeri per ora (il bucket `th` della serie
    giornaliera): la media pesata sul tempo e gli estremi dei campioni con `ref`
    nell'ora. Stessa regola di copertura di `_hourly_pairs`: l'ora corrente vale
    per il tempo TRASCORSO, le ore passate per l'ora intera.
    """
    last_hour = data_end.replace(minute=0, second=0, microsecond=0)
    rows: list[list] = []
    for offset in range(hours - 1, -1, -1):
        start = last_hour - timedelta(hours=offset)
        full_end = start + timedelta(hours=1)
        end = min(full_end, data_end)
        if end <= start:
            continue
        if single:
            value, covered, _ = _window_stats_single(intervals, start, end)
        else:
            value, covered, _ = _evaluate(intervals, start, end, "mean")
        denominator = (
            (full_end - start).total_seconds()
            if offset > 0
            else (end - start).total_seconds()
        )
        if value is None or denominator <= 0:
            continue
        if covered / denominator < min_coverage:
            continue
        if single:
            samples = []
            for ref, end_ref, _period, sample in intervals:
                left = ref if ref > start else start
                right = end_ref if end_ref < end else end
                if right > left:
                    samples.append(sample)
                if start <= ref <= end:
                    samples.append(sample)
        else:
            segments = _segments(_clip(intervals, start, end))
            samples = [value for _left, _right, value, _p in segments]
            samples += [
                v for ref, _end, _period, v in intervals if start <= ref <= end
            ]
        if not samples:
            continue
        rows.append(
            [
                int(start.timestamp()),
                _round(value, 1),
                _round(min(samples), 1),
                _round(max(samples), 1),
            ]
        )
    return rows


def build_rain_block(
    series: dict[str, list[tuple[datetime, float]]],
    now: datetime,
) -> tuple[dict, dict]:
    """Campi `rain_*` di una stazione dalle serie di accumulo B13011.

    Ritorna ({}, stats) quando la stazione non ha NESSUN prodotto di accumulo:
    in quel caso non si scrivono campi (campo assente = grandezza non
    disponibile, come per `visibility`), non si scrivono null finti.

    Regole, tutte dichiarate in `derived_fields` del documento:
      * `rain_<W>_mm` = integrale su [fine - W, fine], dove `fine` e' l'ultimo
        dato disponibile della stazione (mai oltre `now`): con la latenza
        mediana della sorgente (~32 min) ancorare a `now` darebbe < 50% di
        copertura a quasi tutte le stazioni e la cumulata sarebbe sempre null.
      * prodotto scelto per COPERTURA, a parita' il periodo piu' fine;
      * gate fisico: rate istantaneo > 300 mm/h o valore negativo -> punto
        scartato e contato in `rain_qc_rejected`.
    """
    rejected = [0]
    products: dict[str, list[tuple[datetime, datetime, int, float]]] = {}
    for trange, values in series.items():
        if trange_stat_type(trange) != STAT_SUM:
            continue
        period = _period_seconds(trange)
        if period is None:
            continue
        offset = trange_offset_seconds(trange) or 0
        intervals = []
        for ref, value in values:
            rate = value * 3600.0 / period
            if value < 0 or rate > QC_RAIN_MAX_RATE_MM_H:
                rejected[0] += 1
                continue
            start, end = _accum_interval(ref, period, offset)
            intervals.append((start, end, period, value))
        if intervals:
            products[trange] = intervals
    stats = {"points_qc_rejected": rejected[0]}
    if not products:
        return {}, stats
    data_end = _data_end(products, now)
    if data_end is None:
        return {}, stats

    fields: dict = {}
    chosen: dict[str, dict] = {}
    for name, seconds in RAIN_WINDOWS:
        start = data_end - timedelta(seconds=seconds)
        best = _best_product(products, start, data_end, "sum")
        if best is None:
            continue
        chosen[name] = best
        fields[f"rain_{name}_coverage_frac"] = _round(best["frac"], 3)
        if best["value"] is not None and best["frac"] >= RAIN_MIN_COVERAGE_FRAC:
            fields[f"rain_{name}_mm"] = _round(best["value"], 1)
        else:
            fields[f"rain_{name}_mm"] = None
            fields[f"rain_{name}_null_reason"] = COVERAGE_BELOW_REASON
    if not chosen:
        return {}, stats

    primary = chosen.get("1h") or chosen.get("3h")
    fields["rain_period_s_used"] = int(primary["period"])
    fields["rain_source_product"] = primary["label"]
    wide = chosen.get("3h")
    if wide is not None and wide["label"] != primary["label"]:
        # il prodotto migliore delle due finestre puo' non essere lo stesso:
        # si dichiara invece di far credere che il periodo sia unico.
        fields["rain_3h_period_s"] = int(wide["period"])
        fields["rain_3h_product"] = wide["label"]
    fields["rain_window_end"] = data_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    hourly_source = (wide or primary)["intervals"]
    hourly = _hourly_pairs(hourly_source, data_end, "sum")
    if hourly:
        fields["rain_hourly"] = hourly
    return fields, stats


def build_wind_block(
    speed_series: dict[str, list[tuple[datetime, float]]],
    gust_series: dict[str, list[tuple[datetime, float]]],
    now: datetime,
    last_speed: float | None = None,
    last_gust: float | None = None,
    last_gust_period_s: int | None = None,
) -> tuple[dict, dict]:
    """Campi del VENTO: raffica massima, vento medio, massimo del vento medio.

    Il vento NON si accumula: sommare velocita' non ha senso fisico. Le
    grandezze pubblicate sono quindi un MASSIMO e una MEDIA pesata sul tempo.

    Sorgenti possibili nella serie (misurate il 2026-11-23 su 5.097 record):
      * B11002 `254,0,0` istantaneo -> la serie esiste (1.716 prodotti);
      * B11002 `3,0,N` media, `0,0,N` minimo, `200,0,N` tipo non documentato;
      * B11041 `2,0,N` raffica = massimo.
    Si usano SOLO i tipi che hanno un senso fisico per la grandezza: media e
    massimo non si mescolano (la media di un massimo non e' una media e il
    massimo di una media non e' un massimo). Il tipo `200` non e' documentato
    qui e viene IGNORATO: meglio perdere risoluzione che indovinare.

    Senza serie (o con serie che non copre) si ripiega sull'ultimo valore
    (`last_speed`/`last_gust`) marcato `wind_source = "latest_only"`: e' quello
    che il file faceva gia' con `wind_speed_10m`/`wind_gust_10m`, reso esplicito.
    """
    stats = {"points_qc_rejected": 0, "ignored_stats": []}

    def _products(raw_series, allowed_stats, max_value):
        products = {}
        steps = {}
        for trange, values in raw_series.items():
            stat = trange_stat_type(trange)
            if stat not in allowed_stats:
                if trange not in stats["ignored_stats"]:
                    stats["ignored_stats"].append(trange)
                continue
            if stat == STAT_INSTANT:
                intervals = _instant_intervals(values, now)
            else:
                period = _period_seconds(trange)
                if period is None:
                    continue
                offset = trange_offset_seconds(trange) or 0
                intervals = [
                    (*_accum_interval(ref, period, offset), period, value)
                    for ref, value in values
                ]
            kept = []
            for start, end, period, value in intervals:
                if value < 0 or value > max_value:
                    stats["points_qc_rejected"] += 1
                    continue
                kept.append((start, end, period, value))
            if kept:
                if stat == STAT_INSTANT:
                    step = _median_step([start for start, _e, _p, _v in kept])
                else:
                    step = _period_seconds(trange)
                if step is None:
                    continue
                products[trange] = kept
                steps[trange] = step
        return products, steps

    speed_products, speed_steps = _products(
        speed_series, (STAT_INSTANT, STAT_AVG), QC_WIND_MAX_MS
    )
    gust_products, gust_steps = _products(
        gust_series, (STAT_MAX,), QC_GUST_MAX_MS
    )

    def _pick(products, window_start, window_end, metric):
        """Istantaneo se copre >= 50%, altrimenti il migliore per copertura.

        Perche' non sempre "copertura migliore": il massimo su una finestra deve
        essere >= di ogni valore osservato nella finestra. Mescolando medie
        orarie (`3,0,3600`) e istantanei (`254,0,0`) quell'invariante CADE:
        misurato il 2026-11-23, 753 confronti su 1.494 davano il massimo delle
        medie MINORE dell'ultimo istantaneo (arpafvg 2,9 m/s contro 0,9). Quindi
        si preferisce la serie istantanea quando e' utilizzabile, e si ripiega
        sulle medie solo quando l'istantanea non copre.
        """
        instant = {
            label: intervals
            for label, intervals in products.items()
            if trange_stat_type(label) == STAT_INSTANT
        }
        if instant:
            best = _best_product(instant, window_start, window_end, metric)
            if best is not None and best["frac"] >= WIND_MIN_COVERAGE_FRAC:
                return best
        return _best_product(products, window_start, window_end, metric)

    fields: dict = {}
    speed_end = _data_end(speed_products, now)
    if speed_products and speed_end is not None:
        wide_start = speed_end - timedelta(seconds=dict(WIND_WINDOWS)["3h"])
        for name, seconds in WIND_WINDOWS:
            start = speed_end - timedelta(seconds=seconds)
            best = _pick(speed_products, start, speed_end, "mean")
            if best is None:
                continue
            fields[f"wind_mean_{name}_coverage_frac"] = _round(best["frac"], 3)
            if best["value"] is not None and best["frac"] >= WIND_MIN_COVERAGE_FRAC:
                fields[f"wind_mean_{name}_ms"] = _round(best["value"], 1)
            else:
                fields[f"wind_mean_{name}_ms"] = None
                fields[f"wind_mean_{name}_null_reason"] = COVERAGE_BELOW_REASON
        wide = _pick(speed_products, wide_start, speed_end, "mean")
        if (
            wide is not None
            and wide["frac"] >= WIND_MIN_COVERAGE_FRAC
            and trange_stat_type(wide["label"]) == STAT_INSTANT
        ):
            # Il massimo si pubblica SOLO da una serie ISTANTANEA: il massimo di
            # una serie di MEDIE orarie non e' "il massimo del vento" e romperebbe
            # l'invariante `wind_max_3h_ms >= wind_speed_10m` (misurato il
            # 2026-11-23: 753 casi su 1.494). Il criterio e' costruttivo, non un
            # ritocco a posteriori del numero.
            # Massimo dei CAMPIONI con `ref` nella finestra: cosi' entra anche
            # l'ultimo istantaneo, che nei segmenti ha durata zero e sparirebbe.
            samples = [
                value
                for start, _end, _period, value in speed_products[wide["label"]]
                if wide_start <= start <= speed_end
            ]
            peak = max(samples) if samples else None
            if peak is not None:
                fields["wind_max_3h_ms"] = _round(peak, 1)
        first = _pick(
            speed_products,
            speed_end - timedelta(seconds=dict(WIND_WINDOWS)["1h"]),
            speed_end,
            "mean",
        )
        if first is not None:
            fields["wind_period_s"] = int(first["period"])
            fields["wind_source_product"] = first["label"]
        fields["wind_source"] = "series"
        fields["wind_window_end"] = speed_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        hourly_source = (wide or first)["intervals"]
        hourly = _hourly_pairs(hourly_source, speed_end, "mean")
        if hourly:
            fields["wind_hourly"] = hourly
        # Finestra da 30 min: stessa regola del passo delle grandezze termiche
        # (300 s). MISURATO il 2026-11-23: la serie istantanea ha passo 60 s su
        # 147 stazioni, 300 s su 19, 600 s su 915 (che quindi NON hanno la 30m).
        start30 = speed_end - timedelta(seconds=WIND_30M_WINDOW[1])
        eligible30 = {
            label: intervals
            for label, intervals in speed_products.items()
            if speed_steps.get(label) is not None
            and speed_steps[label] <= SHORT_WINDOW_MAX_STEP_S["30m"]
        }
        if eligible30:
            best30 = _pick(eligible30, start30, speed_end, "mean")
            if best30 is not None:
                fields["wind_mean_30m_coverage_frac"] = _round(best30["frac"], 3)
                if (
                    best30["value"] is not None
                    and best30["frac"] >= WIND_MIN_COVERAGE_FRAC
                ):
                    fields["wind_mean_30m_ms"] = _round(best30["value"], 1)
                else:
                    fields["wind_mean_30m_ms"] = None
                    fields["wind_mean_30m_null_reason"] = COVERAGE_BELOW_REASON
        else:
            fields["wind_mean_30m_ms"] = None
            fields["wind_mean_30m_null_reason"] = COARSE_REASON["30m"]
        best_frac = max(
            fields.get("wind_mean_1h_coverage_frac") or 0.0,
            fields.get("wind_mean_3h_coverage_frac") or 0.0,
        )
        if best_frac <= 0.0:
            # La serie esiste ma non copre NULLA (tipico di `mnw`: un solo
            # campione istantaneo, durata zero). Non si pubblica una serie
            # inutilizzabile: si ripiega sull'ultimo valore, marcato.
            # MISURATO il 2026-11-23: 217 stazioni finivano con serie + null
            # invece del ripiego `latest_only` che il client puo' usare.
            # Una copertura PARZIALE (>0 ma <50%) resta invece null con motivo:
            # li' l'informazione "c'e' ma non basta" e' utile.
            for key in (
                "wind_source_product",
                "wind_period_s",
                "wind_hourly",
                "wind_window_end",
                "wind_mean_1h_coverage_frac",
                "wind_mean_3h_coverage_frac",
                "wind_mean_1h_null_reason",
                "wind_mean_3h_null_reason",
                "wind_mean_1h_ms",
                "wind_mean_3h_ms",
                "wind_mean_30m_ms",
                "wind_mean_30m_coverage_frac",
                "wind_mean_30m_null_reason",
                "wind_max_3h_ms",
            ):
                fields.pop(key, None)
            if last_speed is not None:
                fields["wind_mean_1h_ms"] = _round(last_speed, 1)
                fields["wind_source"] = "latest_only"
            else:
                fields.pop("wind_source", None)
    elif last_speed is not None:
        # Ripiego DICHIARATO: un'osservazione non e' una media su un'ora.
        fields["wind_mean_1h_ms"] = _round(last_speed, 1)
        fields["wind_source"] = "latest_only"

    gust_end = _data_end(gust_products, now)
    if gust_products and gust_end is not None:
        for name, seconds in WIND_WINDOWS:
            start = gust_end - timedelta(seconds=seconds)
            best = _best_product(gust_products, start, gust_end, "max")
            if best is None:
                continue
            fields[f"gust_max_{name}_coverage_frac"] = _round(best["frac"], 3)
            if best["value"] is not None and best["frac"] >= WIND_MIN_COVERAGE_FRAC:
                fields[f"gust_max_{name}_ms"] = _round(best["value"], 1)
            else:
                fields[f"gust_max_{name}_ms"] = None
                fields[f"gust_max_{name}_null_reason"] = COVERAGE_BELOW_REASON
        first = _best_product(
            gust_products,
            gust_end - timedelta(seconds=dict(WIND_WINDOWS)["1h"]),
            gust_end,
            "max",
        )
        if first is not None:
            fields["gust_period_s"] = int(first["period"])
            fields["gust_source_product"] = first["label"]
        wide_g = _best_product(
            gust_products,
            gust_end - timedelta(seconds=dict(WIND_WINDOWS)["3h"]),
            gust_end,
            "max",
        )
        hourly = _hourly_pairs((wide_g or first)["intervals"], gust_end, "max")
        if hourly:
            fields["gust_hourly"] = hourly
        fields["gust_window_end"] = gust_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        start30g = gust_end - timedelta(seconds=WIND_30M_WINDOW[1])
        eligible_g30 = {
            label: intervals
            for label, intervals in gust_products.items()
            if gust_steps.get(label) is not None
            and gust_steps[label] <= WIND_30M_WINDOW[1]
        }
        if eligible_g30:
            best_g30 = _best_product(eligible_g30, start30g, gust_end, "max")
            if best_g30 is not None:
                fields["gust_max_30m_coverage_frac"] = _round(best_g30["frac"], 3)
                if (
                    best_g30["value"] is not None
                    and best_g30["frac"] >= WIND_MIN_COVERAGE_FRAC
                ):
                    fields["gust_max_30m_ms"] = _round(best_g30["value"], 1)
                else:
                    fields["gust_max_30m_ms"] = None
                    fields["gust_max_30m_null_reason"] = COVERAGE_BELOW_REASON
        else:
            # Un massimo orario (2,0,3600) non e' il massimo di 30 minuti.
            fields["gust_max_30m_ms"] = None
            fields["gust_max_30m_null_reason"] = COARSE_REASON["30m"]
    elif last_gust is not None and last_gust_period_s == 3600:
        # `2,0,3600` e' davvero un massimo ORARIO della sorgente: pubblicarlo
        # come `gust_max_1h_ms` e' corretto. Con `2,0,900` sarebbe un massimo di
        # 15 minuti e NON si pubblica (resta `wind_gust_10m`, dichiarato "non
        # normalizzato" nel suo `derived_fields`).
        fields["gust_max_1h_ms"] = _round(last_gust, 1)
        fields["gust_source"] = "latest_only"
        fields["gust_period_s"] = 3600

    if not fields:
        stats.pop("points_qc_rejected", None)
    return fields, stats


def dew_point_c(temp_c: float, rh_pct: float) -> float | None:
    """Punto di rugiada con Magnus (Alduchov & Eskridge 1996).

    gamma = ln(RH/100) + a*T/(b+T);  Td = b*gamma/(a-gamma)
    con a = 17.625, b = 243.04 degC. Vale per RH > 0 e T nella norma.
    """
    if rh_pct is None or rh_pct <= 0:
        return None
    gamma = math.log(rh_pct / 100.0) + (17.625 * temp_c) / (243.04 + temp_c)
    denom = 17.625 - gamma
    if denom == 0:
        return None
    return 243.04 * gamma / denom


def build_dew_series(
    temp_series: dict[str, list[tuple[datetime, float]]],
    rh_series: dict[str, list[tuple[datetime, float]]],
) -> tuple[dict[str, list[tuple[datetime, float]]], int]:
    """Serie del dew point calcolata SOLO su campioni con lo stesso `ref`.

    Perche' non interpolare: una coppia T/RH presa da istanti diversi descrive
    un'altra aria. MISURATO il 2026-11-23: per tutte le 1.136 stazioni con
    entrambe le serie istantanee i `ref` coincidono ESATTAMENTE (0 casi di
    sovrapposizione solo approssimata), quindi l'intersezione non perde nulla.

    Il gate fisico e' applicato PRIMA della coppia: un T=99 degC o una RH=150%
    non devono generare un dew point credibile. Ritorna (serie, punti_scartati)
    dove il contatore riguarda SOLO i dew point fuori gate/non calcolabili: i
    T/RH fuori gate sono gia' contati dai rispettivi blocchi.
    """
    out: dict[str, list[tuple[datetime, float]]] = {}
    rejected = 0
    for trange, tpoints in temp_series.items():
        rpoints = rh_series.get(trange)
        if not rpoints:
            continue
        by_ref = {ref: value for ref, value in rpoints}
        kept: list[tuple[datetime, float]] = []
        for ref, temp_k in tpoints:
            rh = by_ref.get(ref)
            if rh is None:
                continue
            temp_c = temp_k - 273.15
            if abs(temp_c) > QC_TEMP_MAX_ABS_C:
                continue
            if not (QC_RH_MIN_PCT <= rh <= QC_RH_MAX_PCT):
                continue
            dew = dew_point_c(temp_c, rh)
            if dew is None or not math.isfinite(dew) or abs(dew) > QC_DEW_MAX_ABS_C:
                rejected += 1
                continue
            kept.append((ref, dew))
        if kept:
            out[trange] = kept
    return out, rejected


def build_instant_block(
    name: str,
    series: dict[str, list[tuple[datetime, float]]],
    now: datetime,
    *,
    unit: str,
    decimals: int = 1,
    extremes: tuple[str, ...] = (),
    scale: float = 1.0,
    offset: float = 0.0,
    qc_min: float = -math.inf,
    qc_max: float = math.inf,
) -> tuple[dict, dict]:
    """Campi di una grandezza ISTANTANEA: media pesata, minimo, massimo.

    `name` = `temp` / `rh` / `dew`; `unit` = suffisso del campo (`c`, `pct`).
    Le finestre sono `THERMAL_WINDOWS`: 5m, 30m, 1h, 3h.

    Regole, tutte dichiarate in `derived_fields`:
      * media = sample-and-hold pesato sul tempo (`_instant_intervals`), come
        `wind_mean_*`; un campione vale dal suo `ref` al successivo;
      * minimo/massimo = estremi dei CAMPIONI con `ref` nella finestra, SOLO da
        una serie istantanea (il minimo di medie orarie non e' un minimo);
      * copertura >= 50% della finestra, altrimenti null con motivo;
      * 5m richiede passo <= 60 s, 30m <= 300 s: sotto quella risoluzione la
        finestra non e' calcolabile e il motivo e' `series_too_coarse_for_*`.
    """
    stats = {"points_qc_rejected": 0, "ignored_stats": []}
    products: dict[str, list[tuple[datetime, datetime, int, float]]] = {}
    steps: dict[str, int] = {}
    for trange, values in series.items():
        stat = trange_stat_type(trange)
        if stat not in (STAT_INSTANT, STAT_AVG):
            if trange not in stats["ignored_stats"]:
                stats["ignored_stats"].append(trange)
            continue
        converted: list[tuple[datetime, float]] = []
        for ref, value in values:
            converted_value = float(value) * scale + offset
            if (
                not math.isfinite(converted_value)
                or converted_value < qc_min
                or converted_value > qc_max
            ):
                stats["points_qc_rejected"] += 1
                continue
            converted.append((ref, converted_value))
        if not converted:
            continue
        step = _series_step_s(trange, converted)
        if step is None:
            continue
        if stat == STAT_INSTANT:
            intervals = _instant_intervals(converted, now)
        else:
            period = _period_seconds(trange)
            if period is None:
                continue
            trange_offset = trange_offset_seconds(trange) or 0
            intervals = [
                (*_accum_interval(ref, period, trange_offset), period, value)
                for ref, value in converted
            ]
        if intervals:
            products[trange] = intervals
            steps[trange] = step

    if not products:
        return {}, stats
    data_end = _data_end(products, now)
    if data_end is None:
        return {}, stats

    fields: dict = {}
    chosen: dict[str, dict] = {}
    for window, seconds in THERMAL_WINDOWS:
        start = data_end - timedelta(seconds=seconds)
        limit = SHORT_WINDOW_MAX_STEP_S.get(window)
        eligible = (
            products
            if limit is None
            else {
                label: intervals
                for label, intervals in products.items()
                if steps.get(label) is not None and steps[label] <= limit
            }
        )
        if not eligible:
            # La serie esiste ma e' troppo grossolana per questa finestra: si
            # dichiara il motivo e la media e' null. Non si scrivono copertura,
            # passo e minimo/massimo di una finestra che non esiste.
            fields[f"{name}_mean_{window}_{unit}"] = None
            fields[f"{name}_{window}_null_reason"] = COARSE_REASON[window]
            continue
        def pick(subset):
            if len(subset) == 1:
                label, intervals = next(iter(subset.items()))
                return _best_single(intervals, start, data_end, label)
            return _best_product(subset, start, data_end, "mean")

        best = None
        instant_products = {
            label: intervals
            for label, intervals in eligible.items()
            if trange_stat_type(label) == STAT_INSTANT
        }
        if instant_products:
            candidate = pick(instant_products)
            if (
                candidate is not None
                and candidate["frac"] >= INSTANT_MIN_COVERAGE_FRAC
            ):
                best = candidate
        if best is None:
            best = pick(eligible)
        if best is None:
            continue
        chosen[window] = best
        fields[f"{name}_{window}_coverage_frac"] = _round(best["frac"], 3)
        usable = (
            best["value"] is not None
            and best["frac"] >= INSTANT_MIN_COVERAGE_FRAC
        )
        if usable:
            fields[f"{name}_mean_{window}_{unit}"] = _round(
                best["value"], decimals
            )
        else:
            fields[f"{name}_mean_{window}_{unit}"] = None
            fields[f"{name}_{window}_null_reason"] = COVERAGE_BELOW_REASON
        if usable and trange_stat_type(best["label"]) == STAT_INSTANT:
            # Min/max dai campioni che VALGONO nella finestra (intervallo
            # sovrapposto) PIU' quelli con `ref` dentro: un campione che inizia
            # prima della finestra e vi vale dentro entra nella media, quindi
            # deve entrare anche negli estremi, altrimenti `min <= mean <= max`
            # cade (misurato: 671 violazioni su 6.906 prima della correzione).
            samples = []
            for ref, end_ref, _period, sample in best["intervals"]:
                left = ref if ref > start else start
                right = end_ref if end_ref < data_end else data_end
                if right > left:
                    samples.append(sample)
                if start <= ref <= data_end:
                    samples.append(sample)
            if samples:
                if "min" in extremes:
                    fields[f"{name}_min_{window}_{unit}"] = (
                        _round(min(samples), decimals) if usable else None
                    )
                if "max" in extremes:
                    fields[f"{name}_max_{window}_{unit}"] = (
                        _round(max(samples), decimals) if usable else None
                    )
    if not chosen:
        return {}, stats

    primary = chosen.get("1h") or chosen.get("3h")
    fields[f"{name}_source_product"] = primary["label"]
    # Passo piu' fine disponibile per la stazione: e' quello che decide se le
    # finestre corte sono calcolabili (vedi `*_null_reason`).
    fields[f"{name}_series_step_s"] = int(min(steps.values()))
    fields[f"{name}_window_end"] = data_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    hourly_source = (chosen.get("3h") or primary)["intervals"]
    if name in ("temp", "rh"):
        hourly = _hourly_triplet(hourly_source, data_end, single=True)
    else:
        hourly = _hourly_pairs(
            hourly_source,
            data_end,
            "mean",
            min_coverage=INSTANT_MIN_COVERAGE_FRAC,
            single=True,
        )
    if hourly:
        fields[f"{name}_hourly"] = hourly
    return fields, stats



def _round(value: float, digits: int):
    """Arrotonda; con digits=0 restituisce un intero (umidita', direzione)."""
    out = round(float(value), digits)
    if out == 0:
        out = 0  # niente -0.0 nei diff
    return int(out) if digits == 0 else out


def _in_limits(field: str, value: float) -> bool:
    lo, hi = LIMITS[field]
    return math.isfinite(value) and lo <= value <= hi


def _latest_value(values: list[dict], cutoff: datetime, counters: dict) -> dict | None:
    """Valore piu' recente non piu' vecchio di `cutoff` (istante minimo).

    Ogni valore scartato per eta' viene contato in `counters['too_old']`: senza
    quel numero non si saprebbe quante osservazioni la finestra ha perso.
    """
    best = None
    for item in values:
        try:
            ref = parse_ref(item["ref"])
        except (KeyError, ValueError):
            continue
        if ref < cutoff:
            counters["too_old"] = counters.get("too_old", 0) + 1
            continue
        if best is None or ref > best[0]:
            best = (ref, item.get("val"))
    if best is None:
        return None
    return {"value": best[1], "ref": best[0]}


def station_id(network: str, lat: float, lon: float, decimals: int) -> str:
    return f"{network}:{lat:.{decimals}f},{lon:.{decimals}f}"


def _assign_ids(stations: list[dict]) -> tuple[int, int]:
    """Assegna `id` sintetici, scalando i decimali finche' non ci sono collisioni.

    Ritorna (decimali usati, n. collisioni che si avrebbero a 4 decimali).

    A 4 decimali le collisioni sono REALI e NON sono duplicati: nel payload del
    2026-10-27 `boa` ha "Cervia Porto" (44.26714, 12.35751) e "Cervia Porto
    radar" (44.26714, 12.35752), cioe' due stazioni distinte a 1,1 m. Fonderle
    perderebbe una stazione, quindi si scende a 5 decimali (~1,1 m) e, se
    servisse, a 6-7. Se nemmeno 7 decimali bastano (coordinate identiche), il
    suffisso `#2`, `#3`... garantisce id univoci senza scartare nessuno.
    """
    nets = [st["_net"] for st in stations]
    probe4 = [
        station_id(net, st["lat"], st["lon"], 4) for net, st in zip(nets, stations)
    ]
    collisions4 = len(probe4) - len(set(probe4))
    decimals = 7
    for candidate in (5, 6, 7):
        ids = [
            station_id(net, st["lat"], st["lon"], candidate)
            for net, st in zip(nets, stations)
        ]
        if len(set(ids)) == len(ids):
            decimals = candidate
            break
    else:
        ids = [
            station_id(net, st["lat"], st["lon"], 7)
            for net, st in zip(nets, stations)
        ]
        seen: dict[str, int] = {}
        unique_ids = []
        for sid in ids:
            seen[sid] = seen.get(sid, 0) + 1
            unique_ids.append(sid if seen[sid] == 1 else f"{sid}#{seen[sid]}")
        ids = unique_ids
    for index, (st, sid) in enumerate(zip(stations, ids)):
        record = {"id": sid}
        record.update(st)
        record.pop("_net", None)
        stations[index] = record
    return decimals, collisions4


def baseline_key(net: object, lat: object, lon: object) -> str:
    """Chiave del blob giornaliero: `<rete>:<lat 5 decimali>,<lon 5 decimali>`.

    E' la stessa forma di `station_id` (`_assign_ids`), senza il suffisso `#N`
    delle collisioni: una collisione a 5 decimali (due stazioni a ~1 m) e' un
    caso raro e per quelle due l'ancora puo' essere quella della vicina, che
    dista meno di un metro. Le stazioni con id a 6 decimali non trovano
    l'ancora: nessun valore inventato, semplicemente restano senza.
    """
    try:
        return f"{net}:{float(lat):.5f},{float(lon):.5f}"
    except (TypeError, ValueError):
        return ""


def load_temperature_baseline(path: Path) -> dict[str, list[tuple[datetime, float]]]:
    """Ultimo tratto di serie ACCETTATO di oggi, dal blob giornaliero.

    PERCHE' SERVE (misurato il 2026-11-23T21:50Z): la finestra della query e'
    180 minuti. Se il sensore si rompe PRIMA dell'inizio della finestra, il
    primo campione della serie e' gia' rotto e un gate relativo non ha un
    valore sano con cui confrontarlo: Palma di Montechiaro alle 19:00Z aveva
    43,1 degC come PRIMO campione della finestra e il gate lo accettava (il
    massimo istantaneo restava 40,5 degC). Il blob giornaliero, che il workflow
    ripristina PRIMA del fetch, contiene la serie della giornata e quindi
    l'ultimo campione sano precedente alla finestra (18:24Z, 14,8 degC).

    Il baseline viene ripulito con lo STESSO gate (`qc_temperature_points`):
    un blob scritto dal codice vecchio contiene ancora i picchi, e usarli come
    ancora propagherebbe l'errore.

    Ritorna `{chiave: [(ref, degC), ...]}` con i soli campioni accettati.
    File assente/illeggibile -> `{}`: il fetcher resta stateless come prima.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    stations = doc.get("stations") if isinstance(doc, dict) else None
    if not isinstance(stations, dict):
        return {}
    out: dict[str, list[tuple[datetime, float]]] = {}
    for sid, record in stations.items():
        if not isinstance(sid, str) or not isinstance(record, dict):
            continue
        points = record.get("pts")
        if not isinstance(points, list):
            continue
        samples: list[tuple[datetime, float]] = []
        for point in points:
            if not isinstance(point, list) or len(point) < 2:
                continue
            timestamp, value = point[0], point[1]
            if not isinstance(timestamp, int) or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                continue
            try:
                ref = datetime.fromtimestamp(timestamp / 1000.0, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                continue
            samples.append((ref, float(value)))
        if not samples:
            continue
        kept, _rejected, _indexes = qc_temperature_points(samples)
        if kept:
            net, _sep, coords = sid.partition(":")
            coords = coords.split("#", 1)[0]
            lat_text, _comma, lon_text = coords.partition(",")
            key = baseline_key(net, lat_text, lon_text)
            if key:
                out[key] = kept
    return out


def normalize_payload(
    payload: dict,
    networks: dict,
    generated_at: datetime,
    max_age_minutes: int = 180,
    temp_baseline: dict[str, list[tuple[datetime, float]]] | None = None,
) -> dict:
    """Trasforma il payload MeteoHub nel documento compatto.

    Regole (tutte dichiarate nel documento di output):
      * solo stazioni di reti con licenza+attribuzione verificate;
      * per ogni grandezza si prende il valore PIU' RECENTE dentro la finestra;
        per temperatura/umidita'/vento si preferisce l'istantanea (trange 254);
      * si scartano i valori piu' vecchi di `max_age_minutes`;
      * un valore fuori range fisico viene scartato e contato, non pubblicato;
      * dedup per stazione: se la stessa (rete, lat, lon) compare due volte,
        vince il valore piu' recente per ogni singola grandezza.
    """
    cutoff = generated_at - timedelta(minutes=max_age_minutes)
    unknown_networks: dict[str, int] = {}
    out_of_range = 0
    fallback_trange = 0
    merged_duplicates = 0
    counters: dict[str, int] = {"too_old": 0}

    # chiave interna = coordinate ESATTE: il dedup non deve fondere due stazioni
    # distinte solo perche' l'id arrotondato coincide.
    cells: dict[tuple, dict] = {}
    meta: dict[tuple, dict] = {}

    # Serie COMPLETE: {chiave stazione: {trange: [(ref, valore)]}}. Si conservano
    # tutti i punti, non solo l'ultimo: e' il difetto che rendeva impossibile una
    # cumulata (un accumulo da 60 s copre l'1,7% di un'ora).
    rain_series: dict[tuple, dict[str, list[tuple[datetime, float]]]] = {}
    speed_series: dict[tuple, dict[str, list[tuple[datetime, float]]]] = {}
    gust_series: dict[tuple, dict[str, list[tuple[datetime, float]]]] = {}
    # Serie ISTANTANEE (temperatura in K, umidita' in %): media/min/max su
    # 5m/30m/1h/3h e hourly per la serie giornaliera. Il dew point si deriva
    # dalle coppie con lo stesso `ref` (vedi `build_dew_series`).
    temp_series: dict[tuple, dict[str, list[tuple[datetime, float]]]] = {}
    rh_series: dict[tuple, dict[str, list[tuple[datetime, float]]]] = {}
    # Periodo dell'ultima raffica (trange `2,0,N`): la sorgente NON la normalizza.
    gust_last_period: dict[tuple, int] = {}
    # contatori dei campi derivati dalle serie (pubblicati in `counts`)
    stations_with_rain_series = 0
    stations_with_rain_1h = 0
    stations_with_rain_3h = 0
    stations_with_wind_series = 0
    stations_with_gust_1h = 0
    stations_with_wind_mean_1h = 0
    stations_with_wind_mean_30m = 0
    stations_with_gust_max_30m = 0
    stations_with_temp_series = 0
    stations_with_rh_series = 0
    stations_with_dew_series = 0
    stations_with_temp_mean_5m = 0
    stations_with_temp_mean_30m = 0
    stations_with_temp_mean_1h = 0
    stations_with_temp_mean_3h = 0
    stations_with_rh_mean_5m = 0
    stations_with_rh_mean_30m = 0
    stations_with_rh_mean_1h = 0
    stations_with_rh_mean_3h = 0
    stations_with_dew_mean_5m = 0
    stations_with_dew_mean_30m = 0
    stations_with_dew_mean_1h = 0
    stations_with_dew_mean_3h = 0
    rain_points_qc_rejected = 0
    wind_points_qc_rejected = 0
    temp_points_qc_rejected = 0
    rh_points_qc_rejected = 0
    dew_points_qc_rejected = 0
    stations_rain_qc_rejected = 0
    stations_wind_qc_rejected = 0
    stations_temp_qc_rejected = 0
    stations_rh_qc_rejected = 0
    stations_dew_qc_rejected = 0

    for entry in payload.get("data") or []:
        stat = entry.get("stat") or {}
        net = stat.get("net")
        lat = stat.get("lat")
        lon = stat.get("lon")
        if net is None or lat is None or lon is None:
            continue
        if net not in networks:
            unknown_networks[net] = unknown_networks.get(net, 0) + 1
            continue

        key = (net, float(lat), float(lon))
        name = None
        for detail in stat.get("details") or []:
            if detail.get("var") == VAR_NAME:
                name = detail.get("val")
                break

        if key in meta:
            merged_duplicates += 1
        fields = cells.setdefault(key, {})
        meta.setdefault(key, {"network": net, "lat": float(lat), "lon": float(lon)})
        if name:
            meta.setdefault(key, {}).setdefault("name", name)

        prods = entry.get("prod") or []

        def collect(code: str, prefer_instant: bool) -> tuple[dict | None, bool]:
            """Valore piu' recente per `code`.

            Con `prefer_instant` si usano le osservazioni istantanee (trange
            254,0,0); se la stazione non ne ha, si usano comunque le medie
            disponibili e il chiamante incrementa il contatore dei fallback.
            """
            candidates = [p for p in prods if p.get("var") == code and p.get("val")]
            if not candidates:
                return None, False
            used_fallback = False
            if prefer_instant:
                instant = [p for p in candidates if p.get("trange") == TRANGE_INSTANT]
                if instant:
                    candidates = instant
                else:
                    used_fallback = True
            best = None
            for prod in candidates:
                latest = _latest_value(prod.get("val") or [], cutoff, counters)
                if latest and (best is None or latest["ref"] > best["ref"]):
                    best = latest
            return best, used_fallback

        temp_hit, temp_fb = collect(VAR_TEMP, True)
        if temp_fb:
            fallback_trange += 1
        rh_hit, _ = collect(VAR_RH, True)
        wspd_hit, _ = collect(VAR_WSPD, True)
        wdir_hit, _ = collect(VAR_WDIR, True)

        # precipitazione: l'accumulo non e' istantaneo, si tiene il periodo.
        precip_hit = None
        precip_period = None
        for prod in prods:
            if prod.get("var") != VAR_PRECIP or not prod.get("val"):
                continue
            period = trange_period_seconds(prod.get("trange", ""))
            if period is None:
                continue
            latest = _latest_value(prod["val"], cutoff, counters)
            if latest and (precip_hit is None or latest["ref"] > precip_hit["ref"]):
                precip_hit = latest
                precip_period = period

        place = {
            "temperature_2m": (temp_hit, 1),
            "relative_humidity_2m": (rh_hit, 0),
            "wind_speed_10m": (wspd_hit, 1),
            "wind_direction_10m": (wdir_hit, 0),
        }
        for field, (hit, digits) in place.items():
            if not hit or not isinstance(hit["value"], (int, float)):
                continue
            value = float(hit["value"])
            if field == "temperature_2m":
                value -= 273.15  # la sorgente e' in kelvin
            if not _in_limits(field, value):
                out_of_range += 1
                continue
            current = fields.get(field)
            if current is None or hit["ref"] > current["ref"]:
                fields[field] = {"value": _round(value, digits), "ref": hit["ref"]}

        if precip_hit and isinstance(precip_hit["value"], (int, float)):
            value = float(precip_hit["value"])
            if _in_limits("precipitation", value):
                current = fields.get("precipitation")
                if current is None or precip_hit["ref"] > current["ref"]:
                    fields["precipitation"] = {
                        "value": _round(value, 1),
                        "ref": precip_hit["ref"],
                    }
                    fields["precipitation_period_s"] = {
                        "value": int(precip_period),
                        "ref": precip_hit["ref"],
                    }
            else:
                out_of_range += 1

        # Campi aggiuntivi REALI: (codice, campo, decimali, livello richiesto, scala).
        # B10004 e' accettato SOLO al livello 1,0,0,0 (pressione di stazione,
        # NON MSL); gli altri non hanno un livello di riduzione. La sorgente
        # non espone visibilita' ne' cloud: non sono in questa tabella.
        extra_specs = (
            (VAR_PRESS, "station_pressure_hPa", 1, PRESS_LEVEL_STATION, 100.0),
            (VAR_GUST, "wind_gust_10m", 1, None, 1.0),
            (VAR_SNOW, "snow_depth_m", 2, None, 1.0),
            (VAR_FRESH_SNOW, "fresh_snow_depth_m", 2, None, 1.0),
            (VAR_RIVER, "river_level_m", 2, None, 1.0),
        )
        for code, field, digits, level, scale in extra_specs:
            candidates = [
                p for p in prods
                if p.get("var") == code and p.get("val")
                and (level is None or str(p.get("lev", "")).strip() == level)
            ]
            if not candidates:
                continue
            best = None
            best_prod = None
            for prod in candidates:
                latest = _latest_value(prod.get("val") or [], cutoff, counters)
                if latest and (best is None or latest["ref"] > best["ref"]):
                    best = latest
                    best_prod = prod
            if not best or not isinstance(best["value"], (int, float)):
                continue
            value = float(best["value"]) / scale
            if not _in_limits(field, value):
                out_of_range += 1
                continue
            current = fields.get(field)
            if current is None or best["ref"] > current["ref"]:
                fields[field] = {"value": _round(value, digits), "ref": best["ref"]}
                if code == VAR_GUST and best_prod is not None:
                    # il periodo della sorgente viaggia con la raffica: `2,0,3600`
                    # e' un massimo orario, `2,0,900` no. Serve per non spacciare
                    # un massimo di 15 minuti per un massimo di un'ora.
                    gust_last_period[key] = _period_seconds(
                        str(best_prod.get("trange") or "")
                    ) or 0

        # SERIE (pioggia, vento, temperatura, umidita'): si raccolgono TUTTI i
        # punti di ogni prodotto, non solo l'ultimo. Nessun filtro di eta' qui:
        # la finestra della query e' gia' 180 minuti e `build_*_block` ancora la
        # finestra all'ultimo dato.
        for prod in prods:
            code = prod.get("var")
            if code not in (VAR_PRECIP, VAR_WSPD, VAR_GUST, VAR_TEMP, VAR_RH):
                continue
            values = prod.get("val") or []
            if not values:
                continue
            trange = str(prod.get("trange") or "")
            if trange_stat_type(trange) is None:
                continue
            if code == VAR_PRECIP:
                target = rain_series
            elif code == VAR_WSPD:
                target = speed_series
            elif code == VAR_GUST:
                target = gust_series
            elif code == VAR_TEMP:
                target = temp_series
            else:
                target = rh_series
            points = target.setdefault(key, {}).setdefault(trange, [])
            for item in values:
                try:
                    ref = parse_ref(item["ref"])
                except (KeyError, ValueError):
                    continue
                value = item.get("val")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                value = float(value)
                if math.isfinite(value):
                    points.append((ref, value))

    # --- GATE DI PLAUSIBILITA' DELLA TEMPERATURA (rate di variazione) --------
    # Si applica UNA volta alla serie grezza, PRIMA di ogni uso: media/min/max
    # delle finestre, `temp_hourly` (bucket della serie giornaliera) e la
    # temperatura ISTANTANEA pubblicata. Il gate assoluto |T| <= 60 resta in
    # `build_instant_block`; questo e' complementare e lavora sulle differenze.
    # Un campione scartato non viene sostituito: sparisce e finisce in
    # `temp_qc_rejected` (per stazione) e in `counts.temp_points_qc_rejected`.
    temp_rate_rejected: dict[tuple, int] = {}
    baseline = temp_baseline or {}
    baseline_anchored = 0
    for station_key in list(temp_series):
        net, lat, lon = station_key
        baseline_points = baseline.get(baseline_key(net, lat, lon)) or []
        total_rejected = 0
        anchored_here = False
        cleaned: dict[str, list[tuple[datetime, float]]] = {}
        for trange, values in temp_series[station_key].items():
            ordered = sorted(values, key=lambda item: item[0])
            if not ordered:
                continue
            # Ancora: ultimo campione ACCETTATO della giornata PRIMA dell'inizio
            # della finestra. Non il piu' recente del blob (che e' dentro la
            # finestra e puo' essere gia' corrotto).
            anchor: tuple[datetime, float] | None = None
            first_ref = ordered[0][0]
            for base_ref, base_value in reversed(baseline_points):
                if base_ref < first_ref:
                    # Il blob giornaliero e' in degC, la serie grezza in K: il
                    # gate lavora sulle differenze ma l'ancora deve stare nella
                    # STESSA unita' della serie (senza +273.15 il primo campione
                    # sarebbe a ~286 "gradi" contro un'ancora a 15).
                    anchor = (base_ref, base_value + 273.15)
                    break
            series = ([anchor] if anchor is not None else []) + ordered
            kept, rejected, _indexes = qc_temperature_points(series)
            if anchor is not None:
                anchored_here = True
                kept = [point for point in kept if point[0] > anchor[0]]
            total_rejected += rejected
            if kept:
                cleaned[trange] = kept
        temp_series[station_key] = cleaned
        if anchored_here:
            baseline_anchored += 1
        if total_rejected:
            temp_rate_rejected[station_key] = total_rejected

    stations = []
    excluded_no_data = 0
    for key, fields in cells.items():
        if not fields:
            excluded_no_data += 1
            continue
        info = meta[key]
        record = {
            "_net": info["network"],
            "lat": _round(info["lat"], 5),
            "lon": _round(info["lon"], 5),
        }
        if info.get("name"):
            record["name"] = info["name"]
        record["network"] = info["network"]
        obs_time = max(f["ref"] for f in fields.values())
        record["obs_time"] = obs_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        for field in (
            "temperature_2m",
            "relative_humidity_2m",
            "wind_speed_10m",
            "wind_direction_10m",
            "precipitation",
            "precipitation_period_s",
            "station_pressure_hPa",
            "wind_gust_10m",
            "snow_depth_m",
            "fresh_snow_depth_m",
            "river_level_m",
        ):
            if field in fields:
                record[field] = fields[field]["value"]
        # La temperatura ISTANTANEA e' l'ultimo campione ACCETTATO dalla serie,
        # non l'ultimo arrivato: senza questo, un picco falso respinto dalle
        # finestre restava pubblicato in `temperature_2m` e finiva nei `pts` del
        # blob giornaliero (e' esattamente il percorso di Palma di Montechiaro).
        if key in temp_series:
            station_temp_series = temp_series[key]
            instant_tranges = [
                label
                for label in station_temp_series
                if trange_stat_type(label) == STAT_INSTANT
            ]
            pool = instant_tranges or list(station_temp_series)
            latest_temp: tuple[datetime, float] | None = None
            for label in pool:
                for sample_ref, sample_k in station_temp_series[label]:
                    if latest_temp is None or sample_ref > latest_temp[0]:
                        latest_temp = (sample_ref, sample_k)
            if latest_temp is not None:
                temp_c = latest_temp[1] - 273.15
                if _in_limits("temperature_2m", temp_c):
                    fields["temperature_2m"] = {
                        "value": _round(temp_c, 1),
                        "ref": latest_temp[0],
                    }
                    record["temperature_2m"] = fields["temperature_2m"]["value"]
                else:
                    fields.pop("temperature_2m", None)
                    record.pop("temperature_2m", None)
            else:
                fields.pop("temperature_2m", None)
                record.pop("temperature_2m", None)
        if "temperature_2m" in record and "relative_humidity_2m" in record:
            dp = dew_point_c(record["temperature_2m"], record["relative_humidity_2m"])
            if dp is not None:
                dp = _round(dp, 1)
                if _in_limits("dew_point_2m", dp):
                    record["dew_point_2m"] = dp

        # --- cumulate di pioggia e sintesi del vento (dalle serie) ---
        if key in rain_series:
            stations_with_rain_series += 1
            rain_fields, rain_stats = build_rain_block(rain_series[key], generated_at)
            record.update(rain_fields)
            rejected = rain_stats.get("points_qc_rejected", 0)
            rain_points_qc_rejected += rejected
            if rejected:
                stations_rain_qc_rejected += 1
            if record.get("rain_1h_mm") is not None:
                stations_with_rain_1h += 1
            if record.get("rain_3h_mm") is not None:
                stations_with_rain_3h += 1
            if rejected:
                record["rain_qc_rejected"] = rejected
        if key in speed_series or key in gust_series:
            stations_with_wind_series += 1
            wind_fields, wind_stats = build_wind_block(
                speed_series.get(key, {}),
                gust_series.get(key, {}),
                generated_at,
                last_speed=record.get("wind_speed_10m"),
                last_gust=record.get("wind_gust_10m"),
                last_gust_period_s=gust_last_period.get(key),
            )
            record.update(wind_fields)
            rejected = wind_stats.get("points_qc_rejected", 0)
            wind_points_qc_rejected += rejected
            if rejected:
                stations_wind_qc_rejected += 1
                record["wind_qc_rejected"] = rejected
            if record.get("gust_max_1h_ms") is not None:
                stations_with_gust_1h += 1
            if record.get("wind_mean_1h_ms") is not None:
                stations_with_wind_mean_1h += 1
            if record.get("wind_mean_30m_ms") is not None:
                stations_with_wind_mean_30m += 1
            if record.get("gust_max_30m_ms") is not None:
                stations_with_gust_max_30m += 1
        # --- temperatura, umidita' relativa, dew point (dalle serie) ---
        if key in temp_series:
            stations_with_temp_series += 1
            temp_fields, temp_stats = build_instant_block(
                "temp",
                temp_series[key],
                generated_at,
                unit="c",
                decimals=1,
                extremes=("min", "max"),
                offset=-273.15,
                qc_min=-QC_TEMP_MAX_ABS_C,
                qc_max=QC_TEMP_MAX_ABS_C,
            )
            record.update(temp_fields)
            rate_rejected = temp_rate_rejected.get(key, 0)
            rejected = temp_stats.get("points_qc_rejected", 0) + rate_rejected
            temp_points_qc_rejected += rejected
            if rejected:
                stations_temp_qc_rejected += 1
                record["temp_qc_rejected"] = rejected
            if record.get("temp_mean_5m_c") is not None:
                stations_with_temp_mean_5m += 1
            if record.get("temp_mean_30m_c") is not None:
                stations_with_temp_mean_30m += 1
            if record.get("temp_mean_1h_c") is not None:
                stations_with_temp_mean_1h += 1
            if record.get("temp_mean_3h_c") is not None:
                stations_with_temp_mean_3h += 1
        if key in rh_series:
            stations_with_rh_series += 1
            rh_fields, rh_stats = build_instant_block(
                "rh",
                rh_series[key],
                generated_at,
                unit="pct",
                decimals=0,
                qc_min=QC_RH_MIN_PCT,
                qc_max=QC_RH_MAX_PCT,
            )
            record.update(rh_fields)
            rejected = rh_stats.get("points_qc_rejected", 0)
            rh_points_qc_rejected += rejected
            if rejected:
                stations_rh_qc_rejected += 1
                record["rh_qc_rejected"] = rejected
            if record.get("rh_mean_5m_pct") is not None:
                stations_with_rh_mean_5m += 1
            if record.get("rh_mean_30m_pct") is not None:
                stations_with_rh_mean_30m += 1
            if record.get("rh_mean_1h_pct") is not None:
                stations_with_rh_mean_1h += 1
            if record.get("rh_mean_3h_pct") is not None:
                stations_with_rh_mean_3h += 1
        dew_series, dew_rejected_pairs = build_dew_series(
            temp_series.get(key, {}), rh_series.get(key, {})
        )
        if dew_series:
            stations_with_dew_series += 1
            dew_fields, dew_stats = build_instant_block(
                "dew",
                dew_series,
                generated_at,
                unit="c",
                decimals=1,
                extremes=("min",),
                qc_min=-QC_DEW_MAX_ABS_C,
                qc_max=QC_DEW_MAX_ABS_C,
            )
            record.update(dew_fields)
            rejected = dew_stats.get("points_qc_rejected", 0) + dew_rejected_pairs
            dew_points_qc_rejected += rejected
            if rejected:
                stations_dew_qc_rejected += 1
                record["dew_qc_rejected"] = rejected
            if record.get("dew_mean_5m_c") is not None:
                stations_with_dew_mean_5m += 1
            if record.get("dew_mean_30m_c") is not None:
                stations_with_dew_mean_30m += 1
            if record.get("dew_mean_1h_c") is not None:
                stations_with_dew_mean_1h += 1
            if record.get("dew_mean_3h_c") is not None:
                stations_with_dew_mean_3h += 1
        stations.append(record)

    # ordine deterministico: i diff git restano leggibili
    stations.sort(key=lambda s: (s["network"], s["lat"], s["lon"]))
    id_decimals, id_collisions_4dec = _assign_ids(stations)
    stations.sort(key=lambda s: (s["network"], s["id"]))

    used_networks = sorted({s["network"] for s in stations})
    # Passo della serie termica per rete: dice quante stazioni possono avere le
    # finestre corte (5m richiede <= 60 s, 30m <= 300 s). Il passo e' quello
    # MEDIANO della serie istantanea, non il periodo di un prodotto mediato.
    step_histogram: dict[str, dict[str, int]] = {}
    for st in stations:
        step = st.get("temp_series_step_s")
        if isinstance(step, (int, float)):
            bucket = step_histogram.setdefault(st["network"], {})
            key = str(int(step))
            bucket[key] = bucket.get(key, 0) + 1
    step_histogram = {
        net: dict(sorted(per_step.items(), key=lambda item: int(item[0])))
        for net, per_step in sorted(step_histogram.items())
    }
    attribution = [
        {
            "network": net,
            "text": networks[net].get("attribution_description")
            or networks[net].get("attribution")
            or net,
            "url": networks[net].get("attribution_url"),
        }
        for net in used_networks
    ]

    doc = {
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "MeteoHub (Agenzia ItaliaMeteo / CINECA), osservazioni di stazione",
        "source_url": f"{API_BASE}{OBS_PATH}",
        "licence": "CC BY 4.0",
        "licence_url": "https://creativecommons.org/licenses/by/4.0/legalcode",
        "license_group": LICENSE_GROUP,
        "attribution": attribution,
        "units": {
            "temperature_2m": "degC",
            "relative_humidity_2m": "%",
            "wind_speed_10m": "m/s",
            "wind_direction_10m": "deg",
            "precipitation": "mm",
            "precipitation_period_s": "s",
            "dew_point_2m": "degC",
            "station_pressure_hPa": "hPa",
            "wind_gust_10m": "m/s",
            "snow_depth_m": "m",
            "fresh_snow_depth_m": "m",
            "river_level_m": "m",
            # Campi derivati dalle SERIE (non dall'ultimo valore).
            "rain_1h_mm": "mm",
            "rain_3h_mm": "mm",
            "rain_1h_coverage_frac": "1",
            "rain_3h_coverage_frac": "1",
            "rain_period_s_used": "s",
            "rain_hourly": "mm",
            "gust_max_1h_ms": "m/s",
            "gust_max_3h_ms": "m/s",
            "gust_max_1h_coverage_frac": "1",
            "gust_max_3h_coverage_frac": "1",
            "wind_mean_1h_ms": "m/s",
            "wind_mean_3h_ms": "m/s",
            "wind_mean_1h_coverage_frac": "1",
            "wind_mean_3h_coverage_frac": "1",
            "wind_max_3h_ms": "m/s",
            "gust_hourly": "m/s",
            "wind_hourly": "m/s",
            "gust_period_s": "s",
            "wind_period_s": "s",
            # Finestra corta del vento (1.3.0.61): media a 30 min solo con passo
            # della serie <= 300 s; la raffica a 30 min richiede un massimo
            # sorgente con periodo <= 1800 s.
            "wind_mean_30m_ms": "m/s",
            "wind_mean_30m_coverage_frac": "1",
            "gust_max_30m_ms": "m/s",
            "gust_max_30m_coverage_frac": "1",
            # Temperatura (degC), umidita' relativa (%), dew point (degC):
            # 5m/30m/1h/3h, media + estremi + copertura + passo.
            **_thermal_units(),
        },
        "max_age_minutes": max_age_minutes,
        "omitted_fields": {
            "pressure_msl": (
                "la sorgente espone solo pressione di stazione (B10004, livello "
                "1,0,0,0), non riducibile al livello del mare senza la quota; "
                "pubblicata come station_pressure_hPa, MAI come MSL; nel campione "
                "reale 56.730-163.820 Pa (567,3-1638,2 hPa), estremi anomali "
                "scartati fuori dalla finestra 600-1085 hPa"
            ),
            "visibility": "non esposta dalla sorgente",
            "cloud_cover": "non esposta dalla sorgente",
        },
        "derived_fields": {
            "dew_point_2m": (
                "Magnus, gamma=ln(RH/100)+17.625*T/(243.04+T), "
                "Td=243.04*gamma/(17.625-gamma) (Alduchov & Eskridge 1996)"
            ),
            "station_pressure_hPa": (
                "B10004 (PRESSURE PA) al livello 1,0,0,0 = pressione di STAZIONE, "
                "non MSL; valore/100 in hPa; fuori da 600-1085 hPa scartato e contato"
            ),
            "wind_gust_10m": (
                "B11041 MAXIMUM WIND GUST SPEED M/S: massimo sulla finestra "
                "della sorgente (trange 2,0,N), non normalizzato a 10 min"
            ),
            "snow_depth_m": "B13013 TOTAL SNOW DEPTH M, metri",
            "fresh_snow_depth_m": "B13012 DEPTH OF FRESH SNOW M, metri",
            "river_level_m": (
                "B13215 River level M: grandezza IDROLOGICA separata, non meteo, "
                "quota riferita allo zero locale della stazione"
            ),
            "id": (
                "sintetico `<rete>:<lat>,<lon>` a "
                f"{id_decimals} decimali (la sorgente non ha un id di stazione)"
            ),
            "rain_1h_mm/rain_3h_mm": (
                "integrale delle serie B13011 (TOTAL PRECIPITATION) su "
                "[fine finestra - 1h/3h, fine finestra]. `ref` di un accumulo = "
                "FINE dell'intervallo [ref-P3, ref] (misurato: 1.634 casi non "
                "ambigui con questa lettura, 0 con quella opposta, 5 giornate "
                "2026-11-17..21). Prodotto scelto per COPERTURA migliore, a pari "
                "copertura il periodo piu' fine: NON il `ref` piu' recente. "
                "Una cumulata vale solo se copre >= 50% della finestra, altrimenti "
                "e' null con motivo. `fine finestra` = ultimo dato della stazione "
                "(vedi rain_window_end), non `now`: con la latenza mediana della "
                "sorgente (~32 min) ancorare a now darebbe <50% quasi sempre"
            ),
            "rain_hourly": (
                "bucket orari [[epoch_s dell'ora, mm], ...] delle ultime "
                f"{HOURLY_BUCKETS} ore, dallo STESSO prodotto scelto per la 3h. "
                "Un'ora entra solo se coperta >=50% (l'ora corrente vale per il "
                "tempo TRASCORSO). In UTC: il fuso Europe/Rome ha offset interi, "
                "quindi i confini di ora locali coincidono con quelli UTC"
            ),
            "gust_max_1h_ms/gust_max_3h_ms": (
                "MASSIMO (non somma) delle serie B11041 (MAXIMUM WIND GUST SPEED, "
                "trange 2,0,N) il cui intervallo sta almeno al 50% nella finestra. "
                "`wind_gust_10m` NON e' normalizzato a 10 minuti: il periodo della "
                "sorgente viaggia in `gust_period_s` e non viene corretto"
            ),
            "wind_mean_1h_ms/wind_mean_3h_ms": (
                "MEDIA pesata sul tempo delle serie B11002 (WIND SPEED) istantanee "
                "(254,0,0, sample-and-hold) o medie (3,0,N). Un istantaneo vale dal "
                "suo ref al ref successivo: e' l'unico modo di pesare una serie "
                "irregolare senza inventare un passo. Il tipo `200` non e' "
                "documentato e viene ignorato"
            ),
            "wind_max_3h_ms": (
                "massimo del vento medio istantaneo nella finestra di 3 h "
                "(non e' una raffica: per quella vale gust_max_3h_ms)"
            ),
            "wind_source": (
                "`series` = media/integrale dalle serie; `latest_only` = serie "
                "assente o insufficiente, si pubblica l'ULTIMO valore marcato: "
                "non e' una media su un'ora"
            ),
            "temp_mean_*/temp_min_*/temp_max_*": (
                "MEDIA delle serie B12101 (TEMPERATURE/DRY-BULB, K convertiti in "
                "degC) pesata sul tempo (sample-and-hold: un istantaneo vale dal "
                "suo ref al ref successivo), MINIMO e MASSIMO dei campioni con "
                "`ref` nella finestra. Unita' degC, dichiarata in `units`. "
                "Due gate: (1) range assoluto |T| > 60 degC scartato; (2) "
                "PLAUSIBILITA' FISICA, |dT|/dt > 6 degC/h rispetto all'ultimo "
                "campione ACCETTATO (SCELTA INGEGNERISTICA, non una soglia "
                "meteorologica validata: il fohn fa +4-5 degC/h, un sensore "
                "rotto fa +15-37 degC in 30-60 min). Oltre 3 h di buco il rate "
                "non e' valutabile e il campione si accetta. Entrambi i gate "
                "scartano senza inventare un sostituto e contano in "
                "`temp_qc_rejected`. Il campo ISTANTANEO `temperature_2m` e' "
                "l'ultimo campione accettato, non l'ultimo arrivato. "
                "Le finestre sono 5m, 30m, 1h, 3h"
            ),
            "rh_mean_*": (
                "MEDIA pesata sul tempo delle serie B13003 (RELATIVE HUMIDITY, %). "
                "Unita' %. Gate fisico: RH fuori 0..100 % scartata e contata in "
                "`rh_qc_rejected`. Finestre 5m, 30m, 1h, 3h"
            ),
            "dew_mean_*/dew_min_*": (
                "Dew point calcolato con Magnus (Alduchov & Eskridge 1996) SOLO "
                "su coppie T/RH con lo STESSO `ref` di una serie istantanea; poi "
                "media pesata sul tempo e minimo dei campioni. Unita' degC. "
                "|dew| > 60 degC scartato e contato in `dew_qc_rejected`"
            ),
            "finestre 5m e 30m (regola di disponibilita')": (
                "Una finestra corta e' calcolabile solo se il PASSO della serie e' "
                "sufficientemente fine: 5m richiede passo <= 60 s, 30m <= 300 s "
                "(passo MEDIANO fra i campioni per l'istantaneo, P3 dichiarato per "
                "i prodotti mediati). Altrimenti il valore e' null con motivo "
                "`series_too_coarse_for_5min` / `series_too_coarse_for_30min`; il "
                "passo piu' fine della stazione e' in `*_series_step_s`. 1h e 3h "
                "usano la regola della copertura >= 50%. MISURATO il 2026-11-23: "
                "147 stazioni a 60 s (5m e 30m), 19 a 300 s (solo 30m), 915 a "
                "600 s (ne' 5m ne' 30m); il conteggio per rete e per passo e' in "
                "`counts.temp_series_step_by_network`"
            ),
            "wind_mean_30m_ms/gust_max_30m_ms": (
                "Finestra da 30 min del vento: la media richiede passo <= 300 s "
                "(stessa regola delle grandezze termiche); la raffica richiede un "
                "massimo sorgente con periodo <= 1800 s, perche' il massimo di "
                "un'ora non e' il massimo di 30 minuti. Altrimenti null con "
                "`series_too_coarse_for_30min`"
            ),
            "campo assente = grandezza non disponibile": (
                "come per `visibility`: se la stazione non ha la serie non si "
                "scrive un null finto. `null` invece significa 'c'e' ma non "
                "supera il gate', e porta SEMPRE `*_null_reason`"
            ),
        },
        "counts": {
            "stations": len(stations),
            "networks_used": len(used_networks),
            "stations_excluded_no_data": excluded_no_data,
            "stations_excluded_unknown_network": sum(unknown_networks.values()),
            "values_out_of_range": out_of_range,
            "values_dropped_too_old": counters["too_old"],
            "stations_with_duplicate_record": merged_duplicates,
            "stations_fallback_trange": fallback_trange,
            "id_collisions_at_4_decimals": id_collisions_4dec,
            "stations_with_rain_series": stations_with_rain_series,
            "stations_with_rain_1h": stations_with_rain_1h,
            "stations_with_rain_3h": stations_with_rain_3h,
            "stations_with_wind_series": stations_with_wind_series,
            "stations_with_gust_1h": stations_with_gust_1h,
            "stations_with_wind_mean_1h": stations_with_wind_mean_1h,
            "stations_with_wind_mean_30m": stations_with_wind_mean_30m,
            "stations_with_gust_max_30m": stations_with_gust_max_30m,
            "stations_with_temp_series": stations_with_temp_series,
            "stations_with_rh_series": stations_with_rh_series,
            "stations_with_dew_series": stations_with_dew_series,
            "stations_with_temp_mean_5m": stations_with_temp_mean_5m,
            "stations_with_temp_mean_30m": stations_with_temp_mean_30m,
            "stations_with_temp_mean_1h": stations_with_temp_mean_1h,
            "stations_with_temp_mean_3h": stations_with_temp_mean_3h,
            "stations_with_rh_mean_5m": stations_with_rh_mean_5m,
            "stations_with_rh_mean_30m": stations_with_rh_mean_30m,
            "stations_with_rh_mean_1h": stations_with_rh_mean_1h,
            "stations_with_rh_mean_3h": stations_with_rh_mean_3h,
            "stations_with_dew_mean_5m": stations_with_dew_mean_5m,
            "stations_with_dew_mean_30m": stations_with_dew_mean_30m,
            "stations_with_dew_mean_1h": stations_with_dew_mean_1h,
            "stations_with_dew_mean_3h": stations_with_dew_mean_3h,
            "rain_points_qc_rejected": rain_points_qc_rejected,
            "rain_stations_qc_rejected": stations_rain_qc_rejected,
            "wind_points_qc_rejected": wind_points_qc_rejected,
            "wind_stations_qc_rejected": stations_wind_qc_rejected,
            "temp_points_qc_rejected": temp_points_qc_rejected,
            "temp_stations_qc_rejected": stations_temp_qc_rejected,
            "rh_points_qc_rejected": rh_points_qc_rejected,
            "rh_stations_qc_rejected": stations_rh_qc_rejected,
            "dew_points_qc_rejected": dew_points_qc_rejected,
            "dew_stations_qc_rejected": stations_dew_qc_rejected,
            "rain_qc_max_rate_mm_h": QC_RAIN_MAX_RATE_MM_H,
            "wind_qc_max_ms": QC_WIND_MAX_MS,
            "gust_qc_max_ms": QC_GUST_MAX_MS,
            "temp_qc_max_abs_c": QC_TEMP_MAX_ABS_C,
            "temp_qc_max_rate_c_per_h": QC_TEMP_MAX_RATE_C_PER_H,
            "temp_qc_min_abs_delta_c": TEMP_MIN_ABS_DELTA_C,
            "temp_baseline_anchored_stations": baseline_anchored,
            "dew_qc_max_abs_c": QC_DEW_MAX_ABS_C,
            "rh_qc_range_pct": [QC_RH_MIN_PCT, QC_RH_MAX_PCT],
            "temp_series_step_by_network": step_histogram,
        },
        "stations": stations,
    }
    if unknown_networks:
        doc["excluded_networks"] = dict(sorted(unknown_networks.items()))
    return doc


# --------------------------------------------------------------------------
# Serializzazione e validazione
# --------------------------------------------------------------------------
def dumps_document(doc: dict) -> str:
    """JSON con una stazione per riga: diff git piccoli e leggibili."""
    head = {k: v for k, v in doc.items() if k != "stations"}
    lines = ["{"]
    for key, value in head.items():
        lines.append(
            "  %s: %s,"
            % (
                json.dumps(key),
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            )
        )
    stations = doc.get("stations") or []
    if stations:
        body = ",\n".join(
            "    " + json.dumps(s, ensure_ascii=False, separators=(",", ":"))
            for s in stations
        )
        lines.append('  "stations": [')
        lines.append(body)
        lines.append("  ]")
    else:
        lines.append('  "stations": []')
    lines.append("}")
    return "\n".join(lines) + "\n"


def validate_document(doc: dict, min_stations: int, max_bytes: int) -> list[str]:
    problems: list[str] = []
    stations = doc.get("stations") or []
    if len(stations) < min_stations:
        problems.append(f"stazioni {len(stations)} < minimo {min_stations}")
    ids = set()
    for st in stations:
        sid = st.get("id")
        if sid in ids:
            problems.append(f"id duplicato: {sid}")
        ids.add(sid)
        if not st.get("network"):
            problems.append(f"rete mancante per {sid}")
        if not st.get("obs_time"):
            problems.append(f"obs_time mancante per {sid}")
        measured = [
            k
            for k in (
                "temperature_2m",
                "relative_humidity_2m",
                "wind_speed_10m",
                "wind_direction_10m",
                "precipitation",
                "station_pressure_hPa",
                "wind_gust_10m",
                "snow_depth_m",
                "fresh_snow_depth_m",
                "river_level_m",
            )
            if k in st
        ]
        if not measured:
            problems.append(f"nessuna grandezza per {sid}")
        if "visibility" in st or "pressure_msl" in st:
            fake = "visibility" if "visibility" in st else "pressure_msl"
            problems.append(f"campo non disponibile `{fake}` pubblicato per {sid}")
        # Contratto #207: un null senza motivo non e' un'informazione, e' un buco.
        for prefix, suffix in (
            ("rain_1h", "mm"),
            ("rain_3h", "mm"),
            ("gust_max_1h", "ms"),
            ("gust_max_3h", "ms"),
            ("wind_mean_1h", "ms"),
            ("wind_mean_3h", "ms"),
            ("wind_mean_30m", "ms"),
            ("gust_max_30m", "ms"),
        ):
            field = f"{prefix}_{suffix}"
            if field in st and st[field] is None and not st.get(f"{prefix}_null_reason"):
                problems.append(f"null senza motivo: {field} per {sid}")
        for name, suffix in (("temp", "c"), ("rh", "pct"), ("dew", "c")):
            for window in ("5m", "30m", "1h", "3h"):
                field = f"{name}_mean_{window}_{suffix}"
                if (
                    field in st
                    and st[field] is None
                    and not st.get(f"{name}_{window}_null_reason")
                ):
                    problems.append(f"null senza motivo: {field} per {sid}")
        for window in ("5m", "30m", "1h", "3h"):
            low = st.get(f"temp_min_{window}_c")
            mean = st.get(f"temp_mean_{window}_c")
            high = st.get(f"temp_max_{window}_c")
            if all(isinstance(v, (int, float)) for v in (low, mean, high)):
                if not (low <= mean <= high):
                    problems.append(
                        f"temp {window}: min<=mean<=max violato per {sid} "
                        f"({low}, {mean}, {high})"
                    )
        if "rain_source_product" in st and not st.get("rain_period_s_used"):
            problems.append(f"prodotto di pioggia senza periodo per {sid}")
        for prefix in ("rain", "gust", "temp", "rh", "dew"):
            hourly = st.get(f"{prefix}_hourly")
            if hourly is None:
                continue
            width = 4 if prefix in ("temp", "rh") else 2
            if not isinstance(hourly, list) or len(hourly) > 24:
                problems.append(f"{prefix}_hourly malformato per {sid}")
                continue
            for row in hourly:
                if (
                    not isinstance(row, list)
                    or len(row) != width
                    or not isinstance(row[0], int)
                ):
                    problems.append(f"{prefix}_hourly riga non valida per {sid}")
                    break
    if not doc.get("attribution"):
        problems.append("attribuzione assente")
    size = len(dumps_document(doc).encode("utf-8"))
    if size > max_bytes:
        problems.append(f"dimensione {size} > budget {max_bytes}")
    return problems


# --------------------------------------------------------------------------
# Indice compatto per il client (index.json)
# --------------------------------------------------------------------------
def _round1(value: float):
    """1 decimale; gli interi restano interi (e niente -0.0 nei diff)."""
    out = round(float(value), 1)
    if out == 0:
        out = 0.0
    return int(out) if out == int(out) else out


def build_index(
    latest: dict,
    networks: dict | None = None,
    include_names: bool = True,
    extra_fields: tuple[str, ...] = (),
) -> dict:
    """Sottoinsieme di `latest.json` senza le serie: solo i valori correnti.

    Perche' non e' una copia: `latest.json` pesa 1.045.677 B (132.209 gzip) e un
    client mobile non deve scaricarlo a ogni apertura. L'indice tiene le stesse
    `count` stazioni, con `id`/`lat`/`lon`/`name`/`network`/`obs_time` e i soli
    valori correnti arrotondati a 1 decimale; le serie orarie restano in
    `latest.json`. Le stazioni sono ARRAY nell'ordine dichiarato da
    `station_fields`: la forma a oggetti costa ~230 B/stazione di chiavi
    ripetute e sfora il budget (misurato: 123.671 B gzip contro 101.258 B).

    `include_names=False` e' la degradazione dichiarata: il nome e' il campo
    opzionale piu' grosso, si azzera (`null`) se l'indice sfora il budget.
    """
    stations = latest.get("stations") or []
    table = networks or {}
    fields = list(INDEX_STATION_FIELDS) + [
        name for name in extra_fields if name not in INDEX_STATION_FIELDS
    ]
    per_network: dict[str, dict] = {}
    for st in stations:
        net = st.get("network")
        entry = per_network.setdefault(
            net,
            {
                "id": net,
                "name": (table.get(net) or {}).get("name"),
                "count": 0,
            },
        )
        entry["count"] += 1

    rows: list[list] = []
    for st in stations:
        row = []
        for field in fields:
            if field == "name" and not include_names:
                row.append(None)
            elif field in ("id", "name", "network", "obs_time", "lat", "lon"):
                row.append(st.get(field))
            elif field in INDEX_STRING_FIELDS:
                value = st.get(field)
                row.append(None if value is None else str(value))
            else:
                value = st.get(field)
                row.append(None if value is None else _round1(value))
        rows.append(row)

    lats = [st["lat"] for st in stations if st.get("lat") is not None]
    lons = [st["lon"] for st in stations if st.get("lon") is not None]
    bbox = (
        {
            "west": min(lons),
            "south": min(lats),
            "east": max(lons),
            "north": max(lats),
        }
        if lats and lons
        else None
    )
    units = latest.get("units") or {}
    return {
        "schema": INDEX_SCHEMA,
        "generated_at": latest.get("generated_at"),
        "count": len(stations),
        "source": latest.get("source"),
        "source_url": latest.get("source_url"),
        "licence": latest.get("licence"),
        "licence_url": latest.get("licence_url"),
        "max_age_minutes": latest.get("max_age_minutes"),
        "attribution": latest.get("attribution") or [],
        "bbox": bbox,
        "networks": [per_network[k] for k in sorted(per_network, key=lambda n: (n is None, n))],
        "units": {key: units[key] for key in fields if key in units},
        "station_fields": fields,
        "note": (
            "Stesse stazioni di latest.json, SENZA le serie orarie. Valori "
            "correnti dell'indice arrotondati a 1 decimale per TUTTI i campi "
            "(in latest.json neve e livello idro hanno 2 decimali); "
            "null = grandezza non disponibile. `precipitation` va letta con "
            "`precipitation_period_s` (il periodo di accumulo cambia per rete). "
            "`station_pressure_hPa` e' pressione di STAZIONE (B10004, livello "
            "1,0,0,0), NON MSL. `river_level_m` e' idrologico, non meteo. "
            "Nessuna visibilita' ne' cloud: la sorgente non li espone."
        ),
        "stations": rows,
    }


def dumps_index(index: dict) -> str:
    """JSON dell'indice con una stazione per riga: diff git piccoli."""
    head = {k: v for k, v in index.items() if k != "stations"}
    lines = ["{"]
    for key, value in head.items():
        lines.append(
            "  %s: %s,"
            % (
                json.dumps(key),
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            )
        )
    rows = index.get("stations") or []
    if rows:
        body = ",\n".join(
            "    " + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            for row in rows
        )
        lines.append('  "stations": [')
        lines.append(body)
        lines.append("  ]")
    else:
        lines.append('  "stations": []')
    lines.append("}")
    return "\n".join(lines) + "\n"


def index_gzip_bytes(index: dict) -> int:
    return len(gzip.compress(dumps_index(index).encode("utf-8"), 9))


def validate_index(
    index: dict,
    latest: dict,
    max_gzip_bytes: int | None = None,
    max_problems: int = 20,
) -> list[str]:
    """Coerenza indice <-> latest: stesse stazioni, stessi valori, attribuzione.

    Non e' un confronto di facciata: ogni riga viene ricomposta con
    `station_fields` e confrontata con la stazione corrispondente di
    `latest.json`. I campi valore vengono confrontati DOPO arrotondamento a 1
    decimale con `_round1`, la STESSA funzione usata da `build_index`: in
    `latest.json` neve e livello idro hanno 2 decimali, nell'indice 1.

    Perche' non una tolleranza assoluta 0,05: in virgola mobile
    `1,25 - 1,2 = 0,050000000000000044 > 0,05`, e il run del 17/11/2026 e'
    fallito con una serie di errori su `river_level_m` (EXIT=2, step 4).
    Arrotondando entrambe le parti il confronto resta severo: 1,25 -> 1,3 e'
    ancora un errore, quindi il test continua a mordere davvero.
    """
    problems: list[str] = []

    def add(message: str) -> None:
        if len(problems) < max_problems:
            problems.append(message)

    if index.get("schema") != INDEX_SCHEMA:
        add(f"schema {index.get('schema')!r} != {INDEX_SCHEMA!r}")
    fields = list(index.get("station_fields") or [])
    expected = list(INDEX_STATION_FIELDS)
    if fields[: len(expected)] != expected:
        add("station_fields non inizia con INDEX_STATION_FIELDS")
    for name in fields[len(expected) :]:
        if name not in INDEX_EXTRA_FIELDS:
            add(f"campo extra non ammesso nell'indice: {name}")
    if not index.get("attribution"):
        add("attribuzione assente")
    rows = index.get("stations") or []
    latest_sts = latest.get("stations") or []
    if index.get("count") != len(rows):
        add(f"count {index.get('count')} != stazioni indice {len(rows)}")
    if len(rows) != len(latest_sts):
        add(f"stazioni indice {len(rows)} != latest {len(latest_sts)}")
    if len(rows) != len(latest_sts):
        return problems  # il confronto riga per riga non avrebbe senso

    value_fields = [
        f
        for f in fields
        if f not in ("id", "lat", "lon", "name", "network", "obs_time")
        and f not in INDEX_STRING_FIELDS
    ]
    string_fields = [f for f in fields if f in INDEX_STRING_FIELDS]
    for position, (row, st) in enumerate(zip(rows, latest_sts)):
        if len(row) != len(fields):
            add(f"riga {position}: {len(row)} campi invece di {len(fields)}")
            continue
        record = dict(zip(fields, row))
        if record.get("id") != st.get("id"):
            add(f"riga {position}: id {record.get('id')!r} != {st.get('id')!r}")
        for key in ("network", "obs_time", "lat", "lon"):
            if record.get(key) != st.get(key):
                add(f"riga {position} ({st.get('id')}): {key} diverso")
        for key in value_fields:
            expected = st.get(key)
            got = record.get(key)
            if expected is None:
                if got is not None:
                    add(f"riga {position} ({st.get('id')}): {key} dovrebbe essere null")
            elif got is None or _round1(expected) != _round1(got):
                add(
                    f"riga {position} ({st.get('id')}): {key} {got!r} != {expected!r}"
                )
        for key in string_fields:
            if record.get(key) != st.get(key):
                add(
                    f"riga {position} ({st.get('id')}): {key} "
                    f"{record.get(key)!r} != {st.get(key)!r}"
                )
    networks = {st.get("network") for st in latest_sts}
    if {item.get("network") for item in index.get("attribution") or []} != networks:
        add("reti dell'attribuzione diverse dalle reti pubblicate")
    declared = {item.get("id") for item in index.get("networks") or []}
    if declared != networks:
        add("reti dell'indice diverse dalle reti pubblicate")
    counts = {item.get("id"): item.get("count") for item in index.get("networks") or []}
    for net in networks:
        expected = sum(1 for st in latest_sts if st.get("network") == net)
        if counts.get(net) != expected:
            add(f"rete {net}: conteggio {counts.get(net)} != {expected}")
    bbox = index.get("bbox")
    if latest_sts and not bbox:
        add("bbox assente")
    elif bbox:
        for st in latest_sts:
            if not (
                bbox["west"] <= st["lon"] <= bbox["east"]
                and bbox["south"] <= st["lat"] <= bbox["north"]
            ):
                add(f"stazione {st.get('id')} fuori dal bbox")
                break
    if max_gzip_bytes is not None:
        size = index_gzip_bytes(index)
        if size > max_gzip_bytes:
            add(f"indice {size} B gzip > budget {max_gzip_bytes}")
    return problems


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="data/stations/latest.json")
    ap.add_argument("--networks-file", default=str(here / "networks.json"))
    ap.add_argument(
        "--window-minutes", type=int, default=180,
        help="finestra reftime richiesta (default 180 = 3 h, come --max-age-minutes)",
    )
    ap.add_argument(
        "--max-age-minutes", type=int, default=180,
        help="scarta osservazioni piu' vecchie (default 180 = 3 h)",
    )
    ap.add_argument(
        "--min-stations", type=int, default=500,
        help="sotto questo numero il file NON viene scritto",
    )
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument(
        "--index-out",
        default=None,
        help="indice compatto (default: index.json nella stessa cartella di --out)",
    )
    ap.add_argument(
        "--index-max-gzip-bytes",
        type=int,
        default=INDEX_MAX_GZIP_BYTES,
        help="budget dell'indice in byte gzip (default 163840 = 160 KiB)",
    )
    ap.add_argument("--refresh-networks", action="store_true")
    ap.add_argument(
        "--now", default=None,
        help="istante di riferimento ISO8601 (default: adesso UTC)",
    )
    ap.add_argument(
        "--baseline",
        default=None,
        help=(
            "blob giornaliero (data/stations/daily_series.json) da usare come "
            "ancora del gate di temperatura: l'ultimo campione sano PRIMA "
            "dell'inizio della finestra. Assente -> nessuna ancora (stateless)"
        ),
    )
    args = ap.parse_args(argv)

    networks_path = Path(args.networks_file)
    if args.refresh_networks:
        try:
            doc = refresh_networks(networks_path)
        except RuntimeError as exc:
            print(f"[FATAL] impossibile leggere i metadati: {exc}", file=sys.stderr)
            return 3
        print(
            f"[OK] tabella reti rigenerata: {len(doc['networks'])} reti -> {networks_path}"
        )
        return 0

    networks = load_networks(networks_path)
    if not networks:
        print("[FATAL] tabella reti vuota", file=sys.stderr)
        return 2

    now = (
        parse_ref(args.now)
        if args.now
        else datetime.now(timezone.utc).replace(microsecond=0)
    )
    if args.window_minutes > args.max_age_minutes:
        print(
            f"[WARN] finestra {args.window_minutes} min > eta' massima "
            f"{args.max_age_minutes} min: la finestra domina",
            file=sys.stderr,
        )
    start = now - timedelta(minutes=args.window_minutes)
    query = build_query(start, now)
    url = f"{API_BASE}{OBS_PATH}?" + urllib.parse.urlencode(
        {"q": query, "last": "true"}
    )
    print(
        f"[INFO] finestra {start:%Y-%m-%d %H:%M} -> {now:%Y-%m-%d %H:%M} UTC "
        f"({args.window_minutes} min)"
    )

    try:
        raw, info = http_get(url)
    except RuntimeError as exc:
        print(
            f"[FATAL] lettura fallita: {exc}\n"
            "        (se e' un 401 il servizio ha introdotto un token: "
            "fermarsi e chiedere l'accesso, non inventarlo)\n"
            "        nessun file scritto: l'ultimo dataset valido resta in piedi",
            file=sys.stderr,
        )
        return 3
    print(
        f"[INFO] HTTP {info['http_status']} wire {info['wire_bytes']} B "
        f"in {info['seconds']} s"
    )
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        print(f"[FATAL] risposta non JSON: {exc}", file=sys.stderr)
        return 3
    baseline: dict[str, list[tuple[datetime, float]]] = {}
    if args.baseline:
        baseline_path = Path(args.baseline)
        if baseline_path.exists():
            baseline = load_temperature_baseline(baseline_path)
            print(
                f"[INFO] ancora di temperatura: {len(baseline)} stazioni dal "
                f"blob giornaliero {baseline_path}"
            )
        else:
            print(
                f"[INFO] ancora di temperatura: {baseline_path} assente "
                "(primo run della giornata o blob non ripristinato)"
            )
    doc = normalize_payload(
        payload,
        networks,
        generated_at=now,
        max_age_minutes=args.max_age_minutes,
        temp_baseline=baseline,
    )

    problems = validate_document(doc, args.min_stations, args.max_bytes)
    # budget: se sfora si toglie il nome (campo opzionale piu' grosso) e si dichiara.
    if any("budget" in p for p in problems):
        for st in doc["stations"]:
            st.pop("name", None)
        doc["name_omitted"] = True
        problems = validate_document(doc, args.min_stations, args.max_bytes)
    if problems:
        for problem in problems:
            print(f"[FATAL] {problem}", file=sys.stderr)
        return 2

    # Indice compatto: si valida PRIMA di scrivere, cosi' non esiste un run in
    # cui latest.json e index.json descrivono stazioni diverse.
    #
    # I campi derivati dalle serie entrano nell'indice SOLO se il budget gzip lo
    # consente: si misura il candidato migliore e si degrada in ordine dichiarato
    # (campi extra -> nomi). Il costo non si stima, si comprime.
    attempts = (
        {"extra_fields": INDEX_EXTRA_FIELDS, "include_names": True},
        {"extra_fields": (), "include_names": True},
        {"extra_fields": (), "include_names": False},
    )
    index = None
    index_text = ""
    index_size = 0
    index_gz = 0
    for attempt, options in enumerate(attempts):
        candidate = build_index(doc, networks=networks, **options)
        if attempt > 0:
            candidate["index_extra_fields_omitted"] = list(INDEX_EXTRA_FIELDS)
        if not options["include_names"]:
            candidate["name_omitted"] = True
        index_problems = validate_index(candidate, doc)
        if index_problems:
            for problem in index_problems:
                print(f"[FATAL] indice: {problem}", file=sys.stderr)
            return 2
        candidate_text = dumps_index(candidate)
        candidate_size = len(candidate_text.encode("utf-8"))
        candidate_gz = index_gzip_bytes(candidate)
        index, index_text, index_size, index_gz = (
            candidate,
            candidate_text,
            candidate_size,
            candidate_gz,
        )
        if candidate_gz <= args.index_max_gzip_bytes:
            if attempt > 0:
                print(
                    f"[WARN] indice {candidate_gz} B gzip: campi extra omessi "
                    f"({'nomi rimossi' if not options['include_names'] else 'nomi tenuti'})",
                    file=sys.stderr,
                )
            break
        print(
            f"[WARN] indice {candidate_gz} B gzip > budget "
            f"{args.index_max_gzip_bytes} B (tentativo {attempt + 1}/{len(attempts)})",
            file=sys.stderr,
        )
    if index_gz > args.index_max_gzip_bytes:
        print(
            f"[WARN] indice ancora sopra budget anche nella forma minima "
            f"({index_gz} B gzip): scritto comunque e dichiarato",
            file=sys.stderr,
        )

    text = dumps_document(doc)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8", newline="\n")
    index_path = (
        Path(args.index_out) if args.index_out else out_path.with_name("index.json")
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_text, encoding="utf-8", newline="\n")

    counts = doc["counts"]
    size = len(text.encode("utf-8"))
    print(
        f"[OK] {out_path} {size} B ({size / 1024:.0f} KiB) "
        f"stazioni={counts['stations']} reti={counts['networks_used']} "
        f"scartate_senza_dati={counts['stations_excluded_no_data']} "
        f"rete_ignota={counts['stations_excluded_unknown_network']} "
        f"fuori_range={counts['values_out_of_range']}"
    )
    print(
        f"[OK] {index_path} {index_size} B ({index_size / 1024:.0f} KiB), "
        f"gzip {index_gz} B ({index_gz / 1024:.0f} KiB), "
        f"stazioni={index['count']}, budget={args.index_max_gzip_bytes} B"
    )
    for net in doc.get("excluded_networks", {}):
        print(f"[WARN] rete esclusa (licenza/attribuzione non verificate): {net}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
