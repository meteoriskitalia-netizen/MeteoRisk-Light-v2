# Nowcast probabilistico — esito della calibrazione

> **Prima iterazione (traslazione rigida), superata.** La diagnosi di questo
> documento (sotto-dispersione, sovra-previsione, nucleo rigido) ha portato
> all'iterazione con moto variabile + deformazione + conservazione della massa:
> risultati e verdetto aggiornati in `docs/NOWCAST_ENSEMBLE_CONFERMA.md`.

Generato da `scripts/validation/nowcast_ensemble_backtest.py` (tabelle: `nowcast_ensemble_report.py`) il 2026-11-25T12:46:43Z.

## Campione e metodo

**12 giornate** (le stesse del backtest della cascata: 4 convettive, 4 stratiformi, 4 deboli) × 2 ancore = **24 casi**, 24 valutabili, lead 15/30/60 min. Stesso `compute()` della produzione con cascata + matching + ensemble accesi. Verità = SRI osservato. `motion_inflation = 1.00`, `noise_inflation = 1.00`.

stesso compute() della produzione con cascata+matching+ensemble accesi; verita' = SRI osservato; Brier/reliability sui pixel del dominio, CRPS fair su 40000 pixel sistematici per caso/lead, climatologia leave-one-day-out.

## Costo misurato

| voce | valore |
| :--- | ---: |
| secondi totali del backtest | 1675.2 |
| secondi di `compute()` (24 casi) | 725.7 |
| secondi del solo ensemble (24 casi) | 458.7 |
| ensemble per caso (media) | 19.1 s |

Il dettaglio per voce (trasporto, rumore, matching, statistiche) è nel caso reale 2026-11-01 11:00 riportato in `docs/NOWCAST_ENSEMBLE_METODO.md` §5: ~5,4 s trasporto, ~5,9 s rumore, ~9,0 s matching+decadimento, ~3,0 s statistiche, **~27 s** per caso contro ~10-12 s del deterministico.

Le WebP di probabilità misurate sulla griglia nativa (1400×1200, RGBA): **338.148 / 354.646 / 366.010 byte** per +15/+30/+60 (~1,06 MB in totale), contro ~80 kB delle tre WebP di intensità. La quantizzazione a 0,05 o 0,10 non riduce il peso (1.058.684 / 917.898 byte): il costo è l'entropia spaziale della mappa, non i livelli. `ensemble.image_leads` permette di pubblicare il solo lead centrale (355 kB).

### Approssimazione del trasporto (composizione vs re-iniezione)

| lead | differenza media p | differenza max | pixel >0,2 | Brier composite | Brier advect |
| ---: | ---: | ---: | ---: | ---: | ---: |
| +15 | 0.023 | 0.80 | 2.0% | 0.1831 | 0.1927 |
| +30 | 0.041 | 0.90 | 5.0% | 0.2256 | 0.2400 |
| +60 | 0.061 | 0.95 | 9.4% | 0.2254 | 0.2561 |

Costo del solo ensemble sul caso A/B: **23.7 s** (composite) contro **57.7 s** (advect), rapporto 2.4×. La composizione dopo il trasporto non è un dettaglio numerico: a +60 cambia in media 6 punti percentuali di probabilità e il 9,4% dei pixel di oltre 0,2. Su questo caso ha Brier migliore, ma è un'approssimazione dichiarata, non un'equivalenza.

## Brier e Brier skill score

BSS = 1 − BS/BS_clim; climatologia = frequenza base del periodo. BSS > 0 = meglio della climatologia.

