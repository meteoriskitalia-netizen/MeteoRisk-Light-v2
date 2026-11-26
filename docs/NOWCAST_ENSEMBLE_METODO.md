# Nowcast probabilistico — metodo

Documento di metodo dell'ensemble a 20 membri (`scripts/radar_engine/nowcast_ensemble.py`).
Il verdetto e le tabelle di calibrazione sono in `docs/NOWCAST_ENSEMBLE_ESITO.md`.

## 1. Perché il deterministico non basta

Il nowcast operativo pubblica un solo campo a 1 km per +15/+30/+60 min. Il
confronto con la persistenza ha mostrato che il vantaggio del trasporto cala
con l'orizzonte (vedi `docs/NOWCAST_RELIABILITY.md`): le scale fini (2-8 km)
hanno una correlazione a un passo (5 min) misurata di ~0,37, e un campo che
conserva intatta la grana a un'ora di distanza produce dettagli che non
esistono più. Il punto non è "quanto è intenso" ma **quanto fidarsi**: la
posizione delle strutture fini è incerta anche quando il trasporto è giusto.

Un ensemble calibrato risponde con una probabilità di superamento, che si può
verificare (Brier, reliability, CRPS). Un campo singolo no.

## 2. Costruzione dei membri

Per ogni lead `L` (15/30/60 min) e per ogni membro `m = 1..20`:

```
membro_m(L) = post( g_b * FFT(trasporto_m(L)) + sqrt(1-g_b^2) * |FFT(trasporto_m(L))| * exp(i*theta_m) )
```

dove `trasporto_m` è il campo SRI trasportato di `L` con il moto di produzione
perturbato, `g_b` sono i guadagni AR(2) per banda della cascata, `theta_m` è una
fase casuale e `post` è **la stessa** correzione del run deterministico
(probability matching + decadimento).

### 2.1 Perturbazione del moto (campo variabile, non traslazione rigida)

- **ampiezza di riferimento**: ±10,88 m/s paralleli al moto, ±5,76 m/s
  perpendicolari (STEPS/BPS: Bowler, Pierce & Seed 2006; Germann et al. 2006),
  moltiplicati per `motion_inflation` (calibrato, §4).
- **campo di errore per blocchi**: un vettore casuale per blocco di
  `motion_block_km = 100 km`, nel sistema solidale al moto, interpolato
  bilinearmente sulla griglia. Blocchi adiacenti hanno errori diversi: il
  campo si **deforma** mentre si sposta (una costante rigida non può
  decorrelare il nucleo).
- **deformazione lineare**: attorno al centroide della pioggia si aggiunge un
  tensore `du = aX + bY`, `dv = cX + eY` con divergenza, vorticità,
  stiramento e taglio casuali di ampiezza `0,6 ×` la perturbazione parallela
  (unità: 100 km). È il termine che allunga/contrae/ruota il nucleo.
- **centratura**: l'errore è a media nulla sui pixel di pioggia osservata,
  così non introduce una deriva globale oltre a quella voluta; il vettore
  totale è limitato a 60 m/s (dichiarato).
- **integrazione**: `integration = "single"` (default) applica lo spostamento
  totale composto dopo il trasporto deterministico (1 warp per lead);
  `"steps"` re-integra il semi-lagrangiano per membro. Differenza misurata
  (2026-11-01 11:00, 10 membri, 3,0/1,0): probabilità media |Δ| 0,030 / 0,048 /
  0,061 a +15/+30/+60, 4,4-13,5% dei pixel oltre 0,2; Brier del composto
  leggermente migliore (0,098 vs 0,101 a +15). Costo: 56,9 s vs 89,4 s
  (1,6×, host condiviso). L'approssimazione è dichiarata, non un'equivalenza
  (`audit/ensemble_integration_ab.json`).
- **stabilità del moto**: `factor = clip(coherence/0,5)·clip(v_mediana/5 km/h)`
  riduce l'ampiezza (e la deformazione) quando il moto è incoerente o quasi
  nullo: con moto nullo la perturbazione è zero, nessuna direzione inventata.

Il disegno a 20 membri è **casuale ma deterministico** (seed da slot e indice
membro): due run con lo stesso istante danno gli stessi membri. La modalità
`rigid` (5×4 livelli, traslazione costante) resta disponibile per l'A/B
storico.

### 2.2 Rumore per banda (non parametrico, varianza conservata)

La cascata (`nowcast_cascade.py`) decompone il campo in 6 bande radiali
dyadiche e stima un AR(2) per banda; `g_b` è il guadagno di ampiezza al lead
`L`. Il rumore si costruisce **nello stesso spazio di frequenze**:

```
Y_out[k] = g_b(k) * Y[k] + sqrt(1 - g_b(k)^2) * |Y[k]| * exp(i*theta[k])
```

- `Y[k]` è il trasporto **prima** di matching e decadimento: è l'unica base su
  cui `g_b` ha un significato fisico (il matching riporta la distribuzione
  all'analisi e falserebbe la conservazione).
- **varianza conservata**: `E|Y_out|^2 = g^2|Y|^2 + (1-g^2)|Y|^2 = |Y|^2` per
  ogni frequenza; quindi la potenza di ogni banda resta quella del trasporto,
  la parte predicibile decade e la parte impredicibile diventa rumore con lo
  spettro della banda (metodo `nonparametric` di STEPS, non uno spettro
  imposto a mano).
- **spettro reale**: si usa `rfft2`/`irfft2`; la simmetria hermitiana è imposta
  dalla trasformata, quindi il membro è reale per costruzione. (Un primo
  tentativo con fase "hermitiana" costruita a mano dimezzava la potenza del
  rumore: metà dello spettro era immaginaria e veniva scartata dal `real()`.
  Difetto misurato e corretto; il test `test_band_noise_conserves_variance` lo
  copre.)
- **supporto**: il rumore è limitato alla maschera dell'eco del membro. Senza
  limite la fase casuale distribuisce l'energia di una banda su tutto il
  dominio e compare pioggia debole dove il trasporto è asciutto (misurato:
  probabilità media 0,45 su un dominio con il 15% di pioggia osservata).
  Dentro il supporto la varianza è conservata; fuori il rumore è zero. È una
  scelta dichiarata, non una conservazione globale.
- **senza cascata** non si aggiunge rumore: il blocco `ensemble.noise.applied`
  è `false` con `null_reason: cascade_gains_unavailable`. Meglio un ensemble
  solo-moto che rumore senza decadimento. **MISURATO nella produzione: la
  cascata è `enabled: False` nel config, quindi il rumore di banda NON si
  applica** (verificato nel run end-to-end 2026-11-01 11:00: `noise.applied =
  false`, `cascade_gains_unavailable`). La dispersione misurata viene da moto
  variabile + deformazione + rasatura dell'eco + fattore d'intensità + coda.

### 2.3 Coerenza con il campo pubblicato

Ogni membro riceve la stessa correzione del run deterministico:

1. **probability matching** (`nowcast_matching.match`) con lo stesso
   riferimento, lo stesso peso di rampa e lo stesso gate di regime;
2. **decadimento** con lo stesso fattore per lead;
3. **conservazione della massa** (`mass_conservation`, default ON): la
   deformazione divergente sposterebbe l'acqua senza conservarla, gonfiando
   l'area bagnata (misurato: probabilità media 2,2× l'osservata, BSS pooled
   −0,12). Dopo matching e decadimento ogni membro viene riscalato in modo che
   l'integrale di pioggia sul dominio sia quello del campo deterministico.
   Lo scalare non cambia le posizioni e riporta il bias di frequenza verso 1
   (misurato: 1,41×). È un vincolo fisico, non un parametro di taratura.

Il membro quindi non è un universo parallelo: è una perturbazione del
trasporto che viene calibrata come il campo operativo. Il matching avviene
**dopo** il rumore, quindi non annulla la conservazione della varianza.

## 2.4 Occorrenza e intensità

Due perturbazioni aggiuntive, entrambe con campi **spazialmente correlati**
(blocchi lisci, non rumore bianco) e **coerenti fra i lead** dello stesso
membro:

- **Rasatura dell'eco** (`occurrence_scale`, `occurrence_curve`): la curva
  `P(pioggia ≥ 0,5 mm/h | intensità)` è **misurata** sul set di sviluppo
  (`audit/occurrence_curve.json`): a I=0,14 → 0,08; a I=0,61 → 0,49; a I=2,4 →
  0,83; a I=6,3 → 0,93. Un membro diventa asciutto con probabilità
  `occurrence_scale · (1 − P(I))`, usando un campo uniforme correlato su 10 km.
  Con `occurrence_max_intensity = 1,0` la rasatura si applica solo all'eco
  debole (tranello logistico): la probabilità a 2/5 mm/h non deve calare.
- **Fattore moltiplicativo** (`intensity_sigma`): `I' = I · exp(σZ − σ²/2)` con
  `Z` normale standardizzato e liscio su 20 km; media 1, quindi non sposta la
  massa (che poi è imposta esatta). `σ = 0,3` è il valore scelto: alza la coda
  senza degradare il corpo; σ più alti peggiorano il BSS a 2/5.
