# Mosaico DPC + OPERA CIRRUS e copertura Doppler

Stato: **1.2.4.27**. Moduli: `scripts/radar_engine/source_opera.py`,
`mosaic.py`, `doppler_coverage.py`, `collimation.py`.
Banco di misura: `scripts/validation/opera_mosaic_bench.py`.

Tutti i numeri di questo documento sono **misurati** il 2026-11-10 sul repo
`MeteoRisk-Light-v2` (frame reali DPC + OPERA), non stimati. I comandi di
riproduzione sono in fondo.

---

## 1. Sorgente OPERA CIRRUS

| Voce | Valore misurato |
| :--- | :--- |
| Endpoint | `https://s3.waw3-1.cloudferro.com/openradar-24h/` (S3 aperto, anonimo) |
| Chiave | `YYYY/MM/DD/OPERA/COMP/OPERA@YYYYMMDDTHHMM@0@DBZH.tiff` |
| Formato | GeoTIFF COG, 2 bande (DBZH + quality), float32, nodata `-9999000` |
| Griglia | 3800 x 4400 px, 1000 m, LAEA `lat_0=55 lon_0=10 x_0=1950000 y_0=-2100000` |
| Cadenza / semantica | 5 minuti, **MAX su 10 minuti** |
| Licenza | **CC BY 4.0** — attribuzione: `OPERA CIRRUS composite - (c) EUMETNET OPERA, CC BY 4.0 (via Open Radar Data / MeteoGate)` |
| Listing | 560 chiavi/giorno, XML 151.467 byte, 0,6-1,0 s |

**Solo echi.** Il composito pubblica un pixel solo dove c'e' eco: un pixel
coperto e asciutto e' `nodata`. Misurato sul frame `2026-11-10T12:00Z` nella
finestra italiana: 20,27% di pixel finiti, 1.700 zeri su 389.930 finiti
(0,44%). Quindi `nodata` **non** significa "fuori copertura" e il codice non
lo interpreta come "assenza di pioggia misurata".

### Lettura efficiente (COG via `/vsicurl/`)

Si legge **solo la finestra** che contiene il bbox configurato
(`opera.bbox_wgs84`, default `6-20E / 35-48N` = **1.294 x 1.486 px** su
3.800 x 4.400).

| Misura | Valore |
| :--- | :--- |
| Apertura (`rasterio.open("/vsicurl/...")`) | 0,50-0,56 s |
| Lettura finestra | 0,89-1,07 s |
| Richieste Range | **5** |
| Byte trasferiti | **771.852** su 3.850.164 del file (**20,0%**) |
| Riproiezione sulla griglia DPC (1200x1400) | **0,047-0,050 s** |

Il conteggio dei byte si fa in un **sottoprocesso** (`measure_frame_bytes`):
su Windows GDAL e Python usano CRT diversi e `os.dup2(2)` non cattura le
righe `CPL_CURL_VERBOSE` della DLL di GDAL (verificato: file di cattura a 0
byte). Costo dichiarato: una lettura in piu' (~3,4 s per frame). Per questo
`opera.measure_bytes` e' **False** in produzione.

Se `/vsicurl/` non e' disponibile (GDAL senza curl) il modulo degrada al
download integrale nel `cache_dir` e lo dichiara in `meta["mode"]="download"`.

### Normalizzazione

* nodata/NaN -> `NaN`;
* clamp fisico `[-32, 95]` dBZ (`opera.clamp_dbz`), pixel fuori range contati
  in `meta["clipped_pixels"]`;
* riproiezione `nearest` (conservativa: nessun valore interpolato).

---

## 2. Merge con provenienza per pixel (`mosaic.py`)

Priorita' **statica**: `DPC=10` (vince), `OPERA=20` (subentra). **Nessun
blend**: ogni pixel viene da una sola sorgente.

