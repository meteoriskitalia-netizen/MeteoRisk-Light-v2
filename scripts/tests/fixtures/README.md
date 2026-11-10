# Fixture reali del prodotto MCD del DWD

I due XML di questa cartella **non sono inventati**: sono file reali del
prodotto *Mesyklonenerkennung* (MCD) del Deutscher Wetterdienst, scaricati il
2026-11-10 dallo zip `OpenDataSampleData/01.zip` del repository pubblico
<https://github.com/buu-huu/mecy> (che li redistribuisce come esempio d'uso del
formato). I nomi sono quelli originali DWD.

| File | Byte | Contenuto |
| :--- | ---: | :--- |
| `meso_20210708_1040.xml` | 9.073 | 4 `<event>` (detection reali) |
| `meso_20210706_2210.xml` | 173 | nessuna detection: sola lista stazioni |

- Fonte primaria: DWD Open Data,
  <https://opendata.dwd.de/weather/radar/mesocyclones/>.
- Licenza: **CC BY 4.0** (DWD Open Data). Attribuzione: Deutscher Wetterdienst.
- I file sono byte-identici agli originali (LF, nessun BOM): non sono stati
  modificati. Le prove sui campi mancanti rimuovono il tag **in memoria**
  dentro il test, senza alterare la fixture su disco.
