#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — source_opera.py

SORGENTE OPERA CIRRUS (EUMETNET / Open Radar Data), usata come SECONDA fonte
del mosaico nazionale accanto alla VMI Radar-DPC.

FONTE (verificata dal vivo il 2026-11-10 su S3 aperto, accesso ANONIMO):
  https://s3.waw3-1.cloudferro.com/openradar-24h/
  chiave:  YYYY/MM/DD/OPERA/COMP/OPERA@YYYYMMDDTHHMM@0@DBZH.tiff
  - GeoTIFF COG, 3800x4400, pixel 1000 m, float32, 2 bande
    (banda 1 = DBZH dBZ, banda 2 = quality index), nodata -9999000
  - CRS Lambert Azimuthal Equal Area (lat_0=55, lon_0=10, x_0=1950000,
    y_0=-2100000), transform (1000, 0, -500.0003, 0, -1000, 499.9999)
  - cadenza 5 minuti, semantica MAX su 10 minuti, licenza CC BY 4.0
  - pubblica SOLO gli echi: un pixel coperto e asciutto e' nodata, non 0
    (misurato sul frame 2026-11-10T12:00Z: 20,27% di pixel finiti nella
    finestra italiana, 1.700 zeri su 389.930 finiti = 0,44%). Quindi nodata
    NON significa "fuori copertura" e non va interpretato come "assenza di eco
    misurata": il modulo non lo fa.

LETTURA EFFICIENTE: il COG viene aperto con GDAL `/vsicurl/` e si legge SOLO la
finestra che contiene l'area di interesse (bbox EPSG:4326 configurabile,
default 6-20E / 35-48N = 1.294x1.486 px su 3.800x4.400). MISURATO il
2026-11-10 sul frame 12:00Z: apertura 0,56 s, lettura 1,07 s, 5 richieste
Range per 771.852 byte su 3.850.164 del file intero (20,0%). Se `/vsicurl/`
non e' disponibile (GDAL senza curl) il modulo degrada al download integrale
nel `cache_dir` e lo dichiara in `meta["mode"] = "download"`.

ATTRIBUZIONE (obbligatoria a valle): OPERA CIRRUS composite, (c) EUMETNET
OPERA, CC BY 4.0 (via Open Radar Data / MeteoGate).
"""

import datetime as _dt
import hashlib
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from . import models

S3_BASE = "https://s3.waw3-1.cloudferro.com/openradar-24h/"
PRODUCT = "DBZH"
EXTENSION = "tiff"
USER_AGENT = "MeteoRisk-Light-radar-engine/1.2.4"

# Nodata dichiarato dal COG. Il valore e' grande e negativo: si accetta una
# tolleranza di 1 dBZ per assorbire eventuali riscalature del produttore.
DEFAULT_NODATA = -9999000.0
NODATA_TOLERANCE = 1.0

# Clamp fisico. OPERA codifica DBZH in un range dichiarato di -31,5..95,5 dBZ;
# qui si scarta tutto cio' che e' fuori da [-32, 95] come NON fisico (un valore
# di 1e6 dBZ non e' un eco piu' forte: e' un dato rotto).
DBZ_MIN = -32.0
DBZ_MAX = 95.0

LICENSE = "CC BY 4.0"
ATTRIBUTION = ("OPERA CIRRUS composite - (c) EUMETNET OPERA, CC BY 4.0 "
               "(via Open Radar Data / MeteoGate)")
SOURCE_NAME = "OPERA CIRRUS (EUMETNET)"

_KEY_RE = re.compile(
    r"OPERA@(?P<stamp>\d{8}T\d{4})@0@(?P<product>[A-Z0-9]+)\.(?P<ext>h5|tiff)$")


# ---------------------------------------------------------------------------
# Errori / helper HTTP (stdlib: nessuna dipendenza extra)
# ---------------------------------------------------------------------------
def _http_get(url, timeout_s=60, headers=None):
    """GET binario anonimo. Ritorna (status, bytes, headers). Solleva SourceError."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raise models.SourceError(f"http_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise models.SourceError(f"network:{exc.__class__.__name__}") from exc


def parse_key_time(key):
    """`OPERA@YYYYMMDDTHHMM@0@DBZH.tiff` -> datetime UTC nominale del prodotto.

    Solleva ValueError se la chiave non ha il formato atteso (mai inventare
    un istante: l'istante di validita' viene dal nome, non dall'orologio)."""
    m = _KEY_RE.search(os.path.basename(str(key)))
    if not m:
        raise ValueError(f"unparseable_opera_key:{os.path.basename(str(key))}")
    return _dt.datetime.strptime(m.group("stamp"), "%Y%m%dT%H%M").replace(
        tzinfo=_dt.timezone.utc)


