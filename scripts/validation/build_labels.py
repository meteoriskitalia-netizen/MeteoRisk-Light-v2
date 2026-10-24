#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Costruzione del dataset etichettato per la validazione del layer severo.

DOMANDA A CUI RISPONDE
----------------------
`scripts/validation/supercell_validation.py` calcola POD/FAR/CSI/HSS/bootstrap,
ma non ha etichette. Questo script produce le etichette: una riga per evento
osservato, con la sua posizione, il suo istante, il suo tipo e -- se esiste uno
snapshot delle celle radar che copre quell'istante -- la cella associata.

FONTE DI VERITA' E LICENZA
--------------------------
La fonte usata e' l'archivio **METAR/SPECI** delle stazioni italiane (reports
orari e speciali, testo WMO). Il dato METAR e' scambio WMO: il testo e' quello
che la stazione ha trasmesso, archiviato da NOAA/NCEI (Integrated Surface
Database, "Global Hourly") e qui trasportato dall'endpoint pubblico
``mesonet.agron.iastate.edu/cgi-bin/request/asos.py`` (IEM, Iowa State
University) che ne e' solo un mirror: il campo ``metar`` e' il bollettino
originale, non un valore derivato.

  - Licenza del dato: NOAA/NCEI -- opera del Governo degli Stati Uniti, dominio
    pubblico (17 U.S.C. Sec. 105), nessuna restrizione d'uso, nessun obbligo di
    registrazione. Nessun accordo firmato richiesto.
  - Licenza del trasporto: IEM espone l'archivio gratuitamente; il servizio non
    concede licenza sul dato (non ne e' il titolare), per questo la provenienza
    dichiarata e' NOAA/NCEI e non IEM.

CRITERIO DI CLASSIFICAZIONE (dichiarato, non tarato)
----------------------------------------------------
Il bollettino e' tokenizzato e i gruppi di tempo presente (WMO code table 4678)
sono decomposti nelle loro parti di 2 caratteri. Da qui:

  type=hail            token che contiene ``GR`` (grandine, WMO: diametro
                       >= 5 mm) oppure ``GS`` (small hail, < 5 mm)
  type=tornado         ``+FC`` (tornado o tromba marina)
  type=funnel_cloud    ``FC`` o ``VCFC`` (imbuto nuvoloso, NON tornado)
  type=severe_wind     raffica >= ``--severe-gust-ms`` (default 25.0 m/s, soglia
                       WMO di raffica severa) oppure ``SQ`` (squall)
  type=thunderstorm    ``TS`` (con o senza prefisso di intensita')

Il token ``VC`` (vicinity) NON viene scartato ma marcato ``vicinity: true``:
significa fenomeno osservato nelle vicinanze della stazione (8-16 km), cioe'
una posizione MENO precisa. Il conteggio separato e' nel report.

Un singolo bollettino che riporta ``TSRAGR`` produce DUE righe (una
``thunderstorm`` e una ``hail``): il tipo e' la chiave di valutazione, non un
campo multi-valore. Lo stesso tipo ripetuto in bollettini consecutivi della
stessa stazione entro ``--merge-min`` minuti diventa UNA riga (``merged_count``
dice quante osservazioni ha assorbito): senza il merge una stessa grandinata
osservata a 00:50 e 01:50 sarebbe due eventi.

INCERTEZZA TEMPORALE E CAMPIONAMENTO (dichiarati, non corretti)
---------------------------------------------------------------
Il tempo presente di un METAR (``GR``, ``TS``, ``FC``) e' il fenomeno OSSERVATO
all'ora del bollettino: ``ts_utc`` e' quindi preciso al minuto
(``ts_uncertainty_min = 5``, la granularita' dichiarata del bollettino), non
all'ora. Il limite vero non e' la precisione, e' il CAMPIONAMENTO: fra due
bollettini di routine passano ~60 minuti (``sampling_gap_min``) e un fenomeno
breve che inizia e finisce in mezzo NON esiste nel dataset. Questo abbassa il
POD (un evento non osservato non puo' essere rilevato), non sposta i timestamp.
La finestra di match resta quella richiesta (+-10 min), piu' larga
dell'incertezza del timestamp.

MATCHING CON LE CELLE
---------------------
``--cells-dir`` punta a una cartella di snapshot (layout del branch
``radar-history``: ``<slot>.json`` con ``radar_timestamp`` e ``tracks[*]`` con
``lonlat``). Il match e' distanza great-circle minima fra l'evento e il
centroide di una cella, fra le sole celle i cui snapshot cadono entro
``+-window-min`` dall'istante dell'evento. Regola: ``distance <= max-distance-km``.

Tre esiti, distinti apposta (non collassati in ``null``):
  ``matched``                 cella trovata entro il gate
  ``no_cell_within_gate``     snapshot presenti, nessuna cella entro il gate
  ``no_snapshot_in_window``   nessuno snapshot copre quell'istante

La distinzione conta: ``no_cell_within_gate`` e' un MISS, ``no_snapshot_in_window``
e' un dato mancante. Un evento senza cella NON viene scartato: resta nel file
con ``matched_cell_id: null`` e il suo ``match_status``.

USCITA
------
JSONL, una riga per evento, campi richiesti:
  event_id, ts_utc, lat, lon, type, severity, source, qc,
  distance_km, matched_cell_id, matched_ts
piu' i campi di servizio: match_status, station, vicinity, detail,
severity_value, ts_uncertainty_min, merged_count, raw.

Con ``--truth-json`` scrive anche la verita' nello schema atteso da
``supercell_validation.py`` (``{"schema_version": "1.0", "events": [...],
"null_events": []}``). I casi nulli NON vengono inventati: restano vuoti e il
report lo dichiara (``hss`` sara' ``null`` finche' non si campionano).

CLI
---
  python scripts/validation/build_labels.py \\
      --start 2015-01-01 --end 2026-10-24 \\
      --cache-dir <cartella con metar_it_YYYY.csv.gz> \\
      --cells-dir <cartella snapshot radar-history> \\
      --out labels.jsonl --truth-json truth.json --report report.json \\
      [--max-distance-km 10] [--window-min 10] [--merge-min 90] \\
      [--severe-gust-ms 25.0] [--min-qc B] [--types hail,tornado,...]

  python scripts/validation/build_labels.py --selftest
"""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import io
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TOOL_NAME = "build_labels"
TOOL_VERSION = "1.0.0"
LABELS_SCHEMA_VERSION = "1.0"
TRUTH_SCHEMA_VERSION = "1.0"

EARTH_RADIUS_KM = 6371.0088
KNOT_TO_MS = 0.514444

# ---------------------------------------------------------------------------
# QC: scala PROPRIA (non e' quella ESWD, che non usiamo)
# ---------------------------------------------------------------------------
# A = report verificato a mano / da un ente con controllo di qualita' dichiarato
# B = osservazione strumentale ufficiale (METAR/SPECI), fonte singola
# C = derivato o non verificato
QC_RANK = {"A": 3, "B": 2, "C": 1}
QC_METAR = "B"

SEVERE_GUST_MS_DEFAULT = 25.0
# Il tempo presente di un METAR e' osservato all'ora del bollettino: il
# timestamp e' preciso al minuto. Il limite e' il campionamento (60 min fra
# bollettini di routine), non l'incertezza dell'ora.
TS_UNCERTAINTY_MIN = 5.0
SAMPLING_GAP_MIN = 60.0
# Oltre a questo intervallo due osservazioni consecutive dello stesso tipo alla
# stessa stazione sono considerate due eventi. 90 minuti coprono una sequenza di
# bollettini orari dello stesso temporale; il costo dichiarato e' che due
# temporali distinti a 1,5 h di distanza sullo stesso aeroporto vengono fusi.
# La sensibilita' della scelta e' misurata (merge 30 vs 90) e riportata.
DEFAULT_MERGE_MIN = 90.0

TYPE_HAIL = "hail"
TYPE_TORNADO = "tornado"
TYPE_FUNNEL = "funnel_cloud"
TYPE_WIND = "severe_wind"
TYPE_TS = "thunderstorm"
ALL_TYPES = (TYPE_HAIL, TYPE_TORNADO, TYPE_FUNNEL, TYPE_WIND, TYPE_TS)

CATEGORY_OF_TYPE = {
    TYPE_HAIL: "large_hail",       # GR: diametro >= 5 mm (WMO)
    TYPE_TORNADO: "tornado",
    TYPE_FUNNEL: "funnel_cloud",
    TYPE_WIND: "severe_wind",
    TYPE_TS: "thunderstorm",
}

# Codici di tempo presente (WMO table 4678) a 2 caratteri. ``NSW`` non e' un
# codice di 2 caratteri ed e' escluso apposta (significa "no significant
# weather", cioe' assenza di fenomeno).
WX_CODES = frozenset({
    "RA", "SN", "GR", "GS", "PL", "DZ", "SG", "IC", "UP",
    "BR", "FG", "FU", "VA", "DU", "SA", "HZ", "PY", "PO",
    "SQ", "FC", "TS", "SH", "FZ", "BL", "DR", "MI", "PR",
    "BC", "DS", "SS",
})
INTENSITY = ("+", "-")

# Gruppo di vento: 27019G29KT / VRB02KT / 00000KT / 24012MPS / /////KT
_WIND_RE = re.compile(r"^(?P<dir>\d{3}|VRB|///)(?P<spd>\d{2,3}|//)(G(?P<gust>\d{2,3}))?(?P<unit>KT|MPS|KMH)$")
_TS_RE = re.compile(r"^(?P<h>\d{2})(?P<m>\d{2})(?P<s>\d{2})?(?P<z>Z)$")


# ---------------------------------------------------------------------------
# utility
# ---------------------------------------------------------------------------
def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distanza great-circle in km fra due punti (lon, lat) in gradi."""
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = phi2 - phi1
    dlam = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(max(a, 0.0))))


def parse_time_utc(value: Any) -> datetime:
    """Stringa ISO / ``YYYY-MM-DD HH:MM`` / ``YYYY-MM-DDTHH-MM-00Z`` -> UTC aware."""
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        candidate = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(value: Any) -> Optional[float]:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


# ---------------------------------------------------------------------------
# parsing del bollettino
# ---------------------------------------------------------------------------
def split_metar(raw: str) -> Tuple[str, str, List[str], bool]:
    """(station, body_time, tokens del corpo, is_speci) di un bollettino METAR.

    Si ferma a ``RMK`` e a ``=``: le osservazioni dentro i remark non sono il
    corpo del bollettino e non vengono classificate.
    """
    text = (raw or "").strip().rstrip("=").strip()
    if not text:
        return "", "", [], False
    parts = text.split()
    is_speci = "SPECI" in parts
    # togli il prefisso COR/AUTO/SPECI/METAR per arrivare a <station> <time>
    idx = 0
    while idx < len(parts) and parts[idx] in ("METAR", "SPECI", "COR", "AUTO", "NIL"):
        idx += 1
    if idx + 1 >= len(parts):
        return "", "", [], is_speci
    station = parts[idx]
    body_time = parts[idx + 1]
    rest = parts[idx + 2:]
    tokens: List[str] = []
    for tok in rest:
        if tok in ("RMK", "BECMG", "TEMPO", "NOSIG"):
            break
        tokens.append(tok)
    return station, body_time, tokens, is_speci


def decompose_wx(token: str) -> Optional[Tuple[Optional[str], bool, List[str]]]:
    """``+TSRAGS`` -> (intensity '+', vicinity False, ['TS','RA','GS']).

    ``None`` se il token non e' un gruppo di tempo presente: la regola e'
    stretta (solo codici di 2 caratteri, eventuale prefisso +/-, eventuale
    ``VC``) proprio per non classificare come tempo le altre centinaia di
    token di un METAR.
    """
    body = token
    intensity: Optional[str] = None
    if body[:1] in INTENSITY:
        intensity = body[0]
        body = body[1:]
    vicinity = False
    if body.startswith("VC"):
        vicinity = True
        body = body[2:]
    if not body or len(body) % 2 != 0:
        return None
    codes = [body[i:i + 2] for i in range(0, len(body), 2)]
    if not all(code in WX_CODES for code in codes):
        return None
    return intensity, vicinity, codes


def parse_wind(token: str) -> Tuple[Optional[float], Optional[str]]:
    """(raffica in m/s, unita') di un gruppo di vento; (None, None) altrimenti."""
    match = _WIND_RE.match(token)
    if not match:
        return None, None
    gust = match.group("gust")
    unit = match.group("unit")
    if not gust:
        return None, unit
    value = _f(gust)
    if value is None:
        return None, unit
    if unit == "KT":
        value *= KNOT_TO_MS
    elif unit == "KMH":
        value /= 3.6
    return value, unit


def classify_report(raw: str, severe_gust_ms: float) -> List[Dict[str, Any]]:
    """(type, severity, detail, vicinity, severity_value) per un bollettino."""
    _station, _time, tokens, _speci = split_metar(raw)
    found: Dict[str, Dict[str, Any]] = {}

    def put(kind: str, severity: str, detail: str, vicinity: bool,
            value: Optional[float] = None) -> None:
        prev = found.get(kind)
        if prev is None:
            found[kind] = {"type": kind, "severity": severity, "detail": detail,
                           "vicinity": vicinity, "severity_value": value}
            return
        # piu' occorrenze dello stesso tipo: vince la piu' severa, la vicinanza
        # si perde solo se almeno una non e' "vicinity"
        if prev["severity_value"] is None or (
                value is not None and value > prev["severity_value"]):
            prev["severity_value"] = value
            prev["severity"] = severity
            prev["detail"] = detail
        prev["vicinity"] = prev["vicinity"] and vicinity

    for tok in tokens:
        gust, _unit = parse_wind(tok)
        if gust is not None and gust >= severe_gust_ms:
            put(TYPE_WIND, "severe_gust", tok, False, gust)
        wx = decompose_wx(tok)
        if wx is None:
            continue
        _intensity, vicinity, codes = wx
        if "GR" in codes:
            put(TYPE_HAIL, "large_hail_ge5mm", tok, vicinity)
        elif "GS" in codes:
            put(TYPE_HAIL, "small_hail_lt5mm", tok, vicinity)
        if "FC" in codes:
            # +FC = tornado/tromba marina; FC o VCFC = imbuto nuvoloso
            if tok.startswith("+"):
                put(TYPE_TORNADO, "tornado_or_waterspout", tok, vicinity)
            else:
                put(TYPE_FUNNEL, "funnel_cloud", tok, vicinity)
        if "SQ" in codes:
            put(TYPE_WIND, "squall", tok, vicinity)
        if "TS" in codes:
            put(TYPE_TS, "thunderstorm", tok, vicinity)

    return list(found.values())


# ---------------------------------------------------------------------------
# lettura della cache
# ---------------------------------------------------------------------------
def iter_cache_rows(cache_dir: str, start: datetime, end: datetime,
                    log: callable) -> Iterable[Dict[str, str]]:
    """Righe (station, valid, lon, lat, metar) della cache, filtrate su [start, end)."""
    files = sorted(glob.glob(os.path.join(cache_dir, "metar_it_*.csv.gz")))
    if not files:
        raise SystemExit("nessun file metar_it_YYYY.csv.gz in %s" % cache_dir)
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            if "metar" not in (reader.fieldnames or []):
                log("SKIP %s: colonna 'metar' assente (%r)" % (path, reader.fieldnames))
                continue
            n = 0
            for row in reader:
                try:
                    ts = parse_time_utc(row.get("valid", ""))
                except ValueError:
                    continue
                if ts < start or ts >= end:
                    continue
                n += 1
                yield row
            log("READ %s righe_usate=%d" % (os.path.basename(path), n))


def build_events(cache_dir: str, start: datetime, end: datetime,
                 types: Sequence[str], severe_gust_ms: float,
                 merge_min: float, log: callable) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Estrae gli eventi grezzi (una riga per bollettino x tipo) e li fonde."""
    raw_events: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "reports_read": 0, "reports_with_weather": 0, "reports_unparsed": 0,
        "raw_by_type": Counter(), "raw_vicinity_by_type": Counter(),
        "stations": set(), "cache_files": 0,
        "reports_by_year": Counter(), "gust_reports_by_year": Counter(),
    }
    for row in iter_cache_rows(cache_dir, start, end, log):
        stats["reports_read"] += 1
        station, body_time, tokens, is_speci = split_metar(row.get("metar", ""))
        if not station or not tokens:
            stats["reports_unparsed"] += 1
            continue
        ts = parse_time_utc(row.get("valid", ""))
        stats["reports_by_year"][ts.year] += 1
        if any(parse_wind(tok)[0] is not None for tok in tokens):
            stats["gust_reports_by_year"][ts.year] += 1
        hits = classify_report(row.get("metar", ""), severe_gust_ms)
        if not hits:
            continue
        stats["reports_with_weather"] += 1
        stats["stations"].add(station)
        lat = _f(row.get("lat"))
        lon = _f(row.get("lon"))
        if lat is None or lon is None:
            stats["reports_unparsed"] += 1
            continue
        for hit in hits:
            if hit["type"] not in types:
                continue
            stats["raw_by_type"][hit["type"]] += 1
            if hit["vicinity"]:
                stats["raw_vicinity_by_type"][hit["type"]] += 1
            raw_events.append({
                "station": station,
                "ts": ts,
                "lat": lat,
                "lon": lon,
                "type": hit["type"],
                "severity": hit["severity"],
                "severity_value": hit["severity_value"],
                "detail": hit["detail"],
                "vicinity": bool(hit["vicinity"]),
                "ts_uncertainty_min": TS_UNCERTAINTY_MIN,
                "sampling_gap_min": 60.0,
                "raw": row.get("metar", "").strip(),
            })
    stats["cache_files"] = len(glob.glob(os.path.join(cache_dir, "metar_it_*.csv.gz")))
    merged = merge_events(raw_events, merge_min)
    annotate_context(merged)
    stats["raw_events"] = len(raw_events)
    stats["merged_events"] = len(merged)
    return merged, stats


_SEVERITY_ORDER = {
    TYPE_HAIL: {"small_hail_lt5mm": 0, "large_hail_ge5mm": 1},
    TYPE_WIND: {"squall": 0, "severe_gust": 1},
}


def merge_events(events: List[Dict[str, Any]], merge_min: float) -> List[Dict[str, Any]]:
    """Fonde osservazioni consecutive (stessa stazione e tipo) entro ``merge_min``."""
    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for ev in events:
        by_key[(ev["station"], ev["type"])].append(ev)
    out: List[Dict[str, Any]] = []
    limit = timedelta(minutes=merge_min)
    for (_station, _type), group in by_key.items():
        group.sort(key=lambda e: e["ts"])
        current = dict(group[0])
        current["merged_count"] = 1
        current["merged_raw"] = [group[0]["raw"]]
        for ev in group[1:]:
            if ev["ts"] - current["ts"] <= limit:
                current["merged_count"] += 1
                current["merged_raw"].append(ev["raw"])
                # vince la severita' piu' alta; la vicinanza si perde se una
                # delle osservazioni fuse era a stazione
                order = _SEVERITY_ORDER.get(_type)
                if order is not None:
                    if order.get(ev["severity"], 0) > order.get(current["severity"], 0):
                        current["severity"] = ev["severity"]
                        current["severity_value"] = ev["severity_value"]
                        current["detail"] = ev["detail"]
                elif ev["severity_value"] is not None and (
                        current["severity_value"] is None
                        or ev["severity_value"] > current["severity_value"]):
                    current["severity_value"] = ev["severity_value"]
                current["vicinity"] = current["vicinity"] and ev["vicinity"]
                current["ts_uncertainty_min"] = max(
                    current["ts_uncertainty_min"], ev["ts_uncertainty_min"])
            else:
                out.append(current)
                current = dict(ev)
                current["merged_count"] = 1
                current["merged_raw"] = [ev["raw"]]
        out.append(current)
    out.sort(key=lambda e: (e["ts"], e["station"], e["type"]))
    return out


# ---------------------------------------------------------------------------
# snapshot delle celle e matching
# ---------------------------------------------------------------------------
def load_snapshots(cells_dir: str, log: callable) -> List[Dict[str, Any]]:
    """Carica gli snapshot ``{radar_timestamp, tracks[{track_id, lonlat}]}``."""
    snaps: List[Dict[str, Any]] = []
    files = sorted(glob.glob(os.path.join(cells_dir, "**", "*.json"), recursive=True))
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "tracks" not in data:
            continue
        stamp = data.get("radar_timestamp") or data.get("slot")
        if not stamp:
            continue
        ts: Optional[datetime] = None
        try:
            ts = parse_time_utc(stamp)
        except ValueError:
            try:
                ts = datetime.strptime(str(stamp), "%Y-%m-%dT%H-%M-%SZ").replace(
                    tzinfo=timezone.utc)
            except ValueError:
                continue
        tracks = []
        for tr in data.get("tracks") or []:
            ll = tr.get("lonlat") or []
            if len(ll) != 2:
                continue
            lon, lat = _f(ll[0]), _f(ll[1])
            if lon is None or lat is None:
                continue
            tracks.append({
                "cell_id": str(tr.get("track_id", tr.get("uid"))),
                "uid": tr.get("uid"),
                "track_type": tr.get("track_type", "cell"),
                "status": tr.get("status"),
                "lat": lat, "lon": lon,
            })
        snaps.append({"ts": ts, "slot": data.get("slot", str(stamp)),
                      "tracks": tracks, "path": os.path.basename(path)})
    snaps.sort(key=lambda s: s["ts"])
    log("SNAPSHOTS caricati=%d (celle totali=%d)" % (len(snaps), sum(len(s["tracks"]) for s in snaps)))
    return snaps


def match_events(events: List[Dict[str, Any]], snaps: List[Dict[str, Any]],
                 max_distance_km: float, window_min: float) -> Dict[str, int]:
    """Popola matched_cell_id / matched_ts / distance_km / match_status."""
    counts: Dict[str, int] = Counter()
    window = timedelta(minutes=window_min)
    if not snaps:
        for ev in events:
            ev["matched_cell_id"] = None
            ev["matched_ts"] = None
            ev["distance_km"] = None
            ev["match_status"] = "no_snapshot_in_window"
            counts["no_snapshot_in_window"] += 1
        return counts
    for ev in events:
        best: Optional[Dict[str, Any]] = None
        best_km = float("inf")
        covered = False
        for snap in snaps:
            if abs(snap["ts"] - ev["ts"]) > window:
                continue
            covered = True
            for tr in snap["tracks"]:
                km = haversine_km(ev["lon"], ev["lat"], tr["lon"], tr["lat"])
                if km < best_km:
                    best_km = km
                    best = {"snap": snap, "track": tr}
        if best is not None and best_km <= max_distance_km:
            ev["matched_cell_id"] = best["track"]["cell_id"]
            ev["matched_ts"] = format_ts(best["snap"]["ts"])
            ev["distance_km"] = round(best_km, 3)
            ev["match_status"] = "matched"
        else:
            ev["matched_cell_id"] = None
            ev["matched_ts"] = None
            ev["distance_km"] = round(best_km, 3) if best is not None else None
            ev["match_status"] = "no_cell_within_gate" if covered else "no_snapshot_in_window"
        counts[ev["match_status"]] += 1
    return counts


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------
def event_id_of(ev: Dict[str, Any]) -> str:
    return "metar:%s:%s:%s" % (ev["station"], ev["ts"].strftime("%Y%m%dT%H%M"), ev["type"])


def to_label_row(ev: Dict[str, Any], source_tag: str) -> Dict[str, Any]:
    return {
        "event_id": event_id_of(ev),
        "ts_utc": format_ts(ev["ts"]),
        "lat": round(ev["lat"], 5),
        "lon": round(ev["lon"], 5),
        "type": ev["type"],
        "severity": ev["severity"],
        "source": "%s:%s" % (source_tag, ev["station"]),
        "qc": ev.get("qc", QC_METAR),
        "distance_km": ev.get("distance_km"),
        "matched_cell_id": ev.get("matched_cell_id"),
        "matched_ts": ev.get("matched_ts"),
        # --- campi di servizio (dichiarati nel docstring) ---
        "match_status": ev.get("match_status"),
        "convective_context": ev.get("convective_context"),
        "station": ev["station"],
        "vicinity": ev["vicinity"],
        "detail": ev["detail"],
        "severity_value": None if ev["severity_value"] is None else round(ev["severity_value"], 2),
        "ts_uncertainty_min": ev["ts_uncertainty_min"],
        "sampling_gap_min": ev.get("sampling_gap_min", SAMPLING_GAP_MIN),
        "merged_count": ev.get("merged_count", 1),
        "raw": ev["raw"],
    }


def to_truth(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Verita' nello schema di ``supercell_validation.py`` (schema 1.0)."""
    events = []
    for row in rows:
        events.append({
            "id": row["event_id"],
            "time_utc": row["ts_utc"],
            "lon": row["lon"],
            "lat": row["lat"],
            "category": CATEGORY_OF_TYPE.get(row["type"], row["type"]),
            "quality": row["qc"],
            "source": row["source"],
            "radar_distance_km": None,
            "example": False,
        })
    return {
        "schema_version": TRUTH_SCHEMA_VERSION,
        "events": events,
        "null_events": [],
        "notes": ("Costruito da build_labels.py (METAR/SPECI italiano, trasporto IEM, "
                  "dato di provenienza NOAA/NCEI ISD, dominio pubblico). "
                  "null_events vuoto: nessun caso nullo campionato -> HSS non calcolabile."),
    }


def write_detections_geojson(snaps: Sequence[Dict[str, Any]], path: str) -> int:
    """Riscrive gli snapshot come FeatureCollection (formato ``storms`` del banco).

    Serve solo a dare al banco di validazione gli ALLARMI nello schema che si
    aspetta: ogni cella di ogni slot diventa una feature con ``timestamp`` dello
    slot. Le posizioni sono quelle reali del tracking, non ricostruite.
    """
    features = []
    for snap in snaps:
        for tr in snap["tracks"]:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [tr["lon"], tr["lat"]]},
                "properties": {
                    "timestamp": format_ts(snap["ts"]),
                    "cell_id": "%s@%s" % (tr["cell_id"], snap["slot"]),
                    "track_id": tr["cell_id"],
                    "track_type": tr["track_type"],
                    "status": tr["status"],
                    "slot": snap["slot"],
                },
            })
    payload = {
        "type": "FeatureCollection",
        "generated_at": format_ts(datetime.now(timezone.utc)),
        "source": "radar-history snapshot (reali) ri-serializzati da build_labels.py",
        "features": features,
    }
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return len(features)


