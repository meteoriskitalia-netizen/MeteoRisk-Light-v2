# MeteoRisk Light 1.3.0.74 — campo centraline: bordo, neve, radar

Bump da **1.3.0.73** (BUILD 204 → **205**). Nessuna modifica al backend.
APP_FILE: `mri-light-1.3.0.74.html`.

## 1. Maschera terra quasi netta (costa e confini)

Il feather della 1.3.0.70 (dilatazione 2 px + box blur 5×5) produceva ~7 px di
gradiente sul raster 256×227, cioè ~15–20 px sullo schermo dopo l'ingrandimento
del browser: il colore sembrava sconfinare oltre la costa.

Ora: terra alpha 1, unico mare colorato l'anello di **1 px** (box blur 3×3),
oltre 1 px l'alpha è **esattamente 0**. Il feather è applicato **una volta**
(prima era soft² fra raster e downscale).

Misura (`scripts/validation/measure_station_field_13074.mjs`, maschera province
even-odd, frame 256×227 36–48N 6–19E):

| Area | pixel di mare con alpha>0 | a ≥2 px dalla costa | distanza max |
| :--- | ---: | ---: | ---: |
| Costa Liguria/Toscana, 1.3.0.73 | 714 / 2.077 (34,38%) | 481 | 4 px |
| Costa Liguria/Toscana, 1.3.0.74 | **233 / 2.077 (11,22%)** | **0** | **1 px** |
| Tutta Italia, 1.3.0.73 | 7.085 | 5.079 | 4 px |
| Tutta Italia, 1.3.0.74 | **2.006** | **0** | **1 px** |
| Confine sintetico, 1.3.0.73 | 1.448 | 1.085 | 4 px |
| Confine sintetico, 1.3.0.74 | **363** | **0** | **1 px** |

Larghezza media del gradiente al confine sintetico: 8,58 → **1,59 px** (max 9 → 2).
Alpha medio dell'anello a 1 px: 198/255 (una sola rampa, poi trasparente).
Sul raster finale con le 3.498 stazioni reali di temperatura (ss=2 + downscale):
1.3.0.73 dipinge mare fino a 3 px (862 px a ≥2 px), 1.3.0.74 **0 px a ≥2 px**.

## 2. Scala neve dedicata in cm

La rampa era quella di `snow_depth` (in **metri**) moltiplicata ×100: sotto i
50 cm tutte le bande cadevano su un verde quasi identico. Nuova scala a 10
bande in **cm** (bianco-azzurro → blu → viola), 0 cm trasparente:

| cm | RGB |
| ---: | :--- |
| 0 | trasparente (alpha 0) |
| 0,5 | 224,242,254 |
| 1 | 186,230,253 |
| 5 | 125,211,252 |
| 10 | 56,189,248 |
| 25 | 14,165,233 |
| 50 | 37,99,235 |
| 100 | 76,29,149 |
| 200 | 124,58,237 |
| 300 | 162,28,175 |

Distanza RGB massima fra 10 e 100 cm: **160** (canale verde) — richiesto > 60.
Legenda: tacche 0/1/5/10/25/50/100/200/300, etichette orizzontali.

## 3. Radar nascosto dal campo centraline

Con il campo acceso il pane `radarPane` va in `display: none`: le precipitazioni
radar spariscono. Il toggle radar **non** viene forzato (resta dello utente).
Spegnendo il campo il radar torna visibile **solo se era visibile prima**.
Gli altri layer (satellite, fulmini, tracce, AFA) non si toccano. Riga di stato:
«radar nascosto dal campo centraline».

## Test

- `scripts/tests/test_station_field.mjs`: **238 controlli / 0 falliti**
  (nuovi: feather 1 px e 0 oltre 2 px; quattro colori neve distinti e distanza
  10–100 cm; soppressione/ripristino del radar su map finta).
- `test_html_js_syntax.py` + `test_tooltips_coverage.py`: **3 passed**.
- `test_sw_cache_policy.mjs`: **PASS** con `CACHE_NAME = 'mri-1.3.0.74'`.

## Non fatto / dichiarato

- Nessuno screenshot in browser: le misure sono sul raster e sulla maschera,
  non sui pixel dello schermo.
- La riga del metodo del campo dice ancora «gaussiano» (etichetta della 1.3.0.72),
  non toccata in questa release.
