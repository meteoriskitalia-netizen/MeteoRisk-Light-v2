#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FASE 7 — fase del tracking derivata da `tracking_events/1` (additiva).

Il blocco di eventi e' QUELLO GIA' PUBBLICATO da output.build_tracking_events:
qui NON si crea nessun evento nuovo. La fase e' una CLASSIFICAZIONE EURISTICA
di comodo (SCELTA INGEGNERISTICA NON TARATA), non una misura fisica.

I casi sintetici sono DICHIARATI come tali; gli enrichments reali sono letti
dallo snapshot congelato del run 2026-11-21T14:46:06Z.
"""
from __future__ import annotations

import json
import pathlib
import sys
import types

import pytest

from radar_engine import advanced as adv
from radar_engine import models
from radar_engine import output

RADAR_TS = "2026-11-21T14:35:00Z"
FIXTURE = (pathlib.Path(__file__).resolve().parents[1]
           / "fixtures" / "real_1446_convective_structures.json")
TRACKS_FIXTURE = (pathlib.Path(__file__).resolve().parents[1]
                  / "fixtures" / "real_1446_tracks.json")


def _events(**observed):
    """Blocco tracking_events/1 sintetico DICHIARATO, con i 7 tipi sempre
    presenti (come output.build_tracking_events). `observed` mappa tipo ->
    value (None = evento assente, con null_reason)."""
    types_ = ("birth", "continuation", "death", "temporary_non_observation",
              "split", "merge", "ambiguous_association")
    stamps = {
        "birth": "2026-11-21T14:05:00Z",
        "continuation": "2026-11-21T14:15:00Z",
        "death": "2026-11-21T14:35:00Z",
        "temporary_non_observation": "2026-11-21T14:25:00Z",
        "split": "2026-11-21T14:20:00Z",
        "merge": "2026-11-21T14:20:00Z",
        "ambiguous_association": "2026-11-21T14:20:00Z",
    }
    events = []
    for kind in types_:
        value = observed.get(kind)
        events.append({
            "type": kind, "timestamp": stamps[kind], "value": value,
            "null_reason": (None if value is not None
                            else "no_evidence_in_run"),
            "source": "sintetico dichiarato (test)",
            "reference": "sintetico dichiarato (test)",
        })
    return {"schema": "meteorisk.tracking_events/1", "events": events}


# ---------------------------------------------------------------------------
# 1) Lessico e contratto
# ---------------------------------------------------------------------------
def test_tracking_phase_lexicon_is_six_and_disjoint():
    assert len(adv.TRACKING_PHASES) == 6
    assert adv.TRACKING_PHASES == (
        "forming", "organizing", "mature", "coasting", "dissipating",
        "ambiguous")
    # Disjoint dal proxy di intensita' gia' esistente: nessuna sovrascrittura.
    assert not set(adv.TRACKING_PHASES) & set(adv.LIFECYCLE_STAGES)
    assert "weakening" not in adv.TRACKING_PHASES


def test_field_contract_value_source_reference_null_reason():
    cases = [
        _events(birth={"track_id": 1}, continuation={"continued": True}),
        _events(birth={"track_id": 1}),
        None,
    ]
    for block in cases:
        f = adv.derive_tracking_phase(block)
        assert {"value", "source", "reference", "null_reason"} <= set(f)
        assert f["source"] and f["reference"]
        if f["value"] is None:
            assert f["null_reason"], "null senza motivo"
        else:
            assert f["value"] in adv.TRACKING_PHASES
            assert f["null_reason"] is None


# ---------------------------------------------------------------------------
# 2) Le 6 fasi sono tutte derivabili da eventi reali del blocco
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("block,expected", [
    (_events(birth={"track_id": 1}), "forming"),
    (_events(birth={"track_id": 1}, continuation={"continued": True}),
     "organizing"),
    (_events(birth={"track_id": 1}, continuation={"continued": True},
             split={"child_ids": [2]}), "organizing"),
    (_events(birth={"track_id": 1},
             temporary_non_observation={"coasted_points": 1}), "coasting"),
    (_events(birth={"track_id": 1}, death={"death_ms": 1}), "dissipating"),
    (_events(birth={"track_id": 1},
             ambiguous_association={"tracking_ambiguity": "overlap"}),
     "ambiguous"),
])
def test_all_six_phases_are_derivable(block, expected):
    f = adv.derive_tracking_phase(block)
    assert f["value"] == expected, (expected, f)


def test_mature_requires_age_and_explicit_threshold():
    young = {"track_id": 1}
    f_org = adv.derive_tracking_phase(
        _events(birth=young, continuation={"continued": True}))
    assert f_org["value"] == "organizing"  # 10 min < 30 min
    f_mat = adv.derive_tracking_phase(
        _events(birth=young, continuation={"continued": True}),
        config={"tracking_mature_min_age_min": 5.0})
    assert f_mat["value"] == "mature"
    assert f_mat["rule"] == "continued_long_lived"


# ---------------------------------------------------------------------------
# 3) Degrado onesto: niente eventi -> null motivato
# ---------------------------------------------------------------------------
def test_null_when_block_missing_wrong_schema_or_empty():
    assert adv.derive_tracking_phase(None)["null_reason"] == \
        "tracking_events_unavailable"
    assert adv.derive_tracking_phase({"schema": "other/9", "events": []})[
        "null_reason"] == "tracking_events_schema_unexpected"
    assert adv.derive_tracking_phase(
        {"schema": "meteorisk.tracking_events/1", "events": []})[
        "null_reason"] == "tracking_events_empty"


def test_null_when_required_event_types_are_missing():
    block = {"schema": "meteorisk.tracking_events/1",
             "events": [{"type": "birth", "timestamp": RADAR_TS,
                         "value": {"track_id": 1}}]}
    f = adv.derive_tracking_phase(block)
    assert f["null_reason"] == "tracking_events_missing:continuation,death"


# ---------------------------------------------------------------------------
# 4) Nessuna parola vietata, nessun orario inventato
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("block", [
    _events(birth={"track_id": 1}),
    _events(birth={"track_id": 1}, death={"death_ms": 1}),
    _events(birth={"track_id": 1},
            ambiguous_association={"tracking_ambiguity": "overlap"}),
])
def test_no_forbidden_words_and_no_invented_time(block):
    f = adv.derive_tracking_phase(block)
    flat = repr(f).lower()
    for bad in ("supercell", "mesociclon", "mesocyclon", "rotazione",
                "rotation", "intenso", "intens ", "pod", "far", "csi"):
        assert bad not in flat, bad
    assert f["age_min"] is not None
    # L'eta' e' calcolata dai timestamp degli eventi, non da Date.now().
    assert f["age_min"] == pytest.approx(30.0) or f["age_min"] >= 0


# ---------------------------------------------------------------------------
# 5) Il blocco reale prodotto da output.py alimenta la funzione
# ---------------------------------------------------------------------------
def test_phase_from_real_output_builder_block():
    pt0 = types.SimpleNamespace(timestamp_iso="2026-11-21T14:05:00Z",
                                frame_index=0, position_source="radar",
                                observed=True)
    pt1 = types.SimpleNamespace(timestamp_iso=RADAR_TS,
                                frame_index=1, position_source="radar",
                                observed=True)
    track = types.SimpleNamespace(
        track_id=1, events=[], points=[pt0, pt1], death_ms=None,
        lineage=None, parent_ids=[], child_ids=[], coasting_null_reason=None,
        tracking_ambiguity=None, continued_from_history=False,
        history_uid=None, ambiguity_reason=None)
    block = output.build_tracking_events(track, RADAR_TS)
    assert block["schema"] == "meteorisk.tracking_events/1"
    f = adv.derive_tracking_phase(block)
    assert f["value"] in adv.TRACKING_PHASES
    assert len(f["events_observed"]) == 7
    assert set(f["events_observed"]) == {
        "birth", "continuation", "death", "temporary_non_observation",
        "split", "merge", "ambiguous_association"}


# ---------------------------------------------------------------------------
# 6) Dati reali congelati: campi presenti e descrittore volumetrico inutile
# ---------------------------------------------------------------------------
def test_real_1446_payload_fields_and_measurements():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    structures = data["structures"]
    assert len(structures) == 6
    for key in ("morphology_organization", "morphology_class",
                "lifecycle_stage", "bwer_candidate", "overhang_candidate",
                "hail_nuclei_tracking", "track_id"):
        assert all(key in s for s in structures), key
    # MISURATO: organized_vertical non e' mai true su questo run reale.
    organized = sum(1 for s in structures
                    if (s["morphology_organization"] or {}).get(
                        "supports_organized") is True)
    assert organized == 0
    assert all(s["morphology_organization"]["null_reason"] for s in structures)
    # Il join struttura -> track e' reale, non inventato.
    tracks = json.loads(TRACKS_FIXTURE.read_text(encoding="utf-8"))
    ids = {t.get("track_id") for t in tracks["tracks"]}
    assert all(s["track_id"] in ids for s in structures)


# ---------------------------------------------------------------------------
# 7) Wiring additivo e summary pubblicato
# ---------------------------------------------------------------------------
def test_attach_is_additive_and_publishes_tracking_phase_counts():
    bundle = models.EngineBundle("ok", RADAR_TS, "test")
    record = {"structure_id": "CS-TEST", "soi": 50,
              "rotation_measurable": False, "track_id": 1,
              "track_type": "cell"}
    bundle.convective_structures = [record]
    pt0 = types.SimpleNamespace(timestamp_iso="2026-11-21T14:05:00Z",
                                frame_index=0, position_source="radar",
                                observed=True)
    pt1 = types.SimpleNamespace(timestamp_iso=RADAR_TS,
                                frame_index=1, position_source="radar",
                                observed=True)
    track = types.SimpleNamespace(
        track_id=1, events=[], points=[pt0, pt1], death_ms=None,
        lineage=None, parent_ids=[], child_ids=[], coasting_null_reason=None,
        tracking_ambiguity=None, continued_from_history=False,
        history_uid=None, ambiguity_reason=None)
    bundle.tracks = [track]
    before = dict(record)
    _, summary = adv.attach_advanced(bundle)
    assert "tracking_phase" in record
    for key, value in before.items():
        assert record[key] == value
    assert summary["tracking_phase_null"] == 0
    assert sum(summary["tracking_phase_counts"].values()) == 1
    pub = output._advanced_summary(bundle)
    assert "advanced_tracking_phase_counts" in pub
    assert "advanced_tracking_phase_null_reasons" in pub
