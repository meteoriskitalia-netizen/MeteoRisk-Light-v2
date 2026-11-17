#!/usr/bin/env python3
"""Costruisce data/stations/daily_series.json da data/stations/latest.json.

REGOLA (brief 17/11/2026): lo storico del giorno va scritto LATO SERVER, non nel
browser. Il workflow stations.yml gira ogni 10 minuti e riscrive UN SOLO BLOB VIVO:

    data/stations/daily_series.json

  * ``day`` e' la giornata LOCALE Europe/Rome del run (non UTC, non quella del browser).
  * Se il ``day`` del file su disco e' diverso -> si AZZERA (nuovo giorno locale).
  * Altrimenti si aggancia ogni osservazione di latest.json, con DEDUP per
    ``obs_time`` (stesso istante -> sostituisci la tupla, non aggiungere).
  * Un solo blob vivo, sovrascritto a ogni run: nessuno storico voluto nel repo.
    NOTA DICHIARATA: se il workflow commotta su `main`, il vecchio blob resta
    raggiungibile nella storia Git di `main`; la gc lo recupera. Per non averlo
    affatto serve una ref a push incrementale/orphan (NON fatto qui: il commit e'
    gestito dal workflow esistente, che questo script non tocca).

SCHEMA (client: stationsSeriesTuple / stationsSeriesValue):

    {
      "schema": "stations-daily-series/1",
      "generated_at": "2026-11-18T07:00:00Z",
      "day": "2026-11-18",
      "timezone": "Europe/Rome",
      "note": "...",
      "max_points_per_station": 288,
      "stations": {
        "<id>": {
          "name": "Roma Test",
          "pts": [[epochMs, temp, rh, wind, dir, rain_mm_h, period_s,
                   dew, stationPressure_hPa, gust, snowDepth_m], ...]
        }
      }
    }

Indici della tupla (NON riordinarli: il client li usa):
  0 epochMs, 1 temp, 2 rh, 3 wind, 4 dir, 5 rain mm/h, 6 period s,
  7 dew, 8 pressione di STAZIONE (non MSL), 9 raffica, 10 neve al suolo.

Solo stdlib. Uso zoneinfo (stdlib) con fallback manuale CET/CEST se tzdata manca.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    _ROME = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover - host senza tzdata
    _ROME = None

SCHEMA = "stations-daily-series/1"
TZ_NAME = "Europe/Rome"
MAX_POINTS_PER_STATION = 288
NOTE = (
    "Serie giornaliera osservata (Europe/Rome). Un solo blob vivo riscritto a ogni "
    "run del workflow stations.yml; azzerato a mezzanotte locale; dedup per obs_time. "
    "Nessun punto passato viene inventato."
)

# Indici tupla — allineati a stationsSeriesTuple()/stationsSeriesValue() nel client.
I_TIME, I_TEMP, I_RH, I_WIND, I_DIR, I_RAIN, I_PERIOD, I_DEW, I_PRESS, I_GUST, I_SNOW = range(11)
TUPLE_LEN = 11


# --------------------------------------------------------------------------- #
# Europe/Rome: giorno locale da epoch ms
# --------------------------------------------------------------------------- #
def _last_sunday(year: int, month: int) -> datetime:
    """Ultima domenica del mese, a mezzanotte (naive)."""
    if month == 12:
        d = datetime(year + 1, 1, 1)
    else:
        d = datetime(year, month + 1, 1)
    d -= timedelta(days=1)
    while d.weekday() != 6:  # 6 = domenica
        d -= timedelta(days=1)
    return d


def _rome_offset_hours(utc_naive: datetime) -> int:
    """Offset EU/CEST: ultima domenica di marzo 01:00Z -> +2, ottobre 01:00Z -> +1."""
    y = utc_naive.year
    start = _last_sunday(y, 3).replace(hour=1)
    end = _last_sunday(y, 10).replace(hour=1)
    return 2 if start <= utc_naive < end else 1


def rome_day(epoch_ms: int) -> str:
    """Data LOCALE Europe/Rome (YYYY-MM-DD) di un epoch ms."""
    utc_dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    if _ROME is not None:
        return utc_dt.astimezone(_ROME).strftime("%Y-%m-%d")
    # Fallback stdlib-only senza tzdata.
    local = utc_dt.replace(tzinfo=None) + timedelta(hours=_rome_offset_hours(utc_dt.replace(tzinfo=None)))
    return local.strftime("%Y-%m-%d")


def local_day_bounds(day: str) -> tuple[int, int]:
    """[00:00, 00:00 del giorno dopo) locali, in epoch ms. Calcolato UNA volta
    per run: chiamare rome_day() per ogni punto salvato sarebbe O(n^2) e could
    rendere il workflow troppo lento con ~4.000 stazioni x 144 punti."""
    y, mo, dd = (int(x) for x in day.split("-"))
    if _ROME is not None:
        start = datetime(y, mo, dd, tzinfo=_ROME)
        end = start + timedelta(days=1)
    else:
        naive = datetime(y, mo, dd)
        nxt = naive + timedelta(days=1)
        start = naive.replace(tzinfo=timezone(timedelta(hours=_rome_offset_hours(naive))))
        end = nxt.replace(tzinfo=timezone(timedelta(hours=_rome_offset_hours(nxt))))
    return int(round(start.timestamp() * 1000)), int(round(end.timestamp() * 1000))


def parse_obs_ms(value: object) -> int | None:
    """'2026-11-17T13:07:59Z' (o ISO con offset) -> epoch ms UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return int(round(dt.timestamp() * 1000))


