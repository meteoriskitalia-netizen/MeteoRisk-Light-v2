# OROGRAFIA SEMPLIFICATA — `data/geography/orography_it_*`

**Scopo.** Avere un campo di quota stabile dentro `main`, senza scaricare
450-580 MB di DEM a ogni uso. In `main` è pubblicata **solo la variante B**
(DEM). La variante A (0,1° dalle 257 quote) è **scartata** — errore del 98%
su `w·∇h` — e resta documentata perché è il confronto che giustifica B.

Dal **BUILD=228** il prodotto B è una griglia **0,02° (~2,2 km)** in
`data/geography/orography_it_0p05.json` (il nome del file è l'ID storico del
prodotto: la risoluzione effettiva è in `grid.dlon`/`grid.dlat`). Il file è
codificato in modo compatto: **uint8 quantizzato a 20 m + LZMA2 (filtro
delta 1) + base64**, con decodifica nel consumer `radar_engine/orography.py`.
Il vecchio vettore JSON di int16 resta leggibile (retrocompatibilità).

| file | griglia | celle | raw | gzip | fonte | sha256 |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| `orography_it_0p1.json` — **NON pubblicata** | 0,1° (≈11 km) | 20.800 | 128.184 B | 13.361 B | 257 quote di punto (Open-Meteo / Copernicus GLO-90) interpolate IDW | `75290b10…0f8fdeebe` |
| `orography_it_0p05.json` — **in `main` dal BUILD=228** | **0,02° (≈2,2 km)**, path storico | **520.000** | **181.770 B** | **139.772 B** | DEM SRTM (~90 m) ridotto, Terrain Tiles z8 | **`17db2272…fea35e`** |
| `orography_it_0p05.json` — versione BUILD=215 (storica) | 0,05° (≈5,5 km) | 83.200 | 254.024 B | 74.939 B | DEM SRTM (~90 m) ridotto | `cc823da4…f021cc` |

Generatore: `scripts/geography/build_orography.py`. Test: `scripts/tests/test_orography.py`
e `scripts/tests/radar_engine/test_orography.py`.

## Formato

```json
{
  "schema": "meteorisk.orography/1",
  "grid": {"registration": "cell_centered", "lon0": 4.0, "lat0": 35.0,
           "lon1": 20.0, "lat1": 48.0, "dlon": 0.02, "dlat": 0.02,
           "nx": 800, "ny": 650,
           "ordering": "row-major, lat crescente (iy=0 -> lat0), poi lon"},
  "units": "m", "dtype": "uint8",
  "encoding": {"codec": "lzma2_delta1", "payload": "base64",
               "scale_m": 20.0, "offset_m": 0.0, "levels": 256,
               "uncompressed_bytes": 520000,
               "uncompressed_sha256": "64cc9726…fcdbfb22",
               "compressed_bytes": 133248},
  "elevations_m": "<base64 del flusso XZ>",
  "...": "schema, fonte, licenza, metodo, stats, validation_samples"
}
```

- Griglia **cell-centered**: la cella `(ix, iy)` copre
  `[lon0+ix*dlon, lon0+(ix+1)*dlon)` × `[lat0+iy*dlat, lat0+(iy+1)*dlat)`,
  centro `lon0+(ix+0,5)*dlon`, `lat0+(iy+0,5)*dlat`.
- Indice lineare: `iy*nx + ix`, con `iy=0` alla latitudine `lat0`.
- **Decodifica** (`encoding.codec = "lzma2_delta1"`):
  1. `base64.b64decode(elevations_m)`;
  2. `lzma.decompress(...)` (il filtro delta è dichiarato nell'header XZ,
     quindi `lzma.decompress` lo annulla da solo);
  3. `np.frombuffer(raw, dtype=np.uint8).reshape(ny, nx)`;
  4. `elevazione_m = q * encoding.scale_m` (passo 20 m, 0 = mare).
- Il vecchio formato (`elevations_m` = lista di interi, `dtype: "int16"`) è
  ancora accettato dal loader; **non** viene più prodotto per il DEM.
- `generated_at` è **null** per costruzione: nessun timestamp nel file.
- `elevations_m` non è più una lista: chi legge il file senza il loader deve
  controllare `dtype`/`encoding`. Il consumer ufficiale è
  `radar_engine.orography.load_grid`.

## A) Le 257 quote di punto — `orography_it_0p1.json`

