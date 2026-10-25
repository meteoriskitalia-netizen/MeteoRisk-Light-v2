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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import clock as _clock
from . import models
from . import preprocess
from . import source_dpc

# 1.2.4.26 — download PARALLELI (misurato il 2026-10-25 sul motore reale:
# 6 VMI 50,15 s -> 14,12 s = 3,55x; 10 CAPPI 69,97 s -> 9,75 s = 7,17x; zero
# HTTP 429 a 6 connessioni). Il valore e' un tetto: il pool non supera mai il
# numero di elementi da scaricare. `fetch_workers: 1` riporta al percorso
# seriale storico senza toccare il codice (leva di rollback).
FETCH_WORKERS_DEFAULT = 6
FETCH_WORKERS_MAX = 8


def _parallel_workers(config, items):
    """Worker effettivi per un set di download (>=1, <= items, <= tetto)."""
    src = (config or {}).get("source") or {}
    try:
        n = int(src.get("fetch_workers", FETCH_WORKERS_DEFAULT))
    except (TypeError, ValueError):
        n = FETCH_WORKERS_DEFAULT
    n = max(1, min(n, FETCH_WORKERS_MAX))
    return max(1, min(n, int(items)))


def _cache_path(cache_dir, s3_key):
    """Percorso locale UNICO per chiave S3 (1.2.4.26).

    Il solo `basename` NON basta piu' da quando i download sono paralleli:
    i 10 CAPPI dello stesso slot hanno lo STESSO nome file
    (`CAPPI/CAPPI_<n>/25-10-2026-05-10.tif`), quindi dieci thread
    scrivevano/leggevano/cancellavano lo stesso percorso. Misurato sul run
    reale 2026-10-25T05:10Z: `CAPPI_4` falliva con
    `PermissionError [WinError 32]` (file in uso) e il livello andava perso.
    Qui si conservano le ultime due parti della chiave (prodotto + file),
    separate da `__`: percorso distinto per prodotto e per slot."""
    os.makedirs(cache_dir, exist_ok=True)
    parts = [p for p in str(s3_key).replace("\\", "/").split("/")
             if p not in ("", ".", "..")]
    if not parts:
        return os.path.join(cache_dir, "product.tif")
    return os.path.join(cache_dir, "__".join(parts[-2:]))


def _save_bytes(path, content):
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(content)
    os.replace(tmp, path)
    return path


def _fetch_one_frame(config, product, ts, period_s):
    """Download + validazione di UN candidato. Non solleva MAI: un frame
    illeggibile e' un warning, non la fine del run (stessa policy del loop
    seriale storico). Thread-safe: file distinti, nessuno stato condiviso."""
    src = config["source"]
    cache_dir = src["cache_dir"]
    keep = bool(src.get("keep_raw_frames", False))
    warnings = []
    probe = models.ProductInfo(product, ts, period_s)
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
        return None, [f"frame@{ts} skipped: {exc}"]
    except Exception as exc:                      # rete/parsing imprevisto
        return None, [f"frame@{ts} fetch_failed: "
                      f"{exc.__class__.__name__}: {exc}"]

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
        return None, warnings
    except Exception as exc:
        # read_raster può propagare guasti non tipizzati (es. pyproj sul
        # centroide): il frame viene scartato con warning, mai crash del run.
        warnings.append(
            f"frame@{real_ts} preprocess_failed: "
            f"{exc.__class__.__name__}: {exc}")
        if not keep:
            _cleanup(path)
        return None, warnings
    raster.time_ms = real_ts
    raster.time_iso = _iso(real_ts)
    raster.source_path = path
    if not keep:
        _cleanup(path)
    return raster, warnings


def plan_window(config, product=None, max_frames=None, anchor_ms=None):
    """(slot_ms crescenti, period_s) della finestra di tracking.

    Stessa logica di sempre (anchor = ultimo prodotto pubblicato, oppure
    `anchor_ms` in replay; candidati arrotondati per difetto al passo del
    prodotto, senza duplicati). Estratta da fetch_frames perche' il percorso
    incrementale (fetch_window) deve pianificare gli STESSI slot senza
    scaricarli."""
    src = config["source"]
    product = product or src["product_type"]
    max_frames = int(max_frames or src.get("history_frames", 6))

    if anchor_ms is None:
        latest = source_dpc.get_latest_product(
            product,
            api_base=src.get("api_base"),
            origin=src.get("origin_header"),
            timeout_s=src.get("http_timeout_s", 30),
        )
        period_s = latest.period_s
        anchor = latest.time_ms
    else:
        try:
            period_s = int(src.get("period_s") or 300)
        except (TypeError, ValueError):
            period_s = 300
        anchor = source_dpc.round_down_to_step(int(anchor_ms), period_s)

    step_ms = period_s * 1000
    candidates = []
    seen = set()
    for k in range(max_frames):
        raw_ts = anchor - k * step_ms
        ts = source_dpc.round_down_to_step(raw_ts, period_s)
        if ts in seen:
            continue
        seen.add(ts)
        candidates.append(ts)
    candidates.reverse()  # dal più vecchio al più recente
    return candidates, period_s


