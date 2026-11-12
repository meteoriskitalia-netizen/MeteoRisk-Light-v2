#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Meteorisk Radar Engine - doppler.py

SHEAR AZIMUTALE OSSERVATO dal Doppler dei radar francesi (OPERA FR).

COSA MISURA (e cosa NON dice)
-----------------------------
Il prodotto DPC VMI e' un mosaico 2D a singola riflettivita': non contiene
velocita' radiale, quindi la rotazione NON e' misurabile da quel dato. Questo
modulo legge il campo VRADH (velocita' radiale Doppler) dei due radar francesi
che coprono la Corsica e il Tirreno settentrionale:

    frale  Aleria   (42.12971 N, 9.49639 E, 63 m)   256 km di portata
    fraja  Ajaccio  (41.95313 N, 8.70054 E, 784 m)  256 km di portata

e ne ricava lo SHEAR AZIMUTALE (gradiente della velocita' radiale fra raggi
adiacenti a distanza costante) in m/s/km. Il campo pubblicato si chiama
`rotation_shear` e la descrizione e' "shear azimutale osservato (Doppler,
radar X)": NON e' un mesociclone, NON e' "rotazione confermata". Uno shear
azimutale e' una condizione necessaria ma non sufficiente per una rotazione,
e la geometria del campionamento (fascio alto a grande distanza) puo'
produrre shear anche senza alcun vortice.

GEOMETRIA (MISURATA il 2026-11-11 sui file OPERA FR)
----------------------------------------------------
I file SCAN OPERA FR NON hanno la geometria del PVOL nativo MÃ©tÃ©o-France
ridistribuito da iRadar (360x256 @ 1000 m, gain -0.5, offset 60):

    OPERA SCAN : 360 raggi x 267 bin, rscale 960 m, rstart 0,
                 max range 256.32 km, gain +0.5, offset -60,
                 nodata 255, undetect 254, `how/NI` = 62.0555 m/s
    iRadar PVOL: 360 raggi x 256 bin, rscale 1000 m, gain -0.5, offset 60,
                 nodata 255, undetect 0, NI in `dataset1/how/NI`

Le due codifiche sono speculari (raw_iRadar = 240 - raw_OPERA) e danno lo
stesso valore fisico: la codifica OPERA e' quella usata qui.

