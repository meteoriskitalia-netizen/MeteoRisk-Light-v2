#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestratore della validazione scientifica di morphology_organization.

SOTTOTASK:
  plan       — piano di raccolta, SOLO calcoli su misure reali (nessuna rete)
  single     — UN slot reale: engine --validate-3d + etichetta AFA + JSONL
  campaign   — tutti gli slot richiesti (richiama `single`), poi finalize
  finalize   — eventi per-evento, split cronologico, calibrazione SOLO su
               train, valutazione su test inviolato, gate di numerosita'

REGOLE APPLICATE:
  * nessun dato simulato nel sistema operativo: tutti i valori arrivano da
    DPC/EUMETSAT o dall'engine; i sintetici stanno solo nei test;
  * nessun push, nessuna modifica a html/VERSION/sw.js/workflow;
  * il flag --validate-3d e' passato all'engine, che staticamente non cambia
    nulla nei run senza flag;
  * se una fonte non e' raggiungibile il comando riporta l'errore con motivo
    e non produce metriche inventate.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

try:
    from . import validate3d_afa as afa
    from . import validate3d_events as evs
    from . import validate3d_metrics as met
except ImportError:  # esecuzione come script diretto
    import validate3d_afa as afa
    import validate3d_events as evs
    import validate3d_metrics as met

UTC = _dt.timezone.utc

# MISURATI il 2026-11-17 con POST /downloadProduct + GET pre-signed su
# https://radar-api.protezionecivile.it (Origin radar.protezionecivile.it).
# Sono MISURE, non stime: 3 slot reali (2026-11-16, 2026-10-20, 2026-07-01).
MEASURED_DPC = [
    {"slot": "2026-11-16T12:00:00Z", "product": "VMI", "bytes": 503670, "s": 0.56},
    {"slot": "2026-11-16T12:00:00Z", "product": "CAPPI_1", "bytes": 455686, "s": 0.65},
    {"slot": "2026-11-16T12:00:00Z", "product": "CAPPI_10", "bytes": 463290, "s": 0.45},
    {"slot": "2026-10-20T12:00:00Z", "product": "VMI", "bytes": 699818, "s": 0.57},
    {"slot": "2026-10-20T12:00:00Z", "product": "CAPPI_1", "bytes": 572248, "s": 0.59},
    {"slot": "2026-10-20T12:00:00Z", "product": "CAPPI_10", "bytes": 522738, "s": 0.47},
    {"slot": "2026-07-01T14:00:00Z", "product": "VMI", "bytes": 692376, "s": 0.73},
    {"slot": "2026-07-01T14:00:00Z", "product": "CAPPI_1", "bytes": 536354, "s": 0.58},
    {"slot": "2026-07-01T14:00:00Z", "product": "CAPPI_10", "bytes": 614830, "s": 0.67},
]
# Misura di produzione gia' in config.py: 10 CAPPI in parallelo = 9,75 s
# (69,97 s seriali, 7,17x), zero 429 a 6 connessioni. Valore usato nel piano.
MEASURED_PARALLEL_CAPPI_S = 9.75
AFA_THROTTLE_S = 3.0
AFA_SLOTS_PER_CELL = 2 * int(round(30.0 / 5.0)) + 1  # 13, finestra +/-30 min

DEFAULT_THRESHOLDS = {
    "echo_top_min_m": 6000.0,
    "vertical_extent_min_km": 4.0,
    "vil_density_min_g_m3": 3.5,
}
CAL_GRID = {
    "echo_top_min_m": (5000.0, 6000.0, 7000.0),
    "vertical_extent_min_km": (3.0, 4.0, 5.0),
    "vil_density_min_g_m3": (3.0, 3.5, 4.5),
}

