# -*- coding: utf-8 -*-
"""Test della PROPAGAZIONE di ``projection`` nella ``timeline.json``.

Perche' esiste: il client (``fmRegionProjection``) risolve la proiezione con la
precedenza dominio -> timeline -> regione -> ``"mercator"``. Se ``index.json``
non e' ancora arrivato e la timeline non dichiara nulla, non c'e' fallback e un
disco polare viene disegnato come striscia Mercator per quella finestra.

Le due regole verificate qui sono quelle (identiche) di ``index.json``:

* ``domains.<dominio>.projection`` SEMPRE se il dominio la dichiara, e sempre
  una STRINGA (la geometria sotto ``projection_meta``);
* ``projection`` a livello REGIONE solo se TUTTI i domini indicizzati dichiarano
  la STESSA proiezione. Con un dominio ancora Mercator la chiave va OMESSA,
  perche' il client la applica a OGNI dominio della regione: con ``dwd_icon``
  su una sorgente 2879x521 la striscia veniva disegnata come un disco
  (misurato in produzione il 2026-10-26, fix ``09f8158b``).

Alberi di test sintetici ma reali su disco (stessa struttura del branch dati
``fm2-rendered``), nessun mock del modulo sotto test, nessuna rete.
"""
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MODULE_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                           "build_timeline_index.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("build_timeline_index_proj_ut",
                                                  MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()

REGION = "italia"
DISC = "ecmwf_ifs025"
STRIP = "dwd_icon"
DISC_META = {"type": "polar_stereographic", "size": 1000,
             "edge_lat": 20.0, "max_lat": 90.0, "circle": True}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle)


def _stamp(valid_time):
    return valid_time.replace(":", "").replace("Z", "")


def _write_run(root, domain, run_id, reference_time, valid_times, var="varX",
               projection=None, projection_meta=None, region=REGION,
               with_files=True):
    """``<root>/<regione>/<dominio>/runs/<run_id>/<var>/latest.json`` (+ webp)."""
    var_dir = os.path.join(root, region, domain, "runs", run_id, var)
    os.makedirs(var_dir, exist_ok=True)
    frames = []
    for valid_time in valid_times:
        name = _stamp(valid_time) + ".webp"
        if with_files:
            with open(os.path.join(var_dir, name), "wb") as handle:
                handle.write(b"RIFF\x00\x00\x00\x00WEBPfixture")
        frames.append({"file": name, "valid_time": valid_time, "bytes": 18})
    manifest = {
        "version": "fm2-webp-1",
        "region": region,
        "domain": domain,
        "variable": var,
        "theme": "light",
        "unit": "1",
        "reference_time": reference_time,
        "generated_at": reference_time,
        "expected_frames": len(frames),
        "frames": frames,
        "valid_times": list(valid_times),
    }
    if projection is not None:
        manifest["projection"] = projection
    if projection_meta is not None:
        manifest["projection_meta"] = projection_meta
    _write_json(os.path.join(var_dir, "latest.json"), manifest)
    return var_dir


def _run_main(root, out, *extra):
    return mod.main(["--root", root, "--out-root", out,
                     "--now", "2026-10-26T12:00:00Z",
                     "--keep-past-hours", "100000"] + list(extra))


def _load(out, region=REGION):
    with open(os.path.join(out, region, "timeline.json"), encoding="utf-8") as fh:
        return json.load(fh)


HOURS = ["2026-10-26T00:00Z", "2026-10-26T01:00Z", "2026-10-26T02:00Z"]


def _mixed_tree(root):
    """ECMWF su disco stereografico + dwd_icon sulla striscia Mercator."""
    _write_run(root, DISC, "20261026T000000Z", "2026-10-26T00:00:00Z", HOURS,
               projection=mod.POLAR_PROJECTION, projection_meta=DISC_META)
    _write_run(root, STRIP, "20261026T000000Z", "2026-10-26T00:00:00Z", HOURS)


def _all_disc_tree(root):
    for domain in (DISC, STRIP):
        _write_run(root, domain, "20261026T000000Z", "2026-10-26T00:00:00Z",
                   HOURS, projection=mod.POLAR_PROJECTION,
                   projection_meta=DISC_META)