| soglia (mm/h) | lead | BS | BS clim | **BSS** | n pixel | freq. oss. |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | +15 | 0.0654 | 0.0575 | **-0.136** | 20901048 | 0.0593 |
| 0.5 | +30 | 0.0832 | 0.0575 | **-0.447** | 20814333 | 0.0605 |
| 0.5 | +60 | 0.0980 | 0.0575 | **-0.703** | 20751582 | 0.0640 |
| 2.0 | +15 | 0.0333 | 0.0298 | **-0.116** | 20901048 | 0.0298 |
| 2.0 | +30 | 0.0430 | 0.0298 | **-0.442** | 20814333 | 0.0304 |
| 2.0 | +60 | 0.0464 | 0.0298 | **-0.554** | 20751582 | 0.0322 |
| 5.0 | +15 | 0.0155 | 0.0133 | **-0.169** | 20901048 | 0.0131 |
| 5.0 | +30 | 0.0216 | 0.0133 | **-0.625** | 20814333 | 0.0132 |
| 5.0 | +60 | 0.0240 | 0.0133 | **-0.810** | 20751582 | 0.0141 |
| 10.0 | +15 | 0.0067 | 0.0053 | **-0.265** | 20901048 | 0.0052 |
| 10.0 | +30 | 0.0089 | 0.0053 | **-0.681** | 20814333 | 0.0052 |
| 10.0 | +60 | 0.0102 | 0.0053 | **-0.933** | 20751582 | 0.0055 |
| 20.0 | +15 | 0.0023 | 0.0016 | **-0.465** | 20901048 | 0.0015 |
| 20.0 | +30 | 0.0029 | 0.0016 | **-0.862** | 20814333 | 0.0015 |
| 20.0 | +60 | 0.0031 | 0.0016 | **-1.019** | 20751582 | 0.0016 |

### BSS per giornata (soglie del criterio, pooled su ancore e lead)

| giornata | 0,5 mm/h | 2 mm/h | 5 mm/h |
| :--- | ---: | ---: | ---: |
| 2026-11-01 | -3.994 | -4.497 | -5.106 |
| 2026-09-10 | -2.130 | -2.011 | -2.563 |
| 2026-10-08 | -1.413 | -1.149 | -1.031 |
| 2026-10-20 | -2.685 | -2.374 | -3.425 |
| 2026-11-09 | 0.663 | 0.787 | 0.945 |
| 2026-05-29 | 0.309 | 0.418 | 0.451 |
| 2026-08-29 | 0.863 | 0.940 | 0.953 |
| 2026-08-09 | 0.727 | 0.715 | 0.694 |
| 2026-09-30 | 0.854 | 0.913 | 0.957 |
| 2026-07-08 | 0.800 | 0.904 | 0.937 |
| 2026-08-13 | 0.633 | 0.593 | 0.543 |
| 2026-07-16 | 0.863 | 0.930 | 0.936 |

| soglia | giornate valutate | BSS>0 | frazione | criterio ≥60% |
| ---: | ---: | ---: | ---: | :--- |
| 0.5 | 12 | 8 | 66.7% | PASSA |
| 2.0 | 12 | 8 | 66.7% | PASSA |
| 5.0 | 12 | 8 | 66.7% | PASSA |

## Reliability (10 bin)

`n pixel` = previsioni nel bin; `casi` = casi/lead che contribuiscono al bin. Criterio: |media p − osservato| ≤ 10 punti percentuali nei bin con ≥ 30 casi.

### Soglia 0.5 mm/h

| bin | n pixel | casi | media p | osservato | delta | entro 10% |
| ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 0 | 41617437 | 69 | 0.002 | 0.003 | 0.001 | sì |
| 1 | 2531936 | 71 | 0.124 | 0.022 | 0.102 | **NO** |
| 2 | 2423416 | 71 | 0.225 | 0.029 | 0.196 | **NO** |
| 3 | 2545099 | 70 | 0.325 | 0.041 | 0.284 | **NO** |
| 4 | 2732004 | 70 | 0.424 | 0.061 | 0.363 | **NO** |
| 5 | 2423850 | 70 | 0.524 | 0.102 | 0.422 | **NO** |
| 6 | 2856245 | 70 | 0.646 | 0.196 | 0.450 | **NO** |
| 7 | 803859 | 69 | 0.750 | 0.289 | 0.461 | **NO** |
| 8 | 2137150 | 69 | 0.848 | 0.407 | 0.441 | **NO** |
| 9 | 2395967 | 65 | 0.985 | 0.577 | 0.408 | **NO** |

