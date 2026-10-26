# -*- coding: utf-8 -*-
"""Test del CONSENSO di ``projection`` fra le RUN di un dominio (renderer / index).

Perche' esiste: ``domains.<dominio>.projection`` non e' la proiezione di una run,
e' il fallback che il client (``fmRegionProjection``) applica a OGNI run del
dominio. Le run di uno stesso dominio possono avere proiezioni diverse: misurato
in produzione il 2026-10-26 su ``ecmwf_ifs025``/``north``, dove la run
``20261025T180000Z`` e' un disco stereografico 1000x1000 e la run
``20261025T120000Z`` e' ancora una striscia Mercator 2879x521. Con la proiezione
del dominio presa dalla run piu' recente i 36 frame della run vecchia venivano
disegnati come disco.

Regola verificata qui (la stessa del fix ``09f8158b`` per la chiave di regione):
la chiave di DOMINIO si scrive SOLO se TUTTE le run indicizzate dichiarano la
STESSA proiezione, altrimenti si omette e ogni run conserva la propria in
``runs[]``. Nessun mock del modulo: alberi veri su disco, ``--index-only`` vero.
"""
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MODULE_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                           "render_fm_webp.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("render_fm_webp_runproj_ut",
                                                  MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()

DOMAIN = "ecmwf_ifs025"
REGION = "north"
DISC_META = {"type": "polar_stereographic", "size": 1000, "edge_lat": 20.0,
             "max_lat": 90.0, "radius_px": 499.5}
DISC_BBOX = {"lat_min": 20.0, "lon_min": -180.0, "lat_max": 90.0,
             "lon_max": 180.0}
STRIP_BBOX = {"lat_min": 20.0, "lon_min": -180.0, "lat_max": 85.0,
              "lon_max": 179.75}
RUNS = (("20261026T000000Z", "2026-10-26T00:00:00Z"),
        ("20261025T180000Z", "2026-10-25T18:00:00Z"),
        ("20261025T120000Z", "2026-10-25T12:00:00Z"))


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _manifest(run_id, reference_time, projection, projection_meta=None,
              var="cape"):
    """Manifest di UNA variabile di UNA run, come lo scrive il renderer."""
    disc = projection == mod.POLAR_PROJECTION
    file_name = "2026-10-25T1800.webp"
    man = {
        "version": mod.WEBP_VERSION,
        "generated_at": reference_time,
        "region": REGION,
        "domain": DOMAIN,
        "variable": var,
        "theme": "light",
        "unit": "J/kg",
        "run_id": run_id,
        "reference_time": reference_time,
        "expected_frames": 1,
        "bbox": dict(DISC_BBOX if disc else STRIP_BBOX),
        "bounds": ([[20.0, -180.0], [90.0, 180.0]] if disc
                   else [[20.0, -180.0], [85.0, 179.75]]),
        "grid": {"nx": 1440, "ny": 281},
        "frames": [{"file": file_name, "valid_time": "2026-10-25T18:00Z",
                    "bytes": 7}],
        "valid_times": ["2026-10-25T18:00Z"],
    }
    if projection:
        man["projection"] = projection
    if projection_meta:
        man["projection_meta"] = projection_meta
    return man


def _tree(tmp_path, projections):
    """Albero renderizzato con una proiezione per run (None = striscia)."""
    in_dir = str(tmp_path / "in")
    out_dir = str(tmp_path / "out")
    _write_json(os.path.join(in_dir, DOMAIN, "latest.json"),
                {"domain": DOMAIN, "reference_time": RUNS[0][1],
                 "frames": [], "valid_times": []})
    for (run_id, ref), projection in zip(RUNS, projections):
        meta = dict(DISC_META) if projection == mod.POLAR_PROJECTION else None
        man = _manifest(run_id, ref, projection, meta)
        vdir = os.path.join(out_dir, REGION, DOMAIN, "runs", run_id, "cape")
        os.makedirs(vdir, exist_ok=True)
        with open(os.path.join(vdir, man["frames"][0]["file"]), "wb") as fh:
            fh.write(b"RIFF\x00\x00\x00\x00WEBPfixture")
        _write_json(os.path.join(vdir, "latest.json"), man)
    return in_dir, out_dir


