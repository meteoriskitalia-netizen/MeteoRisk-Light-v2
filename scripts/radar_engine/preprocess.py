#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — preprocess.py

Caricamento e validazione dei GeoTIFF VMI Float32: separa esplicitamente
  - DATA VALIDITY  (validità numerica: NaN/inf, nodata dichiarato, valori -9999/-9998)
  - METEOROLOGICAL THRESHOLD (soglia dBZ, v. detect.py)

CRS/GEOREFERENZIAZIONE (FAIL SAFE): il CRS è LETTO dal file (mai assunto). Se il
CRS non è interpretabile -> CrsError e nessuna coordinata pubblicata. La
conversione pixel->EPSG:4326 passa per pyproj sulla CRS del file. Un controllo
di plausibilità geografica (bbox Italia) blocca coordinate assurde.
"""

import datetime as _dt

import numpy as np

from . import models

# ---------------------------------------------------------------------------
# 2026-11-12 — SENTINELLE DPC PER PRODOTTO (MISURATE su file reali, slot
# 2026-11-12T15:15Z: VMI/VIL/ETM/POH/SRI).
#
#   VMI, ETM, CAPPI_*  ->  -9999 = FUORI copertura, -9998 = IN copertura
#                          SENZA eco. Sono due cose diverse: -9998 non e' un
#                          valore e non entra nelle medie, ma il pixel e'
#                          dentro il dominio del prodotto.
#   VIL, POH, SRI      ->  -9999 = FUORI copertura; 0.0 = ASSENZA VALIDA
#                          (niente pioggia / niente grandine). NON esiste
#                          -9998 in questi prodotti (misurato: 0 pixel).
#
# Un filtro unico `> -9997` confonde le due famiglie: per VIL/POH/SRI
# tratterebbe 0.0 come "eco" (centinaia di migliaia di pixel) e per VMI
# butterebbe via il "nessun eco" (-9998) dal dominio. Le maschere qui sotto
# sono DERIVATE DAL PRODOTTO, mai da una costante unica.
# ---------------------------------------------------------------------------
DPC_OUT_OF_COVERAGE = -9999.0
DPC_NO_ECHO = -9998.0
ECHO_SENTINEL_PRODUCTS = frozenset({"VMI", "ETM"})
ZERO_ABSENCE_PRODUCTS = frozenset({"VIL", "POH", "SRI"})


def product_family(product_type):
    """Famiglia di sentinelle di un prodotto DPC.

    "echo": VMI, ETM, CAPPI_* (doppia sentinella -9999/-9998).
    "zero_absence": VIL, POH, SRI (solo -9999; 0.0 e' un valore valido).
    "unknown": prodotto non censito -> -9998 trattato come "nessun eco"
    (escluso dai VALORI, incluso nel DOMINIO) e 0.0 lasciato valore valido:
    nessun prodotto noto usa 0 come sentinella."""
    name = str(product_type or "").strip().upper()
    if name in ZERO_ABSENCE_PRODUCTS:
        return "zero_absence"
    if name in ECHO_SENTINEL_PRODUCTS or name.startswith("CAPPI"):
        return "echo"
    return "unknown"


def product_masks(data, product_type):
    """Maschere (valid, outside, no_echo) DERIVATE DAL PRODOTTO.

    valid   : pixel con un valore utilizzabile. Per VIL/POH/SRI include 0.0
              (assenza valida); per VMI/ETM/CAPPI esclude -9998 (nessun eco).
    outside : fuori copertura (-9999 o non finito).
    no_echo : -9998 (solo famiglia "echo"; per VIL/POH/SRI e' sempre vuota).

    `domain` = valid | no_echo = "dentro la copertura" (indipendente dal
    valore): e' la maschera usata per l'allineamento fra prodotti."""
    arr = np.asarray(data, dtype="float64")
    family = product_family(product_type)
    finite = np.isfinite(arr)
    outside = (~finite) | (np.abs(arr - DPC_OUT_OF_COVERAGE) <= 1e-6)
    if family == "zero_absence":
        no_echo = np.zeros(arr.shape, dtype=bool)
        valid = finite & (~outside)
    else:
        no_echo = finite & (np.abs(arr - DPC_NO_ECHO) <= 1e-6)
        valid = finite & (~outside) & (~no_echo)
    return {"valid": valid, "outside": outside, "no_echo": no_echo,
            "domain": valid | no_echo, "family": family}


def product_observed_mask(data, product_type):
    """Maschera del SEGNALE (non solo del dato valido).

    Per VIL/POH/SRI "osservato" = valore > 0 (0.0 e' assenza, non segnale).
    Per VMI/ETM/CAPPI "osservato" = valore valido (un CAPPI puo' essere
    negativo: -29 dBZ misurati; la sentinella e' -9998, non il segno)."""
    masks = product_masks(data, product_type)
    arr = np.asarray(data, dtype="float64")
    if masks["family"] == "zero_absence":
        return masks["valid"] & (arr > 0.0)
    return masks["valid"]


def qc_product(data, product_type, reference_domain=None):
    """QC di UN prodotto su una griglia intera (un frame).

    Ritorna conteggi verificabili: pixel validi, fuori dominio, nessun eco,
    zeri validi, positivi, minimo/massimo sui soli valori validi. Se
    `reference_domain` e' fornito (maschera di dominio della VMI dello stesso
    frame) aggiunge i pixel di disallineamento rispetto al riferimento."""
    arr = np.asarray(data, dtype="float64")
    masks = product_masks(arr, product_type)
    valid = masks["valid"]
    out = {
        "product_type": str(product_type),
        "family": masks["family"],
        "pixels_total": int(arr.size),
        "finite_pixels": int(np.isfinite(arr).sum()),
        "valid_pixels": int(valid.sum()),
        "outside_domain_pixels": int(masks["outside"].sum()),
        "no_echo_pixels": int(masks["no_echo"].sum()),
        "domain_pixels": int(masks["domain"].sum()),
        "zero_pixels": int((valid & (arr == 0.0)).sum()),
        "positive_pixels": int((valid & (arr > 0.0)).sum()),
        "min": (float(arr[valid].min()) if bool(valid.any()) else None),
        "max": (float(arr[valid].max()) if bool(valid.any()) else None),
    }
    if reference_domain is not None:
        ref = np.asarray(reference_domain, dtype=bool)
        if ref.shape == arr.shape:
            out["domain_mismatch_vs_reference_pixels"] = int(
                (masks["domain"] ^ ref).sum())
            out["outside_in_reference_pixels"] = int(
                ((~masks["domain"]) & ref).sum())
            out["outside_in_product_pixels"] = int(
                (masks["domain"] & (~ref)).sum())
    return out


def align_domain_masks(domains_by_product, reference_domain=None,
                       reference_name="VMI"):
    """Allineamento delle maschere di dominio fra i prodotti di UN frame.

    `domains_by_product`: {nome: maschera bool "dentro copertura"}.
    Ritorna intersezione/unione/scarto e, per prodotto, i pixel di
    disallineamento rispetto alla VMI. Nessun resampling: le griglie devono
    avere la stessa forma (altrimenti il prodotto e' escluso e dichiarato)."""
    names = []
    inter = union = None
    for name, mask in (domains_by_product or {}).items():
        if mask is None:
            continue
        m = np.asarray(mask, dtype=bool)
        if inter is None:
            inter = m.copy()
            union = m.copy()
        elif m.shape != inter.shape:
            continue
        else:
            inter &= m
            union |= m
        names.append(name)
    out = {"products": names, "reference": reference_name,
           "intersection_pixels": (int(inter.sum()) if inter is not None
                                   else None),
           "union_pixels": (int(union.sum()) if union is not None else None),
           "mismatch_pixels": (int((union & (~inter)).sum())
                               if inter is not None else None),
           "per_product": {}}
    if reference_domain is not None and inter is not None:
        ref = np.asarray(reference_domain, dtype=bool)
        if ref.shape == inter.shape:
            for name in names:
                m = np.asarray(domains_by_product[name], dtype=bool)
                out["per_product"][name] = {
                    "mismatch_vs_reference_pixels": int((m ^ ref).sum()),
                    "outside_in_reference_pixels": int(((~m) & ref).sum()),
                    "outside_in_product_pixels": int((m & (~ref)).sum()),
                }
    return out


def qc_bundle(entries, product_types, period_s=300, stale_after_s=300):
    """Aggrega il QC per prodotto del bundle DPC (un solo punto).

    entries: {slot_ms: {nome: {"qc": dict|None, "valid_time_ms": int|None,
                               "skew_s": int|None, "stale_for_frame": bool,
                               "error": str|None}}}
    product_types: {nome: product_type} (i prodotti RICHIESTI).
    Ritorna: per prodotto frames_expected/present, totali pixel validi/fuori
    dominio/zeri, slot mancanti; elenco globale dei mancanti e degli stale."""
    slots = sorted(int(s) for s in (entries or {}))
    out = {"period_s": int(period_s), "stale_after_s": int(stale_after_s),
           "slots_total": len(slots), "products": {},
           "missing": {}, "stale": [], "not_configured": []}
    for name, ptype in (product_types or {}).items():
        if not ptype:
            out["not_configured"].append(name)
            continue
        present = 0
        valid = outside = zeros = no_echo = 0
        frames = []
        for slot in slots:
            e = ((entries.get(slot) or {}).get(name))
            qc = (e or {}).get("qc")
            if qc is None:
                continue
            present += 1
            valid += int(qc.get("valid_pixels") or 0)
            outside += int(qc.get("outside_domain_pixels") or 0)
            zeros += int(qc.get("zero_pixels") or 0)
            no_echo += int(qc.get("no_echo_pixels") or 0)
            frames.append({
                "slot_iso": _iso(slot),
                "valid_time_iso": (e or {}).get("valid_time_iso"),
                "skew_s": (e or {}).get("skew_s"),
                "stale_for_frame": bool((e or {}).get("stale_for_frame")),
                "valid_pixels": int(qc.get("valid_pixels") or 0),
                "outside_domain_pixels": int(
                    qc.get("outside_domain_pixels") or 0),
                "zero_pixels": int(qc.get("zero_pixels") or 0),
                "no_echo_pixels": int(qc.get("no_echo_pixels") or 0),
            })
        out["products"][name] = {
            "product_type": ptype,
            "frames_expected": len(slots),
            "frames_present": present,
            "frames_stale": sum(1 for f in frames if f["stale_for_frame"]),
            "valid_pixels_total": valid,
            "outside_domain_pixels_total": outside,
            "zero_pixels_total": zeros,
            "no_echo_pixels_total": no_echo,
            "frames": frames,
        }
    for slot in slots:
        miss = []
        for name, ptype in (product_types or {}).items():
            if not ptype:
                continue
            e = ((entries.get(slot) or {}).get(name))
            if e is None or e.get("qc") is None:
                miss.append(name)
            elif e.get("stale_for_frame"):
                out["stale"].append({
                    "slot_iso": _iso(slot), "product": name,
                    "skew_s": e.get("skew_s"),
                    "valid_time_iso": e.get("valid_time_iso")})
        if miss:
            out["missing"][_iso(slot)] = miss
    return out


def rate_stats_in_mask(data, product_type, mask, threshold=None):
    """Statistiche di un prodotto DENTRO una maschera bool (cella/struttura).

    Sentinelle DERIVATE DAL PRODOTTO (v. product_masks): per VIL/POH/SRI lo
    0.0 e' un valore valido e entra nella media (assenza misurata); per
    VMI/ETM/CAPPI -9998 e' "nessun eco" ed e' escluso. I pixel fuori
    copertura (-9999) non entrano MAI come zero.

    Ritorna: pixels, outside_pixels, no_echo_pixels, zero_pixels, max, mean e
    (se threshold) pixels_gt_threshold. max/mean None se nessun pixel valido:
    il chiamante pubblica null + motivo, mai 0."""
    arr = np.asarray(data, dtype="float64")
    m = np.asarray(mask, dtype=bool)
    if arr.shape != m.shape:
        return {"error": "shape_mismatch", "pixels": 0,
                "outside_pixels": None, "no_echo_pixels": None,
                "zero_pixels": None, "max": None, "mean": None,
                "pixels_gt_threshold": None}
    masks = product_masks(arr, product_type)
    sel = m & masks["valid"]
    n = int(sel.sum())
    out = {
        "pixels": n,
        "outside_pixels": int((m & masks["outside"]).sum()),
        "no_echo_pixels": int((m & masks["no_echo"]).sum()),
        "zero_pixels": int((sel & (arr == 0.0)).sum()),
        "max": None,
        "mean": None,
        "pixels_gt_threshold": None,
    }
    if n:
        vals = arr[sel]
        out["max"] = float(vals.max())
        out["mean"] = float(vals.mean())
    if threshold is not None:
        out["pixels_gt_threshold"] = int(
            (sel & (arr > float(threshold))).sum())
    return out


def accumulate_rate(rate_frames, product_type, mask, step_s):
    """Accumulo STIMATO da rate istantaneo su piu' frame.

    rate_frames: lista di (slot_ms, griglia) gia' ritagliate sulla STESSA
    finestra della maschera e gia' filtrate (niente frame mancanti/stale).
    Per ogni frame: media del rate (mm/h per SRI) sui pixel dentro `mask` e
    validi per il prodotto, convertita in mm con `step_s / 3600` (SRI e'
    mm/ORA: moltiplicare per 300 s darebbe mm/h*s, non mm). La somma e' una
    STIMA da rate istantaneo (non un accumulo misurato): il chiamante lo
    dichiara.

    Un frame senza pixel validi nella maschera NON contribuisce (nessuno zero
    inventato) e finisce in `frames_skipped`. Ritorna accum_mm None se nessun
    frame contribuisce, con `null_reason`."""
    step = float(step_s)
    total = 0.0
    used = []
    skipped = []
    per_frame = []
    for slot, grid in (rate_frames or []):
        stats = rate_stats_in_mask(grid, product_type, mask)
        if stats.get("error") or stats.get("pixels") in (None, 0):
            skipped.append({"slot_ms": int(slot),
                            "reason": stats.get("error")
                            or "no_valid_pixels_in_mask"})
            continue
        contrib = float(stats["mean"]) * step / 3600.0
        total += contrib
        used.append(int(slot))
        per_frame.append({"slot_ms": int(slot), "mean_mmh": stats["mean"],
                          "pixels": stats["pixels"],
                          "contrib_mm": contrib})
    if not used:
        return {"accum_mm": None, "duration_s": 0.0, "frames_used": 0,
                "frames_skipped": skipped, "per_frame": [],
                "null_reason": "no_rate_frame_usable"}
    return {"accum_mm": float(total), "duration_s": float(len(used)) * step,
            "frames_used": len(used), "frames_skipped": skipped,
            "per_frame": per_frame, "null_reason": None}


def _iso(ms):
    return _dt.datetime.fromtimestamp(ms / 1000.0,
                                      _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def read_raster(path, nodata_values, declared_nodata_from_tiff=True,
                geo_plausible_bbox=None):
    """Legge e valida un GeoTIFF VMI su griglia custom TM (Italia).

    Ritorna RasterData. Solleva ValidationError (file corrotto/formato non
    atteso) o CrsError (georeferenziazione non interpretabile -> FAIL SAFE)."""
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover
        raise models.ValidationError("rasterio_missing") from exc

    try:
        with rasterio.open(path) as ds:
            arr = ds.read(1).astype("float64")
            rows, cols = arr.shape
            crs_obj = ds.crs
            transform = ds.transform
            declared = ds.nodata
    except models.ValidationError:
        raise
    except Exception as exc:
        raise models.ValidationError(f"raster_corrupted:{exc}") from exc

    # --- CRS: letto dal file, MAI assunto. Fail-safe se non interpretabile. ---
    if crs_obj is None or not (crs_obj.to_wkt() or "").strip():
        raise models.CrsError("crs_missing_in_tiff")
    crs_wkt = crs_obj.to_wkt()
    try:
        from pyproj import CRS as PjCRS, Transformer
        crs = PjCRS.from_wkt(crs_wkt)
        geo_transform = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    except Exception as exc:
        raise models.CrsError(f"crs_uninterpretable:{exc}") from exc

    # --- nodata set: dichiarati + lista config + NaN/inf ---
    nodata_set = set(float(v) for v in (nodata_values or []))
    if declared_nodata_from_tiff and declared is not None:
        nodata_set.add(float(declared))

    finite = np.isfinite(arr)
    if nodata_set:
        is_nodata = np.zeros(arr.shape, dtype=bool)
        for v in nodata_set:
            is_nodata |= np.abs(arr - v) <= 1e-6
    else:
        is_nodata = np.zeros(arr.shape, dtype=bool)

    valid_mask = finite & (~is_nodata)
    nodata_mask = ~valid_mask

    pixel_area_km2 = (abs(transform.a) * abs(transform.e)) / 1e6
    if not (pixel_area_km2 > 0):
        raise models.ValidationError("degenerate_transform")

    # Plausibilità geografica: il centroide del raster deve cadere in bbox Italia.
    if geo_plausible_bbox is not None:
        center_lon, center_lat = pixel_to_lonlat(
            crs, transform, rows / 2.0, cols / 2.0, geo_transform
        )
        lon_r = geo_plausible_bbox["lon"]
        lat_r = geo_plausible_bbox["lat"]
        if not (lon_r[0] <= center_lon <= lon_r[1]
                and lat_r[0] <= center_lat <= lat_r[1]):
            raise models.CrsError(
                f"geographic_plausibility_failed:center={center_lon:.3f},{center_lat:.3f}"
            )

    return models.RasterData(
        data=arr, valid_mask=valid_mask, nodata_mask=nodata_mask,
        rows=rows, cols=cols, crs=crs, crs_wkt=crs_wkt,
        transform=transform, pixel_area_km2=pixel_area_km2,
        time_ms=None, time_iso=None, source_path=str(path),
        declared_nodata=(float(declared) if declared is not None else None),
        geo_transform=geo_transform,
    )


def build_pixel_to_lonlat(crs, transform):
    """Costruttore alternativo di Transformer CRS->EPSG:4326 (riuso nei test)."""
    from pyproj import Transformer
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


def pixel_to_lonlat(crs, transform, row, col, geo_transform=None):
    """Converte (row, col) -> (lon, lat). centro del pixel."""
    if geo_transform is None:
        geo_transform = build_pixel_to_lonlat(crs, transform)
    x, y = transform @ (float(col) + 0.5, float(row) + 0.5)
    lon, lat = geo_transform.transform(x, y)
    return float(lon), float(lat)


def strip_nodata_decimals(values, nodata_values=None):
    """Rimuove i valori di nodata residui da un array (uso nei test/metriche)."""
    out = values[np.isfinite(values)]
    for v in (nodata_values or []):
        out = out[np.abs(out - float(v)) > 1e-6]
    return out