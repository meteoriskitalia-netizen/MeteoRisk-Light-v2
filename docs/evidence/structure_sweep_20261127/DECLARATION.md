# Structure sweep — dichiarazione PRIMA dei risultati (2026-11-27)

Questo file e' scritto **prima** di eseguire lo sweep. Le giornate, le metriche,
l'obiettivo e i criteri di scarto sono fissati qui e non vengono cambiati dopo
aver visto i numeri.

## Perche' 6 + 8 e non 6 + 12

L'archivio radar DPC utilizzabile in replay parte dal **2026-05-20**. Nelle
etichette METAR/SPECI (`validation-labels`, 42.391 righe, sha256
`5F8023F65E7D5393...B5DED2C2`) gli eventi severi con QC **A/B** nel periodo
2026-05-20 -> 2026-11-27 sono **15**, distribuiti su **14 giornate**. Non
esistono altre giornate con un evento severo A/B: le 12 giornate finali
richieste non sono disponibili. Si usano quindi **6 giornate di screening** e
**8 giornate finali**, senza escludere nessuna giornata con eventi. Le giornate
senza eventi non entrano nel POD; l'area marcata severa viene comunque misurata
su ogni ancora.

## Giornate di SCREENING (6 giornate, 7 eventi, 7 ancore)

| giornata | ancora UTC | evento | stazione | tipo | QC |
| :-- | :-- | :-- | :-- | :-- | :-- |
| 2026-05-28 | 14:55 | 14:55 | LIPL | hail | B |
| 2026-06-10 | 15:55 | 15:55 | LIPI | hail | B |
| 2026-07-01 | 13:20 | 13:20 | LIMF | hail | B |
| 2026-07-01 | 14:50 | 14:50 | LIMP | hail | B |
| 2026-08-18 | 05:50 | 05:50 | LIPR | funnel_cloud | B |
| 2026-08-27 | 20:55 | 20:55 | LIRS | severe_wind | B |
| 2026-10-11 | 23:50 | 23:50 | LIPX | hail | B |

## Giornate FINALI (8 giornate, 8 eventi, 8 ancore)

| giornata | ancora UTC | evento | stazione | tipo | QC |
| :-- | :-- | :-- | :-- | :-- | :-- |
| 2026-06-13 | 20:55 | 20:55 | LIRS | severe_wind | B |
| 2026-06-29 | 13:50 | 13:50 | LIQN | severe_wind | B |
| 2026-07-18 | 19:50 | 19:50 | LIML | hail | B |
| 2026-08-05 | 15:55 | 15:55 | LIRH | severe_wind | B |
| 2026-08-26 | 08:50 | 08:50 | LIMJ | funnel_cloud | B |
| 2026-09-19 | 00:25 | 00:28 | LIRE | severe_wind | B |
| 2026-10-20 | 15:55 | 15:55 | LIVC | severe_wind | B |
| 2026-10-21 | 16:55 | 16:55 | LIVC | severe_wind | B |

L'ancora e' l'istante dell'evento arrotondato per difetto a 5 minuti (stessa
regola di `--as-of`). L'evento cade quindi fra T0 e T0+5 min; la finestra del
motore e' T0..T-25 (6 frame) o T0..T-85 (18 frame nella prova MCS).

## Verita' e limiti

* Verita' = METAR/SPECI (NOAA/NCEI via IEM), non ESWD. Solo eventi
  `hail`, `severe_wind`, `tornado`, `funnel_cloud` con QC A/B. La rete e' di
  **99 stazioni**: un evento non rilevato dal METAR non e' un'assenza di evento.
* Il METAR non dice la morfologia della struttura. La classe (SINGLE_CELL,
  MULTICELL, SQUALL_LINE, MCS) **non e' validabile** con queste etichette: si
  misura la **stabilita'** della classe e la **copertura degli eventi** da parte
  delle strutture severe, non la correttezza della classe.
* `FAR` non e' stimabile: 99 stazioni coprono ~1,35% del territorio. Si usa un
  **proxy dichiarato**: frazione dell'area del dominio radar coperta dalle
  strutture severe (`area_frac`). Non e' un FAR e non viene chiamato FAR.
* Con 7 eventi in screening e 8 in finale, qualunque soglia scelta e'
  un'ipotesi, non una taratura. Il risultato finale e' una **screening
  experiment**, non una validazione statistica.

## Parametri esplorati (screening: 48 combinazioni, seed 20261127)

| asse | valori |
| :-- | :-- |
| soglia di rilevamento | 20 / 25 / 30 dBZ |
| area minima | 30 (baseline) / 50 / 100 / 200 km2 |
| persistenza richiesta | 2 / 3 / 4 frame |
| SOI minimo per pubblicare | 55 / 65 / 75 |
| isteresi di classe | 1 / 2 / 3 frame consecutivi |
| allungamento per SQUALL_LINE | 2,5 / 3,0 / 3,5 (PCA della maschera, non bbox) |
| area MCS | 2.500 / 5.000 / 8.000 km2 |
| durata MCS | 20 / 40 / 60 min |
| core dBZ del poligono | 20 (= inviluppo attuale) / 35 / 40 / 45 |
| buffer del poligono | 0 (= attuale) / 5 / 10 / 15 km |

La combinazione **baseline** e' quella di produzione: 20 dBZ, area 30 km2,
persistenza 2, SOI 65, isteresi 1, elongazione 3,0, MCS 5.000 km2 / 60 min,
core 20 dBZ, buffer 0. Include il comportamento reale del 2026-11-27, non una
sua approssimazione.

## Definizioni delle metriche

* **POD**: evento severo A/B la cui stazione cade dentro almeno una maschera
  pubblicata di una struttura **severa** alla sua ora (finestra 0..+5 min).
  Struttura severa = record pubblicato con `level` in `strong`/`intense` e
  classe non nulla.
* **area_frac**: unione delle maschere severe / area valida del dominio radar,
  alla stessa ancora. Media e massimo sulle ancore.
* **transizione**: cambio di classe fra due frame consecutivi della stessa
  track, dopo l'isteresi. Si contano anche le transizioni da/verso `None`;
  si riporta a parte `class_transitions` (solo fra due classi non nulle).
* **entropia**: entropia normalizzata (log 3) della distribuzione delle classi
  finali. Serve a impedire che la "stabilita'" si compri mettendo tutto in una
  classe.
* **null_class**: record pubblicati senza classe. Non sono severe per il POD.

## Obiettivo e test di sanita' (fissati prima)

```
SCARTO se area_frac > 0,25            (piu' di 25% del dominio marcato severo)
SCARTO se max_share > 0,90 e record >= 20  (una sola classe >90%)
score = POD - 0,50 * transition_rate + 0,10 * entropy
```

L'area e' un **gate**, non un termine da compensare: una combinazione con POD
alto e area enorme non vince. La stabilita' non si compra fondendo tutto in una
classe: il gate `max_share` e il termine entropia lo impediscono.

## Cosa NON viene fatto

* Nessun dato sintetico: solo frame VMI DPC reali e maschere derivate.
* Nessuna soglia "aggiustata" dopo aver visto il finale: il finale serve a
  verificare la combinazione scelta nello screening, non a sceglierla.
* Nessun campo `morphology_organization` inventato: echo top, VIL e overhang
  restano `null` quando i CAPPI non vengono scaricati. La classe 2D non li usa.
* MCS senza echo top e' una **variante dichiarata** (2D): se vince, nel
  prodotto va marcata come candidato con durata osservata e finestra
  troncata, non come MCS confermato.
