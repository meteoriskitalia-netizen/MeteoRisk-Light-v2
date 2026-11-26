# Nowcast — forma credibile, evoluzione e resa (2026-11-26)

Caso di riferimento: **2026-11-26T13:25:00Z** (ultimi 6 frame SRI DPC, griglia
nativa 1400×1200 a 1 km). Le osservazioni ai lead sono scaricate dall'archivio
DPC agli stessi istanti (+15/+30/+60/+90). Immagini in
`docs/evidence/nowcast_20261126/`.

## 1. Il problema misurato

Il nowcast per pixel a 1 km, anche dopo la regolarizzazione divergence-free,
produceva:

- bordi frastagliati e strutture a nastro ai lead lunghi;
- aspetto (p90 delle componenti ≥2 mm/h) 6,11 a +120 contro 3,53 osservato
  (**1,73×**) e 7,58 a +180 (**2,43×**);
- dettaglio fine che calava col lead (HF 0,129 → 0,111 a +60 prima del fix);
- un'intensità quasi illeggibile su fondo scuro (blu notte con alpha pieno
  già a 0,2 mm/h).

## 2. Tre modalità di moto, stesso caso

Tutte con la regolarizzazione divergence-free attiva e la stessa maschera.
Immagini affiancate: `modeA_030.png` / `modeA_060.png`, `modeB_*`, `modeC_*`.

| modo | cosa fa | CSI@30 | CSI@60 | aspetto (pesato) @30/@60 |
| :--- | :--- | ---: | ---: | ---: |
| **A. per pixel** | warp cubico per pixel + regolarizzazione | 0,5718 | 0,4517 | 0,98 / 0,89 |
| **B. per oggetto** | componenti connesse ≥2 mm/h, vettore mediano, spostamento rigido | 0,5559 | 0,4487 | 1,20 / 0,89 |
| **C. raster 3 km** | media a blocchi 3×3 di campo e moto, warp, upsample bilineare | **0,5871** | **0,4596** | 0,98 / 0,88 |

Verdetto **visivo** (le immagini contano più del numero):

- **A**: bordi frastagliati, filamenti sottili e una coda a gancio; il nucleo
  intenso è troppo esteso.
- **B**: forme più coerenti e bordi meno frastagliati di A, ma il vettore
  unico per componente sposta un'intera banda in modo rigido; restano
  disallineamenti e un nucleo troppo pieno. CSI@30 scende a 0,5559 (−0,016).
- **C**: è la più credibile. Bordi lisci, nessuna raggiera, nessun nastro;
  la banda mantiene la forma dell'osservato e le celle del sud restano
  riconoscibili. È anche l'unica che **migliora** il CSI rispetto ad A
  (+0,015 a +30, +0,008 a +60), quindi non c'è da spendere il budget di
  −0,02.

**Scelta: C**, con ripristino HF deterministico (sotto).

## 3. Evoluzione: HF che non cala

Il trasporto a 3 km perde dettaglio fine. La soluzione provata e scartata:

- **rumore STEPS moltiplicativo per banda** (cascata AR(2) + rumore con peso
  √(1−g²), varianza riportata all'analisi): numericamente corretto (HF
  0,149/0,148/0,180/0,153) ma **visivamente un retino da Nyquist** e CSI@30
  0,516. Scartato.

La soluzione adottata è un **unsharp deterministico** con ampiezza risolta
per portare la varianza alto-pass (<~4 px) al 70% di quella dell'analisi, con
crescita 20% su 90 min:

```
out = field + λ·(field − gaussian(field, 4 px))
λ risolto per var_hp(out) = 0.70 · var_hp(analisi) · (1 + 0.20·lead/90)
```

Nessun rumore, nessuna pioggia creata dal nulla, nessun retino. Misura con il
codice di produzione (livello 0,70):

