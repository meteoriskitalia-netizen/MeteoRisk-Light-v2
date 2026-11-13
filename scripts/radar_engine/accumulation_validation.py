#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — accumulation_validation.py

TRIANGOLAZIONE dell'accumulo Radar-DPC SRT1 (mm su 1 h) con le centraline
MeteoHub (`data/stations/latest.json`, campo `precipitation` in mm e
`precipitation_period_s` in secondi).

METODO (dichiarato, nessuna soglia):
  1. ogni stazione con `precipitation` e `precipitation_period_s` validi viene
     normalizzata a 1 h:  mm_1h = precipitation * 3600 / precipitation_period_s
     (IPOTESI: intensita' costante nel periodo di accumulo della stazione);
  2. si usano le stazioni con `obs_time` entro [-5, +tolerance] minuti dal
     `valid_time` del prodotto SRT1;
  3. campione DETERMINISTICO di al massimo N stazioni (ordinate per id, passo
     uniforme): due esecuzioni sugli stessi input danno le stesse coppie;
  4. il valore radar e' il pixel piu' vicino (nearest, 1 km) della griglia
     SRT1, dopo reproiezione del punto in EPSG:4326 -> CRS del prodotto;
  5. si riportano n coppie, bias (radar - stazione), MAE, RMSE, correlazione
     di Pearson, distribuzione degli scarti (p10/p25/p50/p75/p90/min/max) e
     la scomposizione per classe di `precipitation_period_s`, con in piu' il
     sottoinsieme a finestra ESATTA (period_s == 3600, nessuna estrapolazione).

LIMITI DICHIARATI (nel payload, non solo qui):
  - SRT1 integra GIA' una rete pluviometrica (documentazione DPC): la
    triangolazione non e' al 100% indipendente e la rete MeteoHub puo'
    sovrapporsi parzialmente a quella usata da DPC;
  - periodo di accumulo variabile per rete (60-3600 s) -> la normalizzazione
    a 1 h e' un'estrapolazione, non una misura della stessa finestra;
  - latenza delle stazioni variabile (max_age_minutes 180 nel file sorgente);
  - rappresentativita' spaziale: punto (stazione) contro pixel di 1 km;
  - `precipitation` della sorgente e' arrotondata a 1 decimale (0,1 mm).

Nessuna parola di allerta, nessuna soglia: solo millimetri e statistiche.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os

import numpy as np

DEFAULT_WINDOW_S = 3600
DEFAULT_MAX_STATIONS = 200
DEFAULT_TOLERANCE_S = 2700
OBS_FUTURE_TOLERANCE_S = 300

_TRANSFORMERS = {}


class ValidationError(Exception):
    """Errore dichiarato della triangolazione (nessun valore inventato)."""


def iso_utc(ms):
    return _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def parse_obs_ms(text):
    """`2026-11-13T19:00:00Z` -> epoch ms (naive assunto UTC)."""
    s = str(text).strip()
    if not s:
        raise ValidationError("obs_time_empty")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValidationError(f"obs_time_invalid:{text}") from exc
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return int(d.timestamp() * 1000)


def normalize_period_mm(precip_mm, period_s, window_s=DEFAULT_WINDOW_S):
    """Accumulo del periodo -> accumulo sulla finestra (mm).

    mm_finestra = mm_periodo * window_s / period_s.
    Ritorna None se il valore manca; solleva ValidationError se il periodo non
    e' positivo (un periodo nullo non e' normalizzabile).
    """
    if precip_mm is None or period_s is None:
        return None
    try:
        value = float(precip_mm)
        period = float(period_s)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"non_numeric:precip={precip_mm!r},period={period_s!r}") from exc
    if not math.isfinite(period) or period <= 0:
        raise ValidationError(f"period_s_non_positivo:{period_s!r}")
    if not math.isfinite(value):
        return None
    return value * float(window_s) / period


def load_stations(path):
    if not os.path.isfile(path):
        raise ValidationError(f"stations_file_missing:{path}")
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"stations_file_unreadable:{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(
            payload.get("stations"), list):
        raise ValidationError("stations_schema_invalid")
    return payload


