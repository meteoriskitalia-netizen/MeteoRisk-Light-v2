"""Test dello step di commit STABILE delle centraline.

Simula un repository git reale (bare `origin` + clone) e verifica che:

 (a) file presenti + fetch OK  -> viene pubblicato un commit con i dati nuovi;
 (b) file presenti + fetch KO  -> nessun commit, file byte-identici e intatti;
 (c) file assenti              -> ripristino dall'ultimo valido (HEAD), nessun
                                  commit, errore esplicito se non ripristinabile.

I test eseguono il VERO `scripts/stations/publish_stations.py` via subprocess:
non c'e' una reimplementazione della logica sotto test.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PUBLISH = Path(__file__).resolve().parents[1] / "stations" / "publish_stations.py"
FILES = ("data/stations/latest.json", "data/stations/index.json")


def _find_git() -> str:
    g = shutil.which("git")
    if g:
        return g
    # GitHub Desktop bundla git su questo host: non e' su PATH.
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "GitHubDesktop"
    for exe in base.glob("app-*/resources/app/git/cmd/git.exe"):
        return str(exe)
    raise RuntimeError("git non trovato")


GIT = _find_git()

# publish_stations.py invoca `git` per nome (corretto su GitHub Actions, dove
# git e' su PATH). Su questo host git e' solo quello bundlato di GitHub Desktop:
# lo aggiungiamo al PATH del subprocess di test, senza toccare il codice di
# produzione.
_GIT_ENV = dict(os.environ)
_GIT_DIR = str(Path(GIT).parent)
_GIT_ENV["PATH"] = _GIT_DIR + os.pathsep + _GIT_ENV.get("PATH", "")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(
        [GIT, *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} fallito: {r.stderr or r.stdout}")
    return r


def _valid_pair(generated_at: str, n: int = 3) -> dict[str, str]:
    stations = [
        {"id": f"S{i}", "lat": 45.0 + i / 100, "lon": 9.0 + i / 100,
         "temperature_2m": 10.0 + i}
        for i in range(n)
    ]
    latest = {
        "generated_at": generated_at,
        "source": "stub",
        "stations": stations,
        "counts": {"stations": n},
    }
    index = {"schema": "stations-index/1", "count": n, "stations": stations}
    return {
        FILES[0]: json.dumps(latest, ensure_ascii=False, indent=1) + "\n",
        FILES[1]: json.dumps(index, ensure_ascii=False, indent=1) + "\n",
    }


def _write(repo: Path, contents: dict[str, str]) -> None:
    for rel, text in contents.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")


def _sha(repo: Path, rel: str) -> str:
    return hashlib.sha256((repo / rel).read_bytes()).hexdigest()


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path):
    """Bare origin + clone con il dataset valido iniziale gia' pubblicato."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main", ".")
    _git(tmp_path, "clone", str(origin), str(work))
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.test")
    # Su Windows il git di GitHub Desktop ha core.autocrlf=true: il checkout
    # riscriverebbe LF->CRLF e l'hash del file ripristinato cambierebbe.
    # GitHub Actions e' Linux (autocrlf=false): rendiamo il test deterministico.
    _git(work, "config", "core.autocrlf", "false")
    _write(work, _valid_pair("2026-11-18T00:00:00Z"))
    _git(work, "add", "--", *FILES)
    _git(work, "commit", "-m", "seed valido")
    _git(work, "push", "-u", "origin", "main")
    return work, origin


def _publish(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PUBLISH), "--root", str(repo),
         "--message", "chore(stations): test"],
        cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, env=_GIT_ENV,
    )


