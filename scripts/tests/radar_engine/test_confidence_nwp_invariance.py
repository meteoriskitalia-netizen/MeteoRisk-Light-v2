#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#5 — confidenza radar INVARIANTE con/senza contesto NWP, su record REALI.

Sul codice PRISTINO `radar_engine.main.radar_observation_coverage` non esiste:
l'import in testa fallisce (ImportError) — prova che il test morde il guard.
Il guard vieta esplicitamente `env` nei componenti dell'osservazione.
"""
import json
from pathlib import Path

import pytest

from radar_engine.main import radar_observation_coverage, _RADAR_OBSERVATION_COMPONENTS

# Snapshot REALE congelato del run 2026-11-21T14:46:06Z (sha256
# 0134db20..., 315.111 byte), perche' il dato vivo di data/radar ruota.
_RECORDS = (Path(__file__).resolve().parents[1] / "fixtures" /
            "real_1446_convective_structures.json")


def test_env_not_in_observation_components():
    assert "env" not in _RADAR_OBSERVATION_COMPONENTS
    assert set(_RADAR_OBSERVATION_COMPONENTS) == {
        "hook", "structure", "ot", "lightning"}
    with pytest.raises(ValueError, match="env_is_nwp_context"):
        radar_observation_coverage({}, observation_keys=("hook", "env"))


def test_coverage_identical_with_and_without_nwp_real_records():
    structures = json.loads(_RECORDS.read_text(encoding="utf-8"))["structures"]
    assert len(structures) >= 3
    checked = 0
    for s in structures:
        base = {k: s.get(k) for k in _RADAR_OBSERVATION_COMPONENTS}
        with_nwp = dict(base, env=s.get("env"))         # contesto NWP PRESENTE
        without_nwp = dict(base, env=None)             # contesto NWP ASSENTE
        a = radar_observation_coverage(with_nwp)
        b = radar_observation_coverage(without_nwp)
        assert a == b                                   # identico, nonpprox
        assert a[1] == sum(v is not None for v in base.values())
        checked += 1
    assert checked == len(structures)
