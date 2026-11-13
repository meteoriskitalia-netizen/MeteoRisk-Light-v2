#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Endpoint OPERA: fallback documentato, endpoint della chiave, degrado esplicito.

CONTESTO MISURATO il 2026-11-13:
  * il bucket PRIMARIO `https://s3.waw3-1.cloudferro.com/openradar-24h/`
    risponde HTTP 200 con ZERO chiavi sotto `YYYY/MM/DD/OPERA/COMP/` per i
    giorni 10, 11, 12 e 13 novembre (l'intero bucket contiene 2 oggetti, di
    ottobre);
  * l'endpoint di BACKUP dichiarato da EUMETNET (sezione "ORD Backup" della
    documentazione Open Radar Data) ha 197 `DBZH.tiff` il 13/11 a cadenza
    esatta di 5 minuti.
Quindi: la chiave elencata dal backup deve essere LETTA dal backup (mai da un
altro endpoint), il fallback non deve essere interrogato quando il primario
funziona, e il degrado deve restare non bloccante, esplicito e verificabile.
"""

import datetime as _dt
import json

import numpy as np
import pytest
from affine import Affine

from conftest import DPC_TM_WKT, write_synthetic_tif

from radar_engine import models, mosaic, output, preprocess, source_opera

PRIMARY = "https://primary.example/openradar-24h/"
BACKUP = "https://object-store.os-api.cci1.ecmwf.int/openradar-24h-backup/"
KEY = "2026/11/13/OPERA/COMP/OPERA@20261113T1200@0@DBZH.tiff"
# griglia piccola (60x60 km) con pixel di 1 km: stesso CRS/origine della VMI
SMALL_TRANSFORM = Affine(1000.0, 0.0, -600000.0, 0.0, -1000.0, 650000.0)


def _iso_ms(s):
    return int(_dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def _raster(tmp_path, arr, when_ms=None, name="vmi.tif"):
    path = tmp_path / name
    write_synthetic_tif(str(path), arr, transform=SMALL_TRANSFORM,
                        crs=DPC_TM_WKT)
    raster = preprocess.read_raster(str(path), [-9999.0, -9998.0])
    raster.time_ms = when_ms
    raster.time_iso = (
        _dt.datetime.fromtimestamp(when_ms / 1000.0, _dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ") if when_ms else None)
    return raster


def _bundle(generated_at="2026-11-13T16:05:00Z"):
    bundle = models.EngineBundle("ok", generated_at, "Radar-DPC VMI")
    bundle.radar_timestamp_iso = generated_at
    bundle.radar_timestamp_ms = _iso_ms(generated_at)
    bundle.data_latency_minutes = 3
    return bundle


# ---------------------------------------------------------------------------
# 1. Il fallback e' una LISTA ORDINATA di endpoint, non un singolo base
# ---------------------------------------------------------------------------
def test_resolve_slots_falls_back_to_the_backup_endpoint(monkeypatch):
    """Primario a 0 chiavi -> si usa il backup, e lo slot DICHIARA `base`."""
    def fake_list(day, s3_base=None, **kw):
        return [] if s3_base == PRIMARY else [KEY]

    monkeypatch.setattr(source_opera, "list_day_keys", fake_list)
    t = _iso_ms("2026-11-13T12:00:00Z")
    diag = {}
    slots = source_opera.resolve_slots([t], s3_bases=[PRIMARY, BACKUP],
                                       diag=diag)
    assert slots[t]["key"] == KEY
    assert slots[t]["base"] == BACKUP
    # prova del percorso: il primario e' stato interrogato e ha dato 0 chiavi
    assert diag["attempts"] == [
        {"day": "2026/11/13", "base": PRIMARY, "keys": 0},
        {"day": "2026/11/13", "base": BACKUP, "keys": 1},
    ]
    assert diag["bases"] == [PRIMARY, BACKUP]


def test_resolve_slots_does_not_touch_the_backup_when_primary_has_keys(
        monkeypatch):
    """Nessun costo aggiuntivo quando il primario torna a funzionare."""
    calls = []

    def fake_list(day, s3_base=None, **kw):
        calls.append(s3_base)
        return [KEY]

    monkeypatch.setattr(source_opera, "list_day_keys", fake_list)
    t = _iso_ms("2026-11-13T12:00:00Z")
    slots = source_opera.resolve_slots([t], s3_bases=[PRIMARY, BACKUP])
    assert slots[t]["base"] == PRIMARY
    assert calls == [PRIMARY]


def test_resolve_slots_survives_a_listing_error_on_one_endpoint(monkeypatch):
    """Un 5xx sul primario non ferma il backup (degrado non bloccante)."""
    def fake_list(day, s3_base=None, **kw):
        if s3_base == PRIMARY:
            raise models.SourceError("http_503")
        return [KEY]

    monkeypatch.setattr(source_opera, "list_day_keys", fake_list)
    t = _iso_ms("2026-11-13T12:00:00Z")
    diag = {}
    slots = source_opera.resolve_slots([t], s3_bases=[PRIMARY, BACKUP],
                                       diag=diag)
    assert slots[t]["base"] == BACKUP
    assert diag["attempts"][0]["keys"] is None
    assert "503" in diag["attempts"][0]["error"]


def test_list_day_keys_retries_only_transient_errors(monkeypatch):
    """Retry sui soli errori di rete; un listing VUOTO non si ritenta."""
    state = {"n": 0}

    def flaky(url, timeout_s=60, headers=None):
        state["n"] += 1
        if state["n"] == 1:
            raise models.SourceError("network:timeout")
        return (200, b"<ListBucketResult><KeyCount>0</KeyCount>"
                     b"<IsTruncated>false</IsTruncated></ListBucketResult>", {})

    monkeypatch.setattr(source_opera, "_http_get", flaky)
    assert source_opera.list_day_keys("2026/11/13", attempts=2,
                                      retry_wait_s=0) == []
    assert state["n"] == 2

    state["n"] = 0

    def empty(url, timeout_s=60, headers=None):
        state["n"] += 1
        return (200, b"<ListBucketResult><KeyCount>0</KeyCount>"
                     b"<IsTruncated>false</IsTruncated></ListBucketResult>", {})

    monkeypatch.setattr(source_opera, "_http_get", empty)
    assert source_opera.list_day_keys("2026/11/13", attempts=3,
                                      retry_wait_s=0) == []
    assert state["n"] == 1


# ---------------------------------------------------------------------------
# 2. La chiave si legge dallo STESSO endpoint che l'ha elencata
# ---------------------------------------------------------------------------
def test_apply_reads_the_key_from_the_endpoint_that_listed_it(
        tmp_path, monkeypatch):
    t_ms = _iso_ms("2026-11-13T12:00:00Z")
    raster = _raster(tmp_path, np.full((60, 60), -9999.0, dtype="float32"),
                     when_ms=t_ms)
    seen = {}

    def fake_resolve(targets, **kw):
        return {t_ms: {"key": KEY, "time_ms": t_ms, "delta_s": 0.0,
                       "base": BACKUP}}

    def fake_read(url, *a, **k):
        seen["url"] = url
        return source_opera.OperaFrame(
            data=np.full((60, 60), 20.0, dtype="float32"),
            transform=SMALL_TRANSFORM, crs=DPC_TM_WKT, rows=60, cols=60,
            time_ms=t_ms, time_iso="2026-11-13T12:00:00Z", meta={})

    monkeypatch.setattr(source_opera, "resolve_slots", fake_resolve)
    monkeypatch.setattr(source_opera, "read_frame", fake_read)
    cfg = {"opera": {"enabled": True, "max_age_min": 20, "seam_width_px": 2,
                     "s3_base": PRIMARY, "s3_fallback_bases": [BACKUP]}}
    res = mosaic.apply([raster], cfg, now_ms=t_ms)
    # il bug da non reintrodurre: l'URL costruito dal `s3_base` di config
    assert seen["url"] == BACKUP + KEY
    assert res.status == "ok"
    assert res.meta["endpoint"] == BACKUP
    assert res.meta["endpoints_tried"] == [PRIMARY, BACKUP]


# ---------------------------------------------------------------------------
# 3. Degrado ESPLICITO: last_ok_utc, copertura persa, nota per il pannello
# ---------------------------------------------------------------------------
PREV_OK = {
    "generated_at": "2026-11-11T23:55:34Z",
    "opera": {"status": "ok"},
    "mosaic": {"frames": [{"time_iso": "2026-11-11T23:45:00Z",
                           "pixels_opera": 112463, "pct_opera": 6.6942}]},
}

PREV_DEGRADED = {
    "generated_at": "2026-11-12T08:00:00Z",
    "opera": {"status": "unavailable", "reason": "no_slot_for_frame",
              "last_ok_utc": "2026-11-11T23:55:34Z",
              "coverage_lost_pixels": 112463,
              "coverage_lost_km2": 112463.0},
    "mosaic": {"frames": []},
}


def _degraded_bundle():
    bundle = _bundle()
    bundle.opera_status = {
        "status": "unavailable", "reason": "no_slot_for_frame",
        "endpoints_tried": [PRIMARY, BACKUP], "listing": []}
    bundle.mosaic_summary = {"status": "unavailable", "frames": [],
                             "pixel_area_km2": 1.0}
    return bundle


def test_degraded_opera_carries_last_ok_and_lost_coverage():
    latest = output.build_latest(_degraded_bundle(), previous=PREV_OK)
    op = latest["opera"]
    assert op["last_ok_utc"] == "2026-11-11T23:55:34Z"
    assert op["coverage_lost_pixels"] == 112463
    # pixel di 1 km^2 (misurato dal transform) -> 112463 km^2
    assert op["coverage_lost_km2"] == pytest.approx(112463.0)
    assert op["coverage_lost_basis"] == "last_ok_frame_pixels"
    assert "OPERA unavailable" in op["note"]
    assert "2" in op["note"]          # endpoint provati
    assert "km2" in op["note"]


def test_degraded_opera_is_idempotent_across_runs():
    """Secondo run degradato: i valori si TRASPORTANO, non si ricalcolano."""
    latest = output.build_latest(_degraded_bundle(), previous=PREV_DEGRADED)
    op = latest["opera"]
    assert op["last_ok_utc"] == "2026-11-11T23:55:34Z"
    assert op["coverage_lost_pixels"] == 112463
    assert op["coverage_lost_km2"] == pytest.approx(112463.0)


def test_ok_opera_resets_lost_coverage():
    bundle = _bundle()
    bundle.opera_status = {"status": "ok", "reason": None,
                           "endpoints_tried": [PRIMARY]}
    bundle.mosaic_summary = {"status": "ok", "frames": [{"pixels_opera": 5}],
                             "pixel_area_km2": 1.0}
    latest = output.build_latest(bundle, previous=PREV_DEGRADED)
    op = latest["opera"]
    assert op["last_ok_utc"] == bundle.generated_at_iso
    assert op["coverage_lost_pixels"] == 0
    assert op["coverage_lost_km2"] == 0.0
    assert op["coverage_lost_basis"] == "none"
    assert "note" not in op


def test_write_outputs_transports_last_ok_across_two_runs(tmp_path):
    """Il percorso VERO: il secondo run legge il latest.json del primo."""
    out = str(tmp_path / "radar")
    first = _bundle("2026-11-11T23:55:34Z")
    first.opera_status = {"status": "ok", "reason": None}
    first.mosaic_summary = {"status": "ok", "pixel_area_km2": 1.0,
                            "frames": [{"time_iso": "2026-11-11T23:45:00Z",
                                        "pixels_opera": 112463}]}
    output.write_outputs(first, out)
    second = _degraded_bundle()
    output.write_outputs(second, out)
    with open(tmp_path / "radar" / "latest.json", encoding="utf-8") as fh:
        latest = json.load(fh)
    op = latest["opera"]
    assert op["status"] == "unavailable"
    assert op["last_ok_utc"] == "2026-11-11T23:55:34Z"
    assert op["coverage_lost_pixels"] == 112463
    assert op["coverage_lost_km2"] == pytest.approx(112463.0)
    assert op["note"]
