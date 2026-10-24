# -*- coding: utf-8 -*-
"""Test 1.2.4.18 — MESH (Witt 1998), overhang da CAPPI, Bunkers RM/LM.

Copertura per contratto:
  - MESH con 10 livelli REALI (1000..10000 m), formule TRASCRITTE dal paper
    (eq. 1-3 p.288, eq. 6 p.293) e ricalcolate qui, non copiate dall'output;
  - maschere nodata DPC: -9999 = fuori copertura (livello non valutabile),
    -9998 = in copertura senza eco (contributo 0, mai 0 dBZ);
  - discretizzazione DICHIARATA (dh = 1000 m, quote termiche ASL da NWP) e
    caso "tabella non disponibile" -> mesh null con `witt_table_unavailable`;
  - overhang: casi sintetici con e senza nucleo elevato, maschera della cella;
  - Bunkers: profilo completo -> valore, profilo incompleto -> null con motivo,
    shear sotto soglia -> null, scostamento dal moto osservato;
  - degradazione del fetch CAPPI: 500 sullo slot corrente -> retry, poi
    fallback t-5min, poi null SOLO per quel livello (run mai fallito).
"""
import math
import types

import numpy as np
import pytest

from radar_engine import fetch as fetch_mod
from radar_engine import models
from radar_engine import severe_stack as ss


# ---------------------------------------------------------------------------
# MESH — formule del paper
# ---------------------------------------------------------------------------
def test_witt_reference_cites_paper_pages_and_constants():
    assert "13(2):286-303" in ss.REF_WITT
    assert "p.288" in ss.REF_WITT and "p.293" in ss.REF_WITT
    assert "2.54" in ss.REF_WITT
    assert ss.WITT_Z_L_DBZ == 40.0 and ss.WITT_Z_U_DBZ == 50.0
    assert ss.WITT_E_DOT_COEFF == 5e-6 and ss.WITT_E_DOT_EXP == 0.084
    assert ss.WITT_MESH_MM_COEFF == 2.54
    assert ss.MESH_DH_M == 1000.0


def test_e_dot_matches_witt_equation_1():
    """E(Z) = 5e-6 * 10^(0.084 Z) * W(Z): NULLO sotto Z_L, pieno sopra Z_U."""
    import numpy as np
    z = np.array([30.0, 40.0, 45.0, 50.0, 60.0])
    e = ss._e_dot(z)
    assert e[0] == 0.0 and e[1] == 0.0          # W(Z)=0 fino a 40 dBZ
    assert e[2] == pytest.approx(5e-6 * 10 ** (0.084 * 45.0) * 0.5, rel=1e-12)
    assert e[3] == pytest.approx(5e-6 * 10 ** (0.084 * 50.0), rel=1e-12)
    assert e[4] == pytest.approx(5e-6 * 10 ** (0.084 * 60.0), rel=1e-12)


def test_mesh_with_ten_real_levels_one_km_spacing():
    """10 livelli CAPPI reali (1..10 km), colonna a 50 dBZ, H0=3 km, -20 C=5.5 km."""
    levels = [(float(h), np.full((3, 3), 50.0)) for h in range(1, 11)]
    mesh = ss.mesh_from_cappi(levels, h0_km=3.0, h20_km=5.5)
    e = 5e-6 * 10 ** (0.084 * 50.0)
    # W_T = 0 sotto 3 km, (h-3)/2.5 fra 3 e 5.5, 1 sopra; dh = 1000 m per livello
    wt = [0.0, 0.0, 0.0, 0.4, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0]
    shi = 0.1 * 1000.0 * e * sum(wt)
    assert mesh["shi"] == pytest.approx(shi, rel=1e-9)
    assert mesh["value"] == pytest.approx(2.54 * math.sqrt(shi), rel=1e-9)
    assert mesh["null_reason"] is None
    assert mesh["cappi_levels"] == 10
    assert mesh["levels_used"] == [float(h) for h in range(1, 11)]
    assert mesh["min_levels_per_pixel"] == 10
    assert mesh["pixels"] == 9


