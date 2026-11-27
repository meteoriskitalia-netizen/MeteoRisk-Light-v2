# Motore di ricerca del nowcast (2026-11-27)

## Cosa fa

`scripts/validation/nowcast_sweep.py` esplora combinazioni di parametri,
misura copertura/IoU/bias/correlazione a **+90** (e +15 come test di
sanita'), classifica con la funzione obiettivo e tiene un registro di ogni
run. Supporta shard multipli e merge.

### Spazio SUPPORTATO (il motore attuale lo applica davvero)

| parametro | valori |
| :--- | :--- |
| `motion_speed_factor` | 0.7 0.8 0.9 1.0 1.1 1.2 |
| `transport_coarse_factor` | 1 2 3 5 |
| `decay` | off / rear_erosion 5 / rear_erosion 10 |
| `hf_restore` | off / 0.5 / 0.7 |

### Assi dichiarati NON supportati

`motion_inflation`, `dispersion_growth_per_lead`, `evolution`
(cascade/geostatistico), `ensemble_members`: non sono ancora cablati nel
motore di produzione. Il registro li marca `supported: false` e NON li
simula. Non vengono fatti passare per risultati.

## Uso

```
python scripts/validation/nowcast_sweep.py --precache
python scripts/validation/nowcast_sweep.py --shard 0 --shards 4
python scripts/validation/nowcast_sweep.py --shard 1 --shards 4
python scripts/validation/nowcast_sweep.py --shard 2 --shards 4
python scripts/validation/nowcast_sweep.py --shard 3 --shards 4
python scripts/validation/nowcast_sweep.py --merge
```

- Screening: **6 giornate dichiarate PRIMA** (`SCREEN_ANCHORS`):
  2026-11-01 16Z, 2026-10-20 16Z, 2026-09-10 16Z, 2026-11-09 16Z,
  2026-05-29 12Z, 2026-07-27 12Z.
- Verifica finale: **12 giornate** (`FINAL_ANCHORS`, le stesse del report
  affidabilita +90).
- 48 combinazioni (LHS deterministica, seed 20261127) da 216 possibili,
  baseline inclusa.
- Ogni combinazione usa il nowcast di produzione con `nowcast_leads_min =
  [15, 90]`; le nuvole sono spente (non entrano nelle metriche di pioggia).
- Test di sanita': se `coverage@15` peggiora di >0,02 rispetto al baseline,
  la riga e' marcata `suspicious_widening` e non puo' vincere.

## Obiettivo

`coverage_0p5 @ +90`, con vincolo `bias_0p5 @ +90 <= 1,3`; se il bias
supera 1,3 la funzione obiettivo sottrae `1,5 * (bias - 1,3)`. Il bias e'
sempre riportato accanto alla copertura.

## Registro

`docs/evidence/nowcast_sweep_20261127/sweep_all.json` contiene ogni
combinazione con i numeri per ancora, le medie a +15/+90, il flag di
sanita' e l'obiettivo.

## Primo screening eseguito (48 run, 6 giornate)

Migliore screening (6 giornate):

| speed | coarse | decay | hf | cop @90 | bias @90 | IoU @90 | cop @15 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.7 | 5 | 5 | 0 | **0,4625** | 0,9227 | 0,3239 | 0,7533 |
| 0.8 | 5 | 5 | 0 | 0,4595 | 0,9211 | 0,3220 | 0,7420 |
| 0.9 | 5 | 5 | 0 | 0,4546 | 0,9220 | 0,3182 | 0,7267 |

48/48 righe nel registro; nessuna marcata `suspicious_widening`.

## Verifica finale sulle 12 giornate (top 8)

| speed | coarse | decay | hf | cop @90 | bias @90 | IoU @90 | cop @15 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **0.7** | **5** | **5** | **0** | **0,4044** | **1,0712** | **0,2633** | **0,7059** |
| 0.8 | 5 | 5 | 0 | 0,3957 | 1,0978 | 0,2581 | 0,6969 |
| 0.9 | 5 | 5 | 0 | 0,3841 | 1,1051 | 0,2508 | 0,6854 |
| 0.8 | 3 | 0 | 0 | 0,3787 | 1,0674 | 0,2521 | 0,6880 |
| 0.8 | 2 | 0 | 0 | 0,3710 | 1,0574 | 0,2491 | 0,6836 |
| 0.9 | 3 | 10/0/5 | 0 | 0,3658 | 1,0754 | 0,2439 | 0,6770 |

I vicini del migliore (speed 0.8/0.9, coarse 3, hf 0.5) sono tutti
**peggiori**: il punto (0.7, 5, decay 5, hf 0) e' un ottimo locale della
griglia provata. La fase di hill climbing non ha trovato un vicino
migliore; non e' stata inventata una perturbazione fuori griglia.

**Dove siamo rispetto al 90%:** copertura **40,4%**, IoU **26,3%**, bias
**1,07**. Mancano **49,6 punti percentuali** di copertura. Il bias e' sotto
1,3, quindi non e' un problema di area: il limite e' la **posizione**
(centroide mediano 76 km a +90 sulle 12 giornate). Il migliore guadagno
rispetto alla produzione (35,3% -> 40,4%, +5,1 punti) viene da speed 0.7 +
coarse 5 + HF off, ma resta lontanissimo dall'obiettivo.

Nota: due ancora della verifica finale hanno subito un errore di
accesso concorrente al file temporaneo DPC (`WinError 5/32`); le medie
riportate escludono quei valori nulli (n=12 ancore, valori disponibili
per 10-12). Il registro contiene l'errore per ancora, non lo nasconde.
