# -*- coding: utf-8 -*-
"""Test del layer "criticita' idrogeologica/idraulica ufficiale" (DPC).

Il layer NON e' una soglia MeteoRisk: i test verificano che il fetcher copi i
livelli ufficiali, non li interpreti e non li sommi. Fixture = bollettino
REALE del 2026-11-13 ridotto a 7 zone (vedi fixtures/README.md).
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FIX = os.path.join(HERE, "fixtures")
SCRIPT = os.path.join(ROOT, "scripts", "hydro", "fetch_criticality.py")

import fetch_criticality as fc  # noqa: E402  (conftest mette scripts/hydro su sys.path)

NOW = "2026-11-13T17:00:00Z"
CODES = {"none", "yellow", "orange", "red"}


def run(tmp_path, *args, fixture=FIX, expect=0, out_name="criticality.json"):
    out = tmp_path / out_name
    cmd = [sys.executable, SCRIPT, "--out", str(out)]
    if fixture:
        cmd += ["--fixture-dir", str(fixture)]
    cmd += list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == expect, "rc=%s stderr=%s stdout=%s" % (proc.returncode, proc.stderr, proc.stdout)
    return proc, out


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# 1 ---------------------------------------------------------------------------
def test_normalizzazione_stringhe_ufficiali():
    """Solo le 4 classi ufficiali; nessuna stringa interpretata a naso."""
    assert fc.LEVEL_MAP[fc.NONE_RAW] == "none"
    for risk in ("idraulico", "temporali", "idrogeologico"):
        assert fc.LEVEL_MAP["Ordinaria per rischio %s / ALLERTA GIALLA" % risk] == "yellow"
        assert fc.LEVEL_MAP["Moderata per rischio %s / ALLERTA ARANCIONE" % risk] == "orange"
        assert fc.LEVEL_MAP["Elevata per rischio %s / ALLERTA ROSSA" % risk] == "red"
    # forma breve usata nei campi per-rischio del TopoJSON reale
    assert fc.LEVEL_MAP["Ordinaria / ALLERTA GIALLA"] == "yellow"
    assert fc.LEVEL_MAP["Moderata / ALLERTA ARANCIONE"] == "orange"
    assert fc.LEVEL_MAP["Elevata / ALLERTA ROSSA"] == "red"
    # nessuna mappatura inventata: solo la stringa "assenza" produce 'none'
    assert [k for k, v in fc.LEVEL_MAP.items() if v == "none"] == [fc.NONE_RAW]
    assert set(fc.LEVEL_MAP.values()) <= CODES
    # una stringa non prevista NON e' nel dizionario (verra' marcata 'unknown')
    assert "Allerta VIOLA" not in fc.LEVEL_MAP


# 2 ---------------------------------------------------------------------------
def test_scelta_indice_e_slot_topojson():
    """Indice di oggi -> _today; di ieri -> _tomorrow (copre oggi); piu' vecchio -> stale."""
    today = "20261113"
    names = ["20261111_1540.json", "20261112_1500.json", "20261113_1434.json"]
    assert fc.pick_pair(names, today) == ("20261113_1434.json", "today")
    assert fc.pick_pair(names[:2], today) == ("20261112_1500.json", "tomorrow")
    assert fc.pick_pair(names[:1], today) == ("20261111_1540.json", "today")


