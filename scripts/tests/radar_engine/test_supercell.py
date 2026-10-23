#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del descrittore di struttura convettiva (SOI 0-100).

La 1.2.4.17 ha rinominato il modulo `supercell.py` in `convective_structure.py`
e l'indice `ssi` in `soi`; questo file continua a importare il modulo con il
NOME STORICO per verificare che l'alias di compatibilita' regga, ma asserisce
i nomi nuovi (soi/level/rotation_measurable/merged_from/confidence derivata).
"""

import json

from radar_engine import convective_structure as cs
from radar_engine import models
from radar_engine import output
from radar_engine import supercell as sc_alias
from radar_engine.config import CONFIG

from conftest import make_cell, make_track


T0 = 1_777_027_200_000
CFG = CONFIG["convective_structure"]


def _make_tracked(frames=5, dbz=52.0, mean_dbz=48.0, area0=200.0, growth=0.4,
                  solidity=0.92, compactness=2.5, org=90, track_id=1):
    """Track con motion (Organization Score) coerente con la pipeline Fase 1."""
    lo = 12.0
    dlon = 0.01
    cells = []
    for i in range(frames):
        area = area0 * (1 + growth * i / max(frames - 1, 1))
        cells.append(make_cell(
            lo + i * dlon, 41.0, T0 + i * 300000,
            area_km2=area, max_dbz=dbz, mean_dbz=mean_dbz, p90_dbz=mean_dbz,
            ecc=0.5, solidity=solidity, compactness=compactness,
            frame_index=i, cell_id="c-{}-{}".format(track_id, i)))
    track = make_track(cells, track_id)
    track.motion = {
        "_avg_solidity": solidity,
        "_avg_compactness": compactness,
        "organization_score": org,
        "classification": "Organized Convective Cell",
        "velocity_kmh": 45.0,
        "direction_toward_deg": 90.0,
        "distance_km": 5.0,
        "duration_min": 5.0 * (frames - 1),
        "area_growth_pct": round(growth * 100, 1),
        "intensity_delta_dbz": 0.0,
    }
    return track


def _bundle():
    return models.EngineBundle("ok", "2026-04-30T10:00:00Z", output.SOURCE_LABEL)


# ---------------------------------------------------------------------------
# Alias di import (nessun test esistente deve rompersi)
# ---------------------------------------------------------------------------
def test_alias_module_reexports_same_functions():
    assert sc_alias.evaluate is cs.evaluate
    assert sc_alias.structural_organization_index is cs.structural_organization_index
    assert sc_alias.supercell_signature_index is cs.structural_organization_index
    assert sc_alias.DATA_LIMITS_NOTE == cs.DATA_LIMITS_NOTE


def test_alias_index_equals_new_index():
    track = _make_tracked()
    legacy = sc_alias.supercell_signature_index(track)
    new = cs.structural_organization_index(track)
    assert legacy == new


# ---------------------------------------------------------------------------
# Indice, pesi, livelli
# ---------------------------------------------------------------------------
def test_soi_range_and_components_bounds():
    for frames, dbz in [(2, 22.0), (5, 40.0), (7, 55.0)]:
        soi, level, candidate, gates, comp = cs.structural_organization_index(
            _make_tracked(frames=frames, dbz=dbz))
        assert 0 <= soi <= 100
        assert set(comp.keys()) == set(CFG["weights"].keys())
        for v in comp.values():
            assert 0.0 <= v <= 1.0
        assert isinstance(candidate, bool)
        assert set(gates.keys()) == {"intensity_core_dbz", "min_frames",
                                     "organization_organized"}


def test_weights_sum_to_one():
    assert abs(sum(CFG["weights"].values()) - 1.0) < 1e-9


def test_levels_are_the_new_names():
    assert [name for _lo, _hi, name in CFG["bands"]] == [
        "unorganized", "organized", "strong", "intense"]
    for _lo, _hi, name in CFG["bands"]:
        assert cs.classify_level(_lo) == name


def test_unorganized_track_not_emitted():
    soi, level, candidate, gates, _comp = cs.structural_organization_index(
        _make_tracked(frames=2, dbz=22.0, mean_dbz=20.0, area0=60.0, org=15))
    assert soi < 50
    assert level == "unorganized"
    assert candidate is False
    assert gates["intensity_core_dbz"] is False


def test_intensity_gate_blocks_record():
    """SOI alto (strong/intense) ma core <45 dBZ: candidate=False."""
    soi, level, candidate, gates, _comp = cs.structural_organization_index(
        _make_tracked(frames=7, dbz=35.0, mean_dbz=32.0, area0=200.0,
                      growth=0.5, org=100))
    assert soi >= 65
    assert level in ("strong", "intense")
    assert gates["intensity_core_dbz"] is False
    assert candidate is False


def test_strong_track_is_candidate():
    soi, level, candidate, gates, _comp = cs.structural_organization_index(
        _make_tracked(frames=7, dbz=55.0, mean_dbz=52.0, area0=250.0,
                      growth=0.6, org=90))
    assert soi >= 80
    assert level == "intense"
    assert gates["intensity_core_dbz"] is True
    assert gates["min_frames"] is True
    assert candidate is True


# ---------------------------------------------------------------------------
# Confidenza DERIVATA (non la costante "low")
# ---------------------------------------------------------------------------
def test_confidence_is_derived_from_gates_and_persistence():
    weak = cs.derive_confidence(
        gates={"a": False, "b": False, "c": False}, n_points=2, coverage=0.0)
    strong = cs.derive_confidence(
        gates={"a": True, "b": True, "c": True}, n_points=6, coverage=1.0)
    assert weak["confidence"] == "very_low"
    assert strong["confidence"] == "high"
    assert weak["score"] < strong["score"]
    assert set(strong["terms"]) >= {"gates", "persistence", "coverage"}


def test_confidence_capped_without_data_coverage():
    """Senza copertura dati (Fase 2 non ancora eseguita) il cap e' 'medium'."""
    conf = cs.derive_confidence(
        gates={"a": True, "b": True, "c": True}, n_points=10, coverage=None)
    assert conf["capped"] is True
    assert conf["confidence"] == "medium"
    assert conf["terms"]["coverage"] is None
    assert abs(sum(conf["terms"]["weights_effective"].values()) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# evaluate(): dedup, latest frame, status
# ---------------------------------------------------------------------------
def test_evaluate_emits_only_candidates():
    b = _bundle()
    b.cells_by_frame = [[make_cell(12.0, 41.0, T0)],
                        [make_cell(12.01, 41.0, T0 + 300000)]]
    strong = _make_tracked(frames=2, dbz=54.0, org=90)
    weak = _make_tracked(frames=2, dbz=24.0, org=10, track_id=2)
    b.tracks = [strong, weak]
    cs.evaluate(b, CFG)
    assert len(b.convective_structures) == 1
    rec = b.convective_structures[0]
    assert rec["track_id"] == 1
    assert rec["candidate"] is True
    assert rec["track_type"] == "cell"
    assert rec["on_latest_frame"] is True
    for key in ("structure_id", "soi", "n_points", "level", "phase", "status",
                "position", "intensity", "organization", "motion",
                "components", "gates", "confidence", "confidence_detail",
                "rotation_measurable", "rotation_null_reason", "merged_from",
                "first_seen", "last_seen"):
        assert key in rec
    assert rec["rotation_measurable"] is False
    assert rec["merged_from"] == ["cell"]
    assert rec["confidence"] in ("very_low", "low", "medium", "high")
    assert b.structure_tracks_evaluated == 2


def test_latest_frame_index_is_the_real_index():
    """D1: un frame VUOTO in mezzo non deve abbassare l'indice piu' recente."""
    b = _bundle()
    b.cells_by_frame = [[make_cell(12.0, 41.0, T0)], [],
                        [make_cell(12.02, 41.0, T0 + 600000)]]
    track = _make_tracked(frames=3, dbz=54.0, org=90)
    # la track ha punti ai frame 0,1,2: l'ultimo E' il frame piu' recente
    b.tracks = [track]
    cs.evaluate(b, CFG)
    rec = b.convective_structures[0]
    assert rec["on_latest_frame"] is True


def test_status_active_when_observed_on_latest_frame():
    """D2: track chiusa a fine ciclo ma vista sull'ultimo frame -> 'active'."""
    b = _bundle()
    b.cells_by_frame = [[make_cell(12.0, 41.0, T0)],
                        [make_cell(12.01, 41.0, T0 + 300000)]]
    track = _make_tracked(frames=2, dbz=54.0, org=90)
    track.status = models.STATUS_DEAD        # come dopo complete_cycles()
    b.tracks = [track]
    cs.evaluate(b, CFG)
    rec = b.convective_structures[0]
    assert rec["status"] == "active"
    assert rec["track_status_raw"] == models.STATUS_DEAD


def test_dedup_one_record_per_system():
    """C: cella + storm object dello stesso sistema -> UN record merged."""
    import types
    b = _bundle()
    b.cells_by_frame = [[make_cell(12.0, 41.0, T0)],
                        [make_cell(12.01, 41.0, T0 + 300000)]]
    cell_track = _make_tracked(frames=2, dbz=54.0, org=90, track_id=1)
    storm_track = _make_tracked(frames=2, dbz=54.0, org=90, track_id=7)
    # lo storm object del frame 1 contiene la cella dell'ultimo punto cella
    b.storm_objects_by_frame = [
        [types.SimpleNamespace(cell_ids=["c-1-0"], track_id=7)],
        [types.SimpleNamespace(cell_ids=["c-1-1"], track_id=7)],
    ]
    b.tracks = [cell_track]
    b.storm_tracks = [storm_track]
    cs.evaluate(b, CFG)
    assert len(b.convective_structures) == 1
    rec = b.convective_structures[0]
    assert rec["track_type"] == "storm_object"
    assert rec["merged_from"] == ["cell", "storm_object"]
    assert rec["merged_cell"]["track_id"] == 1
    assert rec["merged_cell"]["soi"] == rec["soi"]


def test_dedup_keeps_distinct_systems_separate():
    b = _bundle()
    b.cells_by_frame = [[make_cell(12.0, 41.0, T0)],
                        [make_cell(12.01, 41.0, T0 + 300000)]]
    b.tracks = [_make_tracked(frames=2, dbz=54.0, org=90, track_id=1)]
    b.storm_tracks = [_make_tracked(frames=2, dbz=54.0, org=90, track_id=9)]
    cs.evaluate(b, CFG)
    assert len(b.convective_structures) == 2
    kinds = sorted(r["track_type"] for r in b.convective_structures)
    assert kinds == ["cell", "storm_object"]


def test_data_limits_note_is_explicit():
    assert "Doppler" in cs.DATA_LIMITS_NOTE
    assert "riflettivita'" in cs.DATA_LIMITS_NOTE
    assert "rotation_measurable=false" in cs.DATA_LIMITS_NOTE


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def test_convective_structures_json_output(tmp_path):
    b = _bundle()
    b.radar_timestamp_iso = "2026-04-30T10:00:00Z"
    b.cells_by_frame = [[], []]
    b.tracks = [_make_tracked(frames=2, dbz=54.0, org=90)]
    cs.evaluate(b, CFG)
    out = str(tmp_path / "radar")
    paths = output.write_outputs(b, out, engine_meta={"phase": 1})
    assert "convective_structures.json" in paths
    with open(paths["convective_structures.json"], encoding="utf-8") as fh:
        payload = json.load(fh)
    assert isinstance(payload["structures"], list)
    assert len(payload["structures"]) == 1
    assert payload["summary"]["structures"] == 1
    assert payload["data_limits"] == cs.DATA_LIMITS_NOTE
    assert payload["rotation_measurable"] is False
    with open(paths["latest.json"], encoding="utf-8") as fh:
        latest = json.load(fh)
    assert latest["structure_candidates"] == 1
    assert output.validate_outputs(out) == []


def test_empty_bundle_ok(tmp_path):
    import os as _os
    b = _bundle()
    b.cells_by_frame = []
    b.tracks = []
    cs.evaluate(b, CFG)
    out = str(tmp_path / "radar")
    output.write_outputs(b, out)
    with open(_os.path.join(out, "convective_structures.json"),
              encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["structures"] == []
    assert payload["summary"]["structures"] == 0