def annotate_context(events: List[Dict[str, Any]], window_min: float = 60.0) -> None:
    """Aggiunge ``convective_context`` e ``qc`` a ogni evento.

    ``convective_context`` = allo stesso pluviometro... allo stesso aeroporto e'
    riportato un temporale (``TS``) entro ``window_min`` dall'istante
    dell'evento. Serve per la grandine: un ``GR``/``GS`` di gennaio senza
    temporale e' con alta probabilita' neve tonda o graupel codificato come
    grandine (WMO definisce ``GS`` "small hail OR snow pellets"), non grandine
    convettiva. La regola e' MISURATA sulla distribuzione mensile e dichiarata:
    la grandine senza contesto convettivo riceve ``qc=C`` e non entra nella
    verita' di default (``--min-qc B``), ma resta nel JSONL.
    """
    ts_by_station: Dict[str, List[datetime]] = defaultdict(list)
    for ev in events:
        if ev["type"] == TYPE_TS:
            ts_by_station[ev["station"]].append(ev["ts"])
    for lst in ts_by_station.values():
        lst.sort()
    window = timedelta(minutes=window_min)
    for ev in events:
        if ev["type"] == TYPE_TS:
            ev["convective_context"] = True
            ev["qc"] = QC_METAR
            continue
        stamps = ts_by_station.get(ev["station"]) or []
        ctx = any(abs(stamp - ev["ts"]) <= window for stamp in stamps)
        ev["convective_context"] = ctx
        if ev["type"] == TYPE_HAIL and not ctx:
            ev["qc"] = "C"
            ev["qc_note"] = "hail_without_thunderstorm_context"
        else:
            ev["qc"] = QC_METAR


