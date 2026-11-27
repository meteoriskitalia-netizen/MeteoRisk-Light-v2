# Controlli del prodotto Nowcast (2026-11-27)

## Prima

- `#live-toggle-nowcastprob` (Probabilita' nowcast) e
  `#live-toggle-nowcastclouds` (Nuvole IR) stavano nel **pannello LIVE**,
  accanto a Struttura convettiva, Centraline, Doppler.
- I due layer erano indipendenti: con la probabilita' accesa l'immagine
  poteva sovrapporsi all'intensita'.

## Dopo

Tutti i controlli sono **dentro `#nowcast-panel`**, il pannello del prodotto:

| controllo | posizione | comportamento |
| :--- | :--- | :--- |
| **Vista** | `#nc-view-row` | `Intensita'` / `Probabilita'`, mutuamente esclusivi |
| **Soglia** | `#ncprob-controls` | 2/5/10 mm/h, visibile solo con Vista=Probabilita' |
| **Entro** | `#ncprob-controls` | 15/30/60/90 min, visibile solo con Vista=Probabilita' |
| **Nuvole IR** | `#nc-clouds-toggle` | toggle separato, contesto, non accende la pioggia |

Il pannello LIVE non contiene piu' nessun controllo specifico del nowcast.

## Mutua esclusione

`nowcastSetView(view)`:

- **Probabilita'**: il layer intensita' viene portato a `opacity = 0` (non
  rimosso, cosi' il player non perde il frame) e la probabilita' viene
  disegnata su un pane dedicato **z 408**, sopra la pioggia (z 407) ma con
  la pioggia invisibile: **non c'e' sovrapposizione**.
- **Intensita'**: il layer probabilita' viene rimosso e l'intensita' torna a
  `opacity = 0.75`.
- Cambiare vista aggiorna subito; se `ensemble` non e' disponibile il
  pannello mostra il motivo e non lascia un layer orfano.
- `nowcastApplyFreshData` riaggiorna la probabilita' se la vista e' prob.

Le nuvole restano un layer separato: pane **z 406**, sotto la pioggia,
toggle indipendente; non vengono spente dal cambio Vista.

## Verifica

- **Browser reale** sulla pagina live (2026-11-27, prodotto Nowcast):
  `#nowcast-panel` visibile; `nc-view-intensity`, `nc-view-prob`,
  `nc-clouds-toggle`, `ncprob-controls` presenti; `live-toggle-nowcastprob`
  e `live-toggle-nowcastclouds` ASSENTI.
- Clic su **Probabilita'**: `nowcastView = "prob"`, immagine
  `nowcast_prob015.webp` visibile (opacity 0,72), immagine
  `nowcast_lead005.webp` a **opacity 0**: nessuna sovrapposizione.
- Clic su **Intensita'**: probabilita' rimossa,
  `nowcast_lead005.webp` torna a **opacity 0,75**.
- `test_cloud_toggle_is_separate.py`: 2 test (controlli nel pannello del
  prodotto, non nel LIVE; mutua esclusione con `setOpacity(0)` e pane 408;
  nuvole z 406).
- `test_html_js_syntax`: 1 passed; `test_tooltips_coverage`: 2 passed.
- `radar_engine`: 1006 passed.

## Limiti

- Il selettore Vista non cambia il calcolo: mostra due prodotti gia'
  calcolati (intensita' e probabilita'). A +90 l'ensemble attuale produce
  ancora i lead ufficiali 15/30/60 (il 90 e' selezionabile ma puo' non avere
  immagine: il pannello lo dichiara).
- La probabilita' verificata e' su celle di 20 km; a 1 km la struttura fine
  non e' prevista.
