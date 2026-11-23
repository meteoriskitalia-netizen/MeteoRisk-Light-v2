# -*- coding: utf-8 -*-
"""Bucket orari di pioggia/vento nel blob `daily_series` (schema 2, 1.3.0.56).

Perche' servono: `latest.json` porta solo 3 ore di serie, quindi una cumulata a
6 h o 24 h non si puo' calcolare in un run. Il blob accumula UN valore per ORA
locale e i run successivi lo SOSTITUISCONO (ricalcolo della stessa ora), mai lo
sommano: senza questa regola ogni run aggiungerebbe la stessa pioggia.

Sul codice pristino 1.3.0.55 `build_daily_series` ignora `rain_hourly`/
`wind_hourly`, quindi `rh`/`wh` e tutti i campi derivati NON esistono e questi
test falliscono.
"""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "stations" / "build_daily_series.py"
_spec = importlib.util.spec_from_file_location("build_daily_series_buckets", _SCRIPT)
bds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bds)


def utc_hour(day: str, hour: int) -> int:
    """epoch SECONDI dell'inizio dell'ora UTC (Europe/Rome ha offset interi)."""
    return int(datetime.strptime(f"{day} {hour:02d}", "%Y-%m-%d %H").replace(
        tzinfo=timezone.utc).timestamp())


