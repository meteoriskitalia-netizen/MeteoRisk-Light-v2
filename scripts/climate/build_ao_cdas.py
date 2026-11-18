#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Produttore dell'indice AO CDAS OSSERVATO (NOAA CPC) — sola stdlib.

Pubblica ``data/climate/ao_cdas.json`` con:
  * schema, generated_at, fonte NOAA CPC, licenza public domain con
    attribuzione obbligatoria, baseline 1979-2000;
  * la serie giornaliera fino a ieri (UTC), senza valori inventati;
  * ``profile_10hpa`` con ``null_reason`` esplicito: il profilo 17 livelli di
    PSL NCEP/NCAR R1 si ferma al 2026-03-17 (246 giorni di ritardo misurati
    il 2026-11-18), quindi NON e' una serie corrente.

REGOLA #237 / niente artefatti:
  - nessun valore e' interpolato, estrapolato o preso da un PNG;
  - se il CSV non e' scaricabile si pubblica ``status: producer_failed`` con
    ``null_reason`` e ``series: []``, non un numero finto;
  - l'indice si chiama AO (o ``ao_cdas_daily``), MAI "NAM": il vero NAM
    (Thompson & Wallace) e' l'EOF1 emisferica e non e' calcolabile da questo
    sottoinsieme.

Rete: una sola GET del CSV, con cache locale opzionale (``--cache``).
Uso:
    python -m scripts.climate.build_ao_cdas --out data/climate/ao_cdas.json
    python -m scripts.climate.build_ao_cdas --out out.json --csv fixture.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

SCHEMA_VERSION = 1
SOURCE = "NOAA Climate Prediction Center (CPC)"
SOURCE_URL = ("https://ftp.cpc.ncep.noaa.gov/cwlinks/"
              "norm.daily.ao.cdas.z1000.19500101_current.csv")
LICENSE = ("NOAA public domain (17 USC 105 / NWS disclaimer) — "
           "attribution required")
ATTRIBUTION = ("NOAA Climate Prediction Center — AO CDAS daily index "
               "(1000 mb Z), https://www.cpc.ncep.noaa.gov/products/precip/"
               "CWlink/daily_ao_index/ao.shtml")
BASELINE = "1979-2000"
UNIT = "standardized index"
UA = "MeteoRisk-Light AO CDAS producer/1.0 (public NOAA data)"
DEFAULT_CACHE_MAX_AGE_H = 6.0

# Rianalisi storica PSL: ultimo giorno misurato il 2026-11-18.
PSL_LAST_AVAILABLE = "2026-03-17"
PSL_NULL_REASON = "psl_r1_dailyavgs_stale_since_2026-03-17"
PROFILE_LABEL = ("Profilo 17 livelli 1000->10 hPa — fino al 17/03/2026 — "
                 "rianalisi, non corrente")


class SourceError(RuntimeError):
    """Errore di rete/HTTP sulla sorgente: contiene URL e motivo esatto."""


def utc_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()


def parse_ao_csv(text: str, today: date | None = None):
    """Parsa il CSV CPC. Ritorna (points, meta).

    ``points`` e' una lista ``[[epochMs, value], ...]`` ordinata e senza
    duplicati. Vengono scartate: righe con dati mancanti/invalidi, righe con
    data nel futuro e righe con data successiva a ieri (UTC). Nessun valore
    viene inventato per colmare i giorni mancanti.
    """
    today = today or utc_today()
    yesterday = today - timedelta(days=1)
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    if not {"year", "month", "day", "ao_index_cdas"} <= fields:
        raise SourceError("CSV AO: header inatteso %r" % (reader.fieldnames,))
    by_ts = {}
    stats = {"rows": 0, "kept": 0, "dropped_bad": 0,
             "dropped_future": 0, "duplicates": 0}
    for row in reader:
        stats["rows"] += 1
        try:
            d = date(int(row["year"]), int(row["month"]), int(row["day"]))
            v = float(row["ao_index_cdas"])
        except (ValueError, TypeError, KeyError):
            stats["dropped_bad"] += 1
            continue
        if d > yesterday:
            stats["dropped_future"] += 1
            continue
        if v <= -99.0:  # sentinel CPC (-99.9 / -999.9), come nel produttore teleconn
            stats["dropped_bad"] += 1
            continue
        ts = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        if ts in by_ts:
            stats["duplicates"] += 1
        by_ts[ts] = v
        stats["kept"] += 1
    points = [[ts, by_ts[ts]] for ts in sorted(by_ts)]
    meta = {
        "last_observation_date": (
            datetime.fromtimestamp(points[-1][0] / 1000, timezone.utc).date().isoformat()
            if points else None),
        "today_utc": today.isoformat(),
        "cutoff_yesterday_utc": yesterday.isoformat(),
        "stats": stats,
    }
    return points, meta


