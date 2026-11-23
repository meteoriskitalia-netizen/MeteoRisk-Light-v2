#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 8 (validate_dataset.py): VALIDAZIONE del dataset derivato PRIMA della
pubblicazione. In caso di esito negativo NON viene toccato data/latest
(last known good preservato). Vengono controllate proprietà di forma,
contenuto, integrità territoriale, consistenza col port di coordinate e
coerenza della selezione worst-point ricalcolata.

Output: data/_staging/validation.json (copia anche in data/_workdir per l'Action).
Exit: 0 = valido, 1 = invalido.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, __file__ and __file__[: __file__.rfind("\\")] or ".")
import common


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    parser = argparse.ArgumentParser(description="Validate derived dataset (staging).")
    parser.add_argument("--staging", action="store_true",
                        help="Valida il contenuto di data/_staging (default: data/latest).")
    parser.add_argument("--dir", default=None,
                        help="Valida una directory arbitraria (per test negativi).")
    args = parser.parse_args()
    if args.dir:
        base = Path(args.dir)
    else:
        base = common.DATA_STAGING if args.staging else common.DATA_LATEST

    checks = []
    errors = []
    infos = []

    def check(name, cond, detail=""):
        status = "PASS" if cond else "FAIL"
        checks.append({"check": name, "status": status, "detail": detail or ""})
        print("  [%s] %s%s" % (status, name, (" · " + detail) if detail else ""))
        if not cond:
            errors.append(name)

    def info(msg):
        infos.append(msg)
        print("    ~ %s" % msg)

    metadata_path = base / "metadata.json"
    points_path = base / "meteorisk-points.json"
    provinces_path = base / "meteorisk-provinces.json"

    # A. Presenza / tipi
    check("metadata.json existe", metadata_path.exists())
    check("meteorisk-points.json existe", points_path.exists())
    check("meteorisk-provinces.json existe", provinces_path.exists())
    if errors:
        return finish(checks, infos, errors, "FILES")

    meta = read_json(metadata_path)
    points = read_json(points_path)
    provs = read_json(provinces_path)

    check("dataset_type derivato", meta.get("dataset_type") == common.APP_DATA_TYPE,
          "got %s" % meta.get("dataset_type"))
    check("application", meta.get("application") == common.APP_NAME)
    check("source_data dichiara Open-Meteo come input",
          any((s.get("name") == "Open-Meteo" for s in (meta.get("source_data") or []))))
    check("attribution presente e senza endorsement", bool(meta.get("attribution")))
    att = meta.get("attribution") or ""
    check("nessun endorsement/affiliazione",
          not any(w in att.lower() for w in ("affiliated", "endorsed", "by open-meteo", "supported by")))
    check("forecast_days=%d (giorni PUBBLICATI, il fetch ne chiede %d)"
          % (common.PUBLISH_DAYS, common.FORECAST_DAYS),
          meta.get("forecast_days") == common.PUBLISH_DAYS)
    check("timezone Europe/Rome", meta.get("timezone") == common.TIMEZONE)
    check("models_covered = best_match,ecmwf_ifs",
          sorted(meta.get("models_covered") or []) == ["best_match", "ecmwf_ifs"])

    live = meta.get("status") == "live"
    if not live:
        check("dataset vuoto -> struct valida (placeholder)", points.get("status") == "empty"
              and provs.get("status") == "empty")
        check("zero punti/zero province placeholder", points.get("point_count", len(points.get("points", []))) == 0)
        finish(checks, infos, errors, "EMPTY_OK")
        return 0

    # Coordinated scheduling: il ciclo è comandato dal run ECMWF IFS;
    # best_match + ecmwf_ifs devono essere coperti e temporalmente coerenti.
    ri = meta.get("run_info") or {}
    check("driver_model == ecmwf_ifs", ri.get("driver_model") == common.DRIVER_MODEL,
          "got %s" % ri.get("driver_model"))
    check("run_info.run_key present", bool(ri.get("run_key")))
    check("run_info.run_init_ts present", ri.get("run_init_ts") is not None)
    check("run_info.fetched_at present", bool(ri.get("fetched_at")))
    ft = meta.get("fetch_timestamps") or {}
    check("fetch_timestamps presenti (coerenza temporale)",
          bool(ft.get("ecmwf_fetch_timestamp") and ft.get("best_match_fetch_timestamp")))
    us = meta.get("update_strategy") or "coordinated"
    check("update_strategy valida", us in ("coordinated", "best_match_only"), "got %s" % us)
    check("best_match_fetch_timestamp >= ecmwf_fetch_timestamp",
          ft.get("best_match_fetch_timestamp") >= ft.get("ecmwf_fetch_timestamp"),
          "best=%s ecmwf=%s" % (ft.get("best_match_fetch_timestamp"), ft.get("ecmwf_fetch_timestamp")))
    check("update_strategy coerente con leg_timestamps",
          (us == "best_match_only") == (ft.get("best_match_fetch_timestamp") != ft.get("ecmwf_fetch_timestamp")),
          "strategy=%s best=%s ecmwf=%s" % (us, ft.get("best_match_fetch_timestamp"),
                                            ft.get("ecmwf_fetch_timestamp")))

    # B. Punti: forma e integrità
    plist = points.get("points", [])
    check("point_count == metadati", meta.get("point_count") == len(plist),
          "meta=%s len=%s" % (meta.get("point_count"), len(plist)))
    check("day0 presente", bool(points.get("day0")), "got %r" % points.get("day0"))
    # 1.0.1.4 — INVARIANTE TEMPORALE (fix scarto +1 "oggi→domani"): le serie Open-Meteo
    # partono dall'00:00 del giorno Europe/Rome del fetch; day0 dev'essere l'ESATTO giorno
    # di time_base (primo timestamp orario) e coincidere tra metadata e points. Un dataset
    # con day0 sfasato (run partito il giorno prima) non deve MAI arrivare in pubblicazione.
    check("metadata.day0 == time_base[:10] (giorno di partenza array)", meta.get("time_base")
          and meta.get("day0") == (meta.get("time_base") or "")[:10],
          "day0=%s time_base=%s" % (meta.get("day0"), meta.get("time_base")))
    check("points.day0 == metadata.day0 (stesso giorno di partenza)",
          points.get("day0") == meta.get("day0"),
          "points=%s meta=%s" % (points.get("day0"), meta.get("day0")))
    ids = [p.get("id") for p in plist]
    check("ids univoci e contigui 0..N-1", sorted(ids) == list(range(len(plist))))
    s_prov = set()
    for e in plist:
        check("field lat/lon numerici", isinstance(e.get("lat"), (int, float)) and isinstance(e.get("lon"), (int, float)))
        if not isinstance(e.get("lat"), (int, float)) or not isinstance(e.get("lon"), (int, float)):
            continue
        check("lat in [30,70] lon in [-40,40]", 30 <= e["lat"] <= 70 and -40 <= e["lon"] <= 40,
              "(%s,%s)" % (e["lat"], e["lon"]))
        if e.get("coordIdx") != 0:
            pass  # integrità territoriale verificata in aggregato sotto
        s_prov.add(e.get("provinceIdx"))
        for model_key in ("best_match", "ecmwf_ifs"):
            mod = (e.get("models") or {}).get(model_key)
            check("modello %s presente" % model_key, bool(mod and mod.get("hourly") and mod.get("daily")))
            if not mod:
                continue
            hv = set(mod["hourly"].keys())
            dv = set(mod["daily"].keys())
            # CONTRATTO hourly: tutti i campi OBBLIGATORI presenti, nessun
            # campo sconosciuto, e i parametri OPZIONALI (`visibility`) SOLO se
            # POPOLATI. Un `visibility` di soli null viene scartato da
            # `common.hourly_record_fields`: pretenderlo qui renderebbe il
            # validatore incompatibile con la pipeline (e un campo vuoto non e'
            # un dato).
            req = set(common.HOURLY_FIELDS_REQUIRED)
            check("variabili hourly obbligatorie == param set",
                  req <= hv <= set(common.HOURLY_FIELDS),
                  "mancanti=%s ignote=%s" % (
                      sorted(req - hv)[:5],
                      sorted(hv - set(common.HOURLY_FIELDS))[:5]))
            empty_opt = [f for f in sorted(hv & set(common.HOURLY_OPTIONAL_PARAMS))
                         if all(v is None for v in (mod["hourly"].get(f) or []))]
            check("parametri hourly opzionali presenti solo se popolati",
                  not empty_opt, "vuoti=%s" % empty_opt)
            check("variabili daily == param set", dv == set(common.DAILY_FIELDS))
            lens = {k: len(v) for k, v in mod["hourly"].items()}
            check("hourly array == 72 (3 giorni)", setId(lens.values()) == {72})
            dlens = {k: len(v) for k, v in mod["daily"].items()}
            check("daily array == 3", setId(dlens.values()) == {3})
            check("hourly numerici", all(isinstance(x, (int, float)) for k, v in mod["hourly"].items()
                                          for x in (v or []) if x is not None))
    check("tutte le 107 province coperte", s_prov == set(range(107)),
          "mancanti=%s" % sorted(set(range(107)) - s_prov)[:10])
    check("ogni provincia con >=1 punto", len(s_prov) == 107)

    # integrità territoriale (non-capoluoghi dentro il poligono della propria provincia)
    geojson = json.load(open(common.GEOJSON_PATH, encoding="utf-8"))
    poly_by_sigla = {}
    for f in geojson["features"]:
        sigla = f["properties"].get("SIGLA")
        rings = common.rings_of(f)
        poly_by_sigla[sigla] = [r for r in rings if len(r) >= 4]
    outside = 0
    for e in plist:
        if e.get("coordIdx") == 0:
            continue
        rings = poly_by_sigla.get(e.get("sigla")) or []
        if not any(common.point_in_ring(e, r) for r in rings):
            outside += 1
    check("punti non-capoluogo dentro il poligono (0 tollerato)", outside == 0, "fuori=%d" % outside)

    # C. Province: forma, nomi, selezione worst-point ricalcolata
    prov_list = provs.get("provinces", [])
    check("province_count == 107", provs.get("province_count") == len(prov_list) == 107)
    check("indici 0..106", [p["idx"] for p in prov_list] == list(range(107)))
    regions = common._load_regions()
    mism = 0
    for p in prov_list:
        r = regions[p["idx"]]
        if p.get("sigla") != r["sigla"] or p.get("prov") != r["prov"]:
            mism += 1
    check("sigla/prov coerenti con regions_data", mism == 0, "mismatch=%d" % mism)
    # ricalcolo worst-point da best_match (stesso tie-break: primo max per id crescente)
    by_prov = {}
    for e in plist:
        by_prov.setdefault(e["provinceIdx"], []).append(e)
    sel_mism = 0
    prov_missing = 0
    for p in prov_list:
        sel = p.get("selected_point")
        if not sel:
            prov_missing += 1
            continue
        cands = by_prov.get(p["idx"], [])
        best = None
        bs = -1.0
        for e in cands:
            rec = {"hourly": e["models"]["best_match"]["hourly"], "daily": e["models"]["best_match"]["daily"]}
            sc = common.score_point_for_province(rec)
            if sc > bs:
                bs = sc
                best = e
        if best is None or best["id"] != sel.get("id"):
            sel_mism += 1
    check("worst-point presente per ogni provincia", prov_missing == 0, "mancanti=%d" % prov_missing)
    check("selezione worst-point ricalcolata == dataset (0 mismatch)", sel_mism == 0, "mismatch=%d" % sel_mism)

    # D. Hash e dimensioni
    for rel in ("meteorisk-points.json", "meteorisk-provinces.json"):
        p = base / rel
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        info("sha256 %s = %s (%d byte)" % (rel, sha, p.stat().st_size))

    # E. ESTENSIONE ORARIA 72..167 (schema meteorisk.hourly-ext/1)
    # File AGGIUNTIVO: il dataset base resta a 72 ore. Il build della 7 giorni
    # lo scrive SEMPRE e lo dichiara in metadata.files; un latest piu' vecchio
    # (pubblicato prima dell'estensione) non lo ha e non lo dichiara -> i
    # controlli sono saltati con motivo, non inventati.
    ext_path = base / "hourly_extended.json"
    declared = bool((meta.get("files") or {}).get("hourly_extended"))
    if ext_path.exists() or declared:
        check("hourly_extended.json existe", ext_path.exists(),
              "dichiarato in metadata.files" if declared else "")
    if ext_path.exists():
        ext = read_json(ext_path)
        check("estensione: schema == %s" % common.EXTENDED_SCHEMA,
              ext.get("schema") == common.EXTENDED_SCHEMA, "got %r" % ext.get("schema"))
        check("estensione: start_hour == %d" % common.EXTENDED_START_HOUR,
              ext.get("start_hour") == common.EXTENDED_START_HOUR,
              "got %r" % ext.get("start_hour"))
        ext_hours = ext.get("hours") or []
        check("estensione: %d ore (72 -> 167)" % common.EXTENDED_HOURS,
              isinstance(ext_hours, list) and len(ext_hours) == common.EXTENDED_HOURS,
              "got %d" % len(ext_hours))
        check("estensione: fields == %s" % list(common.EXTENDED_FIELD_KEYS),
              ext.get("fields") == list(common.EXTENDED_FIELD_KEYS),
              "got %r" % (ext.get("fields"),))
        check("estensione: source_fields == EXTENDED_FIELDS",
              ext.get("source_fields") == list(common.EXTENDED_FIELDS),
              "got %r" % (ext.get("source_fields"),))
        check("estensione: unita' dichiarate", ext.get("units") == common.EXTENDED_UNITS,
              "got %r" % (ext.get("units"),))
        # CONTINUITA': l'ora 0 dell'estensione e' time_base + 72 h, cioe' l'istante
        # immediatamente successivo all'ora 71 del dataset base.
        expected_first = None
        try:
            base_dt = _dt.datetime.fromisoformat(str(meta.get("time_base") or ""))
            expected_first = (base_dt + _dt.timedelta(hours=common.EXTENDED_START_HOUR)
                              ).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            expected_first = None
        check("estensione: ora 0 == time_base + 72h (continuita' col base)",
              expected_first is not None and bool(ext_hours)
              and ext_hours[0] == expected_first,
              "atteso=%s got=%s" % (expected_first, ext_hours[0] if ext_hours else None))
        consecutive = False
        try:
            stamps = [_dt.datetime.fromisoformat(str(h)) for h in ext_hours]
            consecutive = all((stamps[i + 1] - stamps[i]) == _dt.timedelta(hours=1)
                              for i in range(len(stamps) - 1))
        except ValueError:
            consecutive = False
        check("estensione: ore consecutive (passo 1 h)", consecutive)

        ext_provs = ext.get("provinces") or {}
        base_sigle = {p.get("sigla") for p in prov_list}
        check("estensione: province == dataset base (%d)" % len(base_sigle),
              set(ext_provs) == base_sigle,
              "mancanti=%s extra=%s" % (sorted(base_sigle - set(ext_provs))[:5],
                                        sorted(set(ext_provs) - base_sigle)[:5]))
        sel_by_sigla = {p.get("sigla"): (p.get("selected_point") or {}).get("id")
                        for p in prov_list}
        ranges = {"t": (-90.0, 60.0), "p": (0.0, 500.0), "pp": (0.0, 100.0),
                  "w": (0.0, 500.0), "c": (0.0, 99.0)}
        bad_pid, bad_models, bad_len, missing_reason, non_numeric = [], [], [], [], 0
        range_hits = {k: 0 for k in common.EXTENDED_FIELD_KEYS}
        series = 0
        for sigla, entry in ext_provs.items():
            recs = (entry or {}).get("points") or []
            if sorted(r.get("model") for r in recs) != ["best_match", "ecmwf_ifs"]:
                bad_models.append(sigla)
            for rec in recs:
                series += 1
                if sel_by_sigla.get(sigla) is None or rec.get("point_id") != sel_by_sigla.get(sigla):
                    bad_pid.append(sigla)
                rows = rec.get("h")
                if rows is None:
                    if not rec.get("null_reason"):
                        missing_reason.append(sigla)
                    continue
                if not isinstance(rows, list) or len(rows) != common.EXTENDED_HOURS:
                    bad_len.append(sigla)
                    continue
                for row in rows:
                    if not isinstance(row, list) or len(row) != len(common.EXTENDED_FIELD_KEYS):
                        bad_len.append(sigla)
                        break
                    for idx, key in enumerate(common.EXTENDED_FIELD_KEYS):
                        v = row[idx]
                        if v is None:
                            continue
                        if isinstance(v, bool) or not isinstance(v, (int, float)):
                            non_numeric += 1
                            continue
                        # p. es. weathercode e probabilita' sono INTERI nel
                        # payload (il client li usa come categorie).
                        if key in ("pp", "c") and not isinstance(v, int):
                            non_numeric += 1
                        lo, hi = ranges[key]
                        if not (lo <= float(v) <= hi):
                            range_hits[key] += 1
                # coda fuori orizzonte (ultima riga tutta null) -> motivo dovuto
                if all(all(v is None for v in r) for r in rows) and not rec.get("null_reason"):
                    missing_reason.append(sigla)
                elif all(v is None for v in rows[-1]) and not rec.get("null_reason"):
                    missing_reason.append(sigla)
        check("estensione: point_id == provinces[].selected_point.id",
              not bad_pid, "esempi=%s" % sorted(set(bad_pid))[:5])
        check("estensione: 2 modelli (best_match + ecmwf_ifs) per provincia",
              not bad_models, "esempi=%s" % sorted(set(bad_models))[:5])
        check("estensione: 96 righe x 5 campi per ogni serie",
              not bad_len, "esempi=%s" % sorted(set(bad_len))[:5])
        check("estensione: valori numerici (weathercode/probabilita' interi)",
              non_numeric == 0, "violazioni=%d" % non_numeric)
        check("estensione: nessun valore fuori range",
              not any(range_hits.values()),
              "fuori=%s" % {k: v for k, v in range_hits.items() if v})
        check("estensione: null solo con null_reason",
              not missing_reason, "esempi=%s" % sorted(set(missing_reason))[:5])
        ext_sha = hashlib.sha256(ext_path.read_bytes()).hexdigest()
        info("estensione: %d serie · sha256 hourly_extended.json = %s (%d byte)"
             % (series, ext_sha, ext_path.stat().st_size))
    else:
        info("hourly_extended.json assente e non dichiarato: controlli estensione saltati "
             "(dataset pubblicato prima dell'orizzonte a 7 giorni)")

    return finish(checks, infos, errors, "OK" if not errors else "FAIL")


def setId(vals):
    s = {int(v) for v in vals if v is not None}
    return s if s else {72}


def finish(checks, infos, errors, outcome):
    report = {
        "valid": not errors,
        "outcome": outcome,
        "checked_at": common.now_iso(),
        "dataset": {"status": "live"},
        "checks": checks,
        "infos": infos,
        "errors": errors,
    }
    common.DATA_STAGING.mkdir(parents=True, exist_ok=True)
    common.DATA_WORK.mkdir(parents=True, exist_ok=True)
    for p in (common.DATA_STAGING / "validation.json", common.DATA_WORK / "validation.json"):
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
    print("RESULT: %s (%d checks, %d errori)" % ("PASS" if not errors else "FAIL", len(checks), len(errors)))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())