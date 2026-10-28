#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 7 (build_meteorisk_dataset.py): METEO-RISK DATA ENGINE.
Trasforma i dati RAW della fonte (Open-Meteo) nel DATASET DERIVATO MeteoRisk:
non copie dell'API ma aggregazioni e indicatori calcolati dalla pipeline.

Contenuto derivato (per punto reale):
  - collasso giornaliero "day-wide" (port di getHourOrDailyData(...,'all'))
  - riepilogo derivato giornaliero (temp max/min, precip, prob, vento, raffica,
    giorni/ore di temporale-grandine-neve, intensità max pioggia, CAPE, etc.)
  - le serie orarie/giornaliere sono i VALORI INTERMEDI STRETTAMENTE NECESSARI
    al motore di rischio MeteoRisk client-side (densificazione IDW, vista oraria,
    indici severi, merge dual): array numerici spogliati di ogni envelope API
    (unità, unit, time, api_response_mask, timezone).

Per provincia:
  - worst-point convettivo (port di scorePointForProvince) con stesso tie-break
    dell'app (primo massimo in ordine di slot, iterazione in indice crescente)
  - riepilogo derivato per giorno del punto peggiore.

Output (in data/_staging, poi validate_dataset.py e publish_dataset.py):
  data/latest/metadata.json, meteorisk-points.json, meteorisk-provinces.json,
  meteorisk-points-compact.json (aggregato per provincia, una sola richiesta
  HTTP dal client: vedi COMPACT_* qui sotto)
