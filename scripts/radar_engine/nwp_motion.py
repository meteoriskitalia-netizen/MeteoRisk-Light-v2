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
  * costruisce un ENSEMBLE di 200 traiettorie come MISTURA di ipotesi
    indipendenti sul moto (2026-11-11): ``observed`` (fit del tracker),
    ``nwp_mean_0_6``, ``nwp_500``, ``nwp_700``, ``bunkers_rm``. Ogni ipotesi
    ha un peso MISURATO (invarianza inversa sull'errore a 60 min) e una
    dispersione propria (errore residuo = sqrt(errore^2 - disaccordo^2)); i
    membri si distribuiscono fra le ipotesi in proporzione al peso (minimo 2
    per ipotesi attiva). Pubblica 24 percorsi (``ensemble_members``), la
    traiettoria media (``ensemble_mean``), la larghezza del fascio a 60/120
    min (``ensemble_spread_km``) e la COMPOSIZIONE (``ensemble_hypotheses``,
    ``ensemble_member_hypothesis``). Le isolinee KDE sono state rimosse nella
    1.2.4.81: il client non le ha mai disegnate e pesavano ~190 KB su 4
    strutture.

PERCHE' LA MISTURA (2026-11-11). Fino alla 1.2.4.82 l'ensemble perturbava
UNA sola ipotesi con una gaussiana isotropa su velocita' e direzione: la
densita' era un ELLISSE per costruzione. Se le ipotesi indipendenti sono
piu' d'una la forma deve nascere dal loro DISACCORDO. Il peso non e' scelto
a mano: ``w_i = (1/e_i^2)`` normalizzato, con ``e_i`` l'errore RMS a 60 min
misurato sui track reali (``HYPOTHESIS_ERROR_KM_60``, riproducibile con
``scripts/validation/motion_mixture_bench.py``). Un'ipotesi senza misura NON
entra nella mistura: peso 0 e motivo in ``ensemble_hypotheses``.

ONESTA'. Il fascio NON e' una probabilita' calibrata: non esiste un dataset
di verifiche etichettate per questa grandezza. ``probability_calibrated`` vale
sempre ``false`` con motivo. La densita' delle linee e' la densita' della
MISTURA (200 membri da 5 ipotesi con peso misurato), non una frequenza
osservata. La forma e' verificabile dai numeri pubblicati in
``ensemble_basis.shape_60min``: rapporto area 50/90, asimmetria picco-vs-banda
e numero di massimi locali della densita' a 60 min.

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
  ensemble                {n, method, steps: [{offset_min, at, n, centroid}]}
  ensemble_members        24 traiettorie [[lon,lat] x 5] (t=0/30/60/90/120)
  ensemble_mean           traiettoria del blend [[lon,lat] x 5]
  ensemble_spread_km      {"60": {along_std_km, cross_std_km, cross_p90_km},
                           "120": {...}} sui 200 membri della mistura
  ensemble_hypotheses     [{name, weight, n_members, n_members_published,
                           sigma_km_60, measured_error_km_60, active,
                           null_reason}] - composizione verificabile
  ensemble_member_hypothesis  24 stringhe allineate a ensemble_members
  ensemble_basis          distribuzione, pesi, residui, forma a 60 min,
                           provenienza, N, seed, selezione dei 24 membri
  ensemble_null_reason    motivo se l'ensemble non c'e'

WIRING. `forecast.build_motion` e `forecast.build_motion_block` (dentro
`_build`) chiamano `build_motion_block`: i campi arrivano a livello
RECORD tramite `convective_structure._copy_motion` (chiavi in
`_MOTION_FIELDS`) oppure restano nel blocco `forecast`.
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

N_MEMBERS = 200                    # traiettorie dell'ensemble (monte Carlo)
ENSEMBLE_OFFSETS_MIN = (30, 60, 90, 120)
# 1.2.4.81: il client disegna un FASCIO di traiettorie, non piu' le isolinee
# di densita' (poligoni mai usati dal client, ~190 KB su 4 strutture).
# Si pubblicano 24 membri scelti ai quantili della densita' (rango della
# distanza di Mahalanobis dal blend), non 24 a caso: il fascio copre la
# dispersione reale, dal nucleo alla coda.
MEMBERS_PUBLISHED = 24
MEMBER_DECIMALS = 3                # ~100 m: compatto senza perdere la forma
SPREAD_OFFSETS_MIN = (60, 120)     # dove si misura la larghezza del fascio
KM_PER_DEG = 111.32                # conversione locale (stessa del client)

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
ENSEMBLE_METHOD = "mixture_hypotheses_members24"

# ---------------------------------------------------------------------------
# MISTURA DI IPOTESI DI MOTO (2026-11-11)
# ---------------------------------------------------------------------------
# PERCHE'. Fino alla 1.2.4.82 l'ensemble perturbava UNA sola ipotesi (il blend
# osservato-NWP) con una gaussiana isotropa su velocita' e direzione: la
# densita' risultante era un ELLISSE per COSTRUZIONE, non un'informazione
# fisica. Se le ipotesi indipendenti sul moto sono piu' d'una, la forma deve
# nascere dal loro DISACCORDO, non da una gaussiana imposta.
#
# Ogni ipotesi ha un centro (velocita'/direzione) e una dispersione misurata
# a 60 min sui track storici. I membri si distribuiscono fra le ipotesi in
# proporzione al peso; dentro ogni ipotesi si perturba con l'errore RESIDUO
# (quello che il disaccordo fra ipotesi non spiega), non con l'errore totale.
#
# La scomposizione usata e' elementare e dichiarata:
#     errore_totale_i^2 = disaccordo_i^2 + residuo_i^2
# dove disaccordo_i = |centro_i - centro_pesato|. Se il residuo scende sotto
# il floor, si usa il floor (altrimenti i membri di un'ipotesi coinciderebbero).
#
# Se una ipotesi non e' calcolabile per una struttura (NWP assente, profilo
# incompleto, shear nullo) viene DISATTIVATA con motivo e i pesi si
# RINORMALIZZANO sulle altre: `ensemble_hypotheses` pubblica anche le
# ipotesi spente, cosi' la composizione e' verificabile.

PROFILE_LEVELS_HPA = (1000, 975, 950, 925, 900, 850, 800, 700, 600, 500)
LAYER_BOTTOM_HPA = 1000
LAYER_TOP_HPA = 500
# Bunkers et al. (2000): deviazione 7.5 m/s sulla perpendicolare allo shear.
# Qui il modulo lavora in km/h: 7.5 m/s = 27 km/h.
BUNKERS_DEVIATION_KMH = 27.0
BUNKERS_MIN_SHEAR_KMH = 1.0        # shear sotto 1 km/h -> RM non definito

HYPOTHESIS_ORDER = ("observed", "nwp_mean_0_6", "nwp_500", "nwp_700",
                    "bunkers_rm")

HYPOTHESIS_LABELS = {
    "observed": "estrapolazione del fit cinematico del tracker",
    "nwp_mean_0_6": "vento medio pesato per massa 1000-500 hPa (proxy 0-6 km)",
    "nwp_500": "vento a 500 hPa",
    "nwp_700": "vento a 700 hPa",
    "bunkers_rm": "Bunkers right-moving (Bunkers et al. 2000)",
}

# Errore RMS a 60 min per ipotesi, km. RIEMPITO DALLA MISURA:
# scripts/validation/motion_mixture_bench.py sui track reali dell'archivio
# radar (radar-history) dentro la finestra del dataset NWP. `None` = ipotesi
# non misurabile su questo campione -> NON usata (dichiarato).
HYPOTHESIS_ERROR_KM_60 = {
    "observed": 39.85,
    "nwp_mean_0_6": 39.77,
    "nwp_500": 49.42,
    "nwp_700": 44.94,
    "bunkers_rm": 43.93,
}
# Errore scomposto (RMS lungo e trasverso al moto vero), km a 60 min.
HYPOTHESIS_ERROR_ALONG_KM_60 = {
    "observed": 34.94,
    "nwp_mean_0_6": 34.77,
    "nwp_500": 39.45,
    "nwp_700": 37.25,
    "bunkers_rm": 32.71,
}
HYPOTHESIS_ERROR_CROSS_KM_60 = {
    "observed": 19.18,
    "nwp_mean_0_6": 19.30,
    "nwp_500": 29.76,
    "nwp_700": 25.14,
    "bunkers_rm": 29.33,
}
HYPOTHESIS_MEASUREMENT = {
    "measured_on": "2026-11-11",
    "cases_60min": 82,
    "source": ("radar-history (branch dati: slot 17:35-21:55Z del 2026-11-11 "
               "con uid persistente + celle 19:40-21:45Z) incrociati con "
               "data/latest/meteorisk-points.json (Open-Meteo, "
               "time_base 2026-11-10T23:00Z, 72 h)"),
    "method": ("scripts/validation/motion_mixture_bench.py: fit_motion sui "
               "soli punti passati, 5 ipotesi alla posizione/istante "
               "dell'origine, confronto con la posizione osservata a +60 min "
               "(tolleranza 6 min); RMS per ipotesi sullo STESSO campione "
               "(n=82 casi in cui tutte e 5 le ipotesi sono calcolabili)"),
    "caveats": ("campione di UNA giornata (un pomeriggio) su 82 casi, non una "
                "climatologia; l'archivio Open-Meteo non fornisce i livelli "
                "di pressione (0/24 valori), quindi non esiste NWP storico "
                "fuori dalla finestra del dataset operativo"),
    "note": ("pesi = 1/errore^2 normalizzati (invarianza inversa); "
             "nessun peso scelto a mano"),
}
HYPOTHESIS_ERROR_FLOOR_KM = 1.0    # errore 0 -> peso finito, mai infinito
MIN_MEMBERS_PER_HYPOTHESIS = 2
RESIDUAL_FLOOR_KM_60 = 2.0         # residuo minimo dentro una ipotesi

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
                 wind, geo, model, n_hours, load_stats=None, profile=None):
        self.path = path
        self.generated_at = generated_at
        self.time_base_utc = time_base_utc
        self.lats = np.asarray(lats, dtype=float)
        self.lons = np.asarray(lons, dtype=float)
        self.wind = wind                  # livello -> (u (n,h), v (n,h))
        self.geo = geo                    # livello -> (n,h) geopotenziale
        # Profilo completo 1000..500 hPa (2026-11-11): serve alla mistura di
        # ipotesi (vento medio 0-6 km, 500/700 hPa, Bunkers). `wind` resta la
        # vista 850/700/600/500 usata dallo steering storico.
        self.profile = profile if profile is not None else dict(wind)
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
    profile = {}
    for lvl in PROFILE_LEVELS_HPA:
        profile[lvl] = (np.full((len(points), n_hours), np.nan),
                        np.full((len(points), n_hours), np.nan))
    wind = {lvl: profile[lvl] for lvl, _w in LEVEL_MASS_WEIGHTS
            if lvl in profile}
    for lvl, _w in LEVEL_MASS_WEIGHTS:
        if lvl not in wind:
            wind[lvl] = (np.full((len(points), n_hours), np.nan),
                         np.full((len(points), n_hours), np.nan))
    geo = {lvl: np.full((len(points), n_hours), np.nan)
           for lvl in (850, 700, 500)}
    for i, p in enumerate(points):
        lats.append(float(p.get("lat")))
        lons.append(float(p.get("lon")))
        hourly = ((p.get("models") or {}).get(model) or {}).get("hourly") or {}
        for lvl in PROFILE_LEVELS_HPA:
            spd = hourly.get("wind_speed_%dhPa" % lvl)
            drc = hourly.get("wind_direction_%dhPa" % lvl)
            if not isinstance(spd, list) or not isinstance(drc, list):
                continue
            n = min(len(spd), len(drc), n_hours)
            u_arr, v_arr = profile[lvl]
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
                      geo, model, n_hours, dict(_LAST_LOAD_STATS),
                      profile=profile)
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
# Ipotesi indipendenti sul moto (mistura, 2026-11-11)
# ---------------------------------------------------------------------------

