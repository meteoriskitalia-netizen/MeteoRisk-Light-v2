#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Morfologia volumetrica come EVIDENZA OSSERVATA del classificatore.

Questo modulo NON e' un pannello e NON e' un file separato: converte i parametri
gi'a calcolati dal volume CAPPI 3D (``volume_publisher.build_volume_payload``)
in un campo ``morphology_organization`` che viaggia DENTRO ogni record di
``convective_structures.json``, accanto a ``soi`` / ``soi_v2``.

REGOLA D'ORO (richiesta dell'utente, 17/11/2026):
    "non si deve vedere, deve servire come dato in piu' per la validazione del
     rilevamento di strutture convettive organizzate".

Conseguenze applicate qui:
  * SOLO CAPPI reali. Nessun campo ambientale NWP: quello e' il layer Fase 2
    (``environment``), non un'osservazione radar. Nessun dato Doppler: la
    rotazione resta ``rotation_measurable: false`` e il suo classificatore
    resta intatto.
  * Nessuna inferenza da 2 CAPPI. Se ``volume_available`` non e' True oppure
    il set e' sotto soglia, il campo e' ``None`` con
    ``null_reason = "cappi_levels_lt_4"``. Il gate grandine di config.py puo'
    scaricare solo CAPPI_2/CAPPI_6: in quel caso il campo e' SEMPRE null.
  * ``soi``, ``level``, ``soi_v2``, ``rotation_shear`` e
    ``rotation_classification`` NON vengono toccati: il campo e' additivo e
    verificabile, non una sostituzione del motore SOI esistente.

