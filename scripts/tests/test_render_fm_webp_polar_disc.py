# -*- coding: utf-8 -*-
"""Test del DISCO POLARE di render_fm_webp.py (attivazione in produzione 1.2.4.30).

Due difetti bloccanti, riparati qui e bloccati da questi test:

1. ``projection`` era un OGGETTO (``polar_projection_meta``). Il client legge
   ``typeof entry.projection === 'string'`` e qualunque altra cosa finisce su
   ``'mercator'``: il disco gia' proiettato veniva riproiettato per pixel
   (misurato: 4,23% di accordo invece di 99,96%). Ora il manifest porta la
   STRINGA ``"stereographic"`` e la geometria sta in ``projection_meta``.
2. L'overlay (isolinee/etichette/frecce) era composto SENZA clip circolare:
   224 px (pressure_msl) .. 2.237 px (wind_speed_850hPa) restavano con
   alpha > 0 fino a rho = 1,0118, cioe' ~4,7 px fuori dal bordo del disco.

Nessun mock del sistema sotto test: si usano gli alberi sintetici su tmp_path
(stessa forma dei manifest reali) e le funzioni vere del renderer.
"""
import importlib.util
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_fm_webp_polar_under_test",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()

POLAR_META = {
    "type": "polar_stereographic",
    "center_lat": 90.0,
    "central_lon": 0.0,
    "edge_lat": 20.0,
    "max_lat": 90.0,
    "size": 1000,
    "radius_px": 499.5,
    "formula": "rho = tan((90-lat)/2) / tan((90-edge_lat)/2)",
}
DISC_BBOX = {"lat_min": 20.0, "lon_min": -180.0,
             "lat_max": 90.0, "lon_max": 180.0}


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _polar_crop(size=1000):
    """Crop polare REALE, costruito dalla geometria del renderer."""
    cgrid = {"nx": 1440, "ny": 281, "lonMin": -180.0, "latMin": 20.0,
             "dx": 0.25, "dy": 0.25}
    return mod._finalise_crop({
        "kind": "polar",
        "row0": 0, "row1": 281, "col0": 0, "col1": 1440,
        "grid": cgrid, "scale": 1,
        "disc_size": size,
        "edge_lat": mod.NORTH_DISC_EDGE_LAT,
        "max_lat": 90.0,
        "font_px": 18,
    })


# --------------------------------------------------------------------------- #
# 1. Il tipo di `projection`
# --------------------------------------------------------------------------- #
def test_polar_crop_publishes_projection_as_string():
    crop = _polar_crop()
    assert crop["projection"] == mod.POLAR_PROJECTION == "stereographic"
    assert isinstance(crop["projection"], str), (
        "il client legge solo una STRINGA: un oggetto diventa 'mercator'")
    assert isinstance(crop["projection_meta"], dict)
    assert crop["projection_meta"]["type"] == "polar_stereographic"
    assert crop["projection_meta"]["edge_lat"] == 20.0
    assert crop["projection_meta"]["size"] == 1000


def test_strip_crop_writes_no_projection_key():
    cgrid = {"nx": 40, "ny": 20, "lonMin": 6.5, "latMin": 36.5,
             "dx": 0.25, "dy": 0.25}
    crop = mod._finalise_crop({
        "row0": 0, "row1": 20, "col0": 0, "col1": 40,
        "grid": cgrid, "scale": 2, "font_px": 12,
    })
    assert "projection" not in crop
    assert "projection_meta" not in crop


def test_projection_parts_splits_the_legacy_object():
    assert mod.projection_parts("stereographic") == ("stereographic", None)
    assert mod.projection_parts("  STEREOGRAPHIC ") == ("stereographic", None)
    legacy = dict(POLAR_META)
    assert mod.projection_parts(legacy) == ("stereographic", legacy)
    assert mod.projection_parts(None) == (None, None)
    assert mod.projection_parts(42) == (None, None)
    # Un oggetto di tipo diverso non e' un disco: nessuna proiezione.
    assert mod.projection_parts({"type": "mercator"}) == (None, {"type": "mercator"})


def test_write_region_index_publishes_string_and_meta(tmp_path):
    out = str(tmp_path / "out")
    mod.write_region_index(out, "north", (20.0, -180.0, 90.0, 180.0),
                           {"domA": {"variables": {}}}, ["light"],
                           projection="stereographic",
                           projection_meta=dict(POLAR_META))
    with open(os.path.join(out, "north", "index.json"), encoding="utf-8") as fh:
        idx = json.load(fh)
    assert idx["projection"] == "stereographic"
    assert isinstance(idx["projection"], str)
    assert idx["projection_meta"] == POLAR_META
    # Senza proiezione (striscia Mercator) le chiavi NON devono comparire.
    mod.write_region_index(out, "italia", (36.5, 6.5, 47.2, 18.6),
                           {"domA": {"variables": {}}}, ["light"])
    with open(os.path.join(out, "italia", "index.json"), encoding="utf-8") as fh:
        idx = json.load(fh)
    assert "projection" not in idx
    assert "projection_meta" not in idx


