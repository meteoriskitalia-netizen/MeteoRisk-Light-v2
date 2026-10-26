# -*- coding: utf-8 -*-
"""Test del render INCREMENTALE di ``scripts/forecast_v2/render_fm_webp.py``.

Perche' esiste: ``--existing-list`` deve (a) NON ri-renderizzare i frame gia'
pubblicati per lo stesso ``run_id``, (b) NON cancellare i WebP gia' pubblicati
(``prune_dir`` tiene SOLO cio' che gli passi: un ``keep`` costruito sul delta
farebbe del delta l'intero contenuto della directory), (c) scrivere il manifest
per-variabile in UNIONE (il client legge ``valid_times`` da li'), (d) lasciare
``expected_frames`` sull'ORIZZONTE e non sul delta, (e) ri-renderizzare quando
cambia la palette, perche' altrimenti i WebP restano alla scala vecchia senza
che niente lo dica.

I frame ``.om`` dei test sono SCRITTI CON ``OmFileWriter`` (formato vero, griglia
9x9, due variabili + i 4 scalari che scrive il motore): nessun mock del modulo
sotto test, nessun archivio finto, nessuna dipendenza di rete. Il render gira con
``--workers 1`` per non pagare lo spawn dei processi (Windows) e per avere un
output deterministico.

Se ``numpy``/``PIL``/``omfiles`` mancano l'intero modulo viene SALTATO
(``importorskip``): un verde senza dipendenze non sarebbe una prova.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sys

import numpy as np
import pytest

pytest.importorskip("PIL")
pytest.importorskip("omfiles")
from omfiles import OmFileWriter  # noqa: E402  (dopo importorskip, dipendenza dichiarata)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MODULE_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "render_fm_webp.py")

DOMAIN = "testdom"
REGION = "italia"
REF_TIME = "2026-01-01T00:00:00Z"
RUN_ID = "20260101T000000Z"
VALID_TIMES = ["2026-01-01T00:00Z", "2026-01-01T01:00Z",
               "2026-01-01T02:00Z", "2026-01-01T03:00Z"]
VARS = ["temperature_2m", "pressure_msl"]
GRID = {"nx": 9, "ny": 9, "lonMin": 6.5, "latMin": 36.5,
        "dx": 1.3375, "dy": 1.3375}
BBOX = {"lat_min": 36.5, "lon_min": 6.5, "lat_max": 47.2, "lon_max": 18.6}


def _load_module():
    spec = importlib.util.spec_from_file_location("render_fm_webp_under_test",
                                                  MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load_module()
assert R._run_id(REF_TIME) == RUN_ID, R._run_id(REF_TIME)


# --------------------------------------------------------------------------- #
# Fixture: un dominio VERO (frame .om scritti con OmFileWriter) + manifest
# --------------------------------------------------------------------------- #
def _write_om(path, fields, valid_time):
    """Scrive un frame ``.om`` con lo stesso schema del motore V2."""
    import datetime as _dt

    def epoch(iso):
        return int(_dt.datetime.fromisoformat(
            str(iso).replace("Z", "+00:00")).timestamp())

    w = OmFileWriter.at_path(str(path))
    children = []
    for name, arr in fields.items():
        arr = np.ascontiguousarray(np.asarray(arr, dtype=np.float32))
        chunks = [min(32, int(arr.shape[0])), min(32, int(arr.shape[1]))]
        children.append(w.write_array(arr, chunks=chunks, scale_factor=10.0,
                                      add_offset=0.0,
                                      compression="pfor_delta_2d_int16",
                                      name=name))
    children.append(w.write_scalar("GEOGCRS[test]", name="crs_wkt"))
    children.append(w.write_scalar("latitude longitude", name="coordinates"))
    children.append(w.write_scalar(epoch(valid_time), name="valid_time"))
    children.append(w.write_scalar(epoch(REF_TIME),
                                   name="forecast_reference_time"))
    root = w.write_group(name="", children=children)
    w.close(root)


@pytest.fixture()
def domain_tree(tmp_path):
    """``<tmp>/in/<dominio>`` con 4 frame reali e il ``latest.json`` del motore."""
    in_root = tmp_path / "in"
    ddir = in_root / DOMAIN
    ddir.mkdir(parents=True)
    yy, xx = np.mgrid[0:GRID["ny"], 0:GRID["nx"]]
    frames = []
    for i, vt in enumerate(VALID_TIMES):
        # Valori nel primo intervallo delle scale: ogni pixel e' opaco, quindi
        # un cambio di palette cambia DAVVERO i byte del WebP.
        temp = np.full((GRID["ny"], GRID["nx"]), -8.0 + i, dtype=np.float32)
        temp += xx * 0.1
        pres = np.full((GRID["ny"], GRID["nx"]), 1008.0 + i, dtype=np.float32)
        pres += yy * 0.5
        name = R.valid_time_to_filename(vt) + ".om"
        _write_om(ddir / name, {"temperature_2m": temp, "pressure_msl": pres}, vt)
        frames.append({"file": name, "valid_time": vt,
                       "bytes": os.path.getsize(ddir / name)})
    manifest = {
        "domain": DOMAIN,
        "reference_time": REF_TIME,
        "valid_times": list(VALID_TIMES),
        "frames": frames,
        "grid": dict(GRID),
        "bbox": dict(BBOX),
        "var_order": list(VARS),
    }
    (ddir / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"in_root": str(in_root), "domain_dir": str(ddir)}


# --------------------------------------------------------------------------- #
# Helper del test
# --------------------------------------------------------------------------- #
def _run(in_root, out_dir, extra=()):
    argv = ["--in", str(in_root), "--out", str(out_dir), "--domains", DOMAIN,
            "--regions", REGION, "--vars", ",".join(VARS),
            "--upsample", "1", "--workers", "1", "--method", "4",
            "--quality", "80"] + list(extra)
    return R.main(argv)


def _webps(out_dir):
    res = {}
    for root, _dirs, files in os.walk(str(out_dir)):
        for name in files:
            if not name.lower().endswith(".webp"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, str(out_dir)).replace("\\", "/")
            with open(path, "rb") as fh:
                res[rel] = hashlib.sha256(fh.read()).hexdigest()
    return res


def _var_manifest(out_dir, var, region=REGION):
    path = os.path.join(str(out_dir), region, DOMAIN, "runs", RUN_ID, var,
                        "latest.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _manifests_no_ts(out_dir):
    """Tutti i ``latest.json`` con ``generated_at`` rimosso (unico campo volatile)."""
    res = {}
    for root, _dirs, files in os.walk(str(out_dir)):
        for name in files:
            if name != "latest.json":
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, str(out_dir)).replace("\\", "/")
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            obj.pop("generated_at", None)
            res[rel] = obj
    return res


def _write_list(path, rels, prefix="data/fm2-rendered"):
    """Elenco di path pubblicati, come lo produce ``git ls-tree`` sul branch."""
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        for rel in sorted(rels):
            fh.write("%s/%s\n" % (prefix, rel))
    return str(path)


def _require(in_root, out_dir, extra=()):
    rc = _run(in_root, out_dir, extra)
    assert rc == 0, "render fallito (rc=%d)" % rc


# --------------------------------------------------------------------------- #
# 1) elenco assente / 2) elenco vuoto -> comportamento storico
# --------------------------------------------------------------------------- #
def test_elenco_assente_e_elenco_vuoto_rendono_tutto(domain_tree, tmp_path):
    out_none = tmp_path / "out_none"
    _require(domain_tree["in_root"], out_none)
    out_empty = tmp_path / "out_empty"
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    rc = _run(domain_tree["in_root"], out_empty, ["--existing-list", str(empty)])
    assert rc == 0

    a, b = _webps(out_none), _webps(out_empty)
    assert len(a) == len(VALID_TIMES) * len(VARS) == 8, sorted(a)
    assert a == b, "elenco vuoto e argomento assente devono dare gli stessi byte"
    assert len(_var_manifest(out_none, "temperature_2m")["valid_times"]) == 4


def test_elenco_vuoto_non_disabilita_il_prune(domain_tree, tmp_path):
    """Un elenco vuoto = nessun pubblicato: il prune resta quello storico."""
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out)
    stale = os.path.join(str(out), "italia", DOMAIN, "runs", RUN_ID,
                         "temperature_2m", "2025-12-31T2300.webp")
    with open(stale, "wb") as fh:
        fh.write(b"stale")
    empty = tmp_path / "empty.txt"
    empty.write_text("\n", encoding="utf-8")
    _run(domain_tree["in_root"], out, ["--existing-list", str(empty)])
    assert not os.path.isfile(stale), "senza elenco il frame fuori orizzonte va potato"


# --------------------------------------------------------------------------- #
# 3) elenco che copre tutto -> nessun render, byte e manifest intatti
# --------------------------------------------------------------------------- #
def test_elenco_che_copre_tutto_non_rirenderizza(domain_tree, tmp_path, capsys):
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out)
    before = _webps(out)
    man_before = _var_manifest(out, "temperature_2m")
    lst = _write_list(tmp_path / "all.txt", before)

    rc = _run(domain_tree["in_root"], out, ["--existing-list", lst])
    assert rc == 0
    out_txt = capsys.readouterr().out
    assert "4 frame gia' pubblicati" in out_txt, out_txt[-2000:]
    assert "0 frame resi" in out_txt

    assert _webps(out) == before
    man_after = _var_manifest(out, "temperature_2m")
    assert man_after["frames"] == man_before["frames"]
    assert man_after["valid_times"] == man_before["valid_times"]


def test_elenco_che_copre_tutto_conta_up_to_date(domain_tree, tmp_path, capsys):
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out)
    lst = _write_list(tmp_path / "all.txt", _webps(out))
    capsys.readouterr()
    rc = _run(domain_tree["in_root"], out, ["--existing-list", lst])
    assert rc == 0, "un dominio senza frame nuovi NON e' un errore"
    assert "up-to-date" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# GRANULARITA' (regione, variabile, frame): una variabile nuova su un frame
# gia' pubblicato costa 1 file per regione, NON l'intero frame.
# --------------------------------------------------------------------------- #
REGION_B = "europa"


def _run_regions(in_root, out_dir, extra=(), regions=(REGION, REGION_B)):
    argv = ["--in", str(in_root), "--out", str(out_dir), "--domains", DOMAIN,
            "--regions", ",".join(regions), "--vars", ",".join(VARS),
            "--upsample", "1", "--workers", "1", "--method", "4",
            "--quality", "80"] + list(extra)
    return R.main(argv)


def _shard_copy(src, dst, drop=()):
    """Copia il pubblicato e toglie cio' che lo shard NON ha.

    Lo shard di produzione non ha i ``latest.json`` per-variabile (il checkout
    del job non contiene ``data/fm2-rendered``) e non ha i WebP che l'elenco
    dichiara mancanti: ``drop`` sono i path relativi da togliere.
    """
    shutil.copytree(str(src), str(dst))
    drop = set(drop)
    for root, _dirs, files in os.walk(str(dst)):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, str(dst)).replace("\\", "/")
            if name == "latest.json" or rel in drop:
                os.remove(path)


def _var_paths(webps, var):
    return sorted(k for k in webps if "/%s/" % var in k)


@pytest.fixture()
def write_counter(monkeypatch):
    """Conta le SCRITTURE reali (``save_webp``), non i soli path nuovi.

    Il percorso "per frame" riscrive anche i file gia' pubblicati: stessi byte,
    stesso path, quindi un diff di alberi non lo vede. ``--workers 1`` tiene il
    conteggio in-process.
    """
    calls = []
    real = R.save_webp

    def counting_save(rgba, path, *args, **kwargs):
        calls.append(path)
        return real(rgba, path, *args, **kwargs)

    monkeypatch.setattr(R, "save_webp", counting_save)
    return calls


def test_variabile_nuova_su_frame_pubblicati_rende_solo_quella(domain_tree,
                                                               tmp_path, capsys,
                                                               write_counter):
    """2 variabili, 4 frame, 2 regioni: pubblicata 1 variabile -> 8 celle da 16.

    E' il caso che il 2026-10-26 ha fatto credere che lo skip fosse per frame:
    qui si misura che il lavoro e' per VARIABILE (8 celle su 16, non 16).
    """
    out_full = tmp_path / "full"
    assert _run_regions(domain_tree["in_root"], out_full) == 0
    full = _webps(out_full)
    assert len(full) == len(VALID_TIMES) * len(VARS) * 2 == 16, sorted(full)

    out = tmp_path / "shard"
    _shard_copy(out_full, out, drop=_var_paths(full, "pressure_msl"))
    published = {k for k in full if "/pressure_msl/" not in k}
    lst = _write_list(tmp_path / "pub.txt", published)

    capsys.readouterr()
    write_counter.clear()
    rc = _run_regions(domain_tree["in_root"], out,
                      ["--existing-list", lst,
                       "--existing-palette-sha", R.palette_sha256()])
    assert rc == 0
    out_txt = capsys.readouterr().out
    # 4 frame x 2 variabili x 2 regioni = 16 celle; 8 gia' pubblicate.
    assert "gia' pubblicate 8 su 16" in out_txt, out_txt[-1500:]
    assert "4 variabili x frame da rendere" in out_txt, out_txt[-1500:]
    assert len(write_counter) == 8, "una variabile, non il frame: 8 file su 16"
    # il frame NON viene rifatto: si rende solo la variabile mancante
    assert _webps(out) == full, "stessi file, stessi byte del render completo"
    assert len(_manifests_no_ts(out)) == len(_manifests_no_ts(out_full))


def test_una_regione_mancante_rende_solo_quella_regione(domain_tree, tmp_path,
                                                        capsys, write_counter):
    """Una variabile pubblicata in 2 regioni su 3 si scrive SOLO nella terza."""
    out_full = tmp_path / "full"
    assert _run_regions(domain_tree["in_root"], out_full) == 0
    full = _webps(out_full)
    missing = [k for k in _var_paths(full, "pressure_msl")
               if k.startswith(REGION_B + "/")]
    assert len(missing) == len(VALID_TIMES)

    out = tmp_path / "shard"
    _shard_copy(out_full, out, drop=missing)
    published = {k for k in full if k not in missing}
    lst = _write_list(tmp_path / "pub.txt", published)

    capsys.readouterr()
    write_counter.clear()
    rc = _run_regions(domain_tree["in_root"], out,
                      ["--existing-list", lst,
                       "--existing-palette-sha", R.palette_sha256()])
    assert rc == 0
    out_txt = capsys.readouterr().out
    assert "gia' pubblicate 12 su 16" in out_txt, out_txt[-1500:]
    # 4 celle mancanti (1 variabile x 4 frame), NON 8 (tutte e 2 le regioni)
    assert len(write_counter) == len(VALID_TIMES), sorted(write_counter)
    assert all(REGION_B in p for p in write_counter), sorted(write_counter)
    assert _webps(out) == full


def test_manifest_stantio_non_prova_la_pubblicazione(domain_tree, tmp_path,
                                                     write_counter):
    """Un ``latest.json`` che nomina un WebP assente NON vale come pubblicato.

    Scenario vero: render interrotto (o push fallito) lascia il manifest di una
    variabile senza i suoi WebP. Con il manifest come prova il file non viene
    mai ricreato e il client prende un 404; con l'elenco del branch come prova
    (o il file su disco) viene rifatto.
    """
    out_full = tmp_path / "full"
    _require(domain_tree["in_root"], out_full)
    full = _webps(out_full)
    gone = _var_paths(full, "temperature_2m")[0]
    out = tmp_path / "shard"
    shutil.copytree(str(out_full), str(out))
    os.remove(os.path.join(str(out), gone.replace("/", os.sep)))
    # il manifest resta (stantio) e nomina ancora il file tolto
    published = {k for k in full if k != gone}
    lst = _write_list(tmp_path / "pub.txt", published)

    write_counter.clear()
    rc = _run(domain_tree["in_root"], out,
              ["--existing-list", lst,
               "--existing-palette-sha", R.palette_sha256()])
    assert rc == 0
    assert len(write_counter) == 1, write_counter
    assert os.path.isfile(os.path.join(str(out), gone.replace("/", os.sep))), \
        "il WebP mancante deve essere ricreato"
    assert _webps(out) == full


# --------------------------------------------------------------------------- #
# PROVA DI IDENTITA': meta' + incrementale == completo
# --------------------------------------------------------------------------- #
def test_incrementale_da_meta_e_identico_al_render_completo(domain_tree, tmp_path):
    out_a = tmp_path / "a"
    _require(domain_tree["in_root"], out_a)

    out_b = tmp_path / "b"
    _require(domain_tree["in_root"], out_b, extra=["--limit-frames", "2"])
    half = _webps(out_b)
    assert len(half) == 2 * len(VARS)
    lst = _write_list(tmp_path / "half.txt", half)

    rc = _run(domain_tree["in_root"], out_b, ["--existing-list", lst])
    assert rc == 0

    assert _webps(out_b) == _webps(out_a), "stessi file, stessi byte"
    assert _manifests_no_ts(out_b) == _manifests_no_ts(out_a), \
        "manifest in unione identico al completo (a meno di generated_at)"
    for name in ("index.json",):
        for region in (REGION, "europa", "north", "domain"):
            pa = os.path.join(str(out_a), region, name)
            pb = os.path.join(str(out_b), region, name)
            assert os.path.isfile(pa) == os.path.isfile(pb)
            if os.path.isfile(pa):
                with open(pa, "r", encoding="utf-8") as fh:
                    ja = json.load(fh)
                with open(pb, "r", encoding="utf-8") as fh:
                    jb = json.load(fh)
                ja.pop("generated_at", None)
                jb.pop("generated_at", None)
                assert ja == jb, "index.json %s diverso" % region


# --------------------------------------------------------------------------- #
# 4) prune con keep seminato: 10 pubblicati + 3 nuovi = 13 file
# --------------------------------------------------------------------------- #
def test_prune_con_keep_seminato_tiene_13_file(tmp_path):
    vdir = tmp_path / "var"
    vdir.mkdir()
    published = ["2026-01-01T%02d00.webp" % h for h in range(10)]
    for name in published:
        (vdir / name).write_bytes(b"published")
    written = ["2026-01-01T1200.webp", "2026-01-01T1300.webp",
               "2026-01-01T1400.webp"]
    for name in written:
        (vdir / name).write_bytes(b"fresh")

    removed = R.prune_published_dir(str(vdir), set(published) | set(written),
                                    True, set(published), label="test")
    assert removed == 0
    assert len(sorted(vdir.glob("*.webp"))) == 13, "10 + 3 = 13, non 3"


def test_prune_naive_sul_delta_cancella_i_pubblicati(tmp_path):
    """Documenta la TRAPPOLA: keep costruito sul solo delta cancella il resto."""
    vdir = tmp_path / "var"
    vdir.mkdir()
    published = ["2026-01-01T%02d00.webp" % h for h in range(10)]
    for name in published:
        (vdir / name).write_bytes(b"published")
    written = ["2026-01-01T1200.webp"]
    for name in written:
        (vdir / name).write_bytes(b"fresh")

    removed = R.prune_dir(str(vdir), set(written))   # keep = SOLO il delta
    assert removed == 10
    assert len(sorted(vdir.glob("*.webp"))) == 1


def test_prune_saltato_se_l_elenco_non_copre_la_directory(tmp_path, capsys):
    """Elenco che "dice niente" su una directory piena: non si cancella nulla."""
    vdir = tmp_path / "var"
    vdir.mkdir()
    for name in ("a.webp", "b.webp"):
        (vdir / name).write_bytes(b"x")
    removed = R.prune_published_dir(str(vdir), set(), True, set(), label="t")
    assert removed == 0
    assert len(sorted(vdir.glob("*.webp"))) == 2
    assert "prune saltato" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 5) manifest in unione + expected_frames = ORIZZONTE (non il delta)
# --------------------------------------------------------------------------- #
def test_manifest_in_unione_e_expected_frames_sull_orizzonte(domain_tree, tmp_path):
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out, extra=["--limit-frames", "1"])
    man = _var_manifest(out, "temperature_2m")
    assert man["valid_times"] == [VALID_TIMES[0]]
    assert man["expected_frames"] == len(VALID_TIMES), "orizzonte, non il delta"

    lst = _write_list(tmp_path / "one.txt",
                      {"italia/%s/runs/%s/temperature_2m/%s.webp"
                       % (DOMAIN, RUN_ID, R.valid_time_to_filename(VALID_TIMES[0])),
                       "italia/%s/runs/%s/pressure_msl/%s.webp"
                       % (DOMAIN, RUN_ID, R.valid_time_to_filename(VALID_TIMES[0]))})
    rc = _run(domain_tree["in_root"], out, ["--existing-list", lst])
    assert rc == 0

    man = _var_manifest(out, "temperature_2m")
    assert man["valid_times"] == VALID_TIMES, man["valid_times"]
    assert [f["file"] for f in man["frames"]] == \
        [R.valid_time_to_filename(v) + ".webp" for v in VALID_TIMES]
    assert man["expected_frames"] == len(VALID_TIMES)
    # il client legge count/valid_times dall'index.json: stesso insieme
    with open(os.path.join(str(out), REGION, "index.json"), "r",
              encoding="utf-8") as fh:
        index = json.load(fh)
    theme = index["domains"][DOMAIN]["variables"]["temperature_2m"]["themes"]["light"]
    assert theme["valid_times"] == VALID_TIMES
    assert theme["count"] == len(VALID_TIMES)


def test_incrementale_senza_manifest_ricostruisce_e_non_pota(domain_tree, tmp_path):
    """Shard senza manifest: `bytes` non e' noto, i file pubblicati restano."""
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out)
    before = _webps(out)
    # si cancellano i manifest per-variabile: resta solo l'elenco del branch
    for root, _dirs, files in os.walk(str(out)):
        if "latest.json" in files and os.path.basename(root) in VARS:
            os.remove(os.path.join(root, "latest.json"))
    lst = _write_list(tmp_path / "all.txt", before)

    rc = _run(domain_tree["in_root"], out, ["--existing-list", lst])
    assert rc == 0
    assert _webps(out) == before, "il prune non deve toccare i pubblicati"
    man = _var_manifest(out, "temperature_2m")
    assert man["valid_times"] == VALID_TIMES
    assert man["palette_sha256"] == R.palette_sha256()
    # senza manifest la palette NON e' dimostrabile: nessuno skip, quindi i frame
    # vengono ridisegnati e il manifest torna con i byte misurati (mai inventati).
    assert all(isinstance(f["bytes"], int) for f in man["frames"])


