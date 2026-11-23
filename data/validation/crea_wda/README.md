# CREA WDA - etichette di danno agricolo da grandine (derivati)

Questi due file sono i **derivati** del dataset CREA WDA, gia' prodotti dagli
script su `main` (`scripts/validation/import_crea_wda.py` e
`scripts/validation/join_crea_wda_provinces.py`). Il CSV originale **NON e'
committato in questo repo** (vedi "Riproduzione").

| file | righe | byte | contenuto |
| :--- | ---: | ---: | :--- |
| `labels.jsonl` | 75 | 82.968 | un evento `Hail` per riga, nessuna geometria |
| `labels_geo.jsonl` | 75 | 94.538 | gli stessi 75 eventi + `province_centroid` (proxy d'area) |

## Attribuzione obbligatoria (CC BY 4.0)

Pontrandolfi G., Pepe M. (CREA), *Dataset of Weather related disasters in
agriculture*, Zenodo, licenza **CC BY 4.0**.

- DOI **v1.2**: `10.5281/zenodo.14499762`
- DOI **v1.3**: `10.5281/zenodo.20134419` (CSV **byte-identico** alla v1.2)
- Articolo: *Data in Brief* `10.1016/j.dib.2025.111323`

L'attribuzione e' ripetuta nel campo `attribution` di **ogni** riga: chi usa
questi file deve conservarla.

## Copertura

- Dataset completo: **13/11/2004 -> 19/09/2021**.
- Sottoinsieme `Hail` usato qui: **75 eventi**, dal **2005-06-07** al
  **2021-08-16** (2 eventi hanno `date_end` nullo e `duration_days = 0`).
- Il CSV v1.2 misurato ha 1.159 righe e 13 tipi di evento; qui e' estratto
  **solo** `Hail` (75). Gli altri tipi (Tornado 115, Strong wind 66, Heavy rain
  leading to flood 339, ...) **non** sono in questi file.
- `sha256` del CSV di input: `846AA6310CD25F1661D825AE8EF30AAA1749C0029EBED181F0FFEDE0A8CB4B6F`
  (verificato dal test `scripts/tests/test_crea_wda_import.py`).

## Cosa sono e cosa NON sono (vincolo, non nota di colore)

Sono **verita' di DANNO AGRICOLO a livello PROVINCIALE**: il record dice "in
questa provincia, in questa data, ci sono stati danni agricoli da grandine".

NON sono:

- una **verita' meteorologica**: il danno e' dichiarato, non misurato;
- una verita' **oraria**: `hour` e' `null` con `hour_null_reason =
  "source_has_only_calendar_date_no_hour"` (75/75);
- una verita' **puntuale**: `lat`/`lon` sono `null` (75/75). In `labels.jsonl`
  il motivo e' `geo_null_reason = "source_has_no_lat_lon_only_province_name_and_code"`;
  in `labels_geo.jsonl` il `province_centroid` e' un **CENTROIDE D'AREA** del
  poligono provinciale (`province_geometry_source` lo dichiara: "AREA proxy,
  NON punto evento"), **non** la posizione dell'evento;
- una misura di **intensita'**: `intensity` e' `null` con
  `intensity_null_reason` (75/75).

Conseguenza operativa: questi file servono a validare **NWP/rianalisi
storiche** (per esempio ERA5 via CDS, che richiede credenziali) su scala
provinciale e giornaliera. **NON** servono a validare il radar operativo, non
danno etichette negative, e **non** coprono la stagione 2026 (l'ultimo evento
Hail e' del 2021): non sono utilizzabili per il "collo di bottiglia 2026"
descritto in `docs/VALIDAZIONE_RISULTATI.md`.

## Riproduzione

Il CSV originale non e' nel repo (licenza CC BY 4.0: il riuso e' consentito,
ma il repo non ridistribuisce il sorgente). Per rigenerare:

```
python scripts/validation/import_crea_wda.py \
  --csv <Dataset_Weather_related_disasters_agriculture_v1.2.csv> \
  --out labels.jsonl

python scripts/validation/join_crea_wda_provinces.py \
  --in labels.jsonl \
  --geo data/geography/province_italiane.geojson \
  --out labels_geo.jsonl
```

Il test `scripts/tests/test_crea_wda_import.py` verifica lo `sha256` del CSV,
i 75 record `Hail`, i `null_reason`, l'assenza di duplicati e il join 75/75; se
il CSV non e' presente fa **skip dichiarato** (nessuna fixture artefatta).
