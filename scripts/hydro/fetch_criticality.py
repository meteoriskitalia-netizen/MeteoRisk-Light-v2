#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layer "criticita' idrogeologica / idraulica / temporali UFFICIALE" del DPC.

Scarica il bollettino giornaliero del Dipartimento della Protezione Civile
(repo pubblico `pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica`,
branch `master`) e produce un file COMPATTO per il client:

    data/hydro/criticality.json

REGOLE DEL PRODOTTO (vincolanti: non modificare senza una decisione esplicita):
  * si usano SOLO i livelli ufficiali del bollettino: nessuna soglia nostra,
    nessuna somma dei tre rischi in un indice unico;
  * i tre rischi restano SEPARATI (`hydraulic`, `thunderstorm`,
    `hydrogeological`) e ogni zona porta i tre codici;
  * `dpc_map_class` e' il campo ufficiale "Rappresentata nella mappa" copiato
    alla lettera: NON e' un indice calcolato da MeteoRisk;
  * la mappatura stringa ufficiale -> codice e' in `levels_legend.map`; una
    stringa NON prevista diventa `unknown` (mai `none`) e viene elencata in
    `levels_legend.unmapped_raw_values`;
  * se il bollettino non copre la data odierna il file si scrive comunque ma
    con `stale: true` e `zones: []`: il client nasconde il layer e mostra la
    data dell'ultimo bollettino disponibile. Nessuno stato inventato.

FONTE E CADENZA (misurate il 2026-11-13):
  * indice giornaliero `files/YYYYMMDD_HHMM.json` (~7-10 KB) con i link al
    TopoJSON `_today` / `_tomorrow` (1,2 MB, 187 geometrie);
  * 2570 indici nel repo; sugli ultimi 14 giorni i commit su `master` cadono
    fra le 13:23 e le 16:10 UTC, un bollettino al giorno. L'ora nel nome file
    e' quella DICHIARATA nel bollettino, non l'ora di pubblicazione: il nome
    non si indovina, si scopre elencando l'albero `files/` (3 richieste API);
  * il bollettino del giorno D copre D (`_today`) e D+1 (`_tomorrow`): prima
    della pubblicazione del giorno D+1 si usa il `_tomorrow` dell'indice piu'
    recente. E' il prodotto ufficiale, non un'inferenza.

Licenza del dato: CC BY 4.0 (README del repo DPC), attribuzione obbligatoria
"Dipartimento della Protezione Civile".

Solo standard library (urllib, json, gzip, time, calendar).

Uso:
    python scripts/hydro/fetch_criticality.py --out data/hydro/criticality.json
    python scripts/hydro/fetch_criticality.py --if-stale      # gate per il cron
    python scripts/hydro/fetch_criticality.py --fixture-dir DIR   # offline/test