"""

import argparse
import datetime as _dt
import json
import math
import os
import sys

sys.path.insert(0, __file__ and __file__[: __file__.rfind("\\")] or ".")
import common

ATTR = ("Meteorological source data: Open-Meteo.com / Data license: CC BY 4.0 / "
        "Processed and aggregated by MeteoRisk Light.")

# ---------------------------------------------------------------------------
# DATASET COMPATTO PER PROVINCIA (data/latest/meteorisk-points-compact.json)
# ---------------------------------------------------------------------------
# PERCHE' ESISTE: un refresh completo del client interroga api.open-meteo.com
# UNA volta per punto di campionamento (257 punti reali, piu' i virtuali), e con
# i limiti free (5.000 richieste/ora, 600/minuto) regge ~19 refresh/ora ->
# HTTP 429 "Hourly API request limit exceeded". Questo file fa la stessa
# aggregazione UNA VOLTA nella pipeline: un record per provincia (107), serie
# orarie in array paralleli, 333 KB gzip MISURATI sul dataset reale del
# 2026-10-28 (raw 1,44 MB contro 8,9 MB del completo), quindi una sola richiesta.
#
# CONTRATTO (letto dal client e da scripts/tests/test_compact_dataset.py):
#   - province_fields = ["sigla","lat","lon","n_points"] e provinces = array di
#     record compatti (stesso stile di data/stations/index.json);
#   - fields = elenco dei campi orari presenti; series[campo][provincia][ora];
#   - time = etichette ISO locali (Europe/Rome) condivise da tutte le serie;
#   - null = nessun punto della provincia ha quel valore a quell'ora.
#
# AGGREGAZIONE: media aritmetica dei punti della provincia (sui soli valori non
# nulli). Due eccezioni DICHIARATE:
#   - `weathercode` non e' una grandezza ordinale (la media di 1 e 95 non e' un
#     codice meteo): si prende il codice PIU' SEVERO fra i punti dell'ora,
#     coerente con la semantica "peggior condizione" gia' usata dal client nella
#     vista giornaliera;
#   - le direzioni del vento usano la media VETTORIALE (u/v): la media
#     aritmetica di 350 e 10 gradi darebbe 180, cioe' il vento opposto.
#
# PRECISIONE (dichiarata nel campo `rounding` del file):
#   - 1 decimale: temperature, dew point, velocita' del vento, pressione,
#     lifted index;
#   - interi: umidita', probabilita', codici meteo, CAPE, CIN, K index,
#     freezing level, geopotenziali, direzioni del vento;
#   - 2 decimali: precipitazione e rovesci (0,01 mm/h non si perde);
#   - direzioni del vento arrotondate a 5 gradi: una media spaziale ha senso a
#     5 gradi e i 3 digit pieni costavano ~35 KB gzip (misurato).
#
# ESCLUSO `visibility`: nessuna funzione del client legge `hourly.visibility`
# (nel client la parola compare solo in CSS e nelle osservazioni METAR), e il
# campo e' popolato solo da best_match (72/72) e nullo sugli altri modelli.
COMPACT_SCHEMA = "meteorisk-points-compact/1"
COMPACT_PROVINCE_FIELDS = ["sigla", "lat", "lon", "n_points"]
COMPACT_WORST_FIELDS = frozenset(("weathercode",))
_COMPACT_LEVELS = (1000, 975, 950, 925, 900, 850, 800, 700, 600, 500)
# ORIZZONTE E CAMPI PUBBLICATI NEL COMPATTO (scelta MISURATA, non stimata).
# Il budget e' 400 KB gzip con 4 modelli x 107 province. Misure del 2026-10-28
# sul dataset reale (257 punti, valori veri) con i 2 modelli nuovi ricostruiti
# come perturbazione ad alta entropia (l'API era in 429: sigma per campo, non
# uno shift costante, che sarebbe troppo comprimibile):
#   48 campi @72h -> 845 KB gz9   (fuori budget)
#   48 campi @48h -> 581 KB       (fuori)
#   27 campi @48h -> 401 KB gz9 / 416 KB gz6  (fuori)
#   24 campi @48h -> 372 KB gz9 / 386 KB gz6  (Pages serve ~+8-10% -> ~405 KB: fuori)
#   22 campi @48h -> 350 KB gz9 / 362 KB gz6  <- SCELTO (Pages atteso ~385 KB)
#   20 campi @48h -> 329 KB gz9
# Nota: GitHub Pages ricomprime (misurato sul file a 1 modello: gz9 341.398 ->
# servito 378.086, +10,7%), quindi il budget va valutato sul gz9 x ~1,1.
# Quindi: orizzonte 48 h (2 giorni) e 22 campi. Tagliati, dichiarati:
#   - 7 livelli di pressione extra (1000/975/950/925/900/800/600 hPa): il
#     hodograph resta su 850/700/500, shear/SRH/SCP calcolabili ma piu' grossolani;
#   - windspeed_100m/winddirection_100m (il rischio vento usa i 10 m);
#   - temperature_850hPa, relative_humidity_850/700hPa, dew_point_850/700hPa:
#     input del solo badge downburst (resta spento sul compatto);
#   - convective_inhibition/lifted_index/k_index e temperature_700/500hPa:
#     gate e input del badge GRANDINE, che sul compatto resta spento (il dataset
#     completo li ha). Con 2 campi in piu' il file servito superava i 400 KB.
# Restano gli input di: mappa dei rischi, badge di rischio (temperatura, caldo/
# freddo, vento, pioggia, temporale), indici severi (core 850/700/500 + quote
# geometriche per AGL/Bunkers/SRH), neve (codice+temperature), nebbia esclusa
# (visibility non e' nel dataset). Il dataset COMPLETO resta a 72 h e 49 campi.
COMPACT_MAX_HOURS = 48
COMPACT_FIELD_NAMES = (
    # superficie: colori mappa + badge di rischio
    "temperature_2m", "relativehumidity_2m", "dew_point_2m", "pressure_msl",
    "cape", "precipitation_probability", "windspeed_10m", "winddirection_10m",
    "windgusts_10m", "weathercode", "precipitation", "showers",
    "freezing_level_height",
    # hodograph ridotto: i 3 livelli CORE che il client pretende
    "wind_speed_850hPa", "wind_direction_850hPa",
    "wind_speed_700hPa", "wind_direction_700hPa",
    "wind_speed_500hPa", "wind_direction_500hPa",
    # quote geometriche (AGL/Bunkers/SRH)
    "geopotential_height_850hPa", "geopotential_height_700hPa",
    "geopotential_height_500hPa",
)
# (campo, decimali, passo di arrotondamento o None)
COMPACT_HOURLY_FIELDS = (
    ("temperature_2m", 1, None),
    ("relativehumidity_2m", 0, None),
    ("dew_point_2m", 1, None),
    ("pressure_msl", 1, None),
    ("cape", 0, None),
    ("precipitation_probability", 0, None),
    ("windspeed_10m", 1, None),
    ("winddirection_10m", 0, 5),
    ("windspeed_100m", 1, None),
    ("winddirection_100m", 0, 5),
    ("windgusts_10m", 1, None),
    ("weathercode", 0, None),
    ("precipitation", 2, None),
    ("showers", 2, None),
    ("freezing_level_height", 0, None),
    ("convective_inhibition", 0, None),
    ("lifted_index", 1, None),
    ("k_index", 0, None),
    # Livelli di pressione: il client li legge TUTTI (hodograph 10 livelli in
    # computeSevereIndices, gate grandine, vorticosi/downburst). Toglierli
    # azzererebbe shear/SRH/SCP e i badge severi.
    ) + tuple(("wind_speed_%dhPa" % lv, 1, None) for lv in _COMPACT_LEVELS) \
      + tuple(("wind_direction_%dhPa" % lv, 0, 5) for lv in _COMPACT_LEVELS) \
      + tuple(("temperature_%dhPa" % lv, 1, None) for lv in (850, 700, 500)) \
      + tuple(("relative_humidity_%dhPa" % lv, 0, None) for lv in (850, 700)) \
      + tuple(("dew_point_%dhPa" % lv, 1, None) for lv in (850, 700)) \
      + tuple(("geopotential_height_%dhPa" % lv, 0, None) for lv in (850, 700, 500))
# Campi giornalieri: la vista "giorno intero" del client legge daily.temp max/min,
# precipitation_sum e precipitation_probability_max (computeAggregateForStore).
COMPACT_DAILY_FIELDS = (
    ("temperature_2m_max", 1, None),
    ("temperature_2m_min", 1, None),
    ("precipitation_sum", 2, None),
    ("windspeed_10m_max", 1, None),
    ("precipitation_probability_max", 0, None),
    ("weathercode", 0, None),
)
COMPACT_UNITS = {
    "temperature_2m": "C", "relativehumidity_2m": "%", "dew_point_2m": "C",
    "pressure_msl": "hPa", "cape": "J/kg", "precipitation_probability": "%",
    "windspeed_10m": "km/h", "winddirection_10m": "deg", "windspeed_100m": "km/h",
    "winddirection_100m": "deg", "windgusts_10m": "km/h", "weathercode": "wmo",
    "precipitation": "mm", "showers": "mm", "freezing_level_height": "m",
    "convective_inhibition": "J/kg", "lifted_index": "K", "k_index": "K",
    "temperature_2m_max": "C", "temperature_2m_min": "C", "precipitation_sum": "mm",
    "windspeed_10m_max": "km/h", "precipitation_probability_max": "%",
}
for _lv in _COMPACT_LEVELS:
    COMPACT_UNITS["wind_speed_%dhPa" % _lv] = "km/h"
    COMPACT_UNITS["wind_direction_%dhPa" % _lv] = "deg"
for _lv in (850, 700, 500):
    COMPACT_UNITS["temperature_%dhPa" % _lv] = "C"
    COMPACT_UNITS["geopotential_height_%dhPa" % _lv] = "m"
for _lv in (850, 700):
    COMPACT_UNITS["relative_humidity_%dhPa" % _lv] = "%"
    COMPACT_UNITS["dew_point_%dhPa" % _lv] = "C"
del _lv


def dedupe_models(record):
    """record: {best_match: {daily,hourly}, ecmwf_ifs: {...}, gfs_seamless: {...},
    italia_meteo_arpae_icon_2i: {...}, elevation}."""
    out = {}
    for m in common.SOURCE_MODELS:
        sub = record.get(m)
        if sub and sub.get("daily") and sub.get("hourly"):
            out[m] = {"daily": sub["daily"], "hourly": sub["hourly"]}
    return out


def day_summary(hourly, daily, day_index, day_label):
    """Derived daily summary from the day-wide collapse + daily arrays."""
    c = common.collapse_day(hourly, day_index)
    daily_arr = {k: (daily.get(k) or []) for k in common.DAILY_FIELDS}
    codes = hourly.get("weathercode") or []
    prec = hourly.get("precipitation") or []
    s = day_index * 24
    thunder = hail = snow = rain_int = 0
    for i in range(24):
        wc = codes[s + i] if (s + i) < len(codes) else None
        if wc is None:
            continue
        wc = int(wc)
        if wc >= 95:
            thunder += 1
        if wc in (96, 99):
            hail += 1
        if 71 <= wc <= 77:
            snow += 1
        pv = prec[s + i] if (s + i) < len(prec) else 0
        if pv is not None and pv > rain_int:
            rain_int = pv
    return {
        "day": day_label,
        "temp_max": common.r2(_at(daily_arr["temperature_2m_max"], day_index)),
        "temp_min": common.r2(_at(daily_arr["temperature_2m_min"], day_index)),
        "precip_sum": common.r2(_at(daily_arr["precipitation_sum"], day_index)),
        "prob_max": common.r2(_at(daily_arr["precipitation_probability_max"], day_index)),
        "wind_max": common.r2(_at(daily_arr["windspeed_10m_max"], day_index)),
        "weathercode": common.r2(c["code"]),
        "humidity": common.r2(c["humidity"]),
        "pressure": common.r2(c["pressure"]),
        "precip_hourly": common.r2(c["precip"]),
        "showers": common.r2(c["showers"]),
        "gusts": common.r2(c["gusts"]),
        "wind_avg": common.r2(c["wind"]),
        "wind100_max": common.r2(c["wind100"]),
        "cape_max": common.r2(c["cape"]),
        "freezing_max": common.r2(c["freezingLevel"]),
        "rain_intensity_max": common.r2(rain_int),
        "thunder_hours": thunder,
        "hail_hours": hail,
        "snow_hours": snow,
    }


def _at(arr, i):
    return arr[i] if i < len(arr) else None


def _is_wind_direction(field):
    """True per ENTRAMBE le forme del dataset: `winddirection_10m` (superficie,
    senza underscore dopo "wind") e `wind_direction_500hPa` (livelli)."""
    return field.startswith("wind_direction") or field.startswith("winddirection")


def compact_value(values, field, decimals, step=None):
    """Valore aggregato di una grandezza per una provincia a un'ora.

    - media aritmetica dei soli valori numerici (None se non ce ne sono);
    - direzioni del vento: media VETTORIALE (u/v), non aritmetica. La media
      aritmetica di 350 e 10 gradi darebbe 180, cioe' il vento opposto; il
      vettore medio da' 0. Se i versori si annullano (venti opposti) si usa la
      media aritmetica e il caso resta dichiarato qui;
    - `weathercode`: codice PIU' SEVERO (non e' una grandezza ordinale);
    - `step`: arrotondamento a multiplo (direzioni a 5 gradi, con
      riavvolgimento a 0-359);
    - `decimals == 0` -> int; altrimenti il float intero diventa int (niente
      ".0": -3 byte per valore, misurato).
    """
    nums = [v for v in values
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return None
    if field in COMPACT_WORST_FIELDS:
        out = float(max(nums))
    elif _is_wind_direction(field):
        sin_sum = sum(math.sin(math.radians(float(v))) for v in nums)
        cos_sum = sum(math.cos(math.radians(float(v))) for v in nums)
        if abs(sin_sum) < 1e-12 and abs(cos_sum) < 1e-12:
            out = float(sum(nums)) / float(len(nums))
        else:
            out = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
    else:
        out = float(sum(nums)) / float(len(nums))
    if step:
        out = round(out / float(step)) * step
        if _is_wind_direction(field):
            out = out % 360
    if decimals == 0:
        return int(round(out))
    out = round(out, decimals)
    if out == int(out):
        return int(out)
    return out


def _series_length(points, model, fields):
    """Lunghezza comune delle serie (minimo fra le serie non vuote presenti)."""
    lens = []
    for p in points:
        hourly = ((p.get("models") or {}).get(model) or {}).get("hourly") or {}
        for field, _dec, _step in fields:
            arr = hourly.get(field)
            if arr:
                lens.append(len(arr))
    return min(lens) if lens else 0


def build_compact_dataset(points, regions, generated_at, day0, time_base=None,
                          model="best_match", models=None, hours=None, fields=None):
    """Aggrega i punti per provincia nel file compatto (una richiesta HTTP).

    `points` sono gli stessi record scritti in meteorisk-points.json: il
    compatto e' quindi coerente per costruzione con la fonte completa, che
    resta invariata. `regions` e' data/geography/regions_data.json (ordine e
    indici delle province). Ritorna il dict pronto da serializzare.

    COPERTURA: tutti i modelli di `common.SOURCE_MODELS` (default) presenti nei
    punti. Il PRIMO (`best_match`) vive nelle chiavi storiche `series`/`daily`;
    gli altri in `model_series[modello]`/`model_daily[modello]`, cosi' un
    lettore del formato a un solo modello continua a funzionare. `model` resta
    il modello principale (compatibilita' della chiave singola).

    LIMITI DI DIMENSIONE: `hours` taglia l'orizzonte (48 = due giorni) e
    `fields` sceglie il sottoinsieme di COMPACT_HOURLY_FIELDS. Servono al
    budget di 400 KB gzip con 4 modelli: vedi COMPACT_HOURS/COMPACT_FIELD_NAMES.
    """
    wanted_models = list(models or common.SOURCE_MODELS)
    present_models = [m for m in wanted_models
                      if any((((p.get("models") or {}).get(m) or {}).get("hourly"))
                             for p in points)]
    primary = model if model in present_models else (present_models[0] if present_models else model)

    allowed = set(fields) if fields else set(COMPACT_FIELD_NAMES)
    schema_hourly = [(f, d, s) for f, d, s in COMPACT_HOURLY_FIELDS
                     if allowed is None or f in allowed]
    present_daily = [
        (field, dec, step) for field, dec, step in COMPACT_DAILY_FIELDS
        if any((((p.get("models") or {}).get(primary) or {}).get("daily") or {}).get(field)
               for p in points)
    ]
    n_hours = _series_length(points, primary, schema_hourly)
    hours = COMPACT_MAX_HOURS if hours is None else hours
    if hours:
        n_hours = min(n_hours, int(hours))
    n_days = 0
    if present_daily and points:
        n_days = min(len((((p.get("models") or {}).get(primary) or {}).get("daily") or {}).get(f)
                         or []) for p in points for f, _d, _s in present_daily
                     if (((p.get("models") or {}).get(primary) or {}).get("daily") or {}).get(f))

    by_prov = {}
    for p in points:
        by_prov.setdefault(p.get("provinceIdx"), []).append(p)

    def hourly_series(group, model_id, fields_spec):
        out = {}
        for field, dec, step in fields_spec:
            row = []
            for h in range(n_hours):
                vals = []
                for p in group:
                    arr = (((p.get("models") or {}).get(model_id) or {}).get("hourly") or {}).get(field) or []
                    if h < len(arr):
                        vals.append(arr[h])
                row.append(compact_value(vals, field, dec, step))
            out[field] = row
        return out

    def daily_series_fn(group, model_id):
        out = {}
        for field, dec, step in present_daily:
            row = []
            for d in range(n_days):
                vals = []
                for p in group:
                    arr = (((p.get("models") or {}).get(model_id) or {}).get("daily") or {}).get(field) or []
                    if d < len(arr):
                        vals.append(arr[d])
                row.append(compact_value(vals, field, dec, step))
            out[field] = row
        return out

    provinces = []
    series = {field: [] for field, _d, _s in schema_hourly}
    daily_series = {field: [] for field, _d, _s in present_daily}
    other_series = {m: {field: [] for field, _d, _s in schema_hourly}
                    for m in present_models if m != primary}
    other_daily = {m: {field: [] for field, _d, _s in present_daily}
                   for m in present_models if m != primary}
    for region in regions:
        idx = region.get("idx")
        group = by_prov.get(idx) or []
        provinces.append([
            region.get("sigla"),
            common.r2(region.get("lat"), 5),
            common.r2(region.get("lon"), 5),
            len(group),
        ])
        prim = hourly_series(group, primary, schema_hourly)
        for field in series:
            series[field].append(prim[field])
        pri_daily = daily_series_fn(group, primary)
        for field in daily_series:
            daily_series[field].append(pri_daily[field])
        for m in other_series:
            again = hourly_series(group, m, schema_hourly)
            for field in again:
                other_series[m][field].append(again[field])
            again_d = daily_series_fn(group, m)
            for field in again_d:
                other_daily[m][field].append(again_d[field])

    base = time_base or (day0.isoformat() + "T00:00")
    try:
        day_start = _dt.date.fromisoformat(str(base)[:10])
    except ValueError:
        day_start = day0
    time = [(day_start + _dt.timedelta(days=h // 24)).isoformat() + ("T%02d:00" % (h % 24))
            for h in range(n_hours)]

    fields_out = [field for field, _d, _s in schema_hourly]
    daily_fields = [field for field, _d, _s in present_daily]
    return {
        "schema": COMPACT_SCHEMA,
        "status": "live" if points else "empty",
        "generated_at": generated_at,
        "model": primary,
        "models_covered": list(common.MODEL_MENU),
        "models_derived": list(common.MODELS_DERIVED),
        "licence": common.SOURCE_LICENSE,
        "licence_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": ATTR,
        "source": common.SOURCE_NAME,
        "timezone": common.TIMEZONE,
        "forecast_days": common.FORECAST_DAYS,
        "hours": n_hours,
        "days": n_days,
        "count": len(provinces),
        "aggregation": "mean per province (vector mean for wind_direction); weathercode = worst WMO code",
        "rounding": {"decimals_default": 1, "wind_direction_step_deg": 5,
                     "integers": "humidity, probability, codes, cape, cin, k_index,"
                                 " freezing_level, geopotential, wind direction"},
        "time": time,
        "fields": fields_out,
        "units": {f: COMPACT_UNITS.get(f) for f in fields_out},
        "province_fields": list(COMPACT_PROVINCE_FIELDS),
        "provinces": provinces,
        "series": series,
        "model_series": other_series,
        "daily_fields": daily_fields,
        "daily": daily_series,
        "model_daily": other_daily,
        "note": ("Aggregato per provincia dei punti di campionamento (media; "
                 "weathercode = codice peggiore). `series`/`daily` = best_match; "
                 "`model_series`/`model_daily` = gli altri modelli. "
                 "dual_best_ecmwf e' il merge client-side di best_match + "
                 "ecmwf_ifs (models_derived), non una serie. null = nessun punto "
                 "della provincia con il dato a quell'ora."),
    }


def main():
    parser = argparse.ArgumentParser(description="Build derived MeteoRisk dataset.")
    parser.add_argument("--points-json", default=str(common.REPO_ROOT / "data" / "_workdir" / "real_points.json"))
    parser.add_argument("--raw-json", default=str(common.DATA_RAW / "source_raw.json"))
    parser.add_argument("--force", action="store_true", help="Build anche senza raw (placeholder/empty).")
    parser.add_argument("--empty", action="store_true", help="Genera dataset placeholder (status=empty).")
    args = parser.parse_args()

    if not args.empty and not os.path.exists(args.raw_json):
        print("[build] RAW non trovato (--raw-json). Usare --empty per il placeholder o eseguire prima fetch_source_data.py.")
        return 2

    regions = common._load_regions()
    state = common.load_run_state()
    run_info = {
        "run_key": state.get("run_key"),
        "driver_model": state.get("driver_model") or common.DRIVER_MODEL,
        "run_init_ts": state.get("run_init_ts"),
        "run_available_ts": state.get("run_available_ts"),
        "fetched_at": state.get("checked_at"),
    }
    fetch_timestamps = None
    points_meta = json.load(open(args.points_json, encoding="utf-8")) if os.path.exists(args.points_json) else []
    points_meta_map = {p["index"]: p for p in points_meta}

    # giorno di partenza del forecast (giorno 0) per le etichette.
    # 1.0.1.4: FALLBACK di sola emergenza (senza marker _time nei raw): init del run.
    # La fonte di verità REALE è il giorno Europe/Rome del FETCH (override sotto).
    init_ts = int(state.get("run_init_ts") or 0)
    day0 = _dt.datetime.fromtimestamp(init_ts, _dt.timezone.utc).astimezone(
        _dt.timezone(_dt.timedelta(hours=1)))  # Europe/Rome e' UTC+1 in inverno (approssimazione sicura: si
    # usa il day label SOLO informativo)
    if init_ts:
        from datetime import timezone
        tz_rome = timezone(_dt.timedelta(hours=1))
        day0 = _dt.datetime.fromtimestamp(init_ts, tz_rome).date()
    else:
        day0 = _dt.datetime.now(_dt.timezone.utc).date() + _dt.timedelta(days=0)

    def day_label(i):
        return (day0 + _dt.timedelta(days=i)).isoformat()

    if args.empty:
        print("[build] EMTPY placeholder dataset (status=empty).")
        common.mkdirs(common.DATA_STAGING)
        metadata = {
            "schema_version": 4,
            "dataset_type": common.APP_DATA_TYPE,
            "status": "empty",
            "application": common.APP_NAME,
            "source_data": [{"name": common.SOURCE_NAME, "role": common.SOURCE_ROLE,
                             "license": common.SOURCE_LICENSE}],
            "attribution": ATTR,
            "generated_at": common.now_iso(),
            "models_covered": list(common.SOURCE_MODELS),
            "forecast_days": common.FORECAST_DAYS,
            "timezone": common.TIMEZONE,
            "point_count": 0,
            "province_count": 0,
            "run_info": run_info,
            "files": {"points": "meteorisk-points.json", "provinces": "meteorisk-provinces.json",
                      "compact": "meteorisk-points-compact.json",
                      "validation": "validation.json"},
            "note": "Placeholder: il dataset reale viene generato dalla GitHub Action "
                    "(o da un run locale riuscito) con i dati della fonte Open-Meteo.",
        }
        if fetch_timestamps is not None:
            metadata["fetch_timestamps"] = fetch_timestamps
        with open(common.DATA_STAGING / "metadata.json", "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2, ensure_ascii=False)
        with open(common.DATA_STAGING / "meteorisk-points.json", "w", encoding="utf-8") as fh:
            json.dump({"schema_version": 4, "status": "empty", "generated_at": metadata["generated_at"],
                       "points": []}, fh, ensure_ascii=False)
        with open(common.DATA_STAGING / "meteorisk-provinces.json", "w", encoding="utf-8") as fh:
            json.dump({"schema_version": 4, "status": "empty", "generated_at": metadata["generated_at"],
                       "province_count": 0, "provinces": []}, fh, ensure_ascii=False)
        # Placeholder del dataset compatto: publish_dataset.py lo copia in
        # blocco con gli altri, quindi il file deve ESISTERE anche a dataset
        # vuoto (contratto: 0 province, 0 campi, series {}).
        with open(common.DATA_STAGING / "meteorisk-points-compact.json", "w", encoding="utf-8") as fh:
            json.dump({"schema": COMPACT_SCHEMA, "status": "empty",
                       "generated_at": metadata["generated_at"], "model": "best_match",
                       "models_covered": list(common.MODEL_MENU),
                       "models_derived": list(common.MODELS_DERIVED),
                       "licence": common.SOURCE_LICENSE,
                       "licence_url": "https://creativecommons.org/licenses/by/4.0/",
                       "attribution": ATTR, "source": common.SOURCE_NAME,
                       "timezone": common.TIMEZONE, "forecast_days": common.FORECAST_DAYS,
                       "hours": 0, "days": 0, "count": 0, "time": [],
                       "fields": [], "units": {}, "province_fields": list(COMPACT_PROVINCE_FIELDS),
                       "provinces": [], "series": {}, "model_series": {},
                       "daily_fields": [], "daily": {}, "model_daily": {}},
                      fh, separators=(",", ":"), ensure_ascii=False)
        print("[build] Staging placeholder pronto in data/_staging.")
        return 0

    raw = json.load(open(args.raw_json, encoding="utf-8"))
    raw_map = {idx: payload for idx, payload in raw.get("points", [])}
    fetch_ts = raw.get("fetched_at")
    run_info["fetched_at"] = fetch_ts

    # 1.0.1.4 — FIX CRITICO day0 (scarto +1 "oggi→domani" LIVE). Le serie orarie/giornaliere
    # Open-Meteo partono SEMPRE dall'00:00 del giorno Europe/Rome in cui avviene il FETCH
    # (daily[0]=stesso giorno; verified live 2026-09-09), NON dal giorno di init del run
    # driver. Se il run partiva la sera/notte del giorno prima, day0 (da run_init_ts) restava
    # indietro di 1 → in app "Oggi" mappava sull'indice del giorno dopo. SOVRASCRIVI day0 dal
    # primo timestamp degli array (marker transitorio _time scritto da fetch_source_data.py);
    # time_base = primo timestamp orario (oggetto d'invariante per validate_dataset).
    data_day0 = None
    time_base = None
    if raw_map:
        for _ridx in sorted(raw_map):
            _pt = raw_map[_ridx]
            _tm = _pt.get("_time") if isinstance(_pt, dict) else None
            if not _tm:
                continue
            _hb = _tm.get("hourly0")
            _db = _tm.get("daily0")
            if _db:
                try:
                    data_day0 = _dt.date.fromisoformat(_db[:10])
                except ValueError:
                    data_day0 = None
            if not data_day0 and _hb:
                try:
                    data_day0 = _dt.date.fromisoformat(_hb[:10])
                except ValueError:
                    data_day0 = None
            if _hb:
                time_base = _hb
            break
    if data_day0:
        day0 = data_day0
        print("[build] day0 effettivo dai dati: %s (time_base=%s)" % (day0.isoformat(), time_base))
    else:
        print("[build] AVVISO: _time assente nei raw; day0 da init run (%s)."
              % day0.isoformat())
    leg_ts = raw.get("leg_timestamps") or {}
    bm_ts = leg_ts.get("best_match_fetched_at") or fetch_ts
    ecm_ts = leg_ts.get("ecmwf_fetched_at") or fetch_ts
    cycle_mode = raw.get("cycle_mode") or "coordinated"
    if cycle_mode == "best_match_only":
        coord_note = ("best_match aggiornata da sola (canary sentinelle 1.0.0.8); ecmwf_ifs invariata "
                      "dal ciclo precedente (leg_timestamps espliciti).")
    else:
        coord_note = ("best_match e ecmwf_ifs scaricati nello stesso ciclo di fetch comandato "
                      "dal run ECMWF IFS (coerenza temporale del dataset).")
    fetch_timestamps = {
        "dataset_generation_timestamp": fetch_ts,
        "ecmwf_fetch_timestamp": ecm_ts,
        "best_match_fetch_timestamp": bm_ts,
        "coordinated_cycle": coord_note,
    }

    provinces = []
    for i, r in enumerate(regions):
        provinces.append({"idx": r["idx"], "sigla": r["sigla"], "prov": r["prov"],
                          "region": r["region"], "selected_point": None, "days": []})

    points_out = []
    pidx_map = {p["sigla"]: p["idx"] for p in regions}
    for meta in sorted(points_meta_map.values(), key=lambda p: p["index"]):
        idx = meta["index"]
        payload = raw_map.get(idx)
        if not payload:
            continue
        models = dedupe_models(payload)
        if not models:
            continue
        record = models.get("best_match")
        hourly = record["hourly"]
        daily = record["daily"]
        elev = payload.get("elevation")
        summary = [day_summary(hourly, daily, d, day_label(d)) for d in range(common.FORECAST_DAYS)]
        entry = {
            "id": idx,
            "provinceIdx": meta["provinceIdx"],
            "sigla": meta["sigla"],
            "coordIdx": meta["coordIdx"],
            "lat": common.r2(meta["lat"], 6),
            "lon": common.r2(meta["lon"], 6),
            "elevation": common.r2(elev, 1) if elev is not None else None,
            "models": models,
            "summary": summary,
        }
        points_out.append(entry)

    # Worst-point per provincia (port fedele di scorePointForProvince, tie-break primo max in ordine slot)
    points_by_prov = {}
    for e in points_out:
        points_by_prov.setdefault(e["provinceIdx"], []).append(e)

    for prov in provinces:
        cands = points_by_prov.get(prov["idx"], [])
        if not cands:
            continue
        best = None
        best_score = -1.0
        for e in cands:  # gia' ordinati per id crescente
            rec = {"hourly": e["models"]["best_match"]["hourly"],
                   "daily": e["models"]["best_match"]["daily"]}
            sc = common.score_point_for_province(rec)
            if sc > best_score:
                best_score = sc
                best = e
        if best is not None:
            prov["selected_point"] = {
                "id": best["id"], "score": common.r2(best_score, 1),
                "coordIdx": best["coordIdx"], "lat": best["lat"], "lon": best["lon"],
            }
            prov["days"] = best["summary"]

    points_write = {"schema_version": 4, "status": "live",

                    "generated_at": raw["fetched_at"],
                    "forecast_days": common.FORECAST_DAYS,
                    "timezone": common.TIMEZONE,
                    "models": list(common.SOURCE_MODELS),
                    # Copertura dei parametri OPZIONALI (visibility in metri):
                    # e' il conteggio che dice quante localita' hanno davvero
                    # il campo. Assente nei raw vecchi -> nessuna chiave.
                    "hourly_optional": raw.get("hourly_optional"),
                    "day0": day_label(0),
                    "points": points_out}
    provinces_write = {"schema_version": 4, "status": "live",
                       "generated_at": raw["fetched_at"],
                       "province_count": len(provinces),
                       "provinces": provinces}

    common.mkdirs(common.DATA_STAGING)
    common.DATA_STAGING.joinpath("meteorisk-points.json").write_text(
        json.dumps(points_write, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    common.DATA_STAGING.joinpath("meteorisk-provinces.json").write_text(
        json.dumps(provinces_write, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    # Dataset COMPATTO per provincia (una sola richiesta HTTP dal client).
    # Costruito dagli stessi record appena scritti in meteorisk-points.json:
    # la fonte completa resta invariata, l'aggregato e' coerente per costruzione.
    compact = build_compact_dataset(points_out, regions, raw["fetched_at"], day0,
                                    time_base=time_base, hours=COMPACT_MAX_HOURS,
                                    fields=COMPACT_FIELD_NAMES)
    compact_path = common.DATA_STAGING / "meteorisk-points-compact.json"
    compact_path.write_text(json.dumps(compact, separators=(",", ":"), ensure_ascii=False),
                            encoding="utf-8")

    metadata = {
        "schema_version": 4,
        "dataset_type": common.APP_DATA_TYPE,
        "status": "live",
        "application": common.APP_NAME,
        "source_data": [{"name": common.SOURCE_NAME, "role": common.SOURCE_ROLE,
                         "license": common.SOURCE_LICENSE}],
        "attribution": ATTR,
        "generated_at": raw["fetched_at"],
        "models_covered": list(common.SOURCE_MODELS),
        "models_derived": list(common.MODELS_DERIVED),
        "forecast_days": common.FORECAST_DAYS,
        "timezone": common.TIMEZONE,
        "point_count": len(points_out),
        "province_count": len(provinces),
        "day0": day_label(0),
        "time_base": time_base or (day0.isoformat() + "T00:00"),
        "run_info": run_info,
        "fetch_timestamps": fetch_timestamps,
        "update_strategy": cycle_mode,
        "files": {"points": "meteorisk-points.json", "provinces": "meteorisk-provinces.json",
                  "compact": "meteorisk-points-compact.json",
                  "validation": "validation.json"},
    }
    with open(common.DATA_STAGING / "metadata.json", "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)

    print("[build] Staging: %d punti · %d province (%.0f KB points, %.0f KB provinces, %.0f KB compact)"
          % (len(points_out), len(provinces),
             (common.DATA_STAGING / "meteorisk-points.json").stat().st_size / 1024.0,
             (common.DATA_STAGING / "meteorisk-provinces.json").stat().st_size / 1024.0,
             compact_path.stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())