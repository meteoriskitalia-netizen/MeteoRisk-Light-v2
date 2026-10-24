#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk — bench di falsificazione su FOOTPRINT GEOMETRICO (1.2.4.21).

Cosa cambia rispetto al collaudo della 1.2.4.20 (`asof_bench.py`): NIENTE
soglie, NIENTE formule, NIENTE pesi. Cambia solo la GRANDEZZA GEOMETRICA con
cui un allarme viene accoppiato alla verita':

  1.2.4.20  distanza fra il RAPPORTO METAR e il CENTROIDE della struttura
            (gate 10 km) -> 0 hit / 6 miss, perche' nessuna cella ha il
            centroide entro 10 km (minimo 20,3 km) mentre le celle sono
            componenti connesse fino a 4.659 km^2: la stazione cade DENTRO
            l'eco e il centroide e' lontano.
  1.2.4.21  distanza fra il RAPPORTO METAR e il POLIGONO del footprint
            (componente connessa poligonizzata, `radar_engine.footprint`):
            0 km se il punto cade DENTRO, altrimenti distanza dal bordo;
            hit se <= 10 km.

Due varianti, entrambe riportate e MAI fuse:
  (a) `cells`            — footprint di OGNI cella rilevata (layer grezzo):
                           misura "il radar ha visto qualcosa".
  (a2) `structures_all`  — footprint di OGNI struttura convettiva emessa:
                           stessa popolazione di allarmi del collaudo 1.2.4.20,
                           quindi il confronto col centroide e' a parita' di
                           allarmi.
  (b) `structures_severe`— footprint delle SOLE strutture con livello SOI
                           `strong`/`intense`: misura "il prodotto avrebbe
                           avvisato".

TASSO BASE (obbligatorio, e' il cuore dell'esperimento): per ognuna delle 6
run, la frazione d'ITALIA (area, rasterizzata a 0,01 grado) coperta dai
poligoni marcati, e quante delle 99 stazioni METAR italiane cadono dentro o
entro 10 km da un poligono marcato, indipendentemente dagli eventi. Se la
copertura supera il 50% (area o stazioni), il POD su footprint NON e'
informativo e viene riportato come DIAGNOSTICA.

