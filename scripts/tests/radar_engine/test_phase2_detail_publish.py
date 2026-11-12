#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pubblicazione del DETTAGLIO Fase 2 nel record (2026-11-12).

Difetto misurato su 40 record pubblicati (18 run del 2026-11-12): `soi_v2` e
`hook` uscivano come numeri nudi.
  - `soi_v2` senza `weights_used`/`components_*`: non si poteva verificare
    perche' un record valesse 41 ne' quali componenti fossero esclusi;
  - `hook = 0.0` senza le feature: non era attribuibile a un gate
    (core < 25 px oppure bay_wrap < 150 deg). La ricostruzione offline ha
    mostrato core_pixels max 18 su 40 record: falliva il gate del CORE.

Questi test falliscono sul codice precedente (attributi/funzioni assenti) e
passano con `soi_v2_detail` + `hook_features` pubblicati.
"""

import numpy as np
import pytest

from radar_engine import main as engine_main
from radar_engine.phase2 import aggregate, hook


# ---------------------------------------------------------------------------
# 1. soi_v2_detail: chiavi, pesi, zeri misurati
# ---------------------------------------------------------------------------
def _v2(**kw):
    args = {k: kw.get(k) for k in aggregate.COMPONENT_KEYS}
    return aggregate.aggregate_ssi_v2(
        args["base"], args["hook"], args["structure"], args["env"],
        args["ot"], args["lightning"])


def test_soi_v2_detail_has_every_declared_key():
    detail = engine_main._soi_v2_detail(
        _v2(base=65.0, hook=0.0, structure=1.2, env=9.2, ot=0.0))
    assert set(detail) == set(engine_main._SOI_V2_DETAIL_KEYS)
    assert set(detail) == {
        "weights_used", "weights_used_sum", "sum_present_weights",
        "components_present", "components_missing", "components_invalid",
        "components_zero_measured", "level", "level_thresholds", "status"}


def test_soi_v2_detail_keys_all_exist_in_aggregate_output():
    v2 = _v2(base=70.0, hook=10.0, structure=20.0, env=30.0, ot=5.0,
             lightning=2.0)
    missing = [k for k in engine_main._SOI_V2_DETAIL_KEYS if k not in v2]
    assert missing == []


def test_soi_v2_detail_weights_sum_one_and_zero_measured():
    """Record reale CS-2026-11-12T17-cel101: hook=0.0 e ot=0.0 MISURATI,
    lightning assente -> i pesi usati sommano 1.0 e gli zeri sono elencati."""
    v2 = _v2(base=65.0, hook=0.0, structure=1.2, env=9.2, ot=0.0)
    detail = engine_main._soi_v2_detail(v2)
    assert abs(sum(detail["weights_used"].values()) - 1.0) <= 1e-9
    assert detail["weights_used_sum"] == pytest.approx(1.0)
    assert detail["components_zero_measured"] == ["hook", "ot"]
    assert detail["components_missing"] == ["lightning"]
    assert detail["status"] == "partial"
    assert detail["level"] == v2["level"]
    assert detail["level_thresholds"] == v2["level_thresholds"]
    # il peso NON e' quello richiesto: lightning e' escluso e rinormalizzato
    assert detail["weights_used"]["base"] == pytest.approx(0.60 / 0.97)
    assert detail["sum_present_weights"] == pytest.approx(0.97)


def test_soi_v2_detail_raises_if_aggregate_loses_a_key():
    """Un dettaglio a meta' non deve passare in silenzio."""
    with pytest.raises(KeyError):
        engine_main._soi_v2_detail({"soi_v2": 41})


# ---------------------------------------------------------------------------
# 2. hook_features: feature dell'ultima finestra + gate falliti
# ---------------------------------------------------------------------------
def _windows(grid, n=1):
    mask = np.isfinite(grid)
    return [(grid, mask)] * n


def test_hook_detail_flags_a_tiny_core_as_core_gate_failure():
    """Core 4x4 = 16 px (< 25): il gate che fallisce e' `core_pixels`.

    E' il caso MISURATO sui 40 record reali (core_pixels max 18): senza il
    dettaglio l'uscita era solo `hook=0.0` e non si sapeva quale gate
    fosse responsabile."""
    grid = np.full((60, 60), np.nan, dtype="float64")
    grid[28:32, 28:32] = 50.0
    score, feats = hook.hook_score_footprint_detail(_windows(grid))
    assert score == 0.0
    assert feats["core_pixels"] == 16
    assert feats["intensity"] == pytest.approx(50.0)
    assert feats["failing_gates"][0] == "core_pixels"
    assert "core_pixels" in feats["failing_gates"]
    assert feats["hook_candidate"] is False
    assert feats["gates"]["core_pixels_min"] == 25


