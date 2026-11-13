#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — accumulation.py

ACCUMULO DI PIOGGIA AL SUOLO da Radar-DPC SRT1 (cumulata 1 h, mm) e
triangolazione con le centraline MeteoHub.

COSA E' SRT1 (LETTO dalla documentazione ufficiale DPC, 2026-11-13):
  https://dpc-radar.readthedocs.io/it/latest/
  "SRT_1 - Cumulata di precipitazione in 1 ora - Aggiornamento 5 min.
   E' un prodotto che rappresenta la cumulata di precipitazione (mm)
   nell'ultima ora sulla base dell'integrazione del dato radar SRI su 1 ora
   e i dati della rete a terra."
  L'endpoint API elenca il tipo come `SRT1` (non `SRT_1`); `findLastProductByType`
  risponde `period: "PT5M"`.
  I prodotti `CUM3/CUM6/CUM12/CUM24` esistono ma sono INTERPOLAZIONI della sola
  rete pluviometrica (griglia 0.01 deg), NON accumuli radar: non sono usati qui
  come fonte radar (verificato il 2026-11-13, v. report della sessione).

MISURATO sul prodotto reale 2026-11-13T19:10Z (slot 13-11-2026-19-10.tif):
  griglia 1200x1400, float32, 1 banda, 1 km, Transverse Mercator custom
  (lat0=42, lon0=12.5, origine -600000/650000) = STESSA griglia della VMI;
  nessun nodata dichiarato nel TIFF -> sentinella DERIVATA dal prodotto: -9999.0
  (734.745 px nello slot misurato); 0.0 e' assenza valida (849.327 px);
  range valori 0.0 .. 27.905 mm; TIFFTAG_DATETIME 19:16:47 per uno slot 19:10
  (latenza di pubblicazione ~6m47s, assunto UTC).
  CUM3 misurato come controllo: 1341x1233, EPSG:4326 passo 0.01 deg, nodata
  -9999 dichiarato, max 32.995 mm -> NON e' la stessa griglia e non e' radar.

REGOLE:
  - nessuna soglia meteorologica, nessuna parola di allerta: solo mm e
    statistiche (p10/p50/p90/max, pixel validi);
  - `0.0` NON e' un buco: e' assenza di accumulo;
  - `-9999.0` (o il nodata dichiarato dal TIFF) e' fuori copertura: escluso;
  - un valore negativo diverso dal nodata viene CONTATO e dichiarato, mai
    silenziosamente incluso nelle statistiche;
  - output atomico e budget di dimensione (< 200 KB) verificato PRIMA della
    scrittura.

Licenza dei dati: CC BY-SA 4.0, attribuzione "Radar-DPC — Dipartimento della
Protezione Civile". Il modulo pubblica SOLO derivati (statistiche), mai il
GeoTIFF raw.

Uso:
  python scripts/radar_engine/accumulation.py --out-dir data/radar
  python scripts/radar_engine/accumulation.py --as-of 2026-11-13T19:10:00Z
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile

import numpy as np

# Importabile sia come package (`python -m radar_engine.accumulation`) sia come
# script (`python scripts/radar_engine/accumulation.py`, come fa il workflow).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from radar_engine import models, source_dpc  # noqa: E402
    from radar_engine import accumulation_validation as _val  # noqa: E402
else:
    from . import models, source_dpc  # noqa: E402
    from . import accumulation_validation as _val  # noqa: E402


PRODUCT_TYPE = "SRT1"
WINDOW_H = 1.0
WINDOW_S = int(round(WINDOW_H * 3600))
# Cadenza MISURATA dall'API (period "PT5M"). Usata SOLO per il replay quando
# `findLastProductByType` non viene interrogato (nessuna dipendenza dall'orologio).
DEFAULT_PERIOD_S = 300
SENTINEL_MM = -9999.0
MAX_OUTPUT_BYTES = 200 * 1024

SOURCE_LABEL = ("Radar-DPC SRT1 — cumulata di precipitazione 1 h (mm), "
                "integrazione del dato radar SRI su 1 ora e rete a terra")
LICENCE = "CC BY-SA 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/deed.it"
ATTRIBUTION = ("Dati: Radar-DPC — Dipartimento della Protezione Civile "
               "(https://radar.protezionecivile.it), licenza CC BY-SA 4.0")

