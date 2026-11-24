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


def test_wind_max_3h_is_not_published_anymore():
    """1.3.0.70 - il «vento massimo medio» NON si pubblica piu'.

    Della serie del vento resta la MEDIA (`wind_mean_*`); il massimo del vento e'
    la RAFFICA (`gust_max_*`), che non cambia. Il campo `wind_max_3h_ms` non esiste
    piu' in `fields` (verificato anche nel client: zero occorrenze di `windMax3h`).
    """
    speed = _instant(END, [2.0] * 15 + [4.0, 6.0, 10.0])
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert "wind_max_3h_ms" not in fields
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
def test_wind_max_3h_is_never_published():
    """1.3.0.70 - il campo del «vento massimo» non esiste piu' in nessun caso.

    Il caso reale del 2026-11-23 (arpafvg 45.61048,13.75263: ultimo istantaneo
    2,9 m/s, massimo delle medie orarie 0,9) era il motivo per cui il campo si
    pubblicava solo da serie istantanee. Ora non si pubblica mai: il massimo del
    vento e' la RAFFICA (`gust_max_3h_ms`), che resta invariata.
    """
    full = _instant(END, [0.5] * 15 + [0.7, 2.9, 2.9])
    fields, _ = fs.build_wind_block({"254,0,0": full}, {}, END, last_speed=2.9)
    assert "wind_max_3h_ms" not in fields
    assert fields["wind_mean_3h_ms"] is not None

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


# ---------------------------------------------------------------------------
# 1.3.0.61: finestra da 30 minuti
# ---------------------------------------------------------------------------
def test_wind_mean_30m_needs_a_300s_step():
    """Passo 300 s: media a 30 min calcolabile; passo 600 s: null con motivo."""
    fine = _instant(END, [2.0] * 24 + [6.0] * 12, step_minutes=5)
    fields, _ = fs.build_wind_block({"254,0,0": fine}, {}, END)
    assert fields["wind_mean_30m_ms"] is not None
    assert fields["wind_mean_30m_ms"] is not None

    coarse = _instant(END, [2.0] * 12 + [6.0] * 6, step_minutes=10)
    fields, _ = fs.build_wind_block({"254,0,0": coarse}, {}, END)
    assert "wind_mean_30m_coverage_frac" not in fields
    assert fields["wind_mean_30m_ms"] is None
    assert fields["wind_mean_30m_null_reason"] == "series_too_coarse_for_30min"


def test_gust_max_30m_requires_an_hourly_or_finer_maximum():
    """Un massimo da 15 min sta nella finestra, uno orario no."""
    sub_hourly = [(END - timedelta(minutes=30), 5.0), (END, 7.0)]
    fields, _ = fs.build_wind_block({}, {"2,0,900": sub_hourly}, END)
    assert fields["gust_max_30m_ms"] == pytest.approx(7.0)

    hourly = [(END - timedelta(hours=1), 5.0), (END, 7.0)]
    fields, _ = fs.build_wind_block({}, {"2,0,3600": hourly}, END)
    assert fields["gust_max_30m_ms"] is None
    assert fields["gust_max_30m_null_reason"] == "series_too_coarse_for_30min"


# ---------------------------------------------------------------------------
# raffica STIMATA dal massimo della serie del vento
#
# Soglia 60 s, non 300: misurato il 2026-11-24 su 1.758 coppie stazione-ora, il
# massimo di una serie a 10-15 min vale il 19-69% della raffica B11041 della
# stessa stazione (Piemonte 0,22, Calabria 0,19, Campania 0,58, Bolzano 0,60,
# Veneto 0,69, Liguria 0,67), a 60 s vale 1,24 (Puglia). Da una serie rada la
# "stima" sbaglia di 1,4-5x: si pubblica null con il motivo.
# ---------------------------------------------------------------------------
def test_gust_estimate_is_the_max_of_a_one_minute_wind_series():
    """Serie a 60 s con picco: la stima e' il MASSIMO, non la media.

    Il picco e' l'ULTIMO campione (ref = END): il suo intervallo ha durata zero
    (`_instant_intervals` non inventa futuro) e `_window_max` lo scarterebbe.
    La stima legge i `ref`, quindi lo vede.
    """
    speed = _instant(END, [2.0] * 180 + [13.0], step_minutes=1)
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["gust_est_1h_ms"] == pytest.approx(13.0)
    assert fields["gust_est_3h_ms"] == pytest.approx(13.0)
    assert fields["gust_est_period_s"] == 60
    assert fields["gust_est_source"] == "max_of_wind_series"
    assert fields["gust_est_1h_coverage_frac"] > 0.9
    # la media della stessa finestra e' 2,0: la stima non e' una media
    assert fields["gust_est_1h_ms"] > fields["wind_mean_1h_ms"]


