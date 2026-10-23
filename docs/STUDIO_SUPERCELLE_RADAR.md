# Supercelle dal radar DPC: che cosa l'engine misura davvero

### Studio congiunto: letteratura di riferimento + misure di produzione dell'engine MeteoRisk

*Documento tecnico. Due sistemi di etichette, tenuti separati:*

- **Sulle fonti**: **[DOC]** = documentato in una fonte citata; **[DER]** = calcolo o inferenza propria a partire da fonti citate; **[PRASSI]** = prassi ingegneristica/opinione dell'autore, non una norma pubblicata.
- **Sulle misure**: **MISURATO** = letto sul codice o sui run, con il riferimento; **INFERITO** = deduzione da misure, dichiarata come tale; **NON VERIFICATO** = non misurato in questa sede, con il motivo.

I riferimenti `file:riga` sono misurati sull'albero della release `releases\MeteoRisk-Light-1.2.4.15-GitHub-Production-Hardening\`. Nessun numero di questo documento è stimato a memoria: o viene dalla letteratura citata, o è una misura riportata con il suo riferimento.

---

## 1. Sintesi esecutiva

L'engine calcola un **indice composito 0-100 (SSI)** da un solo campo: la riflettività VMI del Radar-DPC. Non c'è velocità Doppler in nessun punto della pipeline, quindi l'engine **non misura rotazione**: misura intensità, organizzazione morfologica, persistenza e crescita di una cella convettiva.

**Che cosa può affermare** (con i dati che ha): che una cella convettiva è intensa (`max_dBZ ≥ 45`), organizzata (`OrganizationScore ≥ 56`), persistente (`≥ 2` frame) e in crescita, e che questa combinazione di descrittori è *insolita* rispetto alle altre celle dello stesso frame.

**Che cosa non può affermare**: mesociclone, supercella, tornado, quota di base, continuità verticale, hook echo, velocity couplet. Non è una limitazione di soglie: è una limitazione del dato di ingresso (criteri 1 e 2 della tabella §3 della letteratura, riportata in §4.2).

**Verdetto sulla produzione attuale**: il layer non è tarato, è un generatore di etichette. Emette almeno un candidato nel **54,5% dei run** (54 su 99), il campo `confidence` è la costante `"low"`, lo stesso temporale viene emesso due volte (cella + storm object, senza dedup) e l'unica "validazione" esistente è circolare (i badge VORTEX di `phenomena-verify` derivano dagli stessi file che l'engine produce). Il costo computazionale del layer è **0,002 s su 9,18 s** (0,02%): il problema non è la velocità dell'SSI, è che l'SSI non ha un riferimento esterno.

**Tre azioni a costo zero** (nessun nuovo dato, nessuna nuova fonte):

1. **Rinominare** il prodotto: "supercella" → "cella convettiva organizzata — candidato rotazione non misurabile", e rimuovere `class_probs`/`confidence` finché non sono derivate.
2. **Deduplicare** cella ↔ storm object prima della Fase 2 (stesso oggetto fisico, due schede).
3. **Fermare i fulmini** dopo 3 errori 403 consecutivi e allineare il backoff 36 → 12: **−20 s** sul 54% dei run.

Il resto (validazione con ground truth, Doppler) richiede dati che oggi non ci sono: §7.

---

## 2. Il dato di partenza: VMI senza Doppler

### 2.1 Che cosa entra nella pipeline

| Voce | Valore | Riferimento |
| :--- | :--- | :--- |
| Prodotto | Radar-DPC **VMI** (riflettività) | `config.py:29-31` (`"product_type": "VMI"`) |
| Formato | GeoTIFF **Float32, 1 banda**, griglia geografica | `preprocess.py:55-69` |
| Cadenza | 5 minuti (`PT5M`) | `config.py:29-31`, `history.py:34-37` |
| Accesso | `POST /downloadProduct` → URL S3 pre-signed, header `Origin: https://radar.protezionecivile.it` | `source_dpc.py:85-135` |
| Finestra per run | **6 frame** (T−25m … T0) | `config.py:29-31` (`history_frames: 6`) |
| Preprocessing | maschera nodata (−9999/−9998/NaN) + soglia 20 dBZ + opening/closing 3×3 | `preprocess.py:55-69`, `detect.py:98-105` |
| Filtri clutter/AP | **nessuno** | `preprocess.py:55-69`, `detect.py:98-105` |
| Oggetti prodotti | celle fino a **6.363 km²** (cap 40.000) | misura di produzione |

**MISURATO** (codice): il prodotto richiesto è VMI. **MISURATO** (codice): non esiste in tutta la pipeline alcun prodotto `VEL`, alcuna funzione di dealiasing, alcun calcolo di velocity couplet o di shear azimutale — nessun file del pacchetto `scripts/radar_engine/` gestisce un campo di velocità.

### 2.2 Che cosa questo esclude, in termini di criteri

**[DOC]** La rotazione si deduce solo dalla velocità radiale Doppler, come dipolo di shear azimutale; la riflettività da sola non prova rotazione [1][2][7]. Le firme morfologiche (hook echo, BWER) sono *conseguenze* di un mesociclone, non misure di rotazione [18].

Applicando i due criteri di ingresso della tabella di riferimento (§3 della letteratura, riportata integralmente in §4.2):

| # | Criterio | Soglia / valore di riferimento | Fonte | Effetto sul nostro dato |
| :-- | :--- | :--- | :--- | :--- |
| **1** | **Sensore richiesto** | Velocità radiale Doppler (base velocity), preferibilmente **storm-relative** | [1][2][7][3] | **Fallito per costruzione.** L'input è solo Z: l'algoritmo **non può** rivendicare rotazione. |
| **2** | **Shear azimutale (pattern vector)** | ≥ ~10⁻³ s⁻¹ per la severità 3 DWD; NSSL: soglie di shear **range-dependent** | [7][10][2] | **Non calcolabile.** Nessun ΔV fra gate adiacenti: non esiste il campo. |

