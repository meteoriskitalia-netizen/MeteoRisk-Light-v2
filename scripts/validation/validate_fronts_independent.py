#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validazione INDIPENDENTE dei fronti sinottici — POD/FAR/CSI su verita' METAR.

PERCHE' ESISTE
--------------
`score_calibration.py:74-77` definiva il "positivo" come
``hf > heavy_median`` dove ``hf`` e' la frazione di griglia con
``rain+showers > 2 mm/h`` (`calibrate.py:79-93`), cioe' LA STESSA variabile
di pioggia del modello usata come predittore: etichetta circolare. Questo
modulo NON usa quella regola. La verita' e' il bollettino METAR/SPECI
osservato (NOAA/NCEI via IEM, pubblico dominio), con orario e posizione.

DISEGNO DICHIARATO A PRIORI (prima di leggere i risultati)
-----------------------------------------------------------
Unita' di verifica = coppia (frame_valid_time, stazione METAR).

  * previsione positiva  : almeno un fronte previsto a <= --radius-km
                           (default 50 km) dalla stazione, al quel valid_time
  * osservazione positiva: almeno un bollettino con pioggia (RA/SHRA/TSRA,
                           WMO table 4678) alla stazione entro +- --window-hours
                           (default 3 h) dal valid_time
  * hit / miss / false_allarm / correct_negative = 2x2 standard
  * POD = H/(H+M), FAR = F/(H+F), CSI = H/(H+M+F)
  * controllo a +6 h: si ripete con --window-hours 6; se i hit crescono
                      il modello ha un ritardo di fase dichiarato.

Gate di pubblicazione (regole #233, #237): servono >= 60 casi positivi
(H+M) E >= 60 casi negativi (F+CN) OSSERVATI. Se mancano, il report
contiene i conteggi esatti e `feasible: false`, e NON si pubblica alcuna
metrica (tutti i Campi numerici restano null con null_reason).

REGOLE #207: ogni campo teorico (fronti, distanze,Stazioni) porta source e
reference; nessun campo viene inventato. Le soglie 50 km / 3 h / 6 h sono
SCELTE DI DISEGNO dichiarate, non tarate sull'osservato.

CLI
---
  python scripts/validation/validate_fronts_independent.py \
      --calibration calibration.json \
      --frames-dir <dir con *.om> \
      --manifest <latest.json con valid_times/grid> \
      --metar-csv <CSV IEM: station,valid,lon,lat,metar> \
      --out evidence/validate_fronts_independent.json

  python scripts/validation/validate_fronts_independent.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
VHERE = Path(__file__).resolve().parent
if str(VHERE) not in sys.path:
    sys.path.insert(0, str(VHERE))

from synoptic import fronts  # noqa: E402
from validation import build_labels as bl  # noqa: E402

TOOL_NAME = "validate_fronts_independent"
TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"

DEFAULT_RADIUS_KM = 50.0
DEFAULT_WINDOW_H = 3.0
DEFAULT_CONTROL_H = 6.0
MIN_CASES = 60  # regola #233 / TODO 1.3: positivi E negativi

REF_TRUTH = ("NOAA/NCEI Integrated Surface Database (17 U.S.C. 105, public domain), "
             "trasporto IEM mesonet.agron.iastate.edu/cgi-bin/request/asos.py, "
             "rete IT__ASOS, campo metar (bollettino WMO originale)")
REF_FRONTS = ("TFP Hewson 1998 / front_id MIT; theta_e 850 Bolton 1980; "
              "soglie MISURATE in calibration.json")
GATE_SOURCE = "SCELTA DI DISEGNO dichiarata a priori (non tarata sull'osservato)"


# ---------------------------------------------------------------------------
# Geometria / tempo — funzionalmente pure, testabili senza frame reali
# ---------------------------------------------------------------------------
def parse_utc(value: str) -> datetime:
    """Parse di un orario IEM (gia' in UTC per tz=UTC) o ISO8601."""
    value = str(value).strip().replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"orario non riconosciuto: {value!r}")


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    return bl.haversine_km(lon1, lat1, lon2, lat2)


