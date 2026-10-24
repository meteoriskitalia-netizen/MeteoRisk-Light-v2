#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dello stack severo (1.2.4.17): S1 ambiente, S2 grandine, S3 OT.

Casi coperti per contratto:
  - S2: MESH SOLO con >= 4 livelli CAPPI; con 2 livelli null +
    `mesh_unavailable_reason="cappi_levels<4"`; VIL density con la soglia
    citata di Amburn & Wolf 1997;
  - S1: shear 0-6 km e SRH 0-3 km calcolati solo quando il profilo lo
    permette, altrimenti null con motivo; nessuna estrapolazione;
  - S3: booleano + confidence con fonte e timestamp, riuso di satellite_ot;
  - ogni campo null ha `null_reason` (un None senza motivo solleva).
"""

import math

import numpy as np
import pytest

from radar_engine import severe_stack as ss


# ---------------------------------------------------------------------------
# Contratto dei campi
# ---------------------------------------------------------------------------
def test_field_null_requires_reason():
    with pytest.raises(ValueError):
        ss.field(None, "src", "ref")
    out = ss.field(None, "src", "ref", null_reason="missing_x")
    assert out == {"value": None, "null_reason": "missing_x",
                   "source": "src", "reference": "ref"}
    assert ss.field(3.0, "src", "ref")["null_reason"] is None


def test_field_non_finite_becomes_null_with_reason():
    out = ss.field(float("nan"), "src", "ref")
    assert out["value"] is None
    assert out["null_reason"] == "not_finite"


# ---------------------------------------------------------------------------
# S2 — VIL density (Amburn & Wolf 1997)
# ---------------------------------------------------------------------------
def test_vil_density_conversion_and_threshold():
    # 35 kg/m2 su 10 km di spessore = 3.5 g/m3 (la soglia citata)
    assert ss.vil_density(35.0, 10_000.0) == pytest.approx(3.5)
    assert ss.vil_density(35.0, 0.0) is None
    assert ss.vil_density(None, 10_000.0) is None
    assert ss.vil_density(35.0, -5.0) is None


def test_vil_density_threshold_reference_is_bibliographic():
    hail = ss.hail_layer(vil_grid=np.full((3, 3), 35.0),
                         etm_m_grid=np.full((3, 3), 10_000.0))
    vd = hail["vil_density_g_m3"]
    assert vd["value"] == pytest.approx(3.5)
    assert vd["above_threshold"] is True
    assert "Amburn" in vd["reference"]
    assert "NON validato localmente" in vd["reference"]
    assert hail["threshold_ref"] == "Amburn&Wolf 1997 3.5 g/m3"
    assert hail["vil_density_threshold"] == 3.5


def test_vil_density_null_reason_when_product_missing():
    hail = ss.hail_layer(cappi_levels=None, vil_grid=None, etm_m_grid=None)
    assert hail["vil_density_g_m3"]["value"] is None
    assert hail["vil_density_g_m3"]["null_reason"] == "vil_product_missing"


# ---------------------------------------------------------------------------
# S2 — MESH: SOLO con >= 4 livelli CAPPI (Witt et al. 1998)
# ---------------------------------------------------------------------------
def test_mesh_null_with_two_cappi_levels():
    """Config di produzione: CAPPI_2 + CAPPI_6 -> MESH null con motivo."""
    levels = [(2.0, np.full((2, 2), 50.0)), (6.0, np.full((2, 2), 55.0))]
    hail = ss.hail_layer(cappi_levels=levels, vil_grid=np.full((2, 2), 30.0),
                         etm_m_grid=np.full((2, 2), 9000.0),
                         h0_m=3000.0, h20_m=5500.0)
    assert hail["mesh_mm"]["value"] is None
    assert hail["mesh_mm"]["null_reason"] == "cappi_levels<4"
    assert hail["mesh_unavailable_reason"] == "cappi_levels<4"
    assert hail["mesh_mm"]["cappi_levels"] == 2


def test_mesh_computed_with_four_cappi_levels():
    """4 livelli + quote termiche: SHI/MESH con la formula TRASCRITTA dal paper.

    SHI = 0.1 * sum( E(Z_i) * W_T(H_i) * dh ),  E(Z)=5e-6*10^(0.084 Z)*W(Z),
    W_T lineare fra H0=3 km e H(-20 C)=5.5 km, dh = 1000 m. Il valore atteso e'
    ricalcolato qui dalla formula del paper (p. 288), non copiato dall'output.
    """
    levels = [(2.0, np.full((2, 2), 50.0)), (4.0, np.full((2, 2), 55.0)),
              (6.0, np.full((2, 2), 50.0)), (8.0, np.full((2, 2), 40.0))]
    mesh = ss.mesh_from_cappi(levels, h0_km=3.0, h20_km=5.5)
    e50 = 5e-6 * 10.0 ** (0.084 * 50.0)
    e55 = 5e-6 * 10.0 ** (0.084 * 55.0)
    shi = 0.1 * 1000.0 * (e50 * 0.0 + e55 * 0.4 + e50 * 1.0 + 0.0 * 1.0)
    assert mesh["shi"] == pytest.approx(shi, rel=1e-9)
    assert mesh["value"] == pytest.approx(2.54 * math.sqrt(shi), rel=1e-9)
    assert mesh["null_reason"] is None
    assert mesh["cappi_levels"] == 4
    assert mesh["levels_used"] == [2.0, 4.0, 6.0, 8.0]
    assert mesh["min_levels_per_pixel"] == 4


def test_mesh_null_without_thermal_levels():
    levels = [(h, np.full((2, 2), 50.0)) for h in (2.0, 4.0, 6.0, 8.0)]
    mesh = ss.mesh_from_cappi(levels, h0_km=None, h20_km=None)
    assert mesh["value"] is None
    assert mesh["null_reason"] == "thermal_levels_missing"


def test_mesh_is_not_derived_from_vil():
    """Con 2 livelli, un VIL enorme NON produce alcun MESH."""
    levels = [(2.0, np.full((2, 2), 60.0)), (6.0, np.full((2, 2), 60.0))]
    hail = ss.hail_layer(cappi_levels=levels,
                         vil_grid=np.full((2, 2), 90.0),
                         etm_m_grid=np.full((2, 2), 9000.0),
                         h0_m=3000.0, h20_m=5500.0)
    assert hail["vil_density_g_m3"]["value"] == pytest.approx(10.0)
    assert hail["mesh_mm"]["value"] is None


def test_mesh_null_reason_thermal_invalid():
    levels = [(h, np.full((2, 2), 50.0)) for h in (2.0, 4.0, 6.0, 8.0)]
    mesh = ss.mesh_from_cappi(levels, h0_km=6.0, h20_km=5.0)
    assert mesh["value"] is None
    assert mesh["null_reason"] == "thermal_levels_invalid"


def test_minus20_from_lapse():
    # 0 C a 3000 m, lapse 7 C/km -> -20 C a 3000 + 20/7*1000 = 5857 m
    assert ss.minus20_from_lapse(3000.0, 7.0) == pytest.approx(5857.14, abs=0.02)
    assert ss.minus20_from_lapse(None, 7.0) is None
    assert ss.minus20_from_lapse(3000.0, 0.0) is None


def test_minus20_height_from_pressure_profile():
    # T700=-3 C a 3000 m, T500=-20 C a 5500 m -> la quota del -20 C e' 5500 m
    assert ss.minus20_height_m(-3.0, 3000.0, -20.0, 5500.0) == pytest.approx(5500.0)
    assert ss.minus20_height_m(None, 3000.0, -20.0, 5500.0) is None
    assert ss.minus20_height_m(-3.0, 5500.0, -20.0, 3000.0) is None


# ---------------------------------------------------------------------------
# S1 — ambiente
# ---------------------------------------------------------------------------
def _env_detail(**kw):
    base = {
        "cape": 1200.0, "cin": -50.0, "ebwd_ms": 15.0, "srh_0_3": 120.0,
        "sb_lcl_m": 900.0, "lr_700_500": 7.0, "freezing_level_m": 3000.0,
        "t500_c": -20.0,
        # 4 nodi REALI dell'hodografo: superficie + 1000/925/850 hPa
        "flags": {"srh_low_levels_used": [1000, 925, 850],
                  "srh_profile_nodes": 4},
    }
    base.update(kw)
    return base


def test_environment_layer_from_openmeteo_detail():
    env = ss.environment_layer(_env_detail())
    assert env["cape_jkg"]["value"] == pytest.approx(1200.0)
    assert env["shear_0_6_ms"]["value"] == pytest.approx(15.0)
    assert env["srh_0_3_m2s2"]["value"] == pytest.approx(120.0)
    assert env["srh_0_3_m2s2"]["null_reason"] is None
    assert env["srh_0_3_m2s2"]["n_levels"] == 4
    assert env["wmaxshear_ms2"]["value"] == pytest.approx(
        math.sqrt(2 * 1200.0) * 15.0)
    for key in ("cape_jkg", "shear_0_6_ms", "srh_0_3_m2s2", "lcl_m",
                "wmaxshear_ms2"):
        assert {"value", "null_reason", "source", "reference"} <= set(env[key])


def test_srh_null_when_too_few_wind_levels():
    """SRH richiede la curva dell'hodografo: con pochi livelli -> null."""
    env = ss.environment_layer(
        _env_detail(flags={"srh_low_levels_used": [850],
                           "srh_profile_nodes": 2}))
    assert env["srh_0_3_m2s2"]["value"] is None
    assert env["srh_0_3_m2s2"]["null_reason"] == "wind_levels<4"
    # il valore grezzo esisteva, ma NON si pubblica con troppi pochi nodi
    assert env["srh_0_3_m2s2"]["n_levels"] < 4


