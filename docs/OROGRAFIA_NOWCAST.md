# Effetto orografico applicato al nowcast (novembre 2026)

Applicazione dell'enhancement orografico (componente di pendenza, Alpert & Shafir)
al **campo trasportato** del nowcast SRI, con A/B obbligatorio contro la variante
senza orografia e contro la persistenza.

## 1. Fonte della quota

`data/geography/orography_it_0p05.json` (path storico del prodotto) — schema
`meteorisk.orography/1`. Dal **BUILD=228** la griglia è 0,02° e il campo è
codificato in modo compatto; la versione BUILD=215 (0,05°, int16) resta nella
storia git.

| campo | valore (BUILD=228) |
| :--- | :--- |
| celle | **520.000** (800 x 650, passo **0,02°**, ~2,2 km) |
| byte | **181.770 raw / 139.772 gzip** |
| sha256 | **`17db2272eb8a7afc08f119ca0993ac6469ed869d6c25be511e6375e802fea35e`** |
| dtype | **uint8 quantizzato a 20 m** (0-5100 m), 0 = mare, nessun nodata |
| encoding | **LZMA2 con filtro delta 1, payload base64** (`encoding.codec = lzma2_delta1`) |
| ordinamento | row-major, **lat crescente** (`iy=0 -> 35°N`), 0 = mare |
| sorgente | Terrain Tiles (Mapzen/joerd) z8 → SRTM ~432 m, aggregato cos(lat) |
| licenza | SRTM pubblico dominio (USGS/NASA/NGA), ETOPO1 pubblico dominio (NOAA) |

Storico BUILD=215 (0,05°): 83.200 celle (320 x 260), 254.024 B raw / 74.939 B
gzip, sha256 `cc823da4…f021cc`, int16.

`radar_engine/orography.py::load_grid` decodifica il payload (base64 → LZMA2 →
uint8 × 20 m) e gira l'array in **north-up** una volta sola (riga 0 = lat
massima): da lì in poi la convenzione interna è unica; accetta anche il vecchio
vettore int16. Controllo di orientamento nei test: i campioni dichiarati nel
file (`etna_vetta` 3080 m, `monte_bianco_vetta` 4380 m, `pianura_padana` 40 m,
`adriatico_medio` 0 m) tornano esatti, cella per cella.

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

`config.py` → `orographic_enhancement`, **default `enabled: false` dal BUILD=220** (era `true` nel BUILD=216-219; il motivo è la misura del §7):

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
5. Il default era `enabled: true` per richiesta dell'utente fino al BUILD=219; dal BUILD=220 è `false` per la misura del §7. `nowcast.json`
   pubblica l'esito (`beats_persistence_no_orog` / `_orog`) e la decisione sul
   default va presa con lui sulla base di questi numeri.

## 7. Decisione: orografia SPENTA dal BUILD=220 (misura del 25/11/2026)

L'A/B del §5 era stato misurato su un campo con il **12-16% di eco ferma**
(vettori azzerati dal cap). Dopo il fix del moto del BUILD=218
(`speed_cap_mode = replaced_by_neighbor_median`) il campo è cambiato, quindi la
misura è stata **rifatta** sullo stesso strumento e sugli **stessi 42 slot
reali consecutivi** dell'archivio `nowcast-archive`
(`2026-11-24T23:05Z` → `2026-11-25T05:55Z`, tutti con il moto nuovo):
363 casi appaiati (3 lead x 3 soglie), 726 righe `verified`, 0 `not_informative`,
0 `not_verifiable`, 101 download SRI, 953,3 s.

| lead | soglia | CSI orog | CSI no_orog | ΔCSI | POD orog | POD no_orog | FAR orog | FAR no_orog | bias orog | bias no_orog | CSI pers. |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 15 | 0,5 | 0,6936 | **0,7176** | −0,0240 | 0,8228 | 0,8010 | 0,1847 | 0,1268 | 1,009 | 0,917 | 0,6887 |
| 15 | 2,0 | 0,4448 | **0,5082** | −0,0634 | 0,7434 | 0,6524 | 0,4745 | 0,3031 | 1,415 | 0,936 | 0,4799 |
| 15 | 5,0 | 0,2821 | **0,3469** | −0,0648 | 0,6145 | 0,4949 | 0,6572 | 0,4629 | 1,793 | 0,921 | 0,3061 |
| 30 | 0,5 | 0,6095 | **0,6169** | −0,0074 | 0,7329 | 0,7066 | 0,2164 | 0,1707 | 0,935 | 0,852 | 0,6000 |
| 30 | 2,0 | 0,3526 | **0,3679** | −0,0153 | 0,6053 | 0,5027 | 0,5421 | 0,4214 | 1,322 | 0,869 | 0,3736 |
| 30 | 5,0 | 0,1850 | **0,1986** | −0,0136 | 0,4132 | 0,3025 | 0,7492 | 0,6337 | 1,647 | 0,826 | 0,2050 |
| 60 | 0,5 | **0,5095** | 0,5050 | +0,0045 | 0,6217 | 0,5941 | 0,2616 | 0,2290 | 0,842 | 0,771 | 0,5102 |
| 60 | 2,0 | **0,2600** | 0,2448 | +0,0152 | 0,4532 | 0,3504 | 0,6212 | 0,5518 | 1,196 | 0,782 | 0,2786 |
| 60 | 5,0 | **0,1064** | 0,0995 | +0,0069 | 0,2417 | 0,1575 | 0,8402 | 0,7874 | 1,513 | 0,741 | 0,1360 |

- **Pooled**: orog CSI **0,4915** (POD 0,6808, FAR 0,3614, bias 1,066) contro
  no_orog **0,5198** (POD 0,6332, FAR 0,2562, bias 0,851): Δ −0,0283.
