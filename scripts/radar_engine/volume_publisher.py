#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publisher del volume 3D CAPPI -> blob ``data/radar/3d/volume.json``.

NOTA ARCHITETTURA 3D CACHE (2026-11-17): il volume NON e' pubblicato su
``main``: vive SOLO sul branch orphan ``radar-3d``, in UN solo blob
compatto. Il file locale ``data/radar/3d/volume.json`` e' ripristinato dal
workflow PRIMA del motore e riletto come "volume precedente" per il growth.

Produce un payload COMPATTO e autosufficiente: ogni campo porta `source`,
`timestamp`, `quality` e `null_reason`. Nessun valore e' inventato: i voxel
sono quelli dei CAPPI DPC (con la sola interpolazione verticale VI dichiarata)
e le sezioni verticali sono campionamenti nearest-neighbor degli stessi voxel.

DICHIARAZIONE OBBLIGATORIA (fase 5 della roadmap 1.3):
    volume_source      = "DPC_CAPPI_stack"
    volume_acquisition = "not_native_volumetric"

I CAPPI DPC sono prodotti 2D (mosaico nazionale a singola riflettivita'):
impilarli a 10 quote NON e' un'acquisizione volumetrica polare nativa, non
contiene velocita' radiale e non consente la misura della rotazione.

Questo modulo NON importa config.py e NON modifica main.py: la soglia e'
un argomento esplicito (`thresholds`), il wiring resta al chiamante.

Riferimenti:
  - Zhang et al. 2005 (VI scheme, interpolazione verticale lineare)
  - Greene & Clark 1972 (VIL)
  - Lakshmanan et al. 2013 (echo top)
  - Witt et al. 1998 / Amburn & Wolf 1997 (solo come riferimento di confronto)
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone

import numpy as np
from scipy import ndimage

from . import cross_section as _cs

SCHEMA = "meteorisk.volume/1"
DEFAULT_THRESHOLDS = (30, 40, 45, 50, 55)
ELEVATIONS_KM = [float(i + 1) for i in range(10)]
VOLUME_SOURCE = "DPC_CAPPI_stack"
VOLUME_ACQUISITION = "not_native_volumetric"

# Cache 3D a dimensione costante: UN blob, un branch orphan, nessun dato grezzo.
VOLUME_BRANCH = "radar-3d"
VOLUME_BLOB_PATH = "data/radar/3d/volume.json"
SRC_GROWTH_PAYLOAD = "confronto con il payload compatto precedente (branch radar-3d)"
REF_GROWTH_PAYLOAD = (
    "delta fra i VALORI PUBBLICATI (ECHO_TOP, CELL_VOLUME) di due payload "
    "consecutivi; richiede il volume precedente. Nessun array grezzo viene "
    "conservato: il blob vivo e' uno solo e resta a dimensione costante.")

SRC_VOL = "DPC CAPPI_1..CAPPI_10 (stack 10 livelli, 1 km/passaggio)"
SRC_SECTION = "cross_section.py: nearest-neighbor su volume CAPPI DPC"
REF_VIL = "Greene & Clark 1972: VIL = 3.44e-6*sum(Z_lin^(4/7)*dh), dh=1000 m"
REF_ET = "Lakshmanan et al. 2013: echo top = quota max con Z>=soglia"
REF_VI = "Zhang et al. 2005: VI scheme, interpolazione lineare su buchi singoli"
REF_VOL = "conteggio voxel (>=soglia) x 1 km^3, griglia DPC 1 km"
REF_MORPH = "SCELTA INGEGNERISTICA: estensione verticale = quota max - quota min con Z>=soglia"
REF_GROWTH = "delta fra due slot consecutivi (richiede volume precedente)"

QUALITY_LEGEND = {
    "A_observed": "valore letto direttamente da voxel CAPPI originali (nessun voxel VI nel sotto-volume)",
    "B_vi_interpolated": "sotto-volume contiene voxel interpolati VI (conteggio dichiarato in detail)",
    "C_derived": "quantita' calcolata da voxel CAPPI (VIL, densita', volumi, grown)",
    "D_unavailable": "valore null: il motivo e' SEMPRE in null_reason",
    "A_verified_no_invented": "sezione verificata: mismatch=0 e nan_mismatch=0 sui voxel campionati",
    "X_mismatch_detected": "sezione NON coerente con i voxel: conteggi in detail (usare con cautela)",
}


# ---------------------------------------------------------------------------
# helper di campo / arrotondamento
# ---------------------------------------------------------------------------
def _r2(value, digits=2):
    """Arrotonda un valore finito; NaN/inf -> None (JSON valido, nessun NaN)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, digits)


def _field(value, source, timestamp, quality, reference,
           null_reason=None, detail=None):
    """Costruisce un campo con source/timestamp/quality/null_reason SEMPRE presenti."""
    available = value is not None
    out = {
        "value": value,
        "source": source,
        "timestamp": timestamp,
        "quality": quality if available else "D_unavailable",
        "null_reason": None if available else (null_reason or "unknown"),
        "reference": reference,
    }
    if detail is not None:
        out["detail"] = detail
    return out


def _mask_sentinels(arr):
    a = np.asarray(arr, dtype=np.float32)
    return np.where((a == -9999.0) | (a == -9998.0), np.nan, a).astype(np.float32)


# ---------------------------------------------------------------------------
# celle (stessa logica verificata di compute_params.py: 8-connettivita', VMI>=30)
# ---------------------------------------------------------------------------
def _label_cells(vmi, min_area_px=4):
    """Etichetta le celle sulla VMI con 8-connettivita' e soglia 30 dBZ."""
    if vmi is None:
        return np.zeros((0, 0), dtype=np.int32), []
    vmi_v = _mask_sentinels(vmi)
    mask = np.isfinite(vmi_v) & (vmi_v >= 30.0)
    struct = np.ones((3, 3), dtype=int)
    labeled, nlab = ndimage.label(mask, structure=struct)
    comps = []
    for lab_id in range(1, nlab + 1):
        ys, xs = np.where(labeled == lab_id)
        area = int(len(ys))
        if area < min_area_px:
            continue
        w = np.where(np.isfinite(vmi_v[ys, xs]), vmi_v[ys, xs], 0.0)
        w = np.where(w < 0, 0.0, w)
        sw = float(w.sum())
        cr = float(ys.mean())
        cc = float(xs.mean())
        if sw > 0:
            crw = float((ys * w).sum() / sw)
            ccw = float((xs * w).sum() / sw)
        else:
            crw, ccw = cr, cc
        comps.append({
            "label_id": int(lab_id),
            "area_px": area,
            "bbox_rc": [int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())],
            "centroid_rc": [cr, cc],
            "centroid_weighted_rc": [crw, ccw],
            "max_dbz_vmi": _r2(float(np.nanmax(vmi_v[ys, xs])), 1),
        })
    return labeled, comps


