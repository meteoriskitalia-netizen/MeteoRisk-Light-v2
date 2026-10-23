# Metodi validati per supercelle e temporali severi organizzati

### Rassegna critica, confronto con i dati realmente disponibili in MeteoRisk-Light, e requisiti minimi di validazione

*Documento tecnico. Data: 2026-10-23. Nessuna modifica al codice, nessun push.*

---

## 0. Come leggere questo documento

### 0.1 Codici di provenienza (usati su ogni numero)

| Codice | Significato |
| :--- | :--- |
| **[V]** | verificato sul **testo integrale** di una fonte primaria accessibile in questa sede (il passaggio citato è stato letto) |
| **[A]** | verificato su **abstract** o su **fonte secondaria** che riporta il dato (la fonte primaria non era leggibile) |
| **[P]** | proviene da una **presentazione a convegno** o da documentazione operativa, **non** da un articolo peer-reviewed |
| **[NR]** | **non riportato**: la fonte consultata non espone il valore in forma testuale (tipicamente perché è in una figura o in una tabella-immagine), oppure l'articolo non lo riporta |
| **[NC]** | **non consultato**: il riferimento è bibliografico, il testo non è stato aperto in questa sede |
| **[M]** | **misura o calcolo proprio**, eseguito in questa sede e riportato con il metodo (non è letteratura) |

Regola applicata: **se un valore non è stato letto, non è stato stimato**. Dove la letteratura non riporta un numero, la cella dice `[NR]` e non un intervallo inventato.

### 0.2 Avvertenza sulle premesse

Tre premesse della richiesta vanno corrette prima di usarle (dettaglio in sez. 6):

1. **Cintineo et al. 2012** non è lo studio di skill di ProbHail: è una **climatologia di grandine** basata su MESH (`An Objective High-Resolution Hail Climatology of the Contiguous United States`, *Wea. Forecasting*, **27**(5), 1235–1248) **[V]**. I modelli probabilistici per hazard sono Cintineo et al. **2014** (v1) e **2020** (v2).
2. **"Kunz et al. 2018"** non è identificabile con certezza dalle fonti accessibili in questa sede: viene quindi **dichiarato non consultato** (sez. 7.6) e sostituito, per la parte ambientale europea, da Púčik et al. 2015 e Taszarek et al. 2017, che sono leggibili e quantitativi.
3. Il **POD di MDA/Stumpf 1998** non è estraibile: le tabelle di verifica sono immagini e il testo non è accessibile (sez. 7.1). Questo documento **non** colma il buco con una stima.

---

## 1. Rassegna dei metodi

### 1.0 La premessa fisica che decide tutto: che cosa è "rotazione" e da dove si legge

La rotazione mesociclonica si diagnostica dalla **velocità radiale Doppler** come **dipolo di shear azimutale**: due massimi di segno opposto, affiancati in azimuth, a parità di range. La grandezza fisica è lo shear azimutale (m s⁻¹ per km di arco), non un gradiente di riflettività.

Soglie operative documentate, per dare la scala del fenomeno:

| Grandezza | Valore | Fonte |
| :--- | :--- | :--- |
| Shear azimutale minimo perché un pattern venga considerato (sotto: rumore) | **2 m s⁻¹** per gate, messo a `missing` | DWD/POLARA, Hengstebeck et al., ERAD 2014 **[P][V]** |
| Soglia di severità 4 / 5 (shear massimo) | **> 15 / 20 m s⁻¹ km⁻¹** | ibid., Tab. 1 **[P]** (la tabella è trascritta in modo parziale nella fonte: si citano solo le due soglie di shear leggibili, livelli 4 e 5) |
| Mesociclone reale misurato in un prodotto DWD (XML, 22/10/2026 16:20Z) | `shear_mean` **2,761 m s⁻¹ km⁻¹**, `shear_max` **7,942 m s⁻¹ km⁻¹**, diametro **9,41 km** | sonda `REPORT.md` del 2026-10-23, sez. 5.1 **[M]** |
| Volume Doppler necessario | **10 sweep** da 0,5° a 25°, Nyquist **±32 m s⁻¹** (dual-PRF), 5 min, portata 180 km | Hengstebeck et al., ERAD 2014 **[P][V]** |

Conseguenza **geometrica**, non di soglia: i prodotti Radar-DPC consegnati (`VMI`, `CAPPI_1…10`, `VIL`, `ETM`, `POH`, `SRI`, `IR_108`) sono **mosaici 2D su griglia cartesiana da 1 km**, senza azimuth, senza range-gate e senza elevazione. Non esiste un `ΔV` fra gate adiacenti da calcolare: lo shear azimutale **non è definito** su quel dato, indipendentemente dall'algoritmo. È la stessa conclusione già scritta in `docs/STUDIO_SUPERCELLE_RADAR.md` sez. 2.1 e confermata dalla sonda (`POST /downloadProduct` risponde `400 productType non supportato: VEL`).

---

### 1.1 MDA / mesocyclone detection

**Riferimenti.**
- Zrnić, D. S., Burgess, D. W., & Hennington, L. D. (1985). *Automatic detection of mesocyclonic shear with Doppler radar.* **J. Atmos. Oceanic Technol., 2(4), 425–438** **[V, riferimento]**.
- Stumpf, G. J., Witt, A., Mitchell, E. D., Spencer, P. L., Johnson, J. T., Eilts, M. D., Thomas, K. W., & Burgess, D. W. (1998). *The National Severe Storms Laboratory mesocyclone detection algorithm for the WSR-88D.* **Wea. Forecasting, 13(2), 304–326** **[V, riferimento; tabelle di verifica = immagini, non lette]**.
- Hengstebeck, T., Wapler, K., Heizenreder, D., & Joe, P. (2018). *Radar network–based detection of mesocyclones at the German Weather Service.* **J. Atmos. Oceanic Technol., 35(2), 299–321** **[V, riferimento]**; dettagli algoritmici dalla versione ERAD 2014 **[P][V]**.
- Wapler, K., Hengstebeck, T., & Groenemeijer, P. (2016). *Mesocyclones in Central Europe as seen by radar.* **Atmos. Res., 168, 112–120** **[A]**.

**Cosa misura.** Un oggetto 3D di rotazione: successione verticale di *pattern vector* (sequenze di shear azimutale positivo a range costante), raggruppati in feature 2D per sweep e poi in oggetti 3D se le feature si sovrappongono verticalmente. DWD fonde anche le feature dei radar in copertura sovrapposta, per non contare due volte lo stesso mesociclone **[P][V]**.

**Dati di input richiesti.** Volume di **velocità radiale** su ≥7 sweep, più riflettività per la parte di severità (VIL, VIL density, echo top). DWD: 10 sweep, 5 min, Nyquist ±32 m s⁻¹, **correzione obbligatoria degli errori di unfolding dual-PRF** (filtro Laplaciano a due stadi), altrimenti lo shear è rumore **[P][V]**.

**Skill misurato.** **[NR]** per Stumpf et al. 1998 e per Hengstebeck et al. 2018: le tabelle di verifica non sono in forma testuale accessibile, e nessuna delle due fonti riporta POD/FAR nel testo o nell'abstract che ho potuto leggere. **Non sostituisco il dato con una stima.** L'unico dato quantitativo certo è che la catena è **operativa al DWD dal 2011** e che esiste un prodotto di mesocicloni già classificato per severità **[P][V]**: è un fatto di adozione, **non** una misura di skill.

**Limiti dichiarati.** (i) La qualità dipende dalla correzione dell'unfolding; (ii) a distanza il fascio a 0,5–1,5° viaggia a 1,5–2,5 km di quota e **sorvola** la rotazione bassa; (iii) i mesocicloni elevati possono essere mancati con beam troppo basso e viceversa; (iv) il pattern è **solo ciclonico** nella configurazione DWD (shear negativo trattato come rumore) **[P][V]**.

**Riproducibile con i dati DPC?** **NO, per costruzione.** Con i dati Doppler alternativi disponibili:

