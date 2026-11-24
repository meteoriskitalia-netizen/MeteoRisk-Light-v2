#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FASE 7 — classificazione morfologica estesa, lifecycle, strutture candidate
e tracking dei nuclei grandinigeni. AGGIUNTIVO: non sostituisce nessun campo
esistente (``soi``, ``soi_v2``, ``rotation_*``, ``severe_stack``,
``morphology_organization`` restano invariati).

REGOLA D'ORO (regola #237, mai a tentoni):
  * ogni soglia ha una fonte oppure e' dichiarata ``SCELTA INGEGNERISTICA``
    nel dizionario REFERENCE e nel campo ``reference`` di ogni valore;
  * nessun valore inventato: un dato assente produce SEMPRE ``value=None``
    con ``null_reason`` esplicito (stesso contratto di severe_stack.field);
  * SUPERCELL_CANDIDATE / HP_SUPERCELL_CANDIDATE richiedono EVIDENZA DI
    ROTAZIONE PERSISTENTE misurata da VRADH. Oggi 0 radar italiani espongono
    VRADH (``rotation_measurable:false``, ``no_opera_vradh_data``): quei due
    valori restano SEMPRE null con quel motivo. La sola forma (hook/eco
    elevata) NON basta e non viene mai usata per assegnarli.
  * BWER / overhang sono STRUTTURE CANDIDATE con ``candidate`` + ``quality``
    + ``null_reason``: mai "confermato", mai una classificazione validata.
  * Nessun POD/FAR/CSI (campione 77 positivi / 11 negativi, sbilanciato):
    questo modulo NON calcola metriche di skill.
  * Nessun dato simulato nel prodotto: le griglie/frame sintetici esistono
    SOLO nei test, dichiarati come tali.

Funzioni pure (nessuna I/O, nessuna rete, nessuna dipendenza nuova). La sola
I/O e' in ``attach_advanced``, che scrive i campi nei record passati dal
chiamante e restituisce un summary serializzabile.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

ADVANCED_VERSION = "1.0.0"
SOURCE = "radar_engine.advanced (campi gia' calcolati dal motore)"

# Lessico chiuso (nessuna etichetta outside questo set).
MORPHOLOGY_CLASSES = (
    "SINGLE_CELL", "MULTICELL", "SQUALL_LINE", "BOW_ECHO", "MCS",
    "SUPERCELL_CANDIDATE", "HP_SUPERCELL_CANDIDATE",
)
LIFECYCLE_STAGES = (
    "GENESIS", "DEVELOPING", "INTENSIFYING", "MATURE", "WEAKENING",
    "DISSIPATING",
)
# Fase derivata SOLO dal blocco `meteorisk.tracking_events/1` (7 tipi di
# evento di tracking gia' pubblicati in tracks.json). Lessico chiuso e
# DISJOINT da LIFECYCLE_STAGES: quest'ultimo e' un proxy di intensita',
# qui si descrive lo STATO DEL TRACK. La sesta etichetta e' `ambiguous`
# perche' tracking_events/1 NON contiene alcun segnale di indebolimento:
# introdurre "weakening" qui significherebbe inventare un dato (#207).
TRACKING_PHASES = (
    "forming", "organizing", "mature", "coasting", "dissipating",
    "ambiguous",
)
TRACKING_EVENTS_SCHEMA = "meteorisk.tracking_events/1"
SUPERCELL_CLASSES = ("SUPERCELL_CANDIDATE", "HP_SUPERCELL_CANDIDATE")

# Motivo esplicito per i due valori supercell senza Doppler.
ROTATION_NULL_REASON = "rotation_not_measurable:no_opera_vradh_data"
ROTATION_STATES_WITH_EVIDENCE = frozenset({
    "ROTATION_SUSPECTED", "ROTATION_DETECTED", "MESOCYCLONE_CANDIDATE",
})

# Soglie: TUTTE SCELTA INGEGNERISTICA (nessuna calibrazione locale, campione
# sbilanciato 77/11). Il riferimento completo e' in REFERENCE.
DEFAULT_THRESHOLDS = {
    "single_cell_max_duration_min": 60.0,
    "single_cell_max_cell_count": 1,
    "single_cell_max_echo_top_m": 6000.0,
    "multicell_min_cell_count": 2,
    "multicell_min_area_km2": 150.0,
    "mcs_min_area_km2": 5000.0,
    "mcs_min_duration_min": 60.0,
    "mcs_min_cell_count": 4,
    "mcs_min_echo_top_m": 6000.0,
    "squall_line_min_elongation": 3.0,
    "squall_line_min_cell_count": 3,
    "bow_echo_min_score": 0.65,
    "hp_supercell_vil_density_g_m3": 4.5,
    "supercell_shape_echo_top_m": 6000.0,
    "supercell_shape_vil_density_g_m3": 3.5,
    "supercell_shape_strong_overhang": True,
    "rotation_min_persistence_scans": 2,
    "bwer_min_cappi_levels": 4,
    "bwer_core_dbz": 45.0,
    "bwer_weak_channel_dbz_margin": 10.0,
    "nuclei_min_cappi_levels": 4,
    "nuclei_core_dbz": 50.0,
    "nuclei_max_link_km": 20.0,
    "nuclei_min_frames": 2,
    "life_min_points": 2,
    "life_dissipating_growth_pct": -40.0,
    "life_dissipating_delta_dbz": -8.0,
    "life_weakening_growth_pct": -15.0,
    "life_weakening_delta_dbz": -3.0,
    "life_genesis_max_dbz": 50.0,
    "life_developing_growth_pct": 15.0,
    "life_developing_delta_dbz": 2.0,
    "life_intensifying_growth_pct": 40.0,
    "life_intensifying_delta_dbz": 6.0,
    "life_mature_max_growth_pct": 15.0,
    "life_mature_max_delta_dbz": 3.0,
    "life_mature_min_dbz": 50.0,
    # SCELTA INGEGNERISTICA NON TARATA: eta' minima perche' un track
    # continuato sia descritto come `mature` invece di `organizing`.
    "tracking_mature_min_age_min": 30.0,
}

