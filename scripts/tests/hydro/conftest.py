# -*- coding: utf-8 -*-
"""Mette `scripts/hydro` su sys.path per i test del layer criticita' DPC.

Stesso schema di scripts/tests/phenomena/conftest.py: il modulo e' importato
con il nome top-level `fetch_criticality` (nessun package da creare).
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_HYDRO = os.path.join(_ROOT, "scripts", "hydro")

if not os.path.isdir(_HYDRO):
    raise ImportError("scripts/hydro non trovato: %s" % _HYDRO)
if _HYDRO not in sys.path:
    sys.path.insert(0, _HYDRO)