# --------------------------------------------------------------------------- #
# 2. La maschera circolare applicata DOPO l'overlay
# --------------------------------------------------------------------------- #
def test_mask_rgba_to_disc_zeroes_only_outside():
    np = pytest.importorskip("numpy")
    size = 101
    geo = mod.polar_disc_geometry(size)
    mask = geo["mask"]
    rgba = np.full((size, size, 4), 200, dtype=np.uint8)
    rgba[:, :, 3] = 255
    out = mod.mask_rgba_to_disc(rgba, mask)
    outside = ~mask
    assert outside.any() and mask.any()
    assert int(out[outside].sum()) == 0, "alpha/RGB fuori dal cerchio devono essere 0"
    assert np.array_equal(out[mask], rgba[mask]), "dentro il cerchio: byte-identici"
    # La geometria: rho = 1 al bordo (20 N), 0 al polo.
    assert geo["mask"][size // 2, size // 2]
    assert not geo["mask"][0, 0]
    assert mod.polar_projection_meta(size, 20.0, 90.0)["radius_px"] == (size - 1) * 0.5


def test_disc_mask_is_the_20n_circle():
    np = pytest.importorskip("numpy")
    geo = mod.polar_disc_geometry(1000)
    # 783.764 px dentro il cerchio misurati sul disco 1000x1000 reale.
    assert int(geo["mask"].sum()) == 783764
    jj, ii = np.mgrid[0:1000, 0:1000]
    rho = np.hypot(ii - 499.5, jj - 499.5) / 499.5
    # Il taglio e' ESATTAMENTE rho <= 1: l'ultimo pixel dentro sta a 0.99999
    # (nessun centro di pixel cade sul bordo), il primo fuori sta sopra 1.
    assert 0.9999 < float(rho[geo["mask"]].max()) <= 1.0 + 1e-9
    assert float(rho[~geo["mask"]].min()) > 1.0


# --------------------------------------------------------------------------- #
# 3. --index-only: preserva la stringa, sposta l'oggetto legacy, bbox al polo
# --------------------------------------------------------------------------- #
def _manifest(region, domain, var, run_id, reference_time, projection=None,
              projection_meta=None):
    man = {
        "version": mod.WEBP_VERSION,
        "generated_at": "2026-10-25T19:00:00Z",
        "region": region,
        "domain": domain,
        "variable": var,
        "theme": "light",
        "unit": "hPa",
        "bbox": dict(DISC_BBOX),
        "bounds": [[20.0, -180.0], [90.0, 180.0]],
        "grid": {"nx": 1440, "ny": 281},
        "run_id": run_id,
        "reference_time": reference_time,
        "expected_frames": 1,
        "frames": [{"file": "2026-10-25T1800.webp",
                    "valid_time": "2026-10-25T18:00Z", "bytes": 7}],
        "valid_times": ["2026-10-25T18:00Z"],
    }
    if projection is not None:
        man["projection"] = projection
    if projection_meta is not None:
        man["projection_meta"] = projection_meta
    return man


def _index_tree(tmp_path, projection, projection_meta):
    in_dir = str(tmp_path / "in")
    out_dir = str(tmp_path / "out")
    os.makedirs(os.path.join(in_dir, "domA"))
    _write_json(os.path.join(in_dir, "domA", "latest.json"),
                {"domain": "domA", "reference_time": "2026-10-25T18:00Z",
                 "frames": [], "valid_times": []})
    tdir = os.path.join(out_dir, "north", "domA", "runs", "20261025T180000Z",
                        "pressure_msl")
    os.makedirs(tdir)
    with open(os.path.join(tdir, "2026-10-25T1800.webp"), "wb") as fh:
        fh.write(b"RIFF\x00\x00\x00\x00WEBPdummy")
    _write_json(os.path.join(tdir, "latest.json"),
                _manifest("north", "domA", "pressure_msl",
                          "20261025T180000Z", "2026-10-25T18:00Z",
                          projection, projection_meta))
    return in_dir, out_dir


def _rebuild(in_dir, out_dir):
    return mod.rebuild_index(in_dir, out_dir, ["domA"], ["north"], ["light"],
                             region_bboxes=dict(mod.REGIONS))


def test_index_only_preserves_the_projection_string(tmp_path):
    in_dir, out_dir = _index_tree(tmp_path, "stereographic", dict(POLAR_META))
    assert _rebuild(in_dir, out_dir) == 1
    with open(os.path.join(out_dir, "north", "index.json"), encoding="utf-8") as fh:
        idx = json.load(fh)
    assert idx["projection"] == "stereographic"
    assert isinstance(idx["projection"], str)
    assert idx["projection_meta"] == POLAR_META
    dom = idx["domains"]["domA"]
    assert dom["projection"] == "stereographic"
    assert isinstance(dom["runs"][0]["projection"], str)
    # Il disco arriva al polo: la bbox di regione non puo' fermarsi a 85.05 N.
    assert idx["bbox"] == {"lat_min": 20.0, "lon_min": -180.0,
                           "lat_max": 90.0, "lon_max": 180.0}
    assert dom["bounds"] == [[20.0, -180.0], [90.0, 180.0]]


def test_index_only_heals_a_legacy_projection_object(tmp_path):
    in_dir, out_dir = _index_tree(tmp_path, dict(POLAR_META), None)
    assert _rebuild(in_dir, out_dir) == 1
    with open(os.path.join(out_dir, "north", "index.json"), encoding="utf-8") as fh:
        idx = json.load(fh)
    assert idx["projection"] == "stereographic"
    assert isinstance(idx["projection"], str)
    assert idx["projection_meta"]["type"] == "polar_stereographic"
    assert idx["projection_meta"]["edge_lat"] == 20.0


def test_index_only_keeps_the_mercator_strip_untouched(tmp_path):
    in_dir, out_dir = _index_tree(tmp_path, None, None)
    assert _rebuild(in_dir, out_dir) == 1
    with open(os.path.join(out_dir, "north", "index.json"), encoding="utf-8") as fh:
        idx = json.load(fh)
    assert "projection" not in idx
    assert "projection_meta" not in idx
    assert idx["bbox"]["lat_max"] == 85.05112878


