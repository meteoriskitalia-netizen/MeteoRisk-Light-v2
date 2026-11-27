# Nuvole IR di contesto nel nowcast (2026-11-27)

## Fonte misurata (non dichiarata dal fornitore)

| grandezza | DPC IR_108 (usata) | EUMETSAT `mtg_fd:ir105_hrfi` (scartata) |
| :--- | :--- | :--- |
| canale | IR 10.8 um (MSG SEVIRI) | IR 10.5 um (MTG) |
| dato | **numerico**, Float32, °C | **WebP colorato**, nessun °C |
| risoluzione | 1200x1400 a **1 km**, stessa griglia TM di VMI/SRI | Italia 1024 px = 1,33 km/px; Europa 2048 px = 3,80 km/px |
| validita' | 1.680.000 px su 1.680.000; min -65,0 °C, max 17,0 °C, media -4,1 °C (slot 15:45Z) | solo pixel RGB |
| latenza MISURATA | slot **15:45Z** disponibile alle **15:53Z** = **8,1 min**; slot **16:25Z** alle **16:32:58Z** = **7,7 min** | manifest slot **15:25Z** generato alle **15:45Z** = **28 min** di ritardo (e il frame e' colorato) |

Perche' DPC: le soglie richieste sono in °C. Un WebP colorato non permette
`T < -20 °C` ne' `T < -40 °C` senza inventare una corrispondenza
colore-temperatura. Il campo numerico DPC e' sulla **stessa griglia** del
nowcast (nessun resampling per la maschera), quindi le classi restano
allineate ai pixel della pioggia.

## Indice di nuvolosita'

`scripts/radar_engine/nowcast_clouds.py`, classi 0-3:

| classe | soglia | colore | alpha |
| :--- | :--- | :--- | ---: |
| 1 | -32 °C <= T < -20 °C | grigio-blu chiaro (170,190,210) | 90 |
| 2 | -44 °C <= T < -32 °C | grigio-blu medio (135,160,190) | 120 |
| 3 | T < -44 °C | grigio-blu scuro (100,125,165) | 150 |
| 0 | T >= -20 °C o non valido | trasparente | 0 |

La scala e' **separata** da quella della pioggia: nessun colore della
`PALETTE` del nowcast compare nella palette delle nuvole (test dedicato).
Il file e' **lossless** (4 colori piatti; con lossy l'alpha veniva alterato).

## Payload `clouds`

```json
{
  "schema": "meteorisk.nowcast_clouds/1",
  "available": true,
  "source": "DPC_IR_108",
  "channel": "IR 10.8 um (MSG SEVIRI)",
  "resolution_m": 1000,
  "role": "context",
  "not_a_rain_forecast": true,
  "note": "Nuvole IR (contesto): una nube alta e fredda puo' non piovere. Non e' una previsione di pioggia...",
  "thresholds_c": [-20.0, -32.0, -44.0],
  "classes": [ ... ],
  "image": "nowcast_clouds.webp",
  "image_crs": "EPSG:3857",
  "opacity": 0.70,
  "under_rain": true,
  "slot": "2026-11-27T16:25:00Z",
  "latency_s": 460.5,
  "latency_min": 7.7,
  "cloud_pixels": 164944,
  "cold_pixels": 49524,
  "published_pixels": 1504776,
  "output_bytes": 6312
}
```

Se IR_108 manca, lo slot non coincide con l'SRI o la griglia e' diversa:
`available:false` con `null_reason` (`ir108_unavailable`,
`ir108_slot_mismatch`, `ir108_grid_mismatch`, `no_cloud_below_threshold`,
`build_error:*`). Il nowcast pioggia resta invariato.

**Nuvole anche senza pioggia**: se l'ultimo SRI e' asciutto il nowcast
pioggia resta `available:false` (`no_rain_in_latest_frame`) ma il blocco
`clouds` e l'immagine vengono costruiti lo stesso, perche' e' esattamente
il caso della nube che cresce e non piove ancora. `grid` e' pubblicato
anche in quel ramo (serve al client per i bounds).

## Client

- pulsante **separato** «Nuvole IR (contesto) · OFF/ON» nei layer;
  `nowcastCloudsToggle()` chiama solo `nowcastLoad` + `L.imageOverlay`,
  **non** `nowcastProductOn`/`nowcastShow`: la pioggia non si accende.
- pane `nowcastCloudsPane` **z 406**, sotto `nowcastPane` **z 407**.
- opacita' 0,70; una sola immagine per slot, non una per lead.
- legenda con i tre grigi e la frase **«contesto IR, non previsione di
  pioggia»**; il `title` del pulsante dice che una nube alta e fredda puo'
  non piovere.
