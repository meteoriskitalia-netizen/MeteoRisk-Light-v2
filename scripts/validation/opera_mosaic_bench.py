#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — scripts/validation/opera_mosaic_bench.py

BANCO DI MISURA del mosaico DPC + OPERA CIRRUS (1.2.4.27). NON e' codice di
produzione: scarica frame reali, esegue il merge, misura e scrive un JSON.

Due gruppi di frame, per due domande diverse:
  * `--merge-frames` (default: 6 slot consecutivi): costo REALE di un run
    (listing + N letture + N riproiezioni + merge), copertura per sorgente,
    seam, celle toccate, determinismo.
  * `--frames` (default: 3 ore diverse): collimazione DPC <-> OPERA, che vuole
    frame distanti nel tempo.

Uso:
  python scripts/validation/opera_mosaic_bench.py \
      --merge-frames 2026-11-10T13:25:00Z,...,13:50:00Z \
      --frames 2026-11-10T00:00:00Z,2026-11-10T06:00:00Z,2026-11-10T12:00:00Z \
      --out _bench/opera_mosaic_bench.json
"""

import argparse
import datetime as _dt
import json
import os
import sys
import time

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from radar_engine import collimation, config as cfg_mod, detect, models  # noqa: E402
from radar_engine import mosaic, preprocess, source_dpc, source_opera  # noqa: E402


def _parse(s):
    return _dt.datetime.strptime(s.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc)


def load_dpc(config, when):
    cfg = config["source"]
    info = models.ProductInfo("VMI", int(when.timestamp() * 1000),
                              int(cfg.get("period_s") or 300))
    t0 = time.perf_counter()
    key, content = source_dpc.download_product(
        info, api_base=cfg.get("api_base"), origin=cfg.get("origin_header"),
        timeout_s=int(cfg.get("http_timeout_s") or 30),
        download_timeout_s=int(cfg.get("download_timeout_s") or 60),
        retries=int(cfg.get("max_download_retries") or 2))
    secs = time.perf_counter() - t0
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".tif")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        raster = preprocess.read_raster(path, config["preprocess"]["nodata_values"])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    raster.time_ms = int(when.timestamp() * 1000)
    raster.time_iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return raster, {"key": key, "bytes": len(content),
                    "seconds": round(secs, 3)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge-frames", required=True)
    ap.add_argument("--frames", required=True)
    ap.add_argument("--out", default="_bench/opera_mosaic_bench.json")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    config = cfg_mod.load_config(args.config)
    merge_stamps = [_parse(s) for s in args.merge_frames.split(",")]
    cmp_stamps = [_parse(s) for s in args.frames.split(",")]

    doc = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "opera_config": {k: config["opera"].get(k) for k in
                         ("enabled", "bbox_wgs84", "max_age_min",
                          "seam_width_px", "slot_tolerance_s",
                          "use_vsicurl")},
        "merge": {}, "collimation": {},
    }

    # ---- 1. RUN REALISTICO: N slot consecutivi, un solo apply -------------
    rasters = []
    t_fetch = 0.0
    for when in merge_stamps:
        raster, meta = load_dpc(config, when)
        rasters.append(raster)
        t_fetch += meta["seconds"]
        print(f"[dpc] {raster.time_iso} {meta['bytes']} B {meta['seconds']} s "
              f"valid={int(raster.valid_mask.sum())}")

    # run di PRODUZIONE: nessuna misura dei byte (nessun sottoprocesso)
    t0 = time.perf_counter()
    res = mosaic.apply(rasters, config,
                       now_ms=int(rasters[-1].time_ms) + 120000,
                       measure_bytes=False)
    t_prod = time.perf_counter() - t0
    # run di MISURA: stessa cosa + conteggio byte Range (costo dichiarato)
    t0 = time.perf_counter()
    res_m = mosaic.apply(rasters, config,
                         now_ms=int(rasters[-1].time_ms) + 120000,
                         measure_bytes=True)
    t_meas = time.perf_counter() - t0
    doc["merge"] = {
        "frames": [r.time_iso for r in rasters],
        "status": res.status,
        "reason": res.reason,
        "seconds_merge_production": round(t_prod, 3),
        "seconds_merge_with_byte_measure": round(t_meas, 3),
        "seconds_dpc_fetch_total": round(t_fetch, 3),
        "delta_pct_vs_dpc_fetch": (round(100.0 * t_prod / t_fetch, 1)
                                   if t_fetch else None),
        "bytes_read": res_m.meta.get("bytes_read"),
        "bytes_file": res_m.meta.get("bytes_file"),
        "range_requests": res_m.meta.get("range_requests"),
        "slots_resolved": res_m.meta.get("slots_resolved"),
        "errors": res_m.meta.get("errors"),
        "per_frame": [mf.summary() for mf in res.frames],
    }
    print(f"[merge] status={res.status} prod={t_prod:.2f}s "
          f"meas={t_meas:.2f}s bytes_read={res_m.meta.get('bytes_read')} "
          f"ranges={res_m.meta.get('range_requests')} "
          f"dpc_fetch={t_fetch:.2f}s")

    # ---- 2. seam + celle --------------------------------------------------
    seam = []
    for mf in res.frames:
        cells_all = detect.detect_cells(mf.raster, config["detect"])
        cells_excl = detect.detect_cells(mf.raster, config["detect"],
                                         exclude_mask=mf.seam_mask)
        n_mixed = mosaic.mark_cells_mixed(cells_all, mf.seam_mask, mf.raster,
                                          config["detect"])
        area_all = round(sum(c.area_km2 for c in cells_all), 2)
        area_excl = round(sum(c.area_km2 for c in cells_excl), 2)
        seam.append({
            "frame": mf.raster.time_iso,
            "seam_pixels": int(mf.seam_mask.sum()),
            "seam_pct": round(100.0 * mf.seam_mask.sum() /
                              float(mf.seam_mask.size), 4),
            "cells_without_exclusion": len(cells_all),
            "cells_with_exclusion": len(cells_excl),
            "cells_mixed": int(n_mixed),
            "max_area_without_exclusion_km2": round(
                max((c.area_km2 for c in cells_all), default=0.0), 2),
            "max_area_with_exclusion_km2": round(
                max((c.area_km2 for c in cells_excl), default=0.0), 2),
            "area_without_exclusion_km2": area_all,
            "area_with_exclusion_km2": area_excl,
        })
    doc["merge"]["seam"] = seam
    for s in seam:
        print(f"[seam] {s['frame']} px={s['seam_pixels']} "
              f"cells={s['cells_without_exclusion']}->{s['cells_with_exclusion']} "
              f"mixed={s['cells_mixed']} area={s['area_without_exclusion_km2']}"
              f"->{s['area_with_exclusion_km2']} "
              f"max={s['max_area_without_exclusion_km2']}->"
              f"{s['max_area_with_exclusion_km2']}")

    # ---- 3. costo isolato della riproiezione -----------------------------
    repro = []
    for mf in res.frames[-3:]:
        op = source_opera.fetch_frame(config, target_ms=mf.raster.time_ms,
                                      measure_bytes=False)
        t0 = time.perf_counter()
        source_opera.reproject_to_grid(op, mf.raster.transform,
                                       mf.raster.crs_wkt,
                                       (mf.raster.rows, mf.raster.cols))
        repro.append({"frame": mf.raster.time_iso,
                      "seconds": round(time.perf_counter() - t0, 3),
                      "opera_valid_pct": op.summary()["valid_pct"]})
    doc["merge"]["reprojection"] = repro
    print("[reproject]", repro)

    # ---- 4. determinismo: secondo merge sugli stessi input ----------------
    cache = {}

    def _fake_read(url, *a, **k):
        return cache[url]

    for mf in res.frames:
        t = mf.raster.time_ms
        slot = source_opera.resolve_slots(
            [t], slot_tolerance_s=int(config["opera"]["slot_tolerance_s"]))
        if slot:
            cache[config["opera"]["s3_base"] + slot[t]["key"]] = \
                source_opera.fetch_frame(config, target_ms=t,
                                         measure_bytes=False)
    orig = source_opera.read_frame
    source_opera.read_frame = _fake_read
    try:
        t0 = time.perf_counter()
        res2 = mosaic.apply(rasters, config,
                            now_ms=int(rasters[-1].time_ms) + 120000,
                            measure_bytes=False)
        t_cached = time.perf_counter() - t0
    finally:
        source_opera.read_frame = orig
    det = [{"frame": a.raster.time_iso,
            "digest_run1": a.digest(), "digest_run2": b.digest(),
            "identical": a.digest() == b.digest()}
           for a, b in zip(res.frames, res2.frames)]
    doc["merge"]["determinism"] = det
    doc["merge"]["seconds_merge_cached_no_measure"] = round(t_cached, 3)
    print(f"[determinism] identical={sum(d['identical'] for d in det)}/{len(det)} "
          f"cached_apply={t_cached:.2f}s")

    # ---- 5. collimazione su frame di ore diverse -------------------------
    # Si confronta la VMI ORIGINALE (non il mosaico): nel mosaico i pixel
    # OPERA hanno sostituito la VMI e il bias sarebbe 0 per costruzione.
    frames_cmp = []
    for when in cmp_stamps:
        dpc, meta = load_dpc(config, when)
        op = source_opera.fetch_frame(config, target_ms=dpc.time_ms,
                                      measure_bytes=False)
        grid = source_opera.reproject_to_grid(op, dpc.transform,
                                              dpc.crs_wkt,
                                              (dpc.rows, dpc.cols))
        cmp = collimation.compare(dpc.data, dpc.valid_mask, grid,
                                  dpc.transform, crs=dpc.crs_wkt)
        cmp.update({"frame": dpc.time_iso, "dpc_bytes": meta["bytes"],
                    "opera_key": op.meta.get("key"),
                    "opera_valid_pct": op.summary()["valid_pct"]})
        frames_cmp.append(cmp)
        print(f"[collim] {dpc.time_iso} n_pairs={cmp['n_pairs']} "
              f"bias={[r['bias_dbz'] for r in cmp['rings']]}")
    doc["collimation"] = {
        "generated_at": doc["generated_at"],
        "n_frames": len(frames_cmp),
        "n_pairs": int(sum(c["n_pairs"] for c in frames_cmp)),
        "presence_threshold_dbz": collimation.PRESENCE_THRESHOLD_DBZ,
        "distance_reference": "domain_center",
        "limits": collimation.LIMITS,
        "frames": frames_cmp,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("[out]", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
