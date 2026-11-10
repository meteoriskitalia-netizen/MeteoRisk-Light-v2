#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — mosaic.py

MERGE MULTI-SORGENTE CON PROVENIENZA PER PIXEL (DPC VMI + OPERA CIRRUS).

Regole (nessun blend: ogni pixel viene da UNA sola sorgente):
  * priorita' STATICA: DPC=10 (vince), OPERA=20 (subentra). Dove la VMI ha un
    valore valido vince la VMI, altrove entra OPERA.
  * freschezza (`max_age_min`): se il vincitore e' piu' vecchio della soglia e
    l'altra sorgente e' fresca, il pixel viene SOSTITUITO e il frame lo dichiara
    (`substituted=true` + `substitute_reason`). Un pixel OPERA piu' vecchio
    della soglia non entra mai nel mosaico.
  * provenienza: `source_id` (uint8: 0=none, 10=dpc, 20=opera) e `source_ts`
    (epoch secondi dell'istante nominale della sorgente vincente) per pixel.
  * SEAM: fascia di `seam_width_px` pixel a cavallo del confine fra le due
    sorgenti. I pixel del seam restano dato valido nel mosaico pubblicato ma
    sono ESCLUSI da detect/tracking (una cella a cavallo di due sorgenti non e'
    una cella osservata da una sola sorgente). Le celle il cui footprint tocca
    il seam prendono `mixed=true`.

DETERMINISMO: nessuna casualita', nessun ordinamento non deterministico.
Due run sugli stessi input producono `data`, `source_id` e `source_ts`
byte-identici (test: test_opera_mosaic.py::test_merge_is_deterministic).

LIMITE DICHIARATO: entrambe le sorgenti pubblicano SOLO gli echi. Un pixel
`source_id=0` significa "nessuna delle due sorgenti ha un eco valido", NON
"assenza di precipitazione misurata": la copertura (cielo-radar) non e'
deducibile da questi prodotti e il modulo non la inventa.
"""

import hashlib
import time

import numpy as np
import scipy.ndimage as ndi

from . import models
from . import source_opera

SOURCE_NONE = 0
SOURCE_DPC = 10
SOURCE_OPERA = 20

SOURCE_META = {
    SOURCE_DPC: {
        "id": "dpc",
        "name": "Radar-DPC VMI",
        "license": "Dati Radar-DPC (Dipartimento della Protezione Civile)",
        "attribution": "Radar-DPC - Dipartimento della Protezione Civile",
    },
    SOURCE_OPERA: {
        "id": "opera",
        "name": source_opera.SOURCE_NAME,
        "license": source_opera.LICENSE,
        "attribution": source_opera.ATTRIBUTION,
    },
}


class MosaicFrame:
    """Un frame del mosaico: raster fuso + provenienza + seam + statistiche."""

    def __init__(self, raster, source_id, source_ts, seam_mask, stats):
        self.raster = raster
        self.source_id = source_id
        self.source_ts = source_ts
        self.seam_mask = seam_mask
        self.stats = stats

    def digest(self):
        """sha256 di data/source_id/source_ts (prova di determinismo)."""
        h = hashlib.sha256()
        h.update(np.ascontiguousarray(self.raster.data, dtype="float64").tobytes())
        h.update(np.ascontiguousarray(self.source_id, dtype="uint8").tobytes())
        h.update(np.ascontiguousarray(self.source_ts, dtype="int64").tobytes())
        return h.hexdigest()

    def summary(self):
        return dict(self.stats, digest=self.digest())


class MosaicResult:
    """Esito del merge su tutti i frame di una run."""

    def __init__(self, frames, status, reason=None, meta=None):
        self.frames = frames
        self.status = status            # "ok" | "partial" | "unavailable"
        self.reason = reason
        self.meta = dict(meta or {})
        self.seam_by_time_ms = {mf.raster.time_ms: mf.seam_mask
                                for mf in frames}
        self.mosaic_by_time_ms = {mf.raster.time_ms: mf for mf in frames}

    def summary(self):
        return {
            "status": self.status,
            "reason": self.reason,
            **self.meta,
            "frames": [mf.summary() for mf in self.frames],
        }


def _cfg(config):
    return (config or {}).get("opera") or {}


def _age_min(now_ms, t_ms):
    if now_ms is None or t_ms is None:
        return None
    return round((int(now_ms) - int(t_ms)) / 60000.0, 2)


def _seam_mask(dpc_mask, opera_mask, width_px):
    """Fascia di `width_px` pixel a cavallo del confine fra le due sorgenti."""
    w = int(width_px or 0)
    if w <= 0 or not dpc_mask.any() or not opera_mask.any():
        return np.zeros(dpc_mask.shape, dtype=bool)
    se = np.ones((2 * w + 1, 2 * w + 1), dtype=bool)
    near_dpc = ndi.binary_dilation(dpc_mask, structure=se)
    near_opera = ndi.binary_dilation(opera_mask, structure=se)
    return (near_dpc & opera_mask) | (near_opera & dpc_mask)


def merge_frame(dpc_raster, opera_dbz, opera_time_ms, config, now_ms=None,
                is_latest=True):
    """Fonde un frame VMI con il corrispondente frame OPERA (stessa griglia).

    `opera_dbz`: ndarray float32 gia' riproiettato sulla griglia della VMI
    (NaN dove OPERA non ha eco) oppure None (sorgente non disponibile).
    `is_latest`: il gate di freschezza ASSOLUTO (eta' rispetto a `now_ms`) si
    applica SOLO al frame piu' recente della run: i frame storici della
    finestra di tracking hanno per definizione 5, 10, 20 minuti e non vanno
    sostituiti. Per i frame storici resta il gate di ALLINEAMENTO di slot
    (|t_opera - t_dpc| <= max_age_min).
    Ritorna MosaicFrame."""
    ocfg = _cfg(config)
    max_age = float(ocfg.get("max_age_min", 20))
    seam_px = int(ocfg.get("seam_width_px", 2))

    dpc_data = np.asarray(dpc_raster.data, dtype="float64")
    dpc_valid = np.asarray(dpc_raster.valid_mask, dtype=bool)
    dpc_ts_ms = dpc_raster.time_ms
    dpc_age = _age_min(now_ms, dpc_ts_ms)

    opera_valid = np.zeros(dpc_valid.shape, dtype=bool)
    if opera_dbz is not None:
        arr = np.asarray(opera_dbz)
        opera_valid = np.isfinite(arr) & (arr >= source_opera.DBZ_MIN)
    opera_age = _age_min(now_ms, opera_time_ms) if opera_time_ms else None
    slot_delta_min = (round(abs(int(opera_time_ms) - int(dpc_ts_ms)) / 60000.0, 2)
                      if (opera_time_ms and dpc_ts_ms) else None)

    dpc_fresh = (not is_latest) or dpc_age is None or (dpc_age <= max_age)
    opera_abs_fresh = (opera_age is not None) and (opera_age <= max_age)
    opera_slot_ok = (slot_delta_min is None) or (slot_delta_min <= max_age)

    # --- regola di priorita' + gate di freschezza -------------------------
    use_opera = np.zeros(dpc_valid.shape, dtype=bool)
    substituted = False
    reason = None
    if opera_dbz is None:
        reason = "opera_unavailable"
    elif is_latest and not opera_abs_fresh:
        # OPERA piu' vecchio della soglia: non entra MAI nel mosaico.
        reason = "opera_stale"
    elif is_latest and not dpc_fresh:
        # DPC fermo/vecchio e OPERA corrente: OPERA subentra anche dove la VMI
        # ha un valore (e' il caso per cui esiste il gate).
        use_opera = opera_valid
        substituted = bool((opera_valid & dpc_valid).any())
        reason = "dpc_stale_opera_fresh"
    elif not opera_slot_ok:
        reason = "opera_slot_mismatch"
    else:
        use_opera = opera_valid & ~dpc_valid
    # Un pixel DPC valido viene ceduto a OPERA SOLO nel caso di sostituzione
    # per freschezza (DPC vecchio, OPERA fresco). In tutti gli altri casi il
    # DPC resta il vincitore dove ha un valore.
    opera_override = use_opera & dpc_valid
    use_dpc = dpc_valid & ~opera_override

    source_id = np.full(dpc_valid.shape, SOURCE_NONE, dtype="uint8")
    source_ts = np.zeros(dpc_valid.shape, dtype="int64")
    data = np.full(dpc_valid.shape, np.nan, dtype="float64")

    if use_dpc.any():
        source_id[use_dpc] = SOURCE_DPC
        source_ts[use_dpc] = int(dpc_ts_ms // 1000) if dpc_ts_ms else 0
        data[use_dpc] = dpc_data[use_dpc]
    if use_opera.any():
        source_id[use_opera] = SOURCE_OPERA
        source_ts[use_opera] = int(opera_time_ms // 1000) if opera_time_ms else 0
        data[use_opera] = np.asarray(opera_dbz, dtype="float64")[use_opera]

    valid = source_id != SOURCE_NONE
    seam = _seam_mask(source_id == SOURCE_DPC, source_id == SOURCE_OPERA,
                      seam_px)

    raster = models.RasterData(
        data=data,
        valid_mask=valid,
        nodata_mask=~valid,
        rows=dpc_raster.rows,
        cols=dpc_raster.cols,
        crs=dpc_raster.crs,
        crs_wkt=dpc_raster.crs_wkt,
        transform=dpc_raster.transform,
        pixel_area_km2=dpc_raster.pixel_area_km2,
        time_ms=dpc_raster.time_ms,
        time_iso=dpc_raster.time_iso,
        source_path=dpc_raster.source_path,
        declared_nodata=dpc_raster.declared_nodata,
        geo_transform=dpc_raster.geo_transform,
    )

    total = int(dpc_valid.size)
    n_dpc = int((source_id == SOURCE_DPC).sum())
    n_opera = int((source_id == SOURCE_OPERA).sum())
    n_none = int((source_id == SOURCE_NONE).sum())
    stats = {
        "time_iso": dpc_raster.time_iso,
        "pixels_total": total,
        "pixels_dpc": n_dpc,
        "pixels_opera": n_opera,
        "pixels_none": n_none,
        "pct_dpc": round(100.0 * n_dpc / total, 4) if total else None,
        "pct_opera": round(100.0 * n_opera / total, 4) if total else None,
        "seam_pixels": int(seam.sum()),
        "dpc_age_min": dpc_age,
        "opera_age_min": opera_age,
        "slot_delta_min": slot_delta_min,
        "is_latest": bool(is_latest),
        "dpc_fresh": bool(dpc_fresh),
        "opera_fresh": bool(opera_abs_fresh),
        "substituted": bool(substituted),
        "substitute_reason": reason,
        "opera_time_iso": (source_opera._iso_ms(opera_time_ms)
                           if opera_time_ms else None),
    }
    return MosaicFrame(raster, source_id, source_ts, seam, stats)


def _reproject(frame, dst_raster, resampling="nearest"):
    return source_opera.reproject_to_grid(
        frame, dst_raster.transform, dst_raster.crs_wkt,
        (dst_raster.rows, dst_raster.cols), resampling=resampling)


def apply(frames, config, now_ms=None, measure_bytes=True):
    """Applica il merge OPERA a TUTTI i frame di una run.

    Degrado pulito: se il listing o la lettura OPERA falliscono, `status` e'
    "unavailable"/"partial" con `reason` e i frame restano DPC-only. Nessuna
    eccezione propagata: il motore deve continuare (requisito di produzione).
    Ritorna MosaicResult (mai None se `opera.enabled`)."""
    ocfg = _cfg(config)
    if not ocfg.get("enabled", False):
        return MosaicResult([], "disabled", reason="opera_disabled")

    t0 = time.perf_counter()
    meta = {"seconds": None, "bytes_file": None, "bytes_read": None,
            "range_requests": None, "slots_resolved": 0, "frames_merged": 0,
            "opera_keys": [], "errors": []}
    targets = [int(f.time_ms) for f in frames if f.time_ms is not None]
    if not targets:
        return MosaicResult([], "unavailable", reason="no_frames", meta=meta)

    try:
        slots = source_opera.resolve_slots(
            targets,
            slot_tolerance_s=int(ocfg.get("slot_tolerance_s", 300)),
            s3_base=ocfg.get("s3_base") or source_opera.S3_BASE,
            timeout_s=int(ocfg.get("timeout_s", 60)))
    except Exception as exc:
        meta["seconds"] = round(time.perf_counter() - t0, 3)
        return MosaicResult([], "unavailable",
                            reason=f"listing_failed:{exc}", meta=meta)

    meta["slots_resolved"] = len(slots)
    cache_dir = (config or {}).get("source", {}).get("cache_dir")
    loaded = {}
    out = []
    n_bytes = 0
    n_range = 0
    n_file = 0
    for i, raster in enumerate(frames):
        t_ms = int(raster.time_ms) if raster.time_ms is not None else None
        slot = slots.get(t_ms) if t_ms is not None else None
        opera_frame = None
        if slot is not None:
            key = slot["key"]
            if key not in loaded:
                try:
                    loaded[key] = source_opera.read_frame(
                        (ocfg.get("s3_base") or source_opera.S3_BASE) + key,
                        ocfg.get("bbox_wgs84") or [6.0, 35.0, 20.0, 48.0],
                        time_ms=slot["time_ms"],
                        time_iso=source_opera._iso_ms(slot["time_ms"]),
                        measure_bytes=measure_bytes,
                        cache_dir=cache_dir,
                        timeout_s=int(ocfg.get("timeout_s", 60)),
                        use_vsicurl=bool(ocfg.get("use_vsicurl", True)),
                        clamp_dbz=ocfg.get("clamp_dbz"))
                except Exception as exc:
                    loaded[key] = exc
                    meta["errors"].append(f"{key}:{exc}")
            item = loaded[key]
            if not isinstance(item, Exception):
                opera_frame = item
                meta["opera_keys"].append(key)
                if item.meta.get("bytes_read"):
                    n_bytes += int(item.meta["bytes_read"])
                if item.meta.get("range_requests"):
                    n_range += int(item.meta["range_requests"])
                if item.meta.get("bytes_file"):
                    n_file = max(n_file, int(item.meta["bytes_file"]))
        opera_dbz = _reproject(opera_frame, raster) if opera_frame is not None \
            else None
        mf = merge_frame(raster, opera_dbz,
                         opera_frame.time_ms if opera_frame is not None else None,
                         config, now_ms=now_ms,
                         is_latest=(i == len(frames) - 1))
        out.append(mf)

    meta["seconds"] = round(time.perf_counter() - t0, 3)
    meta["bytes_read"] = n_bytes if n_range else None
    meta["range_requests"] = n_range if n_range else None
    meta["bytes_file"] = n_file or None
    meta["frames_merged"] = len(out)
    meta["opera_keys"] = sorted(set(meta["opera_keys"]))
    status = "ok" if len(slots) == len(targets) and not meta["errors"] else (
        "partial" if slots else "unavailable")
    reason = None
    if status != "ok":
        reason = ("no_slot_for_frame" if not slots
                  else "partial_slots_or_errors")
    return MosaicResult(out, status, reason=reason, meta=meta)


def mark_cells_mixed(cells, seam_mask, raster, cfg_detect=None):
    """Marca `mixed=True` sulle celle il cui footprint attraversa il seam.

    Il footprint e' la componente connessa (stessa soglia/connettivita' di
    detect) che contiene il centroide della cella, ricalcolata SOLO nella bbox
    della cella. Costo trascurabile: si esegue unicamente per le celle la cui
    bbox contiene almeno un pixel di seam.
    Ritorna il numero di celle marcate."""
    if not cells or seam_mask is None or not seam_mask.any():
        return 0
    threshold = float((cfg_detect or {}).get("dbz_threshold", 20.0))
    conn8 = bool((cfg_detect or {}).get("connectivity_8", True))
    se = np.ones((3, 3), dtype=bool) if conn8 else ndi.generate_binary_structure(2, 2)
    n_mixed = 0
    for cell in cells:
        r0, c0, r1, c1 = [int(v) for v in cell.bbox]
        r0 = max(0, r0 - 1)
        c0 = max(0, c0 - 1)
        r1 = min(raster.rows - 1, r1 + 1)
        c1 = min(raster.cols - 1, c1 + 1)
        seam_win = seam_mask[r0:r1 + 1, c0:c1 + 1]
        if not seam_win.any():
            continue
        binary = raster.valid_mask[r0:r1 + 1, c0:c1 + 1] & \
            (raster.data[r0:r1 + 1, c0:c1 + 1] >= threshold)
        lab, n = ndi.label(binary, structure=se)
        if n == 0:
            continue
        cr = int(round(cell.centroid_raster[0])) - r0
        cc = int(round(cell.centroid_raster[1])) - c0
        cr = min(max(cr, 0), binary.shape[0] - 1)
        cc = min(max(cc, 0), binary.shape[1] - 1)
        lbl = lab[cr, cc]
        if lbl == 0:
            # centroide su un pixel non etichettato (morfologia): si prende
            # l'etichetta piu' vicina dentro la finestra, senza inventare.
            ys, xs = np.nonzero(lab)
            if ys.size == 0:
                continue
            k = int(np.argmin((ys - cr) ** 2 + (xs - cc) ** 2))
            lbl = lab[ys[k], xs[k]]
        if (lab == lbl)[seam_win].any():
            cell.mixed = True
            n_mixed += 1
    return n_mixed


def source_summary(mosaic, time_ms=None):
    """`sources[]` per latest.json: pixel e percentuale per sorgente.

    Riferimento = frame piu' recente (quello pubblicato in storms.geojson),
    salvo `time_ms` esplicito. Nessuna riga per una sorgente con 0 pixel."""
    if mosaic is None or not mosaic.frames:
        return []
    mf = mosaic.mosaic_by_time_ms.get(int(time_ms)) if time_ms else \
        mosaic.frames[-1]
    if mf is None:
        mf = mosaic.frames[-1]
    st = mf.stats
    out = []
    for sid, key, n, pct in (
            (SOURCE_DPC, "pixels_dpc", st["pixels_dpc"], st["pct_dpc"]),
            (SOURCE_OPERA, "pixels_opera", st["pixels_opera"], st["pct_opera"])):
        if not n:
            continue
        meta = SOURCE_META[sid]
        ts = None
        if sid == SOURCE_DPC:
            ts = mf.raster.time_iso
        else:
            ts = st.get("opera_time_iso")
        out.append({
            "id": meta["id"],
            "name": meta["name"],
            "license": meta["license"],
            "attribution": meta["attribution"],
            "pixels": int(n),
            "pct": pct,
            "valid_time": ts,
        })
    return out


def dpc_only_summary(frames):
    """`sources[]` quando il merge non e' avvenuto (OPERA non disponibile).

    Riporta la sola sorgente DPC con i pixel del frame piu' recente: il
    consumatore vede SEMPRE da dove viene il dato, anche in degrado."""
    if not frames:
        return []
    raster = frames[-1]
    total = int(raster.rows * raster.cols)
    n = int(raster.valid_mask.sum())
    meta = SOURCE_META[SOURCE_DPC]
    return [{
        "id": meta["id"],
        "name": meta["name"],
        "license": meta["license"],
        "attribution": meta["attribution"],
        "pixels": n,
        "pct": round(100.0 * n / total, 4) if total else None,
        "valid_time": raster.time_iso,
    }]


def write_provenance(path, mosaic):
    """Scrive source_id/source_ts per frame in un .npz compresso (opzionale).

    Non viene committato dal motore: 1,68 Mpx x (1+8) byte per frame sono
    ~15 MB non compressi. Esiste per il collaudo e per chi deve rifare il
    merge pixel-per-pixel."""
    arrays = {}
    for mf in mosaic.frames:
        t = int(mf.raster.time_ms or 0)
        arrays[f"source_id_{t}"] = mf.source_id
        arrays[f"source_ts_{t}"] = mf.source_ts
        arrays[f"seam_{t}"] = mf.seam_mask.astype("uint8")
    arrays["time_ms"] = np.array([int(mf.raster.time_ms or 0)
                                  for mf in mosaic.frames], dtype="int64")
    np.savez_compressed(path, **arrays)
    return path