def _level_wind(dataset, near, idx_h, level):
    """Vento IDW al livello (u, v) in km/h, oppure None.

    Stessa formula di ``steering_for_position`` (1/(d^2+1)) ma su un solo
    livello: serve alle ipotesi separate (500, 700) e alla media di strato.
    """
    entry = getattr(dataset, "profile", dataset.wind).get(level)
    if entry is None:
        return None
    u_arr, v_arr = entry
    u_acc = 0.0
    v_acc = 0.0
    w_sum = 0.0
    for idx, dist in near:
        u = u_arr[idx, idx_h]
        v = v_arr[idx, idx_h]
        if not np.isfinite(u) or not np.isfinite(v):
            continue
        w = 1.0 / (dist * dist + IDW_EPS_KM2)
        u_acc += w * u
        v_acc += w * v
        w_sum += w
    if w_sum <= 0.0:
        return None
    return u_acc / w_sum, v_acc / w_sum


def layer_mean_wind(dataset, near, idx_h, bottom_hpa=LAYER_BOTTOM_HPA,
                    top_hpa=LAYER_TOP_HPA):
    """Vento medio pesato per MASSA fra due pressioni (trapezio su dp).

    ``w_layer = p_k - p_{k+1}`` (proporzionale alla massa dello strato) e il
    vento dello strato e' la media dei due livelli: e' la stessa formula
    dell'integrale di massa, applicata ai livelli DICHIARATI dal dataset.
    Ritorna (u, v, levels_used) oppure (None, None, []).
    """
    levels = [lvl for lvl in PROFILE_LEVELS_HPA
              if bottom_hpa >= lvl >= top_hpa]
    levels = sorted(levels, reverse=True)          # 1000 -> 500
    winds = {}
    for lvl in levels:
        w = _level_wind(dataset, near, idx_h, lvl)
        if w is not None:
            winds[lvl] = w
    if len(winds) < 2:
        return None, None, []
    used = sorted(winds.keys(), reverse=True)
    u_acc = 0.0
    v_acc = 0.0
    w_acc = 0.0
    for lo, hi in zip(used[:-1], used[1:]):
        dp = float(lo - hi)
        if dp <= 0.0:
            continue
        u_layer = 0.5 * (winds[lo][0] + winds[hi][0])
        v_layer = 0.5 * (winds[lo][1] + winds[hi][1])
        u_acc += dp * u_layer
        v_acc += dp * v_layer
        w_acc += dp
    if w_acc <= 0.0:
        return None, None, []
    return u_acc / w_acc, v_acc / w_acc, used


