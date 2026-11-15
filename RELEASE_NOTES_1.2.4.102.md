# MeteoRisk Light 1.2.4.102

## Riparazione: mappa dei rischi vuota su live

Su live (1.2.4.101) il calcolo STP dentro `computeSevereIndices` usava due
identificatori non dichiarati (`lcl` e `cin`). La `ReferenceError` veniva
intercettata dal caricamento del dataset derivato (`loadStaticDataset`), che
annullava l'intero caricamento: `datasetState.loaded = false`, `pointCount = 0`,
zero badge di rischio sulla mappa e card "Zona selezionata" vuota. Radar,
satellite e altre sezioni restavano funzionanti perche' leggono altre fonti.

Riparazione minima e fedele all'intento:
- `lcl` ora e' calcolato da `temperature_2m` / `dew_point_2m` via `computeLclApprox`;
- `cin` ora e' letto da `convective_inhibition`;
- fallback neutro: LCL mancante -> `0.5`, CIN mancante -> nessuna penalizzazione.

Logica, soglie e indici invariate. La .101 resta inalterata.