#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Indice "timeline": unisce in UNA sequenza cronologica i run WebP conservati.

Sorgente (sola lettura): un albero ``fm2-rendered``, cioe' un checkout del branch
dati ``fm2-rendered`` o la stessa cartella prodotta dal renderer::

    <root>/<regione>/<dominio>/runs/<run_id>/<variabile>/latest.json
    <root>/<regione>/<dominio>/runs/<run_id>/<variabile>/<VALID>.webp

Uscita: ``<out-root>/<regione>/timeline.json`` per OGNI regione trovata.

PERCHE' LA TIMELINE NON E' UN ELENCO PIATTO PER REGIONE
------------------------------------------------------
Lo schema "un ``runs`` + un ``timeline`` in cima al file" non regge sui dati
reali, per due fatti MISURATI il 2026-10-24 sul branch ``fm2-rendered``:

1. i run appartengono al DOMINIO, non alla regione: la regione ``italia``
   contiene 7 domini con ``run_id`` diversi (``dwd_icon_d2`` 20261023T210000Z,
   ``ncep_gfs013`` 20261023T000000Z, ``ecmwf_ifs025`` 20261023T120000Z, ...).
   Un unico elenco piatto mescolerebbe nella stessa sequenza frame di modelli
   diversi, e ``bucket`` (eta' della run) non avrebbe piu' un significato: la
   "run precedente" di quale modello?
2. dentro la STESSA run le variabili NON hanno lo stesso insieme di
   ``valid_time``. Misurato: ``ecmwf_ifs025/20261023T120000Z`` ha 3 insiemi
   distinti (66 / 84 / 85 frame), ``dwd_icon/20261023T180000Z`` ne ha 2
   (92 / 93). Una timeline per dominio "sporca" le variabili corte con
   ``valid_time`` che per loro non esistono: il client chiederebbe frame
   inesistenti (404), cioe' esattamente il "file inventato" da evitare.

La timeline e' quindi per ``(regione, dominio, variabile)``. Per non pagare 27
volte la stessa sequenza (le variabili di una run quasi sempre coincidono), le
variabili con timeline IDENTICA (stessi ``valid_time``, stessi ``run_id``,
stessi nomi file) vengono raggruppate: un solo array ``timeline`` per gruppo,
referenziato da ``domains.<dominio>.variables.<variabile>.group``. Misurato
sulla regione ``italia``: 2-3 gruppi per dominio invece di 12-27.

SCHEMA (``fm2-timeline-1``)
---------------------------
::

    {
      "version": "fm2-timeline-1",
      "generated_at": "2026-10-24T09:00:00Z",
      "region": "italia",
      "file_base": "data/fm2-rendered/italia/",
      "file_template": "<dominio>/runs/<run_id>/<variabile>/<file>",
      "merge_rule": "...",
      "runs": [                       # piatto, per comodita' (tutti i domini)
        {"domain": "dwd_icon", "run_id": "20261023T180000Z",
         "reference_time": "2026-10-23T18:00:00Z",
         "reference_time_source": "reference_time", "bucket": 0,
         "completed": true, "expected_frames": 93, "frames": 93,
         "variables": 27, "first_valid_time": "2026-10-23T18:00Z",
         "last_valid_time": "2026-10-28T18:00Z", "timeline_frames": 185}
      ],
      "domains": {
        "dwd_icon": {
          "current_run": "20261023T180000Z",     # bucket 0
          "projection": "stereographic",         # SOLO se TUTTE le run concordano
          "projection_meta": {"type": "polar_stereographic", "size": 1000,
                              "edge_lat": 20.0, ...},   # geometria, se c'e'
          "runs": [{"run_id": "20261023T180000Z", ...,
                    "projection": "stereographic",   # proiezione DELLA run
                    "projection_meta": {...}}],
          "head_trim": {"applied": true, "cutoff": "2026-10-23T15:00Z",
                        "dropped_frames": 12, "reason": "keep_past_hours"},
          "variables": {
            "cape": {"group": "g0", "frames": 123,
                     "by_run": {"20261023T180000Z": 93, "20261023T120000Z": 24,
                                "20261023T060000Z": 6}}
          },
          "timeline_groups": {
            "g0": {
              "variables": ["cape", "cloud_cover", ...],
              "frames": 123,
              "timeline": [
                {"valid_time": "2026-10-23T06:00Z", "run_id": "20261023T060000Z",
                 "bucket": 2, "file": "2026-10-23T0600.webp",
                 "projection": "mercator"}     # proiezione DEL FRAME
              ]
            }
          }
        }
      },
      "projection": "stereographic",   # solo se TUTTI i domini concordano
      "summary": {"domains": 7, "runs": 20, "variables": 153, "groups": 14,
                  "timeline_frames": 1649, "variable_frames": 17372,
                  "files_checked": 17372, "files_missing": 0,
                  "files_verified": true, "head_trim_dropped": 37}
    }

Note di schema:

* ``bucket`` e' l'eta' della run **dentro il suo dominio** (0 = la piu' recente,
  1 = la precedente, 2 = quella prima). E' l'indice in
  ``domains.<dominio>.runs``.
* ``file`` e' il NOME del file, non il path: il path completo e'
  ``file_base + file_template`` con ``<dominio>``, ``<run_id>``, ``<variabile>``
  e ``<file>``. Il nome viene SEMPRE dal ``frames[].file`` del manifest, mai
  ricostruito dal ``valid_time`` (la regola ``2026-10-23T00:00Z`` ->
  ``2026-10-23T0000.webp`` esiste nel renderer, ma qui non si assume).
