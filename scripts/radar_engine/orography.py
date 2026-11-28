#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine â€” orography.py (novembre 2026)

ENHANCEMENT OROGRAFICO DEL NOWCAST (componente di pendenza, Alpert & Shafir).

Cosa fa
-------
Applica al CAMPO TRASPORTATO del nowcast (mm/h) una correzione additiva
calcolata dalla componente di pendenza del vento:

    upslope = u * dh/dx + v * dh/dy        [m/s]

    E = (RH * e_s(T) * upslope) / (R_v * T) * 3600        [mm/h]

dove `e_s` e' la tensione di vapore saturo (Magnus/Tetens, Pa), `R_v` =
461,5 J/(kg K), T in K. La formula e' quella usata per l'enhancement
orografico in IMPROVER/STEPS (Alpert & Shafir 1989): il risultato numerico di
quell'espressione, in kg/(m^3 h), coincide con mm/h perche' 1 kg/m^3 su 1 m di
spessore e' 1 mm d'acqua. Verificato a mano: RH 0,9, T 280 K, v*gradh 0,1 1/s
-> 2,48 mm/h.

Perche' si applica DOPO il trasporto e PRIMA di alpha/statistiche
------------------------------------------------------------------
`nowcast_field.compute` trasporta il campo con il semi-Lagrangiano per ogni
lead; la correzione entra sul frame gia' trasportato di quel lead, prima di
`_lead_stats` e della rampa alpha (`_rgba`). Il cap 110 km/h riguarda il
VETTORE di moto e resta a monte, invariato.

Ingressi (tutti gia' nel repo, nessun download nuovo)
-----------------------------------------------------
* quota:  `data/geography/orography_it_0p05.json` (path storico del prodotto)
  â€” schema `meteorisk.orography/1`, DEM SRTM (USGS, pubblico dominio)
  ridotto a 0,02 deg (520.000 celle, 800x650, uint8 quantizzato a 20 m e
  compresso LZMA2+base64, row-major lat CRESCENTE, 0 = mare). Pubblicato su
  `main` dal BUILD=228: 181.770 B raw / 139.772 B gzip, sha256 17db2272...
  fea35e. Il loader decodifica e gira l'array in north-up UNA volta
  (costo ~20-50 ms) e da li' in poi la griglia e' sempre (riga 0 = lat
  massima);
* ambiente: `data/latest/meteorisk-points.json` + `data/latest/metadata.json`
  (Open-Meteo best_match, 257 punti, ora 0 = `time_base` locale) â€” campi
  `temperature_2m`, `relativehumidity_2m`, `windspeed_10m`, `winddirection_10m`.

Se la quota o l'ambiente mancano (o l'ambiente e' piu' vecchio di
`max_env_age_minutes`, default 240) il nowcast NON viene corretto: il blocco
`orographic_enhancement` del JSON porta `applied: false` e un `null_reason`
esplicito. Mai una correzione a zero spacciata per "calcolata".

Condizioni di attivazione (dichiarate, non tarate)
--------------------------------------------------
1. media 3x3 della quota >= `min_mean_elev_m` (20 m): sotto, l'assunzione
   orografica non vale (delta, lagune, mare);
2. RH >= `rh_threshold` (0,80);
3. componente di pendenza positiva >= `min_upslope_ms` (0,0005 m/s), presa
   come MASSIMO su un intorno SOPRAVVENTO fino a `upwind_km` (15 km);
4. il pixel ha precipitazione trasportata >= `rain_min_mmh` (0,05 mm/h):
   l'enhancement INTENSIFICA la pioggia prevista, non ne inventa dove il
   radar non vede nulla. E' una scelta dichiarata (v. LIMITS).

Sottovento
----------
Con upslope negativo si applica la STESSA formula in attenuazione, con il
limite dichiarato `lee_floor_frac` (0,50): il valore non scende mai sotto il
50% del campo trasportato. Il limite e' una scelta ingegneristica, non una
misura.

