# nowcast-archive — archivio leggero dei nowcast emessi

Un file JSON per slot (UTC): `nowcast_archive/YYYY/MM/DD/HHMM.json`.
Contiene solo i campi necessari alla verifica (slot, lead, valid_time,
disponibilita', moto, cap, riferimento ai WebP, statistiche e griglia).
**Non** contiene i WebP ne' il campo float: la verifica ricalcola il nowcast
dallo stesso slot con `scripts/radar_engine/nowcast_field.py`.

- Schema: `meteorisk.nowcast_archive/1`
- Lead: 15 frame a passo crescente (5..45 ogni 5 min, 60/75/90 ogni 15,
  120/150/180 ogni 30); `official: true` solo su 15/30/60, gli unici che la
  verifica confronta con la persistenza. Gli altri sono per il player.
- Retention: 7 giorni (potatura a ogni pubblicazione)
- Scritto da: job `radar-engine` (step "Archivia nowcast emesso")
- Pubblicato su: branch ORPHAN `nowcast-archive` (commit senza parent,
  `--force-with-lease`); mai su `main`, mai nell'artefatto Pages.
- Verifica: `scripts/radar_engine/nowcast_verify.py` ->
  `data/radar/nowcast_verification.json` (schema
  `meteorisk.nowcast_verification/2`).

Fonte del nowcast: prodotto DPC SRI (Surface Rainfall Intensity, mm/h).
Opera derivata Radar-DPC: CC BY-SA 4.0 (vedi `LICENCE.md`).
