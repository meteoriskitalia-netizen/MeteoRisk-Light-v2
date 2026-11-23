# -*- coding: utf-8 -*-
"""Test dell'ESTENSIONE ORARIA 72..167 (schema `meteorisk.hourly-ext/1`).

Fase 1 "previsioni orarie a 7 giorni" (opzione D + E). Il dataset BASE resta a
72 ore / 3 giorni (`meteorisk-points.json`); l'estensione e' un file AGGIUNTIVO
con le ore 72..167 dei 5 campi usati da grafico + tabella della vista Simple,
per il `selected_point` di ogni provincia e per ENTRAMBI i modelli.

Livelli del test, dichiarati:
  (A) UNIT sul builder (`build_meteorisk_dataset.build_hourly_extended`) con
      input sintetici: schema, 96 ore, 107 province, 5 campi, 2 modelli,
      point_id == selected_point, valori presi dall'ora giusta, orizzonte
      esaurito -> null + `null_reason`, modello assente -> h null + motivo,
      continuita' ora 71 base -> ora 0 estensione, scrittura atomica, budget.
  (B) CONTRATTO SUL FILE REALE (`data/latest/hourly_extended.json`): skip
      dichiarato se il file non e' ancora pubblicato (dataset precedente alla
      7 giorni). Nessuna rete: si legge solo il working tree.
"""
import datetime as _dt
import gzip
import io
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import common  # noqa: E402
import build_meteorisk_dataset as bmd  # noqa: E402

LATEST = os.path.join(ROOT, "data", "latest")
EXTENDED = os.path.join(LATEST, "hourly_extended.json")
POINTS = os.path.join(LATEST, "meteorisk-points.json")
PROVINCES = os.path.join(LATEST, "meteorisk-provinces.json")

DAY0 = _dt.date(2026, 11, 23)
TIME_BASE = "2026-11-23T00:00"
GENERATED = "2026-11-23T03:00:00Z"


def _hourly(n=168, offset=0.0):
    """Serie sintetiche DETERMINISTICHE dei 5 campi dell'estensione."""
    return {
        "temperature_2m": [round(10.0 + offset + i * 0.01, 2) for i in range(n)],
        "precipitation": [round(0.1 * (i % 5), 2) for i in range(n)],
        "precipitation_probability": [(i * 7) % 101 for i in range(n)],
        "windspeed_10m": [round(5.0 + (i % 13), 2) for i in range(n)],
        "weathercode": [i % 4 for i in range(n)],
    }


def _dataset(hours=168, models=("best_match", "ecmwf_ifs"), drop_models=()):
    """107 province REALI (regions_data) con sole serie orarie sintetiche."""
    regions = common._load_regions()
    points_out, provinces = [], []
    for r in regions:
        pid = r["idx"]
        mods = {}
        for m in models:
            if m in drop_models:
                continue
            mods[m] = {"hourly": _hourly(hours, offset=pid * 0.01), "daily": {}}
        points_out.append({"id": pid, "provinceIdx": pid, "sigla": r["sigla"],
                           "coordIdx": 0, "lat": r["lat"], "lon": r["lon"],
                           "models": mods})
        provinces.append({"idx": r["idx"], "sigla": r["sigla"], "prov": r["prov"],
                          "region": r["region"],
                          "selected_point": {"id": pid, "coordIdx": 0,
                                             "lat": r["lat"], "lon": r["lon"]},
                          "days": []})
    return points_out, provinces


def _build(**kwargs):
    points_out, provinces = _dataset(**kwargs)
    payload, stats = bmd.build_hourly_extended(points_out, provinces, DAY0,
                                               TIME_BASE, GENERATED)
    return payload, stats, points_out, provinces


# ------------------------------------------------------------------ (A) UNIT

def test_schema_and_shape():
    payload, stats, _pts, _prov = _build()
    assert payload["schema"] == "meteorisk.hourly-ext/1"
    assert payload["timezone"] == "Europe/Rome"
    assert payload["day0"] == "2026-11-23"
    assert payload["generated_at"] == GENERATED
    assert payload["start_hour"] == 72
    assert payload["fields"] == ["t", "p", "pp", "w", "c"]
    assert list(payload["source_fields"]) == list(common.EXTENDED_FIELDS)
    assert payload["units"] == {"t": "C", "p": "mm", "pp": "%", "w": "km/h", "c": "wmo"}
    assert len(payload["hours"]) == 96
    assert stats["provinces"] == 107
    assert stats["points"] == 107 * 2
    assert set(payload["provinces"]) == {p["sigla"] for p in _prov}
    for entry in payload["provinces"].values():
        models = [r["model"] for r in entry["points"]]
        assert sorted(models) == ["best_match", "ecmwf_ifs"]
        for rec in entry["points"]:
            assert len(rec["h"]) == 96
            assert all(len(row) == 5 for row in rec["h"])


def test_point_id_is_the_selected_point_for_both_models():
    payload, _stats, _pts, provinces = _build()
    for prov in provinces:
        entry = payload["provinces"][prov["sigla"]]
        for rec in entry["points"]:
            assert rec["point_id"] == prov["selected_point"]["id"]


def test_rows_come_from_hour_72_onward():
    payload, _stats, pts, _prov = _build()
    src = pts[0]["models"]["best_match"]["hourly"]
    prov_sigla = common._load_regions()[0]["sigla"]
    rec = [r for r in payload["provinces"][prov_sigla]["points"]
           if r["model"] == "best_match"][0]
    # prima riga = ora 72 della serie (NON ora 0, NON una media)
    assert rec["h"][0][0] == round(src["temperature_2m"][72], 1)
    assert rec["h"][0][1] == round(src["precipitation"][72], 1)
    assert rec["h"][0][2] == src["precipitation_probability"][72]
    assert rec["h"][0][3] == round(src["windspeed_10m"][72], 1)
    assert rec["h"][0][4] == int(src["weathercode"][72])
    # ultima riga = ora 167
    assert rec["h"][-1][0] == round(src["temperature_2m"][167], 1)


