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
                 precipitation, precipitation_period_s, dew_point_2m?}]}

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
    si puo' ridurre. Inoltre nel campione reale i valori vanno da 58.590 a
    163.820 Pa (585,9-1638,2 hPa): 1638 hPa e' fuori da ogni range fisico.
    Pubblicarla come `pressure_msl` sarebbe un'etichetta falsa.
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
}

DEFAULT_MAX_BYTES = 1_500_000  # target dichiarato dal committente

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
)
INDEX_MAX_GZIP_BYTES = 122_880  # 120 KiB (misurato: 101.258 B, margine 17%)


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


def normalize_payload(
    payload: dict,
    networks: dict,
    generated_at: datetime,
    max_age_minutes: int = 180,
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
        ):
            if field in fields:
                record[field] = fields[field]["value"]
        if "temperature_2m" in record and "relative_humidity_2m" in record:
            dp = dew_point_c(record["temperature_2m"], record["relative_humidity_2m"])
            if dp is not None:
                dp = _round(dp, 1)
                if _in_limits("dew_point_2m", dp):
                    record["dew_point_2m"] = dp
        stations.append(record)

    # ordine deterministico: i diff git restano leggibili
    stations.sort(key=lambda s: (s["network"], s["lat"], s["lon"]))
    id_decimals, id_collisions_4dec = _assign_ids(stations)
    stations.sort(key=lambda s: (s["network"], s["id"]))

    used_networks = sorted({s["network"] for s in stations})
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
        },
        "max_age_minutes": max_age_minutes,
        "omitted_fields": {
            "pressure_msl": (
                "la sorgente espone solo pressione di stazione (B10004, livello "
                "1,0,0,0), non riducibile al livello del mare senza la quota; nel "
                "campione reale 585,9-1638,2 hPa, con valori fuori da ogni range "
                "fisico"
            ),
            "visibility": "non esposta dalla sorgente",
        },
        "derived_fields": {
            "dew_point_2m": (
                "Magnus, gamma=ln(RH/100)+17.625*T/(243.04+T), "
                "Td=243.04*gamma/(17.625-gamma) (Alduchov & Eskridge 1996)"
            ),
            "id": (
                "sintetico `<rete>:<lat>,<lon>` a "
                f"{id_decimals} decimali (la sorgente non ha un id di stazione)"
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
            )
            if k in st
        ]
        if not measured:
            problems.append(f"nessuna grandezza per {sid}")
        if "visibility" in st or "pressure_msl" in st:
            fake = "visibility" if "visibility" in st else "pressure_msl"
            problems.append(f"campo non disponibile `{fake}` pubblicato per {sid}")
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
        for field in INDEX_STATION_FIELDS:
            if field == "name" and not include_names:
                row.append(None)
            elif field in ("id", "name", "network", "obs_time", "lat", "lon"):
                row.append(st.get(field))
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
        "units": {
            key: units[key]
            for key in (
                "temperature_2m",
                "relative_humidity_2m",
                "wind_speed_10m",
                "wind_direction_10m",
                "precipitation",
                "precipitation_period_s",
            )
            if key in units
        },
        "station_fields": list(INDEX_STATION_FIELDS),
        "note": (
            "Stesse stazioni di latest.json, SENZA le serie orarie. Valori "
            "correnti arrotondati a 1 decimale; null = grandezza non "
            "disponibile. `precipitation` va letta con "
            "`precipitation_period_s` (il periodo di accumulo cambia per rete)."
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
    `latest.json` (tolleranza 0,05 = arrotondamento a 1 decimale).
    """
    problems: list[str] = []

    def add(message: str) -> None:
        if len(problems) < max_problems:
            problems.append(message)

    if index.get("schema") != INDEX_SCHEMA:
        add(f"schema {index.get('schema')!r} != {INDEX_SCHEMA!r}")
    fields = list(index.get("station_fields") or [])
    if fields != list(INDEX_STATION_FIELDS):
        add("station_fields diverso da INDEX_STATION_FIELDS")
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

    value_fields = [f for f in fields if f not in ("id", "lat", "lon", "name", "network", "obs_time")]
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
            elif got is None or abs(float(got) - float(expected)) > 0.05:
                add(
                    f"riga {position} ({st.get('id')}): {key} {got!r} != {expected!r}"
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
        help="budget dell'indice in byte gzip (default 122880 = 120 KiB)",
    )
    ap.add_argument("--refresh-networks", action="store_true")
    ap.add_argument(
        "--now", default=None,
        help="istante di riferimento ISO8601 (default: adesso UTC)",
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
    doc = normalize_payload(
        payload, networks, generated_at=now, max_age_minutes=args.max_age_minutes
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
    index = build_index(doc, networks=networks)
    index_problems = validate_index(index, doc)
    if index_problems:
        for problem in index_problems:
            print(f"[FATAL] indice: {problem}", file=sys.stderr)
        return 2
    index_text = dumps_index(index)
    index_size = len(index_text.encode("utf-8"))
    index_gz = index_gzip_bytes(index)
    if index_gz > args.index_max_gzip_bytes:
        # Degradazione dichiarata: il nome e' il campo opzionale piu' grosso.
        index = build_index(doc, networks=networks, include_names=False)
        index["name_omitted"] = True
        index_problems = validate_index(index, doc)
        if index_problems:
            for problem in index_problems:
                print(f"[FATAL] indice: {problem}", file=sys.stderr)
            return 2
        index_text = dumps_index(index)
        index_size = len(index_text.encode("utf-8"))
        index_gz = index_gzip_bytes(index)
        print(
            f"[WARN] indice {index_gz} B gzip > budget "
            f"{args.index_max_gzip_bytes} B: nomi rimossi (name_omitted)",
            file=sys.stderr,
        )
        if index_gz > args.index_max_gzip_bytes:
            print(
                f"[WARN] indice ancora sopra budget anche senza nomi "
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