def test_mesh_no_echo_sentinel_is_not_zero_dbz():
    """-9998 (in copertura, senza eco) -> contributo 0 e livello CONTATO.

    Se -9998 fosse letto come "0 dBZ" il risultato sarebbe identico per W(Z),
    ma il test blinda il contrario: il livello NON deve degradare la
    valutabilita' (min_levels_per_pixel resta 10)."""
    levels = [(float(h), np.full((2, 2), 50.0)) for h in range(1, 11)]
    levels[0] = (1.0, np.full((2, 2), ss.DPC_NODATA_NO_ECHO))
    mesh = ss.mesh_from_cappi(levels, h0_km=3.0, h20_km=5.5)
    assert mesh["null_reason"] is None
    assert mesh["min_levels_per_pixel"] == 10
    # il livello 1 km (sotto H0) comunque non contribuisce: stesso valore
    ref = ss.mesh_from_cappi(
        [(float(h), np.full((2, 2), 50.0)) for h in range(1, 11)],
        h0_km=3.0, h20_km=5.5)
    assert mesh["value"] == pytest.approx(ref["value"], rel=1e-12)


def test_mesh_out_of_coverage_levels_are_not_evaluated():
    """-9999 -> livello ASSENTE: con meno di 4 livelli validi il pixel e' null."""
    ok = [(float(h), np.full((2, 2), 50.0)) for h in (1, 2, 3, 4)]
    missing = [(float(h), np.full((2, 2), ss.DPC_NODATA_OUT_OF_COVERAGE))
               for h in (5, 6, 7, 8, 9, 10)]
    four = ss.mesh_from_cappi(ok + missing, h0_km=2.0, h20_km=5.0)
    assert four["null_reason"] is None          # 4 livelli in copertura = gate
    assert four["min_levels_per_pixel"] == 4

    three = ss.mesh_from_cappi(ok[:3] + missing, h0_km=2.0, h20_km=5.0)
    assert three["value"] is None
    # i FILE ci sono tutti (9 livelli), ma nessun pixel ha 4 livelli in
    # copertura: il motivo deve dire questo, non "pochi livelli configurati".
    assert three["null_reason"] == "pixel_coverage_below_4_levels"
    assert three["min_levels_per_pixel"] == 3


def test_mesh_table_unavailable_is_declared_not_faked():
    levels = [(float(h), np.full((2, 2), 55.0)) for h in range(1, 11)]
    mesh = ss.mesh_from_cappi(levels, 3.0, 5.5, table_available=False)
    assert mesh["value"] is None
    assert mesh["null_reason"] == "witt_table_unavailable"
    hail = ss.hail_layer(levels, np.full((2, 2), 30.0),
                         np.full((2, 2), 9000.0), h0_m=3000.0, h20_m=5500.0,
                         mesh_table_available=False)
    assert hail["mesh_unavailable_reason"] == "witt_table_unavailable"
    assert hail["mesh_mm"]["null_reason"] == "witt_table_unavailable"


def test_hail_layer_declares_discretization_and_thermal_sources():
    levels = [(float(h), np.full((2, 2), 50.0)) for h in range(1, 11)]
    hail = ss.hail_layer(levels, h0_m=3000.0, h20_m=5500.0,
                         h0_source="open-meteo freezing_level_height (ASL)",
                         h20_source="interpolazione -20 C sul profilo T")
    field = hail["mesh_mm"]
    assert field["dh_m"] == 1000.0
    assert field["h0_m"] == 3000.0 and field["h20_m"] == 5500.0
    assert "freezing" in field["h0_source"]
    assert "profilo T" in field["h20_source"]
    assert field["paper_rounding_step_mm"] == 6.35
    for key in ("value", "null_reason", "source", "reference"):
        assert key in field


