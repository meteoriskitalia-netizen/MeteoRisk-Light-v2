# Trend per l'evoluzione del nowcast — varianti V-a..V-d (2026-11-27)

## Metodo

8 ancore pesanti, 6 frame SRI ciascuna, frame precedenti **allineati al piu'
recente col moto inverso** (coordinate lagrangiane): ogni cella e' ferma e si
misura solo come cambia. Per ogni regione (componente >=0,5 mm/h, >=200 px,
storia >=3 passi) si stima il trend di area (frazione bagnata) e di intensita'
(media sul bagnato) e si confronta con il cambiamento **reale** misurato nella
stessa regione trasportata in avanti, all'osservato futuro.

Soglia di implementazione: **|rho| >= 0,2 con segno positivo**.

## Tabella delle varianti (rho di Spearman; n = 196 regioni, 8 ancore)

| variante | area @30 | area @60 | int @30 | int @60 | esito |
| :--- | ---: | ---: | ---: | ---: | :--- |
| V-linear (tutte) | -0,2626 | -0,2270 | +0,1550 | +0,0667 | no |
| V-a convettive | **-0,3387** | **-0,2907** | **+0,2903** | **+0,2242** | **int OK** |
| V-a stratiformi | -0,0579 | -0,0691 | +0,0489 | -0,0114 | no |
| V-b logistico | -0,2360 | -0,2061 | +0,1508 | +0,0511 | no |
| V-c memoria pesata | -0,3586 | -0,2557 | +0,1316 | +0,2137 | misto (area no) |
| V-d NWP (CAPE/shear) | — | — | — | — | **non misurabile** |

Pearson per V-a convettive: int@30 **+0,377**, int@60 **+0,212**.

Lettura:
- Il trend **non predice l'area** in nessuna variante: tutti i rho d'area sono
  negativi. Separare convettive/stratiformi peggiora l'area delle convettive.
- L'**intensita' delle convettive** e' l'unico segnale positivo e stabile:
  +0,2903 a +30 e +0,2242 a +60 (Spearman), entrambi sopra 0,2.
- V-b logistico non migliora; V-c migliora l'intensita' a +60 ma l'area resta
  negativa; V-d non e' misurabile: l'archivio Open-Meteo restituisce **CAPE
  0/24** (misurato il 2026-11-01), il vento a 100 m non e' shear 0-6 km.

## Cosa si implementa

**V-a, solo intensita' delle celle convettive** (massimo >= 12 mm/h, proxy di
40 dBZ con Marshall-Palmer), in coordinate lagrangiane:

- trend lineare dell'intensita' su 3-4 passi;
- fattore `1 + slope*lead/i0`, limitato a **±30% su 90 min**;
- applicato fino a **+60** (rho misurato), con **dissolvenza lineare fino a
  +90** (oltre +60 non c'e' misura);
- le celle stratiformi restano invariate;
- celle appena nate (storia <3) restano invariate e sono dichiarate;
- nessun generatore esterno: e' la storia del dato, non CAPE o cascata.

Payload `evolution`: variante, ruolo, soglia di classe, calibrazione (rho,
Pearson, n), regioni totali/convettive/con trend, pixel cambiati e fattore
min/max per lead. Se la calibrazione scende sotto 0,2 il layer si spegne con
`null_reason = calibration_rho_below_threshold`.

## A/B (6 ancore pesanti, codice di produzione)

Rapporti d'area mediani previsto/osservato:

| lead | V1 0,5 / 5 / 10 / 20 | TREND 0,5 / 5 / 10 / 20 |
| ---: | :--- | :--- |
| +15 | 0,987 / 1,039 / 1,066 / 1,133 | 0,982 / 0,996 / 1,016 / 1,062 |
| +30 | 0,952 / 1,027 / 1,076 / 1,003 | 0,941 / 0,961 / 0,987 / 0,884 |
| +60 | 0,940 / 1,048 / 1,103 / 0,914 | 0,906 / 0,877 / 0,888 / **0,769** |
| +90 | 0,927 / 0,995 / 0,937 / 0,973 | 0,927 / 0,995 / 0,937 / 0,973 |

CSI/FSS a 5 mm/h (mediana FSS su 15 km):

| lead | CSI5 V1 -> TREND | delta | FSS5 V1 -> TREND | delta |
| ---: | ---: | ---: | ---: | ---: |
| +15 | 0,5461 -> 0,5410 | -0,0051 | 0,8779 -> 0,8750 | -0,0029 |
| +30 | 0,4048 -> 0,3934 | **-0,0115** | 0,7340 -> 0,7261 | -0,0079 |
| +60 | 0,2629 -> 0,2438 | **-0,0191** | 0,5366 -> 0,5126 | **-0,0240** |
| +90 | 0,1582 -> 0,1582 | 0 | 0,3225 -> 0,3225 | 0 |

**Esito: NON passa.** A +30 il CSI5 cala di 0,0115 (> 0,01); a +60 cala di
0,0191 e il FSS5 di 0,0240; il rapporto d'area a 20 mm/h a +60 e' 0,769
(< 0,8). La correlazione positiva non basta: applicata al campo, il trend
peggiora il prodotto. Il layer e' quindi **spento** in produzione
(`config["nowcast_trend"]["enabled"] = False`) con il motivo dichiarato nel
payload `evolution`. Il codice resta per una futura ri-valutazione.

Immagini dell'A/B: `docs/evidence/nowcast_trend_ab_20261127/` (osservato |
V1 | TREND a +30/+60 per 6 ancore).

## Limiti

- L'area non e' prevista dal trend: resta il difetto del nucleo troppo pieno
  (V1-V5) e va trattata separatamente.
- Il proxy 40 dBZ e' 12 mm/h (Marshall-Palmer); non e' la VMI in dBZ.
- La calibrazione e' su 8 ancore, non una climatologia; il layer si spegne da
  solo se la calibrazione scende sotto soglia.
