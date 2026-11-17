#!/usr/bin/env python3
"""Pubblica in modo STABILE il dataset delle centraline (data/stations/).

Perche' esiste
--------------
Il vecchio step di commit di `.github/workflows/stations.yml` faceva:

    git add data/stations
    ...
    git pull --rebase --autostash -X theirs origin main && git push origin main

`git add data/stations` e' un ADD DIRETTORIO: se al momento del commit uno dei
due file non e' presente nel working tree (fetch che ha scritto solo meta' del
output, checkout parziale, script che cambia i path, rimozione manuale), git
mette in stage la CANCELLAZIONE e il push la pubblica su `main`. I file spariscono
anche se il run e' "success": e' un `git rm` implicito, non un errore di rete.

Questo modulo elimina quella possibilita':

1. **Verifica prima di ogni add** (`verify_outputs`): entrambi i file devono
   esistere, essere non vuoti, essere JSON valido e descrivere lo stesso numero
   di stazioni. Ogni problema e' un `null_reason` esplicito (stessa idea di
   `severe_stack.field()`: nessun buco silenzioso).
2. **Ripristino dall'ultimo valido** (`restore_last_valid`): se la verifica
   fallisce, si ripristinano i file dal commit HEAD (`git checkout HEAD -- ...`)
   e NON si commotta nulla. Se mancano anche in HEAD, l'errore e' esplicito.
3. **ADD esplicito ai due file** (mai alla directory): anche un file stray non
   puo' entrare nel commit, e una cancellazione non puo' essere messa in stage
   perche' i file sono stati verificati/ripristinati prima.
4. **Nessun commit se non cambia niente**: il dataset valido precedente resta
   in piedi (e' il comportamento "conserva l'ultimo dataset valido" che il
   fetch script gia' applica non scrivendo nulla quando la validazione fallisce).

Il modulo e' invocato dal workflow con:

    python -u scripts/stations/publish_stations.py \
        --message "chore(stations): osservazioni MeteoHub ..."

Codici di uscita: 0 = commit pubblicato oppure nessuna modifica oppure ripristino
riuscito; 1 = errore (verifica fallita e ripristino impossibile, o push fallito).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

#: I due file che compongono il dataset. Ordine stabile per log riproducibili.
STATION_FILES: tuple[str, ...] = (
    "data/stations/latest.json",
    "data/stations/index.json",
)


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Esegue git con cwd=root, senza shell, restituendo stdout/stderr testuali."""
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _read_json(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        obj = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def verify_outputs(
    root: Path, paths: Iterable[str] = STATION_FILES
) -> list[str]:
    """Restituisce i motivi per cui il dataset NON e' pubblicabile; [] = ok.

    Controlli (tutti eseguiti, non ci si ferma al primo):
      * file presente, regolare e non vuoto;
      * JSON valido e oggetto;
      * `latest.json` ha una lista `stations` non vuota e `generated_at`;
      * `index.json` ha un `count` coerente con `len(latest.stations)`.

    Il controllo di coerenza e' voluto: impedisce di committare un latest nuovo
    con un index vecchio (situazione che il fetch script evita validando prima
    di scrivere, ma che un add parziale potrebbe invece introdurre).
    """
    paths = tuple(paths)
    problems: list[str] = []
    docs: list[dict | None] = []
    for rel in paths:
        p = root / rel
        if not p.exists():
            problems.append(f"{rel}: file assente")
            docs.append(None)
            continue
        if not p.is_file():
            problems.append(f"{rel}: non e' un file regolare")
            docs.append(None)
            continue
        try:
            size = p.stat().st_size
        except OSError as exc:
            problems.append(f"{rel}: stat fallita ({exc})")
            docs.append(None)
            continue
        if size == 0:
            problems.append(f"{rel}: file vuoto (0 byte)")
            docs.append(None)
            continue
        doc = _read_json(p)
        if doc is None:
            problems.append(f"{rel}: JSON non valido o non oggetto")
            docs.append(None)
            continue
        docs.append(doc)

    # Coerenza fra i due documenti (solo se entrambi sono leggibili).
    latest = docs[0] if len(docs) > 0 else None
    index = docs[1] if len(docs) > 1 else None
    if latest is not None:
        stations = latest.get("stations")
        if not isinstance(stations, list) or not stations:
            problems.append("latest.json: 'stations' assente o vuoto")
        if not latest.get("generated_at"):
            problems.append("latest.json: 'generated_at' assente")
    if index is not None and isinstance(index.get("count"), int):
        if latest is not None and isinstance(latest.get("stations"), list):
            n_latest = len(latest["stations"])
            n_index = index["count"]
            if n_latest != n_index:
                problems.append(
                    f"latest/index disallineati: latest={n_latest} index={n_index}"
                )
    return problems


def restore_last_valid(
    root: Path, paths: Iterable[str] = STATION_FILES
) -> tuple[bool, str]:
    """Ripristina i file dall'ultimo commit valido (HEAD). Non commotta.

    Returns: (success, message). `success=False` e' un errore ESPLICITO:
    i file non erano mai stati committati, quindi non esiste un 'ultimo valido'.
    """
    paths = tuple(paths)
    res = _run_git(root, "checkout", "HEAD", "--", *paths)
    if res.returncode != 0:
        return False, (
            "ripristino da HEAD non riuscito: "
            + (res.stderr.strip() or res.stdout.strip() or "git checkout HEAD fallito")
        )
    return True, "ripristinato da HEAD (ultimo dataset valido)"


def _git_identity(root: Path) -> None:
    """Imposta l'identita' del bot; gli errori sono diagnostici, non silenziati."""
    for key, value in (
        ("user.name", "meteorisk-bot"),
        ("user.email", "actions@users.noreply.github.com"),
    ):
        r = _run_git(root, "config", key, value)
        if r.returncode != 0:
            print(f"[WARN] git config {key}: {r.stderr.strip()}", file=sys.stderr)


def publish(
    root: Path,
    paths: Iterable[str] = STATION_FILES,
    *,
    message: str = "chore(stations): osservazioni MeteoHub (CC BY 4.0, attribuzione per rete)",
    remote: str = "origin",
    branch: str = "main",
    max_attempts: int = 5,
) -> dict:
    """Verifica, ripristina se serve, poi commotta SOLO i due file noti.

    Restituisce un dizionario con `status` in:
      * "committed"  -> nuovo commit pubblicato;
      * "no_change"  -> dataset valido invariato, nessun commit (corretto);
      * "restored"   -> file mancanti/corrotti ripristinati da HEAD, nessun commit;
      * "error"      -> errore esplicito con `reasons`.
    """
    root = Path(root)
    paths = tuple(paths)
    result: dict = {"status": "error", "reasons": [], "commit": None}

    # 1) Guardia: niente add finche' il dataset non e' verificato.
    problems = verify_outputs(root, paths)
    if problems:
        print("[WARN] dataset non pubblicabile:", file=sys.stderr)
        for p in problems:
            print(f"   - {p}", file=sys.stderr)
        ok, info = restore_last_valid(root, paths)
        if not ok:
            result["reasons"] = problems + [info]
            print(f"[FATAL] {info}", file=sys.stderr)
            return result
        after = verify_outputs(root, paths)
        if after:
            result["reasons"] = problems + ["dopo ripristino: " + "; ".join(after)]
            print("[FATAL] ripristino non ha prodotto un dataset valido", file=sys.stderr)
            return result
        result["status"] = "restored"
        result["reasons"] = problems
        result["restore_info"] = info
        print(f"[OK] {info}; nessun commit effettuato.", file=sys.stderr)
        return result

    # 2) ADD esplicito dei due file (MAI `git add data/stations`).
    _git_identity(root)
    add = _run_git(root, "add", "--", *paths)
    if add.returncode != 0:
        result["reasons"] = ["git add fallito: " + (add.stderr.strip() or add.stdout.strip())]
        print(f"[FATAL] {result['reasons'][0]}", file=sys.stderr)
        return result

    # 3) Nessuna modifica -> nessun commit: l'ultimo valido resta in piedi.
    diff = _run_git(root, "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        result["status"] = "no_change"
        print(
            "[INFO] Nessuna modifica alle osservazioni: "
            "ultimo dataset valido conservato (nessun commit)."
        )
        return result
    if diff.returncode != 1:
        result["reasons"] = [
            "git diff --cached --quiet exit "
            f"{diff.returncode}: {(diff.stderr or diff.stdout).strip()}"
        ]
        print(f"[FATAL] {result['reasons'][0]}", file=sys.stderr)
        return result

    # 4) Commit + push con rebase, come nel workflow originale.
    commit = _run_git(root, "commit", "-m", message)
    if commit.returncode != 0:
        result["reasons"] = ["git commit fallito: " + (commit.stderr.strip() or commit.stdout.strip())]
        print(f"[FATAL] {result['reasons'][0]}", file=sys.stderr)
        return result

    push_ok = False
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        _run_git(root, "rebase", "--abort")  # pulisce un eventuale rebase interrotto
        pull = _run_git(
            root, "pull", "--rebase", "--autostash", "-X", "theirs", remote, branch
        )
        if pull.returncode != 0:
            last_error = f"pull --rebase: {(pull.stderr or pull.stdout).strip()}"
        else:
            push = _run_git(root, "push", remote, branch)
            if push.returncode == 0:
                push_ok = True
                break
            last_error = f"push: {(push.stderr or push.stdout).strip()}"
        print(f"[WARN] Push rifiutato (race su {branch}) - tentativo {attempt}/{max_attempts}", file=sys.stderr)

    if not push_ok:
        result["reasons"] = [f"push non riuscito dopo {max_attempts} tentativi: {last_error}"]
        print(f"[FATAL] {result['reasons'][0]}", file=sys.stderr)
        return result

    head = _run_git(root, "rev-parse", "HEAD")
    result["status"] = "committed"
    result["commit"] = head.stdout.strip() if head.returncode == 0 else None
    print(f"[OK] Pubblicato {result['commit']} su {remote}/{branch}.")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".", help="root del repository (default: cwd)")
    ap.add_argument(
        "--file",
        action="append",
        dest="paths",
        default=None,
        help="file da pubblicare (ripetibile); default: i due di STATION_FILES",
    )
    ap.add_argument(
        "--message",
        default="chore(stations): osservazioni MeteoHub (CC BY 4.0, attribuzione per rete)",
    )
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--max-attempts", type=int, default=5)
    args = ap.parse_args(argv)

    paths = tuple(args.paths) if args.paths else STATION_FILES
    res = publish(
        Path(args.root),
        paths,
        message=args.message,
        remote=args.remote,
        branch=args.branch,
        max_attempts=args.max_attempts,
    )
    print(f"[RESULT] status={res['status']} reasons={res['reasons']}")
    return 0 if res["status"] != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
