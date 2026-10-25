#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1.2.4.26 — archivio delle CELLE per frame e finestra di fetch incrementale.

Covers:
  - history.write_cells/read_cells: round-trip ESATTO (float a piena
    precisione, nessun arrotondamento), slot incoerente/corrotto -> None;
  - history.prune_cells: tiene solo gli ultimi N slot per nome;
  - fetch.fetch_window: riusa le celle archiviate, scarica solo gli slot
    mancanti e tiene SEMPRE come raster reali l'ancora e gli ultimi
    `phase2_lookback_frames` slot (servono a _hook_for_candidate);
  - senza archivio / incremental_fetch.enabled=false / history_dir=None:
    comportamento storico (scarica tutto).
"""
import json
import os
import sys
import types

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(os.path.dirname(_TESTS_DIR))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import radar_engine.fetch as fetch          # noqa: E402
import radar_engine.history as history      # noqa: E402
import radar_engine.models as models        # noqa: E402
from radar_engine.config import CONFIG      # noqa: E402

T0 = 1_790_000_000_000


def _iso(ms):
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000.0, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _cell(ms, cid="c1", **kw):
    """Cella con valori 'scomodi' (float non tondi) per il round-trip."""
    defaults = dict(
        area_km2=123.45678901234567, centroid_raster=(12.3456789, 67.8901234),
        centroid_lonlat=(12.3456789012345, 41.2345678901234),
        bbox_raster=(10, 20, 30, 40), max_dbz=47.123456789,
        mean_dbz=33.987654321, p90_dbz=41.111111111,
        eccentricity=0.4234567, solidity=0.8765432,
        compactness=1.2345678, pixel_count=1234)
    defaults.update(kw)
    return models.DetectedCell(
        cell_id=cid, timestamp_ms=int(ms), timestamp_iso=_iso(ms),
        frame_index=0, **defaults)


def _cfg(**inc):
    cfg = json.loads(json.dumps({"source": dict(CONFIG["source"])}))
    cfg["source"]["incremental_fetch"] = dict(
        {"enabled": True, "phase2_lookback_frames": 3}, **inc)
    return cfg


def test_cells_roundtrip_is_bit_exact(tmp_path):
    """I float devono tornare IDENTICI: un arrotondamento puo' spostare una
    soglia (area/DBZ) e cambiare una track."""
    cell = _cell(T0)
    sig = history.cells_signature(_cfg())
    history.write_cells(str(tmp_path), _iso(T0), [cell], signature=sig)
    back = history.read_cells(str(tmp_path), _iso(T0), signature=sig)
    assert back is not None and len(back) == 1
    b = back[0]
    for name in history.CELL_FIELDS:
        a = getattr(cell, name)
        v = getattr(b, name)
        if isinstance(a, tuple):
            a, v = list(a), list(v)
        assert a == v, name
    # firma diversa (soglia/nodata cambiati) -> le celle NON sono riusabili
    assert history.read_cells(str(tmp_path), _iso(T0),
                              signature="deadbeefdeadbeef") is None


def test_cells_missing_or_corrupt_returns_none(tmp_path):
    assert history.read_cells(str(tmp_path), _iso(T0)) is None
    path = os.path.join(str(tmp_path), history.CELLS_DIR,
                        history.slot_name(_iso(T0)) + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{non json")
    assert history.read_cells(str(tmp_path), _iso(T0)) is None
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"slot": "altro-slot", "cells": []}, fh)
    assert history.read_cells(str(tmp_path), _iso(T0)) is None


def test_prune_cells_keeps_last_slots(tmp_path):
    slots = [T0 + i * 300000 for i in range(5)]
    for s in slots:
        history.write_cells(str(tmp_path), _iso(s), [_cell(s)])
    kept = history.prune_cells(str(tmp_path), keep=2)
    assert kept == [history.slot_name(_iso(s)) for s in slots[-2:]]
    names = sorted(os.listdir(os.path.join(str(tmp_path), history.CELLS_DIR)))
    assert len(names) == 2


def _fake_plan(slots):
    def plan(config, product=None, max_frames=None, anchor_ms=None):
        return list(slots), 300
    return plan


def test_fetch_window_reuses_archive_and_keeps_lookback(monkeypatch, tmp_path):
    slots = [T0 + i * 300000 for i in range(6)]
    cfg = _cfg()
    sig = history.cells_signature(cfg)
    for s in slots[:4]:                    # archivio: i primi 4 slot
        history.write_cells(str(tmp_path), _iso(s), [_cell(s, cid=str(s))],
                            signature=sig)
    monkeypatch.setattr(fetch, "plan_window", _fake_plan(slots))
    downloaded = []

    def fake_download(config, product, todo, period_s):
        downloaded.append(list(todo))
        return [types.SimpleNamespace(time_ms=s, time_iso=_iso(s))
                for s in todo], []

    monkeypatch.setattr(fetch, "_download_slots", fake_download)
    frames, warns, latest, reused, planned = fetch.fetch_window(
        cfg, max_frames=6, history_dir=str(tmp_path))
    assert planned == slots
    # lookback 3 -> restano raster gli ultimi 3 (uno dei quali e' anche in
    # archivio): si risparmiano i 3 piu' vecchi.
    assert sorted(reused) == slots[:3]
    assert downloaded == [slots[3:]]
    assert [f.time_ms for f in frames] == slots[3:]
    # il riuso NON e' un warning (non deve degradare lo status del run):
    # e' un campo additivo che main._run pubblica in latest.json.
    assert not any("fetch incrementale" in w for w in warns)


def test_fetch_window_without_archive_downloads_all(monkeypatch, tmp_path):
    slots = [T0 + i * 300000 for i in range(6)]
    monkeypatch.setattr(fetch, "plan_window", _fake_plan(slots))
    downloaded = []

    def fake_download(config, product, todo, period_s):
        downloaded.append(list(todo))
        return [types.SimpleNamespace(time_ms=s, time_iso=_iso(s))
                for s in todo], []

    monkeypatch.setattr(fetch, "_download_slots", fake_download)
    frames, warns, latest, reused, planned = fetch.fetch_window(
        _cfg(), max_frames=6, history_dir=str(tmp_path))
    assert reused == {}
    assert downloaded == [slots]
    assert not any("fetch incrementale" in w for w in warns)


def test_fetch_window_disabled_or_replay_downloads_all(monkeypatch, tmp_path):
    slots = [T0 + i * 300000 for i in range(6)]
    for s in slots:
        history.write_cells(str(tmp_path), _iso(s), [_cell(s)])
    monkeypatch.setattr(fetch, "plan_window", _fake_plan(slots))
    downloaded = []

    def fake_download(config, product, todo, period_s):
        downloaded.append(list(todo))
        return [], []

    monkeypatch.setattr(fetch, "_download_slots", fake_download)
    # enabled=false -> nessun riuso
    fetch.fetch_window(_cfg(enabled=False), max_frames=6,
                       history_dir=str(tmp_path))
    # history_dir=None (replay --as-of) -> nessun riuso
    fetch.fetch_window(_cfg(), max_frames=6, history_dir=None)
    assert downloaded == [slots, slots]


def test_hook_frames_none_when_window_incomplete():
    """La finestra dell'hook non si accorcia in silenzio: se un frame della
    persistenza e' ricostruito (nessun raster) la componente e' ASSENTE."""
    import radar_engine.main as eng

    bundle = types.SimpleNamespace(
        frames=[object(), object()], cells_by_frame=[[], [], [], [], [], []],
        raster_by_frame_index={4: object(), 5: object()})
    assert eng._hook_frames(bundle, {"history_frames": 3}) is None
    bundle.raster_by_frame_index = {3: object(), 4: object(), 5: object()}
    got = eng._hook_frames(bundle, {"history_frames": 3})
    assert len(got) == 3
    # senza mappa (percorso storico) resta la lista dei frame
    bundle2 = types.SimpleNamespace(frames=[1, 2])
    assert eng._hook_frames(bundle2, {"history_frames": 3}) == [1, 2]
