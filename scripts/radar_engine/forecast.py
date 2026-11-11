#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — forecast.py (tracking 2h + nowcast, PARTE 3)

Forecast cinematico <=2h per i record di struttura convettiva: estrapolazione lineare
PESSATA dai centroidi della track associata (TITAN/SCIT-style, NO fit
quadratico), proiezione haversine (riuso di tracking.dest_from), cono di
incertezza ANISOTROPO e intensita' che decade esponenzialmente verso la media
regionale con gate sul trend recente (dBZ/scansione clampato).

CONO DAL CONTORNO DELLA STRUTTURA (2026-11-11). Il cono non e' piu' un'ellisse
fissa uguale per ogni struttura: il motore risolve la maschera del footprint
della struttura (la stessa di footprint.py: componente connessa a
`detect.dbz_threshold`, identita' per cell_id/storm_object_id) e ne estrae il
contorno con marching squares sulla griglia del pixel radar (1 km),
semplificato con Douglas-Peucker (tolleranza 1.5 px; 1.2 px per il cono).
Il contorno corrente e' pubblicato in `outline` (GeoJSON Polygon, [lon,lat],
anello chiuso).

Per ogni step t (30/60/90/120 min) il cono e' la DILATAZIONE del contorno:

    R(t) = max(6, 15, cone0) + growth*t     (p90 calibrato: 15 km + 1 km/min)
    raggi delle bande = R(t) * k,  k = 0.312 / 0.742 / 1.0

Le tre bande annidate sono i livelli 50/75/90%. Ogni step porta `cone_bands`
(lista di 3 dict: `level`, `radius_km`, `area_km2`, `vertices`, `polygon` =
anello [lon,lat] chiuso) e `cone_polygon` = anello 90% (GeoJSON Polygon,
retrocompatibilita' con il client che disegna un solo poligono). Il rapporto
p50/p90 = 0.312 e' MISURATO a 30 min (14.9 / 47.8 km); p75 e' interpolato
linearmente in probabilita' fra p50 e p90; il 90% resta R(t), cioe' lo stesso
valore del cono precedente. La distanza euclidea dalla maschera e' calcolata
UNA volta per struttura (scipy.ndimage.distance_transform_edt) e riusata per
tutti gli step e le bande.

`cone_km_along` / `cone_km_cross` sono i SEMIASSI DELL'INVILUPPO del poligono
90% (0.5*(max-min) lungo il moto e di traverso), non piu' costanti: una cella
da 300 km^2 e un sistema da 32.000 km^2 ricevono coni diversi.

Se la maschera non e' disponibile (frame assente, cella non risolta, poligono
vuoto) `outline` e' null con `outline_null_reason`, `cone_shape` torna
"ellipse" e il cono e' l'ellisse precedente (along = 15+1*t, cross =
0.6*along), dichiarata in `cone_fallback_reason`. Le tre bande sono calcolate
anche in fallback (ellissi annidate con gli stessi k).

Le bande NON sono una probabilita' calibrata: `probability_calibrated` vale
false e `probability_null_reason` lo dichiara. I k vengono dagli errori radiali
storici del tracker, non da un confronto con osservazioni.

Il rapporto 0.6 del fallback e' una SCELTA DICHIARATA, non una calibrazione:
la misura dell'errore scomposto along/cross sui pair disponibili (7 track
affidabili, 533 coppie) da' un rapporto instabile e > 1 (1.42 / 1.81 / 0.58 /
0.34 nelle fasce 0-30 / 30-60 / 60-90 / 90-130 min): il campione non basta per
una stima stabile. Il campo `cone_basis` vale "isotropic_scaled_0.6_cross" e
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

import numpy as np

from . import footprint as footprint_mod
from . import models
from . import nwp_motion
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
# --- CONO DAL CONTORNO (2026-11-11) ----------------------------------------
_CONE_SHAPE_DILATED = "dilated_outline"
_CONE_SHAPE_ELLIPSE = "ellipse"
_CONE_BASIS_DILATED = "footprint_mask_dilated"
_CONE_BASIS_NOTE_DILATED = (
    "outline = marching squares della maschera del footprint (pixel radar, "
    "1 km) semplificato con Douglas-Peucker 1.5 px; cone = dilatazione "
    "euclidea del contorno (distance_transform_edt, una sola mappa per "
    "struttura, contorno semplificato 1.2 px) e traslazione lungo il moto. "
    "Nessuna soglia di pubblicazione toccata.")
