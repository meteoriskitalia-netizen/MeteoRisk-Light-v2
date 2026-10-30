# -*- coding: utf-8 -*-
"""Climatologia 1991-2020 per le mappe di anomalia.

Controlla il file committato `data/climate/normals_1991_2020.json`:
schema, forma (360 celle x 12 mesi), unita' e precisione dichiarate,
budget di dimensione (400 KB gzip), assenza di buchi e plausibilita'
fisica dei valori. Non fa rete: legge il file e basta.

Controlla anche la logica di aggregazione del builder su una serie
sintetica (nessuna rete), perche' e' li' che si puo' sbagliare in silenzio:
un mese da 31 giorni non deve pesare piu' di uno da 30.
"""
import gzip
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NORMALS = os.path.join(ROOT, "data", "climate", "normals_1991_2020.json")
CLIMATE_DIR = os.path.join(ROOT, "scripts", "climate")

BUDGET_GZIP = 400 * 1024
FIELDS = ("temperature_2m", "temperature_850hPa",
          "geopotential_height_500hPa", "pressure_msl")

# intervalli fisici ampi: servono a intercettare un campo scalato male o
# unita' sbagliate, non a validare il clima.
PHYSICAL = {
    "temperature_2m": (-60.0, 50.0),
    "temperature_850hPa": (-70.0, 45.0),
    "geopotential_height_500hPa": (4700.0, 6100.0),
    "pressure_msl": (960.0, 1060.0),
}


