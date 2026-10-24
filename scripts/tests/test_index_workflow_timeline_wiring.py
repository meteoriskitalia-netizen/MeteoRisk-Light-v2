# -*- coding: utf-8 -*-
"""Test STATICO del wiring `timeline.json` <-> `forecast-maps-index.yml`.

Nessuna esecuzione di GitHub Actions, nessun dato reale, nessuna rete: si legge
il workflow come TESTO + YAML e si verifica che il collegamento al generatore
`scripts/forecast_v2/build_timeline_index.py` sia quello dichiarato.

Cosa e' coperto:

  A) il workflow e' YAML valido, ha un solo job e lo step `timeline` sta FRA il
     rebuild degli `index.json` (`id: index`) e lo step di commit (il file deve
     citare i frame dei manifest gia' materializzati, e deve esistere prima che
     il commit legga il working tree);
  B) il comando invoca il generatore (file che deve ESISTERE nel repo) con
     `--root data/fm2-rendered`, `--regions` e `--path-list`, e NON con `--now`
     (l'ora deve essere quella reale: e' `generated_at` a far accorgere il
     polling del client), ne' con `--dry-run`/`--fail-on-missing`;
  C) la guardia di scope ammette SOLO `<regione>/{index,timeline}.json` +
     `color_scales.json` e RIFIUTA qualunque `.webp` (tabella di path, stessa
     semantica di `grep -E -v "$ALLOW_RE"` usata nello step di commit);
  D) i `timeline.json` entrano nel commit SOLO con `timeline_ok == 'true'`, lo
     step di commit resta un NO-OP a 0 file quando il tree non cambia e non
     esiste alcun `git commit --allow-empty` (nessun commit forzato);
  E) il fallimento del generatore e' un `::warning::` + `exit 0` (l'indice si
     pubblica comunque) e scrive `timeline_ok=false` (il file non viene
     committato);
  F) `generated_at` viene dall'OROLOGIO (test unitario con clock iniettato) e
     il default e' l'ora UTC reale, non una costante.
"""

import datetime as dt
import importlib.util
import os
import re
import shutil
import subprocess

import pytest

# PyYAML: usato per il parsing del workflow. Se manca, il test si SALTA invece
# di fallire (dichiarato: senza YAML la verifica statica non e' eseguibile).
yaml = pytest.importorskip("yaml")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
WORKFLOW_REL = ".github/workflows/forecast-maps-index.yml"
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows",
                        "forecast-maps-index.yml")
GENERATOR_REL = "scripts/forecast_v2/build_timeline_index.py"
GENERATOR = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                         "build_timeline_index.py")

