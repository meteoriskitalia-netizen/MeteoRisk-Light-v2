#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test del nowcast del campo di pioggia (nowcast_field.py, novembre 2026).

Coprono i vincoli dichiarati nel modulo:
  - <3 fotogrammi SRI -> `available: false` con motivo, nessuna eccezione;
  - trasporto: un blob sintetico spostato di 1 cella finisce dove deve;
  - cap 110 km/h: vettore da 200 km/h SOSTITUITO dalla mediana del vicinato
    (0 solo se non esiste nessun vicino valido);
  - nessuna crescita di intensita' (max previsto <= max osservato);
  - maschera del dominio rispettata (fuori dominio -> 0);
  - `main.py` continua se il modulo fallisce (try/except con warning);
  - output: nowcast.json + 3 WebP con i campi richiesti.
"""

import json
import math
import os
import sys

import numpy as np
import pytest
from scipy import ndimage

from radar_engine import clock as clock_mod
from radar_engine import main as main_mod
from radar_engine import models
from radar_engine import nowcast_field as nf
from radar_engine.config import CONFIG, load_config
from radar_engine.preprocess import read_raster

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if os.path.join(_SCRIPTS, "validation") not in sys.path:
    sys.path.insert(0, os.path.join(_SCRIPTS, "validation"))

from conftest import DEFAULT_TRANSFORM, write_synthetic_tif   # noqa: E402

T0 = 1_795_512_000_000          # 2026-11-23T10:40Z (slot SRI fittizio)
STEP_MS = 300_000


# ---------------------------------------------------------------------------
# fixture: griglia piccola con dominio a disco (fuori dominio = -9999)
# ---------------------------------------------------------------------------
def _disk_domain(rows, cols, r0, c0, radius):
    rr, cc = np.ogrid[:rows, :cols]
    return ((rr - r0) ** 2 + (cc - c0) ** 2) <= radius ** 2


def _blob(rows, cols, r0, c0, sigma=6.0, amp=20.0):
    rr, cc = np.ogrid[:rows, :cols]
    return (amp * np.exp(-(((rr - r0) ** 2 + (cc - c0) ** 2)
                           / (2.0 * sigma ** 2)))).astype("float32")


def _write_frame(tmp_path, name, field, domain, ts):
    arr = np.where(domain, field, -9999.0).astype("float32")
    path = str(tmp_path / name)
    write_synthetic_tif(path, arr, transform=DEFAULT_TRANSFORM, nodata=-9999.0)
    rd = read_raster(path, nodata_values=[-9999.0], declared_nodata_from_tiff=True,
                     geo_plausible_bbox=CONFIG["preprocess"]["geo_plausible_bbox"])
    rd.time_ms = int(ts)
    rd.time_iso = nf._iso(ts)
    return rd


class _Bundle(object):
    def __init__(self, rasters=None, verification=None):
        self.dpc_bundle = {"entries": {
            int(rd.time_ms): {"sri": {"raster": rd}}
            for rd in (rasters or [])}}
        self.warnings = []
        self._nowcast_verification = verification


def _moving_frames(tmp_path, n=3, dx=1, rows=200, cols=200, sigma=6.0):
    domain = _disk_domain(rows, cols, 100, 100, 95)
    rasters = []
    for k in range(n):
        field = _blob(rows, cols, 100, 60 + k * dx, sigma=sigma, amp=20.0)
        rasters.append(_write_frame(tmp_path, "f%d.tif" % k, field, domain,
                                    T0 + k * STEP_MS))
    return rasters, domain


def _config(tmp_path):
    cfg = load_config()
    cfg["output"] = dict(cfg["output"], out_dir=str(tmp_path / "out"))
    return cfg


# ---------------------------------------------------------------------------
# 1. fotogrammi insufficienti
# ---------------------------------------------------------------------------
def test_lt3_frames_available_false(tmp_path, monkeypatch):
    rasters, _ = _moving_frames(tmp_path, n=2)
    bundle = _Bundle(rasters)
    monkeypatch.setattr(nf, "fetch_sri_frames", lambda *a, **k: [])
    clk = clock_mod.Clock.at_ms(T0 + 2 * STEP_MS)
    payload, native = nf.compute(bundle, _config(tmp_path), clk,
                                 bundle.warnings)
    assert payload["available"] is False
    assert payload["null_reason"] == "sri_frames_lt_3"
    assert payload["frames_used"] == 2
    assert payload["leads"] == []
    assert payload["speed_cap_kmh"] == 110.0
    assert payload["beats_persistence"] is None
    assert native is None


def test_no_frames_available_false_sri_unavailable(tmp_path, monkeypatch):
    bundle = _Bundle([])
    monkeypatch.setattr(nf, "fetch_sri_frames", lambda *a, **k: [])
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0), bundle.warnings)
    assert payload["available"] is False
    assert payload["null_reason"] == "sri_unavailable"
    assert native is None


def test_run_writes_json_when_unavailable(tmp_path, monkeypatch):
    bundle = _Bundle([])
    monkeypatch.setattr(nf, "fetch_sri_frames", lambda *a, **k: [])
    out = tmp_path / "out"
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0))
    assert payload["available"] is False
    path = out / "nowcast.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("available", "null_reason", "source", "source_slot",
                "generated_at", "leads", "speed_cap_kmh", "motion",
                "frames_used"):
        assert key in data
    assert data["source"] == "DPC_SRI"
    assert not list(out.glob("*.webp"))     # niente immagini senza campo


# ---------------------------------------------------------------------------
# 2. trasporto: blob spostato di 1 cella
# ---------------------------------------------------------------------------
def test_transport_blob_one_cell(tmp_path):
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    clk = clock_mod.Clock.at_ms(T0 + 2 * STEP_MS)
    payload, native = nf.compute(bundle, cfg, clk, bundle.warnings)
    assert payload["available"] is True, payload
    assert payload["frames_used"] == 3
    assert payload["motion"] == "optical_flow"
    # il massimo del lead +15 (3 passi da 1 px) e' 3 colonne piu' a destra
    base = np.unravel_index(np.argmax(rasters[-1].data), rasters[-1].data.shape)
    lead = np.unravel_index(np.argmax(native[15]), native[15].shape)
    assert abs(int(lead[0]) - int(base[0])) <= 1
    assert 2 <= int(lead[1]) - int(base[1]) <= 4
    # e il campo previsto resta un blob (non si e' disintegrato)
    assert native[15].max() > 0.5 * rasters[-1].data.max()


def test_flow_synthetic_one_pixel_direction(tmp_path):
    """Il flusso stimato e' ~1 px/frame nel verso giusto (non 1/4)."""
    rasters, domain = _moving_frames(tmp_path, n=2, dx=1)
    a = nf._rain_array(rasters[0])[0]
    b = nf._rain_array(rasters[1])[0]
    u, v = nf.estimate_flow(a, b, domain & (a > 0.02))
    mask = domain & (a > 0.5)
    assert 0.8 <= float(np.median(u[mask])) <= 1.2
    assert abs(float(np.median(v[mask]))) <= 0.2


