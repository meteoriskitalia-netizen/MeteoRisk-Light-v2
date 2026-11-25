# MeteoRisk Light 1.3.0.81 — nowcast a +3 ore con passo crescente e affidabilità misurata

Bump da **1.3.0.80** (BUILD 220 -> **221**). APP_FILE:
`mri-light-1.3.0.81.html`.

## 1) 15 frame fino a +180 minuti

| fascia | lead (min) | passo |
| :--- | :--- | ---: |
| centrale | 5, 10, 15, 20, 25, 30, 35, 40, 45 | 5 min |
| estesa | 60, 75, 90 | 15 min |
| lunga | 120, 150, 180 | 30 min |

Il **trasporto resta cumulativo** a passi di 5 minuti: il +180 è il +5 avanzato
36 volte, non un salto né 15 trasporti indipendenti. Immagini
`nowcast_lead005.webp` … `nowcast_lead180.webp` (15 file).

`nowcast.json` pubblica `leads_total: 15`, `official_leads: [15, 30, 60]`
invariati, e per ogni lead `step_min`, `valid_time`, `image`, `official`,
`reliability`. La soglia per l'etichetta è `low_reliability_over_min: 90`.

## 2) Affidabilità per orizzonte, misurata contro la persistenza

Backtest `scripts/validation/nowcast_reliability_backtest.py` sulle **stesse 12
giornate** del backtest della cascata (già disponibili), 2 ancore (11:00 e 15:00)
= **24 casi**, SRI osservato, FSS a 15 km. Risultato in
`data/radar/nowcast_reliability.json` e in `docs/NOWCAST_RELIABILITY.md`,
incorporato in `nowcast.json` come `reliability_by_lead`.

| lead | CSI nowcast | CSI persistenza | FSS nowcast | FSS persistenza |
| ---: | ---: | ---: | ---: | ---: |
| +30 | 0,518 | 0,458 | 0,614 | 0,629 |
| +60 | 0,372 | 0,344 | 0,402 | 0,430 |
| +90 | 0,293 | 0,285 | 0,301 | 0,347 |
| +120 | 0,242 | 0,241 | 0,236 | 0,288 |
| +150 | 0,190 | 0,197 | 0,180 | 0,228 |
| +180 | 0,161 | 0,174 | 0,144 | 0,193 |

**Dichiarato, non nascosto**: da +150 min il nowcast è **peggiore della
persistenza** su CSI; l'FSS è peggiore a **tutti** gli orizzonti. Le due metriche
non concordano a breve termine; il verdetto ufficiale (`beats_persistence`,
lead 15/30/60) resta invariato e separato.

## 3) Client

- etichetta `+25 min · 16:40` come prima; per i frame **oltre 90** minuti si
  aggiunge `· affidabilità bassa` (es. `+120 min · 18:15 · affidabilità bassa`);
- la frase generica «dopo 30 minuti l'affidabilità cala» è sostituita dai
  **numeri misurati** e viene aggiornata a runtime da `reliability_by_lead`;
- caption della timeline: `passo 5/15/30 min`;
- play/pausa, slider e preload sono gli stessi: leggono `leads[]`
  dinamicamente, una lacuna (`image: null`) salta il frame senza rompere il
  player.

## 4) Costi misurati

Stesso slot reale (2026-11-23T10:25Z, 6 frame SRI):

| voce | 12 frame | 15 frame | delta |
| :--- | ---: | ---: | ---: |
| `compute` | 8,64 s | **10,47 s** | +1,83 s |
| reproject + WebP | 4,68 s | **6,38 s** | +1,70 s |
| totale | 13,32 s | **16,85 s** | **+3,52 s (+26%)** |
| WebP totali | 147.366 B | **162.804 B** | +15.438 B (+10,5%) |
| picco RSS (processo di misura) | 478 MB | 574 MB | +96 MB |

Delta sotto il tetto di +8 s: nessuna riduzione proposta.

## 5) Verifica

- 15 WebP prodotti e HTTP 200 su Pages (verifica live in fondo al report).
- `pytest scripts/tests/radar_engine` completo.
- Nuovi: 4 test Python (15 lead, passo crescente, cumulatività a +180,
  `reliability_by_lead` con e senza file) e
  `scripts/tests/test_nowcast_lead_steps.mjs`.
- `test_html_js_syntax`, `test_tooltips_coverage`, `test_sw_cache_policy`,
  `node --check`.

## 6) Pulizia

I lead **050** e **055** non esistono più nel contratto: i due WebP vanno
rimossi dal repo (una sola app e un solo insieme di frame serviti da Pages).
