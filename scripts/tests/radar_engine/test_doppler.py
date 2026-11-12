#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del layer Doppler OPERA FR (shear azimutale, 1.2.4.84).

Fixture SINTETICHE (nessuna rete):
  - vortice Rankine con velocita' tangenziale nota -> shear atteso
  - campo uniforme -> shear 0
  - campo con aliasing oltre il Nyquist -> srotolato o dichiarato non
    risolvibile (mai un valore inventato)
"""

import math

import h5py
import numpy as np
import pytest

from radar_engine import doppler as dop


# ---------------------------------------------------------------------------
# Helper: campo Rankine visto da un radar
# ---------------------------------------------------------------------------
def rankine_field(nrays=360, nbins=267, rstart=0.0, rscale=960.0,
                  vmax=40.0, rc=6.0, d0=60.0, phi0=90.0):
    """Velocita' radiale di un vortice Rankine (v_t = vmax*r/rc dentro,
    vmax*rc/r fuori) centrato a (d0, phi0) dal radar. Ritorna (vrad, az)."""
    az = np.arange(nrays, dtype="float64") * (360.0 / nrays)
    r = (rstart + (np.arange(nbins, dtype="float64") + 0.5) * rscale) / 1000.0
    PHI, R = np.meshgrid(np.deg2rad(az), r, indexing="ij")
    x = R * np.cos(PHI)
    y = R * np.sin(PHI)
    x0 = d0 * math.cos(math.radians(phi0))
    y0 = d0 * math.sin(math.radians(phi0))
    dx, dy = x - x0, y - y0
    rr = np.hypot(dx, dy)
    theta = np.arctan2(dy, dx)
    vt = np.where(rr < rc, vmax * rr / rc, vmax * rc / np.maximum(rr, 1e-9))
    return vt * np.sin(PHI - theta), az


def volume_from(vrad, az, site="frale", lat=42.12971, lon=9.49639,
                height_m=63.0, el=0.6, rstart=0.0, rscale=960.0,
                valid_time="2026-11-11T20:14:46Z", nyquist=62.0555):
    sh, r_km = dop.azimuthal_shear(vrad, az, rstart, rscale, window_rays=1)
    nrays, nbins = vrad.shape
    return {
        "site": site, "lat": lat, "lon": lon, "height_m": height_m,
        "elangle_deg": el, "nrays": int(nrays), "nbins": int(nbins),
        "rstart_m": rstart, "rscale_m": rscale, "az_deg": az,
        "vrad": vrad, "valid_time": valid_time, "nyquist": nyquist,
        "shear": sh, "range_km": r_km,
        "maxrange_m": np.full(nrays, rstart + nbins * rscale),
        "maxrange_source": "grid_geometry",
    }


def _coverage_inputs(scan, el=0.6, site="frale", key="k"):
    """Input minimo per build_coverage senza rete (by_key gia' popolato)."""
    import datetime as dt
    vol = dict(scan)
    vol["key"] = key
    if "vrad" not in vol and not (np.asarray(scan["max_range_m"]) > 0).any():
        # solo per il caso "nessun eco": un volume coerente senza gate, cosi'
        # anche la versione precedente (che assume by_key = volume completo)
        # arriva in fondo e fallisce sull'assenza di `kind`.
        vol["vrad"] = np.full((int(scan["nrays"]), int(scan["nbins"])), np.nan)
        vol["az_deg"] = np.asarray(scan["az_start_deg"], dtype="float64")
    volumes = [{"ts": dt.datetime(2026, 11, 11, 19, 4, tzinfo=dt.timezone.utc),
                "elevations": {el: key}}]
    st = {"site": site, "valid_time": "2026-11-11T19:04:45Z",
          "files": 0, "bytes": 0, "status": "ok"}
    return [(site, volumes, {el: scan}, el, {key: vol},
             dop.config_for({}), {}, st)]


def _write_scan(path, nrays=360, nbins=267, rscale=960.0, el=0.6,
                ni=62.0555, gain=0.5, offset=-60.0, raw_value=150):
    raw = np.full((nrays, nbins), 255, dtype="uint8")
    raw[:, :10] = raw_value
    with h5py.File(path, "w") as f:
        w = f.create_group("where")
        w.attrs["lat"] = 42.12971
        w.attrs["lon"] = 9.49639
        w.attrs["height"] = 63.0
        f.create_group("what").attrs.update({
            "date": np.bytes_(b"20261111"), "time": np.bytes_(b"201446"),
            "object": np.bytes_(b"SCAN")})
        f.create_group("how").attrs["NI"] = float(ni)
        d1 = f.create_group("dataset1")
        dw = d1.create_group("where")
        dw.attrs.update({"nrays": nrays, "nbins": nbins, "rscale": rscale,
                         "rstart": 0.0, "a1gate": 0, "elangle": el})
        node = d1.create_group("data3")
        node.create_dataset("data", data=raw)
        node.create_group("what").attrs.update({
            "quantity": np.bytes_(b"VRADH"), "gain": float(gain),
            "offset": float(offset), "nodata": 255.0, "undetect": 254.0})
        d1.create_group("how").attrs["startazA"] = np.arange(nrays) + 0.5
    return path


# ---------------------------------------------------------------------------
# Geometria e decodifica
# ---------------------------------------------------------------------------
def test_beam_height_standard_formula():
    # 248.1 km a 0.6 deg con radar a 63 m: 6.22 km di curvatura + 0.063
    assert dop.beam_height_km(248.1, 0.6, 63.0) == pytest.approx(6.28, abs=0.01)
    assert dop.beam_height_km(100.0, 0.6, 0.0) == pytest.approx(1.64, abs=0.02)
    assert dop.beam_height_km(250.0, 0.0, 0.0) == pytest.approx(
        250.0 ** 2 / (2.0 * 8495.0), rel=1e-3)


def test_decode_negative_gain_is_physical():
    # PVOL iRadar: gain -0.5, offset 60 -> raw 90 = +15 m/s (allontanamento)
    v = dop.decode_vradh(np.array([[90, 0, 255, 241]], dtype="uint8"),
                         gain=-0.5, offset=60.0, nodata=255.0, undetect=0.0)
    assert v[0, 0] == pytest.approx(15.0)
    assert np.isnan(v[0, 1])      # undetect
    assert np.isnan(v[0, 2])      # nodata
    assert v[0, 3] == pytest.approx(-60.5)
    # OPERA: gain +0.5, offset -60 -> raw 150 = +15 m/s (stesso valore fisico)
    v2 = dop.decode_vradh(np.array([[150, 254]], dtype="uint8"),
                          gain=0.5, offset=-60.0, nodata=255.0, undetect=254.0)
    assert v2[0, 0] == pytest.approx(15.0)
    assert np.isnan(v2[0, 1])


def test_read_vradh_geometry_and_ni(tmp_path):
    p = _write_scan(str(tmp_path / "frale@20261111T2014@0.6@DBZH_TH_VRADH.h5"))
    vol = dop.read_vradh(p)
    assert vol["nrays"] == 360 and vol["nbins"] == 267
    assert vol["rscale_m"] == pytest.approx(960.0)
    assert vol["elangle_deg"] == pytest.approx(0.6)
    assert vol["nyquist"] == pytest.approx(62.0555)
    assert vol["nyquist_source"] == "how/NI"
    assert vol["valid_time"] == "2026-11-11T20:14:46Z"
    assert vol["vrad"][0, 0] == pytest.approx(15.0)      # 150*0.5-60
    assert np.isnan(vol["vrad"][0, 20])


def test_read_vradh_missing_ni_declares_it(tmp_path):
    p = _write_scan(str(tmp_path / "s.h5"))
    # rimuove NI
    with h5py.File(p, "r+") as f:
        del f["how"].attrs["NI"]
    vol = dop.read_vradh(p)
    assert vol["nyquist"] is None
    assert vol["nyquist_source"] is None
    res = dop.dealias_region_based(vol["vrad"], vol["nyquist"])
    assert res["status"] == "no_nyquist"
    assert "nyquist_missing" in res["notes"]


# ---------------------------------------------------------------------------
# Shear
# ---------------------------------------------------------------------------
def test_azimuthal_shear_uniform_field_is_zero():
    v = np.full((360, 100), 12.0)
    sh, _r = dop.azimuthal_shear(v, np.arange(360.0), 0.0, 1000.0, 1)
    assert np.nanmax(np.abs(sh)) == pytest.approx(0.0, abs=1e-12)


def test_azimuthal_shear_linear_gradient():
    nrays, nbins = 360, 200
    az = np.arange(nrays, dtype="float64")
    k = 0.01                                   # m/s per grado
    v = np.tile(k * az[:, None], (1, nbins))
    sh, r_km = dop.azimuthal_shear(v, az, 0.0, 1000.0, window_rays=1)
    expected = (k * 2.0) / (np.deg2rad(2.0) * r_km)
    # il taglio 0/360 e' una discontinuita' del campo sintetico, non del
    # metodo: si esclude il seam.
    assert np.nanmax(np.abs(sh[1:-1, :] - expected[None, :])) < 1e-9


def test_rankine_shear_matches_expected():
    truth, az = rankine_field(vmax=40.0, rc=6.0, d0=60.0)
    sh, r_km = dop.azimuthal_shear(truth, az, 0.0, 960.0, 1)
    # solo la corona del vortice, lontano dal radar (dove il campo e' liscio)
    ring = (r_km > 40.0) & (r_km < 80.0)
    peak = np.nanmax(np.abs(sh[:, ring]))
    assert 4.0 < peak < 25.0                   # shear reale, non degenere
    # simmetria: il massimo e' vicino al bordo del core (60 +- 6 km)
    j = np.nanargmax(np.nanmax(np.abs(sh[:, ring]), axis=0))
    r_peak = r_km[ring][j]
    assert 50.0 <= r_peak <= 70.0


def test_rankine_dealias_recovers_field_and_shear():
    truth, az = rankine_field(vmax=40.0, rc=6.0, d0=60.0)
    ni = 20.0
    wrapped = (truth + ni) % (2.0 * ni) - ni
    res = dop.dealias_region_based(wrapped, ni)
    assert res["method"] == "region_based"
    assert res["status"] in ("ok", "unresolved")
    good = np.isfinite(res["vrad"]) & np.isfinite(truth)
    assert good.sum() > 0.9 * truth.size
    err = np.abs(res["vrad"][good] - truth[good])
    assert np.median(err) < 0.5
    sh_d, _ = dop.azimuthal_shear(res["vrad"], az, 0.0, 960.0, 1)
    sh_t, _ = dop.azimuthal_shear(truth, az, 0.0, 960.0, 1)
    both = np.isfinite(sh_d) & np.isfinite(sh_t)
    assert np.median(np.abs(sh_d[both] - sh_t[both])) < 0.5


def test_aliased_ramp_is_unwrapped_or_declared_unresolvable():
    nrays, nbins = 360, 120
    az = np.arange(nrays, dtype="float64")
    ramp = np.tile((az * 0.5)[:, None], (1, nbins))   # 0..179.5 m/s
    ni = 40.0
    wrapped = (ramp + ni) % (2.0 * ni) - ni
    res = dop.dealias_region_based(wrapped, ni)
    # o srotolato, o dichiarato: mai un valore taciuto
    assert res["status"] in ("ok", "unresolved")
    if res["status"] == "unresolved":
        assert res["unresolved_gates"] > 0
    sh_d, _ = dop.azimuthal_shear(res["vrad"], az, 0.0, 1000.0, 1)
    sh_t, _ = dop.azimuthal_shear(ramp, az, 0.0, 1000.0, 1)
    both = np.isfinite(sh_d) & np.isfinite(sh_t)
    # un offset costante NON cambia lo shear: il gradiente deve tornare
    assert np.median(np.abs(sh_d[both] - sh_t[both])) < 1e-6


# ---------------------------------------------------------------------------
# Candidati e gate
# ---------------------------------------------------------------------------
def test_candidates_found_inside_gate():
    truth, az = rankine_field(vmax=40.0, rc=6.0, d0=60.0)
    vol = volume_from(truth, az)
    cands = dop.shear_candidates(vol, dop.config_for({}))
    assert cands
    c = cands[0]
    assert abs(c["shear_ms_km"]) >= 4.0
    assert c["range_km"] <= 250.0
    assert c["beam_height_km"] <= 8.0
    assert c["confidence"] == "high"          # 60 km a 0.6 deg -> 0.8 km
    assert c["site"] == "frale"
    assert c["description"] if "description" in c else True


def test_candidates_out_of_range_are_not_emitted():
    truth, az = rankine_field(vmax=60.0, rc=4.0, d0=280.0)
    vol = volume_from(truth, az)
    assert dop.shear_candidates(vol, dop.config_for({})) == []


def test_beam_height_gate_excludes_high_beams():
    truth, az = rankine_field(vmax=40.0, rc=6.0, d0=60.0)
    vol = volume_from(truth, az)
    cfg = dop.config_for({"doppler": {"max_beam_height_km": 0.5}})
    assert dop.shear_candidates(vol, cfg) == []


def test_persistence_requires_previous_same_sign():
    truth, az = rankine_field(vmax=40.0, rc=6.0, d0=60.0)
    vol = volume_from(truth, az)
    prev = volume_from(truth, az)
    cands = dop.shear_candidates(vol, dop.config_for({}), previous=prev)
    assert cands and all(c["persistence_scans"] == 2 for c in cands)
    prev_opp = volume_from(-truth, az)
    cands2 = dop.shear_candidates(vol, dop.config_for({}), previous=prev_opp)
    assert cands2 and all(c["persistence_scans"] == 1 for c in cands2)


def test_vertical_coherence_detected():
    truth, az = rankine_field(vmax=40.0, rc=6.0, d0=60.0)
    vol = volume_from(truth, az, el=0.6)
    other = volume_from(truth, az, el=1.01)
    cands = dop.shear_candidates(vol, dop.config_for({}), other=other)
    assert cands and all(c["vertical_coherent"] is True for c in cands)
    other_zero = volume_from(np.zeros_like(truth), az, el=1.01)
    cands2 = dop.shear_candidates(vol, dop.config_for({}), other=other_zero)
    assert cands2 and all(c["vertical_coherent"] is False for c in cands2)


# ---------------------------------------------------------------------------
# Accoppiamento con le strutture
# ---------------------------------------------------------------------------
def _structure(lat, lon, t="2026-11-11T20:15:00Z"):
    return {"structure_id": "CS-x", "position": [lon, lat], "last_seen": t}


def test_match_structures_fills_within_15km_and_5min():
    truth, az = rankine_field(vmax=40.0, rc=6.0, d0=60.0)
    vol = volume_from(truth, az)
    cands = dop.shear_candidates(vol, dop.config_for({}))
    c = cands[0]
    st = _structure(c["lat"], c["lon"])
    filled, nulls, _ = dop.match_structures([st], cands, dop.config_for({}))
    assert filled == 1 and nulls == 0
    rs = st["rotation_shear"]
    assert rs["shear_ms_km"] == c["shear_ms_km"]
    assert rs["radar"] == "frale"
    assert "shear azimutale osservato" in rs["description"]
    assert "mesociclone" not in rs["description"].lower()


def test_match_structures_null_when_far_or_late():
    truth, az = rankine_field(vmax=40.0, rc=6.0, d0=60.0)
    cands = dop.shear_candidates(volume_from(truth, az), dop.config_for({}))
    c = cands[0]
    far = _structure(c["lat"] + 1.0, c["lon"])
    late = _structure(c["lat"], c["lon"], t="2026-11-11T21:30:00Z")
    filled, nulls, reasons = dop.match_structures([far, late], cands,
                                                  dop.config_for({}))
    assert filled == 0 and nulls == 2
    assert far["rotation_shear"]["null_reason"] == \
        "no_doppler_candidate_within_15km_5min"
    assert reasons["no_doppler_candidate"] == 2


def test_attach_disabled_sets_null_reason():
    st = _structure(42.0, 12.0)
    bundle = type("B", (), {"convective_structures": [st],
                            "radar_timestamp_ms": None})()
    res = dop.attach(bundle, {"doppler": {"enabled": False}})
    assert res["status"] == "disabled"
    assert st["rotation_shear"]["null_reason"] == "doppler_disabled"


# ---------------------------------------------------------------------------
# Copertura
# ---------------------------------------------------------------------------
def test_point_in_ring_and_rome():
    ring = [[12.0, 41.5], [13.0, 41.5], [13.0, 42.5], [12.0, 42.5], [12.0, 41.5]]
    assert dop.point_in_ring(12.48, 41.89, ring) is True
    assert dop.point_in_ring(14.0, 41.89, ring) is False
    doc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
         "properties": {"site": "frale", "layer": "min_elevation",
                        "azimuth_start_deg": 0, "azimuth_end_deg": 360,
                        "max_range_km": 256.3}}]}
    hits = dop.coverage_contains(doc, 12.48, 41.89)
    assert hits and hits[0]["site"] == "frale"
    assert dop.coverage_contains(doc, 14.0, 41.89) == []