def bunkers_right_mover(mean_u, mean_v, shear_u, shear_v,
                        deviation_kmh=BUNKERS_DEVIATION_KMH,
                        min_shear_kmh=BUNKERS_MIN_SHEAR_KMH):
    """Bunkers right-moving (Bunkers et al. 2000, WAF 15:61-66).

    RM = V_medio + D * (shear ruotato di 90 gradi VERSO DESTRA)/|shear|,
    D = 7.5 m/s = 27 km/h. Il vettore a destra di (su, sv) e' (sv, -su).
    Ritorna (u, v, None) oppure (None, None, motivo).
    """
    shear = math.hypot(shear_u, shear_v)
    if not math.isfinite(shear) or shear < float(min_shear_kmh):
        return None, None, "shear_below_%g_kmh" % float(min_shear_kmh)
    perp_u = shear_v / shear
    perp_v = -shear_u / shear
    return (mean_u + deviation_kmh * perp_u,
            mean_v + deviation_kmh * perp_v, None)


def hypothesis_motions(dataset, lon, lat, valid_ms, fit, obs=None,
                       bunkers=None):
    """Le ipotesi di moto calcolabili per una posizione e un istante.

    Ritorna una lista di dict, UNA PER OGNI voce di HYPOTHESIS_ORDER, con
    ``name``, ``speed_kmh``, ``direction_toward_deg``, ``source``,
    ``reference`` e ``null_reason`` (None se l'ipotesi e' calcolabile).
    Nessuna ipotesi inventata: se il dato manca, ``null_reason`` lo dichiara.
    """
    out = []

    def _add(name, u, v, source, reference, null_reason=None, extra=None):
        item = {
            "name": name,
            "label": HYPOTHESIS_LABELS.get(name, name),
            "speed_kmh": None,
            "direction_toward_deg": None,
            "u_kmh": None,
            "v_kmh": None,
            "source": source,
            "reference": reference,
            "null_reason": null_reason,
        }
        if u is not None and v is not None and null_reason is None:
            speed, direction = _speed_dir_from_uv(u, v)
            item.update({"speed_kmh": round(speed, 3),
                         "direction_toward_deg": round(direction, 2),
                         "u_kmh": round(u, 3), "v_kmh": round(v, 3)})
        if extra:
            item.update(extra)
        out.append(item)

    # 1. observed: fit cinematico del tracker (o l'osservato passato dal
    #    chiamante, che puo' gia' includere il gate di affidabilita').
    src_obs = obs or {}
    o_speed = src_obs.get("speed_kmh")
    if o_speed is None:
        o_speed = fit.get("velocity_kmh")
    o_dir = src_obs.get("direction_toward_deg")
    if o_dir is None:
        o_dir = fit.get("direction_toward_deg")
    o_ok = (o_speed is not None and float(o_speed) > 0.0
            and o_dir is not None)
    if o_ok:
        u_o, v_o = _uv_from_speed_dir(float(o_speed), float(o_dir))
        _add("observed", u_o, v_o,
             "fit cinematico sui centroidi della track (forecast.fit_motion)",
             "estrapolazione lineare pesata, TITAN/SCIT-style",
             extra={"fit_used": bool(fit.get("fit_used")),
                    "resid_std_km": (round(float(fit["resid_std_km"]), 2)
                                     if fit.get("resid_std_km") is not None
                                     else None)})
    else:
        _add("observed", None, None,
             "fit cinematico sui centroidi della track",
             "estrapolazione lineare pesata",
             null_reason="observed_motion_missing")

    # 2-4. NWP: media di strato e livelli singoli.
    near = []
    idx_h = None
    if dataset is not None and valid_ms is not None:
        near = dataset.nearest(lon, lat)
        idx_h, _off = dataset.hour_index(valid_ms / 1000.0)
        if idx_h is None:
            near = []
    if not near:
        reason = ("dataset_unavailable" if dataset is None
                  else "no_point_within_%dkm" % int(MAX_DIST_KM))
        for name in ("nwp_mean_0_6", "nwp_500", "nwp_700"):
            _add(name, None, None, "data/latest/meteorisk-points.json",
                 "Open-Meteo, profilo di pressione", null_reason=reason)
    else:
        u_m, v_m, used = layer_mean_wind(dataset, near, idx_h)
        if u_m is None:
            _add("nwp_mean_0_6", None, None,
                 "data/latest/meteorisk-points.json",
                 "media pesata per massa %d-%d hPa" % (LAYER_BOTTOM_HPA,
                                                       LAYER_TOP_HPA),
                 null_reason="profile_incomplete_%d_%dhPa"
                             % (LAYER_BOTTOM_HPA, LAYER_TOP_HPA))
        else:
            _add("nwp_mean_0_6", u_m, v_m,
                 "data/latest/meteorisk-points.json",
                 "media pesata per massa %d-%d hPa (livelli usati: %s)"
                 % (LAYER_BOTTOM_HPA, LAYER_TOP_HPA,
                    ",".join(str(x) for x in used)),
                 extra={"levels_used_hpa": used})
        for name, lvl in (("nwp_500", 500), ("nwp_700", 700)):
            w = _level_wind(dataset, near, idx_h, lvl)
            if w is None:
                _add(name, None, None, "data/latest/meteorisk-points.json",
                     "vento a %d hPa" % lvl,
                     null_reason="level_%dhPa_missing" % lvl)
            else:
                _add(name, w[0], w[1], "data/latest/meteorisk-points.json",
                     "vento a %d hPa, IDW %d punti" % (lvl, len(near)))

    # 5. Bunkers RM. Se il chiamante ha gia' il RM della struttura
    #    (severe_stack.bunkers) si usa QUELLO; altrimenti si deriva dal
    #    profilo del point dataset con la formula del paper.
    if bunkers and bunkers.get("u_kmh") is not None \
            and bunkers.get("v_kmh") is not None:
        _add("bunkers_rm", float(bunkers["u_kmh"]), float(bunkers["v_kmh"]),
             "severe_stack.bunkers (right mover della struttura)",
             "Bunkers et al. 2000, WAF 15:61-66",
             extra={"origin": "severe_stack"})
    elif near and idx_h is not None:
        u_m, v_m, used = layer_mean_wind(dataset, near, idx_h)
        low = _level_wind(dataset, near, idx_h, max(used) if used else 1000)
        high = _level_wind(dataset, near, idx_h, min(used) if used else 500)
        if u_m is None or low is None or high is None:
            _add("bunkers_rm", None, None,
                 "profilo meteorisk-points.json",
                 "Bunkers et al. 2000", null_reason="profile_incomplete")
        else:
            u_rm, v_rm, why = bunkers_right_mover(
                u_m, v_m, high[0] - low[0], high[1] - low[1])
            _add("bunkers_rm", u_rm, v_rm,
                 "profilo meteorisk-points.json",
                 "Bunkers et al. 2000, WAF 15:61-66; shear V(%d)-V(%d) hPa, "
                 "deviazione 7.5 m/s" % (min(used), max(used)),
                 null_reason=why, extra={"origin": "profile"})
    else:
        _add("bunkers_rm", None, None, "profilo meteorisk-points.json",
             "Bunkers et al. 2000", null_reason="profile_unavailable")
    return out