def test_hook_detail_convex_core_passes_core_gate_but_fails_bay():
    """Disco convesso >= 25 px: il core passa, la bay no (nessun uncino)."""
    rows, cols = np.ogrid[:80, :80]
    grid = np.full((80, 80), np.nan, dtype="float64")
    disk = (rows - 40) ** 2 + (cols - 40) ** 2 <= 8 ** 2
    grid[disk] = 52.0
    score, feats = hook.hook_score_footprint_detail(_windows(grid))
    assert score == 0.0
    assert feats["core_pixels"] >= 25
    assert feats["core_pixels"] == int(disk.sum())
    assert "core_pixels" not in feats["failing_gates"]
    assert feats["bay_wrap_deg"] == 0.0
    assert "bay_wrap_deg" in feats["failing_gates"]
    assert feats["concavity"] <= 1.0


def test_hook_detail_reports_history_and_persistence():
    """3 finestre: history_scores = prime 2, score_last_frame = ultima."""
    grid = np.full((60, 60), np.nan, dtype="float64")
    grid[28:32, 28:32] = 50.0
    score, feats = hook.hook_score_footprint_detail(_windows(grid, n=3))
    assert feats["frames"] == 3
    assert len(feats["history_scores"]) == 2
    assert feats["score_last_frame"] == 0.0
    assert feats["persistence_score"] == score == 0.0


def test_hook_detail_none_without_a_complete_window():
    assert hook.hook_score_footprint_detail([]) == (None, None)
    assert hook.hook_score_footprint_detail(None) == (None, None)
    # una finestra senza pixel validi invalida TUTTA la sequenza
    empty = np.full((10, 10), np.nan, dtype="float64")
    assert hook.hook_score_footprint_detail([(empty, np.isfinite(empty))]) == (
        None, None)
    # mask con shape diverso: input malformato -> (None, None), non eccezione
    good = np.full((10, 10), 50.0, dtype="float64")
    assert hook.hook_score_footprint_detail([(good, np.ones((3, 3), bool))]) == (
        None, None)


def test_hook_score_footprint_is_the_score_of_the_detail():
    rows, cols = np.ogrid[:80, :80]
    grid = np.full((80, 80), np.nan, dtype="float64")
    grid[(rows - 40) ** 2 + (cols - 40) ** 2 <= 8 ** 2] = 52.0
    windows = _windows(grid, n=2)
    score, _feats = hook.hook_score_footprint_detail(windows)
    assert hook.hook_score_footprint(windows) == score


# ---------------------------------------------------------------------------
# 3. Il RECORD pubblicato riceve entrambi i blocchi
# ---------------------------------------------------------------------------
class _FakeBundle:
    def __init__(self):
        self.convective_structures = [{
            "soi": 65, "hook": None, "structure": None, "env": None,
            "ot": None, "lightning": None, "gates": {}, "n_points": 5,
            "level": "strong",
        }]
        self.frames = []
        self.warnings = []
        self.phase2 = None
        self.as_of_mode = None
        self.frame_slots_ms = []
        self.dpc_frames = []
        self.raster_by_frame_index = None
        self.cells_by_frame = []
        self.status = "ok"


def test_phase2_evaluate_publishes_soi_v2_detail_and_hook_features():
    """Integrazione senza rete: ambiente e AFA disattivati, nessun frame.

    `hook_features` e' None (nessuna posizione/frame) ma la CHIAVE deve
    esistere; `soi_v2_detail` deve essere il dict completo."""
    cfg = {
        "phase2": {
            "enabled": True,
            "environment": {"enabled": False},
            "lightning": {"afa": {"enabled": False}},
        },
        "convective_structure": {},
    }
    bundle = _FakeBundle()
    engine_main._phase2_evaluate(bundle, cfg)
    cand = bundle.convective_structures[0]
    assert "hook_features" in cand
    assert cand["hook_features"] is None
    assert isinstance(cand["soi_v2_detail"], dict)
    assert set(cand["soi_v2_detail"]) == set(engine_main._SOI_V2_DETAIL_KEYS)
    assert cand["soi_v2_detail"]["components_missing"] == [
        "hook", "structure", "env", "ot", "lightning"]
    assert abs(sum(cand["soi_v2_detail"]["weights_used"].values()) - 1.0) <= 1e-9
    assert cand["soi_v2"] == 65
