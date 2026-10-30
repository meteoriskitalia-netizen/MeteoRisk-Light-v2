# -*- coding: utf-8 -*-
"""Anomalie rispetto alla climatologia 1991-2020 nel renderer WebP.

Perche' esiste: le tre variabili ``*_anomaly`` sono un PRODOTTO (campo meno
norma del mese) e non un campo del frame. Se il calcolo sbaglia di poco nessuno
se ne accorge -- la mappa esce colorata lo stesso -- quindi qui si fissa:
schema e griglia del file delle norme, interpolazione bilineare sui 4 angoli e
in mezzo, mese giusto, ``None`` fuori griglia (nessun colore inventato),
scarto ZERO fra campo sintetico e norma, regioni consentite (europa/north) e
impronta della palette PER VARIABILE.

La parte di integrazione scrive frame ``.om`` VERI con ``OmFileWriter`` (come
``test_render_fm_webp_incremental.py``) e lancia ``main()``: nessun mock del
modulo sotto test, nessuna rete. Se ``numpy``/``PIL``/``omfiles`` mancano il
modulo viene SALTATO: un verde senza dipendenze non sarebbe una prova.
"""
import importlib.util
import json
import os

import numpy as np
import pytest

pytest.importorskip("PIL")
pytest.importorskip("omfiles")
from omfiles import OmFileWriter  # noqa: E402  (dopo importorskip)
from PIL import Image  # noqa: E402  (dopo importorskip)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RENDERER_PATH = os.path.join(REPO_ROOT, "scripts", "forecast_v2",
                             "render_fm_webp.py")
NORMALS_PATH = os.path.join(REPO_ROOT, "data", "climate",
                            "normals_1991_2020.json")