def key_time_ms(key):
    return int(parse_key_time(key).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Discovery S3 (ListObjectsV2 anonimo, con paginazione)
# ---------------------------------------------------------------------------
def list_day_keys(day, s3_base=S3_BASE, max_keys=1000, timeout_s=60,
                  max_pages=10):
    """Tutte le chiavi sotto `YYYY/MM/DD/OPERA/COMP/` (paginazione inclusa).

    `day` puo' essere una stringa 'YYYY/MM/DD' o un datetime/date UTC."""
    if not isinstance(day, str):
        day = day.strftime("%Y/%m/%d")
    prefix = f"{day}/OPERA/COMP/"
    keys = []
    token = None
    for _ in range(int(max_pages)):
        params = {"list-type": "2", "prefix": prefix, "max-keys": int(max_keys)}
        if token:
            params["continuation-token"] = token
        url = f"{s3_base}?{urllib.parse.urlencode(params)}"
        _status, body, _hdrs = _http_get(url, timeout_s=timeout_s)
        text = body.decode("utf-8", "replace")
        keys.extend(re.findall(r"<Key>([^<]+)</Key>", text))
        if "<IsTruncated>true</IsTruncated>" not in text:
            break
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>",
                      text)
        if not m:
            break
        token = m.group(1)
    return keys


def list_slot_keys(day, product=PRODUCT, extension=EXTENSION, **kw):
    """Chiavi del solo prodotto/formato richiesto, ordinate per istante."""
    suffix = f"@{product}.{extension}"
    keys = [k for k in list_day_keys(day, **kw) if k.endswith(suffix)]
    return sorted(keys)


def discover_latest(reference_dt=None, product=PRODUCT, extension=EXTENSION,
                    lookback_days=2, s3_base=S3_BASE, timeout_s=60):
    """Chiave COMP piu' recente <= reference_dt (default: adesso UTC).

    Ritorna dict {key,url,time_ms,time_iso,listing_bytes,listing_seconds}.
    Solleva SourceError se non trova nulla nel lookback (nessuna chiave
    inventata)."""
    ref = reference_dt or _dt.datetime.now(_dt.timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_dt.timezone.utc)
    ref_ms = int(ref.timestamp() * 1000)
    best = None
    for back in range(int(lookback_days) + 1):
        day = (ref - _dt.timedelta(days=back)).strftime("%Y/%m/%d")
        t0 = time.perf_counter()
        keys = list_slot_keys(day, product=product, extension=extension,
                              s3_base=s3_base, timeout_s=timeout_s)
        secs = time.perf_counter() - t0
        candidates = []
        for k in keys:
            try:
                t_ms = key_time_ms(k)
            except ValueError:
                continue
            if t_ms <= ref_ms:
                candidates.append((t_ms, k))
        if candidates:
            t_ms, key = max(candidates)
            return {
                "key": key,
                "url": s3_base + key,
                "time_ms": int(t_ms),
                "time_iso": _iso_ms(t_ms),
                "listing_seconds": round(secs, 3),
            }
        if best is None:
            best = {"day": day, "n_keys": len(keys)}
    raise models.SourceError(
        f"opera_no_product:{product}.{extension}:lookback={lookback_days}d"
        f":last_day={best}")


def resolve_slots(target_times_ms, slot_tolerance_s=300, **kw):
    """Mappa ogni istante target all'ultima chiave OPERA entro la tolleranza.

    Il composito e' a 5 minuti e la VMI DPC a 5 minuti: si cerca la chiave con
    |t_key - t_target| <= slot_tolerance_s e, a parita', la piu' recente <=
    target. Nessuna chiave -> il target non compare nel risultato (il chiamante
    degrada a DPC-only per quel frame)."""
    out = {}
    by_day = {}
    for t_ms in sorted(int(t) for t in target_times_ms):
        day = _dt.datetime.fromtimestamp(t_ms / 1000.0, _dt.timezone.utc)
        by_day.setdefault(day.strftime("%Y/%m/%d"), []).append(t_ms)
    tol_ms = int(slot_tolerance_s) * 1000
    for day, targets in by_day.items():
        keys = []
        for k in list_slot_keys(day, **kw):
            try:
                keys.append((key_time_ms(k), k))
            except ValueError:
                continue
        if not keys:
            continue
        keys.sort()
        for t_ms in targets:
            best = None
            for k_ms, k in keys:
                delta = k_ms - t_ms
                if abs(delta) <= tol_ms and (best is None or abs(delta) < best[0]):
                    best = (abs(delta), k, k_ms)
            if best is not None:
                out[t_ms] = {"key": best[1], "time_ms": int(best[2]),
                             "delta_s": round((best[2] - t_ms) / 1000.0, 1)}
    return out


