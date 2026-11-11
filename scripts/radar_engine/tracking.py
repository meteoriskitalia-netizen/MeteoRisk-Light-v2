#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — tracking.py (Fase 1.6: stabilization)

Storm tracking multi-frame (Hungarian / linear_sum_assignment, mantenuto come
base) con cost function IMPROVED opzionale, motion prediction semplice
(NON Kalman), temporal memory e gating fisico configurabile.

P0 FIX (Fase 1.5 -> 1.6):
  La configurazione scoring è ora INIETTATA (per dependency injection) nel
  Tracker: Tracker(cfg_tracking, cfg_scoring) e/o finalize(cfg_scoring).
  Nessun mutable state globale; il fallback a CONFIG viene usato SOLO quando
  il chiamante non fornisce nulla (libreria), ed è esplicitamente deprecato.

Cost function LEGACY (Fase 1, usata anche in 1.6 come baseline):
    cost = w_distance * d_km
         + w_area     * |ΔA|/max(A_prev,A_curr)
         + w_intensity* |ΔdBZmax|
         + w_overlap  * (1 - IoU_circle)
    (vedi cell_cost; scala invariata, peso legato a max_assignment_cost)

Cost function IMPROVED (cost_formula='improved', normalizzata, opzionale):
    total_cost =
        w_distance  * min(1, d / distance_ref_km)
      + w_prediction* min(1, pred_error / prediction_ref_km)
      + w_area      * |ΔA|/max(A_prev,A_curr)
      + w_intensity * min(1, |ΔdBZmax| / intensity_ref_dbz_cost)
      + w_overlap   * (1 - IoU_circle)
    Tutte le quantità sono normalizzate 0..1; unità documentate nei commenti.

GATING (prima di Hungarian, MAI hardcodato):
    max_storm_speed_kmh * dt  -> raggio massimo di step rispetto all'ultima
    posizione; con predizione attiva anche raggio rispetto alla posizione
    prevista (prediction_gate_km, default = step gate). Candidati fuori gate
    hanno costo = INF e non vengono assegnati (status rejected_cost).

Temporal memory:
    Per il matching si usano le MEDIE degli ultimi N=memory_frames frame per
    area e max dBZ, e una velocità stimata dall'ultimo segmento (>=2 punti)
    per la predizione: predicted = last_position + velocity_hat * dt.
    Se la track ha <2 osservazioni -> fallback distanza dal centroide.

Diagnostica (Parte B, FORMALMENTE OPZIONALE):
    Tracker(diagnostics=True) o cfg 'diagnostics' -> per ogni match/frame
    registra: track_id, prev_cell_id, current_cell_id, distance_km,
    area_ratio, intensity_difference, IoU, prediction_error_km, total_cost,
    assignment_status (matched|new_track|terminated|
                       ambiguous_merge_candidate|ambiguous_split_candidate|
                       rejected_cost).
    La diagnostica NON entra nel dataset pubblico (output.py non la usa).

