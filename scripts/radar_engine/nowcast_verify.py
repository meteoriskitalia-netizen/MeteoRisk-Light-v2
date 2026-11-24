#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — nowcast_verify.py (novembre 2026)

VERIFICA AUTOMATICA A POSTERIORI DEL NOWCAST EMESSO.

Quando lo slot osservato corrispondente a un `valid_time` archiviato e'
disponibile, questo modulo:

  1) legge i record leggeri dell'archivio (`nowcast_archive/YYYY/MM/DD/HHMM.json`,
     scritti da `nowcast_archive.py`);
  2) ricalcola il nowcast dallo STESSO slot con lo STESSO codice del run
     (`nowcast_field.compute`): la copia archiviata non contiene il campo
     float, quindi il confronto e' possibile solo ricalcolando. Il
     `source_slot` ricalcolato DEVE coincidere con quello archiviato,
     altrimenti la riga e' `not_verifiable` con motivo
     `recomputed_source_slot_mismatch` (nessun punteggio su un istante
     diverso);
  3) scarica l'SRI OSSERVATO allo stesso `valid_time` e calcola, per i SOLI
     lead UFFICIALI (15/30/60 min) e per ogni soglia GIA' DICHIARATA (0.5 /
     2.0 / 5.0 mm/h), CSI/POD/FAR del nowcast e della PERSISTENZA (campo fermo
     allo slot sorgente). Gli altri 9 frame prodotti per il player (5, 10, 20,
     25, 35, 40, 45, 50, 55 min) NON entrano nella verifica: sono trasporto
     cumulativo per la fluidita', non un secondo prodotto da validare.

La serie temporale viene accumulata in `data/radar/nowcast_verification.json`
mantenendo INTATTO il riassunto esistente (il file schema 1 con i 49 casi):
lo schema diventa `meteorisk.nowcast_verification/2` e la chiave `series`
contiene una riga per (source_slot, lead, soglia). Un lettore dello schema 1
continua a leggere le stesse chiavi.

Semantica degli stati (nessun valore inventato)
-----------------------------------------------
- `verified`: osservato presente, punteggi calcolati (possono essere `null`
  se il denominatore e' 0, es. nessun pixel previsto/osservato sopra soglia);
- `not_informative`: osservato presente ma con meno di `--min-rain-pixels`
  pixel >= 0.5 mm/h: i punteggi sarebbero tutti "correct negative";
- `not_verifiable`: osservato assente/non ancora pubblicato, oppure
  ricalcolo non riuscito/non coerente. Porta SEMPRE `null_reason`.

Attesa dichiarata: un valid_time non ancora osservabile non produce una riga
definitiva finche' non scade `--grace-min` (default 90 minuti: il ritardo di
pubblicazione DPC misurato e' ~7 minuti; 90 e' un margine dichiarato, non una
misura). Fino a quel momento la coppia resta `pending` e viene ritentata al
run successivo. Dopo la grazia, l'assenza produce una riga `not_verifiable`
con motivo `observed_missing` (o `observed_slot_mismatch`).

Il modulo NON solleva nel percorso operativo: un guasto di rete o un record
corrotto produce `[WARN]` e il run radar prosegue (il workflow chiama questo
script con `continue-on-error`). Nessun file di `data/radar/` diverso da
`nowcast_verification.json` viene toccato.
"""

import argparse
import copy
import datetime as _dt
import json
import os
import sys
import tempfile
import time

import numpy as np

# Bootstrap identico a main.py: eseguibile sia come script sia come modulo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from radar_engine import clock as clock_mod            # noqa: E402
from radar_engine import fetch as fetch_mod            # noqa: E402
from radar_engine import nowcast_archive as na         # noqa: E402
from radar_engine import nowcast_field as nf           # noqa: E402

SCHEMA = "meteorisk.nowcast_verification/2"
SCHEMA_VERSION = 2
PREVIOUS_SCHEMA = "meteorisk.nowcast_verification/1"
SERIES_SCHEMA = "meteorisk.nowcast_verification_series/1"

SERIES_RETENTION_DAYS = 7
GRACE_MIN = 90
MAX_LOOKBACK_HOURS = 4
MAX_SLOTS_PER_RUN = 12
MIN_OBS_RAIN_PIXELS = 100          # come il default di nowcast_replay.py

_ISO = "%Y-%m-%dT%H:%M:%SZ"
_UTC = _dt.timezone.utc
_STEP_MS = nf.STEP_MIN * 60 * 1000


def _now_ms(now=None):
    if now is None:
        return int(time.time() * 1000)
    if isinstance(now, (int, float)):
        return int(now)
    ms = na.parse_iso_ms(now)
    if ms is None:
        raise ValueError("invalid_now:%r" % (now,))
    return int(ms)


def _iso(ms):
    return clock_mod.iso_from_ms(ms)


def _atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    json.loads(text)                  # validazione PRIMA della sostituzione
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".nowcast-verify-", suffix=".tmp")
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


def load_document(path):
    """Legge `nowcast_verification.json`; file assente -> {} (dichiarato)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def row_key(row):
    return (str(row.get("source_slot")),
            int(row.get("lead_min")),
            round(float(row.get("threshold_mmh")), 3),
            str(row.get("variant") or "no_orog"))


