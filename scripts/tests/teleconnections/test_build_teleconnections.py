# -*- coding: utf-8 -*-
"""Test del produttore di teleconnessioni — fixture ridotta REALE (NOAA CPC).

Le fixture sotto ``fixtures/`` sono state estratte verbatim dai 5 file scaricati
il 2026-11-17 (poche righe ciascuno, con i sentinel -99.90 / -999.9 originali).
I test sono offline: nessuna rete, nessun token.
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "synoptic"))
import build_teleconnections as bt  # noqa: E402

FIX = HERE / "fixtures"
LM = "Tue, 17 Nov 2026 14:06:39 GMT"

FIXTURE_BY_URL = {
    bt.CW_AO_URL: "ao.csv",
    bt.CW_NAO_URL: "nao.csv",
    bt.TELE_NH_URL: "tele_index.nh",
    bt.WKSST_URL: "wksst9120.for",
    bt.SOI_URL: "soi",
}


def fixture_fetcher(url, timeout=60):
    """Fetcher locale: restituisce (testo fixture, Last-Modified fissato)."""
    return (FIX / FIXTURE_BY_URL[url]).read_text(encoding="utf-8"), LM


# --------------------------------------------------------------------------
# 1. Filtro lead==0 (regola obbligatoria) + member==0 (controllo GEFS)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fname,expected,lead_pred,lead_vals,member_val",
    [
        (
            "ao.csv",
            -0.6927981684220341,
            -0.22026263541882962,
            0.4901998884133086,
            -0.6978524988254403,
        ),
        (
            "nao.csv",
            0.21868255128826625,
            0.6170835880727982,
            0.6381245617359715,
            0.22590688672421472,
        ),
    ],
)
def test_cwlinks_lead_zero_and_control_member(fname, expected, lead_pred, lead_vals, member_val):
    text = (FIX / fname).read_text(encoding="utf-8")
    values, stats = bt.parse_cwlinks(text)

    assert stats["rows"] == 6
    assert stats["dropped_lead_gt0"] == 2       # le 2 righe lead=1 sono previsioni
    assert stats["dropped_member_gt0"] == 1     # member=1 e' analisi perturbata
    assert stats["kept_lead0_member0"] == 3
    assert len(values) == 3

    assert values[0] == [bt.utc_ms(2026, 7, 21), expected]
    published = [v for _, v in values]
    assert lead_pred not in published           # forecast lead=1 NON pubblicato
    assert lead_vals not in published
    assert member_val not in published          # member perturbato NON pubblicato


# --------------------------------------------------------------------------
# 2. Parsing fixed-width dei mensili (tele_index.nh e soi) + settimanale
# --------------------------------------------------------------------------
def test_tele_nh_monthly_fixed_width():
    text = (FIX / "tele_index.nh").read_text(encoding="utf-8")

    nao, _ = bt.parse_tele_nh(text, 0)
    assert nao[0] == [bt.utc_ms(1950, 1), 0.56]
    assert nao[1] == [bt.utc_ms(1950, 2), 0.01]
    assert nao[2] == [bt.utc_ms(1950, 3), -0.78]

    eawr, _ = bt.parse_tele_nh(text, 5)   # colonna 8 dell'header = EA/WR
    assert eawr[0][1] == 2.29
    scand, _ = bt.parse_tele_nh(text, 6)  # SCA
    assert scand[0][1] == 0.78
    poleur, _ = bt.parse_tele_nh(text, 8)  # POL
    assert poleur[0][1] == -0.71

    tnh, st_tnh = bt.parse_tele_nh(text, 7)
    assert len(tnh) == 2                  # 1950-03 ha TNH = -99.90 -> saltato
    assert st_tnh["skipped_missing"] == 3  # 03, 09, 10

    pt, st_pt = bt.parse_tele_nh(text, 9)
    assert pt == [[bt.utc_ms(2026, 9), 0.30]]  # solo 2026-09 valido
    assert st_pt["skipped_missing"] == 4        # 4 righe con PT=-99.90


def test_wksst_weekly_fixed_width_uses_ssta():
    text = (FIX / "wksst9120.for").read_text(encoding="utf-8")
    n12, _ = bt.parse_wksst(text, 0)
    n34, _ = bt.parse_wksst(text, 2)
    n4, _ = bt.parse_wksst(text, 3)
    # Si pubblica l'anomalia (SSTA), non la SST grezza (20.6 / 26.5 / 28.3).
    assert n12[0] == [bt.utc_ms(1981, 9, 2), -0.1]
    assert n34[0] == [bt.utc_ms(1981, 9, 2), -0.2]
    assert n4[0] == [bt.utc_ms(1981, 9, 2), -0.3]
    assert len(n12) == 3


def test_soi_uses_standardized_block_and_skips_missing():
    text = (FIX / "soi").read_text(encoding="utf-8")
    values, stats = bt.parse_soi(text)
    assert stats["standardized_block_found"] is True
    # 1951 JAN: 1.5 nel blocco STANDARDIZZATO, 2.5 nel blocco grezzo (non usato)
    assert values[0] == [bt.utc_ms(1951, 1), 1.5]
    y2026 = [p for p in values if bt.utc_ms(2026, 1) <= p[0] < bt.utc_ms(2027, 1)]
    assert len(y2026) == 10               # NOV/DIC = -999.9 -> saltati
    assert stats["skipped_missing"] == 2


# --------------------------------------------------------------------------
# 3. Nessun valore null inventato + schema + 4. dimensione JSON
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("teleconn") / "data/climate/teleconnections.json"
    payload, stats, nbytes = bt.build(out, fetcher=fixture_fetcher)
    return payload, stats, nbytes, out


def test_no_invented_null_values(built):
    payload, stats, nbytes, out = built
    for series in payload["series"]:
        assert series["values"], "serie vuota: %s" % series["id"]
        for point in series["values"]:
            assert isinstance(point, list) and len(point) == 2
            assert isinstance(point[0], int) and isinstance(point[1], float)
            assert bt.utc_ms(1900, 1) <= point[0] <= bt.utc_ms(2100, 1)
        epochs = [p[0] for p in series["values"]]
        assert epochs == sorted(epochs)
        assert len(epochs) == len(set(epochs))
    # I sentinel non diventano vecchi null: contano come punti saltati.
    assert stats["pt_monthly"]["skipped_missing"] == 4
    assert stats["soi_monthly"]["skipped_missing"] == 2
    assert stats["ao_daily"]["dropped_lead_gt0"] == 2
    raw = out.read_bytes()
    assert b"null" not in raw


def test_schema_and_source_metadata(built):
    payload, stats, nbytes, out = built
    assert payload["schema_version"] == 1
    assert payload["source"] == "NOAA CPC"
    assert payload["license"] == "NOAA public domain — attribution required"
    assert payload["attribution"] == "NOAA Climate Prediction Center"
    assert payload["methodology_notice"]["effective_from"] == "2027-02-15"
    assert payload["methodology_notice"]["requires_reverification"] is True
    assert len(payload["series"]) == len(bt.SOURCES) == 17
    ids = [s["id"] for s in payload["series"]]
    assert len(ids) == len(set(ids))
    for s in payload["series"]:
        assert set(s) == {"id", "label", "values", "baseline",
                          "last_modified", "source_url"}
        assert s["source_url"].startswith("https://")
        assert s["last_modified"] == LM
        assert s["baseline"]


def test_json_size_compact_and_roundtrip(built):
    payload, stats, nbytes, out = built
    raw = out.read_bytes()
    assert nbytes == len(raw)
    assert 1000 <= nbytes <= 200000          # compacto, non cresce senza motivo
    assert raw.count(b"\n") == 1 and raw.endswith(b"\n")
    assert raw.startswith(b'{"schema_version":1,')
    assert b"\n " not in raw  # nessuna indentazione: JSON compatto
    assert json.loads(raw.decode("utf-8")) == payload
