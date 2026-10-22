# -*- coding: utf-8 -*-
"""Test della modalita' --index-only di render_fm_webp.py (hotfix CI 1.2.4.8.1).

1.2.4.13: l'indice per regione e' MULTI-RUN. Il renderer pubblica ogni run del
modello sotto ``<out>/<regione>/<dominio>/runs/<run_id>/<variabile>/`` (dove
``run_id`` = reference_time del manifest engine sanitizzato) e l'indice espone,
per ogni dominio, ``current_run`` (l'id della run piu' recente) e ``runs`` (la
lista delle run piu' recenti, max 3, ordinate per reference_time decrescente).
Ogni run dichiara ``expected_frames``, ``frames_available`` e
``completion_pct``.

Alberi di test sintetici: NESSUNA dipendenza di rete ne' dati .om reali, NESSUN
mock del sistema sotto test (i manifest/latest.json/.webp sono file reali su un
tmp_path). Il modulo viene caricato via importlib per percorso perche'
scripts/forecast_v2 non e' un package.
"""
import importlib.util
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_fm_webp_under_test",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()

VALID_TIMES = ["2026-10-14T16:00Z", "2026-10-14T17:00Z", "2026-10-14T18:00Z"]
FRAME_FILES = ["2026-10-14T1600.webp", "2026-10-14T1700.webp", "2026-10-14T1800.webp"]
STALE_FILE = "2020-01-01T0000.webp"

# 1.2.4.13: id della run = reference_time sanitizzato (re.sub("[^0-9A-Za-z]")).
RUN_IDS = ["20261014T120000Z", "20261013T120000Z", "20261012T120000Z",
           "20261011T120000Z"]
REF_TIMES = ["2026-10-14T12:00Z", "2026-10-13T12:00Z", "2026-10-12T12:00Z",
             "2026-10-11T12:00Z"]


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _var_manifest(region, domain, var, theme, run_id=None, reference_time=None,
                  expected_frames=None, valid_times=None):
    """Per-var WebP latest.json realistico (stessa forma del render path)."""
    bbox = {"lat_min": 36.5, "lon_min": 6.5, "lat_max": 47.2, "lon_max": 18.6}
    vts = list(valid_times) if valid_times is not None else list(VALID_TIMES)
    man = {
        "version": mod.WEBP_VERSION,
        "generated_at": "2026-10-14T20:00:00Z",
        "region": region,
        "domain": domain,
        "variable": var,
        "theme": theme,
        "unit": "degC",
        "bbox": bbox,
        "bounds": [[36.5, 6.5], [47.2, 18.6]],
        "grid": {"nx": 10, "ny": 12},
        "frames": [{"file": f, "valid_time": vt, "bytes": 5}
                   for f, vt in zip(FRAME_FILES[:len(vts)], vts)],
        "valid_times": vts,
    }
    if run_id is not None:
        # Il render path 1.2.4.13 scrive questi tre campi nel manifest.
        man["run_id"] = run_id
        man["reference_time"] = reference_time
        man["expected_frames"] = (expected_frames if expected_frames is not None
                                   else len(vts))
    return man


def _make_run_var_dir(out, region, domain, run_id, var, theme, malformed=False,
                      valid_times=None, expected_frames=None,
                      reference_time=None):
    """Crea <out>/<region>/<domain>/runs/<run_id>/<var>/ (layout 1.2.4.13)."""
    tdir = os.path.join(out, region, domain, "runs", run_id, var)
    os.makedirs(tdir, exist_ok=True)
    vts = list(valid_times) if valid_times is not None else list(VALID_TIMES)
    for f in FRAME_FILES[:len(vts)]:
        with open(os.path.join(tdir, f), "wb") as fh:
            fh.write(b"RIFF\x00\x00\x00\x00WEBPdummy")
    with open(os.path.join(tdir, STALE_FILE), "wb") as fh:
        fh.write(b"STALE-not-in-manifest")
    if malformed:
        with open(os.path.join(tdir, "latest.json"), "w", encoding="utf-8") as fh:
            fh.write("{ questo non e' JSON valido")
    else:
        _write_json(os.path.join(tdir, "latest.json"),
                    _var_manifest(region, domain, var, theme, run_id=run_id,
                                  reference_time=reference_time,
                                  expected_frames=expected_frames,
                                  valid_times=vts))
    return tdir


