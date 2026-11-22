# Disponibilità radar OPERA con VRADH per l'Italia — audit con richieste HTTP reali

Data misurazione: **2026-11-19**. Fonte: **EUMETNET Open Radar Data (ORD) API** via MeteoGate Gateway
(`https://api.meteogate.eu/eu-eumetnet-weather-radar/`), catalogo `collections/observations/items` e
`collections/observations/locations`, più volumi ODIM HDF5 reali scaricati dallo S3 di backup
(`https://object-store.os-api.cci1.ecmwf.int/openradar-24h-backup/`) e parsati con `h5py`.

Nessun dato è stato inventato: ogni valore non verificabile riporta **NON VERIFICATO** con il motivo
(HTTP status o assenza dell'attributo). Nessun push, nessun Edge, nessuna modifica ad altri file.

> **Aggiornamento 2026-11-22 (TODO_1.3 riga 31).** La tabella non è più solo
> compilata a mano: è **generata dal codice** da misure HTTP/ODIM reali. Vedi
> **sezione 8** per la tabella prodotta da
> `scripts/radar_engine/site_availability.py`, con 13 campi (4 prima mancanti:
> `radar_name`, `variables`, `elevation_angles`, `quality_score`) e **5 stati**
> operativi. La sezione 2 resta l'audit manuale del 19/11; la sezione 8 è la
> misura del 22/11 con i conteggi veri per stato.

## 1. Verdetto

- **Radar italiani con VRADH in ORD oggi: 0 (zero).** Nessuna piattaforma `0-380-0-*` compare nei
  **153** platform del catalogo `/locations`. Le cinque ID richieste — `itbri`, `itset` (Piemonte),
  `itdes`, `itfle` (Lombardia), `itfos` (FVG) — rispondono **HTTP 204** (nessun dato) per `VRADH`,
  `DBZH` e `TH` su tutta la finestra 2026-11-18T00:00Z → 2026-11-19T23:59Z.
- **Corsica: VRADH disponibile.** `frale` (Aleria) e `fraja` (Ajaccio) espongono volumi ODIM reali
  con VRADH, 10 e 8 elevazioni, scan alle **2026-11-19T12:49:45Z** e **12:49:50Z**.
- **Altri siti stranieri con VRADH rilevati nel bbox 4°E–21°E / 34°N–49°N:** 8 francesi, 5 croati,
  4 tedeschi, 5 svizzeri, 1 ungherese. I croati hanno VRADH in catalogo con scan recente ma
  **nessun file ODIM scaricabile** (prefix S3 presente, **0 chiavi**; nessun link in CoverageJSON):
  il loro stato è **parziale**.

## 2. Tabella dinamica (30 righe)

| RADAR_ID | RADAR_NAME | LATITUDE | LONGITUDE | ALTITUDE | NI (/how) | VARIABLES | ELEVATION_ANGLES | RANGE | RESOLUTION | UPDATE_INTERVAL | LAST_AVAILABLE_SCAN | DATA_QUALITY | STATO |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| itbri | ITBRI (Piemonte, per richiesta) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | non disponibile — HTTP 204 su VRADH/DBZH/TH; assente dal catalogo /locations (153 piattaforme, 0 con -380-) |
| itset | ITSET (Piemonte, per richiesta) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | non disponibile — HTTP 204 su VRADH/DBZH/TH; assente dal catalogo /locations (153 piattaforme, 0 con -380-) |
| itdes | ITDES (Lombardia, per richiesta) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | non disponibile — HTTP 204 su VRADH/DBZH/TH; assente dal catalogo /locations (153 piattaforme, 0 con -380-) |
| itfle | ITFLE (Lombardia, per richiesta) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | non disponibile — HTTP 204 su VRADH/DBZH/TH; assente dal catalogo /locations (153 piattaforme, 0 con -380-) |
| itfos | ITFOS (FVG, per richiesta) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | NON VERIFICATO (HTTP 204) | non disponibile — HTTP 204 su VRADH/DBZH/TH; assente dal catalogo /locations (153 piattaforme, 0 con -380-) |
| frale | Aleria | 42.12971 | 9.49639 | 63 m (ODIM /where) | 62.056 | DBZH, TH, VRADH | 0.6°, 1.01°, 1.41°, 2.5°, 3.5°, 5°, 6.5°, 8°, 9.5°, 89.99° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:45Z | Etalab 2.0 (Météo-France); NI(/how)=62.056; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| fraja | Ajaccio | 41.95313 | 8.70054 | 784 m (ODIM /where) | 58.678 | DBZH, TH, VRADH | 0.4°, 0.8°, 1.4°, 2.2°, 3.6°, 4.8°, 6°, 90° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:50Z | Etalab 2.0 (Météo-France); NI(/how)=58.678; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| hrbil | hrbil | 45.88353 | 17.20046 | NON VERIFICATO (file ODIM non disponibile) | NON VERIFICATO (attributo /how assente) | DBZH, RHOHV, TH, VRADH, ZDR | 0.5°, 1.3°, 3.2°, 4.8°, 6°, 7.5°, 11.5°, 17.5°, 25° | 249.2 km (623×400 m) | 400 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:47:54Z | CC BY 4.0; dati solo da catalogo ORD (file ODIM non scaricato: S3 0 chiavi / nessun link in CoverageJSON); QC/dealiasing NON VERIFICATO | parziale — VRADH in catalogo e scan recente, ma metadati ODIM/altitudine NON VERIFICATI (S3 0 chiavi; nessun link in CoverageJSON) |
| hrdeb | hrdeb | 44.04516 | 15.37637 | NON VERIFICATO (file ODIM non disponibile) | NON VERIFICATO (attributo /how assente) | DBZH, RHOHV, TH, VRADH, ZDR | 0.5°, 1.3°, 3.2°, 4.8°, 6°, 7.5°, 11.5°, 17.5°, 25° | 249.2 km (623×400 m) | 400 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:47:55Z | CC BY 4.0; dati solo da catalogo ORD (file ODIM non scaricato: S3 0 chiavi / nessun link in CoverageJSON); QC/dealiasing NON VERIFICATO | parziale — VRADH in catalogo e scan recente, ma metadati ODIM/altitudine NON VERIFICATI (S3 0 chiavi; nessun link in CoverageJSON) |
| hrgol | hrgol | 45.02048 | 14.12234 | NON VERIFICATO (file ODIM non disponibile) | NON VERIFICATO (attributo /how assente) | DBZH, RHOHV, TH, VRADH, ZDR | 0.5°, 1.3°, 3.2°, 4.8°, 6°, 7.5°, 11.5°, 17.5°, 25° | 249.2 km (623×400 m) | 400 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:47:52Z | CC BY 4.0; dati solo da catalogo ORD (file ODIM non scaricato: S3 0 chiavi / nessun link in CoverageJSON); QC/dealiasing NON VERIFICATO | parziale — VRADH in catalogo e scan recente, ma metadati ODIM/altitudine NON VERIFICATI (S3 0 chiavi; nessun link in CoverageJSON) |
| hrgra | hrgra | 45.15917 | 18.70333 | NON VERIFICATO (file ODIM non disponibile) | NON VERIFICATO (attributo /how assente) | DBZH, RHOHV, TH, VRADH, ZDR | 0.5°, 1.3°, 3.2°, 4.8°, 6°, 7.5°, 11.5°, 17.5°, 25° | 249.2 km (623×400 m) | 400 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:47:51Z | CC BY 4.0; dati solo da catalogo ORD (file ODIM non scaricato: S3 0 chiavi / nessun link in CoverageJSON); QC/dealiasing NON VERIFICATO | parziale — VRADH in catalogo e scan recente, ma metadati ODIM/altitudine NON VERIFICATI (S3 0 chiavi; nessun link in CoverageJSON) |
| hrulj | hrulj | 42.89439 | 17.47829 | NON VERIFICATO (file ODIM non disponibile) | NON VERIFICATO (attributo /how assente) | DBZH, RHOHV, TH, VRADH, ZDR | 0.5°, 1.3°, 3.2°, 4.8°, 6°, 7.5°, 11.5°, 17.5°, 25° | 249.2 km (623×400 m) | 400 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:47:46Z | CC BY 4.0; dati solo da catalogo ORD (file ODIM non scaricato: S3 0 chiavi / nessun link in CoverageJSON); QC/dealiasing NON VERIFICATO | parziale — VRADH in catalogo e scan recente, ma metadati ODIM/altitudine NON VERIFICATI (S3 0 chiavi; nessun link in CoverageJSON) |
| frbla | Blaisy | 47.35515 | 4.77594 | 607.2 m (ODIM /where) | 58.678 | DBZH, TH, VRADH | 0.47°, 1°, 1.61°, 2.58°, 3.6°, 4.6°, 6°, 8°, 90° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:45Z | Etalab 2.0 (Météo-France); NI(/how)=58.678; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| frbol | Bollene | 44.32305 | 4.76221 | 325 m (ODIM /where) | 62.100 | DBZH, TH, VRADH | 0.4°, 0.8°, 1.2°, 1.8°, 2.4°, 3.5°, 5°, 90° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:45Z | Etalab 2.0 (Météo-France); NI(/how)=62.100; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| frcol | Collobrieres | 43.21656 | 6.37291 | 653.7 m (ODIM /where) | 61.879 | DBZH, TH, VRADH | 0.4°, 0.8°, 1.4°, 2.19°, 3.6°, 4.79°, 5.98°, 90° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:43Z | Etalab 2.0 (Météo-France); NI(/how)=61.879; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| frmtc | Montancy | 47.36861 | 7.01897 | 925.7 m (ODIM /where) | 58.637 | DBZH, TH, VRADH | 0.4°, 0.7°, 1.2°, 2.2°, 3°, 3.91°, 5°, 6°, 7°, 90° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:47Z | Etalab 2.0 (Météo-France); NI(/how)=58.637; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| frnan | Nancy | 48.71576 | 6.58156 | 296.3 m (ODIM /where) | 58.668 | DBZH, TH, VRADH | 0.7°, 1.3°, 1.9°, 2.6°, 3.6°, 4.6°, 5.59°, 6.6°, 7.6°, 8.6°, 9.6°, 89.99° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:45Z | Etalab 2.0 (Météo-France); NI(/how)=58.668; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| frnim | Nimes | 43.80614 | 4.50269 | 78.1 m (ODIM /where) | 61.725 | DBZH, TH, VRADH | 0.6°, 1.2°, 1.8°, 2.4°, 3.5°, 5°, 6.49°, 8°, 9.49°, 89.99° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:41Z | Etalab 2.0 (Météo-France); NI(/how)=61.725; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| frniz | StNizier | 46.06781 | 4.44535 | 919.8 m (ODIM /where) | 58.626 | DBZH, TH, VRADH | 0.4°, 0.99°, 1.6°, 2.6°, 3.6°, 4.6°, 5.6°, 6.6°, 7.6°, 8.6°, 9.6°, 89.99° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:45Z | Etalab 2.0 (Météo-France); NI(/how)=58.626; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| frtro | Arcis | 48.46213 | 4.30932 | 164.8 m (ODIM /where) | 58.512 | DBZH, TH, VRADH | 0.4°, 1.1°, 2.01°, 3°, 4°, 5°, 6°, 7°, 8°, 9°, 10°, 89.99° | 256.3 km (267×960 m) | 960 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:49:47Z | Etalab 2.0 (Météo-France); NI(/how)=58.512; VRADH= presente; pixel validi 96120; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| defbg | defbg | 47.87361 | 8.00361 | 1516.1 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | 0.5°, 1.5°, 2.499°, 3.499°, 4.499°, 5.499°, 7.998°, 12.003°, 17.001°, 24.999° | 180.0 km (720×250 m) | 250 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:45:20Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 178560; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| deisn | deisn | 48.17471 | 12.10178 | 677.77 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | 0.5°, 1.5°, 2.499°, 3.499°, 4.499°, 5.499°, 7.998°, 12.003°, 17.001°, 24.999° | 180.0 km (720×250 m) | 250 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:45:20Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 178560; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| demem | demem | 48.04214 | 10.21922 | 724.4 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | 0.5°, 1.5°, 2.499°, 3.499°, 4.499°, 5.499°, 7.998°, 12.003°, 17.001°, 24.999° | 180.0 km (720×250 m) | 250 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:45:20Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 178560; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| detur | detur | 48.58538 | 9.78267 | 767.62 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | 0.5°, 1.5°, 2.499°, 3.499°, 4.499°, 5.499°, 7.998°, 12.003°, 17.001°, 24.999° | 180.0 km (720×250 m) | 250 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:45:20Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 178560; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| huhar | huhar | 46.17750 | 18.33723 | 627 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | 1.1°, 1.9°, 3°, 6.5°, 14° | 120.0 km (960×125 m) | 125 m range, 1.04° azimut (347 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:48:00Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 208320; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| chalb | chalb | 47.28433 | 8.51200 | 938 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | -0.2°, 0.4°, 1°, 1.6°, 2.5° | 246.0 km (492×500 m) | 500 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:48:07Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 116640; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| chdol | chdol | 46.42511 | 6.09941 | 1682 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | -0.2°, 0.4°, 1°, 1.6°, 2.5° | 246.0 km (492×500 m) | 500 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:48:07Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 116640; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| chlem | chlem | 46.04076 | 8.83322 | 1626 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | -0.2°, 0.4°, 1°, 1.6°, 2.5° | 246.0 km (492×500 m) | 500 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:48:07Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 116640; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| chppm | chppm | 46.37065 | 7.48655 | 2937 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | -0.2°, 0.4°, 1°, 1.6°, 2.5° | 246.0 km (492×500 m) | 500 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:48:07Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 116640; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |
| chwei | chwei | 46.83497 | 9.79446 | 2850 m (ODIM /where) | NON VERIFICATO (attributo /how assente) | DBZH, TH, VRADH | -0.2°, 0.4°, 1°, 1.6°, 2.5° | 246.0 km (492×500 m) | 500 m range, 1.00° azimut (360 ray) | 5 min (~PT5M, delta mediano asse t; PT1M per singolo scan) | 2026-11-19T12:48:07Z | CC BY 4.0; NI(/how) assente → NON VERIFICATO; VRADH= presente; pixel validi 116640; nessun gruppo /quality nel file: QC nazionale del provider, dealiasing NON uniforme (ORD docs), sentinelle nodata/undetect in /what; valori VRADH quantizzati (gain/offset) | disponibile — VRADH presente, scan entro la finestra cache 24 h |

## 3. Legenda stati (distinzione esplicita)

| Stato | Criterio operativo | Righe |
| :--- | :--- | ---: |
| **disponibile** | VRADH nel catalogo e/o in un file ODIM reale, con `LAST_AVAILABLE_SCAN` dentro la finestra cache 24 h | 20 |
| **non disponibile** | HTTP 204 su VRADH (e su DBZH/TH) e piattaforma assente dal catalogo | 5 |
| **non aggiornato** | scan più recente più vecchio di 60 min rispetto all'estremo temporale del catalogo | 0 (nessun caso osservato) |
| **parziale** | disponibilità confermata ma metadati ODIM/altitudine NON VERIFICATI (S3 0 chiavi, nessun link) | 5 |
| **non valido** | file ODIM corrotto, quantità VRADH assente o parsing fallito | 0 (nessun caso osservato) |

La categoria "non aggiornato" non è stata assegnata: l'estremo temporale del catalogo era
`2026-11-19T12:47:10Z` e tutti gli scan disponibili erano entro ~12 min da quell'istante.

**Stati operativi del codice (22/11/2026, 5 valori — `site_availability.STATES`):**
`ok` · `partial` · `invalid` · `stale` · `unavailable`. I criteri esatti, con i numeri
misurati e la soglia di 60 min, sono in **sezione 8.2**. I quattro nomi storici qui sopra
mappano così: *disponibile*→`ok`/`partial`, *non disponibile*→`unavailable`,
*non aggiornato*→`stale`, *parziale*→`partial`, *non valido*→`invalid`.

## 4. Metodo e verifiche

1. **Catalogo**: `GET /collections?f=json` → estrema temporale `2026-11-18T12:47:38Z/2026-11-19T12:47:10Z`,
   `standard_name` include `VRADH`, formati `ODIM`/`GeoTIFF`.
2. **`GET /collections/observations/items?bbox=4,34,21,49&datetime=2026-11-19T08:00Z/12:00Z&f=GeoJSON`**
   → 924 feature, di cui **214 VRADH** su **25 piattaforme**. ⚠️ Il parametro `standard_name=VRADH`
   viene **ignorato** dall'endpoint `/items`: con e senza il filtro il payload è identico (469 feature,
   distribuzione `TH` 128 / `VRADH` 111 / `DBZH` 133 / `ZDR` 45 / `RHOHV` 45 / `VRAD` 7).
   Il filtro VRADH è stato quindi applicato **lato client** su `properties.standard_name`.
3. **Per-site**: `GET /collections/observations/locations/<id>?…&f=CoverageJSON` per ciascuna ID
   richiesta → 200 per i siti stranieri, **204** per i 5 siti italiani.
4. **Volumi reali**: gli URL S3 compaiono nei `links` della CoverageJSON con finestra stretta
   (12:30–12:50Z). 20 file ODIM HDF5 scaricati e parsati (`h5py`): `/where` (height/lat/lon), `/how`
   (`NI`, `beamwidth`, PRF, `software`), `/what` (date/time, `source`), `/dataset1/where`
   (`elangle`, `nbins`, `rscale`, `nrays`, `a1gate`), `/dataset1/dataN/what` (`quantity`, `gain`,
   `offset`, `nodata`, `undetect`).
5. **Insidie verificate**: la terza coordinata di `geometry.coordinates` in `/items` è il **livello di
   scansione**, NON l'altitudine (es. frale: coordinate[2]=2,5 ma `/where.height`=**63 m**);
   l'altitudine in tabella viene solo da ODIM `/where`. I valori VRADH grezzi sono **quantizzati**:
   frale `gain=0,5 / offset=-60` → intervallo fisico **−36…+67 m/s**; fraja **−60…+67 m/s**;
   defbg `gain=0,003906 / offset=-128` → **−128…+22,98 m/s** (valore estremo, dealiasing non
   applicato); chppm `gain=-0,09744 / offset=12,33` → **≈+12…−12 m/s**.
6. **NI** in tabella è l'**attributo ODIM `/how/NI`** (es. frale 62,0555, fraja 58,6782). Non va
   confuso con il conteggio di dataset per elevazione (catalogo): frale 10, fraja 8. Per DE/CH/HU
   l'attributo `/how/NI` è assente → **NON VERIFICATO**.

## 5. Licenza e throttling

- **Licenza**: i prodotti ORD single-site sono **CC BY 4.0** (link `rel=license` nella collection),
  ma i siti Météo-France dichiarano **Etalab 2.0** nei metadati (`license` dei feature e CoverageJSON).
  I volumi restano proprietà del provider nazionale; EUMETNET OPERA non applica centralmente il
  dealiasing di VRADH e la qualità/QC dipende dalla catena nazionale. Attribuzione da riportare:
  «(c) EUMETNET OPERA — Open Radar Data / MeteoGate». I dati dual-pol non sono ancora scambiati
  nel flusso OPERA (previsti dal 2027, TBD).
- **Throttling usato**: nessuna API key (modalità anonima). Pausa di **1,0–1,5 s** fra chiamate API
  e **0,6 s** fra download S3. Il response header `X-RateLimit-Remaining` è stato monitorato:
  da **198** a ~170 nel corso delle ~35 richieste API della sessione (nessun 429 osservato).
  Nessuna credenziale, token o PAT è stata usata o scritta su disco.

## 6. Comandi di riproduzione (essenziati)

```bash
# catalogo e VRADH
curl -s 'https://api.meteogate.eu/eu-eumetnet-weather-radar/collections?f=json'
curl -s 'https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/items?bbox=4.0,34.0,21.0,49.0&datetime=2026-11-19T08:00Z/2026-11-19T12:00Z&f=GeoJSON'
# singolo sito (200) e sito italiano (204)
curl -s -o /dev/null -w '%{http_code}' 'https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/locations/0-250-0-frale?datetime=2026-11-19T12:30Z/2026-11-19T12:50Z&standard_name=VRADH&format=ODIM&f=CoverageJSON'
curl -s -o /dev/null -w '%{http_code}' 'https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/locations/0-380-0-itfos?datetime=2026-11-18T00:00Z/2026-11-19T23:59Z&standard_name=VRADH&format=ODIM&f=CoverageJSON'
# volume ODIM reale (backup S3)
curl -sO 'https://object-store.os-api.cci1.ecmwf.int/openradar-24h-backup/2026/11/19/FR/frale/SCAN/frale@20261119T1244@0.6@DBZH_TH_VRADH.h5'
```

## 7. Dichiarazione di completezza

- Il campione è una **finestra di ~24 h** (cache rolling ORD), non una climatologia: la disponibilità
  di un sito può cambiare domani.
- `frale`/`fraja` coprono geometricamente la Corsica e parte del Tirreno; la copertura effettiva
  dell'Italia continentale con VRADH **non è stata misurata** in questo audit (servirebbe un'analisi
  di fascio/range-gate, non eseguita).
