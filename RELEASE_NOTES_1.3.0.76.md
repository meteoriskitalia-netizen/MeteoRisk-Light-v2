# MeteoRisk Light 1.3.0.76 — toggle del nowcast pioggia (SRI)

Bump da **1.3.0.75** (BUILD 206 -> **207**). Nessuna modifica al backend: i dati
erano già in produzione (`data/radar/nowcast.json`, `nowcast_lead015/030/060.webp`).
APP_FILE: `mri-light-1.3.0.76.html`.

## Cosa è stato aggiunto

Toggle **«Nowcast pioggia»** nel gruppo dei layer (`.map-corner-switches`),
**spento di default**, con `title` valido; selettore **+15 / +30 / +60 min**
(default **+30**) visibile **solo** con il toggle ON.

- **Overlay**: `L.imageOverlay` sul WebP del lead scelto, bounds letti da
  `grid.bounds` del JSON (EPSG:3857) e convertiti con
  `map.options.crs.unproject(L.point(x, y))`; pane dedicato **`nowcastPane`
  z-index 407** (`pointer-events:none`), opacità **0.75**.
- **URL senza query-string**: con un cache-buster il CDN risponde 404
  (verificato live). Le richieste reali nel browser sono
  `/data/radar/nowcast.json`, `/data/radar/nowcast_lead030.webp`,
  `/data/radar/nowcast_lead060.webp`, tutte **200** e senza `?`.
- **Legenda** in mm/h: **0,05 · 0,5 · 2 · 5 · 10 · 20+** e la frase
  «dopo 30 minuti l'affidabilità cala».
- **Cambio lead**: l'immagine viene **sostituita** (vecchio layer rimosso, mai
  due overlay). **Spegnimento**: overlay e legenda rimossi. **Cambio prodotto**
  fuori da radar/LIVE: il layer viene nascosto (stessa logica degli altri layer
  radar-derivati).

## Gate

Il toggle è **pubblico** solo con `available === true` **e**
`beats_persistence === true` (un `null` vale `false`).

- `available:false` **oppure** `beats_persistence !== true` -> il toggle è
  visibile **solo con Sviluppo ON** (`isSviluppoActive`);
- spegnendo Sviluppo, se i dati non superano il gate pubblico, il toggle
  sparisce e il layer viene rimosso.

## Verifica

- **`node --check`**: 9/9 blocchi `<script>` inline.
- **pytest**: `test_html_js_syntax.py` + `test_tooltips_coverage.py` -> 3 passed.
- **`.mjs`**: `test_sw_cache_policy` PASS, `test_app_mode_selector` PASS,
  `test_ensemble_module` PASS, `test_satellite_sources_italy` PASS,
  `test_fascia_slots` PASS.
- **`test_station_field.mjs`**: 247 controlli, **2 FAIL solo di tempo**
  (realm 811 ms > 400 ms; contour 1034 ms > 800 ms) su questo host.
- **`test_player_fluidity.mjs`**: 2 FAIL **pre-esistenti** (E1/E5 toolbar),
  non toccati da questa release.
- **Browser reale** (Chrome headless 1243 via CDP, server statico locale,
  script `scripts/validation/verify_ui_nowcast_13076.mjs`): **33/33 PASS**
  - toggle OFF di default, selettore lead nascosto;
  - ON -> overlay `nowcast_lead030.webp` nel pane `nowcastPane` z 407,
    opacità 0.75, immagine caricata, legenda e lead visibili, **1** overlay;
  - lead 60 -> URL `nowcast_lead060.webp`, layer **sostituito**, sempre 1;
  - OFF -> overlay, legenda e selettore rimossi;
  - gate: dev OFF + `beats_persistence` true -> visibile; `false` -> nascosto;
    `null` -> nascosto; `available:false` -> nascosto; Sviluppo ON -> visibile;
  - cambio prodotto a «Mappa dei rischi» -> layer rimosso, checkbox spenta;
  - rete nowcast senza query-string e tutta 200; **0 eccezioni JS** e
    **0 errori console** riferiti al nowcast.

## Conteggio funzioni

**1350 -> 1362** dichiarazioni `function`: +12 nuove, nessuna rimossa.

## Limiti dichiarati

- Il nowcast è **trasporto puro** (nessuna crescita/decadimento); a +60 min e
  0,5 mm/h la persistenza può vincere (misurato nel replay: 0,464 vs 0,470).
- La verifica in browser gira su **Chrome headless** con rete esterna bloccata
  (tranne le CDN di Leaflet): non misura il rendering su display reale né la
  latenza di rete verso GitHub Pages.
- I due FAIL di `test_station_field.mjs` sono soglie di **tempo** su questo host,
  non regressioni funzionali: `local` e i casi neve passano tutti.
