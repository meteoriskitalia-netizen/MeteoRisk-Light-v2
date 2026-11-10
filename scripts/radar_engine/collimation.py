#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — collimation.py

COLLIMAZIONE DPC <-> OPERA (harness, NON interfaccia utente).

Confronta la VMI Radar-DPC e il composito OPERA CIRRUS sulla STESSA griglia
(quella della VMI) e riporta le metriche per ANELLO DI DISTANZA:

    anello 0-50, 50-100, 100-150, 150-200, 200+ km

Per ogni anello, sui pixel in cui ENTRAMBE le sorgenti hanno un eco
(valore >= `presence_threshold_dbz`):
    bias (OPERA - DPC), MAE, RMSE, correlazione di Pearson, n_pairs.
Sono riportati anche i conteggi di copertura (solo DPC, solo OPERA, nessuna).

DISTANZA: dal sito radar DPC piu' vicino se `sites` e' fornito (lon/lat),
altrimenti dal CENTRO del dominio. In entrambi i casi la distanza e' euclidea
nella proiezione del dominio (TM, scala ~1): la distorsione entro il dominio
italiano e' < 0,2% e viene dichiarata nei limiti dell'output.

LIMITI (scritti nell'output, non solo qui):
  * OPERA CIRRUS e' un MAX su 10 minuti, la VMI DPC e' un'istantanea a 5
    minuti: il bias atteso e' POSITIVO su OPERA (il max cattura i picchi) e
    non e' un errore di calibrazione. Non si applica nessuna correzione.
  * il composito OPERA e' un mosaico di radar nazionali con attenuazione e
    calibrazioni diverse: la correlazione misura l'accordo, non la verita'.
  * entrambe le sorgenti pubblicano solo gli echi: i pixel "nessuna" non sono
    "assenza di pioggia misurata".
  * nessuna soglia "buona/cattiva" viene inventata: solo numeri.

CLI (usata dal workflow radar-collimation.yml):
    python -m radar_engine.collimation --out data/radar/collimation.json \
        --frames 2026-11-10T00:00Z,2026-11-10T06:00Z,2026-11-10T12:00Z
"""

import argparse
import datetime as _dt
import json
import os

import numpy as np

from . import models

RING_EDGES_KM = (0.0, 50.0, 100.0, 150.0, 200.0, None)
PRESENCE_THRESHOLD_DBZ = 20.0

LIMITS = [
    "OPERA CIRRUS e' un MAX su 10 minuti, la VMI DPC un'istantanea: il bias "
    "atteso e' positivo su OPERA e non e' un errore di calibrazione.",
    "Distanza calcolata nella proiezione del dominio (euclidea, scala ~1): "
    "distorsione < 0,2% entro il dominio italiano.",
    "Entrambe le sorgenti pubblicano solo gli echo: i pixel senza valore non "
    "sono 'assenza di precipitazione misurata'.",
    "Nessuna soglia di qualita' viene applicata: le metriche sono descrittive.",
]


def _ring_labels(edges=RING_EDGES_KM):
    out = []
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]
        out.append((a, b, f"{int(a)}-{int(b)}" if b is not None else f"{int(a)}+"))
    return out


def ring_index(dist_km, edges=RING_EDGES_KM):
    """Indice dell'anello per ogni distanza (array). -1 = fuori tabella."""
    d = np.asarray(dist_km, dtype="float64")
    idx = np.full(d.shape, -1, dtype="int16")
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]
        if b is None:
            m = d >= a
        else:
            m = (d >= a) & (d < b)
        idx[m] = i
    return idx