* ``gaps`` NON viene emesso. Motivo MISURATO il 2026-10-24 sulla regione
  ``italia``: la cadenza dei modelli NON e' costante (1 h nelle prime ore, 3 h
  o 6 h dopo), quindi un rilevatore basato sul passo piu' frequente dichiarava
  centinaia di buchi FALSE (352 su ``ncep_gfs013``, 128 su ``dwd_icon``, 0 su
  ``dwd_icon_d2``) — cioe' avrebbe dichiarato mancanti frame che il modello
  semplicemente non produce. La carenza VERA e' quella dichiarata dal singolo
  run (``expected_frames`` > ``frames`` -> ``completed: false``) e resta nei
  ``runs[*]``; un ``valid_time`` assente da tutte le run non viene inventato e
  non viene nemmeno elencato, perche' non c'e' modo di distinguerlo da una
  cadenza piu' larga con i soli manifest.
* ``dropped`` (solo se ce ne sono) elenca i frame che un manifest dichiara ma
  che NON esistono: non vengono citati, e per quel ``valid_time`` si scende
  alla run successiva che ce l'ha. ``skipped`` elenca le run scartate perche'
  senza alcun manifest valido.
* ``timeline_frames`` = somma dei frame dei gruppi (quante voci contiene il
  JSON); ``variable_frames`` = somma sui singoli ``(dominio, variabile)``
  (quante voci vedrebbe un client che itera una variabile alla volta). Il
  secondo e' il costo evitato dal raggruppamento.

PROIEZIONE (``projection`` / ``projection_meta``)
------------------------------------------------
Il client (``fmRegionProjection``) risolve la proiezione con la precedenza
frame -> run -> dominio -> regione -> ``"mercator"``. Senza queste chiavi, se
``index.json`` non e' ancora arrivato non c'e' alcun fallback e un disco polare
verrebbe disegnato come striscia Mercator per quella finestra. La timeline
quindi PROPAGA quello che i manifest dichiarano, senza indovinare:

* ``timeline_groups.<g>.timeline[].projection``: SEMPRE, su ogni frame, come
  stringa corta (``mercator`` / ``stereographic``). E' la proiezione VERA di
  quel frame, cioe' quella della run che lo ha prodotto: una run senza chiave
  nei manifest e' una striscia Mercator e il frame lo dichiara. Senza questa
  chiave il client applicherebbe la proiezione del dominio (della run piu'
  recente) anche ai frame delle run precedenti -- misurato in produzione il
  2026-10-26 su ``ecmwf_ifs025``/``north``: 36 frame su 85 della run
  ``20261025T120000Z`` (striscia 2879x521) disegnati come disco perche' la run
  ``20261025T180000Z`` era su disco 1000x1000.
* ``domains.<dominio>.runs[].projection`` / ``projection_meta``: la proiezione
  DELLA run, quando la dichiara. E' il livello che il client legge quando un
  frame non ha la chiave (timeline scritta da una versione precedente).
* ``domains.<dominio>.projection``: SOLO se TUTTE le run indicizzate dichiarano
  la STESSA proiezione; altrimenti la chiave e' OMESSA. Il client la usa come
  fallback per ogni frame senza proiezione propria, quindi una run ancora
  Mercator mentre un'altra e' su disco rende la chiave ambigua. SEMPRE una
  STRINGA: la geometria sta sotto ``domains.<dominio>.projection_meta``.
* ``projection`` a livello regione: SOLO se TUTTI i domini indicizzati
  dichiarano la STESSA proiezione; altrimenti la chiave e' OMESSA e non c'e'
  fallback. Regola identica a ``write_region_index`` del renderer (fix
  ``09f8158b``), con la stessa ragione MISURATA: il client applica la chiave di
  regione a OGNI dominio, e con ``dwd_icon`` ancora su una sorgente 2879x521
  disegnava la striscia come un disco. Uno stato misto = nessuna chiave.
* Un ``projection`` che fosse un OGGETTO (forma dei primi build del disco) viene
  spezzato in stringa + ``projection_meta``: il client accetta solo stringhe.
* Nessuna chiave dichiarata dai manifest -> nessuna chiave a livello run/dominio
  (``projection`` assente = ``mercator``, il percorso storico), ma i frame
  dichiarano comunque ``"mercator"``: e' il loro valore effettivo.

POTATURA DELLA TESTA (``--keep-past-hours``, default 3)
-------------------------------------------------------
La regola di merge "per ogni ``valid_time`` vince la run piu' recente che ce
l'ha" NON PUO' rimpiazzare i ``valid_time`` anteriori all'inizio della run piu'
nuova: una run copre solo da ``reference_time`` in avanti. Quindi la testa
della timeline appartiene per costruzione alla run piu' vecchia in retention e
resta ferma finche' quella run non viene potata. MISURATO il 2026-10-25 su
``fm2-rendered``: ``ncep_gfs025`` cominciava a ``2026-10-23T00:00Z``, cioe' 55
ore nel passato rispetto a ``generated_at``.

Per questo, per ogni ``(dominio, variabile)``, i frame con::

    valid_time < reference_time(run corrente) - keep_past_hours

vengono SCARTATI. La CODA (frame dopo la fine della run nuova, che vengono
dalle run precedenti) resta INTATTA: e' la parte utile. Un ``valid_time`` non
parsabile non viene potato (non c'e' prova che sia vecchio).

La run di riferimento e' ``current_run`` (la piu' recente del dominio); se il
suo ``reference_time`` non e' determinabile si usa la prima run della lista che
ne ha uno; se nessuna ne ha uno non si pota nulla (comportamento storico) e
``reason`` lo dichiara.

Ogni ``domains.<dominio>.head_trim`` dichiara l'esito::

    {"applied": true, "cutoff": "2026-10-23T21:00Z",
     "dropped_frames": 51, "reason": "keep_past_hours"}

