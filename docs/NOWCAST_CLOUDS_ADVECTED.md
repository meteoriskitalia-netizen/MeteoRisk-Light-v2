# Nuvole IR advette nel nowcast (2026-11-27)

## Stato precedente (difetto)

`clouds.image` era **una sola WebP per slot** e
`nowcastCloudsRefresh()` non conosceva il lead: scorrendo i 18 frame la
nuvola restava **ferma**. Sembrava uno sfondo immobile, che suggerisce una
nuvola ferma — falso.

## Opzione scelta: A — advezione dichiarata

**Metodo**: la temperatura IR (°C) viene traslata con lo **stesso campo di
moto** del nowcast (`u,v` dopo regolarizzazione e speed factor), con la
stessa media a blocchi a 3 km usata per la pioggia (`coarse_advect`, senza
mass-cap e senza clip a zero: la temperatura e' negativa).

Per ogni lead viene ricostruita la RGBA con `rgba_from_temp` (bilineare,
smoothstep 2 °C, blur 0,7 px, downsample 2) e scritta una WebP.

Il payload lo dichiara:

```json
"clouds": {
  "method": "advected",
  "role": "context",
  "not_a_rain_forecast": true,
  "advection": {
    "transport": "coarse_warp_temperature",
    "speed_factor": 0.8,
    "lead_min": [5, 10, ..., 90],
    "note": "Nuvole traslate col moto del nowcast: contesto estrapolato, non previsione di pioggia ne' della nuvola."
  },
  "image": "nowcast_clouds.webp",
  "images_by_lead": {"5": "nowcast_clouds_lead005.webp", ...}
}
```

L'etichetta nel client dice **«contesto IR estrapolato col moto del
nowcast, non previsione di pioggia né della nuvola»**.

## Perche' A e non B

B (nuvole solo al primo frame) e' la piu' onesta, ma lascia il player senza
contesto dopo il primo frame. A e' un'estrapolazione dichiarata, coerente
con la pioggia che e' anch'essa un'estrapolazione. **Un fondo immobile e'
peggio di un'estrapolazione dichiarata**, perche' suggerisce che la nuvola
sia ferma.

## Costo misurato

Slot `2026-11-27T19:20Z`, 18 lead:

| voce | byte |
| :--- | ---: |
| 18 WebP nuvole per lead | **85.116** (4,5-4,9 KB ciascuna) |
| `nowcast_clouds.webp` (fallback/primo) | 15.482 |
| **totale nuvole** | **100.598** |
| pioggia 18 frame | 553.892 |
| **totale output nowcast** | **654.490** |

Le nuvole advette costano **~100 kB**, non ~290 kB: la temperatura
advetta produce campi piu' semplici del frame classificato originale.
Accettabile; non serve la variante a 6 immagini.

## Verifica del movimento

Centroide dell'alpha delle nuvole (soglia 16/255), griglia 3 km:

| lead | centroide px | spostamento | area px |
| ---: | :--- | ---: | ---: |
| +5 | (459,1; 399,3) | 0 | 7.111 |
| +30 | (456,1; 393,2) | **20,4 km** | 6.540 |
| +60 | (452,4; 384,9) | **47,5 km** | 5.790 |
| +90 | (446,8; 375,9) | **79,3 km** | 4.972 |

Non e' uno sfondo fermo: a +90 la nuvola si e' spostata di ~79 km e l'area
si e' ridotta del 30% uscendo dal dominio/riscaldandosi.

Immagini:

- `docs/evidence/nowcast_clouds_advected_20261127/clouds_advected_5_30_60_90.png` (37.459 B)
- `.../rain_clouds_advected_030.png` (91.192 B)
- `.../rain_clouds_advected_060.png` (90.497 B)
- `.../advected_report.json`

## Client

- `nowcastApplyFrame()` chiama `nowcastCloudsSetLead(lead)`.
- `nowcastCloudsName(cl, lead)` sceglie `images_by_lead[lead]`, fallback
  `cl.image`.
- `nowcastCloudsAdd` e `nowcastCloudsRefresh` usano il lead corrente.
- Il toggle resta separato da Intensita'/Probabilita'; le nuvole non
  accendono la pioggia.

## Test

- `test_nowcast_field::test_clouds_payload_and_image`: `method=advected`,
  18 `images_by_lead`, 18 file >0 byte.
- `test_cloud_toggle_is_separate`: la vista segue il lead e il testo dice
  «estrapolato col moto del nowcast».
- `radar_engine`: **1011 passed**.

## Limiti

- E' un'estrapolazione: la nuvola non si muove necessariamente come la
  pioggia. La dichiarazione e' nel payload e nella legenda.
- L'advezione non crea nuova convezione: una nube che non c'e' nell'ultimo
  IR non compare.
- La temperatura advetta fuori dominio e' esclusa; l'area puo' ridursi
  (misurato -30% a +90) e il campo non viene re-inflazza.
