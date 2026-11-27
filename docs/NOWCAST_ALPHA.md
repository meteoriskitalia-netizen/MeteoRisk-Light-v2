# Resa del nowcast — alpha graduale (2026-11-27)

## 1. Valori trovati nel codice (prima)

`scripts/radar_engine/nowcast_field.py`, `_rgba()`:

- `ALPHA_FLOOR_MMH = 0.5` → **alpha 0** sotto 0,5 mm/h
- `ALPHA_FULL_MMH = 2.0` → **alpha pieno** (235/255) a 2,0 mm/h
- rampa lineare fra i due; primo nodo palette a 0,5 mm/h
- `ALPHA_MAX = 235`; overlay client a opacità 0,75

## 2. Diagnosi sul run 01:45Z (notte, pioggia debole)

Calcolo di produzione (ensemble spento), confronto con l'SRI all'ancora:

| grandezza | previsto +30 | osservato |
| :--- | ---: | ---: |
| pixel bagnati (>0,05 mm/h) | 39.897 | 35.212 |
| frazione bagnata in **0,5-2 mm/h** | **34,0%** | 41,7% |
| frazione bagnata in 0,05-0,5 mm/h | 55,6% | 46,4% |
| frazione ≥2 mm/h | 10,3% | 12,0% |

Con il vecchio alpha erano visibili **17.542 / 39.897 = 44%** dei pixel
bagnati: la parte 0,5-2 mm/h (la maggioranza della parte visibile) era resa
come rampa quasi trasparente, quindi restava l'anello di transizione. Il
previsto e l'osservato hanno la **stessa prevalenza di pioggia debole**: il
problema era la resa, non la copertura.

## 3. Fix

```
alpha = 0                per v <= 0,15 mm/h
alpha = 0,40 * 235        a 0,5 mm/h
alpha = 235              da 1,5 mm/h in su
primo nodo palette       0,3 mm/h (ciano)
```

Il renderer **non modifica il campo**: `_rgba` legge soltanto. La maschera
del dato (>0 mm/h) resta identica; l'alpha è zero solo dove il valore è 0 o
fuori dominio. I pixel 0,05-0,15 restano sotto la soglia di disegno
(dichiarato): è l'unica parte bagnata non visibile, come da specifica.

Sul 01:45Z: visibili **32.507 / 39.897 = 81%** (da 44%), **0** pixel
colorati dove il valore è 0.

## 4. A/B su 6 ancore pesanti (archivio DPC, 12/16Z)

Scelte dal set di conferma, per copertura e moto: 4 convettive (01/11 16Z,
20/10 16Z, 10/09 16Z, 08/10 16Z) e 2 stratiformi (09/11 16Z, 29/05 12Z).
Immagini: `docs/evidence/nowcast_heavy_20261127/<YYYYMMDD>T160000Z_<lead>.png`
(3 colonne: osservato | previsto resa vecchia | previsto resa nuova).

| ancora | cop. ≥0,5 | cop. ≥5 | visibili @30 vecchio→nuovo | CSI 0,5 @30 | aspetto @30/@60/@90 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| 2026-11-01 16Z | 24,6% | 7,8% | 210.127 → 237.571 | 0,702 | 1,00 / 0,97 / 1,28 |
| 2026-10-20 16Z | 18,0% | 4,7% | 162.185 → 197.551 | 0,685 | 0,88 / 0,55 / 0,80 |
| 2026-09-10 16Z | 14,4% | 4,2% | 129.341 → 153.703 | 0,680 | 0,95 / 1,00 / 1,20 |
| 2026-10-08 16Z | 13,7% | 2,5% | 127.201 → 171.119 | 0,555 | 1,03 / 1,07 / 1,10 |
| 2026-11-09 16Z | 6,2% | 0,1% | 57.383 → 82.744 | 0,504 | 1,02 / **1,49** / **1,82** |
| 2026-05-29 12Z | 1,9% | 0,2% | 15.141 → 23.720 | 0,333 | 0,87 / 0,60 / 0,60 |

**Metriche pooled** (identiche con resa vecchia e nuova: l'alpha non tocca
il campo):

| lead | CSI 0,5 | CSI 2 | CSI 5 |
| ---: | ---: | ---: | ---: |
| +15 | 0,744 | 0,649 | 0,546 |
| +30 | 0,638 | 0,531 | 0,405 |
| +60 | 0,506 | 0,398 | 0,263 |
| +90 | 0,407 | 0,283 | 0,158 |

**Esito visivo.** Sull'ancora stratiforme 2026-11-09 il vecchio mostrava
solo contorni/graffi; il nuovo mostra l'area piena, coerente con l'osservato.
Sulle convettive il guadagno è più contenuto ma le aree deboli attorno ai
nuclei sono piene, non filamenti.

**Eccezione dichiarata.** 2026-11-09 a +90 ha aspetto 1,82 (>1,5): è la
forma del campo (nucleo debole e allungato), non la resa; l'alpha non cambia
la geometria. Le altre 17 combinazioni ancora/lead sono ≤1,5 (la 05-29 è
sotto 1).

## 5. Test

- `test_nowcast_field.py`: **36 passed**, inclusi alpha graduale e
  invarianza del campo (`_rgba` non modifica l'array; 0 mm/h → alpha 0;
  0,5 → 40%; 1,5 → pieno).
- `radar_engine`: **986 passed**.
- `test_html_js_syntax.py`: 1 passed; `test_sw_cache_policy.mjs`: PASS.

## 6. Limiti

- Il fix cambia **solo l'opacità**: non aggiunge copertura dove il campo è 0
  e non tocca le metriche. La parte 0,05-0,15 mm/h resta invisibile per
  scelta (soglia di disegno 0,15).
- L'aspetto a +90 della stratiforme 2026-11-09 resta fuori soglia: è un
  limite del moto/forma, non della resa.
- Il confronto con l'osservato usa la stessa rampa alpha sul campo SRI; il
  client radar osservato ha una sua resa, quindi il confronto è
  like-for-like sulla geometria, non sui colori del client.
