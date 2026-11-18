#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRODUTTORE WebP sinottico da frame .om reali (MeteoRisk-Light-v2, 1.3.0.18).

Il motore fronti e' `development_only` (non validato su eventi italiani):
l'output e' un PRODOTTO SPERIMENTALE DICHIARATO, non un'analisi validata.
Nessun POD/FAR/CSI viene calcolato o implicato.

Riutilizza la pipeline esistente (nessuna pipeline inventata):
  * `scripts/forecast_v2/render_fm_webp.py` per crop, ringcampionamento Mercator,
    scala colori e scrittura WebP;
  * `scripts/synoptic/fronts.py` (modulo su `main`, identico al prototipo) per il
    rilevamento TFP e la simbologia convenzionale;
  * `scripts/synoptic/om_http.py` per leggere i .om via HTTP Range senza token.

Layer (dal basso verso l'alto):
  1. oceano + terra (Natural Earth 110m land, public domain);
  2. pressione al livello del mare colorata con la scala `pressure_msl` del
     client (alpha ridotta per non coprire la terra);
  3. isobare ogni 4 hPa;
  4. BLOB sfumati delle aree di alta/bassa pressione (estremi chiusi verificati);
  5. fronti con simbologia convenzionale (tratteggiato senza simboli se la
     velocita' non e' calcolabile).

I blocchi atmosferici NON vengono disegnati: la geometria non e' verificabile
da un singolo valid time (vedi sidecar `blocks` e REPORT.md). Nessun dato
inventato, nessun token, nessun dominio nuovo.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))            # root del repo
FM_DIR = os.path.join(ROOT, "scripts", "forecast_v2")
FRONTS_DIR = os.path.join(ROOT, "scripts", "synoptic")
sys.path.insert(0, FM_DIR)
sys.path.insert(0, FRONTS_DIR)

import render_fm_webp as rfw      # noqa: E402  (pipeline esistente, riutilizzata)
import fronts as fr               # noqa: E402  (modello su main, development_only)
from om_http import open_frame    # noqa: E402  (HTTP Range, zero token)

CALIBRATION = os.path.join(ROOT, "calibration.json")
LAND = os.path.join(HERE, "land_europa.json")

# Regione sinottica (N-Atlantico + Europa): stessa funzione region_crop della
# pipeline, bbox scelto per contenere sistemi chiusi non troncati dal bordo.
SYN_REGION = (20.0, -70.0, 80.0, 50.0)   # lat_min, lon_min, lat_max, lon_max

# Simbologia convenzionale —颜色 solo per il disegno, NON ricalibrata.
COLORS = {
    "ocean": (207, 227, 242, 255),
    "land": (232, 224, 206, 255),
    "coast": (120, 110, 95, 255),
    "isobar": (90, 100, 115, 150),
    "cold": (37, 99, 235, 255),        # blu
    "warm": (220, 38, 38, 255),        # rosso
    "stationary_tri": (37, 99, 235, 255),
    "stationary_semi": (220, 38, 38, 255),
    "occluded": (124, 58, 237, 255),   # viola
    "frontogenesis": (37, 99, 235, 255),
    "frontolysis": (37, 99, 235, 255),
    "unknown": (110, 110, 110, 255),   # velocita' non calcolabile -> dashed, no symbol
    "high": (249, 115, 22),            # arancio (alta pressione)
    "low": (59, 130, 246),             # azzurro (bassa pressione)
}

LICENSE_OM = "Open-Meteo .om — CC BY 4.0 (attribuzione richiesta)"
LICENSE_LAND = "Natural Earth 110m land — public domain (naturalearthdata.com)"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Proiezione coerente con render_fm_webp.build_row_resampler
# ---------------------------------------------------------------------------
def lat_to_out_row(lat: float, cgrid: dict) -> float:
    """Riga dell'immagine (row0 = nord) per una latitudine, nello stesso
    ringcampionamento Mercator usato da render_fm_webp."""
    ny = int(cgrid["ny"]); dy = float(cgrid["dy"]); lat0 = float(cgrid["latMin"])
    lat_n = lat0 + (ny - 1) * dy
    y_n = rfw._merc_y(lat_n); y_s = rfw._merc_y(lat0)
    y = rfw._merc_y(lat)
    t = (y_n - y) / (y_n - y_s)
    return float(np.clip(t, 0.0, 1.0) * (ny - 1))


