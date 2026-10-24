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

Per il collaudo su **footprint geometrico** (1.2.4.21, §9) servono anche le
province italiane (per la frazione d'area) e il GeoJSON dei footprint:

```powershell
python scripts\radar_engine\main.py --as-of 2026-07-01T13:25:00Z `
  --out-dir <dir> --footprints <dir>\footprints.geojson
python scripts\validation\asof_bench_footprint.py `
  --labels <labels_metar_it_2015_2026.jsonl> `
  --italy-geojson data\geography\province_italiane.geojson `
  --work-dir <dir> --report <dir>\report_footprint.json `
  --markdown <dir>\report_footprint.md
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

---

## 9. Accoppiamento sul FOOTPRINT e TASSO BASE (1.2.4.21)

Il collaudo della 1.2.4.20 accoppiava il rapporto METAR al **CENTROIDE** della
struttura con gate 10 km: **0 hit / 6 miss / 69 falsi allarmi**, POD 0,000
[0,000-0,390]. Non era un'assenza di eco: il radar vedeva i temporali (48,5 /
44,0 / 35,5 dBZ sulla verticale di LIPL/LIMF/LIPX) e **nessuna** cella aveva il
centroide entro 10 km (minimo 20,3 km) mentre le celle sono componenti connesse
fino a **4.659 km2**. La stazione cade **dentro** l'eco, il centroide e'
lontano: sbagliava la grandezza dell'accoppiamento, non il prodotto.

### 9.1 Le tre varianti (definizioni)

| variante | allarmi | geometria | domanda a cui risponde |
| :--- | :--- | :--- | :--- |
| **(a) `cells`** | ogni cella di ogni frame | poligono della componente connessa (`radar_engine/footprint.py`) | "il radar ha visto qualcosa sulla stazione?" |
| **(a2) `structures_all`** | ogni struttura convettiva emessa | unione dei poligoni delle celle membre, al frame del `last_seen` | stessa popolazione di allarmi della 1.2.4.20: confronto **a parita' di allarmi** |
| **(b) `structures_severe`** | solo `level` in `strong`/`intense` | idem | "il prodotto avrebbe avvisato?" |

Un evento e' **hit** se il punto della stazione cade **dentro** il poligono
oppure entro **10 km dal bordo**; finestra temporale invariata [-20, +6] min,
greedy 1-a-1 per distanza minima. Nessuna soglia, formula o peso del prodotto
e' stato modificato: l'unica differenza rispetto alla 1.2.4.20 e' la geometria
dell'accoppiamento (punto -> poligono invece di punto -> centroide).

Il footprint e' la componente connessa ricalcolata sulla VMI del frame con
`detect.dbz_threshold`, `morph_radius_px`, `connectivity_8` **della config**
(nessun valore nuovo), poligonizzata con `rasterio.features.shapes` e
georeferenziata con l'affine del GeoTIFF. L'identita' della cella e' il suo
**bbox + numero di pixel** nell'etichetta del frame, non un match geometrico
fra blob. Difetto trovato e corretto qui: il pixel del **centroide** puo'
cadere fuori dalla componente (componente concava) e in 1.2.4.20 lasciava 3
strutture su 69 senza footprint (`cell_mask_not_resolved`); con l'identita'
bbox+pixel_count i 69/69 record hanno il poligono. L'overhang **non** e' stato
toccato (usa ancora il seed del centroide) e i suoi numeri restano quelli
della 1.2.4.20: il residuo e' dichiarato.

### 9.2 Tasso base (misurato: e' il cuore dell'esperimento)

Copertura dell'**unione** dei footprint emessi nella run (episodio di 25-30
min), indipendentemente dagli eventi. Area d'Italia da `province_italiane.geojson`
rasterizzato a 0,01 deg con pixel pesati su `cos(lat)`: 301.609 km2 contro
302.073 km2 di riferimento (-0,15%). Stazioni dentro o entro 10 km dal bordo,
su **99** stazioni METAR italiane.

