# Guida alla Configurazione dei Cronjob Esterni (cron-job.org / Webhook)

> ⚠️ **Fonte di verità degli orari: la disponibilità MISURATA su S3, non i tempi nominali dei modelli.**
> Ogni slot della matrice in §3 è stato tarato sul momento in cui il run è realmente completo su Open-Meteo, cioè sul `LastModified` massimo degli oggetti
> `data_spatial/<dominio>/<YYYY>/<MM>/<DD>/<HH>00Z/` (listing S3 `list-type=2`), **non** sull'orario nominale del modello e **non** sui ritardi dichiarati da Open-Meteo.
> I valori misurati di riferimento sono riportati job per job anche in `scripts/cronjobs/cronjobs_config.json` nel campo `measured_utc_availability`: misura 2026-10-21 e 2026-10-24 e, per DWD e NCEP, la **curva di riempimento per-frame del 2026-10-26** (21 run complete).
> Se un provider cambia la propria tempistica, la tabella va ri-misurata con lo stesso criterio prima di toccare gli orari.

Questa guida spiega come configurare un servizio esterno di cron (ad esempio [cron-job.org](https://cron-job.org/en/)) per avviare con precisione e puntualità i workflow di GitHub Actions di MeteoRisk Light, sincronizzandoli con l'effettiva disponibilità delle run dei modelli meteorologici su Open-Meteo.

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
   > ⚠️ **Sicurezza**: Non inserire mai il token direttamente in file pubblici committati su GitHub. Nei cronjob su cron-job.org viene memorizzato in modo cifrato negli Header della richiesta.

---

## 3. Matrice Orari e Calibrazione Modelli

Tutti gli orari sono tarati su **tempi MISURATI**: misura 2026-10-21 e 2026-10-24 (ore dopo il run nominale, **primo step** = prima pubblicazione utile, **run completo** = ultimo frame utile) e, per DWD e NCEP, **curva di riempimento per-frame del 2026-10-26** ricostruita dai `LastModified` di **ogni** frame su S3 su **21 run complete** (a che percentuale e' completo il run a ogni istante). Non sono i ritardi dichiarati da Open-Meteo ne' i tempi nominali dei modelli. Le misure precedenti restano registrate in `measured_utc_availability` di ogni job.

**Regola dei 2 slot per run** (DWD, DWD ICON Global, ARPEGE, ECMWF) **e dei 3 slot per run** (NCEP). Il primo slot parte subito dopo il **primo step** e pubblica un **run parziale** (`--allow-partial-run`, ~24-30 frame su ~209): la mappa e' visibile ore prima. Lo slot che chiude la coda parte quando la **curva per-frame** dice che il run e' completo. Costo dichiarato: **un render in piu' per run**, modesto perche' ai primi step i frame sono ~24-30.
Margine: **+10..15 min** sul tempo in cui la curva arriva al 100%, e **nessuno slot oltre +30 min** (vincolo esplicito: un margine piu' largo annullerebbe il guadagno del primo slot). Su cron-job.org un job copre un solo orario di minuto, quindi **2 slot per run = 2 job per provider** e **3 slot per run = 3 job** (solo NCEP). Due slot a meno di 20 minuti si accorpano: qui e' successo solo per DWD (vedi 3.2).

| Modello Meteo | Workflow GitHub | Run Nominali (UTC) | Primo step (misurato) | Run completo (misurato) | Slot parziale (UTC) | Slot completo (UTC) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DWD ICON-EU & ICON-D2** | `forecast-maps-dwd.yml` | 00, 03, 06, 09, 12, 15, 18, 21 | ICON-D2 **+0h46**<br>ICON-EU **+2h47** | ICON-D2 **+1h23**<br>ICON-EU **+3h38** (cicli 00/06/12/18)<br>ICON-EU **+2h55** (cicli 03/09/15/21) | **8 slot**<br>`01:10, 04:10, 07:10, 10:10, 13:10, 16:10, 19:10, 22:10` | **8 slot**<br>`00:50, 03:50, 06:50, 09:50, 12:50, 15:50, 18:50, 21:50` |
| **DWD ICON Global** | `forecast-maps-dwdicon.yml` | 00, 06, 12, 18 | **+2h46** | **+3h28** | **4 slot**<br>`03:01, 09:01, 15:01, 21:01` | **4 slot**<br>`03:41, 09:41, 15:41, 21:41` |
| **Meteo-France ARPEGE 0.25** | `forecast-maps-meteofrance.yml` | 00, 06, 12, 18 | **+3h17 .. +4h08** | **+5h16** | **4 slot**<br>`04:20, 10:20, 16:20, 22:20` | **4 slot**<br>`05:29, 11:29, 17:29, 23:29` |
| **NCEP GFS (0.25 + 0.117)** | `forecast-maps-ncep.yml` | 00, 06, 12, 18 | **+3h40** (gfs025) | **+5h39** (gfs025) | **4 slot**<br>`03:55, 09:55, 15:55, 21:55`<br>(copertura **17-36%**) | **4 slot**<br>`05:52, 11:52, 17:52, 23:52`<br>(**~100%**) |
| **ECMWF IFS 0.25** | `forecast-maps-ecmwf.yml` | 00, 06, 12, 18 | **+7h01 .. +7h36** | **+9h40** | **4 slot**<br>`01:48, 07:48, 13:48, 19:48` | **4 slot**<br>`03:52, 09:52, 15:52, 21:52` |
| **WebP Index Aggregator** | `forecast-maps-index.yml` | Post-merge (automatico) | n/a | n/a | **Backup ogni 6 ore**<br>`03:50, 11:50, 17:50, 23:50` | - |

> ⚠️ **NCEP ha un terzo slot** (dal 2026-10-26): `04:50, 10:50, 16:50, 22:50` UTC (+4h50 dal run nominale), copertura **71-85%** secondo la curva per-frame. Non e' ne' parziale ne' completo: e' il job `MeteoRisk - NCEP GFS (intermedio)`, che si **aggiunge** ai due esistenti (non li sostituisce).

### 3.1 Slot per run e margine effettivo

Margine = slot meno il tempo misurato che quello slot deve servire.

| Provider | Run nominale | Slot 1 (parziale) | Margine | Slot 2 (completo) | Margine |
| :--- | :--- | :--- | :--- | :--- | :--- |
| DWD | 00Z | `01:10` | +24' su D2 +0h46 (ICON-EU non ancora pubblicato: primo step +2h47) | `03:50` | +12' su ICON-EU completo +3h38 (ICON-D2 completo da +1h37) |
| DWD | 03Z | `04:10` | +24' su D2 +0h46 | `06:50` | +55' su ICON-EU completo +2h55 (ciclo secondario) |
| DWD | 06Z | `07:10` | +24' su D2 +0h46 | `09:50` | +12' su ICON-EU completo +3h38 |
| DWD | 09Z | `10:10` | +24' su D2 +0h46 | `12:50` | +55' su ICON-EU completo +2h55 (ciclo secondario) |
| DWD | 12Z | `13:10` | +24' su D2 +0h46 | `15:50` | +12' su ICON-EU completo +3h38 |
| DWD | 15Z | `16:10` | +24' su D2 +0h46 | `18:50` | +55' su ICON-EU completo +2h55 (ciclo secondario) |
| DWD | 18Z | `19:10` | +24' su D2 +0h46 | `21:50` | +12' su ICON-EU completo +3h38 |
| DWD | 21Z | `22:10` | +24' su D2 +0h46 | `00:50` | +55' su ICON-EU completo +2h55 (ciclo secondario) |
| DWD ICON Global | 00Z / 06Z / 12Z / 18Z | `03:01` / `09:01` / `15:01` / `21:01` | +15' | `03:41` / `09:41` / `15:41` / `21:41` | +13' |
| ARPEGE 0.25 | 00Z / 06Z / 12Z / 18Z | `04:20` / `10:20` / `16:20` / `22:20` | +12' (sul massimo +4h08; +63' sul minimo +3h17) | `05:29` / `11:29` / `17:29` / `23:29` | +13' |
| NCEP GFS | 00Z / 06Z / 12Z / 18Z | `03:55` / `09:55` / `15:55` / `21:55` | +15' (copertura 17-36%) | `05:52` / `11:52` / `17:52` / `23:52` | +13' (copertura ~100%) |
| NCEP GFS — slot intermedio | 00Z / 06Z / 12Z / 18Z | `04:50` / `10:50` / `16:50` / `22:50` | copertura 71-85% a +4h50 (fra il parziale al 17-36% e il completo a ~100%) | — | — |
| ECMWF IFS 0.25 | 00Z / 06Z / 12Z / 18Z | `07:48` / `13:48` / `19:48` / `01:48` | +12' (sul massimo +7h36; +47' sul minimo +7h01) | `09:52` / `15:52` / `21:52` / `03:52` | +12' |

> ⚠️ **DWD: un trigger lancia entrambi i domini.** ICON-EU e ICON-D2 stanno nella stessa matrice, quindi ogni slot agisce su tutti e due. Conseguenza dichiarata: allo slot `01:10` ICON-EU non ha ancora pubblicato nulla (primo step +2h47) e il suo NO-OP guard lo chiude in pochi minuti; allo slot `03:50` ICON-D2 e' completo da 2h27 (+83' dal run completo a +1h23) e ICON-EU e' appena arrivato al 100% (+12' su +3h38). Prima del 2026-10-26 lo slot completo era alle `03:00`: a quell'ora ICON-EU era completo solo nel **25%** dei casi e la run veniva sostituita dal ciclo successivo.

