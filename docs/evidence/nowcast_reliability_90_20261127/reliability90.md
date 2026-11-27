# Affidabilita' a +30/+60/+90 - criterio fissato prima

Criterio: area 0,5 e 5 mm/h in [0,6;1,4]; centroide <=20 km; copertura >=50% dell'osservato >=5 mm/h.

| ancora | regime | +30 | +60 | +90 |
| :--- | :--- | :---: | :---: | :---: |
| 2026-11-01T16:00:00Z | convettivo veloce | OK | no: centroid,coverage_ge5 | no: centroid,coverage_ge5 |
| 2026-10-20T16:00:00Z | convettivo veloce | OK | no: coverage_ge5 | no: centroid,coverage_ge5 |
| 2026-09-10T16:00:00Z | convettivo | no: centroid | no: centroid,coverage_ge5 | no: centroid,coverage_ge5 |
| 2026-10-08T16:00:00Z | convettivo | no: coverage_ge5 | no: centroid,coverage_ge5 | no: centroid,coverage_ge5 |
| 2026-11-26T13:25:00Z | convettivo veloce | OK | no: centroid,coverage_ge5 | no: centroid,coverage_ge5 |
| 2026-11-09T16:00:00Z | stratiforme veloce | no: area_5.0,centroid,coverage_ge5 | no: centroid,coverage_ge5 | no: area_5.0,centroid,coverage_ge5 |
| 2026-05-29T12:00:00Z | stratiforme | no: coverage_ge5 | no: area_5.0,centroid,coverage_ge5 | no: area_5.0,centroid,coverage_ge5 |
| 2026-08-29T16:00:00Z | stratiforme | no: area_0.5,area_5.0,centroid,coverage_ge5 | no: area_0.5,area_5.0,centroid,coverage_ge5 | no: area_0.5,centroid,coverage_ge5 |
| 2026-07-27T12:00:00Z | debole | no: area_5.0,centroid,coverage_ge5 | no: area_5.0,centroid,coverage_ge5 | no: area_5.0,centroid,coverage_ge5 |
| 2026-09-30T12:00:00Z | debole | no: centroid,coverage_ge5 | no: centroid,coverage_ge5 | no: centroid,coverage_ge5 |
| 2026-07-08T12:00:00Z | debole | no: area_5.0,centroid,coverage_ge5 | no: area_5.0,centroid,coverage_ge5 | no: area_0.5,area_5.0,centroid,coverage_ge5 |
| 2026-08-13T16:00:00Z | debole | no: centroid,coverage_ge5 | no: area_5.0,centroid,coverage_ge5 | no: area_5.0,centroid,coverage_ge5 |

## Percentuale affidabile

| lead | casi | affidabili | frazione |
| ---: | ---: | ---: | ---: |
| +30 | 12 | 3 | 25.0% |
| +60 | 12 | 0 | 0.0% |
| +90 | 12 | 0 | 0.0% |

## Copertura / IoU / bias / correlazione (media)

| lead | cop 0,5 | IoU 0,5 | bias 0,5 | cop 5 | IoU 5 | bias 5 | corr 0,5 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| +30 | 0.5477 | 0.422 | 1.0577 | 0.3757 | 0.2212 | 1.5362 | 0.5414 |
| +60 | 0.4243 | 0.3063 | 1.0646 | 0.2154 | 0.1351 | 1.9414 | 0.4149 |
| +90 | 0.3533 | 0.2389 | 1.0746 | 0.1361 | 0.0784 | 0.9874 | 0.3371 |