# ---------------------------------------------------------------------------
# 3. cap 110 km/h: sostituito dal vicinato, non azzerato
# ---------------------------------------------------------------------------
def test_cap_110_replaces_200_kmh_vector():
    # passo 5 min, pixel 1 km: 200 km/h = 16.667 px/step, 100 km/h = 8.333
    u = np.array([[16.667, 8.333]], dtype=np.float32)
    v = np.array([[0.0, 0.0]], dtype=np.float32)
    cu, cv, n = nf.cap_speed(u, v, 1.0, 300)
    assert n == 1                                    # uno sopra cap
    assert nf.SPEED_CAP_KMH == 110.0
    # sostituito dalla mediana del vicinato (l'unico vettore valido): NON 0
    assert cu[0, 0] == pytest.approx(8.333, abs=1e-3)
    assert cv[0, 0] == 0.0
    assert 8.2 <= cu[0, 1] <= 8.5                    # 100 km/h invariato


def test_cap_is_per_vector_not_global():
    u = np.zeros((2, 2), dtype=np.float32)
    v = np.zeros((2, 2), dtype=np.float32)
    u[0, 0] = 20.0           # 240 km/h
    u[1, 1] = 2.0            # 24 km/h
    cu, _cv, n = nf.cap_speed(u, v, 1.0, 300)
    assert n == 1
    assert cu[0, 0] == pytest.approx(2.0)            # dal vicino, non 0
    assert cu[1, 1] == 2.0


