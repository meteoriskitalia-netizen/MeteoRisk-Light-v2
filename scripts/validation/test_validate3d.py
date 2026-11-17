#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test della validazione 3D. DATI SINTETICI SOLO QUI (mai nel sistema operativo).

I test sul motore non importano radar_engine: estraggono le FUNZIONI REALI dal
file main.py consegnato (AST + exec in namespace isolato) e le eseguono. Cosi'
si verifica il codice che verra' distribuito, non una sua copia.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import validate3d_collect as col  # noqa: E402
import validate3d_events as evs  # noqa: E402
import validate3d_metrics as met  # noqa: E402

ENGINE_DIR = os.path.abspath(os.path.join(
    HERE, "..", "radar_engine"))


# --------------------------------------------------------------------------
# metriche
# --------------------------------------------------------------------------
def test_binary_counts_explicit_definitions():
    m = met.binary_counts(8, 2, 2, 88)
    assert m["pod"] == pytest.approx(0.8)
    assert m["recall"] == m["pod"]
    assert m["far_ratio"] == pytest.approx(0.2)
    assert m["far_rate"] == pytest.approx(2.0 / 90.0)
    assert m["csi"] == pytest.approx(8.0 / 12.0)
    assert m["precision"] == pytest.approx(0.8)
    assert m["f1"] == pytest.approx(0.8)
    assert m["definitions"]["far_ratio"] != m["definitions"]["far_rate"]


def test_zero_denominator_is_none_not_zero():
    m = met.binary_counts(0, 0, 0, None)
    for key in ("pod", "far_ratio", "far_rate", "csi", "precision", "f1"):
        assert m[key] is None, key
    assert m["pod_ci95"] == [None, None]


def test_wilson_interval_brackets_estimate():
    lo, hi = met.wilson_interval(5, 10)
    assert lo < 0.5 < hi
    assert 0.0 <= lo < hi <= 1.0
    assert met.wilson_interval(0, 0) == (None, None)


def test_minimum_sample_gate_blocks_small_samples():
    small = met.binary_counts(3, 1, 3, 4)
    gate = met.minimum_sample(small)
    assert gate["passed"] is False
    assert gate["metric_status"] == "incomplete"
    assert any("positive_labeled" in r for r in gate["reasons"])
    big = met.binary_counts(300, 50, 40, 500)
    assert met.minimum_sample(big)["passed"] is True


def test_paired_baseline_on_same_rows():
    rows = [
        {"label_afa": 1, "baseline_organized": 0, "new_organized": 1},
        {"label_afa": 1, "baseline_organized": 0, "new_organized": 1},
        {"label_afa": 0, "baseline_organized": 1, "new_organized": 0},
        {"label_afa": 0, "baseline_organized": 0, "new_organized": 1},
        {"label_afa": 1, "baseline_organized": 1, "new_organized": 1},
    ]
    r = met.paired_baseline_compare(rows)
    assert r["n_paired"] == 5
    assert r["new_fixed_baseline_errors"] == 3
    assert r["new_broke_baseline_correct"] == 1
    assert r["baseline"]["tp"] == 1 and r["new"]["tp"] == 3


def test_confusion_from_pairs_radius():
    obs = [{"lat": 45.0, "lon": 9.0}, {"lat": 46.0, "lon": 10.0}]
    pred = [{"lat": 45.01, "lon": 9.01}, {"lat": 40.0, "lon": 10.0}]
    c = met.confusion_from_pairs(obs, pred, radius_km=20.0)
    assert c["tp"] == 1 and c["fp"] == 1 and c["fn"] == 1


def test_field_continuity_counts_present_and_stable():
    ev = {"rows": [
        {"ts_ms": 0, "morphology_organization": {"classification": "organized_vertical"},
         "label_afa": 1, "baseline_field_available": False},
        {"ts_ms": 300000, "morphology_organization": {"classification": "organized_vertical"},
         "label_afa": 1, "baseline_field_available": False},
        {"ts_ms": 600000, "morphology_organization": {"classification": None},
         "label_afa": 0, "baseline_field_available": False},
    ]}
    r = met.field_continuity([ev])
    assert r["pairs"] == 2
    assert r["field_present_both"] == 1
    assert r["stable_when_present"] == 1
    assert r["baseline_null_pairs"] == 2
    assert r["detection_vs_afa"]["tp"] == 2


# --------------------------------------------------------------------------
# eventi / split
# --------------------------------------------------------------------------
def _row(day, hour, lat=45.0, lon=9.0):
    iso = f"2026-11-{day:02d}T{hour:02d}:00:00Z"
    return {"slot_iso": iso, "ts_ms": evs.iso_to_ms(iso),
            "lat": lat, "lon": lon}


