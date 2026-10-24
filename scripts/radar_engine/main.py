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
from radar_engine import clock as clock_mod
from radar_engine import models
from radar_engine import output
from radar_engine import history
from radar_engine import fetch
from radar_engine import footprint as footprint_mod
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
    """STORICO (1.2.4.19): orologio di sistema. Dalla 1.2.4.20 il motore usa
    SOLO il clock iniettato (`clock.Clock`); questa funzione resta per i
    chiamanti esterni che la importavano e non e' piu' sul percorso del run."""
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


def _cappi_product_map(scfg):
    """{quota_km: product_type} dei livelli CAPPI da scaricare per MESH.

    `mesh_levels_enabled=False` limita il set a product_low/product_high (il
    comportamento 1.2.4.17): MESH esce null con "cappi_levels<4" e il costo
    per run torna quello della release precedente. Nessun livello inventato:
    la mappa e' quella dichiarata in config."""
    out = {}
    for key, name in (scfg.get("cappi_products") or {}).items():
        try:
            h = float(key)
        except (TypeError, ValueError):
            continue
        out[h] = str(name)
    if not scfg.get("mesh_levels_enabled", True):
        low = scfg.get("product_low")
        high = scfg.get("product_high")
        out = {h: n for h, n in out.items()
               if n in (str(low), str(high))}
    return out


def _fetch_structure_products(config, scfg, ref_shape, warnings,
                              cappi_rd=None, ref_time_ms=None, replay=False):
    """Scarica UNA VOLTA i prodotti DPC di struttura (griglia di riferimento).

    vil/etm/poh: `fetch_frames` (il loro ultimo prodotto, come in 1.2.4.17).
    low/high (CAPPI): NON si scaricano qui — arrivano da `cappi_rd`, cioe' dal
    set allineato al timestamp della VMI (fetch.fetch_cappi_set): chiedere
    `findLastProductByType` per un CAPPI darebbe un istante diverso da quello
    della VMI e la colonna verticale mescolerebbe due volumi.
    Ritorna dict {nome: RasterData | None}: prodotto non configurato, non
    scaricabile o con shape diverso dal riferimento -> None con warning
    (nessun dato inventato, nessun crash del layer).

    1.2.4.20 (`replay=True`): vil/etm/poh sono chiesti allo STESSO
    `ref_time_ms` della VMI (`fetch.fetch_product_at`), non all'ultimo
    prodotto. Era il difetto #2 di retro/REPORT.md §5.2: la VIL density veniva
    calcolata su un VIL di oggi."""
    out = {}
    for name, key in _STRUCTURE_PRODUCTS:
        if name in ("low", "high"):
            rd = (cappi_rd or {}).get(name)
            if rd is None:
                out[name] = None
                continue
            if tuple(rd.data.shape) != tuple(ref_shape):
                warnings.append(f"phase2 structure {name}: shape mismatch")
                out[name] = None
                continue
            out[name] = rd
            continue
        product = scfg.get(key)
        if not product:
            out[name] = None
            continue
        if replay and ref_time_ms is not None:
            rd, meta = fetch.fetch_product_at(config, product, ref_time_ms)
            if rd is None:
                warnings.append(
                    "phase2 structure {}: assente allo slot {} ({})".format(
                        name, fetch._iso(int(ref_time_ms)),
                        meta.get("error")))
                out[name] = None
                continue
            if tuple(rd.data.shape) != tuple(ref_shape):
                warnings.append(f"phase2 structure {name}: shape mismatch")
                out[name] = None
                continue
            out[name] = rd
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


