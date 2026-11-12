#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dei TEMPI PER STADIO del motore radar (2026-11-12).

Perche' esiste: il job del radar dura 159-194 s e il motore e' l'86% del job,
ma dentro il motore non si sapeva dove andasse il tempo (il workflow
pubblicava `timings_ms.engine_internal` tutto null con motivo
`engine_non_espone_tempi_per_stadio`). Questi test coprono la misura, la
pubblicazione e il costo della strumentazione.

Sul codice PRECEDENTE questi test falliscono: `StageTimers` non esiste,
`build_latest` non emette `timings_ms`, `write_timings` non esiste.
"""

import copy
import json
import os
import sys
import time

import pytest

from radar_engine import fetch
from radar_engine import main as main_mod
from radar_engine import models
from radar_engine import output
from radar_engine.config import CONFIG
from radar_engine.preprocess import read_raster

from conftest import add_blob, blank_grid, write_synthetic_tif

T0 = 1_777_027_200_000          # base fissa (2026-04-30), come gli altri test


# ---------------------------------------------------------------------------
# 1. StageTimers: accumulo, chiamate, ordinamento, eccezioni
# ---------------------------------------------------------------------------
def test_stage_accumula_e_conta_le_chiamate():
    timers = main_mod.StageTimers()
    with timers.stage("detect"):
        time.sleep(0.01)
    with timers.stage("detect"):
        time.sleep(0.01)
    snap = timers.snapshot()
    rec = snap["stages"]["detect"]
    assert rec["calls"] == 2
    # 2 x 10 ms con margine largo (non misura la precisione, misura l'accumulo)
    assert 15.0 <= rec["ms"] <= 200.0
    assert snap["unit"] == "ms"
    assert snap["version"] == main_mod.TIMINGS_VERSION
    assert snap["sorted_by"] == "ms_desc"


def test_stage_registra_il_tempo_anche_se_solleva():
    """Uno stadio che esplode deve restare visibile: il tempo si registra nel
    `finally`, prima che l'eccezione risalga."""
    timers = main_mod.StageTimers()
    with pytest.raises(RuntimeError):
        with timers.stage("phase2"):
            time.sleep(0.01)
            raise RuntimeError("boom")
    rec = timers.snapshot()["stages"]["phase2"]
    assert rec["calls"] == 1
    assert rec["ms"] >= 5.0


def test_snapshot_ordina_per_ms_decrescenti():
    timers = main_mod.StageTimers()
    timers.add("lento", 0.030)
    timers.add("veloce", 0.001)
    timers.add("medio", 0.010)
    names = list(timers.snapshot()["stages"])
    assert names == ["lento", "medio", "veloce"]


def test_total_e_residuo_coerenti():
    """`sum_top_level_ms + residual_ms == total` e i figli NON entrano nella
    somma top-level (sono sottoinsiemi del padre, non addendi)."""
    timers = main_mod.StageTimers()
    with timers.stage("phase2"):
        with timers.stage("fetch_cappi"):
            time.sleep(0.01)
    snap = timers.snapshot()
    assert snap["stages"]["fetch_cappi"]["parents"] == ["phase2"]
    assert snap["stages"]["phase2"]["parents"] == []
    top = snap["sum_top_level_ms"]
    assert top == pytest.approx(snap["stages"]["phase2"]["ms"], abs=0.01)
    assert top + snap["residual_ms"] == pytest.approx(snap["total"], abs=0.01)


def test_instrument_ripristina_gli_originali():
    """`instrument()` non lascia patch permanenti sui moduli (i test che
    monkeypatchano `fetch.plan_window` devono continuare a vincere)."""
    original = fetch.plan_window
    timers = main_mod.StageTimers()
    with timers.instrument():
        assert fetch.plan_window is not original
    assert fetch.plan_window is original


