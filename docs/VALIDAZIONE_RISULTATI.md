# Validazione del layer severo: esiti misurati (release 1.2.4.20 e 1.2.4.21)

> **STATO DEL DATASET GREZZO (aggiornato il 2026-11-23).** Questo documento e' la
> prova dei numeri citati altrove ("6 grandini QC A/B su 5 giornate", "13 stazioni
> severe", "42.391 eventi"). Il dataset grezzo che li produce **non sta su `main`**:
> sta sul **branch dati orfano `validation-labels`** (commit senza parent, cosi'
> non pesa su `main`), in `data/validation/labels.jsonl`:
> **42.391 righe, 24.999.406 B, sha256 `5F8023F65E7D5393...B5DED2C2`**.
> Come si legge: `https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/validation-labels/data/validation/labels.jsonl`
> (il `README.md` del branch porta attribuzione, licenza e limiti; `labels_meta.json`
> righe/byte/sha/periodo/comando di rigenerazione).
>
> Storia, misurata e non dedotta: al **2026-11-23** il dataset **non esisteva** ne'
> su `main` ne' in nessuna copia locale (ricerca su `Desktop\meteorisk\**`,
> `%TEMP%\opencode\**`, `Downloads`: zero `labels*.jsonl` da 25 MB, la cache METAR
> dei documenti — `cache/metar_it_*.csv.gz`, ~735 MB — **non c'era piu'**). E' stato
> quindi **ricostruito da zero** riscaricando i bollettini dall'endpoint IEM
> (rete `IT__ASOS`, `report_type=3`): **23 file** (11 annuali + 12 mensili del 2022,
> che come richiesta annuale viene resettata), **123.523.965 B** compressi,
> **7.272.744 bollettini** letti. `scripts/validation/build_labels.py` (sha256
> `CE09C581...041C2391`, lo stesso di `main`) ha riprodotto **42.391 eventi**,
> **42.212 in verita'** (`--min-qc B`) e **179** `hail` qc `C` esclusi: gli stessi
> numeri della misura del 2026-10-25, e **24.999.406 B**, cioe' la stessa
> dimensione byte per byte. `--selftest`: 0 controlli falliti. Unica differenza
> dichiarata: il dataset e' stato rigenerato **senza** `--cells-dir`, quindi
> `match_status` e' `no_snapshot_in_window` su tutte le righe e
> `distance_km`/`matched_cell_id`/`matched_ts` sono `null` (l'accoppiamento con le
> celle e' un passo separato, `asof_bench*.py --labels`).
> I **75 eventi Hail CREA WDA** sono un dataset **diverso** (danno agricolo
> provinciale, nessuna ora ne' coordinate) e stanno in `data/validation/crea_wda/`:
> non sostituiscono questa verita' METAR.

Documento di **risultati**, non di metodo. Scritto il **2026-10-25**. Non contiene push, commit,
modifiche a codice, a release o allo staging: descrive misure gia' eseguite.

Metodo, licenze e protocollo stanno in `docs/VALIDAZIONE_SEVERO.md` (34.673 byte, 379 righe);
replay e accoppiamento stanno in `docs/REPLAY_AS_OF.md` (23.500 byte, 394 righe, release 1.2.4.21).
Questo documento mette i due risultati **uno accanto all'altro** e dichiara cosa resta escluso.

Legenda delle fonti citate riga per riga:

| sigla | fonte |
| :--- | :--- |
| **[V]** | `docs/VALIDAZIONE_SEVERO.md` (licenze, dataset, potere statistico) |
| **[R]** | `docs/REPLAY_AS_OF.md` (replay `--as-of`, determinismo, tasso base, footprint) |
| **[L]** | `scripts/validation/build_labels.py` -> `labels.jsonl` (42.391 righe, 24.999.406 B; dal 2026-11-23 sul branch dati `validation-labels`) |
| **[B]** | `scripts/validation/asof_bench.py` (collaudo centroide) e `asof_bench_footprint.py` (collaudo footprint) |
| **[P]** | `scripts/validation/power_analysis.py` (numerosita' per il POD) |
| **[VERSION]** | blocco `VERSION` della 1.2.4.21, campo `NOTE` (misure della release) |

---

## 1. Sintesi in cinque righe

1. **Verita'**: ESWD e' inutilizzabile per licenza (divieto di pubblicare dati grezzi, clausola 1.iv),
   quindi la verita' sono i **METAR/SPECI**: 42.391 eventi, 99 stazioni, 2015-01-01 -> 2026-10-23,
   estratti da 7.272.677 bollettini **[V]**.
2. **Numerosita'**: l'archivio radar comincia il 2026-05-20, quindi nella finestra utile di 157
   giorni restano **6 grandini QC A/B su 5 giornate**, cioe' il **10%** del `n=60` richiesto per un
   POD con semi-larghezza 0,10 **[V] [P]**.
3. **Replay**: `--as-of` rende il motore deterministico (6/6 file byte-identici in 1.2.4.20, 7/7 in
   1.2.4.21), ma l'archivio Open-Meteo restituisce **0/24** valori sulle variabili di profilo: in
   replay ambiente, Bunkers, OT e **MESH** restano `null` con motivo **[R]**.
4. **Primo collaudo** (accoppiamento sul **centroide**, gate 10 km): 0 hit / 6 miss / 69 allarmi,
   **POD 0,000**. Non misurava il detector: nessuna cella aveva il centroide entro 10 km (minimo
   20,3 km) mentre le celle sono componenti connesse fino a 4.659 km2 **[R] [B]**.
5. **Secondo collaudo** (accoppiamento sul **footprint**): **5 hit / 1 miss**, POD **0,833**
   [0,436-0,970], contro un tasso base per run del 5,1-8,7% dell'area e del 14,1% delle stazioni
   (hit attesi per caso 1,6 su 6, P(X >= 5) = 0,0066). Il **FAR non e' stimabile** **[R] [B]**.

---

## 2. Verita' di riferimento

### 2.1 ESWD (ESSL) - scartato, con la clausola che lo esclude

L'accordo ESWD nella versione del **22/09/2026** consente l'uso non commerciale ma impone cinque
vincoli che questo progetto non puo' rispettare **[V]**:

| clausola | contenuto | effetto qui |
| :--- | :--- | :--- |
| **1.iv** | divieto di pubblicare i dati grezzi | le etichette vivono in un repo pubblico |
| **1.v** | divieto di trasmettere i dati a terzi | il repo e' accessibile a chiunque |
| **1.vi** | divieto di derivati di scarso valore scientifico, giudicati da ESSL | il giudizio non e' nostro |
| **1.ix** | dall'output non deve essere ricostruibile l'originale | un etichetta geolocalizzata lo ricostruisce |
| **1.xi** | sanzione fino a **60.000 EUR** | rischio non assumibile |

**Zero record ESWD scaricati** **[V]**. L'eccezione della clausola 1.vii esiste ma va chiesta **a
priori e per iscritto**, e non e' stata chiesta.

### 2.2 METAR/SPECI usato al suo posto

| voce | valore | fonte |
| :--- | :--- | :--- |
| fonte | METAR/SPECI, NOAA/NCEI via IEM (`mesonet.agron.iastate.edu`), pubblico dominio **dichiarato** | [V] |
| bollettini letti | **7.272.677** | [V] |
| eventi nel dataset (`labels_metar_it_2015_2026.jsonl`) | **42.391** | [L] |
| stazioni | **99** | [V] |
| copertura temporale | **2015-01-01 -> 2026-10-23** (2026 parziale: 482.684 bollettini contro ~660.000 di un anno intero) | [V] |
| ripartizione | thunderstorm **39.494**, severe_wind **2.329**, hail **488** (di cui **309** con QC A/B), funnel_cloud **76**, tornado **4** | [V] |

La somma della ripartizione e' 42.391, cioe' il dataset completo. La **verita' usata nei collaudi**
e' di **42.212 eventi**: i 179 `hail` con `qc=C` restano nel JSONL ma non entrano nella verita'
(hail 309) **[V]**. I due numeri non sono in contraddizione: uno e' il file, l'altro e' il filtro.

**Non verificato, dichiarato**: i termini d'uso **ISD/NODD** e la **policy WMO** sul riuso dei
messaggi di scambio **non sono stati verificati** **[V]**. La qualifica "pubblico dominio" e' quella
dichiarata dal distributore, non un parere legale.

### 2.3 I due limiti che decidono le conclusioni

1. **La verita' e' una rete di 99 punti, non un piano** **[V]**. Un evento a 3 km da un aeroporto e'
   invisibile; una grandinata a 15 km da una stazione non entra nel dataset. Le 309 grandini sono un
   **minimo osservato**, non la frequenza italiana.
2. **La serie delle raffiche non e' omogenea** **[V]**: bollettini con gruppo di raffica dal 2,05%
   (2022) al 2,92% (2017) fino al 2024, poi **1,18% nel 2025**. Il calo degli eventi vento nel 2025
   (79 contro 231 del 2023) e' un calo di **segnalazione**, non di fenomeni: qualunque POD/FAR sul
   vento va calcolato per anno.

---

## 3. Numerosita' e potere statistico

### 3.1 La finestra utile e' dettata dall'archivio radar

| voce | valore | fonte |
| :--- | :--- | :--- |
| inizio archivio radar DPC disponibile | **2026-05-20** | [R] |
| finestra usata | **2026-05-20 -> 2026-10-24 (157 giorni)** | [B] |
| eventi utilizzabili | **6 grandini QC A/B su 5 giornate** | [R] [VERSION] |
| stazioni severe nella finestra | **13** (grandine, vento severo, imbuto/tornado, QC A/B) = 4.084 km2 = **1,35%** del territorio | [R] |
| tasso | **0,0382** grandini valide/giorno (6 / 157) | calcolo sui due numeri sopra |

Sulle stazioni: `retro/REPORT.md` dichiarava **14** stazioni severe (1,46% del territorio); con le
etichette attuali la regola "tipo severo + QC A/B" ne da **13**, e con QC A/B/C ne da 16. **Il 14 non
e' riproducibile**: si usa **13** e il numero vecchio resta dichiarato come non riprodotto **[R]**.

### 3.2 Il progetto chiede n=60, ne abbiamo il 10%

Formula di Wilson (z = **1,959964** per il 95%), non Wald, perche' a `n` piccolo e `p` vicino a 1
Wald esce da [0,1] **[V] [P]**:

| POD | semi-larghezza | n richiesto | CI Wilson a quel n |
| ---: | ---: | ---: | :--- |
| 0,80 | **0,10** | **60** | [0,6822, 0,8817] |
| 0,80 | 0,05 | 245 | [0,7455, 0,8453] |
| 0,50 (caso peggiore) | 0,10 | 93 | [0,4004, 0,5996] |
| 0,50 (caso peggiore) | 0,05 | 381 | [0,4500, 0,5500] |

Con 6 eventi abbiamo il **10%** di n=60 (`6 / 60`) **[P]**. Riproducibile con
`python scripts/validation/power_analysis.py`: la tabella 60/245/93/381 e' identica a quella del
documento di metodo **[V]**.

### 3.3 A n=6 solo 2 esiti su 7 sono informativi

Con 6 casi e un'attesa dichiarata, gli esiti possibili sono 7 (k = 0..6); **solo k=5 e k=6**
distinguono "layer rotto" da "layer come previsto". Un risultato k=0 non falsifica nulla: e' uno dei
5 esiti compatibili con l'attesa **[VERSION]**. E' il motivo per cui il primo collaudo (0/6) e' stato
trattato come **diagnostica**, non come verdetto.

### 3.4 Prospettiva, dichiarata come proiezione

Al tasso di **0,0382** grandini valide/giorno **[B]**, e assumendo il tasso costante: `n=9` arriva con
la **stagione 2027**, `n=60` nel **2030**. E' una **proiezione a tasso costante**, non una misura: con
157 giorni di archivio non c'e' base per stimare la variabilita' interannuale.

Il rimedio strutturale non e' aspettare: e' un **archivio a lungo termine delle predizioni**
(~**1,9 MB/giorno**, ~**700 MB/anno**) **[R]**. Rimandato per **decisione dell'utente il
24/10/2026**. Il registro attuale (`data/state/severe_predictions.jsonl`) **ruota a 30 giorni**,
quindi **oggi non accumula**: senza quell'archivio la numerosita' non cresce piu' velocemente
dell'archivio radar.

---

## 4. Replay deterministico e i suoi muri

### 4.1 Cinque sorgenti di tempo, un solo clock

In 1.2.4.20 le cinque letture dell'orologio che decidevano **quali dati scaricare** sono state
ancorate a un istante fisso. Righe misurate sull'albero 1.2.4.20 **[R]**:

| sorgente | dove | come e' ancorata |
| :--- | :--- | :--- |
| slot radar (VMI, 6 frame) | `fetch.py:53` | `anchor_ms` arrotondato per difetto a `source.period_s` (300 s); `findLastProductByType` non viene piu' chiamata |
| VIL / ETM | `main.py:208-209` | `fetch.fetch_product_at(config, product, ref_time_ms)` allo **stesso** `time_ms` della VMI |
| IR_108 | `main.py:493-500` (`_fetch_ir108`) | stesso slot della VMI, non l'ultimo prodotto |
| ambiente | `environment.py:233-275` | richiesta all'API di **archivio** (`OPENMETEO_ARCHIVE_URL`, costruita a 273-275) + `pick_archive_index` |
| fulmini | `lightning.py:130-142` (`ltg_epoch_floor`) | accetta `now_ms`; il motore passa sempre il clock |

⚠️ Due riferimenti riga che circolano per VIL/ETM e IR_108 (`main.py:200` e `main.py:473`) **non
corrispondono** in questo albero: alla riga 200 c'e' la gestione di un `shape mismatch`, alla 473 una
tupla di `checks` di `_ir108_grid_mismatch`. Le righe in tabella sono quelle **misurate**; il ruolo
della sorgente e' lo stesso. In 1.2.4.21 il punto unico e' `clock.py` e le righe slittano (il wiring
`--footprints` e' stato aggiunto in `main.py`, 1.440 righe totali contro 1.429 della 1.2.4.20).

### 4.2 Determinismo: misurato, non dichiarato

| release | esito | fonte |
| :--- | :--- | :--- |
| **1.2.4.20** | **6/6** file byte-identici fra due run con lo stesso `--as-of` (`latest.json`, `storms.geojson`, `tracks.json`, `storm_objects.geojson`, `storm_tracks.json`, `convective_structures.json`) | [R] §5 |
| **1.2.4.21** | **7/7** file byte-identici, incluso `footprints.geojson` | [VERSION] |

Nessun campo e' stato normalizzato a posteriori: sono due esecuzioni indipendenti, rete inclusa
**[R]**. Il requisito perche' questo valga e' che in replay **non si scriva stato**: registro
predizioni e archivio rolling sono saltati (`severe_predictions: {status: skipped, reason:
as_of_replay}`) e i tempi di parete sono `null` **[R]**.

### 4.3 Il muro: l'archivio Open-Meteo non copre il profilo

Richiesta reale del 2026-10-24 su `archive-api.open-meteo.com/v1/archive`, 34 variabili, su 6 giorni
della finestra utile **e** su 5 date di controllo (2023-07-01, 2024-07-01, 2025-07-01, 2026-01-15,
2026-06-01) **[R]**:

| gruppo di variabili | HTTP | valori |
| :--- | :--- | :--- |
| superficie (`temperature_2m`, `dew_point_2m`, `pressure_msl`, `wind_speed_10m`/`direction_10m`) | **200** | **24/24** |
| `cape`, `convective_inhibition`, `lifted_index`, `wind_*_hPa`, `temperature_*hPa`, `geopotential_height_*`, `freezing_level_height` | **200** | **0/24 (tutti `null`)** |

Non e' un limite di data: succede anche sulle date del 2023 **[R]**. Un ambiente con le sole
variabili di superficie darebbe `env_score = 0`, che sembrerebbe "ambiente stabile" invece di "dato
assente"; per questo il replay applica il gate `ARCHIVE_REQUIRED_VARS` e pubblica `null` con
`environment_unavailable_for_as_of` **[R]**. **Mai il meteo corrente.**

Conseguenze dichiarate, in catena:

| campo | in replay | motivo |
| :--- | :--- | :--- |
| ambiente (CAPE, CIN, shear, SRH, LCL, WMAXSHEAR) | `null` | `environment_unavailable_for_as_of` |
| **Bunkers** RM/LM | `null` | dipende dal profilo 0-6 km |
| **overshooting top** | `null` dove manca l'IR (11/13 slot disponibili; buco 2026-07-01 12:55-13:40Z) | `ir108_history_unavailable` |
| **MESH** | `null` | `thermal_levels_missing` (h0 e H(-20 C) vengono dal profilo termico) |
| **fulmini** | `null` **sempre** | nessun archivio pubblico storico: ogni slot provato risponde **403** |

Restano valutabili **VIL density** e **overhang**, che dipendono solo dal radar **[R]**. Per una
validazione storica completa serve una **rianalisi** (ERA5 via CDS, con credenziali): e' una
**sorgente diversa** dall'analisi operativa, e va dichiarata come tale, non presentata come identica
**[R]**.

---

## 5. I due collaudi a confronto (stessi 6 eventi, stesse 6 run)

### 5.1 Primo collaudo: centroide, gate 10 km (1.2.4.20)

| voce | valore | fonte |
| :--- | ---: | :--- |
| hit | **0** | [R] §9.3 |
| miss | **6** | [R] §9.3 |
| allarmi grezzi | **69** | [R] §9.3 |
| POD | **0,000** [0,000-0,390] | [R] §9.3 |
| CSI | 0,000 | [R] §9.3 |

**Causa diagnosticata, non supposta**: nessuna cella aveva il centroide entro 10 km dalla stazione
(minimo **20,3 km**) mentre le celle sono **componenti connesse fino a 4.659 km2**; sulla verticale
delle stazioni il radar vedeva **48,5 / 44,0 / 35,5 dBZ** (LIPL / LIMF / LIPX) **[R] [VERSION]**.
La stazione cade **dentro** l'eco e il centroide e' lontano: sbagliava la **grandezza
dell'accoppiamento**, non il detector. Il POD = 0 misurava la regola di matching.

### 5.2 Secondo collaudo: footprint, punto -> poligono (1.2.4.21)

Un evento e' **hit** se la stazione cade **dentro** il poligono oppure entro **10 km dal bordo**;
finestra temporale invariata [-20, +6] min, greedy 1-a-1 per distanza minima. Nessuna soglia,
formula o peso del prodotto e' stato modificato: l'unica differenza e' la geometria
dell'accoppiamento **[R]**.

| accoppiamento | hit | miss | allarmi non accoppiati | POD | Wilson 95% | CSI (indicativo) |
| :--- | ---: | ---: | ---: | ---: | :--- | ---: |
| centroide, gate 10 km (1.2.4.20) | 0 | 6 | 69 | 0,000 | [0,000-0,390] | 0,000 |
| **footprint, strutture** (1.2.4.21) | **5** | **1** | **64** | **0,833** | [0,436-0,970] | **0,071** |
| footprint, celle (1.2.4.21) | 5 | 1 | 1.294 | 0,833 | [0,436-0,970] | 0,004 |

I 1.294 allarmi della variante "celle" sono la numerosita' del layer grezzo (ogni cella di ogni
frame), non un giudizio **[R]**. Per evento:

| evento (UTC) | stazione | severita' | footprint | esito |
| :--- | :--- | :--- | :--- | :--- |
| 2026-05-28T14:55Z | LIPL | small_hail_lt5mm | dentro, `intense` | **HIT** |
| 2026-06-10T15:55Z | LIPI | large_hail_ge5mm | dentro, `strong` | **HIT** |
| 2026-07-01T13:20Z | LIMF | large_hail_ge5mm | dentro, `strong` | **HIT** |
| 2026-07-01T14:50Z | LIMP | small_hail_lt5mm | **nessuna eco entro 40,5 km** (struttura piu' vicina 57,9 km) | **MISS** |
| 2026-07-18T19:50Z | LIML | small_hail_lt5mm | 0,2 km dal bordo, `strong` | **HIT** |
| 2026-10-11T23:50Z | LIPX | large_hail_ge5mm | dentro, `strong` | **HIT** |

L'unico miss e' **geometrico**: alle 14:50Z il radar non ha eco su LIMP, e la variante "celle" lo
misura correttamente come assenza (nessuna cella entro 40,5 km) **[R]**.

### 5.3 Tasso base: la difesa contro l'artefatto

Il tasso base e' la copertura dell'**unione** dei footprint emessi nella run (episodio di 25-30 min),
indipendentemente dagli eventi. Area d'Italia rasterizzata a 0,01 deg con pixel pesati su `cos(lat)`:
**301.609 km2** contro 302.073 km2 di riferimento (**-0,15%**) **[R]**.

| grandezza | celle | strutture | fonte |
| :--- | ---: | ---: | :--- |
| area d'Italia coperta, per run | 2,2-23,1% (mediana 10,1%) | **5,1-8,7%** | [R] §9.2 |
| stazioni METAR coperte, per run (su 99) | 9-37 (mediana 20,7%) | **14,1% (mediana), massimo 21,2%** | [R] §9.2 |
| copertura sulle 13 stazioni con evento severo | 26,9% | 26,9% | [R] §9.2 |

Da qui: **hit attesi per caso 1,6 su 6** (6 x 26,9%) contro i **5 osservati**, **P(X >= 5) = 0,0066**
(binomiale; diagnostica con n=6, non un test formale) **[R]**. **Nessuna run supera il 50%** ne' di
area ne' di stazioni: la metrica e' **informativa** secondo la regola dichiarata (> 50% = artefatto)
**[R]**. Con le sole celle una stazione su cinque e' coperta "per caso"; con le strutture una su sette.

### 5.4 FAR: non stimabile, e non viene stimato

Le **13** stazioni severe coprono **1,35%** del territorio (**4.084 km2**) **[R]**. I **64** allarmi
senza evento associato **non sono classificabili come falsi allarmi**: il restante **98,65%** del
territorio (100 - 1,35) non e' visto dalla verita' **[R]**. Un FAR calcolato su questa copertura misurerebbe la densita' della rete
METAR, non il prodotto. Il CSI della tabella e' quindi **indicativo** per lo stesso motivo.

### 5.5 Le 69 strutture emesse: 56 `strong` + 13 `intense`

Zero `organized`, zero `unorganized`: il motore pubblica **solo i candidati** e il gate di
organizzazione li porta gia' a `strong` **[R]**. Quindi le varianti "tutte le strutture" e "solo
strutture severe" **coincidono** (5/1/64 entrambe): e' un **fatto del prodotto**, non una taratura.
Con la politica di emissione attuale "avrebbe avvisato" e "ha emesso una struttura" sono lo stesso
insieme **[R]**.

### 5.6 Conclusione onesta

Il prodotto **avrebbe avvisato su 5 eventi su 6** (POD 0,833, Wilson [0,436-0,970]), con la stazione
**dentro** il poligono in 4 casi su 5 e a 0,2 km dal bordo nel quinto. Questo dice che **il radar ha
visto l'eco e il prodotto l'ha etichettata**: non dice che il prodotto sia **spazialmente accurato**.
Separare le due cose richiede **etichette negative** (stazioni senza evento dentro le strutture) e
piu' di 6 eventi. **Nessuna soglia e' stata tarata** sui 6 eventi: con n=6 qualunque aggiustamento
sarebbe **overfitting** **[R] [VERSION]**.

---

## 6. Cosa e' dimostrato, cosa NON e' dimostrato, cosa servirebbe

### 6.1 Dimostrato (con la misura che lo dimostra)

1. **Il replay e' deterministico** con lo stesso `--as-of`: 6/6 file byte-identici (1.2.4.20), 7/7
   (1.2.4.21) **[R] [VERSION]**.
2. **La causa del POD = 0 era l'accoppiamento**, non il detector: a parita' di 6 eventi, 6 run e
   soglie, cambiando solo la geometria (punto -> poligono) si passa da 0 hit a 5 hit **[R]**.
3. **Il layer vede l'eco e la etichetta** dove la stazione e' dentro l'eco: 5/6, con P(X >= 5) =
   0,0066 sotto il tasso base misurato **[R]**.
4. **Il tasso base e' basso e la metrica e' informativa**: nessuna run copre piu' del 23,1% dell'area
   (celle) o dell'8,7% (strutture), nessuna supera il 50% di stazioni **[R]**.
5. **L'ambiente non e' ricostruibile dall'archivio Open-Meteo**: 0/24 valori su 34 variabili, anche su
   date del 2023 **[R]**.
6. **ESWD non e' utilizzabile** per questo progetto senza violare l'accordo: clausole 1.iv, 1.v, 1.vi,
   1.ix, penale 1.xi **[V]**.

### 6.2 NON DIMOSTRATO

Sezione esplicita: qui non c'e' un risultato, e nessuna riga va letta come tale.

1. **Accuratezza spaziale.** Non c'e' una sola etichetta negativa: non sappiamo quante stazioni
   *dentro* le strutture non abbiano avuto eventi. Il POD misura il richiamo, non la precisione.
2. **FAR / tasso di falsi allarmi.** Non stimabile: 13 stazioni severe = 1,35% del territorio, 64
   allarmi non accoppiati non classificabili **[R]**.
3. **POD con la precisione dichiarata.** n=6 contro n=60 richiesti (10%); l'intervallo
   [0,436-0,970] e' largo 0,534 e contiene sia 0,5 sia 0,9. Il numero 0,833 **non e' una stima
   stabile**: e' quello che 6 eventi possono dire.
4. **MESH, Bunkers, overshooting top, ambiente, fulmini nel collaudo.** Tutti `null` con motivo in
   replay (0/24 sulle variabili di profilo; nessun archivio fulmini): **nessun giudizio** su S1, S1b,
   S3, MESH **[R]**.
5. **Nessuna taratura.** Zero soglie, formule o pesi modificati. Le soglie overhang restano 45/50 dBZ
   **[VERSION]**. Con n=6 non e' prudenza: e' l'unica scelta difendibile.
6. **Le 14 stazioni severe di `retro/REPORT.md`.** Non riproducibili con QC A/B ne' con A/B/C: si usa
   **13** **[R]**.
7. **Termini ISD/NODD e policy WMO.** Non verificati **[V]**.
8. **Copertura prima del 2026-05-20.** Nessun archivio radar: i 42.391 eventi del dataset non hanno
   un passato radar utilizzabile, quindi la numerosita' non e' recuperabile a ritroso.
9. **Rappresentativita' del dataset.** 99 punti, non un piano; serie raffiche disomogenea (1,18% nel
   2025) **[V]**.
10. **Le proiezioni n=9 / n=60.** Sono un tasso costante applicato al futuro, non una misura.
11. **Robustezza del risultato al variare delle soglie.** Non testata: non e' stata fatta nessuna
    analisi di sensibilita', e con 6 eventi non sarebbe interpretabile.

### 6.3 Cosa servirebbe, in ordine di costo

| cosa | perche' | stato |
| :--- | :--- | :--- |
| **etichette negative** (stazioni senza evento dentro le strutture) | unico modo per stimare il FAR e la precisione | da fare |
| **archivio lungo termine delle predizioni** (~1,9 MB/giorno, ~700 MB/anno) | senza accumulo la numerosita' non cresce oltre l'archivio radar; il registro attuale ruota a 30 giorni | **rimandato il 24/10/2026 per decisione dell'utente** |
| **rianalisi ERA5 via CDS** (credenziali) per l'ambiente | sblocca Bunkers e MESH in replay; e' una **sorgente diversa** dall'analisi operativa, da dichiarare | da fare |
| **n=60 eventi** | POD con semi-larghezza 0,10 | **2030** al tasso 0,0382/giorno (proiezione) |
| **bootstrap a blocchi per giornata** | gli eventi si concentrano in poche giornate: i CI a casi indipendenti sono ottimistici | previsto dal protocollo [V] §4.3 |

---

## 7. Riproduzione dei comandi

Interpreti su questo host: `python` non e' su PATH. pytest/python stanno in
`C:\Users\aless\AppData\Local\Python\bin\python.exe` (CPython 3.14.7). La console e' cp1252: senza
`PYTHONIOENCODING=utf-8` i test con caratteri non-ASCII falliscono.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$py = "C:\Users\aless\AppData\Local\Python\bin\python.exe"
```

**1) Dataset etichettato.** Gia' pronto: **`validation-labels`** ->
`data/validation/labels.jsonl` (42.391 righe, 24.999.406 B, sha256
`5F8023F65E7D5393...B5DED2C2`), verificato in andata e ritorno il 2026-11-23 (scaricato dal branch;
byte, sha256, blob git e conteggio righe identici). Per ricostruirlo da zero serve la cache METAR,
**123.523.965 B** compressi in **23 file** (11 annuali + 12 mensili del 2022), scaricati da
`https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py`, rete `IT__ASOS`, campo `metar`,
`report_type=3` = METAR+SPECI **[V]** (i ~735 MB dei documenti sono la dimensione decompressa
stimata: la cache compressa misurata e' 123,5 MB):

```powershell
& $py scripts\validation\build_labels.py --start 2015-01-01 --end 2026-10-24 `
    --cache-dir <cache> `
    --out labels.jsonl --truth-json truth.json --report report.json
```

(`--cells-dir` e `--detections-geojson` restano opzionali: aggiungono l'accoppiamento con gli
snapshot radar. Il dataset pubblicato e' senza celle, `match_status=no_snapshot_in_window`.)

**2) Autotest del parser** (fixture METAR come formato, nessun risultato pubblicato; 20 controlli,
0 falliti il 2026-10-24) **[V]**:

```powershell
& $py scripts\validation\build_labels.py --selftest
```

**3) Numerosita' per il POD** (tabella 60/245/93/381) **[P]**:

```powershell
& $py scripts\validation\power_analysis.py --pod 0.8 0.5 --half-widths 0.10 0.05 --ratio 5
```

**4) Replay deterministico** (due run con lo stesso istante devono dare gli stessi byte; 7/7 file
in 1.2.4.21) **[R]**:

```powershell
& $py scripts\radar_engine\main.py --as-of 2026-07-01T13:25:00Z `
    --out-dir <dir_run1> --footprints <dir_run1>\footprints.geojson
& $py scripts\radar_engine\main.py --as-of 2026-07-01T13:25:00Z `
    --out-dir <dir_run2> --footprints <dir_run2>\footprints.geojson
Get-ChildItem <dir_run1> -File | ForEach-Object {
  (Get-FileHash $_.FullName -Algorithm SHA256).Hash -eq
  (Get-FileHash (Join-Path <dir_run2> $_.Name) -Algorithm SHA256).Hash
}
```

**5) Collaudo centroide** (1.2.4.20; produce la matrice 0/6/69) **[R]**:

```powershell
& $py scripts\validation\asof_bench.py `
    --labels <labels_metar_it_2015_2026.jsonl> `
    --work-dir <dir> --report <dir>\report.json --markdown <dir>\report.md
```

**6) Collaudo footprint + tasso base** (1.2.4.21; produce 5/1, il tasso base per run e la matrice con
Wilson) **[R]**:

```powershell
& $py scripts\radar_engine\main.py --as-of 2026-07-01T13:25:00Z `
    --out-dir <dir> --footprints <dir>\footprints.geojson
& $py scripts\validation\asof_bench_footprint.py `
    --labels <labels_metar_it_2015_2026.jsonl> `
    --italy-geojson data\geography\province_italiane.geojson `
    --work-dir <dir> --report <dir>\report_footprint.json `
    --markdown <dir>\report_footprint.md
```

**Nota sui dati.** `labels.jsonl` (**42.391 righe, 24.999.406 B**) **non sta su `main`**: dal
2026-11-23 e' pubblicato sul branch dati orfano **`validation-labels`**
(`data/validation/labels.jsonl`, `labels_meta.json`, `labels_report.json`, `README.md`), quindi si
puo' usare direttamente senza rigenerarlo. Il passo 1 resta il modo per ricostruirlo (e per
riottenere `truth.json`, che **non** e' pubblicato sul branch: si ottiene con `--truth-json`,
11.237.511 B). Il collaudo completo (6 eventi) e' stato eseguito **una volta sola**: rieseguirlo su
altri eventi e' un esperimento nuovo, non una ripetizione.

---

## 8. Riferimenti

- `docs/VALIDAZIONE_SEVERO.md` - licenze (ESWD verbatim), dataset METAR, potere statistico, protocollo.
- `docs/REPLAY_AS_OF.md` - contratto `--as-of`, cosa e' riproducibile, `null_reason`, determinismo,
  sezione 9 con footprint, tasso base e matrice 2x2.
- `scripts/validation/`: `build_labels.py` (43.121 B), `power_analysis.py` (5.686 B),
  `asof_bench.py` (21.273 B), `asof_bench_footprint.py` (32.572 B), `geo_match.py` (13.315 B),
  `supercell_validation.py` (66.521 B).
- `scripts/radar_engine/footprint.py` (24.141 B) - componente connessa poligonizzata.
- Blocco `VERSION` 1.2.4.21 (`NOTE`) - misure della release, incluse le 69 strutture (56 `strong` +
  13 `intense`) e la dichiarazione "nessuna taratura".