Exit code: 0 ok (o no-op dichiarato), 2 errore di rete/sorgente, 3 dato non
utilizzabile (nessuna zona, topologia malformata, budget irraggiungibile).
"""
from __future__ import annotations

import argparse
import calendar
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

SCHEMA = "meteorisk.hydro.criticality/1"
DPC_REPO = "pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica"
DPC_BRANCH = "master"
DPC_HTML = "https://github.com/" + DPC_REPO
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com/%s/%s/" % (DPC_REPO, DPC_BRANCH)

LICENCE = "CC BY 4.0"
ATTRIBUTION = "Dipartimento della Protezione Civile"

# Campi ufficiali del TopoJSON -> chiave interna. L'ordine e' quello del
# bollettino e NON implica una gerarchia: i tre rischi sono indipendenti.
RISK_FIELDS = {
    "hydraulic": "Per rischio idraulico",
    "thunderstorm": "Per rischio temporali",
    "hydrogeological": "Per rischio idrogeologico",
}
MAP_FIELD = "Rappresentata nella mappa"
NAME_FIELD = "Nome zona"
COMUNI_FIELD = "Comuni"

# Mappatura ESATTA delle stringhe ufficiali. Il bollettino usa due forme:
#   - nei campi per-rischio:            "<classe> / ALLERTA <COLORE>"
#   - in "Rappresentata nella mappa":   "<classe> per rischio <tipo> / ALLERTA ..."
# Nessuna delle due viene interpretata: si confronta la stringa intera.
NONE_RAW = "Assenza di fenomeni significativi prevedibili / NESSUNA ALLERTA"
LEVEL_MAP = {NONE_RAW: "none"}
for _suffix, _code in (
    ("Ordinaria / ALLERTA GIALLA", "yellow"),
    ("Moderata / ALLERTA ARANCIONE", "orange"),
    ("Elevata / ALLERTA ROSSA", "red"),
):
    LEVEL_MAP[_suffix] = _code
    for _risk in ("idraulico", "temporali", "idrogeologico"):
        LEVEL_MAP[_suffix.replace(" / ALLERTA", " per rischio %s / ALLERTA" % _risk)] = _code

CODES = ("none", "yellow", "orange", "red")
CODE_LABELS_IT = {"none": "Nessuna", "yellow": "Gialla", "orange": "Arancione", "red": "Rossa"}

DEFAULT_MAX_BYTES = 300 * 1024
DEFAULT_TOLERANCE_DEG = 0.005  # ~550 m: dichiarata nel file prodotto
MAX_TOLERANCE_DEG = 0.05


# --------------------------------------------------------------------------
# rete (stdlib, con retry) — nessuna dipendenza esterna
# --------------------------------------------------------------------------
class SourceError(RuntimeError):
    pass


def _get(url, timeout=45, attempts=3, retry_statuses=(429, 500, 502, 503, 504), headers=None):
    last = None
    for i in range(attempts):
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "meteorisk-hydro/1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = "HTTP %s su %s" % (e.code, url)
            if e.code not in retry_statuses:
                raise SourceError(last)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = "%s su %s" % (e.__class__.__name__, url)
        if i + 1 < attempts:
            time.sleep(2 * (i + 1))
    raise SourceError("rete: %s (dopo %d tentativi)" % (last, attempts))


def _api(path, timeout=45):
    body = _get(API + path, timeout=timeout, headers={
        "User-Agent": "meteorisk-hydro/1",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise SourceError("risposta API non JSON: %s" % e)


def discover_indexes(timeout=45):
    """Elenca i nomi `YYYYMMDD_HHMM.json` sotto files/ (3 richieste API)."""
    branch = _api("/repos/%s/branches/%s" % (DPC_REPO, DPC_BRANCH), timeout=timeout)
    root_tree = branch["commit"]["commit"]["tree"]["sha"]
    root = _api("/repos/%s/git/trees/%s" % (DPC_REPO, root_tree), timeout=timeout)
    files_sha = None
    for e in root.get("tree", []):
        if e.get("path") == "files" and e.get("type") == "tree":
            files_sha = e["sha"]
            break
    if not files_sha:
        raise SourceError("albero 'files' assente nel repo DPC")
    tree = _api("/repos/%s/git/trees/%s" % (DPC_REPO, files_sha), timeout=timeout)
    names = [e["path"] for e in tree.get("tree", [])
             if e.get("type") == "blob" and len(e["path"]) == len("20260101_1500.json")
             and e["path"].endswith(".json")
             and e["path"][:8].isdigit() and e["path"][9:13].isdigit()]
    if not names:
        raise SourceError("nessun indice giornaliero in files/")
    names.sort()
    return names


def pick_pair(names, today_compact):
    """Sceglie l'indice piu' recente e quale TopoJSON usare.

    Ritorna (index_name, kind):
      * indice di oggi   -> 'today'      (copre oggi)
      * indice di ieri   -> 'tomorrow'   (il bollettino di ieri copre oggi)
      * piu' vecchio     -> 'today'      (il chiamante marca `stale`)
    """
    import datetime
    latest = names[-1]
    if latest[:8] == today_compact:
        return latest, "today"
    today = datetime.date(int(today_compact[:4]), int(today_compact[4:6]), int(today_compact[6:8]))
    yesterday = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    if latest[:8] == yesterday:
        return latest, "tomorrow"
    return latest, "today"


def fetch_index(name, timeout=45):
    raw = _get(RAW + "files/" + name, timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8")), len(raw)
    except ValueError as e:
        raise SourceError("indice %s non JSON: %s" % (name, e))


# --------------------------------------------------------------------------
# TopoJSON -> zone con geometria semplificata
# --------------------------------------------------------------------------
def _rdp(points, tol):
    """Douglas-Peucker su una polilinea aperta (griglia intera)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tol * tol
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best = -1.0
        best_i = -1
        for i in range(a + 1, b):
            px, py = points[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / seg2
                if t < 0:
                    qx, qy = ax, ay
                elif t > 1:
                    qx, qy = bx, by
                else:
                    qx, qy = ax + t * dx, ay + t * dy
                d2 = (px - qx) ** 2 + (py - qy) ** 2
            if d2 > best:
                best, best_i = d2, i
        if best > tol2:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [p for p, k in zip(points, keep) if k]


def decode_topology(topo):
    """Decodifica gli archi in coordinate di griglia INTERE (delta decoding)."""
    tr = topo.get("transform") or {}
    scale = tr.get("scale")
    translate = tr.get("translate")
    if not scale or not translate:
        raise SourceError("TopoJSON senza transform (quantizzazione assente)")
    arcs = []
    for arc in topo.get("arcs", []):
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x, y))
        arcs.append(pts)
    return arcs, scale, translate


