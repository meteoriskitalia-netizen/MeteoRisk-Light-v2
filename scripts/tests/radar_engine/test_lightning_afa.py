# -*- coding: utf-8 -*-
"""Test della sorgente fulmini AFA (EUMETSAT MTG LI, layer mtg_fd:li_afa).

Perche' esistono (misure del 2026-11-11):
  - il vecchio feed DPC `LGT/lgt_5min_*.bin` risponde 403 su 12/12 slot;
  - l'AFA risponde 200 senza token, archivio dal 2025-05-30, passo 5 minuti;
  - sul frame storico 2025-08-02T18:00Z (box Italia, griglia 0,01 gradi)
    la maschera da' 54.515 pixel attivi e 783 celle da 0,1 gradi: lo stesso
    ordine di grandezza misurato a mano sul servizio.

Nessun test tocca la rete: il trasporto e' iniettato con `opener`/`sleep`.
I PNG sintetici riproducono le forme realmente osservate (RGBA 8 bit e
palette 1 bit del frame completamente trasparente).
"""
import json
import os
import struct
import sys
import zlib

import numpy as np
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(os.path.dirname(_TESTS_DIR))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from radar_engine.phase2 import lightning_afa as afa   # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"
WARN_1805 = ("99 Nearest value used: time=2025-08-02T18:05:00.000Z ISO8601 "
             "(mtg_fd:li_afa)")
EXC_XML = (b'<?xml version="1.0" encoding="UTF-8"?>'
           b'<ServiceExceptionReport version="1.3.0">'
           b'<ServiceException code="InvalidDimensionValue" locator="time">'
           b'No nearest match found on mtg_fd:li_afa for time dimension'
           b'</ServiceException></ServiceExceptionReport>')


# ---------------------------------------------------------------------------
# PNG sintetici (stesse forme dei frame reali)
# ---------------------------------------------------------------------------
def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _png(width, height, bit_depth, color_type, rows, plte=None, trns=None):
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type,
                       0, 0, 0)
    scan = b"".join(b"\x00" + r for r in rows)
    out = PNG_SIG + _chunk(b"IHDR", ihdr)
    if plte is not None:
        out += _chunk(b"PLTE", plte)
    if trns is not None:
        out += _chunk(b"tRNS", trns)
    out += _chunk(b"IDAT", zlib.compress(scan)) + _chunk(b"IEND", b"")
    return out


def _rgba_png(width, height, active_cells):
    """RGBA 8 bit: pixel attivo (alpha 255) per le coordinate indicate."""
    rows = []
    for r in range(height):
        row = bytearray()
        for c in range(width):
            if (r, c) in active_cells:
                row += bytes((200, 10, 20, 255))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    return _png(width, height, 8, 6, rows)


def _palette_png(width, height, bit_depth, rows, plte, trns):
    return _png(width, height, bit_depth, 3, rows, plte=plte, trns=trns)


@pytest.fixture(autouse=True)
def _reset_throttle():
    afa.reset_throttle()
    yield
    afa.reset_throttle()


