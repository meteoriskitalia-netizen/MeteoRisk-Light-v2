# MeteoRisk Light — dashboard meteorologica statica-first

Dashboard meteorologica statica-first per l'Italia: radar delle tempeste (DPC),
satellite EUMETSAT e dati Open-Meteo, servita interamente da GitHub Pages — nessun
back-end applicativo, nessuna dipendenza runtime oltre agli asset statici.

Repository: https://github.com/meteoriskitalia-netizen/MeteoRisk-Light-v2

## Versione attiva: 1.2.4.20

- File applicativo: `mri-light-1.2.4.20.html` (`APP_VERSION = '1.2.4.20'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.20', date: '2026-10-24' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.20'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.20 (release sopra la 1.2.4.19) - **replay deterministico del radar engine,
  nessuna funzionalita' nuova dell'app**:
  - **`--as-of <ISO8601 UTC>`** (`scripts/radar_engine/main.py`): un unico clock iniettato
    (`scripts/radar_engine/clock.py`) ancora a un istante **tutte** le letture dell'orologio
    che decidevano quali dati scaricare: slot radar, VIL/ETM/POH, IR_108, ambiente e
    fulmini. L'ancora e' l'istante arrotondato per difetto al passo del prodotto
    (`source.period_s = 300`); i 6 frame sono `T0..T-25` come nel run operativo.
  - **Onesta' in replay**: i dati senza archivio storico **non** vengono presi da oggi.
    Ambiente dall'API di **archivio** Open-Meteo, con gate esplicito sulle variabili di
    profilo: se mancano -> `null` con `environment_unavailable_for_as_of`. Fulmini ->
    `null` con `lightning_history_unavailable`; IR_108 -> `null` con `ir108_history_unavailable`;
    MESH -> `null` con `thermal_levels_missing` (dipende dalle quote termiche). Riepilogo
    `as_of_mode {requested, environment_source, degraded[]}` in `latest.json` e
    `convective_structures.json`, **solo** in replay.
  - **Determinismo misurato**: due run con lo stesso `--as-of` danno i sei file di output
    **byte-identici** (sha256 in `docs/REPLAY_AS_OF.md`): `generated_at` e' ancorato
    all'istante richiesto, i tempi di parete sono `null`, registro predizioni e archivio
    rolling sono saltati (nessuno stato scritto).
  - **Collaudo di falsificazione** (`scripts/validation/asof_bench.py`, una volta): 6 eventi
    grandine METAR QC A/B -> **POD 0/6 = 0,000** (Wilson 95% [0,000, 0,390]), CSI 0,000,
    **69** allarmi non accoppiati; **FAR non stimabile** (il METAR copre 14 stazioni =
    1,46% del territorio). L'esito **non e' informativo** (5 esiti su 7 restano compatibili
    con l'attesa dichiarata POD 0,10-0,30). Soglie overhang 45/50 dBZ **invariate**.
  - **Documentazione**: `docs/REPLAY_AS_OF.md` (cosa e' riproducibile, cosa no, tabella dei
    `null_reason`, verifica grep con `file:riga`, limiti dichiarati).
  - **Test**: `scripts/tests/radar_engine/test_as_of_replay.py` (21 casi: ancoraggio delle
    5 sorgenti, degradazione dichiarata, determinismo, CLI).
- Contenuto della 1.2.4.19 (release sopra la 1.2.4.18) - **solo riparazioni, nessuna
  funzionalita' nuova dell'app**:
  - **Storico delle versioni ripristinato** (`VERSION`): la 1.2.4.18 andava da `BUILD=18` a
    `BUILD=16` perche' il blocco della 1.2.4.17 era stato **sostituito** invece di anteposto
    (74.274 -> 72.964 byte). Il blocco 1.2.4.17 e' stato reincollato **verbatim** dal `VERSION`
    della release 1.2.4.17 (titolo, MAJOR/MINOR/PATCH, `BUILD=17`, `RELEASE_TYPE`,
    `DATE=2026-10-23`, `NOTE` integrale, `APP_FILE`) e l'ordine dei blocchi e' ora 19, 18, 17,
    16, 15, ... (28 blocchi; `BUILD=19/18/17/16` tutti presenti e in quest'ordine).
  - **File di validazione consegnati** (esistevano solo nell'albero di lavoro, in nessuna
    release e su nessun branch): `scripts/validation/build_labels.py` (etichette di eventi
    severi dai bollettini METAR/SPECI italiani: grandine `GR`/`GS`, tornado `+FC`, imbuto
    `FC`/`VCFC`, raffica >= 25 m/s, temporale `TS`; QC proprio A/B/C, merge temporale,
    matching con le celle a tre esiti distinti `matched` / `no_cell_within_gate` /
    `no_snapshot_in_window`), `scripts/validation/power_analysis.py` (numerosita' minima di
    eventi severi perche' l'intervallo di Wilson al 95% abbia una semi-larghezza dichiarata,
    `p = 0,5` come caso peggiore) e `docs/VALIDAZIONE_SEVERO.md` (licenze, dataset, potere
    statistico, protocollo). Nessun dato ESWD scaricato, costruito o pubblicato: la licenza
    ESWD vieta pubblicazione e trasmissione dei dati grezzi e dei derivati di scarso valore
    aggiunto (penale contrattuale fino a 60.000 EUR); il documento dichiara il **METAR come
    fonte di verita'** e che i termini ISD/NODD e la policy WMO sul riuso dei messaggi di
    scambio **non sono stati verificati**.
  - **`AGENTS.md` riallineato ai valori misurati**: per l'html della 1.2.4.18 dichiarava
    2.048.467 byte e `905607B4DDF00BE8...`, mentre il disco dice **2.048.782 byte** e
    **`3A442845F14125B6...7E9ADAC0`** (lo stesso valore `rel` del manifest di upload della
    1.2.4.18); la sezione ora descrive la versione attiva 1.2.4.19 con gli hash misurati.
  - **Bump**: html rinominato `mri-light-1.2.4.19.html`, `APP_VERSION` / `APP_RELEASE_DATE` /
    `APP_CHANGELOG` aggiornati in-app, `sw.js` con `CACHE_NAME = 'mri-1.2.4.19'`, righe `cp`
    dei 3 workflow che costruiscono `_site` e i 24 test `.mjs` riallineati in **entrambe** le
    forme (letterale e pattern con i punti escapati: sostituire solo la prima lascia 4 test
    rossi).
- Contenuto della 1.2.4.18 (release precedente, sopra la 1.2.4.17) - **stack severo con etichette oneste**:
  - **S1 ambiente** (`scripts/radar_engine/severe_stack.py`): CAPE/CIN, shear 0-6 km (proxy
    10 m / 500 hPa dichiarato), SRH 0-3 km **solo** se ci sono almeno 4 livelli di vento e uno
    sopra i 2 km (altrimenti `srh: null` con `null_reason`), LCL e WMAXSHEAR. Riferimento
    europeo Pucik et al. 2015 (MWR 143:4805) citato **come riferimento**, mai come soglia locale.
  - **S2 grandine**: VIL density da `VIL`/`ETM` (Amburn & Wolf 1997, soglia 3,5 g/m3 riportata
    come citazione bibliografica, non tarata qui) e MESH (Witt et al. 1998) **solo** con >= 4
    livelli CAPPI: con i 2 livelli configurati (CAPPI_2, CAPPI_6) il campo esce `null` con
    `mesh_unavailable_reason = "cappi_levels<4"`. Nessun "MESH-like" derivato dal VIL.
  - **S3 overshooting top**: riuso di `phase2/satellite_ot.py` (Bedka et al. 2010) con booleano,
    confidence, fonte e timestamp del frame IR.
  - **`combine()`**: verdetto unico dichiarativo con `rotation_measurable: false` e
    `probability: null` + motivo (nessuna etichetta ESWD: nessuna probabilita' calibrata).
  - **Terminologia e artefatti**: `supercell.py` -> `convective_structure.py` (alias di import
    mantenuto), indice `ssi` -> `soi` (`structural_organization_index`), livelli
    `organized / strong / intense`, `data/radar/supercells.json` ->
    `data/radar/convective_structures.json` (chiave `structures`, `structure_id`).
  - **Dedup** cella/storm object (un record per sistema, `merged_from`), fix
    `latest_frame_index` e `status`, `confidence` **derivata** (gate + persistenza + copertura,
    formula nel docstring) al posto della costante `"low"`.
  - **Fulmini**: backoff allineato al modulo (12 passi) e stop dopo 3 errori consecutivi,
    applicato **solo oltre il grace di 9 passi** di ritardo di pubblicazione misurato — da 37
    richieste HTTP 403 per run a 12, un tentativo per slot (fermarsi a 3 azzererebbe il layer,
    perché il primo slot utile arriva tipicamente a k=9).
  - **Registro predizioni**: `data/state/severe_predictions.jsonl` in append con rotazione a
    30 giorni (`ts, cell_id, lat, lon, soi, vil_density, mesh, ot, environment, version`).
## Versione precedente: 1.2.4.16

- Contenuto della 1.2.4.16 (release sopra la 1.2.4.15):
  - **Polo senza pixel "spalmati"** (`fmPolarCapMean` / `fmPolarCapColor`). Due precisazioni
    verificate il 2026-10-23, perche' cambiano la diagnosi rispetto alla 1.2.4.15: (1) la vista
    polare usa SOLO il WebP pre-renderizzato (`fmPolarSetActive` e' attiva solo quando
    `fmWebpFrameUrl()` esiste) e **non esiste alcun painter del frame `.om` in proiezione
    polare**, quindi non c'e' un secondo ramo con il clamp a 85 da correggere; (2) il frame
    `.om` **non contiene dato sopra 85,05 N**: la `bbox` del manifest dice 20..90 ma l'array
    valori del file ha 262 righe a 0,25 gradi (20..85,05; 522 righe a 0,125 per `dwd_icon`) e
    `getValueFromLatLong` a 86/88/90 N restituisce `NaN` sia su `ncep_gfs025` sia su `dwd_icon`,
    quindi "campionare la latitudine vera dal `.om`" e' impraticabile: il dato non esiste.
    Il residuo visibile era il cap: la dissolvenza lineare teneva l'azimuth della riga 0 (con le
    etichette bianche e le frecce scure disegnate dentro il WebP) al 40-60% per quasi tutto il
    cap, e il colore del capolo era la **media**, trascinata dalle etichette. Ora il capolo e'
    la **mediana per canale** dei pixel opachi della riga 0 (helper pura `fmPolarMedian`,
    cacheata su `fmPolarFrame.pixels`) e la dissolvenza e' **cubica** `w0 = (1 - t)^3`:
    continuita' esatta sul bordo (t = 0), 0,51 a un grado, 0,22 a due gradi, 0,06 a tre gradi.
    Misura dell'anello attorno al polo (720 campioni per raggio, frame `ncep_gfs025`
    `pressure_msl` 2026-10-23T00Z): deviazione standard angolare 10,66 -> 2,99 a r = 2 px
    (lat ~89 N), 11,37 -> 6,47 a r = 4, 11,45 -> 9,56 a r = 6; colori distinti 7 -> 13 a r = 2.
  - **Zone a rischio nascoste all'apertura delle mappe previsionali**: `fmRiskOverlayPlan`
    (pura), `fmRiskOverlayCandidates`, `fmHideRiskOverlays` e `fmRestoreRiskOverlays`,
    richiamate da `toggleForecastMaps()`. Lo stato e' **per layer** (visibile/nascosto), non un
    booleano: si nascondono `provinceGeoJsonLayer` (107 poligoni, pane `tilePane`, riempiti da
    `colorForMetric` e **ricolorati** dalla modalita' DPC con `DPC_ALERT_COLORS`), le voci di
    `provinceLayerMap` e `continuousOverlay` (immagine della vista continua, `overlayPane`
    z 400, **sopra** il pane delle mappe a z 360). `borderOverlayLayer` (bordi ISTAT, z 410)
    resta visibile: non porta colore di rischio ed e' l'unico disegnatore dei confini italiani
    (la geografia Natural Earth delle mappe esclude i confini con l'Italia). Se l'utente aveva
    le zone spente, alla chiusura restano spente.
  - **Reset del player al cambio modello/variabile/run**: `fmPlayerResetState` (pura) +
    `fmResetPlayerForModelChange`, agganciata a `fmDomainChanged`, `fmVariableChanged` e
    `fmRunChanged`. Ferma la riproduzione (bottone ▶), riporta `fm-slider` a `0`, azzera
    contatore e data/ora, invalida il frame in volo (`fmWebpGeneration`) e le cache di
    prefetch/decode del modello precedente (`fmStopPreload`, `fmStopWarm`,
    `fmWebpDecodedImages`), toglie i layer e il frame polare vecchi e mostra il loader finche'
    il primo frame del nuovo modello non e' pronto.
  - **Test**: nuovi `scripts/tests/test_fm_polar_cap_fade.mjs` (mediana robusta, dissolvenza
    cubica, assenza di clamp a 85 nel painter, assenza del ramo `.om` polare),
    `scripts/tests/test_fm_risk_overlay_state.mjs` (`fmRiskOverlayPlan` per-layer) e
    `scripts/tests/test_fm_player_reset.mjs` (`fmPlayerResetState`); `test_fm_polar_cap.mjs`
    aggiornato alla nuova formula e al file 1.2.4.16.

## Versione precedente: 1.2.4.15

- File applicativo: `mri-light-1.2.4.15.html` (`APP_VERSION = '1.2.4.15'`).
- Contenuto della 1.2.4.15 (release sopra la 1.2.4.14):
  - **Polare - primo fix dell'artefatto a stella al polo** (`fmPolarPaintBase`): il WebP del
    renderer si ferma a 85,05112878 N (latitudine di taglio Mercator). Il clamp
    `lat = min(north, lat)` mandava ogni pixel sopra 85 N sulla riga 0 del WebP mentre `sx`
    continuava a variare con l'azimuth: la riga 0 veniva replicata a ventaglio su 360 gradi e,
    con isolinee/valori/frecce disegnati dentro il WebP, si vedevano raggi a stella convergenti
    sul polo. La 1.2.4.15 ha interpolato la fascia 85..90 N con `fmPolarCapMean` (media dei
    pixel opachi della riga 0) e `fmPolarCapColor` (`lerp(capolo, riga0_azimuth, 1 - t)`).
  - **Regione Emisfero nord per i 4 domini globali**: `dwd_icon`, `ncep_gfs025`, `ecmwf_ifs025`,
    `meteofrance_arpege_world025` usano sempre la regione `north` per `index.json` e per gli URL
    dei WebP, qualunque sia il valore del selettore UI (`fmRegionForDomain`, `FM_GLOBAL_DOMAINS`).
  - **Service Worker con cache applicativa**: `CACHE_NAME = 'mri-1.2.4.15'`, nessun precache
    dell'html da 2 MB, nessuna intercettazione delle richieste con header `Range` (i frame `.om`
    usano 206), stale-while-revalidate per gli indici e cache-first per i WebP.
  - **CI - merge su `fm2-rendered` serializzati**: gruppo di concurrency unico
    `fm2-rendered-writer` nei 5 workflow per-fornitore.

## Versione precedente: 1.2.4.14

- File applicativo: `mri-light-1.2.4.14.html` (`APP_VERSION = '1.2.4.14'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.14', date: '2026-10-22' }`).
- Contenuto della 1.2.4.14 (release sopra la 1.2.4.13):
  - **Selettore multi-run WebP e retention**:
    - Archiviazione multi-run con percorsi dedicati (`runs/<run_id>/`) e retention delle ultime 3 run storiche per `(regione, dominio)` tramite `scripts/forecast_v2/prune_fm2_run_index.py`.
    - Selettore della run accanto ai controlli del player con data/ora locale, conteggio frame e percentuale di completezza WebP.
    - Flag `fmRunManual`: preserva la run scelta manualmente dall'utente durante il cambio di dominio.
    - Allineamento `fmDisplayValidTimes` / `fmUpdateTimeDisplay` / `fmRebuild`: corretta visualizzazione e gestione oraria per frame storici e attuali.
    - Integrazione completa di tutte le fonti in 'Fonti & Metodologie' (Mappe previsionali `.om`/WebP multi-run, Ensemble, METAR, SSI radar supercelle) e correzione refusi attribuzione.
  - **Manifest polling a due sorgenti indipendente**:
    - Polling ogni 5 minuti con verifica sia del manifest engine (`latest.json`) sia dell'indice WebP (`index.json`).
    - Eliminata la guardia restrittiva su `generated_at` per ricaricare tempestivamente l'indice WebP appena disponibile.
  - **Motore V2 e renderer in streaming frame-by-frame**:
    - Cache `_DOMAIN_CHILD_MAP_CACHE` e letture parallele con `asyncio.Semaphore(32)` in `_selective_arrays_async` (`forecast_v2_engine.py`): elimina le scansioni sequenziali ripetute di `get_child_by_name`, riducendo su `ncep_gfs025` le richieste HTTP Range da 4.633 (~90,8 s/frame) a 2.272–2.293 (~13,2 s/frame a cache fredda) e 1.968–1.974 (~10,4–10,7 s/frame a cache calda).
    - Guardia NO-OP in streaming: `forecast_v2_engine.py` scrive `noop: true` e `frames: []` nel `--progress-manifest`, e `render_fm_webp.py` termina subito con codice `0` senza rigenerare i WebP né sporcare `out_dir`; in modalità `--watch-manifest` garantisce la presenza delle directory dei domini anche a partenza a freddo.
  - **CI e automazione cron-job.org**:
    - Nei 5 workflow per-fornitore (`forecast-maps-{dwd,dwdicon,ecmwf,ncep,meteofrance}.yml`): output `changed=true` dal job `engine` (salta upload degli shard e `render-merge` in caso di NO-OP; `render-merge` gira con `if: ${{ !cancelled() && needs.engine.outputs.changed == 'true' }}` così un dominio cambiato viene pubblicato anche se l'altro dominio della matrice fallisce), rimosso il `fi` orfano in `forecast-maps-ecmwf.yml`, e dispatch `forecast-maps-published` condizionato a `steps.commit_webp.outputs.published == 'true'`.
    - Script `scripts/cronjobs/setup_cronjobs_api.js` (aggiornamento `PATCH` idempotente, payload senza `inputs` per `forecast-maps-index.yml`, rimozione header `User-Agent`, fail-fast su errore `getExistingJobs`, rate-limit 13 s su `PUT` e 1,5 s su `PATCH`) e documentazione `docs/CRONJOBS_SETUP.md`.

## Versione precedente: 1.2.4.13

- File applicativo: `mri-light-1.2.4.13.html` (`APP_VERSION = '1.2.4.13'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.13', date: '2026-10-21' }`).
- Contenuto della 1.2.4.13 (release sopra la 1.2.4.12):
  - **Renderer - WebP autosufficiente**: le isolinee (fitte, con il valore numerico etichettato) e le frecce del vento sono disegnate dentro l'immagine WebP dal renderer server-side: il livello vettoriale WML sovrapposto non e' piu' necessario. I livelli sono multipli regolari dell'unita' nativa e il raster base non viene mai risamplato (l'overlay e' un layer separato in alpha).
  - **Renderer - solo tema light**: rimossa la variante `dark/` e il flag `--themes dark`; il tema scuro non e' piu' generato lato server.
  - **Client - interruttori opzionali rimossi**: dalla sezione Mappe Previsionali spariscono contorni, frecce, griglia, tema scuro e il selettore di densita' (sono tutti dentro il WebP); il fast-path WebP e' sempre preferito quando esiste.
  - **CI - pipeline delle mappe sbloccata**: i 5 workflow per-fornitore non fallivano piu' sul fetch dei branch dati (`sparse-checkout` in modalita' cone con checkout tolerato) ne' sul percorso degli artifact (`download-artifact`): `fm2-rendered` viene finalmente pubblicata (4.620 WebP).
  - **CI - costo della pipeline ridotto**: l'index-builder materializza solo i manifest JSON (non piu' 240+ MiB di WebP) e il render-merge non scarica piu' l'intero albero WebP, perche' il commit usa gia' un indice temporaneo popolato dal tree del branch (sicurezza provata con git reale 2.53.0).
  - **CI - cron-job.org**: i 3 job dei provider sono stati eliminati (ridondanti col cron nativo dei workflow, che avrebbe prodotto un dispatch triplo su `dwd_icon`); restano i 4 job radar/satellite/data/phenomeni.
  - **Test**: nuovo `scripts/tests/test_render_fm_webp_overlay.py` (26 test) e nuovo `scripts/tests/test_fm_webp_selfcontained.mjs` (55 controlli); i contratti light-only dei test esistenti sono stati aggiornati.
