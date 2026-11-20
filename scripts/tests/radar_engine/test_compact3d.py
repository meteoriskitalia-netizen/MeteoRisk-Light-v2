#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del percorso di campionamento volumetrico compatto (Iride: solo il sampling).

Ogni test in questo file FALLISCE sul codice pristino (BUILD 164):
  * `radar_engine.compact3d` non esiste;
  * `build_volume_payload(..., compact_side=...)` non e' accettato.
E PASSA sul codice modificato. Nessun dato inventato: la misura usa la fixture
reale `volume_cappi_real.npz` (10x64x64, slot DPC 2026-11-15T16:15Z).
"""
import hashlib
import json
import os

import numpy as np
import pytest

from radar_engine import volume_publisher as vp

TS = "2026-11-15T16:15:00Z"
GEN = "2026-11-16T00:00:00Z"
FIX = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "fixtures",
    "volume_cappi_real.npz"))
BBOX = [27, 36, 21, 41]  # cella reale nella fixture (meta.cell_bbox_local)


class _RD:
    def __init__(self, data):
        self.data = np.asarray(data, dtype=np.float32)
        self.rows, self.cols = self.data.shape


@pytest.fixture(scope="module")
def real():
    z = np.load(FIX)
    return {"volume": z["volume"], "vmi": z["vmi"], "mask": z["interp_mask"]}


def _cell(payload):
    hits = [c for c in payload["cells"] if c["bbox_rc_full"] == BBOX]
    assert len(hits) == 1, [c["bbox_rc_full"] for c in payload["cells"]]
    return hits[0]


def _payload(real, side=None, pad=5, gen=GEN):
    kw = dict(timestamp_utc=TS, include_sections=False,
              generated_utc=gen, pad=pad)
    if side is not None:
        kw["compact_side"] = side
    return vp.build_volume_payload(real["volume"], real["vmi"],
                                   interp_mask=real["mask"], **kw)


def _dump(p):
    return json.dumps(p, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


# --- 1. encoding noto -------------------------------------------------------
def test_encoding_known_values():
    from radar_engine import compact3d as c3
    assert c3.encode_dbz(np.array([-32.0, 15.0, 90.0])).tolist() == [2, 96, 246]
    dec = c3.decode_code(np.array([0, 1, 2, 96, 246], dtype=np.uint8))
    assert np.isnan(dec[0]) and np.isnan(dec[1])
    assert dec[2] == -32.0 and dec[3] == 15.0 and dec[4] == 90.0


def test_cube_shape_levels_and_sentinels():
    from radar_engine import compact3d as c3
    # 10 livelli noti: -9999 -> 0, -9998 -> 1, -32 dBZ -> 2, 15 dBZ -> 96
    cappi = {}
    for k in range(1, 11):
        arr = np.full((40, 40), -9998.0, dtype=np.float32)
        arr[0:1, 0:1] = -9999.0
        arr[1:2, 1:2] = -9998.0
        arr[10:12, 10:12] = -32.0
        arr[20:22, 20:22] = 15.0
        cappi[k] = _RD(arr)
    m = c3.build_compact_cube(cappi=cappi, r0=0, r1=32, c0=0, c1=32, side=32)
    cube = m["cube"]
    assert cube.shape == (10, 32, 32) and cube.dtype == np.uint8
    assert m["pool"] == 1 and m["sampling"] == c3.SAMPLING
    assert "maximum valid reflectivity" in m["sampling"]
    assert m["reference"]
    # tutti e 10 i livelli presenti e con la codifica attesa
    for k in range(10):
        assert cube[k, 0, 0] == 0            # -9999 -> missing
        assert cube[k, 1, 1] == 1            # -9998 -> no echo
        assert cube[k, 10, 10] == 2          # -32 dBZ
        assert cube[k, 20, 20] == 96         # 15 dBZ
        assert cube[k, 30, 30] == 1          # resto: no echo


# --- 2. parametri identici alla griglia piena su CAPPI reali ---------------
def test_real_frame_parameters_identical(real):
    full = _cell(_payload(real))
    comp = _cell(_payload(real, side=32))
    for name in ("MAX_REFLECTIVITY", "MEAN_REFLECTIVITY", "MAX_ALTITUDE",
                 "VIL", "VIL_MEAN_OVER_CELL", "VIL_DENSITY", "ECHO_TOP_18"):
        assert comp[name]["value"] == full[name]["value"], name
    for t in (30, 40, 45, 50, 55):
        for name in ("CELL_VOLUME", "ECHO_TOP", "VERTICAL_EXTENT"):
            a = full["by_threshold"][str(t)][name]["value"]
            b = comp["by_threshold"][str(t)][name]["value"]
            assert b == a, (t, name, a, b)
    # i 5 threshold e il contratto dei campi restano invariati
    assert sorted(comp["by_threshold"], key=int) == ["30", "40", "45", "50", "55"]


def test_cube_embedded_in_payload(real):
    comp = _cell(_payload(real, side=32))
    cm = comp["compact_cube"]
    assert cm["available"] is True
    assert cm["shape"] == [10, 32, 32] and cm["dtype"] == "uint8"
    assert cm["sampling"] == "maximum valid reflectivity within horizontal bins"
    assert cm["side"] == 32 and cm["pool"] == 1
    from radar_engine import compact3d as c3
    cube = c3.decode_b64_to_cube(cm["data_base64"], 32)
    assert cube.shape == (10, 32, 32)
    assert int(cube.sum()) > 0


# --- 3. promozione a SIDE=64 misurata --------------------------------------
def test_promotion_when_window_exceeds_side(real):
    comp = _cell(_payload(real, side=32, pad=40))
    cm = comp["compact_cube"]
    assert cm["side"] == 64, cm
    assert cm["requested_side"] == 32 and cm["side_promoted"] is True
    assert cm["pool"] == 1  # binomiale: max pooling non applicato
    full = _cell(_payload(real, pad=40))
    assert comp["by_threshold"]["30"]["CELL_VOLUME"]["value"] == \
           full["by_threshold"]["30"]["CELL_VOLUME"]["value"]
    assert comp["VIL"]["value"] == full["VIL"]["value"]


# --- 4. determinismo e onesta' ---------------------------------------------
def test_determinism(real):
    p1 = _payload(real, side=32)
    p2 = _payload(real, side=32)
    assert hashlib.sha256(_dump(p1)).hexdigest() == \
           hashlib.sha256(_dump(p2)).hexdigest()


def test_floor_not_a_physical_measure(real):
    p = _payload(real, side=32)
    cs = p["compact_sampling"]
    assert cs["enabled"] is True
    assert cs["floor"] == "NON pubblicato come misura fisica"
    assert "artefatto visivo" in cs["floor_note"]
    assert "non echo top" in cs["floor_note"]
    assert "floor" not in _cell(p)  # nessun campo 'floor' tra i parametri


# --- 5. degradazione e branch invariati ------------------------------------
def test_null_reason_cappi_levels_lt_4():
    ref = _RD(np.zeros((40, 40)))
    vmi = _RD(np.zeros((40, 40)))
    cappi = {k: _RD(np.full((40, 40), 40.0)) for k in range(1, 4)}  # 3 livelli
    p = vp.build_and_publish(cappi, ref, vmi,
                             timestamp_utc=TS, compact_side=32)
    assert p["null_reason"] == "cappi_levels_lt_4"
    assert p["volume_available"] is False
    # con 4 livelli il percorso compatto deve essere dichiarato nel payload
    cappi[4] = _RD(np.full((40, 40), 40.0))
    ok = vp.build_and_publish(cappi, ref, vmi,
                              timestamp_utc=TS, compact_side=32)
    assert ok["compact_sampling"]["enabled"] is True
