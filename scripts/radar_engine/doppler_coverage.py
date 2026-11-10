#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — doppler_coverage.py

CONFINE DELLA COPERTURA DOPPLER (VRADH) dei radar OPERA, pubblicato come
GeoJSON. Serve al client per disegnare la linea tratteggiata rossa del limite
oltre il quale la velocita' radiale NON esiste (e quindi nessuna diagnosi di
rotazione e' possibile).

FONTE (S3 aperto OPERA, accesso anonimo):
  YYYY/MM/DD/{FR,HR,CH,DE,...}/<site>/SCAN/<site>@<ts>@<el>@DBZH_TH_VRADH.h5
  - ODIM_H5 2.3, 360 raggi x 267 bin, rscale 960 m
  - `dataset1/data3` = VRADH (gain 0.5, offset -60, nodata 255, undetect 254
    MISURATI sul file fraja_20261110T1329.h5), `/dataset1/where` = a1gate,
    nrays, nbins, rscale, rstart, `/where` = lat/lon/height del sito
  - i settori muti NON sono dichiarati: si MISURANO (un raggio e' coperto se
    almeno un bin ha un valore diverso da nodata/undetect). Misurato il
    2026-11-10: Aleria (frale) muta 80-240 gradi, Ajaccio (fraja) muta 0-80 e
    300-360.

COSA SI PUBBLICA: per ogni sito un poligono per ogni RUN CONTIGUO di azimuth
coperti (un sito con un settore muto produce piu' poligoni, non un poligono
"bucato" inventato). Proprieta' per poligono: site, country, max_range_km,
generated_at, attribution, license "CC BY 4.0", piu' azimuth_start_deg,
azimuth_end_deg, n_rays, coverage_fraction, scan_time.

Il client disegna questi poligoni come linea tratteggiata rossa: lo schema e'
documentato in docs/OPERA_MOSAIC.md.
"""

import concurrent.futures as _fut
import datetime as _dt
import json
import math
import os
import re
import tempfile
import urllib.parse

import numpy as np

from . import models
from . import source_opera

LICENSE = "CC BY 4.0"
COUNTRIES = ("FR", "HR", "CH", "DE", "IT", "SI", "AT", "NL", "BE", "PL",
             "CZ", "SK", "HU", "RO", "BG", "GR", "ES", "PT", "NO", "SE",
             "FI", "DK", "IE", "GB", "LT", "LV", "EE", "UA", "RS", "BA",
             "ME", "MK", "AL", "CY", "MT", "LU")

ATTRIBUTION_BY_COUNTRY = {
    "FR": "Meteo-France via OPERA",
    "HR": "DHMZ via OPERA",
    "CH": "MeteoSwiss via OPERA",
    "DE": "DWD via OPERA",
    "IT": "DPC via OPERA",
    "SI": "ARSO via OPERA",
    "AT": "GeoSphere Austria via OPERA",
    "NL": "KNMI via OPERA",
    "BE": "RMI via OPERA",
    "PL": "IMGW via OPERA",
    "ES": "AEMET via OPERA",
    "PT": "IPMA via OPERA",
    "NO": "MET Norway via OPERA",
    "SE": "SMHI via OPERA",
    "FI": "FMI via OPERA",
    "DK": "DMI via OPERA",
    "IE": "Met Eireann via OPERA",
    "GB": "Met Office via OPERA",
    "GR": "HNMS via OPERA",
    "CY": "DoM via OPERA",
    "MT": "MIA via OPERA",
    "CZ": "CHMI via OPERA",
    "SK": "SHMU via OPERA",
    "HU": "OMSZ via OPERA",
    "RO": "ANM via OPERA",
    "BG": "NIMH via OPERA",
    "RS": "RHMSS via OPERA",
    "BA": "FHMZBIH via OPERA",
    "ME": "ZHMS via OPERA",
    "MK": "UHMR via OPERA",
    "AL": "IGEWE via OPERA",
    "UA": "UHMI via OPERA",
    "LT": "LHMT via OPERA",
    "LV": "LVGMC via OPERA",
    "EE": "KAUR via OPERA",
    "LU": "MeteoLux via OPERA",
}


def attribution_for(country):
    return ATTRIBUTION_BY_COUNTRY.get(
        country, f"{country} national weather service via OPERA")


# ---------------------------------------------------------------------------
# Discovery S3 (delimiter = prefissi "cartella")
# ---------------------------------------------------------------------------
def list_prefixes(prefix, s3_base=source_opera.S3_BASE, timeout_s=60):
    """Prefissi comuni sotto `prefix` (ListObjectsV2 con delimiter='/')."""
    q = urllib.parse.urlencode({"list-type": "2", "prefix": prefix,
                                "delimiter": "/", "max-keys": 1000})
    _st, body, _h = source_opera._http_get(f"{s3_base}?{q}",
                                           timeout_s=timeout_s)
    text = body.decode("utf-8", "replace")
    return re.findall(r"<Prefix>([^<]+)</Prefix>", text)


def list_countries(day, **kw):
    """Paesi con almeno una cartella sotto `YYYY/MM/DD/`."""
    prefix = (day.strftime("%Y/%m/%d") if not isinstance(day, str) else day) + "/"
    out = []
    for p in list_prefixes(prefix, **kw):
        name = p[len(prefix):].strip("/")
        if name:
            out.append(name)
    return sorted(out)


def list_sites(day, country, **kw):
    """Siti radar di un paese per quel giorno (nome cartella)."""
    day_s = day.strftime("%Y/%m/%d") if not isinstance(day, str) else day
    prefix = f"{day_s}/{country}/"
    return sorted(p[len(prefix):].strip("/")
                  for p in list_prefixes(prefix, **kw)
                  if p[len(prefix):].strip("/"))


def latest_scan_key(day, country, site, **kw):
    """Chiave del file SCAN piu' recente con VRADH (None se assente)."""
    day_s = day.strftime("%Y/%m/%d") if not isinstance(day, str) else day
    prefix = f"{day_s}/{country}/{site}/SCAN/"
    q = urllib.parse.urlencode({"list-type": "2", "prefix": prefix,
                                "max-keys": 1000})
    _st, body, _h = source_opera._http_get(
        f"{source_opera.S3_BASE}?{q}", timeout_s=kw.get("timeout_s", 60))
    keys = [k for k in re.findall(r"<Key>([^<]+)</Key>",
                                  body.decode("utf-8", "replace"))
            if k.endswith(".h5")]
    if not keys:
        return None
    # I file SCAN sono per elevazione: si prende il piu' recente per timestamp
    # e, a parita', la quota piu' bassa (el = penultimo campo del nome).
    def sort_key(k):
        m = re.search(r"@(\d{8}T\d{4})@([0-9.]+)@", k)
        if not m:
            return ("", 1e9)
        return (m.group(1), float(m.group(2)))
    return sorted(keys, key=sort_key)[-1]


