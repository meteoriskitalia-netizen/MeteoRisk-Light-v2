#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FASE 7 — test del layer additivo `radar_engine.advanced`.

TUTTI i dati qui sono SINTETICI e dichiarati come tali (nessun dato simulato
entra nel prodotto): servono solo a dimostrare che le regole mordono.
I test importano `radar_engine.advanced`, che NON esiste sul codice pristino:
l'intero file fallisce quindi sul pristino (dimostrazione che i test mordono).
"""
import pytest

from radar_engine import advanced as adv
from radar_engine import models


# ---------------------------------------------------------------------------
# helpers (dati sintetici dichiarati)
# ---------------------------------------------------------------------------
def _candidate(**over):
    """Record minimo sintetico con i campi che il layer legge."""
    base = {
        "structure_id": "CS-TEST",
        "soi": 55,
        "soi_v2": 48.0,
        "level": "strong",
        "phase": "mature",
        "status": "active",
        "on_latest_frame": True,
        "n_points": 6,
        "cell_count": 1,
        "area_km2": 220.0,
        "intensity": {"max_dbz": 52.0, "mean_dbz": 45.0,
                      "delta_dbz": 0.0},
        "motion": {"duration_min": 30.0, "area_growth_pct": 0.0},
        "rotation_measurable": False,
        "rotation_null_reason": "no_opera_vradh_data",
        "morphology_organization": {
            "classification": "organized_vertical",
            "values": {"echo_top_m": 6500, "vertical_extent_km": 5.0,
                       "vil_density_g_m3": 4.0},
        },
        "hail": {"vil_density_g_m3": {"value": 4.0, "null_reason": None}},
        "overhang": {"strong_elevated_core": {"value": True,
                                              "null_reason": None}},
        "severe_stack": {"rotation_measurable": False},
    }
    base.update(over)
    return base


def _rot(persistence=3, state="ROTATION_DETECTED", measurable=True):
    return {"rotation_measurable": measurable,
            "persistence_scans": persistence,
            "classification": state,
            "source": "OPERA FR VRADH (sintetico test)"}


# ---------------------------------------------------------------------------
# 1) lessici e contratto dei campi
# ---------------------------------------------------------------------------
def test_lexicons_are_the_7_and_6_required():
    assert len(adv.MORPHOLOGY_CLASSES) == 7
    assert adv.MORPHOLOGY_CLASSES == (
        "SINGLE_CELL", "MULTICELL", "SQUALL_LINE", "BOW_ECHO", "MCS",
        "SUPERCELL_CANDIDATE", "HP_SUPERCELL_CANDIDATE")
    assert len(adv.LIFECYCLE_STAGES) == 6
    assert adv.LIFECYCLE_STAGES == (
        "GENESIS", "DEVELOPING", "INTENSIFYING", "MATURE", "WEAKENING",
        "DISSIPATING")


@pytest.mark.parametrize("builder", [
    lambda: adv.classify_morphology(_candidate()),
    lambda: adv.derive_lifecycle(_candidate()),
    lambda: adv.bwer_candidate(_candidate(), cappi_levels=2),
    lambda: adv.overhang_candidate(_candidate()),
    lambda: adv.track_hail_nuclei([], cappi_levels=2),
])
def test_every_field_has_source_reference_and_null_reason(builder):
    f = builder()
    assert "value" in f and "source" in f and "reference" in f
    assert "null_reason" in f
    if f["value"] is None:
        assert f["null_reason"], "null senza motivo"
    assert f["reference"]


# ---------------------------------------------------------------------------
# 2) SUPERCELL / HP_SUPERCELL: MAI senza Doppler persistente
# ---------------------------------------------------------------------------
def test_supercell_shape_alone_is_null_with_explicit_reason():
    """Eco profonda + VIL alta + overhang forte, ma SENZA VRADH.

    Le DUE classi supercell restano null con motivo esplicito: la forma/hook
    NON puo' assegnarle. Il descrittore principale resta una classe non-
    supercell, con il flag `supercell_shape_without_doppler` che dichiara
    che la somiglianza e' stata vista ma NON diagnosticata.
    """
    c = _candidate()
    f = adv.classify_morphology(c, shape_2d={"elongation": 2.0,
                                             "bow_score": 0.2})
    assert f["value"] in adv.MORPHOLOGY_CLASSES
    assert f["value"] not in adv.SUPERCELL_CLASSES
    assert f["supercell_shape_without_doppler"] is True
    for cls in adv.SUPERCELL_CLASSES:
        sc = f["supercell_evaluation"][cls]
        assert sc["value"] is None
        assert sc["null_reason"] == adv.ROTATION_NULL_REASON
        assert "no_opera_vradh_data" in sc["null_reason"]


def test_supercell_requires_persistent_rotation_not_only_measurable():
    c = _candidate()
    f = adv.classify_morphology(c, rotation_evidence=_rot(persistence=1))
    assert f["value"] not in adv.SUPERCELL_CLASSES
    for cls in adv.SUPERCELL_CLASSES:
        sc = f["supercell_evaluation"][cls]
        assert sc["value"] is None
        assert sc["null_reason"] == adv.ROTATION_NULL_REASON


def test_supercell_and_hp_assigned_only_with_persistent_doppler():
    c = _candidate()
    # VIL 4.0 (< 4.5) -> classico
    f = adv.classify_morphology(c, rotation_evidence=_rot(persistence=3))
    assert f["value"] == "SUPERCELL_CANDIDATE"
    assert f["supercell_evaluation"]["SUPERCELL_CANDIDATE"]["value"] == \
        "SUPERCELL_CANDIDATE"
    assert f["supercell_evaluation"]["HP_SUPERCELL_CANDIDATE"]["value"] is None
    # VIL 5.0 -> HP
    c2 = _candidate(hail={"vil_density_g_m3": {"value": 5.0,
                                               "null_reason": None}})
    f2 = adv.classify_morphology(c2, rotation_evidence=_rot(persistence=3))
    assert f2["value"] == "HP_SUPERCELL_CANDIDATE"
    assert f2["supercell_evaluation"]["SUPERCELL_CANDIDATE"]["value"] is None


def test_wrong_rotation_state_does_not_assign_supercell():
    c = _candidate()
    f = adv.classify_morphology(
        c, rotation_evidence=_rot(persistence=4, state="NO_ROTATION"))
    assert f["value"] not in adv.SUPERCELL_CLASSES
    for cls in adv.SUPERCELL_CLASSES:
        assert f["supercell_evaluation"][cls]["value"] is None


# ---------------------------------------------------------------------------
# 3) 4 classi non-supercell mordono su dati sintetici dichiarati
# ---------------------------------------------------------------------------
def test_non_supercell_classes_bite():
    # MULTICELL
    c = _candidate(cell_count=3, area_km2=300.0)
    f = adv.classify_morphology(c)
    assert f["value"] == "MULTICELL", f
    # SINGLE_CELL
    c = _candidate(cell_count=1, area_km2=80.0,
                   motion={"duration_min": 25.0, "area_growth_pct": 0.0})
    f = adv.classify_morphology(c)
    assert f["value"] == "SINGLE_CELL", f
    # MCS
    c = _candidate(cell_count=6, area_km2=7000.0,
                   motion={"duration_min": 95.0, "area_growth_pct": 0.0})
    f = adv.classify_morphology(c)
    assert f["value"] == "MCS", f
    # SQUALL_LINE (richiede shape_2d reale)
    c = _candidate(cell_count=4, area_km2=1400.0)
    f = adv.classify_morphology(c, shape_2d={"elongation": 4.2,
                                             "bow_score": 0.1})
    assert f["value"] == "SQUALL_LINE", f
    # BOW_ECHO
    f = adv.classify_morphology(c, shape_2d={"elongation": 2.0,
                                             "bow_score": 0.85})
    assert f["value"] == "BOW_ECHO", f


def test_shape_2d_unavailable_does_not_guess_line_or_bow():
    c = _candidate(cell_count=1, area_km2=80.0,
                   motion={"duration_min": 120.0, "area_growth_pct": 0.0})
    f = adv.classify_morphology(c, shape_2d=None)
    assert f["value"] is None
    assert f["null_reason"] == "shape_2d_unavailable" or \
        f["null_reason"] == "morphology_inconclusive"


# ---------------------------------------------------------------------------
# 4) Lifecycle 6 fasi (distinto dai 7 eventi Doppler)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("over,expected", [
    ({"status": "dead", "on_latest_frame": False}, "DISSIPATING"),
    ({"motion": {"duration_min": 30.0, "area_growth_pct": -25.0}}, "WEAKENING"),
    ({"phase": "birth", "n_points": 2,
      "intensity": {"max_dbz": 40.0, "mean_dbz": 35.0, "delta_dbz": 1.0}},
     "GENESIS"),
    ({"intensity": {"max_dbz": 52.0, "mean_dbz": 45.0, "delta_dbz": 8.0}},
     "INTENSIFYING"),
    ({"intensity": {"max_dbz": 48.0, "mean_dbz": 42.0, "delta_dbz": 3.0}},
     "DEVELOPING"),
    ({"intensity": {"max_dbz": 55.0, "mean_dbz": 48.0, "delta_dbz": 0.0},
      "motion": {"duration_min": 30.0, "area_growth_pct": 2.0}}, "MATURE"),
])
def test_lifecycle_stages(over, expected):
    f = adv.derive_lifecycle(_candidate(**over))
    assert f["value"] == expected, (expected, f)


def test_lifecycle_strong_area_growth_is_not_weakening():
    """Un delta_dbz negativo NON basta se l'area cresce fortemente.

    Caso reale che il vecchio OR etichettava WEAKENING: area +83,9% e
    delta_dbz -7,0. La crescita dell'area (>= soglia intensifying) vince.
    """
    f = adv.derive_lifecycle(_candidate(
        intensity={"max_dbz": 45.0, "mean_dbz": 40.0, "delta_dbz": -7.0},
        motion={"duration_min": 30.0, "area_growth_pct": 83.9}))
    assert f["value"] == "INTENSIFYING", f
    # Con area in calo il segnale negativo resta WEAKENING.
    g = adv.derive_lifecycle(_candidate(
        intensity={"max_dbz": 45.0, "mean_dbz": 40.0, "delta_dbz": -7.0},
        motion={"duration_min": 30.0, "area_growth_pct": -25.0}))
    assert g["value"] == "WEAKENING", g


def test_lifecycle_null_with_short_history():
    c = _candidate(n_points=1,
                   motion={"duration_min": 5.0, "area_growth_pct": None},
                   intensity={"max_dbz": 40.0, "mean_dbz": 35.0,
                              "delta_dbz": None})
    f = adv.derive_lifecycle(c)
    assert f["value"] is None
    assert f["null_reason"] == "insufficient_track_history"


def test_lifecycle_is_not_the_doppler_events():
    # I 7 eventi Doppler restano invariati: il lessico lifecycle e' disjoint.
    assert not set(adv.LIFECYCLE_STAGES) & set(models.ROTATION_EVENTS)
    f = adv.derive_lifecycle(_candidate())
    assert "NON i 7 eventi Doppler" in f["note"]


# ---------------------------------------------------------------------------
# 5) BWER / overhang CANDIDATI con quality
# ---------------------------------------------------------------------------
def test_bwer_null_with_two_cappi():
    f = adv.bwer_candidate(_candidate(), cappi_levels=2)
    assert f["value"] is None
    assert f["null_reason"] == "cappi_levels_lt_4"
    assert f["candidate"] is None and f["quality"] == "D_unavailable"


def test_bwer_candidate_with_profile():
    prof = {"low_dbz": 58.0, "mid_dbz": 40.0, "high_dbz": 52.0}
    f = adv.bwer_candidate(_candidate(), cappi_levels=6, profile=prof)
    assert f["value"] is True and f["candidate"] is True
    assert f["quality"] == "A_observed"
    # profile senza canale debole -> non candidato
    f2 = adv.bwer_candidate(_candidate(), cappi_levels=6,
                            profile={"low_dbz": 50.0, "mid_dbz": 48.0,
                                     "high_dbz": 52.0})
    assert f2["value"] is False


def test_overhang_candidate_quality_and_null():
    f = adv.overhang_candidate(_candidate())
    assert f["value"] is True and f["candidate"] is True
    assert f["quality"] == "A_observed"
    # slot mismatch degrada la qualita'
    c = _candidate(overhang={"strong_elevated_core": {"value": True,
                                                      "null_reason": None},
                             "cappi_slot_offset_s": 300})
    f2 = adv.overhang_candidate(c)
    assert f2["quality"] == "B_slot_mismatch" and f2["slot_mismatch"] is True
    # layer assente -> null motivato
    f3 = adv.overhang_candidate({"overhang": None})
    assert f3["value"] is None
    assert f3["null_reason"] == "overhang_layer_unavailable"


# ---------------------------------------------------------------------------
# 6) Tracking nuclei grandine
# ---------------------------------------------------------------------------
def test_hail_nuclei_null_with_two_cappi():
    frames = [{"time_iso": "2026-11-20T10:00:00Z",
               "nuclei": [{"lat": 45.0, "lon": 9.0, "dbz": 55.0,
                           "area_km2": 30.0}]}]
    f = adv.track_hail_nuclei(frames, cappi_levels=2)
    assert f["value"] is None
    assert f["null_reason"] == "cappi_levels_lt_4"
    assert f["tracks"] == []


def test_hail_nuclei_links_frames_and_ignores_low_dbz():
    frames = [
        {"time_iso": "2026-11-20T10:00:00Z", "nuclei": [
            {"lat": 45.0, "lon": 9.0, "dbz": 55.0, "area_km2": 30.0},
            {"lat": 45.0, "lon": 9.0, "dbz": 45.0, "area_km2": 10.0},
        ]},
        {"time_iso": "2026-11-20T10:05:00Z", "nuclei": [
            {"lat": 45.02, "lon": 9.02, "dbz": 58.0, "area_km2": 40.0},
        ]},
        {"time_iso": "2026-11-20T10:10:00Z", "nuclei": [
            {"lat": 45.04, "lon": 9.04, "dbz": 60.0, "area_km2": 50.0},
        ]},
    ]
    f = adv.track_hail_nuclei(frames, cappi_levels=8)
    assert f["value"] is not None, f
    assert f["value"]["nuclei_total"] == 3  # il 45 dBZ e' sotto soglia -> filtrato
    assert f["value"]["tracks_total"] == 1
    tr = f["value"]["tracks"][0]
    assert tr["n_frames"] == 3 and tr["max_dbz"] == 60.0
    assert tr["area_growth_pct"] is not None


def test_hail_nuclei_no_persistent_track_is_not_counted():
    frames = [
        {"time_iso": "2026-11-20T10:00:00Z",
         "nuclei": [{"lat": 45.0, "lon": 9.0, "dbz": 55.0}]},
    ]
    f = adv.track_hail_nuclei(frames, cappi_levels=8)
    assert f["value"]["tracks_total"] == 0


# ---------------------------------------------------------------------------
# 7) Wiring additivo: nessun campo esistente toccato
# ---------------------------------------------------------------------------
def test_attach_advanced_is_additive_and_publishes_summary():
    bundle = models.EngineBundle("ok", "2026-11-20T10:00:00Z", "test")
    c = _candidate()
    bundle.convective_structures = [c]
    bundle.advanced_cappi_levels = 2
    before = {k: c[k] for k in ("soi", "soi_v2", "rotation_measurable",
                                "severe_stack", "morphology_organization")}
    _, summary = adv.attach_advanced(bundle, config=None)
    for key in ("morphology_class", "lifecycle_stage", "bwer_candidate",
                "overhang_candidate", "hail_nuclei_tracking"):
        assert key in c and isinstance(c[key], dict)
    # campi esistenti INTATTI
    for k, v in before.items():
        assert c[k] == v
    assert bundle.advanced_summary is summary
    assert summary["cappi_levels"] == 2
    assert summary["rotation_evidence_available"] is False
    assert summary["supercell_null_reason"] == adv.ROTATION_NULL_REASON
    assert summary["morphology_class_counts"]["SINGLE_CELL"] == 1
    assert summary["bwer_candidate_null"] == 1
    assert summary["hail_nuclei_null"] == 1


def test_attach_advanced_with_rotation_evidence():
    bundle = models.EngineBundle("ok", "2026-11-20T10:00:00Z", "test")
    c = _candidate(hail={"vil_density_g_m3": {"value": 5.0,
                                              "null_reason": None}})
    bundle.convective_structures = [c]
    bundle.advanced_cappi_levels = 8
    bundle.doppler_analysis = {
        "signature_total": 1,
        "state_counts": {"ROTATION_DETECTED": 1},
        "source": "OPERA FR VRADH (sintetico test)",
    }
    bundle.mesocyclone_tracks = {
        "tracks": [{"persistence": 4}],
    }
    _, summary = adv.attach_advanced(bundle)
    assert summary["rotation_evidence_available"] is True
    assert summary["supercell_null_reason"] is None
    assert c["morphology_class"]["value"] == "HP_SUPERCELL_CANDIDATE"


def test_no_skill_metrics_in_advanced_payloads():
    f = adv.classify_morphology(_candidate())
    flat = repr(f).lower()
    for bad in ("'pod'", "'far'", "'csi'"):
        assert bad not in flat


def test_rotation_null_reason_constant_matches_engine():
    # Nessun VRADH italiano: il motivo e' quello del motore.
    assert "no_opera_vradh_data" in adv.ROTATION_NULL_REASON


# ---------------------------------------------------------------------------
# 8) Pubblicazione dei conteggi in latest.json / convective_structures.json
# ---------------------------------------------------------------------------
def test_output_publishes_fase7_counts():
    from radar_engine import output
    bundle = models.EngineBundle("ok", "2026-11-20T10:00:00Z", "test")
    c = _candidate()
    bundle.convective_structures = [c]
    bundle.advanced_cappi_levels = 2
    adv.attach_advanced(bundle)
    latest = output.build_latest(bundle)
    assert latest["advanced_morphology_class_counts"]["SINGLE_CELL"] == 1
    assert latest["advanced_lifecycle_stage_counts"]["MATURE"] == 1
    assert latest["advanced_supercell_null_reason"] == adv.ROTATION_NULL_REASON
    assert latest["advanced_bwer_candidate_true"] == 0
    cs = output.build_convective_structures(bundle)
    assert cs["summary"]["advanced_morphology_class_counts"]["SINGLE_CELL"] == 1
    assert cs["summary"]["advanced_hail_nuclei_tracks_total"] == 0
    assert cs["rotation_measurable"] is False  # invariato
