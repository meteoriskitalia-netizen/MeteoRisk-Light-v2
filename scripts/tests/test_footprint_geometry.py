# -*- coding: utf-8 -*-
"""Test 1.2.4.21 — matching su FOOTPRINT geometrico.

Copertura per contratto:
  * punto DENTRO il poligono -> distanza 0 e hit;
  * punto entro 10 km dal BORDO -> hit; punto a 40 km -> miss;
  * MultiPolygon: dentro il primo poligono, dentro il buco (NON coperto,
    distanza dal bordo del buco), secondo poligono;
  * poligono degenere (vertici collineari / 2 punti): nessuna eccezione,
    area 0, distanza dal contorno misurabile;
  * evento fuori da ogni poligono -> miss con l'allarme comunque presente;
  * integrazione con `supercell_validation`: un allarme con footprint che
    CONTIENE la stazione e' un hit a 0 km anche se il centroide e' a 20 km,
    mentre lo stesso allarme senza footprint resta miss (regressione 1.2.4.20);
  * calibration check: se il 100% delle stazioni cade dentro un poligono, il
    tasso base DEVE dichiarare il POD non informativo.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from validation import asof_bench_footprint as abf  # noqa: E402
from validation import geo_match as gm  # noqa: E402
from validation import supercell_validation as sv  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 7, 1, 13, 20, 0, tzinfo=UTC)

# quadrato 0.10 x 0.10 deg a cavallo di (7.70, 45.20): ~7.86 km di lato
SQUARE = {"type": "Polygon", "coordinates": [[
    [7.60, 45.10], [7.80, 45.10], [7.80, 45.30], [7.60, 45.30], [7.60, 45.10]]]}


def point_in_deg(lon, lat):
    return lon, lat


def test_point_inside_polygon_is_zero_distance():
    lon, lat = 7.70, 45.20
    assert gm.point_in_geometry(lon, lat, SQUARE) is True
    assert gm.distance_to_geometry_km(lon, lat, SQUARE) == 0.0
    hit, dist, idx = gm.point_covered(lon, lat, [SQUARE], 10.0)
    assert hit is True and dist == 0.0 and idx == 0


def test_point_within_ten_km_of_edge_is_a_hit():
    # 0.05 deg a est del bordo (7.80) a lat 45.20: ~3.9 km
    lon, lat = 7.85, 45.20
    assert gm.point_in_geometry(lon, lat, SQUARE) is False
    d = gm.distance_to_geometry_km(lon, lat, SQUARE)
    assert d == pytest.approx(0.05 * 111.32 * 0.7043, rel=0.01)
    hit, dist, _ = gm.point_covered(lon, lat, [SQUARE], 10.0)
    assert hit is True and dist == pytest.approx(d, rel=1e-6)


def test_point_far_away_is_a_miss():
    lon, lat = 9.00, 45.20        # ~100 km a est
    hit, dist, idx = gm.point_covered(lon, lat, [SQUARE], 10.0)
    assert hit is False and idx is None
    # fuori dal buffer: la distanza esatta NON viene calcolata (None, non 0)
    assert dist is None
    # la distanza esatta e' comunque calcolabile quando serve
    assert gm.distance_to_geometry_km(lon, lat, SQUARE) > 10.0


def test_multipolygon_with_hole():
    outer = [[7.60, 45.10], [7.80, 45.10], [7.80, 45.30], [7.60, 45.30],
             [7.60, 45.10]]
    hole = [[7.68, 45.18], [7.72, 45.18], [7.72, 45.22], [7.68, 45.22],
            [7.68, 45.18]]
    second = [[8.20, 45.10], [8.30, 45.10], [8.30, 45.20], [8.20, 45.20],
              [8.20, 45.10]]
    geom = {"type": "MultiPolygon", "coordinates": [[outer, hole], [second]]}
    # centro del buco: NON coperto, distanza dal bordo del buco > 0
    assert gm.point_in_geometry(7.70, 45.20, geom) is False
    d_hole = gm.distance_to_geometry_km(7.70, 45.20, geom)
    assert 1.0 < d_hole < 2.0
    hit, dist, _ = gm.point_covered(7.70, 45.20, [geom], 10.0)
    assert hit is True and dist == pytest.approx(d_hole, rel=1e-6)
    # dentro il primo poligono ma fuori dal buco
    assert gm.point_in_geometry(7.62, 45.12, geom) is True
    # dentro il secondo poligono
    assert gm.point_in_geometry(8.25, 45.15, geom) is True
    assert gm.geometry_area_km2(geom) > 0


def test_degenerate_polygons_do_not_crash():
    # due punti: spezzata, area 0
    line = {"type": "Polygon",
            "coordinates": [[[7.60, 45.10], [7.80, 45.10]]]}
    assert gm.geometry_area_km2(line) == 0.0
    assert gm.point_in_geometry(7.70, 45.10, line) is False
    d = gm.distance_to_geometry_km(7.70, 45.10, line)
    assert d == pytest.approx(0.0, abs=1e-9)
    # tre punti collineari: area 0, nessuna eccezione
    flat = {"type": "Polygon",
            "coordinates": [[[7.60, 45.10], [7.70, 45.10], [7.80, 45.10],
                             [7.60, 45.10]]]}
    assert gm.geometry_area_km2(flat) == pytest.approx(0.0, abs=1e-6)
    assert gm.point_in_geometry(7.70, 45.20, flat) is False
    # geometria senza coordinate
    assert gm.distance_to_geometry_km(7.70, 45.20, None) is None
    assert gm.geometry_bbox(None) is None


def test_no_polygon_contains_the_event_is_a_miss():
    far = {"type": "Polygon", "coordinates": [[
        [12.0, 41.0], [12.1, 41.0], [12.1, 41.1], [12.0, 41.1], [12.0, 41.0]]]}
    hit, dist, _ = gm.point_covered(7.70, 45.20, [far], 10.0)
    assert hit is False
    assert gm.distance_to_geometry_km(7.70, 45.20, far) > 10.0


# ---------------------------------------------------------------------------
# Integrazione con il banco
# ---------------------------------------------------------------------------
def _truth():
    return [sv.TruthEvent(id="ev1", time_utc=T0, lon=7.70, lat=45.20,
                          category="hail", quality="B")]


def _detection(**kwargs):
    base = dict(id="CS-1", time_utc=T0, lon=7.55, lat=45.20, category="strong")
    base.update(kwargs)
    return sv.Detection(**base)


def test_footprint_makes_a_centroid_miss_a_hit():
    params = sv.MatchParams(max_distance_km=10.0, pre_window_min=20.0,
                            post_window_min=6.0)
    # centroide a ~12 km dalla stazione: 1.2.4.20 -> miss
    det_centroid = _detection()
    d_km = sv.haversine_km(7.70, 45.20, det_centroid.lon, det_centroid.lat)
    assert d_km > 10.0
    res_centroid = sv.match_events([det_centroid], _truth(), params)
    assert res_centroid.counts().hits == 0
    assert res_centroid.counts().misses == 1
    # con il footprint che CONTIENE la stazione -> hit a 0 km
    det_fp = _detection(footprint=SQUARE)
    res_fp = sv.match_events([det_fp], _truth(), params)
    assert res_fp.counts().hits == 1
    assert res_fp.matches[0].distance_km == 0.0


def test_footprint_outside_the_gate_stays_a_miss():
    params = sv.MatchParams(max_distance_km=10.0)
    far = {"type": "Polygon", "coordinates": [[
        [12.0, 41.0], [12.1, 41.0], [12.1, 41.1], [12.0, 41.1], [12.0, 41.0]]]}
    res = sv.match_events([_detection(footprint=far)], _truth(), params)
    assert res.counts().hits == 0
    assert res.counts().false_alarms == 1


# ---------------------------------------------------------------------------
# Calibration check del tasso base
# ---------------------------------------------------------------------------
def test_calibration_flags_full_coverage_as_not_informative():
    informative, why = abf.coverage_is_informative(1.0, 1.0)
    assert informative is False and why == "area_fraction"
    informative, why = abf.coverage_is_informative(0.10, 1.0)
    assert informative is False and why == "stations_fraction"
    informative, why = abf.coverage_is_informative(0.10, 0.20)
    assert informative is True and why is None
    # soglia: ESATTAMENTE 50% non e' "oltre il 50%"
    informative, _ = abf.coverage_is_informative(0.50, 0.50)
    assert informative is True


def test_rasterize_and_area_fraction_on_a_small_grid():
    """2x2 pixel a 1 deg: il poligono copre esattamente meta' griglia."""
    transform, shape, _res = gm.grid_spec((0.0, 0.0, 2.0, 2.0), 1.0)
    region = gm.rasterize([{"type": "Polygon", "coordinates": [[
        [0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]]}],
        transform, shape)
    assert int(region.sum()) == 4
    half = {"type": "Polygon", "coordinates": [[
        [0.0, 0.0], [1.0, 0.0], [1.0, 2.0], [0.0, 2.0], [0.0, 0.0]]]}
    cov = gm.coverage_fraction([half], region, transform)
    assert cov["fraction"] == pytest.approx(0.5, rel=1e-9)


def test_stations_100_percent_covered_is_reported():
    """Se OGNI stazione cade dentro un poligono, il conteggio lo dice e il
    tasso base non e' informativo (nessun POD gonfiato)."""
    points = [(lon, lat) for lon in (7.0, 7.5, 8.0) for lat in (44.0, 45.0)]
    big = {"type": "Polygon", "coordinates": [[
        [6.0, 43.0], [9.0, 43.0], [9.0, 46.0], [6.0, 46.0], [6.0, 43.0]]]}
    res = gm.count_points_covered(points, [big], 10.0)
    assert res["n_covered"] == res["n_points"] == len(points)
    informative, why = abf.coverage_is_informative(0.1, res["fraction"])
    assert informative is False and why == "stations_fraction"