``dropped_frames`` conta i ``valid_time`` DISTINTI rimossi nel dominio (non le
occorrenze per variabile: le variabili di un dominio condividono quasi sempre
la stessa testa). ``reason`` vale ``"keep_past_hours"`` quando il taglio e'
stato applicato (anche con 0 frame rimossi) e ``"no_current_run"`` quando non
c'era alcuna run con ``reference_time`` utilizzabile (``applied: false``,
``cutoff: null``).

Una variabile la cui timeline resta VUOTA dopo la potatura non entra in alcun
gruppo: la sua voce in ``variables`` porta ``"group": null``, ``"frames": 0``,
``"head_trimmed_frames": N`` e ``"fallback": "index.json"``. Il client
(``fmTimelineGroupId``) tratta un ``group`` assente/falsy come "nessuna
timeline" e ricade su ``index.json`` per quella variabile: nessuna modifica
all'HTML. Un gruppo senza frame non viene mai creato.

Il file e' ADDITIVO: ``index.json`` non viene letto ne' scritto, il renderer non
viene toccato, nessun workflow viene toccato.

CLI
---
::

    python scripts/forecast_v2/build_timeline_index.py --root data/fm2-rendered
    python scripts/forecast_v2/build_timeline_index.py --root data/fm2-rendered \\
        --regions italia --check-files
    python scripts/forecast_v2/build_timeline_index.py --root data/fm2-rendered \\
        --path-list paths.txt --now 2026-10-24T00:00:00Z --dry-run
    python scripts/forecast_v2/build_timeline_index.py --root data/fm2-rendered \\
        --keep-past-hours 0        # taglio esatto a reference_time

Exit code: 0 = almeno una regione scritta; 1 = nessuna regione valida;
2 = argomenti/root non validi; 3 = frame mancanti con ``--fail-on-missing``.

⚠️ Sul branch ``fm2-rendered`` i ``.webp`` non sono scaricabili a basso costo:
per verificare TUTTI i path senza scaricare i blob si usa
``git ls-tree -r --name-only <ref> data/fm2-rendered/<regione>/`` e si passa il
risultato (senza il prefisso ``data/fm2-rendered/<regione>/``) con
``--path-list``. ``git ls-tree -r -l`` NON va usato: chiede i blob per la
dimensione.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

TIMELINE_VERSION = "fm2-timeline-1"
DEFAULT_ROOT = os.path.join("data", "fm2-rendered")
FILE_TEMPLATE = "<dominio>/runs/<run_id>/<variabile>/<file>"
MERGE_RULE = ("per ogni valid_time vince la run con reference_time piu' recente "
              "che ha un frame RESO per quella variabile; a parita' di "
              "reference_time vince il run_id maggiore")
# Ore di passato ammesse nella testa della timeline (vedi docstring). 3 ore
# coprono l'anticipo con cui una run puo' essere resa rispetto al suo
# reference_time senza lasciare scoperto l'istante della run stessa.
DEFAULT_KEEP_PAST_HOURS = 3.0

