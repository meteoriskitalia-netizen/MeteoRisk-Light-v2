#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Phase 2 — lightning_afa.py (FULMINI via EUMETSAT MTG LI "AFA")

PERCHE' ESISTE (misure del 2026-11-11, non stime)
--------------------------------------------------
Il vecchio feed fulmini DPC (`LGT/lgt_5min_<epoch_ms>.bin` su
s3-prod-dpc-radar-webp-cache) e' MORTO in produzione:
  - 12 slot su 12 -> HTTP 403 AccessDenied;
  - `findLastProductByType?type=LGT` -> HTTP 404;
  - `downloadProduct` con `productType: LGT` -> HTTP 400.
In `data/radar/convective_structures.json` il campo `lightning` era null su
4/4 strutture e il layer dichiarava `ltg_unavailable:not_published_403`. Il
vecchio percorso, con il backoff, costava fino a 12 richieste 403 per run.

LA SORGENTE NUOVA (verificata il 2026-11-11 con richieste reali)
---------------------------------------------------------------
EUMETView WMS pubblico, layer **`mtg_fd:li_afa`** (MTG-I Lightning Imager,
Accumulated Flash Area):
  - HTTP 200 **senza token**; `AccessConstraints: none` nei capabilities;
  - `Dimension name="time"` = `2025-05-30T15:00Z/.../PT5M` (archivio dal
    2025-05-30, passo 5 minuti);
  - griglia richiesta 0,01 gradi (1300x1300 sul box Italia 6..19E 35..48N),
    `format=image/png8` (palette, 27,7 KB per frame, decodifica 0,3 s);
  - uno slot NON presente nell'archivio risponde **HTTP 200 con
    ServiceExceptionReport** `code="InvalidDimensionValue"` (NON un PNG
    trasparente): l'assenza del granulo e' distinguibile dal "nessuna
    scarica";
  - il granulo realmente servito e' dichiarato dall'header
    `Warning: 99 Nearest value used: time=...Z` (`nearestValue="1"`): senza
    quell'header la richiesta e' stata servita esattamente.