Delta massimo: **0.461**. Criterio: **NON PASSA**.

### Soglia 2.0 mm/h

| bin | n pixel | casi | media p | osservato | delta | entro 10% |
| ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 0 | 50533713 | 72 | 0.002 | 0.002 | 0.000 | sì |
| 1 | 2310256 | 72 | 0.122 | 0.016 | 0.106 | **NO** |
| 2 | 1808395 | 71 | 0.224 | 0.023 | 0.201 | **NO** |
| 3 | 1514559 | 70 | 0.324 | 0.034 | 0.290 | **NO** |
| 4 | 1244971 | 68 | 0.422 | 0.051 | 0.371 | **NO** |
| 5 | 968272 | 67 | 0.524 | 0.091 | 0.432 | **NO** |
| 6 | 1204586 | 64 | 0.648 | 0.162 | 0.486 | **NO** |
| 7 | 371032 | 62 | 0.750 | 0.232 | 0.518 | **NO** |
| 8 | 1105937 | 61 | 0.850 | 0.342 | 0.508 | **NO** |
| 9 | 1405242 | 57 | 0.985 | 0.644 | 0.341 | **NO** |

Delta massimo: **0.518**. Criterio: **NON PASSA**.

### Soglia 5.0 mm/h

| bin | n pixel | casi | media p | osservato | delta | entro 10% |
| ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| 0 | 57009202 | 72 | 0.001 | 0.001 | 0.000 | sì |
| 1 | 1284912 | 72 | 0.121 | 0.022 | 0.099 | sì |
| 2 | 801910 | 69 | 0.223 | 0.032 | 0.191 | **NO** |
| 3 | 605785 | 64 | 0.324 | 0.046 | 0.278 | **NO** |
| 4 | 478244 | 63 | 0.423 | 0.068 | 0.355 | **NO** |
| 5 | 398553 | 59 | 0.524 | 0.090 | 0.434 | **NO** |
| 6 | 522116 | 55 | 0.648 | 0.131 | 0.516 | **NO** |
| 7 | 161634 | 53 | 0.750 | 0.183 | 0.567 | **NO** |
| 8 | 505557 | 51 | 0.851 | 0.265 | 0.587 | **NO** |
| 9 | 699050 | 45 | 0.985 | 0.565 | 0.421 | **NO** |

Delta massimo: **0.587**. Criterio: **NON PASSA**.

## CRPS

CRPS fair sui 20 membri, sottocampione sistematico di 40000 pixel per caso/lead; climatologia empirica leave-one-day-out.

| lead | ensemble | climatologia |
| ---: | ---: | ---: |
| +15 | 0.3013 | 0.2460 |
| +30 | 0.3888 | 0.2469 |
| +60 | 0.4341 | 0.2617 |
| **pooled** | 0.3747 | 0.2515 |

Criterio (CRPS ensemble < climatologia): **NON PASSA**.

## Spread-skill

Rapporto **RMSE/spread = 4.18** (under_dispersed). Fattore di inflazione necessario: **4.18**. Spearman(spread, |errore|) = 0.90.

| lead | RMSE | spread medio | rapporto |
| ---: | ---: | ---: | ---: |
| +15 | 2.154 | 0.371 | 5.80 |
| +30 | 2.616 | 0.584 | 4.48 |
| +60 | 2.695 | 0.840 | 3.21 |

| spread basso | spread alto | n | spread medio | RMSE | rapporto |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.00 | 0.00 | 298649 | 0.000 | 0.157 | n.d. |
| 0.00 | 0.00 | 309509 | 0.000 | 0.210 | n.d. |
| 0.00 | 0.00 | 288000 | 0.001 | 0.225 | n.d. |
| 0.00 | 0.00 | 288000 | 0.002 | 0.213 | n.d. |
| 0.00 | 0.01 | 288000 | 0.005 | 0.551 | n.d. |
| 0.01 | 0.02 | 288000 | 0.011 | 0.217 | n.d. |
| 0.02 | 0.27 | 288000 | 0.137 | 0.495 | 3.61 |
| 0.27 | 0.65 | 288000 | 0.442 | 0.836 | 1.89 |
| 0.65 | 1.60 | 288000 | 1.032 | 1.754 | 1.70 |
| 1.60 | 97.56 | 288000 | 4.356 | 7.613 | 1.75 |

