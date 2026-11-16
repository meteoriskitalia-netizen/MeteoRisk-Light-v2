# Release 1.3.0.0 - 2026-11-16

Bump da 1.2.4.102. Nessun dato simulato nel sistema operativo: i test sintetici restano solo in scripts/tests/.

## Motore
- Doppler Analysis Engine (scripts/radar_engine/doppler.py): velocity couplets da VRADH OPERA FR, NI verificato, dealiasing region-based, 4 stati chiusi, quality geometry-dependent, vertical rotation, boundary_note esplicito.
- Mesocyclone Detection Engine (stesso modulo): track_rotations fra scansioni con eventi e coerenza con la cella. Nessun mesociclone confermato e nessun tornado.
- Volume 3D (build_volume.py, cross_section.py, volume3d.py, volume_publisher.py): volume.json con parametri per soglia 30/40/45/50/55 dBZ, sezioni compatte e provenienza DPC_CAPPI_stack / not_native_volumetric.

## Wiring
- main.py valorizza bundle.doppler_analysis, bundle.mesocyclone_tracks e pubblica volume.json; output.py pubblica riepiloghi limitati in latest.json e i payload completi in data/radar/doppler_analysis.json e mesocyclone_tracks.json.
- radar-engine.yml committano i tre nuovi file di data/radar.

## LIMITI NON VERIFICATI
- Storia fra scansioni non persistente tra run: in produzione il meso tracking vede la sola scansione corrente (nessun dato inventato per colmare la lacuna).
- DPC API HTTP 503 al collaudo: il percorso volume completo con CAPPI reali non e stato rieseguito end-to-end; coperto dai test e dalle misure della consegna (slot 2026-11-15T16:15Z).
- Nessuna validazione POD/FAR e nessuna taratura locale delle soglie.