def test_stadi_richiesti_dichiarati():
    """Gli stadi chiesti per la diagnosi sono tutti nella mappa (un rename
    silenzioso li farebbe sparire dalla tabella senza far fallire nulla)."""
    richiesti = (
        "plan_window", "fetch_dpc", "fetch_cappi", "fetch_vil_etm_poh_sri",
        "preprocess", "detect", "tracking_cells", "aggregate_frame",
        "storm_tracking", "convective_structure", "phase2", "opera_merge",
        "write_outputs",
    )
    assert [n for n in richiesti if n not in main_mod.STAGE_PARENTS] == []
    # SRI non ha uno stadio separato: arriva nello stesso bundle di VIL/ETM/POH.
    # Il nome dello stadio lo dichiara, e `target` dice cosa misura davvero.
    assert "SRI" in main_mod.STAGE_TARGETS["fetch_vil_etm_poh_sri"]


# ---------------------------------------------------------------------------
# 2. Pubblicazione in latest.json
# ---------------------------------------------------------------------------
def _bundle():
    b = models.EngineBundle("ok", "2026-04-30T10:00:00Z", output.SOURCE_LABEL)
    b.frames = []
    b.cells_by_frame = []
    b.tracks = []
    return b


def test_build_latest_senza_timings_non_aggiunge_la_chiave():
    """Nessun timings -> nessuna chiave nuova (output identico a prima)."""
    assert "timings_ms" not in output.build_latest(_bundle())


def test_build_latest_pubblica_la_tabella():
    b = _bundle()
    timers = main_mod.StageTimers()
    timers.add("detect", 0.5)
    b.timings_ms = timers.snapshot()
    latest = output.build_latest(b)
    assert "timings_ms" in latest
    assert latest["timings_ms"]["stages"]["detect"]["ms"] == pytest.approx(500.0)
    assert latest["timings_ms"]["stages"]["detect"]["calls"] == 1


def test_write_timings_aggiorna_il_file_senza_toccare_altro(tmp_path):
    out_dir = str(tmp_path / "radar")
    paths = output.write_outputs(_bundle(), out_dir, engine_meta={"phase": 1})
    with open(paths["latest.json"], encoding="utf-8") as fh:
        prima = json.load(fh)
    timers = main_mod.StageTimers()
    timers.add("write_outputs", 0.2)
    written = output.write_timings(out_dir, timers.snapshot())
    assert written == paths["latest.json"]
    with open(paths["latest.json"], encoding="utf-8") as fh:
        dopo = json.load(fh)
    # le due chiavi additive ci sono...
    assert dopo["timings_ms"]["stages"]["write_outputs"]["ms"] == pytest.approx(200.0)
    assert dopo["engine"]["stage_timings_ms"] == dopo["timings_ms"]
    # ...e NESSUN'altra chiave e' cambiata.
    assert set(dopo) - set(prima) == {"timings_ms"}
    for key in prima:
        if key == "engine":
            assert {k: v for k, v in dopo[key].items()
                    if k != "stage_timings_ms"} == prima[key]
        else:
            assert dopo[key] == prima[key]


def test_write_timings_senza_file_o_senza_tempi_non_fa_nulla(tmp_path):
    assert output.write_timings(str(tmp_path), {"stages": {"x": {}}}) is None
    out_dir = str(tmp_path / "radar")
    output.write_outputs(_bundle(), out_dir)
    assert output.write_timings(out_dir, None) is None
    assert output.write_timings(out_dir, {}) is None


# ---------------------------------------------------------------------------
# 3. `_run` strumentato: la tabella nasce dal pipeline vero
# ---------------------------------------------------------------------------
def _cfg(tmp_path):
    cfg = copy.deepcopy(CONFIG)
    cfg["output"]["out_dir"] = str(tmp_path / "radar")
    cfg["source"]["cache_dir"] = str(tmp_path / "cache")
    cfg["source"]["incremental_fetch"]["enabled"] = False
    cfg["source"]["keep_raw_frames"] = False
    cfg["opera"]["enabled"] = False        # nessuna rete nel test
    cfg["phase2"]["enabled"] = False       # nessuna rete nel test
    return cfg


def _raster(tmp_path):
    arr = blank_grid(rows=300, cols=300)
    add_blob(arr, 150, 150, 12, 45.0)
    path = tmp_path / "vmi.tif"
    write_synthetic_tif(str(path), arr)
    rd = read_raster(str(path),
                     nodata_values=CONFIG["preprocess"]["nodata_values"])
    rd.time_ms = T0
    rd.time_iso = "2026-04-30T10:00:00Z"
    return rd