def test_mesh_cell_mask_restricts_computation():
    """Fuori maschera i pixel valgono -9999: nessun contributo inventato."""
    levels = [(float(h), np.full((4, 4), 50.0)) for h in range(1, 11)]
    mask = np.zeros((4, 4), dtype=bool)
    mask[1:3, 1:3] = True
    hail = ss.hail_layer(levels, h0_m=3000.0, h20_m=5500.0, mask=mask)
    assert hail["mesh_mm"]["pixels"] == 4
    assert hail["mesh_mm"]["min_levels_per_pixel"] == 10


# ---------------------------------------------------------------------------
# OVERHANG / nucleo elevato
# ---------------------------------------------------------------------------
def test_overhang_elevated_core_detected():
    low = np.full((3, 3), 30.0)
    high = np.full((3, 3), 50.0)
    out = ss.overhang_layer(low, high)
    assert out["elevated_core"]["value"] is True
    assert out["elevated_core"]["elevated_columns"] == 9
    assert out["elevated_core"]["columns"] == 9
    assert out["strong_elevated_core"]["value"] is True
    assert out["gradient_max_dbz"]["value"] == pytest.approx(20.0)
    assert out["levels"] == {"low_km": 2.0, "high_km": 6.0}
    for key in ("elevated_core", "strong_elevated_core", "gradient_max_dbz"):
        assert {"value", "null_reason", "source", "reference"} <= set(out[key])


def test_overhang_without_elevated_core():
    low = np.full((3, 3), 50.0)
    high = np.full((3, 3), 30.0)
    out = ss.overhang_layer(low, high)
    assert out["elevated_core"]["value"] is False
    assert out["elevated_core"]["elevated_columns"] == 0
    assert out["strong_elevated_core"]["value"] is False
    assert out["gradient_max_dbz"]["value"] == pytest.approx(-20.0)


def test_overhang_weak_low_echo_but_strong_at_6km():
    """Solo 1 colonna su 4 e' un nucleo elevato (soglia 45 dBZ a 6 km)."""
    high = np.array([[46.0, 44.0], [50.0, 20.0]])
    low = np.array([[10.0, 10.0], [50.0, 10.0]])
    out = ss.overhang_layer(low, high)
    assert out["elevated_core"]["elevated_columns"] == 1     # solo [0][0]
    assert out["elevated_core"]["value"] is True
    assert out["strong_elevated_core"]["strong_columns"] == 1   # [1][0]
    assert out["elevated_core"]["fraction"] == pytest.approx(0.25)


def test_overhang_no_echo_at_low_level_counts_as_elevated():
    low = np.full((2, 2), ss.DPC_NODATA_NO_ECHO)
    high = np.full((2, 2), 50.0)
    out = ss.overhang_layer(low, high)
    assert out["elevated_core"]["value"] is True
    assert out["gradient_max_dbz"]["value"] is None       # nessun eco basso
    assert out["gradient_max_dbz"]["null_reason"] == \
        "no_column_with_echo_both_levels" or \
        out["gradient_max_dbz"]["value"] is None


def test_overhang_out_of_coverage_is_not_evaluated():
    high = np.full((2, 2), 50.0)
    low = np.full((2, 2), ss.DPC_NODATA_OUT_OF_COVERAGE)
    out = ss.overhang_layer(low, high)
    assert out["elevated_core"]["value"] is None
    assert out["elevated_core"]["null_reason"] == "no_overlapping_cappi_coverage"
    assert out["columns"] == 0


def test_overhang_missing_cappi():
    out = ss.overhang_layer(None, np.full((2, 2), 50.0))
    assert out["elevated_core"]["null_reason"] == "cappi_low_or_high_missing"
    out2 = ss.overhang_layer(np.full((2, 2), 30.0), np.full((3, 3), 50.0))
    assert out2["elevated_core"]["null_reason"] == "cappi_grid_mismatch"


def test_overhang_mask_limits_columns():
    low = np.full((4, 4), 30.0)
    high = np.full((4, 4), 50.0)
    mask = np.zeros((4, 4), dtype=bool)
    mask[:2, :2] = True
    out = ss.overhang_layer(low, high, mask=mask)
    assert out["columns"] == 4
    assert out["elevated"] == 4


