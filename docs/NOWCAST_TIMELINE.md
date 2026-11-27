# Timeline del nowcast: 18 frame a passo uniforme (2026-11-27)

## Cosa cambia

| | prima | dopo |
| :--- | :--- | :--- |
| lead | 5..45 ogni 5 (9), 60/75/90 ogni 15 (3) = **12** | **5..90 ogni 5 = 18** |
| passo pubblicato | 5 / 15 | **5 per ogni frame** |
| salto visibile | 45 -> 60 -> 75 -> 90 | nessuno |

Il trasporto era gia' cumulativo a passi di 5 minuti: cambia solo la
pubblicazione dei frame intermedi 50/55/65/70/80/85, non il calcolo.

## Costo misurato (stesso slot 2026-11-27T17:25Z, stesso bundle)

| | 12 frame | 18 frame | rapporto |
| :--- | ---: | ---: | ---: |
| file WebP | 12 | **18** | +50% |
| byte totali | 423.658 | **625.298** | **1,476x** |
| compute `nf.run` | 36,98 s | **51,37 s** | **1,389x** |
| preload client | ~424 kB | **~625 kB** | 1,476x |
| immagine nuvole | una per slot | una per slot | invariata |

I 625 kB sono la somma dei 18 WebP di pioggia; il layer nuvole e' una sola
immagine separata (non entra nel preload del player). Il client costruisce
slider e preload da `leads[]`: nessun numero 12 hardcodato.

Dettaglio per lead (byte): 5=36.520, 10=36.408, 15=36.284, 20=36.102,
25=36.036, 30=35.886, 35=35.694, 40=36.052, 45=34.900, 50=34.472,
55=34.188, 60=34.600, 65=33.664, 70=33.910, 75=32.990, 80=32.896,
85=32.510, 90=32.186.

Report: `docs/evidence/nowcast_timeline18_20261127/timeline18_report.json`.

## Verifica live (slot 2026-11-27T18:25Z)

`nowcast.json` live:

```
leads_total = 18
leads = 5,10,15,20,25,30,35,40,45,50,55,60,65,70,75,80,85,90
step_min = 5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5
```

HTML live 3.463.897 B; nuvole live 16.396 B (591x636, downsample 2).

## Test

`test_nowcast_field` aggiornato: 18 lead, `leads_total=18`, `step_min=5`
per tutti, 18 file distinti, `+90` resta cumulativo (18 passi da +5).
`test_nowcast_archive_verify`: 15 frame del player non verificati
(18 - 3 ufficiali), non piu' 9.

## Limiti

- Il costo compute cresce del **38,9%** e i byte del **47,6%**: accettato
  perche' elimina il salto visibile e resta sotto 1 MB di preload.
- I lead oltre +90 restano rimossi (troncamento dichiarato): l'aspetto p90
  supera 1,5x l'osservato a +120.
