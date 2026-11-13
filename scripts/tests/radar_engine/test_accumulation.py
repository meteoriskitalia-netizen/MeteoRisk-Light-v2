#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test del layer ACCUMULO (SRT1) — nessuna rete, nessun dato DPC reale.

Coprono i difetti che il codice deve prevenire:
  1. file mancante -> errore dichiarato (non un crash generico);
  2. sentinella -9999 esclusa, 0.0 valido, negativi anomali contati;
  3. normalizzazione del periodo della stazione a 1 h;
  4. arrotondamento dello slot / finestra 1 h e replay senza orologio;
  5. statistiche per macro-area (p10/p50/p90/max) su maschere rasterizzate;
  6. budget di dimensione dell'output (< 200 KB);
  7. metriche di triangolazione (bias/MAE) e file stazioni mancante;
  8. nessuna parola di allerta nei payload.
"""

import json
import os

import numpy as np
import pytest
import rasterio
from affine import Affine

from radar_engine import accumulation as acc
from radar_engine import accumulation_validation as val


def _write_tif(path, arr, crs="EPSG:4326", transform=None, dtype="float32",
               nodata=None):
    arr = np.asarray(arr, dtype=dtype)
    h, w = arr.shape
    kwargs = dict(driver="GTiff", height=h, width=w, count=1, dtype=dtype,
                  transform=transform or Affine(0.01, 0, 10.0, 0, -0.01, 46.0))
    if crs is not None:
        kwargs["crs"] = crs
    if nodata is not None:
        kwargs["nodata"] = nodata
    with rasterio.open(path, "w", **kwargs) as dst:
        dst.write(arr, 1)
    return str(path)


def _write_geography(tmp_path):
    # Due province: TO (Piemonte -> Nord) a sinistra, PA (Sicilia) a destra.
    geo = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"SIGLA": "TO"},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [9.99, 46.01], [10.02, 46.01], [10.02, 45.95],
                 [9.99, 45.95], [9.99, 46.01]]]}},
            {"type": "Feature", "properties": {"SIGLA": "PA"},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [10.02, 46.01], [10.05, 46.01], [10.05, 45.95],
                 [10.02, 45.95], [10.02, 46.01]]]}},
        ],
    }
    regions = [
        {"sigla": "TO", "region": "Piemonte"},
        {"sigla": "PA", "region": "Sicilia"},
    ]
    geo_path = tmp_path / "province.geojson"
    reg_path = tmp_path / "regions.json"
    geo_path.write_text(json.dumps(geo), encoding="utf-8")
    reg_path.write_text(json.dumps(regions), encoding="utf-8")
    return str(geo_path), str(reg_path)


def test_read_accumulation_missing_file_raises(tmp_path):
    with pytest.raises(acc.AccumulationError) as exc:
        acc.read_accumulation(str(tmp_path / "assente.tif"))
    assert "file_missing" in str(exc.value)


def test_read_accumulation_rejects_integer_dtype(tmp_path):
    path = _write_tif(tmp_path / "int.tif", np.zeros((2, 2)), dtype="int16")
    with pytest.raises(acc.AccumulationError) as exc:
        acc.read_accumulation(path)
    assert "unsupported_raster" in str(exc.value)


def test_sentinel_excluded_zero_valid_negative_counted(tmp_path):
    arr = np.array([
        [-9999.0, 0.0, 2.5],
        [1.0, -1.0, np.nan],
    ], dtype="float32")
    path = _write_tif(tmp_path / "acc.tif", arr)
    raster = acc.read_accumulation(path)
    assert raster.sentinel_px == 1
    assert raster.valid_px == 3                      # 0.0, 2.5 e 1.0
    assert raster.unexpected_negative_px == 1        # -1.0 anomalo
    assert raster.nonfinite_px == 1
    assert set(np.round(raster.valid_values, 3).tolist()) == {0.0, 1.0, 2.5}


def test_normalize_period_to_one_hour():
    assert val.normalize_period_mm(1.5, 900) == pytest.approx(6.0)
    assert val.normalize_period_mm(2.0, 3600) == pytest.approx(2.0)
    assert val.normalize_period_mm(0.0, 60) == pytest.approx(0.0)
    assert val.normalize_period_mm(None, 60) is None
    with pytest.raises(val.ValidationError):
        val.normalize_period_mm(1.0, 0)


def test_slot_flooring_and_window_are_measured_constants():
    ms = acc.parse_iso_utc("2026-11-13T19:13:20Z")
    assert acc.floor_to_step(ms, 300) == acc.parse_iso_utc("2026-11-13T19:10:00Z")
    assert acc.WINDOW_H == 1.0
    assert acc.WINDOW_S == 3600
    assert acc.PRODUCT_TYPE == "SRT1"


def test_replay_slots_do_not_query_latest_product(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("in replay findLastProductByType non va chiamato")
    monkeypatch.setattr(acc.source_dpc, "get_latest_product", _boom)
    as_of = acc.parse_iso_utc("2026-11-13T19:13:20Z")
    slots, step_s = acc.resolve_slots({}, as_of_ms=as_of, count=2)
    assert step_s == 300
    assert slots == [acc.parse_iso_utc("2026-11-13T19:05:00Z"),
                     acc.parse_iso_utc("2026-11-13T19:10:00Z")]


def test_area_masks_and_percentiles(tmp_path):
    arr = np.array([
        [0.0, 0.0, 0.0, 0.0],
        [1.0, 1.0, 5.0, 5.0],
        [2.0, 2.0, 6.0, 6.0],
        [3.0, 3.0, 7.0, 7.0],
    ], dtype="float32")
    path = _write_tif(tmp_path / "acc.tif", arr)
    raster = acc.read_accumulation(path)
    geo_path, reg_path = _write_geography(tmp_path)
    masks = acc.build_area_masks(raster, geo_path, reg_path)
    nord = acc.area_stats(raster, masks["Nord"])
    sicilia = acc.area_stats(raster, masks["Sicilia"])
    assert nord["valid_px"] == 8
    assert nord["p50_mm"] == pytest.approx(1.5)
    assert nord["max_mm"] == pytest.approx(3.0)
    assert sicilia["max_mm"] == pytest.approx(7.0)
    # Aree senza pixel sulla griglia sintetica: statistiche null, non crash.
    assert masks["Sardegna"].sum() == 0
    empty = acc.area_stats(raster, masks["Sardegna"])
    assert empty["valid_px"] == 0 and empty["max_mm"] is None


def test_payload_size_budget(tmp_path):
    slots = []
    for i in range(12):
        slots.append({
            "valid_time": "2026-11-13T19:%02d:00Z" % (i * 5 % 60),
            "valid_time_ms": 1794597000000 + i * 300000,
            "window_h": 1.0,
            "source": acc.SOURCE_LABEL,
            "licence": acc.LICENCE,
            "areas": {name: {"area_px": 1000, "valid_px": 900, "wet_px": 10,
                             "p10_mm": 0.0, "p50_mm": 0.1, "p90_mm": 2.0,
                             "max_mm": 9.999, "mean_mm": 0.1234}
                      for name in acc.AREA_NAMES},
            "totals": {"valid_px": 5400, "total_px": 6000, "sentinel_px": 600,
                       "nonfinite_px": 0, "unexpected_negative_px": 0,
                       "wet_px": 60, "max_mm": 9.999},
        })
    payload = acc.build_payload(slots, generated_at="2026-11-13T19:20:00Z")
    out = tmp_path / "accumulation.json"
    size = acc._atomic_write_json(str(out), payload)
    assert size < acc.MAX_OUTPUT_BYTES
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 1
    with pytest.raises(acc.AccumulationError):
        acc._atomic_write_json(str(tmp_path / "too_big.json"), payload,
                               budget_bytes=100)


def test_validation_metrics_bias_and_mae(tmp_path):
    arr = np.zeros((10, 10), dtype="float32")
    arr[0, 0] = 3.0
    arr[1, 1] = 1.0
    path = _write_tif(tmp_path / "acc.tif", arr)
    raster = acc.read_accumulation(path)
    stations = {"generated_at": "2026-11-13T19:20:00Z", "stations": [
        {"id": "a", "lat": 45.995, "lon": 10.005, "network": "x",
         "obs_time": "2026-11-13T19:10:00Z", "precipitation": 2.0,
         "precipitation_period_s": 3600},
        {"id": "b", "lat": 45.985, "lon": 10.015, "network": "x",
         "obs_time": "2026-11-13T19:10:00Z", "precipitation": 2.0,
         "precipitation_period_s": 3600},
    ]}
    payload = val.build_validation_payload(
        stations, raster,
        valid_time_ms=acc.parse_iso_utc("2026-11-13T19:10:00Z"))
    m = payload["metrics"]
    assert m["n"] == 2
    assert m["bias_mm"] == pytest.approx(0.0)
    assert m["mae_mm"] == pytest.approx(1.0)
    assert payload["exact_window_1h"]["n"] == 2
    assert payload["stations"]["n_pairs"] == 2


def test_validation_missing_file_raises(tmp_path):
    with pytest.raises(val.ValidationError) as exc:
        val.load_stations(str(tmp_path / "nope.json"))
    assert "stations_file_missing" in str(exc.value)


def test_run_writes_unavailable_payload_on_source_failure(tmp_path, monkeypatch):
    def _fail(*args, **kwargs):
        raise acc.models.SourceError("network:test")
    monkeypatch.setattr(acc.source_dpc, "get_latest_product", _fail)
    payload, raster = acc.run(
        str(tmp_path), config={"source": {"cache_dir": str(tmp_path / "cache")}})
    assert payload["status"] == "unavailable"
    assert raster is None
    written = json.loads((tmp_path / "accumulation.json").read_text(
        encoding="utf-8"))
    assert written["status"] == "unavailable"
    assert written["slots"] == []


def test_payload_has_no_alert_words(tmp_path):
    arr = np.zeros((10, 10), dtype="float32")
    path = _write_tif(tmp_path / "acc.tif", arr)
    raster = acc.read_accumulation(path)
    geo_path, reg_path = _write_geography(tmp_path)
    masks = acc.build_area_masks(raster, geo_path, reg_path)
    slot = acc.build_slot(raster, acc.parse_iso_utc("2026-11-13T19:10:00Z"),
                          masks)
    payload = acc.build_payload(
        [slot], generated_at="2026-11-13T19:20:00Z",
        limits=acc._default_limits(raster))
    text = json.dumps(payload, ensure_ascii=False).lower()
    for word in ("allagamento", "allerta", "rischio"):
        assert word not in text
