#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AFA multi-frame (1.3.0.45) — finestra 25 slot, manifest additivo,
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
HTML_CANDIDATES = ("mri-light-1.3.0.45.html", "mri-light-1.3.0.43.html")


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
    assert target.is_file() and target.read_bytes() == empty


def test_afa_http_error_does_not_write_file(tmp_path, monkeypatch):
    slot = SE.compute_slot(None)
    monkeypatch.setattr(SE, "fetch_afa_png", lambda url: None)
    assert SE.process_afa_source(slot, tmp_path, {}) is None
    assert not (tmp_path / "afa" / (SE.slot_name(slot) + SE.EXT)).exists()


# ---------------------------------------------------------------------------
# 4. Client STATIC-ONLY: showAfaFrameAt usa il manifest, nessun fallback WMS.
# ---------------------------------------------------------------------------
def test_client_afa_has_no_wms_fallback():
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