def test_events_grouped_by_time_and_space():
    rows = [_row(1, 0), _row(1, 1), _row(1, 2),          # evento 1
            _row(5, 0),                                   # evento 2
            _row(10, 0), _row(10, 1)]                     # evento 3
    events, meta = evs.group_events(rows)
    assert meta["n_events"] == 3
    assert meta["n_rows_valid"] == 6
    assert [len(e["rows"]) for e in events] == [3, 1, 2]


def test_split_is_per_event_and_chronological():
    rows = [_row(d, h) for d, h in ((1, 0), (1, 1), (5, 0), (10, 0), (10, 1))]
    events, _ = evs.group_events(rows)
    sp = evs.chronological_split(events, train_fraction=0.6, min_gap_hours=6)
    assert sp["passed"] is True
    assert not (set(sp["train_events"]) & set(sp["test_events"]))
    assert sp["train_end_iso"] < sp["test_start_iso"]
    assert sp["n_train"] + sp["n_test"] == len(events)


def test_split_refuses_when_no_gap():
    rows = [_row(1, h) for h in range(6)]
    events, _ = evs.group_events(rows, gap_min=1.0)
    sp = evs.chronological_split(events, min_gap_hours=240)
    assert sp["passed"] is False
    assert "no_boundary" in sp["reason"]


# --------------------------------------------------------------------------
# etichetta AFA (fetcher iniettato: dati sintetici SOLO nel test)
# --------------------------------------------------------------------------
def test_afa_label_decision_with_injected_fetcher(monkeypatch):
    import validate3d_afa as afa

    class FakeAfa:
        @staticmethod
        def count_within(frame, lon, lat, radius_km):
            return {"active": bool(frame.get("active")), "active_pixels": 1}

    monkeypatch.setattr(afa, "_modules", lambda: (FakeAfa, None))
    ts = evs.iso_to_ms("2026-11-16T12:00:00Z")

    def fetcher(slot_ms):
        return {"status": "ok", "served_iso": None,
                "served_offset_min": 0.0,
                "frame": {"active": slot_ms % 2 == 0}}

    pos = afa.label_cell(45.0, 9.0, ts, fetcher=fetcher)
    assert pos["label_afa"] is True
    assert pos["n_usable_slots"] == pos["n_slots"] == 13

    neg = afa.label_cell(45.0, 9.0, ts,
                         fetcher=lambda s: {"status": "ok",
                                            "served_offset_min": 0.0,
                                            "frame": {"active": False}})
    assert neg["label_afa"] is False

    def partial(slot_ms):
        if slot_ms % 3 == 0:
            return {"status": "slot_missing", "served_offset_min": None,
                    "null_reason": "afa_service_exception:InvalidDimensionValue"}
        return {"status": "ok", "served_offset_min": 0.0,
                "frame": {"active": False}}

    null = afa.label_cell(45.0, 9.0, ts, fetcher=partial)
    assert null["label_afa"] is None
    assert "afa_window_incomplete" in null["label_null_reason"]


# --------------------------------------------------------------------------
# wiring del motore: FUNZIONI REALI estratte dal sorgente consegnato
# --------------------------------------------------------------------------
def _extract_functions(path, names):
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    wanted = [n for n in tree.body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name in names]
    assert {n.name for n in wanted} == set(names)
    ns = {}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), path, "exec"), ns)
    return ns


def test_apply_validate_3d_does_not_change_normal_runs():
    ns = _extract_functions(os.path.join(ENGINE_DIR, "main.py"),
                            {"_apply_validate_3d", "_validate_3d_state"})
    cfg = {"phase2": {"structure": {
        "hail_gate": {"enabled": True},
        "cappi_fetch": {"allow_previous_slot": True},
        "mesh_levels_enabled": True}}}
    before = copy.deepcopy(cfg)
    assert ns["_apply_validate_3d"](cfg, False) is None
    assert cfg == before
    assert ns["_validate_3d_state"](cfg) is None


def test_apply_validate_3d_forces_full_set_and_strict_slot():
    ns = _extract_functions(os.path.join(ENGINE_DIR, "main.py"),
                            {"_apply_validate_3d", "_validate_3d_state"})
    cfg = {"phase2": {"structure": {
        "hail_gate": {"enabled": True},
        "cappi_fetch": {"allow_previous_slot": True},
        "mesh_levels_enabled": False}}}
    decl = ns["_apply_validate_3d"](cfg, True)
    assert decl["force_full_cappi"] is True
    assert cfg["phase2"]["structure"]["hail_gate"]["enabled"] is False
    assert cfg["phase2"]["structure"]["mesh_levels_enabled"] is True
    assert (cfg["phase2"]["structure"]["cappi_fetch"]
            ["allow_previous_slot"]) is False
    assert ns["_validate_3d_state"](cfg)["enabled"] is True


