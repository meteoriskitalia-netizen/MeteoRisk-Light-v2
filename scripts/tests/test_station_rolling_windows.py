# -*- coding: utf-8 -*-
"""Finestre SCORREVOLI 6 h / 24 h del blob `daily_series` (1.3.0.86).

Difetto riparato: le finestre partivano dalla mezzanotte locale, quindi alle
02:00 la "24 h" copriva 2 ore (e poteva mostrare MENO della 1 h / 3 h lette da
latest.json). Qui si verifica la regola nuova:

  * la finestra termina a NOW e attraversa la mezzanotte;
  * servono i bucket del giorno corrente (`rh`, ...) e del precedente
    (`rh_prev`, ...), completati con le ultime ore di ieri presenti in
    latest.json e potati alle ore ancora utili;
  * `*_partial` = copertura < 50% delle ore nominali;
  * `window_start` / `window_end` / `coverage_frac` sono dichiarati.

Gli orari sono Europe/Rome (CET, +1) nel novembre 2026: nessun test dipende
dall'ora legale (il caso DST e' coperto in test_station_daily_buckets.py).
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "stations" / "build_daily_series.py"
_spec = importlib.util.spec_from_file_location("build_daily_series_rolling", _SCRIPT)
bds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bds)


def ms(day: str, hour: int, minute: int = 0) -> int:
    """epoch ms di un istante UTC."""
    return int(datetime.strptime(
        f"{day} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc).timestamp() * 1000)


def local_ms(day: str, hour: int, minute: int = 0) -> int:
    """epoch ms dell'ora LOCALE Europe/Rome (CET, +1) nel novembre 2026."""
    base = datetime.strptime(
        f"{day} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc)
    return int((base - timedelta(hours=1)).timestamp() * 1000)


def local_hour(day: str, hour: int) -> int:
    """epoch SECONDI dell'inizio dell'ora locale (stessa forma di rain_hourly)."""
    return local_ms(day, hour) // 1000


def station(obs_utc: str, **extra) -> dict:
    rec = {
        "id": "IT001", "lat": 41.9, "lon": 12.49, "name": "Roma Test",
        "network": "TEST", "obs_time": obs_utc, "temperature_2m": 10.0,
        "relative_humidity_2m": 68, "wind_speed_10m": 3.6,
        "wind_direction_10m": 210, "precipitation": 0.0,
        "precipitation_period_s": 900,
    }
    rec.update(extra)
    return rec


def latest(*obs) -> dict:
    return {"schema": "stations/objects", "stations": list(obs)}


def prev_day_blob(day: str, **buckets) -> dict:
    """Blob del giorno precedente con i soli bucket indicati."""
    rec = {"name": "Roma Test", "pts": []}
    rec.update(buckets)
    return {"schema": bds.SCHEMA, "day": day, "stations": {"IT001": rec}}


def full_rb(value: float = 1.0) -> dict:
    return {str(hour): value for hour in range(24)}


# --------------------------------------------------------------------------- #
# Il caso segnalato: alle 02:00 la 24 h deve contenere 24 ore, non 2.
# --------------------------------------------------------------------------- #
def test_24h_at_0200_crosses_midnight_and_is_complete():
    previous = prev_day_blob("2026-11-23", rh=full_rb())
    obs = station(
        "2026-11-23T23:05:00Z",  # 00:05 locali del 24
        rain_hourly=[
            [local_hour("2026-11-23", 23), 1.0],  # ultima ora di ieri
            [local_hour("2026-11-24", 0), 1.0],
            [local_hour("2026-11-24", 1), 1.0],
        ],
    )
    doc = bds.build(latest(obs), previous, local_ms("2026-11-24", 2, 0))
    rec = doc["stations"]["IT001"]

    # 22 slot di ieri (02..23) + 2 di oggi (00,01) = 24 ore piene.
    assert rec["rain_24h_mm"] == 24.0
    assert rec["rain_24h_partial"] is False
    assert rec["rain_24h_coverage_frac"] == 1.0
    assert rec["rain_24h_window_start"] == "2026-11-23T02:00:00+01:00"
    assert rec["rain_24h_window_end"] == "2026-11-24T02:00:00+01:00"
    assert rec["rain_24h_since"] == rec["rain_24h_window_start"]
    assert rec["rain_6h_mm"] == 6.0
    assert rec["rain_6h_partial"] is False
    assert rec["rain_6h_window_start"] == "2026-11-23T20:00:00+01:00"
    # La 24 h non puo' mai essere minore della 6 h: la finestra lunga contiene
    # quella corta (era il sintomo segnalato).
    assert rec["rain_24h_mm"] >= rec["rain_6h_mm"]
    assert doc["prev_day"] == "2026-11-23"
    assert doc["window_policy"]["24h"]["window_start"] == "2026-11-23T02:00:00+01:00"
    assert doc["window_policy"]["24h"]["window_end"] == "2026-11-24T02:00:00+01:00"


def test_24h_at_0230_uses_24_slots_not_26():
    """A 02:30 il primo slot e' tagliato a sinistra: 24 slot, non 26."""
    previous = prev_day_blob("2026-11-23", rh=full_rb())
    obs = station(
        "2026-11-23T23:35:00Z",
        rain_hourly=[
            [local_hour("2026-11-23", 21), 1.0],
            [local_hour("2026-11-23", 22), 1.0],
            [local_hour("2026-11-23", 23), 1.0],
            [local_hour("2026-11-24", 0), 1.0],
            [local_hour("2026-11-24", 1), 1.0],
            [local_hour("2026-11-24", 2), 1.0],
        ],
    )
    doc = bds.build(latest(obs), previous, local_ms("2026-11-24", 2, 30))
    rec = doc["stations"]["IT001"]
    # oggi 00,01,02 + ieri 03..23 = 24 slot.
    assert rec["rain_24h_mm"] == 24.0
    assert rec["rain_24h_coverage_frac"] == 1.0
    assert rec["rain_24h_window_start"] == "2026-11-23T03:00:00+01:00"
    assert rec["rain_24h_window_end"] == "2026-11-24T02:30:00+01:00"


def test_24h_without_prev_is_the_old_two_hour_behaviour():
    """Senza la coda di ieri restano 2 ore: e' il difetto, ora DICHIARATO."""
    obs = station(
        "2026-11-24T00:05:00Z",
        rain_hourly=[
            [local_hour("2026-11-24", 0), 1.0],
            [local_hour("2026-11-24", 1), 1.0],
        ],
    )
    rec = bds.build(latest(obs), None, local_ms("2026-11-24", 2, 0))["stations"]["IT001"]
    assert rec["rain_24h_mm"] == 2.0
    assert rec["rain_24h_partial"] is True
    assert rec["rain_24h_coverage_frac"] == pytest.approx(2 / 24, abs=0.001)
    assert rec["rain_24h_window_start"] == "2026-11-24T00:00:00+01:00"


def test_partial_is_coverage_below_50pct_not_midnight():
    previous = prev_day_blob("2026-11-23", rh={"22": 1.0, "23": 1.0})
    obs = station(
        "2026-11-23T23:05:00Z",
        rain_hourly=[
            [local_hour("2026-11-24", 0), 1.0],
            [local_hour("2026-11-24", 1), 1.0],
        ],
    )
    rec = bds.build(latest(obs), previous, local_ms("2026-11-24", 2, 0))["stations"]["IT001"]
    # 4 slot su 24 = 16,7% -> parziale, ma il valore resta un limite inferiore.
    assert rec["rain_24h_mm"] == 4.0
    assert rec["rain_24h_coverage_frac"] == pytest.approx(4 / 24, abs=0.001)
    assert rec["rain_24h_partial"] is True


def test_50pct_coverage_is_not_partial_anymore():
    """Con 12 slot su 24 (esattamente 50%) la finestra NON e' parziale."""
    previous = prev_day_blob("2026-11-23", rh={str(h): 1.0 for h in range(12, 24)})
    obs = station(
        "2026-11-23T23:05:00Z",
        rain_hourly=[
            [local_hour("2026-11-24", 0), 1.0],
            [local_hour("2026-11-24", 1), 1.0],
        ],
    )
    # oggi 0,1 + ieri 12..23 = 14 slot: sopra il 50%, non parziale.
    rec = bds.build(latest(obs), previous, local_ms("2026-11-24", 2, 0))["stations"]["IT001"]
    assert rec["rain_24h_coverage_frac"] == pytest.approx(14 / 24, abs=0.001)
    assert rec["rain_24h_partial"] is False


def test_prev_buckets_are_pruned_to_the_hours_still_needed():
    """A 14:30 le ore di ieri <= 14 non entrano piu' in nessuna finestra."""
    previous = {
        "schema": bds.SCHEMA,
        "day": "2026-11-24",
        "prev_day": "2026-11-23",
        "stations": {"IT001": {
            "name": "Roma Test",
            "pts": [],
            "rh": {str(h): 1.0 for h in range(15)},      # oggi 00..14
            "rh_prev": {str(h): 1.0 for h in range(24)},  # ieri 00..23
        }},
    }
    doc = bds.build(
        latest(station("2026-11-24T13:35:00Z")),
        previous,
        local_ms("2026-11-24", 14, 30),
    )
    rec = doc["stations"]["IT001"]
    assert sorted(int(k) for k in rec["rh_prev"]) == list(range(14, 24))
    # oggi 0..14 (15) + ieri 15..23 (9) = 24 slot pieni.
    assert rec["rain_24h_mm"] == 24.0
    assert rec["rain_24h_coverage_frac"] == 1.0
    assert rec["rain_24h_partial"] is False


def test_stale_blob_does_not_carry_old_buckets():
    """Blob fermo da due giorni: nessuna coda, nessun valore inventato."""
    previous = prev_day_blob("2026-11-21", rh=full_rb())
    obs = station(
        "2026-11-24T01:00:00Z",
        rain_hourly=[[local_hour("2026-11-24", 1), 0.4]],
    )
    doc = bds.build(latest(obs), previous, local_ms("2026-11-24", 2, 0))
    rec = doc["stations"]["IT001"]
    assert "rh_prev" not in rec
    assert "prev_day" not in doc
    assert rec["rain_24h_mm"] == 0.4
    assert rec["rain_24h_partial"] is True


def test_midnight_row_completes_yesterday_bucket_without_summing():
    """La riga di ieri 23 ancora in latest sostituisce il bucket, non somma."""
    previous = prev_day_blob("2026-11-23", rh={"22": 0.2, "23": 0.5})
    obs = station(
        "2026-11-23T23:05:00Z",
        rain_hourly=[
            [local_hour("2026-11-23", 23), 1.0],  # ora di ieri, arriva ora
            [local_hour("2026-11-24", 0), 0.3],
        ],
    )
    doc = bds.build(latest(obs), previous, local_ms("2026-11-24", 0, 10))
    rec = doc["stations"]["IT001"]
    assert rec["rh_prev"]["23"] == 1.0      # sostituito, NON 0.5 + 1.0
    assert rec["rh"]["0"] == 0.3
    # A 00:10 la finestra di 24 h copre ieri 01..23 + oggi 00.
    assert rec["rain_24h_mm"] == pytest.approx(0.2 + 22 * 0.0 + 1.0 + 0.3, abs=0.2)


# --------------------------------------------------------------------------- #
# Vento, raffica e temperatura seguono la stessa finestra.
# --------------------------------------------------------------------------- #
def test_wind_and_gust_cross_midnight():
    previous = prev_day_blob(
        "2026-11-23",
        wh={str(h): [2.0, 9.0] for h in range(24)},
    )
    obs = station(
        "2026-11-23T23:05:00Z",
        wind_hourly=[
            [local_hour("2026-11-23", 23), 4.0],
            [local_hour("2026-11-24", 0), 4.0],
            [local_hour("2026-11-24", 1), 4.0],
        ],
        gust_hourly=[
            [local_hour("2026-11-23", 23), 12.0],
            [local_hour("2026-11-24", 0), 12.0],
            [local_hour("2026-11-24", 1), 12.0],
        ],
    )
    rec = bds.build(latest(obs), previous, local_ms("2026-11-24", 2, 0))["stations"]["IT001"]
    # oggi 00,01 = 4,0; ieri 23 (completata dalla riga di latest) = 4,0;
    # ieri 02..22 (21 ore) = 2,0 -> (42 + 12) / 24 = 2,25 -> 2,2 (banker's).
    assert rec["wind_24h_ms"] == 2.2
    assert rec["wind_24h_partial"] is False
    assert rec["wind_24h_coverage_frac"] == 1.0
    assert rec["gust_max_24h_ms"] == 12.0
    assert rec["gust_max_24h_partial"] is False
    # 6 h: oggi 00,01 (2 * 4,0) + ieri 20,21,22 (3 * 2,0) + ieri 23 (4,0) = 18/6.
    assert rec["wind_6h_ms"] == 3.0
    assert rec["gust_max_6h_ms"] == 12.0


def test_temp_means_cross_midnight():
    previous = prev_day_blob(
        "2026-11-23",
        th={str(h): [10.0, 9.0, 11.0] for h in range(24)},
    )
    obs = station(
        "2026-11-23T23:05:00Z",
        temp_hourly=[
            [local_hour("2026-11-23", 23), 20.0, 19.0, 21.0],
            [local_hour("2026-11-24", 0), 20.0, 19.0, 21.0],
            [local_hour("2026-11-24", 1), 20.0, 19.0, 21.0],
        ],
    )
    rec = bds.build(latest(obs), previous, local_ms("2026-11-24", 2, 0))["stations"]["IT001"]
    # oggi 00,01 = 20; ieri 23 (completata) = 20; ieri 02..22 (21 ore) = 10:
    # (4*20 + 21*10) / 24 = 11,25 -> 11,2.
    assert rec["temp_24h_c"] == pytest.approx(11.2, abs=0.05)
    assert rec["temp_24h_partial"] is False
    # 6 h: (2*20 + 3*10 + 1*20) / 6 = 15,0.
    assert rec["temp_6h_c"] == pytest.approx(15.0, abs=0.05)
    assert rec["temp_6h_partial"] is False
