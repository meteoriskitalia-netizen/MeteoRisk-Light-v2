#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test di JOB A — cattura dei livelli previsti (STILE B).

I casi soglia sono quelli del client 1.3.0.53:
  * R2: rain 45.1/15.1/1.1/0.21, gusts 100.1/75.1/50.1, code 95 + CAPE 300,
    80-90 + rain 8;
  * componenti: caldo 30/32/35/38, freddo 0/-2/-5/-8, neve con quota gelo,
    temporale da codice WMO;
  * grandine: pv null con null_reason (non replicata in fase B);
  * negativi: un giorno senza soglie produce pv=0 (mai omesso);
  * delta-only: la seconda cattura con lo stesso input non scrive righe.

Se qualcuno inverte una soglia o scrive solo i positivi, questi test cadono.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent  # scripts/
sys.path.insert(0, str(ROOT))

from verification import risk_forecast_capture as cap  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture minima: 2 province, 3 giorni
# --------------------------------------------------------------------------- #
def _day(day="2026-11-23", **over):
    base = {
        "day": day, "temp_max": 10.0, "temp_min": 5.0, "precip_sum": 0.0,
        "prob_max": 0, "wind_max": 5.0, "weathercode": 0, "humidity": 70,
        "pressure": 1013, "precip_hourly": 0.0, "showers": 0.0, "gusts": 10.0,
        "wind_avg": 4.0, "wind100_max": 10.0, "cape_max": 0,
        "freezing_max": 3000, "rain_intensity_max": 0.0,
        "thunder_hours": 0, "hail_hours": 0, "snow_hours": 0,
    }
    base.update(over)
    return base


def _write_fixture(tmp_path, days_a, days_b=None):
    days_b = days_b or [_day("2026-11-23"), _day("2026-11-24"), _day("2026-11-25")]
    meta = {
        "generated_at": "2026-11-23T08:25:09Z",
        "day0": "2026-11-23",
        "province_count": 2,
    }
    provinces = {
        "province_count": 2,
        "provinces": [
            {"idx": 0, "sigla": "AA", "prov": "Alfa (AA)", "region": "X",
             "selected_point": {"id": 0, "score": 10, "lat": 44.0, "lon": 10.0},
             "days": days_a},
            {"idx": 1, "sigla": "BB", "prov": "Beta (BB)", "region": "Y",
             "selected_point": {"id": 1, "score": 20, "lat": 45.0, "lon": 11.0},
             "days": days_b},
        ],
    }
    points = {"points": [
        {"id": 0, "sigla": "AA", "elevation": 120},
        {"id": 1, "sigla": "BB", "elevation": 800},
    ]}
    version = tmp_path / "VERSION"
    version.write_text(
        "VERSION=1.3.0.49\nBUILD=179\nDATE=2026-11-23\n"
        "APP_FILE=mri-light-1.3.0.53.html\n"
        "VERSION=1.3.0.53\nBUILD=183\n",
        encoding="utf-8")
    meta_p = tmp_path / "metadata.json"
    prov_p = tmp_path / "provinces.json"
    pts_p = tmp_path / "points.json"
    meta_p.write_text(json.dumps(meta), encoding="utf-8")
    prov_p.write_text(json.dumps(provinces), encoding="utf-8")
    pts_p.write_text(json.dumps(points), encoding="utf-8")
    return meta_p, prov_p, pts_p, version


def _run(tmp_path, *extra):
    out = tmp_path / "snap.jsonl"
    args = [str(a) for a in extra]
    code = cap.main(args + ["--out", str(out)])
    assert code == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    return out, rows


# --------------------------------------------------------------------------- #
# R2: casi soglia esatti
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rain,expected", [
    (45.1, 4), (45.0, 3),
    (15.1, 3), (15.0, 2),
    (1.1, 2), (1.0, 1),
    (0.21, 1), (0.2, 0),
])
def test_r2_rain_thresholds(rain, expected):
    assert cap.risk_level_r2(_day(precip_sum=rain)) == expected


@pytest.mark.parametrize("gusts,expected", [
    (100.1, 4), (100.0, 3),
    (75.1, 3), (75.0, 2),
    (50.1, 2), (50.0, 0),
])
def test_r2_gust_thresholds(gusts, expected):
    assert cap.risk_level_r2(_day(gusts=gusts)) == expected


def test_r2_thunderstorm_cape_legs():
    assert cap.risk_level_r2(_day(weathercode=95, precip_sum=10, cape_max=300)) == 4
    assert cap.risk_level_r2(_day(weathercode=95, precip_sum=10, cape_max=299)) == 3
    assert cap.risk_level_r2(_day(weathercode=95, precip_sum=3, cape_max=150)) == 3
    assert cap.risk_level_r2(_day(weathercode=95, precip_sum=3, cape_max=149)) == 2