def _rebuild(in_dir, out_dir):
    written = mod.rebuild_index(in_dir, out_dir, [DOMAIN], [REGION], ["light"],
                                region_bboxes=dict(mod.REGIONS))
    assert written == 1
    with open(os.path.join(out_dir, REGION, "index.json"), encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# unita': consensus_projection
# --------------------------------------------------------------------------- #
def test_consensus_returns_the_value_when_every_entry_agrees():
    meta = dict(DISC_META)
    assert mod.consensus_projection(
        [{"projection": "stereographic", "projection_meta": meta},
         {"projection": "stereographic", "projection_meta": meta}]) == ("stereographic", meta)
    assert mod.consensus_projection(
        [{"projection": "mercator"}, {"projection": "mercator"}]) == ("mercator", None)


def test_consensus_is_empty_when_any_entry_disagrees_or_says_nothing():
    assert mod.consensus_projection(
        [{"projection": "stereographic"}, {"projection": "mercator"}]) == (None, None)
    # Un assente vale 'mercator' (default del client): NON concorda col disco.
    assert mod.consensus_projection(
        [{"projection": "stereographic"}, {}]) == (None, None)
    assert mod.consensus_projection(
        [{"projection": "stereographic"}, {"projection": None}]) == (None, None)


def test_consensus_ignores_objects_and_non_dicts():
    # Forma dei primi build del disco: il client la ignora, quindi non e' un valore.
    assert mod.consensus_projection(
        [{"projection": dict(DISC_META)}]) == (None, None)
    assert mod.consensus_projection([{"projection": 42}]) == (None, None)
    assert mod.consensus_projection([None]) == (None, None)
    assert mod.consensus_projection([]) == (None, None)
    assert mod.consensus_projection(None) == (None, None)


# --------------------------------------------------------------------------- #
# --index-only: la regressione MISURATA (run miste nello stesso dominio)
# --------------------------------------------------------------------------- #
def test_domain_key_omitted_when_runs_disagree_and_runs_keep_their_own(tmp_path):
    """Lo stato reale del 2026-10-26: run nuova su disco, run vecchia a striscia."""
    in_dir, out_dir = _tree(tmp_path, ["stereographic", None, None])
    idx = _rebuild(in_dir, out_dir)
    dom = idx["domains"][DOMAIN]
    # Il dominio NON dichiara: le run discordano (era il difetto).
    assert "projection" not in dom
    assert "projection_meta" not in dom
    # Ogni RUN resta leggibile con la PROPRIA proiezione.
    by_id = {r["id"]: r for r in dom["runs"]}
    assert by_id["20261026T000000Z"]["projection"] == "stereographic"
    assert by_id["20261026T000000Z"]["projection_meta"] == DISC_META
    assert "projection" not in by_id["20261025T180000Z"]
    assert "projection" not in by_id["20261025T120000Z"]
    # La run vecchia porta anche i SUOI bounds (20..85), non quelli del disco.
    assert by_id["20261025T180000Z"]["bounds"] == [[20.0, -180.0], [85.0, 179.75]]
    # La chiave di regione segue il consenso dei domini: qui c'e' un solo dominio
    # e ha perso la chiave, quindi cade anche quella di regione.
    assert "projection" not in idx


def test_domain_key_kept_when_every_run_is_the_disc(tmp_path):
    in_dir, out_dir = _tree(tmp_path, ["stereographic"] * len(RUNS))
    idx = _rebuild(in_dir, out_dir)
    dom = idx["domains"][DOMAIN]
    assert dom["projection"] == "stereographic"
    assert dom["projection_meta"] == DISC_META
    assert [r["projection"] for r in dom["runs"]] == ["stereographic"] * len(RUNS)
    # Uniforme anche fra i domini (uno solo) -> la chiave di regione si scrive.
    assert idx["projection"] == "stereographic"


def test_no_key_at_all_when_every_run_is_the_mercator_strip(tmp_path):
    in_dir, out_dir = _tree(tmp_path, [None] * len(RUNS))
    idx = _rebuild(in_dir, out_dir)
    dom = idx["domains"][DOMAIN]
    assert "projection" not in dom
    assert "projection_meta" not in dom
    assert all("projection" not in r for r in dom["runs"])
    assert "projection" not in idx


def test_single_run_domain_keeps_behaving_as_before(tmp_path):
    """Il caso normale (una run indicizzata) non cambia: la chiave c'e'."""
    in_dir = str(tmp_path / "in")
    out_dir = str(tmp_path / "out")
    run_id, ref = RUNS[0]
    _write_json(os.path.join(in_dir, DOMAIN, "latest.json"),
                {"domain": DOMAIN, "reference_time": ref})
    man = _manifest(run_id, ref, "stereographic", dict(DISC_META))
    vdir = os.path.join(out_dir, REGION, DOMAIN, "runs", run_id, "cape")
    os.makedirs(vdir, exist_ok=True)
    with open(os.path.join(vdir, man["frames"][0]["file"]), "wb") as fh:
        fh.write(b"RIFF\x00\x00\x00\x00WEBPfixture")
    _write_json(os.path.join(vdir, "latest.json"), man)
    idx = _rebuild(in_dir, out_dir)
    assert idx["domains"][DOMAIN]["projection"] == "stereographic"
    assert idx["domains"][DOMAIN]["projection_meta"] == DISC_META
    assert idx["projection"] == "stereographic"


def test_a_silent_run_blocks_the_domain_key_even_with_a_disc_run(tmp_path):
    """Manifest legacy senza `projection` = striscia Mercator: non concorda."""
    in_dir, out_dir = _tree(tmp_path, ["stereographic"] + [None] * (len(RUNS) - 1))
    dom = _rebuild(in_dir, out_dir)["domains"][DOMAIN]
    assert "projection" not in dom
