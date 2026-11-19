#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — models.py

Strutture dati condivise del motore (Fase 1). Ogni modello espone to_dict() per
la serializzazione JSON/GeoJSON. Errori tipizzati (EngineError e sottotipi) per
una FAILURE POLICY esplicita (status ok/degraded/error).
"""

import datetime as _dt
import math

# ---------------------------------------------------------------------------
# Errori (FAILURE POLICY: mai sovrascrivere l'ultimo dataset valido)
# ---------------------------------------------------------------------------
class EngineError(Exception):
    """Errore generico del motore."""


class SourceError(EngineError):
    """DPC API non raggiungibile / risposta non interpretabile (status=degraded/error)."""


class ValidationError(EngineError):
    """GeoTIFF corrotto o metadati non validi (status=error)."""


class CrsError(EngineError):
    """CRS del GeoTIFF non interpretabile — FAIL SAFE, nessuna coordinata inventata."""


class OutputError(EngineError):
    """Scrittura/output non atomica o non valida."""


# ---------------------------------------------------------------------------
# Prodotti sorgente (modello astratto: il resto del sistema non conosce DPC)
# ---------------------------------------------------------------------------
class ProductInfo:
    """Metadati di un prodotto radar disponibile."""

    def __init__(self, product_type, time_ms, period_s):
        self.product_type = product_type
        self.time_ms = int(time_ms)
        self.period_s = int(period_s)

    @property
    def time_iso(self):
        return _dt.datetime.fromtimestamp(self.time_ms / 1000.0, _dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def to_dict(self):
        return {
            "product_type": self.product_type,
            "time_ms": self.time_ms,
            "time_iso": self.time_iso,
            "period_s": self.period_s,
        }


# ---------------------------------------------------------------------------
# Raster valido (preprocessing)
# ---------------------------------------------------------------------------
class RasterData:
    """Raster VMI validato: dati dBZ + maschera nodata esplicita + georeferenziazione."""

    def __init__(self, data, valid_mask, nodata_mask, rows, cols, crs, crs_wkt,
                 transform, pixel_area_km2, time_ms, time_iso, source_path,
                 declared_nodata, geo_transform):
        self.data = data                # ndarray float64 (rows, cols), dBZ
        self.valid_mask = valid_mask    # bool — DATA VALIDITY (nessuna soglia meteo)
        self.nodata_mask = nodata_mask  # bool — ~valid_mask
        self.rows = rows
        self.cols = cols
        self.crs = crs                  # CRS WKT/oggetto dal file (MAI assunto)
        self.crs_wkt = crs_wkt
        self.transform = transform      # affine geotransform del GeoTIFF
        self.pixel_area_km2 = pixel_area_km2
        self.time_ms = int(time_ms) if time_ms is not None else None
        self.time_iso = time_iso
        self.source_path = source_path
        self.declared_nodata = declared_nodata
        self.geo_transform = geo_transform  # pyproj.Transformer CRS->EPSG:4326

    def pixel_to_lonlat(self, row, col):
        """Converte un pixel (raster coords) in (lon, lat) EPSG:4326 via pyproj."""
        x, y = self.transform @ (float(col) + 0.5, float(row) + 0.5)
        lon, lat = self.geo_transform.transform(x, y)
        return float(lon), float(lat)

    def lonlat_to_pixel(self, lon, lat):
        """Converte (lon, lat) EPSG:4326 nel pixel (row, col) che lo CONTIENE.

        Inversa esatta di pixel_to_lonlat: (lon, lat) -> coordinate raster
        attraverso il transformer `geo_transform` in direzione INVERSA, poi
        affine inversa (~transform). Come in pixel_to_lonlat il centro del
        pixel (row, col) e' a (col+0.5, row+0.5): il floor del risultato e'
        l'indice del pixel che contiene il punto.

        Ritorna (row, col) INTERI che PUO' essere fuori griglia (negativi o
        >= rows/cols): e' compito del chiamante clampare/intersecare con la
        griglia (le finestre per-candidato intersecano con i bordi raster).
        ValueError SOLO se la posizione non e' invertibile (fuori dominio
        CRS: coordinate non finite) — nessuna posizione inventata, FAIL SAFE."""
        try:
            x, y = self.geo_transform.transform(float(lon), float(lat),
                                                direction="INVERSE")
        except (TypeError, ValueError) as exc:
            raise ValueError("lonlat_not_invertible") from exc
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("lonlat_out_of_crs_domain")
        col_f, row_f = (~self.transform) @ (x, y)
        if not (math.isfinite(col_f) and math.isfinite(row_f)):
            raise ValueError("lonlat_out_of_raster_domain")
        return int(math.floor(row_f)), int(math.floor(col_f))

    def to_dict(self):
        return {
            "time_ms": self.time_ms,
            "time_iso": self.time_iso,
            "rows": self.rows,
            "cols": self.cols,
            "crs": self.crs_wkt,
            "pixel_area_km2": self.pixel_area_km2,
            "declared_nodata": self.declared_nodata,
            "valid_pixels": int(self.valid_mask.sum()),
            "nodata_pixels": int(self.nodata_mask.sum()),
            "echo_dbz_pixels": int((self.valid_mask & (self.data >= 10.0)).sum()),
        }


# ---------------------------------------------------------------------------
# Cellula convettiva rilevata (Fase 1: descriptor geometrici, NON firma SC)
# ---------------------------------------------------------------------------
class DetectedCell:
    def __init__(self, cell_id, timestamp_ms, timestamp_iso,
                 area_km2, centroid_raster, centroid_lonlat, bbox_raster,
                 max_dbz, mean_dbz, p90_dbz, eccentricity, solidity,
                 compactness, pixel_count, frame_index):
        self.cell_id = cell_id
        self.timestamp_ms = int(timestamp_ms)
        self.timestamp_iso = timestamp_iso
        self.area_km2 = area_km2
        self.centroid_raster = tuple(centroid_raster)
        self.centroid_lonlat = tuple(centroid_lonlat)
        self.bbox = tuple(int(v) for v in bbox_raster)  # min_r, min_c, max_r, max_c
        self.max_dbz = max_dbz
        self.mean_dbz = mean_dbz
        self.p90_dbz = p90_dbz
        self.eccentricity = eccentricity
        self.solidity = solidity
        self.compactness = compactness
        self.pixel_count = int(pixel_count)
        self.frame_index = int(frame_index) if frame_index is not None else -1
        # 1.2.4.27 — MIXED: la cella attraversa il seam fra due sorgenti del
        # mosaico (mosaic.mark_cells_mixed). Default False; la chiave compare
        # nell'output SOLO quando e' True, cosi' il run a sola sorgente DPC
        # resta byte-identico.
        self.mixed = False

    def to_dict(self):
        out = {
            "cell_id": self.cell_id,
            "timestamp": self.timestamp_iso,
            "timestamp_ms": self.timestamp_ms,
            "area_km2": round(self.area_km2, 2),
            "centroid_lonlat": [round(self.centroid_lonlat[0], 5),
                                round(self.centroid_lonlat[1], 5)],
            "centroid_raster": [round(self.centroid_raster[0], 2),
                                round(self.centroid_raster[1], 2)],
            "bbox_raster": list(self.bbox),
            "max_dbz": round(self.max_dbz, 2),
            "mean_dbz": round(self.mean_dbz, 2),
            "p90_dbz": round(self.p90_dbz, 2),
            "eccentricity": round(self.eccentricity, 4),
            "solidity": round(self.solidity, 4),
            "compactness": round(self.compactness, 4),
            "pixel_count": self.pixel_count,
        }
        if getattr(self, "mixed", False):
            out["mixed"] = True
        return out


# ---------------------------------------------------------------------------
# Tracker (punti e track con motion analysis)
# ---------------------------------------------------------------------------
EVENT_BIRTH = "birth"
EVENT_DEATH = "death"
EVENT_AMBIGUOUS = "ambiguous"   # merge/split candidato (Fase 1: NON inferito)
STATUS_ACTIVE = "active"
STATUS_DEAD = "dead"


class TrackPoint:
    def __init__(self, track_id, frame_index, cell):
        self.track_id = track_id
        self.frame_index = int(frame_index)
        self.timestamp_ms = int(cell.timestamp_ms)
        self.timestamp_iso = cell.timestamp_iso
        self.cell_id = cell.cell_id
        self.lonlat = cell.centroid_lonlat
        self.area_km2 = cell.area_km2
        self.max_dbz = cell.max_dbz
        self.mean_dbz = cell.mean_dbz
        self.solidity = getattr(cell, "solidity", 1.0)
        self.compactness = getattr(cell, "compactness", 1.0)
        self.eccentricity = getattr(cell, "eccentricity", None)

    def to_dict(self):
        return {
            "track_id": self.track_id,
            "frame_index": self.frame_index,
            "timestamp": self.timestamp_iso,
            "timestamp_ms": self.timestamp_ms,
            "cell_id": self.cell_id,
            "lonlat": [round(self.lonlat[0], 5), round(self.lonlat[1], 5)],
            "area_km2": round(self.area_km2, 2),
            "max_dbz": round(self.max_dbz, 2),
            "mean_dbz": round(self.mean_dbz, 2),
            "eccentricity": (round(self.eccentricity, 4)
                             if self.eccentricity is not None else None),
            "solidity": (round(self.solidity, 4)
                         if self.solidity is not None else None),
            "compactness": (round(self.compactness, 4)
                            if self.compactness is not None else None),
        }


class Track:
    def __init__(self, track_id, first_ts_ms):
        self.track_id = track_id
        self.points = []
        self.status = STATUS_ACTIVE
        self.events = []
        self.birth_ms = int(first_ts_ms)
        self.death_ms = None
        self.motion = {}
        self.tracking_confidence = "insufficient"
        self.score_confidence = "low"
        # Lineage (2026-11-11): relazioni esplicite fra identita' quando un
        # oggetto si divide o due si fondono. `parent_ids`/`child_ids` sono
        # track_id LOCALI dello stesso run; `lineage` e' "split"/"merge"/
        # "continuation" oppure None per una nascita senza relazione.
        self.parent_ids = []
        self.child_ids = []
        self.lineage = None
        # Seed dall'archivio rolling (history.py): la track continua una track
        # vista in un run precedente invece di nascere da zero.
        self.continued_from_history = False
        self.history_uid = None
        self.history_slot = None
        self.history_age_min = None

    def to_dict(self):
        pts = [p.to_dict() for p in self.points]
        birth = self.points[0].timestamp_iso if self.points else None
        death = self.points[-1].timestamp_iso if self.points else None
        # n_frames = frame del CICLO corrente. Un punto con frame_index=-1 e'
        # l'ancora di archivio del seed: resta in `points` per storia/moto ma
        # non e' un frame osservato in questo run (2026-11-11).
        n_frames = sum(1 for p in self.points
                       if getattr(p, "frame_index", 0) >= 0)
        out = {
            "track_id": self.track_id,
            "status": self.status,
            "events": self.events,
            "n_frames": n_frames,
            "birth_time": birth,
            "death_time": death,
            "tracking_confidence": self.tracking_confidence,
            "score_confidence": self.score_confidence,
            "parent_ids": list(self.parent_ids),
            "child_ids": list(self.child_ids),
            "lineage": self.lineage,
            "continued_from_history": bool(self.continued_from_history),
            "history_uid": self.history_uid,
            "history_slot": self.history_slot,
            "history_age_min": self.history_age_min,
            "classification": self.motion.get("classification"),
            "organization_score": self.motion.get("organization_score"),
            "class_probs": self.motion.get("class_probs"),
            "velocity_kmh": self.motion.get("velocity_kmh"),
            "motion_quality": self.motion.get("motion_quality"),
            "motion_null_reason": self.motion.get("motion_null_reason"),
            "motion_source": self.motion.get("motion_source"),
            "direction_toward_deg": self.motion.get("direction_toward_deg"),
            "direction_from_deg": self.motion.get("direction_from_deg"),
            "distance_km": self.motion.get("distance_km"),
            "duration_min": self.motion.get("duration_min"),
            "area_growth_pct": self.motion.get("area_growth_pct"),
            "intensity_delta_dbz": self.motion.get("intensity_delta_dbz"),
            "points": pts,
        }
        return out


# ---------------------------------------------------------------------------
# Multi-scale: storm objects (aggregate layer CELL -> STORM OBJECT)
# ---------------------------------------------------------------------------
class StormObject:
    """Aggregato multi-cella di un singolo frame (Fase 1.7).

    L'aggregazione NON sostituisce le celle originali: conserva i riferimenti
    (cell_ids) e produce descriptor di gruppo. La geometria approssimata usa
    dischi da centroide+area (le celle non conservano la mask dei pixel, see
    DetectedCell: bbox + centroid + area) per distanze/IoU/overlap."""

    def __init__(self, storm_object_id, timestamp_ms, timestamp_iso, frame_index,
                 cells, centroid_lonlat, area_km2, equiv_radius_km,
                 bbox_lonlat, convex_hull_lonlat, max_dbz, mean_dbz, p90_dbz,
                 cell_density, solidity=1.0, compactness=1.0):
        self.storm_object_id = storm_object_id
        self.timestamp_ms = int(timestamp_ms)
        self.timestamp_iso = timestamp_iso
        self.frame_index = int(frame_index)
        self.cells = list(cells)
        self.cell_ids = [c.cell_id for c in self.cells]
        self.cell_count = len(self.cells)
        self.centroid_lonlat = tuple(centroid_lonlat)
        self.area_km2 = float(area_km2)
        self.equiv_radius_km = float(equiv_radius_km)
        self.bbox_lonlat = tuple(bbox_lonlat)          # (lon_min, lat_min, lon_max, lat_max)
        self.convex_hull_lonlat = [tuple(p) for p in convex_hull_lonlat]
        self.max_dbz = max_dbz
        self.mean_dbz = mean_dbz
        self.p90_dbz = p90_dbz
        self.cell_density = int(cell_density)          # celle entro density_radius_km
        self.solidity = float(solidity)
        self.compactness = float(compactness)
        # riempiti dal StormObjectTracker (NON dall'aggregazione)
        self.track_id = None
        self.motion_speed_kmh = None        # velocità VALIDATA (None se rifiutata)
        self.raw_motion_speed_kmh = None    # velocità RAW (audit, MAI clampata)
        self.motion_direction = None
        self.motion_confidence = None
        self.velocity_valid = None
        self.motion_status = None
        self.motion_rejection_reason = None
        self.motion_ambiguity_penalty = None
        self.tracking_ambiguity = None
        self.ambiguity_reason = None
        self.organization_score = None
        self.score_confidence = None

    def to_dict(self):
        return {
            "storm_object_id": self.storm_object_id,
            "track_id": self.track_id,
            "timestamp": self.timestamp_iso,
            "timestamp_ms": self.timestamp_ms,
            "frame_index": self.frame_index,
            "cell_count": self.cell_count,
            "cell_ids": list(self.cell_ids),
            "area_km2": round(self.area_km2, 2),
            "equiv_radius_km": round(self.equiv_radius_km, 2),
            "centroid_lonlat": [round(self.centroid_lonlat[0], 5),
                                round(self.centroid_lonlat[1], 5)],
            "bbox_lonlat": [round(v, 5) for v in self.bbox_lonlat],
            "convex_hull_lonlat": [[round(p[0], 5), round(p[1], 5)]
                                   for p in self.convex_hull_lonlat],
            "max_dbz": round(self.max_dbz, 2),
            "mean_dbz": round(self.mean_dbz, 2),
            "p90_dbz": round(self.p90_dbz, 2),
            "cell_density": self.cell_density,
            "motion_speed_kmh": self.motion_speed_kmh,
            "raw_motion_speed_kmh": self.raw_motion_speed_kmh,
            "motion_direction": self.motion_direction,
            "motion_confidence": self.motion_confidence,
            "velocity_valid": self.velocity_valid,
            "motion_status": self.motion_status,
            "motion_rejection_reason": self.motion_rejection_reason,
            "motion_ambiguity_penalty": self.motion_ambiguity_penalty,
            "tracking_ambiguity": self.tracking_ambiguity,
            "tracking_ambiguity_reason": self.ambiguity_reason,
            "organization_score": self.organization_score,
            "score_confidence": self.score_confidence,
        }


class StormTrackPoint:
    """Punto di una storm track (per-frame). Espone gli stessi attributi di
    TrackPoint (max_dbz, area_km2, lonlat, timestamp_ms, solidity, compactness)
    così che scoring.organization_score e le metriche siano riusabili."""

    def __init__(self, track_id, frame_index, obj):
        self.track_id = track_id
        self.frame_index = int(frame_index)
        self.timestamp_ms = int(obj.timestamp_ms)
        self.timestamp_iso = obj.timestamp_iso
        self.storm_object_id = obj.storm_object_id
        self.cell_count = obj.cell_count
        self.lonlat = obj.centroid_lonlat
        self.area_km2 = obj.area_km2
        self.max_dbz = obj.max_dbz
        self.mean_dbz = obj.mean_dbz
        self.solidity = getattr(obj, "solidity", 1.0)
        self.compactness = getattr(obj, "compactness", 1.0)
        self.ambiguity = obj.tracking_ambiguity
        self._obj = obj  # back-ref per il to_dict a livello oggetto (Fase 1.7)
        self.iou_to_prev = None
        self.speed_to_prev_kmh = None
        self.bearing_to_prev_deg = None

    def to_dict(self):
        return {
            "track_id": self.track_id,
            "frame_index": self.frame_index,
            "timestamp": self.timestamp_iso,
            "timestamp_ms": self.timestamp_ms,
            "storm_object_id": self.storm_object_id,
            "cell_count": self.cell_count,
            "lonlat": [round(self.lonlat[0], 5), round(self.lonlat[1], 5)],
            "area_km2": round(self.area_km2, 2),
            "max_dbz": round(self.max_dbz, 2),
            "mean_dbz": round(self.mean_dbz, 2),
            "tracking_ambiguity": self.ambiguity,
            "iou_to_prev": (round(self.iou_to_prev, 4)
                            if self.iou_to_prev is not None else None),
            "speed_to_prev_kmh": (round(self.speed_to_prev_kmh, 2)
                                  if self.speed_to_prev_kmh is not None else None),
            "bearing_to_prev_deg": (round(self.bearing_to_prev_deg, 1)
                                    if self.bearing_to_prev_deg is not None else None),
        }


class StormTrack:
    """Track di storm object (identità a livello di oggetto concettuale).

    Layer 1.7: la tracking_confidence del layer cella (Track) è un dato LOCALE;
    qui la qualità è descritta da motion_confidence e tracking_ambiguity.
    Organization Score e score_confidence conservano la semantica Fase 1 (0-100,
    bande) ma riferita allo storm object aggregato."""

    def __init__(self, track_id, first_ts_ms):
        self.track_id = track_id
        self.points = []
        self.status = STATUS_ACTIVE
        self.events = []
        self.birth_ms = int(first_ts_ms)
        self.death_ms = None
        self.motion = {}
        self.tracking_confidence = "insufficient"
        self.score_confidence = "low"
        self.motion_confidence = "low"
        self.tracking_ambiguity = "high"
        # Lineage (2026-11-11): stessa semantica del layer cella. I track_id
        # sono LOCALI al run; la relazione sopravvive al cambio di ID.
        self.parent_ids = []
        self.child_ids = []
        self.lineage = None
        self.continued_from_history = False
        self.history_uid = None
        self.history_slot = None
        self.history_age_min = None

    def to_dict(self):
        pts = [p.to_dict() for p in self.points]
        birth = self.points[0].timestamp_iso if self.points else None
        death = self.points[-1].timestamp_iso if self.points else None
        # Vedi Track.to_dict: il punto di archivio (frame_index=-1) non conta
        # come frame del ciclo corrente.
        n_frames = sum(1 for p in self.points
                       if getattr(p, "frame_index", 0) >= 0)
        return {
            "track_id": self.track_id,
            "type": "storm_object",
            "status": self.status,
            "events": self.events,
            "n_frames": n_frames,
            "birth_time": birth,
            "death_time": death,
            "tracking_confidence": self.tracking_confidence,
            "motion_confidence": self.motion_confidence,
            "tracking_ambiguity": self.tracking_ambiguity,
            "score_confidence": self.score_confidence,
            "parent_ids": list(self.parent_ids),
            "child_ids": list(self.child_ids),
            "lineage": self.lineage,
            "continued_from_history": bool(self.continued_from_history),
            "history_uid": self.history_uid,
            "history_slot": self.history_slot,
            "history_age_min": self.history_age_min,
            "classification": self.motion.get("classification"),
            "organization_score": self.motion.get("organization_score"),
            "class_probs": self.motion.get("class_probs"),
            "velocity_kmh": self.motion.get("velocity_kmh"),
            "raw_velocity_kmh": self.motion.get("raw_velocity_kmh"),
            "net_velocity_kmh": self.motion.get("net_velocity_kmh"),
            "median_segment_velocity_kmh":
                self.motion.get("median_segment_velocity_kmh"),
            "velocity_valid": self.motion.get("velocity_valid"),
            "motion_quality": self.motion.get("motion_quality"),
            "motion_null_reason": self.motion.get("motion_null_reason"),
            "motion_status": self.motion.get("motion_status"),
            "motion_rejection_reason": self.motion.get("motion_rejection_reason"),
            "rejected_segments": self.motion.get("rejected_segments"),
            "total_segments": self.motion.get("total_segments"),
            "ambiguous_segments": self.motion.get("ambiguous_segments"),
            "direction_toward_deg": self.motion.get("direction_toward_deg"),
            "direction_from_deg": self.motion.get("direction_from_deg"),
            "distance_km": self.motion.get("distance_km"),
            "duration_min": self.motion.get("duration_min"),
            "area_growth_pct": self.motion.get("area_growth_pct"),
            "intensity_delta_dbz": self.motion.get("intensity_delta_dbz"),
            "mean_cell_count": self.motion.get("mean_cell_count"),
            "max_cell_count": self.motion.get("max_cell_count"),
            "mean_iou_km": self.motion.get("mean_iou_km"),
            "points": pts,
        }


# ---------------------------------------------------------------------------
# Bundle finale (output)
# ---------------------------------------------------------------------------
class EngineBundle:
    def __init__(self, status, generated_at_iso, source_label):
        self.status = status            # "ok" | "degraded" | "error"
        self.generated_at_iso = generated_at_iso
        self.source = source_label
        self.radar_timestamp_iso = None
        self.radar_timestamp_ms = None
        self.data_latency_minutes = None
        self.frames = []                # lista RasterData (ordinata)
        self.cells_by_frame = []        # lista lista[DetectedCell]
        self.tracks = []                # lista Track
        self.storm_objects_by_frame = []  # lista lista[StormObject] (Fase 1.7)
        self.storm_tracks = []          # lista StormTrack (Fase 1.7)
        self.convective_structures = []  # record del descrittore SOI (1.2.4.17)
        self.structure_tracks_evaluated = 0
        # 1.2.4.84 - DOPPLER (shear azimutale OPERA FR): riepilogo del layer
        # (siti, candidati, costo, copertura). None = layer non eseguito.
        self.doppler = None
        # 1.3 - DOPPLER ANALYSIS + MESOCYCLONE DETECTION (additivi).
        # `doppler_analysis` = coppie/rotazione verticale/qualita'/4 stati per
        # scansione; `mesocyclone_tracks` = tracking fra scansioni. None =
        # layer non eseguito (nessuna chiave nuova nell'output). Il wiring in
        # main.py e' di competenza di un altro agente (un file, un proprietario).
        self.doppler_analysis = None
        self.mesocyclone_tracks = None
        self.severe_predictions = None   # esito del registro JSONL (o None)
        # 2026-11-12 — BUNDLE PRODOTTI DPC ALLINEATO (VIL/ETM/POH/SRI):
        # matrice slot x prodotto con valid_time/skew/stale e QC per prodotto.
        # None = bundle non costruito (nessuna chiave nuova nell'output).
        self.dpc_bundle = None
        # 2026-11-12 — slot della TIMELINE in epoch ms (stesso ordine di
        # frame_slots_iso): serve al bundle per chiedere i prodotti allo
        # stesso productDate dei frame ricostruiti dall'archivio.
        self.frame_slots_ms = []
        # 2026-11-12 — frame VMI DPC ORIGINALI (prima del mosaico OPERA):
        # il QC di dominio del bundle confronta i prodotti DPC con la VMI DPC
        # (sentinelle -9999/-9998), non con il mosaico (NaN/OPERA).
        self.dpc_frames = []
        # 1.2.4.20: riepilogo del replay (--as-of). None in modalita' live:
        # nessuna chiave nuova nell'output operativo.
        self.as_of_mode = None
        # 1.2.4.27 — MOSAICO MULTI-SORGENTE (DPC + OPERA CIRRUS).
        # `sources` = elenco pubblicato in latest.json (id/nome/licenza/
        # attribuzione/pixel/pct/valid_time); `opera_status` = esito della
        # sorgente OPERA (anche "unavailable" con motivo, senza degradare il
        # run); `mosaic_summary` = dettaglio per frame (provenienza + seam).
        self.sources = []
        self.opera_status = None
        self.mosaic_summary = None
        self.warnings = []
        self.engine = {}

    # --- ALIAS DI COMPATIBILITA' (1.2.4.17) --------------------------------
    # Il layer si chiama `convective_structures`: questi due accessor esistono
    # solo per non rompere i chiamanti storici (properties, non campi
    # duplicati: un solo stato, nessuna divergenza possibile).
    # nome storico, non usare in output
    @property
    def supercells(self):
        return self.convective_structures

    @supercells.setter
    def supercells(self, value):
        self.convective_structures = value

    @property
    def supercell_tracks_evaluated(self):
        return self.structure_tracks_evaluated

    @supercell_tracks_evaluated.setter
    def supercell_tracks_evaluated(self, value):
        self.structure_tracks_evaluated = value


def utcnow_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------------------
# 1.3 — DOPPLER ANALYSIS / MESOCYCLONE DETECTION (additivo)
# ---------------------------------------------------------------------------
# I 4 stati sono ESPLICITI e chiusi. MAI "tornado"/"confermato": la rotazione
# e' MISURATA solo dove esiste VRADH (velocita' radiale). Altrove lo stato e'
# NO_ROTATION con null_reason, non un'etichetta positiva.
ROTATION_NO_ROTATION = "NO_ROTATION"
ROTATION_SUSPECTED = "ROTATION_SUSPECTED"
ROTATION_DETECTED = "ROTATION_DETECTED"
ROTATION_MESOCYCLONE_CANDIDATE = "MESOCYCLONE_CANDIDATE"
ROTATION_STATES = (
    ROTATION_NO_ROTATION,
    ROTATION_SUSPECTED,
    ROTATION_DETECTED,
    ROTATION_MESOCYCLONE_CANDIDATE,
)

# Eventi di tracking della struttura rotante (lessico chiuso).
ROTATION_EV_APPEARANCE = "appearance"
ROTATION_EV_INTENSIFICATION = "intensification"
ROTATION_EV_WEAKENING = "weakening"
ROTATION_EV_DISPLACEMENT = "displacement"
ROTATION_EV_MERGE = "merge"
ROTATION_EV_SEPARATION = "separation"
ROTATION_EV_DISSIPATION = "dissipation"
ROTATION_EVENTS = (
    ROTATION_EV_APPEARANCE, ROTATION_EV_INTENSIFICATION, ROTATION_EV_WEAKENING,
    ROTATION_EV_DISPLACEMENT, ROTATION_EV_MERGE, ROTATION_EV_SEPARATION,
    ROTATION_EV_DISSIPATION,
)


class RotationSignature:
    """Firma di rotazione misurata dal Doppler su una struttura.

    Ogni campo introduce: source (da dove arriva), valid_time (timestamp),
    quality (qualita' osservativa) e limits (cosa NON dimostra). I campi
    ambientali (CAPE/SRH/DLS/SCP) NON compaiono qui: sono contesto separato.
    """

    def __init__(self, signature_id, lat, lon, valid_time, radar_id,
                 delta_velocity_ms, azimuthal_shear_ms_km,
                 rotation_base_km, rotation_top_km, rotation_depth_km,
                 rotation_strength_ms, vertical_continuity,
                 quality=None, parent_cell_id=None, classification=None,
                 null_reason=None, source=None, reference=None, limits=None):
        self.signature_id = signature_id
        self.lat = float(lat)
        self.lon = float(lon)
        self.valid_time = valid_time
        self.radar_id = radar_id
        self.delta_velocity_ms = delta_velocity_ms
        self.azimuthal_shear_ms_km = azimuthal_shear_ms_km
        self.rotation_base_km = rotation_base_km
        self.rotation_top_km = rotation_top_km
        self.rotation_depth_km = rotation_depth_km
        self.rotation_strength_ms = rotation_strength_ms
        self.vertical_continuity = vertical_continuity
        self.quality = quality or {}
        self.parent_cell_id = parent_cell_id
        self.classification = classification
        self.null_reason = null_reason
        self.source = source
        self.reference = reference
        self.limits = limits or []
        self.rotation_measurable = (delta_velocity_ms is not None
                                    and null_reason is None)

    def to_dict(self):
        def rnd(v, n=2):
            return round(float(v), n) if isinstance(v, (int, float)) else v
        return {
            "signature_id": self.signature_id,
            "lat": rnd(self.lat, 5), "lon": rnd(self.lon, 5),
            "valid_time": self.valid_time,
            "radar_id": self.radar_id,
            "delta_velocity_ms": rnd(self.delta_velocity_ms),
            "azimuthal_shear_ms_km": rnd(self.azimuthal_shear_ms_km),
            "rotation_base_km": rnd(self.rotation_base_km),
            "rotation_top_km": rnd(self.rotation_top_km),
            "rotation_depth_km": rnd(self.rotation_depth_km),
            "rotation_strength_ms": rnd(self.rotation_strength_ms),
            "vertical_continuity": rnd(self.vertical_continuity, 3),
            "quality": dict(self.quality),
            "parent_cell_id": self.parent_cell_id,
            "classification": self.classification,
            "rotation_measurable": bool(self.rotation_measurable),
            "null_reason": self.null_reason,
            "source": self.source,
            "reference": self.reference,
            "limits": list(self.limits),
        }


class MesocycloneTrack:
    """Traccia di una struttura rotante fra scansioni (Fase 4).

    `classification` resta uno dei 4 stati: MAI "tornado"/"confermato".
    `parent_cell_ids` sono gli id di cella convettiva associata (non una
    proprieta' della rotazione, ma il contesto a cui e' agganciata).
    """

    def __init__(self, meso_id, first_detection):
        self.meso_id = meso_id
        self.points = []
        self.events = []
        self.first_detection = first_detection
        self.last_detection = first_detection
        self.parent_cell_ids = []
        self.classification = ROTATION_NO_ROTATION
        self.max_shear_ms_km = None
        self.max_delta_velocity_ms = None
        self.max_rotation_depth_km = None
        self.max_vertical_continuity = None
        self.cell_coherent = False
        self.persistence = 0
        self.quality_score = None
        self.source = None
        self.reference = None
        self.limits = []

    def to_dict(self):
        # 1.3.0: tre campi nuovi (lat, lon, rotation_diameter) derivati da
        # points[]. Nessun dato inventato: se un punto non espone lo shear
        # azimutale si usa il primo punto con coordinate valide (scelta
        # dichiarata in field_meta); se non espone couplet_distance_km il
        # diametro resta null con null_reason. Gli altri campi non cambiano.

        def _point_shear(point):
            """Shear azimutale assoluto di un punto, o None se non presente."""
            if not isinstance(point, dict):
                return None
            for key in ("azimuthal_shear_ms_km", "shear"):
                value = point.get(key)
                if value is None:
                    continue
                try:
                    shear = abs(float(value))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(shear):
                    return shear
            return None

        def _couplet_distance(point):
            """Distanza gate-to-gate del velocity couplet, se valida."""
            if not isinstance(point, dict):
                return None
            value = point.get("couplet_distance_km")
            if value is None:
                return None
            try:
                distance = float(value)
            except (TypeError, ValueError):
                return None
            return distance if math.isfinite(distance) and distance >= 0.0 else None

        located = []
        for p in self.points:
            if not isinstance(p, dict):
                continue
            try:
                plat = round(float(p["lat"]), 5)
                plon = round(float(p["lon"]), 5)
            except (KeyError, TypeError, ValueError):
                continue
            located.append((p, plat, plon))

        peak, peak_shear = None, None
        for candidate in located:
            shear = _point_shear(candidate[0])
            if shear is not None and (peak_shear is None or shear > peak_shear):
                peak, peak_shear = candidate, shear
        if peak is not None:
            location_selection = "max_shear_point"
        elif located:
            peak = located[0]
            location_selection = "first_point_no_per_point_shear"
        else:
            location_selection = "none"

        if peak is None:
            lat = lon = None
            location_null_reason = "no_point_with_valid_lat_lon"
        else:
            _, lat, lon = peak
            location_null_reason = None

        # Diametro = proxy geometrico 2 * distanza gate-to-gate del couplet.
        # Se il punto di picco non la espone si cerca il primo punto che la
        # porta; altrimenti null motivato (nessun valore fabbricato).
        distance = _couplet_distance(peak[0]) if peak is not None else None
        if distance is not None:
            diameter_selection = "max_shear_point"
        else:
            diameter_selection = None
            for p in self.points:
                found = _couplet_distance(p)
                if found is not None:
                    distance, diameter_selection = found, "first_available_point"
                    break
        if distance is None:
            rotation_diameter = None
            diameter_null_reason = "couplet_distance_km_unavailable_in_points"
            diameter_selection = "none"
        else:
            rotation_diameter = round(distance * 2.0, 2)
            diameter_null_reason = None

        source = self.source or "MesocycloneTrack.points[]"
        reference_base = (self.reference + " | ") if self.reference else ""
        field_meta = {
            "lat": {
                "source": source,
                "reference": reference_base
                + "Latitudine del punto con massimo shear azimutale; fallback "
                  "al primo punto se lo shear per punto non e' disponibile",
                "null_reason": location_null_reason,
                "selection": location_selection,
            },
            "lon": {
                "source": source,
                "reference": reference_base
                + "Longitudine del punto con massimo shear azimutale; fallback "
                  "al primo punto se lo shear per punto non e' disponibile",
                "null_reason": location_null_reason,
                "selection": location_selection,
            },
            "rotation_diameter": {
                "source": source,
                "reference": reference_base
                + "rotation_diameter = 2 * couplet_distance_km: proxy geometrico "
                  "del diametro del vortice, SCELTA INGEGNERISTICA non calibrata",
                "null_reason": diameter_null_reason,
                "selection": diameter_selection,
            },
        }

        return {
            "meso_id": self.meso_id,
            "first_detection": self.first_detection,
            "last_detection": self.last_detection,
            "persistence": self.persistence,
            "classification": self.classification,
            "max_shear_ms_km": (round(float(self.max_shear_ms_km), 2)
                                if self.max_shear_ms_km is not None else None),
            "max_delta_velocity_ms": (round(float(self.max_delta_velocity_ms), 2)
                                      if self.max_delta_velocity_ms is not None else None),
            "max_rotation_depth_km": (round(float(self.max_rotation_depth_km), 2)
                                      if self.max_rotation_depth_km is not None else None),
            "max_vertical_continuity": (round(float(self.max_vertical_continuity), 3)
                                        if self.max_vertical_continuity is not None else None),
            "cell_coherent": bool(self.cell_coherent),
            "quality_score": (round(float(self.quality_score), 3)
                              if self.quality_score is not None else None),
            "parent_cell_ids": list(self.parent_cell_ids),
            "events": list(self.events),
            "points": [dict(p) for p in self.points],
            "source": self.source,
            "reference": self.reference,
            "limits": list(self.limits),
            "lat": lat,
            "lon": lon,
            "rotation_diameter": rotation_diameter,
            "field_meta": field_meta,
        }
