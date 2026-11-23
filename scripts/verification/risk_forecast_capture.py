#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JOB A — fotografia dei livelli previsti dalla mappa dei rischi (STILE B).

PERCHE' ESISTE
--------------
Il livello 0-4 della provincia non esiste come file: il client lo calcola in
browser (``riskLevelR2`` + ``computeRisksForAgg`` + ``computeZoneRiskSummary``).
``data/latest/*`` viene sovrascritto a ogni run, quindi senza questa cattura il
giorno dopo non c'e' nessuna previsione da verificare.

COSA REPLICA (e cosa no) — STILE B del documento di progetto
------------------------------------------------------------
* ``risk_level_r2`` e' il port ESATTO di ``riskLevelR2`` del client
  ``mri-light-1.3.0.53.html`` (funzione ``riskLevelR2``):
      rain > 45 | gusts > 100 | (code>=95 & rain>=10 & cape>=300)     -> 4
      rain > 15 | gusts > 75  | (code>=95 & rain>=3  & cape>=150)
              | (80<=code<=90 & rain>=8)                              -> 3
      prob > 60 | rain > 1 | gusts > 50                               -> 2
      prob > 20 | rain > 0.2                                          -> 1
      altrimenti                                                      -> 0
  ``meso`` (livello 5) richiede SCP/shear/SRH: non sono pubblicati in
  ``provinces.days[]`` -> il livello 5 non e' rappresentabile (dichiarato).
* I badge per pericolo (``computeRisksForAgg``, 6088-6344):
  - pioggia: rain>1 -> 4 (>90) / 3 (>45) / 2 (>15) / 1; altrimenti prob>60
    -> 1; soppressa se temporale attivo e livello <=1; rovescio convettivo
    (``showers>0.1``) -> 1; pioggerella ``info`` non alza il livello -> 0.
  - vento: usa le RAFRICHE R2 (100/75/50 -> 4/3/2). Il badge della UI usa
    80/60/40, ma il COLORE della provincia passa da R2: qui si replica R2,
    cosi' la riga verificata e' quella che ha deciso il colore.
  - temporale: WMO 95-99 -> 2 (badge base); le gambe 3/4 richiedono pioggia e
    CAPE della stessa ora (R2) e sono replicate; ``scp>0.2`` -> 3 non e'
    replicabile senza indici severi (dichiarato in ``notes``).
  - temporale possibile: ``showers>0.1`` e CAPE>=250 -> 1; il gate CIN
    (|CIN|>150) NON e' applicabile perche' ``convective_inhibition`` non e'
    pubblicato in ``provinces.days[]`` (dichiarato in ``notes``).
  - caldo/freddo: 30/32/35/38 C e 0/-2/-5/-8 C sul massimo/minimo del giorno;
    il cap "montagna" del client usa il punto PIU' BASSO della provincia
    (``tempRefBand``), non pubblicato -> nessun cap (dichiarato in ``notes``).
  - neve: codice WMO 71-77/85-86 e ``freezing_level<=1400`` -> 1/2/3.
  - grandine / convezione organizzata / downburst: NON replicati in fase B ->
    ``pv: null`` con ``null_reason: "component_not_replicated_phaseB"``.
* Livello provincia ``pl = max(R2, componenti non coperte da R2)`` con cap 4,
  esattamente come ``computeZoneRiskSummary`` (R2 copre rain/wind/thunderstorm;
  heat/cold/snow possono alzare il livello). Il livello e' registrato su ogni
  riga (``r2`` e ``pl``) per tracciabilita'.

COSA SCRIVE
-----------
``data/state/risk_forecast_snapshots.jsonl`` (o ``--out``), una riga per
(provincia, pericolo) SOLO se il livello e' cambiato rispetto all'ultimo
snapshot della stessa (giorno, provincia, pericolo), piu' UNA riga ``meta`` per
run con generazione del dataset, versione engine e conteggi. Le righe piu'
vecchie di ``--keep-days`` giorni di validita' vengono ruotate.

Formato riga (campi brevi, ~130 B):
  {"ts","d","hl","dz","p","h","pv","pm","pl","r2","sp","el","ev","ls","v"}
  - ts  istante UTC della cattura        - d   giorno di validita' (locale)
  - hl  ora locale della cattura         - dz  indice del giorno (0/1/2)
  - p   sigla provincia                  - h   pericolo
  - pv  livello previsto 0-4 (null per grandine)
  - pm  metrica prevista che ha deciso il livello
  - pl  livello provincia (max R2+componenti)   - r2 livello R2
  - sp  id del punto scelto (selected_point)    - el quota del punto (m)
  - ev  versione del client/logica (APP_FILE)   - ls  "r2_from_provinces"
  - nr  null_reason (solo se pv e' null)

Uso:
  python scripts/verification/risk_forecast_capture.py \
      --meta-file data/latest/metadata.json \
      --province-file data/latest/meteorisk-provinces.json \
      --points-file data/latest/meteorisk-points.json \
      --out data/state/risk_forecast_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
_SCRIPTS = _HERE.parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from verification.timeutil import (  # noqa: E402
    format_utc, local_day, local_hhmm, now_utc, parse_utc,
)

SCHEMA_VERSION = 1
HAZARDS = ("rain", "wind", "thunderstorm", "hail", "heat", "cold", "snow")
LEVEL_SOURCE = "r2_from_provinces"
DEFAULT_KEEP_DAYS = 10
NULL_HAIL = "component_not_replicated_phaseB"

# Pericoli gia' coperti da R2: NON sono componenti autonome (riga 27173 del
# client: R2_COVERED_RISK_IDS = {rain, wind, thunderstorm}).
R2_COVERED = frozenset(("rain", "wind", "thunderstorm"))

NOTES = (
    "STILE B: R2 esatta + componenti pioggia/vento/temporale/caldo/freddo/neve. "
    "Livello 5 (meso) non rappresentabile: SCP/shear/SRH non sono pubblicati in provinces.days[]. "
    "Temporale: la gamba scp>0.2 non e' replicabile senza indici severi; il badge base e' 2. "
    "Temporale possibile: gate CIN non applicato perche' convective_inhibition non e' pubblicato. "
    "Caldo/freddo: cap 'montagna' non applicato (tempRefBand non pubblicato). "
    "Grandine/convezione organizzata/downburst: pv null con null_reason."
)


# --------------------------------------------------------------------------- #
# Lettura / utilita'
# --------------------------------------------------------------------------- #
def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def as_float(value, default=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def as_optional_float(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def engine_version_from_version_file(path) -> str:
    """Versione del client/logica: ``APP_FILE=mri-light-1.3.0.53.html``.

    Fallback: la prima riga ``VERSION=`` del file. Se nessuna delle due forme
    esiste si solleva: un registro senza versione non e' tracciabile.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^APP_FILE\s*=\s*mri-light-([0-9][0-9A-Za-z.\-]*)\.html\s*$",
                  text, re.MULTILINE)
    if m:
        return m.group(1)
    m = re.search(r"^VERSION\s*=\s*([0-9][0-9A-Za-z.\-]*)\s*$", text, re.MULTILINE)
    if m:
        return m.group(1)
    raise ValueError(f"engine_version_non_trovata:{path}")


# --------------------------------------------------------------------------- #
# Logica del client (port fedele per la parte STILE B)
# --------------------------------------------------------------------------- #
def risk_level_r2(day) -> int:
    """Port esatto di ``riskLevelR2`` (mri-light-1.3.0.53.html, riga 27162)."""
    old = day or {}
    rain = as_float(old.get("precip_sum"), 0.0)
    gusts = as_float(old.get("gusts"), 0.0)
    prob = as_float(old.get("prob_max"), 0.0)
    code = as_float(old.get("weathercode"), 0.0)
    cape = as_float(old.get("cape_max"), 0.0)
    # `meso` non e' pubblicato: nessun livello 5.
    if rain > 45 or gusts > 100 or (code >= 95 and rain >= 10 and cape >= 300):
        return 4
    if (rain > 15 or gusts > 75
            or (code >= 95 and rain >= 3 and cape >= 150)
            or (80 <= code <= 90 and rain >= 8)):
        return 3
    if prob > 60 or rain > 1 or gusts > 50:
        return 2
    if prob > 20 or rain > 0.2:
        return 1
    return 0


def is_thunder_code(code) -> bool:
    code = as_float(code, 0.0)
    return 95 <= code <= 99


def is_snow_code(code) -> bool:
    """``isSnowCode``: 71-77 e 85-86."""
    code = as_float(code, 0.0)
    return (71 <= code <= 77) or code in (85.0, 86.0)


def hazard_levels(day):
    """Livelli per pericolo + metrica che li ha decisi + note di copertura.

    Ritorna ``{"levels": {...}, "pm": {...}, "null_reason": {...}}``.
    """
    d = day or {}
    rain = as_float(d.get("precip_sum"), 0.0)
    prob = as_float(d.get("prob_max"), 0.0)
    code = as_float(d.get("weathercode"), 0.0)
    cape = as_float(d.get("cape_max"), 0.0)
    gusts = as_float(d.get("gusts"), 0.0)
    showers = as_float(d.get("showers"), 0.0)
    tmax = as_optional_float(d.get("temp_max"))
    tmin = as_optional_float(d.get("temp_min"))
    freezing = as_optional_float(d.get("freezing_max"))

    thunder = is_thunder_code(code)
    conv = showers > 0.1

    # --- pioggia: badge computeRisksForAgg (6188-6216) ---
    if rain > 1:
        rain_l = 4 if rain > 90 else 3 if rain > 45 else 2 if rain > 15 else 1
    elif prob > 60:
        rain_l = 1
    else:
        rain_l = 0
    if thunder and rain_l <= 1:
        rain_l = 0
    if rain_l == 0 and not thunder and conv and (prob >= 10 or rain > 0.1):
        rain_l = 1
    # else: pioggia informativa (prob>=10 o rain>0.1) -> NON alza il livello.

    # --- vento: gambe R2 sulle raffiche (100/75/50) ---
    wind_l = 4 if gusts > 100 else 3 if gusts > 75 else 2 if gusts > 50 else 0

    # --- temporale ---
    if thunder:
        if rain >= 10 and cape >= 300:
            thunder_l = 4
        elif rain >= 3 and cape >= 150:
            thunder_l = 3
        else:
            thunder_l = 2  # badge base (WMO 95-99); scp non disponibile
    elif conv and cape >= 250:
        thunder_l = 1  # "temporale possibile"; gate CIN non applicabile
    else:
        thunder_l = 0

    # --- caldo / freddo (cap montagna non applicabile) ---
    heat_l = 0
    if tmax is not None:
        heat_l = 4 if tmax >= 38 else 3 if tmax >= 35 else 2 if tmax >= 32 else 1 if tmax >= 30 else 0
    cold_l = 0
    if tmin is not None:
        cold_l = 4 if tmin <= -8 else 3 if tmin <= -5 else 2 if tmin <= -2 else 1 if tmin <= 0 else 0

    # --- neve: codice + quota gelo bassa ---
    snow_l = 0
    if is_snow_code(code) and freezing is not None and freezing <= 1400:
        snow_l = 3 if rain > 45 else 2 if rain > 15 else 1

    levels = {
        "rain": rain_l,
        "wind": wind_l,
        "thunderstorm": thunder_l,
        "hail": None,
        "heat": heat_l,
        "cold": cold_l,
        "snow": snow_l,
    }
    pm = {
        "rain": rain,
        "wind": gusts,
        "thunderstorm": cape if thunder else showers,
        "hail": None,
        "heat": tmax,
        "cold": tmin,
        "snow": rain,
    }
    null_reason = {"hail": NULL_HAIL}
    return {"levels": levels, "pm": pm, "null_reason": null_reason}


def province_level(r2: int, levels) -> int:
    """``computeZoneRiskSummary``: max fra R2 e le componenti non coperte."""
    top = int(r2)
    for hid, lvl in (levels or {}).items():
        if hid in R2_COVERED or lvl is None:
            continue
        top = max(top, int(lvl))
    return min(4, top)  # fuori da Sviluppo il livello 5 non esiste


# --------------------------------------------------------------------------- #
# Cattura
# --------------------------------------------------------------------------- #
def load_elevations(points_path):
    """id punto -> quota (m). ``selected_point`` e' gia' nel file province."""
    try:
        payload = read_json(points_path)
    except (OSError, ValueError):
        return {}
    out = {}
    for point in payload.get("points") or []:
        try:
            out[int(point.get("id"))] = as_optional_float(point.get("elevation"))
        except (TypeError, ValueError):
            continue
    return out


def capture_rows(meta, provinces_payload, elevations, now, day_indexes,
                 engine_version):
    """Righe (provincia, giorno, pericolo) per i giorni richiesti."""
    rows = []
    hl = local_hhmm(now)
    ts = format_utc(now)
    for prov in provinces_payload.get("provinces") or []:
        sigla = str(prov.get("sigla") or "").strip().upper()
        if not sigla:
            continue
        sp = prov.get("selected_point") or {}
        try:
            point_id = int(sp.get("id"))
        except (TypeError, ValueError):
            point_id = None
        elevation = elevations.get(point_id) if point_id is not None else None
        days = prov.get("days") or []
        for dz in day_indexes:
            if dz < 0 or dz >= len(days):
                continue
            day = days[dz] or {}
            day_key = str(day.get("day") or "")
            if not day_key:
                continue
            r2 = risk_level_r2(day)
            hz = hazard_levels(day)
            pl = province_level(r2, hz["levels"])
            for hid in HAZARDS:
                lvl = hz["levels"].get(hid)
                row = {
                    "ts": ts,
                    "d": day_key,
                    "hl": hl,
                    "dz": dz,
                    "p": sigla,
                    "h": hid,
                    "pv": lvl,
                    "pm": hz["pm"].get(hid),
                    "pl": pl,
                    "r2": r2,
                    "sp": point_id,
                    "el": elevation,
                    "ev": engine_version,
                    "ls": LEVEL_SOURCE,
                    "v": SCHEMA_VERSION,
                }
                if lvl is None:
                    row["nr"] = hz["null_reason"].get(hid, "unknown_null")
                rows.append(row)
    return rows


def load_previous(path):
    """Righe dati precedenti (le righe ``meta`` sono scartate)."""
    rows = []
    corrupt = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    corrupt += 1
                    continue
                if not isinstance(obj, dict) or obj.get("t") == "meta":
                    continue
                rows.append(obj)
    except FileNotFoundError:
        pass
    return rows, corrupt


def row_key(row):
    return (str(row.get("d")), str(row.get("p")), str(row.get("h")))


def delta_rows(new_rows, previous_rows, keep_days, today_local):
    """Filtra i cambi di livello + ruota i giorni troppo vecchi.

    Il confronto e' su ``(pv, nr)`` della stessa (giorno, provincia, pericolo):
    se il livello non cambia, la riga non viene scritta (delta, non polling).
    """
    from datetime import date, timedelta

    cutoff = (date.fromisoformat(today_local)
              - timedelta(days=int(keep_days))).isoformat()
    kept = [r for r in previous_rows if str(r.get("d") or "") >= cutoff]
    dropped = len(previous_rows) - len(kept)

    last = {}
    for row in kept:
        last[row_key(row)] = (row.get("pv"), row.get("nr"))
    out = []
    for row in new_rows:
        sig = (row.get("pv"), row.get("nr"))
        key = row_key(row)
        if key in last and last[key] == sig:
            continue
        out.append(row)
        last[key] = sig
    return kept + out, out, dropped


def _atomic_write(path, text):
    """Scrittura atomica (stesso pattern di severe_predictions._atomic_write)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".riskfc-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run(args) -> dict:
    now = now_utc(args.now)
    meta = read_json(args.meta_file)
    provinces = read_json(args.province_file)
    elevations = load_elevations(args.points_file)
    engine_version = (args.engine_version
                      or engine_version_from_version_file(args.version_file))

    day_indexes = [int(x) for x in str(args.days).split(",") if str(x).strip() != ""]
    if not day_indexes:
        raise ValueError("--days vuoto")

    new_rows = capture_rows(meta, provinces, elevations, now, day_indexes,
                            engine_version)
    previous, corrupt = load_previous(args.out)
    today_local = local_day(now)
    merged, changed, dropped = delta_rows(new_rows, previous, args.keep_days,
                                          today_local)

    meta_row = {
        "t": "meta",
        "ts": format_utc(now),
        "hl": local_hhmm(now),
        "gen": meta.get("generated_at"),
        "day0": meta.get("day0"),
        "days": [d.get("day") for d in (provinces.get("provinces") or [{}])[0].get("days", [])][:3],
        "ev": engine_version,
        "pc": len(provinces.get("provinces") or []),
        "rows": len(new_rows),
        "new_rows": len(changed),
        "rotated": dropped,
        "corrupt": corrupt,
        "keep_days": int(args.keep_days),
        "notes": NOTES,
        "v": SCHEMA_VERSION,
    }
    payload = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")) + "\n"
                      for r in merged)
    payload += json.dumps(meta_row, ensure_ascii=False, sort_keys=True,
                                                                  separators=(",", ":")) + "\n"
    _atomic_write(args.out, payload)

    return {
        "out": args.out,
        "rows": len(new_rows),
        "new_rows": len(changed),
        "total_rows": len(merged),
        "rotated": dropped,
        "corrupt": corrupt,
        "bytes": len(payload.encode("utf-8")),
        "engine_version": engine_version,
        "day0": meta.get("day0"),
        "provinces": meta_row["pc"],
        "days": day_indexes,
        "generated_at": meta.get("generated_at"),
    }


def build_parser():
    root = _HERE.parents[2]
    ap = argparse.ArgumentParser(
        description="JOB A: snapshot dei livelli previsti dalla mappa dei rischi (delta).")
    ap.add_argument("--meta-file", default=str(root / "data/latest/metadata.json"))
    ap.add_argument("--province-file",
                    default=str(root / "data/latest/meteorisk-provinces.json"))
    ap.add_argument("--points-file",
                    default=str(root / "data/latest/meteorisk-points.json"))
    ap.add_argument("--version-file", default=str(root / "VERSION"))
    ap.add_argument("--engine-version", default=None,
                    help="override della versione (default: APP_FILE del VERSION)")
    ap.add_argument("--out", default=str(root / "data/state/risk_forecast_snapshots.jsonl"))
    ap.add_argument("--days", default="0,1,2",
                    help="indici dei giorni da fotografare (default 0,1,2)")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS,
                    help="giorni di validita' da conservare nel registro")
    ap.add_argument("--now", default=None, help="istante ISO-8601 (default: adesso)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[risk_forecast_capture] ERRORE: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
