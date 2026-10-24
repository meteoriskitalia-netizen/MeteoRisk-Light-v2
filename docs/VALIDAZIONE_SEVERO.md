# Validazione del layer severo: licenze, dataset etichettato, potere statistico, protocollo

Data di accesso a tutte le fonti citate: **24 ottobre 2026**.
Ambito: `scripts/validation/` (nuovi file) e questo documento. Nessun file esistente è stato modificato, nessun push.

---

## 0. Sintesi in tre righe

1. **ESWD non è utilizzabile** per questo scopo: la licenza consente l'uso non commerciale ma **vieta di pubblicare dati grezzi e di trasmetterli a terzi**, e i dati 2015–2026 si ottengono solo firmando un accordo e passando da un form. Un dataset di etichette committato in un repo pubblico sarebbe pubblicazione di dati ESWD (penale contrattuale 60.000 €). **Nessun dato ESWD è stato scaricato, nessun dataset ESWD è stato costruito.**
2. **La fonte di verità usata è il METAR/SPECI** delle stazioni italiane (dominio pubblico NOAA/NCEI, trasporto IEM): 7.272.677 bollettini 2015-01-01 → 2026-10-23, **42.391 eventi** (di cui **42.212 in verità** dopo il filtro QC), file `labels_metar_it_2015_2026.jsonl`.
3. **Oggi il POD/FAR del layer severo non è ancora calcolabile**, non per mancanza di etichette ma perché **non esiste un archivio storico delle celle**: il branch `radar-history` conserva **24-25 slot (≈4 ore)**. Le etichette ci sono; gli allarmi no. Il protocollo (§5) dice esattamente cosa manca e come colmarlo.

---

## 1. ESWD — termini d'uso, verbatim

### 1.1 Fonte primaria consultata

- Pagina: <https://www.essl.org/cms/european-severe-weather-database/> (consultata 2026-10-24)
- Accordo: <https://www.essl.org/cms/wp-content/uploads/20260922_ESWD-User-Agreement-for-Non-Commercial-Use.pdf>
  ("ESWD User Agreement for non-commercial use, Version: 22 September 2026", 2 pagine)
- Sito dati: <https://www.eswd.eu/> (versione applicativa 6.5.0)
- QC: <https://www.essl.org/cms/european-severe-weather-database/eswd-quality-control/>

### 1.2 Cosa dice la pagina ufficiale (verbatim)

> "You may use any data retrieved from the ESWD website according to the conditions stipulated in the User's Agreement. For instance, commercial use is not allowed. By using the ESWD data, you accept all conditions in this Agreement (even when not actually signing the Agreement and returning it to ESSL)."

> "**1. For academic or personal non-commercial use** — Please fill in the following User's Agreement, sign it, and return it as a PDF file via our application form here."

> "In the form you will be asked for what project you want to use the data, for what area, time period and event types you need data. **We will then work with you to find a solution for your needs. For use in larger, funded projects, we typically request a fee** as a contribution to our efforts collecting and managing the data."

> "**2. Other use** — Organizations can become a general user by paying a fee that depends on the size of your organization, or by becoming an institutional supporting member of ESSL."

Citazione richiesta: "Dotzek, N., P. Groenemeijer, B. Feuerstein, and A. M. Holzer, 2009a: Overview of ESSL's severe convective storms research using the European Severe Weather Database ESWD. Atmos. Res., 93, 575−586."

### 1.3 L'accordo, clausole che decidono (verbatim dal PDF)

> "**ii. The User may not use ESWD data commercially.**"

> "**iv. The User may not publish raw ESWD data.**"

> "**v. The User may not transmit any raw ESWD data to another party.**"

> "**vi. In addition to raw ESWD data, ii, iii, iv and v also apply to derived works to which little scientific value has been added by the User, this to be judged by ESSL.**"

> "**vii. ESSL may grant exceptions to points ii, iii, iv, and v to the User. Such exceptions are only provided a priori and in writing.**"

> "**viii. Derived works may be presented in a scientific context (like a formal scientific publication or a contribution to a scientific conference) by the User, provided that the following source is in a well-visible and readable way: Source: European Severe Weather Database, ESSL (eswd.eu).**"

> "**ix. The User must ensure that it is not possible that any third party can recreate the original data from the derived product with reasonable accuracy.** In case of a formal scientific publication, the User must provide a reference to Dotzek et al. (2009) both in the text and the reference section."

> "**xi. In case of violation of one or more of points i, ii, iii, iv, v, or viii, the User shall pay to ESSL a penalty corresponding to either 1) € 60,000, 2) five times the annual fee for an Institutional Supporting Member, or 3) the amount of actual damages caused to ESSL, whichever is higher.**"

> "**iii. The User may use the ESWD data only for:** __________ (name of project or description of use)"

Sui livelli di qualità: "QC1 is the most important quality check level. It means that the report is confirmed by a reliable source." · "For statistical studies we suggest to take QC1 as a lower bound."

