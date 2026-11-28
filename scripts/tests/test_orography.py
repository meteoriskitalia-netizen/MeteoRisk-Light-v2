# -*- coding: utf-8 -*-
"""Griglia di orografia semplificata — variante DEM pubblicata.

In `main` e' pubblicata SOLO `data/geography/orography_it_0p05.json` (path
storico del prodotto; dal BUILD=228 la griglia effettiva e' 0,02 gradi).
La variante a 0,1 gradi interpolata dalle 257 quote di punto e' stata
SCARTATA (errore del 98% su `w . grad(h)`): il generatore la sa ancora
produrre (`--mode points`) e questi test la usano in directory temporanea
per verificare il determinismo, ma il file non va in `main`.

Cosa verifica:
  - schema, griglia e numero di celle coerenti con le quote;
  - codifica compatta (uint8 + LZMA2 + base64) e sua decodifica;
  - valori nei limiti fisici, nessun nodata, mare a 0 m;
  - plausibilita' sulle vette (Etna, Monte Bianco, Monte Rosa, Gran Sasso);
  - copertura dell'Italia: TUTTI i vertici dei confini provinciali su celle
    con dato (il DEM non ha buchi);
  - dimensione del file <= 150 KB gzip;
  - licenza SRTM nel JSON E nella scheda Fonti (`docs/LICENSES.md`);
  - riproducibilita' della griglia decodificata (il codec LZMA puo' cambiare
    byte fra versioni di liblzma: si confronta il contenuto, non il container);
  - il generatore `--mode points` resta deterministico e coerente;
  - la variante A non e' pubblicata e il motivo resta scritto in docs.

I file sono generati da scripts/geography/build_orography.py.
"""
import base64
import gzip
import hashlib
import json
import lzma
import os
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GEO = os.path.join(ROOT, "data", "geography")
SCRIPT = os.path.join(ROOT, "scripts", "geography", "build_orography.py")
POINTS = os.path.join(ROOT, "data", "latest", "meteorisk-points.json")
DOC = os.path.join(ROOT, "docs", "OROGRAFIA.md")
LICENSES = os.path.join(ROOT, "docs", "LICENSES.md")

B_PATH = os.path.join(GEO, "orography_it_0p05.json")
A_PATH = os.path.join(GEO, "orography_it_0p1.json")
TILE_CACHE = os.environ.get("METEORISK_ORO_TILE_CACHE")
NODATA = -32768