SEMANTICA — quello che questo modulo NON dice
---------------------------------------------
L'AFA e' l'**area** coperta dall'emissione ottica delle scariche accumulate
(30 s di accumulo secondo l'abstract del layer), non un numero di fulmini.
La legenda del layer (`GetLegendGraphic`) e' una rampa di colore SENZA valori
numerici: il colore non e' convertibile in un conteggio. Per questo il campo
pubblicato si chiama `lightning_afa` e vale:
  - `value: true`  -> almeno un pixel AFA attivo nel raggio del candidato;
  - `value: false` -> nessun pixel AFA attivo nel frame campionato;
  - `value: null`  -> sorgente non disponibile, con `null_reason`.
Il valore `false` NON significa "nessun fulmine": significa "nessun pixel AFA
nel frame campionato entro il raggio". La semantica temporale esatta
(30 s di accumulo per frame? finestra mobile?) NON e' verificata: vedi il
report della release.

COSTO E CACHE
-------------
UNA richiesta HTTP per run (slot corrente; su slot assente si arretra di al
massimo `AFA_MAX_BACKOFF_STEPS` slot, 15 minuti). Throttling >= 3 s fra due
richieste allo stesso servizio e cache su disco dello slot scaricato
(PNG + sidecar JSON con sha256): `data/cache/lightning_afa/` (mai committata,
in `.gitignore`).

Nessuna dipendenza da `requests`: urllib come in `lightning.py`. Decoder PNG
stdlib (zlib) via `lightning.png_alpha_mask`.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from radar_engine.phase2 import lightning as _ltg

# ---------------------------------------------------------------------------
# Costanti — DEFAULT (verificati il 2026-11-11 sul servizio reale)
# ---------------------------------------------------------------------------
AFA_WMS_URL = "https://view.eumetsat.int/geoserver/wms"
AFA_LAYER = "mtg_fd:li_afa"
AFA_STYLE = "mtg_li_afa"
AFA_FORMAT = "image/png8"
AFA_CRS = "CRS:84"                    # lon,lat: EPSG:4326 in WMS 1.3.0 e'
                                      # lat,lon e su questo servizio da 500
AFA_BBOX = (6.0, 35.0, 19.0, 48.0)    # (lon_min, lat_min, lon_max, lat_max)
AFA_GRID_PX = 1300                    # 13 gradi / 1300 = 0,01 gradi
AFA_PERIOD_MS = 5 * 60 * 1000         # passo della Dimension time (PT5M)
AFA_MAX_BACKOFF_STEPS = 3             # 15 minuti indietro (slot mancanti)
AFA_THROTTLE_S = 3.0                  # >= 3 s fra due richieste EUMETSAT
AFA_TIMEOUT_S = 30
AFA_CACHE_DIR = os.path.join("data", "cache", "lightning_afa")

SOURCE = ("EUMETSAT EUMETView WMS mtg_fd:li_afa (MTG-I Lightning Imager, "
          "Accumulated Flash Area)")
REFERENCE = ("EUMETSAT MTG LI Level 2 AFA: area con scariche accumulate; "
             "il raster non e' un conteggio di scariche (legenda senza "
             "valori numerici, verificata 2026-11-11)")
SEMANTICS = ("area con scariche accumulate nel frame campionato, non un "
             "numero di fulmini")

# Riga di Warning con l'istante realmente servito (nearestValue="1").
_SERVED_RE = re.compile(r"time=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
                        r"(?:\.\d+)?Z")
_EXC_CODE_RE = re.compile(r'code="([^"]+)"')

# Throttling di processo (non per istanza): due fetch ravvicinati nello
# stesso run aspettano comunque >= AFA_THROTTLE_S.
_LAST_HTTP_MONO = None


class AfaFetchError(Exception):
    """AFA non disponibile: rete, HTTP, payload o nessuno slot nell'archivio.

    `slot_missing`: True quando il servizio ha risposto con una
    ServiceExceptionReport `InvalidDimensionValue` (granulo assente per
    quell'istante): il chiamante puo' arretrare di uno slot.
    `http_status`: codice HTTP quando il guasto e' di trasporto.
    """

    def __init__(self, message="", slot_missing=False, http_status=None):
        super().__init__(message)
        self.slot_missing = bool(slot_missing)
        self.http_status = http_status


# ---------------------------------------------------------------------------
# Pure: tempo, URL, header
# ---------------------------------------------------------------------------
def afa_slot_epoch(now_ms=None, backoff_steps=0):
    """epoch_ms dell'ultimo slot AFA (multiplo di 5 minuti) <= now (UTC).

    Stessa aritmetica di `lightning.ltg_epoch_floor` (fonte unica del passo
    di 5 minuti): `backoff_steps` arretra di uno slot per volta."""
    return _ltg.ltg_epoch_floor(now_ms=now_ms, backoff_steps=backoff_steps)


def epoch_to_iso(epoch_ms):
    """epoch ms UTC -> 'YYYY-MM-DDTHH:MM:SSZ' (deterministico, senza locale)."""
    import datetime as _dt
    dt = _dt.datetime.fromtimestamp(int(epoch_ms) / 1000.0,
                                    tz=_dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_afa_getmap_url(time_iso, bbox=None, width=None, height=None,
                         wms_url=None, layer=None):
    """URL GetMap WMS 1.3.0 del layer AFA (deterministico).

    bbox = (lon_min, lat_min, lon_max, lat_max) in gradi, CRS:84.
    `format=image/png8`: palette 8 bit (27,7 KB contro 60,8 KB RGBA sullo
    stesso frame, stesso conteggio di pixel attivi — misurato)."""
    bb = AFA_BBOX if bbox is None else tuple(float(v) for v in bbox)
    w = int(AFA_GRID_PX if width is None else width)
    h = int(AFA_GRID_PX if height is None else height)
    params = [
        ("service", "WMS"),
        ("version", "1.3.0"),
        ("request", "GetMap"),
        ("layers", AFA_LAYER if layer is None else layer),
        ("styles", AFA_STYLE),
        ("crs", AFA_CRS),
        ("bbox", ",".join(f"{v:.5f}" for v in bb)),
        ("width", str(w)),
        ("height", str(h)),
        ("format", AFA_FORMAT),
        ("transparent", "true"),
        ("time", str(time_iso)),
    ]
    base = wms_url or AFA_WMS_URL
    sep = "&" if "?" in base else "?"
    return base + sep + urllib.parse.urlencode(params)


def parse_served_iso(warning_header):
    """Istante realmente servito dall'header `Warning` (None se assente).

    GeoServer con `nearestValue="1"` risponde con
    `99 Nearest value used: time=2025-08-02T18:05:00.000Z ISO8601 (layer)`.
    Senza header la richiesta e' stata servita esattamente: il chiamante usa
    l'istante richiesto."""
    if not warning_header:
        return None
    m = _SERVED_RE.search(str(warning_header))
    return (m.group(1) + "Z") if m else None


def _service_exception_code(content):
    """Codice della ServiceExceptionReport OGC, o None se non lo e'."""
    if not content:
        return None
    head = bytes(content[:400]).decode("latin-1", "replace")
    if "ServiceException" not in head:
        return None
    m = _EXC_CODE_RE.search(head)
    return m.group(1) if m else "ServiceException"


# ---------------------------------------------------------------------------
# Pure: decodifica del frame AFA
# ---------------------------------------------------------------------------
def decode_afa_png(content, bbox=None, width=None, height=None):
    """Maschera dei pixel AFA attivi + coordinate dei pixel attivi.

    Ritorna un dict con `active_pixels`, `area_km2`, `cells_0p1` e i vettori
    numpy `lon`/`lat`/`area` dei SOLI pixel attivi (alpha > 0). L'alpha e'
    l'unico segnale affidabile: il colore e' una rampa senza valori numerici.
    ValueError (da lightning.png_alpha_mask) su PNG non valido: nessun
    conteggio inventato."""
    w, h, alpha = _ltg.png_alpha_mask(content)
    if width is not None and int(width) != int(w):
        raise ValueError(f"afa_width_mismatch:{w}!={int(width)}")
    if height is not None and int(height) != int(h):
        raise ValueError(f"afa_height_mismatch:{h}!={int(height)}")
    bb = AFA_BBOX if bbox is None else tuple(float(v) for v in bbox)
    a = np.frombuffer(alpha, dtype=np.uint8).reshape(h, w)
    rows, cols = np.nonzero(a)
    dlon = (bb[2] - bb[0]) / float(w)
    dlat = (bb[3] - bb[1]) / float(h)
    lon = bb[0] + (cols.astype("float64") + 0.5) * dlon
    lat = bb[3] - (rows.astype("float64") + 0.5) * dlat
    # Area del pixel sul globo (sferoide locale): 111,320 km/deg in longitudine
    # all'equatore per cos(lat), 110,574 km/deg in latitudine.
    area = (111.320 * np.cos(np.radians(lat)) * dlon) * (110.574 * dlat)
    cells = 0
    if lon.size:
        key = (np.floor(lat / 0.1).astype("int64") * 100000
               + np.floor(lon / 0.1).astype("int64"))
        cells = int(np.unique(key).size)
    return {
        "width": int(w), "height": int(h), "bbox": list(bb),
        "active_pixels": int(lon.size),
        "area_km2": round(float(area.sum()), 1),
        "cells_0p1": cells,
        "lon": lon, "lat": lat, "area": area,
    }


def _haversine_vec(lon, lat, lon0, lat0):
    """Distanza (km) fra il punto (lon0, lat0) e i vettori lon/lat — vettoriale.

    Stessa formula di `lightning.haversine_km` (raggio EARTH_RADIUS_KM)."""
    r = _ltg.EARTH_RADIUS_KM
    p1 = np.radians(lat0)
    p2 = np.radians(lat)
    dphi = p2 - p1
    dlmb = np.radians(lon - lon0)
    a = (np.sin(dphi / 2.0) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2.0) ** 2)
    return 2.0 * r * np.arcsin(np.minimum(1.0, np.sqrt(a)))


def count_within(frame, lon, lat, radius_km):
    """Pixel AFA attivi entro `radius_km` dal punto (lon, lat).

    Ritorna {"active_pixels", "area_km2", "active"}. Frame None o senza pixel
    attivi -> zeri (mai un conteggio inventato)."""
    if frame is None:
        return {"active_pixels": 0, "area_km2": 0.0, "active": False}
    try:
        x0, y0, rr = float(lon), float(lat), float(radius_km)
    except (TypeError, ValueError):
        raise ValueError("afa_point_non_numeric")
    if not np.isfinite(x0) or not np.isfinite(y0) or not np.isfinite(rr) \
            or rr <= 0.0:
        raise ValueError("afa_point_non_finite_or_non_positive_radius")
    flon = frame.get("lon")
    if flon is None or len(flon) == 0:
        return {"active_pixels": 0, "area_km2": 0.0, "active": False}
    dist = _haversine_vec(flon, frame["lat"], x0, y0)
    hit = dist <= rr
    n = int(np.count_nonzero(hit))
    return {
        "active_pixels": n,
        "area_km2": round(float(frame["area"][hit].sum()), 1),
        "active": bool(n > 0),
    }


def field_for_candidate(frame, lon, lat, radius_km, null_reason=None):
    """Campo `lightning_afa` PER CANDIDATO (value/null_reason/source/reference).

    `value` e' booleano: True = almeno un pixel AFA attivo entro il raggio,
    False = nessuno, None = sorgente non disponibile (`null_reason`)."""
    base = {
        "radius_km": float(radius_km),
        "source": SOURCE,
        "reference": REFERENCE,
        "semantics": SEMANTICS,
    }
    if frame is None:
        base.update({"value": None, "active_pixels": None, "area_km2": None,
                     "slot_iso": None, "slot_offset_min": None,
                     "null_reason": null_reason or "afa_unavailable"})
        return base
    try:
        hit = count_within(frame, lon, lat, radius_km)
    except ValueError as exc:
        base.update({"value": None, "active_pixels": None, "area_km2": None,
                     "slot_iso": frame.get("served_iso"),
                     "slot_offset_min": frame.get("slot_offset_min"),
                     "null_reason": f"afa_bad_position:{exc}"})
        return base
    base.update({
        "value": bool(hit["active"]),
        "active_pixels": int(hit["active_pixels"]),
        "area_km2": float(hit["area_km2"]),
        "slot_iso": frame.get("served_iso"),
        "slot_offset_min": frame.get("slot_offset_min"),
        "null_reason": None,
    })
    return base


def frame_summary(frame):
    """Riepilogo JSON-abile del frame (nessun vettore: solo scalari)."""
    if frame is None:
        return None
    return {
        "slot_iso": frame.get("served_iso"),
        "requested_iso": frame.get("requested_iso"),
        "slot_offset_min": frame.get("slot_offset_min"),
        "active_pixels": frame.get("active_pixels"),
        "area_km2": frame.get("area_km2"),
        "cells_0p1": frame.get("cells_0p1"),
        "width": frame.get("width"),
        "height": frame.get("height"),
        "bbox": frame.get("bbox"),
        "bytes": frame.get("bytes"),
        "sha256": frame.get("sha256"),
        "cache_hit": frame.get("cache_hit"),
        "source": SOURCE,
        "reference": REFERENCE,
    }


# ---------------------------------------------------------------------------
# Cache su disco
# ---------------------------------------------------------------------------
def _cache_paths(cache_dir, epoch_ms):
    base = os.path.join(cache_dir, f"afa_{int(epoch_ms)}")
    return base + ".png", base + ".json"


def _cache_read(cache_dir, epoch_ms):
    """(content, meta) dalla cache su disco, o (None, None).

    Il sidecar JSON e' la chiave di validita': senza di esso il PNG non viene
    usato (nessun frame senza provenienza)."""
    png, meta_path = _cache_paths(cache_dir, epoch_ms)
    if not (os.path.exists(png) and os.path.exists(meta_path)):
        return None, None
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        with open(png, "rb") as fh:
            content = fh.read()
    except (OSError, ValueError):
        return None, None
    digest = hashlib.sha256(content).hexdigest()
    if meta.get("sha256") and meta["sha256"] != digest:
        return None, None
    return content, meta


def _cache_write(cache_dir, epoch_ms, content, meta):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        png, meta_path = _cache_paths(cache_dir, epoch_ms)
        with open(png, "wb") as fh:
            fh.write(content)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=1, sort_keys=True)
    except OSError:
        return False                    # cache non scrivibile: non e' un errore
    return True


