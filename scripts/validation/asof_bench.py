#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk — bench di falsificazione in REPLAY DETERMINISTICO (1.2.4.20).

Cosa fa, una volta sola e con numeri dichiarati:
  1. legge le etichette METAR/SPECI italiane (`labels_*.jsonl`), filtra la
     finestra utile e tiene i soli eventi grandine QC A/B (la "verita'");
  2. per OGNI evento esegue il radar engine con `--as-of <ancora>` dove
     ancora = ceil(evento al passo 5 min) + 5 min: la finestra di frame che ne
     esce e' esattamente [evento-20 min, evento+5 min], cioe' la finestra del
     banco (`supercell_validation.py`, pre=20 / post=6);
  3. estrae le strutture convective (`convective_structures.json`) come
     ALLARMI e le accoppia alla verita' con `supercell_validation.match_events`
     (distanza <= 10 km, finestra [-20, +6] min, greedy 1-a-1);
  4. pubblica la matrice 2x2, POD/CSI con intervallo di Wilson, gli esiti
     informativi su 7 e il confronto delle soglie overhang large/small hail.

Perche' un driver e non una misura a mano: senza replay deterministico lo
stesso comando darebbe numeri diversi a ogni esecuzione (ambiente di oggi sui
radar di luglio: retro/REPORT.md §5). Con `--as-of` due run identici producono
output byte-identici, quindi i numeri qui sotto sono riproducibili.

