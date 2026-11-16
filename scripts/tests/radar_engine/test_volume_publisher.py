#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del publisher volumetrico: dati DPC CAPPI REALI + fixture ridotte.

Nessun dato simulato nel sistema operativo: l'unico payload scritto e' in tmp_path.
La fixture `volume_cappi_real.npz` e' un ritaglio 10x64x64 del volume DPC reale
(slot 2026-11-15T16:15Z), non un sintetico.
"""
import json
import math
import os

import numpy as np
import pytest
from affine import Affine

from radar_engine import volume_publisher as vp

TS = "2026-11-15T16:15:00Z"
FIX = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "volume_cappi_real.npz"))

DPC_TM_WKT = (
    'PROJCS["unnamed",GEOGCS["WGS 84",DATUM["WGS_1984",'
    'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
    'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4326"]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",42],PARAMETER["central_meridian",12.5],'
    'PARAMETER["scale_factor",1],PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)


@pytest.fixture(scope="module")
def real():
    z = np.load(FIX)
    return {
        "volume": np.asarray(z["volume"], dtype=np.float32),
        "vmi": np.asarray(z["vmi"], dtype=np.float32),
        "mask": np.asarray(z["interp_mask"], dtype=bool),
        "meta": json.loads(z["meta"].item()),
    }


def _find_cell(cells, bbox):
    hits = [c for c in cells if c["bbox_rc_full"] == list(bbox)]
    assert len(hits) == 1, "bbox {} non trovato (trovati {})".format(
        bbox, [c["bbox_rc_full"] for c in cells])
    return hits[0]


def _expected(vmi, volume, bbox, thr=30):
    """Ricalcolo INDIPENDENTE (formule di compute_params.py, non del publisher)."""
    rmin, rmax, cmin, cmax = bbox
    r0, r1 = max(0, rmin - 5), min(vmi.shape[0], rmax + 6)
    c0, c1 = max(0, cmin - 5), min(vmi.shape[1], cmax + 6)
    sv = volume[:, r0:r1, c0:c1]
    f = np.isfinite(sv)
    cv = int((f & (sv >= thr)).sum())
    maxref = float(sv[f].max())
    meanref = float(sv[f].mean())
    et = max((float(z + 1) for z in range(sv.shape[0])
              if np.any(f[z] & (sv[z] >= 18.0))), default=None)
    et30 = max((float(z + 1) for z in range(sv.shape[0])
                if np.any(f[z] & (sv[z] >= 30.0))), default=None)
    vil = []
    for rr in range(rmin, rmax + 1):
        for cc in range(cmin, cmax + 1):
            v = 0.0
            has = False
            for z in range(volume.shape[0]):
                x = volume[z, rr, cc]
                if np.isfinite(x):
                    v += 3.44e-6 * (10.0 ** (float(x) / 10.0)) ** (4.0 / 7.0) * 1000.0
                    has = True
            if has:
                vil.append(v)
    return {
        "cv": cv, "maxref": maxref, "meanref": meanref,
        "et18": et, "et30": et30,
        "vil": max(vil) if vil else None,
        "vil_density": (max(vil) / et) if (vil and et) else None,
    }


# ---------------------------------------------------------------------------
# 1. parametri su dati DPC reali
# ---------------------------------------------------------------------------
def test_real_cappi_parameters(real):
    payload = vp.build_volume_payload(
        real["volume"], real["vmi"], timestamp_utc=TS,
        interp_mask=real["mask"], include_sections=False)
    assert payload["volume_available"] is True
    assert len(payload["cells"]) >= 1
    bbox = real["meta"]["cell_bbox_local"]
    cell = _find_cell(payload["cells"], bbox)
    assert cell["max_dbz_vmi"] == real["meta"]["cell_max_dbz_vmi"] == 44.5

    exp = _expected(real["vmi"], real["volume"], bbox)
    by = cell["by_threshold"]
    assert by["30"]["CELL_VOLUME"]["value"] == exp["cv"]
    assert by["30"]["CELL_VOLUME"]["value"] > 0
    assert by["55"]["CELL_VOLUME"]["value"] <= by["50"]["CELL_VOLUME"]["value"]
    assert cell["MAX_REFLECTIVITY"]["value"] == round(exp["maxref"], 1)
    assert cell["MEAN_REFLECTIVITY"]["value"] == round(exp["meanref"], 2)
    assert cell["ECHO_TOP_18"]["value"] == exp["et18"]
    assert by["30"]["ECHO_TOP"]["value"] == exp["et30"]
    assert cell["VIL"]["value"] == pytest.approx(exp["vil"], rel=1e-6)
    assert cell["VIL_DENSITY"]["value"] == pytest.approx(exp["vil_density"], rel=1e-6)
    assert by["30"]["VIL"]["value"] == cell["VIL"]["value"]
    # estensione verticale: max - min quota con Z>=soglia
    ext = by["30"]["VERTICAL_EXTENT"]
    assert ext["value"] is not None
    assert ext["detail"]["echo_top_km"] == by["30"]["ECHO_TOP"]["value"]
    assert ext["value"] == round(ext["detail"]["echo_top_km"]
                                 - ext["detail"]["echo_base_km"], 2)


def test_thresholds_are_configurable(real):
    thr = (20, 30, 40, 45, 50, 55, 60)
    payload = vp.build_volume_payload(
        real["volume"], real["vmi"], timestamp_utc=TS,
        thresholds=thr, include_sections=False)
    assert payload["thresholds_dbz"] == list(thr)
    cell = _find_cell(payload["cells"], real["meta"]["cell_bbox_local"])
    assert sorted(cell["by_threshold"].keys(), key=int) == [str(t) for t in thr]
    vols = [cell["by_threshold"][str(t)]["CELL_VOLUME"]["value"] for t in thr]
    assert vols == sorted(vols, reverse=True), "CELL_VOLUME deve decrescere con la soglia"
    assert cell["by_threshold"]["60"]["ECHO_TOP"]["null_reason"] == "no_voxel_ge_60dbz_in_cell"


# ---------------------------------------------------------------------------
# 2. contratto dei campi / provenance
# ---------------------------------------------------------------------------
def _all_fields(payload):
    for c in payload["cells"]:
        for name, val in c.items():
            if isinstance(val, dict) and {"value", "source", "timestamp", "quality",
                                          "null_reason"} <= set(val):
                yield "cell[{}].{}".format(c["cell_id"], name), val
        for thr, fields in c["by_threshold"].items():
            for name, val in fields.items():
                if isinstance(val, dict) and {"value", "source", "timestamp", "quality",
                                              "null_reason"} <= set(val):
                    yield "cell[{}].by_threshold[{}].{}".format(c["cell_id"], thr, name), val


def test_field_contract_and_null_reasons(real):
    payload = vp.build_volume_payload(
        real["volume"], real["vmi"], timestamp_utc=TS, include_sections=False)
    n = 0
    for where, f in _all_fields(payload):
        n += 1
        assert f["source"], where
        assert f["timestamp"] == TS, where
        assert f["quality"], where
        if f["value"] is None:
            assert isinstance(f["null_reason"], str) and f["null_reason"], where
            assert f["quality"] == "D_unavailable", where
        else:
            assert f["null_reason"] is None, where
            assert f["quality"] != "D_unavailable", where
    assert n >= 100
    json.dumps(payload, allow_nan=False)


def test_volume_source_declaration(real):
    payload = vp.build_volume_payload(
        real["volume"], real["vmi"], timestamp_utc=TS, include_sections=False)
    assert payload["volume_source"] == "DPC_CAPPI_stack"
    assert payload["volume_acquisition"] == "not_native_volumetric"
    assert "NON e' acquisizione volumetrica polare nativa" in payload["volume_source_detail"]
    assert payload["grid"]["elevations_km"] == [float(i) for i in range(1, 11)]
    assert payload["grid"]["shape_zyx"] == list(real["volume"].shape)


# ---------------------------------------------------------------------------
# 3. growth temporale
# ---------------------------------------------------------------------------
def test_growth_null_without_previous(real):
    payload = vp.build_volume_payload(
        real["volume"], real["vmi"], timestamp_utc=TS, include_sections=False)
    for c in payload["cells"]:
        for fields in c["by_threshold"].values():
            for name in ("VERTICAL_GROWTH", "VOLUME_GROWTH"):
                f = fields[name]
                assert f["value"] is None
                assert f["null_reason"] == "no_previous_volume"


def test_growth_with_previous_synthetic():
    vol = np.full((10, 40, 40), np.nan, dtype=np.float32)
    for z in range(5):
        vol[z, 18:23, 18:23] = float(40 - 2 * z)   # 40,38,36,34,32 dBZ
    vmi = np.full((40, 40), np.nan, dtype=np.float32)
    vmi[18:23, 18:23] = 35.0
    prev = vol.copy()
    prev[4, 18:23, 18:23] = np.nan                  # livello 5 km assente: echo top 4 km

    payload = vp.build_volume_payload(
        vol, vmi, timestamp_utc="2026-11-15T16:20:00Z",
        prev={"volume": prev, "vmi": vmi,
              "timestamp_utc": "2026-11-15T16:15:00Z"},
        include_sections=False)
    assert len(payload["cells"]) == 1
    f = payload["cells"][0]["by_threshold"]["30"]
    # 5 livelli x 5x5 pixel = 125 voxel >=30 dBZ (conteggio 3D, non area VMI)
    assert f["CELL_VOLUME"]["value"] == 125
    assert f["ECHO_TOP"]["value"] == 5.0
    assert f["VERTICAL_GROWTH"]["value"] == 1.0
    assert f["VOLUME_GROWTH"]["value"] == 25
    assert f["VERTICAL_GROWTH"]["detail"]["interval_min"] == 5.0
    assert f["VERTICAL_GROWTH"]["detail"]["matched_prev_cell_id"] is not None


# ---------------------------------------------------------------------------
# 4. sezioni verticali compatte, verifica "nessun valore inventato"
# ---------------------------------------------------------------------------
def test_cross_sections_compact_and_no_invented():
    vol = np.full((10, 40, 40), np.nan, dtype=np.float32)
    for z in range(6):
        vol[z, 16:24, 16:24] = float(45 - 3 * z)
    vmi = np.full((40, 40), np.nan, dtype=np.float32)
    vmi[16:24, 16:24] = 40.0
    payload = vp.build_volume_payload(
        vol, vmi, timestamp_utc=TS,
        crs_wkt=DPC_TM_WKT, transform=Affine(1000.0, 0, 0, 0, -1000.0, 0),
        n_section_points=11, section_length_km=20.0)
    cell = payload["cells"][0]
    for key in ("cross_section_longitudinal", "cross_section_transversal"):
        sec = cell[key]
        assert sec["available"] is True, sec
        assert sec["quality"] == "A_verified_no_invented"
        assert sec["detail"]["mismatch"] == 0
        assert sec["detail"]["nan_mismatch"] == 0
        assert len(sec["grid"]) == 11
        assert all(len(row) == 10 for row in sec["grid"])
        rows, cols = sec["detail"]["row_sample"], sec["detail"]["col_sample"]
        assert len(rows) == len(cols) == 11
        for i in range(11):
            for z in range(10):
                raw = vol[z, rows[i], cols[i]]
                want = None if not np.isfinite(raw) else round(float(raw), 1)
                assert sec["grid"][i][z] == want, (key, i, z)
    b1 = cell["cross_section_longitudinal"]["bearing_deg"]
    b2 = cell["cross_section_transversal"]["bearing_deg"]
    assert (b2 - b1) % 360 == 90


def test_cross_section_null_without_projection(real):
    payload = vp.build_volume_payload(real["volume"], real["vmi"], timestamp_utc=TS)
    for key in ("cross_section_longitudinal", "cross_section_transversal"):
        sec = payload["cells"][0][key]
        assert sec["available"] is False
        assert sec["null_reason"] == "no_volume_or_projection"
        assert sec["quality"] == "D_unavailable"


# ---------------------------------------------------------------------------
# 5. degradazione e scrittura atomica
# ---------------------------------------------------------------------------
def test_degradation_no_volume():
    payload = vp.build_volume_payload(None, None, timestamp_utc=TS)
    assert payload["volume_available"] is False
    assert payload["null_reason"] == "volume_unavailable"
    assert payload["cells"] == []
    json.dumps(payload, allow_nan=False)


def test_write_volume_payload_atomic(real, tmp_path):
    payload = vp.build_volume_payload(
        real["volume"], real["vmi"], timestamp_utc=TS, include_sections=False)
    path = vp.write_volume_payload(payload, str(tmp_path))
    assert os.path.basename(path) == "volume.json"
    raw = open(path, "r", encoding="utf-8").read()
    assert "\\r" not in raw and "\r" not in raw
    back = json.loads(raw)
    assert back["schema"] == vp.SCHEMA
    assert len(back["cells"]) == len(payload["cells"])