Riferimenti
-----------
Alpert & Shafir 1989 (orographic enhancement, formulazione usata in
IMPROVER/STEPS), Magnus/Tetens per e_s. La trasposizione al NOWCAST (0-60 min)
NON e' validata: la formula nasce per QPE climatologica, non per una
correzione a 5 minuti. Va nel campo `reference`, e l'esito A/B sta in
`data/radar/nowcast_verification.json`.
"""

import base64
import json
import lzma
import math
import os

import numpy as np

SCHEMA = "meteorisk.orography_enhancement/1"
GRID_SCHEMA = "meteorisk.orography/1"

DEFAULTS = {
    "enabled": True,
    "grid_file": "data/geography/orography_it_0p05.json",
    "env_points_file": "data/latest/meteorisk-points.json",
    "env_metadata_file": "data/latest/metadata.json",
    "max_env_age_minutes": 240,
    "min_mean_elev_m": 20.0,
    "rh_threshold": 0.80,
    "min_upslope_ms": 0.0005,
    "upwind_km": 15.0,
    "upwind_steps": 4,
    "lee_floor_frac": 0.50,
    "max_relative_increase": 1.0,
    "rain_min_mmh": 0.05,
    "idw_neighbors": 8,
    "idw_max_km": 200.0,
    "rv": 461.5,
    # Livello del vento usato per `v . grad(h)`:
    #   "10m"      -> windspeed_10m / winddirection_10m (best_match)
    #   "850_700"  -> media VETTORIALE pesata per massa 850/700 hPa, con gli
    #                 stessi pesi di `nwp_motion.LEVEL_MASS_WEIGHTS`
    #                 (spessore di strato trapezoidale 1000-500 hPa: 850 -> 150,
    #                 700 -> 125). Lo stau e' guidato dal flusso al livello di
    #                 condensazione: il vento a 10 m era 3,8 m/s nel campione e
    #                 non poteva produrre stau.
    "wind_level": "10m",
    # pesi di massa (hPa) dei due livelli, copiati da nwp_motion
    "wind_level_weights": {"850_700": [150.0, 125.0]},
}

LIMITS = (
    "Correzione ADDITIVA limitata dalla formula (nessun moltiplicatore scelto "
    "a occhio), applicata solo dove la precipitazione trasportata e' >= 0,05 "
    "mm/h: non crea pioggia dove il radar non vede nulla. TETTO dichiarato: "
    "il termine positivo non puo' superare il campo trasportato "
    "(`max_relative_increase` = 1.0, cioe' al massimo +100%): senza tetto la "
    "formula aggiunge fino a 32 mm/h su un pixel con 0,3 mm/h, che non e' un "
    "enhancement ma pioggia inventata. I pixel in cui il tetto morde sono "
    "contati (`capped_pixels`). Sottovento l'attenuazione non scende sotto il "
    "50% del campo trasportato (limite ingegneristico dichiarato). Quota = "
    "DEM SRTM 0,02 deg (non un modello NWP ne' le centraline). Formula Alpert "
    "& Shafir nata per QPE, trasposta al nowcast: NON validata."
)

REFERENCE = ("Alpert & Shafir 1989 (enhancement orografico, formulazione "
             "IMPROVER/STEPS); Magnus/Tetens per e_s. Trasposizione "
             "ingegneristica al nowcast, NON validata.")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
def config_for(config):
    raw = (config or {}).get("orographic_enhancement") or {}
    out = dict(DEFAULTS)
    for key, val in raw.items():
        out[key] = val
    return out


# ---------------------------------------------------------------------------
# griglia di quota
# ---------------------------------------------------------------------------
_CACHE = {}


def load_grid(path):
    """Legge la griglia `meteorisk.orography/1`. Ritorna dict o None.

    Dal BUILD=228 il file pubblicato e' a 0,02 gradi con `elevations_m` come
    stringa base64 di un flusso LZMA2 (filtro delta 1) di uint8 quantizzati
    (`encoding.codec = lzma2_delta1`, `encoding.scale_m = 20`). Il loader
    accetta anche il vecchio vettore JSON di int16 (retrocompatibile).
    L'array e' row-major con lat CRESCENTE (iy=0 -> lat0=35): qui viene
    girato UNA volta in north-up. Il mare e' 0 m (valore valido).
    """
    if not path or not os.path.exists(path):
        _CACHE[path] = None
        return None
    if path in _CACHE:
        return _CACHE[path]
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        g = doc["grid"]
        nx = int(g.get("nx") or g.get("width"))
        ny = int(g.get("ny") or g.get("height"))
        step_x = float(g.get("dlon") or g.get("step") or 0.0)
        step_y = float(g.get("dlat") or g.get("step") or step_x)
        if step_x <= 0 or step_y <= 0:
            raise ValueError("grid_step_missing")
        vals = doc.get("elevations_m")
        if vals is None:
            raise ValueError("elevations_m_missing")
        enc = doc.get("encoding") or {}
        if isinstance(vals, str):
            codec = str(enc.get("codec") or "")
            if codec != "lzma2_delta1":
                raise ValueError("elevations_codec_unsupported:%s" % codec)
            raw = lzma.decompress(base64.b64decode(vals.encode("ascii")))
            q = np.frombuffer(raw, dtype=np.uint8)
            if q.size != nx * ny:
                raise ValueError("elevations_length_%d_expected_%d"
                                 % (q.size, nx * ny))
            arr = q.reshape(ny, nx).astype(np.float64) * float(enc["scale_m"])
        else:
            arr = np.asarray(vals, dtype=np.float64).reshape(ny, nx)
        if "crescente" in str(g.get("ordering", "")).lower() or \
                float(g.get("lat0", 0.0)) < float(g.get("lat1", 1.0)):
            arr = arr[::-1, :]          # lat crescente -> north-up
        nodata = (doc.get("method") or {}).get("nodata")
        if nodata is not None:
            arr[arr <= float(nodata) + 1] = np.nan
        out = {
            "lon_min": float(g["lon0"]),
            "lon_max": float(g["lon1"]),
            "lat_min": float(g["lat0"]),
            "lat_max": float(g["lat1"]),
            "step": step_x,
            "step_x": step_x,
            "step_y": step_y,
            "width": nx, "height": ny,
            "elev": arr,
            "doc": doc,
            "encoding": enc,
            "path": path,
        }
        _CACHE[path] = out
        return out
    except Exception:
        _CACHE[path] = None
        return None


def sample_bilinear(grid, lon, lat):
    """Campionamento bilineare della griglia lon/lat. NaN fuori/NODATA."""
    step_x = float(grid.get("step_x") or grid["step"])
    step_y = float(grid.get("step_y") or grid["step"])
    fx = (lon - grid["lon_min"]) / step_x - 0.5
    fy = (grid["lat_max"] - lat) / step_y - 0.5
    h, w = grid["height"], grid["width"]
    inside = (fx >= 0) & (fx <= w - 1) & (fy >= 0) & (fy <= h - 1)
    out = np.full(np.shape(lon), np.nan, dtype=np.float64)
    if not np.any(inside):
        return out
    x0 = np.floor(fx[inside]).astype(int)
    y0 = np.floor(fy[inside]).astype(int)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    tx = fx[inside] - x0
    ty = fy[inside] - y0
    e = grid["elev"]
    v00 = e[y0, x0]; v10 = e[y0, x1]; v01 = e[y1, x0]; v11 = e[y1, x1]
    v = ((v00 * (1 - tx) + v10 * tx) * (1 - ty)
         + (v01 * (1 - tx) + v11 * tx) * ty)
    v = np.where(np.isfinite(v), v, np.nan)
    out[inside] = v
    return out


# ---------------------------------------------------------------------------
# geometria del raster radar
# ---------------------------------------------------------------------------
def pixel_lonlat(rd):
    """lon/lat del centro di OGNI pixel del raster nativo (vettoriale)."""
    t = rd.transform
    rows, cols = np.mgrid[0:rd.rows, 0:rd.cols]
    xs = t.c + (cols + 0.5) * t.a + (rows + 0.5) * t.b
    ys = t.f + (cols + 0.5) * t.d + (rows + 0.5) * t.e
    lon, lat = rd.geo_transform.transform(xs, ys)
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    return lon, lat


def pixel_size_m(rd, lat):
    """Passo (dx, dy) in metri per ogni riga (CRS geografico o proiettato)."""
    t = rd.transform
    crs_txt = str(getattr(rd, "crs_wkt", "") or getattr(rd, "crs", "")).strip()
    head = crs_txt.upper()[:40]
    # ATTENZIONE: un WKT proiettato CONTIENE la stringa GEOGCS nella parte
    # geodetica (`PROJCS["unnamed",GEOGCS[...]]`): conta solo il PRIMO token.
    geographic = head.startswith("GEOGCS") or (not crs_txt and abs(t.a) < 0.1)
    if geographic:
        dx = 111320.0 * np.cos(np.radians(lat)) * abs(t.a)
        dy = 110570.0 * abs(t.e)
    else:
        dx = np.full(np.shape(lat), abs(t.a), dtype=np.float64)
        dy = np.full(np.shape(lat), abs(t.e), dtype=np.float64)
    return dx, dy


# ---------------------------------------------------------------------------
# ambiente NWP (257 punti best_match)
# ---------------------------------------------------------------------------
def _env_hour_index(metadata, valid_ms):
    """Indice orario nella serie a partire da `time_base` (ora locale)."""
    base = (metadata or {}).get("time_base")
    if not base:
        return None, "env_time_base_missing"
    import datetime as _dt
    try:
        base_dt = _dt.datetime.fromisoformat(str(base))
    except ValueError:
        return None, "env_time_base_invalid"
    target = _dt.datetime.fromtimestamp(int(valid_ms) / 1000.0,
                                        _dt.timezone.utc).replace(tzinfo=None)
    # time_base e' ora LOCALE (Europe/Rome) come dichiarato in metadata.json;
    # si lavora in ora solare +1 come fa il builder (nessuna libreria tz).
    target = target + _dt.timedelta(hours=1)
    idx = int(math.floor((target - base_dt).total_seconds() / 3600.0))
    return idx, None


def _series(points, key, idx):
    """Serie oraria `key` all'indice `idx` per OGNI punto. NaN dove manca."""
    out = np.full(len(points), np.nan, dtype=np.float64)
    for i, p in enumerate(points):
        arr = ((((p.get("models") or {}).get("best_match") or {}).get("hourly")
                or {}).get(key))
        if arr and 0 <= idx < len(arr) and arr[idx] is not None:
            try:
                out[i] = float(arr[idx])
            except (TypeError, ValueError):
                pass
    return out


