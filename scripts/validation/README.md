# `scripts/validation` — banco di validazione del rilevamento convettivo

Strumento **event-based** per rispondere con numeri difendibili alla domanda
"quanto e' bravo l'algoritmo?", secondo le convenzioni della letteratura
operativa (NSSL/DWD): tabella di contingenza 2x2, POD/FAR/CSI/HSS/bias,
bootstrap, stratificazione e classificazione dei MISS.

Il pacchetto e' **autonomo**: unica dipendenza `numpy` (nessuna dipendenza
nuova), nessun import da `scripts/radar_engine` e nessuna scrittura sui suoi
output. Gli output esistenti vengono **letti** dagli adattatori.

| file | contenuto |
| :--- | :--- |
| `supercell_validation.py` | modulo: matching, metriche, bootstrap, stratificazione, CLI, adattatori |
| `truth_template.json` | template della verita' etichettata con 3 esempi **fittizi** (esclusi con `--exclude-examples`) |
| `__init__.py` | pacchetto (`scripts` va su `sys.path`) |

Test: `scripts/tests/test_supercell_validation.py` (27 test, valori attesi
calcolati a mano nel test).

---

## 1) Uso rapido

```bash
python scripts/validation/supercell_validation.py \
  --detected data/radar/supercells.json \
  --truth scripts/validation/truth_template.json \
  --out data/validation/supercell_report.json \
  --max-distance-km 15 --bootstrap 2000 --exclude-examples
```

Opzioni principali:

| opzione | default | significato |
| :--- | :--- | :--- |
| `--detected` | — (obbligatorio) | allarmi: `supercells.json`, `storms.geojson`, JSON/CSV generico |
| `--truth` | — (obbligatorio) | verita' etichettata JSON o CSV |
| `--out` | — (obbligatorio) | report JSON |
| `--max-distance-km` | `15` | raggio di matching spaziale |
| `--pre-window-min` | `20` | quanto **prima** dell'evento un allarme e' valido |
| `--post-window-min` | `6` | quanto **dopo** l'evento un allarme e' valido ("un volume radar") |
| `--bootstrap` | `2000` | ricampionamenti per gli IC (`0` = disabilitato) |
| `--seed` | `12345` | seed del bootstrap: stesso seed = stessi IC |
| `--confidence` | `0.95` | livello degli intervalli |
| `--exclude-examples` | off | scarta gli eventi `example: true` |
| `--detected-format` | `auto` | `supercells` \| `storms` \| `events` |
| `--truth-format` | `auto` | `json` \| `csv` |

Codici di uscita: `0` successo, `1` errore di lettura/scrittura, `2` parametri
non validi. Il CLI stampa un riassunto leggibile e scrive il report JSON
(UTF-8 senza BOM, newline LF).

---

## 2) Schema della verita' etichettata

### JSON

```json
{
  "schema_version": "1.0",
  "_instructions": ["..."],
  "events": [
    {
      "id": "ESWD-2026-0123",
      "time_utc": "2026-05-12T14:20:00Z",
      "lon": 9.19,
      "lat": 45.46,
      "category": "supercell",
      "quality": "verified",
      "miss_reason": null,
      "source": "ESWD QC2 + rianalisi radar VMI",
      "radar_distance_km": 48.0,
      "example": false
    }
  ],
  "null_events": []
}
```

### CSV

Stesse colonne in intestazione, una riga per evento (`null_events` inclusi,
marcati `category=null`):

```csv
id,time_utc,lon,lat,category,quality,miss_reason,source,radar_distance_km,example
ESWD-2026-0123,2026-05-12T14:20:00Z,9.19,45.46,supercell,verified,,ESWD QC2 + radar,48.0,false
```

### Campi

| campo | obbligatorio | valori / note |
| :--- | :--- | :--- |
| `id` | si | univoco |
| `time_utc` | si | ISO 8601; `Z`, offset o naive (naive = UTC); ammesso epoch s/ms |
| `lon`, `lat` | si | gradi decimali WGS84 |
| `category` | no | `supercell`, `large_hail`, `tornado`, `severe_wind`, ... ; `null` = caso nullo |
| `quality` | no | `verified` (rianalisi + fonte indipendente/sopralluogo), `reported` (ESWD QC >= 1), `unverified` (report singolo) |
| `miss_reason` | no | `visible_but_missed` \| `not_visible_sampling` \| `bad_data` \| `null`; un valore diverso e' `ValueError` |
| `source` | no | provenienza dell'etichetta |
| `radar_distance_km` | no | distanza dal radar DPC piu' vicino; `null` -> secchio `unknown` |
| `example` | no | `true` solo per esempi fittizi |
| `is_null` | no | `true` per marcare un caso nullo dentro `events` |

Campi obbligatori mancanti -> errore esplicito con indice dell'evento. Campi
sconosciuti in un CSV vengono ignorati **con un avviso nel report**
(`warnings`), non in silenzio.

