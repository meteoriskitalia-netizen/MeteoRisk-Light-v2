"""Test mirati per l'import CREA WDA (etichette Hail indipendenti).

Usa il CSV REALE in temp. Se non e' presente, i test fanno skip DICHIARATO:
non si creano fixture artefatte per simulare il dataset.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest

CSV = (
    r"C:\Users\aless\AppData\Local\Temp\opencode\crea_wda"
    r"\Dataset_Weather_related_disasters_agriculture_v1.2.csv"
)
EXPECTED_SHA = "846AA6310CD25F1661D825AE8EF30AAA1749C0029EBED181F0FFEDE0A8CB4B6F"
SKIP_REASON = "CSV CREA WDA reale non presente in temp: skip dichiarato (nessuna fixture artefatta)"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

pytestmark = pytest.mark.skipif(not os.path.isfile(CSV), reason=SKIP_REASON)


def _mod():
    from validation import import_crea_wda as m

    return m


def test_csv_sha256():
    h = hashlib.sha256()
    with open(CSV, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    assert h.hexdigest().upper() == EXPECTED_SHA
    assert _mod().sha256_file(CSV) == EXPECTED_SHA


def test_75_hail_records_and_13_event_types():
    m = _mod()
    rows, events = m.read_csv(CSV)
    assert len(rows) == 1159
    assert len(events) == 13
    assert events["Hail"] == 75
    assert events["Tornado"] == 115
    assert events["Strong wind"] == 66
    assert events["Heavy rain leading to flood"] == 339
    recs = m.build_records(rows)
    assert len(recs) == 75
    assert all(r["event_type"] == "Hail" for r in recs)


def test_no_invented_values_all_null_reasons():
    m = _mod()
    rows, _ = m.read_csv(CSV)
    recs = m.build_records(rows)
    for r in recs:
        assert r["hour"] is None and r["hour_null_reason"]
        assert r["lat"] is None and r["lon"] is None and r["geo_null_reason"]
        assert r["intensity"] is None and r["intensity_null_reason"]
        assert r["source"] and r["license"] == "CC BY 4.0"
        assert r["attribution"]
        assert r["date_start"] and len(r["date_start"]) == 10
        # ISO yyyy-mm-dd
        assert r["date_start"][4] == "-" and r["date_start"][7] == "-"
        if r["date_end"] is not None:
            assert len(r["date_end"]) == 10


def test_empty_end_date_is_null():
    m = _mod()
    rows, _ = m.read_csv(CSV)
    recs = m.build_records(rows)
    empty = [r for r in recs if r["date_end"] is None]
    assert len(empty) == 2
    assert all(r["duration_days"] == 0 for r in empty)


def test_no_duplicate_dedup_keys():
    m = _mod()
    rows, _ = m.read_csv(CSV)
    recs = m.build_records(rows)
    keys = [r["dedup_key"] for r in recs]
    assert len(set(keys)) == len(keys) == 75


def test_verify_selftest_passes():
    m = _mod()
    rows, _ = m.read_csv(CSV)
    recs = m.build_records(rows)
    assert m.verify(CSV, rows, recs) == []


def test_wrong_sha_is_rejected(tmp_path):
    """Un CSV con sha diverso NON deve produrre output."""
    m = _mod()
    bad = tmp_path / "bad.csv"
    bad.write_bytes(b"ID;CodReg;CodProv\n")
    rc = m.main(["--csv", str(bad), "--out", str(tmp_path / "out.jsonl")])
    assert rc == 3
    assert not (tmp_path / "out.jsonl").exists()


def test_join_by_province_name(tmp_path):
    """Il join arricchisce senza inventare: geometria dichiarata come proxy."""
    from validation import join_crea_wda_provinces as j

    geo = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "geography", "province_italiane.geojson")
    )
    if not os.path.isfile(geo):
        pytest.skip(geo + " non presente in questa copia")
    m = _mod()
    rows, _ = m.read_csv(CSV)
    recs = m.build_records(rows)
    inp = tmp_path / "labels.jsonl"
    with open(inp, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    out = tmp_path / "labels_geo.jsonl"
    rc = j.main(["--in", str(inp), "--geo", geo, "--out", str(out)])
    assert rc == 0
    result = [json.loads(line) for line in open(out, encoding="utf-8")]
    assert len(result) == 75
    joined = [r for r in result if r.get("province_centroid")]
    assert len(joined) == 75  # tutte le 33 province Hail sono nel geojson
    for r in joined:
        assert -20 < r["province_centroid"]["lat"] < 50
        assert -20 < r["province_centroid"]["lon"] < 40
        assert "AREA proxy" in r["province_geometry_source"]
    # L'input NON e' stato modificato
    assert all("province_centroid" not in json.loads(line) for line in open(inp, encoding="utf-8"))