_OUTLINE_DP_TOL_PX = 1.5
_CONE_DP_TOL_PX = 1.2
_OUTLINE_MIN_HOLE_PX = 4.0   # buchi < 4 px (4 km^2 a 1 km) non sono contorno
# Livelli e fattori delle bande annidate. Il 90% e' R(t) (stesso cono di
# prima); il 50% usa il rapporto p50/p90 MISURATO a 30 min (14.9/47.8 =
# 0.3117) e il 75% e' interpolato linearmente in probabilita' fra p50 e p90:
# 0.3117 + (1-0.3117)*(0.75-0.5)/(0.9-0.5) = 0.7419.
_CONE_BAND_LEVELS = (0.50, 0.75, 0.90)
_CONE_BAND_SCALES = (0.312, 0.742, 1.0)
_CONE_BAND_P50_KM_30 = 14.9
_CONE_BAND_P75_KM_30 = 35.5
_CONE_BAND_P90_KM_30 = 47.8
_PROBABILITY_NULL_REASON = (
    "bands_from_historical_tracker_radial_errors_not_observations")
# Budget di prestazioni: se la griglia paddata del cono supera questa soglia
# (celle), il cono usa una griglia a 2 km (il contorno resta a 1 km). La scelta
# e' dichiarata in `cone_grid_km`.
_CONE_MAX_GRID_PX = 200000
_CONE_COARSE_FACTOR = 2


def _cone_bands_basis():
    """Metodo e numeri delle bande (copia fresca a ogni blocco pubblicato)."""
    return {
        "method": "measured_p50_p90_ratio_constant_with_t",
        "levels": list(_CONE_BAND_LEVELS),
        "scale_factors": list(_CONE_BAND_SCALES),
        "p50_km_at_30min": _CONE_BAND_P50_KM_30,
        "p75_km_at_30min": _CONE_BAND_P75_KM_30,
        "p90_km_at_30min": _CONE_BAND_P90_KM_30,
        "outer_envelope_km": "max(6, 15, cone0) + growth*t (p90 calibrato)",
        "p75_derivation": (
            "interpolazione lineare in probabilita' fra p50 e p90"),
        "assumption": (
            "rapporto p50/p90 costante con t; nessuna misura p50 a 60/90/120"),
        "gaussian_2d_k": [1.177, 1.665, 2.146],
        "gaussian_2d_k_normalized_to_p90": [0.549, 0.776, 1.0],
        "note": (
            "bande da errori radiali storici del tracker, NON da osservazioni"),
    }
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


# ---------------------------------------------------------------------------
# Contorno della maschera: marching squares + Douglas-Peucker
# ---------------------------------------------------------------------------
def _marching_squares_segments(mask):
    """Segmenti del contorno di una maschera booleana (marching squares).

    La maschera e' campionata al CENTRO dei pixel; il contorno e' la curva di
    livello 0.5 fra centri adiacenti, cioe' il bordo del pixel (stessa
    geometria di rasterio.features.shapes, senza dipendere da rasterio).
    Ritorna una lista di segmenti ((r0,c0),(r1,c1)) in coordinate di indice
    della maschera (row, col), con r/c frazionari a .0/.5."""
    m = np.pad(np.asarray(mask, dtype=bool), 1, constant_values=False)
    tl = m[:-1, :-1]
    tr = m[:-1, 1:]
    br = m[1:, 1:]
    bl = m[1:, :-1]
    case = (tl.astype(np.uint8)
            | (tr.astype(np.uint8) << 1)
            | (br.astype(np.uint8) << 2)
            | (bl.astype(np.uint8) << 3))

    def _top(i, j):
        return i.astype(float), j.astype(float) + 0.5

    def _right(i, j):
        return i.astype(float) + 0.5, j.astype(float) + 1.0

    def _bottom(i, j):
        return i.astype(float) + 1.0, j.astype(float) + 0.5

    def _left(i, j):
        return i.astype(float) + 0.5, j.astype(float)

    edges = {"top": _top, "right": _right, "bottom": _bottom, "left": _left}
    # 16 casi (bit TL=1, TR=2, BR=4, BL=8). I due casi sella (5, 10) sono
    # risolti collegando i due angoli interni separatamente: nessun segmento
    # condiviso, nessun anello a figura otto.
    cases = {
        1: (("left", "top"),),
        2: (("top", "right"),),
        3: (("left", "right"),),
        4: (("right", "bottom"),),
        5: (("left", "top"), ("right", "bottom")),
        6: (("top", "bottom"),),
        7: (("left", "bottom"),),
        8: (("bottom", "left"),),
        9: (("top", "bottom"),),
        10: (("top", "right"), ("bottom", "left")),
        11: (("right", "bottom"),),
        12: (("left", "right"),),
        13: (("top", "right"),),
        14: (("left", "top"),),
        15: (),
    }
    segs = []
    for k, pairs in cases.items():
        if not pairs:
            continue
        ii, jj = np.nonzero(case == k)
        if ii.size == 0:
            continue
        for ea, eb in pairs:
            ra, ca = edges[ea](ii, jj)
            rb, cb = edges[eb](ii, jj)
            for idx in range(ii.size):
                segs.append(((float(ra[idx]), float(ca[idx])),
                             (float(rb[idx]), float(cb[idx]))))
    return segs