def ring_from_arcs(arc_ids, arcs):
    pts = []
    for aid in arc_ids:
        a = arcs[aid] if aid >= 0 else list(reversed(arcs[-aid - 1]))
        if pts and a and pts[-1] == a[0]:
            a = a[1:]
        pts.extend(a)
    if len(pts) > 1 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _area_deg2(pts, scale, translate):
    """Area (shoelace) CON SEGNO di un anello in gradi^2: i buchi hanno verso
    opposto e si sottraggono da soli quando si somma l'anello esterno."""
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1 = pts[i][0] * scale[0] + translate[0]
        y1 = pts[i][1] * scale[1] + translate[1]
        x2 = pts[(i + 1) % n][0] * scale[0] + translate[0]
        y2 = pts[(i + 1) % n][1] * scale[1] + translate[1]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def build_zones(topo, tol_deg):
    """Ritorna (zones, stats, observed, unmapped) con geometria in griglia intera."""
    arcs, scale, translate = decode_topology(topo)
    tol_units = tol_deg / max(scale[0], scale[1])  # conservativo: meno semplificazione
    before = sum(len(a) for a in arcs)
    s_arcs = [_rdp(a, tol_units) for a in arcs]
    after = sum(len(a) for a in s_arcs)

    objects = topo.get("objects") or {}
    if len(objects) != 1:
        raise SourceError("attesi 1 oggetto TopoJSON, trovati %d" % len(objects))
    obj = list(objects.values())[0]
    geoms = obj.get("geometries") or []
    if not geoms:
        raise SourceError("TopoJSON senza geometrie")

    zones = []
    unmapped = set()
    observed = {}
    emitted = 0
    polys_before = 0
    polys_after = 0
    area_before = 0.0
    area_after = 0.0
    for g in geoms:
        props = g.get("properties") or {}
        name = props.get(NAME_FIELD)
        if not name:
            raise SourceError("geometria senza '%s'" % NAME_FIELD)
        levels = {}
        for key, field in RISK_FIELDS.items():
            raw = props.get(field)
            if raw is None:
                raise SourceError("zona %r senza campo %r" % (name, field))
            observed[raw] = LEVEL_MAP.get(raw, "unknown")
            if raw not in LEVEL_MAP:
                unmapped.add(raw)
            levels[key] = LEVEL_MAP.get(raw, "unknown")
        map_raw = props.get(MAP_FIELD)
        if map_raw is not None:
            observed[map_raw] = LEVEL_MAP.get(map_raw, "unknown")
            if map_raw not in LEVEL_MAP:
                unmapped.add(map_raw)
        comuni = props.get(COMUNI_FIELD) or []

        # Polygon: arcs = [ring, hole...]; MultiPolygon: arcs = [[ring, hole...], ...]
        if g.get("type") == "Polygon":
            polys = [g.get("arcs") or []]
        elif g.get("type") == "MultiPolygon":
            polys = g.get("arcs") or []
        else:
            raise SourceError("tipo geometria non gestito: %r" % g.get("type"))

        out_polys = []
        for poly in polys:
            polys_before += 1
            a_src = 0.0
            for ring_ids in poly:
                a_src += _area_deg2(ring_from_arcs(ring_ids, arcs), scale, translate)
            area_before += abs(a_src)
            rings = []
            for ring_ids in poly:
                pts = ring_from_arcs(ring_ids, s_arcs)
                if len(pts) < 4:  # un poligono deve avere 3 vertici + chiusura
                    continue
                rings.append(pts)
                emitted += len(pts)
            if rings:
                out_polys.append(rings)
                polys_after += 1
                a_out = 0.0
                for pts in rings:
                    a_out += _area_deg2(pts, scale, translate)
                area_after += abs(a_out)
        if not out_polys:
            raise SourceError("zona %r senza anelli dopo la semplificazione" % name)

        # ring piatti con DELTA interi: [dx0,dy0,dx1,dy1,...], il primo punto e'
        # assoluto (delta da 0); il ring e' chiuso (l'ultimo punto assoluto
        # coincide con il primo, non l'ultimo delta).
        flat_polys = []
        xs = []
        ys = []
        for rings in out_polys:
            flat_rings = []
            for pts in rings:
                prev_x = prev_y = 0
                flat = []
                for x, y in pts:
                    flat.append(x - prev_x)
                    flat.append(y - prev_y)
                    prev_x, prev_y = x, y
                    xs.append(x)
                    ys.append(y)
                flat_rings.append(flat)
            flat_polys.append(flat_rings)
        zones.append({
            "name": name,
            "levels": levels,
            "dpc_map_class": LEVEL_MAP.get(map_raw, "unknown") if map_raw is not None else "unknown",
            "comuni_count": len(comuni),
            "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "polygons": flat_polys,
        })
    zones.sort(key=lambda z: z["name"])
    stats = {
        "vertices_before": before,
        "vertices_after": emitted,
        "arc_vertices_after": after,
        "polygons_before": polys_before,
        "polygons_after": polys_after,
        "area_deg2_before": round(area_before, 6),
        "area_deg2_after": round(area_after, 6),
        "tolerance_deg": tol_deg,
        "tolerance_grid_units": round(tol_units, 3),
        "scale": scale,
        "translate": translate,
    }
    return zones, stats, observed, sorted(unmapped)


