#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dei descrittori di forma PUBBLICABILI (multiscale.shape_descriptors).

ACCETTAZIONE = solo il crop CAPPI/VMI REALE 64x64
  scripts/tests/fixtures/volume_cappi_real.npz (slot 2026-11-15T16:15:00Z).
Nessun dato artificiale nei test di accettazione. Sul codice pristino l'import
in testa fallisce con ImportError e CONFIG['multiscale']['publish_descriptors']
con KeyError: la prova che questi test mordono il codice nuovo.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from radar_engine.multiscale import (  # ImportError sul pristino
    SHAPE_SCHEMA, shape_descriptors, shape_descriptors_null)
from radar_engine.config import CONFIG
from radar_engine import models, output as out_mod

_FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures" /
            "volume_cappi_real.npz")


@pytest.fixture(scope="module")
def real_vmi():
    with np.load(_FIXTURE) as z:
        return z["vmi"].astype(float)


# --------------------------------------------------------------------------
# Contratto / flag (KeyError sul pristino)
# --------------------------------------------------------------------------
def test_publish_descriptors_flag_independent():
    blk = CONFIG["multiscale"]
    assert blk["publish_descriptors"] is False
    assert blk["enabled"] is False
    assert "SCELTA INGEGNERISTICA" in blk["reference"]
    assert "SEPARATO" in blk["reference"]


def test_schema_constant_and_no_class_labels(real_vmi):
    assert SHAPE_SCHEMA == "meteorisk.shape_descriptors/1"
    assert out_mod.SHAPE_SCHEMA == SHAPE_SCHEMA
    r = shape_descriptors(real_vmi, 1.0)
    assert r["descriptors_schema"] == SHAPE_SCHEMA
    banned = {"level", "soi", "soi_v2", "classification", "split_by_watershed"}
    for c in r["components"]:
        assert banned.isdisjoint(c)


def test_null_payload_has_reason():
    p = shape_descriptors_null("footprint_unavailable:cell_not_found")
    assert p["components"] == []
    assert p["null_reason"] == "footprint_unavailable:cell_not_found"
    assert p["descriptors_schema"] == SHAPE_SCHEMA


# --------------------------------------------------------------------------
# ACCETTAZIONE — dati reali, nessuna sintesi
# --------------------------------------------------------------------------
def test_real_crop_descriptor_values(real_vmi):
    r = shape_descriptors(real_vmi, 1.0)
    assert r["domain"]["valid_echo_px"]["value"] == 643
    assert r["domain"]["nodata_px"]["value"] == 3114
    assert len(r["components"]) == 4           # 609/29/3/2 km2 misurati
    main = r["components"][0]
    assert main["envelope_area_km2"]["value"] == pytest.approx(609, abs=1)
    assert main["dbz_max"]["value"] == pytest.approx(44.5, abs=0.1)
    assert main["n_nuclei"]["value"] == 6       # plateau prominenti, SENZA watershed
    assert main["prominence"]["value"] == pytest.approx(24.5, abs=0.5)
    assert main["min_dist_nuclei_km"]["value"] == pytest.approx(5.385, abs=0.01)
    assert main["linearity"]["value"] == pytest.approx(0.885076, abs=1e-5)
    assert main["orientation_deg"]["value"] == pytest.approx(169.667, abs=0.01)
    assert main["completeness"]["value"] == pytest.approx(0.678451, abs=1e-5)
    scan = {s["dbz"]: s["value"] for s in main["threshold_scan"]}
    assert scan[20.0] == 18 and scan[30.0] == 11
    assert scan[45.0] == 0 and scan[50.0] == 0


def test_real_crop_null_reasons_not_zero(real_vmi):
    r = shape_descriptors(real_vmi, 1.0)
    small = r["components"][-1]                # 2 px: trop pochi per PCA
    assert small["linearity"]["value"] is None
    assert small["linearity"]["null_reason"] == "too_few_pixels"
    assert small["orientation_deg"]["null_reason"] == "too_few_pixels"
    no_peak = r["components"][2]               # 3 px a 20 dBZ
    assert no_peak["prominence"]["value"] is None
    assert no_peak["prominence"]["null_reason"] == "no_prominent_peak"
    one = r["components"][1]                   # 1 nucleo
    assert one["min_dist_nuclei_km"]["value"] is None
    assert one["min_dist_nuclei_km"]["null_reason"] == "fewer_than_two_nuclei"
    # nessun numero finito mascherato da 0 per un valore mancante
    for c in r["components"]:
        for key, f in c.items():
            if key == "threshold_scan":
                continue
            assert set(f) >= {"value", "null_reason", "source", "reference"}, key
            if f["value"] is None:
                assert f["null_reason"], key
            if isinstance(f["value"], float):
                assert np.isfinite(f["value"]), key