def _link_rings(segs):
    """Unisce i segmenti in anelli chiusi (lista di (r,c) senza doppio punto)."""
    if not segs:
        return []
    from collections import defaultdict

    def key(p):
        return (round(p[0], 6), round(p[1], 6))

    adj = defaultdict(list)
    for idx, (a, b) in enumerate(segs):
        adj[key(a)].append((idx, b))
        adj[key(b)].append((idx, a))
    used = [False] * len(segs)
    rings = []
    for start in range(len(segs)):
        if used[start]:
            continue
        used[start] = True
        a, b = segs[start]
        ring = [a, b]
        cur = b
        while True:
            nxt = None
            for idx, other in adj[key(cur)]:
                if not used[idx]:
                    nxt = (idx, other)
                    break
            if nxt is None:
                break
            idx, other = nxt
            used[idx] = True
            ring.append(other)
            cur = other
            if key(cur) == key(ring[0]):
                ring.pop()
                break
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def _dp_open(points, tol):
    """Douglas-Peucker su una polilinea aperta (lista di (x, y))."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = float(tol) ** 2
    while stack:
        i0, i1 = stack.pop()
        x0, y0 = points[i0]
        x1, y1 = points[i1]
        dx, dy = x1 - x0, y1 - y0
        den = dx * dx + dy * dy
        best = -1.0
        best_idx = -1
        for k in range(i0 + 1, i1):
            px, py = points[k]
            if den <= 1e-12:
                d2 = (px - x0) ** 2 + (py - y0) ** 2
            else:
                t = ((px - x0) * dx + (py - y0) * dy) / den
                t = max(0.0, min(1.0, t))
                qx, qy = x0 + t * dx, y0 + t * dy
                d2 = (px - qx) ** 2 + (py - qy) ** 2
            if d2 > best:
                best = d2
                best_idx = k
        if best > tol2 and best_idx > 0:
            keep[best_idx] = True
            stack.append((i0, best_idx))
            stack.append((best_idx, i1))
    return [p for p, k in zip(points, keep) if k]


def _dp_ring(points, tol):
    """Douglas-Peucker su un anello chiuso (senza doppio endpoint)."""
    n = len(points)
    if n <= 3:
        return list(points)
    x0, y0 = points[0]
    far = max(range(n), key=lambda k: (points[k][0] - x0) ** 2
              + (points[k][1] - y0) ** 2)
    if far <= 0:
        return list(points)
    first = _dp_open(points[:far + 1], tol)
    second = _dp_open(points[far:] + [points[0]], tol)
    out = first[:-1] + second[:-1]
    return out if len(out) >= 3 else list(points)


def _ring_signed_area(ring):
    """Area con segno (shoelace) di un anello aperto in (x, y)."""
    s = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _point_in_ring(pt, ring):
    """Ray casting: True se pt e' dentro l'anello (bordo escluso)."""
    x, y = pt
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y):
            xin = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if xin > x:
                inside = not inside
    return inside


def _mask_ring_to_lonlat(ring, raster, window, row_offset=0.0,
                         col_offset=0.0, scale=1.0):
    """Anello (row, col) della maschera -> ring GeoJSON [lon, lat] chiuso.

    `scale` = fattore di riduzione della griglia del cono (1 = pixel radar,
    2 = griglia a 2 km): le coordinate della maschera ridotta vanno riportate
    alla griglia originale prima dell'affine del raster."""
    r0, _r1, c0, _c1 = window
    half = 0.5 * float(scale)
    xs, ys = [], []
    for r, c in ring:
        x, y = raster.transform @ (
            c0 + (c + col_offset) * float(scale) + half,
            r0 + (r + row_offset) * float(scale) + half)
        xs.append(float(x))
        ys.append(float(y))
    lon, lat = raster.geo_transform.transform(xs, ys)
    out = [[round(float(a), 5), round(float(b), 5)]
           for a, b in zip(lon, lat)]
    out.append(list(out[0]))
    return out


