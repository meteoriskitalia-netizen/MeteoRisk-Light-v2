#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — srt1_province_archive.py

ARCHIVIO ORARIO DELL'SRT1 PER PROVINCIA (statistiche, non la griglia).

PERCHE' ESISTE
  Il verificatore (`scripts/verification/risk_verification.py`) ha bisogno, per
  ogni (ora, provincia), della pioggia massima/accumulata nella provincia e
  della copertura (pixel validi / pixel della provincia). Oggi
  `data/radar/accumulation.json` pubblica statistiche per MACRO-AREA: il campo
  `coverage.radar.reason` vale `radar_grid_not_embedded` e la pioggia radar non
  e' utilizzabile provincia per provincia.

  Due strade possibili, misurate entrambe sul prodotto reale (2026-11-23):

  | forma                        | 1 ora    | 24 file/g | 1/2 h (12/g) | anno (24/g) |
  | :--------------------------- | -------: | --------: | -----------: | ----------: |
  | GeoTIFF deflate (slot secco) | 418.712 B| 10,05 MB  |      5,02 MB |   3,58 GiB  |
  | GeoTIFF deflate (convettivo) | 669.077 B| 16,06 MB  |      8,03 MB |   5,21 GiB  |
  | GeoTIFF LZW                  | 519.105 B| 12,46 MB  |      6,23 MB |   4,44 GiB  |
  | **questo archivio**          |   9.461 B|  0,22 MB  |      0,11 MB |   0,08 GiB  |

  L'archivio per provincia costa ~53x meno del GeoTIFF deflate tipico e
  produce ESATTAMENTE il dato che serve al verificatore. Non e' un compromesso:
  e' la forma del dato richiesta. Le griglie intere non vengono archiviate.

COSA NON E'
  Non e' un archivio della griglia: il GeoTIFF SRT1 raw non viene MAI
  pubblicato (regola di `accumulation.py`, invariata). Qui si pubblicano solo
  statistiche derivate per provincia.

ONESTA' DEL DATO
  - `0.0` mm e' assenza di accumulo VALIDA e resta dentro le statistiche;
  - la sentinella (default -9999.0) e i negativi anomali sono FUORI;
  - una provincia interamente fuori copertura radar NON riceve statistiche:
    `valid = 0.0` e i campi `mean`/`max`/`p90` restano `null`. Il verificatore
    la tratta come "nessuna osservazione radar", mai come "0 mm misurati".
    MISURATO (2026-11-23): la copertura CAMBIA da slot a slot — alle 09:30Z del
    2026-11-23 le 9 province siciliane sono tutte sotto 0,5 (8 a 0,0 e ME a
    0,39) mentre lo slot 2026-11-13T19:10Z le copre quasi tutte (solo TP a
    0,41). Per questo la frazione va registrata ora per ora e non assunta.

LICENZA (vincolante)
  Radar-DPC e' CC BY-SA 4.0. Questo archivio e' un'OPERA DERIVATA: le
  statistiche per provincia sono calcolate sul mosaico SRT1 del Dipartimento
  della Protezione Civile. Ogni file pubblico porta `licence`,
  `licence_url`, `attribution` e `derivative`. Il branch dati che ospita
  l'archivio e' PUBBLICO: per il share-alike della CC BY-SA 4.0 i file
  restano distribuiti sotto la stessa licenza, con attribuzione. Il testo
  della licenza accompagna il branch (vedi `LICENCE_FILE` e il README del
  branch scritto da `write_branch_readme`).

Uso:
  python scripts/radar_engine/srt1_province_archive.py --out-dir data/radar/srt1_provinces
  python scripts/radar_engine/srt1_province_archive.py --out-dir . --tif SRT1__x.tif
  python scripts/radar_engine/srt1_province_archive.py --out-dir . --as-of 2026-11-13T19:10:00Z
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile

import numpy as np

# Importabile sia come package sia come script (come fa il workflow).
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from radar_engine import accumulation as _acc  # noqa: E402
else:
    from . import accumulation as _acc  # noqa: E402

SCHEMA_VERSION = 1
ARCHIVE_KIND = "srt1_province_archive"
DEFAULT_OUT_DIR = os.path.join("data", "radar", "srt1_provinces")
DEFAULT_RETENTION_DAYS = 400
# Sotto questa frazione di pixel validi la provincia NON ha una misura radar
# utilizzabile: il verificatore deve dichiarare "nessuna copertura", non 0 mm.
MIN_VALID_FRACTION = 0.5