# ---------------------------------------------------------------------------
# BUNKERS RM/LM
# ---------------------------------------------------------------------------
def _prof(top_m=6200.0):
    """Profilo sintetico con shear lineare da 0 a 20 m/s e top a 6.2 km."""
    return [
        {"height_m": 10.0, "u_ms": 0.0, "v_ms": 0.0},
        {"height_m": 1000.0, "u_ms": 3.0, "v_ms": 0.0},
        {"height_m": 3000.0, "u_ms": 8.0, "v_ms": 2.0},
        {"height_m": 4500.0, "u_ms": 12.0, "v_ms": 4.0},
        {"height_m": 5800.0, "u_ms": 17.0, "v_ms": 5.0},
        {"height_m": top_m, "u_ms": 19.0, "v_ms": 6.0},
    ]


def test_bunkers_complete_profile_gives_values():
    out = ss.bunkers_layer(levels=_prof(), observed_motion={
        "velocity_kmh": 60.0, "direction_toward_deg": 70.0})
    assert out["rm_ms"]["null_reason"] is None
    assert out["lm_ms"]["null_reason"] is None
    assert out["shear_0_6_ms"]["value"] is not None
    rm = out["rm_ms"]["value"]
    lm = out["lm_ms"]["value"]
    mean = out["mean_wind_0_6_ms"]["value"]
    # RM e LM stanno a 7.5 m/s dal vento medio, su lati opposti
    d_rm = math.hypot(rm["u_ms"] - mean["u_ms"], rm["v_ms"] - mean["v_ms"])
    d_lm = math.hypot(lm["u_ms"] - mean["u_ms"], lm["v_ms"] - mean["v_ms"])
    assert d_rm == pytest.approx(7.5, abs=0.02)
    assert d_lm == pytest.approx(7.5, abs=0.02)
    # il RM e' a DESTRA dello shear (shear verso NE -> RM verso SE)
    assert rm["u_ms"] > mean["u_ms"] and rm["v_ms"] < mean["v_ms"]
    assert out["deviation_angle_deg"]["value"] is not None
    assert "Bunkers et al. 2000" in out["rm_ms"]["reference"]
    assert "euristico" in out["rm_ms"]["reference"]
    assert out["note"].startswith("descrittore ambientale")


def test_bunkers_incomplete_profile_is_null_with_reason():
    """Il 500 hPa si ferma a ~5,5 km: senza un livello >= 6 km niente Bunkers."""
    out = ss.bunkers_layer(levels=_prof()[:5])          # top 5800 m
    assert out["rm_ms"]["value"] is None
    assert out["rm_ms"]["null_reason"] == "wind_profile_incomplete_0_6km"
    assert out["deviation_angle_deg"]["null_reason"] == \
        "wind_profile_incomplete_0_6km"
    assert out["profile_top_m"] == 5800.0


def test_bunkers_empty_profile_is_null_with_reason():
    out = ss.bunkers_layer(levels=[])
    assert out["rm_ms"]["null_reason"] == "open-meteo_wind_profile_missing"


def test_bunkers_shear_below_threshold_is_null():
    flat = [{"height_m": h, "u_ms": 10.0, "v_ms": 0.0}
            for h in (10.0, 1000.0, 3000.0, 6200.0)]
    out = ss.bunkers_layer(levels=flat)
    assert out["rm_ms"]["value"] is None
    assert out["rm_ms"]["null_reason"] == "shear_below_1ms"


def test_bunkers_deviation_is_zero_when_motion_matches_rm():
    """Io stesso vettore RM (valori NON arrotondati) -> deviazione 0."""
    prof = [(n["height_m"], n["u_ms"], n["v_ms"]) for n in _prof()]
    rm_u, rm_v = ss.bunkers_rm_lm(prof)["value"]["rm"]
    speed_ms = math.hypot(rm_u, rm_v)
    toward = (math.degrees(math.atan2(rm_u, rm_v)) + 360.0) % 360.0
    out = ss.bunkers_layer(levels=_prof(), observed_motion={
        "velocity_kmh": speed_ms * 3.6, "direction_toward_deg": toward})
    assert out["deviation_angle_deg"]["value"] == pytest.approx(0.0, abs=1e-6)
    assert out["deviation_magnitude_ms"]["value"] == pytest.approx(0.0, abs=1e-6)