def _subvolume(volume, comp, pad=5):
    rmin, rmax, cmin, cmax = comp["bbox_rc"]
    H, W = int(volume.shape[1]), int(volume.shape[2])
    r0 = max(0, rmin - pad)
    r1 = min(H, rmax + pad + 1)
    c0 = max(0, cmin - pad)
    c1 = min(W, cmax + pad + 1)
    return volume[:, r0:r1, c0:c1], (r0, r1, c0, c1)


def _column_echo_top(sv, thr):
    """Quota massima (km) con Z>=thr per colonna (verificata in compute_params.py)."""
    nz, nr, nc = sv.shape
    out = np.full((nr, nc), np.nan, dtype=np.float64)
    for z in range(nz):
        alt = ELEVATIONS_KM[z]
        ok = np.isfinite(sv[z]) & (sv[z] >= thr)
        better = ok & (np.isnan(out) | (alt > out))
        out = np.where(better, alt, out)
    return out


def _vil_peak(labeled, label_id, volume):
    """VIL massimo sulle colonne della cella (formula Greene & Clark verificata)."""
    ys, xs = np.where(labeled == label_id)
    peaks = []
    nz = int(volume.shape[0])
    for rr, cc in zip(ys.tolist(), xs.tolist()):
        col = volume[:, rr, cc]
        vil = 0.0
        has = False
        for z in range(nz):
            zdbz = col[z]
            if np.isfinite(zdbz):
                zlin = 10.0 ** (float(zdbz) / 10.0)
                vil += 3.44e-6 * (zlin ** (4.0 / 7.0)) * 1000.0
                has = True
        if has:
            peaks.append(vil)
    return (float(max(peaks)) if peaks else None,
            (float(np.mean(peaks)) if peaks else None))


def _quality_for(interp_mask, r0, r1, c0, c1):
    if interp_mask is None:
        return "A_observed", 0
    try:
        n = int(np.sum(interp_mask[:, r0:r1, c0:c1]))
    except Exception:
        n = 0
    return ("B_vi_interpolated" if n > 0 else "A_observed"), n


