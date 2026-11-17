#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Metriche binarie ESPLICITE per la validazione di morphology_organization.

Nessun dato simulato: questo modulo contiene solo funzioni pure che lavorano
su conteggi/record passati dal chiamante. Tutte le metriche con denominatore
a zero restano ``None`` (mai 0 inventato). Le definizioni sono qui perche'
"POD/FAR" sono ambigui: `far_ratio` e' il False Alarm Ratio, `far_rate` e' la
False Alarm Rate e richiede un dominio di non-evento dichiarato.

Uso previsto: `validate3d_collect.py --finalize`. Nessuna dipendenza da
radar_engine: le formule haversine/Wilson sono dichiarate e riproducibili.
"""
from __future__ import annotations

import math
from collections import defaultdict

Z95 = 1.959963984540054  # Wilson 95%, due code


def wilson_interval(k, n, z=Z95):
    """Intervallo di Wilson per una proporzione. (None, None) se n<=0."""
    if n is None or n <= 0:
        return (None, None)
    k = max(0, min(int(k), int(n)))
    p = k / float(n)
    d = 1.0 + z * z / float(n)
    centre = (p + z * z / (2.0 * n)) / d
    half = z * math.sqrt(p * (1.0 - p) / float(n)
                         + z * z / (4.0 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def _div(num, den):
    return None if not den else float(num) / float(den)


def binary_counts(tp, fp, fn, tn=None):
    """Conteggi + metriche. `tn=None` => FAR-rate e specificita' non calcolabili.

    POD = TP/(TP+FN)  [recall]
    FAR_ratio = FP/(FP+TP)  (False Alarm Ratio, NON e' la FAR-rate)
    FAR_rate  = FP/(FP+TN)  (False Alarm Rate, serve un dominio non-evento)
    CSI = TP/(TP+FP+FN)
    precision = TP/(TP+FP); F1 = 2TP/(2TP+FP+FN)
    """
    tp, fp, fn = int(tp), int(fp), int(fn)
    lo, hi = wilson_interval(tp, tp + fn)
    out = {
        "tp": tp, "fp": fp, "fn": fn,
        "tn": None if tn is None else int(tn),
        "n_labeled": tp + fp + fn + (0 if tn is None else int(tn)),
        "pod": _div(tp, tp + fn),
        "pod_ci95": [lo, hi],
        "far_ratio": _div(fp, fp + tp),
        "far_rate": (None if tn is None else _div(fp, fp + int(tn))),
        "csi": _div(tp, tp + fp + fn),
        "precision": _div(tp, tp + fp),
        "recall": _div(tp, tp + fn),
        "f1": _div(2 * tp, 2 * tp + fp + fn),
        "false_discovery_rate": _div(fp, fp + tp),
        "prevalence": _div(tp + fn, tp + fp + fn),
        "definitions": {
            "pod": "TP/(TP+FN) = recall",
            "far_ratio": "FP/(FP+TP) — False Alarm Ratio",
            "far_rate": "FP/(FP+TN) — False Alarm Rate (solo se TN noto)",
            "csi": "TP/(TP+FP+FN)",
            "f1": "2TP/(2TP+FP+FN)",
        },
    }
    return out


def minimum_sample(counts, *, min_positive=30, min_negative=30,
                   max_ci_halfwidth=0.20):
    """Gate di numerosita': vieta di presentare metriche da pochi casi.

    Restituisce `passed` e i motivi. `metric_status` e' "complete" solo se
    tutti i criteri passano, altrimenti "incomplete" — in quel caso il
    report DEVE dichiarare il numero e non presentare POD/FAR come stime.
    """
    reasons = []
    tp = int(counts.get("tp") or 0)
    fp = int(counts.get("fp") or 0)
    fn = int(counts.get("fn") or 0)
    pos = tp + fn
    neg = fp + int(counts.get("tn") or 0)
    if pos < int(min_positive):
        reasons.append(f"positive_labeled={pos}<{min_positive}")
    if neg < int(min_negative):
        reasons.append(f"negative_labeled={neg}<{min_negative}")
    lo, hi = counts.get("pod_ci95") or (None, None)
    if lo is not None and hi is not None:
        half = (hi - lo) / 2.0
        if half > float(max_ci_halfwidth):
            reasons.append(
                "pod_ci95_halfwidth={:.3f}>{:.3f}".format(
                    half, float(max_ci_halfwidth)))
    else:
        reasons.append("pod_ci95_undefined")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "positive_labeled": pos,
        "negative_labeled": neg,
        "min_positive": int(min_positive),
        "min_negative": int(min_negative),
        "max_ci_halfwidth": float(max_ci_halfwidth),
        "metric_status": "complete" if not reasons else "incomplete",
    }


def _haversine_km(lat1, lon1, lat2, lon2):
    """Stessa formula di radar_engine.tracking.haversine_km (R=6371 km)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 6371.0 * 2.0 * math.asin(min(1.0, math.sqrt(a)))


def confusion_from_pairs(observed, predicted, radius_km=20.0):
    """Match greedy 1-a-1 per centroide: tp/fp/fn + coppie abbinate.

    `observed`/`predicted` sono liste di dict con `lat`,`lon` (gradi).
    La soglia di matching e' un PARAMETRO dichiarato, non una soglia tarata.
    """
    radius_km = float(radius_km)
    used, matched = set(), []
    for i, ref in enumerate(observed or []):
        best, best_d = None, radius_km
        for j, cand in enumerate(predicted or []):
            if j in used:
                continue
            try:
                d = _haversine_km(float(ref["lat"]), float(ref["lon"]),
                                  float(cand["lat"]), float(cand["lon"]))
            except (KeyError, TypeError, ValueError):
                continue
            if d <= best_d:
                best, best_d = j, d
        if best is not None:
            used.add(best)
            matched.append({"observed": i, "predicted": best,
                            "distance_km": round(best_d, 3)})
    tp = len(matched)
    return {"tp": tp, "fp": max(0, len(predicted or []) - tp),
            "fn": max(0, len(observed or []) - tp), "matched": matched,
            "radius_km": radius_km}


def reliability_by_band(rows, band_key, label_key="label_afa",
                        max_bands=40):
    """Frequenza OSSERVATA di etichetta positiva per banda di valore 3D.

    NON e' affidabilita' di una probabilita' calibrata: il campo 3D non e'
    una probabilita'. E' il confronto tra bande di osservazioni e la
    frequenza dell'etichetta indipendente, con intervallo di Wilson.
    """
    groups = defaultdict(list)
    for r in rows or []:
        band = r.get(band_key)
        label = r.get(label_key)
        if band is None or label is None:
            continue
        groups[str(band)].append(int(bool(label)))
    out = []
    for band in sorted(groups)[:int(max_bands)]:
        vals = groups[band]
        lo, hi = wilson_interval(sum(vals), len(vals))
        out.append({"band": band, "n": len(vals), "positives": sum(vals),
                    "observed_frequency": sum(vals) / float(len(vals)),
                    "ci95": [lo, hi]})
    return {"bands": out, "note": (
        "frequenza osservata dell'etichetta AFA per banda; NON una "
        "probabilita' calibrata ne' una curva di affidabilita' di un modello "
        " probabilistico")}


def paired_baseline_compare(rows, *, label_key="label_afa",
                            baseline_key="baseline_organized",
                            new_key="new_organized"):
    """Confronto APPAIATO baseline vs nuova regola sugli STESSI casi.

    Le due regole sono valutate riga per riga sullo stesso set di test: non
    si cambia campione fra baseline e nuova. Sono riportati i conteggi di
    ciascuna regola e i due tipi di disaccordo (nuova corregge / nuova
    peggiora). Nessun test di significativita': con pochi casi si dichiara.
    """
    b_tp = b_fp = b_fn = b_tn = 0
    n_tp = n_fp = n_fn = n_tn = 0
    fixed = broken = 0
    used = 0
    for r in rows or []:
        label = r.get(label_key)
        base, new = r.get(baseline_key), r.get(new_key)
        if label is None or base is None or new is None:
            continue
        used += 1
        y = int(bool(label))
        b, n = int(bool(base)), int(bool(new))
        for d, v in ((b, y),):
            if v == 1 and d == 1:
                b_tp += 1
            elif v == 1 and d == 0:
                b_fn += 1
            elif v == 0 and d == 1:
                b_fp += 1
            else:
                b_tn += 1
        if y == 1 and n == 1:
            n_tp += 1
        elif y == 1 and n == 0:
            n_fn += 1
        elif y == 0 and n == 1:
            n_fp += 1
        else:
            n_tn += 1
        if b != y and n == y:
            fixed += 1
        elif b == y and n != y:
            broken += 1
    base_m = binary_counts(b_tp, b_fp, b_fn, b_tn)
    new_m = binary_counts(n_tp, n_fp, n_fn, n_tn)
    return {
        "n_paired": used,
        "baseline": base_m,
        "new": new_m,
        "delta_pod": (None if base_m["pod"] is None or new_m["pod"] is None
                      else new_m["pod"] - base_m["pod"]),
        "delta_csi": (None if base_m["csi"] is None or new_m["csi"] is None
                      else new_m["csi"] - base_m["csi"]),
        "delta_far_ratio": (None if base_m["far_ratio"] is None
                            or new_m["far_ratio"] is None
                            else new_m["far_ratio"] - base_m["far_ratio"]),
        "new_fixed_baseline_errors": fixed,
        "new_broke_baseline_correct": broken,
        "note": ("stessi record, stessa etichetta; differenza di POD/CSI/FAR "
                 "e' descrittiva, non un test di significativita'"),
    }


def field_continuity(events, *, field_key="morphology_organization",
                     label_key="label_afa"):
    """Continuita' del campo fra frame consecutivi, per evento.

    `field_present` = classification non null. La baseline operativa ha il
    campo sempre null con 2 CAPPI, quindi la copertura e' il primo risultato
    misurabile. `stable` = stessa classificazione su due frame consecutivi.
    Quando l'etichetta AFA e' disponibile si riporta anche POD/FAR/CSI del
    "campo presente" rispetto all'etichetta (sempre sugli stessi record).
    """
    pairs = present = stable = baseline_null = 0
    det = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for ev in events or []:
        rows = sorted(ev.get("rows", []), key=lambda r: r.get("ts_ms") or 0)
        for a, b in zip(rows, rows[1:]):
            pairs += 1
            fa = (a.get(field_key) or {}).get("classification")
            fb = (b.get(field_key) or {}).get("classification")
            if fa is not None and fb is not None:
                present += 1
                if fa == fb:
                    stable += 1
            if (a.get("baseline_field_available") is False
                    or b.get("baseline_field_available") is False):
                baseline_null += 1
        for r in rows:
            label = r.get(label_key)
            field = (r.get(field_key) or {}).get("classification")
            if label is None or field is None:
                continue
            y, d = int(bool(label)), int(field == "organized_vertical")
            if y and d:
                det["tp"] += 1
            elif y and not d:
                det["fn"] += 1
            elif not y and d:
                det["fp"] += 1
            else:
                det["tn"] += 1
    lo, hi = wilson_interval(present, pairs)
    return {
        "pairs": pairs,
        "field_present_both": present,
        "field_present_rate": _div(present, pairs),
        "field_present_ci95": [lo, hi],
        "stable_when_present": stable,
        "stability_rate": _div(stable, present),
        "baseline_null_pairs": baseline_null,
        "detection_vs_afa": binary_counts(
            det["tp"], det["fp"], det["fn"], det["tn"]),
        "note": ("continuita' = copertura/stabilita' del campo fra frame "
                 "consecutivi dello STESSO evento; non e' una misura di "
                 "accuratezza spaziale"),
    }
