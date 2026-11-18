# -*- coding: utf-8 -*-
"""Test del produttore di teleconnessioni — fixture ridotta REALE (NOAA CPC).

Le fixture sotto ``fixtures/`` sono state estratte verbatim dai 10 file scaricati
il 2026-11-17/18 (poche righe ciascuno, con i sentinel -99.90 / -999.9 originali).
I test sono offline: nessuna rete, nessun token. Coprono anche le 5 serie nuove
1.3.0.11 (QBO 30/50, AMO, PNA daily, timeline SSW) e i buchi dichiarati.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
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
    bt.QBO30_URL: "qbo30.txt",
    bt.QBO50_URL: "qbo50.txt",
    bt.AMO_URL: "amo.csv",
    bt.CW_PNA_URL: "pna.csv",
    bt.SSW_URL: "ssw.html",
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
            # AMO parte dal 1854: il limite inferiore e' 1800, non 1900.
            assert bt.utc_ms(1800, 1) <= point[0] <= bt.utc_ms(2100, 1)
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
    assert payload["schema_version"] == 2
    assert payload["source"] == "NOAA CPC + NOAA NCEI + NOAA CSL/SSWC"
    assert payload["license"] == "NOAA public domain — attribution required"
    assert "NOAA Climate Prediction Center" in payload["attribution"]
    assert payload["methodology_notice"]["effective_from"] == "2027-02-15"
    assert payload["methodology_notice"]["requires_reverification"] is True
    assert len(payload["series"]) == len(bt.SOURCES) == 22
    ids = [s["id"] for s in payload["series"]]
    assert len(ids) == len(set(ids))
    for s in payload["series"]:
        assert {"id", "label", "values", "baseline", "last_modified",
                "source_url", "source", "license", "unit"} <= set(s)
        assert s["source_url"].startswith("https://")
        assert s["last_modified"] == LM
        assert s["baseline"]
        assert s["source"] and s["license"] and s["unit"]


def test_json_size_compact_and_roundtrip(built):
    payload, stats, nbytes, out = built
    raw = out.read_bytes()
    assert nbytes == len(raw)
    assert 1000 <= nbytes <= 200000          # compacto, non cresce senza motivo
    assert raw.count(b"\n") == 1 and raw.endswith(b"\n")
    assert raw.startswith(b'{"schema_version":2,')
    assert b"\n " not in raw  # nessuna indentazione: JSON compatto
    assert json.loads(raw.decode("utf-8")) == payload


# --------------------------------------------------------------------------
# 5. Serie nuove 1.3.0.11 (QBO, AMO, PNA daily, SSW) e buchi dichiarati
# --------------------------------------------------------------------------
def test_qbo_original_block_only_and_sentinel():
    text = (FIX / "qbo30.txt").read_text(encoding="utf-8")
    values, st = bt.parse_qbo(text)
    assert st["block"] == "ORIGINAL"
    assert st["skipped_other_blocks"] >= 1   # blocco ANOMALY scartato
    # Solo 4 anni del blocco ORIGINAL: nessun valore dei blocchi ANOMALY e
    # STANDARDIZED presenti nella stessa fixture puo' finire nelle values.
    assert len(values) == 48
    assert values[0] == [bt.utc_ms(1979, 1), 1.85]
    # 2027 e' tutto -999.90: i 12 punti vanno saltati, non messi a null.
    assert not any(bt.utc_ms(2027, 1) <= v[0] < bt.utc_ms(2028, 1)
                   for v in values)
    assert st["skipped_missing"] >= 12
    assert all(v[1] > -99 for v in values)


def test_qbo50_unit_and_source_metadata(built):
    s = [x for x in built[0]["series"] if x["id"] == "qbo50_monthly"][0]
    assert s["unit"] == "m/s"
    assert s["source"] == "NOAA CPC"
    # Il blocco ORIGINAL contiene vento in m/s, non scarti standardizzati.
    assert s["values"][0] == [bt.utc_ms(1979, 1), 7.38]


def test_amo_ersstv5_degrees_and_start():
    text = (FIX / "amo.csv").read_text(encoding="utf-8")
    values, st = bt.parse_amo(text)
    assert values[0] == [bt.utc_ms(1854, 1), 0.28]
    assert st["kept"] == 10 and st["skipped_missing"] == 0
    raw = (FIX / "amo.csv").read_text(encoding="utf-8")
    assert "ERSST AMO" in raw  # fonte dichiarata nel file sorgente


def test_pna_daily_uses_lead0_member0_only():
    text = (FIX / "pna.csv").read_text(encoding="utf-8")
    values, st = bt.parse_cwlinks(text)
    assert st["rows"] == 5
    assert st["kept_lead0_member0"] == 3
    assert st["dropped_lead_gt0"] == 1
    assert st["dropped_member_gt0"] == 1
    assert values[0] == [bt.utc_ms(2026, 7, 21), -0.3827183031998311]
    forecast = -0.5560566839969795   # riga lead=1
    member = -0.37651025135744204    # riga member=1
    assert forecast not in [v for _, v in values]
    assert member not in [v for _, v in values]


def test_ssw_timeline_citation_definition_and_century(built):
    text = (FIX / "ssw.html").read_text(encoding="utf-8")
    values, events, st = bt.parse_ssw(text)
    assert st["kept"] == 3 and len(values) == 3
    assert events[0]["name"] == "JAN 1958"
    # IL SECLO: Python %y darebbe 2058. Qui deve essere 1958 (o 1959
    # se la data ERA5 e' slittata a febbraio), mai il 2058.
    # fromtimestamp non accetta epoch negativi su Windows: si somma un delta.
    year = (datetime(1970, 1, 1, tzinfo=timezone.utc)
            + timedelta(milliseconds=values[0][0])).year
    assert 1958 <= year <= 1959
    s = [x for x in built[0]["series"] if x["id"] == "ssw_events"][0]
    assert s["kind"] == "events"
    assert s["values"] and all(v[1] == 1.0 for v in s["values"])
    assert "10.5194/essd-9-63-2017" in s["citation"]
    assert "Charlton" in s["definition"] and "10 hPa" in s["definition"]
    assert s["source"] == "NOAA CSL / SSWC"
    assert len(s["events"]) == len(s["values"])


def test_no_solar_activity_and_declared_gaps(built):
    payload = built[0]
    assert payload["solar_activity"]["implemented"] is False
    ids = [s["id"] for s in payload["series"]]
    assert not any(k in i for i in ids
                   for k in ("sun", "solar", "f107", "sunspot"))
    gaps = {g["id"]: g for g in payload["not_implemented"]}
    assert gaps["nam_10hpa"]["status"] == "RICHIEDE TOKEN"
    assert gaps["itcz"]["status"] == "NON VERIFICATO"
    for g in gaps.values():
        assert g["reason"] and len(g["reason"]) > 40
