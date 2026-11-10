#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test dell'harness di collimazione DPC <-> OPERA (1.2.4.27).

Metriche su array sintetici con bias noto: nessuna rete, nessun dato reale.
"""

import numpy as np
import pytest
from affine import Affine

from conftest import DPC_TM_WKT
from radar_engine import collimation

TRANSFORM = Affine(1000.0, 0.0, -600000.0, 0.0, -1000.0, 650000.0)


def _grid(n=400, value=30.0):
    return np.full((n, n), value, dtype="float64")


def test_ring_index_edges():
    d = np.array([0.0, 49.9, 50.0, 149.9, 150.0, 199.9, 200.0, 500.0])
    idx = collimation.ring_index(d)
    assert list(idx) == [0, 0, 1, 2, 3, 3, 4, 4]


def test_compare_known_bias_mae_rmse():
    n = 400
    base = 20.0 + (np.arange(n)[None, :] % 10)      # 20..29
    dpc = np.repeat(base, n, axis=0)
    opera = dpc + 4.0
    valid = np.ones((n, n), dtype=bool)
    out = collimation.compare(dpc, valid, opera, TRANSFORM, crs=DPC_TM_WKT)
    assert out["n_pairs"] == n * n
    tot = out["rings"][0]
    assert tot["bias_dbz"] == pytest.approx(4.0)
    assert tot["mae_dbz"] == pytest.approx(4.0)
    assert tot["rmse_dbz"] == pytest.approx(4.0)
    assert tot["correlation"] == pytest.approx(1.0)


def test_compare_rings_partition_pairs():
    n = 400
    rng = np.random.default_rng(7)
    dpc = 20.0 + rng.random((n, n)) * 20.0
    opera = dpc + 3.0 + rng.normal(0.0, 2.0, size=(n, n))
    valid = np.ones((n, n), dtype=bool)
    out = collimation.compare(dpc, valid, opera, TRANSFORM, crs=DPC_TM_WKT)
    expected = int(((dpc >= 20.0) & (opera >= 20.0)).sum())
    assert out["n_pairs"] == expected
    assert sum(r["n_pairs"] for r in out["rings"]) == out["n_pairs"]
    assert len(out["rings"]) == 5
    assert all(r["n_pairs"] > 0 for r in out["rings"])
    # anelli crescenti: la distanza media cresce
    labels = [r["label"] for r in out["rings"]]
    assert labels == ["0-50", "50-100", "100-150", "150-200", "200+"]


def test_compare_ignores_below_threshold_and_reports_coverage():
    n = 100
    dpc = _grid(n, 10.0)                    # sotto soglia 20 -> nessun eco
    opera = _grid(n, 35.0)
    valid = np.ones((n, n), dtype=bool)
    out = collimation.compare(dpc, valid, opera, TRANSFORM, crs=DPC_TM_WKT)
    assert out["n_pairs"] == 0
    assert sum(r["n_only_opera"] for r in out["rings"]) == n * n
    assert sum(r["n_echo_dpc"] for r in out["rings"]) == 0


def test_compare_declares_limits_and_distance_reference():
    n = 50
    dpc = _grid(n, 30.0)
    opera = _grid(n, 31.0)
    out = collimation.compare(dpc, np.ones((n, n), dtype=bool), opera,
                              TRANSFORM, crs=DPC_TM_WKT)
    assert out["distance_reference"] == "domain_center"
    assert any("MAX su 10 minuti" in s for s in out["limits"])


def test_compare_with_sites_uses_nearest_site():
    n = 100
    dpc = _grid(n, 30.0)
    opera = _grid(n, 30.0)
    sites = [(12.5, 42.0)]                  # centro del dominio TM
    out = collimation.compare(dpc, np.ones((n, n), dtype=bool), opera,
                              TRANSFORM, crs=DPC_TM_WKT, sites=sites)
    assert out["distance_reference"] == "nearest_dpc_site"
    # con un solo sito al centro la distribuzione per anello resta la stessa
    assert sum(r["n_pairs"] for r in out["rings"]) == n * n
