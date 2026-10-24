#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — fetch.py

Costruzione della finestra temporale multi-frame (target T-20..T0, VMI PT5M)
a partire dai timestamp REALI dei prodotti Radar-DPC.

Regole:
  - il bootstrap usa findLastProductByType (NO WebSocket in Fase 1, GitHub
    Actions = ambiente non persistente);
  - la finestra è costruita SUI timestamp dei prodotti (mai orologio di sistema
    per inferire l'ora radar);
  - candidati arrotondati per difetto al passo del prodotto;
  - frame non disponibili (404) -> buco temporale, NON timestamp inventati;
  - verifica di monotonicità strettamente crescente dei timestamp;
  - ogni download è validato (magic TIFF + apertura raster + CRS);
  - i raw GeoTIFF vivono SOLO in cache_dir (escluso dal repo via .gitignore).
"""

import os
from datetime import datetime, timezone

from . import models
from . import preprocess
from . import source_dpc


def _cache_path(cache_dir, s3_key):
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, os.path.basename(s3_key))


def _save_bytes(path, content):
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(content)
    os.replace(tmp, path)
    return path


def fetch_frames(config, product=None, max_frames=None):
    """Recupera i frames VMI recenti e li valida, ordinati dal più vecchio.

    Ritorna (frames, warnings, latest_time_ms):
      frames:  list[RasterData] ordinata crescente (timestamp reali)
      latest_time_ms: timestamp EPOCH ms del frame più recente (None se vuoto)
    Solleva SourceError se l'API non risponde affatto (bootstrap fallito)."""
    src = config["source"]
    product = product or src["product_type"]
    max_frames = int(max_frames or src.get("history_frames", 6))

    latest = source_dpc.get_latest_product(
        product,
        api_base=src.get("api_base"),
        origin=src.get("origin_header"),
        timeout_s=src.get("http_timeout_s", 30),
    )

    step_ms = latest.period_s * 1000
    candidates = []
    seen = set()
    for k in range(max_frames):
        raw_ts = latest.time_ms - k * step_ms
        ts = source_dpc.round_down_to_step(raw_ts, latest.period_s)
        if ts in seen:
            continue
        seen.add(ts)
        candidates.append(ts)
    candidates.reverse()  # dal più vecchio al più recente

    cache_dir = src["cache_dir"]
    keep = bool(src.get("keep_raw_frames", False))
    frames = []
    warnings = []
    for ts in candidates:
        probe = models.ProductInfo(product, ts, latest.period_s)
        try:
            s3_key, content = source_dpc.download_product(
                probe,
                api_base=src.get("api_base"),
                origin=src.get("origin_header"),
                timeout_s=src.get("http_timeout_s", 30),
                download_timeout_s=src.get("download_timeout_s", 60),
                retries=src.get("max_download_retries", 2),
            )
        except models.SourceError as exc:
            warnings.append(f"frame@{ts} skipped: {exc}")
            continue

        try:
            real_ts = source_dpc.parse_key_timestamp(s3_key)
        except ValueError as exc:
            real_ts = ts
            warnings.append(f"frame@{ts}: {exc} (fallback al candidato)")
        path = _save_bytes(_cache_path(cache_dir, s3_key), content)
        try:
            source_dpc.validate_download(path)
            raster = preprocess.read_raster(
                path,
                nodata_values=src_preprocess_nodata(config),
                declared_nodata_from_tiff=True,
                geo_plausible_bbox=config["preprocess"].get("geo_plausible_bbox"),
            )
        except (models.ValidationError, models.CrsError) as exc:
            warnings.append(f"frame@{real_ts} invalid: {exc}")
            if not keep:
                _cleanup(path)
            continue
        except Exception as exc:
            # read_raster può propagare guasti non tipizzati (es. pyproj sul
            # centroide): il frame viene scartato con warning, mai crash del run.
            warnings.append(
                f"frame@{real_ts} preprocess_failed: "
                f"{exc.__class__.__name__}: {exc}")
            if not keep:
                _cleanup(path)
            continue
        raster.time_ms = real_ts
        raster.time_iso = _iso(real_ts)
        raster.source_path = path
        frames.append(raster)
        if not keep:
            _cleanup(path)

    frames.sort(key=lambda r: r.time_ms)
    # monotonicità STRETTA: drop di eventuali duplicati/correzioni non ordinabili
    deduped = []
    for r in frames:
        if deduped and r.time_ms <= deduped[-1].time_ms:
            warnings.append(f"frame@{r.time_ms} dropped (monotonicity)")
            continue
        deduped.append(r)
    frames = deduped

    latest_ts = frames[-1].time_ms if frames else None
    return frames, warnings, latest_ts


def src_preprocess_nodata(config):
    return config["preprocess"].get("nodata_values", [])


def _cleanup(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 1.2.4.18 — PRODOTTI A TIMESTAMP ALLINEATO (CAPPI per MESH/overhang)
# ---------------------------------------------------------------------------
def _read_product_bytes(config, product, time_ms, period_s, downloader):
    """Download + validazione di UN prodotto al timestamp richiesto.

    Ritorna (RasterData, meta). Solleva models.SourceError (propagata dal
    downloader) o models.ValidationError/CrsError: il chiamante decide se
    ritentare. Nessun timestamp inventato: `used_time_ms` viene dal nome della
    key S3 se interpretabile, altrimenti resta quello richiesto e il meta lo
    dichiara (`key_timestamp_parsed=false`)."""
    import time as _time
    src = config["source"]
    probe = models.ProductInfo(product, int(time_ms), int(period_s))
    t0 = _time.monotonic()
    fn = downloader or source_dpc.download_product
    s3_key, content = fn(
        probe,
        api_base=src.get("api_base"),
        origin=src.get("origin_header"),
        timeout_s=src.get("http_timeout_s", 30),
        download_timeout_s=src.get("download_timeout_s", 60),
        retries=src.get("max_download_retries", 2),
    )
    elapsed = _time.monotonic() - t0
    try:
        real_ts = source_dpc.parse_key_timestamp(s3_key)
        key_parsed = True
    except ValueError:
        real_ts = int(time_ms)
        key_parsed = False
    path = _save_bytes(_cache_path(src["cache_dir"], s3_key), content)
    keep = bool(src.get("keep_raw_frames", False))
    try:
        source_dpc.validate_download(path)
        raster = preprocess.read_raster(
            path,
            nodata_values=src_preprocess_nodata(config),
            declared_nodata_from_tiff=True,
            geo_plausible_bbox=config["preprocess"].get("geo_plausible_bbox"),
        )
    except Exception:
        if not keep:
            _cleanup(path)
        raise
    raster.time_ms = int(real_ts)
    raster.time_iso = _iso(int(real_ts))
    raster.source_path = path
    if not keep:
        _cleanup(path)
    meta = {
        "requested_time_ms": int(time_ms),
        "used_time_ms": int(real_ts),
        "used_time_iso": raster.time_iso,
        "key_timestamp_parsed": key_parsed,
        "bytes": len(content),
        "elapsed_s": round(elapsed, 3),
        "fallback": False,
        "attempts": 1,
    }
    return raster, meta


def fetch_cappi_set(config, products, ref_time_ms, period_s=None, warnings=None,
                    sleeper=None, downloader=None, retry_wait_s=None,
                    retry_attempts=None, allow_previous_slot=None):
    """Scarica un SET di prodotti (CAPPI) allo STESSO timestamp della VMI.

    Perche' non `fetch_frames`: i CAPPI sono pubblicati ~2 min dopo VMI/VIL/ETM
    (+9m03s vs +6m53s misurato il 2026-10-23) e sono un altro prodotto
    dell'API; chiedere `findLastProductByType` per ciascuno darebbe timestamp
    DIVERSI e la colonna dBZ(H) mescolerebbe due volumi. Qui il timestamp e'
    quello del frame VMI di riferimento (`ref_time_ms`).

    Gestione del "non ancora pubblicato" (MISURATO): POST allo slot corrente ->
    HTTP 500 con errore S3, NON 404. Sequenza:
      1) tentativo su ref_time_ms per tutti i prodotti;
      2) UNA attesa condivisa (retry_wait_s) e nuovo tentativo SOLO sui 5xx;
      3) fallback a ref_time_ms - period (se allow_previous_slot).
    Costo limitato: al massimo UNA attesa per run, non una per prodotto.

    Ritorna (results, summary):
      results = {product: RasterData|None}
      summary = {requested, fetched, fallback, failed, bytes, seconds,
                 ref_time_iso, products: {product: meta}}
    Non solleva MAI: un prodotto mancante degrada solo il campo che lo usa.
    """
    import time as _time
    scfg = (((config.get("phase2") or {}).get("structure") or {})
            .get("cappi_fetch") or {})
    if period_s is None:
        period_s = int(scfg.get("period_s") or 300)
    if retry_wait_s is None:
        retry_wait_s = float(scfg.get("retry_wait_s", 20.0))
    if retry_attempts is None:
        retry_attempts = int(scfg.get("retry_attempts", 1))
    if allow_previous_slot is None:
        allow_previous_slot = bool(scfg.get("allow_previous_slot", True))
    if sleeper is None:
        sleeper = _time.sleep
    warnings = warnings if warnings is not None else []
    t_start = _time.monotonic()
    results = {}
    metas = {}
    pending = []

    def _attempt(name, ts, attempt_no):
        try:
            raster, meta = _read_product_bytes(config, name, ts, period_s,
                                               downloader)
        except models.SourceError as exc:
            return None, {"error": str(exc),
                          "not_published": source_dpc.is_not_published_error(exc),
                          "attempts": attempt_no}
        except (models.ValidationError, models.CrsError) as exc:
            return None, {"error": "invalid:{}".format(exc),
                          "not_published": False, "attempts": attempt_no}
        except Exception as exc:                 # rete/parsing imprevisto
            return None, {"error": "{}:{}".format(exc.__class__.__name__, exc),
                          "not_published": False, "attempts": attempt_no}
        meta["attempts"] = attempt_no
        return raster, meta

    for name in products:
        raster, meta = _attempt(name, ref_time_ms, 1)
        metas[name] = meta
        results[name] = raster
        if raster is None and meta.get("not_published"):
            pending.append(name)

    if pending and retry_attempts > 0:
        warnings.append(
            "cappi: {} prodotto/i non ancora pubblicati allo slot {} "
            "(HTTP 5xx) — retry dopo {}s".format(
                len(pending), _iso(int(ref_time_ms)), retry_wait_s))
        if retry_wait_s > 0:
            sleeper(retry_wait_s)
        still = []
        for name in pending:
            raster, meta = _attempt(name, ref_time_ms, 1 + retry_attempts)
            if raster is None:
                still.append(name)
                metas[name].update(meta)
            else:
                results[name] = raster
                metas[name] = meta
        pending = still

    if pending and allow_previous_slot:
        prev_ts = int(ref_time_ms) - int(period_s) * 1000
        warnings.append(
            "cappi: fallback allo slot precedente {} — set COMPLETO "
            "ri-scaricato per coerenza ({} prodotto/i mancavano)".format(
                _iso(prev_ts), len(pending)))
        # COERENZA: si ri-scarica TUTTO il set allo slot precedente. Una
        # colonna dBZ(H) composta da livelli di due volumi diversi non e' un
        # profilo verticale (il probe ha misurato 31-39% di violazioni
        # CAPPI(t) > VMI(t-5min) contro 0% con lo stesso volume).
        for name in products:
            raster, meta = _attempt(name, prev_ts, 2 + retry_attempts)
            if raster is not None:
                meta["fallback"] = True
                results[name] = raster
                metas[name] = meta
            elif results.get(name) is None:
                metas[name].update(meta)

    fetched = sum(1 for r in results.values() if r is not None)
    failed = [n for n, r in results.items() if r is None]
    slots = sorted({int(m["used_time_ms"]) for m in metas.values()
                    if m.get("used_time_ms") is not None})
    summary = {
        "requested": len(products),
        "fetched": fetched,
        "fallback": sum(1 for m in metas.values() if m.get("fallback")),
        "failed": failed,
        "used_slots": [_iso(s) for s in slots],
        "homogeneous": len(slots) == 1,
        "bytes": int(sum(m.get("bytes") or 0 for m in metas.values())),
        "seconds": round(_time.monotonic() - t_start, 3),
        "ref_time_iso": _iso(int(ref_time_ms)),
        "products": metas,
    }
    return results, summary
