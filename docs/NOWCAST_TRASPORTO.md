# Nowcast — trasporto a spostamento composto (2026-11-26)

Base: `origin/main` 1.3.0.85 (BUILD 225). Nessun bump di versione: cambia
solo il motore radar, non l'app.

## 1. Misura sul volume live

Slot riprodotto con il codice di produzione: **2026-11-26T13:25:00Z**, ultimi
6 frame SRI (5 min), dominio nativo **1400 × 1200** a 1,5 km, griglia di
output EPSG:3857 **1183 × 1272** (la WebP ha la STESSA dimensione della
griglia: nessun downscale).

Immagine live scaricata: `data/radar/nowcast_prob030.webp`, 138.318 byte,
`Last-Modified` 2026-11-26T13:47:15Z; `nowcast_lead030.webp` 41.258 byte.

Moto (mediana sui pixel di pioggia del campo di moto effettivo del run):

| grandezza | valore |
| :--- | ---: |
| u | +1,704 px / 5 min |
| v | −1,304 px / 5 min |
| direzione | 37,4° (verso NE, 0° = est) |
| velocità mediana | 2,15 px/5min = **38,6 km/h** |
| `motion_stats.median_speed_kmh` pubblicato | 25,72 (p95 53,71) |

### Angolo e periodo delle strisce

Spettro 2D del campo (finestre 128/256 px sui pixel di pioggia, direttrice
perpendicolare al moto):

| campo | picco dominante | direzione dal moto | prominenza |
| :--- | :--- | ---: | ---: |
| SRI osservato (analisi) | 9,85–11,64 px (**14,8–17,5 km**) | −2,2° … −12,5° | 50,1 |
| deterministico +30 (passi) | 9,85 px (14,8 km) | −12,5° | 80,2 |
| deterministico +30 (warp unico) | 9,85 px (14,8 km) | −12,5° | 42,5 |
| probabilità 0,5 mm/h +30 (passi) | 9,85 px (14,8 km) | −12,5° | 13,2 |
| probabilità 0,5 mm/h +30 (warp unico cubico) | 9,85 px (14,8 km) | −12,5° | 12,6 |

Il picco a 10–12 px (15–18 km) **esiste già nell'osservato** ed è la banda
meteorologica naturale (allineata al moto, −2° … −13°). Il campo di
probabilità non ha nessun picco periodico più forte: **la sua prominenza è
13 contro 50 dell'osservato**.

### Il pettine è nella probabilità, non nell'intensità

`nowcast_prob030.webp` ha **21 valori distinti** (0, 0,05, 0,10 … 1,00):
la probabilità a 20 membri è quantizzata a 1/20 e il **41,7% dei pixel con
p > 0** sta sotto 0,20. Sopra un eco piccolo i contorni di
0,05 / 0,10 / 0,15 sono **bande parallele annidate**: è il "pettine" (in
Puglia 4–6 bande). `nowcast_lead030.webp` non lo mostra perché la scala
mm/h ha bande larghe e le variazioni di 0,2 mm/h restano nello stesso
colore.

## 2. Ipotesi discriminate

| # | ipotesi | esito | prova |
| :-- | :--- | :--- | :--- |
| A | 6/12 warp semi-Lagrangiani | **degrada la texture, non crea il pettine** | HF residua a +30: passi 0,73× l'osservato, warp unico bilineare 1,00×, cubico 1,17×; Nyquist 0,81 / 0,99 / 1,22. La probabilità resta a 21 livelli con prominenza 13,2 (passi) vs 12,6 (cubico): il pettine non cambia |
| B | nearest da qualche parte | **escluso** | nel percorso non c'è nearest (warp `order=1/3`, `reproject` bilineare, WebP lossy); Nyquist ratio 0,86–1,22 < 1,5, un nearest darebbe ~2× |
| C | moto troppo liscio (`extend_motion` σ=25 px) | **escluso come sorgente del pattern** | il warp unico usa lo STESSO moto e il campo numerico non ha picchi periodici (13 vs 50); le bande visibili sono i livelli 1/20 |
| D | rasatura dell'occorrenza (10 km) | **escluso** | con `occurrence_scale = 0` la mappa ha ancora 21 livelli (stessa media, 0,102) |
| E | downscale della WebP | **escluso** | WebP 1183 × 1272 = griglia 1183 × 1272, risoluzione 1,5 km |

## 3. Correzione

1. **Trasporto a spostamento composto** (`nowcast_transport_mode="single"`,
   `nowcast_transport_order=3`): una sola interpolazione cubica per lead,
   `out(T) = W(analisi, −U·T)`. È la regola "interpolate once"
   (Germann & Zawadzki 2002, MWR 130:2859; 2003). Lo schema a passi resta
   disponibile come `"steps"` per l'A/B.
2. **Pettine**: la mappa di probabilità si disegna da un campo smussato di
   1,5 px = 2,25 km (`PROB_IMAGE_SMOOTH_PX`, dichiarato nel JSON come
   `image_smoothing_px`). **Il dato non viene filtrato**: i valori in
   `ensemble.leads[*].probabilities` restano quelli dell'ensemble.
3. **Palette**: LUT RGB a 256 passi interpolata linearmente fra i 7 nodi
   (`PROB_LUT`), nessun salto; legenda come rampa a 101 nodi con tacche a
   0/25/50/75/100% (`legend_ticks`).

## 4. Misure prima/dopo

### Trasporto (campo sintetico a banda limitata, shift noto 2,15/−1,30 px per passo)

| variante | HF residua +30 | HF residua +60 | Nyquist/sorgente +30 | deriva baricentro |
| :--- | ---: | ---: | ---: | ---: |
| catena 6/12 passi bilineari | **0,18×** | **0,13×** | 0,00 | 0,000 px |
| warp unico bilineare | 0,59× | 0,41× | 0,48 | 0,000 px |
| warp unico cubico | **0,88×** | **0,71×** | 0,86 | 0,000 px |

