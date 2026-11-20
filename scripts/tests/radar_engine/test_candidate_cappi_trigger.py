#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1.3-CAPPI-SELETTIVO — trigger OPT-IN a 4 CAPPI su candidato convettivo.

Questi test FALLISCONO sul codice pristino (BUILD 164): la funzione
`_candidate_cappi_trigger` non esiste e `_cappi_product_map` non accetta
`levels_km`. Nessuna rete: tutti i download sono sostituiti.
"""
import copy
import os
import sys

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS as PjCRS
from pyproj import Transformer

_TESTS = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(os.path.dirname(_TESTS))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import radar_engine.fetch as fetch_mod  # noqa: E402
import radar_engine.main as eng  # noqa: E402
import radar_engine.models as models  # noqa: E402

LON0, LAT0, PX, ROWS, COLS = 8.0, 46.0, 0.1, 100, 100


def _frame(data=None, value=5.0, ts_ms=0):
    if data is None:
        data = np.full((ROWS, COLS), float(value))
    data = np.asarray(data, dtype="float64")
    rows, cols = data.shape
    transform = Affine(PX, 0.0, LON0, 0.0, -PX, LAT0)
    crs = PjCRS.from_epsg(4326)
    valid = np.isfinite(data)
    return models.RasterData(
        data=data, valid_mask=valid, nodata_mask=~valid, rows=rows, cols=cols,
        crs=crs, crs_wkt=crs.to_wkt(), transform=transform,
        pixel_area_km2=(PX * 111.0) ** 2, time_ms=ts_ms,
        time_iso="2026-11-20T15:35:00Z", source_path="synthetic",
        declared_nodata=None,
        geo_transform=Transformer.from_crs(crs, "EPSG:4326", always_xy=True))


def _bundle(candidates=None):
    bundle = models.EngineBundle("ok", "2026-11-20T15:35:00Z", "test")
    bundle.frames = [_frame(ts_ms=1795188900000), _frame(ts_ms=1795188900000)]
    bundle.convective_structures = candidates if candidates is not None else [
        {"soi": 80.0, "position": [13.0, 41.0], "cell_id": "c1"},
        {"soi": 70.0, "position": [13.1, 41.1], "cell_id": "c2"},
    ]
    return bundle


def _cfg(enabled=True, min_soi=77.0):
    cfg = copy.deepcopy(eng.load_config())
    cfg["phase2"]["environment"]["enabled"] = False
    cfg["phase2"]["lightning"]["enabled"] = False
    cfg["phase2"]["structure"]["hail_gate"]["enabled"] = True
    trig = cfg["phase2"]["structure"]["candidate_cappi_trigger"]
    trig["enabled"] = enabled
    trig["min_soi"] = min_soi
    return cfg


def test_enabled_by_default_and_declaration_complete():
    cfg = eng.load_config()
    scfg = cfg["phase2"]["structure"]
    # 2026-11-20: trigger ATTIVO per decisione esplicita; il ramo disabilitato
    # resta verificato con una config esplicita qui sotto ( nessun allentamento).
    assert scfg["candidate_cappi_trigger"]["enabled"] is True
    off = copy.deepcopy(scfg)
    off["candidate_cappi_trigger"]["enabled"] = False
    t = eng._candidate_cappi_trigger(_bundle(), off)
    assert t["value"] is False
    assert t["null_reason"] == "trigger_disabled"
    for key in ("value", "null_reason", "source", "reference"):
        assert key in t
    assert "SCELTA INGEGNERISTICA" in t["reference"]
    # default invariante: 2 soli livelli low/high
    cmap = eng._cappi_product_map(dict(scfg, mesh_levels_enabled=False))
    assert cmap == {2.0: "CAPPI_2", 6.0: "CAPPI_6"}


def test_trigger_uses_real_snapshot_soi_values():
    # valori SOI MISURATI su convective_structures.json del 2026-11-20T15:35Z
    cands = [{"soi": s} for s in (77, 73, 69, 65)]
    scfg = _cfg()["phase2"]["structure"]
    t = eng._candidate_cappi_trigger(_bundle(cands), scfg)
    assert t["value"] is True and t["max_soi"] == 77.0
    assert t["null_reason"] is None
    assert t["levels_km"] == [2.0, 4.0, 6.0, 8.0]
    # soglia piu' alta -> non scatta, ma e' VALUTATA (non "assente")
    t80 = eng._candidate_cappi_trigger(_bundle(cands), _cfg(min_soi=80)["phase2"]["structure"])
    assert t80["value"] is False and t80["null_reason"] is None
    assert t80["max_soi"] == 77.0
    # nessun candidato / SOI non disponibile -> motivo esplicito
    tn = eng._candidate_cappi_trigger(_bundle([]), scfg)
    assert tn["value"] is False and tn["null_reason"] == "no_candidate_structures"
    ts = eng._candidate_cappi_trigger(_bundle([{"x": 1}]), scfg)
    assert ts["value"] is False and ts["null_reason"] == "soi_unavailable"


def test_four_levels_are_only_declared_products_and_no_mutation():
    scfg = _cfg()["phase2"]["structure"]
    declared = set(scfg["cappi_products"].values())
    cmap = eng._cappi_product_map(dict(scfg, mesh_levels_enabled=False),
                                 levels_km=[2.0, 4.0, 6.0, 8.0])
    assert cmap == {2.0: "CAPPI_2", 4.0: "CAPPI_4",
                    6.0: "CAPPI_6", 8.0: "CAPPI_8"}
    assert set(cmap.values()).issubset(declared)
    b = _bundle()
    before = copy.deepcopy(b.convective_structures)
    eng._candidate_cappi_trigger(b, scfg)
    assert b.convective_structures == before


def _run_phase2(enabled, monkeypatch):
    """Esegue _phase2_evaluate con I/O sostituiti; ritorna (bundle, richiesti)."""
    cfg = _cfg(enabled=enabled)
    scfg = cfg["phase2"]["structure"]
    requested = []

    def _fake_gate(products, scfg_arg):
        return {"enabled": True, "threshold_g_m3": 3.5,
                "threshold_ref": "test", "max_vil_density_g_m3": 1.453,
                "evaluated": True, "fetch_full_set": False,
                "reason": "hail_implausible"}

    def _fake_struct(config, scfg_arg, ref_shape, warnings, **kw):
        return {"vil": None, "etm": None, "poh": None, "sri": None,
                "low": None, "high": None}

    def _fake_cappi(config, products, ref_time_ms, **kw):
        requested.append(list(products))
        rds = {p: _frame(value=35.0, ts_ms=int(ref_time_ms)) for p in products}
        summary = {"requested": len(products), "fetched": len(products),
                   "fallback": 0, "failed": [], "bytes": 0, "seconds": 0.0,
                   "ref_time_iso": None, "products": {}}
        return rds, summary

    monkeypatch.setattr(eng, "_hail_plausibility_gate", _fake_gate)
    monkeypatch.setattr(eng, "_fetch_structure_products", _fake_struct)
    monkeypatch.setattr(fetch_mod, "fetch_cappi_set", _fake_cappi)
    monkeypatch.setattr(fetch_mod, "fetch_frames",
                        lambda config, product=None, max_frames=None: ([], [], None))
    monkeypatch.setattr("radar_engine.phase2.lightning.fetch_ltg",
                        lambda epoch_ms=None, **kw: {"strikes": [], "count": 0})
    monkeypatch.setattr("radar_engine.phase2.lightning_afa.fetch_afa",
                        lambda **kw: (_ for _ in ()).throw(Exception("no_afa")))
    bundle = _bundle()
    eng._phase2_evaluate(bundle, cfg)
    return bundle, requested, scfg


def test_wiring_fetches_four_only_when_enabled_and_severity_unchanged(monkeypatch):
    b0, r0, _ = _run_phase2(False, monkeypatch)
    b1, r1, _ = _run_phase2(True, monkeypatch)
    assert r0 == [["CAPPI_2", "CAPPI_6"]]
    assert r1 == [["CAPPI_2", "CAPPI_4", "CAPPI_6", "CAPPI_8"]]
    p2 = b1.phase2
    assert p2["cappi_fetch"]["cappi_mode"] == "candidate_4"
    assert p2["cappi_fetch"]["candidate_trigger"]["value"] is True
    assert p2["cappi_fetch"]["candidate_trigger"]["null_reason"] is None
    assert p2["cappi_fetch"]["candidate_trigger"]["max_soi"] == 80.0
    # Fase 1 / SOI / severita' BYTE-IDENTICI fra i due percorsi
    for c0, c1 in zip(b0.convective_structures, b1.convective_structures):
        for k in ("soi", "soi_v2", "level", "candidate", "confidence",
                  "phase", "status", "severe_stack", "hail", "overhang"):
            assert c0.get(k) == c1.get(k), k
        mesh = ((c1.get("severe_stack") or {}).get("hail") or {}).get("mesh_mm") or {}
        # MESH non viene inventato: value=None e motivo esplicito (il motivo
        # esatto dipende dal contesto sintetico, qui mancano i livelli termici)
        assert mesh.get("value") is None
        if mesh:
            assert isinstance(mesh.get("null_reason"), str)
            assert mesh.get("null_reason")
    # il volume 3D vede invece i 4 livelli
    assert sum(1 for rd in (b1._volume_cappi or {}).values() if rd is not None) == 4
    assert sum(1 for rd in (b0._volume_cappi or {}).values() if rd is not None) == 2