# Le SEI macro-aree sono le stesse del client (`ITALIA_AREAS` in
# mri-light-*.html, release 1.2.4.73+): qui sono riportate lato dati per poter
# aggregare i pixel. La mappa regione -> area e' ESPLICITA, non inferita.
AREAS = (
    ("Nord", ("Valle d'Aosta", "Piemonte", "Lombardia", "Trentino-Alto Adige",
              "Veneto", "Friuli-Venezia Giulia", "Liguria", "Emilia-Romagna")),
    ("Centro Tirreno", ("Toscana", "Umbria", "Lazio")),
    ("Centro Adriatico", ("Marche", "Abruzzo", "Molise")),
    ("Sud", ("Campania", "Puglia", "Basilicata", "Calabria")),
    ("Sicilia", ("Sicilia",)),
    ("Sardegna", ("Sardegna",)),
)
AREA_NAMES = tuple(a[0] for a in AREAS)
_REGION_TO_AREA = {r: name for name, regions in AREAS for r in regions}

DEFAULT_GEOJSON = os.path.join("data", "geography", "province_italiane.geojson")
DEFAULT_REGIONS = os.path.join("data", "geography", "regions_data.json")
DEFAULT_STATIONS = os.path.join("data", "stations", "latest.json")

DEFAULT_API_BASE = "https://radar-api.protezionecivile.it"
DEFAULT_ORIGIN = "https://radar.protezionecivile.it"

_AREA_MASK_CACHE = {}


def _source(config):
    """Config source con i default DPC (l'header Origin e' OBBLIGATORIO)."""
    src = dict((config or {}).get("source") or {})
    src.setdefault("api_base", DEFAULT_API_BASE)
    src.setdefault("origin_header", DEFAULT_ORIGIN)
    src.setdefault("http_timeout_s", 30)
    src.setdefault("download_timeout_s", 60)
    src.setdefault("max_download_retries", 2)
    return src


class AccumulationError(Exception):
    """Errore dichiarato del layer accumulo (mai un valore inventato)."""