REFERENCE = {
    "morphology_class": (
        "SCELTA INGEGNERISTICA: 7 classi derivate SOLO da campi gia' calcolati "
        "(area/cell_count/n_points/morphology_organization/overhang/hail/shape_2d). "
        "Nessuna calibrazione locale (campione 77/11 sbilanciato). "
        "SUPERCELL_CANDIDATE/HP_SUPERCELL_CANDIDATE richiedono rotazione VRADH "
        "persistente; senza, restano null con " + ROTATION_NULL_REASON),
    "supercell_evaluation": (
        "Regola di gate: rotation_measurable=True AND persistence_scans>=2 AND "
        "classification in {ROTATION_SUSPECTED,ROTATION_DETECTED,"
        "MESOCYCLONE_CANDIDATE}. VRADH = velocita' radiale (doppler.py); "
        "0 radar italiani oggi -> no_opera_vradh_data. La forma (hook/overhang) "
        "NON assegna mai queste due classi."),
    "hp_supercell_vil_density_g_m3": (
        "SCELTA INGEGNERISTICA 4.5 g/m3 per distinguere HP da classic: soglia "
        "non calibrata localmente; la soglia citata 3.5 g/m3 (Amburn & Wolf "
        "1997) resta il gate di VIL density, non la separazione HP."),
    "lifecycle_stage": (
        "SCELTA INGEGNERISTICA: 6 fasi da intensita' (max_dbz), trend di "
        "area_growth_pct / delta_dbz / echo_top e persistenza (n_points, "
        "duration_min). PROXY del ciclo di vita, NON tarato, NON una probabilita'. "
        "NON sono i 7 eventi Doppler (appearance/intensification/weakening/"
        "displacement/merge/separation/dissipation), che restano invariati."),
    "tracking_phase": (
        "CLASSIFICAZIONE EURISTICA DI COMODO, SCELTA INGEGNERISTICA NON TARATA: "
        "6 etichette derivate SOLO dai timestamp/valori del blocco "
        "meteorisk.tracking_events/1 gia' pubblicato (birth, continuation, "
        "death, temporary_non_observation, split, merge, "
        "ambiguous_association). NON e' una misura fisica, NON e' una "
        "probabilita' e NON sostituisce `lifecycle_stage` (proxy di "
        "intensita'). Nessuna etichetta di indebolimento perche' il blocco "
        "eventi non contiene quel segnale: la sesta fase e' `ambiguous`. "
        "Nessun orario inventato: l'eta' usa i timestamp degli eventi stessi."),
    "bwer_candidate": (
        "BWER = Bounded Weak Echo Region: colonna di eco debole sotto eco elevata. "
        "Richiede profilo CAPPI verticale (>=4 livelli). SCELTA INGEGNERISTICA: "
        "eco elevato >=45 dBZ con canale debole di almeno 10 dBZ respectto al "
        "livello basso. CANDIDATO con quality, mai classificazione confermata."),
    "overhang_candidate": (
        "Riesposizione della misura gia' calcolata da severe_stack.overhang_layer "
        "(CAPPI_2 vs CAPPI_6, 45/50 dBZ, sonda 2026-10-23). CANDIDATO con quality; "
        "la qualita' scende se c'e' slot_mismatch fra CAPPI e VMI."),
    "hail_nuclei_tracking": (
        "SCELTA INGEGNERISTICA: nuclei = pixel/celle con Z>=50 dBZ, collegati "
        "tra frame consecutivi entro 20 km (stesso criterio di "
        "morphology.max_match_km), min 2 frame. Richiede profilo CAPPI >=4 "
        "livelli: con 2 CAPPI (cappi_levels_lt_4) il campo e' null, non inferito. "
        "Nessun POD/FAR/CSI (campione 77/11)."),
}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def _finite(value):
    """float finito o None (nessun NaN/Inf nei payload JSON)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def field(value, *, null_reason=None, source=None, reference=None, **extra):
    """Campo con value/source/reference e null_reason obbligatorio se null.

    Stesso contratto di severe_stack.field: un None senza motivo e' un errore.
    """
    if isinstance(value, float) and not math.isfinite(value):
        value = None
        null_reason = null_reason or "not_finite"
    if value is None and not null_reason:
        raise ValueError("field_null_without_reason")
    out = {"value": value, "null_reason": null_reason,
           "source": source or SOURCE, "reference": reference}
    out.update(extra)
    return out


def thresholds_from_config(cfg=None):
    """Default + override da config['advanced'] (solo valori finiti >=0)."""
    out = dict(DEFAULT_THRESHOLDS)
    if isinstance(cfg, dict):
        for key in out:
            if key in cfg:
                v = _finite(cfg[key])
                if v is not None and v >= 0:
                    out[key] = v
    return out


def _get(mapping, *path, default=None):
    """Accesso annidato sicuro: _get(c,'intensity','max_dbz')."""
    cur = mapping
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def _echo_top_m(candidate):
    """Echo top (m) dal campo morphology_organization gia' calcolato."""
    return _finite(_get(candidate, "morphology_organization", "values",
                        "echo_top_m"))


