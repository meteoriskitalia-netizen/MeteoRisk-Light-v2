# -*- coding: utf-8 -*-
"""Temperatura, umidita' relativa e dew point dalle SERIE di stazione.

Verifica `fetch_stations.build_instant_block` e `fetch_stations.build_dew_series`:

  * la media e' PESATA SUL TEMPO (sample-and-hold), non la media dei campioni;
  * minimo e massimo sono dei campioni nella finestra e NON coincidono con la
    media;
  * le finestre corte 5m/30m hanno un gate sul PASSO della serie (60 s / 300 s)
    e sotto quella risoluzione il campo e' null con un motivo dichiarato;
  * i gate fisici (|T| > 60 degC, RH fuori 0..100 %) scartano e CONTANO;
  * il dew point si calcola solo su coppie T/RH con lo stesso `ref`;
  * `min <= mean <= max` regge anche quando un campione inizia PRIMA della
    finestra e vi vale dentro (era il difetto che dava 671 violazioni su 6.906
    misure reali prima della correzione).

Sul codice pristino (1.3.0.60) `build_instant_block` non esiste: il test fallisce
con AttributeError, come deve.
"""
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "stations"))

import fetch_stations as fs  # noqa: E402

END = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
K = 273.15


def _instant(end, values, step_minutes=10):
    """Serie istantanea (trange 254,0,0): campioni ogni `step_minutes`."""
    count = len(values)
    return [
        (end - timedelta(minutes=step_minutes * (count - 1 - index)), value)
        for index, value in enumerate(values)
    ]


def _temp(values_c, step_minutes=10):
    return {
        "254,0,0": _instant(END, [K + value for value in values_c], step_minutes)
    }


def build(name, series, **kwargs):
    options = {
        "unit": "c" if name != "rh" else "pct",
        "decimals": 1 if name != "rh" else 0,
        "extremes": ("min", "max") if name == "temp" else (),
        "offset": -K if name == "temp" else 0.0,
        "qc_min": -fs.QC_TEMP_MAX_ABS_C if name == "temp" else fs.QC_RH_MIN_PCT,
        "qc_max": fs.QC_TEMP_MAX_ABS_C if name == "temp" else fs.QC_RH_MAX_PCT,
    }
    options.update(kwargs)
    return fs.build_instant_block(name, series, END, **options)


# ---------------------------------------------------------------------------
# media pesata sul tempo
# ---------------------------------------------------------------------------
def test_weighted_mean_on_unequal_intervals():
    """Un campione lungo vale piu' di uno corto.

    Campioni a -52', -42', -12' (poi a -12'+30' = fine): 2 degC per 10 min,
    8 degC per 30 min, 8 degC per 12 min -> (2*10 + 8*42)/52 = 6,8.
    La media dei VALORI sarebbe (2+8+8)/3 = 6,0: il test distingue le due.
    """
    # i tre campioni non sono a 10' fissi: costruzione esplicita
    series = {
        "254,0,0": [
            (END - timedelta(minutes=52), K + 2.0),
            (END - timedelta(minutes=42), K + 8.0),
            (END - timedelta(minutes=12), K + 8.0),
        ]
    }
    fields, _ = build("temp", series)
    assert fields["temp_mean_1h_c"] == pytest.approx(6.8)
    assert fields["temp_min_1h_c"] == pytest.approx(2.0)
    assert fields["temp_max_1h_c"] == pytest.approx(8.0)