def test_cap_without_any_neighbor_stays_zero():
    """Nessun vicino valido (tutti NaN): 0 dichiarato, non un clamp a 110."""
    u = np.full((2, 2), np.nan, dtype=np.float32)
    v = np.full((2, 2), np.nan, dtype=np.float32)
    u[0, 0] = 30.0           # 360 km/h, isolato
    v[0, 0] = 0.0
    cu, cv, n = nf.cap_speed(u, v, 1.0, 300)
    assert n == 1
    assert cu[0, 0] == 0.0 and cv[0, 0] == 0.0


# ---------------------------------------------------------------------------
# 4. nessuna crescita di intensita'
# ---------------------------------------------------------------------------
def test_no_intensity_growth(tmp_path):
    rasters, _domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    observed_max = float(rasters[-1].data.max())
    for lead in nf.LEADS_MIN:
        assert float(native[lead].max()) <= observed_max + 1e-6
        for item in payload["leads"]:
            if item["lead_min"] == lead:
                assert item["stats"]["max_mmh"] <= observed_max + 0.01


# ---------------------------------------------------------------------------
# 5. maschera del dominio (mare/fuori copertura)
# ---------------------------------------------------------------------------
def test_domain_mask_respected(tmp_path):
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    assert payload["domain_mask"] == "latest_sri_valid_mask"
    for lead in nf.LEADS_MIN:
        assert float(native[lead][~domain].max(initial=0.0)) == 0.0


# ---------------------------------------------------------------------------
# 5b. mediana: una coppia senza stima NON vota 0
# ---------------------------------------------------------------------------
def test_median_ignores_missing_pairs():
    a = np.array([[np.nan, 2.0]], dtype=np.float32)
    b = np.array([[np.nan, 2.5]], dtype=np.float32)
    c = np.array([[1.0, 2.2]], dtype=np.float32)
    med = nf._nan_median_stack([a, b, c])
    assert med[0, 0] == 1.0                  # una sola stima -> quella
    assert med[0, 1] == pytest.approx(2.2)   # mediana delle tre
    allnan = nf._nan_median_stack(
        [np.full((1, 2), np.nan, np.float32), a])
    assert np.isnan(allnan[0, 0])            # tutti mancanti -> mancante
    assert allnan[0, 1] == 2.0


# ---------------------------------------------------------------------------
# 5c. eco comparsa SOLO nell'ultimo frame: eredita il moto del vicinato
# ---------------------------------------------------------------------------
def _new_echo_frames(tmp_path, rows=200, cols=200):
    """Blob in moto (1 px/frame) + una striscia che compare solo nel +2."""
    domain = _disk_domain(rows, cols, 100, 100, 95)
    rasters = []
    for k in range(3):
        field = _blob(rows, cols, 100, 60 + k, sigma=3.0, amp=20.0)
        if k == 2:
            field = field.copy()
            field[80:121, 76:82] = 8.0     # nuova eco, adiacente al blob
        rasters.append(_write_frame(tmp_path, "e%d.tif" % k, field, domain,
                                    T0 + k * STEP_MS))
    return rasters, domain


def test_new_echo_gets_motion_from_neighborhood(tmp_path):
    rasters, domain = _new_echo_frames(tmp_path)
    bundle = _Bundle(rasters)
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    u, v = native["_motion"]
    rain = domain & (rasters[-1].data > nf.RAIN_MIN_MMH)
    strip = np.zeros(rain.shape, dtype=bool)
    strip[80:121, 76:82] = rain[80:121, 76:82]
    assert int(strip.sum()) > 50
    # non e' ferma: eredita il moto del vicinato (~1 px/5 min)
    assert float(np.median(u[strip])) > 0.5
    assert abs(float(np.median(v[strip]))) < 0.3
    # nessun pixel della striscia con vettore 0 (ha vicini validi entro 10 px)
    assert int((strip & (u == 0) & (v == 0)).sum()) == 0
    # in questo caso la striscia ha una stima propria (non serve riempimento),
    # ma NESSUN pixel di pioggia resta senza vettore
    assert payload["motion_unfilled_pixels"] == 0
    assert payload["motion_null_reason"] is None
    assert payload["speed_cap_mode"] == "replaced_by_neighbor_median"


