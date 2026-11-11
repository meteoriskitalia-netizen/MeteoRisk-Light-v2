#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — nwp_motion.py (triangolazione moto osservato + NWP)

SCOPO. Il moto di una struttura convettiva ha DUE fonti indipendenti:

  1. il fit cinematico sui centroidi della track (``forecast.fit_motion``),
     che misura il passato recente (20-40 min) e lo estrapola;
  2. il vento di steering del modello NWP nel punto della struttura
     (``data/latest/meteorisk-points.json``, Open-Meteo, 257 punti, 3 giorni),
     che rappresenta il futuro previsto.

La verifica storica (``scripts/validation/nwp_motion_validation.py``) misura
sui pair reali se e quanto la fusione batte l'estrapolazione pura. Il modulo:

  * calcola ``nwp_motion`` = vento di steering NWP interpolato (IDW) sui 4
    punti piu' vicini entro 80 km, media pesata per massa 850/700/600/500 hPa;
  * fonde osservato e NWP in ``motion_blend`` con un peso ``w`` legato
    all'affidabilita' del fit (``motion_weights``);
  * costruisce un ENSEMBLE di 200 traiettorie perturbando velocita' e
    direzione di ``motion_blend`` con la dispersione MISURATA (divergenza
    osservato-NWP + errore storico del tracker) e ne pubblica le isolinee di
    densita' al 25/50/75/90% come poligoni.

ONESTA'. Le bande NON sono una probabilita' calibrata: non esiste un dataset
di verifiche etichettate per questa grandezza. ``probability_calibrated`` vale
sempre ``false`` con motivo. Le soglie di densita' sono frazioni di massa
dell'ensemble, non frequenze osservate.

