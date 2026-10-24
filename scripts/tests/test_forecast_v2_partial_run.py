# -*- coding: utf-8 -*-
"""RUN PARZIALE (P1) del motore V2: scoperta del run in corso + prefisso di frame.

Cosa coprono questi test (nessuna rete: tutte le funzioni I/O sono sostituite):

  (a) flag OFF = comportamento STORICO: un frame mancante viene SALTATO (buco nel
      manifest: `valid_times` piu' lungo di `frames`), e `discover_partial_run`
      non viene MAI chiamata;
  (b) flag ON con run parziale = si FERMA al primo buco, non salta: `valid_times`
      pubblicato e' il PREFISSO scritto (len == len(frames)), i mancanti sono
      dichiarati in `partial.missing`;
  (c) flag ON con tutti i frame = identico al percorso storico (stesse
      `valid_times`, stessi `frames`), piu' i soli campi di provenienza;
  (d) guard NO-OP: con il run ancora `completed: false` il dominio NON viene
      saltato e riparte quando arrivano nuovi frame; quando il run e' completo e
      `valid_times` non cambiano, il NO-OP torna a funzionare;
  (e) scoperta del run: `in-progress.json` come fonte primaria, listing del
      prefix + `<run>/meta.json` come fallback, None quando non c'e' nulla di
      piu' recente di `latest.json`;
  (f) soglia minima: sotto `--partial-min-frames` il dominio fallisce e i `.om`
      scritti vengono rimossi (nessun file orfano, nessun `latest.json` nuovo).

Il motore viene caricato via `importlib` per percorso (stesso pattern di
`test_forecast_v2_derived.py`): `scripts/forecast_v2` non e' un package.
"""
import argparse
import importlib.util
import json
import os

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("omfiles")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ENGINE_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                           "forecast_v2_engine.py")

DOMAIN = "test_domain"
WKT = ('GEOGCRS["test",DATUM["t",ELLIPSOID["e",6371229,0]],'
       'CS[ellipsoidal,2],BBOX[36,6,47,18]]')
# 24x24 su BBOX[36,6,47,18]: dx = 12/23, dy = 11/23 (griglia valida, crop Italia
# interamente dentro il dominio -> nessun clamp che azzera il crop).
SHAPE = (24, 24)

VALID = ["2026-10-24T12:00Z", "2026-10-24T13:00Z",
         "2026-10-24T14:00Z", "2026-10-24T15:00Z"]


def _load_engine():
    spec = importlib.util.spec_from_file_location(
        "forecast_v2_engine_partial_ut", ENGINE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENGINE = _load_engine()


# ---------------------------------------------------------------------------
# Fixture / doppi di rete
# ---------------------------------------------------------------------------
def _payload(ref, valid, completed):
    return {
        "reference_time": ref,
        "valid_times": list(valid),
        "completed": completed,
        "crs_wkt": WKT,
        "last_modified_time": "2026-10-24T12:30:00Z",
        "variables": {},
    }


def _args(tmp_path, **over):
    base = {
        "valid_frames": 0,
        "force": False,
        "progress_manifest": "",
        "out": str(tmp_path / "out"),
        "no_range": False,
        "no_prune": False,
        "allow_partial_run": False,
        "partial_min_frames": 3,
        "partial_probe_hours": 12,
    }
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture
def engine(monkeypatch):
    """Sostituisce SOLO le funzioni I/O; la logica sotto test resta quella vera."""
    monkeypatch.setattr(ENGINE, "probe_frame_shape", lambda *a, **k: (SHAPE, "range"))
    monkeypatch.setattr(ENGINE, "http_status", lambda *a, **k: 404)
    monkeypatch.setattr(ENGINE, "_urlopen", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("nessuna richiesta di rete attesa in questo test")))
    return ENGINE


def _install_frames(monkeypatch, present, written=None):
    """`present` = set di valid_time ottenibili; registra i frame scritti."""
    written = written if written is not None else []
    by_name = {ENGINE.valid_time_to_filename(v): v for v in VALID}

    def fake_load_frame(url, domain, vname, cache_dir, frames_cache, risk_vars,
                        grid, lats, lons, idx, dl, allow_range=True):
        if by_name.get(vname) not in present:
            return None
        return {"crs_frame": WKT, "arrays": {}, "meta": {}, "warnings": [],
                "mode": "range", "shape": SHAPE}

    def fake_write_frame_om(path, ordered, arrays, crs_wkt, coordinates,
                            valid_unix, ref_unix):
        with open(path, "wb") as fh:
            fh.write(b"OM")
        written.append(os.path.basename(path))
        return (2, 1)

    monkeypatch.setattr(ENGINE, "load_frame", fake_load_frame)
    monkeypatch.setattr(ENGINE, "write_frame_om", fake_write_frame_om)
    return written