| run (evento) | (a) area | (a) stazioni /99 | (a2) area | (a2) stazioni /99 | (b) area | (b) stazioni /99 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| LIPL 2026-05-28T14:55Z | 12,2% | 22 | 8,2% | 18 | 8,2% | 18 |
| LIPI 2026-06-10T15:55Z | 12,0% | 19 | 8,7% | 16 | 8,7% | 16 |
| LIMF 2026-07-01T13:20Z | 23,1% | 37 | 6,4% | 21 | 6,4% | 21 |
| LIMP 2026-07-01T14:50Z | 8,2% | 22 | 3,8% | 10 | 3,8% | 10 |
| LIML 2026-07-18T19:50Z | 5,8% | 14 | 3,7% | 12 | 3,7% | 12 |
| LIPX 2026-10-11T23:50Z | 2,2% | 9 | 0,8% | 3 | 0,8% | 3 |
| **mediana / massimo** | **10,1% / 23,1%** | **20,7% / 37,4%** | **5,1% / 8,7%** | **14,1% / 21,2%** | idem | idem |

Nessuna run supera il 50% (ne' di area ne' di stazioni): il POD sul footprint
**e' informativo** secondo la regola dichiarata (> 50% = artefatto). Con
`cells` una stazione su cinque e' coperta "per caso", con le strutture una su
sette: numeri che vanno letti accanto al POD, non dopo.

Sulle **13 stazioni con almeno un evento severo** nella finestra (grandine,
vento severo, imbuto/tornado; QC A/B) la copertura mediana e' **26,9%** in
tutte e tre le varianti: e' la probabilita' che una stazione con evento cada
dentro un poligono marcato **per caso**. Da qui: hit attesi a caso 6 x 26,9% =
**1,6** su 6 contro i 5 osservati, P(X >= 5) = **0,0066** (binomiale,
diagnostica: n=6, non un test formale).

Nota sulle stazioni: `retro/REPORT.md` dichiarava 14 stazioni severe (1,46% del
territorio, 4.398 km2 con p * 10^2 km2). Con le etichette attuali la regola
"tipo severo + QC A/B" da **13** stazioni (4.084 km2 = **1,35%**), con QC A/B/C
ne da 16. Le 14 non sono riproducibili: si usano **13** e il numero vecchio
resta dichiarato come non riprodotto.

### 9.3 Matrice 2x2 per variante (6 eventi, 5 giornate)

| variante | hit | miss | falsi allarmi | correct neg. | POD | Wilson 95% | CSI (indicativo) |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- | ---: |
| (a) `cells` | 5 | 1 | 1.294 | 0 | 0,833 | [0,436 - 0,970] | 0,004 |
| (a2) `structures_all` | 5 | 1 | 64 | 0 | 0,833 | [0,436 - 0,970] | 0,071 |
| (b) `structures_severe` | 5 | 1 | 64 | 0 | 0,833 | [0,436 - 0,970] | 0,071 |

Confronto diretto, **stessi 6 eventi e stesse run**:

| accoppiamento | hit | miss | falsi allarmi | POD | Wilson 95% |
| :--- | ---: | ---: | ---: | ---: | :--- |
| centroide, gate 10 km (1.2.4.20) | 0 | 6 | 69 | 0,000 | [0,000 - 0,390] |
| footprint, (a2) strutture | 5 | 1 | 64 | 0,833 | [0,436 - 0,970] |

Il FAR **non e' stimabile**: il METAR copre 13 stazioni = 1,35% del territorio,
quindi 64 allarmi non accoppiati non sono 64 falsi allarmi. Il CSI e'
indicativo per lo stesso motivo. I falsi allarmi della variante (a) sono 1.294
perche' ogni cella di ogni frame e' un allarme: e' la numerosita' del layer
grezzo, non un giudizio.

### 9.4 Per evento