def test_continuity_with_base_nightly_hour_71():
    payload, _stats, _pts, _prov = _build()
    base = bmd.extended_hours(TIME_BASE, DAY0)
    assert base == payload["hours"]
    first = _dt.datetime.fromisoformat(payload["hours"][0])
    # l'ora 71 del dataset base e' time_base + 71 h: l'ora 0 qui e' +72 h
    assert first - _dt.datetime.fromisoformat(TIME_BASE) == _dt.timedelta(hours=72)
    assert payload["hours"][0] == "2026-11-26T00:00"
    assert payload["hours"][-1] == "2026-11-29T23:00"
    stamps = [_dt.datetime.fromisoformat(h) for h in payload["hours"]]
    assert all((stamps[i + 1] - stamps[i]) == _dt.timedelta(hours=1)
               for i in range(len(stamps) - 1))


def test_short_horizon_is_null_with_reason_not_interpolated():
    payload, _stats, _pts, provinces = _build(hours=100)
    sigla = provinces[0]["sigla"]
    rec = payload["provinces"][sigla]["points"][0]
    # ore 72..99 presenti (28 righe), 100..167 null
    assert any(v is not None for v in rec["h"][27])
    assert all(v is None for v in rec["h"][28])
    assert all(v is None for v in rec["h"][-1])
    assert rec["null_reason"] == "model_horizon_100h"


def test_missing_model_is_null_with_reason():
    payload, _stats, _pts, provinces = _build(drop_models=("ecmwf_ifs",))
    recs = payload["provinces"][provinces[0]["sigla"]]["points"]
    missing = [r for r in recs if r["model"] == "ecmwf_ifs"][0]
    assert missing["h"] is None
    assert missing["null_reason"] == "model_missing"


def test_source_null_cell_is_propagated_not_zeroed():
    points_out, provinces = _dataset()
    points_out[0]["models"]["best_match"]["hourly"]["temperature_2m"][75] = None
    payload, _stats = bmd.build_hourly_extended(points_out, provinces, DAY0,
                                                TIME_BASE, GENERATED)
    rec = payload["provinces"][provinces[0]["sigla"]]["points"][0]
    assert rec["h"][75 - 72][0] is None          # null della fonte, non 0
    assert rec["h"][75 - 72][1] is not None      # le altre celle restano


def test_atomic_write_leaves_no_temp_file(tmp_path):
    payload, _stats, _pts, _prov = _build()
    out = tmp_path / "hourly_extended.json"
    size, sha = common.write_json_atomic(out, payload, separators=(",", ":"))
    assert out.exists()
    assert size == out.stat().st_size
    assert len(sha) == 64
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    assert json.loads(raw.decode("utf-8"))["schema"] == common.EXTENDED_SCHEMA
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != out.name]
    assert leftovers == []


def test_gzip_budget_under_200kb():
    payload, _stats, _pts, _prov = _build()
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    gz = gzip.compress(blob, 9)
    assert len(gz) <= 200 * 1024, "gzip %d byte > 200 KB" % len(gz)


# ------------------------------------------------------- (B) DATASET REALE

requires_ext = pytest.mark.skipif(
    not os.path.exists(EXTENDED),
    reason="data/latest/hourly_extended.json non pubblicato (dataset precedente alla 7 giorni)",
)


@requires_ext
def test_real_extension_contract():
    ext = json.loads(io.open(EXTENDED, encoding="utf-8").read())
    points = json.loads(io.open(POINTS, encoding="utf-8").read())
    provinces = json.loads(io.open(PROVINCES, encoding="utf-8").read())
    assert ext["schema"] == "meteorisk.hourly-ext/1"
    assert ext["start_hour"] == 72
    assert len(ext["hours"]) == 96
    assert ext["fields"] == ["t", "p", "pp", "w", "c"]
    assert list(ext["source_fields"]) == list(common.EXTENDED_FIELDS)
    assert len(ext["provinces"]) == 107

    sel = {p["sigla"]: (p.get("selected_point") or {}).get("id")
           for p in provinces["provinces"]}
    for sigla, entry in ext["provinces"].items():
        assert sel.get(sigla) is not None, "%s non e' una provincia del dataset" % sigla
        recs = entry["points"]
        assert sorted(r["model"] for r in recs) == ["best_match", "ecmwf_ifs"]
        for rec in recs:
            assert rec["point_id"] == sel[sigla]
            rows = rec["h"]
            if rows is None:
                assert rec.get("null_reason")
                continue
            assert len(rows) == 96
            for row in rows:
                assert len(row) == 5
                assert row[2] is None or isinstance(row[2], int)
                assert row[4] is None or isinstance(row[4], int)
                for v in row:
                    assert v is None or isinstance(v, (int, float))
            if all(all(v is None for v in r) for r in rows[-1:]):
                assert rec.get("null_reason"), "coda null senza motivo: %s" % sigla

    # continuita' con il dataset base
    meta = json.loads(io.open(os.path.join(LATEST, "metadata.json"), encoding="utf-8").read())
    expected = (_dt.datetime.fromisoformat(meta["time_base"])
                + _dt.timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M")
    assert ext["hours"][0] == expected

    blob = io.open(EXTENDED, "rb").read()
    assert len(gzip.compress(blob, 9)) <= 200 * 1024