def test_srh_null_with_three_nodes_even_if_ebwd_present():
    """3 nodi (superficie + 2 pressione) NON bastano, con o senza EBWD.

    Guardia sul bug 1.2.4.17: contare `ebwd_ms` come se fosse un livello di
    vento portava 3 nodi a 4 e pubblicava un SRH che era un integrale su un
    hodografo inesistente."""
    env = ss.environment_layer(_env_detail(
        ebwd_ms=15.0,
        flags={"srh_low_levels_used": [1000, 850], "srh_profile_nodes": 3}))
    assert env["srh_0_3_m2s2"]["value"] is None
    assert env["srh_0_3_m2s2"]["null_reason"] == "wind_levels<4"
    assert env["srh_0_3_m2s2"]["n_levels"] == 3


def test_environment_null_reasons_when_detail_empty():
    env = ss.environment_layer(None)
    assert env["cape_jkg"]["null_reason"] == "open-meteo_cape_missing"
    assert env["shear_0_6_ms"]["null_reason"] == \
        "open-meteo_wind_10m_500hPa_missing"
    assert env["srh_0_3_m2s2"]["null_reason"] == \
        "open-meteo_srh_not_derivable"
    assert env["lcl_m"]["null_reason"] == "temperature_dewpoint_missing"
    assert env["wmaxshear_ms2"]["null_reason"] == "cape_or_shear_missing"