Nessun numero inventato: ogni campo assente resta None e viene dichiarato.
"""

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(_HERE))

from validation import supercell_validation as sv  # noqa: E402

WINDOW_START = "2026-05-20"
WINDOW_END = "2026-10-24T23:59:59Z"
HAIL_TYPE = "hail"
QC_OK = ("A", "B")
SLOT_S = 300                      # PT5M misurato
PRE_MIN = 20.0                    # banco: DEFAULT_PRE_WINDOW_MIN
POST_MIN = 6.0                    # banco: DEFAULT_POST_WINDOW_MIN
MAX_KM = 10.0                     # gate dichiarato dal collaudo
Z95 = 1.959964
# Attesa dichiarata PRIMA della misura in docs/VALIDAZIONE_SEVERO.md §4.3
# (POD grandine 0,10-0,30): un esito informa solo se l'intervallo la esclude.
EXPECTED_POD = (0.10, 0.30)


def _iso(dt_obj):
    return sv.format_time_utc(dt_obj)


def _ceil_slot_ms(dt_obj):
    ms = int(dt_obj.timestamp() * 1000)
    step = SLOT_S * 1000
    return int(math.ceil(ms / float(step)) * step)


def anchor_for(event_dt):
    """Ancora di replay: ceil(evento al passo) + 5 min -> frame [T-20, T+5]."""
    return _ceil_slot_ms(event_dt) + SLOT_S * 1000


def wilson(k, n, z=Z95):
    """Intervallo di Wilson 95% per una proporzione k/n (None se n=0)."""
    if n <= 0:
        return None
    p = k / float(n)
    den = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / den
    half = (z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))) / den
    return [max(0.0, center - half), min(1.0, center + half)]


# ---------------------------------------------------------------------------
# Verita' (etichette METAR)
# ---------------------------------------------------------------------------
def read_truth(path):
    events = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") != HAIL_TYPE:
                continue
            if rec.get("qc") not in QC_OK:
                continue
            ts = rec.get("ts_utc") or ""
            if not (WINDOW_START <= ts <= WINDOW_END):
                continue
            events.append(rec)
    events.sort(key=lambda r: r["ts_utc"])
    return events


def truth_for_bench(events):
    out = []
    for rec in events:
        out.append(sv.TruthEvent(
            id=rec["event_id"],
            time_utc=sv.parse_time_utc(rec["ts_utc"]),
            lon=float(rec["lon"]), lat=float(rec["lat"]),
            category=rec.get("severity") or "hail",
            quality=rec.get("qc"),
            source=rec.get("source"),
        ))
    return out


# ---------------------------------------------------------------------------
# Esecuzione del motore in replay
# ---------------------------------------------------------------------------
def run_engine(anchor_ms, out_dir, main_py, extra=None):
    cmd = [sys.executable, main_py,
           "--as-of", _iso(dt.datetime.fromtimestamp(
               anchor_ms / 1000.0, dt.timezone.utc)),
           "--out-dir", out_dir]
    if extra:
        cmd.extend(extra)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, cwd=_ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    summary = None
    try:
        summary = json.loads(proc.stdout)
    except ValueError:
        summary = None
    return proc.returncode, summary, (proc.stderr or "")[-2000:]


def detections_from_run(struct_payload, event_id, source_path):
    """Strutture convective -> Detection del banco.

    time_utc = last_seen (istante rappresentativo, come da docstring del banco);
    first_seen/last_seen = vita della track. NIENTE filtro sui candidati: ogni
    record emesso dal motore e' un allarme (un filtro a posteriori sarebbe una
    scelta non dichiarata che migliora il risultato)."""
    out = []
    for rec in (struct_payload.get("structures") or []):
        pos = rec.get("position") or []
        if len(pos) != 2:
            continue
        lon, lat = float(pos[0]), float(pos[1])
        last = rec.get("last_seen") or rec.get("first_seen")
        first = rec.get("first_seen") or last
        if not last:
            continue
        stack = rec.get("severe_stack") or {}
        hail = rec.get("hail") or {}
        over = rec.get("overhang") or {}
        extras = {
            "event_id": event_id,
            "structure_id": rec.get("structure_id"),
            "soi": rec.get("soi"),
            "soi_v2": rec.get("soi_v2"),
            "level": rec.get("level"),
            "phase": rec.get("phase"),
            "on_latest_frame": rec.get("on_latest_frame"),
            "vil_density_g_m3": (hail.get("vil_density_g_m3") or {}).get("value"),
            "mesh_mm": (hail.get("mesh_mm") or {}).get("value"),
            "mesh_null_reason": (hail.get("mesh_mm") or {}).get("null_reason"),
            "elevated_core": (over.get("elevated_core") or {}).get("value"),
            "strong_elevated_core": (over.get("strong_elevated_core") or {}).get("value"),
            "gradient_max_dbz": (over.get("gradient_max_dbz") or {}).get("value"),
            "elevated_columns": (over.get("elevated_core") or {}).get("elevated_columns"),
            "strong_columns": (over.get("strong_elevated_core") or {}).get("strong_columns"),
            "overhang_columns": (over.get("elevated_core") or {}).get("columns"),
            "env": rec.get("env"),
            "ot": rec.get("ot"),
            "lightning": rec.get("lightning"),
            "as_of_null_reasons": rec.get("as_of_null_reasons"),
            "severe_stack_version": (stack or {}).get("version"),
            "source_file": os.path.basename(source_path),
        }
        out.append(sv.Detection(
            id=rec.get("structure_id") or "unknown",
            time_utc=sv.parse_time_utc(last),
            lon=lon, lat=lat,
            category=rec.get("level") or "structure",
            source="radar_engine.convective_structures",
            first_seen_utc=sv.parse_time_utc(first),
            last_seen_utc=sv.parse_time_utc(last),
            extras=extras,
        ))
    return out


def nearest(detections, truth_event):
    best = None
    for det in detections:
        km = sv.haversine_km(truth_event.lon, truth_event.lat, det.lon, det.lat)
        if best is None or km < best[0]:
            best = (km, det)
    return best


def cell_layer_diagnostic(storms_path, lon, lat, radius_km=MAX_KM):
    """DIAGNOSTICA (non metrica): quante CELLE hanno il centroide entro il gate.

    Il banco accoppia il CENTROIDE della struttura. Il layer celle dice se il
    mancato hit e' un'assenza di eco o un problema geometrico: le celle del
    mosaico DPC sono componenti connesse anche di migliaia di km^2, con il
    centroide lontano dal rapporto anche quando l'eco lo copre. Nessun numero
    qui entra nella matrice 2x2."""
    import json as _json
    if not os.path.exists(storms_path):
        return None
    with open(storms_path, "r", encoding="utf-8") as fh:
        payload = _json.load(fh)
    best = None
    within = 0
    total = 0
    for feat in payload.get("features") or []:
        if (feat.get("geometry") or {}).get("type") != "Point":
            continue
        total += 1
        clon, clat = feat["geometry"]["coordinates"]
        km = sv.haversine_km(lon, lat, clon, clat)
        if km <= radius_km:
            within += 1
        if best is None or km < best["km"]:
            props = feat.get("properties") or {}
            best = {"km": round(km, 3), "cell_id": props.get("cell_id"),
                    "max_dbz": props.get("max_dbz"),
                    "area_km2": props.get("area_km2"),
                    "track_id": props.get("track_id"),
                    "timestamp": props.get("timestamp")}
    return {"cells_total": total,
            "cells_within_radius": within,
            "radius_km": radius_km,
            "nearest_cell": best,
            "note": "diagnostica, NON entra nella matrice 2x2"}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def informative_outcomes(n, expected=EXPECTED_POD):
    rows = []
    for k in range(n + 1):
        iv = wilson(k, n)
        contains = (iv[0] <= expected[0] and iv[1] >= expected[1]) or \
                   (iv[0] <= expected[1] and iv[1] >= expected[0])
        rows.append({"k": k, "n": n, "pod": round(k / float(n), 4),
                     "wilson95": [round(iv[0], 4), round(iv[1], 4)],
                     "informativo": not contains})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bench replay --as-of (1.2.4.20)")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--markdown", default=None)
    ap.add_argument("--main", default=os.path.join(
        _ROOT, "scripts", "radar_engine", "main.py"))
    ap.add_argument("--skip-runs", action="store_true",
                    help="riusa le directory <work-dir>/<slug>/ gia' prodotte")
    args = ap.parse_args(argv)

    events = read_truth(args.labels)
    truth = truth_for_bench(events)
    params = sv.MatchParams(max_distance_km=MAX_KM, pre_window_min=PRE_MIN,
                            post_window_min=POST_MIN)
    os.makedirs(args.work_dir, exist_ok=True)

    detections = []
    runs = []
    for rec, ev in zip(events, truth):
        slug = rec["station"] + "_" + rec["ts_utc"].replace(":", "").replace("-", "")
        out_dir = os.path.join(args.work_dir, slug)
        anchor_ms = anchor_for(ev.time_utc)
        rc, summary = None, None
        if not args.skip_runs:
            rc, summary, err = run_engine(anchor_ms, out_dir, args.main)
        struct_path = os.path.join(out_dir, "convective_structures.json")
        payload = {}
        if os.path.exists(struct_path):
            with open(struct_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        dets = detections_from_run(payload, rec["event_id"], struct_path)
        detections.extend(dets)
        best = nearest(dets, ev)
        over_count = sum(1 for d in dets
                         if d.extras.get("elevated_core") is True)
        strong_count = sum(1 for d in dets
                           if d.extras.get("strong_elevated_core") is True)
        as_of = payload.get("as_of_mode") or (summary or {}).get("as_of_mode") or {}
        runs.append({
            "event_id": rec["event_id"],
            "station": rec.get("station"),
            "severity": rec.get("severity"),
            "detail": rec.get("detail"),
            "truth_ts": rec["ts_utc"],
            "truth_lat": rec["lat"], "truth_lon": rec["lon"],
            "anchor_iso": _iso(dt.datetime.fromtimestamp(
                anchor_ms / 1000.0, dt.timezone.utc)),
            "window_iso": [
                _iso(ev.time_utc - dt.timedelta(minutes=PRE_MIN)),
                _iso(ev.time_utc + dt.timedelta(minutes=POST_MIN)),
            ],
            "rc": rc,
            "run_status": (summary or {}).get("status"),
            "frames": (summary or {}).get("frames"),
            "cells": (summary or {}).get("cells"),
            "structures_emitted": len(dets),
            "radar_timestamp": (summary or {}).get("radar_timestamp"),
            "latency_min": (summary or {}).get("latency_min"),
            "as_of_mode": as_of,
            "nearest_km": (None if best is None else round(best[0], 3)),
            "nearest_structure": (None if best is None else best[1].extras),
            "overhang_run": {
                "structures_total": len(dets),
                "structures_with_elevated_core": over_count,
                "structures_with_strong_core": strong_count,
            },
            "cell_layer_diagnostic": cell_layer_diagnostic(
                os.path.join(out_dir, "storms.geojson"), ev.lon, ev.lat),
            "warnings": (summary or {}).get("warnings"),
        })

    result = sv.match_events(detections, truth, params)
    counts = result.counts()
    metrics = sv.compute_metrics(counts)
    pod_iv = wilson(counts.hits, counts.n_truth_events)
    csi_iv = wilson(counts.hits, counts.hits + counts.misses + counts.false_alarms)

    report = {
        "bench": "as_of_replay_hail_qc_ab",
        "generated_by": "scripts/validation/asof_bench.py",
        "params": params.to_dict(),
        "filters": {
            "type": HAIL_TYPE, "qc": list(QC_OK),
            "window": [WINDOW_START, WINDOW_END],
            "anchor_rule": "ceil(evento a 5 min) + 5 min -> frame [T-20, T+5]",
        },
        "n_events": len(events),
        "n_days": len(sorted({e["ts_utc"][:10] for e in events})),
        "n_detections": len(detections),
        "counts": counts.to_dict(),
        "metrics": metrics,
        "wilson": {
            "pod": {"k": counts.hits, "n": counts.n_truth_events,
                    "interval95": pod_iv},
            "csi": {"k": counts.hits,
                    "n": counts.hits + counts.misses + counts.false_alarms,
                    "interval95": csi_iv,
                    "caveat": ("CSI dipende dal numero di allarmi non "
                               "accoppiati, che dipende dalla copertura della "
                               "verita' (1,46% del territorio): punto e "
                               "intervallo sono indicativi, il FAR non e' "
                               "stimabile")},
        },
        "matches": [m.to_dict() for m in result.matches],
        "unmatched_truth": [t.to_dict() for t in result.unmatched_truth],
        "unmatched_detections": [d.to_dict() for d in
                                 result.unmatched_detections[:50]],
        "unmatched_detections_total": len(result.unmatched_detections),
        "informative_outcomes": informative_outcomes(len(truth)),
        "runs": runs,
        "environment_source_all_runs": sorted({
            (r.get("as_of_mode") or {}).get("environment_source") or ""
            for r in runs}),
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
    print(json.dumps({"counts": report["counts"],
                      "pod": metrics.get("pod"), "csi": metrics.get("csi"),
                      "n_detections": len(detections)}, indent=2))
    return 0


def render_markdown(report):
    c = report["counts"]
    m = report["metrics"]
    w = report["wilson"]
    lines = []
    add = lines.append
    add("# Collaudo di falsificazione in replay `--as-of` — grandine METAR QC A/B")
    add("")
    add("Generato da `scripts/validation/asof_bench.py`. Un solo collaudo, con "
        "i parametri del banco dichiarati qui sotto.")
    add("")
    add("| parametro | valore |")
    add("| :--- | :--- |")
    add("| eventi verita' | %d su %d giornate |" % (report["n_events"],
                                                    report["n_days"]))
    add("| allarmi (strutture emesse) | %d |" % report["n_detections"])
    add("| distanza massima | %s km |" % report["params"]["max_distance_km"])
    add("| finestra temporale | %s |" % report["params"]["time_window"])
    add("| regola d'ancoraggio | %s |" % report["filters"]["anchor_rule"])
    add("| matching | %s |" % report["params"]["matching"])
    add("")
    add("## Matrice 2x2")
    add("")
    add("| | numero |")
    add("| :--- | ---: |")
    add("| hit (a) | %d |" % c["hits"])
    add("| miss (c) | %d |" % c["misses"])
    add("| false alarm (b) | %d |" % c["false_alarms"])
    add("| correct negative (d) | %d |" % c["correct_negatives"])
    add("")
    add("POD = %s, Wilson 95%% = [%.3f, %.3f]" % (
        m.get("pod"), w["pod"]["interval95"][0], w["pod"]["interval95"][1]))
    add("")
    add("CSI = %s, Wilson 95%% = [%.3f, %.3f] (indicativo: vedi caveat nel JSON)" % (
        m.get("csi"), w["csi"]["interval95"][0], w["csi"]["interval95"][1]))
    add("")
    add("FAR: **non stimabile** (14 stazioni = 1,46% del territorio).")
    add("")
    add("## Esiti informativi su %d" % (report["n_events"] + 1))
    add("")
    add("| k | POD | Wilson 95% | informa? |")
    add("| ---: | ---: | :--- | :--- |")
    for row in report["informative_outcomes"]:
        add("| %d | %.3f | [%.3f, %.3f] | %s |" % (
            row["k"], row["pod"], row["wilson95"][0], row["wilson95"][1],
            "si'" if row["informativo"] else "no"))
    add("")
    add("## Per evento")
    add("")
    add("| evento | stazione | severita' | ancora | strutture | piu' vicina | "
        "entra nei 10 km |")
    add("| :--- | :--- | :--- | :--- | ---: | ---: | :--- |")
    for r in report["runs"]:
        add("| %s | %s | %s | %s | %d | %s | %s |" % (
            r["truth_ts"], r["station"], r["severity"], r["anchor_iso"],
            r["structures_emitted"],
            ("n/d" if r["nearest_km"] is None else "%.2f km" % r["nearest_km"]),
            ("n/d" if r["nearest_km"] is None
             else ("si'" if r["nearest_km"] <= MAX_KM else "no"))))
    add("")
    add("## Overhang: large vs small hail")
    add("")
    add("Struttura PIU' VICINA alla stazione di ogni run (distanza in tabella "
        "sopra: 20-67 km, quindi l'associazione con l'evento e' DEBOLE e "
        "dichiarata). Soglie invariate (45/50 dBZ).")
    add("")
    add("| evento | severita' | entro 10 km | elevated_core | strong | "
        "grad_max dBZ | colonne valutate | colonne elevate | VIL dens. | MESH |")
    add("| :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | :--- |")
    for r in report["runs"]:
        near = r.get("nearest_structure") or {}
        inside = (r["nearest_km"] is not None and r["nearest_km"] <= MAX_KM)
        add("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["station"], r["severity"], "si'" if inside else "no",
            near.get("elevated_core"), near.get("strong_elevated_core"),
            near.get("gradient_max_dbz"), near.get("overhang_columns"),
            near.get("elevated_columns"),
            (None if near.get("vil_density_g_m3") is None
             else round(near["vil_density_g_m3"], 2)),
            near.get("mesh_null_reason") or near.get("mesh_mm")))
    add("")
    add("Strutture con `elevated_core=true` sull'INTERA run (non solo la piu' "
        "vicina):")
    add("")
    add("| evento | severita' | strutture | con nucleo elevato | con nucleo forte |")
    add("| :--- | :--- | ---: | ---: | ---: |")
    for r in report["runs"]:
        o = r.get("overhang_run") or {}
        add("| %s | %s | %s | %s | %s |" % (
            r["station"], r["severity"], o.get("structures_total"),
            o.get("structures_with_elevated_core"),
            o.get("structures_with_strong_core")))
    add("")
    add("## Diagnostica del layer CELLE (NON entra nella matrice 2x2)")
    add("")
    add("| evento | celle | con centroide entro 10 km | centroide piu' vicino | "
        "max_dbz | area km2 |")
    add("| :--- | ---: | ---: | ---: | ---: | ---: |")
    for r in report["runs"]:
        d = r.get("cell_layer_diagnostic") or {}
        n = d.get("nearest_cell") or {}
        add("| %s | %s | %s | %s km | %s | %s |" % (
            r["station"], d.get("cells_total"), d.get("cells_within_radius"),
            n.get("km"), n.get("max_dbz"), n.get("area_km2")))
    add("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
