# Affidabilita' a +30/+60/+90 (2026-11-27)

## Criterio fissato PRIMA della misura

Un'ancora e' **affidabile** a un lead se valgono tutte e tre:

1. area prevista / osservata in **[0,6; 1,4]** a **0,5 mm/h e 5 mm/h**;
2. centroide previsto entro **20 km** dall'osservato (maschera >= 0,5);
3. copertura >= **50%** dell'area osservata >= 5 mm/h da parte della
   previsione >= 5 mm/h.

Affidabilita' = frazione di ancore che passano tutte e tre. Nessuna soglia
e' stata adattata dopo la misura.

## Giornate dichiarate prima (12, frame archiviati)

2026-11-01 16Z (convettivo veloce), 2026-10-20 16Z (convettivo veloce),
2026-09-10 16Z (convettivo), 2026-10-08 16Z (convettivo),
2026-11-26 13:25Z (convettivo veloce), 2026-11-09 16Z (stratiforme veloce),
2026-05-29 12Z (stratiforme), 2026-08-29 16Z (stratiforme),
2026-07-27 12Z (debole), 2026-09-30 12Z (debole), 2026-07-08 12Z (debole),
2026-08-13 16Z (debole).

Script: `scripts/validation/nowcast_reliability_90.py`.
Output: `docs/evidence/nowcast_reliability_90_20261127/reliability90.json`
e `.md`.

## Risultato (motore di oggi: trasporto 3 km + V4 + HF)

| lead | affidabili | frazione | copertura 0,5 | IoU 0,5 | bias 0,5 | copertura 5 | IoU 5 | bias 5 | corr 0,5 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| +30 | 3/12 | **25,0%** | 0,5477 | 0,4220 | 1,0577 | 0,3757 | 0,2212 | 1,5362 | 0,5414 |
| +60 | 0/12 | **0,0%** | 0,4243 | 0,3063 | 1,0646 | 0,2154 | 0,1351 | 1,9414 | 0,4149 |
| +90 | 0/12 | **0,0%** | **0,3533** | **0,2389** | **1,0746** | 0,1361 | 0,0784 | 0,9874 | 0,3371 |

## Collo di bottiglia a +90

- **centroide fuori 20 km in 12/12** ancore: mediana **76,4 km**
  (min 26,7, max 435,0); a +60 mediana 48,6 km; a +30 24,2 km.
- **copertura >=5 mm/h sotto 50% in 12/12**.
- L'area a 0,5 mm/h e' invece quasi giusta (bias **1,075**, dentro 1,3):
  non e' un problema di "quanta" pioggia, e' un problema di **dove**.
- A +60 il bias a 5 mm/h e' 1,94 (troppo pieno) mentre a +90 e' 0,99:
  il nucleo si assottiglia troppo tardi; il difetto dominante resta lo
  spostamento.

## Distanza dall'obiettivo 90% di sovrapposizione

A +90: copertura **35,3%**, IoU **23,9%**, bias 1,075. L'obiettivo
"copertura >=90% con bias <=1,3" **non e' raggiunto e non e' vicino**.
Poiche' il bias e' 1,075, allargare la previsione non e' la leva: servirebbe
spostarla. Il numero di partenza e' **35,3% di copertura / 23,9% di IoU**.

## Riproduzione

```
python scripts/validation/nowcast_reliability_90.py
```

Le osservazioni sono in cache locale (`_diag/reliability90_cache`) per non
riscaricare gli stessi slot; il calcolo usa il nowcast di produzione con
`nowcast_trend` spento e `nowcast_clouds` spento (le nuvole non entrano
nelle metriche di pioggia).
