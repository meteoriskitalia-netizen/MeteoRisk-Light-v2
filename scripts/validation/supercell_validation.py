#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MeteoRisk Light — Banco di validazione del rilevamento convettivo (event-based).

DOMANDA A CUI RISPONDE: "quanto e' bravo l'algoritmo?", con numeri difendibili e
secondo le convenzioni della letteratura operativa (NSSL/DWD).

Il modulo e' AUTONOMO e AUTOCONSISTENTE: numpy e' l'unica dipendenza (nessuna
dipendenza nuova), NON importa ``scripts/radar_engine`` e non modifica i suoi
output: li LEGGE tramite gli adattatori (``data/radar/supercells.json``,
``data/radar/storms.geojson``) oppure legge una verita' etichettata esterna.

---------------------------------------------------------------------------
1) REGOLA DI MATCHING (dichiarata, configurabile, riproducibile)
---------------------------------------------------------------------------
Un allarme (detection) e' associato a un evento verita' (truth) se valgono
ENTRAMBE le condizioni:

  a) SPAZIO: distanza great-circle fra i due punti <= ``max_distance_km``
     (default 15 km, come la pratica operativa NSSL per gli eventi convettivi).
  b) TEMPO: l'istante dell'allarme cade nella finestra
         [ t_verita' - pre_window_min , t_verita' + post_window_min ]
     con default ``pre_window_min=20`` e ``post_window_min=6`` (cioe' "un
     volume radar": in letteratura un allarme e' valido se precede l'evento
     fino a 20 minuti o lo segue entro un volume di scansione).
     Per allarmi che hanno un intervallo di vita (es. una track di
     ``supercells.json`` con first_seen/last_seen) la condizione e'
     INTERSEZIONE dell'intervallo con la finestra: una track che esisteva
     ancora dentro la finestra conta come rilevamento.

L'assegnazione e' UNO-A-UNO GREEDY per distanza minima: si ordinano tutte le
coppie candidate per distanza crescente (a parita' di distanza: |dt|, poi id
verita', poi id allarme) e si accetta la coppia solo se ne' l'evento ne'
l'allarme sono gia' usati. Nessun evento e nessun allarme viene contato due
volte; i non accoppiati sono elencati esplicitamente nel report.

I "correct negatives" (CN) richiedono casi NULLI espliciti: eventi verita' con
category ``null`` (o nella lista ``null_events`` del file di verita'), cioe'
situazioni campionate in cui NON si e' verificato l'evento. Regola:
  1. prima si accoppiano gli eventi REALI con gli allarmi (greedy);
  2. gli allarmi rimasti si accoppiano ai casi nulli -> quegli allarmi sono
     FALSI ALLARMI (l'evento non c'era);
  3. i casi nulli rimasti sono CORRECT NEGATIVES.
Senza casi nulli nel file di verita' il CN e' 0 e HSS non e' calcolabile: il
report lo dichiara (``hss: null`` con ``hss_note``), non lo inventa.

---------------------------------------------------------------------------
2) METRICHE (definizioni esplicite, 2x2)
---------------------------------------------------------------------------
Sia la tabella di contingenza 2x2 classica:

                        evento OSSERVATO
                       si'            no
  allarme   si'        hits (a)     false_alarms (b)
  lanciato  no         misses (c)   correct_negatives (d)

  POD  (Probability Of Detection) = a / (a + c)       [0..1], meglio alto
  FAR  (False Alarm Ratio)        = b / (a + b)       [0..1], meglio basso
  CSI  (Critical Success Index)   = a / (a + b + c)   [0..1], meglio alto
  bias (frequency bias)           = (a + b) / (a + c)
  HSS  (Heidke Skill Score)       = (H - E) / (1 - E)
         con H = (a + d) / N (proporzione corretta) e
             E = [(a+b)(a+c) + (c+d)(b+d)] / N^2  (accordo casuale atteso)
  N = a + b + c + d

HSS NON e' l'accuracy: l'accuracy e' H = (a+d)/N, che con classi sbilanciate
premia il "non prevedere nulla". HSS sottrae l'accordo casuale E ed e' quindi
la misura corretta per un 2x2 (range: -inf..1; 1 = perfetto, 0 = come il caso,
<0 = peggio del caso). ``accuracy`` e' riportata nel report SOLO per
trasparenza, e il test verifica esplicitamente che HSS != accuracy.

Intervalli di confidenza: bootstrap non parametrico a livello di CASO
(2000 ricampionamenti di default, seed fisso -> risultato deterministico).
Unita' di ricampionamento = un caso della tabella 2x2: evento reale colpito
(1 hit), evento reale mancato (1 miss), caso nullo con allarme (1 FA), caso
nullo pulito (1 CN), allarme non associato (1 FA). I replicati in cui la
metrica non e' definita (0/0) sono esclusi dal percentile e CONTATI in
``undefined_replicates``.

---------------------------------------------------------------------------
3) STRATIFICAZIONE (dict annidato)
---------------------------------------------------------------------------
  by_distance_band : fasce dal radar ``<60``, ``60-120``, ``120-180``, ``>180``
                     km (campo verita' ``radar_distance_km``; allarmi non
                     associati classificati con il proprio campo, se presente,
                     altrimenti ``unknown``).
  by_month         : ``YYYY-MM`` dell'evento verita'.
  by_season        : DJF / MAM / JJA / SON.
  by_category      : categoria dell'evento verita' (``supercell``,
                     ``large_hail``, ``tornado``, ...).

ATTRIBUZIONE E LIMITE DICHIARATO: hit/miss/CN sono attribuiti all'evento
verita' (o al caso nullo); l'FA da caso nullo all'evento nullo; l'allarme NON
associato alla propria fascia/mese. Per ``by_category`` un allarme non
associato non e' attribuibile a una categoria di evento (le categorie sono
lato verita'): finisce nella chiave esplicita
``unattributed_detections`` e NON in una categoria. Costo dichiarato: dentro
una categoria di evento il FAR e' misurabile solo tramite casi nulli di quella
categoria; il FAR complessivo resta quello del blocco ``metrics``.

---------------------------------------------------------------------------
4) CLASSIFICAZIONE DEI MISS (NSSL)
---------------------------------------------------------------------------
Ogni evento verita' mancato porta ``miss_reason`` in
``{visible_but_missed, not_visible_sampling, bad_data}``:
  - ``visible_but_missed``      l'evento era visibile dal radar e l'algoritmo
                                non l'ha rilevato (errore dell'algoritmo);
  - ``not_visible_sampling``    l'evento non era osservabile per campionamento
                                (fuori copertura, oltre il raggio utile, buco
                                di scansione, volume mancante);
  - ``bad_data``                il dato radar era degradato/inutilizzabile.
Un evento mancato senza ``miss_reason`` finisce nel secchio ``unknown``
(conteggiato e dichiarato, mai nascosto). Un ``miss_reason`` non riconosciuto
e' un errore di schema: ``ValueError`` in fase di lettura.

---------------------------------------------------------------------------
5) SCHEMA DELLA VERITA' (dettagli in scripts/validation/README.md)
---------------------------------------------------------------------------
JSON:
  {"schema_version": "1.0",
   "events": [{"id","time_utc","lon","lat","category","quality",
               "miss_reason","source","radar_distance_km","example"}],
   "null_events": [ ... stesso schema, casi nulli ... ]}
CSV: stesse colonne in intestazione. ``example: true`` marca un esempio
FITTIZIO (escluso con ``--exclude-examples``).

CLI:
  python scripts/validation/supercell_validation.py \\
      --detected data/radar/supercells.json --truth verita.json \\
      --out report.json [--max-distance-km 15] [--bootstrap 2000] \\
      [--pre-window-min 20] [--post-window-min 6] [--seed 12345] \\
      [--exclude-examples] [--detected-format auto|supercells|storms|events]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

