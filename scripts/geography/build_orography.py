#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Costruzione della griglia di orografia semplificata per l'Italia.

Due modalita', dichiarate e misurabili:

  --mode points   (A) interpola le 257 quote di punto di
                  data/latest/meteorisk-points.json (Open-Meteo GLO-90,
                  campo "elevation") con IDW su una griglia regolare.
                  Nessun download. Risoluzione effettiva ~30 km (distanza
                  mediana fra stazioni), quindi la griglia e' LISCIA: non
                  rappresenta valli e creste.
                  NON PUBBLICATA: l'errore su w.grad(h) e' del 98% (documentato
                  in docs/OROGRAFIA.md). La modalita' resta per rifare il
                  confronto A-vs-B, non per produrre un file da committare.

  --mode dem      (B) aggrega i Terrain Tiles (Mapzen/joerd, AWS Open Data)
                  a zoom 8, dove il suolo italiano proviene da SRTM 1/3
                  arcsec (pubblico dominio, USGS). Il download e' una
                  tantum (~13 MB per il riquadro Italia) ed e' messo in
                  cache; il file prodotto resta piccolo (~75 KB gzip).
                  E' la variante pubblicata in data/geography/.

Uscita: JSON con schema, fonte, licenza, griglia e vettore di quote int16.
La griglia e' CELL-CENTERED: la cella (ix, iy) copre
  lon in [lon0 + ix*dlon, lon0 + (ix+1)*dlon)
  lat in [lat0 + iy*dlat, lat0 + (iy+1)*dlat)
e il valore e' riferito al centro
  lon_c = lon0 + (ix+0.5)*dlon, lat_c = lat0 + (iy+0.5)*dlat.
Ordine del vettore: row-major, lat crescente (iy = 0 -> lat0), poi lon.

Uso:
  python scripts/geography/build_orography.py --mode points --res 0.1 \
      --out data/geography/orography_it_0p1.json
  python scripts/geography/build_orography.py --mode dem --res 0.05 \
      --out data/geography/orography_it_0p05.json

Il file e' deterministico: stessi input -> stessi byte (sha256 stabile).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import sys
import time
import urllib.request

import numpy as np

# --- riquadro Italia (come da specifica) -------------------------------------
BBOX = {"lon0": 4.0, "lat0": 35.0, "lon1": 20.0, "lat1": 48.0}

# --- DEM tiles ---------------------------------------------------------------
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE_ZOOM = 8
TILE_ATTRIBUTION = (
    "Terrain Tiles di Mapzen (progetto della Linux Foundation) su AWS Open Data. "
    "Sul suolo italiano a zoom 8 la fonte e' SRTM 1/3 arcsec: "
    "'SRTM data courtesy of the U.S. Geological Survey' (pubblico dominio, "
    "NASA/NGA/USGS). Batimetria ETOPO1 (NOAA, pubblico dominio) usata solo per "
    "il mare, azzerata in uscita. Il pacchetto tile non dichiara una licenza "
    "unica: valgono le attribuzioni per fonte di "
    "https://github.com/tilezen/joerd/blob/master/docs/attribution.md"
)
TILE_LICENSE = (
    "SRTM: pubblico dominio (17 U.S.C., nessun copyright rivendicato dal "
    "Governo USA); attribuzione richiesta e inclusa. ETOPO1: pubblico dominio "
    "(NOAA). Nessun vincolo di ridistribuzione noto per il prodotto derivato; "
    "attribuzione a USGS/NOAA/Mapzen obbligatoria."
)

# --- IDW ---------------------------------------------------------------------
IDW_POWER = 2.0
IDW_MAX_SUPPORT_KM = 60.0  # oltre: nessun dato (nodata), non si inventa quota
NODATA = -32768


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _grid(nx: int, ny: int, dlon: float, dlat: float):
    """Centri di cella (lon 2D, lat 2D) in ordine row-major lat crescente."""
    ix = np.arange(nx, dtype=np.float64)
    iy = np.arange(ny, dtype=np.float64)
    lon_c = BBOX["lon0"] + (ix + 0.5) * dlon
    lat_c = BBOX["lat0"] + (iy + 0.5) * dlat
    lon2d, lat2d = np.meshgrid(lon_c, lat_c)  # (ny, nx)
    return lon2d, lat2d