| Fonte velocità | Copertura | Nyquist | Riproducibilità MDA |
| :--- | :--- | :--- | :--- |
| ARPA Lombardia (anonima) | 27,1% del territorio italiano | **±9,15 m s⁻¹** | parziale: un mesociclone con ΔV di 30–40 m s⁻¹ **aliena** su quasi tutto il ciclo; il dealiasing su campo a basso Nyquist è ambiguo |
| OPERA MeteoGate, CH | confine alpino | **±8,22 m s⁻¹** (un solo sweep) | no: un solo sweep ⇒ nessun oggetto 3D, nessuna verifica di sovrapposizione verticale |
| OPERA MeteoGate, HR | Adriatico orientale | **±6,13 m s⁻¹** (volume) | tecnicamente sì, ma Nyquist ±6 m s⁻¹ rende inutilizzabile lo shear di un mesociclone intenso |
| DWD `VRADH` | Germania, 180 km | ±31,9 m s⁻¹ (misurato) | sì, **ma fuori dall'Italia** salvo margine alpino |

Differenza quantitativa che conta: DWD lavora a **±32 m s⁻¹**, Lombardia a **±9,15 m s⁻¹**. Il rapporto è **3,5×**. Dal caso DWD misurato (shear massimo **7,942 m s⁻¹ km⁻¹** su un diametro di **9,41 km**) il ΔV fra i due massimi del dipolo è dell'ordine di **alcune decine di m s⁻¹** — inferenza d'ordine di grandezza, marcata **[M]**, non un valore di letteratura. A ±9,15 m s⁻¹ quel segnale è **folded** proprio nei casi che interessano.

**Verdetto.** Metodo **peer-reviewed e operativo**, ma **non realizzabile** in Italia con i dati DPC; realizzabile solo su una frazione del territorio con Doppler a basso Nyquist, con qualità da dimostrare e **non** trasferibile come "supercella".

---

### 1.2 BWER e struttura 3D da riflettività

**Riferimenti.** Browning, K. A. (1964). *Airflow and precipitation trajectories within severe local storms which travel to the right of the winds.* **J. Atmos. Sci., 21, 634–639** **[A, riferimento]**; Chisholm, A. J., & Renick, J. H. (1972), studi sulle supercelle grandinigene dell'Alberta **[NC]**; Lemon, L. R. (1980). *Severe thunderstorm radar identification techniques and warning criteria.* **NOAA Tech. Memo. NWS NSSFC-3** **[NC]**; Rasmussen, E. N., & Blanchard, D. O. (1998), climatologia dei parametri da sounding per supercelle **[A, tramite Púčik et al. 2015]**.

**Cosa misurano.** Non la rotazione: le **conseguenze** della rotazione. BWER = volume a debole riflettività (tipicamente 10–20 dBZ) circondato sopra e ai lati da riflettività elevata, con "tetto" sopra il livello di equilibrio dell'updraft. Hook echo = appendice ricurva a bassa quota nel settore di inflow. WER = regione a eco debole non delimitata superiormente.

**Dati richiesti.** Riflettività 3D su **molti livelli** (idealmente 250–500 m di spaziatura verticale vicino alla base), risoluzione orizzontale sub-kilometrica, più la quota dello 0 °C e del −20 °C da NWP.

**Cosa possiamo misurare davvero.** Il DPC espone `CAPPI_1 … CAPPI_10` (quote fisse 1–10 km), cadenza nominale 10 min (misurata PT5M) **[M, sonda]**. Da lì: struttura verticale a **passo 1 km**, griglia 1 km.

Valutazione onesta, per firma:

| Firma | Misurabile da CAPPI 1 km? | Affidabilità documentata |
| :--- | :--- | :--- |
| **BWER** | **marginale**: l'estensione verticale tipica è 2–4 km, il passo di 1 km dà 2–4 campioni; il minimo interno va distinto dal rumore di mosaicatura | **[NR]** — la letteratura sul BWER è descrittiva/casi di studio; **non** esistono POD/FAR pubblicati per un BWER detector su mosaici CAPPI |
| **Hook echo** | sì, su `CAPPI_1`/`CAPPI_2`, se il passo 1 km e lo smoothing non lo cancellano | **[NR]** |
| **Gradiente di riflettività / WER** | sì, come campo 3D grossolano | **[NR]** |
| **Mesociclone** | **no** | richiede velocità (sez. 1.1) |

**Limiti.** (i) La VMI del DPC è **clippata a 10,0 dBZ** (minimo esatto misurato su frame reale) **[M]**: le parti deboli del BWER restano rappresentabili solo sopra 10 dBZ, che è comunque sufficiente (il minimo interno è ~15–20 dBZ), ma **non** per echi deboli o per il "clear slot"; (ii) il mosaico non ha geometria di raggio, quindi non si può separare attenuazione, beam filling e vera struttura; (iii) **nessuna** campagna di verifica italiana su queste firme è stata trovata.

**Verdetto.** Firme **misurabili come descrittori morfologici**, **non validate** come indicatori quantitativi di supercella. Uso corretto: input di un indice descrittivo dichiarato tale, mai etichetta "supercella".

---

### 1.3 MESH / SHI / POSH / POH / VIL / VIL density

**Riferimenti.** Witt, A., Eilts, M. D., Stumpf, G. J., Johnson, J. T., Mitchell, E. D., & Thomas, K. W. (1998). *An enhanced hail detection algorithm for the WSR-88D.* **Wea. Forecasting, 13(2), 286–303** **[V, riferimento]**; Amburn, S. A., & Wolf, P. L. (1997). *VIL density as a hail indicator.* **Wea. Forecasting, 12(3), 473–478** **[V, abstract]**; Cintineo et al. (2012), **Wea. Forecasting, 27(5), 1235–1248** **[V, riferimento]**; Murillo, E. M., & Homeyer, C. R. (2019). *Severe hail fall and hailstorm detection using remote sensing observations.* **J. Appl. Meteor. Climatol., 58(5), 947–970** **[V, testo integrale]**.

**Cosa misurano.** SHI = integrale verticale pesato in temperatura della riflettività sopra lo 0 °C, con pesi `W(Z)` lineari fra 40 e 50 dBZ e `W_T(H)` fra la quota dello 0 °C e quella del −20 °C; `MESH = 2,54·SHI^0,5`, con la costante **calibrata sul 75° percentile di 147 segnalazioni di grandine** **[V, Murillo & Homeyer, eq. 2–6]**. VIL = `3,44·10⁻⁶ ∫ Z^{4/7} dH`; VIL density = VIL diviso lo spessore dell'eco (o l'echo top a 18,5 dBZ) **[V, ibid., eq. 7]**.

**Skill misurato, per fonte.**

| Studio | Dato | Valore | Codice |
| :--- | :--- | :--- | :--- |
| Amburn & Wolf 1997 | VIL density **3,5 g m⁻³** come indicatore di grandine severa | identificava il **90%** dei casi di grandine severa, con **2 falsi allarmi su 36** casi non severi | **[A]** (abstract) |
| Murillo & Homeyer 2019 | 30 giornate, **10.650 celle**, **5.954 segnalazioni**; confronto MESH vs VIL density vs HDR | skill **simile** fra i tre; differenze **non significative** alle barre di bootstrap a 3σ; l'approccio **multi-variabile** aggiunge **~5 punti percentuali di CSI** | **[V]** |
| Murillo & Homeyer 2019 | massimo CSI assoluto per MESH | **`[NR]`**: i valori sono nelle Figg. 9–10, non nel testo | **[V]** |
| Witt et al. 1998 | POD/FAR di HDA/POSH | **`[NR]`** (paywall) | — |

**Limiti dichiarati.** MESH **sovrastima** la dimensione (Picca & Ryzhkov 2012, citato in Murillo & Homeyer **[V]**); il campione di calibrazione è di **147 casi** e il 75° percentile è una scelta, non una legge; la verifica è contro segnalazioni, con i bias noti delle segnalazioni (popolazione, dimensione, orario) **[V, Murillo & Homeyer sez. 1]**.

**Riproducibile con i dati DPC?** **SÌ, in forma parziale ma reale.**
- `VIL` e `POH` sono **prodotti nativi** del DPC (5 min) → disponibili subito, **ma l'algoritmo del POH non è documentato**: il suo skill è **ignoto** e non va dichiarato.
- `MESH` è **calcolabile** da `CAPPI_1…10` + quote 0 °C / −20 °C da NWP. Il passo verticale di 1 km è un'approssimazione grossolana dell'integrale (che nei WSR-88D usa gate da 250 m–1 km su ~14 elevazioni).
- `VIL density` è calcolabile con `VIL`/`ETM`, ma **ETM del DPC ha soglia non documentata** e la VMI è clippata a 10 dBZ: la normalizzazione per lo spessore dell'eco è quindi approssimata.

