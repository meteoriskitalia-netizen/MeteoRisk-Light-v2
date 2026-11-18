#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Riconoscimento fronti TFP (Hewson 1998) su frame .om Open-Meteo — MeteoRisk-Light-v2.

Metodo
------
Implementazione Python del metodo di Hewson (1998) nella forma dell'implementazione
di riferimento ``phil-sansom/front_id`` (MIT, Sansom & Catto 2022, GMD):

  tau = theta_e a 850 hPa (Bolton 1980 eq. 39; T850 + RH850, p = 85000 Pa)
  smoothing gaussiano a distanza fisica S (S in {30, 50, 100} km, MISURATO)
  dtau/dx, dtau/dy su griglia metrica (dx = 111320*dx_deg*cos(lat), dy = 111320*dy_deg)
  |grad tau|  (K/m)
  d|grad tau|/dx, d|grad tau|/dy
  TFP = (dtau/dx * d|grad tau|/dx + dtau/dy * d|grad tau|/dy) / |grad tau|   [K/m^2]
  frloc = laplaciano(|grad tau|)  -> isolinee zero (marching squares)
  filtri: TFP < thresh_t  E  |grad tau| > thresh_g   (SOGLIE MISURATE, mai copiate)
  line joining (searchrad), filtro lunghezza (min_length)
  classe da velocita' del fronte (vento 850, formula front_id) o da due valid time

Dichiarazioni scientifiche obbligatorie
--------------------------------------
* theta_e NON e' indipendente da T: e' calcolata da T850, quindi il contrasto
  termico e' gia' dentro tau. Non si sommano "contributi termici" separati.
* RH satura nelle nubi: il contrasto di theta_e viene appiattito sul fronte.
  Il metodo puo' quindi SOTTOSTIMARE i fronti nuvolosi: e' una proprieta' del
  metodo, non un difetto corretto con soglie inventate.
* Le soglie di ``front_id`` (ERA-Interim 1.5 gradi: thresh_t -1.5e11, thresh_g
  7.3e-6, minlength 251 km, searchrad 170 km) NON sono copiate: sui nostri
  0.02-0.25 gradi vengono MISURATE e scritte in calibration.json.

Simbologia (FISSA, NON ricalibrata — NOAA/WPC, Met Office, NOAA JetStream)
-------------------------------------------------------------------------
  cold        -> blu + triangoli verso il moto
  warm        -> rosso + semicerchi verso il moto
  stationary  -> triangoli blu da una parte, semicerchi rossi dall'altra
  occluded    -> viola + triangoli e semicerchi dallo stesso lato
  frontogenesi-> tratteggiata, simbolo su ogni segmento
  frontolisi  -> tratteggiata, simbolo ogni secondo segmento
  velocita' non calcolabile -> linea tratteggiata SENZA simboli (dichiarata)

Nessun dato simulato, nessun token, nessun dominio nuovo, nessuna UI.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Iterable

import numpy as np

# matplotlib solo per marching squares (backend Agg, nessuna finestra)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

R_EARTH_M = 111320.0
P850_PA = 85000.0
P850_HPA = 850.0
EPS = 0.622

# Riferimenti citati ( NON soglie locali )
REF_HEWSON = "Hewson, I. (1998), Weather and Forecasting 13:55-70, doi:10.1175/1520-0434(1998)013<0055:AOMFIT>2.0.CO;2"
REF_FRONT_ID = "Sansom & Catto (2022), Geosci. Model Dev., front_id (MIT): https://github.com/phil-sansom/front_id"
REF_BOLTON = "Bolton (1980), Mon. Wea. Rev. 108:1049-1053, eq. 10, 13, 15, 39"
REF_SYMBOLOGY = "NOAA/NWS WPC, Met Office, NOAA JetStream — convenzione grafica fronti (fissa)"

FRONT_TYPES = ("cold", "warm", "stationary", "occluded", "frontogenesis", "frontolysis")
STATUS_CALIBRATED = "calibrated"
STATUS_UNCALIBRATED = "uncalibrated"