def test_component_masks_without_watershed(real_vmi):
    """Maschere GIA' note: il descrittore non deve dividere con watershed."""
    with np.load(_FIXTURE) as z:
        vmi = z["vmi"].astype(float)
    env = np.isfinite(vmi) & (vmi >= 20.0)
    r = shape_descriptors(vmi, 1.0, component_masks=[env])
    assert len(r["components"]) == 1
    assert r["components"][0]["n_nuclei"]["value"] >= 2
    assert "NESSUN watershed" in r["components"][0]["n_nuclei"]["reference"]


def test_determinism(real_vmi):
    a = shape_descriptors(real_vmi, 1.0, seed=42)
    b = shape_descriptors(real_vmi, 1.0, seed=42)
    c = shape_descriptors(real_vmi, 1.0, seed=7)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.dumps(a, sort_keys=True) == json.dumps(c, sort_keys=True)


# --------------------------------------------------------------------------
# Pubblicazione output + campi protetti byte-identici
# --------------------------------------------------------------------------
def _bundle_with_record():
    b = models.EngineBundle("ok", "2026-11-15T16:15:00Z", "unit")
    b.convective_structures = [{
        "structure_id": "CS-TEST", "soi": 55.5, "soi_v2": 61.2,
        "level": "strong",
        "severe_stack": {"version": "1.1.0", "mesh": {"value": 12.3}},
        "rotation_measurable": False,
        "rotation_null_reason": "no_radial_velocity_in_dpc_2d_mosaic",
    }]
    return b


def test_main_wiring_flag_on_enabled_off():
    """UNITARIO (griglia sintetica): publish_descriptors=True con enabled=False
    calcola i descrittori sul footprint noto, senza watershed e senza toccare
    i campi protetti."""
    from radar_engine import main as main_mod
    rows = cols = 64
    yy, xx = np.mgrid[0:rows, 0:cols]
    data = np.full((rows, cols), -9998.0, dtype=float)
    g = 45.0 * np.exp(-(((yy - 30) ** 2 + (xx - 32) ** 2) / (2 * 3.0 ** 2)))
    data = np.where(g >= 20, g, -9998.0)
    valid = data != -9999.0
    rd = models.RasterData(data, valid, ~valid, rows, cols, None, None, None,
                           1.0, 0, "2026-11-15T16:15:00Z", "unit",
                           -9999.0, None)
    cell = models.DetectedCell("c1", 0, "2026-11-15T16:15:00Z", 30.0,
                               (30.0, 32.0), (0.0, 0.0),
                               (24, 28, 36, 36), 45.0, 35.0, 40.0,
                               0.5, 0.9, 3.0, 100, 0)
    b = models.EngineBundle("ok", "2026-11-15T16:15:00Z", "unit")
    b.frames = [rd]
    b.raster_by_frame_index = {0: rd}
    b.cells_by_frame = [[cell]]
    b.convective_structures = [{"structure_id": "CS-1", "cell_id": "c1",
                                "soi": 50.0, "rotation_measurable": False}]
    cfg = {"multiscale": {"enabled": False, "publish_descriptors": True}}
    main_mod._attach_shape_descriptors(b, cfg)
    rec = b.convective_structures[0]
    d = rec["shape_descriptors"]
    assert d["descriptors_schema"] == SHAPE_SCHEMA
    assert d.get("null_reason") is None
    assert len(d["components"]) >= 1
    assert rec["soi"] == 50.0 and rec["rotation_measurable"] is False
    # flag OFF -> nessun campo nuovo
    b2 = models.EngineBundle("ok", "2026-11-15T16:15:00Z", "unit")
    b2.convective_structures = [{"soi": 1.0}]
    main_mod._attach_shape_descriptors(
        b2, {"multiscale": {"enabled": False, "publish_descriptors": False}})
    assert "shape_descriptors" not in b2.convective_structures[0]


def test_output_publication_and_protected_fields(real_vmi):
    b = _bundle_with_record()
    p0 = out_mod.build_convective_structures(b)
    r0 = p0["structures"][0]
    assert "shape_descriptors" not in r0
    assert "shape_schema" not in r0
    protected = ("soi", "soi_v2", "level", "severe_stack",
                 "rotation_measurable", "rotation_null_reason")
    baseline = {k: json.dumps(r0[k], sort_keys=True) for k in protected}
    assert "shape_schema" not in out_mod.build_latest(b)

    b.convective_structures[0]["shape_descriptors"] = shape_descriptors(
        real_vmi, 1.0)
    p1 = out_mod.build_convective_structures(b)
    r1 = p1["structures"][0]
    assert r1["shape_schema"] == SHAPE_SCHEMA
    for k in protected:                         # byte-identici
        assert json.dumps(r1[k], sort_keys=True) == baseline[k]
    latest = out_mod.build_latest(b)
    assert latest["shape_schema"] == SHAPE_SCHEMA
    assert latest["shape_descriptors_present"] == 1
    assert latest["shape_descriptors_components"] == 4
