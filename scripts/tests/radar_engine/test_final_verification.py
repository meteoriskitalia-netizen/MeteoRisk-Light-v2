#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#2/#4/#5 — verifiche aggiuntive su record reali (nessun dato simulato)."""
import json
from pathlib import Path
import numpy as np
import pytest

from radar_engine.phase2 import aggregate as agg
from radar_engine.convective_structure import derive_confidence
from radar_engine.main import radar_observation_coverage, _RADAR_OBSERVATION_COMPONENTS
from radar_engine.config import CONFIG
from radar_engine import multiscale as ms

# Snapshot REALE congelato del run 2026-11-21T14:46:06Z (sha256
# 0134db20..., 315.111 byte), perche' il dato vivo di data/radar ruota.
REC = (Path(__file__).resolve().parents[1] / "fixtures" /
       "real_1446_convective_structures.json")
FX = (Path(__file__).resolve().parents[1] / "fixtures" / "vmi_real_slots.npz")

def _real(): return json.loads(REC.read_text(encoding="utf-8"))["structures"]

@pytest.mark.parametrize("idx", range(6))
def test_real_record_env_published_but_unweighted(idx):
    s=_real()[idx]
    o=agg.aggregate_ssi_v2(s["soi"],s["hook"],s["structure"],s["env"],s["ot"],s["lightning"])
    # il valore env resta PUBLBLICATO (non cancellato), ma il peso e' 0.0
    assert o["env"] == s["env"]
    assert o["weights_used"]["env"] == 0.0
    assert o["env_excluded_from_score"] is True

@pytest.mark.parametrize("idx", range(6))
def test_real_record_confidence_byte_identical(idx):
    s=_real()[idx]
    gates=s.get("gates") or {}; n=int(s.get("n_points") or 0)
    base={k:s.get(k) for k in _RADAR_OBSERVATION_COMPONENTS}
    cov_a=radar_observation_coverage(dict(base,env=s.get("env")))[0]
    cov_b=radar_observation_coverage(dict(base,env=None))[0]
    ca=derive_confidence(gates=gates,n_points=n,coverage=cov_a)
    cb=derive_confidence(gates=gates,n_points=n,coverage=cov_b)
    assert json.dumps(ca,sort_keys=True)==json.dumps(cb,sort_keys=True)

@pytest.mark.parametrize("idx", range(4))
def test_each_real_frame_has_split_and_nuclei(idx):
    with np.load(FX) as z:
        arr=z["vmi_%d"%idx].astype(float)
    out=ms.segment_multiscale(arr,1.0,cfg={"enabled":True},seed=42)
    dom=max(out["objects"],key=lambda o:o["envelope_area_km2"]["value"])
    assert dom["split_by_watershed"]["value"] is True
    assert dom["n_nuclei"]["value"] >= 2
    assert dom["min_dist_nuclei_km"]["value"] >= 5.0
