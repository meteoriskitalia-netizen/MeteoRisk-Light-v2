#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Costruzione volume 3D (z, y, x) impilando i 10 CAPPI del DPC.
Lavora SOLO in questa cartella (%TEMP%\\opencode\\vol3d\\). Nessuna scrittura nel repo.

Slot di riferimento: 2026-11-15 16:15:00 UTC (verificato in FASE 3D-1).
"""
import os
import time
import json
import datetime
import urllib.request

import numpy as np
import rasterio

BASE = os.path.dirname(os.path.abspath(__file__))
API = "https://radar-api.protezionecivile.it"
SLOT = datetime.datetime(2026, 11, 15, 16, 15, tzinfo=datetime.timezone.utc)
SLOT_MS = int(SLOT.timestamp() * 1000)
THROTTLE_S = 2.5

PRODUCTS = ["VMI"] + [f"CAPPI_{i}" for i in range(1, 11)]


def download_product(product):
    body = {"productType": product, "productDate": SLOT_MS}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API + "/downloadProduct", data=data,
        headers={"Content-Type": "application/json", "Accept": "*/*"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        meta = json.loads(r.read().decode("utf-8"))
    key = meta["key"]
    url = meta["url"]
    with urllib.request.urlopen(url, timeout=120) as r:
        content = r.read()
    out_path = os.path.join(BASE, f"{product}.tif")
    with open(out_path, "wb") as fh:
        fh.write(content)
    return product, out_path, key, len(content)


def tiff_datetime(ds):
    # prova diversi namespace/tag
    for ns in (None, "TIFF", "IMAGE_STRUCTURE"):
        try:
            tags = ds.tags(ns=ns) if ns else ds.tags()
        except Exception:
            tags = {}
        for k in ("TIFFTAG_DATETIME", "datetime", "DATETIME"):
            if k in tags and tags[k]:
                return tags[k]
    # rasterio profile / driver metadata
    try:
        return ds.tags().get("TIFFTAG_DATETIME")
    except Exception:
        return None


def inspect(path):
    with rasterio.open(path) as ds:
        arr = ds.read(1)
        info = {
            "path": path,
            "shape": (ds.height, ds.width),
            "dtype": str(ds.dtypes[0]),
            "count": ds.count,
            "crs": str(ds.crs),
            "transform": tuple(ds.transform)[:6],
            "nodata_declared": ds.nodata,
            "tiff_datetime": tiff_datetime(ds),
            "min_raw": float(np.nanmin(arr)) if arr.size else float("nan"),
            "max_raw": float(np.nanmax(arr)) if arr.size else float("nan"),
        }
        # conteggi sentinelle
        info["n_m9999"] = int(np.sum(arr == -9999))
        info["n_m9998"] = int(np.sum(arr == -9998))
        info["n_finite"] = int(np.sum(np.isfinite(arr)))
        return info, arr


def main():
    t0 = time.perf_counter()
    lines = []
    lines.append("=== COSTRUZIONE VOLUME 3D CAPPI DPC ===")
    lines.append(f"Slot riferimento: {SLOT.isoformat()}  ({SLOT_MS} ms)")
    lines.append(f"Cartella lavoro: {BASE}")
    lines.append("")

    # 1. Download
    lines.append("--- 1. DOWNLOAD PRODOTTI ---")
    download_t0 = time.perf_counter()
    downloaded = {}  # product -> path
    download_meta = {}
    for idx, prod in enumerate(PRODUCTS):
        try:
            product, path, key, nbytes = download_product(prod)
            downloaded[product] = path
            download_meta[product] = {"key": key, "bytes": nbytes, "ok": True}
            lines.append(f"  {prod}: OK  {key}  ({nbytes} B)")
        except Exception as e:
            download_meta[product] = {"ok": False, "error": repr(e)}
            lines.append(f"  {prod}: FALLITO  {repr(e)}")
        if idx < len(PRODUCTS) - 1:
            time.sleep(THROTTLE_S)
    download_elapsed = time.perf_counter() - download_t0
    lines.append(f"Tempo download totale: {download_elapsed:.3f} s")
    lines.append("")

    # 2. Lettura / ispezione
    lines.append("--- 2. ISPEZIONE GeoTIFF ---")
    infos = {}
    arrays = {}  # product -> np.ndarray float32
    for prod in PRODUCTS:
        if prod not in downloaded:
            continue
        info, arr = inspect(downloaded[prod])
        infos[prod] = info
        arrays[prod] = arr.astype(np.float32)
        lines.append(f"  {prod}:")
        lines.append(f"    shape={info['shape']} dtype={info['dtype']} bands={info['count']}")
        lines.append(f"    CRS={info['crs']}")
        lines.append(f"    transform={info['transform']}")
        lines.append(f"    nodata dichiarato={info['nodata_declared']}")
        lines.append(f"    TIFFTAG_DATETIME={info['tiff_datetime']}")
        lines.append(f"    min={info['min_raw']:.4f} max={info['max_raw']:.4f}")
        lines.append(f"    #(-9999)={info['n_m9999']}  #(-9998)={info['n_m9998']}  #finite={info['n_finite']}")

    # 3. Verifica griglia comune
    lines.append("")
    lines.append("--- 3. VERIFICA GRIGLIA COMUNE ---")
    ref_shape = infos["CAPPI_1"]["shape"]
    ref_crs = infos["CAPPI_1"]["crs"]
    ref_transform = infos["CAPPI_1"]["transform"]
    grid_ok = True
    grid_details = []
    for prod in [f"CAPPI_{i}" for i in range(1, 11)]:
        if prod not in infos:
            grid_ok = False
            grid_details.append(f"  {prod}: MANCANTE")
            continue
        ok = (infos[prod]["shape"] == ref_shape
              and infos[prod]["crs"] == ref_crs
              and infos[prod]["transform"] == ref_transform)
        grid_details.append(f"  {prod}: shape={infos[prod]['shape']} crs_ok={infos[prod]['crs']==ref_crs} "
                           f"transform_ok={infos[prod]['transform']==ref_transform}  -> {'OK' if ok else 'MISMATCH'}")
        if not ok:
            grid_ok = False
    for d in grid_details:
        lines.append(d)
    lines.append(f"Griglia comune (10 CAPPI): {'SI' if grid_ok else 'NO'}")
    lines.append(f"Shape di riferimento: {ref_shape}  CRS: {ref_crs}")
    lines.append("")

    # VMI info (non usato nel volume, ma scaricato/verificato)
    if "VMI" in infos:
        lines.append(f"VMI: shape={infos['VMI']['shape']} crs_ok={infos['VMI']['crs']==ref_crs} "
                     f"transform_ok={infos['VMI']['transform']==ref_transform}  datetime={infos['VMI']['tiff_datetime']}")
        lines.append("")

    # 4. Impilamento volume
    lines.append("--- 4. IMPILAMENTO VOLUME 3D ---")
    cap_names = [f"CAPPI_{i}" for i in range(1, 11)]
    orig_levels = [arrays[c].copy() for c in cap_names]   # float32 originali
    # maschera sentinelle -> NaN
    vol = np.stack(orig_levels, axis=0).astype(np.float32)  # (10,H,W)
    sentinel_mask = (vol == -9999.0) | (vol == -9998.0)
    vol = np.where(sentinel_mask, np.nan, vol).astype(np.float32)

    H, W = ref_shape
    elevations = np.array([float(i) for i in range(1, 11)], dtype=np.float64)  # km

    # 5. Interpolazione verticale lineare (VI scheme, Zhang 2005) su buchi singoli adiacenti
    lines.append("--- 5. INTERPOLAZIONE VERTICALE (VI scheme) ---")
    interp_mask = np.zeros(vol.shape, dtype=bool)
    n_interp = 0
    # lavoriamo su copia per non usare valori appena interpolati a cascata (solo adiacenti originali)
    vol_before_interp = vol.copy()
    for k in range(1, 9):  # livelli interni (indice 1..8)
        missing = np.isnan(vol_before_interp[k])
        above_ok = ~np.isnan(vol_before_interp[k - 1])
        below_ok = ~np.isnan(vol_before_interp[k + 1])
        cand = missing & above_ok & below_ok
        if not np.any(cand):
            continue
        # interpolazione lineare in quota
        z0 = elevations[k - 1]
        z1 = elevations[k]
        z2 = elevations[k + 1]
        # value = v_low + (v_high-v_low)*(z1-z0)/(z2-z0)
        vlow = vol_before_interp[k - 1]
        vhigh = vol_before_interp[k + 1]
        interp_vals = vlow + (vhigh - vlow) * ((z1 - z0) / (z2 - z0))
        # applica solo dove cand
        vol[k] = np.where(cand, interp_vals, vol[k]).astype(np.float32)
        interp_mask[k] = cand
        n_interp += int(np.sum(cand))
    lines.append(f"Buchi singoli interpolati (voxel): {n_interp}")
    lines.append("Nessuna estrapolazione sotto 1 km o sopra 10 km (NaN preservati).")
    lines.append("")

    # 6. Statistiche per livello
    lines.append("--- 6. STATISTICHE PER LIVELLO ---")
    lines.append(f"{'livello':>8} {'quota_km':>9} {'validi':>10} {'totale':>10} {'copertura':>10} {'min':>10} {'max':>10} {'interp':>10}")
    total_pixels = H * W
    for k in range(10):
        sl = vol[k]
        valid = np.isfinite(sl)
        n_valid = int(np.sum(valid))
        cov = n_valid / total_pixels
        vmin = float(np.nanmin(sl)) if n_valid > 0 else float("nan")
        vmax = float(np.nanmax(sl)) if n_valid > 0 else float("nan")
        n_i = int(np.sum(interp_mask[k]))
        lines.append(f"{k+1:>8} {k+1:>9} {n_valid:>10} {total_pixels:>10} {cov:>10.4f} {vmin:>10.4f} {vmax:>10.4f} {n_i:>10}")

    # 7. Verifica "nessun valore inventato"
    lines.append("")
    lines.append("--- 7. VERIFICA: NESSUN VALORE INVENTATO ---")
    # ricostruisci maschere originali valide
    orig_valid_mask = ~sentinel_mask  # (10,H,W) bool: True dove originale valido
    # a) posizioni originariamente valide devono coincidere esattamente
    mismatch_valid = 0
    for k in range(10):
        m = orig_valid_mask[k]
        if np.any(m):
            eq = (vol[k][m] == orig_levels[k][m])
            mismatch_valid += int(np.sum(~eq))
    lines.append(f"Voxel originariamente validi che non coincidono con l'originale: {mismatch_valid}")

    # b) posizioni interpolate: devono essere solo quelle con entrambi i vicini validi,
    #    e il valore deve essere l'interpolazione lineare dei due vicini originali.
    interp_positions_ok = True
    # verifica che interp_mask sia subset di (missing & above & below) originali
    for k in range(1, 9):
        missing = np.isnan(vol_before_interp[k])
        above_ok = ~np.isnan(vol_before_interp[k - 1])
        below_ok = ~np.isnan(vol_before_interp[k + 1])
        expected_cand = missing & above_ok & below_ok
        if not np.array_equal(interp_mask[k], expected_cand):
            interp_positions_ok = False
    lines.append(f"Posizioni interpolate coerenti con (missing & above & below validi): {'SI' if interp_positions_ok else 'NO'}")

    # c) valore interpolato corretto
    interp_value_mismatch = 0
    for k in range(1, 9):
        cand = interp_mask[k]
        if not np.any(cand):
            continue
        z0 = elevations[k - 1]; z1 = elevations[k]; z2 = elevations[k + 1]
        expected = vol_before_interp[k - 1] + (vol_before_interp[k + 1] - vol_before_interp[k - 1]) * ((z1 - z0) / (z2 - z0))
        got = vol[k]
        diff = np.abs(got - expected)
        bad = cand & (diff > 1e-6)  # tolleranza float
        interp_value_mismatch += int(np.sum(bad))
    lines.append(f"Valori interpolati che non coincidono con l'interpolazione lineare: {interp_value_mismatch}")

    # d) nessun valore "spiegato" da altro: ogni finito in vol deve essere o originale valido
    #    o interpolato (verificato sopra). Conta finiti non spiegati.
    unexplained = 0
    for k in range(10):
        finite = np.isfinite(vol[k])
        explained = orig_valid_mask[k] | interp_mask[k]
        unexplained += int(np.sum(finite & ~explained))
    lines.append(f"Voxel finiti non spiegati (ne' originali ne' interpolati): {unexplained}")

    # e) sentinel residue? nessun -9999/-9998 deve restare
    n_sent_residue = int(np.sum((vol == -9999) | (vol == -9998)))
    lines.append(f"Sentinelle (-9999/-9998) residue nel volume: {n_sent_residue}")

    all_ok = (mismatch_valid == 0 and interp_positions_ok and interp_value_mismatch == 0
              and unexplained == 0 and n_sent_residue == 0 and grid_ok)
    lines.append(f"ESITO VERIFICA: {'PASS' if all_ok else 'FAIL'}")
    lines.append("")

    # 8. Salvataggio
    out_npy = os.path.join(BASE, "volume.npy")
    np.save(out_npy, vol.astype(np.float32))
    # salva anche la maschera di interpolazione per audit (opzionale ma utile)
    np.save(os.path.join(BASE, "interpolated_mask.npy"), interp_mask)
    lines.append("--- 8. SALVATAGGIO ---")
    lines.append(f"Volume salvato: {out_npy}  shape={vol.shape} dtype={vol.dtype}")
    lines.append(f"Maschera interpolazione: {os.path.join(BASE, 'interpolated_mask.npy')}")
    lines.append("")

    total_elapsed = time.perf_counter() - t0
    lines.append("--- TEMPI ---")
    lines.append(f"Tempo download: {download_elapsed:.3f} s")
    lines.append(f"Tempo totale (download + costruzione + verifica): {total_elapsed:.3f} s")
    lines.append("")
    lines.append("Riferimenti: TITAN (Dixon & Wiener 1993) stacking CAPPI; Zhang et al. 2005 VI scheme; Doviak & Zrnic 1993 beam height.")

    report_path = os.path.join(BASE, "report.txt")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nReport scritto in: {report_path}")


if __name__ == "__main__":
    main()