DIPENDENZE. Solo stdlib + numpy (il motore ha gia' numpy). Nessun import da
``tracking``/``forecast`` a livello di modulo: distanza e proiezione sono
implementate qui, cosi' il modulo e' importabile e testabile da solo.

CAMPI PUBBLICATI (per record di struttura):
  nwp_motion              {speed_kmh, direction_toward_deg, u_kmh, v_kmh} | None
  nwp_motion_null_reason  motivo del None (dataset_unavailable,
                          no_point_within_80km, valid_time_outside_dataset,
                          dataset_stale, no_wind_level_available, ...)
  nwp_motion_detail       fonte, riferimento, valid_time, punti, livelli, quota
  motion_blend            vettore fuso {speed_kmh, direction_toward_deg, ...}
  motion_weights          {obs, nwp, rule, fit_used, resid_std_km}
  motion_blend_components componenti obs/NWP separati (per l'audit)
  ensemble                {n, steps: [{offset_min, at, centroid,
                          density_levels: [{level, polygon, area_km2, ...}]}]}
  ensemble_basis          distribuzione, sigma, provenienza, N, seed
  ensemble_null_reason    motivo se l'ensemble non c'e'

FILE DI COMPETENZA. Questo modulo e' nuovo e autonomo; il wiring in
``forecast.py`` e' consegnato come patch separata (il file e' conteso).
"""

import datetime as _dt
import hashlib
import json
import math
import os
import time

import numpy as np

# ---------------------------------------------------------------------------
# Costanti dichiarate
# ---------------------------------------------------------------------------

KM_PER_DEG_LAT = 111.1949          # sfera media IUGG, stesso valore di forecast.py

# Livelli di steering e pesi di massa approssimati (trapezio su 1000-500 hPa):
#   850 hPa: (1000-700)/2 = 150
#   700 hPa: ( 850-600)/2 = 125
#   600 hPa: ( 700-500)/2 = 100
#   500 hPa: ( 600-500)/2 =  50   (solo mezzo strato: l'integrale si ferma a 500)
# Totale 425 hPa. Il 600 hPa non ha geopotenziale nel dataset: la quota del
# top viene dal 500 hPa (media IDW dei punti usati), non interpolata.
LEVEL_MASS_WEIGHTS = ((850, 150.0), (700, 125.0), (600, 100.0), (500, 50.0))

NEAREST_K = 4                      # punti usati dall'IDW
MAX_DIST_KM = 80.0                 # gate: oltre, nwp_motion = null
IDW_EPS_KM2 = 1.0                  # +1 km^2 al denominatore (punto coincidente)
MAX_DATASET_AGE_MIN = 24 * 60.0    # oltre 24 h il dataset e' stantio -> null

N_MEMBERS = 200                    # traiettorie dell'ensemble
ENSEMBLE_OFFSETS_MIN = (30, 60, 90, 120)
DENSITY_LEVELS = (0.25, 0.50, 0.75, 0.90)
GRID_DEG = 0.02                    # passo minimo della griglia (~2.2 km)
KDE_MIN_BW_KM = 2.0                # bandwidth minima del KDE (km)

# Pesi del blend (regola DICHIARATA, non calibrata):
#   fit usato e residuo < 2 km   -> 0.8 (il fit spiega quasi tutto il moto)
#   fit usato e residuo 2-6 km   -> 0.5 (fit e NWP valgono uguale)
#   fit usato e residuo >= 6 km  -> 0.4 (il fit e' rumoroso ma resta il segnale
#                                        locale piu' fresco)
#   fit non usato                -> 0.3 (solo il fallback del tracker: il NWP
#                                        e' la fonte migliore)
WEIGHT_RULES = (
    ("fit_used_resid_lt_2km", 0.8),
    ("fit_used_resid_2_6km", 0.5),
    ("fit_used_resid_ge_6km", 0.4),
    ("fit_not_used", 0.3),
)
WEIGHT_DEFAULT = 0.3

# Sigma dell'ensemble. Il contributo "tracker" e' MISURATO sui pair storici
# (stesso dataset della verifica (a)/(b)/(c), 2026-11-11): deviazione standard
# dell'errore di velocita' e di direzione a 60 min dell'estrapolazione pura.
# I valori sono aggiornati da ``scripts/validation/nwp_motion_validation.py``
# (costanti riempite con la misura, non inventate).
TRACKER_SIGMA_SPEED_KMH = 28.9     # MISURATO: RMS errore lungo-traccia a 60 min
TRACKER_SIGMA_DIR_DEG = 23.52      # MISURATO: atan2(RMS cross 21.14, velocita' media 48.57)
SIGMA_SPEED_FLOOR_KMH = 3.0
SIGMA_DIR_FLOOR_DEG = 4.0
SIGMA_SPEED_CAP_KMH = 40.0         # oltre, l'ensemble non dice piu' nulla
SIGMA_DIR_CAP_DEG = 60.0

# Riferimento MISURATO della verifica storica (2026-11-11, 316 casi a 60 min,
# 547 a 30 min, 194 a 90, 123 a 120): serve a dichiarare quanto l'ensemble e'
# conservativo. I sigma qui sopra sono quelli dell'ESTRAPOLAZIONE (tracker),
# non del blend: il blend misura p50 18.66 / p90 47.32 km a 60 min.
VALIDATION_REFERENCE = {
    "measured_on": "2026-11-11",
    "cases_60min": 316,
    "extrapolation_60min_km": {"p50": 21.78, "p90": 57.54},
    "blend_60min_km": {"p50": 18.66, "p90": 47.32},
    "note": ("sigma da errore dell'estrapolazione -> l'ensemble e' "
             "CONSERVATIVO rispetto all'errore misurato del blend"),
}

METHOD = "idw4_massweighted_850_500_blend_ensemble200"
ENSEMBLE_METHOD = "gaussian_antithetic_speed_dir_grid_marching_squares"

# Cache del dataset (path, mtime, size) -> PointDataset. Una lettura per run.
_DATASET_CACHE = {}
_LAST_LOAD_STATS = {}


# ---------------------------------------------------------------------------
# Geometria (stdlib, nessuna dipendenza da tracking.py)
# ---------------------------------------------------------------------------

def haversine_km(a, b):
    """Distanza sferica (km) fra due (lon, lat) in gradi."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    h = (math.sin(dlat / 2.0) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2)
    return 2.0 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def dest_from(lonlat, bearing, dist_km):
    """Punto a ``dist_km`` dal punto ``lonlat`` lungo il bearing dato."""
    lon, lat = math.radians(lonlat[0]), math.radians(lonlat[1])
    brg = math.radians(bearing)
    d = dist_km / 6371.0088
    lat2 = math.asin(math.sin(lat) * math.cos(d)
                     + math.cos(lat) * math.sin(d) * math.cos(brg))
    lon2 = lon + math.atan2(math.sin(brg) * math.sin(d) * math.cos(lat),
                            math.cos(d) - math.sin(lat) * math.sin(lat2))
    return (math.degrees(lon2), math.degrees(lat2))


def _wrap180(deg):
    """Angolo in (-180, 180]."""
    return (float(deg) + 180.0) % 360.0 - 180.0


def _uv_from_speed_dir(speed, direction_toward_deg):
    """Componenti (u est, v nord) da modulo e direzione VERSO cui si muove."""
    brg = math.radians(direction_toward_deg)
    return speed * math.sin(brg), speed * math.cos(brg)


def _speed_dir_from_uv(u, v):
    speed = math.hypot(u, v)
    direction = math.degrees(math.atan2(u, v)) % 360.0
    return speed, direction


# ---------------------------------------------------------------------------
# Tempo (Europe/Rome senza tzdata: regola UE esplicita)
# ---------------------------------------------------------------------------

def _last_sunday(year, month):
    """Ultima domenica del mese (anno, mese) -> datetime naive."""
    if month == 12:
        nxt = _dt.datetime(year + 1, 1, 1)
    else:
        nxt = _dt.datetime(year, month + 1, 1)
    day = nxt - _dt.timedelta(days=1)
    while day.weekday() != 6:
        day -= _dt.timedelta(days=1)
    return day


def _rome_utc_offset_hours(dt_utc):
    """Ore da sommare all'UTC per ottenere l'ora locale Europe/Rome.

    Regola UE: DST dalle 01:00 UTC dell'ultima domenica di marzo alle 01:00
    UTC dell'ultima domenica di ottobre. Deterministico, nessuna tzdata.
    """
    start = _last_sunday(dt_utc.year, 3).replace(hour=1)
    end = _last_sunday(dt_utc.year, 10).replace(hour=1)
    return 2.0 if start <= dt_utc < end else 1.0


def _parse_iso_utc(value):
    """ISO 'YYYY-MM-DDTHH:MM[:SS][Z|+00:00]' -> epoch secondi UTC (None se no)."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.timestamp()


def _iso_from_ms(ms):
    return _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Dataset punti
# ---------------------------------------------------------------------------

class PointDataset:
    """Vista minima di meteorisk-points.json: solo i campi che servono.

    Tiene in memoria, per ogni livello, due array (n_punti, n_ore) con le
    componenti u/v del vento, piu' i geopotenziali 850/700/500 hPa. Il resto
    del JSON viene scartato subito dopo la lettura.
    """

    def __init__(self, path, generated_at, time_base_utc, lats, lons,
                 wind, geo, model, n_hours, load_stats=None):
        self.path = path
        self.generated_at = generated_at
        self.time_base_utc = time_base_utc
        self.lats = np.asarray(lats, dtype=float)
        self.lons = np.asarray(lons, dtype=float)
        self.wind = wind                  # livello -> (u (n,h), v (n,h))
        self.geo = geo                    # livello -> (n,h) geopotenziale
        self.model = model
        self.n_hours = int(n_hours)
        self.load_stats = load_stats or {}

    @property
    def n_points(self):
        return int(self.lats.size)

    def nearest(self, lon, lat, k=NEAREST_K, max_km=MAX_DIST_KM):
        """I k punti piu' vicini entro max_km: [(idx, dist_km), ...]."""
        if self.n_points == 0:
            return []
        # distanza equirettangolare locale (sufficiente a <100 km) poi verifica
        dlat = (self.lats - lat) * KM_PER_DEG_LAT
        dlon = (self.lons - lon) * KM_PER_DEG_LAT * math.cos(math.radians(lat))
        d2 = dlat * dlat + dlon * dlon
        order = np.argsort(d2)[:max(int(k), 1)]
        out = []
        for idx in order:
            d = haversine_km((float(self.lons[idx]), float(self.lats[idx])),
                             (lon, lat))
            if d <= max_km:
                out.append((int(idx), float(d)))
        return out

    def hour_index(self, valid_ts):
        """Indice orario (arrotondato) e offset in minuti rispetto all'ora."""
        delta_h = (valid_ts - self.time_base_utc) / 3600.0
        idx = int(round(delta_h))
        offset_min = (delta_h - idx) * 60.0
        if idx < 0 or idx >= self.n_hours:
            return None, offset_min
        return idx, offset_min


def load_points(path=None, model="best_match", force=False):
    """Carica (o riusa dalla cache) il point dataset.

    Ordine di risoluzione del path:
      1. argomento esplicito;
      2. env METEORISK_POINTS_JSON;
      3. <repo>/data/latest/meteorisk-points.json (repo = 2 livelli sopra
         questo file: scripts/radar_engine/nwp_motion.py);
      4. ./data/latest/meteorisk-points.json (cwd).

    Ritorna un PointDataset o None (file assente/illeggibile). Non solleva:
    il layer e' additivo e non deve mai far fallire il run.
    """
    global _LAST_LOAD_STATS
    candidates = []
    if path:
        candidates.append(path)
    env = os.environ.get("METEORISK_POINTS_JSON")
    if env:
        candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(here)),
                                   "data", "latest", "meteorisk-points.json"))
    candidates.append(os.path.join("data", "latest", "meteorisk-points.json"))
    chosen = None
    for cand in candidates:
        if cand and os.path.isfile(cand):
            chosen = cand
            break
    if chosen is None:
        _LAST_LOAD_STATS = {"status": "missing", "candidates": candidates}
        return None
    try:
        st = os.stat(chosen)
        key = (os.path.abspath(chosen), int(st.st_mtime), int(st.st_size))
    except OSError:
        return None
    if not force and key in _DATASET_CACHE:
        return _DATASET_CACHE[key]

    t0 = time.perf_counter()
    try:
        with open(chosen, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        _LAST_LOAD_STATS = {"status": "unreadable", "error": str(exc)}
        return None

    points = raw.get("points") or []
    generated_at = raw.get("generated_at")
    day0 = raw.get("day0")
    tz = raw.get("timezone") or "Europe/Rome"
    n_hours = 0
    for p in points:
        hourly = ((p.get("models") or {}).get(model) or {}).get("hourly") or {}
        for lvl, _w in LEVEL_MASS_WEIGHTS:
            spd = hourly.get("wind_speed_%dhPa" % lvl)
            if isinstance(spd, list):
                n_hours = max(n_hours, len(spd))
    if not points or not day0 or n_hours <= 0:
        _LAST_LOAD_STATS = {"status": "empty", "points": len(points),
                            "day0": day0, "n_hours": n_hours}
        return None

    # time_base = mezzanotte LOCALE del day0 -> UTC con la regola UE.
    try:
        base_local = _dt.datetime.strptime(day0 + "T00:00", "%Y-%m-%dT%H:%M")
    except ValueError:
        _LAST_LOAD_STATS = {"status": "bad_day0", "day0": day0}
        return None
    # offset calcolato a mezzogiorno locale (evita l'ambiguita' dell'ora DST)
    probe = base_local.replace(hour=12)
    offset_h = _rome_utc_offset_hours(probe)
    time_base_utc = (base_local - _dt.timedelta(hours=offset_h)).replace(
        tzinfo=_dt.timezone.utc).timestamp()

    lats, lons = [], []
    wind = {}
    for lvl, _w in LEVEL_MASS_WEIGHTS:
        wind[lvl] = (np.full((len(points), n_hours), np.nan),
                     np.full((len(points), n_hours), np.nan))
    geo = {lvl: np.full((len(points), n_hours), np.nan)
           for lvl in (850, 700, 500)}
    for i, p in enumerate(points):
        lats.append(float(p.get("lat")))
        lons.append(float(p.get("lon")))
        hourly = ((p.get("models") or {}).get(model) or {}).get("hourly") or {}
        for lvl, _w in LEVEL_MASS_WEIGHTS:
            spd = hourly.get("wind_speed_%dhPa" % lvl)
            drc = hourly.get("wind_direction_%dhPa" % lvl)
            if not isinstance(spd, list) or not isinstance(drc, list):
                continue
            n = min(len(spd), len(drc), n_hours)
            u_arr, v_arr = wind[lvl]
            for h in range(n):
                s, d = spd[h], drc[h]
                if s is None or d is None:
                    continue
                # direzione meteorologica = provenienza; il moto e' VERSO +180.
                brg = math.radians((float(d) + 180.0) % 360.0)
                u_arr[i, h] = float(s) * math.sin(brg)
                v_arr[i, h] = float(s) * math.cos(brg)
        for lvl in (850, 700, 500):
            arr = hourly.get("geopotential_height_%dhPa" % lvl)
            if isinstance(arr, list):
                n = min(len(arr), n_hours)
                for h in range(n):
                    if arr[h] is not None:
                        geo[lvl][i, h] = float(arr[h])

    elapsed = time.perf_counter() - t0
    _LAST_LOAD_STATS = {
        "status": "ok",
        "path": chosen,
        "bytes": st.st_size,
        "points": len(points),
        "hours": n_hours,
        "model": model,
        "load_s": round(elapsed, 3),
        "time_base_utc": _iso_from_ms(time_base_utc * 1000.0),
        "generated_at": generated_at,
        "timezone": tz,
    }
    ds = PointDataset(chosen, generated_at, time_base_utc, lats, lons, wind,
                      geo, model, n_hours, dict(_LAST_LOAD_STATS))
    _DATASET_CACHE[key] = ds
    return ds


def load_stats():
    """Ultimo esito di load_points (per diagnostica/test)."""
    return dict(_LAST_LOAD_STATS)


# ---------------------------------------------------------------------------
# Steering NWP
# ---------------------------------------------------------------------------

def steering_for_position(dataset, lon, lat, valid_ms, k=NEAREST_K,
                          max_km=MAX_DIST_KM):
    """Vento di steering NWP interpolato nella posizione della struttura.

    IDW sui k punti piu' vicini entro max_km (peso 1/(d^2+1)), poi media
    pesata per massa sui livelli disponibili (850/700/600/500 hPa). Il valore
    e' un proxy del vento medio 0-6 km: il dataset non ha la superficie, il
    livello piu' basso e' 850 hPa (~1,5 km) e il piu' alto usato e' 500 hPa
    (~5,5 km). Il campo ``layer_top_m`` dichiara la quota reale del top.

    Ritorna un dict con ``value``/``null_reason``/``source``/``reference`` e i
    dettagli (punti, livelli, valid_time, dataset_age_min). Mai eccezioni.
    """
    if dataset is None:
        return _null("dataset_unavailable",
                     "data/latest/meteorisk-points.json non leggibile")
    if valid_ms is None:
        return _null("valid_time_missing",
                     "istante del run non disponibile")
    near = dataset.nearest(lon, lat, k=k, max_km=max_km)
    if not near:
        idx = (int(np.argmin((dataset.lats - lat) ** 2
                             + (dataset.lons - lon) ** 2))
               if dataset.n_points else None)
        dmin = (haversine_km((float(dataset.lons[idx]),
                              float(dataset.lats[idx])), (lon, lat))
                if idx is not None else None)
        return _null("no_point_within_%dkm" % int(max_km),
                     ("punto piu' vicino a %.1f km" % dmin)
                     if dmin is not None else "nessun punto nel dataset",
                     extra={"nearest_km": round(dmin, 1)
                            if dmin is not None else None})
    idx_h, offset_min = dataset.hour_index(valid_ms / 1000.0)
    if idx_h is None:
        return _null("valid_time_outside_dataset",
                     "istante fuori dalle %d ore del dataset" % dataset.n_hours)
    age_min = None
    gen_ts = _parse_iso_utc(dataset.generated_at)
    if gen_ts is not None:
        age_min = (valid_ms / 1000.0 - gen_ts) / 60.0
        if age_min > MAX_DATASET_AGE_MIN:
            return _null("dataset_stale",
                         "dataset di %.0f min prima dell'istante richiesto"
                         % age_min)

    u_sum = 0.0
    v_sum = 0.0
    w_sum = 0.0
    levels_used = []
    levels_missing = []
    for lvl, mass_w in LEVEL_MASS_WEIGHTS:
        if lvl not in dataset.wind:
            levels_missing.append(lvl)
            continue
        u_arr, v_arr = dataset.wind[lvl]
        u_acc = 0.0
        v_acc = 0.0
        idw_w = 0.0
        n_ok = 0
        for idx, dist in near:
            u = u_arr[idx, idx_h]
            v = v_arr[idx, idx_h]
            if not np.isfinite(u) or not np.isfinite(v):
                continue
            w = 1.0 / (dist * dist + IDW_EPS_KM2)
            u_acc += w * u
            v_acc += w * v
            idw_w += w
            n_ok += 1
        if n_ok == 0 or idw_w <= 0.0:
            levels_missing.append(lvl)
            continue
        u_l = u_acc / idw_w
        v_l = v_acc / idw_w
        u_sum += mass_w * u_l
        v_sum += mass_w * v_l
        w_sum += mass_w
        levels_used.append(lvl)
    if w_sum <= 0.0:
        return _null("no_wind_level_available",
                     "nessun livello 850/700/600/500 hPa valorizzato nei "
                     "punti vicini")

    u = u_sum / w_sum
    v = v_sum / w_sum
    speed, direction = _speed_dir_from_uv(u, v)
    top_hpa = min(levels_used)
    bottom_hpa = max(levels_used)
    top_m = _mean_geopotential(dataset, near, idx_h, top_hpa)
    proxy = ("0-6 km (proxy: media pesata per massa %.0f-%.0f hPa; "
             "superficie assente, top a %s)"
             % (bottom_hpa, top_hpa,
                ("%.0f m" % top_m) if top_m is not None else "quota ignota"))
    return {
        "value": {
            "speed_kmh": round(speed, 2),
            "direction_toward_deg": round(direction, 1),
            "u_kmh": round(u, 2),
            "v_kmh": round(v, 2),
        },
        "null_reason": None,
        "source": ("data/latest/meteorisk-points.json (Open-Meteo, "
                   "model=%s)" % dataset.model),
        "reference": ("IDW %d punti entro %.0f km; media pesata per massa "
                      "850/700/600/500 hPa (150/125/100/50); %s"
                      % (len(near), max_km, proxy)),
        "method": METHOD,
        "valid_time": _iso_from_ms(valid_ms),
        "hour_index": idx_h,
        "hour_offset_min": round(offset_min, 1),
        "dataset_generated_at": dataset.generated_at,
        "dataset_age_min": round(age_min, 1) if age_min is not None else None,
        "points_used": [{"index": i, "distance_km": round(d, 1)}
                        for i, d in near],
        "levels_used_hpa": levels_used,
        "levels_missing_hpa": levels_missing,
        "layer_bottom_hpa": bottom_hpa,
        "layer_top_hpa": top_hpa,
        "layer_top_m": round(top_m, 0) if top_m is not None else None,
        "layer_proxy": proxy,
    }


def _mean_geopotential(dataset, near, idx_h, level_hpa):
    """Media IDW del geopotenziale del livello (None se assente)."""
    arr = dataset.geo.get(level_hpa)
    if arr is None:
        return None
    vals = []
    wsum = 0.0
    for idx, dist in near:
        val = arr[idx, idx_h]
        if not np.isfinite(val):
            continue
        w = 1.0 / (dist * dist + IDW_EPS_KM2)
        vals.append(w * float(val))
        wsum += w
    if wsum <= 0.0:
        return None
    return sum(vals) / wsum


def _null(reason, detail, extra=None):
    out = {
        "value": None,
        "null_reason": reason,
        "detail": detail,
        "source": "data/latest/meteorisk-points.json (Open-Meteo)",
        "reference": "nessun valore inventato: senza NWP il campo e' null",
    }
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Blend osservato + NWP
# ---------------------------------------------------------------------------

def weight_for_fit(fit_used, resid_std_km):
    """Peso w dell'osservato nella regola dichiarata (WEIGHT_RULES)."""
    try:
        resid = float(resid_std_km)
    except (TypeError, ValueError):
        resid = 999.0
    if not fit_used:
        return WEIGHT_DEFAULT, "fit_not_used"
    if resid < 2.0:
        return 0.8, "fit_used_resid_lt_2km"
    if resid < 6.0:
        return 0.5, "fit_used_resid_2_6km"
    return 0.4, "fit_used_resid_ge_6km"


def blend_motion(obs, nwp, fit_used, resid_std_km):
    """Fusione vettoriale w*obs + (1-w)*nwp.

    ``obs`` e ``nwp`` sono dict con speed_kmh/direction_toward_deg.
    Ritorna (motion_blend, motion_weights, motion_blend_components).
    """
    w, rule = weight_for_fit(fit_used, resid_std_km)
    u_o, v_o = _uv_from_speed_dir(float(obs["speed_kmh"]),
                                  float(obs["direction_toward_deg"]))
    u_n, v_n = _uv_from_speed_dir(float(nwp["speed_kmh"]),
                                  float(nwp["direction_toward_deg"]))
    u = w * u_o + (1.0 - w) * u_n
    v = w * v_o + (1.0 - w) * v_n
    speed, direction = _speed_dir_from_uv(u, v)
    blend = {
        "speed_kmh": round(speed, 2),
        "direction_toward_deg": round(direction, 1),
        "u_kmh": round(u, 2),
        "v_kmh": round(v, 2),
    }
    weights = {
        "obs": round(w, 2),
        "nwp": round(1.0 - w, 2),
        "rule": rule,
        "fit_used": bool(fit_used),
        "resid_std_km": (round(float(resid_std_km), 2)
                         if resid_std_km is not None else None),
        "note": ("regola DICHIARATA, non calibrata: 0.8 residuo<2 km, "
                 "0.5 2-6 km, 0.4 >=6 km, 0.3 fit non usato"),
    }
    components = {
        "obs_speed_kmh": round(float(obs["speed_kmh"]), 2),
        "obs_direction_toward_deg": round(
            float(obs["direction_toward_deg"]), 1),
        "nwp_speed_kmh": round(float(nwp["speed_kmh"]), 2),
        "nwp_direction_toward_deg": round(
            float(nwp["direction_toward_deg"]), 1),
    }
    return blend, weights, components


def divergence(obs, nwp):
    """Divergenza osservato-NWP: (|dspeed| km/h, |ddir| deg)."""
    dspeed = abs(float(obs["speed_kmh"]) - float(nwp["speed_kmh"]))
    ddir = abs(_wrap180(float(obs["direction_toward_deg"])
                        - float(nwp["direction_toward_deg"])))
    return dspeed, ddir


# ---------------------------------------------------------------------------
# Ensemble di traiettorie
# ---------------------------------------------------------------------------

def _seed_for(structure_id, anchor_ms):
    key = "%s|%d" % (structure_id or "", int(anchor_ms or 0))
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def _kde_bandwidth_km(values_km, n):
    """Bandwidth di Silverman (2D, per asse): 0.9 * min(std, IQR/1.34) * n^-0.2."""
    v = np.asarray(values_km, dtype=float)
    std = float(np.std(v))
    q75, q25 = np.percentile(v, [75, 25])
    iqr = (q75 - q25) / 1.34
    sigma = min(std, iqr) if iqr > 0.0 else std
    if not np.isfinite(sigma) or sigma <= 0.0:
        sigma = 1.0
    return max(KDE_MIN_BW_KM, 0.9 * sigma * (max(n, 2) ** -0.2))


def _density_polygons(lons, lats, levels=DENSITY_LEVELS, grid_deg=GRID_DEG):
    """KDE gaussiano 2D -> isolinee ai livelli di massa.

    La densita' e' stimata con un kernel gaussiano per punto (bandwidth di
    Silverman per asse, in km) valutato su una griglia locale. Il KDE evita
    le isolinee frammentate di un istogramma con 200 campioni sparsi.
    Ritorna {level: {"polygon": [ring,...], "area_km2": x, "max_extent_km": y}}
    """
    lons = np.asarray(lons, dtype=float)
    lats = np.asarray(lats, dtype=float)
    n = int(lons.size)
    if n < 8:
        return {}
    lat_ref = float(lats.mean())
    kx = KM_PER_DEG_LAT * math.cos(math.radians(lat_ref))
    ky = KM_PER_DEG_LAT
    hx = _kde_bandwidth_km((lons - lons.mean()) * kx, n)
    hy = _kde_bandwidth_km((lats - lats.mean()) * ky, n)
    # griglia: passo max(GRID_DEG, h/2), bordo a 3 bandwidth
    step = max(grid_deg, min(hx, hy) / 2.0 / KM_PER_DEG_LAT)
    lon0, lon1 = float(lons.min()) - 3.0 * hx / kx, float(lons.max()) + 3.0 * hx / kx
    lat0, lat1 = float(lats.min()) - 3.0 * hy / ky, float(lats.max()) + 3.0 * hy / ky
    # tetto di sicurezza sulla spaziatura (griglia massima ~200x200)
    span = max(lon1 - lon0, lat1 - lat0)
    if span / step > 200.0:
        step = span / 200.0
    nx = max(8, int(math.ceil((lon1 - lon0) / step)) + 1)
    ny = max(8, int(math.ceil((lat1 - lat0) / step)) + 1)
    xs = lon0 + np.arange(nx) * step
    ys = lat0 + np.arange(ny) * step

    # KDE diretta: (n_punti, ny, nx) -> somma sull'asse dei punti
    dx = ((lons[None, :] - xs[:, None]) * kx) / hx      # (nx, n)
    dy = ((lats[None, :] - ys[:, None]) * ky) / hy      # (ny, n)
    kernel = np.exp(-0.5 * (dy[:, None, :] ** 2 + dx[None, :, :] ** 2))
    density = kernel.sum(axis=2) / (2.0 * math.pi * hx * hy)  # (ny, nx)
    cell_area = (step * kx) * (step * ky)
    total = float(density.sum() * cell_area)
    if total <= 0.0:
        return {}
    flat_density = density.ravel()
    flat_mass = flat_density * cell_area
    order = np.argsort(flat_density)[::-1]
    cum = np.cumsum(flat_mass[order]) / total

    out = {}
    for frac in levels:
        k = min(int(np.searchsorted(cum, frac)), order.size - 1)
        threshold = float(flat_density[order[k]])
        if threshold <= 0.0:
            continue
        segments = []
        above = density >= threshold
        cases = (above[:-1, :-1].astype(np.int8)
                 | (above[:-1, 1:].astype(np.int8) << 1)
                 | (above[1:, 1:].astype(np.int8) << 2)
                 | (above[1:, :-1].astype(np.int8) << 3))
        active = np.argwhere((cases > 0) & (cases < 15))
        for i, j in active:
            i = int(i)
            j = int(j)
            z00 = density[i, j]
            z10 = density[i, j + 1]
            z11 = density[i + 1, j + 1]
            z01 = density[i + 1, j]
            center_above = (0.25 * (z00 + z10 + z11 + z01)) >= threshold
            segments.extend(_segments_for_cell(
                z00, z10, z11, z01, threshold,
                xs[j], xs[j + 1], ys[i], ys[i + 1], center_above))
        rings = _link_rings(segments)
        if not rings:
            continue
        area = sum(_ring_area_km2(r) for r in rings)
        clon, clat = float(lons.mean()), float(lats.mean())
        ext = 0.0
        for r in rings:
            for p in r:
                ext = max(ext, haversine_km((clon, clat), (p[0], p[1])))
        out[frac] = {
            "polygon": rings,
            "rings": len(rings),
            "area_km2": round(area, 1),
            "max_extent_km": round(2.0 * ext, 1),
        }
    return out


def _segments_for_cell(z00, z10, z11, z01, level, x0, x1, y0, y1,
                       center_above):
    """Segmenti di isolinea in una cella (marching squares, 16 casi)."""
    case = ((1 if z00 >= level else 0) | (2 if z10 >= level else 0)
            | (4 if z11 >= level else 0) | (8 if z01 >= level else 0))
    if case in (0, 15):
        return []

    def bottom():
        t = (level - z00) / (z10 - z00)
        return (x0 + t * (x1 - x0), y0)

    def right():
        t = (level - z10) / (z11 - z10)
        return (x1, y0 + t * (y1 - y0))

    def top():
        t = (level - z01) / (z11 - z01)
        return (x0 + t * (x1 - x0), y1)

    def left():
        t = (level - z00) / (z01 - z00)
        return (x0, y0 + t * (y1 - y0))

    table = {
        1: [("L", "B")],
        2: [("B", "R")],
        3: [("L", "R")],
        4: [("R", "T")],
        6: [("B", "T")],
        7: [("L", "T")],
        8: [("T", "L")],
        9: [("T", "B")],
        11: [("T", "R")],
        12: [("L", "R")],
        13: [("B", "R")],
        14: [("L", "B")],
    }
    if case == 5:
        pairs = ([("L", "T"), ("B", "R")] if center_above
                 else [("L", "B"), ("R", "T")])
    elif case == 10:
        pairs = ([("L", "B"), ("R", "T")] if center_above
                 else [("L", "T"), ("B", "R")])
    else:
        pairs = table[case]
    pts = {"B": bottom, "R": right, "T": top, "L": left}
    return [(pts[a](), pts[b]()) for a, b in pairs]


def _link_rings(segments, tol=1e-6):
    """Concatena segmenti in anelli chiusi; scarta le catene aperte."""
    if not segments:
        return []

    def key(p):
        return (round(p[0] / tol) * tol, round(p[1] / tol) * tol)

    adj = {}
    for si, (a, b) in enumerate(segments):
        ka, kb = key(a), key(b)
        adj.setdefault(ka, []).append((si, b, kb))
        adj.setdefault(kb, []).append((si, a, ka))
    used = [False] * len(segments)
    rings = []
    for start in list(adj.keys()):
        if all(used[si] for si, _p, _k in adj[start]):
            continue
        ring = [start]
        cur = start
        while True:
            nxt = None
            for si, p, k in adj.get(cur, ()):
                if not used[si]:
                    used[si] = True
                    nxt = (p, k)
                    break
            if nxt is None:
                break
            cur = nxt[1]
            ring.append(cur)
            if cur == start:
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append([[round(p[0], 5), round(p[1], 5)] for p in ring])
    return rings


def _ring_area_km2(ring):
    """Area sferica approssimata di un anello chiuso (shoelace locale)."""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring[:-1]) / max(len(ring) - 1, 1)
    kx = KM_PER_DEG_LAT * math.cos(math.radians(lat0))
    ky = KM_PER_DEG_LAT
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring[:-1], ring[1:]):
        area += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(area) * 0.5


