# MeteoRisk Light 1.3.0.75 — neve onesta, AFA regione e pulsante neutro

Bump da **1.3.0.74** (BUILD 205 → **206**). Nessuna modifica al backend.
APP_FILE: `mri-light-1.3.0.75.html`.

## 1. Neve: il campo non copre dove non c'è neve

Il campo interpolava la neve con il raggio generale (25 km) e nei metodi
`full`/`contour` **senza limite**, fra stazioni lontane: da 0 a 25 cm colorava
l'intera penisola.

Ora:

- **raggio dedicato 10 km** (`STATION_FIELD_SNOW_CUTOFF_KM`) applicato in **tutti**
  i metodi, compresi `full`/`contour`: oltre 10 km il pixel resta **trasparente**
  anche con «copertura totale» (la neve è discontinua, riempirla è inventarla);
- **dissolvenza dedicata 2 km** (`STATION_FIELD_SNOW_FADE_KM`): piena fino a 8 km,
  spenta a 10;
- **scala**: sotto **1 cm è trasparente** (0,5 cm compreso), poi
  **1 / 5 / 10 / 25 / 50 / 100 / 200 / 300 cm**. 1 cm = `#bae6fd` (186,230,253),
  25 cm = `#0ea5e9` (14,165,233): distanza RGB massima **172** (> 60 richiesto).

Misura (`scripts/validation/measure_station_field_13075.mjs`, `data/stations/latest.json`
del **2026-11-24T16:20:35Z**, **146** stazioni con `snow_depth_m > 0`, frame Italia
256×227 36–48N 6–19E, maschera province even-odd, raster ss=2 + downscale):

| metodo | pixel dipinti 1.3.0.74 | pixel dipinti 1.3.0.75 | terra coperta | Sicilia dipinta |
| :--- | ---: | ---: | :--- | ---: |
| `local` | 4.252 | **1.620** (−61,9%) | 31,80% → **11,76%** | 0 → **0** |
| `full` | 14.359 | **1.620** (−88,7%) | 100% → **11,76%** | 1.140 → **0** |

## 2. `#afa-region-it` non sembra più «accesa»

I pulsanti AFA Italia/Europa usavano la classe **`.on`** (viola, `#2e1065` /
`#a855f7`), lo stesso stile dei toggle accesi: la regione selezionata sembrava un
layer attivo mentre `#btn-radar-afa` diceva OFF.

Ora hanno la classe **`.afa-region-btn.is-selected`** (bordo chiaro `#cbd5e1`,
fondo neutro `#334155`, box-shadow interna chiara), distinta sia dal viola `.on`
sia dal verde dei toggle. `afaSyncTitles()` aggiorna `is-selected`, non più `on`.

In più il selettore **`#afa-region-switch` è visibile solo con l'AFA accesa**:
`display:none` a layer spento, `inline-flex` quando `liveAfaSyncButtons(true)`.
Prima restava a schermo, selezionato, per un layer spento.

## 3. `#btn-radar-afa` era viola

**Causa misurata**: un `border-color:#a855f7` **inline** nel markup del pulsante
(nessuna regola di stato, nessuna classe `active`/`:focus`). Rimosso.

Nuove regole:

- OFF → bordo **`#334155`** (`rgb(51,65,85)`), fondo e testo di `.tool-btn` (neutro);
- ON → bordo **`#22c55e`** (`rgb(34,197,94)`), fondo `#14532d`, testo `#dcfce7`,
  come gli altri toggle attivi.

## Verifica

- `scripts/tests/test_station_field.mjs`: **247 controlli / 0 falliti**
  (nuovi: 0 / 0,5 / 0,9 cm trasparenti, 1 cm dipinto, 1 vs 25 cm RGB 172;
  per `local`, `full` e `contour` centro e ~6 km dipinti e ~13 km alpha 0;
  controllo che in `full` la temperatura invece dipinga a ~13 km; raggio 10/2
  dichiarato; riga di metodo «entro 10 km»).
- `test_html_js_syntax.py` + `test_tooltips_coverage.py`: **3 passed**.
- `test_sw_cache_policy.mjs`: **PASS** con `CACHE_NAME = 'mri-1.3.0.75'`.
- **Browser headless** (`scripts/validation/verify_ui_13075.mjs`, Chrome 1243,
  DOM reale, rete esterna bloccata): **13/13 PASS**.
  AFA OFF bordo `rgb(51,65,85)` e nessun border inline; AFA con classe `on`
  bordo `rgb(34,197,94)`; regione Italia bordo `rgb(203,213,225)` e fondo
  `rgb(51,65,85)`, diversi da `.sc-toggle.on` (misurato `rgb(168,85,247)` /
  `rgb(46,16,101)`); selettore regione `none` a OFF, `flex` a ON, `none` di nuovo
  a OFF.

## Non fatto / dichiarato

- Nessuno screenshot del campo neve su schermo: la misura è sul raster finale
  (alpha dopo ss=2 + downscale), non sui pixel del monitor.
- Il raggio di 10 km e la soglia di 1 cm sono **scelte ingegneristiche
  dichiarate**, non una taratura: non esiste una verifica contro neve osservata.
