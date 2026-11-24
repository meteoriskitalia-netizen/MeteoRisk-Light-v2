# MeteoRisk Light 1.3.0.80 — nowcast che si aggiorna da sé + tasto Aggiorna

Bump da **1.3.0.79** (BUILD 216 -> **217**). APP_FILE:
`mri-light-1.3.0.80.html`. Nessuna modifica al backend.

## 1) Auto-refresh ogni 5 minuti

Mentre il prodotto **Nowcast pioggia** è attivo il client rilegge
`data/radar/nowcast.json` ogni **5 minuti** (il motore gira ogni 10, così non si
perde il giro).

Se `source_slot` è cambiato:

- la **timeline viene ricostruita** sui nuovi `leads[]` (12 frame reali oggi);
- la **posizione non si resetta**: si mantiene lo stesso `lead_min` (in pausa o
  in play) e si aggiorna l'immagine;
- l'etichetta mostra l'**ora del nuovo slot**.

## 2) WebP senza query-string

Il CDN risponde **404** con un cache-buster (`?v=`), quindi:

- URL **nudo** (`data/radar/nowcast_lead0XX.webp`);
- reload forzato con **`fetch(url, { cache: 'reload' })`**;
- ogni immagine viene convertita in **blob** (`URL.createObjectURL`) e usata
  dall'overlay: il browser non può servire la versione vecchia e non si introduce
  nessun 404.

## 3) Tasto «Aggiorna»

Nel pannello del nowcast: rilegge subito il JSON, con stato
**«aggiorno…» → «slot HH:MM»**, poi torna «Aggiorna». In caso di errore:
«slot non disponibile». `title` valido.

## 4) Stop pulito e background

- Cambiando prodotto o spegnendo, l'**intervallo viene cancellato** (nessun timer
  orfano che continua a fare fetch).
- Con `document.hidden` il tick **non fa fetch**; al ritorno in primo piano
  riprende se sono passati ≥ 5 minuti.

## 5) Freschezza dello slot

`#nowcast-slot` mostra `slot HH:MM` e, se lo slot ha **più di 30 minuti**,
`slot HH:MM · N min fa`: un dato vecchio non sembra più attuale.

## Verifica

- **`node --check`**: 9/9 blocchi `<script>` inline.
- **pytest**: `test_html_js_syntax.py` + `test_tooltips_coverage.py` -> 3 passed.
- **`test_sw_cache_policy.mjs`**: PASS (`CACHE_NAME = mri-1.3.0.80`).
- **`test_station_field.mjs`**: 253 controlli, **2 FAIL solo di tempo** su questo
  host (realm 735 ms > 400; contour 1149 ms > 800), non funzionali.
- **Browser reale** (`scripts/validation/verify_ui_nowcast_13080.mjs`, Chrome
  headless 1243, server con JSON mutabile): **34/34 PASS**
  - Radar Player -> Nowcast: timeline condivisa, 12 lead, timer attivo;
  - cambio `source_slot` -> timeline ricostruita **senza reload**, etichetta
    aggiornata, overlay ancora unico;
  - pausa su **+30** -> nuovo slot -> resta **+30** con immagine **blob nuova**
    (caricata);
  - slot vecchio di 40 min -> **«slot HH:MM · 40 min fa»**;
  - tasto **Aggiorna**: «aggiorno…» immediato, poi «slot HH:MM», pulsante
    riabilitato, nuova richiesta JSON;
  - `document.hidden` -> **nessun fetch**; al ritorno riprende;
  - cambio prodotto -> timer **null**, nessun fetch residuo, radar 25 slot;
  - **nessun 404** e **nessuna query-string** sulle richieste nowcast;
  - **0 eccezioni JS**.

## Conteggio funzioni

**1388** dichiarazioni `function` (misurate; +11 rispetto alle 1377 della
1.3.0.79).

## Limiti dichiarati

- Il test del timer usa il **tick reale** (`nowcastAutoTick`) invocato dal test e
  verifica che l'intervallo di 5 minuti esista (`refreshTimer`), senza attendere
  5 minuti reali.
- La verifica browser è headless, con rete esterna bloccata tranne le CDN di
  Leaflet.
- La visibilità della pagina è forzata nel test (headless) per provare il gate
  `document.hidden`.