Merge/split (Parte E): candidati diagnostici (ambiguous_merge_candidate /
ambiguous_split_candidate) SENZA causalità meteorologica; il comportamento
'ambiguous' della Fase 1 resta invariato (EVENT_AMBIGUOUS).
"""

import datetime as _dt
import math
import statistics

import numpy as np
from scipy.optimize import linear_sum_assignment

from . import models

_EPS = 1e-12
# Soglia di residuo per PUBBLICARE il moto del fit pesato (finalize): sopra
# 6 km il fit non descrive i centroidi e il valore resta null (motivo esplicito).
_MOTION_MAX_RESID_KM = 6.0
# Lineage merge/split (2026-11-11): un oggetto a t e' lo STESSO sistema di un
# oggetto a t+1 se i due dischi (centroide + area equivalente) si sovrappongono
# per almeno il 20% dell'unione. E' un criterio geometrico, non una firma
# meteorologica: serve solo a registrare la relazione, non a fondere gli ID.
_LINEAGE_IOU = 0.2

# ---------------------------------------------------------------------------
# Geografia / distanza
# ---------------------------------------------------------------------------
def haversine_km(lonlat1, lonlat2):
    """Distanza great-circle (geodetica) in km tra due (lon, lat).

    Usa il raggio medio terrestre R=6371.0088 km ed è la convenzione DISTANZA
    del radar engine (tracking, kinematics, storm_tracking, scoring). NON è la
    stessa formula di `common.km_between` (equirettangolare, 111.32 km/grado),
    usata per griglia/sampling: a ~41°N la deviazione relativa tra le due è
    <0.5% nel range 10–200 km (v. tests/radar_engine/test_distance_consistency).
    """
    lon1, lat1 = map(math.radians, lonlat1)
    lon2, lat2 = map(math.radians, lonlat2)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lonlat1, lonlat2):
    """Bearing iniziale (deg, 0=N) da p1 a p2."""
    lon1, lat1 = map(math.radians, lonlat1)
    lon2, lat2 = map(math.radians, lonlat2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    brng = math.degrees(math.atan2(x, y))
    return (brng + 360.0) % 360.0


def dest_from(lonlat, bearing, dist_km):
    """Punto di arrivo (lon, lat) da un punto, per rotta e distanza (equirect.)."""
    R = 6371.0088
    lon1, lat1 = map(math.radians, lonlat)
    ang = dist_km / R
    brg = math.radians(bearing)
    lat2 = math.asin(math.sin(lat1) * math.cos(ang)
                     + math.cos(lat1) * math.sin(ang) * math.cos(brg))
    lon2 = lon1 + math.atan2(math.sin(brg) * math.sin(ang) * math.cos(lat1),
                             math.cos(ang) - math.sin(lat1) * math.sin(lat2))
    return (math.degrees(lon2), math.degrees(lat2))


def _circle_iou(lonlat1, area1, lonlat2, area2):
    """IoU di due dischi (raggio da area) a distanza d = haversine."""
    r1 = math.sqrt(max(area1, _EPS) / math.pi)
    r2 = math.sqrt(max(area2, _EPS) / math.pi)
    d = haversine_km(lonlat1, lonlat2)
    if d >= r1 + r2:
        return 0.0
    if d <= abs(r1 - r2):
        return 1.0
    r1s, r2s = r1 * r1, r2 * r2
    a1 = r1s * math.acos((d * d + r1s - r2s) / (2 * d * r1))
    a2 = r2s * math.acos((d * d + r2s - r1s) / (2 * d * r2))
    a_region = 0.5 * math.sqrt(max(0.0,
                                   (-d + r1 + r2) * (d + r1 - r2) *
                                   (d - r1 + r2) * (d + r1 + r2)))
    inter = max(0.0, a1 + a2 - a_region)
    union = math.pi * (r1s + r2s) - inter
    return inter / max(union, _EPS)


# ---------------------------------------------------------------------------
# Lineage merge/split (2026-11-11)
# ---------------------------------------------------------------------------
def _object_lonlat(obj):
    """(lon, lat) di una cella o di uno storm object (stessa convenzione)."""
    return (getattr(obj, "centroid_lonlat", None)
            or getattr(obj, "lonlat", None))


def lineage_plan(active, objects, assignment, reversed_assign,
                 iou_threshold=_LINEAGE_IOU):
    """Relazioni merge/split del frame, senza cambiare l'assegnazione.

    Regola: si guarda l'IoU fra l'ultimo punto di ogni track ATTIVA e ogni
    oggetto del frame corrente. Il piano NON modifica gli ID: dice solo quali
    track nuove (colonne non assegnate) ereditano un genitore e quali track
    morte (righe non assegnate) sono assorbite da un figlio.

    - `split_new`     {col: [parent_track_id, ...]}: il figlio nasce nuovo ma
      contiene la massa di un genitore (che continua o muore).
    - `merge_new`     {col: [parent_track_id, ...]}: il figlio nasce nuovo e
      assorbe una track morta.
    - `merge_row`     {row: [parent_track_id, ...]}: la track (che continua)
      assorbe una track morta.
    - `split_existing`{row: [parent_track_id, ...]}: una track che continua
      eredita la massa di una track morta che si e' divisa su piu' oggetti.

    Conservativo: un genitore che continua e ha un secondo oggetto gia'
    assegnato a un'altra track non crea un legame (entrambe le identita'
    esistono gia': non c'e' un ID nuovo da agganciare). Il caso "due oggetti
    nuovi da un genitore" e "due genitori su un oggetto" sono coperti.
    """
    plan = {"split_new": {}, "merge_new": {}, "merge_row": {},
            "split_existing": {}, "matched_rows": set(assignment)}
    if not active or not objects:
        return plan
    n_r, n_c = len(active), len(objects)
    iou = [[0.0] * n_c for _ in range(n_r)]
    for i, track in enumerate(active):
        prev = track.points[-1]
        for j, obj in enumerate(objects):
            coord = _object_lonlat(obj)
            area = getattr(obj, "area_km2", None)
            if coord is None or area is None:
                continue
            iou[i][j] = _circle_iou(prev.lonlat, prev.area_km2,
                                    coord, float(area))
    for i, track in enumerate(active):
        cols = [j for j in range(n_c) if iou[i][j] > float(iou_threshold)]
        if not cols:
            continue
        matched_col = assignment.get(i)
        if matched_col is not None:
            # La track continua: i figli NUOVI che si sovrappongono a lei
            # nascono da una sua divisione.
            for j in cols:
                if j == matched_col or j in reversed_assign:
                    continue
                plan["split_new"].setdefault(j, []).append(track.track_id)
            continue
        # La track non ha un oggetto assegnato: muore. Un solo oggetto
        # sovrapposto -> merge; due o piu' -> split (anche verso track che
        # continuano con un'altra identita').
        if len(cols) == 1:
            j = cols[0]
            row = reversed_assign.get(j)
            if row is None:
                plan["merge_new"].setdefault(j, []).append(track.track_id)
            else:
                plan["merge_row"].setdefault(row, []).append(track.track_id)
        else:
            for j in cols:
                row = reversed_assign.get(j)
                if row is None:
                    plan["split_new"].setdefault(j, []).append(track.track_id)
                else:
                    plan["split_existing"].setdefault(row, []).append(track.track_id)
    return plan


def _lineage_event(track, event):
    """Aggiunge un evento di lineage una sola volta (audit, non stato)."""
    if event not in track.events:
        track.events.append(event)


def apply_lineage(active, new_tracks_by_col, plan):
    """Applica il piano: parent_ids/child_ids/lineage + eventi di audit.

    Precedenza: `merge` vince su `split` quando la stessa track e' coinvolta in
    entrambi nello stesso frame (assorbe un genitore e nasce/continua da una
    divisione). `continuation` viene assegnato solo a una track che continua se
    non ha gia' una relazione: non sovrascrive l'origine split/merge.
    """
    by_id = {t.track_id: t for t in active}

    def _link(child, parent_ids, kind):
        linked = False
        for parent_id in parent_ids:
            if parent_id == child.track_id:
                continue
            parent = by_id.get(parent_id)
            # Niente cicli: un figlio che si divide e poi viene riassorbito
            # dal genitore produrrebbe 1 -> 71 -> 1. La relazione temporale
            # resta registrata dal primo legame; il secondo viene saltato
            # (un forecast che risale i parent non deve entrare in loop).
            if parent_id in (getattr(child, "child_ids", None) or []):
                continue
            if parent is not None and child.track_id in \
                    (getattr(parent, "child_ids", None) or []):
                continue
            if parent_id not in child.parent_ids:
                child.parent_ids.append(parent_id)
            linked = True
            if parent is not None and child.track_id not in parent.child_ids:
                parent.child_ids.append(child.track_id)
                _lineage_event(parent, "lineage_" + kind)
        if linked:
            if kind == "merge" or child.lineage != "merge":
                child.lineage = kind
            _lineage_event(child, "lineage_" + kind)

    for col, parent_ids in plan["split_new"].items():
        child = new_tracks_by_col.get(col)
        if child is not None:
            _link(child, parent_ids, "split")
    for col, parent_ids in plan["merge_new"].items():
        child = new_tracks_by_col.get(col)
        if child is not None:
            _link(child, parent_ids, "merge")
    for row, parent_ids in plan["merge_row"].items():
        if 0 <= row < len(active):
            _link(active[row], parent_ids, "merge")
    for row, parent_ids in plan["split_existing"].items():
        if 0 <= row < len(active):
            _link(active[row], parent_ids, "split")
    for row in plan["matched_rows"]:
        if 0 <= row < len(active) and active[row].lineage is None:
            active[row].lineage = "continuation"


# ---------------------------------------------------------------------------
# Metriche di regressione del tracking (2026-11-11)
# ---------------------------------------------------------------------------
def tracking_metrics(tracks, track_type="cell", max_speed_kmh=None,
                     reid_window_min=15.0, reid_radius_km=20.0,
                     lineage_iou=_LINEAGE_IOU):
    """Metriche per-layer, calcolate sui record PUBBLICATI (n_frames >= 2).

    Definizioni (dichiarate perche' non sono standard):
    - `pct_reliable`: % di track con `motion_quality == "reliable"`.
    - `median_duration_min`: mediana della durata delle track ATTIVE.
    - `id_switch_rate`: (salti > max_speed + re-ID inspiegati) / n_track.
      Un re-ID e' una track che nasce entro `reid_window_min` e
      `reid_radius_km` dalla morte di un'altra track senza un legame di
      lineage. I salti sono segmenti consecutivi con velocita' implicita
      sopra `max_speed_kmh` (default: la soglia unica di forecast.py).
    - `lineage_split` / `lineage_merge`: track con quel `lineage`.
    """
    tracks = list(tracks or [])
    published = [t for t in tracks
                 if len(getattr(t, "points", None) or []) >= 2]
    n = len(published)
    reliable = 0
    for t in published:
        motion = getattr(t, "motion", None) or {}
        if motion.get("motion_quality") == "reliable":
            reliable += 1
    active = [t for t in published
              if getattr(t, "status", None) == models.STATUS_ACTIVE]
    durations = []
    current_durations = []
    for t in active:
        motion = getattr(t, "motion", None) or {}
        value = motion.get("duration_min")
        if value is None and t.points:
            value = (t.points[-1].timestamp_ms
                     - t.points[0].timestamp_ms) / 60000.0
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        if value is not None and math.isfinite(value):
            durations.append(value)
        # Durata senza il punto di archivio (frame_index=-1): audit separato,
        # perche' il seed puo' allungare la track con una osservazione REALE
        # del run precedente. Non sostituisce la metrica richiesta.
        pts_now = [p for p in t.points
                   if getattr(p, "frame_index", 0) >= 0]
        if len(pts_now) >= 2:
            current_durations.append(
                (pts_now[-1].timestamp_ms - pts_now[0].timestamp_ms) / 60000.0)
    max_speed = float(max_speed_kmh) if max_speed_kmh is not None \
        else float(_speed_range_kmh()[1])
    jumps = 0
    for t in published:
        for a, b in zip(t.points, t.points[1:]):
            dt_h = (b.timestamp_ms - a.timestamp_ms) / 3600000.0
            if dt_h > 0 and haversine_km(a.lonlat, b.lonlat) / dt_h > max_speed:
                jumps += 1
    reids = 0
    for child in published:
        if getattr(child, "continued_from_history", False):
            continue
        first = child.points[0]
        parents = set(getattr(child, "parent_ids", None) or [])
        for parent in published:
            if parent is child:
                continue
            if getattr(parent, "status", None) != models.STATUS_DEAD:
                continue
            last = parent.points[-1]
            gap_min = (first.timestamp_ms - last.timestamp_ms) / 60000.0
            if gap_min < 0 or gap_min > reid_window_min:
                continue
            if haversine_km(last.lonlat, first.lonlat) > reid_radius_km:
                continue
            if parent.track_id in parents or child.track_id in \
                    (getattr(parent, "child_ids", None) or []):
                continue
            reids += 1
            break
    split = sum(1 for t in published
                if getattr(t, "lineage", None) == "split")
    merge = sum(1 for t in published
                if getattr(t, "lineage", None) == "merge")
    continuation = sum(1 for t in published
                       if getattr(t, "lineage", None) == "continuation")
    links = sum(len(getattr(t, "parent_ids", None) or []) for t in published)
    return {
        "track_type": track_type,
        "n_tracks": n,
        "n_active_tracks": len(active),
        "n_reliable": reliable,
        "pct_reliable": round(100.0 * reliable / n, 1) if n else None,
        "median_duration_min": (round(statistics.median(durations), 1)
                                if durations else None),
        "median_duration_current_min": (
            round(statistics.median(current_durations), 1)
            if current_durations else None),
        "id_switch_rate": (round((jumps + reids) / n, 3) if n else None),
        "id_switch_jumps": jumps,
        "id_switch_reids": reids,
        "lineage_split": split,
        "lineage_merge": merge,
        "lineage_continuation": continuation,
        "lineage_links": links,
        # n_frames = frame del ciclo corrente (come models.to_dict): il punto
        # di archivio frame_index=-1 NON conta. `n_frames_current_ge_3` resta
        # come alias esplicito per l'audit.
        "n_frames_ge_3": sum(
            1 for t in published
            if len([p for p in t.points
                    if getattr(p, "frame_index", 0) >= 0]) >= 3),
        "n_frames_current_ge_3": sum(
            1 for t in published
            if len([p for p in t.points
                    if getattr(p, "frame_index", 0) >= 0]) >= 3),
        "continued_from_history": sum(
            1 for t in published
            if getattr(t, "continued_from_history", False)),
        "thresholds": {
            "max_speed_kmh": max_speed,
            "reid_window_min": float(reid_window_min),
            "reid_radius_km": float(reid_radius_km),
            "lineage_iou": float(lineage_iou),
        },
    }


def build_tracking_metrics(cell_tracks, storm_tracks, **kwargs):
    """Blocco `tracking_metrics` per latest.json: per-layer + aggregato.

    I campi piatti (pct_reliable, median_duration_min, id_switch_rate,
    lineage_split, lineage_merge) sono l'AGGREGATO cella+storm object: nessuna
    selezione a posteriori del layer che migliora. Il dettaglio per layer e'
    in `cell` e `storm_object`.
    """
    cell = tracking_metrics(cell_tracks, "cell", **kwargs)
    storm = tracking_metrics(storm_tracks, "storm_object", **kwargs)
    combined = tracking_metrics(list(cell_tracks or []) + list(storm_tracks or []),
                                "combined", **kwargs)
    # I re-ID NON si contano fra layer diversi: una cella che nasce vicino a
    # uno storm object morto non e' un ID switch. L'aggregato somma i conteggi
    # per layer e ricalcola il rate sul totale (bug corretto il 2026-11-11:
    # prima il combined contava anche le coppie cross-layer).
    combined["id_switch_jumps"] = (cell["id_switch_jumps"]
                                   + storm["id_switch_jumps"])
    combined["id_switch_reids"] = (cell["id_switch_reids"]
                                   + storm["id_switch_reids"])
    n_combined = combined["n_tracks"]
    combined["id_switch_rate"] = (
        round((combined["id_switch_jumps"] + combined["id_switch_reids"])
              / n_combined, 3) if n_combined else None)
    combined["n_reliable"] = cell["n_reliable"] + storm["n_reliable"]
    out = {"primary_layer": "combined"}
    for key in ("pct_reliable", "median_duration_min",
                "median_duration_current_min", "id_switch_rate",
                "lineage_split", "lineage_merge", "lineage_continuation",
                "lineage_links", "id_switch_jumps", "id_switch_reids",
                "n_tracks", "n_active_tracks", "n_reliable",
                "n_frames_ge_3", "n_frames_current_ge_3",
                "continued_from_history", "thresholds"):
        out[key] = combined[key]
    out["cell"] = cell
    out["storm_object"] = storm
    out["combined"] = combined
    return out


# ---------------------------------------------------------------------------
# Cost function
# ---------------------------------------------------------------------------
def cell_cost(prev_cell, curr_cell, cfg):
    """Cost function LEGACY (Fase 1). Scala invariata, da abbinare a
    max_assignment_cost. prev_cell puo' essere TrackPoint o DetectedCell."""
    wd = float(cfg["w_distance"])
    wa = float(cfg["w_area"])
    wi = float(cfg["w_intensity"])
    wo = float(cfg["w_overlap"])

    p_lonlat = _plonlat(prev_cell)
    p_area = getattr(prev_cell, "area_km2", None) or prev_cell.area_km2
    p_max = getattr(prev_cell, "max_dbz", None) or prev_cell.max_dbz

    distance = haversine_km(p_lonlat, curr_cell.centroid_lonlat)
    area_norm = abs(p_area - curr_cell.area_km2) / \
        max(p_area, curr_cell.area_km2, _EPS)
    intensity = abs(p_max - curr_cell.max_dbz)
    overlap = 1.0 - _circle_iou(p_lonlat, p_area,
                                 curr_cell.centroid_lonlat, curr_cell.area_km2)
    return wd * distance + wa * area_norm + wi * intensity + wo * overlap


def match_cost_improved(features, cell, cfg):
    """Cost function IMPROVED (normalizzata 0..1 per termine).

    features (dict, da Tracker._features):
        last_lonlat, area_avg_km2, dbz_avg, predicted_lonlat (o None),
        last_ts_ms (timestamp ultima osservazione)
    Termini normalizzati:
        d / distance_ref_km                       [0..1, rif 100 km]
        pred_error / prediction_ref_km             [0..1, rif 50 km]
        |ΔA|/max(A)                                [0..1]
        min(1, |ΔdBZ|/intensity_ref_dbz_cost)      [0..1, rif 35 dBZ]
        1 - IoU_circle                             [0..1]
    """
    wd = float(cfg["w_distance"])
    wp = float(cfg.get("w_prediction", 0.0))
    wa = float(cfg["w_area"])
    wi = float(cfg["w_intensity"])
    wo = float(cfg["w_overlap"])

    last = features["last_lonlat"]
    d = haversine_km(last, cell.centroid_lonlat)
    dist_ref = float(cfg.get("distance_ref_km", 100.0))
    nd = min(1.0, d / max(dist_ref, _EPS))

    pred_term = 0.0
    if wp > 0 and features.get("predicted_lonlat"):
        ref_p = float(cfg.get("prediction_ref_km", 50.0))
        pe = haversine_km(features["predicted_lonlat"], cell.centroid_lonlat)
        pred_term = min(1.0, pe / max(ref_p, _EPS))

    area_norm = abs(features["area_avg_km2"] - cell.area_km2) / \
        max(features["area_avg_km2"], cell.area_km2, _EPS)
    # default dalla config base (fonte unica), non un magic number
    from . import config as _config
    i_ref = float(cfg.get(
        "intensity_ref_dbz_cost",
        _config.CONFIG["tracking"]["intensity_ref_dbz_cost"]))
    int_norm = min(1.0, abs(features["dbz_avg"] - cell.max_dbz) / max(i_ref, _EPS))
    overlap = 1.0 - _circle_iou(last, features["area_avg_km2"],
                                 cell.centroid_lonlat, cell.area_km2)

    return (wd * nd + wp * pred_term + wa * area_norm
            + wi * int_norm + wo * overlap)


def _plonlat(obj):
    return getattr(obj, "centroid_lonlat", None) or obj.lonlat


def _speed_range_kmh():
    """(lo, hi) della velocita' PLAUSIBILE — FONTE UNICA: forecast.py.

    Import locale: forecast importa questo modulo (bearing_deg/dest_from/
    haversine_km), quindi un import a livello di modulo sarebbe circolare.
    Nessun valore duplicato qui: se il range cambia, cambia in un posto solo.
    """
    from . import forecast as _forecast
    return _forecast.SPEED_RANGE_KMH


class _SeedCell:
    """Vista minima di una track dell'archivio come cella (per TrackPoint).

    Non inventa geometria: `lonlat` e `area_km2` arrivano dallo slot; la
    morfologia mancante negli slot scritti prima del 2026-11-11 usa il neutro
    1.0 (solidity/compactness), che non altera il matching (solo lo scoring).
    """

    def __init__(self, seed):
        self.cell_id = "history:{}".format(seed.get("uid"))
        self.timestamp_ms = int(seed["timestamp_ms"])
        iso = seed.get("timestamp_iso")
        if not iso:
            iso = _dt.datetime.fromtimestamp(
                self.timestamp_ms / 1000.0, _dt.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.timestamp_iso = iso
        self.centroid_lonlat = tuple(seed["lonlat"])
        self.area_km2 = float(seed["area_km2"])
        self.max_dbz = float(seed.get("max_dbz") or 0.0)
        self.mean_dbz = float(seed.get("mean_dbz") or 0.0)
        self.solidity = float(seed["solidity"]
                              if seed.get("solidity") is not None else 1.0)
        self.compactness = float(seed["compactness"]
                                 if seed.get("compactness") is not None else 1.0)
        self.eccentricity = None
        self.frame_index = -1


class Tracker:
    """Aggrega celle in track multi-frame (Hungarian per frame).

    Fase 1.6: accetta config SCORING iniettata (cfg_scoring) e la inoltra a
    finalize()/organization_score() nel caso il chiamante non la fornisca a
    finalize(cfg_scoring=...). La diagnmat/opzionale non altera il risultato."""

    def __init__(self, cfg_tracking, cfg_scoring=None, diagnostics=False,
                 history_dir=None, seed_max_km=20.0, seed_max_age_min=30.0,
                 seed_max_speed_kmh=None):
        self.cfg = cfg_tracking
        self.cfg_scoring = cfg_scoring
        self.tracks = []
        self._next_track_id = 1
        self.diag = []                      # diagnostica per-match (opzionale)
        self._diagnostics = bool(diagnostics or cfg_tracking.get("diagnostics", False))
        self._improved = str(cfg_tracking.get("cost_formula", "legacy")) == "improved"
        # D2 (1.2.4.17): ultimo frame PROCESSATO. Serve a complete_cycles per
        # non dichiarare "dead" una track osservata sull'ultimo frame.
        self._last_frame_index = None
        # Seed dall'archivio rolling (2026-11-11). `history_dir` None = off
        # (replay, libreria, test). Il seed e' ONE-SHOT: si tenta al primo
        # frame con celle e nessuna track attiva, poi non si ripete.
        self.history_dir = history_dir
        self.seed_max_km = float(seed_max_km)
        self.seed_max_age_min = float(seed_max_age_min)
        self.seed_max_speed_kmh = (float(seed_max_speed_kmh)
                                   if seed_max_speed_kmh is not None else None)
        self._seed_attempted = False

    # -- feature/diagnostica helper ----------------------------------------
    def _features(self, track, cfg):
        """Mappa di matching per una track attiva (temporal memory + predizione).

        Il punto di archivio (frame_index=-1) e' un'ancora di STORIA, non un
        frame del ciclo corrente: non entra nella predizione del matching.
        Altrimenti un seed a 20 km sposterebbe la previsione al frame 1 e
        cambierebbe l'associazione rispetto al codice senza seed.
        """
        pts = [p for p in track.points
               if getattr(p, "frame_index", 0) >= 0] or track.points
        last = pts[-1]
        mem = int(cfg.get("memory_frames", 3))
        tail = pts[-mem:]
        area_avg = sum(p.area_km2 for p in tail) / len(tail)
        dbz_avg = sum(p.max_dbz for p in tail) / len(tail)
        predicted, speed_kmh = None, None
        if len(pts) >= 2:
            a, b = pts[-2], pts[-1]
            dt_h = (b.timestamp_ms - a.timestamp_ms) / 3600000.0
            if dt_h > 0:
                d = haversine_km(a.lonlat, b.lonlat)
                speed_kmh = d / dt_h
                if d > 1e-9:
                    brg = bearing_deg(a.lonlat, b.lonlat)
                    # predizione rispetto al delta fino al frame corrente:
                    # il valore TARGET viene calcolato in update() con il
                    # timestamp del candidato; qui forniamo velocity_hat.
                    predicted = {
                        "speed_kmh": speed_kmh, "bearing": brg,
                        "from_ts_ms": b.timestamp_ms, "from_lonlat": b.lonlat,
                    }
        return {
            "last_lonlat": last.lonlat,
            "last_ts_ms": last.timestamp_ms,
            "area_avg_km2": area_avg,
            "dbz_avg": dbz_avg,
            "predicted": predicted,
        }

    def _target_prediction(self, features, cell_ts_ms):
        """Posizione prevista al tempo della cella corrente (se velocity nota)."""
        if not features.get("predicted"):
            return None
        pr = features["predicted"]
        dt_h = (cell_ts_ms - pr["from_ts_ms"]) / 3600000.0
        if dt_h <= 0:
            return None
        dist_km = pr["speed_kmh"] * dt_h
        return dest_from(pr["from_lonlat"], pr["bearing"], dist_km)

    def _new_track(self, cell, frame_index, seed=None):
        track = models.Track(self._next_track_id, cell.timestamp_ms)
        self._next_track_id += 1
        if seed is not None:
            # Continuazione cross-run: il punto dell'archivio e' una
            # OSSERVAZIONE REALE (stesso sistema, run precedente), non un
            # valore interpolato. frame_index=-1 lo distingue dai frame del
            # ciclo corrente e non interferisce con complete_cycles.
            prior = _SeedCell(seed)
            track.points.append(models.TrackPoint(track.track_id, -1, prior))
            track.birth_ms = int(prior.timestamp_ms)
            track.continued_from_history = True
            track.history_uid = seed.get("uid")
            track.history_slot = seed.get("slot")
            track.history_age_min = seed.get("age_min")
            track.lineage = "continuation"
            track.events.append("continued_from_history")
        track.points.append(models.TrackPoint(track.track_id, frame_index, cell))
        if seed is None:
            track.events.append(models.EVENT_BIRTH)
        return track

    def _seed_matches(self, cells):
        """Accoppia le celle del primo frame alle track dell'archivio.

        Vincoli (2026-11-11): entro 20 km e 30 min, con la stessa soglia
        cinematica del tracker (110 km/h). Accoppiamento greedy uno-a-uno per
        distanza: nessuna track dell'archivio puo' generare due track e nessuna
        cella puo' assorbire due seed. Un archivio illeggibile -> nessun seed.
        """
        if self._seed_attempted or not self.history_dir or not cells:
            return {}
        self._seed_attempted = True
        from . import history as history_mod
        try:
            seeds = history_mod.load_track_seeds(
                self.history_dir, int(cells[0].timestamp_ms),
                track_type="cell", max_age_min=self.seed_max_age_min)
        except Exception:
            return {}
        if not seeds:
            return {}
        max_speed = self.seed_max_speed_kmh
        if max_speed is None:
            max_speed = float(_speed_range_kmh()[1])
        candidates = []
        for ci, cell in enumerate(cells):
            for si, seed in enumerate(seeds):
                dist = haversine_km(tuple(seed["lonlat"]), cell.centroid_lonlat)
                if dist > self.seed_max_km:
                    continue
                age_h = float(seed["age_min"]) / 60.0
                if age_h <= 0:
                    continue
                if dist / age_h > max_speed:
                    continue
                candidates.append((dist, ci, si))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        used_cells, used_seeds, out = set(), set(), {}
        for _dist, ci, si in candidates:
            if ci in used_cells or si in used_seeds:
                continue
            out[ci] = seeds[si]
            used_cells.add(ci)
            used_seeds.add(si)
        return out

    def _gate_limits(self, features, cell, cfg):
        """Raggi massimi (km): rispetto all'ultima posizione e (se predizione
        attiva) rispetto alla posizione prevista. Gate = None -> disabled."""
        max_speed = cfg.get("max_storm_speed_kmh")
        if not max_speed:
            return None, None
        dt_h = max((cell.timestamp_ms - features["last_ts_ms"]) / 3600000.0, _EPS)
        step_gate = float(max_speed) * dt_h
        pred_gate = cfg.get("prediction_gate_km")
        pred_gate = float(pred_gate) if pred_gate else step_gate
        return step_gate, pred_gate

    def _record(self, frame_index, rows):
        if self._diagnostics:
            self.diag.append({"frame": frame_index, "matches": rows})

    # -----------------------------------------------------------------------
    def update(self, cells, frame_index):
        """Frame corrente (celle rilevate) -> aggiorna le track attive.

        Coasting (grace): config `max_coast_frames`, default 0 = OFF. Con il
        default una track attiva muore al primo frame senza celle assegnabili
        (comportamento Fase 1 invariato). Con un valore N>0 una track attiva
        sopravvive fino a N frame consecutivi privi di celle (riga diagnostica
        assignment_status='coasting'); se una cella torna assegnabile la track
        la riprende e il contatore si azzera.

        GATE DI CONTINUITA' (2026-11-10): un'associazione e' RIFIUTATA se la
        velocita' implicita (distanza dall'ultima osservazione / dt) supera
        `SPEED_RANGE_KMH[1]` (~110 km/h), la stessa fonte unica di forecast.py
        usata dal clamp a valle. Prima un ID-switch o un centroid jump veniva
        assorbito nella stessa track e la path-speed impossibile era solo
        azzerata a valle (misurato: `velocity_kmh: null` nel 61% dei track).
        Ora il salto apre una NUOVA track: l'effetto atteso e' meno track
        spurie fuse insieme e piu' track corte ma fisicamente coerenti. Il
        gate e' indipendente da `max_storm_speed_kmh` (che resta configurabile,
        None=off) e si applica solo con dt > 0: a dt nullo la velocita' non e'
        definita e vale il comportamento storico."""
        self._last_frame_index = int(frame_index)
        active = [t for t in self.tracks if t.status == models.STATUS_ACTIVE]
        seed_map = self._seed_matches(cells) if not active else {}
        if not active or not cells:
            rows = []
            for ci, cell in enumerate(cells):
                self.tracks.append(self._new_track(cell, frame_index,
                                                   seed=seed_map.get(ci)))
                rows.append({"track_id": self._next_track_id - 1,
                             "prev_cell_id": None, "current_cell_id": cell.cell_id,
                             "assignment_status": "new_track"})
            max_coast = int(self.cfg.get("max_coast_frames", 0) or 0)
            for t in active:
                if not cells and max_coast > 0 and \
                        getattr(t, "_coast_frames", 0) < max_coast:
                    t._coast_frames = getattr(t, "_coast_frames", 0) + 1
                    rows.append({"track_id": t.track_id,
                                 "prev_cell_id": t.points[-1].cell_id,
                                 "current_cell_id": None,
                                 "assignment_status": "coasting"})
                    continue
                t.status = models.STATUS_DEAD
                t.death_ms = t.points[-1].timestamp_ms
                t.events.append(models.EVENT_DEATH)
                rows.append({"track_id": t.track_id, "prev_cell_id":
                             t.points[-1].cell_id, "current_cell_id": None,
                             "assignment_status": "terminated"})
            self._record(frame_index, rows)
            return

        _, max_cont_kmh = _speed_range_kmh()   # fonte unica: forecast.py
        features_list = [self._features(t, self.cfg) for t in active]
        cost = np.full((len(active), len(cells)), np.inf, dtype=float)
        cand_meta = {}   # (i,j) -> {distance_km, area_ratio, int_diff, iou, pred_err}
        for i, (t, feats) in enumerate(zip(active, features_list)):
            for j, cell in enumerate(cells):
                sg, pg = self._gate_limits(feats, cell, self.cfg)
                d = haversine_km(feats["last_lonlat"], cell.centroid_lonlat)
                dt_h = (cell.timestamp_ms - feats["last_ts_ms"]) / 3600000.0
                if dt_h > 0.0 and d / dt_h > max_cont_kmh:
                    # GATE DI CONTINUITA': salto > ~110 km/h -> nessuna
                    # associazione (INF). La detection aprira' una nuova track.
                    continue
                if sg is not None and d > sg:
                    continue  # fuori gate -> INF (rejected)
                pred_target = None
                if feats.get("predicted"):
                    pred_target = self._target_prediction(feats, cell.timestamp_ms)
                    if (pg is not None and pred_target is not None
                            and haversine_km(pred_target, cell.centroid_lonlat) > pg):
                        continue
                feats_here = dict(feats)
                feats_here["predicted_lonlat"] = pred_target
                if self._improved:
                    c = match_cost_improved(feats_here, cell, self.cfg)
                else:
                    c = cell_cost(_pt_as_cell(t.points[-1]), cell, self.cfg)
                if c <= float(self.cfg["max_assignment_cost"]):
                    cost[i, j] = c
                cand_meta[(i, j)] = {
                    "distance_km": round(d, 3),
                    "area_ratio": round(cell.area_km2 / max(feats["area_avg_km2"], _EPS), 3),
                    "intensity_difference": round(abs(feats["dbz_avg"] - cell.max_dbz), 2),
                    "iou": round(_circle_iou(feats["last_lonlat"], feats["area_avg_km2"],
                                             cell.centroid_lonlat, cell.area_km2), 4),
                    "prediction_error_km": round(
                        haversine_km(pred_target, cell.centroid_lonlat), 3)
                        if pred_target is not None else None,
                    "gates": (sg, pg),
                }

        max_cost = float(self.cfg["max_assignment_cost"])
        rows = []
        # candidati "vicini" (<=25 km, co-locazione fisica) per merge/split
        # diagnostico: vicini = raggiungibili (costo finito) e distanza <=25 km.
        near_row = np.zeros(len(active), dtype=int)
        near_col = np.zeros(len(cells), dtype=int)
        for (i, j), meta in cand_meta.items():
            if meta["distance_km"] <= 25.0 and np.isfinite(cost[i, j]):
                near_row[i] += 1
                near_col[j] += 1
        amb_rows = set(np.where(near_row > 1)[0].tolist())  # split candidato
        amb_cols = set(np.where(near_col > 1)[0].tolist())  # merge candidato

        assignment, reversed_assign = {}, {}
        if cost.size:
            fin = np.isfinite(cost)
            if fin.any():
                big = float(max_cost) * max(cost.shape) + 1.0
                work = np.where(fin, cost, big)
                row_idx, col_idx = linear_sum_assignment(work)
                for ti, ci in zip(row_idx, col_idx):
                    if np.isfinite(cost[ti, ci]) and cost[ti, ci] <= max_cost:
                        assignment[ti] = ci
                        reversed_assign[ci] = ti

        # Lineage merge/split (2026-11-11): piano calcolato PRIMA di creare le
        # track nuove, cosi' i figli possono ereditare parent_ids.
        lineage = lineage_plan(active, cells, assignment, reversed_assign)

        # ambiguous candidato (merge/split) -> EVENT_AMBIGUOUS come in Fase 1
        for ti, ci in assignment.items():
            cell = cells[ci]
            track = active[ti]
            track.points.append(models.TrackPoint(track.track_id, frame_index, cell))
            track._coast_frames = 0
            if ti in amb_rows or ci in amb_cols:
                track.events.append(models.EVENT_AMBIGUOUS)

        # righe diagnostica per ogni candidato valutato
        for ti, t in enumerate(active):
            for ci, cell in enumerate(cells):
                meta = cand_meta.get((ti, ci))
                if meta is None:
                    if self._diagnostics:
                        rows.append({"track_id": t.track_id,
                                     "prev_cell_id": t.points[-1].cell_id,
                                     "current_cell_id": cell.cell_id,
                                     "assignment_status": "rejected_cost",
                                     "total_cost": None,
                                     "distance_km": round(haversine_km(
                                         t.points[-1].lonlat, cell.centroid_lonlat), 3)})
                    continue
                is_matched = ti in assignment and assignment[ti] == ci
                status = "matched" if is_matched else "rejected_cost"
                if is_matched and ci in amb_cols:
                    status = "ambiguous_merge_candidate"
                if is_matched and ti in amb_rows:
                    status = "ambiguous_split_candidate"
                if self._diagnostics:
                    rows.append({"track_id": t.track_id,
                                 "prev_cell_id": t.points[-1].cell_id,
                                 "current_cell_id": cell.cell_id,
                                 "assignment_status": status,
                                 "total_cost": round(cost[ti, ci], 4)
                                 if np.isfinite(cost[ti, ci]) else None,
                                 **meta})

        new_tracks_by_col = {}
        for j, cell in enumerate(cells):
            if j not in reversed_assign:
                track = self._new_track(cell, frame_index)
                self.tracks.append(track)
                new_tracks_by_col[j] = track
                rows.append({"track_id": track.track_id,
                             "prev_cell_id": None, "current_cell_id": cell.cell_id,
                             "assignment_status": "new_track"})
        apply_lineage(active, new_tracks_by_col, lineage)

        for ti, track in enumerate(active):
            if ti not in assignment:
                track.status = models.STATUS_DEAD
                track.death_ms = track.points[-1].timestamp_ms
                track.events.append(models.EVENT_DEATH)
                rows.append({"track_id": track.track_id,
                             "prev_cell_id": track.points[-1].cell_id,
                             "current_cell_id": None,
                             "assignment_status": "terminated"})

        self._record(frame_index, rows)

    # -----------------------------------------------------------------------
    def finalize(self, cfg_scoring=None):
        """Calcola motion per ogni track (timestamp reali) + Organization Score
        con la configurazione scoring INIETTATA (P0 fix) + score_confidence.

        MOTO PUBBLICATO (2026-11-10). `velocity_kmh` storica era distanza
        TOTALE del percorso / durata (path-speed): su un ID-switch o un
        centroid jump vale centinaia o migliaia di km/h (misurato: l'11,0% dei
        passi oltre 110 km/h, massimo 299,7; il clamp a valle produceva `null`
        nel 61% dei track). Il valore pubblicato viene ora dal FIT PESATO di
        `forecast.fit_motion` (la stessa regressione usata dal forecast), SOLO
        quando `fit_used` e `resid_std_km < 6 km`: in quel caso
        `motion_source="weighted_fit"`, qualita' `reliable`, direzione
        `direction_toward_deg` del fit. Altrimenti nulla viene inventato:
        `velocity_kmh`/`direction_*` sono `null`, `motion_quality="unreliable"`,
        `motion_source="fallback"` e `motion_null_reason` vale
        `fit_residual_too_high` (fit calcolato ma non rappresentativo) o
        `fit_not_used` (< 4 punti / velocita' fuori range). Con meno di 2 punti
        il record e' `motion_source="none"` / `insufficient_points`.
        `distance_km` resta la lunghezza MISURATA del percorso (audit), non
        una velocita'.
        """
        from . import scoring
        from . import forecast as _forecast
        full_frames = int(self.cfg.get("full_confidence_frames", 5))
        scfg = cfg_scoring or self.cfg_scoring or scoring._fallback_scoring()
        for track in self.tracks:
            # Seed con UNA sola osservazione nel ciclo corrente: l'ancora di
            # archivio non basta a pubblicare una track (sarebbe una nascita
            # di 1 punto). Si toglie il punto frame_index=-1, cosi' il filtro
            # n_frames>=2 dei derivati la esclude come prima del seed. Il flag
            # continued_from_history resta per audit in memoria.
            pts_now = [p for p in track.points
                       if getattr(p, "frame_index", 0) >= 0]
            if getattr(track, "continued_from_history", False) and \
                    len(pts_now) < 2:
                track.points = pts_now
                if pts_now:
                    track.birth_ms = pts_now[0].timestamp_ms
            pts = track.points
            if len(pts) < 2:
                track.tracking_confidence = "insufficient"
                track.score_confidence = "low"
                track.motion = {
                    "distance_km": 0.0,
                    "velocity_kmh": None,
                    "direction_toward_deg": None,
                    "direction_from_deg": None,
                    "motion_quality": "unreliable",
                    "motion_null_reason": "insufficient_points",
                    "motion_source": "none",
                    "duration_min": 0.0,
                }
                continue
            n = len(pts)
            track.tracking_confidence = ("full" if n >= full_frames else "low")
            track.score_confidence = (
                "high" if n >= full_frames and max(p.max_dbz for p in pts) >= 35.0
                else ("medium" if n >= 3 else "low"))
            t0 = pts[0].timestamp_ms
            t1 = pts[-1].timestamp_ms
            duration_min = (t1 - t0) / 60000.0
            dist = 0.0
            for a, b in zip(pts, pts[1:]):
                dist += haversine_km(a.lonlat, b.lonlat)
            if duration_min <= 0:
                # Nessuna durata -> nessuna velocita' e nessuna direzione
                # (la divisione per zero non si fa). La distanza geometrica
                # resta MISURATA: non si pubblica uno zero inventato.
                track.motion = {
                    "distance_km": round(dist, 2),
                    "velocity_kmh": None,
                    "direction_toward_deg": None,
                    "direction_from_deg": None,
                    "motion_quality": "unreliable",
                    "motion_null_reason": "non_positive_duration",
                    "motion_source": "none",
                    "duration_min": round(duration_min, 1),
                }
                continue
            fit = _forecast.fit_motion(pts)
            use_fit = bool(fit["fit_used"]) and \
                float(fit["resid_std_km"]) < _MOTION_MAX_RESID_KM
            if use_fit:
                velocity = float(fit["velocity_kmh"])
                toward = float(fit["direction_toward_deg"])
                from_deg = (toward + 180.0) % 360.0
                null_reason = None
                motion_quality = "reliable"
                motion_source = "weighted_fit"
            else:
                velocity = None
                toward = None
                from_deg = None
                null_reason = ("fit_residual_too_high" if fit["fit_used"]
                               else "fit_not_used")
                motion_quality = "unreliable"
                motion_source = "fallback"
            growth = 0.0
            if pts[0].area_km2 > 0:
                growth = (pts[-1].area_km2 - pts[0].area_km2) / pts[0].area_km2 * 100.0
            idelta = pts[-1].max_dbz - pts[0].max_dbz
            track.motion = {
                "distance_km": round(dist, 2),
                "velocity_kmh": round(velocity, 1) if velocity is not None else None,
                "direction_toward_deg": round(toward, 1) if toward is not None else None,
                "direction_from_deg": round(from_deg, 1) if from_deg is not None else None,
                "motion_quality": motion_quality,
                "motion_null_reason": null_reason,
                "motion_source": motion_source,
                "duration_min": round(duration_min, 1),
                "area_growth_pct": round(growth, 1),
                "intensity_delta_dbz": round(idelta, 2),
                "_avg_solidity": round(sum(p.solidity for p in pts) / n, 4),
                "_avg_compactness": round(sum(p.compactness for p in pts) / n, 4),
            }
            score, label, probs, _comps = scoring.organization_score(track, scfg)
            track.motion["organization_score"] = score
            track.motion["classification"] = label
            track.motion["class_probs"] = probs
        return self.tracks

    def complete_cycles(self):
        """Chiude le track attive NON osservate sull'ultimo frame processato.

        D2 (1.2.4.17): prima TUTTE le track aperte diventavano "dead", anche
        quelle con un punto sull'ultimo frame: ogni record pubblicato usciva
        con status="dead" pur essendo `on_latest_frame: true`. Ora la chiusura
        dipende dal frame: una track vista sull'ultimo frame resta "active"."""
        last = getattr(self, "_last_frame_index", None)
        for track in self.tracks:
            if track.status != models.STATUS_ACTIVE:
                continue
            if last is not None and track.points and \
                    track.points[-1].frame_index == last:
                continue                      # osservata sull'ultimo frame
            track.status = models.STATUS_DEAD
            track.death_ms = track.points[-1].timestamp_ms
        return self.tracks


def _pt_as_cell(point):
    """Adatta un TrackPoint alla cost function legacy (senza copie costose)."""
    return point