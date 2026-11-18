#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del produttore sinottico — SOLO fixture REALI RIDOTTE.

Nessun valore meteorologico inventato: le fixture sono crop/decimazioni di frame
.om pubblicati su fm2-ncep e fm2-ecmwf (vedi scripts/synoptic/make_fixture.py).
I soli valori costruiti a mano sono manifest di prova per la LOGICA di horizon,
che non contengono dati meteorologici.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from omfiles import OmFileReader, OmFileWriter

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from synoptic import build_synoptic as B  # noqa: E402
from synoptic import tm90  # noqa: E402
from synoptic.grid import array_names, read_array  # noqa: E402

FIX = ROOT / "fixtures"
BLOCK_OM = FIX / "ecmwf_ifs025_block_reduced.om"
BLOCK_MAN = FIX / "latest_block_reduced.json"
NOBLOCK_OM = FIX / "ncep_gfs025_reduced.om"
NOBLOCK_MAN = FIX / "latest_reduced.json"


def _load_manifest(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _run(om: Path, man: Path, when: str):
    m = _load_manifest(man)
    return B.build(m, str(om), B.iso(when), domain=m["domain"])


# ---------------------------------------------------------------- real data
def test_real_reduced_frames_are_readable():
    for p in (BLOCK_OM, NOBLOCK_OM):
        r = OmFileReader.from_path(str(p))
        names = array_names(r)
        assert "geopotential_height_500hPa" in names
        z = read_array(r, "geopotential_height_500hPa")
        assert z.shape == (101, 161)
        assert np.isfinite(z).all()
        assert 4000.0 < float(z.min()) < float(z.max()) < 6500.0
        r.close()


def test_no_synthetic_values_in_example():
    """L'esempio e' stato generato dalla fixture reale: Z500 coincide col frame."""
    ex = json.loads((ROOT / "example_synoptic.json").read_text(encoding="utf-8"))
    r = OmFileReader.from_path(str(BLOCK_OM))
    z = np.asarray(read_array(r, "geopotential_height_500hPa"), dtype=float)
    r.close()
    blk = [f for f in ex["features"] if f["kind"] == "block"]
    assert blk
    g = blk[0]["fields"]
    lat = g["lat_0"]
    # i campi GHGS/GHGN sono calcolati sul punto MEDIO del segmento (tm90._feature)
    mid = g["n_longitudes"] // 2
    j = int(round((g["lon_start"] + 180.0) / 0.5)) + mid
    i = int(round((lat - 35.0) / 0.5))
    # GHGS/GHGN ricalcolati dai valori REALI del frame sulla stessa griglia
    rs = int(round((g["lat_S"] - 35.0) / 0.5))
    rn = int(round((g["lat_N"] - 35.0) / 0.5))
    ghgn = (z[i, j] - z[rs, j]) / 20.0
    assert ghgn < -10.0
    assert abs(ghgn - g["GHGN_m_per_deg"]) < 0.01


# ---------------------------------------------------------------- schema
def test_schema_is_exact():
    obj = _run(BLOCK_OM, BLOCK_MAN, "2026-11-17T12:00:00Z")
    assert set(obj) == {"schema_version", "generated_at", "source", "license", "domain",
                        "run_reference_time", "valid_time", "grid", "features", "availability"}
    assert obj["schema_version"] == 1
    assert obj["source"] == "open-meteo .om"
    assert obj["license"] == "CC BY 4.0"
    assert set(obj["grid"]) == {"nx", "ny", "dx", "dy"}
    assert set(obj["availability"]) == {"fields_missing", "horizon_exhausted"}
    for f in obj["features"]:
        assert set(f) == {"id", "kind", "geometry", "label", "confidence",
                          "fields", "null_reason", "reference"}
        assert isinstance(f["geometry"], list)
        assert all(len(pt) == 2 for pt in f["geometry"])


def test_only_validated_kinds():
    for om, man, when in ((BLOCK_OM, BLOCK_MAN, "2026-11-17T12:00:00Z"),
                          (NOBLOCK_OM, NOBLOCK_MAN, "2026-11-17T18:00:00Z")):
        kinds = {f["kind"] for f in _run(om, man, when)["features"]}
        assert kinds <= {"block", "raw_field"}, kinds
        assert not (kinds & {"front", "prefrontal", "postfrontal", "high",
                             "cold_pool", "convergence", "moist_tongue"})


def test_block_citation_only():
    obj = _run(BLOCK_OM, BLOCK_MAN, "2026-11-17T12:00:00Z")
    blocks = [f for f in obj["features"] if f["kind"] == "block"]
    assert blocks
    for f in blocks:
        assert f["confidence"] == "citation_only"
        assert "Tibaldi" in f["reference"] and "1990" in f["reference"]
        assert f["null_reason"] is None
        assert f["fields"]["GHGS_m_per_deg"] > 0
        assert f["fields"]["GHGN_m_per_deg"] < -10
        assert f["fields"]["delta_deg"] in (-4.0, 0.0, 4.0)


def test_raw_fields_have_no_calibrated_threshold():
    for om, man, when in ((BLOCK_OM, BLOCK_MAN, "2026-11-17T12:00:00Z"),
                          (NOBLOCK_OM, NOBLOCK_MAN, "2026-11-17T18:00:00Z")):
        raw = [f for f in _run(om, man, when)["features"] if f["kind"] == "raw_field"]
        assert len(raw) == 4
        for f in raw:
            assert f["confidence"] == "uncalibrated"
            assert f["null_reason"] == "threshold_not_calibrated"
            assert "campo grezzo" in f["label"]
            assert f["geometry"]  # il campo grezzo e' disegnato, non inventato


def test_raw_field_geometry_comes_from_real_extrema():
    """Un punto della polilinea rh850 e' esattamente l'argmax del campo reale."""
    obj = _run(BLOCK_OM, BLOCK_MAN, "2026-11-17T12:00:00Z")
    rh = next(f for f in obj["features"] if f["id"] == "raw_rh850")
    r = OmFileReader.from_path(str(BLOCK_OM))
    a = np.asarray(read_array(r, "relative_humidity_850hPa"), dtype=float)
    r.close()
    lat, lon = rh["geometry"][0]
    j = int(round((lon + 180.0) / 0.5))
    i_expected = int(np.nanargmax(a[:, j]))
    assert abs(lat - (35.0 + 0.5 * i_expected)) < 1e-6


# ---------------------------------------------------------------- horizon
def test_horizon_exhausted_on_gapped_domains():
    obj = _run(NOBLOCK_OM, NOBLOCK_MAN, "2026-11-17T18:00:00Z")
    assert obj["availability"]["horizon_exhausted"] is True
    assert obj["valid_time"] == "2026-11-17T18:00:00Z"  # nessuna estrapolazione
    assert [f["id"] for f in obj["features"] if f.get("null_reason") and
            "horizon_exhausted" in str(f["null_reason"])] == []  # non e' questo il caso


def test_full_window_is_not_exhausted():
    """Manifest con frame reali fino a ref+168h: nessun buco dichiarato."""
    man = _load_manifest(BLOCK_MAN)
    ref = B.iso(man["reference_time"])
    man["frames"] = []
    for h in range(0, 169, 24):
        vt = ref + timedelta(hours=h)
        man["frames"].append({"valid_time": B.z(vt), "file": BLOCK_OM.name,
                              "variables": 1, "source_mode": "real-fixture"})
    obj = B.build(man, str(BLOCK_OM), B.iso("2026-11-17T12:00:00Z"), domain="ecmwf_ifs025")
    assert obj["availability"]["horizon_exhausted"] is False


def test_no_frame_in_window_is_null_with_reason():
    man = _load_manifest(BLOCK_MAN)
    ref = B.iso(man["reference_time"])
    man["frames"] = [{"valid_time": B.z(ref + timedelta(hours=200)),
                      "file": BLOCK_OM.name, "variables": 1}]
    obj = B.build(man, "", B.iso("2026-11-20T00:00:00Z"), domain="ecmwf_ifs025")
    assert obj["valid_time"] is None
    assert obj["availability"]["horizon_exhausted"] is True
    nulls = [f for f in obj["features"] if f["null_reason"]]
    assert nulls and "horizon_exhausted" in nulls[0]["null_reason"]
    assert nulls[0]["kind"] == "raw_field"  # mai un simbolo 'block' senza dati


# ---------------------------------------------------------------- missing field
def test_missing_z500_gives_raw_field_not_block(tmp_path):
    """Riscrive la fixture reale SENZA Z500: il blocco non puo' essere emesso."""
    src = OmFileReader.from_path(str(BLOCK_OM))
    out = tmp_path / "no_z500.om"
    w = OmFileWriter.at_path(str(out))
    children = []
    for name in array_names(src):
        if name == "geopotential_height_500hPa":
            continue
        child = src.get_child_by_name(name)
        a = np.ascontiguousarray(np.asarray(
            child.read_array((slice(None), slice(None))), dtype=np.float32))
        children.append(w.write_array(a, chunks=[32, 64],
                                      scale_factor=float(child.scale_factor),
                                      add_offset=float(child.add_offset), name=name))
    for sname in ("crs_wkt", "coordinates", "valid_time", "forecast_reference_time"):
        c = src.get_child_by_name(sname)
        children.append(w.write_scalar(c.read_scalar(), name=sname))
    w.close(w.write_group(name="", children=children))
    src.close()
    man = _load_manifest(BLOCK_MAN)
    obj = B.build(man, str(out), B.iso("2026-11-17T12:00:00Z"), domain="ecmwf_ifs025")
    assert "geopotential_height_500hPa" in obj["availability"]["fields_missing"]
    blocks = [f for f in obj["features"] if f["kind"] == "block"]
    assert blocks == []
    nulls = [f for f in obj["features"] if "non calcolabile" in f["label"]]
    assert nulls and nulls[0]["kind"] == "raw_field"


def test_tm90_out_of_grid_reason():
    """Un dominio che non raggiunge 80N NON puo' emettere block."""
    r = OmFileReader.from_path(str(BLOCK_OM))
    z = read_array(r, "geopotential_height_500hPa")
    r.close()
    # griglia (101,161) che pero' arriva solo a 25N: 40/60/80N fuori dominio
    bad_grid = {"nx": 161, "ny": 101, "lonMin": -180.0, "latMin": 0.0,
                "dx": 0.5, "dy": 0.25}
    with pytest.raises(tm90.NotComputable) as exc:
        tm90.compute(z, bad_grid)
    assert "latitude" in exc.value.reason