- Per i 5 siti croati l'assenza di file ODIM scaricabili è **osservata** (S3 `KeyCount=0` sul prefix
  `2026/11/19/HR/<id>/SCAN/` e nessun link nei `links` della CoverageJSON), non dedotta.


## 8. Tabella generata dal codice — misura del 2026-11-22

Fonte: `scripts/radar_engine/site_availability.py` (`build_table` / CLI
`python -m radar_engine.site_availability`). Misurata alle **2026-11-22T07:43:25Z** su
**30 siti**, con paginazione S3 COMPLETA (una sola pagina troncava i siti DE
a 1000 chiavi e li faceva sembrare stale: con paginazione l'ultimo scan e'
delle 07:30Z). Nessun dato sintetico: ORD `/locations`, S3 backup 24h e
header ODIM parsati con `h5py`.

```bash
python -m radar_engine.site_availability --day 2026/11/22 \
  --out data/radar/radar_availability.json --markdown radar_availability.md
```

### 8.1 I 4 campi prima mancanti (contratto #207)

Ogni campo e' un dict `{value, null_reason, source, reference}`; un `value`
nullo ha **sempre** un `null_reason` verificabile (il costruttore solleva
`ValueError` altrimenti).

| Campo | Riempito da | Valore misurato (frale, 22/11) |
| :--- | :--- | :--- |
| `radar_name` | ORD `/locations` `properties.name` | `Aleria` |
| `variables` | token prodotto nei nomi chiave S3 + `/dataset1/dataN/what/quantity` | `DBZH, TH, VRADH` |
| `elevation_angles` | token elevazione nei nomi chiave S3 + `/dataset1/where/elangle` | 10 angoli: 0.6° … 90° |
| `quality_score` | **NESSUNA FONTE REALE** | `null` — `odim_no_quality_group_and_no_ord_quality_field` |