def build_document(zones, stats, observed, unmapped, meta):
    return {
        "schema": SCHEMA,
        "generated_at": meta["generated_at"],
        "valid_date": meta["valid_date"],
        "stale": meta["stale"],
        "stale_reason": meta["stale_reason"],
        "source": {
            "repository": DPC_HTML,
            "branch": DPC_BRANCH,
            "index_name": meta["index_name"],
            "index_label": meta["index_label"],
            "index_date": meta["index_date"],
            "index_url": meta["index_url"],
            "topojson_url": meta["topojson_url"],
            "topojson_kind": meta["topojson_kind"],
            "index_bytes": meta["index_bytes"],
            "topojson_bytes": meta["topojson_bytes"],
            "publication_utc_note": "il nome del file porta l'ora del bollettino, non quella di pubblicazione",
        },
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
        "disclaimer": ("Criticita' PREVISTA dal Dipartimento della Protezione Civile. "
                      "Non e' un'allerta emessa da MeteoRisk e non sostituisce i bollettini ufficiali."),
        "levels_legend": {
            "codes": list(CODES),
            "labels_it": CODE_LABELS_IT,
            "risk_fields": RISK_FIELDS,
            "map_field": MAP_FIELD,
            "map": dict(sorted(LEVEL_MAP.items())),
            "observed": dict(sorted(observed.items())),
            "unmapped_raw_values": unmapped,
            "unknown_code": "unknown",
            "note": ("mappatura esatta delle stringhe ufficiali; 'unknown' = stringa non "
                     "prevista dal bollettino, mai interpretata come 'none'"),
        },
        "geometry": {
            "encoding": ("ring PIATTI con DELTA interi in unita' di griglia TopoJSON: "
                         "[dx0,dy0,dx1,dy1,...]; il primo punto e' assoluto (delta da 0), "
                         "i successivi sono delta dal precedente; coordinate assolute = "
                         "somma cumulativa; il ring e' chiuso (l'ultimo punto assoluto "
                         "coincide con il primo); lon = x*scale[0]+translate[0], "
                         "lat = y*scale[1]+translate[1]"),
            "scale": stats["scale"],
            "translate": stats["translate"],
            "simplify": {
                "method": "douglas-peucker sugli archi TopoJSON (bordi condivisi identici)",
                "tolerance_deg": stats["tolerance_deg"],
                "tolerance_grid_units": stats["tolerance_grid_units"],
                "vertices_before": stats["vertices_before"],
                "vertices_after": stats["vertices_after"],
                "arc_vertices_after": stats["arc_vertices_after"],
                "polygons_before": stats["polygons_before"],
                "polygons_after": stats["polygons_after"],
                "area_deg2_before": stats["area_deg2_before"],
                "area_deg2_after": stats["area_deg2_after"],
            },
        },
        "zones": zones,
    }


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _fit_budget(build, tolerance, max_bytes):
    """Semplifica di piu' finche' il file non sta nel budget (tolleranza dichiarata)."""
    steps = []
    for _ in range(8):
        doc = build(tolerance)
        raw = _dump(doc)
        steps.append({"tolerance_deg": tolerance, "bytes": len(raw),
                      "vertices_after": doc["geometry"]["simplify"]["vertices_after"]})
        if len(raw) <= max_bytes:
            return doc, raw, steps
        tolerance *= 1.25
        if tolerance > MAX_TOLERANCE_DEG:
            break
    raise SourceError("budget %d byte non raggiungibile (ultimo tentativo %.3f deg -> %d byte)"
                      % (max_bytes, tolerance, steps[-1]["bytes"]))


