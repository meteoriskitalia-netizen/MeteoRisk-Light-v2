# -*- coding: utf-8 -*-
"""Test di ``scripts/forecast_v2/build_timeline_index.py``.

Alberi di test SINTETICI ma reali su disco: nessun mock del modulo sotto test,
nessuna dipendenza di rete, nessun ``.om``. Il modulo viene caricato via
``importlib`` per percorso perche' ``scripts/forecast_v2`` non e' un package.

Il fixture riproduce la struttura del branch dati ``fm2-rendered``
(``<regione>/<dominio>/runs/<run_id>/<variabile>/latest.json`` + ``.webp``) con
TRE run, e i nomi delle cartelle sono scelti in ordine LESSICOGRAFICO INVERSO
rispetto al ``reference_time``: se lo script ordinasse per nome cartella invece
che per ``reference_time`` i test sui ``bucket`` fallirebbero.
"""
import hashlib
import importlib.util
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MODULE_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                           "build_timeline_index.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("build_timeline_index_under_test",
                                                  MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()

REGION = "italia"
DOMAIN = "domA"

# Cartella -> reference_time. L'ordine delle cartelle (crescente) e' 0101, 0102,
# 0103; l'ordine reale delle run e' 0103 (piu' recente) ... 0101 (piu' vecchia).
RUN_NEWEST = "20261001T000000Z"   # reference_time 2026-10-03 -> bucket 0
RUN_MIDDLE = "20261002T000000Z"   # reference_time 2026-10-02 -> bucket 1
RUN_OLDEST = "20261003T000000Z"   # reference_time 2026-10-01 -> bucket 2
RUN_BROKEN = "20261004T000000Z"   # manifest illeggibile -> scartata

REF_NEWEST = "2026-10-03T00:00:00Z"
REF_MIDDLE = "2026-10-02T00:00:00Z"
REF_OLDEST = "2026-10-01T00:00:00Z"
GENERATED_AT = "2026-10-04T06:00:00Z"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _stamp(valid_time):
    """Convenzione del renderer (usata SOLO per costruire il fixture)."""
    return valid_time.replace(":", "").replace("Z", "")


def _hours(first, count):
    """``count`` valid_time orari a partire da ``first`` (formato Z)."""
    import datetime as dt
    base = dt.datetime.strptime(first, "%Y-%m-%dT%H:%MZ")
    out = []
    for step in range(count):
        out.append((base + dt.timedelta(hours=step)).strftime("%Y-%m-%dT%H:%MZ"))
    return out


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle)


def _write_run(root, run_id, reference_time, frames_by_var, expected_frames=None,
               generated_at=GENERATED_AT, files_by_var=None):
    """Crea ``<root>/<REGION>/<DOMAIN>/runs/<run_id>/<var>/`` con webp + manifest."""
    run_dir = os.path.join(root, REGION, DOMAIN, "runs", run_id)
    for var, valid_times in frames_by_var.items():
        var_dir = os.path.join(run_dir, var)
        os.makedirs(var_dir, exist_ok=True)
        frames = []
        for index, valid_time in enumerate(valid_times):
            if files_by_var and var in files_by_var:
                name = files_by_var[var][index]
            else:
                name = _stamp(valid_time) + ".webp"
            with open(os.path.join(var_dir, name), "wb") as handle:
                handle.write(b"RIFF\x00\x00\x00\x00WEBPfixture")
            frames.append({"file": name, "valid_time": valid_time, "bytes": 18})
        manifest = {
            "version": "fm2-webp-1",
            "region": REGION,
            "domain": DOMAIN,
            "variable": var,
            "theme": "light",
            "unit": "degC",
            "bounds": [[36.5, 6.5], [47.2, 18.6]],
            "grid": {"nx": 10, "ny": 12},
            "frames": frames,
            "valid_times": list(valid_times),
        }
        if reference_time is not None:
            manifest["reference_time"] = reference_time
            manifest["run_id"] = run_id.replace("-", "")
        if generated_at is not None:
            manifest["generated_at"] = generated_at
        if expected_frames is not None:
            manifest["expected_frames"] = expected_frames
        _write_json(os.path.join(var_dir, "latest.json"), manifest)
    return run_dir