def build_report(rows: Sequence[Dict[str, Any]], truth_rows: Sequence[Dict[str, Any]],
                 stats: Dict[str, Any], match_counts: Dict[str, int],
                 args: argparse.Namespace) -> Dict[str, Any]:
    by_type: Dict[str, Dict[str, Any]] = {}
    excluded_by_type = Counter(r["type"] for r in rows if r["qc"] != "B")
    for kind in ALL_TYPES:
        subset = [r for r in rows if r["type"] == kind]
        if not subset:
            by_type[kind] = {"n": 0}
            continue
        in_truth = [r for r in subset if r["qc"] == "B"] if QC_RANK[args.min_qc] <= QC_RANK["B"] else []
        by_type[kind] = {
            "n": len(subset),
            "in_truth": len(in_truth),
            "excluded_by_qc": len(subset) - len(in_truth),
            "vicinity": sum(1 for r in subset if r["vicinity"]),
            "raw_observations": int(stats["raw_by_type"].get(kind, 0)),
            "matched": sum(1 for r in subset if r["match_status"] == "matched"),
            "no_cell_within_gate": sum(1 for r in subset if r["match_status"] == "no_cell_within_gate"),
            "no_snapshot_in_window": sum(1 for r in subset if r["match_status"] == "no_snapshot_in_window"),
            "by_year": dict(sorted(Counter(r["ts_utc"][:4] for r in subset).items())),
        }
    days = {r["ts_utc"][:10] for r in rows}
    gust_fraction = {}
    for year, total in sorted(stats["reports_by_year"].items()):
        gust = stats["gust_reports_by_year"].get(year, 0)
        gust_fraction[str(year)] = {
            "reports": total, "with_gust_group": gust,
            "fraction": round(gust / total, 5) if total else None,
        }
    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "labels_schema_version": LABELS_SCHEMA_VERSION,
        "generated_utc": format_ts(datetime.now(timezone.utc)),
        "params": {
            "start": args.start, "end": args.end,
            "max_distance_km": args.max_distance_km,
            "window_min": args.window_min,
            "merge_min": args.merge_min,
            "severe_gust_ms": args.severe_gust_ms,
            "min_qc": args.min_qc,
            "types": args.types,
        },
        "source": {
            "truth": "METAR/SPECI (WMO present weather + wind group), stazioni italiane",
            "data_owner": "NOAA/NCEI Integrated Surface Database (Global Hourly)",
            "transport": "IEM asos.py (mesonet.agron.iastate.edu)",
            "licence": "US Government work, public domain (17 U.S.C. 105)",
            "qc_scale": "A=verificato, B=osservazione strumentale ufficiale, C=derivato",
        },
        "coverage": {
            "cache_files": stats["cache_files"],
            "reports_read": stats["reports_read"],
            "reports_with_weather": stats["reports_with_weather"],
            "reports_unparsed": stats["reports_unparsed"],
            "stations_seen": len(stats["stations"]),
            "event_days": len(days),
            "events_per_event_day": (round(len(rows) / len(days), 2) if days else None),
        },
        "events": {
            "total": len(rows),
            "raw_observations": stats["raw_events"],
            "merged": stats["merged_events"],
            "in_truth": len(truth_rows),
            "by_type": by_type,
            "by_year": dict(sorted(Counter(r["ts_utc"][:4] for r in rows).items())),
        },
        "qc": {
            "scale": "A=verificato, B=osservazione strumentale ufficiale, C=derivato/non verificato",
            "min_qc_applied_to_truth": args.min_qc,
            "counts_on_all_labels": dict(Counter(r["qc"] for r in rows)),
            "excluded_from_truth": len(rows) - len(truth_rows),
            "excluded_by_type": dict(excluded_by_type),
            "rule": ("qc=C solo per la grandine senza temporale (TS) entro 60 min allo "
                     "stesso aeroporto: GR/GS in quel caso puo' essere neve tonda/"
                     "graupel. Tutte le righe C restano nel JSONL."),
        },
        "data_quality": {
            "gust_group_coverage_by_year": gust_fraction,
            "note": ("La frazione di bollettini con un gruppo di raffica cambia fra gli "
                     "anni: la serie severe_wind NON e' omogenea e un calo di eventi puo' "
                     "essere un calo di segnalazione, non di fenomeni."),
        },
        "matching": {
            "counts": dict(match_counts),
            "cells_dir": args.cells_dir,
            "note": ("no_snapshot_in_window = nessuno snapshot radar per quell'istante "
                     "(dato mancante, NON un miss); no_cell_within_gate = snapshot "
                     "presenti senza cella entro il gate (miss)."),
        },
        "declared_limits": [
            "La verita' e' un'osservazione PUNTUALE a una stazione: non e' la verita' "
            "di area di una cella radar (representativeness ~10-20 km).",
            "Il tempo presente e' osservato all'ora del bollettino (ts_uncertainty_min=5), "
            "ma fra due bollettini di routine passano ~60 min (sampling_gap_min): un "
            "fenomeno breve fra due report NON esiste nel dataset, e questo abbassa il POD "
            "senza spostare i timestamp.",
            "Le grandinate lontane da un aeroporto non entrano nel dataset: la "
            "numerosita' e' un MINIMO osservato, non la frequenza reale degli eventi.",
            "Null events assenti: nessun caso nullo campionato, HSS non calcolabile.",
        ],
    }


