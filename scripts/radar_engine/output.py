#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — output.py

Scrittura ATOMICA degli output derivati (data/radar):
    latest.json            — riepilogo stato
    storms.geojson         — FeatureCollection: celle correnti (Point) + track paths (LineString)
    tracks.json            — dettaglio delle track con motion e classificazione
    storm_objects.geojson  — FeatureCollection: STORM OBJECT layer (Fase 1.7)
    storm_tracks.json      — dettaglio delle storm track (motion + confidenze)
    convective_structures.json — record del descrittore SOI (1.2.4.17)

Garanzie:
  - scrittura su *.tmp nella stessa directory -> json load di validazione ->
    os.replace() (mai file parziali visibili);
  - su errore le versioni precedenti NON vengono sovrascritte;
  - non viene MAI scritto un GeoTIFF/raw in out_dir.
"""

import json
import os
import tempfile

import numpy as np

from . import models
from . import convective_structure as cs_mod
from . import tracking as _tracking

SOURCE_LABEL = "Radar-DPC VMI (GeoTIFF quantitativo)"
# Descrittori di forma/scala pubblicabili (senza watershed, senza classi).
SHAPE_SCHEMA = "meteorisk.shape_descriptors/1"
# Blocco ADDITIVO di eventi di lifecycle del tracking (nessun campo esistente
# viene toccato: v. build_tracking_events).
TRACKING_EVENTS_SCHEMA = "meteorisk.tracking_events/1"
TRACKING_EVENTS_SOURCE = "radar_engine.tracking / radar_engine.storm_tracking"
TRACKING_EVENTS_REFERENCE = (
    "Eventi ricostruiti SOLO da evidenza in-memory: track.events, lineage/"
    "parent_ids/child_ids, tracking_ambiguity, coasting_null_reason, punti "
    "observed=False. Nessun evento viene inferito o inventato: assenza di "
    "evidenza => value=null con null_reason esplicito e timestamp = istante "
    "di valutazione del run (radar_timestamp), non un orario inventato."
)


def _json_default(obj):
    """default= per json.dumps: numpy -> Python nativi, altro -> str.

    Un run normale non lo invoca mai (i valori sono già float/int/str/None);
    interviene solo su oggetti numpy che altrimenti farebbero crashare il
    processo con TypeError (exit 1 senza GITHUB_OUTPUT)."""
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return list(obj)
    return str(obj)


def _atomic_write_text(path, text):
    """Scrive text in modo atomico (tmp nella stessa dir + os.replace)."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    json.loads(text)  # validazione JSON PRIMA della replace
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".engine-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise models.OutputError(f"atomic_write_failed:{exc}") from exc


def _as_of_summary(bundle):
    """1.2.4.20: blocco `as_of_mode` presente SOLO in replay.

    In modalita' live torna {} (nessuna chiave in piu': l'output operativo
    resta byte-identico alla 1.2.4.19)."""
    as_of = getattr(bundle, "as_of_mode", None)
    if not isinstance(as_of, dict):
        return {}
    return {"as_of_mode": as_of}