LICENCE = "CC BY-SA 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/deed.it"
ATTRIBUTION = ("Dati: Radar-DPC — Dipartimento della Protezione Civile "
               "(https://radar.protezionecivile.it), licenza CC BY-SA 4.0")
DERIVATIVE = ("Opera derivata: statistiche per provincia calcolate sul mosaico "
              "SRT1 Radar-DPC. Ridistribuita sotto CC BY-SA 4.0 (share-alike): "
              "attribuzione obbligatoria, i derivati restano CC BY-SA 4.0.")
SOURCE = ("Radar-DPC SRT1 — cumulata di precipitazione 1 h (mm), aggregata per "
          "provincia sui pixel del mosaico radar")

LICENCE_FILE = "LICENCE.md"
README_FILE = "README.md"

_PROVINCE_MASK_CACHE = {}


class ProvinceArchiveError(Exception):
    """Errore dichiarato dell'archivio per provincia (mai un valore inventato)."""


# ---------------------------------------------------------------------------
# Maschere per provincia (stessa griglia del prodotto, rasterizzate)
# ---------------------------------------------------------------------------

def load_province_geometries(geo_path=_acc.DEFAULT_GEOJSON):
    """[(sigla, geometry)] dal GeoJSON delle province. Ordine stabile."""
    if not os.path.isfile(geo_path):
        raise ProvinceArchiveError(f"geojson_missing:{geo_path}")
    try:
        with open(geo_path, encoding="utf-8") as fh:
            geo = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ProvinceArchiveError(f"geojson_unreadable:{exc}") from exc
    out = []
    for feat in geo.get("features") or []:
        sig = (feat.get("properties") or {}).get("SIGLA")
        geom = feat.get("geometry")
        if not sig or geom is None:
            continue
        out.append((str(sig), geom))
    if not out:
        raise ProvinceArchiveError("geojson_no_provinces")
    out.sort(key=lambda item: item[0])
    return out


def build_province_masks(raster, geo_path=_acc.DEFAULT_GEOJSON):
    """dict sigla -> maschera bool sulla griglia del raster.

    Cache per (crs, transform, shape, geo_path): la griglia SRT1 e' costante
    per prodotto, quindi le maschere si rasterizzano UNA volta per processo.
    """
    key = (str(raster.crs), tuple(raster.transform), raster.width,
           raster.height, os.path.abspath(geo_path))
    cached = _PROVINCE_MASK_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from rasterio.features import rasterize
        from rasterio.warp import transform_geom
    except ImportError as exc:  # pragma: no cover - dipende dal runner
        raise ProvinceArchiveError("rasterio_missing") from exc

    pairs = load_province_geometries(geo_path)
    shapes = []
    sigle = []
    for idx, (sig, geom) in enumerate(pairs, start=1):
        try:
            projected = transform_geom("EPSG:4326", raster.crs, geom)
        except Exception as exc:
            raise ProvinceArchiveError(
                f"geometry_transform_failed:{sig}:{exc}") from exc
        shapes.append((projected, idx))
        sigle.append((idx, sig))
    id_raster = rasterize(shapes, out_shape=(raster.height, raster.width),
                          transform=raster.transform, fill=0, dtype="int16",
                          all_touched=False)
    masks = {sig: (id_raster == idx) for idx, sig in sigle}
    _PROVINCE_MASK_CACHE[key] = masks
    return masks


# ---------------------------------------------------------------------------
# Statistiche
# ---------------------------------------------------------------------------

def _frac(num, den):
    return None if not den else round(float(num) / float(den), 4)


def province_stats(raster, mask):
    """Statistiche mm di UNA provincia sui SOLI pixel validi.

    `valid` e' la frazione di pixel della provincia dentro la copertura radar:
    e' la misura che dice se la provincia e' osservata o cieca. Se e' 0 i
    campi statistici sono `null` (nessun valore inventato).
    """
    area_px = int(mask.sum())
    sel = mask & raster.valid_mask
    values = raster.values[sel]
    valid_px = int(values.size)
    out = {
        "px": area_px,
        "vpx": valid_px,
        "valid": _frac(valid_px, area_px),
        "wet": None,
        "mean": None,
        "max": None,
        "p90": None,
    }
    if valid_px:
        wet_px = int((values > 0.0).sum())
        out["wet"] = _frac(wet_px, valid_px)
        out["mean"] = round(float(values.mean()), 4)
        out["max"] = round(float(values.max()), 3)
        out["p90"] = round(float(np.percentile(values, 90.0)), 3)
    return out


