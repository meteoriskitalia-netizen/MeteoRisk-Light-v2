# Guida alla Configurazione dei Cronjob Esterni (cron-job.org / Webhook)

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

Tutti i tempi sono calcolati sui ritardi reali misurati per l'aggiornamento dei frame su Open-Meteo rispetto all'orario nominale della run:

| Modello Meteo | Workflow GitHub | Run Nominali (UTC) | Disponibilità Open-Meteo | Orario Trigger (UTC) | Espressione Cron |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **DWD ICON-EU & ICON-D2** | `forecast-maps-dwd.yml` | 00, 03, 06, 09, 12, 15, 18, 21 | +1h23 (D2) / +2h53 (EU) | **+3h30** dal nominale<br>`00:30, 03:30, 06:30, 09:30, 12:30, 15:30, 18:30, 21:30` | `30 0,3,6,9,12,15,18,21 * * *` |
| **DWD ICON Global** | `forecast-maps-dwdicon.yml` | 00, 06, 12, 18 | ~3h29 | **+4h30** dal nominale<br>`04:30, 10:30, 16:30, 22:30` | `30 4,10,16,22 * * *` |
| **Météo-France ARPEGE 0.25** | `forecast-maps-meteofrance.yml` | 00, 06, 12, 18 | ~4h08 | **+4h15** dal nominale<br>`04:15, 10:15, 16:15, 22:15` | `15 4,10,16,22 * * *` |
| **NCEP GFS (0.25 + 0.117)** | `forecast-maps-ncep.yml` | 00, 06, 12, 18 | ~6h05 - 6h13 | **+6h15** dal nominale<br>`00:15, 06:15, 12:15, 18:15` | `15 0,6,12,18 * * *` |
| **ECMWF IFS 0.25** | `forecast-maps-ecmwf.yml` | 00, 12 | ~7h42 (il più tardivo) | **+7h45** dal nominale<br>`07:45, 19:45` | `45 7,19 * * *` |
| **WebP Index Aggregator** | `forecast-maps-index.yml` | Post-merge (automatico) | Fine ciclo | **Backup ogni 6 ore**<br>`02:50, 08:50, 14:50, 20:50` | `50 2,8,14,20 * * *` |

> ℹ️ **Nota Fuso Orario**: Impostare il fuso orario di ciascun job su cron-job.org su **UTC** (Coordinated Universal Time). In ora solare italiana (CET) corrisponde a UTC+1; in ora legale (CEST) corrisponde a UTC+2. Mantenere UTC garantisce che le run meteo rimangano sempre allineate indipendentemente dal cambio dell'ora.

---

## 4. Configurazione Passo-Passo su cron-job.org

Per ciascuno dei 6 modelli sopra elencati, crea un nuovo cronjob su cron-job.org:

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
- **Expected Status Code**: `204` (No Content è la risposta standard di successo di GitHub Actions)

---

### Schede di Configurazione per Modello:

#### 1. DWD ICON-EU + ICON-D2
- **Title**: `MeteoRisk - DWD ICON EU + D2`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-dwd.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `30 0,3,6,9,12,15,18,21 * * *` (Timezone: **UTC**)

#### 2. DWD ICON Global
- **Title**: `MeteoRisk - DWD ICON Global`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-dwdicon.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `30 4,10,16,22 * * *` (Timezone: **UTC**)

#### 3. Météo-France ARPEGE 0.25
- **Title**: `MeteoRisk - ARPEGE 0.25`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-meteofrance.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `15 4,10,16,22 * * *` (Timezone: **UTC**)

#### 4. NCEP GFS (0.25 + 0.117)
- **Title**: `MeteoRisk - NCEP GFS`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-ncep.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `15 0,6,12,18 * * *` (Timezone: **UTC**)

#### 5. ECMWF IFS 0.25
- **Title**: `MeteoRisk - ECMWF IFS 0.25`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-ecmwf.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `45 7,19 * * *` (Timezone: **UTC**)

#### 6. WebP Index Aggregator (Backup)
- **Title**: `MeteoRisk - WebP Index Backup`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-index.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `50 2,8,14,20 * * *` (Timezone: **UTC**)

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
