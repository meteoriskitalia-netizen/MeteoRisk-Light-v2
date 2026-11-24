#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — nowcast_archive.py (novembre 2026)

ARCHIVIO LEGGERO DEI NOWCAST EMESSI.

Ogni run di `radar-engine` scrive `data/radar/nowcast.json` (schema
`meteorisk.nowcast_field/1`) e **12 WebP** (un frame ogni 5 minuti da +5 a
+60; i tre lead 15/30/60 sono gli ufficiali di verifica). Il nowcast su `main`
viene
sovrascritto al run successivo: senza una copia non e' possibile verificare a
posteriori se il nowcast emesso ieri sera aveva ragione. Questo modulo scrive
UNA COPIA COMPATTA per slot:

    nowcast_archive/YYYY/MM/DD/HHMM.json      (ora UTC dello slot)

nella directory passata con `--archive-dir`. La directory NON va in
`data/radar/` (finirebbe nell'artefatto Pages e gonfierebbe `main`): il
workflow la scrive in `$RUNNER_TEMP` e la pubblica su un branch ORPHAN
dedicato (`nowcast-archive`) con commit senza parent e `--force-with-lease`,
come `radar-history`, `predictions-archive` e `srt1-province-archive`.

Cosa contiene (e cosa NON contiene)
-----------------------------------
Contiene SOLO i campi necessari alla verifica: slot, lead, valid_time,
disponibilita'/motivo, moto (tipo, algoritmo, statistiche), cap di velocita',
riferimenti ai WebP (nome, byte, sha256), griglia e statistiche per lead.
NON copia i tre WebP e NON copia il campo float (sarebbero ~20 MB/run): la
verifica RICALCOLA il nowcast dallo stesso slot con lo stesso codice
deterministico (`nowcast_field.compute`) e confronta il `source_slot`
archiviato con quello ricalcolato. Se i due non coincidono la riga e'
`not_verifiable` con motivo, non un punteggio inventato.

Retention dichiarata: `RETENTION_DAYS = 7` giorni. Misura: con 12 lead un
record e' ~6,5 kB (misurato 6.456 B su un nowcast reale); 288 slot/giorno
(cadenza 5 min) -> ~1,9 MB/giorno, ~13 MB vivi.

Il record viene scritto anche quando il nowcast e' `available: false`
(`sri_unavailable`, `no_rain_in_latest_frame`, ...): serve a misurare la
disponibilita' del layer, non solo i casi con pioggia. In quel caso lo slot
del nome file e' `generated_at` (non esiste un `source_slot`) e il record lo
dichiara in `slot_basis`.

Il modulo fa SOLO filesystem (scrive, legge, pota). Non esegue git.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import sys
import tempfile

SCHEMA = "meteorisk.nowcast_archive/1"
ARCHIVE_DIRNAME = "nowcast_archive"
RETENTION_DAYS = 7
# Lead ufficiali di verifica. Duplicato di proposito: questo modulo e'
# eseguibile come script (`python scripts/radar_engine/nowcast_archive.py`) e
# non deve importare numpy/scipy solo per una costante; il test
# test_nowcast_archive_verify verifica che coincida con
# `nowcast_field.OFFICIAL_LEADS`.
OFFICIAL_LEADS = (15, 30, 60)

_ISO = "%Y-%m-%dT%H:%M:%SZ"
_UTC = _dt.timezone.utc


# ---------------------------------------------------------------------------
# utilita'
# ---------------------------------------------------------------------------
def iso_from_ms(ms):
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0, _UTC).strftime(_ISO)