@pytest.fixture
def tree(tmp_path):
    """Tre run valide + una rotta sulla regione ``italia`` / dominio ``domA``.

    - ``RUN_OLDEST`` (ref 2026-10-01): varX con 09-30T22,23 e 10-01T00,01.
    - ``RUN_MIDDLE`` (ref 2026-10-02): varX orario 10-01T00 .. 10-02T06 (31
      frame) ma ``expected_frames`` 40 -> run INCOMPLETA.
    - ``RUN_NEWEST`` (ref 2026-10-03): varX orario 10-01T09 .. 10-01T20 (12),
      varY orario 10-01T09 .. 10-01T14 (6), varZ senza convenzione di nome
      (``special-0001.webp``).
    - ``RUN_BROKEN`` (ref 2026-10-04): l'unico manifest e' JSON invalido.
    """
    root = str(tmp_path / "fm2-rendered")
    _write_run(root, RUN_OLDEST, REF_OLDEST,
               {"varX": ["2026-09-30T22:00Z", "2026-09-30T23:00Z",
                         "2026-10-01T00:00Z", "2026-10-01T01:00Z"],
                "varW": ["2026-09-30T22:00Z", "2026-09-30T23:00Z",
                         "2026-10-01T00:00Z", "2026-10-01T01:00Z"]})
    middle_frames = _hours("2026-10-01T00:00Z", 31)
    _write_run(root, RUN_MIDDLE, REF_MIDDLE,
               {"varX": middle_frames, "varW": middle_frames},
               expected_frames=40)
    newest_frames = _hours("2026-10-01T09:00Z", 12)
    _write_run(root, RUN_NEWEST, REF_NEWEST,
               {"varX": newest_frames,
                "varW": newest_frames,
                "varY": _hours("2026-10-01T09:00Z", 6),
                "varZ": ["2026-10-01T09:00Z"]},
               files_by_var={"varZ": ["special-0001.webp"]})
    broken_dir = os.path.join(root, REGION, DOMAIN, "runs", RUN_BROKEN, "broken")
    os.makedirs(broken_dir, exist_ok=True)
    with open(os.path.join(broken_dir, "latest.json"), "w", encoding="utf-8") as handle:
        handle.write("{ questo non e' JSON")
    return root


def _run_main(root, out, *extra, keep_past="100000"):
    """Esegue ``main`` sulla fixture.

    Il default ``keep_past="100000"`` (K enorme) DISATTIVA la potatura della
    testa: i test storici misurano il merge (bucket, coda, fallback), non il
    taglio. I test della potatura passano ``keep_past`` esplicito; il default
    VERO del CLI (3 ore) e' verificato da
    ``test_default_keep_past_hours_is_three``.
    """
    return mod.main(["--root", root, "--out-root", out,
                     "--now", "2026-10-24T00:00:00Z",
                     "--keep-past-hours", keep_past] + list(extra))


