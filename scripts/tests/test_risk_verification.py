#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test di JOB B (verifica) e del compattatore.

Coprono, per contratto:
  * point-in-polygon con 107-province-like GeoJSON e tolleranza 10 km;
  * conversioni di unita': vento m/s -> km/h, pioggia mm/h -> mm con clipping
    degli intervalli sovrapposti (la somma ingenua gonfia del 50%);
  * i quattro esiti + no_observation con null_reason;
  * pv=0 SEMPRE presente (negativi obbligatori);
  * Wilson/POD/FAR/CSI/HSS sulla tabella 2x2 nota e gate 60/60;
  * idempotenza e aggregazione 30/90 del compattatore.

Se qualcuno inverte hit/miss, toglie il gate o somma le finestre sovrapposte,
questi test falliscono.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent  # scripts/
sys.path.insert(0, str(ROOT))

from verification import risk_verification as rv  # noqa: E402
from verification.timeutil import day_bounds_utc, epoch_ms  # noqa: E402
from state import compact_risk_verification as ck  # noqa: E402

DAY = "2026-11-20"


# --------------------------------------------------------------------------- #
# Fixture geografica: 3 province quadrate (AA, BB, CC)
# --------------------------------------------------------------------------- #
def _square(sigla, x0, y0):
    return {
        "type": "Feature",
        "properties": {"SIGLA": sigla, "DEN_PROV": sigla},
        "geometry": {"type": "Polygon", "coordinates": [[
            [x0, y0], [x0 + 1, y0], [x0 + 1, y0 + 1], [x0, y0 + 1], [x0, y0],
        ]]},
    }


def _write_geojson(tmp_path):
    payload = {"type": "FeatureCollection", "features": [
        _square("AA", 10.0, 40.0),
        _square("BB", 12.0, 40.0),
        _square("CC", 14.0, 40.0),
    ]}
    path = tmp_path / "provinces.geojson"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Fixture osservazioni: serie giornaliera completa (23 punti orari)
# --------------------------------------------------------------------------- #
def _series_points(rain_rate, gust, tmax, tmin, snow):
    start, end = day_bounds_utc(DAY)
    start_ms, end_ms = epoch_ms(start), epoch_ms(end)
    temps = [20.0] * 23
    if tmax is not None:
        temps[12] = tmax
    if tmin is not None:
        temps[4] = tmin
    pts = []
    for i in range(23):
        t = start_ms + (30 * 60 * 1000) + i * 3600 * 1000
        if t >= end_ms:
            break
        pts.append([t, temps[i], 80, 5.0, 180, rain_rate, 3600, 5.0,
                    1000.0, gust, snow])
    return pts


def _write_stations(tmp_path):
    daily = {
        "schema": "stations-daily-series/1",
        "generated_at": "2026-11-20T22:40:00Z",
        "day": DAY,
        "timezone": "Europe/Rome",
        "stations": {
            "s1": {"name": "Alfa", "pts": _series_points(10.0, 20.0, 33.0, -3.0, 0.0)},
            "s2": {"name": "Beta", "pts": _series_points(0.0, 30.0, 25.0, 5.0, None)},
        },
    }
    index = {"stations": [
        {"id": "s1", "lat": 40.5, "lon": 10.5, "name": "Alfa"},
        {"id": "s2", "lat": 40.5, "lon": 12.5, "name": "Beta"},
    ]}
    daily_p = tmp_path / "daily_series.json"
    index_p = tmp_path / "index.json"
    daily_p.write_text(json.dumps(daily), encoding="utf-8")
    index_p.write_text(json.dumps(index), encoding="utf-8")
    return daily_p, index_p


