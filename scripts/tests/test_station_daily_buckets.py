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

import pytest

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


# --------------------------------------------------------------------------- #
# 1.3.0.61: bucket termici th / rhh / dwh
# --------------------------------------------------------------------------- #
def test_temp_rh_dew_buckets_and_derivatives():
    """Tre ore di bucket: `th` porta media/min/max, i derivati sono medie orarie.

    A 16:30 locali con soli 3 bucket la finestra 6 h e 24 h NON e' completa:
    il valore c'e' e `*_partial` e' true. Non si inventano le ore mancanti.
    """
    day = "2026-11-23"
    obs = station(
        f"{day}T15:30:00Z",
        temp_hourly=[
            [utc_hour(day, 13), 10.0, 9.0, 11.0],
            [utc_hour(day, 14), 11.0, 10.0, 12.0],
            [utc_hour(day, 15), 12.0, 11.0, 13.0],
        ],
        rh_hourly=[
            [utc_hour(day, 13), 80, 70, 88],
            [utc_hour(day, 14), 82, 72, 90],
            [utc_hour(day, 15), 84, 74, 92],
        ],
        dew_hourly=[
            [utc_hour(day, 13), 6.0],
            [utc_hour(day, 14), 7.0],
            [utc_hour(day, 15), 8.0],
        ],
    )
    doc = bds.build(latest(obs), None, ms(day, 15, 30))
    rec = doc["stations"]["IT001"]
    # 13/14/15 UTC = 14/15/16 locali (CET)
    assert rec["th"] == {
        "14": [10.0, 9.0, 11.0],
        "15": [11.0, 10.0, 12.0],
        "16": [12.0, 11.0, 13.0],
    }
    assert rec["rhh"] == {
        "14": [80.0, 70.0, 88.0],
        "15": [82.0, 72.0, 90.0],
        "16": [84.0, 74.0, 92.0],
    }
    assert rec["dwh"] == {"14": [6.0], "15": [7.0], "16": [8.0]}
    assert rec["temp_6h_c"] == pytest.approx(11.0)
    assert rec["temp_24h_c"] == pytest.approx(11.0)
    assert rec["temp_6h_partial"] is True
    assert rec["temp_24h_partial"] is True
    assert rec["rh_6h_pct"] == pytest.approx(82.0)
    assert rec["dew_24h_c"] == pytest.approx(7.0)
    assert rec["rh_6h_partial"] is True and rec["dew_6h_partial"] is True


def test_temp_bucket_is_replaced_not_summed():
    """Due run nella STESSA ora: il bucket termico si sostituisce."""
    day = "2026-11-23"
    first = latest(station(
        f"{day}T13:30:00Z", temp_hourly=[[utc_hour(day, 13), 10.0, 9.0, 11.0]]))
    doc = bds.build(first, None, ms(day, 13, 30))
    assert doc["stations"]["IT001"]["th"] == {"14": [10.0, 9.0, 11.0]}
    second = latest(station(
        f"{day}T13:40:00Z", temp_hourly=[[utc_hour(day, 13), 12.0, 8.0, 14.0]]))
    doc = bds.build(second, doc, ms(day, 13, 40))
    assert doc["stations"]["IT001"]["th"] == {"14": [12.0, 8.0, 14.0]}


def test_temp_24h_is_partial_at_the_start_of_the_day():
    """Un'ora sola a inizio giornata: valore presente, finestra dichiarata parziale."""
    day = "2026-11-23"
    obs = station(
        f"{day}T00:10:00Z",
        temp_hourly=[[utc_hour(day, 0), 5.0, 4.0, 6.0]],
        rh_hourly=[[utc_hour(day, 0), 90]],
        dew_hourly=[[utc_hour(day, 0), 3.0]],
    )
    doc = bds.build(latest(obs), None, ms(day, 0, 10))
    rec = doc["stations"]["IT001"]
    assert rec["temp_6h_c"] == pytest.approx(5.0)
    assert rec["temp_6h_partial"] is True
    assert rec["temp_24h_c"] == pytest.approx(5.0)
    assert rec["temp_24h_partial"] is True
    assert rec["rh_24h_partial"] is True
    assert rec["dew_24h_partial"] is True


