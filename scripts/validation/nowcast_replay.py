#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk — replay del nowcast del campo di pioggia (SRI DPC) vs persistenza.

Scopo: misurare, su fotogrammi SRI REALI gia' pubblicati, se il campo
trasportato dal flusso ottico batte la PERSISTENZA (campo fermo) a +15/+30/+60
minuti. Non e' un run operativo: non scrive `latest.json`, non tocca `data/radar`
se non con `--out` esplicito, e non inventa punteggi.

Come funziona
-------------
Per ogni ancora T (passo `--step-min`, default 60):
  1) scarica gli ultimi 6 fotogrammi SRI fino a T (passo 5 min) con il
     downloader del motore (`fetch.fetch_frames`, product="SRI");
  2) calcola il nowcast con `nowcast_field.compute` (stesso codice del run);
  3) scarica i fotogrammi OSSERVATI a T+15, T+30, T+60;
  4) confronta nowcast e persistenza (campo di T) con l'osservato, sul dominio
     radar, alle soglie 0.5 / 2.0 / 5.0 mm/h.

Un caso entra nella statistica solo se l'osservato a quel lead ha almeno
`--min-rain-pixels` pixel >= 0.5 mm/h: un giorno senza pioggia non produce
punteggi (sarebbero tutti "correct negative").

Soglie di pubblicazione (dichiarate, non aggiustate dopo):
  - `beats_persistence` viene calcolato se i casi validi sono >= 5;
  - i punteggi (CSI/POD/FAR) sono pubblicati solo con >= 20 casi validi e
    >= 10.000 pixel di pioggia osservata accumulati;
  - regola di decisione: media del CSI pooled sui lead 15/30/60 e sulle soglie
    0.5/2.0 mm/h; il nowcast batte la persistenza se la media e' MAGGIORE.

Uso:
  python scripts/validation/nowcast_replay.py \
      --start 2026-11-22T06:25Z --end 2026-11-24T06:25Z --step-min 60 \
      --out data/radar/nowcast_verification.json \
      --full-out audit/nowcast_replay_full.json \
      --report docs/NOWCAST_REPLAY.md
