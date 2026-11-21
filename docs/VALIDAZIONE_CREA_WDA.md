# CREA WDA come fonte di etichette indipendenti

## Fonte, DOI, licenza

- **Dataset**: *Weather related disasters in agriculture* (CREA WDA), v1.2.
- **Autori / ente**: Pontrandolfi G., Pepe M. (CREA).
- **DOI**: [10.5281/zenodo.14499762](https://doi.org/10.5281/zenodo.14499762) (v1.2).
  La **v1.3** ([10.5281/zenodo.20134419](https://doi.org/10.5281/zenodo.20134419))
  ha il CSV **byte-identico** a v1.2 (sha256 `846AA631...`), quindi non cambia
  nulla: si dichiara e basta.
- **Articolo**: *Data in Brief*, DOI [10.1016/j.dib.2025.111323](https://doi.org/10.1016/j.dib.2025.111323).
- **Licenza**: **CC BY 4.0** — attribuzione obbligatoria:
  "Pontrandolfi G., Pepe M. (CREA), Dataset of Weather related disasters in
  agriculture, Zenodo 10.5281/zenodo.14499762; Data in Brief
  10.1016/j.dib.2025.111323; licence CC BY 4.0".

## Numeri misurati (CSV reale, non stimati)

| Quantita' | Valore |
| :--- | :--- |
| File | `Dataset_Weather_related_disasters_agriculture_v1.2.csv` |
| Byte / sha256 | 92.394 / `846AA6310CD25F1661D825AE8EF30AAA1749C0029EBED181F0FFEDE0A8CB4B6F` |
| Righe | 1.160 (1 header + 1.159 record), encoding cp1252, `;`, CRLF |
| Tipi di evento | 13 |
| Record importati | **75** con evento esattamente `Hail` |
| Altri tipi (conteggi) | Tornado 115, Strong wind 66, Heavy rain leading to flood 339, Drought 185, Frost 168, Persistent rain 121, Excess of snow 65, Heat stress 11, Strong wind and heavy rain 7, Sirocco wind 5, Hail and tornado 1, Strong wind/hail/heavy rain 1 |
| Province CREA distinte | 106 (33 tra i soli record `Hail`) |
| Copertura temporale | 2005-11-13 → 2021-09-19 |

`Hail and tornado` (1) e `Strong wind, hail and heavy rain leading to flood`
(1) **non** sono importati: il filtro e' sull'etichetta **esatta** `Hail`.

## I tre limiti (dichiarati, non aggirati)

1. **Solo data**: `Start date` / `End date` in `gg/mm/aaaa` -> ISO `aaaa-mm-dd`.
   `End date` vuota (2 record su 75) -> `date_end: null`; `hour: null` con
   `hour_null_reason = source_has_only_calendar_date_no_hour`.
2. **Nessuna coordinate**: il CSV ha solo `CodProv` (indice CREA sequenziale,
   1=Torino, 2=Vercelli, 3=Novara — **non** ISTAT e **non** SIGLA) e `Den_prov`.
   `lat`/`lon` restano null con `geo_null_reason`. Il join successivo con
   `province_italiane.geojson` produce un **centroide di area** della provincia,
   che NON e' la posizione dell'evento.
3. **Zero overlap radar**: l'archivio radar DPC parte il 2026-05-20, CREA WDA
   finisce nel 2021. Nessun replay radar e' possible su queste etichette.

## Schema JSONL (una riga per evento Hail)

```
source, source_url, source_url_v13, license, attribution, source_id,
dataset_version, event_type, date_start, date_end, hour, hour_null_reason,
lat, lon, geo_null_reason, province_code, province_name, region_code,
intensity, intensity_null_reason, damages{productions,farm_structures,
infrastructures}, duration_days, dedup_key, year
```

Ogni valore mancante e' `null` accompagnato da un `*_null_reason`. Nessun
valore e' inventato: `hour`, `lat`, `lon`, `intensity` sono **sempre** null.

## Comandi di riproduzione

```powershell
$py = "C:\Users\aless\AppData\Local\Python\bin\python.exe"
$csv = "C:\Users\aless\AppData\Local\Temp\opencode\crea_wda\Dataset_Weather_related_disasters_agriculture_v1.2.csv"
$tmp = "C:\Users\aless\AppData\Local\Temp\opencode\crea_wda"

& $py scripts\validation\import_crea_wda.py --csv $csv --selftest
& $py scripts\validation\import_crea_wda.py --csv $csv --out "$tmp\labels.jsonl"
& $py scripts\validation\join_crea_wda_provinces.py --in "$tmp\labels.jsonl" `
    --geo data\geography\province_italiane.geojson --out "$tmp\labels_geo.jsonl"
& $py -m pytest scripts\tests\test_crea_wda_import.py -q --capture=sys
```

## Dichiarazione esplicita

**Nessuna validazione radar e' possibile con CREA WDA; serve ERA5 via CDS per i
predittori storici. FAR non stimabile (le province sono aree, non punti).**

Queste etichette sono una fonte indipendente per conteggi e analisi temporali,
non per accoppiamento spaziale con il radar. I 75 record hanno centroide di
provincia (AREA): usarlo come "punto evento" produrrebbe un falso centroide e
un falso tasso di hit. Nessuna soglia e' tarata su questo dataset.
