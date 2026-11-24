# MeteoRisk Light 1.3.0.77 — nowcast pioggia come prodotto dedicato

Bump da **1.3.0.76** (BUILD 207 -> **208**). Nessuna modifica al backend: il
client legge `data/radar/nowcast.json` e i WebP già pubblicati.
APP_FILE: `mri-light-1.3.0.77.html`.

## Cosa cambia rispetto alla 1.3.0.76

Il nowcast **non è più un toggle nel gruppo dei layer**: è un **prodotto** nel
selettore `#product-select`, subito dopo «Radar Player».

- **Voce**: `Nowcast pioggia`, con `title`.
- **Accensione** (`nowcastProductOn`): mostra overlay, player e legenda; spegne i
  layer che interferiscono (radar live, satellite, struttura convettiva, embed
  radar residuo).
- **Spegnimento** (`nowcastProductOff`): rimuove overlay, ferma il timer,
  nasconde player e legenda.
- **`syncMapCardForProduct`**: ramo esplicito per `nowcast`, la `.map-card` resta
  visibile come per `radar-player`.
- Il vecchio toggle nei layer è stato **rimosso** (markup e funzioni).

## Player

- Pulsante **play/pausa** e **slider** costruiti **dinamicamente** su
  `nowcast.json -> leads[]`: nessun 3 o 12 hardcodato. Se il motore pubblica 12
  frame lo slider ha 12 posizioni; con 3 lead ne ha 3.
- **Un solo** `L.imageOverlay`: ogni frame chiama `setUrl()` sullo stesso layer,
  mai un layer nuovo.
- Cadenza **1 frame ogni 700 ms**.
- Etichetta `+25 min · 16:40` con l'ora **locale** del `valid_time`.
- **Precaricamento** di tutti i WebP al primo ON (~8 KB ciascuno; ~100 KB per 12).
- **URL senza query-string** (con cache-buster il CDN risponde 404).

## Gate

La voce compare solo con `available === true` **e**
`beats_persistence !== false` (un `null` passa), **salvo Sviluppo ON**.
La voce nascosta è `hidden` + `disabled` + `display:none`: nessun buco nella
tendina. Se il gate si chiude mentre il prodotto è attivo, viene spento.

## Legenda

mm/h con i breakpoint **0,05 · 0,5 · 2 · 5 · 10 · 20+** e la nota
«dopo 30 minuti l'affidabilità cala».

## Verifica

- **`node --check`**: 9/9 blocchi `<script>` inline.
- **pytest**: `test_html_js_syntax.py` + `test_tooltips_coverage.py` -> 3 passed.
- **`test_sw_cache_policy.mjs`**: PASS (`CACHE_NAME = mri-1.3.0.77`).
- **`test_station_field.mjs`**: 247 controlli / 0 falliti.
- **`test_app_mode_selector`**, **`test_ensemble_module`**, **`test_fascia_slots`**,
  **`test_satellite_sources_italy`**: PASS. **`test_player_fluidity`**: 2 FAIL
  pre-esistenti (E1/E5 toolbar).
- **Browser reale** (`scripts/validation/verify_ui_nowcast_13077.mjs`, Chrome
  headless 1243 via CDP, server statico locale): **35/35 PASS**
  - option presente, con `title`, subito dopo Radar Player, visibile con
    Sviluppo OFF;
  - selezione -> primo frame (lead minimo) in un solo overlay, pane
    `nowcastPane` z 407, opacità 0.75, pannello + legenda + player visibili,
    slider `max = lead-1`, tutti i frame precaricati, immagine caricata,
    `.map-card` visibile, radar/satellite/LIVE/struttura convettiva spenti;
  - play -> timer attivo, frame e URL avanzati; pausa -> frame congelato;
  - slider -> ultimo frame con URL e label coerenti, sempre un solo overlay;
  - lista sintetica di **12 lead** -> slider max 11, primo `+5 min`;
  - ritorno a **Radar Player** -> nowcast spento e radar acceso;
  - **«— nessuno —»** -> tutto spento, nessun layer orfano;
  - gate: `beats false` o `available false` -> voce nascosta; `beats null` ->
    visibile; Sviluppo ON -> visibile;
  - rete nowcast senza query-string, tutte 200; **0 eccezioni JS**, **0 errori
    console** sul nowcast.

## Conteggio funzioni

**1362 -> 1375** dichiarazioni `function`: +13, nessuna rimossa.

## Limiti dichiarati

- I dati live hanno ancora **3 lead** (15/30/60). Il client è dinamico e il caso
  a 12 frame è coperto dal test sintetico, ma **non è stato visto con i 12 WebP
  reali**: quando il motore li pubblicherà va rifatta la verifica live.
- `test_station_field.mjs`: **247 controlli / 0 falliti** in questa corsa. In due
  corse precedenti erano fallite solo le due soglie di **tempo** su questo host
  (realm 811 ms > 400 ms; contour 1034 ms > 800 ms): metriche instabili, non
  regressioni funzionali.
- `test_player_fluidity.mjs` ha 2 FAIL **pre-esistenti** (E1/E5 toolbar), non
  toccati da questa release.
- La verifica browser gira in headless con la rete esterna bloccata (tranne le
  CDN di Leaflet): non misura il rendering su display reale né la latenza verso
  GitHub Pages.
