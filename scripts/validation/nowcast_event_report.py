#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk — report per evento del nowcast verificato (SRI vs persistenza).

Legge la serie temporale accumulata da `scripts/radar_engine/nowcast_verify.py`
in `data/radar/nowcast_verification.json` (schema
`meteorisk.nowcast_verification/2`, chiave `series.rows`) e produce un
riassunto per un intervallo di date:

  - numero di slot e di righe verificate / non verificabili;
  - CSI medio e CSI pooled per lead (15/30/60 minuto) e per soglia
    (0.5 / 2.0 / 5.0 mm/h, quelle gia' dichiarate: qui non si aggiunge nulla);
  - confronto con la PERSISTENZA (campo fermo), riga per riga e pooled;
  - i casi migliori e peggiori, con slot e coordinate
    (`peak_observed`, centroide dei falsi allarmi / delle mancate).

Uso (domani mattina):

    python scripts/validation/nowcast_event_report.py \
        --from 2026-11-24T18 --to 2026-11-25T12

`--from`/`--to` accettano `YYYY-MM-DD`, `YYYY-MM-DDTHH`,
`YYYY-MM-DDTHH:MM` e ISO con `Z`; un istante senza fuso e' interpretato come
UTC e il report lo dichiara. Il filtro e' su `valid_time` (l'istante
previsione), non su `source_slot`: un nowcast emesso prima delle 18 e valido
dopo le 18 rientra nell'evento.

Se la serie e' vuota o nessuna riga e' verificata, il report lo dice
esplicitamente ("nessun dato", "non verificabile") e NON stampa zeri
travestiti da misura.