def ms(day: str, hour: int, minute: int = 0) -> int:
    return int(datetime.strptime(
        f"{day} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc).timestamp() * 1000)


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


# --------------------------------------------------------------------------- #
def test_schema_is_2_and_tuples_stay_v1():
    """`pts` resta la tupla a 11 valori: il client 1.3.0.55 la legge ancora."""
    doc = bds.build(latest(station("2026-11-23T13:00:00Z")), None, ms("2026-11-23", 13))
    assert doc["schema"] == "stations-daily-series/2"
    assert doc["schema"] in bds.SCHEMA_READABLE
    (point,) = doc["stations"]["IT001"]["pts"]
    assert len(point) == bds.TUPLE_LEN == 11


def test_rain_bucket_is_replaced_not_summed():
    """Due run nella STESSA ora: il bucket si sostituisce (il 2o e' piu' completo)."""
    day = "2026-11-23"
    first = latest(station(
        f"{day}T13:30:00Z", rain_hourly=[[utc_hour(day, 13), 0.4]]))
    doc = bds.build(first, None, ms(day, 13, 30))
    assert doc["stations"]["IT001"]["rh"] == {"14": 0.4}  # 13 UTC = 14 locali (CET)

    second = latest(station(
        f"{day}T13:50:00Z", rain_hourly=[[utc_hour(day, 13), 1.1]]))
    doc = bds.build(second, doc, ms(day, 13, 50))
    assert doc["stations"]["IT001"]["rh"] == {"14": 1.1}  # NON 1.5
    assert doc["stations"]["IT001"]["rain_24h_mm"] == 1.1


def test_rain_24h_is_partial_at_the_start_of_the_day():
    """Alle 03:00 locali il totale del giorno NON copre 24 ore: partial true."""
    day = "2026-11-23"
    hours = [[utc_hour(day, h), 0.5] for h in (0, 1, 2)]  # 01,02,03 locali
    doc = bds.build(
        latest(station(f"{day}T02:30:00Z", rain_hourly=hours)),
        None,
        ms(day, 2, 30),
    )
    rec = doc["stations"]["IT001"]
    assert rec["rain_24h_mm"] == 1.5
    assert rec["rain_24h_partial"] is True
    assert rec["rain_24h_since"] == "2026-11-23T00:00:00+01:00"


def test_rain_24h_is_not_partial_with_all_24_buckets():
    """Con tutte e 24 le ore del giorno il totale e' completo: partial false."""
    day = "2026-11-24"
    # stato costruito a mano: 24 bucket da 1,0 mm (00..23 locali)
    previous = {
        "schema": "stations-daily-series/2",
        "day": day,
        "stations": {"IT001": {
            "name": "Roma Test",
            "pts": [[ms(day, 12), 10.0, 68, 3.6, 210, 0.0, 900, 4.2, 1008.3, 8.1, 0.0]],
            "rh": {str(h): 1.0 for h in range(24)},
        }},
    }
    doc = bds.build(
        latest(station(f"{day}T22:30:00Z")), previous, ms(day, 22, 30)
    )
    rec = doc["stations"]["IT001"]
    assert rec["rain_24h_mm"] == 24.0
    assert rec["rain_24h_partial"] is False


def test_rain_6h_sums_only_the_last_six_hours_and_declares_partial():
    """6 h: si somma la finestra mobile, non tutto il giorno."""
    day = "2026-11-23"
    hours = [[utc_hour(day, h), 2.0] for h in range(0, 12)]  # 00..11 UTC
    doc = bds.build(
        latest(station(f"{day}T11:40:00Z", rain_hourly=hours)), None, ms(day, 11, 40)
    )
    rec = doc["stations"]["IT001"]
    # 11 UTC = 12 locali; finestra 07..12 locali = 6 bucket da 2,0
    assert rec["rain_6h_mm"] == 12.0
    assert rec["rain_6h_partial"] is False
    assert rec["rain_24h_mm"] == 24.0

    early = bds.build(
        latest(station(f"{day}T02:10:00Z", rain_hourly=hours[:3])),
        None,
        ms(day, 2, 10),
    )["stations"]["IT001"]
    assert early["rain_6h_partial"] is True


def test_wind_buckets_merge_speed_and_gust():
    """Vento medio e raffica finiscono nella stessa ora come [media, massimo]."""
    day = "2026-11-23"
    doc = bds.build(
        latest(station(
            f"{day}T13:40:00Z",
            wind_hourly=[[utc_hour(day, 12), 3.0], [utc_hour(day, 13), 4.5]],
            gust_hourly=[[utc_hour(day, 12), 9.0], [utc_hour(day, 13), 11.2]],
        )),
        None,
        ms(day, 13, 40),
    )
    rec = doc["stations"]["IT001"]
    assert rec["wh"] == {"13": [3.0, 9.0], "14": [4.5, 11.2]}
    assert rec["wind_24h_ms"] == 3.8          # media semplice delle ore
    assert rec["gust_max_24h_ms"] == 11.2     # massimo, non somma
    assert rec["wind_6h_ms"] == 3.8
    assert rec["gust_max_6h_ms"] == 11.2
    assert rec["wind_24h_partial"] is True


def test_wind_bucket_keeps_a_missing_quantity_as_null():
    """Solo vento, nessuna raffica nell'ora: la coppia resta [media, null]."""
    day = "2026-11-23"
    doc = bds.build(
        latest(station(f"{day}T13:20:00Z", wind_hourly=[[utc_hour(day, 13), 2.5]])),
        None,
        ms(day, 13, 20),
    )
    rec = doc["stations"]["IT001"]
    assert rec["wh"] == {"14": [2.5, None]}
    assert "gust_max_24h_ms" not in rec


def test_yesterday_buckets_are_dropped_at_midnight():
    """Nuovo giorno locale -> il blob riparte: nessun bucket di ieri."""
    previous = {
        "schema": "stations-daily-series/2",
        "day": "2026-11-22",
        "stations": {"IT001": {
            "name": "Roma Test",
            "pts": [[ms("2026-11-22", 12), 10.0, 68, 3.6, 210, 0.0, 900, 4.2, 1008.3, 8.1, 0.0]],
            "rh": {"10": 3.0, "11": 1.0},
            "wh": {"10": [2.0, 5.0]},
        }},
    }
    doc = bds.build(
        latest(station("2026-11-23T00:10:00Z")), previous, ms("2026-11-23", 0, 10)
    )
    rec = doc["stations"]["IT001"]
    assert doc["day"] == "2026-11-23"
    assert "rh" not in rec and "wh" not in rec
    assert "rain_6h_mm" not in rec


def test_schema_1_blob_is_still_readable():
    """Il blob v1 (senza bucket) si legge: i punti restano, i bucket partono vuoti."""
    previous = {
        "schema": "stations-daily-series/1",
        "day": "2026-11-23",
        "stations": {"IT001": {
            "name": "Roma Test",
            "pts": [[ms("2026-11-23", 12), 10.0, 68, 3.6, 210, 0.0, 900, 4.2, 1008.3, 8.1, 0.0]],
        }},
    }
    doc = bds.build(
        latest(station("2026-11-23T13:10:00Z")), previous, ms("2026-11-23", 13, 10)
    )
    assert len(doc["stations"]["IT001"]["pts"]) == 2
    assert "rh" not in doc["stations"]["IT001"]


def test_serialization_is_compact_and_has_no_bom(tmp_path):
    """Forma del payload: separatori compatti, termina con newline, niente BOM/CR."""
    day = "2026-11-23"
    doc = bds.build(
        latest(station(f"{day}T13:40:00Z",
                       rain_hourly=[[utc_hour(day, 13), 0.7]],
                       wind_hourly=[[utc_hour(day, 13), 3.0]])),
        None,
        ms(day, 13, 40),
    )
    payload = json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
    assert '": ' not in payload  # separatori compatti: nessun ": " da json default
    assert payload.endswith("\n")
    raw = payload.encode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert json.loads(payload)["schema"] == "stations-daily-series/2"