def _xy_km(lat: np.ndarray, lon: np.ndarray):
    """Proiezione equirettangolare locale in km (adeguata a scala Italia)."""
    lat_mid = math.radians(0.5 * (BBOX["lat0"] + BBOX["lat1"]))
    kx = 111.320 * math.cos(lat_mid)
    ky = 110.574
    return lon * kx, lat * ky


# =============================================================================
# (A) IDW dalle 257 quote di punto
# =============================================================================
def _idw(lat_q, lon_q, z_q, lat_c, lon_c, power=IDW_POWER, radius_km=IDW_MAX_SUPPORT_KM,
         exclude=None):
    """IDW locale: pesa solo le stazioni entro radius_km. Fuori -> NaN.

    Il taglio a 60 km evita che stazioni lontane (Alpi) contaminino aree di
    pianura: con IDW su tutte le 257 stazioni la Pianura Padana risultava
    196 m invece di ~45 m. Il peso al bordo (1/R^2) e' ~3e-4, quindi
    l'ingresso/uscita di una stazione al taglio non produce salti visibili.
    """
    xq, yq = _xy_km(lat_q, lon_q)
    xc, yc = _xy_km(lat_c, lon_c)
    shp = np.broadcast(lat_c, lon_c).shape
    xc = np.broadcast_to(xc, shp).ravel()
    yc = np.broadcast_to(yc, shp).ravel()
    out = np.full(xc.size, np.nan, dtype=np.float64)
    z_q = np.asarray(z_q, dtype=np.float64)
    # a blocchi per non allocare (ncells x npoints) interamente
    step = 4096
    for s in range(0, xc.size, step):
        e = min(s + step, xc.size)
        d = np.hypot(xc[s:e, None] - xq[None, :], yc[s:e, None] - yq[None, :])
        if exclude is not None:
            d = d.copy()
            d[:, exclude] = np.inf
        inside = d <= radius_km
        d = np.maximum(d, 1e-6)
        w = np.where(inside, 1.0 / (d ** power), 0.0)
        sw = w.sum(axis=1)
        ok = sw > 0
        res = np.full(e - s, np.nan)
        res[ok] = (w[ok] * z_q[None, :]).sum(axis=1) / sw[ok]
        out[s:e] = res
    return out.reshape(shp)


