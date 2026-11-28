#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test delle nuvole IR di contesto (nowcast_clouds.py, 2026-11-27)."""

import os

import numpy as np
import pytest

from radar_engine import nowcast_clouds as nc
from radar_engine import nowcast_field as nf


def test_classify_thresholds():
    t = np.array([[-10.0, -25.0, -35.0, -50.0]], dtype=np.float32)
    v = np.ones_like(t, bool)
    c = nc.classify(t, v)
    assert c.tolist() == [[0, 1, 2, 3]]
    # non valido -> 0 anche se la temperatura e' bassa
    c2 = nc.classify(t, np.zeros_like(t, bool))
    assert int(c2.sum()) == 0


def test_palette_is_separate_from_rain():
    rain = {tuple(rgb) for _mmh, rgb in nf.PALETTE}
    for cls in nc.CLASSES:
        rgb = tuple(cls["rgb"])
        assert rgb not in rain
        r, g, b = rgb
        # grigio-blu: blu >= verde >= rosso, nessun verde/giallo/rosso
        assert b >= g >= r
        assert r < 200


def test_rgba_from_temp_has_soft_class_edges():
    # rampa di 2 °C attorno a -20: nessun gradino 0 -> 90 in un pixel
    t = np.array([[-18.0, -19.0, -19.5, -20.0, -20.5, -21.0, -22.0]],
                 dtype=np.float32)
    v = np.ones_like(t, bool)
    a = nc.rgba_from_temp(t, v, blend_c=2.0, blur_px=0.0)[:, :, 3].astype(int)
    assert a[0, 0] == 0
    assert a[0, 1] == 0
    assert 0 < a[0, 2] < 90
    assert 0 < a[0, 3] < 90
    assert a[0, 4] > a[0, 3]
    assert abs(int(a[0, 5]) - 90) <= 16   # quantizzato a passi di 16
    # nessun salto maggiore di 48/255 in un pixel (prima era 0 -> 90)
    assert np.abs(np.diff(a[0])).max() <= 48
    # il colore e' una delle 3 classi (per peso); la sfumatura e' l'alpha
    t2 = np.array([[-32.0]], dtype=np.float32)
    px = nc.rgba_from_temp(t2, np.ones_like(t2, bool),
                           blend_c=2.0, blur_px=0.0)[0, 0]
    assert tuple(int(x) for x in px[:3]) in (
        nc.CLASSES[0]["rgb"], nc.CLASSES[1]["rgb"], nc.CLASSES[2]["rgb"])
    assert 0 < int(px[3]) < 150


def test_rgba_from_temp_blur_smooths_the_step():
    t = np.full((1, 40), -50.0, np.float32)
    t[:, :20] = -10.0
    v = np.ones_like(t, bool)
    a0 = nc.rgba_from_temp(t, v, blend_c=2.0, blur_px=0.0)[:, :, 3].astype(int)
    a1 = nc.rgba_from_temp(t, v, blend_c=2.0, blur_px=0.7)[:, :, 3].astype(int)
    assert np.abs(np.diff(a1[0])).max() < np.abs(np.diff(a0[0])).max()
    assert int(a1[0, 19]) > 0   # il blur porta un po' di nube oltre il bordo
    assert int(a1[0, 15]) == 0   # ma non allarga l'area di 5 px


def test_ir_continuous_shows_clouds_warmer_than_minus20():
    t = np.array([[-80.0, -50.0, -20.0, -5.0, 0.0, 5.0, 10.0]],
                 dtype=np.float32)
    v = np.ones_like(t, bool)
    a = nc.rgba_ir_continuous(t, v)[:, :, 3].astype(int)
    assert a[0, 0] > a[0, 1] > a[0, 2] > a[0, 3] > a[0, 4]
    assert a[0, 2] > 0          # -20 °C: prima era il bordo della classe
    assert a[0, 3] > 0          # -5 °C: nube media visibile
    assert a[0, 5] == 0         # suolo caldo: nessuna tinta
    assert a[0, 6] == 0
    # sentinelle e pixel non validi: alpha 0, non bianco
    t2 = np.array([[-9999.0, -20.0, -5.0]], np.float32)
    v2 = np.array([[True, False, True]], bool)
    a2 = nc.rgba_ir_continuous(t2, v2)[:, :, 3].astype(int)
    assert a2[0, 0] == 0 and a2[0, 1] == 0 and a2[0, 2] > 0