def station_candidates(stations_payload, valid_time_ms,
                       tolerance_s=DEFAULT_TOLERANCE_S,
                       window_s=DEFAULT_WINDOW_S):
    """Stazioni con accumulo normalizzabile e obs_time nella finestra."""
    out = []
    for st in stations_payload.get("stations") or []:
        if not isinstance(st, dict):
            continue
        lat, lon = st.get("lat"), st.get("lon")
        if lat is None or lon is None:
            continue
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        try:
            obs_ms = parse_obs_ms(st.get("obs_time"))
        except ValidationError:
            continue
        age_s = (int(valid_time_ms) - obs_ms) / 1000.0
        if age_s < -OBS_FUTURE_TOLERANCE_S or age_s > float(tolerance_s):
            continue
        try:
            mm_1h = normalize_period_mm(
                st.get("precipitation"), st.get("precipitation_period_s"),
                window_s=window_s)
        except ValidationError:
            continue
        if mm_1h is None:
            continue
        out.append({
            "id": st.get("id"),
            "network": st.get("network"),
            "lat": lat_f,
            "lon": lon_f,
            "obs_time": st.get("obs_time"),
            "obs_age_s": int(round(age_s)),
            "precipitation_mm": float(st.get("precipitation")),
            "precipitation_period_s": int(st.get("precipitation_period_s")),
            "station_mm_1h": mm_1h,
        })
    return out


def sample_deterministic(candidates, max_n=DEFAULT_MAX_STATIONS):
    """Campione riproducibile: ordina per id, passo uniforme."""
    n = int(max_n)
    if n <= 0 or not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: str(c.get("id") or ""))
    if len(ordered) <= n:
        return list(ordered)
    step = len(ordered) / float(n)
    picked = [ordered[int(i * step)] for i in range(n)]
    return picked


def _pixel_value(raster, lon, lat):
    """Valore del pixel nearest in EPSG:4326; None se fuori griglia/nodata."""
    try:
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover - dipendenza del motore
        raise ValidationError("pyproj_missing") from exc
    key = str(raster.crs)
    transformer = _TRANSFORMERS.get(key)
    if transformer is None:
        try:
            wkt = raster.crs.to_wkt() if hasattr(raster.crs, "to_wkt") else str(
                raster.crs)
            transformer = Transformer.from_crs(
                "EPSG:4326", wkt, always_xy=True)
        except Exception as exc:
            raise ValidationError(f"crs_not_transformable:{exc}") from exc
        _TRANSFORMERS[key] = transformer
    x, y = transformer.transform(float(lon), float(lat))
    try:
        from rasterio.transform import rowcol
        row_i, col_i = rowcol(raster.transform, x, y)
        row_i, col_i = int(row_i), int(col_i)
    except Exception:  # pragma: no cover - fallback affine
        col, row = ~raster.transform * (x, y)
        row_i = int(math.floor(row))
        col_i = int(math.floor(col))
    if row_i < 0 or col_i < 0 or row_i >= raster.height or col_i >= raster.width:
        return None
    if not bool(raster.valid_mask[row_i, col_i]):
        return None
    return float(raster.values[row_i, col_i]), row_i, col_i


def build_pairs(candidates, raster):
    pairs = []
    excluded = 0
    for c in candidates:
        sampled = _pixel_value(raster, c["lon"], c["lat"])
        if sampled is None:
            excluded += 1
            continue
        value, row, col = sampled
        pairs.append(dict(c, radar_mm=value, row=row, col=col,
                          diff_mm=value - c["station_mm_1h"]))
    return pairs, excluded


def _round(value, ndigits):
    return None if value is None else round(float(value), ndigits)


def _pearson(x, y):
    if len(x) < 3:
        return None
    sx = float(np.std(x))
    sy = float(np.std(y))
    if sx == 0.0 or sy == 0.0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def summarize(pairs):
    """Statistiche delle coppie (radar - stazione)."""
    n = len(pairs)
    if not n:
        return {"n": 0, "bias_mm": None, "mae_mm": None, "rmse_mm": None,
                "pearson_r": None, "diff_p10_mm": None, "diff_p25_mm": None,
                "diff_p50_mm": None, "diff_p75_mm": None, "diff_p90_mm": None,
                "diff_min_mm": None, "diff_max_mm": None,
                "radar_mean_mm": None, "station_mean_mm": None}
    radar = np.asarray([p["radar_mm"] for p in pairs], dtype="float64")
    station = np.asarray([p["station_mm_1h"] for p in pairs], dtype="float64")
    diff = radar - station
    p10, p25, p50, p75, p90 = np.percentile(diff, [10, 25, 50, 75, 90])
    return {
        "n": n,
        "bias_mm": _round(diff.mean(), 3),
        "mae_mm": _round(np.abs(diff).mean(), 3),
        "rmse_mm": _round(np.sqrt((diff ** 2).mean()), 3),
        "pearson_r": _round(_pearson(radar, station), 4),
        "diff_p10_mm": _round(p10, 3),
        "diff_p25_mm": _round(p25, 3),
        "diff_p50_mm": _round(p50, 3),
        "diff_p75_mm": _round(p75, 3),
        "diff_p90_mm": _round(p90, 3),
        "diff_min_mm": _round(diff.min(), 3),
        "diff_max_mm": _round(diff.max(), 3),
        "radar_mean_mm": _round(radar.mean(), 3),
        "station_mean_mm": _round(station.mean(), 3),
    }


