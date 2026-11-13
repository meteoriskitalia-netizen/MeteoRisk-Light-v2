# Fixture del layer criticita' DPC

`index.json` e `topo.json` sono un SOTTOINSIEME REALE del bollettino del
**13 novembre 2026 ore 14:34** del Dipartimento della Protezione Civile:

- repository: <https://github.com/pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica>
- indice originale: `files/20261113_1434.json`
- TopoJSON originale: `files/topojson/20261113_1434_today.json` (1.212.952 byte, 187 geometrie)
- licenza: **CC BY 4.0** — attribuzione: **Dipartimento della Protezione Civile**

Riduzione (nessun dato inventato, nessuna proprieta' modificata):

- 7 zone su 187, scelte per coprire i casi utili: tutte "nessuna" (`Bacini
  Tordino Vomano`), arancione idraulico e idrogeologico (`Serchio-Garfagnana-Lima`,
  `Bisenzio e Ombrone Pt`, `Chiascio - Topino`), giallo temporali (`Marsica`,
  `Litoranea`) e un `MultiPolygon` (`Piana Sele e Alto Cilento`);
- archi TopoJSON rimappati (44 su 9.165) tenendo solo quelli riferiti dalle zone;
- `index.json` conserva `date`, `name`, `style` e i link `today`/`tomorrow`
  riscritti su `fixtures/topo.json`.

La fixture si rigenera con `scripts/tests/hydro/gen_fixture.py` a partire dai
file originali (che NON sono nel repo: si scaricano dal DPC).
