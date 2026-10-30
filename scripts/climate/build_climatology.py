#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Climatologia di riferimento 1991-2020 per le mappe di anomalia.

Produce `data/climate/normals_1991_2020.json`: medie mensili (12 valori per
cella) di temperatura a 2 m, geopotenziale a 500 hPa e pressione al livello
del mare, su una griglia di 5 gradi che copre 20..90 N / -70..45 E
(15 x 24 = 360 celle).

PERCHE' QUESTA FONTE (MISURATO il 2026-10-30)
--------------------------------------------
La specifica chiedeva la Archive API di Open-Meteo (ERA5) con
`daily=temperature_2m_mean,geopotential_height_500hPa_mean,pressure_msl_mean`.
Il piano e' stato eseguito e ha prodotto due muri misurati:

1. I LIVELLI DI PRESSIONE NON ESISTONO. `geopotential_height_500hPa_mean` viene
   rifiutata ("Cannot initialize ForecastVariableDaily from invalid String
   value") e la variabile oraria `hourly=geopotential_height_500hPa` restituisce
   **0 valori non-null** su tutte le date provate (1991-06-01, 2020-06-01,
   2024-06-01, con e senza `&models=era5`). Idem `temperature_850hPa` e
   `temperature_500hPa`. L'Archive API non serve i livelli di pressione.

2. LA QUOTA ORARIA NON BASTA PER 360 CELLE. Con 30 anni di dati per cella
   l'API pesa le richieste sul volume: dopo **40 celle su 360** (4 richieste
   da 10 celle, 876.640 valori) risponde 429 "Hourly API request limit
   exceeded". Il blocco dura un'ora: la griglia completa richiederebbe ~9 ore.