def _analyze_cells(volume, vmi, labeled, comps, thresholds,
                   interp_mask, source, timestamp, pad=5):
    """Analisi per cella: tutti i parametri dipendenti dal volume 2D CAPPI."""
    out_cells = []
    vmi_v = _mask_sentinels(vmi) if vmi is not None else None
    for comp in comps:
        sv, (r0, r1, c0, c1) = _subvolume(volume, comp, pad)
        finite = np.isfinite(sv)
        n_valid = int(finite.sum())
        q, n_interp = _quality_for(interp_mask, r0, r1, c0, c1)
        det = {"interpolated_voxels_in_subvolume": n_interp}

        cell = {
            "cell_id": comp["label_id"],
            "area_px": comp["area_px"],
            "bbox_rc_full": comp["bbox_rc"],
            "centroid_rc_geometric": [_r2(v, 3) for v in comp["centroid_rc"]],
            "centroid_rc_weighted": [_r2(v, 3) for v in comp["centroid_weighted_rc"]],
            "max_dbz_vmi": comp["max_dbz_vmi"],
            "quality": q,
            "detail": det,
        }

        # --- campi indipendenti dalla soglia (semantiche verificate) -------
        maxref = float(np.nanmax(sv)) if n_valid > 0 else None
        meanref = float(np.nanmean(sv)) if n_valid > 0 else None
        if maxref is not None:
            cand = np.argwhere(finite & (sv == maxref))
            maxalt = float(ELEVATIONS_KM[int(cand[:, 0].max())]) if len(cand) else None
        else:
            maxalt = None
        cell["MAX_REFLECTIVITY"] = _field(
            _r2(maxref, 1), SRC_VOL, timestamp, q, "max dBZ sui voxel validi del sotto-volume",
            null_reason="no_valid_voxels")
        cell["MEAN_REFLECTIVITY"] = _field(
            _r2(meanref, 2), SRC_VOL, timestamp, q, "media dBZ sui voxel validi del sotto-volume",
            null_reason="no_valid_voxels")
        cell["MAX_ALTITUDE"] = _field(
            maxalt, SRC_VOL, timestamp, q, "quota (km) del voxel con riflettivita' massima",
            null_reason="no_valid_voxels")

        et18_arr = _column_echo_top(sv, 18.0)
        et18 = _r2(float(np.nanmax(et18_arr))) if np.any(np.isfinite(et18_arr)) else None
        vil_peak, vil_mean = (_vil_peak(labeled, comp["label_id"], volume)
                              if labeled is not None and vmi is not None else (None, None))
        vil_peak = _r2(vil_peak, 6)
        vil_mean = _r2(vil_mean, 6)
        vd = _r2(vil_peak / et18, 6) if (vil_peak is not None and et18) else None
        cell["VIL"] = _field(vil_peak, SRC_VOL, timestamp, "C_derived", REF_VIL,
                             null_reason="no_valid_reflectivity_in_cell_columns")
        cell["VIL_MEAN_OVER_CELL"] = _field(
            vil_mean, SRC_VOL, timestamp, "C_derived", "media VIL colonnare sulla cella",
            null_reason="no_valid_reflectivity_in_cell_columns")
        cell["VIL_DENSITY"] = _field(
            vd, SRC_VOL, timestamp, "C_derived",
            "VIL (kg/m2)/echo_top_18 (km) -> g/m3 (Greene & Clark 1972; Lakshmanan 2013)",
            null_reason="vil_or_echo_top_18_missing")
        cell["ECHO_TOP_18"] = _field(
            et18, SRC_VOL, timestamp, q, REF_ET,
            null_reason="no_voxel_ge_18dbz_in_cell")

        # --- campi per soglia configurabile -------------------------------
        per_thr = {}
        for thr in thresholds:
            t = int(thr)
            voxels = finite & (sv >= t)
            cv = int(voxels.sum())
            et_arr = _column_echo_top(sv, t)
            et = _r2(float(np.nanmax(et_arr))) if np.any(np.isfinite(et_arr)) else None
            alt_min = None
            if np.any(voxels):
                zs = np.where(voxels.any(axis=(1, 2)))[0]
                alt_min = float(ELEVATIONS_KM[int(zs.min())]) if len(zs) else None
            extent = _r2(et - alt_min) if (et is not None and alt_min is not None) else None
            per_thr[str(t)] = {
                "CELL_VOLUME": _field(cv, SRC_VOL, timestamp, q, REF_VOL, detail=det),
                "ECHO_TOP": _field(et, SRC_VOL, timestamp, q, REF_ET,
                                   null_reason="no_voxel_ge_{}dbz_in_cell".format(t)),
                "VERTICAL_EXTENT": _field(extent, SRC_VOL, timestamp, q, REF_MORPH,
                                          null_reason="no_voxel_ge_{}dbz_in_cell".format(t),
                                          detail={"echo_base_km": alt_min,
                                                  "echo_top_km": et}),
                # VIL / densita' ripetute per soglia per completezza del contratto:
                # il valore e' indipendente dalla soglia (calcolato su tutta l'eco).
                "VIL": dict(cell["VIL"]),
                "VIL_DENSITY": dict(cell["VIL_DENSITY"]),
                "MAX_REFLECTIVITY": dict(cell["MAX_REFLECTIVITY"]),
                "MEAN_REFLECTIVITY": dict(cell["MEAN_REFLECTIVITY"]),
                "MAX_ALTITUDE": dict(cell["MAX_ALTITUDE"]),
                "VERTICAL_GROWTH": None,   # compilato solo se prev e' disponibile
                "VOLUME_GROWTH": None,
            }
        cell["by_threshold"] = per_thr
        out_cells.append(cell)
    return out_cells


