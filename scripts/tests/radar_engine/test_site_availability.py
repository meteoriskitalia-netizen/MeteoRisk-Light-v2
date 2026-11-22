#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tabella disponibilita' radar: 13 campi (#207), 5 stati, dati reali.

Tre sorgenti di evidenza:
  - misure SINTETICHE per i 5 stati (classificazione operativa);
  - fixture `frale_odim_header_2026_11_22.json`: ODIM REALE scaricato e
    parsato il 2026-11-22 (88248 byte, sha256 dichiarato nella fixture);
  - fixture `radar_availability_2026_11_22.json`: 30 siti MISURATI con
    paginazione S3 completa, usata per i conteggi reali per stato.
Nessun test usa la rete.
"""

import json
import os

import pytest

from radar_engine import site_availability as sa

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _frale_header():
    with open(os.path.join(FIX, "frale_odim_header_2026_11_22.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def _base(**overrides):
    """Misura sintetica di uno scan FR valido e fresco (frale-like)."""
    m = {
        "site": "frale", "country": "FR", "platform": "0-250-0-frale",
        "ord_status": 200, "ord_coverages": 12, "s3_keycount": 540,
        "variables_from_names": ["DBZH", "TH", "VRADH"],
        "elevation_angles": [0.6, 1.0, 1.4, 90.0],
        "last_available_scan": "2026-11-22T07:29:00Z", "age_min": 3.3,
        "update_interval_min": 5.0, "sample_key": "frale@20261122T0729@0.6.h5",
        "odim_http": 200, "odim_sha256": "abc",
        "odim": {
            "lat": 42.12971, "lon": 9.49639, "height_m": 63.0,
            "ni": 62.0555, "beamwidth": 1.24, "software": "SERVAL",
            "elangle": 0.6, "nrays": 360, "nbins": 267, "rscale": 960.0,
            "quantities": ["DBZH", "TH", "VRADH"],
            "vradh": {"gain": 0.5, "offset": -60.0, "nodata": 255.0,
                      "undetect": 254.0, "valid_pixels": 13140,
                      "total_pixels": 96120},
            "quality_nodes": [], "groups": ["dataset1", "how", "what", "where"],
        },
    }
    m.update(overrides)
    return m


# ---------------------------------------------------------------------------
# 1. I 5 stati sono operativi e DISTINTI
# ---------------------------------------------------------------------------
def test_five_states_are_operational_and_distinct():
    ok = sa.build_record(_base(quality_score=0.85))
    partial = sa.build_record(_base())
    invalid = sa.build_record(_base(
        odim_http=200, odim_error="odim_missing_where_or_dataset1"))
    stale = sa.build_record(_base(
        age_min=sa.STALE_MAX_AGE_MIN + 35.0))
    unavailable = sa.build_record({
        "site": "itbri", "country": "IT", "platform": "0-380-0-itbri",
        "ord_status": 204, "s3_keycount": 0, "variables_from_names": [],
        "elevation_angles": [], "odim_http": None,
    })
    got = [ok["status"], partial["status"], invalid["status"],
           stale["status"], unavailable["status"]]
    assert got == list(sa.STATES)
    assert ok["null_fields"] == []
    assert partial["status_reason"].startswith("missing_fields:")
    assert "quality_score" in partial["status_reason"]
    assert "odim_missing" in invalid["status_reason"]
    assert ">60.0" in stale["status_reason"]
    assert "204" in unavailable["status_reason"]
    # Una misura reale senza quality NON puo' essere ok
    assert partial["n_fields_filled"] == len(sa.FIELDS) - 1


# ---------------------------------------------------------------------------
# 2. Contratto #207 e null_reason sui campi non riempibili
# ---------------------------------------------------------------------------
def test_field_207_contract_and_explicit_null_reasons():
    with pytest.raises(ValueError, match="null_field_requires_null_reason"):
        sa.field(None)
    rec = sa.build_record({
        "site": "itbri", "country": "IT", "platform": "0-380-0-itbri",
        "ord_status": 204, "s3_keycount": 0,
    })
    assert set(rec["fields"]) == set(sa.FIELDS)
    for name, val in rec["fields"].items():
        assert set(val) == {"value", "null_reason", "source", "reference"}
        if val["value"] is None:
            assert isinstance(val["null_reason"], str) and val["null_reason"]
            assert val["source"] and val["reference"]
    assert rec["fields"]["radar_name"]["null_reason"] == \
        "absent_from_ord_catalog_153_platforms"
    assert rec["fields"]["variables"]["null_reason"] == \
        "no_s3_scan_keys_to_read_quantity"
    assert rec["fields"]["elevation_angles"]["null_reason"] == \
        "no_s3_scan_keys_to_read_elangle"
    assert rec["fields"]["quality_score"]["null_reason"] == \
        sa.NO_QUALITY_REASON
    assert "null_reason" in sa.NO_QUALITY_REFERENCE or \
        "misurato" in sa.NO_QUALITY_REFERENCE


# ---------------------------------------------------------------------------
# 3. Caso REALE frale (ODIM vero del 2026-11-22)
# ---------------------------------------------------------------------------
def test_real_frale_odim_header_fills_variables_and_elevations():
    m = _frale_header()
    rec = sa.build_record(m)
    f = rec["fields"]
    assert rec["status"] == "partial"
    assert f["radar_id"]["value"] == "frale"
    assert f["radar_name"]["value"] == "Aleria"
    assert f["latitude"]["value"] == pytest.approx(42.12971)
    assert f["longitude"]["value"] == pytest.approx(9.49639)
    assert f["altitude"]["value"] == 63.0
    assert f["ni"]["value"] == pytest.approx(62.056, abs=0.001)
    # 4 campi prima mancanti: 3 riempiti dal dato vero, quality null motivato
    assert f["variables"]["value"] == ["DBZH", "TH", "VRADH"]
    assert f["elevation_angles"]["value"] == [
        0.6, 1.0, 1.4, 2.5, 3.5, 5.0, 6.5, 8.0, 9.5, 90.0]
    assert f["range"]["value"] == pytest.approx(256.3, abs=0.1)
    assert f["quality_score"]["value"] is None
    assert f["quality_score"]["null_reason"] == sa.NO_QUALITY_REASON
    assert rec["n_fields_filled"] == len(sa.FIELDS) - 1
    assert rec["odim_sha256"] == m["odim_sha256"]
    assert rec["sample_key"].endswith("DBZH_TH_VRADH.h5")


# ---------------------------------------------------------------------------
# 4. Conteggi REALI di oggi (30 siti, paginazione S3 completa)
# ---------------------------------------------------------------------------
def test_real_2026_11_22_table_counts():
    with open(os.path.join(FIX, "radar_availability_2026_11_22.json"),
              encoding="utf-8") as fh:
        data = json.load(fh)
    table = sa.build_table(data["sites"],
                           generated_at=data["measured_at"])
    assert table["n_sites"] == 30
    assert table["fields"] == list(sa.FIELDS)
    assert table["states"] == list(sa.STATES)
    assert table["stale_max_age_min"] == 60.0
    assert table["counts"] == {
        "ok": 0, "partial": 25, "invalid": 0, "stale": 0, "unavailable": 5}
    by_site = {r["radar_id"]: r for r in table["sites"]}
    assert by_site["itbri"]["status"] == "unavailable"
    assert by_site["frale"]["status"] == "partial"
    assert by_site["hrbil"]["status"] == "partial"
    assert set(by_site) == {s[0] for s in sa.SITES}
    md = sa.render_markdown(table)
    assert md.count("\n") == 31          # intestazione + separatore + 30 righe
    assert "| QUALITY_SCORE | STATO |" in md
