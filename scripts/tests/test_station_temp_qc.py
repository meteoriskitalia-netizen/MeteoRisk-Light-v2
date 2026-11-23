# -*- coding: utf-8 -*-
"""Gate di plausibilita' fisica della temperatura delle centraline.

CASO REALE (misurato, non inventato): il blob `data/stations/daily_series.json`
del 2026-11-23T21:31Z pubblicava `temp_max_day_c = 52,0` per
`dpcn-sicilia:37.20139,13.76111` (Palma di Montechiaro) mentre la temperatura
istantanea era 14,8 degC. La serie reale alterna 43,3 / 40,0 / 52,0 / 15,2 /
15,1 / 46,0 / 48,9 / 47,5 / 14,8 / 47,6 / 43,1 / 42,9 / 40,4 / 40,5 / 14,8:
salti da 15-37 degC in 30-60 minuti. Il vecchio gate `|T| <= 60` non li vede.

Copre:
  * `temp_qc.qc_temperature_points`: rate > 6 degC/h E salto assoluto > 3 degC;
    un salto PLAUSIBILE (+4 degC in un'ora, fohn) NON viene scartato;
  * la temperatura istantanea e le finestre usano solo i campioni accettati;
  * il blob giornaliero: i punti incoerenti vengono azzerati, il bucket orario
    incoerente ricostruito/eliminato, `temp_max_day_c` torna coerente (~27);
  * l'ancora dal blob (`load_temperature_baseline`) quando la finestra di 3 h
    comincia DENTRO il blocco corrotto: senza ancora il primo campione falso
    verrebbe accettato (misurato: 40,5 degC), con l'ancora no.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "stations"))

import build_daily_series as bds  # noqa: E402
import fetch_stations as fs  # noqa: E402
import temp_qc  # noqa: E402

K = 273.15
T0 = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 23, hour, minute, tzinfo=timezone.utc)


def _palma_points() -> list[tuple[datetime, float]]:
    """La serie reale misurata: 24-27 normali, poi il blocco 40-52 e i ritorni."""
    points: list[tuple[datetime, float]] = []
    for index in range(32):  # 00:00 .. 15:00 UTC, 30 min
        ref = T0 + timedelta(minutes=30 * index)
        # curva diurna normale 24-27.5 degC
        value = 24.0 + 3.5 * max(0.0, min(1.0, (index - 12) / 14.0))
        points.append((ref, round(value, 1)))
    spikes = {
        (15, 30): 43.3, (16, 0): 40.0, (16, 28): 52.0, (16, 47): 15.2,
        (17, 0): 15.1, (17, 28): 46.0, (17, 40): 48.9, (18, 1): 47.5,
        (18, 24): 14.8, (18, 36): 47.6, (19, 0): 43.1, (19, 30): 42.9,
        (20, 0): 40.4, (20, 30): 40.5, (21, 0): 14.8,
    }
    for (hour, minute), value in spikes.items():
        points.append((_at(hour, minute), value))
    points.sort(key=lambda item: item[0])
    return points


# ---------------------------------------------------------------------------
# Il gate in se'
# ---------------------------------------------------------------------------
def test_palma_spike_is_rejected_and_max_stays_coherent():
    points = _palma_points()
    kept, rejected, indexes = temp_qc.qc_temperature_points(points)
    values = [value for _ref, value in kept]
    assert rejected >= 10, rejected
    assert max(values) <= 27.5, max(values)
    # i campioni estremi (52,0 e i 40+) non sono in `kept`
    assert all(value < 30 for value in values)
    # il ritorno a 14,8 degC e' accettato: il confronto e' con l'ultimo
    # campione ACCETTATO, non con il picco falso
    assert any(ref.hour == 21 and value == 14.8 for ref, value in kept)


def test_plausible_foehn_plus_four_degrees_in_one_hour_is_kept():
    points = [
        (_at(8, 0), 10.0),
        (_at(9, 0), 14.0),   # +4 degC/h: fohn, deve passare
        (_at(10, 0), 18.5),  # +4.5 degC/h: ancora plausibile
    ]
    kept, rejected, _indexes = temp_qc.qc_temperature_points(points)
    assert rejected == 0
    assert [value for _ref, value in kept] == [10.0, 14.0, 18.5]


def test_small_noise_on_short_step_is_not_a_physical_jump():
    """2 degC in 10 minuti = 12 degC/h, ma il salto assoluto e' sotto 3."""
    points = [(_at(8, 0), 10.0), (_at(8, 10), 12.0)]
    kept, rejected, _indexes = temp_qc.qc_temperature_points(points)
    assert rejected == 0 and len(kept) == 2


def test_gap_over_three_hours_has_no_rate():
    """Con un buco di 4 h il rate non e' valutabile: il campione si accetta."""
    points = [(_at(8, 0), 5.0), (_at(12, 0), 20.0)]
    kept, rejected, _indexes = temp_qc.qc_temperature_points(points)
    assert rejected == 0 and len(kept) == 2


def test_instant_block_uses_only_accepted_samples():
    """La massima della finestra non e' il picco falso: e' quella dei campioni ok."""
    points = _palma_points()
    kept, _rejected, _indexes = temp_qc.qc_temperature_points(points)
    series = {"254,0,0": [(ref, value + K) for ref, value in kept]}
    fields, stats = fs.build_instant_block(
        "temp", series, _at(21, 30), unit="c", decimals=1,
        extremes=("min", "max"), offset=-K, qc_min=-60.0, qc_max=60.0,
    )
    assert fields["temp_max_1h_c"] is not None
    assert fields["temp_max_1h_c"] < 30.0, fields["temp_max_1h_c"]
    assert stats["points_qc_rejected"] == 0


