# -*- coding: utf-8 -*-
"""Test 2026-11-12 — ATTESA ATTIVA dello slot CAPPI (fetch_cappi_set).

Contratto nuovo (prima: UNA attesa cieca di `retry_wait_s`, poi un solo retry):
  - si riprova ogni `poll_interval_s` (config `cappi_fetch.poll_interval_s`,
    default `fetch.CAPPI_POLL_INTERVAL_S` = 10 s);
  - si ESCE AL PRIMO successo, senza consumare il tetto intero;
  - il tetto TOTALE resta `retry_wait_s` (nessun timeout allungato): il numero
    di round e' `ceil(retry_wait_s / poll_interval_s)`;
  - l'attesa e' per ROUND, condivisa da tutti i prodotti mancanti: un prodotto
    non paga un `sleep` per se stesso;
  - `retry_attempts=0` -> nessun retry (invariato);
  - `poll_interval_s <= 0` -> attesa unica storica (leva di rollback).

Perche' conta: nei run di produzione del 2026-11-12 `cappi_fetch.seconds` era
121,456 / 121,985 / 122,294 s in 3 run su 40 (motore 148,5-159,5 s) contro
1,05-2,01 s negli altri 37 (motore 21,3-43,3 s): il primo POST prende HTTP 500
"HeadObject Forbidden" (slot non ancora pubblicato) e il vecchio codice dormiva
TUTTO `retry_wait_s` prima di riprovare.
"""
import pytest

from radar_engine import fetch as fetch_mod
from radar_engine import models


def _cfg(**cappi_fetch):
    return {
        "source": {"cache_dir": ".", "api_base": "x", "origin_header": "y",
                   "http_timeout_s": 1, "download_timeout_s": 1,
                   "max_download_retries": 0, "keep_raw_frames": False},
        "preprocess": {"nodata_values": [-9999.0, -9998.0],
                       "geo_plausible_bbox": None},
        "phase2": {"structure": {"cappi_fetch": dict(cappi_fetch)}},
    }


def _raster(ts):
    import types

    import numpy as np
    return types.SimpleNamespace(data=np.zeros((2, 2)), time_ms=ts,
                                 time_iso="2026-11-12T16:50:00Z")


REF = 1794502200000


def test_poll_exits_at_first_success_not_after_full_wait(monkeypatch):
    """Il prodotto compare al 2o poll: si dorme 2x10 s, NON 120 s."""
    waits = []
    calls = []

    def fake_read(config, product, ts, period, downloader):
        calls.append((product, ts))
        if len(calls) <= 2:                  # tentativo iniziale + 1o poll
            raise models.SourceError("http_500")
        return _raster(ts), {"bytes": 10, "elapsed_s": 0.1,
                             "fallback": False, "used_time_ms": ts}

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=1, retry_wait_s=120.0,
             allow_previous_slot=True),
        ["CAPPI_2"], REF, warnings=[], sleeper=waits.append)
    assert waits == [10.0, 10.0]             # non [120.0]
    assert summary["retry"]["waited_s"] == 20.0
    assert summary["retry"]["attempts"] == 2
    assert summary["retry"]["poll_interval_s"] == 10.0
    assert summary["retry"]["max_wait_s"] == 120.0
    assert results["CAPPI_2"] is not None
    assert summary["fallback"] == 0


def test_poll_never_exceeds_retry_wait_s(monkeypatch):
    """Mai pubblicato: 12 round da 10 s = 120 s, poi fallback (tetto invariato)."""
    waits = []

    def fake_read(config, product, ts, period, downloader):
        if ts == REF:
            raise models.SourceError("http_500")
        return _raster(ts), {"bytes": 10, "elapsed_s": 0.1,
                             "fallback": False, "used_time_ms": ts}

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=1, retry_wait_s=120.0,
             allow_previous_slot=True),
        ["CAPPI_2"], REF, warnings=[], sleeper=waits.append)
    assert len(waits) == 12
    assert sum(waits) == pytest.approx(120.0)
    assert summary["retry"]["attempts"] == 12
    assert summary["fallback"] == 1
    assert results["CAPPI_2"] is not None
    assert results["CAPPI_2"].time_ms == REF - 300000