# ---------------------------------------------------------------------------
# Lettura raster (COG via /vsicurl/ o download integrale)
# ---------------------------------------------------------------------------
class OperaFrame:
    """Finestra OPERA letta e validata, pronta per la riproiezione.

    `data` float32 in dBZ con NaN dove il COG e' nodata; `valid` e' la maschera
    dei pixel con un valore fisico. `meta` porta i costi misurati (byte, tempo,
    modo di lettura) senza inventare nulla: se un campo non e' stato misurato
    vale None."""

    def __init__(self, data, transform, crs, rows, cols, time_ms, time_iso,
                 meta):
        self.data = data
        self.valid = np.isfinite(data)
        self.transform = transform
        self.crs = crs
        self.rows = int(rows)
        self.cols = int(cols)
        self.time_ms = int(time_ms) if time_ms is not None else None
        self.time_iso = time_iso
        self.meta = dict(meta or {})

    def summary(self):
        v = self.valid
        n = int(v.sum())
        return {
            "time_iso": self.time_iso,
            "rows": self.rows,
            "cols": self.cols,
            "valid_pixels": n,
            "valid_pct": round(100.0 * n / float(self.rows * self.cols), 3)
            if self.rows and self.cols else None,
            "min_dbz": float(self.data[v].min()) if n else None,
            "max_dbz": float(self.data[v].max()) if n else None,
            **self.meta,
        }


def _iso_ms(ms):
    return _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _mask_nodata(raw, nodata):
    bad = ~np.isfinite(raw)
    for s in (DEFAULT_NODATA, nodata):
        if s is None:
            continue
        bad |= np.abs(raw - float(s)) <= NODATA_TOLERANCE
    return bad


def _clean(raw, nodata, clamp=None):
    """float32 dBZ con NaN su nodata e clamp fisico. Ritorna (data, n_clipped)."""
    lo_b, hi_b = DBZ_MIN, DBZ_MAX
    if clamp:
        lo_b, hi_b = float(clamp[0]), float(clamp[1])
    bad = _mask_nodata(raw, nodata)
    out = np.asarray(raw, dtype="float32").copy()
    out[bad] = np.nan
    finite = np.isfinite(out)
    n_clip = 0
    if finite.any():
        lo = out < lo_b
        hi = out > hi_b
        n_clip = int(lo.sum() + hi.sum())
        if n_clip:
            out[lo] = lo_b
            out[hi] = hi_b
    return out, n_clip


def _window_for_bbox(ds, bbox_wgs84):
    """Window raster che contiene il bbox EPSG:4326 (clippata al raster)."""
    from rasterio.windows import Window, from_bounds
    from rasterio.warp import transform_bounds
    left, bottom, right, top = [float(v) for v in bbox_wgs84]
    b = transform_bounds("EPSG:4326", ds.crs, left, bottom, right, top,
                         densify_pts=21)
    win = from_bounds(b[0], b[1], b[2], b[3], ds.transform)
    win = win.round_offsets().round_lengths()
    c0 = max(0, int(win.col_off))
    r0 = max(0, int(win.row_off))
    c1 = min(ds.width, int(win.col_off + win.width))
    r1 = min(ds.height, int(win.row_off + win.height))
    if c1 <= c0 or r1 <= r0:
        raise models.SourceError("opera_bbox_outside_raster")
    return Window(c0, r0, c1 - c0, r1 - r0)


def _head_bytes(url, timeout_s=30):
    """Content-Length del COG (per il rapporto Range/file). None se non esposto."""
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            v = resp.headers.get("Content-Length")
            return int(v) if v else None
    except Exception:
        return None


