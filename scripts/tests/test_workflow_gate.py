"""Test HARDENING 1.0.0.6: classificazione workflow_gate + catena pipeline (TEST A-F).

Verifica la separazione OBBLIGATORIA:
  A) check 10 (no new run)  -> clean success, zero fetch/build/commit/deploy
  B) check 0 (+ catena ok)  -> planner, guardrail check, fetch, build, validate, commit, deploy
  C) check 1+ (errore)      -> workflow FAIL, nessuna pipeline successiva
  D) planner 1 (errore)     -> workflow FAIL, nessun fetch/pubblicazione
  E) planner 2 (hard safety ceiling) -> workflow SUCCESS, safe skip, last known good
  F) validate 1             -> workflow FAIL, publish/commit/deploy mai eseguiti
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import workflow_gate as wg  # noqa: E402


def run_chain(check_rc, best_rc=0, plan_rc=None, fetch_rc=None, build_rc=None,
              validate_rc=None, publish_rc=None):
    """Simula la catena del workflow (if: sui gate) e riporta quali passi girano."""
    used = {"best": False, "plan": False, "fetch": False, "build": False,
            "validate": False, "publish": False, "commit": False, "deploy": False}
    check = wg.classify_check(check_rc)
    best = plan = fetch = build = validate = publish = None
    if check["decision"] in ("continue",):
        best = wg.classify_best(best_rc)
        used["best"] = True
        if best["decision"] == "continue":
            plan = wg.classify_plan(plan_rc)
            used["plan"] = True
            if plan["decision"] == "continue":
                fetch = wg.classify_fetch(fetch_rc)
                used["fetch"] = True
                if fetch["decision"] == "continue":
                    build = wg.classify_build(build_rc)
                    used["build"] = True
                    if build["decision"] == "continue":
                        validate = wg.classify_validate(validate_rc)
                        used["validate"] = True
                        if validate["decision"] == "continue":
                            publish = wg.classify_publish(publish_rc)
                            used["publish"] = True
                            if publish["decision"] == "continue":
                                used["commit"] = True
                                used["deploy"] = True
    last = publish or validate or build or fetch or plan or best or check
    return used, check, last


def assert_no_heavy(used):
    for s in ("fetch", "build", "validate", "publish", "commit", "deploy"):
        assert used[s] is False, "passo %s non deve eseguire" % s


def test_a_no_new_run_clean_exit():
    d = wg.classify_check(10)
    assert d["exit_code"] == 0
    assert d["decision"] == "clean_exit"
    assert d["outputs"]["new_run"] == "false"
    assert "[INFO] No new model run — clean exit" in d["message"]
    used, _, _ = run_chain(10)
    assert used["plan"] is False
    assert_no_heavy(used)


def test_c2_check_noop_declared_clean_exit():
    # rc=11: cooldown attivo — clean exit, new_run=false, check_state=noop,
    # nessuna richiesta Metadata API e nessun passo della catena pesante.
    d = wg.classify_check(11)
    assert d["exit_code"] == 0
    assert d["decision"] == "clean_exit"
    assert d["outputs"]["new_run"] == "false"
    assert d["outputs"]["check_state"] == "noop"
    assert "NO-OP dichiarato" in d["message"]
    used, _, _ = run_chain(11)
    assert used["best"] is False   # la catena pesante NON viene eseguita
    assert used["plan"] is False
    assert_no_heavy(used)


def test_c3_check_api_unavailable_degraded():
    # rc=13 (API esterna non disponibile): SUCCESS degradato, non FAIL
    d = wg.classify_check(13)
    assert d["exit_code"] == 0
    assert d["decision"] == "degraded"
    assert d["outputs"]["new_run"] == "false"
    assert d["outputs"]["check_state"] == "unavailable"
    assert d["outputs"]["check_reason"] == "api_unavailable_after_retries"
    assert "[WARN]" in d["message"]
    used, _, _ = run_chain(13)
    assert used["best"] is False
    assert used["plan"] is False
    assert_no_heavy(used)


def test_b2_best_api_unavailable_degraded():
    d = wg.classify_best(13)
    assert d["exit_code"] == 0
    assert d["decision"] == "degraded"
    assert d["outputs"]["best_changed"] == "false"
    assert d["outputs"]["best_state"] == "unavailable"
    assert d["outputs"]["best_reason"] == "api_unavailable_after_retries"
    assert "[WARN]" in d["message"]


def test_b_new_run_full_chain():
    d = wg.classify_check(0)
    assert d["exit_code"] == 0
    assert d["decision"] == "continue"
    assert d["outputs"]["new_run"] == "true"
    assert "[INFO] New model run detected" in d["message"]
    used, _, pub = run_chain(0, plan_rc=0, fetch_rc=0, build_rc=0,
                             validate_rc=0, publish_rc=0)
    for s in ("plan", "fetch", "build", "validate", "publish", "commit", "deploy"):
        assert used[s] is True, "passo %s deve eseguire nella catena completa" % s
    assert "[INFO] Dataset successfully published" in pub["message"]


def test_c_model_check_error_fails():
    d = wg.classify_check(1)
    assert d["exit_code"] == 1
    assert d["decision"] == "fail"
    assert d["outputs"]["check_state"] == "api_unavailable"
    assert "[ERROR]" in d["message"]
    used, _, _ = run_chain(1)
    assert_no_heavy(used)


def test_d_planner_error_fails():
    d = wg.classify_plan(1)
    assert d["exit_code"] == 1
    assert d["decision"] == "fail"
    assert "[ERROR] Request planner failed" in d["message"]
    used, _, _ = run_chain(0, plan_rc=1)
    assert used["fetch"] is False
    assert_no_heavy(used)


def test_e_budget_blocked_safe_success():
    d = wg.classify_plan(2)
    assert d["exit_code"] == 0
    assert d["decision"] == "safe_skip"
    assert "[INFO] Hard safety ceiling reached — safe skip, last known good dataset preserved" in d["message"]
    assert d["outputs"]["plan_state"] == "budget_blocked"  # chiave macchina stabile
    used, _, _ = run_chain(0, plan_rc=2)
    assert staged_fetch_not_run(used)
    assert_no_heavy(used)


def staged_fetch_not_run(used):
    # plan gira ma fetch e successivi non devono eseguire
    return used["fetch"] is False and used["publish"] is False


def test_f_validation_failure_blocks_publish():
    d = wg.classify_validate(1)
    assert d["exit_code"] == 1
    assert d["decision"] == "fail"
    assert "[ERROR] Dataset validation failed" in d["message"]
    used, _, _ = run_chain(0, plan_rc=0, fetch_rc=0, build_rc=0, validate_rc=1)
    assert used["publish"] is False
    assert used["commit"] is False
    assert used["deploy"] is False


def test_fetch_budget_and_nopartial_safe():
    d2 = wg.classify_fetch(2)
    assert d2["exit_code"] == 0 and d2["decision"] == "safe_skip"
    assert d2["outputs"]["fetch_reason"] == "budget_blocked"
    d3 = wg.classify_fetch(3)
    assert d3["exit_code"] == 0 and d3["decision"] == "safe_skip"
    assert d3["outputs"]["fetch_reason"] == "capoluoghi_mancanti"
    assert "no partial publish" in d3["message"]
    df = wg.classify_fetch(1)
    assert df["exit_code"] == 1 and df["decision"] == "fail"
    assert "[ERROR] Source data fetch failed" in df["message"]


def test_build_publish_errors_fail():
    assert wg.classify_build(1)["exit_code"] == 1
    assert wg.classify_publish(1)["exit_code"] == 1
    assert wg.classify_publish(0)["outputs"]["new_dataset"] == "true"


def test_best_noop_declared_clean_exit():
    # rc=12: cooldown canary attivo — best_changed=false, best_state=noop,
    # exit 0, nessun passo successivo della catena (come il rc=11 di check).
    d = wg.classify_best(12)
    assert d["exit_code"] == 0
    assert d["decision"] == "clean_exit"
    assert d["outputs"]["best_changed"] == "false"
    assert d["outputs"]["best_state"] == "noop"
    assert "NO-OP dichiarato" in d["message"]
    used, _, _ = run_chain(0, best_rc=12)
    assert used["plan"] is False
    assert_no_heavy(used)


def test_best_api_unavailable_fails_and_exposes_state():
    # rc=1: errore/rete — exit 1, best_changed MAI true, best_state esposto
    # allo step if: failure() che persiste il cooldown.
    d = wg.classify_best(1)
    assert d["exit_code"] == 1
    assert d["decision"] == "fail"
    assert d["outputs"]["best_changed"] == "false"
    assert d["outputs"]["best_state"] == "api_unavailable"
    used, _, _ = run_chain(0, best_rc=1)
    assert used["plan"] is False
    assert_no_heavy(used)


def test_best_changed_and_unchanged_outputs():
    # Nessuna regressione dei rami storici: best_changed resta la chiave usata
    # da decide_cycle; best_state e' solo osservabilita' aggiuntiva.
    assert wg.classify_best(0)["outputs"]["best_changed"] == "true"
    assert wg.classify_best(0)["outputs"]["best_state"] == "changed"
    assert wg.classify_best(10)["outputs"]["best_changed"] == "false"
    assert wg.classify_best(10)["outputs"]["best_state"] == "unchanged"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("  [PASS] %s" % t.__name__)
    print("RESULT: PASS (TEST A-F workflow gate)")