def official_leads(record):
    """Lead del record che entrano nella verifica ufficiale (15/30/60).

    Un record vecchio (schema 1, senza flag `official`) viene filtrato con la
    costante di `nowcast_field`: nessun lead viene inventato."""
    out = []
    for lead in record.get("leads") or []:
        try:
            lead_min = int(lead.get("lead_min"))
        except (TypeError, ValueError):
            continue
        flag = lead.get("official")
        if flag is None:
            flag = lead_min in nf.OFFICIAL_LEADS
        if flag and lead_min in nf.OFFICIAL_LEADS:
            out.append(lead)
    return out


def existing_keys(doc):
    out = set()
    for row in ((doc.get("series") or {}).get("rows") or []):
        try:
            out.add(row_key(row))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# accesso agli SRI (finestra 6 frame + singolo osservato)
# ---------------------------------------------------------------------------
class SriFetcher(object):
    """Cache per-run degli SRI. Conta i download (misura nei report)."""

    def __init__(self, config, fetch_fn=None, cap=80):
        self.config = config
        self.fetch_fn = fetch_fn or fetch_mod.fetch_frames
        self.cap = int(cap)
        self.cache = {}
        self.downloads = 0
        self.failures = 0

    def _store(self, frames):
        for rd in frames or []:
            if rd is not None and rd.time_ms is not None:
                self.cache[int(rd.time_ms)] = rd
        # cache semplice: gli slot della finestra sono al massimo ~80
        if len(self.cache) > self.cap:
            for key in sorted(self.cache)[:len(self.cache) - self.cap]:
                self.cache.pop(key, None)

    def get(self, ts):
        ts = int(ts)
        if ts in self.cache:
            return self.cache[ts]
        try:
            frames, _warn, _latest = self.fetch_fn(
                self.config, product="SRI", max_frames=1, anchor_ms=ts)
        except Exception:
            self.failures += 1
            return None
        self.downloads += len(frames or [])
        self._store(frames)
        rd = self.cache.get(ts)
        # uno slot diverso da quello richiesto NON e' l'osservato
        return rd if (rd is not None and int(rd.time_ms) == ts) else None

    def window(self, end_ms, n=6):
        end_ms = int(end_ms)
        expected = [end_ms - k * _STEP_MS for k in range(n - 1, -1, -1)]
        if all(ts in self.cache for ts in expected):
            return [self.cache[ts] for ts in expected]
        try:
            frames, _warn, _latest = self.fetch_fn(
                self.config, product="SRI", max_frames=n, anchor_ms=end_ms)
        except Exception:
            self.failures += 1
            return []
        self.downloads += len(frames or [])
        self._store(frames)
        return [self.cache[ts] for ts in expected if ts in self.cache]


class _Bundle(object):
    """Bundle minimo per `nowcast_field.compute` (solo dpc_bundle)."""

    def __init__(self, rasters):
        self.dpc_bundle = {"entries": {
            int(rd.time_ms): {"sri": {"raster": rd}} for rd in rasters}}
        self.warnings = []
        self._nowcast_verification = {}


def _default_compute(bundle, config, clk, orographic=None):
    return nf.compute(bundle, config, clk, list(bundle.warnings), out_dir=None,
                      orographic=orographic)