**Metodo.** IDW locale, potenza 2, raggio 60 km, distanza equirettangolare in km
(lat media 41,5°). Oltre 60 km dalla stazione più vicina la cella è nodata.

**Perché locale e non su tutte le stazioni.** IDW su tutte le 257 stazioni dava
**196 m** in Pianura Padana (45,0 N / 10,0 E) contro ~45 m reali, perché le
stazioni alpine a 150 km entravano nella media. Con il taglio a 60 km, nel punto
(45,0 / 10,0) il valore scende a **42,7 m**; il centro della cella 0,1° che lo
contiene (44,95 / 9,95) vale **117,7 m** — la differenza fra i due è la stessa
liscia del campo, non un errore di calcolo.

**Errore misurato (leave-one-out, 257/257 stazioni).**

| metrica | valore |
| :--- | ---: |
| MAE | **322,2 m** |
| RMSE | 439,4 m |
| p90 \|errore\| | 732,9 m |
| max \|errore\| | 1450,3 m |
| bias | +6,6 m |

I casi peggiori sono stazioni di montagna isolate fra stazioni di valle:
Catania a 1849 m (Etna) predetta 392 m; Cosenza 1626 m predetta 192 m; Aosta
2330 m predetta 977 m. Non è un difetto dell'interpolatore: con 257 punti
spaziati (mediana 29,3 km, max 55,8 km) una cima non è rappresentabile.

**Valori di controllo (centro cella).** Alpi/Valtellina 1685 m · Pianura Padana
**118 m** · Sicilia interna 576 m · Monte Bianco 2038 m (reale 4807: nessuna
stazione campiona la cima). La cella della Pianura Padana non rispetta il
criterio "Po < 100 m" (118 m); nel punto esatto 45,0/10,0 il valore è 42,7 m.
È la misura della liscezza del campo, non un errore di calcolo.

**Copertura.** 6.422 celle su 20.800 (30,9%) hanno dato; le altre 14.378 sono
mare o territorio estero a più di 60 km da una stazione. Sui **23.374 vertici**
dei confini delle 107 province, **142 (0,61%)** cadono su nodata e sono **tutti
su isole minori**, in 5 province: **ME 44** (Eolie), **AG 37** (Lampedusa e
Linosa), **LT 27** (Ponza e Ventotene), **TP 25** (Pantelleria), **PA 9**
(Ustica). Nessun vertice della terraferma è scoperto.

## B) DEM ridotto 0,02° — `orography_it_0p05.json`

**Metodo.** Terrain Tiles (Mapzen/joerd, AWS Open Data) a **zoom 8**: sul suolo
italiano la fonte è **SRTM 1/3 arcsec (~90 m)**, pixel a 45°N ≈ 432 m. Ogni
cella 0,02° è la **media pesata cos(lat)** dei pixel DEM che ci cadono dentro
(mediana **20 pixel/cella**); i valori negativi (batimetria ETOPO1) sono
azzerati a 0 m, quindi il mare è 0. **Nessuna cella nodata**
(`cells_filled_from_neighbours: 0`).

**Perché 0,02° e non 0,05°.** A 0,05° la cella dell'Etna vale **2351 m**
 contro una vetta di 3357 m: la cella da 5,5 km media la cima con i pendii e
lascia il vulcano 1 km sotto la realtà. A 0,02° la cella che contiene la vetta
vale **3080 m** (misura di cella, non vetta) e il Monte Bianco **4380 m**
(era 3560 m). Il dettaglio in più è quello che serve al descrittore `w·∇h`:
sulle creste la pendenza è il segnale, e una cella da 5,5 km la cancella.

**Costo del download (una tantum).** 182 tile, **18.713.724 B (17,8 MiB)**,
~150 s la prima volta (dalla cache ~2-9 s). Il file che resta in `main` pesa
**181.770 B raw / 139.772 B gzip** (gzip -9 deterministico, `mtime=0`), cioè
**139,8 KB** di transfer: sotto il tetto di 150 KB con 10 KB di margine.
Il payload compresso è 133.248 B, quello non compresso 520.000 B.

**Valori di controllo (cella che contiene il punto, quantizzazione 20 m).**