def test_bunkers_deviation_sign_positive_to_the_right():
    """Osservato ruotato di +30 gradi (senso orario) -> angolo positivo."""
    ref = (10.0, 0.0)                                  # verso est
    right = (10.0 * math.cos(math.radians(30)),
             10.0 * math.sin(math.radians(30)))        # 30 gradi a destra
    dev = ss.motion_deviation(right, ref)
    assert dev["angle_deg"] == pytest.approx(30.0, abs=1e-9)
    assert dev["magnitude_ms"] == pytest.approx(
        math.hypot(right[0] - 10.0, right[1]), rel=1e-12)


def test_bunkers_observed_motion_missing_keeps_bunkers_value():
    out = ss.bunkers_layer(levels=_prof(), observed_motion={})
    assert out["rm_ms"]["value"] is not None
    assert out["observed_motion_ms"]["null_reason"] == "observed_motion_missing"
    assert out["deviation_angle_deg"]["value"] is None
    assert out["deviation_angle_deg"]["null_reason"] == "observed_motion_missing"


def test_bunkers_reads_wind_profile_from_env_detail():
    env_detail = {"wind_profile": _prof(),
                  "profile_top_m": 6200.0, "h20_from_profile_m": 5600.0}
    out = ss.bunkers_layer(env_detail=env_detail)
    assert out["rm_ms"]["value"] is not None
    assert out["profile_levels"] == len(_prof())


def test_bunkers_observed_motion_above_kinematic_gate_is_rejected():
    """Moto osservato 226,5 km/h (visto in un run reale) -> deviazione null.

    Il valore del tracking oltre `max_validated_velocity_kmh` (200 km/h, gate
    Fase 1.9) NON e' utilizzabile: senza questo gate usciva una deviazione di
    211,6 m/s, cioe' un numero senza significato fisico."""
    out = ss.bunkers_layer(levels=_prof(), observed_motion={
        "velocity_kmh": 226.5, "direction_toward_deg": 129.5},
        max_observed_speed_kmh=200.0)
    assert out["rm_ms"]["value"] is not None          # Bunkers resta pubblicato
    assert out["observed_motion_ms"]["value"] is None
    assert out["observed_motion_ms"]["null_reason"] == \
        "observed_motion_above_kinematic_gate"
    assert out["observed_motion_ms"]["observed_speed_kmh"] == 226.5
    assert out["deviation_angle_deg"]["value"] is None
    assert out["deviation_angle_deg"]["null_reason"] == \
        "observed_motion_above_kinematic_gate"


def test_combine_exposes_overhang_and_bunkers_without_rotation_claim():
    levels = [(float(h), np.full((2, 2), 50.0)) for h in range(1, 11)]
    hail = ss.hail_layer(levels, h0_m=3000.0, h20_m=5500.0)
    overhang = ss.overhang_layer(np.full((2, 2), 30.0), np.full((2, 2), 50.0))
    bunkers = ss.bunkers_layer(levels=_prof())
    out = ss.combine(hail=hail, overhang=overhang, bunkers=bunkers)
    assert out["overhang"] is overhang
    assert out["bunkers"] is bunkers
    assert out["rotation_measurable"] is False
    assert out["rotation_null_reason"] == "no_radial_velocity_in_dpc_2d_mosaic"
    assert out["probability"] is None
    assert out["hail_risk"]["mesh"]["value"] is not None


# ---------------------------------------------------------------------------
# FETCH CAPPI: 500 sullo slot corrente, retry, fallback, degradazione
# ---------------------------------------------------------------------------
def _cfg(**cappi_fetch):
    return {
        "source": {"cache_dir": ".", "api_base": "x", "origin_header": "y",
                   "http_timeout_s": 1, "download_timeout_s": 1,
                   "max_download_retries": 0, "keep_raw_frames": False},
        "preprocess": {"nodata_values": [-9999.0, -9998.0],
                       "geo_plausible_bbox": None},
        "phase2": {"structure": {"cappi_fetch": dict(cappi_fetch)}},
    }