def _fetch_cappi_levels(config, scfg, ref_rd, warnings):
    """Scarica il SET dei CAPPI allo STESSO productDate della VMI (1.2.4.18).

    Ritorna (results {quota_km: RasterData|None}, names {nome_prodotto: rd},
    summary). Un livello mancante NON ferma il run: il campo che lo usa
    (MESH/overhang) degrada a null con motivo. La griglia di ogni livello deve
    combaciare con quella della VMI: altrimenti il livello viene scartato
    (mai un resampling silenzioso su una colonna verticale)."""
    cmap = _cappi_product_map(scfg)
    names = list(dict.fromkeys(cmap.values()))
    results = {h: None for h in cmap}
    by_name = {}
    summary = {"requested": 0, "fetched": 0, "fallback": 0, "failed": [],
               "bytes": 0, "seconds": 0.0,
               "ref_time_iso": getattr(ref_rd, "time_iso", None)}
    if not names:
        return results, by_name, summary
    got, summary = fetch.fetch_cappi_set(
        config, names, int(ref_rd.time_ms), warnings=warnings)
    # COERENZA VERTICALE: se per qualche ragione i livelli arrivano da slot
    # diversi (fallback parziale), si tiene SOLO lo slot con piu' livelli (a
    # parita', il piu' recente): un profilo dBZ(H) mescolato non e' un profilo.
    by_slot = {}
    for name, rd in got.items():
        if rd is not None:
            by_slot.setdefault(int(rd.time_ms), []).append(name)
    keep_slot = None
    if by_slot:
        keep_slot = max(by_slot, key=lambda s: (len(by_slot[s]), s))
        keep_iso = fetch._iso(keep_slot)
        for name, rd in got.items():
            if rd is not None and int(rd.time_ms) != keep_slot:
                warnings.append(
                    "cappi {}: scartato (slot {} != {} del set)".format(
                        name, rd.time_iso, keep_iso))
                got[name] = None
    for h, name in cmap.items():
        rd = got.get(name)
        if rd is None:
            continue
        if tuple(rd.data.shape) != tuple(ref_rd.data.shape):
            warnings.append(f"cappi {name}: shape mismatch")
            continue
        results[h] = rd
        by_name[name] = rd
    summary["kept_slot_iso"] = (None if keep_slot is None
                                else fetch._iso(keep_slot))
    summary["kept_slot_ms"] = keep_slot
    summary["slot_offset_s"] = (None if keep_slot is None
                                else int((keep_slot - ref_rd.time_ms) / 1000))
    summary["levels_per_slot"] = {fetch._iso(s): len(v)
                                  for s, v in by_slot.items()}
    summary["grid_ok"] = sum(1 for rd in results.values() if rd is not None)
    return results, by_name, summary


def _component_containing(arr, valid, threshold, seed_rc):
    """Maschera della componente connessa di `arr>=threshold` che contiene il
    pixel (seed_rc), nello stesso array. None se il seed non e' sopra soglia.

    1.2.4.21: UNA sola implementazione — `footprint.component_mask`. Questo
    wrapper esiste per il chiamante storico (`_cell_footprint`, overhang); la
    semantica e i numeri sono identici alla 1.2.4.20 (test
    `test_footprint.py::test_component_mask_matches_legacy_definition`)."""
    return footprint_mod.component_mask(arr, valid, threshold, seed_rc)


