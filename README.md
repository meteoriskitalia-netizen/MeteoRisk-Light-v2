# Archivio SRT1 per provincia (opera derivata)

Statistiche orarie di cumulata di precipitazione (mm, finestra 1 h) per
provincia, calcolate sul mosaico **SRT1** di **Radar-DPC — Dipartimento
della Protezione Civile** (https://radar.protezionecivile.it).

## Licenza

- Dati di origine: Radar-DPC, **CC BY-SA 4.0**.
- Questo archivio e' un'**opera derivata** e viene ridistribuito sotto la
  stessa licenza **CC BY-SA 4.0** (share-alike), con attribuzione.
- Testo integrale: https://creativecommons.org/licenses/by-sa/4.0/deed.it
- Nessuna modifica e' applicata ai valori; sono aggregate statistiche
  (media, massimo, p90, frazione bagnata, copertura) su pixel di griglia
  radar 1 km con i confini provinciali rasterizzati.

## Formato

`YYYY/MM/DD/HH.json` in UTC, una ora per file. Chiavi principali:
`valid_time`, `totals`, `provinces` (per sigla). Per ogni provincia:
`mean`, `max`, `p90` (mm), `wet` (frazione pixel > 0 su pixel validi),
`valid` (frazione di pixel della provincia dentro la copertura radar),
`px` (pixel della provincia), `vpx` (pixel validi).

`valid` = 0.0 significa **provincia non coperta dal radar**. La
copertura CAMBIA da slot a slot (misurato: 2026-11-23T09:30Z -> 9
province siciliane sotto 0,5; 2026-11-13T19:10Z -> solo TP a 0,41):
non e' "0 mm misurati" e non va assunta costante.

Attribuzione: Dati: Radar-DPC — Dipartimento della Protezione Civile (https://radar.protezionecivile.it), licenza CC BY-SA 4.0
