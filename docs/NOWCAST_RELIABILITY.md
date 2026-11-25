# Affidabilita' del nowcast per orizzonte

Generato da `scripts/validation/nowcast_reliability_backtest.py` il 2026-11-25T10:36:11Z.

stesso compute() della produzione (cascata/matching/decay/orografia spenti) contro SRI osservato; persistenza = SRI dell'ancora; CSI pooled, FSS a 15 km medio sui casi.

Campione: 12 giornate x 2 ancore, 24 casi con pioggia >= 0,5 mm/h su almeno 100 pixel.

| lead | casi | CSI nowcast | CSI persist. | delta CSI | FSS nowcast | FSS persist. | nowcast meglio? |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| +30 | 24 | 0.518 | 0.458 | 0.059 | 0.614 | 0.629 | si |
| +60 | 24 | 0.372 | 0.344 | 0.029 | 0.402 | 0.430 | si |
| +90 | 24 | 0.293 | 0.285 | 0.007 | 0.301 | 0.347 | si |
| +120 | 24 | 0.242 | 0.241 | 0.001 | 0.236 | 0.288 | si |
| +150 | 24 | 0.190 | 0.197 | -0.007 | 0.180 | 0.228 | **NO** |
| +180 | 24 | 0.161 | 0.174 | -0.013 | 0.144 | 0.193 | **NO** |

Da +150 min il nowcast e' **peggiore** della persistenza.

Il verdetto «nowcast meglio?» usa il **CSI pooled** medio alle soglie 0,5 e 2,0 mm/h (regola dichiarata prima del run). L'**FSS a 15 km** e' un secondo giudizio e in questo campione e' **sempre a favore della persistenza** (delta FSS negativo a ogni orizzonte): le due metriche non concordano a breve termine, quindi il risultato va letto con entrambe, non con quella che conviene.
