# -*- coding: utf-8 -*-
"""Test della rete di centraline (`scripts/stations/fetch_stations.py`).

La fixture `fixtures/stations_payload.json` NON e' inventata: e' un ritaglio
byte-per-byte della risposta reale di MeteoHub del 2026-10-27 (finestra
05:51-06:51 UTC), con il solo `descr` ridotto ai codici usati e questi casi
limite presi apposta dal payload vero:

  * `boa` 44.21458,12.47587 "Nausicaa 2"              -> temperatura senza umidita'
  * `boa` 44.26714,12.35751 "Cervia Porto"             -> solo B22037 (livello mare)
  * `marefe` 44.67667,12.24944 "Porto Garibaldi"       -> ha B10004 (pressione)
  * `agrmet` 44.78042,10.03873 "Pieve di Cusignano"    -> temperatura+umidita'+precip
  * `open-trentino` 46.42259,10.69911                  -> vento
  * `simnpr` 44.93,10.38172 "Foce Naviglio"            -> solo B13215 (livello idro)
  * `ofa-01-cmcc` 41.268,16.0568 "Ofanto"              -> rete senza licenza verificata

Nessuna rete: il payload e' su disco e `normalize_payload` e' una funzione pura.
"""
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "stations"))

import fetch_stations as fs  # noqa: E402