# ---------------------------------------------------------------------------
# Simbologia convenzionale — FISSA
# ---------------------------------------------------------------------------
SYMBOLOGY = {
    "cold":        {"line": "solid",  "color": "blue",   "symbol": "triangles",        "side": "toward_motion", "placement": "every_segment"},
    "warm":        {"line": "solid",  "color": "red",    "symbol": "semicircles",      "side": "toward_motion", "placement": "every_segment"},
    "stationary":  {"line": "solid",  "color": "mixed",  "symbol": "triangles_semicircles_opposite", "side": "opposite", "placement": "every_segment"},
    "occluded":    {"line": "solid",  "color": "purple", "symbol": "triangles_semicircles_same",     "side": "same",     "placement": "every_segment"},
    "frontogenesis": {"line": "dashed", "color": "blue", "symbol": "triangles_or_semicircles", "side": "toward_motion", "placement": "every_segment"},
    "frontolysis":   {"line": "dashed", "color": "blue", "symbol": "triangles_or_semicircles", "side": "toward_motion", "placement": "every_second_segment"},
    # velocita' non calcolabile: tratteggiata SENZA simboli (nessun tipo inventato)
    None:          {"line": "dashed", "color": "neutral", "symbol": "none", "side": None, "placement": "none"},
}


@dataclass
class DetectionConfig:
    """Parametri di rilevamento. Tutti MISURATI in calibration.json."""
    S_km: float = 50.0
    thresh_t: float | None = None          # TFP, K/m^2 (negativo come in front_id)
    thresh_g: float | None = None          # |grad tau|, K/m
    min_length_km: float | None = None
    searchrad_km: float | None = None
    speed_threshold_ms: float = 1.5        # citato: front_id classify_fronts.R (non检测)
    topological_occlusion: bool = True
    status: str = STATUS_UNCALIBRATED
    publication_status: str = "hold"
    publication_reason: str | None = None
    measured_on: str | None = None

    @classmethod
    def from_calibration(cls, path: str | None, S_km: float = 50.0,
                         overrides: dict | None = None) -> "DetectionConfig":
        cfg = cls(S_km=S_km)
        if path:
            with open(path, "r", encoding="utf-8-sig") as fh:
                cal = json.load(fh)
            dom = (overrides or {}).get("domain")
            entry = None
            if dom and dom in (cal.get("domains") or {}):
                entry = cal["domains"][dom]
            if entry is None:
                entry = cal.get("global") or {}
            for k in ("S_km", "thresh_t", "thresh_g", "min_length_km",
                      "searchrad_km", "speed_threshold_ms", "topological_occlusion"):
                if k in entry:
                    setattr(cfg, k, entry[k])
            for k in ("status", "measured_on", "publication_status", "publication_reason"):
                if k in cal:
                    setattr(cfg, k, cal[k])
            if "S_km" in entry:
                cfg.S_km = float(entry["S_km"])
        if overrides:
            for k, v in overrides.items():
                if hasattr(cfg, k) and v is not None:
                    setattr(cfg, k, v)
        return cfg

    def uncalibrated_reason(self) -> str | None:
        if self.status == STATUS_CALIBRATED and self.thresh_t is not None and self.thresh_g is not None:
            return None
        return ("thresholds_not_calibrated: TFP/|grad tau| soglie non misurate su "
                "frame .om reali per questo dominio; output diagnostico, non pubblicabile")


# ---------------------------------------------------------------------------
# theta_e — Bolton (1980) eq. 39, coerente con il client MeteoRisk-Light
# ---------------------------------------------------------------------------
def dewpoint_c(T_c: np.ndarray, RH: np.ndarray) -> np.ndarray:
    """Magnus-Tetens (a=17.271, b=237.7), come nel client pubblicato."""
    T = np.asarray(T_c, dtype=np.float64)
    rh = np.clip(np.asarray(RH, dtype=np.float64), 0.01, 100.0)
    g = (17.271 * T) / (237.7 + T) + np.log(rh / 100.0)
    return (237.7 * g) / (17.271 - g)


def bolton_thetae(T_c: np.ndarray, RH: np.ndarray, p_hpa: float = P850_HPA) -> np.ndarray:
    """theta_e (K) da T (C), RH (%) e p (hPa) — Bolton 1980 eq. 10/13/15/39.

    La formula e' identica a quella del client pubblicato (ensThetaE), con la
    sola generalizzazione a p != 850 hPa. Valori non fisici -> NaN (mai 0).
    """
    T = np.asarray(T_c, dtype=np.float64)
    rh = np.asarray(RH, dtype=np.float64)
    Td = dewpoint_c(T, rh)
    e = 6.112 * np.exp(17.67 * Td / (Td + 243.5))          # hPa
    w = 0.622 * e / (p_hpa - e)
    Tk = T + 273.15
    TdK = Td + 273.15
    with np.errstate(divide="ignore", invalid="ignore"):
        TL = 1.0 / (1.0 / (TdK - 56.0) + np.log(Tk / TdK) / 800.0) + 56.0
        theta = Tk * np.pow(1000.0 / p_hpa, 0.2854)
        wg = w * 1000.0
        te = theta * np.exp((3.376 / TL - 0.00254) * wg * (1.0 + 0.81e-3 * wg))
    bad = ~np.isfinite(te) | (e <= 0) | (e >= p_hpa) | (Tk <= 0) | (TdK <= 0)
    te[bad] = np.nan
    return te