def _cell_footprint(bundle, candidate, config, target_window=None):
    """(window, mask, source) del footprint della CELLA del candidato.

    Identita' (nessun match geometrico fra blob): `cell_id` del record ->
    l'unica DetectedCell con quel cell_id nel frame; la maschera e' la
    componente connessa (soglia detect.dbz_threshold) che contiene il
    CENTROIDE della cella, ricalcolata sulla VMI di quel frame. Per un record
    storm_object si usa l'UNIONE delle maschere delle celle membre.

    Ritorna (None, None, motivo) quando la cella non e' risolvibile: il
    chiamante ricade sulla finestra ±radius_km e lo DICHIARA (`footprint_source`
    = "candidate_window")."""
    frames = list(getattr(bundle, "frames", []) or [])
    if not frames:
        return None, None, "no_frames"
    thr = float((config.get("detect") or {}).get("dbz_threshold", 20.0))
    cells = []
    cell_id = candidate.get("cell_id")
    storm_id = candidate.get("storm_object_id")
    if storm_id is not None:
        for objs in (getattr(bundle, "storm_objects_by_frame", None) or []):
            for obj in objs or []:
                if str(getattr(obj, "storm_object_id", "")) == str(storm_id):
                    cells = list(getattr(obj, "cells", None) or [])
                    break
            if cells:
                break
    if not cells and cell_id is not None:
        for frame_cells in (getattr(bundle, "cells_by_frame", None) or []):
            for cell in frame_cells or []:
                if cell.cell_id == cell_id:
                    cells = [cell]
                    break
            if cells:
                break
    if not cells:
        return None, None, "cell_not_found"

    # finestra = unione dei bbox delle celle (clampata al raster)
    raster = None
    r0 = c0 = 10 ** 9
    r1 = c1 = -1
    for cell in cells:
        idx = int(getattr(cell, "frame_index", -1))
        if not (0 <= idx < len(frames)):
            continue
        bmin_r, bmin_c, bmax_r, bmax_c = cell.bbox
        r0, c0 = min(r0, bmin_r), min(c0, bmin_c)
        r1, c1 = max(r1, bmax_r), max(c1, bmax_c)
        raster = frames[idx]
    if raster is None:
        return None, None, "cell_frame_missing"
    r0 = max(0, r0 - 1)
    c0 = max(0, c0 - 1)
    r1 = min(int(raster.rows) - 1, r1 + 1)
    c1 = min(int(raster.cols) - 1, c1 + 1)
    if target_window is not None:
        # la maschera deve avere la STESSA forma delle griglie ritagliate
        r0, r1 = int(target_window[0]), int(target_window[1])
        c0, c1 = int(target_window[2]), int(target_window[3])
        r0 = max(0, r0)
        c0 = max(0, c0)
        r1 = min(int(raster.rows) - 1, r1)
        c1 = min(int(raster.cols) - 1, c1)
    if r1 < r0 or c1 < c0:
        return None, None, "empty_window"
    import numpy as np
    mask = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=bool)
    used = 0
    for cell in cells:
        idx = int(getattr(cell, "frame_index", -1))
        if not (0 <= idx < len(frames)):
            continue
        src = frames[idx]
        bmin_r, bmin_c, bmax_r, bmax_c = cell.bbox
        rr0, cc0 = max(0, bmin_r - 1), max(0, bmin_c - 1)
        rr1, cc1 = min(int(src.rows) - 1, bmax_r + 1), min(int(src.cols) - 1,
                                                          bmax_c + 1)
        sub = src.data[rr0:rr1 + 1, cc0:cc1 + 1]
        sub_valid = src.valid_mask[rr0:rr1 + 1, cc0:cc1 + 1]
        seed = (float(cell.centroid_raster[0]) - rr0,
                float(cell.centroid_raster[1]) - cc0)
        comp = _component_containing(sub, sub_valid, thr, seed)
        if comp is None:
            continue
        # intersezione con la finestra comune
        a0, b0 = max(rr0, r0), max(cc0, c0)
        a1, b1 = min(rr1, r1), min(cc1, c1)
        if a1 < a0 or b1 < b0:
            continue
        sub_comp = comp[a0 - rr0:a1 - rr0 + 1, b0 - cc0:b1 - cc0 + 1]
        mask[a0 - r0:a1 - r0 + 1, b0 - c0:b1 - c0 + 1] |= sub_comp
        used += 1
    if used == 0 or not bool(mask.any()):
        return None, None, "cell_mask_not_resolved"
    source = "storm_object_cells" if len(cells) > 1 else "cell_mask"
    return (r0, r1, c0, c1), mask, source


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