def _destination(lat, lon, bearing_deg, dist_km):
    """Punto a distanza/rotamento dati (formula sferica, come in cross_section.py)."""
    R = 6371.0
    d = float(dist_km) / R
    brng = math.radians(float(bearing_deg))
    la = math.radians(float(lat))
    lo = math.radians(float(lon))
    lat2 = math.asin(math.sin(la) * math.cos(d)
                     + math.cos(la) * math.sin(d) * math.cos(brng))
    lon2 = lo + math.atan2(math.sin(brng) * math.sin(d) * math.cos(la),
                           math.cos(d) - math.sin(la) * math.sin(lat2))
    return float(math.degrees(lat2)), float(math.degrees(lon2))


def _orientation(cell, motion):
    d = (motion or {}).get("direction_toward_deg")
    if d is not None:
        return float(d), "motion_direction"
    r0, r1, c0, c1 = cell["bbox_rc_full"]
    if (r1 - r0) >= (c1 - c0):
        return 180.0, "cell_major_axis_row"
    return 90.0, "cell_major_axis_col"


def _compact_section(volume, cell, motion, crs_wkt, transform, timestamp,
                     n_points, length_km):
    """Sezione verticale compatta: griglia [punto][quota], nessun valore inventato."""
    base = {
        "available": False, "grid": None,
        "source": SRC_SECTION, "timestamp": timestamp,
        "quality": "D_unavailable", "null_reason": None,
        "reference": "cross_section.py (Zhang 2005 VI; Doviak & Zrnic 1993)",
    }
    if volume is None or crs_wkt is None or transform is None:
        base["null_reason"] = "no_volume_or_projection"
        return base
    cr, cc = cell["centroid_rc_weighted"]
    try:
        _cs.set_projection(crs_wkt, transform, int(volume.shape[1]), int(volume.shape[2]))
        lat, lon = _cs.rc_to_latlon(float(cr), float(cc))
        bearing, how = _orientation(cell, motion)
        half = float(length_km) / 2.0
        la1, lo1 = _destination(lat, lon, (bearing + 180.0) % 360.0, half)
        la2, lo2 = _destination(lat, lon, bearing, half)
        sec = _cs.extract_cross_section(volume, la1, lo1, la2, lo2,
                                        n_points=int(n_points))
        mm, nn, tot = _cs.verify_no_invented(sec, volume)
        grid = [[_r2(v, 1) if np.isfinite(v) else None for v in row]
                for row in sec["profile"]]
        good = (mm == 0 and nn == 0)
        out = {
            "available": True,
            "grid": grid,
            "n_points": int(n_points),
            "n_levels": len(ELEVATIONS_KM),
            "z_km": ELEVATIONS_KM,
            "dist_km": [_r2(v, 2) for v in sec["dist_km"]],
            "bearing_deg": _r2(bearing, 1),
            "orientation_source": how,
            "length_km": float(length_km),
            "source": SRC_SECTION,
            "timestamp": timestamp,
            "quality": "A_verified_no_invented" if good else "X_mismatch_detected",
            "null_reason": None,
            "reference": "cross_section.py (nearest neighbor 1 km; Zhang 2005 VI)",
            "detail": {"mismatch": int(mm), "nan_mismatch": int(nn), "total": int(tot),
                       "lat_center": _r2(lat, 5), "lon_center": _r2(lon, 5),
                       "row_sample": [int(v) for v in sec["row"]],
                       "col_sample": [int(v) for v in sec["col"]]},
        }
        return out
    except Exception as exc:
        base["null_reason"] = "error:{}:{}".format(exc.__class__.__name__, exc)
        return base
    finally:
        _cs.reset_projection()


