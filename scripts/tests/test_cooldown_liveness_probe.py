# -*- coding: utf-8 -*-
"""Liveness probe dentro la guard del cooldown (fix 2026-11-18).

Tre scenari, con CLOCK SIMULATO (nessun sleep, nessuna rete reale):
  (a) cooldown attivo + probe 200  -> cooldown azzerato, check/canary eseguito;
  (b) cooldown attivo + probe timeout -> NO-OP (rc 11 / rc 12), stato invariato (H4);
  (c) cooldown scaduto -> comportamento STORICO invariato, probe mai chiamata.

La probe e' UNA sola richiesta con timeout_s=10 allo STESSO endpoint del modulo:
qui la si intercetta su common._http_json, distinta dalla chiamata normale per
il timeout. Nessun test fa richieste di rete reali.
"""

import os
import sys
import urllib.parse
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common  # noqa: E402
import check_model_runs as cmr  # noqa: E402
import check_best_match as cbm  # noqa: E402


NOW = 1_800_000_000
PROBE_TIMEOUT_S = 10


def _clock(monkeypatch, module):
    """Clock simulato: int(time.time()) == NOW per il modulo sotto test."""
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: NOW, sleep=lambda s: None))


def _sentinels():
    return [{"sigla": "MI", "lat": 45.46, "lon": 9.19, "area": "MI"}]


def _canary_payload():
    return [{
        "hourly": {
            "time": ["2026-11-18T00:00", "2026-11-18T01:00"],
            "weathercode": [1, 2],
            "precipitation": [0.0, 0.2],
        }
    }]


# --------------------------------------------------------------------------- #
# (a) cooldown attivo + probe 200 -> azzerato e fetch eseguito
# --------------------------------------------------------------------------- #
def test_ecmwf_probe_200_clears_cooldown_and_fetches(monkeypatch):
    _clock(monkeypatch, cmr)
    state = {"last_processed_key": "old-run", "status": "live",
             "dataset": {"v": 1}, "check_cooldown_until": NOW + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(dict(s)))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)
    monkeypatch.setattr(common, "now_iso", lambda: "2026-11-18T08:00:00+00:00")

    probe_calls = []
    expected_url = common.OPENMETEO_META_BASE.format(
        model=urllib.parse.quote(common.DRIVER_MODEL))

    def fake_http(url, timeout_s=None):
        probe_calls.append((url, timeout_s))
        assert url == expected_url, "la probe deve usare lo STESSO endpoint del check"
        return {"ok": True}

    meta_calls = []

    def fake_meta(model_id):
        meta_calls.append(model_id)
        return {"last_run_initialisation_time": NOW - 72000,
                "last_run_availability_time": NOW - 7200,
                "update_interval_seconds": 3600}

    monkeypatch.setattr(common, "_http_json", fake_http)
    monkeypatch.setattr(common, "get_model_metadata", fake_meta)
    monkeypatch.setattr(sys, "argv", ["check_model_runs", "--liveness-probe"])

    assert cmr.main() == 0
    assert len(probe_calls) == 1 and probe_calls[0][1] == PROBE_TIMEOUT_S
    assert meta_calls == [common.DRIVER_MODEL]  # check normale eseguito
    assert saved, "il cooldown azzerato deve essere persistito"
    assert saved[0]["check_cooldown_until"] == 0
    # H4: last_processed_key NON viene toccato su errore; qui successo, ma va
    # preservato fino alla fine del ciclo di fetch/publish.
    assert saved[-1]["last_processed_key"] == "old-run"