def test_run_pubblica_i_tempi_degli_stadi(monkeypatch, tmp_path):
    rd = _raster(tmp_path)

    def fake_fetch_window(config, product=None, max_frames=None, anchor_ms=None,
                          history_dir=None):
        time.sleep(0.02)                    # stadio misurabile, non istantaneo
        return [rd], [], rd.time_ms, {}, [rd.time_ms]

    monkeypatch.setattr(fetch, "fetch_window", fake_fetch_window)
    cfg = _cfg(tmp_path)
    bundle, rc = main_mod._run(cfg, cfg["output"]["out_dir"], "VMI", 1,
                               False, history_dir=None)

    timings = bundle.timings_ms
    assert rc == main_mod._OK
    stages = timings["stages"]
    for name in ("fetch_window", "detect", "tracking_cells", "phase2",
                 "write_outputs", "predictions_record"):
        assert name in stages, name
    # il tempo dello stadio finto e' quello che ha dormito
    assert stages["fetch_window"]["ms"] >= 15.0
    assert stages["fetch_window"]["calls"] == 1
    # `phase2` e' disabilitato ma lo stadio e' stato eseguito (gate interno)
    assert stages["phase2"]["calls"] == 1
    # total coerente con i top-level + residuo
    assert timings["sum_top_level_ms"] + timings["residual_ms"] == pytest.approx(
        timings["total"], abs=0.01)
    # il file scritto dal motore contiene gia' la tabella
    with open(os.path.join(cfg["output"]["out_dir"], "latest.json"),
              encoding="utf-8") as fh:
        latest = json.load(fh)
    assert latest["timings_ms"]["stages"]["fetch_window"]["ms"] >= 15.0
    assert latest["engine"]["stage_timings_ms"] == latest["timings_ms"]


# ---------------------------------------------------------------------------
# 4. Overhead della strumentazione
# ---------------------------------------------------------------------------
# Nota sul metodo: un "% di overhead" misurato su un singolo ciclo da 30 ms e'
# dominato dal rumore (fra due run dello stesso test si osserva da -0.5% a
# +2%). La misura AFFIDABILE e' il costo ASSOLUTO per chiamata, che qui si
# misura su 50.000 chiamate a una funzione banale; il "< 1% sul run" e' la
# proiezione aritmetica di quel costo sul numero di chiamate del run reale
# (dichiarata come tale, non spacciata per misura diretta).
def _noop():
    return None


def _best_of(fn, n, reps=7):
    best = None
    for _ in range(reps):
        t = time.perf_counter()
        for _ in range(n):
            fn()
        elapsed = time.perf_counter() - t
        best = elapsed if best is None else min(best, elapsed)
    return best


def test_overhead_per_chiamata_e_proiezione_sul_run():
    module = sys.modules[__name__]
    original = module._noop
    timers = main_mod.StageTimers()
    base = _best_of(original, 50_000)
    timers.wrap(module, "_noop", "noop")
    try:
        wrapped = _best_of(module._noop, 50_000)
    finally:
        module._noop = original
    per_call_us = (wrapped - base) / 50_000 * 1e6
    # Misurato ~0,7 us/chiamata: la soglia e' larga di 20x per non essere
    # flaky su una macchina carica, e resta due ordini di grandezza sotto il
    # costo di qualunque stadio reale (il piu' veloce e' ~1 ms).
    assert per_call_us < 20.0
    # Proiezione dichiarata: ~70 chiamate misurate per run (6 frame x
    # preprocess/detect/aggregate + 10 CAPPI + i prodotti del bundle + IR108)
    # su un motore da 159 s. Il costo della misura e' < 0,01% del run.
    calls_per_run = 70
    engine_ms = 159_000.0
    assert (per_call_us * calls_per_run / 1000.0) / engine_ms < 0.01


def test_overhead_dell_accumulo_per_chiamata():
    timers = main_mod.StageTimers()
    n = 20_000
    t = time.perf_counter()
    for _ in range(n):
        timers.add("detect", 1e-6)
    per_call_us = (time.perf_counter() - t) / n * 1e6
    assert per_call_us < 50.0
    assert timers.snapshot()["stages"]["detect"]["calls"] == n