### Come si popola (in breve)

1. **ESWD QC >= 1** come base minima (supercelle, grandine grossa, tornado,
   raffiche convettive). Un report singolo non verificato (QC 0) non basta.
2. **Rianalisi radar** dei volumi VMI del giorno: ora al minuto e posizione
   dell'evento (danno/osservazione), non del centro della cella.
3. **Sopralluoghi e stime di danno** per categoria e qualita'.
4. **Casi nulli**: temporali ordinari etichettati `null` solo se la rianalisi
   conferma l'assenza di firma supercellulare. Senza casi nulli `CN = 0` e HSS
   non e' calcolabile (il report lo dichiara, non lo inventa).
5. Mai inventare eventi, orari o posizioni: la verita' incompleta produce FAR
   sovrastimati e POD non rappresentativi (il report ha un `disclaimer`).

Il template `truth_template.json` contiene 3 eventi **fittizi** marcati
`example: true` e le istruzioni complete (`_instructions`). Vanno sostituiti
con eventi reali; finche' non lo sono, si usa `--exclude-examples`.

---

## 3) Regola di matching (dichiarata, configurabile)

Un allarme e' associato a un evento se valgono **entrambe**:

* **spazio**: distanza great-circle `<= max_distance_km` (default 15 km);
* **tempo**: l'istante dell'allarme cade in
  `[t_evento - pre_window_min, t_evento + post_window_min]` (default
  `[-20, +6]` minuti, "un volume radar" NSSL).

Per allarmi con un intervallo di vita (track di `supercells.json` con
`first_seen`/`last_seen`) conta l'**intersezione** dell'intervallo con la
finestra: una track ancora viva dentro la finestra e' un rilevamento.

Assegnazione **uno-a-uno greedy per distanza minima**: tutte le coppie
candidate sono ordinate per distanza crescente (poi `|dt|`, poi gli id, per
determinismo) e accettate solo se ne' l'evento ne' l'allarme sono gia' usati.
Nessun doppio conteggio: i non accoppiati sono elencati nel report
(`misses.events`, `unmatched_detections.detections`,
`no_match_diagnostics`).

Ordine: prima gli eventi **reali**, poi i **casi nulli** con gli allarmi
residui (un allarme su un caso nullo e' un falso allarme; un caso nullo
rimasto e' un correct negative).

---

## 4) Metriche

| metrica | formula | lettura |
| :--- | :--- | :--- |
| POD | `a / (a + c)` | quota di eventi rilevati; meglio alto |
| FAR | `b / (a + b)` | quota di allarmi sbagliati; meglio basso |
| CSI | `a / (a + b + c)` | skill complessivo, penalizza sia miss sia FA |
| bias | `(a + b) / (a + c)` | frequenza relativa (F/O); 1.0 = neutro |
| HSS | `(H - E) / (1 - E)` | skill rispetto al caso |

con `a = hits`, `b = false_alarms`, `c = misses`, `d = correct_negatives`,
`N = a+b+c+d`, `H = (a+d)/N` (proporzione corretta) e
`E = [(a+b)(a+c) + (c+d)(b+d)] / N^2` (accordo casuale atteso nel 2x2).

**HSS non e' l'accuracy.** L'accuracy (`H`) con classi sbilanciate premia il
"non prevedere nulla"; HSS sottrae l'accordo casuale. Nel report c'e' anche
`accuracy`, ma solo per trasparenza: il test verifica esplicitamente che i due
valori differiscano (esempio 2x2 del test: accuracy 0.95, HSS 0.709).

**Intervalli di confidenza**: bootstrap non parametrico a livello di **caso**
(2000 ricampionamenti di default, seed fisso `12345` -> risultato
deterministico). Unita' di ricampionamento: hit, miss, FA, CN (un allarme non
associato e' un caso FA a se'). I replicati in cui la metrica non e' definita
(0/0) sono esclusi dal percentile e **contati** in `undefined_replicates`.

---

## 5) Stratificazione

| chiave | strati |
| :--- | :--- |
| `by_distance_band` | `<60`, `60-120`, `120-180`, `>180` km (+ `unknown`) |
| `by_month` | `YYYY-MM` dell'evento |
| `by_season` | `DJF`, `MAM`, `JJA`, `SON` |
| `by_category` | categoria dell'evento verita' |

