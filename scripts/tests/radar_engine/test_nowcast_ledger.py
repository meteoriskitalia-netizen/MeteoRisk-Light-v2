# -*- coding: utf-8 -*-
"""Test del ledger compatto della verifica (`nowcast_ledger.py`).

Nessun accesso di rete: i raster sono sintetici ma con una distribuzione
realistica (lognormale, molti zeri, dominio irregolare).
"""

import datetime as _dt
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from radar_engine import nowcast_field as nf          # noqa: E402
from radar_engine import nowcast_ledger as nl         # noqa: E402

STEP_MS = 5 * 60 * 1000


def _scene(seed=7, h=90, w=120):
    rng = np.random.default_rng(seed)
    obs = np.where(rng.random((h, w)) < 0.82, 0.0,
                   rng.lognormal(mean=0.2, sigma=1.1, size=(h, w)))
    # previsto: osservato traslato di 3 px in x + rumore, come un nowcast
    pred = np.roll(obs, 3, axis=1).copy()
    pred *= rng.uniform(0.6, 1.6, size=(h, w))
    dom = np.zeros((h, w), dtype=bool)
    dom[4:h - 4, 5:w - 5] = True
    dom &= ~((np.arange(h)[:, None] - h // 2) ** 2
             + (np.arange(w)[None, :] - w // 2) ** 2 < 25)
    return pred.astype(np.float32), obs.astype(np.float32), dom


def _slot_ms(iso="2026-11-23T06:25:00Z"):
    return int(_dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
               .timestamp() * 1000)


def _one_lead_record(seed=7, lead=15, slot_ms=None):
    pred, obs, dom = _scene(seed)
    slot_ms = slot_ms if slot_ms is not None else _slot_ms()
    return nl.build_record(
        slot_ms, {lead: pred}, {lead: obs}, {lead: dom},
        {lead: slot_ms + lead * 60000},
        generated_ms=slot_ms + 11 * 60000), pred, obs, dom


# ---------------------------------------------------------------------------
# formato
# ---------------------------------------------------------------------------
def test_record_is_1102_bytes():
    rec, _p, _o, _d = _one_lead_record()
    blob = nl.pack(rec)
    assert nl.RECORD_BYTES == 1102
    assert len(blob) == 1102
    # somma dichiarata delle sezioni
    assert (nl.HEADER_BYTES + nl.LEAD_BYTES + nl.COUNTER_BYTES
            + nl.FSS_BYTES + nl.HIST_BYTES) == 1102


def test_pack_unpack_exact_roundtrip():
    rec, _p, _o, _d = _one_lead_record()
    back = nl.unpack(nl.pack(rec))
    assert back["slot_ms"] == rec["slot_ms"]
    assert back["generated_ms"] == rec["generated_ms"]
    assert (back["grid_w"], back["grid_h"]) == (rec["grid_w"], rec["grid_h"])
    for lead in nl.LEADS:
        assert back["leads"][lead]["present"] == (
            rec["leads"][lead]["present"])
        assert back["counters"][lead] == rec["counters"][lead]
        np.testing.assert_array_equal(back["hist"][lead],
                                      rec["hist"][lead])
        for t in nl.THRESHOLDS:
            for side in nl.NEIGHBORHOODS:
                a = back["fss"][lead][t][side]
                b = rec["fss"][lead][t][side]
                if np.isnan(b):
                    assert np.isnan(a)
                else:
                    assert a == np.float32(b)
    # il record ripacchettato e' byte-identico
    assert nl.pack(back) == nl.pack(rec)


def test_header_declares_license_and_source():
    rec, _p, _o, _d = _one_lead_record()
    back = nl.unpack(nl.pack(rec))
    assert back["flags"] & nl.FLAG_LICENSE_CC_BY_SA
    assert back["flags"] & nl.FLAG_SOURCE_RADAR_DPC
    assert back["license"] == "CC BY-SA 4.0"
    assert "Radar-DPC" in back["source"]


def test_bad_size_and_magic_rejected():
    with pytest.raises(ValueError):
        nl.unpack(b"\x00" * 10)
    blob = bytearray(nl.pack(_one_lead_record()[0]))
    blob[0:4] = b"XXXX"
    with pytest.raises(ValueError):
        nl.unpack(bytes(blob))


# ---------------------------------------------------------------------------
# istogramma -> contingenza
# ---------------------------------------------------------------------------
def test_edges_05_2_5_are_exact():
    for t in (0.5, 2.0, 5.0):
        assert nl.threshold_edge_index(t) is not None
    assert nl.threshold_edge_index(3.0) is None
    assert nl.threshold_edge_index(0.1) is not None


def test_ninety_five_percent_of_threshold_rows_reconstruct():
    """Tutte le soglie di bordo: istogramma == calcolo diretto sul raster."""
    pred, obs, dom = _scene(seed=11)
    hist = nl.joint_histogram(pred, obs, dom)
    # 7 bordi finiti dell'istogramma (tutti ricalcolabili senza perdita)
    for t in nl.HIST_EDGES[1:-1]:
        got = nl.contingency_from_hist(hist, t)
        direct = nf.contingency(pred, obs, dom, t)
        assert got["exact"] is True
        assert got["counts"] == tuple(int(x) for x in direct), (
            "soglia %r: istogramma %r != diretto %r"
            % (t, got["counts"], direct))
        sc = got["scores"]
        assert sc["hits"] == int(direct[0])


def test_non_edge_threshold_returns_interval_not_a_number():
    pred, obs, dom = _scene(seed=13)
    hist = nl.joint_histogram(pred, obs, dom)
    got = nl.contingency_from_hist(hist, 3.0)
    assert got["exact"] is False
    assert got["counts"] is None
    assert got["scores"] is None
    assert got["bounds"] is not None
    for key, (lo, hi) in got["bounds"].items():
        assert 0 <= lo <= hi, (key, lo, hi)
    # il valore diretto cade DENTRO l'intervallo dichiarato
    direct = nf.contingency(pred, obs, dom, 3.0)
    names = ("hits", "misses", "false_alarms", "correct_negatives")
    for name, value in zip(names, direct):
        lo, hi = got["bounds"][name]
        assert lo <= int(value) <= hi, (name, value, lo, hi)
    # e l'intervallo e' stretto solo se il bin e' vuoto
    assert got["bin_range_mmh"] == [2.0, 5.0]


def test_histogram_totals_equal_domain_pixels():
    pred, obs, dom = _scene(seed=17)
    ok = (np.isfinite(pred) & np.isfinite(obs) & dom)
    hist = nl.joint_histogram(pred, obs, dom)
    assert int(hist.sum()) == int(ok.sum())


# ---------------------------------------------------------------------------
# FSS
# ---------------------------------------------------------------------------
def test_stored_fss_equals_raster_calculation():
    rec, pred, obs, dom = _one_lead_record(seed=19)
    back = nl.unpack(nl.pack(rec))
    for t in nl.THRESHOLDS:
        for side in nl.NEIGHBORHOODS:
            direct = nl.fss_from_raster(pred >= t, obs >= t, dom, side)
            stored = back["fss"][15][t][side]
            if direct is None:
                assert np.isnan(stored)
            else:
                assert stored == np.float32(direct)
                assert -1.0 <= stored <= 1.0


def test_fss_identity_is_one_and_shift_is_lower():
    # campo STRUTTURATO (due nuclei), non rumore indipendente: su un campo
    # casuale FSS resta alto anche senza sovrapposizione, e il test non
    # distinguerebbe un FSS corretto da uno costante.
    y, x = np.mgrid[0:120, 0:120]
    obs = np.zeros((120, 120), dtype=np.float32)
    for cy, cx in ((35, 40), (80, 75)):
        obs += 8.0 * np.exp(-(((y - cy) ** 2 + (x - cx) ** 2) / 200.0))
    dom = np.ones_like(obs, dtype=bool)
    ident = nl.fss_from_raster(obs >= 0.5, obs >= 0.5, dom, 9)
    assert ident == pytest.approx(1.0)
    shifted = np.roll(obs, 15, axis=1)
    assert nl.fss_from_raster(shifted >= 0.5, obs >= 0.5, dom, 9) < 0.99


# ---------------------------------------------------------------------------
# archivio giornaliero
# ---------------------------------------------------------------------------
def test_append_merge_and_read_day(tmp_path):
    root = str(tmp_path / "nowcast_ledger")
    slot = _slot_ms()
    pred, obs, dom = _scene(seed=29)
    out1 = nl.append_slot(root, slot, {15: pred}, {15: obs}, {15: dom},
                          {15: slot + 15 * 60000},
                          generated_ms=slot + 600000)
    assert os.path.isfile(out1["path"])
    out2 = nl.append_slot(root, slot, {30: pred}, {30: obs}, {30: dom},
                          {30: slot + 30 * 60000},
                          generated_ms=slot + 1200000)
    assert out2["merged"] is True
    records = nl.read_day(root, "2026-11-23")
    assert len(records) == 1
    rec = records[0]
    assert rec["leads"][15]["present"] is True
    assert rec["leads"][30]["present"] is True
    assert rec["leads"][60]["present"] is False
    # il lead 15 del primo append NON e' stato azzerato dal secondo
    assert int(rec["hist"][15].sum()) > 0
    assert int(rec["hist"][30].sum()) > 0
    assert int(rec["hist"][60].sum()) == 0


def test_archive_grows_one_file_per_day_and_no_pruning(tmp_path):
    root = str(tmp_path / "nowcast_ledger")
    pred, obs, dom = _scene(seed=31)
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        slot = _slot_ms(day + "T06:25:00Z")
        nl.append_slot(root, slot, {15: pred}, {15: obs}, {15: dom},
                       {15: slot + 900000}, generated_ms=slot + 600000)
    files = sorted(f for f in os.listdir(os.path.join(root, "2026", "11")))
    assert files == ["01.bin.zst", "02.bin.zst", "03.bin.zst"] or files == [
        "01.bin.gz", "02.bin.gz", "03.bin.gz"]
    # nessuna potatura: tutti e tre i giorni restano
    assert len(list(nl.iter_records(root))) == 3


def test_compression_roundtrip_and_ratio():
    rec, _p, _o, _d = _one_lead_record(seed=37)
    raw = nl.pack(rec) * 12
    blob, codec = nl.compress(raw)
    assert codec in ("zst", "gz")
    assert nl.decompress(blob) == raw
    assert len(blob) < len(raw)