def _match_previous(cell, prev_cells, pixel_km, max_match_km):
    cr, cc = cell["centroid_rc_weighted"]
    best = None
    best_d = None
    for pc in prev_cells:
        pr, pcc = pc["centroid_rc_weighted"]
        d = math.hypot(float(cr) - float(pr), float(cc) - float(pcc)) * float(pixel_km)
        if d <= float(max_match_km) and (best_d is None or d < best_d):
            best, best_d = pc, d
    return best, best_d


def _interval_min(now_iso, prev_iso):
    try:
        a = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(prev_iso).replace("Z", "+00:00"))
        return _r2((a - b).total_seconds() / 60.0, 2)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------
def build_volume_payload(volume, vmi, *, timestamp_utc,
                         source_label=SRC_VOL,
                         crs_wkt=None, transform=None,
                         interp_mask=None,
                         thresholds=DEFAULT_THRESHOLDS,
                         prev=None, motions=None, pad=5, min_area_px=4,
                         n_section_points=200, section_length_km=60.0,
                         pixel_km=1.0, max_match_km=20.0,
                         include_sections=True):
    """Costruisce il payload del blob `data/radar/3d/volume.json`.

    volume : ndarray (10,H,W) float32 dBZ (prodotto da volume3d.build_volume_from_cappi)
    vmi    : ndarray (H,W) dBZ, usata per etichettare le celle (8-connettivita', >=30 dBZ)
    prev   : None, oppure dict {volume, vmi, timestamp_utc, interp_mask} (array
             grezzi in memoria), oppure il PAYLOAD COMPATTO precedente letto dal
             blob del branch radar-3d (chiave `cells`, senza `volume`): in quel
             caso il growth e' calcolato dai valori pubblicati.
    motions: dict {cell_id: {"direction_toward_deg": gradi}} per l'asse longitudinale

    Ritorna sempre un dict serializzabile (nessun NaN): degrada a cells=[] con
    null_reason="volume_unavailable" se volume e' None.
    """
    thresholds = tuple(int(t) for t in (thresholds or DEFAULT_THRESHOLDS))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "schema": SCHEMA,
        "generated_at_utc": generated,
        "volume_available": volume is not None,
        "volume_source": VOLUME_SOURCE,
        "volume_acquisition": VOLUME_ACQUISITION,
        "volume_source_detail": (
            "I 10 CAPPI DPC (quote 1..10 km, griglia comune 1 km) sono impilati e "
            "interpolati verticalmente (VI, Zhang 2005) solo su buchi singoli adiacenti. "
            "NON e' acquisizione volumetrica polare nativa: il mosaico DPC e' 2D a "
            "singola riflettivita', senza velocita' radiale. Nessuna misura di rotazione."
        ),
        "source": source_label,
        "timestamp": timestamp_utc,
        "reference": REF_VI + "; TITAN (Dixon & Wiener 1993) stacking CAPPI",
        "volume_timestamp_utc": timestamp_utc,
        "thresholds_dbz": list(thresholds),
        "grid": {
            "shape_zyx": ([int(v) for v in volume.shape] if volume is not None else None),
            "elevations_km": ELEVATIONS_KM,
            "pixel_km": float(pixel_km),
            "voxel_km3": float(pixel_km) ** 3,
        },
        "interpolation": {
            "scheme": "VI ( Zhang et al. 2005 ), lineare su buchi singoli",
            "interpolated_voxels_total": (int(interp_mask.sum())
                                          if interp_mask is not None else None),
            "extrapolation_below_1km": False,
            "extrapolation_above_10km": False,
            "reference": REF_VI,
        },
        "quality_legend": QUALITY_LEGEND,
        "cell_matching": {
            "method": "centroide pesato piu' vicino",
            "max_distance_km": float(max_match_km),
            "note": "SCELTA INGEGNERISTICA per il confronto temporale dei growth",
        },
        "cells": [],
    }
    if volume is None:
        payload["null_reason"] = "volume_unavailable"
        return payload

    volume = np.asarray(volume, dtype=np.float32)
    if volume.ndim != 3 or volume.shape[0] != 10:
        payload["volume_available"] = False
        payload["null_reason"] = "invalid_volume_shape:expected_10_H_W"
        return payload

    labeled, comps = _label_cells(vmi, min_area_px)
    cells = _analyze_cells(volume, vmi, labeled, comps, thresholds,
                           interp_mask, source_label, timestamp_utc, pad)

    # --- growth temporale (solo con volume precedente) --------------------
    # Due forme di `prev`, entrambe reali:
    #   * {"volume": ndarray, "vmi": ..., ...} -> volume grezzo in memoria
    #     (percorso storico, usato dai test e da chiamanti diretti);
    #   * payload compatto di data/radar/3d/volume.json (chiave `cells`,
    #     SENZA `volume`) -> unico blob vivo del branch radar-3d. In questo
    #     caso il growth si calcola dai VALORI PUBBLICATI, non da array.
    prev_is_compact = (isinstance(prev, dict)
                       and "volume" not in prev
                       and isinstance(prev.get("cells"), list))
    prev_cells = []
    if prev is not None and prev.get("volume") is not None:
        try:
            plabeled, pcomps = _label_cells(prev.get("vmi"), min_area_px)
            prev_cells = _analyze_cells(
                np.asarray(prev["volume"], dtype=np.float32), prev.get("vmi"),
                plabeled, pcomps, thresholds, prev.get("interp_mask"),
                source_label, prev.get("timestamp_utc"), pad)
        except Exception:
            prev_cells = []
    interval = (_interval_min(timestamp_utc, prev.get("timestamp_utc"))
                if prev is not None else None)

    motions = motions or {}
    for cell in cells:
        if include_sections:
            motion = motions.get(str(cell["cell_id"])) or motions.get(cell["cell_id"]) or {}
            cell["cross_section_longitudinal"] = _compact_section(
                volume, cell, motion, crs_wkt, transform, timestamp_utc,
                n_section_points, section_length_km)
            # trasversale = longitudale + 90 gradi; stessa griglia compatta
            bearing, how = _orientation(cell, motion)
            trans_motion = {"direction_toward_deg": (bearing + 90.0) % 360.0}
            cell["cross_section_transversal"] = _compact_section(
                volume, cell, trans_motion, crs_wkt, transform, timestamp_utc,
                n_section_points, section_length_km)

        if prev_is_compact:
            # Compilato in blocco da growth_from_previous_payload() qui sotto.
            continue
        for t in thresholds:
            fields = cell["by_threshold"][str(t)]
            if prev is None or prev.get("volume") is None:
                why = "no_previous_volume"
                matched = None
                dist = None
            else:
                matched, dist = _match_previous(cell, prev_cells, pixel_km, max_match_km)
                why = ("no_matching_previous_cell" if matched is None else None)
            et_now = fields["ECHO_TOP"]["value"]
            cv_now = fields["CELL_VOLUME"]["value"]
            if why is None:
                et_prev = matched["by_threshold"][str(t)]["ECHO_TOP"]["value"]
                cv_prev = matched["by_threshold"][str(t)]["CELL_VOLUME"]["value"]
                vg = _r2(et_now - et_prev) if (et_now is not None and et_prev is not None) else None
                vog = _r2(cv_now - cv_prev) if (cv_now is not None and cv_prev is not None) else None
                if vg is None:
                    why = "echo_top_missing_in_current_or_previous"
                ddetail = {
                    "interval_min": interval,
                    "matched_prev_cell_id": matched["cell_id"],
                    "distance_km": _r2(dist, 2),
                }
            else:
                vg = vog = None
                ddetail = {"interval_min": interval}
            fields["VERTICAL_GROWTH"] = _field(
                vg, SRC_VOL, timestamp_utc, "C_derived", REF_GROWTH,
                null_reason=why, detail=ddetail)
            fields["VOLUME_GROWTH"] = _field(
                vog, SRC_VOL, timestamp_utc, "C_derived", REF_GROWTH,
                null_reason=why, detail=ddetail)

    payload["cells"] = cells
    if prev_is_compact:
        growth_from_previous_payload(payload, prev)
    return payload


