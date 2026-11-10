#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test della copertura Doppler OPERA e dello schema GeoJSON (1.2.4.27)."""

import json

import h5py
import numpy as np
import pytest

from radar_engine import doppler_coverage as dc


def _write_odim(path, lat=41.95, lon=8.75, nrays=360, nbins=20,
                covered_deg=(0.0, 360.0), rscale=960.0):
    """ODIM_H5 minimale con VRADH: copertura per azimuth in `covered_deg`."""
    raw = np.full((nrays, nbins), 255, dtype="uint8")     # nodata
    az = np.arange(nrays, dtype="float64") + 0.5
    covered = (az >= covered_deg[0]) & (az < covered_deg[1])
    raw[covered, :] = 128                                 # tutti i bin validi
    with h5py.File(path, "w") as f:
        w = f.create_group("where")
        w.attrs["lat"] = lat
        w.attrs["lon"] = lon
        w.attrs["height"] = 100.0
        d1 = f.create_group("dataset1")
        dw = d1.create_group("where")
        dw.attrs["nrays"] = nrays
        dw.attrs["nbins"] = nbins
        dw.attrs["rscale"] = rscale
        dw.attrs["rstart"] = 0.0
        dw.attrs["a1gate"] = 0
        dw.attrs["elangle"] = 0.5
        node = d1.create_group("data3")
        node.create_dataset("data", data=raw)
        what = node.create_group("what")
        what.attrs["quantity"] = np.bytes_(b"VRADH")
        what.attrs["gain"] = 0.5
        what.attrs["offset"] = -60.0
        what.attrs["nodata"] = 255.0
        what.attrs["undetect"] = 254.0
        how = d1.create_group("how")
        how.attrs["startazA"] = az
    return path


def test_read_scan_measures_coverage(tmp_path):
    p = _write_odim(str(tmp_path / "fraja@20261110T1329@0.5@DBZH_TH_VRADH.h5"),
                    covered_deg=(0.0, 180.0))
    scan = dc.read_scan(p)
    assert scan["lat"] == pytest.approx(41.95)
    assert scan["nrays"] == 360
    assert scan["max_range_km"] == pytest.approx(
        (19.5 * 960.0 + 480.0) / 1000.0, rel=1e-6)
    assert scan["coverage_fraction"] == pytest.approx(0.5, abs=1e-9)
    assert scan["max_range_m"][10] > 0
    assert scan["max_range_m"][300] == 0.0


def test_read_scan_rejects_missing_vradh(tmp_path):
    p = str(tmp_path / "no_vradh.h5")
    with h5py.File(p, "w") as f:
        f.create_group("where").attrs["lat"] = 45.0
        f["where"].attrs["lon"] = 9.0
        d1 = f.create_group("dataset1")
        d1.create_group("where").attrs.update(
            {"nrays": 360, "nbins": 10, "rscale": 960.0})
        d1.create_group("data1")
    with pytest.raises(dc.models.ValidationError):
        dc.read_scan(p)


def test_coverage_sectors_full_circle_is_closed_ring():
    az = np.arange(360, dtype="float64") + 0.5
    rng = np.full(360, 200_000.0)
    secs = dc.coverage_sectors(az, rng, min_range_km=5.0)
    assert len(secs) == 1
    assert secs[0]["full_circle"] is True
    assert secs[0]["az1"] - secs[0]["az0"] == pytest.approx(360.0)


def test_coverage_sectors_splits_mute_sector():
    az = np.arange(360, dtype="float64") + 0.5
    rng = np.full(360, 200_000.0)
    rng[80:240] = 0.0                      # settore muto 80-240
    secs = dc.coverage_sectors(az, rng, min_range_km=5.0)
    assert len(secs) == 1
    span = secs[0]["az1"] - secs[0]["az0"]
    assert span == pytest.approx(200.0, abs=1.5)


def test_site_features_schema_and_geometry(tmp_path):
    p = _write_odim(str(tmp_path / "s.h5"), covered_deg=(0.0, 180.0))
    scan = dc.read_scan(p)
    feats = dc.site_features(scan, "fraja", "FR", "2026-11-10T13:29:00Z",
                             "2026-11-10T14:00:00Z")
    assert len(feats) == 1
    props = feats[0]["properties"]
    for k in ("site", "country", "max_range_km", "generated_at",
              "attribution", "license"):
        assert k in props
    assert props["license"] == "CC BY 4.0"
    assert props["attribution"] == "Meteo-France via OPERA"
    assert props["max_range_km"] == pytest.approx(19.2, abs=0.05)
    ring = feats[0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]                 # anello chiuso
    assert ring[0] == [8.75, 41.95]            # centro del sito
    assert len(ring) >= 4


def test_geojson_document_shape(tmp_path):
    p = _write_odim(str(tmp_path / "s.h5"))
    scan = dc.read_scan(p)
    feats = dc.site_features(scan, "fraja", "FR", "2026-11-10T13:29:00Z",
                             "2026-11-10T14:00:00Z")
    doc = {"type": "FeatureCollection", "features": feats,
           "counts": {"polygons": len(feats)}}
    out = tmp_path / "doppler.geojson"
    dc.write_geojson(str(out), doc)
    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["type"] == "FeatureCollection"
    assert back["features"][0]["geometry"]["type"] == "Polygon"


def test_attribution_falls_back_to_country_name():
    assert "Meteo-France" in dc.attribution_for("FR")
    assert "DHMZ" in dc.attribution_for("HR")
    assert dc.attribution_for("XX") == "XX national weather service via OPERA"