def _read_previous_latest(out_dir):
    """latest.json del run precedente (None se assente o illeggibile).

    Serve SOLO a trasportare in avanti i campi di degrado di OPERA
    (`last_ok_utc`, copertura persa): non altera nessun altro valore."""
    try:
        with open(os.path.join(out_dir, "latest.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_OPERA_REASON_TEXT = {
    "no_slot_for_frame": ("nessuno slot del composito entro la tolleranza "
                          "fra tutti gli endpoint provati"),
    "partial_slots_or_errors": "slot o letture mancanti su una parte dei frame",
}


def _opera_note(opera):
    """Frase leggibile (pannello) sul degrado OPERA. Solo fatti misurati."""
    status = opera.get("status")
    reason = opera.get("reason") or "motivo_non_dichiarato"
    detail = _OPERA_REASON_TEXT.get(reason, reason)
    tried = opera.get("endpoints_tried") or []
    n_bases = len(tried) if isinstance(tried, (list, tuple)) else 0
    last_ok = opera.get("last_ok_utc")
    lost = opera.get("coverage_lost_km2")
    parts = [f"OPERA {status}: {detail}."]
    if n_bases:
        parts.append(f"Endpoint provati: {n_bases}.")
    if last_ok:
        parts.append(f"Ultimo run con OPERA attivo: {last_ok}.")
    if lost is not None:
        parts.append(f"Copertura non piu' aggiunta: {lost} km2.")
    parts.append("Il mosaico resta alla sola VMI DPC.")
    return " ".join(parts)


def _enrich_opera_status(payload, previous):
    """Rende ESPLICITO il degrado di OPERA in latest.json["opera"].

    Campi aggiunti (tutti additivi, nessun campo esistente cambia):
      `last_ok_utc`           ultimo run con `opera.status == "ok"`;
      `coverage_lost_pixels`  pixel che l'ultimo run OK prendeva da OPERA;
      `coverage_lost_km2`     gli stessi pixel in km^2 (area del pixel della
                              griglia corrente, MISURATA dal transform);
      `coverage_lost_basis`   come e' stato calcolato (`last_ok_frame_pixels`);
      `note`                  frase leggibile per il pannello (solo in degrado).

    In stato "ok" la copertura persa e' 0 per definizione. In degrado i valori
    vengono TRASPORTATI dal run precedente: con OPERA assente la copertura
    corrente non e' misurabile, quindi non si stima e non si inventa."""
    opera = payload.get("opera")
    if not isinstance(opera, dict) or not opera:
        return
    mosaic = payload.get("mosaic") or {}
    pixel_area = mosaic.get("pixel_area_km2")
    prev = (previous or {}).get("opera") or {}
    if opera.get("status") == "ok":
        opera["last_ok_utc"] = payload.get("generated_at")
        opera["coverage_lost_pixels"] = 0
        opera["coverage_lost_km2"] = 0.0
        opera["coverage_lost_basis"] = "none"
        return
    last_ok = prev.get("last_ok_utc")
    lost_px = prev.get("coverage_lost_pixels")
    lost_km2 = prev.get("coverage_lost_km2")
    if prev.get("status") == "ok":
        # Il run precedente era OK: la copertura che oggi non aggiungiamo e'
        # ESATTAMENTE quella che quello prendeva da OPERA (il suo
        # `coverage_lost_pixels` e' 0 per definizione, quindi non si usa).
        last_ok = last_ok or (previous or {}).get("generated_at")
        frames = ((previous or {}).get("mosaic") or {}).get("frames") or []
        px = int(frames[-1].get("pixels_opera") or 0) if frames else 0
        lost_px = px
        if pixel_area is not None:
            lost_km2 = round(px * float(pixel_area), 3)
    opera["last_ok_utc"] = last_ok
    opera["coverage_lost_pixels"] = lost_px
    opera["coverage_lost_km2"] = lost_km2
    opera["coverage_lost_basis"] = (
        "last_ok_frame_pixels" if lost_px is not None else None)
    opera["note"] = _opera_note(opera)


def _doppler_coverage_summary(bundle, previous):
    """Riepilogo dello stato della copertura Doppler per latest.json.

    `opera.status` parla del MOSAICO CIRRUS: NON e' la copertura Doppler.
    Questa nasce dagli SCAN VRADH per sito e vive in un blocco separato, con
    stato esplicito (ok | unavailable | stale), motivo vero ETA' dell'ultima
    geometria valida. Nessuna feature/array di raggi entra in latest.json.
    """
    dop = getattr(bundle, "doppler", None)
    if not isinstance(dop, dict) or not dop:
        return None
    status = dop.get("coverage_status")
    if status not in ("ok", "unavailable", "stale"):
        return None
    prev = (previous or {}).get("doppler_coverage") or {}
    last_ok = prev.get("last_ok_utc")
    if status == "ok":
        last_ok = getattr(bundle, "generated_at_iso", None) or last_ok
    counts = dop.get("coverage_counts") or {}
    note = dop.get("coverage_status_note") or {
        "ok": "Copertura Doppler calcolata dagli scan VRADH correnti.",
        "unavailable": ("Copertura Doppler non disponibile: nessuno scan "
                        "VRADH per sito."),
        "stale": ("Copertura Doppler parzialmente conservata: siti senza "
                  "scan VRADH; la geometria non e' corrente."),
    }.get(status)
    return {
        "coverage_status": status,
        "coverage_null_reason": dop.get("coverage_null_reason"),
        "stale": bool(dop.get("coverage_stale")),
        "last_valid_scan": dop.get("coverage_last_valid_scan"),
        "stale_age_seconds": dop.get("coverage_stale_age_seconds"),
        "last_ok_utc": last_ok,
        "sites_ok": counts.get("sites_current"),
        "sites_stale": counts.get("sites_stale"),
        "polygons": counts.get("polygons"),
        "note": note,
        "scope": ("copertura VRADH per sito (SCAN OPERA): NON il mosaico "
                  "CIRRUS descritto nel blocco 'opera'"),
        "detail_path": "data/radar/doppler_coverage.geojson",
    }


def _continuity_rejections_summary(bundle):
    """Conteggio per-run delle ASSOCIAZIONI FINALI rifiutate dal gate 110 km/h.

    1.3.0.38: la chiave `continuity_rejections` e' retrocompatibile ma il suo
    VALORE cambia base (v1 = coppie (i,j) della matrice di costo, v2 =
    associazioni uno-a-uno post-Hungarian eliminate dal gate). Il token
    `continuity_rejections_basis` dichiara la base: i payload scritti in v1
    vanno rigenerati col prossimo run, non confrontare i valori tra versioni.

    Solo l'intero aggregato: nessun dettaglio dei singoli rigetti entra in
    latest.json. Chiave assente se il bundle non ha ancora le metriche di
    tracking (run parziali / libreria / test).
    """
    tm = getattr(bundle, "tracking_metrics", None)
    if not isinstance(tm, dict) or "continuity_rejections" not in tm:
        return {}
    return {
        "continuity_rejections": int(tm["continuity_rejections"]),
        "continuity_rejections_basis": tm.get(
            "continuity_rejections_basis",
            _tracking.CONTINUITY_REJECTIONS_BASIS),
    }


def _coasting_summary(bundle):
    """Contatori del coasting (2026-11-21): solo interi e motivi, mai dati raw.

    Legge `bundle.tracking_metrics`, che e' gia' l'aggregato cella+storm object.
    Chiave assente se il bundle non ha metriche (run parziale/libreria/test).
    """
    tm = getattr(bundle, "tracking_metrics", None)
    if not isinstance(tm, dict) or "coasted_points" not in tm:
        return {}
    reasons = tm.get("coasting_null_reasons") or {}
    return {
        "coasted_points": int(tm.get("coasted_points", 0) or 0),
        "coasted_tracks_recovered": int(tm.get("coasted_tracks_recovered", 0) or 0),
        "coasting_null_reasons": {str(k): int(v) for k, v in reasons.items()},
    }


def lifecycle_stats(tracks, layer="cell"):
    """Statistiche di evoluzione del run (nascite/morti/fusioni/divisioni).

    Conta le TRACK del layer passato, inclusi i record da 1 punto: il filtro
    `len(points)>=2` di tracks.json/storm_tracks.json non deve rendere
    invisibile una nascita appena avvenuta (per quelle c'e' anche
    `births_latest_frame`).

    `merges`/`splits` contano le RELAZIONI, non le track: si somma
    `len(parent_ids)` sui FIGLI. L'evento e' registrato su entrambi i lati
    (parent.child_ids e child.parent_ids); contare le track raddoppierebbe.
    Una fusione a due genitori vale quindi 2 relazioni, una divisione in due
    figli vale 2 relazioni. E' dichiarato perche' non e' il numero di eventi
    fisici.

    `median_lifetime_min` esclude le track con `continued_from_history=True`:
    la loro vita e' troncata dalla finestra rolling e non e' confrontabile.
    Il numero di track troncate e' pubblicato a parte.
    """
    tracks = list(tracks or [])
    births = {kind: 0 for kind in models.BIRTH_KINDS_PHYSICAL}
    deaths = {kind: 0 for kind in models.DEATH_KINDS}
    merges = splits = reid = truncated = unclassified = 0
    lifetimes = []
    lifetimes_by = {kind: [] for kind in models.BIRTH_KINDS_PHYSICAL}
    for track in tracks:
        events = list(getattr(track, "events", None) or [])
        kind = getattr(track, "birth_kind", None)
        if kind is None and models.EVENT_BIRTH in events:
            # Bundle costruiti a mano / record legacy: stessa regola del
            # tracker, nessun valore nuovo inventato.
            kind, _reason = _tracking.compute_birth_kind(track)
        if kind in births:
            births[kind] += 1
        continued = bool(getattr(track, "continued_from_history", False))
        if continued:
            truncated += 1
        death_kind = getattr(track, "death_kind", None)
        if getattr(track, "status", None) == models.STATUS_DEAD:
            if death_kind in deaths:
                deaths[death_kind] += 1
            else:
                unclassified += 1
        lineage = getattr(track, "lineage", None)
        parent_ids = list(getattr(track, "parent_ids", None) or [])
        if parent_ids:
            if lineage == "merge" or "lineage_merge" in events:
                merges += len(parent_ids)
            elif lineage == "split" or "lineage_split" in events:
                splits += len(parent_ids)
        reid += events.count(_tracking.EVENT_REID_CONTINUATION)
        lifetime = _track_lifetime_min(track)
        if lifetime is not None and not continued:
            lifetimes.append(lifetime)
            if kind in lifetimes_by:
                lifetimes_by[kind].append(lifetime)

    def _median(values):
        return round(float(np.median(values)), 1) if values else None

    return {
        "schema": "meteorisk.lifecycle_stats/1",
        "layer": layer,
        "scope": "all_tracks_including_single_point",
        "counted_tracks": len(tracks),
        "births": births,
        "physical_births_total": sum(births.values()),
        "deaths": deaths,
        "deaths_unclassified": unclassified,
        "merges": merges,
        "splits": splits,
        "relations_basis": "sum(len(child.parent_ids))",
        "reid_stitches": reid,
        "median_lifetime_min": _median(lifetimes),
        "median_lifetime_samples": len(lifetimes),
        "median_lifetime_min_by_birth_kind": {
            kind: _median(values) for kind, values in lifetimes_by.items()},
        "median_lifetime_samples_by_birth_kind": {
            kind: len(values) for kind, values in lifetimes_by.items()},
        "median_lifetime_null_reason": (
            None if lifetimes
            else "no_nontruncated_track_with_two_observed_points"),
        "lifetime_truncated_by_history": truncated,
    }


def _lifecycle_stats_block(bundle):
    """Blocco top-level (layer cella) + dettaglio storm object se presente.

    Il layer cella e' la base di tracks.json; lo storm object e' un layer
    AGGREGATO delle stesse celle, quindi sommarlo al top-level conterebbe due
    volte gli stessi sistemi. Resta un blocco separato e dichiarato.
    """
    stats = lifecycle_stats(getattr(bundle, "tracks", None) or [], "cell")
    storm = lifecycle_stats(getattr(bundle, "storm_tracks", None) or [],
                            "storm_object")
    if storm["counted_tracks"]:
        stats["storm_object"] = storm
    return stats


def _observed_points(track):
    """Punti OSSERVATI (coasted esclusi), in ordine temporale."""
    pts = [p for p in (getattr(track, "points", None) or [])
           if getattr(p, "observed", True)
           and getattr(p, "position_source", None)
           != models.COAST_POSITION_SOURCE]
    return sorted(pts, key=lambda p: (int(getattr(p, "timestamp_ms", 0)),
                                      int(getattr(p, "frame_index", -1))))


def _track_lifetime_min(track):
    """Durata fra primo e ultimo punto OSSERVATO (None se <2 punti)."""
    pts = _observed_points(track)
    if len(pts) < 2:
        return None
    minutes = (pts[-1].timestamp_ms - pts[0].timestamp_ms) / 60000.0
    return minutes if minutes > 0 else None


def _births_latest_frame(bundle):
    """Nascite dell'ULTIMO frame (array leggero, prima del filtro >=2 punti).

    Una track nata sull'ultimo frame ha UN solo punto e non entra in
    tracks.json/storm_tracks.json; senza questo array sarebbe invisibile.
    Inclusi solo i record con `EVENT_BIRTH` (seed e ricuciture escluse).
    """
    frames = getattr(bundle, "cells_by_frame", None) or []
    if not frames:
        return []
    latest = len(frames) - 1
    out = []
    pools = ((getattr(bundle, "tracks", None) or [], "cell"),
             (getattr(bundle, "storm_tracks", None) or [], "storm_object"))
    for tracks, track_type in pools:
        for track in tracks:
            events = list(getattr(track, "events", None) or [])
            if models.EVENT_BIRTH not in events:
                continue
            obs = _observed_points(track)
            if not obs:
                continue
            first = obs[0]
            if int(getattr(first, "frame_index", -1)) != latest:
                continue
            kind = getattr(track, "birth_kind", None)
            if kind is None:
                kind, _reason = _tracking.compute_birth_kind(track)
            lon, lat = first.lonlat
            out.append({
                "track_id": getattr(track, "track_id", None),
                "track_type": track_type,
                "birth_ts": first.timestamp_iso,
                "position": [round(float(lat), 5), round(float(lon), 5)],
                "area_km2": round(float(first.area_km2), 2),
                "max_dbz": round(float(first.max_dbz), 2),
                "birth_kind": kind,
                "physical_birth": (kind != models.BIRTH_KIND_REID_CONTINUATION
                                   if kind is not None else None),
            })
    out.sort(key=lambda rec: (str(rec["track_type"]), int(rec["track_id"])))
    return out


def build_latest(bundle, engine_meta=None, previous=None):
    """latest.json (stato del run, mai dati raw)."""
    payload = {
        "status": bundle.status,                 # ok | degraded | error
        "generated_at": bundle.generated_at_iso,
        "engine": engine_meta or {},
        "source": bundle.source,
        "radar_timestamp": bundle.radar_timestamp_iso,
        "radar_timestamp_ms": bundle.radar_timestamp_ms,
        "data_latency_minutes": bundle.data_latency_minutes,
        "frames_count": len(getattr(bundle, "frame_slots_iso", None)
                            or bundle.frames),
        "cells_count": sum(len(c) for c in bundle.cells_by_frame),
        "tracks_count": len([t for t in bundle.tracks if len(t.points) >= 2]),
        "storm_objects_count": sum(len(o) for o in
                                   getattr(bundle, "storm_objects_by_frame", [])),
        "storm_tracks_count": len([t for t in
                                   getattr(bundle, "storm_tracks", [])
                                   if len(t.points) >= 2]),
        "tracking_confidence": _tracking_confidence(bundle.tracks),
        "structure_tracks_evaluated": getattr(
            bundle, "structure_tracks_evaluated", 0),
        **_continuity_rejections_summary(bundle),
        **_shape_descriptors_summary(bundle),
        **_coasting_summary(bundle),
        **_structure_summary(bundle),
        **_phase2_summary(bundle),
        **_as_of_summary(bundle),
        # 2026-11-24 — evoluzione dei sistemi: nascite/morti/crescita/fusioni.
        # ADDITIVO: nessuna chiave esistente cambia. `births_latest_frame`
        # rende visibili le nascite da 1 punto che i file track filtrano.
        "lifecycle_stats": _lifecycle_stats_block(bundle),
        "births_latest_frame": _births_latest_frame(bundle),
        "warnings": bundle.warnings[-50:],
    }
    # 1.2.4.26 — blocco ADDITIVO `cappi_fetch` (verdetto del gate grandine +
    # livelli CAPPI effettivi). Presente solo quando c'e' un riepilogo da
    # pubblicare: nessuna chiave esistente cambia forma o valore.
    cappi = _cappi_fetch_summary(bundle)
    if cappi is not None:
        payload["cappi_fetch"] = cappi
    # 2026-11-12 — bundle prodotti DPC allineato (VIL/ETM/POH/SRI): status,
    # QC per prodotto (pixel validi/fuori dominio/zeri), mancanti e stale.
    # ADDITIVO: presente solo quando il bundle e' stato costruito.
    product_bundle = _product_bundle_summary(bundle)
    if product_bundle is not None:
        payload["product_bundle"] = product_bundle
    # 1.2.4.26 — modalita' di fetch (additiva): dice quanti frame sono stati
    # scaricati e quanti ricostruiti dall'archivio delle celle. Presente solo
    # quando il percorso incrementale ha davvero riusato qualcosa.
    fetch_mode = getattr(bundle, "fetch_mode", None)
    if isinstance(fetch_mode, dict) and fetch_mode:
        payload["fetch_mode"] = fetch_mode
    # 1.2.4.27 — MOSAICO MULTI-SORGENTE: `sources[]` (provenienza e
    # attribuzione per sorgente effettivamente usata sul frame pubblicato) e
    # `opera` (esito della sorgente OPERA, anche "unavailable" con motivo).
    # Presenti SOLO quando il merge e' stato eseguito: un run a sola sorgente
    # DPC resta byte-identico.
    sources = getattr(bundle, "sources", None)
    if sources:
        payload["sources"] = sources
    opera = getattr(bundle, "opera_status", None)
    if isinstance(opera, dict) and opera:
        payload["opera"] = opera
    mosaic = getattr(bundle, "mosaic_summary", None)
    if isinstance(mosaic, dict) and mosaic:
        payload["mosaic"] = {
            "status": mosaic.get("status"),
            "reason": mosaic.get("reason"),
            "slots_resolved": mosaic.get("slots_resolved"),
            "frames_merged": mosaic.get("frames_merged"),
            "seconds": mosaic.get("seconds"),
            "bytes_read": mosaic.get("bytes_read"),
            "bytes_file": mosaic.get("bytes_file"),
            "range_requests": mosaic.get("range_requests"),
            "endpoint": mosaic.get("endpoint"),
            "pixel_area_km2": mosaic.get("pixel_area_km2"),
            "mixed_cells": getattr(bundle, "mosaic_mixed_cells", None),
            "frames": [{k: f.get(k) for k in (
                "time_iso", "pixels_dpc", "pixels_opera", "pct_dpc",
                "pct_opera", "seam_pixels", "substituted",
                "substitute_reason", "opera_time_iso")}
                for f in (mosaic.get("frames") or [])],
        }
    # Degrado OPERA ESPLICITO: `last_ok_utc`, copertura persa e `note` leggibile
    # per il pannello. Va DOPO il blocco `mosaic` (gli serve `pixel_area_km2`).
    if "opera" in payload:
        _enrich_opera_status(payload, previous)
    # 2026-11-12 — TEMPI PER STADIO (additivo): `{unit, total, stages}` con
    # `stages[name] = {ms, calls, pct, parents}` ordinati per ms decrescenti.
    # Presente solo quando il motore ha misurato (bundle.timings_ms assente ->
    # nessuna chiave nuova, output identico a prima).
    timings = getattr(bundle, "timings_ms", None)
    if isinstance(timings, dict) and timings:
        payload["timings_ms"] = timings
    # 1.3 — DOPPLER ANALYSIS + MESOCYCLONE TRACKING (additivi). `latest.json`
    # resta lo "stato del run, mai dati raw": qui va SOLO un riepilogo limitato
    # (conteggi, motivi, limiti, riferimento), mentre i payload COMPLETI sono
    # pubblicati in data/radar/doppler_analysis.json e mesocyclone_tracks.json.
    # Nessuna chiave viene pubblicata se il layer non ha valorizzato i campi.
    da = getattr(bundle, "doppler_analysis", None)
    if isinstance(da, dict) and da:
        payload["doppler_analysis"] = {
            "status": "unavailable" if da.get("null_reason") else "ok",
            "source": da.get("source"),
            "valid_time": da.get("valid_time"),
            "couplets_total": da.get("couplets_total"),
            "signature_total": da.get("signature_total"),
            "state_counts": da.get("state_counts"),
            "null_reasons": da.get("null_reasons"),
            "limits": da.get("limits"),
            "reference": da.get("reference"),
            "boundary_note": da.get("boundary_note"),
            "detail_path": "data/radar/doppler_analysis.json",
        }
    mt = getattr(bundle, "mesocyclone_tracks", None)
    if isinstance(mt, dict) and mt:
        payload["mesocyclone_tracks"] = {
            "status": "unavailable" if mt.get("null_reason") else "ok",
            "source": mt.get("source"),
            "counts": mt.get("counts"),
            "limits": mt.get("limits"),
            "reference": mt.get("reference"),
            "detail_path": "data/radar/mesocyclone_tracks.json",
        }
    # 1.3 — VOLUME 3D: riepilogo verificabile del blob data/radar/3d/volume.json
    # (payload completo, nessun dato grezzo). Vive SOLO sul branch orphan
    # radar-3d: mai su main, mai nell'artefatto Pages.
    vol = getattr(bundle, "volume", None)
    if isinstance(vol, dict) and vol:
        payload["volume"] = {
            "status": vol.get("status") or (
                "ok" if vol.get("volume_available") else "unavailable"),
            "null_reason": vol.get("null_reason"),
            "generated_at_utc": vol.get("generated_at_utc"),
            "timestamp": vol.get("timestamp"),
            "volume_source": vol.get("volume_source"),
            "volume_acquisition": vol.get("volume_acquisition"),
            "cells_count": len(vol.get("cells") or []),
            # 17/11/2026 (A7): MAI il path assoluto del runner. Il blob vivo
            # e' data/radar/3d/volume.json sul branch radar-3d (mai su main).
            # Se il volume non e' stato pubblicato resta null.
            "path": ("data/radar/3d/volume.json"
                     if getattr(bundle, "volume_path", None) else None),
            "branch": "radar-3d",
            "blob": "data/radar/3d/volume.json",
            "growth": (dict(vol["growth"])
                       if isinstance(vol.get("growth"), dict) else None),
        }
    # 16/11/2026 — COPERTURA DOPPLER: stato esplicito, separato da `opera`
    # (che descrive il mosaico CIRRUS, non gli SCAN VRADH per sito).
    covsum = _doppler_coverage_summary(bundle, previous)
    if covsum is not None:
        payload["doppler_coverage"] = covsum
    # 2026-11-21 — riepilogo TASSONOMIA (additivo): presente SOLO se il layer
    # e' stato eseguito (config taxonomy.enabled=True). Nessuna chiave
    # esistente cambia; il payload completo sta nei record di
    # convective_structures.json sotto `structures[*].taxonomy`.
    tax = getattr(bundle, "taxonomy_summary", None)
    if isinstance(tax, dict) and tax:
        payload["taxonomy"] = {
            "schema": tax.get("schema"),
            "version": tax.get("version"),
            "structures_total": tax.get("structures_total"),
            "echo_regime_counts": tax.get("echo_regime_counts"),
            "organization_mode_counts": tax.get("organization_mode_counts"),
            "system_scale_counts": tax.get("system_scale_counts"),
            "morphology_tag_counts": tax.get("morphology_tag_counts"),
            "evolution_tag_counts": tax.get("evolution_tag_counts"),
            "unknown_rate_echo_regime": tax.get("unknown_rate_echo_regime"),
            "unknown_rate_all_axes": tax.get("unknown_rate_all_axes"),
            "confirmed_count": tax.get("confirmed_count"),
            "nwp_influence": tax.get("nwp_influence"),
            "rule_support_is_probability": tax.get("rule_support_is_probability"),
        }
    return payload


def _cappi_fetch_summary(bundle):
    """Riepilogo verificabile dei CAPPI per latest.json (1.2.4.26).

    Perche' esiste: il verdetto del gate vive in bundle.phase2["cappi_fetch"],
    ma build_latest non lo serializzava, quindi dal pubblicato non si poteva
    controllare se il set completo era stato scaricato ne' quanti livelli
    fossero realmente disponibili. Si espone SOLO il riepilogo: il dettaglio
    per prodotto (`products`, ~4 kB con 10 CAPPI) resta in memoria, perche'
    latest.json e' committato a ogni run e non aggiunge nulla alla verifica
    del verdetto.

    `cappi_levels` = livelli effettivi dopo gate e scarti (severe_stack usa
    `cappi_levels<4` per dichiarare MESH non calcolabile).
    `hail_gate.value_g_m3` = massimo di VIL density sulla griglia
    (= `max_vil_density_g_m3` di main._hail_plausibility_gate)."""
    p2 = getattr(bundle, "phase2", None)
    if not isinstance(p2, dict):
        return None
    cf = p2.get("cappi_fetch")
    if not isinstance(cf, dict) or not cf:
        return None
    out = {
        "requested": cf.get("requested"),
        "fetched": cf.get("fetched"),
        "fallback": cf.get("fallback"),
        "failed": cf.get("failed") or [],
        "used_slots": cf.get("used_slots") or [],
        "homogeneous": cf.get("homogeneous"),
        "bytes": cf.get("bytes"),
        "seconds": cf.get("seconds"),
        "ref_time_iso": cf.get("ref_time_iso"),
        "kept_slot_iso": cf.get("kept_slot_iso"),
        "slot_offset_s": cf.get("slot_offset_s"),
        "cappi_levels": cf.get("cappi_levels"),
    }
    gate = cf.get("hail_gate")
    if isinstance(gate, dict):
        out["hail_gate"] = {
            "evaluated": gate.get("evaluated"),
            "value_g_m3": gate.get("max_vil_density_g_m3"),
            "threshold_g_m3": gate.get("threshold_g_m3"),
            "threshold_ref": gate.get("threshold_ref"),
            "passed": gate.get("fetch_full_set"),
            "reason": gate.get("reason"),
        }
        # 1.3-VAL3D: verdetto del gate che si sarebbe applicato senza il flag
        # (serve a dichiarare il baseline 2-CAPPI sugli stessi casi).
        counter = gate.get("validation_counterfactual")
        if isinstance(counter, dict):
            out["hail_gate"]["validation_counterfactual"] = {
                "evaluated": counter.get("evaluated"),
                "value_g_m3": counter.get("max_vil_density_g_m3"),
                "passed": counter.get("fetch_full_set"),
                "reason": counter.get("reason"),
            }
    # 1.3-CAPPI-SELETTIVO: chiave NUOVA SOLO quando il trigger e' OPT-IN
    # esplicitamente abilitato. Con default (disabilitato) latest.json resta
    # byte-identico al baseline. Ogni campo pubblicato porta value,
    # null_reason, source e reference (nessun dato inventato, nessun buco
    # silenzioso).
    ct = cf.get("candidate_trigger")
    if isinstance(ct, dict) and ct.get("enabled"):
        out["cappi_mode"] = cf.get("cappi_mode")
        out["candidate_cappi_trigger"] = {
            "value": ct.get("value"),
            "null_reason": ct.get("null_reason"),
            "source": ct.get("source"),
            "reference": ct.get("reference"),
            "threshold_soi": ct.get("threshold_soi"),
            "max_soi": ct.get("max_soi"),
            "levels_km": ct.get("levels_km"),
        }
    # 1.3-VAL3D: chiavi NUOVE SOLO in modalita' validazione. I run normali
    # restano byte-identici; un run di raccolta e' riconoscibile dal
    # pubblicato e non puo' essere scambiato per produzione operativa.
    v3d = cf.get("validate_3d")
    if isinstance(v3d, dict):
        out["validate_3d"] = dict(v3d)
        out["validation_mode"] = True
    return out


def _product_bundle_summary(bundle):
    """Riepilogo del bundle DPC allineato (VIL/ETM/POH/SRI) per latest.json.

    ADDITIVO: presente solo quando bundle.dpc_bundle esiste. Il dettaglio per
    slot/prodotto e' in bundle.dpc_bundle["qc"] (conteggi pixel validi/fuori
    dominio, mancanti, stale); qui si pubblica il riepilogo compatto."""
    db = getattr(bundle, "dpc_bundle", None)
    if not isinstance(db, dict) or not db:
        return None
    qc = db.get("qc") or {}
    return {
        "status": db.get("status"),
        "products": db.get("products"),
        "slots": db.get("slots"),
        "reference_slot": db.get("reference_slot_iso"),
        "stale_after_s": db.get("stale_after_s"),
        "bytes": db.get("bytes"),
        "seconds": db.get("seconds"),
        "missing": qc.get("missing") or {},
        "stale": qc.get("stale") or [],
        "not_configured": qc.get("not_configured") or [],
        "reference_missing_slots": qc.get("reference_missing_slots") or [],
        "products_qc": {
            name: {
                "product_type": p.get("product_type"),
                "frames_expected": p.get("frames_expected"),
                "frames_present": p.get("frames_present"),
                "frames_stale": p.get("frames_stale"),
                "valid_pixels_total": p.get("valid_pixels_total"),
                "outside_domain_pixels_total":
                    p.get("outside_domain_pixels_total"),
                "zero_pixels_total": p.get("zero_pixels_total"),
                "no_echo_pixels_total": p.get("no_echo_pixels_total"),
            }
            for name, p in (qc.get("products") or {}).items()
        },
        "domain_alignment": qc.get("domain_alignment") or {},
    }


def _tracking_confidence(tracks):
    full = sum(1 for t in tracks if t.tracking_confidence == "full")
    low = sum(1 for t in tracks if t.tracking_confidence == "low")
    if not tracks:
        return "no_tracks"
    if full == len(tracks):
        return "full"
    if low:
        return "low"
    return "insufficient"


def _structure_summary(bundle):
    """Riepilogo dei record del descrittore SOI per latest.json.

    Conta le strutture PUBBLICATE (quelle che entrano in
    convective_structures.json) e quelle scartate per track non coerente
    (`skipped_incoherent`, 2026-11-11): il numero pubblicato resta
    `structure_candidates`, il file pubblicato resta la fonte di verita'."""
    cells, skipped_incoherent = cs_mod.split_publishable(
        getattr(bundle, "convective_structures", None) or [])
    # 1.3 — EVIDENZA VOLUMETRICA: il dato e' DENTRO i record (campo
    # `morphology_organization`), qui si pubblica solo il RIEPILOGO (nessun
    # dato raw/griglia in latest.json). I conteggi arrivano dal layer che gira
    # in main.py dopo phase2; se il layer non e' girato restano a 0/None.
    morph = getattr(bundle, "morphology_summary", None) or {}
    morph_summary = {
        "morphology_volume_available": morph.get("volume_available"),
        "morphology_present": int(morph.get("morphology_present") or 0),
        "morphology_organized_vertical": int(morph.get("organized_vertical") or 0),
        "morphology_null_reasons": dict(morph.get("null_by_reason") or {}),
    }
    if not cells:
        return {
            "structure_candidates": 0,
            "structure_births": 0,
            "structure_on_latest": 0,
            "soi_max": None,
            "skipped_incoherent": skipped_incoherent,
            **morph_summary,
            **_advanced_summary(bundle),
        }
    births = sum(1 for c in cells if c.get("phase") == "birth")
    on_latest = sum(1 for c in cells if c.get("on_latest_frame"))
    soi_max = max((float(c.get("soi", 0)) for c in cells), default=None)
    return {
        "structure_candidates": len(cells),
        "structure_births": births,
        "structure_on_latest": on_latest,
        "soi_max": soi_max,
        "skipped_incoherent": skipped_incoherent,
        **morph_summary,
        **_advanced_summary(bundle),
    }


def _advanced_summary(bundle):
    """FASE 7: conteggi verificabili per latest.json (nessun dato raw).

    I payload COMPLETI dei 5 campi vivono nei record di
    convective_structures.json; qui vanno SOLO i conteggi per categoria e i
    motivi di null. Nessuna chiave se il layer non e' girato (bundle pristino).
    """
    adv = getattr(bundle, "advanced_summary", None) or {}
    if not isinstance(adv, dict) or not adv:
        return {}
    return {
        "advanced_morphology_class_counts": dict(
            adv.get("morphology_class_counts") or {}),
        "advanced_morphology_class_null": int(
            adv.get("morphology_class_null") or 0),
        "advanced_morphology_null_reasons": dict(
            adv.get("morphology_null_reasons") or {}),
        "advanced_lifecycle_stage_counts": dict(
            adv.get("lifecycle_stage_counts") or {}),
        "advanced_lifecycle_null": int(adv.get("lifecycle_null") or 0),
        "advanced_lifecycle_null_reasons": dict(
            adv.get("lifecycle_null_reasons") or {}),
        "advanced_tracking_phase_counts": dict(
            adv.get("tracking_phase_counts") or {}),
        "advanced_tracking_phase_null": int(
            adv.get("tracking_phase_null") or 0),
        "advanced_tracking_phase_null_reasons": dict(
            adv.get("tracking_phase_null_reasons") or {}),
        "advanced_bwer_candidate_true": int(adv.get("bwer_candidate_true") or 0),
        "advanced_overhang_candidate_true": int(
            adv.get("overhang_candidate_true") or 0),
        "advanced_hail_nuclei_tracks_total": int(
            adv.get("hail_nuclei_tracks_total") or 0),
        "advanced_rotation_evidence_available": bool(
            adv.get("rotation_evidence_available")),
        "advanced_supercell_null_reason": adv.get("supercell_null_reason"),
        "advanced_cappi_levels": adv.get("cappi_levels"),
    }


def _shape_descriptors_summary(bundle):
    """Conteggio verificabile dei descrittori di forma pubblicati (ADDITIVO).

    Presente SOLO se almeno un record di convective_structures porta il blocco
    `shape_descriptors`: un run pristino resta byte-identico ({} -> nessuna
    chiave). Nessun dato grezzo (griglie/maschere) entra in latest.json."""
    cells = getattr(bundle, "convective_structures", None) or []
    blocks = [c.get("shape_descriptors") for c in cells
              if isinstance(c.get("shape_descriptors"), dict)]
    if not blocks:
        return {}
    components = sum(len(b.get("components") or []) for b in blocks)
    return {
        "shape_schema": SHAPE_SCHEMA,
        "shape_descriptors_present": len(blocks),
        "shape_descriptors_components": int(components),
    }


def _phase2_summary(bundle):
    """Riepilogo Fase 2 per latest.json (layer additivo, nessun dato raw).

    Da B2 (PHASE2_VERSION 0.4.0) i sub-valori hook/struttura/env/fulmini sono
    PER CANDIDATO: qui si emette il riepilogo del candidato con soi_v2 piu'
    alto (o il primo candidato se soi_v2 e' nullo ovunque) più il conteggio
    dei candidati con componente presente (components_status, visto anche nel
    blocco phase2 di convective_structures.json). Nessun dato raw (celle,
    lat/lon, griglie) entra nel latest.json."""
    p2 = getattr(bundle, "phase2", None)
    if not isinstance(p2, dict):
        return {"phase2_status": "unavailable"}
    cells = getattr(bundle, "convective_structures", None) or []
    comps = p2.get("components_status")
    if not isinstance(comps, dict):
        comps = {name: sum(1 for c in cells if c.get(name) is not None)
                 for name in ("hook", "structure", "env", "ot", "lightning")}
    best = None
    for c in cells:
        if c.get("soi_v2") is None:
            continue
        if best is None or float(c["soi_v2"]) > float(best["soi_v2"]):
            best = c
    if best is None and cells:
        best = cells[0]
    out = {
        "phase2_status": p2.get("status", "unavailable"),
        "phase2_soi_v2_max": max(
            (float(c["soi_v2"]) for c in cells
             if c.get("soi_v2") is not None), default=None),
        "phase2_components_status": dict(comps),
    }
    if best is not None:
        out["phase2_best_candidate"] = {
            "soi_v2": best.get("soi_v2"),
            "hook": best.get("hook"),
            "structure": best.get("structure"),
            "env": best.get("env"),
            "ot": best.get("ot"),
            "lightning": best.get("lightning"),
        }
    return out


def build_storms_geojson(bundle):
    """FeatureCollection: celle dell'ultimo frame valido + tracciati track."""
    features = []
    if bundle.cells_by_frame:
        for cell in bundle.cells_by_frame[-1]:
            track_id = None
            for trk in bundle.tracks:
                if any(p.cell_id == cell.cell_id for p in trk.points):
                    track_id = trk.track_id
                    break
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [cell.centroid_lonlat[0], cell.centroid_lonlat[1]],
                },
                "properties": {
                    **cell.to_dict(),
                    "track_id": track_id,
                    "source": SOURCE_LABEL,
                },
            })
    for trk in bundle.tracks:
        if len(trk.points) < 2:
            continue
        coords = [[p.lonlat[0], p.lonlat[1]] for p in trk.points]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "feature_type": "track_path",
                "track_id": trk.track_id,
                "organization_score": trk.motion.get("organization_score"),
                "classification": trk.motion.get("classification"),
                "source": SOURCE_LABEL,
            },
        })
    return {
        "type": "FeatureCollection",
        "generated_at": bundle.generated_at_iso,
        "radar_timestamp": bundle.radar_timestamp_iso,
        "features": features,
    }


