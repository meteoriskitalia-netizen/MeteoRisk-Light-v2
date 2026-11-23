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
  data/latest/metadata.json, meteorisk-points.json, meteorisk-provinces.json
  data/latest/hourly_extended.json  (schema meteorisk.hourly-ext/1, 96 ore
  = 72..167, 107 province, 5 campi, 2 modelli: SOLO i 5 campi usati da grafico
  e tabella della vista Simple; il dataset base resta a 72 ore / 3 giorni)

ORIZZONTE (fase 1 "previsioni orarie a 7 giorni", opzione D + E)
----------------------------------------------------------------
Il fetch chiede `common.FORECAST_DAYS` = 7 giorni (best_match ed ecmwf_ifs
coprono entrambi >= 168 ore). Qui si SEPARANO due cose:
  - dataset BASE (points/provinces): SLICE esplicito a PUBLISH_HOURS (72) e
    PUBLISH_DAYS (3). I client attuali non cambiano.
  - ESTENSIONE (hourly_extended.json): le ore 72..167 dei 5 campi, per il
    punto `selected_point` di ogni provincia, per ENTRAMBI i modelli.
Oltre l'orizzonte reale di un modello le ore restano null con `null_reason`
(es. `model_horizon_67h`): MAI interpolazione, MAI zeri.
"""

import argparse
import datetime as _dt
import json
import os
import sys

sys.path.insert(0, __file__ and __file__[: __file__.rfind("\\")] or ".")
import common

ATTR = ("Meteorological source data: Open-Meteo.com / Data license: CC BY 4.0 / "
        "Processed and aggregated by MeteoRisk Light.")


def dedupe_models(record, hours=None, days=None):
    """record: {best_match: {daily,hourly}, ecmwf_ifs: {...}, elevation}.

    `hours`/`days` (default PUBLISH_HOURS/PUBLISH_DAYS, ossia 72/3) troncano
    le serie: il fetch puo' chiedere 7 giorni, il dataset BASE ne pubblica 3.
    Lo slice e' ESPLICITO (non dipende da quante ore ha restituito l'API)."""
    hours = common.PUBLISH_HOURS if hours is None else int(hours)
    days = common.PUBLISH_DAYS if days is None else int(days)
    out = {}
    for m in ("best_match", "ecmwf_ifs"):
        sub = record.get(m)
        if sub and sub.get("daily") and sub.get("hourly"):
            out[m] = {
                "daily": {k: (v[:days] if isinstance(v, list) else v)
                          for k, v in sub["daily"].items()},
                "hourly": {k: (v[:hours] if isinstance(v, list) else v)
                           for k, v in sub["hourly"].items()},
            }
    return out


def dedupe_models_full(record):
    """Come dedupe_models ma SENZA slice: serve all'estensione 72..167."""
    out = {}
    for m in ("best_match", "ecmwf_ifs"):
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


