#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test della MISTURA DI IPOTESI di moto (nwp_motion, 2026-11-11).

Questi test FALLISCONO sul codice precedente (1.2.4.82), che aveva una sola
ipotesi perturbata con una gaussiana isotropa: non esistevano
``ensemble_hypotheses``, ``ensemble_member_hypothesis``, la ripartizione dei
membri per ipotesi e la forma non ellittica della densita'.

Tutti i test sono ERMETICI: dataset sintetico in memoria, nessuna rete.
"""

import math
import os
import sys

import numpy as np
import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from radar_engine import nwp_motion as nm      # noqa: E402


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _ms(iso):
    return int(nm._parse_iso_utc(iso) * 1000.0)


def _dataset(levels=(1000, 975, 950, 925, 900, 850, 800, 700, 600, 500),
             n_hours=24, u_by_level=None, points=None):
    """PointDataset sintetico con vento costante per livello (v=0)."""
    pts = points or [(12.0, 42.0), (12.5, 42.0), (12.0, 42.5), (12.5, 42.5)]
    n = len(pts)
    default = {1000: 5.0, 975: 6.0, 950: 7.0, 925: 8.0, 900: 9.0,
               850: 10.0, 800: 12.0, 700: 20.0, 600: 30.0, 500: 40.0}
    u_by_level = u_by_level or default
    profile = {}
    for lvl in levels:
        u = np.full((n, n_hours), u_by_level.get(lvl, np.nan))
        v = np.zeros((n, n_hours))
        profile[lvl] = (u, v)
    wind = {lvl: profile[lvl] for lvl in (850, 700, 600, 500)
            if lvl in profile}
    geo = {lvl: np.full((n, n_hours), 1500.0 + (1000 - lvl) * 10.0)
           for lvl in (850, 700, 500)}
    return nm.PointDataset(
        path="synthetic", generated_at="2026-11-11T10:00:00+00:00",
        time_base_utc=nm._parse_iso_utc("2026-11-10T23:00:00Z"),
        lats=[p[1] for p in pts], lons=[p[0] for p in pts],
        wind=wind, geo=geo, model="best_match", n_hours=n_hours,
        profile=profile)


def _fit(speed=30.0, direction=70.0, used=True, resid=1.5):
    return {"velocity_kmh": speed, "direction_toward_deg": direction,
            "fit_used": used, "resid_std_km": resid}


def _hyp(name, speed, direction, weight, sal, scr, null_reason=None):
    return {
        "name": name, "label": name, "speed_kmh": speed,
        "direction_toward_deg": direction, "weight": weight,
        "sigma_along_km_60": sal, "sigma_cross_km_60": scr,
        "sigma_km_60": math.sqrt(sal * sal + scr * scr),
        "measured_error_km_60": None, "source": "test",
        "reference": "test", "null_reason": null_reason,
    }


# ---------------------------------------------------------------------------
# Funzioni pure
# ---------------------------------------------------------------------------

def test_layer_mean_wind_media_di_massa():
    """Media pesata per massa sul trapezio 1000-500 hPa."""
    ds = _dataset()
    near = ds.nearest(12.25, 42.25)
    u, v, used = nm.layer_mean_wind(ds, near, 12)
    assert used == [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500]
    # a mano: pesi dp = 25,25,25,25,50,50,100,100,100 (totale 500)
    expect = (25 * 5.5 + 25 * 6.5 + 25 * 7.5 + 25 * 8.5 + 50 * 9.5
              + 50 * 11.0 + 100 * 16.0 + 100 * 25.0 + 100 * 35.0) / 500.0
    assert u == pytest.approx(expect, abs=0.01)
    assert v == pytest.approx(0.0, abs=1e-9)


def test_bunkers_right_mover_perpendicolare_a_destra():
    """Shear verso est -> RM spostato verso SUD (destra dello shear)."""
    u_rm, v_rm, why = nm.bunkers_right_mover(0.0, 0.0, 20.0, 0.0)
    assert why is None
    assert u_rm == pytest.approx(0.0, abs=1e-9)
    assert v_rm == pytest.approx(-nm.BUNKERS_DEVIATION_KMH, abs=1e-9)
    # shear verso nord -> RM spostato verso EST
    u2, v2, _ = nm.bunkers_right_mover(0.0, 0.0, 0.0, 20.0)
    assert u2 == pytest.approx(nm.BUNKERS_DEVIATION_KMH, abs=1e-9)
    assert v2 == pytest.approx(0.0, abs=1e-9)
    # shear nullo -> non definito, con motivo
    u3, v3, why3 = nm.bunkers_right_mover(0.0, 0.0, 0.0, 0.0)
    assert u3 is None and v3 is None
    assert why3.startswith("shear_below_")


def test_hypothesis_motions_cinque_ipotesi():
    ds = _dataset()
    motions = nm.hypothesis_motions(
        ds, 12.25, 42.25, _ms("2026-11-11T12:00:00Z"), _fit())
    names = [m["name"] for m in motions]
    assert names == list(nm.HYPOTHESIS_ORDER)
    by = {m["name"]: m for m in motions}
    for name in nm.HYPOTHESIS_ORDER:
        assert by[name]["null_reason"] is None
    assert by["nwp_500"]["speed_kmh"] > by["nwp_700"]["speed_kmh"]


def test_hypothesis_motions_senza_nwp_dichiara_il_motivo():
    motions = nm.hypothesis_motions(
        None, 12.25, 42.25, _ms("2026-11-11T12:00:00Z"), _fit())
    by = {m["name"]: m for m in motions}
    assert by["observed"]["null_reason"] is None
    for name in ("nwp_mean_0_6", "nwp_500", "nwp_700", "bunkers_rm"):
        assert by[name]["null_reason"] in ("dataset_unavailable",
                                           "profile_unavailable")
        assert by[name]["speed_kmh"] is None


def test_pesi_invarianza_inversa_dall_errore_misurato():
    """w_i proporzionale a 1/e_i^2 (con observed senza fattore)."""
    motions = [{"name": n, "null_reason": None} for n in nm.HYPOTHESIS_ORDER]
    errors = {"observed": 20.0, "nwp_mean_0_6": 40.0, "nwp_500": 10.0,
              "nwp_700": 50.0, "bunkers_rm": 100.0}
    weights, detail = nm.hypothesis_weights(
        motions, errors=errors, reliability={"observed": 1.0})
    raw = {k: 1.0 / (v * v) for k, v in errors.items()}
    tot = sum(raw.values())
    for k in errors:
        assert weights[k] == pytest.approx(raw[k] / tot, rel=1e-9)
    assert sum(weights.values()) == pytest.approx(1.0, rel=1e-9)
    assert detail["renormalized"] is False


def test_pesi_ipotesi_senza_misura_esclusa():
    motions = [{"name": n, "null_reason": None} for n in nm.HYPOTHESIS_ORDER]
    errors = {"observed": 20.0, "nwp_mean_0_6": 40.0, "nwp_500": None,
              "nwp_700": 50.0, "bunkers_rm": 100.0}
    weights, detail = nm.hypothesis_weights(
        motions, errors=errors, reliability={"observed": 1.0})
    assert "nwp_500" not in weights
    assert detail["inactive"]["nwp_500"] == "no_measured_error"
    assert sum(weights.values()) == pytest.approx(1.0, rel=1e-9)


def test_ripartizione_membri_minimo_due_per_ipotesi():
    weights = {"observed": 0.7, "nwp_mean_0_6": 0.2, "nwp_500": 0.1}
    alloc = nm.allocate_members(weights, 24, minimum=2)
    assert sum(alloc.values()) == 24
    for k in weights:
        assert alloc[k] >= 2
    assert alloc["observed"] > alloc["nwp_mean_0_6"]


def test_residuo_sottrae_il_disaccordo():
    """sigma_residuo^2 = errore^2 - disaccordo^2 (componente per componente)."""
    motions = [
        {"name": "observed", "null_reason": None, "speed_kmh": 30.0,
         "direction_toward_deg": 90.0, "u_kmh": 30.0, "v_kmh": 0.0},
        {"name": "nwp_mean_0_6", "null_reason": None, "speed_kmh": 50.0,
         "direction_toward_deg": 90.0, "u_kmh": 50.0, "v_kmh": 0.0},
    ]
    weights = {"observed": 0.5, "nwp_mean_0_6": 0.5}
    res = nm.hypothesis_residuals(
        motions, weights,
        errors_along={"observed": 20.0, "nwp_mean_0_6": 20.0},
        errors_cross={"observed": 10.0, "nwp_mean_0_6": 10.0})
    # centro pesato a 40 km/h -> disaccordo 10 km su ciascuna ipotesi
    assert res["observed"]["disagreement_km_60"] == pytest.approx(10.0)
    assert res["observed"]["sigma_along_km_60"] == pytest.approx(
        math.sqrt(20.0 ** 2 - 10.0 ** 2), abs=0.01)
    assert res["observed"]["sigma_cross_km_60"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Ensemble: composizione e forma
# ---------------------------------------------------------------------------

def test_ensemble_espone_le_ipotesi_con_peso_e_membri():
    ds = _dataset()
    out = nm.build_motion_block(
        {"position": [12.25, 42.25], "structure_id": "CS-M"},
        None, _fit(), _ms("2026-11-11T12:00:00Z"), dataset=ds)
    hyps = out["ensemble_hypotheses"]
    assert hyps is not None
    names = [h["name"] for h in hyps]
    assert names == list(nm.HYPOTHESIS_ORDER)
    active = [h for h in hyps if h["active"]]
    assert len(active) == 5
    assert sum(h["weight"] for h in hyps) == pytest.approx(1.0, abs=1e-3)
    assert sum(h["n_members"] for h in hyps) == 200
    assert sum(h["n_members_published"] for h in hyps) == 24
    for h in active:
        assert h["n_members"] >= nm.MIN_MEMBERS_PER_HYPOTHESIS
        assert h["sigma_km_60"] is not None
        assert h["measured_error_km_60"] == \
            nm.HYPOTHESIS_ERROR_KM_60[h["name"]]


def test_ogni_membro_dichiara_la_sua_ipotesi():
    ds = _dataset()
    out = nm.build_motion_block(
        {"position": [12.25, 42.25], "structure_id": "CS-N"},
        None, _fit(), _ms("2026-11-11T12:00:00Z"), dataset=ds)
    members = out["ensemble_members"]
    tags = out["ensemble_member_hypothesis"]
    assert len(members) == 24
    assert len(tags) == len(members)
    assert set(tags) <= set(nm.HYPOTHESIS_ORDER)
    for h in out["ensemble_hypotheses"]:
        if not h["active"]:
            continue
        assert tags.count(h["name"]) == h["n_members_published"]
    assert out["ensemble"]["member_hypotheses"] == tags
    assert out["ensemble"]["hypotheses"] == out["ensemble_hypotheses"]


def test_rinormalizzazione_senza_nwp():
    """Nessun punto NWP entro 80 km -> resta solo l'osservato, con motivo."""
    ds = _dataset(points=[(0.0, 0.0)])      # ~1400 km dall'Italia
    out = nm.build_motion_block(
        {"position": [12.25, 42.25], "structure_id": "CS-R"},
        None, _fit(), _ms("2026-11-11T12:00:00Z"), dataset=ds)
    hyps = out["ensemble_hypotheses"]
    by = {h["name"]: h for h in hyps}
    assert by["observed"]["active"] is True
    assert by["observed"]["weight"] == pytest.approx(1.0, abs=1e-6)
    for name in ("nwp_mean_0_6", "nwp_500", "nwp_700", "bunkers_rm"):
        assert by[name]["active"] is False
        assert by[name]["weight"] == 0.0
        assert by[name]["null_reason"]
    assert out["ensemble_basis"]["renormalized"] is True
    assert out["ensemble_members"] is not None