# Valore STRINGA che il client (fmRegionProjection, 1.2.4.27+) riconosce per il
# disco polare stereografico. Qualunque altro valore, o l'assenza della chiave,
# vale 'mercator' (il percorso storico). Deve restare una STRINGA: i metadati
# geometrici vivono sotto `projection_meta` (vedi projection_parts).
POLAR_PROJECTION = "stereographic"
# Valore che il client usa quando una run NON dichiara `projection`: i manifest
# scritti prima del disco polare (e qualunque manifest senza la chiave) sono
# strisce Mercator. Lo si scrive ESPLICITAMENTE su ogni frame della timeline,
# perche' il frame deve dichiarare la propria proiezione e non ereditare quella
# del dominio (che puo' essere il disco della run piu' recente).
MERCATOR_PROJECTION = "mercator"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def utcnow_iso(now: str | None = None) -> str:
    """ISO-8601 UTC con la ``Z`` finale (stesso formato del renderer)."""
    if now:
        return str(now)
    return (_dt.datetime.now(_dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"))


def parse_dt(value) -> "_dt.datetime | None":
    """Tollerante: ``2026-10-23T00:00Z``, ``...T00:00:00+00:00``, ``20261023T000000Z``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = _dt.datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%MZ", "%Y-%m-%dT%H%M"):
            try:
                parsed = _dt.datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def format_dt(value: _dt.datetime) -> str:
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def read_json(path: str):
    """``None`` su file assente/illeggibile/JSON invalido (mai un'eccezione)."""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_json(path: str, obj: dict) -> bool:
    """Scrittura atomica compatta (stessa convenzione del renderer).

    Ritorna ``True`` se il file e' cambiato, ``False`` se era gia' identico
    (idempotenza: con ``--now`` fisso un secondo giro non tocca il file).
    """
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if handle.read() == payload:
                    return False
        except OSError:
            pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    os.replace(tmp, path)
    return True


def _valid_manifest(obj) -> bool:
    """Struttura minima: ``frames[]`` con ``file`` e ``valid_time`` non vuoti."""
    if not isinstance(obj, dict):
        return False
    frames = obj.get("frames")
    if not isinstance(frames, list) or not frames:
        return False
    for frame in frames:
        if not isinstance(frame, dict):
            return False
        if not isinstance(frame.get("file"), str) or not frame["file"].strip():
            return False
        if not isinstance(frame.get("valid_time"), str) or not frame["valid_time"].strip():
            return False
    return True


# --------------------------------------------------------------------------- #
# projection (propagata dal manifest del dominio, MAI indovinata)
# --------------------------------------------------------------------------- #
def projection_parts(value):
    """``(projection_string, projection_meta)`` da un valore di manifest.

    Forma pubblicata (>= 1.2.4.30): la STRINGA sotto ``projection`` e la
    geometria sotto ``projection_meta``. Un DICT sotto ``projection`` (forma
    dei primi build del disco) viene accettato e spezzato qui, cosi' la timeline
    non ripubblica un oggetto che il client ignora (``typeof !== 'string'``).

    Copia DELIBERATA di ``render_fm_webp.projection_parts``: questo modulo resta
    SOLO stdlib (nessun numpy/Pillow/omfiles) perche' gira anche dove il
    renderer non e' installato; importarlo tirerebbe dentro le dipendenze
    pesanti. Se cambia la forma del manifest, cambiano entrambi.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        return (text or None), None
    if isinstance(value, dict):
        kind = str(value.get("type") or "").strip().lower()
        name = POLAR_PROJECTION if kind == "polar_stereographic" else None
        return name, dict(value)
    return None, None


def _run_projection(manifests: list) -> tuple:
    """``(projection, projection_meta)`` dichiarate dai manifest di una run.

    Si prende il PRIMO manifest valido il cui ``projection`` normalizza a una
    stringa: tutte le variabili di una run scrivono la stessa proiezione (la
    regione e' la stessa), quindi non c'e' nulla da mediare. Nessuna chiave
    dichiarata -> ``(None, None)``: la timeline non inventa una proiezione.
    """
    for manifest in manifests:
        projection, legacy_meta = projection_parts(manifest.get("projection"))
        if not projection:
            continue
        meta = manifest.get("projection_meta") or legacy_meta
        return projection, (dict(meta) if isinstance(meta, dict) else None)
    return None, None


def consensus_projection(entries) -> tuple:
    """``(projection, projection_meta)`` se TUTTE le voci dichiarano lo stesso valore.

    E' la regola del fix ``09f8158b`` (la stessa di ``write_region_index`` nel
    renderer), applicata alle DUE liste che il client copre in blocco:

    * i DOMINI di una regione -- la chiave di regione viene usata dal client per
      ogni dominio;
    * le RUN di un dominio -- ``domains.<dominio>.projection`` viene usata per
      ogni frame che non ne dichiara una propria.

    Una voce che NON dichiara nulla non concorda: il valore assente significa
    ``mercator`` (default del client) e applicarlo alle altre sarebbe
    un'invenzione. Per questo un elenco tutto-Mercator non produce la chiave.
    Solo STRINGHE: un oggetto (forma dei primi build del disco) non e' un valore
    che il client accetta.

    Copia DELIBERATA di ``render_fm_webp.consensus_projection`` per lo stesso
    motivo di ``projection_parts``: questo modulo resta SOLO stdlib.
    """
    values = set()
    metas = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            return None, None
        value = entry.get("projection")
        if not isinstance(value, str) or not value:
            return None, None
        values.add(value)
        meta = entry.get("projection_meta")
        if value not in metas and isinstance(meta, dict):
            metas[value] = meta
    if len(values) != 1:
        return None, None
    value = next(iter(values))
    return value, metas.get(value)


def frame_projection(record: dict) -> str:
    """Proiezione del SINGOLO frame, sempre una stringa.

    La run che ha prodotto il frame la dichiara (``collect_run``); se non la
    dichiara il frame e' una striscia Mercator, cioe' il default del client. Il
    valore si scrive ESPLICITAMENTE su ogni voce della timeline: senza, il
    client applicherebbe la proiezione del dominio (presa dalla run piu'
    recente) anche ai frame della run precedente -- misurato in produzione il
    2026-10-26: 36 frame della run ``20261025T120000Z`` (striscia 2879x521)
    disegnati come disco perche' la run ``20261025T180000Z`` era su disco.
    """
    value = record.get("projection")
    if isinstance(value, str) and value:
        return value
    return MERCATOR_PROJECTION


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def discover_regions(root: str) -> list:
    """Sottocartelle di ``root`` che contengono almeno ``<dominio>/runs``."""
    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found
    for name in names:
        rdir = os.path.join(root, name)
        if not os.path.isdir(rdir):
            continue
        if discover_domains(rdir):
            found.append(name)
    return found


def discover_domains(region_dir: str) -> list:
    found = []
    try:
        names = sorted(os.listdir(region_dir))
    except OSError:
        return found
    for name in names:
        ddir = os.path.join(region_dir, name)
        if os.path.isdir(ddir) and os.path.isdir(os.path.join(ddir, "runs")):
            found.append(name)
    return found


def _run_reference(run_id: str, manifests: list) -> tuple:
    """``(reference_time, source)`` — manifest, poi generated_at, poi run_id.

    La sorgente e' dichiarata nel JSON: un ``reference_time`` preso dal nome
    della cartella non e' la stessa cosa di uno preso dal manifest e chi legge
    deve poterlo distinguere.
    """
    for key in ("reference_time", "generated_at"):
        for manifest in manifests:
            value = manifest.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), key
    parsed = parse_dt(run_id)
    if parsed is not None:
        return format_dt(parsed), "run_id"
    return None, "missing"


def collect_run(run_dir: str) -> dict | None:
    """Legge ``<run>/<variabile>/latest.json`` e ne ricava frame e metadati.

    Ritorna ``None`` se la run non ha NESSUNA variabile con un manifest
    strutturalmente valido (una run senza frame non entra nella timeline).
    """
    variables = {}
    manifests = []
    expected = 0
    max_frames = 0
    first = None
    last = None
    try:
        names = sorted(os.listdir(run_dir))
    except OSError:
        return None
    for var in names:
        var_dir = os.path.join(run_dir, var)
        if not os.path.isdir(var_dir):
            continue
        manifest = read_json(os.path.join(var_dir, "latest.json"))
        if not _valid_manifest(manifest):
            continue
        manifests.append(manifest)
        # valid_time -> nome file; un duplicato non sovrascrive il primo (il
        # manifest e' gia' ordinato e l'ultimo file non e' "piu' giusto").
        frames = {}
        for frame in manifest["frames"]:
            valid_time = frame["valid_time"].strip()
            if valid_time in frames:
                continue
            frames[valid_time] = frame["file"].strip()
            parsed = parse_dt(valid_time)
            key = parsed if parsed is not None else valid_time
            if first is None or key < first[0]:
                first = (key, valid_time)
            if last is None or key > last[0]:
                last = (key, valid_time)
        if not frames:
            continue
        variables[var] = frames
        declared = manifest.get("expected_frames")
        if isinstance(declared, int) and declared > 0:
            expected = max(expected, declared)
        max_frames = max(max_frames, len(frames))
    if not variables:
        return None
    run_id = os.path.basename(os.path.normpath(run_dir))
    reference_time, source = _run_reference(run_id, manifests)
    projection, projection_meta = _run_projection(manifests)
    return {
        "run_id": run_id,
        "reference_time": reference_time,
        "reference_time_source": source,
        "expected_frames": expected or max_frames,
        "frames": max_frames,
        "completed": max_frames >= (expected or max_frames),
        "variables": len(variables),
        "first_valid_time": first[1] if first else None,
        "last_valid_time": last[1] if last else None,
        "frames_by_var": variables,
        "projection": projection,
        "projection_meta": projection_meta,
    }


def _run_sort_key(record: dict) -> tuple:
    """Recenza: ``reference_time`` parsato, poi ``run_id`` come discriminante.

    Un ``reference_time`` non parsabile non scavalca quelli parsabili: finisce
    in coda (primo elemento della tupla ``False``), poi decide il ``run_id``.
    """
    parsed = parse_dt(record.get("reference_time"))
    if parsed is not None:
        return (1, parsed, record["run_id"])
    return (0, _dt.datetime.min.replace(tzinfo=_dt.timezone.utc), record["run_id"])


def head_trim_cutoff(runs: list, keep_past_hours) -> tuple:
    """``(cutoff, reason, run_id)`` per la potatura della testa.

    ``runs`` e' la lista GIA' ordinata dalla piu' recente (``merge_domain`` la
    ordina prima di chiamare qui). La run di riferimento e' la piu' recente che
    ha un ``reference_time`` parsabile: normalmente ``runs[0]``, cioe'
    ``current_run``. Se NESSUNA run lo ha, il taglio non e' determinabile:
    ``cutoff`` e ``run_id`` sono ``None`` e ``reason`` vale ``"no_current_run"``
    (comportamento storico: nessuna potatura), mai un taglio indovinato.
    """
    try:
        hours = float(keep_past_hours)
    except (TypeError, ValueError):
        hours = 0.0
    if hours < 0:
        hours = 0.0
    for record in runs:
        parsed = parse_dt(record.get("reference_time"))
        if parsed is None:
            continue
        return (parsed - _dt.timedelta(hours=hours), "keep_past_hours",
                record["run_id"])
    return None, "no_current_run", None


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #
def _valid_time_sort_key(valid_time: str) -> tuple:
    parsed = parse_dt(valid_time)
    if parsed is not None:
        return (0, parsed, valid_time)
    return (1, _dt.datetime.min.replace(tzinfo=_dt.timezone.utc), valid_time)


def merge_domain(domain: str, runs: list, max_runs: int, exists=None,
                 dropped=None, stats=None,
                 keep_past_hours=DEFAULT_KEEP_PAST_HOURS) -> dict | None:
    """Costruisce ``domains.<dominio>``: run ordinate, gruppi, timeline.

    ``exists(domain, run_id, var, file)`` (opzionale) e' il predicato di
    esistenza del frame: un frame dichiarato dal manifest ma assente NON viene
    citato, e per quel ``valid_time`` si scende alla run successiva (se ce
    l'ha). Ogni scarto finisce in ``dropped`` con il motivo: nessun buco
    silenzioso.

    ``keep_past_hours`` pota la TESTA della timeline: per ogni variabile i
    frame con ``valid_time < reference_time(run corrente) - keep_past_hours``
    vengono rimossi e contati in ``head_trim``. La coda (frame dopo la fine
    della run nuova, che arrivano dalle run precedenti) non viene toccata.
    """
    runs.sort(key=_run_sort_key, reverse=True)
    if max_runs and max_runs > 0:
        runs = runs[:max_runs]
    if not runs:
        return None

    run_records = []
    for bucket, record in enumerate(runs):
        record["bucket"] = bucket
        run_rec = {
            "run_id": record["run_id"],
            "reference_time": record["reference_time"],
            "reference_time_source": record["reference_time_source"],
            "bucket": bucket,
            "completed": record["completed"],
            "expected_frames": record["expected_frames"],
            "frames": record["frames"],
            "variables": record["variables"],
            "first_valid_time": record["first_valid_time"],
            "last_valid_time": record["last_valid_time"],
            "timeline_frames": 0,  # riempito dopo il merge
        }
        # Proiezione della RUN (stringa + geometria): la stessa forma di
        # `index.json` -> `domains.<dominio>.runs[]`. Non e' un doppione del
        # frame: e' il livello che il client legge quando un frame non la
        # dichiara (timeline.json generata da una versione precedente). Una run
        # senza chiave resta Mercator e NON si scrive nulla.
        if record.get("projection"):
            run_rec["projection"] = record["projection"]
        if record.get("projection_meta"):
            run_rec["projection_meta"] = record["projection_meta"]
        run_records.append(run_rec)

    # Variabili: unione di tutti i run (ordine alfabetico = deterministico).
    all_vars = sorted({var for record in runs for var in record["frames_by_var"]})
    cutoff, trim_reason, _trim_run = head_trim_cutoff(runs, keep_past_hours)
    trimmed_times = set()
    emptied = {}
    per_var_timeline = {}
    per_var_by_run = {}
    for var in all_vars:
        winners = {}
        # Iterazione dalla run PIU' RECENTE: il primo che vede un valid_time vince.
        for record in runs:
            frames = record["frames_by_var"].get(var) or {}
            for valid_time, file_name in frames.items():
                if valid_time in winners:
                    continue
                if exists is not None:
                    if stats is not None:
                        stats["checked"] = stats.get("checked", 0) + 1
                    if not exists(domain, record["run_id"], var, file_name):
                        if dropped is not None:
                            dropped.append({
                                "domain": domain,
                                "variable": var,
                                "run_id": record["run_id"],
                                "valid_time": valid_time,
                                "file": file_name,
                                "reason": "file_non_esistente",
                            })
                        continue
                winners[valid_time] = {
                    "valid_time": valid_time,
                    "run_id": record["run_id"],
                    "bucket": record["bucket"],
                    "file": file_name,
                    # Il frame dichiara la PROPRIA proiezione (quella della run
                    # che lo ha prodotto), sempre come stringa: 'mercator' se la
                    # run non ne dichiara una. Senza questa chiave il client
                    # userebbe quella del dominio, cioe' della run piu' recente.
                    "projection": frame_projection(record),
                }
        if not winners:
            continue
        timeline = [winners[vt] for vt in sorted(winners, key=_valid_time_sort_key)]
        if cutoff is not None:
            # Potatura della TESTA. Un valid_time non parsabile resta: non c'e'
            # prova che sia anteriore al cutoff (e l'ordinamento lo mette in
            # coda, non in testa).
            kept = []
            for entry in timeline:
                parsed = parse_dt(entry["valid_time"])
                if parsed is not None and parsed < cutoff:
                    trimmed_times.add(entry["valid_time"])
                    continue
                kept.append(entry)
            if not kept:
                # La variabile esiste ma dopo il taglio non ha piu' frame: non
                # entra in alcun gruppo e la sua voce lo dichiara (il client
                # ricade su index.json).
                emptied[var] = len(timeline)
                continue
            timeline = kept
        per_var_timeline[var] = timeline
        counts = {}
        for entry in timeline:
            counts[entry["run_id"]] = counts.get(entry["run_id"], 0) + 1
        per_var_by_run[var] = counts

    if not per_var_timeline and not emptied:
        return None

    # Raggruppa le variabili con timeline IDENTICA (stessi valid_time, run_id e
    # file). La firma include anche il nome file: due variabili finiscono nello
    # stesso gruppo solo se il gruppo descrive file realmente esistenti in
    # entrambe le cartelle.
    groups = {}
    order = []
    var_group = {}
    for var in sorted(per_var_timeline):
        timeline = per_var_timeline[var]
        signature = tuple((e["valid_time"], e["run_id"], e["file"]) for e in timeline)
        if signature not in groups:
            groups[signature] = {"variables": [], "timeline": timeline}
            order.append(signature)
        groups[signature]["variables"].append(var)
        var_group[var] = signature

    group_id = {}
    timeline_groups = {}
    timeline_frames_by_run = {}
    for index, signature in enumerate(order):
        gid = "g%d" % index
        group_id[signature] = gid
        timeline = groups[signature]["timeline"]
        payload = {
            "variables": groups[signature]["variables"],
            "frames": len(timeline),
            "timeline": timeline,
        }
        timeline_groups[gid] = payload
        for entry in timeline:
            timeline_frames_by_run[entry["run_id"]] = (
                timeline_frames_by_run.get(entry["run_id"], 0) + 1)

    variables = {}
    for var in sorted(set(per_var_timeline) | set(emptied)):
        if var in emptied:
            # Gruppo svuotato dalla potatura: il gruppo non esiste, la variabile
            # resta citata e il client ricade su index.json (group falsy).
            variables[var] = {
                "group": None,
                "frames": 0,
                "by_run": {},
                "head_trimmed_frames": emptied[var],
                "fallback": "index.json",
            }
            continue
        variables[var] = {
            "group": group_id[var_group[var]],
            "frames": len(per_var_timeline[var]),
            "by_run": per_var_by_run[var],
        }

    for record in run_records:
        record["timeline_frames"] = timeline_frames_by_run.get(record["run_id"], 0)

    domain_doc = {
        "current_run": run_records[0]["run_id"] if run_records else None,
        "runs": run_records,
        "head_trim": {
            "applied": cutoff is not None,
            "cutoff": format_dt(cutoff) if cutoff is not None else None,
            "dropped_frames": len(trimmed_times),
            "reason": trim_reason,
        },
        "variables": variables,
        "timeline_groups": timeline_groups,
    }
    # `projection` del DOMINIO: SOLO se TUTTE le run indicizzate dichiarano la
    # STESSA proiezione (regola del fix `09f8158b`, condivisa con
    # `write_region_index` del renderer). Il client la usa come fallback per i
    # frame che non dichiarano una proiezione propria, quindi applicarla mentre
    # una run e' ancora una striscia Mercator disegnerebbe quella striscia come
    # un disco. MISURATO in produzione il 2026-10-26 su `ecmwf_ifs025`/`north`:
    # run `20261025T180000Z` su disco 1000x1000 e run `20261025T120000Z` ancora
    # striscia 2879x521 -> la chiave va OMESSA (le run discordano), mentre ogni
    # frame porta la sua e ogni run la sua in `runs[]`. Resta una STRINGA; la
    # geometria va sotto `projection_meta`.
    domain_projection, domain_projection_meta = consensus_projection(run_records)
    if domain_projection:
        domain_doc["projection"] = domain_projection
    if domain_projection_meta:
        domain_doc["projection_meta"] = domain_projection_meta
    return domain_doc


# --------------------------------------------------------------------------- #
# region
# --------------------------------------------------------------------------- #
def build_region(root: str, region: str, max_runs: int = 0, exists=None,
                 keep_past_hours=DEFAULT_KEEP_PAST_HOURS) -> dict | None:
    """Costruisce il documento ``timeline.json`` di una regione."""
    region_dir = os.path.join(root, region)
    domains = discover_domains(region_dir)
    if not domains:
        return None

    domain_docs = {}
    flat_runs = []
    skipped = []
    dropped = []
    stats = {"checked": 0}
    for domain in domains:
        runs_root = os.path.join(region_dir, domain, "runs")
        runs = []
        try:
            run_ids = sorted(os.listdir(runs_root))
        except OSError:
            continue
        for run_id in run_ids:
            run_dir = os.path.join(runs_root, run_id)
            if not os.path.isdir(run_dir):
                continue
            record = collect_run(run_dir)
            if record is None:
                skipped.append("%s/%s: nessun manifest valido" % (domain, run_id))
                continue
            runs.append(record)
        doc = merge_domain(domain, runs, max_runs, exists=exists,
                           dropped=dropped, stats=stats,
                           keep_past_hours=keep_past_hours)
        if doc is None:
            skipped.append("%s/%s" % (domain, "nessuna run valida"))
            continue
        domain_docs[domain] = doc
        for record in doc["runs"]:
            flat_runs.append(dict(record, domain=domain))

    if not domain_docs:
        return None

    flat_runs.sort(key=lambda r: (r["domain"], r["bucket"]))
    summary = {
        "domains": len(domain_docs),
        "runs": sum(len(doc["runs"]) for doc in domain_docs.values()),
        "variables": sum(len(doc["variables"]) for doc in domain_docs.values()),
        "groups": sum(len(doc["timeline_groups"]) for doc in domain_docs.values()),
        "timeline_frames": sum(group["frames"]
                               for doc in domain_docs.values()
                               for group in doc["timeline_groups"].values()),
        "variable_frames": sum(entry["frames"]
                               for doc in domain_docs.values()
                               for entry in doc["variables"].values()),
        "files_checked": stats["checked"],
        "files_missing": len(dropped),
        "files_verified": exists is not None,
        "head_trim_dropped": sum(doc["head_trim"]["dropped_frames"]
                                 for doc in domain_docs.values()),
    }
    document = {
        "version": TIMELINE_VERSION,
        "generated_at": None,  # riempito dal chiamante
        "region": region,
        "file_base": "%s/%s/" % (DEFAULT_ROOT.replace(os.sep, "/"), region),
        "file_template": FILE_TEMPLATE,
        "merge_rule": MERGE_RULE,
        "runs": flat_runs,
        "domains": domain_docs,
        "summary": summary,
    }
    # `projection` a livello REGIONE = fallback del client (fmRegionProjection)
    # quando `index.json` non e' ancora arrivato. Vale pero' per TUTTI i domini
    # della regione: pubblicarla mentre un dominio e' ancora una striscia
    # Mercator fa disegnare dal client la striscia 2879x521 come se fosse un
    # disco. MISURATO in produzione il 2026-10-26 (dwd_icon con
    # projection 'stereographic' e sorgente 2879x521). Regola IDENTICA a
    # `write_region_index` del renderer (fix 09f8158b): la chiave si scrive
    # SOLO se TUTTI i domini indicizzati dichiarano la STESSA proiezione;
    # altrimenti si omette (stato misto = nessun fallback). La stessa funzione
    # decide la chiave del DOMINIO sulle sue RUN.
    region_projection, region_projection_meta = consensus_projection(
        list(domain_docs.values()))
    if region_projection:
        document["projection"] = region_projection
        if region_projection_meta:
            document["projection_meta"] = region_projection_meta
    if skipped:
        document["skipped"] = skipped
    if dropped:
        document["dropped"] = dropped
    return document


def make_exists(root: str, region: str, path_list=None):
    """Predicato ``(domain, run_id, var, file) -> bool`` per i frame citati.

    Con ``path_list`` (set di path relativi a ``<root>/<regione>/``) si verifica
    contro un inventario — comodo quando i ``.webp`` non sono stati scaricati
    (``git ls-tree -r --name-only``) — altrimenti si interroga il disco.
    """
    region_dir = os.path.join(root, region)

    def exists(domain, run_id, var, file_name):
        rel = "%s/runs/%s/%s/%s" % (domain, run_id, var, file_name)
        if path_list is not None:
            return rel in path_list
        return os.path.isfile(os.path.join(region_dir, rel.replace("/", os.sep)))

    return exists


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Costruisce <regione>/timeline.json unendo i run WebP "
                    "conservati in un'unica sequenza cronologica.")
    parser.add_argument("--root", dest="root", default=DEFAULT_ROOT,
                        help="radice con <regione>/<dominio>/runs/... "
                             "(default: %s)" % DEFAULT_ROOT)
    parser.add_argument("--out-root", dest="out_root", default=None,
                        help="dove scrivere <regione>/timeline.json "
                             "(default: --root)")
    parser.add_argument("--regions", default=None,
                        help="lista separata da virgole (default: tutte le "
                             "regioni trovate sotto --root)")
    parser.add_argument("--max-runs", type=int, default=0,
                        help="tieni solo le N run piu' recenti per dominio "
                             "(default 0 = tutte quelle presenti)")
    parser.add_argument("--keep-past-hours", type=float,
                        default=DEFAULT_KEEP_PAST_HOURS,
                        help="pota la testa della timeline: per ogni variabile "
                             "si scartano i frame con valid_time < "
                             "reference_time(run corrente) - N ore. La coda "
                             "(frame dopo la fine della run nuova) resta. "
                             "Default %s; 0 = taglio esatto a reference_time"
                             % DEFAULT_KEEP_PAST_HOURS)
    parser.add_argument("--check-files", action="store_true",
                        help="verifica su disco ogni frame citato: quelli assenti "
                             "vengono scartati e dichiarati in 'dropped'")
    parser.add_argument("--path-list", default=None,
                        help="file con un path per riga (relativo a "
                             "<root>/<regione>/) usato al posto di --check-files "
                             "quando i .webp non sono stati scaricati")
    parser.add_argument("--fail-on-missing", action="store_true",
                        help="con --check-files/--path-list: exit 3 e NESSUN "
                             "file scritto se un frame citato non esiste")
    parser.add_argument("--now", default=None,
                        help="generated_at fisso (ISO-8601), per output "
                             "riproducibile")
    parser.add_argument("--dry-run", action="store_true",
                        help="calcola e stampa il riepilogo senza scrivere")
    return parser.parse_args(argv)


def _split_csv(value):
    if not value:
        return None
    items = [item.strip() for item in str(value).split(",") if item.strip()]
    return items or None


def main(argv=None) -> int:
    args = parse_args(argv)
    root = os.path.abspath(args.root)
    out_root = os.path.abspath(args.out_root) if args.out_root else root
    if not os.path.isdir(root):
        print("FATAL: --root non e' una directory: %s" % root, file=sys.stderr)
        return 2
    if args.keep_past_hours is None or args.keep_past_hours < 0:
        print("FATAL: --keep-past-hours deve essere >= 0 (ricevuto %r)"
              % (args.keep_past_hours,), file=sys.stderr)
        return 2

    requested = _split_csv(args.regions)
    if requested:
        regions = requested
        for region in regions:
            if not os.path.isdir(os.path.join(root, region)):
                print("FATAL: regione %r assente sotto %s" % (region, root),
                      file=sys.stderr)
                return 2
    else:
        regions = discover_regions(root)
    if not regions:
        print("[timeline] nessuna regione con <dominio>/runs/ sotto %s" % root,
              file=sys.stderr)
        return 1

    path_list = None
    if args.path_list:
        try:
            # utf-8-sig: l'inventario puo' arrivare da PowerShell/Set-Content,
            # che scrive il BOM. Senza questo il PRIMO path non combacia mai.
            with open(args.path_list, "r", encoding="utf-8-sig") as handle:
                path_list = {line.strip().replace("\\", "/").lstrip("\ufeff")
                             for line in handle if line.strip()}
        except OSError as exc:
            print("FATAL: --path-list illeggibile (%s)" % exc, file=sys.stderr)
            return 2

    generated_at = utcnow_iso(args.now)
    written = 0
    missing_total = 0
    for region in regions:
        exists = None
        if args.check_files or path_list is not None:
            exists = make_exists(root, region, path_list)
        document = build_region(root, region, max_runs=args.max_runs, exists=exists,
                                keep_past_hours=args.keep_past_hours)
        if document is None:
            print("[timeline] %s: nessun dominio con run valide -> nessun file"
                  % region, file=sys.stderr)
            continue
        document["generated_at"] = generated_at

        summary = document["summary"]
        if summary["files_missing"]:
            missing_total += summary["files_missing"]
            print("[timeline] %s: %d frame dichiarati dai manifest NON esistono "
                  "(scartati, il valid_time passa alla run precedente se ce l'ha)"
                  % (region, summary["files_missing"]), file=sys.stderr)
            for item in (document.get("dropped") or [])[:20]:
                print("  ! %s/runs/%s/%s/%s"
                      % (item["domain"], item["run_id"], item["variable"],
                         item["file"]), file=sys.stderr)
            if args.fail_on_missing:
                print("[timeline] %s: --fail-on-missing -> nessun file scritto"
                      % region, file=sys.stderr)
                continue

        target_dir = os.path.join(out_root, region)
        target = os.path.join(target_dir, "timeline.json")
        changed = False
        if not args.dry_run:
            os.makedirs(target_dir, exist_ok=True)
            changed = write_json(target, document)
        print("[timeline] %s: %d domini, %d run, %d variabili, %d gruppi, "
              "%d frame (%d nelle variabili), %d testa potata, "
              "%d file verificati/%d mancanti, %s %s"
              % (region, summary["domains"], summary["runs"],
                 summary["variables"], summary["groups"],
                 summary["timeline_frames"], summary["variable_frames"],
                 summary["head_trim_dropped"],
                 summary["files_checked"], summary["files_missing"],
                 "dry-run" if args.dry_run else
                 ("scritto" if changed else "invariato"), target))
        for name, doc in sorted(document["domains"].items()):
            trim = doc["head_trim"]
            if not trim["dropped_frames"]:
                continue
            print("[timeline] %s/%s: testa potata, %d frame con valid_time < %s "
                  "(run %s)"
                  % (region, name, trim["dropped_frames"], trim["cutoff"],
                     doc["current_run"]))
        written += 1

    if missing_total and args.fail_on_missing:
        return 3
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