# ---------------------------------------------------------------------------
# Griglia metrica e smoothing
# ---------------------------------------------------------------------------
def metric_steps(grid: dict) -> tuple[np.ndarray, float]:
    """Ritorna (dx_m per riga [ny], dy_m scalare)."""
    ny = int(grid["ny"])
    lat0 = float(grid["latMin"])
    dy_deg = float(grid["dy"])
    dx_deg = float(grid["dx"])
    lat = lat0 + np.arange(ny, dtype=np.float64) * dy_deg
    dx_m = R_EARTH_M * dx_deg * np.cos(np.deg2rad(lat))
    # Righe polari: front_id esclude i box a +/-90; qui escludiamo cos(lat)<0.05
    # (lat>87.13) perche' dx->0 rende dtau/dx numericamente singolare. Dichiarato.
    dx_m = np.where(np.isfinite(dx_m) & (dx_m > 0.05 * R_EARTH_M * dx_deg), dx_m, np.nan)
    dy_m = R_EARTH_M * dy_deg
    return dx_m, dy_m


def gaussian_smooth_physical(field: np.ndarray, grid: dict, S_km: float) -> np.ndarray:
    """Smoothing gaussiano con sigma = S km in distanza fisica.

    sigma in celle: sigma_y = S_m/dy_m; sigma_x = S_m/dx_m(lat_center).
    L'anisotropia con latitudine e' approximata con la latitudine centrale del
    dominio (dichiarata: e' una scelta diimplementazione, non una soglia).
    """
    from scipy.ndimage import gaussian_filter
    dx_m, dy_m = metric_steps(grid)
    center = dx_m[int(np.nanargmin(np.abs(
        (float(grid["latMin"]) + np.arange(len(dx_m)) * float(grid["dy"]))
        - (float(grid["latMin"]) + (len(dx_m) - 1) * float(grid["dy"]) / 2.0))))]
    sig_y = (S_km * 1000.0) / dy_m
    sig_x = (S_km * 1000.0) / center
    f = np.where(np.isfinite(field), field, 0.0)
    w = np.where(np.isfinite(field), 1.0, 0.0)
    sf = gaussian_filter(f, (sig_y, sig_x), mode="constant", cval=0.0)
    sw = gaussian_filter(w, (sig_y, sig_x), mode="constant", cval=0.0)
    out = np.full_like(field, np.nan, dtype=np.float64)
    ok = sw > 1e-12
    out[ok] = sf[ok] / sw[ok]
    return out


def _ddx(field: np.ndarray, dx_m: np.ndarray) -> np.ndarray:
    # derivata centrata in colonna / dx(lat)
    g = np.empty_like(field, dtype=np.float64)
    g[:, 1:-1] = (field[:, 2:] - field[:, :-2]) / (2.0 * dx_m[:, None])
    g[:, 0] = (field[:, 1] - field[:, 0]) / dx_m
    g[:, -1] = (field[:, -1] - field[:, -2]) / dx_m
    return g


def _ddy(field: np.ndarray, dy_m: float) -> np.ndarray:
    g = np.empty_like(field, dtype=np.float64)
    g[1:-1, :] = (field[2:, :] - field[:-2, :]) / (2.0 * dy_m)
    g[0, :] = (field[1, :] - field[0, :]) / dy_m
    g[-1, :] = (field[-1, :] - field[-2, :]) / dy_m
    return g


def directional_derivative_5pt(maggrad: np.ndarray, dtdx: np.ndarray,
                               dtdy: np.ndarray, grid: dict, S_km: float) -> np.ndarray:
    """L_tau = d|grad tau|/ds lungo l'asse S-hat (5 punti a meta' S).

    s-hat = grad(tau)/|grad(tau)| (asse attraverso il fronte). I 5 punti sono
    a distanza (-2,-1,0,+1,+2)*(S/2) km; derivata centrata a 5 punti
    (-f[-2]+8f[-1]-8f[1]+f[2])/(12h). Interpolazione bilineare via
    scipy.ndimage.map_coordinates. Campo restituito in K/m^2.
    """
    from scipy.ndimage import map_coordinates
    mag = np.hypot(dtdx, dtdy)
    with np.errstate(invalid="ignore", divide="ignore"):
        sx = np.where(mag > 0, dtdx / mag, np.nan)
        sy = np.where(mag > 0, dtdy / mag, np.nan)
    dx_m, dy_m = metric_steps(grid)
    ny, nx = maggrad.shape
    rr, cc = np.mgrid[0:ny, 0:nx].astype(np.float64)
    h = (S_km * 1000.0) / 2.0
    vals = []
    for k in (-2, -1, 0, 1, 2):
        r = rr + (k * h / dy_m)
        # dx dipende dalla riga: spostamento in colonne usando dx della riga corrente
        c = cc + (k * h / np.where(np.isfinite(dx_m), dx_m, 1.0)[np.clip(rr.astype(int), 0, ny - 1)])
        vals.append(map_coordinates(np.where(np.isfinite(maggrad), maggrad, 0.0),
                                    [r, c], order=1, mode="nearest"))
    f = vals
    out = (-f[0] + 8.0 * f[1] - 8.0 * f[3] + f[4]) / (12.0 * h)
    out[~np.isfinite(sx)] = np.nan
    return out