def hypothesis_weights(motions, fit_used=None, resid_std_km=None,
                       errors=None, reliability=None):
    """Pesi delle ipotesi ATTIVE, da errore misurato (invarianza inversa).

    ``w_i = (1/e_i^2) * r_i`` normalizzato, con ``e_i`` l'errore RMS a 60 min
    MISURATO (HYPOTHESIS_ERROR_KM_60) e ``r_i`` il fattore di affidabilita'
    DICHIARATO dell'ipotesi ``observed`` (la regola 0.8/0.5/0.4/0.3 gia' in
    uso). Le ipotesi senza misura NON entrano nella mistura: peso 0 e motivo.

    Ritorna (weights dict name->float, detail dict con la spiegazione).
    """
    err = dict(HYPOTHESIS_ERROR_KM_60 if errors is None else errors)
    if reliability is None:
        rel, rule = weight_for_fit(fit_used, resid_std_km)
        reliability = {"observed": rel}
    else:
        rule = "explicit"
    raw = {}
    inactive = {}
    for m in motions:
        name = m["name"]
        if m.get("null_reason"):
            inactive[name] = m["null_reason"]
            continue
        e = err.get(name)
        if e is None or not math.isfinite(float(e)) or float(e) <= 0.0:
            inactive[name] = "no_measured_error"
            continue
        e = max(float(e), HYPOTHESIS_ERROR_FLOOR_KM)
        w = 1.0 / (e * e)
        if name == "observed":
            w *= float(reliability.get("observed", 1.0))
        raw[name] = w
    total = sum(raw.values())
    weights = {k: (v / total if total > 0 else 0.0) for k, v in raw.items()}
    detail = {
        "rule": ("inverse_variance_1_over_error_sq, observed x fit "
                 "reliability (%s)" % rule),
        "errors_km_60": {k: err.get(k) for k in HYPOTHESIS_ORDER},
        "reliability": {k: float(v) for k, v in reliability.items()},
        "active": sorted(weights.keys()),
        "inactive": inactive,
        "renormalized": bool(inactive),
        "total_raw": round(total, 6),
    }
    return weights, detail


def hypothesis_residuals(motions, weights, errors_along=None,
                         errors_cross=None):
    """Dispersione RESIDUA dentro ogni ipotesi a 60 min (km).

    ``residuo^2 = errore_totale^2 - disaccordo^2``: il disaccordo e' la
    distanza del centro dell'ipotesi dal centro pesato della mistura a 60 min
    (entrambi a 60 min, quindi il confronto e' diretto). Sotto
    ``RESIDUAL_FLOOR_KM_60`` si usa il floor: un'ipotesi con errore pari al
    solo disaccordo non ha dispersione propria misurabile.
    """
    ea = dict(HYPOTHESIS_ERROR_ALONG_KM_60 if errors_along is None
              else errors_along)
    ec = dict(HYPOTHESIS_ERROR_CROSS_KM_60 if errors_cross is None
              else errors_cross)
    active = [m for m in motions
              if not m.get("null_reason") and weights.get(m["name"], 0.0) > 0]
    if not active:
        return {}
    # Centro pesato della mistura a 60 min. u/v sono in km/h: a 60 min lo
    # spostamento in km coincide numericamente con la velocita', quindi il
    # confronto e' diretto senza conversioni.
    cu = 0.0
    cv = 0.0
    for m in active:
        w = weights[m["name"]]
        cu += w * float(m["u_kmh"])
        cv += w * float(m["v_kmh"])
    out = {}
    for m in active:
        name = m["name"]
        du = float(m["u_kmh"]) - cu
        dv = float(m["v_kmh"]) - cv
        # u/v sono km/h: a 60 min lo spostamento coincide numericamente.
        brg = math.radians(float(m["direction_toward_deg"]))
        ax_u, ax_v = math.sin(brg), math.cos(brg)      # asse lungo
        cr_u, cr_v = ax_v, -ax_u                       # asse trasverso
        dis_along = du * ax_u + dv * ax_v
        dis_cross = du * cr_u + dv * cr_v
        disagreement = math.hypot(du, dv)
        e_tot = HYPOTHESIS_ERROR_KM_60.get(name)
        e_al = ea.get(name) if ea.get(name) is not None else e_tot
        e_cr = ec.get(name) if ec.get(name) is not None else e_tot
        al = None
        cr = None
        if e_al is not None:
            al = math.sqrt(max(float(e_al) ** 2 - dis_along ** 2,
                               RESIDUAL_FLOOR_KM_60 ** 2))
        if e_cr is not None:
            cr = math.sqrt(max(float(e_cr) ** 2 - dis_cross ** 2,
                               RESIDUAL_FLOOR_KM_60 ** 2))
        out[name] = {
            "disagreement_km_60": round(disagreement, 3),
            "disagreement_along_km_60": round(dis_along, 3),
            "disagreement_cross_km_60": round(dis_cross, 3),
            "sigma_along_km_60": round(al, 3) if al is not None else None,
            "sigma_cross_km_60": round(cr, 3) if cr is not None else None,
            "sigma_km_60": (round(math.sqrt(al * al + cr * cr), 3)
                            if al is not None and cr is not None else None),
            "error_km_60": e_tot,
        }
    return out


# ---------------------------------------------------------------------------
# Ensemble di traiettorie
# ---------------------------------------------------------------------------

def _seed_for(structure_id, anchor_ms):
    key = "%s|%d" % (structure_id or "", int(anchor_ms or 0))
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def _ring_area_km2(ring):
    """Area sferica approssimata di un anello chiuso (shoelace locale)."""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring[:-1]) / max(len(ring) - 1, 1)
    kx = KM_PER_DEG * math.cos(math.radians(lat0))
    ky = KM_PER_DEG
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring[:-1], ring[1:]):
        area += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(area) * 0.5