TOOL_NAME = "supercell_validation"
TOOL_VERSION = "1.0.0"
TRUTH_SCHEMA_VERSION = "1.0"

EARTH_RADIUS_KM = 6371.0088

DEFAULT_MAX_DISTANCE_KM = 15.0
DEFAULT_PRE_WINDOW_MIN = 20.0
DEFAULT_POST_WINDOW_MIN = 6.0
DEFAULT_BOOTSTRAP = 2000
DEFAULT_SEED = 12345
DEFAULT_CONFIDENCE = 0.95
BOOTSTRAP_CHUNK = 250

MISS_REASON_VISIBLE = "visible_but_missed"
MISS_REASON_SAMPLING = "not_visible_sampling"
MISS_REASON_BAD_DATA = "bad_data"
MISS_REASON_UNKNOWN = "unknown"
MISS_REASONS = (MISS_REASON_VISIBLE, MISS_REASON_SAMPLING, MISS_REASON_BAD_DATA)
MISS_REASON_BUCKETS = MISS_REASONS + (MISS_REASON_UNKNOWN,)

NULL_CATEGORIES = frozenset({"null", "none", "no_event", "null_event"})

DISTANCE_BAND_LABELS = ("<60", "60-120", "120-180", ">180")
BAND_UNKNOWN = "unknown"

TRUTH_REQUIRED_FIELDS = ("id", "time_utc", "lon", "lat")
TRUTH_CSV_COLUMNS = (
    "id", "time_utc", "lon", "lat", "category", "quality", "miss_reason",
    "source", "radar_distance_km", "example",
)


# ---------------------------------------------------------------------------
# Utility di base (tempo, distanza, arrotondamento)
# ---------------------------------------------------------------------------
_TIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def parse_time_utc(value: Any) -> datetime:
    """Converte in datetime UTC aware.

    Accetta: datetime (naive -> UTC), stringa ISO 8601 (con ``Z``, offset o
    naive: una stringa senza fuso e' ASSUNTA UTC), epoch in secondi o
    millisecondi (>= 1e11 => millisecondi). Solleva ValueError altrimenti.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, bool):
        raise ValueError("timestamp booleano non valido: %r" % (value,))
    elif isinstance(value, (int, float)):
        num = float(value)
        if not math.isfinite(num):
            raise ValueError("timestamp non finito: %r" % (value,))
        seconds = num / 1000.0 if abs(num) >= 1e11 else num
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return dt.astimezone(timezone.utc)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("timestamp vuoto")
        candidate = raw.replace("Z", "+00:00").replace("z", "+00:00")
        for suffix in (" UTC", " utc"):
            if candidate.endswith(suffix):
                candidate = candidate[: -len(suffix)] + "+00:00"
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            dt = None
            for fmt in _TIME_FORMATS:
                try:
                    dt = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                raise ValueError("timestamp non riconosciuto: %r" % (value,))
    else:
        raise ValueError("timestamp di tipo non supportato: %r" % (type(value),))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_time_utc(dt: datetime) -> str:
    """Formato canonico del report: ``YYYY-MM-DDTHH:MM:SSZ`` (secondi UTC)."""
    return parse_time_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distanza great-circle in km fra due punti (lon, lat) in gradi."""
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = phi2 - phi1
    dlam = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _haversine_matrix(lon_a: np.ndarray, lat_a: np.ndarray,
                      lon_b: np.ndarray, lat_b: np.ndarray) -> np.ndarray:
    """Matrice (len(a) x len(b)) delle distanze great-circle in km."""
    phi_a = np.radians(lat_a)[:, None]
    phi_b = np.radians(lat_b)[None, :]
    dphi = phi_b - phi_a
    dlam = np.radians(lon_b)[None, :] - np.radians(lon_a)[:, None]
    h = np.sin(dphi / 2.0) ** 2 + np.cos(phi_a) * np.cos(phi_b) * np.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.minimum(1.0, np.sqrt(np.maximum(h, 0.0))))


def distance_band(km: Optional[float]) -> str:
    """Fascia di distanza dal radar. None -> ``unknown``.

    Confini: ``<60`` = [0,60), ``60-120`` = [60,120), ``120-180`` = [120,180],
    ``>180`` = (180, +inf).
    """
    if km is None:
        return BAND_UNKNOWN
    value = float(km)
    if value < 0.0:
        raise ValueError("distanza dal radar negativa: %r" % (km,))
    if value < 60.0:
        return "<60"
    if value < 120.0:
        return "60-120"
    if value <= 180.0:
        return "120-180"
    return ">180"


def season_of(dt: datetime) -> str:
    """Stagione meteorologica: DJF, MAM, JJA, SON."""
    month = parse_time_utc(dt).month
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def _round_or_none(value: Any, digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    num = float(value)
    if not math.isfinite(num):
        return None
    return round(num, digits)


def _maybe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "si", "s")
    return False