def _make_legacy_var_dir(out, region, domain, var, theme, malformed=False):
    """Crea <out>/<region>/<domain>/<var>[/dark] (layout 1.2.4.12, legacy)."""
    tdir = os.path.join(out, region, domain, var)
    if theme == "dark":
        tdir = os.path.join(tdir, "dark")
    os.makedirs(tdir, exist_ok=True)
    for f in FRAME_FILES:
        with open(os.path.join(tdir, f), "wb") as fh:
            fh.write(b"RIFF\x00\x00\x00\x00WEBPdummy")
    with open(os.path.join(tdir, STALE_FILE), "wb") as fh:
        fh.write(b"STALE-not-in-manifest")
    if malformed:
        with open(os.path.join(tdir, "latest.json"), "w", encoding="utf-8") as fh:
            fh.write("{ questo non e' JSON valido")
    else:
        _write_json(os.path.join(tdir, "latest.json"),
                    _var_manifest(region, domain, var, theme))
    return tdir


@pytest.fixture
def tree(tmp_path):
    """Albero sintetico MULTI-RUN: in/domA (engine), out con 5 run di domA.

    - 20261015T120000Z: l'unica var ha il manifest ROTTO -> la run intera e'
      scartata (non entra in "runs").
    - 20261014T120000Z: varX completa (3 frame) + varMalformed (rotta): la run
      resta, varMalformed e' assente dall'indice.
    - 20261013T120000Z: varX PARZIALE (2 frame su 3 attesi) ->
      completion_pct = round(100*2/3, 1) = 66.7.
    - 20261012T120000Z e 20261011T120000Z: varX complete. Con 4 run valide
      l'indice ne conserva 3 (la piu' vecchia e' oltre il limite).
    - europa/domA: una sola run, tema solo light.
    - domB: presente SOLO sotto out (non sotto in/) -> escluso dalla gate.
    """
    in_dir = str(tmp_path / "in")
    out_dir = str(tmp_path / "out")
    # Gate --in: solo domA ha <domain>/latest.json engine.
    os.makedirs(os.path.join(in_dir, "domA"))
    _write_json(os.path.join(in_dir, "domA", "latest.json"),
                {"domain": "domA", "reference_time": "2026-10-14T12:00Z",
                 "frames": [], "valid_times": []})
    # Run con l'unico manifest rotto -> run interamente scartata.
    _make_run_var_dir(out_dir, "italia", "domA", "20261015T120000Z",
                      "varBroken", "light", malformed=True,
                      reference_time="2026-10-15T12:00Z")
    # Run corrente: varX completa + varMalformed (manifest rotto).
    _make_run_var_dir(out_dir, "italia", "domA", "20261014T120000Z",
                      "varX", "light", reference_time=REF_TIMES[0])
    _make_run_var_dir(out_dir, "italia", "domA", "20261014T120000Z",
                      "varMalformed", "light", malformed=True,
                      reference_time=REF_TIMES[0])
    # Run parziale: 2 frame su 3 attesi.
    _make_run_var_dir(out_dir, "italia", "domA", "20261013T120000Z",
                      "varX", "light", valid_times=VALID_TIMES[:2],
                      expected_frames=len(VALID_TIMES),
                      reference_time=REF_TIMES[1])
    # Due run complete in piu': la piu' vecchia supererebbe il limite di 3.
    _make_run_var_dir(out_dir, "italia", "domA", "20261012T120000Z",
                      "varX", "light", reference_time=REF_TIMES[2])
    _make_run_var_dir(out_dir, "italia", "domA", "20261011T120000Z",
                      "varX", "light", reference_time=REF_TIMES[3])
    # europa/domA: SOLO light.
    _make_run_var_dir(out_dir, "europa", "domA", "20261014T120000Z",
                      "varY", "light", reference_time=REF_TIMES[0])
    # domB: presente SOLO sotto out (non sotto in/) -> escluso dalla gate.
    _make_run_var_dir(out_dir, "italia", "domB", "20261014T120000Z",
                      "varZ", "light", reference_time=REF_TIMES[0])
    return {"in": in_dir, "out": out_dir}


def _webp_names(dir_path):
    return sorted(f for f in os.listdir(dir_path) if f.lower().endswith(".webp"))