def _vil_density(candidate):
    """VIL density (g/m3) dal layer hail (valore, non soglia)."""
    return _finite(_get(candidate, "hail", "vil_density_g_m3", "value"))


def _strong_overhang(candidate):
    """Bool dell'overhang forte, o None se non calcolabile."""
    return _get(candidate, "overhang", "strong_elevated_core", "value")


# ---------------------------------------------------------------------------
# 1) Classificatore morfologico a 7 categorie
# ---------------------------------------------------------------------------
def classify_morphology(candidate, *, shape_2d=None, rotation_evidence=None,
                        config=None):
    """Classifica UNA struttura nelle 7 categorie FASE 7.

    ``shape_2d``: dict opzionale con ``elongation`` (rapporto d'asse della
    componente convettiva, >=1) e ``bow_score`` (0..1, convessita' del bordo
    di attacco). Se assente, SQUALL_LINE/BOW_ECHO non vengono assegnati e
    restano null con ``shape_2d_unavailable`` (nessuna inferenza dalla forma).

    ``rotation_evidence``: dict con ``rotation_measurable``,
    ``persistence_scans`` e ``classification``. Senza evidenza VRADH le due
    classi supercell restano null con ``null_reason`` esplicito.

    Ritorna un campo con ``value`` (una delle 7 classi o None) piu' il blocco
    ``supercell_evaluation`` con i due valori supercell SEMPRE dichiarati.
    """
    th = thresholds_from_config(config)
    cand = candidate if isinstance(candidate, dict) else {}
    shape = shape_2d if isinstance(shape_2d, dict) else {}
    rot = rotation_evidence if isinstance(rotation_evidence, dict) else {}

    echo_top = _echo_top_m(cand)
    vil = _vil_density(cand)
    strong_over = _strong_overhang(cand)
    n_cells = _finite(_get(cand, "cell_count"))
    area = _finite(_get(cand, "area_km2"))
    duration = _finite(_get(cand, "motion", "duration_min"))
    n_points = _finite(_get(cand, "n_points"))
    elong = _finite(shape.get("elongation"))
    bow = _finite(shape.get("bow_score"))

    # --- evidenza di rotazione persistente (obbligatoria per supercell) ----
    persistence = _finite(rot.get("persistence_scans")) or 0.0
    rot_ok = bool(
        rot.get("rotation_measurable") is True
        and int(persistence) >= int(th["rotation_min_persistence_scans"])
        and rot.get("classification") in ROTATION_STATES_WITH_EVIDENCE
    )
    supercell = {}
    if rot_ok:
        hp = bool(vil is not None
                  and vil >= th["hp_supercell_vil_density_g_m3"])
        chosen = ("HP_SUPERCELL_CANDIDATE" if hp else "SUPERCELL_CANDIDATE")
        for cls in SUPERCELL_CLASSES:
            if cls == chosen:
                supercell[cls] = field(
                    cls, source=rot.get("source") or SOURCE,
                    reference=REFERENCE["supercell_evaluation"],
                    persistence_scans=int(persistence),
                    rotation_classification=rot.get("classification"))
            else:
                not_sel = ("not_selected_hp_gate" if hp
                           else "vil_below_hp_gate")
                supercell[cls] = field(
                    None, null_reason=not_sel,
                    source=rot.get("source") or SOURCE,
                    reference=REFERENCE["supercell_evaluation"])
        return field(
            chosen, source=rot.get("source") or SOURCE,
            reference=REFERENCE["morphology_class"],
            supercell_evaluation=supercell,
            rotation_evidence_available=True,
            gates={"rotation_persistent": True,
                   "hp_vil_gate": hp},
            values={"echo_top_m": echo_top, "vil_density_g_m3": vil,
                    "persistence_scans": int(persistence)},
            shape_2d={"elongation": elong, "bow_score": bow})

    # --- nessuna rottazione misurabile: le due classi supercell SONO null --
    for cls in SUPERCELL_CLASSES:
        supercell[cls] = field(
            None, null_reason=ROTATION_NULL_REASON,
            source=SOURCE, reference=REFERENCE["supercell_evaluation"])

    gates = {
        "echo_top_ge_min": (echo_top is not None
                            and echo_top >= th["supercell_shape_echo_top_m"]),
        "vil_density_ge_min": (vil is not None
                               and vil >= th["supercell_shape_vil_density_g_m3"]),
        "strong_overhang": (strong_over is True
                            if bool(th["supercell_shape_strong_overhang"])
                            else None),
    }
    # Forma "supercell-like": eco profonda + VIL alta + overhang forte. Questa
    # EVIDENZA MORFOLOGICA NON puo' assegnare le due classi supercell (serve
    # VRADH): restano null nei `supercell_evaluation`. Il valore principale
    # resta pero' un descrittore non-supercell calcolato dalle regole qui sotto,
    # con il flag esplicito `supercell_shape_without_doppler` (nessuna
    # inferenza silenziosa, nessuna diagnosi di rotazione).
    shape_supercell = bool(all(
        v is True for k, v in gates.items()
        if k in ("echo_top_ge_min", "vil_density_ge_min", "strong_overhang")))

    value = None
    reason = None
    rule = None
    if (elong is not None
          and elong >= th["squall_line_min_elongation"]
          and n_cells is not None
          and n_cells >= th["squall_line_min_cell_count"]):
        value, rule = "SQUALL_LINE", "elongation_and_cell_count"
    elif (bow is not None and bow >= th["bow_echo_min_score"]):
        value, rule = "BOW_ECHO", "bow_score"
    elif (area is not None and area >= th["mcs_min_area_km2"]
          and duration is not None and duration >= th["mcs_min_duration_min"]
          and n_cells is not None and n_cells >= th["mcs_min_cell_count"]
          and echo_top is not None and echo_top >= th["mcs_min_echo_top_m"]):
        value, rule = "MCS", "large_area_long_duration_many_cells"
    elif (n_cells is not None and n_cells >= th["multicell_min_cell_count"]
          and area is not None and area >= th["multicell_min_area_km2"]):
        value, rule = "MULTICELL", "cell_count_and_area"
    elif (n_cells is not None and n_cells <= th["single_cell_max_cell_count"]
          and duration is not None
          and duration <= th["single_cell_max_duration_min"]):
        value, rule = "SINGLE_CELL", "single_cell_short_lived"
    else:
        # I dati per le regole 2D non ci sono: NON assegnare una classe.
        if not shape:
            reason = "shape_2d_unavailable"
        elif (echo_top is None or n_cells is None or area is None
              or duration is None):
            reason = "insufficient_structure_features"
        else:
            reason = "morphology_inconclusive"
    if value is not None and value not in MORPHOLOGY_CLASSES:  # pragma: no cover
        raise AssertionError("class_out_of_lexicon")
    return field(
        value, null_reason=reason, reference=REFERENCE["morphology_class"],
        supercell_evaluation=supercell,
        rotation_evidence_available=False,
        supercell_shape_without_doppler=shape_supercell,
        rule=rule, gates=gates,
        values={"echo_top_m": echo_top, "vil_density_g_m3": vil,
                "cell_count": n_cells, "area_km2": area,
                "duration_min": duration, "n_points": n_points},
        shape_2d={"elongation": elong, "bow_score": bow})


