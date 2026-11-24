# Effetto orografico applicato al nowcast (novembre 2026)

Applicazione dell'enhancement orografico (componente di pendenza, Alpert & Shafir)
al **campo trasportato** del nowcast SRI, con A/B obbligatorio contro la variante
senza orografia e contro la persistenza.

## 1. Fonte della quota

`data/geography/orography_it_0p05.json` — schema `meteorisk.orography/1`, pubblicata
su `main` (commit `3a5975f9`, BUILD=215):

| campo | valore |
| :--- | :--- |
| celle | 83.200 (320 x 260, passo 0,05°) |
| byte | 254.024 raw / 74.939 gzip |
| sha256 | `cc823da416a81eb36c1cb68b642f006e9c1244b04b2d6d15ef6d449aa3f021cc` |
| dtype | int16, metri, nodata `-32768` |
| ordinamento | row-major, **lat crescente** (`iy=0 -> 35°N`), 0 = mare |
| sorgente | Terrain Tiles (Mapzen/joerd) z8 → SRTM ~432 m, aggregato cos(lat) |
| licenza | SRTM pubblico dominio (USGS/NASA/NGA), ETOPO1 pubblico dominio (NOAA) |

`radar_engine/orography.py::load_grid` gira l'array in **north-up** una volta sola
(riga 0 = lat massima): da lì in poi la convenzione interna è unica.
Controllo di orientamento nei test: i tre campioni dichiarati nel file
(`monte_bianco` 3259 m, `pianura_padana` 46 m, `adriatico_medio` 0 m) tornano
esatti, cella per cella.

> Nota sulle centraline (verifica separata, **non usata** per la griglia):
> il payload `data/stations/latest.json` **non** contiene alcuna quota. La
> sorgente MeteoHub espone però `B07030` = *HEIGHT OF STATION GROUND ABOVE MEAN
> SEA LEVEL* (verificato il 2026-11-24 con `stationDetails=true&onlyStations=true`,
> una stazione per richiesta): **5.027/5.027** stazioni del payload hanno il
> valore (100%), licenza CC BY 4.0 (stessa del dato). È la quota del suolo alla
> stazione, non un DEM: la griglia definitiva resta SRTM.

## 2. Metodo

```
upslope   = u·∂h/∂x + v·∂h/∂y                                  [m/s]
e_s(T)    = 610,94 · exp(17,625·T / (T + 243,04))              [Pa]   (Magnus/Tetens)
E         = RH · e_s(T) · upslope_sopravvento / (R_v · T_k) · 3600  [mm/h] (R_v = 461,5)
```