def build_slot_payload(raster, valid_time_ms, masks=None, geo_path=None,
                       generated_at=None):
    """Payload completo di UNA ora (busta + 107 province)."""
    masks = masks if masks is not None else build_province_masks(
        raster, geo_path or _acc.DEFAULT_GEOJSON)
    generated_at = generated_at or _acc.iso_utc(int(
        _dt.datetime.now(_dt.timezone.utc).timestamp() * 1000))
    valid = int(raster.valid_px)
    total = int(raster.total_px)
    provinces = {sig: province_stats(raster, mask)
                 for sig, mask in sorted(masks.items())}
    covered = [s for s, st in provinces.items() if st["valid"] is not None
               and st["valid"] >= MIN_VALID_FRACTION]
    maxima = [st["max"] for st in provinces.values() if st["max"] is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": ARCHIVE_KIND,
        "generated_at": generated_at,
        "valid_time": _acc.iso_utc(valid_time_ms),
        "valid_time_ms": int(valid_time_ms),
        "window_h": _acc.WINDOW_H,
        "product": {
            "type": _acc.PRODUCT_TYPE,
            "units": "mm",
            "sentinel_mm": _acc.SENTINEL_MM,
            "zero_is_valid": True,
            "grid": {
                "width": raster.width, "height": raster.height,
                "crs": str(raster.crs),
                "transform": list(raster.transform)[:6],
            },
        },
        "source": SOURCE,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": ATTRIBUTION,
        "derivative": DERIVATIVE,
        "totals": {
            "valid_px": valid,
            "total_px": total,
            "valid_fraction": _frac(valid, total),
            "max_mm": round(float(max(maxima)), 3) if maxima else None,
            "provinces_total": len(provinces),
            "provinces_with_coverage": len(covered),
        },
        "limits": [
            "Statistiche su pixel di griglia radar 1 km con i confini "
            "provinciali rasterizzati sulla griglia: non sono un valore "
            "pluviometrico puntuale.",
            "`valid` e' la frazione di pixel della provincia dentro la "
            "copertura radar; sotto %.1f la provincia non ha una misura "
            "utilizzabile." % MIN_VALID_FRACTION,
            "0.0 mm e' assenza di accumulo valida; %.1f mm e' fuori "
            "copertura ed e' escluso." % _acc.SENTINEL_MM,
        ],
        "provinces": provinces,
    }


def archive_path(out_dir, valid_time_ms):
    """Percorso `out_dir/YYYY/MM/DD/HH.json` in UTC."""
    d = _dt.datetime.fromtimestamp(valid_time_ms / 1000.0, _dt.timezone.utc)
    return os.path.join(out_dir, d.strftime("%Y"), d.strftime("%m"),
                        d.strftime("%d"), d.strftime("%H") + ".json")