REGIONS = ("italia", "europa", "north", "domain")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load():
    """Il workflow parsato con `yaml.safe_load` (stesso controllo della verifica)."""
    assert os.path.isfile(WORKFLOW), "workflow assente: %s" % WORKFLOW
    with open(WORKFLOW, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _steps():
    doc = _load()
    assert list(doc["jobs"]) == ["build-index"], \
        "job inattesi: %s" % sorted(doc["jobs"])
    steps = doc["jobs"]["build-index"]["steps"]
    assert isinstance(steps, list) and steps
    return steps


def _step(sid):
    """`(indice, step)` dello step con `id: <sid>`."""
    for index, step in enumerate(_steps()):
        if step.get("id") == sid:
            return index, step
    raise AssertionError("step con id %r non trovato: %s"
                         % (sid, [s.get("name") for s in _steps()]))


def _commit_step():
    for index, step in enumerate(_steps()):
        name = str(step.get("name", ""))
        if name.lower().startswith("commit "):
            return index, step
    raise AssertionError("step di commit non trovato")


def _allow_re():
    """La regex della guardia di scope, letta DAL workflow (non ricopiata)."""
    _index, step = _commit_step()
    match = re.search(r"^[ \t]*ALLOW_RE='([^']+)'[ \t]*$", step["run"], re.M)
    assert match is not None, "ALLOW_RE non trovata nello step di commit"
    return match.group(1)


def _code(script):
    """Lo script SENZA righe di commento intere (`# ...`), per asserire sul codice.

    Le asserzioni su "questo flag non deve comparire" devono guardare il CODICE
    eseguibile: un commento che NOMINA `git commit --allow-empty` per dire che
    non viene usato non e' un uso.
    """
    lines = [line for line in script.splitlines()
             if not line.lstrip().startswith("#")]
    return "\n".join(lines)


def _between(text, start, end):
    """Sottostringa fra `start` e la PRIMA occorrenza della PAROLA `end`."""
    first = text.index(start) + len(start)
    match = re.search(r"\b" + re.escape(end) + r"\b", text[first:])
    assert match is not None, "manca %r dopo %r" % (end, start)
    return text[first:first + match.start()]


def _shell_blocks():
    """`(nome, script)` dei blocchi `run:` toccati da questa modifica."""
    wanted = {"Build timeline.json per regione (merge run, --path-list)", "Summary"}
    blocks = []
    for step in _steps():
        name = str(step.get("name", ""))
        if name in wanted or name.lower().startswith("commit "):
            assert "run" in step, "step %r senza run:" % name
            # Le espressioni GitHub (`${{ ... }}`) non sono sintassi shell: si
            # sostituiscono con un segnaposto per poter fare il parse.
            script = re.sub(r"\$\{\{[^}]*\}\}", "PLACEHOLDER", step["run"])
            blocks.append((name, script))
    assert len(blocks) == 3, "blocchi run attesi: timeline, Summary, commit -> %s" \
        % [name for name, _ in blocks]
    return blocks


# --------------------------------------------------------------------------- #
# A) YAML valido + POSIZIONE dello step
# --------------------------------------------------------------------------- #
def test_workflow_is_valid_yaml_with_a_single_job():
    doc = _load()
    assert isinstance(doc, dict)
    # NB: in YAML 1.1 la chiave `on:` viene letta come booleano True; qui non
    # serve (non e' oggetto di questa modifica), quindi non si asserisce.
    job = doc["jobs"]["build-index"]
    assert job["runs-on"] == "ubuntu-latest"
    assert isinstance(job["steps"], list) and len(job["steps"]) >= 6


def test_timeline_step_sits_between_index_rebuild_and_commit():
    index_pos, _ = _step("index")
    timeline_pos, timeline_step = _step("timeline")
    commit_pos, _ = _commit_step()
    assert index_pos < timeline_pos < commit_pos, (
        "ordine errato: index=%d timeline=%d commit=%d"
        % (index_pos, timeline_pos, commit_pos))
    assert timeline_step.get("if") == "success()", \
        "lo step timeline deve girare solo dopo un rebuild riuscito"
    assert "timeline" in str(timeline_step["name"]).lower()
    assert timeline_step.get("continue-on-error") in (None, False), \
        "il job deve restare verde per scelta esplicita (exit 0), non per continue-on-error"
    # Il rebuild degli index.json deve restare PRIMA (la timeline legge i
    # manifest che quello step ha materializzato).
    assert "render_fm_webp.py" in _steps()[index_pos]["run"]


# --------------------------------------------------------------------------- #
# B) il comando: script giusto, argomenti giusti, nessun --now
# --------------------------------------------------------------------------- #
def test_timeline_step_invokes_the_generator_with_the_expected_arguments():
    _pos, step = _step("timeline")
    run = step["run"]

    assert GENERATOR_REL.replace("/", "/") in run, \
        "il comando non invoca %s" % GENERATOR_REL
    # Il file citato dal workflow deve esistere davvero nel repo.
    cited = re.search(r"scripts/forecast_v2/[A-Za-z0-9_]+\.py", run)
    assert cited is not None
    assert os.path.isfile(os.path.join(REPO_ROOT, cited.group(0))), \
        "lo script citato dal workflow non esiste: %s" % cited.group(0)

    assert "--root data/fm2-rendered" in run, "manca --root (checkout fm2-rendered)"
    assert "--regions" in run, "manca --regions (regioni presenti)"
    assert "--path-list" in run, \
        "manca --path-list: i frame citati non sarebbero verificati"
    assert '--out-root "$TMP_OUT"' in run, \
        "la timeline deve essere costruita in una directory temporanea"
    assert "git ls-tree -r --name-only FETCH_HEAD" in run, \
        "l'inventario deve venire dall'albero del branch (nessun blob scaricato)"

    # `--now` NON deve essere passato: generated_at deve essere l'ora reale.
    assert not re.search(r"--now\b", run), \
        "--now non va passato: generated_at deve cambiare a ogni run"
    for forbidden in ("--dry-run", "--fail-on-missing"):
        assert forbidden not in run, \
            "%s non deve essere usato qui (non scriverebbe / bloccherebbe)" % forbidden

    # Una sola regione per invocazione (il --path-list e' relativo alla regione).
    assert '--regions "$r"' in run


# --------------------------------------------------------------------------- #
# C) GUARDIA DI SCOPE: timeline ammessa, .webp rifiutato
# --------------------------------------------------------------------------- #
ALLOWED = tuple(
    ["data/fm2-rendered/%s/index.json" % region for region in REGIONS]
    + ["data/fm2-rendered/%s/timeline.json" % region for region in REGIONS]
    + ["data/fm2-rendered/color_scales.json"]
)

FORBIDDEN = (
    # WebP: mai, in nessuna forma (path reale del branch).
    "data/fm2-rendered/italia/dwd_icon/runs/20261023T120000Z/cape/2026-10-23T1200.webp",
    "data/fm2-rendered/domain/ncep_gfs025/runs/20261023T000000Z/vil/"
    "2026-10-23T0000.webp",
    "data/fm2-rendered/europa/dwd_icon_eu/runs/x/var/frame.webp",
    # JSON che NON sono di proprieta' dell'index-builder.
    "data/fm2-rendered/timeline.json",                       # fuori regione
    "data/fm2-rendered/italia/dwd_icon/index.json",          # indice di dominio
    "data/fm2-rendered/italia/dwd_icon/runs/x/cape/latest.json",
    "data/fm2-rendered/Italia/timeline.json",                # case diverso
    "data/fm2-rendered/italia/timeline.json.bak",
    "data/fm2-rendered/italia/xtimeline.json",
    "data/fm2-rendered/italia/timeline.json.old",
    # Codice: fuori scope per definizione.
    "scripts/forecast_v2/build_timeline_index.py",
    ".github/workflows/forecast-maps-index.yml",
)


def test_scope_guard_admits_timeline_json_only_where_expected():
    pattern = re.compile(_allow_re())
    for path in ALLOWED:
        assert pattern.search(path), "la guardia DEVE ammettere %s" % path
    for path in FORBIDDEN:
        assert not pattern.search(path), "la guardia DEVE rifiutare %s" % path


def test_scope_guard_rejects_any_webp():
    pattern = re.compile(_allow_re())
    webp = [p for p in FORBIDDEN if p.endswith(".webp")]
    assert len(webp) == 3
    for path in webp:
        assert not pattern.search(path)


def test_scope_guard_is_applied_before_the_commit_and_aborts():
    _pos, step = _commit_step()
    code = _code(step["run"])
    assert 'grep -E -v "$ALLOW_RE"' in code, \
        "la guardia deve usare grep -E -v con ALLOW_RE"
    assert "git diff-tree -r --no-commit-id --name-only" in code, \
        "i path toccati devono venire dal diff dei due tree"
    guard = code.index('grep -E -v "$ALLOW_RE"')
    commit = code.index("git commit-tree")
    assert guard < commit, "la guardia deve precedere la creazione del commit"
    # Violazione => exit 1 (nessun commit, nessun push).
    violation = _between(code, 'if [ -n "$BAD_SCOPE" ]; then', "fi")
    assert "exit 1" in violation
    # Il conteggio dei file modificati e' stampato (0 nel caso NO-OP).
    assert "CHANGED=" in code and "path toccati" in code


# --------------------------------------------------------------------------- #
# D) commit solo se il generatore e' riuscito + NO-OP intatto
# --------------------------------------------------------------------------- #
def test_timeline_is_staged_only_when_the_builder_succeeded():
    _pos, step = _commit_step()
    run = step["run"]
    assert '${{ steps.timeline.outputs.timeline_ok }}' in run, \
        "il commit deve leggere l'output dello step timeline"
    assert 'if [ "$TIMELINE_OK" = "true" ]; then' in run

    block = _between(run, 'if [ "$TIMELINE_OK" = "true" ]; then', "fi")
    yes_branch, no_branch = block.split("else", 1)
    for region in REGIONS:
        path = "data/fm2-rendered/%s/timeline.json" % region
        assert path in yes_branch, "manca %s fra i path proprietari" % path
        assert path not in no_branch, \
            "con timeline_ok != true %s non deve essere staged" % path
    # I path storici restano fuori dal ramo condizionale: sempre presenti.
    assert "data/fm2-rendered/italia/index.json" in run
    assert "data/fm2-rendered/color_scales.json" in run


def test_commit_step_stays_noop_with_zero_files_when_nothing_changes():
    _pos, step = _commit_step()
    run = step["run"]
    code = _code(run)
    assert 'if [ "$TREE" = "$BASE" ]' in code, "manca il confronto tree/base (NO-OP)"
    noop = _between(code, 'if [ "$TREE" = "$BASE" ]; then', "fi")
    assert "exit 0" in noop, "il caso NO-OP deve uscire 0"
    assert "0 file" in noop, "il caso NO-OP deve dichiarare 0 file modificati"
    assert "commit" not in noop.lower().replace("no-op", ""), \
        "il caso NO-OP non deve creare alcun commit"
    # Nessun commit forzato/vuoto da nessuna parte.
    assert "--allow-empty" not in code
    assert not re.search(r"git\s+commit(?!-tree)", code)
    # L'aggiunta all'indice resta SCOPED ai soli path proprietari.
    assert "git add -A -- $add_paths" in code
    assert not re.search(r"git add -A(?!\s+--)", code), \
        "nessun `git add -A` senza path (staggerebbe i WebP)"


# --------------------------------------------------------------------------- #
# E) fallimento del generatore = warning, non blocco
# --------------------------------------------------------------------------- #
def test_builder_failure_is_a_warning_and_does_not_block_the_index():
    _pos, step = _step("timeline")
    run = step["run"]
    code = _code(run)
    assert "::warning::" in run, "il fallimento deve essere visibile come warning"
    assert "$GITHUB_OUTPUT" in code
    assert "timeline_ok=false" in code
    assert "timeline_ok=true" in code
    # Lo step NON deve far fallire il job: nessun exit 1, e i fallimenti della
    # build sono intercettati (`rc` raccolto e gestito).
    assert not re.search(r"exit\s+1\b", code), \
        "lo step timeline non deve uscire 1: bloccherebbe la pubblicazione"
    assert "rc=$?" in code, "l'exit code del generatore deve essere raccolto"
    assert re.search(r"if \[ \"\$rc\" -ne 0 \]", code)


# --------------------------------------------------------------------------- #
# F) generated_at: orologio vero, non costante
# --------------------------------------------------------------------------- #
def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "build_timeline_index_wiring_under_test", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_at_follows_the_clock(monkeypatch):
    """`utcnow_iso()` senza `--now` legge l'orologio: due istanti -> due valori."""
    module = _load_generator()
    real = module._dt
    first = real.datetime(2026, 10, 25, 10, 0, 1, tzinfo=real.timezone.utc)
    second = first + real.timedelta(seconds=1)
    current = {"now": first}

    class ClockedDT(real.datetime):
        @classmethod
        def now(cls, tz=None):
            value = current["now"]
            return value.astimezone(tz) if tz is not None else value

    class Shim(object):
        datetime = ClockedDT
        timezone = real.timezone
        timedelta = real.timedelta

    monkeypatch.setattr(module, "_dt", Shim)
    assert module.utcnow_iso() == "2026-10-25T10:00:01Z"
    current["now"] = second
    assert module.utcnow_iso() == "2026-10-25T10:00:02Z"
    # Con `--now` esplicito il valore e' quello passato: l'orologio e' usato SOLO
    # quando `--now` non c'e' (che e' il caso del workflow).
    assert module.utcnow_iso("2020-01-01T00:00:00Z") == "2020-01-01T00:00:00Z"


def test_default_generated_at_is_the_real_utc_now():
    module = _load_generator()
    stamp = module.utcnow_iso()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", stamp), stamp
    parsed = module.parse_dt(stamp)
    now = dt.datetime.now(dt.timezone.utc)
    assert abs((now - parsed).total_seconds()) < 5, \
        "generated_at non e' l'ora corrente: %s" % stamp


# --------------------------------------------------------------------------- #
# shell: sintassi dei blocchi `run:` toccati (skip dichiarato se manca bash)
# --------------------------------------------------------------------------- #
def test_touched_run_blocks_are_valid_bash(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash non disponibile su questo host: sintassi NON verificata")
    failures = []
    for index, (name, script) in enumerate(_shell_blocks()):
        # File SENZA BOM e con LF: `bash -n` su stdin non e' portabile su
        # Windows/MSYS (CreateProcess con stdin rediretto), su file si'.
        path = tmp_path / ("block_%d.sh" % index)
        path.write_bytes((script.rstrip("\n") + "\n").encode("utf-8"))
        proc = subprocess.run([bash, "-n", str(path)], capture_output=True)
        if proc.returncode != 0:
            failures.append("%s: %s" % (name,
                                        proc.stderr.decode("utf-8", "replace")))
    assert not failures, "blocchi run: non validi:\n%s" % "\n".join(failures)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
