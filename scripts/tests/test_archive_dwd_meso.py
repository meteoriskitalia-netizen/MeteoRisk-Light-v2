#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dell'archiviatore dei mesocicloni DWD (``scripts/validation/archive_dwd_meso.py``).

Le fixture sono file DWD REALI (vedi ``fixtures/README.md``):

  - ``meso_20210708_1040.xml``: 4 ``<event>`` del 2021-07-08 10:40 UTC;
  - ``meso_20210706_2210.xml``: 173 byte, nessuna detection.

Copertura richiesta: parser su XML reale, dedup per ``(time, lat, lon)``,
file vuoto -> 0 righe, campi numerici opzionali mancanti -> ``null`` con
motivo, scrittura incrementale idempotente del JSONL e stabilita' della meta.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "validation"))

import archive_dwd_meso as adm  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
REAL = os.path.join(FIXTURES, "meso_20210708_1040.xml")
EMPTY = os.path.join(FIXTURES, "meso_20210706_2210.xml")
ARCHIVED_AT = "2026-11-10T16:30:00Z"


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _parse_real():
    return adm.parse_meso_xml(_read(REAL), "meso_20210708_1040.xml", ARCHIVED_AT)


# ---------------------------------------------------------------------------
# parser su XML reale
# ---------------------------------------------------------------------------

def test_real_file_is_unchanged_fixture():
    """Guardia: la fixture deve restare il file DWD reale (9073 byte)."""
    assert os.path.getsize(REAL) == 9073
    assert os.path.getsize(EMPTY) == 173


def test_real_file_station_list():
    stations, records, stats = _parse_real()
    assert len(records) == 4
    assert stats.events == 4
    assert stats.dropped == 0
    assert stations[0] == "ASB"
    assert stations[-1] == "MEM"
    assert len(stations) == 17
    assert records[0]["radar_stations"] == stations


def test_real_file_first_event_values():
    _stations, records, _stats = _parse_real()
    rec = records[0]
    assert rec["time"] == "2021-07-08T10:40:00Z"
    assert rec["event_id"] == 24
    assert rec["lat"] == pytest.approx(49.723716)
    assert rec["lon"] == pytest.approx(12.113914)
    assert rec["shear_mean"] == pytest.approx(3.131289)
    assert rec["shear_max"] == pytest.approx(10.565076)
    assert rec["diameter_km"] == pytest.approx(8.322812)
    assert rec["diameter_equivalent_km"] == pytest.approx(5.320551)
    assert rec["top_km"] == pytest.approx(9.079971)
    assert rec["base_km"] == pytest.approx(1.987825)
    assert rec["echotop_km"] == pytest.approx(9.639451)
    assert rec["vil"] == pytest.approx(19.938612)
    assert rec["shear_vectors"] == 15
    assert rec["shear_features"] == 3
    assert rec["mean_dbz"] == pytest.approx(43.68)
    assert rec["max_dbz"] == pytest.approx(57.26)
    assert rec["velocity_max"] == pytest.approx(22.305668)
    assert rec["velocity_rotational_max"] == pytest.approx(14.114323)
    assert rec["velocity_rotational_mean"] == pytest.approx(7.978281)
    assert rec["velocity_rotational_max_closest_to_ground"] == pytest.approx(14.114323)
    assert rec["intensity"] == 3
    assert rec["elevations"] == [{"site": "EIS", "deg": [3.5, 8.0, 17.0]}]
    assert rec["source_file"] == "meso_20210708_1040.xml"
    assert rec["archived_at"] == ARCHIVED_AT
    assert "null_reasons" not in rec


def test_real_file_all_events_share_the_slot_time():
    _stations, records, _stats = _parse_real()
    assert {r["time"] for r in records} == {"2021-07-08T10:40:00Z"}
    assert [r["event_id"] for r in records] == [24, 23, 31, 54]
    assert [r["intensity"] for r in records] == [3, 3, 4, 2]


def test_real_empty_file_has_zero_rows():
    stations, records, stats = _parse_meso(EMPTY)
    assert records == []
    assert stats.events == 0
    assert stats.dropped == 0
    assert len(stations) == 17


def _parse_meso(path):
    return adm.parse_meso_xml(_read(path), os.path.basename(path), ARCHIVED_AT)


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------

def test_dedup_same_event_twice():
    _s, records, _st = _parse_real()
    keys = set()
    first, dup_first = adm.split_new(records, keys)
    second, dup_second = adm.split_new(records, keys)
    assert len(first) == 4
    assert dup_first == 0
    assert second == []
    assert dup_second == 4
    assert len(keys) == 4


def test_dedup_within_the_same_batch():
    _s, records, _st = _parse_real()
    doubled = records + [dict(r) for r in records]
    fresh, duplicated = adm.split_new(doubled, set())
    assert len(fresh) == 4
    assert duplicated == 4


def test_event_key_rounds_to_six_decimals():
    a = {"time": "2021-07-08T10:40:00Z", "lat": 49.7237161, "lon": 12.1139144}
    b = {"time": "2021-07-08T10:40:00Z", "lat": 49.7237164, "lon": 12.1139136}
    assert adm.event_key(a) == adm.event_key(b)


# ---------------------------------------------------------------------------
# campi opzionali mancanti / invalidi
# ---------------------------------------------------------------------------

