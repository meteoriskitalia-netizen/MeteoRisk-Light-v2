#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tendenza a 7 giorni per le 107 province — file AGGIUNTIVO e SEPARATO.

PERCHE' UN FILE SEPARATO (vincolo assoluto, incidente passato)
--------------------------------------------------------------
`data/latest/meteorisk-points.json` contiene 49 campi ORARI (CAPE, CIN,
lifted_index, k_index, temperature/geopotenziali sui livelli di pressione,
visibilita', ...) e alimenta gli indici severi del client. NON va assottigliato
per nessun motivo. Questo modulo NON lo legge, NON lo scrive e NON ne tocca la
struttura: produce soltanto un file piccolo con i campi DAILY di Open-Meteo,
destinato alla vista "tendenza 7 giorni".

COSA PRODUCE
------------
`data/latest/weekly.json` (default), schema `meteorisk.weekly/1`:

  {
    "version": "meteorisk.weekly/1",
    "generated_at": "2026-11-10T12:34:56+00:00",
    "days": ["2026-11-10", ... 7 date ISO ...],
    "source": "best_match",                 # modello Open-Meteo usato
    "provider": "Open-Meteo",
    "attribution": "...",
    "timezone": "Europe/Rome",
    "forecast_days": 7,
    "count": 107,
    "fields": {"tmax": {"label": ..., "unit": "C", "decimals": 1}, ...},
    "province_fields": ["sigla", "days"],
    "provinces": [
      ["RM", [{"tmax": 20.2, "tmin": 13.6, ...}, ... 7 record ...]],
      ...
    ]
  }

Le province sono array compatti `[sigla, [7 record]]`. Un campo con valore
`null` (o assente) dalla risposta del modello viene OMESSO dal record di quella
provincia: non viene mai scritto 0 al posto di un dato mancante. Se un campo e'
nullo su TUTTE le province/giorni (es. `et0` non disponibile) sparisce anche
dall'elenco `fields`.

RICHIESTE (budget: al massimo 3 per le 107 province)
----------------------------------------------------
Open-Meteo accetta `latitude=a,b,c&longitude=...` con risposta in array.
Batch BILANCIATI con massimo 100 coordinate (default): 107 province = 2
richieste da 54 + 53, entrambe nel range 50-100. Oltre 3 richieste il modulo
rifiuta di partire. Timeout esplicito e retry limitati (429/5xx/timeout) con
attesa; se i retry si esauriscono il file NON viene toccato e il processo esce 2
(il workflow ha `continue-on-error: true`, quindi resta verde).

SOLO campi DAILY: nessuna variabile oraria viene richiesta, salvata o esposta.
"""

import argparse
import datetime as _dt
import gzip
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import common  # noqa: E402

VERSION = "meteorisk.weekly/1"
FORECAST_DAYS = 7
DEFAULT_MODEL = "best_match"
DEFAULT_BATCH_SIZE = 100
MIN_BATCH_SIZE = 50
MAX_BATCH_SIZE = 100
MAX_REQUESTS = 3
DEFAULT_TIMEOUT_S = 30
DEFAULT_ATTEMPTS = 3
GZIP_BUDGET_BYTES = 120 * 1024
API_URL = "https://api.open-meteo.com/v1/forecast"
ATTRIBUTION = ("Meteorological source data: Open-Meteo.com / Data license: "
               "CC BY 4.0 / Processed and aggregated by MeteoRisk Light.")

# (chiave compatta, parametro daily Open-Meteo, decimali)
# `weathercode` NON viene usato come chiave: `code` evita qualunque collisione
# con i 49 campi orari di meteorisk-points.json.
DAILY_VARS = (
    ("tmax", "temperature_2m_max", 1),
    ("tmin", "temperature_2m_min", 1),
    ("prcp", "precipitation_sum", 2),
    ("pprob", "precipitation_probability_max", 0),
    ("wind", "windspeed_10m_max", 1),
    ("gust", "windgusts_10m_max", 1),
    ("code", "weathercode", 0),
    ("et0", "et0_fao_evapotranspiration", 2),
)
DAILY_PARAMS = ",".join(v for _k, v, _d in DAILY_VARS)
OPTIONAL_VARS = ("et0_fao_evapotranspiration",)

# Etichette/unità dichiarate nel file (nessun nome di variabile oraria:
# il file resta leggibile e la guardia contro i campi di meteorisk-points.json
# e' verificabile anche come testo).
FIELD_META = {
    "tmax": ("Temperatura massima", "C", 1),
    "tmin": ("Temperatura minima", "C", 1),
    "prcp": ("Precipitazione cumulata", "mm", 2),
    "pprob": ("Probabilita' massima di precipitazione", "%", 0),
    "wind": ("Vento massimo a 10 m", "km/h", 1),
    "gust": ("Raffica massima a 10 m", "km/h", 1),
    "code": ("Codice meteo WMO", "wmo", 0),
    "et0": ("Evapotraspirazione FAO ET0", "mm", 2),
}

# Guardia esplicita: nessuna chiave del dataset orario puo' comparire qui.
FORBIDDEN_KEYS = frozenset(common.HOURLY_FIELDS) | frozenset(
    ("hourly", "models", "points", "provinceIdx", "coordIdx"))


class WeeklyError(RuntimeError):
    """Errore previsto (rete/API/schema): il file non va scritto."""


def _num(value, decimals):
    """Numero arrotondato o None. I booleani NON sono numeri validi."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if decimals == 0:
        return int(round(value))
    out = round(float(value), decimals)
    if out == int(out):
        return int(out)
    return out


def plan_batches(regions, batch_size=DEFAULT_BATCH_SIZE):
    """Divide le province in batch BILANCIATI (max `batch_size`), <=3 richieste.

    Con il default 100 e 107 province: 2 richieste da 54+53, entrambe nel range
    50-100 dichiarato. Il partizionamento bilanciato evita l'ultima richiesta
    da pochi punti (100+7) senza aumentare il numero di richieste.
    """
    if not isinstance(batch_size, int) or not (MIN_BATCH_SIZE <= batch_size <= MAX_BATCH_SIZE):
        raise ValueError("batch_size deve stare fra %d e %d (ricevuto %r)"
                         % (MIN_BATCH_SIZE, MAX_BATCH_SIZE, batch_size))
    n = len(regions)
    if n == 0:
        raise ValueError("nessuna provincia da richiedere")
    count = int(math.ceil(float(n) / float(batch_size)))
    if count > MAX_REQUESTS:
        raise ValueError("%d province con batch %d richiederebbero %d richieste (> %d)"
                         % (n, batch_size, count, MAX_REQUESTS))
    base, extra = divmod(n, count)
    sizes = [base + (1 if i < extra else 0) for i in range(count)]
    out = []
    start = 0
    for size in sizes:
        out.append(regions[start:start + size])
        start += size
    return out


def build_url(batch, model=DEFAULT_MODEL, days=FORECAST_DAYS, include_et0=True):
    daily = DAILY_PARAMS
    if not include_et0:
        daily = ",".join(v for _k, v, _d in DAILY_VARS if v not in OPTIONAL_VARS)
    params = {
        "latitude": ",".join("%.5f" % float(r["lat"]) for r in batch),
        "longitude": ",".join("%.5f" % float(r["lon"]) for r in batch),
        "daily": daily,
        "forecast_days": str(int(days)),
        "timezone": common.TIMEZONE,
        "models": model,
    }
    return API_URL + "?" + urllib.parse.urlencode(params)


def _http_json(url, timeout=DEFAULT_TIMEOUT_S):
    req = urllib.request.Request(url, headers={"User-Agent": "MeteoRisk-Light/weekly"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _retry_after(exc):
    try:
        raw = exc.headers.get("Retry-After") if exc.headers else None
        if raw is None:
            return None
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return None


def _error_body(exc):
    try:
        return exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def fetch_batch(batch, model=DEFAULT_MODEL, days=FORECAST_DAYS,
                timeout=DEFAULT_TIMEOUT_S, attempts=DEFAULT_ATTEMPTS,
                include_et0=True, http=None, sleep=None):
    """Una richiesta multi-location con retry limitati.

    Ritorna (data, meta) con meta = {requests, http_429, et0, url}.
    Solleva WeeklyError se i tentativi si esauriscono o c'e' un errore non
    transitorio. `http` e `sleep` sono iniettabili nei test.
    """
    http = http or _http_json
    sleep = sleep or time.sleep
    include_et0 = bool(include_et0)
    meta = {"requests": 0, "http_429": 0, "et0": include_et0, "url": None}
    last = None
    for attempt in range(max(1, int(attempts))):
        url = build_url(batch, model=model, days=days, include_et0=include_et0)
        meta["url"] = url
        try:
            meta["requests"] += 1
            data = http(url, timeout=timeout)
            return data, meta
        except urllib.error.HTTPError as exc:
            body = _error_body(exc)
            last = "HTTP %d: %s" % (exc.code, body[:300])
            if exc.code == 400 and include_et0 and "et0" in body.lower():
                # "se disponibile": il modello non ha ET0 -> una sola volta
                # senza il parametro opzionale, il batch prosegue.
                include_et0 = False
                meta["et0"] = False
                continue
            if exc.code == 429:
                meta["http_429"] += 1
                wait = _retry_after(exc) or (20.0 * (attempt + 1))
            elif 500 <= exc.code < 600:
                wait = 5.0 * (attempt + 1)
            else:
                raise WeeklyError("Open-Meteo ha rifiutato la richiesta: %s" % last)
            if attempt + 1 >= int(attempts):
                break
            sleep(wait)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
            if attempt + 1 >= int(attempts):
                break
            sleep(5.0 * (attempt + 1))
    raise WeeklyError("Open-Meteo non raggiungibile dopo %d tentativi: %s"
                      % (max(1, int(attempts)), last))


def fetch_all(regions, batch_size=DEFAULT_BATCH_SIZE, model=DEFAULT_MODEL,
              days=FORECAST_DAYS, timeout=DEFAULT_TIMEOUT_S,
              attempts=DEFAULT_ATTEMPTS, http=None, sleep=None):
    """Tutte le province in <=3 richieste. Ritorna (responses, stats).

    `responses` e' nell'ordine di `regions` (l'API conserva l'ordine delle
    coordinate). Se una risposta non ha un elemento per ogni coordinata il
    modulo si ferma: mai un file parziale.
    """
    batches = plan_batches(regions, batch_size)
    responses = []
    stats = {"requests": 0, "http_429": 0, "batches": len(batches),
             "et0": True}
    for batch in batches:
        data, meta = fetch_batch(batch, model=model, days=days,
                                 timeout=timeout, attempts=attempts,
                                 include_et0=stats["et0"], http=http, sleep=sleep)
        stats["requests"] += meta["requests"]
        stats["http_429"] += meta["http_429"]
        stats["et0"] = stats["et0"] and meta["et0"]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or len(data) != len(batch):
            got = len(data) if isinstance(data, list) else "non-lista"
            raise WeeklyError("risposta API con %s elementi per %d coordinate"
                              % (got, len(batch)))
        responses.extend(data)
    if len(responses) != len(regions):
        raise WeeklyError("risposta API con %d localita' per %d province"
                          % (len(responses), len(regions)))
    return responses, stats


def _day_records(daily, day_index):
    """Record compatto di un giorno: i campi nulli vengono OMESSI."""
    rec = {}
    for key, param, decimals in DAILY_VARS:
        arr = daily.get(param)
        if not isinstance(arr, list) or day_index >= len(arr):
            continue
        value = _num(arr[day_index], decimals)
        if value is None:
            continue
        rec[key] = value
    return rec


def build_payload(regions, responses, generated_at=None, model=DEFAULT_MODEL,
                  stats=None, days=FORECAST_DAYS):
    """Costruisce il documento compatto da regioni + risposte Open-Meteo."""
    if len(regions) != len(responses):
        raise WeeklyError("regioni %d != risposte %d" % (len(regions), len(responses)))
    days_out = None
    provinces = []
    coverage = {key: 0 for key, _p, _d in DAILY_VARS}
    seen = set()
    for region, resp in zip(regions, responses):
        sigla = str(region.get("sigla") or "").strip()
        if not sigla:
            raise WeeklyError("provincia senza sigla: %r" % (region,))
        if sigla in seen:
            raise WeeklyError("sigla duplicata: %s" % sigla)
        seen.add(sigla)
        daily = (resp or {}).get("daily") or {}
        times = daily.get("time")
        if not isinstance(times, list) or len(times) != days:
            got = len(times) if isinstance(times, list) else "assente"
            raise WeeklyError("%s: %s giorni nella risposta (attesi %d)"
                              % (sigla, got, days))
        if days_out is None:
            days_out = list(times)
        elif list(times) != days_out:
            raise WeeklyError("%s: date diverse dalle altre province" % sigla)
        records = []
        for d in range(days):
            rec = _day_records(daily, d)
            for key in rec:
                coverage[key] += 1
            records.append(rec)
        provinces.append([sigla, records])
    if days_out is None:
        raise WeeklyError("nessuna risposta utile")
    present_fields = [key for key, _p, _d in DAILY_VARS if coverage[key] > 0]
    fields = {}
    for key in present_fields:
        label, unit, decimals = FIELD_META[key]
        fields[key] = {"label": label, "unit": unit, "decimals": decimals}
    payload = {
        "version": VERSION,
        "generated_at": generated_at or _now_utc(),
        "days": days_out,
        "source": model,
        "provider": "Open-Meteo",
        "attribution": ATTRIBUTION,
        "timezone": common.TIMEZONE,
        "forecast_days": days,
        "count": len(provinces),
        "fields": fields,
        "province_fields": ["sigla", "days"],
        "provinces": provinces,
    }
    if stats is not None:
        payload["requests"] = {"total": stats.get("requests"),
                               "http_429": stats.get("http_429")}
    return payload


def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def serialize(payload):
    """JSON compatto + newline finale, UTF-8 senza BOM, solo LF."""
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def gzip_size(data):
    return len(gzip.compress(data, 9))


def _all_keys(obj, acc):
    if isinstance(obj, dict):
        for key, value in obj.items():
            acc.add(key)
            _all_keys(value, acc)
    elif isinstance(obj, list):
        for item in obj:
            _all_keys(item, acc)
    return acc


def validate_payload(payload, data=None, max_gzip_bytes=GZIP_BUDGET_BYTES,
                     expected_provinces=None):
    """Contratto del file: schema, 7 giorni, campi, budget, nessun campo orario."""
    if payload.get("version") != VERSION:
        raise WeeklyError("version inattesa: %r" % (payload.get("version"),))
    days = payload.get("days")
    if not isinstance(days, list) or len(days) != FORECAST_DAYS:
        raise WeeklyError("days deve avere %d date" % FORECAST_DAYS)
    for day in days:
        try:
            _dt.date.fromisoformat(str(day))
        except ValueError:
            raise WeeklyError("data non ISO in days: %r" % (day,))
    provinces = payload.get("provinces")
    if not isinstance(provinces, list) or not provinces:
        raise WeeklyError("provinces vuoto")
    if expected_provinces is not None and len(provinces) != expected_provinces:
        raise WeeklyError("%d province invece di %d" % (len(provinces), expected_provinces))
    if payload.get("count") != len(provinces):
        raise WeeklyError("count %r != province %d" % (payload.get("count"), len(provinces)))
    declared = set((payload.get("fields") or {}).keys())
    used = set()
    seen = set()
    for item in provinces:
        if not isinstance(item, list) or len(item) != 2:
            raise WeeklyError("provincia non compatta [sigla, days]: %r" % (item,))
        sigla, records = item
        if not isinstance(sigla, str) or not sigla:
            raise WeeklyError("sigla non valida: %r" % (sigla,))
        if sigla in seen:
            raise WeeklyError("sigla duplicata: %s" % sigla)
        seen.add(sigla)
        if not isinstance(records, list) or len(records) != FORECAST_DAYS:
            raise WeeklyError("%s: %r record (attesi %d)"
                              % (sigla, len(records) if isinstance(records, list) else records,
                                 FORECAST_DAYS))
        for rec in records:
            if not isinstance(rec, dict):
                raise WeeklyError("%s: record non oggetto" % sigla)
            for key, value in rec.items():
                if key in FORBIDDEN_KEYS:
                    raise WeeklyError("%s: campo del dataset orario %r" % (sigla, key))
                if value is None:
                    raise WeeklyError("%s: campo %s nullo (va omesso)" % (sigla, key))
                used.add(key)
    extra = used - declared
    if extra:
        raise WeeklyError("campi usati ma non dichiarati: %s" % sorted(extra))
    missing = declared - used
    if missing:
        raise WeeklyError("campi dichiarati senza valori: %s" % sorted(missing))
    if data is None:
        data = serialize(payload)
    if b"\r" in data:
        raise WeeklyError("il file contiene CR (richiesto LF)")
    if data.startswith(b"\xef\xbb\xbf"):
        raise WeeklyError("il file contiene BOM")
    size = gzip_size(data)
    if size > max_gzip_bytes:
        raise WeeklyError("gzip %d B oltre il budget %d B" % (size, max_gzip_bytes))
    return {"raw_bytes": len(data), "gzip_bytes": size, "provinces": len(provinces),
            "fields": len(declared)}


def write_atomic(path, data):
    """Scrittura atomica: un errore non lascia un file troncato ne' un .tmp."""
    path = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tendenza 7 giorni per le 107 province.")
    parser.add_argument("--out", default=str(common.DATA_LATEST / "weekly.json"),
                        help="file di output (default data/latest/weekly.json)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="modello Open-Meteo (default best_match)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help="coordinate per richiesta (50-100, default 100)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--dry-run", action="store_true", help="misura senza scrivere il file")
    parser.add_argument("--expect-provinces", type=int, default=None,
                        help="numero atteso di province (default: quante nel file regioni)")
    args = parser.parse_args(argv)

    regions = common._load_regions()
    expected = args.expect_provinces if args.expect_provinces is not None else len(regions)
    try:
        responses, stats = fetch_all(regions, batch_size=args.batch_size,
                                     model=args.model, timeout=args.timeout,
                                     attempts=args.attempts)
        payload = build_payload(regions, responses, model=args.model, stats=stats)
        data = serialize(payload)
        info = validate_payload(payload, data=data, expected_provinces=expected)
    except WeeklyError as exc:
        print("[weekly] FALLITO (file non aggiornato): %s" % exc, file=sys.stderr)
        return 2

    if args.dry_run:
        print("[weekly] dry-run: %d richieste, %d 429, %d province, %d giorni, "
              "raw=%d B gzip=%d B"
              % (stats["requests"], stats["http_429"], info["provinces"],
                 len(payload["days"]), info["raw_bytes"], info["gzip_bytes"]))
        return 0

    try:
        write_atomic(args.out, data)
    except OSError as exc:
        print("[weekly] FALLITO in scrittura (%s): %s" % (args.out, exc), file=sys.stderr)
        return 2
    print("[weekly] scritto %s · %d richieste · %d 429 · %d province · %d giorni "
          "· raw=%d B · gzip=%d B · campi=%d"
          % (args.out, stats["requests"], stats["http_429"], info["provinces"],
             len(payload["days"]), info["raw_bytes"], info["gzip_bytes"],
             info["fields"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
