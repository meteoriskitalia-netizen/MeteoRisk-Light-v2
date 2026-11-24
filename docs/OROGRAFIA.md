# OROGRAFIA SEMPLIFICATA — `data/geography/orography_it_*`

**Scopo.** Avere un campo di quota stabile dentro `main`, senza scaricare
450-580 MB di DEM a ogni uso. Due artefatti, misurati entrambi: **in `main` è
pubblicata SOLO la variante B** (DEM). La variante A (0,1° dalle 257 quote) è
**scartata** — errore del 98% su `w·∇h` — e resta qui documentata perché è il
confronto che giustifica B.

| file | griglia | celle | raw | gzip | fonte | sha256 |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| `orography_it_0p1.json` — **NON pubblicata** | 0,1° (≈11 km), lon 4-20 / lat 35-48 | 20.800 | 128.184 B | 13.361 B | 257 quote di punto (Open-Meteo / Copernicus GLO-90) interpolate IDW | `75290b10…0f8fdeebe` |
| `orography_it_0p05.json` — **in `main`** | 0,05° (≈5,5 km), lon 4-20 / lat 35-48 | 83.200 | 254.024 B | **74.939 B** | DEM SRTM (~90 m) ridotto, Terrain Tiles z8 | `cc823da416a81eb36c1cb68b642f006e9c1244b04b2d6d15ef6d449aa3f021cc` |

Generatore: `scripts/geography/build_orography.py`. Test: `scripts/tests/test_orography.py`.
La variante A si può ancora generare (`--mode points`) ma **non va committata**.

## Formato

```json
{
  "schema": "meteorisk.orography/1",
  "grid": {"registration": "cell_centered", "lon0": 4.0, "lat0": 35.0,
           "lon1": 20.0, "lat1": 48.0, "dlon": 0.1, "dlat": 0.1,
           "nx": 160, "ny": 130,
           "ordering": "row-major, lat crescente (iy=0 -> lat0), poi lon"},
  "units": "m", "dtype": "int16",
  "elevations_m": [ ... nx*ny interi ... ]
}
```

- Griglia **cell-centered**: la cella `(ix, iy)` copre
  `[lon0+ix*dlon, lon0+(ix+1)*dlon)` × `[lat0+iy*dlat, lat0+(iy+1)*dlat)`,
  centro `lon0+(ix+0,5)*dlon`, `lat0+(iy+0,5)*dlat`.
- Indice lineare: `iy*nx + ix`, con `iy=0` alla latitudine `lat0`.
- `-32768` = nodata (solo in A: nessuna stazione entro il raggio di supporto).
- `generated_at` è **null** per costruzione: nessun timestamp nel file, così la
  rigenerazione è byte-identica. La provenienza sta in `inputs` (sha256 degli
  input) e nella storia git.

## A) Le 257 quote di punto — `orography_it_0p1.json`

**Metodo.** IDW locale, potenza 2, raggio 60 km, distanza equirettangolare in km
(lat media 41,5°). Oltre 60 km dalla stazione più vicina la cella è nodata.

**Perché locale e non su tutte le stazioni.** IDW su tutte le 257 stazioni dava
**196 m** in Pianura Padana (45,0 N / 10,0 E) contro ~45 m reali, perché le
stazioni alpine a 150 km entravano nella media. Con il taglio a 60 km, nel punto
(45,0 / 10,0) il valore scende a **42,7 m**; il centro della cella 0,1° che lo
contiene (44,95 / 9,95) vale **117,7 m** — la differenza fra i due è la stessa
liscia del campo, non un errore di calcolo. Il peso al bordo del taglio è
`1/60² ≈ 2,8e-4`, quindi l'ingresso/uscita di una stazione dal raggio non
produce salti visibili.

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
stazione campiona la cima). Nota: sul centro cella della Pianura Padana il
criterio "Po < 100 m" **non** è rispettato (118 m); nel punto esatto 45,0/10,0
il valore è 42,7 m. È la misura della liscezza del campo, non un errore di
calcolo.