def write_hour(out_dir, payload, budget_bytes=200 * 1024):
    """Scrittura atomica dell'ora. Ritorna (path, byte). Budget dichiarato."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=False)
    size = len(text.encode("utf-8"))
    if size >= int(budget_bytes):
        raise ProvinceArchiveError(
            f"output_budget_exceeded:{size}>={int(budget_bytes)}")
    path = archive_path(out_dir, int(payload["valid_time_ms"]))
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".srt1p-", suffix=".tmp")
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
        raise ProvinceArchiveError(f"atomic_write_failed:{exc}") from exc
    return path, size


# ---------------------------------------------------------------------------
# Retention + testo di licenza del branch
# ---------------------------------------------------------------------------

def prune_archive(out_dir, keep_days=DEFAULT_RETENTION_DAYS, now_ms=None):
    """Rimuove le ore piu' vecchie di `keep_days`. Ritorna (rimossi, tenuti).

    Percorre solo `YYYY/MM/DD/HH.json`: nessun altro file viene toccato.
    """
    out_dir = str(out_dir)
    if not os.path.isdir(out_dir):
        return 0, 0
    now_ms = int(now_ms if now_ms is not None else
                 _dt.datetime.now(_dt.timezone.utc).timestamp() * 1000)
    cutoff = now_ms - int(keep_days) * 86400 * 1000
    removed = 0
    kept = 0
    for name in sorted(os.listdir(out_dir)):
        year_dir = os.path.join(out_dir, name)
        if not (len(name) == 4 and name.isdigit() and os.path.isdir(year_dir)):
            continue
        for month in sorted(os.listdir(year_dir)):
            month_dir = os.path.join(year_dir, month)
            if not (len(month) == 2 and month.isdigit()
                    and os.path.isdir(month_dir)):
                continue
            for day in sorted(os.listdir(month_dir)):
                day_dir = os.path.join(month_dir, day)
                if not (len(day) == 2 and day.isdigit()
                        and os.path.isdir(day_dir)):
                    continue
                for hour in sorted(os.listdir(day_dir)):
                    hour_path = os.path.join(day_dir, hour)
                    stem, ext = os.path.splitext(hour)
                    if not (ext == ".json" and len(stem) == 2
                            and stem.isdigit()):
                        continue
                    try:
                        stamp = _dt.datetime(int(name), int(month), int(day),
                                             int(stem), tzinfo=_dt.timezone.utc)
                    except ValueError:
                        continue
                    if stamp.timestamp() * 1000 < cutoff:
                        try:
                            os.unlink(hour_path)
                            removed += 1
                        except OSError:
                            pass
                    else:
                        kept += 1
                try:
                    os.rmdir(day_dir)
                except OSError:
                    pass
            try:
                os.rmdir(month_dir)
            except OSError:
                pass
        try:
            os.rmdir(year_dir)
        except OSError:
            pass
    return removed, kept


def branch_readme_text():
    """README + attribuzione che accompagna il branch pubblico dell'archivio."""
    return (
        "# Archivio SRT1 per provincia (opera derivata)\n"
        "\n"
        "Statistiche orarie di cumulata di precipitazione (mm, finestra 1 h) per\n"
        "provincia, calcolate sul mosaico **SRT1** di **Radar-DPC — Dipartimento\n"
        "della Protezione Civile** (https://radar.protezionecivile.it).\n"
        "\n"
        "## Licenza\n"
        "\n"
        "- Dati di origine: Radar-DPC, **CC BY-SA 4.0**.\n"
        "- Questo archivio e' un'**opera derivata** e viene ridistribuito sotto la\n"
        "  stessa licenza **CC BY-SA 4.0** (share-alike), con attribuzione.\n"
        "- Testo integrale: https://creativecommons.org/licenses/by-sa/4.0/deed.it\n"
        "- Nessuna modifica e' applicata ai valori; sono aggregate statistiche\n"
        "  (media, massimo, p90, frazione bagnata, copertura) su pixel di griglia\n"
        "  radar 1 km con i confini provinciali rasterizzati.\n"
        "\n"
        "## Formato\n"
        "\n"
        "`YYYY/MM/DD/HH.json` in UTC, una ora per file. Chiavi principali:\n"
        "`valid_time`, `totals`, `provinces` (per sigla). Per ogni provincia:\n"
        "`mean`, `max`, `p90` (mm), `wet` (frazione pixel > 0 su pixel validi),\n"
        "`valid` (frazione di pixel della provincia dentro la copertura radar),\n"
        "`px` (pixel della provincia), `vpx` (pixel validi).\n"
        "\n"
        "`valid` = 0.0 significa **provincia non coperta dal radar**. La\n"
        "copertura CAMBIA da slot a slot (misurato: 2026-11-23T09:30Z -> 9\n"
        "province siciliane sotto 0,5; 2026-11-13T19:10Z -> solo TP a 0,41):\n"
        "non e' \"0 mm misurati\" e non va assunta costante.\n"
        "\n"
        "Attribuzione: " + ATTRIBUTION + "\n"
    )


def ensure_branch_docs(out_dir):
    """Scrive README + LICENCE accanto all'archivio (idempotente)."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, README_FILE), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(branch_readme_text())
    with open(os.path.join(out_dir, LICENCE_FILE), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(
            "# Licenza — CC BY-SA 4.0\n\n"
            "Questo archivio e' un'opera derivata dai prodotti Radar-DPC del\n"
            "Dipartimento della Protezione Civile Italiano, distribuiti sotto\n"
            "Creative Commons Attribuzione — Condividi allo stesso modo 4.0\n"
            "Internazionale (CC BY-SA 4.0).\n\n"
            "Testo completo: " + LICENCE_URL + "\n\n"
            "Attribuzione: " + ATTRIBUTION + "\n\n"
            "Il branch che ospita questi file e' pubblico: il share-alike della\n"
            "licenza impone che i derivati restino CC BY-SA 4.0 con la stessa\n"
            "attribuzione. Nessuna restrizione aggiuntiva viene applicata.\n")


# ---------------------------------------------------------------------------
# Orchestrazione: da raster gia' in memoria, da file, o da rete
# ---------------------------------------------------------------------------

def archive_raster(raster, valid_time_ms, out_dir=DEFAULT_OUT_DIR,
                   geo_path=_acc.DEFAULT_GEOJSON, generated_at=None):
    """Archivia UNA ora partendo da un raster gia' letto. Ritorna il payload."""
    payload = build_slot_payload(raster, valid_time_ms, geo_path=geo_path,
                                 generated_at=generated_at)
    _, size = write_hour(out_dir, payload)
    payload["_output_bytes"] = size
    payload["_path"] = archive_path(out_dir, valid_time_ms)
    return payload


