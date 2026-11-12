# MeteoRisk Light — dashboard meteorologica statica-first

Dashboard meteorologica statica-first per l'Italia: radar delle tempeste (DPC),
satellite EUMETSAT e dati Open-Meteo, servita interamente da GitHub Pages — nessun
back-end applicativo, nessuna dipendenza runtime oltre agli asset statici.

Repository: https://github.com/meteoriskitalia-netizen/MeteoRisk-Light-v2

## Versione attiva: 1.2.4.84

- File applicativo: `mri-light-1.2.4.84.html` (`APP_VERSION = '1.2.4.84'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.84', date: '2026-11-12' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.84'`, `TILE_CACHE = 'mri-tiles-1.2.4.84'`,
  `FRAME_CACHE = 'mri-frames-1.2.4.84'`, `NETWORK_TIMEOUT_MS = 10000`, `INDEX_TIMEOUT_MS = 2000`,
  servito come `/sw.js` e copiato in `_site/sw.js` da `deploy-pages.yml`, `radar-engine.yml`,
  `update-weather-data.yml`).
- Contenuto della 1.2.4.84 (release sopra la 1.2.4.83) - **shear Doppler in mappa e area di copertura geometrica**. Solo client: nessuna modifica a `scripts/` (motore, `doppler.py`, renderer, dataset, `color_scales.json`); `sw.js` tocca solo le 3 var di cache e i 3 commenti che le documentano, i workflow solo le righe `cp`/commenti del nome del file.
  - **Shear azimutale osservato**: per ogni struttura con `rotation_shear.shear_ms_km` numerico un **cerchietto di 22x22 px con la freccia di azimuth** (0 gradi = Nord, senso orario) posto **sotto** il pennant **dentro lo stesso `divIcon`**: un solo marker per struttura, niente icone sovrapposte, nessun click conteso. E' un descrittore geometrico osservato dal radar Doppler, **non** una diagnosi di circolazione. Tooltip e riga del pannello Struttura convettiva portano la stessa stringa con i campi del payload (`shear_ms_km`, `azimuth_deg`, `range_km`, `beam_height_km`, `confidence`, `radar`), numeri con la virgola, siti tradotti (`frale` -> Aleria, `fraja` -> Ajaccio). Chip di riepilogo `Shear Doppler X/Y`, presente solo quando il payload pubblica la chiave.
  - **Area di copertura geometrica**: il geojson puo' contenere due famiglie di feature distinte da `properties.kind`: `geometric` (copertura dell'antenna, non dipende dal tempo) e `observed` (copertura misurata nei volumi, raggi con almeno un gate valido). Se esiste almeno una `geometric` il client disegna **solo quelle** e l'area non si restringe quando non piove; le `observed` restano nel file ma non vengono disegnate. Se `kind` non c'e' (file precedente) si disegnano **tutte** le feature come prima; se esiste solo `observed` si disegnano le osservate. Il canvas composito non cambia: maschera opaca fuori schermo, tinta una volta, composizione unica a **alpha 0,10**, nessun bordo, nessun tratteggio. La legenda LIVE dichiara quale area sta guardando (`Area disegnata: N settori geometrici ... - M aree osservate nel file non disegnate`).
  - **Nessun simbolo quando il dato manca**: con `shear_ms_km` null nessun glifo e una riga grigia `Shear azimutale non disponibile: <codice> - <spiegazione>` con i codici del motore mappati (10) e un codice ignoto scritto cosi' com'e'; con la chiave `rotation_shear` assente (payload vecchi) nessun simbolo, nessuna riga, nessun chip, nessuna eccezione.
  - **Misure (Edge headless CDP)**: area di `main` alle 19:56Z (7 feature, nessun `kind`) -> **7 aree disegnate**, 1 canvas, **0** path vettoriali, **0** tratteggi, alpha **26/255 = 0,102** dentro e **0** fuori; Roma (12,4964E 41,9028N) allora **fuori** dall'area osservata (alpha 0, bordo piu' vicino **3,6 km**, feature `frale` `min_elevation`). Alle 20:06Z il motore ha pubblicato `coverage_geometric`: **10 feature (3 `geometric` + 7 `observed`)** e la pagina **servita** disegna **3 aree geometriche su 10**, le **7 osservate non sono disegnate**, **Roma e' DENTRO** (alpha **26**), il punto interno alle sole osservate e' **0**, 1 canvas, **0** path vettoriali, **0** tratteggi. Shear con fixture valorizzata: **1 glifo**, tooltip e riga `Shear azimutale 6,2 m/s/km · az 95° · 48 km · fascio 0,7 km · confidenza media · Doppler Aléria`, chip `1/1`; con null: **0** glifi e la riga-motivo; con chiave assente: **0** glifi, **0** righe, nessun chip. Parole vietate nel pannello (`rotazione`/`mesociclone`/`supercella`): **0**. Overflow orizzontale **0** a 1440x900 e 390x844, **0** eccezioni JS, **0** `console.error`. `node --check` **8/8**, `test_html_js_syntax.py` **1 passed**, suite `.mjs` **28 file -> 26 PASS / 2 non-zero** (stessi nomi della baseline 1.2.4.83: `contract_dataset_loader`, `test_fm_webp_fastpath`).
- Contenuto della 1.2.4.83 (release sopra la 1.2.4.82) - **niente linee, una zona di densita'**. Solo client: nessuna modifica a `scripts/` (motore, ensemble, renderer, dataset, `color_scales.json`, classi di rischio); `sw.js` tocca solo le 3 var di cache e i 3 commenti, i workflow solo le righe `cp`/commenti del nome del file.
  - **La richiesta**: le 24 traiettorie non vanno mostrate come linee ma come **materia prima di una densita'**, mostrata come area colorata con opacita' crescente dove le traiettorie si concentrano.
  - **Come**: ogni percorso e' rasterizzato su un canvas offscreen con pennello bianco **additivo** (`globalCompositeOperation='lighter'`, `lineWidth` **6 px**, `globalAlpha` **0,10** per membro, `lineJoin/lineCap round`); il campo e' sfocato in **FLOAT** (3 passate di box blur separabile orizzontale+verticale, sigma ~ raggio) e mappato in opacita' con `alpha = clamp(densita'^0.7 x 0,55, 0, 0,55)` dopo la normalizzazione a **max 1**. Il pane forecast riceve **UN** `L.imageOverlay` per struttura con `opacity: 1` (l'alpha e' gia' nel canvas, classe `sc-ens-density`). **Zero** path di linea per i membri, **zero** poligoni con `fill-opacity > 0` nel ramo con membri. La media resta una linea marcata a opacita' **0,9**; le etichette `+30/+60/+90` restano sulla media.
  - **Kernel geografico, non pixel**: raggio di sfocatura **26 km** convertiti in pixel allo zoom corrente e clampati a **[6, 44] px** -> 7,4 px a z5, 14,9 a z6, 29,7 a z7, 44 da z8. Motivo misurato: con un blur fisso di 10 px a z8 le 24 traiettorie riemergono come "dita" (il fascio si allarga in pixel, il blur no). Il blur e' fatto in JS su `Float32Array` e non con `ctx.filter` perche' il canale alpha del canvas e' a 8 bit e con `blur() + getImageData` il campo usciva a **bande concentriche** visibili.
  - **Misure (Edge headless CDP, payload 2026-11-11T14:05Z, 4 strutture, 24 membri)**: **0** path di linea dei membri, **1 imageOverlay per struttura (4/4)**, **0** poligoni con `fill-opacity > 0`. Alpha campionati sulla sezione perpendicolare al percorso medio al passo finale, **decrescenti 4/4**: `cel006` **0,472 / 0,152 / 0,000** (rapporto non finito: densita' nulla al bordo), `sto002` **0,262 / 0,149 / 0,072** (**3,65**), `sto025` **0,168 / 0,051 / 0,051** (**3,29**), `sto003` **0,218 / 0,129 / 0,051** (**4,28**). Rapporto fra l'area con `alpha >= 0,275` e quella con `alpha >= 0,055`: **0,175-0,211** (non e' un ellisse uniforme). Tempo di costruzione per struttura: **2,9-25,0 ms** a z6, **53,6-69,1 ms** a z8 con rebuild forzato (tetto di 180.000 px, sotto il quale il canvas viene rasterizzato piu' piccolo e ingrandito dal browser).
  - **Fallback invariato**: run senza `ensemble_members` -> `cone_polygon` pieno o nastro da `cone_km` come nella 1.2.4.82.
  - **Verifiche**: `node --check` **8/8**; `pytest scripts/tests/test_html_js_syntax.py` **1 passed**; suite `.mjs` **28 file -> 26 PASS / 2 non-zero**, gli stessi nomi della baseline 1.2.4.82 (`contract_dataset_loader`, `test_fm_webp_fastpath`); 1440x900 e 390x844 overflow **0**, **0 eccezioni JS**, **0 console.error**, `elementFromPoint` su un marker restituisce il marker; rotazione/mesociclone/supercella **0** nel pannello.
- Contenuto della 1.2.4.82 (release sopra la 1.2.4.81) - **rimosso il poligono pieno quando e' disponibile il fascio di traiettorie**. Solo client: nessuna modifica a `scripts/` (motore radar, renderer, dataset, `color_scales.json`, classi di rischio), al fascio o al cono lato motore; `sw.js` tocca solo le 3 var di cache e i 3 commenti, i workflow solo le righe `cp`/commenti del nome del file.
  - **Il difetto**: con un payload misto (alcune strutture con `ensemble_members`, altre no) la 1.2.4.81 disegnava il fascio per le prime e il `cone_polygon` PIENO per le seconde: **72 linee + 3 medie E 1 poligono** `fill:#22c55e` `fill-opacity:0.35` (o `#94a3b8` 0,18), cioe' la macchia tornava sotto le linee. La regola era per-record, non per-run.
  - **La correzione**: nuovo `scRadarEnsembleAvailable()`: se ALMENO UNA struttura con forecast ha `ensemble_members` non vuoto (marcatore del motore 1.2.4.81+), `drawScForecast` non crea **NESSUN** `L.polygon` pieno, nemmeno per le strutture senza membri. Per quelle restano il path tratteggiato e le etichette `+30/+60/+90`. Nessun contorno dell'anello 90%: meglio niente che una forma piena.
  - **Fallback invariato**: run < 1.2.4.81 (nessuna struttura con membri) -> `cone_polygon` pieno o nastro da `cone_km`, esattamente come prima; il poligono ora porta `className: 'sc-cone-fallback'` per contarlo nei test.
  - **Misure (Edge headless CDP, payload 2026-11-11T14:05Z, 4 strutture)**: con il fascio **0 path con `fill-opacity > 0`**, **96 linee membro** (24x4), **4 medie**; payload misto (3 con fascio, 1 senza): **prima 1** path con `fill-opacity` 0,35, **dopo 0** path con `fill-opacity > 0`, 72 linee + 3 medie; fallback run vecchia (payload 2026-11-11T20:05Z, 11 strutture, 0 con `ensemble_members`): poligoni pieni **invariati** (5). 1440x900 e 390x844 overflow **0**, **0 eccezioni JS**, rotazione/mesociclone/supercella **0**.
  - **Verifiche**: `node --check` **8/8**; `pytest scripts/tests/test_html_js_syntax.py` **1 passed**; suite `.mjs` **28 file -> 26 PASS / 2 non-zero**, gli stessi nomi della baseline 1.2.4.81 (`contract_dataset_loader`, `test_fm_webp_fastpath`).
- Contenuto della 1.2.4.81 (release sopra la 1.2.4.80) - **fascio di 24 traiettorie al posto delle bande annidate**. Motore + client: `scripts/radar_engine/nwp_motion.py` (24 membri, media, spread), `scripts/radar_engine/forecast.py` (niente `cone_bands`, resta `cone_polygon`), `scripts/radar_engine/convective_structure.py` (3 campi nuovi sul record); client: 24 polilinee + media, nessun riempimento; `sw.js` tocca solo le 3 var di cache e i 3 commenti, i workflow solo le righe `cp`/commenti del nome del file.
  - **Il difetto**: le bande annidate 50/75/90% erano una macchia sfumata e non mostravano dove si concentrano le traiettorie. Il motore pubblicava `cone_bands` (3 anelli per step) e le isolinee KDE 25/50/75/90% dell'ensemble, che il client non ha mai disegnato.
  - **Il fascio**: `ensemble_members` = 24 percorsi `[[lon,lat] x 5]` (t=0/30/60/90/120 min, 3 decimali ~100 m) in **12 coppie speculari** rispetto all'asse del moto, ai quantili di densita' dell'ensemble da **200** membri (rango di |z|). `ensemble_mean` = traiettoria del blend (5 punti). `ensemble_spread_km` a 60/120 min = std lungo/trasverso + p90 trasverso sui 200 membri. Seed deterministico invariato (`_seed_for(structure_id, anchor_ms)`).
  - **Il client**: 24 polilinee `weight 1`, `opacity 0,12`, colore unico del cono; media `weight 2,5`, `opacity 0,9`; **nessun riempimento**. Etichette `+30/+60/+90` sulla media. `cone_bands` non esiste piu' nell'output; fallback run < 1.2.4.81: `cone_polygon` singolo (o nastro da `cone_km`), nessuna banda. Legenda: `Fascio di 24 traiettorie simulate - la densita' indica la direzione piu' probabile; non calibrato`.
  - **Misure (payload 2026-11-11T14:05Z, 4 strutture, seed identico 4/4)**: **366.662 B -> 145.383 B** (-221.279 B, **-60,3%**), sotto il tetto di 200 KB con 24 membri. `cone_bands` da solo pesava **43.746 B**, le isolinee KDE **192.637 B**, i 3 campi nuovi **9.580 B**. A 60 min: **4-12** membri su 24 entro 5 km dalla traiettoria media; larghezza trasversale p90 **14,7-34,1 km**. PCA su tutti i punti del fascio: asse lungo **0,5-5,3 gradi** dalla direzione del moto (limite 20), rapporto lunghezza/larghezza **1,54-2,53**. Sulle 7 strutture live del run 2026-11-11T19:15Z: asse **0,4-1,9 gradi**, L/W **1,16-2,45**.
  - **Verifiche**: `pytest scripts/tests/radar_engine` **430 passed** (baseline **429**); `pytest scripts/tests/test_html_js_syntax.py` **1 passed**; `node --check` **8/8**; suite `.mjs` **28 file -> 26 PASS / 2 non-zero**, gli stessi nomi della baseline 1.2.4.80 (`contract_dataset_loader`, `test_fm_webp_fastpath`); Edge headless CDP **19/19** check sul payload a 4 strutture (**96 linee membro = 24x4**, **4 medie**, **0 poligoni di banda**), fallback **7/7** (4 `cone_polygon`, 0 bande), 1440x900 e 390x844 overflow **0**, **0 eccezioni JS**, **0 console.error**, `elementFromPoint` sul marker restituisce il marker.
- Contenuto della 1.2.4.80 (release sopra la 1.2.4.79) - **bande di probabilita annidate 50/75/90 e contorno della cella**. Solo client: nessuna modifica a `scripts/` (motore radar, renderer, dataset, `color_scales.json`, classi di rischio); `sw.js` tocca solo le 3 var di cache e i 3 commenti, i workflow solo le righe `cp`/commenti del nome del file.
  - **Bande annidate**: il motore pubblica per ogni step `forecast.steps[].cone_bands` con 3 anelli (`level` 0.5/0.75/0.9, `polygon` `[[lon,lat],...]`). Il client li disegna dall'esterno verso l'interno: 90% alpha **0,08**, 75% **0,14**, 50% **0,22**, stessa tinta del cono (verde `#22c55e` high, ambra `#f59e0b` medium, grigio `#94a3b8` low), bordo **1 px** a opacita' 0,25. Z-order globale: tutte le 90 sotto, poi le 75, poi le 50; path, etichette `+30/+60/+90` e frecce restano sopra. Misura sul run `2026-11-11T13:35Z`: 5 strutture, 4 con forecast, **16 step**, **48 bande** (16 per livello); contenimento 50 dentro 75 dentro 90 su **803 vertici -> 0 fuori**; z-order DOM ultima 90 indice **15**, prima 75 **16**, prima 50 **32**, ultimo path **50**.
  - **Contorno della cella**: il record pubblica `outline` (GeoJSON Polygon, anche con buchi), `outline_coverage_pct`, `outline_components_dropped`, `outline_null_reason`. Il client lo disegna con una linea **1,2 px** `#0f172a` a opacita' **0,55** e **nessun riempimento**, in un pane dedicato z **545** sopra il cono e sotto i marker (550). Misura: **5/5** strutture con outline, **5 path** disegnati. Se `outline` e' null non si disegna nulla e non compare alcun messaggio.
  - **Fallback**: se `cone_bands` manca (run < 1.2.4.80) si usa `cone_polygon` come prima; se manca anche quello, il nastro storico da `cone_km`. Verificato con due fixture sul payload reale: (A) senza `cone_bands` -> 0 bande, 4 poligoni singoli dash `3,4` con fill 0,35/0,22; (B) senza `cone_bands` e senza `cone_polygon` -> 0 bande, 4 poligoni dal nastro, 0 eccezioni.
  - **Legenda**: nuova riga `Bande di probabilità 50/75/90% — derivate dagli errori storici del tracker, non calibrate`; `probability_calibrated:false` rispettato (4/4 forecast).
  - **Misure**: rapporto area cono 90% / area struttura non piu' una costante, **4,2-1.370,6x** sul run misurato (il brief indicava 5,6-207x, non riprodotto su questo campione; il rapporto dipende dalla dimensione della cella, da 44 km2 a 3.875 km2). Verifiche: `node --check` **8/8**; `pytest scripts/tests/test_html_js_syntax.py` **1 passed**; suite `.mjs` **28 file -> 26 PASS / 2 non-zero**, gli STESSI nomi della baseline 1.2.4.79 (`contract_dataset_loader`, `test_fm_webp_fastpath`); 1440x900 e 390x844 overflow **0**, **0 eccezioni JS**, **0 console.error**; `elementFromPoint` su un marker restituisce il marker; rotazione/mesociclone/supercella **0** nel pannello; Top-N 4 in evidenza, confidence, mini timeline, allerta di prossimita' e area Doppler (1 canvas nel pane 405) verificati.