**Verdetto.** È il **blocco più solido disponibile**: formula pubblicata, calibrazione pubblicata, confronto indipendente pubblicato. Il numero da promettere va però **misurato su ESWD italiana**, non ereditato dagli USA.

---

### 1.4 ProbSevere (v1, v2, v3)

**Riferimenti.** Cintineo, J. L., Pavolonis, M. J., Sieglaff, J. M., & Lindsey, D. T. (2014). *An empirical model for assessing the severe weather potential of developing convection.* **Wea. Forecasting, 29** **[A]** (pagg. non verificate); Cintineo et al. (2018). *The NOAA/CIMSS ProbSevere model: incorporation of total lightning and validation.* **Wea. Forecasting, 33(1)** **[A]**; Cintineo, J. L., Pavolonis, M. J., Sieglaff, J., Cronce, L., & Brunner, J. (2020). *NOAA ProbSevere v2.0—ProbHail, ProbWind, and ProbTor.* **Wea. Forecasting, 35(4), 1523–1543** **[V, testo integrale]**; Cintineo et al. (2024). *ProbSevere version 3.* **Wea. Forecasting, 39(12)** **[A]**.

**Cosa misura.** Probabilità (0–1) che una **cella tracciata** produca, entro 60 min, grandine ≥ 1 in, vento ≥ 58 mph o tornado. Non è un classificatore di "supercella": è un nowcasting di **impatto al suolo**.

**Predittori esatti (v2)** **[V, Tab. 1–4]**:

| Modello | a priori | Predittori |
| :--- | :--- | :--- |
| ProbHail | 0,03 | max MESH / wet-bulb zero · LtgFR / EBShear · hailCAPE / PWAT · Δε_tot (crescita satellite normalizzata) |
| ProbWind (cellulare) | f(MUCAPE, EBShear) | max MESH · LtgFR / EBShear · Δε_tot |
| ProbWind (lineare) | f(MLCAPE, MW 1–3 km) | max VIL density · 98° percentile LLAzShear / MW 1–3 km · flash rate / 98° percentile MLAzShear |
| ProbTor | 0,01 (modulato da MLCAPE/MLCIN) | max LLAzShear · 98° LLAzShear / SRH01 · 98° MLAzShear / LtgFD · EBShear / MW 1–3 km |

dove LLAzShear/MLAzShear sono **shear azimutale da velocità Doppler** a 0–2 e 3–6 km, LtgFR/LtgFD sono **fulmini totali** (ENTLN, IC+CG), Δε_tot è l'emissività di top-of-troposphere da canale IR 11,2 µm.

**Skill misurato (v2)** **[V, sez. 7b]** — validazione su **227 giorni del 2018** (*Storm Data*), **~10.800 celle severe** (3.150 grandine, 8.250 vento, 840 tornado) e **~25.000 segnalazioni**:

| Confronto | POD | FAR | CSI |
| :--- | :--- | :--- | :--- |
| `probSevere` @ soglia 80% | **0,48** | **0,47** | **0,34** |
| Warning NWS (riferimento) | 0,76 | 0,53 | 0,41 |
| `ProbTor` @ soglia 50% | **0,33** | **0,80** | **0,14** |
| Warning tornado NWS | 0,58 | 0,74 | 0,22 |

Guadagno di v2 su v1, a 45 min di longevità: **+0,05 CSI (ProbHail)**, **+0,04 (ProbWind)**, **+0,10 (ProbTor)**; `probSevere` migliora di **fino a 0,08 CSI** in ogni bin di probabilità fra 10% e 90% **[V, sez. 7b]**. v3 migliora v2 "per la maggior parte degli Stati Uniti" **[A]**; **CSI di v3: `[NR]`** in questa sede.

Validazione di v1 (2014+2016): **~120 giorni**, **>3.200 celle severe** e **61.500 non severe**, metriche misurate alla soglia 80%, **valori in figura** **[A, pagina CIMSS; sez. "Model Performance"]** → POD/FAR/CSI assoluti di v1: **`[NR]`**.

**Limiti dichiarati dagli autori** **[V]**: dipendenza da segnalazioni di qualità disomogenea (vento soprattutto); matching segnalazione–centroide che penalizza le linee (doppia penalità: falsi allarmi **e** mancati); sovrastima sopra il 40% di probabilità (reliability diagram); naïf Bayes che degrada con predittori correlati.

**Disponibilità nel nostro caso, predittore per predittore** **[V per i predittori, M per la disponibilità]**:

| Predittore | Da noi | Nota |
| :--- | :--- | :--- |
| max MESH | **PARZIALE** | calcolabile da CAPPI 1 km + quote da NWP (sez. 1.3) |
| max VIL / VIL density | **SÌ** | prodotti nativi, normalizzazione approssimata |
| LLAzShear / MLAzShear | **NO** | richiede velocità radiale |
| LtgFR / LtgFD / LJA | **NO/PARZIALE** | LAMPINET è conteggio di fulminazioni con **buchi 403** e senza distinzione IC/CG documentata (sez. 1.5) |
| Δε_tot (11,2 µm, ε top-of-troposphere) | **PARZIALE** | abbiamo `IR_108` (10,8 µm) → possibile **ΔBT** come surrogato; **non** la stessa grandezza fisica |
| hailCAPE / PWAT / wet-bulb zero / SRH01 | **NO** | Open-Meteo espone CAPE/CIN/T2m/Td2m e venti a 1000/850/500 hPa; **SRH non è esposta** e il profilo è a 3 livelli di pressione |

**Verdetto.** Metodo **peer-reviewed con skill quantitativo**, ma la sua skill **non è trasferibile** a un sistema che perde i due predittori più informativi per la rotazione (AzShear) e per l'elettrificazione (fulmini totali). Costruire un "ProbSevere italiano" è possibile **solo** come modello **nuovo**, da addestrare e validare qui.

---

### 1.5 Lightning jump (LJA)

**Riferimenti.** Williams, E. R., et al. (1999). *The behavior of total lightning activity in severe Florida thunderstorms.* **Atmos. Res., 51, 245–265** **[V, riferimento]**; Schultz, C. J., Petersen, W. A., & Carey, L. D. (2009). *Preliminary development and evaluation of lightning jump algorithms for the real-time detection of severe weather.* **J. Appl. Meteor. Climatol., 48(12), 2543–2563** **[V, riferimento]**; Schultz, C. J., Petersen, W. A., & Carey, L. D. (2011). *Lightning and severe weather: a comparison between total and cloud-to-ground lightning trends.* **Wea. Forecasting, 26, 744–755** **[V, riferimento]**; Schultz, E. V., et al. (2014), *Enhanced verification of the lightning jump algorithm*, XV ICAE **[V, testo integrale]**.

**Cosa misura.** Un **salto** del tasso di fulmini totali (ΔFR/Δt) superiore a una soglia statistica (algoritmo 2σ), come precursore di intensificazione dell'updraft e quindi di severità.

**Skill misurato.**

| Configurazione | POD | FAR | Codice |
| :--- | :--- | :--- | :--- |
| 2σ, Schultz et al. 2009 | **81%** | **41%** | **[A]** (dato riportato in abstract secondario) |
| LJA di ricerca, 2002–2011 (verifica offline) | **65%** | **46%** | **[V]** (ICAE 2014, abstract) |
| LJA real-time 2012–2013 | POD simile, **FAR "notevolmente più alto"** | — | **[V]** (ICAE 2014) |

Il calo 81%→65% e l'aumento del FAR fra ricerca e real-time sono **il dato più importante della sezione**: la skill dipende dal regime di verifica, non solo dall'algoritmo. Gli autori attribuiscono parte del FAR a segnalazioni mancanti (non a falsi allarmi veri) **[V]**.

**Prerequisiti, e perché da noi sono il problema.**
1. **Fulmini totali** (IC+CG): Schultz et al. 2011 è esplicitamente un confronto fra trend totali e CG, e il valore aggiunto è nei totali **[V, riferimento]**.
2. **Copertura omogenea nel tempo**: la statistica 2σ è una deviazione standard su una serie. Un buco nella serie **non è rumore casuale**: può produrre un "salto" fittizio al ripristino del dato.
3. Da noi: il prodotto LTG del DPC è una **frequenza assoluta di fulminazioni** (Aeronautica Militare–CNMCA, rete LAMPINET), distribuita con file da 5 min, e il **403 è indistinguibile da "zero fulmini"**. È **misurato** un caso con **1.769 strike** in uno slot seguito da **cinque slot consecutivi di 403** dentro un temporale attivo (09/10, 01:10–02:35) (`docs/STUDIO_FULMINAZIONI_DPC.md` sez. 4.4) **[V, misura di progetto]**.