def test_palette_dichiarata_sblocca_lo_skip_senza_manifest(domain_tree, tmp_path,
                                                           capsys):
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out)
    before = _webps(out)
    for root, _dirs, files in os.walk(str(out)):
        if "latest.json" in files and os.path.basename(root) in VARS:
            os.remove(os.path.join(root, "latest.json"))
    lst = _write_list(tmp_path / "all.txt", before)
    sha = R.palette_sha256()
    capsys.readouterr()
    rc = _run(domain_tree["in_root"], out,
              ["--existing-list", lst, "--existing-palette-sha", sha])
    assert rc == 0
    assert "4 frame gia' pubblicati" in capsys.readouterr().out
    assert _webps(out) == before
    # i frame non sono stati ridisegnati: la entry viene RICOSTRUITA dal nome e
    # `bytes` resta None (la dimensione non e' nell'elenco: uno 0 sarebbe falso)
    man = _var_manifest(out, "temperature_2m")
    assert man["valid_times"] == VALID_TIMES
    assert all(f["bytes"] is None for f in man["frames"])


# --------------------------------------------------------------------------- #
# 6) impronta della palette
# --------------------------------------------------------------------------- #
def _palette_copy(tmp_path):
    dst = tmp_path / "color_scales.json"
    shutil.copyfile(R.COLOR_SCALES_PATH, str(dst))
    return dst


