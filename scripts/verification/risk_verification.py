#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JOB B — verifica previsione-vs-osservazione della mappa dei rischi (fascia ALL).

Unita' di verifica: (giorno di validita', fascia ``ALL``, provincia, pericolo).
Una riga per unita', scritta UNA volta sola, con i negativi obbligatori
(``pv = 0``). Niente metriche senza negativi (regola #233 del progetto).

PREVISIONE (input: snapshot di JOB A)
-------------------------------------
Per ogni (giorno, provincia, pericolo) si sceglie lo snapshot con il LEAD piu'
lungo disponibile **prima dell'inizio del giorno locale**: e' una previsione
vera. Se non esiste (primo giorno di registro) si usa l'ultimo snapshot del
giorno e ``ld`` diventa negativo (nowcast), dichiarato nel campo stesso.

OSSERVAZIONE (fascia ALL = 00:00-24:00 Europe/Rome)
---------------------------------------------------
* pioggia: stazioni (somma giornaliera con clipping degli intervalli
  sovrapposti) - copertura 107/107; radar SRT1 per provincia quando e'
  disponibile l'archivio compatto delle statistiche orarie
  (``--radar-province-dir``, scritto da
  ``scripts/radar_engine/srt1_province_archive.py``: 11.032 B/ora per 107
  province, nessuna griglia archiviata) oppure, in alternativa, una serie
  oraria di GeoTIFF (``--radar-grids-dir``). Senza nessuno dei due resta
  dichiarato ``radar_grid_not_embedded`` (``accumulation.json`` pubblica
  statistiche per macro-area, non la griglia). Con l'archivio il campo
  ``coverage.radar.reason`` vale ``ok`` e ogni riga di pioggia porta ``rd``
  (max mm della provincia) e ``rdc`` (copertura: pixel validi / pixel della
  provincia). Una provincia sotto la copertura minima NON riceve ``rd``:
  "radar cieco" non e' "0 mm misurati".
* vento: massimo raffica stazioni in m/s -> km/h (x3.6); fallback dichiarato
  sul vento medio (``src: stations_wind_mean_proxy``). Copertura reale 53/107.
* caldo/freddo: massimo/minimo ``temperature_2m`` della provincia.
* neve: profondita' neve osservata (proxy del manto TOTALE, non fresca).
* temporale: eventi HAIL/VORTEX e strutture convettive con centroide nella
  provincia (o entro 10 km dal bordo). Se il file non copre il giorno ->
  ``no_observation`` (assenza di archivio NON e' assenza di temporale).
* grandine: eventi HAIL oppure MESH >= 19 mm. In fase B la previsione e' null
  per costruzione -> ``no_observation`` con motivo.

ESITI: hit / miss / false_alarm / correct_negative / no_observation.
Metriche: POD, FAR (ratio), CSI, bias, HSS + intervallo di Wilson 95%
(``wilson_interval`` importato da ``scripts/validation/validate3d_metrics.py``).
``feasible`` solo con >= 60 positivi E >= 60 negativi; sotto soglia i valori
sono null e ``null_reason`` dice cosa manca — nessuna percentuale inventata.

USCITE
------
* ``risk_verification.jsonl`` (dettaglio, schema breve del progetto);
* ``risk_skill.json`` (aggregato permanente, da committare su main).

Uso:
  python scripts/verification/risk_verification.py --day 2026-11-22
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from validation.validate3d_metrics import wilson_interval  # noqa: E402
from verification.timeutil import (  # noqa: E402
    day_bounds_utc, epoch_ms, format_utc, local_day, now_utc, parse_utc,
    yesterday_local,
)

SCHEMA_VERSION = 1
BAND = "ALL"
HAZARDS = ("rain", "wind", "thunderstorm", "hail", "heat", "cold", "snow")
MIN_CASES = 60
DEFAULT_KEEP_DAYS = 30
DEFAULT_AGGREGATE_DAYS = 90
DEFAULT_AGGREGATE_RETENTION = 365
MATCH_RADIUS_KM = 10.0
# Una finestra di osservazione piu' corta di questa frazione del giorno NON
# puo' decretare hit/miss su una previsione giornaliera: diventa
# ``no_observation`` con motivo (mai un miss da 3 ore di dati).
MIN_WINDOW_FRACTION = 0.75

# Copertura radar minima perche' una provincia abbia una misura SRT1
# utilizzabile. E' la stessa soglia dichiarata dall'archivio
# (`srt1_province_archive.MIN_VALID_FRACTION`): qui e' RIPETUTA come costante
# locale per non creare una dipendenza del verificatore dal motore radar; se
# una delle due cambia, l'altra va allineata (test: test_radar_province_archive
# _threshold_matches_archive).
MIN_RADAR_VALID_FRACTION = 0.5

# Soglia per livello: quella che ha deciso il livello previsto. Per pv=0 si usa
# la soglia minima di rischio (sotto = correct_negative, sopra = false_alarm).
LEVEL_THRESHOLDS = {
    "rain": {0: 1.0, 1: 1.0, 2: 15.0, 3: 45.0, 4: 90.0},
    # wind: gambe R2 (50/75/100). Il livello 1 non esiste in R2; mappato 25 km/h
    # solo per snapshot di engine diversi.
    "wind": {0: 50.0, 1: 25.0, 2: 50.0, 3: 75.0, 4: 100.0},
    "heat": {0: 30.0, 1: 30.0, 2: 32.0, 3: 35.0, 4: 38.0},
    "cold": {0: 0.0, 1: 0.0, 2: -2.0, 3: -5.0, 4: -8.0},
    "snow": {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0},          # presenza
    "thunderstorm": {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0},  # binario
    "hail": {0: 19.0, 1: 19.0, 2: 19.0, 3: 19.0, 4: 19.0},
}
UNITS = {
    "rain": "mm", "wind": "km/h", "thunderstorm": "events",
    "hail": "mm", "heat": "degC", "cold": "degC", "snow": "m",
}
NOTES = (
    "Unita' di verifica: (giorno, fascia ALL, provincia, pericolo). "
    "pv=0 sempre registrato (negativi obbligatori). "
    "radar: accumulation.json non contiene la griglia -> SRT1 usato solo con --radar-grids-dir. "
    "vento: raffica non normalizzata a 10 min (B11041), usata solo come massimo. "
    "neve: snow_depth_m e' il manto totale, non la neve fresca. "
    "thunderstorm/hail: dipendono da file radar correnti o archiviati; senza archivio del giorno -> no_observation. "
    "Soglie 30/32/35/38 C e 0/-2/-5/-8 C sono SCELTA INGEGNERISTICA (non calibrate)."
)


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_json_optional(path):
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def _atomic_write(path, text):
    """Scrittura atomica (pattern di severe_predictions._atomic_write)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".riskver-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_jsonl(path, rows):
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) + "\n"
                   for r in rows)
    _atomic_write(path, text)
    return len(rows)


def read_jsonl(path):
    rows, corrupt = [], 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    corrupt += 1
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
                else:
                    corrupt += 1
    except FileNotFoundError:
        pass
    return rows, corrupt


# --------------------------------------------------------------------------- #
# Geometria: point-in-polygon + distanza dal bordo (tolleranza 10 km)
# --------------------------------------------------------------------------- #
def _ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def load_provinces(path):
    """[(sigla, [polygon, ...])] con polygon = [outer_ring, hole...]."""
    payload = read_json(path)
    out = []
    for feat in payload.get("features") or []:
        props = feat.get("properties") or {}
        sigla = str(props.get("SIGLA") or props.get("sigla") or "").strip().upper()
        if not sigla:
            continue
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "Polygon":
            polys = [coords]
        elif gtype == "MultiPolygon":
            polys = list(coords)
        else:
            continue
        rings = []
        for poly in polys:
            if not poly:
                continue
            rings.append([[(float(x), float(y)) for x, y in ring] for ring in poly])
        if rings:
            xs = [pt[0] for poly in rings for pt in poly[0]]
            ys = [pt[1] for poly in rings for pt in poly[0]]
            out.append({"sigla": sigla, "polys": rings,
                        "bbox": (min(xs), min(ys), max(xs), max(ys))})
    return out


def point_in_ring(lon, lat, ring) -> bool:
    """Ray casting; un punto sul bordo conta come dentro."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        # bordo
        if _point_on_segment(lon, lat, x1, y1, x2, y2, 1e-12):
            return True
        if (y1 > lat) != (y2 > lat):
            xin = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if xin > lon:
                inside = not inside
    return inside


def _point_on_segment(px, py, x1, y1, x2, y2, eps) -> bool:
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > eps:
        return False
    return (min(x1, x2) - eps <= px <= max(x1, x2) + eps
            and min(y1, y2) - eps <= py <= max(y1, y2) + eps)


def _seg_km(px, py, x1, y1, x2, y2):
    """Distanza punto-segmento in km (equirettangolare locale)."""
    kx = 111.320 * math.cos(math.radians(py))
    ky = 110.574
    ax, ay = x1 * kx, y1 * ky
    bx, by = x2 * kx, y2 * ky
    cx, cy = px * kx, py * ky
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((cx - ax) * dx + (cy - ay) * dy) / den))
    ex, ey = ax + t * dx, ay + t * dy
    return math.hypot(cx - ex, cy - ey)