### 3.2 Accorpamenti dichiarati

- **DWD**: i ruoli per-dominio sarebbero 4 (D2 parziale `00:59`, D2 completo `01:36`, EU parziale `03:00`, EU completo `04:10`). D2 completo (`01:36`) e D2 parziale del run dopo (`03:59`) stanno a 11' l'uno dall'altro e il completo EU (`04:10`) coincide con lo slot `01:10` del run successivo: 4 slot per run darebbero 32 trigger/giorno con due coppie sotto i 20'. Sono stati ridotti a **2 trigger per run** (`01:10` e `03:50`), ognuno con due ruoli. Costo dichiarato: i 209 frame completi di ICON-D2 arrivano alle `03:50` invece che alle `01:36` (+2h14), mentre ICON-D2 parziale e' gia' visibile dalle `01:10`.
- **Nessun altro accorpamento**: gli altri provider hanno gli slot a 40'-124' di distanza (NCEP, con tre slot: 55' fra parziale e intermedio, 62' fra intermedio e completo).

### 3.3 Rete di sicurezza nativa (cron GitHub)

Il blocco `schedule:` nativo **non esiste piu'**: e' stato **RIMOSSO dai 5 workflow per-fornitore nella 1.2.4.23** (2026-10-25). Verificato il 2026-10-26 sui file di `main` (`forecast-maps-dwd.yml` e `forecast-maps-ncep.yml`): sotto `on:` c'e' `workflow_dispatch` seguito dal commento "1.2.4.23 - `schedule:` NATIVO RIMOSSO (2026-10-25). Il trigger di questo fornitore e' SOLO cron-job.org". Il trigger delle mappe e' quindi **solo** cron-job.org; resta nativo il solo `50 */6 * * *` di `forecast-maps-index.yml` (recupero periodico dell'indice).

