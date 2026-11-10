#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dei due difetti di PUBBLICAZIONE riparati nella 1.2.4.64.

1) detect.py — le sentinelle no-data (-9999 fuori copertura, -9998 in
   copertura senza eco) assorbite dal closing morfologico NON entrano in
   max/mean/p90 dBZ ne' in `pixel_count`/`area_km2`.
2) tracking.py — NESSUNA velocity impossibile pubblicata (contratto
   2026-11-10): il gate di continuita' rifiuta l'associazione oltre ~110 km/h
   (la detection apre una nuova track) e `finalize` pubblica la velocity solo
   dal fit pesato con residuo < 6 km; altrimenti `null` +
   `motion_quality="unreliable"` + `motion_null_reason` e `motion_source`.

I casi sintetici sono costruiti per FALLIRE sul codice precedente:
  - T1 due rettangoli con fessura di 1 px a -9999: il closing fa da ponte e
    la sentinella entrava nella media -> mean_dbz = -80.952 con max 40.0;
  - T3 blocco uniforme con UN pixel interno a -9999: la media restava
    "plausibile" ma sbagliata (37.211 invece di 40.000, -2.79 dBZ);
  - T2 track con salto: una path-speed di ~1200 km/h veniva pubblicata.
Nessun dato radar reale: solo raster sintetici (stessa griglia VMI dei test
esistenti) e celle sintetiche.
"""

import numpy as np
import pytest

from radar_engine import models
from radar_engine.config import CONFIG
from radar_engine.detect import detect_cells
from radar_engine.preprocess import read_raster
from radar_engine.tracking import Tracker

from conftest import blank_grid, make_cell, write_synthetic_tif

T0 = 1_777_027_200_000          # base fissa (2026-04-30), come test_tracking


def _detect(arr, path):
    write_synthetic_tif(str(path), arr)
    raster = read_raster(str(path),
                        nodata_values=CONFIG["preprocess"]["nodata_values"])
    return detect_cells(raster, CONFIG["detect"]), raster


def _cfg(**over):
    cfg = dict(CONFIG["tracking"])
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------------------
# DIFETTO B — sentinelle no-data nella media dBZ
# ---------------------------------------------------------------------------
def test_sentinel_bridged_by_closing_excluded_from_mean(tmp_path):
    """Il closing unisce due echi attraverso una fessura di 1 px: la sentinella
    che finisce nel componente non e' un'eco e non entra ne' nella media ne'
    nell'area. Sul codice precedente questa cella usciva con mean_dbz=-80.952."""
    arr = blank_grid()
    arr[500:541, 500:541] = 40.0
    arr[500:541, 542:583] = 40.0
    arr[500:541, 541] = -9999.0     # fessura di 1 px: il closing (r=1) la copre
    cells, raster = _detect(arr, tmp_path / "bridge.tif")
    assert len(cells) == 1
    c = cells[0]
    # la FORMA morfologica ha fatto da ponte (41 righe, 83 colonne: 41+1+41)
    minr, minc, maxr, maxc = c.bbox
    assert (maxr - minr, maxc - minc) == (40, 82)
    # ... ma i 41 pixel di sentinella non sono ne' intensita' ne' area
    assert c.mean_dbz == pytest.approx(40.0, abs=1e-9)
    assert c.max_dbz == pytest.approx(40.0, abs=1e-9)
    assert c.p90_dbz == pytest.approx(40.0, abs=1e-9)
    assert c.pixel_count == 41 * 41 * 2
    assert c.area_km2 == pytest.approx(41 * 41 * 2 * raster.pixel_area_km2)
    assert c.mean_dbz <= c.max_dbz


def test_sentinel_pixel_hole_excluded_and_healthy_cell_unchanged(tmp_path):
    """Una cella SANA (eco uniforme) deve dare gli STESSI numeri con o senza un
    pixel no-data che il closing assorbe: la media e' quella dell'eco (40.0).
    Sul codice precedente il buco di 1 px la portava a 37.211 (-2.79 dBZ) pur
    restando un valore "plausibile": il caso peggiore, perche' invisibile a un
    controllo di range fisico."""
    clean = blank_grid()
    clean[500:560, 500:560] = 40.0
    holed = clean.copy()
    holed[530, 530] = -9999.0       # buco interno di 1 px

    cells_clean, raster_clean = _detect(clean, tmp_path / "clean.tif")
    cells_hole, _raster_hole = _detect(holed, tmp_path / "hole.tif")
    assert len(cells_clean) == 1 and len(cells_hole) == 1
    a, b = cells_clean[0], cells_hole[0]

    # intensita': identica (il pixel no-data non e' un'eco)
    assert a.mean_dbz == pytest.approx(40.0, abs=1e-12)
    assert b.mean_dbz == pytest.approx(a.mean_dbz, abs=1e-12)
    assert b.max_dbz == pytest.approx(a.max_dbz, abs=1e-12)
    assert b.p90_dbz == pytest.approx(a.p90_dbz, abs=1e-12)
    # area: esattamente un pixel in meno (il no-data non e' area della cella)
    assert b.pixel_count == a.pixel_count - 1
    assert b.area_km2 == pytest.approx(
        a.area_km2 - raster_clean.pixel_area_km2, abs=1e-12)
    # geometria: il buco viene CHIUSO, la forma resta quella del blocco sano
    assert b.eccentricity == pytest.approx(a.eccentricity, abs=1e-12)
    assert b.solidity == pytest.approx(a.solidity, abs=1e-12)
    assert b.compactness == pytest.approx(a.compactness, abs=1e-12)