| lead | HF previsto | HF osservato | CSI 0,5 | CSI 2 | CSI 5 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| +15 | 0,1081 | 0,1444 | 0,6809 | 0,5561 | 0,5774 |
| +30 | 0,1087 | 0,1509 | **0,5517** | 0,3979 | 0,4152 |
| +60 | 0,1110 | 0,1918 | **0,4332** | 0,2683 | 0,2812 |
| +90 | 0,1135 | 0,1494 | 0,3337 | 0,1890 | 0,1534 |

HF **cresce** col lead (0,1081 → 0,1135). Rispetto ad A il costo è −0,020 a
+30 e −0,019 a +60: dentro il budget dichiarato di −0,02, e le soglie
assolute (+30 ≥0,55, +60 ≥0,42) sono rispettate.

## 4. Troncamento a +90

Con il trasporto a 3 km il rapporto d'aspetto p90 previsto/osservato è:

| lead | p90 previsto | p90 osservato | rapporto |
| ---: | ---: | ---: | ---: |
| +90 | 4,22 | 3,89 | **1,09** |
| +120 | 5,81 | 3,53 | **1,65** |
| +150 | 5,01 | 3,77 | 1,33 |
| +180 | 5,96 | 3,12 | **1,91** |

Il primo lead oltre 1,5× è **+120**. Il prodotto si ferma a **+90** (l'ultimo
lead con rapporto ≤1,5). I lead 120/150/180 non vengono più calcolati né
pubblicati; il payload porta `truncation` con limite, lead rimossi e misure.

## 5. Resa leggibile

- Palette a 7 nodi: ciano (0,5) → azzurro (2) → verde (5) → giallo (10) →
  arancio (20) → rosso (50) → magenta (100+).
- Alpha 0 sotto **0,5 mm/h**, pieno a **2 mm/h** (prima era pieno a 0,2:
  tutto quasi nero).
- La legenda del client legge il `legend` del payload: server e client non
  possono divergere.

## 6. Probabilità: non ridondante, ma a soglia alta

Misurato sul caso (probabilità dell'ensemble a 20 membri contro la maschera
deterministica dello stesso lead):

| lead | IoU prob≥0,5 vs maschera | Spearman |
| ---: | ---: | ---: |
| +15 | 0,6657 | 0,6154 |
| +30 | 0,5775 | 0,5910 |
| +60 | 0,5070 | 0,5624 |

Non è equivalente alla maschera (IoU < 0,8), quindi **resta**. Le immagini
pubblicate usano però le soglie **2/5/10 mm/h** (default 5), dove l'intensità
è ambigua: a 5 mm/h il BSS misurato è 0,6266/0,4959/0,3260 a +15/+30/+60,
contro una correlazione con l'intensità di 0,32. Il blocco `redundancy` per
lead pubblica IoU e Spearman misurati nel run.

## 7. Test

- `test_nowcast_field.py`: **35 passed** (12 lead, troncamento dichiarato,
  trasporto a 3 km, HF non calante, palette e alpha, regressioni del trasporto
  a passi/composto).
- `radar_engine`: **1001 passed** (2 attese aggiornate: 12 lead e soglia
  probabilità 5).
- `test_html_js_syntax.py`: 1 passed; `test_sw_cache_policy.mjs`: PASS
  (`CACHE_NAME = mri-1.3.0.85-r2`).

## 8. Limiti dichiarati

- Il caso A/B/C è **uno** (2026-11-26T13:25Z). Le metriche di modalità sono
  un confronto appaiato, non una climatologia; il replay di produzione e le
  immagini finali sono sullo stesso caso.
- L'unsharp ricostruisce il **dettaglio**, non la posizione: non corregge un
  errore di moto.
- Il nucleo intenso resta più esteso dell'osservato (nessun decadimento
  fisico applicato: la misura precedente su 12 giorni lo ha respinto).
- La probabilità resta una frequenza di ensemble, non una probabilità
  calibrata in produzione; il prodotto va letto a celle di 20 km.
- La verifica live (workflow radar) è la prova che il codice gira; non è una
  validazione statistica prospettica.
