#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dell'indice v2 (soi_v2): rinormalizzazione, zero misurato, livelli.

Copre il difetto misurato il 2026-11-12 su 34 record pubblicati (15 run):
  - un componente ASSENTE deve essere ESCLUSO e il suo peso ridistribuito
    (mai contato come 0.0 a peso pieno);
  - un componente PRESENTE e 0.0 e' un valore MISURATO: resta e conta;
  - NaN/inf/non numerici sono ASSENTI (prima diventavano 0.0 e contavano);
  - soi_v2 ha livelli dichiarati e uno `status` per record;
  - `weights_used` somma a 1.0 su ogni combinazione di componenti presenti.
"""

import itertools

import pytest

from radar_engine.config import CONFIG
from radar_engine.phase2 import aggregate


ALL = aggregate.COMPONENT_KEYS
CFG_AGG = CONFIG["phase2"]["aggregate"]


def _call(**kw):
    """aggregate_ssi_v2 con i componenti non nominati a None."""
    args = {k: kw.get(k) for k in ALL}
    return aggregate.aggregate_ssi_v2(
        args["base"], args["hook"], args["structure"], args["env"],
        args["ot"], args["lightning"])


# ---------------------------------------------------------------------------
# 1. Rinormalizzazione: i pesi usati sommano SEMPRE a 1.0
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("size", range(1, len(ALL) + 1))
def test_weights_used_sum_one_for_every_present_set(size):
    for present in itertools.combinations(ALL, size):
        out = _call(**{k: 60.0 for k in present})
        assert sorted(out["components_present"]) == sorted(present)
        assert sorted(out["weights_used"]) == sorted(present)
        assert abs(out["weights_used_sum"] - 1.0) <= 1e-9
        assert abs(sum(out["weights_used"].values()) - 1.0) <= 1e-9
        # L'alias storico e' ARROTONDATO a 6 decimali: la sua somma puo'
        # scostarsi di ~n*0.5e-6 (ed e' il motivo per cui `weights_used` non
        # e' arrotondato). Tolleranza dichiarata, non 1e-9.
        assert abs(sum(out["weights_effective"].values()) - 1.0) <= 1e-5


def test_weights_used_are_the_requested_weights_renormalized():
    out = _call(base=65.0, env=9.2)
    w = aggregate.DEFAULT_WEIGHTS
    total = w["base"] + w["env"]
    assert out["weights_used"]["base"] == pytest.approx(w["base"] / total)
    assert out["weights_used"]["env"] == pytest.approx(w["env"] / total)
    assert out["sum_present_weights"] == pytest.approx(total)


def test_missing_components_are_not_counted_as_zero():
    """La prova del difetto: con base+env il risultato e' la media pesata dei
    due, NON 0.60*base (che e' quello che succede se i mancanti valgono 0.0)."""
    out = _call(base=80.0, env=40.0)
    assert out["components_present"] == ["base", "env"]
    assert out["components_missing"] == ["hook", "structure", "ot", "lightning"]
    # (0.60*80 + 0.08*40) / 0.68 = 75.29 -> 75
    assert out["soi_v2"] == 75
    assert out["soi_v2"] != int(round(0.60 * 80.0))
    assert abs(out["soi_v2"] - (0.60 * 80.0 + 0.08 * 40.0) / 0.68) <= 0.5


def test_only_base_gives_exactly_soi():
    """Nessun altro componente valutabile: soi_v2 non e' un downgrade di soi."""
    out = _call(base=82.0)
    assert out["weights_used"] == {"base": pytest.approx(1.0)}
    assert out["soi_v2"] == 82


# ---------------------------------------------------------------------------
# 2. Zero MISURATO vs componente assente
# ---------------------------------------------------------------------------
def test_zero_measured_component_is_present_and_counts():
    out = _call(base=80.0, hook=0.0, env=40.0)
    assert "hook" in out["components_present"]
    assert "hook" not in out["components_missing"]
    assert out["components_zero_measured"] == ["hook"]
    # (0.60*80 + 0.15*0 + 0.08*40) / 0.83 = 61.69 -> 62
    assert out["soi_v2"] == 62
    # lo stesso record con hook ASSENTE vale di piu': il peso si ridistribuisce
    out_missing = _call(base=80.0, hook=None, env=40.0)
    assert out_missing["components_missing"] == ["hook", "structure", "ot",
                                                 "lightning"]
    assert out_missing["soi_v2"] == 75
    assert out_missing["soi_v2"] > out["soi_v2"]


def test_zero_measured_hook_is_reported_separately():
    out = _call(base=65.0, hook=0.0, structure=1.2, env=9.2, ot=0.0)
    assert out["components_zero_measured"] == ["hook", "ot"]
    assert len(out["components_present"]) == 5


# ---------------------------------------------------------------------------
# 3. NaN/inf/non numerici sono ASSENTI (non 0.0 misurato)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 "n/d", True])
def test_non_finite_component_is_missing_not_zero(bad):
    out = _call(base=80.0, env=bad)
    assert "env" not in out["components_present"]
    assert "env" in out["components_missing"]
    assert "env" in out["components_invalid"]
    assert out["env"] is None
    # identico al caso "env assente": nessuno zero fabbricato
    assert out["soi_v2"] == _call(base=80.0)["soi_v2"] == 80


def test_invalid_components_do_not_enter_weights_used():
    out = _call(base=80.0, hook=float("nan"), env=40.0)
    assert "hook" not in out["weights_used"]
    assert abs(out["weights_used_sum"] - 1.0) <= 1e-9


# ---------------------------------------------------------------------------
# 4. Status per record
# ---------------------------------------------------------------------------
def test_status_ok_when_all_components_present():
    out = _call(base=70.0, hook=10.0, structure=20.0, env=30.0, ot=5.0,
                lightning=2.0)
    assert out["components_missing"] == []
    assert out["status"] == "ok"
    assert out["partial"] is False


def test_status_partial_lists_the_missing_components():
    out = _call(base=70.0, hook=0.0, structure=20.0, env=30.0, ot=0.0)
    assert out["status"] == "partial"
    assert out["components_missing"] == ["lightning"]
    assert out["partial"] is True


def test_status_unavailable_without_any_component():
    out = _call()
    assert out["soi_v2"] is None
    assert out["status"] == "unavailable"
    assert out["components_present"] == []


# ---------------------------------------------------------------------------
# 5. Livelli dichiarati di soi_v2
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("score,expected", [
    (0, "unorganized"), (49, "unorganized"),
    (50, "organized"), (69, "organized"),
    (70, "strong"), (84, "strong"),
    (85, "intense"), (100, "intense"),
])
def test_classify_level_v2_bands(score, expected):
    assert aggregate.classify_level_v2(score) == expected


def test_classify_level_v2_none_score():
    assert aggregate.classify_level_v2(None) is None


def test_level_bands_partition_zero_to_hundred():
    bands = aggregate.level_bands()
    assert bands[0][0] == 0.0
    assert bands[-1][1] == 100.0
    for low, high in zip(bands, bands[1:]):
        assert high[0] == low[1] + 1.0
    for lo, hi, name in bands:
        assert lo <= hi and isinstance(name, str) and name


def test_level_thresholds_match_config():
    """config.phase2.aggregate.levels_v2 e' il mirror dichiarato del modulo."""
    cfg_bands = tuple(tuple(b) for b in CFG_AGG["levels_v2"])
    assert cfg_bands == aggregate.DEFAULT_LEVELS_V2
    assert aggregate.level_bands(CFG_AGG) == aggregate.DEFAULT_LEVELS_V2


def test_level_is_published_for_every_score():
    out = _call(base=95.0)
    assert out["soi_v2"] == 95
    assert out["level"] == "intense"
    assert out["level_thresholds"] == [list(b) for b in
                                       aggregate.DEFAULT_LEVELS_V2]


# ---------------------------------------------------------------------------
# 6. Regressione su un record REALE pubblicato
# ---------------------------------------------------------------------------
def test_real_record_2026_11_12():
    """Record CS-2026-11-12T17-cel101 (convective_structures.json, main).

    soi=65, hook=0.0, structure=1.2, env=9.2, ot=0.0, lightning=null ->
    soi_v2=41 pubblicato. I pesi usati sono 0.60/0.97, 0.15/0.97, ...
    (lightning escluso: era null, non 0.0)."""
    out = _call(base=65, hook=0.0, structure=1.2, env=9.2, ot=0.0)
    assert out["soi_v2"] == 41
    assert out["level"] == "unorganized"
    assert out["status"] == "partial"
    assert out["components_missing"] == ["lightning"]
    assert out["components_zero_measured"] == ["hook", "ot"]
    assert out["weights_used"]["base"] == pytest.approx(0.60 / 0.97)
    assert "lightning" not in out["weights_used"]
    assert abs(out["weights_used_sum"] - 1.0) <= 1e-9


# ---------------------------------------------------------------------------
# 7. Compatibilita' delle chiavi storiche
# ---------------------------------------------------------------------------
def test_legacy_keys_still_published():
    out = _call(base=60.0, hook=1.0)
    assert out["ssi_v2"] == out["soi_v2"]
    assert out["present"] == out["components_present"]
    assert out["missing"] == out["components_missing"]
    assert out["partial"] is True
    assert abs(out["weights_effective_sum"] - 1.0) <= 1e-6


def test_weights_validation_still_raises():
    with pytest.raises(ValueError):
        aggregate.aggregate_ssi_v2(60.0, None, None, None, None, None,
                                   weights={"base": 1.0})
    with pytest.raises(ValueError):
        aggregate.aggregate_ssi_v2(60.0, None, None, None, None, None,
                                   weights=dict(aggregate.DEFAULT_WEIGHTS,
                                                base=1.0))