def parse_iso_ms(value):
    """ISO-8601 -> epoch ms UTC. None/vuoto -> None. Solleva su formato rotto."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    txt = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    dt = _dt.datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return int(dt.timestamp() * 1000)


def _atomic_write_text(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    json.loads(text)                      # fallisce PRIMA di toccare il file
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".nowcast-archive-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
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


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_path(root, slot_iso):
    """`<root>/YYYY/MM/DD/HHMM.json` dall'istante ISO dello slot."""
    dt = _dt.datetime.fromisoformat(str(slot_iso).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    dt = dt.astimezone(_UTC)
    return os.path.join(root, "%04d" % dt.year, "%02d" % dt.month,
                        "%02d" % dt.day, "%s.json" % dt.strftime("%H%M"))


def _slot_basis(payload):
    """(iso, basis) da usare per il nome file."""
    if payload.get("source_slot"):
        return payload["source_slot"], "source_slot"
    return payload.get("generated_at"), "generated_at"


def _images_ref(payload, images_dir):
    """Riferimento ai WebP: nome, byte, sha256. NON copia i file."""
    out = {}
    names = set()
    declared = ((payload.get("output_bytes") or {}).get("images") or {})
    names.update(declared)
    for lead in payload.get("leads") or []:
        if lead.get("image"):
            names.add(lead["image"])
    for name in sorted(n for n in names if n):
        path = os.path.join(images_dir, name) if images_dir else None
        entry = {"name": name, "bytes": None, "sha256": None,
                 "present": False}
        if declared.get(name) is not None:
            entry["bytes"] = int(declared[name])
        if path and os.path.isfile(path):
            entry["present"] = True
            entry["bytes"] = int(os.path.getsize(path))
            entry["sha256"] = _sha256_file(path)
        out[name] = entry
    return out


def compact(payload, images_dir=None):
    """`nowcast.json` -> record leggero (schema `nowcast_archive/1`)."""
    slot_iso, basis = _slot_basis(payload)
    leads = []
    for lead in payload.get("leads") or []:
        lead_min = lead.get("lead_min")
        leads.append({
            "lead_min": lead_min,
            "valid_time": lead.get("valid_time"),
            "image": lead.get("image"),
            # ufficiale = entra nella verifica contro la persistenza
            "official": bool(lead.get(
                "official", lead_min in OFFICIAL_LEADS)),
            "stats": lead.get("stats"),
        })
    grid = payload.get("grid") or {}
    motion_stats = payload.get("motion_stats") or {}
    return {
        "schema": SCHEMA,
        "slot": slot_iso,
        "slot_basis": basis,
        "source_slot": payload.get("source_slot"),
        "generated_at": payload.get("generated_at"),
        "available": bool(payload.get("available")),
        "null_reason": payload.get("null_reason"),
        "source": payload.get("source"),
        "unit": payload.get("unit"),
        "motion": payload.get("motion"),
        "motion_algorithm": payload.get("motion_algorithm"),
        "motion_pairs": payload.get("motion_pairs"),
        "motion_stats": {
            "median_speed_kmh": motion_stats.get("median_speed_kmh"),
            "p95_speed_kmh": motion_stats.get("p95_speed_kmh"),
            "pixels_over_cap": motion_stats.get("pixels_over_cap"),
        },
        "speed_cap_kmh": payload.get("speed_cap_kmh"),
        "speed_cap_mode": payload.get("speed_cap_mode"),
        "vectors_zeroed_by_cap": payload.get("vectors_zeroed_by_cap"),
        "vectors_over_cap_total": payload.get("vectors_over_cap_total"),
        "vectors_replaced_by_cap": payload.get("vectors_replaced_by_cap"),
        "vectors_unfilled_by_cap": payload.get("vectors_unfilled_by_cap"),
        "motion_filled_pixels": payload.get("motion_filled_pixels"),
        "motion_unfilled_pixels": payload.get("motion_unfilled_pixels"),
        "motion_null_reason": payload.get("motion_null_reason"),
        "frames_used": payload.get("frames_used"),
        "frames_origin": payload.get("frames_origin"),
        "frames_slots": payload.get("frames_slots"),
        "leads": leads,
        "grid": {k: grid.get(k) for k in
                 ("crs", "bounds", "width", "height", "resolution_m")},
        "images": _images_ref(payload, images_dir),
        "limits": payload.get("limits"),
    }


def write_record(root, record):
    """Scrive un record gia' compattato. Ritorna (path, bytes)."""
    slot = record.get("slot")
    if not slot:
        raise ValueError("record_without_slot")
    path = archive_path(root, slot)
    text = json.dumps(record, ensure_ascii=False, allow_nan=False,
                      sort_keys=False, separators=(",", ":"))
    _atomic_write_text(path, text)
    return path, os.path.getsize(path)


def archive_nowcast(nowcast_json, root):
    """Legge `data/radar/nowcast.json` e scrive la copia leggera.

    Ritorna (path, bytes, record). Idempotente: lo stesso slot sovrascrive lo
    stesso file (stesso contenuto deterministico a parita' di codice/dato).
    """
    with open(nowcast_json, encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError("nowcast_json_not_object")
    record = compact(payload, images_dir=os.path.dirname(nowcast_json))
    path, size = write_record(root, record)
    return path, size, record


def read_record(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else None


def iter_records(root):
    """Tutti i record sotto `<root>/YYYY/MM/DD/HHMM.json` (ordine di path)."""
    if not os.path.isdir(root):
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                rec = read_record(path)
            except (OSError, ValueError):
                continue
            if rec:
                rec["_path"] = path
                yield rec


def record_day(record):
    """Data UTC (date) del record: slot, altrimenti generated_at."""
    for key in ("slot", "source_slot", "generated_at"):
        value = record.get(key)
        if not value:
            continue
        try:
            dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        return dt.astimezone(_UTC).date()
    return None


def prune(root, keep_days=RETENTION_DAYS, now=None):
    """Rimuove i file con data < oggi - keep_days. Ritorna (rimossi, tenuti).

    La data viene dal CONTENUTO (slot/generated_at), non dal path: un path
    sbagliato non deve far cancellare un record valido.
    """
    if now is None:
        now_dt = _dt.datetime.now(_UTC)
    elif isinstance(now, str):
        now_dt = _dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=_UTC)
    else:
        now_dt = now
    cutoff = (now_dt.astimezone(_UTC).date()
              - _dt.timedelta(days=int(keep_days)))
    removed = kept = 0
    for rec in list(iter_records(root)):
        day = record_day(rec)
        path = rec.get("_path")
        if day is None or path is None:
            kept += 1
            continue
        if day < cutoff:
            try:
                os.unlink(path)
                removed += 1
            except OSError:
                kept += 1
        else:
            kept += 1
    # elimina le directory vuote (non necessarie, ma tiene il branch pulito)
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath == root:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass
    return removed, kept


def copy_tree(src, dst):
    """Copia i JSON da `src` a `dst` (sovrascrive a parita' di path)."""
    copied = 0
    if not os.path.isdir(src):
        return copied
    for rec in iter_records(src):
        rel = os.path.relpath(rec["_path"], src)
        target = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(rec["_path"], target)
        copied += 1
    return copied


README = """# nowcast-archive — archivio leggero dei nowcast emessi

Un file JSON per slot (UTC): `nowcast_archive/YYYY/MM/DD/HHMM.json`.
Contiene solo i campi necessari alla verifica (slot, lead, valid_time,
disponibilita', moto, cap, riferimento ai WebP, statistiche e griglia).
**Non** contiene i WebP ne' il campo float: la verifica ricalcola il nowcast
dallo stesso slot con `scripts/radar_engine/nowcast_field.py`.

- Schema: `meteorisk.nowcast_archive/1`
- Lead: 12 frame (5..60 min); `official: true` solo su 15/30/60, gli unici
  che la verifica confronta con la persistenza. Gli altri sono per il player.
- Retention: 7 giorni (potatura a ogni pubblicazione)
- Scritto da: job `radar-engine` (step "Archivia nowcast emesso")
- Pubblicato su: branch ORPHAN `nowcast-archive` (commit senza parent,
  `--force-with-lease`); mai su `main`, mai nell'artefatto Pages.
- Verifica: `scripts/radar_engine/nowcast_verify.py` ->
  `data/radar/nowcast_verification.json` (schema
  `meteorisk.nowcast_verification/2`).

Fonte del nowcast: prodotto DPC SRI (Surface Rainfall Intensity, mm/h).
Opera derivata Radar-DPC: CC BY-SA 4.0 (vedi `LICENCE.md`).
"""

LICENCE = """# Licenza e attribuzione

I dati di partenza sono il prodotto **SRI (Surface Rainfall Intensity)** del
Radar-DPC, pubblicato dalla Protezione Civile italiana con licenza
**CC BY-SA 4.0**. Questo archivio contiene solo metadati derivati (slot,
statistiche, riferimenti alle immagini): resta un'opera derivata e va
attribuito alla fonte e condiviso con la stessa licenza.

Fonte: <https://radar-api.protezionecivile.it> — Radar-DPC,
Dipartimento della Protezione Civile.
"""


def ensure_docs(root):
    """Scrive README.md e LICENCE.md alla radice del branch (idempotente)."""
    os.makedirs(root, exist_ok=True)
    for name, text in (("README.md", README), ("LICENCE.md", LICENCE)):
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    continue
        except OSError:
            pass
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def archive_bytes(root):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".json"):
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Archivio leggero dei nowcast emessi (filesystem, no git)")
    ap.add_argument("--nowcast-json", default="data/radar/nowcast.json")
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    ap.add_argument("--now", default=None,
                    help="istante UTC per la potatura (default: adesso)")
    ap.add_argument("--no-prune", action="store_true")
    args = ap.parse_args(argv)

    try:
        path, size, rec = archive_nowcast(args.nowcast_json, args.archive_dir)
    except (OSError, ValueError) as exc:
        print("[WARN] nowcast non archiviato (%s:%s)" % (
            exc.__class__.__name__, exc))
        return 0
    print("[INFO] nowcast archiviato: %s (%d B, available=%s, slot=%s)" % (
        path, size, rec.get("available"), rec.get("slot")))
    if not args.no_prune:
        removed, kept = prune(args.archive_dir,
                              keep_days=args.retention_days, now=args.now)
        print("[INFO] retention %d gg: rimossi %d, tenuti %d (%d B JSON)" % (
            args.retention_days, removed, kept, archive_bytes(args.archive_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
