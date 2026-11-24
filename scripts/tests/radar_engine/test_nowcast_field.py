#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del nowcast del campo di pioggia (nowcast_field.py, novembre 2026).

Coprono i vincoli dichiarati nel modulo:
  - <3 fotogrammi SRI -> `available: false` con motivo, nessuna eccezione;
  - trasporto: un blob sintetico spostato di 1 cella finisce dove deve;
  - cap 110 km/h: vettore da 200 km/h AZZERATO (non clampato a 110);
  - nessuna crescita di intensita' (max previsto <= max osservato);
  - maschera del dominio rispettata (fuori dominio -> 0);
  - `main.py` continua se il modulo fallisce (try/except con warning);
  - output: nowcast.json + 3 WebP con i campi richiesti.
"""

import json
import os
import sys

import numpy as np
import pytest

from radar_engine import clock as clock_mod
from radar_engine import main as main_mod
from radar_engine import models
from radar_engine import nowcast_field as nf
from radar_engine.config import CONFIG, load_config
from radar_engine.preprocess import read_raster

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if os.path.join(_SCRIPTS, "validation") not in sys.path:
    sys.path.insert(0, os.path.join(_SCRIPTS, "validation"))

from conftest import DEFAULT_TRANSFORM, write_synthetic_tif   # noqa: E402

T0 = 1_795_512_000_000          # 2026-11-23T10:40Z (slot SRI fittizio)
STEP_MS = 300_000


# ---------------------------------------------------------------------------
# fixture: griglia piccola con dominio a disco (fuori dominio = -9999)
# ---------------------------------------------------------------------------
def _disk_domain(rows, cols, r0, c0, radius):
    rr, cc = np.ogrid[:rows, :cols]
    return ((rr - r0) ** 2 + (cc - c0) ** 2) <= radius ** 2


def _blob(rows, cols, r0, c0, sigma=6.0, amp=20.0):
    rr, cc = np.ogrid[:rows, :cols]
    return (amp * np.exp(-(((rr - r0) ** 2 + (cc - c0) ** 2)
                           / (2.0 * sigma ** 2)))).astype("float32")


def _write_frame(tmp_path, name, field, domain, ts):
    arr = np.where(domain, field, -9999.0).astype("float32")
    path = str(tmp_path / name)
    write_synthetic_tif(path, arr, transform=DEFAULT_TRANSFORM, nodata=-9999.0)
    rd = read_raster(path, nodata_values=[-9999.0], declared_nodata_from_tiff=True,
                     geo_plausible_bbox=CONFIG["preprocess"]["geo_plausible_bbox"])
    rd.time_ms = int(ts)
    rd.time_iso = nf._iso(ts)
    return rd


class _Bundle(object):
    def __init__(self, rasters=None, verification=None):
        self.dpc_bundle = {"entries": {
            int(rd.time_ms): {"sri": {"raster": rd}}
            for rd in (rasters or [])}}
        self.warnings = []
        self._nowcast_verification = verification


def _moving_frames(tmp_path, n=3, dx=1, rows=200, cols=200, sigma=6.0):
    domain = _disk_domain(rows, cols, 100, 100, 95)
    rasters = []
    for k in range(n):
        field = _blob(rows, cols, 100, 60 + k * dx, sigma=sigma, amp=20.0)
        rasters.append(_write_frame(tmp_path, "f%d.tif" % k, field, domain,
                                    T0 + k * STEP_MS))
    return rasters, domain


def _config(tmp_path):
    cfg = load_config()
    cfg["output"] = dict(cfg["output"], out_dir=str(tmp_path / "out"))
    return cfg


# ---------------------------------------------------------------------------
# 1. fotogrammi insufficienti
# ---------------------------------------------------------------------------
def test_lt3_frames_available_false(tmp_path, monkeypatch):
    rasters, _ = _moving_frames(tmp_path, n=2)
    bundle = _Bundle(rasters)
    monkeypatch.setattr(nf, "fetch_sri_frames", lambda *a, **k: [])
    clk = clock_mod.Clock.at_ms(T0 + 2 * STEP_MS)
    payload, native = nf.compute(bundle, _config(tmp_path), clk,
                                 bundle.warnings)
    assert payload["available"] is False
    assert payload["null_reason"] == "sri_frames_lt_3"
    assert payload["frames_used"] == 2
    assert payload["leads"] == []
    assert payload["speed_cap_kmh"] == 110.0
    assert payload["beats_persistence"] is None
    assert native is None


def test_no_frames_available_false_sri_unavailable(tmp_path, monkeypatch):
    bundle = _Bundle([])
    monkeypatch.setattr(nf, "fetch_sri_frames", lambda *a, **k: [])
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0), bundle.warnings)
    assert payload["available"] is False
    assert payload["null_reason"] == "sri_unavailable"
    assert native is None


def test_run_writes_json_when_unavailable(tmp_path, monkeypatch):
    bundle = _Bundle([])
    monkeypatch.setattr(nf, "fetch_sri_frames", lambda *a, **k: [])
    out = tmp_path / "out"
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0))
    assert payload["available"] is False
    path = out / "nowcast.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("available", "null_reason", "source", "source_slot",
                "generated_at", "leads", "speed_cap_kmh", "motion",
                "frames_used"):
        assert key in data
    assert data["source"] == "DPC_SRI"
    assert not list(out.glob("*.webp"))     # niente immagini senza campo


# ---------------------------------------------------------------------------
# 2. trasporto: blob spostato di 1 cella
# ---------------------------------------------------------------------------
def test_transport_blob_one_cell(tmp_path):
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    clk = clock_mod.Clock.at_ms(T0 + 2 * STEP_MS)
    payload, native = nf.compute(bundle, cfg, clk, bundle.warnings)
    assert payload["available"] is True, payload
    assert payload["frames_used"] == 3
    assert payload["motion"] == "optical_flow"
    # il massimo del lead +15 (3 passi da 1 px) e' 3 colonne piu' a destra
    base = np.unravel_index(np.argmax(rasters[-1].data), rasters[-1].data.shape)
    lead = np.unravel_index(np.argmax(native[15]), native[15].shape)
    assert abs(int(lead[0]) - int(base[0])) <= 1
    assert 2 <= int(lead[1]) - int(base[1]) <= 4
    # e il campo previsto resta un blob (non si e' disintegrato)
    assert native[15].max() > 0.5 * rasters[-1].data.max()


def test_flow_synthetic_one_pixel_direction(tmp_path):
    """Il flusso stimato e' ~1 px/frame nel verso giusto (non 1/4)."""
    rasters, domain = _moving_frames(tmp_path, n=2, dx=1)
    a = nf._rain_array(rasters[0])[0]
    b = nf._rain_array(rasters[1])[0]
    u, v = nf.estimate_flow(a, b, domain & (a > 0.02))
    mask = domain & (a > 0.5)
    assert 0.8 <= float(np.median(u[mask])) <= 1.2
    assert abs(float(np.median(v[mask]))) <= 0.2


