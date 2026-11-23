# -*- coding: utf-8 -*-
"""Test del payload orario compatto della vista Simple (schema simple-hourly/1).

Il test ha due livelli, dichiarati:
  (A) UNIT: costruzione su fixture sintetiche -> schema, 72 ore senza e 168 ore
      con l'estensione (nessun buco inventato: null + motivo), 5 campi,
      null propagati (nessuno zero inventato), punto scelto rispettato,
      day0 assente = errore, campo orario assente = errore (nessun file).
  (B) CONTRATTO SUL DATASET REALE (skip dichiarato se `data/latest/` non ha
      i file): 107 province, 72 o 168 ore x 5 campi, valori finiti/null coerenti
      con `meteorisk-points.json` per il `selected_point` di ogni provincia,
      ore 72..167 coerenti con `hourly_extended.json`, byte raw <= 400 KB e
      gzip <= 100 KB.

Nessuna rete: si legge solo il working tree.
"""
import gzip
import io
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "simple"))

import build_simple_hourly as bsh  # noqa: E402

DATA_LATEST = os.path.join(ROOT, "data", "latest")
POINTS = os.path.join(DATA_LATEST, "meteorisk-points.json")
PROVINCES = os.path.join(DATA_LATEST, "meteorisk-provinces.json")
SIMPLE = os.path.join(DATA_LATEST, "simple_hourly.json")
EXTENDED = os.path.join(DATA_LATEST, "hourly_extended.json")


def _hourly(values=None, n=72, missing=()):
    base = {
        "temperature_2m": [10.0 + i * 0.1 for i in range(n)],
        "precipitation": [0.0 for _ in range(n)],
        "precipitation_probability": [0 for _ in range(n)],
        "windspeed_10m": [5.0 for _ in range(n)],
        "weathercode": [0 for _ in range(n)],
    }
    for src in missing:
        base.pop(src, None)
    if values:
        base.update(values)
    return base


def _fixture(provinces=None, points=None, day0="2026-11-23"):
    prov = provinces if provinces is not None else [
        {"sigla": "RM", "selected_point": {"id": 2}, "days": []},
        {"sigla": "MI", "selected_point": {"id": 1}, "days": []},
    ]
    pts = points if points is not None else [
        {"id": 1, "models": {"best_match": {"hourly": _hourly(), "daily": {}}}},
        {"id": 2, "models": {"best_match": {"hourly": _hourly(), "daily": {}}}},
    ]
    return (
        {"day0": day0, "timezone": "Europe/Rome", "generated_at": "2026-11-23T19:00:00Z",
         "points": pts},
        {"generated_at": "2026-11-23T19:00:01Z", "timezone": "Europe/Rome",
         "provinces": prov},
    )


# ---------------------------------------------------------------- (A) UNIT

def test_schema_and_shape():
    pts, prov = _fixture()
    payload, stats = bsh.build_payload(pts, prov)
    assert payload["schema"] == "simple-hourly/1"
    assert payload["day0"] == "2026-11-23"
    assert payload["timezone"] == "Europe/Rome"
    assert payload["fields"] == ["t", "p", "pp", "w", "c"]
    assert payload["units"] == {"t": "C", "p": "mm", "pp": "%", "w": "km/h", "c": "wmo"}
    assert len(payload["hours"]) == 72
    assert payload["hours"][0] == "2026-11-23T00:00"
    assert payload["hours"][-1] == "2026-11-25T23:00"
    assert stats["provinces"] == 2
    assert set(payload["provinces"]) == {"RM", "MI"}
    for entry in payload["provinces"].values():
        assert len(entry["h"]) == 72
        assert all(len(row) == 5 for row in entry["h"])


def test_values_come_from_selected_point():
    pts, prov = _fixture()
    # RM punta al punto 2: deve leggere QUELLO, non il primo della lista.
    pts["points"][1]["models"]["best_match"]["hourly"]["temperature_2m"] = [99.0] * 72
    payload, _ = bsh.build_payload(pts, prov)
    assert payload["provinces"]["RM"]["id"] == 2
    assert payload["provinces"]["RM"]["h"][0][0] == 99.0
    assert payload["provinces"]["MI"]["id"] == 1
    assert payload["provinces"]["MI"]["h"][0][0] == 10.0


def test_null_is_preserved_not_zero():
    pts, prov = _fixture()
    h = pts["points"][0]["models"]["best_match"]["hourly"]
    h["temperature_2m"][3] = None
    h["precipitation"][4] = None
    h["precipitation_probability"][5] = None
    h["windspeed_10m"][6] = None
    h["weathercode"][7] = None
    payload, _ = bsh.build_payload(pts, prov)
    row_bad = payload["provinces"]["MI"]["h"][3]
    row_bad2 = payload["provinces"]["MI"]["h"][4]
    row_bad3 = payload["provinces"]["MI"]["h"][5]
    row_bad4 = payload["provinces"]["MI"]["h"][6]
    row_bad5 = payload["provinces"]["MI"]["h"][7]
    assert row_bad[0] is None
    assert row_bad2[1] is None
    assert row_bad3[2] is None
    assert row_bad4[3] is None
    assert row_bad5[4] is None
    # le altre ore restano numeriche
    assert isinstance(payload["provinces"]["MI"]["h"][0][0], float)