def lon_to_out_col(lon: float, cgrid: dict) -> float:
    return (float(lon) - float(cgrid["lonMin"])) / float(cgrid["dx"])


def project(lon: float, lat: float, cgrid: dict, scale: int) -> tuple[int, int]:
    return (int(round(lon_to_out_col(lon, cgrid) * scale)),
            int(round(lat_to_out_row(lat, cgrid) * scale)))


# ---------------------------------------------------------------------------
# Terra (Natural Earth)
# ---------------------------------------------------------------------------
def draw_land(draw: ImageDraw.ImageDraw, cgrid: dict, scale: int) -> dict:
    data = json.load(open(LAND, encoding="utf-8"))
    drawn = 0
    for poly in data.get("polygons", []):
        for ring in poly:
            pts = [project(lon, lat, cgrid, scale) for lon, lat in ring]
            if len(pts) >= 3:
                draw.polygon(pts, fill=COLORS["land"], outline=COLORS["coast"])
                drawn += 1
    return {"source": data.get("source"), "license": LICENSE_LAND,
            "n_polygons": data.get("n_polygons"), "n_rings_drawn": drawn,
            "url": data.get("url")}


# ---------------------------------------------------------------------------
# Pressione: scala del client + isobare
# ---------------------------------------------------------------------------
def pressure_rgba(arr: np.ndarray, cgrid: dict, scales: dict, theme: str,
                  alpha_factor: float) -> np.ndarray:
    lookup = rfw.make_lookup(scales, "pressure_msl", theme)
    if lookup is None:
        return np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    rgba = lookup(arr)
    rgba[:, :, 3] = np.rint(rgba[:, :, 3].astype(np.float64) * alpha_factor).astype(np.uint8)
    return rgba


