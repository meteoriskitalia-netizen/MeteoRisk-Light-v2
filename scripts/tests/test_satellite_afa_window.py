#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AFA multi-frame — finestra 25 slot, manifest additivo,
frame vuoto con has_signal=false, client static-only senza fallback WMS.

I test sono scritti CONTRO l'implementazione: sul pristino 1.3.0.44 falliscono
perche' le costanti/le funzioni AFA non esistono. Nessun mock di rete: le
immagini WebP sono generate con Pillow, la stessa libreria usata dal motore.
"""

import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import satellite_engine as SE  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _app_html_path():
    """File dell'app attivo: APP_FILE di VERSION (nessun nome scritto a mano).

    1.3.0.72 - la lista statica puntava a mri-light-1.3.0.55.html, che non esiste
    piu': il test falliva per FileNotFound anche quando il client era corretto.
    """
    version = REPO / "VERSION"
    for line in version.read_text(encoding="utf-8").splitlines():
        if line.startswith("APP_FILE="):
            p = REPO / line.split("=", 1)[1].strip()
            if p.is_file():
                return p
    return None


HTML_CANDIDATES = ("mri-light-1.3.0.72.html",)


def _webp_bytes(size=(8, 8), alpha=0):
    """WebP RGBA reale: tutto trasparente (alpha=0) o tutto opaco (alpha=255)."""
    img = Image.new("RGBA", size, (10, 20, 30, alpha))
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=80, method=6)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. AFA e' AUSILIARIO: SOURCES resta 6, exit code invariato, finestra 25.
# ---------------------------------------------------------------------------
def test_afa_is_auxiliary_and_window_is_25():
    assert len(SE.SOURCES) == 6
    assert "afa" not in SE.SOURCES
    assert SE.WINDOW_SLOTS == 25
    assert SE.MAX_WORKERS == 8
    assert SE.AFA_LAYER == "mtg_fd:li_afa"
    assert SE.AFA_LICENSE == "CC BY 4.0"
    assert len(SE.window_slots(SE.compute_slot(None))) == 25


# ---------------------------------------------------------------------------
# 2. Manifest ADDITIVO: le 6 sorgenti restano intatte, sources.afa + afa_meta.
# ---------------------------------------------------------------------------
def test_manifest_is_additive_with_sources_afa_and_meta(tmp_path):
    slot = SE.compute_slot(None)
    name = SE.slot_name(slot)
    for src in SE.SOURCES:
        d = tmp_path / src
        d.mkdir()
        (d / (name + SE.EXT)).write_bytes(_webp_bytes())
    afa_dir = tmp_path / "afa"
    afa_dir.mkdir()
    (afa_dir / (name + SE.EXT)).write_bytes(_webp_bytes(alpha=255))

    meta = {
        name: {
            "requested_iso": SE.slot_time(slot),
            "served_iso": None,
            "active_pixels": 64,
            "has_signal": True,
            "sha256": "00" * 32,
            "bytes": 100,
            "layer": SE.AFA_LAYER,
            "license": SE.AFA_LICENSE,
        }
    }
    manifest = SE.write_manifest(tmp_path, slot, "2026-11-22T00:00:00Z", meta)

    assert set(SE.SOURCES).issubset(manifest["sources"].keys())
    for src in SE.SOURCES:
        assert manifest["sources"][src] == [name]
    assert manifest["sources"]["afa"] == [name]
    assert manifest["afa_meta"][name]["has_signal"] is True
    assert manifest["afa_meta"][name]["layer"] == "mtg_fd:li_afa"


# ---------------------------------------------------------------------------
# 3. Frame AFA vuoto: HTTP 200 con alpha=0 e' un'osservazione valida -> file
#    pubblicato e has_signal=false (NON un errore, NON uno slot omesso).
# ---------------------------------------------------------------------------
def test_empty_afa_frame_has_signal_false_but_is_published(tmp_path, monkeypatch):
    empty = _webp_bytes(alpha=0)
    active, signal = SE.afa_frame_stats(empty)
    assert active == 0
    assert signal is False

    nonempty = _webp_bytes(alpha=255)
    active2, signal2 = SE.afa_frame_stats(nonempty)
    assert active2 == 64
    assert signal2 is True

    slot = SE.compute_slot(None)
    monkeypatch.setattr(SE, "fetch_afa_png", lambda url: (empty, None))
    meta = SE.process_afa_source(slot, tmp_path, {})
    assert meta is not None
    assert meta["has_signal"] is False
    assert meta["active_pixels"] == 0
    assert meta["layer"] == "mtg_fd:li_afa"
    target = tmp_path / "afa" / (SE.slot_name(slot) + SE.EXT)
    assert target.is_file()
    # 1.3.0.55 - il frame viene SEMPRE ri-codificato (texture a crocette applicata
    # prima dell'encode WebP): il contratto non e' piu' il pass-through dei byte
    # della WMS, ma il contenuto: file WebP valido con alpha tutto 0 e has_signal
    # false. Un frame vuoto non guadagna pixel attivi dalla texture.
    written = target.read_bytes()
    assert SE.is_webp(written)
    assert SE.afa_frame_stats(written) == (0, False)


def test_afa_http_error_does_not_write_file(tmp_path, monkeypatch):
    slot = SE.compute_slot(None)
    monkeypatch.setattr(SE, "fetch_afa_png", lambda url: None)
    assert SE.process_afa_source(slot, tmp_path, {}) is None
    assert not (tmp_path / "afa" / (SE.slot_name(slot) + SE.EXT)).exists()


# ---------------------------------------------------------------------------
# 4. Client STATIC-ONLY: showAfaFrameAt usa il manifest, nessun fallback WMS.
# ---------------------------------------------------------------------------
def test_client_afa_has_no_wms_fallback():
    html_path = _app_html_path()
    if html_path is None:
        html_path = None
        for name in HTML_CANDIDATES:
            if (REPO / name).is_file():
                html_path = REPO / name
                break
    assert html_path is not None, "nessun html di release trovato"
    html = html_path.read_text(encoding="utf-8")

    fn_start = html.find("function showAfaFrameAt(")
    assert fn_start != -1, "showAfaFrameAt assente nel client"
    depth = 0
    fn_end = None
    for i in range(html.find("{", fn_start), len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                fn_end = i + 1
                break
    assert fn_end is not None
    body = html[fn_start:fn_end]

    assert "satStaticManifest.sources.afa" in body
    assert "satStaticUrl('afa'" in body
    for forbidden in ("view.eumetsat.int", "buildEumetsatLiveGetMap",
                      "liveAfaGetMapUrl", "GetMap"):
        assert forbidden not in body, "fallback WMS nel client AFA: %s" % forbidden

    sync_start = html.find("function applySyncFrame(")
    assert sync_start != -1
    sync = html[sync_start:html.find("function onSyncSliderChange(", sync_start)]
    assert "showAfaFrameAt(slot.ms)" in sync


# ---------------------------------------------------------------------------
# 5. 1.3.0.72 - STATO AFA: intenzione separata dal layer, fallback Europa
#    dichiarato, copertura esplicita. Il ramo ITALIA resta static-only (test 4).
# ---------------------------------------------------------------------------
def test_client_afa_toggle_state_and_europe_fallback():
    html_path = _app_html_path()
    assert html_path is not None, "nessun html di release trovato"
    html = html_path.read_text(encoding="utf-8")

    # A1: due variabili distinte e click indipendente dal flag del layer.
    assert "var liveAfaWanted = false;" in html
    assert "var liveAfaEnabled = false;" in html
    assert 'onclick="afaToggleAfa()"' in html
    assert "function liveAfaApply(" in html
    assert "AFA_RETRY_MAX" in html and "AFA_RETRY_MS" in html
    # Il messaggio di errore usa l'id REALE del riquadro di legenda.
    assert "document.getElementById('afa-legend-status')" in html
    # A2: copertura e risoluzione dichiarate nel title e in legenda.
    assert "Copertura: Italia 6,6-18,8" in html
    assert 'id="afa-legend-coverage"' in html
    assert "1,33 km/px" in html
    # A3: due regioni, default Italia, bbox Europa -25..45E / 30..72N.
    assert "AFA_REGION_DEFAULT = 'it'" in html
    assert 'id="afa-region-it"' in html and 'id="afa-region-eu"' in html
    assert "L.latLngBounds([[30, -25], [72, 45]])" in html
    # Il fallback WMS vive in showAfaEuropeFrameAt, dichiarato, non nel ramo Italia.
    eu_start = html.find("function showAfaEuropeFrameAt(")
    assert eu_start != -1, "showAfaEuropeFrameAt assente"
    eu_end = html.find("function warmAheadEumetsatTiles", eu_start)
    eu_body = html[eu_start:eu_end]
    assert "sources.afa_eu" in eu_body
    assert "buildEumetsatLiveGetMap(AFA_WMS_LAYER" in eu_body
    assert "richiesta diretta al servizio EUMETSAT" in eu_body
