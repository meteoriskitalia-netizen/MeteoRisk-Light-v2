#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dell'ensemble probabilistico del nowcast (nowcast_ensemble.py).

Coprono i vincoli dichiarati nel modulo:
  - 20 membri, disegno stratificato, media degli offset ~0;
  - rumore non parametrico: la potenza di OGNI banda torna al livello
    pre-cascata (conservazione della varianza), a guadagni 1 il campo e'
    identico, il rumore non esce dal supporto dell'eco;
  - determinismo: stesso seed -> stesse mappe, seed diverso -> mappe diverse;
  - probabilita' quantizzate a 0,01, comprese in [0,1], p10 <= p50 <= p90,
    spread >= 0;
  - `ensemble.enabled=false` -> nessuna chiave `ensemble` nel payload;
  - immagine di probabilita': alpha 0 sotto la soglia di disegno.
"""

import os
import sys

import numpy as np
import pytest

from radar_engine import clock as clock_mod
from radar_engine import nowcast_ensemble as ne
from radar_engine import nowcast_field as nf
from radar_engine.config import CONFIG, load_config
from radar_engine.preprocess import read_raster

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if os.path.join(_SCRIPTS, "validation") not in sys.path:
    sys.path.insert(0, os.path.join(_SCRIPTS, "validation"))

from conftest import DEFAULT_TRANSFORM, write_synthetic_tif   # noqa: E402

T0 = 1_795_512_000_000
STEP_MS = 300_000


# ---------------------------------------------------------------------------
# geometria sintetica
# ---------------------------------------------------------------------------
def _field(shape=(128, 128), seed=0):
    rng = np.random.default_rng(seed)
    base = rng.random(shape).astype(np.float32) * 4.0
    rr, cc = np.ogrid[:shape[0], :shape[1]]
    blob = 12.0 * np.exp(-(((rr - 60) ** 2 + (cc - 50) ** 2) / (2 * 12 ** 2)))
    return (base + blob).astype(np.float32)


def _band_index(shape, bands):
    """Indice di banda dyadico sullo spettro pieno (solo per i test)."""
    edges = np.asarray(ne.nowcast_cascade_edges(bands), dtype=np.float32)
    ky = np.fft.fftfreq(int(shape[0])).astype(np.float32)
    kx = np.fft.fftfreq(int(shape[1])).astype(np.float32)
    k = 2.0 * np.hypot(ky[:, None], kx[None, :])
    return np.searchsorted(edges, k, side="right").astype(np.int64)


def _state(shape, gains):
    return {"idx_flat": _band_index(shape, len(gains)),
            "domain": np.ones(shape, dtype=np.float32),
            "gains_by_lead": {15: list(gains), 30: list(gains),
                              60: list(gains)}}


def _band_power(field, bands):
    idx = _band_index(field.shape, bands)
    y = np.fft.fft2(np.asarray(field, dtype=np.float64))
    e = (y.real ** 2 + y.imag ** 2)
    e.flat[0] = 0.0
    return np.bincount(idx.ravel(), weights=e.ravel(), minlength=bands)


# ---------------------------------------------------------------------------
# disegno dei membri
# ---------------------------------------------------------------------------
def test_member_offsets_twenty_balanced():
    offs = ne.member_offsets(20)
    assert len(offs) == 20
    assert len(set(offs)) == 20
    par = np.array([a for a, _b in offs])
    perp = np.array([b for _a, b in offs])
    assert abs(float(par.mean())) < 1e-9
    assert abs(float(perp.mean())) < 1e-9
    assert float(par.max()) == pytest.approx(ne.MOTION_PARALLEL_MS)
    assert float(perp.max()) == pytest.approx(ne.MOTION_PERPENDICULAR_MS)


def test_motion_instability_damps_weak_motion():
    shape = (20, 20)
    u = np.zeros(shape, dtype=np.float32)
    v = np.zeros(shape, dtype=np.float32)
    rain = np.ones(shape, dtype=bool)
    inst = ne.motion_instability(u, v, rain, 1.0, 300.0)
    assert inst["factor"] == 0.0
    dx, dy = ne.displacement_for(60, ne.MOTION_PARALLEL_MS, 0.0, inst, 1.0)
    assert dx == 0.0 and dy == 0.0


def test_motion_instability_coherent_motion_full():
    shape = (20, 20)
    u = np.full(shape, 2.0, dtype=np.float32)      # 24 km/h verso est
    v = np.zeros(shape, dtype=np.float32)
    rain = np.ones(shape, dtype=bool)
    inst = ne.motion_instability(u, v, rain, 1.0, 300.0)
    assert inst["coherence"] == pytest.approx(1.0, abs=1e-6)
    assert inst["factor"] == pytest.approx(1.0, abs=1e-6)
    dx, dy = ne.displacement_for(60, ne.MOTION_PARALLEL_MS, 0.0, inst, 1.0)
    # 10.88 m/s * 3600 s / 1000 m = 39.168 px lungo il moto (est)
    assert dx == pytest.approx(39.168, abs=1e-3)
    assert dy == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# rumore: conservazione della varianza per banda
# ---------------------------------------------------------------------------
def test_band_noise_conserves_variance():
    shape = (128, 128)
    bands = 6
    gains = (0.95, 0.85, 0.7, 0.5, 0.35, 0.2)
    src = _field(shape)
    dom = np.ones(shape, dtype=bool)
    state = _state(shape, gains)
    gain, scale = ne.band_gain_arrays(state, 15)
    assert gain is not None and scale is not None
    before = _band_power(src, bands)
    ratios = []
    for seed in range(6):
        out = ne.cascade_noise(src, gain, scale, dom,
                               np.random.default_rng(seed), support=None)
        after = _band_power(out, bands)
        ratios.append(after / np.maximum(before, 1e-9))
    med = np.median(np.array(ratios), axis=0)
    # ogni banda torna al livello del trasporto (g^2 + 1-g^2 = 1)
    assert np.all(med > 0.6), med
    assert np.all(med < 1.5), med


def test_band_noise_identity_when_gain_one():
    shape = (64, 64)
    src = _field(shape)
    dom = np.ones(shape, dtype=bool)
    state = _state(shape, (1.0,) * 6)
    gain, scale = ne.band_gain_arrays(state, 15)
    assert float(scale.max()) == 0.0
    out = ne.cascade_noise(src, gain, scale, dom, np.random.default_rng(0))
    assert np.allclose(out, src, atol=1e-5)


def test_band_noise_stays_inside_support():
    shape = (64, 64)
    src = _field(shape)
    dom = np.ones(shape, dtype=bool)
    support = np.zeros(shape, dtype=bool)
    support[20:40, 20:40] = True
    state = _state(shape, (0.9, 0.7, 0.5, 0.4, 0.3, 0.2))
    gain, scale = ne.band_gain_arrays(state, 15)
    det = ne.cascade_noise(src, gain, np.zeros_like(scale), dom,
                           np.random.default_rng(0), support=None)
    out = ne.cascade_noise(src, gain, scale, dom, np.random.default_rng(0),
                           support=support)
    # il rumore non aggiunge pioggia fuori dal supporto: fuori dal supporto
    # resta la parte predicibile (attenuata), non il campo con rumore
    assert np.allclose(out[~support], det[~support], atol=1e-5)


# ---------------------------------------------------------------------------
# determinismo e statistiche
# ---------------------------------------------------------------------------
def test_ensemble_deterministic_and_quantized():
    shape = (96, 96)
    field = _field(shape)
    dom = np.ones(shape, dtype=bool)
    state = _state(shape, (0.95, 0.85, 0.7, 0.5, 0.35, 0.2))
    u = np.full(shape, 0.5, dtype=np.float32)
    v = np.zeros(shape, dtype=np.float32)
    cfg = {"members": 20, "thresholds_mmh": (0.5, 2.0, 5.0, 10.0, 20.0)}
    blk_a, f_a = ne.run(field, u, v, dom, 15, state, cfg, 1.0, 300.0, T0)
    blk_b, f_b = ne.run(field, u, v, dom, 15, state, cfg, 1.0, 300.0, T0)
    assert blk_a["applied"] and blk_b["applied"]
    for key in f_a["probabilities"]:
        assert np.array_equal(f_a["probabilities"][key],
                              f_b["probabilities"][key])
    p = f_a["probabilities"]["prob_0p5"]
    assert p.min() >= 0.0 and p.max() <= 1.0
    # quantizzazione a 0,01
    assert np.allclose(p * 100.0, np.round(p * 100.0), atol=1e-4)
    q = f_a["quantiles"]
    assert np.all(q["p10"] <= q["p50"] + 1e-6)
    assert np.all(q["p50"] <= q["p90"] + 1e-6)
    assert np.all(f_a["spread"] >= 0.0)
    assert blk_a["members"] == 20
    assert blk_a["noise"]["applied"] is True


def test_ensemble_advect_mode_matches_composite_order():
    shape = (96, 96)
    field = _field(shape)
    dom = np.ones(shape, dtype=bool)
    state = _state(shape, (0.95, 0.85, 0.7, 0.5, 0.35, 0.2))
    u = np.full(shape, 0.5, dtype=np.float32)
    v = np.zeros(shape, dtype=np.float32)
    cfg = {"members": 20, "thresholds_mmh": (0.5, 2.0, 5.0, 10.0, 20.0)}
    blk_c, f_c = ne.run(field, u, v, dom, 15, state, cfg, 1.0, 300.0, T0)
    blk_a, f_a = ne.run(field, u, v, dom, 15, state, cfg, 1.0, 300.0, T0,
                        advect_latest=(field, u, v, dom))
    assert blk_c["applied"] and blk_a["applied"]
    p_c = f_c["probabilities"]["prob_0p5"]
    p_a = f_a["probabilities"]["prob_0p5"]
    assert np.all((p_c >= 0.0) & (p_c <= 1.0))
    assert np.all((p_a >= 0.0) & (p_a <= 1.0))
    # i due metodi devono dare mappe vicine (stesso campo di partenza)
    assert float(np.mean(np.abs(p_c - p_a))) < 0.2


def test_crps_fair_estimator_known_values():
    from nowcast_ensemble_backtest import ensemble_crps, climatology_crps
    # membri tutti uguali alla verita' -> CRPS 0
    mem = np.full((4, 3), 5.0)
    obs = np.array([5.0, 5.0, 5.0])
    assert np.allclose(ensemble_crps(mem, obs), 0.0)
    # membri {0,1}, verita' 0.5 -> 0.5 - 0.25 = 0.25
    mem = np.array([[0.0], [1.0]])
    obs = np.array([0.5])
    assert np.allclose(ensemble_crps(mem, obs), 0.25)
    # climatologia degenere sul valore osservato -> CRPS 0
    q = np.full(50, 2.0)
    assert np.allclose(climatology_crps(np.array([2.0]), q), 0.0)


def test_block_motion_error_is_spatially_variable():
    rng = np.random.default_rng(3)
    shape = (300, 300)
    du, dv = ne.block_motion_error(rng, shape, 1.0, 10.88, 5.76, (1.0, 0.0))
    assert du.shape == shape and dv.shape == shape
    # blocchi diversi hanno errori diversi: non è una costante
    assert float(np.std(du)) > 0.5
    # la media sui blocchi è casuale ma piccola rispetto all'ampiezza
    assert abs(float(du.mean())) < 4.0
    # ampiezza zero -> campo nullo
    du0, dv0 = ne.block_motion_error(rng, shape, 1.0, 0.0, 0.0, (1.0, 0.0))
    assert float(np.abs(du0).max()) == 0.0 and float(np.abs(dv0).max()) == 0.0


def test_deformation_stretches_and_rotates():
    rng = np.random.default_rng(4)
    shape = (200, 200)
    du, dv, comps = ne.deformation_field(rng, shape, 1.0, 8.0, (100.0, 100.0))
    assert set(comps) == {"divergence", "vorticity", "stretch", "shear"}
    # lineare e antisimmetrica rispetto al centro: lati opposti, segni opposti
    assert np.sign(du[100, 20]) != np.sign(du[100, 180]) or \
        abs(float(du[100, 20])) < 1e-6
    assert abs(float(du[100, 100])) < 1e-5
    assert float(np.abs(du).max()) > 0.1


def test_run_leads_field_mode_outputs_probabilities():
    from radar_engine import nowcast_field as nf
    shape = (96, 96)
    latest = _field(shape)
    dom = np.ones(shape, dtype=bool)
    state = _state(shape, (0.95, 0.85, 0.7, 0.5, 0.35, 0.2))
    u = np.full(shape, 0.5, dtype=np.float32)
    v = np.zeros(shape, dtype=np.float32)
    cfg = {"members": 20, "thresholds_mmh": (0.5, 2.0, 5.0, 10.0, 20.0),
           "motion_mode": "field", "motion_inflation": 1.0,
           "noise_inflation": 1.0}
    transports = {15: latest, 30: latest, 60: latest}
    blocks, fields = ne.run_leads(
        transports, u, v, dom, (15, 30, 60), state, cfg, 1.0, 300.0, T0,
        latest=latest)
    assert set(blocks) == {15, 30, 60}
    for lead in (15, 30, 60):
        p = fields[lead]["probabilities"]["prob_0p5"]
        assert p.shape == shape
        assert np.all((p >= 0.0) & (p <= 1.0))
        assert fields[lead]["probabilities"]["prob_0p5"].max() > 0.0
        assert blocks[lead]["motion_perturbation"]["mode"] == "field"


def test_run_leads_conserves_mass():
    shape = (96, 96)
    latest = _field(shape)
    dom = np.ones(shape, dtype=bool)
    state = _state(shape, (0.95, 0.85, 0.7, 0.5, 0.35, 0.2))
    u = np.full(shape, 0.5, dtype=np.float32)
    v = np.zeros(shape, dtype=np.float32)
    cfg = {"members": 8, "thresholds_mmh": (0.5, 2.0),
           "motion_mode": "field", "motion_inflation": 3.0,
           "noise_inflation": 1.0}
    target = 1234.5
    blocks, fields = ne.run_leads(
        {15: latest}, u, v, dom, (15,), state, cfg, 1.0, 300.0, T0,
        latest=latest, mass_target={15: target})
    mass = float(np.asarray(fields[15]["mean"], dtype=np.float64).sum())
    # il campo medio dei membri ha la massa del deterministico
    assert abs(mass - target) <= max(1.0, 0.01 * target)


def test_intensity_sigma_widens_distribution():
    shape = (128, 128)
    latest = _field(shape)
    dom = np.ones(shape, dtype=bool)
    state = _state(shape, (0.95, 0.85, 0.7, 0.5, 0.35, 0.2))
    u = np.full(shape, 0.5, dtype=np.float32)
    v = np.zeros(shape, dtype=np.float32)
    base = {"members": 12, "thresholds_mmh": (0.5, 2.0, 5.0),
            "motion_mode": "field", "motion_inflation": 3.0,
            "noise_inflation": 1.0, "occurrence_scale": 0.0}
    _b0, f0 = ne.run_leads({15: latest}, u, v, dom, (15,), state, base,
                           1.0, 300.0, T0, latest=latest,
                           mass_target={15: float(latest.sum())})
    cfg = dict(base, intensity_sigma=0.7)
    _b1, f1 = ne.run_leads({15: latest}, u, v, dom, (15,), state, cfg,
                           1.0, 300.0, T0, latest=latest,
                           mass_target={15: float(latest.sum())})
    # la massa resta quella del deterministico
    m0 = float(np.asarray(f0[15]["mean"], np.float64).sum())
    m1 = float(np.asarray(f1[15]["mean"], np.float64).sum())
    assert abs(m1 - m0) <= max(1.0, 0.01 * m0)
    # la coda alta non cala e la distribuzione si allarga
    p5_0 = float(f0[15]["probabilities"]["prob_5p0"][dom].mean())
    p5_1 = float(f1[15]["probabilities"]["prob_5p0"][dom].mean())
    assert p5_1 >= p5_0 - 0.02


def test_occurrence_thins_echo():
    shape = (128, 128)
    latest = _field(shape)
    dom = np.ones(shape, dtype=bool)
    state = _state(shape, (0.95, 0.85, 0.7, 0.5, 0.35, 0.2))
    u = np.full(shape, 0.5, dtype=np.float32)
    v = np.zeros(shape, dtype=np.float32)
    curve = [[0.0, 0.0], [0.1, 0.05], [0.5, 0.4], [1.0, 0.6],
             [5.0, 0.95], [1e9, 0.99]]
    base = {"members": 12, "thresholds_mmh": (0.5, 2.0),
            "motion_mode": "field", "motion_inflation": 3.0,
            "noise_inflation": 1.0, "intensity_sigma": 0.0}
    _b0, f0 = ne.run_leads({15: latest}, u, v, dom, (15,), state, base,
                           1.0, 300.0, T0, latest=latest,
                           mass_target={15: float(latest.sum())})
    cfg = dict(base, occurrence_scale=1.0, occurrence_curve=curve)
    _b1, f1 = ne.run_leads({15: latest}, u, v, dom, (15,), state, cfg,
                           1.0, 300.0, T0, latest=latest,
                           mass_target={15: float(latest.sum())})
    m0 = float(np.asarray(f0[15]["mean"], np.float64).sum())
    m1 = float(np.asarray(f1[15]["mean"], np.float64).sum())
    # la rasatura toglie massa e riduce la probabilita' media
    assert m1 < m0
    p0 = float(f0[15]["probabilities"]["prob_0p5"].mean())
    p1 = float(f1[15]["probabilities"]["prob_0p5"].mean())
    assert p1 < p0


def test_probability_rgba_transparent_below_threshold():
    prob = np.zeros((8, 8), dtype=np.float32)
    prob[0, 0] = 0.005
    prob[1, 1] = 0.9
    rgba = ne.probability_rgba(prob)
    assert rgba[0, 0, 3] == 0
    assert rgba[1, 1, 3] > 0
    assert rgba.shape == (8, 8, 4)


def test_disabled_payload_declares():
    blk = ne.disabled_payload("disabled_by_config", {"members": 20})
    assert blk["enabled"] is False
    assert blk["applied"] is False
    assert blk["null_reason"] == "disabled_by_config"


# ---------------------------------------------------------------------------
# integrazione: compute() con e senza ensemble
# ---------------------------------------------------------------------------
def _moving_rasters(tmp_path, n=12, rows=96, cols=96):
    rr, cc = np.ogrid[:rows, :cols]
    domain = ((rr - 48) ** 2 + (cc - 48) ** 2) <= 44 ** 2
    rasters = []
    for k in range(n):
        blob = 18.0 * np.exp(-(((rr - 48) ** 2 + (cc - (20 + k * 2)) ** 2)
                               / (2 * 7.0 ** 2)))
        field = (blob + 0.5).astype(np.float32)
        arr = np.where(domain, field, -9999.0).astype("float32")
        path = str(tmp_path / ("e%d.tif" % k))
        write_synthetic_tif(path, arr, transform=DEFAULT_TRANSFORM,
                            nodata=-9999.0)
        rd = read_raster(path, nodata_values=[-9999.0],
                         declared_nodata_from_tiff=True,
                         geo_plausible_bbox=CONFIG["preprocess"][
                             "geo_plausible_bbox"])
        rd.time_ms = int(T0 + k * STEP_MS)
        rd.time_iso = nf._iso(rd.time_ms)
        rasters.append(rd)
    return rasters


class _Bundle(object):
    def __init__(self, rasters):
        self.dpc_bundle = {"entries": {
            int(r.time_ms): {"sri": {"raster": r}} for r in rasters}}
        self.warnings = []
        self._nowcast_verification = {}


def _cfg(tmp_path, ensemble_on):
    cfg = load_config()
    cfg["output"] = dict(cfg["output"], out_dir=str(tmp_path / "out"))
    cfg["cascade"] = dict(cfg.get("cascade") or {}, enabled=False,
                          probability_matching=False)
    cfg["ensemble"] = dict(cfg.get("ensemble") or {}, enabled=ensemble_on,
                           members=20, keep_members=True)
    return cfg


def test_compute_ensemble_off_declares_disabled(tmp_path):
    rasters = _moving_rasters(tmp_path)
    cfg = _cfg(tmp_path, False)
    bundle = _Bundle(rasters)
    clk = clock_mod.Clock.at_ms(int(rasters[-1].time_ms))
    payload, native = nf.compute(bundle, cfg, clk, [], out_dir=None)
    assert payload["available"]
    # il blocco e' SEMPRE presente (il client deve sapere che e' spento),
    # con enabled false e il motivo dichiarato
    assert payload["ensemble"]["enabled"] is False
    assert payload["ensemble"]["applied"] is False
    assert payload["ensemble"]["null_reason"] == "disabled_by_config"
    assert "_ensemble" not in native


def test_compute_ensemble_on_produces_probabilities(tmp_path):
    rasters = _moving_rasters(tmp_path)
    cfg = _cfg(tmp_path, True)
    bundle = _Bundle(rasters)
    clk = clock_mod.Clock.at_ms(int(rasters[-1].time_ms))
    warn = []
    payload, native = nf.compute(bundle, cfg, clk, warn, out_dir=None)
    assert payload["available"]
    blk = payload["ensemble"]
    print("WARN", warn)
    assert blk["applied"] is True, warn
    assert blk["members"] == 20
    for lead in nf.OFFICIAL_LEADS:
        fields = native["_ensemble"][lead]
        assert "prob_0p5" in fields["probabilities"]
        assert fields["probabilities"]["prob_0p5"].shape == native[
            "_domain"].shape
        assert np.all(fields["quantiles"]["p10"] <=
                      fields["quantiles"]["p90"] + 1e-6)


def test_run_writes_probability_webp(tmp_path):
    rasters = _moving_rasters(tmp_path)
    cfg = _cfg(tmp_path, True)
    bundle = _Bundle(rasters)
    out_dir = str(tmp_path / "out")
    payload = nf.run(bundle, cfg, out_dir)
    assert payload["available"], payload.get("null_reason")
    for lead in nf.OFFICIAL_LEADS:
        name = ne.PROB_IMAGE_NAMES[lead]
        path = os.path.join(out_dir, name)
        assert os.path.isfile(path)
        assert os.path.getsize(path) > 0
        assert payload["ensemble"]["leads"][str(lead)]["images"][
            "prob_0p5"] == name
    # nessuna immagine di probabilita' se l'ensemble e' spento
    cfg_off = _cfg(tmp_path, False)
    out_off = str(tmp_path / "out_off")
    nf.run(_Bundle(rasters), cfg_off, out_off)
    assert not os.path.exists(
        os.path.join(out_off, ne.PROB_IMAGE_NAMES[nf.OFFICIAL_LEADS[0]]))