**Copertura.** 6.422 celle su 20.800 (30,9%) hanno dato; le altre 14.378 sono
mare o territorio estero a più di 60 km da una stazione. Sui **23.374 vertici**
dei confini delle 107 province, **142 (0,61%)** cadono su nodata e sono **tutti
su isole minori**, in 5 province: **ME 44** (Eolie), **AG 37** (Lampedusa e
Linosa), **LT 27** (Ponza e Ventotene), **TP 25** (Pantelleria), **PA 9**
(Ustica). Nessun vertice della terraferma è scoperto: le isole minori non hanno
una stazione entro 60 km (Ventotene: 68,5 km).

## B) DEM ridotto — `orography_it_0p05.json`

**Metodo.** Terrain Tiles (Mapzen/joerd, AWS Open Data) a **zoom 8**: sul suolo
italiano la fonte è **SRTM 1/3 arcsec (~90 m)**, pixel a 45°N ≈ 432 m. Ogni
cella 0,05° è la **media pesata cos(lat)** dei pixel DEM che ci cadono dentro
(mediana **108 pixel/cella**); i valori negativi (batimetria ETOPO1) sono
azzerati a 0 m, quindi il mare è 0.

**Costo del download (una tantum).** 182 tile, **18.713.724 B (17,8 MiB)**,
**154 s** la prima volta (150 s di download); dalle successive è **~5 s** con la
cache. Il file che resta in `main` pesa **254.024 B raw / 74.939 B gzip**.

**Valori di controllo (centro cella).** Alpi/Valtellina 2685 m · Monte Bianco
3259 m · **Monte Rosa 3675 m (massimo del file**, cella 7,875/45,925) · Gran
Sasso 1400 m (cella a SE della vetta; la cella del massiccio vale 1925 m) ·
Pianura Padana **46 m** · Sicilia interna 554 m · Sardegna 150 m · Adriatico
**0 m**. Nessuna cella nodata (`cells_filled_from_neighbours: 0`). Celle sopra
3000 m: 51; sopra 2500 m: 435; a 0 m (mare): 40.976.

## Verifica A contro B (stessa griglia 0,1°)

B è stato mediato 2×2 per portarlo a 0,1° e confrontato con A sulle 6.422 celle
in cui A ha dato:

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
una cella di **5,5 km** che include versanti.

## Quello che conta per il descrittore: il gradiente

Gradiente a 0,1° con differenze centrali, su 5.873 celle comuni:

| | mediana \|∇h\| | p90 \|∇h\| | max |
| :--- | ---: | ---: | ---: |
| A (257 punti) | 6,73 m/km | 24,91 | 102,86 |
| B (DEM) | 9,51 m/km | 41,77 | 112,46 |

**Errore del gradiente A−B: MAE 15,44 m/km, RMSE 22,56 m/km, p90 38,16, max
125,24** — cioè l'errore è **2,4 volte** il gradiente mediano del segnale.

Nelle **1.653 celle piatte** secondo B (\|∇h\| < 1 m/km e quota < 300 m), A mostra
comunque \|∇h\| mediano **2,31 m/km**, p90 10,72, max 36,27: pendii che non
esistono, prodotti dalla composizione delle stazioni vicine.

**Descrittore `upslope_component = w·∇h`** con vento di esempio `w=(7,−3) m/s`:

| | valore |
| :--- | ---: |
| RMS di upslope da B (il segnale) | 0,1365 m/s |
| errore A−B | MAE 0,0804 · **RMSE 0,1332** · max 0,9073 m/s |
| errore relativo | **98%** |

## Riproducibilità

- Determinismo: due generazioni con gli **stessi input** producono gli **stessi
  byte**. Verificato in sessione per A (`75290b10…`) e B (`cc823da4…`), e
  verificato dal test a ogni run.
- Il test `test_riproducibile_points` rifà *due* generazioni in directory
  temporanee e confronta i byte; il confronto con il file committato è
  **condizionato** all'hash degli input (`inputs.points_sha256`). Motivo:
  `data/latest/meteorisk-points.json` viene riscritto dalla pipeline ogni ~20
  minuti, quindi un sha diverso dopo un aggiornamento delle quote è legittimo e
  non è una regressione.