@dataclass
class TauFields:
    tau: np.ndarray
    dtdx: np.ndarray
    dtdy: np.ndarray
    maggrad: np.ndarray
    dmagdx: np.ndarray
    dmagdy: np.ndarray
    tfp: np.ndarray
    frloc: np.ndarray
    ltau: np.ndarray
    tfp_warm: np.ndarray   # TFP campionato a +S/2 lungo s-hat (lato caldo)


def compute_tau_fields(T850: np.ndarray, RH850: np.ndarray, grid: dict,
                       S_km: float, orography_mask: np.ndarray | None = None,
                       compute_ltau: bool = True) -> TauFields:
    """Pipeline completa theta_e -> TFP -> frloc (front_id)."""
    tau = bolton_thetae(T850, RH850)
    if orography_mask is not None:
        tau = np.where(orography_mask, np.nan, tau)
    tau_s = gaussian_smooth_physical(tau, grid, S_km)
    dx_m, dy_m = metric_steps(grid)
    # rimaschera DOPO lo smoothing: il filler normalizzato dei vicini non deve
    # restituire un valore dove 850 hPa e' sotto il suolo.
    if orography_mask is not None:
        tau_s = np.where(orography_mask, np.nan, tau_s)
    tau_s = np.where(np.isfinite(dx_m)[:, None], tau_s, np.nan)
    dtdx = _ddx(tau_s, dx_m)
    dtdy = _ddy(tau_s, dy_m)
    maggrad = np.hypot(dtdx, dtdy)
    # pavimento numerico: 1e-9 K/m e' 4 ordini sotto il minimo fisico atteso;
    # evita TFP = 0/0. Dichiarato come gate numerico, non soglia scientifica.
    maggrad[~np.isfinite(maggrad) | (maggrad < 1e-9)] = np.nan
    dmagdx = _ddx(maggrad, dx_m)
    dmagdy = _ddy(maggrad, dy_m)
    with np.errstate(invalid="ignore", divide="ignore"):
        tfp = (dtdx * dmagdx + dtdy * dmagdy) / maggrad
    # laplaciano del |grad tau| (front locator di front_id)
    d2x = np.empty_like(maggrad)
    d2x[:, 1:-1] = (maggrad[:, 2:] - 2 * maggrad[:, 1:-1] + maggrad[:, :-2]) / (dx_m[:, None] ** 2)
    d2x[:, 0] = d2x[:, 1]
    d2x[:, -1] = d2x[:, -2]
    d2y = np.empty_like(maggrad)
    d2y[1:-1, :] = (maggrad[2:, :] - 2 * maggrad[1:-1, :] + maggrad[:-2, :]) / (dy_m ** 2)
    d2y[0, :] = d2y[1, :]
    d2y[-1, :] = d2y[-2, :]
    frloc = d2x + d2y
    ltau = directional_derivative_5pt(maggrad, dtdx, dtdy, grid, S_km) if compute_ltau else np.full_like(maggrad, np.nan)
    # TFP sul lato caldo: sull'asse L_tau=0 il TFP e' nullo per costruzione;
    # il filtro TFP va quindi applicato a meta'-S lungo s-hat, dove il
    # contrasto termico e' misurabile. Scelta dichiarata, non una nuova soglia.
    tfp_warm = _sample_along_axis(tfp, dtdx, dtdy, grid, S_km)
    return TauFields(tau_s, dtdx, dtdy, maggrad, dmagdx, dmagdy, tfp, frloc, ltau, tfp_warm)