def test_index_only_writes_schema_prunes_and_copies_scales(tree):
    rc = mod.main(["--in", tree["in"], "--out", tree["out"],
                   "--index-only", "--domains", "domA,domB"])
    assert rc == 0

    # (1) indice italia: schema esatto, domA presente, domB assente.
    idx_path = os.path.join(tree["out"], "italia", "index.json")
    assert os.path.isfile(idx_path)
    with open(idx_path, encoding="utf-8") as fh:
        idx = json.load(fh)
    assert set(idx) == {"version", "generated_at", "region", "theme",
                        "themes", "bbox", "domains"}
    assert idx["version"] == mod.WEBP_VERSION
    assert idx["region"] == "italia"
    assert idx["theme"] == "light"
    assert idx["themes"] == ["light"]
    assert idx["bbox"] == {"lat_min": 36.5, "lon_min": 6.5,
                           "lat_max": 47.2, "lon_max": 18.6}
    assert "domA" in idx["domains"]
    assert "domB" not in idx["domains"]

    dom = idx["domains"]["domA"]
    # 1.2.4.13: lo schema del dominio porta ANCHE current_run e runs.
    assert set(dom) == {"bbox", "bounds", "grid", "variables",
                        "current_run", "runs"}
    assert dom["bounds"] == [[36.5, 6.5], [47.2, 18.6]]
    assert dom["grid"] == {"nx": 10, "ny": 12}

    # (2) SEMANTICA delle run: max 3, ordinate per reference_time decrescente,
    #     current_run = id della run piu' recente, id coerente col path
    #     runs/<id>/ dichiarato nell'indice.
    runs = dom["runs"]
    assert len(runs) == 3                        # 4 run valide -> max 3
    assert [r["id"] for r in runs] == ["20261014T120000Z",
                                       "20261013T120000Z",
                                       "20261012T120000Z"]
    assert [r["reference_time"] for r in runs] == [REF_TIMES[0], REF_TIMES[1],
                                                   REF_TIMES[2]]
    assert dom["current_run"] == runs[0]["id"]
    assert dom["current_run"] in [r["id"] for r in runs]
    # La run con l'unico manifest rotto NON e' indicizzata.
    assert "20261015T120000Z" not in [r["id"] for r in runs]
    # La 4a run (la piu' vecchia) e' oltre il limite di 3 conservate.
    assert "20261011T120000Z" not in [r["id"] for r in runs]
    for run in runs:
        # L'id della run corrisponde alla directory reale su disco...
        assert os.path.isdir(os.path.join(tree["out"], "italia", "domA",
                                          "runs", run["id"]))
        # ... e ai path dichiarati nell'indice.
        for var, entry in run["variables"].items():
            assert entry["dir"] == "domA/runs/%s/%s" % (run["id"], var)
            assert entry["themes"]["light"]["dir"] == \
                "domA/runs/%s/%s" % (run["id"], var)
            assert entry["themes"]["light"]["manifest"] == \
                "domA/runs/%s/%s/latest.json" % (run["id"], var)
        # expected/available/completion coerenti fra loro (formula del renderer).
        expected = run["expected_frames"]
        available = run["frames_available"]
        assert available == len(run["variables"]["varX"]["themes"]["light"]
                                ["valid_times"])
        assert run["completion_pct"] == round(
            100.0 * min(available, expected) / expected, 1)
    # Valori ATTESI per run (la run parziale ha 2 frame su 3 attesi).
    assert (runs[0]["expected_frames"], runs[0]["frames_available"],
            runs[0]["completion_pct"]) == (3, 3, 100.0)
    assert (runs[1]["expected_frames"], runs[1]["frames_available"],
            runs[1]["completion_pct"]) == (3, 2, 66.7)
    assert (runs[2]["expected_frames"], runs[2]["frames_available"],
            runs[2]["completion_pct"]) == (3, 3, 100.0)

    # (3) le variabili dell'indice sono quelle della run corrente.
    assert dom["variables"] == dom["runs"][0]["variables"]
    varx = dom["variables"]["varX"]
    assert varx["dir"] == "domA/runs/20261014T120000Z/varX"
    assert varx["unit"] == "degC"
    # 1.2.4.13: il tema scuro e' stato RIMOSSO -> una sola chiave.
    assert set(varx["themes"]) == {"light"}
    light = varx["themes"]["light"]
    assert light == {"count": 3, "valid_times": VALID_TIMES,
                     "dir": "domA/runs/20261014T120000Z/varX",
                     "manifest": "domA/runs/20261014T120000Z/varX/latest.json"}
    # (4) varMalformed: saltata interamente, ASSENTE dall'indice.
    assert "varMalformed" not in dom["variables"]
    assert "varBroken" not in dom["variables"]

    # (5) prune: stale rimosso, i frame listati restano (keep-set proprio).
    tdir = os.path.join(tree["out"], "italia", "domA", "runs",
                        "20261014T120000Z", "varX")
    assert _webp_names(tdir) == sorted(FRAME_FILES)
    assert STALE_FILE not in os.listdir(tdir)
    # Anche nella run parziale lo stale e' potato (keep-set = 2 frame).
    pdir = os.path.join(tree["out"], "italia", "domA", "runs",
                        "20261013T120000Z", "varX")
    assert _webp_names(pdir) == sorted(FRAME_FILES[:2])
    assert STALE_FILE not in os.listdir(pdir)
    # (6) file della var malformed NON potati.
    mdir = os.path.join(tree["out"], "italia", "domA", "runs",
                        "20261014T120000Z", "varMalformed")
    assert os.path.isfile(os.path.join(mdir, STALE_FILE))
    assert os.path.isfile(os.path.join(mdir, FRAME_FILES[0]))

    # (7) europa scritto; solo light -> themes ["light"].
    with open(os.path.join(tree["out"], "europa", "index.json"),
              encoding="utf-8") as fh:
        eidx = json.load(fh)
    assert eidx["themes"] == ["light"]
    assert eidx["theme"] == "light"
    assert "domA" in eidx["domains"]
    assert "domB" not in eidx["domains"]
    assert set(eidx["domains"]["domA"]["variables"]) == {"varY"}
    assert eidx["domains"]["domA"]["current_run"] == "20261014T120000Z"
    assert len(eidx["domains"]["domA"]["runs"]) == 1

    # (8) color_scales.json copiato alla radice di out.
    assert os.path.isfile(os.path.join(tree["out"], "color_scales.json"))
    # (9) exit code 0 gia' assertato in test.


