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
         "reference_time_source": "manifest", "bucket": 0, "completed": true,
         "expected_frames": 93, "frames": 93, "variables": 27,
         "first_valid_time": "...", "last_valid_time": "...",
         "timeline_frames": 96}
      ],
      "domains": {
        "dwd_icon": {
          "current_run": "20261023T180000Z",     # bucket 0
          "runs": [ ... stesso record senza "domain" ... ],
          "variables": {
            "cape": {"group": "g0", "frames": 105,
                     "by_run": {"20261023T180000Z": 93, "20261023T120000Z": 12}}
          },
          "timeline_groups": {
            "g0": {
              "variables": ["cape", "cloud_cover", ...],
              "frames": 105,
              "timeline": [
                {"valid_time": "2026-10-23T06:00Z", "run_id": "20261023T060000Z",
                 "bucket": 0, "file": "2026-10-23T0600.webp"}
              ]
            }
          }
        }
      },
      "summary": {"domains": 7, "runs": 20, "variables": 152, "groups": 14,
                  "timeline_frames": 892, "variable_frames": 17120,
                  "files_checked": 892, "files_missing": 0}
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


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #
def _valid_time_sort_key(valid_time: str) -> tuple:
    parsed = parse_dt(valid_time)
    if parsed is not None:
        return (0, parsed, valid_time)
    return (1, _dt.datetime.min.replace(tzinfo=_dt.timezone.utc), valid_time)


def merge_domain(domain: str, runs: list, max_runs: int, exists=None,
                 dropped=None, stats=None) -> dict | None:
    """Costruisce ``domains.<dominio>``: run ordinate, gruppi, timeline.

    ``exists(run_id, var, file)`` (opzionale) e' il predicato di esistenza del
    frame: un frame dichiarato dal manifest ma assente NON viene citato, e per
    quel ``valid_time`` si scende alla run successiva (se ce l'ha). Ogni scarto
    finisce in ``dropped`` con il motivo: nessun buco silenzioso.
    """
    runs.sort(key=_run_sort_key, reverse=True)
    if max_runs and max_runs > 0:
        runs = runs[:max_runs]
    if not runs:
        return None

    run_records = []
    for bucket, record in enumerate(runs):
        record["bucket"] = bucket
        run_records.append({
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
        })

    # Variabili: unione di tutti i run (ordine alfabetico = deterministico).
    all_vars = sorted({var for record in runs for var in record["frames_by_var"]})
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
                }
        if not winners:
            continue
        timeline = [winners[vt] for vt in sorted(winners, key=_valid_time_sort_key)]
        per_var_timeline[var] = timeline
        counts = {}
        for entry in timeline:
            counts[entry["run_id"]] = counts.get(entry["run_id"], 0) + 1
        per_var_by_run[var] = counts

    if not per_var_timeline:
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
    for var in sorted(per_var_timeline):
        variables[var] = {
            "group": group_id[var_group[var]],
            "frames": len(per_var_timeline[var]),
            "by_run": per_var_by_run[var],
        }

    for record in run_records:
        record["timeline_frames"] = timeline_frames_by_run.get(record["run_id"], 0)

    return {
        "current_run": run_records[0]["run_id"] if run_records else None,
        "runs": run_records,
        "variables": variables,
        "timeline_groups": timeline_groups,
    }


# --------------------------------------------------------------------------- #
# region
# --------------------------------------------------------------------------- #
def build_region(root: str, region: str, max_runs: int = 0, exists=None) -> dict | None:
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
                skipped.append("%s/%s/%s" % (domain, run_id, "nessun manifest valido"))
                continue
            runs.append(record)
        doc = merge_domain(domain, runs, max_runs, exists=exists,
                           dropped=dropped, stats=stats)
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
        document = build_region(root, region, max_runs=args.max_runs, exists=exists)
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
              "%d frame (%d nelle variabili), %d file verificati/%d mancanti, %s %s"
              % (region, summary["domains"], summary["runs"],
                 summary["variables"], summary["groups"],
                 summary["timeline_frames"], summary["variable_frames"],
                 summary["files_checked"], summary["files_missing"],
                 "dry-run" if args.dry_run else
                 ("scritto" if changed else "invariato"), target))
        written += 1

    if missing_total and args.fail_on_missing:
        return 3
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
