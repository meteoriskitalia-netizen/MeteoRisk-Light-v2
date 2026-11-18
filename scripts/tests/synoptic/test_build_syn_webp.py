# -*- coding: utf-8 -*-
"""Test del produttore WebP sinottico: set REALE, nessuna rete."""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MOD = ROOT / "scripts" / "synoptic" / "build_syn_webp.py"
spec = importlib.util.spec_from_file_location("build_syn_webp", MOD)
bw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bw)

SOURCE = ROOT / "data" / "syn_webp"


def test_validate_real_set():
    frames, meas = bw.validate_set(SOURCE)
    assert len(frames) == 6
    assert meas["run_reference_time"] == "2026-11-18T00:00:00Z"
    assert {f["lead_hours"] for f in frames} == {0, 27, 72, 108, 144, 192}
    assert sum(f["fronts"] for f in frames) == 201
    for f in frames:
        assert f["webp_bytes"] > 80_000
        assert len(f["webp_sha256"]) == 64
        assert f["blocks_drawn"] is False
        assert f["experimental"] is True


def test_index_has_didascalia_and_warning():
    idx = bw.build_index(SOURCE)
    assert idx["experimental"] is True
    assert idx["experimental_warning"] == "SPERIMENTALE — non validato, nessuna accuratezza misurata"
    assert idx["publication_status"] == "development_only"
    assert "nessun POD/FAR/CSI" in idx["validation"]
    assert idx["source"] == "Open-Meteo .om / CC BY 4.0"
    assert idx["license"] == "CC BY 4.0 (attribuzione richiesta)"
    for f in idx["frames"]:
        c = f["caption"]
        assert "Run 2026-11-18T00:00:00Z" in c
        assert "valid time" in c and "lead +" in c
        assert "Open-Meteo .om / CC BY 4.0" in c
        assert f["experimental_warning"] == idx["experimental_warning"]


def test_publish_is_constant_size_and_allowlisted(tmp_path):
    out = tmp_path / "out"
    n = bw.publish(SOURCE, out)
    assert n == 6
    files = sorted(p.name for p in out.iterdir() if p.is_file())
    assert len(files) == 7, files  # 6 WebP + index.json, NULL'altro
    assert all(f.endswith(".webp") or f == "index.json" for f in files), files
    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert len(idx["frames"]) == 6
    # secondo run: stessa dimensione, nessun residuo
    bw.publish(SOURCE, out)
    assert len([p for p in out.iterdir() if p.is_file()]) == 7


def test_tampered_webp_is_rejected(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    for p in SOURCE.iterdir():
        if p.is_file():
            shutil.copy2(p, bad / p.name)
    wp = bad / "syn_atlantic_ecmwf_20261118T0000_L000h.webp"
    b = bytearray(wp.read_bytes())
    b[-1] ^= 0xFF
    wp.write_bytes(b)
    with pytest.raises(bw.ValidationError):
        bw.validate_set(bad)


def test_sidecar_with_blocks_drawn_is_rejected(tmp_path):
    bad = tmp_path / "bad2"
    bad.mkdir()
    for p in SOURCE.iterdir():
        if p.is_file():
            shutil.copy2(p, bad / p.name)
    side = bad / "syn_atlantic_ecmwf_20261118T0000_L000h.json"
    d = json.loads(side.read_text(encoding="utf-8"))
    d["blocks"]["drawn"] = True
    side.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(bw.ValidationError):
        bw.validate_set(bad)