def _read_manifest(tmp_path, domain=DOMAIN):
    path = tmp_path / "out" / domain / "latest.json"
    assert path.is_file(), "latest.json non scritto"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# (a) flag OFF = comportamento INVARIATO
# ---------------------------------------------------------------------------
def test_flag_off_frame_mancante_viene_saltato_come_prima(engine, monkeypatch,
                                                          tmp_path):
    """Con il flag OFF un buco NON ferma il dominio (e il manifest resta
    disallineato): e' il comportamento storico, che NON va cambiato."""
    monkeypatch.setattr(engine, "load_manifest",
                        lambda d: _payload("2026-10-24T12:00:00Z", VALID, True))
    monkeypatch.setattr(engine, "discover_partial_run",
                        lambda *a, **k: pytest.fail(
                            "discover_partial_run chiamata con il flag OFF"))
    written = _install_frames(monkeypatch, {VALID[0], VALID[2], VALID[3]})

    res = engine.process_domain(DOMAIN, _args(tmp_path), str(tmp_path / "cache"),
                                "", {}, dry_run=False)
    man = _read_manifest(tmp_path)

    assert res["status"] == "ok", res
    assert res["n_frames"] == 3
    assert written == ["2026-10-24T1200.om", "2026-10-24T1400.om",
                      "2026-10-24T1500.om"]
    # COMPORTAMENTO STORICO: valid_times resta l'elenco COMPLETO (4) mentre i
    # frame sono 3 -> e' esattamente il disallineamento che il flag ON corregge.
    assert man["valid_times"] == VALID
    assert [f["valid_time"] for f in man["frames"]] == [VALID[0], VALID[2], VALID[3]]
    assert "partial" not in man and "run_source" not in man


def test_flag_off_non_aggiunge_campi_nuovi_al_manifest(engine, monkeypatch,
                                                       tmp_path):
    monkeypatch.setattr(engine, "load_manifest",
                        lambda d: _payload("2026-10-24T12:00:00Z", VALID, True))
    _install_frames(monkeypatch, set(VALID))

    res = engine.process_domain(DOMAIN, _args(tmp_path), str(tmp_path / "cache"),
                                "", {}, dry_run=False)
    man = _read_manifest(tmp_path)

    assert res["status"] == "ok"
    assert set(man) == {
        "version", "generated_at", "domain", "reference_time", "valid_times",
        "bbox", "grid", "source_grid", "grid_source", "var_order", "resampling",
        "license", "frames", "variables", "sizes", "pruned", "warnings",
        "download"}
    assert "run_source" not in res


# ---------------------------------------------------------------------------
# (b) flag ON + run parziale: si ferma al primo buco, non salta
# ---------------------------------------------------------------------------
def test_flag_on_prefisso_si_ferma_al_primo_buco(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "load_manifest",
                        lambda d: _payload("2026-10-24T12:00:00Z", VALID, False))
    monkeypatch.setattr(engine, "discover_partial_run", lambda *a, **k: None)
    written = _install_frames(monkeypatch, {VALID[0], VALID[2], VALID[3]})

    res = engine.process_domain(
        DOMAIN, _args(tmp_path, allow_partial_run=True, partial_min_frames=1),
        str(tmp_path / "cache"), "", {}, dry_run=False)
    man = _read_manifest(tmp_path)

    assert res["status"] == "ok", res
    # PREFISSO: si prende SOLO il primo frame, il buco al secondo ferma tutto.
    assert written == ["2026-10-24T1200.om"]
    assert res["n_frames"] == 1
    assert man["valid_times"] == [VALID[0]]
    assert [f["valid_time"] for f in man["frames"]] == [VALID[0]]
    # ALLINEAMENTO POSIZIONALE: la lunghezza coincide sempre.
    assert len(man["valid_times"]) == len(man["frames"])
    assert man["partial"]["n_expected"] == len(VALID)
    assert man["partial"]["n_published"] == 1
    assert man["partial"]["missing"] == VALID[1:]
    assert man["partial"]["stopped_at_first_gap"] is True
    assert man["partial"]["completed"] is False
    assert man["run_source"]["name"] == "latest.json"
    assert man["run_source"]["partial"] is False
    # I frame di un eventuale run precedente non vengono toccati (prune solo
    # sulla finestra pubblicata).
    assert sorted(os.listdir(str(tmp_path / "out" / DOMAIN))) == [
        "2026-10-24T1200.om", "latest.json"]