def build_tracking_events(track, radar_timestamp_iso=None):
    """Blocco ADDITIVO `tracking_events/1` per una singola track.

    Ogni evento ha `timestamp`, `type`, `value`, `null_reason`, `source`,
    `reference`. Il valore viene SOLO da evidenza in-memory (nessuna
    inferenza): se il run non contiene evidenza per un tipo, il valore e'
    null con un null_reason esplicito. Il timestamp di un evento ASSENTE e'
    l'istante di valutazione del run (`radar_timestamp_iso`), NON un orario
    inventato per un evento che non e' mai accaduto.

    I campi esistenti della track NON vengono modificati: il chiamante
    aggiunge il blocco al dizionario gia' prodotto da `Track.to_dict()`.
    """
    events = list(getattr(track, "events", None) or [])
    points = list(getattr(track, "points", None) or [])
    first = points[0] if points else None
    last = points[-1] if points else None
    birth_ts = getattr(first, "timestamp_iso", None) or radar_timestamp_iso
    death_ms = getattr(track, "death_ms", None)
    death_ts = (getattr(last, "timestamp_iso", None) if death_ms is not None
                else None) or radar_timestamp_iso
    lineage = getattr(track, "lineage", None)
    parent_ids = list(getattr(track, "parent_ids", None) or [])
    child_ids = list(getattr(track, "child_ids", None) or [])
    coasted = [p for p in points
               if getattr(p, "position_source", None) == "coasted"
               and getattr(p, "observed", True) is False]
    ambiguity = getattr(track, "tracking_ambiguity", None)

    def ev(kind, value, null_reason, timestamp):
        return {
            "type": kind,
            "timestamp": timestamp,
            "value": value,
            "null_reason": null_reason,
            "source": TRACKING_EVENTS_SOURCE,
            "reference": TRACKING_EVENTS_REFERENCE,
        }

    out = []
    # nascita: SOLO l'evento esplicito. Avere un punto NON e' una nascita:
    # un seed d'archivio (frame_index=-1) e una track ricucita continuano
    # un'identita' gia' esistente. Il vecchio `or (first is not None)`
    # pubblicava birth su 22/22 track contro 11/22 nascite reali.
    if models.EVENT_BIRTH in events:
        birth_kind = getattr(track, "birth_kind", None)
        physical_birth = getattr(track, "physical_birth", None)
        if birth_kind is None:
            # Bundle legacy/costruiti a mano: stessa regola del tracker.
            birth_kind, _reason = _tracking.compute_birth_kind(track)
            physical_birth = (birth_kind != models.BIRTH_KIND_REID_CONTINUATION
                              if birth_kind is not None else None)
        out.append(ev("birth",
                      {"track_id": getattr(track, "track_id", None),
                       "frame_index": getattr(first, "frame_index", None),
                       "birth_kind": birth_kind,
                       "physical_birth": physical_birth},
                      None, birth_ts))
    else:
        out.append(ev("birth", None, "not_born_in_this_run", radar_timestamp_iso))
    # prosecuzione (continuazione stessa run o cross-run)
    if lineage == "continuation" or "continued_from_history" in events:
        out.append(ev("continuation",
                      {"continued_from_history":
                       bool(getattr(track, "continued_from_history", False)),
                       "history_uid": getattr(track, "history_uid", None)},
                      None, birth_ts))
    else:
        out.append(ev("continuation", None, "no_continuation_evidence_in_run",
                      radar_timestamp_iso))
    # morte
    if ((models.EVENT_DEATH in events
         or getattr(track, "status", None) == models.STATUS_DEAD)
            and death_ms is not None):
        out.append(ev("death",
                      {"death_ms": int(death_ms),
                       "death_kind": getattr(track, "death_kind", None)},
                      None, death_ts))
    else:
        out.append(ev("death", None, "no_death_observed_track_still_active",
                      radar_timestamp_iso))
    # mancata osservazione temporanea (coasting = punto STIMATO)
    if coasted:
        coast_reason = getattr(track, "coasting_null_reason", None)
        out.append(ev("temporary_non_observation",
                      {"coasted_points": len(coasted),
                       "position_source": "coasted", "observed": False,
                       "coasting_null_reason": coast_reason},
                      None, getattr(coasted[-1], "timestamp_iso", None) or radar_timestamp_iso))
    else:
        out.append(ev("temporary_non_observation", None,
                      "no_coasted_point_in_run", radar_timestamp_iso))
    # split
    if lineage == "split" or any("lineage_split" == e for e in events):
        out.append(ev("split", {"child_ids": child_ids,
                                "parent_ids": parent_ids}, None, birth_ts))
    else:
        out.append(ev("split", None, "no_split_evidence_in_run", radar_timestamp_iso))
    # merge
    if lineage == "merge" or any("lineage_merge" == e for e in events):
        out.append(ev("merge", {"parent_ids": parent_ids,
                                "child_ids": child_ids}, None, birth_ts))
    else:
        out.append(ev("merge", None, "no_merge_evidence_in_run", radar_timestamp_iso))
    # associazione ambigua
    if models.EVENT_AMBIGUOUS in events or ambiguity is not None:
        out.append(ev("ambiguous_association",
                      {"tracking_ambiguity": ambiguity,
                       "ambiguity_reason": getattr(track, "ambiguity_reason", None)},
                      None, birth_ts))
    else:
        out.append(ev("ambiguous_association", None,
                      "no_ambiguous_association_evidence_in_run",
                      radar_timestamp_iso))
    return {"schema": TRACKING_EVENTS_SCHEMA, "events": out}


