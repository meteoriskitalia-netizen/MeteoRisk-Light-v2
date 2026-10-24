#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quanti eventi servono per dichiarare un POD con una precisione dichiarata.

DOMANDA
-------
"POD = 0.8 +- 0.1" (o +- 0.05) non e' un numero: e' un intervallo di
confidenza. Questo script calcola la numerosita' MINIMA di eventi severi
(cioe' il denominatore del POD, ``a + c`` nella tabella 2x2) perche' l'intervallo
di Wilson al 95% abbia semilarghezza <= h.

FORMULA (Wilson 1927, senza correzione di continuita')
------------------------------------------------------
Con ``p`` proporzione osservata, ``n`` numerosita', ``z = z_{1-alpha/2}``:

    centro(n) = (p + z^2/(2n)) / (1 + z^2/n)
    semilarghezza(n) = z/(1 + z^2/n) * sqrt( p(1-p)/n + z^2/(4 n^2) )

L'intervallo e' ``[centro - semilarghezza, centro + semilarghezza]``, troncato a
[0,1]. Wilson e' usato al posto di Wald perche' a n piccolo e p vicino a 1 Wald
produce limiti fuori da [0,1] e sottostima la copertura.

Il valore di ``p`` che massimizza la semilarghezza e' ``p = 0.5``: la tabella
riporta sia ``p = 0.8`` (il POD che ci si aspetta) sia ``p = 0.5`` (il caso
peggiore, che copre qualunque POD reale).

COSA NON DICE
-------------
Le osservazioni non sono indipendenti: gli eventi si concentrano in poche
giornate convettive. Con ``k`` eventi in ``d`` giornate la numerosita'
EFFICACE e' piu' vicina a ``d`` che a ``k``: per questo lo script stampa anche
il numero di GIORNATE necessarie, che e' il vincolo vero.

CLI
---
  python scripts/validation/power_analysis.py
  python scripts/validation/power_analysis.py --pod 0.8 --half-widths 0.10 0.05 --ratio 5
"""

from __future__ import annotations

import argparse
import json
import math
from typing import Dict, List, Tuple

Z_95 = 1.959963984540054


def wilson_interval(p: float, n: int, z: float = Z_95) -> Tuple[float, float]:
    """Intervallo di Wilson (senza correzione di continuita') per p su n casi."""
    if n <= 0:
        return (0.0, 1.0)
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def required_n(p: float, target_half_width: float, z: float = Z_95,
               nmax: int = 1_000_000) -> int:
    """Minimo n con semilarghezza di Wilson <= target (ricerca binaria)."""
    lo, hi = 1, nmax
    lo_, hi_ = wilson_interval(p, lo, z), wilson_interval(p, hi, z)
    if (hi_[1] - hi_[0]) / 2.0 > target_half_width:
        raise ValueError("target irraggiungibile entro %d" % nmax)
    if (lo_[1] - lo_[0]) / 2.0 <= target_half_width:
        return lo
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        a, b = wilson_interval(p, mid, z)
        if (b - a) / 2.0 <= target_half_width:
            hi = mid
        else:
            lo = mid
    return hi


def table(pods: List[float], half_widths: List[float], ratio: int,
          z: float = Z_95) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for p in pods:
        for h in half_widths:
            n = required_n(p, h, z)
            lo, hi = wilson_interval(p, n, z)
            rows.append({
                "pod": p,
                "target_half_width": h,
                "n_required": n,
                "wilson_low": round(lo, 4),
                "wilson_high": round(hi, 4),
                "actual_half_width": round((hi - lo) / 2.0, 4),
                # negativi: rapporto minimo 1:ratio casi severi : non severi
                "n_negative_events": n * ratio,
                # giornate: con ~1 evento severo per giornata convettiva
                "n_event_days_if_1_per_day": n,
                "alarms_for_far": n * (1 + ratio),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pod", type=float, nargs="+", default=[0.8, 0.5])
    ap.add_argument("--half-widths", type=float, nargs="+", default=[0.10, 0.05])
    ap.add_argument("--ratio", type=int, default=5,
                    help="casi non severi per caso severo (minimo 5 = 1:5)")
    ap.add_argument("--json", default=None, help="scrive la tabella in JSON")
    args = ap.parse_args()

    rows = table(args.pod, args.half_widths, args.ratio)
    print("Wilson 95%% (z=%.6f). n = eventi SEVERI (denominatore del POD)." % Z_95)
    print("negativi = n * %d (rapporto 1:%d dichiarato).\n" % (args.ratio, args.ratio))
    hdr = ("POD", "h", "n", "CI Wilson", "semi", "negativi", "allarmi")
    print("%-5s %-5s %-7s %-19s %-6s %-9s %-8s" % hdr)
    for r in rows:
        print("%-5.2f %-5.2f %-7d [%.4f, %.4f]  %-6.4f %-9d %-8d" % (
            r["pod"], r["target_half_width"], r["n_required"],
            r["wilson_low"], r["wilson_high"], r["actual_half_width"],
            r["n_negative_events"], r["alarms_for_far"]))

    print("\nEsempio esplicito (POD 0.8, h 0.10, n=60 = il valore calcolato):")
    lo, hi = wilson_interval(0.8, 60)
    print("  centro = (0.8 + 1.959964^2/(2*60)) / (1 + 1.959964^2/60) = %.6f"
          % ((0.8 + Z_95 ** 2 / (2 * 60)) / (1 + Z_95 ** 2 / 60)))
    print("  semi   = 1.959964/(1+1.959964^2/60) * sqrt(0.8*0.2/60 + 1.959964^2/(4*60^2))")
    print("         = %.6f" % ((Z_95 / (1 + Z_95 ** 2 / 60)) *
                               math.sqrt(0.8 * 0.2 / 60 + Z_95 ** 2 / (4 * 60 ** 2))))
    print("  CI     = [%.4f, %.4f]" % (lo, hi))

    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"z": Z_95, "ratio": args.ratio, "rows": rows}, fh, indent=1)
        print("\nscritto %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