# --------------------------------------------------------------------------- #
# livello DOMINIO
# --------------------------------------------------------------------------- #
def test_domain_projection_is_propagated_as_a_string(tmp_path, capsys):
    root = str(tmp_path / "fm2-rendered")
    _mixed_tree(root)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)

    # Il dominio che la dichiara la porta, come STRINGA (il client accetta solo
    # `typeof === 'string'`; un oggetto verrebbe ignorato).
    disc = doc["domains"][DISC]
    assert disc["projection"] == "stereographic"
    assert isinstance(disc["projection"], str)
    assert disc["projection_meta"] == DISC_META

    # Il dominio Mercator NON ha la chiave: e' la striscia (nessuna invenzione).
    assert "projection" not in doc["domains"][STRIP]
    assert "projection_meta" not in doc["domains"][STRIP]

    # STATO MISTO: la chiave di REGIONE deve essere OMESSA. Se ci fosse, il
    # client la userebbe come fallback per OGNI dominio e disegnerebbe la
    # striscia 2879x521 di dwd_icon come un disco.
    assert "projection" not in doc
    assert "projection_meta" not in doc


def test_domain_projection_omitted_when_the_runs_disagree(tmp_path, capsys):
    """MISURATO in produzione il 2026-10-26 su `ecmwf_ifs025`/`north`.

    La run nuova (`20261026T000000Z`) e' un disco stereografico 1000x1000, la
    precedente (`20261025T000000Z`) e' ancora una striscia Mercator 2879x521.
    `domains.<dominio>.projection` viene applicata dal client a OGNI frame che
    non ne dichiara una: scriverla 'stereographic' faceva disegnare la striscia
    come un disco. La chiave va OMESSA, mentre ogni RUN conserva la propria.
    """
    root = str(tmp_path / "fm2-rendered")
    _write_run(root, DISC, "20261026T000000Z", "2026-10-26T00:00:00Z", HOURS,
               projection=mod.POLAR_PROJECTION, projection_meta=DISC_META)
    _write_run(root, DISC, "20261025T000000Z", "2026-10-25T00:00:00Z", HOURS)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)
    disc = doc["domains"][DISC]
    assert disc["current_run"] == "20261026T000000Z"
    assert "projection" not in disc
    assert "projection_meta" not in disc
    # La regola di REGIONE e' il consenso dei domini: persa la chiave di dominio,
    # con un solo dominio cade anche quella di regione (fix 09f8158b).
    assert "projection" not in doc
    # Le RUN non perdono nulla: ognuna dichiara la propria (o tace = Mercator).
    by_id = {r["run_id"]: r for r in disc["runs"]}
    assert by_id["20261026T000000Z"]["projection"] == "stereographic"
    assert by_id["20261026T000000Z"]["projection_meta"] == DISC_META
    assert "projection" not in by_id["20261025T000000Z"]


def test_every_frame_declares_the_projection_of_its_own_run(tmp_path, capsys):
    """La timeline e' mista: i frame della run vecchia sono Mercator, gli altri disco."""
    root = str(tmp_path / "fm2-rendered")
    old_hours = ["2026-10-25T00:00Z", "2026-10-25T01:00Z",
                 "2026-10-25T02:00Z", "2026-10-25T03:00Z"]
    new_hours = ["2026-10-26T02:00Z", "2026-10-26T03:00Z"]
    _write_run(root, DISC, "20261025T000000Z", "2026-10-25T00:00:00Z", old_hours)
    _write_run(root, DISC, "20261026T000000Z", "2026-10-26T00:00:00Z", new_hours,
               projection=mod.POLAR_PROJECTION, projection_meta=DISC_META)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)
    disc = doc["domains"][DISC]
    timeline = disc["timeline_groups"][disc["variables"]["varX"]["group"]]["timeline"]
    assert len(timeline) == 6
    # OGNI frame porta la proiezione della run che lo ha prodotto, come STRINGA.
    for entry in timeline:
        assert isinstance(entry["projection"], str)
        if entry["run_id"] == "20261026T000000Z":
            assert entry["projection"] == "stereographic"
        else:
            assert entry["projection"] == "mercator"
    # La testa e' della run vecchia, la coda della nuova (merge invariato).
    assert [e["projection"] for e in timeline] == \
        ["mercator", "mercator", "mercator", "mercator", "stereographic", "stereographic"]


def test_uniform_disc_writes_the_domain_key_and_every_frame(tmp_path, capsys):
    root = str(tmp_path / "fm2-rendered")
    for run_id, ref in (("20261026T000000Z", "2026-10-26T00:00:00Z"),
                        ("20261025T000000Z", "2026-10-25T00:00:00Z")):
        _write_run(root, DISC, run_id, ref, HOURS,
                   projection=mod.POLAR_PROJECTION, projection_meta=DISC_META)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)
    disc = doc["domains"][DISC]
    assert disc["projection"] == "stereographic"
    assert disc["projection_meta"] == DISC_META
    assert all(r["projection"] == "stereographic" for r in disc["runs"])
    projections = {e["projection"] for g in disc["timeline_groups"].values()
                   for e in g["timeline"]}
    assert projections == {"stereographic"}