def test_write_webp_uses_class_alpha(tmp_path):
    c = np.array([[0, 1, 2, 3]], dtype=np.uint8)
    p = str(tmp_path / "clouds.webp")
    n = nc.write_webp(p, c)
    assert n > 0 and os.path.getsize(p) == n
    from PIL import Image
    a = np.array(Image.open(p).convert("RGBA"))
    assert int(a[0, 0, 3]) == 0
    assert int(a[0, 1, 3]) == 90
    assert int(a[0, 2, 3]) == 120
    assert int(a[0, 3, 3]) == 150
    assert tuple(int(x) for x in a[0, 1, :3]) == nc.CLASSES[0]["rgb"]


class _Rd(object):
    def __init__(self, temp, valid, ts):
        self.data = temp
        self.valid_mask = valid
        self.time_ms = ts
        self.rows, self.cols = temp.shape


class _Bundle(object):
    def __init__(self, ir=None, ts=None):
        self.dpc_bundle = {"entries": {}}
        if ir is not None:
            self.dpc_bundle["entries"][int(ts)] = {
                "ir108": {"raster": ir}}


class _Clock(object):
    def __init__(self, ms):
        self._ms = int(ms)

    def now_ms(self):
        return self._ms


def test_build_from_bundle_has_role_context_and_latency():
    rows, cols = 20, 30
    temp = np.full((rows, cols), -5.0, np.float32)
    temp[2:8, 2:8] = -50.0
    temp[12:18, 20:28] = -25.0
    valid = np.ones((rows, cols), bool)
    ts = 1_795_512_000_000
    ir = _Rd(temp, valid, ts)
    bundle = _Bundle(ir, ts)
    sri = type("SRI", (), {"rows": rows, "cols": cols})()
    state = nc.build(bundle, {}, _Clock(ts + 8 * 60 * 1000), ts, sri)
    info = state["info"]
    assert info["available"] is True
    assert info["source"] == "DPC_IR_108"
    assert info["channel"].startswith("IR 10.8")
    assert info["resolution_m"] == 1000
    assert info["role"] == "context"
    assert info["not_a_rain_forecast"] is True
    assert "non e' una previsione di pioggia" in info["note"].lower()
    assert info["latency_min"] == 8.0
    assert info["image"] == nc.IMAGE_NAME
    assert state["classes"].max() == 3


def test_build_no_ir_is_null(monkeypatch):
    def _none(*a, **k):
        return [], [], None
    monkeypatch.setattr("radar_engine.fetch.fetch_frames", _none)
    sri = type("SRI", (), {"rows": 10, "cols": 10})()
    state = nc.build(_Bundle(), {}, _Clock(1), 1, sri)
    assert state["info"]["available"] is False
    assert state["info"]["null_reason"] == "ir108_unavailable"
    assert state["info"]["role"] == "context"


def test_build_slot_mismatch_is_null(monkeypatch):
    rows, cols = 10, 10
    ir = _Rd(np.full((rows, cols), -30.0, np.float32),
             np.ones((rows, cols), bool), 1000)
    bundle = _Bundle(ir, 1000)
    monkeypatch.setattr("radar_engine.fetch.fetch_frames",
                        lambda *a, **k: ([ir], [], None))
    sri = type("SRI", (), {"rows": rows, "cols": cols})()
    state = nc.build(bundle, {}, _Clock(2000), 2000, sri)
    assert state["info"]["available"] is False
    assert state["info"]["null_reason"] == "ir108_slot_mismatch"
