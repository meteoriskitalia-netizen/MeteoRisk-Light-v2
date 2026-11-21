#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#2 — stabilita' della separazione dei nuclei su 4 FRAME REALI DPC.

Frame scaricati dall'API pubblica DPC (radar-api.protezionecivile.it),
slot 2026-11-21 14:20/14:30/14:40/14:50Z, GeoTIFF VMI float32 reali
(licenza CC-BY-SA 4.0, attribuzione DPC). Nessun frame simulato.
Sul codice PRISTINO `multi_frame_stability` non esiste -> ImportError.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from radar_engine import multiscale as ms

_FX = (Path(__file__).resolve().parents[1] / "fixtures" / "vmi_real_slots.npz")


@pytest.fixture(scope="module")
def per_frame():
    out = []
    with np.load(_FX) as z:
        for k in sorted(k for k in z.files if k.startswith("vmi_")):
            out.append(ms.segment_multiscale(z[k].astype(float), 1.0,
                                             cfg={"enabled": True}, seed=42))
    return out


def test_four_real_distinct_frames(per_frame):
    meta = json.loads(str(np.load(_FX)["meta"]))
    isos = [m["iso"] for m in meta]
    assert len(set(isos)) == 4


def test_stability_measured_on_real_frames(per_frame):
    st = ms.multi_frame_stability(per_frame)
    assert st["n_frames"]["value"] == 4
    assert st["nuclei_count_min"]["value"] == 2
    assert st["nuclei_count_max"]["value"] == 3
    assert 2.0 <= st["nuclei_count_mean"]["value"] <= 3.0
    assert st["prominence_min_dbz"]["value"] == pytest.approx(12.0, abs=0.1)
    assert st["prominence_max_dbz"]["value"] == pytest.approx(17.5, abs=0.1)
    assert st["min_dist_min_km"]["value"] >= 5.0
    assert st["min_dist_max_km"]["value"] >= st["min_dist_min_km"]["value"]
    assert st["envelope_area_min_km2"]["value"] > 0
    assert st["split_rate"]["value"] == 1.0  # 4/4 frame con watershed reale


def test_insufficient_frames_is_null_with_reason(per_frame):
    st = ms.multi_frame_stability(per_frame[:1], min_frames=3)
    assert st["null_reason"] == "insufficient_frames"
    assert st["nuclei_count_min"]["value"] is None
    assert st["nuclei_count_min"]["null_reason"] == "insufficient_frames"