def _use_palette(monkeypatch, path):
    """Sostituisce la palette del renderer con una copia controllabile.

    ``load_color_scales`` ha il path come DEFAULT di firma (legato alla
    definizione), quindi patchare il solo ``COLOR_SCALES_PATH`` non basta: si
    sostituisce la funzione, e ``palette_sha256``/``copy_color_scales`` leggono il
    globale (che viene patchato allo stesso modo). Nessuna modifica al modulo
    sotto test: si cambia solo la SORGENTE della palette.
    """
    real = R.load_color_scales
    monkeypatch.setattr(R, "COLOR_SCALES_PATH", str(path))
    monkeypatch.setattr(R, "load_color_scales", lambda _p=None: real(str(path)))


def test_palette_sha256_registrata_nel_manifest(domain_tree, tmp_path):
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out)
    sha = R.palette_sha256()
    assert len(sha) == 64
    for var in VARS:
        assert _var_manifest(out, var)["palette_sha256"] == sha


def test_palette_cambiata_rirenderizza_la_variabile(domain_tree, tmp_path,
                                                    monkeypatch, capsys):
    pal = _palette_copy(tmp_path)
    _use_palette(monkeypatch, pal)
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out)
    before = _webps(out)
    sha_before = _var_manifest(out, "temperature_2m")["palette_sha256"]
    lst = _write_list(tmp_path / "all.txt", before)

    # 1) stessa palette -> skip
    capsys.readouterr()
    rc = _run(domain_tree["in_root"], out, ["--existing-list", lst])
    assert rc == 0
    assert "4 frame gia' pubblicati" in capsys.readouterr().out
    assert _webps(out) == before

    # 2) un colore cambia -> la variabile NON puo' essere saltata
    data = json.loads(pal.read_text(encoding="utf-8"))
    variant = data["scales"]["temperature_2m"]["light"]
    variant["colors"] = [[(c[0] + 40) % 256, c[1], c[2], c[3]]
                         for c in variant["colors"]]
    pal.write_text(json.dumps(data), encoding="utf-8")
    sha_after = R.palette_sha256()
    assert sha_after != sha_before

    capsys.readouterr()
    rc = _run(domain_tree["in_root"], out, ["--existing-list", lst])
    assert rc == 0
    out_txt = capsys.readouterr().out
    assert "0 frame gia' pubblicati" in out_txt, out_txt[-1500:]
    assert "4 frame resi" in out_txt

    man = _var_manifest(out, "temperature_2m")
    assert man["palette_sha256"] == sha_after
    after = _webps(out)
    changed = [k for k in before if before[k] != after.get(k)]
    assert changed, "i WebP della variabile devono essere ridisegnati"
    # solo temperature_2m cambia: pressure_msl ha la stessa palette di prima
    assert all("temperature_2m" in k for k in changed), changed