# ---------------------------------------------------------------------------
# 2) Lifecycle a 6 fasi
# ---------------------------------------------------------------------------
def derive_lifecycle(candidate, config=None):
    """Fase del ciclo di vita di una struttura (6 etichette chiuse).

    Usa intensita', trend (area/VIL/echo top) e persistenza gia' presenti nel
    record. Se lo storico e' insufficiente: null con motivo. NON coincide con
    i 7 eventi Doppler (che restano invariati e separati).
    """
    th = thresholds_from_config(config)
    cand = candidate if isinstance(candidate, dict) else {}
    n_points = _finite(_get(cand, "n_points"))
    growth = _finite(_get(cand, "motion", "area_growth_pct"))
    delta = _finite(_get(cand, "intensity", "delta_dbz"))
    max_dbz = _finite(_get(cand, "intensity", "max_dbz"))
    phase = cand.get("phase")
    status = cand.get("status")
    on_latest = bool(cand.get("on_latest_frame"))
    duration = _finite(_get(cand, "motion", "duration_min"))

    if (n_points is None or n_points < th["life_min_points"]) or (
            growth is None and delta is None):
        return field(
            None, null_reason="insufficient_track_history",
            reference=REFERENCE["lifecycle_stage"],
            values={"n_points": n_points, "area_growth_pct": growth,
                    "delta_dbz": delta})

    rule = None
    value = None
    dying = (status == "dead" and not on_latest)
    # 2026-11-24 — conflitto area/intensita': un calo di dBZ con area in FORTE
    # crescita non e' indebolimento (il sistema si sta espandendo). Prima un
    # delta_dbz=-7.0 con area_growth_pct=+83.9 veniva etichettato WEAKENING
    # perche' la condizione era un OR senza guardia sull'area.
    strong_area_growth = (
        growth is not None and growth >= th["life_intensifying_growth_pct"])
    if dying or (not strong_area_growth and (
            (growth is not None
             and growth <= th["life_dissipating_growth_pct"])
            or (delta is not None
                and delta <= th["life_dissipating_delta_dbz"]))):
        value, rule = "DISSIPATING", "dead_or_strong_negative_trend"
    elif not strong_area_growth and (
            (growth is not None and growth <= th["life_weakening_growth_pct"])
            or (delta is not None and delta <= th["life_weakening_delta_dbz"])):
        value, rule = "WEAKENING", "negative_trend"
    elif (phase == "birth" or (n_points is not None and n_points <= 2)) and (
            max_dbz is None or max_dbz < th["life_genesis_max_dbz"]):
        value, rule = "GENESIS", "birth_or_short_weak"
    elif ((growth is not None and growth >= th["life_intensifying_growth_pct"])
          or (delta is not None and delta >= th["life_intensifying_delta_dbz"])):
        value, rule = "INTENSIFYING", "strong_positive_trend"
    elif ((growth is not None and growth >= th["life_developing_growth_pct"])
          or (delta is not None and delta >= th["life_developing_delta_dbz"])):
        value, rule = "DEVELOPING", "positive_trend"
    elif (max_dbz is not None and max_dbz >= th["life_mature_min_dbz"]
          and (growth is None or abs(growth) < th["life_mature_max_growth_pct"])
          and (delta is None or abs(delta) < th["life_mature_max_delta_dbz"])):
        value, rule = "MATURE", "strong_and_steady"
    else:
        return field(
            None, null_reason="lifecycle_inconclusive",
            reference=REFERENCE["lifecycle_stage"],
            values={"n_points": n_points, "area_growth_pct": growth,
                    "delta_dbz": delta, "max_dbz": max_dbz})

    if value not in LIFECYCLE_STAGES:  # pragma: no cover
        raise AssertionError("stage_out_of_lexicon")
    return field(
        value, reference=REFERENCE["lifecycle_stage"], rule=rule,
        values={"n_points": n_points, "duration_min": duration,
                "area_growth_pct": growth, "delta_dbz": delta,
                "max_dbz": max_dbz, "phase": phase, "status": status},
        note="proxy non tarato; NON i 7 eventi Doppler")