FIXTURE = _HERE / "fixtures" / "stations_payload.json"
NETWORKS = fs.load_networks(_HERE.parent / "stations" / "networks.json")
NOW = datetime(2026, 10, 27, 6, 51, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc(payload):
    return fs.normalize_payload(payload, NETWORKS, generated_at=NOW, max_age_minutes=180)


def by_id(doc, prefix):
    return {st["id"]: st for st in doc["stations"] if st["id"].startswith(prefix)}


# ---------------------------------------------------------------------------
# schema e unita'
# ---------------------------------------------------------------------------
def test_schema_has_no_unavailable_fields(doc):
    """`visibility` e `pressure_msl` non devono esistere: la sorgente non li ha."""
    for st in doc["stations"]:
        assert "visibility" not in st
        assert "pressure_msl" not in st
    assert "pressure_msl" in doc["omitted_fields"]
    assert "visibility" in doc["omitted_fields"]


def test_pressure_is_in_the_source_but_not_published(payload):
    """Porto Garibaldi ha davvero B10004 nel payload: la pressione viene omessa."""
    porto = [
        e
        for e in payload["data"]
        if any(d.get("val") == "Porto Garibaldi" for d in e["stat"]["details"])
    ]
    assert len(porto) == 1
    assert any(p["var"] == "B10004" for p in porto[0]["prod"])


def test_temperature_is_celsius(doc):
    """282,33 K alle 06:30 -> 9,2 degC (la sorgente e' in kelvin)."""
    agrmet = by_id(doc, "agrmet:")["agrmet:44.78042,10.03873"]
    assert agrmet["temperature_2m"] == pytest.approx(9.2)
    assert agrmet["relative_humidity_2m"] == 87
    assert agrmet["obs_time"] == "2026-10-27T06:30:00Z"


def test_dew_point_matches_magnus(doc):
    agrmet = by_id(doc, "agrmet:")["agrmet:44.78042,10.03873"]
    t, rh = agrmet["temperature_2m"], agrmet["relative_humidity_2m"]
    gamma = math.log(rh / 100.0) + 17.625 * t / (243.04 + t)
    expected = 243.04 * gamma / (17.625 - gamma)
    assert agrmet["dew_point_2m"] == pytest.approx(round(expected, 1))
    assert agrmet["dew_point_2m"] == pytest.approx(7.1)


def test_wind_and_precipitation_period(doc):
    trentino = by_id(doc, "open-trentino:")["open-trentino:46.42259,10.69911"]
    assert trentino["wind_speed_10m"] == pytest.approx(1.3)
    assert trentino["wind_direction_10m"] == 170
    # il periodo di accumulo viaggia con il valore: 900 s in questa rete
    assert trentino["precipitation"] == pytest.approx(0.0)
    assert trentino["precipitation_period_s"] == 900


def test_station_without_humidity_has_no_dew_point(doc):
    nausicaa = by_id(doc, "boa:")["boa:44.21458,12.47587"]
    assert "temperature_2m" in nausicaa
    assert "relative_humidity_2m" not in nausicaa
    assert "dew_point_2m" not in nausicaa


# ---------------------------------------------------------------------------
# esclusioni dichiarate
# ---------------------------------------------------------------------------
def test_river_level_only_station_excluded(doc):
    """Foce Naviglio ha solo B13215 (livello idrografico): non e' meteo."""
    assert not [st for st in doc["stations"] if st["id"].startswith("simnpr:")]
    assert doc["counts"]["stations_excluded_no_data"] == 2  # simnpr + Cervia Porto


def test_unknown_network_excluded_and_recorded(doc):
    """`ofa-01-cmcc` non ha licenza/attribuzione verificate: fuori e dichiarata."""
    assert not [st for st in doc["stations"] if st["network"] == "ofa-01-cmcc"]
    assert doc["excluded_networks"] == {"ofa-01-cmcc": 1}
    assert doc["counts"]["stations_excluded_unknown_network"] == 1


def test_attribution_covers_every_published_network(doc):
    published = {st["network"] for st in doc["stations"]}
    declared = {a["network"] for a in doc["attribution"]}
    assert published == declared
    assert doc["licence"] == "CC BY 4.0"
    for item in doc["attribution"]:
        assert item["text"], item
        assert item["url"], item


# ---------------------------------------------------------------------------
# dedup, eta', identificatori
# ---------------------------------------------------------------------------
def test_duplicate_record_keeps_newest_value(doc, payload):
    """Stessa stazione due volte: vince il valore piu' recente, non il primo.

    Il record duplicato e' costruito dal record reale `agrmet` spostandone le
    osservazioni indietro di 90 minuti e cambiando la temperatura: serve a
    provare la regola di dedup, che il payload di oggi non esercita.
    """
    original = [e for e in payload["data"] if e["stat"]["net"] == "agrmet"][0]
    stale = json.loads(json.dumps(original))
    for prod in stale["prod"]:
        for value in prod["val"]:
            ref = fs.parse_ref(value["ref"]) - timedelta(minutes=90)
            value["ref"] = ref.strftime("%Y-%m-%dT%H:%M:%S")
        if prod["var"] == "B12101":
            for value in prod["val"]:
                value["val"] = 250.0
    duplicated = {"descr": payload["descr"], "data": [original, stale]}
    out = fs.normalize_payload(
        duplicated, NETWORKS, generated_at=NOW, max_age_minutes=180
    )
    assert out["counts"]["stations_with_duplicate_record"] == 1
    assert len(out["stations"]) == 1
    station = out["stations"][0]
    assert station["temperature_2m"] == pytest.approx(9.2)  # non 250 K -> -23,2
    assert station["obs_time"] == "2026-10-27T06:30:00Z"


def test_old_observations_are_dropped(payload):
    """Con riferimento +5 h tutte le osservazioni sono oltre le 3 h: zero stazioni.

    Non si tarocca il payload: si sposta l'orologio, che e' esattamente cio' che
    succede quando la rete resta muta.
    """
    later = NOW + timedelta(hours=5)
    out = fs.normalize_payload(payload, NETWORKS, generated_at=later, max_age_minutes=180)
    assert out["stations"] == []
    assert out["counts"]["values_dropped_too_old"] > 0


def test_near_duplicate_coordinates_get_distinct_ids():
    """Due stazioni a 1,1 m NON vanno fuse: sono stazioni diverse.

    Coordinate reali: `boa` "Cervia Porto" (44.26714, 12.35751) e
    "Cervia Porto radar" (44.26714, 12.35752). A 4 decimali l'id coincide e
    una delle due sparirebbe; a 5 decimali restano distinte.
    """
    real = [
        {"_net": "boa", "lat": 44.26714, "lon": 12.35751, "name": "Cervia Porto"},
        {"_net": "boa", "lat": 44.26714, "lon": 12.35752, "name": "Cervia Porto radar"},
    ]
    assert len({fs.station_id("boa", s["lat"], s["lon"], 4) for s in real}) == 1
    decimals, collisions4 = fs._assign_ids(real)
    assert (decimals, collisions4) == (5, 1)
    assert len({s["id"] for s in real}) == 2
    assert all("_" not in s for s in real)

    # coordinate IDENTICHE: nessun numero di decimali basta, serve il suffisso
    same = [
        {"_net": "boa", "lat": 44.2671, "lon": 12.3575, "name": "A"},
        {"_net": "boa", "lat": 44.2671, "lon": 12.3575, "name": "B"},
    ]
    decimals, _ = fs._assign_ids(same)
    assert decimals == 7
    assert len({s["id"] for s in same}) == 2
    assert same[1]["id"].endswith("#2")


def test_station_ids_are_stable_between_runs(payload):
    first = fs.normalize_payload(payload, NETWORKS, generated_at=NOW)
    second = fs.normalize_payload(payload, NETWORKS, generated_at=NOW)
    assert [s["id"] for s in first["stations"]] == [s["id"] for s in second["stations"]]


# ---------------------------------------------------------------------------
# serializzazione e guardie
# ---------------------------------------------------------------------------
def test_document_is_valid_utf8_lf_without_bom(doc):
    text = fs.dumps_document(doc)
    raw = text.encode("utf-8")
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert text.endswith("\n")
    assert json.loads(text)["counts"] == doc["counts"]


def test_one_station_per_line(doc):
    text = fs.dumps_document(doc)
    lines = [ln for ln in text.split("\n") if ln.startswith("    {")]
    assert len(lines) == len(doc["stations"])
    for line in lines:
        assert json.loads(line.strip().rstrip(","))


def test_validate_document_accepts_output_and_rejects_fake_fields(doc):
    assert fs.validate_document(doc, min_stations=1, max_bytes=1_500_000) == []
    broken = json.loads(json.dumps(doc))
    broken["stations"][0]["visibility"] = 10.0
    problems = fs.validate_document(broken, min_stations=1, max_bytes=1_500_000)
    assert any("visibility" in p for p in problems)


def test_validate_document_rejects_too_few_stations(doc):
    problems = fs.validate_document(doc, min_stations=999, max_bytes=1_500_000)
    assert any("minimo" in p for p in problems)


def test_build_query_contains_mandatory_license_clause():
    """Senza `license:` il servizio risponde 400: la clausola e' obbligatoria."""
    query = fs.build_query(
        datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc),
    )
    assert "license:CCBY_COMPLIANT" in query
    assert "reftime: >=2026-10-27 05:00,<=2026-10-27 06:00" in query


