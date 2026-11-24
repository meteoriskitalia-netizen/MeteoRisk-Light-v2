# MeteoRisk Light 1.3.0.78 — vento e raffica: 8 classi, m/s + km/h

Bump da **1.3.0.77** (BUILD 212 → **213**). Nessuna modifica al backend né al
nowcast pioggia della 1.3.0.76/1.3.0.77: solo il campo centraline, solo
presentazione (classi, colori, testi). Dati e campi invariati.
APP_FILE: `mri-light-1.3.0.78.html`.

## 1. Da 6 a 8 classi utili

Scala del campo centraline (vento medio, raffica massima, raffica stimata):

| | breakpoint m/s | classi |
| :--- | :--- | ---: |
| prima (1.3.0.75) | 0 / 5 / 10 / 15 / 20 / 25 | 6 |
| ora (1.3.0.78) | 0 / **2,5** / 5 / **7,5** / 10 / 15 / 20 / 25 / **30** | **8** |

Colori (stessa rampa verde → giallo → ambra → arancio → rosso → cremisi → viola),
uno per banda. Distanza RGB massima per coppia adiacente, misurata:

`0-1: 125 · 1-2: 77 · 2-3: 54 · 3-4: 55 · 4-5: 110 · 5-6: 86 · 6-7: 73` —
**minimo 54** (> 40 richiesto).

## 2. Doppia unità (m/s principale, km/h più piccolo)

- tacche della legenda: **`10 (36)`**, `2,5 (9)`, `30 (108)` — il km/h è a 7 px;
- riga dell'unità: **`m/s · km/h`**;
- popup stazione: vento e raffica con `m/s` e `km/h` fra parentesi (la raffica
  prima mostrava solo `m/s`).

## 3. Legenda adattiva: etichette orizzontali, nessuna sovrapposizione

Misura (`scripts/validation/measure_wind_legend_13076.mjs`, stessa stima di
larghezza usata dal codice):

| larghezza | prima | dopo |
| :--- | :--- | :--- |
| 200 px | 6/6 etichette, min 21,2 px | **4** etichette (passo 3), min **8,5 px** |
| 260 px | 6/6, min 33,2 px | **7** etichette (0, 5, 10, 15, 20, 25, 30), min **2,53 px** |
| 320 px | 6/6, min 45,2 px | **7** etichette, min **12,53 px** |

Zero sovrapposizioni in tutti i casi. Alle larghezze strette 2,5 e 7,5 restano
**tacche senza etichetta** (la banda colorata c'è, il numero no): è il passo
adattivo, non una classe mancante.

Verifica DOM reale (Chrome 1243, 260 px): **7 etichette**, `overlap=0`, testi
`0 (0) | 5 (18) | 10 (36) | 15 (54) | 20 (72) | 25 (90) | 30 (108)`, unità
`m/s · km/h`.

## Test

- `scripts/tests/test_station_field.mjs`: **253 controlli / 0 falliti**
  (9 breakpoint e 8 colori, 7 coppie adiacenti > 40, 9 tacche 0–30,
  «10 (36)» e «2,5 (9)», legenda vento senza sovrapposizioni, popup vento e
  raffica con m/s e km/h).
- `test_html_js_syntax.py` + `test_tooltips_coverage.py`: **3 passed**.
- `test_sw_cache_policy.mjs`: **PASS** con `CACHE_NAME = 'mri-1.3.0.78'`.
- `scripts/validation/verify_ui_13075.mjs` (headless): **24/24 PASS**, inclusa
  la legenda vento costruita nel DOM.

## Non fatto / dichiarato

- Nessuno screenshot della legenda su schermo: la misura usa il modello di
  larghezza del codice e il DOM headless, non i pixel del monitor.
- Le nuove classi sono di **presentazione**: nessuna validazione meteorologica
  (i dati non cambiano).
- Con la rete esterna bloccata il boot headless dell'app si interrompe prima
  delle `var` del campo (già nella 1.3.0.75: `STATION_FIELD_K`,
  `STATION_FIELD_SNOW_SCALE`, `L`, `stationsData` sono `undefined`). Per la
  legenda vento l'harness passa le **stesse** classi/colori letti dal file
  servito; DOM, CSS, larghezze e resa delle etichette restano reali.