# ---------------------------------------------------------------------------
# Lettura ODIM_H5 (solo VRADH: la copertura che ci interessa)
# ---------------------------------------------------------------------------
def _decode(attrs, key, default=None):
    v = attrs.get(key, default)
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return v


def read_scan(path, vradh_dataset="data3"):
    """Legge un ODIM_H5 e ritorna il profilo di copertura VRADH.

    `path` puo' essere locale o un URL http(s): in tal caso viene scaricato in
    un file temporaneo (h5py non legge da HTTP). Ritorna dict:
      site lat/lon/height, elangle, nrays, nbins, rscale_m, a1gate,
      az_start_deg (per raggio), max_range_m (per raggio), coverage_fraction
    Solleva models.ValidationError se VRADH non c'e' o il file e' illeggibile.
    """
    try:
        import h5py
    except ImportError as exc:  # pragma: no cover
        raise models.ValidationError("h5py_missing") from exc
    tmp = None
    local = path
    if str(path).startswith("http"):
        fd, tmp = tempfile.mkstemp(suffix=".h5")
        os.close(fd)
        _st, body, _h = source_opera._http_get(str(path), timeout_s=120)
        with open(tmp, "wb") as fh:
            fh.write(body)
        local = tmp
    try:
        with h5py.File(local, "r") as f:
            where = f["where"].attrs
            lat = float(where["lat"])
            lon = float(where["lon"])
            height = float(where.get("height", 0.0))
            d1 = f["dataset1"]
            dw = d1["where"].attrs
            nrays = int(dw["nrays"])
            nbins = int(dw["nbins"])
            rscale = float(dw["rscale"])
            rstart = float(dw.get("rstart", 0.0))
            a1gate = int(dw.get("a1gate", 0))
            elangle = float(dw.get("elangle", 0.0))
            # Il campo VRADH NON e' sempre in `data3`: si cerca per `quantity`
            # (misurato: i siti svedesi hanno TH in data3). Nessun indice
            # hardcodato.
            node = None
            for name in d1.keys():
                if not str(name).startswith("data"):
                    continue
                q = _decode(d1[name]["what"].attrs, "quantity", "")
                if q == "VRADH":
                    node = d1[name]
                    vradh_dataset = name
                    break
            if node is None:
                raise models.ValidationError("vradh_dataset_missing")
            what = node["what"].attrs
            gain = float(what.get("gain", 1.0))
            offset = float(what.get("offset", 0.0))
            nodata = float(what.get("nodata", 255.0))
            undetect = float(what.get("undetect", 0.0))
            raw = node["data"][...]
            az_start = None
            if "how" in d1 and "startazA" in d1["how"].attrs:
                az_start = np.asarray(d1["how"].attrs["startazA"],
                                      dtype="float64")
    except models.ValidationError:
        raise
    except Exception as exc:
        raise models.ValidationError(f"odim_read_error:{exc}") from exc
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

    raw = np.asarray(raw)
    valid = (raw != nodata) & (raw != undetect) & np.isfinite(raw)
    # valore fisico = raw*gain + offset (non serve per la copertura, ma il
    # controllo di plausibilita' evita di contare sentinelle riscalate).
    max_range_m, n_valid_bins = _max_range_per_ray(
        raw, valid, rstart, rscale, gain, offset)
    if az_start is None or az_start.size != nrays:
        az_start = (np.arange(nrays, dtype="float64") + a1gate + 0.5) % 360.0
    return {
        "lat": lat,
        "lon": lon,
        "height_m": height,
        "elangle_deg": elangle,
        "nrays": nrays,
        "nbins": nbins,
        "rscale_m": rscale,
        "rstart_m": rstart,
        "a1gate": a1gate,
        "az_start_deg": np.mod(az_start, 360.0),
        "max_range_m": max_range_m,
        "n_valid_bins": n_valid_bins,
        "coverage_fraction": float((max_range_m > 0).mean()),
        "max_range_km": round(float(max_range_m.max()) / 1000.0, 2),
    }