def distance_to_poly_km(lon, lat, poly) -> float:
    best = float("inf")
    for ring in poly:
        for i in range(len(ring)):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % len(ring)]
            best = min(best, _seg_km(lon, lat, x1, y1, x2, y2))
    return best


def province_of_point(provs, lon, lat, tol_km=0.0):
    """Sigla della provincia che contiene il punto (o entro ``tol_km``)."""
    for prov in provs:
        x0, y0, x1, y1 = prov["bbox"]
        # margine in gradi ~ tolleranza km
        pad = tol_km / 80.0
        if not (x0 - pad <= lon <= x1 + pad and y0 - pad <= lat <= y1 + pad):
            continue
        for poly in prov["polys"]:
            outer = poly[0]
            if point_in_ring(lon, lat, outer) and not any(
                    point_in_ring(lon, lat, hole) for hole in poly[1:]):
                return prov["sigla"]
        if tol_km > 0:
            for poly in prov["polys"]:
                if distance_to_poly_km(lon, lat, poly) <= tol_km:
                    return prov["sigla"]
    return None


# --------------------------------------------------------------------------- #
# Stazioni
# --------------------------------------------------------------------------- #
def _coords_from_stations_payload(payload):
    out = {}
    stations = payload.get("stations") if isinstance(payload, dict) else payload
    if isinstance(stations, dict):
        items = stations.items()
        for sid, rec in items:
            if not isinstance(rec, dict):
                continue
            lat, lon = rec.get("lat"), rec.get("lon")
            if lat is not None and lon is not None:
                out[str(sid)] = (float(lat), float(lon))
        return out
    for rec in stations or []:
        if not isinstance(rec, dict):
            continue
        sid = rec.get("id")
        lat, lon = rec.get("lat"), rec.get("lon")
        if sid is not None and lat is not None and lon is not None:
            out[str(sid)] = (float(lat), float(lon))
    return out


def load_station_observations(path, index_path=None):
    """Ritorna (stazioni, formato, nota).

    Ogni stazione: {"id","lat","lon","points":[{"t":ms,"temp","wind","gust",
    "rain_rate","period","snow","rain_mm"}], "file_day"}.

    Supporta:
      * ``stations-daily-series/1`` (``pts`` a 11 posizioni) -> coordinate da
        ``--station-index`` (index.json/latest.json);
      * ``latest.json`` (una osservazione per stazione) -> coordinate incluse.
    """
    payload = read_json(path)
    fmt = "daily_series" if (isinstance(payload, dict)
                             and payload.get("schema") == "stations-daily-series/1") else "latest"
    out = []

    if fmt == "daily_series":
        coords = {}
        candidates = []
        if index_path:
            candidates.append(index_path)
        base = Path(path).parent
        candidates.extend([base / "index.json", base / "latest.json"])
        for cand in candidates:
            try:
                coords = _coords_from_stations_payload(read_json(cand))
            except (OSError, ValueError):
                coords = {}
            if coords:
                break
        if not coords:
            raise ValueError("station_coordinates_missing: serve --station-index")
        file_day = payload.get("day")
        for sid, rec in (payload.get("stations") or {}).items():
            if not isinstance(rec, dict):
                continue
            latlon = coords.get(str(sid))
            if not latlon:
                continue
            pts = []
            for tup in rec.get("pts") or []:
                if not isinstance(tup, (list, tuple)) or len(tup) < 7:
                    continue
                try:
                    pts.append({
                        "t": int(tup[0]),
                        "temp": _opt(tup[1]),
                        "wind": _opt(tup[3]),
                        "gust": _opt(tup[9]) if len(tup) > 9 else None,
                        "rain_rate": _opt(tup[5]),
                        "period": _opt(tup[6]),
                        "snow": _opt(tup[10]) if len(tup) > 10 else None,
                        "rain_mm": None,
                    })
                except (TypeError, ValueError):
                    continue
            pts.sort(key=lambda p: p["t"])
            out.append({"id": str(sid), "lat": latlon[0], "lon": latlon[1],
                        "points": pts, "file_day": file_day})
        return out, fmt, "series_day=%s" % file_day

    # latest.json
    for rec in payload.get("stations") or []:
        if not isinstance(rec, dict):
            continue
        lat, lon = rec.get("lat"), rec.get("lon")
        sid = rec.get("id")
        obs = rec.get("obs_time")
        if sid is None or lat is None or lon is None or not obs:
            continue
        try:
            t_ms = epoch_ms(parse_utc(obs))
        except ValueError:
            continue
        period = _opt(rec.get("precipitation_period_s"))
        precip = _opt(rec.get("precipitation"))
        out.append({"id": str(sid), "lat": float(lat), "lon": float(lon),
                    "points": [{
                        "t": t_ms,
                        "temp": _opt(rec.get("temperature_2m")),
                        "wind": _opt(rec.get("wind_speed_10m")),
                        "gust": _opt(rec.get("wind_gust_10m")),
                        "rain_rate": (precip * 3600.0 / period
                                      if precip is not None and period else None),
                        "period": period,
                        "snow": _opt(rec.get("snow_depth_m")),
                        "rain_mm": precip,
                    }],
                    "file_day": None})
    return out, fmt, "latest_snapshot"