def _sample_along_axis(field: np.ndarray, dtdx: np.ndarray, dtdy: np.ndarray,
                       grid: dict, S_km: float) -> np.ndarray:
    """Campiona ``field`` a +S/2 lungo s-hat = grad(tau)/|grad(tau)|."""
    from scipy.ndimage import map_coordinates
    mag = np.hypot(dtdx, dtdy)
    with np.errstate(invalid="ignore", divide="ignore"):
        sx = np.where(mag > 0, dtdx / mag, 0.0)
        sy = np.where(mag > 0, dtdy / mag, 0.0)
    dx_m, dy_m = metric_steps(grid)
    ny, nx = field.shape
    rr, cc = np.mgrid[0:ny, 0:nx].astype(np.float64)
    h = (S_km * 1000.0) / 2.0
    r = rr + h * sy / dy_m
    c = cc + h * sx / np.where(np.isfinite(dx_m), dx_m, 1.0)[np.clip(rr.astype(int), 0, ny - 1)]
    out = map_coordinates(np.where(np.isfinite(field), field, 0.0), [r, c], order=1, mode="nearest")
    out[~np.isfinite(mag)] = np.nan
    return out


def orography_mask_proxy(Z1000: np.ndarray, threshold_m: float = 0.0) -> np.ndarray:
    """Proxy dichiarato: Z1000 < 0 m => superficie 1000 hPa sotto il livello del
    mare => terreno elevato => 850 hPa potenzialmente sotto il suolo.

    NON e' una soglia tarata: e' un proxy fisico dichiarato. I frame reali
    ecmwf_ifs025 hanno Z1000 in [-461, +371] m (misurato 2026-11-18).
    """
    return np.isfinite(Z1000) & (Z1000 < threshold_m)


# ---------------------------------------------------------------------------
# Marching squares + line joining
# ---------------------------------------------------------------------------
def zero_contours(field: np.ndarray, level: float = 0.0) -> list[np.ndarray]:
    """Isolee zero (marching squares) via matplotlib Agg. Punti in (col,row)."""
    arr = np.ma.masked_invalid(np.asarray(field, dtype=np.float64))
    if arr.count() < 4:
        return []
    ny, nx = arr.shape
    xs = np.arange(nx, dtype=np.float64)
    ys = np.arange(ny, dtype=np.float64)
    fig, ax = plt.subplots(figsize=(1.0, 1.0), dpi=20)
    try:
        cs = ax.contour(xs, ys, arr, levels=[float(level)])
        segs = [np.asarray(s, dtype=np.float64) for s in cs.allsegs[0] if len(s) >= 2]
    finally:
        plt.close(fig)
    return segs