def test_missing_optional_field_is_null_with_reason():
    xml = _read(REAL).decode("utf-8")
    xml = xml.replace(
        '      <mesocyclone_vil units="kg/m*m">19.938612</mesocyclone_vil>\n', ""
    )
    _s, records, _st = adm.parse_meso_xml(xml, "meso_20210708_1040.xml", ARCHIVED_AT)
    rec = records[0]
    assert rec["vil"] is None
    assert rec["null_reasons"]["vil"] == "missing"
    # gli altri campi restano letti
    assert rec["shear_max"] == pytest.approx(10.565076)


def test_invalid_numeric_field_is_null_with_reason():
    xml = _read(REAL).decode("utf-8")
    xml = xml.replace(
        '<mesocyclone_shear_max units="m/s/km">10.565076</mesocyclone_shear_max>',
        '<mesocyclone_shear_max units="m/s/km">n/d</mesocyclone_shear_max>',
    )
    _s, records, _st = adm.parse_meso_xml(xml, "meso_20210708_1040.xml", ARCHIVED_AT)
    rec = records[0]
    assert rec["shear_max"] is None
    assert rec["null_reasons"]["shear_max"] == "invalid_number"


def test_event_without_position_is_dropped_with_reason():
    xml = _read(REAL).decode("utf-8")
    xml = xml.replace("            <latitude>49.723716</latitude>\n", "")
    _s, records, stats = adm.parse_meso_xml(xml, "meso_20210708_1040.xml", ARCHIVED_AT)
    assert len(records) == 3
    assert stats.events == 4
    assert stats.dropped == 1
    assert stats.drop_reasons == {"missing_latitude": 1}


# ---------------------------------------------------------------------------
# JSONL, meta, ledger
# ---------------------------------------------------------------------------

def test_jsonl_roundtrip_and_meta(tmp_path):
    _s, records, _st = _parse_real()
    jsonl = str(tmp_path / "dwd_mesocyclones.jsonl")
    assert adm.append_jsonl(jsonl, records) == 4

    state = adm.scan_jsonl(jsonl)
    assert state.detections == 4
    assert state.corrupt_lines == 0
    assert state.first_time == "2021-07-08T10:40:00Z"
    assert state.last_time == "2021-07-08T10:40:00Z"
    assert state.source_files == {"meso_20210708_1040.xml"}

    meta = adm.build_meta(state, files_scanned=1, updated_at=ARCHIVED_AT)
    assert meta["detections"] == 4
    assert meta["files_with_detections"] == 1
    assert meta["files_scanned"] == 1
    assert meta["first_time"] == "2021-07-08T10:40:00Z"
    assert meta["last_time"] == "2021-07-08T10:40:00Z"

    with open(jsonl, "r", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    assert len(lines) == 4
    assert lines[0]["time"] == "2021-07-08T10:40:00Z"


def test_corrupt_jsonl_line_is_counted_not_fatal(tmp_path):
    jsonl = tmp_path / "dwd_mesocyclones.jsonl"
    _s, records, _st = _parse_real()
    adm.append_jsonl(str(jsonl), records)
    with open(jsonl, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("{not-json}\n")
    state = adm.scan_jsonl(str(jsonl))
    assert state.detections == 4
    assert state.corrupt_lines == 1


def test_meta_is_written_only_when_content_changes(tmp_path):
    meta_path = str(tmp_path / "dwd_mesocyclones_meta.json")
    state = adm.ArchiveState(detections=4, first_time="2021-07-08T10:40:00Z",
                             last_time="2021-07-08T10:40:00Z",
                             source_files={"meso_20210708_1040.xml"})
    first = adm.build_meta(state, files_scanned=1, updated_at="2026-11-10T16:30:00Z")
    assert adm._write_json(meta_path, first) is True
    # stesso contenuto logico, updated_at diverso -> nessuna riscrittura
    second = adm.build_meta(state, files_scanned=1, updated_at="2026-11-10T17:00:00Z")
    assert adm._write_json(meta_path, second) is False
    with open(meta_path, "r", encoding="utf-8") as fh:
        assert json.load(fh)["updated_at"] == "2026-11-10T16:30:00Z"
    # contenuto logico diverso -> riscrittura
    third = adm.build_meta(state, files_scanned=2, updated_at="2026-11-10T17:00:00Z")
    assert adm._write_json(meta_path, third) is True


def test_seen_ledger_pruned_to_retention():
    from datetime import datetime, timezone
    names = {
        "meso_20261110_1600.xml",  # recente
        "meso_20261101_0000.xml",  # vecchio (oltre 7 giorni)
        "non_un_nome.xml",
    }
    kept = adm.prune_seen(names, now=datetime(2026, 11, 10, 16, 5, tzinfo=timezone.utc))
    assert kept == {"meso_20261110_1600.xml"}


def test_end_to_end_idempotent(tmp_path):
    jsonl = str(tmp_path / "dwd_mesocyclones.jsonl")
    meta = str(tmp_path / "dwd_mesocyclones_meta.json")
    seen = str(tmp_path / "dwd_mesocyclones_seen.json")
    argv = ["--source-dir", FIXTURES, "--jsonl", jsonl, "--meta", meta, "--seen", seen]

    assert adm.main(argv) == 0
    assert adm.scan_jsonl(jsonl).detections == 4
    assert os.path.getsize(jsonl) > 0

    # secondo passaggio: nessun duplicato, il JSONL non cresce
    before = os.path.getsize(jsonl)
    assert adm.main(argv) == 0
    assert os.path.getsize(jsonl) == before
    assert adm.scan_jsonl(jsonl).detections == 4