def _with_tracking_events(track_dict, track, radar_timestamp_iso):
    """Aggiunge il blocco additivo senza toccare nessun campo esistente."""
    track_dict["tracking_events"] = build_tracking_events(track, radar_timestamp_iso)
    return track_dict


def build_tracks(bundle):
    tracks = [t for t in bundle.tracks if len(t.points) >= 2]
    return {
        "generated_at": bundle.generated_at_iso,
        "radar_timestamp": bundle.radar_timestamp_iso,
        "status": bundle.status,
        "tracks": [_with_tracking_events(t.to_dict(), t, bundle.radar_timestamp_iso)
                   for t in tracks],
    }


def build_storm_objects_geojson(bundle):
    """FeatureCollection dello storm object layer (Fase 1.7).

    Geometry = point del centroide pesato; props includono envelope (bbox),
    convex hull (opzionale), cell_count, area, intensità, motion confidence e
    tracking_ambiguity. storms.geojson (cell-level) NON viene modificato."""
    features = []
    objs_by_frame = getattr(bundle, "storm_objects_by_frame", [])
    if objs_by_frame:
        for obj in objs_by_frame[-1]:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [obj.centroid_lonlat[0],
                                    obj.centroid_lonlat[1]],
                },
                "properties": {
                    **obj.to_dict(),
                    "envelope": {
                        "west": obj.bbox_lonlat[0],
                        "south": obj.bbox_lonlat[1],
                        "east": obj.bbox_lonlat[2],
                        "north": obj.bbox_lonlat[3],
                    },
                    "source": SOURCE_LABEL,
                },
            })
    for trk in getattr(bundle, "storm_tracks", []):
        if len(trk.points) < 2:
            continue
        coords = [[p.lonlat[0], p.lonlat[1]] for p in trk.points]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "feature_type": "storm_track_path",
                "track_id": trk.track_id,
                "motion_confidence": trk.motion_confidence,
                "tracking_ambiguity": trk.tracking_ambiguity,
                "organization_score": trk.motion.get("organization_score"),
                "classification": trk.motion.get("classification"),
                "mean_cell_count": trk.motion.get("mean_cell_count"),
                "source": SOURCE_LABEL,
            },
        })
    return {
        "type": "FeatureCollection",
        "generated_at": bundle.generated_at_iso,
        "radar_timestamp": bundle.radar_timestamp_iso,
        "features": features,
    }


