# MeteoRisk Light 1.3.0.79 — nowcast sulla timeline unica + legenda SRI

Bump da **1.3.0.78** (centraline/vento, già su `main`; BUILD 213 -> **214**).
APP_FILE: `mri-light-1.3.0.79.html`. Nessuna modifica al backend.

## 1) Il nowcast usa la timeline sincronizzata (un solo player)

Il mini-player del nowcast è stato **rimosso** (`#nowcast-player-wrap`, pulsante
e slider dedicati non esistono più nel DOM).

- Prodotto `nowcast` → `#sync-timeline-panel` **visibile**, con play/pausa,
  slider, preload e velocità **riusati**.
- Timeline costruita su `nowcast.leads[]`: **stessa infrastruttura**
  (`buildSyncTimeline`, `ensureSyncTimeline`, `applySyncFrame`, `toggleSyncPlay`,
  `syncIndex`) con una **sorgente** diversa (`syncTimelineSource`).
- Etichetta `#tile-load-box`: `+N min · HH:MM` (con `· ufficiale` su
  +15/+30/+60) **al posto di** «Frame: n/25»; time display `+N min`.
- Play: 1 frame ogni 700 ms diviso per x1/x2/x4/x8. Preload: carica tutti i WebP
  e mostra `n/n OK`.
- **Bug riparato**: Radar Player → Nowcast lasciava il pannello sui 25 slot
  radar. Ora la sorgente cambia e il pannello resta attivo con i frame nowcast.
- Nowcast → **Radar Player**: torna a **25 slot** e «Frame: n/25».
- Nowcast → **«— nessuno —»**: pannello nascosto, nessun timer, nessun overlay.

## 2) Legenda allineata alla scala SRI

- Tacche **1 · 5 · 10 · 20 · 50 · 100+ mm/h**, con gli **stessi RGB** della
  legenda SRI del radar letti da `DPC_SRI_STOPS` (nessun colore duplicato).
- Nota «dopo 30 minuti l'affidabilità cala» + una riga sola: «sotto 1 mm/h il
  radar osservato è trasparente; il nowcast mostra la pioggia debole».
- Official +15/+30/+60 riconoscibili nell'etichetta (`· ufficiale`).

## Verifica

- **`node --check`**: 9/9 blocchi `<script>` inline.
- **pytest**: `test_html_js_syntax.py` + `test_tooltips_coverage.py` -> 3 passed.
- **`test_sw_cache_policy.mjs`**: PASS (`CACHE_NAME = mri-1.3.0.79`).
- **`test_station_field.mjs`**: **253 controlli / 0 falliti** (include la nuova
  scala vento a 8 classi della 1.3.0.78).
- **Browser reale** (`scripts/validation/verify_ui_nowcast_13079.mjs`, Chrome
  headless 1243 via CDP, server statico locale): **39/39 PASS**
  - Radar Player: 25 slot, «Frame: n/25», pannello attivo;
  - Radar → Nowcast: pannello **ancora attivo**, sorgente `nowcast`, 3 lead,
    slider max 2, `+15 min · HH:MM`, overlay unico (pane z 407, opacità 0.75);
  - play avanza e pausa congela; preload `3/3 OK`;
  - legenda: 6 valori SRI, 6 RGB identici a `DPC_SRI_STOPS`, note e official;
  - lista sintetica **12 lead** → timeline 12, slider max 11, ufficiali 15/30/60;
  - Nowcast → Radar Player: 25 slot, «Frame: n/25», radar acceso;
  - Nowcast → «— nessuno —»: pannello nascosto, nessun timer/overlay;
  - **0 doppi player**; rete nowcast senza query-string e 200; **0 eccezioni JS**.

## Conteggio funzioni

**1377** dichiarazioni `function` (misurate sull'HTML unito).

## Limiti dichiarati

- I dati live hanno ancora **3 lead** (15/30/60): il caso a 12 è coperto dal test
  sintetico, ma **non è stato visto con i 12 WebP reali**.
- La verifica browser è headless (rete esterna bloccata tranne le CDN di
  Leaflet): non misura il rendering su display reale né la latenza di Pages.
- Con la rete esterna bloccata il fetch radar fallisce: la timeline radar (25
  slot) viene comunque costruita, ma senza tile.
