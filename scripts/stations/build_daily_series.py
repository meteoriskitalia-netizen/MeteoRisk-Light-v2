#!/usr/bin/env python3
"""Costruisce data/stations/daily_series.json da data/stations/latest.json.

REGOLA (brief 17/11/2026): lo storico del giorno va scritto LATO SERVER, non nel
browser. Il workflow stations.yml gira ogni 10 minuti e riscrive UN SOLO BLOB VIVO:

    data/stations/daily_series.json

  * ``day`` e' la giornata LOCALE Europe/Rome del run (non UTC, non quella del browser).
  * Se il ``day`` del file su disco e' diverso -> i PUNTI si azzerano (nuovo
    giorno locale), ma i bucket orari del giorno precedente si conservano nei
    campi ``*_prev``: le finestre 6 h/24 h sono scorrevoli e devono attraversare
    la mezzanotte.
  * Altrimenti si aggancia ogni osservazione di latest.json, con DEDUP per
    ``obs_time`` (stesso istante -> sostituisci la tupla, non aggiungere).
  * Un solo blob vivo, sovrascritto a ogni run: nessuno storico voluto nel repo.
    NOTA DICHIARATA: se il workflow commotta su `main`, il vecchio blob resta
    raggiungibile nella storia Git di `main`; la gc lo recupera. Per non averlo
    affatto serve una ref a push incrementale/orphan (NON fatto qui: il commit e'
    gestito dal workflow esistente, che questo script non tocca).

SCHEMA 2 (1.3.0.56) — bucket orari per cumulate oltre le 3 ore di serie
------------------------------------------------------------------------
1.3.0.61 aggiunge i bucket TERMICI: `th` {ora: [media, minima, massima]},
`rhh` {ora: [media, minima, massima]}, `dwh` {ora: [media]} e i derivati
`temp_6h_c`, `temp_24h_c`, `rh_6h_pct`, `rh_24h_pct`, `dew_6h_c`,
`dew_24h_c` con i rispettivi `*_partial`. Stessa regola di dedup: l'ora si
SOSTITUISCE. Dagli stessi bucket si leggono `temp_max_day_c` /
`temp_min_day_c` (con l'ora locale del picco) e `rh_max_day_pct` /
`rh_min_day_pct`.
`latest.json` porta solo le ultime 3 ore di serie: per una cumulata a 6 h e 24 h
serve uno stato che sopravviva ai run. Ogni run ricalcola le ultime 4 ore dalla
serie e SOSTITUISCE il bucket di quell'ora (mai somma: ricalcolo -> nessun doppio
conteggio, ed e' il "dedup per minuto" richiesto). I bucket delle ore vecchie,
che il run non rivede piu', restano.

1.3.0.86 — FINESTRE SCORREVOLI, NON ANCORATE ALLA MEZZANOTTE
------------------------------------------------------------
Prima `rain_6h_mm`/`rain_24h_mm` (e vento, temperatura, umidita', dew point)
partivano dalla mezzanotte locale: alle 02:00 la "24 h" copriva 2 ore. Ora le
finestre terminano a `now` e attraversano la mezzanotte. Lo stato necessario e'
il giorno locale corrente (`rh`, `wh`, `th`, `rhh`, `dwh`) PIU' il giorno
precedente (`rh_prev`, ...): al cambio di giorno i bucket si spostano in
`*_prev` e si completano con le righe di ieri ancora presenti in latest.json. La
coda non piu' utile viene POTATA a ogni run (restano le ore >= ora locale
corrente), quindi il costo in byte e' massimo a inizio giornata, quando il resto
del blob e' minimo, e si annulla a fine giornata.

Regola di copertura: `*_partial` = copertura < 50% delle ore nominali (non piu'
"prima di mezzanotte"). Ogni finestra dichiara `window_start` (inizio del primo
slot CON UN VALORE), `window_end` (adesso) e `coverage_frac` (slot con dato /
ore nominali). Limite dichiarato: i bucket sono orari, quindi una finestra di
H ore somma fra H-1 e H ore reali (il primo slot puo' essere tagliato a
sinistra; l'ora corrente e' parziale per costruzione). Anche l'ultima ora prima
della mezzanotte puo' essere parziale per la latenza della sorgente (~32 min
mediani): il totale e' dichiarato come limite inferiore, non come stima.

    {
      "schema": "stations-daily-series/2",
      "generated_at": "2026-11-18T07:00:00Z",
      "day": "2026-11-18",
      "timezone": "Europe/Rome",
      "note": "...",
      "max_points_per_station": 288,
      "stations": {
        "<id>": {
          "name": "Roma Test",
          "pts": [[epochMs, temp, rh, wind, dir, rain_mm_h, period_s,
                   dew, stationPressure_hPa, gust, snowDepth_m], ...],
          "rh": {"3": 0.0, "4": 1.2, "5": 0.4},
          "wh": {"4": [2.3, 7.1], "5": [1.8, null]},
          "th": {"4": [temp_mean, temp_min, temp_max]},
          "rhh": {"4": [rh_mean, rh_min, rh_max]},
          "dwh": {"4": [dew_mean]},
          "rain_6h_mm": 1.6, "rain_6h_partial": false,
          "rain_6h_coverage_frac": 1.0,
          "rain_6h_window_start": "2026-11-18T02:00:00+01:00",
          "rain_6h_window_end": "2026-11-18T08:00:00+01:00",
          "rain_24h_mm": 1.6, "rain_24h_partial": true,
          "rain_24h_coverage_frac": 0.375,
          "rain_24h_window_start": "2026-11-17T23:00:00+01:00",
          "rain_24h_window_end": "2026-11-18T08:00:00+01:00",
          "rain_24h_since": "2026-11-17T23:00:00+01:00",
          "wind_6h_ms": 2.1, "wind_6h_partial": false,
          "wind_6h_coverage_frac": 1.0,
          "wind_24h_ms": 2.1, "wind_24h_partial": true,
          "wind_24h_coverage_frac": 0.375,
          "gust_max_6h_ms": 7.1, "gust_max_6h_partial": false,
          "gust_max_6h_coverage_frac": 1.0,
          "gust_max_24h_ms": 7.1, "gust_max_24h_partial": true,
          "gust_max_24h_coverage_frac": 0.375,
          "temp_6h_c": 9.4, "temp_6h_partial": false,
          "temp_24h_c": 8.9, "temp_24h_partial": true,
          "rh_6h_pct": 71, "rh_24h_pct": 68,
          "dew_6h_c": 5.2, "dew_24h_c": 4.8,
          "temp_max_day_c": 18.2, "temp_max_day_hour": "14",
          "temp_min_day_c": 2.1, "temp_min_day_hour": "6",
          "temp_day_coverage_frac": 0.958, "temp_day_partial": true,
          "rh_max_day_pct": 98, "rh_max_day_hour": "5",
          "rh_min_day_pct": 61, "rh_min_day_hour": "14",
          "rh_day_coverage_frac": 0.958, "rh_day_partial": true
        }
      }
    }

`pts` resta la tupla della v1 (indici 0..10, vedi sotto): il client 1.3.0.55
continua a leggerla senza modifiche, i campi nuovi sono ADDITIVI. Nessuna
migrazione distruttiva.

Indici della tupla (NON riordinarli: il client li usa):
  0 epochMs, 1 temp, 2 rh, 3 wind, 4 dir, 5 rain mm/h, 6 period s,
  7 dew, 8 pressione di STAZIONE (non MSL), 9 raffica, 10 neve al suolo.

I bucket orari usano chiavi "0".."23" = ORA LOCALE. La conversione e' sicura
perche' Europe/Rome ha offset interi: un'ora UTC sta interamente in un'ora
locale (e in un solo giorno locale). I campi `*_prev` usano le stesse chiavi ma
si riferiscono al giorno locale precedente (`prev_day` in testa al documento);
sono la coda necessaria alle finestre che attraversano la mezzanotte e vengono
potati alle ore ancora utili.

Solo stdlib. Uso zoneinfo (stdlib) con fallback manuale CET/CEST se tzdata manca.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # eseguito come script: la cartella dello script e' su sys.path
    from temp_qc import TEMP_DAILY_BUCKET_TOLERANCE_C, qc_temperature_points
except ImportError:  # importato come pacchetto (test/strumenti)
    from scripts.stations.temp_qc import (
        TEMP_DAILY_BUCKET_TOLERANCE_C,
        qc_temperature_points,
    )

try:
    from zoneinfo import ZoneInfo

    _ROME = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover - host senza tzdata
    _ROME = None

SCHEMA = "stations-daily-series/2"
SCHEMA_READABLE = ("stations-daily-series/1", "stations-daily-series/2")
TZ_NAME = "Europe/Rome"
MAX_POINTS_PER_STATION = 288
HOURS_IN_DAY = 24
WINDOW_6H = 6
#: Sotto questa copertura la finestra e' dichiarata parziale (nuova semantica
#: 1.3.0.86: non piu' "prima di mezzanotte", ma "< 50% delle ore nominali").
MIN_COVERAGE_FRAC = 0.5
BUCKET_NAMES = ("rh", "wh", "th", "rhh", "dwh")
PREV_SUFFIX = "_prev"
NOTE = (
    "Serie giornaliera osservata (Europe/Rome) + bucket orari di pioggia (mm), "
    "vento (m/s), temperatura (degC), umidita' relativa (%) e dew point (degC). "
    "Un solo blob vivo riscritto a ogni run del workflow stations.yml; i PUNTI "
    "si azzerano a mezzanotte locale, i BUCKET del giorno precedente restano in "
    "`rh_prev`/`wh_prev`/`th_prev`/`rhh_prev`/`dwh_prev` per le finestre "
    "scorrevoli che attraversano la mezzanotte (la coda non piu' utile e' "
    "potata a ogni run). Dedup per obs_time sui punti e per ora sui bucket "
    "(ricalcolo, mai somma). Le finestre 6 h e 24 h terminano a NOW, non alla "
    "mezzanotte: rain_6h_mm, rain_24h_mm, wind_6h_ms, wind_24h_ms, "
    "gust_max_6h_ms, gust_max_24h_ms, temp_*, rh_*, dew_* sono calcolati dagli "
    "slot orari. `*_partial` = copertura < 50% delle ore nominali; "
    "`window_start`, `window_end` e `coverage_frac` dichiarano la finestra "
    "effettiva (il primo slot puo' essere tagliato a sinistra: la somma copre "
    "fra H-1 e H ore reali; l'ultima ora prima della mezzanotte puo' essere "
    "parziale per la latenza della sorgente, ~32 min mediani: il totale e' un "
    "limite inferiore). Nessun punto o ora passata viene inventato. "
    "Massimo e minimo della GIORNATA (temp_max_day_c / temp_min_day_c e "
    "rh_max_day_pct / rh_min_day_pct) con l'ora locale del picco e la copertura "
    "fino all'ora corrente; la giornata e' dichiarata parziale finche' non sono "
    "passate tutte le ore o la copertura e' sotto il 90%. "
    "GATE DI PLAUSIBILITA' (condiviso con fetch_stations.py, temp_qc.py): i "
    "punti di temperatura con |dT|/dt > 6 degC/h rispetto all'ultimo campione "
    "accettato vengono azzerati (le altre grandezze della stessa osservazione "
    "restano) e contati in `temp_qc_rejected`; un bucket orario che si discosta "
    "di oltre 15 degC dai punti della stessa ora viene ricostruito da quei "
    "punti, e viene eliminato se tutti i punti dell'ora sono stati scartati. "
    "Nessun valore sostitutivo inventato."
)

# Indici tupla — allineati a stationsSeriesTuple()/stationsSeriesValue() nel client.
I_TIME, I_TEMP, I_RH, I_WIND, I_DIR, I_RAIN, I_PERIOD, I_DEW, I_PRESS, I_GUST, I_SNOW = range(11)
TUPLE_LEN = 11


# --------------------------------------------------------------------------- #
# Europe/Rome: giorno locale da epoch ms
# --------------------------------------------------------------------------- #
def _last_sunday(year: int, month: int) -> datetime:
    """Ultima domenica del mese, a mezzanotte (naive)."""
    if month == 12:
        d = datetime(year + 1, 1, 1)
    else:
        d = datetime(year, month + 1, 1)
    d -= timedelta(days=1)
    while d.weekday() != 6:  # 6 = domenica
        d -= timedelta(days=1)
    return d


def _rome_offset_hours(utc_naive: datetime) -> int:
    """Offset EU/CEST: ultima domenica di marzo 01:00Z -> +2, ottobre 01:00Z -> +1."""
    y = utc_naive.year
    start = _last_sunday(y, 3).replace(hour=1)
    end = _last_sunday(y, 10).replace(hour=1)
    return 2 if start <= utc_naive < end else 1


def rome_day(epoch_ms: int) -> str:
    """Data LOCALE Europe/Rome (YYYY-MM-DD) di un epoch ms."""
    utc_dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    if _ROME is not None:
        return utc_dt.astimezone(_ROME).strftime("%Y-%m-%d")
    # Fallback stdlib-only senza tzdata.
    local = utc_dt.replace(tzinfo=None) + timedelta(hours=_rome_offset_hours(utc_dt.replace(tzinfo=None)))
    return local.strftime("%Y-%m-%d")


def local_hour(epoch_s: int) -> tuple[str, int]:
    """(giorno locale, ora locale 0..23) di un'ora espressa in epoch SECONDI.

    Gli offset di Europe/Rome sono interi, quindi un'ora UTC e' interamente
    dentro un'ora locale e dentro un solo giorno locale: nessun bucket a cavallo.
    """
    utc_dt = datetime.fromtimestamp(int(epoch_s), tz=timezone.utc)
    if _ROME is not None:
        local = utc_dt.astimezone(_ROME)
        return local.strftime("%Y-%m-%d"), local.hour
    naive = utc_dt.replace(tzinfo=None)
    local = naive + timedelta(hours=_rome_offset_hours(naive))
    return local.strftime("%Y-%m-%d"), local.hour


def local_day_bounds(day: str) -> tuple[int, int]:
    """[00:00, 00:00 del giorno dopo) locali, in epoch ms. Calcolato UNA volta
    per run: chiamare rome_day() per ogni punto salvato sarebbe O(n^2) e could
    rendere il workflow troppo lento con ~4.000 stazioni x 144 punti."""
    y, mo, dd = (int(x) for x in day.split("-"))
    if _ROME is not None:
        start = datetime(y, mo, dd, tzinfo=_ROME)
        end = start + timedelta(days=1)
    else:
        naive = datetime(y, mo, dd)
        nxt = naive + timedelta(days=1)
        start = naive.replace(tzinfo=timezone(timedelta(hours=_rome_offset_hours(naive))))
        end = nxt.replace(tzinfo=timezone(timedelta(hours=_rome_offset_hours(nxt))))
    return int(round(start.timestamp() * 1000)), int(round(end.timestamp() * 1000))


def day_start_iso(day: str) -> str:
    """`2026-11-18` -> `2026-11-18T00:00:00+01:00` (inizio del giorno LOCALE)."""
    lo, _ = local_day_bounds(day)
    utc_dt = datetime.fromtimestamp(lo / 1000.0, tz=timezone.utc)
    offset = _rome_offset_hours(utc_dt.replace(tzinfo=None))
    return utc_dt.astimezone(timezone(timedelta(hours=offset))).isoformat()


def parse_obs_ms(value: object) -> int | None:
    """'2026-11-17T13:07:59Z' (o ISO con offset) -> epoch ms UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return int(round(dt.timestamp() * 1000))