def iso_utc(ms):
    return _dt.datetime.fromtimestamp(ms / 1000.0, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(text):
    """ISO-8601 -> epoch ms UTC. Accetta 'Z', '+00:00' e naive (assunto UTC)."""
    s = str(text).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError as exc:
        raise AccumulationError(f"invalid_iso8601:{text}") from exc
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return int(d.timestamp() * 1000)


def floor_to_step(ms, step_s=DEFAULT_PERIOD_S):
    step_ms = int(step_s) * 1000
    return (int(ms) // step_ms) * step_ms


# ---------------------------------------------------------------------------
# Lettura del GeoTIFF di accumulo
# ---------------------------------------------------------------------------

class AccumulationRaster:
    """Raster di accumulo con maschera di validita' DERIVATA dal prodotto."""

    def __init__(self, values, transform, crs, width, height, nodata,
                 declared_nodata, source_path=None):
        self.values = values
        self.transform = transform
        self.crs = crs
        self.width = int(width)
        self.height = int(height)
        self.nodata = float(nodata)
        self.declared_nodata = declared_nodata
        self.source_path = source_path
        finite = np.isfinite(values)
        # Un accumulo negativo non esiste: il nodata (tipicamente -9999) e
        # qualunque altro negativo restano FUORI dalle statistiche. `0.0` e'
        # invece un'assenza valida e resta dentro.
        self.valid_mask = finite & (values >= 0.0) & (values != self.nodata)
        self.sentinel_px = int((finite & (values == self.nodata)).sum())
        self.nonfinite_px = int((~finite).sum())
        neg = finite & (values < 0) & (values != self.nodata)
        self.unexpected_negative_px = int(neg.sum())
        self.valid_values = values[self.valid_mask]

    @property
    def valid_px(self):
        return int(self.valid_mask.sum())

    @property
    def total_px(self):
        return int(self.values.size)


def read_accumulation(path):
    """Legge il GeoTIFF di accumulo. Solleva AccumulationError dichiarato."""
    if not os.path.isfile(path):
        raise AccumulationError(f"file_missing:{path}")
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover - dipendenza del motore
        raise AccumulationError("rasterio_missing") from exc
    try:
        with rasterio.open(path) as ds:
            if ds.count != 1:
                raise AccumulationError(
                    f"unsupported_raster:bands={ds.count}")
            if not np.issubdtype(np.dtype(ds.dtypes[0]), np.floating):
                raise AccumulationError(
                    f"unsupported_raster:dtype={ds.dtypes[0]}")
            values = ds.read(1).astype("float64", copy=False)
            declared = ds.nodata
            nodata = SENTINEL_MM if declared is None else float(declared)
            return AccumulationRaster(
                values=values, transform=ds.transform, crs=ds.crs,
                width=ds.width, height=ds.height, nodata=nodata,
                declared_nodata=None if declared is None else float(declared),
                source_path=path)
    except AccumulationError:
        raise
    except Exception as exc:
        raise AccumulationError(
            f"raster_unreadable:{exc.__class__.__name__}:{exc}") from exc


# ---------------------------------------------------------------------------
# Maschere delle macro-aree (province -> regione -> area, rasterizzate)
# ---------------------------------------------------------------------------

def _load_area_geometries(geo_path, regions_path):
    if not os.path.isfile(geo_path):
        raise AccumulationError(f"geojson_missing:{geo_path}")
    if not os.path.isfile(regions_path):
        raise AccumulationError(f"regions_missing:{regions_path}")
    try:
        with open(geo_path, encoding="utf-8") as fh:
            geo = json.load(fh)
        with open(regions_path, encoding="utf-8") as fh:
            regions = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AccumulationError(f"geography_unreadable:{exc}") from exc
    sig2region = {r["sigla"]: r["region"] for r in regions
                  if r.get("sigla") and r.get("region")}
    features = geo.get("features") or []
    if not features:
        raise AccumulationError("geojson_no_features")
    result = []
    unknown = set()
    for feat in features:
        sig = (feat.get("properties") or {}).get("SIGLA")
        region = sig2region.get(sig)
        if region is None:
            unknown.add(str(sig))
            continue
        area = _REGION_TO_AREA.get(region)
        if area is None:
            unknown.add(region)
            continue
        result.append((feat.get("geometry"), area))
    if unknown:
        raise AccumulationError(
            "unmapped_regions:" + ",".join(sorted(unknown)))
    if not result:
        raise AccumulationError("geojson_no_mapped_features")
    return result


def build_area_masks(raster, geo_path=DEFAULT_GEOJSON,
                     regions_path=DEFAULT_REGIONS):
    """dict area -> maschera bool sulla griglia del raster.

    Le province sono rasterizzate sulla griglia radar (stessa CRS del
    prodotto). La maschera e' la copertura del poligono amministrativo, non
    un rettangolo: i pixel fuori copertura radar restano esclusi a valle.
    """
    key = (str(raster.crs), tuple(raster.transform), raster.width, raster.height,
           os.path.abspath(geo_path), os.path.abspath(regions_path))
    cached = _AREA_MASK_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from rasterio.features import rasterize
        from rasterio.warp import transform_geom
    except ImportError as exc:  # pragma: no cover
        raise AccumulationError("rasterio_missing") from exc

    geoms = _load_area_geometries(geo_path, regions_path)
    shapes = []
    for geom, area in geoms:
        if geom is None:
            continue
        try:
            projected = transform_geom("EPSG:4326", raster.crs, geom)
        except Exception as exc:
            raise AccumulationError(
                f"geometry_transform_failed:{area}:{exc}") from exc
        shapes.append((projected, AREA_NAMES.index(area)))
    idx = rasterize(shapes, out_shape=(raster.height, raster.width),
                    transform=raster.transform, fill=-1, dtype="int16",
                    all_touched=False)
    # Un'area senza pixel sulla griglia NON e' un errore (griglia parziale o
    # test sintetico): resta con valid_px=0 e statistiche null, dichiarato.
    masks = {name: (idx == i) for i, name in enumerate(AREA_NAMES)}
    _AREA_MASK_CACHE[key] = masks
    return masks


# ---------------------------------------------------------------------------
# Statistiche
# ---------------------------------------------------------------------------

def _round(value, ndigits):
    return None if value is None else round(float(value), ndigits)


def area_stats(raster, mask):
    """Statistiche mm di una macro-area sui SOLI pixel validi."""
    sel = mask & raster.valid_mask
    values = raster.values[sel]
    out = {
        "area_px": int(mask.sum()),
        "valid_px": int(values.size),
        "wet_px": int((values > 0.0).sum()),
        "p10_mm": None, "p50_mm": None, "p90_mm": None,
        "max_mm": None, "mean_mm": None,
    }
    if values.size:
        p10, p50, p90 = np.percentile(values, [10.0, 50.0, 90.0])
        out.update({
            "p10_mm": _round(p10, 3),
            "p50_mm": _round(p50, 3),
            "p90_mm": _round(p90, 3),
            "max_mm": _round(values.max(), 3),
            "mean_mm": _round(values.mean(), 4),
        })
    return out


def build_slot(raster, valid_time_ms, masks, slot_source=SOURCE_LABEL,
               window_h=WINDOW_H):
    areas = {name: area_stats(raster, masks[name]) for name in AREA_NAMES}
    totals = {
        "valid_px": raster.valid_px,
        "total_px": raster.total_px,
        "sentinel_px": raster.sentinel_px,
        "nonfinite_px": raster.nonfinite_px,
        "unexpected_negative_px": raster.unexpected_negative_px,
        "wet_px": int(sum(a["wet_px"] for a in areas.values())),
        "max_mm": max((a["max_mm"] for a in areas.values()
                       if a["max_mm"] is not None), default=None),
    }
    return {
        "valid_time": iso_utc(valid_time_ms),
        "valid_time_ms": int(valid_time_ms),
        "window_h": float(window_h),
        "source": slot_source,
        "licence": LICENCE,
        "areas": areas,
        "totals": totals,
    }


# ---------------------------------------------------------------------------
# Fetch (stesso schema di fetch.py: slot esplicito, skew dichiarato)
# ---------------------------------------------------------------------------

def resolve_slots(config, as_of_ms=None, count=1):
    """Slot (epoch ms, crescente) da scaricare.

    Live: `findLastProductByType(SRT1)` decide l'ultimo slot pubblicato.
    Replay (`as_of_ms`): l'API NON viene interrogata (nessuna dipendenza
    dall'orologio, stessa regola di `fetch.plan_window(anchor_ms=...)`); si
    parte dallo slot `as_of` arrotondato per difetto al passo MISURATO (300 s)
    e si scende solo se lo slot non e' pubblicato.
    """
    src = _source(config)
    step_s = int(src.get("accumulation_period_s") or DEFAULT_PERIOD_S)
    if as_of_ms is None:
        latest = source_dpc.get_latest_product(
            PRODUCT_TYPE, api_base=src.get("api_base"),
            origin=src.get("origin_header"),
            timeout_s=src.get("http_timeout_s", 30))
        step_s = int(latest.period_s or step_s)
        anchor = int(latest.time_ms)
    else:
        anchor = floor_to_step(int(as_of_ms), step_s)
    slots = []
    for k in range(max(1, int(count))):
        ts = anchor - k * step_s * 1000
        if ts not in slots:
            slots.append(ts)
    slots.sort()
    return slots, step_s


def fetch_slot(config, slot_ms, step_s=DEFAULT_PERIOD_S, back_steps=3):
    """Scarica UNA slot SRT1 (con fallback dichiarato agli slot precedenti).

    Ritorna (path, valid_time_ms, meta). Solleva SourceError se nessuno slot
    e' scaricabile. Il timestamp pubblicato e' quello REALE del nome file
    (`parse_key_timestamp`), mai quello del candidato.
    """
    src = _source(config)
    cache_dir = src.get("cache_dir") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "temp")
    os.makedirs(cache_dir, exist_ok=True)
    last_err = None
    tried = []
    for k in range(int(back_steps) + 1):
        cand = int(slot_ms) - k * int(step_s) * 1000
        tried.append(iso_utc(cand))
        probe = models.ProductInfo(PRODUCT_TYPE, cand, int(step_s))
        try:
            s3_key, content = source_dpc.download_product(
                probe, api_base=src.get("api_base"),
                origin=src.get("origin_header"),
                timeout_s=src.get("http_timeout_s", 30),
                download_timeout_s=src.get("download_timeout_s", 60),
                retries=src.get("max_download_retries", 2))
        except models.SourceError as exc:
            last_err = exc
            if not source_dpc.is_not_published_error(exc):
                raise
            continue
        try:
            real_ts = source_dpc.parse_key_timestamp(s3_key)
        except ValueError as exc:
            last_err = exc
            continue
        path = os.path.join(
            cache_dir, PRODUCT_TYPE + "__" + os.path.basename(s3_key))
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(content)
        os.replace(tmp, path)
        try:
            source_dpc.validate_download(path)
        except models.ValidationError as exc:
            last_err = exc
            continue
        return path, real_ts, {
            "requested_slot": iso_utc(slot_ms),
            "actual_slot": iso_utc(real_ts),
            "skew_s": int((real_ts - int(slot_ms)) / 1000),
            "fallback_steps": k,
            "tried_slots": tried,
            "s3_key": s3_key,
            "bytes": len(content),
        }
    raise models.SourceError(
        f"no_accumulation_slot:{','.join(tried)}:{last_err}")


# ---------------------------------------------------------------------------
# Payload + scrittura atomica con budget
# ---------------------------------------------------------------------------

def _atomic_write_json(path, payload, budget_bytes=MAX_OUTPUT_BYTES):
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=False)
    size = len(text.encode("utf-8"))
    if size >= int(budget_bytes):
        raise AccumulationError(
            f"output_budget_exceeded:{size}>={int(budget_bytes)}")
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".accum-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise AccumulationError(f"atomic_write_failed:{exc}") from exc
    return size