SOI_ORGANIZED_LEVELS = ("organized", "strong", "intense")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def plan(start_date, days, step_min):
    """Piano di raccolta da MISURE reali. Nessuna rete, nessun file scritto."""
    cappi = [m["bytes"] for m in MEASURED_DPC if m["product"].startswith("CAPPI")]
    slots_day = int(round(24.0 * 60.0 / float(step_min)))
    n_slots = slots_day * int(days)
    per_set_min, per_set_max = min(cappi) * 10, max(cappi) * 10
    total_min, total_max = per_set_min * n_slots, per_set_max * n_slots
    seconds = n_slots * MEASURED_PARALLEL_CAPPI_S
    end = (_dt.date.fromisoformat(start_date) + _dt.timedelta(days=int(days) - 1))
    return {
        "start_date": start_date, "end_date_exclusive": str(end),
        "days": int(days), "slot_step_min": int(step_min),
        "slots_per_day": slots_day, "total_engine_runs": n_slots,
        "cappi_per_run": 10,
        "measured_cappi_bytes_range": [min(cappi), max(cappi)],
        "bytes_per_run_range": [per_set_min, per_set_max],
        "total_cappi_bytes_range": [total_min, total_max],
        "total_cappi_gib_range": [round(total_min / 1024 ** 3, 2),
                                   round(total_max / 1024 ** 3, 2)],
        "parallel_download_seconds": round(seconds, 1),
        "parallel_download_hours": round(seconds / 3600.0, 1),
        "afa_requests_upper_bound_per_cell": AFA_SLOTS_PER_CELL,
        "afa_throttle_s": AFA_THROTTLE_S,
        "afa_requests_formula": (
            "slot AFA unici = unione dei 13 slot nelle finestre delle celle "
            "con etichetta; upper bound = min(%d, 13 * n_celle). Con cache "
            "per slot il numero reale e' molto minore." % (slots_day)),
        "values_NOT_measured": [
            "numero di celle/strutture e di eventi nei 30 giorni "
            "(dipende dalla stagione: da misurare durante la raccolta)",
            "numero esatto di slot AFA unici (dipende dalle celle trovate)",
            "tempo totale di calcolo dell'engine per slot (misurato nei singoli run)",
        ],
        "budget_note": (
            "Il brief dichiara ~400 MB/giorno e 25-49 min/giorno. Le MISURE "
            "real qui sopra danno ~1,4-1,8 GB/giorno a cadenza 5 min "
            "(288 set/giorno x 10 CAPPI x 0,46-0,61 MB); i 25-49 min/giorno "
            "sono invece coerenti con 288 set x 9,75 s. I 400 MB/giorno "
            "corrispondono a ~72 set/giorno, cioe' uno ogni 20 minuti: "
            "usare --slot-step-min 20 per restare entro il budget "
            "dichiarato, accettando una perdita di risoluzione temporale."),
        "source": "DPC /downloadProduct misurato il 2026-11-17; parallelo 9,75 s da config.py",
    }


# ---------------------------------------------------------------------------
# single slot (reale)
# ---------------------------------------------------------------------------
def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _parse_engine_stdout(text):
    """Estrae il riepilogo JSON stampato alla fine dall'engine (pretty print)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    idx = text.rfind("{")
    while idx >= 0:
        try:
            return json.loads(text[idx:])
        except (ValueError, TypeError):
            idx = text.rfind("{", 0, idx)
    return None


def _structure_rows(slot_dir, slot_iso):
    """Righe di osservazione da un run dell'engine. Null + motivo, mai 0."""
    payload = _read_json(os.path.join(slot_dir, "convective_structures.json"),
                         {}) or {}
    latest = _read_json(os.path.join(slot_dir, "latest.json"), {}) or {}
    cf = latest.get("cappi_fetch") or {}
    v3d = cf.get("validate_3d") or {}
    cf_gate = cf.get("hail_gate") or {}
    cf_counter = cf_gate.get("validation_counterfactual") or {}
    gate_passed = cf_counter.get("passed")
    baseline_levels = 10 if gate_passed else 2
    rows = []
    for st in payload.get("structures") or []:
        pos = st.get("position") or []
        if len(pos) < 2:
            continue
        # CONVENZIONE MISURATA: structure.position = [lon, lat]
        # (es. [13.92339, 45.09728] e' Modena). Leggerlo come [lat, lon]
        # porterebbe l'etichetta AFA nell'Oceano Indiano -> falsi negativi.
        st_lon, st_lat = float(pos[0]), float(pos[1])
        morph = st.get("morphology_organization") or {}
        level = st.get("level") or ""
        if not level:
            soi_block = st.get("soi_v2") or st.get("soi") or {}
            if isinstance(soi_block, dict):
                level = (soi_block.get("level")
                         or soi_block.get("classification") or "")
        level = str(level).lower()
        cls = morph.get("classification")
        rows.append({
            "slot_iso": slot_iso,
            "ts_ms": evs.iso_to_ms(slot_iso),
            "structure_id": st.get("structure_id") or st.get("id"),
            "lat": st_lat, "lon": st_lon,
            "soi_level": level,
            "baseline_organized": int(level in SOI_ORGANIZED_LEVELS),
            "new_organized": (None if cls is None
                              else int(cls == "organized_vertical")),
            "morphology_organization": morph,
            "volume_available": (
                bool(vol.get("volume_available"))
                if isinstance((vol := latest.get("volume")), dict) else None),
            "cappi_levels": cf.get("cappi_levels"),
            "baseline_cappi_levels": baseline_levels,
            "baseline_field_available": bool(baseline_levels >= 4),
            "validate_3d": bool(v3d),
            "values": morph.get("values") or {},
        })
    return rows, {
        "structures": len(payload.get("structures") or []),
        "cappi_levels": cf.get("cappi_levels"),
        "cappi_bytes": cf.get("bytes"),
        "cappi_seconds": cf.get("seconds"),
        "cappi_fetched": cf.get("fetched"),
        "validation_mode": bool(v3d),
        "baseline_cappi_levels": baseline_levels,
        "gate_would_have_passed": gate_passed,
    }


