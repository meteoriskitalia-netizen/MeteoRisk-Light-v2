"""TEST 1.0.0.8 — PARTE H: robustezza rete / Metadata API.

H1 retry controllati (mai infiniti) · H2 timeout esplicito · H3 backoff
esponenziale + jitter · H4 NETWORK ERROR != NO NEW RUN (state/fingerprint solo
dopo check riuscito) · H5 dopo i retry esauriti -> CHECK FAILED esplicito.
"""

import json
import os
import sys
import time
from unittest import mock

import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common  # noqa: E402


class _HTTPError(urllib.error.HTTPError):
    def __init__(self, code):
        urllib.error.HTTPError.__init__(self, "url", code, "err", None, None)


def test_h2_explicit_timeout_documented():
    # timeout esplicito connect/read, ragionevole e documentato
    assert common.OPENMETEO_METADATA_TIMEOUT_S > 0
    assert common.OPENMETEO_METADATA_TIMEOUT_S <= 30


def test_h1_limited_retries_config():
    # tentativi totali finiti: 3-4, mai infiniti
    assert 3 <= common.METADATA_API_RETRIES <= 4


def test_h3_backoff_exponential_with_jitter():
    d0 = common._backoff_delay(common.METADATA_RETRY_BASE_S, 0)
    d1 = common._backoff_delay(common.METADATA_RETRY_BASE_S, 1)
    d2 = common._backoff_delay(common.METADATA_RETRY_BASE_S, 2)
    assert d0 < d1 < d2  # esponenziale crescente (in media)
    for d in (d0, d1, d2):
        assert common.METADATA_RETRY_BASE_S <= d < common.METADATA_RETRY_BASE_S * 8 + common.RETRY_JITTER_MAX_S


def test_transient_error_classification():
    assert common._is_transient_error(urllib.error.URLError("ssl handshake"))
    assert common._is_transient_error(TimeoutError("timed out"))
    assert common._is_transient_error(ConnectionResetError("reset"))
    assert common._is_transient_error(_HTTPError(429))
    assert common._is_transient_error(_HTTPError(503))
    assert common._is_transient_error(_HTTPError(500))
    assert common._is_transient_error(ValueError("x")) is False
    assert common._is_transient_error(_HTTPError(404)) is False


def test_get_model_metadata_retries_then_succeeds():
    calls = []

    def flaky(url, timeout_s=None):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.URLError("_ssl.c:1015 handshake operation timed out")
        return {"last_run_initialisation_time": 1788609600}

    with mock.patch.object(common, "_http_json", side_effect=flaky), \
         mock.patch.object(common.time, "sleep", lambda s: None):
        meta = common.get_model_metadata("ecmwf_ifs")
    assert meta["last_run_initialisation_time"] == 1788609600
    assert len(calls) == 3  # 2 errori + 1 successo


def test_get_model_metadata_exhausts_retries():
    calls = []

    def always_fail(url, timeout_s=None):
        calls.append(url)
        raise ConnectionResetError("machine reset")

    with mock.patch.object(common, "_http_json", side_effect=always_fail), \
         mock.patch.object(common.time, "sleep", lambda s: None):
        try:
            common.get_model_metadata("ecmwf_ifs")
            raise AssertionError("attesa RuntimeError dopo retry esauriti")
        except RuntimeError as exc:
            assert "unavailable after %d retries" % common.METADATA_API_RETRIES in str(exc)
    assert len(calls) == common.METADATA_API_RETRIES  # MAI infiniti


def test_get_model_metadata_non_transient_raises_immediately():
    calls = []

    def hard_fail(url, timeout_s=None):
        calls.append(url)
        raise _HTTPError(404)

    with mock.patch.object(common, "_http_json", side_effect=hard_fail):
        try:
            common.get_model_metadata("ecmwf_ifs")
            raise AssertionError("attesa propagazione immediata 404")
        except urllib.error.HTTPError:
            pass
    assert len(calls) == 1  # niente retry su 4xx


def test_h4_no_state_update_on_failure():
    # H4: un errore API non deve aggiornare last_processed_key/fingerprint.
    # Il canary Best Match e il check ECMWF scrivono SOLO dopo un fetch riuscito:
    # qui verifichiamo che lo state rimanga byte-identico se la fetch fallisce.
    from unittest import mock
    import sys
    import check_model_runs
    import check_best_match

    # isoliamo sys.argv (sotto pytest contiene gli argomenti del runner)
    argv_guard = mock.patch.object(sys, "argv", ["check_model_runs"])

    # check_model_runs: errore metadata -> non scrive nulla (no-op su save e
    # nessuna telemetria guardrails; l'esito arriva da rc 1 + summary)
    with mock.patch.object(common, "get_model_metadata",
                           side_effect=RuntimeError("Metadata API unavailable after 4 retries")), \
         mock.patch.object(common, "record_api_usage") as rec_m, \
         mock.patch.object(common, "save_run_state") as save, \
         argv_guard:
        assert check_model_runs.main() == 1
        save.assert_not_called()
        rec_m.assert_not_called()

    # canary: errore fetch -> non scrive uso ne' last_checked_at
    with mock.patch.object(common, "is_bootstrap_pending", return_value=False), \
         mock.patch.object(common, "fetch_best_match_check",
                           side_effect=ConnectionResetError("reset")), \
         mock.patch.object(common, "record_api_usage") as rec, \
         mock.patch.object(common, "save_run_state") as save2, \
         mock.patch.object(sys, "argv", ["check_best_match"]):
        assert check_best_match.main() == 1
        rec.assert_not_called()
        save2.assert_not_called()