⚠️ **`quality_score` e' impossibile da riempire con i dati disponibili.**
Misurato il 22/11: **0/20** file ODIM reali contengono un gruppo `/quality`
(gruppi presenti: `dataset1`, `how`, `what`, `where`) e la CoverageJSON ORD
non ha **alcun** campo quality (visita ricorsiva: 0 hit). Il campo e'
pubblicato come `null` con `null_reason` esplicito: NON e' un punteggio
inventato ne' un proxy (né NI, né frazione di gate validi, né banda 2 del
composito CIRRUS, che e' un prodotto diverso).

### 8.2 I 5 stati: criteri operativi e conteggi reali

| Stato | Criterio esatto | Conteggio 22/11 |
| :--- | :--- | ---: |
| `invalid` | HTTP 200 ma parsing ODIM fallito, `/where` o `/dataset1` assenti, lat/lon fuori range o VRADH con `|v| > 150 m/s` | 0 |
| `stale` | scan reale con età > **60 min** (`STALE_MAX_AGE_MIN`, soglia della sezione 3) | 0 |
| `unavailable` | ORD 204/404/errore di rete **e** `KeyCount=0` su S3 (nessun dato) | 5 |
| `partial` | dato reale ma almeno un campo nullo: oggi qualità sempre assente; oppure solo catalogo ORD senza file ODIM (5 siti HR); oppure VRADH assente | 25 |
| `ok` | scan ODIM reale, tutti e 13 i campi valorizzati, entro 60 min | 0 |

