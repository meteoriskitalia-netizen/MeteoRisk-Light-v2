# -*- coding: utf-8 -*-
"""Contratto di data/latest/weekly.json e del builder `scripts/build_weekly.py`.

Nessuna rete: le risposte Open-Meteo sono sintetiche e deterministiche. Il test
controlla le cose che possono rompersi in silenzio:
  - schema e forma compatta [sigla, [7 record]];
  - 7 giorni e 107 province;
  - campi nulli OMESSI (mai 0 al posto di un dato mancante);
  - campo sparito dall'elenco `fields` se nullo su tutte le province;
  - budget gzip (120 KB), LF, nessun CR, nessun BOM;
  - GUARDIA: nessun campo del dataset orario meteorisk-points.json;
  - al massimo 3 richieste per le 107 province;
  - retry sui 429 e nessuna scrittura del file se il fetch fallisce.
"""
import datetime as _dt
import gzip
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import common  # noqa: E402
import build_weekly  # noqa: E402

BUDGET_GZIP = 120 * 1024
N_PROVINCE = 107
N_DAYS = 7


def _regions():
    regions = common._load_regions()
    assert len(regions) == N_PROVINCE, "regions_data.json: %d province" % len(regions)
    return regions


def _value(sigla, param, day):
    """Valore sintetico deterministico, con i tre casi di controllo."""
    base = sum(ord(c) for c in sigla) % 17
    if param == "weathercode":
        return (base + day) % 4 * 20 + 1
    if param == "precipitation_probability_max":
        return (base * 3 + day * 7) % 101
    if param == "temperature_2m_max":
        return round(10.0 + base * 0.3 + day * 0.4, 1)
    if param == "temperature_2m_min":
        return round(2.0 + base * 0.2 + day * 0.3, 1)
    if param == "precipitation_sum":
        return round(((base + day) % 9) * 0.25, 2)
    if param == "windspeed_10m_max":
        return round(4.0 + (base % 11) + day * 0.5, 1)
    if param == "windgusts_10m_max":
        return round(8.0 + (base % 13) + day * 0.7, 1)
    if param == "et0_fao_evapotranspiration":
        return round(0.5 + (base % 5) * 0.2 + day * 0.05, 2)
    raise AssertionError(param)


def _responses(regions, et0=True, gust_null=None, et0_null_sigle=()):
    """Risposte finte: una per provincia, forma reale dell'API (lista)."""
    day0 = _dt.date(2026, 11, 10)
    times = [(day0 + _dt.timedelta(days=i)).isoformat() for i in range(N_DAYS)]
    out = []
    for region in regions:
        sigla = region["sigla"]
        daily = {"time": list(times)}
        for _key, param, _dec in build_weekly.DAILY_VARS:
            if param == "et0_fao_evapotranspiration" and not et0:
                continue
            values = []
            for day in range(N_DAYS):
                value = _value(sigla, param, day)
                if param == "et0_fao_evapotranspiration" and sigla in et0_null_sigle:
                    value = None
                if param == "windgusts_10m_max" and gust_null == (sigla, day):
                    value = None
                values.append(value)
            daily[param] = values
        out.append({"latitude": region["lat"], "longitude": region["lon"],
                    "daily_units": {}, "daily": daily})
    return out


def _payload(**kwargs):
    regions = _regions()
    responses = _responses(regions, **kwargs)
    return regions, build_weekly.build_payload(regions, responses)


def test_schema_and_compact_shape():
    regions, payload = _payload()
    assert payload["version"] == "meteorisk.weekly/1"
    assert _dt.datetime.fromisoformat(payload["generated_at"]).tzinfo is not None
    assert payload["source"] == "best_match"
    assert payload["provider"] == "Open-Meteo"
    assert "Open-Meteo" in payload["attribution"] and "CC BY 4.0" in payload["attribution"]
    assert payload["timezone"] == "Europe/Rome"
    assert payload["forecast_days"] == N_DAYS
    assert payload["province_fields"] == ["sigla", "days"]
    assert payload["count"] == N_PROVINCE
    assert len(payload["days"]) == N_DAYS
    for i, day in enumerate(payload["days"]):
        assert _dt.date.fromisoformat(day) == _dt.date(2026, 11, 10) + _dt.timedelta(days=i)
    assert len(payload["provinces"]) == N_PROVINCE
    assert [p[0] for p in payload["provinces"]] == [r["sigla"] for r in regions]
    for sigla, records in payload["provinces"]:
        assert isinstance(sigla, str) and sigla
        assert len(records) == N_DAYS
        assert all(isinstance(rec, dict) for rec in records)
    assert set(payload["fields"]) == {k for k, _p, _d in build_weekly.DAILY_VARS}
    for meta in payload["fields"].values():
        assert set(meta) == {"label", "unit", "decimals"}
        assert isinstance(meta["decimals"], int) and 0 <= meta["decimals"] <= 2