# ---------------------------------------------------------------------------
# 3. cap 110 km/h: azzerato, non clampato
# ---------------------------------------------------------------------------
def test_cap_110_zeroes_200_kmh_vector():
    # passo 5 min, pixel 1 km: 200 km/h = 16.667 px/step
    u = np.array([[16.667, 8.333]], dtype=np.float32)
    v = np.array([[0.0, 0.0]], dtype=np.float32)
    cu, cv, n = nf.cap_speed(u, v, 1.0, 300)
    assert n == 1
    assert cu[0, 0] == 0.0 and cv[0, 0] == 0.0      # azzerato, NON 9.17
    assert 8.2 <= cu[0, 1] <= 8.5                   # 100 km/h invariato
    assert nf.SPEED_CAP_KMH == 110.0


def test_cap_is_per_vector_not_global():
    u = np.zeros((2, 2), dtype=np.float32)
    v = np.zeros((2, 2), dtype=np.float32)
    u[0, 0] = 20.0           # 240 km/h
    u[1, 1] = 2.0            # 24 km/h
    cu, _cv, n = nf.cap_speed(u, v, 1.0, 300)
    assert n == 1
    assert cu[0, 0] == 0.0
    assert cu[1, 1] == 2.0


# ---------------------------------------------------------------------------
# 4. nessuna crescita di intensita'
# ---------------------------------------------------------------------------
def test_no_intensity_growth(tmp_path):
    rasters, _domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    observed_max = float(rasters[-1].data.max())
    for lead in nf.LEADS_MIN:
        assert float(native[lead].max()) <= observed_max + 1e-6
        for item in payload["leads"]:
            if item["lead_min"] == lead:
                assert item["stats"]["max_mmh"] <= observed_max + 0.01


# ---------------------------------------------------------------------------
# 5. maschera del dominio (mare/fuori copertura)
# ---------------------------------------------------------------------------
def test_domain_mask_respected(tmp_path):
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    assert payload["domain_mask"] == "latest_sri_valid_mask"
    for lead in nf.LEADS_MIN:
        assert float(native[lead][~domain].max(initial=0.0)) == 0.0


