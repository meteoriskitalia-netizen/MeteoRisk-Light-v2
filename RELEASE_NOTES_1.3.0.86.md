# MeteoRisk Light 1.3.0.86 — Centraline: 6 h/24 h scorrevoli, copertura nel client

Bump da **1.3.0.85** (BUILD 225 → 226). Backend invariato: `scripts/stations/`
non è toccato. Solo client (campo centraline), VERSION e sw.

APP_FILE: `mri-light-1.3.0.86.html`.

## Cosa cambia

- **Etichetta**: la finestra 24 h non è più «cumulata da mezzanotte» ma
  **«cumulata 24 ore»**. È una finestra **scorrevole** che termina adesso e
  attraversa la mezzanotte (6 h: «cumulata 6 ore»). Il `title` riporta
  l'intervallo effettivo `window_start→window_end` letto da `window_policy`
  e la copertura media letta da `coverage_frac`.
- **Gate esplicito**: una **cumulata** o un **massimo** con almeno un valore si
  mostra **sempre**, anche con copertura < 20%. Il valore è un limite inferiore
  dichiarato. La nota accanto a Finestra e la riga di stato scrivono
  «parziale, copertura N%».
- **Medie invariate**: temperatura, umidità, rugiada e vento medio restano con
  il gate di completezza (una media sui buchi non è una media).
- **Coerenza 1 h/3 h dichiarata nel tooltip**: 6 h/24 h sommano bucket orari
  con gate di copertura ≥50%; 1 h/3 h usano il prodotto grezzo ancorato a
  `data_end`. Sul blob del 2026-11-27: **66 stazioni su 3.945** con entrambi i
  valori hanno 24 h < 3 h (differenze ≤0,2 mm). È una differenza di
  prodotto/gate, non un errore della finestra, e non viene nascosta.
- **Nuovi campi letti**: `window_policy`, `window_start`, `window_end`,
  `coverage_frac`.

## Misura live (blob 2026-11-27T08:09:24Z, 5.097 stazioni)

`rain_24h_mm` non-null 4.108, copertura media 34,5%; `rain_6h_mm` 3.763
complete; `rain_24h < rain_1h` **0/4.013**, `rain_24h < rain_6h` **0/4.051**;
24 h < 3 h 66/3.945 (≤0,2 mm, causa sopra). Browser reale (localhost, nuovo
HTML): finestra 24 ore selezionata, 4.090 stazioni usate, nota «cumulata 24 ore
· copertura media 35% · parziale, possibile sottostima», title con
`09:21→09:21` e la frase 1 h/3 h.

## Test

- `test_station_field.mjs`: **266 controlli, 0 falliti** (erano 258).
- `test_html_js_syntax.py`: verde; `test_sw_cache_policy.mjs` PASS;
  `test_stations_chart.mjs` 12/12; `test_station_field_index.py` verde.
- `test_tooltips_coverage.py::test_tooltips_have_title` resta l'unico
  fallimento, **pre-esistente** (sei `button.nc-prob-btn` del nowcast senza
  `title`, non toccati da questa release).

## File

- `mri-light-1.3.0.86.html` (rinomina da 1.3.0.85 + modifiche client)
- `VERSION` (BUILD 226, APP_FILE)
- `sw.js` (`CACHE_NAME = 'mri-1.3.0.86'`)
- `scripts/tests/test_station_field.mjs`
