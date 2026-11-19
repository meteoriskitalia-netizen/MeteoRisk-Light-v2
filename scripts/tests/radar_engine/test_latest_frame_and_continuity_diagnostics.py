#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1.3.0.27 — due correzioni minime e misurabili.

(A) REGOLA UNICA `is_latest_frame`: `len(cells_by_frame) - 1` anche quando la
    timeline contiene frame VUOTI intercalati. I due call site
    (`forecast._on_latest_frame` e `convective_structure.evaluate`) devono
    restituire lo stesso risultato.

(B) `continuity_rejections`: contatore intero PER RUN dei rigetti del gate
    110 km/h nei due tracker (cella + storm object), pubblicato in
    `latest.json` (via output.py) e nei riepiloghi. Solo il conteggio: nessun
    dettaglio dei singoli rigetti entra nei file pubblicati.

Dati SINTETICI: celle/timeline costruite nei test (nessun dato radar reale nel
prodotto). Questi test FALLISCONO sul codice pristino 1.3.0.27.
"""

import pathlib

from radar_engine import convective_structure as cs
from radar_engine import forecast
from radar_engine import models
from radar_engine import output
from radar_engine import storm_tracking as st
from radar_engine import tracking
from radar_engine.aggregation import aggregate_frame
from radar_engine.config import CONFIG
from radar_engine.tracking import Tracker, build_tracking_metrics

from conftest import make_cell, make_track

T0 = 1_777_027_200_000
STEP_MS = 300_000
CFG = CONFIG["convective_structure"]


def _timeline_with_empty_middle():
    """Tre slot, il secondo VUOTO: indice ultimo frame = 2, non 1."""
    return [[make_cell(12.0, 41.0, T0, cell_id="f0")],
            [],
            [make_cell(12.02, 41.0, T0 + 2 * STEP_MS, cell_id="f2")]]


def _tracked(frames=3, dbz=54.0, org=90):
    """Track sintetica con punti ai frame 0..frames-1 e motion da candidato."""
    cells = [
        make_cell(12.0 + 0.01 * i, 41.0, T0 + i * STEP_MS,
                  area_km2=200.0, max_dbz=dbz, mean_dbz=50.0,
                  p90_dbz=52.0, frame_index=i, cell_id="c{}".format(i))
        for i in range(frames)
    ]
    track = make_track(cells, 1)
    track.motion = {
        "organization_score": org,
        "classification": "Organized Convective Cell",
        "velocity_kmh": 45.0,
        "motion_quality": "reliable",
        "motion_null_reason": None,
        "distance_km": 5.0,
        "duration_min": 5.0 * (frames - 1),
        "_avg_solidity": 0.92,
        "_avg_compactness": 2.5,
    }
    return track


# ---------------------------------------------------------------------------
# (A) is_latest_frame unico
# ---------------------------------------------------------------------------
def test_is_latest_frame_counts_all_frames_including_empty_ones():
    tl = _timeline_with_empty_middle()
    assert tracking.is_latest_frame(2, tl) is True
    assert tracking.is_latest_frame(0, tl) is False
    assert tracking.is_latest_frame(1, tl) is False
    assert tracking.is_latest_frame(None, tl) is False
    assert tracking.is_latest_frame(0, []) is False


def test_two_call_sites_agree_on_timeline_with_empty_middle_frame():
    """Il frame vuoto in mezzo faceva divergere i due call site: forecast
    contava i soli frame NON vuoti (2 -> ultimo indice 1), convective usava
    len(frames)-1 (= 2). Ora entrambi chiamano la stessa funzione."""
    b = models.EngineBundle("ok", "2026-04-30T10:00:00Z", output.SOURCE_LABEL)
    b.cells_by_frame = _timeline_with_empty_middle()
    track = _tracked(frames=3)
    b.tracks = [track]

    # call site 1: forecast senza chiave ctx (percorso libreria/diretto)
    forecast_says = forecast._on_latest_frame({}, track, b)
    # call site 2: convective_structure pubblica on_latest_frame nel record
    cs.evaluate(b, CFG)
    assert len(b.convective_structures) == 1
    cs_says = b.convective_structures[0]["on_latest_frame"]

    assert forecast_says is True
    assert cs_says is True
    assert forecast_says == cs_says


# ---------------------------------------------------------------------------
# (B) continuity_rejections per run
# ---------------------------------------------------------------------------
def _cell_cfg(**over):
    cfg = dict(CONFIG["tracking"])
    cfg.update(over)
    return cfg


def test_cell_continuity_rejections_counter_splits_track():
    tracker = Tracker(_cell_cfg())
    cells = [
        make_cell(12.0, 41.0, T0, cell_id="a"),
        make_cell(12.1, 41.0, T0 + STEP_MS, cell_id="b"),
        make_cell(14.4, 41.0, T0 + 3 * STEP_MS, cell_id="c"),  # ~200 km/10 min
    ]
    for i, cell in enumerate(cells):
        tracker.update([cell], i)
    assert tracker.continuity_rejections == 1
    tracks = tracker.finalize()
    assert len(tracks) == 2
    assert [p.cell_id for p in tracks[0].points] == ["a", "b"]
    assert [p.cell_id for p in tracks[1].points] == ["c"]


def test_cell_continuity_rejections_zero_on_valid_segments():
    tracker = Tracker(_cell_cfg())
    cells = [make_cell(12.0 + 0.01 * i, 41.0, T0 + i * STEP_MS,
                       cell_id="v{}".format(i)) for i in range(5)]
    for i, cell in enumerate(cells):
        tracker.update([cell], i)
    assert tracker.continuity_rejections == 0
    assert len(tracker.finalize()) == 1


def test_storm_continuity_rejections_counter_splits_track():
    cfg = CONFIG["storm"]
    objs_by_frame = []
    for f, lon in enumerate((10.0, 10.06, 20.0, 20.06)):
        cells = [make_cell(lon, 41.0, T0 + f * STEP_MS, frame_index=f,
                           cell_id="c{}-0".format(f))]
        objs_by_frame.append(aggregate_frame(cells, f, cfg))
    tracker = st.StormObjectTracker(cfg)
    for f, objs in enumerate(objs_by_frame):
        tracker.update(objs, f)
    assert tracker.continuity_rejections == 1
    assert len(tracker.finalize()) == 2


def test_storm_continuity_rejections_zero_on_valid_segments():
    cfg = CONFIG["storm"]
    objs_by_frame = []
    for f in range(5):
        lon = 10.0 + 0.06 * f
        cells = [make_cell(lon, 41.0, T0 + f * STEP_MS, frame_index=f,
                           cell_id="c{}-0".format(f))]
        objs_by_frame.append(aggregate_frame(cells, f, cfg))
    tracker = st.StormObjectTracker(cfg)
    for f, objs in enumerate(objs_by_frame):
        tracker.update(objs, f)
    assert tracker.continuity_rejections == 0
    assert len(tracker.finalize()) == 1


def test_continuity_rejections_published_in_metrics_and_latest():
    metrics = build_tracking_metrics(
        [], [], cell_continuity_rejections=2, storm_continuity_rejections=1)
    assert metrics["continuity_rejections"] == 3
    assert metrics["cell"]["continuity_rejections"] == 2
    assert metrics["storm_object"]["continuity_rejections"] == 1
    assert metrics["combined"]["continuity_rejections"] == 3
    b = models.EngineBundle("ok", "2026-04-30T10:00:00Z", output.SOURCE_LABEL)
    b.cells_by_frame = []
    b.tracks = []
    b.tracking_metrics = metrics
    latest = output.build_latest(b)
    assert latest["continuity_rejections"] == 3


def test_main_summary_publishes_continuity_rejections():
    """Wiring statico: riepilogo stampato + GITHUB_OUTPUT + tracking_metrics."""
    from radar_engine import main as main_mod
    src = pathlib.Path(main_mod.__file__).read_text(encoding="utf-8")
    assert '"continuity_rejections": (' in src
    assert 'continuity_rejections={}' in src
    assert "cell_continuity_rejections=getattr(" in src
    assert "storm_continuity_rejections=storm_continuity_rejections" in src