# --------------------------------------------------------------------------
# date locali (Europe/Rome) senza tzdata
# --------------------------------------------------------------------------
def _rome_offset_hours(struct_time):
    """CET/CEST (DST europeo) — approssimazione dichiarata, niente fusi storici."""
    year = struct_time.tm_year
    last = calendar.monthrange(year, 3)[1]
    last_sun_mar = last - ((calendar.weekday(year, 3, last) + 1) % 7)
    last = calendar.monthrange(year, 10)[1]
    last_sun_oct = last - ((calendar.weekday(year, 10, last) + 1) % 7)
    dst = (struct_time.tm_mon, struct_time.tm_mday) >= (3, last_sun_mar) and \
          (struct_time.tm_mon, struct_time.tm_mday) < (10, last_sun_oct)
    return 2 if dst else 1


def rome_date(now_struct=None):
    st = now_struct or time.gmtime()
    return time.strftime("%Y-%m-%d", time.gmtime(calendar.timegm(st) + _rome_offset_hours(st) * 3600))


def _now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _in_window(window):
    if not window:
        return True
    a, b = window.split("-")
    now = time.gmtime()
    cur = now.tm_hour * 60 + now.tm_min
    h1, m1 = a.split(":")
    h2, m2 = b.split(":")
    return int(h1) * 60 + int(m1) <= cur <= int(h2) * 60 + int(m2)


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def fetch(fixture_dir=None, timeout=45, today_compact=None):
    """Ritorna (index_name, idx, idx_bytes, topo, topo_bytes, kind, url)."""
    if fixture_dir:
        try:
            with open(os.path.join(fixture_dir, "index.json"), "rb") as f:
                idx_raw = f.read()
            idx = json.loads(idx_raw.decode("utf-8"))
            with open(os.path.join(fixture_dir, "topo.json"), "rb") as f:
                topo_raw = f.read()
        except (OSError, ValueError) as e:
            raise SourceError("fixture non leggibile in %s: %s" % (fixture_dir, e))
        # stesso criterio della produzione: la fixture e' "di oggi" o "di ieri"
        day = (idx.get("date") or "")[:10].replace("-", "") or (today_compact or "")
        kind = pick_pair([day + "_1200.json"], today_compact or day)[1] if day else "today"
        return ("fixture_index.json", idx, len(idx_raw),
                json.loads(topo_raw.decode("utf-8")), len(topo_raw), kind, None)

    today = today_compact or rome_date().replace("-", "")
    names = discover_indexes(timeout=timeout)
    index_name, kind = pick_pair(names, today)
    idx, idx_bytes = fetch_index(index_name, timeout=timeout)
    slot = idx.get(kind) or {}
    url = slot.get("topo_json")
    if not url:
        raise SourceError("indice %s senza topo_json per %r" % (index_name, kind))
    topo_raw = _get(url, timeout=timeout)
    try:
        topo = json.loads(topo_raw.decode("utf-8"))
    except ValueError as e:
        raise SourceError("TopoJSON non valido: %s" % e)
    return index_name, idx, idx_bytes, topo, len(topo_raw), kind, url