def build_storm_tracks(bundle):
    tracks = [t for t in getattr(bundle, "storm_tracks", [])
              if len(t.points) >= 2]
    return {
        "generated_at": bundle.generated_at_iso,
        "radar_timestamp": bundle.radar_timestamp_iso,
        "status": bundle.status,
        "tracks": [_with_tracking_events(t.to_dict(), t, bundle.radar_timestamp_iso)
                   for t in tracks],
    }


def _forecast_null_reason_counts(cells):
    """Conteggio dei motivi di `forecast: null` (audit del gate sul moto).

    Solo record pubblicati; un null senza motivo (chiamanti storici che
    costruiscono il payload a mano) finisce sotto `unknown`."""
    counts = {}
    for cell in cells:
        if cell.get("forecast") is not None:
            continue
        reason = cell.get("forecast_null_reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _morphology_null_reason_counts(cells):
    """Conteggio dei motivi di `morphology_organization: null`.

    Un record senza il campo (chiamanti storici / layer non girato) finisce
    sotto `field_absent`: non viene confuso con un volume non disponibile."""
    counts = {}
    for cell in cells:
        field = cell.get("morphology_organization")
        if not isinstance(field, dict):
            counts["field_absent"] = counts.get("field_absent", 0) + 1
            continue
        if field.get("classification") is not None:
            continue
        reason = field.get("null_reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _cone_shape_counts(cells):
    """Conteggio delle strutture per `forecast.cone_shape` (PARTIZIONE esatta).

    `cone_shape` vale "dilated_outline" (cono dilatato dal contorno, quello
    emesso quando il contorno e' disponibile) oppure "ellipse" (fallback
    ellittico). Il record senza cono (`forecast: null`) o con un blocco privo
    del campo finisce in "missing": i quattro conteggi sommano SEMPRE al numero
    di strutture pubblicate, anche quando il cono dilatato e' la forma di tutte
    (con il solo conteggio di "ellipse" il summary pubblicava 0)."""
    counts = {"dilated_outline": 0, "ellipse": 0, "other": 0, "missing": 0}
    for cell in cells:
        block = cell.get("forecast")
        shape = block.get("cone_shape") if isinstance(block, dict) else None
        if shape == "dilated_outline":
            counts["dilated_outline"] += 1
        elif shape == "ellipse":
            counts["ellipse"] += 1
        elif shape:
            counts["other"] += 1
        else:
            counts["missing"] += 1
    return counts


def build_convective_structures(bundle, engine_meta=None):
    """convective_structures.json — record del descrittore SOI.

    Solo track con candidate=True (SOI + gate di intensità + frame minimi),
    DEDUP per sistema (un record per storm object + finestra temporale).
    Nessun claim Doppler: ogni record porta rotation_measurable=false con la
    motivazione, e la nota data_limits è inclusa sempre nel payload.

    PUBBLICAZIONE (2026-11-11): una struttura con `track_coherent=False` non
    entra in `structures` (moto non determinabile = rumore nel pannello).
    Resta in `bundle.convective_structures` e quindi in
    tracks.json/storm_tracks.json per l'audit; il numero scartato e' sempre
    dichiarato in `summary.skipped_incoherent`. Se tutte le strutture vengono
    scartate il payload resta valido con `structures: []` (mai un 404).

    CONO (2026-11-11): i conteggi per forma sono una PARTIZIONE esatta delle
    strutture pubblicate: `summary.cone_dilated_outline` (cono dilatato dal
    contorno), `summary.cone_ellipse` (fallback ellittico, mantenuto per
    retrocompatibilita' con i consumatori esistenti), `summary.cone_other`
    (altra forma dichiarata) e `summary.cone_shape_missing` (record senza
    `cone_shape`: forecast assente o blocco privo del campo). La somma dei
    quattro e' SEMPRE `summary.structures`. `summary.forecast_null` conta
    quelle senza forecast e `summary.forecast_null_reasons` il motivo per
    ciascuna: dal file pubblicato si vede quante strutture hanno un cono, di
    che forma, e perche' quelle che non ce l'hanno non ce l'hanno."""
    all_cells = getattr(bundle, "convective_structures", None) or []
    cells, skipped_incoherent = cs_mod.split_publishable(all_cells)
    # 2026-11-21 — contesto NWP SEPARATO dall'osservazione radar.
    # I due campi sono gia' calcolati in main.py con severe_stack.field(): qui
    # vengono solo pubblicati. Nessun campo esistente viene toccato e lo schema
    # e' versionato: assenza => null con motivo (mai un valore inventato).
    for c in cells:
        c.setdefault("context_schema", "meteorisk.radar_nwp_context/1")
        c.setdefault("nwp_environment_context", {
            "value": None,
            "null_reason": "context_not_computed",
            "source": "radar_engine.main",
            "reference": "meteorisk.radar_nwp_context/1",
        })
        c.setdefault("radar_observed_class", {
            "value": None,
            "null_reason": "observation_class_not_computed",
            "source": "radar_engine.main",
            "reference": "VMI DPC 2D — no radial velocity",
        })
        # Descrittori di forma/scala: schema versionato SOLO se il blocco e'
        # presente (campo aggiunto da main.py quando publish_descriptors=True).
        # Nessun campo esistente viene rimosso o modificato.
        if "shape_descriptors" in c:
            c.setdefault("shape_schema", SHAPE_SCHEMA)
    cone_shapes = _cone_shape_counts(cells)
    summary = {
        "tracks_evaluated": getattr(bundle, "structure_tracks_evaluated", 0),
        "structures": len(cells),
        "skipped_incoherent": skipped_incoherent,
        "births": sum(1 for c in cells if c.get("phase") == "birth"),
        "organized": sum(1 for c in cells if c.get("level") == "organized"),
        "strong": sum(1 for c in cells if c.get("level") == "strong"),
        "intense": sum(1 for c in cells if c.get("level") == "intense"),
        "on_latest_frame": sum(1 for c in cells if c.get("on_latest_frame")),
        "soi_max": max((float(c.get("soi", 0)) for c in cells), default=None),
        "soi_v2_max": max((float(c["soi_v2"]) for c in cells
                           if c.get("soi_v2") is not None), default=None),
        "merged": sum(1 for c in cells
                      if len(c.get("merged_from") or []) > 1),
        "cone_dilated_outline": cone_shapes["dilated_outline"],
        "cone_ellipse": cone_shapes["ellipse"],
        "cone_other": cone_shapes["other"],
        "cone_shape_missing": cone_shapes["missing"],
        "forecast_null": sum(1 for c in cells if c.get("forecast") is None),
        "forecast_null_reasons": _forecast_null_reason_counts(cells),
        # 2026-11-12 — copertura delle feature SRI/POH pubblicate per
        # struttura: sono i numeri che il report usa per "prima/dopo".
        "rain_sri_present": sum(
            1 for c in cells
            if ((c.get("rain") or {}).get("sri_max_mmh") is not None)),
        "rain_accum_present": sum(
            1 for c in cells
            if ((c.get("rain") or {}).get("accum_30min_mm") is not None)),
        "hail_poh_present": sum(
            1 for c in cells
            if ((c.get("hail") or {}).get("poh_max") is not None)),
        # 1.3 — copertura dell'EVIDENZA volumetrica nei record pubblicati.
        # Il campo completo e' in `structures[*].morphology_organization`; qui
        # solo i conteggi verificabili (confronto diretto con il summary del
        # layer morphology e con latest.json).
        "morphology_present": sum(
            1 for c in cells
            if (c.get("morphology_organization") or {}).get("classification")
            is not None),
        "morphology_organized_vertical": sum(
            1 for c in cells
            if (c.get("morphology_organization") or {}).get("classification")
            == "organized_vertical"),
        "morphology_null_reasons": _morphology_null_reason_counts(cells),
        # FASE 7: conteggi verificabili (payload completo nei record).
        **_advanced_summary(bundle),
    }
    payload = {
        "generated_at": bundle.generated_at_iso,
        "radar_timestamp": bundle.radar_timestamp_iso,
        "status": bundle.status,
        "engine": engine_meta or {},
        "source": bundle.source,
        "data_limits": cs_mod.DATA_LIMITS_NOTE,
        "rotation_measurable": False,
        "rotation_null_reason": cs_mod.ROTATION_NULL_REASON,
        "phase2": getattr(bundle, "phase2", None),
        "summary": summary,
        "structures": cells,
        **_as_of_summary(bundle),
    }
    product_bundle = _product_bundle_summary(bundle)
    if product_bundle is not None:
        payload["product_bundle"] = product_bundle
    # 2026-11-21 — riepilogo TASSONOMIA (additivo, solo se presente). I record
    # portano il blocco completo in `structures[*].taxonomy` (schema .../1):
    # qui va solo il conteggio verificabile, senza dati grezzi.
    tax = getattr(bundle, "taxonomy_summary", None)
    if isinstance(tax, dict) and tax:
        payload["taxonomy"] = tax
    return payload


def write_outputs(bundle, out_dir, engine_meta=None):
    """Scrive i sei file derivati in modo atomico. Ritorna le path scritte."""
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "latest.json": os.path.join(out_dir, "latest.json"),
        "storms.geojson": os.path.join(out_dir, "storms.geojson"),
        "tracks.json": os.path.join(out_dir, "tracks.json"),
        "storm_objects.geojson": os.path.join(out_dir, "storm_objects.geojson"),
        "storm_tracks.json": os.path.join(out_dir, "storm_tracks.json"),
        "convective_structures.json": os.path.join(
            out_dir, "convective_structures.json"),
    }
    contents = {
        "latest.json": json.dumps(
            build_latest(bundle, engine_meta,
                         previous=_read_previous_latest(out_dir)),
            default=_json_default),
        "storms.geojson": json.dumps(build_storms_geojson(bundle),
                                     default=_json_default),
        "tracks.json": json.dumps(build_tracks(bundle),
                                  default=_json_default),
        "storm_objects.geojson": json.dumps(build_storm_objects_geojson(bundle),
                                            default=_json_default),
        "storm_tracks.json": json.dumps(build_storm_tracks(bundle),
                                        default=_json_default),
        "convective_structures.json": json.dumps(
            build_convective_structures(bundle, engine_meta),
            default=_json_default),
    }
    # Fase 1: scrivi tutti i tmp e poi replace (atomicità per file);
    # su errore il blocco viene interrotto e i vecchi file restano intatti.
    for name, path in paths.items():
        _atomic_write_text(path, contents[name])
    return paths


def write_timings(out_dir, timings):
    """Aggiunge i TEMPI PER STADIO a latest.json (2026-11-12). ADDITIVO.

    Perche' non basta `build_latest`: `write_outputs` serializza `latest.json`
    per PRIMO, quindi il tempo del suo stesso stadio non puo' stare nel file
    che sta scrivendo (e `history_archive`/`predictions_record` girano dopo).
    Questa funzione rilegge il `latest.json` appena scritto e vi aggiunge:
        `timings_ms`              -> {unit, total, stages{ms,calls,pct,...}}
        `engine.stage_timings_ms` -> lo stesso oggetto
    La seconda chiave esiste perche' lo step "Timings" del workflow
    (`radar-engine.yml`) SOSTITUISCE `timings_ms` con il proprio blocco: la
    chiave `engine` non viene toccata, quindi la tabella del motore sopravvive
    al run e finisce su main/Pages. Nessun'altra chiave e' modificata.

    Ritorna il path scritto, o None se non c'e' nulla da aggiornare (nessun
    timings, latest.json assente o illeggibile)."""
    if not isinstance(timings, dict) or not timings:
        return None
    path = os.path.join(out_dir, "latest.json")
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["timings_ms"] = timings
    engine = payload.get("engine")
    if isinstance(engine, dict):
        engine["stage_timings_ms"] = timings
    else:
        payload["engine"] = {"stage_timings_ms": timings}
    _atomic_write_text(path, json.dumps(payload, default=_json_default))
    return path


def write_status_only(out_dir, status, generated_at_iso, warnings, engine_meta=None):
    """Aggiorna SOLO latest.json (status). storms/tracks restano intatti."""
    bundle = models.EngineBundle(status, generated_at_iso, SOURCE_LABEL)
    bundle.warnings = warnings
    bundle.frames = []
    bundle.cells_by_frame = []
    bundle.tracks = []
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "latest.json")
    _atomic_write_text(path, json.dumps(build_latest(bundle, engine_meta),
                                        default=_json_default))
    return path