def test_srh_from_hodograph_gate_and_value():
    prof = [(10.0, 0.0, 0.0), (800.0, 5.0, 0.0), (1500.0, 8.0, 3.0),
            (3000.0, 12.0, 8.0)]
    res = ss.srh_from_hodograph(prof, storm_motion=(2.0, 1.0))
    assert res["null_reason"] is None
    assert isinstance(res["value"], float)
    assert res["n_levels"] == 4

    too_few = ss.srh_from_hodograph(prof[:3])
    assert too_few["value"] is None
    assert too_few["null_reason"] == "wind_levels<4"

    no_top = ss.srh_from_hodograph(
        [(10.0, 0.0, 0.0), (500.0, 2.0, 1.0), (1000.0, 4.0, 2.0),
         (1500.0, 6.0, 3.0)])
    assert no_top["value"] is None
    assert no_top["null_reason"] == "no_wind_level_above_2km"


def test_bulk_shear_requires_profile_span():
    prof = [(10.0, 0.0, 0.0), (3000.0, 10.0, 0.0)]
    assert ss.bulk_shear(prof, 6000.0) is None          # nessun livello a 6 km
    prof6 = prof + [(6000.0, 20.0, 0.0)]
    assert ss.bulk_shear(prof6, 6000.0) == pytest.approx(20.0)