**[DOC]** La definizione operativa NSSL di mesociclone richiede un vortice 3D con **profondità continua ≥ 3 km** e **base ≤ 5 km ARL**, rilevato in ≥ 2 volumi consecutivi [2]. **[MISURATO]** La VMI è già un **massimo verticale**: il prodotto non espone elevazioni, quindi non esiste alcuna quota e **nessuna continuità verticale è calcolabile**. Il codice lo dichiara in due punti: `supercell.py:6-8` ("Radar-DPC VMI a singola riflettività (nessun Doppler): mesocyclone e hook-echo non misurabili") e `supercell.py:36-40`; la stessa frase è esportata in ogni output nel campo `data_limits` (`output.py:316`).

**[DER]** Conseguenza quantitativa che vale la pena tenere a mente quando si legge una posizione: con rifrazione standard 4/3, a 0,5° di elevazione il centro del fascio è a ~0,9 km a 60 km, ~1,5 km a 100 km, ~2,6 km a 150 km, ~4,1 km a 200 km [20]. Anche se il prodotto avesse la velocità, "rotazione a bassa quota" oltre ~120 km sarebbe **non osservata**, non "assente".

---

## 3. L'algoritmo ricostruito

### 3.1 La formula

```
SSI = 100 * (0.25*intensity_core + 0.30*organization + 0.10*core_morphology
           + 0.15*persistence + 0.20*growth_sustained)      # supercell.py:108-147
                                                             # pesi: config.py:180-186
intensity_core   = 0.6*norm(max_dBZ, 35..55) + 0.4*norm(mean_dBZ, 20..45)
organization     = OrganizationScore/100
core_morphology  = 0.5*clamp01((solidity-0.5)/0.4) + 0.5*(1-clamp01((compactness-1)/7))
persistence      = clamp01(n_frame/5)
growth_sustained = 0.6*clamp01(Δarea_rel/2) + 0.4*(1-clamp01(ΔdBZ_rel))
```

**MISURATO** (`supercell.py:108-147`, `config.py:180-186`). Nessuna delle cinque componenti contiene una variabile dinamica: sono tutte funzione di Z, geometria della macchia e tempo di vita.

### 3.2 Le soglie principali

| Parametro | Valore | Dove | Che cosa vincola |
| :--- | :--- | :--- | :--- |
| `intensity_core_dbz` | **45 dBZ** | `config.py:187-193` | gate: core convettivo reale |
| `min_frames` | **2** (5 min) | `config.py:187-193` | gate: persistenza minima |
| `organization_organized` | **56** (floor della banda "Organized Convective Cell") | `config.py:167-171`, `supercell.py:62-77` | gate: organizzazione |
| `candidate_ssi` | **65** | `config.py:187-193` | gate: indice |
| `persistence_ref_frames` | 5 | `config.py:187-193` | saturazione della componente |
| `intensity_ref_dbz` | 35 – 55 | `config.py:187-193` | normalizzazione del core |
| `compactness_ref` | 8.0 | `config.py:187-193` | normalizzazione morfologica |
| `birth_frames` | 3 | `config.py:187-193` | etichetta `phase: birth` |
| soglia di detection | 20 dBZ | `detect.py:98-105` | maschera binaria |
| raggio morfologia | 3×3 px | `detect.py:98-105` | opening/closing |

Bande dell'SSI (`supercell.py:42-47`, `config.py:194-199`): **0-49** `non_supercell` · **50-64** `weak` · **65-79** `possible` · **80-100** `marked`.

Gate "candidato" (`supercell.py:150-158,183-186`): `max_dBZ ≥ 45` **e** `n_frame ≥ 2` **e** `OrganizationScore ≥ 56` **e** `SSI ≥ 65`, tutti simultaneamente.

### 3.3 Fase 2 (SSI v2)

| Componente | Peso dichiarato | Fonte del dato |
| :--- | :--- | :--- |
| `base` | **0.60** | l'SSI Fase 1 |
| `hook` | 0.15 | proxy morfologico da riflettività |
| `structure` | 0.10 | composito verticale (derivato interno) |
| `env` | 0.08 | ambiente Open-Meteo |
| `ot` | 0.04 | overshooting top da IR_108 |
| `lightning` | 0.03 | fulmini |

Pesi: `phase2/aggregate.py:46-53`. **MISURATO**: `aggregate.py:98-113` e `143-147` **rinormalizzano i pesi sui soli componenti non-`None`** (somma esattamente 1,00 sui presenti, campo `weights_effective`). Conseguenza: due candidati con lo stesso `base = 60` ma componenti mancanti diversi producono `ssi_v2` diversi, e **i valori non sono confrontabili tra candidati**. Il file lo dichiara (`"partial": True` se manca almeno una voce), ma il dato pubblicato non lo rende operativo.

### 3.4 Etichette, identità, igiene

| Aspetto | Stato misurato | Riferimento |
| :--- | :--- | :--- |
| `confidence` | **costante `"low"`**, non calcolata | `supercell.py:239` |
| `class_probs` | gaussiane con `sigma = 12.5` hardcoded, non propagate nel candidato | `scoring.py:165-177` |
| `supercell_id` | `SC-<YYYY-MM-DDTHH>-<cel/sto><id>` → **cambia ogni ora** (l'ora è nel timestamp, non c'è un contatore di episodio) | `supercell.py:208-209` |
| Dedup cella ↔ storm object | **assente**: `_evaluated_tracks` valuta sia le celle sia gli storm object | `supercell.py:243-247` |
| `latest_frame_index` | `len(frame non vuoti) − 1` invece di `len(cells_by_frame) − 1` | `supercell.py:258-260` |
| Stato finale | `complete_cycles` marca **tutti** i track `dead` → ogni candidato esce `"status":"dead"` anche con `on_latest_frame: true` | `tracking.py:491-497` |
| Fulmini, backoff | config **36** passi vs modulo **12** (`LTG_MAX_BACKOFF_STEPS = 12`) | `config.py:244` vs `phase2/lightning.py:57` |

