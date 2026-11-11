#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — forecast.py (tracking 2h + nowcast, PARTE 3)

Forecast cinematico <=2h per i record di struttura convettiva: estrapolazione lineare
PESSATA dai centroidi della track associata (TITAN/SCIT-style, NO fit
quadratico), proiezione haversine (riuso di tracking.dest_from), cono di
incertezza ANISOTROPO e intensita' che decade esponenzialmente verso la media
regionale con gate sul trend recente (dBZ/scansione clampato).

CONO ANISOTROPO (2026-11-11). Il cono era un cerchio di raggio
`cone_km = 15 + 1.0*t` (p90 MISURATO dell'errore radiale, 2026-11-10). Ora e'
un'ellisse orientata lungo la direzione del moto, con due semiassi:

    along(t) = 15 + 1.0*t        (lungo il moto, stesso valore di `cone_km`)
    cross(t) = 0.6 * along(t)    (trasversale)

Ogni step porta `cone_km_along`, `cone_km_cross` e `cone_polygon` (ellisse
campionata a 32 vertici, ring GeoJSON chiuso). `cone_km` resta il semiasse
LUNGO, quindi il client che disegna un cerchio non cambia comportamento e il
cono isotropo e' ricostruibile. `cone_shape` vale "ellipse"; un record SENZA
il campo e' un cono circolare legacy.

Il rapporto 0.6 e' una SCELTA DICHIARATA, non una calibrazione: la misura
dell'errore scomposto along/cross sui pair disponibili (7 track affidabili,
533 coppie) da' un rapporto instabile e > 1 (1.42 / 1.81 / 0.58 / 0.34 nelle
fasce 0-30 / 30-60 / 60-90 / 90-130 min): il campione non basta per una
stima stabile. Il campo `cone_basis` vale "isotropic_scaled_0.6_cross" e
`cone_basis_note` riporta i numeri misurati.

FORECAST SOLO SU MOTO AFFIDABILE (2026-11-11). `forecast` viene emesso solo se
il fit cinematico e' usato (`fit_used`), se `track.motion.velocity_kmh` e'
pubblicata e se `motion_quality == "reliable"`. Altrimenti `forecast: null` e
`forecast_null_reason` esplicito: `fit_not_used`, `velocity_missing`,
`motion_unreliable` (piu' i motivi di non computabilita': `no_track`,
`insufficient_points`, `not_on_latest_frame`, `build_error`).

DECADIMENTO LEGATO AL TIPO (2026-11-11). La costante unica di 30 min diventa
due classi sul SOI del record: SOI < 70 -> "fast" (tau 21 min), SOI >= 70 ->
"slow" (tau 39 min). Sono il valore storico scalato di +-30%: nessuna
calibrazione del decadimento per classe e' disponibile, quindi e' una scelta
dichiarata nei campi `decay_class` / `decay_lambda_min`.