- `test_riproducibile_dem` fa lo stesso con i tile (immutabili, build 2017,
  `tiles_sha256_concat a30d7464…`); se la cache non c'è (`METEORISK_ORO_TILE_CACHE`)
  il test è **saltato con motivo**, mai finto verde.
- Il test non scrive mai nel repo: solo in directory temporanee.

Test eseguiti: `pytest scripts/tests/test_orography.py -q --capture=sys` →
**10 passed** (schema e dimensioni, limiti fisici, nodata coerente, plausibilità,
copertura confini, riproducibilità A e B, accordo A-B).

## Licenza e attribuzione (testo da riportare)

**A — quote di punto.** Le 257 quote vengono dal campo `elevation` di
Open-Meteo, che documenta l'uso di **Copernicus DEM GLO-90** (release 2021,
90 m). L'Elevation API di Open-Meteo dichiara licenza **CC BY 4.0**
(`openapi/elevation.yml`); i termini generali Open-Meteo valgono per uso non
commerciale (questo progetto è non commerciale). Attribuzione obbligatoria:

> Copernicus DEM - ESA (doi:10.5270/ESA-c5d3d65). Dati: Open-Meteo.com.
> Elaborazione: MeteoRisk - Light.

**B — DEM SRTM.** SRTM è **pubblico dominio** (NASA/NGA, USGS; nessun copyright
rivendicato dal Governo USA ai sensi del 17 U.S.C.), con attribuzione richiesta:

> SRTM data courtesy of the U.S. Geological Survey.

Il pacchetto tile (Mapzen/joerd su AWS Open Data) non dichiara una licenza unica:
valgono le attribuzioni per fonte di
<https://github.com/tilezen/joerd/blob/master/docs/attribution.md> (per l'Italia a
z8: SRTM; per il mare: ETOPO1, NOAA, pubblico dominio). Citazione del servizio:
"Terrain Tiles, Mapzen (Linux Foundation), registry.opendata.aws/terrain-tiles".

## Come rigenerare

```bash
# B — è il file pubblicato; una tantum ~18 MB di tile, poi cache
python scripts/geography/build_orography.py --mode dem --res 0.05 \
    --out data/geography/orography_it_0p05.json \
    --tile-cache .cache/orography_tiles

# A — solo per rifare il confronto, in una directory temporanea (NON committare)
python scripts/geography/build_orography.py --mode points --res 0.1 \
    --out /tmp/orography_it_0p1.json \
    --points data/latest/meteorisk-points.json
```

`--generated-at <ISO8601>` scrive un timestamp nel file, ma **rompe** la
riproducibilità byte-identica: usarlo solo per un'istantanea dichiarata.

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

A resta utile solo come **campo qualitativo di larga scala** ("dove sono i
rilievi"), non come base per un termine orografico. **B è il prodotto da usare**:
parte da un DEM vero, costa 18 MB una volta sola e lascia 75 KB gzip in `main`.

## Limiti dichiarati

- **B** deriva da SRTM (~90 m) mediato a 5,5 km: descrive la forma del rilievo a
  scala 5-6 km, **non** valli e crinali singoli. È un DSM (include vegetazione e
  costruito), non un DTM.
- **B** non è validato contro un DEM italiano ufficiale (es. TINITALY o EU-DEM
  a 25 m): la plausibilità è verificata su punti di controllo, non con un
  confronto raster. Un confronto richiederebbe il download di EU-DEM/GLO-90 e
  resta da fare.
- **A** non ha dato sulle **isole minori** (142 vertici di confine, 0,61%, in 5
  province: Eolie, Lampedusa/Linosa, Ponza/Ventotene, Pantelleria, Ustica) né su
  mare/estero oltre 60 km da una stazione. La terraferma è coperta.
- **Nessuno dei due** è ancora consumato dall'app: il descrittore `w·∇h` esiste
  come base dati, non come campo calcolato a runtime.
- La soglia di 0,05° e lo zoom 8 sono **scelte ingegneristiche**, non calibrate
  su etichette (n=0): nessun enhancement orografico è stato tarato.
