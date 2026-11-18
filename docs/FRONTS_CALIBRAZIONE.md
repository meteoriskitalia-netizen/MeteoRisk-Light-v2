# FRONTS — calibrazione misurata del rilevamento TFP (MeteoRisk-Light-v2)

Nessun push. Nessun file HTML, VERSION, sw.js, workflow esistente o client toccato.
Nessun dato simulato. Nessuna UI. Nessun token. Simbologia convenzionale applicata
cosi' com'e', NON ricalibrata.

## 1. Simbologia FISSA (NOAA/WPC, Met Office, NOAA JetStream)

| tipo | linea | simbolo | posizione |
| :--- | :--- | :--- | :--- |
| freddo | blu | triangoli | verso il moto |
| caldo | rosso | semicerchi | verso il moto |
| stazionario | mista | triangoli blu da una parte, semicerchi rossi dall'altra | lati opposti |
| occluso | viola | triangoli e semicerchi | stesso lato |
| frontogenesi | tratteggiata | simbolo su OGNI segmento | verso il moto |
| frontolisi | tratteggiata | simbolo ogni SECONDO segmento | verso il moto |
| velocita' non calcolabile | **tratteggiata SENZA simboli** | nessuno | dichiarato in `null_reason` |

Nessun colore/forma nuovo, nessun tipo "non determinato" inventato.

## 2. Metodo e dichiarazioni scientifiche

* Metodo **TFP di Hewson (1998)** nell'implementazione di riferimento
  `phil-sansom/front_id` (MIT; Sansom & Catto 2022, GMD).
* `tau = theta_e` a 850 hPa da T850 + RH850, `p = 85000 Pa`, formula di
  **Bolton (1980) eq. 39** identica al client pubblicato (`ensThetaE`).
* **theta_e NON e' indipendente da T**: il contrasto termico e' gia' dentro tau.
  Nessun contributo termico aggiuntivo viene sommato.
* **RH satura nelle nubi**: il contrasto di theta_e si appiattisce sul fronte;
  il metodo puo' SOTTOSTIMARE i fronti nuvolosi. Proprieta' dichiarata del metodo.
* Griglia metrica: `dx = 111320*dx_deg*cos(lat)`, `dy = 111320*dy_deg`.
  Righe con `cos(lat) < 0.05` (lat > 87.13 gradi) escluse: singolarita' di dx.
