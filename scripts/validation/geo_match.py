#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk — geometria per l'accoppiamento su FOOTPRINT (1.2.4.21).

Perche' non shapely: sul host di misura `shapely` NON e' installato
(`ModuleNotFoundError`, verificato), quindi qui ci sono le sole primitive che
servono, senza dipendenze nuove:

  * point-in-ring (ray casting, gestisce anelli chiusi e non chiusi);
  * point-in-polygon con i BUCHI e point-in-MultiPolygon;
  * distanza punto-segmento in km con approssimazione equirettangolare locale
    (dx scalato con cos(lat): errore < 0,1% sotto i 50 km);
  * distanza punto -> geometria (0 se dentro, altrimenti distanza dal bordo);
  * area di un anello in km^2 (per i poligoni degeneri);
  * rasterizzazione su griglia geografica (rasterio, gia' dipendenza del
    motore) e area per pixel pesata sulla latitudine, per la frazione d'Italia.

Un poligono DEGENERE (meno di 3 vertici distinti, area ~0) non e' un errore:
resta una geometria valida per il test di distanza (spezzata) e il
point-in-polygon risponde sempre False. E' la scelta onesta: un poligono
degenere non copre area, ma il suo contorno esiste e la distanza da esso e'
misurabile.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

KM_PER_DEG_LAT = 111.32
_EPS = 1e-12


# ---------------------------------------------------------------------------
# Primitive punto / anello
# ---------------------------------------------------------------------------
def _closed(ring: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in ring]
    if len(pts) >= 2 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def point_in_ring(lon: float, lat: float,
                  ring: Sequence[Sequence[float]]) -> bool:
    """Ray casting (odd-even). Anello chiuso o no, buchi gestiti dal chiamante."""
    pts = _closed(ring)
    if len(pts) < 3:
        return False
    inside = False
    n = len(pts) - 1
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > lat) != (yj > lat):
            x_int = xi + (lat - yi) * (xj - xi) / (yj - yi) if yj != yi else xi
            if lon < x_int:
                inside = not inside
        j = i
    return inside


def point_in_polygon(lon: float, lat: float,
                     polygon: Sequence[Sequence[Sequence[float]]]) -> bool:
    """Polygon GeoJSON: [anello_esterno, buco1, buco2, ...]."""
    if not polygon:
        return False
    if not point_in_ring(lon, lat, polygon[0]):
        return False
    for hole in polygon[1:]:
        if point_in_ring(lon, lat, hole):
            return False
    return True


def geometry_polygons(geom: Optional[Dict[str, Any]]
                      ) -> List[List[List[List[float]]]]:
    """Normalizza Polygon / MultiPolygon / Feature in una lista di poligoni."""
    if not geom:
        return []
    if geom.get("type") == "Feature":
        return geometry_polygons(geom.get("geometry"))
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "Polygon":
        return [coords] if coords else []
    if gtype == "MultiPolygon":
        return list(coords)
    return []


def geometry_bbox(geom: Optional[Dict[str, Any]]
                  ) -> Optional[Tuple[float, float, float, float]]:
    """(min_lon, min_lat, max_lon, max_lat) della geometria o None."""
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    found = False
    for poly in geometry_polygons(geom):
        for ring in poly:
            for p in ring:
                x, y = float(p[0]), float(p[1])
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
                found = True
    if not found:
        return None
    return (x0, y0, x1, y1)


def point_in_geometry(lon: float, lat: float,
                      geom: Optional[Dict[str, Any]]) -> bool:
    return any(point_in_polygon(lon, lat, poly) for poly in geometry_polygons(geom))


# ---------------------------------------------------------------------------
# Distanze in km
# ---------------------------------------------------------------------------
def segment_km(lon: float, lat: float, a: Sequence[float],
               b: Sequence[float]) -> float:
    """Distanza punto-segmento in km (equirettangolare locale)."""
    kx = KM_PER_DEG_LAT * math.cos(math.radians(lat))
    px, py = (lon - a[0]) * kx, (lat - a[1]) * KM_PER_DEG_LAT
    bx, by = (b[0] - a[0]) * kx, (b[1] - a[1]) * KM_PER_DEG_LAT
    den = bx * bx + by * by
    if den <= _EPS:
        return math.hypot(px, py)
    t = (px * bx + py * by) / den
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - t * bx, py - t * by)