def test_trange_period_parsing():
    assert fs.trange_period_seconds("1,0,900") == 900
    assert fs.trange_period_seconds("1,0,60") == 60
    assert fs.trange_period_seconds("254,0,0") is None
    assert fs.trange_period_seconds("0,0,900") is None
    assert fs.trange_period_seconds("") is None

# ---------------------------------------------------------------------------
# indice compatto (index.json): schema, coerenza con latest.json, dimensione
# ---------------------------------------------------------------------------
REAL_LATEST = _HERE.parents[1] / "data" / "stations" / "latest.json"
INDEX_VALUE_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "precipitation_period_s",
)


@pytest.fixture(scope="module")
def index(doc):
    return fs.build_index(doc, NETWORKS)


def test_index_schema_is_declared_and_rows_are_compact(index, doc):
    assert index["schema"] == fs.INDEX_SCHEMA
    assert index["station_fields"] == list(fs.INDEX_STATION_FIELDS)
    assert index["count"] == len(doc["stations"]) == len(index["stations"])
    for row in index["stations"]:
        assert len(row) == len(fs.INDEX_STATION_FIELDS)
        # niente serie orarie: solo scalari (nessun oggetto/array annidato)
        assert all(not isinstance(v, (dict, list)) for v in row)
    assert set(index["bbox"]) == {"west", "south", "east", "north"}
    for st in doc["stations"]:
        assert index["bbox"]["west"] <= st["lon"] <= index["bbox"]["east"]
        assert index["bbox"]["south"] <= st["lat"] <= index["bbox"]["north"]


def test_index_has_the_same_stations_and_values_as_latest(index, doc):
    """Stesse stazioni, stesso ordine, stessi valori entro 0,05 (1 decimale)."""
    assert [row[0] for row in index["stations"]] == [
        st["id"] for st in doc["stations"]
    ]
    fields = index["station_fields"]
    for row, st in zip(index["stations"], doc["stations"]):
        record = dict(zip(fields, row))
        for key in ("network", "obs_time", "lat", "lon"):
            assert record[key] == st[key]
        for key in INDEX_VALUE_FIELDS:
            if key in st:
                assert record[key] is not None, (st["id"], key)
                assert abs(float(record[key]) - float(st[key])) <= 0.05, (
                    st["id"],
                    key,
                )
            else:
                assert record[key] is None, (st["id"], key)
    assert fs.validate_index(index, doc) == []