# ---------------------------------------------------------------------------
# ESTENSIONE ORARIA 72..167 (schema meteorisk.hourly-ext/1)
# ---------------------------------------------------------------------------
def _round_ext(value, decimals):
    """Arrotondamento dichiarato per l'estensione (None resta None)."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if decimals == 0:
        return int(round(num))
    return round(num, decimals)


def extended_hours(time_base, day0):
    """96 etichette ISO locali: l'ora 0 dell'estensione e' time_base + 72 h.

    Continuita' con il dataset base: l'ora 71 del base e l'ora 0 qui sono
    istanti CONSECUTIVI (stesso asse temporale Europe/Rome dell'API)."""
    start = None
    if time_base:
        try:
            start = _dt.datetime.fromisoformat(str(time_base))
        except ValueError:
            start = None
    if start is None:
        start = _dt.datetime.combine(day0, _dt.time())
    base = start + _dt.timedelta(hours=common.EXTENDED_START_HOUR)
    return [(base + _dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
            for i in range(common.EXTENDED_HOURS)]


def _extended_row(hourly, hour):
    row = []
    for name in common.EXTENDED_FIELDS:
        arr = hourly.get(name)
        val = arr[hour] if isinstance(arr, list) and hour < len(arr) else None
        row.append(_round_ext(val, common.EXTENDED_DECIMALS[name]))
    return row


def extended_rows(hourly):
    """96 righe [t,p,pp,w,c] + null_reason se la coda e' fuori orizzonte.

    Un null di CELLA e' il null della fonte (propagato, non inventato). Un
    blocco di coda tutto null e' invece un ORIZZONTE esaurito e viene
    dichiarato: `model_horizon_<ora assoluta>h` (es. ICON-D2 -> 67h)."""
    rows = [_extended_row(hourly, common.EXTENDED_START_HOUR + k)
            for k in range(common.EXTENDED_HOURS)]
    tail = None
    for i in range(len(rows) - 1, -1, -1):
        if any(v is not None for v in rows[i]):
            break
        tail = i
    reason = None
    if tail is not None:
        reason = "model_horizon_%dh" % (common.EXTENDED_START_HOUR + tail)
    return rows, reason


def build_hourly_extended(points_out, provinces, day0, time_base, generated_at,
                          models_by_id=None):
    """Costruisce il payload dell'estensione (schema meteorisk.hourly-ext/1).

    `models_by_id` permette di passare le serie COMPLETE del raw (168 ore):
    `points_out` e' gia' troncato a PUBLISH_HOURS e non basta all'estensione.
    Se assente si usano i modelli dell'entry (utile nei test).

    point_id: ESATTAMENTE `provinces[].selected_point.id`, cioe' il punto che
    il client usa per la provincia (worst-point best_match, port di
    scorePointForProvince). Lo stesso punto per ENTRAMBI i modelli: e' una
    localita', non un modello. Nessuna seconda regola di scelta."""
    hours = extended_hours(time_base, day0)
    by_id = {e["id"]: e for e in points_out}
    out = {}
    stats = {"provinces": 0, "points": 0, "with_data": 0, "null_points": 0,
             "horizon_reasons": {}}
    for prov in provinces:
        sel = prov.get("selected_point") or {}
        pid = sel.get("id")
        entry = by_id.get(pid) if pid is not None else None
        if pid is None or entry is None:
            continue
        mods = (models_by_id or {}).get(pid) or (entry.get("models") or {})
        recs = []
        for model in ("best_match", "ecmwf_ifs"):
            mod = mods.get(model)
            hourly = (mod or {}).get("hourly") or {}
            if not hourly:
                recs.append({"model": model, "point_id": int(pid), "h": None,
                             "null_reason": "model_missing"})
                stats["null_points"] += 1
                continue
            rows, reason = extended_rows(hourly)
            rec = {"model": model, "point_id": int(pid), "h": rows}
            if reason:
                rec["null_reason"] = reason
                stats["horizon_reasons"][reason] = \
                    stats["horizon_reasons"].get(reason, 0) + 1
            else:
                stats["with_data"] += 1
            recs.append(rec)
        out[prov["sigla"]] = {"points": recs}
        stats["provinces"] += 1
        stats["points"] += len(recs)
    payload = {
        "schema": common.EXTENDED_SCHEMA,
        "generated_at": generated_at,
        "day0": day0.isoformat() if hasattr(day0, "isoformat") else str(day0),
        "timezone": common.TIMEZONE,
        "start_hour": common.EXTENDED_START_HOUR,
        "hours": hours,
        "fields": list(common.EXTENDED_FIELD_KEYS),
        "source_fields": list(common.EXTENDED_FIELDS),
        "units": dict(common.EXTENDED_UNITS),
        "point_rule": ("point_id = provinces[].selected_point.id del dataset base "
                       "(worst-point best_match); stesso punto per i due modelli"),
        "provinces": out,
    }
    return payload, stats


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
            "models_covered": ["best_match", "ecmwf_ifs"],
            "forecast_days": common.PUBLISH_DAYS,
            "timezone": common.TIMEZONE,
            "point_count": 0,
            "province_count": 0,
            "run_info": run_info,
            "files": {"points": "meteorisk-points.json", "provinces": "meteorisk-provinces.json",
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
    full_models_by_id = {}
    pidx_map = {p["sigla"]: p["idx"] for p in regions}
    for meta in sorted(points_meta_map.values(), key=lambda p: p["index"]):
        idx = meta["index"]
        payload = raw_map.get(idx)
        if not payload:
            continue
        # Serie COMPLETE (168 h) per l'estensione; `models` e' lo SLICE
        # pubblicato (72 h) per il dataset base.
        full_models_by_id[idx] = dedupe_models_full(payload)
        models = dedupe_models(payload)
        if not models:
            continue
        record = models.get("best_match")
        hourly = record["hourly"]
        daily = record["daily"]
        elev = payload.get("elevation")
        summary = [day_summary(hourly, daily, d, day_label(d)) for d in range(common.PUBLISH_DAYS)]
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
                    # 3 giorni PUBBLICATI (il fetch ne chiede 7: slice esplicito)
                    "forecast_days": common.PUBLISH_DAYS,
                    "timezone": common.TIMEZONE,
                    "models": ["best_match", "ecmwf_ifs"],
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

    # ESTENSIONE 72..167 (file AGGIUNTIVO e SEPARATO): scritta in modo atomico,
    # poi pubblicata da publish_dataset.py insieme agli altri file del dataset.
    ext_payload, ext_stats = build_hourly_extended(
        points_out, provinces, day0, time_base, raw["fetched_at"],
        models_by_id=full_models_by_id)
    ext_bytes, ext_sha = common.write_json_atomic(
        common.DATA_STAGING / "hourly_extended.json", ext_payload,
        separators=(",", ":"))
    print("[build] Estensione oraria: %d province x %d ore x %d campi x 2 modelli "
          "(%d serie con dati, %d senza; motivi orizzonte: %s) · %.0f KB · sha256 %s"
          % (ext_stats["provinces"], common.EXTENDED_HOURS,
             len(common.EXTENDED_FIELDS), ext_stats["with_data"],
             ext_stats["null_points"] + sum(ext_stats["horizon_reasons"].values()),
             ext_stats["horizon_reasons"] or "nessuno",
             ext_bytes / 1024.0, ext_sha[:16]))

    metadata = {
        "schema_version": 4,
        "dataset_type": common.APP_DATA_TYPE,
        "status": "live",
        "application": common.APP_NAME,
        "source_data": [{"name": common.SOURCE_NAME, "role": common.SOURCE_ROLE,
                         "license": common.SOURCE_LICENSE}],
        "attribution": ATTR,
        "generated_at": raw["fetched_at"],
        "models_covered": ["best_match", "ecmwf_ifs"],
        # Contratto del dataset BASE: 3 giorni / 72 ore pubblicate.
        "forecast_days": common.PUBLISH_DAYS,
        "forecast_days_requested": common.FORECAST_DAYS,
        "extended_hours": {"start_hour": common.EXTENDED_START_HOUR,
                           "hours": common.EXTENDED_HOURS,
                           "schema": common.EXTENDED_SCHEMA,
                           "fields": list(common.EXTENDED_FIELDS)},
        "timezone": common.TIMEZONE,
        "point_count": len(points_out),
        "province_count": len(provinces),
        "day0": day_label(0),
        "time_base": time_base or (day0.isoformat() + "T00:00"),
        "run_info": run_info,
        "fetch_timestamps": fetch_timestamps,
        "update_strategy": cycle_mode,
        "files": {"points": "meteorisk-points.json", "provinces": "meteorisk-provinces.json",
                  "validation": "validation.json",
                  "hourly_extended": "hourly_extended.json"},
    }
    with open(common.DATA_STAGING / "metadata.json", "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)

    print("[build] Staging: %d punti · %d province (%.0f KB points, %.0f KB provinces)"
          % (len(points_out), len(provinces),
             (common.DATA_STAGING / "meteorisk-points.json").stat().st_size / 1024.0,
             (common.DATA_STAGING / "meteorisk-provinces.json").stat().st_size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())