def _fetch_ir108(config, ocfg, ref_rd, warnings, replay=False):
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
    non viene confrontato (nessun mismatch inventato).

    1.2.4.20 (`replay=True`): IR_108 chiesto allo slot della VMI
    (`fetch.fetch_product_at`), non all'ultimo prodotto di oggi."""
    product = ocfg.get("product_ir108")
    if not product:
        warnings.append("ot_unavailable:ir108_non_configurato")
        return None
    if replay:
        rd, meta = fetch.fetch_product_at(config, product,
                                          int(ref_rd.time_ms))
        if rd is None:
            warnings.append(
                "phase2 ot ir108: assente allo slot {} ({})".format(
                    fetch._iso(int(ref_rd.time_ms)), meta.get("error")))
            return None
        if tuple(rd.data.shape) != tuple(ref_rd.data.shape):
            warnings.append("phase2 ot ir108: shape mismatch")
            return None
        if _ir108_grid_mismatch(ref_rd, rd):
            warnings.append("phase2 ot ir108: grid mismatch")
            return None
        return rd
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
                                ot_details, bundle, footprint=None,
                                observed_motion=None, secfg=None,
                                ocfg=None, cappi_meta=None,
                                max_observed_speed_kmh=None,
                                env_null_reason=None, ot_null_reason=None):
    """S1 + S1b + S2 + S2b + S3 del candidato (1.2.4.17 + 1.2.4.18).

    Ogni componente mancante esce con `null_reason`: nessun valore inventato.
    S3 riusa satellite_ot (details gia' calcolati sulla stessa finestra IR).
    S2b (overhang) e S2b/MESH usano `footprint` (maschera della CELLA
    identificata dal cell_id) quando disponibile: nessun matching geometrico
    fra blob. S1b (Bunkers) usa il profilo vento dell'ambiente e il moto
    OSSERVATO dello stesso candidato (velocity_kmh/direction_toward_deg)."""
    from radar_engine import severe_stack as ss

    secfg = secfg or {}
    ocfg = ocfg or {}
    env_layer = ss.environment_layer(env_detail, null_reason=env_null_reason)

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

    # quote termiche: 0 C dal freezing level Open-Meteo, -20 C preferendo
    # l'INTERPOLAZIONE sul profilo T (1.2.4.18), poi l'estrapolazione sul lapse
    h0 = (env_detail or {}).get("freezing_level_m")
    h0_source = ("open-meteo freezing_level_height (ASL)"
                 if h0 is not None else None)
    h20 = (env_detail or {}).get("h20_from_profile_m")
    h20_source = None
    if h20 is not None:
        h20_source = "interpolazione -20 C sul profilo T Open-Meteo"
    else:
        lr = (env_detail or {}).get("lr_700_500")
        h20 = ss.minus20_from_lapse(h0, lr)
        if h20 is not None:
            h20_source = "estrapolazione lineare dal lapse rate 700-500 hPa"

    hail = ss.hail_layer(
        levels, vil, etm, h0_m=h0, h20_m=h20, mask=footprint,
        mesh_table_available=bool(secfg.get("mesh_table_verified", True)),
        mesh_dh_m=float(secfg.get("mesh_dh_m", 1000.0)),
        h0_source=h0_source, h20_source=h20_source,
        slot_iso=(cappi_meta or {}).get("kept_slot_iso"),
        slot_offset_s=(cappi_meta or {}).get("slot_offset_s"))

    # --- S2b: overhang fra i due livelli dichiarati (default 2 e 6 km) ------
    overhang = None
    if secfg.get("overhang_enabled", True):
        low_km = float(ocfg.get("low_km", 2.0))
        high_km = float(ocfg.get("high_km", 6.0))
        low_rd = (cappi or {}).get(low_km)
        high_rd = (cappi or {}).get(high_km)
        lo = _crop(window, low_rd.data) if (low_rd is not None
                                           and window is not None) else None
        hi = _crop(window, high_rd.data) if (high_rd is not None
                                             and window is not None) else None
        overhang = ss.overhang_layer(
            lo, hi, mask=footprint, low_km=low_km, high_km=high_km,
            core_dbz=float(ocfg.get("core_dbz", ss.OVERHANG_CORE_DBZ)),
            strong_dbz=float(ocfg.get("strong_core_dbz", ss.OVERHANG_STRONG_DBZ)),
            slot_iso=(cappi_meta or {}).get("kept_slot_iso"),
            slot_offset_s=(cappi_meta or {}).get("slot_offset_s"))

    # --- S1b: Bunkers RM/LM (descrittore) + scostamento dal moto osservato --
    bunkers = ss.bunkers_layer(
        env_detail=env_detail, observed_motion=observed_motion,
        top_m=float(secfg.get("bunkers_top_m", ss.BUNKERS_TOP_M)),
        deviation=float(secfg.get("bunkers_deviation_ms",
                                  ss.BUNKERS_DEV_MS)),
        max_observed_speed_kmh=max_observed_speed_kmh)

    ot = ss.ot_layer_from_details(
        ot_details, timestamp_iso=getattr(bundle, "radar_timestamp_iso", None),
        null_reason=ot_null_reason)
    return {"environment": env_layer, "hail": hail, "ot": ot,
            "overhang": overhang, "bunkers": bunkers,
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

    # --- 1.2.4.20: CLOCK INIETTATO (replay --as-of) -------------------------
    # Unica sorgente di tempo anche qui: in replay l'istante e' fisso, e le
    # sorgenti che non hanno archivio storico vengono DICHIARATE assenti
    # invece di essere lette da oggi.
    p2_clock = clock_mod.from_config(config)
    replay = p2_clock.replay
    as_of_ms = p2_clock.now_ms() if replay else None
    as_of_mode = getattr(bundle, "as_of_mode", None)

    def _degrade(field, reason, detail=None, count=None):
        """Registra una degradazione dovuta alla modalita' replay (una voce
        per campo+motivo, con il numero di candidati/valori toccati)."""
        if not replay or as_of_mode is None:
            return
        for item in as_of_mode["degraded"]:
            if item.get("field") == field and item.get("null_reason") == reason:
                if count is not None:
                    item["count"] = max(item.get("count") or 0, int(count))
                if detail:
                    item["detail"] = detail
                return
        entry = {"field": field, "null_reason": reason}
        if count is not None:
            entry["count"] = int(count)
        if detail:
            entry["detail"] = detail
        as_of_mode["degraded"].append(entry)

    # --- CONFIG (raggi finestra per-candidato + finestra fulmini) ----------
    hcfg = p2_cfg.get("hook") or {}
    scfg = p2_cfg.get("structure") or {}
    ecfg = p2_cfg.get("environment") or {}
    lcfg = p2_cfg.get("lightning") or {}
    otcfg = p2_cfg.get("ot") or {}
    # 1.2.4.18: blocchi dello stack severo (overhang da struttura, Bunkers e
    # tabella di Witt da severe_stack).
    stack_cfg = p2_cfg.get("severe_stack") or {}
    ocfg = scfg.get("overhang") or {}
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
    cappi_summary = {}
    cappi_by_name = {}
    if frames:
        # 1.2.4.18: prima i CAPPI, ALLINEATI al timestamp della VMI (10 livelli
        # per MESH + overhang). Un livello mancante -> quel campo degrada a
        # null con motivo, il run prosegue.
        cappi, cappi_by_name, cappi_summary = _fetch_cappi_levels(
            config, scfg, frames[-1], warnings)
        low_high = {"low": cappi_by_name.get(str(scfg.get("product_low"))),
                    "high": cappi_by_name.get(str(scfg.get("product_high")))}
        products = _fetch_structure_products(config, scfg,
                                             frames[-1].data.shape, warnings,
                                             cappi_rd=low_high,
                                             ref_time_ms=frames[-1].time_ms,
                                             replay=replay)
        if not any(rd is not None for rd in products.values()):
            warnings.append("phase2 structure: no products available")
        # OVERSHOOTING TOP: frame IR_108 (stessa griglia TM della VMI).
        ir_rd = _fetch_ir108(config, otcfg, frames[-1], warnings, replay=replay)
        if replay:
            if ir_rd is None:
                _degrade("ot", "ir108_history_unavailable")
            for _name in ("vil", "etm", "poh"):
                if products.get(_name) is None:
                    _degrade(_name, "product_not_archived_for_as_of")
    else:
        warnings.append("phase2 structure: no frames")
        ir_rd = None
    result["cappi_fetch"] = cappi_summary

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
    if replay:
        # 1.2.4.20: NON esiste un archivio pubblico degli slot LTG storici
        # (misurato: 403 su ogni slot storico, retro/REPORT.md §2.4). Il layer
        # si dichiara assente con motivo: NON si leggono i fulmini di oggi.
        _degrade("lightning", "lightning_history_unavailable")
        warnings.append("phase2 lightning: modalita' --as-of senza archivio "
                        "storico (lightning_history_unavailable)")
    else:
        failures_after_grace = 0  # errori di fila DOPO il grace (criterio stop)
        for k in range(0, max_backoff + 1):   # dal piu' recente all'indietro
            if len(ltg_slots_recent) >= window:
                break
            epoch = lightning.ltg_epoch_floor(backoff_steps=k,
                                              now_ms=p2_clock.now_ms())
            if epoch in slot_cache:
                slot = slot_cache[epoch]
            else:
                try:
                    slot = lightning.fetch_ltg(epoch_ms=epoch)["strikes"]
                except lightning.LightningFetchError as exc:
                    warnings.append(f"phase2 lightning slot-{k}: {exc}")
                    slot = None
                except Exception as exc:  # guasto imprevisto -> slot assente
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
    if not ltg_slots and not replay:
        warnings.append("phase2 lightning: no slots available (wider window)")

    # --- LAYER PER CANDIDATO: hook/struttura/fulmini/ambiente ---------------
    # Cache condivise: env per bucket (0.1 gradi), fulmini per slot epoch ->
    # N candidati = UNA chiamata per bucket/slot. Ogni sub-valore finisce
    # sul SINGOLO candidato (chiavi lette dai chip dell'app).
    env_cache = {}
    env_errors = {}          # 1.2.4.20: motivo reale del fallimento per bucket
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
                    timeout_s=ecfg.get("timeout_s"), as_of_ms=as_of_ms,
                    errors=env_errors)
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
        if replay:
            # Visibilita' PER CAMPO del motivo (replay): il dato non esiste in
            # archivio, non e' "assente nel payload di oggi".
            c["as_of_null_reasons"] = {
                "environment": (None if env_detail is not None
                                else "environment_unavailable_for_as_of"),
                "lightning": (None if lightning_score is not None
                              else "lightning_history_unavailable"),
                "ot": (None if ot_score is not None
                       else "ir108_history_unavailable"),
            }

        # --- STACK SEVERO (1.2.4.17 + 1.2.4.18): S1/S1b/S2/S2b/S3 -----------
        footprint = None
        fp_source = "candidate_window"
        fp_pixels = None
        has_ident = (c.get("cell_id") is not None
                     or c.get("storm_object_id") is not None)
        if window is not None and has_ident:
            try:
                fp_win, fp_mask, fp_src = _cell_footprint(
                    bundle, c, config, target_window=window)
            except Exception as exc:
                _warn(f"phase2 footprint failed: {exc}")
                fp_win, fp_mask, fp_src = None, None, "footprint_error"
            if fp_mask is not None:
                footprint = fp_mask
                fp_source = fp_src
                fp_pixels = int(fp_mask.sum())
            else:
                fp_source = "candidate_window ({})".format(fp_src)
        try:
            parts = _severe_stack_for_candidate(
                window, products, cappi, env_detail, ot_details, bundle,
                footprint=footprint,
                observed_motion={"velocity_kmh": (c.get("motion") or {}).get(
                                     "velocity_kmh"),
                                 "direction_toward_deg": (
                                     c.get("motion") or {}).get(
                                         "direction_toward_deg")},
                secfg=stack_cfg, ocfg=ocfg, cappi_meta=cappi_summary,
                max_observed_speed_kmh=((config.get("storm") or {}).get(
                    "motion") or {}).get("max_validated_velocity_kmh"),
                env_null_reason=("environment_unavailable_for_as_of"
                                 if (replay and env_detail is None) else None),
                ot_null_reason=("ir108_history_unavailable"
                                if (replay and ot_details is None
                                    and ir_rd is None) else None))
            from radar_engine import severe_stack as _severe
            c["severe_stack"] = _severe.combine(
                structure=_structure_summary(c), hail=parts["hail"],
                environment=parts["environment"], ot=parts["ot"],
                overhang=parts["overhang"], bunkers=parts["bunkers"])
            c["hail"] = parts["hail"]
            c["overhang"] = parts["overhang"]
            c["bunkers"] = parts["bunkers"]
            c["footprint"] = {"source": fp_source, "pixels": fp_pixels}
        except Exception as exc:
            _warn(f"phase2 severe_stack failed: {exc}")
            c["severe_stack"] = None
            c["hail"] = None
            c["overhang"] = None
            c["bunkers"] = None
            c["footprint"] = {"source": fp_source, "pixels": fp_pixels}

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

    # --- 1.2.4.20: RIEPILOGO REPLAY (as_of_mode) ----------------------------
    if replay and as_of_mode is not None:
        # Ambiente: sorgente DICHIARATA, con l'esito reale della richiesta.
        if env_failures:
            reasons = sorted(set(env_errors.values()))
            _degrade("environment", "environment_unavailable_for_as_of",
                     detail=("; ".join(reasons))[:500],
                     count=env_failures)
            as_of_mode["environment_source"] = (
                "archive-api.open-meteo.com (non utilizzabile: "
                + ", ".join(sorted({r.split(":", 1)[-1] for r in reasons}))
                + ")")
        elif env_cache:
            as_of_mode["environment_source"] = (
                "archive-api.open-meteo.com (ERA5)")
        # MESH: conseguenza a valle dell'ambiente assente (quote termiche).
        mesh_null = 0
        for _c in candidates:
            _mesh = (((_c.get("hail") or {}).get("mesh_mm")) or {})
            if (_mesh.get("value") is None
                    and _mesh.get("null_reason") == "thermal_levels_missing"):
                mesh_null += 1
        if mesh_null:
            _degrade("hail.mesh_mm", "thermal_levels_missing", count=mesh_null,
                     detail=("conseguenza di environment_unavailable_for_as_of: "
                             "h0 e H(-20 C) vengono dal profilo termico NWP"))
        result["as_of_mode"] = as_of_mode