* Pipeline: smoothing gaussiano a distanza fisica S -> theta_e -> gradiente ->
  `|grad tau|` -> asse `s-hat = grad tau / |grad tau|` (5 punti a meta' S) ->
  `L_tau = d|grad tau|/ds` -> **isolinee zero di L_tau** (marching squares) ->
  filtri `TFP < thresh_t` E `|grad tau| > thresh_g` (TFP campionato a +S/2
  lungo s-hat, perche' sull'asse L_tau=0 il TFP e' nullo per costruzione) ->
  line joining (`searchrad`, `min_length`) -> classe da velocita' del fronte.
* **Occluso SOLO** se due fronti gia' classificati si fondono presso un minimo
  di pressione; altrimenti `undetermined` NON viene emesso: si usa la linea
  tratteggiata senza simboli.
* Soglie ERA-Interim 1.5 gradi di front_id (`thresh_t=-1.5e11`, `thresh_g=7.3e-6`)
  **NON copiate** (memoria #237). `speed_threshold_ms = 1.5` e' CITATO da
  `classify_fronts.R`, non una soglia di rilevamento.

## 3. Misura dell'infrastruttura di lettura

I frame `.om` sono stati letti dai branch dati `fm2-ecmwf` / `fm2-dwd` via
`raw.githubusercontent.com` (CC BY 4.0, nessun token).

* **HTTP Range via omfiles**: inadatto alla calibrazione — misurato
- `2026-11-18T0000.om`: 173.876 s, 1056 richieste Range
- `2026-11-18T0300.om`: 194.12 s, 1101 richieste Range
  (circa 1050 richieste Range per 9 campi, 3 minuti per frame).
* **Download integrale + lettura locale**: 3,0-22,4 s per frame (5-7 MB);
  lettura locale di 13 array: ~0,02 s. Scelto per il campione.
* `$p` = `C:\Users\aless\AppData\Local\Python\bin\python.exe` (CPython 3.14.7,
  numpy 2.5.3, scipy 1.18.1, matplotlib 3.11.1, omfiles presente).
  `skimage` e `shapely` NON installati: marching squares via matplotlib Agg.

### Frame usati — ecmwf_ifs025 (branch fm2-ecmwf, griglia 1440x281 @ 0.25 gradi)

| valid_time | byte | download_s | sha256 |
| :--- | ---: | ---: | :--- |
| 2026-11-18T00:00Z | 5273392 | 3.01 | `1c0c39f84a76f39b9c326c9b46b80d20d54e3a615a850e6bd20b4e5999b760a5` |
| 2026-11-18T09:00Z | 6435136 | 5.58 | `ae4b1fa020046207cb04225f1e008c35c07daaf63968b117f4f5e33c5016c156` |
| 2026-11-18T18:00Z | 6510936 | 5.7 | `56277686a9543deda332c8718369eab4ca8350065671752d0af38c7f35a1f1f8` |
| 2026-11-19T03:00Z | 6555880 | 5.96 | `c2759548d4601fd6379028d6db2c0c8d979f79307efec8c875fac71dbec0d17d` |
| 2026-11-19T12:00Z | 6892512 | 5.96 | `1c7efa14dcf959c45b7ac8fc5564a5bf822641e56b92ce5722bf6de849bf1a31` |
| 2026-11-19T21:00Z | 6665104 | 6.59 | `13eb548790b6d71dc8a2bf3aba7a4f34161123a0e0b5deeb8927116596c558ce` |
| 2026-11-20T06:00Z | 6654408 | 4.52 | `2ebb1db49edd09229cc39e6203c56e3310217d3fd7db64fed11c9d5271cde3eb` |
| 2026-11-20T15:00Z | 6663088 | 5.79 | `e9a46e56adae43d99cca0aeddca59ad09b3e83d7ea6cdad62f156651c76f38d7` |
| 2026-11-21T00:00Z | 6646152 | 6.95 | `387f9548dbf59e7e364d514fd6eed9ae1222a90831f2f4fddc861b3af64cc35d` |
| 2026-11-21T09:00Z | 6637032 | 5.27 | `eefccd0dc8d684c182b9985424f7f16cb07918b0379dc1803e2d9e50fd5ef885` |
| 2026-11-21T18:00Z | 6670648 | 5.12 | `3181f224f23cf5b1bb40f96109c4ae234a46c9d8ae95361e7f55ad7fe85e1dbb` |
| 2026-11-22T03:00Z | 6460192 | 5.33 | `f3dfbac516ee016caffdca169a65fb12190e0c6e9faa5eb996451d31a80b7c3e` |
| 2026-11-22T12:00Z | 6395072 | 7.1 | `3892c7149903232759cf8efc2bda702456cdd3615392909917efead0c88e8ac1` |
| 2026-11-23T00:00Z | 6412984 | 5.34 | `f244298c4751421e28b897c24f840523113e52a74a1c9b6b9a075608d976c7a1` |
| 2026-11-24T00:00Z | 6433496 | 5.26 | `38b3091ea97dbfee62cc7f2dc37ac3de777a2935ca14fdb3a159c02192b9157a` |
| 2026-11-26T00:00Z | 6733552 | 5.58 | `5b5a814f7a29715c5e4c0cb74b444954f5afea195ecaa457e5b60e4231cd92e9` |
| 2026-11-28T00:00Z | 6761728 | 5.43 | `c72ea04ffa6bb6f55c642a1c8556207536cb49f698f350562745012c362bf265` |
| 2026-11-30T00:00Z | 6818080 | 5.74 | `4e6e9f410525e50ccdb3788c6b1fb975a63e361bca1124865e72d32755cd4c67` |
| 2026-12-02T00:00Z | 6917304 | 6.04 | `a93f4bc440f22883fb612a87272e05270d9ed2a0f433e96331793b3aa9553008` |

### Frame usati — dwd_icon_eu (branch fm2-dwd, griglia 881x449 @ 0.0625 gradi)

| valid_time | byte | download_s | sha256 |
| :--- | ---: | ---: | :--- |
| 2026-11-18T06:00Z | 3623328 | 5.73 | `815fb3820fdeeb630e9eb4a10c164ea7fc7462632729f87caee2e6b417bfd761` |
| 2026-11-18T16:00Z | 4631952 | 3.66 | `c818b07ef33ef0064b76423e3fbae1a7563e83293fb84de4d39a0e25c774bdf5` |
| 2026-11-18T21:00Z | 4660192 | 3.97 | `12e299cde226b3087d542026def48782e184032594c338ec9783b6ccf0aa0759` |
| 2026-11-19T02:00Z | 4676368 | 8.44 | `c74def0972ccf6c9fe99972f35b21612f98c644de3de73b41bfeb094a165c89f` |
| 2026-11-19T12:00Z | 4592032 | 8.74 | `f7f61898b719f86ca524469a10247f0f251ec803cc3b13ec57af124b8fcd0c29` |
| 2026-11-19T17:00Z | 4589752 | 3.27 | `1d1b7d633114a7ce7f511681229185e2d061ad62c4738e4cfe6c84e55fafdc00` |
| 2026-11-19T22:00Z | 4616792 | 3.0 | `3b32af9ae2865932bbf118140e740c3d4a2478cd006846c70a677862599e2cd4` |
| 2026-11-20T08:00Z | 4599640 | 3.54 | `4ecfa92ad93f72ccfa67aa466271228cb350ba2ddc19b2beb804807b2aae5fb3` |
| 2026-11-20T13:00Z | 4586960 | 3.49 | `830771de130e625dfef5c7ab5abe3b55ccf73e7ac42fe129a2aec115767830aa` |
| 2026-11-20T18:00Z | 4551768 | 4.15 | `2a3618ea7a9d3f7b0be108726908d23505c7a11d152aa1f3411546899a20eec4` |
| 2026-11-21T04:00Z | 4554864 | 22.42 | `92842be2bc60adbe013df92e62018bcd31ecd667914361f994cbe26e7912da8d` |
| 2026-11-21T09:00Z | 4532656 | 3.69 | `3986ceac70d03b65d9abbda97489586d8ec50f704e58abc84297c4f21b83d306` |
| 2026-11-21T18:00Z | 4606056 | 4.27 | `d51fd32c06f875957e63b0f9e73113eecf6f07b614b1f2067c1c3238b770eeb5` |
| 2026-11-23T00:00Z | 4645576 | 3.8 | `f67456e3f859e2bf12c72917f0da2736671d7285490bff2646982e110d7a4466` |

## 4. Dimensionamento del campione — MISURATO, non 30 giorni fissi

Regola R1 (dichiarata prima delle misure): partire da 6 frame, aggiungere 4 alla
volta, fermarsi quando (a) c'e' almeno 1 evento positivo e 1 negativo e (b) la
variazione relativa di P10/P5 di TFP e P90/P95 di `|grad tau|` e' < 10% per
**due passi consecutivi**.

Risultato: **14 frame per dominio** (non 30 giorni).

* ecmwf: a n=14 Δmax = 1,19% (secondo passo stabile), 5 positivi / 8 negativi;
  i successivi n=18 (0,82%) e n=19 (0,31%) confermano la stabilita'.
* dwd: a n=14 Δmax = 5,32% (secondo passo stabile), 5 positivi / 8 negativi.

### ecmwf_ifs025 — progressione percentili (S=100 km)

| n | TFP P10 | TFP P5 | |∇τ| P90 | |∇τ| P95 | Δmax rel | pos | neg | R1 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 6 | -1.37677e-10 | -2.00073e-10 | 3.41825e-05 | 4.43669e-05 | None | 3 | 2 | no |
| 10 | -1.36383e-10 | -1.99546e-10 | 3.4151e-05 | 4.38747e-05 | 0.0111 | 3 | 6 | no |
| 14 | -1.34762e-10 | -1.98102e-10 | 3.39092e-05 | 4.34049e-05 | 0.0119 | 5 | 8 | SI |
| 18 | -1.3366e-10 | -1.9648e-10 | 3.38045e-05 | 4.32851e-05 | 0.0082 | 8 | 9 | SI |
| 19 | -1.33678e-10 | -1.97093e-10 | 3.3709e-05 | 4.32375e-05 | 0.0031 | 9 | 9 | SI |

### dwd_icon_eu — progressione percentili (S=100 km)

| n | TFP P10 | TFP P5 | |∇τ| P90 | |∇τ| P95 | Δmax rel | pos | neg | R1 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 6 | -1.16891e-10 | -1.60307e-10 | 3.87906e-05 | 5.02604e-05 | None | 1 | 4 | no |
| 10 | -1.1012e-10 | -1.55401e-10 | 3.73576e-05 | 4.98564e-05 | 0.0579 | 3 | 6 | no |
| 14 | -1.11951e-10 | -1.53538e-10 | 3.72925e-05 | 4.72048e-05 | 0.0532 | 5 | 8 | SI |

## 5. Soglie finali MISURATE

| dominio | S km | thresh_t (K/m^2) | intervallo fuzzy | thresh_g (K/m) | intervallo fuzzy |
| :--- | ---: | ---: | :--- | ---: | :--- |
| ecmwf_ifs025 | 100 | -1.34762e-10 | -1.98102e-10 .. -1.34762e-10 | 3.39092e-05 | 4.34049e-05 .. 3.39092e-05 |
| dwd_icon_eu | 100 | -1.11951e-10 | -1.53538e-10 .. -1.11951e-10 | 3.72925e-05 | 4.72048e-05 .. 3.72925e-05 |

### Distribuzioni complete sui punti di isolinea (S = 100 km, 14 frame)

ecmwf_ifs025 (649799 punti):

| percentile | TFP (K/m²) | |∇τ| (K/m) |
| :--- | ---: | ---: |
| P1 | -3.70151e-10 | 6.07443e-07 |
| P5 | -1.98102e-10 | 1.59443e-06 |
| P10 | -1.34762e-10 | 2.58023e-06 |
| P25 | -6.27516e-11 | 5.38966e-06 |
| P50 | -1.56064e-11 | 1.11627e-05 |
| P75 | 1.77937e-11 | 2.10305e-05 |
| P90 | 6.25741e-11 | 3.39092e-05 |
| P95 | 1.02565e-10 | 4.34049e-05 |
| P99 | 2.15447e-10 | 6.35275e-05 |

dwd_icon_eu (145013 punti):

| percentile | TFP (K/m²) | |∇τ| (K/m) |
| :--- | ---: | ---: |
| P1 | -2.64042e-10 | 5.02275e-07 |
| P5 | -1.53538e-10 | 1.29885e-06 |
| P10 | -1.11951e-10 | 2.06861e-06 |
| P25 | -5.65876e-11 | 4.24184e-06 |
| P50 | -1.55833e-11 | 8.82722e-06 |
| P75 | 1.46231e-11 | 1.80634e-05 |
| P90 | 5.54707e-11 | 3.72925e-05 |
| P95 | 9.24308e-11 | 4.72048e-05 |
| P99 | 1.9149e-10 | 6.45394e-05 |

**Quantile-match fra domini** (nessuna rianalisi ERA-Interim disponibile come
riferimento; il confronto e' fra i due domini misurati):
`thresh_t(dwd)/thresh_t(ecmwf) = 0.8307`,
`thresh_g(dwd)/thresh_g(ecmwf) = 1.0998`.
I valori sono dell'ordine dell'unità: le due risoluzioni non richiedono un
fattore di scala di ordini di grandezza, ma le soglie restano per-dominio.

`thresh_t` e' il 10mo percentile di TFP (negativo: condizione `TFP < thresh_t`);
`thresh_g` e' il 90mo percentile di `|grad tau|`. S=100 km scelto perche' ha il
minor numero MISURATO di isolinee zero (274 su un frame rappresentativo, contro
1373 a 50 km e 2886 a 30 km). `min_length_km=100` e `searchrad_km=100` sono
scelte geometriche dichiarate, NON copiate da front_id (251/170 km).

## 6. Tabella hit/miss/false-allarm

Score scelto **prima** di vedere la tabella: `CSI = H/(H+M+FA)` (i positivi sono
rari, i corretti-negativi dominerebbero accuracy/POD).
Evento = frame con frazione di `rain+showers > 2 mm/h` nel box Europa
(lat 30-62N, lon -12..30E) sopra/sotto la mediana del campione.
La regola iniziale a "1% di griglia umida" e' stata **misurata e scartata**:
 produceva 0 negativi (frazione umida 0,18-0,52). Costo dichiarato: split
**relativo** al campione, non climatologico.

### ecmwf_ifs025

| S km | fattore | thresh_t | thresh_g | H | M | FA | CN | CSI | POD | n_front |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 30 | 0.3 | -1.31855e-10 | 1.676e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 8452 |
| 30 | 0.5 | -2.19758e-10 | 2.79333e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 5352 |
| 30 | 0.75 | -3.29637e-10 | 4.18999e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 3325 |
| 30 | 1.0 | -4.39517e-10 | 5.58666e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 2203 |
| 30 | 1.5 | -6.59275e-10 | 8.37999e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 1159 |
| 30 | 2.0 | -8.79033e-10 | 0.000111733 | 6 | 3 | 9 | 0 | 0.3333 | 0.6667 | 618 |
| 50 | 0.3 | -8.97055e-11 | 1.38107e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 4859 |
| 50 | 0.5 | -1.49509e-10 | 2.30179e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 3129 |
| 50 | 0.75 | -2.24264e-10 | 3.45268e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 2012 |
| 50 | 1.0 | -2.99018e-10 | 4.60357e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 1316 |
| 50 | 1.5 | -4.48528e-10 | 6.90536e-05 | 8 | 1 | 9 | 0 | 0.4444 | 0.8889 | 639 |
| 50 | 2.0 | -5.98037e-10 | 9.20715e-05 | 6 | 3 | 9 | 0 | 0.3333 | 0.6667 | 298 |
| 100 | 0.3 | -4.04286e-11 | 1.01728e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 2116 |
| 100 | 0.5 | -6.73811e-11 | 1.69546e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 1405 |
| 100 | 0.75 | -1.01072e-10 | 2.54319e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 889 |
| 100 | 1.0 | -1.34762e-10 | 3.39092e-05 | 9 | 0 | 9 | 0 | 0.5 | 1.0 | 552 |
| 100 | 1.5 | -2.02143e-10 | 5.08638e-05 | 4 | 5 | 9 | 0 | 0.2222 | 0.4444 | 210 |
| 100 | 2.0 | -2.69524e-10 | 6.78183e-05 | 1 | 8 | 9 | 0 | 0.0556 | 0.1111 | 73 |

### dwd_icon_eu

| S km | fattore | thresh_t | thresh_g | H | M | FA | CN | CSI | POD | n_front |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 30 | 0.3 | -2.12439e-10 | 1.73213e-05 | 2 | 3 | 9 | 0 | 0.1429 | 0.4 | 709 |
| 30 | 0.5 | -3.54065e-10 | 2.88688e-05 | 1 | 4 | 9 | 0 | 0.0714 | 0.2 | 522 |
| 30 | 0.75 | -5.31097e-10 | 4.33031e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 327 |
| 30 | 1.0 | -7.08129e-10 | 5.77375e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 217 |
| 30 | 1.5 | -1.06219e-09 | 8.66063e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 111 |
| 30 | 2.0 | -1.41626e-09 | 0.000115475 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 47 |
| 50 | 0.3 | -9.80842e-11 | 1.40507e-05 | 1 | 4 | 9 | 0 | 0.0714 | 0.2 | 431 |
| 50 | 0.5 | -1.63474e-10 | 2.34178e-05 | 1 | 4 | 9 | 0 | 0.0714 | 0.2 | 289 |
| 50 | 0.75 | -2.45211e-10 | 3.51267e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 183 |
| 50 | 1.0 | -3.26947e-10 | 4.68356e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 115 |
| 50 | 1.5 | -4.90421e-10 | 7.02533e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 54 |
| 50 | 2.0 | -6.53895e-10 | 9.36711e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 29 |
| 100 | 0.3 | -3.35853e-11 | 1.11878e-05 | 1 | 4 | 9 | 0 | 0.0714 | 0.2 | 201 |
| 100 | 0.5 | -5.59756e-11 | 1.86463e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 118 |
| 100 | 0.75 | -8.39633e-11 | 2.79694e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 73 |
| 100 | 1.0 | -1.11951e-10 | 3.72925e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 57 |
| 100 | 1.5 | -1.67927e-10 | 5.59388e-05 | 0 | 5 | 9 | 0 | 0.0 | 0.0 | 22 |
| 100 | 2.0 | -2.23902e-10 | 7.4585e-05 | 0 | 5 | 2 | 7 | 0.0 | 0.0 | 3 |

**Lo score NON e' informativo**: su ecmwf il CSI e' piatto a 0,5 per ogni
fattore <=1 (rilevatore saturato: 9 hit / 9 false allarmi / 0 miss); su dwd il
CSI massimo misurato e' 0.1429 e alle soglie finali e' 0.
La scelta delle soglie cade quindi sui percentili misurati, non su un massimo
di CSI inattendibile.

## 7. Output e CLI

GeoJSON RFC7946, coordinate **`[lon, lat]`** (conversione esplicita e testata;
attenzione: `synoptic.json` storico usa `[lat, lon]`).
Ogni feature: `kind:'front'`, `front_type`, `level:'850hPa'`,
`surface_analyzed:false`, `speed_ms` + `null_reason`, `tfp`, `strength` (K/m),
`length_km`, `n_points`, `confidence`, `reference`, `symbology`, `smoothing_S_km`.
Nessun campo "supercella", nessuna probabilita', nessun dato simulato.

```bash
python scripts/synoptic/fronts.py --manifest <latest.json> --frame <frame.om> \
  --domain ecmwf_ifs025 --generated-at <ISO8601> --calibration calibration.json \
  --out out.geojson
```

## 8. Verifiche eseguite

* `py_compile` su tutti i 12 file Python: **12/12 OK**.
* `pytest scripts/tests/synoptic -q --capture=sys`: **11 passed**.
  Copre: caso analitico (salto 10 K / 100 km, asse entro 10 km dal massimo
  gradiente, nessuna posizione inventata), determinismo byte-identico,
  `[lon,lat]`, null/motivo per T/RH mancanti, maschera orografia proxy,
  modalita' `uncalibrated`, simbologia fissa, classificazione, velocita' non
  calcolabile -> tratteggiata senza simboli.
* CLI su frame reale `2026-11-21T0000.om`: **2,02 s**, 32 feature,
  due esecuzioni con sha256 identico (`E095F2DF...E66B30`),
  output 59.020 byte.
* Per-frame compute (calibrazione, S=100): ecmwf 52.0 s
  totali su 19 frame x 3 S; dwd 14.79 s su 14 frame x 3 S.

## 9. NON verificato / limiti

1. **NON pubblicato**: `calibration.json` ha `status:'calibrated'` ma
   `publication_status:'hold'`. Servono eventi negativi credibili e verita'
   indipendente per validare l'accuratezza.
2. Lo score per evento e' ininformativo (saturazione / proxy di pioggia debole).
   Nessuna affermazione di POD/CSI/FARaccuratezza.
3. Campione = 19 frame ecmwf + 14 dwd, **non 30 giorni**: 14 bastano alla
   stabilita' dei percentili; 30 giorni non sono ne' necessari ne' misurati.
4. Un solo run per dominio (non indipendenza fra frame); le ripartizioni sono
   relative al campione.
5. `theta_e` dipende da T e RH satura in nube: sotto-stima possibile.
6. Nessuna etichetta ESWD/radar usata come verita'; nessun confronto con
   analisi umane.
7. Nessun run GitHub Actions, nessun workflow, nessun dispatch: tutto locale.
8. Sintassi dei blocchi `run:` non applicabile (nessun workflow creato).
9. `front_type` puo' essere `null` solo per velocita' non calcolabile, con
   simbologia tratteggiata senza simboli — non e' un nuovo tipo inventato.
