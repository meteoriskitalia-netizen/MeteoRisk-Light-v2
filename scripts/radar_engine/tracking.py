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
# 2026-11-24 — SALTO DI CENTROIDE SU DISCHI SOVRAPPOSTI.
# Il gate di continuita' ~110 km/h misura il CENTROIDE. Su una macchia larga
# (es. 1000 km2 -> raggio equivalente ~17,8 km) un salto di 30 km in 5 minuti
# implica 360 km/h ma i due dischi si sovrappongono molto: non e' un ID switch,
# e' lo stesso corpo che cambia forma. Quando i dischi si sovrappongono almeno
# per `_LINEAGE_IOU` l'associazione viene TENUTA (l'ID non cambia) ma il passo
# NON entra nel fit del moto: il vettore pubblicato resta nullo con motivo
# esplicito `centroid_jump_overlapped`. Sotto la soglia il gate resta invariato.
MOTION_NULL_CENTROID_JUMP_OVERLAPPED = "centroid_jump_overlapped"
# Re-ID DENTRO il run (2026-11-24): stessa regola del seed fra run
# (`_seed_matches`), applicata a una traccia MORTA da non piu' di 15 minuti e
# una NATTA nel frame corrente. I due valori sono gli stessi default di
# `tracking_metrics` (sorgente unica: la regola di cucitura e la metrica
# pubblicata non possono divergere).
_REID_RADIUS_KM = 20.0
_REID_WINDOW_MIN = 15.0
# SCELTA INGEGNERISTICA (misurata sul replay 2026-11-24): la sola distanza
# temporale unitebbe una cella di 87 km2 / 45,5 dBZ a un corpo di 2081 km2 /
# 61 dBZ a 15 km (rapporto d'area 24x in 10 minuti): non e' la stessa cella, e'
# una cella nuova (o una fusione). La continuita' richiede area dello stesso
# ordine di grandezza (rapporto <= 8) e intensita' compatibile (|delta| <= 15
# dBZ). Le soglie NON sono tarate su etichette: servono solo a rifiutare i
# legami fisicamente impossibili.
_REID_MAX_AREA_RATIO = 8.0
_REID_MAX_DBZ_JUMP = 15.0
# Evento di audit della cucitura (la prova che il legame e' esplicito).
EVENT_REID_CONTINUATION = "lineage_continuation_in_run"
# 1.3.0.38 — semantica del contatore `continuity_rejections`: conta le
# ASSOCIAZIONI FINALI (uno-a-uno, post-Hungarian) rifiutate dal gate di
# continuita' ~110 km/h, NON le coppie (i,j) della matrice di costo. La versione
# precedente (v1) contava ogni coppia respinta: 17115 sul run 2026-11-20.
# La chiave `continuity_rejections` resta per retrocompatibilita'; questo token
# viene pubblicato come `continuity_rejections_basis` per dichiarare la base.
CONTINUITY_REJECTIONS_BASIS = "final_associations_after_hungarian_v2"

# ---------------------------------------------------------------------------
# COASTING DI 1 FRAME (2026-11-21) — posizione STIMATA, mai un'osservazione
# ---------------------------------------------------------------------------
# Un punto coasted propaga l'ULTIMO MOTO OSSERVATO di un passo di frame. Non
# e' una misura: non entra nel fit del moto, non allunga `duration_observed_min`,
# non conta come frame osservato. Soprattutto NON viene creato quando il frame
# e' un buco di dato: in quel caso la traccia muore con un null_reason esplicito.
COAST_POSITION_SOURCE = "coasted"
COAST_NULL_NO_VALID_FRAME = "no_valid_frame"
COAST_NULL_INSUFFICIENT_COVERAGE = "insufficient_coverage"
COAST_NULL_STALE_DATA = "stale_data"
COAST_NULL_NO_TIMESTAMP = "frame_timestamp_missing"
COAST_NULL_INSUFFICIENT_OBS = "insufficient_observed_points"
COAST_NULL_NON_POSITIVE = "non_positive_coast_interval"


def coast_frame_context(frame_context):
    """Verdetto (allowed, null_reason) per il coasting su questo frame.

    Un contesto assente (libreria/test storici) equivale a frame valido, quindi
    il comportamento di chi non passa il contesto resta invariato. Qualsiasi
    segnale esplicito di buco di dato VIETA il coasting e restituisce il motivo.
    """
    ctx = frame_context or {}
    reason = ctx.get("null_reason")
    if reason:
        return False, str(reason)
    if ctx.get("valid") is False:
        return False, COAST_NULL_NO_VALID_FRAME
    if ctx.get("stale"):
        return False, COAST_NULL_STALE_DATA
    if ctx.get("coverage_ok") is False:
        return False, COAST_NULL_INSUFFICIENT_COVERAGE
    cov, min_cov = ctx.get("coverage_fraction"), ctx.get("min_coverage_fraction")
    if cov is not None and min_cov is not None:
        try:
            if float(cov) < float(min_cov):
                return False, COAST_NULL_INSUFFICIENT_COVERAGE
        except (TypeError, ValueError):
            pass
    return True, None


def coasting_counters():
    """Struttura dei contatori per-run (solo interi, nessun dato raw)."""
    return {"coasted_points": 0, "coasted_tracks_recovered": 0,
            "null_reasons": {}}