def build_payload(slots, generated_at=None, as_of_ms=None, meta=None,
                  validation=None, raster_meta=None, limits=None):
    generated_at = generated_at or iso_utc(int(_dt.datetime.now(
        _dt.timezone.utc).timestamp() * 1000))
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "status": "ok" if slots else "unavailable",
        "product": {
            "type": PRODUCT_TYPE,
            "window_h": WINDOW_H,
            "cadence_s": int((meta or {}).get("step_s") or DEFAULT_PERIOD_S),
            "units": "mm",
            "grid": raster_meta or None,
            "sentinel_mm": SENTINEL_MM,
            "zero_is_valid": True,
        },
        "source": SOURCE_LABEL,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": ATTRIBUTION,
        "as_of_utc": None if as_of_ms is None else iso_utc(as_of_ms),
        "slots_requested": int((meta or {}).get("slots_requested") or 0),
        "slots_published": len(slots),
        "fetch": meta or None,
        "slots": slots,
        "validation": validation,
        "limits": limits or [],
    }
    return payload


def _default_limits(raster):
    coverage = (float(raster.valid_px) / raster.total_px
                if raster.total_px else 0.0)
    return [
        "SRT1 e' un prodotto Radar-DPC derivato da SRI radar e da una rete "
        "pluviometrica: non e' un pluviometro puntuale.",
        "0.0 mm e' assenza di accumulo valida; %.1f mm e' fuori copertura e "
        "viene escluso." % SENTINEL_MM,
        "Le statistiche per macro-area sono su pixel di griglia 1 km con i "
        "confini provinciali rasterizzati sulla griglia radar, non su area "
        "amministrativa esatta.",
        "Copertura radar dello slot: %.2f%% dei pixel della griglia "
        "(%.0f/%.0f); il resto e' fuori copertura e non entra nelle "
        "statistiche." % (100.0 * coverage, raster.valid_px, raster.total_px),
        "Nessuna soglia meteorologica: solo millimetri e statistiche.",
    ]


