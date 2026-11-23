# -*- coding: utf-8 -*-
"""Cumulate di pioggia dalle SERIE di stazione (`fetch_stations.build_rain_block`).

Perche' questi test esistono: prima della 1.3.0.56 `_latest_value` teneva UNA
sola osservazione per prodotto, quindi un accumulo da 60 s copriva 60 s su 3600
(1,7% della finestra) e una cumulata oraria era impossibile. I casi qui sotto
misurano l'integrale, il clipping, i buchi, il gate fisico e i bucket orari.

I valori attesi sono calcolati a mano nel commento di ogni test: se cambia la
semantica di `ref` (misurata: FINE dell'intervallo) questi test devono rompersi.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "stations"))

import fetch_stations as fs  # noqa: E402

END = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def _series_60s(end, minutes, value, start_offset=0):
    """Serie contigua da 60 s: `ref` = FINE dell'intervallo (misurato).

    Con `start_offset=0` l'ultimo `ref` e' `end`, quindi l'ultimo intervallo
    finisce esattamente dove finisce la finestra.
    """
    return [
        (end - timedelta(minutes=start_offset + i), value)
        for i in reversed(range(minutes))
    ]


# ---------------------------------------------------------------------------
# integrale
# ---------------------------------------------------------------------------
def test_contiguous_60s_for_one_hour_is_exact_sum():
    """60 valori da 0,1 mm/60 s = 6,0 mm esatti in 1 h (rate 6 mm/h)."""
    fields, stats = fs.build_rain_block({"1,0,60": _series_60s(END, 60, 0.1)}, END)
    assert fields["rain_1h_mm"] == pytest.approx(6.0)
    assert fields["rain_1h_coverage_frac"] == 1.0
    assert fields["rain_period_s_used"] == 60
    assert fields["rain_source_product"] == "1,0,60"
    assert fields["rain_window_end"] == "2026-11-23T12:00:00Z"
    assert stats["points_qc_rejected"] == 0


def test_three_hours_of_series_cover_the_3h_window():
    """180 valori da 0,1 mm -> 6,0 mm/ora, 18,0 mm su 3 h."""
    fields, _ = fs.build_rain_block({"1,0,60": _series_60s(END, 180, 0.1)}, END)
    assert fields["rain_1h_mm"] == pytest.approx(6.0)
    assert fields["rain_3h_mm"] == pytest.approx(18.0)
    assert fields["rain_3h_coverage_frac"] == 1.0


def test_interval_overlapping_the_left_edge_is_clipped():
    """Accumulo di 2 h (7200 s) che entra per meta' nella finestra di 1 h.

    Un solo valore da 4,0 mm su [10:00, 12:00]: dentro [11:00, 12:00] ci sta
    meta' dell'intervallo, quindi 2,0 mm. Senza clipping il risultato sarebbe
    4,0 (errore del 100%) e senza la regola "ref = FINE" l'intervallo sarebbe
    [12:00, 14:00] con contributo ZERO.
    """
    fields, _ = fs.build_rain_block({"1,0,7200": [(END, 4.0)]}, END)
    assert fields["rain_1h_mm"] == pytest.approx(2.0)
    assert fields["rain_1h_coverage_frac"] == 1.0


def test_window_is_anchored_at_last_data_not_at_now():
    """Serie che finisce 30 min prima di `now`: la finestra segue il DATO.

    Con la latenza mediana della sorgente (~32 min) ancorare a `now` darebbe
    copertura 50% o meno su quasi tutte le stazioni e la cumulata sarebbe
    sempre null: la finestra si ancora all'ultimo dato disponibile.
    """
    end = datetime(2026, 11, 23, 11, 30, tzinfo=timezone.utc)
    fields, _ = fs.build_rain_block({"1,0,60": _series_60s(end, 60, 0.1)}, END)
    assert fields["rain_1h_mm"] == pytest.approx(6.0)
    assert fields["rain_1h_coverage_frac"] == 1.0
    assert fields["rain_window_end"] == "2026-11-23T11:30:00Z"


# ---------------------------------------------------------------------------
# buchi e copertura
# ---------------------------------------------------------------------------
def test_gap_of_60pct_gives_null_with_reason():
    """24 minuti su 60 coperti (40%) -> null e motivo, non un numero inventato."""
    fields, _ = fs.build_rain_block({"1,0,60": _series_60s(END, 24, 0.1)}, END)
    assert fields["rain_1h_mm"] is None
    assert fields["rain_1h_null_reason"] == "coverage_below_50pct"
    assert fields["rain_1h_coverage_frac"] == pytest.approx(0.4)


def test_gap_of_10pct_keeps_the_value():
    """54 minuti su 60 (90%): la cumulata resta, la copertura la dichiara."""
    fields, _ = fs.build_rain_block({"1,0,60": _series_60s(END, 54, 0.1)}, END)
    assert fields["rain_1h_mm"] == pytest.approx(5.4)
    assert fields["rain_1h_coverage_frac"] == pytest.approx(0.9)


def test_three_hours_with_one_hour_of_series_is_null_with_reason():
    """3 h chieste con 1 h di serie: 33% di copertura -> null + motivo."""
    fields, _ = fs.build_rain_block({"1,0,60": _series_60s(END, 60, 0.1)}, END)
    assert fields["rain_1h_mm"] == pytest.approx(6.0)
    assert fields["rain_3h_mm"] is None
    assert fields["rain_3h_null_reason"] == "coverage_below_50pct"


def test_no_series_writes_no_fields_at_all():
    """Senza serie non si scrive un null finto: il campo e' assente."""
    fields, stats = fs.build_rain_block({}, END)
    assert fields == {}
    assert stats["points_qc_rejected"] == 0