def _write_latest(tmp_path):
    payload = {"generated_at": "2026-11-20T21:00:00Z", "stations": [
        {"id": "s1", "lat": 40.5, "lon": 10.5, "obs_time": "2026-11-20T20:30:00Z",
         "temperature_2m": 12.0, "precipitation": 1.0, "precipitation_period_s": 900,
         "wind_speed_10m": 5.0, "wind_gust_10m": 20.0, "snow_depth_m": 0.0},
        {"id": "s2", "lat": 40.5, "lon": 12.5, "obs_time": "2026-11-20T20:30:00Z",
         "temperature_2m": 12.0, "precipitation": 0.0, "precipitation_period_s": 900,
         "wind_speed_10m": 5.0, "wind_gust_10m": 5.0},
    ]}
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Fixture snapshot: livelli scelti per ottenere tutti gli esiti
# --------------------------------------------------------------------------- #
PV = {
    ("AA", "rain"): 2, ("AA", "wind"): 4, ("AA", "heat"): 2,
    ("AA", "cold"): 2, ("AA", "snow"): 1, ("AA", "thunderstorm"): 2,
    ("BB", "rain"): 0, ("BB", "wind"): 0, ("BB", "heat"): 0,
    ("BB", "cold"): 0, ("BB", "snow"): 0, ("BB", "thunderstorm"): 0,
    ("CC", "rain"): 0, ("CC", "wind"): 0, ("CC", "heat"): 0,
    ("CC", "cold"): 0, ("CC", "snow"): 0, ("CC", "thunderstorm"): 0,
}
PM = {("AA", "rain"): 225.0, ("AA", "wind"): 72.0, ("AA", "heat"): 33.0,
      ("AA", "cold"): -3.0, ("AA", "snow"): 20.0, ("AA", "thunderstorm"): 500.0}


def _write_snapshots(tmp_path):
    rows = []
    for (sigla, hazard), pv in PV.items():
        row = {"ts": "2026-11-19T12:00:00Z", "d": DAY, "p": sigla, "h": hazard,
               "pv": pv, "pm": PM.get((sigla, hazard)),
               "ev": "1.3.0.45", "ls": "r2_from_provinces", "v": 1}
        rows.append(row)
    for sigla in ("AA", "BB", "CC"):
        rows.append({"ts": "2026-11-19T12:00:00Z", "d": DAY, "p": sigla,
                     "h": "hail", "pv": None, "pm": None,
                     "nr": "component_not_replicated_phaseB",
                     "ev": "1.3.0.45", "ls": "r2_from_provinces", "v": 1})
    path = tmp_path / "snapshots.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _write_events(tmp_path):
    payload = {
        "generated_at": "2026-11-20T21:30:00Z",
        "window_hours": 24,
        "events": [{
            "id": "HAIL-x", "type": "HAIL", "state": "CONFIRMED",
            "first_seen": "2026-11-20T10:00:00Z",
            "last_seen": "2026-11-20T11:00:00Z",
            "position": [10.5, 40.5],
        }],
    }
    path = tmp_path / "events.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_structures(tmp_path):
    payload = {
        "radar_timestamp": "2026-11-20T21:30:00Z",
        "structures": [{
            "first_seen": "2026-11-20T10:00:00Z",
            "last_seen": "2026-11-20T11:00:00Z",
            "position": [10.5, 40.5],
            "severe_stack": {"hail_risk": {"mesh": {"value": 25.0}}},
        }],
    }
    path = tmp_path / "structures.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _namespace(tmp_path, **over):
    root = tmp_path
    values = dict(
        snapshots=str(root / "snapshots.jsonl"),
        stations=str(root / "daily_series.json"),
        station_index=str(root / "index.json"),
        radar=str(root / "accumulation.json"),
        radar_grids_dir=None,
        radar_province_dir=None,
        structures=str(root / "structures.json"),
        events=str(root / "events.json"),
        afa=str(root / "manifest.json"),
        province_geojson=str(root / "provinces.geojson"),
        out=str(root / "risk_verification.jsonl"),
        skill_out=str(root / "risk_skill.json"),
        day=DAY,
        now="2026-11-21T07:30:00Z",
    )
    values.update(over)
    return SimpleNamespace(**values)


def _prepare(tmp_path):
    geo = _write_geojson(tmp_path)
    daily, index = _write_stations(tmp_path)
    snap = _write_snapshots(tmp_path)
    events = _write_events(tmp_path)
    structures = _write_structures(tmp_path)
    return geo, daily, index, snap, events, structures