def build_ensemble(motion_blend, sigma_speed_kmh, sigma_dir_deg,
                   start_lonlat, anchor_ms, structure_id=None,
                   offsets=ENSEMBLE_OFFSETS_MIN, n=N_MEMBERS):
    """Ensemble di ``n`` traiettorie con perturbazione gaussiana antithetica.

    Velocita': v_i = max(1, v + sigma_speed * z1)
    Direzione: dir_i = dir + sigma_dir * z2
    z1/z2 ~ N(0,1), coppie (z, -z) -> il centroide dell'ensemble coincide con
    il punto blend (nessuna deriva introdotta dal campionamento).

    Ritorna (ensemble, ensemble_basis). Le bande sono frazioni di MASSA
    dell'ensemble, non probabilita' calibrate.
    """
    speed = float(motion_blend["speed_kmh"])
    direction = float(motion_blend["direction_toward_deg"])
    rng = np.random.default_rng(_seed_for(structure_id, anchor_ms))
    half = n // 2
    z1 = rng.standard_normal(half)
    z2 = rng.standard_normal(half)
    z1 = np.concatenate([z1, -z1])
    z2 = np.concatenate([z2, -z2])
    speeds = np.maximum(1.0, speed + sigma_speed_kmh * z1)
    directions = direction + sigma_dir_deg * z2

    steps = []
    for offset in offsets:
        lons = np.empty(n)
        lats = np.empty(n)
        dist = speeds * (offset / 60.0)
        for i in range(n):
            lon, lat = dest_from(start_lonlat, float(directions[i]),
                                 float(dist[i]))
            lons[i] = lon
            lats[i] = lat
        levels = _density_polygons(lons, lats)
        step = {
            "offset_min": int(offset),
            "at": _iso_from_ms(anchor_ms + offset * 60000),
            "n": int(n),
            "centroid": [round(float(lons.mean()), 5),
                         round(float(lats.mean()), 5)],
            "density_levels": [
                {
                    "level": frac,
                    "polygon": levels[frac]["polygon"],
                    "rings": levels[frac]["rings"],
                    "area_km2": levels[frac]["area_km2"],
                    "max_extent_km": levels[frac]["max_extent_km"],
                }
                for frac in DENSITY_LEVELS if frac in levels
            ],
        }
        steps.append(step)
    ensemble = {
        "method": ENSEMBLE_METHOD,
        "n": int(n),
        "steps": steps,
    }
    basis = {
        "n": int(n),
        "distribution": "gaussian_antithetic_speed_direction",
        "sigma_speed_kmh": round(sigma_speed_kmh, 2),
        "sigma_dir_deg": round(sigma_dir_deg, 2),
        "sigma_source": {
            "tracker_history": {
                "speed_kmh": TRACKER_SIGMA_SPEED_KMH,
                "dir_deg": TRACKER_SIGMA_DIR_DEG,
                "source": ("misurato sui pair storici del validation script "
                           "(errore RMS dell'estrapolazione pura a 60 min)"),
            },
            "divergence_obs_nwp": "vedi sigma_components",
        },
        "sigma_components": {},
        "grid_deg": GRID_DEG,
        "kde_bandwidth": "Silverman per asse (0.9*min(std,IQR/1.34)*n^-0.2), minimo %g km" % KDE_MIN_BW_KM,
        "density_levels": list(DENSITY_LEVELS),
        "density_meaning": ("frazione di massa dell'ensemble dentro il "
                            "poligono (25/50/75/90%), NON probabilita'"),
        "probability_calibrated": False,
        "probability_null_reason": ("nessun dataset di verifiche etichettate "
                                    "per questa grandezza: le bande misurano "
                                    "la dispersione del moto, non la "
                                    "frequenza osservata degli eventi"),
        "calibration_reference": dict(VALIDATION_REFERENCE),
        "seed": _seed_for(structure_id, anchor_ms),
    }
    return ensemble, basis


