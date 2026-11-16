#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sezione verticale (vertical cross-section) da un volume 3D CAPPI.

Lavora SOLO in %TEMP%\\opencode\\vol3d. Nessuna scrittura nel repo.

Input:
  - volume.npy   shape (10, 1400, 1200) float32, dBZ, quote 1..10 km
  - VMI.tif      GeoTIFF DPC, CRS Transverse Mercator (lat0=42, lon0=12.5, scale 1)

Funzioni principali:
  - extract_cross_section(volume, lat1, lon1, lat2, lon2, n_points=200)
  - render_cross_section(profile, output_path, ...)

Riferimenti:
  - Zhang et al. 2005: VI scheme (interpolazione verticale lineare)
  - Doviak & Zrnic 1993: beam height / geometria del volume radar
  - nearest-neighbor sampling a 1 km (nessun valore inventato)
"""
import os
import time
import json
import warnings

warnings.filterwarnings("ignore")

import numpy as np
import rasterio
from rasterio.transform import Affine
from pyproj import Transformer
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib import cm

BASE = r"C:\Users\aless\AppData\Local\Temp\opencode\vol3d"
VOL_PATH = os.path.join(BASE, "volume.npy")
VMI_PATH = os.path.join(BASE, "VMI.tif")
PARAMS_JSON = os.path.join(BASE, "parametri_3d.json")

# Quote (km) del volume: livello i (0-based) -> i+1 km
ELEVATIONS_KM = np.array([float(i + 1) for i in range(10)], dtype=np.float64)

# Proiezione iniettabile (per uso nel motore radar, dove VMI_PATH non esiste).
_CRS_WKT = None
_PROJ_SET = False


def _load_crs_and_transform():
    with rasterio.open(VMI_PATH) as ds:
        crs = ds.crs
        transform = ds.transform
        H, W = ds.height, ds.width
    return crs, transform, H, W


# Transformer lazily created
_CRS = None
_TRANSFORM = None
_H = None
_W = None
_FWD = None  # lat/lon -> x/y (metri)  (always_xy=True -> lon,lat)
_INV = None  # x/y -> lat/lon


def _ensure_proj():
    global _CRS, _TRANSFORM, _H, _W, _FWD, _INV, _CRS_WKT, _PROJ_SET
    if _PROJ_SET:
        return
    if _CRS is None:
        _CRS, _TRANSFORM, _H, _W = _load_crs_and_transform()
        _CRS_WKT = _CRS.to_wkt()
    if _FWD is None:
        _FWD = Transformer.from_crs("EPSG:4326", _CRS_WKT, always_xy=True)
        _INV = Transformer.from_crs(_CRS_WKT, "EPSG:4326", always_xy=True)


def set_projection(crs_wkt, transform, H, W):
    """Inietta la proiezione VMI (per uso nel motore radar senza VMI_PATH)."""
    global _CRS, _TRANSFORM, _H, _W, _FWD, _INV, _CRS_WKT, _PROJ_SET
    _CRS = None
    _CRS_WKT = str(crs_wkt)
    _TRANSFORM = transform
    _H = int(H)
    _W = int(W)
    _FWD = Transformer.from_crs("EPSG:4326", _CRS_WKT, always_xy=True)
    _INV = Transformer.from_crs(_CRS_WKT, "EPSG:4326", always_xy=True)
    _PROJ_SET = True


def reset_projection():
    """Ripristina lo stato (usa VMI_PATH al prossimo _ensure_proj)."""
    global _CRS, _TRANSFORM, _H, _W, _FWD, _INV, _CRS_WKT, _PROJ_SET
    _CRS = None
    _TRANSFORM = None
    _H = None
    _W = None
    _FWD = None
    _INV = None
    _CRS_WKT = None
    _PROJ_SET = False


def latlon_to_xy(lat, lon):
    """lat,lon (gradi) -> x,y (metri) nella proiezione TM del VMI."""
    _ensure_proj()
    x, y = _FWD.transform(lon, lat)
    return float(x), float(y)


def xy_to_latlon(x, y):
    _ensure_proj()
    lon, lat = _INV.transform(x, y)
    return float(lat), float(lon)


def xy_to_rc(x, y):
    """x,y (metri) -> (row, col) float nella griglia VMI (1 km/pixel)."""
    _ensure_proj()
    col = (x - _TRANSFORM.c) / _TRANSFORM.a
    row = (y - _TRANSFORM.f) / _TRANSFORM.e
    return float(row), float(col)


def rc_to_xy(row, col):
    _ensure_proj()
    x = _TRANSFORM.c + _TRANSFORM.a * col + _TRANSFORM.b * row
    y = _TRANSFORM.f + _TRANSFORM.d * col + _TRANSFORM.e * row
    return float(x), float(y)


def latlon_to_rc(lat, lon):
    x, y = latlon_to_xy(lat, lon)
    return xy_to_rc(x, y)


def rc_to_latlon(row, col):
    x, y = rc_to_xy(row, col)
    return xy_to_latlon(x, y)


def clamp_rc(row, col):
    _ensure_proj()
    r = int(np.clip(np.rint(row), 0, _H - 1))
    c = int(np.clip(np.rint(col), 0, _W - 1))
    return r, c


def extract_cross_section(volume, lat1, lon1, lat2, lon2, n_points=200):
    """
    Estrae una sezione verticale lungo il segmento (lat1,lon1)->(lat2,lon2).

    Conversione: lat/lon -> x/y (TM, metri) -> row/col griglia (1 km).
    Campionamento: n_points equispaziati lungo il segmento in griglia (x,y);
    per ogni punto, per ogni livello z, nearest-neighbor (round row/col, clamp).

    Ritorna dict:
      profile   : np.ndarray (n_points, 10) float32 dBZ (NaN dove non valido)
      dist_km   : np.ndarray (n_points,) distanza cumulativa lungo la linea (km)
      x_km      : np.ndarray (n_points,) coordinata est (km griglia, origine 0 = centro)
      y_km      : np.ndarray (n_points,) coordinata nord (km griglia)
      z_km      : np.ndarray (10,) quote (km)
      row       : np.ndarray (n_points,) int indice riga griglia
      col       : np.ndarray (n_points,) int indice colonna griglia
      lat       : np.ndarray (n_points,) latitudine campionata
      lon       : np.ndarray (n_points,) longitudine campionata
      lat1, lon1, lat2, lon2 : estremi input
      n_points  : int
    """
    _ensure_proj()
    volume = np.asarray(volume)
    nz, H, W = volume.shape
    assert nz == 10, f"volume deve avere 10 livelli, ha {nz}"
    assert H == _H and W == _W, f"shape volume {(H, W)} != griglia VMI {(_H, _W)}"

    # estremi in griglia (km): x_km = (col), y_km = (row) ma in km con origine centro?
    # Usiamo coordinate griglia in "km dal centro" per comodita': col index (0..W-1) con 0=-600km? 
    # Meglio: coordinate in km nel sistema griglia: X_km = col (0..W-1) espresso come km est da bordo ovest?
    # Il task chiede "coordinate x/y/z di ogni punto". Usiamo x,y in km nel sistema della griglia
    # (1 pixel = 1 km), con origine all'angolo NW? Per coerenza con la proiezione, usiamo metri/1000.
    # Qui salviamo x,y in METRI/1000 (km) nel CRS (origine proiezione = Roma 42N,12.5E).
    r1f, c1f = latlon_to_rc(lat1, lon1)
    r2f, c2f = latlon_to_rc(lat2, lon2)
    x1, y1 = rc_to_xy(r1f, c1f)  # metri
    x2, y2 = rc_to_xy(r2f, c2f)

    # punti equispaziati in x,y (metri)
    ts = np.linspace(0.0, 1.0, n_points)
    xs_m = x1 + ts * (x2 - x1)
    ys_m = y1 + ts * (y2 - y1)

    # converti in row/col float
    rows_f = np.array([xy_to_rc(x, y)[0] for x, y in zip(xs_m, ys_m)], dtype=np.float64)
    cols_f = np.array([xy_to_rc(x, y)[1] for x, y in zip(xs_m, ys_m)], dtype=np.float64)

    # nearest neighbor + clamp
    rows_i = np.array([clamp_rc(r, c)[0] for r, c in zip(rows_f, cols_f)], dtype=np.int64)
    cols_i = np.array([clamp_rc(r, c)[1] for r, c in zip(rows_f, cols_f)], dtype=np.int64)

    # profile
    profile = np.empty((n_points, nz), dtype=np.float32)
    for i in range(n_points):
        profile[i, :] = volume[:, rows_i[i], cols_i[i]]

    # distanza cumulativa (km): euclidea tra punti consecutivi in griglia (1 px=1km)
    # usa coordinate griglia in km (metri/1000) per distanza reale
    pts_km = np.column_stack([xs_m / 1000.0, ys_m / 1000.0])
    seg = np.diff(pts_km, axis=0)
    seglen = np.sqrt((seg ** 2).sum(axis=1))
    dist_km = np.concatenate([[0.0], np.cumsum(seglen)])

    # lat/lon campionati
    lats = np.array([xy_to_latlon(x, y)[0] for x, y in zip(xs_m, ys_m)], dtype=np.float64)
    lons = np.array([xy_to_latlon(x, y)[1] for x, y in zip(xs_m, ys_m)], dtype=np.float64)

    return {
        "profile": profile,
        "dist_km": dist_km.astype(np.float64),
        "x_km": (xs_m / 1000.0).astype(np.float64),
        "y_km": (ys_m / 1000.0).astype(np.float64),
        "z_km": ELEVATIONS_KM.copy(),
        "row": rows_i,
        "col": cols_i,
        "lat": lats,
        "lon": lons,
        "lat1": float(lat1),
        "lon1": float(lon1),
        "lat2": float(lat2),
        "lon2": float(lon2),
        "n_points": int(n_points),
    }


def _radar_colormap():
    """Colormap dBZ 0-70 simile a VMI (bande discrete)."""
    # colori per bande 0,5,10,...,70
    colors = [
        "#ffffff",  # 0
        "#e8f5e9",  # 5
        "#c8e6c9",
        "#a5d6a7",
        "#81c784",
        "#4caf50",  # 25
        "#2e7d32",
        "#1b5e20",
        "#fff176",  # 40? giallo
        "#ffb74d",
        "#ff9800",  # 50 arancio
        "#f44336",
        "#c62828",  # 60 rosso
        "#880e4f",  # 65
        "#4a148c",  # 70 viola
    ]
    # costruisci 15 bande per 0..70 (step 5)
    cmap = ListedColormap(colors)
    cmap.set_bad(color="#d9d9d9")  # NaN -> grigio chiaro
    return cmap


def render_cross_section(profile, output_path, dist_km=None, elevations=None,
                        title=None, vmin=0.0, vmax=70.0):
    """
    Genera PNG della sezione verticale.

    profile: ndarray (n_points, nz) oppure dict da extract_cross_section.
    output_path: percorso PNG.
    """
    if isinstance(profile, dict):
        d = profile
        prof = np.asarray(d["profile"])
        if dist_km is None:
            dist_km = d.get("dist_km")
        if elevations is None:
            elevations = d.get("z_km", d.get("elevations"))
        if title is None:
            title = d.get("title")
    else:
        prof = np.asarray(profile)

    prof = np.asarray(prof, dtype=np.float64)
    n_points, nz = prof.shape
    if dist_km is None:
        dist_km = np.arange(n_points, dtype=np.float64)
    else:
        dist_km = np.asarray(dist_km, dtype=np.float64)
    if elevations is None:
        elevations = np.arange(1, nz + 1, dtype=np.float64)
    else:
        elevations = np.asarray(elevations, dtype=np.float64)

    cmap = _radar_colormap()

    fig, ax = plt.subplots(figsize=(10, 5.5))
    # masked per NaN
    masked = np.ma.masked_invalid(prof.T)  # (nz, n_points)
    extent = [float(dist_km[0]), float(dist_km[-1]),
              float(elevations[0]) - 0.5, float(elevations[-1]) + 0.5]
    im = ax.imshow(masked, origin="lower", aspect="auto", extent=extent,
                   cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
    # isolinee
    try:
        cs = ax.contour(dist_km, elevations, prof.T, levels=[20, 30, 40, 50],
                        colors="black", linewidths=0.9)
        ax.clabel(cs, inline=True, fontsize=8, fmt="%d")
    except Exception:
        pass
    # assi
    ax.set_xlabel("Distanza lungo la linea (km)")
    ax.set_ylabel("Quota (km)")
    if title:
        ax.set_title(title)
    # ticks quote
    ax.set_yticks(elevations)
    # colorbar
    cbar = fig.colorbar(im, ax=ax, shrink=0.85)
    cbar.set_label("dBZ")
    # griglia leggera
    ax.grid(True, linestyle=":", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)
    return output_path


def verify_no_invented(profile_dict, volume):
    """
    Verifica che ogni valore della sezione coincida con il CAPPI originale
    (nearest neighbor). Ritorna (n_mismatch, n_nan_mismatch, total).
    """
    prof = np.asarray(profile_dict["profile"])
    rows = profile_dict["row"]
    cols = profile_dict["col"]
    n_points, nz = prof.shape
    n_mismatch = 0
    n_nan_mismatch = 0
    total = n_points * nz
    for i in range(n_points):
        r = int(rows[i]); c = int(cols[i])
        for z in range(nz):
            got = prof[i, z]
            exp = volume[z, r, c]
            got_nan = (isinstance(got, float) and np.isnan(got)) or (hasattr(got, "dtype") and np.isnan(got))
            exp_nan = np.isnan(exp) if (isinstance(exp, (float, np.floating)) ) else False
            if got_nan != bool(exp_nan):
                n_nan_mismatch += 1
                continue
            if not got_nan and not exp_nan:
                if got != exp:  # exact (float32 stored == float32 original)
                    # tolleranza per cast
                    if not np.isclose(got, exp, rtol=0, atol=0):
                        n_mismatch += 1
    return n_mismatch, n_nan_mismatch, total


def _pick_convective_line():
    """Sceglie una linea che attraversa la cella convettiva con max VMI dBZ."""
    with open(PARAMS_JSON, "r", encoding="utf-8") as fh:
        params = json.load(fh)
    cells = params["cells"]
    # cella con max_dbz_vmi massimo
    best = max(cells, key=lambda cc: cc.get("max_dbz_vmi") or 0.0)
    cr, cc = best["centroid_rc_weighted"]  # row, col
    # diagonale +-40 px
    d = 40
    r1, c1 = cr - d, cc - d
    r2, c2 = cr + d, cc + d
    # clamp
    _ensure_proj()
    r1 = float(np.clip(r1, 0, _H - 1)); c1 = float(np.clip(c1, 0, _W - 1))
    r2 = float(np.clip(r2, 0, _H - 1)); c2 = float(np.clip(c2, 0, _W - 1))
    lat1, lon1 = rc_to_latlon(r1, c1)
    lat2, lon2 = rc_to_latlon(r2, c2)
    return lat1, lon1, lat2, lon2, best


def _motion_line(rec, length_km=60.0):
    """Restituisce (lat1,lon1,lat2,lon2) lungo la direzione del moto."""
    import math
    pos = rec.get("position")
    if not (isinstance(pos, (list, tuple)) and len(pos) >= 2):
        return None
    lon0 = float(pos[0]); lat0 = float(pos[1])
    motion = rec.get("motion") or {}
    dir_deg = motion.get("direction_toward_deg")
    if dir_deg is None:
        return None
    dir_deg = float(dir_deg)
    R = 6371.0
    half = length_km / 2.0
    # punto di partenza: half km nella direzione opposta
    d = half / R
    brng = math.radians((dir_deg + 180.0) % 360.0)
    lat1 = math.asin(math.sin(math.radians(lat0)) * math.cos(d)
                      + math.cos(math.radians(lat0)) * math.sin(d) * math.cos(brng))
    lon1 = math.radians(lon0) + math.atan2(
        math.sin(brng) * math.sin(d) * math.cos(math.radians(lat0)),
        math.cos(d) - math.sin(math.radians(lat0)) * math.sin(lat1))
    lat1 = math.degrees(lat1); lon1 = math.degrees(lon1)
    # punto di arrivo: half km nella direzione del moto
    brng2 = math.radians(dir_deg)
    lat2 = math.asin(math.sin(math.radians(lat0)) * math.cos(d)
                      + math.cos(math.radians(lat0)) * math.sin(d) * math.cos(brng2))
    lon2 = math.radians(lon0) + math.atan2(
        math.sin(brng2) * math.sin(d) * math.cos(math.radians(lat0)),
        math.cos(d) - math.sin(math.radians(lat0)) * math.sin(lat2))
    lat2 = math.degrees(lat2); lon2 = math.degrees(lon2)
    return lat1, lon1, lat2, lon2


def generate_cross_sections(convective_structures, volume, output_dir, proj=None):
    """Genera PNG di sezione verticale per ogni struttura con forecast e
    track_coherent=True. Aggiunge i campi cross_section_* al record.

    Degradazione pulita se volume e' None o i CAPPI mancano.
    """
    results = []
    out_cs_dir = os.path.join(output_dir, "cross_sections")
    try:
        os.makedirs(out_cs_dir, exist_ok=True)
    except Exception:
        pass

    if volume is None:
        for rec in (convective_structures or []):
            rec["cross_section_path"] = None
            rec["cross_section_available"] = False
            rec["cross_section_null_reason"] = "volume_unavailable"
            rec["cross_section_source"] = "cross_section"
            rec["cross_section_reference"] = "cross_section.py (volume 3D CAPPI)"
        return results

    if proj is not None:
        set_projection(proj["crs_wkt"], proj["transform"], proj["H"], proj["W"])
    else:
        _ensure_proj()

    for rec in (convective_structures or []):
        forecast = rec.get("forecast")
        coherent = bool(rec.get("track_coherent"))
        if not (forecast is not None and coherent):
            rec["cross_section_path"] = None
            rec["cross_section_available"] = False
            rec["cross_section_null_reason"] = "no_forecast_or_incoherent"
            rec["cross_section_source"] = "cross_section"
            rec["cross_section_reference"] = "cross_section.py"
            continue
        line = _motion_line(rec, length_km=60.0)
        if line is None:
            rec["cross_section_path"] = None
            rec["cross_section_available"] = False
            rec["cross_section_null_reason"] = "no_position_or_motion_direction"
            rec["cross_section_source"] = "cross_section"
            rec["cross_section_reference"] = "cross_section.py"
            continue
        lat1, lon1, lat2, lon2 = line
        try:
            sec = extract_cross_section(volume, lat1, lon1, lat2, lon2, n_points=200)
            mm, nn, tot = verify_no_invented(sec, volume)
            title = "Sezione verticale - struttura {}".format(
                rec.get("system_key") or rec.get("track_id"))
            sec["title"] = title
            png_name = "cross_section_{}.png".format(rec.get("track_id", "unknown"))
            png_path = os.path.join(out_cs_dir, png_name)
            render_cross_section(sec, png_path, title=title)
            rec["cross_section_path"] = os.path.relpath(png_path, output_dir)
            rec["cross_section_available"] = True
            rec["cross_section_null_reason"] = None
            rec["cross_section_mismatch"] = int(mm)
            rec["cross_section_nan_mismatch"] = int(nn)
            rec["cross_section_total"] = int(tot)
            rec["cross_section_source"] = "cross_section"
            rec["cross_section_reference"] = ("cross_section.py (Zhang 2005 VI; "
                                            "Doviak & Zrnic 1993)")
            results.append({"track_id": rec.get("track_id"), "png": png_path,
                            "mismatch": int(mm), "nan_mismatch": int(nn),
                            "total": int(tot)})
        except Exception as exc:
            rec["cross_section_path"] = None
            rec["cross_section_available"] = False
            rec["cross_section_null_reason"] = "error:{}:{}".format(
                exc.__class__.__name__, exc)
            rec["cross_section_source"] = "cross_section"
    return results


def main():
    t_start = time.perf_counter()
    _ensure_proj()
    volume = np.load(VOL_PATH).astype(np.float32)
    print("Volume:", volume.shape, volume.dtype)

    lines_spec = []

    # (a) cella convettiva
    lat1, lon1, lat2, lon2, best = _pick_convective_line()
    lines_spec.append(("convective", lat1, lon1, lat2, lon2,
                      f"Sezione verticale - cella convettiva (cell_id={best['cell_id']}, max VMI {best['max_dbz_vmi']} dBZ)"))

    # (b) orizzontale E-W a latitudine fissa
    lines_spec.append(("horizontal", 45.0, 7.0, 45.0, 15.0,
                      "Sezione verticale - linea orizzontale E-W a lat 45 N"))

    # (c) diagonale
    lines_spec.append(("diagonal", 46.0, 7.0, 38.0, 16.0,
                      "Sezione verticale - linea diagonale NW-SE"))

    results = []
    for tag, la1, lo1, la2, lo2, title in lines_spec:
        t0 = time.perf_counter()
        sec = extract_cross_section(volume, la1, lo1, la2, lo2, n_points=200)
        sec["title"] = title
        dt = time.perf_counter() - t0
        mm, nn, tot = verify_no_invented(sec, volume)
        print(f"[{tag}] estrazione {dt:.3f}s | mismatch={mm} nan_mismatch={nn} / {tot}")
        # salva profilo
        np.save(os.path.join(BASE, f"profile_{tag}.npy"), sec["profile"])
        np.savez(os.path.join(BASE, f"coords_{tag}.npz"),
                 dist_km=sec["dist_km"], x_km=sec["x_km"], y_km=sec["y_km"],
                 z_km=sec["z_km"], row=sec["row"], col=sec["col"],
                 lat=sec["lat"], lon=sec["lon"])
        # render
        out_png = os.path.join(BASE, f"cross_section_{tag}.png")
        t1 = time.perf_counter()
        render_cross_section(sec, out_png, title=title)
        dt_r = time.perf_counter() - t1
        print(f"  render {dt_r:.3f}s -> {out_png}")
        results.append((tag, sec, dt, dt_r, mm, nn, tot, out_png))

    # report
    report_path = os.path.join(BASE, "report_cross_section.txt")
    lines = []
    lines.append("=== REPORT SEZIONE VERTICALE (vertical cross-section) ===")
    lines.append(f"Slot volume: 2026-11-15T16:15:00Z  (CAPPI DPC)")
    lines.append(f"Volume: shape={volume.shape} dtype={volume.dtype}  quote 1..10 km")
    lines.append(f"Griglia VMI: {_H}x{_W} px, 1 km/px, CRS TM lat0=42 lon0=12.5")
    lines.append(f"Metodo: lat/lon -> TM (metri) -> row/col griglia -> nearest neighbor (1 km).")
    lines.append(f"Nessun valore inventato: i valori sono copiati dai voxel CAPPI originali")
    lines.append(f"(incluse le interpolazioni VI gia' presenti nel volume).")
    lines.append("")
    lines.append("--- TEMPI ---")
    for tag, sec, dt, dt_r, mm, nn, tot, out_png in results:
        lines.append(f"  [{tag}] estrazione: {dt:.3f}s | render: {dt_r:.3f}s | PNG: {os.path.basename(out_png)}")
    lines.append(f"  tempo totale: {time.perf_counter() - t_start:.3f}s")
    lines.append("")
    lines.append("--- DIMENSIONI ---")
    for tag, sec, dt, dt_r, mm, nn, tot, out_png in results:
        prof = sec["profile"]
        import os as _os
        sz = _os.path.getsize(out_png)
        lines.append(f"  [{tag}] profile shape={prof.shape} dtype={prof.dtype} | "
                     f"dist 0..{sec['dist_km'][-1]:.1f} km | PNG {sz} byte")
    lines.append("")
    lines.append("--- VERIFICA NO VALORI INVENTATI ---")
    for tag, sec, dt, dt_r, mm, nn, tot, out_png in results:
        ok = (mm == 0 and nn == 0)
        lines.append(f"  [{tag}] mismatch={mm} nan_mismatch={nn} / {tot} -> {'PASS' if ok else 'FAIL'}")
    lines.append("")
    lines.append("Coordinate campionate salvate in coords_<tag>.npz (row,col,dist_km,lat,lon,...).")
    lines.append("Profili salvati in profile_<tag>.npy.")
    lines.append("Riferimenti: Zhang et al. 2005 (VI scheme); Doviak & Zrnic 1993 (beam height).")

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