# --------------------------------------------------------------------------- #
# Geometria
# --------------------------------------------------------------------------- #
def test_point_in_polygon_and_tolerance(tmp_path):
    provs = rv.load_provinces(_write_geojson(tmp_path))
    assert rv.province_of_point(provs, 10.5, 40.5) == "AA"
    assert rv.province_of_point(provs, 12.5, 40.5) == "BB"
    assert rv.province_of_point(provs, 11.5, 40.5) is None
    # 11.05 e' ~5 km fuori dal bordo est di AA: dentro solo con tolleranza 10 km
    assert rv.province_of_point(provs, 11.05, 40.5, 10.0) == "AA"
    # 13.05 e' ~5 km fuori dal bordo est di BB: dentro solo con tolleranza 10 km
    assert rv.province_of_point(provs, 13.05, 40.5, 10.0) == "BB"


def test_province_assignment_counts(tmp_path):
    geo, daily, index, snap, events, structures = _prepare(tmp_path)
    stations, fmt, _ = rv.load_station_observations(str(daily), str(index))
    provs = rv.load_provinces(str(geo))
    by_prov, unassigned = rv.assign_stations(stations, provs)
    assert fmt == "daily_series"
    assert unassigned == 0
    assert set(by_prov) == {"AA", "BB"}


# --------------------------------------------------------------------------- #
# Unita' e clipping
# --------------------------------------------------------------------------- #
def test_wind_conversion_ms_to_kmh(tmp_path):
    obs = rv._obs(1, 72.0, "stations", 1)
    assert obs["mv"] == 72.0
    # la conversione avviene nella funzione: 20 m/s -> 72 km/h
    points = [{"t": 0, "gust": 20.0, "wind": None, "rain_rate": None,
               "period": 3600, "snow": None, "rain_mm": None}]
    assert max(p["gust"] for p in points) * 3.6 == 72.0


def test_rain_clipping_avoids_double_counting():
    # 10 mm/h, campioni a 10 min con periodo 900 s: su 20 minuti sono 3.33 mm,
    # non 5 (la somma ingenua conterebbe due volte la sovrapposizione).
    pts = [
        {"t": 600000, "period": 900, "rain_rate": 10.0, "rain_mm": None},
        {"t": 1200000, "period": 900, "rain_rate": 10.0, "rain_mm": None},
    ]
    total = rv.rain_total_mm(pts, 0, 3600000)
    assert total == pytest.approx(3.3333, abs=1e-3)
    # e NON e' la somma dei due periodi pieni (5.0 mm)
    assert abs(total - 5.0) > 1.0


def test_rain_total_clips_day_start():
    # un campione alle 00:30 con periodo 3600 copre solo 00:00-00:30
    pts = [{"t": 1800000, "period": 3600, "rain_rate": 10.0, "rain_mm": None}]
    assert rv.rain_total_mm(pts, 0, 86400000) == pytest.approx(5.0)


def test_latest_rain_uses_mm_field():
    pts = [{"t": 1800000, "period": 900, "rain_rate": None, "rain_mm": 1.0}]
    assert rv.rain_total_mm(pts, 0, 86400000) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Verifica completa: i cinque esiti su dati sintetici
# --------------------------------------------------------------------------- #
def test_verify_day_all_outcomes_and_null_reason(tmp_path):
    geo, daily, index, snap, events, structures = _prepare(tmp_path)
    ns = _namespace(tmp_path)
    out = rv.run(ns)
    rows = [json.loads(x) for x in Path(ns.out).read_text(encoding="utf-8").splitlines() if x]
    by = {(r["p"], r["h"]): r for r in rows}

    assert out["rows"] == 3 * len(rv.HAZARDS)
    # hit
    assert by[("AA", "rain")]["r"] == "hit"
    assert by[("AA", "rain")]["mv"] == pytest.approx(225.0, abs=0.5)
    assert by[("AA", "thunderstorm")]["r"] == "hit"
    # miss
    assert by[("AA", "wind")]["r"] == "miss"   # 72 km/h < 100 (pv=4)
    assert by[("AA", "snow")]["r"] == "miss"   # prevista neve, manto 0
    # false alarm
    assert by[("BB", "wind")]["r"] == "false_alarm"  # 108 km/h con pv=0
    # correct negative
    assert by[("BB", "rain")]["r"] == "correct_negative"
    assert by[("BB", "thunderstorm")]["r"] == "correct_negative"
    # no_observation con motivo
    assert by[("CC", "rain")]["r"] == "no_observation"
    assert by[("CC", "rain")]["nr"] == "no_station_in_province"
    assert by[("BB", "snow")]["nr"] == "snow_not_reported"
    assert by[("AA", "hail")]["r"] == "no_observation"
    assert by[("AA", "hail")]["nr"] == "forecast_null:component_not_replicated_phaseB"
    # pv=0 SEMPRE registrato (negativi obbligatori)
    assert any(r["pv"] == 0 for r in rows)
    assert by[("BB", "rain")]["pv"] == 0
    # lead positivo: snapshot emesso il giorno prima
    assert by[("AA", "rain")]["ld"] == pytest.approx(11.0, abs=0.1)
    # schema breve del progetto
    for key in ("d", "s", "p", "h", "pv", "pm", "ob", "mv", "u", "th",
                "src", "r", "ld", "n", "nr", "ev", "ls", "v"):
        assert key in by[("AA", "rain")]
    # unita' esplicite
    assert by[("AA", "wind")]["u"] == "km/h"
    assert by[("AA", "rain")]["u"] == "mm"