def test_flag_on_primo_frame_mancante_fallisce(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "load_manifest",
                        lambda d: _payload("2026-10-24T12:00:00Z", VALID, False))
    monkeypatch.setattr(engine, "discover_partial_run", lambda *a, **k: None)
    monkeypatch.setattr(engine, "probe_frame_shape", lambda *a, **k: (None, "fail"))
    _install_frames(monkeypatch, set(VALID))

    res = engine.process_domain(
        DOMAIN, _args(tmp_path, allow_partial_run=True, partial_min_frames=1),
        str(tmp_path / "cache"), "", {}, dry_run=False)

    assert res["status"] == "fail"
    assert "primo frame" in res["reason"]
    assert not (tmp_path / "out" / DOMAIN / "latest.json").exists()


# ---------------------------------------------------------------------------
# (f) soglia minima
# ---------------------------------------------------------------------------
def test_flag_on_sotto_soglia_minima_fallisce_e_rimuove_i_file(engine,
                                                               monkeypatch,
                                                               tmp_path):
    monkeypatch.setattr(engine, "load_manifest",
                        lambda d: _payload("2026-10-24T12:00:00Z", VALID, False))
    monkeypatch.setattr(engine, "discover_partial_run", lambda *a, **k: None)
    written = _install_frames(monkeypatch, {VALID[0]})

    res = engine.process_domain(
        DOMAIN, _args(tmp_path, allow_partial_run=True, partial_min_frames=3),
        str(tmp_path / "cache"), "", {}, dry_run=False)

    assert res["status"] == "fail"
    assert "insufficienti" in res["reason"]
    assert written == ["2026-10-24T1200.om"]      # scritto durante il tentativo
    out_dir = tmp_path / "out" / DOMAIN
    assert not list(out_dir.glob("*.om"))         # ...e poi RIMOSSO
    assert not (out_dir / "latest.json").exists()


def test_flag_on_soglia_limitata_dall_orizzonte_richiesto(engine, monkeypatch,
                                                          tmp_path):
    """`--valid-frames 2` e' una scelta VOLUTA: la soglia non deve superarla."""
    monkeypatch.setattr(engine, "load_manifest",
                        lambda d: _payload("2026-10-24T12:00:00Z", VALID, True))
    monkeypatch.setattr(engine, "discover_partial_run", lambda *a, **k: None)
    _install_frames(monkeypatch, set(VALID))

    res = engine.process_domain(
        DOMAIN, _args(tmp_path, allow_partial_run=True, partial_min_frames=3,
                      valid_frames=2), str(tmp_path / "cache"), "", {},
        dry_run=False)

    assert res["status"] == "ok", res
    assert res["n_frames"] == 2
    assert res["partial"]["min_frames"] == 2      # min(3, 2 frame richiesti)


# ---------------------------------------------------------------------------
# (c) flag ON con tutti i frame = identico al percorso storico
# ---------------------------------------------------------------------------
def test_flag_on_tutti_i_frame_identico_a_prima(engine, monkeypatch, tmp_path):
    payload = _payload("2026-10-24T12:00:00Z", VALID, True)

    monkeypatch.setattr(engine, "load_manifest", lambda d: payload)
    _install_frames(monkeypatch, set(VALID))
    engine.process_domain(DOMAIN, _args(tmp_path, out=str(tmp_path / "off")),
                          str(tmp_path / "cache"), "", {}, dry_run=False)
    with open(tmp_path / "off" / DOMAIN / "latest.json", "r",
              encoding="utf-8") as fh:
        off = json.load(fh)

    monkeypatch.setattr(engine, "discover_partial_run", lambda *a, **k: None)
    engine.process_domain(
        DOMAIN, _args(tmp_path, out=str(tmp_path / "on"), allow_partial_run=True),
        str(tmp_path / "cache"), "", {}, dry_run=False)
    with open(tmp_path / "on" / DOMAIN / "latest.json", "r",
              encoding="utf-8") as fh:
        on = json.load(fh)

    assert on["valid_times"] == off["valid_times"] == VALID
    assert [f["file"] for f in on["frames"]] == [f["file"] for f in off["frames"]]
    assert [f["valid_time"] for f in on["frames"]] == VALID
    assert on["partial"]["missing"] == []
    assert on["partial"]["n_published"] == on["partial"]["n_expected"] == len(VALID)
    assert on["partial"]["stopped_at_first_gap"] is False
    # Solo i campi di provenienza in piu'.
    assert set(on) - set(off) == {"run_source", "partial"}