**Attribuzione dichiarata**: hit/miss/CN vanno all'evento verita' (o caso
nullo), l'FA da caso nullo all'evento nullo, l'allarme non associato alla
propria fascia/mese/stagione (`radar_distance_km` dell'allarme, se presente).

**Limite dichiarato**: dentro `by_category` un allarme non associato non e'
attribuibile a una categoria di evento (le categorie sono lato verita'): finisce
nella chiave esplicita `unattributed_detections`. Costo: dentro una categoria
il FAR e' misurabile solo tramite casi nulli di quella categoria; il FAR
complessivo resta in `metrics`. Ogni strato riporta il proprio 2x2, senza IC
(campioni piccoli: gli IC sono sul totale, dove il campione lo consente).

---

## 6) Classificazione dei MISS (NSSL)

| `miss_reason` | significato |
| :--- | :--- |
| `visible_but_missed` | l'evento era visibile dal radar e l'algoritmo non l'ha rilevato |
| `not_visible_sampling` | non osservabile per campionamento (copertura, raggio, buco di scansione, volume mancante) |
| `bad_data` | dato radar degradato o inutilizzabile |

Un evento mancato senza `miss_reason` finisce in `unknown`: conteggiato in
`misses.by_reason` e dichiarato. Il report elenca ogni miss con i campi
originali (incluso `miss_reason_declared`).

---

## 7) Adattatori di input

### `supercells.json` (formato reale del radar engine)

Legge `candidates[]`: `supercell_id` -> id, `last_seen` -> istante
rappresentativo, `first_seen`/`last_seen` -> intervallo di vita, `position`
(`[lon, lat]`) -> coordinate, `track_type` (`cell`/`storm_object`) ->
categoria, `ssi`/`level`/`phase` -> `extras` (tracciabilita' degli allarmi non
associati). I metadati del payload (`generated_at`, `radar_timestamp`,
`summary`) finiscono in `inputs.detected.payload_meta`.

### `storms.geojson`

Legge `features[]`: `properties.cell_id` -> id, `properties.timestamp` (o
`timestamp_ms`) -> istante, `geometry.coordinates` (fallback
`properties.centroid_lonlat`) -> coordinate, `max_dbz`/`track_id` -> `extras`.
Sono **celle**, non supercelle: utile per validare il layer di rilevamento.

### Formato generico

Lista JSON (o oggetto `{"detections": [...]}`) e CSV con lo schema della
verita'. `--detected-format events`.

---

## 8) Struttura del report

```json
{
  "tool": {"name": "supercell_validation", "version": "1.0.0"},
  "generated_at": "...",
  "params": {"max_distance_km": 15.0, "pre_window_min": 20.0, "post_window_min": 6.0,
             "bootstrap": 2000, "seed": 12345, "confidence": 0.95, "...": "..."},
  "inputs": {"detected": {"path": "...", "sha256": "...", "count_loaded": 11,
                          "n_used": 11, "by_category": {"cell": 9}},
             "truth": {"path": "...", "n_used": 120, "n_null_cases": 40,
                       "by_quality": {"reported": 120}}},
  "contingency": {"hits": 0, "misses": 3, "false_alarms": 11,
                  "correct_negatives": 0, "n_cases": 14},
  "metrics": {"pod": 0.0, "far": 1.0, "csi": 0.0, "hss": 0.0, "bias": 3.67,
              "accuracy": 0.0, "counts": {}, "bootstrap": {"intervals": {}}},
  "stratified": {"by_distance_band": {}, "by_month": {}, "by_season": {},
                 "by_category": {}, "attribution": {}},
  "misses": {"count": 3, "by_reason": {}, "events": []},
  "unmatched_detections": {"count": 11, "by_category": {}, "detections": []},
  "matches": [], "null_matches": [], "no_match_diagnostics": {},
  "warnings": [], "disclaimer": "..."
}
```

Il report e' riproducibile: stessi input + stessi parametri + stesso seed ->
stessi numeri. `inputs.*.sha256` identifica i file usati.

---

## 9) Test

```bash
C:\Users\aless\AppData\Local\Python\bin\python.exe -m pytest \
  scripts\tests\test_supercell_validation.py -q
```

Copertura: 2x2 nota (POD 0.70, FAR 2/9, CSI 7/12, bias 0.9, HSS a mano),
estremi di HSS (perfetto 1, random 0), matching 1-a-1, finestra temporale ai
bordi (-25 miss, -20 hit, +6 hit, +7 miss), intervallo delle track, fasce di
distanza, stratificazione stagione/mese/categoria, determinismo del bootstrap,
esclusione degli esempi, adattatori (supercells/geojson/CSV) con voci
malformate, errori di schema, contratto del report (UTF-8 senza BOM, LF).

---

## 10) Limiti dichiarati

* Il radar DPC VMI e' a **singola riflettivita'** (nessun Doppler): non si
  validano mesocicloni o velocity couplet, solo la firma convettiva.
* Le metriche valgono **rispetto alla verita' fornita**: se la verita' e'
  incompleta i FAR sono sovrastimati. Non e' una validazione climatica.
* Gli strati hanno campioni piccoli: gli IC bootstrap sono sul totale.
* Il "matching 15 km" e' una convenzione operativa, non una verita' fisica:
  cambiare `--max-distance-km` cambia i numeri, e il valore usato e' scritto
  nel report.
