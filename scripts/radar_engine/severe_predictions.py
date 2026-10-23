#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — severe_predictions.py (registro delle PREDIZIONI)

Scrive in append il JSONL `data/state/severe_predictions.jsonl`: una riga per
record emesso, con i campi che serviranno a calcolare POD/FAR/CSI **quando**
esisteranno le etichette (ESWD QC1). Non contiene etichette e non ne inventa:
una cella senza report NON e' "non severa", e' ASSENZA DI EVIDENZA
(docs/METODI_VALIDATI_SEVERO.md, sez. 4.2 punto 6).

Riga:
    {"ts": ISO-UTC, "cell_id": str, "lat": float, "lon": float,
     "soi": int, "vil_density": float|None, "mesh": float|None,
     "ot": bool|None, "environment": {...}|None, "version": str}

Rotazione: le righe piu' vecchie di `retention_days` (default 30) vengono
riscritte fuori (file compatto, scrittura atomica). Le righe corrotte si
scartano e si CONTANO (mai un crash del run: la funzione e' best-effort e
ritorna il conteggio).
"""

import datetime as _dt
import json
import os
import tempfile

PREDICTIONS_REL = os.path.join("data", "state", "severe_predictions.jsonl")
RETENTION_DAYS = 30


def _now_iso(now=None):
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value):
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        return _dt.datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def prediction_row(record, severe=None, ts=None, version=None):
    """Riga JSONL da un record di convective_structure + severe_stack.

    `record` senza posizione -> None (nessuna riga con coordinate inventate)."""
    pos = (record or {}).get("position") or []
    if len(pos) != 2:
        return None
    try:
        lon, lat = float(pos[0]), float(pos[1])
    except (TypeError, ValueError):
        return None
    severe = severe or {}
    hail = severe.get("hail_risk") or {}
    ot = severe.get("ot") or {}

    def _val(field):
        return field.get("value") if isinstance(field, dict) else None

    return {
        "ts": ts or _now_iso(),
        "cell_id": record.get("cell_id") or record.get("structure_id"),
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "soi": record.get("soi"),
        "vil_density": _val(hail.get("vil_density")),
        "mesh": _val(hail.get("mesh")),
        "ot": _val(ot),
        "environment": severe.get("environment"),
        "version": version,
    }


def rotate(path, retention_days=RETENTION_DAYS, now=None):
    """Riscrive `path` senza le righe piu' vecchie di retention_days.

    Ritorna {"kept": n, "dropped_old": n, "dropped_invalid": n}. File assente
    -> zeri (nessuna creazione implicita)."""
    if not os.path.exists(path):
        return {"kept": 0, "dropped_old": 0, "dropped_invalid": 0}
    now_dt = now or _dt.datetime.now(_dt.timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=_dt.timezone.utc)
    cutoff = now_dt - _dt.timedelta(days=int(retention_days))
    kept, dropped_old, dropped_invalid = [], 0, 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    dropped_invalid += 1
                    continue
                ts = _parse_iso(row.get("ts"))
                if ts is None:
                    dropped_invalid += 1
                    continue
                if ts < cutoff:
                    dropped_old += 1
                    continue
                kept.append(line)
    except OSError:
        return {"kept": 0, "dropped_old": 0, "dropped_invalid": 0}
    _atomic_write(path, "".join(l + "\n" for l in kept))
    return {"kept": len(kept), "dropped_old": dropped_old,
            "dropped_invalid": dropped_invalid}


def _atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".pred-", suffix=".tmp")
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


def append_rows(rows, path):
    """Appende le righe (una per linea, JSON compatto). Ritorna n scritte."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return 0
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    payload = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                      for r in rows)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    return len(rows)


def default_path(out_dir):
    """Path del JSONL a partire dalla out_dir del motore (data/radar).

    Regressione: la directory dei derivati e' `data/radar`, lo stato e'
    `data/state` (stessa radice). Se out_dir non e' `.../radar` si usa
    `<out_dir>/state/severe_predictions.jsonl` (nessun path assoluto
    hardcoded)."""
    parent = os.path.dirname(os.path.abspath(out_dir))
    if os.path.basename(os.path.abspath(out_dir)).lower() == "radar":
        return os.path.join(parent, "state",
                            os.path.basename(PREDICTIONS_REL))
    return os.path.join(os.path.abspath(out_dir), "state",
                        os.path.basename(PREDICTIONS_REL))


def record_predictions(bundle, out_dir, version=None, now=None, path=None,
                       retention_days=RETENTION_DAYS):
    """Rotazione + append delle predizioni del run corrente (best-effort).

    Ritorna dict {"path", "appended", "rotation"} oppure None se non c'e'
    nulla da scrivere (nessuna riga e nessun file da ruotare)."""
    target = path or default_path(out_dir)
    ts = _now_iso(now)
    records = getattr(bundle, "convective_structures", None) or []
    rows = []
    for rec in records:
        row = prediction_row(rec, rec.get("severe_stack"), ts=ts,
                             version=version)
        if row is not None:
            rows.append(row)
    rotation = rotate(target, retention_days=retention_days, now=now)
    appended = append_rows(rows, target) if rows else 0
    if not rows and appended == 0 and rotation["kept"] == 0 \
            and rotation["dropped_old"] == 0 and rotation["dropped_invalid"] == 0 \
            and not os.path.exists(target):
        return None
    return {"path": target, "appended": appended, "rotation": rotation}