# ---------------------------------------------------------------------------
# (e) scoperta del run in corso
# ---------------------------------------------------------------------------
def test_discover_in_progress_piu_recente(engine, monkeypatch):
    latest = _payload("2026-10-24T12:00:00Z", VALID[:2], True)
    prog = _payload("2026-10-24T18:00:00Z", VALID, False)
    calls = []

    def fake_read_json(url, retries=3):
        calls.append(url)
        if url.endswith("/in-progress.json"):
            return prog, None
        return None, RuntimeError("404")

    monkeypatch.setattr(engine, "http_read_json", fake_read_json)
    monkeypatch.setattr(engine, "s3_list_prefix",
                        lambda *a, **k: pytest.fail("listing non atteso"))

    disc = engine.discover_partial_run(DOMAIN, latest=latest)

    assert disc is not None
    assert disc["source"] == "in-progress.json"
    assert disc["reference_time"] == "2026-10-24T18:00:00Z"
    assert disc["valid_times"] == sorted(VALID)
    assert disc["completed"] is False
    assert calls == [engine.s3_in_progress_url(DOMAIN)]


def test_discover_fallback_listing_piu_meta(engine, monkeypatch):
    latest = _payload("2026-10-24T12:00:00Z", VALID[:2], True)
    prog = _payload("2026-10-24T06:00:00Z", VALID[:1], True)   # vecchio: scartato
    meta = _payload("2026-10-24T18:00:00Z", VALID, False)
    listed = []
    seen = []

    def fake_read_json(url, retries=3):
        seen.append(url)
        if url.endswith("/in-progress.json"):
            return prog, None
        if url.endswith("/1800Z/meta.json"):
            return meta, None
        return None, RuntimeError("404")

    def fake_list(prefix, delimiter="/", timeout=60):
        listed.append(prefix)
        return [], ["%s1200Z/" % prefix, "%s1800Z/" % prefix], None

    monkeypatch.setattr(engine, "http_read_json", fake_read_json)
    monkeypatch.setattr(engine, "s3_list_prefix", fake_list)

    now = engine.parse_iso("2026-10-24T21:00:00Z")
    disc = engine.discover_partial_run(DOMAIN, latest=latest, now=now)

    assert disc is not None
    assert disc["source"] == "prefix-listing+meta.json"
    assert disc["url"].endswith("/2026/10/24/1800Z/meta.json")
    assert disc["n_listed"] == len(VALID)
    assert listed[0] == "data_spatial/%s/2026/10/24/" % DOMAIN
    # Il run piu' recente (18Z) e' provato PRIMA del 12Z.
    assert seen[1].endswith("/1800Z/meta.json")


def test_discover_nulla_di_nuovo(engine, monkeypatch):
    latest = _payload("2026-10-24T18:00:00Z", VALID, True)

    def fake_read_json(url, retries=3):
        if url.endswith("/in-progress.json"):
            return _payload("2026-10-24T12:00:00Z", VALID, True), None
        return None, RuntimeError("404")

    monkeypatch.setattr(engine, "http_read_json", fake_read_json)
    monkeypatch.setattr(engine, "s3_list_prefix", lambda *a, **k: ([], [], None))

    assert engine.discover_partial_run(
        DOMAIN, latest=latest, now=engine.parse_iso("2026-10-24T21:00:00Z")) is None


def test_discover_ignora_run_senza_valid_times(engine, monkeypatch):
    latest = _payload("2026-10-24T12:00:00Z", VALID, True)
    empty = _payload("2026-10-24T18:00:00Z", [], False)

    def fake_read_json(url, retries=3):
        if url.endswith("/in-progress.json"):
            return empty, None
        return None, RuntimeError("404")

    monkeypatch.setattr(engine, "http_read_json", fake_read_json)
    monkeypatch.setattr(engine, "s3_list_prefix", lambda *a, **k: ([], [], None))

    assert engine.discover_partial_run(
        DOMAIN, latest=latest, now=engine.parse_iso("2026-10-24T21:00:00Z")) is None


