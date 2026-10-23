#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — convective_structure.py (DESCRITTORE, non diagnosi)

structural_organization_index (SOI, 0-100) per una track radar. NON e' una
diagnosi meteorologica e NON e' una probabilita': il dato di partenza e' il
mosaico Radar-DPC VMI a SINGOLA RIFLETTIVITA' (nessun Doppler). Senza un campo
di velocita' radiale lo shear azimutale non e' DEFINITO: la rotazione
mesociclonica non e' misurabile con una soglia piu' o meno severa, e' una
grandezza assente dal dato (docs/METODI_VALIDATI_SEVERO.md, sez. 1.0 e 1.9).
Per questo ogni record emette `rotation_measurable: false` con la motivazione
esplicita, e nessun campo porta il nome della diagnosi che il dato non contiene
(ne' quello della struttura rotante, ne' quello del suo sinonimo da manuale).

Il SOI combina cinque descrittori GIA' calcolati dalla pipeline (cella +
tracking + Organization Score). I pesi sono quelli storici del layer, non
calibrati su un dataset etichettato:

    intensity_core    -> 25%  (core convettivo: max e media dBZ della track)
    organization      -> 30%  (Organization Score, banda Organized)
    core_morphology   -> 10%  (solidita' e compattezza: core coeso, NON a linee)
    persistence       -> 15%  (n frame nella finestra di analisi)
    growth_sustained  -> 20%  (crescita area + mantenimento intensita')

Livelli (descrittivi, non categorie meteorologiche):
    0-49   unorganized   SOI sotto 50: NESSUNA affermazione sull'assenza di
                         organizzazione (l'indice misura solo cio' che vede)
    50-64  organized
    65-79  strong
    80-100 intense

Il flag `candidate=True` richiede GATE espliciti PRIMA di ogni record:
intensita' reale (max_dbz >= soglia), frame minimi, Organization Score sopra
il floor della banda "Organized" e SOI >= candidate_soi. Il gate NON dichiara
una rotazione: dichiara che i descrittori disponibili superano le soglie.

`confidence` e' DERIVATA (mai una costante): vedi derive_confidence() per la
formula, i pesi e il cap applicato quando la copertura dati non e' ancora nota.
"""

from . import forecast as forecast_mod

DATA_LIMITS_NOTE = (
    "Radar-DPC VMI a singola riflettivita' (nessun Doppler): rotazione "
    "mesociclonica NON misurabile (rotation_measurable=false). SOI = "
    "descrittore morfologico composito 0-100, NON una diagnosi e NON una "
    "probabilita'."
)

# Etichetta del motivo per cui la rotazione non e' misurabile (costante, cosi'
# che client e test leggano lo stesso valore).
ROTATION_NULL_REASON = "no_radial_velocity_in_dpc_2d_mosaic"

_DEF_BANDS = [
    (0.0, 49.0, "unorganized"),
    (50.0, 64.0, "organized"),
    (65.0, 79.0, "strong"),
    (80.0, 100.0, "intense"),
]

_DEF_WEIGHTS = {
    "intensity_core": 0.25,
    "organization": 0.30,
    "core_morphology": 0.10,
    "persistence": 0.15,
    "growth_sustained": 0.20,
}

# L7 — normalizzazione della MEDIA dBZ (mean core): estremi NOMINATI (non magic).
_DBZ_MEAN_NORM_LO = 20.0   # dBZ floor per la normalizzazione della media
_DBZ_MEAN_NORM_HI = 45.0   # dBZ ceiling per la normalizzazione della media

# Pesi della confidenza derivata (somma 1.0 quando tutti e tre i termini sono
# disponibili). Vedi derive_confidence().
CONF_WEIGHTS = {"gates": 0.50, "persistence": 0.30, "coverage": 0.20}
CONF_BANDS = [
    (0.00, 0.34, "very_low"),
    (0.34, 0.55, "low"),
    (0.55, 0.75, "medium"),
    (0.75, 1.01, "high"),
]


def _cfg_defaults():
    """Blocco convective_structure di default (riflette config.CONFIG)."""
    from . import config as _cfg
    return _cfg.CONFIG["convective_structure"]


def _organized_floor(cfg):
    """Soglia minima dell'Organization Score per il gate 'organization_organized'.

    R1: NON un magic number. E' il floor della banda 'Organized Convective Cell'
    in scoring.bands (fonte unica); override opzionale con cfg
    ['organization_organized']. Fallback 56.0 solo se la banda e' assente."""
    override = cfg.get("organization_organized")
    if override is not None:
        return float(override)
    from . import config as _cfg
    for lo, _hi, name in _cfg.CONFIG["scoring"]["bands"]:
        if name == "Organized Convective Cell":
            return float(lo)
    return 56.0


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _norm(x, lo, hi):
    return _clamp01((x - lo) / max(hi - lo, 1e-9))


def _track_values(track):
    """max/mean dBZ e attributi morfologici medi dai punti della track.

    I TrackPoint/StormTrackPoint espongono max_dbz, mean_dbz, solidity e
    compactness (NON p90/eccentricita': non propagati a livello di track)."""
    maxes = [p.max_dbz for p in track.points]
    means = [p.mean_dbz for p in track.points]
    solidity = getattr(track.motion, "get", lambda _k, _d: None)("_avg_solidity", None)
    compactness = getattr(track.motion, "get", lambda _k, _d: None)("_avg_compactness", None)
    if solidity is None:
        solidity = sum(p.solidity for p in track.points) / max(len(track.points), 1)
    if compactness is None:
        compactness = sum(p.compactness for p in track.points) / max(len(track.points), 1)
    return maxes, means, float(solidity), float(compactness)


def _components(track, cfg):
    """Componenti 0..1 del SOI per una track (>=2 punti)."""
    n = len(track.points)
    maxes, means, sol, comp = _track_values(track)
    ref_hi = float(cfg["intensity_ref_dbz"][1])
    ref_lo = float(cfg["intensity_ref_dbz"][0])

    max_dbz_max = max(maxes) if maxes else 0.0
    mean_dbz_mean = sum(means) / len(means) if means else 0.0
    intensity_core = 0.6 * _norm(max_dbz_max, ref_lo, ref_hi) + \
        0.4 * _norm(mean_dbz_mean, _DBZ_MEAN_NORM_LO, _DBZ_MEAN_NORM_HI)

    org = track.motion.get("organization_score", 0) or 0
    organization = _clamp01(float(org) / 100.0)

    # core_morphology: core COESO (solidita' alta) e COMPATTO (compattezza bassa);
    # penalizza l'eco a linea (compactness molto alta) e le celle fragili.
    sol_comp = _clamp01((sol - 0.5) / 0.4)
    comp_ref = float(cfg.get("compactness_ref", 8.0))
    inv_comp = 1.0 - _clamp01((comp - 1.0) / max(comp_ref - 1.0, 1e-9))
    core_morphology = 0.5 * sol_comp + 0.5 * inv_comp

    persistence = _clamp01(n / float(cfg.get("persistence_ref_frames", 5)))

    areas = [p.area_km2 for p in track.points]
    if areas[-1] > areas[0] and areas[0] > 0:
        growth = _clamp01((areas[-1] - areas[0]) / max(areas[0], 1e-9) / 2.0)
    else:
        growth = 0.0
    intensity_kept = 1.0 - _clamp01((maxes[0] - maxes[-1]) / max(maxes[0], 1e-9)) \
        if maxes and maxes[0] > 0 else 1.0
    growth_sustained = 0.6 * growth + 0.4 * intensity_kept

    return {
        "intensity_core": round(intensity_core, 4),
        "organization": round(organization, 4),
        "core_morphology": round(core_morphology, 4),
        "persistence": round(persistence, 4),
        "growth_sustained": round(growth_sustained, 4),
    }


def _gates(track, cfg):
    """Gate espliciti (prima di qualunque record pubblicato)."""
    max_dbz_max = max((p.max_dbz for p in track.points), default=0.0)
    org = track.motion.get("organization_score", 0) or 0
    return {
        "intensity_core_dbz": max_dbz_max >= float(cfg["intensity_core_dbz"]),
        "min_frames": len(track.points) >= int(cfg.get("min_frames", 2)),
        "organization_organized": float(org) >= _organized_floor(cfg),
    }


def classify_level(soi, cfg=None):
    """Banda di livello per il SOI (0-100)."""
    cfg = cfg or _cfg_defaults()
    bands = cfg.get("bands", _DEF_BANDS)
    for lo, hi, name in bands:
        if lo <= soi <= hi:
            return name
    return "unorganized"


def _level_band(value, bands, fallback):
    for lo, hi, name in bands:
        if lo <= value < hi:
            return name
    return fallback


def derive_confidence(gates=None, n_points=0, coverage=None, cfg=None):
    """Confidenza DERIVATA (0-1) + livello, con la formula scritta qui.

    FORMULA (documentata, non calibrata su un dataset etichettato):

        conf = 0.50 * G + 0.30 * P + 0.20 * C

        G = gate superati / gate totali          (0, 1/3, 2/3, 1)
        P = clamp01((n_points - 1) / (persistence_ref_frames - 1))
            persistenza osservata: 1.0 a `persistence_ref_frames` frame
        C = descrittori Fase 2 disponibili / 5
            (hook, structure, env, ot, lightning)

    C e' ignoto quando la confidenza viene emessa dalla Fase 1 (la Fase 2 gira
    dopo, per candidato): in quel caso i pesi di G e P si RINORMALIZZANO sulla
    loro somma (0.80) e il risultato e' LIMITATO a "medium" — senza copertura
    dati non si dichiara "high". La Fase 2 richiama questa funzione con C noto
    e sovrascrive il valore (`confidence_detail.terms` mostra i tre termini).

    Ritorna dict: confidence, score, terms, bands, capped (bool).
    """
    cfg = cfg or _cfg_defaults()
    gates = gates or {}
    total = len(gates)
    passed = sum(1 for v in gates.values() if v)
    g = (passed / float(total)) if total else 0.0

    ref_frames = max(int(cfg.get("persistence_ref_frames", 5)), 2)
    p = _clamp01((max(int(n_points), 0) - 1) / float(ref_frames - 1))

    capped = coverage is None
    if coverage is None:
        wsum = CONF_WEIGHTS["gates"] + CONF_WEIGHTS["persistence"]
        score = (CONF_WEIGHTS["gates"] * g + CONF_WEIGHTS["persistence"] * p) / wsum
        c = None
        terms = {"gates": round(g, 4), "persistence": round(p, 4),
                 "coverage": None, "weights_effective": {
                     "gates": round(CONF_WEIGHTS["gates"] / wsum, 4),
                     "persistence": round(CONF_WEIGHTS["persistence"] / wsum, 4),
                     "coverage": 0.0}}
    else:
        c = _clamp01(float(coverage))
        score = (CONF_WEIGHTS["gates"] * g + CONF_WEIGHTS["persistence"] * p
                 + CONF_WEIGHTS["coverage"] * c)
        terms = {"gates": round(g, 4), "persistence": round(p, 4),
                 "coverage": round(c, 4),
                 "weights_effective": dict(CONF_WEIGHTS)}

    level = _level_band(round(score, 6), CONF_BANDS, "very_low")
    if capped and level == "high":
        level = "medium"
    return {"confidence": level, "score": round(score, 4), "terms": terms,
            "bands": CONF_BANDS, "capped": capped}


def _integral_soi(track, cfg):
    """SOI (0-100) + livello + componenti + gate. Richiede >=2 punti."""
    if len(track.points) < 2:
        return 0, "unorganized", False, {}, {}, {}
    components = _components(track, cfg)
    weights = cfg.get("weights", _DEF_WEIGHTS)
    soi = 0.0
    for key, comp in components.items():
        soi += comp * float(weights.get(key, 0.0))
    soi = int(round(100.0 * max(0.0, min(1.0, soi))))
    level = classify_level(soi, cfg)
    gates = _gates(track, cfg)
    candidate = (soi >= float(cfg.get("candidate_soi", 65.0))
                 and gates["intensity_core_dbz"] and gates["min_frames"]
                 and gates["organization_organized"])
    return soi, level, candidate, gates, components, weights


def structural_organization_index(track, cfg=None):
    """API pubblica: (soi, level, candidate, gates, components)."""
    cfg = cfg or _cfg_defaults()
    soi, level, candidate, gates, components, _w = _integral_soi(track, cfg)
    return soi, level, candidate, gates, components


def _system_key(bundle, track, track_type, cfg):
    """(chiave di sistema, nome sistema, merged_from) per il dedup.

    Chiave = identita' dello storm object che contiene l'ultimo punto della
    track (se risolvibile) + finestra temporale (ora UTC del last_seen).
    Una cella e lo storm object che la contiene finiscono cosi' sulla STESSA
    chiave e producono UN solo record, con `merged_from` che dichiara le due
    sorgenti (docs/STUDIO_SUPERCELLE_RADAR.md, difetto 7)."""
    last = track.points[-1]
    window = (last.timestamp_iso or "")[:13]        # YYYY-MM-DDTHH
    if track_type == "storm_object":
        return ("storm_object:{}".format(track.track_id), "storm_object", window)
    frame = int(getattr(last, "frame_index", -1))
    cell_id = getattr(last, "cell_id", None)
    objs = getattr(bundle, "storm_objects_by_frame", None) or []
    if cell_id is not None and 0 <= frame < len(objs):
        for obj in objs[frame] or []:
            if cell_id in (getattr(obj, "cell_ids", None) or []):
                sid = getattr(obj, "track_id", None)
                if sid is not None:
                    return ("storm_object:{}".format(sid), "cell", window)
    return ("cell:{}".format(track.track_id), "cell", window)


def _candidate_dict(bundle, track, track_type, latest_frame_index, cfg, soi,
                    level, candidate, gates, components):
    """Rappresentazione JSON (solo track candidate)."""
    n = len(track.points)
    last = track.points[-1]
    on_latest = (last.frame_index == latest_frame_index) if latest_frame_index is not None \
        else False
    phase = "birth" if candidate and n <= int(cfg.get("birth_frames", 3)) else \
        ("sustained" if candidate else "n/a")
    max_dbz_max = max(p.max_dbz for p in track.points)
    mean_dbz_mean = sum(p.mean_dbz for p in track.points) / max(n, 1)
    # D2 (1.2.4.17): lo stato pubblicato dipende dal frame. Un track chiuso a
    # fine ciclo ma osservato sull'ULTIMO frame non e' "dead": e' attivo.
    status = "active" if on_latest else track.status
    conf = derive_confidence(gates=gates, n_points=n,
                             coverage=None, cfg=cfg)
    key, origin, window = _system_key(bundle, track, track_type, cfg)
    return {
        "structure_id": "CS-{}-{}{:03d}".format(
            (last.timestamp_iso or "")[:13], track_type[:3], int(track.track_id)),
        "system_key": key,
        "time_window": window,
        "track_id": track.track_id,
        "track_type": track_type,
        "cell_id": getattr(last, "cell_id", None),
        "storm_object_id": getattr(last, "storm_object_id", None),
        "soi": soi,
        "n_points": n,
        "level": level,
        "candidate": candidate,
        "phase": phase,
        "status": status,
        "track_status_raw": track.status,
        "on_latest_frame": on_latest,
        "rotation_measurable": False,
        "rotation_null_reason": ROTATION_NULL_REASON,
        "first_seen": track.points[0].timestamp_iso,
        "last_seen": last.timestamp_iso,
        "position": [round(last.lonlat[0], 5), round(last.lonlat[1], 5)],
        "intensity": {
            "max_dbz": round(max_dbz_max, 2),
            "mean_dbz": round(mean_dbz_mean, 2),
            "delta_dbz": track.motion.get("intensity_delta_dbz"),
        },
        "organization": {
            "organization_score": track.motion.get("organization_score"),
            "classification": track.motion.get("classification"),
        },
        "motion": {
            "velocity_kmh": track.motion.get("velocity_kmh"),
            "direction_toward_deg": track.motion.get("direction_toward_deg"),
            "distance_km": track.motion.get("distance_km"),
            "duration_min": track.motion.get("duration_min"),
            "area_growth_pct": track.motion.get("area_growth_pct"),
        },
        "components": components,
        "gates": gates,
        "confidence": conf["confidence"],
        "confidence_detail": conf,
        "merged_from": [origin],
    }


def _evaluated_tracks(bundle):
    """Lista (track, track_type) da valutare (layer cella + storm object)."""
    out = [(t, "cell") for t in getattr(bundle, "tracks", [])]
    out += [(t, "storm_object") for t in getattr(bundle, "storm_tracks", [])]
    return out


def dedup_records(records):
    """UN record per sistema (chiave: sistema + finestra temporale).

    Regola di fusione DETERMINISTICA: lo storm object e' l'identita' aggregata
    e resta la base del record; il record cella viene allegato in
    `merged_cell` (stesso `first_seen` implica stesso oggetto: nessuna cella
    viene fusa su un sistema diverso). `merged_from` elenca le sorgenti.
    L'ordine di uscita e' stabile: (-soi, track_type, track_id)."""
    merged = {}
    order = []
    for rec in records:
        key = (rec.get("system_key"), rec.get("time_window"))
        if key not in merged:
            merged[key] = dict(rec)
            merged[key]["merged_from"] = list(rec.get("merged_from") or [])
            order.append(key)
            continue
        base = merged[key]
        incoming = dict(rec)
        if base.get("track_type") == "storm_object":
            base["merged_cell"] = {
                "track_id": incoming.get("track_id"),
                "structure_id": incoming.get("structure_id"),
                "soi": incoming.get("soi"),
                "level": incoming.get("level"),
                "first_seen": incoming.get("first_seen"),
                "last_seen": incoming.get("last_seen"),
            }
            base["merged_from"] = ["cell", "storm_object"]
        else:
            # base = cella: promuove lo storm object a base (identita' aggregata)
            obj = dict(rec)
            cell = base
            obj["merged_from"] = ["cell", "storm_object"]
            obj["merged_cell"] = {
                "track_id": cell.get("track_id"),
                "structure_id": cell.get("structure_id"),
                "soi": cell.get("soi"),
                "level": cell.get("level"),
                "first_seen": cell.get("first_seen"),
                "last_seen": cell.get("last_seen"),
            }
            merged[key] = obj
    out = []
    for key in order:
        rec = merged[key]
        rec["merged_from"] = sorted(set(rec.get("merged_from") or []),
                                    key=lambda s: (s != "cell", s))
        out.append(rec)
    out.sort(key=lambda r: (-int(r.get("soi") or 0),
                            str(r.get("track_type") or ""),
                            int(r.get("track_id") or 0)))
    return out


def evaluate(bundle, cfg=None):
    """Valuta tutte le track (cella + storm object) e popola
    bundle.convective_structures (solo candidati, DEDUP per sistema) e
    bundle.structure_tracks_evaluated.

    Layer ADDITIVO: mai fallisce il run (errori -> warning in main.py).
    Ritorna la lista dei record. Ogni record riceve la chiave `forecast`
    (forecast.py, blocco <=2h o None se non computabile)."""
    cfg = cfg or _cfg_defaults()
    latest_frame_index = None
    frames = getattr(bundle, "cells_by_frame", None)
    if frames:
        # D1 (1.2.4.17): l'indice del frame piu' recente e' len(cells_by_frame)-1.
        # Prima si contavano i frame NON VUOTI: con un frame vuoto in mezzo
        # l'indice scendeva e `on_latest_frame` diventava falso per tutti.
        latest_frame_index = len(frames) - 1
    raw = []
    for track, track_type in _evaluated_tracks(bundle):
        if len(track.points) < 2:
            continue
        soi, level, candidate, gates, components, _w = _integral_soi(track, cfg)
        if not candidate:
            continue
        raw.append(_candidate_dict(
            bundle, track, track_type, latest_frame_index, cfg, soi, level,
            candidate, gates, components))
    records = dedup_records(raw)
    for rec in records:
        track = _track_by_key(bundle, rec)
        rec["forecast"] = (forecast_mod.build_forecast(
            {**rec, "track": track}, bundle) if track is not None else None)
    bundle.convective_structures = records
    bundle.structure_tracks_evaluated = len(_evaluated_tracks(bundle))
    return records


def _track_by_key(bundle, record):
    """Track usata per il forecast (identita' del record, non un'altra)."""
    track_type = record.get("track_type")
    pool = (getattr(bundle, "storm_tracks", None) if track_type == "storm_object"
            else getattr(bundle, "tracks", None)) or []
    for trk in pool:
        if int(getattr(trk, "track_id", -1)) == int(record.get("track_id", -2)):
            return trk
    return None


# Alias storici: mantenuti per i chiamanti esistenti (stessa funzione).
supercell_signature_index = structural_organization_index