# --------------------------------------------------------------------------
# (a) file presenti + fetch OK -> commit
# --------------------------------------------------------------------------
def test_a_present_fetch_ok_publishes(repo):
    work, origin = repo
    before = _head(work)
    _write(work, _valid_pair("2026-11-18T00:10:00Z", n=4))

    r = _publish(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "status=committed" in r.stdout, r.stdout
    assert _head(work) != before, "nessun nuovo commit locale"

    # il nuovo dataset e' arrivato davvero su origin/main
    served = json.loads(
        subprocess.run(
            [GIT, "show", f"origin/main:{FILES[0]}"], cwd=str(work),
            capture_output=True, text=True, check=True,
        ).stdout
    )
    assert served["generated_at"] == "2026-11-18T00:10:00Z"
    assert len(served["stations"]) == 4
    # entrambi i file sono presenti nel commit pubblicato
    for rel in FILES:
        out = subprocess.run(
            [GIT, "show", f"origin/main:{rel}"], cwd=str(work),
            capture_output=True, text=True, check=False,
        )
        assert out.returncode == 0, f"{rel} mancante nel commit pubblicato"


# --------------------------------------------------------------------------
# (b) file presenti + fetch KO -> nessun commit, file intatti
# --------------------------------------------------------------------------
def test_b_present_fetch_ko_no_commit_files_intact(repo):
    work, origin = repo
    before_head = _head(work)
    before = {rel: _sha(work, rel) for rel in FILES}

    # il fetch fallisce: non scrive niente (esattamente cio' che fa il fetch
    # reale quando la validazione non passa: return 2/3 senza scrivere).
    stub = work / "scripts/stations/fetch_stations.py"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text("import sys\nsys.stderr.write('[FATAL] fetch KO\\n')\nsys.exit(3)\n",
                    encoding="utf-8")
    fetch = subprocess.run([sys.executable, str(stub)], cwd=str(work),
                           capture_output=True, text=True)
    assert fetch.returncode == 3

    r = _publish(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "status=no_change" in r.stdout, r.stdout
    assert _head(work) == before_head, "e' stato fatto un commit con il fetch KO"
    for rel in FILES:
        assert _sha(work, rel) == before[rel], f"{rel} modificato"
        assert (work / rel).is_file(), f"{rel} sparito"

    # origin/main invariato
    origin_head = subprocess.run(
        [GIT, "rev-parse", "main"], cwd=str(origin),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert origin_head == before_head


# --------------------------------------------------------------------------
# (c) file assenti -> ripristino da ultimo valido
# --------------------------------------------------------------------------
def test_c_absent_files_restored_from_head(repo):
    work, origin = repo
    before_head = _head(work)
    expected = {rel: _sha(work, rel) for rel in FILES}

    for rel in FILES:
        (work / rel).unlink()
    assert all(not (work / rel).exists() for rel in FILES)

    r = _publish(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "status=restored" in r.stdout, r.stdout
    assert "ripristinato da HEAD" in r.stderr, r.stderr
    assert _head(work) == before_head, "il ripristino non deve commottare"
    for rel in FILES:
        assert (work / rel).is_file(), f"{rel} non ripristinato"
        assert _sha(work, rel) == expected[rel], f"{rel} ripristinato diverso"


def test_c2_only_index_absent_restored(repo):
    """Add parziale: manca solo index.json -> must not stage a deletion."""
    work, origin = repo
    before_head = _head(work)
    expected = {rel: _sha(work, rel) for rel in FILES}

    (work / FILES[1]).unlink()

    r = _publish(work)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "status=restored" in r.stdout, r.stdout
    assert _head(work) == before_head
    for rel in FILES:
        assert _sha(work, rel) == expected[rel]


def test_c3_missing_in_head_is_explicit_error(tmp_path: Path):
    """Se i file non sono MAI esistiti in HEAD, l'errore deve essere esplicito."""
    work = tmp_path / "fresh"
    work.mkdir()
    _git(work, "init", "-b", "main", ".")
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.test")
    _git(work, "config", "core.autocrlf", "false")
    (work / "README.md").write_text("niente stations\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "seed senza stations")

    r = _publish(work)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "status=error" in r.stdout, r.stdout
    assert "ripristino da HEAD non riuscito" in r.stderr, r.stderr


def test_verify_outputs_detects_latest_index_mismatch(tmp_path: Path):
    """Guardia: latest e index disallineati -> problema, non si commotta."""
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("publish_stations", PUBLISH)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)

    contents = _valid_pair("2026-11-18T00:00:00Z", n=3)
    obj = json.loads(contents[FILES[1]])
    obj["count"] = 99  # disallineato
    contents[FILES[1]] = json.dumps(obj) + "\n"
    _write(tmp_path, contents)

    problems = mod.verify_outputs(tmp_path)
    assert any("disallineati" in p for p in problems), problems