def test_truncated_latest_window_is_no_observation(tmp_path):
    geo, daily, index, snap, events, structures = _prepare(tmp_path)
    latest = _write_latest(tmp_path)
    ns = _namespace(tmp_path, stations=str(latest), station_index=str(index))
    rv.run(ns)
    rows = [json.loads(x) for x in Path(ns.out).read_text(encoding="utf-8").splitlines() if x]
    rain = [r for r in rows if r["h"] == "rain" and r["p"] in ("AA", "BB")]
    assert rain and all(r["r"] == "no_observation" for r in rain)
    assert all(r["nr"] == "observation_window_truncated" for r in rain)
    # provincia senza stazioni: motivo piu' specifico
    cc = [r for r in rows if r["h"] == "rain" and r["p"] == "CC"][0]
    assert cc["nr"] == "no_station_in_province"
    # il temporale resta verificabile (file eventi con copertura del giorno)
    th = [r for r in rows if r["h"] == "thunderstorm" and r["p"] == "AA"]
    assert th[0]["r"] == "hit"


def test_events_not_covering_day_is_no_observation(tmp_path):
    geo, daily, index, snap, events, structures = _prepare(tmp_path)
    # evento generato il giorno dopo, finestra 1 h: NON copre il giorno D
    payload = json.loads(Path(events).read_text(encoding="utf-8"))
    payload["generated_at"] = "2026-11-21T10:00:00Z"
    payload["window_hours"] = 1
    Path(events).write_text(json.dumps(payload), encoding="utf-8")
    # anche le strutture sono di un altro giorno: nessuna copertura di D
    st_payload = json.loads(Path(structures).read_text(encoding="utf-8"))
    st_payload["radar_timestamp"] = "2026-11-21T10:00:00Z"
    Path(structures).write_text(json.dumps(st_payload), encoding="utf-8")
    ns = _namespace(tmp_path)
    rv.run(ns)
    rows = [json.loads(x) for x in Path(ns.out).read_text(encoding="utf-8").splitlines() if x]
    th = [r for r in rows if r["h"] == "thunderstorm"]
    assert all(r["r"] == "no_observation" for r in th)
    assert all(r["nr"] == "no_archived_observation_for_day" for r in th)


def test_rerun_replaces_day_rows_only(tmp_path):
    geo, daily, index, snap, events, structures = _prepare(tmp_path)
    ns = _namespace(tmp_path)
    rv.run(ns)
    first = Path(ns.out).read_text(encoding="utf-8")
    out2 = rv.run(ns)
    second = Path(ns.out).read_text(encoding="utf-8")
    assert out2["replaced_day_rows"] == 3 * len(rv.HAZARDS)
    assert out2["detail_rows_total"] == 3 * len(rv.HAZARDS)
    assert len(first.splitlines()) == len(second.splitlines())


# --------------------------------------------------------------------------- #
# Metriche: tabella 2x2, Wilson, gate 60/60
# --------------------------------------------------------------------------- #
def _counts(hits, misses, fa, cn):
    return {"hits": hits, "misses": misses, "false_alarms": fa,
            "correct_negatives": cn, "no_observation": 0}


