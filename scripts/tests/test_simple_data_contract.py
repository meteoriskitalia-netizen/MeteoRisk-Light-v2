# -*- coding: utf-8 -*-
"""Contratto DATI della vista Simple (`simple/index.html`).

Verifica sui JSON REALI del working tree che i campi usati dalla pagina
esistano e abbiano i tipi attesi. Non simula: se un file manca, il test e'
SALTATO con motivo dichiarato (non un verde finto) — tranne `province` e
`geography`, che sono il minimo indispensabile della pagina e quindi
obbligatori.

Copre:
  - 107 province, 3 giorni, 20 campi per giorno, `selected_point` presente;
  - weekly.json: 107 province x 7 giorni x 8 campi (tmax,tmin,prcp,pprob,
    wind,gust,code,et0);
  - simple_hourly.json: 107 x 72 x 5 (t,p,pp,w,c) — skip se non pubblicato;
  - criticality.json: 187 zone, 4 codici di livello, disclaimer e licenza;
  - radar/latest.json: stato, timestamp, celle (per il layer Radar);
  - stations/latest.json: elenco stazioni e campi per le 4 variabili;
  - province_italiane.geojson: 107 feature con SIGLA e geometria.
"""
import io
import json
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LATEST = os.path.join(ROOT, "data", "latest")
SIMPLE_HTML = os.path.join(ROOT, "simple", "index.html")

PROVINCES = os.path.join(LATEST, "meteorisk-provinces.json")
WEEKLY = os.path.join(LATEST, "weekly.json")
HOURLY = os.path.join(LATEST, "simple_hourly.json")
HYDRO = os.path.join(ROOT, "data", "hydro", "criticality.json")
RADAR = os.path.join(ROOT, "data", "radar", "latest.json")
STATIONS = os.path.join(ROOT, "data", "stations", "latest.json")
GEO = os.path.join(ROOT, "data", "geography", "province_italiane.geojson")

DAY_FIELDS = (
    "day", "temp_max", "temp_min", "precip_sum", "prob_max", "wind_max",
    "weathercode", "humidity", "pressure", "precip_hourly", "showers", "gusts",
    "wind_avg", "wind100_max", "cape_max", "freezing_max", "rain_intensity_max",
    "thunder_hours", "hail_hours", "snow_hours",
)


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_simple_html_exists_and_loads_the_expected_files():
    with io.open(SIMPLE_HTML, encoding="utf-8") as fh:
        html = fh.read()
    for name in ("meteorisk-provinces.json", "province_italiane.geojson",
                 "weekly.json", "simple_hourly.json", "criticality.json",
                 "radar/latest.json", "stations/latest.json"):
        assert name in html, "simple/index.html non referenzia %s" % name


def test_required_provinces_contract():
    data = _load(PROVINCES)
    provs = data["provinces"]
    assert len(provs) == 107
    sigle = set()
    for p in provs:
        assert p["sigla"] and p["prov"] and p["region"]
        assert "selected_point" in p and p["selected_point"]
        assert isinstance(p["selected_point"]["id"], int)
        assert len(p["days"]) == 3
        for day in p["days"]:
            for field in DAY_FIELDS:
                assert field in day, "%s senza %s" % (p["sigla"], field)
            for field in ("temp_max", "temp_min", "precip_sum", "wind_max", "gusts"):
                assert day[field] is None or isinstance(day[field], (int, float))
            for field in ("thunder_hours", "hail_hours", "snow_hours"):
                assert day[field] is None or isinstance(day[field], (int, float))
        sigle.add(p["sigla"])
    assert len(sigle) == 107


def test_required_geojson_contract():
    geo = _load(GEO)
    assert geo["type"] == "FeatureCollection"
    feats = geo["features"]
    assert len(feats) == 107
    for f in feats:
        props = f["properties"]
        assert props.get("SIGLA"), "feature senza SIGLA"
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")


@pytest.mark.skipif(not os.path.exists(WEEKLY), reason="weekly.json assente")
def test_weekly_contract():
    w = _load(WEEKLY)
    assert w["version"] == "meteorisk.weekly/1"
    assert w["forecast_days"] == 7
    assert len(w["days"]) == 7
    assert len(w["provinces"]) == 107
    expected = ("tmax", "tmin", "prcp", "pprob", "wind", "gust", "code", "et0")
    for sigla, recs in w["provinces"]:
        assert isinstance(sigla, str) and len(sigla) <= 3
        assert len(recs) == 7
        for r in recs:
            for k in expected:
                assert k in r, "%s: manca %s" % (sigla, k)


@pytest.mark.skipif(not os.path.exists(HOURLY), reason="simple_hourly.json non pubblicato")
def test_simple_hourly_contract():
    h = _load(HOURLY)
    assert h["schema"] == "simple-hourly/1"
    assert h["fields"] == ["t", "p", "pp", "w", "c"]
    assert len(h["hours"]) == 72
    assert len(h["provinces"]) == 107
    for sigla, entry in h["provinces"].items():
        assert isinstance(entry["id"], int)
        assert len(entry["h"]) == 72
        for row in entry["h"]:
            assert len(row) == 5
            assert row[2] is None or isinstance(row[2], int)
            assert row[4] is None or isinstance(row[4], int)


@pytest.mark.skipif(not os.path.exists(HYDRO), reason="criticality.json assente")
def test_hydro_contract():
    h = _load(HYDRO)
    assert h["schema"].startswith("meteorisk.hydro.criticality/")
    assert h["licence"] and h["attribution"] and h["disclaimer"]
    assert len(h["zones"]) == 187
    codes = set(h["levels_legend"]["codes"])
    assert codes == {"none", "yellow", "orange", "red"}
    for z in h["zones"]:
        assert z["name"] and "polygons" in z
        assert z["dpc_map_class"] in codes | {"unknown"}
        assert isinstance(z["polygons"], list)
        for poly in z["polygons"]:
            for ring in poly:
                assert len(ring) >= 6 and len(ring) % 2 == 0
    assert len(h["days"]) >= 1
    assert h["days"][0]["levels_by_zone"]


@pytest.mark.skipif(not os.path.exists(RADAR), reason="radar/latest.json assente")
def test_radar_contract():
    r = _load(RADAR)
    for key in ("status", "radar_timestamp", "data_latency_minutes", "cells_count"):
        assert key in r
    if r.get("radar_timestamp"):
        assert r["radar_timestamp"].endswith("Z")


@pytest.mark.skipif(not os.path.exists(STATIONS), reason="stations/latest.json assente")
def test_stations_contract_for_simple_variables():
    s = _load(STATIONS)
    assert len(s["stations"]) > 0
    # I campi usati dalla Simple esistono (almeno come chiave) e sono numerici
    # o null. Se un campo non ci fosse affatto, la pagina mostrerebbe 0 stazioni.
    fields = ("temperature_2m", "rain_3h_mm", "wind_mean_1h_ms",
              "gust_max_1h_ms", "wind_speed_10m", "wind_gust_10m")
    present = {f: 0 for f in fields}
    for st in s["stations"]:
        for f in fields:
            v = st.get(f)
            if isinstance(v, (int, float)):
                present[f] += 1
    for f in ("temperature_2m", "wind_speed_10m"):
        assert present[f] > 0, "nessuna stazione con %s" % f
    # Almeno una delle variabili "derivate" deve avere copertura, altrimenti
    # le opzioni Pioggia 3h / Vento / Raffica sarebbero sempre vuote.
    assert any(present[f] > 0 for f in ("rain_3h_mm", "wind_mean_1h_ms", "gust_max_1h_ms"))
