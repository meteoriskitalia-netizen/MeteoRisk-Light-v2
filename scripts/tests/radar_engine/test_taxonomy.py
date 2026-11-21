#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test della tassonomia multi-assie additiva (schema meteorisk.taxonomy/1).

Tutti i record qui sono SINTETICI e dichiarati tali: i test non toccano dati
radar reali se non l'ultimo, che legge il payload gia' pubblicato in
data/radar/convective_structures.json (sola lettura, nessuna scrittura).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar_engine import config as cfg_mod
from radar_engine import output as out_mod
from radar_engine import taxonomy as tax


def _ring(west, east, south, north):
    return {"type": "Polygon", "coordinates": [[
        [west, south], [east, south], [east, north], [west, north],
        [west, south],
    ]]}


def _record(**overrides):
    """Record sintetico compatto (1 cella compatta, 3 frame)."""
    rec = {
        "structure_id": "CS-TEST-1",
        "system_key": "storm_object:1",
        "time_window": "2026-11-21T00",
        "first_seen": "2026-11-21T00:05:00Z",
        "last_seen": "2026-11-21T00:15:00Z",
        "n_points": 3,
        "cell_count": 1,
        "area_km2": 120.0,
        "intensity": {"max_dbz": 45.0, "mean_dbz": 38.0, "delta_dbz": 3.0},
        "motion": {"area_growth_pct": 20.0, "direction_toward_deg": 90.0,
                   "duration_min": 15.0},
        "outline": _ring(11.95, 12.05, 41.95, 42.05),
        "confidence": "high",
        "soi_v2": 88,
        "nwp_environment_context": {"value": {"cape": 4000}},
        "env": 99.0,
        "bunkers": {"rm_ms": {"value": {"speed_ms": 30.0}}},
    }
    rec.update(overrides)
    return rec


def _line_record(length_deg=1.4, max_dbz=45.0, n_points=4, area_km2=2600.0):
    lat = 42.0
    return _record(
        outline=_ring(12.0, 12.0 + length_deg, lat - 0.005, lat + 0.005),
        max_dbz=max_dbz, area_km2=area_km2, n_points=n_points,
        intensity={"max_dbz": max_dbz, "mean_dbz": 41.0, "delta_dbz": 1.0},
    )


def _two_frames(**extra):
    obs = []
    for i in (1, 2):
        o = {
            "valid_time": "2026-11-21T00:%02d:00Z" % (i * 5),
            "max_dbz": 45.0, "mean_dbz": 38.0, "area_km2": 300.0,
            "persistence_frames": 3, "cell_count": 1,
            "length_km": 30.0, "aspect_ratio": 1.2,
            "delta_dbz": 3.0, "area_growth_pct": 20.0,
        }
        o.update(extra)
        obs.append(o)
    return obs


def test_01_schema_and_vocabularies():
    assert tax.SCHEMA == "meteorisk.taxonomy/1"
    assert tax.CANDIDATE == "candidate"
    assert "unknown" in tax.ECHO_REGIMES
    assert "linear_system_candidate" in tax.ORGANIZATION_MODES
    assert "mcs_candidate" in tax.SYSTEM_SCALES
    assert set(tax.EVOLUTION_TAG_NAMES).issubset(
        {"intensification_candidate", "dissipation_candidate", "steady_candidate"})


def test_02_field_null_without_reason_raises():
    with pytest.raises(ValueError):
        tax.field(None, reference="x", null_reason=None)
    f = tax.field(None, null_reason="no_datum", reference="r")
    assert f["value"] is None and f["status"] == "unknown"
    assert f["reference"] == "r"


def test_03_field_support_bounded_and_confidence_capped():
    f = tax.field("x", rule_support=9.9, confidence="low", reference="r")
    assert f["rule_support"] == 1.0
    assert f["confidence"] == "low"
    with pytest.raises(ValueError):
        tax.field("x", confidence="high", reference="r")
    assert "probabilita'" in f["rule_support_basis"]


def test_04_geometry_from_outline_km():
    g = tax._geometry(_record())
    assert 8 < g["length_km"] < 14
    assert g["aspect_ratio"] < 2.0
    gl = tax._geometry(_line_record())
    assert gl["length_km"] >= 100.0
    assert gl["aspect_ratio"] >= 3.0


def test_05_echo_regime_convective_and_mixed():
    conv, why, _ = tax.raw_echo_regime(tax.observation_from_record(_record()))
    assert conv == "convective_like" and why is None
    mixed, _, _ = tax.raw_echo_regime(
        tax.observation_from_record(_record(
            intensity={"max_dbz": 45.0, "mean_dbz": 28.0, "delta_dbz": 1.0})))
    assert mixed == "mixed"


