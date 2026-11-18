#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Produttore di ``data/radar/synoptic.json`` per MeteoRisk-Light-v2 (client 1.3.0.8).

Legge i frame .om REALI gia' pubblicati sui branch fm2-* (nessun download di nuovi
domini), calcola:

  * ``block`` — SOLO indice Tibaldi--Molteni 1990, soglia citata;
  * ``raw_field`` — tutti gli altri campi, senza soglia calibrata.

Nessun dato simulato. Nessun token. Nessuna scrittura fuori da ``--out``.

Uso (produzione):
    python build_synoptic.py --out data/radar/synoptic.json --domain ecmwf_ifs025

Uso (test con fixture reale ridotta, tutto locale):
    python build_synoptic.py --out out.json \
        --manifest fixtures/latest_reduced.json --frame fixtures/gfs025_reduced.om \
        --generated-at 2026-11-18T00:00:00Z
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from . import raw_fields, tm90
from .grid import array_names, read_array, read_scalar
from .om_http import RangeHttpFS, open_frame

SCHEMA_VERSION = 1
SOURCE = "open-meteo .om"
LICENSE = "CC BY 4.0"
WINDOW_HOURS_MAX = 168
RAW_BASE = "https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads"

# I 7 domini gia' prodotti; nessun dominio nuovo viene scaricato.
DOMAIN_BRANCH = {
    "dwd_icon": "fm2-dwdicon",
    "dwd_icon_eu": "fm2-dwd",
    "dwd_icon_d2": "fm2-dwd",
    "ecmwf_ifs025": "fm2-ecmwf",
    "ncep_gfs025": "fm2-ncep",
    "ncep_gfs013": "fm2-ncep",
    "meteofrance_arpege_world025": "fm2-meteofrance",
}
REQUIRED_FIELDS = (
    "geopotential_height_500hPa",
    "temperature_850hPa",
    "temperature_500hPa",
    "relative_humidity_850hPa",
    "wind_speed_850hPa",
    "wind_direction_850hPa",
)


