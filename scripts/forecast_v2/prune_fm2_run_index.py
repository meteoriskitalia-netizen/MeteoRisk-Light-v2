#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep only the newest WebP forecast runs in an isolated Git index.

The old render blobs do not need to be checked out: ``git ls-files`` reads the
temporary index tree and ``git update-index --force-remove`` prunes old paths
while preserving the retained run blobs by object id.
"""

import argparse
import os
import subprocess


def git(index_path, *args):
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_path
    return subprocess.check_output(["git", *args], env=env, text=True).splitlines()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--keep", type=int, default=3)
    args = parser.parse_args()
    base = args.path.rstrip("/")
    paths = git(args.index, "ls-files", "--", base)
    run_ids = sorted({p[len(base) + 6:].split("/", 1)[0]
                      for p in paths
                      if p.startswith(base + "/runs/") and
                      len(p[len(base) + 6:].split("/")) >= 3})
    while len(run_ids) > max(1, args.keep):
        oldest = run_ids.pop(0)
        prefix = base + "/runs/" + oldest
        for path in git(args.index, "ls-files", "--", prefix):
            git(args.index, "update-index", "--force-remove", "--", path)
        print("[runs] rimossa run WebP obsoleta:", oldest)
    if len(run_ids) >= max(1, args.keep):
        # Elimina il layout non versionato dopo la migrazione: la nuova struttura
        # mantiene al massimo tre run complete e la vecchia run non è più indicizzata.
        for path in paths:
            if not path.startswith(base + "/"):
                continue
            relative = path[len(base) + 1:]
            if relative.split("/", 1)[0] != "runs":
                git(args.index, "update-index", "--force-remove", "--", path)
    print("[runs] %s: conservate %d run" % (base, len(run_ids)))


if __name__ == "__main__":
    main()