def bbox_distance_lower_bound_km(lon: float, lat: float,
                                 bbox: Sequence[float]) -> float:
    """ESTREMO INFERIORE della distanza punto-bbox in km (0 se dentro).

    Serve solo a SCARTARE le coppie lontane senza calcolare la distanza dal
    poligono: essendo un estremo inferiore, `> gate` implica `> gate` vero."""
    x0, y0, x1, y1 = [float(v) for v in bbox]
    kx = KM_PER_DEG_LAT * math.cos(math.radians(lat))
    dx = 0.0
    if lon < x0:
        dx = (x0 - lon) * kx
    elif lon > x1:
        dx = (lon - x1) * kx
    dy = 0.0
    if lat < y0:
        dy = (y0 - lat) * KM_PER_DEG_LAT
    elif lat > y1:
        dy = (lat - y1) * KM_PER_DEG_LAT
    return math.hypot(dx, dy)


def ring_distance_km(lon: float, lat: float,
                     ring: Sequence[Sequence[float]]) -> float:
    pts = _closed(ring)
    if len(pts) < 2:
        if len(pts) == 1:
            kx = KM_PER_DEG_LAT * math.cos(math.radians(lat))
            return math.hypot((lon - pts[0][0]) * kx,
                              (lat - pts[0][1]) * KM_PER_DEG_LAT)
        return float("inf")
    best = float("inf")
    for i in range(len(pts) - 1):
        d = segment_km(lon, lat, pts[i], pts[i + 1])
        if d < best:
            best = d
    return best


def distance_to_geometry_km(lon: float, lat: float,
                            geom: Optional[Dict[str, Any]],
                            inside_is_zero: bool = True) -> Optional[float]:
    """Distanza (km) dal punto alla geometria: 0 se il punto e' DENTRO.

    Dentro un BUCO il punto non e' coperto e la distanza e' quella dal bordo
    del buco (il bordo piu' vicino e' quello). None se la geometria non ha
    coordinate (nessuna distanza inventata)."""
    polys = geometry_polygons(geom)
    if not polys:
        return None
    best = float("inf")
    for poly in polys:
        if not poly:
            continue
        if not point_in_ring(lon, lat, poly[0]):
            best = min(best, ring_distance_km(lon, lat, poly[0]))
            continue
        in_hole = None
        for hole in poly[1:]:
            if point_in_ring(lon, lat, hole):
                d = ring_distance_km(lon, lat, hole)
                if in_hole is None or d < in_hole:
                    in_hole = d
        if in_hole is not None:
            best = min(best, in_hole)
            continue
        if inside_is_zero:
            return 0.0
        best = min(best, ring_distance_km(lon, lat, poly[0]))
    return None if best == float("inf") else float(best)


def ring_area_km2(ring: Sequence[Sequence[float]]) -> float:
    """Area dell'anello in km^2 (proiezione equirettangolare sulla lat media)."""
    pts = _closed(ring)
    if len(pts) < 4:
        return 0.0
    lat0 = sum(p[1] for p in pts) / len(pts)
    kx = KM_PER_DEG_LAT * math.cos(math.radians(lat0))
    ky = KM_PER_DEG_LAT
    s = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i][0] * kx, pts[i][1] * ky
        x1, y1 = pts[i + 1][0] * kx, pts[i + 1][1] * ky
        s += x0 * y1 - x1 * y0
    return abs(s) * 0.5


def geometry_area_km2(geom: Optional[Dict[str, Any]]) -> float:
    """Area (km^2) con i buchi sottratti (geometria degenere -> 0)."""
    total = 0.0
    for poly in geometry_polygons(geom):
        if not poly:
            continue
        total += ring_area_km2(poly[0])
        for hole in poly[1:]:
            total -= ring_area_km2(hole)
    return max(0.0, total)


# ---------------------------------------------------------------------------
# Copertura di punti (stazioni) e frazione d'area
# ---------------------------------------------------------------------------
def point_covered(lon: float, lat: float,
                  geometries: Iterable[Optional[Dict[str, Any]]],
                  buffer_km: float = 10.0
                  ) -> Tuple[bool, Optional[float], Optional[int]]:
    """(coperto, distanza_minima_km, indice_della_geometria_piu_vicina).

    Coperto = punto DENTRO un poligono oppure entro `buffer_km` dal bordo.
    `distanza_minima_km` e' None quando NESSUNA geometria ha il bbox entro
    `buffer_km` dal punto: il punto e' piu' lontano del buffer da tutto e la
    distanza esatta non viene calcolata (nessun valore approssimato)."""
    best_d = None
    best_i = None
    for idx, geom in enumerate(geometries):
        bbox = geometry_bbox(geom)
        if bbox is None:
            continue
        pad = buffer_km / max(1e-6, KM_PER_DEG_LAT * math.cos(math.radians(lat)))
        if not (bbox[0] - pad <= lon <= bbox[2] + pad
                and bbox[1] - pad <= lat <= bbox[3] + pad):
            continue
        if point_in_geometry(lon, lat, geom):
            return True, 0.0, idx
        d = distance_to_geometry_km(lon, lat, geom)
        if d is None:
            continue
        if best_d is None or d < best_d:
            best_d, best_i = d, idx
    if best_d is not None and best_d <= buffer_km:
        return True, best_d, best_i
    return False, best_d, best_i