def profile_block() -> dict:
    """Il profilo 10 hPa NON e' corrente: stato esplicito, nessun valore."""
    return {
        "status": "STORICO_FINO_2026-03-17",
        "label": PROFILE_LABEL,
        "last_available_date": PSL_LAST_AVAILABLE,
        "null_reason": PSL_NULL_REASON,
        "value": None,
        "values": None,
        "source": "NOAA PSL NCEP/NCAR Reanalysis 1 dailyavgs (OPeNDAP)",
        "source_url": ("https://psl.noaa.gov/thredds/dodsC/Datasets/"
                       "ncep.reanalysis.dailyavgs/pressure/hgt.2026.nc.html"),
        "license": "NOAA public domain with mandatory attribution (PSL + NWS)",
        "attribution": ("NOAA PSL, Boulder, Colorado, USA — "
                        "https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.html"),
        "baseline": BASELINE,
        "detail": ("Il profilo 17 livelli non si scarica fino a ieri senza token: "
                   "CPC pubblica solo il PNG e PSL e' fermo al 2026-03-17. "
                   "Nessun proxy da Z500/Z1000 e' stato sostituito al profilo."),
    }


def build_payload(points, meta, generated_at, status="ok",
                  null_reason=None, reason=None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "ao_cdas_observed",
        "generated_at": generated_at,
        "status": status,
        "null_reason": null_reason,
        "reason": reason,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "baseline": BASELINE,
        "unit": UNIT,
        "label": "Arctic Oscillation — indice OSSERVATO CDAS a 1000 mb",
        "display_name": "AO osservato (CDAS, 1000 mb)",
        "is_forecast": False,
        "not_named_nam": True,
        "series": points,
        "last_observation_date": meta.get("last_observation_date"),
        "cutoff_yesterday_utc": meta.get("cutoff_yesterday_utc"),
        "parse_stats": meta.get("stats", {}),
        "profile_10hpa": profile_block(),
        "not_implemented": [{
            "id": "nam_10hpa",
            "status": "RICHIEDE TOKEN / FONTE_NON_NUMERICA",
            "reason": ("Il vero NAM e' l'EOF1 emisferica di Z poleward of 20N: "
                       "non calcolabile dal solo cappello 65-90N ne' dal PNG CPC. "
                       "Nessun indice viene chiamato 'NAM' in questo prodotto."),
        }],
    }


def fetch_csv(url: str, timeout: float, cache_path: str | None = None,
              max_age_h: float = DEFAULT_CACHE_MAX_AGE_H) -> tuple[str, bool]:
    """Scarica il CSV; se ``cache_path`` e' fresco lo riusa. Ritorna (testo, from_cache)."""
    if cache_path and os.path.isfile(cache_path):
        age_h = (time.time() - os.path.getmtime(cache_path)) / 3600.0
        if age_h <= max_age_h:
            with open(cache_path, "r", encoding="utf-8") as fh:
                return fh.read(), True
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise SourceError("HTTP %s su %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise SourceError("URL error su %s: %s" % (url, exc.reason)) from exc
    text = raw.decode("utf-8", errors="replace")
    if cache_path:
        tmp = cache_path + ".tmp"
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, cache_path)
    return text, False


def write_json(path: str, payload: dict) -> int:
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return len(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/climate/ao_cdas.json")
    ap.add_argument("--url", default=SOURCE_URL)
    ap.add_argument("--csv", help="file CSV locale (test/offline)")
    ap.add_argument("--cache", help="cache locale del CSV grezzo (default: disabilitata)")
    ap.add_argument("--cache-max-age-hours", type=float, default=DEFAULT_CACHE_MAX_AGE_H)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--today", help="YYYY-MM-DD (test): default oggi UTC")
    args = ap.parse_args(argv)

    now = (datetime.fromisoformat(args.today + "T00:00:00+00:00")
           if args.today else datetime.now(timezone.utc))
    generated_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    today = utc_today(now)

    try:
        if args.csv:
            with open(args.csv, "r", encoding="utf-8-sig") as fh:
                text = fh.read()
            from_cache = True
        else:
            text, from_cache = fetch_csv(args.url, args.timeout, args.cache,
                                         args.cache_max_age_hours)
        points, meta = parse_ao_csv(text, today)
        if not points:
            payload = build_payload(
                [], meta, generated_at, status="no_observation",
                null_reason="ao_cdas_csv_no_valid_row_up_to_yesterday",
                reason="Il CSV non contiene righe valide fino a ieri (UTC).")
        else:
            payload = build_payload(points, meta, generated_at)
    except (SourceError, OSError, ValueError) as exc:
        payload = build_payload(
            [], {"last_observation_date": None,
                 "cutoff_yesterday_utc": (today - timedelta(days=1)).isoformat(),
                 "stats": {}},
            generated_at, status="producer_failed",
            null_reason="cpc_cdas_ao_unavailable",
            reason=("CSV AO CDAS non scaricabile o non parsabile: %s. "
                    "Nessun valore inventato.") % exc)
        print("ATTENZIONE: %s" % exc, file=sys.stderr)

    payload["csv_cache_used"] = bool(locals().get("from_cache", False))
    nbytes = write_json(args.out, payload)
    print("scritto %s — %d byte, %d punti, status=%s, ultimo=%s" %
          (args.out, nbytes, len(payload["series"]), payload["status"],
           payload["last_observation_date"]))
    return 0 if payload["status"] in ("ok", "no_observation") else 2


if __name__ == "__main__":
    raise SystemExit(main())