def _quantile_indices(values, k):
    """``k`` indici ai quantili uniformi del rango di ``values`` (deterministico)."""
    values = np.asarray(values, dtype=float)
    n = int(values.size)
    if n == 0 or k <= 0:
        return []
    order = np.argsort(values, kind="stable")
    out = []
    for j in range(int(k)):
        pos = int(round(((j + 0.5) / float(k)) * (n - 1)))
        pos = max(0, min(n - 1, pos))
        idx = int(order[pos])
        if idx not in out:
            out.append(idx)
    # Se i quantili cadono sullo stesso indice (k > n) completa in ordine.
    for idx in order.tolist():
        if len(out) >= min(k, n):
            break
        if idx not in out:
            out.append(int(idx))
    return out[:min(k, n)]


def allocate_members(weights, total, minimum=MIN_MEMBERS_PER_HYPOTHESIS):
    """Ripartizione intera dei membri in proporzione ai pesi.

    ``minimum`` membri per OGNI ipotesi con peso > 0 (il brief: minimo 2 per
    ipotesi attiva), poi il resto ai piu' grandi remainder. Se ``total`` non
    basta per il minimo, il minimo scende a 1 e si dichiara (nessuna
    allocazione impossibile).
    """
    items = [(k, float(v)) for k, v in weights.items() if float(v) > 0.0]
    if not items or total <= 0:
        return {}
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    minimum = max(int(minimum), 1)
    if total < minimum * len(items):
        minimum = max(1, total // len(items))
    alloc = {k: minimum for k, _ in items}
    rest = int(total) - minimum * len(items)
    if rest > 0:
        tot_w = sum(v for _, v in items)
        raw = {k: rest * (v / tot_w) for k, v in items}
        for k in raw:
            alloc[k] += int(math.floor(raw[k]))
        short = int(total) - sum(alloc.values())
        if short > 0:
            order = sorted(raw.keys(),
                           key=lambda k: (-(raw[k] - math.floor(raw[k])), k))
            for k in order[:short]:
                alloc[k] += 1
    return alloc


def _path(start_lonlat, speed_kmh, direction_deg, offsets=ENSEMBLE_OFFSETS_MIN):
    """Traiettoria [[lon,lat], ...] a t=0 + offsets, 3 decimali (~100 m)."""
    lon0, lat0 = float(start_lonlat[0]), float(start_lonlat[1])
    path = [[round(lon0, MEMBER_DECIMALS), round(lat0, MEMBER_DECIMALS)]]
    for offset in offsets:
        lon, lat = dest_from((lon0, lat0), float(direction_deg),
                             float(speed_kmh) * (float(offset) / 60.0))
        path.append([round(float(lon), MEMBER_DECIMALS),
                     round(float(lat), MEMBER_DECIMALS)])
    return path


def mixture_density_grid(hypotheses, offset_min=60.0, cell_km=0.5,
                         max_extent_km=300.0):
    """Densita' ANALITICA della mistura su una griglia locale in km.

    Ogni ipotesi e' una gaussiana 2D con centro ``velocita' * offset/60``
    (km) e covarianza diagonale negli assi lungo/trasverso, con sigma che
    cresce linearmente col tempo: ``sigma(offset) = sigma_km_60 * offset/60``.
    La densita' della mistura e' la somma pesata delle gaussiane.

    Serve a misurare la FORMA (area 50/90, asimmetria, multi-modalita') senza
    il rumore di campionamento dei 24 membri pubblicati: e' il modello che i
    membri rappresentano, non una sua stima.

    Ritorna dict con ``grid`` (2D), ``x0``, ``y0``, ``cell_km`` oppure None.
    """
    act = [h for h in hypotheses
           if not h.get("null_reason") and h.get("speed_kmh") is not None
           and float(h.get("weight") or 0.0) > 0.0]
    if not act:
        return None
    scale = float(offset_min) / 60.0
    centres = []
    sigmas = []
    weights = []
    for h in act:
        brg = math.radians(float(h["direction_toward_deg"]))
        d = float(h["speed_kmh"]) * scale
        centres.append((d * math.sin(brg), d * math.cos(brg)))
        sal = float(h.get("sigma_along_km_60") or 0.0) * scale
        scr = float(h.get("sigma_cross_km_60") or 0.0) * scale
        sigmas.append((max(sal, 0.5), max(scr, 0.5), brg))
        weights.append(float(h["weight"]))
    tot_w = sum(weights)
    weights = [w / tot_w for w in weights]

    xs = [c[0] for c in centres]
    ys = [c[1] for c in centres]
    max_sig = max(max(s[0], s[1]) for s in sigmas)
    pad = max(3.0 * max_sig, 10.0)
    x_lo = min(xs) - pad
    x_hi = max(xs) + pad
    y_lo = min(ys) - pad
    y_hi = max(ys) + pad
    # cap sull'estensione: la griglia non deve esplodere
    x_lo = max(x_lo, -max_extent_km)
    x_hi = min(x_hi, max_extent_km)
    y_lo = max(y_lo, -max_extent_km)
    y_hi = min(y_hi, max_extent_km)
    nx = max(int(round((x_hi - x_lo) / cell_km)) + 1, 3)
    ny = max(int(round((y_hi - y_lo) / cell_km)) + 1, 3)
    if nx * ny > 4_000_000:
        cell_km = cell_km * math.sqrt(nx * ny / 4_000_000.0)
        nx = max(int(round((x_hi - x_lo) / cell_km)) + 1, 3)
        ny = max(int(round((y_hi - y_lo) / cell_km)) + 1, 3)
    gx = x_lo + np.arange(nx) * cell_km
    gy = y_lo + np.arange(ny) * cell_km
    grid = np.zeros((ny, nx))
    for (cx, cy), (sal, scr, brg), w in zip(centres, sigmas, weights):
        dx = gx[None, :] - cx
        dy = gy[:, None] - cy
        ax = math.sin(brg)
        ay = math.cos(brg)
        along = dx * ax + dy * ay
        cross = dx * ay - dy * ax
        grid += w * np.exp(-0.5 * ((along / sal) ** 2
                                   + (cross / scr) ** 2)) / (
            2.0 * math.pi * sal * scr)
    return {"grid": grid, "x0": x_lo, "y0": y_lo, "cell_km": cell_km,
            "centres": centres, "weights": weights}


def density_shape_metrics(hypotheses, offset_min=60.0, cell_km=0.5):
    """Forma della densita' della mistura a ``offset_min`` (numeri, non stime).

    Ritorna:
      ``area_ratio_50_90``  area del level set 50% / area del 90% (una
                            gaussiana 2D isotropa vale ln2/ln10 = 0.301);
      ``peak_km``           posizione del massimo di densita' (km dal punto di
                            partenza, x est / y nord);
      ``band90_centre_km``  centroide della regione 90%;
      ``asymmetry_km``      distanza fra picco e centroide della banda 90%;
      ``n_modes``           massimi locali della densita';
      ``mass_fraction_off_axis`` quota di massa oltre 45 gradi dall'asse del
                            moto pesato (0.5 = isotropa).
    None se la mistura non e' costruibile.
    """
    from scipy import ndimage
    d = mixture_density_grid(hypotheses, offset_min=offset_min,
                             cell_km=cell_km)
    if d is None:
        return None
    grid = d["grid"]
    cell = d["cell_km"]
    total = float(grid.sum())
    if total <= 0.0:
        return None
    flat = np.sort(grid.ravel())[::-1]
    cum = np.cumsum(flat) / total
    a50 = int(np.searchsorted(cum, 0.50) + 1)
    a90 = int(np.searchsorted(cum, 0.90) + 1)
    iy, ix = np.unravel_index(int(np.argmax(grid)), grid.shape)
    peak = (d["x0"] + ix * cell, d["y0"] + iy * cell)
    thr90 = flat[max(a90 - 1, 0)]
    mask90 = grid >= thr90
    ys_idx, xs_idx = np.nonzero(mask90)
    cx = d["x0"] + float(xs_idx.mean()) * cell
    cy = d["y0"] + float(ys_idx.mean()) * cell
    mx = ndimage.maximum_filter(grid, size=3)
    peaks = (grid >= mx - 1e-15) & (grid > 0.05 * grid.max())
    _lab, n_modes = ndimage.label(peaks)
    return {
        "offset_min": float(offset_min),
        "cell_km": round(cell, 3),
        "area_ratio_50_90": round(a50 / float(a90), 4),
        "area_ratio_gaussian_reference": 0.3010,
        "peak_km": [round(peak[0], 2), round(peak[1], 2)],
        "band90_centre_km": [round(cx, 2), round(cy, 2)],
        "asymmetry_km": round(math.hypot(peak[0] - cx, peak[1] - cy), 3),
        "n_modes": int(n_modes),
    }


def _spread_km(lons, lats, center, direction_deg):
    """Std lungo/trasverso e p90 trasverso (km) dei membri rispetto al blend.

    ``lons``/``lats``: posizioni degli n membri a un offset. ``center``:
    posizione del blend. ``direction_deg``: direzione VERSO cui si muove il
    blend (0=N, 90=E). La scomposizione e' locale (piano tangente), la stessa
    usata dal client per l'orientamento del fascio.
    """
    lon0, lat0 = float(center[0]), float(center[1])
    kx = KM_PER_DEG * math.cos(math.radians(lat0))
    dx = (np.asarray(lons, dtype=float) - lon0) * kx
    dy = (np.asarray(lats, dtype=float) - lat0) * KM_PER_DEG
    brg = math.radians(float(direction_deg))
    u = math.sin(brg)      # est
    v = math.cos(brg)      # nord
    along = dx * u + dy * v
    cross = dx * v - dy * u
    return {
        "along_std_km": round(float(np.std(along)), 2),
        "cross_std_km": round(float(np.std(cross)), 2),
        "cross_p90_km": round(float(np.percentile(np.abs(cross), 90)), 2),
        "n": int(along.size),
    }


def _legacy_hypothesis(motion_blend, sigma_speed_kmh, sigma_dir_deg):
    """Ipotesi singola equivalente alla perturbazione storica (compatibilita').

    Il vecchio ensemble perturbava velocita' (km/h) e direzione (gradi) con
    due gaussiane indipendenti. Qui la stessa dispersione e' espressa a 60 min
    in km lungo/trasverso, che e' l'unita' della mistura:
        sigma_along_km_60 = sigma_speed_kmh * 1 h
        sigma_cross_km_60 = sin(sigma_dir) * velocita' * 1 h
    """
    speed = float(motion_blend["speed_kmh"])
    direction = float(motion_blend["direction_toward_deg"])
    along = abs(float(sigma_speed_kmh)) * 1.0
    cross = abs(math.sin(math.radians(min(abs(float(sigma_dir_deg)), 89.0)))) \
        * max(speed, 1.0) * 1.0
    return {
        "name": "observed",
        "label": HYPOTHESIS_LABELS["observed"],
        "speed_kmh": speed,
        "direction_toward_deg": direction,
        "weight": 1.0,
        "sigma_along_km_60": along,
        "sigma_cross_km_60": cross,
        "sigma_km_60": math.sqrt(along * along + cross * cross),
        "measured_error_km_60": None,
        "source": "motion_blend (singola ipotesi, compatibilita' 1.2.4.82)",
        "reference": "gaussiana antithetica su velocita'/direzione",
        "null_reason": None,
    }


def build_ensemble(motion_blend, sigma_speed_kmh, sigma_dir_deg,
                   start_lonlat, anchor_ms, structure_id=None,
                   offsets=ENSEMBLE_OFFSETS_MIN, n=N_MEMBERS,
                   hypotheses=None, residuals=None):
    """Ensemble di ``n`` traiettorie da una MISTURA di ipotesi + fascio (24).

    Se ``hypotheses`` e' None si usa la singola ipotesi storica
    (``_legacy_hypothesis``): la firma resta compatibile con la 1.2.4.82.

    Con ``hypotheses`` (lista di dict con ``name``, ``speed_kmh``,
    ``direction_toward_deg``, ``weight``, ``sigma_along_km_60``,
    ``sigma_cross_km_60``) i membri sono distribuiti fra le ipotesi in
    proporzione al peso (minimo ``MIN_MEMBERS_PER_HYPOTHESIS`` per ipotesi
    attiva) e, DENTRO ogni ipotesi, perturbati con l'errore residuo di quella
    ipotesi. La dispersione totale nasce quindi dal DISACCORDO fra ipotesi,
    non da una gaussiana imposta.

    Ogni membro porta l'ipotesi di appartenenza (``member_hypotheses``), cosi'
    la composizione del fascio e' verificabile.
    """
    if hypotheses is None:
        hypotheses = [_legacy_hypothesis(motion_blend, sigma_speed_kmh,
                                        sigma_dir_deg)]
    hyps = [h for h in hypotheses
            if not h.get("null_reason") and h.get("speed_kmh") is not None
            and float(h.get("weight") or 0.0) > 0.0]
    if not hyps:
        raise ValueError("nessuna ipotesi attiva: ensemble non costruibile")

    speed = float(motion_blend["speed_kmh"]) if motion_blend else \
        float(hyps[0]["speed_kmh"])
    direction = float(motion_blend["direction_toward_deg"]) if motion_blend \
        else float(hyps[0]["direction_toward_deg"])
    rng = np.random.default_rng(_seed_for(structure_id, anchor_ms))

    weights = {h["name"]: float(h["weight"]) for h in hyps}
    alloc = allocate_members(weights, int(n))
    # Ogni ipotesi: coppie antitetiche (z, -z) -> il centroide dell'ipotesi
    # resta sul suo centro, la perturbazione non introduce deriva.
    z_by_hyp = {}
    speeds = []
    directions = []
    member_hyp = []
    for h in hyps:
        name = h["name"]
        k = int(alloc.get(name, 0))
        if k <= 0:
            continue
        half = max(k // 2, 1)
        z1 = rng.standard_normal(half)
        z2 = rng.standard_normal(half)
        z1 = np.concatenate([z1, -z1])
        z2 = np.concatenate([z2, -z2])
        if k % 2 == 1:
            # k dispari: coppie antitetiche + UN membro al centro esatto
            # (z=0), cosi' il centroide dell'ipotesi resta sul suo centro e
            # il numero di membri e' esattamente k.
            z1 = np.concatenate([z1, [0.0]])
            z2 = np.concatenate([z2, [0.0]])
        z1 = z1[:k]
        z2 = z2[:k]
        z_by_hyp[name] = (z1, z2)
        dist60 = float(h["speed_kmh"]) * 1.0
        sal = float(h.get("sigma_along_km_60") or 0.0)
        scr = float(h.get("sigma_cross_km_60") or 0.0)
        for a, c in zip(z1, z2):
            new_dist = max(1.0, dist60 + sal * float(a))
            cross_off = scr * float(c)
            new_dir = (float(h["direction_toward_deg"])
                       + math.degrees(math.atan2(cross_off, new_dist))) % 360.0
            speeds.append(new_dist)          # km/h == km a 60 min
            directions.append(new_dir)
            member_hyp.append(name)

    n_members = len(speeds)
    lons_by_offset = {}
    lats_by_offset = {}
    steps = []
    for offset in offsets:
        lons = np.empty(n_members)
        lats = np.empty(n_members)
        for i in range(n_members):
            lon, lat = dest_from(start_lonlat, float(directions[i]),
                                 float(speeds[i]) * (float(offset) / 60.0))
            lons[i] = lon
            lats[i] = lat
        lons_by_offset[int(offset)] = lons
        lats_by_offset[int(offset)] = lats
        steps.append({
            "offset_min": int(offset),
            "at": _iso_from_ms(anchor_ms + offset * 60000),
            "n": int(n_members),
            "centroid": [round(float(lons.mean()), 5),
                         round(float(lats.mean()), 5)],
        })

    # --- fascio pubblicabile: stessa composizione della mistura -------------
    pub_alloc = allocate_members(weights, MEMBERS_PUBLISHED)
    members = []
    member_names = []
    member_meta = []
    for h in hyps:
        name = h["name"]
        k = int(pub_alloc.get(name, 0))
        if k <= 0 or name not in z_by_hyp:
            continue
        z1, z2 = z_by_hyp[name]
        # Indici dei membri di QUESTA ipotesi nell'array globale.
        idx_all = [i for i, nm_ in enumerate(member_hyp) if nm_ == name]
        r = np.hypot(z1, z2)
        picks = _quantile_indices(r, k)
        for rank, local in enumerate(picks):
            gi = idx_all[local]
            members.append(_path(start_lonlat, float(speeds[gi]),
                                 float(directions[gi]), offsets))
            member_names.append(name)
            member_meta.append({
                "index": len(members) - 1,
                "hypothesis": name,
                "rank_in_hypothesis": rank,
                "n_in_hypothesis": len(idx_all),
            })

    mean = _path(start_lonlat, speed, direction, offsets)

    spread = {}
    for offset in SPREAD_OFFSETS_MIN:
        if int(offset) not in lons_by_offset:
            continue
        center_lon, center_lat = dest_from(
            start_lonlat, direction, speed * (float(offset) / 60.0))
        spread[str(int(offset))] = _spread_km(
            lons_by_offset[int(offset)], lats_by_offset[int(offset)],
            (center_lon, center_lat), direction)

    hyp_published = []
    for h in hyps:
        name = h["name"]
        hyp_published.append({
            "name": name,
            "label": h.get("label") or HYPOTHESIS_LABELS.get(name, name),
            "weight": round(float(h["weight"]), 4),
            "n_members": int(alloc.get(name, 0)),
            "n_members_published": int(pub_alloc.get(name, 0)),
            "sigma_km_60": (round(float(h["sigma_km_60"]), 2)
                            if h.get("sigma_km_60") is not None else None),
            "sigma_along_km_60": (round(float(h["sigma_along_km_60"]), 2)
                                  if h.get("sigma_along_km_60") is not None
                                  else None),
            "sigma_cross_km_60": (round(float(h["sigma_cross_km_60"]), 2)
                                  if h.get("sigma_cross_km_60") is not None
                                  else None),
            "measured_error_km_60": h.get("measured_error_km_60"),
            "speed_kmh": round(float(h["speed_kmh"]), 2),
            "direction_toward_deg": round(
                float(h["direction_toward_deg"]), 1),
            "source": h.get("source"),
            "reference": h.get("reference"),
            "active": True,
        })
    for h in hypotheses:
        if h in hyps:
            continue
        hyp_published.append({
            "name": h.get("name"),
            "label": h.get("label") or HYPOTHESIS_LABELS.get(h.get("name")),
            "weight": 0.0,
            "n_members": 0,
            "n_members_published": 0,
            "sigma_km_60": None,
            "measured_error_km_60": h.get("measured_error_km_60"),
            "source": h.get("source"),
            "reference": h.get("reference"),
            "active": False,
            "null_reason": h.get("null_reason") or "not_active",
        })

    ensemble = {
        "method": ENSEMBLE_METHOD,
        "n": int(n_members),
        "steps": steps,
        "members": members,
        "member_hypotheses": member_names,
        "member_meta": member_meta,
        "mean": mean,
        "spread_km": spread,
        "hypotheses": hyp_published,
    }
    basis = {
        "n": int(n_members),
        "distribution": "mixture_of_hypotheses",
        "hypotheses": hyp_published,
        "hypotheses_requested": [h.get("name") for h in hypotheses],
        "renormalized": any(h.get("null_reason") for h in hypotheses),
        "sigma_speed_kmh": round(float(sigma_speed_kmh), 2)
        if sigma_speed_kmh is not None else None,
        "sigma_dir_deg": round(float(sigma_dir_deg), 2)
        if sigma_dir_deg is not None else None,
        "sigma_source": {
            "tracker_history": {
                "speed_kmh": TRACKER_SIGMA_SPEED_KMH,
                "dir_deg": TRACKER_SIGMA_DIR_DEG,
                "source": ("misurato sui pair storici del validation script "
                           "(errore RMS dell'estrapolazione pura a 60 min)"),
            },
            "hypothesis_measurement": dict(HYPOTHESIS_MEASUREMENT),
            "divergence_obs_nwp": "vedi sigma_components",
        },
        "sigma_components": {},
        "members_published": len(members),
        "member_selection": ("ripartizione proporzionale ai pesi (minimo %d "
                             "per ipotesi), quantili di densita' dentro ogni "
                             "ipotesi" % MIN_MEMBERS_PER_HYPOTHESIS),
        "member_offsets_min": list(offsets),
        "member_decimals": MEMBER_DECIMALS,
        "spread_offsets_min": list(SPREAD_OFFSETS_MIN),
        "spread_meaning": ("std lungo/trasverso e p90 trasverso dei membri "
                           "della mistura rispetto al blend, in km"),
        "probability_calibrated": False,
        "probability_null_reason": ("nessun dataset di verifiche etichettate "
                                    "per questa grandezza: il fascio misura "
                                    "la dispersione del moto, non la "
                                    "frequenza osservata degli eventi"),
        "calibration_reference": dict(VALIDATION_REFERENCE),
        "seed": _seed_for(structure_id, anchor_ms),
    }
    if residuals:
        basis["residuals_km_60"] = residuals
    return ensemble, basis


# ---------------------------------------------------------------------------
# Blocco completo per un candidato
# ---------------------------------------------------------------------------

def _bunkers_motion_from_record(candidate_ctx):
    """RM della struttura da ``severe_stack.bunkers`` (se gia' calcolato).

    Accetta le forme {"u_kmh","v_kmh"}, {"u_ms","v_ms"} e il wrapper di
    severe_stack ({"value": {"u_ms","v_ms"}, ...}). Ritorna
    {"u_kmh","v_kmh"} oppure None. Nessun valore inventato: se il campo manca
    o e' null con motivo, si ritorna None e l'ipotesi usa il profilo NWP.
    """
    if not isinstance(candidate_ctx, dict):
        return None
    stack = candidate_ctx.get("severe_stack")
    if not isinstance(stack, dict):
        return None
    bunk = stack.get("bunkers")
    if not isinstance(bunk, dict):
        return None
    rm = bunk.get("rm_ms")
    if isinstance(rm, dict) and isinstance(rm.get("value"), dict):
        rm = rm["value"]
    if not isinstance(rm, dict):
        return None
    if rm.get("u_kmh") is not None and rm.get("v_kmh") is not None:
        return {"u_kmh": float(rm["u_kmh"]), "v_kmh": float(rm["v_kmh"])}
    if rm.get("u_ms") is not None and rm.get("v_ms") is not None:
        return {"u_kmh": float(rm["u_ms"]) * 3.6,
                "v_kmh": float(rm["v_ms"]) * 3.6}
    return None


def build_motion_block(candidate_ctx, bundle, fit, anchor_ms,
                       dataset=None, obs=None):
    """Costruisce i campi NWP/blend/ensemble per un record di struttura.

    ``candidate_ctx``: dict del record (position, structure_id, ...).
    ``fit``: risultato di forecast.fit_motion.
    ``anchor_ms``: ancora temporale del run (radar_timestamp).

    Ritorna un dict con le chiavi:
      nwp_motion, nwp_motion_null_reason, nwp_motion_detail,
      motion_blend, motion_weights, motion_blend_components,
      ensemble, ensemble_basis, ensemble_null_reason,
      ensemble_members, ensemble_mean, ensemble_spread_km,
      ensemble_hypotheses, ensemble_member_hypothesis.

    Regola: l'ensemble esiste se almeno UNA ipotesi di moto e' calcolabile.
    Se manca il NWP ma il fit e' valido, la mistura si costruisce SOLO
    sull'osservato (dichiarato in ensemble_hypotheses: le altre ipotesi hanno
    peso 0, ``active: false`` e il motivo) e i pesi si rinormalizzano.
    """
    out = {
        "nwp_motion": None,
        "nwp_motion_null_reason": None,
        "nwp_motion_detail": None,
        "motion_blend": None,
        "motion_weights": None,
        "motion_blend_components": None,
        "ensemble": None,
        "ensemble_members": None,
        "ensemble_mean": None,
        "ensemble_spread_km": None,
        "ensemble_hypotheses": None,
        "ensemble_member_hypothesis": None,
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

    # --- MISTURA DI IPOTESI (2026-11-11) ----------------------------------
    motions = hypothesis_motions(
        dataset, float(pos[0]), float(pos[1]), anchor_ms, fit, obs=obs,
        bunkers=_bunkers_motion_from_record(candidate_ctx))
    hyp_weights, hyp_detail = hypothesis_weights(
        motions, fit.get("fit_used"), fit.get("resid_std_km"))
    if not hyp_weights:
        out["ensemble_null_reason"] = "no_hypothesis_with_measured_error"
        return out
    for m in motions:
        m["weight"] = round(float(hyp_weights.get(m["name"], 0.0)), 4)
    residuals = hypothesis_residuals(motions, hyp_weights)
    for m in motions:
        r = residuals.get(m["name"]) or {}
        m["sigma_along_km_60"] = r.get("sigma_along_km_60")
        m["sigma_cross_km_60"] = r.get("sigma_cross_km_60")
        m["sigma_km_60"] = r.get("sigma_km_60")
        m["measured_error_km_60"] = r.get("error_km_60")
        m["disagreement_km_60"] = r.get("disagreement_km_60")

    sigma_speed = math.sqrt(dspeed ** 2 + TRACKER_SIGMA_SPEED_KMH ** 2)
    sigma_dir = math.sqrt(ddir ** 2 + TRACKER_SIGMA_DIR_DEG ** 2)
    sigma_speed = min(max(sigma_speed, SIGMA_SPEED_FLOOR_KMH),
                      SIGMA_SPEED_CAP_KMH)
    sigma_dir = min(max(sigma_dir, SIGMA_DIR_FLOOR_DEG), SIGMA_DIR_CAP_DEG)
    ensemble, basis = build_ensemble(
        blend, sigma_speed, sigma_dir, (float(pos[0]), float(pos[1])),
        anchor_ms, structure_id=candidate_ctx.get("structure_id"),
        hypotheses=motions, residuals=residuals)
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
    basis["hypothesis_weights"] = hyp_detail
    # Forma della densita' della mistura a 60 min: numeri, non stime. Sono la
    # verifica che la forma NON e' un ellisse gaussiano imposto (area 50/90,
    # asimmetria picco-vs-banda, massimi locali).
    try:
        basis["shape_60min"] = density_shape_metrics(motions, offset_min=60.0)
    except Exception:
        basis["shape_60min"] = None
    # Il fascio vive a livello RECORD (come `ensemble`): si estraggono i campi
    # dal dict dell'ensemble per non duplicarli nel JSON.
    out["ensemble_members"] = ensemble.pop("members", None)
    # `member_hypotheses` resta DENTRO `ensemble` (che convective_structure
    # sposta a livello record) e viene anche esposto piatto nel blocco
    # forecast: due lettori, un solo dato.
    out["ensemble_member_hypothesis"] = ensemble.get("member_hypotheses")
    ensemble.pop("member_meta", None)
    out["ensemble_mean"] = ensemble.pop("mean", None)
    out["ensemble_spread_km"] = ensemble.pop("spread_km", None)
    out["ensemble_hypotheses"] = ensemble.get("hypotheses")
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