# ---------------------------------------------------------------------------
# coordinate e punteggi
# ---------------------------------------------------------------------------
def _lonlat(rd, row, col):
    try:
        lon, lat = rd.pixel_to_lonlat(int(row), int(col))
    except (AttributeError, TypeError, ValueError):
        return None
    if not (np.isfinite(lon) and np.isfinite(lat)):
        return None
    return {"lat": round(float(lat), 5), "lon": round(float(lon), 5)}


def peak_observed(obs, dom, rd):
    masked = np.where(dom, obs, -np.inf)
    if not np.isfinite(masked).any():
        return None
    idx = int(np.argmax(masked))
    if not np.isfinite(masked.ravel()[idx]):
        return None
    row, col = np.unravel_index(idx, masked.shape)
    pos = _lonlat(rd, row, col)
    if pos is None:
        return None
    return {"mmh": round(float(obs[row, col]), 3), "lat": pos["lat"],
            "lon": pos["lon"]}


def _centroid(mask, rd):
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return None
    return _lonlat(rd, int(round(float(rows.mean()))),
                   int(round(float(cols.mean()))))


def scores_row(source_slot, lead, valid_iso, threshold, pred, pers, obs,
               domain, rd, min_rain_pixels=MIN_OBS_RAIN_PIXELS,
               variant="no_orog"):
    """Una riga (slot, lead, soglia, variante). Nessun valore se il dato manca."""
    ok = (np.isfinite(pred) & np.isfinite(obs)
          & np.asarray(domain, dtype=bool))
    n_obs_rain = int(np.sum(obs[ok] >= 0.5))
    base = {
        "source_slot": source_slot,
        "lead_min": int(lead),
        "valid_time": valid_iso,
        "threshold_mmh": float(threshold),
        "variant": str(variant),
        "n_px_observed": n_obs_rain,
        "domain_pixels": int(ok.sum()),
        "n_px_observed_ge_threshold": int(np.sum(obs[ok] >= threshold)),
        "n_px_predicted_ge_threshold": int(np.sum(pred[ok] >= threshold)),
    }
    peak = peak_observed(obs, ok, rd)
    if n_obs_rain < int(min_rain_pixels):
        base.update({
            "status": "not_informative",
            "null_reason": "no_observed_rain_below_min_pixels",
            "csi_nowcast": None, "pod": None, "far": None,
            "csi_persistenza": None,
            "pod_persistenza": None, "far_persistenza": None,
            "hits": None, "misses": None, "false_alarms": None,
            "correct_negatives": None,
            "hits_persistenza": None, "misses_persistenza": None,
            "false_alarms_persistenza": None,
            "correct_negatives_persistenza": None,
            "beats_persistence": None,
            "peak_observed": peak,
            "error_location": None,
        })
        return base

    cn = nf.contingency(pred, obs, ok, threshold)
    cp = nf.contingency(pers, obs, ok, threshold)
    sn, sp = nf.scores(cn), nf.scores(cp)
    pm = (pred >= threshold) & ok
    om = (obs >= threshold) & ok
    beats = None
    if sn["csi"] is not None and sp["csi"] is not None:
        beats = bool(sn["csi"] > sp["csi"])
    base.update({
        "status": "verified",
        "null_reason": None,
        "csi_nowcast": sn["csi"], "pod": sn["pod"], "far": sn["far"],
        "csi_persistenza": sp["csi"],
        "pod_persistenza": sp["pod"], "far_persistenza": sp["far"],
        "hits": sn["hits"], "misses": sn["misses"],
        "false_alarms": sn["false_alarms"],
        "correct_negatives": sn["correct_negatives"],
        "hits_persistenza": sp["hits"], "misses_persistenza": sp["misses"],
        "false_alarms_persistenza": sp["false_alarms"],
        "correct_negatives_persistenza": sp["correct_negatives"],
        "beats_persistence": beats,
        "peak_observed": peak,
        "error_location": {
            "false_alarm_centroid": _centroid(pm & (~om), rd),
            "miss_centroid": _centroid(om & (~pm), rd),
        },
    })
    return base


