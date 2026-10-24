# Replay deterministico del radar engine (`--as-of`) — release 1.2.4.20

Questo documento dichiara **cosa è riproducibile** con `--as-of`, **cosa non lo è e
perché**, con i `null_reason` esatti che finiscono nel JSON di output. Ogni numero qui
dentro è stato misurato il **2026-10-24**; le affermazioni non verificate sono marcate
come tali.

---

## 1. Il difetto che questa modalità ripara

Prima della 1.2.4.20 il motore non accettava un istante: l'ancora era **sempre**
l'ultimo prodotto pubblicato dall'API. Cinque punti decidevano *quali dati scaricare*
leggendo l'orologio di sistema, quindi un re-run di un evento del 2026-07-01T13:20Z
partiva dallo slot di **oggi** e prendeva l'ambiente di **oggi**.

La prova eseguita (non solo la lettura del codice) è in
`retro/REPORT.md` §5.3: la funzione del motore costruiva
`.../v1/forecast?...&forecast_days=1`, rispondeva con 24 ore del 2026-10-24 e
`pick_current_index` sceglieva `2026-10-24T07:00`. Per quell'evento il motore avrebbe
usato un ambiente di **105 giorni dopo**.

| # | dove (pre-1.2.4.20) | cosa decideva | ora |
| ---: | :--- | :--- | :--- |
| 1 | `fetch.py:53` `get_latest_product` (chiamato da `fetch.py:42` e `main.py:1066`) | slot dei 6 frame VMI | `fetch_frames(..., anchor_ms=...)` |
| 2 | `main.py:200` `fetch_frames(max_frames=1)` per VIL/ETM/POH | VIL density su un VIL di oggi | `fetch.fetch_product_at(config, product, ref_rd.time_ms)` |
| 3 | `main.py:473` `_fetch_ir108` → `fetch_frames` | overshooting top su un IR di oggi | `fetch_product_at` allo slot della VMI |
| 4 | `environment.py:233-275` (`forecast_days=1` + `pick_current_index`) | CAPE/CIN/shear/SRH/LCL/**quote termiche per MESH** | API di **archivio** + indice per istante |
| 5 | `lightning.py:130-142` `ltg_epoch_floor(now=None)` | slot dei fulmini | `ltg_epoch_floor(now_ms=...)` con il clock iniettato |

I CAPPI erano già ancorati al timestamp della VMI (`main.py:237`): è la ragione per cui
MESH e overhang erano gli unici campi già riproducibili *nel dato radar*.

---

## 2. Contratto della modalità

```
python scripts/radar_engine/main.py --as-of 2026-07-01T13:25:00Z --out-dir <dir>
```

- `--as-of <ISO8601 UTC>` — default `None` = comportamento operativo (ora corrente).
  Forme accettate: `...T13:20Z`, `...T13:20:00Z`, `...T13:20:00+00:00`, `...T13:20:00`.
  Un istante **senza fuso** viene assunto UTC e il campo
  `as_of_mode.timezone_assumed_utc: true` lo dichiara. Un formato non interpretabile
  esce con codice 2 (argparse), **non** ripiega in silenzio sull'ora corrente.
- **Ancora degli slot** = istante richiesto arrotondato **per difetto** al passo del
  prodotto (`source.period_s`, default 300 s = cadenza PT5M misurata). `--as-of
  13:23:41` significa "lo slot 13:20". L'arrotondamento è dichiarato in
  `as_of_mode.radar_anchor_iso`.
- Da quell'ancora i 6 frame VMI sono `T0, T-5, ..., T-25` minuti: la stessa finestra
  del run operativo (`config.py` `history_frames: 6`). **Nessuna finestra inventata.**

### Una sola sorgente di tempo

`radar_engine/clock.py` è l'unico punto che conosce il tempo:

| funzione | uso |
| :--- | :--- |
| `Clock.live()` | run operativo: legge l'orologio |
| `Clock.at_iso(s)` / `at_ms(ms)` | replay: istante **fisso** |
| `Clock.now_ms()` / `now_iso()` | l'istante (fisso in replay) |
| `Clock.anchor_ms(period_s)` | ancora degli slot (`None` in live → `findLastProductByType`) |
| `clock.inject(config, clock)` / `from_config(config)` | iniezione sotto la chiave riservata `_clock` |

---

## 3. Cosa è riproducibile e cosa no

| sorgente | in `--as-of` | motivo |
| :--- | :--- | :--- |
| **VMI (6 frame)** | **riproducibile** | archivio DPC completo sulla finestra utile (misurato: 13/13 slot disponibili sui 13 eventi QC A/B, `retro/REPORT.md` §2.4) |
| **VIL / ETM / POH** | **riproducibile** | chiesti allo **stesso** `time_ms` della VMI; se assenti → `null` + `as_of_mode.degraded` |
| **CAPPI 1..10** | **riproducibile** | già ancorati a `ref_rd.time_ms`; 10/10 livelli allo stesso slot (misurato) |
| **overhang** (CAPPI_2 vs CAPPI_6) | **riproducibile** | dipende solo dai due CAPPI |
| **IR_108** | **parziale** | archivio con **buchi**: 11/13 sui 13 eventi QC A/B; manca il 2026-07-01 12:55–13:40Z (HTTP 500, `retro/REPORT.md` §2.4). Se manca → `null` + `ir108_history_unavailable` |
| **ambiente** (CAPE, CIN, shear, SRH, LCL, WMAXSHEAR, Bunkers, quote termiche) | **NO** su questo host | vedi §3.1 |
| **MESH** | **NO** (dipendenza a valle) | richiede `h0` (freezing level) e `H(-20 °C)` dal profilo termico NWP: con l'ambiente nullo il campo è `null` con `thermal_levels_missing` |
| **fulmini** | **NO** | nessun archivio pubblico: ogni slot storico provato risponde **403** (`retro/REPORT.md` §2.4) → `null` + `lightning_history_unavailable` |
| **registro predizioni** (`data/state/severe_predictions.jsonl`) | **non scritto** | ha un effetto collaterale su disco e ruota a 30 giorni sull'orologio: in replay viene saltato (`severe_predictions: {status: skipped, reason: as_of_replay}`) |
| **archivio rolling** (`--history-dir`) | **non scritto** | gli `uid` di continuità dipendono dall'indice su disco: due replay su alberi diversi darebbero uid diversi. Saltato con warning |
| **tempi di parete** (`cappi_fetch.seconds`, `products.*.elapsed_s`) | **`null`** | non sono un risultato e renderebbero due run identici byte-diversi. `null`, non un numero aggiustato |
| `generated_at`, `data_latency_minutes` | **deterministici** | `generated_at` = istante richiesto; `data_latency_minutes` = (istante − ultimo frame)/60 000 |

### 3.1 L'ambiente: cosa risponde davvero l'API di archivio (misurato)

Richiesta reale eseguita il 2026-10-24 su
`https://archive-api.open-meteo.com/v1/archive?latitude=45.20300&longitude=7.64900&start_date=<giorno>&end_date=<giorno>&hourly=<34 variabili>&temperature_unit=celsius&wind_speed_unit=ms&pressure_unit=hPa&timezone=GMT`

| giorno provato | HTTP | variabili di **superficie** | variabili di **profilo** e CAPE |
| :--- | :--- | :--- | :--- |
| 2026-05-28, 2026-06-10, 2026-07-01, 2026-07-18, 2026-10-11, 2026-10-24 | **200** | `temperature_2m`, `dew_point_2m`, `pressure_msl`, `wind_speed_10m/direction_10m`: **24/24 valori** | `cape`, `convective_inhibition`, `lifted_index`, `wind_*_hPa`, `temperature_*hPa`, `geopotential_height_*`, `freezing_level_height`: **0/24 valori (tutti `null`)** |
| 2023-07-01, 2024-07-01, 2025-07-01, 2026-01-15, 2026-06-01 | 200 | 24/24 | **0/24** |

Cioè: **su questo host l'archivio non copre le variabili che servono al descrittore**,
e non è un limite di data (accade anche per il 2023). Le sole variabili presenti
(temperatura a 2 m, dew point, pressione al livello del mare, vento a 10 m) darebbero un
ambiente **parziale con punteggio 0**, che è peggio di `null`: un `env_score = 0`
pubblicato sembrerebbe "ambiente stabile" invece di "dato assente".

Per questo il replay applica un **gate esplicito** (`ARCHIVE_REQUIRED_VARS`):
`cape`, `convective_inhibition`, `lifted_index`, `wind_speed_500hPa`,
`temperature_500hPa`, `geopotential_height_500hPa`, `freezing_level_height`. Se una
sola manca, l'ambiente **non viene calcolato** e il campo diventa
`null` con `environment_unavailable_for_as_of`. **Mai il meteo corrente.**

> Conseguenza dichiarata: in un replay su questo host S1 (ambiente), S1b (Bunkers),
> S3 (OT dove manca l'IR) e **MESH** non sono valutabili. Restano valutabili
> **VIL density** e **overhang**, che dipendono solo dal radar.

---

## 4. `null_reason` — tabella completa

| campo | `null_reason` | quando |
| :--- | :--- | :--- |
| `severe_stack.environment.*` | `environment_unavailable_for_as_of` | replay, archive HTTP ≠ 200 **oppure** variabili di profilo assenti |
| `severe_stack.environment.*` (default, run live) | `open-meteo_cape_missing`, `open-meteo_cin_missing`, `open-meteo_wind_10m_500hPa_missing`, `temperature_dewpoint_missing`, … | invariati |
| `severe_stack.ot.*` | `ir108_history_unavailable` | replay, IR_108 assente allo slot della VMI |
| `severe_stack.ot.*` (default, run live) | `ir108_frame_unavailable` | invariati |
| `hail.mesh_mm` | `thermal_levels_missing` | conseguenza di `environment_unavailable_for_as_of` (mancano `h0` e `H(-20 °C)`) |
| `c.candidates[*].lightning` | `lightning_history_unavailable` | sempre in replay (nessun archivio LTG) |
| `c.candidates[*].as_of_null_reasons` | dict per candidato con i tre motivi | solo in replay |

Riepilogo macchina-leggibile in **`latest.json`** e **`convective_structures.json`**:

```json
"as_of_mode": {
  "requested": "2026-07-01T13:25:00Z",
  "requested_ms": 1782912300000,
  "requested_iso": "2026-07-01T13:25:00Z",
  "radar_anchor_iso": "2026-07-01T13:25:00Z",
  "slot_period_s": 300,
  "timezone_assumed_utc": false,
  "environment_source": "archive-api.open-meteo.com (non utilizzabile: archive_unavailable:cape,...)",
  "degraded": [
    {"field": "ot", "null_reason": "ir108_history_unavailable"},
    {"field": "lightning", "null_reason": "lightning_history_unavailable"},
    {"field": "environment", "null_reason": "environment_unavailable_for_as_of", "count": 25, "detail": "..."},
    {"field": "hail.mesh_mm", "null_reason": "thermal_levels_missing", "count": 25, "detail": "..."}
  ]
}
```

In modalità **live** il blocco `as_of_mode` **non esiste**: nessuna chiave nuova
nell'output operativo (`output._as_of_summary` restituisce `{}`).

---

## 5. Prova di determinismo (misurata)

Due esecuzioni consecutive con lo **stesso** `--as-of 2026-07-01T13:25:00Z`
(6 frame VMI + 10 CAPPI + VIL/ETM/POH):

| file | sha256 (run 1) | sha256 (run 2) | esito |
| :--- | :--- | :--- | :--- |
| `latest.json` | `eb3c5f6869255a7fe3e06057cf259cb893f13abd0ef2f14251bbaf9cfb80de5b` | identico | **byte-identici** |
| `storms.geojson` | `b8c248bad11227b87fb7a1d3e982d40578002c0c5c91b0724fbe56588e991c7f` | identico | **byte-identici** |
| `tracks.json` | `fbaac11542aa5a26ee503158fc11715732ae06da35e2f774a083ef7fe61222c5` | identico | **byte-identici** |
| `storm_objects.geojson` | `7a0db276eba726244f86a25095b4aa14e59d3ac2807b58f4ffc8585eb5e8d2e6` | identico | **byte-identici** |
| `storm_tracks.json` | `852d27dddbe8580c6c64d80dc661b0abd6c6e895efff031b431af7a9311a58c9` | identico | **byte-identici** |
| `convective_structures.json` | `b2d863451db6f4437fda5f02336c400fc8257056c9a92c63d9e9d25801fd75ef` | identico | **byte-identici** |

Nessun campo è stato "normalizzato a posteriori": i due run sono due esecuzioni
indipendenti del motore (rete inclusa).

---

## 6. Verifica `grep`: nessuna lettura dell'orologio sul percorso attivo

Comando: ricerca di `time.time()` / `datetime.now(utc)` / `utcnow()` in
`scripts/radar_engine/**.py`. Ogni occorrenza residua, con il motivo per cui **non è
raggiungibile** in `--as-of`:

| file:riga | codice | perché non è sul percorso replay |
| :--- | :--- | :--- |
| `clock.py:113` | `datetime.now(_UTC)` | è il ramo **live** di `Clock.now_ms()`: in replay `_fixed_ms is not None` e la funzione ritorna subito |
| `main.py:72` | `_utcnow_str()` | **non più chiamata** da nessun punto del motore (la usa solo un chiamante esterno che la importava); il run usa `clock.now_iso()` |
| `models.py:505` | `utcnow_iso()` | chiamata solo da `forecast.py:343` (fallback se `bundle.generated_at_iso` è assente — è sempre impostato) e da `history.py:146` (archivio rolling, **saltato** in replay) |
| `severe_predictions.py:34,89` | rotazione registro | `_record_predictions` **non viene chiamata** in replay (`main.py` ramo `if clock.replay`) |
| `phase2/environment.py:322` | `pick_current_index` | ramo `now_ms is None`; in replay l'indice è `pick_archive_index(times, as_of_ms)` e `parse_environment` riceve l'indice esplicito |
| `phase2/lightning.py:144` | `ltg_epoch_floor` | ramo `now_ms is None`; il motore passa **sempre** `now_ms=p2_clock.now_ms()`. In replay il ciclo fulmini non viene nemmeno eseguito |
| `fusion/opera_cirrus.py:128` | riferimento di default | modulo **non importato** da nessun punto del motore (prototipo OPERA) |

`fetch.py` non contiene alcuna lettura dell'orologio (solo
`datetime.fromtimestamp` per formattare timestamp che arrivano dal nome del file).

---

## 7. Come rieseguire

```powershell
$env:PYTHONIOENCODING='utf-8'
python scripts\radar_engine\main.py `
  --as-of 2026-07-01T13:25:00Z `
  --out-dir C:\tmp\replay_20260701T1325
```

Per il collaudo completo (6 eventi grandine QC A/B, una volta sola):

```powershell
python scripts\validation\asof_bench.py `
  --labels <labels_metar_it_2015_2026.jsonl> `
  --work-dir <dir> --report <dir>\report.json --markdown <dir>\report.md
```

---

## 8. Limiti dichiarati

1. **L'ambiente non è riproducibile** su questo host (§3.1). Un replay misura il
   sistema operativo **senza** S1/S1b/S3/MESH: non è "il sistema operativo com'era".
   Con un archivio che coprisse le variabili di profilo il gate
   `ARCHIVE_REQUIRED_VARS` passerebbe senza modifiche al codice, ma la sorgente
   resterebbe **un'altra** (rianalisi ERA5 contro analisi operativa): il confronto è
   dichiarato, non identico.
2. **I fulmini non sono riproducibili in nessun modo**: nessun archivio pubblico.
3. **L'IR_108 ha buchi storici** non prevedibili (HTTP 500 su fasce di slot): il campo
   degrada a `null` con motivo, non viene sostituito da un'immagine di un altro istante.
4. **MESH resta null** finché l'ambiente è null: le quote `h0`/`H(-20 °C)` non vengono
   inventate.
5. **I tempi di esecuzione sono `null`** in replay: non sono confrontabili fra run e
   non servono alla validazione.
6. **Gli `uid` di continuità** (`history.py`) non sono riproducibili fra alberi diversi:
   per questo l'archivio rolling è saltato in replay invece di produrre uid diversi.
7. `--as-of` **non** cambia il comportamento operativo: senza l'opzione il clock è
   `live` e il percorso è quello della 1.2.4.19 (verificato: suite completa, §9 del
   report di release).
