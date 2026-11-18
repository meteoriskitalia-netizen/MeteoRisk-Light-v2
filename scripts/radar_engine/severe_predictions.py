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

ARCHIVIO ADDITIVO (2026-11-18): le STESSE righe vengono scritte anche in
`data/state/predictions/YYYY/MM/DD.jsonl` (un blob per giorno, giorno UTC del
`ts`). Il registro su main resta a 30 giorni per i consumatori attuali;
l'archivio viene pubblicato dal workflow su un branch cache separato
(`predictions-archive`), con retention 365 giorni. Nessun valore inventato:
le righe dell'archivio sono esattamente quelle del registro.
"""

import datetime as _dt
import json
import os
import tempfile

PREDICTIONS_REL = os.path.join("data", "state", "severe_predictions.jsonl")
RETENTION_DAYS = 30
# Archivio storico (branch cache `predictions-archive`, NON su main).
ARCHIVE_REL = os.path.join("data", "state", "predictions")
ARCHIVE_RETENTION_DAYS = 365


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


def _utc_day(value):
    """Giorno UTC `YYYY-MM-DD` di un ts ISO8601; None se non interpretabile."""
    ts = _parse_iso(value)
    return None if ts is None else ts.strftime("%Y-%m-%d")


def archive_day_path(archive_dir, ts):
    """Path `archive_dir/YYYY/MM/DD.jsonl` per il giorno UTC del ts.

    Ritorna None per un ts non valido: nessun file con giorno inventato."""
    day = _utc_day(ts)
    if day is None:
        return None
    y, m, d = day.split("-")
    return os.path.join(archive_dir, y, m, d + ".jsonl")


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


def append_archive_rows(rows, archive_dir):
    """Scrive le righe nel blob del giorno UTC del loro `ts`.

    Ritorna {"paths": [str], "appended": n, "skipped_invalid_ts": n}.
    Le righe senza `ts` valido NON vengono scritte (nessun giorno inventato);
    vengono solo contate. Riutilizza `append_rows`: stessa serializzazione
    JSON compatta con chiavi ordinate del registro su main."""
    groups = {}
    skipped = 0
    for row in rows or []:
        if not isinstance(row, dict):
            skipped += 1
            continue
        path = archive_day_path(archive_dir, row.get("ts"))
        if path is None:
            skipped += 1
            continue
        groups.setdefault(path, []).append(row)
    written, total = [], 0
    for path in sorted(groups):
        n = append_rows(groups[path], path)
        if n:
            written.append(path)
            total += n
    return {"paths": written, "appended": total,
            "skipped_invalid_ts": skipped}


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


def default_archive_dir(out_dir):
    """Directory dell'archivio: `data/state/predictions`, sorella del registro."""
    return os.path.join(os.path.dirname(default_path(out_dir)),
                        os.path.basename(ARCHIVE_REL))


def record_predictions(bundle, out_dir, version=None, now=None, path=None,
                       retention_days=RETENTION_DAYS, archive_dir=None):
    """Rotazione + append delle predizioni del run corrente (best-effort).

    SCRIVE DUE VOLTE, in modo additivo:
      1) il registro corrente `path` (default `data/state/severe_predictions.jsonl`)
         con rotazione a `retention_days` (30) — comportamento NOTO, invariato;
      2) il blob giornaliero `archive_dir/YYYY/MM/DD.jsonl` — Archivio che il
         workflow pubblica sul branch `predictions-archive` (retention 365 gg).

    Le righe sono ESATTAMENTE le stesse in entrambi i file. L'archivio viene
    scritto PRIMA del registro: se la scrittura del registro fallisce, la riga
    sopravvive almeno nel blob del giorno e il workflow puo' pubblicarla.

    Ritorna {"path", "appended", "rotation", "archive"} oppure None se non c'e'
    nulla da scrivere (nessuna riga e nessun file da ruotare)."""
    target = path or default_path(out_dir)
    arch = archive_dir if archive_dir is not None else os.path.join(
        os.path.dirname(os.path.abspath(target)), os.path.basename(ARCHIVE_REL))
    ts = _now_iso(now)
    records = getattr(bundle, "convective_structures", None) or []
    rows = []
    for rec in records:
        row = prediction_row(rec, rec.get("severe_stack"), ts=ts,
                             version=version)
        if row is not None:
            rows.append(row)
    # 1) archivio additivo (prima: preserva la riga anche se il registro fallisce)
    archive = append_archive_rows(rows, arch) if rows else {
        "paths": [], "appended": 0, "skipped_invalid_ts": 0}
    # 2) registro corrente (comportamento invariato)
    rotation = rotate(target, retention_days=retention_days, now=now)
    appended = append_rows(rows, target) if rows else 0
    if not rows and appended == 0 and rotation["kept"] == 0 \
            and rotation["dropped_old"] == 0 and rotation["dropped_invalid"] == 0 \
            and not os.path.exists(target):
        return None
    return {"path": target, "appended": appended, "rotation": rotation,
            "archive": archive}
