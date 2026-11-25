# Nowcast ledger — archivio compatto della verifica

Stato: **1.3.0.80 BUILD=219** (backend, `APP_FILE` invariato).
Modulo: `scripts/radar_engine/nowcast_ledger.py`.
Aggancio: `scripts/radar_engine/nowcast_verify.py --ledger-dir`.
Branch: **ORPHAN `nowcast-ledger`**, un file per giorno UTC
`nowcast_ledger/YYYY/MM/DD.bin.zst`.

## Perche' esiste

`data/radar/nowcast_verification.json` e' una serie **operativa** a 7 giorni:
viene riscritta e non accumula. Per una validazione statistica servono anni di
coppie (previsto, osservato), ma i raster costano ~20 MB/slot (~5,8 GB/giorno).
Il ledger conserva un riassunto **fedele e compatto**: istogramma congiunto
8x8, contatori di contingenza e FSS. Con l'istogramma le soglie future si
possono **ricalcolare** (variante B), cosa che un archivio di soli CSI/POD/FAR
non permette.

## Formato del record (`meteorisk.nowcast_ledger/1`)

Little-endian, **1102 byte**, nessun padding:

| Sezione | Byte | Contenuto |
| :--- | ---: | :--- |
| header | 28 | magic `NLGR`, version u16, flags u16, slot_ms u64, generated_ms u64, grid_w u16, grid_h u16 |
| lead | 54 | 3 x (lead_min u16, valid_s u32, domain_pixels u32, n_obs_rain u32, flags u32) |
| contatori | 144 | 3 lead x 3 soglie x 4 u32 (hits, misses, false alarms, correct negatives) |
| FSS | 108 | 3 lead x 3 soglie x 3 intorni (9/25/49 px) float32, NaN = non definito |
| istogrammi | 768 | 3 lead x 8x8 u32 (riga = bin previsto, colonna = bin osservato) |

`flags` header: bit0 = licenza CC BY-SA 4.0, bit1 = fonte Radar-DPC.
`flags` lead: bit0 = presente, bit1 = calcolato.

### Bordi dell'istogramma (obbligatori)

```
hist_edges = [0, 0.1, 0.5, 1, 2, 5, 10, 20, inf]   mm/h
```

Le soglie verificate **0,5 / 2 / 5** sono **bordi esatti**. Con bordi diversi
lo scarto misurato sul CSI e' **0,29-0,65**: un numero che sembra una verifica
e non lo e'. Per una soglia **non** di bordo `contingency_from_hist()` non
restituisce un valore: restituisce l'**intervallo** min/max sui vertici
ammissibili del bin che la contiene (`exact: false`, `counts: null`,
`scores: null`).

Il **FSS non e' ricostruibile** dall'istogramma (dipende dalla disposizione
spaziale, non solo dalla distribuzione congiunta): si calcola al momento
dell'ingestione e si archivia.

## Costo

| Voce | Misura |
| :--- | :--- |
| istogramma 8x8, griglia 1200x1400 | 0,071 s |
| FSS 3 lead x 3 soglie x 3 intorni | 1,091 s |
| **ledger, per slot (misurato nel check reale)** | **1,88 s** |
| rete aggiuntiva | **0** (stessi array `pred/obs/dom` della verifica) |

La prima stesura (numpy puro, `den` ricalcolato due volte) costava 9,04 s/slot:
e' stata sostituita con `scipy.ndimage.uniform_filter` (dipendenza gia'
obbligatoria del motore) e con `den`/`ok` calcolati 3 volte per lead invece
di 9. Nessuna asserzione allentata.

Dipendenza: `zstandard>=0.22` (in `scripts/radar_engine/requirements.txt`),
con fallback gzip automatico. Il codec e' **uno per giorno**: se il file del
giorno esiste, l'append riusa il suo codec (mai `.bin.zst` + `.bin.gz` per lo
stesso giorno).

## Prova su dati reali

`scripts/validation/nowcast_ledger_check.py`, finestra
`2026-11-23T00:25Z -> 14:25Z`, 15 ancore orarie, 135 download SRI:

- **315/315** righe di soglia ricostruite dall'istogramma, identiche al calcolo
  diretto (15 slot x 3 lead x 7 bordi finiti), 0 mismatch;
- **405/405** valori FSS archiviati identici al ricalcolo sul raster;
- **45/45** intervalli per la soglia non di bordo 3,0 mm/h contengono il valore
  diretto;
- report: `audit/nowcast_ledger_check15.json`.

## Byte per anno (misurati, non stimati)

288 slot/giorno (SRI ogni 5 min):

| Misura | zstd | gzip |
| :--- | ---: | ---: |
| giorno reale, 15 record: 16.530 B -> | 8.313 B (554,2 B/record) | 8.604 B (573,6 B/record) |
| **stessa densita' per 1 anno** | **58,3 MB** | **60,3 MB** |
| record compresso da solo | 643,3 B | 676,9 B |
| -> anno (standalone, frame overhead incluso) | 67,6 MB | 71,2 MB |
| record a istogramma vuoto | 52 B | 53 B |
| -> anno (tutti vuoti) | 5,5 MB | 5,6 MB |

**Il valore 27,3 MB/anno (zstd) / 34,4 (gzip) non e' riprodotto** da questo
campione: corrisponde a ~40% di record con pioggia, mentre 40/40 slot
campionati su 30 giorni avevano pioggia (5,48% del dominio in media), quindi
il dato reale sta al limite alto. Diverge dal valore atteso ed e' dichiarato,
non arrotondato. Il ledger resta sostenibile (~0,06 GB/anno contro un repo da
~25 GB): **nessuna potatura**.

## Retention

- `nowcast-ledger`: **nessuna potatura**. E' l'archivio lungo.
- `nowcast-archive` (7 giorni, copia leggera dei nowcast emessi): **resta com'e'**,
  invariato.

## Attribuzione

Dati derivati **Radar-DPC** — prodotto SRI, licenza **CC BY-SA 4.0**. La
licenza e la fonte sono nei flag dell'header di ogni record e in `README.md` /
`LICENCE.md` alla radice del branch. Nessuna dipendenza esterna: R2/B2
scartati (decine di MB/anno non giustificano un account terzo e un token).

## Riproduzione

```bash
# prova su SRI reali (rete): 15 ancore, 315 righe di soglia
python scripts/validation/nowcast_ledger_check.py \
  --from 2026-11-23T00:25:00Z --to 2026-11-23T14:25:00Z \
  --step-min 60 --max-slots 15 \
  --ledger-dir "$TEMP/nowcast_ledger" \
  --out audit/nowcast_ledger_check15.json

# test unitari (nessuna rete)
pytest scripts/tests/radar_engine/test_nowcast_ledger.py -q

# ispezione di un giorno
python scripts/radar_engine/nowcast_ledger.py \
  --root "$TEMP/nowcast_ledger" --inspect-day 2026-11-23
```