**Verdetto.** Il metodo è **peer-reviewed e quantitativo**, ma con la nostra sorgente il **prerequisito 2 è violato in modo dimostrato** e il prerequisito 1 è almeno dubbio (il prodotto non documenta la separazione IC/CG). Un LJA sui nostri dati sarebbe **un rivelatore di buche di copertura** almeno quanto un rivelatore di severità. Va escluso finché non esiste una serie a copertura omogenea (o una dichiarazione esplicita di "dato parziale" che inibisce il calcolo).

---

### 1.6 Overshooting tops, cold-U, divergenza di top

**Riferimenti.** Bedka, K. M. (2011). *Overshooting cloud top detections using MSG SEVIRI infrared brightness temperatures and their relationship to severe weather over Europe.* **Atmos. Res., 99, 175–189** **[V, riferimento]**; Bedka, K. M., Brunner, J., Dworak, R., Feltz, W., Otkin, J., & Greenwald, T. (2010). *Objective satellite-based overshooting top detection using infrared window channel brightness temperature gradients.* **J. Appl. Meteor. Climatol., 49, 181–202** **[V, riferimento]**; Dworak, R., Bedka, K. M., Brunner, J., & Feltz, W. (2012). *Comparison between GOES-12 overshooting-top detections, WSR-88D radar reflectivity, and severe storm reports.* **Wea. Forecasting, 27, 684–699** **[V, riferimento]**; Bedka, K., et al. (2018). *The above-anvil cirrus plume: an important severe weather indicator…* **Wea. Forecasting, 33(5)**, DOI `WAF-D-18-0040.1` **[A]**; Monette et al. (2012) **[NC]**.

**Cosa misurano.** OT: nucleo freddo circondato da gradiente termico ripido, sopra l'anvil, con correzione di parallasse; cold-U/cold-ring: struttura ad anello freddo con area calda interna; divergenza di top: derivata da motion vector su imagery ad alta frequenza.

**Skill misurato.**

| Dato | Valore | Codice |
| :--- | :--- | :--- |
| Copertura delle segnalazioni severe significative da parte di tempeste con OT (USA) | **~73%** | **[A]**, Bedka et al. 2018 come citato in letteratura secondaria |
| FAR del rilevatore OT IR-only (USA) | **6–32%** (intervallo) | **[P]** presentazione ECSS 2011; **non** peer-reviewed |
| POD/FAR specifici per l'Europa (Bedka 2011) | **`[NR]`** (paywall) | — |

**Limiti.** L'OT indica un updraft forte, **non** rotazione: molte supercelle hanno OT, molti OT non sono supercelle. La parallasse (≈ 2 km per km di quota a 10,8 µm) va corretta. Il cold-U richiede bande aggiuntive e non ha una soglia formale; la divergenza di top richiede motion vector che **non** calcoliamo.

**Riproducibile?** **SÌ per gli OT** (abbiamo `IR_108` a 5 min e la pipeline satellite già produce overshooting tops) e **PARZIALE per il cold-U** (dipende dalle bande MSG disponibili nel prodotto). **NO per la divergenza di top** (servono AMV).

**Verdetto.** Componente utile **come probabilità condizionata** ("data questa tempesta con OT, quanto è probabile che sia severa"), che va **calibrata su ESWD**: il 73% è la copertura delle segnalazioni, **non** il POD del rilevatore, ed è la differenza che di solito si sbaglia nel comunicarlo.

---

### 1.7 Metodi europei e alpini con verità ESWD

**Riferimenti.** Púčik, T., Groenemeijer, P., Rýva, D., & Kolář, M. (2015). *Proximity soundings of severe and nonsevere thunderstorms in central Europe.* **Mon. Wea. Rev., 143, 4805–4821** **[V, testo integrale]**; Taszarek, M., Brooks, H. E., & Czernecki, B. (2017). *Sounding-derived parameters associated with convective hazards in Europe.* **Mon. Wea. Rev., 145(4), 1511–1528** **[V, abstract esteso]**; Dotzek, N., Groenemeijer, P., Feuerstein, B., & Holzer, A. M. (2009). *Overview of ESSL's severe convective storms research using the ESWD.* **Atmos. Res., 93, 575–586** **[V, riferimento]**; Feldmann, M., et al. (2021). *A characterisation of Alpine mesocyclone occurrence.* **Weather Clim. Dynam., 2, 1225–1241** **[V, riferimento]**; Wapler et al. (2016) **[A]**.

**Cosa misurano.** Il **potere discriminante dell'ambiente**, non la struttura della cella: probabilità di grandine/vento/tornado in funzione di CAPE, shear 0–6 km (DLS), shear 0–1 km (LLS), SRH, LCL.

**Numeri (Púčik et al. 2015)** **[V]**:

| Quantità | Valore |
| :--- | :--- |
| Sounding di prossimità | **16.421** (32 stazioni, 12/2007–12/2013) |
| Con almeno un fenomeno severo | **3.866 (23,54%)** |
| Grandine severa / vento / tornado / pioggia | **10,0% / 8,0% / 3,3% / 13,7%** |
| Probabilità massima di grandine nello spazio (√(2·MUCAPE), DLS) | **0,5** (il valore più alto di tutti i fenomeni) |
| Probabilità massima di vento | **0,30** |
| Probabilità massima di tornado | **≤ 0,15**; **≤ 0,05** se **DLS < 15 m s⁻¹**, qualunque CAPE |
| Grandine: probabilità **< 0,15** in tutte le celle con √(2·MUCAPE) **< 30 m s⁻¹** | — |
| Tornado significativi | DLS mediano **22 m s⁻¹**, 10° percentile **15 m s⁻¹** |
| Vento in stagione fredda vs calda | DLS mediano **33,2 vs 16,1 m s⁻¹**; MUCAPE mediano **14 vs 695 J kg⁻¹** |
| LCL | **cattivo discriminante** fra intensità; LCL più alto ⇒ **più** probabilità di grandine |
| SRH3 | simile a DLS ma con **maggiore sovrapposizione** |

**Taszarek et al. 2017** **[V, abstract]**: **45.677** sounding (169 estremamente severi, 1.754 severi, 8.361 non severi, 35.393 con CAPE > 0 senza temporale); il parametro migliore nel separare non severo/severo è **WMAXSHEAR** = √(2·CAPE) × DLS; lo shear **0–3 km** è il migliore nel separare severo da estremamente severo per tornado e vento.

**Rasmussen & Blanchard 1998** (citato in Púčik): DLS mediano **18,4 m s⁻¹** per supercelle tornadiche e **19,4 m s⁻¹** per non tornadiche, su 0–500 m→6 km **[A]**.

**Limiti.** (i) Sono **climatologie di ambiente**: non dicono nulla sulla singola cella, e la sovrapposizione delle distribuzioni è grande (Púčik lo dichiara esplicitamente: i box-plot ingannano perché le classi severe sono 10–33× meno popolate) **[V]**; (ii) la verità è ESWD, con i suoi bias; (iii) **Italia**: nel campione Púčik ci sono Udine, Milano, Cuneo, S. Pietro Capofiume — cioè il nord, **non** il sud e non le isole.

**Verdetto.** È l'unico blocco **validato in Europa** e **immediatamente realizzabile**: i nostri modelli Open-Meteo danno CAPE e (con 3 livelli di pressione) uno shear 0–6 km approssimato. Il prodotto onesto è **"probabilità ambientale"**, non "supercella".

---

### 1.8 Updraft helicity (UH) da NWP ad alta risoluzione

**Riferimenti.** Kain, J. S., et al. (2008). *Some practical considerations for the use of … updraft helicity in supercell forecasts.* **Wea. Forecasting, 23** **[A]** (pagg. non verificate); Clark, A. J., et al. (2012). *Forecasting tornado pathlengths using a three-dimensional object identification algorithm applied to convection-allowing forecasts.* **Wea. Forecasting, 27** **[A]**; Clark, A. J., et al. (2013). *Tornado pathlength forecasts from 2010 to 2011 using ensemble updraft helicity.* **Wea. Forecasting, 28(2)** **[A]**.

**Cosa misura.** `UH = ∫ w·ζ dz` su 2–5 km (o 0–2 km): co-localizzazione di **updraft risolto** e **vorticità verticale risolta**. È un proxy di **rotazione simulata**, non osservata.

