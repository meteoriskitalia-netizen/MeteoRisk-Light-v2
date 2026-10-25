#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test output.py: scrittura atomica, JSON/GeoJSON validi, no raw nel repo,
failure policy (mai sovrascrivere l'ultimo dataset valido)."""

import json
import os

import pytest

from radar_engine import models
from radar_engine import output
from radar_engine.config import CONFIG

from conftest import make_cell, make_track, DEFAULT_TRANSFORM, DPC_TM_WKT


T0 = 1_777_027_200_000


def _bundle(tmp_path):
    """Bundle con celle (f1) e track (2 frame) per testing output."""
    c1 = make_cell(12.0, 41.0, T0, area_km2=150, max_dbz=42, cell_id="c1")
    c2 = make_cell(12.1, 41.0, T0 + 300000, area_km2=160, max_dbz=44, cell_id="c2")
    tr = make_track([c1, c2], 1)
    tr.tracking_confidence = "low"
    tr.motion = {
        "velocity_kmh": 100.0, "direction_toward_deg": 90.0,
        "distance_km": 8.5, "duration_min": 5.0, "area_growth_pct": 6.7,
        "intensity_delta_dbz": 2.0, "organization_score": 61,
        "classification": "Organized Convective Cell",
        "class_probs": {"Weak Convective Cell": 0.05, "Convective Cell": 0.2,
                        "Organized Convective Cell": 0.5,
                        "Highly Organized Convective Cell": 0.25},
    }
    b = models.EngineBundle("ok", "2026-04-30T10:00:00Z", output.SOURCE_LABEL)
    b.radar_timestamp_iso = c2.timestamp_iso
    b.radar_timestamp_ms = c2.timestamp_ms
    b.data_latency_minutes = 3.5
    b.frames = []
    b.cells_by_frame = [[c1], [c2]]
    b.tracks = [tr]
    return b


def test_write_outputs_creates_files(tmp_path):
    out_dir = str(tmp_path / "radar")
    paths = output.write_outputs(_bundle(tmp_path), out_dir, engine_meta={"phase": 1})
    for name in ("latest.json", "storms.geojson", "tracks.json"):
        assert name in paths
        assert os.path.exists(paths[name])
        with open(paths[name], encoding="utf-8") as fh:
            json.load(fh)  # JSON valido


def test_latest_schema(tmp_path):
    out = str(tmp_path / "radar")
    paths = output.write_outputs(_bundle(tmp_path), out)
    with open(paths["latest.json"], encoding="utf-8") as fh:
        latest = json.load(fh)
    for key in ("status", "generated_at", "source", "radar_timestamp",
                "radar_timestamp_ms", "frames_count", "cells_count",
                "tracks_count", "tracking_confidence", "warnings"):
        assert key in latest
    assert latest["status"] == "ok"
    assert latest["tracking_confidence"] == "low"
    assert latest["tracks_count"] == 1
    assert "VMI" in latest["source"]


def test_storms_geojson_valid(tmp_path):
    out = str(tmp_path / "radar")
    paths = output.write_outputs(_bundle(tmp_path), out)
    with open(paths["storms.geojson"], encoding="utf-8") as fh:
        gj = json.load(fh)
    assert gj["type"] == "FeatureCollection"
    types = {f["geometry"]["type"] for f in gj["features"]}
    assert "Point" in types and "LineString" in types
    # niente coordinate NaN o fuori Italia nel centroid della cella
    for f in gj["features"]:
        if f["geometry"]["type"] == "Point":
            lon, lat = f["geometry"]["coordinates"]
            assert 4.0 <= lon <= 21.0 and 34.0 <= lat <= 48.0


def test_tracks_json(tmp_path):
    out = str(tmp_path / "radar")
    paths = output.write_outputs(_bundle(tmp_path), out)
    with open(paths["tracks.json"], encoding="utf-8") as fh:
        tj = json.load(fh)
    assert len(tj["tracks"]) == 1
    t = tj["tracks"][0]
    assert t["n_frames"] == 2
    assert t["organization_score"] == 61
    assert t["classification"] == "Organized Convective Cell"
    assert "Supercell" not in t["classification"]


def test_tracks_json_points_include_per_frame_morphology(tmp_path):
    """tracks.json espone la morfologia PER PUNTO (eccentricity/solidity/
    compactness dalla DetectedCell): e' la serie temporale per-frame che il
    gate K di phenomena.vortex.is_hookish deve poter leggere."""
    c1 = make_cell(12.0, 41.0, T0, area_km2=150, max_dbz=42, cell_id="c1",
                   ecc=0.95, solidity=0.80, compactness=4.0)
    c2 = make_cell(12.1, 41.0, T0 + 300000, area_km2=160, max_dbz=44,
                   cell_id="c2", ecc=0.60, solidity=0.95, compactness=1.5)
    tr = make_track([c1, c2], 1)
    b = models.EngineBundle("ok", "2026-04-30T10:00:00Z", output.SOURCE_LABEL)
    b.cells_by_frame = [[c1], [c2]]
    b.tracks = [tr]
    out = str(tmp_path / "radar")
    paths = output.write_outputs(b, out)
    with open(paths["tracks.json"], encoding="utf-8") as fh:
        tj = json.load(fh)
    pts = tj["tracks"][0]["points"]
    assert [p["frame_index"] for p in pts] == [0, 1]
    assert {"eccentricity", "solidity", "compactness"} <= set(pts[0])
    assert pts[0]["eccentricity"] == 0.95
    assert pts[0]["solidity"] == 0.80
    assert pts[0]["compactness"] == 4.0
    assert pts[1]["eccentricity"] == 0.60
    assert pts[1]["solidity"] == 0.95
    assert pts[1]["compactness"] == 1.5


def test_no_raw_geotiff_in_out_dir(tmp_path):
    out = str(tmp_path / "radar")
    output.write_outputs(_bundle(tmp_path), out)
    files = os.listdir(out)
    assert not any(f.endswith((".tif", ".tiff")) for f in files)
    assert not any(f.endswith(".tmp") for f in files)


def test_atomic_write_preserves_last_valid_on_failure(tmp_path, monkeypatch):
    out = str(tmp_path / "radar")
    paths = output.write_outputs(_bundle(tmp_path), out)
    with open(paths["latest.json"], encoding="utf-8") as fh:
        before = fh.read()

    def boom(_path, text):
        raise models.OutputError("disk full")

    monkeypatch.setattr(output, "_atomic_write_text", boom)
    with pytest.raises(models.OutputError):
        output.write_outputs(_bundle(tmp_path), out)
    # il file NON è stato toccato (mai sovrascritto su errore)
    with open(paths["latest.json"], encoding="utf-8") as fh:
        assert fh.read() == before
    assert not any(f.endswith(".tmp") for f in os.listdir(out))


def test_status_only_writes_latest(tmp_path):
    out = str(tmp_path / "radar")
    path = output.write_status_only(out, "error", "2026-04-30T10:00:00Z", ["x"])
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    assert d["status"] == "error"


def test_validate_outputs_ok_and_missing(tmp_path):
    out = str(tmp_path / "radar")
    output.write_outputs(_bundle(tmp_path), out)
    assert output.validate_outputs(out) == []
    missing = str(tmp_path / "nope")
    errors = output.validate_outputs(missing)
    assert any("latest.json" in e for e in errors)


def test_empty_frame_bundle_ok(tmp_path):
    b = models.EngineBundle("ok", "2026-04-30T10:00:00Z", output.SOURCE_LABEL)
    b.frames = []
    b.cells_by_frame = []
    b.tracks = []
    out = str(tmp_path / "radar")
    output.write_outputs(b, out)
    with open(os.path.join(out, "storms.geojson"), encoding="utf-8") as fh:
        gj = json.load(fh)
    assert gj["features"] == []


def test_latest_has_no_cappi_block_without_phase2(tmp_path):
    """1.2.4.26: nessun blocco `cappi_fetch` se il layer non ha prodotto dati.

    Il blocco e' ADDITIVO: la forma di latest.json senza phase2 resta quella
    di prima (nessuna chiave nuova), cosi' il client non vede campi vuoti."""
    b = _bundle(tmp_path)
    latest = output.build_latest(b)
    assert "cappi_fetch" not in latest
    assert set(latest) == set(output.build_latest(_bundle(tmp_path)))


def test_latest_serializes_cappi_gate_verdict(tmp_path):
    """1.2.4.26 — il verdetto del gate CAPPI e' controllabile dal pubblicato.

    Prima viveva solo in bundle.phase2["cappi_fetch"]: un dato non
    serializzato non e' verificabile. Devono esserci il valore misurato, la
    soglia, l'esito e i livelli effettivi; il dettaglio per prodotto NO (e'
    ~4 kB/run su un file committato a ogni run)."""
    b = _bundle(tmp_path)
    b.phase2 = {
        "status": "partial",
        "cappi_fetch": {
            "requested": 10, "fetched": 10, "fallback": 0, "failed": [],
            "used_slots": ["2026-10-25T05:10:00Z"], "homogeneous": True,
            "bytes": 4887958, "seconds": 12.25,
            "ref_time_iso": "2026-10-25T05:10:00Z",
            "kept_slot_iso": "2026-10-25T05:10:00Z", "slot_offset_s": 0,
            "cappi_levels": 10,
            "hail_gate": {
                "enabled": True, "threshold_g_m3": 3.5,
                "threshold_ref": "Amburn & Wolf 1997 (WAF 12:473)",
                "max_vil_density_g_m3": 3.702, "evaluated": True,
                "fetch_full_set": True, "reason": "hail_plausible",
            },
            "products": {"CAPPI_1": {"bytes": 488795}},
        },
    }
    latest = output.build_latest(b)
    cf = latest["cappi_fetch"]
    assert cf["cappi_levels"] == 10
    assert cf["homogeneous"] is True
    assert "products" not in cf
    gate = cf["hail_gate"]
    assert gate["value_g_m3"] == 3.702
    assert gate["threshold_g_m3"] == 3.5
    assert gate["passed"] is True
    assert gate["evaluated"] is True
    assert gate["reason"] == "hail_plausible"
    # `passed` falso = set completo NON scaricato (MESH null con motivo)
    b.phase2["cappi_fetch"]["hail_gate"].update(
        {"max_vil_density_g_m3": 0.889, "fetch_full_set": False,
         "reason": "hail_implausible"})
    b.phase2["cappi_fetch"]["cappi_levels"] = 2
    cf2 = output.build_latest(b)["cappi_fetch"]
    assert cf2["hail_gate"]["passed"] is False
    assert cf2["hail_gate"]["value_g_m3"] == 0.889
    assert cf2["cappi_levels"] == 2