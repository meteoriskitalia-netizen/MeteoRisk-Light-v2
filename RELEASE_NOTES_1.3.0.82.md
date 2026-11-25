# MeteoRisk Light 1.3.0.82 — pioggia 6 h/24 h: cumulata parziale = limite inferiore

Bump da **1.3.0.81** (BUILD 221 → **222**). Backend invariato. Solo campo
centraline: regola di lettura delle finestre e presentazione.
APP_FILE: `mri-light-1.3.0.82.html`.

## Perché la Pioggia mostrava solo 1 h e 3 h

Due cause:

1. `rain_24h_partial = true` su **tutte** le stazioni disabilitava la finestra
   (regola sbagliata per una cumulata);
2. il blob `daily_series.json` arriva in background e, quando arrivava, il
   selettore **non veniva riallineato**: 6 h/24 h restavano disabilitate per
   sempre, anche dopo il caricamento.

## Regola nuova

- **Cumulata** (`rain_6h_mm`, `rain_24h_mm`) e **massimo** (`gust_max_*_ms`):
  il valore si mostra **anche se parziale**. I millimetri/massimi misurati sono
  quelli; le ore senza dato non sono stimate → è un **limite inferiore**
  (possibile sottostima).
- **Medie** (`temp_*`, `rh_*`, `dew_*`, `wind_*_ms`): resta il gate di
  completezza (una media sui buchi non è una media).
- Aggiunta la mappatura dei flag `*_partial` anche per temp/rh/dew (prima
  l'assenza faceva passare come completa una media parziale).
- `stationsLoad()` riallinea il selettore quando il blob arriva; il selettore
  chiede il blob se non è ancora stato caricato.

Etichetta breve (accanto a Finestra) e title:

- 24 h: «**cumulata da mezzanotte · parziale, possibile sottostima**»;
- 6 h: «**cumulata 6 ore · parziale, possibile sottostima**»;
- title: conteggi parziali/complete + «limite inferiore, ore mancanti non stimate».

Per la Pioggia **5 min, 30 min e «oggi» sono nascoste** (non sono finestre di
accumulo pubblicate; «oggi» coincide con 24 h da mezzanotte).

## Dato reale misurato (2026-11-25)

Blob `stations-daily`: schema `stations-daily-series/2`, day `2026-11-25`,
generated `15:31:17Z`, **5.138 stazioni**.

| campo | non-null | partial true | partial false |
| :--- | ---: | ---: | ---: |
| `rain_24h_mm` | **4.163** | **4.163** | 0 |
| `rain_6h_mm` | 4.163 | 2.420 | 1.743 |
| `temp_24h_c` | 3.562 | 872 | 2.690 |
| `wind_24h_ms` | 1.704 | 1.704 | 0 |
| `gust_max_24h_ms` | 343 | (nessun flag) | (nessun flag) |

Raggiungibilità: **raw del branch `stations-daily` = HTTP 200** (18.760.283
byte in chiaro); **Pages = 404**. Il client usa raw come prima sorgente: il
caricamento funziona (verificato in Chrome headless con rete attiva).

## Verifica browser reale (Chrome headless, rete attiva)

- finestre visibili con Pioggia: **1 h, 3 h, 6 h, 24 h**; 5 min/30 min/oggi
  nascoste;
- dopo il blob: **6 h e 24 h abilitate**;
- **24 h**: disponibile, 4.089 stazioni usate, **8.630 px** dipinti;
- **6 h**: 4.089 stazioni usate, 8.718 px;
- vento medio 24 h (tutte parziali) **bloccato**; temperatura disponibile
  perché ha 2.690 stazioni complete; raffica massima disponibile.

## Test

- `test_station_field.mjs`: **258/260 PASS**. I 2 assert rossi sono di
  **performance** e sono **preesistenti**: misurati identici sull'HTML
  `1.3.0.81` **non modificato** (`full` 818 ms > 400, `contour` 1087 ms > 800).
  Non sono causati da questa release; le soglie non sono state allentate.
- `test_html_js_syntax.py` + `test_tooltips_coverage.py`: **3 passed**.
- `test_sw_cache_policy.mjs`: **PASS** (`CACHE_NAME = 'mri-1.3.0.82'`).

## Non fatto / dichiarato

- Pages resta 404 per `daily_series.json`: l'URL raw (primario) è corretto e
  funzionante, quindi non c'è un URL da correggere lato client.
- Le cumulate parziali sono **limiti inferiori**, non misure complete.
  Nessuna validazione contro pluviometri indipendenti.