# ---------------------------------------------------------------------------
# 3) BWER / overhang come STRUTTURE CANDIDATE (mai confermate)
# ---------------------------------------------------------------------------
def bwer_candidate(candidate=None, *, cappi_levels=None, profile=None,
                   config=None):
    """BWER come CANDIDATO con quality, mai classificazione confermata.

    ``cappi_levels``: numero di CAPPI reali disponibili. Con <4 (oggi 2) il
    campo e' null con ``cappi_levels_lt_4``: nessuna inferenza.
    ``profile``: dict opzionale ``{low_dbz, high_dbz, mid_dbz}`` estratto da
    una colonna CAPPI reale. Se assente con CAPPI sufficienti, il campo e'
    null con ``bwer_profile_unavailable`` (non si inventa il profilo).
    """
    th = thresholds_from_config(config)
    n = _finite(cappi_levels)
    levels = int(n) if n is not None else 0
    if levels < int(th["bwer_min_cappi_levels"]):
        return field(
            None, null_reason="cappi_levels_lt_4",
            reference=REFERENCE["bwer_candidate"],
            candidate=None, quality="D_unavailable",
            cappi_levels=levels, gates={})
    prof = profile if isinstance(profile, dict) else None
    if prof is None:
        return field(
            None, null_reason="bwer_profile_unavailable",
            reference=REFERENCE["bwer_candidate"],
            candidate=None, quality="D_unavailable",
            cappi_levels=levels, gates={})
    low = _finite(prof.get("low_dbz"))
    high = _finite(prof.get("high_dbz"))
    mid = _finite(prof.get("mid_dbz"))
    if low is None or high is None or mid is None:
        return field(
            None, null_reason="bwer_profile_incomplete",
            reference=REFERENCE["bwer_candidate"],
            candidate=None, quality="D_unavailable", cappi_levels=levels)
    gates = {
        "high_echo_ge_45": high >= th["bwer_core_dbz"],
        "weak_channel_ge_margin": (low - mid) >= th["bwer_weak_channel_dbz_margin"],
    }
    cand = bool(all(gates.values()))
    quality = "A_observed" if levels >= 6 else "B_limited_levels"
    return field(
        cand, reference=REFERENCE["bwer_candidate"],
        candidate=cand, quality=quality,
        cappi_levels=levels, gates=gates,
        values={"low_dbz": low, "mid_dbz": mid, "high_dbz": high},
        note="candidato strutturale, NON classificazione confermata")


def overhang_candidate(candidate=None, *, config=None):
    """Riesposizione di severe_stack.overhang_layer come CANDIDATO.

    Legge ``candidate['overhang']`` (gia' calcolato dal motore). Non ricalcola
    nulla: se il layer e' assente o il suo valore e' null, riporta il motivo
    reale. Aggiunge ``candidate`` (bool) e ``quality`` (!= "confermato").
    """
    over = _get(candidate, "overhang") if isinstance(candidate, dict) else None
    if not isinstance(over, dict):
        return field(
            None, null_reason="overhang_layer_unavailable",
            reference=REFERENCE["overhang_candidate"],
            candidate=None, quality="D_unavailable")
    core = over.get("strong_elevated_core") or {}
    val = core.get("value")
    reason = core.get("null_reason")
    if val is None:
        return field(
            None, null_reason=reason or "overhang_not_measurable",
            reference=REFERENCE["overhang_candidate"],
            candidate=None, quality="D_unavailable",
            cappi_slot_iso=over.get("cappi_slot_iso"),
            cappi_slot_offset_s=over.get("cappi_slot_offset_s"))
    slot_offset = _finite(over.get("cappi_slot_offset_s"))
    mismatch = bool(slot_offset is not None and int(slot_offset) != 0)
    quality = "B_slot_mismatch" if mismatch else "A_observed"
    levels = _get(over, "levels") or {}
    return field(
        bool(val), reference=REFERENCE["overhang_candidate"],
        candidate=bool(val), quality=quality,
        cappi_levels=None, levels=dict(levels) if isinstance(levels, dict) else None,
        cappi_slot_iso=over.get("cappi_slot_iso"),
        cappi_slot_offset_s=slot_offset, slot_mismatch=mismatch,
        note="candidato strutturale, NON classificazione confermata")