def test_area_ratio_gaussiana_di_riferimento():
    """Controllo dell'estimatore: una sola ipotesi -> 0.30 (ln2/ln10)."""
    hyps = [_hyp("observed", 30.0, 90.0, 1.0, 10.0, 10.0)]
    shape = nm.density_shape_metrics(hyps)
    assert shape is not None
    assert shape["area_ratio_50_90"] == pytest.approx(0.301, abs=0.02)
    assert shape["n_modes"] == 1
    assert shape["asymmetry_km"] < 1.0


def test_mistura_non_gaussiana_asimmetria_e_modalita():
    """Due ipotesi a 90 gradi: il picco NON sta al centro della banda 90%."""
    hyps = [
        _hyp("observed", 30.0, 90.0, 0.5, 3.0, 3.0),
        _hyp("nwp_500", 30.0, 180.0, 0.5, 3.0, 3.0),
    ]
    shape = nm.density_shape_metrics(hyps)
    assert shape is not None
    # asimmetria: distanza del picco dal centroide della banda 90%
    assert shape["asymmetry_km"] > 10.0
    # multi-modalita': due massimi locali
    assert shape["n_modes"] >= 2
    # il rapporto aree resta ~0.30 per costruzione (due blob uguali): il
    # discriminante qui e' l'asimmetria, non l'area. Dichiarato nel report.
    assert shape["area_ratio_50_90"] == pytest.approx(0.301, abs=0.03)