def test_r2_shower_code_with_rain_8():
    assert cap.risk_level_r2(_day(weathercode=85, precip_sum=8)) == 3
    assert cap.risk_level_r2(_day(weathercode=85, precip_sum=7.9)) == 2
    assert cap.risk_level_r2(_day(weathercode=80, precip_sum=8)) == 3
    assert cap.risk_level_r2(_day(weathercode=90, precip_sum=8)) == 3


def test_r2_probability_legs():
    assert cap.risk_level_r2(_day(prob_max=60.1)) == 2
    assert cap.risk_level_r2(_day(prob_max=60.0)) == 1
    assert cap.risk_level_r2(_day(prob_max=20.1)) == 1
    assert cap.risk_level_r2(_day(prob_max=20.0)) == 0


# --------------------------------------------------------------------------- #
# Componenti per pericolo
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tmax,expected", [
    (29.9, 0), (30.0, 1), (31.9, 1), (32.0, 2), (35.0, 3), (38.0, 4),
])
def test_heat_levels(tmax, expected):
    assert cap.hazard_levels(_day(temp_max=tmax))["levels"]["heat"] == expected


@pytest.mark.parametrize("tmin,expected", [
    (0.1, 0), (0.0, 1), (-1.9, 1), (-2.0, 2), (-5.0, 3), (-8.0, 4),
])
def test_cold_levels(tmin, expected):
    assert cap.hazard_levels(_day(temp_min=tmin))["levels"]["cold"] == expected


def test_snow_requires_both_gates():
    snow = cap.hazard_levels(_day(weathercode=71, precip_sum=20, freezing_max=1400))
    assert snow["levels"]["snow"] == 2
    # quota gelo alta -> nessuna neve, anche con codice 71
    warm = cap.hazard_levels(_day(weathercode=71, precip_sum=20, freezing_max=1401))
    assert warm["levels"]["snow"] == 0
    # codice non nevoso -> nessuna neve
    rain = cap.hazard_levels(_day(weathercode=61, precip_sum=20, freezing_max=1000))
    assert rain["levels"]["snow"] == 0


def test_thunderstorm_badge_levels():
    assert cap.hazard_levels(_day(weathercode=95, precip_sum=10, cape_max=300))["levels"]["thunderstorm"] == 4
    assert cap.hazard_levels(_day(weathercode=95, precip_sum=3, cape_max=150))["levels"]["thunderstorm"] == 3
    assert cap.hazard_levels(_day(weathercode=95, precip_sum=0, cape_max=0))["levels"]["thunderstorm"] == 2
    possible = cap.hazard_levels(_day(weathercode=61, showers=2.0, cape_max=250))
    assert possible["levels"]["thunderstorm"] == 1
    assert possible["levels"]["thunderstorm"] != 2


def test_rain_component_suppressed_by_thunderstorm():
    # code 95 e pioggia 1 mm: la pioggia non deve restare a livello 1
    hz = cap.hazard_levels(_day(weathercode=95, precip_sum=1.0, cape_max=100))
    assert hz["levels"]["rain"] == 0


def test_hail_is_null_with_reason():
    hz = cap.hazard_levels(_day())
    assert hz["levels"]["hail"] is None
    assert hz["null_reason"]["hail"] == "component_not_replicated_phaseB"


def test_province_level_is_max_of_r2_and_components():
    # R2 2 (prob 65) ma caldo 3 -> livello provincia 3
    levels = cap.hazard_levels(_day(prob_max=65, temp_max=35))["levels"]
    assert cap.province_level(2, levels) == 3
    # cap a 4
    levels = cap.hazard_levels(_day(temp_max=40))["levels"]
    assert cap.province_level(4, levels) == 4


# --------------------------------------------------------------------------- #
# Cattura: negativi, delta, rotazione
# --------------------------------------------------------------------------- #
def test_capture_writes_zero_level_negatives(tmp_path):
    meta, prov, pts, version = _write_fixture(
        tmp_path, [_day(), _day("2026-11-24"), _day("2026-11-25")])
    out, rows = _run(tmp_path, "--meta-file", meta, "--province-file", prov,
                     "--points-file", pts, "--version-file", version,
                     "--now", "2026-11-23T08:30:00Z", "--days", "0")
    data = [r for r in rows if r.get("t") != "meta"]
    assert len(data) == 2 * len(cap.HAZARDS)
    rain = [r for r in data if r["h"] == "rain"]
    assert rain and all(r["pv"] == 0 for r in rain)
    assert all(r["r2"] == 0 and r["pl"] == 0 for r in rain)
    assert all(r["sp"] in (0, 1) and r["el"] in (120, 800) for r in rain)
    assert all(r["ls"] == "r2_from_provinces" and r["v"] == 1 for r in rain)
    meta_row = [r for r in rows if r.get("t") == "meta"][0]
    assert meta_row["ev"] == "1.3.0.53"
    assert meta_row["rows"] == 2 * len(cap.HAZARDS)