def _download_slots(config, product, slots, period_s):
    """Download+validazione dei soli `slots` indicati, in ordine crescente."""
    frames = []
    warnings = []
    workers = _parallel_workers(config, len(slots))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fetched = list(pool.map(
                lambda ts: _fetch_one_frame(config, product, ts, period_s),
                slots))
    else:
        fetched = [_fetch_one_frame(config, product, ts, period_s)
                   for ts in slots]
    # ORDINE: `pool.map` restituisce i risultati nell'ordine dei candidati
    # (dal piu' vecchio al piu' recente), quindi i warning e la finestra
    # restano identici al percorso seriale.
    for raster, fwarn in fetched:
        warnings.extend(fwarn)
        if raster is not None:
            frames.append(raster)
    frames.sort(key=lambda r: r.time_ms)
    # monotonicità STRETTA: drop di eventuali duplicati/correzioni non ordinabili
    deduped = []
    for r in frames:
        if deduped and r.time_ms <= deduped[-1].time_ms:
            warnings.append(f"frame@{r.time_ms} dropped (monotonicity)")
            continue
        deduped.append(r)
    return deduped, warnings


def _resolve_product(config, product):
    """Nome prodotto effettivo (il default e' `source.product_type`).

    Il refactor 1.2.4.26 ha spostato la risoluzione dentro `plan_window`: senza
    questo helper `fetch_frames(product=None)` passava `None` a
    `ProductInfo` e il POST rispondeva HTTP 400 (productType nullo)."""
    return product or ((config.get("source") or {}).get("product_type") or "VMI")


def fetch_frames(config, product=None, max_frames=None, anchor_ms=None):
    """Recupera i frames VMI recenti e li valida, ordinati dal più vecchio.

    Ritorna (frames, warnings, latest_time_ms):
      frames:  list[RasterData] ordinata crescente (timestamp reali)
      latest_time_ms: timestamp EPOCH ms del frame più recente (None se vuoto)
    Solleva SourceError se l'API non risponde affatto (bootstrap fallito).

    `anchor_ms` (1.2.4.20): ancora ESPLICITA degli slot (modalita' --as-of).
      None  -> comportamento storico: `findLastProductByType` decide l'ultimo
               slot disponibile (run operativo).
      valore-> l'ultimo candidato e' `anchor_ms` arrotondato per difetto al
               passo del prodotto, letto da `source.period_s` (default 300 s,
               cadenza PT5M misurata). `findLastProductByType` NON viene
               chiamato: e' proprio la lettura che rendeva il re-run dipendente
               dall'orologio. Uno slot assente resta un buco (404 -> warning),
               mai un timestamp inventato."""
    slots, period_s = plan_window(config, product, max_frames, anchor_ms)
    frames, warnings = _download_slots(config, _resolve_product(config, product),
                                       slots, period_s)
    latest_ts = frames[-1].time_ms if frames else None
    return frames, warnings, latest_ts


