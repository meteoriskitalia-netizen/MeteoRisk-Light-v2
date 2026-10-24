#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test del REPLAY DETERMINISTICO `--as-of` (release 1.2.4.20).

Cosa coprono (senza rete, senza dati radar reali):
  - clock: parsing ISO-8601, arrotondamento dell'ancora al passo, modalita'
    live vs replay;
  - ancoraggio delle CINQUE sorgenti che leggevano l'orologio:
      1) slot radar        (fetch.fetch_frames(anchor_ms=...))
      2) VIL/ETM/POH       (main._fetch_structure_products(replay=True))
      3) IR_108            (main._fetch_ir108(replay=True))
      4) ambiente          (environment: URL di ARCHIVIO + indice per istante)
      5) fulmini           (lightning.ltg_epoch_floor(now_ms=...))
  - degradazione DICHIARATA: ambiente non disponibile in archivio ->
    `environment_unavailable_for_as_of`; nessuna chiamata all'endpoint
    forecast in modalita' replay;
  - determinismo: due costruzioni dello stesso output sono byte-identiche;
  - cablaggio CLI: `--as-of` inietta il clock, un valore invalido esce 2.
"""

import json
import sys

import numpy as np
import pytest

from radar_engine import clock as clock_mod
from radar_engine import fetch
from radar_engine import main as main_mod
from radar_engine import models
from radar_engine import output
from radar_engine.phase2 import environment as env
from radar_engine.phase2 import lightning as ltg


# ---------------------------------------------------------------------------
# 0. Helper
# ---------------------------------------------------------------------------
def base_config(tmp_path, **over):
    cfg = {
        "source": {
            "api_base": "https://example.invalid",
            "origin_header": "https://example.invalid",
            "product_type": "VMI",
            "history_frames": 6,
            "period_s": 300,
            "http_timeout_s": 5,
            "download_timeout_s": 5,
            "max_download_retries": 0,
            "cache_dir": str(tmp_path),
            "keep_raw_frames": False,
        },
        "preprocess": {"nodata_values": [], "geo_plausible_bbox": None},
    }
    cfg.update(over)
    return cfg


def archive_payload(day="2026-07-01", with_profile=True):
    """Payload minimo con la forma di archive-api (24 slot orari)."""
    times = ["%sT%02d:00" % (day, h) for h in range(24)]
    n = len(times)
    hourly = {
        "time": times,
        "temperature_2m": [22.0] * n,
        "dew_point_2m": [12.0] * n,
        "pressure_msl": [1013.0] * n,
        "lifted_index": [-2.0] * n,
        "wind_speed_10m": [4.0] * n,
        "wind_direction_10m": [180.0] * n,
        "wind_speed_600hPa": [8.0] * n,
        "wind_direction_600hPa": [200.0] * n,
        "wind_speed_450hPa": [15.0] * n,
        "wind_direction_450hPa": [220.0] * n,
        "temperature_600hPa": [-10.0] * n,
        "temperature_450hPa": [-25.0] * n,
    }
    if with_profile:
        hourly.update({
            "cape": [800.0] * n,
            "convective_inhibition": [-50.0] * n,
            "freezing_level_height": [3200.0] * n,
            "temperature_700hPa": [5.0] * n,
            "temperature_500hPa": [-18.0] * n,
            "geopotential_height_700hPa": [3000.0] * n,
            "geopotential_height_500hPa": [5500.0] * n,
            "geopotential_height_1000hPa": [110.0] * n,
            "geopotential_height_925hPa": [770.0] * n,
            "geopotential_height_850hPa": [1450.0] * n,
            "geopotential_height_600hPa": [4400.0] * n,
            "geopotential_height_450hPa": [6200.0] * n,
        })
        for pl in (1000, 925, 850, 700, 500):
            hourly["wind_speed_%dhPa" % pl] = [10.0] * n
            hourly["wind_direction_%dhPa" % pl] = [200.0] * n
    else:
        # Come l'archivio REALE su questo host (misurato il 2026-10-24):
        # HTTP 200 con le variabili di profilo a null.
        for key in ("cape", "convective_inhibition", "lifted_index",
                    "freezing_level_height", "temperature_700hPa",
                    "temperature_500hPa", "geopotential_height_700hPa",
                    "geopotential_height_500hPa", "wind_speed_600hPa",
                    "wind_direction_600hPa", "wind_speed_450hPa",
                    "wind_direction_450hPa", "temperature_600hPa",
                    "temperature_450hPa", "geopotential_height_1000hPa",
                    "geopotential_height_925hPa", "geopotential_height_850hPa",
                    "geopotential_height_600hPa", "geopotential_height_450hPa",
                    "wind_speed_1000hPa", "wind_direction_1000hPa",
                    "wind_speed_925hPa", "wind_direction_925hPa",
                    "wind_speed_850hPa", "wind_direction_850hPa",
                    "wind_speed_700hPa", "wind_direction_700hPa",
                    "wind_speed_500hPa", "wind_direction_500hPa"):
            hourly[key] = [None] * n
    return {"hourly": hourly}


# ---------------------------------------------------------------------------
# 1. Clock
# ---------------------------------------------------------------------------
def test_clock_parse_forms():
    ms_z, assumed_z = clock_mod.parse_iso8601_utc("2026-07-01T13:20:00Z")
    assert assumed_z is False
    assert clock_mod.iso_from_ms(ms_z) == "2026-07-01T13:20:00Z"
    ms_off, assumed_off = clock_mod.parse_iso8601_utc("2026-07-01T13:20:00+00:00")
    assert ms_off == ms_z and assumed_off is False
    # istante SENZA fuso: assunto UTC e DICHIARATO (nessun fuso inventato)
    ms_naive, assumed_naive = clock_mod.parse_iso8601_utc("2026-07-01T13:20:00")
    assert ms_naive == ms_z and assumed_naive is True
    assert clock_mod.parse_iso8601_utc(None) is None
    with pytest.raises(ValueError):
        clock_mod.parse_iso8601_utc("ieri pomeriggio")


def test_clock_anchor_floors_to_product_step():
    clock = clock_mod.Clock.at_iso("2026-07-01T13:23:41Z")
    assert clock.replay is True
    assert clock.now_ms() == 1782912221000
    assert clock_mod.iso_from_ms(clock.anchor_ms(300)) == "2026-07-01T13:20:00Z"
    # nessun arrotondamento se il passo non e' dichiarato
    assert clock.anchor_ms(None) == clock.now_ms()


def test_clock_live_is_default_and_reads_the_clock():
    clock = clock_mod.from_config({})
    assert clock.replay is False
    assert clock.anchor_ms(300) is None
    assert clock.describe(300) is None
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).timestamp() * 1000
    assert abs(clock.now_ms() - now) < 5000


def test_clock_describe_contains_requested_and_anchor():
    clock = clock_mod.Clock.at_iso("2026-07-01T13:23:41Z")
    desc = clock.describe(300)
    assert desc["requested"] == "2026-07-01T13:23:41Z"
    assert desc["radar_anchor_iso"] == "2026-07-01T13:20:00Z"
    assert desc["slot_period_s"] == 300
    assert desc["degraded"] == []


# ---------------------------------------------------------------------------
# 2. Slot radar (fetch)
# ---------------------------------------------------------------------------
def test_fetch_frames_replay_uses_explicit_anchor_not_find_last(monkeypatch,
                                                                tmp_path):
    cfg = base_config(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("findLastProductByType chiamato in replay")

    monkeypatch.setattr(fetch.source_dpc, "get_latest_product", _boom)

    def _not_found(*a, **k):
        raise models.SourceError("not_found")

    monkeypatch.setattr(fetch.source_dpc, "download_product", _not_found)

    anchor = clock_mod.parse_iso8601_utc("2026-07-01T13:25:00Z")[0]
    frames, warnings, latest = fetch.fetch_frames(cfg, max_frames=6,
                                                  anchor_ms=anchor)
    assert frames == [] and latest is None
    # i 6 candidati sono esattamente T-25, T-20, ..., T0 dello slot richiesto
    got = [w.split("frame@")[1].split(" skipped")[0] for w in warnings]
    assert got == [str(anchor - i * 300000) for i in range(5, -1, -1)]


def test_fetch_frames_live_still_uses_find_last_product(monkeypatch, tmp_path):
    cfg = base_config(tmp_path)
    anchor = clock_mod.parse_iso8601_utc("2026-07-01T13:25:00Z")[0]
    calls = []

    def _latest(*a, **k):
        calls.append(1)
        return models.ProductInfo("VMI", anchor, 300)

    monkeypatch.setattr(fetch.source_dpc, "get_latest_product", _latest)

    def _not_found(*a, **k):
        raise models.SourceError("not_found")

    monkeypatch.setattr(fetch.source_dpc, "download_product", _not_found)
    frames, warnings, _latest_ts = fetch.fetch_frames(cfg, max_frames=2)
    assert calls == [1]                      # comportamento storico intatto
    assert len(warnings) == 2


# ---------------------------------------------------------------------------
# 3. VIL/ETM/POH e IR_108 ancorati allo slot della VMI
# ---------------------------------------------------------------------------
def test_structure_products_replay_fetches_at_vmi_slot(monkeypatch, tmp_path):
    cfg = base_config(tmp_path)
    scfg = {"product_vil": "VIL", "product_etm": "ETM", "product_poh": "POH"}
    ref_ms = clock_mod.parse_iso8601_utc("2026-07-01T13:25:00Z")[0]
    seen = []

    def _at(config, product, time_ms, period_s=None):
        seen.append((product, int(time_ms)))
        return None, {"error": "not_found"}

    monkeypatch.setattr(main_mod.fetch, "fetch_product_at", _at)

    def _boom(*a, **k):
        raise AssertionError("fetch_frames usato in replay per vil/etm/poh")

    monkeypatch.setattr(main_mod.fetch, "fetch_frames", _boom)
    warnings = []
    out = main_mod._fetch_structure_products(
        cfg, scfg, (1400, 1200), warnings, cappi_rd=None,
        ref_time_ms=ref_ms, replay=True)
    assert sorted(seen) == [("ETM", ref_ms), ("POH", ref_ms), ("VIL", ref_ms)]
    assert out == {"vil": None, "etm": None, "poh": None, "low": None,
                   "high": None}
    assert any("assente allo slot" in w for w in warnings)


def test_ir108_replay_fetches_at_vmi_slot(monkeypatch, tmp_path):
    cfg = base_config(tmp_path)
    ref_ms = clock_mod.parse_iso8601_utc("2026-07-01T13:25:00Z")[0]

    class _Rd(object):
        def __init__(self, shape):
            self.data = np.zeros(shape, dtype="float32")
            self.time_ms = ref_ms

    ref_rd = _Rd((12, 10))
    seen = []

    def _at(config, product, time_ms, period_s=None):
        seen.append((product, int(time_ms)))
        return _Rd((12, 10)), {}

    monkeypatch.setattr(main_mod.fetch, "fetch_product_at", _at)

    def _boom(*a, **k):
        raise AssertionError("fetch_frames usato in replay per IR_108")

    monkeypatch.setattr(main_mod.fetch, "fetch_frames", _boom)
    warnings = []
    rd = main_mod._fetch_ir108(cfg, {"product_ir108": "IR_108"}, ref_rd,
                               warnings, replay=True)
    assert rd is not None
    assert seen == [("IR_108", ref_ms)]


# ---------------------------------------------------------------------------
# 4. Ambiente: API di ARCHIVIO, indice per istante, degradazione dichiarata
# ---------------------------------------------------------------------------
def test_archive_url_is_the_archive_endpoint_and_has_the_day():
    url = env.build_archive_url(45.2, 7.6, "2026-07-01")
    assert "archive-api.open-meteo.com" in url
    assert "/v1/forecast" not in url
    assert "start_date=2026-07-01" in url and "end_date=2026-07-01" in url
    assert "timezone=GMT" in url


def test_pick_archive_index_floors_to_the_hour():
    times = archive_payload()["hourly"]["time"]
    ms = clock_mod.parse_iso8601_utc("2026-07-01T13:58:00Z")[0]
    idx = env.pick_archive_index(times, ms)
    assert times[idx] == "2026-07-01T13:00"
    ms2 = clock_mod.parse_iso8601_utc("2026-07-01T00:00:00Z")[0]
    assert times[env.pick_archive_index(times, ms2)] == "2026-07-01T00:00"
    assert env.pick_archive_index([], ms) is None


def test_environment_replay_uses_archive_url_and_no_forecast():
    urls = []
    ms = clock_mod.parse_iso8601_utc("2026-07-01T13:25:00Z")[0]

    def _client(url):
        urls.append(url)
        return archive_payload()

    detail = env.evaluate_environment(45.2, 7.6, openmeteo_client=_client,
                                      as_of_ms=ms)
    assert urls and all("archive-api.open-meteo.com" in u for u in urls)
    assert all("/v1/forecast" not in u for u in urls)
    assert detail["time_iso"] == "2026-07-01T13:00"
    assert detail["environment_source"].startswith("archive-api")
    assert detail["cape"] == 800.0


def test_environment_replay_without_profile_raises_declared_reason():
    ms = clock_mod.parse_iso8601_utc("2026-07-01T13:25:00Z")[0]
    with pytest.raises(env.EnvironmentFetchError) as exc:
        env.evaluate_environment(45.2, 7.6,
                                 openmeteo_client=lambda u: archive_payload(
                                     with_profile=False),
                                 as_of_ms=ms)
    assert "archive_unavailable:cape" in str(exc.value)


def test_environment_cached_records_error_for_degradation():
    ms = clock_mod.parse_iso8601_utc("2026-07-01T13:25:00Z")[0]
    errors = {}
    warnings = []
    detail = env.evaluate_environment_cached(
        45.2, 7.6, cache={}, grid_deg=0.1, as_of_ms=ms, errors=errors,
        evaluator=lambda lat, lon, timeout_s=None, as_of_ms=None: (_ for _ in
                                                                  ()).throw(
            env.EnvironmentFetchError("archive_unavailable:cape")))
    assert detail is None
    assert errors and "archive_unavailable:cape" in list(errors.values())[0]
    assert warnings == []


def test_severe_stack_environment_override_reason():
    from radar_engine import severe_stack as ss
    layer = ss.environment_layer(None,
                                 null_reason="environment_unavailable_for_as_of")
    assert layer["null_reason"] == "environment_unavailable_for_as_of"
    assert layer["cape_jkg"]["null_reason"] == "environment_unavailable_for_as_of"
    assert layer["cape_jkg"]["value"] is None
    # default invariato (nessuna chiave nuova senza override)
    plain = ss.environment_layer(None)
    assert "null_reason" not in plain
    assert plain["cape_jkg"]["null_reason"] == "open-meteo_cape_missing"


def test_ot_layer_override_reason():
    from radar_engine import severe_stack as ss
    layer = ss.ot_layer_from_details(None,
                                     null_reason="ir108_history_unavailable")
    assert layer["null_reason"] == "ir108_history_unavailable"
    default = ss.ot_layer_from_details(None)
    assert default["null_reason"] == "ir108_frame_unavailable"


# ---------------------------------------------------------------------------
# 5. Fulmini
# ---------------------------------------------------------------------------
def test_ltg_epoch_floor_uses_injected_now_ms():
    ms = clock_mod.parse_iso8601_utc("2026-07-01T13:23:41Z")[0]
    floor = ltg.ltg_epoch_floor(now_ms=ms)
    assert floor == clock_mod.parse_iso8601_utc("2026-07-01T13:20:00Z")[0]
    assert floor == ltg.ltg_epoch_floor(now_ms=ms)        # deterministico
    assert ltg.ltg_epoch_floor(backoff_steps=2, now_ms=ms) == floor - 2 * 300000


# ---------------------------------------------------------------------------
# 6. Output: la chiave as_of_mode compare SOLO in replay
# ---------------------------------------------------------------------------
def test_output_has_as_of_mode_only_in_replay():
    bundle = models.EngineBundle("ok", "2026-07-01T13:25:00Z", "test")
    assert "as_of_mode" not in output.build_latest(bundle)
    assert "as_of_mode" not in output.build_convective_structures(bundle)
    bundle.as_of_mode = {"requested": "2026-07-01T13:25:00Z", "degraded": [
        {"field": "lightning", "null_reason": "lightning_history_unavailable"}]}
    latest = output.build_latest(bundle)
    struct = output.build_convective_structures(bundle)
    assert latest["as_of_mode"]["requested"] == "2026-07-01T13:25:00Z"
    assert struct["as_of_mode"]["degraded"][0]["null_reason"] == \
        "lightning_history_unavailable"


def test_output_is_byte_identical_for_the_same_replay_instant():
    clock = clock_mod.Clock.at_iso("2026-07-01T13:25:00Z")
    a = models.EngineBundle("ok", clock.now_iso(), "test")
    b = models.EngineBundle("ok", clock.now_iso(), "test")
    for bundle in (a, b):
        bundle.as_of_mode = clock.describe(300)
        bundle.radar_timestamp_iso = clock.now_iso()
        bundle.radar_timestamp_ms = clock.now_ms()
        bundle.data_latency_minutes = 0.0
    assert json.dumps(output.build_latest(a)) == json.dumps(output.build_latest(b))


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------
def test_cli_as_of_injects_the_clock(monkeypatch):
    captured = {}

    def _stub_run(config, out_dir, product, max_frames, dry_run,
                  history_dir=None, clock=None):
        captured["clock"] = clock
        captured["config_clock"] = clock_mod.from_config(config)
        bundle = models.EngineBundle("ok", clock.now_iso(), "test")
        bundle.as_of_mode = clock.describe(300)
        return bundle, 0

    monkeypatch.setattr(main_mod, "_run", _stub_run)
    rc = main_mod.main(["--as-of", "2026-07-01T13:23:41Z", "--dry-run"])
    assert rc == 0
    assert captured["clock"].replay is True
    assert captured["clock"].now_iso() == "2026-07-01T13:23:41Z"
    assert captured["config_clock"] is captured["clock"]


def test_cli_default_is_live(monkeypatch):
    captured = {}

    def _stub_run(config, out_dir, product, max_frames, dry_run,
                  history_dir=None, clock=None):
        captured["clock"] = clock
        return models.EngineBundle("ok", clock.now_iso(), "test"), 0

    monkeypatch.setattr(main_mod, "_run", _stub_run)
    assert main_mod.main(["--dry-run"]) == 0
    assert captured["clock"].replay is False


def test_cli_invalid_as_of_exits_2(monkeypatch):
    def _stub_run(*a, **k):                     # non deve essere chiamato
        raise AssertionError("_run chiamato con --as-of invalido")

    monkeypatch.setattr(main_mod, "_run", _stub_run)
    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--as-of", "non-una-data", "--dry-run"])
    assert exc.value.code == 2