- **Fattore di coda** (`tail_factor`): sopra il `p90` dell'intensità il campo
  è moltiplicato per `k`, con `k` disperso fra i membri (15%), applicato
  DOPO la rasatura. Serve perché il campo deterministico perde i picchi:
  il moltiplicatore globale (σ) non basta e a σ alto degrada il corpo.
  Curva `k → bias a 5` e `k → BSS a 5` in
  `audit/ensemble_grid_tail.json`.

Entrambi sono applicati **dopo** matching/decadimento; la massa è imposta
prima della rasatura, quindi la rasatura riduce la massa (e il bias) come deve.

## 3. Output

Per ogni lead, sulla griglia nativa (1400×1200, 1 km):

| campo | significato |
| :--- | :--- |
| `prob_0p5`, `prob_2`, `prob_5`, `prob_10`, `prob_20` | frazione di membri ≥ soglia, 0-1, quantizzata a 0,01 |
| `p10`, `p50`, `p90` | quantili del campo di pioggia fra i 20 membri |
| `spread` | deviazione standard fra membri (mm/h) |

Immagini WebP: `nowcast_prob015.webp`, `nowcast_prob030.webp`,
`nowcast_prob060.webp` — **colore = probabilità** di superare 0,5 mm/h, non
intensità. Legenda 0-100% con nodi a 0/10/30/50/70/85/100%; alpha 0 sotto l'1%.
Il blocco `ensemble` di `nowcast.json` porta legenda, membri, perturbazione,
instabilità del moto, stato del rumore, statistiche per lead e costo misurato.

Le mappe di probabilità non sostituiscono i WebP di intensità: sono lo stesso
prodotto visto come incertezza.

**Stato del client.** In questa linea il client non ha ancora un layer per il
nowcast SRI (le 10 occorrenze di «nowcast» nell'HTML riguardano RainViewer e
l'allerta di prossimità): le immagini di probabilità sono pubblicate in
`data/radar/` e servite da Pages, con la legenda nel JSON, ma **non c'è ancora
un pannello che le mostri**. Il rendering lato client è un passo separato e
non è stato fatto qui.

## 4. Parametri di correzione (dichiarati, non nascosti)

- `motion_inflation` (**3,0**, calibrato): moltiplica gli offset di blocco e la
  deformazione. La curva BSS sul set di sviluppo ha l'ottimo a 3,0; a 4,5 e 6,0
  il BSS cala (4,5 → −0,64, 6,0 → −0,69 sul set di curve).
- `noise_inflation` (**1,0**, calibrato): 0,5 non migliora il bias (2,29 → 2,25)
  e 1,5 non sposta il BSS; 1,0 è il valore con il bias più vicino a 1.
- `mass_conservation` (**ON**): corregge la creazione di pioggia da deformazione
  divergente; senza, bias 2,2× e BSS 0,5 −0,12, con, bias 1,4× e BSS +0,19
  (4 casi, stessi parametri).
- `members` = **20**: 40 membri sulle stesse 2 giornate non migliorano la
  reliability (0,216 → 0,228) né il CRPS (0,285 → 0,282); 10 membri sulla
  conferma peggiorano la reliability a 0,5 da 0,109 a 0,140 (probabilità a
  passi di 0,1). 20 è il minimo, 40 non serve.
- `occurrence_scale` (**1,8**, calibrato): con il taglio a 1,0 mm/h porta il
  bias 0,5 a 0,93-0,95 sul set di curve e a 1,12 sulla conferma; occ=2,0 dà
  0,93 sul set di curve. È il parametro da portare a 2,0-2,1.
- `intensity_sigma` (**0,3**, calibrato): σ=0,5/0,7 abbassano il BSS a 5 mm/h
  (0,096/0,086 contro 0,104) e a 2 mm/h; 0,3 è il compromesso.
- `tail_factor` (**1,8**, calibrato): sopra il p90 alza il bias a 5 da 0,44
  (k=1) a 0,65 (1,3), 0,73 (1,6), **0,74** (1,8); il BSS a 5 resta positivo
  (0,107). Non arriva a 0,8: il limite è la distribuzione del campo
  deterministico, non il moltiplicatore.
- `occurrence_max_intensity` (**1,0** mm/h): senza taglio la rasatura tocca
  anche i pixel intensi e il bias a 2/5 crolla (0,66/0,41).

**Scala dichiarata**: la verifica a celle di 10 e 20 km (non intorni FSS)
mostra che il convettivo passa a 10-20 km (+0,17/+0,39 sul set di curve) e
fallisce a 1 km (−0,51). Il prodotto va letto alla scala di cella 10-20 km;
a 1 km la struttura fine non è prevedibile.

I valori sono fittati sul set di sviluppo; la conferma su 12 giornate nuove
(elenco in `docs/NOWCAST_ENSEMBLE_CONFERMA.md`) è separata. I valori di
letteratura restano quelli di riferimento: l'inflation è un parametro
dichiarato, non un ritocco silenzioso.

## 5. Costo

Misurato sul caso reale 2026-11-01 11:00 (24 frame SRI, griglia 1400×1200),
3 lead × 20 membri, moto variabile + massa:

| voce | secondi |
| :--- | ---: |
| trasporto (60 warp, spostamento composto) | ~6-10 |
| rumore per banda (60 FFT reali) | ~6 |
| matching + decadimento per membro | ~9 |
| statistiche (probabilità, quantili, spread) | ~3 |
| **ensemble, totale** | **~25-30** |

Il run deterministico sullo stesso caso costa ~10-12 s (dopo il download):
l'ensemble lo porta a ~35-42 s. Nei run di calibrazione e conferma (host
condiviso, carico variabile) il tempo per caso-combinazione è stato 25-50 s.
L'integrazione a passi (`integration="steps"`) costa ~4×; il rumore e il
matching dominano il resto. Il dettaglio per lead e per voce è in
`nowcast.json` (`ensemble.leads.*.cost_seconds`).