def _period_class(period_s):
    return str(int(period_s))


def by_period_class(pairs):
    groups = {}
    for p in pairs:
        groups.setdefault(_period_class(p["precipitation_period_s"]), []).append(p)
    out = {}
    for key in sorted(groups, key=lambda k: int(k)):
        item = summarize(groups[key])
        item["period_s"] = int(key)
        out[key] = item
    return out


def build_validation_payload(stations_payload, raster, valid_time_ms,
                             window_s=DEFAULT_WINDOW_S,
                             max_stations=DEFAULT_MAX_STATIONS,
                             tolerance_s=DEFAULT_TOLERANCE_S,
                             generated_at=None):
    candidates = station_candidates(
        stations_payload, valid_time_ms, tolerance_s=tolerance_s,
        window_s=window_s)
    used = sample_deterministic(candidates, max_stations)
    pairs, excluded = build_pairs(used, raster)
    metrics = summarize(pairs)
    exact = [p for p in pairs if int(p["precipitation_period_s"]) == int(window_s)]
    ages = [p["obs_age_s"] for p in pairs]
    period_counts = {}
    for p in pairs:
        key = _period_class(p["precipitation_period_s"])
        period_counts[key] = period_counts.get(key, 0) + 1

    generated_at = generated_at or iso_utc(int(_dt.datetime.now(
        _dt.timezone.utc).timestamp() * 1000))
    age_p50 = _round(np.percentile(ages, 50), 1) if ages else None
    age_p90 = _round(np.percentile(ages, 90), 1) if ages else None
    limits = [
        "SRT1 integra gia' una rete pluviometrica (documentazione DPC): la "
        "triangolazione non e' al 100% indipendente; la rete MeteoHub puo' "
        "sovrapporsi parzialmente a quella usata da DPC.",
        "Periodo di accumulo variabile per rete (60-3600 s): la "
        "normalizzazione a 1 h assume intensita' costante e non e' una "
        "misura della stessa finestra.",
        "Latenza delle stazioni variabile: obs_time - valid_time p50 %.0f s, "
        "p90 %.0f s sulle coppie usate (file sorgente con max_age_minutes "
        "180)." % ((age_p50 or 0.0), (age_p90 or 0.0)),
        "Rappresentativita' spaziale: la stazione e' un punto, SRT1 un pixel "
        "di 1 km; il confronto usa il pixel piu' vicino (nearest).",
        "`precipitation` della sorgente e' arrotondata a 1 decimale: "
        "quantizzazione 0,1 mm sul valore di stazione.",
        "Nessuna soglia meteorologica: solo millimetri e statistiche.",
    ]
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "product": "SRT1",
        "valid_time": iso_utc(valid_time_ms),
        "valid_time_ms": int(valid_time_ms),
        "window_h": round(float(window_s) / 3600.0, 3),
        "method": {
            "station_accumulation_mm": (
                "precipitation * window_s / precipitation_period_s"),
            "assumption": "intensita' costante nel periodo della stazione",
            "radar_sampling": "pixel piu' vicino (nearest), griglia 1 km",
            "time_match": "obs_time in [-%d s, +%d s] dal valid_time" % (
                OBS_FUTURE_TOLERANCE_S, int(tolerance_s)),
            "sample": ("deterministico: stazioni ordinate per id, passo "
                       "uniforme, massimo %d" % int(max_stations)),
        },
        "stations": {
            "file": "data/stations/latest.json",
            "generated_at": stations_payload.get("generated_at"),
            "n_total": len(stations_payload.get("stations") or []),
            "n_with_precipitation_and_period": len([
                s for s in (stations_payload.get("stations") or [])
                if isinstance(s, dict)
                and s.get("precipitation") is not None
                and s.get("precipitation_period_s") is not None]),
            "n_time_window_candidates": len(candidates),
            "n_sampled": len(used),
            "n_pairs": len(pairs),
            "n_excluded_pixel_outside_coverage_or_nodata": excluded,
            "period_s_counts": period_counts,
            "obs_age_s_p50": age_p50,
            "obs_age_s_p90": age_p90,
        },
        "metrics": metrics,
        "by_period_class": by_period_class(pairs),
        "exact_window_1h": summarize(exact),
        "limits": limits,
    }