n = 6: nessuna taratura e' ammessa. Le soglie restano quelle del prodotto.
"""

import argparse
import datetime as dt
import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(_HERE))

from validation import asof_bench as bench  # noqa: E402
from validation import geo_match  # noqa: E402
from validation import supercell_validation as sv  # noqa: E402

FOCUS_LAYERS = ("cells", "structures_all", "structures_severe")
SEVERE_LEVELS = ("strong", "intense")
# Oltre questa copertura il POD su footprint non distingue nulla.
NOT_INFORMATIVE_FRACTION = 0.50
GRID_RES_DEG = 0.01
# Area dell'Italia (ISTAT, superficie amministrativa): solo per il CONTROLLO
# della griglia, non per i conti (i conti usano la griglia, dichiarata).
ITALY_REFERENCE_KM2 = 302073.0


# ---------------------------------------------------------------------------
# Stazioni METAR (99) e stazioni con eventi (14)
# ---------------------------------------------------------------------------
def load_stations(labels_path):
    """{sid: (lon, lat)} di TUTTE le stazioni presenti nelle etichette."""
    out = {}
    with open(labels_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            sid = rec.get("station")
            if not sid:
                continue
            key = (round(float(rec["lon"]), 5), round(float(rec["lat"]), 5))
            prev = out.get(sid)
            if prev is not None and prev != key:
                raise ValueError("station_coords_inconsistent:%s" % sid)
            out[sid] = key
    return out


def event_stations(labels_path, window=(bench.WINDOW_START, bench.WINDOW_END)):
    """Stazioni con ALMENO un evento SEVERO nella finestra.

    "Severo" = grandine, vento severo, nube a imbuto / tornado (QC A/B). Il
    TEMPORALE semplice NON conta: e' il criterio gia' usato in
    retro/REPORT.md, che dava 14 stazioni (* p * 10 km^2 = 4.398 km^2 =
    1,46% del territorio)."""
    stations = set()
    with open(labels_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ts = rec.get("ts_utc") or ""
            if not (window[0] <= ts <= window[1]):
                continue
            if rec.get("type") in ("hail", "severe_wind", "funnel_cloud",
                                   "tornado"):
                if rec.get("qc") in bench.QC_OK:
                    stations.add(rec.get("station"))
    return sorted(s for s in stations if s)


# ---------------------------------------------------------------------------
# Footprint -> Detection
# ---------------------------------------------------------------------------
def read_footprints(path):
    if not os.path.exists(path):
        return [], []
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    cells, structs = [], []
    for feat in payload.get("features") or []:
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if props.get("layer") == "cell":
            cells.append((props, geom))
        else:
            structs.append((props, geom))
    return cells, structs


def detections_from_footprints(cells, structs, struct_payload, event_id,
                               variant):
    """Detection con `footprint` geometrico per una delle varianti.

    Un footprint che il motore NON e' riuscito a risolvere NON viene scartato:
    l'allarme resta (distanza dal centroide) e viene contato in
    `footprint_missing`, perche' eliminarlo cambierebbe la matrice 2x2."""
    out = []
    missing = 0
    if variant == "cells":
        for props, geom in cells:
            if geom is None:
                missing += 1
                continue
            centroid = props.get("centroid") or [None, None]
            out.append(sv.Detection(
                id="cell:%s@%s" % (props.get("cell_id"),
                                   props.get("frame_iso")),
                time_utc=sv.parse_time_utc(props.get("frame_iso")),
                lon=float(centroid[0]), lat=float(centroid[1]),
                category="cell",
                source="radar_engine.footprint",
                extras={"cell_id": props.get("cell_id"),
                        "area_km2": props.get("area_km2"),
                        "max_dbz": props.get("max_dbz"),
                        "frame_iso": props.get("frame_iso"),
                        "footprint_source": props.get("source")},
                footprint=geom,
            ))
        return out, missing

    by_id = {}
    for props, geom in structs:
        by_id[props.get("structure_id")] = (props, geom)
    for rec in (struct_payload.get("structures") or []):
        sid = rec.get("structure_id")
        props, geom = by_id.get(sid, ({}, None))
        level = rec.get("level")
        if variant == "structures_severe" and str(level) not in SEVERE_LEVELS:
            continue
        pos = rec.get("position") or []
        if len(pos) != 2:
            continue
        last = rec.get("last_seen") or rec.get("first_seen")
        first = rec.get("first_seen") or last
        if not last:
            continue
        if geom is None:
            missing += 1
        stack = rec.get("severe_stack") or {}
        hail = rec.get("hail") or {}
        over = rec.get("overhang") or {}
        out.append(sv.Detection(
            id=sid or "unknown",
            time_utc=sv.parse_time_utc(last),
            lon=float(pos[0]), lat=float(pos[1]),
            category=str(level or "structure"),
            source="radar_engine.convective_structures",
            first_seen_utc=sv.parse_time_utc(first),
            last_seen_utc=sv.parse_time_utc(last),
            extras={
                "event_id": event_id,
                "structure_id": sid,
                "soi": rec.get("soi"),
                "level": level,
                "phase": rec.get("phase"),
                "on_latest_frame": rec.get("on_latest_frame"),
                "vil_density_g_m3": (hail.get("vil_density_g_m3") or {}).get("value"),
                "mesh_mm": (hail.get("mesh_mm") or {}).get("value"),
                "mesh_null_reason": (hail.get("mesh_mm") or {}).get("null_reason"),
                "elevated_core": (over.get("elevated_core") or {}).get("value"),
                "strong_elevated_core":
                    (over.get("strong_elevated_core") or {}).get("value"),
                "severe_stack_version": (stack or {}).get("version"),
                "footprint_area_km2": props.get("area_km2"),
                "footprint_source": props.get("source"),
                "footprint_missing": geom is None,
            },
            footprint=geom,
        ))
    return out, missing