def build_points(points_path: str, res: float, verbose=True):
    t0 = time.time()
    with open(points_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    pts = doc["points"]
    lat_q = np.array([p["lat"] for p in pts], dtype=np.float64)
    lon_q = np.array([p["lon"] for p in pts], dtype=np.float64)
    z_q = np.array([p["elevation"] for p in pts], dtype=np.float64)
    if z_q.size != 257:
        raise SystemExit("attesi 257 punti, trovati %d" % z_q.size)

    nx = int(round((BBOX["lon1"] - BBOX["lon0"]) / res))
    ny = int(round((BBOX["lat1"] - BBOX["lat0"]) / res))
    lon2d, lat2d = _grid(nx, ny, res, res)

    flat = _idw(lat_q, lon_q, z_q, lat2d, lon2d)

    # distanza dal punto piu' vicino, per il supporto dichiarato
    nn = np.full(flat.size, np.inf)
    step = 4096
    xq, yq = _xy_km(lat_q, lon_q)
    xc, yc = _xy_km(lat2d, lon2d)
    xc = xc.ravel()
    yc = yc.ravel()
    for s in range(0, xc.size, step):
        e = min(s + step, xc.size)
        d = np.hypot(xc[s:e, None] - xq[None, :], yc[s:e, None] - yq[None, :])
        nn[s:e] = d.min(axis=1)
    nn = nn.reshape(flat.shape)

    # interpolazione ai punti noti, lasciando fuori il punto stesso (LOO)
    loo = np.empty(z_q.size)
    for i in range(z_q.size):
        loo[i] = _idw(lat_q, lon_q, z_q, np.array([lat_q[i]]), np.array([lon_q[i]]),
                      exclude=i)[0]
    err = loo - z_q
    if verbose:
        print("  A: %d stazioni, LOO MAE %.1f m, %.2f s" % (
            z_q.size, float(np.nanmean(np.abs(err))), time.time() - t0),
            file=sys.stderr, flush=True)

    grid = flat.copy()
    grid[np.isnan(grid)] = NODATA
    values = np.rint(grid).astype(np.int32)

    stats = {
        "loo_error_m": {
            "n": int(z_q.size),
            "mae": round(float(np.nanmean(np.abs(err))), 2),
            "rmse": round(float(np.sqrt(np.nanmean(err ** 2))), 2),
            "p90_abs": round(float(np.nanpercentile(np.abs(err), 90)), 2),
            "max_abs": round(float(np.nanmax(np.abs(err))), 2),
            "mean_signed": round(float(np.nanmean(err)), 2),
        },
        "station_spacing_km": {
            "nearest_neighbour_median": 29.3,
            "nearest_neighbour_max": 55.8,
        },
        "cells": int(values.size),
        "cells_nodata": int((values == NODATA).sum()),
        "cells_with_station_within_60km": int((nn <= IDW_MAX_SUPPORT_KM).sum()),
        "max_support_km": IDW_MAX_SUPPORT_KM,
    }

    meta = {
        "schema": "meteorisk.orography/1",
        "id": "orography_it_%s_idw257" % ("0p1" if res == 0.1 else "0p05"),
        "generated_at": None,
        "inputs": {
            "points_file": os.path.basename(points_path),
            "points_sha256": _sha256_file(points_path),
            "points_n": int(z_q.size),
        },
        "source": "meteorisk-points.json (campo points[i].elevation), 257 quote di punto",
        "source_detail": (
            "Quote di punto Open-Meteo GLO-90 (mediana 294 m, max 2563 m) sui "
            "capoluoghi/sentinelle gia' presenti in data/latest/meteorisk-points.json"
        ),
        "licence": (
            "Quote dal campo 'elevation' di Open-Meteo, che documenta l'uso di "
            "Copernicus DEM GLO-90 (release 2021). Open-Meteo Elevation API "
            "dichiara licenza CC BY 4.0 (openapi/elevation.yml); termini generali "
            "Open-Meteo per uso non commerciale (questo progetto e' non commerciale). "
            "Attribuzione obbligatoria: 'Copernicus DEM - ESA (doi:10.5270/ESA-c5d3d65)' "
            "e 'Dati: Open-Meteo.com'. Prodotto derivato: interpolazione IDW MeteoRisk."
        ),
        "attribution": (
            "Copernicus DEM - ESA (doi:10.5270/ESA-c5d3d65). Dati: Open-Meteo.com. "
            "Elaborazione: MeteoRisk - Light (script scripts/geography/build_orography.py)."
        ),
        "method": {
            "interpolation": "local inverse distance weighting",
            "power": IDW_POWER,
            "radius_km": IDW_MAX_SUPPORT_KM,
            "distance_metric": "equirettangolare locale in km (lat media 41.5)",
            "nodata": NODATA,
            "nodata_rule": "nessuna stazione entro %.0f km" % IDW_MAX_SUPPORT_KM,
        },
        "grid": {
            "registration": "cell_centered",
            "lon0": BBOX["lon0"],
            "lat0": BBOX["lat0"],
            "lon1": BBOX["lon1"],
            "lat1": BBOX["lat1"],
            "dlon": res,
            "dlat": res,
            "nx": nx,
            "ny": ny,
            "ordering": "row-major, lat crescente (iy=0 -> lat0), poi lon",
        },
        "units": "m",
        "dtype": "int16",
        "note": (
            "Interpolata da 257 quote di punto (Open-Meteo GLO-90), risoluzione "
            "effettiva ~30 km: NON e' un DEM. Griglia liscia, non rappresenta "
            "valli e creste; i massimi reali (es. Monte Bianco 4807 m) non sono "
            "presenti perche' nessuna stazione li campiona. Usare come descrittore "
            "di larga scala, non come maschera."
        ),
        "uses": [
            "descrittore upslope_component = w . grad(h) con vento NWP, per area; nessun effetto sull'intensita'",
            "base per un eventuale enhancement orografico futuro (richiede calibrazione: oggi n=0 etichette)",
            "NON come maschera: dove il radar non vede non c'e' eco, la maschera e' gia' nel dato",
        ],
        "stats": stats,
        "validation_samples": {},
    }
    for name, la, lo in [
        ("alpi_valtellina", 46.5, 10.0),
        ("pianura_padana", 45.0, 10.0),
        ("sicilia_interna_enna", 37.5, 14.0),
        ("monte_bianco_indicativo", 45.85, 6.85),
    ]:
        ix = int((lo - BBOX["lon0"]) / res - 0.5)
        iy = int((la - BBOX["lat0"]) / res - 0.5)
        ix = min(max(ix, 0), nx - 1)
        iy = min(max(iy, 0), ny - 1)
        meta["validation_samples"][name] = {
            "lat": la,
            "lon": lo,
            "cell": [ix, iy],
            "elevation_m": int(values[iy, ix]),
        }

    return meta, values, flat, nn


# =============================================================================
# (B) DEM tiles -> griglia
# =============================================================================
def _num_tiles(lon0, lon1, lat0, lat1, z):
    n = 2 ** z

    def xt(lon):
        return (lon + 180.0) / 360.0 * n

    def yt(lat):
        r = math.radians(lat)
        return (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * n

    return int(math.floor(xt(lon0))), int(math.floor(xt(lon1))), int(math.floor(yt(lat1))), int(math.floor(yt(lat0)))


def _tile_lonlat(z, x, y):
    n = 2 ** z
    lon_w = x / n * 360.0 - 180.0
    lon_e = (x + 1) / n * 360.0 - 180.0

    def lat_of(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * yy / n))))

    return lon_w, lon_e, lat_of(y + 1), lat_of(y)  # south, north