### Volume reale 13:25Z (energia sul dominio di pioggia)

| campo | Nyquist/osservato | HF/osservato |
| :--- | ---: | ---: |
| deterministico +30, passi | 0,81 | 0,73 |
| deterministico +30, warp unico bilineare | 0,99 | 1,00 |
| deterministico +30, warp unico cubico | **1,22** | **1,17** |

### Immagine di probabilità (+30): cosa aggiunge il rendering

| metrica della LUMINANZA | grezza | smussata 1,5 px |
| :--- | ---: | ---: |
| var(Laplaciano)/var(L) | 0,2577 | **0,0083** (−96,8%) |
| salto massimo su 1 px (0-255) | 116,0 | **47,4** |
| salto p99.9 su 1 px | 67,1 | **29,5** |
| frazione HF spettrale (k>0,10) | 0,190 | **0,047** |

La palette **non** era la sorgente: sostituire `np.interp` con la LUT a 256
passi cambia la luminanza di **max 1,5/255** (media 0,06) — cioè il colore
era già continuo. I gradini visibili sono i **21 livelli del dato** (1/20),
e lo smussamento è ciò che li trasforma in un gradiente: HF −97%, salto
massimo su 1 px −59%.

Livelli a 0,01 (101 valori) **non sono ottenibili con 20 membri**: la
probabilità è esattamente k/20, quindi 21 valori. Per 101 livelli servono
≥100 membri (costo ×5) oppure una soglia morbida (cambia la calibrazione):
nessuna delle due è stata fatta.

Correlazione con l'intensità deterministica +30 (pixel di pioggia):
Pearson 0,48 grezza → 0,49 smussata; **Spearman (ranghi) 0,80**.

## 5. Test di regressione aggiunti

- `test_composite_shift_keeps_texture_and_position`: deriva < 0,5 px,
  HF > 0,60 (warp unico) e < 0,25 (catena), Nyquist < 2× l'osservato e
  catena < 0,5× del warp unico.
- `test_composite_shift_is_one_interpolation`: il campo a +L è **una**
  interpolazione sullo spostamento totale.
- `test_prob_image_smoothing_kills_the_comb_and_keeps_the_data`: HF della
  luminanza < 50% del grezzo, 21 livelli nel dato, il dato non viene
  modificato, `smooth_px=0` riproduce il grezzo.
- `test_probability_lut_is_continuous_and_legend_has_ticks`: 256 passi,
  salto massimo 4/255 fra passi adiacenti, equivalenza con i nodi entro
  1/255, legenda a 101 nodi con 5 tacche.

Campo numerico invariato dalla resa: `write_prob_webp` lavora su una copia
e non scrive mai l'array; verificato su 5 punti (0,10 / 0,40 / 0,60 / 0,25 /
0,10 prima e dopo, bit-identici) e nell'input (`np.array_equal` True).

## 6. A/B appaiato su 4 giornate reali

Stesse giornate, stessa ancora (15:00), stesse maschere e stesso frame
iniziale; cambia solo il trasporto deterministico (`--transport-mode`).
Dettaglio in `audit/nowcast_transport_ab.json`.

| metrica (soglia) | lead | passi | warp unico cubico | Δ |
| :--- | ---: | ---: | ---: | ---: |
| BSS (0,5 mm/h) | +15 | 0,7137 | 0,7114 | **−0,0023** |
| BSS (0,5 mm/h) | +30 | 0,6435 | 0,6369 | **−0,0066** |
| BSS (0,5 mm/h) | +60 | 0,5064 | 0,4920 | **−0,0144** |
| BSS (2,0 mm/h) | +30 | 0,5644 | 0,5594 | −0,0051 |
| CSI det (0,5) | +15 | 0,7507 | 0,7456 | −0,0051 |
| CSI det (0,5) | +30 | 0,6678 | 0,6574 | −0,0104 |
| CSI det (0,5) | +60 | 0,5567 | 0,5388 | −0,0179 |
| POD det (0,5) | +30 | 0,8028 | 0,7905 | −0,0123 |
| FAR det (0,5) | +30 | 0,2012 | 0,2039 | +0,0027 |
| bias det (0,5) | +30 | 1,005 | 0,993 | −0,012 |

Il criterio "BSS 0,5 a +30 non cala più di 0,02" è rispettato (−0,0066).
A **+60** il calo è −0,0144, ancora dentro il budget ma vicino al limite: il
warp unico conserva la texture (più struttura fine = più falsi allarmi a
soglia) mentre la catena di 6–12 ricampionamenti la sfoca e "guadagna"
qualche punto di CSI a spese della struttura. La scelta è il trasporto
corretto, non il punteggio: sfocare per alzare il CSI sarebbe la
scorciatoia che questo fix evita.

## 7. Limiti dichiarati

- Lo smussamento dell'immagine è una scelta di **resa** (2,25 km, sotto il
  dettaglio utile del prodotto), non di calibrazione: non corregge la
  quantizzazione a 1/20 dei valori pubblicati. Per eliminarla servono più
  membri (costo lineare) o una soglia morbida (cambia la calibrazione).
- L'A/B BSS/CSI è su **4 giornate × 1 ancora** (4 casi, 3,67 M pixel per
  lead): è un confronto appaiato, non la climatologia delle 12 giornate.
  La differenza a +60 (−0,0144) va riletta su un campione più ampio prima
  di considerarla definitiva.
- Nessuna validazione italiana del cubico: la scelta è della letteratura e
  la misura è sintetica + spettrale su 1 volume reale + BSS/CSI su 4 casi.