def test_rain_only_in_last_frame_is_missing_not_zero(tmp_path):
    """La non-stima e' NaN (mancante), NON 0: il test della causa misurata.

    Un'eco che compare solo nell'ultimo frame e' lontana da ogni altra
    pioggia: nessuna coppia ha gradiente la' e la mediana non deve
    restituire 0 (che congelerebbe l'eco per tutto l'orizzonte)."""
    domain = np.ones((200, 200), dtype=bool)
    frames = []
    for k in range(3):
        data = _blob(200, 200, 100, 60 + k, sigma=4.0, amp=20.0)
        if k == 2:
            data = data.copy()
            data[80:121, 150:171] = 8.0        # comparsa, lontana dal blob
        frames.append((data.astype(np.float32), domain, T0 + k * STEP_MS))
    motion = nf.estimate_motion(frames, [])
    assert motion is not None
    far = (slice(80, 121), slice(150, 171))
    assert np.isnan(motion["u"][far]).all()    # mancante, non 0
    assert np.isnan(motion["v"][far]).all()
    # il blob in moto invece e' stimato (~1 px/5min)
    near = (slice(95, 106), slice(58, 66))
    assert float(np.nanmedian(motion["u"][near])) > 0.5
    assert abs(float(np.nanmedian(motion["v"][near]))) < 0.3


def test_fill_motion_uses_neighbors_and_declares_isolated():
    u = np.full((20, 20), np.nan, dtype=np.float32)
    v = np.full((20, 20), np.nan, dtype=np.float32)
    u[:, 0:4] = 2.0
    v[:, 0:4] = 0.0
    missing = np.zeros((20, 20), dtype=bool)
    missing[:, 4:7] = True        # a <=3 px da un vettore valido
    missing[:, 15:20] = True      # isolati (12 px): restano mancanti
    cu, cv, info = nf.fill_motion(u, v, missing, valid=np.isfinite(u),
                                  max_radius=3)
    assert np.allclose(cu[:, 4:7], 2.0) and np.allclose(cv[:, 4:7], 0.0)
    assert np.isnan(cu[:, 15:20]).all()
    assert info["filled"] == 20 * 3
    assert info["unfilled"] == 20 * 5