def _coasted_point(track, frame_index, frame_ts_ms, point_cls, id_attr):
    """Costruisce il punto coasted oppure (None, motivo).

    La posizione e' `ultima_osservazione + ultimo_moto_osservato * dt`, con dt
    calcolato dal timestamp REALE del frame. Se il moto osservato e' nullo la
    posizione resta quella osservata (nessuna velocita' inventata). Tutti gli
    attributi non posizionali sono COPIA dell'ultima osservazione: il punto
    non inventa area/intensita' nuove.
    """
    pts = [p for p in track.points
           if getattr(p, "observed", True) and getattr(p, "frame_index", -1) >= 0]
    # Dopo una cucitura di re-ID (2026-11-24) il moto da propagare e' solo
    # quello OSSERVATO DOPO il buco: le due osservazioni a cavallo di 15 minuti
    # non sono il moto attuale. Con meno di 2 osservazioni post-cucitura il
    # coasting e' VIETATO (motivo esplicito), come per una track appena nata.
    reset = getattr(track, "reid_reset_frame", None)
    if reset is not None:
        pts = [p for p in pts if p.frame_index >= int(reset)]
    if len(pts) < 2:
        return None, COAST_NULL_INSUFFICIENT_OBS
    if frame_ts_ms is None:
        return None, COAST_NULL_NO_TIMESTAMP
    a, b = pts[-2], pts[-1]
    dt_h = (float(frame_ts_ms) - float(b.timestamp_ms)) / 3600000.0
    dt_obs_h = (float(b.timestamp_ms) - float(a.timestamp_ms)) / 3600000.0
    if dt_h <= 0 or dt_obs_h <= 0:
        return None, COAST_NULL_NON_POSITIVE
    d = haversine_km(a.lonlat, b.lonlat)
    lonlat = (dest_from(b.lonlat, bearing_deg(a.lonlat, b.lonlat),
                        (d / dt_obs_h) * dt_h) if d > 1e-9
              else tuple(b.lonlat))

    class _CoastedSource:
        pass

    src = _CoastedSource()
    src.timestamp_ms = int(frame_ts_ms)
    src.timestamp_iso = _dt.datetime.fromtimestamp(
        int(frame_ts_ms) / 1000.0, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    setattr(src, id_attr, None)          # nessuna cella/oggetto osservato
    src.centroid_lonlat = lonlat
    src.area_km2 = b.area_km2
    src.max_dbz = b.max_dbz
    src.mean_dbz = b.mean_dbz
    src.solidity = getattr(b, "solidity", 1.0)
    src.compactness = getattr(b, "compactness", 1.0)
    src.eccentricity = getattr(b, "eccentricity", None)
    src.cell_count = getattr(b, "cell_count", 1)
    src.cell_density = getattr(b, "cell_count", 1)
    src.tracking_ambiguity = getattr(b, "ambiguity", None)
    pt = point_cls(track.track_id, frame_index, src)
    pt.position_source = COAST_POSITION_SOURCE
    pt.observed = False
    for attr in ("iou_to_prev", "speed_to_prev_kmh", "bearing_to_prev_deg"):
        if hasattr(pt, attr):
            setattr(pt, attr, None)
    return pt, None


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


def is_latest_frame(frame_index, cells_by_frame):
    """REGOLA UNICA: `frame_index` e' l'ultimo frame della timeline.

    L'indice dell'ultimo frame e' SEMPRE `len(cells_by_frame) - 1`, anche se
    la timeline contiene frame VUOTI intercalati: contare i soli frame non
    vuoti faceva divergere forecast.py da convective_structure.py. Usata da
    entrambi i call site; nessuna altra regola di "ultimo frame" resta nel
    motore. Frame_index None o timeline vuota -> False.
    """
    frames = cells_by_frame or []
    if not frames or frame_index is None:
        return False
    return int(frame_index) == len(frames) - 1


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
# Evoluzione del sistema: origine, crescita, fine (2026-11-24)
# ---------------------------------------------------------------------------
def compute_birth_kind(track):
    """(birth_kind, birth_null_reason) di una track, da evidenza in-memory.

    Ordine di precedenza (dichiarato, non inferito):
      1. seed d'archivio  -> None / "continuation_from_history"
      2. nessun evento birth -> None / "not_born_in_this_run"
      3. merge_result se lineage=="merge" o evento lineage_merge
      4. split_child se lineage=="split" o evento lineage_split
      5. reid_continuation se EVENT_REID_CONTINUATION (physical_birth=False)
      6. after_reject se la colonna aveva un candidato ungated rifiutato
         dal gate ~110 km/h (evento registrato alla creazione)
      7. first_observation
    """
    events = list(getattr(track, "events", None) or [])
    if getattr(track, "continued_from_history", False):
        return None, "continuation_from_history"
    if models.EVENT_BIRTH not in events:
        return None, "not_born_in_this_run"
    lineage = getattr(track, "lineage", None)
    # `apply_lineage` registra l'evento lineage_* su ENTRAMBI i lati: il
    # genitore (child_ids) e il figlio (parent_ids). Solo il figlio ha
    # parent_ids, quindi l'evento da solo non basta a classificare la nascita:
    # senza questa guardia un genitore che si divide veniva contato come
    # `split_child` (misurato: 4 split_child contro 2 relazioni di split).
    parent_ids = list(getattr(track, "parent_ids", None) or [])
    is_child = bool(parent_ids)
    if lineage == "merge" or (is_child and "lineage_merge" in events):
        return models.BIRTH_KIND_MERGE_RESULT, None
    if lineage == "split" or (is_child and "lineage_split" in events):
        return models.BIRTH_KIND_SPLIT_CHILD, None
    if EVENT_REID_CONTINUATION in events:
        return models.BIRTH_KIND_REID_CONTINUATION, None
    if (models.EVENT_BIRTH_AFTER_GATE_REJECT in events
            or getattr(track, "birth_after_gate_reject", False)):
        return models.BIRTH_KIND_AFTER_REJECT, None
    return models.BIRTH_KIND_FIRST_OBSERVATION, None


def _observed_points(track):
    """Punti OSSERVATI (esclusi i coasted), in ordine temporale."""
    pts = [p for p in (getattr(track, "points", None) or [])
           if getattr(p, "observed", True)
           and getattr(p, "position_source", None) != COAST_POSITION_SOURCE]
    return sorted(pts, key=lambda p: (int(getattr(p, "timestamp_ms", 0)),
                                      int(getattr(p, "frame_index", -1))))


def _growth_null(reason, window_points=0, window_min=None):
    return {
        "window_points": int(window_points),
        "window_min": window_min,
        "area_km2_per_min": None,
        "max_dbz_per_min": None,
        "mean_dbz_per_min": None,
        "area_delta_km2": None,
        "max_dbz_delta": None,
        "null_reason": reason,
        "vil_density": None,
        "vil_density_null_reason": models.GROWTH_VIL_NULL_REASON,
        "method": "linear_slope_last_3_observed_points",
        "source": "track.points (observed=True, position_source!=coasted)",
    }


def compute_growth(track):
    """Pendenza recente sugli ULTIMI 3 punti OSSERVATI (non cumulato).

    Non e' un tasso "dalla nascita": e' la pendenza della finestra mobile
    finale, cosi' una struttura che ha gia' raggiunto il picco non viene
    descritta dalla crescita totale. Con meno di 3 punti osservati i valori
    sono null con `insufficient_points`.
    """
    obs = _observed_points(track)
    window = obs[-models.GROWTH_WINDOW_POINTS:]
    n = len(window)
    if n < models.GROWTH_WINDOW_POINTS:
        window_min = None
        if n >= 2:
            delta = (window[-1].timestamp_ms - window[0].timestamp_ms) / 60000.0
            window_min = round(delta, 2) if delta > 0 else None
        return _growth_null(models.GROWTH_NULL_INSUFFICIENT, n, window_min)
    first, last = window[0], window[-1]
    window_min = (last.timestamp_ms - first.timestamp_ms) / 60000.0
    if window_min <= 0:
        return _growth_null(models.GROWTH_NULL_NON_POSITIVE_WINDOW, n, None)

    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _slope(attr):
        a, b = _num(getattr(first, attr, None)), _num(getattr(last, attr, None))
        if a is None or b is None:
            return None, None
        return (b - a) / window_min, (b - a)

    area_rate, area_delta = _slope("area_km2")
    max_rate, max_delta = _slope("max_dbz")
    mean_rate, _mean_delta = _slope("mean_dbz")
    return {
        "window_points": n,
        "window_min": round(window_min, 2),
        "area_km2_per_min": (round(area_rate, 3)
                             if area_rate is not None else None),
        "max_dbz_per_min": (round(max_rate, 3)
                            if max_rate is not None else None),
        "mean_dbz_per_min": (round(mean_rate, 3)
                             if mean_rate is not None else None),
        "area_delta_km2": (round(area_delta, 2)
                           if area_delta is not None else None),
        "max_dbz_delta": (round(max_delta, 2)
                          if max_delta is not None else None),
        "null_reason": None,
        "vil_density": None,
        "vil_density_null_reason": models.GROWTH_VIL_NULL_REASON,
        "method": "linear_slope_last_3_observed_points",
        "source": "track.points (observed=True, position_source!=coasted)",
    }


def finalize_lifecycle_fields(track):
    """Popola birth_kind/physical_birth/death_kind/growth di una track.

    Va chiamata in `finalize()`, cioe' DOPO `apply_lineage` e
    `_stitch_reids_in_run` (l'ultima ricucitura puo' cambiare l'esito) e DOPO
    `complete_cycles` (che chiude le track non osservate sull'ultimo frame).
    """
    kind, reason = compute_birth_kind(track)
    track.birth_kind = kind
    track.birth_null_reason = reason
    track.physical_birth = (None if kind is None
                            else kind != models.BIRTH_KIND_REID_CONTINUATION)
    if getattr(track, "status", None) == models.STATUS_DEAD:
        if getattr(track, "death_kind", None) is None:
            track.death_kind = models.DEATH_KIND_LOST
            track.death_null_reason = "death_cause_unavailable"
        else:
            track.death_null_reason = None
    else:
        track.death_kind = None
        track.death_null_reason = "track_still_active"
    track.growth = compute_growth(track)
    return track



def tracking_metrics(tracks, track_type="cell", max_speed_kmh=None,
                     reid_window_min=_REID_WINDOW_MIN,
                     reid_radius_km=_REID_RADIUS_KM,
                     lineage_iou=_LINEAGE_IOU):
    """Metriche per-layer, calcolate sui record PUBBLICATI (n_frames >= 2).

    Definizioni (dichiarate perche' non sono standard):
    - `pct_reliable`: % di track con `motion_quality == "reliable"`.
    - `median_duration_min`: mediana della durata delle track ATTIVE.
    - `id_switch_rate`: (salti > max_speed + re-ID inspiegati) / n_track.
      Un re-ID e' una track che nasce entro `reid_window_min` e
      `reid_radius_km` dalla morte di un'altra track senza un legame di
      lineage. I salti sono segmenti consecutivi OSSERVATI con velocita'
      implicita sopra `max_speed_kmh` (default: la soglia unica di forecast.py),
      ESCLUSI i segmenti dichiarati `centroid_jump_overlapped`: li' l'ID non e'
      cambiato e i due dischi si sovrappongono almeno per `lineage_iou`, quindi
      non e' un ID switch ma un cambio di forma del corpo. Quei segmenti sono
      contati a parte in `centroid_jump_segments` (nessun numero nascosto).
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
    jump_overlap_segments = 0
    for t in published:
        # Un punto coasted e' una stima: NON puo' generare un falso ID switch.
        obs = [p for p in t.points if getattr(p, "observed", True)]
        for a, b in zip(obs, obs[1:]):
            dt_h = (b.timestamp_ms - a.timestamp_ms) / 3600000.0
            if dt_h <= 0:
                continue
            if haversine_km(a.lonlat, b.lonlat) / dt_h <= max_speed:
                continue
            if getattr(b, "centroid_jump", False):
                # Stesso ID, dischi sovrapposti: cambio di forma, non switch.
                jump_overlap_segments += 1
                continue
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
        # Segmenti sopra soglia TENUTI per sovrapposizione (ID invariato):
        # contati qui, non nei salti. 0 finche' nessuna associazione e' tenuta.
        "centroid_jump_segments": jump_overlap_segments,
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


def build_tracking_metrics(cell_tracks, storm_tracks,
                           cell_continuity_rejections=0,
                           storm_continuity_rejections=0,
                            cell_coasting=None, storm_coasting=None,
                            cell_kept_by_overlap=0, storm_kept_by_overlap=0,
                            cell_reids_stitched=0, storm_reids_stitched=0,
                            **kwargs):
    """Blocco `tracking_metrics` per latest.json: per-layer + aggregato.

    I campi piatti (pct_reliable, median_duration_min, id_switch_rate,
    lineage_split, lineage_merge) sono l'AGGREGATO cella+storm object: nessuna
    selezione a posteriori del layer che migliora. Il dettaglio per layer e'
    in `cell` e `storm_object`.

    `continuity_rejections` conta le ASSOCIAZIONI FINALI rifiutate dal gate
    110 km/h per run (uno per legame uno-a-uno che l'Hungarian avrebbe scelto
    senza gate e che non sopravvive al gate): NON le coppie (i,j) della matrice
    di costo. Pubblicato anche `continuity_rejections_basis` che dichiara la
    semantica. Solo il conteggio: mai i dettagli dei singoli rigetti.

    MIGRAZIONE 1.3.0.38: la chiave `continuity_rejections` e' invariata ma il
    suo VALORE cambia base (v1 = coppie, v2 = associazioni finali). I payload
    gia' scritti con la v1 vanno rigenerati con il prossimo run: non confrontare
    il valore tra versioni diverse senza leggere `continuity_rejections_basis`.
    """
    cell = tracking_metrics(cell_tracks, "cell", **kwargs)
    storm = tracking_metrics(storm_tracks, "storm_object", **kwargs)
    combined = tracking_metrics(list(cell_tracks or []) + list(storm_tracks or []),
                                "combined", **kwargs)
    n_cell_rej = int(cell_continuity_rejections or 0)
    n_storm_rej = int(storm_continuity_rejections or 0)
    # 2026-11-24 — contatori della riparazione dei salti: associazioni TENUTE
    # solo grazie all'IoU dei dischi (`kept_by_overlap`) e re-ID cuciti dentro
    # il run (`reids_stitched`). Solo interi per run: nessun dettaglio.
    for block, kept, stitched in (
            (cell, cell_kept_by_overlap, cell_reids_stitched),
            (storm, storm_kept_by_overlap, storm_reids_stitched)):
        block["kept_by_overlap"] = int(kept or 0)
        block["reids_stitched"] = int(stitched or 0)
    combined["kept_by_overlap"] = (int(cell_kept_by_overlap or 0)
                                   + int(storm_kept_by_overlap or 0))
    combined["reids_stitched"] = (int(cell_reids_stitched or 0)
                                  + int(storm_reids_stitched or 0))
    for block, n in ((cell, n_cell_rej), (storm, n_storm_rej),
                     (combined, n_cell_rej + n_storm_rej)):
        block["continuity_rejections"] = n
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
    # COASTING (2026-11-21): contatori per-run, per layer e aggregati. Solo
    # interi + un dict motivo->conteggio: nessun dato raw, nessuna posizione.
    def _merge_coast(dst, src):
        src = src or {}
        for key in ("coasted_points", "coasted_tracks_recovered"):
            dst[key] = int(src.get(key, 0) or 0)
        reasons = {}
        for reason, count in (src.get("null_reasons") or {}).items():
            reasons[str(reason)] = int(count)
        dst["coasting_null_reasons"] = reasons

    nul = {}
    for src in (cell_coasting, storm_coasting):
        for reason, count in ((src or {}).get("null_reasons") or {}).items():
            nul[str(reason)] = nul.get(str(reason), 0) + int(count)
    combined_coast = {
        "coasted_points": (int((cell_coasting or {}).get("coasted_points", 0) or 0)
                           + int((storm_coasting or {}).get("coasted_points", 0) or 0)),
        "coasted_tracks_recovered": (
            int((cell_coasting or {}).get("coasted_tracks_recovered", 0) or 0)
            + int((storm_coasting or {}).get("coasted_tracks_recovered", 0) or 0)),
        "null_reasons": nul,
    }
    for block, src in ((cell, cell_coasting), (storm, storm_coasting),
                       (combined, combined_coast)):
        _merge_coast(block, src)
    out = {"primary_layer": "combined"}
    # 1.3.0.38: dichiara la base del contatore (retrocompatibile: la chiave
    # `continuity_rejections` resta, cambia solo la semantica del valore).
    out["continuity_rejections_basis"] = CONTINUITY_REJECTIONS_BASIS
    for key in ("pct_reliable", "median_duration_min",
                "median_duration_current_min", "id_switch_rate",
                "lineage_split", "lineage_merge", "lineage_continuation",
                "lineage_links", "id_switch_jumps", "id_switch_reids",
                "n_tracks", "n_active_tracks", "n_reliable",
                "n_frames_ge_3", "n_frames_current_ge_3",
                "continued_from_history", "continuity_rejections",
                "centroid_jump_segments", "kept_by_overlap", "reids_stitched",
                "coasted_points", "coasted_tracks_recovered",
                "coasting_null_reasons", "thresholds"):
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
                 seed_max_speed_kmh=None, coasting_cfg=None):
        self.cfg = cfg_tracking
        # Config COASTING dedicata: se assente vale il vecchio `max_coast_frames`
        # del layer (=0), quindi le firme storiche non cambiano comportamento.
        self.coasting_cfg = coasting_cfg
        self.coasting = coasting_counters()
        self.cfg_scoring = cfg_scoring
        self.tracks = []
        self._next_track_id = 1
        self.diag = []                      # diagnostica per-match (opzionale)
        # Contatore per RUN delle ASSOCIAZIONI FINALI rifiutate dal gate di
        # continuita' (110 km/h), NON delle coppie (i,j) della matrice di costo.
        # SEMPRE disponibile, anche con diagnostics=False: il dettaglio dei
        # singoli rigetti NON viene pubblicato (solo il conteggio).
        self.continuity_rejections = 0
        # 2026-11-24 — contatori per RUN della riparazione dei salti:
        # `kept_by_overlap` = associazioni che il gate 110 km/h avrebbe
        # rifiutato e che sono state TENUTE per sovrapposizione dei dischi;
        # `reids_stitched` = re-ID cuciti dentro il run (traccia morta da
        # <= 15 min + traccia nata ora). `reid_stitches` e' il dettaglio per
        # il replay, non entra nei file pubblicati.
        self.kept_by_overlap = 0
        self.reids_stitched = 0
        self.reid_stitches = []

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
        # Il punto COASTED e' una stima: non puo' diventare origine del gate
        # 110 km/h ne' della velocita' di predizione. Si misura sempre
        # dall'ultima OSSERVAZIONE (il gate resta invariato).
        pts = [p for p in track.points
               if getattr(p, "observed", True)
               and getattr(p, "frame_index", 0) >= 0]
        if not pts:
            pts = [p for p in track.points if getattr(p, "observed", True)] \
                or track.points
        last = pts[-1]
        # 2026-11-24 — dopo una cucitura di re-ID la memoria (area/intensita'
        # medie e moto di predizione) NON puo' usare le osservazioni di prima
        # del buco: a 15 minuti di distanza descrivono un altro stato del corpo
        # e trascinavano l'associazione successiva (misurato in ablazione:
        # track 27 -> 32). Si riparte dal punto cucito.
        reset = getattr(track, "reid_reset_frame", None)
        mem_src = pts
        if reset is not None:
            mem_src = [p for p in pts if p.frame_index >= int(reset)] or pts
        mem = int(cfg.get("memory_frames", 3))
        tail = mem_src[-mem:]
        area_avg = sum(p.area_km2 for p in tail) / len(tail)
        dbz_avg = sum(p.max_dbz for p in tail) / len(tail)
        predicted, speed_kmh = None, None
        # 2026-11-24: un passo tenuto per sovrapposizione (`centroid_jump`) non
        # e' un'osservazione di MOTO (il centroide e' saltato): la velocita' di
        # predizione si misura fra gli ultimi due punti NON saltati.
        motion_pts = [p for p in mem_src
                      if not getattr(p, "centroid_jump", False)]
        if len(motion_pts) >= 2:
            a, b = motion_pts[-2], motion_pts[-1]
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

    def _new_track(self, cell, frame_index, seed=None, after_reject=False):
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
            # 2026-11-24 — la colonna aveva un candidato che il gate 110 km/h
            # ha rifiutato: la nascita e' un re-ingresso, non un primo eco.
            if after_reject:
                track.birth_after_gate_reject = True
                track.events.append(models.EVENT_BIRTH_AFTER_GATE_REJECT)
        return track

    def _mark_death(self, track, kind):
        """Chiude una track registrando il MOTIVO al momento della morte."""
        track.status = models.STATUS_DEAD
        track.death_ms = track.points[-1].timestamp_ms
        if models.EVENT_DEATH not in track.events:
            track.events.append(models.EVENT_DEATH)
        track.death_kind = kind
        track.death_null_reason = None

    def _merged_into(self, track):
        """True se la track e' stata assorbita da un figlio con lineage merge.

        Non basta `child_ids` (anche uno split ne produce) ne' l'evento
        `lineage_merge` (anche il SURVIVOR di un merge lo porta, ma lui ha
        assorbito, non e' stato assorbito). Serve un figlio con
        `lineage == "merge"` che la elenca nei propri `parent_ids`.
        """
        ids = set(getattr(track, "child_ids", None) or [])
        if not ids:
            return False
        for other in self.tracks:
            if other is track:
                continue
            if getattr(other, "lineage", None) != "merge":
                continue
            if track.track_id in (getattr(other, "parent_ids", None) or []):
                return True
        return False

    def _death_kind_for(self, track, cells_present, gated_row, max_coast):
        """Motivo della morte, deciso con l'evidenza disponibile in quel punto.

        - merged_into: la track e' assorbita da un'altra (merge child reale)
        - coasting_expired: il grace di coasting e' esaurito
        - lost: nessuna osservazione utile (frame vuoto o gate 110 km/h)
        - dissipated: celle presenti ma nessuna associazione: l'eco non c'e' piu'
        """
        if self._merged_into(track):
            return models.DEATH_KIND_MERGED_INTO
        if max_coast > 0 and getattr(track, "_coast_frames", 0) >= max_coast:
            return models.DEATH_KIND_COASTING_EXPIRED
        if not cells_present:
            return models.DEATH_KIND_LOST
        if gated_row:
            return models.DEATH_KIND_LOST
        return models.DEATH_KIND_DISSIPATED

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

    def _stitch_reids_in_run(self, born, rows=None):
        """Ricuce i re-ID dentro il run con le regole del seed fra run.

        Regole (le stesse di `_seed_matches`, valori dalla sorgente unica
        `_REID_RADIUS_KM` / `_REID_WINDOW_MIN` = default di
        `tracking_metrics`): una traccia MORTA da non piu' di 15 minuti (la
        finestra in cui la metrica pubblicata conta un re-ID), entro 20 km,
        velocita' implicita non superiore alla soglia unica di forecast.py
        (~110 km/h), accoppiamento greedy uno-a-uno per distanza. Il child e'
        una traccia NATTA nel frame corrente e ancora viva: una traccia viva
        non viene MAI unita a un'altra (il parent deve essere STATUS_DEAD).
        Nessuna cucitura se esiste gia' un legame di lineage (parent_ids /
        child_ids) o se il child e' un seed d'archivio.

        L'identita' che sopravvive e' quella del parent (track_id piu' vecchio):
        il child esce da `self.tracks`, il suo punto passa al parent con lo
        stesso track_id, `lineage = "continuation"` e un evento ESPLICITO
        (`EVENT_REID_CONTINUATION`). La morte resta negli eventi (audit).

        Perche' non basta "lo farebbe l'Hungarian": il gate 110 km/h si misura
        dall'ultima OSSERVAZIONE, mentre qui si misura dall'ultima posizione
        PUBBLICATA (che puo' essere un punto coasted). Sono due misure diverse
        sullo stesso intervallo, e la metrica dei re-ID usa la seconda.
        """
        born = [c for c in (born or [])
                if c.status == models.STATUS_ACTIVE
                and len(c.points) == 1
                and not getattr(c, "continued_from_history", False)
                and not getattr(c, "parent_ids", None)
                and not getattr(c, "child_ids", None)]
        if not born:
            return {}
        max_speed = float(_speed_range_kmh()[1])
        # La cucitura non puo' disfare un gate FISICO configurato: se
        # `max_storm_speed_kmh` e' attivo, vale anche qui (altrimenti un
        # rigetto di quel gate verrebbe ricucito subito dopo).
        gate = self.cfg.get("max_storm_speed_kmh") if self.cfg else None
        if gate:
            max_speed = min(max_speed, float(gate))
        candidates = []
        for parent in self.tracks:
            if parent.status != models.STATUS_DEAD or not parent.points:
                continue
            if getattr(parent, "child_ids", None):
                continue                 # legame di lineage gia' esistente
            observed = [p for p in parent.points if getattr(p, "observed", True)]
            if not observed:
                continue
            first_ts = observed[-1].timestamp_ms
            for child in born:
                pt = child.points[0]
                gap_min = (pt.timestamp_ms - first_ts) / 60000.0
                if gap_min <= 0 or gap_min > _REID_WINDOW_MIN:
                    continue
                dist = haversine_km(observed[-1].lonlat, pt.lonlat)
                if dist > _REID_RADIUS_KM:
                    continue
                if dist / (gap_min / 60.0) > max_speed:
                    continue
                prev = observed[-1]
                big = max(pt.area_km2, prev.area_km2)
                small = min(pt.area_km2, prev.area_km2)
                if big / max(small, _EPS) > _REID_MAX_AREA_RATIO:
                    continue             # corpo diverso (o fusione), non re-ID
                if abs(pt.max_dbz - prev.max_dbz) > _REID_MAX_DBZ_JUMP:
                    continue             # intensita' incompatibile
                candidates.append((dist, gap_min, parent, child))
        candidates.sort(key=lambda item: (item[0], item[1],
                                          item[2].track_id, item[3].track_id))
        used_parents, used_children, stitched = set(), set(), {}
        for dist, gap_min, parent, child in candidates:
            if parent.track_id in used_parents or \
                    child.track_id in used_children:
                continue
            if parent.status != models.STATUS_DEAD or not parent.points:
                continue
            pt = child.points[0]
            prev = parent.points[-1]
            prev_cell_id = prev.cell_id
            pt.track_id = parent.track_id
            parent.points.append(pt)
            parent.events.append(EVENT_REID_CONTINUATION)
            parent.lineage = "continuation"
            parent.status = models.STATUS_ACTIVE
            parent.death_ms = None
            # La track e' VIVA di nuovo: il motivo della morte precedente non
            # descrive piu' il suo stato (resta l'evento EVENT_DEATH in audit).
            parent.death_kind = None
            parent.death_null_reason = None
            parent._coast_frames = 0
            # La memoria di matching riparte dal punto cucito (v. _features).
            parent.reid_reset_frame = pt.frame_index
            self._note_coast_recovery(parent)
            if child in self.tracks:
                self.tracks.remove(child)
            # La track cucita prende il POSTO del child nella lista: cosi' la
            # matrice di costo del frame successivo ha le righe nello stesso
            # ordine che avrebbe avuto con il child e la cucitura e' un puro
            # cambio di identita' (misurato: senza questo, l'ordine diverso
            # cambiava i pareggi dell'Hungarian e frammentava, track 27 -> 32).
            if parent in self.tracks:
                self.tracks.remove(parent)
            self.tracks.append(parent)
            used_parents.add(parent.track_id)
            used_children.add(child.track_id)
            stitched[child.track_id] = parent.track_id
            self.reids_stitched += 1
            self.reid_stitches.append({
                "parent_id": parent.track_id, "child_id": child.track_id,
                "gap_min": round(gap_min, 2),
                "distance_km": round(dist, 3),
                "speed_kmh": round(dist / (gap_min / 60.0), 1),
                # Audit della continuita' (non pubblicato): la stessa cella non
                # cambia area/intensita' di ordini di grandezza in 15 minuti.
                "parent_area_km2": round(prev.area_km2, 1),
                "child_area_km2": round(pt.area_km2, 1),
                "parent_max_dbz": round(prev.max_dbz, 1),
                "child_max_dbz": round(pt.max_dbz, 1),
                "parent_was_dead": True, "child_was_newborn": True,
            })
            if rows is not None:
                rows.append({"track_id": parent.track_id,
                             "prev_cell_id": prev_cell_id,
                             "current_cell_id": pt.cell_id,
                             "assignment_status": "reid_continuation",
                             "distance_km": round(dist, 3),
                             "gap_min": round(gap_min, 2)})
        return stitched

    def _max_coast_frames(self):
        src = self.coasting_cfg or self.cfg or {}
        try:
            return max(0, int(src.get("max_coast_frames", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _note_coast_reason(self, track, reason):
        """Registra un coasting VIETATO: il buco di dato resta esplicito."""
        track.coasting_null_reason = reason
        counts = self.coasting["null_reasons"]
        counts[reason] = counts.get(reason, 0) + 1

    def _try_coast(self, track, frame_index, frame_ts_ms, frame_context,
                   max_coast, rows):
        """Aggiunge UN punto stimato se il frame e' valido. False = far morire."""
        if max_coast <= 0 or getattr(track, "_coast_frames", 0) >= max_coast:
            return False
        if frame_ts_ms is None and self.coasting_cfg is None:
            # RETROCOMPATIBILITA': le firme storiche (solo cfg_tracking, senza
            # timestamp/contesto di frame) mantengono il "grace" M1 originale:
            # nessun punto stimato viene creato, la track sopravvive N frame.
            track._coast_frames = getattr(track, "_coast_frames", 0) + 1
            rows.append({"track_id": track.track_id,
                         "prev_cell_id": track.points[-1].cell_id,
                         "current_cell_id": None,
                         "assignment_status": "coasting"})
            return True
        allowed, reason = coast_frame_context(frame_context)
        if not allowed:
            self._note_coast_reason(track, reason)
            return False
        pt, err = _coasted_point(track, frame_index, frame_ts_ms,
                                 models.TrackPoint, "cell_id")
        if pt is None:
            self._note_coast_reason(track, err)
            return False
        track.points.append(pt)
        track._coast_frames = getattr(track, "_coast_frames", 0) + 1
        track.coasted_points = getattr(track, "coasted_points", 0) + 1
        self.coasting["coasted_points"] += 1
        rows.append({"track_id": track.track_id,
                     "prev_cell_id": track.points[-2].cell_id,
                     "current_cell_id": None,
                     "assignment_status": "coasting",
                     "position_source": COAST_POSITION_SOURCE})
        return True

    def _note_coast_recovery(self, track):
        """Una track coasted che SI riassocia e' stata 'recuperata'."""
        if (getattr(track, "coasted_points", 0) > 0
                and not getattr(track, "coasted_recovered", False)):
            track.coasted_recovered = True
            self.coasting["coasted_tracks_recovered"] += 1

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
    def update(self, cells, frame_index, frame_ts_ms=None, frame_context=None):
        """Frame corrente (celle rilevate) -> aggiorna le track attive.

        `frame_ts_ms` e' il timestamp REALE del frame (serve a propagare il
        moto); `frame_context` dichiara la qualita' del frame. Entrambi sono
        OPZIONALI: senza di essi non si fa coasting (nessun tempo inventato).

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
        max_coast = self._max_coast_frames()
        active = [t for t in self.tracks if t.status == models.STATUS_ACTIVE]
        seed_map = self._seed_matches(cells) if not active else {}
        if not active or not cells:
            rows = []
            born = []
            for ci, cell in enumerate(cells):
                track = self._new_track(cell, frame_index, seed=seed_map.get(ci))
                self.tracks.append(track)
                born.append(track)
                rows.append({"track_id": track.track_id,
                             "prev_cell_id": None, "current_cell_id": cell.cell_id,
                             "assignment_status": "new_track"})
            for t in active:
                if not cells and self._try_coast(
                        t, frame_index, frame_ts_ms, frame_context,
                        max_coast, rows):
                    continue
                self._mark_death(t, self._death_kind_for(
                    t, cells_present=False, gated_row=False,
                    max_coast=max_coast))
                rows.append({"track_id": t.track_id, "prev_cell_id":
                             t.points[-1].cell_id, "current_cell_id": None,
                             "assignment_status": "terminated"})
            # Un re-ID puo' esistere anche quando NESSUNA track era attiva (la
            # traccia morta e' quella del frame precedente): la stessa regola
            # si applica qui, altrimenti il caso piu' semplice non verrebbe mai
            # cucito.
            self._stitch_reids_in_run(born, rows)
            self._record(frame_index, rows)
            return

        _, max_cont_kmh = _speed_range_kmh()   # fonte unica: forecast.py
        features_list = [self._features(t, self.cfg) for t in active]
        n_r, n_c = len(active), len(cells)
        max_cost = float(self.cfg["max_assignment_cost"])
        cost = np.full((n_r, n_c), np.inf, dtype=float)
        # 1.3.0.38: seconda matrice SENZA gate di continuita' (solo tetto di
        # costo). Serve a contare le associazioni finali che il gate elimina.
        cost_ungated = np.full((n_r, n_c), np.inf, dtype=float)
        gated_pairs = set()   # (i,j) rifiutati dal gate ~110 km/h
        overlap_kept_pairs = set()   # (i,j) tenuti SOLO per sovrapposizione
        cand_meta = {}   # (i,j) -> {distance_km, area_ratio, int_diff, iou, pred_err}
        for i, (t, feats) in enumerate(zip(active, features_list)):
            for j, cell in enumerate(cells):
                sg, pg = self._gate_limits(feats, cell, self.cfg)
                d = haversine_km(feats["last_lonlat"], cell.centroid_lonlat)
                dt_h = (cell.timestamp_ms - feats["last_ts_ms"]) / 3600000.0
                speed_gate = dt_h > 0.0 and d / dt_h > max_cont_kmh
                # 2026-11-24 — PRIMA del gate: se i dischi (centroide + area
                # equivalente) si sovrappongono almeno per `_LINEAGE_IOU`,
                # l'associazione e' la STESSA cella che ha cambiato forma. Il
                # gate non la rifiuta (l'ID non cambia) ma il passo non e' un
                # moto: non entra nel fit, il vettore pubblicato resta nullo.
                iou = _circle_iou(feats["last_lonlat"], feats["area_avg_km2"],
                                  cell.centroid_lonlat, cell.area_km2)
                overlap_kept = bool(speed_gate
                                    and iou >= float(_LINEAGE_IOU))
                if speed_gate and not overlap_kept:
                    gated_pairs.add((i, j))
                pred_target = None
                if feats.get("predicted"):
                    pred_target = self._target_prediction(feats, cell.timestamp_ms)
                # Costo "ungated": nessun gate, vale solo il tetto di costo.
                feats_here = dict(feats)
                feats_here["predicted_lonlat"] = pred_target
                if self._improved:
                    c = match_cost_improved(feats_here, cell, self.cfg)
                else:
                    c = cell_cost(_pt_as_cell(t.points[-1]), cell, self.cfg)
                if c <= max_cost:
                    cost_ungated[i, j] = c
                # Matrice "gated": comportamento storico (INF sui rigetti).
                if speed_gate and not overlap_kept:
                    continue
                if sg is not None and d > sg:
                    continue  # fuori gate -> INF (rejected)
                if (pg is not None and pred_target is not None
                        and haversine_km(pred_target, cell.centroid_lonlat) > pg):
                    continue
                cost[i, j] = c
                if overlap_kept:
                    overlap_kept_pairs.add((i, j))
                cand_meta[(i, j)] = {
                    "distance_km": round(d, 3),
                    "area_ratio": round(cell.area_km2 / max(feats["area_avg_km2"], _EPS), 3),
                    "intensity_difference": round(abs(feats["dbz_avg"] - cell.max_dbz), 2),
                    "iou": round(iou, 4),
                    "prediction_error_km": round(
                        haversine_km(pred_target, cell.centroid_lonlat), 3)
                        if pred_target is not None else None,
                    "gates": (sg, pg),
                }

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

        def _hungarian(matrix):
            """Assignment uno-a-uno (Hungarian) sulla matrice data."""
            out, rev = {}, {}
            if matrix.size:
                fin = np.isfinite(matrix)
                if fin.any():
                    big = float(max_cost) * max(matrix.shape) + 1.0
                    work = np.where(fin, matrix, big)
                    r_idx, c_idx = linear_sum_assignment(work)
                    for ti, ci in zip(r_idx, c_idx):
                        if (np.isfinite(matrix[ti, ci])
                                and matrix[ti, ci] <= max_cost):
                            out[ti] = ci
                            rev[ci] = ti
            return out, rev

        assignment, reversed_assign = _hungarian(cost)
        ungated_assignment, _ = _hungarian(cost_ungated)
        # 1.3.0.38 — conta le ASSOCIAZIONI FINALI rifiutate dal gate: legami
        # che l'Hungarian sceglierebbe senza gate e che NON sopravvivono al
        # gate. Un singolo track con M candidati fuori gate conta 1, non M.
        self.continuity_rejections += sum(
            1 for i, j in ungated_assignment.items() if (i, j) in gated_pairs)
        # 2026-11-24 — associazioni TENUTE solo grazie alla sovrapposizione: il
        # gate le avrebbe rifiutate. Si contano solo quelle che l'Hungarian ha
        # effettivamente scelto (non le coppie candidate).
        kept_pairs = {(i, j) for (i, j) in overlap_kept_pairs
                      if assignment.get(i) == j}
        self.kept_by_overlap += len(kept_pairs)
        # 2026-11-24 — per la classificazione della NASCITA e della MORTE:
        # conta il candidato UNGATED SCELTO dall'Hungarian, non qualsiasi
        # coppia sotto `max_assignment_cost` (con 400 km di tetto ogni cella
        # avrebbe un "candidato" a 300 km). E' la stessa base di
        # `continuity_rejections`: associazione finale che il gate elimina.
        gated_assignment = {(i, j) for (i, j) in ungated_assignment.items()
                            if (i, j) in gated_pairs}
        gated_rows = {i for (i, _j) in gated_assignment}
        rejected_cols = {j for (_i, j) in gated_assignment}

        # Lineage merge/split (2026-11-11): piano calcolato PRIMA di creare le
        # track nuove, cosi' i figli possono ereditare parent_ids.
        lineage = lineage_plan(active, cells, assignment, reversed_assign)

        # ambiguous candidato (merge/split) -> EVENT_AMBIGUOUS come in Fase 1
        for ti, ci in assignment.items():
            cell = cells[ci]
            track = active[ti]
            pt = models.TrackPoint(track.track_id, frame_index, cell)
            track.points.append(pt)
            if (ti, ci) in kept_pairs:
                # ID invariato, passo tenuto per sovrapposizione: dichiarato sul
                # punto (escluso dal fit del moto) e sulla track (motivo nullo).
                pt.centroid_jump = True
                pt.overlap_iou = round(_circle_iou(
                    features_list[ti]["last_lonlat"],
                    features_list[ti]["area_avg_km2"],
                    cell.centroid_lonlat, cell.area_km2), 4)
                track.centroid_jump_points = \
                    getattr(track, "centroid_jump_points", 0) + 1
                if MOTION_NULL_CENTROID_JUMP_OVERLAPPED not in track.events:
                    track.events.append(MOTION_NULL_CENTROID_JUMP_OVERLAPPED)
            track._coast_frames = 0
            self._note_coast_recovery(track)
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
                track = self._new_track(cell, frame_index,
                                        after_reject=(j in rejected_cols))
                self.tracks.append(track)
                new_tracks_by_col[j] = track
                rows.append({"track_id": track.track_id,
                             "prev_cell_id": None, "current_cell_id": cell.cell_id,
                             "assignment_status": "new_track"})
        apply_lineage(active, new_tracks_by_col, lineage)

        for ti, track in enumerate(active):
            if ti not in assignment:
                if self._try_coast(track, frame_index, frame_ts_ms,
                                   frame_context, max_coast, rows):
                    continue
                self._mark_death(track, self._death_kind_for(
                    track, cells_present=True, gated_row=(ti in gated_rows),
                    max_coast=max_coast))
                rows.append({"track_id": track.track_id,
                             "prev_cell_id": track.points[-1].cell_id,
                             "current_cell_id": None,
                             "assignment_status": "terminated"})

        # 2026-11-24 — RE-ID DENTRO IL RUN: una traccia nata in QUESTO frame che
        # continua una traccia morta da non piu' di 15 minuti (stessa finestra
        # della metrica pubblicata) e' la stessa cella. Solo ora (dopo morti e
        # nascite) si puo' decidere: nessuna traccia VIVA viene toccata.
        self._stitch_reids_in_run(list(new_tracks_by_col.values()), rows)

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
            # 2026-11-24 — origine/morte/crescita: DOPO lineage, re-ID e
            # complete_cycles, sui punti gia' definitivi (seed eventualmente
            # rimosso). Additivo: non tocca motion ne' events.
            finalize_lifecycle_fields(track)
            # Coasting: il fit e TUTTE le statistiche di moto usano solo i
            # punti OSSERVATI. `duration_min` resta lo span temporale del
            # record (incluso l'eventuale punto stimato); la durata ONesta e'
            # il campo separato `duration_observed_min`.
            obs = [p for p in pts if getattr(p, "observed", True)] or pts
            # 2026-11-24 — un punto tenuto per sovrapposizione e' una
            # OSSERVAZIONE (la cella esiste) ma NON un passo di moto: il fit
            # usa solo i punti che non hanno saltato il centroide. Se la track
            # ne contiene almeno uno, il vettore pubblicato resta nullo con
            # motivo esplicito (nessun valore inventato).
            jump_pts = [p for p in obs if getattr(p, "centroid_jump", False)]
            fit_obs = [p for p in obs if not getattr(p, "centroid_jump", False)]
            duration_observed_min = (
                (obs[-1].timestamp_ms - obs[0].timestamp_ms) / 60000.0
                if len(obs) >= 2 else 0.0)
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
                    "duration_observed_min": 0.0,
                }
                continue
            n = len(obs)
            track.tracking_confidence = ("full" if n >= full_frames else "low")
            track.score_confidence = (
                "high" if n >= full_frames and max(p.max_dbz for p in obs) >= 35.0
                else ("medium" if n >= 3 else "low"))
            t0 = pts[0].timestamp_ms
            t1 = pts[-1].timestamp_ms
            duration_min = (t1 - t0) / 60000.0
            dist = 0.0
            for a, b in zip(obs, obs[1:]):
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
                    "duration_observed_min": round(duration_observed_min, 1),
                }
                continue
            fit = _forecast.fit_motion(fit_obs)
            use_fit = bool(fit["fit_used"]) and \
                float(fit["resid_std_km"]) < _MOTION_MAX_RESID_KM and \
                not jump_pts
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
                if jump_pts:
                    null_reason = MOTION_NULL_CENTROID_JUMP_OVERLAPPED
                elif fit["fit_used"]:
                    null_reason = "fit_residual_too_high"
                else:
                    null_reason = "fit_not_used"
                motion_quality = "unreliable"
                motion_source = "fallback"
            growth = 0.0
            if obs[0].area_km2 > 0:
                growth = (obs[-1].area_km2 - obs[0].area_km2) / obs[0].area_km2 * 100.0
            idelta = obs[-1].max_dbz - obs[0].max_dbz
            track.motion = {
                "distance_km": round(dist, 2),
                "velocity_kmh": round(velocity, 1) if velocity is not None else None,
                "direction_toward_deg": round(toward, 1) if toward is not None else None,
                "direction_from_deg": round(from_deg, 1) if from_deg is not None else None,
                "motion_quality": motion_quality,
                "motion_null_reason": null_reason,
                "motion_source": motion_source,
                "duration_min": round(duration_min, 1),
                "duration_observed_min": round(duration_observed_min, 1),
                "area_growth_pct": round(growth, 1),
                "intensity_delta_dbz": round(idelta, 2),
                "_avg_solidity": round(sum(p.solidity for p in obs) / n, 4),
                "_avg_compactness": round(sum(p.compactness for p in obs) / n, 4),
            }
            if jump_pts:
                # Audit: quanti passi sono stati ESCLUSI dal fit del moto.
                track.motion["centroid_jump_points"] = len(jump_pts)
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
            # Un punto COASTED sull'ultimo frame NON e' un'osservazione: la
            # track va chiusa come se l'ultimo frame fosse vuoto.
            obs = [p for p in track.points
                   if getattr(p, "observed", True)] or track.points
            if last is not None and obs and obs[-1].frame_index == last:
                continue                      # osservata sull'ultimo frame
            track.status = models.STATUS_DEAD
            track.death_ms = obs[-1].timestamp_ms if obs else None
            # 2026-11-24 — chiusura amministrativa a fine run: nessuna prova
            # fisica di dissipazione. Se l'ultimo punto e' coasted il grace si
            # e' esaurito col termine del run; altrimenti l'identita' non e'
            # piu' stata osservata.
            if getattr(track, "death_kind", None) is None:
                last_pt = track.points[-1] if track.points else None
                track.death_kind = (
                    models.DEATH_KIND_COASTING_EXPIRED
                    if (last_pt is not None
                        and getattr(last_pt, "position_source", None)
                        == COAST_POSITION_SOURCE)
                    else models.DEATH_KIND_LOST)
                track.death_null_reason = None
        return self.tracks


def _pt_as_cell(point):
    """Adatta un TrackPoint alla cost function legacy (senza copie costose)."""
    return point