def test_environment_with_local_profile_wins_over_detail():
    prof = [{"height_m": 10.0, "u_ms": 0.0, "v_ms": 0.0},
            {"height_m": 1000.0, "u_ms": 4.0, "v_ms": 0.0},
            {"height_m": 2000.0, "u_ms": 8.0, "v_ms": 2.0},
            {"height_m": 3000.0, "u_ms": 12.0, "v_ms": 4.0},
            {"height_m": 6000.0, "u_ms": 25.0, "v_ms": 5.0}]
    env = ss.environment_layer(_env_detail(), levels=prof)
    assert env["shear_0_6_ms"]["value"] == pytest.approx(
        math.hypot(25.0, 5.0), rel=1e-6)
    assert env["srh_0_3_m2s2"]["value"] is not None


# ---------------------------------------------------------------------------
# S3 — overshooting top
# ---------------------------------------------------------------------------
def _ot_details(**kw):
    base = {"valid_pixels": 400, "ot_flag": True, "n_flags": 5,
            "ctt_min_c": -68.0, "cold_top": True,
            "ctt_threshold_c": -58.0, "anvil_threshold_c": -48.0,
            "anomaly_threshold_c": 6.5, "ring_radius_px": 8}
    base.update(kw)
    return base


def test_ot_layer_boolean_with_source_and_timestamp():
    ot = ss.ot_layer_from_details(_ot_details(),
                                 timestamp_iso="2026-10-23T12:00:00Z")
    assert ot["value"] is True
    assert ot["confidence"] == "high"
    assert ot["timestamp"] == "2026-10-23T12:00:00Z"
    assert "IR_108" in ot["source"]
    assert "Bedka" in ot["reference"]
    assert ot["null_reason"] is None


def test_ot_layer_confidence_medium_single_pixel():
    ot = ss.ot_layer_from_details(_ot_details(n_flags=1))
    assert ot["value"] is True
    assert ot["confidence"] == "medium"


def test_ot_layer_false_is_not_a_null():
    ot = ss.ot_layer_from_details(_ot_details(ot_flag=False, n_flags=0,
                                             cold_top=False))
    assert ot["value"] is False
    assert ot["confidence"] == "low"
    assert ot["null_reason"] is None


def test_ot_layer_null_when_frame_missing():
    ot = ss.ot_layer_from_details(None, timestamp_iso="2026-10-23T12:00:00Z")
    assert ot["value"] is None
    assert ot["null_reason"] == "ir108_frame_unavailable"


def test_ot_layer_from_grid_reuses_satellite_module():
    grid = np.full((40, 40), -50.0)
    grid[18:22, 18:22] = -72.0
    ot = ss.ot_layer_from_grid(grid, timestamp_iso="2026-10-23T12:00:00Z")
    assert ot["value"] is True
    assert ot["n_flags"] >= 1


# ---------------------------------------------------------------------------
# combine(): nessuna probabilita' finta
# ---------------------------------------------------------------------------
def test_combine_contract():
    hail = ss.hail_layer(cappi_levels=[(2.0, 50.0), (6.0, 55.0)],
                         vil_grid=np.full((2, 2), 30.0),
                         etm_m_grid=np.full((2, 2), 9000.0),
                         h0_m=3000.0, h20_m=5500.0)
    env = ss.environment_layer(_env_detail())
    ot = ss.ot_layer_from_details(_ot_details(), timestamp_iso="T")
    out = ss.combine(structure={"soi": 72, "level": "strong"}, hail=hail,
                     environment=env, ot=ot)
    assert set(out) >= {"structure", "hail_risk", "environment", "ot",
                        "rotation_measurable", "probability"}
    assert out["rotation_measurable"] is False
    assert out["rotation_null_reason"]
    assert out["probability"] is None
    assert out["probability_null_reason"]
    assert out["hail_risk"]["threshold_ref"] == "Amburn&Wolf 1997 3.5 g/m3"
    assert out["hail_risk"]["mesh_unavailable_reason"] == "cappi_levels<4"
    assert out["hail_risk"]["mesh"]["value"] is None
    assert out["hail_risk"]["vil_density"]["value"] == pytest.approx(
        30.0 / 9000.0 * 1000.0)


def test_combine_without_components_does_not_invent_values():
    out = ss.combine()
    assert out["structure"] is None
    assert out["hail_risk"]["vil_density"] is None
    assert out["hail_risk"]["mesh"] is None
    assert out["probability"] is None