def _raster():
    return types.SimpleNamespace(data=np.zeros((2, 2)), time_ms=0,
                                 time_iso="2026-10-23T21:15:00Z")


def test_fetch_cappi_set_retry_then_success(monkeypatch):
    """Primo tentativo 500 ("non pubblicato") -> retry con UNA attesa -> ok."""
    waits = []
    calls = []

    def fake_read(config, product, ts, period, downloader):
        calls.append((product, ts))
        if len(calls) <= 2:
            raise models.SourceError("http_500")
        return _raster(), {"bytes": 500000, "elapsed_s": 0.4,
                           "fallback": False, "used_time_ms": ts}

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=1, retry_wait_s=20.0, allow_previous_slot=True),
        ["CAPPI_1", "CAPPI_2"], 1792790100000, warnings=[],
        sleeper=waits.append)
    assert waits == [20.0]                       # UNA attesa, non una per file
    assert results["CAPPI_1"] is not None and results["CAPPI_2"] is not None
    assert summary["fetched"] == 2 and summary["fallback"] == 0
    assert summary["bytes"] == 1000000


def test_fetch_cappi_set_falls_back_to_previous_slot(monkeypatch):
    """Sempre 500 sullo slot corrente -> fallback a t-5min, dichiarato."""
    calls = []

    def fake_read(config, product, ts, period, downloader):
        calls.append((product, ts))
        if ts == 1792790100000:
            raise models.SourceError("http_500")
        return _raster(), {"bytes": 100, "elapsed_s": 0.1,
                           "fallback": False, "used_time_ms": ts}

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    warnings = []
    results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=0, retry_wait_s=0.0, allow_previous_slot=True),
        ["CAPPI_6"], 1792790100000, warnings=warnings, sleeper=lambda s: None)
    assert results["CAPPI_6"] is not None
    assert summary["fallback"] == 1
    assert any("fallback allo slot precedente" in w for w in warnings)
    assert calls[-1][1] == 1792790100000 - 300000


def test_fetch_cappi_set_partial_failure_never_raises(monkeypatch):
    """Un livello non scaricabile NON ferma il run: None + summary, gli altri ok."""
    def fake_read(config, product, ts, period, downloader):
        if product == "CAPPI_9":
            raise models.SourceError("http_500")
        if product == "CAPPI_10":
            raise models.ValidationError("raster_corrupted:test")
        return _raster(), {"bytes": 10, "elapsed_s": 0.1,
                           "fallback": False, "used_time_ms": ts}

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=1, retry_wait_s=0.0, allow_previous_slot=True),
        ["CAPPI_1", "CAPPI_9", "CAPPI_10"], 1792790100000,
        warnings=[], sleeper=lambda s: None)
    assert results["CAPPI_1"] is not None
    assert results["CAPPI_9"] is None
    assert results["CAPPI_10"] is None
    assert summary["fetched"] == 1
    assert set(summary["failed"]) == {"CAPPI_9", "CAPPI_10"}
    assert "not_published" in summary["products"]["CAPPI_9"]


# ---------------------------------------------------------------------------
# Config + wiring (main)
# ---------------------------------------------------------------------------
def test_cappi_product_map_switch_off_uses_two_levels():
    import radar_engine.main as eng
    from radar_engine.config import CONFIG
    scfg = CONFIG["phase2"]["structure"]
    on = eng._cappi_product_map(scfg)
    assert len(on) == 10
    assert on[2.0] == "CAPPI_2" and on[10.0] == "CAPPI_10"
    off = eng._cappi_product_map(dict(scfg, mesh_levels_enabled=False))
    assert len(off) == 2
    assert set(off.values()) == {"CAPPI_2", "CAPPI_6"}