### 1.4 Verdetto sulla licenza ESWD

**La licenza consente esplicitamente l'uso non commerciale con termini pubblici e verificabili** (il PDF è pubblico, datato, versionato): questo punto del requisito è soddisfatto *sulla carta*.

**Ma il dataset non è costruibile né pubblicabile in questo progetto**, per tre motivi indipendenti e ognuno sufficiente:

| # | Motivo | Clausola / fatto |
| :-- | :--- | :--- |
| 1 | **Obbligo di accordo firmato + form + eventuale fee** per ottenere i dati 2015-2026. Il sito pubblico come ospite mostra solo "recent events" ("As a guest you can only browse recent events"), non una serie storica nazionale 2015-2026. | pagina ufficiale §1.2; accordo punto 1.iii (progetto da dichiarare) |
| 2 | **Divieto di pubblicazione e trasmissione**: il deliverable richiesto è un file di etichette geolocalizzate committato in un repo pubblico — cioè pubblicazione e trasmissione a terzi di un derivato che ESSL stessa può giudicare "poco valore scientifico aggiunto" (punto vi). Con `lat/lon/ts/type` un terzo ricostruisce il record ESWD, il che viola anche il punto ix. | accordo 1.iv, 1.v, 1.vi, 1.ix |
| 3 | **Sanzione** in caso di violazione dei punti i-iv-v: fino a 60.000 € o 5× la fee istituzionale. Non è un rischio da assumere per un progetto che pubblica tutto su GitHub Pages. | accordo 1.xi |

Il punto 1.vii permette eccezioni, ma **solo "a priori e per iscritto"**: un'eccezione non richiesta non esiste. **Quindi: fermato qui, come da regola.** Nessuna richiesta HTTP a `eswd.eu` ha prelevato record; le uniche chiamate sono state sonde di schema (`/api/...` → 404) e la pagina come ospite.

### 1.5 Accesso tecnico ESWD (documentato, non usato)

| Aspetto | Esito verificato |
| :--- | :--- |
| Endpoint pubblico senza credenziali | **No.** `https://www.eswd.eu/api/events`, `/api/v1/events`, `/en/api/events?country=IT` → **HTTP 404** (2026-10-24). La radice `https://www.eswd.eu/en/` risponde **HTTP 308** verso un redirect che il client usato non ha risolto. |
| Accesso come ospite | UI web, solo eventi recenti (dichiarato dal sito). |
| Bulk / serie storica | Solo via accordo firmato + form Wix (`forms.wix.com/r/7038770502055231719`), con area, periodo e tipi dichiarati; fee per progetti finanziati. |
| Formato | Specifica pubblica: "ESWD Data Format Specification" (`ESWD-data-format-specification_03062026rev1.pdf`), non verificata nel contenuto (non serviva, dato il verdetto). |
| Dimensione Italia 2015-2026 | **NON VERIFICATA** (richiederebbe i dati). Riferimento esterno non verificato in questa sessione: la climatologia di grandine grande su ESWD riporta ~2.447 report per l'Italia 2000-2020 (Púčik et al., `egusphere-2023-176`) — **numero citato, non ricontrollato qui**. |

---

## 2. Alternative verificate

Regola applicata: licenza **esplicita, pubblica, verificabile** che consenta l'uso non commerciale e la redistribuzione. Fonti commerciali escluse per mandato (Vaisala, Earth Networks, Xweather/Aeris, Infoplaza/SAT24): non sono state valutate.