def test_delta_only_second_run_has_no_hazard_rows(tmp_path):
    meta, prov, pts, version = _write_fixture(
        tmp_path, [_day(), _day("2026-11-24"), _day("2026-11-25")])
    out, _ = _run(tmp_path, "--meta-file", meta, "--province-file", prov,
                  "--points-file", pts, "--version-file", version,
                  "--now", "2026-11-23T08:30:00Z", "--days", "0")
    out2, rows2 = _run(tmp_path, "--meta-file", meta, "--province-file", prov,
                       "--points-file", pts, "--version-file", version,
                       "--now", "2026-11-23T09:00:00Z", "--days", "0")
    meta2 = [r for r in rows2 if r.get("t") == "meta"][0]
    assert meta2["new_rows"] == 0, "il livello non e' cambiato: nessuna riga nuova"
    assert meta2["rows"] == 2 * len(cap.HAZARDS)
    # il file resta quello di prima: le righe vengono riscritte, non duplicate
    assert len(rows_all(out2)) == 2 * len(cap.HAZARDS)


def rows_all(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x and json.loads(x).get("t") != "meta"]


def test_delta_appends_only_changed_level(tmp_path):
    meta, prov, pts, version = _write_fixture(
        tmp_path, [_day(), _day("2026-11-24"), _day("2026-11-25")])
    _run(tmp_path, "--meta-file", meta, "--province-file", prov,
         "--points-file", pts, "--version-file", version,
         "--now", "2026-11-23T08:30:00Z", "--days", "0")
    # cambia solo la pioggia di AA
    meta, prov, pts, version = _write_fixture(
        tmp_path, [_day(precip_sum=20.0), _day("2026-11-24"), _day("2026-11-25")])
    out, rows = _run(tmp_path, "--meta-file", meta, "--province-file", prov,
                     "--points-file", pts, "--version-file", version,
                     "--now", "2026-11-23T09:00:00Z", "--days", "0")
    meta_row = [r for r in rows if r.get("t") == "meta"][0]
    assert meta_row["new_rows"] == 1
    assert meta_row["rows"] == 2 * len(cap.HAZARDS)
    data = [r for r in rows if r.get("t") != "meta"]
    assert len(data) == 2 * len(cap.HAZARDS) + 1
    last = data[-1]
    assert last["p"] == "AA" and last["h"] == "rain"
    assert last["pv"] == 2  # rain > 15 -> livello 2 (badge pioggia)


def test_rotation_drops_old_validity_days(tmp_path):
    meta, prov, pts, version = _write_fixture(
        tmp_path, [_day("2026-11-23"), _day("2026-11-24"), _day("2026-11-25")])
    _run(tmp_path, "--meta-file", meta, "--province-file", prov,
         "--points-file", pts, "--version-file", version,
         "--now", "2026-11-23T08:30:00Z", "--days", "0,1,2", "--keep-days", "10")
    # 20 giorni dopo: tutti i giorni di validita' sono fuori dalla finestra
    out, rows = _run(tmp_path, "--meta-file", meta, "--province-file", prov,
                     "--points-file", pts, "--version-file", version,
                     "--now", "2026-12-13T08:30:00Z", "--days", "0,1,2",
                     "--keep-days", "10")
    data = [r for r in rows if r.get("t") != "meta"]
    assert {r["d"] for r in data} == {"2026-11-23", "2026-11-24", "2026-11-25"}
    assert len(data) == 2 * len(cap.HAZARDS) * 3  # solo i giorni nuovi
    meta_row = [r for r in rows if r.get("t") == "meta"][0]
    assert meta_row["rotated"] == 2 * len(cap.HAZARDS) * 3


def test_engine_version_from_version_file(tmp_path):
    version = tmp_path / "VERSION"
    version.write_text("VERSION=1.3.0.49\nBUILD=179\n"
                       "APP_FILE=mri-light-1.3.0.53.html\n", encoding="utf-8")
    assert cap.engine_version_from_version_file(version) == "1.3.0.53"


def test_engine_version_falls_back_to_version_line(tmp_path):
    version = tmp_path / "VERSION"
    version.write_text("VERSION=1.3.0.49\nBUILD=179\n", encoding="utf-8")
    assert cap.engine_version_from_version_file(version) == "1.3.0.49"