"""

import argparse
import collections
import datetime as _dt
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar_engine import clock as clock_mod                     # noqa: E402
from radar_engine import fetch as fetch_mod                     # noqa: E402
from radar_engine import nowcast_field as nf                    # noqa: E402
from radar_engine.config import load_config                     # noqa: E402

ISO = "%Y-%m-%dT%H:%M:%SZ"


def _iso(ms):
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0,
                                      _dt.timezone.utc).strftime(ISO)


def _parse_ms(value):
    ms, _assumed = clock_mod.parse_iso8601_utc(value)
    return int(ms)


class _Bundle(object):
    """Bundle minimo per `nowcast_field.compute` (solo dpc_bundle)."""

    def __init__(self, rasters):
        self.dpc_bundle = {"entries": {
            int(rd.time_ms): {"sri": {"raster": rd}} for rd in rasters}}
        self.warnings = []
        self._nowcast_verification = {}


class FrameCache(object):
    """Cache LRU di raster SRI (evita di riscaricare la stessa slot)."""

    def __init__(self, config, cap=30):
        self.config = config
        self.cap = int(cap)
        self.data = collections.OrderedDict()
        self.downloads = 0

    def get(self, ts):
        if ts in self.data:
            self.data.move_to_end(ts)
            return self.data[ts]
        rasters, _w, _t = fetch_mod.fetch_frames(
            self.config, product="SRI", max_frames=1, anchor_ms=int(ts))
        self.downloads += len(rasters)
        rd = rasters[-1] if rasters else None
        self.data[ts] = rd
        while len(self.data) > self.cap:
            self.data.popitem(last=False)
        return rd

    def window(self, end_ms, n=6, step_ms=300000):
        out = []
        for k in range(n - 1, -1, -1):
            ts = int(end_ms) - k * step_ms
            rd = self.get(ts)
            if rd is not None:
                out.append(rd)
        return out


def evaluate_case(bundle, frames, config, clock, observed, min_rain_pixels):
    """Un caso: conteggi per (lead, soglia) per nowcast e persistenza."""
    payload, native = nf.compute(bundle, config, clock,
                                 list(bundle.warnings), out_dir=None)
    if not payload.get("available"):
        return {"status": "skipped", "null_reason": payload.get("null_reason"),
                "source_slot": payload.get("source_slot")}
    latest = frames[-1]
    pers, pvalid = nf._rain_array(latest)
    domain = native["_domain"]
    rows = {}
    # VERIFICA UFFICIALE: solo 15/30/60. Gli altri 9 frame del player non
    # entrano nel confronto con la persistenza (dichiarato nel docstring).
    for lead in nf.OFFICIAL_LEADS:
        obs_rd = observed.get(lead)
        if obs_rd is None:
            continue
        obs, ovalid = nf._rain_array(obs_rd)
        dom = domain & ovalid & pvalid
        if float((obs[dom] >= 0.5).sum()) < int(min_rain_pixels):
            continue
        pred = native[lead]
        for thr in nf.VERIFY_THRESHOLDS_MMH:
            key = "%d|%.1f" % (lead, thr)
            rows[key] = {
                "nowcast": nf.contingency(pred, obs, dom, thr),
                "persistence": nf.contingency(pers, obs, dom, thr),
                "rain_pixels": int((obs[dom] >= 0.5).sum()),
            }
    if not rows:
        return {"status": "skipped", "null_reason": "no_observed_rain",
                "source_slot": payload.get("source_slot")}
    return {"status": "ok", "source_slot": payload.get("source_slot"),
            "leads": rows}


def _mean(values):
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def aggregate(cases, min_cases=5, publish_min_cases=20,
              min_rain_pixels=10000):
    """Pooled counts + CSI per lead/soglia + regola di decisione."""
    totals = collections.defaultdict(lambda: {"nowcast": [0, 0, 0, 0],
                                              "persistence": [0, 0, 0, 0],
                                              "rain_pixels": 0,
                                              "case_csi_nowcast": [],
                                              "case_csi_persistence": []})
    valid_cases = 0
    # I pixel di pioggia osservata vanno contati UNA volta per (caso, lead):
    # le righe sono per soglia, quindi sommarle direttamente gonfierebbe il
    # totale di 3x (tante quante le soglie).
    rain_total = 0
    seen_rain = set()
    for case_idx, case in enumerate(cases):
        if case.get("status") != "ok":
            continue
        valid_cases += 1
        for key, row in case["leads"].items():
            lead = key.split("|")[0]
            t = totals[key]
            for arm in ("nowcast", "persistence"):
                for i in range(4):
                    t[arm][i] += int(row[arm][i])
            t["rain_pixels"] += int(row["rain_pixels"])
            if (case_idx, lead) not in seen_rain:
                seen_rain.add((case_idx, lead))
                rain_total += int(row["rain_pixels"])
            sn = nf.scores(row["nowcast"])["csi"]
            sp = nf.scores(row["persistence"])["csi"]
            if sn is not None:
                t["case_csi_nowcast"].append(sn)
            if sp is not None:
                t["case_csi_persistence"].append(sp)

    leads = {}
    for key in sorted(totals, key=lambda k: (int(k.split("|")[0]),
                                             float(k.split("|")[1]))):
        lead, thr = key.split("|")
        t = totals[key]
        leads[key] = {
            "lead_min": int(lead), "threshold_mmh": float(thr),
            "rain_pixels": int(t["rain_pixels"]),
            "nowcast": nf.scores(t["nowcast"]),
            "persistence": nf.scores(t["persistence"]),
            "cases_with_rain": len(t["case_csi_nowcast"]),
            "mean_case_csi_nowcast": _mean(t["case_csi_nowcast"]),
            "mean_case_csi_persistence": _mean(t["case_csi_persistence"]),
        }

    decision_keys = ["%d|%.1f" % (l, t) for l in nf.VERIFY_DECISION_LEADS
                     for t in nf.VERIFY_DECISION_THRESHOLDS]
    n_mean = _mean([leads[k]["nowcast"]["csi"] for k in decision_keys
                    if k in leads])
    p_mean = _mean([leads[k]["persistence"]["csi"] for k in decision_keys
                    if k in leads])
    publish = (valid_cases >= publish_min_cases
               and rain_total >= min_rain_pixels)
    beats = None
    if valid_cases >= min_cases and n_mean is not None and p_mean is not None:
        beats = bool(n_mean > p_mean)
    status = "ok" if publish else (
        "insufficient_sample" if beats is not None else "not_evaluable")
    return {
        "status": status,
        "beats_persistence": beats,
        "null_reason": None if beats is not None else "no_valid_rain_cases",
        "cases_total": len(cases),
        "cases_with_rain": valid_cases,
        "rain_pixels_total": rain_total,
        "publish_scores": bool(publish),
        "publish_rule": {
            "min_cases_for_decision": int(min_cases),
            "min_cases_for_scores": int(publish_min_cases),
            "min_rain_pixels_for_scores": int(min_rain_pixels),
        },
        "decision_rule": {
            "leads_min": list(nf.VERIFY_DECISION_LEADS),
            "thresholds_mmh": list(nf.VERIFY_DECISION_THRESHOLDS),
            "statistic": "mean pooled CSI over leads x thresholds",
            "nowcast_mean_csi": n_mean,
            "persistence_mean_csi": p_mean,
            "delta": (None if (n_mean is None or p_mean is None)
                      else n_mean - p_mean),
        },
        "leads": (leads if publish else {}),
        "scores_hidden_reason": (
            None if publish else
            "sample insufficient: %d/%d cases, %d/%d rain pixels" % (
                valid_cases, publish_min_cases, rain_total,
                min_rain_pixels)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay nowcast SRI vs persistenza")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--step-min", type=int, default=60)
    ap.add_argument("--min-rain-pixels", type=int, default=100)
    ap.add_argument("--out", default=None,
                    help="JSON COMPATTO (senza i casi) per data/radar")
    ap.add_argument("--full-out", default=None,
                    help="JSON completo con un record per ancora (audit)")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    config = load_config()
    config["source"]["fetch_workers"] = int(
        config["source"].get("fetch_workers") or 6)
    start, end = _parse_ms(args.start), _parse_ms(args.end)
    step = int(args.step_min) * 60000
    cache = FrameCache(config)
    cases = []
    anchors = list(range(start, end + 1, step))
    t0 = time.time()
    for i, anchor in enumerate(anchors, 1):
        frames = cache.window(anchor)
        if len(frames) < 3:
            cases.append({"anchor": _iso(anchor), "status": "skipped",
                          "null_reason": "sri_frames_lt_3"})
            print("[%d/%d] %s skip (frames<3)" % (i, len(anchors),
                                                  _iso(anchor)))
            continue
        clk = clock_mod.Clock.at_ms(int(frames[-1].time_ms))
        observed = {}
        for lead in nf.OFFICIAL_LEADS:
            observed[lead] = cache.get(int(frames[-1].time_ms) + lead * 60000)
        bundle = _Bundle(frames)
        try:
            res = evaluate_case(bundle, frames, config, clk, observed,
                                args.min_rain_pixels)
        except Exception as exc:                  # un caso rotto non ferma il run
            res = {"status": "error",
                   "null_reason": "%s:%s" % (exc.__class__.__name__, exc)}
        res["anchor"] = _iso(anchor)
        res["warnings"] = list(bundle.warnings)[:10]
        cases.append(res)
        print("[%d/%d] %s %s" % (i, len(anchors), _iso(anchor),
                                 res.get("status")))
    elapsed = time.time() - t0
    result = aggregate(cases, min_cases=nf.VERIFY_MIN_CASES,
                       publish_min_cases=nf.VERIFY_PUBLISH_MIN_CASES,
                       min_rain_pixels=nf.VERIFY_MIN_RAIN_PIXELS)
    result.update({
        "schema": "meteorisk.nowcast_verification/1",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime(ISO),
        "window": {"start": _iso(start), "end": _iso(end),
                   "anchor_step_min": int(args.step_min)},
        "source": nf.SOURCE,
        "motion_algorithm": nf.MOTION_ALGORITHM,
        "speed_cap_kmh": nf.SPEED_CAP_KMH,
        "min_rain_pixels_per_case_lead": int(args.min_rain_pixels),
        "seconds": round(elapsed, 1),
        "sri_downloads": cache.downloads,
        "cases": cases,
    })
    text = json.dumps(result, ensure_ascii=False, indent=1, default=float)
    if args.full_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.full_out)),
                    exist_ok=True)
        tmp = args.full_out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, args.full_out)
        print("scritto", args.full_out,
              "(%d byte)" % os.path.getsize(args.full_out))
    if args.out:
        # COMPATTO: il file va in data/radar e viene incorporato nel nowcast
        # di ogni run; i record per ancora restano nell'audit (--full-out).
        compact = {k: v for k, v in result.items() if k != "cases"}
        ctext = json.dumps(compact, ensure_ascii=False, indent=1,
                           default=float)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(ctext)
        os.replace(tmp, args.out)
        print("scritto", args.out, "(%d byte)" % os.path.getsize(args.out))
    if not args.out and not args.full_out:
        print(text)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(_markdown(result))
        print("scritto", args.report)
    return 0


def _markdown(result):
    beats = result["beats_persistence"]
    lines = ["# Nowcast del campo di pioggia (SRI) — replay vs persistenza", ""]
    lines += ["Generato: `%s` · finestra `%s` → `%s` (passo %d min)" % (
        result["generated_at"], result["window"]["start"],
        result["window"]["end"], result["window"]["anchor_step_min"]), ""]
    lines += ["- casi totali: **%d**, con pioggia osservata: **%d**" % (
        result["cases_total"], result["cases_with_rain"]),
        "- pixel di pioggia osservata accumulati: **%d**" % (
            result["rain_pixels_total"]),
        "- `beats_persistence`: **%s**" % (
            "null" if beats is None else ("true" if beats else "false")),
        "- stato: `%s`%s" % (
            result["status"],
            "" if result["status"] == "ok" else
            " — %s" % (result.get("scores_hidden_reason") or "")), ""]
    dr = result["decision_rule"]
    lines += ["Regola: %s, lead %s, soglie %s mm/h." % (
        dr["statistic"], dr["leads_min"], dr["thresholds_mmh"]),
        "CSI medio nowcast **%.3f** vs persistenza **%.3f** (delta %+.3f)." % (
            dr["nowcast_mean_csi"] or 0.0, dr["persistence_mean_csi"] or 0.0,
            dr["delta"] or 0.0), ""]
    if result["leads"]:
        lines += ["| lead | soglia | casi | nowcast CSI | persist. CSI | "
                  "POD n | POD p | FAR n | FAR p |",
                  "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for key, r in result["leads"].items():
            n, p = r["nowcast"], r["persistence"]
            lines.append("| %d | %.1f | %d | %.3f | %.3f | %.3f | %.3f | %.3f | %.3f |" % (
                r["lead_min"], r["threshold_mmh"], r["cases_with_rain"],
                n["csi"] or 0.0, p["csi"] or 0.0, n["pod"] or 0.0,
                p["pod"] or 0.0, n["far"] or 0.0, p["far"] or 0.0))
        lines.append("")
    lines += ["## Limiti statistici", "",
              "Le ancore sono a passo regolare e le finestre di 25 minuti si "
              "sovrappongono: i casi NON sono indipendenti. La finestra copre "
              "`%s` → `%s`; il verdetto descrive questi giorni, non una "
              "climatologia. Un campione indipendente richiede piu' eventi "
              "(giorni separati), non piu' ancore nello stesso giorno." % (
                  result["window"]["start"], result["window"]["end"]), ""]
    lines += ["## Casi", "", "| ancora | stato | motivo |", "| :--- | :--- | :--- |"]
    for c in result["cases"]:
        lines.append("| %s | %s | %s |" % (c.get("anchor"), c.get("status"),
                                           c.get("null_reason") or ""))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