def point_to_line_km(lat: float, lon: float,
                     lines: Sequence[Sequence[Tuple[float, float]]]) -> Optional[float]:
    """Distanza minima in km da un punto a QUALSIASI segmento di fronte.

    `lines`: sequenza di LineString, ciascuna lista di (lon, lat). La distanza
    e' al SEGMENTO (non al solo vertice): con griglie 0.25 gradi i vertici
    distano ~20-28 km, quindi usare i soli punti sovrastimerebbe la distanza.
    Proiezione locale equirettangolare (x = lon*111.32*cos(lat), y = lat*111.32).
    """
    best = None
    k = 111.32
    px = float(lon) * k * math.cos(math.radians(float(lat)))
    py = float(lat) * k
    for line in lines:
        if len(line) < 2:
            # un singolo vertice: distanza al punto
            for (lo, la) in line:
                d = haversine_km(lon, lat, lo, la)
                best = d if best is None else min(best, d)
            continue
        xy = np.array([(float(lo) * k * math.cos(math.radians(float(la))),
                        float(la) * k) for (lo, la) in line], dtype=np.float64)
        a = xy[:-1]
        b = xy[1:]
        ab = b - a
        ap = np.array([px, py], dtype=np.float64) - a
        denom = np.einsum("ij,ij->i", ab, ab)
        t = np.zeros(len(ab), dtype=np.float64)
        nz = denom > 0
        t[nz] = np.einsum("ij,ij->i", ap[nz], ab[nz]) / denom[nz]
        t = np.clip(t, 0.0, 1.0)
        proj = a + t[:, None] * ab
        d2 = np.sum((proj - np.array([px, py])) ** 2, axis=1)
        d = math.sqrt(float(np.min(d2)))
        best = d if best is None else min(best, d)
    return best


def within_window(event_t: datetime, frame_t: datetime, window_h: float) -> bool:
    return abs((event_t - frame_t).total_seconds()) <= window_h * 3600.0


def classify_pair(front_within: bool, rain_observed: bool) -> str:
    """2x2: hit / miss / false_alarm / correct_negative."""
    if front_within and rain_observed:
        return "hits"
    if (not front_within) and rain_observed:
        return "misses"
    if front_within and (not rain_observed):
        return "false_alarms"
    return "correct_negatives"


def scores(counts: Dict[str, int]) -> Dict[str, Any]:
    """POD/FAR/CSI con null espliciti se il denominatore e' 0 (regola #207)."""
    h, m, f = counts.get("hits", 0), counts.get("misses", 0), counts.get("false_alarms", 0)

    def ratio(num: int, den: int) -> Tuple[Optional[float], Optional[str]]:
        if den <= 0:
            return None, "zero_denominator"
        return round(num / den, 6), None

    pod, pod_null = ratio(h, h + m)
    far, far_null = ratio(f, h + f)
    csi, csi_null = ratio(h, h + m + f)
    return {
        "POD": {"value": pod, "null_reason": pod_null},
        "FAR": {"value": far, "null_reason": far_null},
        "CSI": {"value": csi, "null_reason": csi_null},
        "source": "conteggi misurati su coppie (valid_time, stazione) — questo modulo",
        "reference": "CSI/POD/FAR: definizioni standard (WMO/OGMet verification guidance)",
    }