# ---------------------------------------------------------------------------
# (d) guard NO-OP con il run parziale
# ---------------------------------------------------------------------------
def test_noop_riparte_quando_arrivano_nuovi_frame(engine, monkeypatch, tmp_path):
    """Il run parziale che AVANZA non deve essere saltato; quando e' completo e
    `valid_times` non cambiano il NO-OP torna a funzionare."""
    ref = "2026-10-24T18:00:00Z"
    monkeypatch.setattr(engine, "load_manifest",
                        lambda d: _payload("2026-10-24T12:00:00Z", VALID[:1], True))
    _install_frames(monkeypatch, set(VALID))
    args = _args(tmp_path, allow_partial_run=True, partial_min_frames=1)
    cache = str(tmp_path / "cache")

    # 1) run 18Z appena partito: 1 frame.
    monkeypatch.setattr(engine, "discover_partial_run", lambda *a, **k: {
        "source": "in-progress.json", "url": "u", "reference_time": ref,
        "valid_times": VALID[:1], "completed": False,
        "last_modified_time": "t", "crs_wkt": WKT, "n_listed": 1,
        "discovered_at": "d"})
    r1 = engine.process_domain(DOMAIN, args, cache, "", {}, dry_run=False)
    assert r1["status"] == "ok" and r1.get("noop") is not True
    assert r1["n_frames"] == 1

    # 2) stesso run, 2 frame: il dominio DEVE ripartire.
    monkeypatch.setattr(engine, "discover_partial_run", lambda *a, **k: {
        "source": "in-progress.json", "url": "u", "reference_time": ref,
        "valid_times": VALID[:2], "completed": False,
        "last_modified_time": "t", "crs_wkt": WKT, "n_listed": 2,
        "discovered_at": "d"})
    r2 = engine.process_domain(DOMAIN, args, cache, "", {}, dry_run=False)
    assert r2.get("noop") is not True
    assert r2["n_frames"] == 2
    assert _read_manifest(tmp_path)["valid_times"] == VALID[:2]

    # 3) stesso run ancora incompleto e STESSI 2 frame: il NO-OP e' comunque
    #    VIETATO (la sorgente puo' pubblicare da un momento all'altro).
    r3 = engine.process_domain(DOMAIN, args, cache, "", {}, dry_run=False)
    assert r3.get("noop") is not True
    assert r3["n_frames"] == 2

    # 4) run COMPLETO e stessi valid_times: NO-OP.
    monkeypatch.setattr(engine, "discover_partial_run", lambda *a, **k: {
        "source": "in-progress.json", "url": "u", "reference_time": ref,
        "valid_times": VALID[:2], "completed": True,
        "last_modified_time": "t", "crs_wkt": WKT, "n_listed": 2,
        "discovered_at": "d"})
    r4 = engine.process_domain(DOMAIN, args, cache, "", {}, dry_run=False)
    assert r4.get("noop") is True
    assert r4["status"] == "ok"


def test_noop_con_flag_off_resta_invariato(engine, monkeypatch, tmp_path):
    """Flag OFF: due run identici -> il secondo e' NO-OP (come sempre)."""
    payload = _payload("2026-10-24T12:00:00Z", VALID, True)
    monkeypatch.setattr(engine, "load_manifest", lambda d: payload)
    _install_frames(monkeypatch, set(VALID))
    args = _args(tmp_path)

    r1 = engine.process_domain(DOMAIN, args, str(tmp_path / "cache"), "", {},
                               dry_run=False)
    r2 = engine.process_domain(DOMAIN, args, str(tmp_path / "cache"), "", {},
                               dry_run=False)
    assert r1["status"] == "ok" and r1.get("noop") is not True
    assert r2["status"] == "ok" and r2.get("noop") is True


# ---------------------------------------------------------------------------
# CLI: il flag e' OFF di default e accendibile
# ---------------------------------------------------------------------------
def test_cli_flag_off_di_default(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "process_domain",
                        lambda *a, **k: {"domain": a[0], "status": "ok",
                                         "reason": None, "anchors": [],
                                         "out_dir": None, "latest_path": None})
    monkeypatch.setattr(engine, "close_http", lambda: None)
    assert engine.ALLOW_PARTIAL_RUN is False
    rc = engine.main(["--domains", DOMAIN, "--dry-run",
                      "--out", str(tmp_path / "out")])
    assert rc == 0


def test_cli_accetta_i_tre_parametri(engine):
    ap = engine.build_parser()
    ns = ap.parse_args([])
    assert ns.allow_partial_run is False            # INERTE DI DEFAULT
    assert ns.partial_min_frames == engine.PARTIAL_MIN_FRAMES == 3
    assert ns.partial_probe_hours == engine.PARTIAL_PROBE_HOURS
    ns2 = ap.parse_args(["--allow-partial-run", "--partial-min-frames", "5",
                         "--partial-probe-hours", "48"])
    assert ns2.allow_partial_run is True
    assert ns2.partial_min_frames == 5
    assert ns2.partial_probe_hours == 48