def test_coverage_from_valid_rays_contains_rome(tmp_path):
    # volume sintetico: tutti i raggi con gate validi fino a 256 km
    nrays, nbins = 360, 267
    vrad = np.full((nrays, nbins), 5.0)
    vol = volume_from(vrad, np.arange(nrays, dtype="float64") + 0.5)
    scan = dop._scan_like(vol)
    from radar_engine import doppler_coverage as dc
    feats = dc.site_features(scan, "frale", "FR", vol["valid_time"],
                             "2026-11-11T20:20:00Z")
    doc = {"type": "FeatureCollection", "features": feats}
    hits = dop.coverage_contains(doc, 12.48, 41.89)
    assert hits, "Roma (12.48E 41.89N) deve cadere nel poligono di copertura"


# ---------------------------------------------------------------------------
# Copertura GEOMETRICA vs OSSERVATA (1.2.4.85)
# ---------------------------------------------------------------------------
def test_range_at_beam_height_is_inverse():
    for el in (0.6, 1.01, 1.4):
        r = dop.range_at_beam_height(8.0, el, 63.0)
        assert dop.beam_height_km(r, el, 63.0) == pytest.approx(8.0, abs=1e-3)
    assert dop.range_at_beam_height(8.0, 0.6, 63.0) == pytest.approx(289.0, abs=2.0)
    assert dop.range_at_beam_height(8.0, 1.01, 63.0) < 250.0
    assert dop.range_at_beam_height(8.0, 1.4, 63.0) < 220.0