def test_rounding_declared():
    pts, prov = _fixture()
    h = pts["points"][0]["models"]["best_match"]["hourly"]
    h["temperature_2m"][0] = 12.345
    h["precipitation"][0] = 0.049
    h["precipitation_probability"][0] = 62.7
    h["windspeed_10m"][0] = 7.87
    h["weathercode"][0] = 3.6
    payload, _ = bsh.build_payload(pts, prov)
    row = payload["provinces"]["MI"]["h"][0]
    assert row == [12.3, 0.0, 63, 7.9, 4]


def test_missing_day0_is_an_error():
    pts, prov = _fixture(day0=None)
    pts.pop("day0")
    with pytest.raises(bsh.SimpleHourlyError):
        bsh.build_payload(pts, prov)


def test_missing_hourly_field_is_an_error():
    pts, prov = _fixture()
    pts["points"][0]["models"]["best_match"]["hourly"] = _hourly(missing=("precipitation",))
    with pytest.raises(bsh.SimpleHourlyError):
        bsh.build_payload(pts, prov)


def test_unknown_selected_point_is_reported():
    pts, prov = _fixture()
    prov["provinces"][0]["selected_point"] = {"id": 999}
    payload, stats = bsh.build_payload(pts, prov)
    assert "RM" not in payload["provinces"]
    assert "RM" in stats["missing_points"]


def _ext_fixture(points_doc, hours=None, point_ids=None, models=("best_match",)):
    """Estensione sintetica allineata al day0 della fixture (ora 0 = day0+72h)."""
    hours = hours or bsh.build_hours(points_doc["day0"], bsh.HOURS)[bsh.BASE_HOURS:]
    out = {}
    for sigla, pid in (point_ids or {}).items():
        recs = []
        for m in models:
            recs.append({"model": m, "point_id": pid,
                         "h": [[1.0 + i * 0.01, 0.0, 10, 5.0, 1]
                               for i in range(len(hours))]})
        out[sigla] = {"points": recs}
    return {"schema": "meteorisk.hourly-ext/1", "start_hour": bsh.BASE_HOURS,
            "hours": hours, "provinces": out}


def test_extension_merges_168_hours():
    pts, prov = _fixture()
    ext = _ext_fixture(pts, point_ids={"RM": 2, "MI": 1})
    payload, stats = bsh.build_payload(pts, prov, ext)
    assert stats["extended"] is True
    assert len(payload["hours"]) == 168
    assert payload["hours"][71] == "2026-11-25T23:00"
    assert payload["hours"][72] == "2026-11-26T00:00"
    assert len(payload["provinces"]["MI"]["h"]) == 168
    # ora 0 dell'estensione = prima riga estesa (continuita' con l'ora 71)
    assert payload["provinces"]["MI"]["h"][72][0] == 1.0
    assert payload["provinces"]["MI"]["h"][0][0] == 10.0  # dal dataset base


def test_extension_on_another_point_is_not_used():
    pts, prov = _fixture()
    ext = _ext_fixture(pts, point_ids={"RM": 999, "MI": 1})
    payload, stats = bsh.build_payload(pts, prov, ext)
    assert len(payload["provinces"]["RM"]["h"]) == 168
    # nessun valore inventato: la provincia non coperta ha righe null + motivo
    assert payload["provinces"]["RM"]["extended_reason"] == "extended_point_mismatch"
    assert all(v is None for v in payload["provinces"]["RM"]["h"][72])
    assert stats["extended_reasons"] == {"extended_point_mismatch": 1}


def test_extension_misaligned_in_time_is_ignored():
    pts, prov = _fixture()
    ext = _ext_fixture(pts, point_ids={"RM": 2, "MI": 1})
    ext["hours"] = ["2026-12-01T00:00"] * 96        # altro giorno
    payload, stats = bsh.build_payload(pts, prov, ext)
    assert stats["extended"] is False
    assert stats["extended_reason"] == "extended_time_misaligned"
    assert len(payload["hours"]) == 72
    assert len(payload["provinces"]["MI"]["h"]) == 72


def test_no_extension_stays_at_72_hours():
    pts, prov = _fixture()
    payload, stats = bsh.build_payload(pts, prov, None)
    assert stats["extended"] is False
    assert len(payload["hours"]) == 72
    assert all(len(e["h"]) == 72 for e in payload["provinces"].values())


def test_main_without_inputs_exits_zero(tmp_path):
    code = bsh.main(["--points", str(tmp_path / "nope.json"),
                     "--provinces", str(tmp_path / "nope2.json"),
                     "--out", str(tmp_path / "out.json")])
    assert code == 0
    assert not (tmp_path / "out.json").exists()