def _rings_to_polygon(rings, raster, window, row_offset=0.0,
                      col_offset=0.0, fill_holes=False, min_hole_px=0.0,
                      scale=1.0):
    """(Polygon GeoJSON, meta) dagli anelli in coordinate maschera.

    L'anello esterno piu' grande e' il contorno; gli anelli contenuti sono
    buchi (mantenuti, salvo fill_holes=True o area < min_hole_px); gli altri
    anelli esterni sono componenti staccate e vengono contati in
    `components_dropped`."""
    empty = {"holes": 0, "holes_filled": 0, "holes_dropped": 0,
             "components_dropped": 0, "vertices": 0}
    if not rings:
        return None, dict(empty)
    items = []
    for ring in rings:
        area = abs(_ring_signed_area(ring))
        if area > 0.0:
            items.append((area, ring))
    if not items:
        return None, dict(empty)
    items.sort(key=lambda t: -t[0])
    _area, outer = items[0]
    holes = []
    dropped = 0
    for area, ring in items[1:]:
        if _point_in_ring(ring[0], outer):
            holes.append((area, ring))
        else:
            dropped += 1
    n_holes = len(holes)
    tiny = sum(1 for a, _r in holes if float(min_hole_px) > 0.0
               and a < float(min_hole_px))
    if fill_holes:
        holes = []
    else:
        holes = [r for a, r in holes
                 if not (float(min_hole_px) > 0.0
                         and a < float(min_hole_px))]
    if _ring_signed_area(outer) < 0:
        outer = list(reversed(outer))
    holes = [list(reversed(h)) if _ring_signed_area(h) > 0 else h
             for h in holes]
    coord_rings = [_mask_ring_to_lonlat(outer, raster, window,
                                        row_offset, col_offset, scale)]
    for h in holes:
        coord_rings.append(_mask_ring_to_lonlat(h, raster, window,
                                                row_offset, col_offset, scale))
    return ({"type": "Polygon", "coordinates": coord_rings},
            {"holes": len(holes), "holes_filled": n_holes - len(holes)
             if fill_holes else 0,
             "holes_dropped": tiny,
             "components_dropped": dropped, "vertices": len(outer)})


def _ring_area_km2(ring):
    """Area (km^2) di un ring [lon, lat] con piano locale equirettangolare."""
    if not ring or len(ring) < 4:
        return 0.0
    n = len(ring) - 1
    lat0 = sum(p[1] for p in ring[:n]) / max(n, 1)
    kx = _KM_PER_DEG * math.cos(math.radians(lat0))
    ky = _KM_PER_DEG
    s = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        s += (x0 * kx) * (y1 * ky) - (x1 * kx) * (y0 * ky)
    return abs(s) * 0.5


def _polygon_area_km2(geom):
    """Area (km^2) di un Polygon GeoJSON: esterno meno i buchi."""
    if not geom:
        return 0.0
    rings = geom.get("coordinates") or []
    if not rings:
        return 0.0
    total = _ring_area_km2(rings[0])
    for hole in rings[1:]:
        total -= _ring_area_km2(hole)
    return max(total, 0.0)


def _translate_ring(ring, bearing, distance_km):
    """Ring [lon,lat] traslato di `distance_km` verso `bearing`."""
    out = []
    for lon, lat in ring[:-1]:
        nlon, nlat = dest_from((lon, lat), bearing, distance_km)
        out.append([round(float(nlon), 5), round(float(nlat), 5)])
    out.append(list(out[0]))
    return out


def _envelope_axes(ring, center, heading_deg):
    """(semi-asse lungo, semi-asse traverso) dell'inviluppo del ring.

    Proietta ogni vertice sul versante del moto (along) e sulla perpendicolare
    (cross) rispetto a `center`; il semi-asse e' 0.5*(max-min)."""
    along, cross = [], []
    for lon, lat in ring[:-1]:
        d = haversine_km(center, (lon, lat))
        brg = bearing_deg(center, (lon, lat))
        delta = math.radians(brg - heading_deg)
        along.append(d * math.cos(delta))
        cross.append(d * math.sin(delta))
    if not along:
        return 0.0, 0.0
    return (0.5 * (max(along) - min(along)),
            0.5 * (max(cross) - min(cross)))


