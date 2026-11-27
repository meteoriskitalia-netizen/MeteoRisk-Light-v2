#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""I controlli del nowcast sono nel pannello del prodotto e la vista e'
esclusiva (2026-11-27). Non e' un test di rendering: controlla il contratto.
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


def test_nowcast_controls_are_inside_the_product_panel():
    html = io.open(_html_path(), encoding="utf-8", newline="").read()
    panel = html[html.index('id="nowcast-panel"'):]
    panel = panel[:panel.index('<!-- Selettore ORARIO')]
    assert 'id="nc-view-intensity"' in panel
    assert 'id="nc-view-prob"' in panel
    assert 'id="ncprob-controls"' in panel
    assert 'id="nc-clouds-toggle"' in panel
    # il pannello LIVE non contiene piu' i controlli del nowcast
    live = html[html.index('class="map-corner-switches"'):]
    live = live[:live.index('id="nowcast-panel"')]
    assert "live-toggle-nowcastprob" not in live
    assert "live-toggle-nowcastclouds" not in live
    assert "nc-view-intensity" not in live


def test_view_is_mutually_exclusive():
    html = io.open(_html_path(), encoding="utf-8", newline="").read()
    start = html.index("function nowcastSetView(view)")
    body = html[start:html.index("function ncProbPane()", start)]
    # vista prob: intensita' a opacita' 0 e probabilita' disegnata
    assert "setOpacity(0)" in body
    assert "ncProbOn = true" in body
    # vista intensita': probabilita' rimossa
    assert "ncProbClearLayer" in body
    assert "setOpacity(0.75)" in body
    # il layer probabilita' ha un pane sopra la pioggia
    assert "nowcastPane').style.zIndex = 407" in html
    assert "ncProbPane').style.zIndex = 408" in html
    # nuvole sotto la pioggia, toggle separato
    assert "nowcastCloudsPane').style.zIndex = 406" in html
    assert "nowcastProductOn" not in html[
        html.index("function nowcastCloudsToggle()"):
        html.index("function nowcastCloudsRefresh()")]