def pixel_distances_km(transform, rows, cols, crs=None, sites=None,
                       center_lonlat=None):
    """Distanza (km) di ogni pixel dal sito piu' vicino o dal centro dominio.

    `sites`: lista di (lon, lat) in EPSG:4326 -> proiettati nella CRS del
    dominio con pyproj. `center_lonlat`: centro alternativo (default: centro
    geometrico del raster)."""
    cols_idx = np.arange(cols, dtype="float64") + 0.5
    rows_idx = np.arange(rows, dtype="float64") + 0.5
    xs = transform.c + transform.a * cols_idx
    ys = transform.f + transform.e * rows_idx
    if sites:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        pts = [tr.transform(float(lon), float(lat)) for lon, lat in sites]
        best = None
        for x, y in pts:
            d2 = (xs[None, :] - x) ** 2 + (ys[:, None] - y) ** 2
            best = d2 if best is None else np.minimum(best, d2)
        return np.sqrt(best) / 1000.0
    if center_lonlat is not None:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        cx, cy = tr.transform(float(center_lonlat[0]), float(center_lonlat[1]))
    else:
        cx = transform.c + transform.a * (cols / 2.0)
        cy = transform.f + transform.e * (rows / 2.0)
    d2 = (xs[None, :] - cx) ** 2 + (ys[:, None] - cy) ** 2
    return np.sqrt(d2) / 1000.0


def compare(dpc_dbz, dpc_valid, opera_dbz, transform, crs=None,
            presence_threshold_dbz=PRESENCE_THRESHOLD_DBZ, sites=None,
            center_lonlat=None, edges=RING_EDGES_KM):
    """Metriche per anello fra due raster ALLINEATI (stessa forma/griglia)."""
    dpc = np.asarray(dpc_dbz, dtype="float64")
    op = np.asarray(opera_dbz, dtype="float64")
    if dpc.shape != op.shape:
        raise ValueError(f"grid_mismatch:{dpc.shape}!={op.shape}")
    dv = np.asarray(dpc_valid, dtype=bool) & np.isfinite(dpc)
    ov = np.isfinite(op)
    rows, cols = dpc.shape
    dist = pixel_distances_km(transform, rows, cols, crs=crs, sites=sites,
                              center_lonlat=center_lonlat)
    ridx = ring_index(dist, edges)
    d_echo = dv & (dpc >= float(presence_threshold_dbz))
    o_echo = ov & (op >= float(presence_threshold_dbz))
    both = d_echo & o_echo

    rings = []
    for i, (a, b, label) in enumerate(_ring_labels(edges)):
        m = ridx == i
        n_pairs = int((both & m).sum())
        row = {
            "ring_km": [a, b],
            "label": label,
            "n_pairs": n_pairs,
            "n_echo_dpc": int((d_echo & m).sum()),
            "n_echo_opera": int((o_echo & m).sum()),
            "n_only_dpc": int((d_echo & ~o_echo & m).sum()),
            "n_only_opera": int((o_echo & ~d_echo & m).sum()),
            "n_neither": int((~d_echo & ~o_echo & m).sum()),
            "bias_dbz": None,
            "mae_dbz": None,
            "rmse_dbz": None,
            "correlation": None,
        }
        if n_pairs > 0:
            d = dpc[both & m]
            o = op[both & m]
            diff = o - d
            row["bias_dbz"] = round(float(diff.mean()), 4)
            row["mae_dbz"] = round(float(np.abs(diff).mean()), 4)
            row["rmse_dbz"] = round(float(np.sqrt((diff ** 2).mean())), 4)
            if n_pairs >= 2 and float(d.std()) > 0 and float(o.std()) > 0:
                row["correlation"] = round(float(np.corrcoef(d, o)[0, 1]), 4)
        rings.append(row)
    return {
        "presence_threshold_dbz": float(presence_threshold_dbz),
        "rings": rings,
        "n_pairs": int(both.sum()),
        "n_pixels": int(rows * cols),
        "distance_km": {
            "min": round(float(dist[both].min()), 2) if both.any() else None,
            "median": round(float(np.median(dist[both])), 2)
            if both.any() else None,
            "max": round(float(dist[both].max()), 2) if both.any() else None,
        },
        "distance_reference": ("nearest_dpc_site" if sites else "domain_center"),
        "limits": list(LIMITS),
    }