- Contenuto della 1.2.4.79 (release sopra la 1.2.4.78) - **R2 orario coerente: raffiche e probabilita dell'ora, non i massimi del giorno**. Solo client: nessuna modifica a `scripts/` (motore radar, renderer, dataset, `color_scales.json`, classi di rischio), al cono (dilatazione del poligono lato motore), a Top-N/confidence/timeline; `sw.js` tocca solo le 3 var di cache e i 3 commenti, i workflow solo le righe `cp`/commenti del nome del file.
  - **Il difetto**: `computeR2ForStore` passava a ogni ora la raffica massima e la probabilita massima del giorno. Un'ora calma con una raffica forte nel pomeriggio veniva classificata come se la raffica fosse in quell'ora. Misurato da un backtest indipendente su 3.360 celle-giorno: accensioni di R2 >= 3 da **75** a **173** (2,3x), FAR da **0,787** a **0,896**.
  - **La correzione**: nuovo `r2HourlyInput(store, ix)`; ogni ora usa SOLO `windgusts_10m`, `precipitation_probability` e `precipitation` di quell'ora. Campo orario mancante -> `null` (mai il massimo del giorno): la condizione che lo usa non scatta.
  - **Scala invariata**: `riskLevelR2`, `riskR2Reason`, soglie 100/75/50 km/h, 45/15/1/0,2 mm, 60/20%, pesi, ordine dei livelli e meso (5) invariati. La vista giornaliera continua a usare i massimi del giorno (pioggia cumulata, raffica massima, probabilita massima).
  - **Verifiche**: fixture con raffica 80 km/h solo alle 18:00 -> 06:00 non supera il livello per vento, 18:00 si; distribuzione oraria prima/dopo sui dati serviti; rotazione/mesociclone/supercella 0 nel pannello; `node --check` 8/8; `pytest scripts/tests/test_html_js_syntax.py` 1 passed; suite `.mjs` 28 file con baseline 1.2.4.78 (26 PASS, 2 non-zero pre-esistenti) e confronto per nome; 1440x900 e 390x844 overflow 0, 0 eccezioni.
