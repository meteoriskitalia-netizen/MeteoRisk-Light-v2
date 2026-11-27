# MeteoRisk Light 1.3.0.87 (BUILD 227)

Data: 2026-11-27. Bump da 1.3.0.86. App: `mri-light-1.3.0.87.html`.

## Cosa cambia

### Struttura convettiva: geometria a nuclei (MultiPolygon)
Il contorno pubblicato non e' piu' l'inviluppo 20 dBZ (che copriva il
41-63% del dominio radar) e non e' un solo nucleo scelto dal centroide
(che perdeva le celle severe lontane). Sono **tutti i nuclei >= 35 dBZ**,
in un MultiPolygon con cap 8, senza buffer. Il client disegna ogni
poligono. Fallback dichiarato: una componente dell'inviluppo se nessun
nucleo esiste.

Misure su 15 eventi METAR/SPECI A/B (6 giornate di screening + 8 finali,
archivio DPC dal 2026-05-20):

| metrica | baseline 1.3.0.86 | 1.3.0.87 |
| :-- | --: | --: |
| POD screening (7 eventi) | 4/7 = 0,571 | **4/7 = 0,571** |
| POD finale (8 eventi) | 3/8 = 0,375 | **3/8 = 0,375** |
| area severa mediana, screening | 43,97% | **7,33%** |
| area severa mediana, finale | 41,93% | **3,53%** |
| area severa massima, screening | 63,1% | **10,8%** |
| area severa massima, finale | 52,6% | **10,7%** |
| transizioni di classe / frame, screening | 0,121 | **0,035** |
| transizioni di classe / frame, finale | 0,131 | **0,037** |

Lo sweep completo e' in `docs/evidence/structure_sweep_20261127/`
(dichiarazione delle giornate prima dei risultati, 48 combinazioni di
screening, 44 di raffinamento, shortlist finale).

### Classi stabili
Isteresi di 3 frame consecutivi (`advanced.morphology_hysteresis_frames`).
`morphology_class.value` e' la classe stabile; `raw_value` e la sequenza
restano per audit in `morphology_class.hysteresis`.

### Classe morfologica visibile
Il pannello «Struttura convettiva» mostra la classe (Cella singola /
Multicella / Linea di groppo) con i frame di stabilita'. Prima era
pubblicata nel JSON ma non mostrata. L'elongazione e' la PCA del poligono
pubblicato, non il bbox.

### Pubblicazione delle track corte
Una track con meno di 4 punti ma **senza salti** oltre il gate 110 km/h non
viene piu' scartata: si pubblica con `track_coherent=false`,
`motion_reliable=false` e `forecast=null`. Restano scartate solo le track con
un salto impossibile. `summary.short_track_published` dichiara quante.

Misura di prodotto (replay `2026-07-18T19:50Z`): prima **1 struttura** e LIML
fuori; dopo **3 strutture** (2 track corte), LIML dentro 2, area 931/972 km2
contro inviluppo 5.031/3.774 km2. Sul caso LIML il prodotto passa da miss a
hit. Il POD 3/8 della tabella sweep e' a livello di harness e non include
questo fix; gli altri 7 eventi finali non sono stati ri-eseguiti end-to-end.

### Ultima osservazione, non coasted
Il contorno si risolve su `last_observed`, non sull'ultimo punto coasted
(che non ha `cell_id`/`storm_object_id`). Corregge il caso LIML
2026-07-18T19:50Z e riduce i contorni mancanti. `observed_points` e
`coasted_points` dichiarano la composizione della track.

## Cosa NON e' stato fatto (dichiarato)

* `BOW_ECHO`: `bow_score` non e' calcolato. Resta non producibile.
* `MCS`: richiede durata >= 60 min ed echo top >= 6000 m. Con la finestra
  a 6 frame (25 min) e' strutturalmente irraggiungibile. Non viene
  inventato un MCS 2D.
* `SUPERCELL_CANDIDATE` / `HP_SUPERCELL_CANDIDATE`: richiedono VRADH
  persistente; la VMI 2D non la contiene.
* 1 contorno su 35 resta `cell_mask_not_resolved` (difetto pre-esistente).
* Il POD finale e' 3/8: la geometria non migliora il POD, migliora area e
  stabilita'. Non e' una validazione statistica (15 eventi totali).

## Verifica live (2026-11-27)

- Commit `3ef7975` su `main`; sito **1.3.0.87**, 3.475.966 byte, sha256
  `7E1F137B...196D5`; `scMorphologyClass` e `scBuildOutlinePolygons` presenti.
- Dati radar live (2026-11-27T19:05Z): **4 strutture, 4/4 MultiPolygon, 4/4
  core_dbz 35, 4/4 isteresi 3, 4/4 last_observed**; `latest.json` con
  `advanced_morphology_hysteresis_frames=3` e classi raggiungibili.
- Dettaglio e caveat (delta di 6 byte dal blob pushato, run cancellati dalla
  concorrenza) in `docs/evidence/structure_sweep_20261127/LIVE_VERIFICATION.md`.

## Verifica

* `pytest scripts/tests/radar_engine -q --capture=sys`: 1.010 passed,
  1 schema esatto aggiornato ai 9 campi nuovi.
* `pytest scripts/tests/test_html_js_syntax.py`: 1 passed.
* `node scripts/tests/test_sw_cache_policy.mjs`: PASS.
* `node scripts/tests/test_structure_ui.mjs`: PASS.
* Replay reale `--as-of 2026-07-01T13:25:00Z`: 35 strutture, 34/35
  MultiPolygon, area contorni 20.755 km2 contro 113.796 km2 di inviluppo,
  isteresi presente su 35/35.
