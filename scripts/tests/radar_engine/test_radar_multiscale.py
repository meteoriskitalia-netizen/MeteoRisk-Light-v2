#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del layer ADDITIVO multiscale.py (segmentazione multi-soglia).

ACCETTAZIONE = solo il crop CAPPI/VMI REALE
  scripts/tests/fixtures/volume_cappi_real.npz (slot 2026-11-15T16:15:00Z).
UNITARI = grid sintetiche, dichiarate come tali (nessun dato artificiale
nei test di accettazione). Sul codice pristino l'import in testa fallisce con
ImportError: e' la prova che questi test mordono il modulo nuovo.

NOTA: esiste gia' un test_multiscale.py (Fase 1.7 storm object) che NON e'
stato toccato; questo file ha nome distinto per evitare collisioni.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from radar_engine import multiscale as ms
from radar_engine.config import CONFIG

_FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures" /
            "volume_cappi_real.npz")
CFG_ON = {"enabled": True}


@pytest.fixture(scope="module")
def real_vmi():
    with np.load(_FIXTURE) as z:
        return z["vmi"].astype(float)


def test_import_and_field_contract():
    """Il modulo esiste (ImportError sul pristino) e il contratto campo e' rigido."""
    f = ms.field(1.0, reference="r")
    assert set(f) >= {"value", "null_reason", "source", "reference"}
    with pytest.raises(ValueError):
        ms.field(None)                      # None senza null_reason
    assert ms.field(None, null_reason="x")["null_reason"] == "x"


def test_default_disabled_in_config(real_vmi):
    """Default di produzione: config OFF e nessun oggetto prodotto."""
    blk = CONFIG["multiscale"]
    assert blk["enabled"] is False
    assert "SCELTA INGEGNERISTICA" in blk["reference"]
    out = ms.segment_multiscale(real_vmi, 1.0)     # nessun cfg -> default
    assert out["enabled"] is False and out["objects"] == []
    assert out["domain"] is None


# --------------------------------------------------------------------------
# ACCETTAZIONE — dati reali, nessuna sintesi
# --------------------------------------------------------------------------
def test_real_crop_domain_four_way(real_vmi):
    out = ms.segment_multiscale(real_vmi, 1.0, cfg=CFG_ON)
    d = out["domain"]
    assert d["valid_echo_px"]["value"] == 643      # misurato
    assert d["valid_no_echo_px"]["value"] == 339   # 982 validi - 643 eco
    assert d["nodata_px"]["value"] == 3114         # NaN del crop
    assert sum(d[k]["value"] for k in
               ("valid_echo_px", "valid_no_echo_px", "nodata_px")) == 64 * 64
    assert d["coverage_status"]["value"] == "insufficient_coverage"
    assert d["observed_fraction"]["value"] == pytest.approx(982 / 4096, abs=1e-6)


def test_real_crop_descriptors(real_vmi):
    out = ms.segment_multiscale(real_vmi, 1.0, cfg=CFG_ON, seed=42)
    main = out["objects"][0]
    assert main["envelope_area_km2"]["value"] == pytest.approx(609, abs=1)
    assert main["dbz_max"]["value"] == pytest.approx(44.5, abs=0.1)
    assert main["n_nuclei"]["value"] >= 2          # divisione effettiva
    assert main["split_by_watershed"]["value"] is True
    assert main["prominence"]["value"] == pytest.approx(24.5, abs=0.5)
    assert main["min_dist_nuclei_km"]["value"] >= 5.0 - 1e-9
    assert main["linearity"]["value"] == pytest.approx(0.885, abs=0.01)
    assert main["orientation_deg"]["value"] == pytest.approx(169.7, abs=0.5)
    assert main["completeness"]["value"] == pytest.approx(0.678, abs=0.01)
    # lo scan per soglia riproduce i numeri misurati nel report (nell'inviluppo)
    scan = {s["dbz"]: s["value"] for s in main["threshold_scan"]}
    assert scan[20.0] == 18 and scan[30.0] == 11
    assert scan[45.0] == 0 and scan[50.0] == 0     # 0 nuclei >=50 dBZ: null legittimo
    # ogni campo ha value/null_reason/source/reference e nessun NaN/Inf
    for obj in out["objects"]:
        for key, f in obj.items():
            if key == "threshold_scan":
                continue
            assert set(f) >= {"value", "null_reason", "source", "reference"}, key
            if isinstance(f["value"], float):
                assert np.isfinite(f["value"]), key
            if f["value"] is None:
                assert f["null_reason"], key


