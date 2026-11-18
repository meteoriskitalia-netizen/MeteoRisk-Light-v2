# -*- coding: utf-8 -*-
"""Test del produttore AO CDAS (conCSV reale ridotto, nessuna rete)."""
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "scripts" / "climate" / "build_ao_cdas.py"
spec = importlib.util.spec_from_file_location("build_ao_cdas", MOD)
ao = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ao)

CSV = """year,month,day,ao_index_cdas
2026,11,14,-0.5
2026,11,15,0.25
2026,11,16,-99.9
2026,11,17,1.5
2026,11,18,0.9
2026,11,19,4.0
"""


def test_parse_csv_up_to_yesterday():
    # "oggi" = 2026-11-18 -> ieri = 17/11. La riga del 18 e' un sentinel
    # (-9.999) e comunque fuori finestra; il 19 e' futuro.
    pts, meta = ao.parse_ao_csv(CSV, date(2026, 11, 18))
    assert len(pts) == 3, pts
    assert [p[1] for p in pts] == [-0.5, 0.25, 1.5], pts
    assert meta["last_observation_date"] == "2026-11-17"
    assert meta["cutoff_yesterday_utc"] == "2026-11-17"
    assert meta["stats"]["dropped_future"] == 2
    assert meta["stats"]["dropped_bad"] == 1


def test_payload_is_honest_and_licensed():
    pts, meta = ao.parse_ao_csv(CSV, date(2026, 11, 18))
    p = ao.build_payload(pts, meta, "2026-11-18T00:00:00Z")
    assert p["status"] == "ok"
    assert p["baseline"] == "1979-2000"
    assert p["unit"] == "standardized index"
    assert "public domain" in p["license"].lower()
    assert "attribution" in p["license"].lower()
    assert p["attribution"]
    assert p["is_forecast"] is False
    assert p["not_named_nam"] is True
    assert "NAM" not in p["display_name"].upper().replace("OSSERVATO", "")
    prof = p["profile_10hpa"]
    assert prof["value"] is None and prof["values"] is None
    assert prof["null_reason"] == "psl_r1_dailyavgs_stale_since_2026-03-17"
    assert "17/03/2026" in prof["label"]
    assert "rianalisi, non corrente" in prof["label"]


def test_failed_payload_has_no_invented_value():
    p = ao.build_payload([], {"last_observation_date": None,
                              "cutoff_yesterday_utc": "2026-11-17",
                              "stats": {}},
                         "2026-11-18T00:00:00Z", status="producer_failed",
                         null_reason="cpc_cdas_ao_unavailable",
                         reason="test")
    assert p["series"] == []
    assert p["null_reason"] == "cpc_cdas_ao_unavailable"
    assert p["last_observation_date"] is None
    assert p["profile_10hpa"]["value"] is None


def test_cache_is_reused_without_network(tmp_path):
    cache = tmp_path / "ao.csv"
    cache.write_text(CSV, encoding="utf-8")
    text, from_cache = ao.fetch_csv("http://127.0.0.1:1/mai", 1.0, str(cache))
    assert from_cache is True
    assert text.startswith("year,month,day")


def test_generated_json_if_present():
    path = ROOT / "data" / "climate" / "ao_cdas.json"
    if not path.exists():
        return  # non generato in questa copia: non e' un fallimento
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["schema_version"] == 1
    assert d["baseline"] == "1979-2000"
    assert d["status"] in ("ok", "no_observation", "producer_failed")
    assert isinstance(d["series"], list)
    if d["status"] == "ok":
        assert len(d["series"]) > 1000
        assert d["last_observation_date"] is not None
        assert all(len(p) == 2 for p in d["series"])
    assert d["profile_10hpa"]["value"] is None
    assert d["profile_10hpa"]["null_reason"]
