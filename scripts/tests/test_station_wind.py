# -*- coding: utf-8 -*-
"""Vento dalle SERIE di stazione (`fetch_stations.build_wind_block`).

Il vento NON si accumula: sommare velocita' non ha senso fisico. Qui si verifica
che il campo pubblicato sia davvero un MASSIMO (non una somma) e una MEDIA
pesata sul tempo (non una media di valori), che i due non si mescolino e che
l'invariante "massimo su 3 h >= ultimo valore" non cada.

Il difetto reale che l'ultimo test difende: il 2026-11-23, scegliendo il prodotto
per sola copertura, 753 confronti su 1.494 davano `wind_max_3h_ms` MINORE di
`wind_speed_10m` (arpafvg 0,9 contro 2,9), perche' si confrontava il massimo di
medie orarie con un istantaneo.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "stations"))

import fetch_stations as fs  # noqa: E402

END = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def _instant(end, values, step_minutes=10):
    """Serie istantanea (trange 254,0,0): campioni ogni `step_minutes`."""
    count = len(values)
    return [
        (end - timedelta(minutes=step_minutes * (count - 1 - index)), value)
        for index, value in enumerate(values)
    ]


# ---------------------------------------------------------------------------
# massimo, non somma
# ---------------------------------------------------------------------------
def test_gust_is_a_maximum_not_a_sum():
    """Due raffiche orarie da 7 e 12 m/s: il massimo e' 12, la somma sarebbe 19."""
    gust = [
        (END - timedelta(hours=2), 7.0),
        (END - timedelta(hours=1), 12.0),
        (END, 9.0),
    ]
    fields, _ = fs.build_wind_block({}, {"2,0,3600": gust}, END)
    assert fields["gust_max_3h_ms"] == pytest.approx(12.0)
    assert fields["gust_max_1h_ms"] == pytest.approx(9.0)
    assert fields["gust_period_s"] == 3600
    assert fields["gust_source_product"] == "2,0,3600"


def test_gust_window_coverage_is_declared():
    """Una sola ora di raffica su tre: la 3 h resta null con il motivo."""
    gust = [(END, 12.0)]
    fields, _ = fs.build_wind_block({}, {"2,0,3600": gust}, END)
    assert fields["gust_max_1h_ms"] == pytest.approx(12.0)
    assert fields["gust_max_3h_ms"] is None
    assert fields["gust_max_3h_null_reason"] == "coverage_below_50pct"
    assert fields["gust_max_3h_coverage_frac"] == pytest.approx(1 / 3, rel=0.01)


def test_wind_max_3h_is_the_max_of_the_instantaneous_samples():
    """Su 3 ore di istantanei il massimo e' il piu' alto, non la somma."""
    speed = _instant(END, [2.0] * 15 + [4.0, 6.0, 10.0])
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["wind_max_3h_ms"] == pytest.approx(10.0)
    assert fields["wind_mean_3h_ms"] is not None
    assert fields["wind_mean_3h_coverage_frac"] > 0.9
    assert fields["wind_source"] == "series"
    assert fields["wind_period_s"] == 0  # istantaneo: sample-and-hold