Conseguenza dichiarata: se cron-job.org non dispatcha, il run non parte. E' il motivo per cui la tabella di §3 va ri-misurata quando un provider cambia tempistica.

> ℹ️ **Nota Fuso Orario**: Impostare il fuso orario di ciascun job su cron-job.org su **UTC** (Coordinated Universal Time). In ora solare italiana (CET) corrisponde a UTC+1; in ora legale (CEST) corrisponde a UTC+2. Mantenere UTC garantisce che le run meteo rimangano sempre allineate indipendentemente dal cambio dell'ora.

---

## 4. Configurazione Passo-Passo su cron-job.org

Servono **12 job**: 2 per ciascuno dei 5 provider, **3 per NCEP** (lo slot intermedio `04:50` aggiunto il 2026-10-26) e il backup dell'indice. I valori sotto sono la copia esatta di `scripts/cronjobs/cronjobs_config.json`; `scripts/cronjobs/setup_cronjobs_api.js` li applica leggendo quel file (non ci sono piu' costanti duplicate: era il difetto per cui rieseguire lo script riportava i job agli orari vecchi).

### Parametri Comuni a tutti i job:
- **Request Method**: `POST`
- **Request Headers** (Aggiungi 5 header):
  ```
  Accept: application/vnd.github+json
  Authorization: Bearer INSERISCI_QUI_IL_TUO_GITHUB_TOKEN
  X-GitHub-Api-Version: 2022-11-28
  User-Agent: MeteoRisk-CronJob/1.0
  Content-Type: application/json
  ```
