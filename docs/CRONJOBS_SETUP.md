# Guida alla Configurazione dei Cronjob Esterni (cron-job.org / Webhook)

> ⚠️ **Fonte di verità degli orari: la disponibilità MISURATA su S3, non i tempi nominali dei modelli.**
> Ogni slot è tarato sul momento in cui il run è realmente completo su Open-Meteo, cioè sul `LastModified` massimo degli oggetti
> `data_spatial/<dominio>/<YYYY>/<MM>/<DD>/<HH>00Z/` (listing S3 `list-type=2`), **non** sull'orario nominale del modello e **non** sui ritardi dichiarati da Open-Meteo.
> La misura di riferimento è la **curva di riempimento per-frame del 2026-10-25T23:00Z** (21 run complete, 7 domini, `t100` = minuto in cui il run raggiunge il 100% dei frame); i valori sono riportati run per run in `scripts/cronjobs/cronjobs_config.json` → `measured_t100`.
> Se un provider cambia la propria tempistica, la tabella va ri-misurata con lo stesso criterio prima di toccare gli orari.

---

## 0. Stato attuale: tabella T17 (2026-10-26)

| | prima (T11) | ora (T17) |
| :--- | ---: | ---: |
| job di mappe su cron-job.org | 11 | **17** |
| dispatch/giorno (mappe) | **52** | **68** |
| dispatch/giorno (con il backup indice) | **56** | **72** |
| costo runner/giorno, **senza** render incrementale | **49,0 h** | **63,1 h** |
| costo runner/giorno, **con** render incrementale | **36,5 h** | **42,6 h** |