Se il costo esplodesse, la riduzione prevista è 20 membri sui lead 15/30/60 e
5 sugli altri: qui i lead sono già solo tre, quindi non si applica.

## 6. Validazione

Due fasi separate, con criteri dichiarati prima:

- **sviluppo**: `nowcast_ensemble_calibrate.py --phase grid` sulle 12 giornate
  del backtest della cascata (curva dei due parametri) e `--phase final` sul
  set completo;
- **conferma**: 12 giornate nuove, dichiarate prima del run (elenco in
  `docs/NOWCAST_ENSEMBLE_CONFERMA.md`), stessa pipeline e stessi parametri.

Metriche: Brier e BSS contro la frequenza base del periodo (leave-one-day-out),
reliability a 10 bin, CRPS fair contro la climatologia empirica, spread-skill.

Esito misurato (conferma, 12 casi, occ 1,8 / σ 0,3 / k 1,8, 20 membri): BSS
pooled **+0,529/+0,462/+0,316** (0,5 mm/h) e **+0,288/+0,186/+0,121** (5 mm/h)
a +15/+30/+60; CRPS **0,076 vs 0,105**; reliability alla scala dichiarata
(20 km) **0,095/0,066/0,084**, a 1 km 0,109/0,051/0,073; bias **1,12/0,97/0,85**;
BSS convettivo **−0,77 a 1 km**, **+0,325/+0,261/+0,128 a 20 km**. Il verdetto
resta **non passa per un solo numero: bias a 0,5 = 1,12 (soglia 1,10)**.

## 7. Limiti

- Le probabilità sono **frequenze di ensemble**, non probabilità calibrate in
  produzione: la calibrazione è misurata, non assunta.
- I 20 membri non sono indipendenti e non c'è nessuna perturbazione
  dell'ambiente (CAPE, shear) né membro NWP: l'incertezza coperta è quella del
  trasporto e della struttura fine, non quella della convezione futura.
- La perturbazione del moto è un campo per blocchi + deformazione, applicato
  come spostamento composto dopo il trasporto (approssimazione dichiarata e
  misurata in `audit/ensemble_integration_ab.json`).
- Il rumore riproduce lo spettro della banda, non la struttura fine osservata:
  è incertezza, non previsione del dettaglio.
- **Il convettivo a 1 km non è prevedibile** (BSS −0,77): a celle di 20 km
  passa (+0,325/+0,261/+0,128) e a 10 km no (−0,10 di media). Il prodotto va
  letto e verificato a celle di 20 km. Su 2026-08-21 (la giornata peggiore)
  area e centroide del campo deterministico sono giusti (a +15 116.389 pixel
  previsti ≥0,5 contro 108.064 osservati, centroide a ~10 km: l'errore è di
  struttura a 1 km, non di posizione del sistema).
- **Bias a 0,5 = 1,12** (soglia 1,10): la correzione è `occurrence_scale`
  ≈ 2,0-2,1; sul set di curve 1,8 dà 0,95 e 2,0 dà 0,93.
- **Coda a 5 mm/h**: bias 0,85 con k=1,8 (era 0,54), ma la dispersione di k
  fra i membri (0,15) allarga la reliability; con 0,05-0,08 la diagonale
  rientra. Un k unico non riproduce insieme media e dispersione della coda.
- La verità è il mosaico SRI DPC: un ensemble perfetto rispetto al SRI non è
  un ensemble perfetto rispetto al suolo.