def _load(path):
    if not os.path.exists(path):
        pytest.skip("file assente: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _decode(doc):
    """Decodifica `elevations_m` (stringa compatta o vettore int16 legacy)."""
    g = doc["grid"]
    nx, ny = int(g["nx"]), int(g["ny"])
    vals = doc["elevations_m"]
    if isinstance(vals, str):
        enc = doc["encoding"]
        raw = lzma.decompress(base64.b64decode(vals.encode("ascii")))
        q = np.frombuffer(raw, dtype=np.uint8)
        assert q.size == nx * ny
        return q.reshape(ny, nx).astype(np.float64) * float(enc["scale_m"])
    return np.asarray(vals, dtype=np.float64).reshape(ny, nx)


def _cells(doc):
    g = doc["grid"]
    vals = _decode(doc)
    assert vals.size == g["nx"] * g["ny"]
    return g, vals


def _cell(doc, lat, lon):
    g = doc["grid"]
    arr = _decode(doc)
    ix = min(max(int((lon - g["lon0"]) / g["dlon"]), 0), g["nx"] - 1)
    iy = min(max(int((lat - g["lat0"]) / g["dlat"]), 0), g["ny"] - 1)
    return float(arr[iy, ix])


def _run(mode, res, out, extra=()):
    cmd = [sys.executable, SCRIPT, "--mode", mode, "--res", str(res), "--out", out]
    cmd += list(extra)
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


# --------------------------------------------------------------- struttura
def test_b_schema_e_griglia():
    doc = _load(B_PATH)
    assert doc["schema"] == "meteorisk.orography/1"
    assert doc["units"] == "m"
    assert doc["dtype"] == "uint8"
    assert doc["generated_at"] is None, "timestamp nel file: romperebbe la riproducibilita'"
    assert doc["source"] and doc["licence"] and doc["attribution"] and doc["note"]
    assert len(doc["uses"]) == 3
    enc = doc["encoding"]
    assert enc["codec"] == "lzma2_delta1"
    assert enc["payload"] == "base64"
    assert enc["scale_m"] == pytest.approx(20.0)
    assert enc["uncompressed_bytes"] == 800 * 650
    g = doc["grid"]
    assert g["registration"] == "cell_centered"
    assert abs(g["dlon"] - 0.02) < 1e-12 and abs(g["dlat"] - 0.02) < 1e-12
    assert (g["lon0"], g["lat0"], g["lon1"], g["lat1"]) == (4.0, 35.0, 20.0, 48.0)
    assert g["nx"] * g["dlon"] == pytest.approx(16.0)
    assert g["ny"] * g["dlat"] == pytest.approx(13.0)
    assert (g["nx"], g["ny"]) == (800, 650)
    assert "row-major" in g["ordering"]
    assert isinstance(doc["elevations_m"], str)
    _, vals = _cells(doc)
    assert vals.dtype == np.float64


def test_b_valori_fisici_e_nessun_nodata():
    doc = _load(B_PATH)
    _, vals = _cells(doc)
    assert np.all(np.isfinite(vals)), "il DEM non deve avere nodata"
    assert doc["stats"]["cells_filled_from_neighbours"] == 0
    assert float(np.min(vals)) == 0.0, "il mare deve essere 0 m, non una batimetria"
    assert float(np.max(vals)) <= 5000.0, "quota sopra 5000 m: non fisica per l'Italia"
    assert float(np.max(vals)) > 4200.0, "manca il Monte Bianco: DEM declassato o corrotto"
    assert doc["stats"]["max_elevation_m"] == pytest.approx(float(np.max(vals)))
    assert doc["encoding"]["uncompressed_sha256"] == hashlib.sha256(
        np.rint(vals / doc["encoding"]["scale_m"]).astype(np.uint8).tobytes()
    ).hexdigest()


def test_b_plausibilita_italia():
    doc = _load(B_PATH)
    s = doc["validation_samples"]
    assert s["alpi_valtellina"]["elevation_m"] > 1500
    assert s["monte_bianco_vetta"]["elevation_m"] > 4000
    assert s["etna_vetta"]["elevation_m"] >= 2900
    assert s["gran_sasso_vetta"]["elevation_m"] > 2200
    assert s["vesuvio_vetta"]["elevation_m"] > 800
    assert s["campeda"]["elevation_m"] > 550
    assert s["pianura_padana"]["elevation_m"] < 100
    assert s["sicilia_interna_enna"]["elevation_m"] > 400
    assert s["adriatico_medio"]["elevation_m"] == 0
    # massimo del file: Monte Bianco (cella a 0,02 deg), non un picco isolato
    mx = float(np.max(_decode(doc)))
    assert 4200 < mx < 4600
    assert _cell(doc, 45.8326, 6.8652) == mx


def test_b_dimensione_gzip_sotto_150_kb():
    """Il vincolo e' il transfer gzip, non il file su disco."""
    with open(B_PATH, "rb") as fh:
        data = fh.read()
    gz = len(gzip.compress(data, 9, mtime=0))
    assert gz <= 150 * 1024, "gzip %d B > 150 KB" % gz
    assert len(data) < 250 * 1024


def test_b_loader_consumer():
    """Il consumer reale (radar_engine) deve leggere la codifica compatta."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from radar_engine import orography as orog  # noqa: E402
    g = orog.load_grid(B_PATH)
    assert g is not None
    assert (g["width"], g["height"]) == (800, 650)
    assert g["step_x"] == pytest.approx(0.02) and g["step_y"] == pytest.approx(0.02)
    assert np.isfinite(g["elev"]).all()
    v = orog.sample_bilinear(g, np.array([14.9934, 6.8652]),
                             np.array([37.7510, 45.8326]))
    assert v[0] >= 2900.0 and v[1] >= 3150.0


def test_b_copertura_confini_provinciali():
    doc = _load(B_PATH)
    g = doc["grid"]
    geo_path = os.path.join(GEO, "province_italiane.geojson")
    if not os.path.exists(geo_path):
        pytest.skip("province_italiane.geojson assente")
    with open(geo_path, "r", encoding="utf-8") as fh:
        geo = json.load(fh)
    arr = _decode(doc)
    total = 0
    missing = {}
    for feat in geo["features"]:
        name = feat["properties"].get("SIGLA") or "?"
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for ring in poly:
                for lon, lat in ring:
                    ix = int((lon - g["lon0"]) / g["dlon"])
                    iy = int((lat - g["lat0"]) / g["dlat"])
                    if 0 <= ix < g["nx"] and 0 <= iy < g["ny"]:
                        total += 1
                        if not np.isfinite(arr[iy, ix]):
                            missing[name] = missing.get(name, 0) + 1
    assert total > 20000
    assert not missing, "buchi di copertura su vertici provinciali: %s" % missing


# ------------------------------------------------------------------ licenza
def test_b_licenza_srtm_nel_file():
    doc = _load(B_PATH)
    blob = " ".join([doc["licence"], doc["attribution"], doc["source"], doc["source_detail"]])
    assert "SRTM" in blob
    assert "U.S. Geological Survey" in blob
    assert "pubblico dominio" in blob
    assert "tilezen/joerd" in blob, "manca il rimando alle attribuzioni del pacchetto tile"


def test_licenza_srtm_in_LICENSES():
    if not os.path.exists(LICENSES):
        pytest.skip("docs/LICENSES.md assente")
    with open(LICENSES, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "orography_it_0p05.json" in text
    assert "SRTM" in text
    assert "U.S. Geological Survey" in text
    assert "ETOPO1" in text


# ------------------------------------------------------------ riproducibilita'
def test_b_riproducibile_dem():
    """Due run sugli stessi tile devono dare la stessa griglia decodificata.

    Il container LZMA puo' cambiare byte fra versioni di liblzma: si confronta
    il contenuto (array uint8 + sha non compresso), non il container. Il
    confronto con il file committato e' condizionato all'hash dei tile.
    Senza cache il test e' SALTATO, non finto verde.
    """
    if not os.path.exists(B_PATH):
        pytest.skip("orography_it_0p05.json assente")
    if not TILE_CACHE or not os.path.isdir(TILE_CACHE):
        pytest.skip("cache tile DEM non disponibile (METEORISK_ORO_TILE_CACHE)")
    tmp = tempfile.mkdtemp(prefix="oroB_")
    o1 = _run("dem", 0.02, os.path.join(tmp, "b1.json"), ["--tile-cache", TILE_CACHE])
    o2 = _run("dem", 0.02, os.path.join(tmp, "b2.json"), ["--tile-cache", TILE_CACHE])
    p1 = _load(o1)
    p2 = _load(o2)
    assert p1["encoding"]["uncompressed_sha256"] == p2["encoding"]["uncompressed_sha256"]
    np.testing.assert_array_equal(_decode(p1), _decode(p2))
    committed = _load(B_PATH)
    if (committed["inputs"]["tiles_sha256_concat"]
            == p1["inputs"]["tiles_sha256_concat"]):
        assert (committed["encoding"]["uncompressed_sha256"]
                == p1["encoding"]["uncompressed_sha256"]), \
            "stessi tile ma griglia decodificata diversa dal committato"
        np.testing.assert_array_equal(_decode(committed), _decode(p1))


@pytest.fixture(scope="module")
def temp_a():
    """Una generazione della variante A (0,1 gradi) in directory temporanea."""
    if not os.path.exists(POINTS):
        pytest.skip("meteorisk-points.json assente")
    tmp = tempfile.mkdtemp(prefix="oroA_")
    out = _run("points", 0.1, os.path.join(tmp, "a1.json"), ["--points", POINTS])
    return out


def test_generatore_points_deterministico(temp_a):
    tmp = os.path.dirname(temp_a)
    out2 = _run("points", 0.1, os.path.join(tmp, "a2.json"), ["--points", POINTS])
    assert _sha256(temp_a) == _sha256(out2), "due run sugli stessi input non sono identiche"


def test_generatore_points_schema_e_supporto(temp_a):
    """La modalita' A resta corretta come generatore, anche se non pubblicata."""
    doc = _load(temp_a)
    g, vals = _cells(doc)
    assert (g["nx"], g["ny"]) == (160, 130)
    n_nodata = int(np.sum(vals == NODATA))
    assert n_nodata == doc["stats"]["cells_nodata"]
    assert n_nodata > 0, "A deve dichiarare il supporto limitato delle 257 stazioni"
    assert 0 < np.max(vals) < 3000, "A non puo' raggiungere le cime reali"
    assert doc["stats"]["loo_error_m"]["mae"] > 100, "il LOO di A e' grande per costruzione"
    assert doc["inputs"]["points_n"] == 257


# ------------------------------------------------------------ decisione A/B
def test_variante_A_non_pubblicata():
    """La variante A e' scartata: non deve stare in data/geography e il motivo in docs."""
    assert not os.path.exists(A_PATH), (
        "orography_it_0p1.json non va pubblicata: errore del 98%% su w.grad(h)")
    with open(DOC, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "scartata" in text.lower()
    assert "98%" in text
    assert "orography_it_0p05.json" in text