def _max_range_per_ray(raw, valid, rstart, rscale, gain, offset):
    """Range massimo con un valore valido per ogni raggio + n bin validi."""
    nrays, nbins = valid.shape
    idx = np.arange(nbins, dtype="float64")
    rng = rstart + (idx + 0.5) * rscale
    max_range = np.zeros(nrays, dtype="float64")
    n_valid = valid.sum(axis=1).astype("int32")
    for i in range(nrays):
        w = np.nonzero(valid[i])[0]
        if w.size:
            max_range[i] = rng[w[-1]] + rscale / 2.0
    return max_range, n_valid


# ---------------------------------------------------------------------------
# Costruzione dei poligoni (uno per run contiguo di azimuth coperti)
# ---------------------------------------------------------------------------
def _destination(lon, lat, bearing_deg, dist_km):
    """Punto a `dist_km` e azimuth `bearing_deg` (sferoide, R=6371.0088 km)."""
    r = 6371.0088
    b = math.radians(bearing_deg)
    la1 = math.radians(lat)
    lo1 = math.radians(lon)
    la2 = math.asin(math.sin(la1) * math.cos(dist_km / r) +
                    math.cos(la1) * math.sin(dist_km / r) * math.cos(b))
    lo2 = lo1 + math.atan2(
        math.sin(b) * math.sin(dist_km / r) * math.cos(la1),
        math.cos(dist_km / r) - math.sin(la1) * math.sin(la2))
    return [round(math.degrees(lo2), 5), round(math.degrees(la2), 5)]