def wind_components(points, idx, cfg):
    """(u, v, livelli) per `cfg['wind_level']`.

    `10m`     : vento a 10 m (best_match).
    `850_700` : media VETTORIALE pesata per massa 850/700 hPa, stessi pesi di
                `nwp_motion.LEVEL_MASS_WEIGHTS` (150 e 125 hPa, spessore di
                strato trapezoidale su 1000-500 hPa). Media dei vettori, non
                delle direzioni: mediare due direzioni a 350° e 10° darebbe
                180°, cioe' il vento opposto. Se UNO dei due livelli manca, il
                punto e' NaN (nessun ripiego silenzioso su un solo livello).
    """
    level = str((cfg or {}).get("wind_level") or "10m")
    if level == "850_700":
        w = ((cfg or {}).get("wind_level_weights") or {}).get(
            "850_700", [150.0, 125.0])
        w8, w7 = float(w[0]), float(w[1])
        u = v = None
        for lvl, wl in ((850, w8), (700, w7)):
            ws = _series(points, "wind_speed_%dhPa" % lvl, idx)
            wd = _series(points, "wind_direction_%dhPa" % lvl, idx)
            rad = np.radians(wd)
            # direzione = PROVENIENZA: u verso est = -sin, v verso nord = -cos
            ul = -ws * np.sin(rad)
            vl = -ws * np.cos(rad)
            u = wl * ul if u is None else u + wl * ul
            v = wl * vl if v is None else v + wl * vl
        tot = w8 + w7
        return u / tot, v / tot, {"wind_level": "850_700",
                                  "levels_hpa": [850, 700],
                                  "weights": [w8, w7]}
    ws = _series(points, "windspeed_10m", idx)
    wd = _series(points, "winddirection_10m", idx)
    rad = np.radians(wd)
    return (-ws * np.sin(rad), -ws * np.cos(rad),
            {"wind_level": "10m", "levels_hpa": [10], "weights": [1.0]})


