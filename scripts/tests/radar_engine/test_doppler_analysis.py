#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test 1.3 — Doppler Analysis Engine + Mesocyclone Detection Engine.

TUTTI i campi qui sono SINTETICI e vivono SOLO in questo file (vincolo
"nessun dato simulato nel sistema operativo"). Nessuna rete, nessuna
scrittura su disco: le funzioni ricevono volumi in memoria.

Copre: velocity couplets, scarto configurazioni incoerenti, qualita'
osservativa, rotazione verticale, 4 stati (con confine morfologia/ambiente),
tracking fra scansioni, completezza dei campi pubblicati.
"""

import datetime as dt
import math

import numpy as np
import pytest

from radar_engine import doppler as dop
from radar_engine import models


# ---------------------------------------------------------------------------
# Helper sintetici (SOLO in memoria)
# ---------------------------------------------------------------------------
def _make_volume(vrad, el=0.6, site="frale", nyq=62.0555,
                 dealias_status="ok", rscale=960.0,
                 valid_time="2026-11-11T20:14:46Z"):
    nrays, nbins = vrad.shape
    az = np.arange(nrays, dtype="float64") * (360.0 / nrays)
    sh, r_km = dop.azimuthal_shear(vrad, az, 0.0, rscale, 1)
    return {
        "site": site, "lat": 42.12971, "lon": 9.49639, "height_m": 63.0,
        "elangle_deg": el, "nrays": nrays, "nbins": nbins,
        "rstart_m": 0.0, "rscale_m": rscale, "az_deg": az,
        "vrad": vrad, "valid_time": valid_time, "nyquist": nyq,
        "nyquist_source": "how/NI", "dealias_status": dealias_status,
        "dealias_method": "region_based", "shear": sh, "range_km": r_km,
        "maxrange_m": np.full(nrays, nbins * rscale),
        "maxrange_source": "grid_geometry",
    }


def _couplet_field(nrays=360, nbins=267, target_ray=90, r0_km=100.0,
                   amp=30.0, half_width_km=8.0):
    """Coppia inbound/outbound sintetica LOCALIZZATA (una sola, no seam).

    Due lobi gaussiani opposti a target_ray+-2 gradi, nello stesso anello di
    range: e' una firma Doppler sintetica di velocita' radiale opposta, non
    un dato reale. Ritorna vrad (float64) con NaN fuori banda di range.
    """
    az = np.arange(nrays, dtype="float64")
    r_km = (np.arange(nbins, dtype="float64") + 0.5) * 0.96
    AZ, R = np.meshgrid(az, r_km, indexing="ij")
    daz = AZ - float(target_ray)
    dr = R - float(r0_km)
    g_az = np.exp(-0.5 * (daz / 1.5) ** 2)
    g_r = np.exp(-0.5 * (dr / 3.0) ** 2)
    vin = -amp * np.exp(-0.5 * ((daz + 2.0) / 1.5) ** 2) * g_r
    vout = amp * np.exp(-0.5 * ((daz - 2.0) / 1.5) ** 2) * g_r
    v = vin + vout
    v[np.abs(dr) > float(half_width_km)] = np.nan
    return v


def _sig(delta=30.0, shear=4.0, depth=None, cont=None, q=0.8,
         null_reason=None, extra=None):
    s = {
        "lat": 42.0, "lon": 9.5, "valid_time": "2026-11-11T20:14:46Z",
        "radar_id": "frale", "delta_velocity_ms": delta,
        "azimuthal_shear_ms_km": shear, "rotation_base_km": 1.0,
        "rotation_top_km": (1.0 + depth) if depth is not None else None,
        "rotation_depth_km": depth, "rotation_strength_ms": delta,
        "vertical_continuity": cont, "null_reason": null_reason,
        "quality": {"quality_score": q},
        "source": "OPERA FR VRADH", "limits": [],
    }
    if extra:
        s.update(extra)
    return s


# ---------------------------------------------------------------------------
# 1. Velocity couplets
# ---------------------------------------------------------------------------
def test_velocity_couplet_found_with_expected_measurements():
    v = _couplet_field()
    vol = _make_volume(v)
    res = dop.velocity_couplets(vol)
    assert res["status"] == "ok" and res["couplets"]
    c = res["couplets"][0]
    assert c["inbound_ms"] < 0 < c["outbound_ms"]
    assert c["delta_velocity_ms"] == pytest.approx(c["outbound_ms"] - c["inbound_ms"], abs=0.01)
    assert c["delta_velocity_ms"] >= 15.0
    assert len(res["couplets"]) == 1       # un solo fenomeno sintetico
    assert 0.2 < c["couplet_distance_km"] <= 12.0
    assert c["azimuthal_shear_ms_km"] == pytest.approx(
        c["delta_velocity_ms"] / c["couplet_distance_km"], rel=0.02)
    assert 0.6 <= c["signal_continuity"] <= 1.0
    assert c["support_gates"] >= 3
    assert c["sign"] in (-1, 0, 1)
    assert c["radar_id"] == "frale"
    assert c["valid_time"] == vol["valid_time"]


def test_velocity_couplet_fields_have_source_time_quality_limits():
    v = _couplet_field()
    res = dop.velocity_couplets(_make_volume(v))
    for c in res["couplets"]:
        for k in ("source", "reference", "limits", "valid_time"):
            assert k in c
        assert isinstance(c["quality"], dict)
        assert c["quality"]["quality_score"] == pytest.approx(
            c["quality"]["quality_score"])
        assert "radar_id" in c["quality"]
        assert "nyquist_velocity_ms" in c["quality"]


def test_couplet_rejected_too_close_to_radar():
    # a 15 km lo shear azimutale misura il rumore lungo il raggio: nessuna
    # coppia va emessa anche se il campo e' identico.
    v = _couplet_field(r0_km=15.0, half_width_km=4.0)
    vol = _make_volume(v)
    res = dop.velocity_couplets(vol)
    assert res["couplets"] == []


def test_couplet_rejected_when_nyquist_missing_no_invented_number():
    v = _couplet_field()
    vol = _make_volume(v, nyq=None, dealias_status="no_nyquist")
    res = dop.velocity_couplets(vol)
    assert res["status"] == "no_nyquist"
    assert res["null_reason"] == "nyquist_missing"
    assert res["couplets"] == []
    assert vol["vrad"].size  # il campo grezzo resta, ma nessun numero pubblicato


def test_couplet_keeps_valid_gates_when_dealias_partially_unresolved():
    # NI c'e': i gate non risolvibili sono NaN, gli altri sono dealiased e
    # utilizzabili. Non si butta via l'intero volume per un saltino locale.
    v = _couplet_field()
    vol = _make_volume(v, dealias_status="unresolved")
    vol["unresolved_gates"] = 50
    res = dop.velocity_couplets(vol)
    assert res["status"] == "ok" and res["couplets"]
    assert any("dealias_unresolved_gates:50" in n for n in res["notes"])
    assert all(c["dealias_status"] == "unresolved" for c in res["couplets"])
    # la qualita' riflette la frazione di gate persi (non 0 arbitrario)
    q = res["couplets"][0]["quality"]["quality_score"]
    assert 0.0 < q < 1.0


def test_no_couplet_when_all_gates_unresolved():
    vol = _make_volume(np.full((360, 267), np.nan),
                       dealias_status="unresolved", nyq=60.0)
    res = dop.velocity_couplets(vol)
    assert res["couplets"] == []


def test_couplet_rejected_single_gate_isolated():
    # due gate isolati con segno opposto, senza supporto: non e' una coppia.
    v = np.full((360, 267), np.nan)
    r0 = 100.0
    j = int(r0 / 0.96)
    v[90, j] = -30.0
    v[91, j] = 30.0
    res = dop.velocity_couplets(_make_volume(v))
    assert res["couplets"] == []


def test_couplet_rejected_when_signal_not_continuous():
    # massimi opposti separati da un buco di NaN: continuita' sotto soglia.
    v = np.full((360, 267), np.nan)
    j = int(100.0 / 0.96)
    v[88:90, j - 1:j + 2] = -30.0
    v[93:95, j - 1:j + 2] = 30.0
    # buco di 3 raggi fra i due blocchi
    res = dop.velocity_couplets(_make_volume(v))
    assert all(c["signal_continuity"] >= 0.6 for c in res["couplets"])


def test_couplet_persistence_from_previous_scan():
    v = _couplet_field()
    cur = dop.velocity_couplets(_make_volume(v),
                                previous=_make_volume(v))
    assert cur["couplets"]
    assert all(c["persistence_scans"] == 2 for c in cur["couplets"])
    none = dop.velocity_couplets(_make_volume(v))
    assert all(c["persistence_scans"] == 1 for c in none["couplets"])


# ---------------------------------------------------------------------------
# 2. Qualita' osservativa
# ---------------------------------------------------------------------------
def test_observation_quality_is_geometry_dependent():
    vol = _make_volume(_couplet_field())
    q30 = dop.observation_quality(vol, 30.0)
    q250 = dop.observation_quality(vol, 250.0)
    assert q30["radar_id"] == "frale"
    assert q30["radar_distance_km"] == 30.0
    assert q30["beam_height_km"] < q250["beam_height_km"]
    assert q30["beam_width_km"] < q250["beam_width_km"]
    assert q30["quality_score"] > q250["quality_score"]
    assert all(0.0 <= q["quality_score"] <= 1.0 for q in (q30, q250))


def test_observation_quality_beam_height_uses_4_3_earth():
    vol = _make_volume(_couplet_field())
    q = dop.observation_quality(vol, 248.1)
    assert q["beam_height_km"] == pytest.approx(
        dop.beam_height_km(248.1, 0.6, 63.0), abs=0.01)
    assert q["beam_width_km"] == pytest.approx(
        2.0 * 248.1 * math.tan(math.radians(0.5)), rel=0.01)


def test_observation_quality_data_age_and_fields():
    vol = _make_volume(_couplet_field())
    as_of = dt.datetime(2026, 11, 11, 20, 29, 46, tzinfo=dt.timezone.utc)
    q = dop.observation_quality(vol, 100.0, as_of=as_of)
    assert q["data_age_min"] == pytest.approx(15.0, abs=0.01)
    for k in ("radar_id", "radar_distance_km", "beam_height_km", "beam_width_km",
              "elevation_deg", "nyquist_velocity_ms", "data_age_min",
              "quality_score", "source", "valid_time", "reference", "limits"):
        assert k in q
    assert q["nyquist_velocity_ms"] == pytest.approx(62.0555)


def test_quality_score_zero_when_nyquist_unresolved():
    vol = _make_volume(_couplet_field(), nyq=None, dealias_status="no_nyquist")
    q = dop.observation_quality(vol, 100.0)
    assert q["quality_score"] == 0.0
    assert q["nyquist_velocity_ms"] is None


# ---------------------------------------------------------------------------
# 3. Rotazione verticale
# ---------------------------------------------------------------------------
def test_vertical_rotation_measures_base_top_depth_strength_continuity():
    v = _couplet_field()
    low = _make_volume(v, el=0.6)
    high = _make_volume(v, el=2.0)
    res = dop.vertical_rotation([low, high])
    assert res["status"] == "ok" and len(res["signatures"]) == 1
    s = res["signatures"][0]
    assert s["rotation_base_km"] is not None
    assert s["rotation_top_km"] > s["rotation_base_km"]
    assert s["rotation_depth_km"] == pytest.approx(
        s["rotation_top_km"] - s["rotation_base_km"], abs=0.01)
    assert s["rotation_depth_km"] > 1.5
    assert s["rotation_strength_ms"] >= 15.0
    assert s["vertical_continuity"] == pytest.approx(1.0)
    assert s["null_reason"] is None


def test_vertical_rotation_single_elevation_is_null_not_depth():
    v = _couplet_field()
    res = dop.vertical_rotation([_make_volume(v, el=0.6)])
    assert res["signatures"]
    s = res["signatures"][0]
    assert s["rotation_depth_km"] is None
    assert s["vertical_null_reason"] == "insufficient_vertical_elevations"
    assert s["null_reason"] is None   # la coppia 2D resta una misura reale


def test_vertical_rotation_rejects_opposite_sign_across_elevations():
    v = _couplet_field(target_ray=90)
    low = _make_volume(v, el=0.6)
    # segno opposto: la coppia cambia orientamento in quota -> non e' la
    # stessa struttura rotante, non va associata.
    high = _make_volume(-v, el=2.0)
    res = dop.vertical_rotation([low, high])
    s = res["signatures"][0]
    assert s["n_elevations"] == 1
    assert s["rotation_depth_km"] is None


def test_vertical_rotation_no_couplets_declares_reason():
    v = np.full((360, 267), 0.0)
    res = dop.vertical_rotation([_make_volume(v, el=0.6)])
    assert res["status"] == "no_couplets"
    assert res["null_reason"]


# ---------------------------------------------------------------------------
# 4. Quattro stati + confine semantico
# ---------------------------------------------------------------------------
def test_four_states_all_reachable():
    assert dop.classify_rotation(_sig(null_reason="nyquist_missing"))[
        "classification"] == models.ROTATION_NO_ROTATION
    assert dop.classify_rotation(_sig(depth=None, cont=None),
                                 persistence_scans=1)[
        "classification"] == models.ROTATION_SUSPECTED
    assert dop.classify_rotation(_sig(depth=2.0, cont=1.0, q=0.8),
                                 persistence_scans=2)[
        "classification"] == models.ROTATION_DETECTED
    assert dop.classify_rotation(_sig(depth=2.0, cont=1.0, q=0.8),
                                 persistence_scans=3, cell_coherent=True)[
        "classification"] == models.ROTATION_MESOCYCLONE_CANDIDATE


def test_meso_candidate_needs_all_four_requirements():
    base = dict(depth=2.0, cont=1.0, q=0.8)
    assert dop.classify_rotation(_sig(**base), persistence_scans=3,
                                 cell_coherent=False)[
        "classification"] == models.ROTATION_DETECTED
    assert dop.classify_rotation(_sig(depth=0.5, cont=1.0, q=0.8),
                                 persistence_scans=3, cell_coherent=True)[
        "classification"] == models.ROTATION_DETECTED
    assert dop.classify_rotation(_sig(depth=2.0, cont=0.3, q=0.8),
                                 persistence_scans=3, cell_coherent=True)[
        "classification"] == models.ROTATION_DETECTED
    assert dop.classify_rotation(_sig(depth=2.0, cont=1.0, q=0.8),
                                 persistence_scans=2, cell_coherent=True)[
        "classification"] == models.ROTATION_DETECTED


def test_never_tornado_or_confirmed():
    # Il vincolo riguarda le ETICHETTE di stato: nei limiti e' corretto
    # scrivere che NON si dimostra un tornado (onesta', non classificazione).
    for kwargs in ({}, {"depth": 2.0, "cont": 1.0}):
        out = dop.classify_rotation(_sig(**kwargs), persistence_scans=4,
                                    cell_coherent=True)
        label = out["classification"]
        assert label in models.ROTATION_STATES
        assert "tornado" not in label.lower()
        assert "conferm" not in label.lower()


def test_environmental_and_morphology_fields_do_not_change_state():
    # CAPE/SRH/DLS/SCP e morfologia CAPPI NON sono ingressi della
    # classificazione: aggiungerli non puo' alzare lo stato.
    clean = _sig(depth=None, cont=None)
    dirty = _sig(depth=None, cont=None,
                 extra={"cape": 5000, "srh": 400, "dls": 40, "scp": 10,
                        "bwer": True, "overhang": True, "morphology_organization": "strong"})
    assert dop.classify_rotation(clean, persistence_scans=1)[
        "classification"] == dop.classify_rotation(dirty, persistence_scans=1)[
        "classification"] == models.ROTATION_SUSPECTED


def test_single_elevation_couplet_is_suspected_not_no_rotation():
    # Una coppia 2D e' una misura Doppler reale: almeno SUSPECTED. Solo i
    # campi VERTICALI restano null (con vertical_null_reason), non lo stato.
    v = _couplet_field()
    out = dop.analyze_volume([_make_volume(v, el=0.6)])
    assert out["state_counts"]["ROTATION_SUSPECTED"] >= 1
    assert out["state_counts"]["NO_ROTATION"] == 0
    s = out["signatures"][0]
    assert s["rotation_depth_km"] is None
    assert s["vertical_null_reason"] == "insufficient_vertical_elevations"


def test_analyze_volume_boundary_note_and_counts():
    v = _couplet_field()
    out = dop.analyze_volume([_make_volume(v, el=0.6),
                              _make_volume(v, el=2.0)])
    assert "morfologia" in out["boundary_note"].lower()
    assert set(out["state_counts"]) == set(models.ROTATION_STATES)
    assert out["couplets_total"] >= 1
    assert out["valid_time"] is not None
    for s in out["signatures"]:
        assert s["classification"] in models.ROTATION_STATES
        assert "rotation_measurable" in s
        assert "limits" in s and "source" in s


# ---------------------------------------------------------------------------
# 5. Tracking
# ---------------------------------------------------------------------------
def test_tracking_appearance_intensification_displacement():
    t0 = "2026-11-11T20:00:00Z"
    scans = [
        {"valid_time": t0, "signatures": [_sig(delta=20.0, depth=2.0, cont=1.0)]},
        {"valid_time": "2026-11-11T20:10:00Z",
         "signatures": [_sig(delta=32.0, depth=2.0, cont=1.0,
                             extra={"lat": 42.12})]},
    ]
    out = dop.track_rotations(scans)
    assert len(out["tracks"]) == 1
    tr = out["tracks"][0]
    assert tr["persistence"] == 2
    assert models.ROTATION_EV_APPEARANCE in tr["events"]
    assert models.ROTATION_EV_INTENSIFICATION in tr["events"]
    assert any(p["distance_from_prev_km"] is not None for p in tr["points"])
    assert tr["max_delta_velocity_ms"] == pytest.approx(32.0)
    assert tr["meso_id"].startswith("MESO-")


def test_tracking_dissipation_and_new_track():
    scans = [
        {"valid_time": "2026-11-11T20:00:00Z",
         "signatures": [_sig(delta=25.0, depth=2.0, cont=1.0)]},
        {"valid_time": "2026-11-11T20:10:00Z",
         "signatures": [_sig(delta=25.0, depth=2.0, cont=1.0)]},
        {"valid_time": "2026-11-11T21:00:00Z",
         "signatures": [_sig(delta=25.0, depth=2.0, cont=1.0,
                             extra={"lat": 44.0, "lon": 12.0})]},
    ]
    out = dop.track_rotations(scans)
    assert len(out["tracks"]) == 2
    assert any(models.ROTATION_EV_DISSIPATION in t["events"] for t in out["tracks"])
    assert any(models.ROTATION_EV_APPEARANCE in t["events"] for t in out["tracks"])


def test_tracking_collects_parent_cell_ids_and_states():
    scans = [
        {"valid_time": "2026-11-11T20:00:00Z",
         "signatures": [_sig(depth=2.0, cont=1.0,
                             extra={"parent_cell_id": "CELL-7",
                                    "classification": models.ROTATION_DETECTED})]},
        {"valid_time": "2026-11-11T20:05:00Z",
         "signatures": [_sig(depth=2.0, cont=1.0,
                             extra={"parent_cell_id": "CELL-7",
                                    "classification": models.ROTATION_DETECTED})]},
    ]
    tr = dop.track_rotations(scans)["tracks"][0]
    assert tr["parent_cell_ids"] == ["CELL-7"]
    assert tr["classification"] == models.ROTATION_DETECTED
    assert models.ROTATION_EV_DISSIPATION not in tr["events"]


def test_tracking_one_point_per_scan_not_inflated_persistence():
    # Due firme vicine nella STESSA scansione non devono diventare una track
    # con persistence=2: la persistenza conta le scansioni, non i punti.
    scans = [{"valid_time": "2026-11-11T20:00:00Z",
              "signatures": [_sig(delta=25.0), _sig(delta=26.0)]}]
    out = dop.track_rotations(scans)
    assert len(out["tracks"]) == 2
    assert all(t["persistence"] == 1 for t in out["tracks"])


def test_tracking_promotes_to_meso_candidate_with_all_requirements():
    # La persistenza >= 3 scansioni e' della TRACCIA: solo qui si puo'
    # raggiungere MESOCYCLONE_CANDIDATE (coerenza cella inclusa).
    scans = []
    for i in range(3):
        scans.append({
            "valid_time": "2026-11-11T20:{:02d}:00Z".format(i * 5),
            "signatures": [_sig(depth=2.0, cont=1.0, q=0.8,
                                extra={"classification": models.ROTATION_DETECTED,
                                       "parent_cell_id": "CELL-9"})]})
    tr = dop.track_rotations(scans)["tracks"][0]
    assert tr["persistence"] == 3
    assert tr["cell_coherent"] is True
    assert tr["max_vertical_continuity"] == pytest.approx(1.0)
    assert tr["classification"] == models.ROTATION_MESOCYCLONE_CANDIDATE


def test_track_fields_complete():
    out = dop.track_rotations([{"valid_time": "2026-11-11T20:00:00Z",
                                "signatures": [_sig()]}])
    tr = out["tracks"][0]
    for k in ("meso_id", "first_detection", "last_detection", "persistence",
              "classification", "parent_cell_ids", "events", "points",
              "source", "reference", "limits",
              "lat", "lon", "rotation_diameter"):
        assert k in tr
    assert set(out["counts"]) == set(models.ROTATION_STATES)


# ---------------------------------------------------------------------------
# 6. Nessun dato simulato nel sistema operativo
# ---------------------------------------------------------------------------
def test_engines_do_not_write_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    v = _couplet_field()
    vol = _make_volume(v)
    dop.velocity_couplets(vol)
    dop.vertical_rotation([vol])
    dop.analyze_volume([vol])
    dop.track_rotations([{"valid_time": vol["valid_time"],
                          "signatures": [_sig()]}])
    assert list(tmp_path.iterdir()) == []


def test_models_rotation_states_and_events_exist():
    assert len(models.ROTATION_STATES) == 4
    assert models.ROTATION_MESOCYCLONE_CANDIDATE in models.ROTATION_STATES
    assert set(models.ROTATION_EVENTS) >= {
        models.ROTATION_EV_APPEARANCE, models.ROTATION_EV_INTENSIFICATION,
        models.ROTATION_EV_DISPLACEMENT, models.ROTATION_EV_MERGE,
        models.ROTATION_EV_DISSIPATION}
    sig = models.RotationSignature("RS-1", 42.0, 9.5,
                                   "2026-11-11T20:14:46Z", "frale",
                                   30.0, 4.0, 1.0, 3.0, 2.0, 30.0, 1.0)
    d = sig.to_dict()
    assert d["rotation_measurable"] is True
    assert d["classification"] is None
