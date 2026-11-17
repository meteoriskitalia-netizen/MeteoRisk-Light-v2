#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del campo `morphology_organization` (evidenza volumetrica 1.3).

Il volume 3D NON e' un pannello: deve entrare come DATO nei record del
classificatore. Questi test verificano la regola, il degrado onesto
(`cappi_levels_lt_4`), l'invarianza di soi/soi_v2/Doppler e il wiring in
main.py/output.py. Nessun test dipende da rete o da dati di produzione.
"""
from __future__ import annotations

import copy

import pytest

from radar_engine import morphology as morph
from radar_engine import output as out_mod


TS = "2026-11-17T12:00:00Z"


def _cell(cell_id=1, rc=(20.0, 20.0), et_km=8.0, vd=4.5, vext=6.0,
          growth=12.0, quality="A_observed"):
    return {
        "cell_id": cell_id,
        "centroid_rc_weighted": list(rc),
        "quality": quality,
        "ECHO_TOP_18": {"value": et_km},
        "VIL_DENSITY": {"value": vd},
        "by_threshold": {
            "30": {
                "VERTICAL_EXTENT": {"value": vext},
                "VOLUME_GROWTH": {"value": growth},
            },
        },
    }


def _payload(cells=None, available=True, reason=None):
    return {
        "schema": "meteorisk.volume/1",
        "volume_available": available,
        "null_reason": reason,
        "volume_timestamp_utc": TS,
        "timestamp": TS,
        "thresholds_dbz": [30, 40, 45, 50, 55],
        "cells": cells if cells is not None else [_cell()],
    }


def _structure(**overrides):
    rec = {
        "structure_id": "CS-20261117T12-cel-001",
        "soi": 72,
        "level": "strong",
        "soi_v2": 60,
        "rotation_measurable": False,
        "rotation_shear": None,
        "rotation_classification": "not_measurable",
        "position": [45.46, 9.19],
        "cell_id": 1,
    }
    rec.update(overrides)
    return rec


# ---------------------------------------------------------------------------
# 1. Regola esplicita, spiegabile, CAPPI-only
# ---------------------------------------------------------------------------
def test_classification_organized_vertical_explicit_rule():
    rec = _structure()
    field = morph.derive_morphology(rec, _payload())
    assert field["classification"] == "organized_vertical"
    assert field["supports_organized"] is True
    assert field["null_reason"] is None
    assert field["source"] == "CAPPI_3D"
    assert field["valid_time"] == TS
    assert field["values"]["echo_top_m"] == 8000
    assert field["values"]["vertical_extent_km"] == 6.0
    assert field["values"]["vil_density_g_m3"] == 4.5
    assert field["values"]["volume_growth"] == 12.0
    assert field["gates"] == {
        "echo_top_ge_min": True,
        "vertical_extent_ge_min": True,
        "vil_density_ge_min": True,
    }
    assert field["detail"]["cells_3d_matched"] == 1
    # Nessun ambiente e nessun Doppler in questo campo.
    flat = repr(field).lower()
    for forbidden in ("cape", "srh", "scp", "rotation", "doppler", "bedka", "witt"):
        assert forbidden not in flat
    assert "amburn" in flat  # unica citazione pertinente (VIL density)


def test_thresholds_are_declared_engineering_choices():
    field = morph.derive_morphology(_structure(), _payload())
    assert field["thresholds"]["echo_top_min_m"] == 6000.0
    assert field["thresholds"]["vertical_extent_min_km"] == 4.0
    assert field["thresholds"]["vil_density_min_g_m3"] == 3.5
    assert "SCELTA INGEGNERISTICA" in field["reference"]["classification"]
    # Override esplicito via config (senza toccare config.py).
    field2 = morph.derive_morphology(
        _structure(), _payload(), cfg={"echo_top_min_m": 9000.0})
    assert field2["gates"]["echo_top_ge_min"] is False
    assert field2["classification"] == "not_organized_vertical"


def test_classification_is_not_organized_when_a_gate_fails():
    payload = _payload([_cell(et_km=4.0, vd=4.5, vext=6.0)])
    field = morph.derive_morphology(_structure(), payload)
    assert field["classification"] == "not_organized_vertical"
    assert field["supports_organized"] is False
    assert field["gates"]["echo_top_ge_min"] is False
    assert field["gates"]["vil_density_ge_min"] is True


# ---------------------------------------------------------------------------
# 2. Degrado onesto: MAI inferire da 2 CAPPI
# ---------------------------------------------------------------------------
def test_volume_unavailable_null_with_cappi_levels_lt_4():
    rec = _structure()
    before = copy.deepcopy(rec)
    payload = _payload(available=False, reason="cappi_levels_lt_4", cells=[])
    field = morph.derive_morphology(rec, payload)
    assert field["classification"] is None
    assert field["supports_organized"] is False
    assert field["null_reason"] == "cappi_levels_lt_4"
    assert field["quality"] == "D_unavailable"
    # Nessun altro campo della struttura e' stato toccato.
    assert rec == before


def test_two_cappi_gate_never_produces_morphology():
    """Il gate grandine puo' scaricare solo CAPPI_2/CAPPI_6: campo SEMPRE null."""
    payload = {
        "volume_available": False,
        "null_reason": "cappi_levels_lt_4",
        "thresholds_dbz": [2, 6],
        "cappi_levels": 2,
        "cells": [],
    }
    field = morph.derive_morphology(_structure(), payload)
    assert field["classification"] is None
    assert field["null_reason"] == "cappi_levels_lt_4"