def run(out_dir, as_of_ms=None, stations_path=DEFAULT_STATIONS,
        geo_path=DEFAULT_GEOJSON, regions_path=DEFAULT_REGIONS,
        max_stations=200, tolerance_s=2700, slots=1, config=None,
        generated_at=None):
    """Fetch + statistiche + validazione + scrittura. Ritorna il payload.

    Un prodotto non disponibile NON e' un crash: si scrive `status:
    unavailable` con il motivo e si esce 0 (il run radar prosegue).
    """
    config = config or {}
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "accumulation.json")
    val_path = os.path.join(out_dir, "accumulation_validation.json")
    generated_at = generated_at or iso_utc(int(_dt.datetime.now(
        _dt.timezone.utc).timestamp() * 1000))

    try:
        slot_list, step_s = resolve_slots(config, as_of_ms=as_of_ms,
                                          count=slots)
    except (models.SourceError, AccumulationError) as exc:
        payload = build_payload(
            [], generated_at=generated_at, as_of_ms=as_of_ms,
            meta={"reason": f"slot_resolution_failed:{exc}"},
            limits=["Nessun prodotto SRT1 disponibile: nessuna statistica "
                    "pubblicata (nessun valore inventato)."])
        _atomic_write_json(out_path, payload)
        return payload, None

    slot_payloads = []
    last_raster = None
    last_meta = None
    last_error = None
    for slot_ms in slot_list:
        try:
            path, real_ts, meta = fetch_slot(config, slot_ms, step_s=step_s)
            raster = read_accumulation(path)
            masks = build_area_masks(raster, geo_path, regions_path)
        except (models.SourceError, models.ValidationError,
                AccumulationError) as exc:
            last_error = {"requested_slot": iso_utc(slot_ms),
                          "error": f"{exc.__class__.__name__}:{exc}"}
            continue
        slot_payloads.append(build_slot(raster, real_ts, masks))
        last_raster, last_meta = raster, dict(meta, step_s=step_s)
        last_meta["grid"] = {
            "width": raster.width, "height": raster.height,
            "crs": str(raster.crs),
            "transform": list(raster.transform)[:6],
            "nodata_declared": raster.declared_nodata,
        }
        last_meta["valid_time"] = iso_utc(real_ts)

    if not slot_payloads:
        payload = build_payload(
            [], generated_at=generated_at, as_of_ms=as_of_ms,
            meta=last_error or last_meta or {"reason": "no_slot_fetched"},
            limits=["Nessun prodotto SRT1 scaricabile negli slot richiesti: "
                    "nessuna statistica pubblicata (nessun valore inventato)."])
        _atomic_write_json(out_path, payload)
        return payload, None

    raster_meta = last_meta.pop("grid", None)
    last_meta["slots_requested"] = len(slot_list)
    limits = _default_limits(last_raster)

    validation = None
    if os.path.isfile(stations_path):
        try:
            stations_payload = _val.load_stations(stations_path)
            validation = _val.build_validation_payload(
                stations_payload, last_raster,
                valid_time_ms=slot_payloads[-1]["valid_time_ms"],
                window_s=WINDOW_S, max_stations=max_stations,
                tolerance_s=tolerance_s, generated_at=generated_at)
            _atomic_write_json(val_path, validation, budget_bytes=MAX_OUTPUT_BYTES)
            validation = {
                "file": "accumulation_validation.json",
                "n_pairs": (validation.get("metrics") or {}).get("n"),
                "bias_mm": (validation.get("metrics") or {}).get("bias_mm"),
                "mae_mm": (validation.get("metrics") or {}).get("mae_mm"),
                "pearson_r": (validation.get("metrics") or {}).get("pearson_r"),
            }
        except (AccumulationError, ValueError) as exc:
            validation = {"file": None, "status": "unavailable",
                          "reason": f"{exc.__class__.__name__}:{exc}"}
    else:
        validation = {"file": None, "status": "unavailable",
                      "reason": "stations_file_missing"}

    payload = build_payload(
        slot_payloads, generated_at=generated_at, as_of_ms=as_of_ms,
        meta=last_meta, validation=validation, raster_meta=raster_meta,
        limits=limits)
    size = _atomic_write_json(out_path, payload)
    payload["_output_bytes"] = size
    return payload, last_raster


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Accumulo Radar-DPC SRT1 (mm, 1 h) + centraline")
    ap.add_argument("--out-dir", default=os.path.join("data", "radar"))
    ap.add_argument("--as-of", dest="as_of", default=None, metavar="ISO8601",
                    help="istante UTC di replay (es. 2026-11-13T19:10:00Z)")
    ap.add_argument("--stations", default=DEFAULT_STATIONS)
    ap.add_argument("--geo", default=DEFAULT_GEOJSON)
    ap.add_argument("--regions", default=DEFAULT_REGIONS)
    ap.add_argument("--max-stations", type=int, default=200)
    ap.add_argument("--tolerance-min", type=float, default=45.0)
    ap.add_argument("--slots", type=int, default=1,
                    help="slot consecutivi (passo 5 min) da pubblicare")
    args = ap.parse_args(argv)

    as_of_ms = parse_iso_utc(args.as_of) if args.as_of else None
    payload, _ = run(
        args.out_dir, as_of_ms=as_of_ms, stations_path=args.stations,
        geo_path=args.geo, regions_path=args.regions,
        max_stations=args.max_stations,
        tolerance_s=int(args.tolerance_min * 60), slots=args.slots)
    summary = {
        "status": payload.get("status"),
        "slots": len(payload.get("slots") or []),
        "output_bytes": payload.get("_output_bytes"),
        "validation": payload.get("validation"),
        "fetch": payload.get("fetch"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
