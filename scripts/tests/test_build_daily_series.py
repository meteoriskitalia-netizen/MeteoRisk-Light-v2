"""Test di build_daily_series.py con clock simulato (--now-ms / build()).

Eseguito con:
  & 'C:\\Users\\aless\\AppData\\Local\\Python\\bin\\python.exe' -m pytest \
      'C:\\Users\\aless\\Desktop\\meteorisk\\work_13\\series_fix\\scripts\\tests\\test_build_daily_series.py' -q
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = ROOT / "stations" / "build_daily_series.py"
FIXTURE = HERE / "fixtures" / "latest_3obs.json"

spec = importlib.util.spec_from_file_location("build_daily_series", SCRIPT)
bds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bds)


def ms(y, mo, d, h, mi, s=0) -> int:
    return int(round(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000))


def station(obs_utc: str, temp: float = 10.0) -> dict:
    return {
        "id": "IT001", "lat": 41.9, "lon": 12.49, "name": "Roma Test", "network": "TEST",
        "obs_time": obs_utc, "temperature_2m": temp, "relative_humidity_2m": 68,
        "wind_speed_10m": 3.6, "wind_direction_10m": 210,
        "precipitation": 1.0, "precipitation_period_s": 900,
        "dew_point_2m": 4.2, "station_pressure_hPa": 1008.3,
        "wind_gust_10m": 8.1, "snow_depth_m": 0.0,
    }


def latest(*obs) -> dict:
    return {"schema": "stations/objects", "generated_at": "2026-11-18T07:25:00Z",
            "stations": list(obs)}


# --------------------------------------------------------------------------- #
def test_fixture_three_points_accumulate(tmp_path):
    """(a) 3 osservazioni alle 08:00/08:10/08:20 locali -> 3 punti, 3 ore distinte."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out = tmp_path / "daily_series.json"
    prev = None
    # 08:00 / 08:10 / 08:20 Europe/Rome = 07:00 / 07:10 / 07:20 UTC (CET, +1)
    for h, mi in ((7, 0), (7, 10), (7, 20)):
        one = latest(station(f"2026-11-18T{h:02d}:{mi:02d}:00Z"))
        doc = bds.build(one, prev, ms(2026, 11, 18, h, mi))
        prev = doc
    pts = prev["stations"]["IT001"]["pts"]
    assert prev["day"] == "2026-11-18"
    assert len(pts) == 3
    assert len({p[0] for p in pts}) == 3
    assert [p[0] for p in pts] == sorted(p[0] for p in pts)
    assert all(len(p) == 11 for p in pts)
    # la tupla e' quella del client: index 5 = mm/h = 1*3600/900 = 4.0
    assert pts[0][5] == 4.0

    # Stesso scenario via CLI sul fixture (3 stazioni -> una con 3 punti).
    subprocess.run(
        [sys.executable, str(SCRIPT), "--latest", str(FIXTURE), "--out", str(out),
         "--now-ms", str(ms(2026, 11, 18, 7, 20)), "--quiet"],
        check=True,
    )
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk["day"] == "2026-11-18"
    assert len(disk["stations"]["IT001"]["pts"]) == 3


def test_midnight_reset(tmp_path):
    """(c) 4 punti il 18, aggiornamento alle 00:01 del 19 -> 1 punto e day cambiato."""
    prev = None
    steps = [
        ("2026-11-18T07:00:00Z", ms(2026, 11, 18, 7, 0)),    # 08:00 locali
        ("2026-11-18T07:10:00Z", ms(2026, 11, 18, 7, 10)),   # 08:10
        ("2026-11-18T07:20:00Z", ms(2026, 11, 18, 7, 20)),   # 08:20
        ("2026-11-18T22:50:00Z", ms(2026, 11, 18, 22, 50)),  # 23:50
    ]
    for obs_utc, now in steps:
        prev = bds.build(latest(station(obs_utc)), prev, now)
    assert len(prev["stations"]["IT001"]["pts"]) == 4
    assert prev["day"] == "2026-11-18"

    # 00:01 locali del 19 = 23:01Z del 18: giorno locale nuovo -> azzera, poi
    # aggancia la prima osservazione del nuovo giorno (1 punto, non 5).
    now_next = ms(2026, 11, 18, 23, 1)
    doc = bds.build(latest(station("2026-11-18T23:01:00Z")), prev, now_next)
    assert doc["day"] == "2026-11-19"
    assert len(doc["stations"]["IT001"]["pts"]) == 1
    assert doc["stations"]["IT001"]["pts"][0][0] == now_next


def test_midnight_no_observation_yet(tmp_path):
    """A mezzanotte, se latest non ha ancora osservazioni del nuovo giorno -> 0 punti (onesto)."""
    prev = bds.build(latest(station("2026-11-18T22:50:00Z")), None, ms(2026, 11, 18, 22, 50))
    now_next = ms(2026, 11, 18, 23, 1)
    doc = bds.build(latest(station("2026-11-18T22:50:00Z")), prev, now_next)
    assert doc["day"] == "2026-11-19"
    assert doc["stations"] == {}


def test_dedup_same_obs_time():
    """(d) doppio aggiornamento con lo stesso obs_time -> conteggio invariato, valore aggiornato."""
    prev = None
    prev = bds.build(latest(station("2026-11-18T07:00:00Z", 10.0)), prev, ms(2026, 11, 18, 7, 0))
    prev = bds.build(latest(station("2026-11-18T07:00:00Z", 11.5)), prev, ms(2026, 11, 18, 7, 5))
    pts = prev["stations"]["IT001"]["pts"]
    assert len(pts) == 1
    assert pts[0][1] == 11.5  # sostituita, non agganciata
    assert pts[0][0] == ms(2026, 11, 18, 7, 0)


def test_preserves_stations_not_republished():
    """Una stazione non ripubblicata in questo run non perde i punti di oggi."""
    prev = bds.build(latest(station("2026-11-18T07:00:00Z")), None, ms(2026, 11, 18, 7, 0))
    other = station("2026-11-18T07:00:00Z")
    other["id"] = "IT002"
    prev = bds.build(latest(other), prev, ms(2026, 11, 18, 7, 10))
    pts = prev["stations"]["IT001"]["pts"]
    assert len(pts) == 1  # IT001 non era in latest, ma il punto di oggi resta


def test_dst_and_fallback():
    """Giorno Europe/Rome corretto d'inverno e d'estate, con e senza zoneinfo."""
    # 2026-07-01 22:30Z -> 00:30 del 2 luglio (CEST, +2)
    assert bds.rome_day(ms(2026, 7, 1, 22, 30)) == "2026-07-02"
    # 2026-11-18 22:30Z -> 23:30 del 18 (CET, +1)
    assert bds.rome_day(ms(2026, 11, 18, 22, 30)) == "2026-11-18"
    old = bds._ROME
    try:
        bds._ROME = None
        assert bds.rome_day(ms(2026, 7, 1, 22, 30)) == "2026-07-02"
        assert bds.rome_day(ms(2026, 11, 18, 22, 30)) == "2026-11-18"
    finally:
        bds._ROME = old


def test_cli_refuses_empty_latest(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"stations": []}), encoding="utf-8")
    out = tmp_path / "daily_series.json"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--latest", str(empty), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert r.returncode == 4
    assert not out.exists()
