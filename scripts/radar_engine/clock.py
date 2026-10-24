#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — clock.py (1.2.4.20)

SORGENTE DI TEMPO UNICA del motore.

Problema che risolve (MISURATO il 2026-10-24, `retro/REPORT.md` §5.2): cinque
punti del motore leggevano l'orologio di sistema (`datetime.now(utc)`) per
decidere QUALI dati scaricare. Un re-run di un evento del 2026-07-01T13:20Z
partiva quindi dall'ultimo slot di oggi e prendeva l'ambiente di oggi: non era
un replay, era un run di oggi con i radar di luglio.

Qui il tempo entra in UN SOLO modo:

    clock = Clock.at_iso("2026-07-01T13:20:00Z")   # replay deterministico
    clock = Clock.live()                            # comportamento storico

e viene iniettato nel dizionario di configurazione sotto la chiave riservata
`_clock` (`inject(config, clock)`). Ogni consumatore fa
`clock.from_config(config)` e legge `now_ms()` / `now_iso()` / `anchor_ms()`.
Nessun modulo del percorso attivo chiama piu' `datetime.now()` quando il clock
e' in modalita' replay: il grep di verifica e' in `docs/REPLAY_AS_OF.md`.

`now_ms()` in modalita' live continua a leggere l'orologio (e' cio' che serve
al run operativo). In modalita' replay `now_ms()` e' COSTANTE e vale l'istante
richiesto: due run con lo stesso `--as-of` devono produrre byte identici.
"""

import datetime as _dt

_UTC = _dt.timezone.utc

# Chiave riservata nel dict di configurazione (non serializzata negli output).
CONFIG_KEY = "_clock"

_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def parse_iso8601_utc(value):
    """ISO-8601 -> epoch ms UTC. None/vuoto -> None.

    Accetta 'YYYY-MM-DDTHH:MM[:SS][Z|+00:00]' e 'YYYY-MM-DD HH:MM[:SS]'.
    Un istante SENZA fuso viene interpretato come UTC e il chiamante lo
    dichiara (`as_of_mode.timezone_assumed`): non si inventa un fuso.
    Solleva ValueError su formato non interpretabile (il CLI esce con errore,
    non ripiega in silenzio sull'ora corrente)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    txt = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    assumed = False
    try:
        dt = _dt.datetime.fromisoformat(txt)
    except ValueError:
        raise ValueError(f"invalid_iso8601:{value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
        assumed = True
    return int(dt.timestamp() * 1000), assumed


def iso_from_ms(ms):
    """epoch ms -> 'YYYY-MM-DDTHH:MM:SSZ' (None -> None)."""
    if ms is None:
        return None
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0, _UTC).strftime(_ISO_Z)


def datetime_from_ms(ms):
    """epoch ms -> datetime UTC."""
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0, _UTC)


class Clock(object):
    """Orologio iniettabile: `live` oppure fermo su un istante (replay)."""

    def __init__(self, now_ms=None, requested_iso=None, timezone_assumed=False):
        self._fixed_ms = None if now_ms is None else int(now_ms)
        self.requested_iso = requested_iso
        self.timezone_assumed = bool(timezone_assumed)

    # --- costruttori -------------------------------------------------------
    @classmethod
    def live(cls):
        """Comportamento storico: l'istante e' quello di esecuzione."""
        return cls(None)

    @classmethod
    def at_ms(cls, ms, requested_iso=None):
        return cls(int(ms), requested_iso=requested_iso or iso_from_ms(ms))

    @classmethod
    def at_iso(cls, value):
        parsed = parse_iso8601_utc(value)
        if parsed is None:
            raise ValueError("as_of_missing")
        ms, assumed = parsed
        return cls(ms, requested_iso=value, timezone_assumed=assumed)

    # --- stato -------------------------------------------------------------
    @property
    def replay(self):
        """True se l'istante e' FISSO (modalita' --as-of)."""
        return self._fixed_ms is not None

    # --- letture di tempo --------------------------------------------------
    def now_ms(self):
        if self._fixed_ms is not None:
            return int(self._fixed_ms)
        return int(_dt.datetime.now(_UTC).timestamp() * 1000)

    def now_utc(self):
        return datetime_from_ms(self.now_ms())

    def now_iso(self):
        return iso_from_ms(self.now_ms())

    def anchor_ms(self, period_s=None):
        """Istante da cui ancorare gli slot del radar.

        live  -> None (il chiamante usa findLastProductByType come sempre).
        replay-> l'istante richiesto, arrotondato per DIFETTO al passo del
                 prodotto: `--as-of 13:23` con PT5M significa "lo slot 13:20".
                 Nessun istante inventato: l'arrotondamento e' dichiarato."""
        if self._fixed_ms is None:
            return None
        if period_s:
            step = int(period_s) * 1000
            return (int(self._fixed_ms) // step) * step
        return int(self._fixed_ms)

    def describe(self, period_s=None):
        """Blocco `as_of_mode` per l'output (None in modalita' live)."""
        if self._fixed_ms is None:
            return None
        out = {
            "requested": self.requested_iso or iso_from_ms(self._fixed_ms),
            "requested_ms": int(self._fixed_ms),
            "requested_iso": iso_from_ms(self._fixed_ms),
            "radar_anchor_iso": iso_from_ms(self.anchor_ms(period_s)),
            "slot_period_s": (int(period_s) if period_s else None),
            "timezone_assumed_utc": bool(self.timezone_assumed),
            "environment_source": None,
            "degraded": [],
        }
        return out


def inject(config, clock):
    """Inietta il clock nel dict di configurazione (chiave riservata)."""
    config[CONFIG_KEY] = clock
    return config


def from_config(config):
    """Clock iniettato oppure LIVE (default: nessun cambio di comportamento)."""
    clock = (config or {}).get(CONFIG_KEY)
    return clock if isinstance(clock, Clock) else Clock.live()