def test_versione_webp_cambiata_rirenderizza(domain_tree, tmp_path, capsys):
    """Un manifest con una versione VECCHIA non prova nulla: si ri-renderizza."""
    out = tmp_path / "out"
    _require(domain_tree["in_root"], out)
    lst = _write_list(tmp_path / "all.txt", _webps(out))
    for var in VARS:
        path = os.path.join(str(out), REGION, DOMAIN, "runs", RUN_ID, var,
                            "latest.json")
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        obj["version"] = "fm2-webp-0"
        R.write_json(path, obj)
    capsys.readouterr()
    rc = _run(domain_tree["in_root"], out, ["--existing-list", lst])
    assert rc == 0
    assert "0 frame gia' pubblicati" in capsys.readouterr().out
    assert _var_manifest(out, "temperature_2m")["version"] == R.WEBP_VERSION


# --------------------------------------------------------------------------- #
# Helper: elenco, path, stato per-directory, unione
# --------------------------------------------------------------------------- #
def test_existing_key_from_path_tollera_prefissi_e_separatori():
    p = ("data/fm2-rendered/italia/dwd_icon_eu/runs/20261025T180000Z/"
         "temperature_2m/2026-10-25T1800.webp")
    assert R.existing_key_from_path(p) == (
        ("italia", "dwd_icon_eu", "20261025T180000Z", "temperature_2m"),
        "2026-10-25T1800.webp")
    assert R.existing_key_from_path("./" + p) == R.existing_key_from_path(p)
    assert R.existing_key_from_path(p.replace("/", "\\")) == \
        R.existing_key_from_path(p)
    assert R.existing_key_from_path("/abs/root/" + p) == \
        R.existing_key_from_path(p)
    assert R.existing_key_from_path("data/fm2-rendered/italia/x.webp") is None
    assert R.existing_key_from_path(
        "data/fm2-rendered/italia/d/runs/r/v/f.webp".replace("/runs/", "/nos/")
    ) is None
    assert R.existing_key_from_path("") is None


