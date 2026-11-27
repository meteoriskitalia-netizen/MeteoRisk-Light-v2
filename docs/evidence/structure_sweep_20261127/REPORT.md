# Struttura convettiva - sweep sistematico (2026-11-27)

Documento generato dai JSON di misura in `results/`. Le giornate e l'obiettivo sono in `DECLARATION.md`, scritto **prima** di eseguire lo sweep.

## Sintesi

**Combinazione scelta**: geometria a TUTTI i nuclei >=35 dBZ (`core_mode=all`, cap 8, buffer 0) + isteresi di classe 3 frame. Soglie di detection, area minima, SOI ed elongazione **invariate** rispetto alla 1.3.0.86.

| insieme | POD baseline | POD 1.3.0.87 | area mediana baseline | area mediana 1.3.0.87 | transizioni baseline | transizioni 1.3.0.87 |
| :-- | --: | --: | --: | --: | --: | --: |
| screening (6 giornate, 7 eventi) | 0.5714 | 0.5714 | 0.43968 | 0.07329 | 0.1212 | 0.0331 |
| finale (8 giornate, 8 eventi) | 0.375 | 0.375 | 0.41929 | 0.03531 | 0.1314 | 0.0365 |

Il POD **non migliora** (4/7 screening, 3/8 finale, identico al baseline). Migliorano l'area marcata severa (proxy del falso positivo, non un FAR) e la stabilita' della classe. Il finale e' un holdout: la combinazione e' stata scelta solo sullo screening.

## Screening: 48 combinazioni (top 10 non scartate)

| POD | hit/eventi | area mediana | area max | trans/frame | track cambiate % | core dBZ | buffer km | core mode | isteresi | SOI | area min km2 |
| --: | --: | --: | --: | --: | --: | --: | --: | :-- | --: | --: | --: |
| 0.4286 | 3/7 | 0.02549 | 0.34168 | 0.0095 | 4.17 | 45.0 | 5.0 | nearest | 3 | 55.0 | 200.0 |
| 0.4286 | 3/7 | 0.14529 | 0.4159 | 0.0594 | 24.72 | 20.0 | 0.0 | nearest | 2 | 65.0 | 50.0 |
| 0.4286 | 3/7 | 0.04754 | 0.24147 | 0.069 | 30.39 | 45.0 | 5.0 | nearest | 2 | 65.0 | 30.0 |
| 0.4286 | 3/7 | 0.09237 | 0.36771 | 0.0723 | 27.47 | 45.0 | 15.0 | nearest | 2 | 55.0 | 50.0 |
| 0.4286 | 3/7 | 0.13629 | 0.20806 | 0.034 | 14.71 | 20.0 | 5.0 | nearest | 2 | 55.0 | 200.0 |
| 0.2857 | 2/7 | 0.01768 | 0.33907 | 0.0345 | 15.22 | 45.0 | 0.0 | nearest | 3 | 55.0 | 30.0 |
| 0.2857 | 2/7 | 0.01233 | 0.33907 | 0.0388 | 17.05 | 45.0 | 0.0 | nearest | 3 | 55.0 | 50.0 |
| 0.2857 | 2/7 | 0.07177 | 0.14914 | 0.0367 | 16.67 | 20.0 | 0.0 | nearest | 3 | 75.0 | 30.0 |
| 0.2857 | 2/7 | 0.16511 | 0.18891 | 0.0342 | 14.73 | 35.0 | 10.0 | nearest | 3 | 55.0 | 30.0 |
| 0.2857 | 2/7 | 0.12105 | 0.26798 | 0.0375 | 17.07 | 20.0 | 5.0 | nearest | 3 | 75.0 | 30.0 |

Nota: le prime 48 combinazioni sono state eseguite prima di introdurre `core_mode` come asse; `None` nella colonna core mode significa il default dell'epoca (= `nearest`, un solo nucleo). Scartate dal gate: area mediana > 25% del dominio o una classe >90% con >=20 record. Il baseline (inviluppo 20 dBZ, isteresi 1) ha area mediana 43,97% ed e' scartato.

## Rifinitura: 44 combinazioni mirate (top 6 non scartate)