def _age_seconds(now_iso, older_iso):
    """Eta' in secondi fra due ISO8601 (Z -> +00:00). None se non parsabile."""
    try:
        a = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(older_iso).replace("Z", "+00:00"))
        return max(0, int((a - b).total_seconds()))
    except Exception:
        return None


def load_previous_volume(path=None):
    """Legge il blob compatto ripristinato nel job (branch ``radar-3d``).

    Ritorna ``(payload, reason)``. Il secondo valore e' SEMPRE esplicito:
      * ``None``            -> payload valido, growth calcolabile;
      * ``"no_previous_volume"`` -> file assente (branch vuoto / primo run);
      * ``"previous_volume_unavailable"`` -> volume_available non True;
      * ``"previous_volume_unreadable:<...>"`` -> JSON o I/O guasti.

    Nessun valore viene inventato: un payload degradato o corrotto NON viene
    usato come "precedente" e il growth resta null con il motivo reale.
    """
    path = path or VOLUME_BLOB_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, "no_previous_volume"
    except (OSError, ValueError) as exc:
        return None, "previous_volume_unreadable:{}".format(exc.__class__.__name__)
    if not isinstance(data, dict):
        return None, "previous_volume_unreadable:not_a_dict"
    if data.get("volume_available") is not True:
        return None, "previous_volume_unavailable"
    if not isinstance(data.get("cells"), list) or not data["cells"]:
        return None, "previous_volume_unavailable"
    return data, None