def test_load_existing_list_vuoto_e_righe_ignorate(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("\n\n# commento\n", encoding="utf-8")
    table, stats = R.load_existing_list(str(empty))
    assert table == {} and stats == {"lines": 0, "kept": 0, "ignored": 0,
                                     "sample": []}

    mixed = tmp_path / "mixed.txt"
    mixed.write_text(
        "data/fm2-rendered/italia/d/runs/r1/v1/2026-01-01T0000.webp\n"
        "spazzatura\n"
        "data/fm2-rendered/europa/d/runs/r1/v1/2026-01-01T0100.webp\n",
        encoding="utf-8")
    table, stats = R.load_existing_list(str(mixed))
    assert stats["lines"] == 3 and stats["kept"] == 2 and stats["ignored"] == 1
    assert table[("italia", "d", "r1", "v1")] == {"2026-01-01T0000.webp"}
    assert table[("europa", "d", "r1", "v1")] == {"2026-01-01T0100.webp"}


def test_valid_time_from_webp_name_inverte_il_writer():
    for vt in VALID_TIMES:
        assert R.valid_time_from_webp_name(
            R.valid_time_to_filename(vt) + ".webp") == vt
    assert R.valid_time_from_webp_name("non-un-tempo.webp") is None


def test_frames_from_names_senza_bytes_e_senza_tempi_inventati():
    recs = R.frames_from_names({"2026-01-01T0200.webp", "2026-01-01T0000.webp",
                                "boh.webp"})
    assert [r["file"] for r in recs] == ["2026-01-01T0000.webp",
                                        "2026-01-01T0200.webp"]
    assert [r["valid_time"] for r in recs] == [VALID_TIMES[0], VALID_TIMES[2]]
    assert all(r["bytes"] is None for r in recs)


def test_var_publish_state_senza_manifest_ricostruisce_e_non_riusa(tmp_path):
    vdir = tmp_path / "v"
    vdir.mkdir()
    names = {"2026-01-01T0000.webp", "2026-01-01T0100.webp"}
    st = R.var_publish_state(str(vdir), names, "a" * 64)
    assert st["reuse"] is False and st["reason"] == "no_manifest"
    assert st["files"] == names
    assert [r["valid_time"] for r in st["frames"]] == VALID_TIMES[:2]


def test_var_publish_state_manifest_con_palette_diversa_non_riusa(tmp_path):
    vdir = tmp_path / "v"
    vdir.mkdir()
    (vdir / "2026-01-01T0000.webp").write_bytes(b"x")
    R.write_json(str(vdir / "latest.json"), {
        "version": R.WEBP_VERSION, "palette_sha256": "b" * 64,
        "frames": [{"file": "2026-01-01T0000.webp",
                    "valid_time": VALID_TIMES[0], "bytes": 1}],
        "valid_times": [VALID_TIMES[0]], "bounds": [[0, 0], [1, 1]],
        "grid": {"nx": 2, "ny": 2},
    })
    st = R.var_publish_state(str(vdir), {"2026-01-01T0000.webp"}, "a" * 64)
    assert st["reuse"] is False and st["reason"] == "palette_changed"
    assert st["files"] == {"2026-01-01T0000.webp"}

    st = R.var_publish_state(str(vdir), {"2026-01-01T0000.webp"}, "b" * 64)
    assert st["reuse"] is True and st["reason"] == "manifest"
    assert st["frames"][0]["bytes"] == 1


def test_var_publish_state_versione_diversa_non_riusa(tmp_path):
    vdir = tmp_path / "v"
    vdir.mkdir()
    R.write_json(str(vdir / "latest.json"), {
        "version": "fm2-webp-0", "palette_sha256": "a" * 64,
        "frames": [{"file": "f.webp", "valid_time": VALID_TIMES[0], "bytes": 1}],
        "valid_times": [VALID_TIMES[0]], "bounds": [[0, 0], [1, 1]],
        "grid": {"nx": 2, "ny": 2},
    })
    st = R.var_publish_state(str(vdir), {"f.webp"}, "a" * 64)
    assert st["reuse"] is False and st["reason"] == "version_changed"


def test_var_publish_state_elenco_e_manifest_si_completano(tmp_path):
    """Un file elencato ma assente dal manifest resta protetto e in manifest."""
    vdir = tmp_path / "v"
    vdir.mkdir()
    R.write_json(str(vdir / "latest.json"), {
        "version": R.WEBP_VERSION, "palette_sha256": "a" * 64,
        "frames": [{"file": "2026-01-01T0000.webp",
                    "valid_time": VALID_TIMES[0], "bytes": 7}],
        "valid_times": [VALID_TIMES[0]], "bounds": [[0, 0], [1, 1]],
        "grid": {"nx": 2, "ny": 2},
    })
    known = {"2026-01-01T0000.webp", "2026-01-01T0100.webp"}
    st = R.var_publish_state(str(vdir), known, "a" * 64)
    assert st["reuse"] is True
    assert st["files"] == known
    by_file = {r["file"]: r for r in st["frames"]}
    assert by_file["2026-01-01T0000.webp"]["bytes"] == 7      # dal manifest
    assert by_file["2026-01-01T0100.webp"]["bytes"] is None   # ricostruita
    assert by_file["2026-01-01T0100.webp"]["valid_time"] == VALID_TIMES[1]


def test_merge_frame_records_unione_fresh_vince_e_ordina():
    seed = [{"file": "b.webp", "valid_time": VALID_TIMES[1], "bytes": None},
            {"file": "a.webp", "valid_time": VALID_TIMES[0], "bytes": 5}]
    fresh = [{"file": "b.webp", "valid_time": VALID_TIMES[1], "bytes": 9},
             {"file": "c.webp", "valid_time": VALID_TIMES[2], "bytes": 3}]
    recs, keep = R.merge_frame_records(seed, fresh)
    assert [r["file"] for r in recs] == ["a.webp", "b.webp", "c.webp"]
    assert {r["file"]: r["bytes"] for r in recs}["b.webp"] == 9
    assert keep == {"a.webp", "b.webp", "c.webp"}


def test_merge_frame_records_tiene_i_nomi_senza_tempo(tmp_path):
    recs, keep = R.merge_frame_records(
        [{"file": "strano.webp", "valid_time": None, "bytes": None}], [])
    assert recs == []
    assert keep == {"strano.webp"}, "un nome illeggibile non va potato"


# --------------------------------------------------------------------------- #
# Errori dichiarati
# --------------------------------------------------------------------------- #
def test_existing_list_illeggibile_e_fatale(domain_tree, tmp_path):
    rc = _run(domain_tree["in_root"], tmp_path / "out",
              ["--existing-list", str(tmp_path / "non-esiste.txt")])
    assert rc == 2, "un elenco richiesto e illeggibile NON deve degradare in silenzio"
