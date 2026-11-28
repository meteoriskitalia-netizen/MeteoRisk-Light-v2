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
from radar_engine import nowcast_decay as nd
from radar_engine import nowcast_field as nf
from radar_engine import nowcast_trend as nt
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
    # I test del trasporto isolano trend ed erosione (test dedicati sotto).
    cfg["nowcast_trend"] = {"enabled": False}
    cfg["nowcast_decay"] = {"enabled": False}
    cfg["nowcast_clouds"] = {"enabled": False}
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
    cfg = _config(tmp_path)
    # Il test misura il TRASPORTO puro: spegne il 3 km e il ripristino HF
    # (che e' un unsharp dichiarato e puo' superare il massimo osservato).
    cfg["nowcast_transport_coarse"] = 0
    cfg["nowcast_hf_restore"] = {"enabled": False}
    payload, native = nf.compute(bundle, cfg,
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
    # 18 frame a passo UNIFORME di 5 min fino a +90 (troncamento dichiarato):
    # 5,10,...,90. Prima 60/75/90 avevano passo 15 e si vedeva il salto.
    expected = list(range(5, 91, 5))
    assert [l["lead_min"] for l in data["leads"]] == expected
    assert data["leads_total"] == 18
    assert data["official_leads"] == [15, 30, 60]
    assert [l["lead_min"] for l in data["leads"] if l["official"]] == [15, 30, 60]
    assert [l["step_min"] for l in data["leads"]] == [5] * 18
    assert data["truncation"]["max_lead_min"] == 90
    assert data["truncation"]["removed_leads"] == [120, 150, 180]
    assert data["truncation"]["aspect_ratio_limit"] == 1.5
    assert data["low_reliability_over_min"] == 90
    assert data["grid"]["crs"] == "EPSG:3857"
    assert len(data["grid"]["bounds"]) == 4
    assert data["grid"]["resolution_m"] == nf.OUTPUT_RES_M
    assert data["motion_algorithm"] == "lucas_kanade_pyramidal"
    assert data["legend"][0]["mmh"] == 0.3
    assert [l["mmh"] for l in data["legend"]] == [0.3, 2.0, 5.0, 10.0, 20.0,
                                                  50.0, 100.0]
    for lead in nf.LEADS_MIN:
        p = out / nf.IMAGE_NAMES[lead]
        assert p.exists() and p.stat().st_size > 0
        assert data["leads"][[l["lead_min"] for l in data["leads"]].index(lead)][
            "image"] == nf.IMAGE_NAMES[lead]
        assert p.read_bytes()[:4] == b"RIFF"      # WebP container
    # 18 file distinti (010, 020, ... non sovrascrivono 005)
    assert len(list(out.glob("nowcast_lead*.webp"))) == 18
    assert nf.IMAGE_NAMES[5] == "nowcast_lead005.webp"
    assert nf.IMAGE_NAMES[60] == "nowcast_lead060.webp"
    assert nf.IMAGE_NAMES[90] == "nowcast_lead090.webp"
    assert 180 not in nf.IMAGE_NAMES


def test_frames_are_cumulative_and_coherent(tmp_path):
    """Con `transport_mode="steps"` (A/B) il +10 e' il +5 avanzato di 1 passo.

    Il default di produzione e' invece lo spostamento COMPOSTO: qui si fissa
    esplicitamente il vecchio schema per non perderne la copertura.
    """
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    cfg["nowcast_transport_mode"] = "steps"
    cfg["nowcast_transport_coarse"] = 0
    cfg["nowcast_hf_restore"] = {"enabled": False}
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
    # il +90 e' il +5 avanzato di altri 17 passi (18 totali), non un salto
    assert np.allclose(expect(17, 90), native[90], rtol=1e-5, atol=1e-4)


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
    # Il default di produzione e' il trasporto a 3 km: qui si isola il
    # vecchio warp unico per non perderne la copertura.
    cfg["nowcast_transport_coarse"] = 0
    cfg["nowcast_hf_restore"] = {"enabled": False}
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
    for lead in (10, 20, 90):
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


def test_step_min_for_uniform_leads():
    """Il passo pubblicato e' uniforme a 5 min (2026-11-27)."""
    assert nf.step_min_for(5) == 5
    assert nf.step_min_for(45) == 5
    assert nf.step_min_for(60) == 5
    assert nf.step_min_for(90) == 5
    # i lead oltre +90 non sono pubblicati (troncamento dichiarato)
    assert nf.step_min_for(120) == 5          # fallback: STEP_MIN, mai 30
    # override dei backtest: il passo si ricava dalla lista, non da una tabella
    assert nf.step_min_for(90, (5, 30, 60, 90)) == 30


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
    # il lead 180 non e' pubblicato: la sua riga nel file resta ignorata
    assert 180 not in by_lead
    assert by_lead[90]["reliability"]["nowcast_csi"] == 0.31
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
    assert payload["available"] is True          # gli altri 11 restano validi
    by_lead = {l["lead_min"]: l for l in payload["leads"]}
    assert by_lead[20]["image"] is None
    assert "RuntimeError" in by_lead[20]["image_error"]
    assert by_lead[15]["image"] == nf.IMAGE_NAMES[15]
    assert len(list(out.glob("nowcast_lead*.webp"))) == 17
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
# 6b. palette AD ALTO CONTRASTO (2026-11-26): non piu' il blu notte
# ---------------------------------------------------------------------------
# Nodi del renderer, gli stessi pubblicati in `legend` e usati dal client.
NOWCAST_STOPS = ((0.3, (0, 220, 255)), (2.0, (0, 150, 255)),
                 (5.0, (0, 220, 110)), (10.0, (255, 235, 0)),
                 (20.0, (255, 140, 0)), (50.0, (255, 40, 40)),
                 (100.0, (240, 0, 210)))


def test_palette_is_high_contrast_scale():
    assert nf.PALETTE == NOWCAST_STOPS
    for mmh, rgb in NOWCAST_STOPS:
        px = nf._rgba(np.array([[np.float32(mmh)]]))[0, 0]
        assert [int(px[0]), int(px[1]), int(px[2])] == list(rgb)
    # code dichiarate: sopra 100 -> ultimo colore
    high = nf._rgba(np.array([[np.float32(200.0)]]))[0, 0]
    assert [int(high[0]), int(high[1]), int(high[2])] == [240, 0, 210]
    # alpha GRADUALE: 0 a 0,15, 40% a 0,5, pieno a 1,5; niente colore a 0.
    low = nf._rgba(np.array([[np.float32(0.1)]]))[0, 0]
    mid = nf._rgba(np.array([[np.float32(0.5)]]))[0, 0]
    full = nf._rgba(np.array([[np.float32(1.5)]]))[0, 0]
    dry = nf._rgba(np.array([[np.float32(0.0)]]))[0, 0]
    assert int(low[3]) == 0
    assert int(mid[3]) == round(0.40 * nf.ALPHA_MAX)
    assert int(full[3]) == nf.ALPHA_MAX
    assert int(dry[3]) == 0


def test_palette_between_stops_is_linear_like_the_client():
    # 1 mm/h fra 0,3 e 2,0: t=0,7/1,7 -> (0, 191.18, 255) -> round 191
    px = nf._rgba(np.array([[np.float32(1.0)]]))[0, 0]
    assert [int(px[0]), int(px[1]), int(px[2])] == [0, 191, 255]


# ---------------------------------------------------------------------------
# 7. main.py continua se il modulo fallisce
# ---------------------------------------------------------------------------
def _pipeline_config():
    cfg = load_config()
    cfg["nowcast_trend"] = {"enabled": False}
    cfg["nowcast_decay"] = {"enabled": False}
    cfg["nowcast_clouds"] = {"enabled": False}
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

# ---------------------------------------------------------------------------
# regolarizzazione divergence-free del moto (2026-11-26)
# ---------------------------------------------------------------------------
def _divergence(a, b):
    ay, ax = np.gradient(np.asarray(a, np.float32))
    by, bx = np.gradient(np.asarray(b, np.float32))
    return ax + by


def test_regularize_motion_removes_small_scale_divergence():
    """La divergenza a scala di cella sparisce; traslazione e rotazione no."""
    h = w = 160
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    yc, xc = yy - h / 2.0, xx - w / 2.0
    # divergenza LOCALIZZATA a scala ~8 px (quella che deforma una cella),
    # non un ramp a scala di griglia che il low-pass deve giustamente tenere
    g = np.exp(-(xc ** 2 + yc ** 2) / (2.0 * 8.0 ** 2))
    u = 0.01 * xc * g + 1.5
    v = 0.01 * yc * g - 0.5
    reg_u, reg_v, info = nf.regularize_motion(u, v, sigma_px=30.0)
    assert info["applied"] is True
    assert info["divergence_reduction"] is not None
    before = np.abs(_divergence(u, v)[20:-20, 20:-20]).mean()
    after = np.abs(_divergence(reg_u, reg_v)[20:-20, 20:-20]).mean()
    assert after < 0.25 * before, (before, after)
    # la traslazione media resta
    assert abs(float(reg_u.mean()) - float(u.mean())) < 0.02
    assert abs(float(reg_v.mean()) - float(v.mean())) < 0.02
    # una rotazione pura (divergenza 0) resta quasi identica all'interno
    ur = -0.01 * yc + 1.5
    vr = 0.01 * xc - 0.5
    rr_u, rr_v, _ = nf.regularize_motion(ur, vr, sigma_px=30.0)
    assert np.abs(rr_u - ur)[20:-20, 20:-20].mean() < 0.05
    assert np.abs(rr_v - vr)[20:-20, 20:-20].mean() < 0.05


def test_regularize_motion_sigma_zero_is_identity():
    """sigma 0 = A/B: il campo non viene toccato e il motivo e' dichiarato."""
    u = np.arange(64, dtype=np.float32).reshape(8, 8)
    v = -u
    reg_u, reg_v, info = nf.regularize_motion(u, v, sigma_px=0.0)
    assert info["applied"] is False
    assert info["null_reason"] == "sigma_zero"
    assert np.array_equal(reg_u, u)
    assert np.array_equal(reg_v, v)


def test_compute_publishes_motion_regularization(tmp_path):
    """Il payload dichiara la regolarizzazione e la divergenza residua."""
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    payload, native = nf.compute(bundle, cfg,
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    reg = payload["motion_regularization"]
    assert reg["method"] == "helmholtz_divergence_free_lowpass"
    assert reg["applied"] is True
    assert reg["sigma_km"] == nf.MOTION_DIVERGENCE_SIGMA_KM
    assert reg["divergence_after"]["mean_abs"] is not None
    # su un blob quasi rigido la divergenza non deve crescere
    assert (reg["divergence_after"]["mean_abs"]
            <= reg["divergence_before"]["mean_abs"] + 1e-6)
    assert "Helmholtz" in payload["limits"]


def test_compute_motion_regularization_can_be_disabled(tmp_path):
    """`nowcast_motion_divergence_sigma_km = 0` riproduce il vecchio moto."""
    rasters, domain = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    cfg["nowcast_motion_divergence_sigma_km"] = 0
    payload, native = nf.compute(bundle, cfg,
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    reg = payload["motion_regularization"]
    assert reg["applied"] is False
    assert reg["null_reason"] == "sigma_zero"


# ---------------------------------------------------------------------------
# 14. trasporto a 3 km + ripristino HF + troncamento (2026-11-26)
# ---------------------------------------------------------------------------
def test_coarse_transport_and_hf_restore_are_declared(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    assert payload["available"] is True
    assert payload["transport_coarse_factor"] == 3
    assert payload["transport_coarse_method"]
    assert payload["truncation"]["max_lead_min"] == 90
    assert 120 not in native and 180 not in native
    hf = payload["hf_restore"]
    assert hf["enabled"] is True and hf["applied"] is True
    assert hf["method"] == "deterministic_unsharp_to_analysis_hf"
    assert set(hf["per_lead"]) == {str(l) for l in nf.LEADS_MIN}


def test_hf_detail_does_not_decay_with_lead(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1, sigma=4.0)
    bundle = _Bundle(rasters)
    payload, native = nf.compute(bundle, _config(tmp_path),
                                 clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
                                 bundle.warnings)
    dom = np.asarray(native["_domain"], dtype=bool)
    vals = {}
    for lead in (15, 30, 60, 90):
        hf, _tot = nf.hf_stats(native[lead], dom)
        assert hf is not None
        vals[lead] = hf
    # il dettaglio non cala col lead: il +90 resta almeno al 90% del +15
    assert vals[90] >= 0.90 * vals[15]


# ---------------------------------------------------------------------------
# 15. resa: alpha graduale, maschera del dato invariata
# ---------------------------------------------------------------------------
def test_alpha_renders_weak_rain_without_changing_the_data():
    f = np.array([[0.0, 0.1, 0.3, 0.5, 1.0, 1.5, 5.0]],
                 dtype=np.float32)
    before = f.copy()
    rgba = nf._rgba(f)
    # il renderer NON modifica il campo
    assert np.array_equal(f, before)
    # 0 e sotto la soglia di disegno: trasparente
    assert int(rgba[0, 0, 3]) == 0
    assert int(rgba[0, 1, 3]) == 0
    # 0,3-0,5: visibile (prima era trasparente sotto 0,5)
    assert int(rgba[0, 2, 3]) > 0
    # a 0,5 il 40% dell'alpha, a 1,5 il pieno
    assert int(rgba[0, 3, 3]) == round(0.40 * nf.ALPHA_MAX)
    assert int(rgba[0, 5, 3]) == nf.ALPHA_MAX
    # nessun pixel asciutto colorato
    assert int(rgba[0, 0, 3]) == 0


# ---------------------------------------------------------------------------
# 16. intervalli reali fra i frame (2026-11-27): niente 5 min per assunzione
# ---------------------------------------------------------------------------
def _synthetic_frames(times_ms, rows=120, cols=160):
    """Blob che si sposta di 1 px ogni 5 min, con timestamp arbitrari."""
    domain = _disk_domain(rows, cols, rows // 2, cols // 2, rows // 2 - 5)
    frames = []
    for i, ts in enumerate(times_ms):
        f = _blob(rows, cols, rows // 2, 30 + i, sigma=5.0, amp=20.0)
        frames.append((f.astype(np.float32), domain, int(ts)))
    return frames


def test_motion_discards_out_of_band_pair_and_keeps_real_dt():
    # 0,5,15,20,25 min: la coppia 5->15 (10 min) e' fuori banda 4-6
    times = [T0 + m * 60000 for m in (0, 5, 15, 20, 25)]
    frames = _synthetic_frames(times)
    warns = []
    m = nf.estimate_motion(frames, warns, band_s=(240.0, 360.0))
    assert m is not None
    assert m["pair_dt_s"] == [300.0]
    assert m["pairs_discarded"] >= 1
    assert m["pair_dt_band_s"] == [240.0, 360.0]
    assert m["reference_s"] == 300.0
    assert any("banda" in w for w in warns)
    # il moto e' normalizzato a 5 min: 1 px per 5 min (u ~ 1, v ~ 0)
    u, v = m["u"], m["v"]
    assert np.nanmedian(np.abs(u)) == pytest.approx(1.0, abs=0.25)
    assert np.nanmedian(np.abs(v)) < 0.25


def test_motion_with_no_pair_in_band_returns_none():
    frames = _synthetic_frames([T0 + m * 60000 for m in (0, 10, 20)])
    warns = []
    m = nf.estimate_motion(frames, warns, band_s=(240.0, 360.0))
    assert m is None
    assert any("nessuna coppia nella banda" in w for w in warns)


def test_payload_declares_frame_intervals_gaps_and_lead_origin(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=4, dx=1)
    # 0,5,15,20 min: buco di 10 min fra il secondo e il terzo
    for rd, m in zip(rasters, (0, 5, 15, 20)):
        rd.time_ms = T0 + m * 60000
        rd.time_iso = nf._iso(rd.time_ms)
    bundle = _Bundle(rasters)
    clk = clock_mod.Clock.at_ms(T0 + 20 * 60000)
    payload, native = nf.compute(bundle, _config(tmp_path), clk,
                                 bundle.warnings)
    assert payload["available"] is True
    assert payload["lead_origin"] == "source_slot"
    assert "source_slot" in payload["lead_origin_note"]
    assert payload["frame_intervals_min"] == [5.0, 10.0, 5.0]
    assert payload["gap_minutes"] == [10.0]
    assert payload["motion_pair_dt_band_s"] == [240.0, 360.0]
    assert payload["motion_pairs_discarded"] >= 1
    # il +5 e' 5 minuti dopo il frame, non dopo l'orologio (qui coincidono)
    first = payload["leads"][0]
    assert first["lead_min"] == 5
    assert first["valid_time"] == nf._iso(T0 + 25 * 60000)
    assert first["lead_from_now_min"] == 5.0


# ---------------------------------------------------------------------------
# 17. trend convettivo V-a: solo convettive, +30, dissolvenza a +60
# ---------------------------------------------------------------------------
def _trend_case(rows=180, cols=240):
    domain = _disk_domain(rows, cols, rows // 2, cols // 2, rows // 2 - 5)
    conv = [_blob(rows, cols, rows // 2, 80, sigma=7.0, amp=a)
            for a in (15.0, 25.0, 35.0)]
    strat = _blob(rows, cols, rows // 2, 180, sigma=18.0, amp=4.0)
    frames = [(np.maximum(c, strat).astype(np.float32), domain,
               T0 + k * 300000) for k, c in enumerate(conv)]
    return frames, domain


def test_trend_convective_grows_and_stratiform_is_untouched():
    frames, domain = _trend_case()
    zero = np.zeros_like(frames[0][0], dtype=np.float32)
    state = nt.prepare(frames, zero, zero, domain,
                       {"calibration": {"rho_int_30": 0.28}})
    assert state["info"]["regions_with_trend"] == 1
    field = frames[-1][0]
    out30, info30 = nt.apply(field, 30, state, zero, zero, domain)
    assert info30["applied"] is True
    assert info30["factor_max"] > 1.0
    conv = _disk_domain(*field.shape, field.shape[0] // 2, 80, 12)
    strat = _disk_domain(*field.shape, field.shape[0] // 2, 180, 25)
    assert float(out30[conv].mean()) > float(field[conv].mean())
    assert np.allclose(out30[strat], field[strat], rtol=1e-6, atol=1e-6)
    # a +60 il trend e' ancora attivo (rho@60 = 0,224): il nucleo cresce
    out60, info60 = nt.apply(field, 60, state, zero, zero, domain)
    assert info60["applied"] is True
    assert float(out60[conv].mean()) > float(field[conv].mean())
    # a +90 la dissolvenza porta il fattore a 1: campo invariato nel dominio
    out90, _i90 = nt.apply(field, 90, state, zero, zero, domain)
    assert np.allclose(out90[domain], field[domain], rtol=1e-6, atol=1e-6)


def test_trend_disabled_if_calibration_rho_is_low():
    frames, domain = _trend_case()
    zero = np.zeros_like(frames[0][0], dtype=np.float32)
    state = nt.prepare(frames, zero, zero, domain,
                       {"calibration": {"rho_int_30": 0.10}})
    assert state["labels"] is None
    assert state["info"]["null_reason"] == "calibration_rho_below_threshold"


def test_trend_newborn_region_has_no_history():
    rows, cols = 180, 240
    domain = _disk_domain(rows, cols, rows // 2, cols // 2, rows // 2 - 5)
    conv = _blob(rows, cols, rows // 2, 80, sigma=7.0, amp=35.0)
    z = np.zeros((rows, cols), np.float32)
    frames = [(z, domain, T0), (z, domain, T0 + 300000),
              (conv, domain, T0 + 600000)]
    zero = np.zeros_like(conv)
    state = nt.prepare(frames, zero, zero, domain,
                       {"calibration": {"rho_int_30": 0.28}})
    assert state["info"]["regions_with_trend"] == 0
    assert state["info"]["null_reason"] == "no_convective_region_with_history"


def test_compute_payload_declares_evolution(tmp_path):
    rows, cols = 180, 240
    domain = _disk_domain(rows, cols, rows // 2, cols // 2, rows // 2 - 5)
    rasters = []
    for k, amp in enumerate((15.0, 25.0, 35.0)):
        field = _blob(rows, cols, rows // 2, 60 + k, sigma=7.0, amp=amp)
        rasters.append(_write_frame(tmp_path, "t%d.tif" % k, field, domain,
                                    T0 + k * STEP_MS))
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    cfg["nowcast_trend"] = {
        "enabled": True,
        "calibration": {"rho_int_30": 0.28, "rho_int_60": 0.16},
    }
    payload, _native = nf.compute(
        bundle, cfg, clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
        bundle.warnings)
    assert payload["available"] is True
    ev = payload["evolution"]
    assert ev["enabled"] is True
    assert ev["variant"] == "V-a_convective_intensity_linear"
    assert ev["regions_with_trend"] >= 1
    assert ev["applied"] is True
    assert ev["per_lead"]["30"]["applied"] is True


def test_trend_disabled_declares_reason(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    cfg["nowcast_trend"] = {"enabled": False,
                            "disabled_reason": "aB_csi5_failed"}
    payload, _native = nf.compute(
        bundle, cfg, clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
        bundle.warnings)
    assert payload["available"] is True
    ev = payload["evolution"]
    assert ev["enabled"] is False
    assert ev["applied"] is False
    assert ev["null_reason"] == "aB_csi5_failed"
    assert ev["per_lead"] == {}


# ---------------------------------------------------------------------------
# 18. erosione del nucleo V4: solo >5, dal retro, copertura invariata
# ---------------------------------------------------------------------------
def _strong_field(rows=180, cols=260):
    domain = _disk_domain(rows, cols, rows // 2, cols // 2, rows // 2 - 5)
    f = _blob(rows, cols, rows // 2, cols // 2, sigma=12.0, amp=40.0)
    return f.astype(np.float32), domain


def test_rear_erosion_radius_and_strong_core_only():
    assert nd._radius(5) == 0
    assert nd._radius(15) == 1
    assert nd._radius(30) == 1
    assert nd._radius(45) == 2
    assert nd._radius(60) == 2
    assert nd._radius(90) == 3
    f, dom = _strong_field()
    u = np.ones_like(f, dtype=np.float32)   # moto verso +x
    v = np.zeros_like(f, dtype=np.float32)
    a05 = int((f >= 0.5)[dom].sum())
    a5 = int((f >= 5.0)[dom].sum())
    out30, i30 = nd.apply(f, 30, u, v, dom, {})
    assert i30["applied"] is True
    assert int((out30 >= 0.5)[dom].sum()) == a05      # copertura invariata
    assert int((out30 >= 5.0)[dom].sum()) < a5        # nucleo ridotto
    # il retro e' il lato -x: la banda sinistra scende a 4,9, il destro no
    yy, xx = np.mgrid[0:f.shape[0], 0:f.shape[1]]
    m = dom & (f >= 5.0)
    cut = float(xx[m].min()) + 1.0
    band = m & (xx <= cut)
    right = m & (xx > float(xx[m].max()) - 2.0)
    assert float(out30[band].max()) <= 4.9001
    assert np.allclose(out30[right], f[right], rtol=1e-6, atol=1e-6)
    # a +10 il raggio e' 0: campo invariato
    out10, i10 = nd.apply(f, 10, u, v, dom, {})
    assert i10["applied"] is False
    assert np.allclose(out10, f, rtol=1e-6, atol=1e-6)


def test_compute_payload_declares_decay(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    cfg["nowcast_decay"] = {"enabled": True}
    payload, _native = nf.compute(
        bundle, cfg, clock_mod.Clock.at_ms(T0 + 2 * STEP_MS),
        bundle.warnings)
    assert payload["available"] is True
    dec = payload["decay"]
    assert dec["enabled"] is True
    assert dec["method"] == "rear_erosion_strong_core"
    assert dec["threshold_mmh"] == 5.0
    assert dec["applied"] is True
    assert dec["per_lead"]["30"]["pixels_changed"] > 0


# ---------------------------------------------------------------------------
# 19. nuvole IR di contesto: payload e WebP, sotto la pioggia
# ---------------------------------------------------------------------------
def test_clouds_payload_and_image(tmp_path):
    rows, cols = 200, 200
    domain = _disk_domain(rows, cols, 100, 100, 95)
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1, rows=rows, cols=cols)
    # IR_108 sintetico: -5 °C di base, -50 °C in un settore (nube convettiva)
    temp = np.full((rows, cols), -5.0, np.float32)
    temp[40:90, 40:90] = -50.0
    temp[100:140, 120:170] = -28.0
    ir_rd = _write_frame(tmp_path, "ir108.tif", temp, domain,
                         T0 + 2 * STEP_MS)
    bundle = _Bundle(rasters)
    bundle.dpc_bundle["entries"][int(ir_rd.time_ms)]["ir108"] = {
        "raster": ir_rd}
    cfg = _config(tmp_path)
    cfg["nowcast_clouds"] = {"enabled": True}
    out = tmp_path / "out"
    payload = nf.run(bundle, cfg, str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    cl = payload["clouds"]
    assert cl["available"] is True
    assert cl["role"] == "context"
    assert cl["not_a_rain_forecast"] is True
    assert cl["source"] == "DPC_IR_108"
    assert cl["mode"] == "ir_gray_continuous"
    assert cl["transfer"]["t_warm_cut_c"] == 3.0
    assert cl["opacity"] == 0.72
    assert cl["thresholds_c"] == [-20.0, -32.0, -44.0]
    assert cl["image"] == "nowcast_clouds.webp"
    assert cl["method"] == "advected"
    assert cl["advection"]["speed_factor"] == nf.MOTION_SPEED_FACTOR
    assert cl["advection"]["lead_min"] == list(nf.LEADS_MIN)
    assert cl["slot"] == nf._iso(T0 + 2 * STEP_MS)
    p = out / "nowcast_clouds.webp"
    assert p.exists() and p.stat().st_size > 0
    assert payload["output_bytes"]["clouds"]["nowcast_clouds.webp"] > 0
    # una immagine per lead, non uno sfondo fisso
    assert len(cl["images_by_lead"]) == len(nf.LEADS_MIN)
    for lead in nf.LEADS_MIN:
        name = cl["images_by_lead"][str(lead)]
        assert name == "nowcast_clouds_lead%03d.webp" % lead
        assert (out / name).exists() and (out / name).stat().st_size > 0
        assert payload["output_bytes"]["clouds"][name] > 0
    # nessun colore della pioggia nella palette delle nuvole
    rain = {tuple(rgb) for _mmh, rgb in nf.PALETTE}
    for cls in cl["classes"]:
        assert tuple(cls["rgb"]) not in rain


def test_clouds_available_even_without_rain(tmp_path):
    """Il caso d'uso: nube che cresce ma nessuna pioggia nell'ultimo frame."""
    rows, cols = 160, 160
    domain = _disk_domain(rows, cols, 80, 80, 75)
    rasters = []
    for k in range(3):
        dry = np.zeros((rows, cols), np.float32)
        rasters.append(_write_frame(tmp_path, "dry%d.tif" % k, dry, domain,
                                    T0 + k * STEP_MS))
    temp = np.full((rows, cols), -5.0, np.float32)
    temp[50:110, 50:110] = -52.0
    ir_rd = _write_frame(tmp_path, "ir_dry.tif", temp, domain,
                         T0 + 2 * STEP_MS)
    bundle = _Bundle(rasters)
    bundle.dpc_bundle["entries"][int(ir_rd.time_ms)]["ir108"] = {
        "raster": ir_rd}
    cfg = _config(tmp_path)
    cfg["nowcast_clouds"] = {"enabled": True}
    out = tmp_path / "out_dry"
    payload = nf.run(bundle, cfg, str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["available"] is False
    assert payload["null_reason"] == "no_rain_in_latest_frame"
    assert payload["clouds"]["available"] is True
    assert payload["clouds"]["role"] == "context"
    assert payload["grid"]["bounds"]
    assert (out / "nowcast_clouds.webp").exists()
    assert payload["clouds"]["image"] == "nowcast_clouds.webp"


def test_spectral_evolve_is_lognormal_and_area_mean_is_zero():
    rows, cols = 120, 140
    dom = _disk_domain(rows, cols, 60, 70, 55)
    field = _blob(rows, cols, 60, 70, sigma=8.0, amp=20.0)
    cfg = {"sigma_model": 0.0, "epsilon_corr_mult": 3.0,
           "sigma_max": 1.5, "m_intensity": 0.0}
    out, info = nf.spectral_evolve(
        field, dom, 60, 2.0, cfg, np.random.default_rng(7))
    assert out.shape == field.shape
    assert float(out.min()) >= 0.0 and bool(np.isfinite(out).all())
    assert info["applied"] is True
    assert info["m_area"] == 0.0
    assert len(info["bands"]) == 6
    assert all(b["sigma"] <= 1.5 for b in info["bands"])
    # m=0: il valor medio non deve cambiare di ordini di grandezza
    m0 = float(field[dom].mean())
    m1 = float(out[dom].mean())
    assert 0.3 < m1 / m0 < 3.0
    # sigma cresce dove rho e' piu' bassa (banda fine vs 64+)
    by = {b["band_km"][0]: b for b in info["bands"]}
    assert by[2.0]["sigma"] >= by[64.0]["sigma"]


def test_spectral_evolution_block_is_reported(tmp_path):
    rasters, _ = _moving_frames(tmp_path, n=3, dx=1, rows=140, cols=140)
    bundle = _Bundle(rasters)
    cfg = _config(tmp_path)
    cfg["nowcast_spectral"] = {"enabled": True, "sigma_max": 1.2,
                               "m_intensity": 0.0, "epsilon_corr_mult": 3.0}
    out = tmp_path / "out_spectral"
    payload = nf.run(bundle, cfg, str(out),
                     clock=clock_mod.Clock.at_ms(T0 + 2 * STEP_MS))
    assert payload["available"] is True
    sp = payload["spectral_evolution"]
    assert sp["enabled"] is True and sp["applied"] is True
    assert sp["method"] == "spectral_lognormal"
    assert sp["m_area"] == 0.0
    assert len(sp["per_lead"]) == len(nf.LEADS_MIN)
    for lead in nf.LEADS_MIN:
        b = sp["per_lead"][str(lead)]
        assert len(b["bands"]) == 6
        assert all(x["sigma"] <= 1.2 for x in b["bands"])