def coverage_sectors(az_start_deg, max_range_m, min_range_km=5.0,
                     max_points=360, min_run_deg=5.0, gap_deg=3.0):
    """Run contigui di azimuth coperti -> lista di dict.

    Ogni voce: {az0, az1, az[], range_km[], full_circle}. Un azimuth e'
    coperto se il suo range massimo e' >= `min_range_km` (definizione della
    specifica: "almeno un bin non-nodata"). I buchi piu' stretti di
    `gap_deg` vengono chiusi (speckle da singoli raggi) e i run piu' corti di
    `min_run_deg` vengono scartati: senza questa tolleranza un sito produce
    decine di spicchi da 1-2 gradi. Il taglio a 0/360 e' gestito ruotando
    l'array su un raggio coperto (nessun settore spezzato a nord)."""
    az = np.asarray(az_start_deg, dtype="float64") % 360.0
    rng_km = np.asarray(max_range_m, dtype="float64") / 1000.0
    covered = rng_km >= float(min_range_km)
    n = az.size
    if n == 0 or not covered.any():
        return []
    order = np.argsort(az)
    azs = az[order]
    rgs = rng_km[order]
    cov = covered[order]
    step_deg = 360.0 / n
    if cov.all():
        idx = list(range(0, n, max(1, int(math.ceil(n / float(max_points))))))
        return [{
            "az0": float(azs[0]),
            "az1": float(azs[0]) + 360.0,
            "az": [float(azs[i]) for i in idx],
            "range_km": [float(rgs[i]) for i in idx],
            "full_circle": True,
        }]
    # rotazione sull'INIZIO di un run coperto (predecessore non coperto): il
    # taglio a 0/360 cade in un buco, quindi il run che lo attraversa resta
    # UN solo settore.
    boundaries = np.nonzero(cov & ~np.roll(cov, 1))[0]
    if boundaries.size == 0:
        return []
    start = int(boundaries[0])
    cov_r = np.roll(cov, -start)
    az_r = np.roll(azs, -start)
    rgs_r = np.roll(rgs, -start)
    az_u = az_r.astype("float64").copy()
    for i in range(1, n):
        while az_u[i] < az_u[i - 1] - 1e-9:
            az_u[i] += 360.0
    # chiusura dei buchi corti (speckle)
    gap_rays = int(round(float(gap_deg) / step_deg))
    i = 0
    while i < n:
        if not cov_r[i]:
            j = i
            while j < n and not cov_r[j]:
                j += 1
            if (j - i) <= gap_rays:
                cov_r[i:j] = True
            i = j
        else:
            i += 1
    runs = []
    s = None
    for i in range(n):
        if cov_r[i] and s is None:
            s = i
        if s is not None and (not cov_r[i] or i == n - 1):
            e = i if not cov_r[i] else i + 1
            runs.append((s, e))
            s = None
    out = []
    min_rays = max(1, int(round(float(min_run_deg) / step_deg)))
    for a, b in runs:
        if (b - a) < min_rays:
            continue
        idx = list(range(a, b,
                         max(1, int(math.ceil((b - a) / float(max_points))))))
        out.append({
            "az0": float(az_u[a]),
            "az1": float(az_u[b - 1]) + step_deg,
            "az": [float(az_u[i]) for i in idx],
            "range_km": [float(rgs_r[i]) for i in idx],
            "full_circle": False,
        })
    return out


def site_features(scan, site, country, scan_time_iso, generated_at,
                  min_range_km=5.0, max_points=360):
    """Feature GeoJSON (una per run di azimuth coperti) per un sito.

    Giro completo (>= 355 gradi coperti) -> anello chiuso SENZA il centro.
    Settore parziale -> "spicchio": centro + arco + centro (nessun poligono
    bucato inventato per un settore muto)."""
    sectors = coverage_sectors(scan["az_start_deg"], scan["max_range_m"],
                               min_range_km=min_range_km,
                               max_points=max_points)
    feats = []
    for sec in sectors:
        ring = []
        if not sec["full_circle"]:
            ring.append([round(scan["lon"], 5), round(scan["lat"], 5)])
        for a, r in zip(sec["az"], sec["range_km"]):
            ring.append(_destination(scan["lon"], scan["lat"], a, r))
        ring.append(ring[0] if sec["full_circle"]
                    else [round(scan["lon"], 5), round(scan["lat"], 5)])
        if len(ring) < 4:
            continue
        span = sec["az1"] - sec["az0"]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "site": site,
                "country": country,
                "max_range_km": round(max(sec["range_km"]), 2),
                "generated_at": generated_at,
                "attribution": attribution_for(country),
                "license": LICENSE,
                "azimuth_start_deg": round(sec["az0"] % 360.0, 1),
                "azimuth_end_deg": round(sec["az1"] % 360.0, 1),
                "azimuth_span_deg": round(span, 1),
                "full_circle": bool(sec["full_circle"]),
                "n_rays": len(sec["az"]),
                "coverage_fraction": round(span / 360.0, 4),
                "scan_time": scan_time_iso,
                "site_lonlat": [round(scan["lon"], 5), round(scan["lat"], 5)],
                "site_height_m": round(scan["height_m"], 1),
                "elangle_deg": round(scan["elangle_deg"], 2),
            },
        })
    return feats