def test_06_echo_regime_stratiform_no_echo_unknown():
    strat, _, _ = tax.raw_echo_regime(tax.observation_from_record(_record(
        intensity={"max_dbz": 28.0, "mean_dbz": 25.0, "delta_dbz": 0.0})))
    assert strat == "stratiform_like"
    no, _, _ = tax.raw_echo_regime(tax.observation_from_record(_record(
        intensity={"max_dbz": 5.0, "mean_dbz": 4.0, "delta_dbz": 0.0})))
    assert no == "no_echo"
    unk, why, _ = tax.raw_echo_regime(tax.observation_from_record(_record(
        intensity={"max_dbz": 45.0}, area_km2=10.0)))
    assert unk is None and "area" in why


def test_07_linear_gate_75km_ratio3_persistence3():
    mode, why, _ = tax.raw_organization_mode(
        tax.observation_from_record(_line_record(length_deg=0.95)))
    assert mode == "linear_system_candidate" and why is None
    below, _, _ = tax.raw_organization_mode(
        tax.observation_from_record(_line_record(length_deg=0.80)))
    assert below != "linear_system_candidate"
    short, _, _ = tax.raw_organization_mode(
        tax.observation_from_record(_line_record(length_deg=0.95, n_points=2)))
    assert short != "linear_system_candidate"
    assert "SCELTA INGEGNERISTICA" in tax.REFERENCE["linear_system"]
    assert "NON validata" in tax.REFERENCE["linear_system"]


def test_08_isolated_cluster_and_embedded():
    iso, _, _ = tax.raw_organization_mode(tax.observation_from_record(_record()))
    assert iso == "isolated_cell_candidate"
    cluster, _, _ = tax.raw_organization_mode(
        tax.observation_from_record(_record(cell_count=3, area_km2=400.0)))
    assert cluster == "cell_cluster"
    emb, _, _ = tax.raw_organization_mode(
        tax.observation_from_record(_record(
            intensity={"max_dbz": 45.0, "mean_dbz": 28.0, "delta_dbz": 0.0})))
    assert emb == "embedded_convection"


def test_09_system_scale_mcs_gate_100km():
    scale, _, _ = tax.raw_system_scale(
        tax.observation_from_record(_line_record(length_deg=1.4)))
    assert scale == "mcs_candidate"
    cell, _, _ = tax.raw_system_scale(tax.observation_from_record(_record()))
    assert cell == "cell_candidate"
    assert "SCELTA INGEGNERISTICA" in tax.REFERENCE["mcs"]


def test_10_bow_requires_two_frames_curvature():
    one, _, _ = tax.evaluate_tags([_two_frames(bow_score=0.9)[0]])
    assert "bow_echo_candidate" not in [d["tag"] for d in one]
    two, _, _ = tax.evaluate_tags(_two_frames(bow_score=0.9))
    bow = [d for d in two if d["tag"] == "bow_echo_candidate"]
    assert len(bow) == 1
    assert "NON inferisce raffiche" in bow[0]["reference"]
    assert "danno" in bow[0]["reference"]


def test_11_v_notch_and_radar_v_shape_are_distinct():
    tags, _, ne = tax.evaluate_tags(_two_frames(notch_depth_km=8.0))
    names = [d["tag"] for d in tags]
    assert "v_notch_reflectivity" in names
    assert "radar_v_shape" not in names
    vs, _, _ = tax.evaluate_tags(_two_frames(
        upstream_regeneration_frames=3, vs_anchor_frames=2,
        echo_6km_dbz=46.0))
    names2 = [d["tag"] for d in vs]
    assert "radar_v_shape" in names2
    assert "v_notch_reflectivity" not in names2
    assert any(d["tag"] == "enhanced_V_IR" for d in ne)


def test_12_enhanced_ir_requires_valid_image():
    _, _, ne = tax.evaluate_tags(_two_frames())
    ir = [d for d in ne if d["tag"] == "enhanced_V_IR"]
    assert ir and ir[0]["null_reason"] == "ir_image_not_available"
    tags, _, _ = tax.evaluate_tags(_two_frames(
        ir_image_valid=True, enhanced_v_signature=True))
    assert "enhanced_V_IR" in [d["tag"] for d in tags]
    assert "NON calcolabile da VMI/CAPPI" in tax.REFERENCE["enhanced_V_IR"]