# ---------------------------------------------------------------------------
# Runner (CLI): frame reali DPC + OPERA
# ---------------------------------------------------------------------------
def _parse_iso(s):
    return _dt.datetime.strptime(s.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc)


def _load_dpc(config, when):
    """Scarica e valida la VMI DPC per l'istante `when` (SourceError se 5xx)."""
    from . import source_dpc, preprocess
    cfg = (config or {}).get("source") or {}
    info = models.ProductInfo("VMI", int(when.timestamp() * 1000),
                              int(cfg.get("period_s") or 300))
    key, content = source_dpc.download_product(
        info, api_base=cfg.get("api_base"), origin=cfg.get("origin_header"),
        timeout_s=int(cfg.get("http_timeout_s") or 30),
        download_timeout_s=int(cfg.get("download_timeout_s") or 60),
        retries=int(cfg.get("max_download_retries") or 2))
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".tif")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        raster = preprocess.read_raster(
            path, (config or {}).get("preprocess", {}).get("nodata_values"),
            geo_plausible_bbox=None)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    raster.time_ms = int(when.timestamp() * 1000)
    raster.time_iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return raster, key, len(content)


def run(config, frames_iso, sites=None, out_path=None):
    """Confronta N frame reali e ritorna il documento di collimazione."""
    from . import source_opera
    results = []
    for s in frames_iso:
        when = _parse_iso(s)
        raster, key, n_bytes = _load_dpc(config, when)
        opera = source_opera.fetch_frame(
            config, reference_dt=when,
            target_ms=int(when.timestamp() * 1000))
        op_grid = source_opera.reproject_to_grid(
            opera, raster.transform, raster.crs_wkt,
            (raster.rows, raster.cols))
        cmp = compare(raster.data, raster.valid_mask, op_grid,
                      raster.transform, crs=raster.crs_wkt, sites=sites)
        cmp.update({
            "frame": s,
            "dpc_key": key,
            "dpc_bytes": n_bytes,
            "opera_key": opera.meta.get("key"),
            "opera_bytes_read": opera.meta.get("bytes_read"),
            "opera_bytes_file": opera.meta.get("bytes_file"),
            "opera_valid_pct": opera.summary().get("valid_pct"),
        })
        results.append(cmp)
    doc = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "n_frames": len(results),
        "n_pairs": int(sum(r["n_pairs"] for r in results)),
        "presence_threshold_dbz": PRESENCE_THRESHOLD_DBZ,
        "distance_reference": ("nearest_dpc_site" if sites else "domain_center"),
        "limits": list(LIMITS),
        "frames": results,
    }
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, out_path)
    return doc


def main(argv=None):
    from . import config as cfg_mod
    ap = argparse.ArgumentParser(description="Collimazione DPC <-> OPERA")
    ap.add_argument("--config", default=None)
    ap.add_argument("--frames", required=True,
                    help="istanti UTC separati da virgola (YYYY-MM-DDTHH:MM:SSZ)")
    ap.add_argument("--out", default="data/radar/collimation.json")
    ap.add_argument("--sites", default=None,
                    help="file JSON [[lon,lat],...] dei siti radar DPC")
    args = ap.parse_args(argv)
    config = cfg_mod.load_config(args.config)
    sites = None
    if args.sites:
        with open(args.sites, encoding="utf-8") as fh:
            sites = [(float(p[0]), float(p[1])) for p in json.load(fh)]
    doc = run(config, args.frames.split(","), sites=sites, out_path=args.out)
    print(json.dumps({k: doc[k] for k in ("generated_at", "n_frames",
                                          "n_pairs")}, ensure_ascii=False))
    for r in doc["frames"]:
        print(r["frame"], "n_pairs", r["n_pairs"],
              "bias", [x["bias_dbz"] for x in r["rings"]])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
