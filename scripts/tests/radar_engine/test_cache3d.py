#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cache 3D a dimensione costante: branch orphan radar-3d, un solo blob.

Nessun dato simulato su disco: i payload sono costruiti in memoria da array
dichiarati nel test; l'unica scrittura e' in tmp_path. Verifica il growth dal
blob compatto precedente (difetto misurato: `prev=None` in produzione) e la
struttura del workflow (orphan commit, force-with-lease, no-op, concurrency).
"""
from __future__ import annotations

import json
import os
import pathlib

import numpy as np

from radar_engine import morphology as morph
from radar_engine import volume_publisher as vp

TS0 = "2026-11-17T12:00:00Z"
TS1 = "2026-11-17T12:10:00Z"


def _volume(n_levels):
    """Array 10x40x40 dichiarato: una cella 5x5, `n_levels` livelli >=30 dBZ."""
    vol = np.full((10, 40, 40), np.nan, dtype=np.float32)
    for z in range(n_levels):
        vol[z, 18:23, 18:23] = float(45 - 2 * z)
    vmi = np.full((40, 40), np.nan, dtype=np.float32)
    vmi[18:23, 18:23] = 40.0
    return vol, vmi


def _payload(n_levels, ts):
    vol, vmi = _volume(n_levels)
    return vp.build_volume_payload(
        vol, vmi, timestamp_utc=ts, thresholds=(30, 40, 45, 50, 55),
        include_sections=False)


# ---------------------------------------------------------------------------
# 1. blob precedente: loader onesto
# ---------------------------------------------------------------------------
def test_load_previous_volume_missing_and_valid(tmp_path):
    missing = tmp_path / "3d" / "volume.json"
    payload, reason = vp.load_previous_volume(str(missing))
    assert payload is None
    assert reason == "no_previous_volume"

    good = _payload(4, TS0)
    missing.parent.mkdir(parents=True)
    missing.write_text(json.dumps(good), encoding="utf-8")
    payload, reason = vp.load_previous_volume(str(missing))
    assert reason is None
    assert payload["volume_available"] is True


def test_load_previous_volume_degraded_is_not_used(tmp_path):
    path = tmp_path / "volume.json"
    path.write_text(json.dumps({"volume_available": False,
                                "null_reason": "cappi_levels_lt_4"}),
                    encoding="utf-8")
    payload, reason = vp.load_previous_volume(str(path))
    assert payload is None
    assert reason == "previous_volume_unavailable"


# ---------------------------------------------------------------------------
# 2. growth dal payload compatto (unico blob vivo)
# ---------------------------------------------------------------------------
def test_growth_from_compact_previous_payload():
    prev = _payload(4, TS0)          # 4 livelli >=30 dBZ
    now = _payload(6, TS1)           # 6 livelli: volume cresciuto
    vp.growth_from_previous_payload(now, prev)

    prev_f = prev["cells"][0]["by_threshold"]["30"]
    now_f = now["cells"][0]["by_threshold"]["30"]
    want_v = now_f["CELL_VOLUME"]["value"] - prev_f["CELL_VOLUME"]["value"]
    want_e = now_f["ECHO_TOP"]["value"] - prev_f["ECHO_TOP"]["value"]
    assert want_v > 0 and want_e > 0
    vol_growth = now_f["VOLUME_GROWTH"]
    assert vol_growth["value"] == want_v
    assert vol_growth["null_reason"] is None
    assert vol_growth["detail"]["matched_prev_cell_id"] is not None
    assert vol_growth["detail"]["interval_min"] == 10.0
    assert now_f["VERTICAL_GROWTH"]["value"] == want_e
    assert now["growth"]["available"] is True
    assert now["growth"]["matched_cells"] == 1


def test_growth_null_when_previous_absent():
    now = _payload(6, TS1)
    vp.growth_from_previous_payload(now, None)
    field = now["cells"][0]["by_threshold"]["30"]["VOLUME_GROWTH"]
    assert field["value"] is None
    assert field["null_reason"] == "no_previous_volume"
    assert now["growth"]["available"] is False


def test_growth_field_contract_with_partial_previous_values():
    """Un campo valorizzato non porta mai un null_reason (contratto _field)."""
    import copy
    prev = _payload(4, TS0)
    now = _payload(6, TS1)
    partial = copy.deepcopy(prev)
    f = partial["cells"][0]["by_threshold"]["30"]["CELL_VOLUME"]
    f["value"] = None
    f["null_reason"] = "test_missing_in_previous"
    vp.growth_from_previous_payload(now, partial)
    fields = now["cells"][0]["by_threshold"]["30"]
    vg = fields["VERTICAL_GROWTH"]
    vog = fields["VOLUME_GROWTH"]
    assert vg["value"] is not None and vg["null_reason"] is None
    assert vog["value"] is None
    assert vog["null_reason"] == "cell_volume_missing_in_current_or_previous"


def test_build_volume_payload_accepts_compact_prev():
    """La forma compatta di prev deve funzionare anche nella API principale."""
    prev = _payload(4, TS0)
    now = _payload(6, TS1)
    vol, vmi = _volume(6)
    rebuilt = vp.build_volume_payload(
        vol, vmi, timestamp_utc=TS1, prev=prev, include_sections=False)
    want = (now["cells"][0]["by_threshold"]["30"]["CELL_VOLUME"]["value"]
            - prev["cells"][0]["by_threshold"]["30"]["CELL_VOLUME"]["value"])
    assert rebuilt["cells"][0]["by_threshold"]["30"]["VOLUME_GROWTH"]["value"] == want


def test_growth_for_cell_does_not_mutate_cell():
    prev = _payload(4, TS0)
    now = _payload(6, TS1)
    cell = now["cells"][0]
    before = json.dumps(cell["by_threshold"]["30"]["VOLUME_GROWTH"], sort_keys=True)
    value, reason, detail = vp.growth_for_cell(cell, prev, thr_dbz=30)
    assert value > 0 and reason is None and detail is not None
    assert json.dumps(cell["by_threshold"]["30"]["VOLUME_GROWTH"],
                      sort_keys=True) == before


# ---------------------------------------------------------------------------
# 3. morphologia: growth come EVIDENZA, non gate
# ---------------------------------------------------------------------------
def test_morphology_growth_from_previous_does_not_change_classification():
    prev = _payload(4, TS0)
    now = _payload(6, TS1)
    structure = {"structure_id": "CS-test", "cell_id": now["cells"][0]["cell_id"],
                 "position": [], "soi": 50, "rotation_measurable": False}
    without = morph.derive_morphology(structure, now)
    with_prev = morph.derive_morphology(structure, now, prev=prev)
    assert with_prev["classification"] == without["classification"]
    assert with_prev["gates"] == without["gates"]
    assert with_prev["values"]["volume_growth"] > 0
    assert with_prev["value_null_reasons"]["volume_growth"] is None
    assert with_prev["detail"]["growth_source"] == "previous_compact_payload"


def test_morphology_attach_passes_previous():
    prev = _payload(4, TS0)
    now = _payload(6, TS1)
    rec = {"structure_id": "CS-test", "cell_id": now["cells"][0]["cell_id"],
           "position": []}
    records, summary = morph.attach(now, [rec], prev=prev)
    assert records[0]["morphology_organization"]["values"]["volume_growth"] > 0
    assert summary["growth_previous_available"] is True
    assert summary["growth_values_present"] == 1


# ---------------------------------------------------------------------------
# 4. wiring statico (motore + workflow)
# ---------------------------------------------------------------------------
def test_main_wiring_reads_previous_blob():
    root = pathlib.Path(morph.__file__).resolve().parents[2]
    main_src = (root / "scripts" / "radar_engine" / "main.py").read_text(
        encoding="utf-8")
    assert "def _previous_volume_payload(bundle, config=None):" in main_src
    assert "vpub.VOLUME_BLOB_PATH" in main_src
    assert "prev=prev_payload, motions={})" in main_src
    assert "cfg=mcfg, prev=prev_payload)" in main_src
    assert "write_volume_payload(payload, os.path.join(out_dir, \"3d\"))" in main_src
    out_src = (root / "scripts" / "radar_engine" / "output.py").read_text(
        encoding="utf-8")
    assert '"data/radar/3d/volume.json"' in out_src
    assert '"branch": "radar-3d"' in out_src


def test_workflow_has_separate_orphan_3d_cache():
    root = pathlib.Path(morph.__file__).resolve().parents[2]
    wf = (root / ".github" / "workflows" / "radar-engine.yml").read_text(
        encoding="utf-8")
    # concurrency separata (non condivisa con radar-history)
    assert "group: radar-3d" in wf
    assert "group: radar-history" in wf
    i3d = wf.index("group: radar-3d")
    ihist = wf.index("group: radar-history")
    assert i3d != ihist
    # un solo blob vivo e nessun dato grezzo
    assert "git add -f data/radar/3d/volume.json" in wf
    for forbidden in (".tif", ".npz"):
        assert forbidden not in wf
    # no-op e force-with-lease con 3 tentativi
    assert 'if [ "$OLD_TREE" = "$NEW_TREE" ]; then' in wf
    assert "commit-tree \"$NEW_TREE\"" in wf
    assert "for attempt in 1 2 3; do" in wf
    assert "--force-with-lease=refs/heads/radar-3d" in wf
    # main non riceve piu' il volume
    assert "git rm -q --cached --ignore-unmatch data/radar/volume.json" in wf
    main_commit = wf[wf.index("for f in latest.json"):
                     wf.index("done", wf.index("for f in latest.json"))]
    assert "volume.json" not in main_commit