Il valore `ok` e' **0 oggi non per un difetto di codice**: e' la conseguenza
misurata dell'assenza del quality score in tutti i prodotti. Se un provider
pubblicasse un campo quality, lo stesso codice classificherebbe quei siti
`ok` senza modifiche (test `test_five_states_are_operational_and_distinct`).
I 20 siti con ODIM reale hanno **12/13 campi valorizzati**; i 5 siti HR hanno
solo il catalogo (lat/lon/nome reali da ORD, ODIM non scaricabile: S3 0
chiavi, come nel doc); i 5 siti IT hanno 0 campi (HTTP 204).

### 8.3 Tabella compilata (30 righe, 14 colonne)

| RADAR_ID | RADAR_NAME | LATITUDE | LONGITUDE | ALTITUDE | NI | VARIABLES | ELEVATION_ANGLES | RANGE | RESOLUTION | UPDATE_INTERVAL | LAST_AVAILABLE_SCAN | QUALITY_SCORE | STATO |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| itbri | NON VERIFICATO (absent_from_ord_catalog_153_platforms) | NON VERIFICATO (lat_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (lon_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (ord_204_and_s3_0_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | unavailable — ord_204_and_s3_0_keys |
| itset | NON VERIFICATO (absent_from_ord_catalog_153_platforms) | NON VERIFICATO (lat_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (lon_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (ord_204_and_s3_0_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | unavailable — ord_204_and_s3_0_keys |
| itdes | NON VERIFICATO (absent_from_ord_catalog_153_platforms) | NON VERIFICATO (lat_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (lon_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (ord_204_and_s3_0_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | unavailable — ord_204_and_s3_0_keys |
| itfle | NON VERIFICATO (absent_from_ord_catalog_153_platforms) | NON VERIFICATO (lat_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (lon_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (ord_204_and_s3_0_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | unavailable — ord_204_and_s3_0_keys |
| itfos | NON VERIFICATO (absent_from_ord_catalog_153_platforms) | NON VERIFICATO (lat_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (lon_unavailable_in_odim_and_ord_catalog) | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (ord_204_and_s3_0_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | unavailable — ord_204_and_s3_0_keys |
| frale | Aleria | 42.12971 | 9.49639 | 63.0 | 62.056 | DBZH, TH, VRADH | 0.6, 1.0, 1.4, 2.5, 3.5, 5.0, 6.5, 8.0, 9.5, 90.0 | 256.3 | 960 m range, 1.24 deg azimuth | 5.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| fraja | Ajaccio | 41.95313 | 8.70054 | 784.0 | 58.47 | DBZH, TH, VRADH | 0.4, 0.8, 1.4, 2.2, 3.6, 4.8, 6.0, 90.0 | 256.3 | 960 m range, 1.1 deg azimuth | 5.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| hrbil | Bilogora | 45.88353 | 17.20046 | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (no_s3_scan_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — no_scan_time_but_some_data |
| hrdeb | Debeljak | 44.04516 | 15.37637 | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (no_s3_scan_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — no_scan_time_but_some_data |
| hrgol | Goli | 45.02048 | 14.12234 | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (no_s3_scan_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — no_scan_time_but_some_data |
| hrgra | Gradiste | 45.15917 | 18.70333 | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (no_s3_scan_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — no_scan_time_but_some_data |
| hrulj | Uljenje | 42.89439 | 17.47829 | NON VERIFICATO (odim_where_height_unavailable) | NON VERIFICATO (odim_how_NI_absent) | NON VERIFICATO (no_s3_scan_keys_to_read_quantity) | NON VERIFICATO (no_s3_scan_keys_to_read_elangle) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (odim_grid_geometry_unavailable) | NON VERIFICATO (fewer_than_two_scan_stamps) | NON VERIFICATO (no_s3_scan_keys) | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — no_scan_time_but_some_data |
| frbla | Blaisy | 47.35515 | 4.77594 | 607.2 | 58.678 | DBZH, TH, VRADH | 0.47, 0.51, 1.0, 1.61, 2.58, 2.6, 2.62, 3.6, 4.6, 6.0, 8.0, 90.0 | 256.3 | 960 m range, 1.1 deg azimuth | 35.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| frbol | Bollene | 44.32305 | 4.76221 | 325.0 | 62.1 | DBZH, TH, VRADH | 0.4, 0.8, 1.2, 1.8, 2.4, 3.5, 5.0, 89.99, 90.0 | 256.3 | 960 m range, 1.24 deg azimuth | 5.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| frcol | Collobrieres | 43.21656 | 6.37291 | 653.7 | 61.879 | DBZH, TH, VRADH | 0.39, 0.4, 0.79, 0.8, 1.39, 1.4, 2.19, 2.2, 3.59, 3.6, 4.79, 4.8, 6.0, 89.99, 90.0 | 256.3 | 960 m range, 1.25 deg azimuth | 25.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| frmtc | Montancy | 47.36861 | 7.01897 | 925.6999999999999 | 58.637 | DBZH, TH, VRADH | 0.4, 0.7, 1.2, 2.2, 3.0, 3.91, 5.0, 6.0, 7.0, 90.0 | 256.3 | 960 m range, 1.1 deg azimuth | 5.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| frnan | Nancy | 48.71576 | 6.58156 | 296.3 | 58.668 | DBZH, TH, VRADH | 0.7, 1.3, 1.9, 2.59, 2.6, 3.59, 3.6, 4.59, 4.6, 5.59, 5.6, 6.59, 6.6, 7.6, 8.59, 8.6, 9.6, 89.99 | 256.3 | 960 m range, 1.1 deg azimuth | 5.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| frnim | Nimes | 43.80614 | 4.50269 | 78.1 | 61.725 | DBZH, TH, VRADH | 0.59, 0.6, 1.2, 1.8, 2.39, 2.4, 3.5, 5.0, 6.49, 6.5, 8.0, 9.49, 9.5, 89.99, 90.0 | 256.3 | 960 m range, 1.25 deg azimuth | 70.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| frniz | StNizier | 46.06781 | 4.44535 | 919.8 | 58.626 | DBZH, TH, VRADH | 0.4, 0.99, 1.59, 1.6, 2.6, 3.6, 4.6, 5.6, 6.6, 7.6, 8.6, 9.6, 89.99, 90.0 | 256.3 | 960 m range, 1.1 deg azimuth | 5.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| frtro | Arcis | 48.46213 | 4.30932 | 164.79999999999998 | 58.512 | DBZH, TH, VRADH | 0.4, 1.1, 2.01, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 89.99, 90.0 | 256.3 | 960 m range, 1.1 deg azimuth | 5.0 | 2026-11-22T07:39:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:quality_score |
| defbg | defbg | 47.873611 | 8.003611 | 1516.1 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 8.0, 12.0, 17.0, 25.0 | 180.0 | 250 m range, 0.9 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| deisn | deisn | 48.174705 | 12.101779 | 677.77 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 8.0, 12.0, 17.0, 25.0 | 180.0 | 250 m range, 1.0 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| demem | demem | 48.042145 | 10.219222 | 724.4 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 8.0, 12.0, 17.0, 25.0 | 180.0 | 250 m range, 0.9 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| detur | detur | 48.585379 | 9.782675 | 767.62 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 8.0, 12.0, 17.0, 25.0 | 180.0 | 250 m range, 0.9 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| huhar | Harmashegy | 46.1775016784668 | 18.337230682373047 | 627.0 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.5, 1.1, 1.9, 3.0, 4.7, 6.5, 7.0, 10.0, 14.0, 14.2, 20.0 | 240.0 | 125 m range | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| chalb | chalb | 47.284332275390625 | 8.51200008392334 | 938.0 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.4, 1.0, 1.6, 2.5 | 246.0 | 500 m range, 1.0 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| chdol | chdol | 46.425113677978516 | 6.099414825439453 | 1682.0 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.4, 1.0, 1.6, 2.5 | 246.0 | 500 m range, 1.0 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| chlem | chlem | 46.0407600402832 | 8.833216667175293 | 1626.0 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.4, 1.0, 1.6, 2.5 | 246.0 | 500 m range, 1.0 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| chppm | chppm | 46.37064743041992 | 7.486551761627197 | 2937.0 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.4, 1.0, 1.6, 2.5 | 246.0 | 500 m range, 1.0 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |
| chwei | chwei | 46.8349723815918 | 9.794458389282227 | 2850.0 | NON VERIFICATO (odim_how_NI_absent) | DBZH, TH, VRADH | 0.4, 1.0, 1.6, 2.5 | 246.0 | 500 m range, 1.0 deg azimuth | 5.0 | 2026-11-22T07:40:00Z | NON VERIFICATO (odim_no_quality_group_and_no_ord_quality_field) | partial — missing_fields:ni,quality_score |

### 8.4 Dichiarazione di completezza

- I 5 ID italiani (`itbri`, `itset`, `itdes`, `itfle`, `itfos`) non compaiono
  nei 153 platform del catalogo ORD: le coordinate restano `null` motivate,
  non stimate dall'ID.
- L'eta' degli scan e' calcolata rispetto all'istante della misura
  (`2026-11-22T07:43:25Z`): tutti i siti con ODIM erano entro ~4 min, quindi nessuno
  `stale` oggi. La soglia 60 min e' verificabile e non e' stata allentata.
- `update_interval` e' il delta mediano fra i file S3 **dell'elevazione
  minima** (cadenza del volume = 5 min; i singoli file sono a 1 min, come
  gia' dichiarato nella sezione 4).