def _gc_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = np.deg2rad(lat1), np.deg2rad(lat2)
    dp = p2 - p1
    dl = np.deg2rad(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2.0 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def colrow_to_lonlat(seg: np.ndarray, grid: dict) -> tuple[np.ndarray, np.ndarray]:
    col, row = seg[:, 0], seg[:, 1]
    lon = float(grid["lonMin"]) + col * float(grid["dx"])
    lat = float(grid["latMin"]) + row * float(grid["dy"])
    return lon, lat


def filter_and_join(segs: list[np.ndarray], fields: TauFields, grid: dict,
                    cfg: DetectionConfig) -> list[dict]:
    """Filtra i segmenti Per TFP/|grad tau| e li unisce (searchrad, minlength).

    Ritorna una lista di fronti grezzi: {"points": [[lon,lat],...], "tfp": [...],
    "strength": [...], "length_km": float}. Tutti i numeri sono deterministici.
    """
    if cfg.thresh_t is None or cfg.thresh_g is None:
        raise ValueError("soglie non calibrate: passare calibration.json")
    gathered: list[dict] = []
    for seg in segs:
        col = np.clip(np.rint(seg[:, 0]).astype(int), 0, fields.tfp.shape[1] - 1)
        row = np.clip(np.rint(seg[:, 1]).astype(int), 0, fields.tfp.shape[0] - 1)
        tv = fields.tfp_warm[row, col]
        gv = fields.maggrad[row, col]
        keep = np.isfinite(tv) & np.isfinite(gv) & (tv < cfg.thresh_t) & (gv > cfg.thresh_g)
        idx = np.flatnonzero(keep)
        if idx.size == 0:
            continue
        lon, lat = colrow_to_lonlat(seg[idx], grid)
        pts = [[round(float(a), 5), round(float(b), 5)] for a, b in zip(lon, lat)]
        gathered.append({"points": pts,
                         "tfp": [float(x) for x in tv[idx]],
                         "strength": [float(x) for x in gv[idx]]})
    # line joining: usa il primo/ultimo punto di ogni gruppo, greedily deterministico
    order = sorted(range(len(gathered)), key=lambda i: gathered[i]["points"][0])
    used = [False] * len(gathered)
    fronts: list[dict] = []
    searchrad = cfg.searchrad_km or 0.0
    for i in order:
        if used[i]:
            continue
        used[i] = True
        cur = gathered[i]
        changed = True
        while changed:
            changed = False
            tail = cur["points"][-1]
            for j in order:
                if used[j]:
                    continue
                head = gathered[j]["points"][0]
                if _gc_km(tail[0], tail[1], head[0], head[1]) <= searchrad:
                    cur["points"].extend(gathered[j]["points"])
                    cur["tfp"].extend(gathered[j]["tfp"])
                    cur["strength"].extend(gathered[j]["strength"])
                    used[j] = True
                    changed = True
                    break
        length = 0.0
        for a, b in zip(cur["points"], cur["points"][1:]):
            length += _gc_km(a[0], a[1], b[0], b[1])
        cur["length_km"] = round(length, 3)
        cur["n_points"] = len(cur["points"])
        fronts.append(cur)
    minlen = cfg.min_length_km or 0.0
    return [f for f in fronts if f["length_km"] >= minlen]


# ---------------------------------------------------------------------------
# Classificazione (vento 850 o due valid time) + simbologia
# ---------------------------------------------------------------------------
def front_speed_ms(fields: TauFields, grid: dict, front: dict,
                   u850: np.ndarray | None, v850: np.ndarray | None) -> tuple[float | None, str | None]:
    """Velocita' del fronte verso il lato caldo (front_id compute_tfp.R).

    frspeed = u * d|grad tau|/dx / |grad|grad tau|| + v * d|grad tau|/dy / |grad|grad tau||
    Se il vento manca o il denominatore e' nullo -> (None, motivo).
    """
    if u850 is None or v850 is None:
        return None, "front_speed_wind_850_missing"
    lon = np.array([p[0] for p in front["points"]])
    lat = np.array([p[1] for p in front["points"]])
    col = np.rint((lon - float(grid["lonMin"])) / float(grid["dx"])).astype(int)
    row = np.rint((lat - float(grid["latMin"])) / float(grid["dy"])).astype(int)
    ny, nx = fields.tfp.shape
    ok = (row >= 0) & (row < ny) & (col >= 0) & (col < nx)
    if not ok.any():
        return None, "front_speed_points_outside_grid"
    g = np.hypot(fields.dmagdx, fields.dmagdy)[row[ok], col[ok]]
    good = np.isfinite(g) & (g > 0)
    if not good.any():
        return None, "front_speed_degenerate_gradient_of_gradient"
    sp = (u850[row[ok], col[ok]] * fields.dmagdx[row[ok], col[ok]] +
          v850[row[ok], col[ok]] * fields.dmagdy[row[ok], col[ok]]) / g
    sp = sp[np.isfinite(sp) & good]
    if sp.size == 0:
        return None, "front_speed_nonfinite"
    return float(np.median(sp)), None


def classify(speed_ms: float | None, tendency: float | None,
             threshold_ms: float, merge_near_min: bool = False) -> tuple[str | None, str | None]:
    """Classificazione convenzionale. Occluso SOLO se due fronti classificati si
    fondono presso un minimo (merge_near_min=True). Altrimenti, se la velocita'
    non e' calcolabile: None -> simbologia tratteggiata senza simboli.
    """
    if merge_near_min:
        return "occluded", None
    if speed_ms is None:
        # con due valid time si puo' dare frontogenesi/frontolisi (convenzione)
        if tendency is not None and math.isfinite(tendency):
            eps = 1e-12
            if tendency > eps:
                return "frontogenesis", None
            if tendency < -eps:
                return "frontolysis", None
            return "stationary", "front_tendency_zero_without_speed"
        return None, "front_speed_not_computable_dashed_without_symbols"
    if speed_ms < -threshold_ms:
        return "cold", None
    if speed_ms > threshold_ms:
        return "warm", None
    return "stationary", None


def occluded_merge(fronts: list[dict], min_pressure_proxy: np.ndarray | None,
                   grid: dict) -> set[int]:
    """Marca gli indici dei fronti che si fondono presso un minimo.

    Usa SOLO campi presenti: se due fronti classificati distano < 50 km e la
    pressure_msl (se disponibile) ha un minimo locale fra loro, entrambi diventano
    'occluded'. Nessun minimo -> nessuna occlusione inventata.
    """
    out: set[int] = set()
    if min_pressure_proxy is None:
        return out
    for i in range(len(fronts)):
        for j in range(i + 1, len(fronts)):
            a = fronts[i]["points"][-1]
            b = fronts[j]["points"][0]
            if _gc_km(a[0], a[1], b[0], b[1]) <= 50.0:
                col = int(round(((a[0] + b[0]) / 2 - float(grid["lonMin"])) / float(grid["dx"])))
                row = int(round(((a[1] + b[1]) / 2 - float(grid["latMin"])) / float(grid["dy"])))
                ny, nx = min_pressure_proxy.shape
                if 1 <= row < ny - 1 and 1 <= col < nx - 1:
                    v = min_pressure_proxy[row, col]
                    nb = min_pressure_proxy[row - 1:row + 2, col - 1:col + 2]
                    if np.isfinite(v) and np.nanmin(nb) == v:
                        out.add(i)
                        out.add(j)
    return out


# ---------------------------------------------------------------------------
# Analisi frame -> GeoJSON RFC7946
# ---------------------------------------------------------------------------
def analyze_frame(arrays: dict, grid: dict, cfg: DetectionConfig,
                  generated_at: datetime | None = None,
                  valid_time: str | None = None,
                  second_arrays: dict | None = None) -> dict:
    """Costruisce il FeatureCollection. Coordinate SEMPRE [lon, lat] (RFC7946)."""
    required = ("temperature_850hPa", "relative_humidity_850hPa")
    missing = [n for n in required if n not in arrays]
    reason = cfg.uncalibrated_reason()
    features: list[dict] = []
    diagnostics = {"missing_fields": list(missing), "S_km": cfg.S_km,
                   "status": cfg.status, "n_contours": 0, "n_fronts": 0}
    T = arrays.get("temperature_850hPa")
    RH = arrays.get("relative_humidity_850hPa")
    Z1000 = arrays.get("geopotential_height_1000hPa")
    u = None
    v = None
    if "wind_speed_850hPa" in arrays and "wind_direction_850hPa" in arrays:
        spd = np.asarray(arrays["wind_speed_850hPa"], dtype=np.float64)
        d = np.deg2rad(np.asarray(arrays["wind_direction_850hPa"], dtype=np.float64))
        u = -spd * np.sin(d)
        v = -spd * np.cos(d)
    if missing or T is None or RH is None:
        diag = {"kind": "front_diagnostics", "null_reason": "missing:" + ",".join(missing),
                "front_type": None, "speed_ms": None, "tfp": None,
                "strength": None, "length_km": 0.0, "n_points": 0,
                "confidence": "uncalibrated", "reference": REF_HEWSON,
                "symbology": SYMBOLOGY[None]}
        # geometria vuota ma valida RFC7946
        return _collection(features, grid, cfg, generated_at, valid_time, diagnostics, reason, diag)
    mask = orography_mask_proxy(Z1000) if Z1000 is not None else None
    fld = compute_tau_fields(T, RH, grid, cfg.S_km, mask, compute_ltau=True)
    # asse del fronte = isolinea zero di L_tau (d|grad tau|/ds lungo s-hat):
    # passa per il massimo del gradiente, come richiesto dal test analitico.
    segs = zero_contours(fld.ltau)
    diagnostics["n_contours"] = len(segs)
    if reason is None:
        fronts = filter_and_join(segs, fld, grid, cfg)
    else:
        fronts = []
    diagnostics["n_fronts"] = len(fronts)

    pres = arrays.get("pressure_msl")
    merged = occluded_merge(fronts, pres, grid) if pres is not None else set()
    tendency = None
    if second_arrays is not None and "temperature_850hPa" in second_arrays and "relative_humidity_850hPa" in second_arrays:
        f2 = compute_tau_fields(second_arrays["temperature_850hPa"],
                                second_arrays["relative_humidity_850hPa"],
                                grid, cfg.S_km,
                                orography_mask_proxy(second_arrays.get("geopotential_height_1000hPa")) if second_arrays.get("geopotential_height_1000hPa") is not None else None,
                                compute_ltau=False)
        tendency = float(np.nanmedian(f2.maggrad - fld.maggrad))

    for i, fr in enumerate(fronts):
        speed, speed_reason = front_speed_ms(fld, grid, fr, u, v)
        ftype, type_reason = classify(speed, tendency, cfg.speed_threshold_ms, i in merged)
        # TFP e' ~1e-10 K/m^2: arrotondare a 6 decimali darebbe -0.0
        tfp_val = float(np.median(fr["tfp"])) if fr["tfp"] else None
        strength_val = float(np.median(fr["strength"])) if fr["strength"] else None
        null_reason = reason or speed_reason or type_reason
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": fr["points"]},
            "properties": {
                "kind": "front",
                "front_type": ftype,
                "level": "850hPa",
                "surface_analyzed": False,
                "speed_ms": None if speed is None else round(speed, 4),
                "null_reason": null_reason,
                "tfp": tfp_val,
                "strength": strength_val,
                "strength_unit": "K/m",
                "length_km": fr["length_km"],
                "n_points": fr["n_points"],
                "confidence": "calibrated" if reason is None else "uncalibrated",
                "reference": " | ".join((REF_HEWSON, REF_FRONT_ID, REF_BOLTON, REF_SYMBOLOGY)),
                "symbology": SYMBOLOGY[ftype],
                "smoothing_S_km": cfg.S_km,
            },
        })
    return _collection(features, grid, cfg, generated_at, valid_time, diagnostics, reason)