def test_index_only_reads_the_legacy_layout_as_a_migration_run(tmp_path):
    """Il layout 1.2.4.12 (<domain>/<var>) resta leggibile come una run.

    La run legacy e' identificata dal prefisso "legacy-" + run_id(reference_time
    del manifest o, se assente, generated_at). Il path dichiarato resta quello
    legacy (nessun livello "runs/"). In --index-only il renderer NON pota il
    sottoalbero legacy (la sua rimozione avviene a livello git, vedi
    scripts/forecast_v2/prune_fm2_run_index.py): il test lo documenta.
    """
    in_dir = str(tmp_path / "in")
    out_dir = str(tmp_path / "out")
    os.makedirs(os.path.join(in_dir, "domA"))
    _write_json(os.path.join(in_dir, "domA", "latest.json"),
                {"domain": "domA", "reference_time": "2026-10-14T12:00Z",
                 "frames": [], "valid_times": []})
    legacy = _make_legacy_var_dir(out_dir, "italia", "domA", "varX", "light")
    dark = _make_legacy_var_dir(out_dir, "italia", "domA", "varX", "dark")

    rc = mod.main(["--in", in_dir, "--out", out_dir, "--index-only",
                   "--regions", "italia", "--domains", "domA"])
    assert rc == 0
    with open(os.path.join(out_dir, "italia", "index.json"),
              encoding="utf-8") as fh:
        idx = json.load(fh)
    dom = idx["domains"]["domA"]
    assert set(dom) == {"bbox", "bounds", "grid", "variables",
                        "current_run", "runs"}
    runs = dom["runs"]
    assert len(runs) == 1
    run = runs[0]
    # id = "legacy-" + _run_id(generated_at "2026-10-14T20:00:00Z").
    assert run["id"] == "legacy-20261014T200000Z"
    assert run["reference_time"] == "2026-10-14T20:00:00Z"
    assert dom["current_run"] == run["id"]
    # Il path dichiarato resta quello legacy (nessun livello "runs/").
    assert run["variables"]["varX"]["dir"] == "domA/varX"
    assert run["variables"]["varX"]["themes"]["light"]["manifest"] == \
        "domA/varX/latest.json"
    assert run["expected_frames"] == 3
    assert run["frames_available"] == 3
    assert run["completion_pct"] == 100.0
    # Il tema scuro non e' indicizzato (rimosso nel 1.2.4.13).
    assert set(run["variables"]["varX"]["themes"]) == {"light"}
    # --index-only NON pota il layout legacy (lo fa prune_fm2_run_index.py).
    assert os.path.isfile(os.path.join(legacy, STALE_FILE))
    assert os.path.isdir(dark)