def isobar_segments(arr: np.ndarray, level_step: float = 4.0):
    """Segmenti delle isobare (col,row) sull'array già ringcampionato."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    finite = np.isfinite(arr)
    if finite.sum() < 10:
        return [], []
    a = np.ma.masked_invalid(np.asarray(arr, dtype=np.float64))
    lo = float(np.nanmin(arr)); hi = float(np.nanmax(arr))
    levels = np.arange(math.ceil(lo / level_step) * level_step,
                       math.floor(hi / level_step) * level_step + 1e-9, level_step)
    if levels.size == 0:
        return [], []
    fig, ax = plt.subplots(figsize=(1.0, 1.0), dpi=20)
    try:
        cs = ax.contour(np.arange(a.shape[1]), np.arange(a.shape[0]), a, levels=levels)
        segs = []
        for lev, ss in zip(cs.levels, cs.allsegs):
            for s in ss:
                if len(s) >= 2:
                    segs.append((float(lev), np.asarray(s, dtype=np.float64)))
    finally:
        plt.close(fig)
    return segs, [float(x) for x in levels]


# ---------------------------------------------------------------------------
# Aree di alta/bassa pressione: estremi chiusi verificati + blob sfumato
# ---------------------------------------------------------------------------
def pressure_centers(arr: np.ndarray, cgrid: dict, max_centers: int = 12,
                     contour_delta_hpa: float = 1.0):
    """Estremi locali con VERIFICA DI CONTORNO CHIUSO.

    Un centro e' accettato solo se l'isobara (v - 2 hPa per un massimo,
    v + 2 hPa per un minimo) lo circonda senza toccare il bordo del ritaglio:
    e' geometria verificabile, non un'etichetta inventata. I plateau vengono
    fusi in un unico centro (connected-components).
    """
    from scipy.ndimage import gaussian_filter, maximum_filter, minimum_filter, label
    a = np.where(np.isfinite(arr), arr, np.nan)
    fill = float(np.nanmean(a))
    sm = gaussian_filter(np.where(np.isfinite(a), a, fill), 3)
    hi = maximum_filter(sm, size=31)
    lo = minimum_filter(sm, size=31)
    ny, nx = sm.shape
    out = []
    for kind, cand, thr in (("high", (sm == hi) & (sm > 1010.0), 1010.0),
                            ("low", (sm == lo) & (sm < 1005.0), 1005.0)):
        lab, nlab = label(cand)
        if nlab == 0:
            continue
        for k in range(1, nlab + 1):
            rr, cc = np.where(lab == k)
            if rr.size == 0:
                continue
            r, c = int(rr[np.argmax(sm[rr, cc])]), int(cc[np.argmax(sm[rr, cc])])
            v = float(sm[r, c])
            if not ((v > thr) if kind == "high" else (v < thr)):
                continue
            inside = (sm > v - contour_delta_hpa) if kind == "high" else (sm < v + contour_delta_hpa)
            cl, cn = label(inside)
            lbl = cl[r, c]
            comp = cl == lbl
            if not comp.any():
                continue
            touches = bool(comp[0, :].any() or comp[-1, :].any() or
                           comp[:, 0].any() or comp[:, -1].any())
            if touches:
                continue  # contorno aperto: non e' un centro chiuso verificato
            lat = float(cgrid["latMin"]) + (ny - 1 - r) * float(cgrid["dy"])  # row0 = nord
            lon = float(cgrid["lonMin"]) + c * float(cgrid["dx"])
            span = float(np.nanmax(sm[comp]) - np.nanmin(sm[comp]))
            out.append({"kind": kind, "value_hpa": round(v, 2),
                        "lat": round(lat, 3), "lon": round(lon, 3),
                        "contour_level_hpa": round(v - contour_delta_hpa if kind == "high"
                                                   else v + contour_delta_hpa, 2),
                        "closed_isobar_verified": True,
                        "region_span_hpa": round(span, 2),
                        "n_cells": int(comp.sum())})
    out.sort(key=lambda d: (-d["region_span_hpa"], d["kind"]))
    return out[:max_centers]


def blend_gaussian(base: Image.Image, centers: list, cgrid: dict, scale: int) -> None:
    """Sfumatura: ogni centro e' un blob gaussiano (non testo, non barra)."""
    w, h = base.size
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.asarray(base, dtype=np.float64).copy()
    for ctr in centers:
        cx, cy = project(ctr["lon"], ctr["lat"], cgrid, scale)
        sigma = max(60.0 * scale / 4.0, 8.0)   # ~500 km a 0.25 gradi
        g = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma ** 2))
        a = (0.42 * g)[:, :, None]
        col = np.array(COLORS[ctr["kind"]], dtype=np.float64)[None, None, :]
        arr[:, :, :3] = arr[:, :, :3] * (1.0 - a) + col * a
    base.paste(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)))
    # ricostruisci alpha opaco
    base.putalpha(Image.new("L", (w, h), 255))