def _collection(features: list[dict], grid: dict, cfg: DetectionConfig,
                generated_at: datetime | None, valid_time: str | None,
                diagnostics: dict, reason: str | None, extra: dict | None = None) -> dict:
    if extra:
        features.append({"type": "Feature",
                         "geometry": {"type": "LineString", "coordinates": []},
                         "properties": extra})
    ts = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "type": "FeatureCollection",
        "schema_version": 1,
        "generated_at": ts,
        "valid_time": valid_time,
        "source": "open-meteo .om (CC BY 4.0)",
        "domain_grid": {k: grid[k] for k in ("nx", "ny", "dx", "dy")},
        "status": cfg.status,
        "publication_status": cfg.publication_status,
        "publication_reason": cfg.publication_reason,
        "null_reason": reason,
        "calibration_measured_on": cfg.measured_on,
        "detection_method": "TFP Hewson 1998 / front_id (theta_e 850 Bolton)",
        "coordinate_order": "RFC7946 [lon, lat]",
        "diagnostics": diagnostics,
        "features": features,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def load_array_file(path: str) -> dict:
    """Legge i campi richiesti da un .om locale (nessun downloadWho)."""
    from omfiles import OmFileReader
    try:
        from .grid import array_names, read_array
    except ImportError:  # esecuzione come `python fronts.py` (non modulo)
        from grid import array_names, read_array
    reader = OmFileReader.from_path(path)
    try:
        present = set(array_names(reader))
        out = {}
        for n in ("temperature_850hPa", "relative_humidity_850hPa",
                  "wind_speed_850hPa", "wind_direction_850hPa",
                  "geopotential_height_1000hPa", "pressure_msl", "cape",
                  "precipitation", "precipitation_cum", "precipitation_6h",
                  "rain", "showers", "weather_code"):
            if n in present:
                out[n] = read_array(reader, n)
        return out
    finally:
        try:
            reader.close()
        except Exception:
            pass