# ---------------------------------------------------------------------------
# 6. output: JSON + 3 WebP georeferenziati
# ---------------------------------------------------------------------------
def test_output_json_and_webp(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["available"] is True
    data = json.loads((out / "nowcast.json").read_text(encoding="utf-8"))
    # 15 frame a passo crescente: 5..45 ogni 5, 60/75/90 ogni 15, 120/150/180
    expected = list(range(5, 46, 5)) + [60, 75, 90, 120, 150, 180]
    assert [l["lead_min"] for l in data["leads"]] == expected
    assert data["leads_total"] == 15
    assert data["official_leads"] == [15, 30, 60]
    assert [l["lead_min"] for l in data["leads"] if l["official"]] == [15, 30, 60]
    assert [l["step_min"] for l in data["leads"]] == (
        [5] * 9 + [15] * 3 + [30] * 3)
    assert data["low_reliability_over_min"] == 90
    assert data["grid"]["crs"] == "EPSG:3857"
    assert len(data["grid"]["bounds"]) == 4
    assert data["grid"]["resolution_m"] == nf.OUTPUT_RES_M
    assert data["motion_algorithm"] == "lucas_kanade_pyramidal"
    assert data["legend"][0]["mmh"] == 1.0
    assert [l["mmh"] for l in data["legend"]] == [1.0, 5.0, 10.0, 20.0, 50.0,
                                                  100.0]
    for lead in nf.LEADS_MIN:
        p = out / nf.IMAGE_NAMES[lead]
        assert p.exists() and p.stat().st_size > 0
        assert data["leads"][[l["lead_min"] for l in data["leads"]].index(lead)][
            "image"] == nf.IMAGE_NAMES[lead]
        assert p.read_bytes()[:4] == b"RIFF"      # WebP container
    # 15 file distinti (010, 020, ... non sovrascrivono 005)
    assert len(list(out.glob("nowcast_lead*.webp"))) == 15
    assert nf.IMAGE_NAMES[5] == "nowcast_lead005.webp"
    assert nf.IMAGE_NAMES[60] == "nowcast_lead060.webp"
    assert nf.IMAGE_NAMES[180] == "nowcast_lead180.webp"


def test_frames_are_cumulative_and_coherent(tmp_path):
    """Con `transport_mode="steps"` (A/B) il +10 e' il +5 avanzato di 1 passo.

    Il default di produzione e' invece lo spostamento COMPOSTO: qui si fissa
    esplicitamente il vecchio schema per non perderne la copertura.
    """
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    cfg["nowcast_transport_mode"] = "steps"
    payload, native = nf.compute(bundle, cfg,
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    # il campo di moto EFFETTIVO del run (dopo cap, riempimento, smoothing e
    # estensione a tutto il dominio)
    u, v = native["_motion"]
    scale = native["_mass_scale"]

    def expect(steps, lead):
        """Trasporto cumulativo, con la scala di massa DICHIARATA (cap)."""
        adv = nf.semi_lagrangian(native[5], u, v, steps, domain)
        return adv * np.float32(scale[lead] / scale[5])

    assert np.allclose(expect(1, 10), native[10], rtol=1e-5, atol=1e-4)
    assert np.allclose(expect(3, 20), native[20], rtol=1e-5, atol=1e-4)
    # il +180 e' il +5 avanzato di altri 35 passi (36 totali), non un salto
    assert np.allclose(expect(35, 180), native[180], rtol=1e-5, atol=1e-4)


def test_composite_shift_is_one_interpolation(tmp_path):
    """Default: ogni lead e' UNA interpolazione sullo spostamento totale.

    Il campo a +L e' il frame osservato campionato in x - v*L, non il
    risultato di L/5 ricampionamenti: la ripetizione del filtro bilineare e'
    la sorgente delle bande periodiche allineate al moto.
    """
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    assert cfg.get("nowcast_transport_mode") == "single"
    payload, native = nf.compute(bundle, cfg,
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    assert payload["transport_mode"] == "single"
    assert payload["transport_order"] == 3          # cubico (letteratura)
    latest = nf._rain_array(rasters[-1])[0]
    u, v = native["_motion"]
    scale = native["_mass_scale"]
    dom = np.asarray(native["_domain"], dtype=bool)
    for lead in (10, 20, 180):
        steps = lead // nf.STEP_MIN
        smp, inside = nf._warp(latest, -(u * np.float32(steps)),
                               -(v * np.float32(steps)),
                               order=payload["transport_order"])
        one = np.where(inside & dom, smp, 0.0).astype(np.float32)
        if scale[lead] != 1.0:
            one = one * np.float32(scale[lead])
        assert np.allclose(one, native[lead], rtol=1e-5, atol=1e-4), lead
    # il composto e' diverso dal sequenziale a +30 (6 passi)
    seq = nf.semi_lagrangian(latest, u, v, 6, dom)
    assert not np.allclose(seq, native[30], rtol=1e-3, atol=1e-3)


def test_step_min_for_growing_leads():
    """Il passo pubblicato e' la distanza dal frame precedente."""
    assert nf.step_min_for(5) == 5
    assert nf.step_min_for(45) == 5
    assert nf.step_min_for(60) == 15
    assert nf.step_min_for(90) == 15
    assert nf.step_min_for(120) == 30
    assert nf.step_min_for(180) == 30
    # override dei backtest: il passo si ricava dalla lista, non da una tabella
    assert nf.step_min_for(180, (5, 30, 90, 180)) == 90


def test_reliability_by_lead_from_measured_file(tmp_path):
    """L'affidabilita' e' quella MISURATA nel file: nessun numero inventato."""
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / nf.RELIABILITY_FILE).write_text(json.dumps({
        "schema": "meteorisk.nowcast_reliability/1",
        "measured_on": "2026-11-25",
        "sample": {"days": 12, "cases": 24},
        "horizons": {
            "90": {"lead_min": 90, "cases": 24, "level": "media",
                   "nowcast_csi": 0.31, "persistence_csi": 0.29,
                   "csi_delta": 0.02, "nowcast_fss": 0.40,
                   "persistence_fss": 0.38, "fss_delta": 0.02,
                   "nowcast_beats_persistence": True,
                   "low_reliability": False},
            "180": {"lead_min": 180, "cases": 24, "level": "bassa",
                    "nowcast_csi": 0.18, "persistence_csi": 0.22,
                    "csi_delta": -0.04, "nowcast_fss": 0.25,
                    "persistence_fss": 0.30, "fss_delta": -0.05,
                    "nowcast_beats_persistence": False,
                    "low_reliability": True},
        },
    }), encoding="utf-8")
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    rel = payload["reliability_by_lead"]
    assert rel["horizons"]["180"]["nowcast_csi"] == 0.18
    by_lead = {l["lead_min"]: l for l in payload["leads"]}
    assert by_lead[15]["reliability"] is None          # orizzonte non misurato
    assert by_lead[180]["reliability"]["nowcast_csi"] == 0.18
    assert by_lead[180]["reliability"]["low_reliability"] is True
    assert by_lead[90]["reliability"]["level"] == "media"


def test_reliability_missing_is_declared_null(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["reliability_by_lead"] is None
    assert all(l["reliability"] is None for l in payload["leads"])
    assert payload["low_reliability_over_min"] == 90


def test_one_missing_frame_does_not_break_the_rest(tmp_path, monkeypatch):
    """Un frame che non si scrive lascia una lacuna DICHIARATA, non un buco."""
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"
    original = nf.write_webp

    def _maybe_fail(path, field, quality=nf.WEBP_QUALITY):
        if path.endswith("nowcast_lead020.webp"):
            raise RuntimeError("encoder non disponibile per il frame 20")
        return original(path, field, quality=quality)

    monkeypatch.setattr(nf, "write_webp", _maybe_fail)
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["available"] is True          # gli altri 14 restano validi
    by_lead = {l["lead_min"]: l for l in payload["leads"]}
    assert by_lead[20]["image"] is None
    assert "RuntimeError" in by_lead[20]["image_error"]
    assert by_lead[15]["image"] == nf.IMAGE_NAMES[15]
    assert len(list(out.glob("nowcast_lead*.webp"))) == 14
    assert not (out / nf.IMAGE_NAMES[20]).exists()
    data = json.loads((out / "nowcast.json").read_text(encoding="utf-8"))
    assert data["output_bytes"]["image_errors"]
    assert any("non scritti" in w for w in bundle.warnings)


def test_encoder_failure_degrades_but_writes_json(tmp_path, monkeypatch):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"

    def _boom(*_a, **_k):
        raise RuntimeError("webp encoder non disponibile")

    monkeypatch.setattr(nf, "write_webp", _boom)
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    # tutti i frame falliti -> prodotto non mostrabile, dichiarato
    assert payload["available"] is False
    assert payload["null_reason"] == "image_write_failed:all_frames"
    assert all(l["image"] is None for l in payload["leads"])
    assert (out / "nowcast.json").exists()


def test_beats_persistence_comes_from_verification_file(tmp_path):
    """Il verdetto NON e' calcolato nel run live: viene dal file del replay."""
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / nf.VERIFICATION_FILE).write_text(json.dumps({
        "status": "ok", "beats_persistence": False,
        "cases_with_rain": 24, "null_reason": None,
    }), encoding="utf-8")
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["beats_persistence"] is False
    data = json.loads((out / "nowcast.json").read_text(encoding="utf-8"))
    assert data["beats_persistence"] is False
    assert data["verification"]["cases_with_rain"] == 24


def test_verification_series_not_embedded_in_nowcast_json(tmp_path):
    """La serie (schema 2) resta nel file: nowcast.json ne porta solo il conteggio.

    Senza questo taglio il JSON committato ogni 10 minuti cresce con la serie
    (misurato in produzione: 67 kB al primo run)."""
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / nf.VERIFICATION_FILE).write_text(json.dumps({
        "schema": "meteorisk.nowcast_verification/2",
        "status": "ok", "beats_persistence": True, "cases_total": 49,
        "series": {"schema": "meteorisk.nowcast_verification_series/1",
                   "retention_days": 7,
                   "counts": {"rows": 63, "slots": 8},
                   "rows": [{"lead_min": 15, "threshold_mmh": 0.5}]},
    }), encoding="utf-8")
    payload = nf.run(bundle, _config(tmp_path), str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    ver = payload["verification"]
    assert "series" not in ver
    assert ver["series_summary"]["rows"] == 63
    assert ver["series_summary"]["slots"] == 8
    assert ver["series_summary"]["retention_days"] == 7
    assert payload["beats_persistence"] is True
    data = json.loads((out / "nowcast.json").read_text(encoding="utf-8"))
    assert "series" not in data["verification"]


# ---------------------------------------------------------------------------
# 6b. palette ALLINEATA alla scala SRI del client (stessi RGB)
# ---------------------------------------------------------------------------
# Copia letterale di DPC_SRI_STOPS (mri-light-1.3.0.77.html, righe 10983-10990).
CLIENT_DPC_SRI_STOPS = ((1, (0, 85, 136)), (5, (0, 163, 224)),
                        (10, (136, 221, 238)), (20, (255, 238, 0)),
                        (50, (255, 170, 0)), (100, (193, 0, 0)))


def test_palette_is_the_client_sri_scale():
    assert nf.PALETTE == CLIENT_DPC_SRI_STOPS
    for mmh, rgb in CLIENT_DPC_SRI_STOPS:
        px = nf._rgba(np.array([[np.float32(mmh)]]))[0, 0]
        assert [int(px[0]), int(px[1]), int(px[2])] == list(rgb)
    # code dichiarate: sotto 1 mm/h -> primo colore, sopra 100 -> ultimo
    low = nf._rgba(np.array([[np.float32(0.5)]]))[0, 0]
    high = nf._rgba(np.array([[np.float32(200.0)]]))[0, 0]
    assert [int(low[0]), int(low[1]), int(low[2])] == [0, 85, 136]
    assert [int(high[0]), int(high[1]), int(high[2])] == [193, 0, 0]


def test_palette_between_stops_is_linear_like_the_client():
    # 2 mm/h: interpolazione 1->5 (t=0,25) -> (0,104.5,158); il client usa
    # Math.round (104.5 -> 105), quindi il server deve dare lo stesso.
    px = nf._rgba(np.array([[np.float32(2.0)]]))[0, 0]
    assert [int(px[0]), int(px[1]), int(px[2])] == [0, 105, 158]


# ---------------------------------------------------------------------------
# 7. main.py continua se il modulo fallisce
# ---------------------------------------------------------------------------
def _pipeline_config():
    cfg = load_config()
    cfg["opera"] = dict(cfg.get("opera") or {}, enabled=False)
    cfg["doppler"] = dict(cfg.get("doppler") or {}, enabled=False)
    cfg["phase2"] = dict(cfg.get("phase2") or {}, enabled=False)
    cfg["storm"] = dict(cfg.get("storm") or {}, enabled=False)
    cfg["convective_structure"] = dict(
        cfg.get("convective_structure") or {}, enabled=False)
    cfg["multiscale"] = dict(cfg.get("multiscale") or {},
                             publish_descriptors=False)
    cfg["taxonomy"] = dict(cfg.get("taxonomy") or {}, enabled=False)
    cfg["coasting"] = dict(cfg.get("coasting") or {})
    return cfg


def test_main_continues_when_nowcast_raises(tmp_path, monkeypatch):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    cfg = _pipeline_config()

    def _window(*_a, **_k):
        slots = [int(r.time_ms) for r in rasters]
        return rasters, [], int(rasters[-1].time_ms), {}, slots

    monkeypatch.setattr(main_mod.fetch, "fetch_window", _window)

    def _boom(*_a, **_k):
        raise RuntimeError("guasto simulato del nowcast")

    monkeypatch.setattr(nf, "run", _boom)
    out = tmp_path / "out"
    bundle, rc = main_mod._run_pipeline(
        cfg, str(out), "VMI", 3, False, history_dir=None,
        clock=clock_mod.Clock.live())
    assert rc in (main_mod._OK, main_mod._DEGRADED)
    assert any("nowcast layer failed" in w for w in bundle.warnings)
    assert getattr(bundle, "nowcast", {}).get("available") is False
    assert (out / "latest.json").exists()


# ---------------------------------------------------------------------------
# 8. verifica: soglie di pubblicazione e regola di decisione
# ---------------------------------------------------------------------------
def test_aggregate_hides_scores_on_small_sample():
    from nowcast_replay import aggregate
    case = {"status": "ok", "anchor": "x",
            "leads": {"15|0.5": {"nowcast": [10, 2, 3, 100],
                                 "persistence": [8, 4, 5, 98],
                                 "rain_pixels": 12},
                      "30|0.5": {"nowcast": [9, 3, 4, 99],
                                 "persistence": [7, 5, 6, 97],
                                 "rain_pixels": 12},
                      "60|0.5": {"nowcast": [8, 4, 5, 98],
                                 "persistence": [6, 6, 7, 96],
                                 "rain_pixels": 12}}}
    res = aggregate([case] * 3, min_cases=5, publish_min_cases=20,
                    min_rain_pixels=10000)
    assert res["beats_persistence"] is None
    assert res["status"] == "not_evaluable"
    big = aggregate([case] * 25, min_cases=5, publish_min_cases=20,
                    min_rain_pixels=10)
    assert big["beats_persistence"] is True
    assert big["status"] == "ok"
    assert big["leads"]["15|0.5"]["nowcast"]["csi"] == pytest.approx(
        10 / 15.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 12. trasporto: un solo warp, texture e posizione (regressione del pettine)
# ---------------------------------------------------------------------------
def _texture_field(h=192, w=192, seed=3):
    """Campo sintetico con struttura e texture a banda limitata."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    f = np.zeros((h, w), np.float32)
    for _ in range(8):
        cx, cy = rng.uniform(40, h - 40, 2)
        s = rng.uniform(6, 16)
        f += rng.uniform(3, 10) * np.exp(
            -((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * s * s))
    noise = ndimage.gaussian_filter(rng.standard_normal((h, w)), 2.5)
    f += (noise / noise.std() * 0.25) * (f > 1)
    return np.clip(f, 0, None).astype(np.float32)


def _hf_energy(a):
    from scipy import ndimage as ndi
    return float(np.var(ndi.laplace(a)))


def _nyquist_fraction(a):
    h, w = a.shape
    p = np.abs(np.fft.fft2(a - a.mean())) ** 2
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    k = np.hypot(fx, fy)
    tot = p[(k > 0.02) & (k <= 0.5)].sum()
    return float(p[(k >= 0.40) & (k <= 0.5)].sum() / max(tot, 1e-30))


def _centroid(a):
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    tot = float(a.sum())
    return (float((a * yy).sum() / tot), float((a * xx).sum() / tot))


def test_composite_shift_keeps_texture_and_position():
    """Un warp cubico conserva HF e posizione; la catena di passi no.

    Misura su un campo sintetico a banda limitata (nessuna rete): la catena
    di 6/12 ricampionamenti bilineari perde il 82-87% dell'energia HF, il
    warp unico cubico il 12-29%. La deriva del baricentro e' < 0,5 px.
    """
    base = _texture_field()
    dom = np.ones(base.shape, dtype=bool)
    h, w = base.shape
    u = np.full((h, w), 2.15, np.float32)
    v = np.full((h, w), -1.30, np.float32)
    c0 = _centroid(base)
    hf0 = _hf_energy(base)
    ny0 = _nyquist_fraction(base)
    for steps in (6, 12):
        one, inside = nf._warp(base, -(u * steps), -(v * steps), order=3)
        one = np.where(inside & dom, one, 0.0)
        seq = nf.semi_lagrangian(base, u, v, steps, dom)
        # 1) niente deriva da arrotondamento: il baricentro si sposta quanto
        #    il vettore (entro mezzo pixel)
        exp = (steps * math.hypot(2.15, 1.30))
        c1 = _centroid(one)
        drift = math.hypot(c1[1] - c0[1], c1[0] - c0[0])
        assert abs(drift - exp) < 0.5, (steps, drift, exp)
        # 2) il warp unico conserva la texture, la catena la distrugge
        assert _hf_energy(one) / hf0 > 0.60, steps
        assert _hf_energy(seq) / hf0 < 0.25, steps
        ratio_one = _nyquist_fraction(one) / max(ny0, 1e-12)
        ratio_seq = _nyquist_fraction(seq) / max(ny0, 1e-12)
        # 3) nessun aliasing: il forecast non supera 2x l'osservato
        assert ratio_one < 2.0, (steps, ratio_one)
        # 4) la catena ha perso anche la banda di Nyquist (campo "mousse")
        assert ratio_seq < 0.5 * ratio_one, (steps, ratio_seq, ratio_one)
        # 5) il composto e' diverso dal sequenziale ma non un salto di fase
        assert not np.allclose(one, seq, rtol=1e-3, atol=1e-3)
        assert np.abs(one - seq)[dom].mean() < 0.05 * base[dom].mean()
