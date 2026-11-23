#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test dell'ARCHIVIO SRT1 PER PROVINCIA — nessuna rete, nessun dato DPC reale.

Coprono i difetti che il modulo deve prevenire:
  1. la griglia NON viene archiviata: si scrivono statistiche per provincia;
  2. una provincia fuori copertura radar NON riceve statistiche (valid=0,
     mean/max/p90 null) -> il verificatore dira' "nessuna osservazione", non
     "0 mm";
  3. la sentinella -9999 e' esclusa, 0.0 e' un valore valido;
  4. `valid` e' pixel validi / pixel della provincia (non del totale griglia);
  5. il percorso e' YYYY/MM/DD/HH.json in UTC e la scrittura e' atomica;
  6. il tempo VALIDO e' lo slot (`--as-of`), non il tag di pubblicazione TIFF;
  7. retention: le ore piu' vecchie vengono rimosse, le altre restano;
  8. licenza: ogni payload e' un derivato CC BY-SA 4.0 con attribuzione, e il
     branch porta README + LICENCE;
  9. la soglia di copertura coincide con quella del verificatore.
"""

import json
import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine

from radar_engine import accumulation as acc
from radar_engine import srt1_province_archive as prov

TRANSFORM = Affine(0.01, 0.0, 10.0, 0.0, -0.01, 46.0)
SHAPE = (20, 20)


def _geometry(lon0, lon1):
    return {"type": "Polygon", "coordinates": [[
        [lon0, 45.80], [lon1, 45.80], [lon1, 46.00], [lon0, 46.00],
        [lon0, 45.80]]]}


def _write_geo(tmp_path):
    """Tre province: AA [10.00-10.05), BB [10.05-10.10), CC [10.10-10.15)."""
    payload = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"SIGLA": "AA"},
         "geometry": _geometry(10.00, 10.05)},
        {"type": "Feature", "properties": {"SIGLA": "BB"},
         "geometry": _geometry(10.05, 10.10)},
        {"type": "Feature", "properties": {"SIGLA": "CC"},
         "geometry": _geometry(10.10, 10.15)},
    ]}
    path = tmp_path / "provinces.geojson"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _write_tif(tmp_path, name="srt1.tif", tiff_datetime=None):
    """AA: due pixel bagnati (5.0, 2.5) e il resto 0.0.
    BB: solo le prime 4 righe accumulate (3.0), il resto -9999 (valid 0.2).
    CC: tutto -9999 (fuori copertura, valid 0.0)."""
    arr = np.full(SHAPE, 0.0, dtype="float32")
    arr[0, 0] = 5.0
    arr[0, 1] = 2.5
    arr[:, 5:10] = 3.0
    arr[4:, 5:10] = -9999.0
    arr[:, 10:15] = -9999.0
    path = tmp_path / name
    with rasterio.open(path, "w", driver="GTiff", height=SHAPE[0],
                       width=SHAPE[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=TRANSFORM,
                       nodata=None) as dst:
        dst.write(arr, 1)
        if tiff_datetime:
            dst.update_tags(TIFFTAG_DATETIME=tiff_datetime)
    return str(path)


def _raster(tmp_path):
    return acc.read_accumulation(_write_tif(tmp_path))


# --------------------------------------------------------------------------- #
# 1-4: statistiche per provincia
# --------------------------------------------------------------------------- #
def test_province_stats_valid_wet_and_max(tmp_path):
    raster = _raster(tmp_path)
    masks = prov.build_province_masks(raster, _write_geo(tmp_path))
    assert set(masks) == {"AA", "BB", "CC"}

    aa = prov.province_stats(raster, masks["AA"])
    assert aa["px"] == 100
    assert aa["vpx"] == 100
    assert aa["valid"] == 1.0
    assert aa["max"] == pytest.approx(5.0)
    assert aa["wet"] == pytest.approx(0.02)          # 2 pixel > 0 su 100
    assert aa["mean"] == pytest.approx(0.075)        # (5.0 + 2.5) / 100

    # BB: 20 pixel validi su 100 -> 0.2 (sotto soglia, ma la statistica esiste)
    bb = prov.province_stats(raster, masks["BB"])
    assert bb["px"] == 100
    assert bb["vpx"] == 20
    assert bb["valid"] == pytest.approx(0.2)
    assert bb["max"] == pytest.approx(3.0)

    # CC: nessun pixel valido -> nessun valore inventato
    cc = prov.province_stats(raster, masks["CC"])
    assert cc["px"] == 100
    assert cc["vpx"] == 0
    assert cc["valid"] == 0.0
    assert cc["max"] is None
    assert cc["mean"] is None
    assert cc["p90"] is None
    assert cc["wet"] is None


def test_zero_is_valid_and_sentinel_is_excluded(tmp_path):
    raster = _raster(tmp_path)
    # AA e' tutto 0.0 tranne 2 pixel: 0.0 NON e' un buco
    masks = prov.build_province_masks(raster, _write_geo(tmp_path))
    assert masks["AA"].sum() == 100
    assert prov.province_stats(raster, masks["AA"])["valid"] == 1.0
    # CC e' tutto sentinella: 0 pixel validi
    assert prov.province_stats(raster, masks["CC"])["vpx"] == 0


def test_payload_totals_and_coverage_count(tmp_path):
    raster = _raster(tmp_path)
    geo = _write_geo(tmp_path)
    payload = prov.build_slot_payload(raster, 1795426200000, geo_path=geo)
    assert payload["totals"]["provinces_total"] == 3
    # solo AA supera MIN_VALID_FRACTION (BB=0.2, CC=0.0)
    assert payload["totals"]["provinces_with_coverage"] == 1
    assert payload["totals"]["max_mm"] == pytest.approx(5.0)
    # `valid_fraction` e' la copertura dell'INTERA griglia (220/400): 120 px
    # validi nelle 3 province + 100 px fuori dalle province (cols 15-19), che
    # sono 0.0 e quindi validi. Non e' la media delle province.
    assert payload["totals"]["valid_px"] == 220
    assert payload["totals"]["total_px"] == 400
    assert payload["totals"]["valid_fraction"] == pytest.approx(0.55)


# --------------------------------------------------------------------------- #
# 5: percorso, scrittura atomica, budget
# --------------------------------------------------------------------------- #
def test_archive_path_is_utc_hour_file(tmp_path):
    raster = _raster(tmp_path)
    out_dir = tmp_path / "archive"
    payload = prov.archive_raster(raster, 1795426200000, str(out_dir),
                                  geo_path=_write_geo(tmp_path))
    path = Path(payload["_path"])
    assert path == out_dir / "2026" / "11" / "23" / "09.json"
    assert path.is_file()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["valid_time"] == "2026-11-23T09:30:00Z"
    assert stored["valid_time_ms"] == 1795426200000
    assert set(stored["provinces"]) == {"AA", "BB", "CC"}
    assert payload["_output_bytes"] == path.stat().st_size
    assert path.stat().st_size < 200 * 1024


def test_write_hour_is_atomic_and_leaves_no_tmp(tmp_path):
    raster = _raster(tmp_path)
    out_dir = tmp_path / "archive"
    payload = prov.build_slot_payload(raster, 1795426200000,
                                      geo_path=_write_geo(tmp_path))
    prov.write_hour(str(out_dir), payload)
    files = [p for p in out_dir.rglob("*") if p.is_file()]
    assert len(files) == 1
    assert files[0].suffix == ".json"
    assert not list(out_dir.rglob(".srt1p-*"))


# --------------------------------------------------------------------------- #
# 6: il tempo valido e' lo slot, non il tag TIFF
# --------------------------------------------------------------------------- #
def test_valid_time_prefers_as_of_over_tiff_tag(tmp_path):
    tif = _write_tif(tmp_path, tiff_datetime="2026:11:23 09:36:50")
    out_dir = tmp_path / "archive"
    payload = prov.run(str(out_dir), as_of_ms=1795426200000, tif_path=tif,
                       geo_path=_write_geo(tmp_path))
    assert payload["status"] == "ok"
    # 09:36:50 e' la PUBBLICAZIONE (~+7 min): il tempo valido resta lo slot
    assert payload["valid_time"] == "2026-11-23T09:30:00Z"
    assert (out_dir / "2026" / "11" / "23" / "09.json").is_file()
    stored = json.loads((out_dir / "2026" / "11" / "23" / "09.json")
                        .read_text(encoding="utf-8"))
    assert stored["valid_time"] == "2026-11-23T09:30:00Z"


# --------------------------------------------------------------------------- #
# 7: retention
# --------------------------------------------------------------------------- #
def test_prune_removes_only_old_hours(tmp_path):
    root = tmp_path / "archive"
    (root / "2024" / "01" / "01").mkdir(parents=True)
    (root / "2024" / "01" / "01" / "00.json").write_text("{}", encoding="utf-8")
    (root / "2026" / "11" / "23").mkdir(parents=True)
    (root / "2026" / "11" / "23" / "09.json").write_text("{}", encoding="utf-8")
    now_ms = 1795426200000  # 2026-11-23T09:30Z
    removed, kept = prov.prune_archive(str(root), keep_days=400, now_ms=now_ms)
    assert removed == 1
    assert kept == 1
    assert not (root / "2024" / "01" / "01" / "00.json").exists()
    assert (root / "2026" / "11" / "23" / "09.json").exists()


def test_prune_keeps_unrelated_files(tmp_path):
    root = tmp_path / "archive"
    (root / "2024" / "01" / "01").mkdir(parents=True)
    (root / "README.md").write_text("x", encoding="utf-8")
    (root / "2024" / "01" / "01" / "00.json").write_text("{}", encoding="utf-8")
    prov.prune_archive(str(root), keep_days=1, now_ms=1795426200000)
    assert (root / "README.md").is_file()


# --------------------------------------------------------------------------- #
# 8: licenza (opera derivata CC BY-SA 4.0, branch pubblico -> share-alike)
# --------------------------------------------------------------------------- #
def test_payload_declares_licence_and_derivative(tmp_path):
    raster = _raster(tmp_path)
    payload = prov.build_slot_payload(raster, 1795426200000,
                                      geo_path=_write_geo(tmp_path))
    assert payload["licence"] == "CC BY-SA 4.0"
    assert payload["licence_url"].startswith("https://creativecommons.org/")
    assert "Radar-DPC" in payload["attribution"]
    assert "Dipartimento della Protezione Civile" in payload["attribution"]
    assert "derivata" in payload["derivative"].lower()
    assert "share-alike" in payload["derivative"].lower()


def test_branch_docs_written_with_attribution(tmp_path):
    root = tmp_path / "archive"
    prov.ensure_branch_docs(str(root))
    readme = (root / prov.README_FILE).read_text(encoding="utf-8")
    licence = (root / prov.LICENCE_FILE).read_text(encoding="utf-8")
    for text in (readme, licence):
        assert "CC BY-SA 4.0" in text
        assert "Radar-DPC" in text
    assert "share-alike" in readme.lower()
    assert "share-alike" in licence.lower()


# --------------------------------------------------------------------------- #
# 9: la soglia coincide con quella del verificatore
# --------------------------------------------------------------------------- #
def test_threshold_matches_verifier():
    from verification import risk_verification as rv
    assert rv.MIN_RADAR_VALID_FRACTION == prov.MIN_VALID_FRACTION


# --------------------------------------------------------------------------- #
# wiring: accumulation.run(province_dir=...) archivia senza secondo download
# --------------------------------------------------------------------------- #
def test_accumulation_archive_provinces_from_same_raster(tmp_path):
    raster = _raster(tmp_path)
    out_dir = tmp_path / "archive"
    report = acc._archive_provinces(raster, 1795426200000, str(out_dir),
                                    geo_path=_write_geo(tmp_path))
    assert report["status"] == "ok"
    assert report["provinces"] == 3
    assert report["provinces_with_coverage"] == 1
    assert report["bytes"] < 200 * 1024
    assert Path(report["file"]).is_file()


def test_accumulation_archive_failure_never_raises(tmp_path):
    raster = _raster(tmp_path)
    # geografia inesistente: l'archivio fallisce ma la funzione non solleva
    report = acc._archive_provinces(raster, 1795426200000,
                                    str(tmp_path / "archive"),
                                    geo_path=str(tmp_path / "nope.geojson"))
    assert report["status"] == "unavailable"
    assert "geojson_missing" in report["reason"]


def test_no_alert_words_in_payload(tmp_path):
    raster = _raster(tmp_path)
    payload = prov.build_slot_payload(raster, 1795426200000,
                                      geo_path=_write_geo(tmp_path))
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for word in ("allerta", "allarme", "warning", "pericolo"):
        assert word not in blob
