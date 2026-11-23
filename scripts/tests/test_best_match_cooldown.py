# -*- coding: utf-8 -*-
"""Test del cooldown del canary Best Match (meccanismo speculare al check ECMWF).

Copre: rc 12 (NO-OP dichiarato, zero rete/scritture), persistenza con
--record-api-failure su campo DEDICATO, bypass con --force-update, indipendenza
dal cooldown ECMWF, e il fatto che un errore di rete resti rc 1 senza inventare
best_changed=true ne' toccare lo stato (H4).
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common  # noqa: E402
import check_best_match as cbm  # noqa: E402
import check_model_runs as cmr  # noqa: E402


def _sentinel():
    return [{"sigla": "MI", "lat": 45.46, "lon": 9.19, "area": "MI"}]


def _ok_response():
    return [{"hourly": {"time": ["2026-11-16T00:00"],
                        "weathercode": [1], "precipitation": [0.0]}}]


def test_record_api_failure_writes_only_dedicated_cooldown(monkeypatch, tmp_path):
    """--record-api-failure scrive SOLO best_match_cooldown_until: il cooldown
    ECMWF e il resto dello stato restano invariati."""
    state_file = tmp_path / "last_model_run.json"
    original = {
        "last_processed_key": "1788609600",
        "status": "live",
        "dataset": {"v": 1},
        "check_cooldown_until": 1234567890,
        "last_model_runs": {"best_match": {"last_fingerprint": "abc"}},
    }
    state_file.write_text(json.dumps(original), encoding="utf-8")
    written = {}

    def fake_load():
        return json.loads(state_file.read_text(encoding="utf-8"))

    def fake_save(s):
        written.update(s)
        state_file.write_text(json.dumps(s), encoding="utf-8")

    monkeypatch.setattr(common, "load_run_state", fake_load)
    monkeypatch.setattr(common, "save_run_state", fake_save)
    monkeypatch.setattr(sys, "argv", ["check_best_match", "--record-api-failure"])

    assert cbm.main() == 0
    assert cbm.COOLDOWN_FIELD == "best_match_cooldown_until"
    assert written[cbm.COOLDOWN_FIELD] > int(time.time())
    assert written["check_cooldown_until"] == original["check_cooldown_until"]
    assert written["last_processed_key"] == original["last_processed_key"]
    assert written["status"] == original["status"]
    assert written["dataset"] == original["dataset"]
    assert written["last_model_runs"] == original["last_model_runs"]


def test_cooldown_future_returns_12_without_network_or_save(monkeypatch):
    """Con cooldown canary futuro: rc=12, nessuna richiesta forecast e nessuna
    scrittura (best_changed=false dichiarato, mai inventato)."""
    state = {"best_match_cooldown_until": int(time.time()) + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))

    def net_should_not_run(*a, **kw):
        raise AssertionError("fetch_best_match_check NON deve girare con cooldown attivo")

    def save_should_not_run(s):
        raise AssertionError("save_run_state NON deve girare con cooldown attivo")

    monkeypatch.setattr(common, "fetch_best_match_check", net_should_not_run)
    monkeypatch.setattr(common, "save_run_state", save_should_not_run)
    monkeypatch.setattr(sys, "argv", ["check_best_match"])

    assert cbm.main() == 12


def test_force_update_bypasses_cooldown(monkeypatch):
    """--force-update ignora il cooldown: la rete viene chiamata e il canary
    riprende il suo esito normale (qui rc 0, fingerprint assente)."""
    state = {"best_match_cooldown_until": int(time.time()) + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "best_match_sentinels", _sentinel)
    monkeypatch.setattr(common, "available_today", lambda: 100)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(s))
    calls = []

    def fake_fetch(lats, lons):
        calls.append((lats, lons))
        return _ok_response()

    monkeypatch.setattr(common, "fetch_best_match_check", fake_fetch)
    monkeypatch.setattr(sys, "argv", ["check_best_match", "--force-update", "--dry-run"])

    assert cbm.main() == 0
    assert len(calls) == 1  # cooldown bypassato
    assert saved == []      # --dry-run: nessuna scrittura


def test_ecmwf_cooldown_does_not_block_best_match(monkeypatch):
    """I due check sono indipendenti: un cooldown ECMWF attivo NON blocca il
    canary Best Match (campo dedicato)."""
    state = {"check_cooldown_until": int(time.time()) + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "best_match_sentinels", _sentinel)
    monkeypatch.setattr(common, "available_today", lambda: 100)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)
    monkeypatch.setattr(common, "save_run_state", lambda s: None)
    calls = []
    monkeypatch.setattr(common, "fetch_best_match_check",
                        lambda a, b: calls.append(1) or _ok_response())
    monkeypatch.setattr(sys, "argv", ["check_best_match", "--dry-run"])

    assert cbm.main() == 0
    assert calls == [1]


def test_network_failure_still_rc13_no_state_no_false_changed(monkeypatch):
    """Errore TLS/timeout -> rc 13 (degrado esplicito), MAI rc 0.

    Contratto reale dal 2026-11-18 (commit cc029763 "degrado esplicito su API
    esterna non disponibile (rc 13)", speculare al check ECMWF): `main()`
    ritorna 13, `workflow_gate.classify_best(13)` produce
    best_state=unavailable + best_reason=api_unavailable_after_retries con
    exit 0, e lo step di cooldown del workflow gira su
    best_state in (unavailable, api_unavailable). Nessuna scrittura dello stato
    da parte del canary (la fingerprint non viene toccata, H4)."""
    monkeypatch.setattr(common, "load_run_state", lambda: {})
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "best_match_sentinels", _sentinel)
    monkeypatch.setattr(common, "available_today", lambda: 100)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)

    def raise_timeout(a, b):
        raise TimeoutError("The handshake operation timed out")

    monkeypatch.setattr(common, "fetch_best_match_check", raise_timeout)
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(s))
    monkeypatch.setattr(sys, "argv", ["check_best_match"])

    assert cbm.main() == 13
    assert saved == []


def test_new_ecmwf_run_preserves_best_match_cooldown(monkeypatch, tmp_path):
    """Un NUOVO run ECMWF (rc 0) ricostruisce new_state come dict letterale:
    il campo DEDICATO best_match_cooldown_until del canary NON deve essere
    azzerato. I due cooldown sono indipendenti: il cooldown ECMWF
    (check_cooldown_until) si azzera al rilevamento del nuovo run (comportamento
    accettato), quello del canary va preservato."""
    future = int(time.time()) + 1800
    state = {
        "last_processed_key": "old-run",
        "status": "live",
        "check_cooldown_until": int(time.time()) - 100,   # cooldown ECMWF SCADUTO (non blocca)
        "best_match_cooldown_until": future,              # cooldown canary nel futuro
        "dataset": {"v": 1},
    }
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "DRIVER_MODEL", "ecmwf_ifs025")

    new_init = int(time.time()) - 100000

    def fake_meta(model_id):
        return {
            "last_run_initialisation_time": new_init,
            "last_run_availability_time": int(time.time()) - 99000,
            "update_interval_seconds": 3600,
        }

    monkeypatch.setattr(common, "get_model_metadata", fake_meta)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)
    monkeypatch.setattr(common, "now_iso", lambda: "2026-11-16T00:00:00+00:00")
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(dict(s)))
    monkeypatch.setattr(sys, "argv", ["check_model_runs"])

    # Il check deve rilevare un NUOVO run (rc 0): prev_key "old-run" != run_key.
    assert cmr.main() == 0
    assert len(saved) == 1
    out = saved[0]
    # IL PUNTO: il cooldown del canary e' PRESERVATO nello stato salvato.
    assert out.get("best_match_cooldown_until") == future
    # Il cooldown ECMWF NON e' preservato (si azzera al nuovo run): accettato.
    assert "check_cooldown_until" not in out
    # Il nuovo run ECMWF e' stato registrato.
    assert out.get("run_key") == str(new_init)
    assert out.get("dataset") == {"v": 1}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("  [PASS] %s" % t.__name__)
    print("RESULT: PASS (Best Match canary cooldown)")