def test_best_match_probe_200_clears_cooldown_and_fetches(monkeypatch):
    _clock(monkeypatch, cbm)
    state = {"best_match_cooldown_until": NOW + 1800,
             "last_model_runs": {"best_match": {"last_fingerprint": "old-fp"}}}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(dict(s)))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "best_match_sentinels", _sentinels)
    monkeypatch.setattr(common, "available_today", lambda: 100)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)
    monkeypatch.setattr(common, "now_iso", lambda: "2026-11-18T08:00:00+00:00")
    monkeypatch.setattr(common, "fingerprint_best_match", lambda payload: "new-fp")

    probe_calls = []

    def fake_http(url, timeout_s=None):
        probe_calls.append((url, timeout_s))
        assert url.startswith(common.OPENMETEO_BASE), "stesso endpoint del canary"
        assert "models=best_match" in url
        return {"ok": True}

    fetch_calls = []

    def fake_fetch(lats, lons):
        fetch_calls.append((lats, lons))
        return _canary_payload()

    monkeypatch.setattr(common, "_http_json", fake_http)
    monkeypatch.setattr(common, "fetch_best_match_check", fake_fetch)
    monkeypatch.setattr(sys, "argv", ["check_best_match", "--liveness-probe"])

    assert cbm.main() == 0  # fingerprint cambiata -> refresh autorizzato
    assert len(probe_calls) == 1 and probe_calls[0][1] == PROBE_TIMEOUT_S
    assert len(fetch_calls) == 1  # canary normale eseguito
    assert saved[0]["best_match_cooldown_until"] == 0
    assert saved[-1]["best_match_cooldown_until"] == 0
    # La fingerprint NON e' scritta dal canary (lo fa publish dopo validate):
    # il fix non deve toccarla.
    assert saved[-1]["last_model_runs"]["best_match"]["last_fingerprint"] == "old-fp"


# --------------------------------------------------------------------------- #
# (b) cooldown attivo + probe timeout -> NO-OP, stato invariato (H4)
# --------------------------------------------------------------------------- #
def test_ecmwf_probe_timeout_noop_state_unchanged(monkeypatch):
    _clock(monkeypatch, cmr)
    state = {"last_processed_key": "old-run", "status": "live",
             "check_cooldown_until": NOW + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(s))
    probe_calls = []

    def fake_http(url, timeout_s=None):
        probe_calls.append((url, timeout_s))
        raise TimeoutError("The handshake operation timed out")

    def api_should_not_run(model):
        raise AssertionError("il check normale NON deve girare se la probe fallisce")

    monkeypatch.setattr(common, "_http_json", fake_http)
    monkeypatch.setattr(common, "get_model_metadata", api_should_not_run)
    monkeypatch.setattr(sys, "argv", ["check_model_runs", "--liveness-probe"])

    assert cmr.main() == 11
    assert len(probe_calls) == 1 and probe_calls[0][1] == PROBE_TIMEOUT_S
    assert saved == []
    assert state["check_cooldown_until"] == NOW + 1800  # cooldown intatto
    assert state["last_processed_key"] == "old-run"     # H4


def test_best_match_probe_timeout_noop_state_unchanged(monkeypatch):
    _clock(monkeypatch, cbm)
    state = {"best_match_cooldown_until": NOW + 1800,
             "last_model_runs": {"best_match": {"last_fingerprint": "old-fp"}}}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(s))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "best_match_sentinels", _sentinels)
    monkeypatch.setattr(common, "available_today", lambda: 100)
    probe_calls = []

    def fake_http(url, timeout_s=None):
        probe_calls.append((url, timeout_s))
        raise TimeoutError("The handshake operation timed out")

    def fetch_should_not_run(lats, lons):
        raise AssertionError("il canary NON deve girare se la probe fallisce")

    monkeypatch.setattr(common, "_http_json", fake_http)
    monkeypatch.setattr(common, "fetch_best_match_check", fetch_should_not_run)
    monkeypatch.setattr(sys, "argv", ["check_best_match", "--liveness-probe"])

    assert cbm.main() == 12
    assert len(probe_calls) == 1 and probe_calls[0][1] == PROBE_TIMEOUT_S
    assert saved == []
    assert state["best_match_cooldown_until"] == NOW + 1800
    assert state["last_model_runs"]["best_match"]["last_fingerprint"] == "old-fp"