# ---------------------------------------------------------------------------
# selftest (fixture METAR reali come formato, NON risultati pubblicati)
# ---------------------------------------------------------------------------
def selftest() -> int:
    failures = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failures
        if not cond:
            failures += 1
            print("FAIL %s" % label)
        else:
            print("ok   %s" % label)

    cases = [
        ("LIRF 010050Z VRB02KT CAVOK 17/14 Q1015 NOSIG", set()),
        ("LIEO 010750Z 27019G29KT 230V300 9999 FEW030 26/13 Q1011", set()),
        ("LIRK 010255Z AUTO 34004G15KT //// 10/09 Q1023", set()),
        ("LIPE 121850Z 09012KT 9999 TSRA SCT025CB 22/19 Q1008", {TYPE_TS}),
        # raffica 28 kt = 14.4 m/s: sotto la soglia severa, quindi NIENTE severe_wind
        ("LIMC 052130Z 24015G28KT 2000 +TSRAGR BKN008CB 18/17 Q1004", {TYPE_TS, TYPE_HAIL}),
        # raffica 55 kt = 28.3 m/s: sopra soglia
        ("LIMC 052130Z 24015G55KT 2000 TSRAGR BKN008CB 18/17 Q1004",
         {TYPE_WIND, TYPE_TS, TYPE_HAIL}),
        ("LIPZ 090300Z 00000KT 0800 -GR FG VV001 12/11 Q1010", {TYPE_HAIL}),
        ("LIRF 150900Z 21008KT 9999 FC SCT030 25/18 Q1012", {TYPE_FUNNEL}),
        ("LIRF 150930Z 21018KT 4000 +FC SCT020 24/18 Q1011", {TYPE_TORNADO}),
        ("LIRQ 010600Z 00000KT 9999 -RA VCTS FEW025CB 19/17 Q1009", {TYPE_TS}),
        ("LIRN 010600Z 31025KT 9999 SQ FEW040 21/16 Q1007", {TYPE_WIND}),
        # token che NON sono tempo presente
        ("LIPE 121850Z 09012KT 9999 NSC 22/19 Q1008", set()),
        ("LIRF 010050Z VRB02KT CAVOK 17/14 Q1015", set()),
        ("LIML 010020Z VRB01KT 1000 R36/0800N BCFG SCT002 BKN012 08/08 Q1032 NOSIG", set()),
        ("LIMC 010020Z 24006KT 9999 TSRAGR RMK GR TS", {TYPE_TS, TYPE_HAIL}),
    ]
    for raw, expected in cases:
        got = {h["type"] for h in classify_report(raw, SEVERE_GUST_MS_DEFAULT)}
        check(got == expected, "classify %-62s -> %s" % (raw[:62], sorted(got)))

    # raffica 28 kt = 14.4 m/s < 25 -> non severa; 50 kt = 25.7 m/s -> severa
    check(not classify_report("LIRF 010050Z 24015G28KT 9999 SCT030 20/12 Q1010",
                              SEVERE_GUST_MS_DEFAULT),
          "raffica 28kt (14.4 m/s) NON severa")
    check(any(h["type"] == TYPE_WIND for h in
              classify_report("LIRF 010050Z 24015G50KT 9999 SCT030 20/12 Q1010",
                              SEVERE_GUST_MS_DEFAULT)),
          "raffica 50kt (25.7 m/s) severa")

    # vicinity: FC dentro VCFC resta funnel e viene marcato
    hits = classify_report("LIRF 150900Z 21008KT 9999 VCFC SCT030 25/18 Q1012", SEVERE_GUST_MS_DEFAULT)
    check(len(hits) == 1 and hits[0]["type"] == TYPE_FUNNEL and hits[0]["vicinity"],
          "VCFC -> funnel con vicinity=True")

    # merge: due bollettini a 20 min con lo stesso tipo -> 1 evento
    t0 = datetime(2023, 6, 1, 12, 0, tzinfo=timezone.utc)
    base = {"station": "LIPE", "lat": 44.5, "lon": 11.3, "type": TYPE_HAIL,
            "severity": "small_hail_lt5mm", "severity_value": None, "detail": "-GR",
            "vicinity": False, "ts_uncertainty_min": TS_UNCERTAINTY_MIN, "raw": "x"}
    ev1 = dict(base, ts=t0)
    ev2 = dict(base, ts=t0 + timedelta(minutes=20), severity="large_hail_ge5mm", detail="GR")
    ev3 = dict(base, ts=t0 + timedelta(minutes=120))
    merged = merge_events([ev1, ev2, ev3], 30.0)
    check(len(merged) == 2 and merged[0]["merged_count"] == 2
          and merged[0]["severity"] == "large_hail_ge5mm",
          "merge 30 min: 3 osservazioni -> 2 eventi, vince la severita' piu' alta")

    # distanza: 1 grado di latitudine ~ 111 km
    check(abs(haversine_km(12.0, 45.0, 12.0, 46.0) - 111.19) < 0.5,
          "haversine 1 deg lat = 111.19 km (%.3f)" % haversine_km(12.0, 45.0, 12.0, 46.0))

    print("\n%d controlli falliti" % failures)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2026-10-24")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--cells-dir", default=None)
    parser.add_argument("--out", default="labels.jsonl")
    parser.add_argument("--truth-json", default=None)
    parser.add_argument("--detections-geojson", default=None,
                        help="riscrive gli snapshot come FeatureCollection per il banco")
    parser.add_argument("--report", default=None)
    parser.add_argument("--max-distance-km", type=float, default=10.0)
    parser.add_argument("--window-min", type=float, default=10.0)
    parser.add_argument("--merge-min", type=float, default=DEFAULT_MERGE_MIN)
    parser.add_argument("--severe-gust-ms", type=float, default=SEVERE_GUST_MS_DEFAULT)
    parser.add_argument("--min-qc", default=QC_METAR)
    parser.add_argument("--types", default=",".join(ALL_TYPES))
    parser.add_argument("--source-tag", default="metar_isd")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if not args.cache_dir:
        parser.error("--cache-dir obbligatorio (o --selftest)")

    started = datetime.now(timezone.utc)
    args.start = args.start
    args.end = args.end
    types = tuple(t.strip() for t in args.types.split(",") if t.strip())
    unknown = [t for t in types if t not in ALL_TYPES]
    if unknown:
        parser.error("tipi sconosciuti: %r" % unknown)
    if QC_RANK.get(args.min_qc, 0) > QC_RANK[QC_METAR]:
        print("ATTENZIONE: --min-qc %s esclude tutte le etichette METAR (qc=%s)"
              % (args.min_qc, QC_METAR), file=sys.stderr)

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    start = parse_time_utc(args.start)
    end = parse_time_utc(args.end)

    log("== %s v%s ==" % (TOOL_NAME, TOOL_VERSION))
    log("periodo %s -> %s, tipi=%s" % (format_ts(start), format_ts(end), ",".join(types)))
    events, stats = build_events(args.cache_dir, start, end, types,
                                 args.severe_gust_ms, args.merge_min, log)
    log("eventi: grezzi=%d fusi=%d" % (stats["raw_events"], stats["merged_events"]))

    snaps: List[Dict[str, Any]] = []
    if args.cells_dir:
        snaps = load_snapshots(args.cells_dir, log)
    match_counts = match_events(events, snaps, args.max_distance_km, args.window_min)
    log("matching: %s" % dict(match_counts))
    if args.detections_geojson and snaps:
        n_feat = write_detections_geojson(snaps, args.detections_geojson)
        log("scritti %d allarmi in %s (FeatureCollection)" % (n_feat, args.detections_geojson))

    rows = [to_label_row(ev, args.source_tag) for ev in events]
    truth_rows = [r for r in rows if QC_RANK.get(r["qc"], 0) >= QC_RANK[args.min_qc]]
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
    log("scritto %s (%d righe, di cui %d in verita' con --min-qc %s)"
        % (args.out, len(rows), len(truth_rows), args.min_qc))

    if args.truth_json:
        with open(args.truth_json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(to_truth(truth_rows), fh, ensure_ascii=False, indent=1)
        log("scritta verita' %s (%d eventi, %d esclusi da QC, 0 null)"
            % (args.truth_json, len(truth_rows), len(rows) - len(truth_rows)))

    report = build_report(rows, truth_rows, stats, match_counts, args)
    report["elapsed_s"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        log("scritto report %s" % args.report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