def test_13_hysteresis_two_consecutive_frames():
    h = tax.Hysteresis(2)
    assert h.update("x", "convective_like", "t1")[0] == "unknown"
    v, why, det = h.update("x", "convective_like", "t2")
    assert v == "convective_like" and why is None
    assert det["hysteresis_frames"] == 2 and det["valid_time"] == "t2"
    v2, _, _ = h.update("x", "stratiform_like", "t3")
    assert v2 == "unknown"
    assert h.update("x", "stratiform_like", "t4")[0] == "stratiform_like"


def test_14_hysteresis_drops_class_without_valid_datum():
    h = tax.Hysteresis(2)
    h.update("x", "convective_like", "t1")
    h.update("x", "convective_like", "t2")
    v, why, det = h.update("x", None, "t3")
    assert v == "unknown"
    assert why == "no_valid_datum_after_last_observation"
    assert det["datum_valid"] is False and det["valid_time"] == "t3"
    # nessuna conservazione:serve di nuovo 2 frame dopo il buco
    h.update("x", "convective_like", "t4")
    assert h.update("x", "convective_like", "t5")[0] == "convective_like"


def test_15_single_observation_stays_unknown():
    recs, summary = tax.annotate_records([_record()])
    blk = recs[0]["taxonomy"]
    assert blk["echo_regime"]["value"] == "unknown"
    assert blk["echo_regime"]["null_reason"] == "hysteresis_requires_2_consecutive_frames"
    assert summary["unknown_rate_echo_regime"] == 1.0
    assert summary["confirmed_count"] == 0


def test_16_two_frame_evidence_promotes_to_candidate_only():
    recs, summary = tax.annotate_records(
        [_record()], frame_observations={"CS-TEST-1": _two_frames()})
    blk = recs[0]["taxonomy"]
    assert blk["echo_regime"]["value"] == "convective_like"
    assert blk["echo_regime"]["status"] == "candidate"
    assert blk["confirmed"] is False
    assert blk["enhanced_V_IR"]["value"] is None
    assert blk["enhanced_V_IR"]["null_reason"] == "ir_image_not_available"
    assert summary["unknown_rate_echo_regime"] == 0.0


def test_17_nwp_never_changes_taxonomy():
    a = _record()
    b = copy.deepcopy(a)
    b["env"] = 100.0
    b["soi_v2"] = 1
    b["confidence"] = "very_low"
    b["nwp_environment_context"] = {"value": {"cape": 99999}}
    b["bunkers"] = {"rm_ms": {"value": {"speed_ms": 99}}}
    ta = tax.annotate_records([a], frame_observations={"CS-TEST-1": _two_frames()})[0][0]
    tb = tax.annotate_records([b], frame_observations={"CS-TEST-1": _two_frames()})[0][0]
    assert ta["taxonomy"] == tb["taxonomy"]
    assert ta["taxonomy"]["nwp_influence"] == "none"
    assert all("nwp" not in k.lower() for k in ta["taxonomy"]["echo_regime"]
               if k != "nwp_influence")


def test_18_rule_support_not_probability():
    recs, summary = tax.annotate_records(
        [_record()], frame_observations={"CS-TEST-1": _two_frames()})
    blk = recs[0]["taxonomy"]
    assert blk["rule_support_is_probability"] is False
    assert summary["rule_support_is_probability"] is False
    for axis in tax.LOGICAL_VARIABLES:
        f = blk[axis]
        assert 0.0 <= f["rule_support"] <= 1.0
        assert "probabilita'" in f["rule_support_basis"]
        assert f["confidence"] in ("low", "medium")
    assert not any(k == "probability" for k in blk)


def test_19_config_default_off_and_attach_wiring():
    assert cfg_mod.CONFIG["taxonomy"]["enabled"] is False
    assert "SCELTA INGEGNERISTICA" in cfg_mod.CONFIG["taxonomy"]["reference"]
    rec = _record()
    bundle = SimpleNamespace(convective_structures=[rec])
    assert tax.attach(bundle, cfg_mod.CONFIG) is None
    assert "taxonomy" not in rec
    out = tax.attach(bundle, {"taxonomy": {"enabled": True}})
    assert out is not None
    assert rec["taxonomy"]["schema"] == tax.SCHEMA
    assert bundle.taxonomy_summary["structures_total"] == 1


def _bundle(rec):
    """Bundle minimo per gli output (nessun dato reale, solo test)."""
    return SimpleNamespace(
        convective_structures=[rec], generated_at_iso="2026-11-21T00:20:00Z",
        radar_timestamp_iso="2026-11-21T00:15:00Z", status="ok", engine={},
        source="test", phase2=None, structure_tracks_evaluated=1,
        warnings=[], advanced_summary={}, cells_by_frame=[], tracks=[],
        storm_objects_by_frame=[], storm_tracks=[], frames=[],
        frame_slots_iso=None, radar_timestamp_ms=0, data_latency_minutes=0.0,
    )


