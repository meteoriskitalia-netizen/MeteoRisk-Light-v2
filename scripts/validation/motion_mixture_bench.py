#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Misura l'errore a 60 min di OGNI ipotesi di moto sui track reali.

SCOPO. I pesi della mistura di ipotesi (``nwp_motion.HYPOTHESIS_ERROR_KM_60``)
non si scelgono a mano: si misurano. Questo script prende i track REALI
dell'archivio radar e, per ogni origine temporale con almeno 4 punti:

  * fitta il moto sui soli punti passati (``forecast.fit_motion``);
  * calcola le 5 ipotesi alla posizione e all'istante dell'origine;
  * confronta la posizione prevista a +60 min con quella OSSERVATA.

Due sorgenti, entrambe dal branch ``radar-history``:

  --slots   snapshot ``data/radar/history/slots/*.json``: posizioni dei track
            per slot, con ``uid`` persistente fra slot (e' l'associazione del
            tracker vero, ``history.py``). E' la sorgente PRIMARIA.
  --history snapshot ``data/radar/history/cells/*.json``: celle grezze per
            frame; i track si ricostruiscono col ``tracking.Tracker`` del
            motore. Sorgente di CONTROLLO (piu' corta: 5 min per frame).

Uso:
    python scripts/validation/motion_mixture_bench.py \
        --slots <cartella slots> --history <cartella cells> \
        --points data/latest/meteorisk-points.json \
        --out data/validation/motion_mixture_bench.json

Nessun valore inventato: se un'ipotesi non e' calcolabile per un caso, il caso
non entra nella sua statistica e il conteggio lo dichiara.
"""

import argparse
import glob
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if os.path.join(ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "scripts"))

from radar_engine import config as config_mod          # noqa: E402
from radar_engine import forecast as forecast_mod     # noqa: E402
from radar_engine import nwp_motion as nm             # noqa: E402
from radar_engine import tracking as tracking_mod     # noqa: E402

TARGET_MIN = 60.0
TARGET_TOL_MIN = 6.0
MIN_BASIS_POINTS = 4
MAX_BASIS_GAP_MIN = 15.0
# Gate di continuita' FISICA: la stessa soglia del motore
# (forecast.SPEED_RANGE_KMH[1], usata da tracking.Tracker per rifiutare
# un'associazione). Un track che "salta" piu' veloce di cosi' non e' un track:
# senza questo gate una serie di uid riusato produce errori inventati.
MAX_SPEED_KMH = 110.0


class _Pt:
    """Punto minimale (timestamp_ms, lonlat) per fit_motion."""

    __slots__ = ("timestamp_ms", "lonlat")

    def __init__(self, ts_ms, lonlat):
        self.timestamp_ms = int(ts_ms)
        self.lonlat = (float(lonlat[0]), float(lonlat[1]))


class _Cell:
    """Cella minimale compatibile con tracking.Tracker (duck typing)."""

    __slots__ = ("cell_id", "timestamp_ms", "timestamp_iso", "centroid_lonlat",
                 "area_km2", "max_dbz", "mean_dbz", "solidity", "compactness",
                 "eccentricity")

    def __init__(self, row):
        self.cell_id = row["cell_id"]
        self.timestamp_ms = int(row["timestamp_ms"])
        self.timestamp_iso = row.get("timestamp_iso")
        self.centroid_lonlat = tuple(float(x) for x in row["centroid_lonlat"])
        self.area_km2 = float(row["area_km2"])
        self.max_dbz = float(row["max_dbz"])
        self.mean_dbz = float(row.get("mean_dbz") or 0.0)
        self.solidity = float(row.get("solidity") or 1.0)
        self.compactness = float(row.get("compactness") or 1.0)
        self.eccentricity = row.get("eccentricity")


def load_cell_frames(history_dir):
    frames = []
    for path in sorted(glob.glob(os.path.join(history_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        rows = raw.get("cells") or []
        if not rows:
            continue
        ts = int(raw.get("radar_timestamp_ms") or rows[0]["timestamp_ms"])
        frames.append((ts, [_Cell(r) for r in rows]))
    frames.sort(key=lambda x: x[0])
    return frames


def tracks_from_cells(history_dir, cfg):
    """Track veri del motore sui frame storici (nessun seed d'archivio)."""
    frames = load_cell_frames(history_dir)
    tracker = tracking_mod.Tracker(cfg, None, diagnostics=False)
    for idx, (_ts, cells) in enumerate(frames):
        tracker.update(cells, idx)
    tracker.complete_cycles()
    out = []
    for t in tracker.tracks:
        if len(t.points) < MIN_BASIS_POINTS:
            continue
        pts = sorted(t.points, key=lambda p: p.timestamp_ms)
        out.append(("cell_track_%d" % t.track_id,
                    [_Pt(p.timestamp_ms, p.lonlat) for p in pts]))
    return out, len(frames)


def tracks_from_slots(slots_dir):
    """Serie per ``uid`` dagli snapshot degli slot (associazione del tracker)."""
    series = {}
    n_slots = 0
    for path in sorted(glob.glob(os.path.join(slots_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        ts = raw.get("radar_timestamp_ms")
        if ts is None:
            continue
        n_slots += 1
        for kind in ("tracks", "structures"):
            for row in raw.get(kind) or []:
                uid = row.get("uid")
                ll = row.get("lonlat")
                # `structures` porta uid null: senza identita' persistente non
                # e' una serie e NON entra nella misura (nessuna serie
                # inventata accorpando oggetti diversi).
                if uid is None or not ll or len(ll) != 2:
                    continue
                key = "%s_%s" % (kind, uid)
                series.setdefault(key, []).append(_Pt(ts, ll))
    out = []
    for key, pts in series.items():
        pts.sort(key=lambda p: p.timestamp_ms)
        if len(pts) < MIN_BASIS_POINTS:
            continue
        out.append((key, pts))
    return out, n_slots


def _target_at(points, origin_ms, minutes, tol_min):
    want = origin_ms + minutes * 60000.0
    best = None
    for p in points:
        d = abs(p.timestamp_ms - want)
        if d <= tol_min * 60000.0 and (best is None or d < best[0]):
            best = (d, p)
    return best[1] if best else None


def _basis_is_contiguous(basis, max_gap_min, max_speed_kmh=None):
    for a, b in zip(basis[:-1], basis[1:]):
        dt_min = (b.timestamp_ms - a.timestamp_ms) / 60000.0
        if dt_min > max_gap_min:
            return False
        if max_speed_kmh is not None and dt_min > 0.0:
            v = tracking_mod.haversine_km(a.lonlat, b.lonlat) / (dt_min / 60.0)
            if v > max_speed_kmh:
                return False
    return True


def _component(err_km, bearing_error_deg):
    brg = math.radians(bearing_error_deg)
    return err_km * math.cos(brg), err_km * math.sin(brg)


def measure(tracks, dataset):
    cases = []
    for key, pts in tracks:
        for i in range(MIN_BASIS_POINTS - 1, len(pts)):
            origin = pts[i]
            target = _target_at(pts, origin.timestamp_ms, TARGET_MIN,
                                TARGET_TOL_MIN)
            if target is None:
                continue
            basis = pts[:i + 1][-8:]
            if not _basis_is_contiguous(basis, MAX_BASIS_GAP_MIN,
                                        MAX_SPEED_KMH):
                continue
            fit = forecast_mod.fit_motion(basis)
            motions = nm.hypothesis_motions(
                dataset, origin.lonlat[0], origin.lonlat[1],
                origin.timestamp_ms, fit)
            true_bearing = tracking_mod.bearing_deg(origin.lonlat,
                                                    target.lonlat)
            row = {
                "track": key,
                "origin_ms": int(origin.timestamp_ms),
                "lon": round(float(origin.lonlat[0]), 5),
                "lat": round(float(origin.lonlat[1]), 5),
                "n_basis": len(basis),
                "fit_used": bool(fit.get("fit_used")),
                "resid_std_km": round(float(fit.get("resid_std_km") or 0.0), 2),
                "observed_bearing_deg": round(float(true_bearing), 1),
                "observed_step_km": round(
                    tracking_mod.haversine_km(origin.lonlat, target.lonlat), 2),
                "target_lon": round(float(target.lonlat[0]), 5),
                "target_lat": round(float(target.lonlat[1]), 5),
                "hypotheses": {},
            }
            for m in motions:
                name = m["name"]
                if m.get("null_reason") or m.get("speed_kmh") is None:
                    row["hypotheses"][name] = {"null_reason":
                                               m.get("null_reason")}
                    continue
                pred = tracking_mod.dest_from(
                    origin.lonlat, float(m["direction_toward_deg"]),
                    float(m["speed_kmh"]) * (TARGET_MIN / 60.0))
                err = tracking_mod.haversine_km(pred, target.lonlat)
                bearing_err = tracking_mod.bearing_deg(pred, target.lonlat)
                along, cross = _component(err, bearing_err - true_bearing)
                row["hypotheses"][name] = {
                    "speed_kmh": round(float(m["speed_kmh"]), 2),
                    "direction_toward_deg": round(
                        float(m["direction_toward_deg"]), 1),
                    "error_km": round(err, 3),
                    "along_km": round(along, 3),
                    "cross_km": round(cross, 3),
                }
            cases.append(row)
    return cases


def _pct(values, q):
    data = sorted(values)
    if not data:
        return 0.0
    if len(data) == 1:
        return float(data[0])
    pos = (q / 100.0) * (len(data) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(data[lo])
    return float(data[lo] + (data[hi] - data[lo]) * (pos - lo))


def _stats(values):
    if not values:
        return {"n": 0}
    absv = [abs(v) for v in values]
    rms = math.sqrt(sum(v * v for v in values) / len(values))
    return {
        "n": len(values),
        "rms_km": round(rms, 3),
        "mean_km": round(statistics.fmean(values), 3),
        "p50_abs_km": round(_pct(absv, 50), 3),
        "p90_abs_km": round(_pct(absv, 90), 3),
        "max_abs_km": round(max(absv), 3),
    }


def summarise(cases, names):
    out = {}
    for name in names:
        errs, along, cross = [], [], []
        for c in cases:
            h = c["hypotheses"].get(name) or {}
            if h.get("error_km") is None:
                continue
            errs.append(float(h["error_km"]))
            along.append(float(h["along_km"]))
            cross.append(float(h["cross_km"]))
        st = _stats(errs)
        st["along_rms_km"] = round(math.sqrt(
            sum(v * v for v in along) / len(along)), 3) if along else None
        st["cross_rms_km"] = round(math.sqrt(
            sum(v * v for v in cross) / len(cross)), 3) if cross else None
        out[name] = st
    return out


def _weighted_centre(case, weights):
    lon0, lat0 = case["lon"], case["lat"]
    kx = 111.1949 * math.cos(math.radians(lat0))
    su = sv = 0.0
    tot = 0.0
    for name, w in weights.items():
        h = case["hypotheses"].get(name) or {}
        if h.get("speed_kmh") is None:
            continue
        brg = math.radians(float(h["direction_toward_deg"]))
        d = float(h["speed_kmh"]) * 1.0          # 60 min = 1 h
        su += w * d * math.sin(brg)
        sv += w * d * math.cos(brg)
        tot += w
    if tot <= 0:
        return None
    lon = lon0 + (su / tot) / kx
    lat = lat0 + (sv / tot) / 111.1949
    return (lon, lat)


def _error_km(case, weights):
    centre = _weighted_centre(case, weights)
    if centre is None:
        return None
    return tracking_mod.haversine_km(
        centre, (case["target_lon"], case["target_lat"]))


def _now_iso():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wrap180(deg):
    return (float(deg) + 180.0) % 360.0 - 180.0


def shape_report(cases, weights, along, cross):
    """Forma della densita' della mistura sui casi REALI (numeri, non stime).

    Per ogni caso costruisce le 5 ipotesi, i pesi (invarianza inversa),
    i residui (errore^2 - disaccordo^2) e chiama la funzione del modulo:
    cosi' la misura usa lo stesso codice che gira nel motore.
    """
    names = [n for n in nm.HYPOTHESIS_ORDER if n in weights]
    angles = []
    ratios = []
    asym = []
    modes = []
    for c in cases:
        motions = []
        dirs = []
        for n in names:
            h = c["hypotheses"].get(n) or {}
            if h.get("speed_kmh") is None:
                continue
            brg = math.radians(float(h["direction_toward_deg"]))
            motions.append({"name": n, "null_reason": None,
                            "speed_kmh": h["speed_kmh"],
                            "direction_toward_deg": h["direction_toward_deg"],
                            "u_kmh": h["speed_kmh"] * math.sin(brg),
                            "v_kmh": h["speed_kmh"] * math.cos(brg)})
            dirs.append(float(h["direction_toward_deg"]))
        if len(motions) < 2:
            continue
        # angolo medio fra le direzioni delle ipotesi attive (media a coppie)
        pairs = []
        for i in range(len(dirs)):
            for j in range(i + 1, len(dirs)):
                pairs.append(abs(_wrap180(dirs[i] - dirs[j])))
        angles.append(statistics.fmean(pairs))
        res = nm.hypothesis_residuals(motions, weights, errors_along=along,
                                      errors_cross=cross)
        for m in motions:
            r = res.get(m["name"]) or {}
            m["weight"] = weights.get(m["name"], 0.0)
            m["sigma_along_km_60"] = r.get("sigma_along_km_60")
            m["sigma_cross_km_60"] = r.get("sigma_cross_km_60")
        shape = nm.density_shape_metrics(motions, offset_min=60.0)
        if shape is None:
            continue
        ratios.append(shape["area_ratio_50_90"])
        asym.append(shape["asymmetry_km"])
        modes.append(shape["n_modes"])

    def _summ(values):
        if not values:
            return {"n": 0}
        return {
            "n": len(values),
            "mean": round(statistics.fmean(values), 3),
            "median": round(statistics.median(values), 3),
            "p10": round(_pct(values, 10), 3),
            "p90": round(_pct(values, 90), 3),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
        }

    return {
        "cases": len(ratios),
        "area_ratio_50_90": _summ(ratios),
        "area_ratio_gaussian_reference": 0.3010,
        "asymmetry_km": _summ(asym),
        "n_modes": {
            "n": len(modes),
            "cases_with_gt1_mode": sum(1 for m in modes if m > 1),
            "share_gt1_mode": round(
                sum(1 for m in modes if m > 1) / len(modes), 3) if modes else 0,
            "max_modes": max(modes) if modes else 0,
        },
        "angle_between_hypotheses_deg": _summ(angles),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bench ipotesi di moto (60 min)")
    ap.add_argument("--slots", default=None)
    ap.add_argument("--history", default=None)
    ap.add_argument("--points", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-resid", type=float, default=None,
                    help="tieni solo i fit con residuo <= valore (km)")
    ap.add_argument("--dump-cases", default=None)
    args = ap.parse_args(argv)

    dataset = nm.load_points(args.points, force=True)
    cfg = config_mod.CONFIG["tracking"]
    blocks = {}
    all_cases = []
    for label, source in (("slots", args.slots), ("cells", args.history)):
        if not source:
            continue
        if label == "slots":
            tracks, n_frames = tracks_from_slots(source)
        else:
            tracks, n_frames = tracks_from_cells(source, cfg)
        cases = measure(tracks, dataset)
        if args.min_resid is not None:
            cases = [c for c in cases
                     if c["fit_used"] and c["resid_std_km"] <= args.min_resid]
        blocks[label] = {
            "source": os.path.abspath(source),
            "units": n_frames,
            "series": len(tracks),
            "cases_60min": len(cases),
            "per_hypothesis": summarise(cases, list(nm.HYPOTHESIS_ORDER)),
        }
        all_cases.extend(cases)

    names = list(nm.HYPOTHESIS_ORDER)
    summary = summarise(all_cases, names)
    # CONFRONTO EQUO: le ipotesi NWP esistono solo dove c'e' un punto entro
    # 80 km (n=82 su 165). I pesi e il confronto mistura-vs-singola si fanno
    # sullo STESSO sottoinsieme, altrimenti si confrontano campioni diversi.
    fair = [c for c in all_cases
            if all((c["hypotheses"].get(n) or {}).get("error_km") is not None
                   for n in names)]
    fair_summary = summarise(fair, names)
    errs = {k: v.get("rms_km") for k, v in fair_summary.items()
            if v.get("rms_km")}
    weights, wdetail = nm.hypothesis_weights(
        [{"name": k, "null_reason": None if k in errs else "no_measured_error"}
         for k in names], errors=errs, reliability={"observed": 1.0})
    mix_err = [e for e in (_error_km(c, weights) for c in fair)
               if e is not None]
    along = {k: v.get("along_rms_km") for k, v in fair_summary.items()}
    cross = {k: v.get("cross_rms_km") for k, v in fair_summary.items()}
    shape = shape_report(fair, weights, along, cross)

    out = {
        "schema_version": "1.0",
        "generated_at": _now_iso(),
        "target_min": TARGET_MIN,
        "target_tol_min": TARGET_TOL_MIN,
        "min_basis_points": MIN_BASIS_POINTS,
        "max_basis_gap_min": MAX_BASIS_GAP_MIN,
        "min_resid_km": args.min_resid,
        "cases_60min": len(all_cases),
        "sources": blocks,
        "per_hypothesis": summary,
        "fair_subset": {
            "cases": len(fair),
            "note": ("casi in cui TUTTE e 5 le ipotesi sono calcolabili: e' "
                     "l'unico campione su cui il confronto e' lecito"),
            "per_hypothesis": fair_summary,
            "mixture": _stats(mix_err),
            "best_single": (min(errs.items(), key=lambda kv: kv[1])
                            if errs else None),
            "mixture_vs_best_single_rms_ratio": (
                round(_stats(mix_err)["rms_km"] / min(errs.values()), 4)
                if errs and mix_err else None),
        },
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "weights_detail": wdetail,
        "mixture": _stats(mix_err),
        "shape": shape,
        "best_single": (min(errs.items(), key=lambda kv: kv[1])
                        if errs else None),
    }
    text = json.dumps(out, indent=1, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    if args.dump_cases:
        with open(args.dump_cases, "w", encoding="utf-8") as f:
            json.dump(all_cases, f)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