Classificazione ESPLICITA e spiegabile (soglie ``SCELTA INGEGNERISTICA``,
dichiarate finche' non esistono etichette locali):

    organized_vertical = echo_top_m   >= echo_top_min_m (6000 m)
                     AND vertical_extent_km >= vertical_extent_min_km (4 km)
                     AND vil_density_g_m3  >= vil_density_min_g_m3 (3.5 g/m3)

Il valore 3.5 g/m3 e' la soglia citata da Amburn & Wolf (1997); e' una SCELTA
di soglia, NON una calibrazione locale (nessuna etichetta verificata qui). Bedka
(overshooting top IR) e Witt (MESH) NON sono pertinenti a questo campo e NON
vengono citati.

Funzione pura: nessuna I/O, nessun import di config.py.
"""
from __future__ import annotations

import math

from .tracking import haversine_km

MORPHOLOGY_VERSION = "1.0.0"
SOURCE = "CAPPI_3D"
NULL_REASON_CAPPI = "cappi_levels_lt_4"

# Soglia dBZ usata per VERTICAL_EXTENT / CELL_VOLUME nel volume publisher.
# DISTINCT dall'echo top 18 dBZ (che e' il tetto dell'eco precipitante).
MORPH_THRESHOLD_DBZ = 30

DEFAULT_THRESHOLDS = {
    "echo_top_min_m": 6000.0,
    "vertical_extent_min_km": 4.0,
    "vil_density_min_g_m3": 3.5,
    "max_match_km": 20.0,
}

REFERENCE = {
    "echo_top_m": (
        "Lakshmanan et al. 2013: echo top = quota massima con Z>=18 dBZ; "
        "SCELTA INGEGNERISTICA soglia 6000 m (nessuna calibrazione locale)"),
    "vertical_extent_km": (
        "SCELTA INGEGNERISTICA: estensione verticale = quota max - quota min "
        "con Z>=30 dBZ (CAPPI DPC 1 km/passaggio)"),
    "vil_density_g_m3": (
        "Greene & Clark 1972 (VIL); soglia 3.5 g/m3 citata da Amburn & Wolf "
        "1997, SCELTA INGEGNERISTICA non calibrata localmente"),
    "volume_growth": (
        "SCELTA INGEGNERISTICA: delta voxel CAPPI (>=30 dBZ) fra due slot "
        "consecutivi; null con motivo se non c'e' volume precedente"),
    "cells_3d_matched": (
        "SCELTA INGEGNERISTICA: matching centroide pesato (max 20 km), "
        "stesso criterio di volume_publisher.cell_matching"),
    "classification": (
        "SCELTA INGEGNERISTICA: organized_vertical se echo_top>=6000 m E "
        "vertical_extent>=4 km E vil_density>=3.5 g/m3; nessuna etichetta "
        "locale, nessuna probabilita' calibrata"),
}


def _cappi_levels(volume_payload):
    """Livelli CAPPI REALI del volume (>=4 richiesti), NON numero di celle."""
    if not isinstance(volume_payload, dict):
        return 0
    if volume_payload.get("cappi_levels") is not None:
        return int(volume_payload["cappi_levels"])
    elev = ((volume_payload.get("grid") or {}).get("elevations_km"))
    if isinstance(elev, (list, tuple)):
        return len(elev)
    return 0


def _finite(value):
    """Valore finito o None (nessun NaN nei payload JSON)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def thresholds_from_config(cfg=None):
    """Soglie effettive: default + override opzionale da volume.morphology."""
    out = dict(DEFAULT_THRESHOLDS)
    if isinstance(cfg, dict):
        for key in out:
            if key in cfg:
                v = _finite(cfg[key])
                if v is not None and v >= 0:
                    out[key] = v
    return out


def null_field(reason=NULL_REASON_CAPPI, detail=None):
    """Campo null ESPLICITO: mai inferire da dati assenti."""
    out = {
        "classification": None,
        "supports_organized": False,
        "null_reason": reason,
        "source": SOURCE,
        "valid_time": None,
        "quality": "D_unavailable",
        "reference": dict(REFERENCE),
        "thresholds": thresholds_from_config(),
        "gates": {},
        "values": {},
        "value_null_reasons": {},
    }
    if detail is not None:
        out["detail"] = detail
    return out


def _field_value(block, key=None):
    """Estrae il valore da un campo _field() di volume_publisher."""
    if isinstance(block, dict):
        return block.get("value") if key is None else (block.get(key) or {}).get("value")
    return None


def _cell_values(cell, thr_dbz=MORPH_THRESHOLD_DBZ):
    """Valori morfologici grezzi da UNA cella del payload volume.json."""
    by_thr = cell.get("by_threshold") or {}
    thr_key = str(int(thr_dbz))
    thr = by_thr.get(thr_key) or {}
    et18_km = _finite(_field_value(cell, "ECHO_TOP_18"))
    echo_top_m = int(round(et18_km * 1000.0)) if et18_km is not None else None
    vext = _finite(_field_value(thr, "VERTICAL_EXTENT"))
    vd = _finite(_field_value(cell, "VIL_DENSITY"))
    growth = _finite(_field_value(thr, "VOLUME_GROWTH"))
    return {
        "echo_top_m": echo_top_m,
        "vertical_extent_km": round(vext, 2) if vext is not None else None,
        "vil_density_g_m3": round(vd, 6) if vd is not None else None,
        "volume_growth": growth,
    }


def _cell_quality(cell):
    q = (cell.get("quality") or "").lower()
    if "vi_interpolated" in q:
        return "B_vi_interpolated"
    if "observed" in q:
        return "A_observed"
    if "derived" in q:
        return "C_derived"
    return "C_derived"


def _lonlat_distance_km(position, cell_latlon, order="lon_lat"):
    """Distanza fra `position` DELLA STRUTTURA e un centroide 3D.

    CONVENZIONI MISURATE (entrambe presenti nel progetto):
      * produzione reale: `structure.position = [lon, lat]` (es.
        [13.92339, 45.09728] e' Modena; leggerlo [lat, lon] darebbe
        `no_3d_cell_within_20km` su 17/17 strutture del 2026-07-01);
      * test storici: `position = [lat, lon]` perche' costruiti con
        `cross_section.rc_to_latlon()`, che restituisce (lat, lon);
      * `tracking.haversine_km()` vuole `(lon, lat)` per entrambi i punti.
    L'ordine usato e' un PARAMETRO esplicito e viene registrato nel campo
    `detail.position_order`: nessuna inferenza silenziosa.
    """
    a, b = float(position[0]), float(position[1])
    lon, lat = (a, b) if order == "lon_lat" else (b, a)
    clat, clon = cell_latlon
    return haversine_km((lon, lat), (float(clon), float(clat)))


def _match_cells(structure, cells, ref_rd, max_match_km, thr_dbz):
    """Celle 3D associate: (list[(cell, dist_km)], reason, position_order).

    Prova le due convenzioni DICHIARATE, in ordine: `lon_lat` (produzione)
    poi `lat_lon_legacy` (test storici). Si accetta la prima che produca un
    match entro `max_match_km`; con distanze dell'ordine di 20 km l'altra
    convenzione landing a migliaia di km non puo' produrre match spurii.
    """
    if not cells:
        return [], "no_3d_cells_in_volume", None
    pos = structure.get("position") or []
    if len(pos) < 2:
        # MERGE A+B (2026-11-17): B ha aggiunto questo early-return, ma in main
        # il fallback su cell_id (qui sotto, "Ultima ratio") era raggiungibile
        # anche senza posizione ed e' usato dalle celle tracciate. Senza questa
        # guardia, le strutture senza `position` non prendono piu' il match su
        # cell_id e il campo 3D resta null. Si ripristina SOLO il fallback su
        # cell_id; la correzione lon/lat di B resta intatta per chi ha posizione.
        # Nessuna inferenza geometrica, nessun dato inventato.
        _cid = structure.get("cell_id")
        if _cid is not None:
            for _cell in cells:
                if _cell.get("cell_id") == _cid:
                    return [(_cell, None)], None, "cell_id_fallback"
        return [], "no_structure_position", None
    H = W = None
    grid = ((ref_rd or {}).get("grid") or {}) if isinstance(ref_rd, dict) else {}
    shape = grid.get("shape_zyx")
    if isinstance(shape, (list, tuple)) and len(shape) == 3:
        H, W = int(shape[1]), int(shape[2])
    use_proj = False
    if H and W:
        crs = ((ref_rd or {}).get("crs_wkt") if isinstance(ref_rd, dict)
               else getattr(ref_rd, "crs_wkt", None))
        tr = ((ref_rd or {}).get("transform") if isinstance(ref_rd, dict)
              else getattr(ref_rd, "transform", None))
        if crs and tr:
            try:
                from . import cross_section as _cs
                _cs.set_projection(crs, tr, H, W)
                use_proj = True
            except Exception:
                use_proj = False
    centroids = []
    try:
        for cell in cells:
            cen = cell.get("centroid_rc_weighted") or cell.get("centroid_rc_geometric") or []
            if len(cen) < 2:
                continue
            clat = clon = None
            if use_proj:
                try:
                    # rc_to_latlon restituisce (lat, lon): NON scambiare.
                    clat, clon = _cs.rc_to_latlon(float(cen[0]),
                                                   float(cen[1]))
                except Exception:
                    clat = clon = None
            if clat is not None and clon is not None:
                centroids.append((cell, (float(clat), float(clon))))
    finally:
        if use_proj:
            try:
                _cs.reset_projection()
            except Exception:
                pass
    matched, order_used = [], None
    for order in ("lon_lat", "lat_lon_legacy"):
        matched = []
        for cell, latlon in centroids:
            dist = _lonlat_distance_km(pos, latlon, order=order)
            if dist <= float(max_match_km):
                matched.append((cell, round(dist, 2)))
        if matched:
            order_used = order
            break
    if not matched:
        # Ultima ratio: match sul cell_id del tracking, solo se e' lo stesso
        # spazio di ID (dichiarato nel detail). Nessuna inferenza geometrica.
        cid = structure.get("cell_id")
        if cid is not None:
            for cell in cells:
                if cell.get("cell_id") == cid:
                    matched.append((cell, None))
                    order_used = "cell_id_fallback"
                    break
    if not matched:
        return [], "no_3d_cell_within_{}km".format(int(max_match_km)), None
    matched.sort(key=lambda item: (item[1] is None, item[1] or 0.0))
    return matched, None, order_used


def derive_morphology(structure, volume_payload, *, ref_rd=None,
                      cfg=None, thr_dbz=MORPH_THRESHOLD_DBZ, prev=None):
    """Campo ``morphology_organization`` per UNA struttura.

    Ritorna sempre un dict serializzabile. ``volume_payload`` e' il payload
    prodotto da ``volume_publisher.build_volume_payload`` (gia' in memoria).

    ``prev`` e' il payload compatto del run precedente (unico blob vivo del
    branch radar-3d) oppure None. Quando c'e', ``volume_growth`` diventa
    calcolabile dal confronto dei VALORI PUBBLICATI delle due celle 3D. Resta
    EVIDENZA, non gate: senza blob precedente e' null con il motivo reale.
    """
    th = thresholds_from_config(cfg)
    if not isinstance(volume_payload, dict) or volume_payload.get("volume_available") is not True:
        reason = (volume_payload or {}).get("null_reason") or NULL_REASON_CAPPI
        return null_field(reason, detail={
            "source": SOURCE,
            "cappi_levels": _cappi_levels(volume_payload),
        })
    cells = volume_payload.get("cells") or []
    matched, reason, order_used = _match_cells(structure, cells, ref_rd,
                                                th["max_match_km"], thr_dbz)
    if reason is not None:
        return null_field(reason, detail={
            "cappi_levels": _cappi_levels(volume_payload),
            "cells_3d": len(cells),
            "volume_timestamp_utc": volume_payload.get("volume_timestamp_utc"),
            "position_order": None,
        })
    # Valori dalla cella 3D piu' vicina; le altre diventano conteggio.
    best, dist = matched[0]
    values = _cell_values(best, thr_dbz)
    et = values["echo_top_m"]
    ve = values["vertical_extent_km"]
    vd = values["vil_density_g_m3"]
    gates = {
        "echo_top_ge_min": (et is not None and et >= th["echo_top_min_m"]),
        "vertical_extent_ge_min": (ve is not None and ve >= th["vertical_extent_min_km"]),
        "vil_density_ge_min": (vd is not None and vd >= th["vil_density_min_g_m3"]),
    }
    # `volume_growth` e' EVIDENZA, non gate: resta null con motivo quando non
    # c'e' volume precedente, ma NON azzera la classificazione. Solo i tre
    # valori che entrano nella regola sono obbligatori.
    _thr_block = (best.get("by_threshold") or {}).get(str(int(thr_dbz))) or {}
    value_null_reasons = {
        "echo_top_m": (best.get("ECHO_TOP_18") or {}).get("null_reason"),
        "vertical_extent_km": (_thr_block.get("VERTICAL_EXTENT") or {}).get("null_reason"),
        "vil_density_g_m3": (best.get("VIL_DENSITY") or {}).get("null_reason"),
        "volume_growth": (_thr_block.get("VOLUME_GROWTH") or {}).get("null_reason"),
    }
    # Growth dal blob compatto precedente (branch radar-3d). Il campo resta
    # EVIDENZA: non entra nei gates e non cambia la classificazione.
    growth_detail = None
    if prev is not None:
        try:
            from . import volume_publisher as vpub
            pixel_km = ((volume_payload.get("grid") or {}).get("pixel_km"))
            gval, greason, growth_detail = vpub.growth_for_cell(
                best, prev, thr_dbz=thr_dbz, pixel_km=pixel_km,
                max_match_km=th["max_match_km"])
            values["volume_growth"] = gval
            value_null_reasons["volume_growth"] = greason
        except Exception as exc:
            values["volume_growth"] = None
            value_null_reasons["volume_growth"] = (
                "growth_error:{}".format(exc.__class__.__name__))
            growth_detail = {"error": exc.__class__.__name__}
    missing = [k for k in ("echo_top_m", "vertical_extent_km", "vil_density_g_m3")
               if values[k] is None]
    if missing:
        classification = None
        supports = False
        null_reason = "missing_3d_values:" + ",".join(sorted(missing))
    else:
        supports = bool(all(gates.values()))
        classification = "organized_vertical" if supports else "not_organized_vertical"
        null_reason = None
    qualities = sorted({_cell_quality(c) for c, _d in matched})
    if "B_vi_interpolated" in qualities:
        quality = "B_vi_interpolated"
    elif qualities and all(q == "A_observed" for q in qualities):
        quality = "A_observed"
    else:
        quality = "C_derived"
    return {
        "classification": classification,
        "supports_organized": supports,
        "null_reason": null_reason,
        "source": SOURCE,
        "valid_time": (volume_payload.get("volume_timestamp_utc")
                       or volume_payload.get("timestamp")),
        "quality": quality,
        "reference": dict(REFERENCE),
        "thresholds": {k: th[k] for k in DEFAULT_THRESHOLDS},
        "gates": gates,
        "values": values,
        "value_null_reasons": value_null_reasons,
        "detail": {
            "growth": growth_detail,
            "growth_source": ("previous_compact_payload" if prev is not None
                              else "current_payload_or_none"),
            "cells_3d_matched": len(matched),
            "match_distance_km": dist,
            "matched_cell_id": best.get("cell_id"),
            "match_method": "centroid_rc_weighted + haversine"
                            if dist is not None else "cell_id fallback",
            "position_order": order_used,
            "volume_available": True,
            "cappi_levels": _cappi_levels(volume_payload),
            "cells_3d": len(cells),
            "thresholds_dbz": volume_payload.get("thresholds_dbz"),
        },
    }


def attach(volume_payload, structures, *, ref_rd=None, cfg=None,
           thr_dbz=MORPH_THRESHOLD_DBZ, prev=None):
    """Aggiunge il campo a OGNI record; non modifica nessun altro campo.

    Ritorna (structures, summary). Le strutture sono modificate IN PLACE
    (stessi oggetti usati da output.py), ma solo la chiave
    ``morphology_organization`` viene scritta. ``prev`` (payload compatto del
    branch radar-3d) e' passato a ogni ``derive_morphology`` per il growth.
    """
    th = thresholds_from_config(cfg)
    summary = {
        "version": MORPHOLOGY_VERSION,
        "source": SOURCE,
        "volume_available": bool(isinstance(volume_payload, dict)
                                 and volume_payload.get("volume_available") is True),
        "structures_total": 0,
        "morphology_present": 0,
        "organized_vertical": 0,
        "not_organized_vertical": 0,
        "null_by_reason": {},
        "valid_time": ((volume_payload or {}).get("volume_timestamp_utc")
                       if isinstance(volume_payload, dict) else None),
        "thresholds": {k: th[k] for k in DEFAULT_THRESHOLDS},
    }
    for rec in structures or []:
        field = derive_morphology(rec, volume_payload, ref_rd=ref_rd,
                                  cfg=cfg, thr_dbz=thr_dbz, prev=prev)
        rec["morphology_organization"] = field
        summary["structures_total"] += 1
        if field["classification"] is not None:
            summary["morphology_present"] += 1
            key = field["classification"]
            summary[key] = summary.get(key, 0) + 1
        else:
            reason = field["null_reason"] or "unknown"
            summary["null_by_reason"][reason] = summary["null_by_reason"].get(reason, 0) + 1
    summary["growth_previous_available"] = bool(
        isinstance(prev, dict) and prev.get("volume_available") is True)
    summary["growth_previous_timestamp_utc"] = (
        ((prev or {}).get("volume_timestamp_utc") or (prev or {}).get("timestamp"))
        if isinstance(prev, dict) else None)
    summary["growth_values_present"] = sum(
        1 for rec in structures or []
        if _finite((rec.get("morphology_organization") or {}).get("values", {}).get(
            "volume_growth")) is not None)
    return structures, summary
