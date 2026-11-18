# Consegna — produttore `data/radar/synoptic.json` (MeteoRisk-Light-v2)

**Nessun push, nessun commit, nessuna modifica a `main`.**
Cartella: `C:\Users\aless\Desktop\meteorisk\work_13\synoptic_prod\`

Ho creato SOLO nuovi file in `scripts/synoptic/`, un nuovo test in
`scripts/tests/synoptic/` e un nuovo workflow `synoptic.yml`. **Non ho toccato**
HTML 1.3.0.8, VERSION, sw.js, moduli `radar_engine`, workflow esistenti.

## 1. Consegne (MISURATE — sha256 completi in `MANIFEST.sha256`)

| File (path repo) | byte | sha256 (prime 16) |
| :--- | ---: | :--- |
| `scripts/synoptic/build_synoptic.py` | 9.118 | `86B42D7145E0B2B3…` |
| `scripts/synoptic/tm90.py` | 5.047 | `911F7C281E773C6F…` |
| `scripts/synoptic/raw_fields.py` | 5.360 | `0C8F19C0D57A7D09…` |
| `scripts/synoptic/grid.py` | 2.297 | `84A8D19FB36846ED…` |
| `scripts/synoptic/om_http.py` | 3.683 | `749A8D37FC889319…` |
| `scripts/synoptic/make_fixture.py` | 3.690 | `C7980F5FC248DF26…` |
| `scripts/synoptic/__init__.py` | 268 | `F4572CB027235081…` |
| `scripts/synoptic/README.md` | 5.156 | `90D7EAD4ACD8B6F9…` |
| `scripts/tests/synoptic/test_build_synoptic.py` | 9.616 | `E5CC890BE43AB04F…` |
| `.github/workflows/synoptic.yml` | 4.278 | `AE7F974A015F0A6D…` |
| `verify_client_gate.cjs` | 5.977 | `0F42EAC49E789B1A…` |
| `example_synoptic.json` | **8.566** | `8820720C28FDD65E…` |
| `fixtures/ecmwf_ifs025_block_reduced.om` | 270.568 | `962E9D7700247506…` |
| `fixtures/ncep_gfs025_reduced.om` | 194.000 | `997215925FDA95DE…` |
| `fixtures/ecmwf_ifs025_2026-11-17T1200.om` (sorgente reale) | 5.262.704 | `70C0E81745546327…` |
| `fixtures/ncep_gfs025_2026-11-17T1800.om` (sorgente reale) | 3.637.696 | `D64AE831AAA1F716…` |

Totale del payload di consegna (21 file, escluso `MANIFEST.sha256` che non puo'
contenere il proprio hash): si legga il manifest. Il sottoinsieme stabile dei
**20 file di prodotto** (esclusi REPORT.md e MANIFEST.sha256) e'
**9.431.648 byte**. Sha256 completi a 64 cifre in `MANIFEST.sha256`.

## 2. JSON di esempio (MISURATO)

`example_synoptic.json`: **8.566 byte**, **5 feature** — **1 `block`** +
**4 `raw_field`** (`raw_t850_gradient`, `raw_t500_cold`, `raw_div850`, `raw_rh850`).
Generato da `fixtures/latest_block_reduced.json` + `fixtures/ecmwf_ifs025_block_reduced.om`,
`--generated-at 2026-11-17T12:00:00Z`. LF, nessun BOM.
`availability.horizon_exhausted = true` perche' la fixture ridotta ha un solo frame
(il produttore non ha riempito nulla).

## 3. Verifiche eseguite (comandi reali, non ereditate)

| Verifica | Comando | Esito |
| :--- | :--- | :--- |
| Test prodotto | `python -m pytest scripts/tests/synoptic -q --capture=sys` | **12 passed** |
| Gate client | `node verify_client_gate.cjs` | **18 PASS / 0 FAIL** |
| YAML workflow | `yaml.safe_load` | OK — `on: {schedule: [23 12 * * *], workflow_dispatch}` |
| Sintassi Python | `py_compile` ricorsivo su `scripts/` | **8/8 OK** |
| Sintassi JS | `node --check verify_client_gate.cjs` | OK |

Il gate client estrae il modulo **REALE dall'HTML di release 1.3.0.8 pubblicato**
(`releases/MeteoRisk-Light-1.3.0.8-…/mri-light-1.3.0.8.html`) e gli passa
l'`example_synoptic.json` prodotto. Verifica: (a) il prodotto emette solo `block`/
`raw_field`; (b) ogni `raw_field` e' disegnato dal client con il solo grigio neutro
`#94a3b8` — zero semicerchi, triangoli, `H`, `B`, `C`, frecce di convergenza o banda
umida; (c) il `block` disegna SOLO il riquadro `B` validato (`#a78bfa`); (d) nessuna
label `burian` -> pannello burian nascosto.