# ---------------------------------------------------------------------------
# 4) Tracking dei nuclei grandinigeni
# ---------------------------------------------------------------------------
def track_hail_nuclei(frames=None, *, cappi_levels=None, config=None):
    """Collega nuclei ad alta riflettivita' (Z>=50 dBZ) fra frame consecutivi.

    ``frames``: lista di dict {time_iso, nuclei:[{lat, lon, dbz, area_km2}]}.
    I nuclei devono essere ESTRATTI da griglie reali dal chiamante: qui non se
    ne fabbrica nessuno. Con ``cappi_levels < 4`` il campo e' null con
    ``cappi_levels_lt_4`` (oggi 2 CAPPI): nessuna inferenza.
    """
    th = thresholds_from_config(config)
    n = _finite(cappi_levels)
    levels = int(n) if n is not None else 0
    if levels < int(th["nuclei_min_cappi_levels"]):
        return field(
            None, null_reason="cappi_levels_lt_4",
            reference=REFERENCE["hail_nuclei_tracking"],
            tracks=[], nuclei_total=0, quality="D_unavailable",
            cappi_levels=levels)
    if not isinstance(frames, (list, tuple)) or len(frames) == 0:
        return field(
            None, null_reason="nuclei_frames_unavailable",
            reference=REFERENCE["hail_nuclei_tracking"],
            tracks=[], nuclei_total=0, quality="D_unavailable",
            cappi_levels=levels)
    # Filtra i nuclei per soglia reale (nessun valore inventato).
    clean = []
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        pts = []
        for nu in (fr.get("nuclei") or []):
            if not isinstance(nu, dict):
                continue
            lat, lon, dbz = (_finite(nu.get("lat")), _finite(nu.get("lon")),
                             _finite(nu.get("dbz")))
            if lat is None or lon is None or dbz is None:
                continue
            if dbz < th["nuclei_core_dbz"]:
                continue
            pts.append({"lat": lat, "lon": lon, "dbz": dbz,
                        "area_km2": _finite(nu.get("area_km2"))})
        clean.append({"time_iso": fr.get("time_iso"), "nuclei": pts})
    total = sum(len(f["nuclei"]) for f in clean)
    if total == 0:
        return field(
            None, null_reason="no_nucleus_above_threshold",
            reference=REFERENCE["hail_nuclei_tracking"],
            tracks=[], nuclei_total=0, quality="C_none_above_threshold",
            cappi_levels=levels)

    # Collegamento greedy fra frame consecutivi entro max_link_km.
    from .tracking import haversine_km  # dipendenza gia' nel progetto
    tracks = []
    active = []  # list of dict(id, points)
    for fi, fr in enumerate(clean):
        touched = set()  # indici di `active` aggiornati O appena creati
        for nu in fr["nuclei"]:
            best = None
            best_d = None
            for idx, tr in enumerate(active):
                if idx in touched:
                    continue
                last = tr["points"][-1]
                d = haversine_km((last["lon"], last["lat"]),
                                 (nu["lon"], nu["lat"]))
                if d <= th["nuclei_max_link_km"] and (best_d is None or d < best_d):
                    best, best_d = idx, d
            if best is not None:
                touched.add(best)
                active[best]["points"].append(dict(nu, time_iso=fr.get("time_iso")))
            else:
                active.append({"id": "HN-{}-{}".format(fi, len(active)),
                               "points": [dict(nu, time_iso=fr.get("time_iso"))]})
                touched.add(len(active) - 1)
        # chiude i track NON toccati in questo frame; sopravvivono solo quelli
        # che hanno ricevuto un punto (nuovi o aggiornati).
        tracks.extend(t for i, t in enumerate(active) if i not in touched)
        active = [t for i, t in enumerate(active) if i in touched]
    tracks.extend(active)

    out_tracks = []
    for tr in tracks:
        pts = tr["points"]
        nfr = len({p.get("time_iso") for p in pts})
        if nfr < int(th["nuclei_min_frames"]):
            continue
        dbzs = [p["dbz"] for p in pts]
        areas = [p["area_km2"] for p in pts if p["area_km2"] is not None]
        out_tracks.append({
            "track_id": tr["id"],
            "n_frames": nfr,
            "max_dbz": round(max(dbzs), 2),
            "mean_dbz": round(sum(dbzs) / len(dbzs), 2),
            "area_growth_pct": (round((areas[-1] - areas[0]) / areas[0] * 100.0, 2)
                                if len(areas) >= 2 and areas[0] > 0 else None),
        })
    out_tracks.sort(key=lambda t: (-t["n_frames"], -t["max_dbz"]))
    quality = "A_observed" if levels >= 6 else "B_limited_levels"
    value = {
        "nuclei_total": total,
        "tracks_total": len(out_tracks),
        "tracks_persistent": sum(1 for t in out_tracks
                                 if t["n_frames"] >= int(th["nuclei_min_frames"])),
        "tracks": out_tracks[:50],
    }
    return field(
        value, reference=REFERENCE["hail_nuclei_tracking"],
        tracks=value["tracks"], nuclei_total=total,
        quality=quality, cappi_levels=levels,
        thresholds={"core_dbz": th["nuclei_core_dbz"],
                    "max_link_km": th["nuclei_max_link_km"],
                    "min_frames": int(th["nuclei_min_frames"])})