def _fetch_tile(z, x, y, cache_dir):
    path = os.path.join(cache_dir, "terrarium_z%d_%d_%d.png" % (z, x, y))
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            return path, f.read()
    url = TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": "MeteoRisk-orography/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path, data


def _decode_terrarium(png_bytes):
    from PIL import Image

    im = Image.open(io.BytesIO(png_bytes))
    a = np.asarray(im.convert("RGB")).astype(np.float32)
    return (a[:, :, 0] * 256.0 + a[:, :, 1] + a[:, :, 2] / 256.0) - 32768.0


def build_dem(res: float, cache_dir: str, verbose=True):
    t0 = time.time()
    z = TILE_ZOOM
    x0, x1, y0, y1 = _num_tiles(BBOX["lon0"], BBOX["lon1"], BBOX["lat0"], BBOX["lat1"], z)
    tiles = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    nx = int(round((BBOX["lon1"] - BBOX["lon0"]) / res))
    ny = int(round((BBOX["lat1"] - BBOX["lat0"]) / res))
    sumw = np.zeros((ny, nx), dtype=np.float64)
    sumwz = np.zeros((ny, nx), dtype=np.float64)
    cnt = np.zeros((ny, nx), dtype=np.int64)
    tile_hashes = {}
    nbytes = 0
    t_dl = 0.0
    for k, (x, y) in enumerate(tiles):
        t1 = time.time()
        path, data = _fetch_tile(z, x, y, cache_dir)
        t_dl += time.time() - t1
        nbytes += len(data)
        tile_hashes["%d/%d" % (x, y)] = hashlib.sha256(data).hexdigest()
        el = _decode_terrarium(data)
        el = np.maximum(el, 0.0)  # mare/sotto il livello del mare -> 0 m
        lon_w, lon_e, lat_s, lat_n = _tile_lonlat(z, x, y)
        npix = el.shape[0]
        lons = lon_w + (np.arange(el.shape[1]) + 0.5) / el.shape[1] * (lon_e - lon_w)
        # lat per riga (Mercator): riga 0 = nord
        ys = np.arange(npix)
        lats = np.degrees(np.arctan(np.sinh(
            np.pi * (1.0 - 2.0 * (y + (ys + 0.5) / npix) / (2 ** z)))))
        ix = np.floor((lons - BBOX["lon0"]) / res).astype(np.int64)
        iy = np.floor((lats - BBOX["lat0"]) / res).astype(np.int64)
        ok = (ix >= 0) & (ix < nx)
        w_row = np.cos(np.radians(lats))
        for r in range(npix):
            if not (0 <= iy[r] < ny):
                continue
            sel = ok
            if not sel.any():
                continue
            # peso = cos(lat) (area reale del pixel), valore = quota del pixel
            np.add.at(sumw[iy[r]], ix[sel], w_row[r])
            np.add.at(sumwz[iy[r]], ix[sel], w_row[r] * el[r, sel])
            np.add.at(cnt[iy[r]], ix[sel], 1)
        if verbose and (k + 1) % 20 == 0:
            print("  tile %d/%d" % (k + 1, len(tiles)), file=sys.stderr, flush=True)

    empty = cnt == 0
    z_grid = np.full((ny, nx), np.nan)
    valid = ~empty
    z_grid[valid] = sumwz[valid] / sumw[valid]

    # riempimento deterministico delle celle vuote con la media dei vicini validi
    if empty.any():
        for _ in range(4):
            if not empty.any():
                break
            pad = np.pad(np.where(np.isfinite(z_grid), z_grid, np.nan), 1,
                         mode="constant", constant_values=np.nan)
            stack = np.stack([pad[0:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, 0:-2], pad[1:-1, 2:]])
            with np.errstate(invalid="ignore"):
                nb = np.nanmean(stack, axis=0)
            fill = empty & np.isfinite(nb)
            z_grid[fill] = nb[fill]
            empty = empty & ~fill
    if empty.any():
        raise SystemExit("celle senza alcun pixel DEM: %d" % empty.sum())

    values = np.rint(z_grid).astype(np.int32)

    if verbose:
        print("  B: %d tile, %d B, %.1f s (download %.1f s)" % (
            len(tiles), nbytes, time.time() - t0, t_dl), file=sys.stderr, flush=True)

    cat = hashlib.sha256()
    for key in sorted(tile_hashes):
        cat.update(key.encode())
        cat.update(tile_hashes[key].encode())

    stats = {
        "zoom": z,
        "tiles": len(tiles),
        "tiles_bytes": int(nbytes),
        "tiles_sha256_concat": cat.hexdigest(),
        "tile_range": {"x": [x0, x1], "y": [y0, y1]},
        "cells": int(values.size),
        "cells_filled_from_neighbours": int((cnt == 0).sum()),
        "pixels_per_cell_median": int(np.median(cnt)),
    }

    meta = {
        "schema": "meteorisk.orography/1",
        "id": "orography_it_%s_dem" % ("0p05" if res == 0.05 else "0p1"),
        "generated_at": None,
        "inputs": {
            "tiles": len(tiles),
            "tiles_bytes": int(nbytes),
            "tiles_sha256_concat": cat.hexdigest(),
            "tile_range": {"x": [x0, x1], "y": [y0, y1]},
            "zoom": z,
        },
        "source": "Terrain Tiles (Mapzen/joerd) zoom %d, aggregati a %g gradi" % (z, res),
        "source_detail": (
            "A zoom 8 il suolo italiano proviene da SRTM 1/3 arcsec (~90 m), "
            "risoluzione pixel a 45N ~432 m. Ogni cella e' la media pesata "
            "(peso cos(lat)) dei pixel DEM contenuti; il mare e la batimetria "
            "ETOPO1 sono azzerati (max(0, quota))."
        ),
        "licence": TILE_LICENSE,
        "attribution": TILE_ATTRIBUTION,
        "method": {
            "aggregation": "media pesata cos(lat) dei pixel DEM nella cella",
            "sea_handling": "valori negativi (batimetria/ETOPO1) azzerati a 0 m",
            "nodata": None,
        },
        "grid": {
            "registration": "cell_centered",
            "lon0": BBOX["lon0"],
            "lat0": BBOX["lat0"],
            "lon1": BBOX["lon1"],
            "lat1": BBOX["lat1"],
            "dlon": res,
            "dlat": res,
            "nx": nx,
            "ny": ny,
            "ordering": "row-major, lat crescente (iy=0 -> lat0), poi lon",
        },
        "units": "m",
        "dtype": "int16",
        "note": (
            "DEM ridotto da SRTM (~90 m) a %g gradi: descrive la forma del "
            "rilievo a scala ~5-6 km, non valli/crinali singole. Il mare e' 0 m."
            % res
        ),
        "uses": [
            "descrittore upslope_component = w . grad(h) con vento NWP, per area; nessun effetto sull'intensita'",
            "base per un eventuale enhancement orografico futuro (richiede calibrazione: oggi n=0 etichette)",
            "NON come maschera: dove il radar non vede non c'e' eco, la maschera e' gia' nel dato",
        ],
        "stats": stats,
        "validation_samples": {},
    }
    for name, la, lo in [
        ("alpi_valtellina", 46.5, 10.0),
        ("pianura_padana", 45.0, 10.0),
        ("sicilia_interna_enna", 37.5, 14.0),
        ("monte_bianco", 45.85, 6.85),
        ("gran_sasso", 42.45, 13.55),
        ("sardegna_golfo_aranci", 40.05, 9.65),
        ("adriatico_medio", 43.0, 14.5),
    ]:
        ix = min(max(int((lo - BBOX["lon0"]) / res - 0.5), 0), nx - 1)
        iy = min(max(int((la - BBOX["lat0"]) / res - 0.5), 0), ny - 1)
        meta["validation_samples"][name] = {
            "lat": la,
            "lon": lo,
            "cell": [ix, iy],
            "elevation_m": int(values[iy, ix]),
        }

    return meta, values, z_grid, None


