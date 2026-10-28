#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_compact_dataset.py — CONTRATTO del dataset compatto per provincia.

Cosa copre (data/latest/meteorisk-points-compact.json, prodotto da
scripts/build_meteorisk_dataset.py e pubblicato da scripts/publish_dataset.py):

  1. schema: chiavi obbligatorie, `models_covered` (i modelli del PERCORSO PRO:
     dual_best_ecmwf, best_match, ecmwf_ifs), `models_dev_only` (i modelli di
     Sviluppo, dichiarati fuori dal dataset), `fields`, `province_fields`;
  2. lunghezze coerenti: `time` = 72 ore consecutive, ogni serie = 107 x 72,
     daily 107 x 3, `model_hours` coerente;
  3. nessun null INATTESO: input completo -> zero null; dato mancante in TUTTI i
     punti -> un solo null; dato mancante in UN punto -> media sui presenti;
     modello con orizzonte piu' corto -> null DOPO la sua disponibilita', senza
     troncare gli altri;
  4. regole di aggregazione: media aritmetica, weathercode = codice peggiore,
     direzioni del vento con media VETTORIALE (350 e 10 gradi -> 0, non 180),
     arrotondamenti dichiarati; ogni modello usa i PROPRI valori (non copia);
  5. BUDGET di dimensione: 600 KB gzip SERVITI (misurato +10,7% fra gz9 e file
     servito da Pages), verificato SUL DATASET REALE quando
     data/latest/meteorisk-points.json e' presente (in CI lo e'). La serie
     sintetica NON e' un proxy valido del budget (sovrastima del 30-45%);
  6. il file viene davvero scritto/pubblicato/servito: build lo produce,
     publish_dataset.py lo copia in data/latest, i 3 workflow che costruiscono
     `_site` copiano data/latest in blocco.

BASELINE MISURATA (2026-10-28, dataset REALE servito da Pages, 257 punti, 107
province, 72 ore):
  - 2 modelli Pro, 48 campi: gzip-9 442.664 B (432,3 KB), gzip-6 462.152 B;
  - 2 modelli Pro, 27 campi (schema pubblicato): gzip-9 259.844 B (253,8 KB),
    gzip-6 271.366 B (265,0 KB);
  - 4 modelli (gfs_seamless + ARPAE, NON pubblicati) 27 campi: 506,7 KB gz9.
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

HOURS = build.COMPACT_MAX_HOURS
DAYS = 3
BUDGET_SERVED = 600 * 1024      # budget sul file che arriva al client
PAGES_INFLATION = 1.10          # gz9 -> servito (misurato: 341.398 -> 378.086)
DAY0 = "2026-01-01"
REAL_POINTS = common.DATA_LATEST / "meteorisk-points.json"
MODELS = list(common.SOURCE_MODELS)

_RANGES = {
    "temperature_2m": (-3.7, 23.5), "relativehumidity_2m": (19.0, 100.0),
    "dew_point_2m": (-16.8, 16.0), "pressure_msl": (1014.0, 1028.7),
    "cape": (0.0, 420.0), "precipitation_probability": (0.0, 95.0),
    "windspeed_10m": (0.0, 37.6), "winddirection_10m": (0.0, 360.0),
    "windspeed_100m": (0.0, 69.4), "winddirection_100m": (0.0, 360.0),
    "windgusts_10m": (0.4, 84.6), "weathercode": (0.0, 95.0),
    "precipitation": (0.0, 3.3), "showers": (0.0, 1.4),
    "freezing_level_height": (1610.0, 4090.0),
    "convective_inhibition": (0.0, 282.0), "lifted_index": (-2.0, 20.4),
    "k_index": (10.0, 40.0),
}
_LEVEL_SPEED = (0.0, 154.4)
_LEVEL_DIRECTION = (0.0, 360.0)
_LEVEL_TEMP = {850: (1.0, 14.7), 700: (-9.7, 5.6), 500: (-23.6, -8.4)}
_LEVEL_RH = {850: (6.0, 100.0), 700: (2.0, 100.0)}
_LEVEL_DEW = {850: (-26.1, 10.5), 700: (-42.2, 2.7)}
_LEVEL_GPH = {850: (1453.0, 1567.0), 700: (2986.0, 3145.0), 500: (5531.0, 5817.0)}
_DAILY_RANGES = {
    "temperature_2m_max": (0.2, 23.6), "temperature_2m_min": (-3.9, 16.6),
    "precipitation_sum": (0.0, 13.3), "windspeed_10m_max": (3.1, 37.6),
    "precipitation_probability_max": (0.0, 95.0), "weathercode": (0.0, 95.0),
}
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