def env_at(valid_ms, cfg, cfg_meta=None):
    """T, RH, u, v ai punti NWP per l'ora di `valid_ms`. None + motivo."""
    path = cfg["env_points_file"]
    meta_path = cfg["env_metadata_file"]
    if not (os.path.exists(path) and os.path.exists(meta_path)):
        return None, "env_files_missing"
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None, "env_unreadable"
    idx, reason = _env_hour_index(meta, valid_ms)
    if idx is None:
        return None, reason
    points = doc.get("points") or []
    if not points:
        return None, "env_points_missing"
    hourly = ((points[0].get("models") or {}).get("best_match") or {}).get("hourly") or {}
    n_hours = len(hourly.get("temperature_2m") or [])
    if idx < 0 or idx >= n_hours:
        return None, "env_slot_outside_horizon"
    # eta' del dato rispetto ad adesso (generated_at del dataset)
    try:
        import datetime as _dt
        gen = _dt.datetime.fromisoformat(str(doc.get("generated_at")).replace("Z", "+00:00"))
        age_min = (_dt.datetime.now(_dt.timezone.utc) - gen).total_seconds() / 60.0
    except Exception:
        age_min = None
    if age_min is not None and age_min > float(cfg["max_env_age_minutes"]):
        return None, "env_stale_%dmin" % int(age_min)

    lat = np.array([float(p["lat"]) for p in points])
    lon = np.array([float(p["lon"]) for p in points])
    t_c = np.array([np.nan if v is None else float(v)
                    for p in points
                    for v in [(p.get("models", {}).get("best_match", {})
                               .get("hourly", {}).get("temperature_2m") or [None])[idx]]])
    rh = np.array([np.nan if v is None else float(v)
                   for p in points
                   for v in [(p.get("models", {}).get("best_match", {})
                              .get("hourly", {}).get("relativehumidity_2m") or [None])[idx]]])
    u, v, levels = wind_components(points, idx, cfg)
    ok = (np.isfinite(t_c) & np.isfinite(rh) & np.isfinite(u) & np.isfinite(v))
    if ok.sum() < 8:
        return None, "env_fields_incomplete"
    return {
        "lat": lat[ok], "lon": lon[ok],
        "t_c": t_c[ok], "rh": rh[ok],
        "u": u[ok], "v": v[ok],
        "hour_index": idx, "points_used": int(ok.sum()),
        "dataset_generated_at": doc.get("generated_at"),
        "age_min": None if age_min is None else round(age_min, 1),
        "time_base": meta.get("time_base"),
        "wind_level": levels["wind_level"],
        "wind_levels_hpa": levels["levels_hpa"],
        "wind_level_weights": levels["weights"],
    }, None