def test_main_writes_file_and_report(tmp_path, capsys):
    pts, prov = _fixture()
    p1 = tmp_path / "points.json"
    p2 = tmp_path / "provinces.json"
    p1.write_text(json.dumps(pts), encoding="utf-8")
    p2.write_text(json.dumps(prov), encoding="utf-8")
    out = tmp_path / "simple_hourly.json"
    code = bsh.main(["--points", str(p1), "--provinces", str(p2),
                     "--extended", str(tmp_path / "nessuna_estensione.json"),
                     "--out", str(out), "--json-report"])
    assert code == 0
    assert out.exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["schema"] == "simple-hourly/1"
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")][-1]
    report = json.loads(line)
    assert report["ok"] is True
    assert report["raw_bytes"] == out.stat().st_size


# ------------------------------------------------- (B) CONTRATTO REALE

requires_data = pytest.mark.skipif(
    not (os.path.exists(POINTS) and os.path.exists(PROVINCES)),
    reason="dataset live assente: contratto reale non verificabile",
)


@requires_data
def test_real_dataset_contract():
    points = json.loads(io.open(POINTS, encoding="utf-8").read())
    provinces = json.loads(io.open(PROVINCES, encoding="utf-8").read())
    ext = None
    if os.path.exists(EXTENDED):
        ext = json.loads(io.open(EXTENDED, encoding="utf-8").read())
    payload, stats = bsh.build_payload(points, provinces, ext)
    assert len(payload["provinces"]) == 107
    assert payload["generated_at"]
    expected_hours = 168 if stats["extended"] else 72
    assert len(payload["hours"]) == expected_hours
    for sigla, entry in payload["provinces"].items():
        assert isinstance(entry["id"], int)
        assert len(entry["h"]) == expected_hours
        for row in entry["h"]:
            assert len(row) == 5
            assert row[2] is None or isinstance(row[2], int)
            assert row[4] is None or isinstance(row[4], int)
            for v in row:
                assert v is None or isinstance(v, (int, float))
    # I valori possono essere null (dato mancante) ma non "inventati":
    # ogni riga non-null deve coincidere col punto scelto.
    by_id = {p["id"]: p for p in points["points"]}
    for prov in provinces["provinces"]:
        sigla = prov["sigla"]
        pid = prov["selected_point"]["id"]
        src = by_id[pid]["models"]["best_match"]["hourly"]
        for i in (0, 35, 71):
            row = payload["provinces"][sigla]["h"][i]
            assert row[0] == bsh._round(src["temperature_2m"][i], 1)
            assert row[1] == bsh._round(src["precipitation"][i], 1)
            assert row[2] == bsh._round(src["precipitation_probability"][i], 0)
            assert row[3] == bsh._round(src["windspeed_10m"][i], 1)
            assert row[4] == bsh._round(src["weathercode"][i], 0)
        if stats["extended"]:
            # ore 72..167: stesso point_id, valori dall'estensione (non dal base)
            ext_rec = [r for r in ext["provinces"][sigla]["points"]
                       if r["model"] == "best_match"][0]
            assert ext_rec["point_id"] == pid
            for k in (0, 47, 95):
                assert payload["provinces"][sigla]["h"][72 + k] == [
                    bsh._round(v, 0) if idx in (2, 4) else bsh._round(v, 1)
                    for idx, v in enumerate(ext_rec["h"][k])]


@requires_data
def test_real_payload_is_compact():
    points = json.loads(io.open(POINTS, encoding="utf-8").read())
    provinces = json.loads(io.open(PROVINCES, encoding="utf-8").read())
    ext = None
    if os.path.exists(EXTENDED):
        ext = json.loads(io.open(EXTENDED, encoding="utf-8").read())
    text = json.dumps(bsh.build_payload(points, provinces, ext)[0],
                      separators=(",", ":"), ensure_ascii=False)
    raw = len(text.encode("utf-8"))
    gz = len(gzip.compress(text.encode("utf-8"), 9))
    assert raw <= 400 * 1024, "payload raw %d byte > 400 KB" % raw
    assert gz <= 100 * 1024, "payload gzip %d byte > 100 KB" % gz


@pytest.mark.skipif(not os.path.exists(SIMPLE), reason="simple_hourly.json non pubblicato")
def test_published_file_matches_builder():
    published = json.loads(io.open(SIMPLE, encoding="utf-8").read())
    points = json.loads(io.open(POINTS, encoding="utf-8").read())
    provinces = json.loads(io.open(PROVINCES, encoding="utf-8").read())
    ext = None
    if os.path.exists(EXTENDED):
        ext = json.loads(io.open(EXTENDED, encoding="utf-8").read())
    expected, _ = bsh.build_payload(points, provinces, ext)
    assert published["schema"] == expected["schema"]
    assert "days" not in published  # il payload orario non contiene aggregati giornalieri
    assert published["hours"] == expected["hours"]
    assert set(published["provinces"]) == set(expected["provinces"])
