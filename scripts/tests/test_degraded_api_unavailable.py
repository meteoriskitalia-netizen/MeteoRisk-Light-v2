"""Test del degrado esplicito (rc=13) quando l'API esterna non e' raggiungibile.

NON applica patch di produzione: verifica il CONTRATTO che le patch proposte
devono rispettare. I timeout TLS reali dei 6 run sono simulati con mock, come
gia' fa `test_metadata_retry.py` (che butta `_http_json` in side_effect).
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_best_match  # noqa: E402
import check_model_runs  # noqa: E402
import common  # noqa: E402
import workflow_gate as wg  # noqa: E402


def _isolated_state(tmp_path, monkeypatch):
    target = tmp_path / "last_model_run.json"
    monkeypatch.setattr(common, "LAST_MODEL_RUN", target)
    common.save_run_state({"status": "live", "last_processed_key": "123"})
    return target


def test_gate_rc13_is_clean_degraded():
    for stage, key in (("check", "check_state"), ("best", "best_state")):
        d = wg.classify(stage, 13)
        assert d["exit_code"] == 0
        assert d["decision"] == "degraded"
        assert d["outputs"][key] == "unavailable"
        assert "[WARN]" in d["message"]


def test_gate_rc1_still_fails():
    # i veri errori interni (bootstrap senza run, nessuna sentinella) restano FAIL
    assert wg.classify("check", 1)["exit_code"] == 1
    assert wg.classify("best", 1)["exit_code"] == 1


def test_step5_returns_13_without_touching_state(tmp_path, monkeypatch):
    target = _isolated_state(tmp_path, monkeypatch)
    before = target.read_text(encoding="utf-8")
    err = RuntimeError(
        "Metadata API unavailable after 4 retries: "
        "<urlopen error _ssl.c:1018: The handshake operation timed out>")
    with mock.patch.object(sys, "argv", ["check_model_runs"]), \
            mock.patch.object(common, "is_bootstrap_pending", return_value=False), \
            mock.patch.object(common, "get_model_metadata", side_effect=err):
        rc = check_model_runs.main()
    assert rc == 13
    assert target.read_text(encoding="utf-8") == before  # H4: stato intatto


def test_step7_returns_13_without_touching_state(tmp_path, monkeypatch):
    target = _isolated_state(tmp_path, monkeypatch)
    before = target.read_text(encoding="utf-8")
    err = RuntimeError(
        "Best Match sentinel check failed after 3 tentativi: "
        "<urlopen error _ssl.c:1018: The handshake operation timed out>")
    sents = [{"sigla": "MI", "lat": 45.46, "lon": 9.19}] * 6
    with mock.patch.object(sys, "argv", ["check_best_match"]), \
            mock.patch.object(common, "is_bootstrap_pending", return_value=False), \
            mock.patch.object(common, "best_match_sentinels", return_value=sents), \
            mock.patch.object(common, "available_today", return_value=100), \
            mock.patch.object(common, "fetch_best_match_check", side_effect=err):
        rc = check_best_match.main()