I bin con spread < 0,05 mm/h non hanno un rapporto leggibile (spread ~0, errore non nullo): sono i pixel asciutti, dove l'errore assoluto cresce solo se l'ensemble sbaglia a mettere pioggia. Il rapporto complessivo è dominato dai bin con spread reale (righe 0,65-1,60 e 1,60+): lì il rapporto è ~1,7-1,8, cioè l'ensemble è sotto-disperso di un fattore ~1,75 **dentro le aree con spread**, mentre il valore 4,18 pooled è gonfiato dai pixel con spread nullo.

### Frequenza prevista vs osservata

| soglia | media p (pooled) | osservata | rapporto | Brier |
| ---: | ---: | ---: | ---: | ---: |
| 0.5 | 0.173 | 0.059 | 2.92 | 0.0654 |
| 2.0 | 0.091 | 0.030 | 3.05 | 0.0333 |
| 5.0 | 0.041 | 0.013 | 3.17 | 0.0155 |

Un rapporto > 1 significa che l'ensemble prevede pioggia su una frazione di dominio maggiore di quella osservata (tipico del rumore additivo con clip a 0: la parte negativa viene tagliata e la media sale). È la grandezza da correggere con `noise_inflation < 1`.

## Criteri dichiarati prima

| criterio | esito |
| :--- | :--- |
| BSS > 0 su ≥60% delle giornate (soglia 0.5) | PASSA |
| BSS > 0 su ≥60% delle giornate (soglia 2.0) | PASSA |
| BSS > 0 su ≥60% delle giornate (soglia 5.0) | PASSA |
| Reliability ±10% (soglia 0.5) | **NON PASSA** |
| Reliability ±10% (soglia 2.0) | **NON PASSA** |
| Reliability ±10% (soglia 5.0) | **NON PASSA** |
| CRPS meglio della climatologia | **NON PASSA** |

Il criterio BSS per-giornata è l'unico che passa: **non è una prova di calibrazione** (vedi BSS pooled e regimi sotto).

**Verdetto: NON PASSA.**

Motivo: reliability fuori ±10% su tutte le soglie (delta massimo 0,46-0,59), CRPS peggiore della climatologia a ogni lead, e BSS pooled negativo a +60 (0,5/2/5 mm/h: -0.703 / -0.554 / -0.810). Il «PASSA» del criterio per-giornata non compensa: è un criterio non additivo, e le 4 giornate convettive lo dominano in negativo.

## Cosa manca e quale correzione

- reliability fuori ±10% per 0.5, 2.0, 5.0.
- CRPS non migliore della climatologia.

**Il criterio per-giornata passa ma il BSS pooled è negativo a tutti i lead** (0,5 mm/h: -0.136 / -0.447 / -0.703). Le due cose non si contraddicono: il BSS non è additivo, e le 4 giornate convettive hanno errori molto grandi che dominano il punteggio pooled. Il criterio «≥60% delle giornate» da solo NON è una prova di calibrazione: va letto insieme al pooled.

### BSS per regime (perché il pooled è negativo)

| regime | giornate | BSS 0,5 (media) | BSS 2 (media) | BSS 5 (media) |
| :--- | ---: | ---: | ---: | ---: |
| convective | 4 | -2.556 | -2.508 | -3.032 |
| stratiform | 4 | 0.641 | 0.715 | 0.761 |
| weak | 4 | 0.788 | 0.835 | 0.843 |

Le giornate convettive hanno BSS da −1,0 a −5,1 (il Brier è 2-6 volte quello della climatologia): è lì che il nowcast probabilistico fallisce. Le giornate stratiformi/deboli battono la climatologia. Un prodotto che si comporta così non può essere pubblicato come probabilità calibrata su tutti i regimi.