- Contenuto della 1.2.4.78 (release sopra la 1.2.4.77) - **Top-N con gerarchia, confidence del tracking nel LIVE, mini timeline al click, allerta solo con moto affidabile, cono ellittico dal poligono del motore**. Solo client: nessuna modifica a `scripts/` (motore radar, renderer, dataset, `color_scales.json`, classi di rischio); i workflow toccano solo le righe `cp`/commenti del nome del file.
  - **Top-N e gerarchia**: le prime **4** strutture per SOI (**3** sotto 1280 px) in evidenza con badge `#1..#N`, bordo viola e sfondo; le altre in un `<details>` chiuso `Altre strutture (N) Â· ordinate per SOI`. Ordinamento SOI decrescente con tie-break deterministico sulla chiave del track; l'intestazione dichiara il criterio (`organizzazione stimata dalla morfologia, senza Doppler`). Marker del Top-N piu' grandi (+10 px, peso +0,8, alone tratteggiato e pallino numerato). Con meno strutture del Top-N il gruppo secondario non esiste.
  - **Confidence del tracking nel LIVE**: `#live-sc-confidence` e `#sc-confidence`, stesso testo `Qualita' del tracking: alta|media|bassa|n/d Â· N% delle track con moto affidabile Â· durata mediana N min` + riga della fonte. Fonte: `tracking_metrics`, altrimenti `tracking_confidence`, altrimenti fallback **dichiarato** da `tracks.json` (track `motion_quality=reliable` con velocita' numerica, mediana di `duration_min`), altrimenti dalle strutture pubblicate (campione biased, solo SOI >= 65). Se manca tutto: `n/d`.
  - **Mini timeline al click**: SVG inline con tre sparkline (area kmÂ², dBZ max, km/h), ultimo valore a destra e asse `HH:MMZâ€“HH:MMZ`, testata `N frame Â· range Â· fonte`. Priorita' delle serie, tutta dati gia' in memoria: `track_points` se array, `tracks.json` (per uno storm object `merged_cell.track_id`), archivio 2h delle tracce, stato corrente. Velocita' fra punti consecutivi, oltre 200 km/h scartata (stesso gate del track).
  - **Allerta solo con moto affidabile**: primo gate `motion_reliable=true` (altrimenti `N non allertabili (moto non affidabile (motion_reliable=false))`), secondo gate ETA calcolabile (`distanza / velocita' > 0`). L'ETA arrotonda al **minuto** (prima a 5): 12 km a 40 km/h = **18 min**.
  - **Cono ellittico**: il motore pubblica `forecast.steps[].cone_polygon` (33 vertici chiusi, GeoJSON Polygon), `cone_km_along`, `cone_km_cross` e `cone_shape: "ellipse"`. `scBuildForecastCone` usa il **poligono del motore** quando c'e' (anelli dei 4 step fusi in un solo anello con l'inviluppo convesso dei vertici: nessun punto inventato) e tiene il nastro da `cone_km` come **fallback** per le run precedenti. Misura su struttura reale: area **34.218 kmÂ²** contro **57.256 kmÂ²** del cerchio di raggio `cone_km` (rapporto **0,598**), 270,3 km lungo il moto (uguale) e 162,2 km di traverso invece di 270,3. Colori, opacita', `scForecastUsable` ed etichette `+30/+60/+90` invariati.
  - **Test e misure**: `node --check` sui blocchi inline **8/8**; `pytest scripts/tests/test_html_js_syntax.py` **1 passed**; suite `.mjs` **28 file**, confronto per nome con la baseline misurata sulla 1.2.4.77 PRIMA delle modifiche. Marcatori intatti: `#live-toggle-doppler`, `scAlertEvaluate`, `#forecast-details` (3 tab), 6 aree, fasce altimetriche.
- Contenuto della 1.2.4.77 (release sopra la 1.2.4.76) - **fase 4: PWA installabile e offline parziale**. Solo client + service worker + asset statici: nessuna modifica a `scripts/` (tranne `scripts/tests/test_sw_cache_policy.mjs`), al motore radar, al renderer, a `color_scales.json`, al dataset o alle classi di rischio; i workflow toccano solo le righe `cp`/commenti del nome del file e le 5 righe che pubblicano manifest/offline/icone.
  - **PWA installabile**: nuovi asset statici alla radice `manifest.webmanifest` (724 B, JSON valido: `name`, `short_name`, `start_url`, `display: standalone`, `background_color`/`theme_color` `#0b1220`, `lang: it`, icone 192 e 512), `offline.html` (2.390 B), `icons/icon-192.png` (32.332 B) e `icons/icon-512.png` (147.100 B, PNG reali con IHDR 192x192 e 512x512). L'html dichiara **solo due righe nuove** dopo `<title>`: `<link rel="manifest" href="manifest.webmanifest">` e `<link rel="apple-touch-icon" href="icons/icon-192.png">`. Nessun'altra modifica alla 1.2.4.76: la fase 3 (`scAlertEvaluate`, coni, frecce) e l'area Doppler (`#live-toggle-doppler`) restano intatte.
  - **Tile offline 7 giorni**: nuova cache `mri-tiles-*` per `tile.openstreetmap.org/z/x/y.png`, cache-first con TTL **7 giorni** e tetto **400 voci** (~16 MB). Ogni copia porta `x-mri-cached-at`; se e' scaduta vince la rete; se la rete manca si serve la copia marcata `X-MRI-Cache: stale`. La cache dei tile e' separata da quella dell'app: la sua potatura non puo' sfrattare i WebP previsionali.
  - **Ultimi frame 30 minuti**: nuova cache `mri-frames-*` per tile radar DPC (S3), tile RainViewer e frame satellite, cache-first con TTL **30 minuti** e massimo **6 slot per famiglia** (tetto **2000 voci**, ~6 MB). Per il satellite, se lo slot richiesto non e' in cache e la rete manca, si serve il frame piu' recente della stessa sorgente con `X-MRI-Frame: <slot servito>`; per le tile radar questo fallback non c'e' (mescolare istanti diversi nella stessa griglia produrrebbe una mappa chimera).
  - **Pagina offline**: navigazione senza rete e senza html in cache -> `offline.html` precached, marcato `X-MRI-Cache: offline`; se manca anche quello, risposta inline 503 con lo stesso testo italiano. Precache all'install **solo** `index.html` + `manifest.webmanifest` + `offline.html`, tollerante (nessun `addAll`; nessun WebP, tile o frame scaricato all'install).
  - **Policy 1.2.4.56 intatta**: indici `index.json`/`timeline.json`/`color_scales.json` in network-first con timeout **2 s**, WebP previsionali cache-first, `request.cache` rispettato (`no-store` solo rete; `no-cache`/`reload` rete con fallback marcato), navigazione network-first **10 s**, copia di cache marcata `X-MRI-Cache: stale`. `data/radar/**` passa a stale-while-revalidate; `data/stations/**` resta pass-through per scelta.
  - **Test**: `node --check sw.js` -> 0; `test_sw_cache_policy.mjs` -> **124 PASS / 0 FAIL** (baseline **83 PASS / 0 FAIL**, +41 check); 4 asserzioni esistenti cambiate e dichiarate (A3, B2, B3, C4); manifest JSON valido; PNG verificati (magic + IHDR). `pytest scripts/tests/test_html_js_syntax.py` -> **1 passed**.
  - **Prova offline reale** (Edge headless 157 + CDP, server locale spento, browser rilanciato con proxy morto `127.0.0.1:9`): l'app si apre (`readyState=complete`, Leaflet e mappa presenti), **8/8 tile OSM** della zona visitata caricate dalla cache; frame satellite **57.400 B** e tile radar DPC **1.308 B** gia' visti -> **200** dalla cache; uno slot satellite mai visto (08:55) -> **200** con `X-MRI-Cache: stale` e `X-MRI-Frame: 2026-11-11T07-35-00Z` (fallback al piu' recente della famiglia); frame/tile con `x-mri-cached-at` azzerato -> **200** con `X-MRI-Cache: stale` (satellite 57.400 B, tile OSM 27.412 B, tile DPC 1.308 B); pagina offline -> titolo `MeteoRisk Italia - offline` e testo `Sei offline - i dati potrebbero essere vecchi`. Cache misurate: app **7 voci / 2.674.067 B**, tile **17 voci / 522.010 B**, frame **2 voci / 58.708 B**. **0 eccezioni JS, 0 `console.error`**; 6 richieste fallite dichiarate (metadata.json x3 e satellite/manifest.json e radar/latest.json chiesti dal client con `no-store`, `favicon.ico` inesistente). Layout: overflow orizzontale **0** a 1440x900 e 390x844. Marcatori: `#live-toggle-doppler` presente, `scAlertEvaluate` funzione, `ITALIA_AREAS` 6, fasce `pianura/collina/montagna`, `#forecast-details` con 3 tab.
  - **NON verificato** (dichiarato): iOS/Safari; installazione reale su telefono (`beforeinstallprompt`, splash screen, icona sulla home, standalone); quota reale del browser (i tetti sono in voci + stima dai byte misurati); TTL reale a 7 giorni/30 minuti (provato con time-travel sul timestamp, non aspettando); RainViewer nel browser (policy e CORS misurati, ma il frame reale e' stato esercitato su DPC); player satellite EUMETSAT (frame provato come `<img>` e fetch, non dal player); Leaflet offline (arriva dalla cache HTTP del browser, non dal SW); maskable icon; `data/stations/**` fuori scope per scelta.
- Contenuto della 1.2.4.76 (release sopra la 1.2.4.75) - **nowcasting fase 3: coni con gli orari di arrivo, frecce di movimento, allerta di prossimita' e geolocalizzazione**. Solo client: nessuna modifica a `scripts/`, ai workflow dei dati, a `color_scales.json`, alle classi di rischio, al dataset o al renderer; i workflow toccano solo le 5 righe (`cp` + commenti) del nome del file.
  - **Coni con gli orari**: le etichette non sono piu' solo `+30`: ogni vertice 30/60/90 min porta i minuti dallo scan e l'**ora UTC di arrivo** (`+30 min · 07:05Z`), calcolata da `scForecastArrivalClock` su `radar_timestamp + offset_min`. Il vertice 120 resta senza etichetta per non impilare quattro testi su un cono corto; il lato e' alternato (pari a sinistra, dispari a destra). Se `radar_timestamp` manca l'ora e' omessa e resta `+30 min`.
  - **Frecce di movimento**: per ogni struttura sull'ultimo frame con `motion_reliable` e track coerente, freccia dal centroide nella direzione del moto (`direction_toward_deg`), lunga **30 minuti di tragitto** alla velocita' osservata (`km = v x 0,5`), etichetta `v km/h → punto cardinale`. Nessuna freccia se il moto non e' affidabile o il track non e' coerente; i vettori si disegnano dopo i coni.
  - **Allerta di prossimita'**: punto scelto dall'utente (posizione del dispositivo o clic sulla mappa, segnaposto trascinabile), soglia **10/25/50 km** (default 25, `localStorage`), distanza **punto ↔ bordo del cono** (`scPointToPolygonKm`) e non dal centroide; `arrivo stimato = distanza / velocita'`, arrotondato a 5 minuti, e' una **stima cinematica, non una probabilita'**. Banner su controllo Leaflet in basso a sinistra (`pointer-events:none`, nessun suono) e, sotto 1280 px, lo stesso testo dentro il pannello (`#sc-alert-inline`) perche' il bottom-sheet coprirebbe il controllo mappa. `scAlertEvaluate` valuta ogni struttura sull'ultimo frame con cono disegnabile e dichiara il motivo di quelle senza cono riusando `scForecastLine`.
  - **Geolocalizzazione**: `navigator.geolocation.getCurrentPosition` con timeout **10 s**, `enableHighAccuracy` e `maximumAge 60 s`; i tre errori W3C (1 permesso negato, 2 posizione non disponibile, 3 timeout) hanno messaggi distinti e il codice sconosciuto un messaggio generico. Se il contesto non e' sicuro (serve HTTPS o localhost) il messaggio lo dice e resta il punto cliccabile; `localStorage` e geolocalizzazione sono protetti, un input invalido non imposta il punto, nessuna eccezione.
  - **Misure sulla copia locale statica con i dati reali del run 2026-11-11T06:35:00Z** (Edge headless CDP): 4 strutture, 4 `motion_reliable`, 4 `track_coherent`, 4 `forecast_usable` → **4 frecce, 4 coni, 12 etichette cono** (3 per cono: +30/+60/+90 con ora UTC); lunghezze frecce 14,9/20,0/22,1/19,6 km contro `v x 0,5` 14,9/20,0/22,15/19,6; etichette `30 km/h → W`, `40 km/h → NE`, `44 km/h → NE`, `39 km/h → E`. Allerta: fixture 12 km con soglia 25 → banner visibile `Sistema #36 a 12 km · arrivo stimato 40 min` (distanza 12,000 km), fixture 40 km → banner nascosto (40,000 km), `track_coherent=false` dentro il cono → banner nascosto e stato `1 senza cono (track non coerente)`. Geolocalizzazione: 6 casi → 0 eccezioni, timeout 10000 passato al browser.
  - **La 1.2.4.75 resta intatta**: primo caricamento 0 canvas Doppler e 0 `#btn-doppler`, `#live-toggle-doppler` nel pannello LIVE; dopo il click 1 canvas 754x640, 0 path e 0 `stroke-dasharray` nel pane (z 405); chiusura pannello → 0 canvas e preferenza `0`. Presenti anche le 6 aree, le fasce altimetriche e il pannello a tab. Overflow orizzontale 0 a 1440x900 e 390x844, 0 eccezioni JS e 0 `console.error`. Test: `node --check` 8/8, `test_html_js_syntax.py` 1 passed, suite `.mjs` 28 file → 25 PASS / 3 non-zero (gli stessi 3 nomi della baseline misurata su 1.2.4.75 PRIMA delle modifiche).
- Contenuto della 1.2.4.75 (release sopra la 1.2.4.74) - **area Doppler: superficie riempita unica al posto di 134 contorni tratteggiati, spenta di default e attivabile solo dal pannello LIVE**. Solo client: nessuna modifica a `scripts/`, ai workflow dei dati, a `color_scales.json`, alle classi di rischio, al dataset o al renderer; i workflow toccano solo le 5 righe (`cp` + commenti) del nome del file.
  - **Area, non contorni**: i 134 poligoni di `data/radar/doppler_coverage.geojson` non sono piu' 134 path con `stroke #ff3b30`, `dashArray "6 4"` e bordo per sito. Si riempiono tutti in **bianco pieno (opaco)** su un canvas **fuori schermo**: le sovrapposizioni non contano e il risultato e' una maschera. La maschera si tinge una volta (`source-in`, `#38bdf8`) e si compone **una sola volta** sulla mappa con `globalAlpha 0.13`: l'opacita' e' identica dove si sovrappongono piu' siti. Nessun bordo (un contorno esterno unico non e' ricavabile in modo pulito: meglio nessuna linea che linee sbagliate). Il canvas sta nel pane `z 405` (sopra il raster radar 400, sotto i bordi 410 e i marker 600), non intercetta il mouse e si ridisegna a `moveend`/`zoomend`/`resize`.
  - **Spenta di default e solo dal LIVE**: `#btn-doppler` e' **rimosso** dalla toolbar (non nascosto) e il controllo e' il terzo toggle del pannello LIVE, `#live-toggle-doppler` (`Area Doppler`), accanto a `Struttura convettiva` e `Centraline`. Con `meteorisk.dopplerCoverage.v1` assente il layer e' OFF: 0 canvas e 0 path al primo caricamento. Funziona con qualunque sorgente del selettore LIVE senza cambiare la voce; chiudendo il pannello il layer si spegne e la preferenza torna a `0`. La legenda `#live-doppler-box` compare solo con il layer acceso: `Area in cui esiste un radar con velocita' radiale (Doppler). Fuori, solo riflettivita': la rotazione non e' misurabile.` + `Fonti: EUMETNET/OPERA (CC BY 4.0)`.
  - **Misure sulla pagina servita in locale (Edge headless CDP)**: primo caricamento 0 canvas Doppler, 0 path, 0 `#btn-doppler`; dopo il click 1 canvas (754x640 px a 1440x900, 348x473 px a 390x844), 0 path e 0 `stroke-dasharray` nel pane; alpha letto dal canvas **33/255 in un punto coperto da 1 sito e 33/255 in un punto coperto da 3 siti** (differenza 0, a DPR 1 e DPR 2); marker delle strutture sopra l'area (pane marker `z 600` contro `z 405`, 10 marker in viewport a 1440x900, `elementFromPoint` restituisce il marker); overflow orizzontale 0 a 1440x900 e 390x844; 0 eccezioni JS e 0 `console.error`.
- Contenuto della 1.2.4.74 (release sopra la 1.2.4.73) - **pannello Struttura convettiva riscritto: composizione, posizione con provincia, moto osservato con i punti utili, cono solo se coerente, drawer laterale; via le parole che il dato non sostiene**. Solo client: nessuna modifica a `scripts/`, ai workflow dei dati, a `color_scales.json`, alle classi di rischio, al dataset o al renderer; i workflow toccano solo le 5 righe (`cp` + commenti) del nome del file.
  - **Riga riscritta**: `Sistema #17 · 7 celle · 32.025 km²` (o `Cella #43 · 1 cella · 305 km²`), `44,19°N 17,88°E · 277 km da Foggia · fuori Italia` oltre 50 km, `SOI 78/100 · organizzazione stimata (morfologia, senza Doppler)`, `Moto osservato: 64 km/h verso S 180° · 3/6 punti utili` oppure `Moto non determinabile · track non coerente`, `traccia 25 min · 6 frame · percorso 264 km` e descrittori con il contesto (`Hook 0 (nessun hook)`, `Strutt.V 0 (nessuna struttura verticale)`, `OT 0 (nessun OT)`, `Fulmini n/d` con il motivo). Se `cell_count`/`area_km2` mancano (run vecchie) la parte si **omette**: nessun `0`, nessun `n/d celle`. `distance_km` del motore e' la lunghezza del percorso, non la distanza da una provincia: quella e' calcolata sui capoluoghi di `regionsData`.
  - **Cono e badge**: il cono si disegna solo con `fit_used=true`, non clampato, track coerente, moto affidabile e direzione del fit concorde con il moto osservato entro 45°; altrimenti la riga dichiara il motivo. I badge di fenomeni compaiono solo se il loro scan dista al massimo 10 minuti da quello delle strutture; oltre, la nota dice `badge HH:MM (N min prima/dopo)`.
  - **Parole rimosse**: `rotazione`, `mesociclone`, `supercella`, `vortice` e `intenso` come severita' non compaiono nel pannello (misurato 0/0/0/0); il riepilogo dice `N organizzate` / `N molto organizzate`.
  - **Layout**: a >=1280 px drawer fisso a destra di 440 px, mappa 754x640 interamente visibile a 1440x900 e 0 marker sotto il pannello; sotto 1280 px bottom-sheet con `max-height: 44vh`, scroll interno e mappa sopra il foglio (390x844: pannello 371 px = 44%, mappa 472 px visibile, 0 marker coperti, overflow orizzontale 0).
  - **Celle tracciate**: lista separata e collassabile da `data/radar/tracks.json`, prime 10 per `organization_score`, nessun forecast e nessun colore di allerta, piede `95 celle tracciate · 31 con org. ≥50 · 64 sotto`. Il motore non pubblica il SOI per ogni cella (il SOI esiste solo per le strutture candidate): la lista dichiara `org.`, non `SOI`, e il `title` lo spiega.
  - **Misure sul run servito 2026-11-11T07:15:00Z** (4 strutture): 3 con `track_coherent=true`, 4/4 con composizione, 1 riga `Moto non determinabile · track non coerente`, 3 coni (solo i coerenti), 0/0/0/0 parole vietate. Sul run 22:55 (7 strutture, campi nuovi assenti): 6 righe `Moto non determinabile`, 0 composizioni (omesse, non `n/d`), 1 cono (solo #5). Test: `node --check` 8/8, `test_html_js_syntax.py` 1 passed, suite `.mjs` 26 PASS / 2 FAIL preesistenti (stessi nomi della baseline).
- Contenuto della 1.2.4.73 (release sopra la 1.2.4.72) - **sei macro-aree (Sicilia e Sardegna separate) e temperature per fascia altimetrica, trend dentro la stessa fascia, fix `0-0 mm`**. Solo client: nessuna modifica a `scripts/`, ai workflow dei dati, a `color_scales.json`, alle classi di rischio, al dataset o al renderer; i workflow toccano solo le 5 righe (`cp` + commenti) del nome del file.
  - **Sei aree**: `ITALIA_AREAS` passa da 5 a 6 (Nord, Centro Tirreno, Centro Adriatico, Sud, Sicilia, Sardegna); `Isole maggiori` non esiste piu. Le frasi giornaliere sono 18 (6 x 3) e le settimanali 6; badge `6 macro-aree`; le due occorrenze storiche di `Isole maggiori` restano solo nel changelog in-app delle release 1.2.4.68 e 1.2.4.70.
  - **Fasce altimetriche**: pianura <300 m, collina 300-800 m, montagna >800 m; minime e massime per fascia, `n` province nel `title`, fasce con meno di 3 province omesse, una provincia puo' stare in piu' fasce. Esempio Nord 2026-11-11: pianura 5-19 C (37), collina 5-14 C (15), montagna -1-13 C (19); la montagna e' l'unica fascia sotto zero e la frase dice `Gelate solo in montagna (minima -0,8 C)`.
  - **Trend**: confronto dentro la stessa fascia (massima con massima, minima con minima), una sola variazione per frase (la piu' forte) per restare sotto le 60 parole. Il summary della provincia usa la fascia dominante della provincia (piu' punti) e aggiunge `(montagna ...)` solo se la minima di montagna e' sotto zero.
  - **Fix**: `italiaFmtRange` non produce piu' `0-0 mm` nel caso degenere ma `0 mm`.
- Contenuto della 1.2.4.72 (release sopra la 1.2.4.71) - **disclaimer potenziale/NWP una volta sola, confine Doppler sempre visibile e disegnato di default, legenda fulmini morta rimossa**. Solo client: nessuna modifica a `scripts/`, ai workflow dei dati, a `color_scales.json`, alle classi di rischio della 1.2.4.71, al dataset o al renderer; i workflow toccano solo le 5 righe (`cp` + commenti) del nome del file.
  - **Niente disclaimer ripetuto**: la riga `Potenziale ambientale (modello NWP): condizioni favorevoli previste, non un evento osservato` non e piu nel corpo della scheda provincia (`#zone-risk-origin` rimosso) ne nei tooltip (badge della scheda, badge sulla mappa, dischi dei rischi, barra riepilogo). La distinzione potenziale/evento resta una sola volta nella legenda badge e nel pulsante `?`. Nel tooltip resta `Colore dovuto a: ... (livello N)`; `osservato dal radar` compare solo se il livello deriva da una cella radar osservata (oggi la scala provincia e solo NWP, quindi non compare).
  - **Confine Doppler**: `#btn-doppler` non e piu `display:none` e non dipende dal player radar; il confine (`data/radar/doppler_coverage.geojson`, 134 poligoni) si carica all'apertura della mappa anche con provider `rainviewer`, con preferenza `meteorisk.dopplerCoverage.v1` in `localStorage`. Su 404/errore il pulsante e disabilitato con il motivo nel `title` e nessuna eccezione.
  - **Legenda fulmini**: rimossi dal pannello LIVE il blocco `Legenda fulmini (eta delle scariche)` (intestazione collassabile + 4 pastiglie eta) e l'hint `#live-hint`; la pastiglia `Fulmini n/d` delle righe candidato non viene piu disegnata quando il valore manca.
- Contenuto della 1.2.4.71 (release sopra la 1.2.4.70) - **classi di rischio ricalibrate: scala R2 con le raffiche, merge per modello, potenziale distinto dall'evento; mantiene i pannelli a tab della 1.2.4.70**. Solo client: nessuna modifica a `scripts/`, ai workflow dei dati, a `color_scales.json`, alle formule o ai calcoli; i workflow toccano solo le 5 righe `cp` del nome del file.
  - **Scala R2**: 4 = pioggia >45 mm o raffiche >100 km/h o (codice >=95 e pioggia >=10 mm e CAPE >=300 J/kg); 3 = pioggia >15 mm o raffiche >75 km/h o (codice >=95 e pioggia >=3 mm e CAPE >=150 J/kg) o (codice 80-90 e pioggia >=8 mm); 2 = probabilita >60% o pioggia >1 mm o raffiche >50 km/h; 1 = probabilita >20% o pioggia >0,2 mm. Le congiunte convettive sono valutate ora per ora sullo stesso modello; la classe 5 resta il mesociclone con innesco.
  - **Vento sulle raffiche**: `windgusts_10m` con soglie 50/75/100 km/h. La vecchia `wind >60` non scattava mai (media massima 24,6 km/h contro raffiche 89 km/h). Misurato il 2026-11-10: 3,9% delle province-giorno sopra 75 km/h, 0,3% sopra 100 km/h.
  - **Merge per modello**: livello calcolato per modello e poi massimo; prima il CAPE poteva venire da ECMWF e shear/SRH da `best_match` a ore diverse (4 delle 5 viola di oggi avevano questa incoerenza). ECMWF escluso dalla dinamica: 0 valori non-null su 7.704 ore-punto di vento ai livelli di pressione (0 su 18.504 in 3 giorni).
  - **Potenziale non e evento**: convezione organizzata come classe distinta `#7d93a8`; viola e rosso solo con innesco alla stessa ora; rimossi i gate senza fonte (WMS3 >=500), EHI >=1,0 declassato a display, CAPE saturato a 800 J/kg, veto RH <50% alle quote medie con motivo nel tooltip. Etichette organizzati/celle organizzate solo se il gate passa.
  - **Tooltip e misure**: riga `Colore dovuto a: ... Altri rischi: ...`. Sul dataset 2026-11-10: viola 5 -> 0, rosso 19 -> 3, arancione o superiore 63 -> 32 (29,9%), badge temporali organizzati 8 -> 1; le tre rosse rimaste sono LU, MS e PT con pioggia >45 mm.
  - **Pannelli a tab**: restano `#forecast-details` con le 3 tab, il summary di una riga, le frasi senza toponimi e le raffiche p50-p90 della 1.2.4.70.
- Contenuto della 1.2.4.70 (release sopra la 1.2.4.69) - **un solo pannello previsione con 3 tab (Provincia / Aree / 7 giorni), chiuso di default; frasi senza toponimi e senza conteggi; raffiche p50-p90 per modello; cloud_cover e snowfall_water_equivalent reali**. Solo client: nessuna modifica a `scripts/`, ai workflow dei dati, a `color_scales.json`, alle formule o ai calcoli; i workflow toccano solo le 5 righe `cp` del nome del file.
  - **Pannello unico**: `#forecast-details` chiuso di default, summary di una riga con i 3 giorni della provincia selezionata (es. `oggi pioggia 14 mm, max 18 °C · domani pioggia 0,4 mm, max 20 °C · dopodomani pioggia 9 mm, max 20 °C`), 3 tab con `role=tab`/`aria-selected`, frecce sinistra-destra/Home/End, persistenza in `localStorage` (`mri.forecast.tab.v1`, try/catch). Una sola pane visibile: Provincia (3-giorni invariato), Aree (5 macro-aree), 7 giorni (7 celle + 5 frasi). Le 7 celle e le frasi stanno dentro la pane, quindi il pannello chiuso non riserva altezza.
  - **Frasi senza toponimi**: le 15 frasi giornaliere e le 5 settimanali non citano province, regioni o località; i nomi e i conteggi restano solo nei `title`. Rimossi "sulle province interessate" e "con il massimo di X a Y"; il "dove" è un settore generico ("settore tirrenico", "pianura padana", "settore occidentale"). Le frasi sono autosufficienti (cielo e temperature ogni giorno) e misurano 27-50 parole (max 60).
  - **Raffiche p50-p90**: per ogni area/giorno le massime giornaliere per provincia, con p50/p90/p99 separati su `best_match` e `ecmwf_ifs`; il testo usa p50-p90, cita "punte oltre 100 km/h" solo se p99 ≥ 100 (e il range non supera già 100), dichiara la discordia solo se i due p90 differiscono di oltre il 30%. Nel dataset 2026-11-10 la discordia scatta solo per Centro Tirreno oggi (84 vs 58 km/h, 31,0%).
  - **Cielo e neve reali**: cielo da `hourly.cloud_cover` (sereno <20, poco nuvoloso 20-40, nuvoloso con schiarite 40-70, molto nuvoloso 70-90, coperto >90), fallback sul `weathercode` dichiarato nel commento; neve solo con `hourly.snowfall_water_equivalent` > 0 nelle 24 h, con quota (`freezing_level_height` − 500 m, ±300 m) e mm equivalenti. Nel dataset 2026-11-10 la neve compare solo su Nord (Sondrio 0,2 mm oggi, Verbano-Cusio-Ossola 0,4 mm domani, Aosta 26,0 mm dopodomani).
- Contenuto della 1.2.4.69 (release sopra la 1.2.4.68) - **sintesi in stile bollettino e tendenza settimanale per macro-aree, zero conteggi di province nel testo visibile**. Solo client: nessuna modifica a `scripts/`, ai workflow dei dati, a `color_scales.json`, alle formule o ai calcoli; i workflow toccano solo le righe `cp` del nome del file.
  - **15 frasi giornaliere (5 aree x 3 giorni).** Ogni frase dice: cielo (da `weathercode` 0/1/2/3, perche' `cloud_cover` non e' nel dataset: fallback dichiarato nella nota), fenomeno con classi misurate (debole >1 e <=14,5 mm, diffusa <=39,3, intensa oltre; rovesci se >=50% delle province bagnate ha un'ora 80-82/95-96/99 e il massimo supera 14,5 mm), range p25-p75 delle province bagnate e massimo con provincia e regione, fascia piu' piovosa (0-6/6-12/12-18/18-24) con l'ora di picco, temperature con minima e massima dell'area e trend di massime/minime rispetto a ieri (stazionarie se |delta| < 1,5 C), vento con direzione dominante (media vettoriale pesata su `wind_speed_10m`, 8 punti cardinali) e classe sulle raffiche (debole <28,6, moderato <=52,9, teso <=94,1, forte oltre km/h), range e provincia del massimo, quota neve (`freezing_level_height` - 500 m, scarto +/-300 m) solo con precipitazione o sotto i 2.000 m. Nessuna soglia di allerta, nessun consiglio. I conteggi di province restano solo nei `title`.
  - **5 frasi settimanali (una per area) da `weekly.json`.** Giorni che cambiano (run asciutti/piovosi), picco con range p25-p75 e massimo con provincia, massime in calo/rialzo tra due giorni (mediana delle massime dell'area), raffiche tese solo se superano 52,9 km/h. 25-45 parole per frase; nessun conteggio di province. Le 7 mini-celle per provincia restano invariate.
- Contenuto della 1.2.4.68 (release sopra la 1.2.4.67) - **sintesi nazionale per macro-aree e riepilogo settimanale nel pannello destro**. Solo client: nessuna modifica a `scripts/` (motore, renderer, dataset), ai workflow dei dati, a `color_scales.json`, alle formule o ai calcoli; i workflow toccano solo le righe `cp` del nome del file.
  - **`#italia-synth` per macro-aree.** Niente piu' estremi provinciali: per Oggi / Domani / Dopodomani una frase per ognuna delle 5 aree (Nord 47 province, Centro Tirreno 17, Centro Adriatico 11, Sud 18, Isole maggiori 14), con carattere (asciutto / pioggia debole / pioggia diffusa / rovesci / pioggia intensa), numero di province interessate su quelle con dato, neve, raffiche e range delle massime. Soglie dichiarate e misurate sulle 321 province-giorno del dataset 2026-11-10: debole <= 14,5 mm (p75), diffusa <= 39,3 mm (p90), intensa oltre; rovesci con >= 50% delle province bagnate e un'ora 80-82/95-96/99; vento >= 65 km/h (p75); caldo >= 25,0 C (p95), freddo <= 10,8 C (p10). La nota "sintesi dei punti provincia, non di un campo continuo" resta.
  - **`Prossimi 7 giorni`.** Nel pannello destro, dopo la sintesi a 3 giorni: 7 mini-celle NON cliccabili (giorno, icona WMO da `wmoToSvg`, max/min, prcp mm) e una frase nazionale deterministica (giorno piu' piovoso con numero di province, calo o risalita delle massime con i due valori). Fonte `data/latest/weekly.json` (schema `meteorisk.weekly/1`), scaricata UNA volta per sessione solo quando il pannello della provincia e' visibile e in modo non bloccante; 404 o payload malformato -> "tendenza non disponibile" senza eccezioni. Su mobile la riga scorre dentro `.wk-scroll` (`-webkit-overflow-scrolling: touch`), senza overflow della pagina.
  - **Cache invariata.** La sintesi a 3 giorni resta cachata per (dataset, provincia) e non viene ricalcolata a ogni frame dello slider; il weekly e' statico per sessione.
- Contenuto della 1.2.4.67 (release sopra la 1.2.4.66) - **sintesi nazionale Italia a larghezza piena, calcolata una volta per dataset**. Solo client: nessuna modifica a `scripts/` (motore, renderer, dataset), ai workflow dei dati, a `color_scales.json`, alle formule o ai calcoli; i workflow toccano solo le righe `cp` del nome del file.
  - **Blocco `#italia-synth`.** Card a larghezza piena (`grid-column: 1 / -1`) come seconda riga della grid `.container`, dopo `details-card`: toolbar, `map-card`, `#leaflet-map` e il pannello destro non si spostano. Tre colonne **Oggi / Domani / Dopodomani** (a <=600 px restano tre colonne piu' compatte, con font ridotto) aggregate dalle **107 province** del dataset derivato.
  - **Cosa mostra ogni colonna.** Province con pioggia **>1 mm** e **>10 mm** e massimo con la provincia; massima e minima nazionale con la provincia; raffica massima con la provincia; province con **CAPE sopra 200 J/kg** (soglia = **75° percentile pooled** misurato sulle 321 province-giorno, valore grezzo 190 arrotondato) con il massimo; province con **neve** per codice (71/73/75/77/85/86) con accumulo **n/d** (non e' nel dataset); ripartizione **Nord / Centro / Sud** da `regionsData.region` (**47 / 22 / 38** province su 107, il Sud include le Isole).
  - **Solo dati reali, zero rete.** Il calcolo usa i punti di `data/latest/meteorisk-points.json` (257 punti, `best_match`) dentro `applyStaticDataset`: **una volta per dataset**, nessuna richiesta aggiuntiva, nessun modello AI, nessun file nuovo. Ogni metrica dichiara **"N su M con dato"** ed esclude le province senza dato; nessun valore inventato.
  - **Frase legata al giorno selezionato.** La frase sopra le colonne segue il giorno scelto: `selectDay` chiama solo `renderItaliaSentence()`, senza ricalcolare i punti del dataset.
- Contenuto della 1.2.4.66 (release sopra la 1.2.4.65) - **sintesi deterministica a 3 giorni nel pannello destro**. Solo client: nessuna modifica a `scripts/` (motore, renderer, dataset), ai workflow dei dati, a `color_scales.json`, alle formule o ai calcoli; i workflow toccano solo le righe `cp` del nome del file.
  - **Blocco `#three-day-summary`.** Sotto la riga del rischio, tre colonne **Oggi / Domani / Dopodomani** (a <=600px tre righe, una card per giorno con i valori su due colonne interne, mai schiacciate in tre colonne da ~100 px). Per ogni giorno: temperatura massima e minima con l'ora del picco, pioggia totale con probabilità massima e fascia contigua con precipitazione > 0,1 mm (o "assente"), raffica massima e vento medio massimo con l'ora della raffica, CAPE massimo con il numero di ore sopra soglia, quota zero termico media e neve (equivalente acqua) se il campo esiste nel dataset.
  - **Solo dati reali, zero rete.** Il calcolo usa i campi già presenti in `data/latest/meteorisk-points.json` (72 ore x 257 punti, `forecast_days=3`): nessuna richiesta aggiuntiva, nessun modello AI, nessun testo generato. Le frasi di sintesi sono template deterministici. Un campo null su tutte le ore mostra **n/d**, mai 0; se è null solo in alcune ore si usano le ore valide e, sotto 18/24, il dettaglio dichiara quante ore sono state usate.
  - **Arrotondamenti dichiarati.** Temperatura 0,1 °C; pioggia 1 mm (0,1 mm sotto 1 mm); vento 1 km/h; CAPE 1 J/kg; quota zero termico 100 m (media del giorno); neve 0,1 mm.
  - **Soglia CAPE misurata.** 300 J/kg, scelta sulla distribuzione del dataset del 2026-11-10 (18.504 ore-punto): mediana 0, p95 170, p98 330, p99 440 J/kg; ore con CAPE >= 300 = 413 (2,23%), punti-giorno con almeno un'ora sopra = 104 su 771 (13,5%). La nota fissa del blocco dichiara che è **CAPE previsto dal modello NWP, non un temporale osservato**.
  - **Cache per (località, dataset).** Il risultato è memorizzato sullo store della località e ricalcolato solo quando cambia `datasetState.generatedAt` o la località: nessun ricalcolo a ogni frame dello slider. La frase si aggiorna cliccando una delle tre colonne, senza ricalcolare i numeri.
- Contenuto della 1.2.4.65 (release sopra la 1.2.4.64) - **confine di copertura Doppler e legenda delle fonti radar**. Solo client: nessuna modifica a `scripts/` (motore, renderer, dataset), ai workflow dei dati, a `color_scales.json`, alle formule o ai calcoli; i workflow toccano solo le righe `cp` del nome del file.
  - **Confine Doppler.** Nuovo pulsante `#btn-doppler` nel player radar, spento di default e visibile **solo** con il radar attivo (`isRadarActive === true`). Il confine viene letto da `data/radar/doppler_coverage.geojson` (**134 poligoni**, 17.051 punti, 397.313 byte) e disegnato con `L.geoJSON` su un pane dedicato `dopplerPane` con `zIndex 405`: sopra il raster radar (400), sotto i confini amministrativi (410). Stile: `#ff3b30`, weight 1.4, opacity 0.9, `dashArray "6 4"`, fillOpacity 0.035, `interactive:false`. Il valore del pulsante e' `Doppler: ON` / `Doppler: OFF`; la preferenza resta al reload in `localStorage` chiave **`meteorisk.dopplerCoverage.v1`** (`"1"`/`"0"`). Spegnendo il radar il layer sparisce e il pulsante torna nascosto. Misure CDP (Edge headless): **134** path con `stroke-dasharray` e **134** con stroke `#ff3b30` a radar acceso, **0** a radar spento; ON -> OFF -> ON = 134 -> 0 -> 134; reload con preferenza OFF = **0** path, con preferenza ON = **134**; **0 eccezioni JS** e **0 `console.error`**. Primo disegno una tantum: mediana **28,5 ms**, peggior caso **53,1 ms** a 1440x900, **41,1 ms** a 390x844; nessun costo per frame.
  - **Legenda fonti e badge sorgente.** Sotto la mappa compare `#radar-legend` con le fonti effettive (DPC Italia e OPERA CIRRUS / EUMETNET, CC BY 4.0), la percentuale di dominio con eco per ciascuna, il layer visibile e lo stato della fusione. La riga del MAE mediano dichiara valore e metodo: mediana degli MAE per anello di distanza (11 anelli con coppie su 3 frame, 85.569 coppie totali) -> **3,44 dBZ**, con la nota "Differenza fra prodotti, non un errore". Il badge `#sc-source-badge` nel pannello Struttura convettiva mostra la sorgente del record: `DPC + OPERA CIRRUS` (classe `ok`) con `opera.status = "ok"`, `Solo DPC: OPERA non raggiungibile` (classe `warn`) con `unavailable`, piu' uno stato `partial`. Misure: con il `latest.json` reale del run 2026-11-10T15:05:39Z il badge e' `ok` e la legenda mostra DPC **4.84%** e OPERA **11.33%** del dominio con eco; con la fixture `unavailable` il badge passa a `warn` e la legenda dichiara solo DPC. La legenda si aggiorna al cambio frame senza richieste aggiuntive.
- Contenuto della 1.2.4.64 (release sopra la 1.2.4.63) - **guida "?" sempre visibile, tooltip in parole, legende collassabili con memoria**. Solo client: nessuna modifica a `scripts/` (motore, renderer, dataset), ai workflow dei dati, a `color_scales.json`, alle formule o ai calcoli; i workflow toccano solo le righe `cp` del nome del file.
  - **Pulsante "?".** `#help-btn` fisso in alto a destra (34x34, `z-index: 11500`, `top/right: 8px`), fuori dal flusso: nessun contenitore si sposta. Misure CDP: in viewport con `elementFromPoint` = `help-btn` a **1440x900** (1398,8 34x34), **390x844** (348,8 34x34) e **360x640**; **zero** sovrapposizioni con controlli interattivi e con i glifi dell'header (ultimo glifo della riga 2 del titolo a x=243, paragrafo da y=60). Fra 768 e 1279 px scende a `top: 86px`, dove la toolbar finisce a x=1036.
  - **Modale.** Tre sezioni in italiano semplice: (1) i sei gradini del colore province con le soglie del codice; (2) potenziale del modello NWP vs radar osservato, con la frase **"Potenziale alto non significa temporale in corso"** e un esempio con CAPE 1.800 J/kg; (3) ordine di lettura del pannello destro in 5 passi. Chiusura con **Esc**, click sullo sfondo e **X**; focus trap elementare; `role="dialog"`, `aria-modal="true"`. Misure: **1440x900** card 680x552 senza scroll; **390x844** card 366x804, corpo **710/710 senza scroll** e zero scroll orizzontale (364/364); **360x640** card 336x616 con **221 px** di scroll interno dichiarato.
  - **Tooltip.** `title` che spiegano il valore in parole: badge di rischio (`Rischio Alto / Estremo — livello 4 di 5 (Estremo): Potenziali fenomeni pericolosi. Potenziale ambientale del modello (NWP): ...`), indicatore grandine (CAPE con la fascia, WMAXSHEAR, SHIP, shear), chip SOI (`SOI 71/100 (nucleo, forma e persistenza); da 65 il record viene pubblicato; non e una probabilita`), badge sulla mappa (livello + significato + origine). Misurati **65 title informativi su 78**, **14 chip SOI** e **3 badge mappa** nel DOM.
  - **Legende collassabili.** Tre intestazioni generate (`#sc-legend`, `#live-legend`, `#fm-legend`) più le due esistenti (`#badge-legend-toggle`, previsioni/fonti): click per comprimere, titolo sempre visibile (head 32 px), stato in `localStorage` chiave **`meteorisk.legendCollapse.v1`**. Verificato: collassata -> reload -> ancora collassata; riespansa -> reload -> espansa.
  - **Tap su mobile.** I `title` non esistono al tocco: tap su `#zone-badge` (o `#hail-indicator`) apre un popover (**340x66**) con lo stesso testo, il modale resta chiuso (`modalHidden: true`) e si chiude toccando fuori.
  - **Eccezioni.** 0 eccezioni JS; unico 404 di rete `favicon.ico`. Suite `.mjs` 26 PASS / 2 FAIL (stessi due nomi della baseline) e `test_html_js_syntax.py` verde.
- Contenuto della 1.2.4.63 (release sopra la 1.2.4.62) - **la ricerca comuni normalizza la query**. Solo client: nessuna modifica a `scripts/` (motore, renderer, dataset), ai workflow dei dati o a `color_scales.json`; i workflow toccano solo le righe `cp` del nome del file.
  - **Difetto.** Dopo una selezione il campo contiene `Nome (Regione)` e la geocoding API non risolve la parentesi: sulla 1.2.4.62 il campo precompilato `Roma (Lazio)` + Invio faceva **1 richiesta con `name=Roma (Lazio)`** e tornava «Nessun comune trovato».
  - **Fix.** Una funzione unica `citySearchQuery(raw)`, usata da **tutti** i percorsi (`oninput` dentro `handleSearch`, Invio, pulsante Cerca, evento nativo `search` dentro `runCitySearch`), toglie **solo un gruppo finale tra parentesi**, comprime i doppi spazi e trimma. Le parentesi non finali restano. Tabella verificata con richiesta reale: `Roma (Lazio)` -> `Roma` (1 richiesta, 1 risultato); `Milano (Lombardia)` -> `Milano` (1 richiesta, 2 risultati); `Reggio nell'Emilia (Emilia-Romagna)` -> `Reggio nell'Emilia` (1 richiesta, 1 risultato); `Rocca (TE) Centro` -> invariato (1 richiesta, 0 risultati); `  Roma  ` -> `Roma` (1 richiesta, 1 risultato); `()` -> stringa vuota (**0 richieste**, nessun errore).
  - **Caso reale.** 1.2.4.62 servita: prefill senza eventi + Invio -> **1 richiesta `name=Roma (Lazio)`, 0 risultati**. 1.2.4.63: **1 richiesta `name=Roma`, 1 risultato `Roma (Lazio)` selezionabile**; identico col click sul pulsante (focus che resta su `#city-search`). I 6 casi della 1.2.4.62 restano verdi. Mobile 390x844 DPR 2: riga **344 px** (input 294 + gap 6 + pulsante **44x44**), `scrollWidth` **390**, mappa **y=369 h=473 bottom=841 invariata**.
- Contenuto della 1.2.4.62 (release sopra la 1.2.4.61) - **ricerca comuni con gesto esplicito: pulsante Cerca e Invio**. Solo client: nessuna modifica a `scripts/` (motore, renderer, dataset), ai workflow dei dati o a `color_scales.json`; i workflow toccano solo le righe `cp` del nome del file.
  - **Pulsante Cerca.** `#city-search` aveva solo `oninput`: nessun pulsante, nessun gesto esplicito. Ora c'e' un pulsante con `aria-label="Cerca comune o provincia"` e icona lente nella stessa riga, a destra, `mousedown` con `preventDefault()` per non rubare il focus all'input. Misure CDP: **390x844 DPR 2** riga **344 px** (input 294 + gap 6 + pulsante **44x44**), `scrollWidth` **390** (zero overflow), `#leaflet-map` **y=369, h=473, bottom=841 invariata**; **1440x900** riga **362 px** (input 314 + gap 8 + pulsante **40x38**), mappa **135,201 754x640 invariata**. Click reale: **1 richiesta**, focus che resta su `#city-search`.
  - **Invio esplicito.** `keydown` su `Enter` e `NumpadEnter` legge `el.value` a runtime, chiama `preventDefault()` e rilancia `handleSearch(query,{explicit:true})`; **nessuna guardia `lastQuery === query`** (verificata assente nel codice), quindi due Invii fanno due richieste. Caso del difetto misurato: sulla 1.2.4.61 campo precompilato `Roma (Lazio)` senza eventi (autofill) + Invio -> **0 richieste, lista none**; sulla 1.2.4.62 -> **1 richiesta, lista block**. Doppio Invio = **2**, `NumpadEnter` = **1**, evento nativo `search` = **1** (soppresso entro 350 ms dal keydown: keydown+search = **1**), campo vuoto = **0 richieste e nessuna eccezione**, `zzzz` = **1 richiesta con messaggio esplicito**. Al `DOMContentLoaded` nessuna ricerca automatica con valore gia' presente.
- Contenuto della 1.2.4.61 (release sopra la 1.2.4.60) - **chiusura LIVE senza etichette residue e mobile misurato**. Solo client: nessuna modifica a `scripts/` (motore, renderer, `build_meteorisk_dataset.py`), ai workflow dei dati o a `color_scales.json`; i workflow toccano solo le righe `cp` del nome del file.
  - **Difetto 1: chiusura del pannello LIVE.** Sulla 1.2.4.60 il ramo else di `toggleLivePanel()` chiamava `stopLivePanel()` ma non `stationsSetActive(false)`: `liveShowStations` restava `true`, il canvas delle centraline (pane z 560) restava sulla mappa (696x946, 52.200 pixel dipinti) e `stationsAgeTimer` continuava a girare. Ora la chiusura azzera lo stato, toglie la classe `on` da `#live-toggle-stations` e chiama `stationsSetActive(false)`: misurati **0 pixel dipinti**, canvas assente dal pane, **0 richieste** a `data/stations/*` nei 30 s, select `#mnw-source-select` invariato. Alla riapertura lo stato e' OFF (default di sessione).
  - **Difetto 2: mobile.** Misure CDP a 390x844 DPR 2 e 360x640 DPR 1: la mappa partiva a **y=497** (h 473/358) per 232 px di toolbar in 3 righe; il pannello LIVE in flusso la spingeva a **y=813**; tap target <32 px **5/23** (chiuso) e **15/33** (polare, zoom 26x26 a y=934 fuori viewport); testo <11 px **54/95**. Dopo: toolbar in una riga scorrevole da 44 px (232 -> **114**), mappa a **y=369** (bottom 842 a 390x844), LIVE fisso in basso con `overflow-y:auto` che non sposta la mappa, tap target <32 px **0/23** e **0/33** (zoom 32x32 a y=798), testo <11 px **8/95**. Disco polare invariato: diametro **334,1 px CSS = 0,96*348**. Desktop 1440x900 identico (mappa 135,201 754x640; toolbar 120,82 1200x42; tap 15/23; testo 28/95).
- Contenuto della 1.2.4.60 (release sopra la 1.2.4.57) - **modelli coperti letti dal dataset, perimetro Pro dichiarato, cache del confronto per localita'**. Solo client: nessuna modifica a `scripts/` (motore, renderer, `build_meteorisk_dataset.py`), ai workflow dei dati o a `color_scales.json`; i workflow toccano solo le righe `cp` del nome del file.
  - **La lista dei modelli coperti non e' piu' cablata.** Si legge da `metadata.json` -> `models_covered` + `models_derived` a ogni caricamento (fallback all'array statico solo se il campo manca) e si usa in `initWeatherData`, `loadStaticDataset`, `applyStaticDataset`, nel cambio modello, nel pannello confronto, nell'Ensemble e nel grafico. Lo store di provincia di ogni modello coperto si costruisce dal payload gia' scaricato: `points[].models[modello]` nel payload a punti, `series`/`daily` + `model_series[modello]`/`model_daily[modello]` nel payload compatto (stessa forma, nessuna trasformazione dei valori); `dual_best_ecmwf` resta il merge client-side di `best_match` + `ecmwf_ifs`.
  - **Perimetro Pro dichiarato.** Il dataset pubblica `best_match`, `ecmwf_ifs` e il dual derivato; `gfs_seamless` e `italia_meteo_arpae_icon_2i` restano sul percorso API per scelta di perimetro, non per un guasto, e il pannello lo scrive ("disponibile solo in Sviluppo: il dato non e' nel dataset") con richieste stimate e peso. Il primo 429 apre un **gate unico** con countdown a schermo che ferma i cicli live: nessuna attesa silenziosa di 60 s per coordinata e nessun retry a raffica.
  - **Orizzonte derivato dagli array.** 72 h = 3 giorni (48 h = 2): day mapping, pulsanti giorno, pannello confronto e grafico leggono la lunghezza reale delle serie, e il grafico non inventa ore oltre la fine dell'array.
  - **Confronto modelli: 0 richieste cambiando giorno.** La chiave di cache non contiene piu' il giorno (il record contiene gia' tutti i giorni disponibili), quindi cambiare giorno con la stessa localita' costa 0 richieste invece di 1.
- Contenuto della 1.2.4.57 (release sopra la 1.2.4.56) - **dataset prima dell'API in ogni
  pannello, confronto modelli in cache, slider con debounce**. Solo client: nessuna modifica a
  `scripts/` (motore, renderer, `build_meteorisk_dataset.py`), ai workflow dei dati o a
  `color_scales.json`; i workflow toccano solo le 3 righe `cp` del nome del file.
  - **Confronto modelli: da 4 richieste per azione a 1-2, e 0 a parita' di chiave.** Misure
    della 1.2.4.56 (Edge headless, cache disabilitata): 4 richieste per **ogni** `updateUI()`
    (apertura, cambio giorno, un passo di slider, click provincia) — ECMWF daily, GFS daily,
    ECMWF hourly, GFS hourly — tutte a `forecast_days=7` mentre il pannello ne mostra 3; le
    stesse 4 URL partite **5 volte in 11 minuti = 20 richieste, 4 uniche** (80% sprecate); un
    drag completo dello slider `0->24` = **96 richieste**. Ora `daily` e `hourly` viaggiano
    nella **stessa** chiamata, **una richiesta per modello**, `forecast_days=3`, **cache** per
    chiave `(lat, lon, giorno, modelli)` con TTL **10 minuti** e **promise condivisa** se la
    stessa chiave e' gia' in volo.
  - **Gerarchia dataset -> API, dichiarata.** Prima il dataset gia' pubblicato su GitHub
    (`data/latest`, 9.294.452 B, `models_covered` = `best_match` + `ecmwf_ifs`,
    `forecast_days` = 3, 257 punti): il pannello prende **ECMWF dal payload gia' scaricato**
    (0 richieste) e sulla rete resta solo **GFS**, che il dataset **non copre** (lacuna
    dichiarata nel pannello, non errore). La **run operativa GFS** dell'Ensemble (deterministica
    `gfs_seamless`, duplicato di `best_match`) legge il payload; resta API solo `ensemble-api`,
    che non ha equivalente locale. Il grafico orario usa il payload per le province e l'API solo
    per le localita' fuori dataset (es. La Caletta). I **5 campi daily** e i **5 hourly** del
    pannello sono verificati contro il payload (chiave mancante -> record scartato e ricaduta
    sull'API), non assunti.
  - **Niente percorso live quando il dataset esiste, e 429 con attesa.** Un errore transitorio
    (rete, timeout, 5xx, payload incompleto) ritenta **una volta dopo 1 s** e **non** fa partire
    le 257 richieste (misurato sulla 1.2.4.56: pulsante Aggiorna -> **257 richieste, peso 2.808,
    406 s**); si passa all'API solo con **404** o **status != live**, e allora il percorso live
    parte con i **soli 107 capoluoghi** (peso dichiarato nel messaggio) invece di 257, con tutti
    i punti solo su un secondo click esplicito. Sul **429** il pannello rispetta l'attesa letta
    dal `reason` dell'API (fallback 60 s) e mostra «Limite API raggiunto, riprovo tra Ns»
    invece di ripartire al successivo `updateUI` (1 tentativo, nessun backoff: misurato).
  - **Slider orario: debounce 300 ms su `input`, commit immediato su `change`.** Un drag
    completo non chiama piu' `updateUI()` a ogni passo; a parita' di chiave il confronto
    modelli non fa richieste.
- Contenuto della 1.2.4.56 (storico, release sopra la 1.2.4.55) - **tre cause di cache, misurate**:
  - **CAUSA 1 - il Service Worker ignorava `request.cache`.** Il client chiede la timeline con
    `cache: 'no-store'` e l'index con `cache: 'no-cache'`, ma `staleWhileRevalidate` rispondeva
    `if (hit) return hit;` **prima** della rete: con un `index.json` vecchio in CacheStorage il
    player chiedeva gli 8 frame di una run gia' potata, tutti 404, e la mappa restava vuota
    («Nessun frame caricabile: 8 tentativi consecutivi falliti» - misura della diagnosi). Ora
    `request.cache` ha la priorita' su ogni regola: `no-store` = solo rete (la cache non si legge
    nemmeno in errore), `no-cache`/`reload` = rete sempre e cache ammessa solo in errore/5xx,
    marcata `X-MRI-Cache: stale`; la risposta ok aggiorna la cache. La navigazione e' l'eccezione
    dichiarata (un reload puo' arrivare con `reload`): resta network-first con timeout e cache.
    In piu' gli indici aggregati (`index.json`, `timeline.json`, `color_scales.json`) passano a
    **network-first con timeout 2000 ms** per le richieste a cache di default. **MISURATO in
    browser: 2.021 ms** con header `stale`, contro i **10,2 ms** del vecchio
    stale-while-revalidate. INVARIATI: WebP cache-first, `latest.json` e gli altri JSON del
    branch in stale-while-revalidate. Il player riprogramma una rivalidazione 2 s dopo una
    risposta stale (max 3 giri) e ricostruisce i frame **solo se `generated_at` cambia**.
  - **CAUSA 2 - un errore transitorio diventava definitivo.** `cacheFirst` rispondeva 504 se
    la rete falliva senza copia; su un `<img>` l'errore finiva in `fmDeadWebpRemember`, il
    player saltava il frame e dopo lo sblocco quel WebP non veniva piu' richiesto (ripiego
    `.om` da **512.710-525.014 B** in 206 Range contro **~319 KB** del WebP, mappa vuota nel
    frattempo: misura della diagnosi), e la blacklist si azzerava solo ricaricando la pagina.
    Ora, sul solo percorso di errore, una **probe HEAD** distingue 404/410 (frame non
    pubblicato -> blacklist per la vita del run, come la 1.2.4.23) da errore di rete/5xx/timeout
    (**nessuna blacklist**: retry del frame corrente con backoff **1 s / 3 s / 9 s**, tetto 3;
    esauriti, `.om` per quel solo rebuild senza marcare il frame).
  - **CAUSA 3 - la navigazione con timeout 3 s serviva la build precedente.** L'html della
    1.2.4.55 pesa **2.294.179 B**: a 3 s servono **>=6,12 Mbit/s**, sotto quella soglia il SW
    serviva la build in cache e chi aveva una versione vecchia non vedeva le mappe nuove. Il
    timeout della sola navigazione passa a **10000 ms**; se la rete arriva **dopo** e la build
    e' diversa (ETag/Last-Modified/Content-Length) il SW manda un `postMessage` ai client e
    compare il banner `#app-update` gia' esistente, senza ricaricare e senza bloccare il primo
    paint (la fetch tardiva e' tenuta viva da `event.waitUntil`).
  - **Misure in browser (Edge headless + CDP, server locale con due stati degli indici reali).**
    Cache popolata dalla run `20261028T000000Z`, server che pubblica la `20261028T030000Z`: la
    **1.2.4.55** resta sulla run vecchia e chiede `runs/20261028T000000Z/...`; la **1.2.4.56**
    mostra `generated_at 2026-10-28T09:01:19Z` e chiede `runs/20261028T030000Z/...`. Frame in
    cache del SW: **2/2** dopo il primo caricamento; alla riapertura i WebP arrivano dal SW
    (`fromServiceWorker: true`). WebP con 5xx poi 200: **nessuna blacklist**, retry a 1 s,
    frame caricato (`Frame 41/93`); WebP 404: blacklist immediata e salto al frame successivo.
    Index con rete a 5 s: risposta dalla cache in **2.021 ms** con `X-MRI-Cache: stale`.
    Navigazione con html a +12 s: build dalla cache in **10.244 ms** (`transferSize 0`), banner
    nascosto al load e visibile dopo la risposta tardiva, cache aggiornata alla build nuova.
    Rivalidazione con `generated_at` identico: **1** rivalidazione eseguita, `fmTimeline` e
    `fmWebpIndex` **stessi oggetti**, `fmWebpGeneration` invariato (nessun rebuild).
    0 eccezioni JS.
  - **Test**: `test_sw_cache_policy.mjs` **83 PASS / 0 FAIL** (prima 41/0; le asserzioni nuove
    coprono `request.cache`, network-first 2 s, il marker stale, 404 vs 5xx e l'avviso di build
    nuova); `test_html_js_syntax.py` **1 passed**; suite `.mjs` **26 PASS / 2 FAIL**, gli stessi
    due FAIL pre-esistenti della baseline (`contract_dataset_loader` 257 errori,
    `test_fm_webp_fastpath` A15c).

- Storico - Contenuto della 1.2.4.55 (release sopra la 1.2.4.54) - **una modifica, solo documentale nel
  pannello "Fonti Dati & Metodologie"**:
  - **Nuova sezione "Osservazioni al Suolo — Rete di Centraline"**, subito dopo i METAR:
    MeteoHub (Agenzia ItaliaMeteo / CINECA, portale del progetto MISTRAL), **4.269 stazioni su
    26 reti** nel file del 2026-10-27T13:20:25Z (ARPA regionali e MeteoNetwork, **1.045**
    MeteoNetwork), campi temperatura a 2 m, umidità relativa, vento a 10 m e precipitazione.
    **Nessuna visibilità** e **nessuna pressione al livello del mare**: la sorgente espone solo
    la pressione di stazione (`B10004`, livello 1.0.0.0), non riducibile senza la quota.
    Licenza **CC BY 4.0** con attribuzione per ente nel campo `attribution` di
    `data/stations/latest.json`; aggiornamento ogni **10 minuti** (`stations.yml`, cron
    `*/10 * * * *`), latenza mediana misurata **37,7 minuti**, **99,11%** delle osservazioni
    entro **90 minuti**, scarto client a 90 minuti (`STATIONS_MAX_OBS_AGE_MIN`). Periodo di
    accumulo dichiarato per stazione (`precipitation_period_s`: 60, 300, 900, 1.800, 3.600 s
    prevalenti, 600 s su 202 stazioni, due casi a 180/360 s), quindi i millimetri non sono
    confrontabili senza normalizzare. Copertura non uniforme (da 1 a 1.045 stazioni). I dati
    sono **osservati, non previsti**, su uno strato indipendente dal pannello LIVE.
  - **Riferimento morto corretto**: la sezione "Mappe Previsionali" citava `fm2-cache` (branch
    storico, non più scritto da alcun workflow). Ora indica `fm2-rendered` per i WebP e gli
    indici aggregati (scrittore unico `forecast-maps-index.yml`) e i cinque branch per
    fornitore (`fm2-dwdicon`, `fm2-dwd`, `fm2-ecmwf`, `fm2-ncep`, `fm2-meteofrance`) per i
    frame `.om`.
  - **Verificato senza modifiche**: i **7 domini** delle mappe sono 7 anche nei workflow di
    `main`; le risoluzioni dichiarate tornano con i manifest (`dwd_icon_eu` dx 0,0625° =
    6,95 km in latitudine; `dwd_icon` dx 0,125° = 13,9 km N-S e 9,8 km E-W a 45°N).
- Storico - Contenuto della 1.2.4.54 (release sopra la 1.2.4.53) - **due modifiche, entrambe sul client**:
  il **secondo toggle "Centraline"** nel pannello LIVE e la **rimozione della sincronizzazione**
  fra selettore della sorgente e strato delle etichette.
  - **Due toggle affiancati e indipendenti.** `#live-toggle-stations` ("Centraline") sta accanto a
    `#live-toggle-supercells` ("Struttura convettiva") nello stesso `.sc-toggles`, con la stessa
    classe `.sc-toggle`: rettangoli misurati **112,47x17** e **66,91x17 px**, stessa riga
    (`y=174`), altezza identica, distanza **6 px**. I due non si escludono.
  - **Cosa accende.** ON: carica `data/stations/` se serve e mostra il canvas delle etichette, la
    legenda `#live-stations-box` e la riga di stato `#live-stations-status`; OFF: rimuove lo
    strato dal DOM, nasconde legenda e stato e ferma il timer di refresh (60 s). Lo stato e' una
    variabile di sessione (`liveShowStations`), non `localStorage`: al reload parte OFF
    (misurato: `localStorage` vuoto, dopo il reload toggle OFF e canvas assente).
  - **Ortogonale al selettore.** `#mnw-source-select` resta a **tre voci** (`live`, `model`,
    `stations`) e il ramo `renderStationsExtract` resta: il selettore sceglie cosa mostra il
    **pannello**, il toggle accende lo **strato** sulla mappa. `refreshMnwPanel()` non chiama piu'
    `stationsSetActive`, `toggleLiveStations()` non legge ne' scrive `select.value`.
  - **Sei combinazioni verificate** (select x toggle): `select.value` invariato prima e dopo il
    click in **6/6**, **0 eccezioni JS**. Con `stations` + toggle OFF il pannello mostra le
    centraline e la mappa **non** ha etichette (canvas assente, **0** pixel); con `live` + toggle
    ON le etichette stanno sopra i METAR; con `model` + toggle ON sopra l'estratto modelli.
  - **Misure dello strato** (zoom 7, canvas **754x640**, filtro 90 minuti invariato): **4.182/4.275**
    centraline attive, **1.796** candidate, **94** etichette (griglia **32 px**), **147** scartate
    per collisione, **0** intersezioni verificate a coppie su **4.371** coppie, **51.087** pixel
    dipinti, **6,2-46,1 ms** di disegno. Con OFF: canvas rimosso e **0** pixel dipinti.
  - **Difetto pre-esistente corretto.** `fetchWithTimeout()` passava `options.priority` (numero
    della coda interna, es. `40`) dentro `fetch()`; `RequestInit.priority` accetta solo
    `high|low|auto`, quindi l'**Estratto Modelli** falliva prima della rete con `40 is not a valid
    enum` anche nella 1.2.4.53. Ora `delete opts.priority` sulla copia passata a `fetch` e il
    pannello modello carica (misurato: Open-Meteo, **19,6 °C**).
  - **Nebbia e Sviluppo.** `#metric-tab-fog` resta dietro lo switch (none / flex / none), i due
    toggle restano visibili e alti **17 px** in tutti e 3 gli stati.
- Storico - Contenuto della 1.2.4.53 (release sopra la 1.2.4.52) - **due modifiche, entrambe sul client**:
  le centraline osservate diventano **etichette con la temperatura** (rettangoli arrotondati,
  fondo a bande della scala `temperature_2m`, testo a contrasto calcolato, zero sovrapposizioni)
  e la **Struttura convettiva (SOI)** torna visibile nel pannello LIVE per tutti.
  - **Etichette, non puntini.** Con la sorgente "Centraline" ogni stazione visibile e' un
    rettangolo arrotondato (testo **11 px**, angoli 3 px, **32x15 px** misurati sul contesto) con
    la temperatura a 2 m, **una cifra decimale e la virgola** (`13,8`). Il valore senza
    temperatura **non si disegna** (nessun `--`, nessun `n/d`).
  - **Fondo = banda della scala delle mappe.** I **42** breakpoint e i **42** colori di
    `temperature_2m` (**-80..+50 °C**, ultima banda aperta) sono copiati identici da
    `scripts/forecast_v2/color_scales.json` e registrati in `FM_COLOR_SCALE_OVERRIDES` come
    `FM_TEMP2M_COLOR_SCALE` (registro da **21** a **22** chiavi). La scala `temperature` della
    libreria (wml 0.2.2) e' **identica** voce per voce: mappa, legenda ed etichette usano lo
    stesso array.
  - **Testo scelto per contrasto, non a occhio.** Nero `#000000` o bianco `#ffffff`, quello con
    il rapporto WCAG maggiore: **42/42 bande >= 4,5:1**, minimo **4,87:1** (banda -40 °C). Con
    `#0f172a`/`#f8fafc` tre bande restavano sotto (4,14 / 4,21 / 4,46).
  - **Zero sovrapposizioni.** Griglia di occupazione di **(larghezza + 2) x (altezza + 1) px**
    (**34x16**): ogni etichetta marca tutte le celle che copre, quindi due rettangoli possono
    intersecarsi solo se condividono una cella. Ordine per **osservazione piu' recente**: chi
    collide viene scartato (vince la piu' recente). Tetto **250** etichette: la decimazione
    **12->16->24->32->48->64 px** alza il lato finche' non scende sotto il tetto.
  - **Misure (Edge headless, CDP, canvas 754x640, filtro 90 minuti invariato).** File reale
    **4.246** stazioni / **4.105** attive: **60** etichette a zoom 6. Con una **fixture
    dichiarata** (tutte le 4.246 con `obs_time` = adesso, 3.364 con temperatura, coordinate e
    valori reali): **63 / 93 / 91** etichette a zoom **6 / 7 / 8**, **0** sovrapposizioni
    geometriche, **161 / 117 / 55** scartate per collisione, tempo di disegno (mediana su 20)
    **19,1 / 11,7 / 8,9 ms** (soglia 60). Testi intercettati da `fillText`: `16,8` `17,8` `12,5`
    `13,5` `17,1`, **91/91** conformi a `xx,x`.
  - **Click.** Il popup si apre sul **rettangolo** dell'etichetta; il canvas resta
    `pointer-events:none`, quindi un click lontano non viene rubato alla mappa (verificato a
    40,700 px: nessun popup).
  - **Struttura convettiva (SOI) di nuovo visibile per tutti.** `#live-toggle-supercells` non ha
    piu' `style="display:none"` e `toggleSviluppo()` non lo nasconde piu' fuori da Sviluppo. La
    **nebbia resta dietro lo switch**. Corretto nello stesso punto un difetto reale:
    `toggleScRadar()` aggiungeva `active` ma l'inline `display:none` del markup vinceva su
    `.panel-live.active{display:flex}`, quindi il pannello restava chiuso; ora azzera l'inline
    (misurato: `flex`, altezza **312 px**, e di nuovo `none` a altezza 0 al secondo click).
  - Non toccati: filtro delle osservazioni a **90 minuti**, nebbia dietro lo switch, popup e hover
    con tutti i campi, attribuzione CC BY 4.0, mappe previsionali, player.
- Storico - Contenuto della 1.2.4.52 (release sopra la 1.2.4.51) - **due modifiche, entrambe sul client**:
  la legenda delle temperature a 850 e 500 hPa che segue la mappa (scale copiate identiche dal
  JSON del renderer, commit `a449322a`, che finora il client non registrava) e la regione
  **Europa** dei domini globali allargata al **Nord Atlantico** (renderer gia' aggiornato,
  commit `dc9a31b6`).
  - **`temperature_850hPa`: 34 bande da 2 °C, da -32 a +34 °C** (ultima banda aperta
    `[34, +inf)`), in `FM_COLOR_SCALE_OVERRIDES` come `FM_T850_COLOR_SCALE`. Prima la variabile
    non aveva override e la libreria la risolveva per prefisso sulla scala di `temperature`
    (**42 bande -80..+50 °C**): il rosso arrivava solo verso **+38/+40 °C**, quindi i valori reali
    di 850 hPa (**0 °C contro +8 °C**, la differenza che decide la neve in quota) cadevano su tre
    verdi quasi uguali. Range tarato su **10.724.490** celle misurate (15 frame `.om`, 5 domini x
    3 frame): min **-32,1**, p50 **+2,9**, max **+32,6**. **0 °C** resta il verde menta
    `#72E8A5` e la zona **-18..0 °C** e' **identica** alla rampa precedente.
  - **`temperature_500hPa`: 27 bande da 2 °C, da -46 a +6 °C** (ultima banda aperta
    `[6, +inf)`), in `FM_COLOR_SCALE_OVERRIDES` come `FM_T500_COLOR_SCALE`. Con la rampa
    generica il rosso stava a **+38 °C** su un livello che in Italia sta fra **-27 e -7 °C**: la
    mappa era tutta blu. Stessa misura: min **-45,5**, p50 **-18,7**, max **+5,6**; prime **19**
    voci della rampa piu' **8** verdi/gialle, saltando `#11DC3D` (indistinguibile dalla precedente,
    dE2000 **2,0**).
  - Il registro `FM_COLOR_SCALE_OVERRIDES` passa da **19** a **21** chiavi.
  - **Regione `europe` dei 4 domini globali: da `bbox [30, -30, 72, 50]` a `[30, -70, 75, 45]`**,
    cioe' **Labrador, Groenlandia meridionale, Islanda e Azzorre**. Serve perche'
    `fmApplyRegion` **interseca** il bbox della regione con i bounds della run: con il riquadro
    vecchio il Labrador a **60W** sarebbe stato tagliato anche se il WebP lo contiene. Il
    renderer copre ora **30..75N / 70W..45E** con output **921x679** (prima 1121x1111): area piu'
    grande e **file piu' leggeri**, **-27,2%** di byte su `ecmwf_ifs025` e **-31,5%** su
    `dwd_icon` (misurati dal renderer), cifre **8,19 px** a schermo. **Invariati**
    `FM_GLOBAL_DOMAINS`, `fmDomainRegionIds` e i 3 domini regionali: con `dwd_icon_eu` la vista
    Europa resta **34..62N / 15W..40E**.
  - Non toccati: `temperature_2m` (resta -80..+50), `temperature_10hPa`, nebbia dietro lo switch,
    centraline del LIVE, zoom polare, legenda dam, fix mappe.
- Storico - Contenuto della 1.2.4.51 (release sopra la 1.2.4.50) - **tre cose, tutte sul client**.
  - **Il selettore di modalità è eliminato e torna lo switch "Sviluppo" del footer** (dov'era
    nella 1.2.4.47). Rimossi per intero: il markup `#ui-mode-switch` con i due pulsanti
    Pro/Sviluppo, il CSS `#ui-mode-switch`/`.uim-btn` con
    `body:not([data-ui-mode="sviluppo"]) .dev-only`, la regola
    `#app-footer .dev-switch { display:none !important; }`, `readUiMode`/`applyUiMode`/`setUiMode`,
    `uiMode`/`UI_MODES`/`UI_MODE_KEY`/`UI_MODE_LEGACY_KEY`, lo script di pre-paint che scriveva
    `data-ui-mode` e la chiamata di boot. `isSviluppoActive` è pilotato **solo** da
    `toggleSviluppo()`; le chiavi `meteorisk.uiMode.v2` e `meteorisk.uiMode` vengono **rimosse**
    alla prima apertura. Le classi `.dev-only` non esistono più: **Conv./Grandine/Nebbia/SOI** le
    accende e le spegne `toggleSviluppo()` con `style.display` (e uscendo da sviluppo chiude timer e
    layer). Misurato in stato normale: **19 pulsanti** visibili, **3**
    `<select>`, **0** elementi con id `ui-mode-switch`, **0** `data-ui-mode` nel DOM,
    `typeof setUiMode === 'undefined'`, switch del footer visibile e cliccabile. Confronto con la
    1.2.4.47: stessi **20** pulsanti, **differenza di insieme vuota**.
  - **Terza sorgente "Centraline (osservato, ~30 min)"** nel selettore del pannello live: legge
    `data/stations/index.json` su Pages e, se non risponde, `data/stations/latest.json` su
    `raw.githubusercontent` (se rispondono 404 **entrambi** compare un messaggio, nessuna
    eccezione). Filtro `obs_time` entro **90 minuti**; disegno su **un canvas** con
    **decimazione dichiarata** (griglia di **16 px** nello spazio dello schermo, una
    stazione per cella — la piu' recente — lato 12/16/24/32/48/64 px oltre il tetto di
    **600** punti); colore **continuo** per temperatura (-10..+40 °C) con legenda; popup e
    hover con nome, **ente** (attribuzione per rete del file), eta' dell'osservazione,
    temperatura, umidita', vento e pioggia **normalizzata** su `precipitation_period_s`; campi
    mancanti **omessi**. Attribuzione **CC BY 4.0** visibile nel pannello e nel popup. Pannello
    LIVE con **"ultimo aggiornamento: N min fa"**, **attive/totali** e badge **"dato fermo"**
    oltre **2 ore**. Misurato: index.json **542.989 byte** / **4205 stazioni**,
    latest.json **1.063.821 byte**, **1787** attive entro 90 min, **471**
    punti disegnati a zoom 6 in **908 ms**.
  - **Nebbia solo con lo switch**: il tab `#metric-tab-fog` e il badge `id:'fog'` di
    `computeActiveRisks` esistono solo con `isSviluppoActive === true`. Soglie **70/85/95**,
    `FOG_BADGE_MIN` e `case 'fog'` **invariati**. Misurato: **7** tab metriche e
    **0** badge nebbia sulle **107** province in stato normale, **10** tab
    e **0** province con badge con lo switch ON.
- Storico - Contenuto della 1.2.4.50 (release sopra la 1.2.4.49) - **la modalita' "Semplice" e'
  eliminata, non nascosta: restano DUE modalita', `pro` (default) e `sviluppo`**. Solo client.
  - **Rimosso per intero**: il pulsante "Semplice" dal selettore, la legenda `#easy-legend` con il
    suo markup, le **6** regole CSS `body[data-ui-mode="easy"]` piu'
    `body:not([data-ui-mode="easy"]) #easy-legend`, la classe `.easy-hide` da **25** punti del
    markup, `easyRiskSentence` con il dizionario `EASY_RISK_WORDS` e `easyPeriodWord`, il ramo
    `if (uiMode === 'easy')` in `buildRiskTooltip` e il gate `uiMode !== 'easy'` nel
    raggruppamento dei badge.
  - **Selettore a 2 pulsanti**: con profilo nuovo **22 pulsanti** visibili, **3 `<select>`** e
    almeno **13** acronimi; nel DOM servito la parola "Semplice" non compare piu' (**0**
    occorrenze) e le eccezioni JS sono **0**.
  - **Migrazione**: chi ha `easy` salvato in `meteorisk.uiMode.v2` parte in **`pro`** e la chiave
    viene **riscritta** a `pro`; `sviluppo` resta `sviluppo`.
- Storico - Contenuto della 1.2.4.49 (release sopra la 1.2.4.48) - **la pagina si apre di nuovo in
  `pro`**; le tre modalita' (`easy` / `pro` / `sviluppo`) restavano una **scelta** nel selettore.
  Solo client: nessuna modifica a `scripts/`, al renderer, al player o ai workflow dei dati oltre
  alle righe `cp`.
- Storico - Contenuto della 1.2.4.48 (release sopra la 1.2.4.47) - **tre modalita' di visualizzazione
  (`easy` / `pro` / `sviluppo`) scelte da un selettore in toolbar e ricordate in `localStorage`**.
  Solo client: nessuna modifica a `scripts/`, al renderer, al player o ai workflow dei dati oltre
  alle righe `cp`.
  - **`easy` (default nella 1.2.4.48, non piu' dalla 1.2.4.49)**: 6 pulsanti visibili, 0 `<select>`, 0 acronimi e 58
    parole di testo visibile (misurate); legenda a 3 classi in parole (Nessun rischio / Attenzione /
    Forte) al posto di quella tecnica; tooltip di provincia con UNA frase in italiano da un
    dizionario chiuso (max 12 parole, 0 sigle), es. "Oggi nebbia molto fitta e vento forte.".
  - **`pro`**: la UI completa (23 pulsanti, 3 `<select>`, 327 parole, 25 token in maiuscolo su 13
    distinti); rispetto alla 1.2.4.47 la differenza di insieme dei pulsanti e' **solo** i 3 del
    selettore, e le `<select>` sono le stesse.
  - **`sviluppo`**: `pro` piu' i pannelli e i tab tecnici gia' esistenti (SOI "Struttura
    convettiva", tab Conv. e Grandine, selettori shear e livello grandine, Meteociel, Fonte API,
    Nuovi Indici): 42 pulsanti, 19 `<select>`, 706 parole, 57 token su 25 distinti.
  - **Un solo stato** (`uiMode`, chiave `meteorisk.uiMode`) con **mappatura esplicita**
    `isSviluppoActive = (mode === 'sviluppo')`; la visibilita' la decide il CSS con
    `body[data-ui-mode=...]` e le classi `.easy-hide` / `.dev-only`. Tornando in `easy` i pannelli
    di `pro` vengono **chiusi davvero** (non solo nascosti): con il solo CSS la mappa del rischio
    restava senza i **107** poligoni delle province (difetto noto "pannelli chiusi -> 0 poligoni").
- Storico - Contenuto della 1.2.4.47 (release sopra la 1.2.4.46) - **la vista polare si puo' zoomare fino a
  3x e trascinare**, **la legenda del geopotenziale a 500 hPa si legge in decametri con una
  tacca per banda** e **il pannello mappa desktop passa da 560 a 640 px**. Solo client: nessuna
  modifica a `scripts/`, al renderer, al player o ai workflow dei dati oltre alle righe `cp`.
  - **Zoom e trascinamento della vista polare (`north`)**: due pulsanti (`-` e `+`, passo
    **1,25x**), **rotella** ancorata al cursore (passo **1,15x**) e **trascinamento** del disco,
    con `FM_POLAR_ZOOM_MIN = 1` e `FM_POLAR_ZOOM_MAX = 3`. Il tetto a 3 si misura sul WebP
    **1000x1000**: al pannello 754x560 un pixel di immagine vale **0,538 px** di schermo, a zoom 2
    vale **1,075** (upscale 1,075x, testo ancora nitido), a zoom 3 vale **1,613**, a zoom 4
    varrebbe **2,15** e le cifre cotte nel raster sarebbero sfocate, perche' il client non
    conosce isolivelli e valori e non puo' ridisegnarle.
  - **Un solo punto di trasformazione** (`fmPolarGeometry`): disco pre-proiettato, riproiezione
    per pixel del frame Mercator, terra, coste, confini, reticolo e cerchio di bordo passano tutti
    da `{R0, R, cx, cy, zoom}`; se il campo fosse trasformato e le coste no, i due si
    separerebbero. A **zoom 1** la geometria e' **esattamente** quella della 1.2.4.46
    (`cx = w/2`, `cy = h/2`, `R = 0,48*min(w,h)`): misurato sul canvas reale, a zoom 1 il canvas
    e' **identico pixel per pixel** alla 1.2.4.46. Il centro e' memorizzato in unita' di `R0`
    (`ox`, `oy`), quindi un ridimensionamento del pannello conserva l'inquadratura relativa.
  - **Leaflet spento in vista polare, riacceso solo cio' che era acceso**: i gestori che
    ascoltano sul contenitore (`scrollWheelZoom`, `dragging`, `doubleClickZoom`, `boxZoom`,
    `touchZoom`) vengono disabilitati in `north` e riabilitati all'uscita **solo** se erano
    attivi prima. Senza, la rotella faceva lo zoom di una mappa invisibile e il trascinamento
    spostava i pane sotto il disco. La rotella non tocca `map.getZoom()`: verificato che resta
    fermo.
  - **Difetto della prima stesura, trovato in verifica e corretto**: `fmPolarSetActive(true)`
    viene richiamata **a ogni frame** dal player, quindi il ramo che spegne i gestori girava piu'
    volte e al secondo giro salvava `{}` (i gestori erano gia' spenti): uscendo da `north` non si
    riaccendeva **nulla** (misurato: `scrollWheelZoom`, `dragging`, `doubleClickZoom`, `boxZoom`,
    `touchZoom` tutti `false` dopo il ritorno a `italy`). Aggiunta una guardia di idempotenza in
    `fmPolarSetLeafletHandlers`: lo stato salvato si scrive **una volta sola** e viene consumato
    all'uscita; dopo il fix i 5 gestori tornano attivi (misurato).
  - **Legenda Z500 in decametri, 31 tacche**: la carta composita `__pressure_geopot` mostrava
    **9 tacche in metri** (4920...6120) mentre la carta emisferica di riferimento riporta **492,
    496, 500 ... 612 dam** sulle stesse bande. Ora la legenda divide per 10 e mette **31 tacche**,
    una per banda di `FM_GEOP_BREAKPOINTS_500` (4920..6120 m, passo 40), con unita' **`dam`**.
    Il dato resta in metri e la scala colori e' la stessa: cambia solo l'etichetta. Le altre
    variabili restano a 9 tacche.
  - **Le 31 tacche solo su legenda larga (>= 680 px)**: le etichette a 5 caratteri stanno in
    ~610 px; a **390 px di viewport** la legenda e' **348 px** e si misuravano **30 coppie di
    etichette sovrapposte, fino a 12 px**. Sotto la soglia il conteggio torna a **9 tacche**
    (misurato dopo il fix: 0 sovrapposizioni; a 1240 px restano 31).
  - **Pannello mappa desktop 560 -> 640 px**: con 560 px il disco polare misurava
    `0,96*560 = 537,6` px di diametro e le cifre **10,76 px** a schermo (fattore
    `0,96*560/999 = 0,53814`, cifre da 20 px d'inchiostro nel WebP), sotto il riferimento di
    prodotto (Meteociel 11-12 px). Con 640 px il disco e' **614,4 px** e le cifre **12,30 px**
    (`0,96*640/999 = 0,61478`). Cambia **solo** la regola `#leaflet-map` desktop: i breakpoint
    (`max-width: 950px` -> `60vh`; 768-1024 px -> `clamp(380px,60vh,640px)`; `<= 767px` ->
    `56dvh`) hanno altezze proprie e non sono toccati; `#radar-embed-container` resta 560 px.
  - **Follow-up dichiarato**: `PANEL_HEIGHT_PX = 560.0` in
    `scripts/forecast_v2/render_fm_webp.py` e' la costante con cui il renderer sceglie la
    dimensione delle cifre del disco **per un pannello da 560 px**. Il pannello ora e' 640,
    quindi a schermo le cifre sono **12,30 px** invece dei 10,76 px per cui il font e' stato
    scelto. Non e' un difetto visibile (le sovrapposizioni sono disegnate nell'immagine e non
    cambiano), ma la costante va allineata in una release che tocca il renderer: in questa **non**
    e' stata toccata.

## Versione 1.2.4.46 (storica)
- Contenuto della 1.2.4.46 (release sopra la 1.2.4.45) - **la nebbia non si legge piu' come una
  probabilita'** e **il badge si accende solo nel decile alto dell'indice**. Solo client: nessuna
  modifica a `scripts/`, al renderer, al player o ai workflow dei dati oltre alle righe `cp`.
  - **Segnalazione**: per la notte del 2026-10-26 l'app mostrava un valore di nebbia che si
    leggeva come **"70%"** in una provincia dove la nebbia non c'e' stata. Il punteggio 0-100
    di `fogScoreC` e' un **indice di potenziale**, non una probabilita': l'unica cosa da
    correggere e' come viene presentato, piu' la soglia con cui accende il badge.
  - **Misura (43 stazioni METAR, 1.649 bollettini)**: `aviationweather.gov` per singola
    stazione, finestra **2026-10-25T23:00Z -> 2026-10-26T22:20Z** (tutto il giorno 0 del
    dataset), verita' = `FG` nel tempo presente **oppure** visibilita' **< 1000 m**; il nostro
    dato = `data/latest/meteorisk-points.json` (modello `best_match` del punto selezionato di
    ogni provincia) con la formula identica a quella servita. **28 province** abbinate 1:1 alla
    stazione piu' vicina (**<= 30 km**, dislivello **<= 250 m**), **621 ore-provincia**, nessun
    abbinamento oltre 40 km. Unita' del vento verificate prima di applicare la formula:
    `scripts/common.py` non passa `wind_speed_unit`, quindi la serie e' in **km/h** (default
    Open-Meteo) come la firma `fogScoreC(dpd, windKmh, rh, code)` richiede.
  - **Esito**: 3 ore-provincia con nebbia (tutte a Milano Linate, 08-10 locali), 17 con
    visibilita' < 5000 m. A **soglia 30**: TP 3, **FP 205**, FN 0, TN 413 -> il **33,2%** delle
    ore senza nebbia aveva il badge. A **70**: TP 3, **FP 94** (15,2%). A 85: TP 2, FP 63.
    Per classi di indice la nebbia osservata e' 0/197 (0-19), 0/216 (20-29), 0/25 (30-39),
    0/25 (40-49), 0/29 (50-59), 0/32 (60-69), 1/21 (70-79), 1/26 (80-89), 1/50 (90-100):
    **sopra 60 l'indice non discrimina**. In vista giornaliera (massimo delle 24 ore) il badge
    copriva 21/28 province a soglia 30 e 14/28 a soglia 70.
  - **Taratura scelta (una sola)**: `FOG_BADGE_MIN` **30 -> 70** e livelli **30/50/70 ->
    70/85/95** (p90 dell'indice = **85,1**, p95 = 92,8). Sulla stessa finestra: FP **205 -> 94**
    (-54%) con gli stessi 3 eventi (POD 1,00 su questo campione), province col badge **21/28 ->
    14/28**, ore-provincia notturne col badge **179/319 -> 91/319**. Un punteggio di 70 che era
    livello 3 (Forte) ora e' livello 1 (Lieve).
  - **Formula invariata** e **pavimento 85 invariato**: con 3 eventi osservati rifare i pesi
    sarebbe overfitting dichiarato; il pavimento sul codice WMO 45/48 non e' la causa (su 46
    ore-provincia con codice 45/48 cambia il punteggio in **1** caso, differenza media **0,22**,
    e 19/22 non avevano nebbia nemmeno a 85).
  - **Presentazione allineata agli altri badge**: `riskLevelLine` e `riskLevelImpact` non
    avevano il caso `fog` e il riepilogo provinciale stampava **"Nebbia - ."** senza
    descrizione; ora ci sono tre righe per livello e la nota di impatto. Etichetta
    **"Nebbia (potenziale NWP)"** come grandine e convezione organizzata; tooltip **"indice
    N/100 (potenziale del modello, non probabilita)"** invece del solo "N/100"; legenda della
    metrica con gli scaglioni **70-85 / 85-95 / >= 95** e la dicitura **"indice 0-100, non una
    probabilita"**. `BADGE_LEVEL_RANGE.fog` resta `[1,2,3]`.

## Versione 1.2.4.45 (storica)

- Contenuto della 1.2.4.45 (release sopra la 1.2.4.44) - **il primo frame delle mappe
  previsionali non passa piu' dal raster `.om`** e **un solo URL per frame WebP**. Solo client:
  nessuna modifica a `scripts/`, al renderer, al player, ai workflow dei dati o al Service
  Worker oltre alle righe `cp`.
  - **Difetto misurato il 2026-10-26 sulla pagina servita** (Edge headless, profilo nuovo,
    `dwd_icon_eu` / `domain` / `temperature_2m`, byte attribuiti per richiesta): apertura a
    freddo **1.403.949 byte** di `.om` in **5** richieste; cambio regione `domain`->`italy`
    **1.525.652 byte** in **5**; `italy`->`domain` (regione mai visitata) **524.815 byte** in
    **2**. Il WebP dello stesso frame esisteva sempre e con il Service Worker attivo costava
    **0 byte** di rete: il raster era il **100%** del payload di una prima apertura.
  - **Causa: l'ordine, non il dato.** `fmApplyRegion()` finiva con `fmRebuild()`
    incondizionato mentre `fmTimeline`/`fmWebpIndex` erano ancora quelli della regione
    precedente: `fmWebpFrameUrl()` tornava `null` e `fmCurrentOmUrl()` imboccava il raster.
    Indice e timeline arrivano 100-300 ms dopo e il WebP sostituisce il raster, ma i byte sono
    gia' spesi.
  - **Fix A - si aspetta l'indice della regione.** `fmApplyRegion()` chiama
    `fmBeginRegionWait()` quando indice/timeline della regione resa non sono noti; il rebuild
    avviene nel callback di `fmFetchRenderedIndex()` (lo stesso ordine che `fmSetTimeline` usa
    gia' per la timeline) e una guardia in `fmRebuild()` vieta il ramo `.om` mentre l'attesa e'
    in corso. Il `.om` resta come fallback **reale**: indice arrivato senza il WebP di quel
    frame/dominio (verificato togliendo il dominio dall'indice: **1.049.820 byte** in **4**
    richieste, raster a **152 ms**), indice fallito, o nessun esito entro
    `FM_TIMELINE_RETRY_MS` (**60 s**). `fmFetchRenderedIndex()` ora accoda i callback quando un
    fetch e' gia' in volo.
  - **Fix B - un solo URL per frame.** Tolto `?run=` dall'URL WebP del path `index.json`: il
    path contiene gia' `<run_id>`. Misurato: sulla 1.2.4.44 **31/31** URL dell'indice avevano
    `?run=` e nessuno coincideva con l'URL della timeline; sulla 1.2.4.45 **0/31** e l'URL del
    frame e' **identico** (`urlIdentico: true`), quindi la cache puo' servirlo una volta sola.
  - **Risultato dopo il fix** (stessa sonda, build 1.2.4.45 senza Service Worker, quindi con i
    WebP a rete piena): apertura **0 byte** di `.om` in **0** richieste, cambio regione **0/0**,
    cambio dominio **0/0** (due volte), regione mai visitata **0/0**.
  - **404 vero sull'immagine** (URL dichiarato dall'indice ma riscritto su un path inesistente):
    con la timeline attiva il client salta al frame successivo e disegna quello (stato "Frame
    non disponibile: salto al frame 11/93 ... tentativo 6/8"), **0 byte** di `.om`; con TUTTI i
    frame a 404 il fallback `.om` parte comunque (**1.237.910 byte**). Comportamento invariato:
    la gestione dell'errore non e' stata toccata.
  - **Verifica.** `test_html_js_syntax.py` -> **1 passed**; suite `.mjs` **25 PASS / 1 FAIL**
    identica alla 1.2.4.44 (l'unico FAIL e' `test_fm_webp_fastpath.mjs` A15c, che chiede `>= 2`
    occorrenze di `data/fm2-rendered/north/index.json` in `forecast-maps-index.yml`: il file su
    `main` ne ha **1**, quindi il test e' disallineato dal workflow, non dalla release).
- **Nota sullo storico di questo README.** Era fermo alla 1.2.4.34: le release 1.2.4.35 ... 1.2.4.44
  sono documentate in `VERSION` (un blocco per release) e nel changelog in-app, non qui.
- Storico immediato documentato qui sotto: **1.2.4.34**.

## Versione 1.2.4.34 (storica; il README non e' stato aggiornato fino alla 1.2.4.42)

- File applicativo: `mri-light-1.2.4.34.html` (`APP_VERSION = '1.2.4.34'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.34', date: '2026-10-26' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.34'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.34 (release sopra la 1.2.4.32) - **il composito Pressione/Geopotenziale
  e' selezionabile nell'app** e **il Service Worker avvisa quando c'e' una versione nuova**.
  Solo client: nessuna modifica a `scripts/forecast_v2/*` e nessuna ai workflow.
  - **Il composito arriva dal server, con id proprio.** `__pressure_geopot` era pubblicata in
    `index.json` e nella timeline ma non era selezionabile: la pseudo-voce diventava raster Z500
    + overlay vettoriale MSL e su `north` il WebP vinceva comunque (`wantWebp`), quindi a schermo
    nessuna isobara e i valori del geopotenziale scritti. Ora `fmVariable` resta
    `__pressure_geopot` e `fmOverlayVariable` e' sempre null: si usa il WebP composto dal renderer
    (Z500 colorato **senza** valori + isobare MSL **bianche con il valore**).
  - **Fallback dichiarato.** Se la run non ha il WebP composito (run precedenti al renderer
    nuovo) si ricade sul percorso storico `.om` Z500 + overlay MSL, con status e legenda
    "disegnate dal client": senza fallback la mappa sarebbe vuota.
  - **Legenda.** Scala del raster (`geopotential_height_500hPa`) e nota
    "Isobares MSL (hPa) - 5 hPa" su ogni regione (5 hPa, non 10).
  - **Banner "Nuova versione disponibile - ricarica".** Su `updatefound` +
    `statechange === 'activated'` (piu' `controllerchange` e update gia' in attesa) compare
    `#app-update` (stile `#fm-newrun`, fixed, hidden di default) con bottone `location.reload()`.
    La prima installazione non mostra nulla: prima non c'era una versione da aggiornare.
  - **Numeri misurati dal renderer** (non stimati in questa release): rapporto Mercatore dedotto
    dal crop `dwd_icon_eu` **1,2675** (1761x1390) e `ecmwf_ifs025` **1,0090** (1121x1111); il
    rapporto 1,6 chiesto avrebbe stirato il contenuto del **26%** e del **59%**. Etichette Europa
    da 12 px forfettari (3,63/4,54 px a schermo) a font dedotto **7,55/7,56 px**. Composito sul
    disco da 1000 px: **16 livelli, 1.489 anelli**, etichette `strict` con **0** sovrapposte.
    Z500 senza valori; isobare bianche con casing scuro (**38,2%** dell'Europa ha luminanza >210
    nella palette Z500).
  - **Stato reale (dichiarato).** L'`index.json` di `fm2-rendered` letto il 2026-10-26T16:36Z
    espone `__pressure_geopot` solo per `ncep_gfs025` (39 frame); gli altri domini usano il
    fallback finche' il loro run non rigenera i WebP. Composito non verificato pixel per pixel.
  - Verifica: `test_html_js_syntax.py` 1 passed; suite `.mjs` 28 file -> **26 PASS / 2 non-zero**
    (`contract_dataset_loader` dataset-bound, `test_fm_webp_fastpath` A15c), contro la baseline
    di `main` 21 PASS / 7 non-zero: nessun nome nuovo, 5 asserzioni stale (4 `APP_VERSION`
    ferme alla 1.2.4.12 e il passo `fmContourInterval` atteso a 2 invece di 5) riallineate.
- Storico immediato: **1.2.4.32** (legenda del tratteggio neve, release di propagazione).

## Versione 1.2.4.32 (storica)

- File applicativo: `mri-light-1.2.4.32.html` (`APP_VERSION = '1.2.4.32'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.32', date: '2026-10-26' }`) + `sw.js`
  (`CACHE_NAME = 'mri-1.2.4.32'`).
- Contenuto: legenda del tratteggio neve in `precipitation` e `precipitation_6h` (due campioni:
  barra 50-80%, incrocio oltre 80%), `precipitation_6h` prima voce del gruppo, `precipitation`
  rinominata "Precipitazioni (passo del modello)". Soglie 0,5/0,8 scelte di prodotto; il
  tratteggio arriva nelle immagini con la rigenerazione `fm2-webp-2` del renderer.

## Versione 1.2.4.31 (storica)

- File applicativo: `mri-light-1.2.4.31.html` (`APP_VERSION = '1.2.4.31'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.31', date: '2026-10-26' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.31'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.31 (release sopra la 1.2.4.30) - **proiezione e bounds del frame**.
  Nessuna variabile nuova, nessun cambio di palette, nessuna modifica a `scripts/forecast_v2/*`.
  - **La proiezione e' del frame.** `fmRegionProjection(dom, frameEntry)` risolve con la
    precedenza: `projection` del frame della timeline -> `runs[].projection` della run del frame
    nell'`index.json` -> dominio (index, poi timeline) -> regione (timeline, poi index) ->
    `mercator`. Il difetto misurato il 2026-10-26 su `ecmwf_ifs025`/`north`: due run con
    proiezioni diverse (20261025T120000Z striscia 2879x521, 20261025T180000Z disco 1000x1000),
    il dominio dichiarava `stereographic` e i 36 frame della run vecchia venivano disegnati come
    disco (accordo misurato **4,23%** invece di **99,96%**).
  - **I bounds seguono la run.** `fmTimelineFrameBounds(entry)` usa `runs[].bounds` della run del
    frame. Senza, una striscia veniva riproiettata con `north=90` (i bounds del disco): errore
    medio di latitudine **18,895°**, massimo **54,466°** (a 84,95 N campionava 30,49 N).
  - **Quando si vede (dichiarato).** Ogni volta che la proiezione di un dominio cambia
    (attivazione del disco polare, cambio di `--north-disc-size`, rollback a una run precedente) e
    per tutta la durata della retention delle run (3 run): i frame delle run vecchie convivono
    con quelli della run nuova.
  - Verifica: `test_fm_frame_projection.mjs` 24 controlli PASS e `test_fm_timeline_projection.mjs`
    PASS; suite `.mjs` 28 file -> 21 PASS / 7 non-zero, gli stessi 7 nomi della baseline;
    `test_html_js_syntax.py` 1 passed; sulla pagina pubblicata il parser estratto verbatim
    risolve 36 frame mercator + 49 stereographic in 36 + 49.
- NON toccati: `scripts/forecast_v2/*`, `scripts/radar_engine/*`, `scripts/cronjobs/*`, i
  workflow `forecast-maps-*.yml`, `satellite-engine.yml`, `color_scales.json`.

## Versione 1.2.4.30 (storica)

- File applicativo: `mri-light-1.2.4.30.html` (`APP_VERSION = '1.2.4.30'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.30', date: '2026-10-26' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.30'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.30 (release sopra la 1.2.4.29) - **due fix al client, entrambi misurati**.
  Nessuna variabile nuova, nessun cambio di palette.
  - **Fix 1, la proiezione del dominio arriva anche dalla timeline.** `fmRegionProjection`
    risolveva `projection` come dominio da `index.json` -> `timeline.projection` -> regione ->
    `mercator`, ma la timeline porta la chiave anche a livello dominio
    (`domains.<dominio>.projection`, scritta sempre dal renderer e da
    `scripts/forecast_v2/build_timeline_index.py`) e il client leggeva solo la chiave di REGIONE.
    Nello stato misto (ECMWF IFS su disco, `dwd_icon`/`ncep`/arpege ancora su striscia) la chiave
    di regione e' omessa apposta (fix `09f8158b`), quindi finche' `index.json` non arrivava un
    disco polare veniva disegnato come striscia Mercator. Ora
    `fmTimeline.domains[fmDomain].projection` si legge PRIMA delle chiavi di regione, e solo se
    e' una STRINGA: un `projection` che fosse un oggetto viene ignorato e si passa al fallback.
    La chiave di regione resta l'ultimo fallback.
  - **Fix 2, geometria esatta del disco.** `fmPolarPaintProjectedDisc` disegnava `2R x 2R`, ma
    nella sorgente centro e raggio sono `(N-1)/2` su un lato di N pixel: il cerchio `rho=1`
    finiva a `0,999*R` e il polo a `(cx-0,27, cy-0,27)`. Ora il lato e' `2*R*N/(N-1)` (N letto da
    `source.width`/`naturalWidth`, non assunto 1000; con N=1000 e R=268,8 -> 538,14 px) con la
    STESSA origine `(cx-R, cy-R)`; `ctx.clip()` resta del raggio R.
  - Verifica fix 1: parser vero estratto verbatim dall'html, 13 controlli PASS; gli stessi 13
    controlli sul file 1.2.4.29 danno 3 FAIL, cioe' la controprova del difetto.
  - Verifica fix 2: scarto client<->disco sui 37.084 vertici di `ne_50m_coastline` con lat>=20
    (canvas 754x560, R=268,8) da media 0,3912 / max 0,6470 px a media 2,7e-14 / max 2,3e-13 px;
    polo e bordo a 0.
  - Test: `test_html_js_syntax.py` 1 passed; suite `.mjs` 27 file, gli stessi 6 nomi non-zero
    della baseline misurata su `main` PRIMA delle modifiche (`test_ensemble_module`,
    `test_fascia_slots`, `test_fm_webp_fastpath`, `test_fm_webp_palette_sync`,
    `test_player_fluidity`, `test_satellite_sources_italy`); `test_fm_timeline_projection.mjs`
    verde.
- NON toccati: `scripts/forecast_v2/*`, `scripts/radar_engine/*`, `scripts/cronjobs/*`, i
  workflow `forecast-maps-*.yml`, `satellite-engine.yml`, `color_scales.json`.

## Versione 1.2.4.29 (storica)

- File applicativo: `mri-light-1.2.4.29.html` (`APP_VERSION = '1.2.4.29'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.29', date: '2026-10-26' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.29'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.29 (release sopra la 1.2.4.28) - **scale colore delle due variabili a 6
  ore**, lato server e lato client. Nessuna modifica al codice di calcolo.
  - Il motore pubblica `precipitation_6h` e `snow_fraction_6h` dal commit `e67e0607`, ma
    `scripts/forecast_v2/color_scales.json` non aveva le due scale: il renderer le saltava con
    `~ no color scale for ... (skip)` e non esisteva alcun WebP. Aggiunte `scales.precipitation_6h`
    (18 bande 0, 0.2, 0.5, 1, 2, 3, 5, 8, 12, 16, 20, 30, 40, 60, 80, 100, 125, 150; banda
    `[150, +inf)` di SATURAZIONE; unita' `mm in 6 h`) e `scales.snow_fraction_6h` (copia esatta di
    `snow_fraction`: 7 bande 0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0; unita' `frazione 0-1`).
  - Stessa grammatica visiva di `precipitation` (identica sequenza di 18 colori, prima banda
    `[0,0.2)` trasparente), con le bande DIMENSIONATE sulla distribuzione reale: 1.198.911 celle
    bagnate, p50 0.50, p90 2.90, p95 5.10, p99 14.80, p99.9 37.60, p99.99 90.59, max 216.60 mm;
    sotto i 20 mm sta il 99% delle celle bagnate.
  - Client: `FM_PRECIP6H_COLOR_SCALE` e `FM_SNOWFRAC6H_COLOR_SCALE` copiate dal JSON (breakpoints,
    colori e unita', light == dark) e registrate in `FM_COLOR_SCALE_OVERRIDES`; legenda a 18 bande
    con unita' `mm in 6 h` e 7 bande con `frazione 0-1`.
  - Semantica dichiarata: `precipitation_6h` e' la SOMMA dei frame che tassellano le 6 ore
    precedenti di valid_time (6 cornici a passo 1 h, 2 a passo 3 h, 1 a passo 6 h), non "gli ultimi
    6 frame"; `snow_fraction_6h` e' PESATA sulla precipitazione, non la media delle frazioni
    orarie; la soglia neve/pioggia non e' nel motore ne' nel client (la scala e' solo una legenda).
  - Verifica: render reale (ARPEGE 0.25, 10 frame) 20 WebP in `italia` e 10 in `north`, zero skip;
    non regressione 6/6 sha256 identici per `temperature_2m`, `pressure_msl`, `weather_code`,
    `snowfall_water_equivalent`; `test_html_js_syntax.py` 1 passed; suite `.mjs` 19 PASS / 7
    (stessa baseline di `main` con `data/` vuota), `test_fm_webp_palette_sync` 143/144.
- NOTA: questo README era rimasto alla 1.2.4.27 perche' la 1.2.4.28 (fase neve, `weather_code`
  categorico) aggiorno' solo il changelog in-app e `VERSION`. Viene riallineato qui.

## Versione 1.2.4.28 (documentata solo in `VERSION` e nel changelog in-app)

- File applicativo: `mri-light-1.2.4.28.html` (`APP_VERSION = '1.2.4.28'`), `VERSION` con
  `BUILD=28`, `sw.js` con `CACHE_NAME = 'mri-1.2.4.28'`.
- Contenuto: fase neve nel client con 4 etichette (`snowfall_water_equivalent`, `snow_fraction`,
  `precipitation_6h`, `snow_fraction_6h`), scale della neve copiate dal JSON, `weather_code`
  categorico a 45 bande con legenda a 7 famiglie, chiusura di due difetti preesistenti (id
  duplicati, classi senza regola CSS). Il dettaglio completo e' nel changelog in-app e nel blocco
  `VERSION` della 1.2.4.28: questa sezione e' ricostruita da quelle due fonti, non fu scritta
  allora.

## Versione 1.2.4.27 (storica)

- File applicativo: `mri-light-1.2.4.27.html` (`APP_VERSION = '1.2.4.27'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.27', date: '2026-10-26' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.27'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.27 (release sopra la 1.2.4.26) - **percorso client "immagine gia'
  proiettata"** per la vista polare (disco stereografico), con il comportamento della 1.2.4.26
  finche' il renderer non pubblica il campo `projection`:
  - **La proiezione arriva dai DATI, non da una costante del client.** `projection` si legge con
    la precedenza dominio (`index.json` -> `domains.<dominio>.projection`) -> timeline
    (`timeline.json` -> `projection`) -> regione (`index.json` -> `projection`). Valori
    riconosciuti: `mercator` (default) e `stereographic`; campo assente o valore sconosciuto =
    `mercator`, cioe' **esattamente** il percorso della 1.2.4.26 (riproiezione per pixel +
    capolo sintetico).
  - **Il disco non e' ancora pubblicato**: il renderer (`scripts/forecast_v2/*`, **non** toccato)
    non scrive il campo, quindi in produzione non cambia nulla. Quando arrivera',
    `fmPolarPaintProjectedDisc()` disegna l'immagine quadrata **gia' proiettata** nel quadrato
    circoscritto al cerchio di raggio `min(w,h)*0.48` con `clip()` circolare: nessun
    campionamento per pixel, nessun capolo inventato (il polo e' nei dati), angoli = fondo
    `#0b1830`. Su questo percorso `fmPolarFrame.pixels` e' `null` e il frame vive in
    `fmPolarFrame.source` (niente `getImageData`).
  - **La proiezione fa parte dell'identita' del frame**: `fmPolarLoadFrame(url, bounds,
    projection)` ricarica se la proiezione cambia anche a parita' di URL;
    `fmPolarFrameProjection` viene azzerato dal reset del player insieme a `fmPolarFrame`.
  - **Verificato A/B contro la 1.2.4.26** (Edge headless via CDP, blocco polare reale estratto
    dai due file, stesso frame WebP, stesso harness): canvas **pixel-identico** con l'`index.json`
    reale delle 4 regioni (nessuna dichiara `projection`) a **800x600** (`520f4e06`), a
    **1024x700** (`53154e17`) e a **devicePixelRatio 2** (`5dd9114`); **0 eccezioni JS**. Con
    `projection: 'stereographic'` e un disco finto: angoli = fondo, centro e anelli dipinti,
    `fmPolarFrame.pixels === null`; togliendo il campo si torna al percorso Mercator (hash
    `88f0d2e8`).
  - **Test**: `scripts/tests/test_html_js_syntax.py` -> 1 passed; suite `.mjs` 26 file ->
    **19 PASS / 7 exit 1**, gli stessi 7 nomi della baseline 1.2.4.26.

## Versione precedente: 1.2.4.26

- File applicativo: `mri-light-1.2.4.26.html` (`APP_VERSION = '1.2.4.26'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.26', date: '2026-10-26' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.26'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.26 (release sopra la 1.2.4.25) - **la run nuova si vede**:
  - **Il pannello non resta piu' sulla run vecchia.** Causa misurata: `fmDomainChanged()`
    chiama `fmResetPlayerForModelChange()` **prima** che la variabile sia risolta, e
    `fmStartFrameIndex()` restituisce **0** quando la variabile corrente non appartiene al
    dominio nuovo (`fmTimelineGroupId()` null -> nessun frame attivo); poi `fmLoadDomain()`
    confrontava `fmIdx` con `meta.valid_times.length` (manifest `.om`: 26/64 frame) e **non**
    con la timeline (~206), quindi l'indice restava 0 = frame piu' vecchio della run
    precedente. Fix: dopo la risoluzione della variabile si riancora su
    `fmStartFrameIndex()` e il limite usa il numero di frame **della timeline** quando e'
    attiva.
  - **Timeline aggiornata dal polling (5 minuti).** Nuovo flag `fmUserMovedFrame`, azzerato a
    ogni cambio dominio/regione/variabile: se l'utente **non** ha mai toccato
    slider/prev/next/play il player si **riancora** al primo frame del run corrente; se si e'
    mosso il frame **non** viene spostato e compare l'avviso **non bloccante**
    `Nuova run disponibile · vai` nella riga di stato (`#fm-newrun`), che porta al frame
    preferito. Non e' un alert ne' un modal: chi sta guardando +240 h di proposito non deve
    essere strappato via.
  - **`Nord Atlantico` (`natl`) rimosso dal menu regioni**: `fmRenderRegionId()` restituiva
    `null` per `natl` (non e' in `FM_RENDER_REGION_MAP`) e `/natl/` risponde **404**, quindi
    selezionarlo non avviava **nessuna** richiesta. L'Atlantico e' gia' nel riquadro Europa
    (bbox 34..62N / -15..40E, rapporto 1,60:1). Le altre regioni restano identiche e
    `FM_RENDER_REGION_MAP` non e' toccato (non conteneva `natl`).
  - **Didascalia delle sigle** nel pannello radar: toggle `Cosa significano i valori?`
    (`<button>` con `aria-expanded`, accessibile da tastiera) + blocco collassabile
    `#sc-caption` con una riga per sigla e la nota esplicita su cosa **non** e' calcolabile
    (rotazione, probabilita' calibrata). Riusa `.live-legend`/`.legend-title`/`.legend-row`/
    `.live-hint`: nessun CSS nuovo.
  - **`precipitation_cum` tolto dal menu per `ncep_gfs025`**: misurato **0 su 404.640 celle**
    in 3 frame distinti, perche' la sorgente Open-Meteo di quel dominio non pubblica alcuna
    variabile di superficie. Filtro **per dominio** (`FM_DOMAIN_HIDDEN_VARS`), non una lista
    globale: se un giorno il campo arriva basta togliere la voce.
  - **Changelog del satellite riallineato** senza riscrivere le voci storiche: una voce nuova
    dichiara che le voci 1.1.0.4/1.1.0.6 descrivono i frame statici come PNG mentre dal
    1.1.0.8 il client usa `SAT_STATIC_EXT = 'webp'` (i `.png` restano solo per Limaps e i
    tile Blitzortung).

## Versione precedente: 1.2.4.25

- File applicativo: `mri-light-1.2.4.25.html` (`APP_VERSION = '1.2.4.25'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.25', date: '2026-10-25' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.25'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.25 (release sopra la 1.2.4.24) - **poligoni delle province nascosti
  all'apertura delle Mappe previsionali**, **modelli globali che si aprono sulla regione
  Europa** (non piu' sull'emisfero nord) e **livello 10 hPa nell'interfaccia** (solo per i
  domini che lo pubblicano):
  - **Causa misurata** (sito di produzione 1.2.4.24, Edge headless via CDP, Sviluppo OFF):
    il riempimento colorato (`provinceGeoJsonLayer`, pane `tilePane` z 200) era **gia'**
    nascosto dalla 1.2.4.16 (107 path -> 0). Il layer ancora visibile era
    `borderOverlayLayer`, la rete bianca dei confini provinciali ISTAT (pane
    `borderOverlayPane`, z 410, **107 path**, `stroke:#ffffff`, `stroke-width:1.2`,
    `stroke-opacity:0.85`): e' quello che l'utente vede come "poligoni delle province".
  - **Fix**: `borderOverlayLayer` aggiunto a `fmRiskOverlayCandidates()` con nome `borders`:
    stessa lista e stesso stato per-layer della 1.2.4.16 (rimozione con `map.removeLayer`,
    ripristino **solo** dei layer che erano visibili). Nessun `if` separato.
  - **Geografia di riferimento intatta**: `fmCountryBordersLayer` e `fmCoastlinesLayer`
    (Natural Earth, pane `forecastGeographyPane` z 411) **non** sono toccati. Misurato: il
    dataset `ne_50m_admin_0_boundary_lines_land` (390 feature) non contiene le proprieta'
    `ADM0_A3_L`/`ADM0_A3_R`, quindi `fmIsItalyBoundaryFeature` non esclude l'Italia e il
    contorno nazionale resta disegnato (con il pane borderOverlay nascosto via CSS il
    confine alpino e la costa restano visibili).
  - **Verificato in browser** (cartella di release servita in locale + produzione): baseline
    **107** path nel pane borderOverlay; mappe aperte **0** borderOverlay + **0** tilePane,
    **109** voci salvate; chiusura **107 + 107**; con Sfuma ON prima dell'apertura i bordi
    restano `stroke-width:0` anche dopo la chiusura; **0** eccezioni JS.
  - **Test**: `scripts/tests/test_fm_risk_overlay_state.mjs` (W7 riscritta + nuova W7b che
    protegge `fmCountryBordersLayer`/`fmCoastlinesLayer`) e `scripts/tests/test_html_js_syntax.py`
    (`node --check` su ogni blocco `<script>` inline).
  - **Regione iniziale dei modelli globali = Europa** (seconda modifica della 1.2.4.25):
    - `fmRegionDefaultForDomain(domain)` e' la regola unica: `europe` per `dwd_icon`,
      `ecmwf_ifs025`, `ncep_gfs025`, `meteofrance_arpege_world025`; `domain` per
      `dwd_icon_eu`, `dwd_icon_d2`, `ncep_gfs013`. `FM_REGIONS` e l'elenco delle regioni
      ammesse per dominio **non cambiano** (i globali restano `north`/`europe`/`italy`/`natl`):
      cambia solo quale e' **selezionata all'inizio**.
    - La regola vale **solo al primo caricamento del dominio**: `fmRegionUserChosen` diventa
      `true` alla prima `fmRegionChanged()` (scelta manuale) e da quel momento la scelta
      dell'utente non viene sovrascritta cambiando modello.
    - `fmRegionForDomain()` **non forza piu' `north`** per i globali: `timeline.json`,
      `index.json` e i WebP usano la regione scelta (`/europa/`, `/italia/`, `/north/`).
      Prima un modello globale chiedeva sempre `/north/` qualunque fosse il selettore.
    - **Misurato in browser** (Edge headless via CDP, cartella di release servita in locale,
      rete reale `fm2-rendered`, 0 eccezioni JS): apertura con `dwd_icon_eu` -> selettore
      **Intero dominio** e richieste a `/domain/timeline.json` + `/domain/dwd_icon_eu/...`;
      `ecmwf_ifs025`, `ncep_gfs025` e `dwd_icon` -> selettore **Europa** e richieste a
      `/europa/timeline.json` + `/europa/<dominio>/runs/...`; catena `ecmwf` -> `ncep` ->
      `dwd_icon` -> `meteofrance` sempre `/europa/`; ritorno a `dwd_icon_eu` -> **Intero
      dominio**; scelta manuale **Italia** su `ecmwf_ifs025` -> `/italia/timeline.json` +
      `/italia/ecmwf_ifs025/...`, e restando nella stessa sessione `dwd_icon_eu` e
      `ncep_gfs025` restano su `/italia/`.
    - **Test**: `scripts/tests/test_fm_region_global.mjs` riscritto sul nuovo contratto
      (default `europe` per i globali, `domain` per i regionali, regione esplicita rispettata,
      `FM_REGIONS` invariato, flag `fmRegionUserChosen`) -> PASS; `test_html_js_syntax.py`
      -> 1 passed.

  - **Livello 10 hPa nel menu e nella legenda** (terza modifica della 1.2.4.25, solo client):
    il motore pubblica `temperature_10hPa`, `geopotential_height_10hPa` e `wind_speed_10hPa`
    per `ecmwf_ifs025` (27 variabili, era 24) e `ncep_gfs025` (17, era 14), con
    `VAR_10HPA_DOMAINS = {ecmwf_ifs025, ncep_gfs025}`. Il client non aveva ne' etichette ne'
    palette, quindi le tre variabili venivano **scartate** dal filtro di visibilita' di
    `fmLoadDomain` (che ammette `FM_CURATED_VARS` **oppure** una chiave di `FM_VAR_LABELS`).
    - **Etichette**: `Temperatura 10 hPa`, `Geopotenziale 10 hPa`, `Vento 10 hPa`. Il gate e'
      lo stesso `fmLoadDomain`: la voce compare se e solo se il manifest `.om` del dominio
      contiene la variabile, quindi **nessun meccanismo per dominio da inventare** e nessuna
      variabile 10 hPa per i DWD. `FM_CURATED_VARS` resta a **28** voci (aggiungerle li'
      avrebbe cambiato il conteggio bloccato da `test_fm_webp_palette_sync` H6 senza aggiungere
      nulla, perche' il filtro e' in OR con `FM_VAR_LABELS`).
    - **Palette**: copiate **identiche** da `scripts/forecast_v2/color_scales.json` (fonte unica
      del renderer): temperatura 16 bande da **-95 a -20 °C**, geopotenziale 31 bande da
      **29500 a 33700 m** (stessa rampa del geopotenziale 500/850/1000, `FM_GEOP_COLORS_10` =
      alias esplicito dello stesso array), vento 21 bande da **0 a 120 m/s** (stessa rampa
      `wind` del getto 300 hPa, ri-spaziata). Se client e renderer divergono la mappa e la
      legenda si contraddicono: per questo i valori sono quelli pubblicati, non una rampa nuova.
    - **Unita' del vento in m/s**, come tutte le altre scale di velocita' dell'app e come il
      dato `.om` (`VARIABLE_STEP` 0.1 m/s nel motore): dichiararla in km/h avrebbe etichettato
      la legenda con un'unita' sbagliata di un fattore 3,6.
    - **`wind_direction_10hPa` non ha voce ne' scala**, esattamente come
      `wind_direction_10m/850/300hPa`: la direzione e' pubblicata per il reader, non per il
      disegno. Verificato che **nessun** `index.json` pubblicato contiene una variabile
      `wind_direction_*` (0 occorrenze su north/europa/italia), quindi non esiste WebP per lei.
    - **Misurato in browser** (Edge headless via CDP, Sviluppo OFF, release servita in locale,
      rete reale `fm2-rendered`): `ecmwf_ifs025` 27 variabili con le tre voci 10 hPa,
      `ncep_gfs025` 18 voci di menu con le tre voci 10 hPa, `dwd_icon_eu` e `dwd_icon`
      **zero** voci 10 hPa; selezionando `temperature_10hPa` la legenda mostra **16 bande**
      (`[-95 – -90) °C` ... `[-20 – ∞) °C`) e viene richiesto
      `italia/ecmwf_ifs025/runs/20261025T060000Z/temperature_10hPa/2026-10-25T0600.webp`
      (HTTP 200); `geopotential_height_10hPa` 31 bande 29500..33700 m e
      `wind_speed_10hPa` 21 bande 0..120 m/s, entrambi con WebP 200; **0 eccezioni JS**.

## Versione 1.2.4.24 (storico)

- File applicativo: `mri-light-1.2.4.24.html` (`APP_VERSION = '1.2.4.24'`; ultimo blocco
  changelog in-app `{ version: '1.2.4.24', date: '2026-10-25' }`) + `sw.js` (Service Worker,
  `CACHE_NAME = 'mri-1.2.4.24'`, servito come `/sw.js` e copiato in `_site/sw.js` da
  `deploy-pages.yml`, `radar-engine.yml`, `update-weather-data.yml`).
- Contenuto della 1.2.4.24 (release sopra la 1.2.4.23) - **pulsante «Mappe previsionali»
  nella toolbar principale, pannello utilizzabile senza attivare Sviluppo**:
  - **Pulsante spostato**: `<button id="btn-forecast-maps">` era dentro
    `#weather-model-wrapper`, che `toggleSviluppo` mostra solo con Sviluppo ON (`display:none`
    altrimenti): con Sviluppo OFF non era cliccabile (misurato sulla 1.2.4.23: rect **0x0**,
    `offsetParent` **null**, `btnInWrapper` **true**). Ora e' il **primo elemento** di
    `.toolbar > .toolbar-row`, prima di `btn-radar-player`. Invariati `id`,
    `onclick="toggleForecastMaps()"`, emoji e `style`.
  - **Gate di Sviluppo rimosso**: in `toggleSviluppo` e' stata eliminata la guardia (2 righe di
    commento + `if` + chiamata) che chiudeva il pannello quando Sviluppo passava a OFF.
    `#forecast-maps-panel` e' un `div` autonomo dentro `.map-card`, **non** un figlio del
    wrapper, e `toggleForecastMaps()` non legge mai `isSviluppoActive`.
  - **Misurato** (Edge headless via CDP, cartella di release servita da `python -m http.server`,
    Sviluppo OFF): pulsante **176x29 px**, `display:flex`, `offsetParent:BODY`, primo figlio;
    click -> `#forecast-maps-panel.active`, `fmActive===true`, pannello **754x380 px**,
    `map.getSize()` **754x560 prima e dopo** (mappa non schiacciata), `fmInvalidateSize`
    **1** chiamata, tile canvas Leaflet **4 x 512x512**; `toggleSviluppo(true)` e
    `toggleSviluppo(false)` **non** chiudono il pannello (`fmActive` resta `true`) mentre sulla
    1.2.4.23 il solo `toggleSviluppo(false)` lo chiudeva (`fmActive` -> `false`, altezza -> 0).
  - **Pannello realmente funzionante** con Sviluppo OFF: dati **reali** dalla branch
    `fm2-rendered` (`domain/timeline.json` + `domain/index.json`) e `fm2-dwd`
    (`dwd_icon_eu/latest.json` + frame `.om`); status
    `Frame 4/96 · dom 25/10, 02:00 · run 00Z · 25 ott`, slider 0..95, selettore run nascosto
    (timeline attiva).
  - **Polling invariato**: `checkWorkflowDataUpdates()` gira ogni `WORKFLOW_DATA_POLL_MS` =
    **300000 ms** e le richieste a `latest.json`/`index.json`/`timeline.json` stanno tutte
    dentro il ramo `if (fmActive)`: a pannello chiuso **0** richieste a `index.json` e
    `timeline.json` (2 cicli forzati a mano -> solo `data/latest/metadata.json`; 60 s di
    attesa reale -> 0). I 404 locali (`data/latest/metadata.json`, `satellite/manifest.json`,
    `data/radar/latest.json`) sono l'assenza di `data/` nella cartella locale, non un difetto.
- Contenuto della 1.2.4.23 (storico, release sopra la 1.2.4.22) - **pannello Mappe previsionali
  robusto quando la timeline cita frame che non esistono piu'**:
  - **Frame iniziale utile**: all'apertura (e al cambio modello/variabile/run) il pannello
    parte dal primo frame del run piu' recente (`bucket 0`; se assente, dal bucket minimo
    disponibile), non dal frame 0 che nella timeline reale e' il piu' vecchio. Misurato sulla
    timeline reale della regione `domain` (dominio `dwd_icon_eu`, 102 frame): apertura al
    frame 9, `2026-10-23T18:00Z`, run `20261023T180000Z`, layer WebP caricato.
  - **Salto dei frame morti**: se il WebP fallisce (404/errore) il client passa al frame
    successivo (sull'ultimo, al precedente), marca l'URL come morto per la sessione e non lo
    ritenta piu'; il salto automatico si ferma dopo **8 tentativi consecutivi** con un
    messaggio esplicito. Misurato: 6 frame morti (run 09Z potata) saltati e frame 6 caricato;
    con tutti i frame assenti, 8 richieste e stop.
  - **Status coerente**: il path `.om` viene risolto solo quando il WebP non e' utilizzabile;
    il messaggio "Errore frame cache mappe previsionali mancante" non compare piu' sopra un
    WebP visibile e, a caricamento riuscito, lo status descrive il frame effettivo.
  - **Binario invariato**: segmenti colorati contigui e cursore rettangolare (10x16 px,
    raggio 3 px), nessun pallino.
  - **Fallback invariato**: senza `timeline.json` il pannello usa `index.json` come la
    1.2.4.22 (misurati 93 frame e selettore run visibile).
- Contenuto della 1.2.4.22 (storico, release sopra la 1.2.4.21) - **timeline dei frame**:
  la sequenza arriva da `data/fm2-rendered/<regione>/timeline.json` per dominio e variabile,
  il binario dello slider e' colorato per run in segmenti contigui e il selettore di run e'
  nascosto quando la timeline e' attiva, con fallback obbligatorio a `index.json`.
- Contenuto della 1.2.4.21 (storico, release sopra la 1.2.4.20) - **footprint geometrico e
  accoppiamento punto->poligono nel collaudo, nessuna soglia toccata**:
  - **Perche'**: il collaudo della 1.2.4.20 accoppiava il rapporto METAR al **centroide**
    con gate 10 km e dava 0 hit / 6 miss / 69 falsi allarmi. Non era un'assenza di eco
    (48,5 / 44,0 / 35,5 dBZ sulla verticale di LIPL/LIMF/LIPX): nessuna cella aveva il
    centroide entro 10 km (minimo 20,3 km) mentre le celle sono componenti connesse fino a
    **4.659 km2**, e la stazione cade **dentro** l'eco.
  - **`--footprints <path>`** (`scripts/radar_engine/footprint.py`): GeoJSON con il poligono
    (MultiPolygon, buchi inclusi) di **ogni cella** e di **ogni struttura**, ricavato dalla
    componente connessa sulla VMI con le soglie **della config** (`dbz_threshold`,
    `morph_radius_px`, `connectivity_8`) e georeferenziato con l'affine del GeoTIFF. Senza
    il flag l'output operativo e' **byte-identico** alla 1.2.4.20.
  - **Identita' della cella** per `bbox` + `pixel_count` dell'etichetta del frame (non un
    match geometrico fra blob): il pixel del centroide puo' cadere **fuori** da una
    componente concava e in 1.2.4.20 lasciava 3 strutture su 69 senza footprint
    (`cell_mask_not_resolved`); ora **69/69**. L'**overhang non e' stato toccato**.
  - **Accoppiamento** (`scripts/validation/geo_match.py`, `Detection.footprint`): punto
    dentro il poligono o entro 10 km dal bordo; se l'allarme non ha footprint resta la
    distanza dal centroide (regressione coperta dai test). `shapely` **non** e' installato e
    non e' stato aggiunto: point-in-ring, buchi, MultiPolygon e distanza punto-segmento sono
    implementati e testati qui.
  - **Collaudo** (`scripts/validation/asof_bench_footprint.py`, tre varianti dichiarate:
    celle / tutte le strutture / strutture severe) sui 6 eventi grandine METAR QC A/B:
    **celle 5 hit / 1 miss** (1294 allarmi non accoppiati, POD 0,833, Wilson [0,436-0,970],
    CSI 0,004 indicativo), **strutture 5 / 1** (64 non accoppiati, POD 0,833, CSI 0,071)
    contro **0 hit / 6 miss** del centroide. L'unico miss e' **LIMP** (nessuna cella entro
    40,5 km alle 14:50Z).
  - **Tasso base misurato per run** (obbligatorio accanto al POD): area d'Italia coperta
    **10,1%** mediana (celle) e **5,1%** (strutture), massimo 23,1% e 8,7%; stazioni su 99
    coperte **20,7%** e **14,1%** (massimo 37,4% e 21,2%); stazioni con eventi coperte
    "per caso" **26,9%** -> hit attesi a caso 1,6 su 6, P(X>=5)=0,0066. Sotto il 50% di area
    e di stazioni, quindi il POD sul footprint e' informativo. La variante **severa coincide
    con tutte le strutture** (69 record: 56 `strong` + 13 `intense`, solo candidati).
  - **Nessuna taratura**: n=6, nessuna soglia/formula/peso modificato. In replay MESH e
    ambiente sono `null` con motivo: il collaudo misura **SOI, overhang, VIL density**, non
    MESH. **FAR non stimabile** (13 stazioni severe = 1,35% del territorio).
  - **Documentazione**: `docs/REPLAY_AS_OF.md` §9 (definizioni, tasso base per run, matrice
    2x2 con Wilson, confronto con il centroide, limiti dichiarati). **Test**:
    `scripts/tests/radar_engine/test_footprint.py` (10 casi) e
    `scripts/tests/test_footprint_geometry.py` (11 casi).
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

- `mri-light-1.2.4.24.html` — applicazione (html/css/js unico, nessuna dipendenza runtime)
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