def count_points_covered(points: Iterable[Sequence[float]],
                         geometries: Iterable[Optional[Dict[str, Any]]],
                         buffer_km: float = 10.0) -> Dict[str, Any]:
    """Quanti punti (lon, lat) cadono dentro o entro `buffer_km` dai poligoni."""
    geoms = [g for g in geometries if g]
    rows = []
    covered = 0
    for lon, lat in points:
        hit, dist, idx = point_covered(float(lon), float(lat), geoms, buffer_km)
        covered += 1 if hit else 0
        rows.append({"lon": round(float(lon), 5), "lat": round(float(lat), 5),
                     "covered": bool(hit),
                     "distance_km": (None if dist is None else round(dist, 3)),
                     "geometry_index": idx})
    n = len(rows)
    return {"n_points": n, "n_covered": covered,
            "fraction": (covered / float(n)) if n else None,
            "buffer_km": buffer_km, "points": rows}


# ---------------------------------------------------------------------------
# Griglia e rasterizzazione (rasterio, gia' dipendenza del motore)
# ---------------------------------------------------------------------------
def grid_spec(bounds: Sequence[float], res_deg: float
              ) -> Tuple[Any, Tuple[int, int], Tuple[float, float]]:
    """Affine + shape per una griglia geografica REGOLARE in EPSG:4326.

    bounds = (min_lon, min_lat, max_lon, max_lat). Ritorna (transform, (h, w),
    (res_lon_deg, res_lat_deg))."""
    from affine import Affine

    min_lon, min_lat, max_lon, max_lat = [float(v) for v in bounds]
    w = int(math.ceil((max_lon - min_lon) / float(res_deg)))
    h = int(math.ceil((max_lat - min_lat) / float(res_deg)))
    transform = Affine(float(res_deg), 0.0, min_lon,
                       0.0, -float(res_deg), max_lat)
    return transform, (h, w), (float(res_deg), float(res_deg))


def rasterize(geometries: Iterable[Optional[Dict[str, Any]]], transform: Any,
              shape: Tuple[int, int]):
    """Maschera booleana (h, w) dell'unione delle geometrie."""
    import numpy as np
    from rasterio.features import rasterize as _ras

    geoms = [g for g in geometries if g]
    if not geoms:
        return np.zeros(shape, dtype=bool)
    out = _ras([(g, 1) for g in geoms], out_shape=shape, transform=transform,
               fill=0, all_touched=False, dtype="uint8")
    return out.astype(bool)


def pixel_area_km2(transform: Any, shape: Tuple[int, int]):
    """Area (km^2) di ogni pixel: lat costante per riga, cos(lat) sulla riga."""
    import numpy as np

    h, w = shape
    res_lon = abs(float(transform.a))
    res_lat = abs(float(transform.e))
    lat_c = transform.f + (np.arange(h) + 0.5) * transform.e
    km_per_deg_lon = KM_PER_DEG_LAT * np.cos(np.radians(lat_c))
    row_area = res_lat * KM_PER_DEG_LAT * res_lon * km_per_deg_lon  # (h,)
    return np.repeat(row_area[:, None], w, axis=1)


def coverage_fraction(geometries: Iterable[Optional[Dict[str, Any]]],
                      region_mask: Any, transform: Any) -> Dict[str, Any]:
    """Frazione (per AREA) della regione coperta dai poligoni, sulla griglia."""
    import numpy as np

    shape = region_mask.shape
    cov = rasterize(geometries, transform, shape)
    area = pixel_area_km2(transform, shape)
    region_area = float(area[region_mask].sum())
    covered = float(area[cov & region_mask].sum())
    return {
        "region_area_km2": round(region_area, 3),
        "covered_area_km2": round(covered, 3),
        "fraction": (covered / region_area) if region_area > 0 else None,
    }