def test_index_only_empty_domain_list_exits_2_nothing_written(tree):
    # --domains mancante/vuoto: errore CLI (exit 2), nessuna scrittura.
    rc = mod.main(["--in", tree["in"], "--out", tree["out"], "--index-only"])
    assert rc == 2
    assert not os.path.exists(os.path.join(tree["out"], "italia", "index.json"))
    assert not os.path.exists(os.path.join(tree["out"], "europa", "index.json"))
    assert not os.path.isfile(os.path.join(tree["out"], "color_scales.json"))


def test_index_only_no_valid_domain_exits_1_nothing_written(tree):
    # Lista non vuota ma nessun dominio valido (domB non esiste in --in):
    # exit 1, nessun indice, nessuna pota, nessun color_scales.
    rc = mod.main(["--in", tree["in"], "--out", tree["out"],
                   "--index-only", "--domains", "domB"])
    assert rc == 1
    assert not os.path.exists(os.path.join(tree["out"], "italia", "index.json"))
    assert not os.path.isfile(os.path.join(tree["out"], "color_scales.json"))
    zdir = os.path.join(tree["out"], "italia", "domB", "runs",
                        "20261014T120000Z", "varZ")
    assert os.path.isfile(os.path.join(zdir, STALE_FILE))  # non potato


def test_index_only_requires_in_dir_with_manifest(tmp_path):
    # --in senza alcun <dominio>/latest.json: gate exit 2 (stesso stile del render).
    os.makedirs(str(tmp_path / "in"))
    rc = mod.main(["--in", str(tmp_path / "in"), "--out", str(tmp_path / "out"),
                   "--index-only", "--domains", "domA"])
    assert rc == 2


def test_module_imports_with_lazy_heavy_deps():
    # Il modulo va importato anche senza numpy/PIL/omfiles: il meccanismo lazy e'
    # il dict _IMPORT_ERRORS (catturato, non alzato) + _require_render_deps().
    assert isinstance(mod._IMPORT_ERRORS, dict)
    assert callable(mod._require_render_deps)
    # Con deps presenti l'import non ha FATALato (altrimenti mod non esisterebbe).


def test_require_render_deps_fatals_when_missing(monkeypatch, capsys):
    # Simula l'assenza di numpy SENZA disinstallare: FATAL su stderr + re-raise.
    err = ImportError("numpy assente (simulato)")
    monkeypatch.setattr(mod, "_IMPORT_ERRORS", {"numpy": err})
    with pytest.raises(ImportError):
        mod._require_render_deps()
    assert "FATAL" in capsys.readouterr().err
    # Deps effettivamente presenti in questo ambiente: nessun FATAL.
    monkeypatch.setattr(mod, "_IMPORT_ERRORS", {})
    mod._require_render_deps()  # non deve alzare


def test_parse_args_method_default_and_override():
    assert mod.parse_args([]).method == 6
    assert mod.parse_args(["--method", "4"]).method == 4
    assert mod.parse_args([]).index_only is False
    assert mod.parse_args(["--index-only"]).index_only is True


def test_default_theme_is_light_only():
    # 1.2.4.13: il tema scuro e' stato rimosso, il default e' "light".
    assert mod.parse_args([]).themes == "light"
    assert mod.parse_args(["--themes", "light"]).themes == "light"


def test_dark_theme_is_rejected_with_exit_2(tree, capsys):
    # --themes dark: FATAL esplicito + exit 2, NESSUNA scrittura.
    rc = mod.main(["--in", tree["in"], "--out", tree["out"],
                   "--index-only", "--domains", "domA", "--themes", "dark"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "dark theme removed in 1.2.4.13" in err
    assert not os.path.exists(os.path.join(tree["out"], "italia", "index.json"))


def test_save_webp_method_parameter():
    # Se Pillow/numpy non sono installati questo e' l'UNICO test saltato.
    try:
        import numpy as np
        from PIL import Image  # noqa: F401
    except ImportError:
        pytest.skip("Pillow/numpy non disponibili: test save_webp saltato")
    import tempfile
    arr = np.zeros((4, 4, 4), dtype=np.uint8)
    arr[..., 0] = 200
    arr[..., 3] = 255
    with tempfile.TemporaryDirectory() as tmp:
        for method in (0, 6):
            path = os.path.join(tmp, "m%d.webp" % method)
            size = mod.save_webp(arr, path, quality=90, lossless=False,
                                 method=method)
            assert size > 0
            with open(path, "rb") as fh:
                head = fh.read(12)
            assert head[:4] == b"RIFF" and head[8:12] == b"WEBP"