def _sparse_series(rnd, field, count_lo, count_hi, zero_frac, step_h=6, n=72):
    """Serie realistica per i campi SPARSI (precipitazione, CAPE, probabilita',
    codici): la maggior parte delle ore e' secca/zero e i valori cambiano a
    blocchi, non a ogni ora."""
    out = []
    value = 0.0
    for h in range(n):
        if h % step_h == 0:
            value = 0.0 if rnd.random() < zero_frac else rnd.uniform(count_lo, count_hi)
        out.append(_quantize(value, field))
    return out


def _smooth(rnd, field, base, amp24, amp72, noise, phase=None, n=72):
    """Serie liscia con la FASE CONDIVISA dalla provincia: i campi di una
    provincia si muovono insieme, come nell'atmosfera reale."""
    p1 = rnd.uniform(0, 2 * math.pi) if phase is None else phase
    p2 = p1 * 0.7 + 1.1
    return [_quantize(base + amp24 * math.sin(2 * math.pi * h / 24.0 + p1)
                      + amp72 * math.sin(2 * math.pi * h / 72.0 + p2)
                      + rnd.gauss(0, noise), field) for h in range(n)]


def synthetic_points(seed=20261028, provinces=107, points_per_province=None,
                     models=MODELS, hours=72):
    """Punti deterministici con la STRUTTURA dei dati veri e i modelli del
    percorso PRO. Serie lisce quantizzate alla precisione di storage."""
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
        phase = rnd.uniform(0, 2 * math.pi)
        for field in fields:
            lo, hi = _range_of(field)
            span = hi - lo
            if field.startswith(("wind_direction", "winddirection")):
                base[field] = (rnd.uniform(lo, hi), span * 0.10, span * 0.05, span * 0.002)
            elif field in ("precipitation", "showers", "cape", "precipitation_probability",
                           "convective_inhibition"):
                base[field] = (lo, span * 0.05, span * 0.05, span * 0.002)
            else:
                base[field] = (lo + span * 0.5, span * 0.12, span * 0.05, span * 0.002)
        for _ in range(counts[pidx] if pidx < len(counts) else 1):
            per_model = {}
            for mi, model in enumerate(models):
                shift = (mi - 0.5) * 0.8
                hourly = {}
                for field in fields:
                    if field in ("precipitation", "showers"):
                        hourly[field] = _sparse_series(rnd, field, 0.1, 3.2, 0.72, n=hours)
                    elif field in ("precipitation_probability", "weathercode"):
                        hourly[field] = _sparse_series(rnd, field, 5, 95, 0.55, n=hours)
                    elif field in ("cape", "convective_inhibition"):
                        hourly[field] = _sparse_series(rnd, field, 20, 420, 0.70, n=hours)
                    else:
                        hourly[field] = _smooth(rnd, field, base[field][0] + shift,
                                                base[field][1], base[field][2],
                                                base[field][3] + 0.02 * mi, phase, n=hours)
                daily = {}
                for field in daily_fields:
                    lo, hi = _DAILY_RANGES[field]
                    daily[field] = [_quantize(rnd.uniform(lo, hi) + shift, field)
                                    for _ in range(DAYS)]
                per_model[model] = {"hourly": hourly, "daily": daily}
            points.append({
                "id": index,
                "provinceIdx": pidx,
                "sigla": "P%03d" % pidx,
                "lat": 36.0 + pidx * 0.1,
                "lon": 6.0 + pidx * 0.1,
                "models": per_model,
            })
            index += 1
    return points


def points_in_province_zero(n=2, seed=20261028, models=("best_match",)):
    """n punti tutti nella provincia 0, con i soli modelli richiesti."""
    return synthetic_points(seed=seed, provinces=1, points_per_province=n,
                            models=models)


ONE_REGION = [{"idx": 0, "sigla": "AA", "prov": "Test (AA)", "region": "Test",
               "lat": 42.0, "lon": 12.0}]


def build_compact(points, regions=None, generated_at="2026-01-01T00:00:00Z",
                  day0=DAY0, **kw):
    regions = regions if regions is not None else common._load_regions()
    return build.build_compact_dataset(points, regions, generated_at,
                                       common._dt.date.fromisoformat(day0),
                                       time_base=day0 + "T00:00", **kw)


