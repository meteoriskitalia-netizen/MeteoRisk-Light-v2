#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_compact_dataset.py — CONTRATTO del dataset compatto per provincia.

Cosa copre (data/latest/meteorisk-points-compact.json, prodotto da
scripts/build_meteorisk_dataset.py e pubblicato da scripts/publish_dataset.py):

  1. schema: chiavi obbligatorie, `fields`, `province_fields`, unita';
  2. lunghezze coerenti: `time` = 72 ore consecutive, ogni serie = 107 x 72,
     ogni record provincia della stessa lunghezza, daily = 107 x 3;
  3. nessun null INATTESO: input completo -> zero null; un dato mancante in
     TUTTI i punti di una provincia/ora -> un solo null; un dato mancante in UN
     punto -> media sui soli valori presenti (non null);
  4. regole di aggregazione: media aritmetica, weathercode = codice peggiore,
     direzioni del vento con media VETTORIALE (350 e 10 gradi -> 0, non 180),
     arrotondamenti dichiarati (interi, 1 decimale, 2 decimali, passi di 5 gradi);
  5. BUDGET di dimensione: gzip <= 400 KB, verificato SUL DATASET REALE quando
     data/latest/meteorisk-points.json e' presente (in CI lo e') e, in sua
     assenza, su una serie sintetica che riproduce la struttura statistica dei
     dati veri (serie lisce + quantizzazione alla precisione di storage);
  6. il file viene davvero scritto/pubblicato/servito: build lo produce,
     publish_dataset.py lo copia in data/latest, i 3 workflow che costruiscono
     `_site` copiano data/latest in blocco.

BASELINE MISURATA (2026-10-28, dataset REALE pubblicato su main: 257 punti,
107 province, 72 ore, best_match):
  - meteorisk-points.json (completo): 9.294.452 byte;
  - compatto: raw 1.488.449 byte, gzip-9 346.094 byte (338,0 KB),
    gzip-6 360.041 byte (351,6 KB) -> 27x in meno del completo.