**MISURATO** sulla produzione: il doppio conteggio è reale, non teorico — `SC-2026-10-23T01-cel001` e `SC-2026-10-23T01-sto001`, **3,3 km** di distanza, stesso `first_seen`, SSI 66 vs 68.

**Nota sulle citazioni.** La copia di lavoro `scripts\radar_engine\supercell.py` ha **257 righe**, la release 1.2.4.15 ne ha **277**: i numeri di riga di questo documento valgono per la release. Nella copia di lavoro il gate di organizzazione non è più la costante `56.0` ma `_organized_floor(cfg)` (`supercell.py:62-77`), che legge il floor della banda `Organized Convective Cell` da `config.py:170` — il valore numerico resta 56.

---

## 4. Validità scientifica

### 4.1 I sedici difetti

Gravità: **critico** = invalida il claim; **alto** = produce output fuorviante o non confrontabile; **medio** = rumore, costo, o perdita di informazione.

| # | Difetto | Gravità | Evidenza / riga di codice |
| :-- | :--- | :--- | :--- |
| 1 | **Nessun Doppler**: nessuna velocità radiale in input, quindi nessuna misura di rotazione | **critico** | `supercell.py:6-8,36-40`, `output.py:316` |
| 2 | **Nessuna continuità verticale**: VMI è già un massimo verticale, nessuna elevazione, nessuna quota | **critico** | `output.py:316`, `config.py:29-31` |
| 3 | **54,5% dei run emette candidati** (54 su 99, 128 candidati) con un indice non tarato | **critico** | misura di produzione (99 commit di `supercells.json`) |
| 4 | **`confidence` costante `"low"`** su ogni candidato | **critico** | `supercell.py:239` |
| 5 | **Validazione circolare con `phenomena-verify`**: HAIL/VORTEX derivati dagli stessi file che l'engine scrive | **critico** | `phenomena-verify.yml:5-14`, `phenomena/engine.py:470-476` |
| 6 | **Nessun ground truth, nessuna metrica** POD/FAR/CSI per l'SSI: `validation/metrics.py` copre solo il cell detection | **critico** | `validation/metrics.py:77-101` |
| 7 | **Dedup assente** cella ↔ storm object: lo stesso temporale emesso due volte | alto | `supercell.py:243-247` |
| 8 | **SSI v2 con pesi rinormalizzati**: non confrontabile tra candidati | alto | `phase2/aggregate.py:98-113,143-147` |
| 9 | **`supercell_id` cambia ogni ora**: nessuna identità nel tempo, nessun episodio | alto | `supercell.py:208-209` |
| 10 | **`latest_frame_index` sbagliato**: conta i frame non vuoti, non i frame con celle | alto | `supercell.py:258-260` |
| 11 | **`status` sempre `dead`** (finalize marca tutti i track chiusi) | alto | `tracking.py:491-497` |
| 12 | **Nessun filtro clutter/AP**: solo maschera nodata, soglia 20 dBZ e morfologia 3×3 | alto | `preprocess.py:55-69`, `detect.py:98-105` |
| 13 | **Nessuna dipendenza dal range**: soglie uniche globali, nessun gating su distanza o altezza del fascio | alto | `config.py:187-193` (un solo blocco per tutti) |
| 14 | **Oggetti fino a 6.363 km²** (cap 40.000): complessi convettivi trattati come "celle" | medio-alto | misura di produzione |
| 15 | **Fulmini in 403 a ogni run**: componente `lightning` sempre assente → rinormalizzazione su `base` | medio | `config.py:244` vs `phase2/lightning.py:57` |
| 16 | **`class_probs` gaussiane** con `sigma = 12.5` hardcoded, non propagate nel candidato pubblicato | medio | `scoring.py:165-177` |

**MISURATO** sui numeri di produzione (finestra 24 h, 99 commit validi di `supercells.json`): run con ≥1 candidato **54/99 = 54,5%**; candidati totali **128** (media **1,29**/run, mediana **0**, media **2,4** = 128/54 nei run con candidati **[DER]**, massimo **8** in un singolo run); livello `marked` (SSI ≥ 80) in **3 run**, SSI massimo **85**, SSI v2 massimo **53**; `confidence` alta **0**. Fulmini: **37 richieste in HTTP 403** in *ogni* run con candidati; **46 dei 54** run con candidati senza alcun dato fulmini. Archivio storico: **25 slot = 2 ore** (`history.py:34-37`), tutti con `supercells: []` nelle ultime 2 h; oltre 2 ore non esiste archivio, solo commit. Ground truth: `cases/cases.json` **3 casi** (0 `DOCUMENTED_SUPERCELL`), `cases_extended.json` **25** (19 con qualità `UNKNOWN`).

**[DER]** Il quadro che ne esce non è "soglia troppo bassa": è assenza di riferimento. Con 128 candidati in 24 h su 99 run, e nessun caso etichettato da confrontare, non esiste alcuna evidenza che l'SSI separi qualcosa. La letteratura è esplicita sull'ordine di grandezza atteso: il TDA del NSSL ha POD 43% e FAR 48% sui tornado [3], e un FAR ~50% è **normale** per il 1998, non un bug [3]. Un sistema che non dichiara FAR non è confrontabile con nulla.

### 4.2 Confronto puntuale con i criteri di riferimento