def iso(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_manifest(url: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "MeteoRisk-Light synoptic producer/1.0")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def load_manifest(path_or_url: str) -> dict:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return fetch_manifest(path_or_url)
    with open(path_or_url, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def select_frame(manifest: dict, generated_at: datetime, window_hours: int):
    ref = iso(manifest["reference_time"])
    frames = []
    for f in manifest.get("frames") or []:
        vt = iso(f["valid_time"])
        if ref <= vt <= ref + timedelta(hours=window_hours):
            frames.append((vt, f))
    if not frames:
        return None, True, ref, None
    frames.sort(key=lambda x: x[0])
    target = generated_at
    chosen = min(frames, key=lambda x: abs((x[0] - target).total_seconds()))
    last = frames[-1][0]
    exhausted = (ref + timedelta(hours=WINDOW_HOURS_MAX)) > last
    return chosen[1], exhausted, ref, chosen[0]


def build(manifest: dict, frame_spec: str, generated_at: datetime,
          window_hours: int = WINDOW_HOURS_MAX, domain: str | None = None) -> dict:
    frame, exhausted, ref, valid = select_frame(manifest, generated_at, window_hours)
    domain = domain or manifest.get("domain") or "unknown"
    grid_raw = manifest.get("grid") or {}
    grid = {
        "nx": int(grid_raw["nx"]), "ny": int(grid_raw["ny"]),
        "lonMin": float(grid_raw["lonMin"]), "latMin": float(grid_raw["latMin"]),
        "dx": float(grid_raw["dx"]), "dy": float(grid_raw["dy"]),
    }

    features = []
    missing = []
    if frame is None:
        features.append({
            "id": "horizon_exhausted", "kind": "raw_field", "geometry": [],
            "label": "campo grezzo: finestra temporale non coperta dal run",
            "confidence": "uncalibrated", "reference": None, "fields": {},
            "null_reason": f"horizon_exhausted: run {z(ref)} + {window_hours}h "
                           f"beyond available frames; no extrapolation",
        })
        exhausted = True
        valid = None
    else:
        fs = RangeHttpFS() if frame_spec.startswith(("http://", "https://")) else None
        reader, _ = open_frame(frame_spec, fs)
        try:
            present = set(array_names(reader))
            arrays = {}
            for name in REQUIRED_FIELDS + ("wind_direction_850hPa",):
                if name in present:
                    arrays[name] = read_array(reader, name)
            missing = sorted(n for n in REQUIRED_FIELDS if n not in present)
            # Coerenza: il valid time del frame e' quello del manifest.
            fvt = iso(frame["valid_time"])
            if valid is not None and fvt != valid:
                raise ValueError(f"valid_time mismatch: manifest {z(valid)} vs frame {z(fvt)}")
            valid = fvt

            if "geopotential_height_500hPa" in arrays:
                try:
                    features.extend(tm90.compute(arrays["geopotential_height_500hPa"], grid))
                except tm90.NotComputable as exc:
                    missing.append("geopotential_height_500hPa")
                    features.append({
                        "id": "tm90_not_computable", "kind": "raw_field", "geometry": [],
                        "label": "campo grezzo Z500 — blocco TM90 non calcolabile",
                        "confidence": "uncalibrated", "reference": None,
                        "fields": {"reason": str(exc)},
                        "null_reason": str(exc),
                    })
            else:
                features.append({
                    "id": "tm90_no_z500", "kind": "raw_field", "geometry": [],
                    "label": "campo grezzo Z500 assente — blocco TM90 non calcolabile",
                    "confidence": "uncalibrated", "reference": None, "fields": {},
                    "null_reason": "geopotential_height_500hPa_missing",
                })

            raw, raw_missing = raw_fields.compute(arrays, grid)
            features.extend(raw)
            missing = sorted(set(missing + raw_missing))
        finally:
            try:
                reader.close()
            except Exception:
                pass

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": z(generated_at),
        "source": SOURCE,
        "license": LICENSE,
        "domain": domain,
        "run_reference_time": z(ref),
        "valid_time": z(valid) if valid is not None else None,
        "grid": {k: grid[k] for k in ("nx", "ny", "dx", "dy")},
        "features": features,
        "availability": {
            "fields_missing": missing,
            "horizon_exhausted": bool(exhausted),
        },
    }


def write_json(path: str, obj: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="path del JSON da scrivere")
    ap.add_argument("--domain", default="ecmwf_ifs025", choices=sorted(DOMAIN_BRANCH))
    ap.add_argument("--manifest", help="override locale/URL di latest.json")
    ap.add_argument("--frame", help="override locale/URL del frame .om")
    ap.add_argument("--generated-at", help="ISO8601 UTC (default: now)")
    ap.add_argument("--window-hours", type=int, default=WINDOW_HOURS_MAX)
    args = ap.parse_args(argv)

    if args.window_hours > WINDOW_HOURS_MAX:
        print(f"FATAL: window-hours {args.window_hours} > {WINDOW_HOURS_MAX}", file=sys.stderr)
        return 2
    generated_at = iso(args.generated_at) if args.generated_at else datetime.now(timezone.utc)

    branch = DOMAIN_BRANCH[args.domain]
    manifest_url = (f"{RAW_BASE}/{branch}/data/fm2/{args.domain}/latest.json")
    manifest = load_manifest(args.manifest or manifest_url)

    if args.frame:
        frame_spec = args.frame
    else:
        frame, _, _, _ = select_frame(manifest, generated_at, args.window_hours)
        if frame is None:
            frame_spec = ""
        else:
            frame_spec = f"{RAW_BASE}/{branch}/data/fm2/{args.domain}/{frame['file']}"

    obj = build(manifest, frame_spec, generated_at, args.window_hours, args.domain)
    write_json(args.out, obj)
    print(json.dumps({
        "out": args.out, "domain": obj["domain"], "valid_time": obj["valid_time"],
        "features": len(obj["features"]),
        "blocks": sum(1 for f in obj["features"] if f["kind"] == "block"),
        "horizon_exhausted": obj["availability"]["horizon_exhausted"],
        "fields_missing": obj["availability"]["fields_missing"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