def test_bollettino_di_ieri_usa_il_tomorrow(tmp_path):
    """Prima della pubblicazione odierna si usa il _tomorrow di ieri: non e' stale."""
    fixture = tmp_path / "fix"
    shutil.copytree(FIX, fixture)
    idx = load(fixture / "index.json")
    idx["date"] = "2026-11-12T15:00:00.000Z"
    idx["name"] = "Bollettino del 12 novembre 2026 ore 15:00"
    with open(fixture / "index.json", "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False)
    _, out = run(tmp_path, "--now", NOW, fixture=str(fixture))
    doc = load(out)
    assert doc["stale"] is False
    assert doc["source"]["topojson_kind"] == "tomorrow"
    assert doc["valid_date"] == "2026-11-13"
    assert len(doc["zones"]) == 7


# 3 ---------------------------------------------------------------------------
def test_bollettino_reale_ridotto(tmp_path):
    """Parsing di un bollettino reale ridotto: zone, 3 livelli, metadati."""
    proc, out = run(tmp_path, "--now", NOW)
    doc = load(out)
    assert doc["schema"] == "meteorisk.hydro.criticality/1"
    assert doc["licence"] == "CC BY 4.0"
    assert doc["attribution"] == "Dipartimento della Protezione Civile"
    assert doc["stale"] is False
    assert doc["valid_date"] == "2026-11-13"
    assert doc["source"]["index_label"] == "Bollettino del 13 novembre 2026 ore 14:34"
    assert "Dipartimento della Protezione Civile" in doc["disclaimer"]

    zones = {z["name"]: z for z in doc["zones"]}
    assert len(zones) == 7
    for z in zones.values():
        assert set(z["levels"]) == {"hydraulic", "thunderstorm", "hydrogeological"}
        assert set(z["levels"].values()) <= CODES
        assert isinstance(z["comuni_count"], int) and z["comuni_count"] > 0
        assert isinstance(z["polygons"], list) and z["polygons"]
        assert "comuni" not in z  # solo il conteggio, mai la lista
    # i tre rischi restano separati: la zona ha tre valori distinti possibili
    serchio = zones["Serchio-Garfagnana-Lima"]
    assert serchio["levels"]["hydraulic"] == "orange"
    assert serchio["levels"]["hydrogeological"] == "orange"
    assert zones["Marsica"]["levels"]["thunderstorm"] == "yellow"
    assert zones["Bacini Tordino Vomano"]["levels"]["hydraulic"] == "none"
    # il campo ufficiale "Rappresentata nella mappa" e' copiato, non calcolato
    assert serchio["dpc_map_class"] in CODES
    assert "Comuni" not in json.dumps(doc, ensure_ascii=False)


# 3 ---------------------------------------------------------------------------
def test_geometria_semplificata(tmp_path):
    """Douglas-Peucker dichiarato, anelli chiusi, coordinate dentro l'Italia."""
    _, out = run(tmp_path, "--now", NOW)
    doc = load(out)
    g = doc["geometry"]
    assert g["simplify"]["method"].startswith("douglas-peucker")
    assert g["simplify"]["tolerance_deg"] > 0
    assert g["simplify"]["vertices_after"] < g["simplify"]["vertices_before"]
    sx, sy = g["scale"]
    tx, ty = g["translate"]
    for z in doc["zones"]:
        for poly in z["polygons"]:
            for ring in poly:
                assert len(ring) % 2 == 0 and len(ring) >= 8
                x = y = 0
                lons = []
                lats = []
                for i in range(0, len(ring), 2):
                    x += ring[i]
                    y += ring[i + 1]
                    lons.append(x * sx + tx)
                    lats.append(y * sy + ty)
                # anello chiuso: dopo la somma cumulativa l'ultimo punto e' il primo
                assert lons[0] == lons[-1] and lats[0] == lats[-1]
                assert len(lons) >= 4
                assert 5.0 < min(lons) and max(lons) < 19.5
                assert 35.0 < min(lats) and max(lats) < 48.0
        bx = z["bbox"]
        assert bx[0] < bx[2] and bx[1] < bx[3]


# 4 ---------------------------------------------------------------------------
def test_file_mancante(tmp_path):
    """TopoJSON assente: exit 2, nessun file scritto, motivo esplicito."""
    fixture = tmp_path / "fix"
    shutil.copytree(FIX, fixture)
    os.remove(fixture / "topo.json")
    proc, out = run(tmp_path, "--now", NOW, fixture=str(fixture), expect=2)
    assert "ERRORE sorgente" in proc.stderr
    assert not out.exists()


# 5 ---------------------------------------------------------------------------
def test_bollettino_vecchio(tmp_path):
    """Bollettino di 3 giorni prima: stale, nessuna zona, motivo con la data."""
    fixture = tmp_path / "fix"
    shutil.copytree(FIX, fixture)
    idx = load(fixture / "index.json")
    idx["date"] = "2026-11-10T15:00:00.000Z"
    idx["name"] = "Bollettino del 10 novembre 2026 ore 15:00"
    with open(fixture / "index.json", "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False)
    _, out = run(tmp_path, "--now", NOW, fixture=str(fixture))
    doc = load(out)
    assert doc["stale"] is True
    assert doc["zones"] == []          # il layer non ha nulla da disegnare
    assert doc["valid_date"] == "2026-11-10"
    assert "ultimo bollettino disponibile" in doc["stale_reason"]
    assert "10 novembre 2026" in doc["stale_reason"]


# 6 ---------------------------------------------------------------------------
def test_budget_auto_semplifica(tmp_path):
    """Budget piccolo: la tolleranza cresce da sola e il file ci sta dentro."""
    _, out = run(tmp_path, "--now", NOW, "--max-bytes", "7000")
    doc = load(out)
    assert out.stat().st_size <= 7000
    assert doc["geometry"]["simplify"]["tolerance_deg"] > fc.DEFAULT_TOLERANCE_DEG
    assert doc["geometry"]["simplify"]["vertices_after"] < doc["geometry"]["simplify"]["vertices_before"]


# 7 ---------------------------------------------------------------------------
def test_stringa_non_mappata(tmp_path):
    """Una classe nuova del DPC diventa 'unknown', mai 'none'."""
    fixture = tmp_path / "fix"
    shutil.copytree(FIX, fixture)
    topo = load(fixture / "topo.json")
    obj = list(topo["objects"].values())[0]
    target = obj["geometries"][0]["properties"]["Nome zona"]
    obj["geometries"][0]["properties"]['Per rischio idraulico'] = "Allerta VIOLA (classe nuova)"
    with open(fixture / "topo.json", "w", encoding="utf-8") as fh:
        json.dump(topo, fh, ensure_ascii=False)
    _, out = run(tmp_path, "--now", NOW, fixture=str(fixture))
    doc = load(out)
    z = [zz for zz in doc["zones"] if zz["name"] == target][0]
    assert z["levels"]["hydraulic"] == "unknown"
    assert "Allerta VIOLA (classe nuova)" in doc["levels_legend"]["unmapped_raw_values"]
    # strict: il dato non passa silenziosamente
    proc, out2 = run(tmp_path, "--now", NOW, "--strict-unknown", fixture=str(fixture), expect=3,
                     out_name="strict.json")
    assert "non mappate" in proc.stderr
    assert not out2.exists()


# 8 ---------------------------------------------------------------------------
def test_budget_default_su_topologia_grande(tmp_path):
    """Topologia sintetica da ~40k vertici: con il budget default (300 KiB)
    il file resta sotto budget e i vertici calano. Non e' il bollettino reale
    (quello e' misurato a parte), e' il ciclo di semplificazione."""
    import math
    arcs = []
    n = 5000
    for k in range(8):
        pts = []
        for i in range(n):
            a = 2 * math.pi * i / n
            r = 4000 + 900 * math.sin(7 * a + k)
            pts.append([int(r * math.cos(a)) + 60000, int(r * math.sin(a)) + 45000])
        # delta encoding come il TopoJSON
        prev = [0, 0]
        d = []
        for p in pts:
            d.append([p[0] - prev[0], p[1] - prev[1]])
            prev = p
        d.append([pts[0][0] - prev[0], pts[0][1] - prev[1]])
        arcs.append(d)
    geoms = []
    for k in range(8):
        geoms.append({
            "type": "Polygon",
            "arcs": [[k]],
            "properties": {
                "Nome zona": "zona sintetica %d" % k,
                "Per rischio idraulico": fc.NONE_RAW,
                "Per rischio temporali": fc.NONE_RAW,
                "Per rischio idrogeologico": "Moderata / ALLERTA ARANCIONE",
                "Rappresentata nella mappa": "Moderata per rischio idrogeologico / ALLERTA ARANCIONE",
                "Comuni": ["a", "b"],
            },
        })
    topo = {
        "type": "Topology",
        "arcs": arcs,
        "transform": {"scale": [0.001, 0.001], "translate": [10.0, 40.0]},
        "objects": {"x": {"type": "GeometryCollection", "geometries": geoms}},
    }
    out = tmp_path / "big.json"
    fixture = tmp_path / "bigfix"
    fixture.mkdir()
    with open(fixture / "topo.json", "w", encoding="utf-8") as fh:
        json.dump(topo, fh)
    with open(fixture / "index.json", "w", encoding="utf-8") as fh:
        json.dump({"date": "2026-11-13T16:00:00Z", "name": "Bollettino sintetico",
                   "today": {"topo_json": "x"}}, fh)
    proc, out = run(tmp_path, "--now", NOW, fixture=str(fixture))
    doc = load(out)
    assert out.stat().st_size <= fc.DEFAULT_MAX_BYTES
    assert fc.DEFAULT_MAX_BYTES == 300 * 1024
    assert doc["geometry"]["simplify"]["vertices_before"] > 40000
    assert doc["geometry"]["simplify"]["vertices_after"] < doc["geometry"]["simplify"]["vertices_before"]
    assert len(doc["zones"]) == 8