def test_null_fields_are_omitted_not_zero():
    regions, payload = _payload(gust_null=("MI", 2), et0_null_sigle=("PA",))
    by_sigla = {sigla: records for sigla, records in payload["provinces"]}
    assert "gust" not in by_sigla["MI"][2], "campo nullo scritto nel record"
    assert "gust" in by_sigla["MI"][0] and "gust" in by_sigla["MI"][1]
    for rec in by_sigla["PA"]:
        assert "et0" not in rec, "ET0 nullo su PA scritto nel record"
    # et0 resta dichiarato: altre province hanno il valore.
    assert "et0" in payload["fields"]
    assert "et0" in by_sigla["RM"][0]
    # nessun None nel file: i campi mancanti sono omessi, non null.
    for _sigla, records in payload["provinces"]:
        for rec in records:
            assert None not in rec.values()


def test_field_with_no_values_is_dropped_from_fields():
    _regions_, payload = _payload(et0=False)
    assert "et0" not in payload["fields"]
    for _sigla, records in payload["provinces"]:
        for rec in records:
            assert "et0" not in rec
    assert "tmax" in payload["fields"]


def test_gzip_budget_and_encoding():
    _regions_, payload = _payload()
    data = build_weekly.serialize(payload)
    assert isinstance(data, bytes)
    assert not data.startswith(b"\xef\xbb\xbf"), "BOM presente"
    assert b"\r" not in data, "CR presente (richiesto LF)"
    assert data.endswith(b"\n")
    data.decode("utf-8")
    gz = gzip.compress(data, 9)
    assert len(gz) <= BUDGET_GZIP, "gzip %d B > budget %d B" % (len(gz), BUDGET_GZIP)
    assert build_weekly.gzip_size(data) == len(gz)
    # budget verificato anche dalla validazione del contratto
    info = build_weekly.validate_payload(payload, data=data)
    assert info["gzip_bytes"] == len(gz)
    assert info["provinces"] == N_PROVINCE


def test_no_hourly_fields_of_points_dataset():
    """GUARDIA incidente: weekly.json non deve contenere i 49 campi orari."""
    assert len(common.HOURLY_FIELDS) == 49
    forbidden = set(common.HOURLY_FIELDS)
    _regions_, payload = _payload()
    keys = build_weekly._all_keys(payload, set())
    overlap = keys & forbidden
    assert not overlap, "campi del dataset orario nel weekly: %s" % sorted(overlap)
    assert "hourly" not in keys and "models" not in keys and "points" not in keys
    text = build_weekly.serialize(payload).lower()
    for name in ("hourly", "meteorisk-points", "convective_inhibition",
                 "lifted_index", "wind_speed_850hpa"):
        assert name.encode() not in text, "riferimento vietato nel file: %s" % name
    assert os.path.basename(str(common.DATA_LATEST / "weekly.json")) == "weekly.json"
    assert "meteorisk-points" not in str(common.DATA_LATEST / "weekly.json")


def test_at_most_three_batches_for_107_provinces():
    regions = _regions()
    batches = build_weekly.plan_batches(regions, 100)
    assert len(batches) == 2
    assert [len(b) for b in batches] == [100, 7]
    batches50 = build_weekly.plan_batches(regions, 50)
    assert len(batches50) == 3
    assert [len(b) for b in batches50] == [50, 50, 7]
    assert [r for b in batches50 for r in b] == regions
    assert build_weekly.MAX_REQUESTS == 3
    with pytest.raises(ValueError):
        build_weekly.plan_batches(regions, 30)
    with pytest.raises(ValueError):
        build_weekly.plan_batches(regions, 101)