def test_20_output_publishes_only_when_present():
    rec = _record()
    bundle = _bundle(rec)
    p1 = out_mod.build_convective_structures(bundle, None)
    assert "taxonomy" not in p1
    tax.attach(bundle, {"taxonomy": {"enabled": True}})
    p2 = out_mod.build_convective_structures(bundle, None)
    assert p2["taxonomy"]["schema"] == tax.SCHEMA
    assert p2["structures"][0]["taxonomy"]["confirmed"] is False
    l2 = out_mod.build_latest(bundle, None, previous=None)
    assert l2["taxonomy"]["schema"] == tax.SCHEMA


def test_21_real_run_distribution_and_no_confirmed():
    path = (Path(__file__).resolve().parents[1] / "fixtures"
            / "real_1446_convective_structures.json")
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    recs = copy.deepcopy(payload.get("structures") or [])
    assert recs, "il payload reale deve contenere almeno una struttura"
    _, summary = tax.annotate_records(recs)
    n = len(recs)
    assert sum(summary["echo_regime_counts"].values()) == n
    assert sum(summary["organization_mode_counts"].values()) == n
    assert sum(summary["system_scale_counts"].values()) == n
    assert summary["confirmed_count"] == 0
    assert summary["unknown_rate_echo_regime"] == 1.0  # nessuna evidenza multi-frame
    assert all(r["taxonomy"]["confirmed"] is False for r in recs)
    for r in recs:
        b = r["taxonomy"]
        assert b["echo_regime"]["value"] in tax.ECHO_REGIMES + (tax.UNKNOWN,)
        assert (b["organization_mode"]["value"]
                in tax.ORGANIZATION_MODES + (tax.UNKNOWN,))
        assert b["system_scale"]["value"] in tax.SYSTEM_SCALES
    # diagnostica (NON pubblicata come classe): i gate raw danno candidati
    raw = {}
    for r in recs:
        v, _, _ = tax.raw_echo_regime(tax.observation_from_record(r))
        raw[v] = raw.get(v, 0) + 1
    assert sum(raw.values()) >= 1


def test_22_validate_outputs_accepts_taxonomy_block(tmp_path):
    rec = _record()
    bundle = _bundle(rec)
    tax.attach(bundle, {"taxonomy": {"enabled": True}})
    texts = {
        "latest.json": out_mod.build_latest(bundle, None, previous=None),
        "storms.geojson": out_mod.build_storms_geojson(bundle),
        "tracks.json": out_mod.build_tracks(bundle),
        "storm_objects.geojson": out_mod.build_storm_objects_geojson(bundle),
        "storm_tracks.json": out_mod.build_storm_tracks(bundle),
        "convective_structures.json": out_mod.build_convective_structures(
            bundle, None),
    }
    for name, payload in texts.items():
        (tmp_path / name).write_text(
            json.dumps(payload, default=out_mod._json_default), encoding="utf-8")
    assert out_mod.validate_outputs(str(tmp_path)) == []


def test_23_existing_record_fields_are_untouched():
    rec = _record()
    before = copy.deepcopy(rec)
    tax.attach(_bundle(rec), {"taxonomy": {"enabled": True}})
    for key, value in before.items():
        assert rec[key] == value, key
    assert set(rec) - set(before) == {"taxonomy"}


def test_24_validate_rejects_confirmed_flag(tmp_path):
    rec = _record()
    bundle = _bundle(rec)
    tax.attach(bundle, {"taxonomy": {"enabled": True}})
    rec["taxonomy"]["confirmed"] = True  # corruzione deliberata del test
    texts = {
        "latest.json": out_mod.build_latest(bundle, None, previous=None),
        "storms.geojson": out_mod.build_storms_geojson(bundle),
        "tracks.json": out_mod.build_tracks(bundle),
        "storm_objects.geojson": out_mod.build_storm_objects_geojson(bundle),
        "storm_tracks.json": out_mod.build_storm_tracks(bundle),
        "convective_structures.json": out_mod.build_convective_structures(
            bundle, None),
    }
    for name, payload in texts.items():
        (tmp_path / name).write_text(
            json.dumps(payload, default=out_mod._json_default), encoding="utf-8")
    errors = out_mod.validate_outputs(str(tmp_path))
    assert any("taxonomy confirmed flag" in e for e in errors)