# ---------------------------------------------------------------------------
# 4b) Fase del tracking derivata da `tracking_events/1` (ADDITIVA, NON fisica)
# ---------------------------------------------------------------------------
def _parse_event_ts(value):
    """ISO8601 -> datetime UTC, o None. Nessun orario inventato."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def derive_tracking_phase(tracking_events, *, reference_time=None, config=None):
    """Fase a 6 etichette da un blocco `meteorisk.tracking_events/1`.

    Il blocco e' QUELLO GIA' PUBBLICATO da ``output.build_tracking_events``:
    qui non si crea nessun evento nuovo e non si tocca nessun evento esistente.
    Le regole sono deterministische e usano solo ``type``/``value``/
    ``timestamp`` degli eventi. Se il blocco non basta, il valore e' null con
    ``null_reason`` esplicito (nessuna inferenza silenziosa).

    `reference_time` e' opzionale: se assente si usa il massimo timestamp
    presente nel blocco, quindi la funzione resta pura e indipendente
    dall'orologio di sistema (regola: mai `Date.now()`).
    """
    th = thresholds_from_config(config)
    ref = _parse_event_ts(reference_time)
    block = tracking_events if isinstance(tracking_events, dict) else None
    if block is None:
        return field(None, null_reason="tracking_events_unavailable",
                     reference=REFERENCE["tracking_phase"])
    if block.get("schema") != TRACKING_EVENTS_SCHEMA:
        return field(None, null_reason="tracking_events_schema_unexpected",
                     reference=REFERENCE["tracking_phase"],
                     schema_seen=block.get("schema"))
    events = block.get("events")
    if not isinstance(events, list) or not events:
        return field(None, null_reason="tracking_events_empty",
                     reference=REFERENCE["tracking_phase"])
    by_type = {}
    for event in events:
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            by_type.setdefault(event["type"], event)
    required = ("birth", "continuation", "death")
    missing = [kind for kind in required if kind not in by_type]
    if missing:
        return field(None, null_reason="tracking_events_missing:"
                     + ",".join(missing),
                     reference=REFERENCE["tracking_phase"])

    def observed(kind):
        event = by_type.get(kind)
        return bool(event is not None and event.get("value") is not None)

    # SOLO eventi OSSERVATI: il timestamp di un evento assente e' l'istante di
    # valutazione del run, NON un'evidenza temporale (v. output.py).
    stamps = [e.get("timestamp") for e in events
              if isinstance(e, dict) and e.get("timestamp")
              and e.get("value") is not None]
    if ref is None:
        parsed = [dt for dt in (_parse_event_ts(s) for s in stamps) if dt is not None]
        ref = max(parsed) if parsed else None
    birth_ts = _parse_event_ts(by_type["birth"].get("timestamp"))
    age_min = None
    if ref is not None and birth_ts is not None:
        age_min = (ref - birth_ts).total_seconds() / 60.0
        # Scarto di orologio/arrotondamento: un'eta' negativa non e' un dato.
        if age_min < 0:
            age_min = 0.0
        age_min = round(age_min, 2)

    present = {kind: observed(kind) for kind in (
        "birth", "continuation", "death", "temporary_non_observation",
        "split", "merge", "ambiguous_association")}
    if present["death"]:
        value, rule = "dissipating", "death_observed"
    elif present["temporary_non_observation"]:
        value, rule = "coasting", "coasted_point_observed"
    elif present["ambiguous_association"]:
        value, rule = "ambiguous", "ambiguous_association_observed"
    elif present["split"] or present["merge"]:
        value, rule = "organizing", "split_or_merge_observed"
    elif present["continuation"]:
        if (age_min is not None
                and age_min >= th["tracking_mature_min_age_min"]):
            value, rule = "mature", "continued_long_lived"
        else:
            value, rule = "organizing", "continued_short_lived"
    elif present["birth"]:
        value, rule = "forming", "birth_only"
    else:
        return field(
            None, null_reason="tracking_events_insufficient_for_phase",
            reference=REFERENCE["tracking_phase"], events_observed=present,
            age_min=age_min)

    if value not in TRACKING_PHASES:  # pragma: no cover
        raise AssertionError("tracking_phase_out_of_lexicon")
    return field(
        value, reference=REFERENCE["tracking_phase"], rule=rule,
        schema=TRACKING_EVENTS_SCHEMA, events_observed=present,
        age_min=age_min,
        thresholds={"tracking_mature_min_age_min":
                    th["tracking_mature_min_age_min"]},
        note="euristica NON tarata; NON e' una misura fisica")


def _tracking_events_index(bundle):
    """Indice track_id -> blocco eventi GIA' generato da output.py.

    Riusa `output.build_tracking_events`: nessun evento viene creato o
    modificato qui. Indice separato per celle e storm object; le due
    collezioni hanno namespace di ID distinto nel motore.
    """
    from . import output as _out
    radar_ts = getattr(bundle, "radar_timestamp_iso", None)
    out = {"cell": {}, "storm_object": {}}
    for kind, attr in (("cell", "tracks"), ("storm_object", "storm_tracks")):
        for track in (getattr(bundle, attr, None) or []):
            tid = getattr(track, "track_id", None)
            if tid is None:
                continue
            try:
                out[kind][tid] = _out.build_tracking_events(track, radar_ts)
            except Exception:
                # Layer additivo: un track non leggibile non rompe il run.
                continue
    return out


# ---------------------------------------------------------------------------
# 5) Wiring additivo
# ---------------------------------------------------------------------------
def _rotation_evidence(bundle):
    """Evidenza VRADH misurata, o None (nessun dato). NON inventa rotazione."""
    da = getattr(bundle, "doppler_analysis", None) or {}
    mt = getattr(bundle, "mesocyclone_tracks", None) or {}
    if not isinstance(da, dict) or da.get("null_reason"):
        return None
    if not (da.get("signature_total") or 0):
        return None
    counts = da.get("state_counts") or {}
    state = None
    for st in ("MESOCYCLONE_CANDIDATE", "ROTATION_DETECTED", "ROTATION_SUSPECTED"):
        if (counts.get(st) or 0) > 0:
            state = st
            break
    if state is None:
        return None
    persistence = 0
    if isinstance(mt, dict):
        for tr in (mt.get("tracks") or []):
            if isinstance(tr, dict):
                persistence = max(persistence, int(tr.get("persistence") or 0))
    return {"rotation_measurable": True,
            "persistence_scans": persistence,
            "classification": state,
            "source": da.get("source") or "OPERA FR VRADH (doppler.py)"}


def _shape_2d_for(bundle, candidate):
    """Elongazione reale dall'hull dello storm object (se disponibile).

    Nessun dato inventato: senza storm object matchato, ritorna {} e le classi
    che richiedono forma restano null con ``shape_2d_unavailable``.
    """
    soid = candidate.get("storm_object_id")
    if soid is None:
        return {}
    frames = getattr(bundle, "storm_objects_by_frame", None) or []
    if not frames:
        return {}
    last = frames[-1]
    if not isinstance(last, (list, tuple)):
        return {}
    import math as _m
    for obj in last:
        if getattr(obj, "storm_object_id", None) != soid:
            continue
        bbox = getattr(obj, "bbox_lonlat", None)
        if not bbox or len(bbox) != 4:
            return {}
        lon0, lat0, lon1, lat1 = [float(v) for v in bbox]
        latm = _m.radians((lat0 + lat1) / 2.0)
        dx = abs(lon1 - lon0) * 111.32 * max(_m.cos(latm), 0.01)
        dy = abs(lat1 - lat0) * 110.57
        if dx <= 0 or dy <= 0:
            return {}
        elong = max(dx, dy) / min(dx, dy)
        return {"elongation": round(elong, 3), "bow_score": None,
                "source": "storm_object.bbox_lonlat (SCELTA INGEGNERISTICA)"}
    return {}


def attach_advanced(bundle, config=None):
    """Aggiunge i 6 campi FASE 7 a Ogni record e restituisce il summary.

    SOLO chiavi nuove: nessun campo esistente viene modificato. ``soi``,
    ``soi_v2``, ``rotation_*``, ``severe_stack`` e
    ``morphology_organization`` restano intatti.
    """
    cfg = (config or {}).get("advanced") if isinstance(config, dict) else None
    structures = getattr(bundle, "convective_structures", None) or []
    rot = _rotation_evidence(bundle)
    n_levels = sum(1 for rd in (getattr(bundle, "_volume_cappi", None) or {}).values()
                   if rd is not None)
    cappi_levels = int(_finite(getattr(bundle, "advanced_cappi_levels", None))
                       or n_levels)
    summary = {
        "version": ADVANCED_VERSION,
        "source": SOURCE,
        "structures_total": len(structures),
        "morphology_class_counts": {k: 0 for k in MORPHOLOGY_CLASSES},
        "morphology_class_null": 0,
        "morphology_null_reasons": {},
        "lifecycle_stage_counts": {k: 0 for k in LIFECYCLE_STAGES},
        "lifecycle_null": 0,
        "lifecycle_null_reasons": {},
        "tracking_phase_counts": {k: 0 for k in TRACKING_PHASES},
        "tracking_phase_null": 0,
        "tracking_phase_null_reasons": {},
        "bwer_candidate_true": 0,
        "bwer_candidate_null": 0,
        "overhang_candidate_true": 0,
        "overhang_candidate_null": 0,
        "hail_nuclei_tracks_total": 0,
        "hail_nuclei_null": 0,
        "rotation_evidence_available": bool(rot),
        "supercell_null_reason": (None if rot else ROTATION_NULL_REASON),
        "cappi_levels": cappi_levels,
        "reference": dict(REFERENCE),
    }
    events_index = _tracking_events_index(bundle)
    for rec in structures:
        shape = _shape_2d_for(bundle, rec)
        mfield = classify_morphology(rec, shape_2d=shape,
                                     rotation_evidence=rot, config=cfg)
        lfield = derive_lifecycle(rec, config=cfg)
        tid = rec.get("track_id")
        kind = "storm_object" if rec.get("track_type") == "storm_object" else "cell"
        ev_block = events_index[kind].get(tid)
        if ev_block is None:
            ev_block = events_index["cell"].get(tid)
        if ev_block is None:
            ev_block = events_index["storm_object"].get(tid)
        pfield = derive_tracking_phase(ev_block, config=cfg)
        bfield = bwer_candidate(rec, cappi_levels=cappi_levels, config=cfg)
        ofield = overhang_candidate(rec, config=cfg)
        hfield = track_hail_nuclei(
            getattr(bundle, "advanced_hail_nuclei_frames", None),
            cappi_levels=cappi_levels, config=cfg)
        rec["morphology_class"] = mfield
        rec["lifecycle_stage"] = lfield
        rec["tracking_phase"] = pfield
        rec["bwer_candidate"] = bfield
        rec["overhang_candidate"] = ofield
        rec["hail_nuclei_tracking"] = hfield

        mv = mfield["value"]
        if mv is None:
            summary["morphology_class_null"] += 1
            r = mfield["null_reason"] or "unknown"
            summary["morphology_null_reasons"][r] = (
                summary["morphology_null_reasons"].get(r, 0) + 1)
        else:
            summary["morphology_class_counts"][mv] += 1
        lv = lfield["value"]
        if lv is None:
            summary["lifecycle_null"] += 1
            r = lfield["null_reason"] or "unknown"
            summary["lifecycle_null_reasons"][r] = (
                summary["lifecycle_null_reasons"].get(r, 0) + 1)
        else:
            summary["lifecycle_stage_counts"][lv] += 1
        pv = pfield["value"]
        if pv is None:
            summary["tracking_phase_null"] += 1
            r = pfield["null_reason"] or "unknown"
            summary["tracking_phase_null_reasons"][r] = (
                summary["tracking_phase_null_reasons"].get(r, 0) + 1)
        else:
            summary["tracking_phase_counts"][pv] += 1
        if bfield["candidate"] is True:
            summary["bwer_candidate_true"] += 1
        else:
            summary["bwer_candidate_null"] += 1
        if ofield["candidate"] is True:
            summary["overhang_candidate_true"] += 1
        else:
            summary["overhang_candidate_null"] += 1
        if hfield["value"] is not None:
            summary["hail_nuclei_tracks_total"] += int(
                hfield["value"].get("tracks_total") or 0)
        else:
            summary["hail_nuclei_null"] += 1
    bundle.advanced_summary = summary
    return structures, summary