def iso_utc(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Tupla
# --------------------------------------------------------------------------- #
def _num(value: object, decimals: int = 2) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, decimals)


def tuple_for(station: dict, obs_ms: int) -> list:
    """Una riga [epochMs, ...] da un record di latest.json."""
    precip = _num(station.get("precipitation"))
    period = _num(station.get("precipitation_period_s"), 0)
    rain = None
    if precip is not None and period is not None and period > 0:
        rain = round(precip * 3600.0 / period, 2)
    return [
        obs_ms,
        _num(station.get("temperature_2m")),
        _num(station.get("relative_humidity_2m")),
        _num(station.get("wind_speed_10m")),
        _num(station.get("wind_direction_10m")),
        rain,
        int(period) if period is not None else None,
        _num(station.get("dew_point_2m")),
        _num(station.get("station_pressure_hPa")),
        _num(station.get("wind_gust_10m")),
        _num(station.get("snow_depth_m"), 3),
    ]


def _valid_point(p: object) -> bool:
    """Solo forma: il controllo del giorno e' per range (vedi local_day_bounds)."""
    return (
        isinstance(p, list)
        and len(p) == TUPLE_LEN
        and isinstance(p[I_TIME], int)
    )


# --------------------------------------------------------------------------- #
# Aggiornamento del blob
# --------------------------------------------------------------------------- #
def build(latest: dict, previous: dict | None, now_ms: int, max_points: int = MAX_POINTS_PER_STATION) -> dict:
    """Restituisce il nuovo documento. `previous` e' il file su disco (o None)."""
    day = rome_day(now_ms)
    lo, hi = local_day_bounds(day)
    prev_day = previous.get("day") if isinstance(previous, dict) else None
    prev_stations = previous.get("stations", {}) if isinstance(previous, dict) else {}
    if not isinstance(prev_stations, dict):
        prev_stations = {}

    stations: dict[str, dict] = {}
    if prev_day == day:
        # Conserva le serie delle stazioni gia' presenti (anche quelle che questo
        # run non ripubblica: il loro punto di oggi non deve sparire).
        for sid, rec in prev_stations.items():
            if not isinstance(rec, dict) or not isinstance(rec.get("pts"), list):
                continue
            pts = [p for p in rec["pts"] if _valid_point(p) and lo <= p[I_TIME] < hi]
            if pts:
                stations[sid] = {"name": str(rec.get("name") or ""), "pts": pts}

    latest_stations = latest.get("stations", [])
    if not isinstance(latest_stations, list):
        latest_stations = []

    added = 0
    replaced = 0
    for st in latest_stations:
        if not isinstance(st, dict):
            continue
        sid = st.get("id")
        if not isinstance(sid, str) or not sid:
            continue
        obs_ms = parse_obs_ms(st.get("obs_time"))
        if obs_ms is None:
            continue
        # Solo osservazioni della giornata corrente: niente punto di ieri nel blob di oggi.
        # Range precalcolato (equivalente a rome_day(obs_ms) == day, ma O(1)).
        if not (lo <= obs_ms < hi):
            continue
        rec = stations.get(sid)
        if rec is None:
            rec = {"name": str(st.get("name") or ""), "pts": []}
            stations[sid] = rec
        elif not rec.get("name") and st.get("name"):
            rec["name"] = str(st["name"])
        pts = rec["pts"]
        new_tuple = tuple_for(st, obs_ms)
        hit = -1
        for i, p in enumerate(pts):
            if p[I_TIME] == obs_ms:
                hit = i
                break
        if hit >= 0:
            pts[hit] = new_tuple          # dedup per obs_time: sostituisci
            replaced += 1
        else:
            pts.append(new_tuple)
            added += 1
        pts.sort(key=lambda p: p[I_TIME])
        if len(pts) > max_points:
            del pts[: len(pts) - max_points]

    return {
        "schema": SCHEMA,
        "generated_at": iso_utc(now_ms),
        "day": day,
        "timezone": TZ_NAME,
        "note": NOTE,
        "max_points_per_station": max_points,
        "stations": stations,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--latest", required=True, help="data/stations/latest.json")
    ap.add_argument("--out", required=True, help="data/stations/daily_series.json")
    ap.add_argument("--now-ms", type=int, default=None,
                    help="epoch ms del run (SOLO per test/clock simulato); default = adesso")
    ap.add_argument("--max-points", type=int, default=MAX_POINTS_PER_STATION)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    now_ms = args.now_ms if args.now_ms is not None else int(round(time.time() * 1000))
    latest_path = Path(args.latest)
    out_path = Path(args.out)

    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] latest.json non leggibile: {exc}", file=sys.stderr)
        return 3
    if not isinstance(latest, dict) or not isinstance(latest.get("stations"), list) or not latest["stations"]:
        print("[ERROR] latest.json senza stazioni: non sovrascrivo il blob buono.", file=sys.stderr)
        return 4

    previous = None
    if out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            previous = None  # blob corrotto: riparte come giorno nuovo

    doc = build(latest, previous, now_ms, args.max_points)
    payload = json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(out_path)

    if not args.quiet:
        pts = sum(len(r.get("pts", [])) for r in doc["stations"].values())
        print(f"[OK] day={doc['day']} stations={len(doc['stations'])} points={pts} "
              f"bytes={len(payload.encode('utf-8'))} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
