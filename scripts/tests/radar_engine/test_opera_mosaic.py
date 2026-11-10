#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del mosaico multi-sorgente DPC + OPERA CIRRUS (1.2.4.27).

Nessuna rete: si usano raster sintetici sulla griglia DPC documentata
(TM lat0=42, lon0=12.5, 1 km). La sorgente OPERA e' simulata da un array
gia' riproiettato sulla stessa griglia, che e' esattamente cio' che
`source_opera.reproject_to_grid` produce in produzione.
"""

import datetime as _dt

import numpy as np
import pytest
from affine import Affine

from conftest import DPC_TM_WKT, write_synthetic_tif

from radar_engine import detect as detect_mod
from radar_engine import mosaic, preprocess, source_opera

# griglia piccola (60x60 km) per test veloci, stesso CRS/origine della VMI
SMALL_TRANSFORM = Affine(1000.0, 0.0, -600000.0, 0.0, -1000.0, 650000.0)
CFG = {
    "opera": {
        "enabled": True,
        "max_age_min": 20,
        "seam_width_px": 2,
        "bbox_wgs84": [6.0, 35.0, 20.0, 48.0],
        "clamp_dbz": [-32.0, 95.0],
    },
    "detect": {
        "dbz_threshold": 20.0,
        "min_area_km2": 30.0,
        "max_area_km2": 40000.0,
        "morph_radius_px": 0,
        "connectivity_8": True,
    },
}


def _raster(tmp_path, arr, name="vmi.tif", when_ms=None):
    path = tmp_path / name
    write_synthetic_tif(str(path), arr, transform=SMALL_TRANSFORM,
                        crs=DPC_TM_WKT)
    raster = preprocess.read_raster(str(path), [-9999.0, -9998.0])
    raster.time_ms = when_ms
    raster.time_iso = (
        _dt.datetime.fromtimestamp(when_ms / 1000.0, _dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ") if when_ms else None)
    return raster


def _iso_ms(s):
    return int(_dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------------------
# source_opera: parsing, discovery, clamp, riproiezione
# ---------------------------------------------------------------------------
def test_parse_key_time():
    t = source_opera.parse_key_time(
        "2026/11/10/OPERA/COMP/OPERA@20261110T1315@0@DBZH.tiff")
    assert t == _dt.datetime(2026, 11, 10, 13, 15, tzinfo=_dt.timezone.utc)
    with pytest.raises(ValueError):
        source_opera.parse_key_time("non-un-nome-opera.tiff")


def test_discover_latest_picks_most_recent_before_reference(monkeypatch):
    keys = ["d/OPERA@20261110T1200@0@DBZH.tiff",
            "d/OPERA@20261110T1315@0@DBZH.tiff",
            "d/OPERA@20261110T1355@0@DBZH.tiff"]
    monkeypatch.setattr(source_opera, "list_slot_keys",
                        lambda *a, **k: sorted(keys))
    ref = _dt.datetime(2026, 11, 10, 13, 30, tzinfo=_dt.timezone.utc)
    info = source_opera.discover_latest(ref, lookback_days=0)
    assert info["key"].endswith("OPERA@20261110T1315@0@DBZH.tiff")
    assert info["time_iso"] == "2026-11-10T13:15:00Z"


def test_resolve_slots_respects_tolerance(monkeypatch):
    keys = ["d/OPERA@20261110T1200@0@DBZH.tiff",
            "d/OPERA@20261110T1205@0@DBZH.tiff"]
    monkeypatch.setattr(source_opera, "list_slot_keys",
                        lambda *a, **k: sorted(keys))
    t_exact = _iso_ms("2026-11-10T12:05:00Z")
    t_far = _iso_ms("2026-11-10T12:20:00Z")
    slots = source_opera.resolve_slots([t_exact, t_far], slot_tolerance_s=300)
    assert slots[t_exact]["key"].endswith("T1205@0@DBZH.tiff")
    assert t_far not in slots


def test_clean_masks_nodata_and_clamps_physical_range():
    raw = np.array([[-9999000.0, 12.0, 1e6, -500.0]], dtype="float32")
    data, n_clip = source_opera._clean(raw, -9999000.0, [-32.0, 95.0])
    assert np.isnan(data[0, 0])
    assert data[0, 1] == pytest.approx(12.0)
    assert data[0, 2] == pytest.approx(95.0)
    assert data[0, 3] == pytest.approx(-32.0)
    assert n_clip == 2


def test_reproject_identity_on_same_grid(tmp_path):
    arr = np.full((10, 10), -9999.0, dtype="float32")
    arr[3:6, 4:8] = 33.0
    path = tmp_path / "opera.tif"
    write_synthetic_tif(str(path), arr, transform=SMALL_TRANSFORM,
                        crs=DPC_TM_WKT, nodata=-9999.0)
    import rasterio
    with rasterio.open(path) as ds:
        data, _ = source_opera._clean(ds.read(1), ds.nodata, [-32.0, 95.0])
        frame = source_opera.OperaFrame(
            data=data, transform=ds.transform, crs=ds.crs, rows=10, cols=10,
            time_ms=_iso_ms("2026-11-10T12:00:00Z"), time_iso="x", meta={})
    out = source_opera.reproject_to_grid(frame, SMALL_TRANSFORM, DPC_TM_WKT,
                                         (10, 10))
    assert np.isnan(out[0, 0])
    assert out[4, 5] == pytest.approx(33.0)


# ---------------------------------------------------------------------------
# mosaic: priorita', freschezza, seam, provenienza, determinismo
# ---------------------------------------------------------------------------
def test_merge_priority_dpc_wins_where_valid(tmp_path):
    dpc = np.full((60, 60), -9999.0, dtype="float32")
    dpc[10:50, 0:30] = 35.0
    raster = _raster(tmp_path, dpc, when_ms=_iso_ms("2026-11-10T12:00:00Z"))
    opera = np.full((60, 60), 25.0, dtype="float32")
    mf = mosaic.merge_frame(raster, opera, _iso_ms("2026-11-10T12:00:00Z"),
                            CFG, now_ms=_iso_ms("2026-11-10T12:02:00Z"))
    assert (mf.source_id[10:50, :30] == mosaic.SOURCE_DPC).all()
    assert (mf.source_id[10:50, 30:] == mosaic.SOURCE_OPERA).all()
    assert (mf.source_id[:10, :] == mosaic.SOURCE_OPERA).all()
    assert mf.stats["pixels_dpc"] == 40 * 30
    assert mf.stats["pixels_opera"] == 60 * 60 - 40 * 30
    # nessun blend: i valori DPC restano 35, quelli OPERA 25
    assert np.nanmax(mf.raster.data[:, :30]) == pytest.approx(35.0)
    assert np.nanmax(mf.raster.data[:, 30:]) == pytest.approx(25.0)


def test_merge_substitutes_stale_dpc_with_fresh_opera(tmp_path):
    dpc = np.full((60, 60), -9999.0, dtype="float32")
    dpc[10:50, :] = 35.0
    raster = _raster(tmp_path, dpc, when_ms=_iso_ms("2026-11-10T12:00:00Z"))
    opera = np.full((60, 60), 22.0, dtype="float32")
    now = _iso_ms("2026-11-10T12:45:00Z")   # DPC vecchio di 45 min
    mf = mosaic.merge_frame(raster, opera, _iso_ms("2026-11-10T12:40:00Z"),
                            CFG, now_ms=now)
    assert mf.stats["substituted"] is True
    assert mf.stats["substitute_reason"] == "dpc_stale_opera_fresh"
    assert (mf.source_id[10:50, :] == mosaic.SOURCE_OPERA).all()
    assert np.nanmax(mf.raster.data) == pytest.approx(22.0)


def test_merge_ignores_stale_opera(tmp_path):
    dpc = np.full((60, 60), -9999.0, dtype="float32")
    dpc[10:50, :] = 35.0
    raster = _raster(tmp_path, dpc, when_ms=_iso_ms("2026-11-10T12:40:00Z"))
    opera = np.full((60, 60), 22.0, dtype="float32")
    now = _iso_ms("2026-11-10T12:45:00Z")
    mf = mosaic.merge_frame(raster, opera, _iso_ms("2026-11-10T12:00:00Z"),
                            CFG, now_ms=now)
    assert mf.stats["substitute_reason"] == "opera_stale"
    assert (mf.source_id[10:50, :] == mosaic.SOURCE_DPC).all()
    assert mf.stats["pixels_opera"] == 0


def test_seam_pixels_are_marked_and_excluded_from_detect(tmp_path):
    dpc = np.full((60, 60), -9999.0, dtype="float32")
    dpc[20:40, :30] = 40.0          # fascia che si ferma al confine
    raster = _raster(tmp_path, dpc, when_ms=_iso_ms("2026-11-10T12:00:00Z"))
    opera = np.full((60, 60), np.nan, dtype="float32")
    opera[20:40, 30:] = 40.0
    mf = mosaic.merge_frame(raster, opera, _iso_ms("2026-11-10T12:00:00Z"),
                            CFG, now_ms=_iso_ms("2026-11-10T12:02:00Z"))
    assert mf.seam_mask.any()
    # il seam e' una fascia di 2 px a cavallo di col 30
    cols = np.nonzero(mf.seam_mask.any(axis=0))[0]
    assert cols.min() == 28 and cols.max() == 31
    cells_full = detect_mod.detect_cells(mf.raster, CFG["detect"])
    cells_excl = detect_mod.detect_cells(mf.raster, CFG["detect"],
                                         exclude_mask=mf.seam_mask)
    assert cells_full and len(cells_excl) >= 2      # la cella viene spezzata
    assert all(c.mixed is False for c in cells_excl)
    n_mixed = mosaic.mark_cells_mixed(cells_full, mf.seam_mask, mf.raster,
                                      CFG["detect"])
    assert n_mixed == len(cells_full)
    assert cells_full[0].to_dict().get("mixed") is True


def test_merge_is_deterministic(tmp_path):
    dpc = np.full((60, 60), -9999.0, dtype="float32")
    dpc[5:20, 5:40] = 38.0
    raster = _raster(tmp_path, dpc, when_ms=_iso_ms("2026-11-10T12:00:00Z"))
    opera = np.full((60, 60), np.nan, dtype="float32")
    opera[30:50, 10:55] = 27.5
    a = mosaic.merge_frame(raster, opera, _iso_ms("2026-11-10T12:00:00Z"),
                           CFG, now_ms=_iso_ms("2026-11-10T12:01:00Z"))
    b = mosaic.merge_frame(raster, opera.copy(),
                           _iso_ms("2026-11-10T12:00:00Z"),
                           CFG, now_ms=_iso_ms("2026-11-10T12:01:00Z"))
    assert a.digest() == b.digest()
    assert a.source_id.tobytes() == b.source_id.tobytes()


def test_historical_frame_keeps_dpc_even_if_old(tmp_path):
    """Il gate assoluto vale SOLO per il frame piu' recente: un frame storico
    di 60 minuti non deve essere sostituito da OPERA (romperebbe la timeline)."""
    dpc = np.full((60, 60), -9999.0, dtype="float32")
    dpc[10:50, :] = 35.0
    raster = _raster(tmp_path, dpc, when_ms=_iso_ms("2026-11-10T11:00:00Z"))
    opera = np.full((60, 60), 22.0, dtype="float32")
    mf = mosaic.merge_frame(raster, opera, _iso_ms("2026-11-10T11:00:00Z"),
                            CFG, now_ms=_iso_ms("2026-11-10T12:00:00Z"),
                            is_latest=False)
    assert mf.stats["substituted"] is False
    assert (mf.source_id[10:50, :] == mosaic.SOURCE_DPC).all()
    assert mf.stats["slot_delta_min"] == pytest.approx(0.0)


def test_source_summary_reports_pixels_and_pct(tmp_path):
    dpc = np.full((60, 60), -9999.0, dtype="float32")
    dpc[0:30, :] = 30.0
    raster = _raster(tmp_path, dpc, when_ms=_iso_ms("2026-11-10T12:00:00Z"))
    opera = np.full((60, 60), 25.0, dtype="float32")
    mf = mosaic.merge_frame(raster, opera, _iso_ms("2026-11-10T12:00:00Z"),
                            CFG, now_ms=_iso_ms("2026-11-10T12:01:00Z"))
    res = mosaic.MosaicResult([mf], "ok")
    rows = mosaic.source_summary(res)
    by_id = {r["id"]: r for r in rows}
    assert by_id["dpc"]["pixels"] == 30 * 60
    assert by_id["dpc"]["pct"] == pytest.approx(50.0)
    assert by_id["opera"]["pixels"] == 30 * 60
    assert by_id["opera"]["license"] == "CC BY 4.0"
    assert "EUMETNET OPERA" in by_id["opera"]["attribution"]


def test_apply_degrades_cleanly_when_listing_fails(tmp_path, monkeypatch):
    dpc = np.full((60, 60), -9999.0, dtype="float32")
    dpc[10:20, 10:20] = 30.0
    raster = _raster(tmp_path, dpc, when_ms=_iso_ms("2026-11-10T12:00:00Z"))

    def boom(*a, **k):
        raise source_opera.models.SourceError("http_503")

    monkeypatch.setattr(source_opera, "resolve_slots", boom)
    res = mosaic.apply([raster], CFG, now_ms=_iso_ms("2026-11-10T12:05:00Z"))
    assert res.status == "unavailable"
    assert "listing_failed" in res.reason
    assert res.frames == []
    # il motore non riceve eccezioni: il frame resta disponibile a valle
    assert raster.valid_mask.any()