# ---------------------------------------------------------------------------
# Tasso base
# ---------------------------------------------------------------------------
def italy_mask(italy_geojson, bounds=None, res_deg=GRID_RES_DEG):
    """Griglia geografica + maschera delle province italiane."""
    with open(italy_geojson, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    feats = payload.get("features") or []
    geoms = [f.get("geometry") for f in feats]
    bbox = geo_match.geometry_bbox({"type": "MultiPolygon",
                                    "coordinates": [p for g in geoms
                                                    for p in
                                                    geo_match.geometry_polygons(g)]})
    if bbox is None:
        raise ValueError("italy_geojson_empty")
    if bounds is not None:
        bbox = bounds
    transform, shape, res = geo_match.grid_spec(bbox, res_deg)
    mask = geo_match.rasterize(geoms, transform, shape)
    area = geo_match.pixel_area_km2(transform, shape)
    return {
        "transform": transform,
        "shape": shape,
        "res_deg": float(res[0]),
        "mask": mask,
        "area_km2": float(area[mask].sum()),
        "n_pixels": int(mask.sum()),
        "bounds": [round(float(v), 4) for v in bbox],
        "n_features": len(feats),
    }


def coverage_is_informative(area_fraction, stations_fraction,
                            threshold=NOT_INFORMATIVE_FRACTION):
    """(informativo, motivo). Regola dichiarata: oltre il 50% di AREA o di
    STAZIONI coperte, il POD sul footprint non distingue il prodotto da
    'qualunque punto d'Italia' e diventa diagnostica."""
    if area_fraction is not None and area_fraction > threshold:
        return False, "area_fraction"
    if stations_fraction is not None and stations_fraction > threshold:
        return False, "stations_fraction"
    return True, None


def base_rate_for_run(cells, structs, italy, stations, event_stations,
                      buffer_km=bench.MAX_KM):
    """Copertura (area d'Italia + stazioni) di OGNI variante in UNA run.

    La copertura di una run e' l'UNIONE dei footprint emessi nei suoi frame:
    e' la domanda giusta per un episodio di 25-30 minuti ("cosa avrebbe
    marcato il prodotto durante l'episodio"), ed e' CONSERVATIVA: una finestra
    piu' larga puo' solo alzare il tasso base, cioe' rendere il POD meno
    impressionante, non piu'."""
    out = {}
    variants = {
        "cells": [g for _p, g in cells],
        "structures_all": [g for _p, g in structs],
        "structures_severe": [g for p, g in structs if p.get("severe")],
    }
    for name, geoms in variants.items():
        geoms = [g for g in geoms if g]
        cov = geo_match.coverage_fraction(geoms, italy["mask"],
                                          italy["transform"])
        all_st = geo_match.count_points_covered(
            [stations[s] for s in sorted(stations)], geoms, buffer_km)
        ev_st = geo_match.count_points_covered(
            [stations[s] for s in event_stations if s in stations], geoms,
            buffer_km)
        area_frac = cov["fraction"]
        st_frac = all_st["fraction"]
        informative, why = coverage_is_informative(area_frac, st_frac)
        not_informative = not informative
        out[name] = {
            "n_polygons": len(geoms),
            "area_km2": cov["covered_area_km2"],
            "area_fraction": area_frac,
            "stations_covered": all_st["n_covered"],
            "stations_total": all_st["n_points"],
            "stations_fraction": st_frac,
            "event_stations_covered": ev_st["n_covered"],
            "event_stations_total": ev_st["n_points"],
            "event_stations_fraction": ev_st["fraction"],
            "not_informative": not_informative,
            "not_informative_reason": why,
            "buffer_km": buffer_km,
            "covered_stations": [s for s in all_st["points"]
                                 if s["covered"]],
        }
    return out


def summarise_base_rate(rows):
    """min/mediana/max della copertura per variante sulle 6 run."""
    out = {}
    for name in FOCUS_LAYERS:
        af = [r[name]["area_fraction"] for r in rows
              if r[name]["area_fraction"] is not None]
        sf = [r[name]["stations_fraction"] for r in rows
              if r[name]["stations_fraction"] is not None]
        ef = [r[name]["event_stations_fraction"] for r in rows
              if r[name]["event_stations_fraction"] is not None]
        out[name] = {
            "area_fraction_min": min(af) if af else None,
            "area_fraction_median": _median(af),
            "area_fraction_max": max(af) if af else None,
            "stations_fraction_min": min(sf) if sf else None,
            "stations_fraction_median": _median(sf),
            "stations_fraction_max": max(sf) if sf else None,
            "event_stations_fraction_median": _median(ef),
            "stations_covered_max": max(r[name]["stations_covered"]
                                        for r in rows),
            "not_informative_any_run": any(r[name]["not_informative"]
                                           for r in rows),
        }
    return out


def _median(values):
    vals = sorted(values)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def binom_tail_at_least(k, n, p):
    """P(X >= k) per X ~ Binomiale(n, p). Diagnostica, non un test formale."""
    if p is None or n <= 0:
        return None
    p = min(1.0, max(0.0, float(p)))
    total = 0.0
    for i in range(int(k), int(n) + 1):
        total += math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i))
    return total


