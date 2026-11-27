#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dell'archivio e della verifica automatica del nowcast (novembre 2026).

Coprono i vincoli richiesti:
  - archivio scritto e riletto (path, campi, sha256 dei WebP, byte);
  - verifica con osservato mancante -> `not_verifiable` con motivo;
  - i conteggi della verifica coincidono con il metodo del replay
    (`nowcast_replay.evaluate_case`) sullo stesso caso sintetico;
  - schema 2 compatibile: le chiavi del riassunto schema 1 restano intatte;
  - dedup per (slot, lead, soglia) e retention della serie.
"""

import hashlib
import json
import os
import sys

import numpy as np
import pytest

from radar_engine import clock as clock_mod
from radar_engine import models
from radar_engine import nowcast_archive as na
from radar_engine import nowcast_field as nf
from radar_engine import nowcast_verify as nv
from radar_engine.config import CONFIG, load_config
from radar_engine.preprocess import read_raster

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if os.path.join(_SCRIPTS, "validation") not in sys.path:
    sys.path.insert(0, os.path.join(_SCRIPTS, "validation"))

import nowcast_event_report as report                       # noqa: E402
import nowcast_replay as replay                             # noqa: E402

from conftest import DEFAULT_TRANSFORM, write_synthetic_tif  # noqa: E402

T0 = 1_795_512_000_000          # 2026-11-23T10:40Z
STEP_MS = 300_000


# ---------------------------------------------------------------------------
# fixture sintetiche (stesse di test_nowcast_field.py)
# ---------------------------------------------------------------------------
def _disk_domain(rows, cols, r0, c0, radius):
    rr, cc = np.ogrid[:rows, :cols]
    return ((rr - r0) ** 2 + (cc - c0) ** 2) <= radius ** 2


def _blob(rows, cols, r0, c0, sigma=6.0, amp=20.0):
    rr, cc = np.ogrid[:rows, :cols]
    return (amp * np.exp(-(((rr - r0) ** 2 + (cc - c0) ** 2)
                           / (2.0 * sigma ** 2)))).astype("float32")


def _write_frame(tmp_path, name, field, domain, ts):
    arr = np.where(domain, field, -9999.0).astype("float32")
    path = str(tmp_path / name)
    write_synthetic_tif(path, arr, transform=DEFAULT_TRANSFORM,
                        nodata=-9999.0)
    rd = read_raster(path, nodata_values=[-9999.0],
                     declared_nodata_from_tiff=True,
                     geo_plausible_bbox=CONFIG["preprocess"][
                         "geo_plausible_bbox"])
    rd.time_ms = int(ts)
    rd.time_iso = nf._iso(ts)
    return rd


def _moving_rasters(tmp_path, n=3, dx=1, rows=200, cols=200):
    domain = _disk_domain(rows, cols, 100, 100, 95)
    out = []
    for k in range(n):
        field = _blob(rows, cols, 100, 60 + k * dx, amp=20.0)
        out.append(_write_frame(tmp_path, "f%d.tif" % k, field, domain,
                                T0 + k * STEP_MS))
    return out, domain


def _config(tmp_path):
    cfg = load_config()
    cfg["output"] = dict(cfg["output"], out_dir=str(tmp_path / "out"))
    cfg["nowcast_trend"] = {"enabled": False}
    cfg["nowcast_decay"] = {"enabled": False}
    cfg["nowcast_clouds"] = {"enabled": False}
    return cfg


def _payload(available=True, generated="2026-11-23T10:50:00Z",
             source_ms=None):
    source_ms = T0 + 2 * STEP_MS if source_ms is None else int(source_ms)
    leads = []
    for lead in nf.LEADS_MIN:
        leads.append({
            "lead_min": lead,
            "valid_time": na.iso_from_ms(source_ms + lead * 60000),
            "unit": "mm/h",
            "image": nf.IMAGE_NAMES[lead],
            "stats": {"max_mmh": 12.0, "rain_pixels": 100,
                      "area_by_threshold_km2": {"0.5": 10.0}},
        })
    return {
        "schema": nf.SCHEMA,
        "available": available,
        "null_reason": None if available else "no_rain_in_latest_frame",
        "source": nf.SOURCE,
        "source_slot": na.iso_from_ms(source_ms),
        "generated_at": generated,
        "unit": "mm/h",
        "motion": nf.MOTION,
        "motion_algorithm": nf.MOTION_ALGORITHM,
        "motion_pairs": 2,
        "motion_stats": {"median_speed_kmh": 20.0, "p95_speed_kmh": 30.0,
                         "pixels_over_cap": 0},
        "speed_cap_kmh": nf.SPEED_CAP_KMH,
        "speed_cap_mode": "vector_zeroed_not_clamped",
        "vectors_zeroed_by_cap": 0,
        "frames_used": 3,
        "frames_slots": [na.iso_from_ms(T0 - 2 * STEP_MS),
                         na.iso_from_ms(T0 - STEP_MS),
                         na.iso_from_ms(T0)],
        "leads": leads,
        "grid": {"crs": "EPSG:3857", "bounds": [0, 0, 1, 1],
                 "width": 10, "height": 10, "resolution_m": 1500},
        "output_bytes": {"images": {nf.IMAGE_NAMES[l]: 80
                                    for l in nf.LEADS_MIN}},
        "limits": "test",
    }


# ---------------------------------------------------------------------------
# 1. archivio: scritto e riletto
# ---------------------------------------------------------------------------
def test_archive_roundtrip(tmp_path):
    payload = _payload()
    images = tmp_path / "radar"
    images.mkdir()
    for name in nf.IMAGE_NAMES.values():
        (images / name).write_bytes(b"RIFF" + name.encode("ascii"))
    nowcast_json = images / "nowcast.json"
    nowcast_json.write_text(json.dumps(payload), encoding="utf-8")

    root = str(tmp_path / "archive")
    path, size, rec = na.archive_nowcast(str(nowcast_json), root)
    assert path.endswith(os.path.join("2026", "11", "24", "0930.json"))
    assert size == os.path.getsize(path)
    assert rec["schema"] == na.SCHEMA
    assert rec["slot_basis"] == "source_slot"
    assert rec["source_slot"] == "2026-11-24T09:30:00Z"
    assert [l["lead_min"] for l in rec["leads"]] == list(nf.LEADS_MIN)
    assert [l["lead_min"] for l in rec["leads"] if l["official"]] == [15, 30, 60]
    assert na.OFFICIAL_LEADS == nf.OFFICIAL_LEADS
    assert rec["speed_cap_kmh"] == 110.0
    # riferimento ai WebP: byte e sha256, NESSUN file copiato
    for name in nf.IMAGE_NAMES.values():
        ref = rec["images"][name]
        assert ref["present"] is True
        assert ref["bytes"] == os.path.getsize(str(images / name))
        assert ref["sha256"] == hashlib.sha256(
            (b"RIFF" + name.encode("ascii"))).hexdigest()
    assert not os.path.exists(os.path.join(root, "nowcast_lead015.webp"))

    back = na.read_record(path)
    assert back["leads"][0]["lead_min"] == 5
    assert back["leads"][0]["valid_time"] == "2026-11-24T09:35:00Z"
    assert back["leads"][2]["lead_min"] == 15 and back["leads"][2]["official"]
    assert list(na.iter_records(root))[0]["_path"] == path


def test_archive_unavailable_uses_generated_at(tmp_path):
    payload = _payload(available=False, generated="2026-11-23T11:05:00Z")
    payload["source_slot"] = None
    payload["leads"] = []
    rec = na.compact(payload, images_dir=None)
    assert rec["slot_basis"] == "generated_at"
    assert rec["slot"] == "2026-11-23T11:05:00Z"
    assert rec["available"] is False
    root = str(tmp_path / "archive")
    path, _size = na.write_record(root, rec)
    assert path.endswith(os.path.join("2026", "11", "23", "1105.json"))


def test_archive_prune_retention(tmp_path):
    root = str(tmp_path / "archive")
    for day, hhmm in ((1, "00:00"), (20, "12:00")):
        payload = _payload(generated="2026-11-%02dT12:00:00Z" % day)
        payload["source_slot"] = "2026-11-%02dT%s:00Z" % (day, hhmm)
        for lead in payload["leads"]:
            lead["valid_time"] = "2026-11-%02dT13:00:00Z" % day
        na.write_record(root, na.compact(payload))
    removed, kept = na.prune(root, keep_days=7,
                             now="2026-11-21T00:00:00Z")
    assert removed == 1 and kept == 1
    remaining = list(na.iter_records(root))
    assert len(remaining) == 1
    assert remaining[0]["source_slot"].startswith("2026-11-20")


# ---------------------------------------------------------------------------
# 2. verifica: osservato mancante -> not_verifiable con motivo
# ---------------------------------------------------------------------------
def test_missing_observed_not_verifiable(tmp_path):
    rasters, _domain = _moving_rasters(tmp_path, n=3)
    source = T0 + 2 * STEP_MS
    payload = _payload()
    payload["leads"] = [
        {"lead_min": 15, "valid_time": na.iso_from_ms(source + 15 * 60000)},
        {"lead_min": 30, "valid_time": na.iso_from_ms(source + 30 * 60000)},
        {"lead_min": 60, "valid_time": na.iso_from_ms(source + 60 * 60000)},
    ]
    root = str(tmp_path / "archive")
    na.write_record(root, na.compact(payload))
    out = str(tmp_path / "verification.json")

    def fetch_fn(config, product=None, max_frames=None, anchor_ms=None):
        if int(max_frames or 0) >= 6:
            return list(rasters), [], rasters[-1].time_ms
        return [], ["missing"], None

    # now = slot + 3 h: tutti i lead sono oltre la grazia di 90 min
    stats = nv.verify_pending(
        root, out, _config(tmp_path),
        now=na.iso_from_ms(source + 180 * 60000),
        fetch_fn=fetch_fn, grace_min=90, max_lookback_hours=24,
        max_slots=10)
    doc = json.loads(open(out, encoding="utf-8").read())
    rows = doc["series"]["rows"]
    assert stats["rows_not_verifiable"] == 9       # 3 lead x 3 soglie
    assert len(rows) == 9
    assert all(r["status"] == "not_verifiable" for r in rows)
    assert all(r["null_reason"] == "observed_missing" for r in rows)
    assert all(r["csi_nowcast"] is None for r in rows)
    assert doc["schema"] == nv.SCHEMA


def test_verify_counts_match_replay(tmp_path):
    rasters, domain = _moving_rasters(tmp_path, n=3, dx=1)
    source = T0 + 2 * STEP_MS
    payload = _payload()
    root = str(tmp_path / "archive")
    na.write_record(root, na.compact(payload))
    out = str(tmp_path / "verification.json")

    observed = {}
    for lead in nf.OFFICIAL_LEADS:
        shift = lead // 5
        field = _blob(200, 200, 100, 60 + 2 + shift, amp=20.0)
        observed[source + lead * 60000] = _write_frame(
            tmp_path, "obs%d.tif" % lead, field, domain,
            source + lead * 60000)

    def fetch_fn(config, product=None, max_frames=None, anchor_ms=None):
        if int(max_frames or 0) >= 6:
            return list(rasters), [], rasters[-1].time_ms
        rd = observed.get(int(anchor_ms))
        return ([rd], [], rd.time_ms) if rd is not None else ([], [], None)

    cfg = _config(tmp_path)
    now = source + 75 * 60000
    stats = nv.verify_pending(root, out, cfg, now=now, fetch_fn=fetch_fn,
                              grace_min=90, max_lookback_hours=24,
                              max_slots=10)
    # gli altri 15 frame del player (18 lead - 3 ufficiali) sono archiviati
    # ma NON verificati: i lead oltre +90 non esistono piu'
    assert stats["player_leads_skipped"] == 15
    doc = json.loads(open(out, encoding="utf-8").read())
    rows = {(r["lead_min"], r["threshold_mmh"]): r
            for r in doc["series"]["rows"]}
    assert len(rows) == 9

    # stesso caso con il metodo del replay (stesso compute, stessi frame)
    clk = clock_mod.Clock.at_ms(int(rasters[-1].time_ms))
    bundle = replay._Bundle(rasters)
    case = replay.evaluate_case(bundle, rasters, cfg, clk,
                                {l: observed[source + l * 60000]
                                 for l in nf.OFFICIAL_LEADS},
                                min_rain_pixels=100)
    assert case["status"] == "ok"
    for lead in nf.OFFICIAL_LEADS:
        for thr in nf.VERIFY_THRESHOLDS_MMH:
            key = "%d|%.1f" % (lead, thr)
            row = rows[(lead, thr)]
            ref = case["leads"][key]
            for i, name in enumerate(("hits", "misses", "false_alarms",
                                      "correct_negatives")):
                assert row[name] == ref["nowcast"][i]
                assert row["%s_persistenza" % name] == ref["persistence"][i]
            assert row["status"] == "verified"
            assert row["peak_observed"] is not None

    # secondo passaggio con lo stesso istante: nessuna riga nuova, nessuna
    # riscrittura (evita un commit ogni 10 minuti senza dati nuovi)
    before = open(out, "rb").read()
    stats2 = nv.verify_pending(root, out, cfg, now=now, fetch_fn=fetch_fn,
                               grace_min=90, max_lookback_hours=24,
                               max_slots=10)
    assert stats2["rows_added"] == 0
    assert stats2["verification_written"] is False
    assert open(out, "rb").read() == before


# ---------------------------------------------------------------------------
# 3. schema 2 compatibile con il riassunto schema 1
# ---------------------------------------------------------------------------
def _row(valid="2026-11-23T10:55:00Z", status="verified"):
    return {
        "source_slot": "2026-11-23T10:40:00Z", "lead_min": 15,
        "valid_time": valid, "threshold_mmh": 0.5, "status": status,
        "null_reason": None, "n_px_observed": 500, "domain_pixels": 10000,
        "n_px_observed_ge_threshold": 500,
        "n_px_predicted_ge_threshold": 400,
        "csi_nowcast": 0.6, "pod": 0.7, "far": 0.2,
        "csi_persistenza": 0.5, "pod_persistenza": 0.6,
        "far_persistenza": 0.3,
        "hits": 200, "misses": 100, "false_alarms": 100,
        "correct_negatives": 9600,
        "hits_persistenza": 180, "misses_persistenza": 120,
        "false_alarms_persistenza": 140,
        "correct_negatives_persistenza": 9560,
        "beats_persistence": True, "peak_observed": {"mmh": 12.0, "lat": 45.0,
                                                     "lon": 9.0},
        "error_location": {"false_alarm_centroid": {"lat": 45.1, "lon": 9.1},
                           "miss_centroid": {"lat": 45.2, "lon": 9.2}},
    }


def test_schema2_keeps_schema1_summary():
    v1 = {
        "schema": "meteorisk.nowcast_verification/1",
        "status": "ok", "beats_persistence": True, "null_reason": None,
        "cases_total": 49, "cases_with_rain": 49,
        "rain_pixels_total": 9244031, "publish_scores": True,
        "decision_rule": {"nowcast_mean_csi": 0.499,
                          "persistence_mean_csi": 0.478},
        "leads": {"15|0.5": {"nowcast": {"csi": 0.677}}},
        "generated_at": "2026-11-24T11:26:55Z",
    }
    now = na.parse_iso_ms("2026-11-24T12:00:00Z")
    out = nv.merge_series(v1, [_row()], now)
    # le chiavi dello schema 1 sono intatte, valore per valore
    for key, value in v1.items():
        if key == "schema":
            continue
        assert out[key] == value
    assert out["schema"] == "meteorisk.nowcast_verification/2"
    assert out["schema_version"] == 2
    assert out["compatibility"]["previous_schema"] == \
        "meteorisk.nowcast_verification/1"
    assert out["compatibility"]["summary_keys_preserved"] is True
    assert out["series"]["schema"] == nv.SERIES_SCHEMA
    assert len(out["series"]["rows"]) == 1
    # serializzabile e leggibile come JSON (nessun NaN/Inf)
    text = json.dumps(out, allow_nan=False)
    assert "meteorisk.nowcast_verification/2" in text


def test_series_dedup_and_retention():
    now = na.parse_iso_ms("2026-11-24T12:00:00Z")
    old = _row(valid="2026-10-01T00:00:00Z")     # oltre 14 giorni
    keep = _row(valid="2026-11-24T10:55:00Z")
    new = _row(valid="2026-11-24T10:55:00Z")
    new["csi_nowcast"] = 0.9
    out = nv.merge_series({}, [old, keep, new], now, retention_days=14)
    rows = out["series"]["rows"]
    assert len(rows) == 1                       # dedup per (slot, lead, soglia)
    assert rows[0]["csi_nowcast"] == 0.9        # vince la riga nuova
    assert out["compatibility"]["summary_keys_preserved"] is False
    assert out["series"]["counts"]["rows"] == 1


# ---------------------------------------------------------------------------
# 4. report: filtra per valid_time e produce casi migliori/peggiori
# ---------------------------------------------------------------------------
def test_report_filters_window_and_ranks_cases():
    rows = [_row(valid="2026-11-24T19:00:00Z")]
    rows[0]["csi_nowcast"] = 0.4
    better = _row(valid="2026-11-24T20:00:00Z")
    better["source_slot"] = "2026-11-24T19:40:00Z"
    better["csi_nowcast"] = 0.8
    better["csi_persistenza"] = 0.6
    outside = _row(valid="2026-11-26T01:00:00Z")

    start, _ = report.parse_iso_ms("2026-11-24T18")
    end, _ = report.parse_iso_ms("2026-11-25T12")
    # un istante senza fuso puo' essere letto nel fuso locale dichiarato
    local, _ = report.parse_iso_ms("2026-11-24T18", tz="Europe/Rome")
    assert report.iso_ms(local) == "2026-11-24T17:00:00Z"
    summary = report.summarize(rows + [better, outside], start, end, True)
    assert summary["counts"]["rows"] == 2
    assert summary["counts"]["verified"] == 2
    assert summary["counts"]["slots"] == 2
    assert summary["per_lead"]["15"]["rows"] == 2
    assert summary["best"][0]["source_slot"] == "2026-11-24T19:40:00Z"
    assert summary["worst"][0]["csi_nowcast"] == 0.4
    assert summary["best"][0]["peak_observed_lat"] == 45.0
    assert summary["persistence"]["beats_persistence"] is True
    assert "Nowcast del campo di pioggia" in report.render(summary)