- **Request Body (JSON)**:
  ```json
  {"ref":"main","inputs":{"force_update":false}}
  ```
  (il job di backup dell'indice usa `{"ref":"main"}` senza `inputs`: con `inputs` l'API risponde HTTP 422)
- **Expected Status Code**: `204` (No Content e' la risposta standard di successo di GitHub Actions)

---

### Schede di Configurazione (12 job)

URL: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/<workflow_file>/dispatches`

| # | Title (cron-job.org) | Workflow | Cron (UTC) | Orari |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `MeteoRisk - DWD ICON EU + D2 (parziale)` | `forecast-maps-dwd.yml` | `10 1,4,7,10,13,16,19,22 * * *` | 01:10, 04:10, 07:10, 10:10, 13:10, 16:10, 19:10, 22:10 |
| 2 | `MeteoRisk - DWD ICON EU + D2 (completo)` | `forecast-maps-dwd.yml` | `50 0,3,6,9,12,15,18,21 * * *` | 00:50, 03:50, 06:50, 09:50, 12:50, 15:50, 18:50, 21:50 |
| 3 | `MeteoRisk - DWD ICON Global (parziale)` | `forecast-maps-dwdicon.yml` | `1 3,9,15,21 * * *` | 03:01, 09:01, 15:01, 21:01 |
| 4 | `MeteoRisk - DWD ICON Global (completo)` | `forecast-maps-dwdicon.yml` | `41 3,9,15,21 * * *` | 03:41, 09:41, 15:41, 21:41 |
| 5 | `MeteoRisk - ARPEGE 0.25 (parziale)` | `forecast-maps-meteofrance.yml` | `20 4,10,16,22 * * *` | 04:20, 10:20, 16:20, 22:20 |
| 6 | `MeteoRisk - ARPEGE 0.25 (completo)` | `forecast-maps-meteofrance.yml` | `29 5,11,17,23 * * *` | 05:29, 11:29, 17:29, 23:29 |
| 7 | `MeteoRisk - NCEP GFS (parziale)` | `forecast-maps-ncep.yml` | `55 3,9,15,21 * * *` | 03:55, 09:55, 15:55, 21:55 |
| 8 | `MeteoRisk - NCEP GFS (intermedio)` | `forecast-maps-ncep.yml` | `50 4,10,16,22 * * *` | 04:50, 10:50, 16:50, 22:50 |
| 9 | `MeteoRisk - NCEP GFS (completo)` | `forecast-maps-ncep.yml` | `52 5,11,17,23 * * *` | 05:52, 11:52, 17:52, 23:52 |
| 10 | `MeteoRisk - ECMWF IFS 0.25 (parziale)` | `forecast-maps-ecmwf.yml` | `48 1,7,13,19 * * *` | 01:48, 07:48, 13:48, 19:48 |
| 11 | `MeteoRisk - ECMWF IFS 0.25 (completo)` | `forecast-maps-ecmwf.yml` | `52 3,9,15,21 * * *` | 03:52, 09:52, 15:52, 21:52 |
| 12 | `MeteoRisk - WebP Index Backup` | `forecast-maps-index.yml` | `50 3,11,17,23 * * *` | 03:50, 11:50, 17:50, 23:50 |

> ⚠️ **I 5 titoli della tabella a 1 slot (`MeteoRisk - DWD ICON EU + D2`, `- DWD ICON Global`, `- ARPEGE 0.25`, `- NCEP GFS`, `- ECMWF IFS 0.25`) vanno DISABILITATI**, altrimenti continuano a dispatchare agli orari vecchi. `setup_cronjobs_api.js` lo fa da solo (lista `retired_job_titles` nel config) e stampa l'esito; se la PATCH viene rifiutata, disabilitarli a mano dalla dashboard.

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