def _load():
    assert os.path.exists(NORMALS), "manca %s" % NORMALS
    with open(NORMALS, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _index(lat, lon, grid):
    i_lat = int(round((lat - grid["lat0"]) / grid["step"]))
    i_lon = int(round((lon - grid["lon0"]) / grid["step"]))
    return i_lat * grid["n_lon"] + i_lon


def test_schema_and_grid():
    doc = _load()
    assert doc["schema"] == "meteorisk.climate.normals/1"
    assert doc["period"] == "1991-2020"
    assert doc["monthly"] is True
    assert doc["months"] == 12
    grid = doc["grid"]
    assert grid["lat0"] == 20.0
    assert grid["lat1"] == 90.0
    assert grid["lon0"] == -70.0
    assert grid["lon1"] == 45.0
    assert grid["step"] == 5.0
    assert grid["n_lat"] == 15
    assert grid["n_lon"] == 24
    assert grid["n_cells"] == 360
    assert grid["n_lat"] * grid["n_lon"] == grid["n_cells"]


def test_fields_shape_units_decimals():
    doc = _load()
    assert set(doc["fields"]) == set(FIELDS)
    units = {
        "temperature_2m": "degC",
        "temperature_850hPa": "degC",
        "geopotential_height_500hPa": "m",
        "pressure_msl": "hPa",
    }
    for name in FIELDS:
        field = doc["fields"][name]
        assert field["units"] == units[name], name
        assert isinstance(field["decimals"], int) and field["decimals"] >= 0
        assert len(field["values"]) == 360, name
        for i, row in enumerate(field["values"]):
            assert len(row) == 12, "%s cella %d: %d mesi" % (name, i, len(row))


def test_no_holes():
    doc = _load()
    for name in FIELDS:
        field = doc["fields"][name]
        holes = sum(1 for row in field["values"] for v in row if v is None)
        assert holes == 0, "%s: %d valori nulli" % (name, holes)
        assert field["n_missing"] == 0
        assert doc["n_missing"][name] == 0
    assert len(doc["n_missing_by_cell"]) == 360
    assert sum(doc["n_missing_by_cell"]) == 0
    assert doc["cells_with_missing"] == []


def test_values_are_physical_and_rounded():
    doc = _load()
    for name in FIELDS:
        lo, hi = PHYSICAL[name]
        dec = doc["fields"][name]["decimals"]
        for i, row in enumerate(doc["fields"][name]["values"]):
            for j, value in enumerate(row):
                assert lo <= value <= hi, (
                    "%s cella %d mese %d fuori intervallo: %r"
                    % (name, i, j + 1, value))
                assert abs(round(value, dec) - value) < 1e-9


def test_physical_structure_of_the_seasonal_cycle():
    """A 45N 10E: estate piu' calda e Z500 piu' alto, inverno piu' basso."""
    doc = _load()
    grid = doc["grid"]
    cell = _index(45, 10, grid)
    t2m = doc["fields"]["temperature_2m"]["values"][cell]
    z500 = doc["fields"]["geopotential_height_500hPa"]["values"][cell]
    msl = doc["fields"]["pressure_msl"]["values"][cell]
    assert t2m[0] < t2m[6] - 10.0, "gennaio vs luglio T2m a 45N/10E: %r" % (t2m,)
    assert z500[0] < z500[6] - 100.0, "gennaio vs luglio Z500: %r" % (z500,)
    assert msl[0] > msl[6], "pressione invernale non sopra la estiva: %r" % (msl,)
    # la temperatura deve scendere verso nord in OGNI mese, ma solo in media
    # zonale: il punto singolo a 70N/10E e' mare di Norvegia (3 C a gennaio)
    # mentre 45N/10E e' un nodo alpino a ~770 m (1.9 C): il confronto
    # punto-a-punto non e' una proprieta' fisica, quello zonale si'.
    zonal = {}
    for lat in (30, 45, 70, 80):
        i = int(round((lat - grid["lat0"]) / grid["step"]))
        rows = doc["fields"]["temperature_2m"]["values"][
            i * grid["n_lon"]:(i + 1) * grid["n_lon"]]
        zonal[lat] = [sum(row[m] for row in rows) / len(rows) for m in range(12)]
    for month in range(12):
        assert zonal[70][month] < zonal[45][month] - 5.0, (
            "mese %d: media zonale 70N non piu' fredda di 45N" % (month + 1))
        assert zonal[80][month] < zonal[30][month] - 15.0, (
            "mese %d: media zonale 80N non piu' fredda di 30N" % (month + 1))
    # gradiente annuale monotono scendendo verso nord
    annual = {lat: sum(zonal[lat]) / 12 for lat in zonal}
    assert annual[30] > annual[45] > annual[70] > annual[80]


def test_850hpa_is_a_free_atmosphere_field():
    """T850 e' sotto T2m in ogni mese (media di griglia), ma non cella per cella.

    Perche' conta: le mappe di anomalia di temperatura si fanno a 850 hPa per
    non ereditare l'orografia del modello che ha prodotto la climatologia. Il
    campo a 850 hPa deve quindi essere un campo VERO a quel livello, non una
    copia del 2 m: la media di griglia sale sopra T2m se i due campi coincidono.

    Cella per cella la disuguaglianza NON vale, ed e' misurato: in 22-111 celle
    (a seconda del mese) T850 e' piu' caldo di T2m fino a +12,9 K, perche' su
    Groenlandia e rilievi il nodo a 2 m sta a migliaia di metri mentre gli
    850 hPa sono sopra. E' esattamente il difetto che l'anomalia a 2 m
    erediterebbe, e che il livello 850 hPa evita.
    """
    doc = _load()
    t2 = doc["fields"]["temperature_2m"]["values"]
    t8 = doc["fields"]["temperature_850hPa"]["values"]
    n = len(t2)
    for month in range(12):
        mean2 = sum(row[month] for row in t2) / n
        mean8 = sum(row[month] for row in t8) / n
        assert mean8 < mean2, (
            "mese %d: media di griglia T850 (%.2f) non sotto T2m (%.2f)"
            % (month + 1, mean8, mean2))
        assert mean2 - mean8 < 25.0, (
            "mese %d: T2m - T850 = %.2f K, troppo per 850 hPa"
            % (month + 1, mean2 - mean8))
        diffs = [row8[month] - row2[month] for row2, row8 in zip(t2, t8)]
        assert min(diffs) > -15.0 and max(diffs) < 15.0, (
            "mese %d: dT fuori da +-15 K (min %.2f, max %.2f)"
            % (month + 1, min(diffs), max(diffs)))


def test_gzip_budget():
    with open(NORMALS, "rb") as fh:
        raw = fh.read()
    packed = gzip.compress(raw, 9)
    assert len(packed) <= BUDGET_GZIP, (
        "gzip %d byte > budget %d" % (len(packed), BUDGET_GZIP))


def test_file_is_lf_without_bom():
    with open(NORMALS, "rb") as fh:
        raw = fh.read()
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM in testa al file"
    assert b"\r" not in raw, "trovati CR: il file deve essere LF"


def test_sources_declared():
    doc = _load()
    sources = doc["sources"]
    for name in FIELDS:
        assert name in sources, "fonte non dichiarata per %s" % name
        assert sources[name].get("provider")
        assert sources[name].get("endpoint")
    # la fonte effettivamente usata e' NCEP/NCAR R1 per tutti e tre i campi
    for name in FIELDS:
        assert "NCEP" in sources[name]["provider"], name
    # il ramo ERA5 previsto dalla specifica deve restare dichiarato, con il
    # motivo per cui non e' stato usato
    assert "open_meteo_era5" in sources
    era5 = sources["open_meteo_era5"]
    assert "Open-Meteo" in era5["provider"]
    assert "429" in era5["note"] or "quota" in era5["note"]
    assert doc["licence"] and doc["attribution"]
    assert doc["caveats"], "le cautele vanno dichiarate, non omesse"


def _builder():
    if CLIMATE_DIR not in sys.path:
        sys.path.insert(0, CLIMATE_DIR)
    import build_climatology
    return build_climatology


def test_monthly_aggregation_weights_each_year_equally():
    clim = _builder()
    times = []
    values = []
    # gennaio 1991: 31 giorni a 0 C; gennaio 1992: 31 giorni a 10 C
    for day in range(1, 32):
        times.append("1991-01-%02d" % day)
        values.append(0.0)
        times.append("1992-01-%02d" % day)
        values.append(10.0)
    # febbraio 1991: 28 giorni a 4 C (mese corto)
    for day in range(1, 29):
        times.append("1991-02-%02d" % day)
        values.append(4.0)
    series, missing = clim.monthly_climatology(times, values)
    assert missing == 0
    assert series[0] == pytest.approx(5.0), "gennaio: media dei due anni"
    assert series[1] == pytest.approx(4.0), "febbraio 1991"
    assert series[2] is None, "marzo senza dati deve essere null, non 0"
    assert len(series) == 12


def test_monthly_aggregation_counts_nulls_without_shifting_them():
    clim = _builder()
    times = ["1991-03-%02d" % d for d in range(1, 32)]
    values = [3.0] * 31
    values[4] = None
    series, missing = clim.monthly_climatology(times, values)
    assert missing == 1
    assert series[2] == pytest.approx(3.0), "il null non deve entrare nella media"


def test_grid_cells_cover_the_declared_box():
    clim = _builder()
    cells = clim.grid_cells()
    assert len(cells) == 360
    assert cells[0] == (20.0, -70.0)
    assert cells[-1] == (90.0, 45.0)
    assert len(set(cells)) == 360
    lats = {c[0] for c in cells}
    lons = {c[1] for c in cells}
    assert min(lats) == 20.0 and max(lats) == 90.0 and len(lats) == 15
    assert min(lons) == -70.0 and max(lons) == 45.0 and len(lons) == 24