def test_uniform_strip_writes_no_key_but_frames_say_mercator(tmp_path, capsys):
    root = str(tmp_path / "fm2-rendered")
    for run_id, ref in (("20261026T000000Z", "2026-10-26T00:00:00Z"),
                        ("20261025T000000Z", "2026-10-25T00:00:00Z")):
        _write_run(root, DISC, run_id, ref, HOURS)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)
    disc = doc["domains"][DISC]
    assert "projection" not in disc
    projections = {e["projection"] for g in disc["timeline_groups"].values()
                   for e in g["timeline"]}
    assert projections == {"mercator"}


def test_legacy_object_projection_is_split_into_string_and_meta(tmp_path, capsys):
    """Un `projection` OGGETTO (forma dei primi build del disco) viene spezzato."""
    root = str(tmp_path / "fm2-rendered")
    _write_run(root, DISC, "20261026T000000Z", "2026-10-26T00:00:00Z", HOURS,
               projection=dict(DISC_META))
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    disc = _load(out)["domains"][DISC]
    assert disc["projection"] == "stereographic"
    assert isinstance(disc["projection"], str)
    assert disc["projection_meta"] == DISC_META


def test_projection_meta_never_leaks_into_the_string(tmp_path, capsys):
    root = str(tmp_path / "fm2-rendered")
    _all_disc_tree(root)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)
    for value in (doc["projection"], doc["domains"][DISC]["projection"]):
        assert isinstance(value, str) and value == "stereographic"


# --------------------------------------------------------------------------- #
# livello REGIONE
# --------------------------------------------------------------------------- #
def test_region_projection_is_written_when_every_domain_agrees(tmp_path, capsys):
    root = str(tmp_path / "fm2-rendered")
    _all_disc_tree(root)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)
    assert doc["projection"] == "stereographic"
    assert isinstance(doc["projection"], str)
    assert doc["projection_meta"] == DISC_META
    # ... e i domini restano dichiarati uno per uno.
    assert doc["domains"][DISC]["projection"] == "stereographic"
    assert doc["domains"][STRIP]["projection"] == "stereographic"


def test_region_projection_absent_when_projections_differ(tmp_path, capsys):
    root = str(tmp_path / "fm2-rendered")
    _write_run(root, DISC, "20261026T000000Z", "2026-10-26T00:00:00Z", HOURS,
               projection=mod.POLAR_PROJECTION, projection_meta=DISC_META)
    _write_run(root, STRIP, "20261026T000000Z", "2026-10-26T00:00:00Z", HOURS,
               projection="mercator")
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)
    assert "projection" not in doc
    # Le chiavi per dominio invece ci sono entrambe (nessuna e' indovinata).
    assert doc["domains"][DISC]["projection"] == "stereographic"
    assert doc["domains"][STRIP]["projection"] == "mercator"


def test_skipped_domain_does_not_block_the_region_key(tmp_path, capsys):
    """Un dominio senza manifest validi non e' indicizzato: non entra nel test."""
    root = str(tmp_path / "fm2-rendered")
    _all_disc_tree(root)
    broken = os.path.join(root, REGION, "dwd_icon_eu", "runs", "20261026T000000Z",
                          "varX")
    os.makedirs(broken, exist_ok=True)
    with open(os.path.join(broken, "latest.json"), "w", encoding="utf-8") as fh:
        fh.write("{ non e' JSON")
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    captured = capsys.readouterr()
    doc = _load(out)
    assert "dwd_icon_eu" not in doc["domains"]
    assert doc["projection"] == "stereographic"
    assert any("dwd_icon_eu" in item for item in doc.get("skipped", []))


def test_no_projection_anywhere_writes_no_key(tmp_path, capsys):
    """Renderer Mercator (comportamento storico): nessuna chiave aggiunta."""
    root = str(tmp_path / "fm2-rendered")
    _write_run(root, STRIP, "20261026T000000Z", "2026-10-26T00:00:00Z", HOURS)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load(out)
    assert "projection" not in doc
    assert "projection_meta" not in doc
    assert "projection" not in doc["domains"][STRIP]


# --------------------------------------------------------------------------- #
# idempotenza / forma del file
# --------------------------------------------------------------------------- #
def test_projection_is_idempotent(tmp_path, capsys):
    root = str(tmp_path / "fm2-rendered")
    _all_disc_tree(root)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    with open(os.path.join(out, REGION, "timeline.json"), "rb") as fh:
        first = fh.read()
    assert _run_main(root, out) == 0
    out_text = capsys.readouterr().out
    with open(os.path.join(out, REGION, "timeline.json"), "rb") as fh:
        second = fh.read()
    assert first == second
    assert "invariato" in out_text
    doc = json.loads(second.decode("utf-8"))
    assert doc["projection"] == "stereographic"