# ---------------------------------------------------------------------------
# Blocco completo per un candidato
# ---------------------------------------------------------------------------

def build_motion_block(candidate_ctx, bundle, fit, anchor_ms,
                       dataset=None, obs=None):
    """Costruisce i campi NWP/blend/ensemble per un record di struttura.

    ``candidate_ctx``: dict del record (position, structure_id, ...).
    ``fit``: risultato di forecast.fit_motion.
    ``anchor_ms``: ancora temporale del run (radar_timestamp).

    Ritorna un dict con le chiavi:
      nwp_motion, nwp_motion_null_reason, nwp_motion_detail,
      motion_blend, motion_weights, motion_blend_components,
      ensemble, ensemble_basis, ensemble_null_reason.

    Regola: l'ensemble esiste se esiste un ``motion_blend`` (osservato e/o
    NWP). Se manca il NWP ma il fit e' valido, l'ensemble si costruisce SOLO
    sull'osservato (dichiarato in ensemble_basis.sigma_source) e
    ``motion_weights.nwp`` vale 0.
    """
    out = {
        "nwp_motion": None,
        "nwp_motion_null_reason": None,
        "nwp_motion_detail": None,
        "motion_blend": None,
        "motion_weights": None,
        "motion_blend_components": None,
        "ensemble": None,
        "ensemble_basis": None,
        "ensemble_null_reason": None,
    }
    pos = candidate_ctx.get("position") if isinstance(candidate_ctx, dict) else None
    if not pos or len(pos) != 2:
        out["nwp_motion_null_reason"] = "position_missing"
        out["ensemble_null_reason"] = "position_missing"
        return out
    if anchor_ms is None:
        out["nwp_motion_null_reason"] = "valid_time_missing"
        out["ensemble_null_reason"] = "valid_time_missing"
        return out

    if dataset is None:
        dataset = load_points()
    steering = steering_for_position(dataset, float(pos[0]), float(pos[1]),
                                     anchor_ms)
    out["nwp_motion_detail"] = {k: v for k, v in steering.items()
                                if k not in ("value", "null_reason")}
    if steering.get("value") is None:
        out["nwp_motion_null_reason"] = steering.get("null_reason")
    else:
        out["nwp_motion"] = steering["value"]

    if obs is None:
        obs = {
            "speed_kmh": float(fit.get("velocity_kmh") or 0.0),
            "direction_toward_deg": float(
                fit.get("direction_toward_deg") or 0.0),
            "available": bool(float(fit.get("velocity_kmh") or 0.0) > 0.0),
        }

    nwp_value = steering.get("value")
    if nwp_value is not None and obs.get("available"):
        blend, weights, components = blend_motion(
            obs, nwp_value, fit.get("fit_used"), fit.get("resid_std_km"))
        dspeed, ddir = divergence(obs, nwp_value)
    elif nwp_value is not None:
        blend, weights, components = blend_motion(
            nwp_value, nwp_value, False, fit.get("resid_std_km"))
        weights = dict(weights)
        weights.update({"obs": 0.0, "nwp": 1.0, "rule": "obs_unavailable"})
        dspeed, ddir = 0.0, 0.0
    elif obs.get("available"):
        blend, weights, components = blend_motion(
            obs, obs, True, fit.get("resid_std_km"))
        weights = dict(weights)
        weights.update({"obs": 1.0, "nwp": 0.0, "rule": "nwp_unavailable"})
        dspeed, ddir = 0.0, 0.0
    else:
        out["ensemble_null_reason"] = "no_motion_source"
        return out

    out["motion_blend"] = blend
    out["motion_weights"] = weights
    out["motion_blend_components"] = components

    sigma_speed = math.sqrt(dspeed ** 2 + TRACKER_SIGMA_SPEED_KMH ** 2)
    sigma_dir = math.sqrt(ddir ** 2 + TRACKER_SIGMA_DIR_DEG ** 2)
    sigma_speed = min(max(sigma_speed, SIGMA_SPEED_FLOOR_KMH),
                      SIGMA_SPEED_CAP_KMH)
    sigma_dir = min(max(sigma_dir, SIGMA_DIR_FLOOR_DEG), SIGMA_DIR_CAP_DEG)
    ensemble, basis = build_ensemble(
        blend, sigma_speed, sigma_dir, (float(pos[0]), float(pos[1])),
        anchor_ms, structure_id=candidate_ctx.get("structure_id"))
    basis["sigma_components"] = {
        "divergence_speed_kmh": round(dspeed, 2),
        "divergence_dir_deg": round(ddir, 2),
        "tracker_speed_kmh": TRACKER_SIGMA_SPEED_KMH,
        "tracker_dir_deg": TRACKER_SIGMA_DIR_DEG,
        "combined_speed_kmh": round(sigma_speed, 2),
        "combined_dir_deg": round(sigma_dir, 2),
        "formula": "sqrt(divergence^2 + tracker^2), floor/cap dichiarati",
    }
    basis["motion_weights"] = weights
    out["ensemble"] = ensemble
    out["ensemble_basis"] = basis
    return out