# ---------------------------------------------------------------------------
# Risoluzione del contorno e cono dilatato
# ---------------------------------------------------------------------------
def _resolve_outline(candidate_ctx, bundle):
    """(meta pubblico, contesto interno) del contorno alla posizione attuale.

    `meta` e' serializzabile: geometry (Polygon o None), null_reason, source,
    pixel_count, area_km2, vertices, holes, components_dropped. `ctx` contiene
    mask/window/raster per la dilatazione (None se il contorno non esiste)."""
    from . import config as _cfg
    cfg_detect = dict((_cfg.CONFIG or {}).get("detect") or {})
    cfg_detect.setdefault("dbz_threshold", 20.0)
    meta = {"geometry": None, "null_reason": None, "source": None,
            "pixel_count": None, "area_km2": None, "polygon_area_km2": None,
            "coverage_pct": None, "vertices": None, "holes": 0,
            "holes_dropped": 0, "components_dropped": 0}
    rec = candidate_ctx if isinstance(candidate_ctx, dict) else {}
    frame_iso = rec.get("last_seen") or rec.get("first_seen")
    try:
        frame_index = footprint_mod._frame_index_by_iso(bundle, frame_iso)
    except Exception:
        frame_index = None
    if frame_index is None:
        meta["null_reason"] = "frame_not_found"
        return meta, None
    try:
        cells, source = footprint_mod._cells_for_record(bundle, rec,
                                                        frame_index)
    except Exception:
        cells, source = [], "cell_not_found"
    if not cells:
        meta["null_reason"] = source or "cell_not_found"
        return meta, None
    # I frame RICOSTRUITI dall'archivio rolling hanno `cell.frame_index=None`
    # (history._cell_from_payload -> -1): la maschera va risolta sul frame del
    # record, non su un indice mancante. L'override e' temporaneo (ripristinato
    # nel finally) e vale per tutte le celle del record, che sono allo stesso
    # frame per costruzione (_cells_for_record).
    saved = []
    for cell in cells:
        old = getattr(cell, "frame_index", None)
        if old != frame_index:
            saved.append((cell, old))
            cell.frame_index = frame_index
    try:
        win, mask, _used, _skipped, _how = footprint_mod.masks_for_cells(
            cells, bundle, cfg_detect)
    except Exception:
        win, mask = None, None
    finally:
        for cell, old in saved:
            cell.frame_index = old
    if win is None or mask is None:
        meta["null_reason"] = "cell_mask_not_resolved"
        return meta, None
    raster = bundle.frames[frame_index]
    meta["source"] = source
    meta["pixel_count"] = int(mask.sum())
    meta["area_km2"] = round(float(mask.sum())
                             * float(raster.pixel_area_km2), 3)
    try:
        rings = _link_rings(_marching_squares_segments(mask))
        rings = [_dp_ring(r, _OUTLINE_DP_TOL_PX) for r in rings]
        geom, info = _rings_to_polygon(
            rings, raster, win, fill_holes=False,
            min_hole_px=_OUTLINE_MIN_HOLE_PX)
    except Exception:
        geom, info = None, {}
    if geom is None:
        meta["null_reason"] = "polygonize_empty"
        return meta, None
    poly_area = _polygon_area_km2(geom)
    meta["geometry"] = geom
    meta["polygon_area_km2"] = round(poly_area, 3)
    meta["coverage_pct"] = (
        round(100.0 * poly_area / meta["area_km2"], 2)
        if meta["area_km2"] else None)
    meta["vertices"] = info.get("vertices")
    meta["holes"] = info.get("holes", 0)
    meta["holes_dropped"] = info.get("holes_dropped", 0)
    meta["components_dropped"] = info.get("components_dropped", 0)
    ctx = {"mask": mask, "window": win, "raster": raster,
           "frame_index": frame_index, "frame_iso": frame_iso,
           "source": source,
           "pixel_area_km2": float(raster.pixel_area_km2)}
    return meta, ctx