def validate_outputs(out_dir):
    """Verifica che gli output derivati esistano e siano JSON/GeoJSON validi."""
    errors = []
    try:
        with open(os.path.join(out_dir, "latest.json"), encoding="utf-8") as fh:
            latest = json.load(fh)
    except (OSError, ValueError) as exc:
        errors.append(f"latest.json: {exc}")
        return errors
    if "status" not in latest:
        errors.append("latest.json missing 'status'")
    if "phase2_status" not in latest:
        errors.append("latest.json missing 'phase2_status'")
    # 2026-11-24 — blocco lifecycle (additivo): se presente deve avere la
    # forma dichiarata; un blocco malformato non deve passare in silenzio.
    ls = latest.get("lifecycle_stats")
    if isinstance(ls, dict):
        if not isinstance(ls.get("births"), dict):
            errors.append("latest.json lifecycle_stats.births not a dict")
        if not isinstance(ls.get("deaths"), dict):
            errors.append("latest.json lifecycle_stats.deaths not a dict")
    bf = latest.get("births_latest_frame")
    if bf is not None and not isinstance(bf, list):
        errors.append("latest.json births_latest_frame not a list")
    try:
        with open(os.path.join(out_dir, "storms.geojson"), encoding="utf-8") as fh:
            geojson = json.load(fh)
    except (OSError, ValueError) as exc:
        errors.append(f"storms.geojson: {exc}")
        return errors
    if geojson.get("type") != "FeatureCollection":
        errors.append("storms.geojson not FeatureCollection")
    try:
        with open(os.path.join(out_dir, "tracks.json"), encoding="utf-8") as fh:
            tracks = json.load(fh)
    except (OSError, ValueError) as exc:
        errors.append(f"tracks.json: {exc}")
        return errors
    if not isinstance(tracks.get("tracks"), list):
        errors.append("tracks.json missing 'tracks' list")
    # Fase 1.7 storm layer (additivo; richiesto se presenti i file)
    for name in ("storm_objects.geojson", "storm_tracks.json"):
        path = os.path.join(out_dir, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        if name.endswith(".geojson") and payload.get("type") != "FeatureCollection":
            errors.append(f"{name} not FeatureCollection")
        if name == "storm_tracks.json" and not isinstance(payload.get("tracks"), list):
            errors.append("storm_tracks.json missing 'tracks' list")
    # descrittore SOI (additivo; richiesto se presente)
    spath = os.path.join(out_dir, "convective_structures.json")
    if os.path.exists(spath):
        try:
            with open(spath, encoding="utf-8") as fh:
                sp = json.load(fh)
        except (OSError, ValueError) as exc:
            errors.append(f"convective_structures.json: {exc}")
            return errors
        if not isinstance(sp.get("structures"), list):
            errors.append("convective_structures.json missing 'structures' list")
        if "data_limits" not in sp:
            errors.append("convective_structures.json missing 'data_limits'")
        if "phase2" not in sp:
            errors.append("convective_structures.json missing 'phase2'")
        if sp.get("rotation_measurable") is not False:
            errors.append("convective_structures.json rotation_measurable!=False")
        # Tassonomia: ADDITIVA, solo se presente. Nessuna classe puo' essere
        # "confirmed" e ogni blocco deve dichiarare lo schema versionato.
        tx = sp.get("taxonomy")
        if isinstance(tx, dict) and tx:
            if tx.get("schema") != "meteorisk.taxonomy/1":
                errors.append("convective_structures.json taxonomy schema wrong")
            for rec in sp.get("structures") or []:
                block = rec.get("taxonomy") if isinstance(rec, dict) else None
                if not isinstance(block, dict):
                    continue
                if block.get("confirmed") is not False:
                    errors.append("taxonomy confirmed flag != False")
                if block.get("nwp_influence") != "none":
                    errors.append("taxonomy nwp_influence != none")
                for axis in ("echo_regime", "organization_mode", "system_scale",
                             "morphology_tags", "evolution_stage", "enhanced_V_IR"):
                    f = block.get(axis)
                    if not isinstance(f, dict):
                        continue
                    if "rule_support" not in f or "confidence" not in f:
                        errors.append("taxonomy field missing support/confidence")
                    if f.get("status") == "confirmed":
                        errors.append("taxonomy field status confirmed")
    return errors