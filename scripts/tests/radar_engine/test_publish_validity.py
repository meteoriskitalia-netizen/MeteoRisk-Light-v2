#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dei due difetti di PUBBLICAZIONE riparati nella 1.2.4.64.

1) detect.py — le sentinelle no-data (-9999 fuori copertura, -9998 in
   copertura senza eco) assorbite dal closing morfologico NON entrano in
   max/mean/p90 dBZ ne' in `pixel_count`/`area_km2`.
2) tracking.py — `velocity_kmh` fuori dal range plausibile di forecast.py
   (path-speed grezza su un ID-switch) NON viene pubblicata: `null` +
   `motion_quality="unreliable"` + `motion_null_reason`; la direzione resta
   solo se e' plausibile la velocita' dello spostamento NETTO.

I casi sintetici sono costruiti per FALLIRE sul codice precedente:
  - T1 due rettangoli con fessura di 1 px a -9999: il closing fa da ponte e
    la sentinella entrava nella media -> mean_dbz = -80.952 con max 40.0;
  - T3 blocco uniforme con UN pixel interno a -9999: la media restava
    "plausibile" ma sbagliata (37.211 invece di 40.000, -2.79 dBZ);
  - T2 track con salto: velocity_kmh = 1008 km/h invece di null.
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
# DIFETTO A — velocity_kmh pubblicata fuori range
# ---------------------------------------------------------------------------
def _run(cells, over=None):
    tracker = Tracker(_cfg(**(over or {})))
    for i, cell in enumerate(cells):
        tracker.update([cell], i)
    tracker.complete_cycles()
    tracker.finalize()
    return tracker.tracks[0]


def test_velocity_out_of_plausible_range_published_as_null():
    """Track con un salto di ~160 km in 5 min (ID-switch): la path-speed e'
    1008 km/h. Non si pubblica un numero aggiustato: null + motivo, e la
    direzione cade con lei perche' anche lo spostamento netto e' impossibile."""
    track = _run([
        make_cell(12.0, 41.0, T0, cell_id="a"),
        make_cell(12.1, 41.0, T0 + 300000, cell_id="b"),
        make_cell(14.0, 41.0, T0 + 600000, cell_id="c"),   # salto ~160 km
    ])
    m = track.motion
    assert m["velocity_kmh"] is None
    assert m["motion_quality"] == "unreliable"
    assert m["motion_null_reason"] == "path_speed_out_of_range"
    assert m["direction_toward_deg"] is None
    assert m["direction_from_deg"] is None
    # l'audit resta misurabile: distanza e durata NON vengono toccate
    assert m["distance_km"] > 150.0
    assert m["duration_min"] == pytest.approx(10.0, abs=0.1)
    # il null arriva fino al file pubblicato (tracks.json)
    d = track.to_dict()
    assert d["velocity_kmh"] is None
    assert d["motion_quality"] == "unreliable"
    assert d["motion_null_reason"] == "path_speed_out_of_range"
    assert d["direction_toward_deg"] is None


def test_direction_kept_only_when_net_displacement_is_plausible():
    """La direzione e' il bearing dello spostamento NETTO: si pubblica solo se
    QUEL vettore e' plausibile. Andata e ritorno -> velocita' di percorso ok
    (~101 km/h) ma netto nullo -> direzione null con motivo proprio."""
    back_and_forth = _run([
        make_cell(12.0, 41.0, T0, cell_id="p0"),
        make_cell(12.1, 41.0, T0 + 300000, cell_id="p1"),
        make_cell(12.2, 41.0, T0 + 600000, cell_id="p2"),
        make_cell(12.1, 41.0, T0 + 900000, cell_id="p3"),
        make_cell(12.0, 41.0, T0 + 1200000, cell_id="p4"),
    ])
    m = back_and_forth.motion
    assert m["velocity_kmh"] is not None            # percorso: ~101 km/h
    assert 70.0 <= m["velocity_kmh"] <= 110.0
    assert m["motion_quality"] == "reliable"
    assert m["direction_toward_deg"] is None        # netto ~0 km
    assert m["direction_from_deg"] is None
    assert m["motion_null_reason"] == "net_speed_out_of_range"

    # caso opposto: percorso impossibile (204 km/h) ma netto plausibile (~68):
    # la velocita' e' null, la DIREZIONE resta (descrive il vettore netto)
    zigzag = _run([
        make_cell(12.0, 41.0, T0, cell_id="z0"),
        make_cell(12.2, 41.0, T0 + 300000, cell_id="z1"),
        make_cell(12.0, 41.0, T0 + 600000, cell_id="z2"),
        make_cell(12.2, 41.0, T0 + 900000, cell_id="z3"),
    ])
    m2 = zigzag.motion
    assert m2["velocity_kmh"] is None
    assert m2["motion_quality"] == "unreliable"
    assert m2["motion_null_reason"] == "path_speed_out_of_range"
    assert m2["direction_toward_deg"] == pytest.approx(90.0, abs=1.0)  # verso E
    assert m2["direction_from_deg"] == pytest.approx(270.0, abs=1.0)


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
    assert m["duration_min"] == 0.0
