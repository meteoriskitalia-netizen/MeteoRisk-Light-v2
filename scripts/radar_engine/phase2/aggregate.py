#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Phase 2 — aggregate.py (indice v2 del descrittore, EXPERIMENTAL)

Combinazione pesata dei layer Fase 2 nell'indice v2 del descrittore SOI:

  soi_v2 = min(100, round( sum_k w_used[k] * value[k] ))   k sui componenti PRESENTI

Pesi DEFAULT (somma ESATTAMENTE 1.00, nessun iperparametro):
  base      0.60   SOI Fase 1 (convective_structure) — resta la base
  hook      0.15   morphologia uncino (hook.py)
  structure 0.10   struttura verticale VIL/ETM/POH/overhang (vertical_structure)
  env       0.08   ambiente NWP SCP/STP/SHIP (environment)
  ot        0.04   overshooting top (overshoot)
  lightning 0.03   tasso/jump fulmini (lightning)

PRESENZA DI UN COMPONENTE (2026-11-12) — la distinzione che conta:
  PRESENTE = numero FINITO (0.0 compreso). Un 0.0 MISURATO e' un dato: il
             descrittore e' stato valutato e non ha trovato nulla.
             Contribuisce con il suo peso, NON viene escluso.
  ASSENTE  = None (layer non calcolato / dato mancante per QUEL candidato)
             oppure NaN/inf/non numerico (valore arrivato ma inutilizzabile).
             Escluso, e il suo peso viene ridistribuito sui presenti.
  Prima di questa versione NaN/inf/valori non numerici venivano convertiti in
  0.0 e contati come PRESENTI: un dato mancante diventava uno zero misurato.
  L'elenco `components_invalid` rende quei casi ispezionabili.

Politica RINORMALIZZAZIONE PESI (B2, PHASE2_VERSION 0.4.0 — componenti per
candidato): i componenti ASSENTI vengono ESCLUSI e i pesi dei componenti
PRESENTI vengono ridistribuiti in modo che la somma dei pesi usati sia
ESATTAMENTE 1.00 (w_used[k] = w[k] / sum(w[j] per j presenti)). Conseguenze
esplicite:
  - un layer assente NON abbassa lo score degli altri (prima: contribuiva 0.0
    a peso pieno, sottostimando sistematicamente i candidati con dati parziali);
  - tutti i componenti assenti -> soi_v2 = None (nessun punteggio fabbricato da
    nessun dato);
  - componenti presenti ma tutti a peso 0 -> soi_v2 = None (nessuna base per
    una media pesata);
  - 'weights' resta il dict RICHIESTO (validato: somma <= 1.0) e
    'weights_used' documenta i pesi REALMENTE usati (somma 1.0 sui presenti).

LIMITE STRUTTURALE DELL'INDICE (misurato, NON compensato qui): un componente
PRESENTE e sempre 0.0 non e' un dato mancante e non viene rinormalizzato. Se
un descrittore non scatta MAI, il suo peso resta una costante negativa sullo
score. MISURA del 2026-11-12 su 34 record pubblicati (15 run osservati, 14 con
almeno una struttura): hook 0.0 su 34/34, ot 0.0 su 34/34 (pesi 0.15 + 0.04 =
0.19 sempre a zero), lightning null su 34/34 (escluso dalla
rinormalizzazione). Con i presenti {base, hook, structure, env, ot} i pesi
usati sono 0.6186 / 0.1546 / 0.1031 / 0.0825 / 0.0412:
soi_v2 = 0.6186*soi + 0.1546*hook + 0.1031*structure + 0.0825*env +
0.0412*ot, cioe' ~20.7 punti sotto soi (media misurata, 17-25). La correzione
sta nei descrittori (hook/ot), non nell'indice: abbassare o togliere quei pesi
per "recuperare" i 20 punti sarebbe una compensazione, non una misura.

LIVELLI soi_v2 (2026-11-12): bande DESCRITTIVE 0-49 unorganized / 50-69
organized / 70-84 strong / 85-100 intense (DEFAULT_LEVELS_V2, mirror in
config.phase2.aggregate.levels_v2). Stesse parole dei livelli SOI ma soglie
DIVERSE (SOI: 50/65/80) e valori NON comparabili: soi_v2 e' un indice diverso,
non una correzione di soi.

STATUS del record (2026-11-12): "ok" se nessun componente e' assente,
"partial" se almeno uno lo e' (l'elenco e' in `components_missing`),
"unavailable" se non c'e' alcun punteggio. E' uno stato PER RECORD: dice
quali componenti mancano, non solo che ne manca uno.

Validazioni (ValueError, input malformati):
  - chiavi pesi sconosciute, mancanti o negative;
  - SOMMA PESI RICHIESTI > 1.0 (divieto esplicito: nessuna normalizzazione
    fittizia sui pesi di default).