- Contenuto della 1.2.4.12 (release sopra la 1.2.4.11):
  - **CI - cache dati su branch dedicato `fm2-cache`**: i frame `.om` e le tile WebP generati
    non vengono piu' committati su `main` ma su un branch dedicato `fm2-cache`, quindi i dati non
    crescono piu' la storia di `main`.
  - **CI - workflow `forecast-maps-v2.yml` riscritto**: legge i dati preesistenti da `fm2-cache`
    (`git fetch --depth=1 origin fm2-cache` + `git checkout FETCH_HEAD -- data/fm2/<dominio>`),
    committa in cima al tip di `fm2-cache`, pusha con retry e chiude con uno **squash a un solo
    commit** (`git commit-tree` + `git push --force-with-lease`): il branch resta a un commit e non
    accumula storia.
  - **Client - nuovo repository**: `FM_CACHE_BASE` e `FM_RENDERED_BASE` ripuntano al nuovo
    repository `meteoriskitalia-netizen/MeteoRisk-Light-v2`, path
    `/refs/heads/fm2-cache/data/...`.
  - **Test**: 20 file aggiornati, 31 riferimenti da `1.2.4.11` a `1.2.4.12` (bump dei riferimenti
    all'APP_FILE, nessuna asserzione nuova).
  - **Nota motore**: nessuna modifica al motore V2 ne' al renderer, `scripts/forecast_v2/*` sono
    identici alla 1.2.4.11.
- Contenuto della 1.2.4.11 (release sopra la 1.2.4.10):
  - **Renderer/Client - palette pioggia riscritta**: 18 bande da 0 a 50 mm, di cui 17 opache
    (alpha 1.0; la sola banda sotto 0,1 mm resta trasparente = nessuna pioggia), contro le 15
    precedenti con le prime trasparenti o semitrasparenti e le ultime 3 identiche. I breakpoint
    0.01/0.055 erano sotto il quanto di 0.1 mm raggiungibile nei dati e il valore minimo positivo
    cadeva su alpha 0.4: la pioggia debole era un velo quasi invisibile.
  - **Renderer/Client - geopotenziale riscritto** sul modello Meteociel: 31 bande con alpha 1.0
    (era 0.7 su tutte), Z500 4920-6120 m passo 40, Z850 700-1900 m, Z1000 -800-400 m. Prima Z500
    aveva 11 bande con salti di 200 m, Z850 lasciava fuori il 23,5% dei valori e Z1000 l'82,4%.
  - **Renderer/Client - isobare e `pressure_msl`**: isobare ogni 5 hPa (erano 4); la banda di
    bianco pieno `[1010, 1016)` di `pressure_msl` (32,9% delle celle) diventa grigio chiaro nel
    solo tema chiaro (nel tema scuro il bianco resta).
  - **Client - palette unica**: legenda e WebP usano le stesse scale nostre, iniettate nel registro
    della libreria (`defaultOmProtocolSettings.colorScales`) e passate come terzo argomento a
    `getColorScale`. Prima la legenda leggeva la scala della libreria e la mappa quella del
    renderer: due palette diverse per la stessa variabile.
  - **Client - legenda a bande di uguale larghezza** (come Meteociel) con tooltip e intervallo, e
    unita' `mm · <passo>` calcolata dai `valid_times` della serie (1 h, 3 h o 6 h secondo il
    dominio e la fascia di orizzonte), omessa se non determinabile.
  - **Motore V2 - domini da 10 a 7**: restano `dwd_icon`, `dwd_icon_eu`, `dwd_icon_d2`,
    `ecmwf_ifs025`, `ncep_gfs025`, `meteofrance_arpege_world025`; rimossi
    `ukmo_global_deterministic_10km`, `jma_gsm`, `cma_grapes_global`, `italia_meteo_arpae_icon_2i`.
  - **Motore V2 - nuovo dominio `ncep_gfs013`**: GFS di superficie a 0,117° (griglia reale
    3072x1536, il nome "013" e' fuorviante), croppato sull'Europa con bbox `(34, -15, 62, 40)` come
    `dwd_icon_eu`. `ncep_gfs025` e' un prodotto solo upper-air e non contiene alcuna variabile di
    precipitazione (0 su 24): la sovrapposizione fra i due e' 0 variabili, quindi si tengono
    entrambi. Con `ncep_gfs013` arrivano precipitazione, nuvole a 3 livelli, neve e vento a 10 m a
    risoluzione doppia.
  - **Motore V2 - download selettivo via HTTP Range**: `fsspec` + `aiohttp` con
    `OmFileReaderAsync.from_fsspec` leggono dal frame remoto solo le variabili necessarie invece del
    file intero, con **fallback esplicito** al download completo se `fsspec` manca o il formato
    remoto non funziona (`--no-range` forza il percorso classico).
  - **Motore V2 - variabili derivate** (prima il motore risolveva solo nomi esatti): `dew_point_2m`
    (Magnus da T2m + RH2m), `wind_speed_*` e `wind_direction_*` (coppia calcolata dalle componenti
    u/v) e `precipitation_cum` (cumulata dall'inizio della run, sommando i frame in ordine di
    `valid_time`).
  - **Motore V2 - contratto del vento**: si pubblicano `wind_speed_10m/850hPa/300hPa` e
    `wind_direction_10m/850hPa/300hPa` in coppia; le componenti `u/v` non sono piu' pubblicate
    (restano letture interne). La libreria client `@openmeteo/weather-map-layer@0.2.2` tratta i nomi
    `_speed_`/`_direction_` come coppia e lancia se manca la secondaria; inoltre per i nomi
    `_[uv]_component` il client derivava gia' `sqrt(u^2+v^2)`, quindi pubblicare u/v mostrava cose
    diverse fra percorso `.om` e percorso WebP.
  - **Motore V2 - 31 variabili pubblicate** (erano 24): aggiunge `dew_point_2m`,
    `relative_humidity_700hPa`, le sei `wind_speed_*`/`wind_direction_*` e `precipitation_cum`,
    togliendo le componenti `u/v` a 10 m.
  - **Client - filtro disponibilita' variabili**: il selettore mostra solo le variabili realmente
    presenti nel dominio (campo `variables` del manifest, unione sui frame), invece di offrire mappe
    destinate a restare vuote (es. `surface_pressure`, assente in tutti i domini).
  - **Client - domini ed etichette**: `FM_DOMAINS` = 7 domini; nuove etichette 'Punto di rugiada
    2 m', 'Umidita' 700 hPa', 'Vento 10 m', 'Vento 850 hPa', 'Jet Stream 300 hPa', 'Cumulata
    precipitazione'.
  - **Test**: `test_forecast_v2_scale_step.py` (quantizzazione `.om`), `test_fm_webp_palette_sync.mjs`
    (coerenza palette renderer/client, con prova di sensibilita'), test della coppia vento e delle
    derivate nel motore.
  - **Nota dati**: i dati dei 4 domini rimossi **saranno potati dopo il deploy**, non in questa
    release.
- Contenuto della 1.2.4.10 (release sopra la 1.2.4.9):
  - **Motore V2 - fix quantizzazione `.om`**: `scale_factor` era passato uguale al passo (0.1)
    mentre la convenzione `omfiles` e' `valore = int / scale_factor`, quindi il passo effettivo
    diventava 10 (10 °C, 10 hPa, 10 m/s, 10 mm/h); le mappe risultavano a 4-5 bande piatte e la
    pioggia sotto i 5 mm/h finiva a 0, cioe' TRASPARENTE. La tabella diventa `VARIABLE_STEP`
    (passo) e il writer riceve `scale_factor = 1.0 / passo`.
  - **Motore V2 - guardia NO-OP**: il motore non riscrive i frame gia' pubblicati se il
    `reference_time` non cambia: servono `--force`/`force_update=true` oppure il ciclo di
    forecast successivo.
  - **Client - filtro disponibilita' variabili**: il selettore variabili mostra solo le variabili
    realmente presenti nel dominio (campo `variables` del manifest, unione sui frame),
    eliminando le mappe vuote (`surface_pressure` e' assente in tutti i 10 domini;
    `ncep_gfs025` pubblica 10 variabili su 24).
  - **Test**: `scripts/tests/test_forecast_v2_scale_step.py` (15 test, round-trip omfiles che
    fallisce col codice vecchio).
- Contenuto della 1.2.4.9 (release sopra la 1.2.4.8):
  - **CI - Mappe previsionali**: nuova regione di rendering WebP `domain` (intero dominio)
    accanto a Europa e Italia nel workflow `forecast-maps-v2.yml` (shard `render` + single-writer
    `render-merge`, indice `domain/index.json`).
  - **Client - legenda a segmenti con tick numerici**: la legenda delle Mappe previsionali
    e' composta da una banda per colore, posizionata in modo proporzionale ai valori reali,
    con tooltip dell'intervallo `[lo - hi)` e un asse a tick adattivi (max 9); fallback
    invariato per le scale non-breakpoint.
  - **Client - preload WebP parallelo (pool)**: il preload usa un pool a concorrenza
    (`FM_PRELOAD_CONCURRENCY`) con fast-path WebP (`new Image`) e fallback automatico al
    layer `.om` su errore/timeout.
  - **Client - fix Mappe previsionali**: gate tema scuro (`themes` array + path per-tema),
    race su metadati/indice (token di generazione), leak Chart.js (un solo `setInterval`
    condiviso + `clearInterval`/timeout), regione `domain` per il rendering per-dominio,
    upsampling mappe e rimozione thinning.
- Contenuto della 1.2.4.8 (release sopra la 1.2.4.7):
  - **Mappe previsionali - fast-path raster WebP pre-renderizzati**: il client serve le
    mappe come raster via `L.imageOverlay` dalla cache `data/fm2-rendered` (non piu' i frame
    `.om` scaricati per il layer raster).
  - **Renderer server-side**: nuovo `scripts/forecast_v2/render_fm_webp.py` (Pillow) che
    renderizza i WebP per regione, con indice per-regione `index.json` e `color_scales.json`.
  - **Regioni di rendering**: Europa e Italia; per ogni coppia regione/dominio il ritaglio
    e' l'intersezione tra il bbox del dominio e il bbox della regione.
  - **Workflow**: `forecast-maps-v2.yml` renderizza i WebP dopo il motore e li pubblica in
    `data/fm2-rendered`; i 3 workflow che copiano `data/fm2` in `_site` copiano anche
    `data/fm2-rendered`.
- Contenuto della 1.2.4.6 (release sopra la 1.2.4.5):
  - **Mappe previsionali - fix "schermata bianca"**: `fmRegisterDomain` ora registra/aggiorna
    la grid Italia sul dominio attivo (`fmDomain`) oltre al fallback legacy `fm2`
    (`applyGrid` in-place); le mappe previsionali tornano a visualizzarsi.
  - **Mappe previsionali - dominio rimosso**: rimosso il dominio CHMI ALADIN (2 km)
    (`chmi_aladin_central_europe_2km`) dall'elenco `FM_DOMAINS` delle Mappe previsionali
    (da 11 a 10 domini); nessun'altra modifica funzionale.
  - **Mappe previsionali cache-only**: rimossa la sorgente S3 (switch `fm-source-select`,
    fallback `useS3`, `fmSourcePref`, `fmSourceChanged`, `FM_BASE`); la cache `data/fm2`
    e' l'unica fonte; gli errori di `fmRegisterDomain`/`fm2FetchMeta` e di frame mancante
    sono ora visibili via `fmSetStatus` (prima muti -> possibile causa "mappe bianche");
    filtro variabili `FM_CURATED_VARS` applicato sempre.
  - **Mappe previsionali V2**: rimosso il client V2 dall'app; pannello "Mappe
    previsionali" con registro domini (`fmRegisterDomain`) nel blocco FM; set variabili curate.
  - **Motore forecast_v2**: orizzonte intero (`V2_MAX_FRAMES=0`, `--valid-frames 0`)
    con workflow `forecast-maps-v2.yml` allineato.
  - **Popup mappe previsionali in-frame**: su mobile il popup delle mappe previsionali
    non copre piu' l'intera pagina (in-frame nel viewport).
  - **Download tile piu' veloce**: block cache da 512 KB nelle richieste tile
    (da 9 a 3 richieste/tile).
  - **Formato numerico**: i valori meteo della UI passano dall'helper `fmtNum(v,d)`
    (blocco `//#pure# BEGIN numFormat`) con precisione fisica: temperatura/vento/pioggia
    1 decimale, umidita/pressione/probabilita interi; i float lunghi della pipeline IDW
    non vengono piu' stampati raw (in `updateUI`, `renderModelDaily`, tooltip chart e
    pannelli MNW); null/non-finiti -> `--`.
  - **Etichette zona**: le province mostrano "Provincia di <nome>" (pannello zona,
    tooltip rischio, riepilogo rischio, status); citta/comune restano con il nome nudo.
  - **Supercelle (LIVE)**: attivazione spostata nel pannello LIVE (rimosso il toggle
    dalla main); sub-toggle Traccia/Forecast/Fenomeni/Grandine nel LIVE, visibili a
    supercelle attive.
  - **Grandine**: nuova metrica sotto Sviluppo con due sottolivelli (Probabilita
    potenziale 0-5 e Dimensione stimata in mm) che colora province/celle (dati
    client-side da `hailMetrics`; soglie stima non calibrate).
  - **Player**: rimosso il "kick" di velocita' (il pacing al 1x resta 1000 ms/frame).
  - **Bordi/confini**: i confini regionali/provinciali sono ora visibili sopra il
    satellite (nuovo pane dedicato, stroke-only).
  - **Grandine**: nuovo toggle dedicato in UI.
  - **CAPPI**: CAPPI_2/CAPPI_6 attivati (overhang non piu' neutro).
  - **Vortex**: proxy hook echo su K frame coerenti + morfologia per-frame persistita
    (`tracks.json`).
  - **Grandine multi-prodotto**: POH primario + struttura verticale (VIL/ETM/CAPPI/overhang)
    + H0 per-cella + gate fulmini ricalibrato.
  - **Vortex**: riduzione falsi positivi + deduplica eventi (badge come evento, non per-frame).
  - **UI**: distinzione esplicita tra rischio ambientale NWP (potenziale) ed evento
    osservato radar.
  - **Nuovo**: logica overshooting top da IR_108 (DPC) sulla griglia radar; entra in
    SSI v2 e come corroboratore satellite di hail/vortex.
  - **Multifonte**: prototipo isolato adapter OPERA CIRRUS (`radar_engine/fusion/`), non attivo.
  - **Pannello LIVE**: ospita la funzione "Supercelle radar" (sub-toggle nel LIVE),
    raggiungibile anche senza fulmini.
  - **Mappe previsionali (1.2.4.0)**: nuova sezione basata su Open-Meteo Weather Map
    Layer (maps.open-meteo.com / openmeteo.s3.amazonaws.com, CC BY 4.0), nata al posto
    del vecchio modulo Meteociel (dismesso); solo visualizzazione sinottica, nessun
    impatto sui calcoli. Selettore modello/dominio (default DWD ICON EU 0.0625,
    `dwd_icon_eu`; 10 domini dopo la rimozione del dominio CHMI ALADIN nella 1.2.4.5), variabile e ora di previsione; selettore area
    geografica (11 voci: 10 aree bbox predefinite + "Intero dominio"); player con
    velocita' (Lenta/Normale/Veloce) e slider; tasto "Preload" con contatore di
    avanzamento + warm-ahead del frame successivo; layer serviti via protocollo `om://`;
    flag `forecastMaps` attivo di default.
- **Limiti noti**: l'overshooting top e' un proxy IR-only (senza WV/BTD); l'adapter
  OPERA CIRRUS e' un prototipo isolato e non attivo; le soglie hail/vortex sono
  sperimentali.
- Le release vivono in `releases\MeteoRisk-Light-X.Y.Z-GitHub-Production-Hardening\`.
- Regola dello storico: si PRESERVA. Mai rinominare una cartella di release esistente;
  per una nuova versione si crea una nuova cartella copiando lo stato attivo e bumpando
  solo i file sensibili (html, VERSION, workflow, test).

## Architettura dati

Sito statico piu' sei workflow GitHub Actions che aggiornano i dati e pubblicano su Pages:

| Workflow | Ruolo |
| --- | --- |
| `radar-engine.yml` | ogni 5 min scarica i prodotti radar DPC (VMI), rileva e traccia le celle convettive, committa **solo** i derivati in `data/radar/` (`latest.json`, `storms.geojson`, `tracks.json`, `supercells.json`) e l'archivio rolling su branch orfano `radar-history`, build dell'artefatto Pages e deploy |
| `satellite-engine.yml` | ogni 5 min scarica i frame WMS EUMETSAT (6 sorgenti: 3 Europa + 3 Italia), li ri-codifica in WebP e li pubblica con force-push sul branch orfano `satellite-cache` (finestra rolling di 25 slot = 2 ore). **Niente** build/deploy Pages in questo workflow: i frame arrivano su Pages perche' radar-engine e update-weather-data la restaurano da `satellite-cache` nell'artefatto di deploy |
| `update-weather-data.yml` | ogni 10 min: check del run ECMWF IFS + canary Best Match -> fetch Open-Meteo -> build, validazione e pubblicazione del dataset derivato in `data/latest/` (commit e deploy **solo** con nuovo dataset valido) |
| `phenomena-verify.yml` | ogni 10 min valuta i fenomeni grandine/rotazione dai derivati radar + fulmini MLI EUMETSAT (WMS anonimo, CC-BY-4.0) e committa `data/phenomena/{events,badges}.json` su `main` (niente build/deploy: i file arrivano su Pages nell'artefatto di radar-engine e update-weather-data) |
| `forecast-maps-v2.yml` | ogni 6h (cron-job.org) genera la cache mappe previsionali V2 in `data/fm2/` (`<VALID>.om` + `latest.json`, crop multi-variabile, orizzonte intero `--valid-frames 0`, prune). Da 1.2.4.12 i dati (frame `.om` + WebP) NON stanno piu' su `main` ma sul branch `fm2-cache`: il commit nasce dal tree del branch + allowlist dei soli path `data/fm2/**` (isolamento indice), il push dei WebP e' incrementale a soglia 1,4 GiB, il job engine ha `timeout-minutes: 180` |
| `deploy-pages.yml` | deploy manuale/consolidamento: ricostruisce `_site/` con app + `data/latest`, `data/geography`, `data/radar` (incl. archivio rolling `radar-history`), `data/phenomena`, `data/fm2` e `satellite/` (finestra `satellite-cache`) e pubblica su Pages (`configure-pages` + `upload-pages-artifact@v4` + `deploy-pages@v4`, concurrency dedicata `pages-deploy` come gli altri) |

- I 4 domini **globali** (`dwd_icon`, `ecmwf_ifs025`, `ncep_gfs025`, `meteofrance_arpege_world025`) usano il bbox emisferico `NORTH_HEMISPHERE_BBOX = (20.0, -180.0, 90.0, 180.0)` (lat 20..90N, lon intera), definito in `scripts\forecast_v2\forecast_v2_engine.py`. Il renderer WebP pubblica anche la regione north, selezionabile come Emisfero nord nel player. Il client la presenta in proiezione stereografica polare dall'alto: riproietta i frame WebP, mantenendo colori, isolinee numeriche e frecce del vento; aggiunge graticola e coste Natural Earth. Il frame sorgente è Web Mercator fino a 85.05N, quindi il piccolo cappuccio fino al Polo usa il valore del bordo più vicino. T850 è etichettata ogni 4°C. I 3 regionali mantengono i loro bbox (`dwd_icon_eu`/`ncep_gfs013` `(34,-15,62,40)`, `dwd_icon_d2` `(43.18,-3.94,58.08,20.34)`).

- I frame satellitari sono WebP a **risoluzione invariata**: stesso slot, stesso
  orologio e stessi formati dati della codifica precedente, solo meno byte.
- GeoTIFF radar raw e payload Open-Meteo non vengono mai committati: solo derivati.

## Trigger

Ogni workflow parte da `workflow_dispatch` e `repository_dispatch`:

- `ci-run` -> radar-engine + satellite-engine (dispatch esterno ogni 5 min);
- `ci-weather` -> update-weather-data (dispatch esterno ogni 10 min).

Il dispatch esterno e' **best-effort**: se non arriva, l'unico recupero e' un
trigger manuale via `workflow_dispatch`. Nessuna garanzia di latenza fissa sui dati.

## Test

Verifiche di riferimento (eseguite su questa release):

```bash
# Comando ufficiale su questo host: la cattura fd di pytest e' instabile
# (OSError: [WinError 6] Handle non valido sui test che lanciano subprocess) e
# con -s la console cp1252 rompe sul carattere U+2192, quindi --capture=sys.
$env:PYTHONIOENCODING='utf-8'                        # PowerShell
python -m pytest scripts\tests\radar_engine -q       # 227 passed
python -m pytest scripts\tests\phenomena -q          # 125 passed, 1 skipped
python -m pytest scripts\tests -q --ignore=scripts\tests\test_decide_cycle.py --capture=sys
                                                     # 649 passed, 9 failed PRE-ESISTENTI, 1 skipped
                                                     # 9 = 6 FileNotFoundError regions_data.json + 1 api_usage.json + 2 AssertionError (test_bootstrap)
python -m py_compile <tutti i .py sotto scripts\>    # 118/118 OK, 0 errori (118 file, ricorsivo)
python -c "import yaml,glob; [yaml.safe_load(open(f,encoding='utf-8')) for f in glob.glob('.github/workflows/*.yml')]"  # 12 workflow, 12/12 OK
node --test "scripts/tests/**/*.mjs"                 # suite .mjs: 25 file, 17 PASS, 8 EXIT 1 (7 pre-esistenti + contract_dataset_loader.mjs senza dataset baseline)
```

Comandi e conteggi aggiornati sono quelli documentati in `C:\Users\aless\Desktop\AGENTS.md`, sezione "Verifica/qualita'" (il documento di progetto e' il file in `C:\Users\aless\Desktop\`, NON dentro `meteorisk\`).

## Convenzioni del repo

- **Push**: lo fa SEMPRE l'utente manualmente (tramite GitHub Desktop), oppure solo su
  esplicita richiesta dell'utente. Nessun push/commit/clone verso il remote senza
  richiesta esplicita.
- **Staging**: `GITHUB UPLOAD\` contiene SOLO il delta ADD+UPDATE, secondo la nota
  interna `_NOTE_UPLOAD_README.txt` (da leggere prima di operare).
- **Mai in staging**: `data/`, `__pycache__/`, `.pytest_cache/`, `.git/`, cartelle
  `releases\` vecchie.
- **Niente token/PAT** salvati nei file di configurazione o istruzioni.

## File chiave

- `mri-light-1.2.4.20.html` — applicazione (html/css/js unico, nessuna dipendenza runtime)
- `sw.js` — Service Worker (cache applicativa: WebP, indici; mai le richieste Range `.om`)
- `VERSION` — bump di versione e storico note di rilascio
- `.github/workflows/{radar-engine,satellite-engine,update-weather-data,phenomena-verify,deploy-pages,forecast-maps-v2}.yml` — i 6 workflow
- `scripts/radar_engine/` — motore radar DPC (Fase 1 + descrittore di struttura convettiva
  `convective_structure.py`/SOI + stack severo `severe_stack.py`, sperimentali)
- `scripts/satellite_engine.py` + `scripts/satellite_engine_requirements.txt` — download
  dei frame EUMETSAT e ri-codifica WebP
- `scripts/*.py` — pipeline meteo (check, decision, fetch, build, validate, publish)
- `scripts/tests/` — test pytest e suite `.mjs`
- `data/latest/` — dataset derivato (nello snapshot locale solo `.gitkeep`: il dataset
  reale e' generato e committato dai workflow)
- `docs/` — report e documentazione storica