def run_single(slot_iso, out_dir, scripts_dir, python_exe,
               *, with_afa=True, cache_dir=None, throttle_s=AFA_THROTTLE_S,
               timeout_s=1800, grid=1300):
    """Esegue UN slot reale. Ritorna (rows, report). Non solleva su guasti."""
    scripts_dir = os.path.abspath(scripts_dir)
    # Il processo COLLECTOR importa i decoder AFA da radar_engine: la stessa
    # `scripts_dir` usata dal subprocess va messa sul suo sys.path, altrimenti
    # l'etichettatura fallisce anche se l'engine ha girato correttamente.
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    engine = os.path.join(scripts_dir, "radar_engine", "main.py")
    slot_dir = os.path.join(out_dir, "slots", slot_iso.replace(":", "-"))
    os.makedirs(slot_dir, exist_ok=True)
    report = {
        "slot_iso": slot_iso, "engine_path": engine,
        "slot_dir": slot_dir, "engine_returncode": None,
        "engine_error": None, "engine_summary": None,
        "rows": 0, "afa_labeled": 0, "afa_null": 0,
    }
    if not os.path.isfile(engine):
        report["engine_error"] = "engine_not_found:" + engine
        return [], report
    env = dict(os.environ)
    env["PYTHONPATH"] = scripts_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [python_exe, engine, "--as-of", slot_iso,
           "--validate-3d", "--out-dir", slot_dir]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True,
                            env=env, timeout=timeout_s,
                            encoding="utf-8", errors="replace")
        report["engine_returncode"] = cp.returncode
        report["engine_summary"] = _parse_engine_stdout(cp.stdout)
        with open(os.path.join(slot_dir, "_engine_stdout.txt"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(cp.stdout or "")
        with open(os.path.join(slot_dir, "_engine_stderr.txt"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(cp.stderr or "")
        if cp.returncode != 0:
            report["engine_error"] = "rc={}:{}".format(
                cp.returncode, (cp.stderr or "")[-300:])
    except Exception as exc:
        report["engine_error"] = "{}:{}".format(exc.__class__.__name__, exc)
        return [], report

    rows, detail = _structure_rows(slot_dir, slot_iso)
    report.update({"rows": len(rows), "detail": detail})
    if with_afa:
        cache = cache_dir or os.path.join(out_dir, "afa_cache")
        for r in rows:
            lab = afa.label_cell(
                r["lat"], r["lon"], r["ts_ms"], cache_dir=cache,
                throttle_s=throttle_s, grid=grid)
            r.update(lab)
            r["label_afa"] = lab["label_afa"]
            if lab["label_afa"] is None:
                report["afa_null"] += 1
            else:
                report["afa_labeled"] += 1
    return rows, report


def write_slot(out_dir, slot_iso, rows, report):
    slot_dir = report.get("slot_dir") or os.path.join(
        out_dir, "slots", slot_iso.replace(":", "-"))
    os.makedirs(slot_dir, exist_ok=True)
    with open(os.path.join(slot_dir, "observations.jsonl"), "w",
              encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with open(os.path.join(slot_dir, "slot_report.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
    return slot_dir


# ---------------------------------------------------------------------------
# calibrate (TRAIN ONLY) + finalize (TEST inviolato)
# ---------------------------------------------------------------------------
def classify_values(values, thr):
    """Classificazione morfologica esplicita dai 3 valori 3D. None se mancano."""
    et = values.get("echo_top_m")
    ve = values.get("vertical_extent_km")
    vd = values.get("vil_density_g_m3")
    if et is None or ve is None or vd is None:
        return None
    try:
        return int(float(et) >= float(thr["echo_top_min_m"])
                   and float(ve) >= float(thr["vertical_extent_min_km"])
                   and float(vd) >= float(thr["vil_density_min_g_m3"]))
    except (TypeError, ValueError):
        return None


def _score(train_rows, thr):
    tp = fp = fn = tn = 0
    for r in train_rows:
        if r.get("label_afa") is None:
            continue
        pred = classify_values(r.get("values") or {}, thr)
        if pred is None:
            continue
        y = int(bool(r["label_afa"]))
        if y and pred:
            tp += 1
        elif y and not pred:
            fn += 1
        elif not y and pred:
            fp += 1
        else:
            tn += 1
    c = met.binary_counts(tp, fp, fn, tn)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "f1": c["f1"]}


def calibrate_on_train(train_rows, min_support=10):
    """Grid search LIMITATA su TRAIN. Il test NON viene mai letto qui."""
    import itertools
    scored = []
    keys = ("echo_top_min_m", "vertical_extent_min_km", "vil_density_min_g_m3")
    for combo in itertools.product(*(CAL_GRID[k] for k in keys)):
        thr = dict(zip(keys, combo))
        s = _score(train_rows, thr)
        if s["tp"] + s["fp"] + s["fn"] + s["tn"] < int(min_support):
            continue
        scored.append({"thresholds": thr, "train": s})
    scored.sort(key=lambda x: (x["train"]["f1"] is not None,
                               x["train"]["f1"] or -1.0,
                               x["train"]["tp"]), reverse=True)
    return {
        "selected_thresholds": (scored[0]["thresholds"] if scored
                                else dict(DEFAULT_THRESHOLDS)),
        "selected_train_f1": (scored[0]["train"]["f1"] if scored else None),
        "n_combinations": len(scored),
        "grid": {k: list(v) for k, v in CAL_GRID.items()},
        "min_support": int(min_support),
        "dataset_used": "TRAIN ONLY (split cronologico per evento)",
        "test_touched": False,
        "no_selection_if_empty": "nessuna combo con supporto -> default dichiarato",
    }


def finalize(out_dir, *, train_fraction=0.7, gap_min=180.0, link_km=80.0):
    """Legge TUTTE le osservazioni, costruisce eventi, calibra su train e
    valuta su test. Ritorna (report, test_rows)."""
    paths = []
    for root, _dirs, files in os.walk(os.path.join(out_dir, "slots")):
        if "observations.jsonl" in files:
            paths.append(os.path.join(root, "observations.jsonl"))
    paths.sort()
    rows = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # riga corrotta contata, non inventata
    band_values(rows)  # bande dichiarate, non tarate sull'etichetta
    events, ev_meta = evs.group_events(
        rows, gap_min=gap_min, link_km=link_km)
    split = evs.chronological_split(events, train_fraction=train_fraction)
    report = {
        "observations": len(rows), "event_meta": ev_meta,
        "event_stats": evs.event_stats(events),
        "split": split,
        "calibration": None, "test": None, "minimum_sample": None,
        "status": "incomplete",
    }
    if not split["passed"]:
        report["reason"] = "split_non_valido:" + str(split["reason"])
        return report, []
    train_ids, test_ids = set(split["train_events"]), set(split["test_events"])
    train_rows = [r for e in events if e["event_id"] in train_ids
                  for r in e["rows"]]
    test_rows = [r for e in events if e["event_id"] in test_ids
                 for r in e["rows"]]
    cal = calibrate_on_train(train_rows)
    report["calibration"] = cal
    thr = cal["selected_thresholds"]
    for r in test_rows:
        r["new_organized_calibrated"] = classify_values(
            r.get("values") or {}, thr)

    # (b) classificazione organized_vertical: regola SHIPPED su test inviolato
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for r in test_rows:
        if r.get("label_afa") is None or r.get("new_organized") is None:
            continue
        y = int(bool(r["label_afa"]))
        d = int(bool(r["new_organized"]))
        k = ("tp" if y and d else "fn" if y else "fp" if d else "tn")
        counts[k] += 1
    metrics = met.binary_counts(**counts)
    gate = met.minimum_sample(metrics)
    report["test"] = {
        "n_rows": len(test_rows),
        "n_labeled": sum(counts.values()),
        "shipped_rule": metrics,
        "calibrated_on_train_rule": met.paired_baseline_compare(
            test_rows, baseline_key="new_organized",
            new_key="new_organized_calibrated"),
        "baseline_vs_new": met.paired_baseline_compare(test_rows),
        "reliability_by_band": {
            "echo_top_m": met.reliability_by_band(
                test_rows, band_key="echo_top_band"),
            "vil_density_g_m3": met.reliability_by_band(
                test_rows, band_key="vil_density_band"),
        },
        "field_continuity": met.field_continuity(
            [e for e in events if e["event_id"] in test_ids]),
    }
    report["minimum_sample"] = gate
    report["status"] = ("complete" if gate["passed"] else "incomplete")
    report["incomplete_reason"] = (None if gate["passed"]
                                   else "; ".join(gate["reasons"]))
    return report, test_rows


def band_values(rows):
    """Aggiunge bande dichiarate (non tarate) per la affidabilita' per banda."""
    for r in rows:
        v = r.get("values") or {}
        et, vd = v.get("echo_top_m"), v.get("vil_density_g_m3")
        r["echo_top_band"] = (None if et is None
                              else "ge6km" if float(et) >= 6000
                              else "3-6km" if float(et) >= 3000 else "lt3km")
        r["vil_density_band"] = (None if vd is None
                                 else "ge3.5" if float(vd) >= 3.5
                                 else "lt3.5")
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--start-date", default="2026-10-19")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--slot-step-min", type=int, default=5)

    s = sub.add_parser("single")
    s.add_argument("--slot", required=True, metavar="ISO8601")
    s.add_argument("--out-dir", required=True)
    s.add_argument("--scripts-dir", required=True)
    s.add_argument("--python", default=sys.executable)
    s.add_argument("--no-afa", action="store_true")
    s.add_argument("--cache-dir", default=None)
    s.add_argument("--grid", type=int, default=1300)
    s.add_argument("--timeout-s", type=int, default=1800)

    c = sub.add_parser("campaign")
    c.add_argument("--start-date", required=True)
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--slot-step-min", type=int, default=5)
    c.add_argument("--out-dir", required=True)
    c.add_argument("--scripts-dir", required=True)
    c.add_argument("--python", default=sys.executable)
    c.add_argument("--no-afa", action="store_true")
    c.add_argument("--resume", action="store_true")
    c.add_argument("--timeout-s", type=int, default=1800)

    f = sub.add_parser("finalize")
    f.add_argument("--out-dir", required=True)
    f.add_argument("--train-fraction", type=float, default=0.7)
    f.add_argument("--gap-min", type=float, default=180.0)
    f.add_argument("--link-km", type=float, default=80.0)

    args = ap.parse_args(argv)
    if args.cmd == "plan":
        print(json.dumps(plan(args.start_date, args.days,
                              args.slot_step_min), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "single":
        rows, report = run_single(
            args.slot, args.out_dir, args.scripts_dir, args.python,
            with_afa=not args.no_afa, cache_dir=args.cache_dir,
            timeout_s=args.timeout_s, grid=args.grid)
        write_slot(args.out_dir, args.slot, rows, report)
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0 if report.get("engine_returncode") == 0 else 2
    if args.cmd == "campaign":
        start = _dt.date.fromisoformat(args.start_date)
        step_ms = int(args.slot_step_min) * 60000
        t0 = int(_dt.datetime.combine(
            start, _dt.time(0, 0, tzinfo=UTC)).timestamp() * 1000)
        reports = []
        for k in range(int(args.days) * int(1440 / args.slot_step_min)):
            ms = t0 + k * step_ms
            iso = _dt.datetime.fromtimestamp(ms / 1000.0, UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            slot_dir = os.path.join(args.out_dir, "slots",
                                    iso.replace(":", "-"))
            if args.resume and os.path.isfile(
                    os.path.join(slot_dir, "slot_report.json")):
                continue
            rows, rep = run_single(
                iso, args.out_dir, args.scripts_dir, args.python,
                with_afa=not args.no_afa, timeout_s=args.timeout_s)
            write_slot(args.out_dir, iso, rows, rep)
            reports.append(rep)
            print(json.dumps({"slot": iso, "rows": rep["rows"],
                              "error": rep.get("engine_error")},
                             ensure_ascii=False), flush=True)
        final, _ = finalize(args.out_dir)
        with open(os.path.join(args.out_dir, "validation_report.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(final, fh, indent=2, ensure_ascii=False, default=str)
        print(json.dumps(final, indent=2, ensure_ascii=False, default=str))
        return 0
    if args.cmd == "finalize":
        final, _ = finalize(args.out_dir,
                            train_fraction=args.train_fraction,
                            gap_min=args.gap_min, link_km=args.link_km)
        with open(os.path.join(args.out_dir, "validation_report.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(final, fh, indent=2, ensure_ascii=False, default=str)
        print(json.dumps(final, indent=2, ensure_ascii=False, default=str))
        return 0 if final["status"] == "complete" else 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