def test_no_matching_3d_cell_is_explicit_null():
    payload = _payload([_cell(rc=(0.0, 0.0))])
    rec = _structure(cell_id=999, position=[10.0, 10.0])
    field = morph.derive_morphology(rec, payload)
    assert field["classification"] is None
    assert field["null_reason"].startswith("no_3d_cell_within_")


def test_missing_3d_value_is_explicit_null_not_zero():
    cell = _cell()
    cell["ECHO_TOP_18"] = {"value": None, "null_reason": "no_voxel_ge_18dbz_in_cell"}
    field = morph.derive_morphology(_structure(), _payload([cell]))
    assert field["classification"] is None
    assert field["null_reason"].startswith("missing_3d_values:")
    assert "echo_top_m" in field["null_reason"]


# ---------------------------------------------------------------------------
# 3. Il dato passa DAI CAPPI reali (array) al campo, e la classifica cambia
# ---------------------------------------------------------------------------
def test_real_cappi_arrays_change_classification_measurably():
    import numpy as np
    from radar_engine import volume3d, volume_publisher as vpub

    H = W = 40
    cappi = {}
    def _rd(arr):
        return type("RD", (), {"data": arr, "rows": H, "cols": W})()

    for z in range(1, 11):
        arr = np.full((H, W), -9998.0, dtype=np.float32)
        # Nucleo convettivo 8x8 strongly convettivo (43..70 dBZ).
        arr[14:22, 14:22] = 40.0 + 3.0 * z
        cappi[z] = _rd(arr)
    vmi = np.full((H, W), -9998.0, dtype=np.float32)
    vmi[14:22, 14:22] = 70.0
    vmi_rd = _rd(vmi)
    volume = volume3d.build_volume_from_cappi(cappi, vmi_rd)
    assert volume is not None and volume.shape[0] == 10
    payload = vpub.build_volume_payload(
        volume, vmi, timestamp_utc=TS, include_sections=False)
    assert payload["volume_available"] is True
    assert payload["cells"]

    # Proiezione sintetica DICHIARATA: il centroide 3D viene convertito in
    # lat/lon e usato come posizione della struttura (stesso percorso reale).
    from pyproj import CRS
    from rasterio.transform import from_origin
    from radar_engine import cross_section as cs
    crs_wkt = CRS.from_epsg(32632).to_wkt()
    transform = from_origin(500000.0, 5000000.0, 1000.0, 1000.0)
    ref = {"crs_wkt": crs_wkt, "transform": transform,
           "grid": {"shape_zyx": [10, H, W]}}
    cs.set_projection(crs_wkt, transform, H, W)
    try:
        lat, lon = cs.rc_to_latlon(18.0, 18.0)
    finally:
        cs.reset_projection()

    rec = _structure(cell_id=None, position=[lat, lon])
    rec["morphology_organization"] = morph.null_field()
    before = rec["morphology_organization"]["classification"]
    out_records, summary = morph.attach(payload, [rec], ref_rd=ref)
    field = out_records[0]["morphology_organization"]
    assert before is None
    assert field["classification"] in ("organized_vertical", "not_organized_vertical")
    assert field["classification"] == "organized_vertical"
    assert field["supports_organized"] is True
    # La sorgente e' il volume CAPPI reale, non un dato sintetico a mano.
    assert field["source"] == "CAPPI_3D"
    assert field["detail"]["cappi_levels"] == 10
    assert summary["morphology_present"] == 1
    assert summary["organized_vertical"] == 1