# ---------------------------------------------------------------------------
# Fronti: simbologia convenzionale
# ---------------------------------------------------------------------------
def signed_normal(front: dict, T: np.ndarray, grid: dict) -> np.ndarray | None:
    """n = grad(theta_e)/|grad(theta_e)| (verso aria piu' calda), campionato
    sul punto medio del fronte. Convenzione di disegno dichiarata."""
    dtdy, dtdx = np.gradient(np.where(np.isfinite(T), T, np.nan))
    pts = front.get("points") or []
    if len(pts) < 2:
        return None
    lon, lat = pts[len(pts) // 2]
    c = int(round((lon - float(grid["lonMin"])) / float(grid["dx"])))
    r = int(round((lat - float(grid["latMin"])) / float(grid["dy"])))
    if not (0 <= r < T.shape[0] and 0 <= c < T.shape[1]):
        return None
    gx, gy = dtdx[r, c], dtdy[r, c]
    if not (np.isfinite(gx) and np.isfinite(gy)) or (gx * gx + gy * gy) < 1e-12:
        return None
    n = np.array([gx, gy]); n /= np.linalg.norm(n)   # [col(+), row(+)] verso caldo
    return n


def draw_fronts(draw: ImageDraw.ImageDraw, features: list, cgrid: dict,
                scale: int, T: np.ndarray, grid: dict,
                valid_time: str | None, run: str | None,
                frame: str | None) -> list:
    """Disegna i fronti. Ritorna il sidecar per-feature."""
    out = []
    for feat in features:
        if feat.get("properties", {}).get("kind") != "front":
            continue
        p = feat["properties"]
        pts = feat.get("geometry", {}).get("coordinates") or []
        ftype = p.get("front_type")           # None => velocities non calcolabile
        reason = p.get("null_reason")
        speed = p.get("speed_ms")
        record = {
            "front_type": ftype, "speed_ms": speed, "null_reason": reason,
            "length_km": p.get("length_km"), "n_points": p.get("n_points"),
            "tfp": p.get("tfp"), "strength": p.get("strength"),
            "confidence": p.get("confidence"), "symbology": p.get("symbology"),
            "symbol_side_convention": None,
            "provenance": {"valid_time": valid_time, "run": run,
                           "source": "open-meteo .om via branch fm2-* (HTTP Range)",
                           "license": LICENSE_OM, "method": fr.REF_HEWSON,
                           "frame": frame},
        }
        if len(pts) < 2:
            out.append(record); continue
        pix = [project(lon, lat, cgrid, scale) for lon, lat in pts]
        # colore/tipo di linea
        if ftype is None:
            color, dashed = COLORS["unknown"], True
        elif ftype == "cold":
            color, dashed = COLORS["cold"], False
        elif ftype == "warm":
            color, dashed = COLORS["warm"], False
        elif ftype == "stationary":
            color, dashed = COLORS["stationary_tri"], False
        elif ftype == "occluded":
            color, dashed = COLORS["occluded"], False
        else:  # frontogenesis / frontolysis
            color, dashed = COLORS.get(ftype, COLORS["unknown"]), True

        if dashed:
            for a, b in zip(pix, pix[1:]):
                mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
                dx, dy = (b[0] - a[0]) / 2.0, (b[1] - a[1]) / 2.0
                draw.line([(a[0], a[1]), (mx - dx * 0.3, my - dy * 0.3)], fill=color, width=max(1, scale))
                draw.line([(mx + dx * 0.3, my + dy * 0.3), (b[0], b[1])], fill=color, width=max(1, scale))
        else:
            draw.line(pix, fill=color, width=max(1, scale), joint="curve")

        # simboli: solo se la velocita' e' calcolabile
        if ftype is not None:
            n = signed_normal({"points": pts}, T, grid)
            rec_conv = None
            if n is not None:
                # row0=nord nell'immagine, ma n e' in coordinate griglia (row+ = sud)
                motion_sign = 1.0 if (speed or 0.0) >= 0 else -1.0
                nx_img, ny_img = float(n[0]), -float(n[1]) * motion_sign
                norm = math.hypot(nx_img, ny_img) or 1.0
                nx_img, ny_img = nx_img / norm, ny_img / norm
                rec_conv = ("+grad(theta_e) per speed>=0, -grad(theta_e) per speed<0; "
                            "convenzione di disegno ereditata da fronts.py, NON validata "
                            "contro lo spostamento osservato")
                step = max(3, len(pix) // 14)
                period = 1 if ftype not in ("frontolysis",) else 2
                for k in range(1, len(pix) - 1, step):
                    if (k // step) % period != 0:
                        continue
                    x, y = pix[k]
                    size = 5 * scale / 4.0
                    ux, uy = nx_img, ny_img       # lato "verso il moto"
                    tx, ty = -uy, ux              # tangente approssimata
                    def pt(dist, along=0.0):
                        return (x + ux * dist + tx * along, y + uy * dist + ty * along)
                    if ftype == "cold":
                        draw.polygon([pt(size), pt(-size * 0.25, -size * 0.55),
                                      pt(-size * 0.25, size * 0.55)], fill=COLORS["cold"])
                    elif ftype == "warm":
                        cxp, cyp = pt(size * 0.35)
                        draw.ellipse([cxp - size, cyp - size, cxp + size, cyp + size],
                                     fill=COLORS["warm"])
                    elif ftype == "stationary":
                        draw.polygon([pt(size * 0.7), pt(-size * 0.15, -size * 0.45),
                                      pt(-size * 0.15, size * 0.45)], fill=COLORS["stationary_tri"])
                        cxp, cyp = pt(-size * 0.55)
                        draw.ellipse([cxp - size * 0.7, cyp - size * 0.7,
                                      cxp + size * 0.7, cyp + size * 0.7],
                                     fill=COLORS["stationary_semi"])
                    elif ftype == "occluded":
                        draw.polygon([pt(size * 0.7), pt(-size * 0.15, -size * 0.45),
                                      pt(-size * 0.15, size * 0.45)], fill=COLORS["occluded"])
                        cxp, cyp = pt(size * 0.9)
                        draw.ellipse([cxp - size * 0.7, cyp - size * 0.7,
                                      cxp + size * 0.7, cyp + size * 0.7],
                                     fill=COLORS["occluded"])
                    elif ftype in ("frontogenesis", "frontolysis"):
                        draw.polygon([pt(size * 0.7), pt(-size * 0.15, -size * 0.45),
                                      pt(-size * 0.15, size * 0.45)], fill=color)
            record["symbol_side_convention"] = rec_conv
        out.append(record)
    return out


# ---------------------------------------------------------------------------
# Orchestra
# ---------------------------------------------------------------------------
def read_arrays(path: str, names: list) -> tuple[dict, list]:
    # path puo' essere un file locale o un URL raw.githubusercontent (HTTP Range)
    reader, _fs = open_frame(path)
    got = {}
    try:
        present = set(rfw.frame_array_names(reader))
        for n in names:
            if n in present:
                got[n] = rfw.read_frame_array(reader, n)
        return got, sorted(present)
    finally:
        try:
            reader.close()
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--manifest", required=True, help="latest.json del run")
    ap.add_argument("--domain", default="ecmwf_ifs025")
    ap.add_argument("--region", default="syn", choices=("syn", "europa", "italia"),
                    help="syn = N-Atlantico+Europa (default); europa/italia = regioni pipeline")
    ap.add_argument("--scale", type=int, default=2, help="ingrandimento di presentazione")
    ap.add_argument("--out", required=True)
    ap.add_argument("--sidecar", required=True)
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    manifest = rfw.read_manifest(args.manifest)
    grid = manifest["grid"]
    bbox = manifest["bbox"]
    region = SYN_REGION if args.region == "syn" else rfw.REGIONS[args.region]
    crop = rfw.region_crop(bbox, grid, region)
    if crop is None:
        raise SystemExit("FATAL: regione non interseca il dominio")
    r0, r1, c0, c1, cgrid = crop
    resample = rfw.build_row_resampler(cgrid)
    scales = rfw.load_color_scales()

    fname = os.path.basename(args.frame)
    vt = None
    for f in manifest.get("frames") or []:
        if f.get("file") == fname:
            vt = f.get("valid_time"); break
    ref_time = manifest.get("reference_time")
    log("frame=%s valid_time=%s run=%s" % (fname, vt, ref_time))

    NEED = ["pressure_msl", "geopotential_height_500hPa", "geopotential_height_1000hPa",
            "temperature_850hPa", "relative_humidity_850hPa",
            "wind_speed_850hPa", "wind_direction_850hPa"]
    arrays, present = read_arrays(args.frame, NEED)

    # pressione ringcampionata (stesso resampler della pipeline)
    pfull = arrays.get("pressure_msl")
    if pfull is None:
        raise SystemExit("FATAL: pressure_msl assente")
    parr = resample(pfull[r0:r1, c0:c1])

    # fronti: modulo su main, development_only
    cfg = fr.DetectionConfig.from_calibration(CALIBRATION, overrides={"domain": args.domain})
    gen = _dt.datetime.now(_dt.timezone.utc)
    coll = fr.analyze_frame(arrays, grid, cfg, gen, vt)

    ny, nx = parr.shape
    sc = max(1, int(args.scale))
    W, H = nx * sc, ny * sc

    # --- composizione layer ---
    base = Image.new("RGBA", (W, H), COLORS["ocean"])
    draw = ImageDraw.Draw(base)
    land_info = draw_land(draw, cgrid, sc)

    pres = pressure_rgba(parr, cgrid, scales, "light", 0.42)
    pres_img = Image.fromarray(pres, "RGBA").resize((W, H), Image.BILINEAR)
    base = Image.alpha_composite(base, pres_img)
    draw = ImageDraw.Draw(base)

    segs, levels = isobar_segments(parr, 4.0)
    for lev, s in segs:
        pp = [(int(round(x * sc)), int(round(y * sc))) for x, y in s]
        if len(pp) >= 2:
            draw.line(pp, fill=COLORS["isobar"], width=max(1, sc // 2))

    centers = pressure_centers(parr, cgrid)
    blend_gaussian(base, centers, cgrid, sc)
    draw = ImageDraw.Draw(base)

    T = arrays.get("temperature_850hPa")
    front_recs = (draw_fronts(draw, coll.get("features", []), cgrid, sc, T, grid,
                              vt, ref_time, fname) if T is not None else [])
    n_fronts = len(front_recs)

    # cornice + titolo discreto (sopra il mare, non una barra piatta di dati)
    draw.rectangle([0, 0, W - 1, H - 1], outline=(60, 70, 85, 200), width=max(1, sc // 2))

    rfw.save_webp(np.asarray(base, dtype=np.uint8), args.out, 90, False)
    t1 = time.perf_counter()

    prov = {
        "schema_version": 1,
        "kind": "synoptic_webp_prototype",
        "publication_status": "development_only — NON pubblicare",
        "validation": "fronts NOT validated on Italian events; no POD/FAR/CSI published",
        "run_reference_time": ref_time,
        "valid_time": vt,
        "domain": args.domain,
        "region": args.region,
        "region_bbox_latlon": list(region),
        "manifest": os.path.basename(args.manifest),
        "frame": fname,
        "frame_bytes": os.path.getsize(args.frame),
        "source": "open-meteo .om via branch fm2-* (HTTP Range, nessun token)",
        "license": LICENSE_OM,
        "pixel_grid": {"nx": nx, "ny": ny, "scale": sc, "out_w": W, "out_h": H,
                       "bbox": rfw.cropped_bbox(cgrid),
                       "projection": "Web Mercator row resampling (render_fm_webp.build_row_resampler)"},
        "land": land_info,
        "pressure": {"variable": "pressure_msl", "unit": "hPa",
                     "color_scale": "client color_scales.json (light), alpha 0.42",
                     "isobar_step_hpa": 4.0, "n_isobar_levels": len(levels)},
        "pressure_centers": centers,
        "blocks": {
            "drawn": False,
            "null_reason": "block_geometry_not_verifiable_from_single_valid_time",
            "detail": ("Un blocco atmosferico richiede persistenza >=5 giorni e firma a "
                       "500 hPa (split-flow); un anticiclone chiuso ad un singolo valid time "
                       "NON e' un blocco. Il precedente layer TM90 e' stato rimosso perche' "
                       "i bounding box erano segmenti piatti (geometria non verificabile). "
                       "Qui si disegnano solo BLOB di alta/bassa pressione con contorno chiuso "
                       "verificato; nessun blob di blocco."),
            "provenance": {"source": "n/a", "license": "n/a"},
        },
        "fronts": {
            "engine": fr.REF_HEWSON, "reference_front_id": fr.REF_FRONT_ID,
            "calibration_status": cfg.status, "publication_status": cfg.publication_status,
            "publication_reason": cfg.publication_reason,
            "detected": n_fronts,
            "diagnostics": coll.get("diagnostics"),
            "collection_null_reason": coll.get("null_reason"),
            "features": front_recs,
            "note": ("Nessun fronte inventato: se fronts.py non emette feature, il WebP "
                     "mostra solo pressione/terra e il campo 'fronts.detected' e' 0."),
        },
        "cost": {
            "render_seconds": round(t1 - t0, 3),
            "webp_bytes": os.path.getsize(args.out),
            "webp_sha256": None,
        },
    }
    import hashlib
    prov["cost"]["webp_sha256"] = hashlib.sha256(open(args.out, "rb").read()).hexdigest()
    with open(args.sidecar, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(prov, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    log(" fronts=%d centers=%d webp=%dB %.3fs -> %s" %
        (n_fronts, len(centers), prov["cost"]["webp_bytes"], t1 - t0, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