**Soglie operative documentate** (prodotto WoFS, NSSL) **[P][V]**: probabilità d'ensemble di **UH 2–5 km > 60 m² s⁻²**, **UH 0–2 km > 20 m² s⁻²**, **vorticità verticale 0–2 km > 0,003 s⁻¹**. Nel prodotto appare anche l'uso di **180 m² s⁻²** come soglia di "supercella" in letteratura applicata **[A]**.

**Riproducibile con i nostri modelli?** **NO.** Serve un modello **convection-allowing** (1–4 km) che risolva l'updraft. I nostri domini Open-Meteo sono **0,25° (≈ 28 km)**, `0,125°` (ICON globale, ≈ 14 km) e `0,13°` (GFS): a quella scala l'updraft è **parametrizzato**, la vorticità verticale del campo medio non ha relazione fisica con la rotazione di una supercella. Un "UH" calcolato lì sarebbe **un artefatto numerico** (e la letteratura che lo definisce lo calcola su griglie di 1–4 km).

**Verdetto.** **Non ottenibile** con l'infrastruttura attuale. Sostituibile solo con il proxy ambientale (sez. 1.7), dichiarandolo diverso.

---

### 1.9 Esiste un metodo validato per la rotazione SENZA Doppler?

La domanda è centrale perché è l'unica via per un "rilevamento di supercella" su tutto il territorio. Risposta, per candidato:

| Candidato | Stato | Dettaglio |
| :--- | :--- | :--- |
| **Moto deviante** (right-moving rispetto al vento medio) | **euristico, non validato quantitativamente** | Concetto da Browning 1964 **[A]**; il moto si può stimare dal tracking. **Nessuna** pubblicazione trovata che riporti POD/FAR del moto deviante come classificatore di supercella su un dataset etichettato |
| **Hook echo / inflow notch / BWER** | **euristico** | firme *conseguenti* alla rotazione, non misure; nessun POD/FAR su mosaici CAPPI (sez. 1.2) |
| **Rotazione da satellite** (cloud-top rotation) | **non disponibile** | richiede loop ad alta frequenza (1–2,5 min) e feature tracking; noi abbiamo 5 min e nessun prodotto di rotazione di top |
| **Divergenza da vento di superficie** | **non applicabile** | non abbiamo una rete di stazioni orarie nell'app; e la divergenza di superficie non è rotazione mesociclonica |
| **Polarimetria** (ZDR, ρHV, "hail spike") | **non disponibile** | il DPC non espone ZDR/ρHV; senza di esse niente idrometeore né segnatura di grandine |
| **"Vortice" da fulmini** (dedup geometrico) | **circolare** | `phenomena-verify` deriva dai nostri stessi file: non è verità |

**Conclusione.** **Non esiste**, nelle fonti consultate, un metodo **peer-reviewed con skill quantitativo** che identifichi la rotazione mesociclonica **senza** un campo di velocità (radar Doppler o, in prospettiva, lidar Doppler). Tutto ciò che resta senza Doppler è **morfologia + ambiente + satellite + fulmini**, e va etichettato come **"candidato"**, mai come "supercella" o "mesociclone".

---

## 2. Tabella di valutazione

Legenda disponibilità: **SÌ** = prodotto/calcolabile con i dati già presenti; **PARZIALE** = calcolabile con approssimazioni dichiarate; **NO** = impossibile con i dati attuali.

| # | Metodo | Dati richiesti | Li abbiamo? | Skill pubblicato | Copertura Italia | Costo computazionale | Rischio | Verdetto |
| :- | :--- | :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| 1 | **MDA / mesociclone** | velocità radiale 3D, ≥7 sweep, dealiasing | **NO** | **[NR]** su Stumpf 1998 e Hengstebeck 2018 | solo 27,1% (Lombardia) o confine alpino | alto (dealiasing + pattern vector) | **altissimo**: Nyquist ±9,15 ⇒ folded; copertura parziale spacciata per nazionale | **escluso** salvo progetto Doppler dedicato |
| 2 | **BWER / struttura 3D** | CAPPI multi-livello, quote 0/−20 °C | **PARZIALE** | **[NR]** (letteratura descrittiva) | 100% (mosaico DPC) | basso (stack di 10 CAPPI) | medio: passo 1 km, nessuna verifica | **ammesso solo come descrittore** |
| 3 | **MESH / SHI** | CAPPI + quote termiche | **PARZIALE** | Amburn & Wolf: 90% (VILd) **[A]**; Murillo: simile a VILd/HDR, multi-var **+5 pp CSI** **[V]**; MESH assoluto **[NR]** | 100% | basso | medio: calibrazione USA, 147 casi, sovrastima nota | **primo candidato quantitativo** |
| 4 | **POH DPC** | prodotto nativo | **SÌ** | **ignoto** (algoritmo non documentato) | 100% | nullo | alto: numero senza provenienza | **non dichiarare skill**; usabile solo come input |
| 5 | **VIL / VIL density** | VIL + ETM | **SÌ/PARZIALE** | 3,5 g m⁻³ ⇒ 90% POD, 2/36 FA **[A]** | 100% | nullo | medio: ETM a soglia ignota | **ammesso con cautela** |
| 6 | **ProbSevere-like** | MESH, AzShear, fulmini totali, ε_tot, RAP | **NO** (2 predittori chiave mancano) | v2: CSI 0,34 / POD 0,48 / FAR 0,47 **[V]**; ProbTor CSI 0,14 **[V]** | 100% | medio | **altissimo**: skill non trasferibile | **solo come modello nuovo, dopo 3–5** |
| 7 | **Lightning jump** | fulmini **totali**, copertura omogenea | **NO** (403 ambigui, IC/CG non documentati) | 81%/41% **[A]**; 65%/46% **[V]** | 100% nominale, ~6,7% di slot presenti | basso | **altissimo**: il 403 genera salti fittizi | **escluso finché il dato non è omogeneo** |
| 8 | **OT / cold-U** | IR 10,8 µm 5 min + parallasse | **SÌ (OT) / PARZIALE (cold-U)** | ~73% copertura dei severi significativi **[A]**; FAR 6–32% **[P]**; POD **[NR]** | 100% | basso (già in pipeline) | medio: confondere copertura con POD | **ammesso come probabilità condizionata** |
| 9 | **Ambiente (CAPE/DLS/SRH)** | sounding o NWP | **PARZIALE** (SRH assente, 3 livelli) | Púčik: 16.421 sounding, prob. max 0,5/0,30/0,15 **[V]**; Taszarek: WMAXSHEAR migliore **[V]** | 100% | nullo | basso, **se** etichettato "ambiente" | **raccomandato come base** |
| 10 | **UH da NWP** | modello 1–4 km | **NO** (griglie 14–28 km) | soglie operative 60 / 20 m² s⁻² **[P]** | — | — | **altissimo**: artefatto fisico | **escluso** |
| 11 | **Rotazione senza Doppler** | — | — | **nessun metodo validato** | — | — | altissimo | **non affermabile** |

---

## 3. Stack raccomandato (in ordine di rapporto rigore/costo)

Ogni componente dichiara **cosa aggiunge**, **quale metrica si può promettere** e **quale validazione serve prima di prometterla**.

### S0 — Igiene delle etichette (costo ~0, rigore: obbligatorio)

**Cosa aggiunge.** Nulla di fisico: toglie affermazioni non dimostrate. Rinominare "supercella" in **"cella convettiva organizzata — rotazione non misurabile"**, rimuovere `class_probs`/`confidence` finché non sono calibrati, fermare il layer fulmini dopo N errori 403 consecutivi (già indicato in `docs/STUDIO_SUPERCELLE_RADAR.md` sez. 1).

**Metrica promettibile.** Nessuna. **Validazione necessaria:** nessuna (è una correzione di linguaggio).

### S1 — Livello ambiente (costo basso, rigore alto)

**Cosa aggiunge.** La sola cosa **validata in Europa**: probabilità condizionata di grandine/vento/tornado dato l'ambiente (CAPE, DLS 0–6 km, LCL se derivabile). Riferimento quantitativo: Púčik et al. 2015 **[V]**.

**Metrica promettibile.** **AUC** e **Brier skill score** del classificatore ambientale, per hazard, con **reliability diagram**. *Non* POD/CSI di "supercella".