Legenda: **NSSL MDA** = Mesocyclone Detection Algorithm (USA, WSR-88D); **DWD MCD** = Mesocyclone Detection (POLARA, rete C-band tedesca). Le soglie NSSL dipendono dal range e sono espresse in "strength rank" 1–25 (rank 5 = soglie "mesociclone tipico", rank 1 = soglie più permissive) [2].

| # | Criterio | Soglia / valore di riferimento | Fonte | Presente nel nostro engine | Stato |
| :-- | :--- | :--- | :--- | :--- | :--- |
| 1 | Sensore richiesto | Velocità radiale Doppler, preferibilmente storm-relative | [1][2][7][3] | Solo riflettività VMI | **ASSENTE** |
| 2 | Shear azimutale (pattern vector) | ≥ ~10⁻³ s⁻¹ (DWD liv. 3); NSSL range-dependent | [7][10][2] | Non calcolabile | **ASSENTE** |
| 3 | Velocità rotazionale | V_r = (V_max − V_min)/2 ≥ 15 m s⁻¹ (DWD liv. 3) | [7][10][2] | Non calcolabile | **ASSENTE** |
| 4 | Diametro del vortice | 2–10 km; DWD ~5 km equivalente; NSSL aspect ratio ≤ 2 | [18][7][10][2] | Nessuna stima di diametro di vortice (solo area/compactness/solidity della cella) | **ASSENTE** |
| 5 | Estensione / continuità verticale | ≥ 3 km di profondità continua, senza gap | [2][7] | Nessuna elevazione nel dato | **ASSENTE** |
| 6 | Quota della base | ≤ 5 km ARL | [2][14] | Nessuna quota nel dato | **ASSENTE** |
| 7 | Persistenza | ≥ 2 volumi consecutivi (5–6 min); per "supercella" ≫ 10–20 min | [2][7][18] | `min_frames = 2`, finestra di 6 frame (25 min) | **PARZIALE** |
| 8 | Associazione verticale | Raggio 2 → 8 km, ordinamento per forza, uno-a-uno | [2] | Nessuno step di clustering 3D | **ASSENTE** |
| 9 | Associazione temporale | Volumi a < 15 min; raggio 2 km, max ~33 m s⁻¹ | [2] | Tracking con vettore di moto, 6 frame | **PARZIALE** |
| 10 | Soglie dipendenti dal range | NSSL: soglie multiple per fasce di range | [2] | Soglie costanti globali (45 dBZ, 56, 65) | **ASSENTE** |
| 11 | Range massimo utile | DWD 180 km; NSSL long-range oltre 175 km | [7][2] | Nessun gating per distanza | **ASSENTE** |
| 12 | Controllo qualità velocità | Dealiasing dual-PRF con correzione, filtri clutter/SQI | [7] | Nessun campo velocità da controllare; QC della Z limitato alla maschera nodata | **N/A + PARZIALE** |
| 13 | Fusione multi-radar | Feature da tutti i siti in un oggetto 3D | [7] | Non stabilito se la VMI DPC sia composito o single-radar | **NON VERIFICATO** |
| 14 | Contesto convettivo | Soglie per rigettare rotazioni non associate a temporale | [7][19][10] | Detection legata alla cella convettiva (Z ≥ 20 dBZ + morfologia) | **PRESENTE** |
| 15 | Output | Severità 1–5 (DWD) o classe + attributi (NSSL) | [7][2] | Banda 0-100 + componenti + gate, ma `confidence` costante, nessun V_r/diametro/base/distanza | **PARZIALE** |

