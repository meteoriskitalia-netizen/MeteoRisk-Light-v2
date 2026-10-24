# Guida alla Configurazione dei Cronjob Esterni (cron-job.org / Webhook)

> ⚠️ **Fonte di verità degli orari: la disponibilità MISURATA su S3, non i tempi nominali dei modelli.**
> Ogni slot della matrice in §3 è stato tarato sul momento in cui il run è realmente completo su Open-Meteo, cioè sul `LastModified` massimo degli oggetti
> `data_spatial/<dominio>/<YYYY>/<MM>/<DD>/<HH>00Z/` (listing S3 `list-type=2`), **non** sull'orario nominale del modello e **non** sui ritardi dichiarati da Open-Meteo.
> I valori misurati di riferimento (campione 2026-10-21) sono riportati job per job anche in `scripts/cronjobs/cronjobs_config.json` nel campo `measured_utc_availability`.
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

Tutti gli orari sono tarati sui **tempi MISURATI il 2026-10-24** (ore dopo il run nominale): **primo step** = prima pubblicazione utile del run, **run completo** = ultimo frame utile. Non sono i ritardi dichiarati da Open-Meteo ne' i tempi nominali dei modelli. La misura precedente (2026-10-21, solo run completo) resta registrata in `measured_utc_availability.previous_measurement_2026_10_21` di ogni job.

**Regola P2 — 2 slot per run.** Il primo slot parte subito dopo il **primo step** e pubblica un **run parziale** (`--allow-partial-run`, ~24-30 frame su ~209): la mappa e' visibile ore prima. Il secondo slot parte dopo la **disponibilita' completa** e chiude la coda. Costo dichiarato: **un render in piu' per run**, modesto perche' ai primi step i frame sono ~24-30.
Margine: **+10..15 min** sul tempo misurato, e **nessuno slot oltre +30 min** (vincolo esplicito: un margine piu' largo annullerebbe il guadagno del primo slot). Su cron-job.org un job copre un solo orario di minuto, quindi **2 slot per run = 2 job per provider**. Due slot a meno di 20 minuti si accorpano: qui e' successo solo per DWD (vedi 3.2).

| Modello Meteo | Workflow GitHub | Run Nominali (UTC) | Primo step (misurato) | Run completo (misurato) | Slot parziale (UTC) | Slot completo (UTC) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DWD ICON-EU & ICON-D2** | `forecast-maps-dwd.yml` | 00, 03, 06, 09, 12, 15, 18, 21 | ICON-D2 **+0h46**<br>ICON-EU **+2h47** | ICON-D2 **+1h23**<br>ICON-EU **+3h57** | **8 slot**<br>`01:10, 04:10, 07:10, 10:10, 13:10, 16:10, 19:10, 22:10` | **8 slot**<br>`00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00` |
| **DWD ICON Global** | `forecast-maps-dwdicon.yml` | 00, 06, 12, 18 | **+2h46** | **+3h28** | **4 slot**<br>`03:01, 09:01, 15:01, 21:01` | **4 slot**<br>`03:41, 09:41, 15:41, 21:41` |
| **Meteo-France ARPEGE 0.25** | `forecast-maps-meteofrance.yml` | 00, 06, 12, 18 | **+3h17 .. +4h08** | **+5h16** | **4 slot**<br>`04:20, 10:20, 16:20, 22:20` | **4 slot**<br>`05:29, 11:29, 17:29, 23:29` |
| **NCEP GFS (0.25 + 0.117)** | `forecast-maps-ncep.yml` | 00, 06, 12, 18 | **+3h40** (gfs025) | **+5h39** (gfs025) | **4 slot**<br>`03:55, 09:55, 15:55, 21:55` | **4 slot**<br>`05:52, 11:52, 17:52, 23:52` |
| **ECMWF IFS 0.25** | `forecast-maps-ecmwf.yml` | 00, 06, 12, 18 | **+7h01 .. +7h36** | **+9h40** | **4 slot**<br>`01:48, 07:48, 13:48, 19:48` | **4 slot**<br>`03:52, 09:52, 15:52, 21:52` |
| **WebP Index Aggregator** | `forecast-maps-index.yml` | Post-merge (automatico) | n/a | n/a | **Backup ogni 6 ore**<br>`03:50, 11:50, 17:50, 23:50` | - |

### 3.1 Slot per run e margine effettivo

Margine = slot meno il tempo misurato che quello slot deve servire.

| Provider | Run nominale | Slot 1 (parziale) | Margine | Slot 2 (completo) | Margine |
| :--- | :--- | :--- | :--- | :--- | :--- |
| DWD | 00Z | `01:10` | +24' su D2 +0h46 / chiude EU 21Z del giorno prima a +13' su +3h57 | `03:00` | +13' su EU +2h47 |
| DWD | 03Z | `04:10` | +24' / chiude EU 00Z a +13' su +3h57 | `06:00` | +13' |
| DWD | 06Z | `07:10` | +24' / chiude EU 03Z a +13' | `09:00` | +13' |
| DWD | 09Z | `10:10` | +24' / chiude EU 06Z a +13' | `12:00` | +13' |
| DWD | 12Z | `13:10` | +24' / chiude EU 09Z a +13' | `15:00` | +13' |
| DWD | 15Z | `16:10` | +24' / chiude EU 12Z a +13' | `18:00` | +13' |
| DWD | 18Z | `19:10` | +24' / chiude EU 15Z a +13' | `21:00` | +13' |
| DWD | 21Z | `22:10` | +24' / chiude EU 18Z a +13' | `00:00` | +13' su EU 21Z (il completo EU arriva a `01:10` del run 00Z) |
| DWD ICON Global | 00Z / 06Z / 12Z / 18Z | `03:01` / `09:01` / `15:01` / `21:01` | +15' | `03:41` / `09:41` / `15:41` / `21:41` | +13' |
| ARPEGE 0.25 | 00Z / 06Z / 12Z / 18Z | `04:20` / `10:20` / `16:20` / `22:20` | +12' (sul massimo +4h08; +63' sul minimo +3h17) | `05:29` / `11:29` / `17:29` / `23:29` | +13' |
| NCEP GFS | 00Z / 06Z / 12Z / 18Z | `03:55` / `09:55` / `15:55` / `21:55` | +15' | `05:52` / `11:52` / `17:52` / `23:52` | +13' |
| ECMWF IFS 0.25 | 00Z / 06Z / 12Z / 18Z | `07:48` / `13:48` / `19:48` / `01:48` | +12' (sul massimo +7h36; +47' sul minimo +7h01) | `09:52` / `15:52` / `21:52` / `03:52` | +12' |