Quindi il file e' costruito da UNA sola rianalisi, NCEP/NCAR Reanalysis 1
(medie mensili, NOAA PSL), identica per tutti e tre i campi: nessuna
discontinuita' fra i campi, nessuna dipendenza da una quota. Il ramo ERA5
resta nel codice (`--source era5`): e' quello che ha prodotto 40 celle, e
quando la quota lo consente si puo' rigenerare il file con quella fonte.

  * NCEP/NCAR R1: `hgt.mon.mean.nc` (500 hPa), `air.mon.mean.nc` (0.995
    sigma, 2 m), `pres.mon.mean.nc` (MSL). Subset via NCSS in NetCDF-3
    classico, parsato qui con la sola stdlib. Nodi 2.5 gradi: i punti della
    griglia 5 gradi cadono ESATTAMENTE sui nodi (5 e' multiplo di 2.5),
    nessuna interpolazione.

SCHEMA DEL FILE
---------------
    grid: lat0=20, lat1=90, lon0=-70, lon1=45, step=5, n_lat=15, n_lon=24
    indice cella: i = i_lat*n_lon + i_lon  (lat crescente, lon crescente)
    fields.<nome>.values: lista di 360 righe, ognuna con 12 valori
                          (gennaio .. dicembre); `null` dove il dato manca.

USO
---
    python scripts/climate/build_climatology.py            # build (usa la cache)
    python scripts/climate/build_climatology.py --refresh   # riscarica tutto
    python scripts/climate/build_climatology.py --limit 6   # prova su 6 celle

La cache vive in `data/climate/_cache/` (non va committata): una richiesta per
cella, cosi' un'interruzione non ricomincia da zero. `--limit` scrive di default
in `data/climate/_cache/_sample_normals.json` per non sovrascrivere il file
buono.

Solo stdlib: nessuna dipendenza esterna.
"""

import argparse
import datetime as dt
import gzip
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request

SCHEMA = "meteorisk.climate.normals/1"
PERIOD = "1991-2020"
START_DATE = "1991-01-01"
END_DATE = "2020-12-31"

GRID = {"lat0": 20.0, "lat1": 90.0, "lon0": -70.0, "lon1": 45.0, "step": 5.0}
N_LAT = 15
N_LON = 24
N_CELLS = N_LAT * N_LON

FIELDS = ("temperature_2m", "geopotential_height_500hPa", "pressure_msl")
UNITS = {
    "temperature_2m": "degC",
    "geopotential_height_500hPa": "m",
    "pressure_msl": "hPa",
}
DECIMALS = {
    "temperature_2m": 1,
    "geopotential_height_500hPa": 1,
    "pressure_msl": 1,
}

ERA5_URL = "https://archive-api.open-meteo.com/v1/archive"
ERA5_BATCH = 10  # celle per richiesta: l'API pesa le richieste sul volume e ha
                 # un limite orario (misurato: 429 dopo 40 celle su 360).
NCEP_BASE = "https://psl.noaa.gov/thredds/ncss/grid/Datasets/ncep.reanalysis.derived/"
NCEP_FIELDS = {
    "temperature_2m": {
        "file": "surface/air.mon.mean.nc",
        "var": "air",
        "query": "&vertCoord=0.995",
        "units": "degC",
        "scale": 1.0,
        "offset": 0.0,
    },
    "pressure_msl": {
        "file": "surface/slp.mon.mean.nc",
        "var": "slp",
        "query": "",
        "units": "millibars",   # = hPa, nessuna conversione
        "scale": 1.0,
        "offset": 0.0,
    },
    "geopotential_height_500hPa": {
        "file": "pressure/hgt.mon.mean.nc",
        "var": "hgt",
        "query": "&vertCoord=500",
        "units": "m",
        "scale": 1.0,
        "offset": 0.0,
    },
}
NCEP_PERIOD_QUERY = ("?var=%s&time_start=1991-01-01T00:00:00Z"
                     "&time_end=2020-12-31T00:00:00Z%s"
                     "&north=90&west=-70&east=45&south=20&horizStride=2"
                     "&accept=netcdf")

USER_AGENT = "MeteoRisk-Light/1.0 (climatology builder; +https://meteoriskitalia.netizen.github.io)"

LICENCE = (
    "NCEP/NCAR Reanalysis 1: dati NOAA PSL (Boulder, Colorado, USA), "
    "pubblico dominio, citazione richiesta: Kalnay et al. 1996, "
    "'The NCEP/NCAR 40-Year Reanalysis Project', BAMS 77, 437-471."
)
ATTRIBUTION = (
    "NCEP/NCAR Reanalysis 1 fornita da NOAA PSL, Boulder, Colorado, USA, "
    "https://psl.noaa.gov (medie mensili 1991-2020, hgt a 500 hPa, air a "
    "0.995 sigma, pres al livello del mare). Il ramo Open-Meteo/ERA5 previsto "
    "dalla specifica e' stato provato e scartato per i motivi misurati "
    "riportati in sources.open_meteo_era5."
)


def log(msg):
    sys.stderr.write("[clim] %s\n" % msg)
    sys.stderr.flush()


class Pacer(object):
    """Distanzia le richieste e si allarga quando il server risponde 429."""

    def __init__(self, delay=2.0, floor=1.0, ceiling=15.0):
        self.delay = delay
        self.floor = floor
        self.ceiling = ceiling

    def on_throttle(self):
        self.delay = min(self.ceiling, self.delay * 1.6)

    def on_ok(self):
        self.delay = max(self.floor, self.delay * 0.95)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def http_get(url, tries=8, timeout=300, pacing=None):
    """GET con attesa dichiarata su 429/5xx. Non brucia la quota con retry."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                body = fh.read()
            if pacing is not None:
                pacing.on_ok()
            return body
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                if pacing is not None:
                    pacing.on_throttle()
                wait = 0
                try:
                    wait = int(exc.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    wait = 0
                wait = wait if wait > 0 else min(180, 20 * (attempt + 1))
                log("HTTP 429 da %s (%s): attendo %d s (tentativo %d/%d)"
                    % (_host(url), _reason(exc), wait, attempt + 1, tries))
                time.sleep(wait)
                continue
            if 500 <= exc.code < 600:
                wait = min(300, 15 * (attempt + 1))
                log("HTTP %d da %s: attendo %d s" % (exc.code, _host(url), wait))
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            wait = min(120, 10 * (attempt + 1))
            log("errore di rete su %s (%s): attendo %d s"
                % (_host(url), exc, wait))
            time.sleep(wait)
    raise RuntimeError("GET fallita dopo %d tentativi: %s (%s)"
                       % (tries, url, last))


def _reason(exc):
    try:
        return exc.read().decode("utf-8", "replace")[:160]
    except Exception:
        return ""


def _host(url):
    return url.split("/")[2]


# --------------------------------------------------------------------------
# Griglia
# --------------------------------------------------------------------------
def grid_cells():
    cells = []
    for i_lat in range(N_LAT):
        lat = GRID["lat0"] + i_lat * GRID["step"]
        for i_lon in range(N_LON):
            lon = GRID["lon0"] + i_lon * GRID["step"]
            cells.append((round(lat, 4), round(lon, 4)))
    return cells


# --------------------------------------------------------------------------
# ERA5 via Open-Meteo Archive
# --------------------------------------------------------------------------
def era5_url(coords):
    """URL per una o piu' celle (l'API accetta liste di coordinate)."""
    lat = ",".join("%g" % c[0] for c in coords)
    lon = ",".join("%g" % c[1] for c in coords)
    return ("%s?latitude=%s&longitude=%s&start_date=%s&end_date=%s&timezone=UTC"
            "&daily=temperature_2m_mean,pressure_msl_mean"
            % (ERA5_URL, lat, lon, START_DATE, END_DATE))


def cache_path(cache_dir, lat, lon):
    return os.path.join(cache_dir, "era5_%g_%g.json" % (lat, lon))


def era5_fetch(coords, pacer=None):
    """Scarica una lista di celle in UNA richiesta e valida l'aggancio."""
    raw = http_get(era5_url(coords), pacing=pacer)
    payload = json.loads(raw.decode("utf-8"))
    if isinstance(payload, dict):
        payload = [payload]
    if len(payload) != len(coords):
        raise RuntimeError("%d coordinate richieste, %d risposte"
                           % (len(coords), len(payload)))
    out = []
    for (lat, lon), item in zip(coords, payload):
        got_lat = item.get("latitude")
        got_lon = item.get("longitude")
        if got_lat is None or got_lon is None:
            raise RuntimeError("risposta senza coordinate per %g,%g" % (lat, lon))
        if abs(got_lat - lat) > 0.2:
            raise RuntimeError("latitudine agganciata %r troppo lontana da %r"
                               % (got_lat, lat))
        if abs((got_lon - lon + 180.0) % 360.0 - 180.0) > 0.2:
            raise RuntimeError("longitudine agganciata %r troppo lontana da %r"
                               % (got_lon, lon))
        daily = item.get("daily") or {}
        days = daily.get("time") or []
        t2m = daily.get("temperature_2m_mean")
        msl = daily.get("pressure_msl_mean")
        if not days or t2m is None or msl is None:
            raise RuntimeError("risposta ERA5 incompleta per %g,%g" % (lat, lon))
        out.append({
            "lat_request": lat,
            "lon_request": lon,
            "lat_grid": got_lat,
            "lon_grid": got_lon,
            "elevation_m": item.get("elevation"),
            "source": "open-meteo archive (era5)",
            "fetched_utc": dt.datetime.now(dt.timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "n_days": len(days),
            "time": days,
            "temperature_2m": t2m,
            "pressure_msl": msl,
        })
    return out


def _store_cells(cells, cache_dir):
    for cell in cells:
        path = cache_path(cache_dir, cell["lat_request"], cell["lon_request"])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cell, fh, separators=(",", ":"))
        os.replace(tmp, path)


# --------------------------------------------------------------------------
# Medie mensili
# --------------------------------------------------------------------------
def monthly_climatology(times, values):
    """Medie mensili 1991-2020.

    Passo 1: media di tutti i giorni validi di ogni (anno, mese).
    Passo 2: media dei 30 valori mensili per ciascun mese di calendario
             (ogni anno pesa uguale: nessun vantaggio per i mesi da 31 giorni).
    Ritorna (12 valori o None, n_missing).
    """
    sums = {}
    counts = {}
    missing = 0
    for t, v in zip(times, values):
        if v is None:
            missing += 1
            continue
        key = (t[:4], t[5:7])
        sums[key] = sums.get(key, 0.0) + float(v)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None, missing
    per_month = {}
    for (year, month), total in sums.items():
        per_month.setdefault(month, []).append(total / counts[(year, month)])
    out = []
    for m in range(1, 13):
        vals = per_month.get("%02d" % m) or []
        out.append(sum(vals) / len(vals) if vals else None)
    return out, missing


# --------------------------------------------------------------------------
# NetCDF-3 classico (minimo, solo per il subset NCSS del NCEP)
# --------------------------------------------------------------------------
NC_TYPES = {1: ("b", 1), 2: ("c", 1), 3: ("h", 2), 4: ("i", 4), 5: ("f", 4),
            6: ("d", 8)}


class NetCDF3(object):
    """Lettore NetCDF-3 classic / 64-bit-offset, sola stdlib."""

    def __init__(self, path_or_bytes):
        if isinstance(path_or_bytes, (bytes, bytearray)):
            self.buf = bytes(path_or_bytes)
        else:
            with open(path_or_bytes, "rb") as fh:
                self.buf = fh.read()
        buf = self.buf
        if buf[:3] != b"CDF":
            raise ValueError("non e' un NetCDF-3 (magic %r)" % buf[:4])
        self.version = buf[3]
        pos = 4
        if self.version == 1:
            self.numrecs = struct.unpack_from(">I", buf, pos)[0]
            pos += 4
        elif self.version == 2:
            self.numrecs = struct.unpack_from(">Q", buf, pos)[0]
            pos += 8
        else:
            raise ValueError("versione NetCDF-3 non gestita: %d" % self.version)
        self.dims, pos = self._dims(pos)
        self.gatts, pos = self._atts(pos)
        self.vars, pos = self._vars(pos)

    def _name(self, pos):
        n = struct.unpack_from(">I", self.buf, pos)[0]
        pos += 4
        name = self.buf[pos:pos + n].decode("latin-1")
        return name, pos + n + (-n) % 4

    def _dims(self, pos):
        tag, n = struct.unpack_from(">II", self.buf, pos)
        pos += 8
        dims = []
        if tag == 0:
            return dims, pos
        if tag != 0x0A:
            raise ValueError("dim_list tag inatteso %#x" % tag)
        for _ in range(n):
            name, pos = self._name(pos)
            size = struct.unpack_from(">I", self.buf, pos)[0]
            pos += 4
            dims.append((name, size))
        return dims, pos

    def _atts(self, pos):
        tag, n = struct.unpack_from(">II", self.buf, pos)
        pos += 8
        atts = {}
        if tag == 0:
            return atts, pos
        if tag != 0x0C:
            raise ValueError("att_list tag inatteso %#x" % tag)
        for _ in range(n):
            name, pos = self._name(pos)
            typ, cnt = struct.unpack_from(">II", self.buf, pos)
            pos += 8
            fmt, size = NC_TYPES[typ]
            if typ == 2:
                atts[name] = self.buf[pos:pos + cnt].decode("latin-1")
            else:
                vals = list(struct.unpack_from(">" + fmt * cnt, self.buf, pos))
                atts[name] = vals[0] if cnt == 1 else vals
            pos += cnt * size + (-cnt * size) % 4
        return atts, pos

    def _vars(self, pos):
        tag, n = struct.unpack_from(">II", self.buf, pos)
        pos += 8
        out = {}
        if tag == 0:
            return out, pos
        if tag != 0x0B:
            raise ValueError("var_list tag inatteso %#x" % tag)
        order = []
        for idx in range(n):
            name, pos = self._name(pos)
            ndim = struct.unpack_from(">I", self.buf, pos)[0]
            pos += 4
            dimids = list(struct.unpack_from(">" + "I" * ndim, self.buf, pos))
            pos += 4 * ndim
            atts, pos = self._atts(pos)
            # nel formato classic l'ULTIMA variabile omette vsize e begin:
            # i suoi dati iniziano dove finisce la variabile precedente
            if idx == n - 1 and self.version == 1:
                typ = struct.unpack_from(">I", self.buf, pos)[0]
                pos += 4
                vsize, begin = None, None
            else:
                typ, vsize = struct.unpack_from(">II", self.buf, pos)
                pos += 8
                begin = struct.unpack_from(">I", self.buf, pos)[0]
                pos += 4
            out[name] = dict(name=name, dimids=dimids, atts=atts, type=typ,
                             vsize=vsize, begin=begin)
            order.append(name)
        for name in order:
            var = out[name]
            if var["begin"] is None:
                prev_end = 0
                for other in order:
                    v = out[other]
                    if v is var or v["begin"] is None:
                        continue
                    prev_end = max(prev_end, v["begin"] + v["vsize"]
                                   + (-v["vsize"]) % 4)
                var["begin"] = prev_end
                var["vsize"] = len(self.buf) - prev_end
        return out, pos

    def shape(self, name):
        return tuple(self.dims[d][1] for d in self.vars[name]["dimids"])

    def dim_names(self, name):
        return [self.dims[d][0] for d in self.vars[name]["dimids"]]

    def _record_size(self):
        total = 0
        for var in self.vars.values():
            if self.dims[var["dimids"][0]][1] == 0:
                total += var["vsize"] + (-var["vsize"]) % 4
        return total

    def values(self, name, index):
        """Valori (lista piatta) per una selezione per nome di dimensione."""
        var = self.vars[name]
        dims = [self.dims[d] for d in var["dimids"]]
        fmt, size = NC_TYPES[var["type"]]
        picks = []
        for dim_name, dim_size in dims:
            sel = index.get(dim_name, slice(None))
            if isinstance(sel, int):
                picks.append([sel % dim_size])
            else:
                picks.append(list(range(dim_size))[sel])
        strides = [1] * len(dims)
        for i in range(len(dims) - 2, -1, -1):
            strides[i] = strides[i + 1] * dims[i + 1][1]
        record = dims[0][1] == 0
        recsize = self._record_size() if record else 0
        unpack = struct.Struct(">" + fmt).unpack_from
        scale = var["atts"].get("scale_factor")
        offset = var["atts"].get("add_offset")
        fill = var["atts"].get("missing_value", var["atts"].get("_FillValue"))
        out = []
        for combo in _product(picks):
            if record:
                off = var["begin"] + combo[0] * recsize + sum(
                    combo[k] * strides[k] for k in range(1, len(dims))) * size
            else:
                off = var["begin"] + sum(
                    combo[k] * strides[k] for k in range(len(dims))) * size
            val = unpack(self.buf, off)[0]
            if fill is not None and val == fill:
                out.append(None)
                continue
            if scale is not None or offset is not None:
                val = val * (scale if scale is not None else 1.0) \
                    + (offset if offset is not None else 0.0)
            out.append(val)
        return out


def _product(lists):
    if not lists:
        yield ()
        return
    head, rest = lists[0], lists[1:]
    for item in head:
        for tail in _product(rest):
            yield (item,) + tail


def ncep_monthly(field, cache_dir, refresh=False, pacing=None):
    """Media mensile 1991-2020 di un campo NCEP/NCAR R1 su 15x24 punti."""
    spec = NCEP_FIELDS[field]
    path = os.path.join(cache_dir,
                        "_ncep_%s_%s.nc" % (field, spec["var"]))
    downloaded = False
    if not os.path.exists(path) or refresh:
        downloaded = True
        url = NCEP_BASE + spec["file"] + NCEP_PERIOD_QUERY % (spec["var"],
                                                              spec["query"])
        log("scarico %s (NCEP/NCAR R1, %s 1991-2020, griglia 5 gradi)"
            % (field, spec["file"]))
        raw = http_get(url, pacing=pacing)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, path)
        log("  %d byte" % len(raw))

    nc = NetCDF3(path)
    lats = nc.values("lat", {})
    lons = nc.values("lon", {})
    hours = nc.values("time", {})
    if len(lats) != N_LAT or len(lons) != N_LON:
        raise RuntimeError("%s: subset NCEP inatteso, %d lat x %d lon"
                           % (field, len(lats), len(lons)))
    expect_lats = [GRID["lat1"] - i * GRID["step"] for i in range(N_LAT)]
    for got, want in zip(lats, expect_lats):
        if abs(got - want) > 1e-3:
            raise RuntimeError("latitudine NCEP %r diversa da %r" % (got, want))
    for j, lon in enumerate(lons):
        want = (GRID["lon0"] + j * GRID["step"]) % 360.0
        if abs((lon - want + 180) % 360 - 180) > 1e-3:
            raise RuntimeError("longitudine NCEP %r diversa da %r" % (lon, want))

    units = nc.vars["time"]["atts"].get("units", "")
    match = re.match(r"hours since\s+(\d{4})-(\d{1,2})-(\d{1,2})[ T]"
                     r"(\d{1,2}):(\d{2}):(\d{1,2})", units)
    if not match:
        raise RuntimeError("unita' del tempo NCEP inattese: %r" % units)
    epoch = dt.datetime(*(int(g) for g in match.groups()))
    stamps = [epoch + dt.timedelta(hours=h) for h in hours]
    wanted = [i for i, s in enumerate(stamps)
              if dt.datetime(1991, 1, 1) <= s < dt.datetime(2021, 1, 1)]
    if len(wanted) != 360:
        raise RuntimeError("%s: attesi 360 mesi NCEP, trovati %d"
                           % (field, len(wanted)))

    var_units = nc.vars[spec["var"]]["atts"].get("units")
    if var_units != spec["units"]:
        raise RuntimeError("%s: unita' NCEP %r inattese (attese %r)"
                           % (field, var_units, spec["units"]))

    # [time][level?][lat][lon] -> 15 x 24 serie, per i 360 mesi scelti
    grid = [[[] for _ in range(N_LON)] for _ in range(N_LAT)]
    for step, t_index in enumerate(wanted):
        flat = nc.values(spec["var"], {"time": t_index})
        if len(flat) != N_LAT * N_LON:
            raise RuntimeError("%s mese %d: %d valori"
                               % (field, t_index, len(flat)))
        for i_lat in range(N_LAT):
            for i_lon in range(N_LON):
                value = flat[i_lat * N_LON + i_lon]
                if value is not None:
                    value = value * spec["scale"] + spec["offset"]
                grid[i_lat][i_lon].append(value)
        if (step + 1) % 120 == 0:
            log("  %s: %d/360 mesi letti" % (field, step + 1))

    out = {}
    for i_lat in range(N_LAT):
        for i_lon in range(N_LON):
            series = grid[i_lat][i_lon]
            by_month = {}
            for value, t_index in zip(series, wanted):
                if value is None:
                    continue
                by_month.setdefault(stamps[t_index].month, []).append(value)
            # NCEP arriva con le latitudini DECRESCENTI (la riga 0 e' 90N),
            # la griglia di output e' crescente (la cella 0 e' 20N): si inverte.
            out[(N_LAT - 1 - i_lat, i_lon)] = [
                sum(by_month[m]) / len(by_month[m]) if by_month.get(m) else None
                for m in range(1, 13)
            ]
    return out, {"months": len(wanted), "lat": lats[0], "lon": lons[0],
                 "bytes": os.path.getsize(path), "downloaded": downloaded}


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def build(root, refresh=False, limit=None, sleep_s=2.0, out_path=None,
          source="ncep"):
    cache_dir = os.path.join(root, "data", "climate", "_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cells = grid_cells()
    n_cells = len(cells) if limit is None else min(limit, len(cells))

    values = {f: [None] * N_CELLS for f in FIELDS}
    daily_missing = {f: 0 for f in FIELDS}
    missing = {f: 0 for f in FIELDS}
    n_requests = 0
    started = time.time()
    pacer = Pacer(delay=sleep_s)

    if source == "ncep":
        for field in FIELDS:
            data, meta = ncep_monthly(field, cache_dir, refresh=refresh,
                                      pacing=pacer)
            if meta["downloaded"]:
                n_requests += 1
            for idx in range(n_cells):
                i_lat, i_lon = divmod(idx, N_LON)
                values[field][idx] = data[(i_lat, i_lon)]
            log("  %s: %d mesi, %d byte di subset"
                % (field, meta["months"], meta["bytes"]))
            time.sleep(pacer.delay)
    else:
        log("ERA5: %d celle (Open-Meteo Archive), %d per richiesta"
            % (n_cells, ERA5_BATCH))
        todo = []
        for idx in range(n_cells):
            lat, lon = cells[idx]
            if refresh or not os.path.exists(cache_path(cache_dir, lat, lon)):
                todo.append(idx)
        for start in range(0, len(todo), ERA5_BATCH):
            chunk = todo[start:start + ERA5_BATCH]
            coords = [cells[i] for i in chunk]
            _store_cells(era5_fetch(coords, pacer=pacer), cache_dir)
            n_requests += 1
            done = min(start + ERA5_BATCH, len(todo))
            elapsed = time.time() - started
            eta = elapsed / done * (len(todo) - done) if done else 0.0
            log("  %d/%d celle scaricate (%d richieste, %.0f s, ETA %.0f s, "
                "pausa %.1f s)"
                % (done, len(todo), n_requests, elapsed, eta, pacer.delay))
            if done < len(todo):
                time.sleep(pacer.delay)

        for idx in range(n_cells):
            lat, lon = cells[idx]
            with open(cache_path(cache_dir, lat, lon), "r",
                      encoding="utf-8") as fh:
                data = json.load(fh)
            t2m, miss_t = monthly_climatology(data["time"],
                                              data["temperature_2m"])
            msl, miss_p = monthly_climatology(data["time"], data["pressure_msl"])
            values["temperature_2m"][idx] = t2m
            values["pressure_msl"][idx] = msl
            daily_missing["temperature_2m"] += miss_t
            daily_missing["pressure_msl"] += miss_p

        log("Z500 dallo stesso ramo non e' disponibile: NCEP/NCAR R1")
        data, meta = ncep_monthly("geopotential_height_500hPa", cache_dir,
                                  refresh=refresh, pacing=pacer)
        if meta["downloaded"]:
            n_requests += 1
        for idx in range(n_cells):
            i_lat, i_lon = divmod(idx, N_LON)
            values["geopotential_height_500hPa"][idx] = data[(i_lat, i_lon)]

    rounded = {}
    for field in FIELDS:
        dec = DECIMALS[field]
        rows = []
        for row in values[field]:
            if row is None:
                rows.append([None] * 12)
            else:
                rows.append([None if v is None else round(v, dec) for v in row])
        rounded[field] = rows
        missing[field] = sum(1 for r in rows for v in r if v is None)

    by_cell = []
    for idx in range(N_CELLS):
        by_cell.append(abs_missing(rounded, idx))

    if source == "ncep":
        caveats = [
            "Fonte unica NCEP/NCAR Reanalysis 1 (medie mensili, griglia 2.5 "
            "gradi): i suoi nodi coincidono con quelli della griglia 5 gradi, "
            "quindi non c'e' interpolazione, ma la risoluzione effettiva e' "
            "2.5 gradi.",
            "Orografia: il nodo NCEP a 45N/10E ha pressione di superficie media "
            "924 hPa (~770 m); ERA5 nello stesso punto e' a 40 m. La "
            "temperatura NCEP li' vale 1.9 C a gennaio e 19.3 C a luglio, "
            "contro ~3 C e ~23 C a bassa quota: su terreno complesso (Alpi, "
            "Caucaso, Scandinavia) le anomalie di temperatura ereditano questo "
            "scarto e vanno lette con cautela.",
            "ERA5 via Open-Meteo non e' utilizzabile per questa griglia: l'API "
            "non serve i livelli di pressione (0 valori non-null) e la quota "
            "oraria si esaurisce dopo 40 celle su 360. Misurato il 2026-10-30.",
            "Differenza NCEP-ERA5 misurata sulle 40 celle ERA5 gia' scaricate "
            "(20N-25N): temperature_2m da -2.0 a +0.6 C, pressure_msl entro "
            "0.3 hPa.",
            "Z500: NCEP 2022-2025 meno GFS 2022-2025 sulla stessa finestra = "
            "+2.6 m a 45N/10E e +3.1 m a 40N/60W.",
        ]
        source_mode = "ncep"
    else:
        caveats = [
            "temperature_2m e pressure_msl: ERA5 via Open-Meteo (0.25 gradi). "
            "geopotential_height_500hPa: NCEP/NCAR Reanalysis 1 (la Archive API "
            "non serve i livelli di pressione). Le due fonti non sono "
            "omogenee: la differenza non e' corretta.",
            "Rigenerare con --source era5 richiede una quota Open-Meteo "
            "sufficiente: misurato, il limite orario si esaurisce dopo 40 celle "
            "su 360 con 30 anni di dati per cella.",
        ]
        source_mode = "era5+ncep"

    doc = {
        "schema": SCHEMA,
        "period": PERIOD,
        "monthly": True,
        "months": 12,
        "generated_at": dt.datetime.now(dt.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grid": dict(GRID, n_lat=N_LAT, n_lon=N_LON, n_cells=N_CELLS,
                     index="i = i_lat*n_lon + i_lon; lat = lat0 + i_lat*step; "
                           "lon = lon0 + i_lon*step; values[j] = mese j+1"),
        "fields": {
            field: {
                "units": UNITS[field],
                "decimals": DECIMALS[field],
                "n_missing": missing[field],
                "values": rounded[field],
            }
            for field in FIELDS
        },
        "n_missing": missing,
        "n_missing_daily_samples": daily_missing,
        "n_missing_by_cell": by_cell,
        "cells_with_missing": [i for i, n in enumerate(by_cell) if n],
        "method": (
            "Medie mensili: per ogni (anno, mese) la media dei valori validi, "
            "poi la media dei 30 valori mensili dello stesso mese di calendario "
            "(ogni anno pesa uguale). Unica fonte per tutti e tre i campi: "
            "NCEP/NCAR Reanalysis 1, medie mensili, campionate esattamente sui "
            "nodi della griglia 5 gradi (i nodi NCEP da 2.5 gradi coincidono con "
            "quelli da 5: 5 e' multiplo di 2.5, nessuna interpolazione). "
            "temperature_2m: air a 0.995 sigma. pressure_msl: pres al livello "
            "del mare (Pa convertiti in hPa). geopotential_height_500hPa: hgt a "
            "500 hPa. La Archive API di Open-Meteo (ERA5) e' stata provata e "
            "scartata per due motivi MISURATI: non serve i livelli di pressione "
            "e la sua quota oraria si esaurisce dopo 40 celle su 360."
        ),
        "sources": {
            "temperature_2m": {
                "provider": "NOAA PSL NCEP/NCAR Reanalysis 1, monthly mean (air 0.995 sigma)",
                "endpoint": NCEP_BASE + NCEP_FIELDS["temperature_2m"]["file"],
                "request": "var=air&vertCoord=0.995&horizStride=2",
                "grid_deg": 2.5,
                "cells": N_CELLS if limit is None else n_cells,
            },
            "pressure_msl": {
                "provider": "NOAA PSL NCEP/NCAR Reanalysis 1, monthly mean (pres, MSL)",
                "endpoint": NCEP_BASE + NCEP_FIELDS["pressure_msl"]["file"],
                "request": "var=pres&horizStride=2",
                "grid_deg": 2.5,
                "cells": N_CELLS if limit is None else n_cells,
            },
            "geopotential_height_500hPa": {
                "provider": "NOAA PSL NCEP/NCAR Reanalysis 1, monthly mean (hgt 500 hPa)",
                "endpoint": NCEP_BASE + NCEP_FIELDS["geopotential_height_500hPa"]["file"],
                "request": "var=hgt&vertCoord=500&horizStride=2",
                "grid_deg": 2.5,
                "cells": N_CELLS if limit is None else n_cells,
            },
            "open_meteo_era5": {
                "provider": "Open-Meteo Archive API (ERA5)",
                "endpoint": ERA5_URL,
                "status": "provata e scartata (misurato il 2026-10-30)",
                "note": (
                    "due muri misurati: (1) i livelli di pressione non sono "
                    "serviti - daily=geopotential_height_500hPa_mean e' "
                    "rifiutata e hourly=geopotential_height_500hPa restituisce "
                    "0 valori non-null su 1991, 2020 e 2024; (2) la quota "
                    "oraria non copre 360 celle da 30 anni - dopo 40 celle "
                    "(876.640 valori) l'API risponde 429 'Hourly API request "
                    "limit exceeded'. Il ramo resta nel builder con "
                    "--source era5."
                ),
            },
        },
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
        "source_mode": source_mode,
        "caveats": caveats,
    }

    target = out_path or os.path.join(root, "data", "climate",
                                       "normals_1991_2020.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=False)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")

    raw_size = os.path.getsize(target)
    gz_size = len(gzip.compress(text.encode("utf-8"), 9))
    log("scritto %s" % target)
    log("raw %d byte (%.1f KB), gzip %d byte (%.1f KB)"
        % (raw_size, raw_size / 1024.0, gz_size, gz_size / 1024.0))
    log("richieste HTTP fatte in questo run: %d" % n_requests)
    log("n_missing (griglia): " + ", ".join("%s=%d" % (f, missing[f]) for f in FIELDS))
    log("giorni ERA5 nulli: " + ", ".join("%s=%d" % (f, daily_missing[f])
                                          for f in FIELDS))
    return doc


def abs_missing(rounded, idx):
    return sum(1 for field in FIELDS
               for v in rounded[field][idx] if v is None)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Climatologia 1991-2020 per le mappe di anomalia")
    parser.add_argument("--refresh", action="store_true",
                        help="ignora la cache e riscarica tutto")
    parser.add_argument("--limit", type=int, default=None,
                        help="elabora solo le prime N celle (prove)")
    parser.add_argument("--sleep", type=float, default=2.0,
                        help="pausa fra due richieste (secondi)")
    parser.add_argument("--source", default="ncep", choices=("ncep", "era5"),
                        help="ncep (default): NCEP/NCAR R1 per tutti i campi; "
                             "era5: temperature_2m e pressure_msl da Open-Meteo "
                             "(Z500 resta NCEP). La quota Open-Meteo non copre "
                             "360 celle: misurato, si ferma a 40.")
    parser.add_argument("--out", default=None, help="percorso del file JSON")
    parser.add_argument("--root", default=None,
                        help="radice del repo (default: due livelli sopra)")
    args = parser.parse_args(argv)

    root = args.root or os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    out = args.out
    if args.limit is not None and out is None:
        out = os.path.join(root, "data", "climate", "_cache",
                           "_sample_normals.json")
        log("--limit: scrivo il campione in %s" % out)
    build(root, refresh=args.refresh, limit=args.limit, sleep_s=args.sleep,
          out_path=out, source=args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