# ---------------------------------------------------------------------------
# 6. output: JSON + 3 WebP georeferenziati
# ---------------------------------------------------------------------------
def test_output_json_and_webp(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["available"] is True
    data = json.loads((out / "nowcast.json").read_text(encoding="utf-8"))
    assert [l["lead_min"] for l in data["leads"]] == [15, 30, 60]
    assert data["grid"]["crs"] == "EPSG:3857"
    assert len(data["grid"]["bounds"]) == 4
    assert data["grid"]["resolution_m"] == nf.OUTPUT_RES_M
    assert data["motion_algorithm"] == "lucas_kanade_pyramidal"
    assert data["legend"][0]["mmh"] == 0.05
    for lead in (15, 30, 60):
        p = out / nf.IMAGE_NAMES[lead]
        assert p.exists() and p.stat().st_size > 0
        assert data["leads"][[l["lead_min"] for l in data["leads"]].index(lead)][
            "image"] == nf.IMAGE_NAMES[lead]
        assert p.read_bytes()[:4] == b"RIFF"      # WebP container


def test_encoder_failure_degrades_but_writes_json(tmp_path, monkeypatch):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"

    def _boom(*_a, **_k):
        raise RuntimeError("webp encoder non disponibile")

    monkeypatch.setattr(nf, "write_webp", _boom)
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["available"] is False
    assert payload["null_reason"] == "image_write_failed:RuntimeError"
    assert (out / "nowcast.json").exists()


def test_beats_persistence_comes_from_verification_file(tmp_path):
    """Il verdetto NON e' calcolato nel run live: viene dal file del replay."""
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / nf.VERIFICATION_FILE).write_text(json.dumps({
        "status": "ok", "beats_persistence": False,
        "cases_with_rain": 24, "null_reason": None,
    }), encoding="utf-8")
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["beats_persistence"] is False
    data = json.loads((out / "nowcast.json").read_text(encoding="utf-8"))
    assert data["beats_persistence"] is False
    assert data["verification"]["cases_with_rain"] == 24


# ---------------------------------------------------------------------------
# 7. main.py continua se il modulo fallisce
# ---------------------------------------------------------------------------
def _pipeline_config():
    cfg = load_config()
    cfg["opera"] = dict(cfg.get("opera") or {}, enabled=False)
    cfg["doppler"] = dict(cfg.get("doppler") or {}, enabled=False)
    cfg["phase2"] = dict(cfg.get("phase2") or {}, enabled=False)
    cfg["storm"] = dict(cfg.get("storm") or {}, enabled=False)
    cfg["convective_structure"] = dict(
        cfg.get("convective_structure") or {}, enabled=False)
    cfg["multiscale"] = dict(cfg.get("multiscale") or {},
                             publish_descriptors=False)
    cfg["taxonomy"] = dict(cfg.get("taxonomy") or {}, enabled=False)
    cfg["coasting"] = dict(cfg.get("coasting") or {})
    return cfg


def test_main_continues_when_nowcast_raises(tmp_path, monkeypatch):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    cfg = _pipeline_config()

    def _window(*_a, **_k):
        slots = [int(r.time_ms) for r in rasters]
        return rasters, [], int(rasters[-1].time_ms), {}, slots

    monkeypatch.setattr(main_mod.fetch, "fetch_window", _window)

    def _boom(*_a, **_k):
        raise RuntimeError("guasto simulato del nowcast")

    monkeypatch.setattr(nf, "run", _boom)
    out = tmp_path / "out"
    bundle, rc = main_mod._run_pipeline(
        cfg, str(out), "VMI", 3, False, history_dir=None,
        clock=clock_mod.Clock.live())
    assert rc in (main_mod._OK, main_mod._DEGRADED)
    assert any("nowcast layer failed" in w for w in bundle.warnings)
    assert getattr(bundle, "nowcast", {}).get("available") is False
    assert (out / "latest.json").exists()


# ---------------------------------------------------------------------------
# 8. verifica: soglie di pubblicazione e regola di decisione
# ---------------------------------------------------------------------------
def test_aggregate_hides_scores_on_small_sample():
    from nowcast_replay import aggregate
    case = {"status": "ok", "anchor": "x",
            "leads": {"15|0.5": {"nowcast": [10, 2, 3, 100],
                                 "persistence": [8, 4, 5, 98],
                                 "rain_pixels": 12},
                      "30|0.5": {"nowcast": [9, 3, 4, 99],
                                 "persistence": [7, 5, 6, 97],
                                 "rain_pixels": 12},
                      "60|0.5": {"nowcast": [8, 4, 5, 98],
                                 "persistence": [6, 6, 7, 96],
                                 "rain_pixels": 12}}}
    res = aggregate([case] * 3, min_cases=5, publish_min_cases=20,
                    min_rain_pixels=10000)
    assert res["beats_persistence"] is None
    assert res["status"] == "not_evaluable"
    big = aggregate([case] * 25, min_cases=5, publish_min_cases=20,
                    min_rain_pixels=10)
    assert big["beats_persistence"] is True
    assert big["status"] == "ok"
    assert big["leads"]["15|0.5"]["nowcast"]["csi"] == pytest.approx(
        10 / 15.0, abs=1e-6)