```
source_id = 10 dove la VMI ha un eco valido
source_id = 20 dove la VMI non ha eco e OPERA si'
source_id =  0 altrove (nessuna delle due ha un eco: NON "assenza di pioggia")
source_ts = epoch (s) dell'istante nominale della sorgente vincente
```

### Freschezza (`opera.max_age_min`, default 20)

* **Frame piu' recente**: se la VMI e' piu' vecchia della soglia e OPERA e'
  corrente, OPERA subentra anche dove la VMI ha un valore ->
  `substituted=true`, `substitute_reason="dpc_stale_opera_fresh"`. Se OPERA e'
  piu' vecchia della soglia non entra mai (`"opera_stale"`).
* **Frame storici** della finestra di tracking: nessuna sostituzione assoluta
  (hanno 5-25 minuti per costruzione). Resta il gate di **allineamento di
  slot** `|t_opera - t_dpc| <= max_age_min` (`"opera_slot_mismatch"`).

### Seam

Fascia di `opera.seam_width_px` (default **2**) pixel a cavallo del confine
fra le due sorgenti (`source_id` diversi). I pixel del seam restano **dato
valido** nel mosaico pubblicato, ma sono **esclusi da detect/tracking**
(`detect.detect_cells(..., exclude_mask=...)`): una cella a cavallo di due
sorgenti non e' una cella osservata da una sola. Le celle il cui footprint
tocca il seam prendono `mixed=true` (chiave emessa **solo** quando vera:
l'output a sola sorgente DPC resta invariato).

### Determinismo

Nessuna casualita'. Due esecuzioni sugli stessi input danno `data`,
`source_id`, `source_ts` con lo **stesso sha256** (`MosaicFrame.digest()`):
misurato **6/6 frame identici** su 6 slot consecutivi.

### Perche' il seam non e' cosmetico (misurato)

Senza esclusione del seam, DPC e OPERA formano **una sola componente connessa
da 47.772 km²** sul frame 14:00Z: il cap `detect.max_area_km2 = 40.000`
la scarta. Con l'esclusione la componente si spezza (massimo 34.921 km²) e
l'area totale delle celle passa da 68.871 a 111.994 km². Non e' un artefatto
del test: e' il comportamento reale del merge + cap.

---

## 3. Config (`config.py`)

```json
"opera": {
  "enabled": true,
  "s3_base": "https://s3.waw3-1.cloudferro.com/openradar-24h/",
  "product": "DBZH", "extension": "tiff",
  "bbox_wgs84": [6.0, 35.0, 20.0, 48.0],
  "priority": {"dpc": 10, "opera": 20},
  "max_age_min": 20,
  "slot_tolerance_s": 300,
  "lookback_days": 2,
  "timeout_s": 60,
  "seam_width_px": 2,
  "clamp_dbz": [-32.0, 95.0],
  "use_vsicurl": true,
  "measure_bytes": false,
  "provenance_out": null
},
"collimation": {
  "enabled": false,
  "out": "data/radar/collimation.json",
  "presence_threshold_dbz": 20.0,
  "ring_edges_km": [0, 50, 100, 150, 200, null],
  "sites_file": null
}
```

`opera.enabled=false` = percorso storico a sola sorgente DPC, output
byte-identico alla 1.2.4.26.

---

## 4. Output: `data/radar/latest.json`

Blocchi **additivi** (presenti solo quando il merge e' stato eseguito):

```json
"sources": [
  {"id": "dpc", "name": "Radar-DPC VMI",
   "license": "Dati Radar-DPC (Dipartimento della Protezione Civile)",
   "attribution": "Radar-DPC - Dipartimento della Protezione Civile",
   "pixels": 83090, "pct": 4.9458, "valid_time": "2026-11-10T14:40:00Z"},
  {"id": "opera", "name": "OPERA CIRRUS (EUMETNET)", "license": "CC BY 4.0",
   "attribution": "OPERA CIRRUS composite - (c) EUMETNET OPERA, CC BY 4.0 (...)",
   "pixels": 190545, "pct": 11.342, "valid_time": "2026-11-10T14:40:00Z"}
],
"opera": {
  "status": "ok",                 // ok | partial | unavailable | disabled
  "reason": null,                 // motivo del degrado, se presente
  "slots_resolved": 3, "frames_merged": 3, "seconds": 17.2,
  "bytes_read": null, "bytes_file": null, "range_requests": null,
  "license": "CC BY 4.0", "attribution": "..."
},
"mosaic": {
  "status": "ok", "frames_merged": 3, "seconds": 17.2,
  "mixed_cells": 97,
  "frames": [{"time_iso": "...", "pixels_dpc": 83090, "pixels_opera": 190545,
              "pct_dpc": 4.9458, "pct_opera": 11.342, "seam_pixels": 21281,
              "substituted": false, "substitute_reason": null,
              "opera_time_iso": "..."}]
}
```

`pixels`/`pct` sono riferiti al **frame piu' recente** (quello pubblicato in
`storms.geojson`); `pct` e' la percentuale sui pixel del dominio
(1.680.000 = 1200x1400).

### Array di provenienza (opzionale)

`source_id` (uint8) e `source_ts` (int64 epoch s) sono disponibili per frame
in memoria e si scrivono su disco solo con `opera.provenance_out` (`.npz`
compresso, `mosaic.write_provenance`). Non vengono committati: 1,68 Mpx x 9
byte = ~15 MB per frame non compressi, inutili in un file di stato.

---

## 5. `data/radar/doppler_coverage.geojson`

GeoJSON `FeatureCollection`. Un poligono per ogni **run contiguo di azimuth
coperti** da VRADH: un sito con un settore muto produce piu' poligoni, non un
poligono "bucato" inventato.

Copertura = raggio con **almeno un bin non nodata/undetect** (definizione
della specifica). Buchi < 3° chiusi (speckle), run < 5° scartati.

```json
{"type": "FeatureCollection",
 "generated_at": "2026-11-10T14:52:00Z",
 "license": "CC BY 4.0",
 "attribution": "OPERA (EUMETNET) via Open Radar Data/MeteoGate - ...",
 "data_limits": ["..."],
 "counts": {"sites_listed": 152, "sites_ok": 54, "sites_skipped": 91,
            "polygons": 134},
 "skipped": [{"site": "hrdeb", "country": "HR", "status": "no_scan"}],
 "features": [{
   "type": "Feature",
   "geometry": {"type": "Polygon", "coordinates": [[[lon,lat], ...]]},
   "properties": {
     "site": "fraja", "country": "FR",
     "max_range_km": 252.48,
     "generated_at": "2026-11-10T14:52:00Z",
     "attribution": "Meteo-France via OPERA",
     "license": "CC BY 4.0",
     "azimuth_start_deg": 79.5, "azimuth_end_deg": 317.5,
     "azimuth_span_deg": 238.0, "full_circle": false,
     "n_rays": 239, "coverage_fraction": 0.6611,
     "scan_time": "2026-11-10T14:50:00Z",
     "site_lonlat": [8.75, 41.95], "site_height_m": 100.0,
     "elangle_deg": 0.5
   }
 }]}
```

* `coverage_fraction` = `azimuth_span_deg / 360`.
* `full_circle: true` -> anello chiuso **senza** il centro (giro completo).
* `attribution` per paese: FR Météo-France, HR DHMZ, CH MeteoSwiss, DE DWD,
  IT DPC, SI ARSO, ... (fallback `<CC> national weather service via OPERA`).
* Il client lo disegna come **linea tratteggiata rossa** (non implementato
  qui: consegna del solo GeoJSON).

Misura del 2026-11-10: **152 siti elencati, 54 con VRADH, 91 saltati**
(76 `no_scan`, 15 `vradh_dataset_missing`), **134 poligoni**, 397.313 byte,
51,8 s con 8 worker. 9 poligoni sono giri completi.
`max_range_km`: minimo 5,0 / mediana 88,8 / massimo 256,3.

⚠️ I siti **HR** il 2026-11-10 pubblicano solo `PVOL`, non `SCAN`: risultano
`no_scan`. `doppler_coverage` legge le scansioni (`SCAN`), come da specifica:
il supporto PVOL non e' implementato.

---

## 6. `data/radar/collimation.json`

Confronto DPC <-> OPERA sulla **stessa griglia** (quella della VMI), dove
**entrambe** hanno un eco (`>= presence_threshold_dbz`, default 20).

```json
{"generated_at": "...", "n_frames": 3, "n_pairs": 85569,
 "presence_threshold_dbz": 20.0, "distance_reference": "domain_center",
 "limits": ["OPERA CIRRUS e' un MAX su 10 minuti, la VMI DPC un'istantanea: ...", "..."],
 "frames": [{"frame": "2026-11-10T00:00:00Z", "n_pairs": 17730,
   "distance_km": {"min": 88.79, "median": 423.15, "max": 633.43},
   "rings": [{"ring_km": [0, 50], "label": "0-50", "n_pairs": 0,
              "n_echo_dpc": 85, "n_echo_opera": 0, "n_only_dpc": 85,
              "n_only_opera": 0, "n_neither": 0,
              "bias_dbz": null, "mae_dbz": null, "rmse_dbz": null,
              "correlation": null}, ...]}]}
```

Distanza: dal **sito DPC piu' vicino** se `sites` e' fornito (file JSON di
`[lon,lat]`), altrimenti dal **centro del dominio** (nessuna lista di siti DPC
nel repo: non viene inventata).

### Tabella misurata (3 frame, 2026-11-10)

bias = OPERA - DPC, in dBZ.

| Frame | Anello | n_pairs | bias | MAE | RMSE | corr | eco DPC | eco OPERA |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 00:00Z | 0-50 | 0 | — | — | — | — | 85 | 0 |
| 00:00Z | 50-100 | 25 | -3,32 | 3,44 | 3,79 | 0,405 | 109 | 63 |
| 00:00Z | 100-150 | 1.337 | +0,75 | 2,13 | 2,77 | 0,607 | 2.497 | 1.683 |
| 00:00Z | 150-200 | 1.041 | -0,06 | 2,48 | 3,32 | 0,036 | 3.087 | 1.739 |
| 00:00Z | 200+ | 15.327 | -0,15 | 3,80 | 5,08 | 0,319 | 29.924 | 139.515 |
| 06:00Z | 0-50 | 275 | +0,05 | 2,45 | 3,24 | 0,482 | 2.727 | 372 |
| 06:00Z | 50-100 | 536 | -1,67 | 2,64 | 3,46 | 0,625 | 1.737 | 620 |
| 06:00Z | 100-150 | 2.287 | -0,81 | 3,52 | 4,32 | 0,218 | 3.985 | 3.004 |
| 06:00Z | 150-200 | 1.160 | -1,93 | 4,48 | 5,75 | -0,061 | 5.106 | 2.448 |
| 06:00Z | 200+ | 26.810 | +0,35 | 4,93 | 6,49 | 0,300 | 45.058 | 155.471 |
| 12:00Z | 100-150 | 0 | — | — | — | — | 389 | 0 |
| 12:00Z | 150-200 | 143 | +2,06 | 3,43 | 4,14 | -0,119 | 421 | 324 |
| 12:00Z | 200+ | 36.628 | -1,63 | 5,16 | 6,64 | 0,146 | 66.377 | 133.456 |

`n_pairs` totale sui 3 frame: **85.569**.

**Il bias atteso positivo NON e' confermato**: OPERA e' mediamente piu' basso
della VMI (-1,9 .. +2,1 dBZ a seconda dell'anello, con 7 anelli su 13
negativi). Il MAX su 10 minuti dovrebbe essere >= dell'istantanea, quindi la
differenza viene da calibrazione/mosaicatura, non dalla finestra temporale.

**Allineamento**: la ricerca del miglior shift ±2 px sul frame 12:00Z da'
`r=0.204` a (+1,+1) contro `r=0.146` a (0,0): un offset di ~1 km e'
possibile, ma la correlazione resta bassa anche al massimo (nessuna
correzione applicata: sarebbe una calibrazione non giustificata).

---

## 7. Degrado

Simulando OPERA irraggiungibile (`opera.s3_base` su un host inesistente), il
motore **finisce `ok`** (rc 0) con:

```json
"opera": {"status": "unavailable", "reason": "listing_failed:network:URLError", ...}
```

e `sources[]` contiene la sola sorgente DPC. Misurato: 5,7 s, 3 frame, status
`ok`, nessun warning aggiunto (l'assenza della seconda sorgente non degrada
il run).

---

## 8. Costo misurato per run (6 frame consecutivi)

| Voce | Valore |
| :--- | :--- |
| Fetch DPC (6 VMI) | 6,19 s |
| Fase merge OPERA (produzione) | **9,17 s** |
| Delta vs fetch DPC | **+148%** |
| Byte OPERA letti (con misura) | 4.611.940 (30 richieste Range, 768.657/frame) |
| Riproiezione (per frame) | 0,047-0,050 s |
| Fase merge con misura byte | 23,25 s (6 sottoprocessi da ~3,4 s) |

Su un run completo del motore (e2e, 3 frame, con phase2/CAPPI/fulmini) il
tempo totale misurato e' 82,4 s **con la misura dei byte attiva** (27,6 s la
sola fase OPERA). Con `measure_bytes=false` (default di produzione) lo stesso
e2e su 2 frame chiude in **33,8 s**, di cui **6,2 s** la fase OPERA
(listing + 2 letture + 2 riproiezioni + merge).