def test_coverage_geometric_published_without_echo():
    # NESSUN gate valido: l'osservata non ha poligoni, la geometrica si'.
    # Questo test FALLISCE sulla versione precedente (nessun `kind`).
    nrays = 360
    vrad = np.full((nrays, 267), np.nan)
    vol = volume_from(vrad, np.arange(nrays, dtype="float64"))
    doc = dop.build_coverage(_coverage_inputs(dop._scan_like(vol)),
                             dop.config_for({}))
    kinds = {f["properties"]["kind"] for f in doc["features"]}
    assert "geometric" in kinds
    geo = [f for f in doc["features"] if f["properties"]["kind"] == "geometric"]
    assert geo and all(f["properties"]["max_range_km"] > 250 for f in geo)
    assert all("beam_height_at_max_km" in f["properties"] for f in geo)
    st = dop.coverage_status(doc, 12.48, 41.89)
    assert st["geometric"]["inside"] is True
    assert st["observed"]["present"] is False


def test_coverage_geometric_follows_per_ray_maxrange():
    nrays = 360
    vrad = np.full((nrays, 267), np.nan)
    vol = volume_from(vrad, np.arange(nrays, dtype="float64"))
    vol["maxrange_m"] = np.full(nrays, 100_000.0)
    vol["maxrange_source"] = "where/maxrange"
    doc = dop.build_coverage(_coverage_inputs(dop._scan_like(vol)),
                             dop.config_for({}))
    geo = [f for f in doc["features"] if f["properties"]["kind"] == "geometric"]
    assert geo and geo[0]["properties"]["max_range_km"] == pytest.approx(100.0, abs=0.1)
    assert geo[0]["properties"]["max_range_source"] == "where/maxrange"
    # a 100 km Roma (248 km) e' fuori
    assert dop.coverage_status(doc, 12.48, 41.89)["geometric"]["inside"] is False


