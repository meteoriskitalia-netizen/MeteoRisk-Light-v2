#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Copertura Doppler: degrado ESPLICITO e conservazione per sito.

Difetto replicato (16/11/2026, run 21:30Z): con il solo composito OPERA
CIRRUS e nessuno SCAN VRADH per sito, `build_coverage` produceva ZERO
geometrie e `attach` sovrascriveva i 134 poligoni validi senza dichiarare
perche'. Il calcolo di zero geometrie e' CORRETTO; il difetto era la
pubblicazione silenziosa del degrado.

Tutti i dati sono SINTETICI: nessuna rete, nessun file reale del repo.
"""

import datetime as dt
import json

import numpy as np
import pytest

from radar_engine import doppler as dop
from radar_engine import doppler_coverage as dc
from radar_engine import models, output


CFG = None


def _cfg():
    global CFG
    if CFG is None:
        CFG = dop.config_for({})
    return CFG


def _scan(site="frale", lat=42.12971, lon=9.49639,
          valid_time="2026-11-16T21:25:00Z"):
    """Volume VRADH sintetico, tutto coperto (nessuna rete)."""
    nrays, nbins = 360, 267
    vrad = np.full((nrays, nbins), 5.0)
    az = np.arange(nrays, dtype="float64") + 0.5
    vol = {
        "site": site, "lat": lat, "lon": lon, "height_m": 63.0,
        "elangle_deg": 0.6, "nrays": nrays, "nbins": nbins,
        "rstart_m": 0.0, "rscale_m": 960.0, "az_deg": az,
        "vrad": vrad, "valid_time": valid_time, "nyquist": 62.0555,
        "maxrange_m": np.full(nrays, nbins * 960.0),
        "maxrange_source": "grid_geometry",
    }
    return dop._scan_like(vol)


def _inputs(pairs, valid_time="2026-11-16T21:20:00Z"):
    """Input minimo di build_coverage per una lista (site, scan)."""
    cfg = _cfg()
    out = []
    for site, scan in pairs:
        vol = dict(scan)
        key = f"{site}-k"
        vol["key"] = key
        volumes = [{"ts": dt.datetime(2026, 11, 16, 21, 20,
                                      tzinfo=dt.timezone.utc),
                    "elevations": {0.6: key}}]
        st = {"site": site, "valid_time": valid_time,
              "files": 0, "bytes": 0, "status": "ok"}
        out.append((site, volumes, {0.6: scan}, 0.6,
                    {key: vol}, cfg, {}, st))
    return out


# ---------------------------------------------------------------------------
# 1. Zero SCAN -> unavailable, motivo esplicito, NESSUNA geometria inventata
# ---------------------------------------------------------------------------
def test_zero_scans_publish_unavailable_with_reason_and_no_geometry():
    doc = dop.build_coverage([], _cfg())
    assert doc["coverage_status"] == "unavailable"
    assert doc["coverage_null_reason"] == "no_vradh_scan_for_doppler_coverage"
    assert doc["coverage_null_reason"] == dop.COVERAGE_NULL_REASON
    assert doc["features"] == []
    assert doc["stale"] is False
    assert doc["counts"]["polygons"] == 0
    assert doc["counts"]["sites_current"] == 0
    # il motivo distingue lo SCAN Doppler dal mosaico CIRRUS
    assert "CIRRUS" in doc["status_note"]
    assert "VRADH" in doc["status_note"]


def test_zero_scans_with_no_previous_keep_empty_doc_json_serializable(tmp_path):
    doc = dop.build_coverage([], _cfg())
    path = str(tmp_path / "doppler_coverage.geojson")
    dc.write_geojson(path, doc)
    with open(path, encoding="utf-8") as fh:
        reread = json.load(fh)
    assert reread["features"] == []
    assert reread["coverage_status"] == "unavailable"


# ---------------------------------------------------------------------------
# 2. SCAN validi -> geometria prodotta, status ok
# ---------------------------------------------------------------------------
def test_valid_scans_publish_ok_with_geometry():
    doc = dop.build_coverage(_inputs([("frale", _scan())]), _cfg())
    assert doc["coverage_status"] == "ok"
    assert doc["coverage_null_reason"] is None
    assert doc["counts"]["polygons"] > 0
    assert doc["counts"]["sites_current"] == 1
    assert doc["counts"]["sites_stale"] == 0
    assert all(f["properties"]["stale"] is False for f in doc["features"])


# ---------------------------------------------------------------------------
# 3. SCAN misti -> conserva i siti validi, degrada solo quelli mancanti
# ---------------------------------------------------------------------------
def test_mixed_scans_preserve_previous_site_and_flag_it_stale(tmp_path):
    prev = dop.build_coverage(
        _inputs([("frale", _scan("frale")),
                 ("fraja", _scan("fraja", 41.95313, 8.70054))],
                valid_time="2026-11-16T21:25:00Z"), _cfg())
    assert prev["coverage_status"] == "ok"
    prev_path = str(tmp_path / "prev.geojson")
    dc.write_geojson(prev_path, prev)
    previous_doc = dop.load_previous_coverage(prev_path)
    assert previous_doc is not None

    cur = dop.build_coverage(
        _inputs([("frale", _scan("frale"))],
                valid_time="2026-11-16T21:30:00Z"),
        _cfg(), previous_doc=previous_doc)

    assert cur["coverage_status"] == "stale"
    assert cur["stale"] is True
    assert cur["coverage_null_reason"] is None
    assert set(cur["stale_sites"]) == {"fraja"}
    assert set(cur["current_sites"]) == {"frale"}
    assert cur["counts"]["sites_current"] == 1
    assert cur["counts"]["sites_stale"] == 1

    by_site = {}
    for f in cur["features"]:
        by_site.setdefault(f["properties"]["site"], []).append(f)
    assert set(by_site) == {"frale", "fraja"}
    assert all(f["properties"]["stale"] is False for f in by_site["frale"])
    stale = by_site["fraja"]
    assert stale
    assert all(f["properties"]["stale"] is True for f in stale)
    assert all(f["properties"]["last_valid_scan"] == "2026-11-16T21:25:00Z"
               for f in stale)
    assert all(isinstance(f["properties"]["stale_age_seconds"], (int, float))
               for f in stale)
    assert "NON e' uno stato corrente" in cur["status_note"]


def test_finalize_coverage_is_idempotent():
    prev = dop.build_coverage(_inputs([("frale", _scan())]), _cfg())
    once = dop.finalize_coverage(json.loads(json.dumps(prev)),
                                 previous_doc=prev,
                                 generated_at="2026-11-16T21:35:00Z")
    twice = dop.finalize_coverage(json.loads(json.dumps(once)),
                                  previous_doc=prev,
                                  generated_at="2026-11-16T21:36:00Z")
    assert len(twice["features"]) == len(once["features"])
    assert twice["coverage_status"] == "ok"


# ---------------------------------------------------------------------------
# 4. attach(): un run vuoto NON sovrascrive il file buono senza degrado
# ---------------------------------------------------------------------------
def test_attach_does_not_wipe_valid_file_when_collect_has_no_scan(
        tmp_path, monkeypatch):
    prev = dop.build_coverage(_inputs([("frale", _scan())]), _cfg())
    out_dir = str(tmp_path)
    path = out_dir + "/doppler_coverage.geojson"
    dc.write_geojson(path, prev)
    n_before = len(prev["features"])

    empty = {
        "schema_version": dop.SCHEMA_VERSION, "status": "ok",
        "sites": [], "candidates": [], "coverage": {
            "type": "FeatureCollection", "features": [],
        }, "errors": [], "cost": {"files": 0, "bytes": 0, "seconds": 0.0},
    }
    monkeypatch.setattr(dop, "collect", lambda *a, **k: empty)

    bundle = models.EngineBundle("ok", "2026-11-16T21:35:00Z", output.SOURCE_LABEL)
    res = dop.attach(bundle, {}, out_dir=out_dir)

    assert res["coverage_status"] in ("ok", "unavailable", "stale")
    with open(path, encoding="utf-8") as fh:
        disk = json.load(fh)
    assert len(disk["features"]) == n_before, "geometria valida cancellata"
    assert disk["coverage_status"] == "stale"
    assert all(f["properties"]["stale"] is True for f in disk["features"])
    assert disk["stale_sites"] == ["frale"]


# ---------------------------------------------------------------------------
# 5. latest.json: campo leggibile, distinto da opera.status
# ---------------------------------------------------------------------------
def test_latest_publishes_coverage_status_separate_from_opera():
    b = models.EngineBundle("ok", "2026-11-16T21:35:00Z", output.SOURCE_LABEL)
    b.opera_status = {"status": "ok", "reason": None}
    b.doppler = {
        "coverage_status": "stale",
        "coverage_null_reason": None,
        "coverage_stale": True,
        "coverage_last_valid_scan": "2026-11-16T21:25:00Z",
        "coverage_stale_age_seconds": 600.0,
        "coverage_status_note": "Copertura Doppler parzialmente aggiornata.",
        "coverage_counts": {"sites_current": 1, "sites_stale": 1,
                            "polygons": 6},
    }
    payload = output.build_latest(b)
    cov = payload["doppler_coverage"]
    assert cov["coverage_status"] == "stale"
    assert cov["last_valid_scan"] == "2026-11-16T21:25:00Z"
    assert cov["stale_age_seconds"] == 600.0
    assert cov["stale"] is True
    assert cov["sites_ok"] == 1 and cov["sites_stale"] == 1
    assert cov["note"] == "Copertura Doppler parzialmente aggiornata."
    assert cov["detail_path"] == "data/radar/doppler_coverage.geojson"
    assert "SCAN OPERA" in cov["scope"]
    # opera resta il mosaico: due blocchi distinti, nessuna confusione
    assert payload["opera"]["status"] == "ok"
    assert payload["opera"]["status"] != cov["coverage_status"]


def test_latest_publishes_unavailable_reason_and_omits_when_layer_absent():
    b = models.EngineBundle("ok", "2026-11-16T21:35:00Z", output.SOURCE_LABEL)
    assert "doppler_coverage" not in output.build_latest(b)
    b.doppler = {
        "coverage_status": "unavailable",
        "coverage_null_reason": "no_vradh_scan_for_doppler_coverage",
        "coverage_stale": False,
    }
    cov = output.build_latest(b)["doppler_coverage"]
    assert cov["coverage_status"] == "unavailable"
    assert cov["coverage_null_reason"] == "no_vradh_scan_for_doppler_coverage"
    assert cov["stale"] is False
    assert cov["last_valid_scan"] is None
