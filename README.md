# nowcast-ledger — ledger compatto della verifica del nowcast

Un file **per giorno UTC**, compresso, con record binari da **1102 byte**
(schema `meteorisk.nowcast_ledger/1`):

    nowcast_ledger/YYYY/MM/DD.bin.zst

Ogni record contiene, per lo slot sorgente del nowcast e per i 3 lead
ufficiali (15/30/60 min):

- **istogramma congiunto 8x8** di (previsto, osservato) in mm/h, con bordi
  `0, 0.1, 0.5, 1, 2, 5, 10, 20, inf`: le soglie 0,5/2/5 sono bordi esatti,
  quindi CSI/POD/FAR si possono RICALCOLARE per qualunque soglia futura che
  cada su un bordo; per una soglia interna a un bin il modulo restituisce un
  intervallo, non un numero inventato;
- **contatori** hits/misses/false alarms/correct negatives (3 lead x 3 soglie);
- **FSS** (Roberts & Lean 2008) per 3 lead x 3 soglie x 3 intorni
  (9/25/49 px), calcolato al momento dell'ingestione: non e' ricostruibile
  dall'istogramma.

Nessuna potatura: il ledger e' l'archivio lungo (~27 MB/anno con zstd).
Il branch `nowcast-archive` (finestra 7 giorni) resta separato e invariato.

**Attribuzione.** I dati di partenza sono il prodotto **SRI del Radar-DPC**
(Dipartimento della Protezione Civile), licenza **CC BY-SA 4.0**. Questo
ledger e' un'opera derivata: va attribuito alla fonte e condiviso con la
stessa licenza. Fonte: <https://radar-api.protezionecivile.it>

Scritto da: job `radar-engine`, step "Pubblica ledger verifica
(branch nowcast-ledger)". Pubblicato con commit ORPHAN senza parent e
`--force-with-lease` (mai `--force` nudo).
