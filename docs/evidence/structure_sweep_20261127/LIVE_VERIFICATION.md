# Verifica live 1.3.0.87 — 2026-11-27

## Push

- Commit su `main`: `3ef797519b6e887b38d654f96b2c7c0cd1a9caf8` (un commit,
  `force:false`; il primo tentativo e' stato rifiutato con 422 perché `main`
  era avanzato, il retry ha usato la base nuova `31e89517a3ae`).
- Blob HTML pushato: `21c9f32f551ed88633ef50eb43dbe67d04825d3e`,
  **3.475.972 byte**, sha256 **`D98DA7ABE3321E84D41276EF5CCB8069FAF095954EBBC6D4AE81B5CA184F8DFA`**.
- `VERSION` live: `1.3.0.87`, `BUILD=227`, `APP_FILE=mri-light-1.3.0.87.html`.
- `mri-light-1.3.0.86.html` su `main`: **HTTP 404** (rimossa).

## Pages

- Run `deploy-pages.yml` **`46056645962`**, `conclusion: success`, head
  `775527c` (main avanzato dopo il push).
- Sito servito: `index.html` **3.475.966 byte**, sha256
  **`7E1F137B2049BC550692B6A911D524A44DDCC54A8A55FF33891A553B646196D5`**,
  `const APP_VERSION = '1.3.0.87'`, `const APP_VERSION = '1.3.0.86'` assente,
  `scMorphologyClass` e `scBuildOutlinePolygons` presenti.
- Delta dal blob pushato: **6 byte**. Un commit concorrente dello stesso
  giorno (nowcast clouds, sync del lead) ha modificato il client sopra il
  nostro push; il diff e' limitato alle funzioni `nowcastClouds*` e a una
  stringa del player, le funzioni della struttura convettiva sono intatte.
  Il numero da citare per il **sito** e' `7E1F137B...`; per il **blob
  pushato** e' `D98DA7AB...`.

## Dati radar live

Fonte: `raw.githubusercontent.com/.../main/data/radar/convective_structures.json`,
timestamp radar **2026-11-27T19:05:00Z** (run del nuovo codice; il run e'
stato cancellato dalla concorrenza dopo aver pubblicato i dati).

| controllo | valore |
| :-- | --: |
| strutture | 4 |
| `outline.type = MultiPolygon` | **4 / 4** |
| `outline_core_dbz = 35` | **4 / 4** |
| `morphology_class.hysteresis.frames = 3` | **4 / 4** |
| `last_observed` presente | **4 / 4** |
| `summary.structure_candidates` | 4 |
| `summary.skipped_incoherent` | 1 |
| `summary.short_track_published` | 0 (slot senza track corte) |
| `latest.advanced_morphology_hysteresis_frames` | **3** |
| `latest.advanced_morphology_class_reachable` | **SINGLE_CELL, MULTICELL, SQUALL_LINE** |
| classi pubblicate | SINGLE_CELL 4, MULTICELL 1 |

## Procedura di deploy

I run `deploy-pages` venivano cancellati dalla concorrenza dei run
`radar-engine` (ogni 10 minuti). Per il deploy sono stati **disabilitati per
pochi minuti** i workflow `radar-engine.yml` (id `390783398`) e
`update-weather-data.yml` (id `390783405`), poi riabilitati subito dopo la
verifica. Nessuna modifica ai workflow e' stata pushatta: disabilitazione e
riabilitazione sono state fatte via API e sono reversibili.

## Cosa NON e' stato verificato live

- Il fix delle **track corte** non ha trovato un caso nel slot 19:05
  (`short_track_published=0`). E' verificato nel replay di prodotto
  `2026-07-18T19:50Z`: 3 strutture, 2 track corte, LIML dentro 2.
- Il POD di prodotto sugli altri 7 eventi finali non e' stato ri-eseguito
  end-to-end: la tabella POD e' a livello di harness.