def test_mistura_con_pesi_diversi_cambia_il_rapporto_aree():
    """Pesi molto diversi -> il rapporto aree si discosta dal 0.30."""
    hyps = [
        _hyp("observed", 30.0, 90.0, 0.9, 3.0, 3.0),
        _hyp("nwp_500", 60.0, 180.0, 0.1, 3.0, 3.0),
    ]
    shape = nm.density_shape_metrics(hyps)
    assert shape is not None
    assert abs(shape["area_ratio_50_90"] - 0.301) > 0.05


def test_spread_cresce_col_disaccordo_fra_ipotesi():
    """A parita' di sigma, piu' disaccordo -> spread maggiore."""
    blend = {"speed_kmh": 30.0, "direction_toward_deg": 90.0}
    close = [_hyp("observed", 30.0, 90.0, 0.5, 3.0, 3.0),
             _hyp("nwp_500", 30.0, 95.0, 0.5, 3.0, 3.0)]
    far = [_hyp("observed", 30.0, 90.0, 0.5, 3.0, 3.0),
           _hyp("nwp_500", 30.0, 180.0, 0.5, 3.0, 3.0)]
    e_close, _ = nm.build_ensemble(blend, 10.0, 10.0, (12.0, 42.0), 1,
                                   structure_id="C", hypotheses=close)
    e_far, _ = nm.build_ensemble(blend, 10.0, 10.0, (12.0, 42.0), 1,
                                 structure_id="C", hypotheses=far)
    assert e_far["spread_km"]["60"]["cross_std_km"] > \
        e_close["spread_km"]["60"]["cross_std_km"]


def test_compatibilita_singola_ipotesi():
    """La firma storica (blend + sigma) resta valida e unimodale."""
    blend = {"speed_kmh": 30.0, "direction_toward_deg": 60.0}
    ens, basis = nm.build_ensemble(blend, 10.0, 12.0, (12.0, 42.0),
                                   1794398400000, structure_id="CS-OLD")
    assert ens["n"] == 200
    assert len(ens["members"]) == 24
    assert set(ens["member_hypotheses"]) == {"observed"}
    assert basis["distribution"] == "mixture_of_hypotheses"