def _load_renderer():
    spec = importlib.util.spec_from_file_location("render_fm_webp_anomaly_ut",
                                                  RENDERER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_renderer()


# --------------------------------------------------------------------------- #
# Norma sintetica: 2x2 nodi a 10 gradi, angoli noti 0/10/20/30
# --------------------------------------------------------------------------- #
def _synthetic_spec():
    """Norma 2x2 con angoli 0/10 (riga 0) e 20/30 (riga 1), 12 mesi identici."""
    vals = np.empty((2, 2, 12), dtype=np.float64)
    vals[0, 0, :] = 0.0     # (lat 0, lon 0)
    vals[0, 1, :] = 10.0    # (lat 0, lon 10)
    vals[1, 0, :] = 20.0    # (lat 10, lon 0)
    vals[1, 1, :] = 30.0    # (lat 10, lon 10)
    return {"lat0": 0.0, "lon0": 0.0, "step": 10.0,
            "n_lat": 2, "n_lon": 2, "period": "1991-2020",
            "schema": mod.NORMALS_SCHEMA, "holes": 0,
            "fields": {"sintetica": vals}}


def test_norm_bilineare_sui_quattro_angoli():
    """Sui nodi la bilineare deve dare ESATTAMENTE il valore del nodo."""
    norm = mod.normals_norm(_synthetic_spec(), "sintetica")
    assert norm(0.0, 0.0, 1) == pytest.approx(0.0)
    assert norm(0.0, 10.0, 1) == pytest.approx(10.0)
    assert norm(10.0, 0.0, 1) == pytest.approx(20.0)
    assert norm(10.0, 10.0, 1) == pytest.approx(30.0)
    # centro: media dei quattro
    assert norm(5.0, 5.0, 1) == pytest.approx(15.0)
    # punto NON simmetrico: fy=0.7, fx=0.2 -> 0,56*20 + 0,06*10 + 0,14*30
    assert norm(7.0, 2.0, 1) == pytest.approx(16.0)
    # interpolazione lineare lungo un solo asse
    assert norm(2.5, 0.0, 1) == pytest.approx(5.0)
    assert norm(0.0, 5.0, 1) == pytest.approx(5.0)


def test_norm_usa_il_mese_richiesto():
    """``values[j] = mese j+1``: gennaio e luglio devono leggere cose diverse."""
    spec = _synthetic_spec()
    spec["fields"]["sintetica"][:, :, 0] = 1.0     # gennaio
    spec["fields"]["sintetica"][:, :, 6] = 7.0     # luglio
    norm = mod.normals_norm(spec, "sintetica")
    assert norm(0.0, 0.0, 1) == pytest.approx(1.0)
    assert norm(0.0, 0.0, 7) == pytest.approx(7.0)
    for month in range(12):
        assert norm(0.0, 0.0, month + 1) is not None
    with pytest.raises(ValueError):
        norm(0.0, 0.0, 0)
    with pytest.raises(ValueError):
        norm(0.0, 0.0, 13)


def test_norm_fuori_griglia_e_none():
    """Fuori dalla griglia la norma NON si estrapola: scalare None, vettore NaN."""
    norm = mod.normals_norm(_synthetic_spec(), "sintetica")
    assert norm(10.0001, 5.0, 1) is None
    assert norm(-0.0001, 5.0, 1) is None
    assert norm(5.0, 10.0001, 1) is None
    assert norm(5.0, -0.0001, 1) is None
    arr = norm(np.array([0.0, 5.0, 20.0]), np.array([0.0, 5.0, 5.0]), 1)
    assert arr.shape == (3,)
    assert arr[0] == pytest.approx(0.0)
    assert arr[1] == pytest.approx(15.0)
    assert np.isnan(arr[2]), "fuori griglia deve essere NaN, non un valore"


def test_fuori_griglia_il_pixel_diventa_trasparente():
    """La norma assente deve arrivare alla palette come NaN -> alpha 0."""
    scales = mod.load_color_scales()
    lk = mod.make_lookup(scales, "temperature_850hPa_anomaly", "light")
    rgba = lk(np.array([[np.nan, 0.0]], dtype=np.float64))
    assert rgba[0, 0, 3] == 0, "dove la norma non c'e' il pixel deve essere trasparente"
    assert rgba[0, 1, 3] == 255, "dove c'e' deve essere opaco"


# --------------------------------------------------------------------------- #
# Calcolo dell'anomalia (griglia sorgente)
# --------------------------------------------------------------------------- #
GRID_SMALL = {"nx": 3, "ny": 2, "lonMin": 0.0, "latMin": 0.0,
              "dx": 5.0, "dy": 10.0}


def test_anomalia_zero_quando_il_campo_e_la_norma():
    """Campo costruito sulla norma: anomalia 0 in OGNI cella (scarto 0)."""
    norm = mod.normals_norm(_synthetic_spec(), "sintetica")
    lats = GRID_SMALL["latMin"] + np.arange(GRID_SMALL["ny"]) * GRID_SMALL["dy"]
    lons = GRID_SMALL["lonMin"] + np.arange(GRID_SMALL["nx"]) * GRID_SMALL["dx"]
    field = norm(lats[:, None], lons[None, :], 1)
    out = mod.anomaly_grid(field.astype(np.float32), norm, GRID_SMALL, 1)
    assert out.shape == (GRID_SMALL["ny"], GRID_SMALL["nx"])
    assert np.all(np.abs(out) < 1e-6), out


def test_anomalia_e_campo_meno_norma_al_decimo():
    """Tre punti di controllo: anomalia == campo - norma, al decimo."""
    norm = mod.normals_norm(_synthetic_spec(), "sintetica")
    lats = GRID_SMALL["latMin"] + np.arange(GRID_SMALL["ny"]) * GRID_SMALL["dy"]
    lons = GRID_SMALL["lonMin"] + np.arange(GRID_SMALL["nx"]) * GRID_SMALL["dx"]
    base = norm(lats[:, None], lons[None, :], 1)
    field = np.array(base + [[-2.5, 0.0, 3.7], [10.1, -0.4, 0.0]], dtype=np.float32)
    out = mod.anomaly_grid(field, norm, GRID_SMALL, 1)
    # (0,0): norma 0 + (-2.5); (0,2): norma 20/10 -> lon 10 = 10 + 3.7
    assert out[0, 0] == pytest.approx(-2.5, abs=1e-4)
    assert out[0, 2] == pytest.approx(3.7, abs=1e-4)
    assert out[1, 0] == pytest.approx(10.1, abs=1e-4)
    assert out[1, 1] == pytest.approx(-0.4, abs=1e-4)


def test_anomalia_nan_fuori_dalla_norma():
    """Un frame che esce dalla griglia delle norme -> NaN solo li'."""
    norm = mod.normals_norm(_synthetic_spec(), "sintetica")
    grid = {"nx": 2, "ny": 1, "lonMin": 0.0, "latMin": 0.0,
            "dx": 50.0, "dy": 10.0}     # la seconda colonna e' a lon 50
    field = np.array([[1.0, 2.0]], dtype=np.float32)
    out = mod.anomaly_grid(field, norm, grid, 1)
    assert out[0, 0] == pytest.approx(1.0, abs=1e-6)
    assert np.isnan(out[0, 1]), "oltre il bordo est la norma non esiste"


def test_valid_time_month():
    assert mod.valid_time_month("2026-01-01T00:00Z") == 1
    assert mod.valid_time_month("2026-11-06T2100".replace("2100", "21:00Z")) == 11
    assert mod.valid_time_month("2026-12-31T23:00Z") == 12
    for bad in ("2026-13-01T00:00Z", "2026-00-01T00:00Z", "nonsense"):
        with pytest.raises(ValueError):
            mod.valid_time_month(bad)


# --------------------------------------------------------------------------- #
# Il file delle norme vero
# --------------------------------------------------------------------------- #
def test_normals_reali_si_caricano():
    spec = mod.load_normals(NORMALS_PATH)
    assert spec["schema"] == "meteorisk.climate.normals/1"
    assert spec["period"] == "1991-2020"
    assert spec["step"] == 5.0
    assert (spec["n_lat"], spec["n_lon"]) == (15, 24)
    assert spec["holes"] == 0
    for name in ("temperature_850hPa", "geopotential_height_500hPa",
                 "pressure_msl"):
        assert name in spec["fields"], name
        assert spec["fields"][name].shape == (15, 24, 12)
    # 45N/10E e' un NODO della griglia: il valore deve essere quello del file
    norm = mod.normals_norm(spec, "temperature_850hPa")
    assert norm(45.0, 10.0, 1) == pytest.approx(0.1, abs=0.05)
    # 850 hPa gennaio PIU' FREDDO del 2 m nello stesso nodo (0,1 contro 1,9 C)
    norm2 = mod.normals_norm(spec, "temperature_2m")
    assert norm(45.0, 10.0, 1) < norm2(45.0, 10.0, 1)


def test_normals_fuori_dall_area_coperta_non_hanno_norma():
    """A 40W/45N (dentro) c'e'; a 120E (fuori dal bbox) no."""
    spec = mod.load_normals(NORMALS_PATH)
    norm = mod.normals_norm(spec, "pressure_msl")
    assert norm(45.0, -40.0, 1) is not None
    assert norm(45.0, 120.0, 1) is None
    assert norm(10.0, 10.0, 1) is None, "sotto 20N la griglia non copre"


def test_normals_invalidi_danno_errore(tmp_path):
    good = json.load(open(NORMALS_PATH, "r", encoding="utf-8"))
    cases = {
        "schema": dict(good, schema="altro/1"),
        "mesi": dict(good, months=6),
        "celle": dict(good, grid=dict(good["grid"], n_lat=16)),
    }
    for name, doc in cases.items():
        path = tmp_path / ("%s.json" % name)
        path.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(ValueError):
            mod.load_normals(str(path))
    rows = json.loads(json.dumps(good))
    rows["fields"]["pressure_msl"]["values"][3] = rows["fields"]["pressure_msl"]["values"][3][:11]
    path = tmp_path / "riga.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError):
        mod.load_normals(str(path))
    with pytest.raises(OSError):
        mod.load_normals(str(tmp_path / "assente.json"))


def test_le_tre_anomalie_sono_dichiarate_e_senza_isolinee():
    assert sorted(mod.ANOMALY_VARS) == [
        "geopotential_height_500hPa_anomaly", "pressure_msl_anomaly",
        "temperature_850hPa_anomaly"]
    for var, spec in mod.ANOMALY_VARS.items():
        # NESSUNA isolinea: la variabile non compare nella tabella degli intervalli
        assert mod.contour_interval_for(var) is None, var
        # il campo sorgente esiste come variabile del frame
        assert spec["source"] in ("temperature_850hPa",
                                  "geopotential_height_500hPa", "pressure_msl")
    # Z500: valore in METRI ma palette in DAM -> fattore 0,1, non 1
    assert mod.ANOMALY_VARS["geopotential_height_500hPa_anomaly"]["palette_scale"] == 0.1
    assert mod.ANOMALY_VARS["geopotential_height_500hPa_anomaly"]["units"] == "m"
    scales = mod.load_color_scales()
    unit = scales["geopotential_height_500hPa_anomaly"]["light"]["unit"]
    assert unit.startswith("dam"), unit
    # le altre due non hanno conversione
    for var in ("temperature_850hPa_anomaly", "pressure_msl_anomaly"):
        assert mod.ANOMALY_VARS[var]["palette_scale"] == 1.0


def test_la_palette_non_ha_piu_la_chiave_a_2m():
    """La scala e' a 850 hPa: il nome vecchio non deve sopravvivere."""
    scales = mod.load_color_scales()
    assert "temperature_850hPa_anomaly" in scales
    assert "temperature_2m_anomaly" not in scales


# --------------------------------------------------------------------------- #
# Impronta della palette PER VARIABILE
# --------------------------------------------------------------------------- #
def test_impronta_per_variabile_distinta_e_isolata():
    scales = mod.load_color_scales()
    a = mod.scale_sha256(scales, "temperature_850hPa_anomaly")
    b = mod.scale_sha256(scales, "pressure_msl_anomaly")
    assert a and b and a != b, "due variabili con scale diverse hanno impronte diverse"

    mutated = json.loads(json.dumps(scales))
    mutated["temperature_850hPa_anomaly"]["light"]["colors"][0][0] = 254
    assert mod.scale_sha256(mutated, "temperature_850hPa_anomaly") != a
    assert mod.scale_sha256(mutated, "pressure_msl_anomaly") == b, \
        "cambiare una scala non deve toccare l'impronta dell'altra"


def test_impronta_globale_cambia_e_quella_per_variabile_no(tmp_path):
    """Prova del difetto che l'impronta per variabile elimina.

    Scrivendo due file di palette, il digest GLOBALE (quello storico) cambia
    quando cambia UNA sola scala; l'impronta della variabile che non e' stata
    toccata resta identica. E' esattamente la condizione che permette al
    percorso incrementale di riusare le altre variabili.
    """
    scales = mod.load_color_scales()
    src = os.path.join(REPO_ROOT, "scripts", "forecast_v2", "color_scales.json")
    before = mod.palette_sha256(src)
    mutated = json.loads(json.dumps(scales))
    mutated["temperature_850hPa_anomaly"]["light"]["breakpoints"][0] = -13
    path = tmp_path / "mutata.json"
    path.write_text(json.dumps({"version": "0.2.2", "scales": mutated}),
                    encoding="utf-8")
    after = mod.palette_sha256(str(path))
    assert after != before, "il digest globale deve cambiare (comportamento storico)"
    assert (mod.scale_sha256(mutated, "pressure_msl_anomaly")
            == mod.scale_sha256(scales, "pressure_msl_anomaly"))


def test_manifest_con_impronta_per_variabile_vince_sul_digest_globale(tmp_path):
    """``var_publish_state`` confronta la scala, non piu' il file intero."""
    scales = mod.load_color_scales()
    scale_sha = mod.scale_sha256(scales, "pressure_msl_anomaly")
    vdir = tmp_path / "europa" / "dom" / "runs" / "R" / "pressure_msl_anomaly"
    vdir.mkdir(parents=True)
    (vdir / "latest.json").write_text(json.dumps({
        "version": mod.WEBP_VERSION,
        "palette_sha256": "0" * 64,          # digest globale DIVERSO
        "palette_var_sha256": scale_sha,
        "frames": [{"file": "2026-11-06T1200.webp",
                    "valid_time": "2026-11-06T12:00Z", "bytes": 10}],
        "valid_times": ["2026-11-06T12:00Z"],
    }), encoding="utf-8")
    state = mod.var_publish_state(str(vdir), set(), "f" * 64,
                                 scale_sha=scale_sha)
    assert state["reuse"] is True, state
    assert state["reason"] == "manifest"

    # scala cambiata: NON si riusa, e il motivo lo dice
    state = mod.var_publish_state(str(vdir), set(), "f" * 64,
                                 scale_sha="a" * 64)
    assert state["reuse"] is False
    assert state["reason"] == "scale_changed"


# --------------------------------------------------------------------------- #
# Integrazione: frame .om veri, main() vero
# --------------------------------------------------------------------------- #
DOMAIN = "testdom"
REF_TIME = "2026-11-01T00:00:00Z"
RUN_ID = "20261101T000000Z"
VALID_TIMES = ["2026-11-06T00:00Z", "2026-11-06T12:00Z"]
SOURCE_VARS = ["temperature_850hPa", "geopotential_height_500hPa",
               "pressure_msl"]
ANOM_VARS = ["temperature_850hPa_anomaly",
             "geopotential_height_500hPa_anomaly", "pressure_msl_anomaly"]

GLOBAL_GRID = {"nx": 72, "ny": 37, "lonMin": -180.0, "latMin": -90.0,
               "dx": 5.0, "dy": 5.0}
GLOBAL_BBOX = {"lat_min": -90.0, "lon_min": -180.0, "lat_max": 90.0,
               "lon_max": 175.0}
REGIONAL_GRID = {"nx": 111, "ny": 57, "lonMin": -15.0, "latMin": 34.0,
                 "dx": 0.5, "dy": 0.5}
REGIONAL_BBOX = {"lat_min": 34.0, "lon_min": -15.0, "lat_max": 62.0,
                 "lon_max": 40.0}


def _write_om(path, fields):
    import datetime as _dt

    def epoch(iso):
        return int(_dt.datetime.fromisoformat(
            str(iso).replace("Z", "+00:00")).timestamp())

    w = OmFileWriter.at_path(str(path))
    children = []
    for name, arr in fields.items():
        arr = np.ascontiguousarray(np.asarray(arr, dtype=np.float32))
        children.append(w.write_array(arr, chunks=[min(32, int(arr.shape[0])),
                                                   min(32, int(arr.shape[1]))],
                                      scale_factor=10.0, add_offset=0.0,
                                      compression="pfor_delta_2d_int16",
                                      name=name))
    children.append(w.write_scalar("GEOGCRS[test]", name="crs_wkt"))
    children.append(w.write_scalar("latitude longitude", name="coordinates"))
    children.append(w.write_scalar(epoch(REF_TIME), name="valid_time"))
    children.append(w.write_scalar(epoch(REF_TIME), name="forecast_reference_time"))
    root = w.write_group(name="", children=children)
    w.close(root)


def _domain_tree(tmp_path, grid, bbox):
    """Dominio con 2 frame veri e il manifest del motore."""
    in_root = tmp_path / "in"
    ddir = in_root / DOMAIN
    ddir.mkdir(parents=True)
    frames = []
    ny, nx = grid["ny"], grid["nx"]
    lats = grid["latMin"] + np.arange(ny) * grid["dy"]
    lons = grid["lonMin"] + np.arange(nx) * grid["dx"]
    for i, vt in enumerate(VALID_TIMES):
        # temperature_850hPa: un gradiente zonale; il resto costante: l'anomalia
        # e' definita ovunque dentro la griglia delle norme e i pixel restano
        # tutti opachi (nessuna banda trasparente).
        t850 = np.empty((ny, nx), dtype=np.float32)
        for r in range(ny):
            t850[r, :] = 6.0 - 0.2 * lats[r] + 0.05 * lons
        z500 = np.full((ny, nx), 5400.0 + 5.0 * i, dtype=np.float32)
        z500 += np.add.outer(lats * 0.5, lons * 0.1).astype(np.float32)
        pmsl = np.full((ny, nx), 1012.0 - 2.0 * i, dtype=np.float32)
        pmsl += np.add.outer(lats * 0.05, np.zeros(nx)).astype(np.float32)
        name = mod.valid_time_to_filename(vt) + ".om"
        _write_om(ddir / name, {"temperature_850hPa": t850,
                                "geopotential_height_500hPa": z500,
                                "pressure_msl": pmsl})
        frames.append({"file": name, "valid_time": vt,
                       "bytes": os.path.getsize(ddir / name)})
    (ddir / "latest.json").write_text(json.dumps({
        "domain": DOMAIN,
        "reference_time": REF_TIME,
        "valid_times": list(VALID_TIMES),
        "frames": frames,
        "grid": dict(grid),
        "bbox": dict(bbox),
        "var_order": list(SOURCE_VARS),
    }), encoding="utf-8")
    return str(in_root)


def _run(in_root, out_dir, regions):
    return mod.main(["--in", in_root, "--out", str(out_dir),
                     "--domains", DOMAIN, "--regions", regions,
                     "--vars", ",".join(SOURCE_VARS + ANOM_VARS),
                     "--upsample", "1", "--workers", "1", "--method", "4",
                     "--quality", "80"])


def _var_dir(out_dir, region, var):
    return os.path.join(str(out_dir), region, DOMAIN, "runs", RUN_ID, var)


def test_climatologia_assente_o_invalida_non_fa_fallire_il_render(tmp_path,
                                                                 monkeypatch):
    """Senza climatologia le anomalie si saltano; il frame si rende lo stesso.

    Requisito esplicito: un file mancante o invalido non deve fermare la
    pipeline. Si verifica sul render VERO: exit 0, variabili sorgente presenti,
    nessuna directory di anomalia.
    """
    in_root = _domain_tree(tmp_path, REGIONAL_GRID, REGIONAL_BBOX)
    cases = {"assente": str(tmp_path / "non_esiste.json"),
             "invalido": str(tmp_path / "rotto.json")}
    (tmp_path / "rotto.json").write_text('{"schema": "altro/1"}', encoding="utf-8")
    for name, path in cases.items():
        monkeypatch.setattr(mod, "NORMALS_PATH", path)
        out = tmp_path / ("out_" + name)
        assert _run(in_root, out, "europa") == 0, name
        for var in SOURCE_VARS:
            assert os.path.isdir(_var_dir(out, "europa", var)), (name, var)
        for var in ANOM_VARS:
            assert not os.path.isdir(_var_dir(out, "europa", var)), (name, var)


def test_anomalie_solo_su_europa_e_north(tmp_path):
    """Dominio globale: anomalie su europa/north, NESSUNA su italia."""
    in_root = _domain_tree(tmp_path, GLOBAL_GRID, GLOBAL_BBOX)
    out = tmp_path / "out"
    assert _run(in_root, out, "italia,europa,north") == 0

    for region in ("europa", "north"):
        for var in ANOM_VARS:
            vdir = _var_dir(out, region, var)
            assert os.path.isdir(vdir), "%s/%s non prodotta" % (region, var)
            assert os.path.isfile(os.path.join(vdir, "latest.json"))
            # una variabile per frame, tutte scritte
            webps = [n for n in os.listdir(vdir) if n.endswith(".webp")]
            assert len(webps) == len(VALID_TIMES), (region, var, webps)

    # italia: la variabile SORGENTE c'e', l'anomalia NO
    for var in SOURCE_VARS:
        assert os.path.isdir(_var_dir(out, "italia", var)), var
    for var in ANOM_VARS:
        assert not os.path.isdir(_var_dir(out, "italia", var)), \
            "%s non deve esistere su italia" % var

    # il manifest dichiara contro cosa ha calcolato e il mese di ogni frame
    man = json.load(open(os.path.join(_var_dir(out, "europa", ANOM_VARS[0]),
                                      "latest.json"), encoding="utf-8"))
    assert man["anomaly"]["source"] == "temperature_850hPa"
    assert man["anomaly"]["normals_period"] == "1991-2020"
    assert man["anomaly"]["isolines"] is False
    assert man["anomaly"]["palette_scale"] == 1.0
    assert [r["month"] for r in man["frames"]] == [11, 11]
    # ...e l'impronta per variabile, distinta da quella globale
    assert man["palette_var_sha256"] and man["palette_sha256"]
    assert man["palette_var_sha256"] != man["palette_sha256"]


def test_nessuna_anomalia_su_domain_e_italia(tmp_path):
    """Dominio regionale: c'e' 'domain', ma l'anomalia si produce solo su europa."""
    in_root = _domain_tree(tmp_path, REGIONAL_GRID, REGIONAL_BBOX)
    out = tmp_path / "out"
    assert _run(in_root, out, "italia,europa,domain") == 0
    for var in SOURCE_VARS:
        assert os.path.isdir(_var_dir(out, "domain", var)), var
    for var in ANOM_VARS:
        assert os.path.isdir(_var_dir(out, "europa", var)), var
        assert not os.path.isdir(_var_dir(out, "domain", var)), var
        assert not os.path.isdir(_var_dir(out, "italia", var)), var


def test_anomalia_trasparente_fuori_dalla_griglia_della_norma(tmp_path):
    """Su ``north`` (360 gradi) i pixel oltre il bordo est della norma sono trasparenti.

    La climatologia copre 70W..45E: a est di 45E l'anomalia non esiste e il
    pixel deve avere alpha 0 (nessun colore inventato), mentre dentro il bbox
    deve essere opaco. Si controlla il WebP VERO, non un array intermedio.
    """
    in_root = _domain_tree(tmp_path, GLOBAL_GRID, GLOBAL_BBOX)
    out = tmp_path / "out"
    assert _run(in_root, out, "north") == 0
    vdir = _var_dir(out, "north", "temperature_850hPa_anomaly")
    name = sorted(n for n in os.listdir(vdir) if n.endswith(".webp"))[0]
    with Image.open(os.path.join(vdir, name)) as img:
        img = img.convert("RGBA")
        w, h = img.size
        row = h // 2
        # colonna -> longitudine: il crop di north parte da -180 a 5 gradi
        def alphas_at(lon):
            col = int(round((lon - (-180.0)) / 5.0))
            col = max(0, min(w - 1, col))
            return img.getpixel((col, row))[3]

        inside = [alphas_at(lon) for lon in (-60.0, -10.0, 10.0, 40.0)]
        outside = [alphas_at(lon) for lon in (60.0, 120.0, 179.0)]
    assert all(a == 255 for a in inside), inside
    assert all(a == 0 for a in outside), outside