def _run(config, out_dir, product, max_frames, dry_run, history_dir=None,
         clock=None):
    clock = clock if isinstance(clock, clock_mod.Clock) else \
        clock_mod.from_config(config)
    clock_mod.inject(config, clock)
    period_s = None
    try:
        period_s = int((config.get("source") or {}).get("period_s") or 300)
    except (TypeError, ValueError):
        period_s = 300
    as_of_mode = clock.describe(period_s)
    bundle = models.EngineBundle("ok", clock.now_iso(), output.SOURCE_LABEL)
    bundle.engine = _engine_meta()
    bundle.as_of_mode = as_of_mode

    # ---- FECTH + VALIDAZIONE ------------------------------------------------
    try:
        frames, warnings, latest_ts = fetch.fetch_frames(
            config, product=product, max_frames=max_frames,
            anchor_ms=clock.anchor_ms(period_s),
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
            (clock.now_ms() - frames[-1].time_ms) / 60000.0, 1
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
    # 1.2.4.20: in REPLAY non si scrive stato (il registro predizioni appende
    # righe con l'orologio e ruota a 30 giorni; l'archivio rolling dipende
    # dall'indice su disco). Un replay non deve avere effetti collaterali.
    if clock.replay:
        if history_dir:
            bundle.warnings.append(
                "history archive skipped in --as-of mode (nessuno stato scritto)")
        bundle.severe_predictions = {"status": "skipped",
                                     "reason": "as_of_replay"}
    else:
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
    # 1.2.4.20 — REPLAY DETERMINISTICO: ancora TUTTE le letture dell'orologio
    # (slot radar, VIL/ETM/POH, IR_108, ambiente, fulmini) a un istante UTC.
    # Default None = comportamento operativo (ora corrente).
    ap.add_argument("--as-of", dest="as_of", default=None, metavar="ISO8601",
                    help="istante UTC di replay (es. 2026-07-01T13:20:00Z); "
                         "default: ora corrente")
    # 1.2.4.21 — FOOTPRINT GEOMETRICO su richiesta: GeoJSON con la componente
    # connessa poligonizzata di OGNI cella e di OGNI struttura. Senza questo
    # flag l'output operativo e' byte-identico alla 1.2.4.20 (nessun file in
    # piu', nessuna soglia toccata).
    ap.add_argument("--footprints", default=None, metavar="PATH",
                    help="scrive il footprint GeoJSON (celle + strutture) in "
                         "PATH; default: non scritto")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    if args.keep_frames:
        config["source"]["keep_raw_frames"] = True
    try:
        clock = (clock_mod.Clock.at_iso(args.as_of) if args.as_of
                 else clock_mod.Clock.live())
    except ValueError as exc:
        ap.error(f"--as-of non interpretabile: {exc}")
    clock_mod.inject(config, clock)
    out_dir = os.path.abspath(args.out_dir or config["output"]["out_dir"])

    bundle, rc = _run(config, out_dir, args.product, args.max_frames,
                      args.dry_run, history_dir=args.history_dir, clock=clock)

    fp_summary = None
    if args.footprints and not args.dry_run and getattr(bundle, "frames", None):
        try:
            fp_summary = footprint_mod.write_geojson(args.footprints, bundle,
                                                     config)
        except Exception as exc:  # accessorio: non cambia l'esito del run
            fp_summary = {"status": "error",
                          "error": f"{exc.__class__.__name__}: {exc}"}

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
    as_of_mode = getattr(bundle, "as_of_mode", None)
    if as_of_mode is not None:
        summary["as_of_mode"] = as_of_mode
    if fp_summary is not None:
        summary["footprints"] = fp_summary
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