#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del BUNDLE DPC ALLINEATO (SRI/POH/VIL/ETM) — 2026-11-12.

Cosa coprono (senza rete, senza dati radar reali):
  1) sentinelle DISTINTE per prodotto: VMI/ETM/CAPPI usano -9999 (fuori
     copertura) e -9998 (in copertura senza eco); VIL/POH/SRI usano -9999 e
     0.0 come ASSENZA VALIDA (nessun -9998). Un filtro unico `> -9997`
     sbaglia: conta gli 0 di VIL/POH/SRI come eco e butta -9998 fuori dal
     dominio VMI.
  2) CAPPI non troncati a 10 dBZ: -29 dBZ e' un valore, non una sentinella.
  3) skew esplicito: un prodotto a +300 s (una cadenza) e' `stale_for_frame`
     e NON entra nel riferimento/feature; skew 0 resta usabile.
  4) accumulo: la somma dei rate istantanei convertita in mm (SRI mm/h ->
     `rate * 300 / 3600`) coincide con il conto a mano su 6 frame.
  5) feature per cella: SRI max/mean (0.0 valido, -9999 escluso) e POH
     max/pixels>0.5 dentro la maschera della cella.
  6) prodotto mancante -> null + motivo, MAI 0 (QC, riferimento e feature).
  7) pubblicazione: `rain` e `hail.poh` nel record e i contatori in summary.