def not_verifiable_rows(source_slot, lead, valid_iso, reason,
                        n_px_observed=None):
    """Una riga per soglia con stato `not_verifiable` e motivo esplicito."""
    rows = []
    for thr in nf.VERIFY_THRESHOLDS_MMH:
        rows.append({
            "source_slot": source_slot,
            "lead_min": int(lead),
            "valid_time": valid_iso,
            "threshold_mmh": float(thr),
            "variant": None,
            "status": "not_verifiable",
            "null_reason": reason,
            "n_px_observed": n_px_observed,
            "domain_pixels": None,
            "n_px_observed_ge_threshold": None,
            "n_px_predicted_ge_threshold": None,
            "csi_nowcast": None, "pod": None, "far": None,
            "csi_persistenza": None,
            "pod_persistenza": None, "far_persistenza": None,
            "hits": None, "misses": None, "false_alarms": None,
            "correct_negatives": None,
            "hits_persistenza": None, "misses_persistenza": None,
            "false_alarms_persistenza": None,
            "correct_negatives_persistenza": None,
            "beats_persistence": None,
            "peak_observed": None,
            "error_location": None,
        })
    return rows


def _pooled(rows):
    """Somme di contingenza e CSI pooled (CSI = H/(H+M+F) sui totali)."""
    h = sum(int(r.get("hits") or 0) for r in rows)
    m = sum(int(r.get("misses") or 0) for r in rows)
    f = sum(int(r.get("false_alarms") or 0) for r in rows)
    cn = sum(int(r.get("correct_negatives") or 0) for r in rows)
    hp = sum(int(r.get("hits_persistenza") or 0) for r in rows)
    mp = sum(int(r.get("misses_persistenza") or 0) for r in rows)
    fp = sum(int(r.get("false_alarms_persistenza") or 0) for r in rows)
    cnp = sum(int(r.get("correct_negatives_persistenza") or 0) for r in rows)

    def csi(a, b, c):
        den = a + b + c
        return round(a / den, 4) if den else None

    return {
        "n_rows": len(rows),
        "hits": h, "misses": m, "false_alarms": f, "correct_negatives": cn,
        "csi_nowcast": csi(h, m, f),
        "pod": round(h / (h + m), 4) if (h + m) else None,
        "far": round(f / (h + f), 4) if (h + f) else None,
        "csi_persistenza": csi(hp, mp, fp),
        "pod_persistenza": round(hp / (hp + mp), 4) if (hp + mp) else None,
        "far_persistenza": round(fp / (hp + fp), 4) if (hp + fp) else None,
    }


def orography_ab(rows, min_cases=5):
    """A/B orografia: stessi slot, varianti `orog` e `no_orog` + persistenza.

    Confronto APPAIATO per (slot, lead, soglia): entrano solo le celle in cui
    ENTRAMBE le varianti hanno una riga `verified`, cosi' la differenza non e'
    un effetto di campione. Il verdetto usa il CSI pooled sui totali di
    contingenza (H/(H+M+F)), non la media di CSI con denominatori diversi.
    """
    verified = [r for r in rows if r.get("status") == "verified"]
    by = {}
    for r in verified:
        v = str(r.get("variant") or "no_orog")
        key = (int(r.get("lead_min")), round(float(r.get("threshold_mmh")), 3),
               str(r.get("source_slot")))
        by.setdefault(key, {})[v] = r
    paired = {k: d for k, d in by.items()
              if "orog" in d and "no_orog" in d}
    out = {
        "schema": "meteorisk.nowcast_orography_ab/1",
        "variant_key": "variant",
        "variants": ["orog", "no_orog", "persistenza"],
        "min_cases_per_verdict": int(min_cases),
        "paired_cases": len(paired),
        "cells": [],
        "overall": {},
        "null_reason": None,
    }
    if not paired:
        out["null_reason"] = "no_paired_verified_cases"
        out["beats_persistence_no_orog"] = None
        out["beats_persistence_orog"] = None
        out["orog_beats_no_orog"] = None
        return out

    orog_rows = [d["orog"] for d in paired.values()]
    no_rows = [d["no_orog"] for d in paired.values()]
    wins = {"orog": 0, "no_orog": 0, "tie": 0}
    lead_thr = {}
    for d in paired.values():
        o = d["orog"].get("csi_nowcast")
        n = d["no_orog"].get("csi_nowcast")
        if o is None or n is None:
            continue
        if o > n:
            wins["orog"] += 1
        elif n > o:
            wins["no_orog"] += 1
        else:
            wins["tie"] += 1
        key = (int(d["orog"]["lead_min"]),
               round(float(d["orog"]["threshold_mmh"]), 3))
        lead_thr.setdefault(key, {"orog": [], "no_orog": []})
        lead_thr[key]["orog"].append(d["orog"])
        lead_thr[key]["no_orog"].append(d["no_orog"])

    for (lead, thr) in sorted(lead_thr):
        cell = lead_thr[(lead, thr)]
        po = _pooled(cell["orog"])
        pn = _pooled(cell["no_orog"])
        out["cells"].append({
            "lead_min": lead, "threshold_mmh": thr,
            "n_cases": po["n_rows"],
            "csi_orog": po["csi_nowcast"],
            "csi_no_orog": pn["csi_nowcast"],
            "csi_persistenza": po["csi_persistenza"],
            "pod_orog": po["pod"], "pod_no_orog": pn["pod"],
            "far_orog": po["far"], "far_no_orog": pn["far"],
            "delta_csi_orog_minus_no_orog": (
                round(po["csi_nowcast"] - pn["csi_nowcast"], 4)
                if (po["csi_nowcast"] is not None
                    and pn["csi_nowcast"] is not None) else None),
        })

    po = _pooled(orog_rows)
    pn = _pooled(no_rows)
    out["overall"] = {"orog": po, "no_orog": pn}
    out["case_wins"] = wins
    enough = len(paired) >= int(min_cases)

    def _better(a, b):
        if not enough or a is None or b is None:
            return None
        return bool(a > b)

    out["beats_persistence_orog"] = _better(po["csi_nowcast"],
                                            po["csi_persistenza"])
    out["beats_persistence_no_orog"] = _better(pn["csi_nowcast"],
                                               pn["csi_persistenza"])
    out["orog_beats_no_orog"] = _better(po["csi_nowcast"], pn["csi_nowcast"])
    if not enough:
        out["null_reason"] = "paired_cases_below_%d" % int(min_cases)
    out["decision"] = (
        "l'orografia MIGLIORA il nowcast" if out["orog_beats_no_orog"] is True
        else "l'orografia PEGGIORA il nowcast" if out["orog_beats_no_orog"] is False
        else "non conclusivo (casi insufficienti o pari)")
    return out