def test_real_crop_small_echoes_null_reasons(real_vmi):
    out = ms.segment_multiscale(real_vmi, 1.0, cfg=CFG_ON)
    small = [o for o in out["objects"] if o["envelope_area_km2"]["value"] <= 3]
    assert len(small) == 2
    for o in small:
        assert o["n_nuclei"]["value"] == 0
        assert o["prominence"]["null_reason"] == "no_prominent_peak"
        assert o["min_dist_nuclei_km"]["null_reason"] == "fewer_than_two_nuclei"


def test_determinism_same_seed(real_vmi):
    a = ms.segment_multiscale(real_vmi, 1.0, cfg=CFG_ON, seed=42)
    b = ms.segment_multiscale(real_vmi, 1.0, cfg=CFG_ON, seed=42)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    # nessuna sorgente casuale: anche un seed diverso da' lo stesso output
    c = ms.segment_multiscale(real_vmi, 1.0, cfg=CFG_ON, seed=7)
    assert json.dumps(a, sort_keys=True) == json.dumps(c, sort_keys=True)


# --------------------------------------------------------------------------
# UNITARI — griglie sintetiche (DICHIARATE, non usate come accettazione)
# --------------------------------------------------------------------------
def _grid():
    yy, xx = np.mgrid[0:24, 0:24]
    return yy, xx, np.full((24, 24), -9998.0)      # tutto in copertura, senza eco


def test_unit_single_peak_no_watershed():
    yy, xx, arr = _grid()
    g = 45.0 * np.exp(-(((yy - 12) ** 2 + (xx - 12) ** 2) / (2 * 4.0)))
    arr = np.where(g >= 10, g, -9998.0)
    out = ms.segment_multiscale(arr, 1.0, cfg=CFG_ON)
    assert len(out["objects"]) == 1
    o = out["objects"][0]
    assert o["n_nuclei"]["value"] == 1
    assert o["split_by_watershed"]["value"] is False
    assert o["min_dist_nuclei_km"]["null_reason"] == "fewer_than_two_nuclei"


def test_unit_two_peaks_watershed():
    yy, xx, arr = _grid()
    g1 = 45.0 * np.exp(-(((yy - 8) ** 2 + (xx - 8) ** 2) / (2 * 2.5 ** 2)))
    g2 = 45.0 * np.exp(-(((yy - 8) ** 2 + (xx - 16) ** 2) / (2 * 2.5 ** 2)))
    arr = np.maximum(arr, np.maximum(g1, g2))
    arr[8, 8:17] = np.maximum(arr[8, 8:17], 23.0)   # ponte 20<23<30 dBZ
    out = ms.segment_multiscale(arr, 1.0, cfg=CFG_ON)
    assert len(out["objects"]) == 1                 # UN eco connesso a 20 dBZ
    o = out["objects"][0]
    assert o["split_by_watershed"]["value"] is True
    assert o["n_nuclei"]["value"] == 2
    assert o["min_dist_nuclei_km"]["value"] == pytest.approx(8.0, abs=1)


def test_unit_no_morphology_across_nodata_gap():
    yy, xx, arr = _grid()
    g1 = 45.0 * np.exp(-(((yy - 12) ** 2 + (xx - 5) ** 2) / (2 * 2.0 ** 2)))
    g2 = 45.0 * np.exp(-(((yy - 12) ** 2 + (xx - 18) ** 2) / (2 * 2.0 ** 2)))
    arr = np.maximum(arr, np.maximum(g1, g2))
    arr[:, 12] = np.nan                             # lacuna di copertura
    out = ms.segment_multiscale(arr, 1.0, cfg=CFG_ON)
    assert len(out["objects"]) == 2
    assert out["domain"]["nodata_px"]["value"] == 24
    for o in out["objects"]:
        r0, c0, r1, c1 = o["envelope_bbox_px"]["value"]
        assert not (c0 <= 12 <= c1)                 # nessun oggetto attraversa il gap
