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

Tutti gli orari sono tarati sulla **disponibilità reale misurata su S3** (campione 2026-10-21, `LastModified` massimo degli oggetti `data_spatial/<dominio>/<YYYY>/<MM>/<DD>/<HH>00Z/`), non sui ritardi dichiarati da Open-Meteo né sui tempi nominali dei modelli.

| Modello Meteo | Workflow GitHub | Run Nominali (UTC) | Disponibilità MISURATA su S3 | Orario Trigger (UTC) | Espressione Cron |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **DWD ICON-EU & ICON-D2** | `forecast-maps-dwd.yml` | 00, 03, 06, 09, 12, 15, 18, 21 | run lunghi (00/06/12/18Z): mediana **3,71h**, max **4,38h**<br>run corti (03/09/15/21Z): mediana **3,20h**, max **3,20h** | **8 slot/giorno**<br>`00:30, 04:30, 06:30, 10:30, 12:30, 16:30, 18:30, 22:30` | `30 0,4,6,10,12,16,18,22 * * *` |
| **DWD ICON Global** | `forecast-maps-dwdicon.yml` | 00, 06, 12, 18 | mediana **3,72h**, max **4,57h** (00Z) | **4 slot/giorno**<br>`04:45, 10:45, 16:45, 22:45` | `45 4,10,16,22 * * *` |
| **Météo-France ARPEGE 0.25** | `forecast-maps-meteofrance.yml` | 00, 06, 12, 18 | mediana **4,99h**, max **6,20h** (12Z) | **4 slot/giorno**<br>`00:45, 06:45, 12:45, 18:45` | `45 0,6,12,18 * * *` |
| **NCEP GFS (0.25 + 0.117)** | `forecast-maps-ncep.yml` | 00, 06, 12, 18 | mediana **6,20h**, max **6,59h** (06Z) | **4 slot/giorno**<br>`01:00, 07:00, 13:00, 19:00` | `0 1,7,13,19 * * *` |
| **ECMWF IFS 0.25** | `forecast-maps-ecmwf.yml` | 00, 06, 12, 18 (4 run/giorno) | mediana **8,24h**, max **9,11h** (12Z) | **4 slot/giorno**<br>`02:50, 09:50, 14:50, 21:50` | `50 2,9,14,21 * * *` |
| **WebP Index Aggregator** | `forecast-maps-index.yml` | Post-merge (automatico) | n/a (job di backup: non dipende da un singolo dominio) | **Backup ogni 6 ore**<br>`03:50, 11:50, 17:50, 23:50` | `50 3,11,17,23 * * *` |

### 3.1 Perché quegli slot (mappatura slot → run coperto)

Ogni slot cade **dopo** la disponibilità del run che deve servire; i run "lunghi" e "corti" di DWD hanno tempi diversi, quindi la fascia DWD ha 8 slot e non 4.

| Job | Slot → run coperto | Margine misurato minimo |
| :--- | :--- | :--- |
| **DWD ICON-EU/D2** | `00:30`←21Z (g. prec.) · `04:30`←00Z · `06:30`←03Z · `10:30`←06Z · `12:30`←09Z · `16:30`←12Z · `18:30`←15Z · `22:30`←18Z | **7 min** (`04:30` vs run 00Z pronto alle 04:22:58) |
| **DWD ICON Global** | `04:45`←00Z · `10:45`←06Z · `16:45`←12Z · `22:45`←18Z | **11 min** (`04:45` vs 04:34:02) |
| **ARPEGE 0.25** | `00:45`←18Z (g. prec.) · `06:45`←00Z · `12:45`←06Z · `18:45`←12Z | **34 min** (`18:45` vs 18:11:43) |
| **NCEP GFS** | `01:00`←18Z (g. prec.) · `07:00`←00Z · `13:00`←06Z · `19:00`←12Z | **25 min** (`13:00` vs 12:35:13) |
| **ECMWF IFS 0.25** | `02:50`←18Z (g. prec.) · `09:50`←00Z · `14:50`←06Z · `21:50`←12Z | **44 min** (`21:50` vs 21:06:47) |
| **WebP Index Backup** | sfasato di ~50 min dai dispatch provider, nessuna sovrapposizione | n/a |

> ⚠️ **Margine più stretto dell'intera matrice: 7 minuti** sullo slot DWD `04:30` (run 00Z). Se DWD rallenta oltre ~4h30 sul run 00Z, quello slot dispatcha il run precedente: è accettato perché il run precedente è comunque servito 2 ore prima e il workflow successivo riallinea. Il margine è documentato, non nascosto.

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
- **Cron Expression**: `30 0,4,6,10,12,16,18,22 * * *` (Timezone: **UTC**)

#### 2. DWD ICON Global
- **Title**: `MeteoRisk - DWD ICON Global`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-dwdicon.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `45 4,10,16,22 * * *` (Timezone: **UTC**)

#### 3. Météo-France ARPEGE 0.25
- **Title**: `MeteoRisk - ARPEGE 0.25`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-meteofrance.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `45 0,6,12,18 * * *` (Timezone: **UTC**)

#### 4. NCEP GFS (0.25 + 0.117)
- **Title**: `MeteoRisk - NCEP GFS`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-ncep.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `0 1,7,13,19 * * *` (Timezone: **UTC**)

#### 5. ECMWF IFS 0.25
- **Title**: `MeteoRisk - ECMWF IFS 0.25`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-ecmwf.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `50 2,9,14,21 * * *` (Timezone: **UTC**)

#### 6. WebP Index Aggregator (Backup)
- **Title**: `MeteoRisk - WebP Index Backup`
- **URL**: `https://api.github.com/repos/meteoriskitalia-netizen/MeteoRisk-Light-v2/actions/workflows/forecast-maps-index.yml/dispatches`
- **Schedule**: User-defined (cron expression)
- **Cron Expression**: `50 3,11,17,23 * * *` (Timezone: **UTC**)

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
