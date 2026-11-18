#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dell'archivio ADDITIVO delle predizioni (blob giornalieri, clock simulato).

Copre:
  (a) una riga scritta oggi finisce nel registro corrente E nel blob del giorno;
  (b) dopo 31 giorni il registro corrente ruota, ma l'archivio conserva il
      giorno vecchio;
  (c) nessun valore inventato; righe corrotte contate e scartate come in rotate;
  + dedup, retention 365 giorni, backfill una tantum, no-candidates no-op.

Il clock e' SEMPRE simulato (now=): nessun test dipende dalla data reale.
"""

import datetime as dt
import json
import os

from radar_engine import severe_predictions as sp
from radar_engine import predictions_archive as pa

REC = {
    "structure_id": "CS-2026-10-23T12-cel001",
    "cell_id": "c-1-4",
    "position": [12.5, 41.2],
    "soi": 78,
    "severe_stack": {
        "hail_risk": {
            "vil_density": {"value": 4.2, "null_reason": None},
            "mesh": {"value": None, "null_reason": "cappi_levels<4"},
        },
        "ot": {"value": True, "null_reason": None},
        "environment": {"cape_jkg": {"value": 1500.0}},
    },
}
FIELDS = {"ts", "cell_id", "lat", "lon", "soi", "vil_density", "mesh",
          "ot", "environment", "version"}

DAY1 = dt.datetime(2026, 10, 23, 12, 5, tzinfo=dt.timezone.utc)
DAY32 = DAY1 + dt.timedelta(days=31)          # 2026-11-23
ISO_DAY1 = sp._now_iso(DAY1)
ISO_DAY32 = sp._now_iso(DAY32)


class _Bundle(object):
    pass


def _bundle():
    b = _Bundle()
    b.convective_structures = [REC]
    return b


def _layout(tmp_path):
    out = str(tmp_path / "data" / "radar")
    os.makedirs(out, exist_ok=True)
    return out, sp.default_path(out), sp.default_archive_dir(out)


def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh.read().splitlines() if line]


def _write_lines(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(l + "\n" for l in lines))


# ── (a) riga scritta oggi → registro corrente + archivio del giorno ─────────
def test_a_row_goes_to_both_register_and_archive_day(tmp_path):
    out, reg, arch = _layout(tmp_path)
    res = sp.record_predictions(_bundle(), out, version="1.2.4.102", now=DAY1)

    blob = sp.archive_day_path(arch, ISO_DAY1)
    assert blob.endswith(os.path.join("2026", "10", "23.jsonl"))
    assert os.path.exists(reg) and os.path.exists(blob)
    reg_rows, arch_rows = _read_jsonl(reg), _read_jsonl(blob)
    assert len(reg_rows) == 1 and len(arch_rows) == 1
    assert reg_rows == arch_rows                       # stesse righe, non copie modificate
    assert set(arch_rows[0]) == FIELDS                 # nessun campo inventato
    assert arch_rows[0]["ts"] == ISO_DAY1
    assert res["appended"] == 1
    assert res["archive"]["appended"] == 1
    assert res["archive"]["paths"] == [blob]
    assert res["archive"]["skipped_invalid_ts"] == 0


# ── (b) dopo 31 gg: registro ruota, archivio conserva il giorno vecchio ─────
def test_b_register_rotates_but_archive_keeps_old_day(tmp_path):
    out, reg, arch = _layout(tmp_path)
    sp.record_predictions(_bundle(), out, version="v", now=DAY1)
    res2 = sp.record_predictions(_bundle(), out, version="v", now=DAY32)

    assert res2["rotation"] == {"kept": 0, "dropped_old": 1,
                                "dropped_invalid": 0}
    reg_rows = _read_jsonl(reg)
    assert [r["ts"] for r in reg_rows] == [ISO_DAY32]   # vecchia riga fuori

    old_blob = sp.archive_day_path(arch, ISO_DAY1)
    new_blob = sp.archive_day_path(arch, ISO_DAY32)
    assert os.path.exists(old_blob) and os.path.exists(new_blob)
    assert [r["ts"] for r in _read_jsonl(old_blob)] == [ISO_DAY1]
    assert [r["ts"] for r in _read_jsonl(new_blob)] == [ISO_DAY32]


# ── (c) righe corrotte contate/scartate, valori non inventati ───────────────
def test_c_corrupt_lines_counted_and_no_invented_values(tmp_path):
    arch = str(tmp_path / "data" / "state" / "predictions")
    new = str(tmp_path / "new")
    day = "2026-10-23"
    old_valid = json.dumps(sp.prediction_row(
        REC, REC["severe_stack"], ts="2026-10-23T12:00:00Z", version="v"),
        ensure_ascii=False, sort_keys=True)
    new_valid = json.dumps(sp.prediction_row(
        REC, REC["severe_stack"], ts="2026-10-23T12:10:00Z", version="v"),
        ensure_ascii=False, sort_keys=True)

    _write_lines(pa._day_blob_path(arch, day), ["{not json}", old_valid])
    _write_lines(os.path.join(new, "2026", "10", "23.jsonl"),
                 ["{also not json}", new_valid])

    report = pa.merge_daily(arch, new, now=dt.datetime(
        2026, 10, 23, 13, tzinfo=dt.timezone.utc))

    assert report["dropped_invalid"] == 2
    assert report["days_written"] == 1
    assert report["blobs_alive"] == 1
    rows = _read_jsonl(pa._day_blob_path(arch, day))
    assert {r["ts"] for r in rows} == {"2026-10-23T12:00:00Z",
                                       "2026-10-23T12:10:00Z"}
    assert all(set(r) == FIELDS for r in rows)
    # I valori null restano null (nessuno 0 inventato), ot resta True.
    assert all(r["mesh"] is None and r["ot"] is True for r in rows)
    assert all(r["vil_density"] == 4.2 for r in rows)


# ── dedup identico al registro (old prima, poi new) ─────────────────────────
def test_merge_dedups_exact_lines(tmp_path):
    arch = str(tmp_path / "arch")
    new = str(tmp_path / "new")
    line = json.dumps(sp.prediction_row(
        REC, REC["severe_stack"], ts="2026-10-23T12:00:00Z", version="v"),
        ensure_ascii=False, sort_keys=True)
    _write_lines(pa._day_blob_path(arch, "2026-10-23"), [line])
    _write_lines(os.path.join(new, "2026", "10", "23.jsonl"), [line, line])
    report = pa.merge_daily(arch, new, now=DAY1)
    assert report["rows_added"] == 2
    assert len(_read_jsonl(pa._day_blob_path(arch, "2026-10-23"))) == 1


# ── retention 365 giorni dell'archivio (non 30) ─────────────────────────────
def test_archive_retention_365_days(tmp_path):
    arch = str(tmp_path / "arch")
    new = str(tmp_path / "new-empty")
    mk = lambda day: json.dumps(sp.prediction_row(
        REC, REC["severe_stack"], ts=day + "T12:00:00Z", version="v"),
        ensure_ascii=False, sort_keys=True)
    _write_lines(pa._day_blob_path(arch, "2025-11-17"), [mk("2025-11-17")])
    _write_lines(pa._day_blob_path(arch, "2025-11-18"), [mk("2025-11-18")])
    now = dt.datetime(2026, 11, 18, 12, tzinfo=dt.timezone.utc)
    report = pa.merge_daily(arch, new, now=now, retention_days=365)
    assert report["cutoff_day"] == "2025-11-18"
    assert report["removed_old_days"] == ["2025-11-17"]
    assert report["blobs_alive"] == 1
    assert os.path.exists(pa._day_blob_path(arch, "2025-11-18"))
    assert not os.path.exists(pa._day_blob_path(arch, "2025-11-17"))


# ── backfill una tantum dal registro corrente ───────────────────────────────
def test_backfill_from_register_splits_by_day(tmp_path):
    reg = str(tmp_path / "state" / "severe_predictions.jsonl")
    arch = str(tmp_path / "state" / "predictions")
    sp.append_rows([
        sp.prediction_row(REC, REC["severe_stack"],
                          ts="2026-10-23T12:00:00Z", version="v"),
        sp.prediction_row(REC, REC["severe_stack"],
                          ts="2026-10-24T12:00:00Z", version="v"),
    ], reg)
    report = pa.backfill_from_register(reg, arch, now=DAY32)
    assert report["blobs_alive"] == 2
    assert [r["ts"] for r in _read_jsonl(
        pa._day_blob_path(arch, "2026-10-23"))] == ["2026-10-23T12:00:00Z"]
    assert [r["ts"] for r in _read_jsonl(
        pa._day_blob_path(arch, "2026-10-24"))] == ["2026-10-24T12:00:00Z"]


# ── nessuna riga corrente → nessun blob (nessun file Inventato) ─────────────
def test_no_candidates_creates_no_archive_blob(tmp_path):
    out, reg, arch = _layout(tmp_path)
    b = _Bundle()
    b.convective_structures = []
    assert sp.record_predictions(b, out, version="v", now=DAY1) is None
    assert not os.path.exists(reg)
    assert not os.path.exists(arch)
