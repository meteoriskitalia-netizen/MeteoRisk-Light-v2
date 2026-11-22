# Validazione indipendente dei fronti sinottici

Stato: **eseguita il 2026-11-22, esito NEGATIVO**. `calibration.json` resta
`publication_status: development_only` con motivazione aggiornata (i numeri
misurati non reggono come evidenza di abilita': POD 0, CSI 0).

Niente push. Tutto misurato su file su disco e API pubbliche raggiungibili.

---

## 1. Perche' era `development_only` — la circolarita' (verificata, non ipotizzata)

- `work_13/fronts_engine/scripts/validation/score_calibration.py:74-77`:

  ```python
  if heavy_median > 0:
      lab = "positive" if hf > heavy_median else ("negative" if hf < heavy_median else None)
  else:
      lab = "positive" if hf > 0 else "negative"
  ```

- `hf` arriva da `calibrate.py:79-93` (`box_fractions`), che costruisce la
  frazione con `rain`/`showers`/`precipitation` **> 2 mm/h** — le stesse
  variabili che `score_calibration.py:39-50` (`heavy_pixels`) usa come
  "verita'" per decidere se un fronte e' un hit. L'etichetta e' quindi la
  stessa variabile del predittore: circolare, confermata riga per riga.

- `calibration.json` (fork `idswitch_real`, righe 4-6, 46-52, 81-87) dichiarava:
  CSI piatto 0,5 su ECMWF per fattori <= 1 (rilevatore saturato), CSI massimo
  0,1429 su DWD con 0 hit alle soglie finali, `informative: false`.

- `TODO_1.3.md:33`: servono ~97 eventi con verita' indipendente e un controllo
  negativo a +6 h.

---

## 2. Fonti indipendenti — cosa e' stato verificato (misurato)

| Fonte | Verifica eseguita | Esito |
| :--- | :--- | :--- |
| **METAR/SPECI (IEM, `IT__ASOS`)** | `GET https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?network=IT__ASOS&...&report_type=3`, 2026-11-18 → 11-22 | **HTTP 200**, 6.329 bollettini, **74 stazioni**, 2026-11-18T00:45Z → 2026-11-22T06:55Z. **312 eventi pioggia** con orario e posizione: 306 `RA`, 6 `SHRA`, 0 `TSRA`. **USATA come verita'.** |
| **SYNOP via IEM** | stessa API con `network=IT__SYNOP` | **HTTP 400** (rete non valida); il network list IEM non espone una rete SYNOP italiana equivalente. Non usata. |
| **AFA EUMETSAT (WMS anonimo)** | `GetMap` `mtg_fd:li_afa` su bbox Italia a `time=2026-11-19T17:00:00Z` | **HTTP 502** sul tempo storico richiesto. Inoltre: anche se raggiungibile, il numero massimo di casi e' il numero di frame frontali (**19 ECMWF + 14 DWD**, di cui 11 + 13 con copertura osservativa), molto sotto i 60 positivi richiesti. Non usata. Licenza dichiarata nel client: CC BY 4.0, `AccessConstraints: none`; e' area di emissione ottica booleana per pixel, NON conteggio di scariche. |
| **CREA WDA** | `import_crea_wda.py` / `join_crea_wda_provinces.py` nel repo | 339 record "heavy rain" + 121 "persistent rain", ma solo **data + provincia, senza ora**: troppo grossolano per fronti a scala oraria. Dichiarato, non usato. |

Il dataset storico `labels_metar_it_2015_2026.jsonl` (42.391 eventi, 99
stazioni, 2015→2026-10-23, descritto in `docs/VALIDAZIONE_SEVERO.md`) **non e'
su disco** e comunque **non copre il periodo dei frame frontali**
(2026-11-18 → 12-02): ho quindi interrogato IEM per il periodo sovrapponibile.

---

## 3. Disegno di validazione (dichiarato A PRIORI, prima di leggere i risultati)

Unita' di verifica = coppia `(valid_time del frame, stazione METAR)`.

- **(a) positivo osservato** — pioggia osservata (`RA`/`SHRA`/`TSRA`, WMO
  tab. 4678, parser di `scripts/validation/build_labels.py`) alla stazione
  entro **±3 h** dal `valid_time` del fronte previsto.
- **(b) negativo** — finestra spazio-temporale `(frame, stazione)` **senza**
  pioggia osservata nella stessa finestra.
- **(c) previsione positiva** — almeno un fronte previsto a **≤ 50 km** dalla
  stazione (distanza al **segmento** della LineString, non al vertice: con
  griglia 0,25° i vertici distano 20-28 km e usare i soli punti sovrastimerebbe).
- **(d) controllo +6 h** — stessa valutazione con finestra ±6 h: se i hit
  crescono, il prodotto ha un ritardo di fase dichiarato.
- 2x2: hit / miss / false allarme / corretto negativo;
  **POD = H/(H+M)**, **FAR = F/(H+F)**, **CSI = H/(H+M+F)**.
- Gate (#233): **≥ 60 positivi E ≥ 60 negativi** osservati. Soglie 50 km /
  3 h / 6 h = **SCELTE DI DISEGNO** dichiarate, non tarate (#218).

Regola #237 applicata: i frame con `valid_time` successivo all'ultimo
bollettino METAR disponibile sono **esclusi** (un frame senza copertura
osservativa non e' un negativo: 19 → 11 per ECMWF, 14 → 13 per DWD).

---

## 4. Numeri misurati (2026-11-22)

Comando: `scripts/validation/validate_fronts_independent.py` con
`--calibration calibration.json`, i frame reali di
`work_13/fronts_engine/evidence/frames{,dwd}`, i manifest `*_latest.json`,
il CSV IEM cached, `--radius-km 50 --window-hours 3 --control-hours 6`.

Report completi: `evidence/validate_fronts_ecmwf.json`,
`evidence/validate_fronts_dwd.json`.

| | **ECMWF IFS 0.25** | **DWD ICON EU** |
| :--- | ---: | ---: |
| Frame entro copertura osservativa | 11 | 13 |
| Stazioni METAR | 74 | 74 |
| Coppie verificate | 814 | 962 |
| **Hit** | **0** | **0** |
| Miss | 82 | 61 |
| Falsi allarmi | 18 | 13 |
| Corretti negativi | 714 | 888 |
| **Positivi (H+M)** | **82** | **61** |
| **Negativi (F+CN)** | **732** | **901** |
| **POD** | **0,000** | **0,000** |
| **FAR** | **1,000** | **1,000** |
| **CSI** | **0,000** | **0,000** |
| POD upper 95% (Wilson, z=1,959964) | 0,0448 | 0,0592 |
| **Controllo +6 h** — hit / POD / CSI | 1 / 0,00709 / 0,00633 | 1 / 0,00685 / 0,00633 |

Eventi pioggia con un fronte entro 50 km e ±3 h: **0** in entrambi i domini
(1 a ±6 h). La distanza mediana dall'evento pioggia al fronte piu' vicino,
su qualsiasi frame, e' **270,8 km**; solo **5/312** eventi hanno un fronte
entro 50 km su **qualsiasi** istante (e in quei casi l'istante e' distante
fino a 62 h). La distanza minima del fronte piu' vicino al centro d'Italia
(42,5 N / 12,5 E) e' 154-1.545 km nei 25 frame: nel periodo esaminato i
fronti rilevati stanno soprattutto sull'Atlantico e sul Nord Europa.

**Gate #233: RISPETTATO** (82 ≥ 60 e 732 ≥ 60; 61 ≥ 60 e 901 ≥ 60).
Le metriche sono quindi **pubblicabili** — e dicono che il prodotto, alle
soglie calibrate, **non mostra abilita'** su questo campione.

---

## 5. Verdetto e perche' resta `development_only`

La circolarita' e' rimossa: l'etichetta ora viene dal bollettino METAR, non
dalla pioggia del modello. Il dibattito "CSI 0,5 piatto" non e' piu' il
motivo del blocco. Il nuovo motivo e' il **risultato misurato**: POD 0 con
limite superiore di Wilson 0,045/0,059, FAR 1, CSI 0, e un solo hit quando
la finestra viene raddoppiata a ±6 h. Portare `publication_status` a
`validated` significherebbe dichiarare verde un layer per cui la validazione
indipendente dice "nessuna abilita'": e' la scorciatoia vietata dalle regole
(niente artefatti, niente "verdi" ottenuti per tecnicalita'). Quindi:
**`development_only` mantenuto**, con `publication_reason` e
`publication_note` riscritti sui numeri qui sopra.

---

## 6. Limiti dichiarati (non corretti, non nascosti)

1. La verita' e' una rete di **74 punti** (non un piano), con sampling ~60 min:
   l'assenza di bollettino non e' assenza di pioggia. Questo **abbassa il POD**.
2. I fronti esistono solo ai 24 istanti dei frame scaricati da **una sola run**
   (reference 2026-11-18): il campionamento temporale (9 h ECMWF, 5 h DWD)
   limita il POD indipendentemente dal rilevatore. Il controllo +6 h lo mostra.
3. Nessun bollettino `TSRA` nel periodo: la validazione copre pioggia
   stratiforme/convettiva debole, non temporali forti.
4. Nessun dato ESWD grezzo, nessun campo simulato, nessuna soglia tarata
   sull'osservato (regole #207/#218/#227/#237).
5. Il bridge CDP di ChatGPT (porta 9222) **non era raggiungibile**
   (`Impossibile effettuare la connessione al server remoto`): la verifica
   incrociata richiesta dalle istruzioni non e' stata possibile. La diagnosi
   e' stata invece controllata con tre controlli diretti: coordinate dei
   fronti coerenti con i manifest, array shapes uguali alle griglie, e
   distribuzione delle distanze (min 11,4 km / mediana 270,8 km / max 390,6 km).

---

## 7. Piano per un risultato positivo (da decidere, NON eseguito)

1. **Archiviare i fronti previsti** a ogni run operativa (non solo 24 frame
   sparsi): serve una storia di almeno 60-100 istanti con pioggia osservata
   vicino all'Italia. Stima: 3 mesi di fronti ogni 3 h.
2. **Ricalibrare le soglie su verita' indipendente**, non sul proxy di pioggia
   del modello: oggi `thresh_g` e' il 90° percentile di `|grad theta_e|`
   sulle isolinee zero — in novembre seleziona pochi fronti, lontani.
3. **Verificare la fase** con il controllo +6 h: un solo hit su 141/146
   coppie indica che allargare la finestra non basta; il problema e'
   geografico/temporale, non solo un ritardo.
4. **Aggiungere SYNOP o AFA numerica** (account EUMETSAT) solo se servono
   piu' positivi: con 74 stazioni e 312 eventi il campione e' gia' sufficiente
   (#233); il collo di bottiglia e' la posizione dei fronti, non la verita'.

---

## 8. Riproduzione

```powershell
$py = "C:\Users\aless\AppData\Local\Python\bin\python.exe"
$base = "C:\Users\aless\Desktop\meteorisk\work_fork\idswitch_real"
$fe   = "C:\Users\aless\Desktop\meteorisk\work_13\fronts_engine\evidence"
$mc   = <CSV IEM: station,valid,lon,lat,metar>

# 1) cache METAR (endpoint e parametri in §2)
# 2) validazione ECMWF
& $py "$base\scripts\validation\validate_fronts_independent.py" `
   --calibration "$base\calibration.json" --frames-dir "$fe\frames" `
   --manifest "$fe\manifests\ecmwf_ifs025_latest.json" --metar-csv $mc `
   --out "$base\evidence\validate_fronts_ecmwf.json"
# 3) validazione DWD (stesso comando, frames_dwd / dwd_icon_eu_latest.json)
# 4) test
& $py -m pytest "$base\scripts\tests\test_validate_fronts_independent.py" -q --capture=sys
& $py "$base\scripts\validation\validate_fronts_independent.py" --selftest
```

Test: **3/3 PASS** (`test_positive_case`, `test_negative_case`,
`test_control_6h`), piu' **8/8** controlli del `--selftest`.