# ---------------------------------------------------------------------------
# fusione nella serie (schema 2, riassunto intatto)
# ---------------------------------------------------------------------------
def _parse_row_valid(row):
    try:
        return na.parse_iso_ms(row.get("valid_time"))
    except (TypeError, ValueError):
        return None


def merge_series(doc, new_rows, now_ms, retention_days=SERIES_RETENTION_DAYS,
                 min_rain_pixels=MIN_OBS_RAIN_PIXELS):
    """Fonde le righe nella serie senza toccare il riassunto dello schema 1."""
    original = copy.deepcopy(doc or {})
    merged = {}
    for row in ((original.get("series") or {}).get("rows") or []):
        try:
            merged[row_key(row)] = row
        except (TypeError, ValueError):
            continue
    for row in new_rows or []:
        merged[row_key(row)] = row

    cutoff = int(now_ms) - int(retention_days) * 86400000
    kept = []
    for key in sorted(merged, key=lambda k: (
            _parse_row_valid(merged[k]) or 0, k[1], k[2])):
        row = merged[key]
        valid_ms = _parse_row_valid(row)
        if valid_ms is not None and valid_ms < cutoff:
            continue
        kept.append(row)

    out = original
    had_summary = "cases_total" in original
    out["schema"] = SCHEMA
    out["schema_version"] = SCHEMA_VERSION
    out["compatibility"] = {
        "previous_schema": PREVIOUS_SCHEMA,
        "summary_keys_preserved": had_summary,
        "summary_note": (
            "Le chiavi del riassunto schema 1 (status, beats_persistence, "
            "cases_total, leads, decision_rule, ...) NON vengono ricalcolate "
            "ne' modificate da questo modulo: la serie operativa si aggiunge "
            "sotto `series`."),
        "series_key": "series",
        "unknown_keys_ignored_by_v1_readers": True,
    }
    old_series = original.get("series") if isinstance(
        original.get("series"), dict) else {}
    series = dict(old_series)
    series.update({
        "schema": SERIES_SCHEMA,
        "generated_at": _iso(now_ms),
        "retention_days": int(retention_days),
        "min_rain_pixels_per_case_lead": int(min_rain_pixels),
        "thresholds_mmh": list(nf.VERIFY_THRESHOLDS_MMH),
        "leads_min": list(nf.OFFICIAL_LEADS),
        "counts": {
            "rows": len(kept),
            "slots": len({str(r.get("source_slot")) for r in kept}),
            "verified": sum(1 for r in kept if r.get("status") == "verified"),
            "not_informative": sum(
                1 for r in kept if r.get("status") == "not_informative"),
            "not_verifiable": sum(
                1 for r in kept if r.get("status") == "not_verifiable"),
        },
        "rows": kept,
    })
    out["series"] = series
    out["orography_ab"] = orography_ab(kept)
    return out