**[DER]** Su 15 criteri: **1 presente** (14, contesto convettivo), **4 parziali** (7 persistenza, 9 tracking, 12 controllo qualità — formalmente non applicabile perché non c'è un campo velocità da controllare, 15 output), **9 assenti** (1, 2, 3, 4, 5, 6, 8, 10, 11), **1 non verificato** (13, fusione multi-radar). I quattro parziali sono esattamente la parte che si può fare con la sola riflettività. Tutto ciò che riguarda la rotazione è assente per una ragione sola: il dato.

**[DOC]** Le soglie non sono universali: Hengstebeck et al. riportano che per la rete DWD "sono risultati adatti valori fino a circa **un terzo** di quelli di Zrnić et al. (1985)", e che l'adeguamento è necessario per le differenze ambientali [7]; Stumpf et al. avvertono che "**i numeri magici o le soglie costanti non definiranno sempre se un vortice è significativo**", con rank ottimale diverso per tipo di temporale (rank 3 per supercelle low-topped, rank 5 per isolate) [2]. **[DER]** Copiare soglie NSSL/DWD senza ricalibrarle sulla propria rete è un errore metodologico documentato come tale — e il nostro engine, che non ha nemmeno un criterio 1 soddisfatto, non ha nulla da calibrare finché resta sulla sola Z.

---

## 5. Che cosa si può dire onestamente

### 5.1 Rinomina delle etichette

| Campo / testo attuale | Proposta | Motivo |
| :--- | :--- | :--- |
| `supercells.json` / "supercella" | **"cellule convettive organizzate — candidati"** | Nessuna misura di rotazione: il nome afferma ciò che il dato non contiene [18] |
| `ssi` (Supercell Signature Index) | **OCI** — *Organized Convection Index* (0-100) | Il nome attuale descrive un oggetto che non è misurato |
| `level: non_supercell / weak / possible / marked` | `weak / organized / strong / extreme` | `non_supercell` è una diagnosi negativa non supportata: l'assenza di firma non prova assenza di supercella |
| `candidate: true` | **"candidato rotazione non misurabile"** | Il gate dichiara ciò che manca, non ciò che c'è [PRASSI] |
| `confidence: "low"` | **rimosso** finché non è derivato dai gate | Una costante non è una confidenza |
| `phase: birth / sustained` | invariato | Descrive la traccia, non la meteorologia |
| `data_limits` | invariato, **in testa** al file e in UI | È l'unica parte già corretta del prodotto |

**[PRASSI]** Il criterio di accettazione della letteratura (§5.3 della stessa, punto 8) è più severo di così: non pubblicare l'etichetta "supercella" senza severità ≥ 3, profondità ≥ 3 km, base ≤ 5 km, persistenza ≥ 3 volumi, distanza < 150 km e qualità della velocità OK. Con la sola VMI **nessuno** di quei sei requisiti è verificabile: la conclusione non è "abbassiamo il criterio", è "cambiamo il nome a ciò che pubblichiamo".

### 5.2 Che cosa mostrare in UI

**Mostrare** (tutto verificabile dal dato):

- la cella convettiva con `max_dBZ`, area, `OrganizationScore`, età in frame e vettore di moto;
- il rank dell'indice **dentro il frame corrente** ("la 2ª cella più organizzata delle 14 di questo frame"), che è un'affermazione relativa e vera;
- il testo `data_limits` accanto all'oggetto, sempre, non in un tooltip;
- la distanza dal radar e, se calcolabile, l'altezza del fascio: cambia l'affidabilità di ciò che si vede [4][DER].

**Togliere**:

- l'SSI v2 con pesi rinormalizzati (`ssi_v2`, `weights_effective`, `partial`): due numeri sulla stessa scala ottenuti con pesi diversi non sono confrontabili e non devono comparire nella stessa lista;
- `class_probs` (`scoring.py:165-177`): probabilità gaussiane costruite su una banda, non su una frequenza osservata;
- `confidence: "low"`;
- qualsiasi badge "supercella"/"mesociclone"/"VORTEX" che non sia etichettato come *proxy da riflettività*: i badge VORTEX di `phenomena-verify` usano soglie proprie (Organization Score ≥ 76 per SUSPECT, ≥ 80 per CORROBORATED, ≥ 3 frame recenti, `phenomena/vortex.py:52-59`) e leggono gli stessi derivati — è un secondo parere sullo stesso numero, non una conferma indipendente.

---

## 6. Prestazioni: dove va il tempo

### 6.1 I run (20 run, step-level)

| Misura | Valore |
| :--- | :--- |
| Run totale | mediana **68 s** · p95 **233 s** · max **234 s** |
| Job motore (`radar-engine`) | mediana **40 s** |
| Job deploy (`deploy-pages`) | mediana **11 s** · max **52 s** |

**MISURATO**. **INFERITO**: i run da 201-234 s non sono lenti nel calcolo — stanno in coda sul job `deploy-pages`, che ha `concurrency: group: pages-deploy` condiviso (`radar-engine.yml:223-226`): il motore finisce in ~40 s, l'attesa è dello scheduler.

### 6.2 Gli step

| Step | Tempo |
| :--- | :--- |
| `pip install` | **14 s** |
| `Run radar engine` | **9 s** |
| `pytest` (184 test) | 3 s |
| build `_site` | 2 s |
| commit | 2 s |
| deploy | 7 s |

### 6.3 Il profilo interno del motore (6 frame, `--dry-run`)

| Fase | Tempo | Quota |
| :--- | :--- | :--- |
| `fetch_frames` | **6,74 s** | **73,4%** |
| — di cui `download_product` (6 POST + 6 GET) | 5,42 s | 59,0% |
| — di cui `validate_download` (riapre il file già aperto) | 1,54 s | 16,8% |
| — di cui `read_raster` | 1,44 s | 15,7% |
| `detect_cells` | **2,27 s** | **24,7%** |
| tracking + storm object | 0,26 s | 2,8% |
| **`supercell.evaluate`** | **0,002 s** | **0,02%** |
| Fase 2, 0 candidati | 0,000 s | 0% |
| Fase 2, con candidati | **+28,8 s** | misura su run `39886262604` (36,4 s vs 7,6 s) |
| **Totale (senza Fase 2)** | **9,18 s** | 100% |

**MISURATO**. La conclusione è netta e va scritta senza giri di parole: **"velocizzare il rilevamento delle supercelle" non significa toccare l'SSI**. L'SSI costa 2 millisecondi. Il tempo va in tre posti: il **fetch** (73,4%), la **Fase 2 quando ci sono candidati** (+28,8 s, causati da 37 richieste fulmini in 403 più 6 download di prodotto), e la **coda di deploy** (fino a ~190 s di attesa contro 40 s di lavoro).

**MISURATO** sul volume: 6 GeoTIFF per run, **3.017.718 byte** (~503 KB/frame), **13 richieste HTTP**; **144 run/giorno**; **6 commit/h = 144/giorno** su `main` da questo workflow; ~**132 job-min/giorno**; costo monetario 0 (repo pubblico). **INFERITO**: con prodotto ogni 5 min e run ogni 10 min, **5 frame su 6 sono ricalcolo** puro.

### 6.4 Tabella dei risparmi

| Intervento | Risparmio |
| :--- | :--- |
| fulmini: backoff 36 → 12 e stop dopo 3 errori consecutivi | **−20 s** sul 54% dei run (~−11 s/run medio) |
| dedup cella ↔ storm object prima della Fase 2 | −50% del costo della Fase 2 |
| download dei 6 frame in parallelo | −4 s/run |
| cache raster tra run (solo il frame nuovo) | −5,4 s/run, −2,5 MB/run |
| `validate_download` + `read_raster` in un'unica apertura | −1,5 s/run |
| pip cache / `uv` | −12 s/run |
| test solo su push di codice, non ogni 10 min | −3 s/run |
| `detect_cells` vettorizzato/multiprocess | 2,27 s → ~0,8 s |
| **Totale** | da ~68 s a **~30-35 s**; da 132 a **~55-60 job-min/giorno** |

Da leggere con due avvertenze: i risparmi sono **INFERITI** dalle misure sopra (non riapplicati e rimisurati), e la voce "dedup" vale −50% *della Fase 2*, che oggi è zero nel 46% dei run e +28,8 s nel resto.

---

## 7. Piano di lavoro in tre fasi

### Fase A — onestà e pulizia (nessun nuovo dato)

| # | Intervento | Valore | Risparmio |
| :-- | :--- | :--- | :--- |
| A1 | Rinomina etichette e indice (§5.1); `data_limits` in testa all'output e in UI | elimina il claim non supportato | 0 s |
| A2 | Dedup cella ↔ storm object prima della Fase 2 (stesso `first_seen`, distanza < 5 km) | dimezza le schede doppie (verificato: 3,3 km, SSI 66/68) | −50% Fase 2 |
| A3 | `confidence` derivata dai gate (non costante); rimozione `class_probs` | sostituisce una costante con una funzione dichiarata | 0 s |
| A4 | Fulmini: backoff 36 → 12 allineato al modulo, stop dopo 3× 403 | toglie 37 richieste fallite per run | **−20 s** sul 54% dei run |
| A5 | `supercell_id` stabile per episodio; `status` coerente con `on_latest_frame`; fix `latest_frame_index` | rende la traccia utilizzabile | 0 s |
| A6 | `ssi_v2` fuori dalla lista pubblica finché i pesi non sono fissi | evita confronti invalidi | 0 s |

Valore complessivo: il prodotto smette di affermare cose non misurate, e i run con candidati tornano vicini a quelli senza. Costo: nessuna fonte nuova, nessun dato nuovo, nessuna dipendenza esterna.

### Fase B — validazione (richiede lavoro, non richiede Doppler)

| # | Intervento | Come | Criterio di uscita |
| :-- | :--- | :--- | :--- |
| B1 | Dataset etichettato | Eventi ESWD con **QC ≥ 1** come base minima [16], più rianalisi radar indipendente di un meteorologo | ≥ 30 eventi (sotto ~30 le differenze di POD non sono distinguibili **[DER]**) |
| B2 | Matching spazio-temporale dichiarato **a priori** | Riferimento NSSL: detection entro diametro + 2 km, finestra −20 min / +1 volume [2][3] | regola scritta prima di guardare i risultati |
| B3 | Metriche | POD = hit/(hit+miss) · FAR = FA/(hit+FA) · CSI = hit/(hit+miss+FA) · HSS [2][16]; CSI e HSS perché HSS è fra le misure migliori in eventi rari [2] | valori con numerosità e intervalli di confidenza (Wilson o bootstrap) |
| B4 | Stratificazione | distanza (< 60, 60-120, 120-180 km), tipo di temporale (isolato vs linea), stagione; NSSL trova prestazioni 2-3 volte migliori sulle supercelle isolate [2] | tabelle separate, non un numero unico |
| B5 | Classificazione dei miss | (i) firma visibile ma algoritmo cieco; (ii) firma non visibile per campionamento; (iii) dato corrotto. Solo (i) è un errore dell'algoritmo [2] | conteggio per categoria |
| B6 | Versione congelata | ogni cambio di soglia = rerun del dataset + confronto A/B con la versione precedente [PRASSI] | changelog delle soglie con la metrica associata |

Riferimenti pubblicati con cui confrontarsi, senza pretese di superiorità: Zrnić et al. 1985 POD 90% / POFD 10% su **16** mesocicloni (intervallo al 95% di un POD 90% con n=16: ~64-98% **[DER]**) [1]; Stumpf et al. 1998 POD ≈ 100% sulle **firme visibili** e HSS max 35,7% nella diagnosi tornado/no-tornado [2]; Mitchell et al. 1998 POD 43% / FAR 48% / CSI 31% / HSS 46%, con l'88D TVS a POD 3% [3]; Wapler et al. 2016: tutti e 6 i tornado F2 tedeschi del triennio con mesociclone rilevato, ~50% delle grandinate entro 10 km/10 min [8]. **[DER]** Un algoritmo che dichiara POD > 90% e FAR < 20% su mesocicloni, senza dataset etichettato e senza stratificazione per range, è statisticamente implausibile rispetto a questa letteratura.

Nota di onestà sulla Fase B: le metriche misurano **l'indice**, e l'indice non è un mesociclone. Una validazione corretta può dire al massimo "l'OCI ordina le celle in modo utile rispetto agli eventi ESWD"; non può dire "l'SSI rileva supercelle", perché non le rileva — non ha il campo per farlo.

### Fase C — Doppler: se e quando

| Condizione | Stato |
| :--- | :--- |
| Prodotto **VEL** (velocità radiale) disponibile e accessibile con le stesse modalità del VMI | **NON VERIFICATO**: la pipeline non ne usa nessuno; non è stato accertato se il Radar-DPC lo esponga |
| Volume multi-elevazione | **NON DISPONIBILE** nel VMI: oggi non esiste alcuna elevazione |
| Dealiasing + flag di qualità | **DA COSTRUIRE**: nessun codice presente |
| Fusione multi-radar | **NON VERIFICATO**: non è stabilito se la VMI sia un composito |
| Soglie ricalibrate sulla rete italiana | **DA FARE**: le soglie NSSL/DWD non sono trasferibili acriticamente **[DER]** [7] |

**Dichiarazione esplicita**: l'engine attuale **non ha** il Doppler. Fino a quando non arriva un prodotto di velocità (o un dataset diverso con velocità e elevazioni), ogni frase che contenga "rotazione", "mesociclone", "supercella" o "hook echo" è fuori portata — non "da migliorare". Con il Doppler — e solo con il Doppler **più** un volume multi-elevazione — diventano calcolabili i criteri 1, 2, 3, 5, 6, 8 e 12 della tabella §4.2; 10 e 11 (soglie dipendenti dal range, range massimo) diventano allora ricalibrabili invece che arbitrari.

---

## 8. Cosa NON è stato verificato

| # | Voce | Perché |
| :-- | :--- | :--- |
| 1 | Prestazioni reali dell'SSI (POD/FAR/CSI/HSS) | **Nessuna metrica esiste** per l'SSI: `validation/metrics.py:77-101` copre solo il cell detection. Non c'è un numero da riportare, nemmeno sbagliato |
| 2 | Confronto con ESWD QC ≥ 1 | Non eseguito: nessun matching eventi↔candidati prodotto |
| 3 | Comportamento su casi documentati | `cases/cases.json` contiene **0** `DOCUMENTED_SUPERCELL`: non esiste un caso di riferimento |
| 4 | Generalità dei numeri di produzione | Finestra di **24 h** e **99** commit: è un campione, non una climatologia **[INFERITO]** |
| 5 | Costo della Fase 2 | **+28,8 s** misurato su **un** run (`39886262604`): n = 1, non una distribuzione |
| 6 | Causa dei 403 sui fulmini | Il codice la attribuisce a slot non ancora pubblicati (`phase2/lightning.py:52-57`), ma non è stata verificata contro il provider in questa sede |
| 7 | Effetto del dedup sul numero di candidati | Non implementato: il −50% della Fase 2 è una stima **[INFERITO]** |
| 8 | Cache raster tra run | Non implementata: −5,4 s e −2,5 MB/run sono **INFERITI** |
| 9 | Natura (composito o single-radar) della VMI DPC | Non accertata: cambia l'interpretazione dei criteri 11 e 13 |
| 10 | Disponibilità di un prodotto VEL dal Radar-DPC | Non accertata: condizione di ingresso della Fase C |
| 11 | Copia di lavoro vs release | `scripts\radar_engine\supercell.py` = **257 righe**, release 1.2.4.15 = **277**: le due copie non sono identiche (misurato). I riferimenti di questo documento valgono per la release |
| 12 | I risparmi della §6.4 | Somma di misure, non di run riottimizzati: nessun intervento è stato applicato e rimisurato |

---

## 9. Bibliografia

1. Zrnić, D. S., D. W. Burgess, and L. D. Hennington, 1985: *Automatic Detection of Mesocyclonic Shear with Doppler Radar.* J. Atmos. Oceanic Technol., **2**(4), 425–438. DOI: 10.1175/1520-0426(1985)002<0425:ADOMSW>2.0.CO;2 — https://journals.ametsoc.org/view/journals/atot/2/4/1520-0426_1985_002_0425_adomsw_2_0_co_2.xml *(letto: abstract completo; testo integrale non acquisito)*
2. Stumpf, G. J., A. Witt, E. D. Mitchell, P. L. Spencer, J. T. Johnson, M. D. Eilts, K. W. Thomas, and D. W. Burgess, 1998: *The National Severe Storms Laboratory Mesocyclone Detection Algorithm for the WSR-88D.* Wea. Forecasting, **13**(2), 304–326. DOI: 10.1175/1520-0434(1998)013<0304:TNSSLM>2.0.CO;2 — https://journals.ametsoc.org/view/journals/wefo/13/2/1520-0434_1998_013_0304_tnsslm_2_0_co_2.xml *(testo integrale letto; le Tabelle 1–2 e 10–17 sono immagini e non sono state lette: i valori numerici per fascia di range non sono citati)*
3. Mitchell, E. D., S. V. Vasiloff, G. J. Stumpf, A. Witt, M. D. Eilts, J. T. Johnson, and K. W. Thomas, 1998: *The National Severe Storms Laboratory Tornado Detection Algorithm.* Wea. Forecasting, **13**(2), 352–366. DOI: 10.1175/1520-0434(1998)013<0352:TNSSLT>2.0.CO;2 — https://journals.ametsoc.org/view/journals/wefo/13/2/1520-0434_1998_013_0352_tnsslt_2_0_co_2.xml *(abstract e note lette)*
4. Wood, V. T., and R. A. Brown, 1997: *Effects of Radar Sampling on Single-Doppler Velocity Signatures of Mesocyclones and Tornadoes.* Wea. Forecasting, **12**(4), 928–938. DOI: 10.1175/1520-0434(1997)012<0928:EORSOS>2.0.CO;2 — https://journals.ametsoc.org/view/journals/wefo/12/4/1520-0434_1997_012_0928_eorsos_2_0_co_2.xml
5. Brown, R. A., and V. T. Wood, 2000: *Improved WSR-88D Scanning Strategies for Convective Storms.* Wea. Forecasting, **15**, 208–220. DOI: 10.1175/1520-0434(2000)015<0208:IWSSFC>2.0.CO;2
6. Moller, A. R., C. A. Doswell III, M. P. Foster, and G. R. Woodall, 1994: *The Operational Recognition of Supercell Thunderstorm Environments and Storm Structures.* Wea. Forecasting, **9**(3), 327–347. DOI: 10.1175/1520-0434(1994)009<0327:TOROST>2.0.CO;2 — https://doi.org/10.1175/1520-0434(1994)009%3C0327:TOROST%3E2.0.CO;2 *(solo abstract: paywall)*
7. Hengstebeck, T., K. Wapler, D. Heizenreder, and P. Joe, 2018: *Radar Network–Based Detection of Mesocyclones at the German Weather Service.* J. Atmos. Oceanic Technol., **35**(2), 299–321. DOI: 10.1175/JTECH-D-16-0230.1 — https://journals.ametsoc.org/view/journals/atot/35/2/jtech-d-16-0230.1.xml *(open access, testo integrale letto; Tabelle 1, 3 e 4 sono immagini: si citano solo le soglie riportate nel testo)*
8. Wapler, K., T. Hengstebeck, and P. Groenemeijer, 2016: *Mesocyclones in Central Europe as seen by radar.* Atmos. Res., **168**, 112–120. DOI: 10.1016/j.atmosres.2015.08.023 — sintesi: https://www.hans-ertel-zentrum.de/en/publikationen/Wapler2016/index.html
9. Wapler, K., 2017: *The life-cycle of hailstorms: Lightning, radar reflectivity and rotation characteristics.* Atmos. Res., **193**, 60–72. DOI: 10.1016/j.atmosres.2017.04.009
10. Pacey, G., S. Pfahl, L. Schielicke, and K. Wapler, 2023: *The climatology and nature of warm-season convective cells in cold-frontal environments over Germany.* Nat. Hazards Earth Syst. Sci., **23**(12), 3703–3721. DOI: 10.5194/nhess-23-3703-2023 — https://nhess.copernicus.org/articles/23/3703/2023/
11. Trapp, R. J., S. A. Tessendorf, E. S. Godfrey, and H. E. Brooks, 2005: *Tornadoes from Squall Lines and Bow Echoes. Part I: Climatological Distribution.* Wea. Forecasting, **20**, 23–34. DOI: 10.1175/WAF-835.1
12. Trapp, R. J., and M. L. Weisman, 2003: *Low-Level Mesovortices within Squall Lines and Bow Echoes. Part I: Overview and Dependence on Environmental Shear.* Mon. Wea. Rev., **131**, 2779–2793. DOI: 10.1175/1520-0493(2003)131<2779:LMWSLA>2.0.CO;2
13. Ryzhkov, A. V., T. J. Schuur, D. W. Burgess, and D. S. Zrnić, 2005: *Polarimetric Tornado Detection.* J. Appl. Meteor., **44**(5), 557–570. DOI: 10.1175/JAM2235.1 — https://doi.org/10.1175/JAM2235.1 *(solo abstract)*
14. Miller, M. L., V. Lakshmanan, and T. M. Smith, 2013: *An Automated Method for Depicting Mesocyclone Paths and Intensities.* Wea. Forecasting, **28**, 570–585. DOI: 10.1175/WAF-D-12-00065.1
15. Newman, J. F., V. Lakshmanan, P. L. Heinselman, M. B. Richman, and T. M. Smith, 2013: *Range-Correcting Azimuthal Shear in Doppler Radar Data.* Wea. Forecasting, **28**, 194–211. DOI: 10.1175/WAF-D-11-00154.1
16. Dotzek, N., P. Groenemeijer, B. Feuerstein, and A. M. Holzer, 2009: *Overview of ESSL's severe convective storms research using the European Severe Weather Database ESWD.* Atmos. Res., **93**, 575–586. DOI: 10.1016/j.atmosres.2008.10.020 — ESWD: https://www.essl.org/cms/european-severe-weather-database/ ; controllo qualità: https://www.essl.org/cms/european-severe-weather-database/eswd-quality-control/
17. Deutscher Wetterdienst, *Mesocyclone detection* (descrizione operativa del MCD: 10 sweep ogni 5 min, approccio pattern-vector, livelli di severità 1–5). https://www.dwd.de/EN/research/weatherforecasting/met_applications/radar_data_applications/mesocyclone_detection_node.html
18. American Meteorological Society, *Glossary of Meteorology*, voci: **supercell** (https://glossary.ametsoc.org/wiki/Supercell), **mesocyclone** (https://glossary.ametsoc.org/wiki/Mesocyclone), **tornadic vortex signature** (https://glossary.ametsoc.org/wiki/Tornadic_vortex_signature), **hook echo** (https://glossary.ametsoc.org/wiki/Hook_echo), **bounded weak echo region** (https://glossary.ametsoc.org/wiki/Bounded_weak_echo_region)
19. Johnson, J. T., P. L. MacKeen, A. Witt, E. D. Mitchell, G. J. Stumpf, M. D. Eilts, and K. W. Thomas, 1998: *The Storm Cell Identification and Tracking (SCIT) Algorithm: An Enhanced WSR-88D Algorithm.* Wea. Forecasting, **13**, 263–276. DOI: 10.1175/1520-0434(1998)013<0263:TCIATA>2.0.CO;2
20. Doviak, R. J., and D. S. Zrnić, 1993: *Doppler Radar and Weather Observations*, 2nd ed., Academic Press. *(usato per la formula standard dell'altezza del fascio con rifrazione 4/3; i valori numerici nella §2.2 sono un calcolo proprio)*
21. Witt, A., M. D. Eilts, G. J. Stumpf, E. D. Mitchell, J. T. Johnson, and K. W. Thomas, 1998: *Evaluating the Performance of WSR-88D Severe Storm Detection Algorithms.* Wea. Forecasting, **13**, 513–518. DOI: 10.1175/1520-0434(1998)013<0513:ETPOWS>2.0.CO;2

### Nota di trasparenza sulle fonti

- **Lette integralmente (open access)**: [2] Stumpf et al. 1998 (testo, escluso tabelle-immagine), [7] Hengstebeck et al. 2018 (testo, escluso tabelle-immagine), [10] Pacey et al. 2023 (sezioni citate).
- **Lette come abstract/riassunto strutturato**: [1], [3], [4], [6], [8], [11], [12], [13], [14], [15].
- **Non accessibili**: Moller et al. 1994 (paywall, solo abstract); Ryzhkov et al. 2005 (paywall, solo abstract); Wapler et al. 2016 (paywall, riassunto dell'istituto); tabelle numeriche di [2] e [7] (immagini non testuali).
- Nessuna soglia di questo documento è riportata "a memoria": o è citata con la fonte, o è marcata **[DER]** / **[PRASSI]**.
- I numeri di produzione (§4.1, §6) sono misure sui run e sui commit del repository, non stime: dove derivano da un calcolo su quelle misure sono marcati **[DER]** o **INFERITO**.
