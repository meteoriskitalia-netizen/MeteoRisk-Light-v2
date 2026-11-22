#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — site_availability.py

TABELLA DISPONIBILITA' RADAR OPERA/VRADH — 13 campi, 5 stati.

Perche' esiste (TODO_1.3.md riga 31): `doppler_coverage.site_features()`
pubblica 9 campi su ~13 e 3 stati (`ok`/`unavailable`/`stale`), e la tabella
`docs/OPERA_VRADH_AVAILABILITY.md` era compilata A MANO: nessun codice la
generava, quindi "0 feature pubblicate". Questo modulo produce la tabella
dai DATI REALI (ORD catalogo + S3 ODIM_H5), senza inventare nulla.

CONTRATTO #207 — ogni campo nuovo e' un dict con QUATTRO chiavi:
    {"value": ..., "null_reason": ..., "source": ..., "reference": ...}
`value is None` IMPLICA `null_reason` non vuoto (field() solleva ValueError
altrimenti). I campi NON riempibili restano null MOTIVATI, mai riempiti con
stime, placeholder o valori di comodo.

I 13 CAMPI (mappatura con la tabella del doc):
    radar_id, radar_name, latitude, longitude, altitude, ni,
    variables, elevation_angles, range, resolution, update_interval,
    last_available_scan, quality_score

I 5 STATI (criteri OPERATIVI, misurati il 2026-11-22):
    invalid     HTTP 200 ma ODIM non parsabile / /where o /dataset1 assenti /
                VRADH fuori range fisico (|v| > 150 m/s)
    stale       scan reale con eta' > STALE_MAX_AGE_MIN (60 min)
    unavailable ORD 204/404/assente dal catalogo AND S3 KeyCount = 0
    partial     dato reale ma almeno un campo non valorizzato (per es.
                quality_score, sempre assente; oppure VRADH non presente;
                oppure solo catalogo ORD senza file ODIM scaricabile)
    ok          scan ODIM reale, tutti e 13 i campi valorizzati, entro 60 min