**Conclusione del gate: il client mostra SOLO il kind validato (`block`).** Le feature
non validate non vengono inventate dal produttore e il client riceve solo
`raw_field`, che non e' un simbolo meteorologico. Non mi sono fermato perche' la
condizione richiesta ("il client mostri solo i kind validati") e' rispettata.

## 4. Misure scientifiche su dati REALI

- **TM90** su `ecmwf_ifs025`, frame reale `2026-11-17T1200.om` (5.262.704 byte,
  sha256 `70C0E817…`): **3 segmenti di blocco**. Primo segmento: 172 longitudini,
  `delta=0`, `GHGS=+4,7 m/grado`, `GHGN=-26,75 m/grado` (soglie citate `>0` / `<-10`).
  Nessuna soglia aggiuntiva (estensione, persistenza) applicata.
- **Frame ncep_gfs025** `2026-11-17T1800.om` (3.637.696 byte): array Z500 reale
  `(281,1440)`, min `4915,0` / max `5935,0` m. **0 blocchi** quel giorno: il
  produttore non ne inventa.
- **Shim HTTP**: `OmFileReader.from_fsspec` con `RangeHttpFS`, **11 richieste Range**
  per leggere Z500 remoto; array identico alla lettura locale. L'adapter fsspec di
  serie fallisce su questo host (`HTTPFileSystem._cat_file() missing 'url'`) — per
  questo esiste lo shim.
- **Fixture ridotte**: crop reale lat 35–85N, lon −180..−100E (ECMWF, 0,5°, 161×101,
  270.568 byte) e lon −20..60E (GFS, 0,5°, 161×101, 194.000 byte). Nessun valore
  generato: solo crop/decimazione di frame pubblicati.

## 5. Cosa NON verificato (dichiarato)

1. **Nessun run end-to-end di `synoptic.yml` su GitHub Actions**: nessun push. Il
   workflow e' nuovo e non e' mai stato eseguito.
2. **Sintassi bash dei blocchi `run:`**: solo `yaml.safe_load`; ShellCheck/bash non
   disponibili su questo host. Le righe `run:` sono state riviste a mano.
3. **Resa visiva del pannello nel browser interno** con il JSON reale: il gate e'
   statico/su vm, non un rendering a schermo.
4. **Il dispatch di `deploy-pages.yml`** dal nuovo workflow non e' stato provato
   (richiede push e permessi `actions: write` su main).
5. **TM90 non validata su eventi italiani**: e' una soglia citata (Tibaldi–Molteni
   1990), non tarata qui; nessuna affermazione di accuratezza.
6. **Copertura/finestra dei 7 domini** non ri-misurata in questa sessione: il codice
   usa i valori consegnati (170h/350h/263h/374h; buchi 23h/44h/92h) e dichiara
   `horizon_exhausted` confrontando l'ultimo frame del manifest con `ref+168h`.
7. **Nessuna verifica che `deploy-pages.yml` copi il JSON**: ho verificato solo che
   `data/radar` viene copiato in `_site/data/radar` (riga 113 del workflow di release).

## 6. Note di sicurezza

Nessun token/PAT scritto su disco. Il workflow usa solo `${{ github.token }}`
implicito per il commit e il dispatch. Nessun push, nessun commit, nessuna modifica
a file esistenti del repository.
