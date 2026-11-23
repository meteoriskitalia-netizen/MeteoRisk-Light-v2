#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tempo locale Europe/Rome senza dipendenze obbligatorie da tzdata.

Il repo pubblica con ``timezone: "Europe/Rome"``: ogni fascia, giorno e ora
locale va calcolata in quel fuso, non in UTC. ``zoneinfo`` e' la strada
primaria; se il fuso non e' installato (host Windows senza tzdata) si usa la
regola UE esplicita, che e' la stessa cosa per il nostro intervallo di date:

    ora legale: ultima domenica di marzo 01:00 UTC -> ultima domenica di
                ottobre 01:00 UTC, offset +02:00; fuori, +01:00.

Nessun valore inventato: se una data non e' parsabile si solleva ValueError.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc

try:  # pragma: no cover - dipende dall'host
    from zoneinfo import ZoneInfo

    _ROME = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover - host senza tzdata
    _ROME = None

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d+))?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)


def _last_sunday(year: int, month: int) -> date:
    """Ultima domenica del mese (naive date)."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != 6:  # Monday=0, Sunday=6
        d -= timedelta(days=1)
    return d


def _fallback_offset_seconds(dt_utc: datetime) -> int:
    """Offset Europe/Rome con la regola UE, in secondi."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    y = dt_utc.year
    start = datetime.combine(_last_sunday(y, 3), datetime.min.time(),
                             tzinfo=UTC) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(y, 10), datetime.min.time(),
                           tzinfo=UTC) + timedelta(hours=1)
    return 7200 if start <= dt_utc < end else 3600


def offset_seconds(dt_utc: datetime) -> int:
    """Offset del fuso Europe/Rome per un istante UTC (secondi)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    dt_utc = dt_utc.astimezone(UTC)
    if _ROME is not None:
        return int(dt_utc.astimezone(_ROME).utcoffset().total_seconds())
    return _fallback_offset_seconds(dt_utc)


def parse_utc(value) -> datetime:
    """Parse ISO-8601 (con o senza offset) -> datetime UTC aware.

    Una stringa senza offset e' interpretata come UTC (il repo scrive ``Z``).
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None:
        return datetime.now(UTC)
    text = str(value).strip()
    m = _ISO_RE.match(text)
    if not m:
        raise ValueError(f"timestamp_non_parsabile:{value!r}")
    y, mo, d, hh, mm, ss, frac, off = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    dt = datetime(int(y), int(mo), int(d), int(hh), int(mm),
                  int(ss or 0), micro, tzinfo=UTC)
    if off and off != "Z":
        sign = 1 if off[0] == "+" else -1
        body = off[1:].replace(":", "")
        dt -= sign * timedelta(hours=int(body[:2]), minutes=int(body[2:4]))
    return dt


def format_utc(dt: datetime) -> str:
    """ISO UTC con ``Z`` e secondi (formato usato dai file del repo)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def day_bounds_utc(day: str):
    """(start_utc, end_utc) del giorno LOCALE ``YYYY-MM-DD`` in Europe/Rome.

    ``end_utc`` e' la mezzanotte locale del giorno successivo (intervallo
    semiaperto). L'offset si legge a mezzogiorno UTC: le transizioni UE
    avvengono alle 01:00 UTC, mai a mezzanotte locale.
    """
    d = date.fromisoformat(str(day))
    noon = datetime(d.year, d.month, d.day, 12, tzinfo=UTC)
    off = offset_seconds(noon)
    start = datetime(d.year, d.month, d.day, tzinfo=UTC) - timedelta(seconds=off)
    nxt = d + timedelta(days=1)
    noon2 = datetime(nxt.year, nxt.month, nxt.day, 12, tzinfo=UTC)
    off2 = offset_seconds(noon2)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=UTC) - timedelta(seconds=off2)
    return start, end


def local_day(dt_utc: datetime) -> str:
    """Giorno locale Europe/Rome di un istante UTC."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    off = offset_seconds(dt_utc)
    return (dt_utc + timedelta(seconds=off)).date().isoformat()


def local_hhmm(dt_utc: datetime) -> str:
    """Ora locale Europe/Rome ``HH:MM`` di un istante UTC."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    off = offset_seconds(dt_utc)
    return (dt_utc + timedelta(seconds=off)).strftime("%H:%M")


def local_to_utc(day: str, hhmm: str) -> datetime:
    """Ora locale ``YYYY-MM-DD`` + ``HH:MM`` -> datetime UTC."""
    d = date.fromisoformat(str(day))
    hh, mm = [int(x) for x in str(hhmm).split(":")[:2]]
    noon = datetime(d.year, d.month, d.day, 12, tzinfo=UTC)
    off = offset_seconds(noon)
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=UTC) - timedelta(seconds=off)


def now_utc(value=None) -> datetime:
    """``--now`` opzionale: None -> adesso; altrimenti ISO-8601."""
    if value is None or value == "":
        return datetime.now(UTC)
    return parse_utc(value)


def yesterday_local(dt_utc: datetime) -> str:
    """Giorno locale precedente a quello di ``dt_utc``."""
    today = date.fromisoformat(local_day(dt_utc))
    return (today - timedelta(days=1)).isoformat()