def growth_from_previous_payload(payload, prev_payload, *, pixel_km=None,
                                 max_match_km=None):
    """Compila VERTICAL_GROWTH / VOLUME_GROWTH da DUE payload compatti.

    ``prev_payload`` e' il blob ``data/radar/3d/volume.json`` del run
    precedente; non contiene array grezzi (a dimensione costante), quindi il
    confronto usa gli STESSI valori pubblicati (ECHO_TOP e CELL_VOLUME per
    soglia) e lo stesso matching a centroide pesato di ``_match_previous``.
    Aggiunge ``payload["growth"]`` con il riepilogo e ritorna ``(payload,
    summary)``. Non solleva: ogni assenza diventa un null con motivo.
    """
    if not isinstance(payload, dict):
        return payload, {"available": False, "reason": "invalid_current_payload"}
    thresholds = tuple(int(t) for t in (
        payload.get("thresholds_dbz") or DEFAULT_THRESHOLDS))
    px = _finite_pixel(pixel_km if pixel_km is not None
                       else (payload.get("grid") or {}).get("pixel_km", 1.0))
    radius = _finite_pixel(max_match_km if max_match_km is not None
                           else (payload.get("cell_matching") or {}).get(
                               "max_distance_km", 20.0))
    now_ts = (payload.get("volume_timestamp_utc") or payload.get("timestamp"))
    prev_ts = ((prev_payload or {}).get("volume_timestamp_utc")
               or (prev_payload or {}).get("timestamp"))
    interval = _interval_min(now_ts, prev_ts) if prev_ts is not None else None
    valid_prev = (isinstance(prev_payload, dict)
                  and prev_payload.get("volume_available") is True
                  and isinstance(prev_payload.get("cells"), list)
                  and bool(prev_payload["cells"]))
    prev_cells = prev_payload["cells"] if valid_prev else []
    matched = 0
    unmatched = 0
    for cell in payload.get("cells") or []:
        best, dist = ((None, None) if not valid_prev else
                      _match_previous(cell, prev_cells, px, radius))
        if best is not None:
            matched += 1
        else:
            unmatched += 1
        why = (None if best is not None else
               ("no_previous_volume" if not valid_prev else
                "no_matching_previous_cell"))
        detail = {
            "interval_min": interval,
            "previous_volume_timestamp_utc": prev_ts,
            "source_blob": VOLUME_BLOB_PATH,
            "branch": VOLUME_BRANCH,
        }
        if why is None:
            detail["matched_prev_cell_id"] = best.get("cell_id")
            detail["distance_km"] = _r2(dist, 2)
        for t in thresholds:
            fields = (cell.get("by_threshold") or {}).get(str(t))
            if not isinstance(fields, dict):
                continue
            vg = vog = None
            if why is None:
                pfields = (best.get("by_threshold") or {}).get(str(t)) or {}
                et_now = _plain_value(fields, "ECHO_TOP")
                et_prev = _plain_value(pfields, "ECHO_TOP")
                cv_now = _plain_value(fields, "CELL_VOLUME")
                cv_prev = _plain_value(pfields, "CELL_VOLUME")
                vg = _r2(et_now - et_prev) if (et_now is not None
                                               and et_prev is not None) else None
                vog = _r2(cv_now - cv_prev) if (cv_now is not None
                                                and cv_prev is not None) else None
                # Motivi PER CAMPO: un valore disponibile non puo' portare un
                # null_reason (contratto _field), anche se l'altro e' mancante.
                why_vg = (None if vg is not None else
                          "echo_top_missing_in_current_or_previous")
                why_vog = (None if vog is not None else
                           "cell_volume_missing_in_current_or_previous")
            else:
                why_vg = why_vog = why
            fields["VERTICAL_GROWTH"] = _field(
                vg, SRC_GROWTH_PAYLOAD, now_ts, "C_derived",
                REF_GROWTH_PAYLOAD, null_reason=why_vg, detail=detail)
            fields["VOLUME_GROWTH"] = _field(
                vog, SRC_GROWTH_PAYLOAD, now_ts, "C_derived",
                REF_GROWTH_PAYLOAD, null_reason=why_vog, detail=detail)
    summary = {
        "available": valid_prev,
        "reason": (None if valid_prev else
                   ("no_previous_volume" if not isinstance(prev_payload, dict)
                    else "previous_volume_unavailable")),
        "previous_volume_timestamp_utc": prev_ts,
        "interval_min": interval,
        "cells_total": len(payload.get("cells") or []),
        "matched_cells": matched,
        "unmatched_cells": unmatched,
        "source_blob": VOLUME_BLOB_PATH,
        "branch": VOLUME_BRANCH,
        "reference": REF_GROWTH_PAYLOAD,
    }
    payload["growth"] = summary
    return payload, summary