def test_coverage_geometric_beam_height_caps_range():
    nrays = 360
    vrad = np.full((nrays, 267), np.nan)
    vol = volume_from(vrad, np.arange(nrays, dtype="float64"), el=1.4)
    doc = dop.build_coverage(_coverage_inputs(dop._scan_like(vol), el=1.4),
                             dop.config_for({}))
    geo = [f for f in doc["features"] if f["properties"]["kind"] == "geometric"]
    assert geo and 200.0 < geo[0]["properties"]["max_range_km"] < 220.0
    assert geo[0]["properties"]["beam_height_at_max_km"] <= 8.05
    assert dop.coverage_status(doc, 12.48, 41.89)["geometric"]["inside"] is False


def test_coverage_has_both_kinds_and_counts():
    nrays = 360
    vrad = np.full((nrays, 267), 5.0)
    vol = volume_from(vrad, np.arange(nrays, dtype="float64"))
    doc = dop.build_coverage(_coverage_inputs(dop._scan_like(vol)),
                             dop.config_for({}))
    kinds = [f["properties"]["kind"] for f in doc["features"]]
    assert "geometric" in kinds and "observed" in kinds
    assert doc["counts"]["geometric"] == kinds.count("geometric")
    assert doc["counts"]["observed"] == kinds.count("observed")
    assert doc["counts"]["polygons"] == len(doc["features"])
    for f in doc["features"]:
        p = f["properties"]
        for k in ("kind", "elevation_deg", "max_range_km",
                  "beam_height_at_max_km"):
            assert k in p
        assert p["kind"] in ("geometric", "observed")


def test_coverage_status_reports_signed_distance():
    nrays = 360
    vrad = np.full((nrays, 267), np.nan)
    vol = volume_from(vrad, np.arange(nrays, dtype="float64"))
    doc = dop.build_coverage(_coverage_inputs(dop._scan_like(vol)),
                             dop.config_for({}))
    st = dop.coverage_status(doc, 12.48, 41.89)
    assert st["geometric"]["inside"] is True
    # 256.32 - 247.93 = 8.39 km dal bordo
    assert 7.0 < st["geometric"]["distance_to_edge_km"] < 9.5
    far = dop.coverage_status(doc, 15.0, 45.0)
    assert far["geometric"]["inside"] is False
    assert far["geometric"]["distance_to_edge_km"] < 0