| Fonte | Licenza (verbatim dove utile) | Copertura | Tipo di evento | Granularità | Usabile? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **METAR/SPECI — NOAA/NCEI ISD "Global Hourly"**, trasporto IEM | "The information on National Weather Service (NWS) Web pages are in the public domain, unless specifically noted otherwise, and may be used without charge for any lawful purpose" (<https://www.weather.gov/disclaimer>, 2026-10-24); opere del governo USA non coperte da copyright (17 U.S.C. §105). **Nessun testo di licenza specifico trovato sulla pagina del dataset ISD/NODD** (dichiarato, §6). | 2015-2026, 99 stazioni italiane con dati (108 in rete) | grandine (GR/GS), tornado/tromba marina (+FC), imbuto (FC/VCFC), raffica ≥ 25 m/s, temporale | **punto**: lat/lon della stazione, data/ora al minuto | **SÌ — usata** |
| **ARPA FVG — OSMER, rete hailpads** | "gli altri contenuti del sito sono rilasciati sotto licenza Creative Commons, attribuzione 3.0 Italia (CC BY-SA 3.0 IT) e possono pertanto essere riprodotti, distribuiti, comunicati, esposti, rappresentati, modificati e usati per fini commerciali, rispettando però … attribuzione … citando la fonte come "ARPA FVG - OSMER e GRN" e l'url http://www.meteo.fvg.it/ ; stessa licenza …; divieto di restrizioni aggiuntive" (<https://www.osmer.fvg.it/note-legali.php>, 2026-10-24) | **1988-2016** per l'archivio pubblico: il selettore si ferma al 2016 e le richieste per 2017 e 2020 restituiscono **0 righe** (verificato) | grandine (diametro, densità di impatto, energia cinetica) | **giorno × area** (macroaree 1-4), **senza coordinate** e **senza ora** | **Parzialmente**: solo 2015-2016 nella finestra richiesta, e solo come verità *di area per giornata* |
| **ARPA Piemonte — Banca Dati Eventi (BDE)** | "La Regione Piemonte, Arpa Piemonte e la Città Metropolitana di Torino autorizzano la libera e gratuita consultazione, estrazione, riproduzione e modifica dei dati in essa contenuti da parte di chiunque vi abbia interesse per qualunque fine, purché nel rispetto dei termini della licenza Creative Commons Attribuzione CCBY 4.0" (metadato GeoNetwork `arlpa_to:07.04.06`, 2026-10-24) | Piemonte, serie storica lunga | **geo-idrologico**: "segnalazioni georiferite ciascuna relativa a processi ascrivibili a fenomeni di natura geo-idrologica in Piemonte (frane, processi fluviotorrentizi ecc.)" | punto georiferito + data | **NO**: fenomeno sbagliato (frane/alluvioni, non grandine/vento/tornado) |
| **Zenodo 15008662 — "Tracked Hailstorm Events from Radar Data"** | CC BY 4.0 (record Zenodo, 2026-10-24) | Alberta, Canada | grandine derivata da radar (max_VIL) | traccia temporale con lat/lon | **NO**: geografia sbagliata, e non è una verità osservata ma un prodotto radar |
| **Protezione Civile — Annali meteo-idro** | Non verificato | Italia | eventi severi narrati | PDF, nessun lat/lon/timestamp di evento | **NO** (non machine-readable) |
| **ESWD derivati con DOI** (es. climatologie Púčik et al.) | Ereditano i vincoli ESWD (nessuna licenza aperta sul dato) | Europa | grandine/vento/tornado | vario | **NO**: il vincolo "no pubblicazione di dati grezzi" si trasferisce al derivato |

**Conclusione §2.** Non esiste, per l'Italia 2015-2026, una fonte di verità *event-level* (lat/lon + data/ora) con licenza aperta per la convezione severa. L'unica fonte con licenza esplicita e granularietà di evento è il **METAR/SPECI**, che è però una rete di **99 punti** (aeroporti e osservatori d'alta quota), non una rete di rilevamento di grandine. Il dataset costruito va letto con questo vincolo, che è il suo limite principale e non una nota a piè di pagina.

---

## 3. Il dataset costruito

### 3.1 File prodotti

Nel workspace di lavoro (`C:\Users\aless\AppData\Local\Temp\opencode\eswd\`):

| File | Contenuto | Dimensione |
| :--- | :--- | ---: |
| `labels_metar_it_2015_2026.jsonl` | 42.391 righe, schema richiesto + campi di servizio | 24.999.406 B |
| `truth_metar_it_2015_2026.json` | verità nello schema di `supercell_validation.py` (42.212 eventi, `null_events: []`) | — |
| `labels_report.json` | report di costruzione (conteggi, QC, copertura, qualità del dato) | — |
| `detections_radar_history.geojson` | i 24 slot radar reali riscritti come allarmi (1.385 celle) | — |
| `cells/radar_history/*.json` | 24 snapshot reali scaricati dal branch `radar-history` | 320.982 B |
| `cache/metar_it_*.csv.gz` | cache dei bollettini (23 file: 11 annuali + 12 mensili per il 2022, che ha fallito come richiesta annuale) | ≈ 735 MB |

Il dataset **non** viene committato nel repo (22,7 MB di dati derivati non sono un file di codice); il repo riceve gli **script** che lo ricostruiscono.

### 3.2 Schema di una riga

```json
{"event_id":"metar:LIME:20191106T0450:hail","ts_utc":"2019-11-06T04:50:00Z",
 "lat":45.6689,"lon":9.7003,"type":"hail","severity":"large_hail_ge5mm",
 "source":"metar_isd:LIME","qc":"B","distance_km":null,"matched_cell_id":null,
 "matched_ts":null,"match_status":"no_snapshot_in_window","convective_context":true,
 "station":"LIME","vicinity":false,"detail":"TSGRRA","severity_value":null,
 "ts_uncertainty_min":5.0,"sampling_gap_min":60.0,"merged_count":1,
 "raw":"LIME 060450Z 20005KT 150V230 4000 TSGRRA BR SCT030CB BKN050 09/08 Q1000 NOSIG"}
```

(riga reale del dataset, non un esempio: temporale con grandine a Bergamo-Orio al Serio, 6 novembre 2019, 04:50 UTC)

Campi richiesti: `event_id, ts_utc, lat, lon, type, severity, source, qc, distance_km, matched_cell_id, matched_ts`.
Campi di servizio aggiunti: `match_status, convective_context, station, vicinity, detail, severity_value, ts_uncertainty_min, sampling_gap_min, merged_count, raw` (il bollettino originale: ogni etichetta è ricontrollabile).

### 3.3 Regole di classificazione (dichiarate)

| `type` | Regola | `severity` |
| :--- | :--- | :--- |
| `hail` | token di tempo presente contenente `GR` (grandine, WMO ≥ 5 mm) o `GS` (small hail / **snow pellets**, < 5 mm) | `large_hail_ge5mm` / `small_hail_lt5mm` |
| `tornado` | `+FC` (tornado o tromba marina) | `tornado_or_waterspout` |
| `funnel_cloud` | `FC`, `VCFC` | `funnel_cloud` |
| `severe_wind` | raffica ≥ **25,0 m/s** (soglia WMO di raffica severa) oppure `SQ` | `severe_gust` / `squall` |
| `thunderstorm` | `TS` (con o senza intensità) | `thunderstorm` |

- `VC` (vicinity) non viene scartato: viene **marcato** (`vicinity: true`) e contato a parte (indica fenomeno a 8-16 km dalla stazione, quindi posizione meno precisa).
- **Fusione**: osservazioni consecutive dello stesso tipo alla stessa stazione entro **90 minuti** diventano un evento (`merged_count`). Scelta: 90 min copre una sequenza di bollettini orari dello stesso temporale; costo dichiarato: due temporali distinti a 1,5 h sullo stesso aeroporto vengono fusi. Sensibilità misurata sul periodo 2015-2019: **28.278** righe con merge 30 min → **19.743** con merge 90 (grandine **295 → 274**, −7%; temporale **26.239 → 18.283**, −30%). Il merge a 30 min è quasi un no-op sui bollettini di routine, che distano 60 minuti.
- **Tempo: preciso, il problema è il campionamento.** Il tempo presente di un METAR (`GR`, `TS`, `FC`) è il fenomeno **osservato all'ora del bollettino**: `ts_utc` è preciso al minuto (`ts_uncertainty_min = 5`), non all'ora. Il limite vero è che fra due bollettini di routine passano ~60 minuti (`sampling_gap_min`): un fenomeno breve che inizia e finisce in mezzo **non esiste nel dataset**. Questo abbassa il POD (un evento non osservato non può essere rilevato), non sposta i timestamp. La finestra di matching resta ±10 min come richiesto, ed è più larga dell'incertezza del timestamp.

### 3.4 Filtro di qualità applicato (e contato)

Scala **propria**, non ESWD: `A` = verificato a mano / ente con QC dichiarato · `B` = osservazione strumentale ufficiale · `C` = derivato o non plausibile.

Regola misurata e applicata: **`qc=C` solo per la grandine senza temporale (`TS`) entro 60 min allo stesso aeroporto**, perché `GS` significa per WMO "small hail **or snow pellets**" e un `GR` a −1 °C non è grandine convettiva. Prova concreta dal dataset: l'unico evento del 2026-10-23 è
`LIVP 231655Z AUTO 17012KT 0050 GR FG M01/M01` → grandine a **−1 °C** con nebbia: neve tonda, marcata `qc=C`, esclusa dalla verità.

- `qc=C`: **179** righe (tutte `hail`), su 488. Restano nel JSONL, non nella verità.
- Concentrazione delle 179: gennaio 36, febbraio 28, dicembre 16, marzo 13 (e 20 in giugno, 13 in luglio: grandine reale tra due bollettini, non grandine "finta"). Il filtro non è neutro: **toglie anche grandine estiva vera**; per questo il numero è dichiarato e la regola è ispezionabile.
- Verità risultante: **42.212 eventi** (hail 309, severe_wind 2.329, thunderstorm 39.494, funnel_cloud 76, tornado 4).

### 3.5 Numerosità reale (misurata, non stimata)

7.272.677 bollettini letti · 99 stazioni · 3.018 giornate con almeno un evento · **14,05 eventi/giorno-evento**.
3 bollettini non interpretabili su 7,27 M (0,00004%).

| type | eventi (merge 90) | in verità | osservazioni grezze | giorni con ≥1 evento | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026* |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| hail | 488 | **309** | 518 | 261 | 26 | 25 | 44 | 36 | 48 | 20 | 30 | 10 | 19 | 26 | 13 | 12 |
| severe_wind | 2.329 | 2.329 | 3.383 | 749 | 238 | 176 | 314 | 192 | 231 | 215 | 208 | 266 | 231 | 147 | 79 | 32 |
| thunderstorm | 39.494 | 39.494 | 56.224 | 2.767 | 3.255 | 3.622 | 2.773 | 4.801 | 3.832 | 3.243 | 3.051 | 3.142 | 3.285 | 3.194 | 2.894 | 2.402 |
| funnel_cloud | 76 | 76 | 79 | 73 | 6 | 3 | 8 | 7 | 10 | 6 | 8 | 5 | 5 | 6 | 10 | 2 |
| tornado | 4 | 4 | 4 | 4 | – | – | – | 1 | – | – | – | 2 | – | – | 1 | – |
| **totale** | **42.391** | **42.212** | 60.208 | 3.018 | | | | | | | | | | | | |

\* 2026 è **parziale**: il file copre fino al 2026-10-23 (482.684 bollettini contro ~660.000 di un anno intero).

Distribuzione mensile della grandine in verità: gen 20, feb 13, mar 14, apr 26, mag 37, **giu 46, lug 47, ago 49**, set 15, ott 15, nov 8, dic 19 → massimo in estate, come atteso, con una coda invernale residua (grandine d'alta quota: LIMH, Pian Rosa 3.480 m, contribuisce con **51 dei 309** eventi, tutti maggio-settembre).

### 3.6 Matching con le celle: esito e perché

`match_events()` produce tre esiti distinti, mai collassati in `null`:
`matched` (cella entro 10 km) · `no_cell_within_gate` (**snapshot presenti**, nessuna cella entro il gate → è un MISS) · `no_snapshot_in_window` (**dato mancante**, non un miss).

Risultato sul dataset completo: **42.391 / 42.391 = `no_snapshot_in_window`**. Motivo verificato: gli unici snapshot disponibili sono 24 slot del 2026-10-23 19:00-22:50Z, mentre gli eventi coprono 11,8 anni. **Non è un fallimento dello script: è l'assenza dell'archivio.**

### 3.7 Due difetti del dato misurati (e dichiarati)

1. **La serie `severe_wind` non è omogenea.** Frazione di bollettini con un gruppo di raffica: 2015 2,21% · 2016 2,44% · 2017 2,92% · 2018 2,17% · 2019 2,48% · 2020 2,13% · 2021 2,21% · 2022 2,05% · 2023 2,31% · 2024 2,11% · **2025 1,18%** · 2026 1,44%. Il crollo degli eventi vento nel 2025 (79 contro 231 del 2023) è **un calo di segnalazione, non di fenomeni**. Qualunque POD/FAR sul vento va calcolato per anno o con questa cautela.
2. **La verità è una rete di 99 punti, non un piano.** Un evento a 3 km da un aeroporto è invisibile; una grandinata a 15 km da una stazione non entra nel dataset. Le 309 grandini sono un **minimo osservato**, non la frequenza italiana.

---

## 4. Potere statistico: quanti eventi servono

### 4.1 Formula (Wilson, 1927)

Con `p` proporzione, `n` numerosità, `z = 1,959964` (95%):

```
centro(n)       = (p + z²/(2n)) / (1 + z²/n)
semilarghezza(n)= z/(1 + z²/n) · √( p(1−p)/n + z²/(4n²) )
CI(n)           = [centro − semilarghezza, centro + semilarghezza] ∩ [0,1]
```

Wilson e non Wald perché a `n` piccolo e `p` vicino a 1 Wald esce da [0,1] e sottostima la copertura.

Calcolo esplicito per **POD 0,8 con semi-larghezza 0,10**, `n = 60`:

```
centro = (0,8 + 3,8415/(2·60)) / (1 + 3,8415/60) = 0,781948
semi   = 1,959964/(1+3,8415/60) · √(0,8·0,2/60 + 3,8415/(4·3600))
       = 0,031948 · √(0,0026667 + 0,0026677) = 0,099767   → ≤ 0,10  ✔
CI     = [0,6822, 0,8817]
```

### 4.2 Tabella (riproducibile: `python scripts/validation/power_analysis.py`)

`n` = eventi **severi** (denominatore del POD). Rapporto minimo dichiarato **1:5** casi severi : non severi.

| POD | semi-larghezza | **n richiesto** | CI Wilson a quel n | non severi (×5) | allarmi totali |
| ---: | ---: | ---: | :--- | ---: | ---: |
| 0,80 | **0,10** | **60** | [0,6822, 0,8817] | 300 | 360 |
| 0,80 | **0,05** | **245** | [0,7455, 0,8453] | 1.225 | 1.470 |
| 0,50 (caso peggiore) | 0,10 | 93 | [0,4004, 0,5996] | 465 | 558 |
| 0,50 (caso peggiore) | 0,05 | 381 | [0,4500, 0,5500] | 1.905 | 2.286 |

`p = 0,5` è il valore che massimizza la semi-larghezza: la riga 0,50 copre **qualunque** POD reale. Chi vuole essere conservativo usa quella.

### 4.3 Confronto con il dataset

| Tipo | eventi in verità (11,8 anni) | vs n(±0,10)=60 | vs n(±0,05)=245 |
| :--- | ---: | :--- | :--- |
| hail | 309 | **abbastanza (5×)** | **abbastanza (1,26×)** |
| severe_wind | 2.329 | abbondante | abbondante, **ma serie non omogenea** (§3.7) |
| thunderstorm | 39.494 | abbondante | abbondante |
| funnel_cloud | 76 | al limite (1,27×) | no (0,31×) |
| tornado | 4 | **no** (0,07×) | no |

**Il vincolo vero non è il numero di eventi, è il numero di GIORNATE indipendenti.** Gli eventi si concentrano in poche giornate convettive: 309 grandini in **261 giorni** (1,18 per giorno), 2.329 vento in 749 giorni (3,1 per giorno). La numerosità efficace non è `n` ma ≈ `numero di giorni`: per la grandine 261 ≥ 245 → il POD a ±0,05 resta raggiungibile, ma con un bootstrap **a blocchi per giornata**, non a casi indipendenti. Per il vento 2.329 eventi su 749 giorni: il CI a casi indipendenti è ottimistico di un fattore ≈ √3.

### 4.4 Stratificazione per distanza

Il POD dipende dalla distanza evento-cella, quindi la tabella va data per fasce, e la numerosità va verificata **dentro ogni fascia** (altrimenti una fascia con 8 eventi produce un CI inutile). Proposta di fasce e numerosità minima perché la fascia sia riportabile:

| fascia (distanza evento → centroide cella) | n minimo per ±0,10 | n minimo per ±0,05 | note |
| :--- | ---: | ---: | :--- |
| 0-2 km | 60 | 245 | "cella sopra l'evento": la fascia che conta per il POD |
| 2-10 km | 60 | 245 | entro il gate richiesto |
| 10-20 km | 60 | 245 | **fuori** dal gate di 10 km: serve solo a misurare quanto il gate taglia |
| nessuna cella nella finestra | – | – | è il `no_snapshot_in_window`: **non è una fascia di errore** |

Con 309 grandini totali, **nessuna fascia raggiunge 245** dopo la ripartizione: la stratificazione per distanza è calcolabile a ±0,10 solo per il dato aggregato, non per fascia. Va dichiarato prima di produrre la tabella, non dopo averla vista.

---

## 5. Protocollo di valutazione

Vale per `scripts/validation/supercell_validation.py` (POD/FAR/CSI/HSS/bootstrap già implementati) + il dataset di §3.

### 5.1 Split temporale (mai casuale)

| Blocco | Anni | Uso |
| :--- | :--- | :--- |
| **Sviluppo / congelamento** | 2015-2021 | si fissano le soglie e si ispeziona il comportamento. Nessuna metrica riportata come risultato. |
| **Test** | **2022-2026** | valutazione unica, dopo il congelamento. 2026 è parziale (fino al 23/10). |
| **Hold-out finale** | nessuno | non esiste: 11,8 anni sono tutti usati. Dichiarato: un secondo giro di tuning sul test lo rende un set di sviluppo. |

Lo split è **per anno**, non per evento: mescolare eventi dello stesso giorno fra sviluppo e test è la fuga di informazione più facile da commettere qui (una stessa linea temporale produce decine di righe).

### 5.2 Soglie congelate PRIMA

Le soglie attuali del layer (`overhang.core_dbz 45`, `strong_core_dbz 50`, `mesh` = formula Witt, `bunkers_deviation_ms 7.5`) sono **già** scelte ingegneristiche, non tarate su un test set. Il congelamento consiste nel **non toccarle** dopo aver visto le metriche del 2022-2026. Se si vuole tarare, si tara sul 2015-2021 e si dichiara quale parametro è stato mosso e con quale criterio.

### 5.3 Unità della tabella 2x2 — il punto che decide tutto

Numeri misurati che rendono obbligatoria questa sezione:
- celle per slot radar: **1.385 / 24 = 57,7** (misurato sui 24 slot reali);
- eventi severi osservati: 2.897 in 11,8 anni ≈ **0,028 / ora** (grandine+vento+tornado+imbuto);
- quindi, se l'allarme fosse "esiste una cella", gli allarmi sarebbero ~350 per ora contro 0,028 eventi/ora: **FAR ≈ 0,9999 e CSI ≈ 0** per costruzione, non per demerito dell'algoritmo.

Regola conseguente, da applicare **prima** di ogni altra cosa: **l'unità non è la cella.** Due definizioni ammesse:

**(A) Valutazione per evento (consigliata, è quella che il banco implementa).** L'unità è l'evento osservato: positivo = almeno un allarme entro 10 km e ±10 min; negativo = evento osservato senza allarme. `null_events` = campione di situazioni in cui l'evento **non** c'era (vedi 5.4). POD e FAR si calcolano così.

**(B) Valutazione cell-hour (solo se si vuole il FAR "operativo").** L'unità è la cella-ora: positivo = cella-ora che contiene ≥1 report entro 10 km. Richiede un archivio di celle (§5.7) e produce numeri non confrontabili con (A). Non mescolare le due.

### 5.4 Negativi: come si campionano (è qui che si compra o si perde la credibilità)

Tre pool, in ordine di difficoltà crescente. Il pool facile va **dichiarato** perché gonfia il CSI:

1. **Negativi facili (da NON usare da soli).** Giorni casuali aprile-settembre senza celle. Un layer severo non allarma mai: HSS e CSI diventano alti senza che l'algoritmo abbia fatto nulla. Se usati, riportarli in una riga separata.
2. **Negativi difficili (il pool corretto).** Celle che **esistevano** e hanno superato un gate di intensità (es. `soi ≥ organized`, MESH ≥ 25 mm, o `max_dbz ≥ 45`) nella stessa giornata e nella stessa regione di un evento, ma **senza** un report entro 10 km/±10 min. Rapporto **1:5** (§4.2) estratto con seed fisso. Questo pool misura l'errore che conta: "cella intensa, nessun evento".
3. **Negativi regionali da fonte ufficiale (controllo indipendente).** ARPA FVG hailpads: 2015 → **22** giornate con grandine, 2016 → **24** (misurato, tutta la regione, mesi 4-9). Le giornate 4-9 senza grandine in FVG sono il pool negativo **più difendibile** che esista nel panorama italiano con licenza aperta — ma copre solo il FVG e solo due anni.

**Vincolo obbligatorio:** un negativo deve essere **osservabile**. Una cella in un'ora in cui la rete METAR non ha bollettini (buco di osservazione) non è un negativo, è un dato mancante: va in `not_observable`, contato e riportato, mai nel denominatore del FAR. Vale la stessa logica di `match_status`.

### 5.5 Metriche e loro interpretazione

| Metrica | Formula / nota | Stato oggi |
| :--- | :--- | :--- |
| POD | `a/(a+c)` | calcolabile **appena esiste l'archivio celle** |
| FAR | `b/(a+b)` | idem, ma dipende dal pool di negativi (5.4) |
| CSI | `a/(a+b+c)` | idem; **non** confrontabile con ProbSevere (§5.6) |
| HSS | `(H−E)/(1−E)` | richiede `null_events` non vuoto |
| bias | `(a+b)/(a+c)` | 1,0 = neutro |
| CI | bootstrap 2.000 ricampionamenti, seed 12345 | nel banco; **da rifare a blocchi per giornata** (§4.3) |
| Brier / BSS / reliability | richiedono una probabilità | **non calcolabili**: `probability` è `null` per scelta, `rotation_measurable` è `false`. Il Brier si potrà calcolare solo se il layer pubblica una probabilità, e in quel caso il BSS va riferito a una climatologia di riferimento dichiarata (frequenza base per mese e regione), non a 0,5. |

Regola di reportistica: si pubblica **la tabella 2x2**, non solo le metriche. Un FAR senza `b` e senza il pool che lo ha generato non è un risultato.

### 5.6 Risultati attesi realistici (attese, non misure)

Riferimento esterno: **ProbSevere v2** (Cintineo et al. 2020, *WAF* 35, 1523-1543, doi:10.1175/WAF-D-19-0242.1) riporta **CSI ≈ 0,34**. Il numero 0,34 viene dal mandato di questa attività e **non è stato ricontrollato** sul paper in questa sessione (dichiarato, §6).

Perché **non** ci si deve aspettare 0,34 qui, con tre argomenti indipendenti:

1. **Verità diversa.** ProbSevere è validato contro storm reports NOAA (rete di segnalatori densa, decine di migliaia di report/anno); qui la verità sono 99 punti con 26 grandini/anno. A parità di algoritmo, meno verità = meno hit = POD e CSI più bassi.
2. **Gate sul centroide.** Il gate è sulla **distanza dal centroide** della cella, non dal suo poligono: una cella di 500-16.000 km² può coprire la stazione con il centroide a 10-20 km. Il dataset reale mostra aree da 57 a 16.439 km² (24 slot). Un gate di 10 km sul centroide **taglia hit veri**: il POD misurato è una stima per difetto, non una proprietà dell'algoritmo.
3. **Etichetta puntuale vs cella di area.** L'evento è un punto (la stazione); l'allarme è un'area. Il confronto punto-area è strutturalmente sfavorevole e va dichiarato nel report, altrimenti si confrontano due cose diverse.

Attesa dichiarata e falsificabile, **da verificare e non da citare come risultato**: con questo dataset, POD sulla grandine nell'ordine di **0,1-0,3** e CSI **0,05-0,15**; per il vento valori più alti ma su una serie non omogenea. Se esce molto più alto, la prima ipotesi da escludere è una fuga di informazione (stesso giorno in sviluppo e test, oppure negativi facili). **Nessuno di questi numeri è stato misurato**: sono l'ordine di grandezza atteso, scritto prima di misurare, così che sbagliare si veda.

### 5.7 Ciò che blocca la valutazione oggi (misurato)

| # | Blocco | Evidenza |
| :-- | :--- | :--- |
| 1 | **Nessun archivio storico delle celle.** Il branch `radar-history` contiene `index.json` con `"window_slots": 25` e 25 file slot (uno solo 404 al momento del download: 24 scaricati). Copertura ≈ **4 ore**. | `data/radar/history/index.json` sul branch, 2026-10-24 |
| 2 | Nessun `null_events` campionato → `HSS = null` | `truth_*.json` ha `null_events: []` |
| 3 | Nessuna probabilità → niente Brier/BSS | `severe_stack` pubblica `probability: null` |
| 4 | La verità non ha geometria di cella → gate sul centroide | gli slot pubblicano `lonlat` + `area_km2`, non il poligono |
| 5 | ESWD non utilizzabile | §1.4 |
| 6 | FVG hailpads: archivio pubblico fermo al 2016 | richieste 2017/2020 → 0 righe |

**Rimedio minimo, in ordine di costo:** (a) archiviare gli slot di `radar-history` senza finestra scorrevole (append su un branch dedicato: 13,4 KB per slot × 144 slot/giorno = **~1,9 MB/giorno, ~700 MB/anno** ai 57,7 celle/slot attuali); (b) campionare i negativi difficili con seed fisso; (c) pubblicare il poligono della cella (o almeno `area_km2` + forma) per sostituire il gate sul centroide con un gate area-evento. Senza (a), POD/FAR restano non calcolabili qualunque cosa si scriva nel resto del documento.

---

## 6. Cosa NON è stato verificato / non è stato fatto

1. **Licenza specifica del dataset ISD**: la pagina ISD/NODD **non riporta un testo di licenza**. La base giuridica usata è (i) il disclaimer NWS, che dichiara il pubblico dominio per i dati NOAA/NWS, e (ii) 17 U.S.C. §105 per l'opera del governo USA. Il METAR italiano è però prodotto da stazioni italiane e scambiato in ambito WMO: **non ho verificato** la policy WMO vigente sul riuso dei messaggi di scambio. Questo è il punto più debole della catena di licenza del dataset e va chiuso prima di una pubblicazione.
2. **ESWD**: nessun dato scaricato, nessuna dimensione Italia 2015-2026 misurata, formato non ispezionato. Verdetto basato sui termini pubblici, non su un rifiuto dell'ente.
3. **ProbSevere CSI 0,34**: numero del mandato, non ricontrollato sul paper.
4. **Protezione Civile / Annali meteo-idro**: non verificati (probabilmente non machine-readable, ma non l'ho dimostrato). La riga della tabella §2 dice "non verificato".
5. **Matching su celle reali**: mai eseguito su un evento reale. L'unica verifica end-to-end è uno **smoke test di schema**: verità completa (42.212 eventi) contro i 24 slot del 2026-10-23 → il banco gira, accetta il file e produce `hits=0, misses=42212, false_alarms=1385, correct_negatives=0` (HSS −0,066). È la prova che lo schema è compatibile, **non** una misura: nessuno dei 42.212 eventi cade nelle ~4 ore di archivio, quindi tutti finiscono in `miss` e tutti gli allarmi in `false_alarms`. **Nessuna metrica del layer severo è stata prodotta.**
6. **`vicinity`**: 36 dei 76 `funnel_cloud` e 7.111 dei 39.494 `thunderstorm` sono `VC` (fenomeno a 8-16 km). Non ho stimato l'effetto sul matching: sono marcati, non corretti.
7. **`sampling_gap_min`**: dichiarato (60 min fra bollettini di routine), non corretto. Quanti eventi brevi sfuggono fra due report **non è quantificabile con questi dati** (servirebbe una fonte a più alta frequenza): l'effetto sul POD è reale ma di dimensione ignota.
8. **ShellCheck / bash**: non applicabile (nessuno script shell toccato).
9. **Nessun push, nessun commit.** Solo file nuovi in `scripts/validation/` e questo documento.

---

## 7. Riproduzione

```powershell
$py = "C:\Users\aless\AppData\Local\Python\bin\python.exe"

# 1) cache METAR (una volta; ~735 MB in <cache>)
#    endpoint: https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py
#    rete IT__ASOS, campo "metar", report_type=3 (METAR+SPECI)

# 2) dataset + verità + report
& $py scripts\validation\build_labels.py --start 2015-01-01 --end 2026-10-24 `
    --cache-dir <cache> --cells-dir <snapshot radar-history> `
    --out labels.jsonl --truth-json truth.json --report report.json

# 3) numerosità per il POD
& $py scripts\validation\power_analysis.py --pod 0.8 0.5 --half-widths 0.10 0.05 --ratio 5

# 4) autotest del parser (fixture METAR, nessun risultato pubblicato)
& $py scripts\validation\build_labels.py --selftest

# 5) banco di validazione (quando esiste l'archivio celle)
& $py scripts\validation\supercell_validation.py --detected <celle.json> `
    --truth truth.json --out report_val.json `
    --max-distance-km 10 --pre-window-min 10 --post-window-min 10
```

Autotest: 20 controlli, **0 falliti** (2026-10-24). `py_compile` su entrambi gli script: OK.
