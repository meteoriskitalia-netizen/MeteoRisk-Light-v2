# Produttore di `data/radar/synoptic.json`

Consegna per MeteoRisk-Light-v2 / client **1.3.0.8** (HTML già pubblicato, NON toccato).
Nessun push eseguito. Nessun token. Nessun dato simulato.

## Contratto dati (schema esatto letto dal client)

```
{schema_version, generated_at, source:"open-meteo .om", license:"CC BY 4.0",
 domain, run_reference_time, valid_time, grid:{nx,ny,dx,dy},
 features:[{id,kind,geometry:[[lat,lon],...],label,confidence,fields,null_reason,reference}],
 availability:{fields_missing,horizon_exhausted}}
```

Provenienza e tempo sono **top-level**: il client li mostra per ogni feature nel
pannello di dettaglio (`synState.syn.valid_time/source/license`). La chiave
`reference` e' additiva e compare solo sulle feature `block` (e' la citazione
richiesta dalla regola scientifica); il client la ignora.

## Regole scientifiche vincolanti

| descrittore | campo reale | kind emesso | confidence | null_reason |
| :--- | :--- | :--- | :--- | :--- |
| blocco | `geopotential_height_500hPa` | **`block`** | `citation_only` | `null` se calcolato, motivo se non calcolabile |
| fronte | `temperature_850hPa` | `raw_field` | `uncalibrated` | `threshold_not_calibrated` |
| goccia fredda | `temperature_500hPa` (+Z500) | `raw_field` | `uncalibrated` | `threshold_not_calibrated` |
| convergenza | vento 850 (`speed`+`direction`) | `raw_field` | `uncalibrated` | `threshold_not_calibrated` |
| moist tongue | `relative_humidity_850hPa` | `raw_field` | `uncalibrated` | `threshold_not_calibrated` |
| burian | — | **nessuna feature** | — | — |

### L'unica soglia CITATA: Tibaldi–Molteni 1990

Per `Delta in {-4,0,+4}` gradi, con `phi_S=40+D`, `phi_0=60+D`, `phi_N=80+D`:

```
GHGS = (Z(phi_N) - Z(phi_0)) / 20   [m/grado]
GHGN = (Z(phi_0) - Z(phi_S)) / 20   [m/grado]
blocco  <=>  GHGS > 0  E  GHGN < -10 m/grado
```

Riferimento: Tibaldi & Molteni (1990), *Tellus A* 42(3):343-365,
doi:10.3402/tellusa.v42i3.11882. **Nessuna soglia aggiuntiva** (estensione minima,
persistenza, conteggio giorni) viene applicata: sarebbe una scelta locale non citata.
Il raggruppamento di longitudini contigue e' solo la geometria della polilinea.

Misurato su un frame REELE (`ecmwf_ifs025`, 2026-11-17T12:00Z): 3 segmenti di blocco;
il primo ha `GHGS=+4.7 m/deg`, `GHGN=-26.75 m/deg`, 172 longitudini.

### Perche' gli altri sono `raw_field`

Non esiste una soglia calibrata su dati italiani che trasformi un gradiente termico
in un fronte, un minimo termico in una goccia fredda, una divergenza del vento in una
convergenza o un massimo di umidita' in una lingua umida. Il produttore emette
l'**asse del valore estremo reale** del campo (per longitudine, la latitudine di
massimo/minimo) con etichetta che dice esplicitamente "campo grezzo ... soglia non
calibrata". La decimazione a 120 punti e' di rappresentazione, non una soglia.

**Gate client verificato**: il client 1.3.0.8 pubblicato manda i `raw_field` al ramo
neutrale di `synFeatureSvg` (grigio `#94a3b8`, nessun semicerchio/triangolo/H/B/C/freccia/
banda umida); solo `block` produce il simbolo `B` validato. Verifica automatica:
`node verify_client_gate.cjs` -> **18 PASS / 0 FAIL** sull'HTML di release.

## Dati e finestre

- Frame `.om` gia' nei branch `fm2-*`; nessun dominio nuovo. Mapping in `build_synoptic.py:DOMAIN_BRANCH`.
- Finestra massima `run_reference_time -> +168h` (`--window-hours`, tetto duro).
- Copertura misurata (dalla consegna): dwd_icon 170h, ECMWF 350h, GFS025 263h,
  GFS013 374h; buchi dwd_icon_eu 23h, dwd_icon_d2 44h, ARPEGE 92h.
  Se la copertura e' inferiore a 168h -> `availability.horizon_exhausted=true`.
  **Mai** si estrapola o si riempie un buco: viene scelto il frame reale piu' vicino.
- I punti Pages coprono 72h e NON sono in questo JSON: due timestamp separati nel client.

## Shim HTTP (omfiles)

`OmFileReader.from_fsspec` con `fsspec.HTTPFileSystem` e' incompatibile con la versione
di omfiles su questo host. `om_http.RangeHttpFS` implementa il contratto minimo che
omfiles usa davvero (`size(path)`, `cat_file(start=,end=,path=)`) traducendo `end`
esclusivo in Range HTTP inclusivo. Misurato: 11 richieste Range, array (281,1440)
identico alla lettura locale. Nessuna credenziale.

## Uso

```bash
# produzione (workflow synoptic.yml)
python -m scripts.synoptic.build_synoptic --out data/radar/synoptic.json \
    --domain ecmwf_ifs025 --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# test locale su fixture reale ridotta
python -m scripts.synoptic.build_synoptic --out out.json \
    --domain ecmwf_ifs025 --manifest fixtures/latest_block_reduced.json \
    --frame fixtures/ecmwf_ifs025_block_reduced.om \
    --generated-at 2026-11-17T12:00:00Z
python -m pytest scripts/tests/synoptic -q --capture=sys
node ../../verify_client_gate.cjs
```

## Dichiarato come non verificato

- Nessun run end-to-end di `synoptic.yml` su GitHub Actions (nessun push).
- Sintassi dei blocchi `run:` del workflow: solo `yaml.safe_load` ( ShellCheck/bash
  non disponibili su questo host ).
- Resa visiva nel browser interno del pannello con il JSON reale.
- La regola TM90 non e' validata su eventi italiani: e' una soglia citata, non tarata qui.