def _load_timeline(out, region=REGION):
    with open(os.path.join(out, region, "timeline.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _entries(document, domain=DOMAIN, var="varX"):
    """Timeline della variabile ``var`` risolta attraverso il gruppo."""
    doc = document["domains"][domain]
    group = doc["variables"][var]["group"]
    return doc["timeline_groups"][group]["timeline"]


# --------------------------------------------------------------------------- #
# (a)(b)(c)(d)(e)(f) — merge su tre run
# --------------------------------------------------------------------------- #
def test_timeline_merges_three_runs(tree, tmp_path, capsys):
    out = str(tmp_path / "out")
    assert _run_main(tree, out) == 0
    document = _load_timeline(out)
    capsys.readouterr()

    # --- document -------------------------------------------------------
    assert document["version"] == mod.TIMELINE_VERSION
    assert document["region"] == REGION
    assert document["generated_at"] == "2026-10-24T00:00:00Z"
    assert document["file_base"] == "data/fm2-rendered/italia/"
    assert document["file_template"] == mod.FILE_TEMPLATE
    assert set(document["domains"]) == {DOMAIN}

    doc = document["domains"][DOMAIN]
    # (c) bucket = eta' della run, determinata dal reference_time del manifest e
    #     NON dal nome della cartella (che qui e' in ordine inverso).
    assert [r["run_id"] for r in doc["runs"]] == [RUN_NEWEST, RUN_MIDDLE, RUN_OLDEST]
    assert [r["bucket"] for r in doc["runs"]] == [0, 1, 2]
    assert [r["reference_time"] for r in doc["runs"]] == [REF_NEWEST, REF_MIDDLE,
                                                          REF_OLDEST]
    assert {r["reference_time_source"] for r in doc["runs"]} == {"reference_time"}
    assert doc["current_run"] == RUN_NEWEST
    # RUN_MIDDLE dichiara 40 frame attesi e ne ha 31 -> incompleta.
    middle = [r for r in doc["runs"] if r["run_id"] == RUN_MIDDLE][0]
    assert middle["completed"] is False
    assert (middle["expected_frames"], middle["frames"]) == (40, 31)
    assert [r for r in doc["runs"] if r["run_id"] == RUN_NEWEST][0]["completed"] is True
    # La run col manifest rotto non entra, ma viene DICHIARATA.
    assert RUN_BROKEN not in [r["run_id"] for r in doc["runs"]]
    assert any(RUN_BROKEN in item for item in document.get("skipped", []))

    timeline = _entries(document)
    by_time = {entry["valid_time"]: entry for entry in timeline}
    assert len(by_time) == len(timeline), "valid_time duplicato nella timeline"

    # (a) il run nuovo vince sull'overlap 10-01T09..20 (anche il medio li ha).
    for valid_time in _hours("2026-10-01T09:00Z", 12):
        assert by_time[valid_time]["run_id"] == RUN_NEWEST
        assert by_time[valid_time]["bucket"] == 0
    # ... e il medio resta padrone di 10-01T00..08, che il nuovo non copre.
    for valid_time in _hours("2026-10-01T00:00Z", 9):
        assert by_time[valid_time]["run_id"] == RUN_MIDDLE
        assert by_time[valid_time]["bucket"] == 1

    # (b) la CODA viene dal run precedente: il nuovo finisce a 10-01T20, dopo
    #     di lui il medio copre fino a 10-02T06.
    assert by_time["2026-10-01T20:00Z"]["run_id"] == RUN_NEWEST
    for valid_time in _hours("2026-10-01T21:00Z", 10):
        assert by_time[valid_time]["run_id"] == RUN_MIDDLE
        assert by_time[valid_time]["bucket"] == 1
    assert timeline[-1]["valid_time"] == "2026-10-02T06:00Z"
    assert timeline[-1]["run_id"] == RUN_MIDDLE

    # (d) un valid_time che esiste SOLO nella run piu' vecchia entra comunque.
    for valid_time in ("2026-09-30T22:00Z", "2026-09-30T23:00Z"):
        assert valid_time in by_time
        assert by_time[valid_time]["run_id"] == RUN_OLDEST
        assert by_time[valid_time]["bucket"] == 2
    assert timeline[0]["valid_time"] == "2026-09-30T22:00Z"
    assert len(timeline) == 33            # 2 + 9 + 12 + 10

    # (e) ordine cronologico stretto (e nessun buco: cadenza oraria continua).
    stamps = [entry["valid_time"] for entry in timeline]
    assert stamps == sorted(stamps)
    assert [entry["valid_time"] for entry in timeline] == sorted(
        by_time, key=lambda value: mod.parse_dt(value))
    assert "gaps" not in doc["timeline_groups"][doc["variables"]["varX"]["group"]]

    # (f) nessun file inventato: ogni (run, variabile, file) esiste su disco.
    for var, entry in doc["variables"].items():
        group = doc["timeline_groups"][entry["group"]]
        assert var in group["variables"]
        for item in group["timeline"]:
            path = os.path.join(tree, REGION, DOMAIN, "runs", item["run_id"],
                                var, item["file"])
            assert os.path.isfile(path), "file inesistente dichiarato: %s" % path
    assert document["summary"]["files_missing"] == 0
    assert document["summary"]["files_verified"] is False  # non richiesto


def test_variables_get_independent_groups(tree, tmp_path, capsys):
    out = str(tmp_path / "out")
    assert _run_main(tree, out) == 0
    document = _load_timeline(out)
    capsys.readouterr()
    doc = document["domains"][DOMAIN]

    varx_group = doc["variables"]["varX"]["group"]
    vary_group = doc["variables"]["varY"]["group"]
    varz_group = doc["variables"]["varZ"]["group"]
    assert varx_group != vary_group
    assert varz_group not in (varx_group, vary_group)

    # varW ha la stessa sequenza di varX: stesso gruppo, un solo array timeline.
    assert doc["variables"]["varW"]["group"] == varx_group
    assert doc["timeline_groups"][varx_group]["variables"] == ["varW", "varX"]

    vary = doc["timeline_groups"][vary_group]
    assert vary["variables"] == ["varY"]
    assert vary["frames"] == 6
    assert {entry["run_id"] for entry in vary["timeline"]} == {RUN_NEWEST}
    assert [entry["valid_time"] for entry in vary["timeline"]] == \
        _hours("2026-10-01T09:00Z", 6)

    # varZ: il nome del file NON e' derivato dal valid_time, viene dal manifest.
    varz = doc["timeline_groups"][varz_group]
    assert varz["frames"] == 1
    assert varz["timeline"][0]["file"] == "special-0001.webp"
    assert os.path.isfile(os.path.join(tree, REGION, DOMAIN, "runs", RUN_NEWEST,
                                       "varZ", "special-0001.webp"))

    assert doc["variables"]["varX"]["frames"] == 33
    assert doc["variables"]["varX"]["by_run"] == {RUN_NEWEST: 12, RUN_MIDDLE: 19,
                                                   RUN_OLDEST: 2}
    assert document["summary"]["timeline_frames"] == \
        sum(group["frames"] for group in doc["timeline_groups"].values())
    assert document["summary"]["variable_frames"] == \
        sum(entry["frames"] for entry in doc["variables"].values())
    # varW + varX condividono 33 frame: 73 "viste per variabile" contro 40
    # effettivamente scritti.
    assert document["summary"]["variable_frames"] == 33 * 2 + 6 + 1
    assert document["summary"]["timeline_frames"] == 40
    assert document["summary"]["variable_frames"] > document["summary"]["timeline_frames"]


def test_flat_runs_carry_the_domain(tree, tmp_path, capsys):
    out = str(tmp_path / "out")
    assert _run_main(tree, out) == 0
    document = _load_timeline(out)
    capsys.readouterr()
    assert [r["run_id"] for r in document["runs"]] == [RUN_NEWEST, RUN_MIDDLE,
                                                       RUN_OLDEST]
    assert {r["domain"] for r in document["runs"]} == {DOMAIN}
    assert [r["bucket"] for r in document["runs"]] == [0, 1, 2]
    assert all("domain" not in r for r in document["domains"][DOMAIN]["runs"])


# --------------------------------------------------------------------------- #
# reference_time vs nome cartella, fallback, max-runs
# --------------------------------------------------------------------------- #
def test_missing_reference_time_falls_back_to_run_id(tmp_path, capsys):
    root = str(tmp_path / "fm2-rendered")
    run_dir = "20261005T120000Z"
    _write_run(root, run_dir, None, {"varX": ["2026-10-05T12:00Z"]},
               generated_at=None)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load_timeline(out)["domains"][DOMAIN]
    assert doc["runs"][0]["reference_time_source"] == "run_id"
    assert doc["runs"][0]["reference_time"] == "2026-10-05T12:00Z"
    assert doc["runs"][0]["bucket"] == 0


def test_max_runs_keeps_the_two_newest(tree, tmp_path, capsys):
    out = str(tmp_path / "out")
    assert _run_main(tree, out, "--max-runs", "2") == 0
    capsys.readouterr()
    document = _load_timeline(out)
    doc = document["domains"][DOMAIN]
    assert [r["run_id"] for r in doc["runs"]] == [RUN_NEWEST, RUN_MIDDLE]
    stamps = {entry["valid_time"] for entry in _entries(document)}
    # La run piu' vecchia e' fuori: 09-30T22,23 non esistono piu' da nessuna parte.
    assert "2026-09-30T22:00Z" not in stamps
    assert "2026-10-02T06:00Z" in stamps
    assert document["summary"]["runs"] == 2


# --------------------------------------------------------------------------- #
# buchi: non si inventa nulla e non si dichiara nulla che non sia dimostrabile
# --------------------------------------------------------------------------- #
def test_hole_is_not_filled_and_the_deficit_stays_on_the_run(tmp_path, capsys):
    """Un ``valid_time`` assente da tutte le run non entra nella timeline.

    Il campo ``gaps`` non esiste (vedi docstring del modulo: sulla cadenza
    reale, variabile, il rilevatore produceva centinaia di falsi positivi). La
    carenza dichiarabile e' quella del singolo run: ``expected_frames`` >
    ``frames`` -> ``completed: false``.
    """
    root = str(tmp_path / "fm2-rendered")
    _write_run(root, RUN_NEWEST, REF_NEWEST,
               {"varX": ["2026-10-01T00:00Z", "2026-10-01T01:00Z",
                         "2026-10-01T03:00Z", "2026-10-01T04:00Z"]},
               expected_frames=5)
    out = str(tmp_path / "out")
    assert _run_main(root, out) == 0
    capsys.readouterr()
    doc = _load_timeline(out)["domains"][DOMAIN]
    group = doc["timeline_groups"][doc["variables"]["varX"]["group"]]
    assert [entry["valid_time"] for entry in group["timeline"]] == [
        "2026-10-01T00:00Z", "2026-10-01T01:00Z",
        "2026-10-01T03:00Z", "2026-10-01T04:00Z"]
    assert "gaps" not in group and "gaps_truncated" not in group
    run = doc["runs"][0]
    assert (run["expected_frames"], run["frames"], run["completed"]) == (5, 4, False)


def test_no_gaps_field_anywhere(tree, tmp_path, capsys):
    out = str(tmp_path / "out")
    assert _run_main(tree, out) == 0
    capsys.readouterr()
    doc = _load_timeline(out)["domains"][DOMAIN]
    for group in doc["timeline_groups"].values():
        assert "gaps" not in group
        assert set(group) == {"variables", "frames", "timeline"}


# --------------------------------------------------------------------------- #
# verifica dei file
# --------------------------------------------------------------------------- #
def test_check_files_passes_and_counts(tree, tmp_path, capsys):
    out = str(tmp_path / "out")
    assert _run_main(tree, out, "--check-files") == 0
    capsys.readouterr()
    document = _load_timeline(out)
    summary = document["summary"]
    assert summary["files_verified"] is True
    assert summary["files_missing"] == 0
    # Un frame per ogni (gruppo, variabile, voce): i gruppi replicano lo stesso
    # file su ogni variabile che vi appartiene.
    expected = sum(len(group["timeline"]) * len(group["variables"])
                   for group in document["domains"][DOMAIN]["timeline_groups"].values())
    assert summary["files_checked"] == expected


def test_check_files_drops_missing_frame_and_falls_back(tree, tmp_path, capsys):
    """Un frame dichiarato ma assente NON viene citato: vince la run precedente."""
    victim = os.path.join(tree, REGION, DOMAIN, "runs", RUN_NEWEST, "varX",
                          _stamp("2026-10-01T12:00Z") + ".webp")
    os.remove(victim)
    out = str(tmp_path / "out")
    assert _run_main(tree, out, "--check-files") == 0
    err = capsys.readouterr().err
    assert "NON esistono" in err and "2026-10-01T1200.webp" in err
    document = _load_timeline(out)
    assert document["summary"]["files_verified"] is True
    assert document["summary"]["files_missing"] == 1
    dropped = document["dropped"]
    assert len(dropped) == 1 and dropped[0]["valid_time"] == "2026-10-01T12:00Z"
    assert dropped[0]["run_id"] == RUN_NEWEST and dropped[0]["variable"] == "varX"
    # Il valid_time resta nella timeline, ma passa alla run che il file ce l'ha.
    timeline = {entry["valid_time"]: entry for entry in _entries(document)}
    assert timeline["2026-10-01T12:00Z"]["run_id"] == RUN_MIDDLE
    assert timeline["2026-10-01T12:00Z"]["bucket"] == 1
    for item in _entries(document):
        path = os.path.join(tree, REGION, DOMAIN, "runs", item["run_id"], "varX",
                            item["file"])
        assert os.path.isfile(path)


def test_fail_on_missing_writes_nothing(tree, tmp_path, capsys):
    os.remove(os.path.join(tree, REGION, DOMAIN, "runs", RUN_NEWEST, "varX",
                           _stamp("2026-10-01T12:00Z") + ".webp"))
    out = str(tmp_path / "out")
    assert _run_main(tree, out, "--check-files", "--fail-on-missing") == 3
    capsys.readouterr()
    assert not os.path.exists(os.path.join(out, REGION, "timeline.json"))


def test_path_list_replaces_stat_for_partial_checkouts(tree, tmp_path, capsys):
    """Una lista di path (es. ``git ls-tree -r --name-only``) vale come il disco."""
    inventory = []
    region_dir = os.path.join(tree, REGION)
    for dirpath, _dirnames, filenames in os.walk(region_dir):
        for name in filenames:
            full = os.path.join(dirpath, name)
            inventory.append(os.path.relpath(full, region_dir).replace(os.sep, "/"))
    listing = tmp_path / "paths.txt"
    listing.write_text("\n".join(inventory) + "\n", encoding="utf-8")

    out = str(tmp_path / "out")
    assert _run_main(tree, out, "--path-list", str(listing)) == 0
    capsys.readouterr()
    assert _load_timeline(out)["summary"]["files_verified"] is True

    # Si toglie un frame CHE LA TIMELINE CITA (non un latest.json qualsiasi).
    cited = _entries(_load_timeline(out))[12]
    victim = "%s/runs/%s/varX/%s" % (DOMAIN, cited["run_id"], cited["file"])
    assert victim in inventory

    # Una lista incompleta non produce falsi positivi: il frame cade e si dichiara.
    partial = tmp_path / "partial.txt"
    partial.write_text("\n".join(item for item in inventory if item != victim) + "\n",
                       encoding="utf-8")
    out2 = str(tmp_path / "out2")
    assert _run_main(tree, out2, "--path-list", str(partial)) == 0
    capsys.readouterr()
    document2 = _load_timeline(out2)
    assert document2["summary"]["files_missing"] == 1
    assert document2["dropped"][0]["file"] == cited["file"]
    out3 = str(tmp_path / "out3")
    assert _run_main(tree, out3, "--path-list", str(partial),
                     "--fail-on-missing") == 3
    capsys.readouterr()
    assert not os.path.exists(os.path.join(out3, REGION, "timeline.json"))


# --------------------------------------------------------------------------- #
# additivita', determinismo, exit code
# --------------------------------------------------------------------------- #
def test_index_json_is_never_touched(tree, tmp_path, capsys):
    index_path = os.path.join(tree, REGION, "index.json")
    _write_json(index_path, {"version": "fm2-webp-1", "region": REGION,
                             "domains": {}})
    before = hashlib.sha256(open(index_path, "rb").read()).hexdigest()
    out = str(tmp_path / "out")
    assert _run_main(tree, out, "--check-files") == 0
    capsys.readouterr()
    after = hashlib.sha256(open(index_path, "rb").read()).hexdigest()
    assert before == after
    # Il timeline.json va in --out-root, non nella cartella della regione.
    assert not os.path.exists(os.path.join(tree, REGION, "timeline.json"))
    assert os.path.isfile(os.path.join(out, REGION, "timeline.json"))


def test_second_run_with_same_now_is_byte_identical(tree, tmp_path, capsys):
    out = str(tmp_path / "out")
    assert _run_main(tree, out) == 0
    capsys.readouterr()
    path = os.path.join(out, REGION, "timeline.json")
    first = open(path, "rb").read()
    assert _run_main(tree, out) == 0
    second_out = capsys.readouterr().out
    assert "invariato" in second_out
    assert open(path, "rb").read() == first


def test_dry_run_writes_nothing(tree, tmp_path, capsys):
    out = str(tmp_path / "out")
    assert _run_main(tree, out, "--dry-run") == 0
    assert not os.path.exists(os.path.join(out, REGION, "timeline.json"))
    assert "dry-run" in capsys.readouterr().out


def test_exit_codes_for_bad_root_and_empty_tree(tmp_path, capsys):
    assert mod.main(["--root", str(tmp_path / "nope")]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert mod.main(["--root", str(empty)]) == 1
    assert mod.main(["--root", str(empty), "--regions", "italia"]) == 2
    capsys.readouterr()


def test_regions_filter_selects_one_region(tree, tmp_path, capsys):
    # Una seconda regione SENZA run valide non deve produrre un file.
    other = os.path.join(tree, "europa", "domA", "runs", "x", "varX")
    os.makedirs(other)
    with open(os.path.join(other, "latest.json"), "w", encoding="utf-8") as handle:
        handle.write("{}")
    out = str(tmp_path / "out")
    assert _run_main(tree, out, "--regions", REGION) == 0
    capsys.readouterr()
    assert os.path.isfile(os.path.join(out, REGION, "timeline.json"))
    assert not os.path.exists(os.path.join(out, "europa", "timeline.json"))


# --------------------------------------------------------------------------- #
# potatura della testa (--keep-past-hours, default 3)
#
# Il difetto misurato in produzione il 2026-10-25: la timeline cominciava nel
# passato (fino a 55 h) perche' il merge non puo' rimpiazzare i valid_time
# anteriori al reference_time della run piu' nuova. Questi test coprono (a)-(f)
# della richiesta + il default VERO del CLI + il rifiuto di K negativo.
# --------------------------------------------------------------------------- #
TRIM_CURRENT = "20261024T000000Z"   # run corrente, reference_time 2026-10-24T00Z
TRIM_OLD = "20261022T000000Z"       # run precedente, reference_time 2026-10-22T00Z
TRIM_REF = "2026-10-24T00:00:00Z"
TRIM_OLD_REF = "2026-10-22T00:00:00Z"
CUTOFF_3 = "2026-10-23T21:00Z"      # TRIM_REF - 3 h
CUTOFF_0 = "2026-10-24T00:00Z"      # TRIM_REF - 0 h


def _trim_fixture(tmp_path, current_frames, old_frames, var="varX"):
    """Run corrente + run precedente, con la testa vecchia nella run precedente."""
    root = str(tmp_path / "fm2-rendered")
    _write_run(root, TRIM_CURRENT, TRIM_REF, {var: current_frames})
    _write_run(root, TRIM_OLD, TRIM_OLD_REF, {var: old_frames})
    return root


def test_head_trim_cuts_a_55_hour_old_head(tmp_path, capsys):
    """(a) testa vecchia di 55 h -> potata a K=3."""
    # 21/17 .. 24/03 (59 frame): il primo valid_time e' 55 h prima della run.
    old = _hours("2026-10-21T17:00Z", 59)
    current = _hours("2026-10-24T00:00Z", 6)   # 24/00 .. 24/05
    root = _trim_fixture(tmp_path, current, old)
    out = str(tmp_path / "out")
    assert _run_main(root, out, keep_past="3") == 0
    capsys.readouterr()
    document = _load_timeline(out)
    doc = document["domains"][DOMAIN]

    assert doc["head_trim"] == {"applied": True, "cutoff": CUTOFF_3,
                                "dropped_frames": 52,
                                "reason": "keep_past_hours"}
    timeline = _entries(document)
    assert timeline[0]["valid_time"] == CUTOFF_3
    assert timeline[0]["run_id"] == TRIM_OLD and timeline[0]["bucket"] == 1
    assert [entry["valid_time"] for entry in timeline][-1] == "2026-10-24T05:00Z"
    assert len(timeline) == 9            # 23/21..23/23 (3) + 24/00..24/05 (6)
    assert doc["variables"]["varX"]["frames"] == 9
    assert doc["variables"]["varX"]["by_run"] == {TRIM_OLD: 3, TRIM_CURRENT: 6}
    assert document["summary"]["head_trim_dropped"] == 52


def test_keep_past_zero_cuts_exactly_at_reference_time(tmp_path, capsys):
    """(b) K=0 -> taglio esatto a reference_time (nessuna tolleranza)."""
    old = _hours("2026-10-21T17:00Z", 59)
    current = _hours("2026-10-24T00:00Z", 6)
    root = _trim_fixture(tmp_path, current, old)
    out = str(tmp_path / "out")
    assert _run_main(root, out, keep_past="0") == 0
    capsys.readouterr()
    document = _load_timeline(out)
    doc = document["domains"][DOMAIN]
    assert doc["head_trim"]["cutoff"] == CUTOFF_0
    assert doc["head_trim"]["dropped_frames"] == 55
    timeline = _entries(document)
    assert timeline[0]["valid_time"] == CUTOFF_0
    assert len(timeline) == 6
    assert doc["variables"]["varX"]["by_run"] == {TRIM_CURRENT: 6}


def test_tail_from_the_previous_run_is_untouched(tmp_path, capsys):
    """(c) la CODA (frame dopo la fine della run nuova) resta intatta."""
    # La run nuova copre solo 24/00..24/02; la coda 24/03..24/08 viene dalla
    # run precedente, che copre anche una testa vecchia (21/17..).
    old = _hours("2026-10-21T17:00Z", 64)       # 21/17 .. 24/08
    current = _hours("2026-10-24T00:00Z", 3)    # 24/00 .. 24/02
    root = _trim_fixture(tmp_path, current, old)
    out = str(tmp_path / "out")
    assert _run_main(root, out, keep_past="3") == 0
    capsys.readouterr()
    document = _load_timeline(out)
    doc = document["domains"][DOMAIN]
    timeline = _entries(document)

    assert doc["head_trim"]["dropped_frames"] == 52
    assert timeline[0]["valid_time"] == CUTOFF_3
    assert timeline[-1]["valid_time"] == "2026-10-24T08:00Z"
    assert timeline[-1]["run_id"] == TRIM_OLD and timeline[-1]["bucket"] == 1
    tail = [entry for entry in timeline
            if mod.parse_dt(entry["valid_time"]) >= mod.parse_dt("2026-10-24T03:00Z")]
    assert [entry["valid_time"] for entry in tail] == \
        _hours("2026-10-24T03:00Z", 6)
    # La coda e' della run PRECEDENTE: la potatura non l'ha toccata.
    assert {entry["run_id"] for entry in tail} == {TRIM_OLD}
    assert len(timeline) == 12       # 23/21..23/23 + 24/00..24/08


def test_no_current_run_disables_the_trim(tmp_path, capsys):
    """(d) nessuna run con reference_time -> nessuna potatura, dichiarata."""
    root = str(tmp_path / "fm2-rendered")
    frames = _hours("2026-10-21T17:00Z", 4)
    # run_id non parsabile + nessun reference_time/generated_at nel manifest.
    _write_run(root, "run-alpha", None, {"varX": frames}, generated_at=None)
    out = str(tmp_path / "out")
    assert _run_main(root, out, keep_past="3") == 0
    capsys.readouterr()
    document = _load_timeline(out)
    doc = document["domains"][DOMAIN]
    assert doc["head_trim"] == {"applied": False, "cutoff": None,
                                "dropped_frames": 0,
                                "reason": "no_current_run"}
    assert [entry["valid_time"] for entry in _entries(document)] == frames
    assert document["summary"]["head_trim_dropped"] == 0


def test_variable_emptied_by_the_trim_loses_its_group(tmp_path, capsys):
    """(e) gruppo svuotato -> rimosso; la variabile ricade su index.json."""
    root = str(tmp_path / "fm2-rendered")
    _write_run(root, TRIM_CURRENT, TRIM_REF,
               {"varX": _hours("2026-10-20T00:00Z", 4),     # tutta nel passato
                "varY": _hours("2026-10-24T00:00Z", 3)})    # tutta valida
    out = str(tmp_path / "out")
    assert _run_main(root, out, keep_past="3") == 0
    capsys.readouterr()
    document = _load_timeline(out)
    doc = document["domains"][DOMAIN]

    assert doc["variables"]["varX"] == {"group": None, "frames": 0,
                                        "by_run": {},
                                        "head_trimmed_frames": 4,
                                        "fallback": "index.json"}
    groups = doc["timeline_groups"]
    assert len(groups) == 1
    group = list(groups.values())[0]
    assert group["variables"] == ["varY"]
    assert group["frames"] == 3
    assert all(group["frames"] > 0 for group in groups.values())
    assert all("varX" not in group["variables"] for group in groups.values())
    assert doc["head_trim"]["dropped_frames"] == 4
    # La variabile resta citata (non sparisce) e il dominio resta nel file.
    assert document["summary"]["variables"] == 2
    assert document["summary"]["head_trim_dropped"] == 4
    assert _entries(document, var="varY")[0]["valid_time"] == "2026-10-24T00:00Z"


def test_large_keep_past_hours_does_not_trim(tmp_path, capsys):
    """(f) K grande -> nessun taglio (la testa resta quella di prima)."""
    old = _hours("2026-10-21T17:00Z", 3)
    current = _hours("2026-10-24T00:00Z", 2)
    root = _trim_fixture(tmp_path, current, old)
    out = str(tmp_path / "out")
    assert _run_main(root, out, keep_past="100000") == 0
    capsys.readouterr()
    document = _load_timeline(out)
    doc = document["domains"][DOMAIN]
    trim = doc["head_trim"]
    assert trim["applied"] is True and trim["reason"] == "keep_past_hours"
    assert trim["dropped_frames"] == 0
    assert mod.parse_dt(trim["cutoff"]) == (
        mod.parse_dt(TRIM_REF) - mod._dt.timedelta(hours=100000))
    timeline = _entries(document)
    assert timeline[0]["valid_time"] == "2026-10-21T17:00Z"
    assert len(timeline) == 5


def test_default_keep_past_hours_is_three(tmp_path, capsys):
    """Senza il flag vale K=3: e' il comportamento che va in produzione."""
    assert mod.parse_args([]).keep_past_hours == 3.0
    old = _hours("2026-10-21T17:00Z", 59)
    current = _hours("2026-10-24T00:00Z", 6)
    root = _trim_fixture(tmp_path, current, old)
    out = str(tmp_path / "out")
    assert mod.main(["--root", root, "--out-root", out,
                     "--now", "2026-10-24T00:00:00Z"]) == 0
    capsys.readouterr()
    doc = _load_timeline(out)["domains"][DOMAIN]
    assert doc["head_trim"]["cutoff"] == CUTOFF_3
    assert doc["head_trim"]["dropped_frames"] == 52


def test_negative_keep_past_hours_is_rejected(tmp_path, capsys):
    old = _hours("2026-10-21T17:00Z", 3)
    current = _hours("2026-10-24T00:00Z", 2)
    root = _trim_fixture(tmp_path, current, old)
    out = str(tmp_path / "out")
    assert _run_main(root, out, keep_past="-1") == 2
    capsys.readouterr()
    assert not os.path.exists(os.path.join(out, REGION, "timeline.json"))