# ---------------------------------------------------------------------------
# gate fisico
# ---------------------------------------------------------------------------
def test_impossible_rate_is_dropped_and_counted():
    """126,6 mm in 60 s = 7.596 mm/h: il record reale di Francavilla di Sicilia.

    Il punto esce dall'integrale (restano 59 da 0,1 = 5,9 mm) e viene CONTATO:
    5,9 e non 6,0, cosi' il test vede davvero lo scarto.
    """
    series = _series_60s(END, 60, 0.1)
    series[-1] = (END - timedelta(minutes=30), 126.6)
    fields, stats = fs.build_rain_block({"1,0,60": series}, END)
    assert fields["rain_1h_mm"] == pytest.approx(5.9)
    assert stats["points_qc_rejected"] == 1


def test_negative_accumulation_is_rejected():
    """Una cumulata negativa non esiste: scartata e contata."""
    series = _series_60s(END, 60, 0.1)
    series[10] = (series[10][0], -1.0)
    fields, stats = fs.build_rain_block({"1,0,60": series}, END)
    assert fields["rain_1h_mm"] == pytest.approx(5.9)
    assert stats["points_qc_rejected"] == 1


# ---------------------------------------------------------------------------
# periodi misti: nessun doppio conteggio
# ---------------------------------------------------------------------------
def test_mixed_periods_do_not_double_count():
    """La stessa ora pubblicata a 15 min e a 60 min vale UNA volta.

    4 quarti d'ora da 1,0 mm (4 mm/h) coprono [11:15, 12:00] meno gli ultimi 15
    minuti di latenza; l'orario da 4,0 mm copre [11:00, 12:00]. Sono lo STESSO
    evento: la cumulata e' 4,0 mm, non 8,0. Il prodotto scelto e' quello con la
    copertura migliore (l'orario, 100% contro 75%), non la somma dei due.
    """
    quarters = [
        (END - timedelta(minutes=15 * (i + 1)), 1.0) for i in range(4)
    ]
    fields, _ = fs.build_rain_block(
        {"1,0,900": quarters, "1,0,3600": [(END, 4.0)]}, END
    )
    assert fields["rain_1h_mm"] == pytest.approx(4.0)
    assert fields["rain_source_product"] == "1,0,3600"


def test_duplicate_points_inside_one_product_are_not_double_counted():
    """Lo stesso punto due volte (record duplicato) non raddoppia la cumulata.

    E' il motivo per cui l'integrale passa da uno sweep a segmenti invece di
    sommare `valore * sovrapposizione` per ogni campione.
    """
    base = _series_60s(END, 60, 0.1)
    duplicated = base + base
    fields, _ = fs.build_rain_block({"1,0,60": duplicated}, END)
    assert fields["rain_1h_mm"] == pytest.approx(6.0)
    assert fields["rain_1h_coverage_frac"] == 1.0


def test_product_is_chosen_by_coverage_not_by_newest_ref():
    """Il prodotto BUCOso ma recente perde contro quello completo.

    `1,0,60` ha un solo punto (recentissimo) e copre 1 minuto su 60; `1,0,900`
    copre l'ora intera. Vince il secondo, anche se il suo `ref` e' piu' vecchio.
    """
    sparse = [(END, 0.5)]
    complete = [(END - timedelta(minutes=60 - 15 * (i + 1)), 1.0) for i in range(4)]
    fields, _ = fs.build_rain_block(
        {"1,0,60": sparse, "1,0,900": complete}, END
    )
    assert fields["rain_source_product"] == "1,0,900"
    assert fields["rain_1h_mm"] == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# bucket orari
# ---------------------------------------------------------------------------
def test_hourly_buckets_cover_the_three_hours_of_series():
    """3 h contigue -> 3 bucket orari pieni da 6,0 mm ciascuno."""
    fields, _ = fs.build_rain_block({"1,0,60": _series_60s(END, 180, 0.1)}, END)
    hourly = fields["rain_hourly"]
    assert len(hourly) == 3
    assert [row[1] for row in hourly] == pytest.approx([6.0, 6.0, 6.0])
    assert hourly[-1][0] == int(datetime(2026, 11, 23, 11, tzinfo=timezone.utc).timestamp())


def test_hourly_bucket_of_a_dry_hour_is_zero_not_missing():
    """Un'ora MISURATA senza pioggia e' 0,0 mm: diverso da un'ora senza dati."""
    series = [(END - timedelta(minutes=i), 0.0) for i in range(1, 61)]
    fields, _ = fs.build_rain_block({"1,0,60": series}, END)
    assert fields["rain_1h_mm"] == pytest.approx(0.0)
    assert fields["rain_hourly"][-1][1] == pytest.approx(0.0)


def test_trange_offset_is_respected():
    """`1,1800,3600`: l'accumulo non finisce al `ref` ma 30 min dopo.

    La finestra segue l'ultimo dato disponibile, quindi con UNA sola serie il
    P2 non produce mai un taglio a valle: si verifica dove si vede davvero,
    cioe' nella costruzione dell'intervallo (unitario). Con P2 ignorato
    l'intervallo sarebbe [11:00, 12:00] e la mezz'ora di sfasamento sparirebbe.

    Gli altri due test di clipping (`interval_overlapping_the_left_edge`,
    `mixed_periods`) coprono il taglio a monte, che si vede nell'integrale.
    """
    start, end = fs._accum_interval(
        datetime(2026, 11, 23, 11, 0, tzinfo=timezone.utc), 3600, 1800
    )
    assert start == datetime(2026, 11, 23, 10, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 23, 11, 30, tzinfo=timezone.utc)
    assert fs.trange_offset_seconds("1,1800,3600") == 1800
