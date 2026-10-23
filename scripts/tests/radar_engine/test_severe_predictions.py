#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del registro delle predizioni (1.2.4.17): JSONL append + rotazione 30 gg.

Il registro NON contiene etichette: serve a calcolare POD/FAR quando ci sara'
una verita' (ESWD QC1). Questi test verificano il FORMATO e la ritenzione, non
un'accuratezza che non esiste.
"""

import datetime as dt
import json
import os

from radar_engine import severe_predictions as sp


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


def test_prediction_row_contract():
    row = sp.prediction_row(REC, REC["severe_stack"], ts="2026-10-23T12:05:00Z",
                            version="1.2.4.17")
    assert set(row) == {"ts", "cell_id", "lat", "lon", "soi", "vil_density",
                        "mesh", "ot", "environment", "version"}
    assert row["ts"] == "2026-10-23T12:05:00Z"
    assert row["cell_id"] == "c-1-4"
    assert (row["lat"], row["lon"]) == (41.2, 12.5)
    assert row["vil_density"] == 4.2
    assert row["mesh"] is None                 # null resta null, nessuno 0
    assert row["ot"] is True
    assert row["version"] == "1.2.4.17"


def test_prediction_row_without_position_is_skipped():
    rec = dict(REC)
    rec.pop("position")
    assert sp.prediction_row(rec, None) is None


def test_append_rows_writes_one_json_per_line(tmp_path):
    path = str(tmp_path / "state" / "severe_predictions.jsonl")
    rows = [sp.prediction_row(REC, REC["severe_stack"], ts="2026-10-23T12:00:00Z",
                              version="1.2.4.17") for _ in range(2)]
    assert sp.append_rows(rows, path) == 2
    with open(path, encoding="utf-8") as fh:
        lines = [l for l in fh.read().splitlines() if l]
    assert len(lines) == 2
    assert all(isinstance(json.loads(l), dict) for l in lines)


def test_append_rows_empty_is_noop(tmp_path):
    path = str(tmp_path / "severe_predictions.jsonl")
    assert sp.append_rows([], path) == 0
    assert not os.path.exists(path)


def test_rotate_drops_rows_older_than_30_days(tmp_path):
    path = str(tmp_path / "severe_predictions.jsonl")
    old = dt.datetime(2026, 8, 1, 12, tzinfo=dt.timezone.utc)
    new = dt.datetime(2026, 10, 23, 12, tzinfo=dt.timezone.utc)
    sp.append_rows([
        sp.prediction_row(REC, REC["severe_stack"],
                          ts=old.strftime("%Y-%m-%dT%H:%M:%SZ"), version="v"),
        sp.prediction_row(REC, REC["severe_stack"],
                          ts=new.strftime("%Y-%m-%dT%H:%M:%SZ"), version="v"),
    ], path)
    res = sp.rotate(path, retention_days=30, now=new)
    assert res == {"kept": 1, "dropped_old": 1, "dropped_invalid": 0}
    with open(path, encoding="utf-8") as fh:
        kept = [json.loads(l) for l in fh.read().splitlines() if l]
    assert kept[0]["ts"] == "2026-10-23T12:00:00Z"


def test_rotate_counts_corrupt_lines(tmp_path):
    path = str(tmp_path / "severe_predictions.jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("{not json}\n")
        fh.write(json.dumps({"ts": "2026-10-23T12:00:00Z"}) + "\n")
    res = sp.rotate(path, retention_days=30,
                    now=dt.datetime(2026, 10, 23, 13, tzinfo=dt.timezone.utc))
    assert res["dropped_invalid"] == 1
    assert res["kept"] == 1


def test_rotate_missing_file_is_zero(tmp_path):
    res = sp.rotate(str(tmp_path / "nope.jsonl"))
    assert res == {"kept": 0, "dropped_old": 0, "dropped_invalid": 0}


def test_default_path_is_data_state_sibling_of_radar(tmp_path):
    radar = str(tmp_path / "data" / "radar")
    os.makedirs(radar)
    assert sp.default_path(radar) == os.path.join(
        str(tmp_path), "data", "state", "severe_predictions.jsonl")


class _Bundle(object):
    pass


def test_record_predictions_appends_bundle_records(tmp_path):
    bundle = _Bundle()
    bundle.convective_structures = [REC]
    out_dir = str(tmp_path / "data" / "radar")
    os.makedirs(out_dir)
    res = sp.record_predictions(
        bundle, out_dir, version="1.2.4.17",
        now=dt.datetime(2026, 10, 23, 12, 5, tzinfo=dt.timezone.utc))
    assert res["appended"] == 1
    assert res["path"] == os.path.join(str(tmp_path), "data", "state",
                                       "severe_predictions.jsonl")
    with open(res["path"], encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    assert row["version"] == "1.2.4.17"
    assert row["ts"] == "2026-10-23T12:05:00Z"


def test_record_predictions_without_records_does_not_create_file(tmp_path):
    bundle = _Bundle()
    bundle.convective_structures = []
    out_dir = str(tmp_path / "data" / "radar")
    os.makedirs(out_dir)
    res = sp.record_predictions(bundle, out_dir, version="v")
    assert res is None
    assert not os.path.exists(sp.default_path(out_dir))
