#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Eventi temporali e split per evento (MAI split a caso) per la validazione 3D.

Un "evento" e' una componente connessa di osservazioni: due osservazioni
appartengono allo stesso evento se distano meno di `gap_min` minuti E i loro
centroidi distano meno di `link_km`. Tutte le scansioni dello stesso temporale
finiscono nello stesso insieme (train oppure test). Lo split e' CRONOLOGICO
con un margine temporale fra train e test, dichiarato nell'output.

Nessun dato simulato: il modulo lavora solo su record passati dal chiamante.
"""
from __future__ import annotations

import datetime as _dt
import math

UTC = _dt.timezone.utc


def iso_to_ms(iso):
    """ISO8601 UTC -> epoch ms. None se non interpretabile."""
    if not iso:
        return None
    s = str(iso).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def ms_to_iso(ms):
    if ms is None:
        return None
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 6371.0 * 2.0 * math.asin(min(1.0, math.sqrt(a)))


def _bucket(lat, lon, deg=1.0):
    return (int(math.floor(lat / deg)), int(math.floor(lon / deg)))


def normalize_rows(rows):
    """Filtra righe con tempo/posizione non validi; dichiara gli scarti."""
    out, skipped = [], 0
    for r in rows or []:
        try:
            ts = int(r.get("ts_ms") if r.get("ts_ms") is not None
                     else iso_to_ms(r.get("slot_iso") or r.get("timestamp")))
            lat = float(r["lat"])
            lon = float(r["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("lat/lon fuori intervallo")
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        item = dict(r)
        item["ts_ms"] = ts
        item["lat"] = lat
        item["lon"] = lon
        out.append(item)
    out.sort(key=lambda r: r["ts_ms"])
    return out, skipped


def group_events(rows, *, gap_min=180.0, link_km=80.0, bucket_deg=1.0):
    """Componenti connesse nel tempo+spazio. Ritorna (events, meta).

    `gap_min` di default 180 min (3 ore): una pausa piu' lunga separa due
    temporali distinti. `link_km` di default 80 km: due celle dello stesso
    sistema convettivo si muovono meno di questo in 3 ore. Entrambi sono
    parametri DICHIARATI (non tarati sull'etichetta), quindi non costituiscono
    una perdita di informazione fra train e test.
    """
    clean, skipped = normalize_rows(rows)
    n = len(clean)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    buckets = {}
    gap_ms = float(gap_min) * 60000.0
    link = float(link_km)
    for i, cur in enumerate(clean):
        b = _bucket(cur["lat"], cur["lon"], bucket_deg)
        for db0 in (-1, 0, 1):
            for db1 in (-1, 0, 1):
                for j in buckets.get((b[0] + db0, b[1] + db1), ()): 
                    prev = clean[j]
                    if cur["ts_ms"] - prev["ts_ms"] > gap_ms:
                        continue
                    if _haversine_km(cur["lat"], cur["lon"],
                                     prev["lat"], prev["lon"]) <= link:
                        union(i, j)
        buckets.setdefault(b, []).append(i)

    comps = {}
    for i, r in enumerate(clean):
        comps.setdefault(find(i), []).append(r)
    events = []
    for members in comps.values():
        members.sort(key=lambda r: r["ts_ms"])
        events.append({
            "start_ms": members[0]["ts_ms"],
            "end_ms": members[-1]["ts_ms"],
            "start_iso": ms_to_iso(members[0]["ts_ms"]),
            "end_iso": ms_to_iso(members[-1]["ts_ms"]),
            "n_observations": len(members),
            "n_scans": len({r["ts_ms"] for r in members}),
            "rows": members,
        })
    events.sort(key=lambda e: (e["start_ms"], e["end_ms"]))
    for k, ev in enumerate(events, 1):
        ev["event_id"] = "EV{:04d}".format(k)
    meta = {
        "n_rows_in": len(rows or []),
        "n_rows_valid": n,
        "n_rows_skipped": skipped,
        "n_events": len(events),
        "gap_min": float(gap_min),
        "link_km": float(link_km),
        "bucket_deg": float(bucket_deg),
        "method": "componente connessa tempo+spazio; parametri dichiarati, non tarati",
    }
    return events, meta


def chronological_split(events, *, train_fraction=0.7, min_gap_hours=6.0):
    """Split temporale per evento con margine fra train e test.

    Restituisce `passed=False` (con motivo) se non esiste un confine con il
    margine richiesto: in quel caso NON si deve addestrare/validare con quei
    dati. Nessun evento puo' comparire in entrambi gli insiemi.
    """
    evs = sorted(events or [], key=lambda e: e["start_ms"])
    n = len(evs)
    if n < 2:
        return {
            "passed": False, "reason": "too_few_events:{}".format(n),
            "train_events": [], "test_events": [],
            "n_train": 0, "n_test": 0,
        }
    target = max(1, min(n - 1, int(round(n * float(train_fraction)))))
    gap_ms = float(min_gap_hours) * 3600000.0
    candidates = [i for i in range(1, n)
                  if evs[i - 1]["end_ms"] + gap_ms <= evs[i]["start_ms"]]
    if not candidates:
        return {
            "passed": False,
            "reason": "no_boundary_with_{}h_gap".format(float(min_gap_hours)),
            "train_events": [], "test_events": [],
            "n_train": 0, "n_test": 0,
        }
    boundary = min(candidates, key=lambda i: abs(i - target))
    train, test = evs[:boundary], evs[boundary:]
    train_ids = [e["event_id"] for e in train]
    test_ids = [e["event_id"] for e in test]
    assert not (set(train_ids) & set(test_ids)), "evento in entrambi gli insiemi"
    assert train[-1]["end_ms"] < test[0]["start_ms"], "sovrapposizione temporale"
    return {
        "passed": True,
        "train_events": train_ids,
        "test_events": test_ids,
        "n_train": len(train_ids),
        "n_test": len(test_ids),
        "train_end_iso": ms_to_iso(train[-1]["end_ms"]),
        "test_start_iso": ms_to_iso(test[0]["start_ms"]),
        "gap_hours": float(min_gap_hours),
        "target_train_fraction": float(train_fraction),
        "actual_train_fraction": round(len(train) / float(n), 4),
        "declaration": (
            "split PER EVENTO e cronologico: tutte le scansioni di un evento "
            "nello stesso insieme; nessuna sovrapposizione temporale fra "
            "train e test; la calibrazione usa SOLO train, il test e' "
            "inviolato"),
    }


def event_stats(events):
    """Riepilogo descrittivo degli eventi (nessuna etichetta coinvolta)."""
    out = []
    for e in events or []:
        out.append({
            "event_id": e.get("event_id"),
            "start_iso": e.get("start_iso"),
            "end_iso": e.get("end_iso"),
            "duration_min": (None if e.get("end_ms") is None
                             else round((e["end_ms"] - e["start_ms"]) / 60000.0, 1)),
            "n_observations": e.get("n_observations"),
            "n_scans": e.get("n_scans"),
        })
    return out
