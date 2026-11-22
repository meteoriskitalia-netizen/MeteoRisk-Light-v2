#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test minimi (3 casi) della validazione indipendente dei fronti.

Nessun dato simulato nel sistema operativo: i frame qui sono LineString
sintetici usati SOLO per verificare la logica di matching (regola #237).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from validation.validate_fronts_independent import (  # noqa: E402
    classify_pair, evaluate, parse_utc, point_to_line_km, scores, within_window,
)


LINE = [[12.0, 40.0], [12.5, 40.2], [13.0, 40.4]]
STATIONS = {"TEST": (40.1, 12.2)}  # (lat, lon) — vicina alla LINE di test


def _event(t, station="TEST"):
    return {"station": station, "t": t, "lat": 40.1, "lon": 12.2, "kind": "RA"}


def _frame(vt, lines=None):
    return {"vt": vt, "lines": [LINE] if lines is None else [lines]}


def test_positive_case():
    """Un fronte entro 50 km + pioggia osservata entro 3 h => hit e POD/CSI calcolati."""
    d = point_to_line_km(40.1, 12.2, [LINE])
    assert d is not None and 0 <= d < 50
    assert classify_pair(True, True) == "hits"
    vt = parse_utc("2026-11-19T12:00Z")
    rep = evaluate([_frame(vt)], [_event(vt + timedelta(hours=1))], STATIONS, 50.0, 3.0)
    assert rep["counts"]["hits"] == 1
    assert rep["positives"] == 1
    assert rep["scores"]["POD"]["value"] == 1.0
    assert rep["scores"]["CSI"]["value"] == 1.0


def test_negative_case():
    """Nessun fronte e nessuna pioggia => correct_negative; FAR resta null senza denominatore."""
    assert classify_pair(False, False) == "correct_negatives"
    assert classify_pair(True, False) == "false_alarms"
    vt = parse_utc("2026-11-19T12:00Z")
    far_line = [[12.0, 41.9], [12.5, 42.0], [13.0, 42.1]]
    rep = evaluate([_frame(vt, far_line)], [], STATIONS, 50.0, 3.0)
    assert rep["counts"]["correct_negatives"] == 1
    assert rep["negatives"] == 1
    assert rep["scores"]["FAR"]["null_reason"] == "zero_denominator"
    assert rep["scores"]["FAR"]["value"] is None


def test_control_6h():
    """Un evento a +4 h e' fuori dalla finestra 3 h ma dentro la finestra 6 h."""
    vt = parse_utc("2026-11-19T12:00Z")
    ev = _event(vt + timedelta(hours=4))
    assert not within_window(ev["t"], vt, 3.0)
    assert within_window(ev["t"], vt, 6.0)
    p3 = evaluate([_frame(vt)], [ev], STATIONS, 50.0, 3.0)
    p6 = evaluate([_frame(vt)], [ev], STATIONS, 50.0, 6.0)
    # a 3 h il fronte c'e' ma la pioggia e' fuori finestra -> false_allarme
    assert p3["counts"]["hits"] == 0 and p3["counts"]["false_alarms"] == 1
    # a 6 h la pioggia entra nella finestra -> hit (l'allarme non e' piu' falso)
    assert p6["counts"]["hits"] == 1 and p6["counts"]["false_alarms"] == 0
