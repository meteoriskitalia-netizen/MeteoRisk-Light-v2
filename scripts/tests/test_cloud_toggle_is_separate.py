#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Il toggle delle nuvole e' SEPARATO dalla pioggia (2026-11-27).

Non e' un test di rendering: controlla il contratto nel client.
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _html_path():
    ver = os.path.join(ROOT, "VERSION")
    name = None
    with io.open(ver, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("APP_FILE="):
                name = line.split("=", 1)[1].strip()
                break
    if not name:
        raise AssertionError("APP_FILE assente in VERSION")
    return os.path.join(ROOT, name)


def test_cloud_toggle_does_not_turn_on_rain():
    html = io.open(_html_path(), encoding="utf-8",
                   newline="").read()
    assert 'id="live-toggle-nowcastclouds"' in html
    btn = html[html.index('id="live-toggle-nowcastclouds"'):]
    btn = btn[:btn.index(">") + 1]
    assert "title=" in btn and "non" in btn.lower()
    start = html.index("function nowcastCloudsToggle()")
    body = html[start:html.index("function nowcastCloudsRefresh()", start)]
    assert "nowcastProductOn" not in body
    assert "nowcastShow" not in body
    assert "nowcastCloudsAdd" in body
    # pane: nuvole 406, pioggia 407 (nuvole SOTTO)
    assert "nowcastCloudsPane').style.zIndex = 406" in html
    assert "nowcastPane').style.zIndex = 407" in html
    assert ("non è pioggia prevista" in html or
            "non e' pioggia prevista" in html)