def dump(compact):
    return json.dumps(compact, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@pytest.fixture(scope="module")
def compact():
    return build_compact(synthetic_points())


# ---------------------------------------------------------------------------
# 1. Schema e copertura
# ---------------------------------------------------------------------------
def test_schema_keys(compact):
    for key in ("schema", "status", "generated_at", "model", "models_covered",
                "models_derived", "models_dev_only", "licence", "attribution",
                "timezone", "forecast_days", "hours", "days", "model_hours",
                "count", "time", "fields", "units", "province_fields",
                "provinces", "series", "model_series", "daily_fields", "daily",
                "model_daily", "note"):
        assert key in compact, "chiave mancante: %s" % key
    assert compact["schema"] == "meteorisk-points-compact/1"
    assert compact["status"] == "live"
    assert compact["model"] == "best_match"
    assert compact["licence"] == "CC BY 4.0"
    assert "Open-Meteo" in compact["attribution"]
    assert "MeteoRisk" in compact["attribution"]
    assert compact["timezone"] == "Europe/Rome"
    assert compact["forecast_days"] == common.FORECAST_DAYS == 3


def test_models_covered_are_the_pro_models(compact):
    # SOLO percorso Pro: 2 modelli scaricati + il merge client-side
    assert compact["models_covered"] == ["dual_best_ecmwf", "best_match", "ecmwf_ifs"]
    assert len(compact["models_covered"]) == 3
    assert compact["models_derived"] == ["dual_best_ecmwf"]
    # i modelli di Sviluppo sono dichiarati FUORI dal dataset
    assert compact["models_dev_only"] == ["gfs_seamless", "italia_meteo_arpae_icon_2i"]
    for model in compact["models_dev_only"]:
        assert model not in compact["models_covered"]
        assert model not in compact["model_series"]
        assert model not in compact["model_daily"]
    # 2 blocchi reali: best_match in `series`, ecmwf_ifs in `model_series`
    assert set(compact["model_series"]) == {"ecmwf_ifs"}
    assert set(compact["model_daily"]) == {"ecmwf_ifs"}
    assert compact["series"]


def test_note_declares_horizon_and_layout(compact):
    note = compact["note"]
    for token in ("72 ore", "series", "model_series", "best_match", "ecmwf_ifs",
                  "dual_best_ecmwf", "gfs_seamless", "italia_meteo_arpae_icon_2i",
                  "API diretta", "257", "2.808"):
        assert token in note, "il note non dichiara %r" % token


def test_fields_lists(compact):
    assert set(compact["fields"]) == set(build.COMPACT_FIELD_NAMES)
    assert len(compact["fields"]) == 27
    assert compact["province_fields"] == ["sigla", "lat", "lon", "n_points"]
    assert compact["daily_fields"] == [f for f, _d, _s in build.COMPACT_DAILY_FIELDS]
    for field in ("temperature_2m", "relativehumidity_2m", "dew_point_2m", "pressure_msl",
                  "cape", "precipitation_probability", "windspeed_10m", "windgusts_10m",
                  "weathercode", "precipitation", "freezing_level_height",
                  "convective_inhibition", "lifted_index", "k_index",
                  "wind_speed_850hPa", "wind_direction_500hPa",
                  "geopotential_height_500hPa", "temperature_700hPa",
                  "temperature_500hPa"):
        assert field in compact["fields"], field
    # campi tagliati (stanno solo nel dataset completo): NON devono comparire
    for field in ("visibility", "windspeed_100m", "wind_speed_925hPa",
                  "dew_point_850hPa", "wind_speed_950hPa", "temperature_850hPa"):
        assert field not in compact["fields"], field
    for field in compact["fields"]:
        assert compact["units"].get(field), "unita' mancante per %s" % field


# ---------------------------------------------------------------------------
# 2. Lunghezze (72 ore, 3 giorni)
# ---------------------------------------------------------------------------
def test_lengths(compact):
    assert compact["hours"] == HOURS == 72
    assert compact["days"] == DAYS == 3
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
    for model, block in compact["model_series"].items():
        assert set(block) == set(compact["fields"]), model
        for field, rows in block.items():
            assert len(rows) == 107, (model, field)
            for row in rows:
                assert len(row) == HOURS, (model, field)
    for field, rows in compact["daily"].items():
        assert len(rows) == 107 and all(len(r) == DAYS for r in rows), field
    for model, block in compact["model_daily"].items():
        assert set(block) == set(compact["daily_fields"]), model
        assert all(len(r) == DAYS for rows in block.values() for r in rows), model
    assert compact["model_hours"] == {"best_match": 72, "ecmwf_ifs": 72}


# ---------------------------------------------------------------------------
# 3. Null
# ---------------------------------------------------------------------------
def test_complete_input_has_no_nulls(compact):
    nulls = [(f, i, h) for f, rows in compact["series"].items()
             for i, row in enumerate(rows) for h, v in enumerate(row) if v is None]
    assert nulls == []
    for model in compact["model_series"]:
        nulls = [(f, i, h) for f, rows in compact["model_series"][model].items()
                 for i, row in enumerate(rows) for h, v in enumerate(row) if v is None]
        assert nulls == [], model


def test_null_only_where_the_source_is_null():
    points = synthetic_points(provinces=2, models=("best_match",))
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
    points[0]["models"]["best_match"]["hourly"]["temperature_2m"][3] = None
    out = build_compact(points, regions=ONE_REGION)
    got = out["series"]["temperature_2m"][0][3]
    others = [p["models"]["best_match"]["hourly"]["temperature_2m"][3] for p in points[1:]]
    assert got is not None
    assert abs(got - sum(others) / float(len(others))) <= 0.05


def test_shorter_model_horizon_is_declared_and_does_not_truncate_others():
    """Se un modello ha orizzonte piu' corto: `model_hours` lo dichiara, le ore
    oltre la sua disponibilita' sono null, gli altri modelli restano a 72."""
    points = synthetic_points(models=("best_match", "ecmwf_ifs"), hours=72)
    for p in points:
        h = p["models"]["ecmwf_ifs"]["hourly"]
        for field in list(h):
            h[field] = h[field][:48]
    out = build_compact(points)
    assert out["model_hours"] == {"best_match": 72, "ecmwf_ifs": 48}
    assert len(out["time"]) == 72
    row = out["model_series"]["ecmwf_ifs"]["temperature_2m"][0]
    assert len(row) == 72
    assert all(v is not None for v in row[:48])
    assert all(v is None for v in row[48:])
    assert all(v is not None for v in out["series"]["temperature_2m"][0])


# ---------------------------------------------------------------------------
# 4. Aggregazione e arrotondamenti
# ---------------------------------------------------------------------------
def test_temperature_is_the_mean_rounded_to_one_decimal():
    points = points_in_province_zero(2)
    for p, val in zip(points, (10.04, 11.06)):
        p["models"]["best_match"]["hourly"]["temperature_2m"] = [val] * 72
    got = build_compact(points, regions=ONE_REGION)["series"]["temperature_2m"][0][0]
    assert got == pytest.approx(10.55, abs=0.05)
    assert isinstance(got, float)


def test_weathercode_is_the_worst_not_the_mean():
    points = points_in_province_zero(2)
    for p, code in zip(points, (1, 95)):
        p["models"]["best_match"]["hourly"]["weathercode"] = [code] * 72
    got = build_compact(points, regions=ONE_REGION)["series"]["weathercode"][0][0]
    assert got == 95


def test_wind_direction_uses_vector_mean_not_arithmetic():
    points = points_in_province_zero(2)
    for p, deg in zip(points, (350.0, 10.0)):
        p["models"]["best_match"]["hourly"]["winddirection_10m"] = [deg] * 72
        p["models"]["best_match"]["hourly"]["wind_direction_500hPa"] = [deg] * 72
    out = build_compact(points, regions=ONE_REGION)
    for field in ("winddirection_10m", "wind_direction_500hPa"):
        got = out["series"][field][0][0]
        assert got in (0, 360), (field, got)
        assert got != 180, field


def test_wind_direction_is_multiple_of_five_in_range():
    regions = common._load_regions()[:8]
    out = build_compact(synthetic_points(provinces=8), regions=regions)
    for field in ("winddirection_10m", "wind_direction_850hPa", "wind_direction_500hPa"):
        for row in out["series"][field]:
            for v in row:
                assert v is not None
                assert 0 <= v < 360, (field, v)
                assert v % 5 == 0, (field, v)


def test_integer_and_decimal_precision():
    points = points_in_province_zero(2)
    for p in points:
        h = p["models"]["best_match"]["hourly"]
        h["relativehumidity_2m"] = [55.4] * 72
        h["precipitation_probability"] = [29.5] * 72
        h["precipitation"] = [0.333] * 72
        h["cape"] = [120.6] * 72
        h["pressure_msl"] = [1013.24] * 72
    out = build_compact(points, regions=ONE_REGION)
    series = out["series"]
    for field in ("relativehumidity_2m", "precipitation_probability", "cape"):
        assert isinstance(series[field][0][0], int), field
    assert abs(series["precipitation"][0][0] - 0.33) < 1e-9
    assert series["pressure_msl"][0][0] == pytest.approx(1013.2, abs=1e-9)


def test_integral_floats_are_stored_as_integers():
    points = points_in_province_zero(2)
    for p in points:
        p["models"]["best_match"]["hourly"]["temperature_2m"] = [12.0] * 72
    out = build_compact(points, regions=ONE_REGION)
    assert isinstance(out["series"]["temperature_2m"][0][0], int)
    assert "12.0" not in dump(out).decode("utf-8")


def test_each_model_uses_its_own_values_not_best_match():
    points = points_in_province_zero(2, models=("best_match", "ecmwf_ifs"))
    for p, (t_bm, t_ec) in zip(points, ((10.0, 20.0), (10.0, 20.0))):
        p["models"]["best_match"]["hourly"]["temperature_2m"] = [t_bm] * 72
        p["models"]["ecmwf_ifs"]["hourly"]["temperature_2m"] = [t_ec] * 72
    out = build_compact(points, regions=ONE_REGION)
    assert out["series"]["temperature_2m"][0][0] == pytest.approx(10.0, abs=0.05)
    assert out["model_series"]["ecmwf_ifs"]["temperature_2m"][0][0] == pytest.approx(
        20.0, abs=0.05)


def test_horizon_is_capped_but_override_works():
    points = points_in_province_zero(2)
    out = build_compact(points, regions=ONE_REGION)
    assert out["hours"] == HOURS == 72
    out48 = build_compact(points, regions=ONE_REGION, hours=48)
    assert out48["hours"] == 48
    assert len(out48["time"]) == 48


# ---------------------------------------------------------------------------
# 5. Budget (600 KB serviti) sul dataset reale
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
    served = len(gz9) * PAGES_INFLATION
    print("\nREALE: %d punti, modelli %s, %d campi, %d h -> raw %d B (%.1f KB),"
          " gzip-9 %d B (%.1f KB), gzip-6 %d B (%.1f KB), servito atteso %.0f B (%.1f KB)"
          % (len(points), ",".join(compact["model_series"].keys()), len(compact["fields"]),
             compact["hours"], len(raw), len(raw) / 1024.0, len(gz9), len(gz9) / 1024.0,
             len(gz6), len(gz6) / 1024.0, served, served / 1024.0))
    assert compact["hours"] == 72
    assert len(gz9) <= BUDGET_SERVED / PAGES_INFLATION
    assert len(gz6) <= BUDGET_SERVED
    assert served <= BUDGET_SERVED


def test_gzip_synthetic_is_not_the_binding_check(compact):
    """Il budget si misura sul dataset REALE (test sopra): la serie sintetica
    sovrastima del 30-45%. Qui si verifica solo che il formato non esploda e
    che il gzip riduca davvero (rapporto raw/gzip >= 2)."""
    raw = dump(compact)
    gz9 = gzip.compress(raw, 9, mtime=0)
    gz6 = gzip.compress(raw, 6, mtime=0)
    print("\nSINTETICO 2 modelli: raw %d B (%.1f KB), gzip-9 %d B (%.1f KB),"
          " gzip-6 %d B (%.1f KB)"
          % (len(raw), len(raw) / 1024.0, len(gz9), len(gz9) / 1024.0,
             len(gz6), len(gz6) / 1024.0))
    assert len(raw) < 4000000
    assert len(raw) / float(len(gz9)) >= 2.0


# ---------------------------------------------------------------------------
# 6. Scrittura, pubblicazione, servizio
# ---------------------------------------------------------------------------
def test_build_writes_and_metadata_declares_compact():
    src = (common.Path(SCRIPTS) / "build_meteorisk_dataset.py").read_text(encoding="utf-8")
    assert "meteorisk-points-compact.json" in src
    assert '"compact": "meteorisk-points-compact.json"' in src
    assert "build_compact_dataset(" in src


def test_dataset_downloads_only_pro_models():
    """La pipeline NON deve scaricare i modelli di Sviluppo."""
    assert common.SOURCE_MODELS == ("best_match", "ecmwf_ifs")
    assert getattr(common, "DEV_MODELS", ()) == ("gfs_seamless",
                                                 "italia_meteo_arpae_icon_2i")
    assert common.DUAL_MODELS == "best_match,ecmwf_ifs"
    for model in common.DEV_MODELS:
        assert model not in common.SOURCE_MODELS


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
