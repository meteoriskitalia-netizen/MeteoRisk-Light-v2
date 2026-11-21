#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#4 — soi_v2: env (NWP) rimosso dal peso 0.08. MISURATO su record REALI.

Sul codice PRISTINO `aggregate.DEFAULT_WEIGHTS['env'] == 0.08` e non esiste
il campo `env_excluded_from_score`: questi test FALLISCONO (valori diversi /
KeyError). Nessun campo protetto (soi, severe_stack, rotation_*) e' toccato:
questi test non modificano i record, li ricalcolano in memoria.
"""
import json
from pathlib import Path

import pytest

from radar_engine.phase2 import aggregate as agg

# Snapshot REALE congelato del run 2026-11-21T14:46:06Z (sha256
# 0134db20..., 315.111 byte): il dato vivo di data/radar ruota ogni 10 minuti
# e i valori attesi sotto sono misurati su questo run specifico.
_RECORDS = (Path(__file__).resolve().parents[1] / "fixtures" /
            "real_1446_convective_structures.json")


def _real():
    return json.loads(_RECORDS.read_text(encoding="utf-8"))["structures"]


def test_env_weight_is_zero_and_audited():
    assert agg.DEFAULT_WEIGHTS["env"] == 0.0
    out = agg.aggregate_ssi_v2(84, 0.0, 0.0, 1.9, 0.0, None)
    assert out["env_excluded_from_score"] is True
    assert out["weights_used"]["env"] == 0.0


def test_real_records_before_after_deltas():
    """Delta misurati sul run reale 2026-11-21T14:46:06Z (6 strutture)."""
    old = dict(agg.DEFAULT_WEIGHTS); old["env"] = 0.08
    expected = {"CS-2026-11-21T14-sto008": (52, 57, False),
                "CS-2026-11-21T14-cel024": (48, 53, True),
                "CS-2026-11-21T14-sto006": (44, 48, False),
                "CS-2026-11-21T14-sto002": (42, 46, False),
                "CS-2026-11-21T14-sto010": (47, 46, False),
                "CS-2026-11-21T14-sto009": (43, 44, False)}
    seen = 0
    for s in _real():
        args = [s.get("soi"), s.get("hook"), s.get("structure"),
                s.get("env"), s.get("ot"), s.get("lightning")]
        before = agg.aggregate_ssi_v2(*args, weights=old)
        after = agg.aggregate_ssi_v2(*args)
        if s["structure_id"] in expected:
            b, a, changed = expected[s["structure_id"]]
            assert (before["soi_v2"], after["soi_v2"]) == (b, a)
            assert (before["level"] != after["level"]) is changed
            seen += 1
    assert seen == 6


def test_protected_fields_are_untouched():
    """Il record reale resta byte-identico eccetto soi_v2/soi_v2_detail."""
    for s in _real():
        snapshot = json.dumps({k: v for k, v in s.items()
                               if k not in ("soi_v2", "soi_v2_detail")},
                              sort_keys=True)
        agg.aggregate_ssi_v2(s.get("soi"), s.get("hook"), s.get("structure"),
                             s.get("env"), s.get("ot"), s.get("lightning"))
        assert json.dumps({k: v for k, v in s.items()
                           if k not in ("soi_v2", "soi_v2_detail")},
                          sort_keys=True) == snapshot
        assert s["rotation_measurable"] is False
        assert isinstance(s.get("severe_stack"), dict)
