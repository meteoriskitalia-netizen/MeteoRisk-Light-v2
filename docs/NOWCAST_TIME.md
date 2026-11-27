# Intervalli reali fra i frame del nowcast (2026-11-27)

## 1. Dove il codice assumeva 5 minuti

| punto | prima | ora |
| :--- | :--- | :--- |
| `estimate_motion` | normalizzava con `scale = 300/dt_s`; accettava coppie fino a 15 min e le **riscalava** | normalizza col `dt_s` reale a 300 s; coppie fuori banda **4-6 min scartate** |
| `estimate_motion` | ultime 3 coppie per indice | fino a **6 coppie candidate**, si tengono le ultime 3 **valide** |
| `STEP_MIN = 5` | usato come passo fisso anche per lo spostamento (`lead // STEP_MIN`) | resta il **riferimento** del vettore (u,v per 300 s); lo spostamento e' `lead/300` frazionario (`coarse_warp`, trasporto singolo) |
| `STEP_MIN_BY_LEAD` | passo pubblicato per lead | invariato: e' il contratto del player (5/15 min), non il dt dei frame |
| `fetch.plan_window` | `period_s` da `findLastProductByType` (default 300) | invariato: il periodo dichiarato dal prodotto; se il fornitore dichiara 10 min, i candidati sono a 10 min |
| payload | solo `motion_pair_dt_s` | `frame_intervals_min`, `gap_minutes`, `motion_pair_dt_band_s`, `motion_pairs_discarded`, `lead_origin`, `lead_from_now_min` |

## 2. Timestamp reali misurati

Otto finestre da 12 frame (96 frame, 88 intervalli), scaricate dall'archivio
DPC SRI con `anchor_ms` esplicito, orari presi dai `time_ms` dei raster (non
dal nome):

| ancora | n | intervalli (min) | buchi |
| :--- | ---: | :--- | :--- |
| 2026-11-26T13:25Z | 12 | 5,0 x 11 | 0 |
| 2026-11-01T16:00Z | 12 | 5,0 x 11 | 0 |
| 2026-10-20T16:00Z | 12 | 5,0 x 11 | 0 |
| 2026-09-10T16:00Z | 12 | 5,0 x 11 | 0 |
| 2026-05-29T12:00Z | 12 | 5,0 x 11 | 0 |
| 2026-08-13T16:00Z | 12 | 5,0 x 11 | 0 |
| 2026-07-27T12:00Z | 12 | 5,0 x 11 | 0 |
| 2026-11-27T02:50Z | 12 | 5,0 x 11 | 0 |

**Risultato: 88/88 intervalli a 5,0 min, zero buchi** in queste finestre.
La premessa "i frame non sono tutti a 5 minuti" **non e' confermata** su
questo campione: il prodotto SRI DPC e' pubblicato a 5 min. Il codice pero'
non lo assume piu': se un buco c'e', viene scartato e dichiarato.

## 3. Convenzione dei lead (dichiarata)

I lead sono contati **dallo slot del frame piu' recente** (`source_slot`),
non dall'orologio:

```
valid_time = source_slot + lead
```

Se il frame e' alle 01:45 e l'orologio alle 01:52, il +15 e' **02:00**
(15 min dal frame), non 02:07. Il client mostra il `valid_time` accanto al
lead (es. `+15 min · 02:00`), quindi la convenzione e' coerente. Ogni lead
pubblica anche `lead_from_now_min` (quanto il valid_time dista
dall'orologio del run; negativo = istante gia' passato).

**Verifica live (2026-11-27T07:32:24Z, slot 07:20Z):** `lead_origin =
source_slot`, `frame_intervals_min = [5,5,5,5,5]`, `gap_minutes = []`,
banda `[240,360]`, 3 coppie, 0 scartate, 6 frame. `+5` -> valid
**07:25** con `lead_from_now_min = -7,4` (il frame era gia' vecchio di
7,4 min: il +5 e' 5 min dopo il frame, non 5 min dopo l'orologio);
`+30` -> valid **07:50** con `lead_from_now_min = +17,6`. E' esattamente
la convenzione dichiarata, resa visibile nei numeri.

## 4. Effetto sul moto con un buco noto

Caso 2026-11-26T13:25Z, 6 frame a 5 min. Tolto il terzo frame si crea un
buco di 10 min (timestamp 13:00, 13:05, 13:15, 13:20, 13:25):

| confronto | coppie | dt usati | mediana | p95 |
| :--- | ---: | :--- | ---: | ---: |
| full vs gap **nuovo** (coppia scartata) | 3 vs 3 | 300 s | **0,542 px/5min (6,51 km/h)** | **4,158 px (49,90 km/h)** |
| full vs gap **vecchio** (coppia riscalata) | 3 vs 3 | 300/600 s | 0,331 px (3,97 km/h) | 3,276 px (39,31 km/h) |
| vecchio vs nuovo sullo stesso buco | — | — | **0,538 px (6,46 km/h)** | **4,586 px (55,03 km/h)** |

Il buco cambia il moto in modo misurabile (mediana ~6,5 km/h, p95 ~50 km/h):
la coppia a 10 min riscalata a 5 min non e' equivalente a una coppia vera a
5 min. Il cap a 110 km/h resta e sostituisce i vettori fuori soglia.

## 5. CSI prima/dopo

Le 6 ancore pesanti (4 convettive + 2 stratiformi) hanno **tutte** intervalli
di 5,0 min: la banda 4-6 tiene le stesse coppie e i campi sono identici.
CSI pooled prima -> dopo:

| soglia | +15 | +30 | +60 | +90 |
| ---: | ---: | ---: | ---: | ---: |
| 0,5 | 0,744 -> 0,744 | 0,638 -> 0,638 | 0,506 -> 0,506 | 0,407 -> 0,407 |
| 2,0 | 0,649 -> 0,649 | 0,531 -> 0,531 | 0,398 -> 0,398 | 0,283 -> 0,283 |
| 5,0 | 0,546 -> 0,546 | 0,405 -> 0,405 | 0,263 -> 0,263 | 0,158 -> 0,158 |

Delta entro **0,001** (arrotondamento a 3 decimali): nessuna regressione.
Dati in `docs/evidence/nowcast_time_20261127/heavy_csi.json`.

## 6. Test

`test_nowcast_field.py`: **39 passed**, inclusi:
- coppia a 10 min scartata, 3 coppie valide usate, vettore ~1 px/5min;
- nessuna coppia in banda -> `None` con warning;
- payload con `frame_intervals_min=[5,10,5]`, `gap_minutes=[10]`,
  `lead_origin=source_slot`, `lead_from_now_min=5.0`.

## 7. Limiti

- Il campione e' di 8 finestre (96 intervalli), non una climatologia: non
  dimostra che i buchi non esistano mai, dimostra che in queste finestre non
  ce ne sono.
- Il test del buco e' **sintetico** (frame tolto a posteriori), non un buco
  reale osservato.
- Se un fornitore passasse a una cadenza di 10 min, la banda 4-6 scarterebbe
  tutte le coppie e il nowcast uscirebbe `motion_unavailable`: la banda va
  cambiata in `config["nowcast_pair_dt_band_s"]` e dichiarata.