def _interp_weights(lat2d, lon2d, pts_lat, pts_lon, k, max_km):
    """Pesi IDW p=2 sui k vicini. Ritorna (idx, w) con -1 = nessun vicino."""
    from scipy.spatial import cKDTree
    R = 6371.0088

    def unit(la, lo):
        la = np.radians(la); lo = np.radians(lo)
        return np.column_stack([np.cos(la) * np.cos(lo),
                                np.cos(la) * np.sin(lo),
                                np.sin(la)])

    tree = cKDTree(unit(np.asarray(pts_lat), np.asarray(pts_lon)))
    q = unit(lat2d.ravel(), lon2d.ravel())
    kk = min(int(k), len(pts_lat))
    dist, idx = tree.query(q, k=kk, workers=-1)
    dist = np.atleast_2d(dist)
    idx = np.atleast_2d(idx)
    d_km = 2.0 * R * np.arcsin(np.clip(dist / 2.0, 0.0, 1.0))
    w = 1.0 / (d_km ** 2 + 1e-4)
    w[~np.isfinite(d_km) | (d_km > max_km)] = 0.0
    return idx, w


def _interp_field(vals, idx, w):
    v = np.asarray(vals, dtype=np.float64)[idx]
    bad = ~np.isfinite(v)
    ww = np.where(bad, 0.0, w)
    num = (np.where(bad, 0.0, v) * ww).sum(axis=1)
    den = ww.sum(axis=1)
    out = np.where(den > 0, num / np.maximum(den, 1e-12), np.nan)
    return out