Layer ADDITIVO ed ENGINE-ONLY: la chiave `forecast` viene aggiunta ai dict
record di convective_structures.json. Record non proiettabile (non sull'ultimo
frame, basis < 2 punti, qualsiasi errore) -> forecast = None: nessun errore
del modulo fa fallire il run.
"""

import datetime as _dt
import math

from . import models
from .tracking import bearing_deg, dest_from, haversine_km

HORIZON_MIN = 120
STEP_MIN = 30
OFFSETS_MIN = (30, 60, 90, 120)
METHOD = "linear_weighted_haversine_decay"

_ALPHA = 0.5                  # peso 0.5^(step di distanza dall'ultimo punto)
_BASIS_MAX = 8                # ultimi N punti della track come basis
_FIT_MIN_POINTS = 4           # >=4 punti -> fit lineare, altrimenti vector-avg
_OUTLIER_SPEED_KMH = 50.0     # segmento > 50 km/h equiv. -> candidato outlier
_ENDPOINT_MAD_K = 2.5         # soglia estremi: max(50, k*mediana, mediana+k*MAD)
_ENDPOINT_MIN_KEEP = 5        # potatura estremi solo se restano >=4 punti
_CONE_MIN_KM = 6.0            # floor del cono di incertezza
# Cono ricalibrato il 2026-11-10 sui percentili MISURATI degli errori reali
# (due campioni indipendenti, R^2 0.999 e 0.985): 15 km a 30 min + 1 km/min.
# Il vecchio 8 km + 0.08 km/min copriva solo il 44% degli errori a 30 min e il
# 27% a 90 min; questa forma copre l'88-94%. Resta il semiasse LUNGO.
_CONE_DEFAULT_KM = 15.0       # intercetta fissa (p90 misurato)
_CONE_GROWTH_KM_MIN = 1.0     # crescita per minuto di offset (p90 misurato)
# --- ANISOTROPIA (2026-11-11) ---------------------------------------------
# Rapporto cross/along DICHIARATO, NON calibrato. Misura sui pair disponibili
# (7 track con fit affidabile, 533 coppie, residuo < 6 km): cross/along
# misurato = 1.42 (0-30 min), 1.81 (30-60), 0.58 (60-90), 0.34 (90-130).
# Il campione e' piccolo e instabile (21 coppie oltre 90 min) e il rapporto
# misurato NON e' < 1: 0.6 e' una SCELTA INGEGNERISTICA dichiarata.
# Verifica fatta: 0.6*(15+t) copre il 100% dei |cross| misurati in ogni
# fascia, quindi il restringimento laterale non sotto-copre il campione.
_CONE_CROSS_RATIO = 0.6
_CONE_VERTICES = 32
_CONE_SHAPE = "ellipse"
_CONE_BASIS = "isotropic_scaled_0.6_cross"
_CONE_BASIS_NOTE = (
    "declared: cross = 0.6 * along, along = p90 measured 15 km + 1 km/min; "
    "measured cross/along on reliable fits = 1.42 (0-30 min), 1.81 (30-60), "
    "0.58 (60-90), 0.34 (90-130) su 7 track / 533 coppie -> campione troppo "
    "piccolo e instabile per una calibrazione; 0.6 e' una scelta dichiarata")
_YOUNG_MINUTES = 20.0         # durata < 20 min -> cella giovane
# --- DECADIMENTO LEGATO AL TIPO (2026-11-11) ------------------------------
# Costante storica unica: 30 min. Due classi dichiarate sul SOI (0-100):
#   SOI <  70 -> "fast"  tau = 21 min  (30 * 0.7)
#   SOI >= 70 -> "slow"  tau = 39 min  (30 * 1.3)
# I due valori sono il 30 min esistente scalato di +-30%: non esiste una
# calibrazione del decadimento dBZ per classe, quindi e' una SCELTA
# DICHIARATA (esposta nei campi decay_class / decay_lambda_min).
_INTENSITY_LAMBDA_MIN = 30.0      # default se SOI e organization_score mancano
_INTENSITY_LAMBDA_FAST_MIN = 21.0
_INTENSITY_LAMBDA_SLOW_MIN = 39.0
_DECAY_SOI_SPLIT = 70.0
_TREND_SLOPE_MIN = -2.5       # dBZ/scansione, gate trend decadimento
_TREND_SLOPE_MAX = 1.5        # dBZ/scansione, gate trend crescita
_DBZ_RANGE = (20.0, 75.0)     # clamp intensita' proiettata
_MEAN_DBZ_RANGE = (10.0, 60.0)
_SPEED_RANGE_KMH = (1.0, 110.0)  # velocita' di fit plausibile
# Nome PUBBLICO della stessa tupla (una sola fonte, nessuna copia del valore):
# la usa tracking.finalize per NON pubblicare una velocity_kmh fuori range.
SPEED_RANGE_KMH = _SPEED_RANGE_KMH
_REGION_DEFAULT_DBZ = 30.0    # media regionale se nessun punto nel bundle
_KM_PER_DEG = 111.1949        # km per grado (sfera media IUGG)
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso_from_ms(ms):
    """Epoch ms -> ISO UTC '...Z'."""
    return _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).strftime(_ISO_FMT)


def _ms_from_iso(iso):
    """ISO UTC '...Z' -> epoch ms (None se non interpretabile)."""
    try:
        dt = _dt.datetime.strptime(iso, _ISO_FMT).replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None
    return int(dt.timestamp() * 1000.0)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _decay_lambda(candidate_ctx, track):
    """(tau_min, classe) del decadimento intensita' legato al tipo (SOI).

    Due classi dichiarate: SOI < 70 -> "fast" (21 min), SOI >= 70 -> "slow"
    (39 min). Il SOI del record pubblicato e' la fonte primaria; se manca si
    usa `organization_score` della track; se mancano entrambi vale il
    decadimento storico (30 min, classe "default"). I valori sono il 30 min
    storico scalato di +-30%: scelta dichiarata, non calibrazione (vedi le
    costanti _INTENSITY_LAMBDA_*)."""
    soi = candidate_ctx.get("soi") if isinstance(candidate_ctx, dict) else None
    if soi is None:
        motion = getattr(track, "motion", None) or {}
        soi = motion.get("organization_score")
    try:
        soi = float(soi)
    except (TypeError, ValueError):
        return _INTENSITY_LAMBDA_MIN, "default"
    if soi >= _DECAY_SOI_SPLIT:
        return _INTENSITY_LAMBDA_SLOW_MIN, "slow"
    return _INTENSITY_LAMBDA_FAST_MIN, "fast"


def _ellipse_polygon(center_lonlat, bearing_deg, along_km, cross_km,
                     vertices=_CONE_VERTICES):
    """Poligono GeoJSON dell'ellisse orientata lungo il moto.

    Vertice i al parametro theta = 2*pi*i/vertices: componente lungo il moto =
    along*cos(theta), componente trasversale (verso DESTRA del moto) =
    cross*sin(theta). Il punto e' a distanza hypot(along*cos, cross*sin) e
    bearing bearing_deg + atan2(cross*sin, along*cos): e' la forma
    parametrica dell'ellisse ruotata, non una circonferenza. Ring chiuso
    (vertices+1 punti, il primo ripetuto in coda come richiede GeoJSON),
    coordinate arrotondate a 5 decimali (~1 m)."""
    ring = []
    for i in range(vertices):
        theta = 2.0 * math.pi * i / vertices
        a = along_km * math.cos(theta)
        b = cross_km * math.sin(theta)
        dist = math.hypot(a, b)
        brg = (bearing_deg + math.degrees(math.atan2(b, a))) % 360.0
        lon, lat = dest_from(center_lonlat, brg, dist)
        ring.append([round(float(lon), 5), round(float(lat), 5)])
    ring.append(list(ring[0]))
    return {"type": "Polygon", "coordinates": [ring]}


def _motion_gate(track, fit):
    """Motivo per cui il forecast NON va emesso, None se e' emettibile.

    Ordine DETERMINISTICO (il primo che scatta):
      fit_not_used      -> il fit cinematico non e' utilizzabile (< 4 punti o
                           velocita' fuori range: forecast.py non proietta)
      velocity_missing  -> nessuna `velocity_kmh` pubblicata dal tracker
      motion_unreliable -> `motion_quality != "reliable"` (track.motion)
    Il gate non inventa una velocita' e non usa il fallback: senza un moto
    affidabile il cono non viene disegnato."""
    if not fit["fit_used"]:
        return "fit_not_used"
    motion = getattr(track, "motion", None) or {}
    velocity = motion.get("velocity_kmh")
    if velocity is None or float(fit["velocity_kmh"] or 0.0) <= 0.0:
        return "velocity_missing"
    if motion.get("motion_quality") != "reliable":
        return "motion_unreliable"
    return None


def _steps_weights(n):
    """Pesi 0.5^(distanza in step dall'ultimo punto), basis ordinata per tempo."""
    return [_ALPHA ** (n - 1 - i) for i in range(n)]


def _weighted_fit(t_min, values, weights):
    """Regressione lineare pesata y = a + b*t. Ritorna (a, b)."""
    sw = sum(weights)
    if sw <= 0.0:
        return sum(values) / max(len(values), 1), 0.0
    t_bar = sum(w * t for w, t in zip(weights, t_min)) / sw
    y_bar = sum(w * y for w, y in zip(weights, values)) / sw
    sxx = sum(w * (t - t_bar) ** 2 for w, t in zip(weights, t_min))
    sxy = sum(w * (t - t_bar) * (y - y_bar)
              for w, t, y in zip(weights, t_min, values))
    b = sxy / sxx if sxx > 1e-9 else 0.0
    return y_bar - b * t_bar, b


def _median(values):
    """Mediana robusta (0.0 su input vuoto)."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _remove_outliers(points):
    """Scarta i punti-spike dalla basis (interni e agli estremi).

    Punto interno: outlier se ENTRAMBI i segmenti adiacenti superano i 50
    km/h equivalenti E rimuovendolo il percorso si accorcia (salto isolato,
    non traiettoria realmente veloce; per un moto rettilineo il chord ~=
    somma dei segmenti e il punto resta anche a >50 km/h). Estremi: il
    primo/ultimo punto viene scartato se la sua velocita' di segmento supera
    la soglia robusta max(_OUTLIER_SPEED_KMH, k*mediana,
    mediana + k*MAD) stimata sui segmenti correnti, cosi' un singolo spike
    di coda (o di testa) non trascina fit e fallback. La potatura degli
    estremi parte solo se restano >= _ENDPOINT_MIN_KEEP punti (protegge le
    tracce brevi); l'invariante assoluta e' comunque >= 2 punti."""
    keep = list(points)
    removed = 0
    i = 1
    while i < len(keep) - 1:
        p0, p1, p2 = keep[i - 1], keep[i], keep[i + 1]
        d_in = haversine_km(p0.lonlat, p1.lonlat)
        d_out = haversine_km(p1.lonlat, p2.lonlat)
        dt_in = (p1.timestamp_ms - p0.timestamp_ms) / 60000.0
        dt_out = (p2.timestamp_ms - p1.timestamp_ms) / 60000.0
        s_in = d_in / dt_in * 60.0 if dt_in > 0.0 else math.inf
        s_out = d_out / dt_out * 60.0 if dt_out > 0.0 else math.inf
        if s_in > _OUTLIER_SPEED_KMH and s_out > _OUTLIER_SPEED_KMH:
            d_short = haversine_km(p0.lonlat, p2.lonlat)
            if d_short < 0.5 * (d_in + d_out):
                del keep[i]
                removed += 1
                continue
        i += 1
    if len(keep) >= _ENDPOINT_MIN_KEEP:
        speeds = []
        for a, b in zip(keep, keep[1:]):
            dt = (b.timestamp_ms - a.timestamp_ms) / 60000.0
            d = haversine_km(a.lonlat, b.lonlat)
            speeds.append(d / dt * 60.0 if dt > 0.0 else math.inf)
        med = _median(speeds)
        mad = _median([abs(s - med) for s in speeds])
        thr = max(_OUTLIER_SPEED_KMH, _ENDPOINT_MAD_K * med,
                  med + _ENDPOINT_MAD_K * mad)
        if speeds[-1] > thr:
            del keep[-1]
            removed += 1
        if len(keep) >= _ENDPOINT_MIN_KEEP:
            dt0 = (keep[1].timestamp_ms - keep[0].timestamp_ms) / 60000.0
            d0 = haversine_km(keep[0].lonlat, keep[1].lonlat)
            s0 = d0 / dt0 * 60.0 if dt0 > 0.0 else math.inf
            if s0 > thr:
                del keep[0]
                removed += 1
    return keep, removed


def fit_motion(points):
    """Fit cinematico pesato sui centroidi della track.

    Basis = ultimi min(8, n) punti ordinati per tempo meno gli outlier
    (interni ed estremi). Con >=4 punti: regressione lineare separata su
    lon/lat vs tempo (pesi 0.5^step); con meno punti o velocita' fuori range:
    fallback sulla media vettoriale dell'ultimo segmento, con velocita'
    clampata in _SPEED_RANGE_KMH (clamped=True). Ritorna dict con
    basis_frames, n_outliers, fit_used, clamped, velocity_kmh,
    velocity_km_min, direction_toward_deg, resid_std_km, cone0_km."""
    pts = sorted(points, key=lambda p: p.timestamp_ms)
    basis, n_outliers = _remove_outliers(pts[-_BASIS_MAX:])
    n = len(basis)
    if n < 2:
        return {
            "basis_frames": n,
            "n_outliers": n_outliers,
            "fit_used": False,
            "clamped": False,
            "velocity_kmh": 0.0,
            "velocity_km_min": 0.0,
            "direction_toward_deg": 0.0,
            "resid_std_km": _CONE_DEFAULT_KM,
            "cone0_km": _CONE_DEFAULT_KM,
        }

    last_ms = basis[-1].timestamp_ms
    t_min = [(p.timestamp_ms - last_ms) / 60000.0 for p in basis]
    lons = [p.lonlat[0] for p in basis]
    lats = [p.lonlat[1] for p in basis]
    weights = _steps_weights(n)

    fit_used = False
    clamped = False
    vel_km_min = 0.0
    vel_kmh = 0.0
    brg = 0.0
    resid_km = _CONE_DEFAULT_KM
    lo, hi = _SPEED_RANGE_KMH
    if n >= _FIT_MIN_POINTS:
        a_lon, b_lon = _weighted_fit(t_min, lons, weights)
        a_lat, b_lat = _weighted_fit(t_min, lats, weights)
        lat_ref = math.radians(sum(lats) / n)
        vx = b_lon * _KM_PER_DEG * math.cos(lat_ref)   # km/min verso est
        vy = b_lat * _KM_PER_DEG                        # km/min verso nord
        cand_km_min = math.hypot(vx, vy)
        if lo <= cand_km_min * 60.0 <= hi:
            fit_used = True
            vel_km_min = cand_km_min
            vel_kmh = cand_km_min * 60.0
            brg = math.degrees(math.atan2(vx, vy)) % 360.0
            sw = sum(weights)
            acc = 0.0
            for idx, p in enumerate(basis):
                fitted = (a_lon + b_lon * t_min[idx], a_lat + b_lat * t_min[idx])
                acc += weights[idx] * haversine_km(p.lonlat, fitted) ** 2
            resid_km = math.sqrt(acc / sw)

    if not fit_used:
        p0, p1 = basis[-2], basis[-1]
        seg = haversine_km(p0.lonlat, p1.lonlat)
        dt = (p1.timestamp_ms - p0.timestamp_ms) / 60000.0
        vel_km_min = seg / dt if dt > 0.0 else 0.0
        vel_kmh = vel_km_min * 60.0
        brg = bearing_deg(p0.lonlat, p1.lonlat) if seg > 0.0 else 0.0
        if vel_kmh > hi:
            vel_kmh = hi
            clamped = True
        elif vel_kmh < lo:
            vel_kmh = lo
            clamped = True
        vel_km_min = vel_kmh / 60.0
        resid_km = _CONE_DEFAULT_KM

    return {
        "basis_frames": n,
        "n_outliers": n_outliers,
        "fit_used": fit_used,
        "clamped": clamped,
        "velocity_kmh": vel_kmh,
        "velocity_km_min": vel_km_min,
        "direction_toward_deg": brg,
        "resid_std_km": resid_km,
        "cone0_km": resid_km,
    }


def _region_dbz_means(bundle):
    """Media regionale di max/mean dBZ su TUTTI i punti di tutte le track
    del bundle (cella + storm object); default 30.0 dBZ se nessun punto."""
    maxes, means = [], []
    pools = list(getattr(bundle, "tracks", None) or []) + \
        list(getattr(bundle, "storm_tracks", None) or [])
    for track in pools:
        for p in track.points:
            maxes.append(float(p.max_dbz))
            means.append(float(p.mean_dbz))
    if not maxes:
        return _REGION_DEFAULT_DBZ, _REGION_DEFAULT_DBZ
    return sum(maxes) / len(maxes), sum(means) / len(means)


def _dbz_trend(points):
    """Slope OLS (dBZ per scansione) del max_dbz sugli ultimi min(5, n) punti."""
    ys = [float(p.max_dbz) for p in points[-5:]]
    n = len(ys)
    if n < 2:
        return 0.0
    x_bar = (n - 1) / 2.0
    y_bar = sum(ys) / n
    sxx = sum((i - x_bar) ** 2 for i in range(n))
    sxy = sum((i - x_bar) * (y - y_bar) for i, y in enumerate(ys))
    return sxy / sxx if sxx > 1e-9 else 0.0


def _confidence(fit, young):
    """high: basis >=6 + nessun outlier + residui <6 km; medium: basis >=4;
    low: basis <4, cella giovane (<20 min), fit NON usato (fallback),
    velocita' clampata o qualsiasi outlier scartato (basis non pulita). Solo
    fit realmente accettati su basis pulite possono essere medium/high."""
    n = fit["basis_frames"]
    if (n < _FIT_MIN_POINTS or young or not fit["fit_used"]
            or fit["clamped"] or fit["n_outliers"] > 0):
        return "low"
    if n >= 6 and fit["n_outliers"] == 0 and fit["resid_std_km"] < 6.0:
        return "high"
    return "medium"


def _resolve_track(candidate_ctx, bundle):
    """Track associata al candidato: dalla chiave 'track' del ctx, oppure da
    track_id/track_type risolti sul bundle."""
    if isinstance(candidate_ctx, dict) and candidate_ctx.get("track") is not None:
        return candidate_ctx["track"]
    track_id = candidate_ctx.get("track_id") if isinstance(candidate_ctx, dict) else None
    if track_id is None:
        return None
    cell_pool = list(getattr(bundle, "tracks", None) or [])
    storm_pool = list(getattr(bundle, "storm_tracks", None) or [])
    track_type = candidate_ctx.get("track_type")
    pools = (storm_pool, cell_pool) if track_type == "storm_object" else \
        (cell_pool, storm_pool)
    for pool in pools:
        for track in pool:
            if track.track_id == track_id:
                return track
    return None


def _on_latest_frame(candidate_ctx, track, bundle):
    """True se il candidato e' vivo sull'ultimo frame (chiave del ctx, oppure
    derivata da frame_index == ultimo frame con celle nel bundle)."""
    if isinstance(candidate_ctx, dict) and "on_latest_frame" in candidate_ctx:
        return bool(candidate_ctx["on_latest_frame"])
    cells_by_frame = getattr(bundle, "cells_by_frame", None) or []
    n_frames = sum(1 for f in cells_by_frame if f)
    if not n_frames:
        return False
    return track.points[-1].frame_index == n_frames - 1


def _anchor_ms(bundle, last):
    """Ancora temporale: radar_timestamp del bundle (ms o ISO), fallback
    sull'ultimo punto della track."""
    ms = getattr(bundle, "radar_timestamp_ms", None)
    if ms is not None:
        return int(ms)
    ms = _ms_from_iso(getattr(bundle, "radar_timestamp_iso", None))
    if ms is not None:
        return ms
    return int(last.timestamp_ms)


def _build(candidate_ctx, bundle):
    """Costruisce il blocco forecast (dict) o None se non computabile."""
    block, _reason = _build_with_reason(candidate_ctx, bundle)
    return block


def _build_with_reason(candidate_ctx, bundle):
    """(blocco forecast, motivo del null). Motivo None se il blocco esiste."""
    track = _resolve_track(candidate_ctx, bundle)
    if track is None:
        return None, "no_track"
    if len(track.points) < 2:
        return None, "insufficient_points"
    if not _on_latest_frame(candidate_ctx, track, bundle):
        return None, "not_on_latest_frame"

    points = sorted(track.points, key=lambda p: p.timestamp_ms)
    fit = fit_motion(points)
    if fit["basis_frames"] < 2:
        return None, "insufficient_points"
    gate_reason = _motion_gate(track, fit)
    if gate_reason is not None:
        return None, gate_reason

    last = points[-1]
    duration_min = (last.timestamp_ms - points[0].timestamp_ms) / 60000.0
    young = duration_min < _YOUNG_MINUTES
    anchor_ms = _anchor_ms(bundle, last)
    generated_at = getattr(bundle, "generated_at_iso", None) or models.utcnow_iso()
    mean_max, mean_mean = _region_dbz_means(bundle)
    i0 = float(last.max_dbz)
    m0 = float(last.mean_dbz)
    trend = _clamp(_dbz_trend(points), _TREND_SLOPE_MIN, _TREND_SLOPE_MAX)
    growth = _CONE_GROWTH_KM_MIN * (1.5 if young else 1.0)
    decay_lambda, decay_class = _decay_lambda(candidate_ctx, track)

    steps = []
    for offset_min in OFFSETS_MIN:
        lon, lat = dest_from(last.lonlat, fit["direction_toward_deg"],
                             fit["velocity_km_min"] * offset_min)
        decay = math.exp(-offset_min / decay_lambda)
        i_dec = mean_max + (i0 - mean_max) * decay
        max_dbz = _clamp(i_dec + trend * (offset_min / 5.0), *_DBZ_RANGE)
        mean_dbz = _clamp(mean_mean + (m0 - mean_mean) * decay, *_MEAN_DBZ_RANGE)
        # Semiasse LUNGO = cono isotropo storico (stesso valore di `cone_km`);
        # semiasse TRASVERSALE = along * rapporto dichiarato (0.6).
        along_km = max(_CONE_MIN_KM, _CONE_DEFAULT_KM,
                       fit["cone0_km"]) + growth * offset_min
        cross_km = along_km * _CONE_CROSS_RATIO
        steps.append({
            "offset_min": offset_min,
            "at": _iso_from_ms(anchor_ms + offset_min * 60000),
            "position": [round(float(lon), 5), round(float(lat), 5)],
            "cone_km": round(along_km, 1),
            "cone_km_along": round(along_km, 1),
            "cone_km_cross": round(cross_km, 1),
            "cone_polygon": _ellipse_polygon(
                (lon, lat), fit["direction_toward_deg"], along_km, cross_km),
            "max_dbz": round(max_dbz, 1),
            "mean_dbz": round(mean_dbz, 1),
            "area_km2": round(float(last.area_km2), 1),
        })

    return {
        "horizon_min": HORIZON_MIN,
        "step_min": STEP_MIN,
        "generated_at": generated_at,
        "method": METHOD,
        "cone_shape": _CONE_SHAPE,
        "cone_basis": _CONE_BASIS,
        "cone_basis_note": _CONE_BASIS_NOTE,
        "cone_cross_ratio": _CONE_CROSS_RATIO,
        "cone_vertices": _CONE_VERTICES,
        "decay_class": decay_class,
        "decay_lambda_min": decay_lambda,
        "basis_frames": int(fit["basis_frames"]),
        "confidence": _confidence(fit, young),
        "fit_used": bool(fit["fit_used"]),
        "clamped": bool(fit["clamped"]),
        "direction_toward_deg": round(float(fit["direction_toward_deg"]), 1),
        "velocity_kmh": round(float(fit["velocity_kmh"]), 1),
        "steps": steps,
    }, None


def build_forecast(candidate_ctx, bundle):
    """Blocco `forecast` per un record di struttura, None se non computabile.

    candidate_ctx = dict candidato (on_latest_frame/track_id/track_type) con
    eventuale track associata sotto la chiave 'track'; senza 'track' la track
    viene risolta da track_id/track_type sul bundle. Qualsiasi errore -> None
    (layer additivo: mai rompere il run). Per il motivo del None usare
    build_forecast_with_reason()."""
    block, _reason = build_forecast_with_reason(candidate_ctx, bundle)
    return block


def build_forecast_with_reason(candidate_ctx, bundle):
    """(blocco `forecast`, `forecast_null_reason`).

    Il motivo e' None quando il blocco esiste; altrimenti e' uno di:
    `no_track`, `insufficient_points`, `not_on_latest_frame`, `fit_not_used`,
    `velocity_missing`, `motion_unreliable`, `build_error`. Qualsiasi errore
    imprevisto -> (None, "build_error") senza far fallire il run."""
    try:
        return _build_with_reason(candidate_ctx, bundle)
    except Exception:
        return None, "build_error"


def attach_forecasts(bundle):
    """Aggiunge `forecast` + `forecast_null_reason` a ogni record.

    Ritorna il numero di candidati con forecast calcolato (0 su bundle senza
    candidati)."""
    count = 0
    for candidate in (getattr(bundle, "convective_structures", None) or []):
        block, reason = build_forecast_with_reason(candidate, bundle)
        candidate["forecast"] = block
        candidate["forecast_null_reason"] = None if block is not None else reason
        if block is not None:
            count += 1
    return count