# --------------------------------------------------------------------------- #
# (c) cooldown scaduto -> comportamento attuale invariato, probe mai chiamata
# --------------------------------------------------------------------------- #
def test_ecmwf_expired_cooldown_unchanged(monkeypatch):
    _clock(monkeypatch, cmr)
    state = {"last_processed_key": "old-run", "status": "live",
             "check_cooldown_until": NOW - 10}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(s))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)

    def probe_should_not_run(url, timeout_s=None):
        raise AssertionError("probe NON deve girare con cooldown scaduto")

    monkeypatch.setattr(common, "_http_json", probe_should_not_run)
    monkeypatch.setattr(common, "get_model_metadata", lambda m: {
        "last_run_initialisation_time": NOW - 72000,
        "last_run_availability_time": NOW + 7200,  # grace non scaduto -> rc 10
        "update_interval_seconds": 3600,
    })
    monkeypatch.setattr(sys, "argv", ["check_model_runs", "--liveness-probe"])

    assert cmr.main() == 10  # identico al comportamento storico
    assert saved == []


def test_best_match_expired_cooldown_unchanged(monkeypatch):
    _clock(monkeypatch, cbm)
    state = {"best_match_cooldown_until": NOW - 10,
             "last_model_runs": {"best_match": {"last_fingerprint": "same-fp"}}}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(s))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "best_match_sentinels", _sentinels)
    monkeypatch.setattr(common, "available_today", lambda: 100)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)
    monkeypatch.setattr(common, "fingerprint_best_match", lambda payload: "same-fp")

    def probe_should_not_run(url, timeout_s=None):
        raise AssertionError("probe NON deve girare con cooldown scaduto")

    monkeypatch.setattr(common, "_http_json", probe_should_not_run)
    monkeypatch.setattr(common, "fetch_best_match_check", lambda a, b: _canary_payload())
    monkeypatch.setattr(sys, "argv", ["check_best_match", "--liveness-probe"])

    assert cbm.main() == 10  # fingerprint invariata: identico allo storico
    assert saved[-1]["best_match_cooldown_until"] == NOW - 10  # non riscritto


# --------------------------------------------------------------------------- #
# Retrocompatibilita': senza flag, cooldown attivo = zero richieste/scritture
# --------------------------------------------------------------------------- #
def test_ecmwf_active_cooldown_without_flag_never_probes(monkeypatch):
    _clock(monkeypatch, cmr)
    state = {"check_cooldown_until": NOW + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    monkeypatch.setattr(common, "save_run_state",
                        lambda s: (_ for _ in ()).throw(AssertionError("save vietato")))
    monkeypatch.setattr(common, "_http_json",
                        lambda url, timeout_s=None: (_ for _ in ()).throw(
                            AssertionError("probe vietata senza --liveness-probe")))
    monkeypatch.setattr(common, "get_model_metadata",
                        lambda m: (_ for _ in ()).throw(AssertionError("API vietata")))
    monkeypatch.setattr(sys, "argv", ["check_model_runs"])

    assert cmr.main() == 11


def test_best_match_active_cooldown_without_flag_never_probes(monkeypatch):
    _clock(monkeypatch, cbm)
    state = {"best_match_cooldown_until": NOW + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    monkeypatch.setattr(common, "save_run_state",
                        lambda s: (_ for _ in ()).throw(AssertionError("save vietato")))
    monkeypatch.setattr(common, "_http_json",
                        lambda url, timeout_s=None: (_ for _ in ()).throw(
                            AssertionError("probe vietata senza --liveness-probe")))
    monkeypatch.setattr(common, "fetch_best_match_check",
                        lambda a, b: (_ for _ in ()).throw(AssertionError("canary vietato")))
    monkeypatch.setattr(sys, "argv", ["check_best_match"])

    assert cbm.main() == 12


if __name__ == "__main__":
    print("RESULT: PASS (cooldown liveness probe — 8 casi)")