def _http_error(code, body):
    import email.message
    import io
    import urllib.error
    headers = email.message.Message()
    return urllib.error.HTTPError("https://api.open-meteo.com/v1/forecast", code,
                                  "error", headers,
                                  io.BytesIO(body.encode("utf-8")))


def test_retry_on_429_and_exhaustion():
    batch = _regions()[:2]
    calls = {"n": 0}
    sleeps = []

    def fake_http(url, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(429, '{"reason":"Minutely API request limit exceeded"}')
        return [{"daily": {"time": []}}]

    data, meta = build_weekly.fetch_batch(batch, attempts=3, http=fake_http,
                                          sleep=sleeps.append)
    assert calls["n"] == 3
    assert meta["requests"] == 3
    assert meta["http_429"] == 2
    assert sleeps == [20.0, 40.0]
    assert isinstance(data, list)

    def always_429(url, timeout=None):
        raise _http_error(429, '{"reason":"limit"}')

    with pytest.raises(build_weekly.WeeklyError):
        build_weekly.fetch_batch(batch, attempts=2, http=always_429, sleep=lambda s: None)


def test_failure_does_not_touch_existing_file(tmp_path, monkeypatch):
    out = tmp_path / "weekly.json"
    out.write_text("SENTINEL", encoding="utf-8")

    def boom(*_a, **_k):
        raise build_weekly.WeeklyError("Open-Meteo 429")

    monkeypatch.setattr(build_weekly, "fetch_all", boom)
    rc = build_weekly.main(["--out", str(out)])
    assert rc == 2
    assert out.read_text(encoding="utf-8") == "SENTINEL"


def test_main_writes_only_weekly_file(tmp_path, monkeypatch):
    regions = _regions()
    responses = _responses(regions)
    stats = {"requests": 2, "http_429": 0, "batches": 2, "et0": True}
    monkeypatch.setattr(build_weekly, "fetch_all",
                        lambda *a, **k: (responses, dict(stats)))
    out = tmp_path / "weekly.json"
    rc = build_weekly.main(["--out", str(out)])
    assert rc == 0
    assert sorted(os.listdir(str(tmp_path))) == ["weekly.json"]
    raw = out.read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"\n")
    doc = json.loads(raw.decode("utf-8"))
    info = build_weekly.validate_payload(doc, data=raw, expected_provinces=N_PROVINCE)
    assert info["raw_bytes"] == len(raw)
    assert doc["requests"] == {"total": 2, "http_429": 0}


def test_committed_weekly_file_if_present():
    path = os.path.join(ROOT, "data", "latest", "weekly.json")
    if not os.path.exists(path):
        pytest.skip("data/latest/weekly.json non ancora generato")
    with open(path, "rb") as fh:
        raw = fh.read()
    doc = json.loads(raw.decode("utf-8"))
    info = build_weekly.validate_payload(doc, data=raw, expected_provinces=N_PROVINCE)
    assert info["provinces"] == N_PROVINCE
    assert len(doc["days"]) == N_DAYS
    sigle = {r["sigla"] for r in _regions()}
    assert {p[0] for p in doc["provinces"]} == sigle
    for i, day in enumerate(doc["days"]):
        assert _dt.date.fromisoformat(day) == _dt.date.fromisoformat(doc["days"][0]) + _dt.timedelta(days=i)
    # plausibilita' fisica minima (intercetta campi scalati male/unita' sbagliate)
    for sigla, records in doc["provinces"]:
        for rec in records:
            if "tmax" in rec and "tmin" in rec:
                assert rec["tmax"] >= rec["tmin"], sigla
            if "pprob" in rec:
                assert 0 <= rec["pprob"] <= 100, (sigla, rec["pprob"])
            for key in ("prcp", "wind", "gust", "et0"):
                if key in rec:
                    assert rec[key] >= 0, (sigla, key, rec[key])
            if "code" in rec:
                assert 0 <= rec["code"] <= 99, (sigla, rec["code"])