def test_gust_estimate_is_null_when_the_series_is_not_one_minute():
    """Serie a 900 s: null con `series_too_coarse_for_gust_estimate`."""
    speed = _instant(END, [2.0] * 13, step_minutes=15)
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["gust_est_1h_ms"] is None
    assert fields["gust_est_1h_null_reason"] == "series_too_coarse_for_gust_estimate"
    assert fields["gust_est_3h_ms"] is None
    assert fields["gust_est_3h_null_reason"] == "series_too_coarse_for_gust_estimate"
    assert "gust_est_period_s" not in fields
    assert "gust_est_source" not in fields
    # la MEDIA resta calcolabile: la soglia e' della stima, non del vento
    assert fields["wind_mean_1h_ms"] is not None


def test_a_300s_series_is_too_coarse_for_the_estimate():
    """Il passo di 300 s NON basta piu' (era la soglia sbagliata).

    La media a 30 min resta disponibile: e' solo la stima a non essere
    calcolabile, perche' a 300 s il massimo della serie e' un numero che
    sottostima la raffica di 1,4-5x.
    """
    speed = _instant(END, [2.0] * 37, step_minutes=5)
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["gust_est_1h_ms"] is None
    assert fields["gust_est_1h_null_reason"] == "series_too_coarse_for_gust_estimate"
    assert fields["wind_mean_30m_ms"] is not None


def test_gust_estimate_never_uses_averaged_wind():
    """Solo medie orarie: il massimo di medie non e' un massimo -> null."""
    means = [(END - timedelta(hours=3 - i), 3.0) for i in range(4)]
    fields, _ = fs.build_wind_block({"3,0,3600": means}, {}, END)
    assert fields["gust_est_1h_ms"] is None
    assert fields["gust_est_1h_null_reason"] == "series_too_coarse_for_gust_estimate"


def test_gust_estimate_needs_half_of_the_window():
    """24 campioni a 60 s = 23 min su 60 (38%): null con il motivo."""
    speed = _instant(END, [5.0] * 24, step_minutes=1)
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["gust_est_1h_ms"] is None
    assert fields["gust_est_1h_null_reason"] == "coverage_below_50pct"


def test_gust_estimate_is_never_below_the_mean_wind():
    """Invariante: il massimo della serie >= media pesata della stessa serie."""
    speed = _instant(END, [1.0] * 30 + [8.0] * 30 + [3.0], step_minutes=1)
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {}, END)
    assert fields["wind_mean_1h_ms"] == pytest.approx(4.5)
    assert fields["gust_est_1h_ms"] == pytest.approx(8.0)
    assert fields["gust_est_1h_ms"] >= fields["wind_mean_1h_ms"]


def test_gust_estimate_does_not_replace_the_measured_gust():
    """Dove c'e' la raffica misurata, la stima NON la sostituisce: convivono."""
    speed = _instant(END, [2.0] * 180 + [9.0], step_minutes=1)
    gust = [(END - timedelta(hours=1), 12.0), (END, 18.0)]
    fields, _ = fs.build_wind_block({"254,0,0": speed}, {"2,0,3600": gust}, END)
    assert fields["gust_max_1h_ms"] == pytest.approx(18.0)
    assert fields["gust_est_1h_ms"] == pytest.approx(9.0)
    assert fields["gust_est_source"] == "max_of_wind_series"


def _doc_with_station(station):
    return {
        "attribution": [{"network": "x", "text": "x", "url": None}],
        "stations": [station],
    }


def test_validate_document_enforces_the_estimate_gate_and_the_mean():
    """Il gate (0..75 m/s), l'invariante sulla media e il passo dichiarato."""
    base = {
        "id": "x:1,1",
        "network": "x",
        "lat": 1.0,
        "lon": 1.0,
        "obs_time": "2026-11-23T12:00:00Z",
        "wind_speed_10m": 3.0,
        "wind_mean_1h_ms": 5.0,
        "gust_est_1h_ms": 6.0,
        "gust_est_period_s": 60,
        "gust_est_source": "max_of_wind_series",
    }
    ok = fs.validate_document(_doc_with_station(dict(base)), 1, 1_000_000)
    assert [p for p in ok if "gust_est" in p] == []

    below = fs.validate_document(
        _doc_with_station(dict(base, gust_est_1h_ms=4.0)), 1, 1_000_000
    )
    assert any("sotto la media" in p for p in below)

    over = fs.validate_document(
        _doc_with_station(dict(base, gust_est_1h_ms=80.0)), 1, 1_000_000
    )
    assert any("fuori gate" in p for p in over)

    coarse = fs.validate_document(
        _doc_with_station(dict(base, gust_est_period_s=900)), 1, 1_000_000
    )
    assert any("gust_est_period_s" in p for p in coarse)