> ⚠️ **DWD: un trigger lancia entrambi i domini.** ICON-EU e ICON-D2 stanno nella stessa matrice, quindi ogni slot agisce su tutti e due. Conseguenza dichiarata: allo slot `01:10` ICON-EU non ha ancora pubblicato nulla (primo step +2h47) e il suo NO-OP guard lo chiude in pochi minuti; allo slot `03:00` ICON-D2 e' completo da 1h37 (+97' dal run completo +1h23), che e' un ruolo **secondario dichiarato** e non un obbligo dei +30'. Il completo di ICON-EU arriva allo slot `01:10` del run successivo, +13' su +3h57.

### 3.2 Accorpamenti dichiarati

- **DWD**: i ruoli per-dominio sarebbero 4 (D2 parziale `00:59`, D2 completo `01:36`, EU parziale `03:00`, EU completo `04:10`). D2 completo (`01:36`) e D2 parziale del run dopo (`03:59`) stanno a 11' l'uno dall'altro e il completo EU (`04:10`) coincide con lo slot `01:10` del run successivo: 4 slot per run darebbero 32 trigger/giorno con due coppie sotto i 20'. Sono stati ridotti a **2 trigger per run** (`01:10` e `03:00`), ognuno con due ruoli. Costo dichiarato: i 209 frame completi di ICON-D2 arrivano alle `03:00` invece che alle `01:36` (+1h24), mentre ICON-D2 parziale e' gia' visibile dalle `01:10`.
- **Nessun altro accorpamento**: gli altri provider hanno i due slot a 40'-124' di distanza.

### 3.3 Rete di sicurezza nativa (cron GitHub)

Il blocco `schedule:` dei 5 workflow **non** ripete gli slot di cron-job.org (sarebbero due run per lo stesso run nominale): contiene **una sola riga** per provider, spostata di **+57'** rispetto allo slot che chiude la coda del run.