# ---------------------------------------------------------------------------
# correzione
# ---------------------------------------------------------------------------
def sat_vapour_pressure_pa(t_c):
    """Magnus/Tetens (Alduchov & Eskridge 1996): e_s in Pa da T in degC."""
    t = np.asarray(t_c, dtype=np.float64)
    return 610.94 * np.exp(17.625 * t / (t + 243.04))


def enhancement_fields(mean3, t_c, rh, upslope, up_max, cfg):
    """e_pos / e_neg (mm/h) e maschera dei gate, dalle sole grandezze fisiche.

    Estratta da `prepare` per essere testabile senza un raster radar.
    `rh` in frazione 0-1, `t_c` in degC, upslope/up_max in m/s.
    """
    mean3 = np.asarray(mean3, dtype=np.float64)
    t_c = np.asarray(t_c, dtype=np.float64)
    rh = np.asarray(rh, dtype=np.float64)
    upslope = np.asarray(upslope, dtype=np.float64)
    up_max = np.asarray(up_max, dtype=np.float64)
    gate = (np.isfinite(mean3) & (mean3 >= float(cfg["min_mean_elev_m"]))
            & np.isfinite(t_c) & np.isfinite(rh)
            & (rh >= float(cfg["rh_threshold"]))
            & np.isfinite(upslope) & np.isfinite(up_max))
    t_k = t_c + 273.15
    es = sat_vapour_pressure_pa(t_c)
    min_up = float(cfg["min_upslope_ms"])
    rv = float(cfg["rv"])
    with np.errstate(invalid="ignore", divide="ignore"):
        e_pos = (rh * es * np.maximum(up_max - min_up, 0.0)
                 / (rv * t_k) * 3600.0)
        e_neg = (rh * es * np.maximum(-upslope - min_up, 0.0)
                 / (rv * t_k) * 3600.0)
    e_pos = np.where(gate & np.isfinite(e_pos), np.maximum(e_pos, 0.0), 0.0)
    e_neg = np.where(gate & np.isfinite(e_neg), np.maximum(e_neg, 0.0), 0.0)
    return e_pos, e_neg, gate