def test_wind_max_3h_is_absent_without_an_instantaneous_series():
    """Solo medie orarie: NON si pubblica un "massimo del vento".

    Il massimo di una serie di MEDIE non e' il massimo del vento, e pubblicarlo
    romperebbe l'invariante `wind_max_3h_ms >= wind_speed_10m`. Il campo e'
    assente (grandezza non disponibile), non un null con motivo.
    """
    means = [(END - timedelta(hours=3 - i), 3.0) for i in range(4)]
    fields, _ = fs.build_wind_block({"3,0,3600": means}, {}, END)
    assert "wind_max_3h_ms" not in fields
    assert fields["wind_mean_3h_ms"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# media pesata sul tempo
# ---------------------------------------------------------------------------
def test_wind_mean_is_weighted_by_time_not_by_sample_count():
    """Un campione lungo vale piu' di uno corto.

    Istanti a 0', 10', 40' (poi a 52'): 2 m/s per 10 min, 8 m/s per 30 min,
    8 m/s per 12 min -> media pesata = (2*10 + 8*30 + 8*12)/52 = 6,8 m/s.
    La media dei valori sarebbe (2+8+8)/3 = 6,0: il test distingue le due.
    """
    speed = [
        (END - timedelta(minutes=52), 2.0),
        (END - timedelta(minutes=42), 8.0),
        (END - timedelta(minutes=12), 8.0),
    ]
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["wind_mean_1h_ms"] == pytest.approx(6.8)


def test_wind_mean_needs_half_of_the_window():
    """Due campioni su un'ora (copertura 33%): null con il motivo."""
    speed = [(END - timedelta(minutes=20), 5.0), (END, 5.0)]
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["wind_mean_1h_ms"] is None
    assert fields["wind_mean_1h_null_reason"] == "coverage_below_50pct"


# ---------------------------------------------------------------------------
# gate fisico
# ---------------------------------------------------------------------------
def test_gust_above_the_gate_is_dropped_and_counted():
    """90 m/s non e' una raffica: scartata e contata, resta la valida."""
    gust = [(END - timedelta(hours=1), 90.0), (END, 10.0)]
    fields, stats = fs.build_wind_block({}, {"2,0,3600": gust}, END)
    assert fields["gust_max_1h_ms"] == pytest.approx(10.0)
    assert stats["points_qc_rejected"] == 1


def test_wind_above_the_gate_is_dropped_and_counted():
    """70 m/s di vento medio oltre il gate (60): scartato e contato."""
    speed = _instant(END, [70.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    fields, stats = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["wind_mean_1h_ms"] == pytest.approx(5.0)
    assert stats["points_qc_rejected"] == 1


def test_negative_wind_is_rejected():
    speed = _instant(END, [-3.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    _, stats = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert stats["points_qc_rejected"] == 1


# ---------------------------------------------------------------------------
# periodi misti
# ---------------------------------------------------------------------------
def test_instantaneous_series_wins_over_hourly_means():
    """Istantaneo e media oraria dello stesso vento: si usa UNO solo.

    La media oraria `3,0,3600` avrebbe copertura piena, l'istantaneo `254,0,0`
    anche: si preferisce l'istantaneo (piu' fine, e coerente con
    `wind_speed_10m`, da cui deriva l'invariante del massimo).

    Il valore 6,0 e non 6,67 e' il sample-and-hold: l'ultimo campione cade su
    `now` e vale ZERO secondi (non si inventa il futuro), quindi la media e'
    (2+4+6+8+10)/5 sui 50 minuti coperti.
    """
    speed = _instant(END, [2.0, 4.0, 6.0, 8.0, 10.0, 10.0])
    means = [(END, 3.0), (END - timedelta(hours=1), 3.0), (END - timedelta(hours=2), 3.0)]
    fields, _ = fs.build_wind_block(
        {"254,0,0": speed, "3,0,3600": means}, {}, END
    )
    assert fields["wind_source_product"] == "254,0,0"
    assert fields["wind_mean_1h_ms"] == pytest.approx(6.0)
    assert fields["wind_mean_1h_coverage_frac"] == pytest.approx(50 / 60, rel=0.01)


def test_average_product_is_used_when_the_instantaneous_series_is_absent():
    """Solo medie orarie: la media pesata e' la media delle medie (costante)."""
    means = [(END, 3.0), (END - timedelta(hours=1), 3.0), (END - timedelta(hours=2), 3.0)]
    fields, _ = fs.build_wind_block({"3,0,3600": means}, {}, END)
    assert fields["wind_source_product"] == "3,0,3600"
    assert fields["wind_period_s"] == 3600
    assert fields["wind_mean_3h_ms"] == pytest.approx(3.0)


def test_unknown_statistic_type_is_ignored_and_declared():
    """Il tipo `200` non e' documentato: si ignora invece di indovinare."""
    speed = [(END, 5.0)]
    _, stats = fs.build_wind_block({"200,0,900": speed}, {}, END)
    assert "200,0,900" in stats["ignored_stats"]


# ---------------------------------------------------------------------------
# invariante: massimo su 3 h >= ultimo valore
# ---------------------------------------------------------------------------
def test_max_3h_is_never_below_the_last_instantaneous_value():
    """Il massimo su 3 h non puo' essere minore di un valore dentro la finestra.

    Caso reale del 2026-11-23 (arpafvg 45.61048,13.75263): ultimo istantaneo
    2,9 m/s, massimo delle medie orarie 0,9. Con una serie istantanea completa
    l'invariante regge; con una serie istantanea troppo corta il campo NON viene
    pubblicato (non si confronta un massimo di medie con un istantaneo).
    """
    full = _instant(END, [0.5] * 15 + [0.7, 2.9, 2.9])
    fields, _ = fs.build_wind_block({"254,0,0": full}, {}, END, last_speed=2.9)
    assert fields["wind_max_3h_ms"] >= 2.9

    short = _instant(END, [0.5, 0.7, 0.9, 1.2, 2.9])
    means = [(END, 0.9), (END - timedelta(hours=1), 0.7), (END - timedelta(hours=2), 0.5)]
    mixed, _ = fs.build_wind_block(
        {"254,0,0": short, "3,0,3600": means}, {}, END, last_speed=2.9
    )
    assert "wind_max_3h_ms" not in mixed


def test_latest_only_fallback_is_declared():
    """Senza serie si pubblica l'ultimo valore, MARCATO: non e' una media."""
    fields, _ = fs.build_wind_block({}, {}, END, last_speed=4.4, last_gust=11.0)
    assert fields["wind_mean_1h_ms"] == pytest.approx(4.4)
    assert fields["wind_source"] == "latest_only"
    assert "wind_mean_3h_ms" not in fields  # non si inventa una media su 3 h


def test_series_with_a_single_point_falls_back_to_latest_only():
    """Serie presente ma inutilizzabile (un solo istante) -> ripiego marcato.

    Misurato il 2026-11-23 su `mnw`: 217 stazioni con un solo campione
    istantaneo finivano con `wind_source=series` e `wind_mean_1h_ms=null`
    (copertura 0), perdendo il valore che invece il file gia' pubblicava in
    `wind_speed_10m`. Il ripiego lo recupera e lo dichiara.
    """
    single = {"254,0,0": [(END, 6.5)]}
    fields, _ = fs.build_wind_block(single, {}, END, last_speed=6.5)
    assert fields["wind_source"] == "latest_only"
    assert fields["wind_mean_1h_ms"] == pytest.approx(6.5)
    assert "wind_source_product" not in fields
    assert "wind_hourly" not in fields


def test_latest_only_gust_is_published_only_if_the_source_is_hourly():
    """`2,0,900` non e' un massimo orario: non si pubblica come tale."""
    fields, _ = fs.build_wind_block({}, {}, END, last_gust=11.0, last_gust_period_s=900)
    assert "gust_max_1h_ms" not in fields
    hourly, _ = fs.build_wind_block({}, {}, END, last_gust=11.0, last_gust_period_s=3600)
    assert hourly["gust_max_1h_ms"] == pytest.approx(11.0)
    assert hourly["gust_source"] == "latest_only"


# ---------------------------------------------------------------------------
# bucket orari
# ---------------------------------------------------------------------------
def test_wind_hourly_buckets_are_means_of_the_hour():
    """Tre ore di istantanei: un bucket per ora, con la media dell'ora.

    18 campioni ogni 10 minuti a partire da 09:10 (valori 2, poi 4, poi 10).
    Con l'ora locale 09/10/11 e l'allineamento ai :10 i valori attesi sono
    2,0 / 3,7 / 9,0 (l'ora 10 contiene l'ultimo 2,0 dei 10 minuti iniziali).
    Se il bucket fosse una somma o una media GLOBALE (5,3) i numeri sarebbero
    diversi: e' questo che il test distingue.
    """
    speed = _instant(END, [2.0] * 6 + [4.0] * 6 + [10.0] * 6, step_minutes=10)
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    hourly = fields["wind_hourly"]
    assert len(hourly) == 3
    assert [row[1] for row in hourly] == pytest.approx([2.0, 3.7, 9.0])

    gust = [(END - timedelta(hours=1), 6.0), (END, 14.0)]
    gust_fields, _ = fs.build_wind_block({}, {"2,0,3600": gust}, END)
    assert [row[1] for row in gust_fields["gust_hourly"]] == pytest.approx([6.0, 14.0])
