# Decadimento del nucleo — V1..V5 (2026-11-27)

## Metodo

8 ancore pesanti, 6 frame SRI. Il nowcast di produzione (trend spento) viene
calcolato una volta; le varianti V2..V5 sono applicate al campo nativo e
confrontate con l'osservato futuro agli stessi lead. Nessun dato futuro
entra nel calcolo.

| variante | cosa fa |
| :--- | :--- |
| V1 | trasporto attuale (nessun decadimento) |
| V2 | fattore globale misurato: 0,85 a +30, 0,63 a +60 (estrapolato a +90) |
| V3 | stesso fattore SOLO sopra 5 mm/h |
| V4 | erosione del solo >5 mm/h dal RETRO (lato opposto al moto), `round(lead/30)` px |
| V5 | V3 + V4 |

## Rapporti d'area (mediana previsto/osservato)

| variante | lead | 0,5 | 5 | 10 | 20 |
| :--- | ---: | ---: | ---: | ---: | ---: |
| V1 | +15 | 0,990 | 1,039 | 1,066 | 1,105 |
| V1 | +30 | 0,952 | 1,027 | 1,076 | 1,003 |
| V1 | +60 | 0,929 | 0,954 | 1,011 | 0,914 |
| V1 | +90 | 0,888 | 0,855 | 0,931 | 0,925 |
| V2 | +30 | 0,914 | 0,873 | 0,836 | **0,778** |
| V2 | +60 | 0,755 | **0,461** | **0,408** | **0,321** |
| V3 | +30 | 0,952 | 0,873 | 0,836 | **0,778** |
| V3 | +60 | 0,929 | **0,461** | **0,408** | **0,321** |
| **V4** | +15 | 0,990 | 1,030 | 1,065 | 1,105 |
| **V4** | +30 | 0,952 | 1,012 | 1,075 | 1,003 |
| **V4** | +60 | 0,929 | 0,912 | 1,001 | 0,910 |
| **V4** | +90 | 0,888 | 0,811 | 0,916 | 0,920 |
| V5 | +30 | 0,952 | 0,847 | 0,829 | **0,778** |
| V5 | +60 | 0,929 | **0,441** | **0,387** | **0,319** |

Lettura: V2, V3 e V5 distruggono l'area a 5/10/20 (0,32-0,46 a +60), fuori
dalla banda 0,8-1,3. **V4 e' l'unica che resta dentro la banda a ogni
soglia e lead**, con l'area a 0,5 invariata (0,929-0,990).

## CSI e FSS a 5 mm/h

| lead | CSI5 V1 -> V4 | delta | FSS5 V1 -> V4 | delta |
| ---: | ---: | ---: | ---: | ---: |
| +15 | 0,5253 -> 0,5260 | +0,0007 | 0,8779 -> 0,8778 | -0,0001 |
| +30 | 0,3719 -> 0,3723 | +0,0004 | 0,7340 -> 0,7335 | -0,0005 |
| +60 | 0,2513 -> 0,2518 | +0,0005 | 0,5366 -> 0,5339 | -0,0027 |
| +90 | 0,1561 -> 0,1558 | -0,0003 | 0,3525 -> 0,3483 | -0,0042 |

Nessun peggioramento oltre 0,01: **V4 passa** (CSI5 migliora leggermente,
FSS5 cala al massimo di 0,0042).

## Cosa e' implementato

`scripts/radar_engine/nowcast_decay.py`, attivo in produzione:

- soglia 5,0 mm/h, componenti >=20 px;
- per ogni componente: versore del moto = mediana `(u,v)` del nucleo,
  proiezione `s = x*ux + y*vy`, banda `s <= min(s) + r` abbassata a 4,9 mm/h;
- `r = round(lead/30)` px: 0 sotto +15, 1 a +15/+30, 2 a +45/+60, 3 a +75/+90;
- applicata **dopo** il ripristino HF (prima HF la riporterebbe sopra 5);
- i pixel erosi restano a 4,9: la copertura a 0,5/2 mm/h non cambia;
- payload `decay`: metodo, soglia, pixel cambiati e raggio per lead;
- configurazione `nowcast_decay` (enabled, threshold, min_region_px,
  px_per_30min).

Immagini osservato | V1 | V4 a +30/+60:
`docs/evidence/nowcast_decay_20261127/` (8 file, 74-356 kB).

## Limiti

- L'erosione e' una contrazione **numerica** proiettata sul moto, non una
  legge fisica; su nuclei molto irregolari puo' lasciare piccoli vuoti
  (visibili a piena risoluzione) invece di un bordo netto.
- Tocca solo >5 mm/h: la forma a 2 mm/h e l'aspetto a 2 mm/h sono invariati.
- La misura e' su 8 ancore, non una climatologia.
- V1 aveva gia' rapporti d'area dentro 0,8-1,3: V4 non "corregge" un errore
  medio di area, riduce il nucleo intenso dove l'osservato si svuota; il
  guadagno e' piccolo e dichiarato (CSI5 +0,0005).