def measure_frame_bytes(url, bbox_wgs84, timeout_s=90):
    """Byte realmente trasferiti per leggere la finestra (MISURA, non stima).

    Su Windows GDAL e Python usano CRT diversi: `os.dup2(2)` NON cattura le
    righe di CPL_CURL_VERBOSE scritte dalla DLL di GDAL (verificato: il file
    di cattura resta a 0 byte). Quindi la lettura viene ripetuta in un
    SOTTOPROCESSO con stderr rediretto, e si sommano le richieste Range.
    Costo dichiarato: UNA lettura in piu' della stessa finestra.
    Ritorna {range_requests, bytes_read, seconds}."""
    import subprocess
    import sys
    env = dict(os.environ)
    env["CPL_CURL_VERBOSE"] = "YES"
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    cmd = [sys.executable, "-m", "radar_engine.source_opera", "--measure",
           url, "--bbox", ",".join(str(float(v)) for v in bbox_wgs84),
           "--timeout", str(int(timeout_s))]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, env=env, text=True,
                          errors="replace", timeout=max(60, int(timeout_s) * 3))
    secs = time.perf_counter() - t0
    rng = [(int(m.group(1)), int(m.group(2))) for m in
           re.finditer(r"Range: bytes=(\d+)-(\d+)", proc.stderr or "")]
    return {
        "range_requests": len(rng),
        "bytes_read": sum(b - a + 1 for a, b in rng) if rng else 0,
        "seconds": round(secs, 3),
    }


def read_frame(url, bbox_wgs84, time_ms=None, time_iso=None,
               measure_bytes=False, cache_dir=None, timeout_s=60,
               use_vsicurl=True, clamp_dbz=None):
    """Legge la finestra italiana di un COG OPERA (o degrada al download).

    Ritorna OperaFrame. `meta` contiene: mode, bytes_file, bytes_read,
    range_requests, seconds_open, seconds_read, window, clipped_pixels.
    Con `measure_bytes=True` la lettura viene RIPETUTA in un sottoprocesso per
    contare i byte Range (costo dichiarato, solo per il banco di misura).
    Solleva models.SourceError / models.ValidationError."""
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover
        raise models.ValidationError("rasterio_missing") from exc

    meta = {"mode": None, "bytes_file": None, "bytes_read": None,
            "range_requests": None, "seconds_open": None,
            "seconds_read": None, "window": None, "clipped_pixels": None,
            "url": url}
    try:
        if use_vsicurl:
            meta["mode"] = "vsicurl"
            t0 = time.perf_counter()
            env = rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                GDAL_HTTP_TIMEOUT=str(int(timeout_s)),
                GDAL_HTTP_MAX_RETRY="1",
                VSI_CACHE="FALSE",
            )
            with env:
                with rasterio.open("/vsicurl/" + url) as ds:
                    meta["seconds_open"] = round(time.perf_counter() - t0, 3)
                    frame = _read_from_dataset(ds, bbox_wgs84, time_ms,
                                               time_iso, meta, clamp_dbz)
        else:
            # --- fallback: download integrale nel cache_dir ---
            meta["mode"] = "download"
            if not cache_dir:
                raise models.SourceError("opera_cache_dir_missing")
            os.makedirs(cache_dir, exist_ok=True)
            local = os.path.join(cache_dir, "opera_" + os.path.basename(url))
            t0 = time.perf_counter()
            _status, data, _hdrs = _http_get(url, timeout_s=max(timeout_s, 180))
            with open(local, "wb") as fh:
                fh.write(data)
            meta["bytes_file"] = len(data)
            meta["bytes_read"] = len(data)
            meta["seconds_open"] = round(time.perf_counter() - t0, 3)
            meta["sha256"] = hashlib.sha256(data).hexdigest()
            with rasterio.open(local) as ds:
                frame = _read_from_dataset(ds, bbox_wgs84, time_ms, time_iso,
                                           meta, clamp_dbz)
        if measure_bytes:
            m = measure_frame_bytes(url, bbox_wgs84, timeout_s=timeout_s)
            # OperaFrame.meta e' una COPIA del dict: si aggiorna il frame, non
            # il dict locale (bug trovato misurando: i byte restavano None).
            frame.meta["range_requests"] = m["range_requests"]
            frame.meta["bytes_read"] = m["bytes_read"]
            frame.meta["measure_seconds"] = m["seconds"]
        return frame
    except models.EngineError:
        raise
    except Exception as exc:
        raise models.ValidationError(f"opera_raster_error:{exc}") from exc

def _read_from_dataset(ds, bbox_wgs84, time_ms, time_iso, meta,
                       clamp_dbz=None):
    if ds.count < 1:
        raise models.ValidationError("opera_no_bands")
    win = _window_for_bbox(ds, bbox_wgs84)
    meta["window"] = [int(win.col_off), int(win.row_off),
                      int(win.width), int(win.height)]
    if meta.get("bytes_file") is None:
        meta["bytes_file"] = _head_bytes(ds.name.replace("/vsicurl/", ""))
    t1 = time.perf_counter()
    raw = ds.read(1, window=win)
    meta["seconds_read"] = round(time.perf_counter() - t1, 3)
    data, n_clip = _clean(raw, ds.nodata, clamp_dbz)
    meta["clipped_pixels"] = n_clip
    tr = ds.window_transform(win)
    return OperaFrame(data=data, transform=tr, crs=ds.crs,
                      rows=raw.shape[0], cols=raw.shape[1],
                      time_ms=time_ms, time_iso=time_iso, meta=meta)