def _opt(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def assign_stations(stations, provs):
    """Assegna ogni stazione a una provincia (point-in-polygon)."""
    by_prov = {}
    unassigned = 0
    for st in stations:
        sigla = province_of_point(provs, st["lon"], st["lat"], 0.0)
        if sigla is None:
            unassigned += 1
            continue
        st["sigla"] = sigla
        by_prov.setdefault(sigla, []).append(st)
    return by_prov, unassigned


def station_points_in_day(st, start_ms, end_ms):
    return [p for p in (st.get("points") or []) if start_ms <= p["t"] < end_ms]


def rain_total_mm(points, start_ms=None, end_ms=None):
    """Somma giornaliera con clipping degli intervalli sovrapposti.

    Ogni osservazione copre ``(t-period, t]``. Si somma solo la parte non
    gia' contata (``t - periodo`` tagliato a ``prev_t`` e all'inizio finestra):
    senza questo accorgimento un campionamento 10 min con periodo 900 s
    gonfierebbe la cumulata di ~50%. Il bordo sinistro della prima osservazione
    viene tagliato all'inizio del giorno.

    ``latest.json`` porta i mm gia' accumulati sulla finestra della sorgente
    (campo ``rain_mm``): si scala la frazione della finestra che cade nel giorno.
    """
    total = 0.0
    prev_t = None
    for p in sorted(points, key=lambda x: x["t"]):
        t = int(p["t"])
        period_ms = (p["period"] or 0.0) * 1000.0
        left = t - period_ms if period_ms > 0 else t
        if start_ms is not None and left < start_ms:
            left = start_ms
        if prev_t is not None and prev_t > left:
            left = prev_t
        right = t
        if end_ms is not None and right > end_ms:
            right = end_ms
        if right > left:
            if p.get("rain_mm") is not None:
                frac = 1.0
                if period_ms > 0:
                    frac = min(1.0, (right - left) / period_ms)
                total += p["rain_mm"] * frac
            else:
                rate = p.get("rain_rate")
                if rate is not None and period_ms > 0:
                    total += rate * (right - left) / 3600000.0
        prev_t = t if prev_t is None else max(prev_t, t)
    return total


# --------------------------------------------------------------------------- #
# Osservazioni per provincia / pericolo
# --------------------------------------------------------------------------- #
def _obs(ob, mv, src, n, nr=None, truncated=False, th=None):
    return {"ob": ob, "mv": mv, "src": src, "n": n, "nr": nr,
            "truncated": truncated, "th": th}


def _max_field(points, key):
    vals = [p[key] for p in points if p.get(key) is not None]
    return max(vals) if vals else None


def _min_field(points, key):
    vals = [p[key] for p in points if p.get(key) is not None]
    return min(vals) if vals else None


def province_station_obs(prov_stations, start_ms, end_ms, hazard, th,
                         truncated_day, series_day_mismatch=False,
                         window_ok=True):
    """Osservazione da stazioni per un pericolo, su una provincia."""
    if not prov_stations:
        return _obs(None, None, "stations", 0, "no_station_in_province", th=th)
    if not window_ok:
        return _obs(None, None, "stations", 0, "observation_window_truncated", th=th)
    points = []
    used_stations = 0
    for st in prov_stations:
        pts = station_points_in_day(st, start_ms, end_ms)
        if pts:
            used_stations += 1
            points.extend(pts)
    if not points:
        reason = ("daily_series_other_day" if series_day_mismatch
                  else "observation_too_old")
        return _obs(None, None, "stations", 0, reason, th=th)

    if hazard == "rain":
        # il massimo per provincia e' sulla stazione con piu' pioggia
        per_station = []
        for st in prov_stations:
            pts = station_points_in_day(st, start_ms, end_ms)
            if pts:
                per_station.append(rain_total_mm(pts, start_ms, end_ms))
        mv = max(per_station) if per_station else 0.0
        ob = 1 if mv >= th else 0
        return _obs(ob, round(mv, 3), "stations", used_stations,
                    truncated=truncated_day, th=th)

    if hazard == "wind":
        gust = _max_field(points, "gust")
        if gust is not None:
            mv = gust * 3.6
            return _obs(1 if mv >= th else 0, round(mv, 2), "stations", used_stations,
                        truncated=truncated_day, th=th)
        wind = _max_field(points, "wind")
        if wind is not None:
            mv = wind * 3.6
            return _obs(1 if mv >= th else 0, round(mv, 2),
                        "stations_wind_mean_proxy", used_stations,
                        truncated=truncated_day, th=th)
        return _obs(None, None, "stations", used_stations,
                    "variable_not_reported", truncated=truncated_day, th=th)

    if hazard == "heat":
        mv = _max_field(points, "temp")
        if mv is None:
            return _obs(None, None, "stations", used_stations,
                        "variable_not_reported", truncated=truncated_day, th=th)
        return _obs(1 if mv >= th else 0, round(mv, 2), "stations", used_stations,
                    truncated=truncated_day, th=th)

    if hazard == "cold":
        mv = _min_field(points, "temp")
        if mv is None:
            return _obs(None, None, "stations", used_stations,
                        "variable_not_reported", truncated=truncated_day, th=th)
        return _obs(1 if mv <= th else 0, round(mv, 2), "stations", used_stations,
                    truncated=truncated_day, th=th)

    if hazard == "snow":
        mv = _max_field(points, "snow")
        if mv is None:
            return _obs(None, None, "stations", used_stations,
                        "snow_not_reported", truncated=truncated_day, th=th)
        # snow_depth_m e' il manto TOTALE, non la neve fresca (dichiarato in src)
        return _obs(1 if mv > 0 else 0, round(mv, 3),
                    "stations_snow_depth_total_proxy", used_stations,
                    truncated=truncated_day, th=th)

    return _obs(None, None, "stations", used_stations, "hazard_not_supported", th=th)


def _event_window(event):
    try:
        first = parse_utc(event.get("first_seen"))
    except ValueError:
        first = None
    try:
        last = parse_utc(event.get("last_seen"))
    except ValueError:
        last = first
    return first, last


def convection_obs(sigla, provs, events_payload, structures_payload,
                   start, end, hazard, th):
    """Temporale/grandine da eventi e strutture convettive (centroide).

    Regola di copertura (nessun falso negativo da archivio mancante):
      * eventi (finestra 24 h) con ``generated_at`` nel giorno D ->
        copertura valida [00:00, gen], troncata se manca piu' di 30 min;
      * ``generated_at`` dopo D e ``gen-24h <= inizio`` -> copertura completa;
      * altrimenti gli eventi NON coprono D -> solo evidenza positiva dalle
        strutture, mai un ``correct_negative``.
    """
    events_gen = None
    events_covers = False
    events_full = False
    if isinstance(events_payload, dict):
        try:
            events_gen = parse_utc(events_payload.get("generated_at"))
        except (ValueError, TypeError):
            events_gen = None
        if events_gen is not None:
            window_h = float(events_payload.get("window_hours") or 24.0)
            if start <= events_gen < end:
                events_covers = True
            elif events_gen >= end and events_gen - timedelta(hours=window_h) <= start:
                events_covers = True
                events_full = True

    structures_gen = None
    if isinstance(structures_payload, dict):
        try:
            structures_gen = parse_utc(structures_payload.get("radar_timestamp")
                                       or structures_payload.get("generated_at"))
        except (ValueError, TypeError):
            structures_gen = None
    structures_covers = bool(structures_gen is not None
                             and start <= structures_gen < end)

    if not (events_covers or structures_covers):
        return _obs(None, None, "radar_events", 0,
                    "no_archived_observation_for_day", th=th)

    truncated = False
    if events_covers and not events_full and events_gen is not None:
        truncated = events_gen < end - timedelta(minutes=30)
    if structures_covers and structures_gen is not None and not events_covers:
        truncated = True

    hits = 0
    max_mesh = None
    for ev in (events_payload or {}).get("events") or []:
        etype = str(ev.get("type") or "").upper()
        if etype not in ("HAIL", "VORTEX"):
            continue
        first, last = _event_window(ev)
        if first is None or last is None:
            continue
        if not (first < end and last >= start):
            continue
        if hazard == "hail" and etype != "HAIL":
            continue
        pos = ev.get("position") or []
        if len(pos) < 2:
            continue
        if province_of_point(provs, float(pos[0]), float(pos[1]),
                             MATCH_RADIUS_KM) != sigla:
            continue
        hits += 1

    if hazard == "hail":
        for st in (structures_payload or {}).get("structures") or []:
            try:
                first = parse_utc(st.get("first_seen"))
                last = parse_utc(st.get("last_seen"))
            except (ValueError, TypeError):
                continue
            if not (first < end and last >= start):
                continue
            pos = st.get("position") or []
            if len(pos) < 2 or province_of_point(
                    provs, float(pos[0]), float(pos[1]), MATCH_RADIUS_KM) != sigla:
                continue
            mesh = (((st.get("severe_stack") or {}).get("hail_risk") or {})
                    .get("mesh") or {}).get("value")
            mesh = _opt(mesh)
            if mesh is None:
                continue
            max_mesh = mesh if max_mesh is None else max(max_mesh, mesh)
            if mesh >= th:
                hits += 1

    if hazard == "hail":
        if max_mesh is None and hits == 0 and not events_covers:
            # solo strutture (copertura di ~1 h): nessuna stima di grandine.
            return _obs(None, None, "meshw+events", 0,
                        "mesh_not_available", truncated=truncated, th=th)
        mv = max_mesh if max_mesh is not None else float(hits)
        src = "meshw+events" if max_mesh is not None else "radar_events"
        return _obs(1 if (hits > 0 or (max_mesh is not None and max_mesh >= th)) else 0,
                    round(mv, 2), src, hits, truncated=truncated, th=th)

    if hits > 0:
        return _obs(1, float(hits), "radar_events", hits,
                    truncated=truncated, th=th)
    if not events_covers:
        # strutture presenti ma nessun evento in un file che copre solo ~1 h:
        # non e' un negativo valido.
        return _obs(None, None, "radar_events", 0,
                    "no_archived_observation_for_day", truncated=truncated, th=th)
    return _obs(0, 0.0, "radar_events", 0, truncated=truncated, th=th)


# --------------------------------------------------------------------------- #
# Radar opzionale (serie oraria di GeoTIFF SRT1)
# --------------------------------------------------------------------------- #
def radar_context(radar_path):
    payload = read_json_optional(radar_path)
    if not isinstance(payload, dict):
        return {"available": False, "reason": "accumulation_json_missing"}
    slots = payload.get("slots") or []
    if not slots:
        return {"available": False, "reason": "accumulation_json_no_slots"}
    slot = slots[0]
    totals = slot.get("totals") or {}
    valid = totals.get("valid_px")
    total = totals.get("total_px")
    return {
        "available": False,  # la griglia NON e' nel JSON: dichiarato
        "reason": "radar_grid_not_embedded",
        "file": str(radar_path),
        "slot": slot.get("valid_time"),
        "note": ("accumulation.json pubblica statistiche per macro-area, non la "
                 "griglia: per provincia serve --radar-grids-dir (serie oraria "
                 "di GeoTIFF SRT1)."),
        "coverage_pct": round(100.0 * valid / total, 2) if valid and total else None,
        "max_mm_area": (slot.get("areas") or {}).get("Sud", {}).get("max_mm"),
    }


def radar_province_max(grids_dir, provs, start, end):
    """Massimo giornaliero SRT1 per provincia, da una serie di GeoTIFF orari.

    Ritorna {sigla: max_mm} oppure (None, motivo). Dipendenze opzionali
    (rasterio/numpy): se mancano si dichiara, non si inventa.
    """
    try:
        import numpy as np
        import rasterio
        from rasterio.features import rasterize
        from rasterio.warp import transform_geom
    except Exception as exc:  # pragma: no cover - dipende dal runner
        return None, f"rasterio_unavailable:{type(exc).__name__}"

    files = sorted(str(p) for p in Path(grids_dir).glob("*.tif"))
    if not files:
        return None, "radar_grids_empty"
    accum = None
    transform = None
    shape = None
    crs = None
    for path in files:
        with rasterio.open(path) as src:
            arr = src.read(1).astype("float64")
            nodata = src.nodata
            if nodata is not None:
                arr[arr == nodata] = np.nan
            arr[arr < -9000] = np.nan
            if accum is None:
                accum = arr
                transform = src.transform
                shape = (src.height, src.width)
                crs = src.crs
            elif arr.shape == accum.shape:
                accum = np.nansum(np.stack([accum, arr]), axis=0)
    if accum is None:
        return None, "radar_grids_unreadable"

    id_raster = np.zeros(shape, dtype="int32")
    sigle = []
    for idx, prov in enumerate(provs, start=1):
        geoms = []
        for poly in prov["polys"]:
            geom = {"type": "Polygon",
                    "coordinates": [[[x, y] for x, y in ring] for ring in poly]}
            try:
                geoms.append(transform_geom("EPSG:4326", crs, geom))
            except Exception:
                continue
        if not geoms:
            continue
        mask = rasterize(geoms, out_shape=shape, transform=transform,
                         fill=0, default_value=idx, dtype="int32")
        id_raster[mask == idx] = idx
        sigle.append((idx, prov["sigla"]))

    out = {}
    for idx, sigla in sigle:
        vals = accum[id_raster == idx]
        vals = vals[np.isfinite(vals)]
        if vals.size:
            out[sigla] = float(np.nanmax(vals))
    return out, None


def radar_province_archive(province_dir, start, end):
    """Massimo SRT1 per provincia dall'ARCHIVIO ORARIO delle statistiche.

    L'archivio e' scritto da `scripts/radar_engine/srt1_province_archive.py`
    (`<dir>/YYYY/MM/DD/HH.json`, una ora per file) e costa 11.032 B/ora invece
    dei 418.712-669.077 B di un GeoTIFF deflate (45,5x sul valore tipico): le
    stesse informazioni per provincia (massimo, media, p90, copertura) senza
    archiviare la griglia.

    Ritorna ``(by_prov, coverage, reason)`` dove:
      * ``by_prov`` = {sigla: max_mm} sui SOLI giorni/ore archiviati e sulle
        sole province con copertura sufficiente (None se nessuna);
      * ``coverage`` = {sigla: {"valid": frazione, "hours": n, "max": mm}};
      * ``reason`` = None se il dato c'e', altrimenti un motivo dichiarato.

    Una provincia con copertura sotto ``MIN_RADAR_VALID_FRACTION`` NON entra in
    ``by_prov``: il verificatore la tratta come "nessuna osservazione radar",
    mai come 0 mm misurati. La copertura cambia da slot a slot (misurato
    2026-11-23: 9 province siciliane sotto 0,5 alle 09:30Z; quasi tutte coperte
    alle 2026-11-13T19:10Z), quindi si legge ora per ora e non si assume.
    """
    root = Path(province_dir)
    if not root.is_dir():
        return None, {}, "radar_province_archive_missing"
    start_d = start.date()
    end_d = end.date()
    days = []
    cursor = start_d
    while cursor <= end_d:
        days.append(cursor)
        cursor += timedelta(days=1)

    hours_read = 0
    corrupt = 0
    per_prov = {}
    for day in days:
        day_dir = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if not day_dir.is_dir():
            continue
        for path in sorted(day_dir.glob("*.json")):
            payload = read_json_optional(path)
            if not isinstance(payload, dict):
                corrupt += 1
                continue
            provinces = payload.get("provinces")
            if not isinstance(provinces, dict) or not provinces:
                corrupt += 1
                continue
            hours_read += 1
            for sigla, st in provinces.items():
                if not isinstance(st, dict):
                    continue
                acc = per_prov.setdefault(
                    str(sigla), {"valid": 0.0, "hours": 0, "max": None,
                                 "wet": None})
                acc["hours"] += 1
                valid = _opt(st.get("valid"))
                if valid is not None:
                    acc["valid"] = max(acc["valid"], float(valid))
                mm = _opt(st.get("max"))
                if mm is not None and valid is not None \
                        and float(valid) >= MIN_RADAR_VALID_FRACTION:
                    acc["max"] = mm if acc["max"] is None else max(acc["max"], mm)
                wet = _opt(st.get("wet"))
                if wet is not None and (acc["wet"] is None or wet > acc["wet"]):
                    acc["wet"] = wet

    if not hours_read:
        reason = ("radar_province_archive_corrupt" if corrupt
                  else "radar_province_archive_empty_for_day")
        return None, {}, reason

    coverage = {sigla: {"valid": round(rec["valid"], 4),
                        "hours": rec["hours"],
                        "max": None if rec["max"] is None else round(rec["max"], 3),
                        "wet": None if rec["wet"] is None else round(rec["wet"], 4)}
                for sigla, rec in per_prov.items()}
    by_prov = {sigla: rec["max"] for sigla, rec in coverage.items()
               if rec["max"] is not None}
    if not by_prov:
        # Tutte le province archiviate sono fuori copertura radar.
        return None, coverage, "no_radar_coverage"
    return by_prov, coverage, None


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
def load_snapshots(path):
    rows, corrupt = read_jsonl(path)
    data = [r for r in rows if r.get("t") != "meta" and r.get("h") in HAZARDS]
    return data, corrupt


def pick_snapshots(rows, day):
    """Per (provincia, pericolo): ultima previsione prima dell'inizio del giorno.

    Se non esiste, l'ultima del giorno (nowcast): il lead risultante e' <= 0 e
    resta registrato in ``ld``.
    """
    start, _ = day_bounds_utc(day)
    start_ms = epoch_ms(start)
    grouped = {}
    for row in rows:
        if str(row.get("d")) != str(day):
            continue
        grouped.setdefault((str(row.get("p")), str(row.get("h"))), []).append(row)
    chosen = {}
    for key, cands in grouped.items():
        before = []
        after = []
        for row in cands:
            try:
                t = epoch_ms(parse_utc(row.get("ts")))
            except ValueError:
                continue
            (before if t <= start_ms else after).append((t, row))
        if before:
            before.sort(key=lambda x: x[0])
            chosen[key] = before[-1]
        elif after:
            after.sort(key=lambda x: x[0])
            chosen[key] = after[-1]
    return chosen


# --------------------------------------------------------------------------- #
# Esiti + metriche
# --------------------------------------------------------------------------- #
def outcome(pv, ob):
    if ob is None:
        return "no_observation"
    if pv is None:
        return "no_observation"
    pv = int(pv)
    if pv > 0 and ob == 1:
        return "hit"
    if pv > 0 and ob == 0:
        return "miss"
    if pv == 0 and ob == 1:
        return "false_alarm"
    return "correct_negative"


def threshold_for(hazard, pv):
    table = LEVEL_THRESHOLDS.get(hazard) or {}
    if pv is None:
        return table.get(1)
    return table.get(int(pv), table.get(1))


def empty_counts():
    return {"hits": 0, "misses": 0, "false_alarms": 0, "correct_negatives": 0,
            "no_observation": 0}


def counters_from_rows(rows):
    """Contatori per pericolo dalle righe di dettaglio."""
    out = {h: empty_counts() for h in HAZARDS}
    for row in rows:
        hazard = row.get("h")
        if hazard not in out:
            continue
        res = row.get("r")
        key = {"hit": "hits", "miss": "misses", "false_alarm": "false_alarms",
               "correct_negative": "correct_negatives",
               "no_observation": "no_observation"}.get(res)
        if key:
            out[hazard][key] += 1
    return out


def counters_from_daily(rows):
    """Contatori per pericolo dall'aggregato giornaliero (5 contatori)."""
    out = {h: empty_counts() for h in HAZARDS}
    for row in rows:
        hazard = row.get("h")
        if hazard not in out:
            continue
        dst = out[hazard]
        dst["hits"] += int(row.get("tp") or 0)
        dst["false_alarms"] += int(row.get("fp") or 0)
        dst["misses"] += int(row.get("fn") or 0)
        dst["correct_negatives"] += int(row.get("tn") or 0)
        dst["no_observation"] += int(row.get("no") or 0)
    return out


def merge_counts(*chunks):
    out = {h: empty_counts() for h in HAZARDS}
    for chunk in chunks:
        for hazard, counts in (chunk or {}).items():
            if hazard not in out:
                continue
            for key in out[hazard]:
                out[hazard][key] += int(counts.get(key) or 0)
    return out


def _metric_block(value, lo, hi, k, n):
    return {"value": None if value is None else round(value, 4),
            "lo": None if lo is None else round(lo, 4),
            "hi": None if hi is None else round(hi, 4),
            "k": int(k), "n": int(n)}


def hazard_metrics(counts, min_cases=MIN_CASES):
    hits = int(counts["hits"])
    misses = int(counts["misses"])
    fa = int(counts["false_alarms"])
    cn = int(counts["correct_negatives"])
    no = int(counts["no_observation"])
    pos = hits + misses
    neg = fa + cn
    total = pos + neg
    reasons = []
    if pos < int(min_cases):
        reasons.append("positive_labeled=%d<%d" % (pos, min_cases))
    if neg < int(min_cases):
        reasons.append("negative_labeled=%d<%d" % (neg, min_cases))
    feasible = not reasons

    pod = hits / pos if pos else None
    pod_lo, pod_hi = wilson_interval(hits, pos)
    far = fa / (hits + fa) if (hits + fa) else None
    far_lo, far_hi = wilson_interval(fa, hits + fa)
    csi_den = hits + misses + fa
    csi = hits / csi_den if csi_den else None
    csi_lo, csi_hi = wilson_interval(hits, csi_den)
    bias = (hits + fa) / pos if pos else None
    hss = None
    if total:
        observed = (hits + cn) / total
        expected = (((hits + fa) * (hits + misses)
                     + (cn + misses) * (cn + fa)) / float(total * total))
        if abs(1.0 - expected) > 1e-12:
            hss = (observed - expected) / (1.0 - expected)

    if feasible:
        pod_block = _metric_block(pod, pod_lo, pod_hi, hits, pos)
        far_block = _metric_block(far, far_lo, far_hi, fa, hits + fa)
        csi_block = _metric_block(csi, csi_lo, csi_hi, hits, csi_den)
    else:
        pod_block = _metric_block(None, None, None, hits, pos)
        far_block = _metric_block(None, None, None, fa, hits + fa)
        csi_block = _metric_block(None, None, None, hits, csi_den)

    return {
        "n": total,
        "hits": hits, "misses": misses,
        "false_alarms": fa, "correct_negatives": cn,
        "no_observation": no,
        "pod": pod_block,
        "far": far_block,
        "csi": csi_block,
        # Sotto il gate 60/60 nessuna metrica e' pubblicata: i conteggi restano.
        "bias": None if (bias is None or not feasible) else round(bias, 4),
        "hss": None if (hss is None or not feasible) else round(hss, 4),
        "feasible": feasible,
        "null_reason": None if feasible else "; ".join(reasons),
        "definitions": {
            "pod": "hits/(hits+misses)",
            "far": "false_alarms/(hits+false_alarms) — False Alarm RATIO",
            "csi": "hits/(hits+misses+false_alarms)",
            "csi_ci": "Wilson applicato alla proporzione hits/(hits+misses+FA): approssimazione dichiarata",
        },
    }


def build_skill_payload(counts, detail_rows, daily_rows, generated_at,
                        engine_version, coverage=None, notes=NOTES):
    """Payload di ``risk_skill.json`` dai contatori gia' calcolati."""
    days = sorted({str(r.get("d")) for r in (detail_rows or []) if r.get("d")})
    days += sorted({str(r.get("d")) for r in (daily_rows or []) if r.get("d")})
    days = sorted(set(days))
    hazards = {}
    for hazard in HAZARDS:
        hazards[hazard] = hazard_metrics(counts.get(hazard) or empty_counts())
    return {
        "schema": "risk-skill/1",
        "generated_at": generated_at,
        "window": {
            "from": days[0] if days else None,
            "to": days[-1] if days else None,
            "days": len(days),
            "band": BAND,
        },
        "engine_version": engine_version,
        "min_cases": MIN_CASES,
        "hazards": hazards,
        "coverage": coverage or {},
        "notes": notes,
        "v": SCHEMA_VERSION,
    }


def coverage_from_rows(rows, stations_assigned, stations_unassigned, radar, afa):
    """Copertura osservata: la misura che dice DOVE il registro e' cieco."""
    per_hazard = {}
    for hazard in HAZARDS:
        subset = [r for r in rows if r.get("h") == hazard]
        reasons = {}
        observed = 0
        for row in subset:
            if row.get("ob") is None:
                reason = row.get("nr") or "unknown"
                reasons[reason] = reasons.get(reason, 0) + 1
            else:
                observed += 1
        per_hazard[hazard] = {
            "rows": len(subset),
            "with_observation": observed,
            "no_observation": len(subset) - observed,
            "reasons": dict(sorted(reasons.items())),
        }
    return {
        "stations_assigned": int(stations_assigned),
        "stations_unassigned": int(stations_unassigned),
        "hazards": per_hazard,
        "radar": radar or {},
        "afa": afa or {},
    }


def afa_context(manifest_path):
    payload = read_json_optional(manifest_path)
    if not isinstance(payload, dict):
        return {"available": False, "reason": "satellite_manifest_missing"}
    afa = (payload.get("sources") or {}).get("afa")
    meta = payload.get("afa_meta") or {}
    if not afa and not meta:
        return {"available": False, "reason": "afa_not_in_manifest"}
    slots = meta if isinstance(meta, dict) else {}
    with_signal = sum(1 for v in slots.values()
                      if isinstance(v, dict) and v.get("has_signal"))
    active = [v.get("active_pixels") for v in slots.values()
              if isinstance(v, dict) and isinstance(v.get("active_pixels"), (int, float))]
    return {
        "available": True,
        "slots": len(slots),
        "slots_with_signal": with_signal,
        "max_active_pixels": max(active) if active else None,
        "note": ("AFA e' un proxy di area (non conteggio di scariche) e non e' "
                 "georeferenziata per provincia in questo registro: usata solo "
                 "come contesto, mai come osservazione provinciale."),
    }


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def verify_day(day, snapshots_path, stations_path, station_index, radar_path,
               structures_path, events_path, afa_path, province_geojson,
               now=None, radar_grids_dir=None, radar_province_dir=None):
    start, end = day_bounds_utc(day)
    start_ms, end_ms = epoch_ms(start), epoch_ms(end)

    provs = load_provinces(province_geojson)
    stations, station_format, station_note = load_station_observations(
        stations_path, station_index)
    by_prov, unassigned = assign_stations(stations, provs)

    snap_rows, snap_corrupt = load_snapshots(snapshots_path)
    chosen = pick_snapshots(snap_rows, day)

    events_payload = read_json_optional(events_path)
    structures_payload = read_json_optional(structures_path)
    radar = radar_context(radar_path)

    radar_by_prov = None
    radar_province_coverage = {}
    if radar_province_dir:
        # ARCHIVIO COMPATTO per provincia (preferito): statistiche orarie SRT1
        # scritte dal motore, 11.032 B/ora, nessuna griglia archiviata.
        radar_by_prov, radar_province_coverage, radar_reason = \
            radar_province_archive(radar_province_dir, start, end)
        radar = dict(radar)
        if radar_by_prov is None:
            radar["reason"] = radar_reason
            radar["archive"] = str(radar_province_dir)
        else:
            blind = sorted(s for s, c in radar_province_coverage.items()
                           if (c.get("valid") or 0.0) < MIN_RADAR_VALID_FRACTION)
            radar.update({
                "available": True,
                "reason": "ok",
                "status": "ok",
                "source": "srt1_province_archive",
                "used_for_provinces": len(radar_by_prov),
                "provinces_archived": len(radar_province_coverage),
                "provinces_without_coverage": blind,
                "min_valid_fraction": MIN_RADAR_VALID_FRACTION,
                "archive": str(radar_province_dir),
                "note": ("SRT1 letto dall'archivio compatto per provincia "
                         "(statistiche orarie): il massimo per provincia e' "
                         "registrato in `rd`, la copertura in `rdc`. Le "
                         "province sotto la copertura minima restano SENZA "
                         "osservazione radar (mai 0 mm). La fonte primaria "
                         "delle stazioni resta invariata."),
            })
    elif radar_grids_dir:
        radar_by_prov, radar_reason = radar_province_max(radar_grids_dir, provs, start, end)
        if radar_by_prov is None:
            radar = dict(radar)
            radar["reason"] = radar_reason
        else:
            radar = dict(radar)
            radar.update({
                "available": True,
                "reason": "ok",
                "status": "ok",
                "source": "srt1_geotiff_series",
                "used_for_provinces": len(radar_by_prov),
                "grids_dir": str(radar_grids_dir),
                "note": ("SRT1 integrato dalla serie oraria di GeoTIFF: il "
                         "massimo per provincia e' registrato in `rd`, la fonte "
                         "delle stazioni resta la primaria."),
            })

    # Copertura del file stazioni rispetto al giorno:
    #  * daily_series: se il blob e' di un altro giorno -> mismatch dichiarato;
    #  * latest.json: finestra di 180 min -> troncato per costruzione.
    truncated_day = False
    series_day_mismatch = False
    if station_format == "latest":
        truncated_day = True
    else:
        file_days = {str(st.get("file_day")) for st in stations if st.get("file_day")}
        if file_days and str(day) not in file_days:
            series_day_mismatch = True
            truncated_day = False

    # Copertura temporale effettiva: una finestra corta (es. latest.json, 180
    # minuti) non basta a verificare un giorno intero -> no_observation.
    all_pts = []
    for st in stations:
        all_pts.extend(station_points_in_day(st, start_ms, end_ms))
    if all_pts:
        span_ms = (max(p["t"] for p in all_pts) - min(p["t"] for p in all_pts))
        coverage_frac = span_ms / float(max(1, end_ms - start_ms))
    else:
        coverage_frac = 0.0
    window_ok = coverage_frac >= MIN_WINDOW_FRACTION
    if window_ok and coverage_frac < 0.95:
        truncated_day = True

    rows = []
    for (sigla, hazard), picked in sorted(chosen.items()):
        t_ms, snap = picked
        pv = snap.get("pv")
        pm = snap.get("pm")
        lead_h = (start_ms - t_ms) / 3600000.0
        th = threshold_for(hazard, pv)
        prov_stations = by_prov.get(sigla) or []

        if hazard in ("thunderstorm", "hail"):
            obs = convection_obs(sigla, provs, events_payload, structures_payload,
                                 start, end, hazard, th)
        else:
            obs = province_station_obs(prov_stations, start_ms, end_ms, hazard,
                                       th, truncated_day, series_day_mismatch,
                                       window_ok)

        # radar come fonte INDIPENDENTE per la pioggia: non sostituisce le
        # stazioni, si registra il disaccordo nel campo additivo `rd` (mm).
        radar_mm = None
        if hazard == "rain" and radar_by_prov is not None:
            radar_mm = radar_by_prov.get(sigla)

        ob = obs["ob"]
        if pv is None:
            nr = "forecast_null:" + str(snap.get("nr") or "unknown")
            ob = None
        else:
            nr = obs["nr"]
        res = outcome(pv, ob)

        row = {
            "d": str(day),
            "s": BAND,
            "p": sigla,
            "h": hazard,
            "pv": pv,
            "pm": pm,
            "ob": ob,
            "mv": obs["mv"],
            "u": UNITS.get(hazard),
            "th": th,
            "src": obs["src"],
            "r": res,
            "ld": round(lead_h, 2),
            "n": int(obs["n"]),
            "nr": nr,
            "ev": snap.get("ev"),
            "ls": snap.get("ls") or "r2_from_provinces",
            "v": SCHEMA_VERSION,
        }
        if radar_mm is not None:
            row["rd"] = round(radar_mm, 2)
            _cov = radar_province_coverage.get(sigla) or {}
            if _cov.get("valid") is not None:
                row["rdc"] = _cov["valid"]
        if obs.get("truncated"):
            row["tr"] = True
        rows.append(row)

    summary = {
        "day": str(day),
        "rows": len(rows),
        "provinces": len({r["p"] for r in rows}),
        "hazards": sorted({r["h"] for r in rows}),
        "snapshots": len(snap_rows),
        "snapshots_corrupt": snap_corrupt,
        "stations": len(stations),
        "station_format": station_format,
        "station_note": station_note,
        "stations_assigned": len(stations) - unassigned,
        "stations_unassigned": unassigned,
        "station_window_coverage": round(coverage_frac, 4),
        "station_window_ok": bool(window_ok),
        "start_utc": format_utc(start),
        "end_utc": format_utc(end),
        "radar": radar,
    }
    return rows, summary, (len(stations) - unassigned, unassigned, radar,
                           afa_context(afa_path))


def merge_detail(existing_rows, new_rows, day):
    """Idempotenza: sostituisce le righe del giorno verificato."""
    kept = [r for r in existing_rows if str(r.get("d")) != str(day)]
    return kept + new_rows


def run(args) -> dict:
    now = now_utc(args.now)
    day = args.day or yesterday_local(now)
    row_day = date.fromisoformat(str(day)).isoformat()

    rows, summary, cov = verify_day(
        row_day,
        args.snapshots,
        args.stations,
        args.station_index,
        args.radar,
        args.structures,
        args.events,
        args.afa,
        args.province_geojson,
        now=now,
        radar_grids_dir=args.radar_grids_dir,
        radar_province_dir=args.radar_province_dir,
    )

    existing, corrupt_existing = read_jsonl(args.out)
    detail = merge_detail(existing, rows, row_day)
    detail.sort(key=lambda r: (str(r.get("d")), str(r.get("p")), str(r.get("h"))))
    write_jsonl(args.out, detail)

    detail_rows_for_skill = [r for r in detail if r.get("r") is not None]
    counts = counters_from_rows(detail_rows_for_skill)
    generated_at = format_utc(now)
    engine_versions = [r.get("ev") for r in detail_rows_for_skill if r.get("ev")]
    engine_version = max(sorted(set(engine_versions)), key=engine_versions.count) \
        if engine_versions else None
    coverage = coverage_from_rows(detail_rows_for_skill, cov[0], cov[1], cov[2], cov[3])
    skill = build_skill_payload(counts, detail_rows_for_skill, [], generated_at,
                                engine_version, coverage)
    _atomic_write(args.skill_out, json.dumps(skill, ensure_ascii=False,
                                             sort_keys=True, indent=2) + "\n")

    return {
        "day": row_day,
        "rows": len(rows),
        "detail_rows_total": len(detail),
        "replaced_day_rows": len([r for r in existing if str(r.get("d")) == row_day]),
        "corrupt_existing": corrupt_existing,
        "summary": summary,
        "out": args.out,
        "skill_out": args.skill_out,
        "feasible": {h: skill["hazards"][h]["feasible"] for h in HAZARDS},
    }


def build_parser():
    root = _HERE.parents[2]
    ap = argparse.ArgumentParser(
        description="JOB B: verifica previsione-vs-osservazione (fascia ALL).")
    ap.add_argument("--snapshots",
                    default=str(root / "data/state/risk_forecast_snapshots.jsonl"))
    ap.add_argument("--stations", default=str(root / "data/stations/daily_series.json"))
    ap.add_argument("--station-index", default=str(root / "data/stations/index.json"))
    ap.add_argument("--radar", default=str(root / "data/radar/accumulation.json"))
    ap.add_argument("--radar-grids-dir", default=None,
                    help="serie oraria di GeoTIFF SRT1 per integrare la pioggia (opzionale)")
    ap.add_argument("--radar-province-dir", default=None,
                    help="archivio compatto SRT1 per provincia "
                         "(data/radar/srt1_provinces, YYYY/MM/DD/HH.json): "
                         "preferito a --radar-grids-dir, 45,5x piu' piccolo")
    ap.add_argument("--structures", default=str(root / "data/radar/convective_structures.json"))
    ap.add_argument("--events", default=str(root / "data/phenomena/events.json"))
    ap.add_argument("--afa", default=str(root / "satellite/manifest.json"))
    ap.add_argument("--province-geojson",
                    default=str(root / "data/geography/province_italiane.geojson"))
    ap.add_argument("--out", default=str(root / "data/state/risk_verification.jsonl"))
    ap.add_argument("--skill-out", default=str(root / "data/state/risk_skill.json"))
    ap.add_argument("--day", default=None,
                    help="giorno di validita' (locale); default: ieri")
    ap.add_argument("--now", default=None, help="istante ISO-8601 (default: adesso)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[risk_verification] ERRORE: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