| Provider | Slot che chiude la coda | Cron nativo (UTC) | Distanza minima dagli slot cron-job.org |
| :--- | :--- | :--- | :--- |
| DWD ICON-EU/D2 | `04:10` (EU completo) | `7 2,5,8,11,14,17,20,23 * * *` | 53' |
| DWD ICON Global | `03:41` | `38 4,10,16,22 * * *` | 57' |
| ARPEGE 0.25 | `05:29` | `26 0,6,12,18 * * *` | 57' |
| NCEP GFS | `05:52` | `49 0,6,12,18 * * *` | 57' |
| ECMWF IFS 0.25 | `09:52` | `49 4,10,16,22 * * *` | 57' |

> ℹ️ **Nota Fuso Orario**: Impostare il fuso orario di ciascun job su cron-job.org su **UTC** (Coordinated Universal Time). In ora solare italiana (CET) corrisponde a UTC+1; in ora legale (CEST) corrisponde a UTC+2. Mantenere UTC garantisce che le run meteo rimangano sempre allineate indipendentemente dal cambio dell'ora.

---

## 4. Configurazione Passo-Passo su cron-job.org

Servono **11 job**: 2 per ciascuno dei 5 provider + il backup dell'indice. I valori sotto sono la copia esatta di `scripts/cronjobs/cronjobs_config.json`; `scripts/cronjobs/setup_cronjobs_api.js` li applica leggendo quel file (non ci sono piu' costanti duplicate: era il difetto per cui rieseguire lo script riportava i job agli orari vecchi).

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

### Schede di Configurazione (11 job)

URL: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/<workflow_file>/dispatches`

| # | Title (cron-job.org) | Workflow | Cron (UTC) | Orari |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `MeteoRisk - DWD ICON EU + D2 (parziale)` | `forecast-maps-dwd.yml` | `10 1,4,7,10,13,16,19,22 * * *` | 01:10, 04:10, 07:10, 10:10, 13:10, 16:10, 19:10, 22:10 |
| 2 | `MeteoRisk - DWD ICON EU + D2 (completo)` | `forecast-maps-dwd.yml` | `0 0,3,6,9,12,15,18,21 * * *` | 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00 |
| 3 | `MeteoRisk - DWD ICON Global (parziale)` | `forecast-maps-dwdicon.yml` | `1 3,9,15,21 * * *` | 03:01, 09:01, 15:01, 21:01 |
| 4 | `MeteoRisk - DWD ICON Global (completo)` | `forecast-maps-dwdicon.yml` | `41 3,9,15,21 * * *` | 03:41, 09:41, 15:41, 21:41 |
| 5 | `MeteoRisk - ARPEGE 0.25 (parziale)` | `forecast-maps-meteofrance.yml` | `20 4,10,16,22 * * *` | 04:20, 10:20, 16:20, 22:20 |
| 6 | `MeteoRisk - ARPEGE 0.25 (completo)` | `forecast-maps-meteofrance.yml` | `29 5,11,17,23 * * *` | 05:29, 11:29, 17:29, 23:29 |
| 7 | `MeteoRisk - NCEP GFS (parziale)` | `forecast-maps-ncep.yml` | `55 3,9,15,21 * * *` | 03:55, 09:55, 15:55, 21:55 |
| 8 | `MeteoRisk - NCEP GFS (completo)` | `forecast-maps-ncep.yml` | `52 5,11,17,23 * * *` | 05:52, 11:52, 17:52, 23:52 |
| 9 | `MeteoRisk - ECMWF IFS 0.25 (parziale)` | `forecast-maps-ecmwf.yml` | `48 1,7,13,19 * * *` | 01:48, 07:48, 13:48, 19:48 |
| 10 | `MeteoRisk - ECMWF IFS 0.25 (completo)` | `forecast-maps-ecmwf.yml` | `52 3,9,15,21 * * *` | 03:52, 09:52, 15:52, 21:52 |
| 11 | `MeteoRisk - WebP Index Backup` | `forecast-maps-index.yml` | `50 3,11,17,23 * * *` | 03:50, 11:50, 17:50, 23:50 |

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