**Validazione necessaria.** ESWD QC1 come verità, matching sounding–evento secondo criteri espliciti (Púčik: 3 fulmini entro 150 km e 3 h **[V]**), stratificazione per stagione e regione, **almeno 3 stagioni**; confronto contro la climatologia di base (frequenza di evento) per dimostrare che il BSS > 0. **Nota:** con Open-Meteo la SRH non è disponibile: il modello va definito **senza** SRH, oppure la SRH va derivata da un provider con profilo verticale e **dichiarata come derivata**.

### S2 — MESH + VIL density + POH (costo basso, rigore medio-alto)

**Cosa aggiunge.** Il primo indicatore **di cella** con formula pubblicata e calibrazione pubblicata.

**Metrica promettibile.** Dopo misura: **CSI e POD/FAR per grandine ≥ 2 cm** (soglia ESWD "large hail"), con CI bootstrap. *Prima* della misura: **solo** la citazione della letteratura, **mai** un numero nostro.

**Validazione necessaria.** Etichette ESWD QC1 entro 10 km e ±10 min (finestra da giustificare, non da scegliere a posteriori); **≥ 150–200 eventi** di grandine severa e un rapporto **≥ 5:1** di celle non severe (sez. 4.1); **strati obbligatori**: distanza dal radar (errore di campionamento verticale), stagione, ora del giorno, regime (cella isolata vs linea); **congelamento delle soglie prima** della valutazione (niente taratura sul test).

### S3 — Overshooting tops + cold-U (costo basso, rigore medio)

**Cosa aggiunge.** Un predittore **indipendente dal radar** (satellite), utile contro i guasti radar e per la stratificazione.

**Metrica promettibile.** Probabilità condizionata di severità **data la presenza di OT**, calibrata (reliability + Brier). Il "73%" di copertura dei severi significativi **[A]** va citato **come copertura**, non come POD.

**Validazione necessaria.** Confronto OT ↔ ESWD con correzione di parallasse e con **controllo dei falsi OT su anvil non severi** (il FAR 6–32% **[P]** è USA e non peer-reviewed: da rifare qui); stratificazione per ora (di notte il VIS manca) e per dimensione del pixel a latitudine italiana.

### S4 — Firma morfologica 3D (BWER/hook) (costo medio, rigore basso)

**Cosa aggiunge.** Descrizione, non probabilità. **Metrica promettibile:** nessuna senza un dataset etichettato; al più **accordo inter-annotatore** su un campione manuale.

**Validazione necessaria.** Se si vuole un numero: etichettatura manuale di **≥ 200** celle (presenza/assenza di hook e BWER) da parte di **2+ analisti indipendenti**, con **κ di Cohen** riportato, e solo dopo il confronto con ESWD. Senza questo passaggio, resta un descrittore.

### S5 — Doppler regionale + MDA (costo alto, rigore alto ma copertura 27%)

**Cosa aggiunge.** L'unica **misura di rotazione** possibile oggi.

**Metrica promettibile.** POD/FAR/CSI di **mesociclone** (non "supercella", non "tornado") **limitatamente all'area coperta**, con **maschera di copertura e Nyquist pubblicata** insieme alla metrica.

**Validazione necessaria.** (i) Dealiasig verificato su casi noti; (ii) Nyquist ±9,15 m s⁻¹ ⇒ dichiarare la **frazione di tempo in cui il segnale è folded**; (iii) ESWD QC1 **dentro** l'area coperta, con **≥ 100 mesocicloni** per una stima utile; (iv) confronto con un secondo radar in sovrapposizione, se esiste.

### S6 — Fusione probabilistica (naïve Bayes / logistica) (costo medio, rigore: solo dopo S1–S3)

**Cosa aggiunge.** Una probabilità calibrata invece di punteggi arbitrari.

**Metrica promettibile.** **Brier skill score** e **AUC**, più **CSI a soglia scelta a priori**. Riferimento realistico: ProbSevere v2 ha **CSI 0,34** per "any severe" a soglia 80% e **0,14** per tornado **[V]**: chi promette 0,7 non ha capito la letteratura.

**Validazione necessaria.** Split **temporale** (mai casuale): addestramento su stagioni 1–2, validazione su stagione 3; **≥ 300 eventi severi** totali; controllo di **leakage** (nessun predittore derivato dalla verità); calibrazione (Platt/isotonic) valutata su dati **non** usati per il fit.

---

## 4. Requisiti di validazione

### 4.1 Quanti eventi servono (calcolo esplicito)

Per una proporzione `p` stimata su `n` casi indipendenti, l'ampiezza dell'intervallo al 95% è circa `±1,96·√(p(1−p)/n)`; invertendo, `n = z²·p(1−p)/e²` con `z = 1,959964`. Valori calcolati **[M]**:

| Proporzione attesa | semi-ampiezza ±0,10 | ±0,05 | ±0,03 |
| :--- | ---: | ---: | ---: |
| 0,50 | **97** | **385** | **1.068** |
| 0,70 | **81** | **323** | **897** |
| 0,80 | **62** | **246** | **683** |
| 0,90 | **35** | **139** | **385** |

Attenzione: **POD e FAR hanno denominatori diversi.** POD usa gli **eventi** (grandine/vento/tornado osservati); FAR usa gli **allarmi emessi**. Servono quindi **due** numerosità minime. Con FAR atteso 0,5 e ±0,05: **385 allarmi**; con FAR 0,7 e ±0,05: **323 allarmi** **[M]**.

L'approssimazione normale è ottimistica per campioni piccoli. Intervalli di Wilson (più onesti) su casi concreti **[M]**:

| Osservato | Stima | IC 95% (Wilson) | semi-ampiezza |
| :--- | ---: | :--- | ---: |
| 16/20 | 0,80 | 0,584 – 0,919 | **0,168** |
| 48/60 | 0,80 | 0,682 – 0,882 | **0,100** |
| 80/100 | 0,80 | 0,711 – 0,867 | **0,078** |
| 19/20 | 0,95 | 0,764 – 0,991 | **0,114** |

**Conclusione operativa.** "20 eventi" non è una validazione: con 16/20 il vero POD può essere 0,58. Per un **POD 0,8 ± 0,1** servono **≥ 62 eventi**; per **± 0,05**, **≥ 246**. Per un **CSI ± 0,05** con FAR ~0,5 servono **≈ 380 allarmi** (stima binomiale sulla cella dominante **[M]**; la varianza esatta del CSI non è binomiale e va stimata in bootstrap).

### 4.2 Costruire il dataset etichettato con ESWD

**Accesso e licenza.** L'ESWD è gestito da ESSL; i livelli di controllo qualità sono documentati pubblicamente **[V]**:

| Livello | Significato | Uso suggerito |
| :--- | :--- | :--- |
| QC0 | non ancora verificato, presente solo per pochi giorni | **escluso** |
| QC0+ | plausibilità di base | ammesso solo con analisi di sensibilità |
| **QC1** | confermato da fonte affidabile | **soglia minima per studi statistici** (indicazione ESSL) **[V]** |
| QC2 | verifica approfondita, raro | sotto-campione di riferimento |

La pagina ESWD consultata **non pubblica una licenza aperta**: l'accesso è tramite registrazione/richiesta secondo la *Data Policy* ESSL, i cui termini **non sono stati verificati in dettaglio** in questa sede → **`[NC]`**. Va letto il testo della Data Policy **prima** di costruire il dataset, e la licenza va registrata nel catalogo sorgenti (`docs/GROUND_TRUTH_SOURCE_CATALOG.md`, campo `license_status`, oggi `UNKNOWN`).

**Filtri di qualità da applicare (espliciti e pre-registrati).**
1. Solo **QC1+**; QC2 come controllo di sensibilità.
2. **Soglie di severità**: grandine **≥ 2 cm** (categoria "large hail" ESWD); vento **≥ 25 m s⁻¹**; tornado **F0+** (separando F2+). Le soglie vanno scritte **prima**.
3. **Matching spaziale/temporale**: distanza massima cella–report (es. 10 km, come Murillo & Homeyer **[V]**) e finestra temporale (es. ±10 min per radar a 5 min); **una** cella–evento per volta, con regola esplicita per gli eventi multipli.
4. **Esclusione dei report derivati dal radar** (inclusi i nostri): romperebbero l'indipendenza.
5. **Bias di segnalazione**: dichiarare la densità di popolazione/viabilità per cella; riportare la **curva di riportabilità** e, se serve, **filtrare** le zone a bassa densità (Murillo & Homeyer usano una soglia di popolazione per il campione "no hail" **[V]**).
6. **Nessun evento "nullo" inventato**: una cella senza report **non** è "non severa" con certezza; va trattata come **assenza di evidenza**, con il tasso di riportabilità stimato.