def fetch_frame(config, reference_dt=None, target_ms=None, measure_bytes=True):
    """Scopre e legge il frame OPERA piu' adatto al riferimento.

    `target_ms`: istante della VMI da affiancare (None -> adesso). Ritorna
    OperaFrame con `time_ms` nominale del composito."""
    ocfg = (config or {}).get("opera") or {}
    bbox = ocfg.get("bbox_wgs84") or [6.0, 35.0, 20.0, 48.0]
    ref = reference_dt or _dt.datetime.now(_dt.timezone.utc)
    tol = int(ocfg.get("slot_tolerance_s") or 300)
    if target_ms is None:
        info = discover_latest(
            ref, product=ocfg.get("product") or PRODUCT,
            extension=ocfg.get("extension") or EXTENSION,
            lookback_days=int(ocfg.get("lookback_days") or 2),
            s3_base=ocfg.get("s3_base") or S3_BASE,
            timeout_s=int(ocfg.get("timeout_s") or 60))
    else:
        slots = resolve_slots(
            [int(target_ms)], slot_tolerance_s=tol,
            s3_base=ocfg.get("s3_base") or S3_BASE,
            timeout_s=int(ocfg.get("timeout_s") or 60))
        info = slots.get(int(target_ms))
        if info is None:
            raise models.SourceError(
                f"opera_no_slot:{_iso_ms(int(target_ms))}:tol={tol}s")
        info["url"] = (ocfg.get("s3_base") or S3_BASE) + info["key"]
    frame = read_frame(
        info["url"], bbox,
        time_ms=info["time_ms"], time_iso=_iso_ms(info["time_ms"]),
        measure_bytes=measure_bytes,
        cache_dir=(config or {}).get("source", {}).get("cache_dir"),
        timeout_s=int(ocfg.get("timeout_s") or 60),
        use_vsicurl=bool(ocfg.get("use_vsicurl", True)),
        clamp_dbz=ocfg.get("clamp_dbz"))
    frame.meta["key"] = info["key"]
    frame.meta["listing_seconds"] = info.get("listing_seconds")
    return frame


# ---------------------------------------------------------------------------
# Riproiezione sulla griglia DPC (stessa griglia del mosaico VMI)
# ---------------------------------------------------------------------------
def reproject_to_grid(frame, dst_transform, dst_crs, dst_shape,
                      resampling="nearest"):
    """dBZ OPERA sulla griglia esatta di destinazione. float32 con NaN fuori.

    `nearest` e' il default CONSERVATIVO: nessuna interpolazione inventa un
    valore che il radar non ha misurato (il max dBZ resta un valore osservato).
    """
    from rasterio.warp import reproject, Resampling
    dst = np.full((int(dst_shape[0]), int(dst_shape[1])), np.nan,
                  dtype="float32")
    res = getattr(Resampling, str(resampling).lower())
    reproject(
        source=frame.data,
        destination=dst,
        src_transform=frame.transform,
        src_crs=frame.crs,
        src_nodata=np.nan,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=np.nan,
        resampling=res,
    )
    return dst


def bbox_of_grid(transform, crs, rows, cols):
    """bbox EPSG:4326 della griglia (per documentazione/manifest)."""
    from rasterio.warp import transform_bounds
    left = transform.c
    top = transform.f
    right = left + transform.a * cols
    bottom = top + transform.e * rows
    b = transform_bounds(crs, "EPSG:4326", left, bottom, right, top,
                         densify_pts=21)
    return [round(float(v), 5) for v in b]

def _cli_measure(argv):
    """Sottoprocesso di misura: legge la finestra e lascia che GDAL scriva su
    stderr le richieste Range (CPL_CURL_VERBOSE=YES impostato dal padre)."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", required=True)
    ap.add_argument("--bbox", required=True)
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args(argv)
    bbox = [float(v) for v in args.bbox.split(",")]
    frame = read_frame(args.measure, bbox, timeout_s=args.timeout)
    print(frame.summary())
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    raise SystemExit(_cli_measure(_sys.argv[1:]))