| vetta | quota reale | cella 0,05° (vecchia) | cella 0,02° (nuova) | bilineare sul punto (nuova) |
| :--- | ---: | ---: | ---: | ---: |
| Etna | 3357 m | 2351 m | **3080 m** | 3017 m |
| Monte Bianco | 4806 m | 3560 m | **4380 m** | 4219 m |
| Monte Rosa (Dufourspitze) | 4634 m | 3675 m | **4180 m** | 4044 m |
| Gran Sasso (Corno Grande) | 2912 m | 1793 m | **2320 m** | 2295 m |
| Vesuvio | 1281 m | 714 m | **1000 m** | 837 m |
| Campeda (altopiano, media ~650 m) | 425-845 m | 657 m | **620 m** | 605 m |

Le quote sono **medie di cella**, non vette: una cella da 2,2 km contiene la
cima e i pendii, quindi resta sotto la punta reale. Il confronto corretto è
con il riferimento 0,01° (che a sua volta vale 3200 m sull'Etna e 4540 m sul
Monte Bianco), non con il punto più alto.

**Massimo del file:** 4380 m (Monte Bianco). Celle sopra 3000 m: 439; sopra
4000 m: 14. Mare (0 m): 269.111 celle su 520.000 (51,8%).

## Confronto risoluzioni × codifica (tutto misurato)

Le righe 0,05/0,033/0,025/0,02/0,01 sono state generate dagli **stessi 182
tile** con lo stesso aggregatore cos(lat), poi codificate con lo stesso
schema (uint8 20 m + LZMA2 delta1 + base64). Il riferimento è la griglia
0,01° (2.080.000 celle) costruita con lo stesso metodo.

| risoluzione | celle | raw (JSON) | **gzip** | Etna | M. Bianco | RMSE quota vs 0,01° | upslope locale | upslope 10 km | up_max 15 km | load (ms) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0,05° | 83.200 | 37.969 B | 28.426 B | 2360 | 3560 | 133,74 m | 0,918 | 0,575 | 0,772 | 10,1 |
| 0,033° | 191.090 | 75.993 B | 57.856 B | 2840 | 4060 | 103,31 m | 0,832 | 0,446 | 0,644 | 19,3 |
| 0,025° | 332.800 | 122.921 B | 94.187 B | 2900* | 4220 | 83,03 m | 0,738 | 0,358 | 0,534 | 32,3 |
| **0,02°** | **520.000** | **181.770 B** | **139.772 B** | **3080** | **4380** | **69,21 m** | **0,640** | **0,299** | **0,434** | **47,2** |
| 0,01° (rif.) | 2.080.000 | 619.698 B | 478.576 B | 3200 | 4540 | 0 | 0 | 0 | 0 | 158,9 |

\* A 0,025° la cella dell'Etna vale **2896 m** prima della quantizzazione e
**2900 m** dopo: il criterio "≥ 2900" è rispettato solo per l'arrotondamento
di +4 m, non è un passaggio robusto. Per questo la scelta non è 0,025°.

> Le colonne `Etna`/`M. Bianco` sono i valori **pubblicati** (uint8, passo
> 20 m); `RMSE quota`, `upslope` e `up_max` sono calcolati sulla griglia a
> 1 m **prima** della quantizzazione, per isolare l'effetto della
> risoluzione. La quantizzazione aggiunge il 2,66% sull'upslope locale e
> l'1,11% su `up_max` (misurato sotto, a parità di risoluzione).

- `upslope locale` = RMS di `w·∇h` (Sobel, passo della griglia) contro il
  riferimento 0,01°, su tutte le celle di terra del riquadro (1.006.121),
  vento fisso `w=(7,−3) m/s`; `upslope 10 km` = differenze centrali a base
  10 km sul campo campionato a 0,01°; `up_max 15 km` = massimo sopravento
  su 4 passi fino a 15 km, la grandezza che entra in `e_pos`.
- La media sulle 8 direzioni di vento non cambia il quadro: upslope locale
  0,919/0,835/0,742/0,644/0; 10 km 0,550/0,425/0,342/0,285/0.
- `load` = `radar_engine.orography.load_grid` a freddo, mediana di 5 letture
  (file non in cache di processo). Il riferimento 0,01° supera i 100 ms.

## Il criterio dell'8%: misurato e NON raggiunto

Il criterio "errore di upslope ≤ 8% contro il riferimento 0,01°" **non è
soddisfatto da nessuna combinazione che stia sotto 150 KB gzip**. Il 0,02°
— la più fine che sta nel budget — misura:

| metrica (vento statico 7,−3) | 0,05° | 0,02° | 0,01° |
| :--- | ---: | ---: | ---: |
| upslope locale | 0,918 | 0,640 | 0 |
| upslope 10 km | 0,575 | 0,299 | 0 |
| up_max 15 km | 0,772 | 0,434 | 0 |
| e_pos (T=5 °C, RH=0,9) | 0,773 | 0,434 | 0 |

L'unica griglia che dà 0% è il riferimento 0,01°, che pesa **478.576 B gzip
(3,2× il budget)** e si carica in **158,9 ms (>100 ms)**. Quindi il criterio
è **incompatibile con i vincoli di dimensione e tempo di caricamento** su
questa sorgente. Le strade per scendere sotto l'8%, dichiarate e non
percorse qui, sono: (a) alzare il budget a ~480 KB gzip; (b) una griglia
**non uniforme** (fine sui rilievi, grossolana su mare/pianura) con un loader
che compone i due livelli; (c) una sorgente a risoluzione più alta e un
formato binario su branch dati. Non è stato costruito un artefatto per far
passare il numero: il 0,02° è la scelta **migliore dentro il budget**, non
un 8% finto.

## Quanto costa la quantizzazione (misurato)

Con 255 livelli lineari 0-5100 m il passo è **20 m** (errore massimo ±10 m).
A parità di risoluzione 0,02°:

| grandezza | errore relativo uint8(20 m) vs 1 m |
| :--- | ---: |
| elevazione | max 10,0 m |
| upslope locale | **2,66%** |
| up_max 15 km | **1,11%** |

Cioè la quantizzazione costa **meno del 3%** del descrittore, mentre la
risoluzione costa il 30-77%. Il vincolo "0-3500 m, passo 13,7 m" non è
applicabile all'Italia: le celle del file arrivano a 4549 m a 0,01° e la
vetta del Monte Bianco è 4806 m; con 0-3500 m si clipperebbero le Alpi. La
scala scelta (0-5100 m, 20 m) copre tutto e resta dentro l'errore dichiarato.

## Effetto sullo stau di oggi (2026-11-28)

Misura **offline** sulla griglia del nowcast di oggi (`nowcast.json`, slot
`2026-11-28T14:50Z`, 1183×1272 a 1,5 km, lead 15 = 15:05Z), con l'ambiente
del dataset `2026-11-28T15:03:59Z` (257 punti, ora 16 locale): vento medio
`u = −0,194`, `v = −9,191 m/s` (northerly, 12,4 m/s), RH media 68,6%,
T media 8,0 °C. La catena è quella di `orography.prepare` (Sobel 1,5 km,
`up_max` 15 km, formula Alpert & Shafir), applicata alle tre griglie.

| metrica (714.903 celle di terra) | 0,05° (vecchia) | 0,02° (nuova) | 0,01° (rif.) |
| :--- | ---: | ---: | ---: |
| errore relativo upslope locale | 0,774 | **0,354** | 0 |
| errore relativo up_max 15 km | 0,597 | **0,231** | 0 |
| errore relativo e_pos | 0,607 | **0,233** | 0 |
| pixel con gate attivo | 39.820 | 39.898 | 39.485 |
| e_pos medio sui gated (mm/h) | 10,66 | **19,42** | 24,17 |

- Il nuovo DEM **dimezza** l'errore del descrittore rispetto al 0,05°:
  −54,3% sull'upslope locale, −61,3% su `up_max`, −61,6% su `e_pos`.
- I pixel con gate attivo restano gli stessi (+78 solo nuovi, **zero persi**):
  il DEM più fine non spegne il gate dove il 0,05° lo accendeva.
- Sui pixel gated da entrambe le griglie il termine `e_pos` cresce in media
  di **+8,86 mm/h** (mediana +6,40, p99 +57,87, max +96,64): è il termine
  **prima** del tetto `max_relative_increase` e del gate sulla pioggia, non
  il delta finale in mm/h.
- Per macroarea (media di `e_pos`, mm/h, 0,05° → 0,02°): Alpi 0,076→0,156;
  Prealpi/Pianura 0,093→0,191; Appennino 1,054→1,983; Sicilia 2,288→3,726;
  Sardegna 0,072→0,111.