SEGNO (verificato, non assunto)
-------------------------------
ODIM: VRADH positivo = allontanamento dal radar. Verificato il 2026-11-12
sul PVOL iRadar delle 16:45 UTC: il fit sinusoidale VRADH(azimuth) su
96-192 km da' ampiezza 13-15 m/s e fase 67.7 gradi, mentre il vento NWP a
850-700 hPa (Open-Meteo, 41.89N 12.48E) e' diretto verso 71-74 gradi:
positivo = allontanamento. Sul file OPERA il fit a 0-48 km (volume
2026-11-11T05:29) da' fase 102 gradi contro un vento a 10 m diretto verso
119 gradi: coerente ma con ampiezza 2.9 m/s (prova piu' debole).

DEALIASING
----------
Il campo VRADH e' avvolto in [-NI, +NI]. Il dealiasing e' REGION-BASED:
si etichettano le regioni connesse di gate "piegati" (|v| > 0.9*NI), e per
ogni regione si sceglie lo sfasamento k*2*NI che minimizza la distanza dalla
mediana dei gate validi confinanti (gia' srotolati). Il metodo e' dichiarato
in output (`dealias_method`); se `NI` manca il campo resta avvolto e ogni
candidato porta `null_reason = "nyquist_missing"`.

Se dopo lo srotolamento restano salti > 1.5*NI fra raggi adiacenti, quei
gate sono marcati non risolvibili (`unresolved_gates`) e NON generano
candidati: meglio un null con motivo che uno shear inventato.

GATE DI UN CANDIDATO
--------------------
- distanza <= `max_range_km` (default 250: Roma e' a 247.9 km da Aleria)
- altezza del fascio <= `max_beam_height_km` (default 8.0, kRe = 8495 km)
- |shear| >= `shear_threshold_ms_km` (default 4.0, SCELTA INGEGNERISTICA)
- persistenza su `persistence_scans` volumi consecutivi (stesso segno)
- coerenza verticale su 2 elevazioni (stesso segno, +-2 raggi/gate)
La confidenza dipende SOLO dall'altezza del fascio: < 3 km = high,
< 6 km = medium, >= 6 km = low (a 248 km la 0.6 gradi e' a 6.2 km: aloft).
"""

import datetime as _dt
import io
import json
import math
import os
import re

import numpy as np

from . import models
from . import source_opera

SCHEMA_VERSION = 1
LICENSE = "CC BY 4.0"
COUNTRY = "FR"
SITES = ("frale", "fraja")
SITE_LABEL = {"frale": "Aleria", "fraja": "Ajaccio"}
ATTRIBUTION = "Meteo-France via OPERA (EUMETNET) - CC BY 4.0"
DEALIAS_METHOD = "region_based"
DESCRIPTION = "shear azimutale osservato (Doppler, radar {site})"

EARTH_RADIUS_KM = 8495.0          # 4/3 * 6371 km (rifrazione standard)
S3_BASE = source_opera.S3_BASE

DEFAULTS = {
    "enabled": True,
    "sites": list(SITES),
    "country": COUNTRY,
    "s3_base": S3_BASE,
    # --- gate fisici -----------------------------------------------------
    "max_range_km": 250.0,
    "min_range_km": 30.0,
    "max_beam_height_km": 8.0,
    "high_beam_height_km": 3.0,
    "medium_beam_height_km": 6.0,
    # --- shear -----------------------------------------------------------
    "shear_threshold_ms_km": 4.0,
    "shear_window_rays": 1,        # 1 -> 3 raggi (i-1, i, i+1)
    "persistence_scans": 2,
    "persistence_factor": 0.5,     # la scansione precedente deve avere >= 50%
    "vertical_tolerance_rays": 2,
    "vertical_tolerance_gates": 2,
    "vertical_factor": 0.5,
    # --- ingestione ------------------------------------------------------
    "max_elevations_shear": 2,
    "max_elevations_coverage": 3,
    "lookback_days": 2,
    "timeout_s": 60,
    # --- accoppiamento con le strutture ----------------------------------
    "match_radius_km": 15.0,
    "match_window_min": 5.0,
    # --- copertura -------------------------------------------------------
    "coverage_out": "data/radar/doppler_coverage.geojson",
    "coverage_window_min": 30.0,
    "coverage_max_volumes": 3,
    "coverage_min_range_km": 5.0,
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def config_for(config):
    """Blocco `doppler` della config con i default applicati (nessun globale
    mutato)."""
    base = dict(DEFAULTS)
    block = (config or {}).get("doppler") or {}
    if isinstance(block, dict):
        base.update(block)
    return base


# ---------------------------------------------------------------------------
# Geometria
# ---------------------------------------------------------------------------
def beam_height_km(range_km, elevation_deg, site_height_m=0.0,
                   earth_radius_km=EARTH_RADIUS_KM):
    """Altezza del fascio (km s.l.m.) con rifrazione 4/3.

    h = sqrt(r^2 + kRe^2 + 2*r*kRe*sin(el)) - kRe + h_sito
    """
    r = np.asarray(range_km, dtype="float64")
    th = np.deg2rad(np.asarray(elevation_deg, dtype="float64"))
    h = np.sqrt(r * r + earth_radius_km ** 2
                + 2.0 * r * earth_radius_km * np.sin(th)) - earth_radius_km
    return h + float(site_height_m) / 1000.0


def range_at_beam_height(height_km, elevation_deg, site_height_m=0.0,
                         earth_radius_km=EARTH_RADIUS_KM):
    """Raggio (km) dove il fascio raggiunge `height_km` (inversa di
    `beam_height_km`). `inf` se non lo raggiunge mai (elevazione <= 0).

    Risolve r^2 + 2*k*sin(el)*r + k^2 - A^2 = 0 con A = h + k - h_sito.
    """
    k = float(earth_radius_km)
    th = math.radians(float(elevation_deg))
    a = float(height_km) + k - float(site_height_m) / 1000.0
    disc = (k * math.sin(th)) ** 2 + a * a - k * k
    if disc <= 0.0:
        return float("inf")
    return -k * math.sin(th) + math.sqrt(disc)


def destination(lon, lat, bearing_deg, dist_km):
    """Punto a `dist_km` e azimuth `bearing_deg` (sfera R=6371.0088 km)."""
    r = 6371.0088
    b = math.radians(bearing_deg)
    la1 = math.radians(lat)
    lo1 = math.radians(lon)
    la2 = math.asin(math.sin(la1) * math.cos(dist_km / r)
                    + math.cos(la1) * math.sin(dist_km / r) * math.cos(b))
    lo2 = lo1 + math.atan2(math.sin(b) * math.sin(dist_km / r) * math.cos(la1),
                           math.cos(dist_km / r) - math.sin(la1) * math.sin(la2))
    return math.degrees(lo2), math.degrees(la2)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------------------
# Lettura ODIM_H5
# ---------------------------------------------------------------------------
def _attr(attrs, key, default=None):
    v = attrs.get(key, default)
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if isinstance(v, np.ndarray) and v.size == 1:
        v = v.reshape(-1)[0]
    if isinstance(v, np.generic):
        v = v.item()
    return v


def _iso_from_what(what):
    d = _attr(what, "date")
    t = _attr(what, "time")
    if not d or not t:
        return None
    try:
        stamp = _dt.datetime.strptime(f"{d}{t}", "%Y%m%d%H%M%S")
    except ValueError:
        try:
            stamp = _dt.datetime.strptime(f"{d}{t}", "%Y%m%d%H%M")
        except ValueError:
            return None
    return stamp.replace(tzinfo=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_h5(source):
    """Apre un ODIM_H5 da path locale, bytes o URL http(s)."""
    import h5py
    if isinstance(source, (bytes, bytearray)):
        return h5py.File(io.BytesIO(bytes(source)), "r"), None
    if str(source).startswith("http"):
        _st, body, _hd = source_opera._http_get(str(source), timeout_s=120)
        return h5py.File(io.BytesIO(body), "r"), body
    return h5py.File(str(source), "r"), None


def read_vradh(source):
    """Legge un ODIM_H5 SCAN e ritorna il campo VRADH decodificato.

    Ritorna un dict con: site lat/lon/height, valid_time (dal gruppo `what`),
    elangle, nrays, nbins, rstart_m, rscale_m, az_deg, vrad (float32,
    NaN = nodata/undetect), nyquist (+ `nyquist_source`), gain/offset.

    Solleva models.ValidationError se VRADH manca o il file e' illeggibile.
    """
    try:
        fh, _body = _open_h5(source)
    except models.SourceError:
        raise
    except Exception as exc:
        raise models.ValidationError(f"odim_open_error:{exc}") from exc
    try:
        where = fh["where"].attrs
        lat = float(_attr(where, "lat"))
        lon = float(_attr(where, "lon"))
        height = float(_attr(where, "height", 0.0))
        # `where/maxrange` e' la portata REALE del raggio. In questi SCAN
        # OPERA FR non c'e' (misurato il 2026-11-12: where ha solo
        # height/lat/lon) -> si ricava dalla geometria del file
        # (rstart + nbins*rscale) e la fonte viene DICHIARATA in output.
        maxrange_attr = _attr(where, "maxrange")
        d1 = fh["dataset1"]
        dw = d1["where"].attrs
        nrays = int(_attr(dw, "nrays"))
        nbins = int(_attr(dw, "nbins"))
        rscale = float(_attr(dw, "rscale"))
        rstart = float(_attr(dw, "rstart", 0.0))
        a1gate = int(_attr(dw, "a1gate", 0))
        elangle = float(_attr(dw, "elangle", 0.0))
        node = None
        for name in d1.keys():
            if not str(name).startswith("data"):
                continue
            if _attr(d1[name]["what"].attrs, "quantity", "") == "VRADH":
                node = d1[name]
                break
        if node is None:
            raise models.ValidationError("vradh_dataset_missing")
        what = node["what"].attrs
        gain = float(_attr(what, "gain", 1.0))
        offset = float(_attr(what, "offset", 0.0))
        nodata = float(_attr(what, "nodata", 255.0))
        undetect = float(_attr(what, "undetect", -1.0))
        raw = np.asarray(node["data"][...])
        # NI: prima `how` radice (OPERA), poi `dataset1/how` (PVOL iRadar).
        ni = _attr(fh["how"].attrs, "NI") if "how" in fh else None
        ni_source = "how/NI" if ni is not None else None
        if ni is None and "how" in d1:
            ni = _attr(d1["how"].attrs, "NI")
            ni_source = "dataset1/how/NI" if ni is not None else None
        prf = None
        wavelength = None
        if "how" in fh:
            prf = _attr(fh["how"].attrs, "highprf")
            wavelength = _attr(fh["how"].attrs, "wavelength")
        if ni is None and prf and wavelength:
            # NI = PRF * lambda / 4. ATTENZIONE: per un radar dual-PRF
            # questo e' il Nyquist della singola PRF, non quello esteso:
            # il campo `nyquist_note` lo dichiara.
            ni = float(prf) * (float(wavelength) / 100.0) / 4.0
            ni_source = "prf*wavelength/4"
        # ODIM: `startazA`/`stopazA` sono gli ESTREMI del raggio, non il
        # centro. Usare lo start come centro sfasava di mezzo raggio (0.5 deg,
        # 2.2 km a 248 km: abbastanza per far cadere Roma fuori dal poligono
        # di copertura). Il centro e' la media dei due estremi.
        az_start = None
        if "how" in d1 and "startazA" in d1["how"].attrs:
            az_start = np.asarray(d1["how"].attrs["startazA"], dtype="float64")
        az_stop = None
        if "how" in d1 and "stopazA" in d1["how"].attrs:
            az_stop = np.asarray(d1["how"].attrs["stopazA"], dtype="float64")
        if az_start is None or az_start.size != nrays:
            az_start = (np.arange(nrays, dtype="float64") + a1gate) % 360.0
            az_stop = az_start + 360.0 / float(nrays)
        if az_stop is None or az_stop.size != nrays:
            az_stop = az_start + 360.0 / float(nrays)
        az_center = (az_start + az_stop) / 2.0
        valid_time = _iso_from_what(fh["what"].attrs) if "what" in fh else None
    except models.ValidationError:
        raise
    except Exception as exc:
        raise models.ValidationError(f"odim_read_error:{exc}") from exc
    finally:
        try:
            fh.close()
        except Exception:
            pass

    v = decode_vradh(raw, gain, offset, nodata, undetect)
    grid_max = float(rstart + nbins * rscale)
    if maxrange_attr is None:
        maxrange = np.full(nrays, grid_max, dtype="float64")
        maxrange_source = "grid_geometry"
    else:
        arr = np.asarray(maxrange_attr, dtype="float64").reshape(-1)
        if arr.size == 1:
            arr = np.full(nrays, float(arr[0]), dtype="float64")
            maxrange_source = "where/maxrange"
        elif arr.size != nrays:
            arr = np.full(nrays, grid_max, dtype="float64")
            maxrange_source = "grid_geometry"
        else:
            maxrange_source = "where/maxrange"
        maxrange = arr
    return {
        "lat": lat,
        "lon": lon,
        "height_m": height,
        "valid_time": valid_time,
        "elangle_deg": elangle,
        "nrays": nrays,
        "nbins": nbins,
        "rstart_m": rstart,
        "rscale_m": rscale,
        "az_deg": np.mod(az_center, 360.0),
        "vrad": v,
        "maxrange_m": maxrange,
        "maxrange_source": maxrange_source,
        "nyquist": (float(ni) if ni is not None else None),
        "nyquist_source": ni_source,
        "nyquist_note": ("single_prf_nyquist" if ni_source == "prf*wavelength/4"
                         else None),
        "gain": gain,
        "offset": offset,
        "nodata": nodata,
        "undetect": undetect,
    }


def decode_vradh(raw, gain, offset, nodata, undetect):
    """raw -> m/s con NaN su nodata/undetect (gain puo' essere negativo)."""
    a = np.asarray(raw)
    v = a.astype("float32") * float(gain) + float(offset)
    bad = (a == nodata)
    if undetect is not None and not (isinstance(undetect, float)
                                     and math.isnan(undetect)):
        bad = bad | (a == undetect)
    v[bad] = np.nan
    return v


# ---------------------------------------------------------------------------
# Dealiasing region-based
# ---------------------------------------------------------------------------
def _label_regions(mask):
    """Etichette 8-connesse delle regioni True (0 = sfondo).

    Usa scipy.ndimage.label (dipendenza dichiarata in requirements.txt). Il
    bordo azimuthale e' periodico: si etichetta su una copia affiancata e si
    tiene la prima meta', cosi' un fold che attraversa 0/360 resta UNA
    regione (senza, si srotolerebbero due meta' in modo indipendente).
    """
    from scipy import ndimage
    if not mask.any():
        return np.zeros(mask.shape, dtype="int32")
    doubled = np.concatenate([mask, mask], axis=0)
    lab, _n = ndimage.label(doubled, structure=np.ones((3, 3), dtype=bool))
    lab = lab[:mask.shape[0]]
    # Le due copie possono aver dato etichette diverse alla stessa regione
    # periodica: si rinumerano per equivalenza (unione delle etichette che
    # compaiono sulla stessa colonna di bordo).
    return lab.astype("int32")


def _region_labels(mask):
    """Etichette per regione con continuita' azimuthale (0/360)."""
    labels = _label_regions(mask)
    if not mask.any():
        return labels
    # unisce le etichette che toccano lo stesso punto sui due bordi
    top, bottom = labels[0], labels[-1]
    pairs = set()
    for a, b in zip(top, bottom):
        if a and b and a != b:
            pairs.add((min(a, b), max(a, b)))
    if pairs:
        # union-find minimale
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        for a, b in pairs:
            union(a, b)
        flat = labels.ravel()
        for i, v in enumerate(flat):
            if v:
                flat[i] = find(int(v))
        labels = flat.reshape(labels.shape)
    return labels


def dealias_region_based(vrad, nyquist, max_iterations=4,
                         fold_fraction=0.9):
    """Srotola il campo VRADH con il metodo region-based.

    Ritorna dict:
      vrad        campo srotolato (NaN dove non valido/non risolvibile)
      method      "region_based"
      status      "ok" | "no_nyquist" | "unresolved"
      regions     numero di regioni piegate trattate
      folded_gates / unresolved_gates
      notes       lista di motivi (diagnostica, nessun numero inventato)

    Se `nyquist` e' None o <= 0 il campo resta AVVOLTO e status="no_nyquist"
    (il chiamante deve pubblicare null con motivo, non usare lo shear).
    """
    v = np.array(vrad, dtype="float64", copy=True)
    out = {"vrad": v, "method": DEALIAS_METHOD, "status": "ok",
           "regions": 0, "folded_gates": 0, "unresolved_gates": 0,
           "notes": []}
    if nyquist is None or not np.isfinite(nyquist) or nyquist <= 0:
        out["status"] = "no_nyquist"
        out["notes"].append("nyquist_missing")
        return out
    ni = float(nyquist)
    interval = 2.0 * ni
    finite = np.isfinite(v)
    folded = finite & (np.abs(v) > fold_fraction * ni)
    out["folded_gates"] = int(folded.sum())
    if not folded.any():
        return out

    unresolved = np.zeros(v.shape, dtype=bool)
    ids = []
    for _ in range(int(max_iterations)):
        labels = _region_labels(folded)
        ids = [i for i in np.unique(labels) if i > 0]
        if not ids:
            break
        # regioni grandi prima: hanno piu' probabilita' di avere un bordo
        # gia' srotolato su cui appoggiarsi.
        ids.sort(key=lambda i: -int((labels == i).sum()))
        changed = 0
        for rid in ids:
            region = labels == rid
            # vicini: celle adiacenti alla regione ma fuori da essa
            grown = np.zeros_like(region)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    shifted = np.roll(region, dy, axis=0)
                    if dx > 0:
                        shifted = np.concatenate(
                            [np.zeros((shifted.shape[0], dx), dtype=bool),
                             shifted[:, :-dx]], axis=1)
                    elif dx < 0:
                        shifted = np.concatenate(
                            [shifted[:, -dx:],
                             np.zeros((shifted.shape[0], -dx), dtype=bool)],
                            axis=1)
                    grown |= shifted
            neigh = grown & ~region & finite
            ref_vals = v[neigh]
            ref_vals = ref_vals[np.isfinite(ref_vals)]
            if ref_vals.size < 4:
                unresolved |= region
                out["notes"].append(f"region_{int(rid)}_no_reference")
                continue
            ref = float(np.median(ref_vals))
            cur = v[region]
            cur = cur[np.isfinite(cur)]
            if cur.size == 0:
                continue
            best_k, best_d = 0, None
            for k in range(-4, 5):
                d = abs(float(np.median(cur + k * interval)) - ref)
                if best_d is None or d < best_d:
                    best_k, best_d = k, d
            if best_k:
                v[region] = v[region] + best_k * interval
                changed += 1
        # ricalcola i gate piegati: dopo lo srotolamento possono essere
        # spariti (il valore ora e' continuo) o essersi spostati.
        new_folded = finite & (np.abs(v) > fold_fraction * ni) & ~unresolved
        if not changed and np.array_equal(new_folded, folded):
            break
        folded = new_folded
        if not folded.any():
            break

    # Controllo di continuita': un salto > 1.5*NI fra raggi adiacenti sullo
    # stesso anello e' un fold non risolto.
    jump = np.zeros(v.shape, dtype=bool)
    dv = np.abs(np.roll(v, -1, axis=0) - np.roll(v, 1, axis=0))
    jump = np.isfinite(dv) & (dv > 1.5 * ni)
    unresolved |= jump
    if unresolved.any():
        v[unresolved] = np.nan
        out["notes"].append("unresolved_fold_jumps")
    out["vrad"] = v
    out["regions"] = int(len(ids))
    out["unresolved_gates"] = int(unresolved.sum())
    if unresolved.any():
        out["status"] = "unresolved"
    return out


# ---------------------------------------------------------------------------
# Shear azimutale
# ---------------------------------------------------------------------------
def azimuthal_shear(vrad, az_start_deg, rstart_m, rscale_m,
                    window_rays=1):
    """Shear azimutale (m/s)/km su anelli a distanza costante.

    Per il raggio i e il gate j:
        shear = (v[i+w, j] - v[i-w, j]) / (2*w*Delta_az_rad * r_j)
    con w = `window_rays` (default 1 -> 3 raggi). Il gradiente e' calcolato
    sull'arco a distanza costante, non sulla coordinata cartesiana.
    Ritorna (shear, range_km) con NaN dove uno dei due estremi e' invalido.
    """
    v = np.asarray(vrad, dtype="float64")
    nrays, nbins = v.shape
    w = max(1, int(window_rays))
    r_km = (float(rstart_m) + (np.arange(nbins, dtype="float64") + 0.5)
            * float(rscale_m)) / 1000.0
    daz = np.deg2rad(2.0 * w * (360.0 / float(nrays)))
    ds_km = daz * r_km
    a = np.roll(v, -w, axis=0)
    b = np.roll(v, w, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        shear = (a - b) / ds_km[None, :]
    shear[~np.isfinite(shear)] = np.nan
    return shear, r_km


def _local_maxima(values, size=5):
    """Maschera dei massimi locali (size x size) con azimuth periodico."""
    from scipy import ndimage
    if values.size == 0:
        return np.zeros_like(values, dtype=bool)
    mx = ndimage.maximum_filter(values, size=(size, size),
                                mode=("wrap", "nearest"))
    return np.isfinite(values) & (values >= mx)


# ---------------------------------------------------------------------------
# Candidati di shear
# ---------------------------------------------------------------------------
def shear_candidates(volume, cfg, previous=None, other=None):
    """Estrae i candidati di shear da un volume (una elevazione).

    `previous` = volume della stessa elevazione alla scansione precedente
    (persistenza), `other` = volume di un'altra elevazione (coerenza
    verticale). Ritorna una lista di dict gia' filtrati dai gate.
    """
    cf = cfg
    vrad = volume["vrad"]
    shear, r_km = azimuthal_shear(
        vrad, volume["az_deg"], volume["rstart_m"],
        volume["rscale_m"], window_rays=cf["shear_window_rays"])
    h_km = beam_height_km(r_km, volume["elangle_deg"], volume["height_m"])
    gate = ((r_km >= float(cf["min_range_km"]))
            & (r_km <= float(cf["max_range_km"]))
            & (h_km <= float(cf["max_beam_height_km"]))
            & np.isfinite(shear))
    abs_s = np.where(gate, np.abs(shear), 0.0)
    peaks = _local_maxima(abs_s, size=5) & gate & (
        abs_s >= float(cf["shear_threshold_ms_km"]))
    rays, gates = np.nonzero(peaks)
    out = []
    for i, j in zip(rays, gates):
        cand = _candidate_from_gate(volume, shear, r_km, h_km, i, j, cf)
        if cand is None:
            continue
        if previous is not None:
            cand["persistence_scans"] = _persistence(
                cand, previous, cf)
        else:
            cand["persistence_scans"] = 1
        if other is not None:
            cand["vertical_coherent"] = _vertical_ok(cand, other, cf)
        else:
            cand["vertical_coherent"] = None
        out.append(cand)
    # dedup: due massimi adiacenti dello stesso blob -> uno solo
    out.sort(key=lambda c: -abs(c["shear_ms_km"]))
    kept = []
    for c in out:
        if any(abs(c["azimuth_deg"] - k["azimuth_deg"]) <= 2.0
               and abs(c["range_km"] - k["range_km"]) <= 2.0
               for k in kept):
            continue
        kept.append(c)
    return kept


def _candidate_from_gate(volume, shear, r_km, h_km, i, j, cf):
    s = float(shear[i, j])
    if not np.isfinite(s):
        return None
    az = float(volume["az_deg"][i]) % 360.0
    rng = float(r_km[j])
    lat, lon = destination(volume["lon"], volume["lat"], az, rng)
    h = float(h_km[j])
    if h < float(cf["high_beam_height_km"]):
        conf = "high"
    elif h < float(cf["medium_beam_height_km"]):
        conf = "medium"
    else:
        conf = "low"
    return {
        "site": volume["site"],
        "radar": volume["site"],
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "azimuth_deg": round(az, 2),
        "range_km": round(rng, 2),
        "beam_height_km": round(h, 3),
        "elevation_deg": round(float(volume["elangle_deg"]), 2),
        "shear_ms_km": round(s, 2),
        "shear_sign": ("increasing_azimuth" if s > 0
                       else "decreasing_azimuth"),
        "valid_time": volume["valid_time"],
        "dealias_method": volume.get("dealias_method", DEALIAS_METHOD),
        "dealias_status": volume.get("dealias_status", "ok"),
        "confidence": conf,
        "ray": int(i),
        "gate": int(j),
        "persistence_scans": 1,
        "vertical_coherent": None,
    }


def _same_gate_shear(volume, ray, gate, tol_r, tol_g):
    """Shear al gate (ray, gate) o, se invalido, al vicino piu' prossimo."""
    if volume is None or "shear" not in volume:
        return None
    sh = volume["shear"]
    nrays, nbins = sh.shape
    if 0 <= gate < nbins and np.isfinite(sh[ray, gate]):
        return float(sh[ray, gate])
    best, best_d = None, None
    for dr in range(-int(tol_r), int(tol_r) + 1):
        for dg in range(-int(tol_g), int(tol_g) + 1):
            i = (ray + dr) % nrays
            j = gate + dg
            if 0 <= j < nbins:
                val = sh[i, j]
                if np.isfinite(val):
                    d = abs(dr) + abs(dg)
                    if best_d is None or d < best_d:
                        best, best_d = float(val), d
    return best


def _persistence(cand, previous, cf):
    prev = _same_gate_shear(previous, cand["ray"], cand["gate"],
                            cf["vertical_tolerance_rays"],
                            cf["vertical_tolerance_gates"])
    if prev is None:
        return 1
    same_sign = (prev * cand["shear_ms_km"]) > 0
    strong = abs(prev) >= (float(cf["shear_threshold_ms_km"])
                           * float(cf["persistence_factor"]))
    return 2 if (same_sign and strong) else 1


def _vertical_ok(cand, other, cf):
    val = _same_gate_shear(other, cand["ray"], cand["gate"],
                           cf["vertical_tolerance_rays"],
                           cf["vertical_tolerance_gates"])
    if val is None:
        return False
    same_sign = (val * cand["shear_ms_km"]) > 0
    strong = abs(val) >= (float(cf["shear_threshold_ms_km"])
                          * float(cf["vertical_factor"]))
    return bool(same_sign and strong)


# ---------------------------------------------------------------------------
# Discovery S3 e ingestione
# ---------------------------------------------------------------------------
def _http_text(url, timeout_s=60):
    _st, body, _hd = source_opera._http_get(url, timeout_s=timeout_s)
    return body.decode("utf-8", "replace")


def list_scan_keys(day, site, s3_base=S3_BASE, country=COUNTRY,
                   timeout_s=60, max_pages=8):
    """Chiavi SCAN di un sito per un giorno (paginazione inclusa)."""
    day_s = day.strftime("%Y/%m/%d") if not isinstance(day, str) else day
    prefix = f"{day_s}/{country}/{site}/SCAN/"
    keys, token = [], None
    for _ in range(int(max_pages)):
        params = {"list-type": "2", "prefix": prefix, "max-keys": 1000}
        if token:
            params["continuation-token"] = token
        text = _http_text(f"{s3_base}?{_urlencode(params)}",
                          timeout_s=timeout_s)
        keys.extend(re.findall(r"<Key>([^<]+)</Key>", text))
        if "<IsTruncated>true</IsTruncated>" not in text:
            break
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>",
                      text)
        if not m:
            break
        token = m.group(1)
    return keys


def _urlencode(params):
    import urllib.parse
    return urllib.parse.urlencode(params)


_KEY_RE = re.compile(r"@(\d{8}T\d{4})@([0-9.]+)@([A-Z_]+)\.h5$")


def parse_scan_key(key):
    """`fraja@20261110T1329@0.5@DBZH_TH_VRADH.h5` -> (datetime, el)."""
    m = _KEY_RE.search(os.path.basename(str(key)))
    if not m:
        return None
    try:
        ts = _dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None
    return ts, float(m.group(2))


def group_volumes(keys, window_min=5.0):
    """Raggruppa le chiavi SCAN in VOLUMI (un ciclo di scansione).

    Il radar pubblica UNA elevazione per timestamp (0.6, 1.01, 1.41, 2.5 in
    sequenza) e chiude il ciclo con la elevazione PIU' BASSA: un "volume" e'
    l'insieme dei file dall'apertura (dopo la 0.6 precedente) alla 0.6
    successiva. Il raggruppamento per soli intervalli non funziona: i file
    distano 1 minuto e un ciclo ne dura 5, quindi il gap non esiste.
    `window_min` e' il tetto di durata di un volume (oltre -> nuovo volume,
    per non fondere due cicli quando manca la 0.6).
    Ritorna una lista ordinata di dict {"ts": T, "elevations": {el: key}}.
    """
    rows = []
    for k in keys:
        p = parse_scan_key(k)
        if p:
            rows.append((p[0], p[1], k))
    if not rows:
        return []
    rows.sort()
    el_min = min(r[1] for r in rows)
    volumes = []
    cur = None
    for ts, el, key in rows:
        too_long = (cur is not None
                    and (ts - cur["start"]).total_seconds() > window_min * 60.0)
        if cur is None or cur["closed"] or too_long:
            cur = {"ts": ts, "start": ts, "elevations": {}, "closed": False}
            volumes.append(cur)
        cur["elevations"][el] = key
        cur["ts"] = max(cur["ts"], ts)
        if el == el_min:
            cur["closed"] = True
    return [v for v in volumes if v["elevations"]]


def _latest_day_with_data(site, cfg, as_of):
    """Giorno piu' recente (UTC) con almeno un file SCAN per il sito."""
    day = (as_of or _dt.datetime.now(_dt.timezone.utc)).astimezone(
        _dt.timezone.utc).date()
    for back in range(int(cfg["lookback_days"])):
        d = day - _dt.timedelta(days=back)
        try:
            keys = list_scan_keys(d, site, s3_base=cfg["s3_base"],
                                  country=cfg["country"],
                                  timeout_s=cfg["timeout_s"])
        except Exception:
            keys = []
        if keys:
            return d, keys
    return None, []


def _download(key, cfg, cache):
    if key in cache:
        return cache[key]
    url = cfg["s3_base"] + key
    _st, body, _hd = source_opera._http_get(url, timeout_s=cfg["timeout_s"])
    cache[key] = body
    return body


def _prefetch(keys, cfg, cache):
    """Scarica in PARALLELO le chiavi mancanti (max 8 connessioni).

    Il costo per run e' dominato dalla latenza del bucket S3, non dalla
    banda: 4 file in serie misurati 2.4 s, gli stessi 8 in parallelo 1.5 s.
    """
    import concurrent.futures as _fut
    todo = [k for k in dict.fromkeys(keys) if k not in cache]
    if not todo:
        return
    def _one(key):
        _st, body, _hd = source_opera._http_get(
            cfg["s3_base"] + key, timeout_s=cfg["timeout_s"])
        return key, body
    with _fut.ThreadPoolExecutor(max_workers=min(8, len(todo))) as ex:
        for key, body in ex.map(_one, todo):
            cache[key] = body


def _prepare_volume(vol, site, cfg):
    """Decodifica + dealias + shear di un volume (una elevazione)."""
    raw = read_vradh(vol["_bytes"])
    d = dealias_region_based(raw["vrad"], raw["nyquist"])
    out = dict(raw)
    out["site"] = site
    out["vrad"] = d["vrad"]
    out["dealias_method"] = d["method"]
    out["dealias_status"] = d["status"]
    out["dealias_notes"] = d["notes"]
    out["unresolved_gates"] = d["unresolved_gates"]
    out["folded_gates"] = d["folded_gates"]
    sh, r_km = azimuthal_shear(out["vrad"], out["az_deg"],
                               out["rstart_m"], out["rscale_m"],
                               window_rays=cfg["shear_window_rays"])
    out["shear"] = sh
    out["range_km"] = r_km
    out["beam_height_km"] = beam_height_km(r_km, out["elangle_deg"],
                                           out["height_m"])
    return out


def collect(config=None, as_of=None):
    """Scarica i volumi piu' recenti e calcola i candidati di shear.

    Ritorna dict con status, per-sito (valid_time, elevazioni, nyquist),
    candidati filtrati e contatori di costo (file e byte scaricati, secondi).
    Nessuna eccezione di rete esce: un sito che fallisce finisce in `errors`.
    """
    cfg = config_for(config)
    t0 = _dt.datetime.now(_dt.timezone.utc)
    res = {"schema_version": SCHEMA_VERSION, "status": "ok",
           "generated_at": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "as_of": (as_of.astimezone(_dt.timezone.utc).strftime(
               "%Y-%m-%dT%H:%M:%SZ") if as_of else None),
           "license": LICENSE, "attribution": ATTRIBUTION,
           "sites": [], "candidates": [], "coverage": None,
           "errors": [], "cost": {"files": 0, "bytes": 0, "seconds": 0.0}}
    cache = {}
    all_candidates = []
    coverage_inputs = []
    for site in cfg["sites"]:
        st = {"site": site, "label": SITE_LABEL.get(site, site),
              "status": "ok", "valid_time": None, "elevations": [],
              "nyquist": None, "nyquist_source": None,
              "dealias_status": None, "candidates": 0,
              "files": 0, "bytes": 0}
        used_keys = set()
        try:
            day, keys = _latest_day_with_data(site, cfg, as_of)
            if not keys:
                st["status"] = "no_data"
                st["null_reason"] = "no_opera_scan_for_site"
                res["sites"].append(st)
                continue
            volumes = group_volumes(keys, window_min=5.0)
            if as_of is not None:
                volumes = [v for v in volumes if v["ts"] <= as_of]
            if not volumes:
                st["status"] = "no_data"
                st["null_reason"] = "no_volume_before_as_of"
                res["sites"].append(st)
                continue
            latest = volumes[-1]
            previous = volumes[-2] if len(volumes) > 1 else None
            elevs = sorted(latest["elevations"])[:int(cfg["max_elevations_shear"])]
            cov_elevs = sorted(latest["elevations"])[
                :int(cfg["max_elevations_coverage"])]
            if not elevs:
                st["status"] = "no_data"
                st["null_reason"] = "no_vradh_elevation"
                res["sites"].append(st)
                continue
            el0 = elevs[0]
            # --- prefetch PARALLELO: il collo di bottiglia e' la latenza S3
            # (misurato: 4 file in serie 2.4 s, gli stessi 8 in parallelo
            # 1.5 s). Il tetto e' 8 connessioni, come fetch.FETCH_WORKERS_MAX.
            want = [latest["elevations"][el] for el in cov_elevs]
            if previous is not None:
                for el in elevs:
                    if el in previous["elevations"]:
                        want.append(previous["elevations"][el])
            window_used = 0
            for vol in reversed(volumes[:-1]):
                if window_used >= int(cfg["coverage_max_volumes"]):
                    break
                if (latest["ts"] - vol["ts"]).total_seconds() > float(
                        cfg["coverage_window_min"]) * 60.0:
                    break
                if el0 in vol["elevations"]:
                    want.append(vol["elevations"][el0])
                    window_used += 1
            _prefetch(want, cfg, cache)
            prepared = {}
            by_key = {}
            for el in elevs:
                key = latest["elevations"][el]
                body = _download(key, cfg, cache)
                used_keys.add(key)
                vol = _prepare_volume({"_bytes": body}, site, cfg)
                vol["key"] = key
                prepared[el] = vol
                by_key[key] = vol
            base = prepared[el0]
            st["valid_time"] = base["valid_time"]
            st["elevations"] = [{"elevation_deg": e,
                                 "valid_time": prepared[e]["valid_time"],
                                 "nyquist": prepared[e]["nyquist"],
                                 "nyquist_source": prepared[e]["nyquist_source"],
                                 "dealias_status": prepared[e]["dealias_status"],
                                 "unresolved_gates": prepared[e]["unresolved_gates"]}
                                for e in elevs]
            st["nyquist"] = base["nyquist"]
            st["nyquist_source"] = base["nyquist_source"]
            st["dealias_status"] = base["dealias_status"]
            prev_vol = None
            if previous is not None and el0 in previous["elevations"]:
                key = previous["elevations"][el0]
                body = _download(key, cfg, cache)
                used_keys.add(key)
                prev_vol = _prepare_volume({"_bytes": body}, site, cfg)
                prev_vol["key"] = key
                by_key[key] = prev_vol
            other = prepared.get(elevs[1]) if len(elevs) > 1 else None
            cands = shear_candidates(base, cfg, previous=prev_vol, other=other)
            for c in cands:
                c["persistence_required"] = int(cfg["persistence_scans"])
                c["passes"] = bool(
                    c["persistence_scans"] >= int(cfg["persistence_scans"])
                    and (c["vertical_coherent"] is not False))
            st["candidates"] = len(cands)
            all_candidates.extend(cands)
            # copertura: TUTTE le elevazioni basi del volume (non solo quelle
            # usate per lo shear) + la finestra precedente sulla piu' bassa.
            cov_scans = {}
            for el in cov_elevs:
                if el in prepared:
                    cov_scans[el] = _scan_like(prepared[el])
                    continue
                key = latest["elevations"][el]
                try:
                    body = _download(key, cfg, cache)
                    used_keys.add(key)
                    cov_scans[el] = _scan_like(read_vradh(body))
                except Exception as exc:
                    st.setdefault("coverage_errors", []).append(
                        f"el_{el}:{exc.__class__.__name__}")
            coverage_inputs.append((site, volumes, cov_scans, el0, by_key,
                                    cfg, cache, st))
            st["files"] = len(used_keys)
            st["bytes"] = int(sum(len(cache[k]) for k in used_keys
                                  if k in cache))
        except Exception as exc:
            st["status"] = "error"
            st["null_reason"] = f"{exc.__class__.__name__}:{exc}"
            res["errors"].append({"site": site, "error": st["null_reason"]})
        res["sites"].append(st)

    # tieni solo i candidati che passano TUTTI i gate (persistenza+verticale)
    res["candidates"] = [c for c in all_candidates if c["passes"]]
    res["candidates_rejected"] = [c for c in all_candidates if not c["passes"]]
    # copertura (dai raggi con gate validi, non un cerchio)
    try:
        res["coverage"] = build_coverage(
            coverage_inputs, cfg,
            previous_doc=load_previous_coverage(cfg["coverage_out"]))
    except Exception as exc:
        res["errors"].append({"coverage": f"{exc.__class__.__name__}:{exc}"})
    if res.get("coverage"):
        st74 = coverage_status(res["coverage"], 12.48, 41.89)
        res["coverage_rome"] = st74
        res["coverage_rome_geometric_inside"] = st74.get(
            "geometric", {}).get("inside")
        res["coverage_rome_observed_inside"] = st74.get(
            "observed", {}).get("inside")
        res["coverage_rome_inside"] = res["coverage_rome_geometric_inside"]
    res["cost"]["files"] = int(sum(s["files"] for s in res["sites"]))
    res["cost"]["bytes"] = int(sum(s["bytes"] for s in res["sites"]))
    res["cost"]["seconds"] = round(
        (_dt.datetime.now(_dt.timezone.utc) - t0).total_seconds(), 2)
    if res["errors"] and not res["candidates"]:
        res["status"] = "degraded"
    return res


# ---------------------------------------------------------------------------
# Copertura GeoJSON (minima elevazione + qualsiasi elevazione)
# ---------------------------------------------------------------------------
def _scan_like(vol, max_range_m=None):
    """Dict con lo schema atteso da doppler_coverage.site_features()."""
    v = vol["vrad"]
    valid = np.isfinite(v)
    nb = valid.shape[1]
    idx = np.arange(nb, dtype="float64")
    rng = vol["rstart_m"] + (idx + 0.5) * vol["rscale_m"]
    max_r = np.zeros(valid.shape[0], dtype="float64")
    for i in range(valid.shape[0]):
        w = np.nonzero(valid[i])[0]
        if w.size:
            max_r[i] = rng[w[-1]] + vol["rscale_m"] / 2.0
    if max_range_m is not None:
        max_r = np.maximum(max_r, max_range_m)
    mr = vol.get("maxrange_m")
    if mr is None:
        mr = np.full(vol["nrays"], float(vol["rstart_m"]) + nb * float(vol["rscale_m"]))
    else:
        mr = np.asarray(mr, dtype="float64").reshape(-1)
        if mr.size == 1:
            mr = np.full(vol["nrays"], float(mr[0]))
    return {
        "lat": vol["lat"], "lon": vol["lon"], "height_m": vol["height_m"],
        "elangle_deg": vol["elangle_deg"], "nrays": vol["nrays"],
        "nbins": nb, "rscale_m": vol["rscale_m"], "rstart_m": vol["rstart_m"],
        "a1gate": 0, "az_start_deg": vol["az_deg"],
        "max_range_m": max_r, "n_valid_bins": valid.sum(axis=1),
        "maxrange_m": mr,
        "maxrange_source": vol.get("maxrange_source", "grid_geometry"),
        "coverage_fraction": float((max_r > 0).mean()),
        "max_range_km": round(float(max_r.max()) / 1000.0, 2),
    }


def geometric_scan(vol, cfg):
    """Scan GEOMETRICO: portata del raggio tagliata dal limite di altezza.

    NON usa i gate VRADH: un raggio senza eco ha COMUNQUE copertura (il fascio
    campiona il volume, semplicemente non c'e' velocita' da misurare).
    La portata per raggio viene da `where/maxrange` se presente, altrimenti
    dalla geometria della griglia (`rstart + nbins*rscale`); la fonte resta
    dichiarata in `maxrange_source`. Il taglio e' `min(maxrange, r(h_max))`.
    """
    el = float(vol["elangle_deg"])
    nrays = int(vol["nrays"])
    r_max = np.asarray(vol.get("maxrange_m"), dtype="float64").reshape(-1)
    if r_max.size == 1:
        r_max = np.full(nrays, float(r_max[0]), dtype="float64")
    elif r_max.size != nrays:
        r_max = np.full(nrays, float(vol["rstart_m"])
                        + int(vol["nbins"]) * float(vol["rscale_m"]))
    limit = range_at_beam_height(float(cfg["max_beam_height_km"]), el,
                                 vol.get("height_m", 0.0)) * 1000.0
    out = dict(vol)
    out["max_range_m"] = np.minimum(r_max, limit)
    out["az_start_deg"] = np.asarray(vol.get("az_deg", vol["az_start_deg"]),
                                     dtype="float64")
    out["n_valid_bins"] = np.zeros(nrays, dtype="int32")
    out["coverage_fraction"] = 1.0
    out["max_range_km"] = round(float(out["max_range_m"].max()) / 1000.0, 2)
    return out


def _expand_beam(scan):
    """Poligono = IMPRONTA DEL FASCIO, non il centro del raggio.

    Ogni raggio copre `step` gradi (1 per questi radar): il punto a meta'
    fra due centri appartiene al raggio piu' vicino, non alla corda fra i due
    raggi. Senza questa espansione un salto di range fra due raggi adiacenti
    (es. 245.8 -> 250.6 km) fa rientrare la corda di ~1 km e un punto a
    247.9 km sulla verticale del raggio lungo risulta FUORI dal poligono
    (misurato su Roma il 2026-11-11T19:04Z). Con l'impronta del fascio il
    bordo e' una scala: ogni raggio vale per tutto il suo settore.
    """
    az = np.asarray(scan.get("az_deg", scan["az_start_deg"]), dtype="float64")
    r = np.asarray(scan["max_range_m"], dtype="float64")
    if az.size == 0:
        return scan
    step = 360.0 / float(az.size)
    out = dict(scan)
    edges = np.concatenate([az - step / 2.0 + 1e-4, az + step / 2.0 - 1e-4]) % 360.0
    out["az_deg"] = edges
    out["az_start_deg"] = edges
    out["max_range_m"] = np.concatenate([r, r])
    return out


def load_previous_coverage(path):
    """Legge il GeoJSON di copertura del run precedente (None se assente)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and doc.get("type") == "FeatureCollection":
            return doc
    except Exception:
        pass
    return None


def _previous_rays(doc, site, layer, day):
    """Array dei range massimi per raggio accumulati nello stesso giorno."""
    if not doc:
        return None
    for f in doc.get("features", []):
        p = f.get("properties") or {}
        if (p.get("site") == site and p.get("layer") == layer
                and p.get("day") == day):
            rays = p.get("rays_km")
            if isinstance(rays, list) and len(rays) == 360:
                try:
                    return np.asarray(rays, dtype="float64") * 1000.0
                except (TypeError, ValueError):
                    return None
    return None


def point_in_ring(lon, lat, ring):
    """Ray casting su un anello [[lon, lat], ...] (bordo incluso)."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        # bordo
        if (min(x1, x2) - 1e-9 <= lon <= max(x1, x2) + 1e-9
                and min(y1, y2) - 1e-9 <= lat <= max(y1, y2) + 1e-9):
            cross = (x2 - x1) * (lat - y1) - (y2 - y1) * (lon - x1)
            if abs(cross) < 1e-9:
                return True
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < xin:
                inside = not inside
    return inside


def build_coverage(coverage_inputs, cfg, previous_doc=None):
    """FeatureCollection con due layer: `min_elevation` e `any_elevation`.

    `min_elevation` fonde i volumi della finestra `coverage_window_min` alla
    elevazione piu' bassa; `any_elevation` aggiunge le altre elevazioni
    ingerite. Il poligono e' l'inviluppo dei range MASSIMI per azimuth con
    almeno un gate valido: non e' un cerchio teorico.
    """
    from . import doppler_coverage as dc
    generated = _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    features = []
    for site, volumes, cov_scans, el0, by_key, cf, cache, st in coverage_inputs:
        if not cov_scans:
            continue
        # merge della finestra alla elevazione minima
        merged = None
        used = 0
        t_end = volumes[-1]["ts"]
        for vol in reversed(volumes):
            if (t_end - vol["ts"]).total_seconds() > float(
                    cf["coverage_window_min"]) * 60.0:
                break
            if el0 not in vol["elevations"]:
                continue
            key = vol["elevations"][el0]
            pv = by_key.get(key)
            if pv is None:
                try:
                    body = _download(key, cf, cache)
                    pv = _prepare_volume({"_bytes": body}, site, cf)
                    by_key[key] = pv
                    st["files"] += 1
                    st["bytes"] += len(body)
                except Exception:
                    continue
            scan = _scan_like(pv) if "vrad" in pv else dict(pv)
            used += 1
            if merged is None:
                merged = scan
            else:
                merged = dict(merged)
                merged["max_range_m"] = np.maximum(merged["max_range_m"],
                                                   scan["max_range_m"])
        if merged is None:
            continue
        day_s = (st.get("valid_time") or "")[:10]
        # ---- coverage_GEOMETRIC: portata del fascio, INDIPENDENTE dall'eco.
        # Un raggio senza eco e' coperto: il fascio campiona il volume, non
        # c'e' solo la velocita' da misurare. Portata per raggio (maxrange o
        # griglia) tagliata dal limite di altezza del fascio (<= 8 km).
        geo, geo_el, geo_src = None, None, None
        for el in sorted(cov_scans):
            g = geometric_scan(cov_scans[el], cf)
            if geo is None:
                geo, geo_el, geo_src = g, el, g.get("maxrange_source")
            else:
                geo = dict(geo)
                geo["max_range_m"] = np.maximum(geo["max_range_m"],
                                                g["max_range_m"])
        if geo is not None:
            geo_feats = dc.site_features(
                _expand_beam(geo), site, cf["country"], st["valid_time"],
                generated, min_range_km=float(cf["coverage_min_range_km"]),
                max_points=720)
            for f in geo_feats:
                p = f["properties"]
                p["kind"] = "geometric"
                p["layer"] = "geometric"
                p["elevation_deg"] = round(float(geo_el), 2)
                p["elevations_merged"] = sorted(cov_scans)
                p["max_range_source"] = geo_src
                p["beam_height_at_max_km"] = round(float(beam_height_km(
                    p["max_range_km"], geo_el, geo.get("height_m", 0.0))), 2)
                p["day"] = day_s
                p["rays_km"] = [round(float(x) / 1000.0, 2)
                                for x in geo["max_range_m"]]
            features.extend(geo_feats)
        prev = _previous_rays(previous_doc, site, "min_elevation", day_s)
        if prev is not None:
            merged = dict(merged)
            merged["max_range_m"] = np.maximum(merged["max_range_m"], prev)
        min_feats = dc.site_features(_expand_beam(merged), site, cf["country"],
                                     st["valid_time"], generated,
                                     min_range_km=float(cf["coverage_min_range_km"]),
                                     max_points=720)
        for f in min_feats:
            p = f["properties"]
            p["kind"] = "observed"
            p["layer"] = "min_elevation"
            p["elevation_deg"] = round(float(el0), 2)
            p["volumes_merged"] = used
            p["day"] = day_s
            p["max_range_source"] = "gates"
            p["beam_height_at_max_km"] = round(float(beam_height_km(
                p["max_range_km"], el0, merged.get("height_m", 0.0))), 2)
            p["rays_km"] = [
                round(float(x) / 1000.0, 2) for x in merged["max_range_m"]]
        features.extend(min_feats)
        # any elevation: max sui volumi gia' preparati (tutte le elevazioni)
        any_scan = dict(merged)
        for el, scan in cov_scans.items():
            if el == el0:
                continue
            any_scan["max_range_m"] = np.maximum(any_scan["max_range_m"],
                                                 scan["max_range_m"])
        prev_any = _previous_rays(previous_doc, site, "any_elevation", day_s)
        if prev_any is not None:
            any_scan = dict(any_scan)
            any_scan["max_range_m"] = np.maximum(any_scan["max_range_m"],
                                                 prev_any)
        any_feats = dc.site_features(_expand_beam(any_scan), site, cf["country"],
                                     st["valid_time"], generated,
                                     min_range_km=float(cf["coverage_min_range_km"]),
                                     max_points=720)
        for f in any_feats:
            p = f["properties"]
            p["kind"] = "observed"
            p["layer"] = "any_elevation"
            p["elevation_deg"] = None
            p["elevations_merged"] = len(cov_scans)
            p["day"] = day_s
            p["max_range_source"] = "gates"
            p["beam_height_at_max_km"] = round(float(beam_height_km(
                p["max_range_km"], el0, merged.get("height_m", 0.0))), 2)
            p["rays_km"] = [
                round(float(x) / 1000.0, 2) for x in any_scan["max_range_m"]]
        features.extend(any_feats)
    kinds = {}
    for f in features:
        k = f["properties"].get("kind")
        kinds[k] = kinds.get(k, 0) + 1
    doc = {
        "type": "FeatureCollection",
        "generated_at": generated,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "kinds": {
            "geometric": (
                "Dove il fascio PUO' campionare: portata del raggio "
                "(where/maxrange, altrimenti rstart+nbins*rscale) tagliata "
                "dal limite di altezza del fascio (<= 8 km). NON dipende "
                "dall'eco: un raggio senza precipitazione resta coperto."),
            "observed": (
                "Dove esiste almeno un gate VRADH valido nella finestra: "
                "dipende dall'eco ed e' quindi dinamica."),
        },
        "data_limits": [
            "coverage_geometric: la portata per raggio viene da "
            "where/maxrange quando il produttore la scrive; in questi SCAN "
            "OPERA FR l'attributo NON c'e' (where ha solo height/lat/lon), "
            "quindi si usa la geometria della griglia (rstart+nbins*rscale) "
            "e la fonte e' dichiarata in max_range_source.",
            "coverage_geometric e' limitata dal fascio <= 8 km: a 248 km la "
            "0.6 gradi e' a 6.2 km, la 1.01 a 8.0 km, la 1.4 a 9.7 km.",
            "coverage_observed e' l'inviluppo dei range massimi per azimuth "
            "con almeno un gate VRADH valido: un raggio senza eco non ha "
            "Doppler, quindi il poligono si restringe quando non piove.",
            "Il bordo del poligono e' l'impronta del fascio (mezzo raggio per "
            "lato), non la corda fra i centri dei raggi.",
        ],
        "counts": {"polygons": len(features),
                   "geometric": kinds.get("geometric", 0),
                   "observed": kinds.get("observed", 0),
                   "sites": len({f["properties"]["site"] for f in features})},
        "features": features,
    }
    return doc


def distance_to_ring_km(lon, lat, ring):
    """Distanza minima (km) dal punto all'anello (proiezione locale).

    Positiva per costruzione: il segno lo decide `coverage_status` con
    point_in_ring. Sufficiente alla scala del poligono (< 300 km): errore
    della proiezione equirettangolare < 0.1%.
    """
    if not ring or len(ring) < 2:
        return None
    lat0 = math.radians(float(lat))
    kx = 111.320 * math.cos(lat0)
    ky = 110.574
    best = None
    n = len(ring)
    for i in range(n - 1):
        x1 = (ring[i][0] - lon) * kx
        y1 = (ring[i][1] - lat) * ky
        x2 = (ring[i + 1][0] - lon) * kx
        y2 = (ring[i + 1][1] - lat) * ky
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0.0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / l2))
        d = math.hypot(x1 + t * dx, y1 + t * dy)
        if best is None or d < best:
            best = d
    return best


def coverage_contains(doc, lon, lat, kind=None):
    """Poligoni che contengono il punto (filtro opzionale per `kind`)."""
    hits = []
    for f in (doc or {}).get("features", []):
        p = f.get("properties") or {}
        if kind is not None and p.get("kind") != kind:
            continue
        ring = f["geometry"]["coordinates"][0]
        if point_in_ring(lon, lat, ring):
            d = distance_to_ring_km(lon, lat, ring)
            hits.append({"site": p.get("site"), "kind": p.get("kind"),
                         "layer": p.get("layer"),
                         "elevation_deg": p.get("elevation_deg"),
                         "max_range_km": p.get("max_range_km"),
                         "beam_height_at_max_km": p.get("beam_height_at_max_km"),
                         "distance_to_edge_km": (round(float(d), 3)
                                                 if d is not None else None)})
    return hits


def coverage_status(doc, lon, lat):
    """Stato del punto per ciascun `kind`: dentro/fuori + distanza dal bordo.

    `distance_to_edge_km` e' positiva dentro, negativa fuori.
    """
    feats = [f for f in (doc or {}).get("features", [])
             if (f.get("properties") or {}).get("kind")]
    out = {}
    for kind in ("geometric", "observed"):
        group = [f for f in feats if f["properties"]["kind"] == kind]
        if not group:
            out[kind] = {"present": False, "inside": None,
                         "distance_to_edge_km": None}
            continue
        inside, outside = [], []
        for f in group:
            ring = f["geometry"]["coordinates"][0]
            d = distance_to_ring_km(lon, lat, ring)
            (inside if point_in_ring(lon, lat, ring) else outside).append(
                (d, f))
        if inside:
            d, f = max(inside, key=lambda x: x[0] if x[0] is not None else -1)
            signed = d
            hit = True
        else:
            d, f = min(outside, key=lambda x: x[0] if x[0] is not None else 1e9)
            signed = -d if d is not None else None
            hit = False
        p = f["properties"]
        out[kind] = {
            "present": True,
            "inside": bool(hit),
            "site": p.get("site"),
            "layer": p.get("layer"),
            "elevation_deg": p.get("elevation_deg"),
            "max_range_km": p.get("max_range_km"),
            "beam_height_at_max_km": p.get("beam_height_at_max_km"),
            "distance_to_edge_km": (round(float(signed), 3)
                                    if signed is not None else None),
        }
    return out


# ---------------------------------------------------------------------------
# Accoppiamento con le strutture convettive
# ---------------------------------------------------------------------------
def _parse_iso(value):
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.astimezone(_dt.timezone.utc)
    except ValueError:
        return None


def match_structures(structures, candidates, cfg):
    """Per ogni struttura: candidato entro `match_radius_km` e
    `match_window_min`. Ritorna (n_valorizzate, n_nulle, motivi)."""
    radius = float(cfg["match_radius_km"])
    window_s = float(cfg["match_window_min"]) * 60.0
    filled = 0
    reasons = {}
    for st in structures or []:
        pos = st.get("position") or [None, None]
        lon, lat = (float(pos[0]) if pos[0] is not None else None,
                    float(pos[1]) if pos[1] is not None else None)
        t = _parse_iso(st.get("last_seen"))
        best = None
        best_d = None
        if lon is not None and lat is not None and t is not None:
            for c in candidates or []:
                tc = _parse_iso(c.get("valid_time"))
                if tc is None or abs((tc - t).total_seconds()) > window_s:
                    continue
                d = haversine_km(lat, lon, c["lat"], c["lon"])
                if d > radius:
                    continue
                if best is None or d < best_d:
                    best, best_d = c, d
        if best is None:
            st["rotation_shear"] = {
                "shear_ms_km": None,
                "null_reason": ("no_doppler_candidate_within_15km_5min"
                                if lon is not None and lat is not None
                                else "structure_position_missing"),
                "description": DESCRIPTION.format(
                    site="fraja/frale"),
            }
            reasons["no_doppler_candidate"] = reasons.get(
                "no_doppler_candidate", 0) + 1
        else:
            st["rotation_shear"] = {
                "shear_ms_km": best["shear_ms_km"],
                "shear_sign": best["shear_sign"],
                "azimuth_deg": best["azimuth_deg"],
                "range_km": best["range_km"],
                "beam_height_km": best["beam_height_km"],
                "elevation_deg": best["elevation_deg"],
                "radar": best["site"],
                "valid_time": best["valid_time"],
                "dealias_method": best["dealias_method"],
                "persistence_scans": best["persistence_scans"],
                "confidence": best["confidence"],
                "distance_km": round(float(best_d), 2),
                "description": DESCRIPTION.format(
                    site=f"{best['site']} ({SITE_LABEL.get(best['site'], '')})"),
            }
            filled += 1
    return filled, len(structures or []) - filled, reasons


def attach(bundle, config=None, out_dir=None):
    """Layer additivo: valorizza `rotation_shear` su ogni struttura.

    Non solleva mai: un errore diventa `status="error"` e un warning sul
    bundle (il run base resta valido). Scrive il GeoJSON di copertura in
    `out_dir/doppler_coverage.geojson` se `out_dir` e' dato.
    """
    cfg = config_for(config)
    structures = getattr(bundle, "convective_structures", None) or []
    result = {"schema_version": SCHEMA_VERSION, "status": "disabled"}
    if not cfg.get("enabled"):
        for st in structures:
            st["rotation_shear"] = {
                "shear_ms_km": None, "null_reason": "doppler_disabled",
                "description": DESCRIPTION.format(site="fraja/frale")}
        return result
    as_of = None
    aom = getattr(bundle, "as_of_mode", None)
    if isinstance(aom, dict) and aom.get("requested_ms"):
        # REPLAY: l'istante chiesto dall'utente (il radar anchor puo' essere
        # 5 minuti prima e taglierebbe fuori il volume francese giusto).
        as_of = _dt.datetime.fromtimestamp(
            float(aom["requested_ms"]) / 1000.0, _dt.timezone.utc)
    elif getattr(bundle, "radar_timestamp_ms", None):
        as_of = _dt.datetime.fromtimestamp(
            float(bundle.radar_timestamp_ms) / 1000.0, _dt.timezone.utc)
    try:
        result = collect(config, as_of=as_of)
    except Exception as exc:
        result = {"schema_version": SCHEMA_VERSION, "status": "error",
                  "null_reason": f"{exc.__class__.__name__}:{exc}",
                  "sites": [], "candidates": [], "coverage": None,
                  "errors": [{"collect": str(exc)}],
                  "cost": {"files": 0, "bytes": 0, "seconds": 0.0}}
    filled, nulls, reasons = match_structures(structures, result.get("candidates") or [], cfg)
    result["structures_total"] = len(structures)
    result["structures_with_rotation_shear"] = filled
    result["structures_null"] = nulls
    result["null_reasons"] = reasons
    cov = result.get("coverage")
    if cov:
        st74 = coverage_status(cov, 12.48, 41.89)
        result["coverage_rome"] = st74
        result["coverage_rome_geometric_inside"] = st74.get(
            "geometric", {}).get("inside")
        result["coverage_rome_observed_inside"] = st74.get(
            "observed", {}).get("inside")
        result["coverage_rome_inside"] = result["coverage_rome_geometric_inside"]
    if out_dir and cov:
        try:
            from . import doppler_coverage as dc
            path = os.path.join(out_dir, "doppler_coverage.geojson")
            dc.write_geojson(path, cov)
            result["coverage_path"] = path
        except Exception as exc:
            result["coverage_write_error"] = f"{exc.__class__.__name__}:{exc}"
    return result


# ---------------------------------------------------------------------------
# CLI (diagnostica: nessun output di prodotto)
# ---------------------------------------------------------------------------
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Shear azimutale Doppler OPERA FR")
    ap.add_argument("--as-of", default=None, help="ISO8601 UTC")
    ap.add_argument("--site", default=None, help="frale | fraja")
    ap.add_argument("--out", default=None, help="scrive il JSON del risultato")
    args = ap.parse_args(argv)
    cfg = config_for({})
    if args.site:
        cfg["sites"] = [args.site]
    as_of = None
    if args.as_of:
        as_of = _dt.datetime.fromisoformat(
            args.as_of.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    res = collect({"doppler": cfg}, as_of=as_of)
    text = json.dumps(res, ensure_ascii=False, indent=1, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        print(args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