def growth_for_cell(cell, prev_payload, *, thr_dbz=30, thresholds=None,
                    pixel_km=None, max_match_km=None):
    """Growth di UNA cella dal payload compatto precedente.

    Ritorna ``(value, null_reason, detail)`` per VOLUME_GROWTH. Usa ESATTAMENTE
    la stessa logica di ``growth_from_previous_payload`` (via un holder
    deep-coppiato: la cella chiamante NON viene modificata). Nessun valore
    inventato: se manca il precedente o il match, il motivo e' esplicito.
    """
    import copy
    ths = tuple(int(t) for t in (thresholds or (int(thr_dbz),)))
    holder = {"cells": [copy.deepcopy(cell)], "thresholds_dbz": list(ths),
              "volume_timestamp_utc": None}
    if pixel_km is not None:
        holder["grid"] = {"pixel_km": pixel_km}
    if max_match_km is not None:
        holder["cell_matching"] = {"max_distance_km": max_match_km}
    growth_from_previous_payload(holder, prev_payload)
    field = ((holder["cells"][0].get("by_threshold") or {}).get(str(ths[0]))
             or {}).get("VOLUME_GROWTH") or {}
    return field.get("value"), field.get("null_reason"), field.get("detail")


def _finite_pixel(value, default=1.0):
    """Valore positivo finito per pixel_km / max_match_km."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if (math.isfinite(f) and f > 0) else default


def _plain_value(block, key):
    """Valore grezzo di un campo _field() (o None)."""
    if isinstance(block, dict):
        f = block.get(key)
        if isinstance(f, dict):
            return f.get("value")
        return f
    return None


def write_volume_payload(payload, out_dir):
    """Scrive `volume.json` in modo atomico (tmp + os.replace). Ritorna la path.

    17/11/2026 (A7): un payload degradato (`volume_available` non True) NON
    deve cancellare un volume valido gia' pubblicato. In quel caso il documento
    precedente viene conservato byte per byte e marcato `stale: true`, con
    `last_valid_scan` (data dell'ultimo volume valido), `stale_age_seconds` e
    `stale_reason` (il null_reason del run che non ha prodotto dati). Nessuna
    geometria/valore inventato: i dati del volume restano quelli reali del run
    valido piu' recente, dichiarati come non correnti.
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "volume.json")
    is_empty = (not isinstance(payload, dict)
                or payload.get("volume_available") is not True)
    if is_empty and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                previous = json.load(fh)
        except (OSError, ValueError):
            previous = None
        if isinstance(previous, dict) and previous.get("volume_available") is True:
            carried = dict(previous)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            last = (carried.get("generated_at_utc")
                    or carried.get("volume_timestamp_utc")
                    or carried.get("timestamp"))
            carried["stale"] = True
            carried["last_valid_scan"] = last
            carried["stale_age_seconds"] = _age_seconds(now, last)
            carried["stale_reason"] = (
                payload.get("null_reason") or "volume_unavailable")
            carried["carried_forward_at"] = now
            carried["carried_forward_from"] = "volume.json (ultimo volume valido)"
            payload = carried
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))
    json.loads(text)  # validazione prima della replace
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".volume-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def build_and_publish(cappi, ref_rd, vmi_rd, *, timestamp_utc,
                      thresholds=DEFAULT_THRESHOLDS, **kwargs):
    """Convenienza: volume3d -> payload. Degrada se men di 4 CAPPI validi."""
    from . import volume3d
    volume = volume3d.build_volume_from_cappi(cappi, ref_rd)
    if volume is None:
        p = build_volume_payload(None, None, timestamp_utc=timestamp_utc,
                                 thresholds=thresholds)
        p["null_reason"] = "cappi_levels_lt_4"
        return p
    vmi_arr = getattr(vmi_rd, "data", None) if vmi_rd is not None else None
    crs_wkt = getattr(ref_rd, "crs_wkt", None) if ref_rd is not None else None
    transform = getattr(ref_rd, "transform", None) if ref_rd is not None else None
    return build_volume_payload(
        volume, vmi_arr, timestamp_utc=timestamp_utc, thresholds=thresholds,
        crs_wkt=crs_wkt, transform=transform, **kwargs)