def test_poll_wait_is_shared_across_products(monkeypatch):
    """3 prodotti mancanti -> UN solo sleep per round, non uno per prodotto."""
    waits = []
    state = {"done": False}

    def fake_read(config, product, ts, period, downloader):
        if not state["done"]:
            raise models.SourceError("http_500")
        return _raster(ts), {"bytes": 10, "elapsed_s": 0.1,
                             "fallback": False, "used_time_ms": ts}

    def sleeper(s):
        waits.append(s)
        state["done"] = True            # pubblicati dopo il primo round

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=1, retry_wait_s=120.0,
             allow_previous_slot=True),
        ["CAPPI_2", "CAPPI_6", "CAPPI_1"], REF, warnings=[], sleeper=sleeper)
    assert waits == [10.0]              # 1 sleep per round, non 3
    assert all(r is not None for r in results.values())


def test_poll_interval_zero_restores_single_wait(monkeypatch):
    """Rollback: poll_interval_s=0 -> attesa unica storica da retry_wait_s."""
    waits = []
    calls = []

    def fake_read(config, product, ts, period, downloader):
        calls.append(1)
        if len(calls) <= 1:
            raise models.SourceError("http_500")
        return _raster(ts), {"bytes": 10, "elapsed_s": 0.1,
                             "fallback": False, "used_time_ms": ts}

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=1, retry_wait_s=120.0, poll_interval_s=0,
             allow_previous_slot=True),
        ["CAPPI_2"], REF, warnings=[], sleeper=waits.append)
    assert waits == [120.0]
    assert summary["retry"]["attempts"] == 1
    assert summary["retry"]["poll_interval_s"] is None
    assert results["CAPPI_2"] is not None


def test_poll_interval_comes_from_config(monkeypatch):
    """La granularita' si cambia dalla config, senza toccare il codice."""
    waits = []
    calls = []

    def fake_read(config, product, ts, period, downloader):
        calls.append(1)
        if len(calls) <= 2:
            raise models.SourceError("http_500")
        return _raster(ts), {"bytes": 10, "elapsed_s": 0.1,
                             "fallback": False, "used_time_ms": ts}

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    _results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=1, retry_wait_s=120.0, poll_interval_s=5.0,
             allow_previous_slot=True),
        ["CAPPI_2"], REF, warnings=[], sleeper=waits.append)
    assert waits == [5.0, 5.0]
    assert summary["retry"]["poll_interval_s"] == 5.0


def test_retry_attempts_zero_still_means_no_wait(monkeypatch):
    """Invariato: retry_attempts=0 -> nessun retry, nessuno sleep."""
    waits = []

    def fake_read(config, product, ts, period, downloader):
        raise models.SourceError("http_500")

    monkeypatch.setattr(fetch_mod, "_read_product_bytes", fake_read)
    results, summary = fetch_mod.fetch_cappi_set(
        _cfg(retry_attempts=0, retry_wait_s=120.0,
             allow_previous_slot=False),
        ["CAPPI_2"], REF, warnings=[], sleeper=waits.append)
    assert waits == []
    assert summary["retry"]["attempts"] == 0
    assert summary["retry"]["waited_s"] == 0.0
    assert results["CAPPI_2"] is None


def test_config_declares_poll_interval():
    """La config di produzione dichiara la granularita' (10 s) e il tetto 120 s."""
    from radar_engine.config import CONFIG
    cf = CONFIG["phase2"]["structure"]["cappi_fetch"]
    assert cf["retry_wait_s"] == 120.0
    assert cf["poll_interval_s"] == 10.0
    assert cf["retry_attempts"] == 1
    assert cf["allow_previous_slot"] is True
    assert fetch_mod.CAPPI_POLL_INTERVAL_S == 10.0