def iso_utc(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_local_ms(epoch_ms: int) -> str:
    """epoch ms -> ISO 8601 Europe/Rome con offset, secondi azzerati.

    Stessa forma di `rain_24h_since` (es. ``2026-11-17T08:00:00+01:00``): e' la
    forma con cui il blob dichiara `window_start`/`window_end`, verificabile a
    occhio contro `generated_at` (UTC).
    """
    utc_dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    if _ROME is not None:
        return utc_dt.astimezone(_ROME).replace(second=0, microsecond=0).isoformat()
    naive = utc_dt.replace(tzinfo=None, second=0, microsecond=0)
    offset = _rome_offset_hours(naive)
    return naive.replace(tzinfo=timezone(timedelta(hours=offset))).isoformat()


def window_slots(now_ms: int, hours: int) -> list[tuple[int, str, int]]:
    """Slot orari della finestra scorrevole che termina a `now_ms`.

    Uno slot entra se il suo INIZIO cade in [now - hours h, now): a un'ora
    tonda sono `hours` slot pieni (il piu' vecchio parte esattamente al bordo
    della finestra), a meta' ora sono `hours` slot con il primo gia' dentro la
    finestra e l'ultimo (ora corrente) parziale. Per questo la somma copre fra
    `hours - 1` e `hours` ore reali: e' il limite dichiarato dell'aggregato
    orario, non una stima delle ore mancanti.
    """
    start_limit = now_ms - hours * 3_600_000
    now_hour = (now_ms // 3_600_000) * 3_600_000
    slots: list[tuple[int, str, int]] = []
    seen: set[tuple[str, int]] = set()
    for back in range(hours + 1):
        start = now_hour - back * 3_600_000
        if start < start_limit or start >= now_ms:
            continue
        day, hour = local_hour(start // 1000)
        if (day, hour) in seen:
            # Cambio ora legale: l'ora locale si ripete e il bucket ha una sola
            # chiave. Si conta UNA volta (limite dichiarato, niente doppio conteggio).
            continue
        seen.add((day, hour))
        slots.append((start, day, hour))
    return slots


def _bucket_for(record: dict, day: str, current_day: str, prev_day: str | None, name: str) -> dict:
    """Il dizionario bucket giusto per un giorno: corrente o `*_prev`."""
    if day == current_day:
        bucket = record.get(name)
    elif prev_day is not None and day == prev_day:
        bucket = record.get(name + PREV_SUFFIX)
    else:
        return {}
    return bucket if isinstance(bucket, dict) else {}


def _has_bucket(record: dict, name: str) -> bool:
    """True se esiste almeno un bucket (corrente o precedente) per la grandezza."""
    for key in (name, name + PREV_SUFFIX):
        bucket = record.get(key)
        if isinstance(bucket, dict) and bucket:
            return True
    return False


def _window_values(
    record: dict,
    slots: list[tuple[int, str, int]],
    current_day: str,
    prev_day: str | None,
    name: str,
    index: int | None = None,
) -> list[tuple[int, float]]:
    """[(inizio slot, valore)] degli slot della finestra che hanno un numero.

    `index` sceglie la posizione nelle forme a lista: 0 = media (th/rhh/dwh),
    1 = raffica (wh). Con `index=None` il bucket e' un numero nudo (rh).
    """
    out: list[tuple[int, float]] = []
    for start, day, hour in slots:
        entry = _bucket_for(record, day, current_day, prev_day, name).get(str(hour))
        value = entry
        if index is not None:
            if not isinstance(entry, list) or len(entry) <= index:
                continue
            value = entry[index]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append((start, float(value)))
    return out


def _coverage(present: int, hours: int) -> float:
    """Copertura dichiarata: slot con dato / ore nominali, limitata a 1."""
    if hours <= 0:
        return 0.0
    return round(min(1.0, present / float(hours)), 3)


# --------------------------------------------------------------------------- #
# Tupla
# --------------------------------------------------------------------------- #
def _num(value: object, decimals: int = 2) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, decimals)


def tuple_for(station: dict, obs_ms: int) -> list:
    """Una riga [epochMs, ...] da un record di latest.json."""
    precip = _num(station.get("precipitation"))
    period = _num(station.get("precipitation_period_s"), 0)
    rain = None
    if precip is not None and period is not None and period > 0:
        rain = round(precip * 3600.0 / period, 2)
    return [
        obs_ms,
        _num(station.get("temperature_2m")),
        _num(station.get("relative_humidity_2m")),
        _num(station.get("wind_speed_10m")),
        _num(station.get("wind_direction_10m")),
        rain,
        int(period) if period is not None else None,
        _num(station.get("dew_point_2m")),
        _num(station.get("station_pressure_hPa")),
        _num(station.get("wind_gust_10m")),
        _num(station.get("snow_depth_m"), 3),
    ]


def _valid_point(p: object) -> bool:
    """Solo forma: il controllo del giorno e' per range (vedi local_day_bounds)."""
    return (
        isinstance(p, list)
        and len(p) == TUPLE_LEN
        and isinstance(p[I_TIME], int)
    )


# --------------------------------------------------------------------------- #
# Bucket orari
# --------------------------------------------------------------------------- #
def _merge_hourly(
    previous: dict, payload: object, day: str, wrap: bool = False
) -> dict:
    """{ora locale: valore} aggiornato con le ore del run corrente.

    `wrap=True` scrive `[valore]` invece del valore nudo: i bucket `rhh`/`dwh`
    hanno la forma a lista di un elemento, come `th` ne ha tre.

    REGOLA DI DEDUP: il bucket di un'ora viene SOSTITUITO, non sommato. Ogni run
    ricalcola quell'ora dalla serie completa di `latest.json` (una cumulata su
    [inizio ora, fine ora] o un massimo sull'ora), quindi il risultato e'
    idempotente: due run nella stessa ora danno lo stesso numero dell'ultimo, e
    l'ora in corso si completa da sola nei run successivi. Le ore che il run non
    rivede piu' (> 4 h di serie) restano quelle salvate.
    """
    out = dict(previous) if isinstance(previous, dict) else {}
    if not isinstance(payload, list):
        return out
    for row in payload:
        if not isinstance(row, list) or len(row) < 2 or not isinstance(row[0], int):
            continue
        hour_day, hour = local_hour(row[0])
        if hour_day != day:
            continue  # ora di un altro giorno locale (a cavallo di mezzanotte)
        value = _num(row[1], 1)
        if value is None:
            continue
        out[str(hour)] = [value] if wrap else value
    return out


def _merge_temp(previous: dict, payload: object, day: str) -> dict:
    """{ora locale: [media, minima, massima]} dalle triple orarie del run.

    Il bucket `th` porta TRE numeri (la temperatura non e' solo una media): la
    media pesata sul tempo e i due estremi osservati nell'ora. Stessa regola di
    dedup degli altri bucket: l'ora viene SOSTITUITA, mai sommata.
    """
    out = dict(previous) if isinstance(previous, dict) else {}
    if not isinstance(payload, list):
        return out
    hours: dict[int, list] = {}
    for row in payload:
        if not isinstance(row, list) or len(row) < 2 or not isinstance(row[0], int):
            continue
        hour_day, hour = local_hour(row[0])
        if hour_day != day:
            continue
        mean = _num(row[1], 1)
        if mean is None:
            continue
        hours[hour] = [
            mean,
            _num(row[2], 1) if len(row) > 2 else None,
            _num(row[3], 1) if len(row) > 3 else None,
        ]
    for hour, triple in hours.items():
        merged = _temp_triplet(out.get(str(hour)))
        for index in range(3):
            if triple[index] is not None:
                merged[index] = triple[index]
        out[str(hour)] = merged
    return out


def _temp_triplet(current: object) -> list:
    """Tripla [media, minima, massima] di un bucket, con i null espliciti."""
    triple = [None, None, None]
    if isinstance(current, list):
        for index in range(min(3, len(current))):
            if isinstance(current[index], (int, float)):
                triple[index] = round(float(current[index]), 1)
    return triple


def _day_extremes(bucket: object, current_hour: int) -> tuple[dict[int, tuple], float]:
    """{ora locale: (minima, massima)} del giorno e copertura fino all'ora corrente.

    Il denominatore e' `current_hour + 1`: le ore TRASCORSE della giornata locale
    (a mezzanotte e' 1). Un'ora senza massimo/minimo validi non conta. La
    copertura e' limitata a 1 (nei giorni di cambio ora legale il numero di ore
    locali non e' 24; il contatore resta nominale e la limitazione e' dichiarata).
    """
    hours: dict[int, tuple] = {}
    if isinstance(bucket, dict):
        for key, value in bucket.items():
            try:
                hour = int(key)
            except (TypeError, ValueError):
                continue
            if not 0 <= hour <= 23:
                continue
            if not isinstance(value, list) or len(value) < 3:
                continue
            low, high = value[1], value[2]
            if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                hours[hour] = (float(low), float(high))
    elapsed = max(1, min(24, current_hour + 1))
    return hours, round(min(1.0, len(hours) / elapsed), 3)



def _buckets_from_points(points: object, index: int) -> dict:
    """Bucket orari [media, minima, massima] ricavati dai PUNTI della giornata.

    Serve SOLO all'avvio a freddo: il blob precedente alla 1.3.0.61 non ha `th`/
    `rhh`, e senza questo passaggio il massimo/minimo della giornata resterebbe
    null per 12 ore (copertura < 50%). Non e' un secondo calcolo della serie nel
    run normale: si chiama una volta per stazione e si aggiungono SOLO le ore che
    il run corrente non ha prodotto; le ore prodotte dalla serie (media pesata
    sul tempo) vincono. La media delle ore recuperate e' la media dei punti, non
    la media pesata: la differenza e' dichiarata e riguarda solo l'avvio.
    """
    groups: dict[int, list[float]] = {}
    if not isinstance(points, list):
        return {}
    for point in points:
        if not isinstance(point, list) or len(point) <= index:
            continue
        value = point[index]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        try:
            _day, hour = local_hour(int(point[I_TIME]) // 1000)
        except (TypeError, ValueError, IndexError):
            continue
        groups.setdefault(hour, []).append(float(value))
    return {
        str(hour): [
            round(sum(values) / len(values), 1),
            round(min(values), 1),
            round(max(values), 1),
        ]
        for hour, values in groups.items()
    }


def _sanitize_temp_points(record: dict) -> tuple[int, set[int]]:
    """Azzera SOLO la temperatura dei punti che non passano il gate del rate.

    Perche' serve anche qui: il fetcher pulisce le osservazioni NUOVE, ma i
    `pts` e i bucket `th` gia' salvati nel blob vengono da run precedenti. Il
    caso misurato il 2026-11-23 (Palma di Montechiaro) e' esattamente questo:
    la serie del sensore e' rotta da ore e i valori 40-52 degC erano gia' nel
    blob. senza questa pulizia il massimo falso resterebbe pubblicato fino a
    mezzanotte locale.

    Il gate tocca `p[I_TEMP]` e nient'altro: la stessa osservazione puo' avere
    pioggia, vento e umidita' validi, e azzerarli per una temperatura rotta
    sarebbe una perdita non giustificata. Un punto con temperatura nulla resta
    nel grafico per le altre grandezze.

    Ritorna `(campioni_scartati, ore_con_tutti_i_punti_scartati)`.
    """
    pts = record.get("pts")
    if not isinstance(pts, list) or len(pts) < 2:
        return 0, set()
    samples: list[tuple[datetime, float]] = []
    input_index: dict[int, int] = {}
    for position, point in enumerate(pts):
        if not _valid_point(point):
            continue
        value = point[I_TEMP]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        try:
            ref = datetime.fromtimestamp(int(point[I_TIME]) / 1000.0, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            continue
        input_index[len(samples)] = position
        samples.append((ref, float(value)))
    if len(samples) < 2:
        return 0, set()
    _kept, rejected, rejected_indexes = qc_temperature_points(samples)
    rejected_hours: set[int] = set()
    for sample_index in rejected_indexes:
        position = input_index[sample_index]
        pts[position][I_TEMP] = None
        try:
            _day, hour = local_hour(int(pts[position][I_TIME]) // 1000)
        except (TypeError, ValueError, IndexError):
            continue
        rejected_hours.add(hour)
    return rejected, rejected_hours


def _repair_temp_buckets(
    record: dict,
    rejected_hours: set[int],
    tolerance: float = TEMP_DAILY_BUCKET_TOLERANCE_C,
) -> int:
    """Riconcilia i bucket `th` salvati con i punti (puliti) della stessa ora.

    Regola dichiarata: `max_day <= max(serie oraria) + tolleranza`. Se il
    massimo (o il minimo) salvato si discosta di oltre `tolerance` degC dagli
    estremi dei punti della stessa ora, il bucket e' incoerente con la serie da
    cui doveva derivare e viene RICOSTRUITO dai punti (media semplice, minima,
    massima). Se l'ora non ha punti accettati perche' sono stati tutti scartati
    dal gate, il bucket viene ELIMINATO: non e' corroborabile e lasciarlo
    significherebbe pubblicare un massimo che nessun campione sostiene.

    Perche' non ricostruire sempre: i punti sono un campione ogni run (~10 min),
    la serie originale puo' essere piu' fitta e il bucket e' una media pesata
    sul tempo. Si tocca solo cio' che e' palesemente incoerente.

    Ritorna il numero di bucket ricostruiti o eliminati.
    """
    bucket = record.get("th")
    if not isinstance(bucket, dict) or not bucket:
        return 0
    by_hour: dict[int, list[float]] = {}
    for point in record.get("pts") or []:
        if not _valid_point(point):
            continue
        value = point[I_TEMP]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        try:
            _day, hour = local_hour(int(point[I_TIME]) // 1000)
        except (TypeError, ValueError, IndexError):
            continue
        by_hour.setdefault(hour, []).append(float(value))
    repaired = 0
    for key in list(bucket):
        try:
            hour = int(key)
        except (TypeError, ValueError):
            continue
        triple = _temp_triplet(bucket[key])
        values = by_hour.get(hour)
        if values:
            low, high = min(values), max(values)
            inconsistent = (
                (triple[2] is not None and triple[2] > high + tolerance)
                or (triple[1] is not None and triple[1] < low - tolerance)
            )
            if inconsistent:
                bucket[key] = [
                    round(sum(values) / len(values), 1),
                    round(low, 1),
                    round(high, 1),
                ]
                repaired += 1
        elif hour in rejected_hours:
            del bucket[key]
            repaired += 1
    return repaired


def _rain_window(
    record: dict,
    slots: list[tuple[int, str, int]],
    hours: int,
    day: str,
    prev_day: str | None,
    now_ms: int,
) -> dict:
    """Pezzi della cumulata di pioggia su una finestra scorrevole."""
    values = _window_values(record, slots, day, prev_day, "rh")
    coverage = _coverage(len(values), hours)
    return {
        "mm": round(sum(value for _start, value in values), 1) if values else None,
        "null_reason": None if values else "no_buckets_in_window",
        "partial": coverage < MIN_COVERAGE_FRAC,
        "coverage": coverage,
        "start": iso_local_ms(values[-1][0]) if values else None,
        "end": iso_local_ms(now_ms),
    }


def _wind_window(
    record: dict,
    slots: list[tuple[int, str, int]],
    hours: int,
    day: str,
    prev_day: str | None,
) -> tuple[dict, dict]:
    """(vento medio, raffica massima) su una finestra scorrevole."""
    speeds = _window_values(record, slots, day, prev_day, "wh", 0)
    gusts = _window_values(record, slots, day, prev_day, "wh", 1)
    speed = {
        "mean": (
            round(sum(value for _start, value in speeds) / len(speeds), 1)
            if speeds else None
        ),
        "partial": _coverage(len(speeds), hours) < MIN_COVERAGE_FRAC if speeds else None,
        "coverage": _coverage(len(speeds), hours) if speeds else None,
    }
    gust = {
        "max": round(max(value for _start, value in gusts), 1) if gusts else None,
        "partial": _coverage(len(gusts), hours) < MIN_COVERAGE_FRAC if gusts else None,
        "coverage": _coverage(len(gusts), hours) if gusts else None,
    }
    return speed, gust


def _derived(
    record: dict,
    now_ms: int,
    current_hour: int,
    day: str,
    prev_day: str | None,
    slots_6: list[tuple[int, str, int]],
    slots_24: list[tuple[int, str, int]],
) -> None:
    """Aggiunge a `record` le finestre SCORREVOLI a 6 h e 24 h.

    Le finestre terminano a `now_ms` e possono attraversare la mezzanotte: gli
    slot vengono dal giorno corrente e da `*_prev`. `*_partial` e' true quando
    la copertura e' sotto il 50% delle ore nominali; ogni finestra di pioggia
    dichiara inizio effettivo, fine e copertura.
    """
    if _has_bucket(record, "rh"):
        w6 = _rain_window(record, slots_6, WINDOW_6H, day, prev_day, now_ms)
        w24 = _rain_window(record, slots_24, HOURS_IN_DAY, day, prev_day, now_ms)
        record["rain_6h_mm"] = w6["mm"]
        record["rain_6h_partial"] = w6["partial"]
        record["rain_6h_coverage_frac"] = w6["coverage"]
        record["rain_6h_window_start"] = w6["start"]
        record["rain_6h_window_end"] = w6["end"]
        if w6["null_reason"]:
            record["rain_6h_null_reason"] = w6["null_reason"]
        record["rain_24h_mm"] = w24["mm"]
        record["rain_24h_partial"] = w24["partial"]
        record["rain_24h_coverage_frac"] = w24["coverage"]
        record["rain_24h_window_start"] = w24["start"]
        record["rain_24h_window_end"] = w24["end"]
        if w24["null_reason"]:
            record["rain_24h_null_reason"] = w24["null_reason"]
        # Nome storico: prima era la mezzanotte, ora e' l'inizio EFFETTIVO
        # della finestra di 24 h (alias di rain_24h_window_start).
        record["rain_24h_since"] = w24["start"]

    if _has_bucket(record, "wh"):
        speed6, gust6 = _wind_window(record, slots_6, WINDOW_6H, day, prev_day)
        speed24, gust24 = _wind_window(record, slots_24, HOURS_IN_DAY, day, prev_day)
        if speed6["mean"] is not None:
            record["wind_6h_ms"] = speed6["mean"]
            record["wind_6h_partial"] = speed6["partial"]
            record["wind_6h_coverage_frac"] = speed6["coverage"]
        if speed24["mean"] is not None:
            record["wind_24h_ms"] = speed24["mean"]
            record["wind_24h_partial"] = speed24["partial"]
            record["wind_24h_coverage_frac"] = speed24["coverage"]
        if gust6["max"] is not None:
            record["gust_max_6h_ms"] = gust6["max"]
            record["gust_max_6h_partial"] = gust6["partial"]
            record["gust_max_6h_coverage_frac"] = gust6["coverage"]
        if gust24["max"] is not None:
            record["gust_max_24h_ms"] = gust24["max"]
            record["gust_max_24h_partial"] = gust24["partial"]
            record["gust_max_24h_coverage_frac"] = gust24["coverage"]

    # Medie 6 h / 24 h dai bucket orari (media delle medie orarie: ogni bucket
    # e' gia' una media pesata sulla sua ora). Con copertura parziale la media
    # resta quella delle ore disponibili e il flag lo dichiara: nessun valore
    # inventato per le ore mancanti.
    for bucket_name, label, unit in (
        ("th", "temp", "c"),
        ("rhh", "rh", "pct"),
        ("dwh", "dew", "c"),
    ):
        if not _has_bucket(record, bucket_name):
            continue
        for slots, hours in ((slots_6, WINDOW_6H), (slots_24, HOURS_IN_DAY)):
            values = _window_values(record, slots, day, prev_day, bucket_name, 0)
            if not values:
                continue
            mean = round(sum(value for _start, value in values) / len(values), 1)
            record[f"{label}_{hours}h_{unit}"] = mean
            record[f"{label}_{hours}h_partial"] = (
                _coverage(len(values), hours) < MIN_COVERAGE_FRAC
            )

    # Massimo e minimo DELLA GIORNATA (dalla mezzanotte locale): si leggono dai
    # bucket orari gia' calcolati (`th`/`rhh` = [media, minima, massima]), non si
    # ripercorre la serie. L'ora del picco e' quella del PRIMO bucket che lo
    # raggiunge (a parita' di valore vince l'ora piu' bassa).
    for bucket_name, label, unit, decimals in (
        ("th", "temp", "c", 1),
        ("rhh", "rh", "pct", 0),
    ):
        bucket = record.get(bucket_name)
        if not isinstance(bucket, dict) or not bucket:
            # Nessun bucket orario: se pero' la stazione ha osservazioni di
            # quella grandezza oggi (punti di `pts`), il motivo va dichiarato;
            # se la grandezza non esiste affatto, non si scrivono campi finti.
            index = I_TEMP if label == "temp" else I_RH
            has_observation = any(
                isinstance(point, list)
                and len(point) > index
                and isinstance(point[index], (int, float))
                for point in record.get("pts") or []
            )
            if not has_observation:
                continue
            record[f"{label}_day_coverage_frac"] = 0.0
            record[f"{label}_day_partial"] = True
            record[f"{label}_max_day_{unit}"] = None
            record[f"{label}_min_day_{unit}"] = None
            record[f"{label}_max_day_hour"] = None
            record[f"{label}_min_day_hour"] = None
            record[f"{label}_day_null_reason"] = "no_points_today"
            continue
        hours, coverage = _day_extremes(bucket, current_hour)
        record[f"{label}_day_coverage_frac"] = coverage
        # La giornata e' parziale se non sono passate tutte le ore (prima delle
        # 23) o se la copertura e' sotto il 90%. A inizio giornata e' la norma.
        record[f"{label}_day_partial"] = bool(coverage < 0.9 or current_hour < 23)
        reason = None
        if not hours:
            reason = "no_points_today"
        elif coverage < 0.5:
            reason = "coverage_below_50pct"
        if reason:
            record[f"{label}_max_day_{unit}"] = None
            record[f"{label}_min_day_{unit}"] = None
            record[f"{label}_max_day_hour"] = None
            record[f"{label}_min_day_hour"] = None
            record[f"{label}_day_null_reason"] = reason
            continue
        high = max(value[1] for value in hours.values())
        low = min(value[0] for value in hours.values())
        high_hour = min(hour for hour, value in hours.items() if value[1] == high)
        low_hour = min(hour for hour, value in hours.items() if value[0] == low)
        record[f"{label}_max_day_{unit}"] = round(high, decimals)
        record[f"{label}_min_day_{unit}"] = round(low, decimals)
        record[f"{label}_max_day_hour"] = str(high_hour)
        record[f"{label}_min_day_hour"] = str(low_hour)


def _wind_pair(current: object) -> list:
    """Coppia [vento medio, raffica max] di un bucket, con i null espliciti."""
    pair = [None, None]
    if isinstance(current, list):
        for index in range(min(2, len(current))):
            if isinstance(current[index], (int, float)):
                pair[index] = round(float(current[index]), 1)
    return pair


def _merge_wind(previous: dict, speed: object, gust: object, day: str) -> dict:
    """{ora locale: [vento medio, raffica]} dalle due serie orarie del run."""
    out = dict(previous) if isinstance(previous, dict) else {}
    hours: dict[int, list] = {}
    for payload, position in ((gust, 1), (speed, 0)):
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, list) or len(row) < 2 or not isinstance(row[0], int):
                continue
            hour_day, hour = local_hour(row[0])
            if hour_day != day:
                continue
            value = _num(row[1], 1)
            if value is None:
                continue
            pair = hours.setdefault(hour, [None, None])
            pair[position] = value
    for hour, pair in hours.items():
        merged = _wind_pair(out.get(str(hour)))
        for index in range(2):
            if pair[index] is not None:
                merged[index] = pair[index]
        out[str(hour)] = merged
    return out


def _copy_bucket(name: str, bucket: object) -> dict:
    """Copia tipizzata di un dizionario bucket (scarta le voci malformate).

    Serve al passaggio di giorno: `rh`/`wh`/`th`/`rhh`/`dwh` del giorno appena
    finito diventano `*_prev` con la STESSA forma che i calcoli si aspettano.
    Nessun valore nuovo: solo copia e normalizzazione di forma.
    """
    if not isinstance(bucket, dict) or not bucket:
        return {}
    if name == "rh":
        return {
            str(k): float(v)
            for k, v in bucket.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    if name == "wh":
        return {str(k): _wind_pair(v) for k, v in bucket.items()}
    if name in ("th", "rhh"):
        return {str(k): _temp_triplet(v) for k, v in bucket.items()}
    if name == "dwh":
        return {
            str(k): [round(float(v[0]), 1)]
            for k, v in bucket.items()
            if isinstance(v, list) and v and isinstance(v[0], (int, float))
        }
    return {}


# --------------------------------------------------------------------------- #
# Aggiornamento del blob
# --------------------------------------------------------------------------- #
def build(latest: dict, previous: dict | None, now_ms: int, max_points: int = MAX_POINTS_PER_STATION) -> dict:
    """Restituisce il nuovo documento. `previous` e' il file su disco (o None)."""
    day = rome_day(now_ms)
    lo, hi = local_day_bounds(day)
    yesterday = rome_day(lo - 1)
    prev_day = previous.get("day") if isinstance(previous, dict) else None
    prev_prev_day = previous.get("prev_day") if isinstance(previous, dict) else None
    prev_stations = previous.get("stations", {}) if isinstance(previous, dict) else {}
    if not isinstance(prev_stations, dict):
        prev_stations = {}
    current_hour = local_hour(now_ms // 1000)[1]
    slots_6 = window_slots(now_ms, WINDOW_6H)
    slots_24 = window_slots(now_ms, HOURS_IN_DAY)
    # Giorno a cui appartengono i bucket `*_prev`: ieri se il blob e' di ieri
    # (cambio di giorno), ieri anche se il blob e' gia' di oggi (coda portata
    # avanti da un run precedente). Blob piu' vecchio di un giorno -> nessuna
    # coda: il buco non si riempie con ore inventate.
    carried_prev_day = yesterday if prev_day in (day, yesterday) else None

    stations: dict[str, dict] = {}
    if prev_day in (day, yesterday):
        # Conserva le serie delle stazioni gia' presenti (anche quelle che questo
        # run non ripubblica: il loro punto di oggi non deve sparire).
        for sid, rec in prev_stations.items():
            if not isinstance(rec, dict):
                continue
            pts = [
                p for p in (rec.get("pts") or [])
                if _valid_point(p) and lo <= p[I_TIME] < hi
            ]
            kept = {"name": str(rec.get("name") or ""), "pts": pts}
            if prev_day == day:
                # Bucket del giorno corrente.
                for name in BUCKET_NAMES:
                    copied = _copy_bucket(name, rec.get(name))
                    if copied:
                        kept[name] = copied
                if prev_prev_day == yesterday:
                    # Coda di ieri gia' portata avanti: resta finche' serve alla
                    # finestra di 24 h (la potatura avviene dopo i derivati).
                    for name in BUCKET_NAMES:
                        copied = _copy_bucket(name, rec.get(name + PREV_SUFFIX))
                        if copied:
                            kept[name + PREV_SUFFIX] = copied
            else:
                # Cambio di giorno: i bucket di ieri diventano `*_prev`. I PUNTI
                # di ieri no: il grafico riparte da mezzanotte.
                for name in BUCKET_NAMES:
                    copied = _copy_bucket(name, rec.get(name))
                    if copied:
                        kept[name + PREV_SUFFIX] = copied
            # Il conteggio dei campioni termici scartati e' cumulativo della
            # giornata: i punti gia' azzerati non vengono ricontati, ma il
            # numero resta visibile nel blob.
            if isinstance(rec.get("temp_qc_rejected"), int) and rec["temp_qc_rejected"] > 0:
                kept["temp_qc_rejected"] = rec["temp_qc_rejected"]
            if pts or any(key.endswith(PREV_SUFFIX) for key in kept):
                stations[sid] = kept

    latest_stations = latest.get("stations", [])
    if not isinstance(latest_stations, list):
        latest_stations = []

    added = 0
    replaced = 0
    for st in latest_stations:
        if not isinstance(st, dict):
            continue
        sid = st.get("id")
        if not isinstance(sid, str) or not sid:
            continue
        obs_ms = parse_obs_ms(st.get("obs_time"))
        if obs_ms is None:
            continue
        # Solo osservazioni della giornata corrente: niente punto di ieri nel blob di oggi.
        # Range precalcolato (equivalente a rome_day(obs_ms) == day, ma O(1)).
        if not (lo <= obs_ms < hi):
            continue
        rec = stations.get(sid)
        if rec is None:
            rec = {"name": str(st.get("name") or ""), "pts": []}
            stations[sid] = rec
        elif not rec.get("name") and st.get("name"):
            rec["name"] = str(st["name"])
        pts = rec["pts"]
        new_tuple = tuple_for(st, obs_ms)
        hit = -1
        for i, p in enumerate(pts):
            if p[I_TIME] == obs_ms:
                hit = i
                break
        if hit >= 0:
            pts[hit] = new_tuple          # dedup per obs_time: sostituisci
            replaced += 1
        else:
            pts.append(new_tuple)
            added += 1
        pts.sort(key=lambda p: p[I_TIME])
        if len(pts) > max_points:
            del pts[: len(pts) - max_points]

        # Bucket orari: SOSTITUZIONE per ora (vedi _merge_hourly).
        rh = _merge_hourly(rec.get("rh") or {}, st.get("rain_hourly"), day)
        if rh:
            rec["rh"] = rh
        wh = _merge_wind(rec.get("wh") or {}, st.get("wind_hourly"), st.get("gust_hourly"), day)
        if wh:
            rec["wh"] = wh
        th = _merge_temp(rec.get("th") or {}, st.get("temp_hourly"), day)
        if th:
            rec["th"] = th
        rhh = _merge_temp(rec.get("rhh") or {}, st.get("rh_hourly"), day)
        if rhh:
            rec["rhh"] = rhh
        dwh = _merge_hourly(rec.get("dwh") or {}, st.get("dew_hourly"), day, wrap=True)
        if dwh:
            rec["dwh"] = dwh

        if prev_day == yesterday:
            # Il run di mezzanotte puo' portare le ULTIME ore di ieri (la serie
            # di latest.json copre ~3 h): si completano i bucket `*_prev` con la
            # stessa regola di sostituzione per ora, senza mai sommare.
            prev_rh = _merge_hourly(rec.get("rh_prev") or {}, st.get("rain_hourly"), prev_day)
            if prev_rh:
                rec["rh_prev"] = prev_rh
            prev_wh = _merge_wind(
                rec.get("wh_prev") or {}, st.get("wind_hourly"),
                st.get("gust_hourly"), prev_day,
            )
            if prev_wh:
                rec["wh_prev"] = prev_wh
            prev_th = _merge_temp(rec.get("th_prev") or {}, st.get("temp_hourly"), prev_day)
            if prev_th:
                rec["th_prev"] = prev_th
            prev_rhh = _merge_temp(rec.get("rhh_prev") or {}, st.get("rh_hourly"), prev_day)
            if prev_rhh:
                rec["rhh_prev"] = prev_rhh
            prev_dwh = _merge_hourly(
                rec.get("dwh_prev") or {}, st.get("dew_hourly"), prev_day, wrap=True
            )
            if prev_dwh:
                rec["dwh_prev"] = prev_dwh

    for sid, rec in stations.items():
        if not isinstance(rec, dict):
            continue
        # GATE DI PLAUSIBILITA' SU TUTTA LA GIORNATA (non solo sul run corrente):
        # azzera la temperatura dei punti incoerenti, poi riconcilia i bucket
        # `th` salvati con i punti ripuliti. Va PRIMA del backfill, cosi' le ore
        # ricostruite usano solo campioni accettati.
        rejected_points, rejected_hours = _sanitize_temp_points(rec)
        if rejected_points:
            rec["temp_qc_rejected"] = int(rec.get("temp_qc_rejected") or 0) + rejected_points
        _repair_temp_buckets(rec, rejected_hours)
        # Avvio a freddo: completa i bucket termici con le ore gia' presenti nei
        # punti (vedi `_buckets_from_points`). Le ore del run corrente vincono.
        for bucket, index in (("th", I_TEMP), ("rhh", I_RH)):
            current = rec.get(bucket) if isinstance(rec.get(bucket), dict) else {}
            backfill = _buckets_from_points(rec.get("pts"), index)
            if not current:
                if backfill:
                    rec[bucket] = backfill
                continue
            added = False
            for hour, triple in backfill.items():
                if hour not in current:
                    current[hour] = triple
                    added = True
            if added:
                rec[bucket] = current
        _derived(rec, now_ms, current_hour, day, carried_prev_day, slots_6, slots_24)
        # Potatura della coda: le ore di ieri <= ora locale corrente non possono
        # piu' entrare in una finestra di 24 h (ne' in quella di 6 h). Tenerle
        # gonfierebbe il blob senza cambiare nessun valore. La potatura e'
        # monotona (l'ora corrente cresce), quindi un run saltato non la rende
        # sbagliata: servono sempre le ore successive.
        for name in BUCKET_NAMES:
            key = name + PREV_SUFFIX
            bucket = rec.get(key)
            if not isinstance(bucket, dict):
                continue
            pruned = {}
            for hour_key, value in bucket.items():
                try:
                    hour = int(hour_key)
                except (TypeError, ValueError):
                    continue
                if hour >= current_hour:
                    pruned[str(hour)] = value
            if pruned:
                rec[key] = pruned
            else:
                rec.pop(key, None)

    has_prev = any(
        isinstance(rec.get(name + PREV_SUFFIX), dict) and rec.get(name + PREV_SUFFIX)
        for rec in stations.values()
        for name in BUCKET_NAMES
    )
    return {
        "schema": SCHEMA,
        "generated_at": iso_utc(now_ms),
        "day": day,
        **({"prev_day": carried_prev_day} if has_prev and carried_prev_day else {}),
        "timezone": TZ_NAME,
        "window_policy": {
            "6h": {
                "hours": WINDOW_6H,
                "slot_minutes": 60,
                "window_start": iso_local_ms(now_ms - WINDOW_6H * 3_600_000),
                "window_end": iso_local_ms(now_ms),
                "partial_below_coverage": MIN_COVERAGE_FRAC,
            },
            "24h": {
                "hours": HOURS_IN_DAY,
                "slot_minutes": 60,
                "window_start": iso_local_ms(now_ms - HOURS_IN_DAY * 3_600_000),
                "window_end": iso_local_ms(now_ms),
                "partial_below_coverage": MIN_COVERAGE_FRAC,
            },
        },
        "note": NOTE,
        "max_points_per_station": max_points,
        "stations": stations,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--latest", required=True, help="data/stations/latest.json")
    ap.add_argument("--out", required=True, help="data/stations/daily_series.json")
    ap.add_argument("--now-ms", type=int, default=None,
                    help="epoch ms del run (SOLO per test/clock simulato); default = adesso")
    ap.add_argument("--max-points", type=int, default=MAX_POINTS_PER_STATION)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    now_ms = args.now_ms if args.now_ms is not None else int(round(time.time() * 1000))
    latest_path = Path(args.latest)
    out_path = Path(args.out)

    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] latest.json non leggibile: {exc}", file=sys.stderr)
        return 3
    if not isinstance(latest, dict) or not isinstance(latest.get("stations"), list) or not latest["stations"]:
        print("[ERROR] latest.json senza stazioni: non sovrascrivo il blob buono.", file=sys.stderr)
        return 4

    previous = None
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(previous, dict) and previous.get("schema") not in SCHEMA_READABLE:
                print(
                    f"[WARN] schema precedente {previous.get('schema')!r} non noto: "
                    "si riparte dai soli punti del giorno (bucket azzerati)",
                    file=sys.stderr,
                )
                previous = {k: v for k, v in previous.items() if k != "stations"}
                previous["stations"] = {}
        except Exception:
            previous = None  # blob corrotto: riparte come giorno nuovo

    doc = build(latest, previous, now_ms, args.max_points)
    payload = json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(out_path)

    if not args.quiet:
        pts = sum(len(r.get("pts", [])) for r in doc["stations"].values())
        rh = sum(len(r.get("rh", {})) for r in doc["stations"].values())
        wh = sum(len(r.get("wh", {})) for r in doc["stations"].values())
        th = sum(len(r.get("th", {})) for r in doc["stations"].values())
        rhh = sum(len(r.get("rhh", {})) for r in doc["stations"].values())
        dwh = sum(len(r.get("dwh", {})) for r in doc["stations"].values())
        prev_rh = sum(len(r.get("rh_prev", {})) for r in doc["stations"].values())
        prev_wh = sum(len(r.get("wh_prev", {})) for r in doc["stations"].values())
        wet = sum(1 for r in doc["stations"].values() if r.get("rain_24h_mm"))
        complete6 = sum(
            1 for r in doc["stations"].values()
            if r.get("rain_6h_partial") is False
        )
        complete24 = sum(
            1 for r in doc["stations"].values()
            if r.get("rain_24h_partial") is False
        )
        print(f"[OK] day={doc['day']} prev_day={doc.get('prev_day')} "
              f"stations={len(doc['stations'])} points={pts} "
              f"rain_buckets={rh} wind_buckets={wh} temp_buckets={th} "
              f"rh_buckets={rhh} dew_buckets={dwh} "
              f"prev_rain_buckets={prev_rh} prev_wind_buckets={prev_wh} "
              f"stazioni_con_pioggia_24h={wet} "
              f"rain6_complete={complete6} rain24_complete={complete24} "
              f"bytes={len(payload.encode('utf-8'))} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