### I due difetti misurati

1. **Sotto-dispersione**: nei bin con spread reale (≥0,25 mm/h, cioè dove l'ensemble ha davvero dispersione) il rapporto RMSE/spread è **1.70-1.89**, mediana **1.75**: serve un `motion_inflation` di circa **1.75**. Il rapporto pooled varianza-based (RMSE/spread = 4.18) e i rapporti per lead (+15: 5.80, +30: 4.48, +60: 3.21) sono più alti perché includono i pixel asciutti con spread≈0 e RMSE non nullo: non vanno usati come fattore di inflazione.
   ~1,8× è una correzione che i ±10,88 m/s citati non coprono (prima del damping per instabilità, che sul campione riduce l'ampiezza di un altro 20-50%). Non è un ritocco del valore di letteratura: è un parametro DICHIARATO da fittare, e il fit va fatto su giornate separate.
2. **Sovra-previsione della frequenza**: probabilità media pooled 0.173 contro frequenza osservata 0.059 (rapporto **2.92**) a 0,5 mm/h; 3.05× a 2 mm/h e 3.17× a 5 mm/h. Il rumore additivo con clip a 0 gonfia la media (la parte negativa viene tagliata), e il supporto dell'eco è più largo del vero. La correzione è `noise_inflation < 1` (indicativamente **0,5-0,6**), ma va fittata su giornate non usate per sceglierla.

### Il pezzo che manca davvero

I due difetti non si correggono con un solo scalare: aumentare la dispersione abbassa le probabilità nel nucleo (bene per la reliability) ma allarga anche l'area bagnata (male per la sovra-previsione). Il pezzo mancante è **l'incertezza del campo di moto per-pixel** (e dell'analisi), non l'ampiezza di una traslazione costante: una traslazione rigida non può decorrelare il nucleo di un sistema convettivo, e infatti nel bin 0,95-1,00 la frequenza osservata resta 0,58 (0,5 mm/h) e 0,64 (2 mm/h). Servono:

- una perturbazione **spazialmente variabile** del moto (fattori casuali lisci per zona, non un offset unico), con ampiezza tarata su questa stessa metrica;
- una perturbazione dell'**analisi** (il campo SRI di partenza) e non solo del trasporto;
- il **fit su 6 giornate e la verifica su 6** di `motion_inflation` e `noise_inflation`: con un solo campione non si distingue la correzione dall'overfitting.

Nel frattempo `ensemble.enabled` **resta false**: pubblicarlo acceso significherebbe pubblicare probabilità che su 4 giornate convettive su 12 sono peggiori della climatologia.

## Pilota di correzione (`motion_inflation = 3`)

Stesse giornate del run principale che compaiono nel pilota (2026-10-08, 2026-11-01), solo 4 casi: **non è una validazione**, è la prova che la direzione della correzione è quella giusta e che da sola non basta.

| soglia | lead | Brier (infl. 1) | Brier (infl. 3) | media p | nucleo p | nucleo oss. |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | +15 | 0.1772 | 0.1399 | 0.365 | 0.981 | 0.765 |
| 0.5 | +30 | 0.2200 | 0.1829 | 0.438 | 0.977 | 0.750 |
| 0.5 | +60 | 0.2359 | 0.2242 | 0.471 | 0.975 | 0.806 |
| 2.0 | +15 | 0.0919 | 0.0785 | 0.199 | 0.984 | 0.763 |
| 2.0 | +30 | 0.1195 | 0.0960 | 0.237 | 0.980 | 0.749 |
| 2.0 | +60 | 0.1267 | 0.1064 | 0.254 | 0.977 | 0.775 |

Con `motion_inflation = 3` il Brier a +15 migliora di ~21% e lo spread-skill scende da ~3-4 a ~1,2-2,2, ma il nucleo resta a 0,98-0,99 contro 0,75-0,81 osservato e la probabilità media non cambia (0,365 contro 0,367): la sotto-dispersione si riduce, la sovra-previsione dell'area no. Servono due parametri distinti e una perturbazione non rigida, non un solo scalare.