# ---------------------------------------------------------------------------
# verifica
# ---------------------------------------------------------------------------
def verify_pending(archive_root, verification_path, config, now=None,
                   fetch_fn=None, compute_fn=None,
                   grace_min=GRACE_MIN,
                   max_lookback_hours=MAX_LOOKBACK_HOURS,
                   max_slots=MAX_SLOTS_PER_RUN,
                   min_rain_pixels=MIN_OBS_RAIN_PIXELS,
                   retention_days=SERIES_RETENTION_DAYS,
                   from_iso=None, to_iso=None, force=False):
    """Verifica le coppie (slot, lead) dovute e non ancora nella serie.

    Ritorna un dizionario di misure. Non solleva per dati mancanti; un record
    corrotto viene contato e saltato.
    """
    t0 = time.time()
    now_ms = _now_ms(now)
    doc = load_document(verification_path)
    done = set() if force else existing_keys(doc)
    done_pairs = {(key[0], key[1]) for key in done}
    fetcher = SriFetcher(config, fetch_fn=fetch_fn)
    compute_fn = compute_fn or _default_compute

    start_ms = now_ms - int(max_lookback_hours) * 3600000
    end_ms = now_ms
    if from_iso:
        start_ms = na.parse_iso_ms(from_iso)
    if to_iso:
        end_ms = na.parse_iso_ms(to_iso)

    stats = {
        "now": _iso(now_ms),
        "window": {"from": _iso(start_ms), "to": _iso(end_ms)},
        "grace_min": int(grace_min),
        "records_seen": 0,
        "slots_due": 0,
        "slots_capped": 0,
        "slots_evaluated": 0,
        "pairs_pending": 0,
        "rows_added": 0,
        "rows_verified": 0,
        "rows_not_informative": 0,
        "rows_not_verifiable": 0,
        "player_leads_skipped": 0,
        "records_corrupt": 0,
        "sri_downloads": 0,
        "sri_failures": 0,
        "warnings": [],
    }
    new_rows = []

    # raggruppa le coppie dovute per slot sorgente (un solo ricalcolo)
    groups = {}
    for rec in na.iter_records(archive_root):
        stats["records_seen"] += 1
        if not rec.get("available"):
            continue
        slot_iso = rec.get("source_slot")
        if not slot_iso:
            continue
        try:
            slot_ms = na.parse_iso_ms(slot_iso)
        except (TypeError, ValueError):
            stats["records_corrupt"] += 1
            continue
        if slot_ms is None or slot_ms < start_ms or slot_ms > end_ms:
            continue
        for lead in rec.get("leads") or []:
            try:
                lead_min = int(lead.get("lead_min"))
                valid_ms = na.parse_iso_ms(lead.get("valid_time"))
            except (TypeError, ValueError):
                stats["records_corrupt"] += 1
                continue
            if lead_min not in nf.OFFICIAL_LEADS:
                # frame del player: registrati nell'archivio, NON verificati
                stats["player_leads_skipped"] += 1
                continue
            if valid_ms is None or valid_ms > now_ms:
                continue
            # una coppia e' chiusa se esiste gia' una qualunque delle sue 3
            # righe di soglia (le righe si scrivono sempre tutte e 3 insieme)
            if (str(slot_iso), lead_min) in done_pairs:
                continue
            groups.setdefault((str(slot_iso), slot_ms), []).append(
                (lead_min, valid_ms, lead.get("valid_time")))

    for (slot_iso, slot_ms), due in sorted(groups.items(),
                                           key=lambda kv: kv[0][1]):
        if stats["slots_due"] >= int(max_slots):
            stats["slots_capped"] = len(groups) - stats["slots_due"]
            break
        stats["slots_due"] += 1
        # 1) scarica l'osservato PRIMA di ricalcolare: se manca non serve
        observed = {}
        for lead_min, valid_ms, valid_iso in due:
            observed[(lead_min, valid_ms)] = fetcher.get(valid_ms)
        to_score = [(l, v, i) for (l, v, i) in due
                    if observed.get((l, v)) is not None]
        missing = [(l, v, i) for (l, v, i) in due
                   if observed.get((l, v)) is None]
        for lead_min, valid_ms, valid_iso in missing:
            if now_ms >= valid_ms + int(grace_min) * 60000:
                new_rows.extend(not_verifiable_rows(
                    slot_iso, lead_min, valid_iso, "observed_missing"))
                stats["rows_not_verifiable"] += len(nf.VERIFY_THRESHOLDS_MMH)
            else:
                stats["pairs_pending"] += 1

        if not to_score:
            continue

        # 2) ricalcola il nowcast dallo stesso slot
        frames = fetcher.window(slot_ms, n=nf.FRAMES_WANTED)
        if len(frames) < 3:
            reason = "sri_frames_lt_3"
            for lead_min, _valid_ms, valid_iso in to_score:
                if now_ms >= _valid_ms + int(grace_min) * 60000:
                    new_rows.extend(not_verifiable_rows(
                        slot_iso, lead_min, valid_iso, reason))
                    stats["rows_not_verifiable"] += len(
                        nf.VERIFY_THRESHOLDS_MMH)
                else:
                    stats["pairs_pending"] += 1
            continue
        clk = clock_mod.Clock.at_ms(int(frames[-1].time_ms))
        bundle = _Bundle(frames)
        try:
            payload, native = compute_fn(bundle, config, clk)
        except Exception as exc:
            stats["warnings"].append("compute_failed:%s:%s" % (
                exc.__class__.__name__, exc))
            payload, native = {"available": False,
                               "null_reason": "compute_error:%s" % (
                                   exc.__class__.__name__)}, None
        # A/B OROGRAFIA: secondo calcolo dallo STESSO slot e dalle STESSE
        # frames, con il flag forzato all'OPPOSTO di quello della config. Il
        # confronto e' quindi a parita' di trasporto: cambia solo la
        # correzione, non il flusso ottico.
        cfg_orog = bool(((config or {}).get("orographic_enhancement") or {})
                        .get("enabled", True))
        primary_variant = "orog" if cfg_orog else "no_orog"
        ab_variant = "no_orog" if cfg_orog else "orog"
        try:
            payload_ab, native_ab = compute_fn(bundle, config, clk,
                                               orographic=(not cfg_orog))
        except Exception as exc:
            stats["warnings"].append("compute_ab_failed:%s:%s" % (
                exc.__class__.__name__, exc))
            payload_ab, native_ab = None, None
        if not payload.get("available") or native is None:
            reason = payload.get("null_reason") or "nowcast_unavailable"
            for lead_min, _valid_ms, valid_iso in to_score:
                if now_ms >= _valid_ms + int(grace_min) * 60000:
                    new_rows.extend(not_verifiable_rows(
                        slot_iso, lead_min, valid_iso, reason))
                    stats["rows_not_verifiable"] += len(
                        nf.VERIFY_THRESHOLDS_MMH)
                else:
                    stats["pairs_pending"] += 1
            continue
        if payload.get("source_slot") != slot_iso:
            reason = "recomputed_source_slot_mismatch"
            stats["warnings"].append(
                "%s: %s (archiviato %s)" % (reason, payload.get(
                    "source_slot"), slot_iso))
            for lead_min, _valid_ms, valid_iso in to_score:
                new_rows.extend(not_verifiable_rows(
                    slot_iso, lead_min, valid_iso, reason))
                stats["rows_not_verifiable"] += len(
                    nf.VERIFY_THRESHOLDS_MMH)
            continue

        # 3) punteggi per lead/soglia, UNA riga per variante (A/B orografia)
        stats["slots_evaluated"] += 1
        domain = native["_domain"]
        pers, pvalid = nf._rain_array(frames[-1])
        variants = [(primary_variant, native)]
        if native_ab is not None:
            variants.append((ab_variant, native_ab))
        for lead_min, _valid_ms, valid_iso in to_score:
            obs_rd = observed[(lead_min, _valid_ms)]
            obs, ovalid = nf._rain_array(obs_rd)
            dom = domain & ovalid & pvalid
            for variant, nat in variants:
                pred = nat.get(lead_min)
                if pred is None:
                    new_rows.extend(not_verifiable_rows(
                        slot_iso, lead_min, valid_iso, "lead_not_computed"))
                    stats["rows_not_verifiable"] += len(
                        nf.VERIFY_THRESHOLDS_MMH)
                    continue
                for thr in nf.VERIFY_THRESHOLDS_MMH:
                    row = scores_row(slot_iso, lead_min, valid_iso, thr,
                                     pred, pers, obs, dom, obs_rd,
                                     min_rain_pixels=min_rain_pixels,
                                     variant=variant)
                    new_rows.append(row)
                    if row["status"] == "verified":
                        stats["rows_verified"] += 1
                    else:
                        stats["rows_not_informative"] += 1

    out = merge_series(doc, new_rows, now_ms,
                       retention_days=retention_days,
                       min_rain_pixels=min_rain_pixels)
    # NIENTE RISCRITTURE INUTILI: se non ci sono righe nuove e il file e' gia'
    # schema 2, riscriverlo cambierebbe solo `generated_at` e produrrebbe un
    # commit ogni 10 minuti anche senza dati nuovi.
    written = bool(new_rows) or doc.get("schema") != SCHEMA
    if written:
        _atomic_write(verification_path,
                      json.dumps(out, ensure_ascii=False, allow_nan=False,
                                 indent=1))
    stats["verification_written"] = written
    stats["rows_added"] = len(new_rows)
    stats["sri_downloads"] = fetcher.downloads
    stats["sri_failures"] = fetcher.failures
    stats["seconds"] = round(time.time() - t0, 2)
    stats["verification_file"] = os.path.abspath(verification_path)
    stats["verification_bytes"] = (os.path.getsize(verification_path)
                                   if os.path.exists(verification_path)
                                   else 0)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Verifica a posteriori del nowcast SRI vs persistenza")
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--verification",
                    default="data/radar/nowcast_verification.json")
    ap.add_argument("--now", default=None,
                    help="istante UTC di riferimento (default: adesso)")
    ap.add_argument("--grace-min", type=int, default=GRACE_MIN)
    ap.add_argument("--max-lookback-hours", type=int,
                    default=MAX_LOOKBACK_HOURS)
    ap.add_argument("--max-slots", type=int, default=MAX_SLOTS_PER_RUN)
    ap.add_argument("--min-rain-pixels", type=int,
                    default=MIN_OBS_RAIN_PIXELS)
    ap.add_argument("--retention-days", type=int,
                    default=SERIES_RETENTION_DAYS)
    ap.add_argument("--from", dest="from_iso", default=None)
    ap.add_argument("--to", dest="to_iso", default=None)
    ap.add_argument("--force", action="store_true",
                    help="ricalcola anche le coppie gia' presenti")
    ap.add_argument("--report", default=None,
                    help="scrive il riepilogo delle misure in JSON")
    args = ap.parse_args(argv)

    from radar_engine.config import load_config
    config = load_config()
    config.setdefault("source", {})
    config["source"].setdefault("fetch_workers", 4)
    try:
        stats = verify_pending(
            args.archive_dir, args.verification, config, now=args.now,
            grace_min=args.grace_min,
            max_lookback_hours=args.max_lookback_hours,
            max_slots=args.max_slots,
            min_rain_pixels=args.min_rain_pixels,
            retention_days=args.retention_days,
            from_iso=args.from_iso, to_iso=args.to_iso, force=args.force)
    except Exception as exc:              # il run radar non deve fallire
        print("[WARN] verifica nowcast non eseguita (%s:%s)" % (
            exc.__class__.__name__, exc))
        return 0
    print("[INFO] verifica nowcast: %d slot dovuti, %d valutati, %d righe "
          "(%d verificate, %d non informative, %d non verificabili), "
          "%d lead player saltati, %d download SRI, %.1f s, file %s" % (
              stats["slots_due"], stats["slots_evaluated"],
              stats["rows_added"], stats["rows_verified"],
              stats["rows_not_informative"], stats["rows_not_verifiable"],
              stats["player_leads_skipped"], stats["sri_downloads"],
              stats["seconds"],
              "aggiornato" if stats["verification_written"] else "invariato"))
    for warning in stats["warnings"][:10]:
        print("[WARN] %s" % warning)
    if args.report:
        _atomic_write(args.report,
                      json.dumps(stats, ensure_ascii=False, indent=1))
        print("[INFO] misure verifica scritte in %s" % args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