# --------------------------------------------------------------------------- #
# 1.3.0.61: massimo/minimo della GIORNATA dagli stessi bucket orari
# --------------------------------------------------------------------------- #
def test_temp_day_extremes_and_local_hours():
    """Il picco e il minimo della giornata con l'ora locale che li ha prodotti.

    A 03:30 locali (02:30 UTC) con tre bucket (locali 1,2,3) la copertura e'
    3/4: il valore si pubblica, `temp_day_partial` e' true (giornata non finita).
    """
    day = "2026-11-23"
    obs = station(
        f"{day}T02:30:00Z",
        temp_hourly=[
            [utc_hour(day, 0), 10.0, 9.0, 18.2],
            [utc_hour(day, 1), 11.0, 10.0, 12.0],
            [utc_hour(day, 2), 12.0, 2.0, 13.0],
        ],
    )
    rec = bds.build(latest(obs), None, ms(day, 2, 30))["stations"]["IT001"]
    assert rec["temp_max_day_c"] == pytest.approx(18.2)
    assert rec["temp_max_day_hour"] == "1"
    assert rec["temp_min_day_c"] == pytest.approx(2.0)
    assert rec["temp_min_day_hour"] == "3"
    assert rec["temp_day_coverage_frac"] == pytest.approx(0.75)
    assert rec["temp_day_partial"] is True
    assert "temp_day_null_reason" not in rec


def test_temp_day_coverage_40pct_is_null_with_reason():
    """3 bucket + il punto del run = 4 ore su 10 trascorse: null con motivo."""
    day = "2026-11-23"
    rows = [
        [utc_hour(day, hour), 10.0, 9.0, 11.0]
        for hour in range(3)
    ]
    obs = station(f"{day}T08:30:00Z", temp_hourly=rows)
    rec = bds.build(latest(obs), None, ms(day, 8, 30))["stations"]["IT001"]
    assert rec["temp_day_coverage_frac"] == pytest.approx(0.4)
    assert rec["temp_max_day_c"] is None
    assert rec["temp_min_day_c"] is None
    assert rec["temp_max_day_hour"] is None
    assert rec["temp_day_null_reason"] == "coverage_below_50pct"
    assert rec["temp_day_partial"] is True


def test_temp_day_absent_series_has_no_fields():
    """Stazione senza temperatura: nessun campo, non un null finto."""
    day = "2026-11-23"
    obs = station(f"{day}T08:30:00Z")
    obs.pop("temperature_2m", None)
    rec = bds.build(latest(obs), None, ms(day, 8, 30))["stations"]["IT001"]
    assert "temp_max_day_c" not in rec
    assert "temp_min_day_c" not in rec
    assert "temp_day_null_reason" not in rec


def test_temp_day_malformed_bucket_is_no_points_today():
    """Bucket presente ma senza minima/massima: null + `no_points_today`."""
    day = "2026-11-23"
    previous = {
        "schema": bds.SCHEMA,
        "day": day,
        "stations": {
            "IT001": {
                "name": "",
                "pts": [[ms(day, 1, 10), None, 80.0, 2.0, 180, 0.0, 900,
                         5.0, 1010.0, 4.0, 0.0]],
                "th": {"1": [None, None, None]},
            }
        },
    }
    doc = bds.build({"schema": "stations/objects", "stations": []}, previous, ms(day, 1, 30))
    rec = doc["stations"]["IT001"]
    assert rec["temp_max_day_c"] is None
    assert rec["temp_day_null_reason"] == "no_points_today"


def test_temp_day_full_day_is_not_partial():
    """24 bucket su 24 alle 23:30 locali: la finestra e' completa."""
    day = "2026-11-23"
    rows = [[utc_hour("2026-11-22", 23), 5.0, 4.0, 6.0]]  # ora locale 0
    for local_hour in range(1, 24):
        high = 20.0 if local_hour == 14 else 11.0
        low = 1.0 if local_hour == 6 else 9.0
        rows.append([utc_hour(day, local_hour - 1), 10.0, low, high])
    obs = station(f"{day}T22:30:00Z", temp_hourly=rows)
    rec = bds.build(latest(obs), None, ms(day, 22, 30))["stations"]["IT001"]
    assert rec["temp_day_coverage_frac"] == pytest.approx(1.0)
    assert rec["temp_day_partial"] is False
    assert rec["temp_max_day_c"] == pytest.approx(20.0)
    assert rec["temp_max_day_hour"] == "14"
    assert rec["temp_min_day_c"] == pytest.approx(1.0)
    assert rec["temp_min_day_hour"] == "6"