"""

import gzip
import json
import math
import os
import random
import re
import sys

import pytest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import common  # noqa: E402
import build_meteorisk_dataset as build  # noqa: E402

HOURS = 72
DAYS = 3
BUDGET_GZIP = 400 * 1024
DAY0 = "2026-01-01"
REAL_POINTS = common.DATA_LATEST / "meteorisk-points.json"

# Intervalli MISURATI sul dataset reale (min/max dei 257 punti, 72 ore):
# in CI data/latest c'e' e il budget si misura sui dati veri; questa tabella
# serve solo al rimpiazzo sintetico.
_RANGES = {
    "temperature_2m": (-3.7, 23.5),
    "relativehumidity_2m": (19.0, 100.0),
    "dew_point_2m": (-16.8, 16.0),
    "pressure_msl": (1014.0, 1028.7),
    "cape": (0.0, 420.0),
    "precipitation_probability": (0.0, 95.0),
    "windspeed_10m": (0.0, 37.6),
    "winddirection_10m": (0.0, 360.0),
    "windspeed_100m": (0.0, 69.4),
    "winddirection_100m": (0.0, 360.0),
    "windgusts_10m": (0.4, 84.6),
    "weathercode": (0.0, 95.0),
    "precipitation": (0.0, 3.3),
    "showers": (0.0, 1.4),
    "freezing_level_height": (1610.0, 4090.0),
    "convective_inhibition": (0.0, 282.0),
    "lifted_index": (-2.0, 20.4),
    "k_index": (10.0, 40.0),
}
_LEVEL_SPEED = (0.0, 154.4)
_LEVEL_DIRECTION = (0.0, 360.0)
_LEVEL_TEMP = {850: (1.0, 14.7), 700: (-9.7, 5.6), 500: (-23.6, -8.4)}
_LEVEL_RH = {850: (6.0, 100.0), 700: (2.0, 100.0)}
_LEVEL_DEW = {850: (-26.1, 10.5), 700: (-42.2, 2.7)}
_LEVEL_GPH = {850: (1453.0, 1567.0), 700: (2986.0, 3145.0), 500: (5531.0, 5817.0)}
_DAILY_RANGES = {
    "temperature_2m_max": (0.2, 23.6),
    "temperature_2m_min": (-3.9, 16.6),
    "precipitation_sum": (0.0, 13.3),
    "windspeed_10m_max": (3.1, 37.6),
    "precipitation_probability_max": (0.0, 95.0),
    "weathercode": (0.0, 95.0),
}
# Distribuzione MISURATA dei punti per provincia (31x1, 29x2, 26x3, 15x4, 6x5).
_POINTS_PER_PROVINCE = sorted([1] * 31 + [2] * 29 + [3] * 26 + [4] * 15 + [5] * 6)
_PRECISION = {f: d for f, d, _s in build.COMPACT_HOURLY_FIELDS}
_DAILY_PRECISION = {f: d for f, d, _s in build.COMPACT_DAILY_FIELDS}


def _range_of(field):
    if field in _RANGES:
        return _RANGES[field]
    if field.startswith("wind_speed_"):
        return _LEVEL_SPEED
    if field.startswith("wind_direction_"):
        return _LEVEL_DIRECTION
    if field.startswith("temperature_"):
        return _LEVEL_TEMP[int(field.rsplit("_", 1)[1][:-3])]
    if field.startswith("relative_humidity_"):
        return _LEVEL_RH[int(field.rsplit("_", 1)[1][:-3])]
    if field.startswith("dew_point_"):
        return _LEVEL_DEW[int(field.rsplit("_", 1)[1][:-3])]
    if field.startswith("geopotential_height_"):
        return _LEVEL_GPH[int(field.rsplit("_", 1)[1][:-3])]
    raise KeyError(field)


def _quantize(value, field):
    digits = _PRECISION.get(field, _DAILY_PRECISION.get(field, 1))
    return float(int(round(value))) if digits == 0 else round(value, digits)


def _smooth(rnd, field, base, amp24, amp72, noise):
    p1, p2 = rnd.uniform(0, 2 * math.pi), rnd.uniform(0, 2 * math.pi)
    return [_quantize(base + amp24 * math.sin(2 * math.pi * h / 24.0 + p1)
                      + amp72 * math.sin(2 * math.pi * h / 72.0 + p2)
                      + rnd.gauss(0, noise), field) for h in range(HOURS)]


def synthetic_points(seed=20261028, provinces=107, points_per_province=None):
    """Punti deterministici con la STRUTTURA dei dati veri.

    Le serie sono lisce (ciclo diurno + tendenza a 72 h + rumore piccolo) e
    quantizzate alla precisione di storage, come i dati Open-Meteo reali: una
    serie di valori uniformi e indipendenti non rappresenterebbe nulla di reale
    e gonfierebbe l'entropia (misurato: 518 KB contro 374 KB di questa).
    """
    rnd = random.Random(seed)
    counts = list(_POINTS_PER_PROVINCE)
    rnd.shuffle(counts)
    if points_per_province is not None:
        counts = [int(points_per_province)] * provinces
    fields = [f for f, _d, _s in build.COMPACT_HOURLY_FIELDS]
    daily_fields = [f for f, _d, _s in build.COMPACT_DAILY_FIELDS]
    points = []
    index = 0
    for pidx in range(provinces):
        base = {}
        for field in fields:
            lo, hi = _range_of(field)
            span = hi - lo
            if field.startswith(("wind_direction", "winddirection")):
                base[field] = (rnd.uniform(lo, hi), span * 0.10, span * 0.05, span * 0.01)
            elif field in ("precipitation", "showers", "cape", "precipitation_probability",
                           "convective_inhibition"):
                base[field] = (lo, span * 0.05, span * 0.05, span * 0.01)
            else:
                base[field] = (lo + span * 0.5, span * 0.12, span * 0.05, span * 0.01)
        for _ in range(counts[pidx] if pidx < len(counts) else 1):
            hourly = {field: _smooth(rnd, field, *base[field]) for field in fields}
            daily = {}
            for field in daily_fields:
                lo, hi = _DAILY_RANGES[field]
                daily[field] = [_quantize(rnd.uniform(lo, hi), field) for _ in range(DAYS)]
            points.append({
                "id": index,
                "provinceIdx": pidx,
                "sigla": "P%03d" % pidx,
                "lat": 36.0 + pidx * 0.1,
                "lon": 6.0 + pidx * 0.1,
                "models": {"best_match": {"hourly": hourly, "daily": daily}},
            })
            index += 1
    return points


def points_in_province_zero(n=2, seed=20261028):
    """n punti tutti nella provincia 0 (per i test di aggregazione)."""
    return synthetic_points(seed=seed, provinces=1, points_per_province=n)


ONE_REGION = [{"idx": 0, "sigla": "AA", "prov": "Test (AA)", "region": "Test",
               "lat": 42.0, "lon": 12.0}]


def build_compact(points, regions=None, generated_at="2026-01-01T00:00:00Z", day0=DAY0):
    regions = regions if regions is not None else common._load_regions()
    return build.build_compact_dataset(points, regions, generated_at,
                                       common._dt.date.fromisoformat(day0),
                                       time_base=day0 + "T00:00")


def dump(compact):
    return json.dumps(compact, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@pytest.fixture(scope="module")
def compact():
    return build_compact(synthetic_points())


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------
def test_schema_keys(compact):
    for key in ("schema", "status", "generated_at", "model", "licence", "attribution",
                "timezone", "forecast_days", "hours", "count", "time", "fields",
                "units", "province_fields", "provinces", "series", "daily_fields", "daily"):
        assert key in compact, "chiave mancante: %s" % key
    assert compact["schema"] == "meteorisk-points-compact/1"
    assert compact["status"] == "live"
    assert compact["model"] == "best_match"
    assert compact["licence"] == "CC BY 4.0"
    assert "Open-Meteo" in compact["attribution"]
    assert "MeteoRisk" in compact["attribution"]
    assert compact["timezone"] == "Europe/Rome"
    assert compact["forecast_days"] == common.FORECAST_DAYS
    assert compact["generated_at"] == "2026-01-01T00:00:00Z"


def test_fields_lists(compact):
    assert compact["fields"] == [f for f, _d, _s in build.COMPACT_HOURLY_FIELDS]
    assert len(compact["fields"]) == 48
    assert compact["province_fields"] == ["sigla", "lat", "lon", "n_points"]
    assert compact["daily_fields"] == [f for f, _d, _s in build.COMPACT_DAILY_FIELDS]
    # i campi che la mappa dei rischi e i badge leggono DEVONO esserci
    for field in ("temperature_2m", "relativehumidity_2m", "dew_point_2m", "pressure_msl",
                  "cape", "precipitation_probability", "windspeed_10m", "windgusts_10m",
                  "weathercode", "precipitation", "showers", "freezing_level_height",
                  "convective_inhibition", "lifted_index", "k_index",
                  "wind_speed_850hPa", "wind_direction_500hPa",
                  "geopotential_height_500hPa", "temperature_700hPa"):
        assert field in compact["fields"], field
    # visibility esclusa: nessuna funzione del client la legge
    assert "visibility" not in compact["fields"]
    for field in compact["fields"]:
        assert compact["units"].get(field), "unita' mancante per %s" % field


# ---------------------------------------------------------------------------
# 2. Lunghezze
# ---------------------------------------------------------------------------
def test_lengths(compact):
    assert compact["hours"] == HOURS
    assert len(compact["time"]) == HOURS
    assert compact["time"][0] == DAY0 + "T00:00"
    assert compact["time"][-1] == "2026-01-03T23:00"
    prev = None
    for label in compact["time"]:
        cur = common._dt.datetime.strptime(label, "%Y-%m-%dT%H:%M")
        if prev is not None:
            assert (cur - prev) == common._dt.timedelta(hours=1), label
        prev = cur
    assert compact["count"] == 107
    assert len(compact["provinces"]) == 107
    for rec in compact["provinces"]:
        assert len(rec) == len(compact["province_fields"])
    for field, rows in compact["series"].items():
        assert len(rows) == 107, field
        for row in rows:
            assert len(row) == HOURS, field
    for field, rows in compact["daily"].items():
        assert len(rows) == 107, field
        for row in rows:
            assert len(row) == DAYS, field


# ---------------------------------------------------------------------------
# 3. Null: nessuno inatteso
# ---------------------------------------------------------------------------
def test_complete_input_has_no_nulls(compact):
    nulls = [(f, i, h) for f, rows in compact["series"].items()
             for i, row in enumerate(rows) for h, v in enumerate(row) if v is None]
    assert nulls == []


def test_null_only_where_the_source_is_null():
    points = synthetic_points(provinces=2)
    for p in points:
        if p["provinceIdx"] == 0:
            p["models"]["best_match"]["hourly"]["temperature_2m"][0] = None
            p["models"]["best_match"]["hourly"]["temperature_2m"][5] = None
    out = build_compact(points)
    row = out["series"]["temperature_2m"][0]
    assert row[0] is None and row[5] is None
    assert all(v is not None for h, v in enumerate(row) if h not in (0, 5))
    assert all(v is not None for v in out["series"]["temperature_2m"][1])


def test_null_in_one_point_uses_the_others():
    points = points_in_province_zero(3)
    first = points[0]["models"]["best_match"]["hourly"]["temperature_2m"][3]
    points[0]["models"]["best_match"]["hourly"]["temperature_2m"][3] = None
    out = build_compact(points, regions=ONE_REGION)
    got = out["series"]["temperature_2m"][0][3]
    others = [p["models"]["best_match"]["hourly"]["temperature_2m"][3] for p in points[1:]]
    assert got is not None
    assert abs(got - sum(others) / float(len(others))) <= 0.05
    assert first is not None


def test_no_field_of_only_nulls_when_source_has_data():
    out = build_compact(synthetic_points(provinces=8), regions=common._load_regions()[:8])
    empty = [f for f, rows in out["series"].items() if all(v is None for row in rows for v in row)]
    assert empty == []


# ---------------------------------------------------------------------------
# 4. Regole di aggregazione e arrotondamenti
# ---------------------------------------------------------------------------
def test_temperature_is_the_mean_rounded_to_one_decimal():
    points = points_in_province_zero(2)
    for p, val in zip(points, (10.04, 11.06)):
        p["models"]["best_match"]["hourly"]["temperature_2m"] = [val] * HOURS
    out = build_compact(points, regions=ONE_REGION)
    got = out["series"]["temperature_2m"][0][0]
    assert got == pytest.approx(10.55, abs=0.05)
    assert isinstance(got, float)


def test_weathercode_is_the_worst_not_the_mean():
    points = points_in_province_zero(2)
    for p, code in zip(points, (1, 95)):
        p["models"]["best_match"]["hourly"]["weathercode"] = [code] * HOURS
    out = build_compact(points, regions=ONE_REGION)
    assert out["series"]["weathercode"][0][0] == 95


def test_wind_direction_uses_vector_mean_not_arithmetic():
    # 350 e 10 gradi: media aritmetica 180 (vento OPPOSTO), vettoriale 0
    points = points_in_province_zero(2)
    for p, deg in zip(points, (350.0, 10.0)):
        p["models"]["best_match"]["hourly"]["winddirection_10m"] = [deg] * HOURS
    out = build_compact(points, regions=ONE_REGION)
    got = out["series"]["winddirection_10m"][0][0]
    assert got in (0, 360), got
    assert got != 180


def test_wind_direction_of_pressure_levels_uses_the_same_rule():
    points = points_in_province_zero(2)
    for p, deg in zip(points, (350.0, 10.0)):
        p["models"]["best_match"]["hourly"]["wind_direction_500hPa"] = [deg] * HOURS
    out = build_compact(points, regions=ONE_REGION)
    got = out["series"]["wind_direction_500hPa"][0][0]
    assert got in (0, 360), got


def test_wind_direction_is_multiple_of_five_in_range():
    regions = common._load_regions()[:12]
    out = build_compact(synthetic_points(provinces=12), regions=regions)
    for field in ("winddirection_10m", "winddirection_100m", "wind_direction_500hPa"):
        for row in out["series"][field]:
            for v in row:
                assert v is not None
                assert 0 <= v < 360, (field, v)
                assert v % 5 == 0, (field, v)


def test_integer_and_decimal_precision():
    points = points_in_province_zero(2)
    for p in points:
        p["models"]["best_match"]["hourly"]["relativehumidity_2m"] = [55.4] * HOURS
        p["models"]["best_match"]["hourly"]["precipitation_probability"] = [29.5] * HOURS
        p["models"]["best_match"]["hourly"]["precipitation"] = [0.333] * HOURS
        p["models"]["best_match"]["hourly"]["cape"] = [120.6] * HOURS
        p["models"]["best_match"]["hourly"]["pressure_msl"] = [1013.24] * HOURS
    out = build_compact(points, regions=ONE_REGION)
    series = out["series"]
    for field in ("relativehumidity_2m", "precipitation_probability", "cape"):
        assert isinstance(series[field][0][0], int), field
    assert abs(series["precipitation"][0][0] - 0.33) < 1e-9
    assert series["pressure_msl"][0][0] == pytest.approx(1013.2, abs=1e-9)


def test_integral_floats_are_stored_as_integers():
    points = points_in_province_zero(2)
    for p in points:
        p["models"]["best_match"]["hourly"]["temperature_2m"] = [12.0] * HOURS
    out = build_compact(points, regions=ONE_REGION)
    assert isinstance(out["series"]["temperature_2m"][0][0], int)
    assert "12.0" not in dump(out).decode("utf-8")


# ---------------------------------------------------------------------------
# 5. Budget di dimensione
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not REAL_POINTS.exists(), reason="data/latest assente (copia di release)")
def test_gzip_budget_on_real_dataset():
    full = json.loads(REAL_POINTS.read_text(encoding="utf-8"))
    points = full.get("points") or []
    if len(points) < 100:
        pytest.skip("dataset reale non popolato (%d punti)" % len(points))
    compact = build_compact(points, generated_at=full.get("generated_at") or "x",
                            day0=full.get("day0") or DAY0)
    raw = dump(compact)
    gz9 = gzip.compress(raw, 9, mtime=0)
    gz6 = gzip.compress(raw, 6, mtime=0)
    print("\nREALE: punti %d, raw %d B (%.1f KB), gzip-9 %d B (%.1f KB), gzip-6 %d B (%.1f KB)"
          % (len(points), len(raw), len(raw) / 1024.0, len(gz9), len(gz9) / 1024.0,
             len(gz6), len(gz6) / 1024.0))
    assert len(gz9) <= BUDGET_GZIP
    assert len(gz6) <= BUDGET_GZIP


def test_gzip_budget_synthetic(compact):
    raw = dump(compact)
    gz9 = gzip.compress(raw, 9, mtime=0)
    gz6 = gzip.compress(raw, 6, mtime=0)
    print("\nSINTETICO: raw %d B (%.1f KB), gzip-9 %d B (%.1f KB), gzip-6 %d B (%.1f KB)"
          % (len(raw), len(raw) / 1024.0, len(gz9), len(gz9) / 1024.0,
             len(gz6), len(gz6) / 1024.0))
    assert len(gz9) <= BUDGET_GZIP, "gzip-9 %d > %d" % (len(gz9), BUDGET_GZIP)
    assert len(gz6) <= BUDGET_GZIP, "gzip-6 %d > %d" % (len(gz6), BUDGET_GZIP)
    assert len(raw) < 2500000


# ---------------------------------------------------------------------------
# 6. Scrittura, pubblicazione, servizio
# ---------------------------------------------------------------------------
def test_build_writes_and_metadata_declares_compact():
    src = (common.Path(SCRIPTS) / "build_meteorisk_dataset.py").read_text(encoding="utf-8")
    assert 'meteorisk-points-compact.json' in src
    assert '"compact": "meteorisk-points-compact.json"' in src
    assert "build_compact_dataset(" in src


def test_publish_copies_compact():
    src = (common.Path(SCRIPTS) / "publish_dataset.py").read_text(encoding="utf-8")
    block = re.search(r"files = \[(.*?)\]", src, re.S)
    assert block, "lista files non trovata in publish_dataset.py"
    assert "meteorisk-points-compact.json" in block.group(1)


def test_workflows_ship_data_latest():
    workflows = common.Path(SCRIPTS).parent / ".github" / "workflows"
    for name in ("deploy-pages.yml", "radar-engine.yml", "update-weather-data.yml"):
        path = workflows / name
        assert path.exists(), "workflow mancante: %s" % name
        assert "cp -R data/latest _site/data/latest" in path.read_text(encoding="utf-8"), name