- refresh: `fetch(url,{cache:'reload'})` + blob quando cambia lo slot; il
  layer pioggia continua col suo player, le nuvole non entrano nella
  timeline (nessun frame in piu').

## Immagine reale (slot 2026-11-27T16:25Z)

| file | byte | contenuto |
| :--- | ---: | :--- |
| `docs/evidence/nowcast_clouds_20261127/nowcast_clouds.webp` | 6.312 | sole nuvole, lossless |
| `.../clouds_rain_lead030.png` | 111.471 | nuvole (0,70) sotto + nowcast pioggia (0,75) a +30 |
| `.../clouds_rain_lead060.png` | 110.359 | idem a +60 |
| `.../clouds_rain_live_lead030.png` | 113.273 | **LIVE** slot 17:00Z, nuvole+pioggia +30 |
| `.../clouds_rain_live_lead060.png` | 108.534 | **LIVE** slot 17:00Z, nuvole+pioggia +60 |
| `.../nowcast_lead030.webp` | 35.834 | pioggia prevista +30 (solo) |
| `.../nowcast_lead060.webp` | 34.576 | pioggia prevista +60 (solo) |
| `.../clouds_report.json` | 2.089 | numeri della misura |
| `.../live_clouds.json` | 3.595 | payload live e verifica HTTP |
| `.../live_clouds_report.json` | 518 | byte dei compositi live |

### Verifica live resa morbida (slot 2026-11-27T18:25Z)

`nowcast.json` live: `clouds.image_size = 591x636`, `downsample = 2`,
`resampling = bilinear`, `blend_c = 2.0`, `blur_px = 0.7`;
`nowcast_clouds.webp` **HTTP 200, 16.396 B**. L'immagine e' stirata dal
client sulla stessa `grid.bounds`; i bordi sono sfumati, non a blocchi.

### Verifica live precedente (slot 2026-11-27T17:00Z, generato 17:09:52Z)

- `clouds.available = true`, `role = context`, `source = DPC_IR_108`;
- `latency_min = 9,7` (583 s, misurata al run);
- `cloud_pixels = 178.626`, `cold_pixels = 52.116`, `published_pixels = 1.504.776`;
- immagine `nowcast_clouds.webp`: **HTTP 200, 6.464 B**;
- HTML live **3.459.894 B**, con pulsante `live-toggle-nowcastclouds`,
  palette dei tre grigi e testo «non è pioggia prevista».

Cosa si vede: le nuvole sono **grigio-blu** e stanno sotto; la pioggia
mantiene ciano/verde/giallo/arancio/rosso ed e' dominante. Le aree di nube
senza pioggia (nord della penisola) sono visibili come contesto, non come
previsione.

## Prestazioni

- una sola immagine da **6.312 B** per slot, non una per lead;
- caricata **solo** al toggle ON (o al refresh se resta ON), fuori dalla
  timeline del player: nessun frame aggiuntivo, nessun rallentamento del
  play;
- il run di produzione completo (6 SRI + 12 lead + nuvole) ha misurato
  **37,9 s** (fetch SRI 3,3 s), contro i 43,3 s della stessa prova con
  orologio ancorato allo slot: il layer nuvole non e' il costo dominante.

## Resa morbida (2026-11-27, dopo la segnalazione dei bordi pixelati)

Causa: le classi erano **discrete** (uint8) e il resampling era **nearest**
da 1 km a 1,5 km; l'alpha a gradini aggiungeva un bordo netto.

Fix applicato SOLO alle nuvole:

- il resampling e' **bilineare sulla temperatura** (°C), non sulle classi:
  la soglia viene applicata dopo, a 1,5 km;
- alpha con **smoothstep su 2 °C** attorno a -20/-32/-44 °C;
- blur **0,7 px** sul solo alpha;
- colore a **3 classi** (palette separata) e alpha quantizzato a passi di
  16: con il colore interpolato su 256 livelli il WebP passava da 6 kB a
  63-78 kB. La sfumatura visibile e' nell'alpha;
- **downsample 2** (layer ~3 km, il client lo stira): il contesto non ha
  bisogno di 1,5 km e il peso resta piccolo.

Misure sullo slot 2026-11-27T17:35Z (immagini in
`docs/evidence/nowcast_clouds_smooth_20261127/`):

| | prima (classi+nearest) | dopo (temperatura+bilineare) |
| :--- | ---: | ---: |
| byte immagine nuvole | 6.114 | **18.002** |
| gradiente alpha massimo | 150 | **112** (-25%) |
| salti alpha > 30 | 14.324 | **8.417** (-41%) |
| composito nuvole+pioggia +30 | 105.099 | 144.087 |

L'immagine affiancata e' `clouds_old_vs_new.png` (118.102 B): i bordi a
scaletta spariscono; la pioggia resta sopra e dominante. Il peso cresce
(2,9x) ma resta nell'ordine delle decine di kB, non dei 63-78 kB del primo
tentativo lossless con colore interpolato.

## Test

- `test_nowcast_clouds.py`: **8 passed** (soglie, palette separata dalla
  pioggia, alpha sfumato, blur, ruolo `context`, latenza, null senza IR,
  null su slot mismatch).
- `test_nowcast_field.py`: **48 passed**, inclusi payload+WebP delle nuvole
  e il caso **senza pioggia** con nuvole disponibili.
- `test_html_js_syntax` 1 passed; `test_tooltips_coverage` 2 passed (il
  nuovo pulsante ha `title`).

## Limiti

- La temperatura di sommita' non e' pioggia: una nube a -50 °C puo' non
  piovere. Il layer e' dichiarato `context` e non modifica l'intensita'.
- Le soglie -20/-32/-44 °C sono una scelta dichiarata, non una calibrazione
  contro fulmini o pluviometri.
- Una sola immagine per slot: non e' una previsione dell'evoluzione della
  nuvola ai lead +30/+60; resta il contesto dell'istante del nowcast.
- Il campo IR e' valido su tutto il dominio nel campione, ma non e'
  verificato su ogni slot; se arriva parziale, le classi usano solo i pixel
  `valid_mask`.
- La latenza varia (7,7-8,1 min nei due campioni); non e' una costante.