def test_min_max_are_not_the_mean():
    """Una punta a 30 degC non sposta la media al massimo."""
    fields, _ = build("temp", _temp([10.0] * 5 + [30.0] + [10.0] * 4))
    assert fields["temp_mean_1h_c"] == pytest.approx(13.3, abs=0.1)
    assert fields["temp_min_1h_c"] == pytest.approx(10.0)
    assert fields["temp_max_1h_c"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# finestre corte: 5 min e 30 min
# ---------------------------------------------------------------------------
def test_5m_mean_on_60s_series():
    """Serie a 60 s: la finestra di 5 min e' calcolabile.

    55 campioni a 10 degC poi 5 a 20 degC, uno al minuto. La finestra
    [fine-5', fine] contiene il campione a -5' (ancora 10, vale 1 minuto) e
    quattro minuti a 20 -> (10*1 + 20*4)/5 = 18,0.
    """
    fields, _ = build(
        "temp", _temp([10.0] * 55 + [20.0] * 5, step_minutes=1)
    )
    assert fields["temp_series_step_s"] == 60
    assert fields["temp_mean_5m_c"] == pytest.approx(18.0)
    assert fields["temp_series_step_s"] == 60
    # 30 min: 26 minuti a 10 e 4 a 20 -> (260 + 80)/30 = 11,3
    assert fields["temp_mean_30m_c"] == pytest.approx(11.3)


def test_5m_refuses_a_900s_series():
    """Passo 900 s > 60 s: null e motivo, non una media finta."""
    fields, _ = build("temp", _temp([5.0] * 13, step_minutes=15))
    assert fields["temp_mean_5m_c"] is None
    assert fields["temp_5m_null_reason"] == "series_too_coarse_for_5min"
    assert fields["temp_series_step_s"] == 900
    assert fields["temp_mean_30m_c"] is None
    assert fields["temp_30m_null_reason"] == "series_too_coarse_for_30min"
    # la 1h resta calcolabile: il gate vale solo per le finestre corte
    assert fields["temp_mean_1h_c"] == pytest.approx(5.0)


def test_30m_mean_on_300s_series():
    """Passo 300 s: 30 min calcolabile, 5 min no."""
    fields, _ = build(
        "temp", _temp([10.0, 10.0, 10.0, 20.0, 20.0, 20.0], step_minutes=5)
    )
    assert fields["temp_series_step_s"] == 300
    assert fields["temp_mean_30m_c"] == pytest.approx(14.0)
    assert fields["temp_mean_5m_c"] is None
    assert fields["temp_5m_null_reason"] == "series_too_coarse_for_5min"


# ---------------------------------------------------------------------------
# copertura
# ---------------------------------------------------------------------------
def test_coverage_40pct_is_null_with_reason():
    """Due soli campioni a 24 minuti: copertura 40% -> null con motivo."""
    series = {
        "254,0,0": [
            (END - timedelta(minutes=24), K + 5.0),
            (END, K + 5.0),
        ]
    }
    fields, _ = build("temp", series)
    assert fields["temp_1h_coverage_frac"] == pytest.approx(0.4)
    assert fields["temp_mean_1h_c"] is None
    assert fields["temp_1h_null_reason"] == "coverage_below_50pct"
    # estremi NON pubblicati: la finestra non e' utilizzabile, non si scrivono
    # tre null (l'assenza e' gia' l'informazione).
    assert "temp_min_1h_c" not in fields


# ---------------------------------------------------------------------------
# gate fisici
# ---------------------------------------------------------------------------
def test_temp_99c_is_dropped_and_counted():
    values = [10.0] * 60
    values[30] = 99.0
    fields, stats = build("temp", _temp(values, step_minutes=1))
    assert stats["points_qc_rejected"] == 1
    assert fields["temp_max_1h_c"] <= 10.0
    assert fields["temp_mean_1h_c"] == pytest.approx(10.0)


def test_rh_150pct_is_dropped_and_counted():
    series = {
        "254,0,0": _instant(END, [50.0, 50.0, 150.0, 50.0, 50.0, 50.0])
    }
    fields, stats = build("rh", series)
    assert stats["points_qc_rejected"] == 1
    assert fields["rh_mean_1h_pct"] == 50
    assert fields["rh_1h_coverage_frac"] == pytest.approx(50 / 60, rel=0.01)


# ---------------------------------------------------------------------------
# dew point
# ---------------------------------------------------------------------------
def test_dew_series_uses_the_same_ref_and_keeps_the_minimum():
    """RH e T con lo stesso `ref`: coppia valida; un T orfano non entra."""
    tpoints = _instant(END, [K + 20.0] * 6)
    tpoints.append((END - timedelta(minutes=55), K + 5.0))  # nessuna RH qui
    rpoints = _instant(END, [80.0] * 6)
    dew_series, rejected = fs.build_dew_series(
        {"254,0,0": tpoints}, {"254,0,0": rpoints}
    )
    assert rejected == 0
    assert len(dew_series["254,0,0"]) == 6
    fields, stats = fs.build_instant_block(
        "dew",
        dew_series,
        END,
        unit="c",
        decimals=1,
        extremes=("min",),
        qc_min=-fs.QC_DEW_MAX_ABS_C,
        qc_max=fs.QC_DEW_MAX_ABS_C,
    )
    expected = fs.dew_point_c(20.0, 80.0)
    assert fields["dew_mean_1h_c"] == pytest.approx(round(expected, 1))
    assert fields["dew_min_1h_c"] == pytest.approx(round(expected, 1))
    assert stats["points_qc_rejected"] == 0


# ---------------------------------------------------------------------------
# periodi misti e invarianti
# ---------------------------------------------------------------------------
def test_mixed_periods_are_not_double_counted():
    """Istantaneo + media oraria: si usa UNO dei due, non la loro somma."""
    instant = _instant(END, [K + value for value in (2.0, 4.0, 6.0, 8.0, 10.0, 10.0)])
    means = [
        (END, K + 3.0),
        (END - timedelta(hours=1), K + 3.0),
        (END - timedelta(hours=2), K + 3.0),
    ]
    fields, _ = build(
        "temp", {"254,0,0": instant, "3,0,3600": means}
    )
    assert fields["temp_source_product"] == "254,0,0"
    assert fields["temp_mean_1h_c"] == pytest.approx(6.0)
    assert fields["temp_1h_coverage_frac"] == pytest.approx(50 / 60, rel=0.01)


def test_min_mean_max_invariant_with_a_sample_starting_before_the_window():
    """Un campione a 0 degC che inizia PRIMA della finestra vale dentro.

    Era il difetto reale: la media lo includeva (sample-and-hold) ma il minimo
    guardava solo i `ref` dentro la finestra, e `min <= mean` cadeva (671
    violazioni su 6.906 misure). Ora gli estremi vengono dai SEGMENTI.
    """
    series = {
        "254,0,0": [
            (END - timedelta(minutes=65), K + 0.0),
            (END - timedelta(minutes=55), K + 10.0),
            (END - timedelta(minutes=25), K + 10.0),
            (END, K + 10.0),
        ]
    }
    fields, _ = build("temp", series)
    assert fields["temp_min_1h_c"] == pytest.approx(0.0)
    assert fields["temp_mean_1h_c"] == pytest.approx(9.2)
    assert fields["temp_max_1h_c"] == pytest.approx(10.0)
    assert (
        fields["temp_min_1h_c"]
        <= fields["temp_mean_1h_c"]
        <= fields["temp_max_1h_c"]
    )


def test_hourly_triplet_has_mean_min_and_max():
    """Tre ore di istantanei: un bucket `[media, minima, massima]` per ora."""
    values = [2.0] * 6 + [4.0] * 6 + [10.0] * 6
    fields, _ = build("temp", _temp(values, step_minutes=10))
    hourly = fields["temp_hourly"]
    assert len(hourly) == 3
    assert all(len(row) == 4 for row in hourly)
    assert [row[1] for row in hourly] == pytest.approx([2.0, 3.7, 9.0])
    assert [row[2] for row in hourly] == pytest.approx([2.0, 2.0, 4.0])
    assert [row[3] for row in hourly] == pytest.approx([2.0, 4.0, 10.0])


def test_no_series_writes_no_fields():
    fields, stats = build("temp", {})
    assert fields == {}
    assert stats["points_qc_rejected"] == 0


def test_temperature_in_kelvin_is_converted():
    """La sorgente e' in kelvin: 283,15 K -> 10,0 degC, non 283,2."""
    fields, _ = build("temp", _temp([10.0] * 7))
    assert fields["temp_mean_1h_c"] == pytest.approx(10.0)
    assert not math.isclose(fields["temp_mean_1h_c"], 283.2)