La soglia 60 min e' quella MISURATA e dichiarata in
`docs/OPERA_VRADH_AVAILABILITY.md` sezione 3 ("non aggiornato = scan piu'
recente piu' vecchio di 60 min rispetto all'estremo temporale del catalogo").
Il codice di `doppler.finalize_coverage` NON ha una soglia numerica: marca
`stale` per carry-forward quando uno scan non arriva affatto; qui la soglia
temporale e' esplicita e verificabile.

FONTI (nessuna credenziale, accesso anonimo):
  ORD   https://api.meteogate.eu/eu-eumetnet-weather-radar/
  S3    https://object-store.os-api.cci1.ecmwf.int/openradar-24h-backup/
  ODIM  ODIM_H5 2.3, h5py (lazy import)

MISURAZIONE 2026-11-22T07:32:16Z (30 siti, nessun dato inventato):
  5 unavailable (itbri/itset/itdes/itfle/itfos: ORD 204 + S3 0 chiavi)
  25 partial (20 con ODIM reale e VRADH ma SENZA quality score; 5 HR con
             catalogo 200 ma 0 chiavi S3)
  0 ok, 0 stale, 0 invalid
Il quality_score e' IMPOSSIBILE da riempire con questi dati: 0/20 file ODIM
hanno un gruppo /quality e la CoverageJSON ORD non ha alcun campo quality
(verificato con visita ricorsiva: 0 hit). Pubblicato come null motivato.
"""

import collections
import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

LICENSE = "CC BY 4.0"
ATTRIBUTION = "(c) EUMETNET OPERA - Open Radar Data / MeteoGate"
ORD_BASE = "https://api.meteogate.eu/eu-eumetnet-weather-radar/"
S3_BACKUP_BASE = ("https://object-store.os-api.cci1.ecmwf.int/"
                  "openradar-24h-backup/")
USER_AGENT = "MeteoRisk-Light-radar-engine/1.2.4"

STALE_MAX_AGE_MIN = 60.0
VRADH_PHYSICAL_LIMIT_MS = 150.0

FIELDS = (
    "radar_id", "radar_name", "latitude", "longitude", "altitude", "ni",
    "variables", "elevation_angles", "range", "resolution",
    "update_interval", "last_available_scan", "quality_score",
)
STATES = ("ok", "partial", "invalid", "stale", "unavailable")

# 30 siti della tabella. I platform ID ORD sono MISURATI il 2026-11-22 su
# /collections/observations/locations (153 piattaforme, 25 trovati, 5 IT assenti).
# I 5 ID italiani non sono nel catalogo: si interroga comunque 0-380-0-<id>
# per poter dichiarare l'HTTP 204 (non un'ipotesi).
SITES = (
    ("itbri", "IT", "0-380-0-itbri"),
    ("itset", "IT", "0-380-0-itset"),
    ("itdes", "IT", "0-380-0-itdes"),
    ("itfle", "IT", "0-380-0-itfle"),
    ("itfos", "IT", "0-380-0-itfos"),
    ("frale", "FR", "0-250-0-frale"),
    ("fraja", "FR", "0-250-0-fraja"),
    ("hrbil", "HR", "0-191-0-hrbil"),
    ("hrdeb", "HR", "0-191-0-hrdeb"),
    ("hrgol", "HR", "0-191-0-hrgol"),
    ("hrgra", "HR", "0-191-0-hrgra"),
    ("hrulj", "HR", "0-191-0-hrulj"),
    ("frbla", "FR", "0-250-0-frbla"),
    ("frbol", "FR", "0-250-0-frbol"),
    ("frcol", "FR", "0-250-0-frcol"),
    ("frmtc", "FR", "0-250-0-frmtc"),
    ("frnan", "FR", "0-250-0-frnan"),
    ("frnim", "FR", "0-250-0-frnim"),
    ("frniz", "FR", "0-250-0-frniz"),
    ("frtro", "FR", "0-250-0-frtro"),
    ("defbg", "DE", "0-276-0-defbg"),
    ("deisn", "DE", "0-276-0-deisn"),
    ("demem", "DE", "0-276-0-demem"),
    ("detur", "DE", "0-276-0-detur"),
    ("huhar", "HU", "0-348-0-huhar"),
    ("chalb", "CH", "0-756-0-chalb"),
    ("chdol", "CH", "0-756-0-chdol"),
    ("chlem", "CH", "0-756-0-chlem"),
    ("chppm", "CH", "0-756-0-chppm"),
    ("chwei", "CH", "0-756-0-chwei"),
)

# Nomi MISURATI dal catalogo ORD /locations (proprieta' `name`, 2026-11-22).
# I 5 italiani non compaiono: il nome resta null con null_reason esplicito.
CATALOG_NAMES = {
    "frale": "Aleria", "fraja": "Ajaccio",
    "hrbil": "Bilogora", "hrdeb": "Debeljak", "hrgol": "Goli",
    "hrgra": "Gradiste", "hrulj": "Uljenje",
    "frbla": "Blaisy", "frbol": "Bollene", "frcol": "Collobrieres",
    "frmtc": "Montancy", "frnan": "Nancy", "frnim": "Nimes",
    "frniz": "StNizier", "frtro": "Arcis",
    "defbg": "defbg", "deisn": "deisn", "demem": "demem", "detur": "detur",
    "huhar": "Harmashegy",
    "chalb": "chalb", "chdol": "chdol", "chlem": "chlem",
    "chppm": "chppm", "chwei": "chwei",
}

# Coordinate REALI lette su ORD /locations il 2026-11-22 (geometry.coordinates
# = [lon, lat], 5 decimali). Usate SOLO come fallback quando l'ODIM non e'
# scaricabile (5 siti HR): mai al posto di /where se il file c'e'.
CATALOG_COORDS = {
    "frale": (9.49639, 42.12971), "fraja": (8.70054, 41.95313),
    "hrbil": (17.20046, 45.88353), "hrdeb": (15.37637, 44.04516),
    "hrgol": (14.12234, 45.02048), "hrgra": (18.70333, 45.15917),
    "hrulj": (17.47829, 42.89439),
    "frbla": (4.77594, 47.35515), "frbol": (4.76221, 44.32305),
    "frcol": (6.37291, 43.21656), "frmtc": (7.01897, 47.36861),
    "frnan": (6.58156, 48.71576), "frnim": (4.50269, 43.80614),
    "frniz": (4.44535, 46.06781), "frtro": (4.30932, 48.46213),
    "defbg": (8.003611, 47.873611), "deisn": (12.101779, 48.174705),
    "demem": (10.219222, 48.042145), "detur": (9.782675, 48.585379),
    "huhar": (18.33723, 46.1775),
    "chalb": (8.512, 47.28433), "chdol": (6.09941, 46.42511),
    "chlem": (8.83322, 46.04076), "chppm": (7.48655, 46.37065),
    "chwei": (9.79446, 46.83497),
}

NO_QUALITY_REASON = "odim_no_quality_group_and_no_ord_quality_field"
NO_QUALITY_SOURCE = ("ODIM /quality (assente in 20/20 file misurati); "
                     "ORD CoverageJSON (0 campi quality su visita ricorsiva)")
NO_QUALITY_REFERENCE = ("docs/OPERA_VRADH_AVAILABILITY.md sez. 4; misurato "
                        "2026-11-22 su frale/fraja/defbg/huhar/chalb")


# ---------------------------------------------------------------------------
# Contratto #207
# ---------------------------------------------------------------------------
def field(value, *, null_reason=None, source=None, reference=None):
    """Campo con `value`/`null_reason`/`source`/`reference` (regola #207).

    Un valore None SENZA null_reason e' un buco silenzioso: solleva ValueError
    invece di pubblicare un campo vuoto non spiegato.
    """
    if value is None and not null_reason:
        raise ValueError("null_field_requires_null_reason")
    return {
        "value": value,
        "null_reason": null_reason,
        "source": source,
        "reference": reference,
    }


# ---------------------------------------------------------------------------
# HTTP (injectable: i test non usano la rete)
# ---------------------------------------------------------------------------
def http_get(url, timeout_s=40):
    """GET anonimo. Ritorna (status, bytes). 4xx non solleva: e' un dato."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"http_error:{type(exc).__name__}", b""


def list_scan_keys(base, day, country, site, http=None, max_pages=12):
    """Tutte le chiavi SCAN del giorno, con PAGINAZIONE COMPLETA.

    Il 2026-11-22 un listing con una sola pagina (max-keys=1000) troncava
    defbg a 1000 chiavi e faceva sembrare lo scan delle 02:45Z (283 min)
    l'ultimo: con paginazione il vero ultimo e' delle 07:30Z. Questa e' la
    ragione per cui la paginazione non e' opzionale.
    """
    get = http or http_get
    prefix = f"{day}/{country}/{site}/SCAN/"
    out, token = [], None
    for _ in range(max_pages):
        q = {"list-type": "2", "prefix": prefix, "max-keys": 1000}
        if token:
            q["continuation-token"] = token
        status, body = get(base + "?" + urllib.parse.urlencode(q))
        if status != 200:
            return out, f"http_{status}"
        text = body.decode("utf-8", "replace")
        out.extend(re.findall(r"<Key>([^<]+)</Key>", text))
        if "<IsTruncated>true</IsTruncated>" not in text:
            return out, None
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>",
                      text)
        token = m.group(1) if m else None
        if not token:
            return out, None
    return out, "too_many_pages"


def _attr(attrs, key, default=None):
    """Attributo ODIM -> Python nativo (bytes decodificati, numpy scalar)."""
    v = attrs.get(key, default)
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    if hasattr(v, "item"):
        try:
            v = v.item()
        except (ValueError, AttributeError):
            pass
    return v


def parse_odim_bytes(data):
    """Parsa un ODIM_H5 e ritorna header + VRADH + presenza di /quality.

    Solleva ValueError sugli header incompatibili (-> stato `invalid`).
    """
    try:
        import h5py
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise ValueError("h5py_missing") from exc
    fd, tmp = tempfile.mkstemp(suffix=".h5")
    os.close(fd)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        try:
            f = h5py.File(tmp, "r")
        except Exception as exc:
            raise ValueError(f"odim_open_error:{exc}") from exc
        with f:
            if "where" not in f or "dataset1" not in f:
                raise ValueError("odim_missing_where_or_dataset1")
            W = f["where"].attrs
            H = f["how"].attrs if "how" in f else {}
            d1 = f["dataset1"]
            if "where" not in d1:
                raise ValueError("odim_missing_dataset1_where")
            DW = d1["where"].attrs
            quantities = []
            vradh = None
            for name in sorted(d1.keys()):
                if not str(name).startswith("data") or "what" not in d1[name]:
                    continue
                wa = d1[name]["what"].attrs
                q = _attr(wa, "quantity", "")
                quantities.append(q)
                if q == "VRADH" and vradh is None:
                    gain = float(_attr(wa, "gain", 1.0))
                    offset = float(_attr(wa, "offset", 0.0))
                    nodata = float(_attr(wa, "nodata", 255.0))
                    undetect = float(_attr(wa, "undetect", 0.0))
                    raw = np.asarray(d1[name]["data"][...])
                    good = ((raw != nodata) & (raw != undetect)
                            & np.isfinite(raw))
                    phys = raw[good] * gain + offset
                    lo = round(float(phys.min()), 2) if phys.size else None
                    hi = round(float(phys.max()), 2) if phys.size else None
                    xs = [abs(v) for v in (lo, hi) if v is not None]
                    if xs and max(xs) > VRADH_PHYSICAL_LIMIT_MS:
                        raise ValueError(
                            f"vradh_out_of_physical_range:{lo}..{hi}")
                    vradh = {"gain": gain, "offset": offset,
                             "nodata": nodata, "undetect": undetect,
                             "valid_pixels": int(good.sum()),
                             "total_pixels": int(raw.size),
                             "min_ms": lo, "max_ms": hi}
            quality_nodes = []
            def visit(name, _obj):
                if "quality" in name.lower():
                    quality_nodes.append(name)
            f.visititems(visit)
            lat = float(_attr(W, "lat"))
            lon = float(_attr(W, "lon"))
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                raise ValueError(f"odim_latlon_out_of_range:{lat},{lon}")
            return {
                "lat": lat, "lon": lon,
                "height_m": float(_attr(W, "height", 0.0)),
                "ni": _attr(H, "NI"),
                "beamwidth": _attr(H, "beamwidth"),
                "software": _attr(H, "software"),
                "elangle": float(_attr(DW, "elangle", 0.0)),
                "nrays": int(_attr(DW, "nrays", 0)),
                "nbins": int(_attr(DW, "nbins", 0)),
                "rscale": float(_attr(DW, "rscale", 0.0)),
                "quantities": quantities, "vradh": vradh,
                "quality_nodes": quality_nodes[:10],
            }
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Misura di un sito ORD + S3 + ODIM (rete, injectable)
# ---------------------------------------------------------------------------
def measure_site(site, country, platform, *, day=None, now=None, http=None,
                 s3_base=S3_BACKUP_BASE, ord_base=ORD_BASE,
                 fetch_bytes=None):
    """Raccoglie la misura GREZZA di un sito. Nessun campo derivato qui."""
    get = http or http_get
    day = day or _dt.datetime.now(_dt.timezone.utc).strftime("%Y/%m/%d")
    now = now or _dt.datetime.now(_dt.timezone.utc)
    url = (ord_base + "collections/observations/locations/" + platform +
           "?datetime=" + _ord_window(day) +
           "&standard_name=VRADH&format=ODIM&f=CoverageJSON")
    ord_status, ord_body = get(url)
    if ord_status == 200:
        try:
            ord_coverages = len((json.loads(ord_body)).get("coverages") or [])
        except Exception:
            ord_coverages = None
    else:
        ord_coverages = None
    keys, listing_error = list_scan_keys(s3_base, day, country, site,
                                         http=get)
    out = {
        "site": site, "country": country, "platform": platform,
        "measured_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ord_status": ord_status, "ord_coverages": ord_coverages,
        "s3_keycount": len(keys), "s3_listing_error": listing_error,
    }
    products, elevations, stamps = set(), set(), []
    for key in keys:
        products.update(key.split("@")[-1].replace(".h5", "").split("_"))
        m = re.search(r"@(\d{8}T\d{4})@([0-9.]+)@", key)
        if m:
            stamps.append(m.group(1))
            elevations.add(float(m.group(2)))
    out["variables_from_names"] = sorted(products)
    out["elevation_angles"] = sorted(elevations)
    if not stamps:
        return out
    latest = max(stamps)
    t = _dt.datetime.strptime(latest, "%Y%m%dT%H%M").replace(
        tzinfo=_dt.timezone.utc)
    out["last_available_scan"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["age_min"] = round(max(0.0, (now - t).total_seconds() / 60.0), 1)
    # Cadenza del VOLUME, non del singolo file: il backup S3 pubblica un file
    # per elevazione a minuti consecutivi (1 min), mentre lo stesso volume
    # torna ogni 5 min. Si misura il delta mediano fra i timestamp dell'ELEVAZIONE
    # MINIMA: e' un valore letto dai nomi, non una costante (doc sez. 3-4:
    # PT5M volume / PT1M singolo scan).
    if elevations:
        low = min(elevations)
        low_stamps = sorted({
            m.group(1) for k in keys
            if (m := re.search(r"@(\d{8}T\d{4})@([0-9.]+)@", k))
            and float(m.group(2)) == low})
        if len(low_stamps) > 1:
            deltas = [(_dt.datetime.strptime(b, "%Y%m%dT%H%M")
                       - _dt.datetime.strptime(a, "%Y%m%dT%H%M")).total_seconds()
                      / 60.0 for a, b in zip(low_stamps, low_stamps[1:])]
            out["update_interval_min"] = round(float(
                sorted(deltas)[len(deltas) // 2]), 2)
    cands = [k for k in keys if f"@{latest}@" in k]
    vrads = [k for k in cands if "VRADH" in k.split("@")[-1]]
    pick = sorted(vrads or cands)[0]
    out["sample_key"] = pick
    dl = fetch_bytes or (lambda u: http_get(u, timeout_s=90))
    status, data = dl(s3_base + pick)
    out["odim_http"] = status
    if status != 200:
        out["odim_error"] = f"http_{status}"
        return out
    out["odim_sha256"] = hashlib.sha256(data).hexdigest()
    try:
        out["odim"] = parse_odim_bytes(data)
    except ValueError as exc:
        out["odim_error"] = str(exc)
    return out


def _ord_window(day):
    """Finestra ORD di 48 h che termina alla fine del giorno misurato."""
    d = _dt.datetime.strptime(day, "%Y/%m/%d").replace(tzinfo=_dt.timezone.utc)
    return ((d - _dt.timedelta(days=1)).strftime("%Y-%m-%dT00:00Z") + "/" +
            (d + _dt.timedelta(days=1)).strftime("%Y-%m-%dT23:59Z"))


# ---------------------------------------------------------------------------
# Classificazione operativa dei 5 stati
# ---------------------------------------------------------------------------
def classify(m, null_fields=None):
    """Stato OPERATIVO da una misura grezza. Nessun valore inventato.

    `null_fields` (quando e' passato da `build_record`) e' l'elenco REALE dei
    campi il cui valore e' None con null_reason. Se non e' passato, si calcola
    una versione conservativa: il quality_score manca sempre oggi, quindi uno
    scan reale e' `partial` finche' nessun provider pubblica un quality score.
    """
    if m.get("odim_error") or m.get("odim_http") not in (200, None):
        return "invalid", m.get("odim_error") or f"http_{m.get('odim_http')}"
    age = m.get("age_min")
    if age is not None and age > STALE_MAX_AGE_MIN:
        return "stale", (f"scan_age_min={age}>{STALE_MAX_AGE_MIN}")
    ord_st = m.get("ord_status")
    no_ord = (ord_st in (204, "not_in_catalog")
              or (isinstance(ord_st, str) and ord_st.startswith("http_error")))
    if no_ord and not m.get("s3_keycount"):
        return "unavailable", f"ord_{ord_st}_and_s3_0_keys"
    if age is None:
        return "partial", "no_scan_time_but_some_data"
    if null_fields is None:
        null_fields = []
        if m.get("quality_score") is None:
            null_fields.append("quality_score")
    if null_fields:
        return "partial", "missing_fields:" + ",".join(sorted(null_fields))
    return "ok", None


def build_record(m, *, now=None):
    """Record completo: 13 campi con contratto #207 + stato + motivi."""
    od = m.get("odim") or {}
    site = m["site"]
    name = CATALOG_NAMES.get(site)
    lat, lon = od.get("lat"), od.get("lon")
    coord_source = "ODIM /where/lat, /where/lon"
    if lat is None or lon is None:
        cat = CATALOG_COORDS.get(site)
        if cat:
            lon, lat = cat  # geometry ORD e' [lon, lat]: MISURATO, non dedotto
            coord_source = "ORD /collections/observations/locations geometry"
    height = od.get("height_m")
    variables = m.get("variables_from_names") or []
    variables = sorted(set(variables) | set(od.get("quantities") or []))
    # `elevations` e' il nome usato dalle misure grezze salvate; `elevation_angles`
    # e' il nome del campo pubblicato. Entrambi sono accettati (stesso dato).
    elevations = m.get("elevation_angles") or m.get("elevations") or []
    if od.get("elangle") is not None and not elevations:
        elevations = [round(float(od["elangle"]), 4)]
    nrays, nbins, rscale = (od.get("nrays"), od.get("nbins"),
                            od.get("rscale"))
    range_km = (round(nbins * rscale / 1000.0, 1)
                if nbins and rscale else None)
    resolution = None
    if nbins and rscale:
        bw = od.get("beamwidth")
        resolution = (f"{round(rscale)} m range, "
                      f"{round(float(bw), 2)} deg azimuth" if bw
                      else f"{round(rscale)} m range")
    interval = m.get("update_interval_min")
    scan = m.get("last_available_scan")
    ni = od.get("ni")
    fields = {
        "radar_id": field(site, source="S3 SCAN key / ORD platform id",
                          reference="OPERA Open Radar Data"),
        "radar_name": field(
            name,
            null_reason=("absent_from_ord_catalog_153_platforms"
                         if name is None else None),
            source="ORD /collections/observations/locations name",
            reference="misurato 2026-11-22"),
        "latitude": field(
            lat,
            null_reason="lat_unavailable_in_odim_and_ord_catalog"
            if lat is None else None,
            source=coord_source, reference="ODIM_H5 2.3 / ORD locations"),
        "longitude": field(
            lon,
            null_reason="lon_unavailable_in_odim_and_ord_catalog"
            if lon is None else None,
            source=coord_source, reference="ODIM_H5 2.3 / ORD locations"),
        "altitude": field(
            height,
            null_reason="odim_where_height_unavailable" if height is None
            else None,
            source="ODIM /where/height", reference="ODIM_H5 2.3"),
        "ni": field(
            round(float(ni), 3) if ni is not None else None,
            null_reason="odim_how_NI_absent" if ni is None else None,
            source="ODIM /how/NI", reference="ODIM_H5 2.3"),
        "variables": field(
            variables or None,
            null_reason="no_s3_scan_keys_to_read_quantity"
            if not variables else None,
            source=("S3 key product token + ODIM "
                    "/dataset1/dataN/what/quantity"),
            reference="misurato 2026-11-22"),
        "elevation_angles": field(
            elevations or None,
            null_reason="no_s3_scan_keys_to_read_elangle"
            if not elevations else None,
            source="S3 key elevation token + ODIM /dataset1/where/elangle",
            reference="misurato 2026-11-22"),
        "range": field(
            range_km,
            null_reason="odim_grid_geometry_unavailable" if range_km is None
            else None,
            source="ODIM /dataset1/where nbins x rscale",
            reference="ODIM_H5 2.3"),
        "resolution": field(
            resolution,
            null_reason="odim_grid_geometry_unavailable"
            if resolution is None else None,
            source="ODIM /dataset1/where rscale + /how/beamwidth",
            reference="ODIM_H5 2.3"),
        "update_interval": field(
            interval,
            null_reason="fewer_than_two_scan_stamps" if interval is None
            else None,
            source=("mediana dei delta fra timestamp S3 dell'elevazione "
                    "minima (cadenza del volume, non del singolo file)"),
            reference="misurato 2026-11-22"),
        "last_available_scan": field(
            scan,
            null_reason=("ord_204_and_s3_0_keys" if m.get("ord_status") == 204
                         else "no_s3_scan_keys") if scan is None else None,
            source="max timestamp nei nomi dei file S3 SCAN",
            reference="misurato 2026-11-22"),
        "quality_score": field(
            m.get("quality_score"),
            null_reason=(None if m.get("quality_score") is not None
                         else NO_QUALITY_REASON),
            source=(NO_QUALITY_SOURCE
                    if m.get("quality_score") is None
                    else "provider quality field (se presente)"),
            reference=NO_QUALITY_REFERENCE),
    }
    # coerenza #207: ogni campo ha 4 chiavi e i null hanno motivo
    for key, val in fields.items():
        if set(val) != {"value", "null_reason", "source", "reference"}:
            raise ValueError(f"bad_field_contract:{key}")
        if val["value"] is None and not val["null_reason"]:
            raise ValueError(f"silent_null:{key}")
    null_reasons = [k for k, v in fields.items() if v["value"] is None]
    status, status_reason = classify(m, null_fields=null_reasons)
    return {
        "radar_id": site, "country": m.get("country"), "status": status,
        "status_reason": status_reason, "fields": fields,
        "null_fields": null_reasons,
        "n_fields_filled": len(FIELDS) - len(null_reasons),
        "n_fields": len(FIELDS),
        "sample_key": m.get("sample_key"),
        "odim_sha256": m.get("odim_sha256"),
        "age_min": m.get("age_min"),
    }


def build_table(measurements, *, generated_at=None, license_=LICENSE):
    """Tabella completa: 30 righe, conteggi per stato, dichiarazione limiti."""
    generated_at = generated_at or _dt.datetime.now(
        _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [build_record(m) for m in measurements]
    counts = collections.Counter(r["status"] for r in rows)
    return {
        "type": "RadarAvailabilityTable",
        "schema_version": 1,
        "generated_at": generated_at,
        "license": license_, "attribution": ATTRIBUTION,
        "fields": list(FIELDS), "states": list(STATES),
        "stale_max_age_min": STALE_MAX_AGE_MIN,
        "counts": {s: int(counts.get(s, 0)) for s in STATES},
        "n_sites": len(rows),
        "limits": [
            "quality_score e' null MOTIVATO: nessun gruppo /quality nei file "
            "ODIM misurati e nessun campo quality nella CoverageJSON ORD.",
            "La finestra e' di 24 h (cache rolling ORD): la disponibilita' "
            "di un sito puo' cambiare domani.",
            "Le elevazioni multiple sono lette dai nomi dei file S3: il "
            "backup pubblica un file per elevazione (o per prodotto).",
        ],
        "sites": rows,
    }


def render_markdown(table):
    """Tabella Markdown con le 13 colonne + STATO (per docs/)."""
    head = ("| RADAR_ID | RADAR_NAME | LATITUDE | LONGITUDE | ALTITUDE | NI | "
            "VARIABLES | ELEVATION_ANGLES | RANGE | RESOLUTION | "
            "UPDATE_INTERVAL | LAST_AVAILABLE_SCAN | QUALITY_SCORE | STATO |")
    sep = "|" + "|".join(["---"] * 14) + "|"
    lines = [head, sep]
    for r in table["sites"]:
        f = r["fields"]
        def v(name):
            x = f[name]
            if x["value"] is None:
                return "NON VERIFICATO (" + x["null_reason"] + ")"
            val = x["value"]
            if isinstance(val, list):
                return ", ".join(str(t) for t in val)
            return str(val)
        cells = [v(k) for k in FIELDS] + [
            r["status"] + " — " + r["status_reason"]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Tabella disponibilita' radar OPERA/VRADH (dati reali)")
    ap.add_argument("--out", default="data/radar/radar_availability.json")
    ap.add_argument("--markdown", default=None,
                    help="scrive anche la tabella Markdown in questo file")
    ap.add_argument("--day", default=None, help="YYYY/MM/DD UTC (default oggi)")
    ap.add_argument("--sites", default=None,
                    help="lista di ID separata da virgole (default: 30)")
    ap.add_argument("--pause-s", type=float, default=0.3)
    args = ap.parse_args(argv)
    wanted = (args.sites.split(",") if args.sites else
              [s[0] for s in SITES])
    by_id = {s[0]: s for s in SITES}
    now = _dt.datetime.now(_dt.timezone.utc)
    measurements = []
    for sid in wanted:
        _id, country, platform = by_id[sid]
        measurements.append(measure_site(sid, country, platform,
                                         day=args.day, now=now))
        if args.pause_s:
            time.sleep(float(args.pause_s))
    table = build_table(measurements,
                        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    out = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(table, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp, out)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render_markdown(table) + "\n")
    print(json.dumps({"counts": table["counts"], "n_sites": table["n_sites"],
                      "out": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