Il risultato numerico dell'espressione è in kg/(m³·h), che **coincide** con mm/h
(1 kg/m³ su 1 m di spessore = 1 mm d'acqua). Verifica a mano: RH 0,9, T 280 K,
v·∇h 0,1 s⁻¹ → **2,48 mm/h** (test `test_formula_order_of_magnitude`).

**Dove si applica** (`nowcast_field.py`):

| passo | cosa |
| :--- | :--- |
| `compute(..., orographic=None)` | override del flag per l'A/B |
| `orography.prepare` | una volta per run: quota, vento, RH, T, gate |
| `orography.apply` nel loop dei lead | sul frame **trasportato**, dopo `semi_lagrangian` |
| statistiche e rampa alpha | `_lead_stats` e `_rgba` vedono il campo corretto |

Il cap 110 km/h riguarda il **vettore di moto** ed è a monte: invariato.

**Gate dichiarati** (nessuno tarato su casi):
1. media 3x3 della quota ≥ 20 m;
2. RH ≥ 0,80;
3. `upslope` positivo ≥ 0,0005 m/s, preso come **massimo su un intorno
   sopravvento fino a 15 km** (4 passi lungo −vento);
4. il pixel ha precipitazione trasportata ≥ 0,05 mm/h: **l'enhancement
   intensifica la pioggia prevista, non ne inventa** dove il radar non vede nulla.

**Sottovento**: stessa formula con `|upslope|`, in attenuazione, con pavimento
dichiarato al **50%** del campo trasportato.

**Tetto del termine positivo** (`max_relative_increase` = 1,0 → al massimo +100%):
senza tetto la formula aggiungeva fino a **32 mm/h su un pixel con 0,3 mm/h**
(misurato), che non è enhancement ma pioggia inventata. I pixel in cui il tetto
morde sono contati (`capped_pixels`).

**Livello del vento** (`wind_level`): default **`10m`** (best_match).
La modalità `850_700` (media **vettoriale** pesata per massa 850/700 hPa, pesi
150/125 hPa come `nwp_motion.LEVEL_MASS_WEIGHTS`) è implementata e misurata, ma
**peggiora** (§5): resta disponibile come parametro, non come default.

## 3. Config e JSON

`config.py` → `orographic_enhancement`, **default `enabled: true`**:

```
enabled, grid_file, env_points_file, env_metadata_file, max_env_age_minutes (240),
min_mean_elev_m (20), rh_threshold (0.80), min_upslope_ms (0.0005), upwind_km (15),
upwind_steps (4), lee_floor_frac (0.50), max_relative_increase (1.0),
wind_level ("10m" | "850_700"), wind_level_weights ({850_700: [150, 125]}),
rain_min_mmh (0.05), idw_neighbors (8), idw_max_km (200), rv (461.5)
```

`nowcast.json` → `orographic_enhancement`:
`enabled`, `applied`, `null_reason`, `params`, `limits`, `reference`,
`env` (ora NWP, età del dataset, `wind_level`), `wind`, `upslope_ms{mean,p99,max,min}`,
`enhancement_mm_h{max,mean_gated,levy_max}`, `delta_mean_mm_h`, `delta_max_mm_h`,
`cells_corrected`, `capped_pixels`, `gated_pixels`, `lee_pixels`, `per_lead{...}`.

Se quota o ambiente mancano (o l'ambiente è più vecchio di 240 min):
`applied: false` + `null_reason` (`orography_grid_missing`, `env_files_missing`,
`env_stale_<n>min`, `env_slot_outside_horizon`, `disabled_by_config`), **mai una
correzione a zero spacciata per calcolata**.

## 4. Casi reali (24/11/2026, SRI DPC reale, 12 frame)

`prepare` costa **~3-4 s** sul raster 1400x1200; il `compute` completo passa da
**4,4 s a 8,4 s** (misurato, stesso slot).

| caso | ora UTC | vento best_match 10 m | Alpi (media / max / celle stau) | Pianura (media / p95) | delta medio / max | celle corrette |
| :--- | :--- | :--- | :--- | :--- | :--- | ---: |
| A stau S | 14:00 | 175,7° 5,9 m/s | +0,0042 / **+2,06** / 49.358 | −0,0099 / 0,083 | 0,222 / 2,62 mm/h | 3.960 |
| B föhn NW | 06:00 | 330,2° 4,6 m/s | **−0,0035** / min **−3,12** / 59.018 lee | +0,0011 / 0,100 | 0,246 / 1,41 mm/h | 242 |
| C neutro | 17:00 | 121,6° 3,8 m/s | +0,0007 / +1,60 / 43.937 | **+0,00002** / 0,083 | 0,194 / 3,97 mm/h | 14.641 |

Sanity check: **Pianura Padana ≈ 0** (media 2·10⁻⁵ m/s nel caso neutro, p95 0,083
contro un massimo alpino di 1,6-2,1 m/s); **föhn sottovento negativo** (caso B,
Alpi −3,12 m/s, 59.018 celle in ombra). Lo stau non è un descrittore: nel caso A
il campo cambia su 3.960 pixel con +0,22 mm/h medi e +2,62 mm/h di picco, e il
massimo del frame +30' resta 8,45 mm/h in entrambe le varianti (il pixel di picco
non è su un pendio); il tetto resta quello dichiarato, +100% del campo trasportato.

WebP: con `enabled: false` il blocco dice `applied: false` e le 12 immagini sono
prodotte dal solo trasporto; con `enabled: true` **tutte e 12 cambiano** (verificato
sugli sha256 dei file: 0 identici su 12).

## 5. A/B (pipeline `nowcast_verify.py`)

Righe della serie con `variant` = `orog` / `no_orog` (+ `persistenza` come
riferimento), chiave `orography_ab` nel documento. Ogni slot è ricalcolato **due
volte dalle stesse frame** (cambia solo la correzione, non il flusso ottico).

**Campione**: 6 slot reali 17:45-19:25Z, **54 casi appaiati** (6 slot x 3 lead x 3
soglie), 108 righe verificate, 53 download SRI, ~112-118 s per variante.

### 5.1 Vento a 10 m (default)

| lead | soglia | CSI orog | CSI no_orog | CSI persistenza |
| ---: | ---: | ---: | ---: | ---: |
| 15 | 0,5 | 0,5282 | **0,5292** | 0,5085 |
| 15 | 2,0 | 0,2985 | **0,3328** | 0,3178 |
| 15 | 5,0 | 0,2080 | **0,2270** | 0,2444 |
| 30 | 0,5 | **0,3998** | 0,3905 | 0,3830 |
| 30 | 2,0 | 0,1944 | **0,2087** | 0,1860 |
| 30 | 5,0 | 0,0964 | **0,1020** | 0,1450 |
| 60 | 0,5 | **0,2797** | 0,2646 | 0,2892 |
| 60 | 2,0 | **0,0905** | 0,0754 | 0,0852 |
| 60 | 5,0 | **0,0447** | 0,0445 | 0,0875 |

Pooled: orog **CSI 0,3374** (POD 0,4566, FAR 0,4362) · no_orog **CSI 0,3392**
(POD 0,4188, FAR 0,3593) · persistenza 0,3381.
Casi vinti: orog 29, no_orog 25, pari 0.
Verdetto: `beats_persistence_orog: false`, `beats_persistence_no_orog: true`,
`orog_beats_no_orog: false`.

### 5.2 Vento 850/700 hPa (sperimentale, **non** default)

Stessi 6 slot, stesse frame, stessa griglia; cambia solo il vento:
**22-23 m/s in quota** contro 3,8-4,3 m/s a 10 m.

| lead | soglia | CSI orog 850/700 | CSI no_orog | CSI persistenza |
| ---: | ---: | ---: | ---: | ---: |
| 15 | 0,5 | 0,5157 | **0,5292** | 0,5085 |
| 15 | 2,0 | 0,2957 | **0,3328** | 0,3178 |
| 15 | 5,0 | 0,1830 | **0,2270** | 0,2444 |
| 30 | 0,5 | **0,3935** | 0,3905 | 0,3830 |
| 30 | 2,0 | 0,1919 | **0,2087** | 0,1860 |
| 30 | 5,0 | 0,0841 | **0,1020** | 0,1450 |
| 60 | 0,5 | **0,2777** | 0,2646 | 0,2892 |
| 60 | 2,0 | **0,0900** | 0,0754 | 0,0852 |
| 60 | 5,0 | 0,0379 | **0,0445** | 0,0875 |

Pooled: orog **CSI 0,3301** (POD 0,4436, FAR 0,4367) · no_orog 0,3392 · persistenza 0,3381.
Casi vinti: orog 21, no_orog 33. Delta applicato: medi **0,40-0,82 mm/h**,
massimi **11,5-23,9 mm/h**, 22.524-48.263 celle corrette per slot (a 10 m:
0,43-0,82 mm/h, 9,2-16,0 mm/h, 22.426-47.976 celle).

**Esito del test richiesto**: 850/700 **peggiora** (−0,0091 di CSI contro −0,0018
del 10 m, e 21 casi vinti contro 29). Con 22 m/s la formula produce un delta che
il radar non conferma: il FAR resta 0,437 mentre il POD non sale (0,444 contro
0,457 del 10 m). Quindi **il default resta 10 m**: non era il vento a 10 m il
limite, era l'assenza di stau nel campione (nessuno slot con S/SW ≥ 10 m/s).

## 6. Cosa manca per una conclusione statistica

1. **Casi con stau vero**: il campione non contiene vento sostenuto contro i
   rilievi (max 7,2 m/s, media 3,8). Servono slot con S/SW ≥ 10 m/s sulle Alpi.
2. **Numerosità**: 6 slot non distinguono 0,3374 da 0,3392. Con ~40-60 slot la
   differenza pooled avrebbe senso; con 9 celle (3 lead x 3 soglie) il minimo
   dichiarabile resta 20 casi per cella.
3. **Calibrazione del tetto** (+100%) e del **pavimento** sottovento (50%): oggi
   sono scelte ingegneristiche dichiarate, non stime.
4. **Vento**: 850/700 già provato e scartato sui numeri; resta da provare il vento
   a 925 hPa (livello di condensazione tipico in inverno) e una media pesata
   sulla sola componente perpendicolare al rilievo.
5. Il default resta `enabled: true` per richiesta dell'utente; `nowcast.json`
   pubblica l'esito (`beats_persistence_no_orog` / `_orog`) e la decisione sul
   default va presa con lui sulla base di questi numeri.