def test_rh_day_extremes():
    """Umidita': massimo e minimo di oggi dagli stessi bucket `rhh`."""
    day = "2026-11-23"
    obs = station(
        f"{day}T02:30:00Z",
        rh_hourly=[
            [utc_hour(day, 0), 80, 70, 95],
            [utc_hour(day, 1), 82, 72, 90],
            [utc_hour(day, 2), 84, 61, 88],
        ],
    )
    rec = bds.build(latest(obs), None, ms(day, 2, 30))["stations"]["IT001"]
    assert rec["rh_max_day_pct"] == 95
    assert rec["rh_max_day_hour"] == "1"
    assert rec["rh_min_day_pct"] == 61
    assert rec["rh_min_day_hour"] == "3"
    assert rec["rh_day_partial"] is True


def test_local_hour_uses_zoneinfo_across_dst():
    """Ora locale da `zoneinfo`: nessun offset fisso +1/+2.

    2026-03-29: alle 01:00 UTC si passa da 02:00 a 03:00 (CEST).
    2026-10-25: alle 01:00 UTC si passa da 03:00 CEST a 02:00 CET: l'ora 02
    compare DUE volte e il dizionario dei bucket puo' tenere una sola chiave.
    """
    from datetime import datetime as dt, timezone as tz

    def hour_at(y, mo, d, h, mi=0):
        return bds.local_hour(int(dt(y, mo, d, h, mi, tzinfo=tz.utc).timestamp()))[1]

    assert hour_at(2026, 3, 29, 0, 30) == 1
    assert hour_at(2026, 3, 29, 1, 30) == 3
    assert hour_at(2026, 10, 25, 0, 30) == 2
    assert hour_at(2026, 10, 25, 1, 30) == 2


def _point(day: str, utc_hour: int, temp=10.0, rh=80.0) -> list:
    return [ms(day, utc_hour, 10), temp, rh, 2.0, 180, 0.0, 900, 5.0, 1010.0, 4.0, 0.0]


def test_cold_start_backfills_buckets_from_points():
    """Blob vecchio senza `th`: le ore si ricavano dai punti gia' in `pts`.

    Il run corrente non pubblica la stazione (latest vuoto), quindi restano i
    punti del blob precedente: senza backfill `temp_max_day_c` sarebbe null con
    `coverage_below_50pct` per mezza giornata.
    """
    day = "2026-11-23"
    pts = [_point(day, hour, temp=10.0 + hour) for hour in range(10)]  # 1..10 locali
    previous = {
        "schema": bds.SCHEMA,
        "day": day,
        "stations": {"IT001": {"name": "Roma Test", "pts": pts}},
    }
    doc = bds.build({"schema": "stations/objects", "stations": []}, previous, ms(day, 9, 30))
    rec = doc["stations"]["IT001"]
    assert len(rec["th"]) == 10
    assert rec["temp_max_day_c"] == pytest.approx(19.0)
    assert rec["temp_max_day_hour"] == "10"
    assert rec["temp_min_day_c"] == pytest.approx(10.0)
    assert rec["temp_min_day_hour"] == "1"
    assert rec["temp_day_coverage_frac"] == pytest.approx(10 / 11, rel=0.01)


def test_backfill_does_not_overwrite_the_run_bucket():
    """L'ora prodotta dalla serie resta quella del run, non quella dei punti."""
    day = "2026-11-23"
    pts = [_point(day, 0, temp=10.0), _point(day, 1, temp=12.0)]
    previous = {
        "schema": bds.SCHEMA,
        "day": day,
        "stations": {
            "IT001": {"name": "", "pts": pts, "th": {"2": [50.0, 49.0, 51.0]}}
        },
    }
    doc = bds.build({"schema": "stations/objects", "stations": []}, previous, ms(day, 1, 30))
    assert doc["stations"]["IT001"]["th"]["2"] == [50.0, 49.0, 51.0]
    assert doc["stations"]["IT001"]["th"]["1"] == [10.0, 10.0, 10.0]