**Attenzione al verdetto operativo.** Nel nowcast pubblicato
`orographic_enhancement.enabled = false` dal BUILD=220: l'effetto sul campo
**operativo di oggi è zero**. L'A/B su 4.941 casi appaiati dà orog CSI
**0,4787** contro no_orog **0,4965** (`decision: "l'orografia PEGGIORA il
nowcast"`). Il DEM 0,02° riduce l'errore del descrittore, **non ribalta
l'A/B**: riaccendere il flag resta una decisione separata, da prendere con
la misura aggiornata, non con questo file.

## Verifica storica A contro B (griglia 0,05° del BUILD=215)

B era stato mediato 2×2 per portarlo a 0,1° e confrontato con A sulle 6.422
celle in cui A ha dato:

| metrica | valore |
| :--- | ---: |
| bias | +58,5 m |
| MAE | **210,1 m** |
| RMSE | 322,7 m |
| p90 | 536,2 m |
| max | 1713,5 m |
| correlazione | 0,879 |

**Errore nei 257 punti noti** (valore della cella meno quota della stazione):
A **MAE 19,0 m** — ma è una misura circolare, A è costruito da quelle quote e la
stazione dista al massimo 5 km dal centro cella. B **MAE 89,8 m / max 791 m**:
non è "errore del DEM", è la differenza fra una quota di **punto** e la media di
una cella di **5,5 km** che include versanti. Questa tabella è storica: la B
attuale è 0,02°, non 0,05°.

## Riproducibilità

- La griglia **decodificata** è deterministica: stessi tile + stessa
  risoluzione + stessa scala → stessi 520.000 byte uint8 e stesso
  `encoding.uncompressed_sha256`. Il container LZMA può cambiare byte fra
  versioni di liblzma, quindi il test confronta il contenuto decodificato e
  lo sha non compresso, **non** il container.
- Il test `test_b_riproducibile_dem` rifà *due* generazioni in directory
  temporanee e confronta gli array; il confronto con il file committato è
  condizionato all'hash dei tile (`inputs.tiles_sha256_concat a30d7464…`).
  Se la cache non c'è (`METEORISK_ORO_TILE_CACHE`) il test è **saltato con
  motivo**, mai finto verde.
- `test_b_dimensione_gzip_sotto_150_kb` impone il tetto sul gzip del file.
- Il test `test_b_loader_consumer` carica il file con il vero consumer
  (`radar_engine.orography.load_grid`) e verifica Etna ≥ 2900 m e Monte
  Bianco ≥ 3150 m con il campionamento bilineare.

Test eseguiti (2026-11-28):

- `pytest scripts/tests/test_orography.py -q --capture=sys` → **12 passed**;
- `pytest scripts/tests/radar_engine/test_orography.py -q --capture=sys` →
  **13 passed**;
- `python scripts/geography/build_orography.py --mode dem --res 0.02 …`
  → 520.000 celle, 181.770 B raw / 139.772 B gzip, 9,4 s (cache calda).

## Licenza e attribuzione (testo da riportare)

**A — quote di punto.** Le 257 quote vengono dal campo `elevation` di
Open-Meteo, che documenta l'uso di **Copernicus DEM GLO-90** (release 2021,
90 m). L'Elevation API di Open-Meteo dichiara licenza **CC BY 4.0**
(`openapi/elevation.yml`); i termini generali Open-Meteo valgono per uso non
commerciale (questo progetto è non commerciale). Attribuzione obbligatoria:

> Copernicus DEM - ESA (doi:10.5270/ESA-c5d3d65). Dati: Open-Meteo.com.
> Elaborazione: MeteoRisk - Light.

**B — DEM SRTM.** SRTM è **pubblico dominio** (NASA/NGA, USGS; nessun
copyright rivendicato dal Governo USA ai sensi del 17 U.S.C.), con
attribuzione richiesta:

> SRTM data courtesy of the U.S. Geological Survey.

Il pacchetto tile (Mapzen/joerd su AWS Open Data) non dichiara una licenza
unica: valgono le attribuzioni per fonte di
<https://github.com/tilezen/joerd/blob/master/docs/attribution.md> (per
l'Italia a z8: SRTM; per il mare: ETOPO1, NOAA, pubblico dominio). Citazione
del servizio: "Terrain Tiles, Mapzen (Linux Foundation),
registry.opendata.aws/terrain-tiles".

## Come rigenerare

