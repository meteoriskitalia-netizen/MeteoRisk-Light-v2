#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate di PLAUSIBILITA' FISICA della temperatura, condiviso dai due script.

PERCHE' ESISTE (misurato il 2026-11-23, non ipotizzato)
------------------------------------------------------
Il blob `data/stations/daily_series.json` del 2026-11-23T21:31Z pubblicava
`temp_max_day_c = 52,0` per `dpcn-sicilia:37.20139,13.76111` (Palma di
Montechiaro) mentre la temperatura istantanea era 14,8 degC e la serie oraria
stava fra 24 e 27 degC. Il gate precedente (`|T| <= 60 degC`) non intercetta un
valore che, preso da solo, e' fisicamente possibile: serve un confronto con la
SERIE. La serie reale di quella stazione, dal 2026-11-23T15:30Z, alterna
43,3 / 40,0 / 52,0 / 15,2 / 15,1 / 46,0 / 48,9 / 47,5 / 14,8 / 47,6 / 43,1 /
42,9 / 40,4 / 40,5 / 14,8: salti di 20-37 degC in 30-60 minuti.

REGOLA SCELTA (dichiarata come SCELTA INGEGNERISTICA, non come soglia
meteorologica validata)
----------------------------------------------------------------------------------------
Si scarta un campione se il RATE di variazione rispetto all'ULTIMO CAMPIONE
ACCETTATO supera `TEMP_MAX_RATE_C_PER_H = 6,0 degC/h` E il salto assoluto
supera `TEMP_MIN_ABS_DELTA_C = 3,0 degC`:

    |T2 - T1| > 3,0  e  |T2 - T1| / ((t2 - t1) in ore) > 6,0
        ->   campione scartato