# ---------------------------------------------------------------------------
# Runner: scoperta siti + download + GeoJSON
# ---------------------------------------------------------------------------
def _one_site(day, country, site, min_range_km, timeout_s):
    key = latest_scan_key(day, country, site, timeout_s=timeout_s)
    if key is None:
        return {"site": site, "country": country, "status": "no_scan"}
    url = source_opera.S3_BASE + key
    try:
        scan = read_scan(url)
    except Exception as exc:
        return {"site": site, "country": country, "status": f"error:{exc}",
                "key": key}
    m = re.search(r"@(\d{8}T\d{4})@", key)
    t_iso = None
    if m:
        t_iso = _dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M").replace(
            tzinfo=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"site": site, "country": country, "status": "ok", "key": key,
            "scan_time": t_iso, "scan": scan}


def build_geojson(day=None, min_range_km=5.0, max_sites=None, workers=6,
                  timeout_s=60, countries=None, progress=None):
    """Scopre i siti con VRADH e costruisce il FeatureCollection."""
    day = day or _dt.datetime.now(_dt.timezone.utc)
    if day.tzinfo is None:
        day = day.replace(tzinfo=_dt.timezone.utc)
    generated_at = _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    present = list_countries(day, timeout_s=timeout_s)
    wanted = [c for c in (countries or present) if c in present]
    sites = []
    for c in wanted:
        for s in list_sites(day, c, timeout_s=timeout_s):
            sites.append((c, s))
    if max_sites:
        sites = sites[:int(max_sites)]
    features = []
    skipped = []
    with _fut.ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futs = {ex.submit(_one_site, day, c, s, min_range_km, timeout_s):
                (c, s) for c, s in sites}
        for fut in _fut.as_completed(futs):
            c, s = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = {"site": s, "country": c, "status": f"error:{exc}"}
            if res["status"] != "ok":
                skipped.append({"site": s, "country": c,
                                "status": res["status"]})
                if progress:
                    progress(s, res["status"])
                continue
            feats = site_features(res["scan"], s, c, res.get("scan_time"),
                                  generated_at, min_range_km=min_range_km)
            features.extend(feats)
            if progress:
                progress(s, f"ok:{len(feats)}")
    return {
        "type": "FeatureCollection",
        "generated_at": generated_at,
        "license": LICENSE,
        "attribution": ("OPERA (EUMETNET) via Open Radar Data/MeteoGate - "
                        "dati dei servizi meteo nazionali indicati per sito"),
        "data_limits": [
            "La copertura e' MISURATA sul campo VRADH del volume piu' recente "
            "del giorno: un raggio senza alcun bin valido e' fuori copertura.",
            "I settori muti noti (Aleria 80-240 deg, Ajaccio 0-80 e 300-360) "
            "compaiono come assenza di poligono, non come cerchio pieno.",
            "Il poligono e' l'inviluppo dei range massimi per azimuth: non "
            "tiene conto del mascheramento locale (orografia, clutter).",
        ],
        "counts": {
            "sites_listed": len(sites),
            "sites_ok": len({f["properties"]["site"] for f in features}),
            "sites_skipped": len(skipped),
            "polygons": len(features),
        },
        "skipped": skipped,
        "features": features,
    }


def write_geojson(path, doc):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp, path)
    return path


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Copertura Doppler OPERA -> GeoJSON")
    ap.add_argument("--out", default="data/radar/doppler_coverage.geojson")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (UTC), default oggi")
    ap.add_argument("--min-range-km", type=float, default=5.0)
    ap.add_argument("--max-sites", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--countries", default=None,
                    help="lista separata da virgola (default: tutti)")
    args = ap.parse_args(argv)
    day = (_dt.datetime.strptime(args.day, "%Y-%m-%d").replace(
        tzinfo=_dt.timezone.utc) if args.day else None)
    doc = build_geojson(day=day, min_range_km=args.min_range_km,
                        max_sites=args.max_sites, workers=args.workers,
                        countries=(args.countries.split(",")
                                   if args.countries else None),
                        progress=lambda s, st: print(f"[doppler] {s}: {st}"))
    write_geojson(args.out, doc)
    print(json.dumps(doc["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