def sha256_file(path: str) -> Optional[str]:
    """sha256 del file (per la tracciabilita' del report). None se assente."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest().upper()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Modelli
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TruthEvent:
    """Evento della verita' etichettata (o caso nullo esplicito).

    ``null_case``/``category`` in NULL_CATEGORIES marcano un caso in cui
    l'evento NON si e' verificato: serve per i correct negatives.
    """
    id: str
    time_utc: datetime
    lon: float
    lat: float
    category: str = "unspecified"
    quality: Optional[str] = None
    miss_reason: Optional[str] = None
    source: Optional[str] = None
    radar_distance_km: Optional[float] = None
    example: bool = False
    null_case: bool = False

    @property
    def is_null(self) -> bool:
        return bool(self.null_case) or str(self.category).strip().lower() in NULL_CATEGORIES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "time_utc": format_time_utc(self.time_utc),
            "lon": round(float(self.lon), 5),
            "lat": round(float(self.lat), 5),
            "category": self.category,
            "quality": self.quality,
            "miss_reason": self.miss_reason,
            "source": self.source,
            "radar_distance_km": _round_or_none(self.radar_distance_km),
            "example": bool(self.example),
            "is_null": self.is_null,
        }


@dataclass(frozen=True)
class Detection:
    """Allarme del rilevamento (evento rilevato dall'algoritmo).

    ``time_utc`` e' l'istante rappresentativo (per ``supercells.json``:
    ``last_seen``); ``first_seen_utc``/``last_seen_utc`` definiscono
    l'intervallo di vita della track, usato nella regola temporale.
    """
    id: str
    time_utc: datetime
    lon: float
    lat: float
    category: str = "cell"
    source: Optional[str] = None
    first_seen_utc: Optional[datetime] = None
    last_seen_utc: Optional[datetime] = None
    radar_distance_km: Optional[float] = None
    example: bool = False
    extras: Dict[str, Any] = field(default_factory=dict)
    # 1.2.4.21 — FOOTPRINT GEOMETRICO (GeoJSON Polygon/MultiPolygon, EPSG:4326).
    # Se presente, la distanza usata dal matching e' quella al POLIGONO
    # (0 quando il punto e' DENTRO), non quella al centroide. Default None:
    # comportamento identico alla 1.2.4.20.
    footprint: Optional[Dict[str, Any]] = None

    @property
    def window_start(self) -> datetime:
        return self.first_seen_utc or self.time_utc

    @property
    def window_end(self) -> datetime:
        return self.last_seen_utc or self.time_utc

    def to_dict(self, with_extras: bool = True) -> Dict[str, Any]:
        out = {
            "id": self.id,
            "time_utc": format_time_utc(self.time_utc),
            "lon": round(float(self.lon), 5),
            "lat": round(float(self.lat), 5),
            "category": self.category,
            "source": self.source,
            "radar_distance_km": _round_or_none(self.radar_distance_km),
            "example": bool(self.example),
        }
        if self.first_seen_utc is not None or self.last_seen_utc is not None:
            out["first_seen_utc"] = format_time_utc(self.window_start)
            out["last_seen_utc"] = format_time_utc(self.window_end)
        if with_extras and self.extras:
            out["extras"] = self.extras
        return out


@dataclass(frozen=True)
class MatchParams:
    """Parametri dichiarati della validazione (finiscono nel report)."""
    max_distance_km: float = DEFAULT_MAX_DISTANCE_KM
    pre_window_min: float = DEFAULT_PRE_WINDOW_MIN
    post_window_min: float = DEFAULT_POST_WINDOW_MIN
    bootstrap: int = DEFAULT_BOOTSTRAP
    seed: int = DEFAULT_SEED
    confidence: float = DEFAULT_CONFIDENCE

    def __post_init__(self) -> None:
        if float(self.max_distance_km) < 0.0:
            raise ValueError("max_distance_km deve essere >= 0")
        if float(self.pre_window_min) < 0.0:
            raise ValueError("pre_window_min deve essere >= 0")
        if float(self.post_window_min) < 0.0:
            raise ValueError("post_window_min deve essere >= 0")
        if int(self.bootstrap) < 0:
            raise ValueError("bootstrap deve essere >= 0")
        if not (0.0 < float(self.confidence) < 1.0):
            raise ValueError("confidence deve stare in (0, 1)")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_distance_km": float(self.max_distance_km),
            "pre_window_min": float(self.pre_window_min),
            "post_window_min": float(self.post_window_min),
            "time_window": "[-%g, +%g] min dall'evento verita'"
                           % (float(self.pre_window_min), float(self.post_window_min)),
            "matching": "greedy 1-a-1 per distanza minima (great-circle)",
            "bootstrap": int(self.bootstrap),
            "seed": int(self.seed),
            "confidence": float(self.confidence),
        }


@dataclass(frozen=True)
class MatchPair:
    """Coppia accettata verita' <-> allarme."""
    truth: TruthEvent
    detection: Detection
    distance_km: float
    dt_minutes: float

    def to_dict(self, null: bool = False) -> Dict[str, Any]:
        out = {
            "truth_id": self.truth.id,
            "detection_id": self.detection.id,
            "distance_km": round(float(self.distance_km), 3),
            "dt_minutes": round(float(self.dt_minutes), 2),
            "truth_category": self.truth.category,
            "detection_category": self.detection.category,
        }
        if null:
            out["null_case"] = True
        return out


@dataclass(frozen=True)
class Contingency:
    """Tabella 2x2 event-based."""
    hits: int = 0
    misses: int = 0
    false_alarms: int = 0
    correct_negatives: int = 0

    @property
    def total(self) -> int:
        return int(self.hits + self.misses + self.false_alarms + self.correct_negatives)

    @property
    def n_truth_events(self) -> int:
        return int(self.hits + self.misses)

    @property
    def n_detections(self) -> int:
        return int(self.hits + self.false_alarms)

    def to_dict(self) -> Dict[str, int]:
        return {
            "hits": int(self.hits),
            "misses": int(self.misses),
            "false_alarms": int(self.false_alarms),
            "correct_negatives": int(self.correct_negatives),
            "n_cases": self.total,
            "n_truth_events": self.n_truth_events,
            "n_detections": self.n_detections,
        }


@dataclass
class MatchResult:
    """Esito completo dell'accoppiamento (con i non accoppiati espliciti)."""
    matches: List[MatchPair] = field(default_factory=list)
    null_matches: List[MatchPair] = field(default_factory=list)
    unmatched_truth: List[TruthEvent] = field(default_factory=list)
    unmatched_null: List[TruthEvent] = field(default_factory=list)
    unmatched_detections: List[Detection] = field(default_factory=list)

    def counts(self) -> Contingency:
        return Contingency(
            hits=len(self.matches),
            misses=len(self.unmatched_truth),
            false_alarms=len(self.null_matches) + len(self.unmatched_detections),
            correct_negatives=len(self.unmatched_null),
        )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _candidate_pairs(detected: Sequence[Detection], truth: Sequence[TruthEvent],
                     params: MatchParams) -> List[Tuple[Any, ...]]:
    """Coppie candidate ordinate per distanza (poi |dt|, poi id: deterministico)."""
    if not detected or not truth:
        return []
    t_lon = np.asarray([t.lon for t in truth], dtype=float)
    t_lat = np.asarray([t.lat for t in truth], dtype=float)
    d_lon = np.asarray([d.lon for d in detected], dtype=float)
    d_lat = np.asarray([d.lat for d in detected], dtype=float)
    dist = _haversine_matrix(t_lon, t_lat, d_lon, d_lat)
    # 1.2.4.21 — se un allarme porta un footprint, la distanza e' quella al
    # BORDO del poligono (0 dentro). Un allarme senza footprint resta alla
    # distanza dal centroide: il comportamento predefinito non cambia.
    if any(getattr(d, "footprint", None) for d in detected):
        from validation import geo_match
        for di, det in enumerate(detected):
            fp = getattr(det, "footprint", None)
            if not fp:
                continue
            bbox = geo_match.geometry_bbox(fp)
            if bbox is None:
                continue
            for ti, event in enumerate(truth):
                # estremo inferiore dalla bbox: se supera il gate la coppia
                # e' scartata comunque, senza calcolare la distanza esatta.
                low = geo_match.bbox_distance_lower_bound_km(
                    event.lon, event.lat, bbox)
                if low > params.max_distance_km:
                    dist[ti][di] = low
                    continue
                km = geo_match.distance_to_geometry_km(event.lon, event.lat,
                                                       fp)
                if km is not None:
                    dist[ti][di] = km
    pre = timedelta(minutes=float(params.pre_window_min))
    post = timedelta(minutes=float(params.post_window_min))
    pairs: List[Tuple[Any, ...]] = []
    for ti, event in enumerate(truth):
        lo = event.time_utc - pre
        hi = event.time_utc + post
        row = dist[ti]
        for di, det in enumerate(detected):
            km = float(row[di])
            if km > params.max_distance_km:
                continue
            if det.window_end < lo or det.window_start > hi:
                continue
            dt = (det.time_utc - event.time_utc).total_seconds() / 60.0
            pairs.append((km, abs(dt), event.id, det.id, ti, di, dt))
    pairs.sort(key=lambda p: (p[0], p[1], p[2], p[3]))
    return pairs


def _greedy(pairs: Sequence[Tuple[Any, ...]]) -> List[Tuple[int, int, float, float]]:
    """Assegnazione greedy 1-a-1: nessun evento/allarme usato due volte."""
    used_truth, used_det = set(), set()
    assigned: List[Tuple[int, int, float, float]] = []
    for km, _abs_dt, _tid, _did, ti, di, dt in pairs:
        if ti in used_truth or di in used_det:
            continue
        used_truth.add(ti)
        used_det.add(di)
        assigned.append((ti, di, float(km), float(dt)))
    return assigned


def match_events(detected: Sequence[Detection], truth: Sequence[TruthEvent],
                 params: Optional[MatchParams] = None) -> MatchResult:
    """Accoppiamento 1-a-1 allarmi <-> verita' con la regola del modulo.

    Ordine: prima gli eventi REALI (poi i casi nulli con gli allarmi residui).
    """
    params = params or MatchParams()
    real = [t for t in truth if not t.is_null]
    nulls = [t for t in truth if t.is_null]

    real_pairs = _greedy(_candidate_pairs(detected, real, params))
    matched_real = [MatchPair(real[ti], detected[di], km, dt)
                    for ti, di, km, dt in real_pairs]
    used_det = {di for _ti, di, _km, _dt in real_pairs}
    used_truth = {ti for ti, _di, _km, _dt in real_pairs}

    remaining = [i for i in range(len(detected)) if i not in used_det]
    remaining_det = [detected[i] for i in remaining]
    null_pairs = _greedy(_candidate_pairs(remaining_det, nulls, params))
    matched_null = [MatchPair(nulls[ti], remaining_det[di], km, dt)
                    for ti, di, km, dt in null_pairs]
    used_null = {ti for ti, _di, _km, _dt in null_pairs}
    used_after_null = {remaining[di] for _ti, di, _km, _dt in null_pairs}

    return MatchResult(
        matches=matched_real,
        null_matches=matched_null,
        unmatched_truth=[real[i] for i in range(len(real)) if i not in used_truth],
        unmatched_null=[nulls[i] for i in range(len(nulls)) if i not in used_null],
        unmatched_detections=[detected[i] for i in range(len(detected))
                              if i not in used_det and i not in used_after_null],
    )


# ---------------------------------------------------------------------------
# Metriche
# ---------------------------------------------------------------------------
def _metric_arrays(hits: Any, misses: Any, false_alarms: Any,
                   correct_negatives: Any) -> Dict[str, np.ndarray]:
    """POD/FAR/CSI/bias/HSS vettoriali (NaN dove la metrica non e' definita).

    Formula HSS 2x2: H = (a+d)/N (proporzione corretta),
    E = [(a+b)(a+c) + (c+d)(b+d)] / N^2, HSS = (H - E) / (1 - E).
    """
    a = np.atleast_1d(np.asarray(hits, dtype=np.float64))
    c = np.atleast_1d(np.asarray(misses, dtype=np.float64))
    b = np.atleast_1d(np.asarray(false_alarms, dtype=np.float64))
    d = np.atleast_1d(np.asarray(correct_negatives, dtype=np.float64))

    nan = np.full(a.shape, np.nan, dtype=np.float64)

    pod = nan.copy()
    np.divide(a, a + c, out=pod, where=(a + c) > 0)

    far = nan.copy()
    np.divide(b, a + b, out=far, where=(a + b) > 0)

    csi = nan.copy()
    np.divide(a, a + b + c, out=csi, where=(a + b + c) > 0)

    bias = nan.copy()
    np.divide(a + b, a + c, out=bias, where=(a + c) > 0)

    total = a + b + c + d
    observed = nan.copy()                                        # (a+d)/N
    np.divide(a + d, total, out=observed, where=total > 0)
    expected = nan.copy()
    numerator = (a + b) * (a + c) + (c + d) * (b + d)
    np.divide(numerator, total * total, out=expected, where=total > 0)

    hss = nan.copy()
    denom = 1.0 - expected
    np.divide(observed - expected, denom, out=hss, where=np.abs(denom) > 1e-12)

    return {"pod": pod, "far": far, "csi": csi, "bias": bias, "hss": hss,
            "accuracy": observed, "hss_expected_agreement": expected}


def compute_metrics(counts: Contingency) -> Dict[str, Any]:
    """Metriche puntuali + conteggi (HSS calcolato a 2x2, non come accuracy)."""
    arrays = _metric_arrays([counts.hits], [counts.misses],
                            [counts.false_alarms], [counts.correct_negatives])
    metrics: Dict[str, Any] = {
        "counts": counts.to_dict(),
        "pod": _round_or_none(arrays["pod"][0]),
        "far": _round_or_none(arrays["far"][0]),
        "csi": _round_or_none(arrays["csi"][0]),
        "hss": _round_or_none(arrays["hss"][0]),
        "bias": _round_or_none(arrays["bias"][0]),
        "accuracy": _round_or_none(arrays["accuracy"][0]),
        "hss_expected_agreement": _round_or_none(arrays["hss_expected_agreement"][0]),
        "hss_definition": "HSS = (H - E) / (1 - E); H = (hits+cn)/N; "
                          "E = [(a+b)(a+c)+(c+d)(b+d)]/N^2",
        "definitions": {
            "pod": "hits / (hits + misses)",
            "far": "false_alarms / (hits + false_alarms)",
            "csi": "hits / (hits + misses + false_alarms)",
            "bias": "(hits + false_alarms) / (hits + misses)",
            "hss": "skill rispetto all'accordo casuale atteso (2x2)",
            "accuracy": "(hits + correct_negatives) / N (NON e' skill)",
        },
    }
    if metrics["hss"] is None:
        metrics["hss_note"] = ("HSS non calcolabile: N=0 oppure accordo casuale "
                               "atteso E=1 (classi degeneri). Nessun valore inventato.")
    return metrics


def _bootstrap_codes(result: MatchResult) -> np.ndarray:
    """Codici dei casi 2x2, unita' di ricampionamento del bootstrap."""
    counts = result.counts()
    codes = np.concatenate([
        np.zeros(counts.hits, dtype=np.int8),
        np.ones(counts.misses, dtype=np.int8),
        np.full(counts.false_alarms, 2, dtype=np.int8),
        np.full(counts.correct_negatives, 3, dtype=np.int8),
    ])
    return codes


def bootstrap_ci(counts: Contingency, params: Optional[MatchParams] = None,
                 codes: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """Intervalli di confidenza bootstrap (seed fisso => deterministico).

    ``codes`` opzionale contiene i codici dei casi (0 hit, 1 miss, 2 FA, 3 CN):
    se assente sono ricostruiti dai soli conteggi (stessa distribuzione).
    """
    params = params or MatchParams()
    n_cases = int(counts.total)
    blocks: Dict[str, List[np.ndarray]] = {}
    undefined: Dict[str, int] = {}
    resamples = int(params.bootstrap)

    if codes is None:
        codes = np.concatenate([
            np.zeros(counts.hits, dtype=np.int8),
            np.ones(counts.misses, dtype=np.int8),
            np.full(counts.false_alarms, 2, dtype=np.int8),
            np.full(counts.correct_negatives, 3, dtype=np.int8),
        ])
    codes = np.asarray(codes, dtype=np.int8).reshape(-1)

    if resamples <= 0 or n_cases == 0 or codes.size != n_cases:
        return {
            "level": float(params.confidence),
            "resamples": 0 if (resamples <= 0 or n_cases == 0) else resamples,
            "seed": int(params.seed),
            "unit": "case (hit/miss/fa/cn)",
            "intervals": {k: None for k in ("pod", "far", "csi", "hss", "bias")},
            "undefined_replicates": {},
            "note": ("bootstrap non eseguito: nessun caso" if n_cases == 0
                     else "bootstrap non eseguito (resamples<=0 o codici incoerenti)"),
        }

    rng = np.random.default_rng(int(params.seed))
    for start in range(0, resamples, BOOTSTRAP_CHUNK):
        size = min(BOOTSTRAP_CHUNK, resamples - start)
        idx = rng.integers(0, n_cases, size=(size, n_cases))
        sample = codes[idx]
        h = (sample == 0).sum(axis=1).astype(np.float64)
        m = (sample == 1).sum(axis=1).astype(np.float64)
        f = (sample == 2).sum(axis=1).astype(np.float64)
        cn = (sample == 3).sum(axis=1).astype(np.float64)
        arrays = _metric_arrays(h, m, f, cn)
        for key in ("pod", "far", "csi", "hss", "bias"):
            blocks.setdefault(key, []).append(arrays[key])

    low_q = 50.0 * (1.0 - float(params.confidence))
    high_q = 50.0 * (1.0 + float(params.confidence))
    intervals: Dict[str, Any] = {}
    for key, chunks in blocks.items():
        values = np.concatenate(chunks)
        finite = np.isfinite(values)
        undefined[key] = int(values.size - int(finite.sum()))
        if not finite.any():
            intervals[key] = None
            continue
        lo, hi = np.nanpercentile(values, [low_q, high_q])
        intervals[key] = [_round_or_none(lo), _round_or_none(hi)]
    return {
        "level": float(params.confidence),
        "resamples": resamples,
        "seed": int(params.seed),
        "unit": "case (hit/miss/fa/cn), ricampionamento con reimmissione",
        "method": "percentile bootstrap (numpy default_rng, PCG64)",
        "intervals": intervals,
        "undefined_replicates": undefined,
    }


# ---------------------------------------------------------------------------
# Stratificazione, miss, allarmi non associati
# ---------------------------------------------------------------------------
def _empty_counts() -> Dict[str, int]:
    return {"hits": 0, "misses": 0, "false_alarms": 0, "correct_negatives": 0}


def _strata_counts(result: MatchResult,
                   truth_key,
                   detection_key) -> Dict[str, Dict[str, int]]:
    """Conteggi 2x2 per strato, con attribuzione dichiarata nel docstring modulo."""
    buckets: Dict[str, Dict[str, int]] = {}

    def bump(key: str, name: str) -> None:
        bucket = buckets.setdefault(str(key), _empty_counts())
        bucket[name] += 1

    for pair in result.matches:
        bump(truth_key(pair.truth), "hits")
    for event in result.unmatched_truth:
        bump(truth_key(event), "misses")
    for pair in result.null_matches:
        bump(truth_key(pair.truth), "false_alarms")
    for event in result.unmatched_null:
        bump(truth_key(event), "correct_negatives")
    for detection in result.unmatched_detections:
        bump(detection_key(detection), "false_alarms")
    return buckets


def _band_of_truth(event: TruthEvent) -> str:
    return distance_band(event.radar_distance_km)


def _band_of_detection(detection: Detection) -> str:
    return distance_band(detection.radar_distance_km)


def _month_of_truth(event: TruthEvent) -> str:
    return format_time_utc(event.time_utc)[:7]


def _season_of_truth(event: TruthEvent) -> str:
    return season_of(event.time_utc)


def _month_of_detection(detection: Detection) -> str:
    return format_time_utc(detection.time_utc)[:7]


def _season_of_detection(detection: Detection) -> str:
    return season_of(detection.time_utc)


def _category_of_truth(event: TruthEvent) -> str:
    return str(event.category)


def _category_of_detection(_detection: Detection) -> str:
    return "unattributed_detections"


def stratify(result: MatchResult) -> Dict[str, Dict[str, Any]]:
    """Stratificazione annidata: distanza dal radar, mese, stagione, categoria."""
    by_distance_raw = _strata_counts(result, _band_of_truth, _band_of_detection)
    by_distance: Dict[str, Any] = {}
    for label in DISTANCE_BAND_LABELS:
        counts = by_distance_raw.get(label, _empty_counts())
        by_distance[label] = _stratum_metrics(Contingency(**counts))
    if BAND_UNKNOWN in by_distance_raw:
        by_distance[BAND_UNKNOWN] = _stratum_metrics(
            Contingency(**by_distance_raw[BAND_UNKNOWN]))

    def _dynamic(raw: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
        return {key: _stratum_metrics(Contingency(**raw[key]))
                for key in sorted(raw)}

    by_month = _dynamic(_strata_counts(result, _month_of_truth, _month_of_detection))
    by_season = _dynamic(_strata_counts(result, _season_of_truth, _season_of_detection))
    by_category = _dynamic(_strata_counts(result, _category_of_truth,
                                          _category_of_detection))
    return {
        "by_distance_band": by_distance,
        "by_month": by_month,
        "by_season": by_season,
        "by_category": by_category,
        "attribution": {
            "primary_key": "evento verita' (hit/miss) o caso nullo (FA/CN)",
            "unmatched_detections": "fascia/mese/stagione del proprio allarme; "
                                    "per categoria: chiave 'unattributed_detections'",
            "limitation": "dentro by_category il FAR e' misurabile solo con casi nulli "
                          "della stessa categoria: il FAR complessivo e' in metrics",
        },
    }


def _stratum_metrics(counts: Contingency) -> Dict[str, Any]:
    """2x2 + metriche di uno strato (senza CI: campioni piccoli, dichiarato)."""
    arrays = _metric_arrays([counts.hits], [counts.misses],
                            [counts.false_alarms], [counts.correct_negatives])
    return {
        "counts": counts.to_dict(),
        "pod": _round_or_none(arrays["pod"][0]),
        "far": _round_or_none(arrays["far"][0]),
        "csi": _round_or_none(arrays["csi"][0]),
        "hss": _round_or_none(arrays["hss"][0]),
        "bias": _round_or_none(arrays["bias"][0]),
    }


def summarise_misses(result: MatchResult) -> Dict[str, Any]:
    """Miss con classificazione NSSL (3 categorie + secchio unknown dichiarato)."""
    by_reason = {reason: 0 for reason in MISS_REASON_BUCKETS}
    events = []
    for event in sorted(result.unmatched_truth, key=lambda e: (e.time_utc, e.id)):
        reason = event.miss_reason if event.miss_reason in MISS_REASONS else MISS_REASON_UNKNOWN
        by_reason[reason] += 1
        entry = event.to_dict()
        entry["miss_reason"] = reason
        entry["miss_reason_declared"] = event.miss_reason
        events.append(entry)
    return {
        "count": len(result.unmatched_truth),
        "by_reason": by_reason,
        "categories": {
            "visible_but_missed": "visibile dal radar, non rilevato dall'algoritmo",
            "not_visible_sampling": "non osservabile per campionamento/copertura",
            "bad_data": "dato radar degradato o inutilizzabile",
            "unknown": "evento mancato senza miss_reason dichiarato",
        },
        "events": events,
    }


def summarise_unmatched_detections(result: MatchResult) -> Dict[str, Any]:
    """Allarmi non associati (elenco esplicito: nessun evento contato due volte)."""
    by_category: Dict[str, int] = {}
    for detection in result.unmatched_detections:
        by_category[detection.category] = by_category.get(detection.category, 0) + 1
    detections = [d.to_dict() for d in
                  sorted(result.unmatched_detections, key=lambda d: (d.time_utc, d.id))]
    return {
        "count": len(result.unmatched_detections),
        "by_category": dict(sorted(by_category.items())),
        "detections": detections,
    }


# ---------------------------------------------------------------------------
# Valutazione di alto livello
# ---------------------------------------------------------------------------
def evaluate(detected: Sequence[Detection], truth: Sequence[TruthEvent],
             params: Optional[MatchParams] = None,
             exclude_examples: bool = False) -> Dict[str, Any]:
    """Esegue matching + metriche + stratificazione + miss (senza I/O)."""
    params = params or MatchParams()
    detections = list(detected)
    events = list(truth)
    if exclude_examples:
        detections = [d for d in detections if not d.example]
        events = [e for e in events if not e.example]

    result = match_events(detections, events, params)
    counts = result.counts()
    metrics = compute_metrics(counts)
    metrics["bootstrap"] = bootstrap_ci(counts, params, codes=_bootstrap_codes(result))
    return {
        "contingency": counts.to_dict(),
        "metrics": metrics,
        "stratified": stratify(result),
        "misses": summarise_misses(result),
        "unmatched_detections": summarise_unmatched_detections(result),
        "matches": [p.to_dict() for p in sorted(
            result.matches, key=lambda p: (p.truth.time_utc, p.truth.id))],
        "null_matches": [p.to_dict(null=True) for p in sorted(
            result.null_matches, key=lambda p: (p.truth.time_utc, p.truth.id))],
        "no_match_diagnostics": {
            "unmatched_truth_ids": [e.id for e in result.unmatched_truth],
            "unmatched_detection_ids": [d.id for d in result.unmatched_detections],
            "unmatched_null_ids": [e.id for e in result.unmatched_null],
        },
    }


# ---------------------------------------------------------------------------
# Lettura della verita' etichettata
# ---------------------------------------------------------------------------
def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _truth_event_from_record(record: Dict[str, Any], index: int,
                             source: str, null_default: bool = False) -> TruthEvent:
    if not isinstance(record, dict):
        raise ValueError("%s: evento #%d non e' un oggetto JSON" % (source, index))
    missing = [f for f in TRUTH_REQUIRED_FIELDS if record.get(f) in (None, "")]
    if missing:
        raise ValueError("%s: evento #%d manca dei campi obbligatori %s"
                         % (source, index, ", ".join(missing)))
    reason = record.get("miss_reason")
    if reason is not None and str(reason).strip() != "":
        reason = str(reason).strip()
        if reason not in MISS_REASONS:
            raise ValueError(
                "%s: evento #%d con miss_reason non riconosciuto %r (ammessi: %s)"
                % (source, index, reason, ", ".join(MISS_REASONS)))
    else:
        reason = None
    category = str(record.get("category") or "unspecified").strip()
    return TruthEvent(
        id=str(record["id"]),
        time_utc=parse_time_utc(record["time_utc"]),
        lon=float(record["lon"]),
        lat=float(record["lat"]),
        category=category,
        quality=None if record.get("quality") in (None, "") else str(record["quality"]),
        miss_reason=reason,
        source=None if record.get("source") in (None, "") else str(record["source"]),
        radar_distance_km=_maybe_float(record.get("radar_distance_km",
                                                   record.get("distance_from_radar_km"))),
        example=_as_bool(record.get("example")),
        null_case=bool(null_default) or _as_bool(record.get("is_null")),
    )


def _read_truth_csv(path: str) -> Tuple[List[TruthEvent], List[str], List[str]]:
    events: List[TruthEvent] = []
    warnings: List[str] = []
    unknown_columns: List[str] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("%s: CSV senza intestazione" % path)
        for column in reader.fieldnames:
            if column is not None and column.strip() not in TRUTH_CSV_COLUMNS:
                unknown_columns.append(column)
        if unknown_columns:
            warnings.append("%s: colonne CSV ignorate (non in schema): %s"
                            % (path, ", ".join(unknown_columns)))
        for index, row in enumerate(reader, start=2):
            clean = {k: (v.strip() if isinstance(v, str) else v)
                     for k, v in row.items() if k is not None}
            if not any(v not in (None, "") for v in clean.values()):
                continue
            if clean.get("example") is not None:
                clean["example"] = _as_bool(clean.get("example"))
            try:
                events.append(_truth_event_from_record(clean, index, path))
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
    return events, warnings, unknown_columns


def read_truth(path: str, fmt: str = "auto") -> Dict[str, Any]:
    """Legge la verita' etichettata (JSON o CSV) e ne ritorna eventi + meta.

    Ritorna ``{"events", "warnings", "format", "path", "sha256",
    "unknown_columns", "schema_version", "generated_at"}``.
    """
    fmt = (fmt or "auto").strip().lower()
    if fmt == "auto":
        fmt = "csv" if os.path.splitext(path)[1].lower() == ".csv" else "json"
    if fmt == "csv":
        events, warnings, unknown_columns = _read_truth_csv(path)
        meta: Dict[str, Any] = {}
    elif fmt == "json":
        payload = _read_json(path)
        warnings = []
        unknown_columns = []
        meta = {}
        if isinstance(payload, dict):
            meta["schema_version"] = payload.get("schema_version")
            meta["generated_at"] = payload.get("generated_at")
            records = payload.get("events")
            if records is None:
                raise ValueError("%s: JSON senza chiave 'events'" % path)
            if not isinstance(records, list):
                raise ValueError("%s: 'events' non e' una lista" % path)
            null_records = payload.get("null_events") or []
            if not isinstance(null_records, list):
                raise ValueError("%s: 'null_events' non e' una lista" % path)
        elif isinstance(payload, list):
            records = payload
            null_records = []
        else:
            raise ValueError("%s: JSON ne' lista ne oggetto con 'events'" % path)
        events = [_truth_event_from_record(rec, i, path)
                  for i, rec in enumerate(records) if rec not in (None, "")]
        events += [_truth_event_from_record(rec, i, path, null_default=True)
                   for i, rec in enumerate(null_records) if rec not in (None, "")]
    else:
        raise ValueError("formato verita' non supportato: %r" % (fmt,))
    return {
        "events": events,
        "warnings": warnings,
        "unknown_columns": unknown_columns,
        "format": fmt,
        "path": path,
        "sha256": sha256_file(path),
        "schema_version": meta.get("schema_version"),
        "generated_at": meta.get("generated_at"),
    }


def load_truth(path: str, fmt: str = "auto") -> List[TruthEvent]:
    """Scorciatoia: lista di eventi verita' (senza i metadati di ``read_truth``)."""
    return read_truth(path, fmt)["events"]


# ---------------------------------------------------------------------------
# Lettura degli allarmi (output reali del radar engine)
# ---------------------------------------------------------------------------
def _detections_from_supercells(payload: Dict[str, Any], path: str,
                                warnings: List[str]) -> Tuple[List[Detection], Dict[str, Any]]:
    # 1.2.4.17: il radar engine pubblica `structures` (record SOI). Il tool
    # resta compatibile con i payload storici che usavano `candidates`:
    # nessuna rottura del banco gia' pubblicato, nessun campo inventato.
    candidates = payload.get("structures")
    if candidates is None:
        candidates = payload.get("candidates")
    if candidates is None:
        warnings.append("%s: nessuna chiave 'structures'/'candidates' "
                        "(trattata come lista vuota)" % path)
        candidates = []
    if not isinstance(candidates, list):
        raise ValueError("%s: 'structures' non e' una lista" % path)
    detections: List[Detection] = []
    for index, cand in enumerate(candidates):
        if not isinstance(cand, dict):
            warnings.append("%s: candidato #%d non e' un oggetto JSON, saltato"
                            % (path, index))
            continue
        position = cand.get("position") or []
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            warnings.append("%s: candidato #%d senza 'position' valida, saltato"
                            % (path, index))
            continue
        first = cand.get("first_seen")
        last = cand.get("last_seen")
        stamp = last or first
        if stamp is None:
            warnings.append("%s: candidato #%d senza first_seen/last_seen, saltato"
                            % (path, index))
            continue
        extras = {}
        for key in ("structure_id", "supercell_id", "track_id", "track_type",
                    "soi", "ssi", "soi_v2", "ssi_v2", "level", "phase",
                    "status", "on_latest_frame", "merged_from",
                    "rotation_measurable"):
            if cand.get(key) is not None:
                extras[key] = cand.get(key)
        detections.append(Detection(
            id=str(cand.get("structure_id") or cand.get("supercell_id")
                   or "CS-track-%s" % cand.get("track_id", index)),
            time_utc=parse_time_utc(stamp),
            lon=float(position[0]),
            lat=float(position[1]),
            category=str(cand.get("track_type") or "cell"),
            source=os.path.basename(path),
            first_seen_utc=parse_time_utc(first) if first else None,
            last_seen_utc=parse_time_utc(last) if last else None,
            radar_distance_km=_maybe_float(cand.get("radar_distance_km")),
            example=_as_bool(cand.get("example")),
            extras=extras,
        ))
    meta = {key: payload.get(key) for key in
            ("generated_at", "radar_timestamp", "status", "source")
            if payload.get(key) is not None}
    summary = payload.get("summary")
    if isinstance(summary, dict):
        meta["summary"] = {k: summary[k] for k in
                           ("tracks_evaluated", "structures", "candidates",
                            "births", "organized", "strong", "intense",
                            "possible", "marked", "soi_max", "ssi_max")
                           if k in summary}
    return detections, meta


def _detections_from_geojson(payload: Dict[str, Any], path: str,
                             warnings: List[str]) -> Tuple[List[Detection], Dict[str, Any]]:
    if payload.get("type") != "FeatureCollection":
        raise ValueError("%s: GeoJSON senza type=FeatureCollection" % path)
    features = payload.get("features") or []
    if not isinstance(features, list):
        raise ValueError("%s: 'features' non e' una lista" % path)
    detections: List[Detection] = []
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            warnings.append("%s: feature #%d non e' un oggetto JSON, saltata"
                            % (path, index))
            continue
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        lon = lat = None
        coords = geometry.get("coordinates")
        if geometry.get("type") == "Point" and isinstance(coords, (list, tuple)) \
                and len(coords) >= 2:
            lon, lat = float(coords[0]), float(coords[1])
        elif isinstance(props.get("centroid_lonlat"), (list, tuple)) \
                and len(props["centroid_lonlat"]) >= 2:
            lon, lat = float(props["centroid_lonlat"][0]), float(props["centroid_lonlat"][1])
        if lon is None or lat is None:
            warnings.append("%s: feature #%d senza coordinate, saltata" % (path, index))
            continue
        stamp = props.get("timestamp")
        if stamp is None:
            stamp = props.get("timestamp_ms")
        if stamp is None:
            warnings.append("%s: feature #%d senza timestamp, saltata" % (path, index))
            continue
        extras = {key: props.get(key) for key in
                  ("track_id", "max_dbz", "mean_dbz", "area_km2", "cell_id")
                  if props.get(key) is not None}
        detections.append(Detection(
            id=str(props.get("cell_id") or props.get("track_id") or "feature-%d" % index),
            time_utc=parse_time_utc(stamp),
            lon=lon,
            lat=lat,
            category=str(props.get("track_type") or "cell"),
            source=os.path.basename(path),
            radar_distance_km=_maybe_float(props.get("radar_distance_km")),
            example=_as_bool(props.get("example")),
            extras=extras,
        ))
    meta = {key: payload.get(key) for key in ("generated_at", "radar_timestamp")
            if payload.get(key) is not None}
    return detections, meta


def _detections_from_records(payload: Any, path: str) -> Tuple[List[Detection], Dict[str, Any]]:
    """Formato generico: lista di allarmi con lo schema della verita'."""
    if isinstance(payload, dict):
        records = payload.get("detections", payload.get("events"))
        if records is None:
            raise ValueError("%s: JSON senza chiave 'detections'/'events'" % path)
    else:
        records = payload
    if not isinstance(records, list):
        raise ValueError("%s: allarmi non in lista" % path)
    detections = []
    for index, record in enumerate(records):
        event = _truth_event_from_record(record, index, path)
        detections.append(Detection(
            id=event.id, time_utc=event.time_utc, lon=event.lon, lat=event.lat,
            category=event.category, source=event.source or os.path.basename(path),
            radar_distance_km=event.radar_distance_km, example=event.example,
        ))
    return detections, {}


def read_detections(path: str, fmt: str = "auto") -> Dict[str, Any]:
    """Legge gli allarmi da ``supercells.json``, ``storms.geojson`` o JSON/CSV generico.

    ``fmt``: ``auto`` (default), ``supercells``, ``storms``, ``events``.
    """
    fmt = (fmt or "auto").strip().lower()
    warnings: List[str] = []
    meta: Dict[str, Any] = {}
    if fmt == "auto":
        ext = os.path.splitext(path)[1].lower()
        if ext == ".csv":
            fmt = "events"
        else:
            payload = _read_json(path)
            if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
                fmt = "supercells"
            elif isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
                fmt = "storms"
            else:
                fmt = "events"
    if fmt == "events" and os.path.splitext(path)[1].lower() == ".csv":
        truth = read_truth(path, "csv")
        detections = [Detection(id=e.id, time_utc=e.time_utc, lon=e.lon, lat=e.lat,
                                category=e.category or "cell",
                                source=e.source or os.path.basename(path),
                                radar_distance_km=e.radar_distance_km,
                                example=e.example)
                      for e in truth["events"]]
        warnings.extend(truth["warnings"])
        meta["schema_version"] = truth["schema_version"]
    else:
        payload = _read_json(path)
        if fmt == "supercells":
            detections, meta = _detections_from_supercells(payload, path, warnings)
        elif fmt == "storms":
            detections, meta = _detections_from_geojson(payload, path, warnings)
        elif fmt == "events":
            detections, meta = _detections_from_records(payload, path)
        else:
            raise ValueError("formato allarmi non supportato: %r" % (fmt,))
    return {
        "detections": detections,
        "warnings": warnings,
        "format": fmt,
        "path": path,
        "sha256": sha256_file(path),
        "payload_meta": meta,
    }


def load_detections(path: str, fmt: str = "auto") -> List[Detection]:
    """Scorciatoia: lista di allarmi."""
    return read_detections(path, fmt)["detections"]


# ---------------------------------------------------------------------------
# Report e CLI
# ---------------------------------------------------------------------------
def build_report(detected: Sequence[Detection], truth: Sequence[TruthEvent],
                 params: Optional[MatchParams] = None,
                 exclude_examples: bool = False,
                 detected_source: Optional[Dict[str, Any]] = None,
                 truth_source: Optional[Dict[str, Any]] = None,
                 warnings: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Report JSON completo e riproducibile."""
    params = params or MatchParams()
    detected_list = list(detected)
    truth_list = list(truth)
    outcome = evaluate(detected_list, truth_list, params, exclude_examples)

    used_detected = detected_list
    used_truth = truth_list
    if exclude_examples:
        used_detected = [d for d in detected_list if not d.example]
        used_truth = [t for t in truth_list if not t.example]

    all_warnings = list(warnings or [])
    if exclude_examples:
        dropped_det = len(detected_list) - len(used_detected)
        dropped_truth = len(truth_list) - len(used_truth)
        if dropped_det or dropped_truth:
            all_warnings.append("esclusi %d allarmi e %d eventi verita' marcati example"
                                % (dropped_det, dropped_truth))

    cat_counts: Dict[str, int] = {}
    for det in used_detected:
        cat_counts[det.category] = cat_counts.get(det.category, 0) + 1
    quality_counts: Dict[str, int] = {}
    for event in used_truth:
        key = event.quality or "null"
        quality_counts[key] = quality_counts.get(key, 0) + 1

    return {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": format_time_utc(datetime.now(tz=timezone.utc)),
        "params": params.to_dict(),
        "inputs": {
            "detected": dict(detected_source or {}, n_used=len(used_detected),
                             by_category=dict(sorted(cat_counts.items()))),
            "truth": dict(truth_source or {}, n_used=len(used_truth),
                          n_null_cases=sum(1 for t in used_truth if t.is_null),
                          by_quality=dict(sorted(quality_counts.items()))),
            "exclude_examples": bool(exclude_examples),
        },
        "contingency": outcome["contingency"],
        "metrics": outcome["metrics"],
        "stratified": outcome["stratified"],
        "misses": outcome["misses"],
        "unmatched_detections": outcome["unmatched_detections"],
        "matches": outcome["matches"],
        "null_matches": outcome["null_matches"],
        "no_match_diagnostics": outcome["no_match_diagnostics"],
        "warnings": all_warnings,
        "disclaimer": ("Numeri validi SOLO rispetto alla verita' fornita: se la verita' "
                       "e' incompleta i FAR sono sovrastimati e i POD dipendono dai casi "
                       "etichettati. Non e' una validazione climatica."),
    }


def _format_value(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/d"
    return ("%." + str(digits) + "f") % float(value)


def _format_interval(interval: Any) -> str:
    if not interval:
        return "[n/d]"
    return "[%s, %s]" % (_format_value(interval[0]), _format_value(interval[1]))


def format_summary(report: Dict[str, Any]) -> str:
    """Riassunto leggibile del report (stdout del CLI)."""
    metrics = report["metrics"]
    counts = metrics["counts"]
    bootstrap = metrics.get("bootstrap") or {}
    intervals = bootstrap.get("intervals") or {}
    lines = []
    lines.append("=== VALIDAZIONE RILEVAMENTO CONVETTIVO (event-based) ===")
    lines.append("generato: %s   tool: %s %s" % (
        report["generated_at"], report["tool"]["name"], report["tool"]["version"]))
    lines.append("allarmi usati: %d   eventi verita' usati: %d (casi nulli: %d)" % (
        report["inputs"]["detected"].get("n_used", 0),
        report["inputs"]["truth"].get("n_used", 0),
        report["inputs"]["truth"].get("n_null_cases", 0)))
    params = report["params"]
    lines.append("matching: d <= %s km, finestra %s, greedy 1-a-1" % (
        _format_value(params["max_distance_km"], 1), params["time_window"]))
    lines.append("contingenza 2x2: hits=%d misses=%d false_alarms=%d "
                 "correct_negatives=%d (N=%d)" % (
                     counts["hits"], counts["misses"], counts["false_alarms"],
                     counts["correct_negatives"], counts["n_cases"]))
    lines.append("POD  %s  IC95 %s   (+ = meglio)" % (
        _format_value(metrics["pod"]), _format_interval(intervals.get("pod"))))
    lines.append("FAR  %s  IC95 %s   (- = meglio)" % (
        _format_value(metrics["far"]), _format_interval(intervals.get("far"))))
    lines.append("CSI  %s  IC95 %s" % (
        _format_value(metrics["csi"]), _format_interval(intervals.get("csi"))))
    lines.append("HSS  %s  IC95 %s   (2x2, NON accuracy: accuracy=%s)" % (
        _format_value(metrics["hss"]), _format_interval(intervals.get("hss")),
        _format_value(metrics["accuracy"])))
    lines.append("bias %s  IC95 %s   (F/O, 1.0 = neutro)" % (
        _format_value(metrics["bias"]), _format_interval(intervals.get("bias"))))
    if bootstrap.get("resamples"):
        lines.append("bootstrap: %d ricampionamenti, seed=%s, livello %.2f" % (
            bootstrap["resamples"], bootstrap.get("seed"), bootstrap.get("level", 0.95)))
    else:
        lines.append("bootstrap: %s" % bootstrap.get("note", "non eseguito"))

    misses = report["misses"]
    lines.append("miss (%d): %s" % (misses["count"], ", ".join(
        "%s=%d" % (k, v) for k, v in misses["by_reason"].items())))
    lines.append("allarmi non associati: %d" % report["unmatched_detections"]["count"])
    bands = report["stratified"]["by_distance_band"]
    band_bits = []
    for label in ("<60", "60-120", "120-180", ">180", BAND_UNKNOWN):
        if label in bands:
            counts_b = bands[label]["counts"]
            band_bits.append("%s km: hits=%d miss=%d fa=%d" % (
                label, counts_b["hits"], counts_b["misses"], counts_b["false_alarms"]))
    lines.append("fasce radar: " + " | ".join(band_bits))
    if report.get("warnings"):
        lines.append("avvisi: " + "; ".join(report["warnings"]))
    lines.append("nota: %s" % report.get("disclaimer", ""))
    return "\n".join(lines)


def write_report(report: Dict[str, Any], path: str) -> str:
    """Scrive il report JSON (UTF-8 senza BOM, newline LF)."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp_path, path)
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supercell_validation.py",
        description="Banco di validazione event-based del rilevamento convettivo "
                    "(contingenza 2x2, POD/FAR/CSI/HSS/bias, bootstrap, "
                    "stratificazione, classificazione dei MISS).")
    parser.add_argument("--detected", required=True,
                        help="allarmi: data/radar/supercells.json, storms.geojson, "
                             "JSON/CSV generico")
    parser.add_argument("--truth", required=True,
                        help="verita' etichettata JSON o CSV (schema nel README)")
    parser.add_argument("--out", required=True, help="report JSON di output")
    parser.add_argument("--max-distance-km", type=float, default=DEFAULT_MAX_DISTANCE_KM,
                        help="distanza massima di matching in km (default %(default)s)")
    parser.add_argument("--pre-window-min", type=float, default=DEFAULT_PRE_WINDOW_MIN,
                        help="quanto PRIMA dell'evento un allarme e' valido, in minuti "
                             "(default %(default)s)")
    parser.add_argument("--post-window-min", type=float, default=DEFAULT_POST_WINDOW_MIN,
                        help="quanto DOPO l'evento un allarme e' valido, in minuti "
                             "(default %(default)s)")
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP,
                        help="ricampionamenti bootstrap (default %(default)s)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="seed del bootstrap (default %(default)s: deterministico)")
    parser.add_argument("--confidence", type=float, default=DEFAULT_CONFIDENCE,
                        help="livello di confidenza degli IC (default %(default)s)")
    parser.add_argument("--exclude-examples", action="store_true",
                        help="esclude gli eventi marcati example=true (esempi fittizi)")
    parser.add_argument("--detected-format", default="auto",
                        choices=("auto", "supercells", "storms", "events"),
                        help="formato allarmi (default %(default)s)")
    parser.add_argument("--truth-format", default="auto",
                        choices=("auto", "json", "csv"),
                        help="formato verita' (default %(default)s)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point CLI. Ritorna 0 su successo, 1 su errore di lettura/scrittura."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        params = MatchParams(
            max_distance_km=args.max_distance_km,
            pre_window_min=args.pre_window_min,
            post_window_min=args.post_window_min,
            bootstrap=args.bootstrap,
            seed=args.seed,
            confidence=args.confidence,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    try:
        detected = read_detections(args.detected, args.detected_format)
        truth = read_truth(args.truth, args.truth_format)
    except (OSError, ValueError) as exc:
        print("ERRORE in lettura input: %s" % exc, file=sys.stderr)
        return 1

    report = build_report(
        detected["detections"], truth["events"], params,
        exclude_examples=args.exclude_examples,
        detected_source={
            "path": args.detected,
            "format": detected["format"],
            "sha256": detected["sha256"],
            "payload_meta": detected["payload_meta"],
            "count_loaded": len(detected["detections"]),
        },
        truth_source={
            "path": args.truth,
            "format": truth["format"],
            "sha256": truth["sha256"],
            "schema_version": truth["schema_version"],
            "count_loaded": len(truth["events"]),
        },
        warnings=list(detected["warnings"]) + list(truth["warnings"]),
    )
    try:
        write_report(report, args.out)
    except OSError as exc:
        print("ERRORE in scrittura report: %s" % exc, file=sys.stderr)
        return 1

    print(format_summary(report))
    print("report scritto: %s" % os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