def prepare(rd, config, valid_ms, warnings=None):
    """Precalcola la correzione sul raster nativo. Ritorna dict o None."""
    cfg = config_for(config)
    ctx = {"schema": SCHEMA, "enabled": bool(cfg["enabled"]), "applied": False,
           "null_reason": None, "params": {
               "min_mean_elev_m": cfg["min_mean_elev_m"],
               "rh_threshold": cfg["rh_threshold"],
               "min_upslope_ms": cfg["min_upslope_ms"],
               "upwind_km": cfg["upwind_km"],
               "lee_floor_frac": cfg["lee_floor_frac"],
               "max_relative_increase": cfg["max_relative_increase"],
               "wind_level": cfg["wind_level"],
               "rain_min_mmh": cfg["rain_min_mmh"],
               "grid_file": cfg["grid_file"],
               "rv": cfg["rv"],
           }, "limits": LIMITS, "reference": REFERENCE}
    if not cfg["enabled"]:
        ctx["null_reason"] = "disabled_by_config"
        return ctx
    grid = load_grid(cfg["grid_file"])
    if grid is None:
        ctx["null_reason"] = "orography_grid_missing"
        return ctx
    env, reason = env_at(valid_ms, cfg)
    if env is None:
        ctx["null_reason"] = reason or "env_unavailable"
        return ctx

    lon, lat = pixel_lonlat(rd)
    elev = sample_bilinear(grid, lon, lat)
    dx, dy = pixel_size_m(rd, lat)

    # medie 3x3 e gradiente (solo su celle con quota)
    from scipy import ndimage
    fin = np.isfinite(elev)
    if not fin.any():
        ctx["null_reason"] = "no_land_cells_in_domain"
        return ctx
    filled = np.where(fin, elev, np.nanmean(elev[fin]))
    mean3 = ndimage.uniform_filter(filled, size=3, mode="nearest")
    gx = ndimage.sobel(filled, axis=1, mode="nearest") / (8.0 * dx)
    gy = ndimage.sobel(filled, axis=0, mode="nearest") / (8.0 * dy)

    idx, w = _interp_weights(lat, lon, env["lat"], env["lon"],
                             cfg["idw_neighbors"], cfg["idw_max_km"])
    t_c = _interp_field(env["t_c"], idx, w).reshape(elev.shape)
    rh = _interp_field(env["rh"], idx, w).reshape(elev.shape) / 100.0
    u = _interp_field(env["u"], idx, w).reshape(elev.shape)
    v = _interp_field(env["v"], idx, w).reshape(elev.shape)

    upslope = u * gx + v * gy

    # massimo sopraVvento entro upwind_km (offset lungo -vento)
    spd = np.hypot(u, v)
    safe = np.where(spd > 1e-6, spd, np.nan)
    ux = np.where(np.isfinite(safe), u / safe, 0.0)
    vy = np.where(np.isfinite(safe), v / safe, 0.0)
    up_max = np.where(np.isfinite(upslope), np.maximum(upslope, 0.0), 0.0)
    steps = int(max(1, cfg["upwind_steps"]))
    coords_y, coords_x = np.mgrid[0:elev.shape[0], 0:elev.shape[1]].astype(float)
    for k in range(1, steps + 1):
        back_m = float(cfg["upwind_km"]) * 1000.0 * k / steps
        sy = coords_y - (vy * back_m) / np.where(np.isfinite(dy), dy, 1.0)
        sx = coords_x - (ux * back_m) / np.where(np.isfinite(dx), dx, 1.0)
        shifted = ndimage.map_coordinates(up_max, [sy, sx], order=1,
                                          mode="nearest")
        up_max = np.maximum(up_max, np.where(np.isfinite(shifted), shifted, 0.0))

    # enhancement positivo e negativo (gate: quota media 3x3, RH, pendenza)
    e_pos, e_neg, gate = enhancement_fields(mean3, t_c, rh, upslope, up_max,
                                            cfg)

    ctx.update({
        "applied": True,
        "elev": elev, "mean3": mean3, "upslope": upslope,
        "upwind_max": up_max, "e_pos": e_pos, "e_neg": e_neg,
        "env": {k: env[k] for k in ("hour_index", "points_used",
                                    "dataset_generated_at", "age_min",
                                    "time_base", "wind_level",
                                    "wind_levels_hpa", "wind_level_weights")},
        "env_hour_index": env["hour_index"],
        "wind": {"u_mean": float(np.nanmean(u)), "v_mean": float(np.nanmean(v)),
                 "speed_mean": float(np.nanmean(spd)),
                 "dir_mean_deg": float((math.degrees(math.atan2(
                     -float(np.nanmean(u)), -float(np.nanmean(v)))) + 360.0) % 360.0)},
        "pixels_with_elevation": int(fin.sum()),
        "gated_pixels": int((e_pos > 0).sum()),
        "lee_pixels": int((e_neg > 0).sum()),
    })
    return ctx