# ---------------------------------------------------------------------------
# 4. Invarianza del motore SOI e del layer Doppler
# ---------------------------------------------------------------------------
def test_attach_does_not_alter_soi_or_rotation_or_soi_v2():
    records = [_structure(soi=72, soi_v2=60, rotation_shear=12.5,
                          rotation_classification="dwd_mesocyclone")]
    protected = copy.deepcopy(records)
    morph.attach(_payload(), records)
    for before, after in zip(protected, records):
        for key, value in before.items():
            assert after[key] == value
        assert set(after) - set(before) == {"morphology_organization"}


def test_attach_summary_and_null_reasons():
    records = [_structure(), _structure(structure_id="B", cell_id=2)]
    payload = _payload([_cell(1), _cell(2, rc=(30.0, 30.0))])
    records, summary = morph.attach(payload, records)
    assert summary["structures_total"] == 2
    assert summary["morphology_present"] == 2
    assert summary["organized_vertical"] == 2
    assert summary["volume_available"] is True

    degraded = [_structure(), _structure(structure_id="B")]
    degraded, s2 = morph.attach(
        _payload(available=False, reason="cappi_levels_lt_4", cells=[]),
        degraded)
    assert s2["morphology_present"] == 0
    assert s2["null_by_reason"]["cappi_levels_lt_4"] == 2
    for rec in degraded:
        assert rec["morphology_organization"]["null_reason"] == "cappi_levels_lt_4"


# ---------------------------------------------------------------------------
# 5. Pubblicazione: il campo e' nel record e il riepilogo e' nei summary
# ---------------------------------------------------------------------------
def test_output_publishes_field_in_records_and_counts_in_summaries():
    bundle = type("B", (), {})()
    cells = [_structure()]
    morph.attach(_payload(), cells)
    bundle.convective_structures = cells
    bundle.structure_tracks_evaluated = 1
    bundle.generated_at_iso = TS
    bundle.radar_timestamp_iso = TS
    bundle.status = "ok"
    bundle.source = "test"
    bundle.as_of_mode = None
    bundle.morphology_summary = {
        "volume_available": True, "morphology_present": 1,
        "organized_vertical": 1, "null_by_reason": {},
    }
    payload = out_mod.build_convective_structures(bundle, {})
    assert "morphology_organization" in payload["structures"][0]
    assert payload["summary"]["morphology_present"] == 1
    assert payload["summary"]["morphology_organized_vertical"] == 1
    # `_structure_summary` e' esattamente cio' che build_latest() espande in
    # latest.json (vedi output.py: **_structure_summary(bundle)).
    latest_summary = out_mod._structure_summary(bundle)
    assert latest_summary["morphology_present"] == 1
    assert latest_summary["morphology_organized_vertical"] == 1
    assert latest_summary["morphology_volume_available"] is True


# ---------------------------------------------------------------------------
# 6. Wiring statico (nessun pannello, nessun ambiente)
# ---------------------------------------------------------------------------
def test_main_wiring_is_after_phase2_and_before_publisher():
    import pathlib
    root = pathlib.Path(morph.__file__).resolve().parents[2]
    main_src = (root / "scripts" / "radar_engine" / "main.py").read_text(encoding="utf-8")
    assert "_attach_morphology_organization(bundle, config)" in main_src
    i_phase2 = main_src.index("_phase2_evaluate(bundle, config)")
    i_morph = main_src.index("_attach_morphology_organization(bundle, config)")
    i_pub = main_src.index("_publish_volume(bundle, out_dir, config)")
    assert i_phase2 < i_morph < i_pub
    out_src = (root / "scripts" / "radar_engine" / "output.py").read_text(encoding="utf-8")
    assert "morphology_present" in out_src
    assert "morphology_null_reasons" in out_src
    morph_src = (root / "scripts" / "radar_engine" / "morphology.py").read_text(encoding="utf-8")
    assert "CAPPI_3D" in morph_src
    for forbidden in ("cape", "srh", "scp"):
        assert forbidden not in morph_src.lower()