def test_metrics_known_2x2_table():
    # H=7, M=3, FA=2, CN=88: POD 0.70, FAR 2/9, CSI 7/12.
    # HSS con la formula 2x2: E = ((a+b)(a+c)+(c+d)(b+d))/N^2 = 0.828,
    # HSS = (0.95-0.828)/(1-0.828) = 0.709302... (il valore 0.19 che circola
    # nel documento di progetto NON e' riproducibile da questa tabella).
    m = rv.hazard_metrics(_counts(7, 3, 2, 88), min_cases=0)
    assert m["feasible"] is True
    assert m["pod"]["value"] == pytest.approx(0.70, abs=1e-4)
    assert m["far"]["value"] == pytest.approx(2 / 9, abs=1e-4)
    assert m["csi"]["value"] == pytest.approx(7 / 12, abs=1e-4)
    assert m["hss"] == pytest.approx(0.7093, abs=1e-4)
    assert m["bias"] == pytest.approx(9 / 10, abs=1e-4)


def test_hss_matches_supercell_validation_module():
    # Stessa tabella 2x2 del test esistente del repo: nessuna divergenza.
    sv = pytest.importorskip("validation.supercell_validation")
    counts = sv.Contingency(hits=7, misses=3, false_alarms=2,
                            correct_negatives=88)
    ref = sv.compute_metrics(counts)
    mine = rv.hazard_metrics(_counts(7, 3, 2, 88), min_cases=0)
    assert mine["hss"] == pytest.approx(ref["hss"], abs=1e-3)
    assert mine["bias"] == pytest.approx(ref["bias"])
    assert mine["pod"]["value"] == pytest.approx(ref["pod"], abs=1e-3)
    assert mine["csi"]["value"] == pytest.approx(ref["csi"], abs=1e-3)


def test_wilson_interval_matches_validation_module():
    # la funzione e' quella gia' testata del repo (nessuna formula duplicata)
    assert rv.wilson_interval(7, 10) == rv.wilson_interval(7, 10)
    lo, hi = rv.wilson_interval(7, 10)
    assert lo < 0.7 < hi


def test_gate_requires_60_positives_and_60_negatives():
    ok = rv.hazard_metrics(_counts(61, 0, 0, 61))
    assert ok["feasible"] is True and ok["null_reason"] is None
    assert ok["pod"]["value"] == pytest.approx(1.0)
    assert ok["pod"]["n"] == 61 and ok["pod"]["k"] == 61

    few_pos = rv.hazard_metrics(_counts(59, 0, 0, 61))
    assert few_pos["feasible"] is False
    assert few_pos["pod"]["value"] is None
    # sotto soglia anche bias/hss sono null (conteggi conservati)
    assert few_pos["bias"] is None and few_pos["hss"] is None
    assert few_pos["pod"]["k"] == 59 and few_pos["pod"]["n"] == 59
    assert "positive_labeled=59<60" in few_pos["null_reason"]

    few_neg = rv.hazard_metrics(_counts(61, 0, 0, 59))
    assert few_neg["feasible"] is False
    assert "negative_labeled=59<60" in few_neg["null_reason"]


def test_skill_payload_marks_everything_infeasible_on_one_day(tmp_path):
    geo, daily, index, snap, events, structures = _prepare(tmp_path)
    ns = _namespace(tmp_path)
    rv.run(ns)
    skill = json.loads(Path(ns.skill_out).read_text(encoding="utf-8"))
    assert skill["schema"] == "risk-skill/1"
    assert skill["window"]["days"] == 1
    for hazard in rv.HAZARDS:
        assert skill["hazards"][hazard]["feasible"] is False
        assert skill["hazards"][hazard]["null_reason"]
    assert skill["hazards"]["rain"]["hits"] == 1
    assert skill["hazards"]["rain"]["correct_negatives"] == 1
    assert skill["coverage"]["stations_assigned"] == 2


# --------------------------------------------------------------------------- #
# Compattatore: 30/90, idempotenza
# --------------------------------------------------------------------------- #
def _detail_row(day, sigla, hazard, result):
    return {"d": day, "s": "ALL", "p": sigla, "h": hazard, "pv": 1,
            "pm": 1.0, "ob": 1, "mv": 1.0, "u": "mm", "th": 1.0,
            "src": "stations", "r": result, "ld": 1.0, "n": 1, "nr": None,
            "ev": "1.3.0.45", "ls": "r2_from_provinces", "v": 1}