def _downsample_mask(mask, factor):
    """Maschera ridotta di `factor` con OR di blocco (copertura conservata)."""
    m = np.asarray(mask, dtype=bool)
    h, w = m.shape
    ph = (-h) % factor
    pw = (-w) % factor
    if ph or pw:
        m = np.pad(m, ((0, ph), (0, pw)), constant_values=False)
    return m.reshape(m.shape[0] // factor, factor,
                     m.shape[1] // factor, factor).any(axis=(1, 3))


def _dilated_cone_bands(ctx, fit, growth):
    """({offset: [bande 50/75/90]}, grid_km) dal contorno della maschera.

    Una sola distance transform per struttura; i tre livelli sono soglie della
    stessa mappa. Se la griglia paddata supera `_CONE_MAX_GRID_PX` il cono usa
    una griglia a 2 km (dichiarata in `cone_grid_km`). Ritorna (None, motivo)
    se la maschera non e' utilizzabile."""
    mask = ctx.get("mask")
    win = ctx.get("window")
    raster = ctx.get("raster")
    if mask is None or win is None or raster is None:
        return None, "outline_context_missing"
    try:
        import scipy.ndimage as ndi
    except Exception:
        return None, "scipy_unavailable"
    px_km = math.sqrt(max(float(ctx.get("pixel_area_km2") or 1.0), 1e-9))
    base = max(_CONE_MIN_KM, _CONE_DEFAULT_KM, float(fit["cone0_km"]))
    max_km = max(base + growth * offset_min for offset_min in OFFSETS_MIN)
    pad = int(math.ceil(max_km / px_km)) + 2
    est_px = ((int(mask.shape[0]) + 2 * pad)
              * (int(mask.shape[1]) + 2 * pad))
    factor = _CONE_COARSE_FACTOR if est_px > _CONE_MAX_GRID_PX else 1
    if factor > 1:
        mask = _downsample_mask(mask, factor)
        px_km *= factor
        pad = int(math.ceil(max_km / px_km)) + 2
    radii = {}
    for offset_min in OFFSETS_MIN:
        outer_km = base + growth * offset_min
        radii[offset_min] = [max(px_km, outer_km * k)
                             for k in _CONE_BAND_SCALES]
    padded = np.pad(np.asarray(mask, dtype=bool), pad, constant_values=False)
    dist = ndi.distance_transform_edt(~padded, sampling=(px_km, px_km))
    out = {}
    for offset_min in OFFSETS_MIN:
        outer = dist <= radii[offset_min][-1]
        rows, cols = np.nonzero(outer)
        if rows.size == 0:
            return None, "dilated_mask_empty"
        r0d, r1d = int(rows.min()), int(rows.max())
        c0d, c1d = int(cols.min()), int(cols.max())
        sub_dist = dist[r0d:r1d + 1, c0d:c1d + 1]
        row_off = r0d - pad
        col_off = c0d - pad
        bands = []
        for level, radius in zip(_CONE_BAND_LEVELS, radii[offset_min]):
            sub = ndi.binary_fill_holes(sub_dist <= radius)
            rings = _link_rings(_marching_squares_segments(sub))
            if not rings:
                return None, "dilated_polygonize_empty"
            rings = [_dp_ring(r, _CONE_DP_TOL_PX) for r in rings]
            geom, info = _rings_to_polygon(
                rings, raster, win, row_offset=row_off, col_offset=col_off,
                fill_holes=True, scale=factor)
            if geom is None:
                return None, "dilated_polygonize_empty"
            bands.append({
                "level": float(level),
                "radius_km": round(float(radius), 1),
                "area_km2": round(_polygon_area_km2(geom), 1),
                "vertices": int(info.get("vertices") or 0),
                "holes_filled": int(info.get("holes_filled") or 0),
                "components_dropped": int(info.get("components_dropped") or 0),
                "polygon": geom,
            })
        out[offset_min] = bands
    return out, px_km


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
    # Triangolazione osservato + NWP (nwp_motion.py): campi ADDITIVI nel blocco
    # forecast. Se il modulo non riesce a calcolare nulla, i campi restano
    # None con motivo: il cono qui sotto non cambia di una virgola.
    try:
        motion_fields = nwp_motion.build_motion_block(
            candidate_ctx, bundle, fit, anchor_ms)
    except Exception:
        motion_fields = {
            "nwp_motion": None,
            "nwp_motion_null_reason": "build_error",
            "nwp_motion_detail": None,
            "motion_blend": None,
            "motion_weights": None,
            "motion_blend_components": None,
            "ensemble": None,
            "ensemble_basis": None,
            "ensemble_null_reason": "build_error",
        }
    mean_max, mean_mean = _region_dbz_means(bundle)
    i0 = float(last.max_dbz)
    m0 = float(last.mean_dbz)
    trend = _clamp(_dbz_trend(points), _TREND_SLOPE_MIN, _TREND_SLOPE_MAX)
    growth = _CONE_GROWTH_KM_MIN * (1.5 if young else 1.0)
    decay_lambda, decay_class = _decay_lambda(candidate_ctx, track)

    # Contorno della struttura alla posizione attuale + cono dilatato.
    outline_meta, outline_ctx = _resolve_outline(candidate_ctx, bundle)
    band_data = None
    grid_km = None
    fallback_reason = None
    if outline_ctx is not None:
        band_data, grid_km = _dilated_cone_bands(outline_ctx, fit, growth)
        if band_data is None:
            fallback_reason = grid_km
            grid_km = None
    else:
        fallback_reason = (outline_meta.get("null_reason")
                           or "outline_unavailable")
    cone_shape = (_CONE_SHAPE_DILATED if band_data is not None
                  else _CONE_SHAPE_ELLIPSE)

    steps = []
    for offset_min in OFFSETS_MIN:
        lon, lat = dest_from(last.lonlat, fit["direction_toward_deg"],
                             fit["velocity_km_min"] * offset_min)
        decay = math.exp(-offset_min / decay_lambda)
        i_dec = mean_max + (i0 - mean_max) * decay
        max_dbz = _clamp(i_dec + trend * (offset_min / 5.0), *_DBZ_RANGE)
        mean_dbz = _clamp(mean_mean + (m0 - mean_mean) * decay, *_MEAN_DBZ_RANGE)
        # Semiasse LUNGO = cono isotropo storico (fallback); semiasse
        # TRASVERSALE = along * rapporto dichiarato (0.6).
        along_km = max(_CONE_MIN_KM, _CONE_DEFAULT_KM,
                       fit["cone0_km"]) + growth * offset_min
        cross_km = along_km * _CONE_CROSS_RATIO
        if band_data is not None:
            bands = []
            for band in band_data[offset_min]:
                ring = _translate_ring(
                    band["polygon"]["coordinates"][0],
                    fit["direction_toward_deg"],
                    fit["velocity_km_min"] * offset_min)
                bands.append({
                    "level": float(band["level"]),
                    "radius_km": band["radius_km"],
                    "radius_basis": "dilation",
                    "area_km2": round(_ring_area_km2(ring), 1),
                    "vertices": int(band["vertices"]),
                    "holes_filled": int(band["holes_filled"]),
                    "components_dropped": int(band["components_dropped"]),
                    "polygon": ring,
                })
            outer_ring = bands[-1]["polygon"]
            cone_polygon = {"type": "Polygon", "coordinates": [outer_ring]}
            cone_along, cone_cross = _envelope_axes(
                outer_ring, (lon, lat), fit["direction_toward_deg"])
            cone_vertices = int(bands[-1]["vertices"])
        else:
            bands = []
            for level, scale in zip(_CONE_BAND_LEVELS, _CONE_BAND_SCALES):
                ring = _ellipse_polygon(
                    (lon, lat), fit["direction_toward_deg"],
                    along_km * scale, cross_km * scale)["coordinates"][0]
                bands.append({
                    "level": float(level),
                    "radius_km": round(along_km * scale, 1),
                    "radius_basis": "ellipse_along",
                    "area_km2": round(_ring_area_km2(ring), 1),
                    "vertices": _CONE_VERTICES,
                    "holes_filled": 0,
                    "components_dropped": 0,
                    "polygon": ring,
                })
            outer_ring = bands[-1]["polygon"]
            cone_polygon = {"type": "Polygon", "coordinates": [outer_ring]}
            cone_along = along_km
            cone_cross = cross_km
            cone_vertices = _CONE_VERTICES
        steps.append({
            "offset_min": offset_min,
            "at": _iso_from_ms(anchor_ms + offset_min * 60000),
            "position": [round(float(lon), 5), round(float(lat), 5)],
            "cone_km": round(float(cone_along), 1),
            "cone_km_along": round(float(cone_along), 1),
            "cone_km_cross": round(float(cone_cross), 1),
            "cone_polygon": cone_polygon,
            "cone_bands": bands,
            "cone_area_km2": round(_ring_area_km2(
                cone_polygon["coordinates"][0]), 1),
            "cone_vertices": cone_vertices,
            "max_dbz": round(max_dbz, 1),
            "mean_dbz": round(mean_dbz, 1),
            "area_km2": round(float(last.area_km2), 1),
        })

    return {
        "horizon_min": HORIZON_MIN,
        "step_min": STEP_MIN,
        "generated_at": generated_at,
        "method": METHOD,
        "cone_shape": cone_shape,
        "cone_basis": (_CONE_BASIS_DILATED if band_data is not None
                       else _CONE_BASIS),
        "cone_basis_note": (_CONE_BASIS_NOTE_DILATED if band_data is not None
                            else _CONE_BASIS_NOTE),
        "cone_cross_ratio": (None if band_data is not None
                             else _CONE_CROSS_RATIO),
        "cone_vertices": max((s["cone_vertices"] for s in steps),
                             default=_CONE_VERTICES),
        "cone_grid_km": (round(float(grid_km), 2) if grid_km else None),
        "cone_dp_tolerance_px": _CONE_DP_TOL_PX,
        "cone_fallback_reason": (fallback_reason if band_data is None else None),
        "cone_bands_basis": _cone_bands_basis(),
        "probability_calibrated": False,
        "probability_null_reason": _PROBABILITY_NULL_REASON,
        "outline": outline_meta.get("geometry"),
        "outline_null_reason": outline_meta.get("null_reason"),
        "outline_source": outline_meta.get("source"),
        "outline_pixel_count": outline_meta.get("pixel_count"),
        "outline_area_km2": outline_meta.get("area_km2"),
        "outline_polygon_area_km2": outline_meta.get("polygon_area_km2"),
        "outline_coverage_pct": outline_meta.get("coverage_pct"),
        "outline_vertices": outline_meta.get("vertices"),
        "outline_holes": outline_meta.get("holes", 0),
        "outline_holes_dropped": outline_meta.get("holes_dropped", 0),
        "outline_components_dropped": outline_meta.get(
            "components_dropped", 0),
        "decay_class": decay_class,
        "decay_lambda_min": decay_lambda,
        "basis_frames": int(fit["basis_frames"]),
        "confidence": _confidence(fit, young),
        "fit_used": bool(fit["fit_used"]),
        "clamped": bool(fit["clamped"]),
        "direction_toward_deg": round(float(fit["direction_toward_deg"]), 1),
        "velocity_kmh": round(float(fit["velocity_kmh"]), 1),
        "steps": steps,
        **motion_fields,
    }, None


def build_outline(candidate_ctx, bundle):
    """Contorno pubblico della struttura alla posizione attuale (meta dict).

    Stessa risoluzione usata dal forecast (`_resolve_outline`): geometry
    (Polygon o None), null_reason, source, pixel_count, area_km2,
    polygon_area_km2, coverage_pct, vertices, holes, holes_dropped,
    components_dropped. Pensato per i record SENZA forecast (moto non
    affidabile): il contorno esiste anche se il cono non viene emesso."""
    meta, _ctx = _resolve_outline(candidate_ctx, bundle)
    return meta


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


def build_motion(candidate_ctx, bundle):
    """Campi NWP/blend/ensemble per un record, INDIPENDENTI dal cono.

    Stessa forma di `build_outline`: si puo' chiamare anche quando
    `build_forecast_with_reason` restituisce None (moto non affidabile).
    Se `motion_nwp` e' valido l'ensemble esiste lo stesso; se non lo e' e
    nemmeno il fit e' utilizzabile, i campi sono None con motivo.
    """
    try:
        track = _resolve_track(candidate_ctx, bundle)
        if track is None:
            return {"nwp_motion": None, "nwp_motion_null_reason": "no_track",
                    "nwp_motion_detail": None, "motion_blend": None,
                    "motion_weights": None, "motion_blend_components": None,
                    "ensemble": None, "ensemble_basis": None,
                    "ensemble_null_reason": "no_track"}
        points = sorted(track.points, key=lambda p: p.timestamp_ms)
        fit = fit_motion(points)
        anchor_ms = _anchor_ms(bundle, points[-1])
        return nwp_motion.build_motion_block(
            candidate_ctx, bundle, fit, anchor_ms)
    except Exception:
        return {"nwp_motion": None, "nwp_motion_null_reason": "build_error",
                "nwp_motion_detail": None, "motion_blend": None,
                "motion_weights": None, "motion_blend_components": None,
                "ensemble": None, "ensemble_basis": None,
                "ensemble_null_reason": "build_error"}


def attach_nwp_motion(bundle):
    """Campi NWP/ensemble a LIVELLO RECORD per i record senza forecast.

    `build_forecast_with_reason` mette gia' i campi DENTRO il blocco
    `forecast`. Questa funzione copre il caso "cono assente ma motion_nwp
    valido": senza questo giro l'ensemble non sarebbe pubblicato.

    NON e' chiamata da convective_structure.evaluate: il file e' di un altro
    agente. Il wiring e' una riga, subito dopo `_copy_outline`:

        rec.update(forecast_mod.build_motion({**rec, "track": track}, bundle))

    oppure, per tutti i record dopo il ciclo:

        forecast_mod.attach_nwp_motion(bundle)
    """
    return nwp_motion.attach_motion_fields(
        getattr(bundle, "convective_structures", None) or [], bundle)