def attach_motion_fields(records, bundle, anchor_ms=None, dataset=None):
    """Aggiunge i campi a livello RECORD (per il caso cono assente).

    Pensata per essere chiamata dal chiamante che ha gia' i record e le track
    (una riga in convective_structure.evaluate, NON applicata qui perche' il
    file e' di un altro agente). Ricalcola il fit dai punti della track.

    Ritorna il numero di record con ``ensemble`` non null.
    """
    from . import forecast as forecast_mod
    if dataset is None:
        dataset = load_points()
    if anchor_ms is None:
        anchor_ms = getattr(bundle, "radar_timestamp_ms", None)
        if anchor_ms is None:
            anchor_ms = forecast_mod._ms_from_iso(
                getattr(bundle, "radar_timestamp_iso", None))
    count = 0
    for rec in records or []:
        track = forecast_mod._resolve_track(rec, bundle)
        if track is None:
            continue
        points = sorted(track.points, key=lambda p: p.timestamp_ms)
        fit = forecast_mod.fit_motion(points)
        block = build_motion_block(rec, bundle, fit, anchor_ms,
                                   dataset=dataset)
        rec.update(block)
        if block.get("ensemble") is not None:
            count += 1
    return count


# ---------------------------------------------------------------------------
# CLI minima (diagnostica, non usata dal motore)
# ---------------------------------------------------------------------------

def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="NWP steering + ensemble (diagnostica)")
    ap.add_argument("--points", default=None)
    ap.add_argument("--model", default="best_match")
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--time", required=True, help="ISO UTC dell'istante")
    ap.add_argument("--speed", type=float, default=30.0)
    ap.add_argument("--direction", type=float, default=90.0)
    ap.add_argument("--fit-used", action="store_true")
    ap.add_argument("--resid", type=float, default=3.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    ds = load_points(args.points, model=args.model)
    if ds is None:
        print(json.dumps(load_stats(), indent=1))
        return 2
    ms = int(_parse_iso_utc(args.time) * 1000.0)
    st = steering_for_position(ds, args.lon, args.lat, ms)
    rec = {"position": [args.lon, args.lat], "structure_id": "CLI"}
    fit = {"velocity_kmh": args.speed,
           "direction_toward_deg": args.direction,
           "fit_used": bool(args.fit_used),
           "resid_std_km": args.resid}
    block = build_motion_block(rec, None, fit, ms, dataset=ds)
    out = {"load": load_stats(), "steering": st, "block": block}
    text = json.dumps(out, indent=1, ensure_ascii=False)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(text)
    print(text[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