| POD | hit/eventi | area mediana | area max | trans/frame | track cambiate % | core dBZ | buffer km | core mode | isteresi | SOI | area min km2 |
| --: | --: | --: | --: | --: | --: | --: | --: | :-- | --: | --: | --: |
| 0.5714 | 4/7 | 0.06095 | 0.10688 | 0.0369 | 16.25 | 35.0 | 0.0 | all | 3 | 65.0 | 50.0 |
| 0.5714 | 4/7 | 0.06095 | 0.10688 | 0.0348 | 15.38 | 35.0 | 0.0 | all | 3 | 55.0 | 30.0 |
| 0.5714 | 4/7 | 0.06095 | 0.10688 | 0.0323 | 14.29 | 35.0 | 0.0 | all | 3 | 65.0 | 30.0 |
| 0.5714 | 4/7 | 0.06095 | 0.10688 | 0.0318 | 14.12 | 35.0 | 0.0 | all | 3 | 65.0 | 30.0 |
| 0.5714 | 4/7 | 0.19947 | 0.41799 | 0.0331 | 14.63 | 25.0 | 0.0 | all | 3 | 65.0 | 30.0 |
| 0.5714 | 4/7 | 0.12256 | 0.20527 | 0.0331 | 14.63 | 30.0 | 0.0 | all | 3 | 65.0 | 30.0 |

La scoperta della rifinitura: il fix "un solo nucleo piu' vicino al centroide" perdeva l'evento LIMF (stazione dentro un nucleo >=35 dBZ di 970 km2, ma non nel nucleo piu' vicino al centroide). La modalita' `all` recupera il POD del baseline con area molto minore.

## Holdout finale: 8 combinazioni dichiarate

| POD | hit/eventi | area mediana | area max | trans/frame | track cambiate % | core dBZ | buffer km | core mode | isteresi | SOI | area min km2 |
| --: | --: | --: | --: | --: | --: | --: | --: | :-- | --: | --: | --: |
| 0.375 | 3/8 | 0.03531 | 0.10723 | 0.036 | 14.71 | 35.0 | 0.0 | all | 3 | 65.0 | 30.0 |
| 0.375 | 3/8 | 0.03531 | 0.10723 | 0.0365 | 14.93 | 35.0 | 0.0 | all | 3 | 65.0 | 30.0 |
| 0.375 | 3/8 | 0.0143 | 0.04904 | 0.0365 | 14.93 | 40.0 | 0.0 | all | 3 | 65.0 | 30.0 |
| 0.375 | 3/8 | 0.1306 | 0.26883 | 0.0365 | 14.93 | 35.0 | 5.0 | all | 3 | 65.0 | 30.0 |
| 0.375 | 3/8 | 0.03531 | 0.10723 | 0.0335 | 13.58 | 35.0 | 0.0 | all | 3 | 55.0 | 30.0 |
| 0.375 | 3/8 | 0.03531 | 0.10723 | 0.0584 | 23.88 | 35.0 | 0.0 | all | 2 | 65.0 | 30.0 |
| 0.25 | 2/8 | 0.03211 | 0.10723 | 0.0448 | 19.05 | 35.0 | 0.0 | all | 3 | 65.0 | 50.0 |
| 0.375 | 3/8 | 0.41929 | 0.52554 | 0.1314 | 40.3 | 20.0 | 0.0 | nearest | 1 | 65.0 | 30.0 |

## Eventi del finale (combinazione scelta)

| ancora | stazione | tipo | QC | hit | area severa |
| :-- | :-- | :-- | :-- | :-- | --: |
| 20260613T2055 | LIRS | severe_wind | B | no | 0.0 |
| 20260629T1350 | LIQN | severe_wind | B | no | 0.07292 |
| 20260718T1950 | LIML | hail | B | SI | 0.10723 |
| 20260805T1555 | LIRH | severe_wind | B | SI | 0.03793 |
| 20260826T0850 | LIMJ | funnel_cloud | B | no | 0.0 |
| 20260919T0025 | LIRE | severe_wind | B | SI | 0.08991 |
| 20261020T1555 | LIVC | severe_wind | B | no | 0.0022 |
| 20261021T1655 | LIVC | severe_wind | B | no | 0.03268 |

## Pubblicazione delle track corte: il caso LIML (prodotto, non harness)

Il filtro `split_publishable` scartava ogni track con `track_coherent=False`.
La track dello storm object su LIML (2026-07-18T19:50Z) ha **3 punti** a
19:25/19:30/19:35, tutti osservati e senza salti: era scartata per
`insufficient_points`, non per un salto. Il replay di prodotto con il fix
(`track_short_safe`, nessun segmento >110 km/h) da':