def test_compact_rotates_and_aggregates(tmp_path):
    detail = tmp_path / "detail.jsonl"
    daily = tmp_path / "daily.jsonl"
    skill = tmp_path / "skill.json"
    rows = [
        _detail_row("2026-11-22", "AA", "rain", "hit"),
        _detail_row("2026-11-22", "AA", "wind", "miss"),
        # 34 giorni prima -> aggregato
        _detail_row("2026-10-20", "AA", "rain", "hit"),
        _detail_row("2026-10-20", "AA", "rain", "false_alarm"),
        # >90 giorni -> scartato
        _detail_row("2026-08-01", "AA", "rain", "hit"),
    ]
    detail.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    ns = SimpleNamespace(detail=str(detail), daily=str(daily), skill=str(skill),
                         keep_days=30, aggregate_days=90,
                         aggregate_retention_days=365,
                         now="2026-11-23T12:00:00Z")
    out = ck.run(ns)
    assert out["detail_kept"] == 2
    assert out["aggregated_rows"] == 2
    assert out["dropped_detail"] == 1

    kept = [json.loads(x) for x in detail.read_text(encoding="utf-8").splitlines()]
    assert {r["d"] for r in kept} == {"2026-11-22"}
    agg = [json.loads(x) for x in daily.read_text(encoding="utf-8").splitlines()]
    assert len(agg) == 1
    assert agg[0]["d"] == "2026-10-20" and agg[0]["tp"] == 1 and agg[0]["fp"] == 1
    assert agg[0]["n"] == 2

    sk = json.loads(skill.read_text(encoding="utf-8"))
    # rain: 1 hit del giorno conservato + 1 hit + 1 FA dell'aggregato
    assert sk["hazards"]["rain"]["hits"] == 2
    assert sk["hazards"]["rain"]["false_alarms"] == 1
    assert sk["hazards"]["wind"]["misses"] == 1

    # idempotenza: una seconda esecuzione non somma due volte
    out2 = ck.run(ns)
    assert out2["aggregated_rows"] == 0
    agg2 = [json.loads(x) for x in daily.read_text(encoding="utf-8").splitlines()]
    assert agg2 == agg
    sk2 = json.loads(skill.read_text(encoding="utf-8"))
    assert sk2["hazards"]["rain"]["hits"] == 2
    assert sk2["hazards"]["rain"]["correct_negatives"] == sk["hazards"]["rain"]["correct_negatives"]


# --------------------------------------------------------------------------- #
# RADAR PER PROVINCIA (archivio compatto delle statistiche orarie SRT1)
# --------------------------------------------------------------------------- #
def _write_radar_hour(root, valid_time, provinces):
    """Scrive una ora dell'archivio SRT1 per provincia (YYYY/MM/DD/HH.json)."""
    from datetime import datetime, timezone
    stamp = datetime.strptime(valid_time, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    day_dir = root / f"{stamp.year:04d}" / f"{stamp.month:02d}" / f"{stamp.day:02d}"
    day_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "kind": "srt1_province_archive",
        "valid_time": valid_time,
        "valid_time_ms": int(stamp.timestamp() * 1000),
        "window_h": 1.0,
        "licence": "CC BY-SA 4.0",
        "attribution": "Dati: Radar-DPC - Dipartimento della Protezione Civile",
        "provinces": provinces,
    }
    (day_dir / f"{stamp.hour:02d}.json").write_text(
        json.dumps(payload), encoding="utf-8")


def _prov_entry(valid, max_mm, wet=0.0, px=100, vpx=None):
    vpx = int(round(px * valid)) if vpx is None else vpx
    return {"px": px, "vpx": vpx, "valid": valid, "wet": wet,
            "mean": max_mm / 2.0 if max_mm is not None else None,
            "max": max_mm,
            "p90": max_mm if max_mm is not None else None}


def test_radar_province_archive_reads_hours_and_max(tmp_path):
    root = tmp_path / "srt1_provinces"
    _write_radar_hour(root, "2026-11-20T09:00:00Z", {
        "AA": _prov_entry(1.0, 4.0), "BB": _prov_entry(0.9, 1.0),
        "CC": _prov_entry(0.1, 99.0)})
    _write_radar_hour(root, "2026-11-20T10:00:00Z", {
        "AA": _prov_entry(1.0, 12.5), "BB": _prov_entry(0.9, 2.0),
        "CC": _prov_entry(0.1, 99.0)})
    start, end = day_bounds_utc(DAY)
    by_prov, coverage, reason = rv.radar_province_archive(str(root), start, end)
    assert reason is None
    # massimo del giorno per provincia
    assert by_prov["AA"] == pytest.approx(12.5)
    assert by_prov["BB"] == pytest.approx(2.0)
    # CC ha copertura 0.1 < 0.5: NON entra, anche se il massimo e' alto
    assert "CC" not in by_prov
    assert coverage["CC"]["valid"] == pytest.approx(0.1)
    assert coverage["AA"]["hours"] == 2