def test_engine_source_declares_counterfactual_and_flag():
    with open(os.path.join(ENGINE_DIR, "main.py"), "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "--validate-3d" in src
    assert "validation_counterfactual" in src
    assert "full_set=gate[\"fetch_full_set\"]" in src
    with open(os.path.join(ENGINE_DIR, "fetch.py"), "r", encoding="utf-8") as fh:
        fsrc = fh.read()
    assert "validation_mode=False" in fsrc
    assert "allow_previous_slot = False" in fsrc
    with open(os.path.join(ENGINE_DIR, "config.py"), "r", encoding="utf-8") as fh:
        csrc = fh.read()
    assert "\"validate_3d\"" in csrc and "\"enabled\": False" in csrc


def test_morphology_lonlat_convention_is_fixed():
    """Defetto reale: 17/17 strutture con no_3d_cell_within_20km.

    `structure.position` e' [lon, lat], `rc_to_latlon` restituisce (lat, lon)
    e `haversine_km` vuole (lon, lat). La funzione estratta dal sorgente
    consegnato deve dare ~0 km quando i punti coincidono.
    """
    path = os.path.join(ENGINE_DIR, "morphology.py")
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef)
          and n.name == "_lonlat_distance_km"]
    assert len(fn) == 1

    def haversine_km(a, b):  # stessa formula di tracking.haversine_km
        return met._haversine_km(a[1], a[0], b[1], b[0])

    ns = {"haversine_km": haversine_km}
    exec(compile(ast.Module(body=fn, type_ignores=[]), path, "exec"), ns)
    dist = ns["_lonlat_distance_km"]([13.92339, 45.09728],
                                     (45.09728, 13.92339))
    assert dist < 0.5, dist
    legacy = ns["_lonlat_distance_km"]([45.09728, 13.92339],
                                       (45.09728, 13.92339),
                                       order="lat_lon_legacy")
    assert legacy < 0.5, legacy
    # il vecchio ordine (lat,lon) passato a haversine dava un valore assurdo
    old = met._haversine_km(13.92339, 45.09728, 45.09728, 13.92339)
    assert old > 1000.0, old


# --------------------------------------------------------------------------
# piano di raccolta (misure reali inglobate nel modulo)
# --------------------------------------------------------------------------
def test_plan_uses_measured_numbers_and_flags_budget():
    p5 = col.plan("2026-10-19", 30, 5)
    p20 = col.plan("2026-10-19", 30, 20)
    assert p5["total_engine_runs"] == 30 * 288
    assert p20["total_engine_runs"] == 30 * 72
    assert p5["total_cappi_bytes_range"][0] > p20["total_cappi_bytes_range"][0]
    assert "400 MB/giorno" in p5["budget_note"]
    assert p5["values_NOT_measured"]


def test_finalize_end_to_end_on_test_only_observations(tmp_path):
    """Dati SINTETICI scritti solo in tmp_path (fixture pytest), mai nel repo."""
    slots = tmp_path / "slots"
    slots.mkdir()
    for i in range(8):
        day = 1 + i * 2
        event_dir = slots / f"2026-11-{day:02d}T12-00-00Z"
        event_dir.mkdir()
        rows = []
        for j in range(3):
            iso = f"2026-11-{day:02d}T{12 + j:02d}:00:00Z"
            y = int(i % 2 == 0)
            rows.append({
                "slot_iso": iso, "ts_ms": evs.iso_to_ms(iso),
                "lat": 45.0 + i * 0.01, "lon": 9.0, "structure_id": f"S{i}",
                "label_afa": y,
                "baseline_organized": 0, "new_organized": y,
                "baseline_field_available": False,
                "morphology_organization": {
                    "classification": ("organized_vertical" if y
                                       else "not_organized_vertical")},
                "values": {"echo_top_m": 7000 if y else 3000,
                           "vertical_extent_km": 5.0 if y else 2.0,
                           "vil_density_g_m3": 4.0 if y else 2.0},
            })
        with open(event_dir / "observations.jsonl", "w",
                  encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    report, test_rows = col.finalize(str(tmp_path))
    assert report["split"]["passed"] is True
    assert report["calibration"]["dataset_used"].startswith("TRAIN ONLY")
    assert report["calibration"]["test_touched"] is False
    assert report["test"]["n_labeled"] > 0
    assert report["test"]["shipped_rule"]["pod"] is not None
    assert report["minimum_sample"]["metric_status"] in ("complete",
                                                         "incomplete")
    assert report["status"] == report["minimum_sample"]["metric_status"]