| replay 2026-07-18T19:50Z | strutture | track corte | LIML dentro | area |
| :-- | --: | --: | --: | --: |
| prima (1.3.0.86) | 1 | 0 | no | 2.238 km2 (unica struttura) |
| dopo (1.3.0.87) | **3** | **2** | **si, in 2** | 931 / 972 km2 |

L'area delle due strutture che coprono LIML e' 931 e 972 km2 contro un
inviluppo di 5.031 e 3.774 km2. Il POD finale misurato sul caso passa da miss
a hit; gli altri 7 eventi del finale non sono stati ri-eseguiti end-to-end.
La tabella sweep soprastante e' **a livello di harness** e non include questo
fix: non va letta come POD del prodotto.

## MCS: test a 18 frame (90 min)

Richiesto dal capitolato: la finestra di produzione e' 6 frame (25 min), quindi
MCS (durata >= 60 min) e' strutturalmente irraggiungibile. Test mirato su
**2026-07-01T13:20Z** con 18 frame (T0..T-85, cache da 151 MB) e 11 combinazioni
(`results/shard_screening_0_1_mcs18.json`):

| gate MCS | MCS emessi | POD | area | trans/frame |
| :-- | --: | --: | --: | --: |
| durata 60 min, echo top richiesto (produzione) | 0 | 1/1 | 17,3% | 0,026 |
| durata 20 min, area 2500, senza echo top | 2 | 1/1 | 17,3% | 0,029 |
| durata 20 min, area 5000, senza echo top | 1 | 1/1 | 17,3% | 0,027 |
| durata 40/60 min, senza echo top | 0 | 1/1 | 17,3% | 0,026 |
| baseline inviluppo, durata 20 min | 3 | 1/1 | 63,6% (scartato) | 0,112 |

Con 18 frame e una durata di **20 min** compaiono 1-2 "MCS", ma un sistema di
20 minuti non e' un MCS: la regola sarebbe un'etichetta sbagliata, non una
classe recuperata. A 40 e 60 min non compare nulla. **MCS resta non
producibile**; il test e' registrato per non ripetere la stessa scorciatoia.
La finestra di produzione resta 6 frame.

## Cosa cambia nel prodotto

* `outline` diventa un **MultiPolygon** di tutti i nuclei >=35 dBZ (cap 8), senza buffer; `outline_cores` elenca i nuclei, `outline_envelope_area_km2` conserva l'inviluppo come numero. Il client disegna ogni poligono.
* `morphology_class.value` e' la classe **stabile** dopo 3 frame; `raw_value` e `hysteresis` restano per audit. Il pannello mostra la classe (prima era solo nel JSON).
* Il contorno si risolve su `last_observed`, non sull'ultimo punto coasted; `observed_points`/`coasted_points` dichiarano la track.
* `advanced_morphology_class_reachable` = SINGLE_CELL, MULTICELL, SQUALL_LINE; `advanced_morphology_class_unreachable` dichiara BOW_ECHO, MCS e le due supercell con il motivo.

## Limiti (non dimostrato)

* **POD 3/8 nel finale** (e 4/7 nello screening): il prodotto non e' un rilevatore validato di eventi severi. La geometria non migliora il POD; migliora area e stabilita'.
* **FAR non stimabile**: 99 stazioni METAR coprono ~1,35% del territorio. L'area e' un proxy, non un FAR.
* **Classi non validate**: il METAR non contiene la morfologia. La stabilita' e' misurata, la correttezza della classe no.
* **MCS** resta non producibile con la finestra a 6 frame; **BOW_ECHO** resta non producibile senza `bow_score`; le due supercell richiedono VRADH. Non sono state inventate.
* 1 contorno su 35 resta `cell_mask_not_resolved` (difetto pre-esistente, indipendente dalla geometria).

## Riproduzione

```powershell
python scripts/validation/structure_sweep.py --screening --precache --frames 6
python scripts/validation/structure_sweep.py --screening --frames 6
python scripts/validation/structure_sweep.py --final --combo-file docs/evidence/structure_sweep_20261127/results/combos_final_shortlist.json --frames 6
```

I file `cache/*.pkl` (frame VMI, ~50 MB ciascuno) NON vanno nel repo: sono ricostruibili con `--precache`.