def fetch_window(config, product=None, max_frames=None, anchor_ms=None,
                 history_dir=None):
    """Finestra di tracking con RIUSO delle celle archiviate (1.2.4.26).

    Ritorna (frames, warnings, latest_time_ms, reused_by_slot, slots):
      frames   list[RasterData] dei SOLI slot scaricati (crescente)
      reused_by_slot  {slot_ms: list[DetectedCell]} ricostruite dall'archivio
      slots    list[int] epoch ms di TUTTI gli slot della finestra (crescente)

    Regole (nessuna scorciatoia):
      - lo slot dell'ancora si scarica SEMPRE (e' il frame pubblicato: senza,
        `frames[-1]` sarebbe un istante vecchio e radar_timestamp mentirebbe);
      - gli ultimi `phase2_lookback_frames` slot (default 3) restano raster
        reali perche' `_hook_for_candidate` legge le ultime 3 griglie continue:
        riusare le celle produrrebbe un hook diverso (2 finestre invece di 3);
      - ogni altro slot con celle archiviate valide viene riusato;
      - archivio assente/corrotto o `incremental_fetch.enabled=false` ->
        `reused` vuoto = comportamento di fetch_frames (worst case identico).
    Con `history_dir=None` (replay --as-of: nessuno stato scritto) non si
    riusa nulla: un replay non deve dipendere da un archivio live."""
    src = config.get("source") or {}
    inc = src.get("incremental_fetch") or {}
    slots, period_s = plan_window(config, product, max_frames, anchor_ms)
    reused = {}
    enabled = bool(inc.get("enabled", False)) and history_dir is not None
    if enabled and slots:
        try:
            lookback = int(inc.get("phase2_lookback_frames", 3))
        except (TypeError, ValueError):
            lookback = 3
        lookback = max(1, min(lookback, len(slots)))
        keep_rasters = set(slots[-lookback:])
        # import locale: history non importa fetch, nessun ciclo
        from . import history as _history
        signature = _history.cells_signature(config)
        for slot in slots:
            if slot in keep_rasters:
                continue
            cells = _history.read_cells(history_dir, _iso(slot),
                                       signature=signature)
            if cells is not None:
                reused[slot] = cells
    download = [s for s in slots if s not in reused]
    frames, warnings = _download_slots(config, _resolve_product(config, product),
                                       download, period_s)
    latest_ts = frames[-1].time_ms if frames else None
    return frames, warnings, latest_ts, reused, slots



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
    # 1.2.4.20: in modalita' --as-of il tempo di parete NON e' un risultato e
    # renderebbe due run identici byte-diversi. Il campo diventa None (assente
    # come misura), non un numero aggiustato.
    deterministic = bool(_clock.from_config(config).replay)
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
        "elapsed_s": (None if deterministic else round(elapsed, 3)),
        "fallback": False,
        "attempts": 1,
    }
    return raster, meta


def fetch_product_at(config, product, time_ms, period_s=None):
    """Scarica UN prodotto a un timestamp ESPLICITO (modalita' --as-of).

    Perche' esiste: VIL/ETM/POH e IR_108 venivano presi con
    `fetch_frames(max_frames=1)`, cioe' con `findLastProductByType`: l'ultimo
    prodotto di OGGI. In replay si chiede invece lo STESSO slot della VMI di
    riferimento (`ref_rd.time_ms`): un VIL di un altro istante produrrebbe una
    VIL density che non appartiene al volume, e un IR_108 di oggi un
    overshooting top di oggi (retro/REPORT.md §5.2 punti 2 e 3).

    Non solleva: (raster|None, meta). Un prodotto assente e' un dato assente
    dichiarato (`meta["error"]`), non un crash e non un valore inventato."""
    try:
        period_s = int(period_s or (config["source"].get("period_s") or 300))
    except (TypeError, ValueError):
        period_s = 300
    try:
        raster, meta = _read_product_bytes(config, product, int(time_ms),
                                           period_s, None)
    except models.SourceError as exc:
        return None, {"error": str(exc),
                      "not_published": source_dpc.is_not_published_error(exc),
                      "requested_time_ms": int(time_ms)}
    except (models.ValidationError, models.CrsError) as exc:
        return None, {"error": "invalid:{}".format(exc),
                      "not_published": False,
                      "requested_time_ms": int(time_ms)}
    except Exception as exc:
        return None, {"error": "{}:{}".format(exc.__class__.__name__, exc),
                      "not_published": False,
                      "requested_time_ms": int(time_ms)}
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
    workers = _parallel_workers(config, len(products))

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

    def _attempt_many(names, ts, attempt_no):
        """Tentativo su piu' prodotti, in parallelo se configurato.

        1.2.4.26: 10 CAPPI in parallelo (7,17x misurato: 69,97 s -> 9,75 s,
        zero 429 a 6 connessioni). `pool.map` conserva l'ordine di `names`,
        quindi `results`/`metas` restano deterministici; `_attempt` non
        solleva mai, quindi un prodotto fallito non annulla gli altri."""
        if workers <= 1 or len(names) <= 1:
            return [(n,) + _attempt(n, ts, attempt_no) for n in names]
        with ThreadPoolExecutor(max_workers=min(workers, len(names))) as pool:
            return list(pool.map(
                lambda n: (n,) + _attempt(n, ts, attempt_no), names))

    for name, raster, meta in _attempt_many(products, ref_time_ms, 1):
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
        for name, raster, meta in _attempt_many(
                pending, ref_time_ms, 1 + retry_attempts):
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
        for name, raster, meta in _attempt_many(
                products, prev_ts, 2 + retry_attempts):
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
        "seconds": (None if _clock.from_config(config).replay
                    else round(_time.monotonic() - t_start, 3)),
        "ref_time_iso": _iso(int(ref_time_ms)),
        "products": metas,
    }
    return results, summary