def run(out_dir, as_of_ms=None, tif_path=None, geo_path=_acc.DEFAULT_GEOJSON,
        config=None, retention_days=DEFAULT_RETENTION_DAYS,
        generated_at=None):
    """Fetch (o file) + statistiche per provincia + scrittura.

    Un prodotto non disponibile NON e' un crash: si ritorna
    `{"status": "unavailable", "reason": ...}` e si esce 0 (il run radar
    prosegue; l'archivio e' additivo).
    """
    config = config or {}
    if tif_path:
        raster = _acc.read_accumulation(tif_path)
        # Il tempo VALIDO e' lo slot dell'accumulo, non il tag di pubblicazione
        # del TIFF (TIFFTAG_DATETIME e' ~+6-7 min: misurato 19:16:47 per uno
        # slot 19:10). Se il chiamante passa `--as-of`, quello vince.
        valid_time_ms = int(as_of_ms) if as_of_ms is not None else None
        if valid_time_ms is None:
            try:
                import rasterio
                with rasterio.open(tif_path) as ds:
                    dt = (ds.tags() or {}).get("TIFFTAG_DATETIME")
                if dt:
                    base = _dt.datetime.strptime(dt, "%Y:%m:%d %H:%M:%S")
                    valid_time_ms = int(base.replace(
                        tzinfo=_dt.timezone.utc).timestamp() * 1000)
            except Exception:
                valid_time_ms = None
        if valid_time_ms is None:
            raise ProvinceArchiveError(
                "valid_time_unknown:passa --as-of insieme a --tif")
        payload = archive_raster(raster, valid_time_ms, out_dir, geo_path,
                                 generated_at)
        payload["_source"] = "tif"
    else:
        try:
            slots, step_s = _acc.resolve_slots(config, as_of_ms=as_of_ms,
                                               count=1)
            path, real_ts, meta = _acc.fetch_slot(config, slots[-1],
                                                  step_s=step_s)
            raster = _acc.read_accumulation(path)
        except (_acc.AccumulationError, _acc.models.SourceError,
                _acc.models.ValidationError) as exc:
            return {"status": "unavailable",
                    "reason": f"{exc.__class__.__name__}:{exc}"}
        payload = archive_raster(raster, real_ts, out_dir, geo_path,
                                 generated_at)
        payload["_source"] = "dpc"
        payload["_fetch"] = meta

    payload["status"] = "ok"
    removed, kept = prune_archive(out_dir, retention_days)
    payload["_retention"] = {"removed": removed, "kept": kept,
                             "keep_days": int(retention_days)}
    try:
        ensure_branch_docs(out_dir)
    except OSError:
        pass
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Archivio orario SRT1 per provincia (statistiche, non griglia)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--as-of", dest="as_of", default=None, metavar="ISO8601",
                    help="istante UTC di replay (es. 2026-11-13T19:10:00Z)")
    ap.add_argument("--tif", dest="tif_path", default=None,
                    help="GeoTIFF SRT1 gia' scaricato (richiede --as-of se il "
                         "tag TIFFTAG_DATETIME manca)")
    ap.add_argument("--geo", default=_acc.DEFAULT_GEOJSON)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    args = ap.parse_args(argv)

    config = {}
    if args.cache_dir:
        config = {"source": {"cache_dir": args.cache_dir}}
    as_of_ms = _acc.parse_iso_utc(args.as_of) if args.as_of else None
    payload = run(args.out_dir, as_of_ms=as_of_ms, tif_path=args.tif_path,
                  geo_path=args.geo, config=config,
                  retention_days=args.retention_days)
    summary = {
        "status": payload.get("status"),
        "valid_time": payload.get("valid_time"),
        "path": payload.get("_path"),
        "output_bytes": payload.get("_output_bytes"),
        "provinces": (payload.get("totals") or {}).get("provinces_total"),
        "provinces_with_coverage": (payload.get("totals") or {}).get(
            "provinces_with_coverage"),
        "retention": payload.get("_retention"),
        "reason": payload.get("reason"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
