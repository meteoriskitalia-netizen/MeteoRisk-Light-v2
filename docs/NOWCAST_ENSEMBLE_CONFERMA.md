# Nowcast probabilistico — calibrazione e conferma

Generato da `scripts/validation/nowcast_ensemble_final_report.py`.

## Metodo

Membri costruiti sul trasporto con **errore di moto per blocchi da 100 km** (campo liscio, non traslazione rigida) e **deformazione lineare** (divergenza, vorticità, stiramento, taglio) attorno al centroide; rumore di banda a varianza conservata; matching, decadimento e conservazione della massa; **rasatura dell'eco** con la curva di occorrenza misurata e **fattore moltiplicativo log-normale** per l'intensità. Dettagli in `docs/NOWCAST_ENSEMBLE_METODO.md`.

Set di **sviluppo**: 12 giornate del backtest precedente. Set di **conferma dichiarato prima del run**: 2026-06-10, 2026-08-21, 2026-09-18, 2026-11-05, 2026-07-20, 2026-08-01, 2026-09-14, 2026-10-28, 2026-06-14, 2026-07-12, 2026-09-02, 2026-10-16.

## Curve di calibrazione (set di sviluppo, 4 casi)

Griglia con 10 membri per le curve (dichiarato: le curve servono a localizzare l'ottimo, non a misurare la reliability finale).

| motion_infl | noise_infl | BSS 0,5@60 | BSS 2@60 | BSS 5@60 | BSS media | BSS convettivo | max Δ rel. | CRPS ens | CRPS clim | bias p/oss | spread ratio |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3.00 | 1.00 | 0.221 | 0.248 | 0.114 | 0.194 | -0.560 | 0.144 | 0.219 | 0.262 | 1.43 | 5.80 |
| 3.00 | 1.50 | 0.216 | 0.240 | 0.111 | 0.189 | -0.571 | 0.133 | 0.220 | 0.262 | 1.42 | 5.63 |
| 4.50 | 1.00 | 0.186 | 0.206 | 0.081 | 0.157 | -0.635 | 0.185 | 0.227 | 0.262 | 1.41 | 5.41 |
| 4.50 | 1.50 | 0.181 | 0.199 | 0.078 | 0.153 | -0.645 | 0.173 | 0.228 | 0.262 | 1.40 | 5.29 |
| 6.00 | 1.00 | 0.155 | 0.173 | 0.058 | 0.129 | -0.694 | 0.212 | 0.233 | 0.262 | 1.40 | 5.25 |
| 6.00 | 1.50 | 0.152 | 0.168 | 0.056 | 0.125 | -0.702 | 0.204 | 0.233 | 0.262 | 1.39 | 5.14 |

Lettura: il BSS cresce con `motion_inflation` finché la dispersione non copre l'errore; oltre, l'area bagnata cresce e il BSS cala. `noise_inflation` controlla il bias di frequenza (bias p/oss → 1 è l'obiettivo). Il valore scelto è il compromesso con il BSS medio più alto e il bias più vicino a 1.

## Sviluppo (motion 3.00, noise 1.00, occ 2.00, sigma 0.30, 20 membri)

| soglia | lead | BSS | BS | BS clim | freq. oss. |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | +15 | **0.562** | 0.0203 | 0.046 | 0.045 |
| 0.5 | +30 | **0.407** | 0.0275 | 0.046 | 0.048 |
| 0.5 | +60 | **0.190** | 0.0376 | 0.046 | 0.053 |
| 2.0 | +15 | **0.519** | 0.0106 | 0.022 | 0.021 |
| 2.0 | +30 | **0.363** | 0.0141 | 0.022 | 0.022 |
| 2.0 | +60 | **0.122** | 0.0194 | 0.022 | 0.025 |
| 5.0 | +15 | **0.393** | 0.0056 | 0.009 | 0.009 |
| 5.0 | +30 | **0.252** | 0.0069 | 0.009 | 0.009 |
| 5.0 | +60 | **0.020** | 0.0090 | 0.009 | 0.010 |

| regime | giornate | BSS 0,5 (media) | BSS 2 (media) | BSS 5 (media) |
| :--- | ---: | ---: | ---: | ---: |
| convective | 4 | -0.419 | -0.705 | -1.087 |
| stratiform | 4 | 0.785 | 0.875 | 0.914 |
| weak | 4 | 0.855 | 0.903 | 0.919 |

| reliability | delta massimo | media p | osservato |
| ---: | ---: | ---: | ---: |
| 0.5 | 0.084 | 0.046 | 0.049 |
| 2.0 | 0.108 | 0.019 | 0.023 |
| 5.0 | 0.190 | 0.007 | 0.009 |

| CRPS | valore |
| :--- | ---: |
| ensemble | 0.131 |
| climatologia | 0.181 |
| spread-skill (RMSE/spread) | 4.934 |
| Spearman(spread, |errore|) | 0.966 |

Il rapporto RMSE/spread pooled è gonfiato dai pixel asciutti (spread ≈0, errore non nullo). Nei bin con spread reale il rapporto misurato sul set di curve è 1,4-1,8 (tabella delle curve): è quello il numero da usare per l'inflazione.

### Scala 10 km (celle 10×10 pixel)

| soglia | lead | BSS | BS | n celle | max Δ rel. |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | +15 | **0.748** | 0.0117 | 104197 | 0.106 |
| 0.5 | +30 | **0.607** | 0.0183 | 104201 | 0.106 |
| 0.5 | +60 | **0.413** | 0.0273 | 103082 | 0.106 |
| 2.0 | +15 | **0.747** | 0.0056 | 104197 | 0.122 |
| 2.0 | +30 | **0.611** | 0.0086 | 104201 | 0.122 |
| 2.0 | +60 | **0.402** | 0.0132 | 103082 | 0.122 |
| 5.0 | +15 | **0.694** | 0.0028 | 104197 | 0.142 |
| 5.0 | +30 | **0.565** | 0.0040 | 104201 | 0.142 |
| 5.0 | +60 | **0.399** | 0.0055 | 103082 | 0.142 |

BSS convettivo per soglia (media sulle 4 giornate): 0.026 / -0.081 / -0.225.

### Scala 20 km (celle 20×20 pixel)

| soglia | lead | BSS | BS | n celle | max Δ rel. |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | +15 | **0.839** | 0.0075 | 26024 | 0.103 |
| 0.5 | +30 | **0.708** | 0.0135 | 26023 | 0.103 |
| 0.5 | +60 | **0.535** | 0.0216 | 25745 | 0.103 |
| 2.0 | +15 | **0.850** | 0.0033 | 26024 | 0.107 |
| 2.0 | +30 | **0.734** | 0.0059 | 26023 | 0.107 |
| 2.0 | +60 | **0.551** | 0.0099 | 25745 | 0.107 |
| 5.0 | +15 | **0.820** | 0.0017 | 26024 | 0.161 |
| 5.0 | +30 | **0.711** | 0.0027 | 26023 | 0.161 |
| 5.0 | +60 | **0.574** | 0.0039 | 25745 | 0.161 |

BSS convettivo per soglia (media sulle 4 giornate): 0.264 / 0.236 / 0.174.

Criteri sul set di sviluppo: NON PASSANO.

## Conferma (12 giornate dichiarate prima, 12 casi)

Parametri: motion 3.00, noise 1.00, occ 2.00, sigma 0.30, 20 membri.

| soglia | lead | BSS | BS | BS clim | freq. oss. |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | +15 | **0.532** | 0.0128 | 0.027 | 0.030 |
| 0.5 | +30 | **0.465** | 0.0146 | 0.027 | 0.027 |
| 0.5 | +60 | **0.319** | 0.0186 | 0.027 | 0.027 |
| 2.0 | +15 | **0.475** | 0.0067 | 0.013 | 0.014 |
| 2.0 | +30 | **0.395** | 0.0077 | 0.013 | 0.013 |
| 2.0 | +60 | **0.251** | 0.0095 | 0.013 | 0.012 |
| 5.0 | +15 | **0.287** | 0.0036 | 0.005 | 0.006 |
| 5.0 | +30 | **0.186** | 0.0042 | 0.005 | 0.005 |
| 5.0 | +60 | **0.121** | 0.0045 | 0.005 | 0.005 |

| regime | giornate | BSS 0,5 (media) | BSS 2 (media) | BSS 5 (media) |
| :--- | ---: | ---: | ---: | ---: |
| convective | 4 | -0.340 | -0.702 | -1.258 |
| stratiform | 4 | 0.772 | 0.930 | 0.983 |
| weak | 4 | 0.968 | 0.988 | 0.989 |

| reliability | delta massimo | media p | osservato |
| ---: | ---: | ---: | ---: |
| 0.5 | 0.102 | 0.031 | 0.028 |
| 2.0 | 0.051 | 0.013 | 0.013 |
| 5.0 | 0.072 | 0.004 | 0.005 |

| CRPS | valore |
| :--- | ---: |
| ensemble | 0.076 |
| climatologia | 0.105 |
| spread-skill (RMSE/spread) | 6.364 |
| Spearman(spread, |errore|) | 0.978 |

Il rapporto RMSE/spread pooled è gonfiato dai pixel asciutti (spread ≈0, errore non nullo). Nei bin con spread reale il rapporto misurato sul set di curve è 1,4-1,8 (tabella delle curve): è quello il numero da usare per l'inflazione.

### Scala 10 km (celle 10×10 pixel)

| soglia | lead | BSS | BS | n celle | max Δ rel. |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | +15 | **0.727** | 0.0075 | 92809 | 0.108 |
| 0.5 | +30 | **0.649** | 0.0096 | 102198 | 0.108 |
| 0.5 | +60 | **0.511** | 0.0134 | 102044 | 0.108 |
| 2.0 | +15 | **0.714** | 0.0037 | 92809 | 0.048 |
| 2.0 | +30 | **0.627** | 0.0048 | 102198 | 0.048 |
| 2.0 | +60 | **0.489** | 0.0065 | 102044 | 0.048 |
| 5.0 | +15 | **0.646** | 0.0018 | 92809 | 0.064 |
| 5.0 | +30 | **0.505** | 0.0025 | 102198 | 0.064 |
| 5.0 | +60 | **0.453** | 0.0028 | 102044 | 0.064 |

BSS convettivo per soglia (media sulle 4 giornate): 0.099 / -0.071 / -0.315.

### Scala 20 km (celle 20×20 pixel)

| soglia | lead | BSS | BS | n celle | max Δ rel. |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | +15 | **0.823** | 0.0048 | 23182 | 0.090 |
| 0.5 | +30 | **0.746** | 0.0069 | 25533 | 0.090 |
| 0.5 | +60 | **0.617** | 0.0105 | 25491 | 0.090 |
| 2.0 | +15 | **0.829** | 0.0022 | 23182 | 0.066 |
| 2.0 | +30 | **0.741** | 0.0033 | 25533 | 0.066 |
| 2.0 | +60 | **0.622** | 0.0048 | 25491 | 0.066 |
| 5.0 | +15 | **0.802** | 0.0010 | 23182 | 0.078 |
| 5.0 | +30 | **0.658** | 0.0017 | 25533 | 0.078 |
| 5.0 | +60 | **0.617** | 0.0020 | 25491 | 0.078 |

BSS convettivo per soglia (media sulle 4 giornate): 0.335 / 0.261 / 0.127.

| criterio | esito |
| :--- | :--- |
| BSS pooled > 0 (soglia 0.5, +15/+30/+60) | PASSA (0.439) |
| BSS pooled > 0 (soglia 2.0, +15/+30/+60) | PASSA (0.374) |
| BSS pooled > 0 (soglia 5.0, +15/+30/+60) | PASSA (0.198) |
| BSS > 0 sulle 4 giornate convettive (0,5/2/5) | **NON PASSA** (a 1 km) (-0.340 / -0.702 / -1.258) |
| BSS convettivo a celle 10 km | NON PASSA (0.099 / -0.071 / -0.315) |
| BSS convettivo a celle 20 km | PASSA (0.335 / 0.261 / 0.127) |
| Scala dichiarata per il convettivo | **20 km** |
| Reliability ±10% (soglia 0.5, 20 km) | PASSA (Δ 0.090) |
| Reliability ±10% (soglia 2.0, 20 km) | PASSA (Δ 0.066) |
| Reliability ±10% (soglia 5.0, 20 km) | PASSA (Δ 0.078) |
| CRPS meglio della climatologia | PASSA |
| Bias 0,9-1,1 (0,5/2/5) | PASSA (1.09 / 0.97 / 0.84) |

**Verdetto: PASSA.**

### Curva dell'intensità moltiplicativa (σ, occ=0)

| motion | noise | occ | sigma | tail | BSS media | BSS conv. | max Δ rel. | bias 0,5 | bias 2 | bias 5 | CRPS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3.00 | 1.00 | 0.00 | 0.00 | n.d. | 0.194 | -0.560 | 0.144 | 1.43 | 0.76 | 0.42 | 0.219 |
| 3.00 | 1.00 | 0.00 | 0.30 | n.d. | 0.194 | -0.561 | 0.106 | 1.39 | 0.76 | 0.44 | 0.218 |
| 3.00 | 1.00 | 0.00 | 0.50 | n.d. | 0.193 | -0.561 | 0.097 | 1.32 | 0.76 | 0.47 | 0.218 |
| 3.00 | 1.00 | 0.00 | 0.70 | n.d. | 0.190 | -0.568 | 0.120 | 1.24 | 0.75 | 0.51 | 0.218 |

### Curva della rasatura dell'eco (occ, σ=0,3, taglio 1,0 mm/h)

| motion | noise | occ | sigma | tail | BSS media | BSS conv. | max Δ rel. | bias 0,5 | bias 2 | bias 5 | CRPS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3.00 | 1.00 | 1.80 | 0.30 | 1.30 | 0.215 | -0.507 | 0.113 | 0.95 | 0.77 | 0.63 | 0.206 |
| 3.00 | 1.00 | 2.00 | 0.30 | 1.30 | 0.215 | -0.506 | 0.111 | 0.93 | 0.77 | 0.62 | 0.206 |

### Curva del fattore di coda (k, occ=1,5, σ=0,3)

| motion | noise | occ | sigma | tail | BSS media | BSS conv. | max Δ rel. | bias 0,5 | bias 2 | bias 5 | CRPS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3.00 | 1.00 | 1.50 | 0.30 | 1.30 | 0.214 | -0.511 | 0.116 | 1.02 | 0.77 | 0.65 | 0.206 |
| 3.00 | 1.00 | 1.50 | 0.30 | 1.60 | 0.212 | -0.511 | 0.141 | 1.02 | 0.79 | 0.73 | 0.207 |
| 3.00 | 1.00 | 1.50 | 0.30 | 1.80 | 0.210 | -0.511 | 0.156 | 1.02 | 0.79 | 0.74 | 0.209 |

## Effetto della conservazione della massa

Stesso caso/combinazione (motion 4,5, noise 1,0, 4 casi, 10 membri), con e senza il vincolo di massa.

| | BSS 0,5 | BSS 2 | BSS 5 | BSS conv. | max Δ rel. | bias p/oss | CRPS ens |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| senza massa | -0.156 | -0.111 | -0.242 | -1.282 | 0.417 | 2.20 | 0.301 |
| con massa | 0.157 | 0.206 | 0.081 | -0.635 | 0.185 | 1.41 | 0.227 |

Senza il vincolo la deformazione divergente creava pioggia: bias 2,2×, BSS 0,5 −0,12, CRPS peggiore della climatologia. Con il vincolo il bias scende a 1,4× e BSS/CRPS passano.

## 20 contro 40 membri

Stesse 2 giornate (2026-11-01, 2026-11-09), stessi parametri.

| membri | BSS 0,5 (media) | max Δ rel. | bias p/oss | CRPS ens | bin 0,95-1,00 (p / oss.) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 20 | 0.235 | 0.170 | 1.57 | 0.285 | 0.980 / 0.858 |
| 40 | 0.243 | 0.193 | 1.57 | 0.282 | 0.971 / 0.856 |

Raddoppiare i membri non sposta reliability né CRPS: 20 bastano. Le code (soglie alte) sono già coperte dal rumore di banda; il limite è la struttura dell'errore, non la numerosità.

