#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — main.py (Fase 1, CLI)

Pipeline:
  fetch (findLastProductByType + downloadProduct) -> validazione GeoTIFF
  -> preprocessing (validità dato, CRS, georeferenziazione)
  -> convective cell detection (per frame)
  -> storm tracking multi-frame (Hungarian, cost combinata)
  -> preliminary scoring (Organization Score 0-100)
  -> convective structure (additivo): SOI 0-100 su celle + storm object
  -> Phase 2 (additivo): hook/struttura/ambiente/fulmini/OT -> stack severo
  -> output atomico derivati (data/radar)

FAILURE POLICY (exit codes):
  0  = ok            (almeno 1 frame valido, output scritto, status=ok)
  3  = degraded      (almeno 1 frame valido ma fetch parziale -> status=degraded)
  4  = error         (nessun frame valido / GeoTIFF corrotto / CRS non
                      interpretabile -> SOLLO latest.json con status=error,
                      storms/tracks restano intatti)

Utilizzo:
  python scripts/radar_engine/main.py [--out-dir data/radar]
                                      [--config <json>]
                                      [--product VMI]
                                      [--max-frames 6]
                                      [--keep-frames]
                                      [--history-dir <dir>]
                                      [--dry-run]
"""

import argparse
import datetime as _dt
import json
import math
import os
import sys

# Bootstrap: consente l'esecuzione sia come `python scripts/radar_engine/main.py`
# sia come modulo package (import assoluti vs via `radar_engine`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import radar_engine as _engine
from radar_engine import models
from radar_engine import output
from radar_engine import history
from radar_engine import fetch
from radar_engine import detect as detect_mod
from radar_engine import tracking as track_mod
from radar_engine.config import load_config, CONFIG

_OK = 0
_DEGRADED = 3
_ERROR = 4

# Convergenza locale: 1 grado di latitudine ~ 111.32 km (WGS84 medio).
_KM_PER_DEG_LAT = 111.32
# Prodotti DPC di struttura -> nome interno (ordine di structure_score).
_STRUCTURE_PRODUCTS = (("vil", "product_vil"), ("etm", "product_etm"),
                       ("poh", "product_poh"), ("low", "product_low"),
                       ("high", "product_high"))
# Componenti Fase 2 calcolati PER CANDIDATO (B2).
_PHASE2_COMPONENTS = ("hook", "structure", "env", "ot", "lightning")


def _utcnow_str():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _engine_meta():
    return {
        "name": _engine.ENGINE_NAME,
        "version": _engine.ENGINE_VERSION,
        "phase": _engine.ENGINE_PHASE,
    }


def _candidate_position(candidate):
    """(lon, lat) float del candidato; None se assente/invalido.

    Nessuna posizione inventata: chiave 'position' assente, corta o non
    numerica -> None (i layer per-candidato restano None)."""
    pos = candidate.get("position") or []
    if len(pos) != 2:
        return None
    try:
        lon, lat = float(pos[0]), float(pos[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    return lon, lat


def _candidate_window(frame, lon, lat, radius_km):
    """Finestra INCLUSIVA (r0, r1, c0, c1) che copre il disco di raggio
    `radius_km` attorno a (lon, lat) sul raster `frame`.

    Conversione inversa lon/lat -> (row, col) con transform/geo_transform del
    raster (models.RasterData.lonlat_to_pixel: transformer CRS<->EPSG:4326 in
    direzione INVERSA + affine inversa ~transform). Gli4 angoli del bounding
    box geografico del disco piu' il centro danno gli estremi: l'intersezione
    con la griglia copre il disco anche dove la proiezione deforma gli assi.

    Ritorna None se la posizione non e' invertibile (fuori dominio CRS) o se
    la finestra non interseca la griglia (nessun pixel inventato, FAIL SAFE)."""
    try:
        rows, cols = int(frame.rows), int(frame.cols)
        radius = float(radius_km)
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if radius <= 0.0 or rows <= 0 or cols <= 0:
        return None
    dlat = radius / _KM_PER_DEG_LAT
    dlon = radius / max(_KM_PER_DEG_LAT
                        * abs(math.cos(math.radians(lat))), 1e-6)
    try:
        r_c, c_c = frame.lonlat_to_pixel(lon, lat)
    except ValueError:
        return None
    rmin = rmax = r_c
    cmin = cmax = c_c
    for s_lat in (-1.0, 1.0):
        for s_lon in (-1.0, 1.0):
            try:
                r, c = frame.lonlat_to_pixel(lon + s_lon * dlon,
                                             lat + s_lat * dlat)
            except ValueError:
                continue            # angolo fuori dominio CRS: nessun dato li'
            rmin, rmax = min(rmin, r), max(rmax, r)
            cmin, cmax = min(cmin, c), max(cmax, c)
    r0, r1 = max(0, rmin), min(rows - 1, rmax)
    c0, c1 = max(0, cmin), min(cols - 1, cmax)
    if r0 > r1 or c0 > c1:
        return None
    return r0, r1, c0, c1


def _crop(window, arr):
    """Ritaglio ndarray sulla finestra (r0, r1, c0, c1) inclusiva."""
    r0, r1, c0, c1 = window
    return arr[r0:r1 + 1, c0:c1 + 1]


def _fetch_structure_products(config, scfg, ref_shape, warnings):
    """Scarica UNA VOLTA i prodotti DPC di struttura (griglia di riferimento).

    Ritorna dict {nome: RasterData | None}: prodotto non configurato, non
    scaricabile o con shape diverso dal riferimento VMI -> None con warning
    (nessun dato inventato, nessun crash del layer)."""
    out = {}
    for name, key in _STRUCTURE_PRODUCTS:
        product = scfg.get(key)
        if not product:
            out[name] = None
            continue
        try:
            gframes, gwarn, _ts = fetch.fetch_frames(config, product=product,
                                                      max_frames=1)
            warnings.extend(gwarn)
        except Exception as exc:
            warnings.append(f"phase2 structure {name}: {exc}")
            out[name] = None
            continue
        if not gframes:
            warnings.append(f"phase2 structure {name}: no frames")
            out[name] = None
            continue
        rd = gframes[-1]
        if tuple(rd.data.shape) != tuple(ref_shape):
            warnings.append(f"phase2 structure {name}: shape mismatch")
            out[name] = None
            continue
        out[name] = rd
    return out


def _ir108_grid_mismatch(ref, cand):
    """True solo se un metadata e' confrontabile su ENTRAMBI i lati e differisce.

    Confronti: crs_wkt/crs, transform affine entro tolleranza numerica,
    geo_transform (affine o to_wkt), pixel_area_km2 entro tolleranza relativa
    1e-6. Un campo assente/None su almeno un lato -> saltato (mai un mismatch
    inventato). Nessun campo confrontabile -> None (nessuna decisione)."""
    def _str_eq(a, b):
        if a is None or b is None:
            return None
        return str(a) == str(b)

    def _affine_eq(a, b, tol=1e-9):
        if a is None or b is None:
            return None
        try:
            xa = (a.a, a.b, a.c, a.d, a.e, a.f)
            xb = (b.a, b.b, b.c, b.d, b.e, b.f)
        except AttributeError:
            wa = a.to_wkt() if hasattr(a, "to_wkt") else None
            wb = b.to_wkt() if hasattr(b, "to_wkt") else None
            if wa is None or wb is None:
                return None
            return wa == wb
        eq = True
        for x, y in zip(xa, xb):
            try:
                x, y = float(x), float(y)
            except (TypeError, ValueError):
                return None
            if not (math.isfinite(x) and math.isfinite(y)):
                return None
            if abs(x - y) > tol * max(1.0, abs(x), abs(y)):
                eq = False
        return eq

    def _f64_eq(a, b, rel_tol=1e-6):
        if a is None or b is None:
            return None
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(a) and math.isfinite(b)):
            return None
        return a == b or abs(a - b) <= rel_tol * max(abs(a), abs(b))

    checks = (
        _str_eq(getattr(ref, "crs_wkt", None), getattr(cand, "crs_wkt", None)),
        _str_eq(getattr(ref, "crs", None), getattr(cand, "crs", None)),
        _affine_eq(getattr(ref, "transform", None),
                   getattr(cand, "transform", None)),
        _affine_eq(getattr(ref, "geo_transform", None),
                   getattr(cand, "geo_transform", None)),
        _f64_eq(getattr(ref, "pixel_area_km2", None),
                getattr(cand, "pixel_area_km2", None)),
    )
    if all(c is None for c in checks):
        return None
    return any(c is False for c in checks)


def _fetch_ir108(config, ocfg, ref_rd, warnings):
    """Scarica il frame DPC IR_108 (OVERSHOOTING TOP), griglia di riferimento.

    ref_rd: RasterData VMI di riferimento (frames[-1]). Ritorna RasterData |
    None: prodotto non configurato, non scaricabile, senza frame o con griglia
    NON allineata alla VMI -> None con warning (nessun dato inventato). Il
    frame IR_108 usa la STESSA griglia TM della VMI (evidenza file reale:
    1200x1400, res 1000 m): shape diverso -> pagina probabilmente cambiata ->
    layer assente (fail-closed); shape uguale ma CRS/transform/geo_transform/
    pixel_area diversi -> stesso shape ma footprint geografico diverso:
    correrebbe in silenzio male il crop della finestra OT calcolata sulla VMI,
    quindi il frame viene rifiutato (B5). Un metadata assente su almeno un lato
    non viene confrontato (nessun mismatch inventato)."""
    product = ocfg.get("product_ir108")
    if not product:
        warnings.append("ot_unavailable:ir108_non_configurato")
        return None
    try:
        gframes, gwarn, _ts = fetch.fetch_frames(config, product=product,
                                                  max_frames=1)
        warnings.extend(gwarn)
    except Exception as exc:
        warnings.append(f"phase2 ot ir108: {exc}")
        return None
    if not gframes:
        warnings.append("phase2 ot ir108: no frames")
        return None
    rd = gframes[-1]
    if tuple(rd.data.shape) != tuple(ref_rd.data.shape):
        warnings.append("phase2 ot ir108: shape mismatch")
        return None
    if _ir108_grid_mismatch(ref_rd, rd):
        warnings.append("phase2 ot ir108: grid mismatch")
        return None
    return rd


def _structure_at_window(products, window, vs):
    """Score + features struttura verticale SULLA FINESTRA del candidato.

    products: dict nome -> RasterData (None = prodotto assente); window:
    (r0, r1, c0, c1) o None. Ritorna (score, features) oppure (None, None)
    quando nessun prodotto e' disponibile, la finestra e' vuota/fuori griglia
    o non contiene alcun valore finito (dato assente -> nessun punteggio).

    UNITA': al ritaglio l'ETM di prodotto (metri) e' convertito in km
    (vs.etm_to_km) e il POH (frazione) in % (vs.poh_to_percent), in modo che
    le soglie 6/9/12 km e 30/50/70 % siano applicate alle unita' giuste.
    I prodotti non configurati restano NaN -> membership 0 (mai inventati)."""
    import numpy as np

    if window is None or not any(rd is not None for rd in products.values()):
        return None, None
    r0, r1, c0, c1 = window
    shape = (int(r1) - int(r0) + 1, int(c1) - int(c0) + 1)
    if shape[0] <= 0 or shape[1] <= 0:
        return None, None
    nan_grid = np.full(shape, np.nan, dtype="float64")
    grids = {}
    any_finite = False
    for name, _key in _STRUCTURE_PRODUCTS:
        rd = products.get(name)
        if rd is None:
            grids[name] = nan_grid
            continue
        g = _crop(window, rd.data)
        if name == "etm":
            g = vs.etm_to_km(g)            # metri -> km (x/1000)
        elif name == "poh":
            g = vs.poh_to_percent(g)       # frazione -> % (x100)
        grids[name] = g
        if not any_finite and bool(np.isfinite(g).any()):
            any_finite = True
    if not any_finite:
        return None, None
    g5 = tuple(grids[n] for n, _k in _STRUCTURE_PRODUCTS)
    return vs.structure_score(*g5), vs.structure_features(*g5)


def _hook_for_candidate(frames, lon, lat, radius_km, hcfg, hook_mod):
    """Hook morfologico sul FOOTPRINT del candidato (ultime N griglie).

    Ogni frame viene ritagliato sulla finestra ±radius_km attorno al
    candidato e la coppia (data, valid_mask) passata a
    hook.hook_score_footprint: finestra assente/fuori griglia -> None.
    Ritorna lo score persistito (float) oppure None."""
    try:
        n_hist = max(1, int(hcfg.get("history_frames", 3)))
    except (TypeError, ValueError):
        n_hist = 3
    windows = []
    for fr in frames[-n_hist:]:
        win = _candidate_window(fr, lon, lat, radius_km)
        if win is None:
            return None
        windows.append((_crop(win, fr.data), _crop(win, fr.valid_mask)))
    if not windows:
        return None
    return hook_mod.hook_score_footprint(
        windows, dbz_threshold=hcfg.get("dbz_threshold"))


def _fetch_extra_cappi(config, scfg, products, ref_shape, warnings):
    """Scarica i livelli CAPPI configurati che NON sono gia' in `products`.

    Serve a MESH (Witt et al. 1998: >= 4 livelli per il profilo dBZ(H)). Con la
    config di default `cappi_products` = {2.0: CAPPI_2, 6.0: CAPPI_6} questa
    funzione NON fa richieste: i due prodotti sono product_low/product_high,
    gia' scaricati. Ogni livello in piu' costa un download DPC: il costo e'
    dichiarato nella config, non nascosto.

    Ritorna {nome_prodotto: RasterData|None}; prodotto non scaricabile o con
    shape diverso -> None con warning (fail-closed, nessuna griglia finta)."""
    already = {}
    if scfg.get("product_low"):
        already[str(scfg["product_low"])] = products.get("low")
    if scfg.get("product_high"):
        already[str(scfg["product_high"])] = products.get("high")
    extra = {}
    for name in (scfg.get("cappi_products") or {}).values():
        name = str(name)
        if name in already or name in extra:
            continue
        try:
            gframes, gwarn, _ts = fetch.fetch_frames(config, product=name,
                                                      max_frames=1)
            warnings.extend(gwarn)
        except Exception as exc:
            warnings.append(f"cappi {name}: {exc}")
            extra[name] = None
            continue
        if not gframes:
            warnings.append(f"cappi {name}: no frames")
            extra[name] = None
            continue
        rd = gframes[-1]
        if tuple(rd.data.shape) != tuple(ref_shape):
            warnings.append(f"cappi {name}: shape mismatch")
            extra[name] = None
            continue
        extra[name] = rd
    return extra


def _cappi_level_map(scfg, products, extra):
    """{quota_km: RasterData|None} dei livelli CAPPI disponibili per MESH."""
    by_name = {}
    if scfg.get("product_low"):
        by_name[str(scfg["product_low"])] = products.get("low")
    if scfg.get("product_high"):
        by_name[str(scfg["product_high"])] = products.get("high")
    for name, rd in (extra or {}).items():
        by_name[str(name)] = rd
    out = {}
    for key, name in (scfg.get("cappi_products") or {}).items():
        try:
            h = float(key)
        except (TypeError, ValueError):
            continue
        out[h] = by_name.get(str(name))
    return out


def _structure_summary(cand):
    """Sintesi del descrittore per lo stack severo (campo con fonte)."""
    from radar_engine import severe_stack as ss
    from radar_engine.convective_structure import ROTATION_NULL_REASON
    soi = cand.get("soi")
    return {
        "structure_id": cand.get("structure_id"),
        "soi": ss.field(
            None if soi is None else int(soi),
            "radar_engine.convective_structure (SOI 0-100)",
            "descrittore morfologico interno: nessuna validazione locale, "
            "NON una probabilita'",
            null_reason=None if soi is not None else "soi_not_computed"),
        "level": cand.get("level"),
        "phase": cand.get("phase"),
        "rotation_measurable": False,
        "rotation_null_reason": ROTATION_NULL_REASON,
    }


def _severe_stack_for_candidate(window, products, cappi, env_detail,
                                ot_details, bundle):
    """S1 + S2 + S3 del candidato (1.2.4.17).

    Ogni componente mancante esce con `null_reason`: nessun valore inventato.
    S3 riusa satellite_ot (details gia' calcolati sulla stessa finestra IR)."""
    from radar_engine import severe_stack as ss

    env_layer = ss.environment_layer(env_detail)

    vil = etm = None
    levels = []
    if window is not None:
        if products.get("vil") is not None:
            vil = _crop(window, products["vil"].data)
        if products.get("etm") is not None:
            etm = _crop(window, products["etm"].data)
        for h_km in sorted(cappi or {}):
            rd = cappi[h_km]
            if rd is not None:
                levels.append((h_km, _crop(window, rd.data)))
    h0 = (env_detail or {}).get("freezing_level_m")
    lr = (env_detail or {}).get("lr_700_500")
    h20 = ss.minus20_from_lapse(h0, lr)
    hail = ss.hail_layer(levels, vil, etm, h0_m=h0, h20_m=h20)
    ot = ss.ot_layer_from_details(
        ot_details, timestamp_iso=getattr(bundle, "radar_timestamp_iso", None))
    return {"environment": env_layer, "hail": hail, "ot": ot,
            "h0_m": h0, "h20_m": h20, "cappi_levels": len(levels)}


def _record_predictions(bundle, config, out_dir):
    """Registro JSONL delle predizioni (1.2.4.17, best-effort).

    Rotazione a `predictions_retention_days` (30) + append. Un errore NON
    cambia l'rc del run: finisce nei warning (stessa politica di history)."""
    try:
        from radar_engine import severe_predictions as pred_mod
    except Exception as exc:                      # pragma: no cover
        bundle.warnings.append(f"severe_predictions import failed: {exc}")
        return None
    secfg = ((config.get("phase2") or {}).get("severe_stack") or {})
    if not secfg.get("predictions_enabled", True):
        return None
    try:
        bundle.severe_predictions = pred_mod.record_predictions(
            bundle, out_dir,
            version=(bundle.engine or {}).get("version"),
            retention_days=int(secfg.get("predictions_retention_days",
                                         pred_mod.RETENTION_DAYS)))
    except Exception as exc:
        bundle.warnings.append(f"severe_predictions failed: {exc}")
    return bundle.severe_predictions


def _phase2_evaluate(bundle, config):
    """Fase 2 (additivo): sub-layer A1 -> indice v2 + stack severo PER CANDIDATO.

    Policy: ogni sotto-layer e' OPZIONALE; errore/dato assente -> warning in
    bundle.warnings + sub-score None PER IL CANDIDATO toccato (mai crash del
    run, nessun dato inventato). Da B2 hook/struttura/ambiente/fulmini sono
    calcolati sulla FINESTRA LOCALE del candidato (footprint ±km, cache per
    slot/bucket condivise fra i candidati); l'OT (0.5.0) usa il prodotto DPC
    IR_108 e satellite_ot sulla finestra del candidato. L'SSI v2 rinormalizza
    i pesi sui componenti presenti (aggregate). I candidati Fase 1 sono solo
    ARRICCHITI: ssi/level/... e bundle.status restano intatti (lo status e'
    gia' calcolato alla riga 85 del pipeline, prima di questo layer)."""
    p2_cfg = config.get("phase2") or {}
    cs_cfg = config.get("convective_structure") or {}
    warnings = bundle.warnings
    if not p2_cfg.get("enabled"):
        bundle.phase2 = {"status": "disabled", "warnings": warnings}
        return
    candidates = getattr(bundle, "convective_structures", None) or []
    if not candidates:
        warnings.append("phase2: no candidates")
        bundle.phase2 = {"status": "unavailable", "warnings": warnings}
        return
    try:
        from radar_engine import phase2 as p2_mod
        from radar_engine.phase2 import (aggregate, environment, hook,
                                         lightning, satellite_ot,
                                         vertical_structure)
    except Exception as exc:
        warnings.append(f"phase2 import failed: {exc}")
        bundle.phase2 = {"status": "unavailable", "warnings": warnings}
        return

    result = {"status": "unavailable", "version": p2_mod.PHASE2_VERSION,
              "warnings": warnings}
    bundle.phase2 = result
    frames = list(getattr(bundle, "frames", []) or [])

    # --- CONFIG (raggi finestra per-candidato + finestra fulmini) ----------
    hcfg = p2_cfg.get("hook") or {}
    scfg = p2_cfg.get("structure") or {}
    ecfg = p2_cfg.get("environment") or {}
    lcfg = p2_cfg.get("lightning") or {}
    otcfg = p2_cfg.get("ot") or {}
    # R1: i default di fallback dei raggi NON sono magic numbers, ma i valori
    # base di config.CONFIG["phase2"] (fonte unica); usati solo se la chiave
    # manca nel blocco iniettato.
    hook_radius = float(hcfg.get(
        "footprint_radius_km", CONFIG["phase2"]["hook"]["footprint_radius_km"]))
    struct_radius = float(scfg.get(
        "local_radius_km", CONFIG["phase2"]["structure"]["local_radius_km"]))
    ltg_radius = float(lcfg.get("radius_km", 30.0))
    ot_radius = float(otcfg.get(
        "footprint_radius_km", CONFIG["phase2"]["ot"]["footprint_radius_km"]))
    try:
        ltg_min_strikes = int(lcfg.get("min_strikes", 1))
    except (TypeError, ValueError):
        ltg_min_strikes = 1
    env_enabled = bool(ecfg.get("enabled", True))

    def _warn(msg):
        """Warning deduplicato (i layer per-candidato girano N volte)."""
        if msg not in warnings:
            warnings.append(msg)

    # --- STRUTTURA + IR_108: prodotti DPC scaricati UNA VOLTA (rif. VMI) ----
    products = {}
    ir_rd = None
    cappi = {}
    if frames:
        products = _fetch_structure_products(config, scfg,
                                             frames[-1].data.shape, warnings)
        if not any(rd is not None for rd in products.values()):
            warnings.append("phase2 structure: no products available")
        # CAPPI per MESH: default 2 livelli (CAPPI_2/CAPPI_6, GIA' scaricati),
        # nessuna richiesta extra. >=4 livelli solo se configurati.
        extra_cappi = _fetch_extra_cappi(config, scfg, products,
                                         frames[-1].data.shape, warnings)
        cappi = _cappi_level_map(scfg, products, extra_cappi)
        # OVERSHOOTING TOP: frame IR_108 (stessa griglia TM della VMI).
        ir_rd = _fetch_ir108(config, otcfg, frames[-1], warnings)
    else:
        warnings.append("phase2 structure: no frames")
        ir_rd = None

    # --- FULMINI: slot scaricati UNA VOLTA, strike in cache per slot --------
    # Scansione all'INDIETRO fino a max_backoff_steps: si raccolgono gli slot
    # pubblicati PIU' RECENTI fino a window_slots disponibili, cosi' un ritardo
    # di pubblicazione oltre la finestra base non azzera il layer fulmini.
    try:
        window = max(2, min(int(lcfg.get("window_slots", 4)),
                            lightning.LIGHTNING_TREND_WINDOW))
    except (TypeError, ValueError):
        window = lightning.LIGHTNING_TREND_WINDOW
    # E (1.2.4.17): il backoff effettivo e' il MINIMO fra config e costante
    # del modulo (LTG_MAX_BACKOFF_STEPS = 12). Prima la config diceva 36 e il
    # modulo 12: un run senza slot pubblicati produceva 37 richieste HTTP 403
    # (STUDIO_SUPERCELLE_RADAR sez. 6.4). Ora: un solo tentativo per slot e
    # stop dopo `max_consecutive_failures` errori CONSECUTIVI, ma SOLO dopo il
    # grace period dei 9 passi di ritardo di pubblicazione MISURATO: fermarsi
    # prima azzererebbe il layer fulmini in ogni run (il primo slot utile e'
    # tipicamente a k=9) invece di risparmiare richieste.
    try:
        max_backoff = max(0, min(int(lcfg.get("max_backoff_steps",
                                              lightning.LTG_MAX_BACKOFF_STEPS)),
                                 int(lightning.LTG_MAX_BACKOFF_STEPS)))
    except (TypeError, ValueError):
        max_backoff = int(lightning.LTG_MAX_BACKOFF_STEPS)
    try:
        max_failures = max(1, int(lcfg.get("max_consecutive_failures", 3)))
    except (TypeError, ValueError):
        max_failures = 3
    try:
        grace = max(0, min(int(lcfg.get("publication_grace_steps",
                                        lightning.LTG_PUBLICATION_DELAY_STEPS)),
                          max_backoff))
    except (TypeError, ValueError):
        grace = min(lightning.LTG_PUBLICATION_DELAY_STEPS, max_backoff)
    slot_cache = {}
    ltg_slots_recent = []
    failures_after_grace = 0      # errori di fila DOPO il grace (criterio di stop)
    for k in range(0, max_backoff + 1):   # dal piu' recente all'indietro
        if len(ltg_slots_recent) >= window:
            break
        epoch = lightning.ltg_epoch_floor(backoff_steps=k)
        if epoch in slot_cache:
            slot = slot_cache[epoch]
        else:
            try:
                slot = lightning.fetch_ltg(epoch_ms=epoch)["strikes"]
            except lightning.LightningFetchError as exc:
                warnings.append(f"phase2 lightning slot-{k}: {exc}")
                slot = None
            except Exception as exc:      # guasto imprevisto -> slot assente
                warnings.append(f"phase2 lightning failed: {exc}")
                slot = None
            slot_cache[epoch] = slot
        if slot is not None:
            failures_after_grace = 0
            ltg_slots_recent.append(slot)
        else:
            if k >= grace:
                failures_after_grace += 1
                if failures_after_grace >= max_failures:
                    warnings.append(
                        f"phase2 lightning: stop dopo {failures_after_grace} "
                        f"errori consecutivi oltre il grace di {grace} passi "
                        f"(backoff max {max_backoff})")
                    break
    # dal piu' vecchio al piu' recente (ordine atteso da lightning_spatial_score)
    ltg_slots = list(reversed(ltg_slots_recent))
    if not ltg_slots:
        warnings.append("phase2 lightning: no slots available (wider window)")

    # --- LAYER PER CANDIDATO: hook/struttura/fulmini/ambiente ---------------
    # Cache condivise: env per bucket (0.1 gradi), fulmini per slot epoch ->
    # N candidati = UNA chiamata per bucket/slot. Ogni sub-valore finisce
    # sul SINGOLO candidato (chiavi lette dai chip dell'app).
    env_cache = {}
    env_bucket_deg = ecfg.get("cache_grid_deg", environment.ENV_CACHE_GRID_DEG)
    env_failures = 0
    no_position = False
    for c in candidates:
        pos = _candidate_position(c)
        if pos is None:
            no_position = True
            lon = lat = None
        else:
            lon, lat = pos

        # Finestra struttura sul riferimento (ultima VMI): hook usa la sua
        # propria finestra per ciascuno degli ultimi frame della storia.
        window = (_candidate_window(frames[-1], lon, lat, struct_radius)
                  if frames and lon is not None else None)

        hook_score = None
        if lon is not None:
            try:
                hook_score = _hook_for_candidate(frames, lon, lat, hook_radius,
                                                 hcfg, hook)
            except Exception as exc:
                _warn(f"phase2 hook failed: {exc}")

        structure_score = None
        structure_feats = None
        if window is not None:
            try:
                structure_score, structure_feats = _structure_at_window(
                    products, window, vertical_structure)
            except Exception as exc:
                _warn(f"phase2 structure failed: {exc}")

        # Overshooting top: finestra IR del candidato -> score + dettagli.
        ot_score = None
        ot_details = None
        if ir_rd is not None and lon is not None and frames:
            ot_window = _candidate_window(frames[-1], lon, lat, ot_radius)
            if ot_window is not None:
                try:
                    ctt = _crop(ot_window, ir_rd.data)
                    vm = (_crop(ot_window, ir_rd.valid_mask)
                          if ir_rd.valid_mask is not None else None)
                    if ctt.size:
                        ot_details = satellite_ot.evaluate_ir_ot(
                            ctt, valid_mask=vm,
                            pixel_area_km2=ir_rd.pixel_area_km2,
                            ctt_threshold_c=otcfg.get("ctt_threshold_c"),
                            anvil_threshold_c=otcfg.get("anvil_threshold_c"),
                            anomaly_threshold_c=otcfg.get(
                                "anomaly_threshold_c"),
                            ring_radius_px=otcfg.get("ring_radius_px"),
                            ring_directions=otcfg.get("ring_directions"),
                            min_anvil_samples=otcfg.get("min_anvil_samples"),
                            erosion_kernel=otcfg.get("erosion_kernel"))
                        if not ot_details.get("valid_pixels"):
                            ot_details = None    # nessun pixel valido
                        else:
                            ot_score = ot_details.get("score")
                except Exception as exc:
                    _warn(f"phase2 ot failed: {exc}")
                    ot_score = None
                    ot_details = None

        lightning_score = None
        if lon is not None:
            try:
                counts = [(lightning.count_strikes_in_radius(
                    s, lon, lat, ltg_radius) if s is not None else None)
                    for s in ltg_slots]
                lightning_score = lightning.lightning_spatial_score(
                    counts, min_strikes=ltg_min_strikes)
            except Exception as exc:
                _warn(f"phase2 lightning failed: {exc}")

        env_score = None
        env_detail = None
        if env_enabled and lon is not None:
            detail = None
            try:
                detail = environment.evaluate_environment_cached(
                    lat, lon, cache=env_cache, grid_deg=env_bucket_deg,
                    timeout_s=ecfg.get("timeout_s"))
            except Exception as exc:
                _warn(f"phase2 environment failed: {exc}")
            if detail is None:
                env_failures += 1
            else:
                env_score = detail.get("env_score")
                env_detail = detail
                if result.get("environment") is None:
                    result["environment"] = {
                        "scp": detail.get("scp"), "stp": detail.get("stp"),
                        "ship": detail.get("ship"),
                        "env_score": detail.get("env_score"),
                        "completeness": detail.get("completeness"),
                        "partial": detail.get("partial"),
                        "position": [lon, lat],
                        "bucket": list(environment.cache_bucket(
                            lat, lon, env_bucket_deg)),
                    }

        c["hook"] = hook_score
        c["structure"] = structure_score
        if structure_feats is None:
            c["structure_features"] = None
        else:
            # 'poc' e' il refuso storico di 'poh' (Probability Of Hail): alias
            # emesso con lo stesso valore per compatibilita' con A3.
            c["structure_features"] = dict(structure_feats,
                                            poc=structure_feats.get("poh_max"))
        c["ot"] = ot_score
        c["ot_details"] = ot_details
        c["lightning"] = lightning_score
        c["env"] = env_score
        c["env_scp"] = env_score        # chip 'Env' (0-100; SCP grezzo sopra)

        # --- STACK SEVERO (1.2.4.17): S1 + S2 + S3 per candidato -------------
        try:
            parts = _severe_stack_for_candidate(
                window, products, cappi, env_detail, ot_details, bundle)
            from radar_engine import severe_stack as _severe
            c["severe_stack"] = _severe.combine(
                structure=_structure_summary(c), hail=parts["hail"],
                environment=parts["environment"], ot=parts["ot"])
            c["hail"] = parts["hail"]
        except Exception as exc:
            _warn(f"phase2 severe_stack failed: {exc}")
            c["severe_stack"] = None
            c["hail"] = None

    if no_position:
        _warn("phase2: candidate without valid position -> per-cell layers None")
    if env_failures:
        _warn(f"phase2 environment: {env_failures} candidate bucket(s) "
              "without data")

    # --- STATUS: componenti presenti su ALMENO UN candidato -----------------
    comps = {name: sum(1 for c in candidates if c.get(name) is not None)
             for name in _PHASE2_COMPONENTS}
    result["components_status"] = comps
    present = sum(1 for v in comps.values() if v)
    result["status"] = ("ok" if present == len(_PHASE2_COMPONENTS)
                        else "partial" if present else "unavailable")

    # --- SSI v2 per candidato (pesi rinormalizzati sui presenti) ------------
    weights = (p2_cfg.get("aggregate") or {}).get("weights")
    from radar_engine.convective_structure import derive_confidence
    try:
        for c in candidates:
            v2 = aggregate.aggregate_ssi_v2(
                float(c.get("soi") or 0.0), c.get("hook"), c.get("structure"),
                c.get("env"), c.get("ot"), c.get("lightning"), weights=weights)
            c["soi_v2"] = v2["soi_v2"]
            # A (1.2.4.17): la confidenza si RICALCOLA con la copertura dati
            # nota (5 descrittori Fase 2) invece di restare la costante "low".
            present = sum(1 for k in _PHASE2_COMPONENTS
                          if c.get(k) is not None)
            conf = derive_confidence(
                gates=c.get("gates") or {},
                n_points=int(c.get("n_points") or 0),
                coverage=present / float(len(_PHASE2_COMPONENTS)),
                cfg=cs_cfg)
            c["confidence"] = conf["confidence"]
            c["confidence_detail"] = conf
    except ValueError as exc:
        warnings.append(f"phase2 aggregate weights invalid: {exc}")


def _run(config, out_dir, product, max_frames, dry_run, history_dir=None):
    bundle = models.EngineBundle("ok", _utcnow_str(), output.SOURCE_LABEL)
    bundle.engine = _engine_meta()

    # ---- FECTH + VALIDAZIONE ------------------------------------------------
    try:
        frames, warnings, latest_ts = fetch.fetch_frames(
            config, product=product, max_frames=max_frames
        )
    except models.SourceError as exc:
        bundle.warnings = [f"fetch failed: {exc}"]
        return bundle, _ERROR

    bundle.frames = frames
    bundle.warnings = list(warnings)
    if not frames:
        bundle.status = "error"
        bundle.warnings.append("no valid frames available")
        return bundle, _ERROR

    bundle.status = "degraded" if warnings else "ok"
    bundle.radar_timestamp_ms = frames[-1].time_ms
    bundle.radar_timestamp_iso = frames[-1].time_iso
    try:
        bundle.data_latency_minutes = round(
            (_dt.datetime.now(_dt.timezone.utc).timestamp() * 1000 - frames[-1].time_ms)
            / 60000.0, 1
        )
    except Exception:
        bundle.data_latency_minutes = None

    # ---- DETECTION ----------------------------------------------------------
    cfg_detect = config["detect"]
    for idx, raster in enumerate(frames):
        try:
            cells = detect_mod.detect_cells(raster, cfg_detect)
        except Exception as exc:
            bundle.warnings.append(f"detect@{raster.time_iso} failed: {exc}")
            cells = []
        for c in cells:
            c.frame_index = idx
        bundle.cells_by_frame.append(cells)

    # ---- TRACKING (>=2 frame validi; altrimenti solo detection) -------------
    tracker = track_mod.Tracker(config["tracking"],
                                cfg_scoring=config.get("scoring"))
    for idx, cells in enumerate(bundle.cells_by_frame):
        tracker.update(cells, idx)
    tracker.complete_cycles()
    bundle.tracks = tracker.finalize(cfg_scoring=config.get("scoring"))

    # ---- MULTI-SCALE: STORM OBJECT LAYER (Fase 1.7, additivo) ---------------
    # Il layer cella resta intatto; l'aggregazione e lo storm tracking sono
    # un layer di identità aggiuntivo. Errori -> warning (non fa fallire il run).
    storm_cfg = config.get("storm", {"enabled": False})
    if storm_cfg.get("enabled"):
        try:
            from radar_engine import aggregation as agg_mod
            from radar_engine import storm_tracking as storm_mod
            bundle.storm_objects_by_frame = [
                agg_mod.aggregate_frame(cells, idx, storm_cfg)
                for idx, cells in enumerate(bundle.cells_by_frame)]
            storm_tracker = storm_mod.StormObjectTracker(
                storm_cfg, cfg_scoring=config.get("scoring"))
            for idx, objs in enumerate(bundle.storm_objects_by_frame):
                storm_tracker.update(objs, idx)
            storm_tracker.complete_cycles()
            bundle.storm_tracks = storm_tracker.finalize(
                cfg_scoring=config.get("scoring"))
        except Exception as exc:  # layer additivo: mai rompere il pipeline base
            bundle.warnings.append(f"storm_layer failed: {exc}")
            bundle.storm_objects_by_frame = []
            bundle.storm_tracks = []

    # ---- CONVECTIVE STRUCTURE (descrittore SOI, additivo) ------------------
    # SOI su celle + storm object tracciati (stessi dati Fase 1, zero richieste
    # rete aggiuntive). Nessun Doppler: rotation_measurable=false. Un errore
    # NON fa fallire il run (warning) e la lista resta [].
    cs_cfg = config.get("convective_structure", {"enabled": False})
    if cs_cfg.get("enabled"):
        try:
            from radar_engine import convective_structure as cs_mod
            cs_mod.evaluate(bundle, cs_cfg)
        except Exception as exc:  # layer additivo: mai rompere il pipeline base
            bundle.warnings.append(f"convective_structure layer failed: {exc}")
            bundle.convective_structures = []
            bundle.structure_tracks_evaluated = 0

    # ---- PHASE 2 (Fase 2, additivo): sub-layer A1 -> SSI v2 sui candidati --
    # Layer ADDITIVO: nessun errore fa fallire il pipeline base (warning) e
    # la firma Fase 1 dei candidati (ssi/level/...) resta intatta.
    try:
        _phase2_evaluate(bundle, config)
    except Exception as exc:  # layer additivo: mai rompere il pipeline base
        bundle.warnings.append(f"phase2 layer failed: {exc}")
        bundle.phase2 = {"status": "unavailable", "warnings": bundle.warnings}

    # ---- SCORING incluso in finalize (per track) ----------------------------
    if len(frames) < 2:
        bundle.warnings.append("tracking skipped (<2 valid frames); "
                               "detection output only, tracking_confidence=insufficient")

    # ---- OUTPUT -------------------------------------------------------------
    if dry_run:
        return bundle, _OK

    try:
        output.write_outputs(bundle, out_dir, engine_meta=_engine_meta())
    except models.OutputError as exc:
        return _fail_output(bundle, out_dir, str(exc))
    except Exception as exc:  # guasto imprevisto del writer -> OutputError
        return _fail_output(bundle, out_dir,
                            f"unexpected:{exc.__class__.__name__}:{exc}")
    if history_dir:
        _archive_history(bundle, history_dir)
    _record_predictions(bundle, config, out_dir)
    return bundle, _OK if bundle.status == "ok" else _DEGRADED


def _archive_history(bundle, history_dir):
    """Archivio rolling 2h (history.build_history): BEST-EFFORT.

    L'output derivato e' gia' scritto: un errore dell'archivio viene loggato
    come warning (in bundle.warnings -> summary) e NON cambia l'rc del run."""
    try:
        index = history.build_history(bundle, history_dir)
    except Exception as exc:
        bundle.warnings.append(f"history archive failed: {exc}")
        print(f"[history] WARNING: archivio rolling non aggiornato: {exc}")
        return None
    if index is None:
        print("[history] nessuno scan da archiviare.")
        return None
    print(f"[history] slots={len(index.get('slots', []))} "
          f"window={index.get('window_slots')}")
    return index


def _fail_output(bundle, out_dir, reason):
    """Scrivi SOLO latest.json (status=error) e ritorna rc 4, mai crash."""
    bundle.status = "error"
    try:
        output.write_status_only(out_dir, "error", bundle.generated_at_iso,
                                 bundle.warnings + [f"output failed: {reason}"],
                                 engine_meta=_engine_meta())
    except Exception:
        pass  # out_dir irrecuperabile: rc 4 comunque (nessun exit 1)
    return bundle, _ERROR


def main(argv=None):
    ap = argparse.ArgumentParser(description="Meteorisk Radar Engine — Fase 1")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--product", default="VMI")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--history-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    if args.keep_frames:
        config["source"]["keep_raw_frames"] = True
    out_dir = os.path.abspath(args.out_dir or config["output"]["out_dir"])

    bundle, rc = _run(config, out_dir, args.product, args.max_frames,
                      args.dry_run, history_dir=args.history_dir)

    summary = {
        "status": bundle.status,
        "frames": len(bundle.frames),
        "cells": sum(len(c) for c in bundle.cells_by_frame),
        "tracks": len([t for t in bundle.tracks if len(t.points) >= 2]),
        "structures": len(getattr(bundle, "convective_structures", []) or []),
        "radar_timestamp": bundle.radar_timestamp_iso,
        "latency_min": bundle.data_latency_minutes,
        "warnings": bundle.warnings,
        "phase2_status": (getattr(bundle, "phase2", None) or {}).get(
            "status", "unavailable"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    # GitHub Actions: espone status/stats come step outputs (se presente).
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"status={bundle.status}\n")
            fh.write(f"frames={len(bundle.frames)}\n")
            fh.write(f"cells={summary['cells']}\n")
            fh.write(f"tracks={summary['tracks']}\n")
            fh.write("structure_candidates={}\n".format(
                len(getattr(bundle, "convective_structures", []) or [])))
            fh.write(f"phase2_status={summary['phase2_status']}\n")
            fh.write(f"radar_timestamp={bundle.radar_timestamp_iso or ''}\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())