- **Per slot**: no_orog vince **42 su 42** (100,0%), orog 0, pari 0;
  ΔCSI medio per slot −0,0294 (da −0,0038 a −0,0668).
- **Per cella**: 119 vinte da orog, 244 da no_orog, 0 pari.
- **Persistenza** (CSI pooled 0,5121): con l'orografia il nowcast **perde**
  (0,4915), senza orografia **vince** (0,5198) → `beats_persistence_orog =
  false`, `beats_persistence_no_orog = true`.
- **Meccanismo**: l'enhancement alza il POD (+0,048) ma aggiunge molti più
  falsi allarmi (FAR +0,105) e sovra-prevede (bias 1,066 contro 0,851). Il segno
  è negativo a 15 e 30 minuti e si annulla a 60: la correzione nuoce dove il
  trasporto è già buono.
- **Confronto con la misura pre-fix** (219 casi, moto con zeri): orog 0,5054
  contro no_orog 0,5333 (FAR 0,3415 contro 0,2319). Il fix del moto **non**
  ribalta il verdetto: lo rende più netto.

**`delta_mean_mm_h` / `delta_max_mm_h` con il moto nuovo** (produzione, slot
reali): 0,909-1,190 / 14,97-22,71 mm/h con il moto vecchio (20:55-21:35Z) contro
1,049-1,228 / 14,84-22,69 con il moto nuovo (22:45-23:35Z) e 1,143-1,203 /
18,6-22,7 a 02:55-03:45Z; i pixel corretti passano da 74.331-91.097 a
104.397-125.143. ⚠ Nello stesso intervallo cambia anche l'ambiente NWP (vento
medio a 10 m 4,64-4,80 → 5,11-6,19 m/s; `gated_pixels` 336.716-381.142 →
424.964-468.332, conteggio **indipendente dal moto**), quindi l'aumento **non è
attribuibile al solo fix del moto**: dichiarato, non attribuito.

**Limiti dichiarati.** L'A/B usa l'ambiente NWP del dataset `2026-11-25T05:04Z`
(l'unico disponibile) per tutti gli slot, mentre in produzione ogni slot usa il
dataset del momento: il confronto resta appaiato (stesse frame, stesso trasporto,
cambia solo la correzione) ma i valori assoluti non sono quelli del run
operativo. Il campione è una notte sola, senza stau forte. Il flag è
reversibile con una riga e `nowcast_verify` continua a misurare ON e OFF.

**Effetto del flag su WebP e JSON** (riconfermato su uno slot reale,
`2026-11-25T05:05Z`): con `enabled: false` le 12 WebP sono **identiche** a quelle
calcolate con `compute(..., orographic=False)` (12 su 12, sha256) e **diverse** da
quelle con l'orografia attiva (12 su 12); il blocco `orographic_enhancement`
pubblica `enabled: false`, `applied: false`, `null_reason: disabled_by_config`.

## 8. Effetto del DEM 0,02° sullo stau del 2026-11-28 (misura offline)

La griglia del BUILD=228 è stata provata sulla griglia reale del nowcast di
oggi (`data/radar/nowcast.json`, slot `2026-11-28T14:50Z`, lead 15 =
`15:05Z`, 1183×1272 a 1,5 km) con l'ambiente del dataset
`2026-11-28T15:03:59Z` (257 punti, ora 16 locale): vento medio `u = −0,194`,
`v = −9,191 m/s` (northerly, 12,4 m/s), RH media 68,6%, T media 8,0 °C.
Catena identica a `orography.prepare` (Sobel 1,5 km, `up_max` 15 km,
Alpert & Shafir). Riferimento: la griglia 0,01° costruita dagli stessi tile.

| metrica (714.903 celle di terra) | 0,05° (vecchia) | 0,02° (nuova) | 0,01° (rif.) |
| :--- | ---: | ---: | ---: |
| errore relativo upslope locale | 0,774 | **0,354** | 0 |
| errore relativo `up_max` 15 km | 0,597 | **0,231** | 0 |
| errore relativo `e_pos` | 0,607 | **0,233** | 0 |
| pixel con gate attivo | 39.820 | 39.898 | 39.485 |
| `e_pos` medio sui gated (mm/h) | 10,66 | **19,42** | 24,17 |

- Il nuovo DEM **dimezza** l'errore del descrittore: −54,3% sull'upslope
  locale, −61,3% su `up_max`, −61,6% su `e_pos`.
- Il gate non perde pixel: +78 solo nuovi, **zero** spenti.
- Sui pixel gated da entrambe le griglie il termine `e_pos` cresce in media
  di **+8,86 mm/h** (mediana +6,40, p99 +57,87, max +96,64). È il termine
  **prima** del tetto `max_relative_increase` e del gate sulla pioggia: non è
  il delta finale in mm/h.
- Per macroarea (media `e_pos`, mm/h, 0,05° → 0,02°): Alpi 0,076→0,156;
  Prealpi/Pianura 0,093→0,191; Appennino 1,054→1,983; Sicilia 2,288→3,726;
  Sardegna 0,072→0,111.

**Il nowcast pubblicato di oggi ha `orographic_enhancement.enabled = false`:
l'effetto operativo è zero.** La misura dice che il DEM 0,02° è un
descrittore migliore, non che l'enhancement vada riacceso: l'A/B aggiornato
(`nowcast_verification.json`, 4.941 casi appaiati) dà orog CSI **0,4787**
contro no_orog **0,4965** (`decision: "l'orografia PEGGIORA il nowcast"`),
in linea con la misura dei 42 slot del §7. La decisione sul flag resta
separata e va presa con una misura prospettica, non con questo file.