def main(argv=None):
    ap = argparse.ArgumentParser(description="Criticita' DPC -> data/hydro/criticality.json")
    ap.add_argument("--out", default=os.path.join("data", "hydro", "criticality.json"))
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--tolerance-deg", type=float, default=DEFAULT_TOLERANCE_DEG)
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--window-utc", default="12:30-20:30",
                    help="finestra di pubblicazione misurata; fuori finestra --if-stale non fa rete")
    ap.add_argument("--if-stale", action="store_true",
                    help="gate per il cron: fuori finestra esce 0 senza rete se il file esiste")
    ap.add_argument("--force", action="store_true", help="ignora il gate orario")
    ap.add_argument("--fixture-dir", default=None, help="usa index.json + topo.json locali (test)")
    ap.add_argument("--now", default=None, help="ISO UTC per test deterministici")
    ap.add_argument("--strict-unknown", action="store_true",
                    help="esce 3 se una stringa ufficiale non e' mappata")
    args = ap.parse_args(argv)

    now_struct = time.strptime(args.now, "%Y-%m-%dT%H:%M:%SZ") if args.now else time.gmtime()
    now_iso = args.now or _now_utc()
    today_iso = rome_date(now_struct)
    today_compact = today_iso.replace("-", "")

    if args.if_stale and not args.force and not _in_window(args.window_utc) and os.path.exists(args.out):
        # fuori finestra si salta SOLO se il file copre gia' la data odierna:
        # dopo la mezzanotte il bollettino di ieri copre oggi con il suo
        # `_tomorrow`, e va preso (una volta) anche fuori dalla finestra.
        covers_today = False
        try:
            with open(args.out, "rb") as f:
                old = json.loads(f.read().decode("utf-8"))
            covers_today = (not old.get("stale")) and old.get("valid_date") == today_iso
        except (ValueError, OSError):
            covers_today = False
        if covers_today:
            print("[hydro] fuori finestra %s: file gia' valido per il %s, nessuna richiesta"
                  % (args.window_utc, today_iso))
            return 0
        print("[hydro] fuori finestra %s ma il file NON copre il %s: aggiorno"
              % (args.window_utc, today_iso))

    try:
        index_name, idx, idx_bytes, topo, topo_bytes, kind, topo_url = fetch(
            args.fixture_dir, args.timeout, today_compact)
    except SourceError as e:
        print("[hydro] ERRORE sorgente: %s" % e, file=sys.stderr)
        return 2

    label = idx.get("name") or ""
    index_day = (idx.get("date") or "")[:10]
    # il bollettino del giorno D copre D e, con _tomorrow, D+1
    stale = kind != "tomorrow" and index_day != today_iso
    valid_date = today_iso if not stale else (index_day or today_iso)

    def build(tol):
        zones, stats, observed, unmapped = build_zones(topo, tol)
        meta = {
            "generated_at": now_iso,
            "valid_date": valid_date,
            "stale": stale,
            "stale_reason": None if not stale else
            ("ultimo bollettino disponibile: %s (non copre la data odierna %s)" % (label or index_name, today_iso)),
            "index_name": index_name,
            "index_label": label,
            "index_date": idx.get("date"),
            "index_url": None if args.fixture_dir else (DPC_HTML + "/blob/" + DPC_BRANCH + "/files/" + index_name),
            "topojson_url": topo_url,
            "topojson_kind": kind,
            "index_bytes": idx_bytes,
            "topojson_bytes": topo_bytes,
        }
        return build_document(zones if not stale else [], stats, observed, unmapped, meta)

    try:
        doc, raw, steps = _fit_budget(build, args.tolerance_deg, args.max_bytes)
    except SourceError as e:
        print("[hydro] ERRORE dato: %s" % e, file=sys.stderr)
        return 3

    unmapped = doc["levels_legend"]["unmapped_raw_values"]
    if unmapped and args.strict_unknown:
        print("[hydro] ERRORE: stringhe ufficiali non mappate: %s" % unmapped, file=sys.stderr)
        return 3

    if os.path.exists(args.out) and not args.fixture_dir:
        try:
            with open(args.out, "rb") as f:
                old = json.loads(f.read().decode("utf-8"))
            if (old.get("schema") == SCHEMA and
                    old.get("source", {}).get("index_name") == index_name and
                    old.get("source", {}).get("topojson_kind") == kind and
                    old.get("stale") == doc["stale"]):
                print("[hydro] nessun cambio: %s (%s) gia' pubblicato" % (index_name, kind))
                return 0
        except (ValueError, OSError):
            pass

    _atomic_write(args.out, raw)
    gz = len(gzip.compress(raw, 9))
    dist = {c: 0 for c in CODES}
    for z in doc["zones"]:
        for key in RISK_FIELDS:
            code = z["levels"][key]
            dist[code] = dist.get(code, 0) + 1
    print("[hydro] scritto %s: %d byte (gzip %d), zone=%d, vertici %d -> %d (tol %.4f deg, %d passi)"
          % (args.out, len(raw), gz, len(doc["zones"]),
             doc["geometry"]["simplify"]["vertices_before"],
             doc["geometry"]["simplify"]["vertices_after"],
             doc["geometry"]["simplify"]["tolerance_deg"], len(steps)))
    print("[hydro] bollettino=%s kind=%s valid=%s stale=%s" % (index_name, kind, doc["valid_date"], doc["stale"]))
    print("[hydro] livelli (zone x rischio): %s" % json.dumps(dist))
    if unmapped:
        print("[hydro] ATTENZIONE stringhe non mappate: %s" % unmapped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