# ---------------------------------------------------------------------------
# Esecuzione
# ---------------------------------------------------------------------------
def run_engine_with_footprints(anchor_ms, out_dir, main_py):
    fp_path = os.path.join(out_dir, "footprints.geojson")
    return bench.run_engine(anchor_ms, out_dir, main_py,
                            extra=["--footprints", fp_path])


def per_event_row(truth_event, dets, variant):
    """Diagnostica per evento: sei coperto? da cosa? a che distanza?"""
    best = None
    for det in dets:
        d = (geo_match.distance_to_geometry_km(truth_event.lon,
                                               truth_event.lat, det.footprint)
             if det.footprint else None)
        if d is None:
            d = sv.haversine_km(truth_event.lon, truth_event.lat,
                                det.lon, det.lat)
        if best is None or d < best[0]:
            best = (d, det)
    if best is None:
        return {"variant": variant, "covered": False, "distance_km": None,
                "detection": None}
    d, det = best
    return {
        "variant": variant,
        "covered": bool(d <= bench.MAX_KM),
        "distance_km": round(float(d), 3),
        "inside": bool(d == 0.0),
        "detection": det.id,
        "level": det.extras.get("level"),
        "area_km2": det.extras.get("area_km2")
        or det.extras.get("footprint_area_km2"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Bench su footprint geometrico (1.2.4.21)")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--italy-geojson", required=True,
                    help="province_italiane.geojson (107 province)")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--markdown", default=None)
    ap.add_argument("--main", default=os.path.join(
        _ROOT, "scripts", "radar_engine", "main.py"))
    ap.add_argument("--skip-runs", action="store_true",
                    help="riusa le directory <work-dir>/<slug>/ gia' prodotte")
    args = ap.parse_args(argv)

    events = bench.read_truth(args.labels)
    truth = bench.truth_for_bench(events)
    stations = load_stations(args.labels)
    ev_stations = event_stations(args.labels)
    missing_stations = [s for s in ev_stations if s not in stations]
    if missing_stations:
        raise ValueError("event_stations_without_coords:%s"
                         % ",".join(missing_stations))
    italy = italy_mask(args.italy_geojson)
    os.makedirs(args.work_dir, exist_ok=True)

    params = sv.MatchParams(max_distance_km=bench.MAX_KM,
                            pre_window_min=bench.PRE_MIN,
                            post_window_min=bench.POST_MIN)

    dets = {name: [] for name in FOCUS_LAYERS}
    missing = {name: 0 for name in FOCUS_LAYERS}
    runs = []
    base_rows = []
    level_counts = {}

    for rec, ev in zip(events, truth):
        slug = rec["station"] + "_" + rec["ts_utc"].replace(":", "") \
            .replace("-", "")
        out_dir = os.path.join(args.work_dir, slug)
        anchor_ms = bench.anchor_for(ev.time_utc)
        rc = summary = None
        if not args.skip_runs:
            rc, summary, err = run_engine_with_footprints(
                anchor_ms, out_dir, args.main)
        else:
            summary = None
        struct_path = os.path.join(out_dir, "convective_structures.json")
        struct_payload = {}
        if os.path.exists(struct_path):
            with open(struct_path, "r", encoding="utf-8") as fh:
                struct_payload = json.load(fh)
        cells, structs = read_footprints(
            os.path.join(out_dir, "footprints.geojson"))
        for props, _geom in structs:
            lv = str(props.get("level"))
            level_counts[lv] = level_counts.get(lv, 0) + 1

        run_dets = {}
        for variant in FOCUS_LAYERS:
            ds, miss = detections_from_footprints(cells, structs,
                                                  struct_payload,
                                                  rec["event_id"], variant)
            dets[variant].extend(ds)
            missing[variant] += miss
            run_dets[variant] = ds

        base = base_rate_for_run(cells, structs, italy, stations, ev_stations)
        base_rows.append(base)

        per_variant = {v: per_event_row(ev, run_dets[v], v)
                       for v in FOCUS_LAYERS}
        runs.append({
            "event_id": rec["event_id"],
            "station": rec.get("station"),
            "severity": rec.get("severity"),
            "truth_ts": rec["ts_utc"],
            "truth_lat": rec["lat"], "truth_lon": rec["lon"],
            "anchor_iso": bench._iso(dt.datetime.fromtimestamp(
                anchor_ms / 1000.0, dt.timezone.utc)),
            "rc": rc,
            "run_status": (summary or {}).get("status"),
            "frames": (summary or {}).get("frames"),
            "cells": (summary or {}).get("cells"),
            "footprint_summary": (summary or {}).get("footprints"),
            "structures_emitted": len(struct_payload.get("structures") or []),
            "cell_footprints": len([1 for p, g in cells if g]),
            "structure_footprints": len([1 for p, g in structs if g]),
            "structure_footprints_missing": len([1 for p, g in structs
                                                 if g is None]),
            "per_variant": per_variant,
            "detections_per_variant": {v: len(run_dets[v])
                                       for v in FOCUS_LAYERS},
        })

    variants = {}
    for name in FOCUS_LAYERS:
        result = sv.match_events(dets[name], truth, params)
        counts = result.counts()
        k = counts.hits + counts.misses + counts.false_alarms
        variants[name] = {
            "counts": counts.to_dict(),
            "metrics": sv.compute_metrics(counts),
            "wilson": {
                "pod": {"k": counts.hits, "n": counts.n_truth_events,
                        "interval95": bench.wilson(counts.hits,
                                                   counts.n_truth_events)},
                "csi": {"k": counts.hits, "n": k,
                        "interval95": bench.wilson(counts.hits, k),
                        "caveat": ("CSI indicativo: gli allarmi non "
                                   "accoppiati dipendono dalla copertura "
                                   "della verita' (14 stazioni)")},
            },
            "footprint_missing": missing[name],
            "matches": [m.to_dict() for m in result.matches],
            "unmatched_truth": [t.to_dict() for t in result.unmatched_truth],
            "unmatched_detections_total": len(result.unmatched_detections),
        }

    summary_base = summarise_base_rate(base_rows)
    not_informative = {name: bool(summary_base[name]["not_informative_any_run"])
                       for name in FOCUS_LAYERS}

    # Diagnostica di tasso base: quante attese a caso e con che probabilita'
    # si vedono >= hits su 6 quando ogni stazione ha la probabilita' del
    # tasso base misurato (frazione delle 14 stazioni con eventi coperte).
    null_rows = {}
    for name in FOCUS_LAYERS:
        p_null = summary_base[name]["event_stations_fraction_median"]
        hits = variants[name]["counts"]["hits"]
        null_rows[name] = {
            "p_covered_by_chance": p_null,
            "expected_hits": (None if p_null is None
                              else round(len(events) * p_null, 3)),
            "p_value_binomial_ge_hits": binom_tail_at_least(
                hits, len(events), p_null),
            "note": ("probabilita' che una stazione con evento cada dentro un "
                     "poligono marcato per caso, misurata sulle 14 stazioni "
                     "con eventi; non e' un test formale (n=6)"),
        }

    report = {
        "bench": "as_of_replay_hail_qc_ab_footprint",
        "generated_by": "scripts/validation/asof_bench_footprint.py",
        "params": params.to_dict(),
        "distance_mode": ("punto -> poligono del footprint: 0 dentro, "
                          "distanza dal bordo fuori; hit se <= %.1f km"
                          % bench.MAX_KM),
        "variants_definition": {
            "cells": "footprint di OGNI cella rilevata (layer grezzo)",
            "structures_all": "footprint di OGNI struttura convettiva emessa "
                              "(stessa popolazione del collaudo 1.2.4.20)",
            "structures_severe": "footprint delle sole strutture "
                                 "level in %s" % (list(SEVERE_LEVELS),),
        },
        "filters": {
            "type": bench.HAIL_TYPE, "qc": list(bench.QC_OK),
            "window": [bench.WINDOW_START, bench.WINDOW_END],
            "anchor_rule": "ceil(evento a 5 min) + 5 min -> frame [T-20, T+5]",
        },
        "n_events": len(events),
        "n_days": len(sorted({e["ts_utc"][:10] for e in events})),
        "variants": variants,
        "variants_note": {
            "structures_level_counts": level_counts,
            "severe_equals_all": bool(
                level_counts and set(level_counts).issubset(set(SEVERE_LEVELS))),
            "explanation": (
                "il motore pubblica SOLO i record con `candidate: true`, e il "
                "gate di organizzazione li porta gia' a livello strong/intense: "
                "se tutti i livelli emessi sono strong/intense, il filtro (b) "
                "non toglie nulla e (b) coincide con (a2). E' un fatto del "
                "prodotto, non una scelta del collaudo."),
        },
        "base_rate": {
            "definition": ("copertura dell'UNIONE dei footprint emessi nella "
                           "run: frazione d'AREA d'Italia (griglia %.2f deg, "
                           "px pesati su cos(lat)) e frazione delle %d "
                           "stazioni METAR dentro o entro %.0f km dal bordo"
                           % (GRID_RES_DEG, len(stations), bench.MAX_KM)),
            "grid": {
                "res_deg": italy["res_deg"],
                "bounds": italy["bounds"],
                "n_pixels_italy": italy["n_pixels"],
                "italy_area_km2_from_grid": round(italy["area_km2"], 3),
                "italy_area_km2_reference": ITALY_REFERENCE_KM2,
                "grid_vs_reference_pct": round(
                    100.0 * (italy["area_km2"] / ITALY_REFERENCE_KM2 - 1.0), 3),
                "source": os.path.basename(args.italy_geojson),
            },
            "stations": {
                "n_total": len(stations),
                "n_event_stations": len(ev_stations),
                "event_stations": ev_stations,
            },
            "per_run": [
                {"event_id": r["event_id"], "run": r["station"],
                 **{name: base_rows[i][name] for name in FOCUS_LAYERS}}
                for i, r in enumerate(runs)],
            "summary": summary_base,
            "null_model": null_rows,
            "not_informative": not_informative,
            "not_informative_rule": ("> %.0f%% di area O di stazioni coperte"
                                     % (100.0 * NOT_INFORMATIVE_FRACTION)),
        },
        "runs": runs,
        "centroid_comparison": {
            "source": "1.2.4.20 asof_bench.py (stessi 6 eventi, stesse run)",
            "counts": {"hits": 0, "misses": 6, "false_alarms": 69,
                       "correct_negatives": 0},
            "pod": 0.0,
            "pod_wilson95": [0.0, 0.390],
            "note": ("la sola differenza fra questa tabella e le varianti "
                     "(a2)/(b) e' la GEOMETRIA dell'accoppiamento; l'insieme "
                     "di allarmi della (a2) e' quello del collaudo 1.2.4.20"),
        },
        "tuning": {
            "n_events": len(events),
            "declaration": ("n=6: NESSUNA soglia, formula o peso e' stato "
                            "modificato in questa release. Qualunque taratura "
                            "sui 6 eventi sarebbe overfitting e non e' stata "
                            "fatta."),
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
    print(json.dumps({
        "variants": {k: {"counts": v["counts"],
                         "pod": v["metrics"].get("pod")}
                     for k, v in variants.items()},
        "base_rate": {k: {"area_fraction_median":
                          summary_base[k]["area_fraction_median"],
                          "stations_fraction_median":
                          summary_base[k]["stations_fraction_median"]}
                      for k in FOCUS_LAYERS},
        "not_informative": not_informative,
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def _pct(x):
    return "n/d" if x is None else "%.1f%%" % (100.0 * x)


def render_markdown(report):
    lines = []
    add = lines.append
    b = report["base_rate"]
    s = b["summary"]
    v = report["variants"]
    add("# Collaudo su FOOTPRINT geometrico — grandine METAR QC A/B")
    add("")
    add("Generato da `scripts/validation/asof_bench_footprint.py`. Soglie, "
        "formule e pesi sono quelli del prodotto: nessuna taratura (n=6).")
    add("")
    add("Accoppiamento: **punto -> poligono del footprint** (0 dentro, "
        "distanza dal bordo fuori), hit se <= %.0f km. Il centroide non entra "
        "nel calcolo." % report["params"]["max_distance_km"])
    add("")
    add("## Tasso base (obbligatorio prima di leggere il POD)")
    add("")
    add("| variante | area d'Italia coperta (mediana / max) | stazioni su %d "
        "(mediana / max) | stazioni con eventi su %d (mediana) | informativo? |"
        % (b["stations"]["n_total"], b["stations"]["n_event_stations"]))
    add("| :--- | :--- | :--- | :--- | :--- |")
    for name in FOCUS_LAYERS:
        x = s[name]
        add("| %s | %s / %s | %s / %s | %s | %s |" % (
            name, _pct(x["area_fraction_median"]), _pct(x["area_fraction_max"]),
            _pct(x["stations_fraction_median"]),
            _pct(x["stations_fraction_max"]),
            _pct(x["event_stations_fraction_median"]),
            "**NO** (artefatto)" if report["base_rate"]["not_informative"][name]
            else "si'"))
    add("")
    add("Regola: %s. Griglia %.2f deg, area d'Italia dalla griglia %.0f km2 "
        "(riferimento %.0f km2, scarto %+.2f%%)." % (
            b["not_informative_rule"], b["grid"]["res_deg"],
            b["grid"]["italy_area_km2_from_grid"],
            b["grid"]["italy_area_km2_reference"],
            b["grid"]["grid_vs_reference_pct"]))
    add("")
    add("### Quanto pesa il tasso base sul POD")
    add("")
    add("| variante | p (stazione coperta per caso) | hit attesi su 6 | "
        "P(X >= hit osservati) |")
    add("| :--- | ---: | ---: | ---: |")
    for name in FOCUS_LAYERS:
        nm = b["null_model"][name]
        add("| %s | %s | %s | %s |" % (
            name, _pct(nm["p_covered_by_chance"]), nm["expected_hits"],
            ("n/d" if nm["p_value_binomial_ge_hits"] is None
             else "%.4f" % nm["p_value_binomial_ge_hits"])))
    add("")
    add("Diagnostica, non un test formale: n=6.")
    add("")
    add("## Matrice 2x2 per variante")
    add("")
    add("| variante | hit | miss | falsi allarmi | correct neg. | POD | "
        "Wilson 95% | CSI (indicativo) |")
    add("| :--- | ---: | ---: | ---: | ---: | ---: | :--- | ---: |")
    for name in FOCUS_LAYERS:
        c = v[name]["counts"]
        w = v[name]["wilson"]
        add("| %s | %d | %d | %d | %d | %.3f | [%.3f, %.3f] | %s |" % (
            name, c["hits"], c["misses"], c["false_alarms"],
            c["correct_negatives"], v[name]["metrics"].get("pod") or 0.0,
            w["pod"]["interval95"][0], w["pod"]["interval95"][1],
            ("n/d" if v[name]["metrics"].get("csi") is None
             else "%.3f" % v[name]["metrics"]["csi"])))
    add("")
    cc = report["centroid_comparison"]
    vn = report["variants_note"]
    add("Livelli delle strutture emesse: %s. Variante (b) = (a2)? **%s** — %s"
        % (vn["structures_level_counts"], "si'" if vn["severe_equals_all"]
           else "no", vn["explanation"]))
    add("")
    add("Confronto con il collaudo sul CENTROIDE (1.2.4.20, stessi 6 eventi, "
        "stesse run): hit %d, miss %d, falsi allarmi %d, POD %.3f "
        "[%.3f, %.3f]." % (cc["counts"]["hits"], cc["counts"]["misses"],
                           cc["counts"]["false_alarms"], cc["pod"],
                           cc["pod_wilson95"][0], cc["pod_wilson95"][1]))
    add("")
    add("## Per evento")
    add("")
    add("| evento | stazione | severita' | " +
        " | ".join(FOCUS_LAYERS) + " |")
    add("| :--- | :--- | :--- | " + " | ".join(":---" for _ in FOCUS_LAYERS)
        + " |")
    for r in report["runs"]:
        cells_txt = []
        for name in FOCUS_LAYERS:
            pv = r["per_variant"][name]
            if pv["distance_km"] is None:
                cells_txt.append("n/d")
            else:
                cells_txt.append("%s (%.1f km)" % (
                    "HIT" if pv["covered"] else "miss", pv["distance_km"]))
        add("| %s | %s | %s | %s |" % (
            r["truth_ts"], r["station"], r["severity"], " | ".join(cells_txt)))
    add("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