Un componente assente resta None nel dict di output (MAI convertito in 0.0:
0.0 sarebbe un valore fabbricato) ed e' elencato in 'components_missing' con
partial=True. Funzione PURA (nessuna I/O).
"""

import math

# ---------------------------------------------------------------------------
# Costanti — pesi di default (somma 1.00, verificata in _validate_weights)
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS = {
    "base": 0.60,
    "hook": 0.15,
    "structure": 0.10,
    "env": 0.08,
    "ot": 0.04,
    "lightning": 0.03,
}
WEIGHTS_MAX_SUM = 1.0
COMPONENT_KEYS = ("base", "hook", "structure", "env", "ot", "lightning")

# Livelli DESCRITTIVI di soi_v2 (0-100). Stesse parole dei livelli SOI, soglie
# DIVERSE (qui 50/70/85, SOI 50/65/80): i due indici non sono comparabili.
# Mirror dichiarato in config.phase2.aggregate.levels_v2 (un test verifica che
# i valori coincidano). cfg=None -> questi default.
DEFAULT_LEVELS_V2 = (
    (0.0, 49.0, "unorganized"),
    (50.0, 69.0, "organized"),
    (70.0, 84.0, "strong"),
    (85.0, 100.0, "intense"),
)


def _clamp100(x):
    """Valore in [0,100]. SOLO su valori gia' validati finiti: la presenza o
    l'assenza di un componente si decide in _component_state (NaN non e' 0.0)."""
    return max(0.0, min(100.0, float(x)))


def _component_state(x):
    """(valore 0..100 | None, stato) di un componente grezzo.

    stato: 'present' (numero finito, 0.0 compreso: MISURATO), 'missing'
    (None), 'invalid' (NaN/inf/bool/non numerico: arrivato ma inutilizzabile).
    Un bool NON e' uno score 0-100: True -> 1.0 sarebbe un valore fabbricato."""
    if x is None:
        return None, "missing"
    if isinstance(x, bool):
        return None, "invalid"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None, "invalid"
    if not math.isfinite(v):
        return None, "invalid"
    return _clamp100(v), "present"


def _validate_weights(weights):
    """Valida i pesi: chiavi note, valori >=0, somma <= 1.0. Ritorna dict."""
    w = dict(weights)
    unknown = set(w) - set(COMPONENT_KEYS)
    if unknown:
        raise ValueError(f"unknown_weight_keys:{sorted(unknown)}")
    missing = set(COMPONENT_KEYS) - set(w)
    if missing:
        raise ValueError(f"missing_weight_keys:{sorted(missing)}")
    total = 0.0
    for k, v in w.items():
        try:
            fv = float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"weight_not_numeric:{k}") from exc
        if fv < 0:
            raise ValueError(f"weight_negative:{k}")
        if fv != fv:
            raise ValueError(f"weight_nan:{k}")
        w[k] = fv
        total += fv
    if total > WEIGHTS_MAX_SUM + 1e-9:
        raise ValueError(f"weights_sum_gt_1:{round(total, 6)}")
    return w, round(total, 6)


def effective_weights(weights, present):
    """Pesi RINORMALIZZATI sui componenti presenti (somma esattamente 1.00).

    present: iterable di chiavi componente. Ritorna dict {chiave: w/sum} SOLO
    per le chiavi presenti (ordine COMPONENT_KEYS). Se la somma dei pesi dei
    presenti e' 0 -> dict vuoto (nessuna media pesata possibile, nessun
    valore inventato). ValueError per chiavi sconosciute (input malformati)."""
    unknown = set(present) - set(COMPONENT_KEYS)
    if unknown:
        raise ValueError(f"unknown_weight_keys:{sorted(unknown)}")
    w, _ = _validate_weights(weights)
    keys = [k for k in COMPONENT_KEYS if k in set(present)]
    total = sum(w[k] for k in keys)
    if total <= 0.0:
        return {}
    return {k: w[k] / total for k in keys}


def level_bands(cfg=None):
    """Bande di livello per soi_v2: tupla di (lo, hi, nome).

    cfg: blocco `phase2.aggregate` (dict) con la chiave `levels_v2`; accetta
    sia [[lo, hi, nome], ...] (forma JSON) sia [(lo, hi, nome), ...]. Senza
    cfg, o senza la chiave, si usano DEFAULT_LEVELS_V2 (comportamento di
    produzione: main.py non passa cfg)."""
    raw = None
    if isinstance(cfg, dict):
        raw = cfg.get("levels_v2")
    if not raw:
        return DEFAULT_LEVELS_V2
    bands = []
    for item in raw:
        if isinstance(item, dict):
            lo, hi, name = item["lo"], item["hi"], item["name"]
        else:
            lo, hi, name = item
        bands.append((float(lo), float(hi), str(name)))
    return tuple(bands)