# ---------------------------------------------------------------------------
# DIFETTO A — nessuna velocity impossibile pubblicata (contratto 2026-11-10)
# ---------------------------------------------------------------------------
def _run(cells, over=None):
    tracker = Tracker(_cfg(**(over or {})))
    for i, cell in enumerate(cells):
        tracker.update([cell], i)
    tracker.complete_cycles()
    tracker.finalize()
    return tracker.tracks


def test_id_switch_is_split_by_continuity_gate():
    """Track con salto di ~200 km in 10 min (ID-switch): il gate di
    continuita' rifiuta l'associazione e la detection apre una NUOVA track.
    La vecchia path-speed di ~1200 km/h non entra piu' nel dataset."""
    tracks = _run([
        make_cell(12.0, 41.0, T0, cell_id="a"),
        make_cell(12.1, 41.0, T0 + 300000, cell_id="b"),
        make_cell(14.4, 41.0, T0 + 900000, cell_id="c"),   # ~200 km in 10 min
    ])
    assert len(tracks) == 2
    old, new = tracks
    assert [p.cell_id for p in old.points] == ["a", "b"]
    assert [p.cell_id for p in new.points] == ["c"]
    assert models.EVENT_DEATH in old.events
    for trk in tracks:
        vel = trk.motion.get("velocity_kmh")
        assert vel is None or 1.0 <= vel <= 110.0
        assert trk.motion.get("motion_source") in (
            "weighted_fit", "fallback", "none")


def test_regular_track_publishes_weighted_fit_motion():
    """Track regolare (5 punti, 60 km/h): velocity e direzione vengono dal
    fit pesato, non dalla path-speed; `motion_source` lo dichiara anche nel
    record pubblicato (tracks.json)."""
    km_per_deg_lon = 111.1949 * np.cos(np.radians(41.0))
    dlon = (60.0 * 300 / 3600.0) / km_per_deg_lon   # 5 km ogni 5 min
    cells = [make_cell(12.0 + i * dlon, 41.0, T0 + i * 300000,
                       cell_id=f"r{i}") for i in range(5)]
    tracks = _run(cells)
    assert len(tracks) == 1
    m = tracks[0].motion
    assert m["motion_source"] == "weighted_fit"
    assert m["motion_quality"] == "reliable"
    assert m["motion_null_reason"] is None
    assert m["velocity_kmh"] == pytest.approx(60.0, abs=1.0)
    assert m["direction_toward_deg"] == pytest.approx(90.0, abs=2.0)
    d = tracks[0].to_dict()
    assert d["motion_source"] == "weighted_fit"


def test_short_track_has_declared_motion_null_reason():
    """3 punti: il fit richiede >=4 punti -> null con motivo esplicito,
    nessun valore sostitutivo."""
    tracks = _run([
        make_cell(12.0, 41.0, T0, cell_id="s0"),
        make_cell(12.1, 41.0, T0 + 300000, cell_id="s1"),
        make_cell(12.2, 41.0, T0 + 600000, cell_id="s2"),
    ])
    m = tracks[0].motion
    assert m["velocity_kmh"] is None
    assert m["direction_toward_deg"] is None
    assert m["motion_null_reason"] == "fit_not_used"
    assert m["motion_source"] == "fallback"


def test_non_positive_duration_has_declared_reason():
    """Track con due punti sullo STESSO istante: nessuna velocita' calcolabile.
    Il campo non resta assente: null + motivo dichiarato."""
    tracker = Tracker(_cfg())
    tracker.update([make_cell(12.0, 41.0, T0, cell_id="s0")], 0)
    tracker.update([make_cell(12.05, 41.0, T0, cell_id="s1")], 1)
    tracker.complete_cycles()
    tracker.finalize()
    m = tracker.tracks[0].motion
    assert m["velocity_kmh"] is None
    assert m["motion_quality"] == "unreliable"
    assert m["motion_null_reason"] == "non_positive_duration"
    assert m["motion_source"] == "none"
    assert m["duration_min"] == 0.0