---

## 9. Cosa NON e' verificato

* **Nessun run su GitHub Actions**: il codice e' provato solo su questo host
  (Python 3.14.7, rasterio 1.5.2, GDAL 3.12.2).
* **Nessuna validazione della bontà del merge** (POD/FAR, verità a terra):
  non esistono etichette indipendenti per OPERA in questo repo.
* **HR**: solo PVOL il 2026-11-10, quindi nessun poligono Doppler croato;
  il supporto PVOL non e' implementato.
* **Siti IT/SI**: cartelle vuote su S3 (verificato dal listing), quindi il
  Doppler italiano non e' disponibile da OPERA.
* **`source_ts`**: e' l'istante *nominale* del frame, non quello di
  acquisizione del singolo volume (il composito non lo espone per pixel).
* **Offset di ~1 km** fra DPC e OPERA: misurato come shift che massimizza la
  correlazione, non corretto.
* Il campo `quality` (banda 2 del COG) **non** viene usato: nessuna soglia di
  qualita' applicata.

---

## 10. Riproduzione

```bash
pip install -r scripts/radar_engine/requirements.txt   # include h5py

# merge + seam + determinismo + collimazione (frame reali)
python scripts/validation/opera_mosaic_bench.py \
  --merge-frames 2026-11-10T14:00:00Z,...,2026-11-10T14:25:00Z \
  --frames 2026-11-10T00:00:00Z,2026-11-10T06:00:00Z,2026-11-10T12:00:00Z \
  --out _bench/opera_mosaic_bench.json

# copertura Doppler
python -m radar_engine.doppler_coverage --day 2026-11-10 \
  --out data/radar/doppler_coverage.geojson

# collimazione (CLI, riscarica i frame)
python -m radar_engine.collimation --out data/radar/collimation.json \
  --frames 2026-11-10T00:00:00Z,2026-11-10T06:00:00Z,2026-11-10T12:00:00Z

# test
pytest scripts/tests/radar_engine -q --capture=sys
```