# ---------------------------------------------------------------------------
# Blob giornaliero: riparazione dei punti e dei bucket
# ---------------------------------------------------------------------------
def _tuple(ms: int, temp: float | None) -> list:
    return [ms, temp, None, None, None, None, None, None, None, None, None]


def _daily_doc(points_c: list[tuple[datetime, float]], day: str = "2026-11-23", sid: str = "dpcn-sicilia:37.20139,13.76111") -> dict:
    pts = [_tuple(int(ref.timestamp() * 1000), value) for ref, value in points_c]
    th = bds._buckets_from_points(pts, bds.I_TEMP)
    return {
        "schema": "stations-daily-series/2",
        "day": day,
        "stations": {sid: {"name": "Palma di Montechiaro", "pts": pts, "th": th}},
    }


def test_daily_blob_repairs_the_false_max_and_counts_rejected():
    previous = _daily_doc(_palma_points())
    sid = "dpcn-sicilia:37.20139,13.76111"
    before = previous["stations"][sid]["th"]
    assert max(value[2] for value in before.values()) == 52.0
    doc = bds.build({"stations": []}, previous, int(_at(21, 50).timestamp() * 1000))
    record = doc["stations"][sid]
    assert record["temp_max_day_c"] <= 28.0, record["temp_max_day_c"]
    assert record["temp_max_day_c"] >= 20.0
    assert record.get("temp_qc_rejected", 0) >= 1
    assert all(
        point[bds.I_TEMP] is None or point[bds.I_TEMP] < 35.0
        for point in record["pts"]
    )
    # i bucket orari incoerenti non possono piu' contenere il 52,0
    assert all(
        not isinstance(value, list) or value[2] is None or value[2] < 40.0
        for value in (record.get("th") or {}).values()
    )


def test_daily_blob_keeps_a_plausible_series_untouched():
    points = [
        (T0 + timedelta(minutes=30 * index), round(8.0 + 10.0 * index / 24.0, 1))
        for index in range(24)
    ]
    previous = _daily_doc(points)
    sid = "dpcn-sicilia:37.20139,13.76111"
    doc = bds.build({"stations": []}, previous, int(_at(12, 0).timestamp() * 1000))
    record = doc["stations"][sid]
    assert not record.get("temp_qc_rejected")
    assert record["temp_max_day_c"] == pytest.approx(17.6, abs=0.2)


# ---------------------------------------------------------------------------
# Ancora dal blob giornaliero (finestra che comincia dentro il blocco corrotto)
# ---------------------------------------------------------------------------
def test_load_temperature_baseline_filters_the_bad_points(tmp_path):
    previous = _daily_doc(_palma_points())
    path = tmp_path / "daily_series.json"
    path.write_text(json.dumps(previous), encoding="utf-8")
    baseline = fs.load_temperature_baseline(path)
    points = baseline["dpcn-sicilia:37.20139,13.76111"]
    assert points
    assert all(value < 30.0 for _ref, value in points)
    assert points[-1][1] == pytest.approx(14.8)


def _payload_from(points: list[tuple[datetime, float]]) -> dict:
    return {
        "data": [{
            "stat": {
                "net": "dpcn-sicilia",
                "lat": 37.20139,
                "lon": 13.76111,
                "details": [{"var": "B01019", "val": "Palma di Montechiaro"}],
            },
            "prod": [{
                "var": "B12101",
                "lev": "103,2000,0,0",
                "trange": "254,0,0",
                "val": [
                    {"val": value + K, "ref": ref.strftime("%Y-%m-%dT%H:%M:%S")}
                    for ref, value in points
                ],
            }],
        }]
    }


def test_normalize_payload_baseline_rejects_the_corrupted_window():
    """La finestra 19:00-21:30 comincia DENTRO il blocco: serve l'ancora."""
    points = [item for item in _palma_points() if item[0] >= _at(19, 0)]
    payload = _payload_from(points)
    generated = _at(22, 0)
    networks = json.loads(
        (_HERE.parent / "stations" / "networks.json").read_text(encoding="utf-8")
    )["networks"]
    sid = "dpcn-sicilia:37.20139,13.76111"
    anchor = {"dpcn-sicilia:37.20139,13.76111": [(_at(18, 24), 14.8)]}
    doc = fs.normalize_payload(
        payload, networks, generated_at=generated,
        max_age_minutes=180, temp_baseline=anchor,
    )
    record = next(item for item in doc["stations"] if item["id"] == sid)
    assert record["temperature_2m"] == pytest.approx(14.8, abs=0.1)
    # la finestra 1 h ha un solo campione accettato (21:00) e puo' restare
    # sotto copertura: cio' che conta e' che NON ci sia piu' 40,5
    assert record.get("temp_max_1h_c") is None or record["temp_max_1h_c"] <= 15.0
    assert record.get("temp_qc_rejected", 0) >= 1

    # senza ancora il primo campione (43,1 all'inizio della finestra) e' preso
    # per buono: e' il motivo per cui l'ancora esiste, non un ripiego cosmetico.
    without = fs.normalize_payload(
        payload, networks, generated_at=generated, max_age_minutes=180,
    )
    record_without = next(item for item in without["stations"] if item["id"] == sid)
    assert record_without["temperature_2m"] > 40.0
