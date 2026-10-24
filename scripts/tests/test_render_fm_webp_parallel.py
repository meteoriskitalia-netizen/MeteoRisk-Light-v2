# -*- coding: utf-8 -*-
"""Parallelismo del renderer (``--workers``): output BYTE-IDENTICO al seriale.

Costruisce un dataset ``.om`` minimale ma REALE (``OmFileWriter``: 2 frame,
temperatura con isolinee, vento con frecce, nubi senza overlay, un buco NaN)
e lancia la CLI due volte, ``--workers 1`` e ``--workers 4``, confrontando lo
sha256 di OGNI WebP prodotto.

Perche' serve: il renderer e' passato da un ciclo seriale a un
``ProcessPoolExecutor``. Il requisito e' che la parallelizzazione non cambi un
byte: un test che confrontasse solo il numero di file non vedrebbe una
differenza di encoding (Pillow/libwebp) ne' di compositing dell'overlay.

Skip DICHIARATO: se ``omfiles``/``Pillow``/``numpy`` mancano, o se l'API di
``OmFileWriter`` non e' quella attesa, il test salta con un motivo esplicito
(non finge di coprire). Con i dati reali il confronto equivalente e' stato
fatto a mano sul frame ``dwd_icon 2026-10-23T1800.om`` (66 WebP, 0 differenze).
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                             "render_fm_webp.py")

DOMAIN = "test_domain"
GRID = {"nx": 24, "ny": 18, "lonMin": 6.0, "latMin": 36.0,
        "dx": 0.5, "dy": 0.5}
BBOX = {"lat_min": 36.0, "lon_min": 6.0, "lat_max": 44.5, "lon_max": 17.5}
VALID_TIMES = ["2026-10-14T16:00Z", "2026-10-14T17:00Z"]
VAR_ORDER = ["temperature_2m", "wind_speed_10m", "wind_direction_10m",
             "cloud_cover"]
EXPECTED_WEBP = 12          # 3 var con scala x 2 regioni x 2 frame


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_fm_webp_parallel_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fields(ny, nx, k):
    """Campi deterministici: nessun RNG, cosi' i due run partono uguali."""
    import numpy as np
    y, x = np.mgrid[0:ny, 0:nx]
    temp = 8.0 + 6.0 * np.sin((x + k * 3) / 5.0) + 4.0 * np.cos(y / 4.0)
    temp = np.round(temp, 2).astype(np.float32)
    temp[3:6, 5:9] = np.nan                 # buco -> pixel trasparenti
    speed = 2.0 + 12.0 * np.abs(np.sin((x + y) / 7.0 + k))
    direction = (x * 13.0 + y * 7.0 + k * 20.0) % 360.0
    cloud = np.clip(50.0 + 40.0 * np.sin(x / 3.0 + y / 5.0 + k), 0.0, 100.0)
    return {
        "temperature_2m": temp,
        "wind_speed_10m": np.round(speed, 2).astype(np.float32),
        "wind_direction_10m": np.round(direction, 2).astype(np.float32),
        "cloud_cover": np.round(cloud, 2).astype(np.float32),
    }


def _write_frame(path, arrays):
    from omfiles import OmFileWriter
    tmp = str(path) + ".tmp"
    w = OmFileWriter.at_path(tmp)
    children = []
    for name, arr in arrays.items():
        children.append(w.write_array(
            arr, chunks=[min(16, arr.shape[0]), min(16, arr.shape[1])],
            scale_factor=100.0, add_offset=0.0,
            compression="pfor_delta_2d_int16", name=name))
    children.append(w.write_scalar("{}", name="crs_wkt"))
    children.append(w.write_scalar(0, name="valid_time"))
    root = w.write_group(name="", children=children)
    w.close(root)
    os.replace(tmp, str(path))


def _build_dataset(in_dir):
    dom_dir = os.path.join(in_dir, DOMAIN)
    os.makedirs(dom_dir, exist_ok=True)
    frames = []
    for k, valid_time in enumerate(VALID_TIMES):
        name = valid_time.replace(":", "").replace("Z", "") + ".om"
        _write_frame(os.path.join(dom_dir, name), _fields(GRID["ny"],
                                                         GRID["nx"], k))
        frames.append({"file": name, "valid_time": valid_time,
                       "bytes": os.path.getsize(os.path.join(dom_dir, name))})
    manifest = {
        "reference_time": "2026-10-14T12:00:00Z",
        "grid": dict(GRID),
        "bbox": dict(BBOX),
        "frames": frames,
        "valid_times": list(VALID_TIMES),
        "var_order": list(VAR_ORDER),
        "complete": True,
    }
    with open(os.path.join(dom_dir, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)


def _run(workers, in_dir, out_dir):
    cmd = [sys.executable, RENDERER_PATH,
           "--in", in_dir, "--out", out_dir,
           "--domains", DOMAIN, "--regions", "italia,europa",
           "--workers", str(workers), "--method", "4"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=REPO_ROOT)
    assert proc.returncode == 0, "exit=%d\nSTDOUT:\n%s\nSTDERR:\n%s" % (
        proc.returncode, proc.stdout, proc.stderr)
    return proc


def _webp_hashes(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".webp"):
                continue
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root)] = \
                    hashlib.sha256(fh.read()).hexdigest()
    return out


def test_workers_argument_defaults_and_bounds():
    mod = _load_renderer()
    assert 1 <= mod.default_workers() <= 4
    assert mod.parse_args([]).workers == 0          # 0 = auto in main()
    assert mod.parse_args(["--workers", "3"]).workers == 3


def test_workers_1_and_4_produce_identical_webp(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    pytest.importorskip("omfiles")

    in_dir = str(tmp_path / "in")
    out1 = str(tmp_path / "out_w1")
    out4 = str(tmp_path / "out_w4")
    try:
        _build_dataset(in_dir)
    except Exception as exc:  # API di OmFileWriter diversa: skip DICHIARATO
        pytest.skip("OmFileWriter non utilizzabile in questo ambiente: %s" % exc)

    p1 = _run(1, in_dir, out1)
    p4 = _run(4, in_dir, out4)
    assert "[workers] 1 (seriale)" in p1.stdout, p1.stdout
    assert "[workers] 4 processi" in p4.stdout, p4.stdout

    h1 = _webp_hashes(out1)
    h4 = _webp_hashes(out4)
    assert len(h1) == EXPECTED_WEBP, sorted(h1)
    assert sorted(h1) == sorted(h4), "set di file diverso fra seriale e parallelo"
    differing = sorted(k for k in h1 if h1[k] != h4[k])
    assert not differing, "WebP diversi (sha256) fra --workers 1 e 4: %s" % \
        differing[:5]