| evento | stazione | severita' | (a) cells | (a2)/(b) strutture |
| :--- | :--- | :--- | :--- | :--- |
| 2026-05-28T14:55Z | LIPL | small_hail_lt5mm | HIT (dentro) | HIT (dentro, `intense`) |
| 2026-06-10T15:55Z | LIPI | large_hail_ge5mm | HIT (dentro) | HIT (dentro, `strong`) |
| 2026-07-01T13:20Z | LIMF | large_hail_ge5mm | HIT (dentro) | HIT (dentro, `strong`) |
| 2026-07-01T14:50Z | LIMP | small_hail_lt5mm | **miss (40,5 km)** | **miss (57,9 km)** |
| 2026-07-18T19:50Z | LIML | small_hail_lt5mm | HIT (dentro) | HIT (0,2 km dal bordo, `strong`) |
| 2026-10-11T23:50Z | LIPX | large_hail_ge5mm | HIT (dentro) | HIT (dentro, `strong`) |

L'unico miss e' LIMP: nessuna cella entro 40,5 km alle 14:50Z, nessuna
struttura entro 57,9 km. E' un miss **geometrico** (il radar non ha eco li'),
non un problema di centroide: la variante (a) lo misura correttamente come
assenza.

### 9.5 La variante (b) coincide con (a2): fatto del prodotto

I 69 record emessi dalle 6 run sono **56 `strong` + 13 `intense`**, zero
`organized`/`unorganized`: il motore pubblica solo i record con
`candidate: true` e il gate di organizzazione li porta gia' a `strong`. Quindi
il filtro (b) non toglie nulla e (b) = (a2) **per costruzione del prodotto**.
Non e' stata introdotta nessuna soglia nuova per separarli: si dichiara che con
la politica di emissione attuale "avrebbe avvisato" e "ha emesso una struttura"
sono lo stesso insieme.

### 9.6 Cosa questo collaudo NON misura (dichiarato)

* **MESH**: in replay e' `null` con `thermal_levels_missing` (l'archivio
  Open-Meteo non ha CAPE ne' variabili di profilo: 0/24 valori, verificato il
  2026-10-24). Il collaudo misura **SOI, overhang, VIL density**, non MESH.
* **Ambiente / fulmini / overshooting top**: `null` con motivo in replay
  (§3.1). Nessun giudizio su S1, S1b, S3.
* **FAR**: non stimabile con 13 stazioni (§9.3).
* **Taratura**: n=6. **Nessuna** soglia, formula o peso e' stato modificato in
  questa release e nessuna taratura e' stata fatta: con 6 eventi qualunque
  aggiustamento sarebbe overfitting. Le soglie overhang restano 45/50 dBZ.
* **Poligono**: il footprint e' la componente connessa a 1 km (pixel interi),
  non il contorno dell'eco a risoluzione radar nativa; il bordo e' quindi
  quantizzato a 1 km, coerente con il gate di 10 km.

### 9.7 Risposta alla domanda del collaudo

**Il prodotto avrebbe avvisato?** Si', su 5 dei 6 eventi grandine QC A/B
(POD 0,833, Wilson [0,436-0,970]), con la stazione DENTRO il poligono in 4 casi
su 5 e a 0,2 km dal bordo nel quinto; il sesto (LIMP) non ha eco entro 40 km.

**Quanto spesso avrebbe avvisato a vuoto?** Non e' misurabile con questa
verita': 64 strutture su 69 non hanno un evento associato, ma il METAR vede
1,35% del territorio e quindi non puo' dire se quei 64 fossero falsi allarmi.
Quello che si puo' dire e' il **tasso base**: in una run il 5,1-8,7% dell'area
d'Italia e il 14,1% (mediana) delle stazioni e' dentro o entro 10 km da una
struttura marcata, e il 26,9% delle stazioni con eventi lo e' per caso. Un POD
alto con questo tasso base non e' un artefatto (P a caso 0,0066), ma non e'
nemmeno una misura di accuratezza spaziale: e' "il radar ha visto l'eco e il
prodotto l'ha etichettata". Per separare le due cose servono etichette negative
(stazioni senza evento dentro le strutture) e un numero di eventi che renda
una taratura diversa da un fit su 6 punti.