# =============================================================================
# scrittura
# =============================================================================
def write_json(meta, values, out_path):
    doc = dict(meta)
    doc["elevations_m"] = [int(v) for v in values.ravel()]
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(text.encode("utf-8"))
    raw = len(text.encode("utf-8"))
    gz = len(gzip.compress(text.encode("utf-8"), 9))
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return raw, gz, sha


def main(argv=None):
    ap = argparse.ArgumentParser(description="Orografia semplificata MeteoRisk")
    ap.add_argument("--mode", choices=["points", "dem"], required=True)
    ap.add_argument("--res", type=float, default=0.1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--points", default="data/latest/meteorisk-points.json")
    ap.add_argument("--tile-cache", default=".cache/orography_tiles")
    ap.add_argument("--generated-at", default=None,
                    help="ISO8601 da scrivere nel file (default: null, per output riproducibile)")
    args = ap.parse_args(argv)
    t_all = time.time()

    if args.mode == "points":
        meta, values, flat, nn = build_points(args.points, args.res)
    else:
        meta, values, z_grid, _ = build_dem(args.res, args.tile_cache)
    meta["generated_at"] = args.generated_at

    raw, gz, sha = write_json(meta, values, args.out)
    print("out=%s" % args.out)
    print("cells=%d raw=%d B gzip=%d B sha256=%s" % (values.size, raw, gz, sha))
    print("generation_seconds=%.3f" % (time.time() - t_all))
    print("stats=%s" % json.dumps(meta["stats"], ensure_ascii=False))
    print("samples=%s" % json.dumps(meta["validation_samples"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
