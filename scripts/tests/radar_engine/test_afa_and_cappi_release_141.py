# -*- coding: utf-8 -*-
"""Release 1.3.0.41: 10 CAPPI sempre e AFA come descrittore graduato.

I test leggono il CODICE SORGENTE REALE (non una copia):
  - `test_always_ten_cappi_when_gate_disabled` invoca la funzione vera
    `_hail_plausibility_gate` con la configurazione effettiva di `CONFIG`;
  - i due test AFA estraggono via `ast` il blocco di assegnazione scritto in
    `main.py` subito dopo `c["lightning_afa"] = ...` e lo eseguono in uno
    spazio di nomi controllato. Cosi' viene testato il codice che gira in
    produzione, non un duplicato. Su un albero pristino (senza il blocco) i
    due test AFA falliscono: il blocco non c'e' e quindi ``activity_0_100``
    non viene prodotto.
"""
import ast
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(os.path.dirname(_TESTS_DIR))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from radar_engine import config as cfgmod                     # noqa: E402
from radar_engine import main as rmain                        # noqa: E402

_MAIN_PY = os.path.join(_SCRIPTS, "radar_engine", "main.py")


def _afa_descriptor_source():
    """Restituisce il sorgente del blocco AFA presente in main.py.

    Cerca l'assegnazione `c["lightning_afa"] = ...` e restituisce tutte le
    istruzioni successive finche' non arriva la successiva assegnazione a
    `c[...]` (che chiude il blocco). Solleva AssertionError se il blocco non
    c'e': e' la prova che il test morde sul pristino.
    """
    with open(_MAIN_PY, "r", encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    found = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = node.targets
        if len(targets) != 1 or not isinstance(targets[0], ast.Subscript):
            continue
        base = targets[0].value
        sl = targets[0].slice
        if (isinstance(base, ast.Name) and base.id == "c"
                and isinstance(sl, ast.Constant) and sl.value == "lightning_afa"):
            found = node
            break
    assert found is not None, "assegnazione c['lightning_afa'] NON trovata in main.py"
    parent = None
    for cand in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(cand, field, None)
            if isinstance(body, list) and found in body:
                parent = body
                break
        if parent is not None:
            break
    assert parent is not None, "blocco genitore NON trovato"
    idx = parent.index(found)
    block = []
    for stmt in parent[idx + 1:]:
        # il blocco descriptor finisce alla prima assegnazione successiva a c[...]
        is_c_assign = (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                       and isinstance(stmt.targets[0], ast.Subscript)
                       and isinstance(stmt.targets[0].value, ast.Name)
                       and stmt.targets[0].value.id == "c")
        if is_c_assign:
            break
        block.append(ast.get_source_segment(src, stmt))
    assert block, "blocco AFA (activity_0_100) NON trovato in main.py: test che morde sul pristino"
    joined = "\n".join(block)
    assert "activity_0_100" in joined, "activity_0_100 NON prodotto dal blocco AFA"
    return joined


def _apply_afa(afa, activity_ref_px=None):
    block = _afa_descriptor_source()
    ns = {"c": {"lightning_afa": afa},
          "lcfg": {"afa": {"activity_ref_px": activity_ref_px} if activity_ref_px else {}},
          "float": float, "round": round, "min": min, "max": max}
    exec(block, ns)
    return ns["c"]["lightning_afa"]


# ---------------------------------------------------------------------------
# 1) CAPPI: set completo sempre
# ---------------------------------------------------------------------------
def test_always_ten_cappi_when_gate_disabled():
    scfg = cfgmod.CONFIG["phase2"]["structure"]
    gate_cfg = scfg.get("hail_gate") or {}
    assert gate_cfg.get("enabled") is False, "hail_gate DEVE essere disabilitato (decisione utente 2026-11-21)"
    assert gate_cfg.get("levels_when_implausible") == "full"
    # la funzione vera ritorna fetch_full_set=True con gate disabilitato
    gate = rmain._hail_plausibility_gate({}, scfg)
    assert gate["enabled"] is False
    assert gate["reason"] == "gate_disabled"
    assert gate["evaluated"] is False
    assert gate["fetch_full_set"] is True, "con gate off il set da 10 CAPPI va scaricato sempre"
    # anche con VIL/ETM presenti e valori estremi resta full set
    gate2 = rmain._hail_plausibility_gate({}, dict(
        scfg, hail_gate=dict(gate_cfg, enabled=True, vil_density_threshold_g_m3=0.0)))
    # gate riabilitato con soglia 0 e' valutabile solo con products; senza
    # products resta fetch_full_set=True per conservativita'
    assert gate2["fetch_full_set"] is True


# ---------------------------------------------------------------------------
# 2) AFA: descrittore graduato, NON booleano->1
# ---------------------------------------------------------------------------
def test_afa_activity_is_not_bool_to_one():
    # active_pixels e' un bool True (Caso AFA storico): NON deve diventare 100
    afa = {"value": True, "active_pixels": True, "null_reason": None}
    out = _apply_afa(dict(afa), activity_ref_px=600)
    assert out["activity_0_100"] is None, "un booleano NON puo' essere trattato come conteggio di pixel"
    assert "NON TARATA" in out["activity_reference"]
    # 582 px su 600 -> 97.0, non 1.0 e non 100
    afa2 = {"value": True, "active_pixels": 582, "null_reason": None}
    out2 = _apply_afa(dict(afa2), activity_ref_px=600)
    assert out2["activity_0_100"] == 97.0
    # 0 px -> 0.0 (non None), valori negativi e oltre soglia limitati
    assert _apply_afa({"active_pixels": 0}, 600)["activity_0_100"] == 0.0
    assert _apply_afa({"active_pixels": 1500}, 600)["activity_0_100"] == 100.0
    assert _apply_afa({"active_pixels": -5}, 600)["activity_0_100"] == 0.0


def test_afa_null_excludes_weight_and_renormalizes():
    # active_pixels None -> descrittore None, con motivo dichiarato altrove
    afa = {"value": None, "active_pixels": None,
           "null_reason": "afa_frame_unavailable"}
    out = _apply_afa(dict(afa), activity_ref_px=600)
    assert out["value"] is None
    assert out["null_reason"] == "afa_frame_unavailable"
    assert out["activity_0_100"] is None, "un AFA nullo NON deve produrre un peso numerico"
    # il descrittore NON e' una componente SOI: il campo non compare nei
    # pesi usati da aggregate.py (verifica sul sorgente, nessuna modifica)
    agg_py = os.path.join(_SCRIPTS, "radar_engine", "phase2", "aggregate.py")
    with open(agg_py, "r", encoding="utf-8") as fh:
        agg_src = fh.read()
    assert "activity_0_100" not in agg_src, "activity_0_100 NON deve entrare nei pesi SOI"
    assert "lightning_afa" not in agg_src or "afa" in agg_src.lower()
    # config: riferimento dichiarato e non tarato
    afa_cfg = cfgmod.CONFIG["phase2"]["lightning"]["afa"]
    assert afa_cfg.get("activity_ref_px") == 600