```bash
# B — è il file pubblicato; una tantum ~18 MB di tile, poi cache
python scripts/geography/build_orography.py --mode dem --res 0.02 \
    --out data/geography/orography_it_0p05.json \
    --tile-cache .cache/orography_tiles \
    --encoding compact --scale-m 20

# A — solo per rifare il confronto, in una directory temporanea (NON committare)
python scripts/geography/build_orography.py --mode points --res 0.1 \
    --out /tmp/orography_it_0p1.json \
    --points data/latest/meteorisk-points.json
```

Il nome `orography_it_0p05.json` è l'**ID storico del prodotto**: non è la
risoluzione. La risoluzione effettiva è sempre in `grid.dlon`/`grid.dlat`
(oggi 0,02°). Un rename a `orography_it_0p02.json` romperebbe i path già
pubblicati e il default di `radar_engine/config.py`; se si farà, va fatto in
una release che aggiorna insieme config, docs e test.

`--generated-at <ISO8601>` scrive un timestamp nel file, ma **rompe** la
riproducibilità: usarlo solo per un'istantanea dichiarata.

## Verdetto

**A non basta come descrittore di upslope — decisione: A scartata, si pubblica
solo B.** Con 257 punti spaziati ~29 km:

- l'errore di interpolazione LOO è 322 m di MAE (max 1450 m);
- il criterio di plausibilità "Po < 100 m" **non** è rispettato al centro cella
  (118 m contro 46 m di B);
- rispetto al DEM l'errore di quota è 210 m di MAE e quello di **gradiente
  15,4 m/km**, più del doppio del gradiente mediano (9,5 m/km);
- il descrittore `w·∇h` ha un **errore del 98%** rispetto al segnale;
- su aree piatte inventa pendenze fino a 36 m/km.

**B 0,02° è il prodotto scelto.** Rispetto al 0,05°:

- Etna **2351 → 3080 m**, Monte Bianco **3560 → 4380 m**, Gran Sasso
  **1793 → 2320 m** (medie di cella, quantizzazione 20 m);
- RMSE di quota contro il riferimento 0,01° **133,74 → 69,21 m**;
- upslope massimo 15 km **0,772 → 0,434** di errore relativo; sul nowcast di
  oggi **0,597 → 0,231**;
- costo **74.939 → 139.772 B gzip** (sempre sotto 150 KB), carico a freddo
  **10,1 → 47,2 ms** (sempre sotto 100 ms).

**Il criterio dell'8% non è raggiunto.** La combinazione che lo raggiunge
(0,01°) costa 478.576 B gzip e 158,9 ms di caricamento: fuori da entrambi i
vincoli. La scelta 0,02° è la migliore **dentro** i vincoli, non un 8%
mascherato.

## Limiti dichiarati

- **B** deriva da SRTM (~90 m) mediato a 2,2 km: descrive la forma del rilievo
  a scala ~2 km, **non** valli e crinali singoli. È un DSM (include vegetazione
  e costruito), non un DTM.
- La cella è una **media**, non una vetta: Etna 3080 m contro 3357 m reali,
  Monte Bianco 4380 contro 4806. La sottostima è strutturale e dichiarata; il
  criterio del prodotto è sulla media di cella, non sulla punta.
- La quantizzazione uint8 a 20 m aggiunge ±10 m (2,66% sull'upslope locale,
  1,11% su `up_max`): misurata, non stimata.
- **B non è validato contro un DEM italiano ufficiale** (es. TINITALY o EU-DEM
  a 25 m): la plausibilità è verificata su punti di controllo e contro la
  griglia 0,01° costruita dagli stessi tile, non con un confronto raster
  indipendente. Un confronto richiederebbe il download di EU-DEM/GLO-90 e
  resta da fare.
- Il riferimento 0,01° è anch'esso SRTM z8 aggregato: non è "la verità", è la
  migliore rappresentazione disponibile con questa sorgente. L'errore residuo
  del 0,02° include la differenza fra due medie della stessa sorgente.
- **Nessuno dei due** è ancora consumato dall'app: il descrittore `w·∇h` esiste
  nel radar-engine/nowcast, ma l'enhancement è spento dal BUILD=220 e l'A/B
  dice che peggiora il CSI. Il DEM è la base dati, non una taratura.
- La soglia di 0,02° e la scala 20 m sono **scelte ingegneristiche**, non
  calibrate su etichette (n=0): nessun enhancement orografico è stato tarato.