⚠️ **Correzione del conto dei dispatch.** La proposta parlava di "48 dispatch/giorno oggi" e quindi di "42,6 h contro 44,3 h (−4%)". Il conteggio era sbagliato: i **due job DWD hanno 8 slot ciascuno**, non 4, quindi oggi i dispatch sono **52** (56 con l'indice). Ricalcolato con lo **stesso modello** (`fill_study/day2.py`: 60 s di enumerazione per leg, `delta × s/frame` misurato per dominio, 45 s per un NO-OP, 300 s di `render-merge` + 500 s di `index` per ogni dispatch che pubblica):

| | T11 (52 dispatch) | T17 (68 dispatch) | Δ |
| :--- | ---: | ---: | ---: |
| frame renderizzati/giorno, senza incrementale | 5.545 | 7.093 | +28% |
| frame renderizzati/giorno, con incrementale | 3.220 | 3.508 | +9% |
| runner/giorno, senza incrementale | 49,0 h | 63,1 h | **+14,1 h (+29%)** |
| runner/giorno, con incrementale | 36,5 h | 42,6 h | **+6,1 h (+17%)** |

⚠️ **Il render incrementale fra dispatch diversi non esiste ancora** in `scripts/forecast_v2/render_fm_webp.py` (c'è solo lo skip intra-processo `seen_frames`): finché non viene implementato, la T17 costa **~63,1 h/giorno di runner, cioè +29%** rispetto alle 49,0 h della T11, e anche con l'incrementale costa **+17%**, non meno di oggi. Il lavoro sull'incrementale **non** è in questo cambio: è dichiarato qui perché non sia scoperto dopo.

Regola della T17:
- **ogni classe di run ha uno slot di CHIUSURA che parte 30-40 minuti DOPO il `t100` misurato**: la sorgente ha finito di pubblicare e il render non lavora su un run monco;
- **nessuno slot parte prima di +14 minuti dal primo frame misurato** (altrimenti il dispatch trova 0 frame, come faceva ICON-EU a +70);
- gli slot parziali e intermedi esistono solo per mostrare la mappa prima e **non** sostituiscono la chiusura.

Il difetto che la T17 chiude (misurato il 2026-10-25/26): con la tabella precedente ICON-EU aveva slot a **+70' (0% di frame)** e **+180' (25%)** con il `t100` a **+218'** e nessuno slot lì → pubblicava **28 frame su 93** e il dispatch successivo trovava 0 frame (NO-OP); NCEP passava **117 minuti al 17-36%**.

---

## 1. Perché usare cron-job.org invece del `schedule` nativo di GitHub Actions

I trigger `schedule` (cron) di GitHub Actions soffrono storicamente di:
- **Ritardi nell'accodamento**: Nelle ore di picco (specie ai minuti `:00` e `:30`), i workflow possono ritardare da 15 fino a 60+ minuti.
- **Mancata esecuzione (drop)**: In caso di congestione elevata dei runner pubblici, alcuni job possono saltare.
- **Disattivazione silente**: Se un repository non registra commit per oltre 60 giorni, GitHub disattiva automaticamente tutti gli schedule.

Invocando l'API REST di GitHub (`workflow_dispatch`) tramite **cron-job.org**:
- L'esecuzione parte al secondo esatto specificato.
- Ricevi log immediati e notifiche di fallimento (HTTP status e corpo della risposta).
- Puoi gestire tentativi automatici (retry) in caso di errori temporanei.

---

## 2. Creazione del GitHub Personal Access Token (PAT)

Per consentire a cron-job.org di avviare i workflow occorre un token di autenticazione:

1. Vai su GitHub su **Settings** > **Developer Settings** > **Personal Access Tokens** > **Fine-grained tokens** (oppure [Tokens (classic)](https://github.com/settings/tokens)).
2. Clicca su **Generate new token**.
3. Compila i campi:
   - **Token name**: `cronjob-org-meteorisk-light`
   - **Expiration**: 1 anno (o personalizzata)
   - **Repository access**: *Only select repositories* > seleziona `meteoriskitalia-netizen/MeteoRisk-Light-v2`
   - **Permissions** (Repository permissions):
     - **Actions**: `Read and write`
     - **Contents**: `Read-only` (default)
4. Clicca su **Generate token** e copia la stringa generata (es. `github_pat_11...` o `ghp_...`).
   > ⚠️ **Sicurezza**: Non inserire mai il token in file pubblici committati su GitHub. Su cron-job.org vive **solo** nell'header `Authorization` dei job; `setup_cronjobs_api.js` non lo stampa e, per i job nuovi, lo **copia** dagli header di un job esistente dello stesso workflow.

---

## 3. Matrice orari T17

Tutti gli orari sono **UTC**. Su cron-job.org `hours[] × minutes[]` è un **prodotto cartesiano**: per avere minuti diversi a ore diverse servono job diversi. La T17 sfrutta il fatto che cicli principali e secondari hanno **ore disgiunte**, quindi ogni classe può avere i propri minuti senza job aggiuntivi.

### 3.1 La misura (t100, minuti dopo l'ora nominale del run)

| dominio | classe | run | frame | 1° frame | 50% | 100% (`t100`) |
| :--- | :--- | :--- | ---: | ---: | ---: | ---: |
| `dwd_icon_d2` | unica | 2100Z / 1800Z / 1500Z | 49 | +46,4 | +65,6 | **+82,8** / **+82,8** / **+84,3** |
| `dwd_icon_eu` | principale 00/06/12/18 | 1800Z / 1200Z | 93 | +167,5 | +192,6 | **+218,5** |
| `dwd_icon_eu` | secondaria 03/09/15/21 | 1500Z | 31 | +165,8 | +169,7 | **+174,6** |
| `dwd_icon` | principale 00/12 | 1200Z | 113 | +162,6 | +182,5 | **+208,0** |
| `dwd_icon` | secondaria 06/18 | 1800Z / 0600Z | 93 | +166,1 / +162,6 | +180,1 / +176,5 | **+195,9** / **+193,9** |
| `ecmwf_ifs025` | principale 00/12 | 1200Z / 0000Z | 85 | +456,0 / +456,7 | +517,4 / +516,2 | **+547,8** / **+557,0** |
| `ecmwf_ifs025` | secondaria 06/18 | 0600Z | 49 | +421,7 | +428,6 | **+443,3** |
| `ncep_gfs025` | unica | 1200Z / 0600Z | 209 | +220,5 / +220,4 | +280,6 / +257,5 | **+342,6** / **+314,4** |
| `ncep_gfs013` | unica | 1200Z / 0600Z | 209 | +220,2 | +245,0 / +240,2 | **+314,6** / **+313,2** |
| `meteofrance_arpege_world025` | veloce 00/12 | 1200Z | 67 | +196,8 | +227,9 | **+259,1** |
| `meteofrance_arpege_world025` | lenta 06/18 | 0600Z | 67 | +243,8 | +284,9 | **+332,0** |

Le classi non sono una convenzione: sono **orizzonti diversi** (`ICON-EU` 93 frame su 00/06/12/18 e **31** su 03/09/15/21; `ICON` 113 su 00/12 e 93 su 06/18; `ECMWF` 85 su 00/12 e 49 su 06/18; `GFS` 209 sempre; `ARPEGE` 67 sempre) con `t100` che differiscono fino a **73 minuti**. Un unico "slot completo" per entrambe le classi è la ragione per cui la tabella precedente era sbagliata.

### 3.2 I 17 job (68 dispatch/giorno) + backup indice

| # | Title su cron-job.org | Workflow | Orari UTC | offset | copertura allo slot | margine su `t100` | margine 1° frame |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: | ---: |
| 1 | `MeteoRisk - DWD ICON EU + D2 (parziale)` | `forecast-maps-dwd.yml` | 01:15, 04:15, 07:15, 10:15, 13:15, 16:15, 19:15, 22:15 | +75' | 46-77% | — (parziale) | **+28'** |
| 2 | `MeteoRisk - DWD ICON-D2 (completo)` | `forecast-maps-dwd.yml` | 01:57, 04:57, 07:57, 10:57, 13:57, 16:57, 19:57, 22:57 | +117' | **100%** | **+33..+34'** | +70' |
| 3 | `MeteoRisk - DWD ICON EU + D2 (completo)` | `forecast-maps-dwd.yml` | 04:10, 10:10, 16:10, 22:10 | +250' | **100%** | **+32'** | +82' |
| 4 | `MeteoRisk - DWD ICON-EU (completo, cicli 03/09/15/21)` | `forecast-maps-dwd.yml` | 00:30, 06:30, 12:30, 18:30 | +210' | **100%** | **+35'** | +44' |
| 5 | `MeteoRisk - DWD ICON Global (parziale)` | `forecast-maps-dwdicon.yml` | 03:05, 09:05, 15:05, 21:05 | +185' | 56-84% | — (parziale) | **+19'** |
| 6 | `MeteoRisk - DWD ICON Global (completo)` | `forecast-maps-dwdicon.yml` | 04:03, 16:03 | +243' | **100%** | **+35'** | +80' |
| 7 | `MeteoRisk - DWD ICON Global (completo, cicli 06/18)` | `forecast-maps-dwdicon.yml` | 09:48, 21:48 | +228' | **100%** | **+32..+34'** | +62' |
| 8 | `MeteoRisk - NCEP GFS (parziale)` | `forecast-maps-ncep.yml` | 03:55, 09:55, 15:55, 21:55 | +235' | 12-37% | — (parziale) | **+15'** |
| 9 | `MeteoRisk - NCEP GFS (intermedio)` | `forecast-maps-ncep.yml` | 04:50, 10:50, 16:50, 22:50 | +290' | 58-85% | — (intermedio) | +70' |
| 10 | `MeteoRisk - NCEP GFS (intermedio +5h30)` | `forecast-maps-ncep.yml` | 05:30, 11:30, 17:30, 23:30 | +330' | 90-100% | — (intermedio) | +110' |
| 11 | `MeteoRisk - NCEP GFS (completo)` | `forecast-maps-ncep.yml` | 00:15, 06:15, 12:15, 18:15 | +375' | **100%** | **+32..+62'** | +155' |
| 12 | `MeteoRisk - ECMWF IFS 0.25 (parziale)` | `forecast-maps-ecmwf.yml` | 01:55, 07:55, 13:55, 19:55 | +475' | **100%** sui cicli 06/18, 16% sui 00/12 | **+32'** (06/18) | **+18'** |
| 13 | `MeteoRisk - ECMWF IFS 0.25 (princ. +8h40)` | `forecast-maps-ecmwf.yml` | 08:40, 20:40 | +520' | 53% | — (intermedio) | +63' |
| 14 | `MeteoRisk - ECMWF IFS 0.25 (completo)` | `forecast-maps-ecmwf.yml` | 09:47, 21:47 | +587' | **100%** | **+30..+39'** | +130' |
| 15 | `MeteoRisk - ARPEGE 0.25 (parziale)` | `forecast-maps-meteofrance.yml` | 04:20, 10:20, 16:20, 22:20 | +260' | **100%** sui cicli veloci, 21% sui lenti | — (parziale) | **+16'** |
| 16 | `MeteoRisk - ARPEGE 0.25 (intermedio +4h55)` | `forecast-maps-meteofrance.yml` | 04:55, 10:55, 16:55, 22:55 | +295' | **100%** veloci, 82% lenti | **+36'** (veloci) | +51' |
| 17 | `MeteoRisk - ARPEGE 0.25 (completo)` | `forecast-maps-meteofrance.yml` | 00:10, 06:10, 12:10, 18:10 | +370' | **100%** | **+38..+111'** | +126' |
| — | `MeteoRisk - WebP Index Backup` | `forecast-maps-index.yml` | 03:50, 11:50, 17:50, 23:50 | invariato | — | — | — |

`offset` = minuti dopo l'ora nominale del run servito. Le ore di ogni job si **derivano** da `(ora del run + offset) mod 24` e nel config vengono verificate contro l'elenco (un offset sbagliato non può passare inosservato).

### 3.3 Margine per run: la prova che nessuno slot parte a vuoto

| Provider | Run | Chiusura | `t100` | Margine | Parziale/intermedio | Margine sul 1° frame |
| :--- | :--- | :--- | ---: | ---: | :--- | ---: |
| DWD | 00Z / 06Z / 12Z / 18Z | `04:10` / `10:10` / `16:10` / `22:10` | +218,5 | **+31,5'** | `01:15` (D2 77%) | +28,6' |
| DWD | 03Z / 09Z / 15Z / 21Z | `06:30` / `12:30` / `18:30` / `00:30` | +174,6 | **+35,4'** | `01:15` del ciclo successivo | +28,6' |
| DWD (solo D2) | tutti gli 8 run | `01:57` … | +82,8 / +84,3 | **+32,7 / +30,7'** | `01:15` (D2 46-77%) | +28,1' |
| DWD ICON Global | 00Z / 12Z | `04:03` / `16:03` | +208,0 | **+35,0'** | `03:05` / `15:05` (56-84%) | +22,4' |
| DWD ICON Global | 06Z / 18Z | `09:48` / `21:48` | +193,9 / +195,9 | **+34,1 / +32,1'** | `09:05` / `21:05` | +18,9' |
| ARPEGE | 00Z / 12Z | `04:55` / `16:55` (poi `06:10` / `18:10`) | +259,1 | **+35,9'** poi +110,9' | `04:20` / `16:20` (100% / 21%) | +63,2 / +16,2' |
| ARPEGE | 06Z / 18Z | `06:10` / `18:10` | +332,0 | **+38,0'** | `10:20` / `22:20` (21%) + `10:55` / `22:55` (82%) | +16,2' |
| NCEP | 00Z / 06Z / 12Z / 18Z | `06:15` / `12:15` / `18:15` / `00:15` | +314,4 … +342,6 | **+32,4 … +61,8'** | `03:55` (12-37%), `04:50` (58-85%), `05:30` (90-100%) | +14,5' |
| ECMWF | 00Z / 12Z | `09:47` / `21:47` | +547,8 / +557,0 | **+39,2 / +30,0'** | `07:55` / `19:55` (16%), `08:40` / `20:40` (53%) | +18,3' |
| ECMWF | 06Z / 18Z | `13:55` / `01:55` | +443,3 | **+31,7'** | — (già 100% al primo slot) | +53,3' |

### 3.4 Cosa è cambiato rispetto alla tabella proposta il 2026-10-26 e perché

La proposta (`fill_study/REPORT.md`, 17 job / 68 dispatch) era un calcolo, non un dato: ricalcolata sui `t100`, **7 slot su 17 sono stati spostati** e uno è stato diviso.

| # | proposta | applicato | perché |
| ---: | :--- | :--- | :--- |
| 2 | `01:50` (+110', D2 100%, `t100`+27') | `01:57` (+117', **+33'**) | 27' è fuori dalla banda 30-40; a `:55` collideva con ECMWF (`01:55`) e ARPEGE (`04:55`) |
| 3 | `03:50` (+230', EU princ 100%, `t100`+12') | `04:10` (+250', **+32'**) | 12' di margine sul `t100` è la stessa classe di difetto che si voleva chiudere |
| 4 | `03:10` (+190', EU second 100%, `t100`+15') | `00:30` (+210', **+35'**) | idem: 15' è sotto la banda |
| 5 | `03:00` (+180') | `03:05` (+185') | margine sul primo frame della run 18Z = **13,9' < 14'** |
| 6 | `03:35` (+215', `t100`+7..20') | **diviso**: `04:03` (+243', princ **+35'**) e `09:48` (+228', second **+32..34'**) | un solo orario non può stare a +30-40' da `t100` 208 e 196: +7' è troppo poco. Costo: **+1 job**, compensato dalla fusione della riga 11+14 |
| 10 | `06:05` (+365', `t100`+22') | `06:15` (+375', **+32'**) | 22' fuori banda |
| 11+14 | `07:50` (+470') e `13:45` (+465') | **fusi** in `07:55`/`13:55`/`01:55`/`19:55` (+475') | +470 dava 13' di margine sul primo frame della run 00Z; a +475 i cicli 06/18 chiudono al **+31,7'** e i 00/12 sono al 16% |
| 13 | `09:20` (+560', `t100`+3..12') | `09:47` (+587', **+30..39'**) | **+3 minuti** dal `t100` era il margine più sottile di tutta la tabella |
| 15 | `04:10` (+250') | `04:20` (+260') | margine sul primo frame della run lenta = **+6,2' < 14'** (e 04:20 è l'orario già in produzione) |
| 17 | `05:45` (+345', `t100`+13') | `06:10` (+370', **+38'**) | 13' fuori banda; +370 è anche lo slot che chiude la classe lenta |

Righe **non** toccate: 1 (`01:15`), 7 (`03:55`), 8 (`04:50`), 9 (`05:30`), 12 (`08:40`), 16 (`04:55`). Totale: **17 job, 68 dispatch/giorno** — come la proposta, con uno split e una fusione.

### 3.5 Deviazioni dichiarate (impossibili da chiudere con un solo orario)

- **NCEP completo** (`+375'`): `gfs025` ha `t100` **+314,4** (run 06Z) e **+342,6** (run 12Z), cioè **28 minuti di dispersione dentro lo stesso dominio**. Un orario solo non può stare a +30/+40 da entrambi: `+375` dà **+32,4'** sulla run peggiore e **+60..+62'** sulle altre. Nessuna run viene dispatchata prima del 100%; le altre sono solo più "vecchie" di 60'.
- **ARPEGE completo** (`+370'`): le due classi distano **73 minuti** (`t100` +259,1 vs +332,0). La classe veloce chiude già a **+295'** (job 16, +35,9'), la lenta a **+370'** (+38,0'). La classificazione "veloce = 00/12" è un'**inferenza** su 3 run (12Z veloce, 06Z lento, 18Z incompleta ma con primo frame `+240,2` e 50% `+286,9`, cioè lenta): non è una climatologia.
- **DWD job 3 e 4** dispatchano **entrambi i domini** (`ICON-EU` + `ICON-D2`): a +250' e +210' ICON-D2 è al 100% da oltre 2 ore. Non è una scelta, è la matrice del workflow.
- **Costo**: vedi §0. Con l'incrementale 42,6 h/giorno, senza 63,1 h/giorno (oggi: 36,5 h e 49,0 h).

### 3.6 Rete di sicurezza nativa (cron GitHub)

Il blocco `schedule:` nativo **non esiste più**: è stato **RIMOSSO dai 5 workflow per-fornitore nella 1.2.4.23** (2026-10-25). Verificato il 2026-10-26 sui file di `main` (`forecast-maps-dwd.yml` e `forecast-maps-ncep.yml`): sotto `on:` c'è `workflow_dispatch` seguito dal commento "1.2.4.23 - `schedule:` NATIVO RIMOSSO (2026-10-25). Il trigger di questo fornitore e' SOLO cron-job.org". Il trigger delle mappe è quindi **solo** cron-job.org; resta nativo il solo `50 */6 * * *` di `forecast-maps-index.yml` (recupero periodico dell'indice).

Conseguenza dichiarata: se cron-job.org non dispatcha, il run non parte. È il motivo per cui la tabella di §3 va ri-misurata quando un provider cambia tempistica.

> ℹ️ **Nota Fuso Orario**: tutti i job della T17 sono su **UTC**. In ora solare italiana (CET) corrisponde a UTC+1; in ora legale (CEST) a UTC+2. Mantenere UTC garantisce che le run meteo restino allineate indipendentemente dal cambio dell'ora.

---

## 4. Applicazione della tabella (cron-job.org REST API)

`scripts/cronjobs/cronjobs_config.json` è la **fonte unica**: `scripts/cronjobs/setup_cronjobs_api.js` non contiene nessuna costante di orario e applica esattamente quel file. I 5 titoli della tabella a 1 slot (2026-10-21) restano nella lista `retired_job_titles` e vengono **disabilitati** se riappaiono attivi.

```bash
# 1. Piano senza rete (non serve la API key)
node scripts/cronjobs/setup_cronjobs_api.js --dry-run

# 2. Confronto col vivo, sola lettura (serve CRONJOB_API_KEY)
#    con la tabella gia' applicata il piano deve dire "0 modifiche"
node scripts/cronjobs/setup_cronjobs_api.js --diff

# 3. Applicazione (PATCH sui job esistenti, PUT su quelli nuovi)
export CRONJOB_API_KEY="..."        # la API key di cron-job.org
node scripts/cronjobs/setup_cronjobs_api.js
```

Note operative:

- **Idempotenza**: se il job vivo ha già titolo, url, `enabled`, `saveResponses`, `requestMethod` e `schedule` uguali al config, lo script **non fa nessuna chiamata di scrittura**. Un secondo passaggio è un no-op.
- **`PATCH /jobs/<id>`** per i job esistenti (aggiorna solo i campi diversi), **`PUT /jobs`** per crearne di nuovi. `POST /jobs` **non** esiste (risponde 404).
- **Header**: i job nuovi ereditano gli header (compreso `Authorization`) da un job esistente **dello stesso workflow**; se `GH_TOKEN` è in ambiente, quello vince. Il token non viene mai stampato né riscritto in una PATCH di sola schedulazione.
- **Body**: per i workflow delle mappe `{"ref":"main","inputs":{"force_update":false}}`. Per `forecast-maps-index` il body NON deve contenere `force_update` (il workflow non lo dichiara): l'API risponde **HTTP 422 `Unexpected inputs provided: ["force_update"]`**. Il job in produzione usa `{"ref":"main","inputs":{}}` (oggetto `inputs` vuoto: nessun input nominato) ed è lasciato così.
- **Limiti free di cron-job.org**: **100 richieste API/giorno**, **1 req/s**, max **5 creazioni/minuto**, timeout HTTP **30 s**; un job viene disabilitato dopo **25 fallimenti consecutivi**. Lo script mette 13 s fra una creazione e l'altra e 1,5 s fra gli aggiornamenti.
- **`requestMethod: 1`** = POST. Il campo `schedule` è un **oggetto** `{timezone, hours[], minutes[], mdays[], months[], wdays[]}` con `-1` = "tutti", **non** una stringa cron.
- **Verifica**: lo script rilegge `/jobs` dopo l'applicazione e confronta di nuovo (con `--check-body` anche il body via `GET /jobs/<id>`); se qualcosa non torna esce con codice 1.

### 4.1 Schede di configurazione (18 job gestiti)

| # | Title (cron-job.org) | Workflow | Cron (UTC) |
| ---: | :--- | :--- | :--- |
| 1 | `MeteoRisk - DWD ICON EU + D2 (parziale)` | `forecast-maps-dwd.yml` | `15 1,4,7,10,13,16,19,22 * * *` |
| 2 | `MeteoRisk - DWD ICON-D2 (completo)` | `forecast-maps-dwd.yml` | `57 1,4,7,10,13,16,19,22 * * *` |
| 3 | `MeteoRisk - DWD ICON EU + D2 (completo)` | `forecast-maps-dwd.yml` | `10 4,10,16,22 * * *` |
| 4 | `MeteoRisk - DWD ICON-EU (completo, cicli 03/09/15/21)` | `forecast-maps-dwd.yml` | `30 0,6,12,18 * * *` |
| 5 | `MeteoRisk - DWD ICON Global (parziale)` | `forecast-maps-dwdicon.yml` | `5 3,9,15,21 * * *` |
| 6 | `MeteoRisk - DWD ICON Global (completo)` | `forecast-maps-dwdicon.yml` | `3 4,16 * * *` |
| 7 | `MeteoRisk - DWD ICON Global (completo, cicli 06/18)` | `forecast-maps-dwdicon.yml` | `48 9,21 * * *` |
| 8 | `MeteoRisk - NCEP GFS (parziale)` | `forecast-maps-ncep.yml` | `55 3,9,15,21 * * *` |
| 9 | `MeteoRisk - NCEP GFS (intermedio)` | `forecast-maps-ncep.yml` | `50 4,10,16,22 * * *` |
| 10 | `MeteoRisk - NCEP GFS (intermedio +5h30)` | `forecast-maps-ncep.yml` | `30 5,11,17,23 * * *` |
| 11 | `MeteoRisk - NCEP GFS (completo)` | `forecast-maps-ncep.yml` | `15 0,6,12,18 * * *` |
| 12 | `MeteoRisk - ECMWF IFS 0.25 (parziale)` | `forecast-maps-ecmwf.yml` | `55 1,7,13,19 * * *` |
| 13 | `MeteoRisk - ECMWF IFS 0.25 (princ. +8h40)` | `forecast-maps-ecmwf.yml` | `40 8,20 * * *` |
| 14 | `MeteoRisk - ECMWF IFS 0.25 (completo)` | `forecast-maps-ecmwf.yml` | `47 9,21 * * *` |
| 15 | `MeteoRisk - ARPEGE 0.25 (parziale)` | `forecast-maps-meteofrance.yml` | `20 4,10,16,22 * * *` |
| 16 | `MeteoRisk - ARPEGE 0.25 (intermedio +4h55)` | `forecast-maps-meteofrance.yml` | `55 4,10,16,22 * * *` |
| 17 | `MeteoRisk - ARPEGE 0.25 (completo)` | `forecast-maps-meteofrance.yml` | `10 0,6,12,18 * * *` |
| 18 | `MeteoRisk - WebP Index Backup` | `forecast-maps-index.yml` | `50 3,11,17,23 * * *` |

> ⚠️ **I 5 titoli della tabella a 1 slot (`MeteoRisk - DWD ICON EU + D2`, `- DWD ICON Global`, `- ARPEGE 0.25`, `- NCEP GFS`, `- ECMWF IFS 0.25`) restano DISABILITATI**: se riappaiono attivi continuano a dispatchare agli orari vecchi. `setup_cronjobs_api.js` li disabilita da solo (lista `retired_job_titles`) e stampa l'esito.

---

## 5. Test e Verifica tramite CLI

È disponibile lo script `scripts/cronjobs/test_dispatch.sh` per testare la configurazione o lanciare workflow manualmente:

```bash
# 1. Simulare la chiamata e visualizzare l'esatto comando cURL:
./scripts/cronjobs/test_dispatch.sh --dry-run

# 2. Elencare tutti i modelli e file di workflow associati:
./scripts/cronjobs/test_dispatch.sh --list

# 3. Eseguire una chiamata reale per uno specifico modello:
export GH_TOKEN="ghp_tuo_token_qui"
./scripts/cronjobs/test_dispatch.sh --model dwd

# 4. Forzare l'aggiornamento ignorando il NO-OP guard:
./scripts/cronjobs/test_dispatch.sh --model dwd --force
```

In caso di successo, GitHub risponderà con `HTTP 204 No Content` e il workflow risulterà immediatamente visibile nella scheda **Actions** del repository su GitHub.