### 4.3 Metriche appropriate (CSI non basta)

| Metrica | Perché serve | Cosa aggiunge rispetto a CSI |
| :--- | :--- | :--- |
| POD, FAR, CSI | standard operativo, confrontabile con la letteratura (ProbSevere 0,34/0,48/0,47 **[V]**) | base |
| **HSS** | corregge per il caso (le celle non severe sono molte di più) | un CSI alto con frequenza base bassa è illusorio |
| **Brier score + BSS** | valuta la **probabilità**, non la decisione | penalizza la sovra/sotto-confidenza |
| **Reliability diagram** | mostra dove il modello è sbilanciato (ProbSevere: sovrastima > 40% **[V]**) | rende visibile il difetto che il CSI nasconde |
| **AUC/ROC** | indipendente dalla soglia | utile per confrontare predittori, **non** per l'operatività |
| **Bootstrap CI** (10.000 riscampionamenti) | obbligatorio con n piccolo | senza CI le differenze non sono affermabili (Murillo usa 3σ **[V]**) |
| **Verifica spaziale/object-based** | evita la doppia penalità su linee e split/merge | il CSI per pixel punisce un errore di 5 km come un errore di 50 km |

### 4.4 Stratificazione obbligatoria

Ogni metrica va riportata **almeno** per: (i) **distanza dal radar** (errore di campionamento verticale: a 100 km il fascio a 0,5° è a ~1,2 km); (ii) **stagione** (vento freddo: DLS mediano 33,2 vs 16,1 m s⁻¹, MUCAPE 14 vs 695 J kg⁻¹ **[V]**); (iii) **ora del giorno** (fulmini e satellite); (iv) **regime** (cella isolata vs linea: ProbSevere ha modelli separati **[V]**); (v) **copertura** (dove c'è Doppler e dove no).

---

## 5. Cosa NON si può dire (con i dati disponibili)

Elenco esplicito delle affermazioni che restano **indimostrabili**, con la ragione fisica.

1. **"Mesociclone"** — richiede uno shear azimutale da velocità radiale. I prodotti DPC sono mosaici 2D cartesiani: il gradiente azimutale **non esiste** nel dato. Non è una soglia da tarare: è una grandezza non definita.
2. **"Supercella"** in senso dinamico (updraft stazionario e rotante) — implicazione 1. La si può usare solo come **descrizione morfologica** e va scritto.
3. **"Rotazione"** in qualunque forma (anche "possibile rotazione") — nessun metodo validato senza Doppler (sez. 1.9).
4. **"Tornado"** o probabilità di tornado — nessun predittore di rotazione a bassa quota; ProbTor ha CSI 0,14 **con** AzShear e 0,01 di a priori **[V]**: senza AzShear non è nemmeno definito.
5. **"Quota della base / top dell'updraft"** dal mosaico — manca la geometria di raggio e la quota del fascio; i CAPPI danno quote fisse, non la quota dell'eco.
6. **"Continuità verticale del mesociclone"** — richiede oggetti 3D di velocità, non disponibili.
7. **"Grandine di X cm"** — MESH stima una **dimensione massima attesa**, sovrastima notoriamente (Picca & Ryzhkov 2012, in Murillo & Homeyer **[V]**) e la calibrazione è su **147** casi USA; nessuna verifica italiana.
8. **"Probabilità"** senza calibrazione — un punteggio 0–100 non è una probabilità: senza reliability diagram e Brier non si può chiamare così.
9. **"POD/FAR/CSI" nostri** — non esistono; qualunque numero citato oggi sarebbe un prestito dalla letteratura USA, non una misura nostra.
10. **"Supercella" da lightning jump** — il LJA misura l'intensificazione dell'updraft, e da noi è **inquinato dai 403** (1.769 strike seguiti da 5 slot assenti, misurato **[V]**).
11. **"UH"** dai modelli Open-Meteo — a 14–28 km l'updraft è parametrizzato: la grandezza non ha il significato fisico che Kain et al. le attribuiscono su griglie 1–4 km.
12. **"Miglioramento"** di un metodo rispetto a un altro senza baseline — serve la stessa verifica, stesso campione, stesse soglie, con CI bootstrap.
13. **"Copertura nazionale"** del Doppler — è il **27,1%** del territorio; una metrica calcolata lì **non** è una metrica italiana.
14. **"Confronto con altre reti"** (DWD, MeteoSwiss) — strumenti, Nyquist, geometrie e verità diverse: il confronto richiede armonizzazione, non è un numero.

---

## 6. Correzioni alle premesse della richiesta

| Premessa | Stato | Correzione |
| :--- | :--- | :--- |
| "Cintineo et al. 2012" come fonte di skill per grandine | imprecisa | è una **climatologia** MESH (WAF 27(5), 1235–1248) **[V]**; lo skill model è 2014/2020 |
| "Kunz et al. 2018" | non identificato | dichiarato **[NC]**; l'ambiente europeo è coperto da Púčik 2015 e Taszarek 2017 **[V]** |
| "MDA ha skill pubblicato" | vero ma non estraibile qui | POD/FAR di Stumpf 1998 e Hengstebeck 2018: **[NR]** in questa sede (tabelle-immagine / paywall) |
| "POH del DPC è un prodotto di grandine" | vero, ma | algoritmo **non documentato** ⇒ skill **ignoto**, non citabile |
| "LAMPINET è una rete di fulmini" | vero, ma | prodotto = **frequenza assoluta**, 403 ambiguo, IC/CG non documentato ⇒ non è "total lightning" certificato |
| "ProbSevere è replicabile" | falso nei predittori chiave | mancano **AzShear** (Doppler) e **fulmini totali**; Δε_tot sostituibile solo con ΔBT |

---

## 7. Bibliografia

Le voci marcate **[NC]** sono riferimenti bibliografici **non consultati** in questa sede: sono elencati perché citati nel testo come concetti, non come fonte di numeri.

**7.1 Mesocicloni / Doppler**
1. Zrnić, D. S., Burgess, D. W., & Hennington, L. D. (1985). Automatic detection of mesocyclonic shear with Doppler radar. *J. Atmos. Oceanic Technol.*, **2**(4), 425–438.
2. Stumpf, G. J., Witt, A., Mitchell, E. D., Spencer, P. L., Johnson, J. T., Eilts, M. D., Thomas, K. W., & Burgess, D. W. (1998). The NSSL mesocyclone detection algorithm for the WSR-88D. *Wea. Forecasting*, **13**(2), 304–326.
3. Hengstebeck, T., Wapler, K., Heizenreder, D., & Joe, P. (2018). Radar network–based detection of mesocyclones at the German Weather Service. *J. Atmos. Oceanic Technol.*, **35**(2), 299–321.
4. Hengstebeck, T., Heizenreder, D., & Joe, P. (2014). Detection of atmospheric rotation by means of the DWD weather radar network. *ERAD 2014*, abstract ID 196. **[P]**
5. Wapler, K., Hengstebeck, T., & Groenemeijer, P. (2016). Mesocyclones in Central Europe as seen by radar. *Atmos. Res.*, **168**, 112–120.
6. Feldmann, M., et al. (2021). A characterisation of Alpine mesocyclone occurrence. *Weather Clim. Dynam.*, **2**, 1225–1241.
7. Miller, M. L., Lakshmanan, V., & Smith, T. M. (2013). An automated method for depicting mesocyclone paths and intensities. *Wea. Forecasting*, **28**, 570–585.
8. Johnson, J. T., et al. (1998). The Storm Cell Identification and Tracking (SCIT) algorithm. *Wea. Forecasting*, **13**, 263–276.

**7.2 Struttura da riflettività**
9. Browning, K. A. (1964). Airflow and precipitation trajectories within severe local storms which travel to the right of the winds. *J. Atmos. Sci.*, **21**, 634–639.
10. Chisholm, A. J., & Renick, J. H. (1972). Studi sulle supercelle grandinigene dell'Alberta. **[NC]**
11. Lemon, L. R. (1980). Severe thunderstorm radar identification techniques and warning criteria. *NOAA Tech. Memo. NWS NSSFC-3*. **[NC]**

**7.3 Grandine**
12. Witt, A., Eilts, M. D., Stumpf, G. J., Johnson, J. T., Mitchell, E. D., & Thomas, K. W. (1998). An enhanced hail detection algorithm for the WSR-88D. *Wea. Forecasting*, **13**(2), 286–303.
13. Amburn, S. A., & Wolf, P. L. (1997). VIL density as a hail indicator. *Wea. Forecasting*, **12**(3), 473–478.
14. Cintineo, J. L., Smith, T. M., Lakshmanan, V., Brooks, H. E., & Ortega, K. L. (2012). An objective high-resolution hail climatology of the contiguous United States. *Wea. Forecasting*, **27**(5), 1235–1248.
15. Murillo, E. M., & Homeyer, C. R. (2019). Severe hail fall and hailstorm detection using remote sensing observations. *J. Appl. Meteor. Climatol.*, **58**(5), 947–970.

**7.4 ProbSevere**
16. Cintineo, J. L., Pavolonis, M. J., Sieglaff, J. M., & Lindsey, D. T. (2014). An empirical model for assessing the severe weather potential of developing convection. *Wea. Forecasting*, **29**. (pagg. non verificate)
17. Cintineo, J. L., et al. (2018). The NOAA/CIMSS ProbSevere model: incorporation of total lightning and validation. *Wea. Forecasting*, **33**(1). (pagg. non verificate)
18. Cintineo, J. L., Pavolonis, M. J., Sieglaff, J., Cronce, L., & Brunner, J. (2020). NOAA ProbSevere v2.0—ProbHail, ProbWind, and ProbTor. *Wea. Forecasting*, **35**(4), 1523–1543.
19. Cintineo, J. L., et al. (2024). ProbSevere version 3: improved exploitation of data fusion and machine learning. *Wea. Forecasting*, **39**(12).

**7.5 Fulmini**
20. Williams, E. R., et al. (1999). The behavior of total lightning activity in severe Florida thunderstorms. *Atmos. Res.*, **51**, 245–265.
21. Schultz, C. J., Petersen, W. A., & Carey, L. D. (2009). Preliminary development and evaluation of lightning jump algorithms for the real-time detection of severe weather. *J. Appl. Meteor. Climatol.*, **48**(12), 2543–2563.
22. Schultz, C. J., Petersen, W. A., & Carey, L. D. (2011). Lightning and severe weather: a comparison between total and cloud-to-ground lightning trends. *Wea. Forecasting*, **26**, 744–755.
23. Schultz, E. V., et al. (2014). Enhanced verification of the lightning jump algorithm. *XV Int. Conf. on Atmospheric Electricity*, Norman, OK.

**7.6 Satellite**
24. Bedka, K. M. (2011). Overshooting cloud top detections using MSG SEVIRI infrared brightness temperatures and their relationship to severe weather over Europe. *Atmos. Res.*, **99**, 175–189.
25. Bedka, K. M., Brunner, J., Dworak, R., Feltz, W., Otkin, J., & Greenwald, T. (2010). Objective satellite-based overshooting top detection using infrared window channel brightness temperature gradients. *J. Appl. Meteor. Climatol.*, **49**, 181–202.
26. Dworak, R., Bedka, K. M., Brunner, J., & Feltz, W. (2012). Comparison between GOES-12 overshooting-top detections, WSR-88D radar reflectivity, and severe storm reports. *Wea. Forecasting*, **27**, 684–699.
27. Bedka, K., et al. (2018). The above-anvil cirrus plume: an important severe weather indicator. *Wea. Forecasting*, **33**(5), DOI 10.1175/WAF-D-18-0040.1.
28. Monette, S., et al. (2012). Probabilità di severità da proprietà del top in IR. **[NC]**

**7.7 Ambiente europeo e verità di riferimento**
29. Púčik, T., Groenemeijer, P., Rýva, D., & Kolář, M. (2015). Proximity soundings of severe and nonsevere thunderstorms in central Europe. *Mon. Wea. Rev.*, **143**, 4805–4821.
30. Taszarek, M., Brooks, H. E., & Czernecki, B. (2017). Sounding-derived parameters associated with convective hazards in Europe. *Mon. Wea. Rev.*, **145**(4), 1511–1528.
31. Rasmussen, E. N., & Blanchard, D. O. (1998). A baseline climatology of sounding-derived supercell and tornado forecast parameters. *Wea. Forecasting*, **13**. (pagg. non verificate)
32. Dotzek, N., Groenemeijer, P., Feuerstein, B., & Holzer, A. M. (2009). Overview of ESSL's severe convective storms research using the European Severe Weather Database ESWD. *Atmos. Res.*, **93**, 575–586.
33. ESSL, *ESWD quality control* (livelli QC0/QC0+/QC1/QC2). https://www.essl.org/cms/european-severe-weather-database/eswd-quality-control/
34. Kunz, M., et al. (2018) — **non identificato**: dichiarato **[NC]**, non usato come fonte di numeri.

**7.8 Updraft helicity**
35. Kain, J. S., et al. (2008). Some practical considerations for the use of updraft helicity in supercell forecasts. *Wea. Forecasting*, **23**. (pagg. non verificate)
36. Clark, A. J., et al. (2012). Forecasting tornado pathlengths using a three-dimensional object identification algorithm applied to convection-allowing forecasts. *Wea. Forecasting*, **27**. (pagg. non verificate)
37. Clark, A. J., et al. (2013). Tornado pathlength forecasts from 2010 to 2011 using ensemble updraft helicity. *Wea. Forecasting*, **28**(2).
38. NSSL/CIWRO, *Cb-WoFS — Product descriptions* (soglie UH 60 / 20 m² s⁻², vorticità 0,003 s⁻¹). https://cbwofs.nssl.noaa.gov/product-description **[P]**

---

## 8. Registro degli accessi (trasparenza sulle fonti)

| Fonte | Esito | Conseguenza |
| :--- | :--- | :--- |
| `journals.ametsoc.org` (testo integrale) | **paywall/CAPTCHA**; le pagine HTML restituiscono solo didascalie delle figure | per Stumpf 1998, Witt 1998, Hengstebeck 2018, Bedka 2018: nessun POD/FAR estratto → **[NR]** |
| `ui.adsabs.harvard.edu` | **HTTP 405** su GET | abstract ADS non disponibili |
| `api.semanticscholar.org` | **HTTP 429** (rate limit) su 3 richieste su 4 | abstract non recuperati per Bedka 2011, Kain 2008, ProbSevere v3 |
| PDF via fetch diretto | **tipo `application/pdf` non supportato** dallo strumento | PDF letti tramite proxy di estrazione testo (`r.jina.ai`), dichiarato qui: i contenuti **[V]** da PDF sono passati per quel servizio |
| Presentazione ESSL ECSS 2011 (Bedka) | PDF **senza testo estraibile** (solo immagini) | FAR 6–32% riportato da citazione, marcato **[P]** |
| Sito ESWD | pagina QC letta; **Data Policy non aperta** | licenza: **`[NC]`** |
| `docs/STUDIO_SUPERCELLE_RADAR.md`, `docs/STUDIO_FULMINAZIONI_DPC.md`, `docs/ENVIRONMENT_DATA_SOURCES.md`, `docs/GROUND_TRUTH_SOURCE_CATALOG.md` | letti | usati per i vincoli di dato (Doppler assente, 403, SRH non esposta, catalogo sorgenti) |
| Sonda `%TEMP%\opencode\vel_probe\REPORT.md` | letto | whitelist DPC, VMI clippata a 10 dBZ, Nyquist e sweep DWD |

---

## 9. Sintesi in cinque righe

1. **Con i dati DPC la rotazione non è misurabile**: manca il campo di velocità, non la soglia.
2. **L'unico blocco validato e disponibile subito è l'ambiente** (Púčik 2015, Taszarek 2017), e va chiamato "ambiente", non "supercella".
3. **Il secondo è la grandine da riflettività** (MESH/VIL density), con skill USA documentato ma **numero nostro da misurare**.
4. **ProbSevere e LJA non sono trasferibili**: mancano AzShear e fulmini totali omogenei; il loro skill pubblicato (CSI 0,34 e POD 81%/FAR 41%) descrive **un altro sistema di osservazione**.
5. **Nessuna metrica nostra è dichiarabile** finché non esistono ≥ 62 eventi (POD ± 0,1) e ≥ 380 allarmi (FAR/CSI ± 0,05) su ESWD QC1, con stratificazione e intervalli bootstrap.