def apply(field, ctx, cfg=None):
    """Applica la correzione al campo trasportato. Ritorna (nuovo, stats)."""
    cfg = cfg or DEFAULTS
    if not ctx or not ctx.get("applied"):
        return field, {"applied": False}
    rain_min = float(cfg.get("rain_min_mmh", DEFAULTS["rain_min_mmh"]))
    valid = np.isfinite(field)
    has_rain = valid & (field >= rain_min)
    # UN SOLO termine additivo: sopravvento (massimo entro 15 km a monte) MENO
    # sottovento locale. Sommare e sottrarre nello stesso pixel sarebbe un
    # doppio conteggio: il netto decide il segno.
    delta = ctx["e_pos"] - ctx["e_neg"]
    # TETTO sul termine positivo: non piu' di `max_relative_increase` volte il
    # campo trasportato (default +100%). Il tetto NON e' un moltiplicatore
    # arbitrario: impedisce alla formula di dominare dove il campo e' quasi
    # asciutto. I pixel in cui morde sono contati e pubblicati.
    cap_ratio = float(cfg.get("max_relative_increase", DEFAULTS["max_relative_increase"]))
    cap = cap_ratio * np.where(valid, field, 0.0)
    capped = delta > cap
    delta = np.where(capped, cap, delta)
    delta = np.where(has_rain, delta, 0.0)
    out = np.array(field, dtype=np.float64, copy=True) + delta

    floor = float(cfg.get("lee_floor_frac", DEFAULTS["lee_floor_frac"]))
    neg = has_rain & (delta < 0)
    if np.any(neg):
        out = np.where(neg, np.maximum(out, floor * field), out)
    out = np.where(valid, np.maximum(out, 0.0), out)

    changed = has_rain & (np.abs(out - field) > 1e-9)
    d = out - field
    stats = {
        "applied": True,
        "pixels_changed": int(changed.sum()),
        "capped_pixels": int((capped & has_rain).sum()),
        "delta_mean_mm_h": (round(float(d[changed].mean()), 4)
                            if changed.any() else 0.0),
        "delta_max_mm_h": (round(float(d[changed].max()), 4)
                           if changed.any() else 0.0),
        "delta_min_mm_h": (round(float(d[changed].min()), 4)
                           if changed.any() else 0.0),
        "delta_mean_positive_mm_h": (round(float(d[d > 0].mean()), 4)
                                     if (d > 0).any() else 0.0),
        "delta_mean_negative_mm_h": (round(float(d[d < 0].mean()), 4)
                                     if (d < 0).any() else 0.0),
    }
    return out, stats


def summarize(ctx):
    """Blocco JSON da pubblicare (senza gli array)."""
    if not ctx:
        return {"schema": SCHEMA, "enabled": False, "applied": False,
                "null_reason": "not_prepared"}
    out = {
        "schema": SCHEMA,
        "enabled": ctx.get("enabled"),
        "applied": bool(ctx.get("applied")),
        "null_reason": ctx.get("null_reason"),
        "applied_to": "transported_field_per_lead_before_alpha_and_stats",
        "params": ctx.get("params"),
        "limits": ctx.get("limits"),
        "reference": ctx.get("reference"),
    }
    if ctx.get("applied"):
        up = ctx["upslope"]
        up_f = up[np.isfinite(up)]
        out.update({
            "env": ctx.get("env"),
            "wind_level": (ctx.get("env") or {}).get("wind_level"),
            "wind": ctx.get("wind"),
            "pixels_with_elevation": ctx.get("pixels_with_elevation"),
            "gated_pixels": ctx.get("gated_pixels"),
            "lee_pixels": ctx.get("lee_pixels"),
            "upslope_ms": {
                "mean": round(float(up_f.mean()), 5) if up_f.size else None,
                "p99": round(float(np.percentile(up_f, 99)), 5) if up_f.size else None,
                "max": round(float(up_f.max()), 5) if up_f.size else None,
                "min": round(float(up_f.min()), 5) if up_f.size else None,
            },
            "enhancement_mm_h": {
                "max": round(float(ctx["e_pos"].max()), 3),
                "mean_gated": (round(float(ctx["e_pos"][ctx["e_pos"] > 0].mean()), 3)
                               if (ctx["e_pos"] > 0).any() else 0.0),
                "levy_max": round(float(ctx["e_neg"].max()), 3),
            },
        })
    return out
