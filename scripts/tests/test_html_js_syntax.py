# -*- coding: utf-8 -*-
"""Sintassi JavaScript degli script inline dell'html pubblicato.

Perche' esiste: un apostrofo NON escapato dentro una stringa del changelog
(`'... probabilita' calibrata ...'`) chiude il letterale e rende l'INTERO
script inline un SyntaxError: la pagina si apre ma nessuna funzione e'
definita. I test Python non leggono il JS, e i test .mjs esistenti estraggono
singole funzioni con regex (quindi non vedono l'errore). Questo test compila
OGNI blocco <script> inline con `node --check`, che e' l'unico controllo che
copre l'intero file.

Se `node` non e' nel PATH il test viene SALTATO (dichiarato), mai finto verde.
"""
import os
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _app_file():
    """APP_FILE dichiarato in VERSION (fonte unica della verita')."""
    path = os.path.join(ROOT, "VERSION")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("APP_FILE="):
                return line.split("=", 1)[1].strip()
    return None


def _inline_scripts(html):
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html,
                      re.S | re.I)


def test_inline_scripts_are_valid_javascript():
    app = _app_file()
    assert app, "VERSION senza APP_FILE"
    html_path = os.path.join(ROOT, app)
    assert os.path.exists(html_path), "APP_FILE non trovato: %s" % app
    node = shutil.which("node")
    if not node:
        pytest.skip("node non disponibile: controllo sintassi JS non eseguibile")

    with open(html_path, "r", encoding="utf-8", errors="replace") as fh:
        html = fh.read()
    blocks = _inline_scripts(html)
    assert blocks, "nessun blocco <script> inline trovato in %s" % app

    tmp = tempfile.mkdtemp(prefix="mri-jscheck-")
    failures = []
    for i, body in enumerate(blocks):
        path = os.path.join(tmp, "block_%02d.js" % i)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        proc = subprocess.run([node, "--check", path],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            failures.append((i, (proc.stderr or proc.stdout or "").strip()))
    assert not failures, "script inline con SyntaxError: %s" % (
        [(i, msg.splitlines()[:4]) for i, msg in failures],)