I test nuovi falliscono sul codice precedente: `product_masks`,
`qc_product`, `qc_bundle`, `rate_stats_in_mask`, `accumulate_rate`,
`fetch_product_bundle` e `_rain_hail_for_candidate` non esistevano.
"""

import datetime as dt

import numpy as np
import pytest
from affine import Affine

from radar_engine import fetch
from radar_engine import main as main_mod
from radar_engine import models
from radar_engine import output
from radar_engine import preprocess
from radar_engine.config import CONFIG

from conftest import (DPC_TM_WKT, add_blob, blank_grid, write_synthetic_tif)

T0 = 1_794_496_500_000          # 2026-11-12T15:15:00Z (slot PT5M)
SMALL = Affine(1000.0, 0.0, -10000.0, 0.0, -1000.0, 10000.0)   # 20x20
SMALL60 = Affine(1000.0, 0.0, -30000.0, 0.0, -1000.0, 30000.0)  # 60x60


def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000.0,
                                     dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _key(product, ts):
    d = dt.datetime.fromtimestamp(ts / 1000.0, dt.timezone.utc)
    return "{}/{:02d}-{:02d}-{:04d}-{:02d}-{:02d}.tif".format(
        product, d.day, d.month, d.year, d.hour, d.minute)


def _cfg(tmp_path):
    return {
        "source": {
            "api_base": "https://example.invalid",
            "origin_header": "https://example.invalid",
            "product_type": "VMI",
            "history_frames": 6,
            "period_s": 300,
            "http_timeout_s": 5,
            "download_timeout_s": 5,
            "max_download_retries": 0,
            "fetch_workers": 1,
            "cache_dir": str(tmp_path / "cache"),
            "keep_raw_frames": False,
        },
        "preprocess": {
            "nodata_values": [-9999.0, -9998.0],
            "geo_plausible_bbox": CONFIG["preprocess"]["geo_plausible_bbox"],
        },
        "phase2": {"structure": {"bundle": {
            "stale_after_s": 300,
            "accum_step_s": 300,
            "poh_threshold": 0.5,
            "retain_non_reference": ["poh", "sri"],
        }}},
    }


def _downloader(tmp_path, arrays, skew_by_product=None, fail=()):
    """Fake downloader: scrive un GeoTIFF sintetico e restituisce (key, bytes).

    `skew_by_product` sposta la chiave S3 di N ms rispetto allo slot
    richiesto (per simulare il caso VMI 14:40 vs VIL 14:45). `fail` elenca i
    product type che rispondono SourceError (prodotto assente)."""
    skew_by_product = skew_by_product or {}
    counter = {"n": 0}

    def _fn(probe, api_base=None, origin=None, timeout_s=30,
            download_timeout_s=60, retries=2):
        if probe.product_type in fail:
            raise models.SourceError("not_found")
        arr = arrays.get(probe.product_type)
        if arr is None:
            raise models.SourceError("not_found")
        counter["n"] += 1
        key_ts = int(probe.time_ms) + int(
            skew_by_product.get(probe.product_type, 0))
        path = tmp_path / "src_{}_{}.tif".format(probe.product_type,
                                                 counter["n"])
        write_synthetic_tif(str(path), arr,
                            transform=SMALL if arr.shape == (20, 20)
                            else SMALL60)
        return _key(probe.product_type, key_ts), path.read_bytes()

    return _fn


def _read_vmi(tmp_path, arr, ts, transform=SMALL):
    path = write_synthetic_tif(str(tmp_path / "vmi_ref.tif"), arr,
                               transform=transform)
    rd = preprocess.read_raster(
        str(path), nodata_values=[-9999.0, -9998.0],
        geo_plausible_bbox=CONFIG["preprocess"]["geo_plausible_bbox"])
    rd.time_ms = int(ts)
    rd.time_iso = _iso(ts)
    return rd


# ---------------------------------------------------------------------------
# 1. Sentinelle DISTINTE per prodotto
# ---------------------------------------------------------------------------
def test_sentinels_are_product_specific():
    vmi = np.array([[10.0, -9998.0], [-9999.0, 0.0]])
    m = preprocess.product_masks(vmi, "VMI")
    assert m["family"] == "echo"
    assert m["valid"][0, 0] and not m["valid"][0, 1] and not m["valid"][1, 0]
    assert m["no_echo"][0, 1]                 # -9998 = nessun eco
    assert m["outside"][1, 0]                 # -9999 = fuori copertura
    assert m["domain"][0, 1]                  # -9998 e' DENTRO il dominio
    assert not m["domain"][1, 0]
    assert m["valid"][1, 1]                   # 0 dBZ e' un valore valido

    vil = np.array([[0.0, -9999.0], [2.5, -9999.0]])
    m = preprocess.product_masks(vil, "VIL")
    assert m["family"] == "zero_absence"
    assert m["valid"][0, 0] and not m["outside"][0, 0]   # 0.0 = assenza valida
    assert int(m["no_echo"].sum()) == 0                  # VIL non ha -9998
    assert m["outside"][0, 1] and m["outside"][1, 1]
    obs = preprocess.product_observed_mask(vil, "VIL")
    assert not obs[0, 0]                      # 0.0 NON e' segnale
    assert obs[1, 0]                          # 2.5 mm/h e' segnale


def test_cappi_negative_is_a_value_not_a_threshold():
    cappi = np.array([[-29.0, 10.0, -9998.0]])
    m = preprocess.product_masks(cappi, "CAPPI_3")
    assert m["family"] == "echo"
    assert m["valid"][0, 0]                   # -29 dBZ misurato, non sentinella
    assert m["valid"][0, 1]
    assert not m["valid"][0, 2] and m["no_echo"][0, 2]


def test_qc_product_counts_zero_and_no_echo_separately():
    vmi = np.array([[10.0, -9998.0], [-9999.0, -9998.0]])
    qc = preprocess.qc_product(vmi, "VMI")
    assert qc["valid_pixels"] == 1
    assert qc["no_echo_pixels"] == 2
    assert qc["outside_domain_pixels"] == 1
    assert qc["domain_pixels"] == 3
    vil = np.array([[0.0, 0.0], [-9999.0, 5.0]])
    qc = preprocess.qc_product(vil, "VIL")
    assert qc["valid_pixels"] == 3            # i due 0.0 sono validi
    assert qc["zero_pixels"] == 2
    assert qc["outside_domain_pixels"] == 1
    assert qc["no_echo_pixels"] == 0


# ---------------------------------------------------------------------------
# 2. Skew: 300 s = stale, 0 = usabile
# ---------------------------------------------------------------------------
def test_skew_300_is_stale(tmp_path):
    cfg = _cfg(tmp_path)
    arrays = {
        "VIL": np.full((20, 20), 1.0, dtype="float32"),
        "SRI": np.full((20, 20), 2.0, dtype="float32"),
    }
    out = fetch.fetch_product_bundle(
        cfg, [T0], {"vil": "VIL", "sri": "SRI"}, warnings=[],
        downloader=_downloader(tmp_path, arrays,
                               skew_by_product={"SRI": 300000}),
        reference_slot_ms=T0, reference_rasters={})
    sri = out["entries"][T0]["sri"]
    assert sri["skew_s"] == 300
    assert sri["stale_for_frame"] is True
    assert out["reference"]["sri"] is None
    assert "stale_for_frame" in out["reference_reason"]["sri"]
    assert out["qc"]["stale"][0]["product"] == "sri"
    assert out["qc"]["stale"][0]["skew_s"] == 300
    vil = out["entries"][T0]["vil"]
    assert vil["skew_s"] == 0 and vil["stale_for_frame"] is False
    assert out["reference"]["vil"] is not None


def test_skew_negative_is_also_stale(tmp_path):
    cfg = _cfg(tmp_path)
    arrays = {"SRI": np.full((20, 20), 2.0, dtype="float32")}
    out = fetch.fetch_product_bundle(
        cfg, [T0], {"sri": "SRI"}, warnings=[],
        downloader=_downloader(tmp_path, arrays,
                               skew_by_product={"SRI": -300000}),
        reference_slot_ms=T0, reference_rasters={})
    sri = out["entries"][T0]["sri"]
    assert sri["skew_s"] == -300
    assert sri["stale_for_frame"] is True


# ---------------------------------------------------------------------------
# 3. Accumulo: rate mm/h * step_s / 3600, somma sui frame
# ---------------------------------------------------------------------------
def test_accumulation_matches_manual_sum():
    mask = np.array([[True, True], [False, False]])
    rates = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    frames = []
    for i, rate in enumerate(rates):
        # i pixel fuori maschera valgono 99: NON devono entrare
        frames.append((T0 + i * 300000,
                       np.array([[rate, rate], [99.0, 99.0]])))
    out = preprocess.accumulate_rate(frames, "SRI", mask, 300)
    manual = sum(r * 300.0 / 3600.0 for r in rates)
    assert out["accum_mm"] == pytest.approx(manual, abs=1e-12)
    assert out["frames_used"] == 6
    assert out["duration_s"] == 1800.0
    assert out["null_reason"] is None


def test_accumulation_skips_frame_without_valid_pixels():
    mask = np.ones((2, 2), dtype=bool)
    frames = [
        (T0, np.full((2, 2), 3.0)),
        (T0 + 300000, np.full((2, 2), -9999.0)),   # fuori copertura
    ]
    out = preprocess.accumulate_rate(frames, "SRI", mask, 300)
    assert out["frames_used"] == 1
    assert out["accum_mm"] == pytest.approx(3.0 * 300.0 / 3600.0)
    assert out["frames_skipped"][0]["slot_ms"] == T0 + 300000


def test_rate_stats_zero_valid_outside_excluded():
    mask = np.array([[True, True, True, True]])
    sri = np.array([[0.0, 2.0, 4.0, -9999.0]])
    stats = preprocess.rate_stats_in_mask(sri, "SRI", mask)
    assert stats["pixels"] == 3               # 0.0 incluso, -9999 escluso
    assert stats["max"] == 4.0
    assert stats["mean"] == pytest.approx(2.0)   # (0+2+4)/3
    assert stats["outside_pixels"] == 1
    poh = np.array([[0.0, 0.4, 0.6, -9999.0]])
    stats = preprocess.rate_stats_in_mask(poh, "POH", mask, threshold=0.5)
    assert stats["max"] == pytest.approx(0.6)
    assert stats["pixels_gt_threshold"] == 1  # strettamente > 0.5
    assert stats["mean"] == pytest.approx(1.0 / 3.0)


def test_missing_product_is_null_never_zero():
    mask = np.ones((2, 2), dtype=bool)
    stats = preprocess.rate_stats_in_mask(
        np.full((2, 2), -9999.0), "SRI", mask)
    assert stats["pixels"] == 0
    assert stats["max"] is None and stats["mean"] is None


# ---------------------------------------------------------------------------
# 4. Bundle: QC di dominio e prodotto mancante
# ---------------------------------------------------------------------------
def test_bundle_qc_domain_alignment_and_missing(tmp_path):
    cfg = _cfg(tmp_path)
    vmi_arr = blank_grid(20, 20, fill=-9999.0)
    vmi_arr[0:10, :] = -9998.0          # nessun eco (dentro dominio)
    vmi_arr[0:5, 0:5] = 35.0
    vmi = _read_vmi(tmp_path, vmi_arr, T0)

    vil = np.zeros((20, 20), dtype="float32")
    vil[0:10, :] = 2.0
    vil[10:, :] = -9999.0
    poh = np.zeros((20, 20), dtype="float32")
    poh[0:10, :] = 0.3
    poh[10:, :] = -9999.0
    sri = np.zeros((20, 20), dtype="float32")
    sri[0:5, :] = 1.5
    sri[5:10, :] = 0.0
    sri[10:, :] = -9999.0

    out = fetch.fetch_product_bundle(
        cfg, [T0], {"vil": "VIL", "poh": "POH", "sri": "SRI",
                    "etm": "ETM"}, warnings=[],
        downloader=_downloader(tmp_path, {"VIL": vil, "POH": poh,
                                          "SRI": sri},
                               fail=("ETM",)),
        reference_slot_ms=T0, reference_rasters={T0: vmi})
    assert out["status"] == "partial"
    assert out["reference"]["vil"] is not None
    assert out["reference"]["etm"] is None
    assert "not_found" in out["reference_reason"]["etm"]
    assert out["qc"]["missing"][_iso(T0)] == ["etm"]
    assert out["qc"]["products"]["sri"]["valid_pixels_total"] == 200
    assert out["qc"]["products"]["sri"]["zero_pixels_total"] == 100
    assert out["qc"]["products"]["sri"]["outside_domain_pixels_total"] == 200
    # VMI: -9998 (nessun eco) e' dentro il dominio, non fuori
    vmi_qc = out["entries"][T0]["vmi"]["qc"]
    assert vmi_qc["no_echo_pixels"] == 175
    assert vmi_qc["valid_pixels"] == 25
    assert vmi_qc["outside_domain_pixels"] == 200
    align = out["alignments"][_iso(T0)]
    assert align["intersection_pixels"] == 200
    assert align["mismatch_pixels"] == 0


# ---------------------------------------------------------------------------
# 5. Feature per cella/struttura dal bundle (SRI + POH)
# ---------------------------------------------------------------------------
def _bundle_with_blob(tmp_path):
    arr = blank_grid(60, 60, fill=-9999.0)
    add_blob(arr, 30, 30, 5, 40.0)
    vmi = _read_vmi(tmp_path, arr, T0, transform=SMALL60)
    cell = models.DetectedCell(
        cell_id="c1", timestamp_ms=T0, timestamp_iso=_iso(T0),
        area_km2=80.0, centroid_raster=(30.0, 30.0),
        centroid_lonlat=(12.5, 42.0), bbox_raster=(25, 25, 35, 35),
        max_dbz=40.0, mean_dbz=40.0, p90_dbz=40.0, eccentricity=0.0,
        solidity=1.0, compactness=1.0, pixel_count=81, frame_index=0)
    b = models.EngineBundle("ok", _iso(T0), "test")
    b.frames = [vmi]
    b.cells_by_frame = [[cell]]
    b.raster_by_frame_index = {0: vmi}
    b.frame_slots_ms = [T0]
    b.frame_slots_iso = [_iso(T0)]
    return b, cell


def _raster_like(tmp_path, arr, ts, transform=SMALL60):
    return _read_vmi(tmp_path, arr, ts, transform=transform)


def test_rain_and_poh_features_inside_cell_mask(tmp_path):
    b, cell = _bundle_with_blob(tmp_path)
    sri_arr = np.full((60, 60), -9999.0, dtype="float32")
    sri_arr[24:37, 24:37] = 4.0
    sri_arr[0:24, :] = 0.0               # assenza valida fuori dalla cella
    poh_arr = np.full((60, 60), -9999.0, dtype="float32")
    poh_arr[24:37, 24:37] = 0.7
    poh_arr[0:24, :] = 0.0
    sri = _raster_like(tmp_path, sri_arr, T0)
    poh = _raster_like(tmp_path, poh_arr, T0)
    dpc = {
        "entries": {T0: {
            "sri": {"qc": {"valid_pixels": 1}, "raster": sri,
                    "valid_time_iso": _iso(T0), "skew_s": 0,
                    "stale_for_frame": False, "error": None},
            "poh": {"qc": {"valid_pixels": 1}, "raster": poh,
                    "valid_time_iso": _iso(T0), "skew_s": 0,
                    "stale_for_frame": False, "error": None},
        }},
        "slots_ms": [T0], "period_s": 300,
    }
    cand = {"cell_id": "c1", "storm_object_id": None,
            "last_seen": _iso(T0)}
    rain, poh_feat = main_mod._rain_hail_for_candidate(
        b, cand, dpc, _cfg(tmp_path), [])
    assert rain["sri_max_mmh"] == pytest.approx(4.0)
    assert rain["sri_mean_mmh"] == pytest.approx(4.0)
    assert rain["accum_30min_mm"] == pytest.approx(4.0 * 300.0 / 3600.0)
    assert rain["valid_time"] == _iso(T0)
    assert rain["null_reason"] is None
    assert poh_feat["poh_max"] == pytest.approx(0.7)
    assert poh_feat["poh_pixels_gt_0_5"] == rain["pixels"]
    assert poh_feat["valid_time"] == _iso(T0)

    main_mod._attach_poh_to_hail(cand, poh_feat)
    assert cand["hail"]["poh_max"] == pytest.approx(0.7)
    assert cand["hail"]["poh"]["source"] == "POH"
    assert cand["hail"]["poh_valid_time"] == _iso(T0)
    assert cand["hail"]["source"] == "POH"
    assert cand["hail"]["valid_time"] == _iso(T0)


def test_missing_sri_gives_null_with_reason(tmp_path):
    b, cell = _bundle_with_blob(tmp_path)
    dpc = {"entries": {T0: {
        "sri": {"qc": None, "raster": None, "valid_time_iso": None,
                "skew_s": None, "stale_for_frame": False,
                "error": "not_found"},
    }}, "slots_ms": [T0], "period_s": 300}
    cand = {"cell_id": "c1", "storm_object_id": None,
            "last_seen": _iso(T0)}
    rain, poh = main_mod._rain_hail_for_candidate(
        b, cand, dpc, _cfg(tmp_path), [])
    assert rain["sri_max_mmh"] is None
    assert rain["sri_mean_mmh"] is None
    assert rain["accum_30min_mm"] is None
    assert "sri_missing_at_frame" in rain["null_reason"]
    assert poh["poh_max"] is None
    assert "poh_missing_at_frame" in poh["null_reason"]


def test_stale_sri_is_excluded_from_feature(tmp_path):
    b, cell = _bundle_with_blob(tmp_path)
    sri_arr = np.full((60, 60), 4.0, dtype="float32")
    sri = _raster_like(tmp_path, sri_arr, T0)
    dpc = {"entries": {T0: {
        "sri": {"qc": {"valid_pixels": 1}, "raster": sri,
                "valid_time_iso": _iso(T0 + 300000), "skew_s": 300,
                "stale_for_frame": True, "error": None},
    }}, "slots_ms": [T0], "period_s": 300}
    cand = {"cell_id": "c1", "storm_object_id": None,
            "last_seen": _iso(T0)}
    rain, _poh = main_mod._rain_hail_for_candidate(
        b, cand, dpc, _cfg(tmp_path), [])
    assert rain["sri_max_mmh"] is None
    assert rain["sri_mean_mmh"] is None
    assert "sri_stale_for_frame" in rain["null_reason"]
    assert rain["stale_for_frame"] is True
    assert rain["accum_30min_mm"] is None


# ---------------------------------------------------------------------------
# 6. Pubblicazione: rain + hail.poh nel record, contatori in summary
# ---------------------------------------------------------------------------
def test_output_publishes_rain_and_poh_fields(tmp_path):
    b = models.EngineBundle("ok", _iso(T0), "test")
    b.convective_structures = [{
        "structure_id": "CS-test",
        "track_coherent": True,
        "soi": 70,
        "level": "strong",
        "rain": {"sri_max_mmh": 12.5, "sri_mean_mmh": 3.2,
                 "accum_30min_mm": 1.6, "source": "SRI_adj",
                 "valid_time": _iso(T0), "null_reason": None},
        "hail": {"poh_max": 0.62, "poh_pixels_gt_0_5": 17,
                 "poh_source": "POH", "poh_valid_time": _iso(T0),
                 "poh_null_reason": None,
                 "poh": {"poh_max": 0.62, "poh_pixels_gt_0_5": 17,
                         "source": "POH", "valid_time": _iso(T0),
                         "null_reason": None}},
    }]
    payload = output.build_convective_structures(b)
    assert payload["summary"]["rain_sri_present"] == 1
    assert payload["summary"]["rain_accum_present"] == 1
    assert payload["summary"]["hail_poh_present"] == 1
    rec = payload["structures"][0]
    assert rec["rain"]["source"] == "SRI_adj"
    assert rec["hail"]["poh_max"] == pytest.approx(0.62)
    assert rec["hail"]["poh"]["source"] == "POH"