def classify_level_v2(score, cfg=None):
    """Banda di livello per soi_v2 (0-100), None se lo score non c'e'.

    Stesse parole dei livelli SOI (unorganized/organized/strong/intense) ma
    soglie DIVERSE e valori NON comparabili: soi_v2 e' un indice diverso."""
    if score is None:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(s):
        return None
    s = max(0.0, min(100.0, s))
    for lo, hi, name in level_bands(cfg):
        if lo <= s <= hi:
            return name
    return "unorganized"


def aggregate_ssi_v2(base_ssi, hook, structure, env, ot, lightning,
                     weights=None, cfg=None):
    """Combina i layer nell'indice v2 (0-100 intero) con RINORMALIZZAZIONE PESI.

    Componenti ASSENTI (None/NaN/inf/non numerici) ESCLUSI e pesi ridistribuiti
    sui presenti (somma 1.0); tutti assenti -> soi_v2 None. Un 0.0 finito e'
    PRESENTE (misurato) e conta con il suo peso. Ritorna dict con:
      - le singole voci (componenti presenti arrotondati a 1 decimale,
        assenti None);
      - 'soi_v2' (int | None) e 'ssi_v2' (alias deprecato, stesso valore);
      - 'level' (banda DESCRITTIVA di soi_v2 | None);
      - 'status' ('ok' | 'partial' | 'unavailable');
      - 'weights' (richiesti), 'weights_used' (USATI, non arrotondati, somma
        1.0 sui presenti), 'weights_used_sum', 'sum_present_weights' (somma
        dei pesi richiesti dei presenti: perche' i pesi usati differiscono);
      - 'weights_effective' / 'weights_effective_sum' (alias arrotondati a 6
        decimali, pubblicati dalle versioni precedenti);
      - 'components_present' / 'components_missing' / 'components_invalid' /
        'components_zero_measured' (presenti con valore pubblicato 0.0: uno
        zero MISURATO, non un'assenza) e gli alias 'present'/'missing'/
        'invalid'/'partial';
      - 'level_thresholds' (bande applicate, per audit).
    cfg: blocco `phase2.aggregate` per le sole bande di livello (None ->
    DEFAULT_LEVELS_V2). Funzione pura."""
    w, wsum = _validate_weights(DEFAULT_WEIGHTS if weights is None
                                else weights)
    raw = {
        "base": base_ssi,
        "hook": hook,
        "structure": structure,
        "env": env,
        "ot": ot,
        "lightning": lightning,
    }
    values = {}
    states = {}
    for k in COMPONENT_KEYS:
        values[k], states[k] = _component_state(raw[k])
    present = [k for k in COMPONENT_KEYS if states[k] == "present"]
    missing = [k for k in COMPONENT_KEYS if states[k] != "present"]
    invalid = [k for k in COMPONENT_KEYS if states[k] == "invalid"]

    out = {}
    for k in COMPONENT_KEYS:
        out[k] = round(values[k], 1) if states[k] == "present" else None
    # Uno zero "misurato" e' un presente che si legge 0.0 nell'output.
    zero_measured = [k for k in present if out[k] == 0.0]

    eff = effective_weights(w, present)
    soi_v2 = None
    if eff:
        acc = sum(eff[k] * values[k] for k in present)
        soi_v2 = int(min(100, round(acc)))

    if soi_v2 is None:
        status = "unavailable"
    elif missing:
        status = "partial"
    else:
        status = "ok"

    out["ssi_v2"] = soi_v2
    # 1.2.4.17: il campo pubblico e' soi_v2. `ssi_v2` resta SOLO come alias
    # DEPRECATO (stesso valore) per non rompere i consumer e i test gia'
    # pubblicati: non usarlo nel codice nuovo.
    out["soi_v2"] = soi_v2
    out["level"] = classify_level_v2(soi_v2, cfg)
    out["status"] = status
    out["weights"] = dict(w)
    out["weights_sum"] = wsum
    # Pesi EFFETTIVI usati nel calcolo: NON arrotondati (il chiamante puo'
    # verificarne la somma a 1.0). L'alias arrotondato resta per compatibilita'.
    out["weights_used"] = dict(eff)
    out["weights_used_sum"] = sum(eff.values())
    out["sum_present_weights"] = round(sum(w[k] for k in present), 6)
    out["weights_effective"] = {k: round(v, 6) for k, v in eff.items()}
    out["weights_effective_sum"] = round(sum(eff.values()), 6)
    out["components_present"] = present
    out["components_missing"] = missing
    out["components_invalid"] = invalid
    out["components_zero_measured"] = zero_measured
    out["level_thresholds"] = [list(b) for b in level_bands(cfg)]
    # Alias storici (B2, PHASE2_VERSION 0.4.0): stessi valori, chiavi diverse.
    out["present"] = present
    out["missing"] = missing
    out["invalid"] = invalid
    out["partial"] = bool(missing)
    return out