def write_json(path: str, obj: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", help="manifesto latest.json (solo metadati)")
    ap.add_argument("--frame", required=True, help="file .om locale del frame")
    ap.add_argument("--domain", default="ecmwf_ifs025")
    ap.add_argument("--generated-at", help="ISO8601 UTC")
    ap.add_argument("--out", required=True)
    ap.add_argument("--calibration", help="calibration.json")
    ap.add_argument("--S-km", type=float, default=None, help="override scala smoothing (MISURATA)")
    ap.add_argument("--second-frame", help="frame successivo per tendenza/frontogenesi")
    args = ap.parse_args(argv)

    overrides = {}
    if args.S_km is not None:
        overrides["S_km"] = args.S_km
    cfg = DetectionConfig.from_calibration(args.calibration, overrides={"domain": args.domain})
    if args.S_km is not None:
        cfg.S_km = args.S_km

    manifest = {}
    if args.manifest:
        with open(args.manifest, "r", encoding="utf-8-sig") as fh:
            manifest = json.load(fh)
    grid = manifest.get("grid") or {}
    if not grid:
        raise SystemExit("FATAL: --manifest senza grid: impossibile costruire la griglia metrica")
    arrays = load_array_file(args.frame)
    second = load_array_file(args.second_frame) if args.second_frame else None
    gen = datetime.fromisoformat(args.generated_at.replace("Z", "+00:00")) if args.generated_at else datetime.now(timezone.utc)
    vt = None
    if args.manifest:
        for f in manifest.get("frames") or []:
            if f.get("file") == os.path.basename(args.frame):
                vt = f.get("valid_time")
                break
    obj = analyze_frame(arrays, grid, cfg, gen, vt, second)
    write_json(args.out, obj)
    print(json.dumps({"out": args.out, "status": obj["status"],
                      "features": len(obj["features"]),
                      "null_reason": obj["null_reason"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
