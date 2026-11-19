#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Campi top-level nuovi dell'output mesociclone (1.3.0).

Verifica che MesocycloneTrack.to_dict() esponga lat/lon e rotation_diameter
con provenienza dichiarata (source/reference/null_reason) e senza valori
fabbricati. Questi test falliscono sul codice 1.3.0.20 (solo 11 campi nel
test di completezza: lat/lon esistevano solo dentro points[], rotation_diameter
non esisteva affatto).

La documentazione parla di "13 campi": 11 campi storici + la posizione
(lat/lon, un campo logico espresso da due chiavi) + rotation_diameter.
In termini di chiavi del dict sono quindi 14 chiavi concrete.
"""

import math

from radar_engine import models


# 11 campi storici + 3 chiavi nuove (lat, lon, rotation_diameter).
EXPECTED_TRACK_KEYS = (
    "meso_id", "first_detection", "last_detection", "persistence",
    "classification", "parent_cell_ids", "events", "points",
    "source", "reference", "limits",
    "lat", "lon", "rotation_diameter",
)
LEGACY_TRACK_KEYS = EXPECTED_TRACK_KEYS[:11]


def _make_track(points, source="OPERA FR VRADH", reference="Doppler test ref"):
    tr = models.MesocycloneTrack("MESO-TEST", "2026-11-19T12:00:00Z")
    tr.source = source
    tr.reference = reference
    tr.points = list(points)
    tr.persistence = len(points)
    tr.max_shear_ms_km = 4.0
    tr.max_delta_velocity_ms = 30.0
    tr.classification = models.ROTATION_MESOCYCLONE_CANDIDATE
    return tr


def _point(lat, lon, shear=None, distance=None):
    p = {"valid_time": "2026-11-19T12:00:00Z", "lat": lat, "lon": lon}
    if shear is not None:
        p["azimuthal_shear_ms_km"] = shear
    if distance is not None:
        p["couplet_distance_km"] = distance
    return p


# ---------------------------------------------------------------------------
# 1. Completezza: fallisce sul codice vecchio (11 chiavi)
# ---------------------------------------------------------------------------
def test_track_output_has_all_new_top_level_fields():
    tr = _make_track([_point(42.0, 9.5)])
    d = tr.to_dict()
    for key in EXPECTED_TRACK_KEYS:
        assert key in d, "missing top-level field: %s" % key
    assert "field_meta" in d
    assert set(d["field_meta"]) == {"lat", "lon", "rotation_diameter"}


def test_new_keys_are_top_level_not_only_inside_points():
    tr = _make_track([_point(42.0, 9.5)])
    d = tr.to_dict()
    for key in ("lat", "lon", "rotation_diameter"):
        assert key in d, "%s must be a top-level key" % key
    # lat/lon esistevano gia' dentro points[]: la novita' e' che ora sono
    # anche top-level. rotation_diameter non era da nessuna parte.
    assert any("lat" in p and "lon" in p for p in d["points"])
    assert all("rotation_diameter" not in p for p in d["points"])


# ---------------------------------------------------------------------------
# 2. lat/lon: punto con massima shear, fallback al primo punto
# ---------------------------------------------------------------------------
def test_lat_lon_come_from_max_shear_point():
    tr = _make_track([
        _point(41.0, 9.0, shear=2.0),
        _point(42.123456, 9.654321, shear=9.0),
        _point(43.0, 10.0, shear=4.0),
    ])
    d = tr.to_dict()
    assert d["lat"] == 42.12346          # arrotondato a 5 decimali
    assert d["lon"] == 9.65432
    assert d["field_meta"]["lat"]["selection"] == "max_shear_point"
    assert d["field_meta"]["lon"]["selection"] == "max_shear_point"
    assert d["field_meta"]["lat"]["null_reason"] is None


def test_lat_lon_fall_back_to_first_point_without_per_point_shear():
    tr = _make_track([_point(41.11111, 9.11111), _point(42.0, 9.5)])
    d = tr.to_dict()
    assert d["lat"] == 41.11111
    assert d["lon"] == 9.11111
    assert d["field_meta"]["lat"]["selection"] == "first_point_no_per_point_shear"
    assert "fallback" in d["field_meta"]["lat"]["reference"]


def test_lat_lon_null_with_reason_when_no_valid_point():
    tr = _make_track([{"lat": "nope", "lon": None}, {"valid_time": "x"}])
    d = tr.to_dict()
    assert d["lat"] is None and d["lon"] is None
    assert d["field_meta"]["lat"]["null_reason"] == "no_point_with_valid_lat_lon"
    assert d["field_meta"]["lon"]["null_reason"] == "no_point_with_valid_lat_lon"


# ---------------------------------------------------------------------------
# 3. rotation_diameter: 2 * couplet_distance_km, mai valore inventato
# ---------------------------------------------------------------------------
def test_rotation_diameter_is_twice_couplet_distance():
    tr = _make_track([_point(42.0, 9.5, shear=5.0, distance=3.5)])
    d = tr.to_dict()
    assert d["rotation_diameter"] == 7.0
    assert d["field_meta"]["rotation_diameter"]["null_reason"] is None
    assert d["field_meta"]["rotation_diameter"]["selection"] == "max_shear_point"


def test_rotation_diameter_uses_first_available_distance_when_peak_lacks_it():
    tr = _make_track([
        _point(42.0, 9.5, shear=9.0),
        _point(43.0, 10.0, shear=2.0, distance=1.25),
    ])
    d = tr.to_dict()
    assert d["rotation_diameter"] == 2.5
    assert d["field_meta"]["rotation_diameter"]["selection"] == "first_available_point"


def test_rotation_diameter_null_with_reason_when_distance_unavailable():
    tr = _make_track([_point(42.0, 9.5, shear=4.0)])
    d = tr.to_dict()
    assert d["rotation_diameter"] is None
    meta = d["field_meta"]["rotation_diameter"]
    assert meta["null_reason"] == "couplet_distance_km_unavailable_in_points"
    assert meta["selection"] == "none"
    assert "2 * couplet_distance_km" in meta["reference"]
    assert "SCELTA INGEGNERISTICA" in meta["reference"]


def test_rotation_diameter_rejects_invalid_distance():
    tr = _make_track([
        _point(42.0, 9.5),
        {"lat": 43.0, "lon": 10.0, "couplet_distance_km": -1.0},
        {"lat": 44.0, "lon": 11.0, "couplet_distance_km": float("nan")},
    ])
    d = tr.to_dict()
    assert d["rotation_diameter"] is None
    assert d["field_meta"]["rotation_diameter"]["null_reason"] == (
        "couplet_distance_km_unavailable_in_points")


# ---------------------------------------------------------------------------
# 4. Provenienza dei campi nuovi
# ---------------------------------------------------------------------------
def test_new_fields_carry_source_reference_and_null_reason():
    tr = _make_track([_point(42.0, 9.5, shear=4.0, distance=2.0)])
    d = tr.to_dict()
    for key in ("lat", "lon", "rotation_diameter"):
        meta = d["field_meta"][key]
        assert set(("source", "reference", "null_reason", "selection")) <= set(meta)
        assert meta["source"] == "OPERA FR VRADH"
        assert meta["reference"].startswith("Doppler test ref | ")
        assert meta["null_reason"] is None
        assert isinstance(meta["selection"], str) and meta["selection"] != "none"


def test_new_fields_source_fallback_has_no_fabricated_provenance():
    tr = _make_track([_point(42.0, 9.5)], source=None, reference=None)
    d = tr.to_dict()
    assert d["source"] is None
    for key in ("lat", "lon", "rotation_diameter"):
        meta = d["field_meta"][key]
        assert meta["source"] == "MesocycloneTrack.points[]"
        assert meta["reference"]


# ---------------------------------------------------------------------------
# 5. Gli 11 campi storici restano invariati (nessuna regressione)
# ---------------------------------------------------------------------------
def test_legacy_eleven_fields_are_unchanged():
    tr = _make_track([_point(42.0, 9.5, shear=4.0)])
    tr.events.append(models.ROTATION_EV_APPEARANCE)
    tr.parent_cell_ids.append("CELL-1")
    tr.cell_coherent = True
    tr.quality_score = 0.81234
    tr.max_rotation_depth_km = 2.345
    tr.max_vertical_continuity = 0.91234
    tr.last_detection = "2026-11-19T12:05:00Z"
    tr.limits = ["limit-a"]
    d = tr.to_dict()
    assert set(LEGACY_TRACK_KEYS) <= set(d)
    assert d["meso_id"] == "MESO-TEST"
    assert d["first_detection"] == "2026-11-19T12:00:00Z"
    assert d["last_detection"] == "2026-11-19T12:05:00Z"
    assert d["persistence"] == 1
    assert d["classification"] == models.ROTATION_MESOCYCLONE_CANDIDATE
    assert d["max_shear_ms_km"] == 4.0
    assert d["max_delta_velocity_ms"] == 30.0
    assert d["max_rotation_depth_km"] == 2.35
    assert d["max_vertical_continuity"] == 0.912
    assert d["cell_coherent"] is True
    assert d["quality_score"] == 0.812
    assert d["parent_cell_ids"] == ["CELL-1"]
    assert d["events"] == [models.ROTATION_EV_APPEARANCE]
    assert d["points"] == tr.points
    assert d["source"] == "OPERA FR VRADH"
    assert d["reference"] == "Doppler test ref"
    assert d["limits"] == ["limit-a"]


def test_to_dict_does_not_mutate_points():
    pts = [_point(42.0, 9.5, shear=4.0, distance=2.0)]
    d = _make_track(pts).to_dict()
    assert d["points"] == pts
    assert all(set(p) == {"valid_time", "lat", "lon",
                          "azimuthal_shear_ms_km", "couplet_distance_km"} for p in pts)


def test_non_numeric_shear_and_distance_are_ignored():
    tr = _make_track([
        {"lat": 42.0, "lon": 9.5, "azimuthal_shear_ms_km": "x",
         "couplet_distance_km": "n/a"},
        _point(43.0, 10.0, shear=3.0, distance=2.0),
    ])
    d = tr.to_dict()
    assert d["lat"] == 43.0 and d["lon"] == 10.0
    assert d["field_meta"]["lat"]["selection"] == "max_shear_point"
    assert d["rotation_diameter"] == 4.0