def test_index_attribution_and_network_counts(index, doc):
    assert index["licence"] == "CC BY 4.0"
    assert index["licence_url"].startswith("https://creativecommons.org/")
    assert len(index["attribution"]) == len(doc["attribution"])
    assert {a["network"] for a in index["attribution"]} == {
        st["network"] for st in doc["stations"]
    }
    for item in index["attribution"]:
        assert item["text"], item
        assert item["url"], item
    counts = {n["id"]: n["count"] for n in index["networks"]}
    assert sum(counts.values()) == index["count"]
    for net, count in counts.items():
        assert count == sum(1 for st in doc["stations"] if st["network"] == net)


def test_index_serialization_is_lf_no_bom_one_row_per_station(index):
    text = fs.dumps_index(index)
    raw = text.encode("utf-8")
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert text.endswith("\n")
    rows = [ln for ln in text.split("\n") if ln.startswith("    [")]
    assert len(rows) == index["count"]
    assert json.loads(text)["count"] == index["count"]


def test_index_validation_catches_drift(index, doc):
    """Il validatore non e' di facciata: ogni deriva deve produrre un problema."""
    broken = json.loads(json.dumps(index))
    position = next(
        i for i, row in enumerate(index["stations"]) if row[6] is not None
    )
    broken["stations"][position][6] = round(float(broken["stations"][position][6]) + 0.2, 1)
    assert any(
        "temperature_2m" in p for p in fs.validate_index(broken, doc)
    ), fs.validate_index(broken, doc)

    no_attr = json.loads(json.dumps(index))
    no_attr["attribution"] = []
    assert any("attribuzione" in p for p in fs.validate_index(no_attr, doc))

    dropped = json.loads(json.dumps(index))
    dropped["stations"].pop()
    assert any(
        "count" in p or "latest" in p for p in fs.validate_index(dropped, doc)
    )


def test_index_can_drop_names_without_losing_stations(doc):
    """Degradazione dichiarata: si perde il nome, non la stazione ne' il valore."""
    index = fs.build_index(doc, NETWORKS, include_names=False)
    name_position = fs.INDEX_STATION_FIELDS.index("name")
    assert all(row[name_position] is None for row in index["stations"])
    assert index["count"] == len(doc["stations"])
    assert fs.validate_index(index, doc) == []


@pytest.mark.skipif(
    not REAL_LATEST.exists(),
    reason="data/stations/latest.json assente in questa copia (i dati vivono su main)",
)
def test_index_budget_on_real_data_and_ten_station_spot_check():
    """Sul file REALE: budget < 120 KB gzip e 10 stazioni a caso coerenti.

    Il seed e' fisso: il campione e' riproducibile. `latest.json` sul repo ha
    4.137 stazioni; nelle copie di release senza `data/` il test si salta e lo
    dichiara, invece di passare su una fixture che non misura niente.
    """
    latest = json.loads(REAL_LATEST.read_text(encoding="utf-8"))
    index = fs.build_index(latest, NETWORKS)
    assert fs.validate_index(index, latest) == []
    raw_size = len(fs.dumps_index(index).encode("utf-8"))
    gzip_size = fs.index_gzip_bytes(index)
    latest_size = REAL_LATEST.stat().st_size
    print(
        f"[index reale] latest={latest_size} B -> index={raw_size} B "
        f"({raw_size / 1024:.0f} KiB) gzip={gzip_size} B ({gzip_size / 1024:.0f} KiB)"
    )
    assert gzip_size <= fs.INDEX_MAX_GZIP_BYTES
    assert gzip_size < 120_000
    assert raw_size < latest_size  # piu' piccolo anche NON compresso

    rng = random.Random(20261027)
    fields = index["station_fields"]
    positions = sorted(rng.sample(range(len(index["stations"])), 10))
    for position in positions:
        record = dict(zip(fields, index["stations"][position]))
        st = latest["stations"][position]
        assert record["id"] == st["id"]
        for key in INDEX_VALUE_FIELDS:
            if key in st:
                assert abs(float(record[key]) - float(st[key])) <= 0.05, (
                    st["id"],
                    key,
                    record[key],
                    st[key],
                )
            else:
                assert record[key] is None, (st["id"], key)