# ---------------------------------------------------------------------------
# Caricamento verita' indipendente (METAR)
# ---------------------------------------------------------------------------
def load_rain_events(metar_csv: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Righe METAR IEM -> eventi pioggia osservati + stazioni (lat/lon)."""
    text = Path(metar_csv).read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    events: List[Dict[str, Any]] = []
    stations: Dict[str, Tuple[float, float]] = {}
    n_reports = 0
    for r in rows:
        station = (r.get("station") or "").strip()
        if not station:
            continue
        n_reports += 1
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        stations.setdefault(station, (lat, lon))
        raw = r.get("metar", "")
        _s, _t, tokens, _speci = bl.split_metar(raw)
        codes: List[str] = []
        for tok in tokens:
            d = bl.decompose_wx(tok)
            if d is not None:
                codes.extend(d[2])
        cset = set(codes)
        if "RA" not in cset:
            continue
        try:
            t = parse_utc(r["valid"])
        except ValueError:
            continue
        events.append({
            "station": station, "t": t, "lat": lat, "lon": lon,
            "kind": "TSRA" if "TS" in cset else ("SHRA" if "SH" in cset else "RA"),
            "ts_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "raw": raw,
        })
    meta = {
        "reports_read": n_reports,
        "stations": len(stations),
        "rain_events": len(events),
        "kinds": {k: sum(1 for e in events if e["kind"] == k) for k in ("RA", "SHRA", "TSRA")},
        "source": REF_TRUTH,
        "reference": "WMO table 4678 (present weather); parser: scripts/validation/build_labels.py",
    }
    return events, meta


# ---------------------------------------------------------------------------
# Caricamento fronti previsti (.om reali + calibration.json)
# ---------------------------------------------------------------------------
def frame_valid_time(name: str) -> Optional[datetime]:
    m = re.search(r"(20\d\d-\d\d-\d\dT)(\d\d)(\d\d)", name)
    if not m:
        return None
    try:
        return parse_utc(m.group(1) + m.group(2) + ":" + m.group(3))
    except ValueError:
        return None


def load_fronts(frames_dir: str, manifest: str, calibration: str,
                radius_box: Tuple[float, float, float, float] = (30.0, -12.0, 62.0, 30.0)
                ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Calcola i LineString dei fronti per ogni frame osservabile."""
    man = json.loads(Path(manifest).read_text(encoding="utf-8-sig"))
    grid = man["grid"]
    domain = man.get("domain", "ecmwf_ifs025")
    vt_from_manifest = {parse_utc(v) for v in man.get("valid_times", [])}

    cfg = fronts.DetectionConfig.from_calibration(
        calibration, S_km=float(man.get("S_km", grid.get("dx", 0.25) * 100.0 / 4.0)),
        overrides={"domain": domain})
    frames: List[Dict[str, Any]] = []
    la0, lo0, la1, lo1 = radius_box
    seen: set = set()
    for fn in sorted(os.listdir(frames_dir)):
        if not fn.endswith(".om"):
            continue
        vt = frame_valid_time(fn)
        if vt is None or vt not in vt_from_manifest:
            continue
        if vt in seen:  # duplicati con prefisso dominio: UN solo frame per valid_time
            continue
        seen.add(vt)
        arrays = fronts.load_array_file(str(Path(frames_dir) / fn))
        fc = fronts.analyze_frame(arrays, grid, cfg, valid_time=vt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        lines: List[List[Tuple[float, float]]] = []
        for feat in fc.get("features", []):
            if feat.get("geometry", {}).get("type") != "LineString":
                continue
            if feat.get("properties", {}).get("kind") != "front":
                continue
            pts = [(float(lo), float(la)) for lo, la in feat["geometry"]["coordinates"]
                   if la0 <= float(la) <= la1 and lo0 <= float(lo) <= lo1]
            if len(pts) >= 2:
                lines.append(pts)
        frames.append({"vt": vt, "file": fn, "lines": lines,
                       "n_fronts": len(lines), "n_points": sum(len(x) for x in lines)})
    meta = {
        "domain": domain,
        "frames": len(frames),
        "S_km": cfg.S_km,
        "thresh_t": cfg.thresh_t,
        "thresh_g": cfg.thresh_g,
        "status": cfg.status,
        "grid": {k: grid[k] for k in ("nx", "ny", "dx", "dy", "latMin", "lonMin") if k in grid},
        "box": {"lat_min": la0, "lon_min": lo0, "lat_max": la1, "lon_max": lo1},
        "source": "Open-Meteo .om (CC BY 4.0), frame reali",
        "reference": REF_FRONTS,
    }
    return frames, meta


# ---------------------------------------------------------------------------
# Valutazione
# ---------------------------------------------------------------------------
def evaluate(frames: Sequence[Dict[str, Any]], events: Sequence[Dict[str, Any]],
             stations: Dict[str, Tuple[float, float]], radius_km: float,
             window_h: float) -> Dict[str, Any]:
    """2x2 su coppie (frame, stazione) + conteggio eventi con fronte entro il gate."""
    counts = {"hits": 0, "misses": 0, "false_alarms": 0, "correct_negatives": 0}
    # indicizzazione eventi per stazione per evitare O(N^2) cieco
    by_station: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        by_station.setdefault(e["station"], []).append(e)
    event_hits = 0
    for fr in frames:
        for st, (lat, lon) in stations.items():
            d = point_to_line_km(lat, lon, fr["lines"]) if fr["lines"] else None
            front_within = (d is not None and d <= radius_km)
            rain = any(within_window(e["t"], fr["vt"], window_h)
                       for e in by_station.get(st, ()))
            counts[classify_pair(front_within, rain)] += 1
            if front_within and rain:
                event_hits += 1
    positives = counts["hits"] + counts["misses"]
    negatives = counts["false_alarms"] + counts["correct_negatives"]
    return {
        "counts": counts,
        "positives": positives,
        "negatives": negatives,
        "event_hits": event_hits,
        "scores": scores(counts),
        "radius_km": {"value": radius_km, "source": GATE_SOURCE},
        "window_hours": {"value": float(window_h), "source": GATE_SOURCE},
    }


def _metar_time_range(metar_csv: str) -> Tuple[datetime, datetime]:
    """Intervallo coperto dai bollettini: fuori da qui non esiste osservazione."""
    times: List[datetime] = []
    for r in csv.DictReader(io.StringIO(Path(metar_csv).read_text(encoding="utf-8-sig"))):
        try:
            times.append(parse_utc(r["valid"]))
        except (KeyError, ValueError):
            continue
    if not times:
        raise ValueError("nessun orario valido nel CSV METAR")
    return min(times), max(times)


def build_report(calibration: str, frames_dir: str, manifest: str, metar_csv: str,
                 radius_km: float, window_h: float, control_h: float) -> Dict[str, Any]:
    events, tmeta = load_rain_events(metar_csv)
    obs_min, obs_max = _metar_time_range(metar_csv)
    frames, fmeta = load_fronts(frames_dir, manifest, calibration)
    n_all = len(frames)
    # REGOLA #237: un frame senza copertura osservativa NON e' un negativo.
    frames = [f for f in frames if obs_min <= f["vt"] <= obs_max]
    fmeta["frames_total"] = n_all
    fmeta["frames_within_observation_coverage"] = len(frames)
    fmeta["observation_window"] = {
        "start": obs_min.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": obs_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "bollettini METAR presenti nel CSV",
    }
    stations = _stations_from(metar_csv)
    primary = evaluate(frames, events, stations, radius_km, window_h)
    control = evaluate(frames, events, _stations_from(metar_csv), radius_km, control_h)
    feasible = bool(primary["positives"] >= MIN_CASES and primary["negatives"] >= MIN_CASES)
    pub = {
        "feasible": feasible,
        "rule": f">= {MIN_CASES} positivi E >= {MIN_CASES} negativi osservati (regola #233)",
        "measured_positives": primary["positives"],
        "measured_negatives": primary["negatives"],
        "publish_metrics": feasible,
        "null_reason": None if feasible else
        f"campione insufficiente: {primary['positives']} positivi / {primary['negatives']} negativi",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "design": {
            "unit": "coppia (frame_valid_time, stazione METAR)",
            "positive_forecast": "fronte previsto entro radius_km dalla stazione",
            "positive_observation": "pioggia osservata (RA/SHRA/TSRA) entro window_hours",
            "negative": "finestra spazio-temporale senza pioggia osservata",
            "control": "stessa valutazione a +control_hours per escludere ritardi di fase",
            "circularity_removed": "nessuna etichetta da rain+showers del modello; verita' = METAR",
            "source": GATE_SOURCE,
            "reference": "TODO 1.3 riga 33; regole #207/#218/#233/#237",
        },
        "truth": tmeta,
        "forecasts": fmeta,
        "primary": primary,
        "control_6h": control,
        "publication_gate": pub,
        "limits": [
            "La verita' e' una rete di punti (stazioni METAR), non un piano: "
            "l'assenza di bollettino non e' assenza di pioggia (sampling ~60 min).",
            "I fronti sono istantanei ai soli valid_time dei frame disponibili: "
            "il campionamento temporale dei frame limita il POD indipendentemente "
            "dall'abilita' del rilevatore.",
            "Le soglie 50 km / 3 h / 6 h sono scelte di disegno, non tarate.",
            "Nessun campo e' simulato; nessuna etichetta ESWD; nessun dato inventato.",
        ],
    }


def _stations_from(metar_csv: str) -> Dict[str, Tuple[float, float]]:
    stations: Dict[str, Tuple[float, float]] = {}
    for r in csv.DictReader(io.StringIO(Path(metar_csv).read_text(encoding="utf-8-sig"))):
        st = (r.get("station") or "").strip()
        if not st:
            continue
        try:
            stations.setdefault(st, (float(r["lat"]), float(r["lon"])))
        except (KeyError, TypeError, ValueError):
            continue
    return stations


# ---------------------------------------------------------------------------
# Selftest (3 casi: positivo / negativo / controllo +6h)
# ---------------------------------------------------------------------------
def selftest() -> int:
    checks: List[Tuple[str, bool]] = []
    line = [[12.0, 40.0], [12.5, 40.2], [13.0, 40.4]]
    d = point_to_line_km(40.1, 12.2, [line])
    checks.append(("distanza punto-segmento finita e piccola", d is not None and 0 <= d < 50))
    checks.append(("classificazione positiva (fronte+pioggia)",
                   classify_pair(True, True) == "hits"))
    checks.append(("classificazione negativa (no front+no rain)",
                   classify_pair(False, False) == "correct_negatives"))
    t0 = parse_utc("2026-11-19T12:00Z")
    checks.append(("finestra 3h include +2h", within_window(parse_utc("2026-11-19T14:00Z"), t0, 3)))
    checks.append(("finestra 3h esclude +4h", not within_window(parse_utc("2026-11-19T16:00Z"), t0, 3)))
    checks.append(("controllo 6h include +4h", within_window(parse_utc("2026-11-19T16:00Z"), t0, 6)))
    sc = scores({"hits": 1, "misses": 1, "false_alarms": 1})
    checks.append(("POD 1/2 e CSI 1/3", abs(sc["POD"]["value"] - 0.5) < 1e-6
                   and abs(sc["CSI"]["value"] - 1 / 3) < 1e-6))
    checks.append(("FAR null con denominatore 0", scores({"hits": 0, "misses": 0,
                   "false_alarms": 0})["FAR"]["null_reason"] == "zero_denominator"))
    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(("PASS " if ok else "FAIL ") + n)
    print(f"selftest: {len(checks) - len(bad)}/{len(checks)} OK")
    return 1 if bad else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--calibration", default="calibration.json")
    ap.add_argument("--frames-dir", default="")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--metar-csv", default="")
    ap.add_argument("--radius-km", type=float, default=DEFAULT_RADIUS_KM)
    ap.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_H)
    ap.add_argument("--control-hours", type=float, default=DEFAULT_CONTROL_H)
    ap.add_argument("--out", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    for name, val in (("--frames-dir", args.frames_dir), ("--manifest", args.manifest),
                      ("--metar-csv", args.metar_csv), ("--out", args.out)):
        if not val:
            ap.error(f"{name} obbligatorio (oppure --selftest)")
    t0 = time.perf_counter()
    rep = build_report(args.calibration, args.frames_dir, args.manifest, args.metar_csv,
                       args.radius_km, args.window_hours, args.control_hours)
    rep["compute_s"] = round(time.perf_counter() - t0, 2)
    Path(args.out).write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": args.out, "positives": rep["publication_gate"]["measured_positives"],
                      "negatives": rep["publication_gate"]["measured_negatives"],
                      "feasible": rep["publication_gate"]["feasible"],
                      "POD": rep["primary"]["scores"]["POD"]["value"],
                      "CSI": rep["primary"]["scores"]["CSI"]["value"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
