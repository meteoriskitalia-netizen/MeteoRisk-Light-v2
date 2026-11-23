# `validation-labels` — dataset etichette METAR/SPECI Italia 2015-2026

Branch **dati**, orfano (commit senza parent), dedicato **solo** al dataset di
etichette della validazione del layer severo. Non pesa su `main`: `main` riceve
gli **script** che lo ricostruiscono (`scripts/validation/build_labels.py`), non
i 25 MB di dati derivati.

| | |
| :--- | :--- |
| File dataset | `data/validation/labels.jsonl` |
| Righe | **42.391** |
| Byte | **24.999.406** |
| sha256 | `5F8023F65E7D53937BB1487848FBD416E2D1170FCF6379D80497F8FBB5DED2C2` |
| Periodo | 2015-01-01T08:50Z -> 2026-10-23T16:55Z |
| Stazioni distinte | **99** |
| Bollettini letti (cache) | **7.272.744** |
| Rigenerato | **2026-11-23** (ricostruzione da zero, non recupero di un file) |
| Metadati | `data/validation/labels_meta.json` |
| Report di costruzione | `data/validation/labels_report.json` |

## Che cos'e'

Una riga per **evento osservato** in un bollettino METAR/SPECI di una stazione
italiana, classificato per tipo di tempo presente (WMO code table 4678) e, per
il vento, per raffica. Il bollettino originale e' in ogni riga (`raw`): ogni
etichetta e' ricontrollabile.

Distribuzione per tipo (misurata sul file, non copiata):

| `type` | righe | in verita' (qc A/B) | escluse da QC |
| :--- | ---: | ---: | ---: |
| `thunderstorm` | 39.494 | 39.494 | 0 |
| `severe_wind` | 2.329 | 2.329 | 0 |
| `hail` | 488 | 309 | **179** (qc `C`) |
| `funnel_cloud` | 76 | 76 | 0 |
| `tornado` | 4 | 4 | 0 |
| **totale** | **42.391** | **42.212** | **179** |

Le 179 righe `hail` con `qc=C` sono grandine **senza** temporale (`TS`) entro 60
minuti alla stessa stazione: in quel caso `GR`/`GS` puo' essere neve tonda o
graupel. Restano nel JSONL, **non** entrano nella verita' di default
(`--min-qc B`).

## Fonte, licenza, attribuzione

- **Dato**: bollettini METAR/SPECI, testo WMO originale. Archivio **NOAA/NCEI
  Integrated Surface Database ("Global Hourly")**.
- **Licenza del dato**: opera del Governo degli Stati Uniti, **dominio pubblico**
  (17 U.S.C. §105), nessuna restrizione d'uso, nessun obbligo di registrazione.
  Attribuzione consigliata: *"METAR/SPECI, NOAA/NCEI Integrated Surface Database"*.
- **Trasporto**: endpoint pubblico
  `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py`, rete `IT__ASOS`,
  campo `metar`, `report_type=3` (METAR + SPECI). **IEM (Iowa State University)**
  e' un mirror: non concede licenza sul dato e non ne e' il titolare, per questo
  la provenienza dichiarata e' NOAA/NCEI.
- ⚠️ **NON VERIFICATO** (dichiarato, non nascosto): la pagina **ISD/NODD** non
  riporta un testo di licenza esplicito, e la **policy WMO** vigente sul riuso
  dei messaggi di scambio **non e' stata verificata**. Il METAR italiano e'
  prodotto da stazioni italiane e scambiato in ambito WMO: e' il punto piu'
  debole della catena di licenza e va chiuso prima di un uso che non sia di
  ricerca interna.
- **ESWD**: **non** usato e **non** presente in questo dataset (nessun record
  scaricato). Il divieto di pubblicare dati grezzi (clausola 1.iv) e' il motivo.

## Come si legge

Una riga JSON per evento (schema `1.0`). Campi richiesti:
`event_id, ts_utc, lat, lon, type, severity, source, qc, distance_km,
matched_cell_id, matched_ts`. Campi di servizio: `match_status,
convective_context, station, vicinity, detail, severity_value,
ts_uncertainty_min, sampling_gap_min, merged_count, raw`.

```python
import json
with open("data/validation/labels.jsonl", encoding="utf-8") as fh:
    for line in fh:
        row = json.loads(line)      # 42.391 righe
        if row["type"] == "hail" and row["qc"] in ("A", "B"):
            ...                     # 309 grandini in verita'
```

⚠️ In **queste** righe `match_status` e' **sempre** `no_snapshot_in_window`
(non e' stato passato `--cells-dir`): `distance_km`, `matched_cell_id`,
`matched_ts` sono `null`. Il dataset e' stato rigenerato **senza** snapshot
radar; l'accoppiamento con le celle e' un passo separato
(`asof_bench.py --labels ...`, `asof_bench_footprint.py --labels ...`).

## Come si rigenera

```powershell
$py = "C:\Users\aless\AppData\Local\Python\bin\python.exe"

# 1) cache METAR (~124 MB gz, 23 file: 11 annuali + 12 mensili del 2022;
#    il 2022 annuale NON e' servibile: la richiesta viene resettata)
#    endpoint: https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py
#    rete IT__ASOS, campo "metar", report_type=3

# 2) dataset + verita' + report
& $py scripts\validation\build_labels.py --start 2015-01-01 --end 2026-10-24 `
    --cache-dir <cache> --out labels.jsonl --truth-json truth.json --report report.json
```

Misure della rigenerazione del **2026-11-23** su questo host: cache 23 file /
123.523.965 B gz, ~596 s di download effettivo (piu' riavvii); costruzione del
dataset **117 s**; `--selftest` 0 controlli falliti.

## Cosa NON e' dimostrato

- La verita' e' una rete di **99 punti** (aeroporti), non un piano: le
  grandinate lontane da un aeroporto **non esistono** nel dataset. La
  numerosita' e' un **minimo osservato**.
- Il tempo presente e' osservato all'ora del bollettino (`ts_uncertainty_min=5`)
  ma fra due bollettini di routine passano ~60 min (`sampling_gap_min`): un
  fenomeno breve fra due report non e' nel dataset. Questo abbassa il POD, non
  sposta i timestamp.
- La serie `severe_wind` **non e' omogenea**: la frazione di bollettini con
  gruppo di raffica va dal 2,92% (2017) all'1,18% (2025). Un calo di eventi puo'
  essere un calo di **segnalazione**, non di fenomeni.
- Nessun caso nullo campionato -> **HSS non calcolabile**.
- La forma byte-identica (24.999.406 B) e' una **coincidenza verificata** con il
  dataset misurato il 2026-10-25, non una garanzia: qualunque modifica di
  `build_labels.py` (versione, soglie, merge) cambia i byte e va ri-misurata.
