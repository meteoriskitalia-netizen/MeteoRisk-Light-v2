# -*- coding: utf-8 -*-
"""Sintassi JavaScript degli script inline di `simple/index.html` (vista Simple).

Gemello di `test_html_js_syntax.py`, che valida SOLO `APP_FILE` (la Pro). La
Simple e' un secondo HTML pubblicato su Pages (`/simple/`) e non era coperta:
un apostrofo non escapato in un blocco inline aprirebbe la pagina con zero
funzioni definite, senza che nessun test Python lo veda.

Se `node` non e' nel PATH il test viene SALTATO (dichiarato), mai finto verde.
"""
import os
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIMPLE_HTML = os.path.join(ROOT, "simple", "index.html")


def _inline_scripts(html):
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html,
                      re.S | re.I)


def test_simple_file_exists():
    assert os.path.exists(SIMPLE_HTML), "simple/index.html mancante"


def test_simple_inline_scripts_are_valid_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("node non disponibile: controllo sintassi JS non eseguibile")
    with open(SIMPLE_HTML, "r", encoding="utf-8", errors="replace") as fh:
        html = fh.read()
    blocks = _inline_scripts(html)
    assert blocks, "nessun blocco <script> inline in simple/index.html"
    tmp = tempfile.mkdtemp(prefix="mri-simple-jscheck-")
    failures = []
    for i, body in enumerate(blocks):
        path = os.path.join(tmp, "block_%02d.js" % i)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        proc = subprocess.run([node, "--check", path], capture_output=True, text=True)
        if proc.returncode != 0:
            failures.append((i, (proc.stderr or proc.stdout or "").strip()))
    assert not failures, "script inline con SyntaxError: %s" % (
        [(i, msg.splitlines()[:4]) for i, msg in failures],)


def test_simple_has_sri_and_no_service_worker():
    with open(SIMPLE_HTML, "r", encoding="utf-8", errors="replace") as fh:
        html = fh.read()
    # Leaflet da CDN: DEVE avere l'integrity (SRI) verificato.
    assert re.search(r"leaflet@1\.9\.4/dist/leaflet\.js[^>]*integrity=\"sha256-"
                     r"20nQCchB9co0qIjJZRGuk2/Z9VM\+kNiyxNV1lvTlZBo=\"", html)
    assert re.search(r"leaflet@1\.9\.4/dist/leaflet\.css[^>]*integrity=\"sha256-"
                     r"p4NxAoJBhIIN\+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=\"", html)
    # La Simple non registra un Service Worker (dichiarato nel footer).
    assert "serviceWorker.register" not in html
    assert "non registra un Service Worker" in html