def test_query_model_metadata_uses_real_availability_key(monkeypatch):
    """La chiave REALE di static/meta.json e' last_run_availability_time;
    available_time resta come fallback per retrocompatibilita'."""
    import check_model_runs
    meta = {
        "last_run_initialisation_time": 1788609600,
        "last_run_availability_time": 1788613200,
        "available_time": 999,
        "update_interval_seconds": 3600,
    }
    monkeypatch.setattr(common, "get_model_metadata", lambda m: meta)
    out = check_model_runs.query_model_metadata("ecmwf_ifs")
    assert out["available_time"] == 1788613200

    meta_fallback = {
        "last_run_initialisation_time": 1788609600,
        "available_time": 888,
    }
    monkeypatch.setattr(common, "get_model_metadata", lambda m: meta_fallback)
    out2 = check_model_runs.query_model_metadata("ecmwf_ifs")
    assert out2["available_time"] == 888


def test_record_api_failure_writes_cooldown_only(monkeypatch, tmp_path):
    """--record-api-failure scrive SOLO il cooldown: last_processed_key
    (e il resto dello stato) restano invariati."""
    import check_model_runs
    state_file = tmp_path / "last_model_run.json"
    original = {"last_processed_key": "1788609600", "status": "live",
                "dataset": {"v": 1}}
    state_file.write_text(json.dumps(original), encoding="utf-8")
    written = {}

    def fake_load():
        return json.loads(state_file.read_text(encoding="utf-8"))

    def fake_save(s):
        written.update(s)
        state_file.write_text(json.dumps(s), encoding="utf-8")

    monkeypatch.setattr(common, "load_run_state", fake_load)
    monkeypatch.setattr(common, "save_run_state", fake_save)
    monkeypatch.setattr(sys, "argv", ["check_model_runs", "--record-api-failure"])

    assert check_model_runs.main() == 0
    assert check_model_runs.COOLDOWN_FIELD in written
    assert written[check_model_runs.COOLDOWN_FIELD] > int(time.time())
    assert written["last_processed_key"] == original["last_processed_key"]
    assert written["status"] == original["status"]
    assert written["dataset"] == original["dataset"]


def test_cooldown_future_returns_11_without_api_or_save(monkeypatch):
    """Con cooldown futuro: rc=11, nessuna Metadata API, nessuna scrittura."""
    import check_model_runs
    state = {"last_processed_key": "1788609600",
             check_model_runs.COOLDOWN_FIELD: int(time.time()) + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))

    def api_should_not_run(model):
        raise AssertionError("get_model_metadata NON deve essere chiamata con cooldown attivo")

    def save_should_not_run(state_arg):
        raise AssertionError("save_run_state NON deve essere chiamato con cooldown attivo")

    monkeypatch.setattr(common, "get_model_metadata", api_should_not_run)
    monkeypatch.setattr(common, "save_run_state", save_should_not_run)
    monkeypatch.setattr(sys, "argv", ["check_model_runs"])

    assert check_model_runs.main() == 11


def test_force_update_bypasses_cooldown(monkeypatch):
    """--force-update ignora il cooldown: la Metadata API viene chiamata e
    il check prosegue normalmente (non e' piu' un NO-OP)."""
    import check_model_runs
    state = {"last_processed_key": "1788609600", "status": "live",
             check_model_runs.COOLDOWN_FIELD: int(time.time()) + 1800}
    monkeypatch.setattr(common, "load_run_state", lambda: dict(state))
    monkeypatch.setattr(common, "is_bootstrap_pending", lambda: False)
    monkeypatch.setattr(common, "record_api_usage", lambda **kw: None)

    api_calls = []
    monkeypatch.setattr(common, "get_model_metadata", lambda m: api_calls.append(m) or {
        "last_run_initialisation_time": 1000,
        "last_run_availability_time": 1000,
        "update_interval_seconds": 3600,
    })
    saved = []
    monkeypatch.setattr(common, "save_run_state", lambda s: saved.append(s))
    monkeypatch.setattr(sys, "argv", ["check_model_runs", "--force-update", "--dry-run"])

    rc = check_model_runs.main()
    assert api_calls == [common.DRIVER_MODEL]  # cooldown bypassato: API chiamata
    assert rc == 0                             # non e' piu' rc=11
    assert saved == []                         # --dry-run: nessuna scrittura


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("  [PASS] %s" % t.__name__)
    print("RESULT: PASS (Parte H — robustezza rete / Metadata API, H1-H5)")