# ---------------------------------------------------------------------------
# Wrapper I/O
# ---------------------------------------------------------------------------
def _default_opener(url, timeout_s):
    """GET urllib -> (content, headers dict). HTTPError diventa AfaFetchError."""
    req = urllib.request.Request(url, method="GET",
                                 headers={"Accept": "image/png,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.read(), headers
    except urllib.error.HTTPError as exc:
        raise AfaFetchError(f"afa_http_{exc.code}",
                            http_status=int(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AfaFetchError(
            f"afa_network:{exc.__class__.__name__}") from exc


def _normalize_opener_result(out):
    """opener -> (content, headers); accetta anche i soli byte (test)."""
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], dict):
        return out[0], out[1]
    return out, {}


def _throttle(min_interval_s, sleep):
    global _LAST_HTTP_MONO
    try:
        gap = float(min_interval_s)
    except (TypeError, ValueError):
        gap = AFA_THROTTLE_S
    if gap <= 0:
        return
    now = time.monotonic()
    if _LAST_HTTP_MONO is not None:
        wait = gap - (now - _LAST_HTTP_MONO)
        if wait > 0:
            sleep(wait)
            now = time.monotonic()
    _LAST_HTTP_MONO = now


def reset_throttle():
    """Azzera il timer di throttling (solo test)."""
    global _LAST_HTTP_MONO
    _LAST_HTTP_MONO = None


def fetch_afa(now_ms=None, max_backoff_steps=None, bbox=None, width=None,
              height=None, wms_url=None, layer=None, timeout_s=None,
              throttle_s=None, cache_dir=None, opener=None, sleep=None,
              use_cache=True):
    """WRAPPER DI RETE: frame AFA dello slot corrente (o del primo slot
    disponibile arretrando di `max_backoff_steps` slot).

    Ritorna il dict di `decode_afa_png` piu':
      requested_iso, served_iso, slot_offset_min, bytes, sha256, cache_hit,
      attempts.
    Solleva AfaFetchError se nessuno slot della finestra e' disponibile o il
    payload non e' un PNG AFA: nessun frame inventato.
    Cache su disco (`cache_dir`, default AFA_CACHE_DIR) e throttling
    (`throttle_s`, default AFA_THROTTLE_S) fra richieste consecutive.
    `opener(url, timeout_s) -> (bytes, headers)` iniettabile nei test."""
    steps = (AFA_MAX_BACKOFF_STEPS if max_backoff_steps is None
             else max(0, int(max_backoff_steps)))
    to = float(AFA_TIMEOUT_S if timeout_s is None else timeout_s)
    cdir = AFA_CACHE_DIR if cache_dir is None else cache_dir
    slp = time.sleep if sleep is None else sleep
    op = _default_opener if opener is None else opener

    last_reason = "no_slot"
    for i in range(steps + 1):
        epoch = afa_slot_epoch(now_ms=now_ms, backoff_steps=i)
        requested_iso = epoch_to_iso(epoch)
        content, meta = (None, None)
        if use_cache and cdir:
            content, meta = _cache_read(cdir, epoch)
        cache_hit = content is not None
        headers = {}
        if content is None:
            url = build_afa_getmap_url(requested_iso, bbox=bbox, width=width,
                                       height=height, wms_url=wms_url,
                                       layer=layer)
            _throttle(AFA_THROTTLE_S if throttle_s is None else throttle_s,
                      slp)
            content, headers = _normalize_opener_result(op(url, to))
            code = _service_exception_code(content)
            if code is not None:
                # Granulo assente per quell'istante: si arretra di uno slot.
                last_reason = f"slot_missing:{code}"
                continue
            if not content or bytes(content[:8]) != b"\x89PNG\r\n\x1a\n":
                raise AfaFetchError("afa_not_a_png")
        try:
            frame = decode_afa_png(content, bbox=bbox, width=width,
                                   height=height)
        except ValueError as exc:
            raise AfaFetchError(f"afa_invalid_png:{exc}") from exc

        served_iso = (parse_served_iso(headers.get("warning"))
                      or (meta or {}).get("served_iso") or requested_iso)
        try:
            import datetime as _dt
            t_req = _dt.datetime.strptime(requested_iso,
                                          "%Y-%m-%dT%H:%M:%SZ")
            t_srv = _dt.datetime.strptime(served_iso, "%Y-%m-%dT%H:%M:%SZ")
            offset_min = int(round((t_srv - t_req).total_seconds() / 60.0))
        except (TypeError, ValueError):
            offset_min = None
        digest = hashlib.sha256(bytes(content)).hexdigest()
        frame.update({
            "requested_iso": requested_iso,
            "served_iso": served_iso,
            "slot_offset_min": offset_min,
            "bytes": len(content),
            "sha256": digest,
            "cache_hit": bool(cache_hit),
            "attempts": i + 1,
        })
        if not cache_hit and use_cache and cdir:
            _cache_write(cdir, epoch, content, frame_summary(frame))
        return frame
    raise AfaFetchError(f"afa_unavailable:{last_reason}", slot_missing=True)
