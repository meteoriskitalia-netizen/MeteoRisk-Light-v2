#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A6/A7 — regression dei fix di isolamento path e volume non sovrascritto.

A6: l'harness manuale `doppler_coverage.py` scrive su una path DEDICATA e
    pubblica un marcatore di schema, così non può cancellare il GeoJSON
    operativo con `coverage_status`/`stale` prodotto da doppler.attach().
A7: `write_volume_payload()` non sostituisce un volume valido con un payload
    vuoto; conserva il precedente con `stale: true` e la data dell'ultimo
    volume valido.
"""
import json
import os

from radar_engine import doppler_coverage as dc
from radar_engine import models, output
from radar_engine import volume_publisher as vp

TS = "2026-11-16T22:00:00Z"


def _valid_payload(ts=TS):
    return {
        "schema": vp.SCHEMA,
        "generated_at_utc": ts,
        "volume_available": True,
        "timestamp": ts,
        "volume_timestamp_utc": ts,
        "cells": [{"cell_id": "c1", "by_threshold": {}}],
    }


def _empty_payload(reason="no_cappi_in_this_run"):
    return {
        "schema": vp.SCHEMA,
        "generated_at_utc": "2026-11-16T22:10:00Z",
        "volume_available": False,
        "null_reason": reason,
        "timestamp": "2026-11-16T22:10:00Z",
        "cells": [],
    }


# ---------------------------------------------------------------------------
# A7 — guard di write_volume_payload
# ---------------------------------------------------------------------------
def test_a7_empty_payload_does_not_erase_valid_volume(tmp_path):
    path = vp.write_volume_payload(_valid_payload(), str(tmp_path))
    first = json.loads(open(path, encoding="utf-8").read())
    assert first["volume_available"] is True

    vp.write_volume_payload(_empty_payload("cappi_levels_lt_4"), str(tmp_path))
    back = json.loads(open(path, encoding="utf-8").read())
    assert back["volume_available"] is True, "volume valido cancellato"
    assert back["cells"] == first["cells"], "celle reali perse"
    assert back["stale"] is True
    assert back["last_valid_scan"] == TS
    assert isinstance(back["stale_age_seconds"], int)
    assert back["stale_age_seconds"] >= 0
    assert back["stale_reason"] == "cappi_levels_lt_4"
    assert back["generated_at_utc"] == TS, "data dell'ultimo valido alterata"


def test_a7_empty_payload_still_written_when_no_previous_valid(tmp_path):
    path = vp.write_volume_payload(_empty_payload(), str(tmp_path))
    back = json.loads(open(path, encoding="utf-8").read())
    assert back["volume_available"] is False
    assert back["null_reason"] == "no_cappi_in_this_run"
    assert back.get("stale") is not True


def test_a7_fresh_valid_volume_replaces_stale(tmp_path):
    vp.write_volume_payload(_valid_payload(), str(tmp_path))
    vp.write_volume_payload(_empty_payload(), str(tmp_path))
    stale = json.loads(open(os.path.join(str(tmp_path), "volume.json"),
                            encoding="utf-8").read())
    assert stale["stale"] is True
    fresh_ts = "2026-11-16T22:20:00Z"
    vp.write_volume_payload(_valid_payload(fresh_ts), str(tmp_path))
    back = json.loads(open(os.path.join(str(tmp_path), "volume.json"),
                           encoding="utf-8").read())
    assert back["volume_available"] is True
    assert back["generated_at_utc"] == fresh_ts
    assert "stale" not in back, "marcatore stale non rimosso dopo volume fresco"


def test_a7_atomic_no_tmp_leftover(tmp_path):
    vp.write_volume_payload(_valid_payload(), str(tmp_path))
    vp.write_volume_payload(_empty_payload(), str(tmp_path))
    leftovers = [p for p in os.listdir(str(tmp_path)) if p.endswith(".tmp")]
    assert leftovers == []


def test_a7_latest_volume_path_is_relative_not_runner_absolute():
    """latest.json:volume.path non deve mai esporre il path assoluto del runner."""
    b = models.EngineBundle("ok", "2026-11-16T22:00:00Z", output.SOURCE_LABEL)
    b.volume = {"volume_available": True, "status": "ok", "cells": [1]}
    b.volume_path = ("/home/runner/work/MeteoRisk-Light-v2/MeteoRisk-Light-v2/"
                     "data/radar/volume.json")
    vol = output.build_latest(b)["volume"]
    assert vol["path"] == "data/radar/volume.json"
    assert "runner" not in vol["path"]
    assert os.path.isabs(vol["path"]) is False

    b.volume_path = None
    assert output.build_latest(b)["volume"]["path"] is None


# ---------------------------------------------------------------------------
# A6 — isolamento della path dell'harness manuale
# ---------------------------------------------------------------------------
def test_a6_harness_default_path_is_dedicated(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        return {"counts": {"polygons": 0}, "features": [],
                "schema_version": 2, "channel": "manual_harness"}

    def fake_write(path, doc):
        captured["path"] = path
        captured["doc"] = doc
        return path

    monkeypatch.setattr(dc, "build_geojson", fake_build)
    monkeypatch.setattr(dc, "write_geojson", fake_write)
    assert dc.main([]) == 0
    assert captured["path"].endswith("data/radar/doppler_coverage_harness.geojson"), captured["path"]
    assert "doppler_coverage.geojson" != os.path.basename(captured["path"])
    assert captured["doc"]["schema_version"] == 2
    assert captured["doc"]["channel"] == "manual_harness"


def test_a6_explicit_path_still_honoured(monkeypatch, tmp_path):
    captured = {}

    def fake_build(**kwargs):
        return {"counts": {}, "features": [], "schema_version": 2,
                "channel": "manual_harness"}

    def fake_write(path, doc):
        captured["path"] = path
        return path

    monkeypatch.setattr(dc, "build_geojson", fake_build)
    monkeypatch.setattr(dc, "write_geojson", fake_write)
    want = str(tmp_path / "custom.geojson")
    dc.main(["--out", want])
    assert captured["path"] == want