Motivazione, con i numeri:
  * in Italia una variazione di 6 degC in un'ora e' gia' eccezionale. I fronti
    freddi piu' rapidi misurati sulle centraline fanno 3-5 degC/h; il fohn
    alpino puo' fare +4-5 degC/h per un'ora o due. 6,0 lascia margine a questi
    eventi reali e taglia i salti da sensore rotto (15-37 degC in 30-60 min);
  * il rate da solo e' troppo sensibile sui passi CORTI: con campioni ogni
    10-20 minuti un rumore di 1,5-2 degC (risoluzione 0,1 degC, aria diversa
    fra due sensori della stessa rete) diventa 5-12 degC/h. Per questo il
    campione si scarta solo se il salto ASSOLUTO supera
    `TEMP_MIN_ABS_DELTA_C = 3,0 degC` E il rate supera 6,0 degC/h. MISURATO sul
    blob reale del 2026-11-23: con il solo rate 728 stazioni su 5.158 avevano
    almeno un campione scartato (la maggior parte con UNA oscillazione da
    1,5-2,5 degC, cioe' rumore o fronte reale); con il minimo assoluto sono
    171 stazioni e 365 campioni, e i salti da 9-37 degC restano;
  * la soglia e' un RATE, non un salto assoluto: un +4 degC in un'ora (fohn)
    passa, un +30 degC in un'ora no. Anche un riscaldamento lento di 10 degC in
    3 ore (3,3 degC/h) passa;
  * il confronto e' con l'ULTIMO CAMPIONE ACCETTATO, non con il precedente in
    assoluto: dopo un picco falso la serie non resta "agganciata" al valore
    rotto (con il confronto col precedente, il ritorno a 15 degC verrebbe
    scartato come un secondo salto).
  * oltre `TEMP_RATE_MAX_GAP_H = 3 h` senza dati il rate non e' valutabile
    (la sorgente puo' avere buchi): il campione si accetta. Scartarlo
    significherebbe buttare un'osservazione vera solo perche' manca il vicino;
    il buco e' dichiarato dalla copertura delle finestre.

Completamente separata (e complementare) e' la riparazione del blob
giornaliero in `build_daily_series.py`: li' un bucket orario gia' salvato
([media, minima, massima]) viene ricostruito dai punti della stessa ora quando
si discosta da essi di oltre `TEMP_DAILY_BUCKET_TOLERANCE_C = 15 degC`, e viene
ELIMINATO quando tutti i punti di quell'ora sono stati scartati dal gate qui
sopra. Non e' una seconda soglia sulla serie: e' il confronto fra il valore
pubblicato e la serie da cui doveva derivare (max_day <= max(serie) + tolleranza).

Il gate NON inventa un valore sostitutivo: il campione scartato sparisce e il
conteggio finisce in `temp_qc_rejected` (latest.json) e in
`temp_qc_rejected` del blob giornaliero. Il massimo/minimo pubblicato e' quello
dei campioni ACCETTATI.

Solo stdlib: nessuna dipendenza aggiunta.
"""
from __future__ import annotations

from datetime import datetime

# SCELTA INGEGNERISTICA (non una soglia meteorologica validata): vedi docstring.
TEMP_MAX_RATE_C_PER_H = 6.0
# Salto ASSOLUTO minimo perche' il rate sia significativo: sotto 3 degC il rate
# su un passo di 10-20 minuti misura il rumore, non un evento fisico.
TEMP_MIN_ABS_DELTA_C = 3.0
# Oltre 3 h senza un campione valido il rate non e' valutabile: si accetta.
TEMP_RATE_MAX_GAP_H = 3.0
# Riparazione dei bucket orari del blob giornaliero: se [min, max] del bucket
# si discosta di oltre 15 degC dai punti della stessa ora, il bucket e'
# incoerente con la serie e viene ricostruito (o eliminato se i punti sono
# tutti scartati).
TEMP_DAILY_BUCKET_TOLERANCE_C = 15.0


def qc_temperature_points(
    points: list[tuple[datetime, float]],
    max_rate_c_per_h: float = TEMP_MAX_RATE_C_PER_H,
    max_gap_h: float = TEMP_RATE_MAX_GAP_H,
    min_abs_delta_c: float = TEMP_MIN_ABS_DELTA_C,
) -> tuple[list[tuple[datetime, float]], int, set[int]]:
    """Filtra una serie di temperatura con il gate del rate di variazione.

    `points`: [(datetime UTC, valore in degC o K)]. Il gate lavora sulle
    DIFFERENZE, quindi l'unita' non conta (6 K/h == 6 degC/h).

    Ritorna `(kept, rejected_count, rejected_indexes)`:
      * `kept` mantiene l'ordine temporale e le tuple originali;
      * `rejected_indexes` sono gli indici della lista di INPUT, cosi' il
        chiamante puo' azzerare il singolo campo di una tupla multi-grandezza
        (build_daily_series) senza ricostruire la serie.

    Casi limite, tutti dichiarati:
      * il primo campione e' sempre accettato (non esiste un rate);
      * due campioni con lo stesso istante: si tiene il primo, i successivi
        con valore diverso sono scartati (duplicato incoerente);
      * `dt <= 0` non produce divisioni per zero;
      * `dt > max_gap_h`: accettato senza rate (buco nella serie).
    """
    order = sorted(range(len(points)), key=lambda i: (points[i][0], i))
    kept: list[tuple[datetime, float]] = []
    rejected: set[int] = set()
    previous: tuple[datetime, float] | None = None
    for index in order:
        ref, value = points[index]
        if previous is None:
            kept.append((ref, value))
            previous = (ref, value)
            continue
        seconds = (ref - previous[0]).total_seconds()
        if seconds <= 0:
            # stesso istante: il primo vince, un duplicato incoerente si scarta
            if value == previous[1]:
                kept.append((ref, value))
            else:
                rejected.add(index)
            continue
        hours = seconds / 3600.0
        if hours <= max_gap_h:
            delta = abs(value - previous[1])
            rate = delta / hours
            if delta > min_abs_delta_c and rate > max_rate_c_per_h:
                rejected.add(index)
                continue
        kept.append((ref, value))
        previous = (ref, value)
    return kept, len(rejected), rejected
