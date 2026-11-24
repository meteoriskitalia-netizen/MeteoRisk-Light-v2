# Verifica automatica del nowcast del campo di pioggia

Stato: **2026-11-24** — raccolta e verifica pronte per l'evento della notte
2026-11-24/25. La raccolta parte al primo run `radar-engine` successivo al
push del workflow (branch `nowcast-archive` assente -> creato dal run).

## Cosa fa, in tre pezzi

1. **Archivio leggero dei nowcast emessi** —
   `scripts/radar_engine/nowcast_archive.py`.
   Ogni run copia `data/radar/nowcast.json` in
   `nowcast_archive/YYYY/MM/DD/HHMM.json` (ora UTC dello slot) dentro
   `$RUNNER_TEMP`. Il branch ORPHAN `nowcast-archive` pubblica la directory con
   commit senza parent e `--force-with-lease`, come `radar-history`,
   `predictions-archive` e `srt1-province-archive`. **Mai su `main`, mai
   nell'artefatto Pages.**

2. **Verifica a posteriori** — `scripts/radar_engine/nowcast_verify.py`.
   Quando lo slot osservato di un `valid_time` archiviato e' disponibile,
   scarica l'SRI osservato, **ricalcola** il nowcast dallo stesso slot con lo
   stesso codice (`nowcast_field.compute`) e calcola CSI/POD/FAR di nowcast e
   persistenza alle soglie gia' dichiarate **0.5 / 2.0 / 5.0 mm/h**. La serie
   viene accumulata in `data/radar/nowcast_verification.json`.

3. **Report per evento** — `scripts/validation/nowcast_event_report.py`.
   Riassunto a mano su un intervallo di date, con CSI per lead, confronto con
   la persistenza e casi migliori/peggiori con coordinate.

## Archivio: schema e costi

Schema: `meteorisk.nowcast_archive/1`. Un record per slot contiene:

- `slot`, `slot_basis` (`source_slot` oppure `generated_at` se il nowcast non
  e' disponibile), `source_slot`, `generated_at`, `available`, `null_reason`;
- `motion`, `motion_algorithm`, `motion_pairs`, `motion_stats`,
  `speed_cap_kmh`, `speed_cap_mode`, `vectors_zeroed_by_cap`;
- `frames_used`, `frames_origin`, `frames_slots`;
- `leads[]` con `lead_min`, `valid_time`, `image`, `stats`;
- `grid` (crs/bounds/width/height/risoluzione) e `images` con **nome, byte e
  sha256** dei tre WebP. I WebP **non** vengono copiati.

Retention: **7 giorni** (`RETENTION_DAYS = 7`), potatura a ogni pubblicazione.

Costo MISURATO il 2026-11-24 su 6 slot reali (06:00–11:00Z):

| voce | valore |
| :--- | ---: |
| record | 6 |
| byte totali | 13.032 |
| byte/record | 2.172 (2.166–2.179) |
| record/giorno | 144 (un run ogni 10 min) – 288 (slots DPC ogni 5 min) |
| byte/giorno | ~0,31 – 0,62 MB |
| byte vivi a 7 giorni | ~2,2 – 4,4 MB |

I record `available: false` sono piu' piccoli (nessun lead) e vengono scritti
lo stesso: servono a misurare la disponibilita' del layer, non solo i casi con
pioggia.

## Verifica: semantica (nessun valore inventato)

Ogni riga della serie e' per `(source_slot, lead_min, threshold_mmh)`:

```
source_slot, lead_min, valid_time, threshold_mmh, status,
n_px_observed, domain_pixels,
n_px_observed_ge_threshold, n_px_predicted_ge_threshold,
csi_nowcast, pod, far, hits, misses, false_alarms, correct_negatives,
csi_persistenza, pod_persistenza, far_persistenza,
hits_persistenza, misses_persistenza, false_alarms_persistenza,
correct_negatives_persistenza,
beats_persistence, peak_observed {mmh, lat, lon},
error_location {false_alarm_centroid, miss_centroid}
```

Stati:

| stato | significato |
| :--- | :--- |
| `verified` | osservato presente e punteggi calcolati. I punteggi possono essere `null` se il denominatore e' zero (es. nessun pixel sopra soglia): non sono zeri. |
| `not_informative` | osservato presente ma < 100 pixel >= 0.5 mm/h: i punteggi sarebbero tutti "correct negative". |
| `not_verifiable` | osservato assente/non pubblicato dopo la grazia, oppure ricalcolo non riuscito/non coerente. Porta **sempre** `null_reason` (`observed_missing`, `observed_slot_mismatch`, `sri_frames_lt_3`, `recomputed_source_slot_mismatch`, ...). |

Regole dichiarate:

- soglie: **0.5 / 2.0 / 5.0 mm/h** (le stesse di `nowcast_field.VERIFY_THRESHOLDS_MMH`);
- lead: **15 / 30 / 60 min**;
- persistenza = campo SRI dello slot sorgente, fermo;
- dominio = maschera valida del nowcast & maschera valida dell'osservato;
- `beats_persistence` per riga = `csi_nowcast > csi_persistenza` (null se uno
  dei due non e' definito);
- grazia osservato: **90 min** dopo `valid_time`. Prima della grazia la coppia
  resta `pending` e viene ritentata; dopo, l'assenza produce una riga
  `not_verifiable` con motivo. Il ritardo DPC misurato e' ~7 min: 90 e' un
  margine dichiarato, non una misura;
- nessuna soglia nuova e nessun punteggio pubblicato con osservato mancante.

Efficienza: la verifica ricalcola il nowcast dallo slot archiviato (la copia
leggera non contiene il campo float). Una coppia (slot, lead) costa 6 SRI per
la finestra + 1 osservato. Nel run operativo i tre lead di uno stesso slot
cadono in run diversi: ~21 download/slot complessivi. Limite per run:
`--max-slots 12` (i residui restano per il run successivo). Un guasto di rete
o un record corrotto e' un `[WARN]`; il workflow ha `continue-on-error` e il
run radar prosegue.

## Schema 2 e compatibilita' con lo schema 1

`data/radar/nowcast_verification.json` conserva **intatto** il riassunto
schema 1 (i 49 casi, `decision_rule`, `leads`, `beats_persistence`, ...). Il
modulo aggiunge in coda:

```json
{
  "schema": "meteorisk.nowcast_verification/2",
  "schema_version": 2,
  "compatibility": {
    "previous_schema": "meteorisk.nowcast_verification/1",
    "summary_keys_preserved": true
  },
  "series": {
    "schema": "meteorisk.nowcast_verification_series/1",
    "retention_days": 7,
    "thresholds_mmh": [0.5, 2.0, 5.0],
    "leads_min": [15, 30, 60],
    "counts": {...},
    "rows": [...]
  }
}
```

Un lettore dello schema 1 continua a leggere le stesse chiavi con gli stessi
valori (verificato nel test `test_schema2_keeps_schema1_summary`). Le chiavi
nuove sono additive: un lettore che ignora `series` non cambia comportamento.
La serie ha retention **7 giorni** e non viene riscritta quando non ci sono
righe nuove (nessun commit ogni 10 minuti a vuoto). Costo MISURATO: 54 righe
= 59.967 byte (1.110 B/riga); a 144 slot/giorno × 3 lead × 3 soglie = 1.296
righe/giorno, ~1,44 MB/giorno e ~10 MB vivi a 7 giorni (il file resta in
`data/radar/` ed entra nell'artefatto Pages: e' il motivo della retention
corta).

## Prova end-to-end (2026-11-24, dato reale)

6 slot passati, 06:00–11:00Z, con il codice di produzione e SRI DPC reale:

- archivio: 6/6 record, tutti `available: true`;
- verifica: **6/6 slot**, **54/54 righe verificate**, 0 non verificabili,
  54 download SRI, **65,9 s** (11,0 s/slot);
- confronto con `scripts/validation/nowcast_replay.py` sullo stesso intervallo:
  **54/54 coppie identiche** per hits/misses/false alarms/correct negatives,
  sia nowcast sia persistenza.

CSI medi della prova:

| lead | righe | CSI nowcast | CSI persistenza | vinte/perse/pari |
| ---: | ---: | ---: | ---: | ---: |
| 15 min | 18 | 0,309 | 0,238 | 10/6/2 |
| 30 min | 18 | 0,146 | 0,140 | 6/9/3 |
| 60 min | 18 | 0,043 | 0,077 | 4/10/4 |

Pooled su tutte le righe: nowcast **0,237** vs persistenza **0,233**
(20 vittorie, 25 sconfitte, 9 pari). A 60 minuti il nowcast **perde** contro
la persistenza: e' un risultato, non un guasto, ed e' coerente con il limite
dichiarato del trasporto puro (oltre 30 minuti l'affidabilita' cala).

Le 54 righe sono nel file consegnato, con `series.notes` e
`series.provenance` che dichiarano la provenienza della prova.

## Come si usa domani mattina

```bash
python scripts/validation/nowcast_event_report.py \
    --from 2026-11-24T18 --to 2026-11-25T12
```

Opzioni: `--json FILE`, `--markdown FILE`, `--verification PATH`,
`--tz Europe/Rome`. Il filtro e' su `valid_time`: un nowcast emesso prima
delle 18 ma valido dopo le 18 rientra nell'evento. Senza `--tz` un istante
senza `Z`/offset e' letto come **UTC** e il report lo dichiara; con
`--tz Europe/Rome` l'esempio `2026-11-24T18` copre dalle 17:00Z (ora locale
18:00). Se la serie e' vuota il report scrive "nessun dato": non stampa zeri
travestiti da misura.

## File

| file | ruolo |
| :--- | :--- |
| `scripts/radar_engine/nowcast_archive.py` | copia leggera per slot + retention + docs del branch |
| `scripts/radar_engine/nowcast_verify.py` | verifica a posteriori + serie schema 2 |
| `scripts/validation/nowcast_event_report.py` | report per intervallo di date |
| `scripts/tests/radar_engine/test_nowcast_archive_verify.py` | 8 test (archivio, osservato mancante, replay, schema 2, report) |
| `.github/workflows/radar-engine.yml` | restore/archivio/verifica/publish del branch `nowcast-archive` |
| `data/radar/nowcast_verification.json` | riassunto schema 1 + serie schema 2 |

## Limiti dichiarati

- La verifica ricalcola il nowcast: se il codice cambia, le righe nuove possono
  non essere comparabili con le vecchie. Il campo `source_slot` ricalcolato
  deve coincidere con l'archiviato, altrimenti la riga e' `not_verifiable`.
- L'archivio conserva metadati, non il campo: non permette di rigiocare una
  previsione con un codice diverso da quello attuale.
- La persistenza e' il campo fermo, non un NWP: e' la baseline dichiarata.
- La grazia di 90 minuti e' una scelta, non una misura del ritardo DPC.
- Il branch `nowcast-archive` viene creato dal primo run dopo il push: fino a
  quel momento la raccolta non e' attiva. Non e' stato creato da questa
  sessione (nessun push eseguito).

## Insieme di file da pubblicare (verificato contro `main`)

Confronto con `main` remoto (`meteoriskitalia-netizen/MeteoRisk-Light-v2`,
2026-11-24):

| file | azione | base remota |
| :--- | :--- | :--- |
| `scripts/radar_engine/nowcast_archive.py` | ADD | assente su `main` (404) |
| `scripts/radar_engine/nowcast_verify.py` | ADD | assente su `main` |
| `scripts/validation/nowcast_event_report.py` | ADD | assente su `main` |
| `scripts/tests/radar_engine/test_nowcast_archive_verify.py` | ADD | assente su `main` |
| `docs/NOWCAST_VERIFICATION.md` | ADD | assente su `main` |
| `.github/workflows/radar-engine.yml` | UPDATE | il diff contro `main` e' **solo** l'aggiunta dei 4 step/nowcast (156 righe aggiunte, 2 sostituite: le due righe `ORDER` dei tempi) |
| `data/radar/nowcast_verification.json` | UPDATE | il riassunto schema 1 e' **identico** a `main` (49 casi); si aggiungono `schema 2` + `series` |
| `docs/NOWCAST_FIELD.md` | UPDATE | +9 righe (rimando a questo documento) |

⚠️ **NON pubblicare `scripts/radar_engine/main.py` da questa copia di lavoro**:
rispetto a `main` e' piu' vecchio di 5 righe (mancano
`cell_kept_by_overlap`, `storm_kept_by_overlap`, `cell_reids_stitched`,
`storm_reids_stitched`). La copia locale e' una base precedente e
sovrascriverebbe il fix presente su `main`. Il wiring del nowcast in
`main.py` e' gia' su `main` e non richiede modifiche per questa funzione.

`scripts/radar_engine/nowcast_field.py`, `scripts/validation/nowcast_replay.py`,
`scripts/tests/radar_engine/test_nowcast_field.py` e `docs/NOWCAST_REPLAY.md`
sono **byte-identici** a `main`: non sono un delta e non vanno ricaricati.