Nessuna dipendenza oltre la standard library: puo' girare a mano senza
numpy/rasterio.
"""

import argparse
import collections
import datetime as _dt
import json
import os
import sys

_ISO = "%Y-%m-%dT%H:%M:%SZ"
_UTC = _dt.timezone.utc

DEFAULT_VERIFICATION = os.path.join("data", "radar",
                                    "nowcast_verification.json")
THRESHOLDS = (0.5, 2.0, 5.0)
LEADS = (15, 30, 60)


# ---------------------------------------------------------------------------
# parsing tempo (stessa semantica di radar_engine.clock.parse_iso8601_utc)
# ---------------------------------------------------------------------------
def parse_iso_ms(value, tz=None):
    """ISO-8601 -> (epoch ms, timezone_assumed). None/vuoto -> (None, False).

    Un istante senza fuso viene interpretato come `tz` se fornito (es.
    `Europe/Rome`), altrimenti come UTC. Il chiamante lo dichiara.
    """
    if value is None:
        return None, False
    s = str(value).strip()
    if not s:
        return None, False
    txt = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    dt = _dt.datetime.fromisoformat(txt)
    assumed = dt.tzinfo is None
    if assumed:
        zone = None
        if tz:
            try:
                from zoneinfo import ZoneInfo
            except ImportError:               # Python < 3.9 (non atteso)
                raise ValueError("zoneinfo_unavailable")
            try:
                zone = ZoneInfo(tz)
            except Exception as exc:
                raise ValueError("invalid_tz:%s" % tz) from exc
        dt = dt.replace(tzinfo=zone or _UTC)
    return int(dt.timestamp() * 1000), assumed


def iso_ms(ms):
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0, _UTC).strftime(_ISO)


def _num(value):
    return None if value is None else float(value)


def _mean(values):
    vals = [v for v in values if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _pooled(rows, prefix):
    """CSI pooled dai conteggi; None se la riga non li ha."""
    hit = miss = fa = 0
    seen = False
    for row in rows:
        h, m, f = (row.get("hits" if prefix == "nowcast" else
                           "hits_persistenza"),
                   row.get("misses" if prefix == "nowcast" else
                           "misses_persistenza"),
                   row.get("false_alarms" if prefix == "nowcast" else
                           "false_alarms_persistenza"))
        if None in (h, m, f):
            continue
        seen = True
        hit += int(h)
        miss += int(m)
        fa += int(f)
    if not seen:
        return None
    den = hit + miss + fa
    return (hit / float(den)) if den else None


def _fmt(value, digits=3):
    return "n/d" if value is None else ("%.*f" % (digits, value))


def summarize(rows, start_ms, end_ms, timezone_assumed, tz_label="UTC"):
    """Righe `series.rows` filtrate su valid_time -> riassunto serializzabile."""
    selected = []
    skipped_bad_time = 0
    for row in rows:
        try:
            valid_ms, _ = parse_iso_ms(row.get("valid_time"))
        except (TypeError, ValueError):
            skipped_bad_time += 1
            continue
        if valid_ms is None:
            skipped_bad_time += 1
            continue
        if valid_ms < start_ms or valid_ms > end_ms:
            continue
        selected.append(row)

    verified = [r for r in selected if r.get("status") == "verified"]
    not_inf = [r for r in selected if r.get("status") == "not_informative"]
    not_ver = [r for r in selected if r.get("status") == "not_verifiable"]

    slots = sorted({str(r.get("source_slot")) for r in selected
                    if r.get("source_slot")})
    slots_verified = sorted({str(r.get("source_slot")) for r in verified
                             if r.get("source_slot")})

    per_lead = {}
    for lead in LEADS:
        lrows = [r for r in verified if int(r.get("lead_min") or 0) == lead]
        if not lrows:
            per_lead[str(lead)] = {
                "lead_min": lead, "rows": 0,
                "mean_csi_nowcast": None, "mean_csi_persistence": None,
                "pooled_csi_nowcast": None, "pooled_csi_persistence": None,
                "wins": 0, "losses": 0, "ties": 0,
            }
            continue
        wins = losses = ties = 0
        for row in lrows:
            n, p = _num(row.get("csi_nowcast")), _num(row.get(
                "csi_persistenza"))
            if n is None or p is None:
                continue
            if n > p:
                wins += 1
            elif n < p:
                losses += 1
            else:
                ties += 1
        per_lead[str(lead)] = {
            "lead_min": lead,
            "rows": len(lrows),
            "mean_csi_nowcast": _mean([_num(r.get("csi_nowcast"))
                                       for r in lrows]),
            "mean_csi_persistence": _mean([_num(r.get("csi_persistenza"))
                                           for r in lrows]),
            "pooled_csi_nowcast": _pooled(lrows, "nowcast"),
            "pooled_csi_persistence": _pooled(lrows, "persistence"),
            "wins": wins, "losses": losses, "ties": ties,
        }

    per_threshold = {}
    for thr in THRESHOLDS:
        trows = [r for r in verified
                 if abs(float(r.get("threshold_mmh") or -1) - thr) < 1e-9]
        per_threshold["%.1f" % thr] = {
            "threshold_mmh": thr,
            "rows": len(trows),
            "mean_csi_nowcast": _mean([_num(r.get("csi_nowcast"))
                                       for r in trows]),
            "mean_csi_persistence": _mean([_num(r.get("csi_persistenza"))
                                           for r in trows]),
            "pooled_csi_nowcast": _pooled(trows, "nowcast"),
            "pooled_csi_persistence": _pooled(trows, "persistence"),
        }

    scored = [r for r in verified
              if r.get("csi_nowcast") is not None
              and r.get("csi_persistenza") is not None]
    wins = sum(1 for r in scored
               if float(r["csi_nowcast"]) > float(r["csi_persistenza"]))
    losses = sum(1 for r in scored
                 if float(r["csi_nowcast"]) < float(r["csi_persistenza"]))
    ties = len(scored) - wins - losses

    def brief(row, include_error=False):
        peak = row.get("peak_observed") or {}
        out = {
            "source_slot": row.get("source_slot"),
            "valid_time": row.get("valid_time"),
            "lead_min": row.get("lead_min"),
            "threshold_mmh": row.get("threshold_mmh"),
            "csi_nowcast": row.get("csi_nowcast"),
            "csi_persistenza": row.get("csi_persistenza"),
            "pod": row.get("pod"),
            "far": row.get("far"),
            "n_px_observed": row.get("n_px_observed"),
            "peak_observed_mmh": peak.get("mmh"),
            "peak_observed_lat": peak.get("lat"),
            "peak_observed_lon": peak.get("lon"),
        }
        if include_error:
            out["error_location"] = row.get("error_location")
        return out

    ranked = sorted(scored, key=lambda r: float(r["csi_nowcast"]))
    return {
        "window": {
            "from": iso_ms(start_ms),
            "to": iso_ms(end_ms),
            "timezone_assumed": bool(timezone_assumed),
            "interpreted_timezone": tz_label,
            "filter_field": "valid_time",
        },
        "available": bool(selected),
        "counts": {
            "rows": len(selected),
            "verified": len(verified),
            "not_informative": len(not_inf),
            "not_verifiable": len(not_ver),
            "slots": len(slots),
            "slots_verified": len(slots_verified),
            "skipped_bad_time": skipped_bad_time,
        },
        "slots": slots_verified,
        "not_verifiable_reasons": dict(collections.Counter(
            str(r.get("null_reason")) for r in not_ver)),
        "per_lead": per_lead,
        "per_threshold": per_threshold,
        "persistence": {
            "rows_compared": len(scored),
            "nowcast_wins": wins,
            "persistence_wins": losses,
            "ties": ties,
            "pooled_csi_nowcast": _pooled(verified, "nowcast"),
            "pooled_csi_persistence": _pooled(verified, "persistence"),
            "beats_persistence": (
                None if not scored else
                (_pooled(verified, "nowcast") or 0.0)
                > (_pooled(verified, "persistence") or 0.0)),
        },
        "worst": [brief(r, include_error=True) for r in ranked[:3]],
        "best": [brief(r, include_error=True) for r in ranked[-3:][::-1]],
    }


def render(summary):
    c = summary["counts"]
    lines = ["# Nowcast del campo di pioggia — report evento", ""]
    lines.append("Finestra (`valid_time`): **%s** → **%s**%s." % (
        summary["window"]["from"], summary["window"]["to"],
        " (istanti senza fuso interpretati come %s)"
        % summary["window"].get("interpreted_timezone", "UTC")
        if summary["window"]["timezone_assumed"] else ""))
    lines.append("")
    lines.append("- slot distinti con almeno una riga: **%d** "
                 "(verificati: **%d**)" % (c["slots"], c["slots_verified"]))
    lines.append("- righe: **%d** — verificate **%d**, non informative **%d**, "
                 "non verificabili **%d**" % (
                     c["rows"], c["verified"], c["not_informative"],
                     c["not_verifiable"]))
    if summary["not_verifiable_reasons"]:
        lines.append("- motivi `not_verifiable`: " + ", ".join(
            "`%s` × %d" % (k, v) for k, v in sorted(
                summary["not_verifiable_reasons"].items())))
    lines.append("")
    if not summary["available"]:
        lines += ["**Nessun dato nella finestra**: nessun nowcast archiviato "
                  "con valid_time in questo intervallo. Non e' un risultato "
                  "sull'accuratezza, e' assenza di dati.", ""]
        return "\n".join(lines) + "\n"
    if c["verified"] == 0:
        lines += ["**Nessuna riga verificata**: l'osservato manca o e' sotto "
                  "la soglia di pioggia minima. Vedi i motivi sopra: non si "
                  "pubblicano punteggi.", ""]
        return "\n".join(lines) + "\n"

    lines += ["## CSI per lead", "",
              "| lead | righe | CSI medio nowcast | CSI medio persist. | "
              "CSI pooled nowcast | CSI pooled persist. | vinte/perse/pari |",
              "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for lead in LEADS:
        r = summary["per_lead"][str(lead)]
        lines.append("| %d min | %d | %s | %s | %s | %s | %d/%d/%d |" % (
            lead, r["rows"], _fmt(r["mean_csi_nowcast"]),
            _fmt(r["mean_csi_persistence"]), _fmt(r["pooled_csi_nowcast"]),
            _fmt(r["pooled_csi_persistence"]), r["wins"], r["losses"],
            r["ties"]))
    lines += ["", "## CSI per soglia", "",
              "| soglia | righe | CSI medio nowcast | CSI medio persist. |",
              "| ---: | ---: | ---: | ---: |"]
    for thr in THRESHOLDS:
        r = summary["per_threshold"]["%.1f" % thr]
        lines.append("| %.1f mm/h | %d | %s | %s |" % (
            thr, r["rows"], _fmt(r["mean_csi_nowcast"]),
            _fmt(r["mean_csi_persistence"])))
    p = summary["persistence"]
    lines += ["", "## Confronto con la persistenza", ""]
    lines.append("- righe confrontabili: **%d**; nowcast vince **%d**, "
                 "perde **%d**, pari **%d**" % (
                     p["rows_compared"], p["nowcast_wins"],
                     p["persistence_wins"], p["ties"]))
    lines.append("- CSI pooled: nowcast **%s** vs persistenza **%s**" % (
        _fmt(p["pooled_csi_nowcast"]), _fmt(p["pooled_csi_persistence"])))
    verdict = p["beats_persistence"]
    lines.append("- esito: %s" % (
        "il nowcast **batte** la persistenza su questa finestra."
        if verdict else
        "il nowcast **NON batte** la persistenza su questa finestra "
        "(e' un risultato, non un guasto)."))
    lines += ["", "## Casi peggiori", ""]
    if not summary["worst"]:
        lines.append("nessun caso con punteggio definito.")
    for r in summary["worst"]:
        lines.append("- `%s` lead %d, %.1f mm/h: CSI %s (persist. %s), "
                     "picco osservato %s mm/h a %.4f, %.4f" % (
                         r["source_slot"], r["lead_min"], r["threshold_mmh"],
                         _fmt(r["csi_nowcast"]),
                         _fmt(r["csi_persistenza"]),
                         _fmt(r["peak_observed_mmh"], 2),
                         r["peak_observed_lat"] or 0.0,
                         r["peak_observed_lon"] or 0.0))
    lines += ["", "## Casi migliori", ""]
    if not summary["best"]:
        lines.append("nessun caso con punteggio definito.")
    for r in summary["best"]:
        lines.append("- `%s` lead %d, %.1f mm/h: CSI %s (persist. %s), "
                     "picco osservato %s mm/h a %.4f, %.4f" % (
                         r["source_slot"], r["lead_min"], r["threshold_mmh"],
                         _fmt(r["csi_nowcast"]),
                         _fmt(r["csi_persistenza"]),
                         _fmt(r["peak_observed_mmh"], 2),
                         r["peak_observed_lat"] or 0.0,
                         r["peak_observed_lon"] or 0.0))
    lines += ["", "Punteggi pooled calcolati dai conteggi delle righe "
              "verificate; i punteggi `null` (denominatore zero) sono esclusi "
              "dalle medie, non contati come zero.", ""]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Report per evento del nowcast verificato")
    ap.add_argument("--from", dest="from_iso", required=True)
    ap.add_argument("--to", dest="to_iso", required=True)
    ap.add_argument("--verification", default=DEFAULT_VERIFICATION)
    ap.add_argument("--tz", default=None,
                    help="fuso per gli istanti SENZA offset (es. Europe/Rome); "
                         "default UTC")
    ap.add_argument("--json", default=None,
                    help="scrive il riassunto in JSON")
    ap.add_argument("--markdown", default=None,
                    help="scrive il report in Markdown")
    args = ap.parse_args(argv)

    try:
        start_ms, a1 = parse_iso_ms(args.from_iso, tz=args.tz)
        end_ms, a2 = parse_iso_ms(args.to_iso, tz=args.tz)
    except (TypeError, ValueError) as exc:
        print("[ERRORE] intervallo non interpretabile: %s" % exc)
        return 2
    if start_ms is None or end_ms is None:
        print("[ERRORE] --from/--to obbligatori e interpretabili")
        return 2
    if end_ms < start_ms:
        print("[ERRORE] --to precede --from")
        return 2
    try:
        with open(args.verification, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print("[ERRORE] %s non leggibile (%s)" % (args.verification, exc))
        print("La serie e' scritta dal job radar-engine. Se il file non "
              "esiste, la raccolta non e' ancora partita.")
        return 1
    rows = ((doc.get("series") or {}).get("rows") or [])
    summary = summarize(rows, start_ms, end_ms, bool(a1 or a2),
                        tz_label=args.tz or "UTC")
    text = render(summary)
    print(text)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)),
                    exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1, default=float)
        print("[INFO] JSON scritto in %s" % args.json)
    if args.markdown:
        os.makedirs(os.path.dirname(os.path.abspath(args.markdown)),
                    exist_ok=True)
        with open(args.markdown, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("[INFO] Markdown scritto in %s" % args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