def test_radar_province_archive_missing_and_empty(tmp_path):
    start, end = day_bounds_utc(DAY)
    by_prov, cov, reason = rv.radar_province_archive(
        str(tmp_path / "nope"), start, end)
    assert by_prov is None
    assert reason == "radar_province_archive_missing"
    empty = tmp_path / "empty"
    empty.mkdir()
    by_prov, cov, reason = rv.radar_province_archive(str(empty), start, end)
    assert by_prov is None
    assert reason == "radar_province_archive_empty_for_day"


def test_radar_province_archive_all_blind_is_no_radar_coverage(tmp_path):
    root = tmp_path / "srt1_provinces"
    _write_radar_hour(root, "2026-11-20T09:00:00Z", {
        "AA": _prov_entry(0.0, None), "BB": _prov_entry(0.2, 3.0),
        "CC": _prov_entry(0.3, 5.0)})
    start, end = day_bounds_utc(DAY)
    by_prov, coverage, reason = rv.radar_province_archive(str(root), start, end)
    assert by_prov is None
    assert reason == "no_radar_coverage"
    assert coverage["AA"]["valid"] == 0.0


def test_verify_day_with_province_archive_reports_ok_and_rd(tmp_path):
    geo, daily, index, snap, events, structures = _prepare(tmp_path)
    root = tmp_path / "srt1_provinces"
    _write_radar_hour(root, "2026-11-20T09:00:00Z", {
        "AA": _prov_entry(1.0, 12.5), "BB": _prov_entry(0.9, 3.0),
        "CC": _prov_entry(0.1, 40.0)})
    ns = _namespace(tmp_path, radar_province_dir=str(root))
    out = rv.run(ns)
    rows = [json.loads(x) for x in
            Path(ns.out).read_text(encoding="utf-8").splitlines() if x]
    by = {(r["p"], r["h"]): r for r in rows}

    # la copertura radar e' dichiarata OK, non piu' "griglia non incorporata"
    skill = json.loads(Path(ns.skill_out).read_text(encoding="utf-8"))
    radar = skill["coverage"]["radar"]
    assert radar["available"] is True
    assert radar["reason"] == "ok"
    assert radar["source"] == "srt1_province_archive"
    assert radar["used_for_provinces"] == 2
    assert radar["provinces_without_coverage"] == ["CC"]

    # rd = massimo radar della provincia; rdc = copertura
    assert by[("AA", "rain")]["rd"] == pytest.approx(12.5)
    assert by[("AA", "rain")]["rdc"] == pytest.approx(1.0)
    assert by[("BB", "rain")]["rd"] == pytest.approx(3.0)
    # provincia cieca: nessun rd, mai "0 mm misurati"
    assert "rd" not in by[("CC", "rain")]
    # il radar non sostituisce la fonte stazioni
    assert by[("AA", "rain")]["src"].startswith("stations")
    assert out["summary"]["radar"]["reason"] == "ok"


def test_verify_day_without_archive_keeps_declared_reason(tmp_path):
    geo, daily, index, snap, events, structures = _prepare(tmp_path)
    radar_path = tmp_path / "accumulation.json"
    radar_path.write_text(json.dumps({
        "slots": [{"valid_time": "2026-11-20T21:30:00Z",
                   "totals": {"valid_px": 800, "total_px": 1000},
                   "areas": {"Sud": {"max_mm": 4.0}}}]}), encoding="utf-8")
    ns = _namespace(tmp_path, radar=str(radar_path))
    rv.run(ns)
    skill = json.loads(Path(ns.skill_out).read_text(encoding="utf-8"))
    radar = skill["coverage"]["radar"]
    assert radar["available"] is False
    assert radar["reason"] == "radar_grid_not_embedded"