def test_config_declares_mesh_and_overhang_and_cost():
    from radar_engine.config import CONFIG
    scfg = CONFIG["phase2"]["structure"]
    assert scfg["product_low"] == "CAPPI_2"
    assert scfg["product_high"] == "CAPPI_6"
    assert scfg["mesh_levels_enabled"] is True
    assert scfg["mesh_min_levels"] == 4
    assert scfg["mesh_dh_m"] == 1000.0
    assert scfg["cappi_fetch"]["period_s"] == 300
    assert scfg["cappi_fetch"]["allow_previous_slot"] is True
    assert scfg["overhang"]["core_dbz"] == 45.0
    assert CONFIG["phase2"]["severe_stack"]["bunkers_top_m"] == 6000.0


def test_severe_stack_wiring_with_ten_levels_footprint_and_motion():
    """Wiring: 10 livelli + maschera + moto osservato -> campi non null."""
    import radar_engine.main as eng
    # colonna tipo: 45 dBZ, con 2 km debole (30) e 6 km forte (52) -> nucleo
    # elevato; tutte le quote restano sopra il gate di 4 livelli.
    values = {float(h): 45.0 for h in range(1, 11)}
    values[2.0] = 30.0
    values[6.0] = 52.0
    levels = {h: types.SimpleNamespace(data=np.full((4, 4), v))
              for h, v in values.items()}
    mask = np.zeros((4, 4), dtype=bool)
    mask[:2, :2] = True
    env_detail = {"freezing_level_m": 3000.0, "h20_from_profile_m": 5500.0,
                  "wind_profile": _prof(), "profile_top_m": 6200.0}
    parts = eng._severe_stack_for_candidate(
        window=(0, 3, 0, 3),
        products={"vil": types.SimpleNamespace(data=np.full((4, 4), 30.0)),
                  "etm": types.SimpleNamespace(data=np.full((4, 4), 9000.0))},
        cappi=levels, env_detail=env_detail, ot_details=None,
        bundle=types.SimpleNamespace(radar_timestamp_iso="2026-10-23T21:15:00Z"),
        footprint=mask,
        observed_motion={"velocity_kmh": 60.0, "direction_toward_deg": 70.0})
    assert parts["cappi_levels"] == 10
    assert parts["hail"]["mesh_mm"]["value"] is not None
    assert parts["hail"]["mesh_mm"]["pixels"] == 4        # solo la maschera
    assert parts["hail"]["mesh_mm"]["h0_source"].startswith("open-meteo")
    assert parts["overhang"]["elevated_core"]["value"] is True
    assert parts["overhang"]["columns"] == 4
    assert parts["bunkers"]["rm_ms"]["value"] is not None
    assert parts["bunkers"]["deviation_angle_deg"]["value"] is not None
    assert parts["ot"]["null_reason"] == "ir108_frame_unavailable"


def test_severe_stack_wiring_survives_missing_cappi_levels():
    """Degradazione: solo 3 livelli disponibili -> MESH null, run mai fallito."""
    import radar_engine.main as eng
    cappi = {1.0: None, 2.0: None, 3.0: None,
             4.0: types.SimpleNamespace(data=np.full((4, 4), 50.0)),
             5.0: types.SimpleNamespace(data=np.full((4, 4), 50.0)),
             6.0: types.SimpleNamespace(data=np.full((4, 4), 50.0))}
    parts = eng._severe_stack_for_candidate(
        window=(0, 3, 0, 3), products={}, cappi=cappi, env_detail=None,
        ot_details=None,
        bundle=types.SimpleNamespace(radar_timestamp_iso="T"))
    assert parts["cappi_levels"] == 3
    assert parts["hail"]["mesh_mm"]["null_reason"] == "cappi_levels<4"
    # overhang: CAPPI_2 assente -> null con motivo, nessuna eccezione
    assert parts["overhang"]["elevated_core"]["null_reason"] == \
        "cappi_low_or_high_missing"
    assert parts["bunkers"]["rm_ms"]["null_reason"] == \
        "open-meteo_wind_profile_missing"