# ---------------------------------------------------------------------------
# Pure: tempo, URL, header
# ---------------------------------------------------------------------------
def test_afa_slot_epoch_is_the_5_minute_floor():
    import datetime as dt
    ms = int(dt.datetime(2025, 8, 2, 18, 12, 30,
                         tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert afa.epoch_to_iso(afa.afa_slot_epoch(now_ms=ms)) == \
        "2025-08-02T18:10:00Z"
    assert afa.epoch_to_iso(afa.afa_slot_epoch(now_ms=ms,
                                               backoff_steps=2)) == \
        "2025-08-02T18:00:00Z"
    assert afa.AFA_PERIOD_MS == 300000


def test_build_afa_getmap_url_declares_layer_crs_bbox_and_time():
    url = afa.build_afa_getmap_url("2025-08-02T18:00:00Z")
    assert url.startswith(afa.AFA_WMS_URL)
    assert "layers=mtg_fd%3Ali_afa" in url
    assert "crs=CRS%3A84" in url                 # lon,lat (non lat,lon)
    assert "bbox=6.00000%2C35.00000%2C19.00000%2C48.00000" in url
    assert "width=1300" in url and "height=1300" in url
    assert "format=image%2Fpng8" in url
    assert "time=2025-08-02T18%3A00%3A00Z" in url
    # override espliciti (usati dalla config)
    url2 = afa.build_afa_getmap_url("2025-08-02T18:00:00Z", bbox=(0, 30, 20, 50),
                                    width=200, height=100)
    assert "bbox=0.00000%2C30.00000%2C20.00000%2C50.00000" in url2
    assert "width=200" in url2 and "height=100" in url2


def test_parse_served_iso_from_warning_header():
    assert afa.parse_served_iso(WARN_1805) == "2025-08-02T18:05:00Z"
    assert afa.parse_served_iso(None) is None
    assert afa.parse_served_iso("99 Nearest value used: time=...") is None


# ---------------------------------------------------------------------------
# Pure: decodifica del frame
# ---------------------------------------------------------------------------
def test_decode_rgba_counts_pixels_area_and_cells():
    png = _rgba_png(10, 10, {(0, 0), (0, 1), (5, 5)})
    f = afa.decode_afa_png(png)
    assert f["width"] == 10 and f["height"] == 10
    assert f["active_pixels"] == 3
    assert f["area_km2"] > 0.0
    # sul bbox Italia un pixel da 10x10 vale 1,3 gradi: 3 celle da 0,1 gradi
    assert f["cells_0p1"] == 3
    # con un bbox di 0,1 gradi e 10 px (0,01 gradi/px) i tre pixel attivi
    # cadono nella STESSA cella da 0,1 gradi
    small = afa.decode_afa_png(png, bbox=(12.0, 41.0, 12.1, 41.1))
    assert small["cells_0p1"] == 1
    # coordinate: prima riga = bordo nord del bbox
    assert f["lat"][0] > f["lat"][1] or f["lat"][0] > f["lat"][2]
    assert all(6.0 <= v <= 19.0 for v in f["lon"])
    assert all(35.0 <= v <= 48.0 for v in f["lat"])


def test_decode_transparent_1bit_palette_returns_zero():
    """Forma REALE del frame senza scariche: palette 1 bit + tRNS [0,0]."""
    png = _palette_png(8, 4, 1, [b"\x00", b"\x00", b"\x00", b"\x00"],
                       plte=b"\xff\xff\xff\xff\xff\xff", trns=b"\x00\x00")
    f = afa.decode_afa_png(png)
    assert f["active_pixels"] == 0
    assert f["area_km2"] == 0.0
    assert f["cells_0p1"] == 0
    assert f["lon"].size == 0


def test_decode_palette_8bit_uses_trns_alpha():
    # 4x1: indici 0,1,0,1 ; palette con alpha 0 (indice 0) e 255 (indice 1)
    png = _palette_png(4, 1, 8, [bytes((0, 1, 0, 1))],
                       plte=b"\x00\x00\x00\xff\xff\xff", trns=b"\x00\xff")
    f = afa.decode_afa_png(png)
    assert f["active_pixels"] == 2


def test_decode_rejects_width_mismatch_and_non_png():
    png = _rgba_png(4, 4, {(0, 0)})
    with pytest.raises(ValueError, match="afa_width_mismatch"):
        afa.decode_afa_png(png, width=8)
    with pytest.raises(ValueError, match="not_a_png"):
        afa.decode_afa_png(b"nope")


def test_count_within_radius_uses_distance_not_pixel_index():
    # 0,01 gradi: il pixel (0,0) e' a ~ (6.005E, 47.995N)
    png = _rgba_png(100, 100, {(0, 0)})
    f = afa.decode_afa_png(png)
    lon0, lat0 = float(f["lon"][0]), float(f["lat"][0])
    inside = afa.count_within(f, lon0 + 0.2, lat0 - 0.2, 30.0)
    assert inside["active"] is True and inside["active_pixels"] == 1
    outside = afa.count_within(f, lon0 + 5.0, lat0 - 5.0, 30.0)
    assert outside["active"] is False and outside["active_pixels"] == 0
    with pytest.raises(ValueError, match="non_numeric"):
        afa.count_within(f, None, lat0, 30.0)
    with pytest.raises(ValueError, match="non_positive_radius"):
        afa.count_within(f, lon0, lat0, 0.0)


def test_field_for_candidate_contract():
    png = _rgba_png(10, 10, {(0, 0)})
    f = afa.decode_afa_png(png)
    f.update({"served_iso": "2025-08-02T18:00:00Z", "slot_offset_min": 0})
    lon0, lat0 = float(f["lon"][0]), float(f["lat"][0])
    hit = afa.field_for_candidate(f, lon0, lat0, 30.0)
    assert hit["value"] is True and hit["active_pixels"] == 1
    assert hit["null_reason"] is None
    assert hit["source"].startswith("EUMETSAT")
    assert "non un numero di fulmini" in hit["semantics"]
    miss = afa.field_for_candidate(f, lon0 + 5.0, lat0 - 5.0, 30.0)
    assert miss["value"] is False
    none = afa.field_for_candidate(None, lon0, lat0, 30.0,
                                   null_reason="afa_http_500")
    assert none["value"] is None and none["null_reason"] == "afa_http_500"
    assert none["active_pixels"] is None and none["source"].startswith("EUMETSAT")


def test_frame_summary_is_json_serializable():
    f = afa.decode_afa_png(_rgba_png(4, 4, {(1, 1)}))
    f.update({"served_iso": "2025-08-02T18:00:00Z",
              "requested_iso": "2025-08-02T18:00:00Z",
              "slot_offset_min": 0, "bytes": 10, "sha256": "a" * 64,
              "cache_hit": False})
    s = afa.frame_summary(f)
    assert json.loads(json.dumps(s))["active_pixels"] == 1
    assert "lon" not in s and "lat" not in s


# ---------------------------------------------------------------------------
# Wrapper I/O: opener iniettato, cache su disco, throttling
# ---------------------------------------------------------------------------
def _ok_opener(png, headers=None):
    calls = []

    def opener(url, timeout_s):
        calls.append(url)
        return png, dict(headers or {})
    return opener, calls


def test_fetch_afa_uses_disk_cache_and_one_request(tmp_path):
    png = _rgba_png(8, 8, {(0, 0)})
    opener, calls = _ok_opener(png)
    cache = str(tmp_path / "cache")
    f1 = afa.fetch_afa(now_ms=1754157600000, cache_dir=cache, opener=opener,
                       throttle_s=0.0)
    assert f1["cache_hit"] is False and len(calls) == 1
    assert f1["served_iso"] == "2025-08-02T18:00:00Z"
    assert os.path.exists(os.path.join(cache, "afa_1754157600000.png"))
    assert os.path.exists(os.path.join(cache, "afa_1754157600000.json"))
    f2 = afa.fetch_afa(now_ms=1754157600000, cache_dir=cache, opener=opener,
                       throttle_s=0.0)
    assert f2["cache_hit"] is True
    assert len(calls) == 1                     # nessuna seconda richiesta
    assert f2["sha256"] == f1["sha256"]
    assert f2["active_pixels"] == f1["active_pixels"]


def test_fetch_afa_declares_the_served_slot_from_warning(tmp_path):
    png = _rgba_png(8, 8, {(0, 0)})
    opener, _calls = _ok_opener(png, {"warning": WARN_1805})
    f = afa.fetch_afa(now_ms=1754157900000,        # richiesta 18:05
                      cache_dir=str(tmp_path), opener=opener, throttle_s=0.0)
    assert f["requested_iso"] == "2025-08-02T18:05:00Z"
    assert f["served_iso"] == "2025-08-02T18:05:00Z"
    assert f["slot_offset_min"] == 0


def test_fetch_afa_backs_off_when_the_slot_is_missing(tmp_path):
    """ServiceException `InvalidDimensionValue` -> slot precedente."""
    png = _rgba_png(8, 8, {(0, 0)})
    seen = []

    def opener(url, timeout_s):
        seen.append(url)
        if len(seen) == 1:
            return EXC_XML, {"content-type": "text/xml"}
        return png, {}

    sleeps = []
    f = afa.fetch_afa(now_ms=1754157900000, cache_dir=str(tmp_path),
                      opener=opener, sleep=sleeps.append)
    assert len(seen) == 2 and f["attempts"] == 2
    assert f["requested_iso"] == "2025-08-02T18:00:00Z"
    assert f["served_iso"] == "2025-08-02T18:00:00Z"
    assert len(sleeps) == 1 and sleeps[0] == pytest.approx(3.0, abs=0.5)


def test_fetch_afa_all_slots_missing_raises_declared_error(tmp_path):
    opener, calls = _ok_opener(EXC_XML, {"content-type": "text/xml"})
    with pytest.raises(afa.AfaFetchError) as exc:
        afa.fetch_afa(now_ms=1754157900000, cache_dir=str(tmp_path),
                      opener=opener, throttle_s=0.0, max_backoff_steps=2)
    assert exc.value.slot_missing is True
    assert "InvalidDimensionValue" in str(exc.value)
    assert len(calls) == 3                      # 0, -5, -10 minuti


def test_fetch_afa_non_png_payload_raises(tmp_path):
    opener, _calls = _ok_opener(b"<html>nope</html>")
    with pytest.raises(afa.AfaFetchError, match="afa_not_a_png"):
        afa.fetch_afa(now_ms=1754157600000, cache_dir=str(tmp_path),
                      opener=opener, throttle_s=0.0)


def test_fetch_afa_http_error_carries_status(tmp_path):
    def opener(url, timeout_s):
        raise afa.AfaFetchError("afa_http_503", http_status=503)

    with pytest.raises(afa.AfaFetchError) as exc:
        afa.fetch_afa(now_ms=1754157600000, cache_dir=str(tmp_path),
                      opener=opener, throttle_s=0.0)
    assert exc.value.http_status == 503
