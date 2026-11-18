#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Produttore di teleconnessioni — fonti pubbliche NOAA, nessun token.

Scarica le sorgenti pubbliche NOAA (CPC, NCEI ERSSTv5 e CSL/SSWC; HTTP 200
verificato il 2026-11-17/18), le parsa e scrive
``data/climate/teleconnections.json``.

Aggiunte 1.3.0.11: QBO 30/50 hPa (CPC, m/s, solo blocco ORIGINAL DATA),
AMO (NCEI ERSSTv5, gradi C), PNA daily (cwlinks, lead==0 e member==0) e
timeline SSW storica (CSL/SSWC, Butler et al. 2017 doi 10.5194/essd-9-63-2017;
definizione Charlton & Polvani 2007). NESSUNA attivita' solare. NAM 10 hPa e
ITCZ NON implementati e dichiarati in ``not_implemented`` con stato e motivo.

REGOLA OBBLIGATORIA (cwlinks AO/NAO):
    nei CSV ``norm.daily.*.gefs.*`` si pubblicano SOLO le righe con
    ``lead == 0``. Le righe con lead > 0 sono previsioni GEFS e non devono
    mai finire nell'archivio come indici osservati.
    Inoltre si prende ``member == 0`` (il controllo non perturbato): a lead 0
    gli altri 30 member sono analisi perturbate dell'ensieme GEFS e non sono
    l'indice osservato (misurato: 31 member diversi per la stessa data,
    min -0.7510 / max -0.6234 contro member0 = -0.6928 per il 2026-07-21).

Valori mancanti (sentinel -99.9 / -99.90 / -999.9 nei fixed-width, e qualsiasi
valore <= -99) NON vengono trasformati in ``null``: il punto viene saltato.
Nessun valore ``null`` e' mai emesso nell'array ``values``.

Licenza: NOAA public domain (17 USC 105), attribuzione richiesta.

Uso:
    python build_teleconnections.py --out <path/to/teleconnections.json>
"""

from __future__ import annotations

import argparse
import csv
import html as html_mod
import io
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
SOURCE_LABEL = "NOAA CPC + NOAA NCEI + NOAA CSL/SSWC"
LICENSE = "NOAA public domain — attribution required"
ATTRIBUTION = ("NOAA Climate Prediction Center; NOAA NCEI ERSSTv5; "
               "NOAA CSL Sudden Stratospheric Warming Compendium")

# 1.3.0.11 — nuove sorgenti pubbliche. Nessuna richiesta con token/credenziali.
QBO30_URL = "https://ftp.cpc.ncep.noaa.gov/wd52dg/data/indices/qbo.u30.index"
QBO50_URL = "https://ftp.cpc.ncep.noaa.gov/wd52dg/data/indices/qbo.u50.index"
AMO_URL = "https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.amo.dat"
CW_PNA_URL = ("https://ftp.cpc.ncep.noaa.gov/cwlinks/"
              "norm.daily.pna.gefs.z500.120days.csv")
SSW_URL = "https://www.csl.noaa.gov/groups/csl8/sswcompendium/majorevents.html"

SSW_CITATION = ("Butler, A. H., Sjoberg, J. P., Seidel, D. J., and Rosenlof, "
                "K. H. (2017), A sudden stratospheric warming compendium, "
                "Earth Syst. Sci. Data, 9, 63-76, doi:10.5194/essd-9-63-2017")
SSW_DEFINITION = ("SSW maggiore invernale: inversione tra novembre e marzo del "
                  "vento zonale medio 10 hPa a 60 N da westerly a easterly, con "
                  "ritorno a westerly per almeno 20 giorni consecutivi fra eventi "
                  "e almeno 10 giorni prima del 30 aprile (Charlton & Polvani "
                  "2007, doi:10.1029/2006GL028526).")

# Buchi dichiarati: NON sono implementati e NON vanno inventati da proxy.
NOT_IMPLEMENTED = [
    {
        "id": "nam_10hpa",
        "status": "RICHIEDE TOKEN",
        "reason": ("Nessun indice NAM 10 hPa giornaliero pubblicato senza credenziali "
                   "tra le sorgenti pubbliche NOAA verificate in questa sessione: "
                   "servirebbe una rianalisi con accesso autenticato (per esempio "
                   "ERA5/CDS). Nessun proxy da geopotenziale 500 hPa e' stato "
                   "sostituito al NAM 10 hPa."),
    },
    {
        "id": "itcz",
        "status": "NON VERIFICATO",
        "reason": ("Nessuna definizione e licenza di un indice ITCZ giornaliero e' "
                   "stata verificata su una fonte pubblica in questa sessione. "
                   "Nessuna posizione dell'ITCZ viene quindi pubblicata o "
                   "disegnata: il dato resta non disponibile con motivo."),
    },
]
SOLAR_EXCLUSION = {
    "implemented": False,
    "reason": ("Nessuna attivita' solare (macchie, F10.7, irradianza) e' inclusa: "
               "fuori dal perimetro di questa release e nessuna serie solare e' "
               "stata aggiunta al JSON."),
}

# Cambio metodologia CPC con effetto dal 15/2/2027: dichiarato perche' chi
# consuma le serie sappia che oltre quella data le baseline vanno riverificate.
METHODOLOGY_NOTICE = {
    "effective_from": "2027-02-15",
    "statement": (
        "NOAA CPC ha annunciato un cambio di metodologia con effetto dal "
        "15/2/2027. Le serie qui pubblicate usano le metodologie e le baseline "
        "dichiarate nei singoli campi 'baseline' e riflettono lo stato delle "
        "sorgenti alla data di generazione. Dal 15/2/2027 il produttore va "
        "riverificato e le baseline ricalibrate prima di confrontare le serie "
        "con quelle generate in precedenza."
    ),
    "requires_reverification": True,
    "verified_details": False,
}

UA = "MeteoRisk-Light teleconn producer/1.0 (+NOAA CPC public data)"

CW_AO_URL = (
    "https://ftp.cpc.ncep.noaa.gov/cwlinks/"
    "norm.daily.ao.gefs.z1000.120days.csv"
)
CW_NAO_URL = (
    "https://ftp.cpc.ncep.noaa.gov/cwlinks/"
    "norm.daily.nao.gefs.z500.120days.csv"
)
TELE_NH_URL = "https://ftp.cpc.ncep.noaa.gov/wd52dg/data/indices/tele_index.nh"
WKSST_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
SOI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/soi"

# id -> (url, label, baseline, parser)
SOURCES = (
    {
        "id": "ao_daily",
        "label": "Arctic Oscillation / Northern Annular Mode — daily, lead=0",
        "baseline": "CPC daily standardized index from GEFS Z1000; baseline non esplicitata nel file",
        "url": CW_AO_URL,
        "kind": "cwlinks",
    },
    {
        "id": "nao_daily",
        "label": "North Atlantic Oscillation — daily, lead=0",
        "baseline": "CPC daily standardized index from GEFS Z500; baseline non esplicitata nel file",
        "url": CW_NAO_URL,
        "kind": "cwlinks",
    },
    {
        "id": "nao_monthly",
        "label": "North Atlantic Oscillation — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 0,
    },
    {
        "id": "ea_monthly",
        "label": "East Atlantic Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 1,
    },
    {
        "id": "wp_monthly",
        "label": "West Pacific Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 2,
    },
    {
        "id": "epnp_monthly",
        "label": "East Pacific / North Pacific Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 3,
    },
    {
        "id": "pna_monthly",
        "label": "Pacific / North American Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 4,
    },
    {
        "id": "eawr_monthly",
        "label": "East Atlantic / West Russia Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 5,
    },
    {
        "id": "scand_monthly",
        "label": "Scandinavia Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 6,
    },
    {
        "id": "tnh_monthly",
        "label": "Tropical / Northern Hemisphere Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 7,
    },
    {
        "id": "poleur_monthly",
        "label": "Polar / Eurasia Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 8,
    },
    {
        "id": "pt_monthly",
        "label": "Pacific Transition Pattern — monthly",
        "baseline": "1981-2010 (dichiarato nell'header di tele_index.nh)",
        "url": TELE_NH_URL,
        "kind": "tele_nh",
        "column": 9,
    },
    {
        "id": "nino12_weekly",
        "label": "ENSO Niño 1+2 — weekly SST anomaly",
        "baseline": "1991-2020 (wksst9120.for)",
        "url": WKSST_URL,
        "kind": "wksst",
        "column": 0,
    },
    {
        "id": "nino3_weekly",
        "label": "ENSO Niño 3 — weekly SST anomaly",
        "baseline": "1991-2020 (wksst9120.for)",
        "url": WKSST_URL,
        "kind": "wksst",
        "column": 1,
    },
    {
        "id": "nino34_weekly",
        "label": "ENSO Niño 3.4 — weekly SST anomaly",
        "baseline": "1991-2020 (wksst9120.for)",
        "url": WKSST_URL,
        "kind": "wksst",
        "column": 2,
    },
    {
        "id": "nino4_weekly",
        "label": "ENSO Niño 4 — weekly SST anomaly",
        "baseline": "1991-2020 (wksst9120.for)",
        "url": WKSST_URL,
        "kind": "wksst",
        "column": 3,
    },
    {
        "id": "soi_monthly",
        "label": "Southern Oscillation Index — Tahiti minus Darwin SLP",
        "baseline": "standardized anomaly; baseline non esplicitata nel file",
        "url": SOI_URL,
        "kind": "soi",
    },
    # ---- 1.3.0.11: nuove serie pubbliche, nessun token ----
    {
        "id": "qbo30_monthly",
        "label": "QBO 30 hPa — vento zonale equatoriale (ORIGINAL DATA)",
        "baseline": "CPC ORIGINAL DATA, valori assoluti; nessuna baseline di standardizzazione",
        "url": QBO30_URL,
        "kind": "qbo",
        "source": "NOAA CPC",
        "license": LICENSE,
        "unit": "m/s",
    },
    {
        "id": "qbo50_monthly",
        "label": "QBO 50 hPa — vento zonale equatoriale (ORIGINAL DATA)",
        "baseline": "CPC ORIGINAL DATA, valori assoluti; nessuna baseline di standardizzazione",
        "url": QBO50_URL,
        "kind": "qbo",
        "source": "NOAA CPC",
        "license": LICENSE,
        "unit": "m/s",
    },
    {
        "id": "amo_monthly",
        "label": "AMO — Atlantic Multidecadal Oscillation (ERSSTv5, SSTA 0-60 N)",
        "baseline": "ERSSTv5, anomalia SSTA 0-60 N; nessuna detrendizzazione applicata",
        "url": AMO_URL,
        "kind": "amo",
        "source": "NOAA NCEI ERSSTv5",
        "license": LICENSE,
        "unit": "°C",
    },
    {
        "id": "pna_daily",
        "label": "Pacific / North American Pattern — daily, lead=0, member=0",
        "baseline": "CPC daily standardized index from GEFS Z500; baseline non esplicitata nel file",
        "url": CW_PNA_URL,
        "kind": "cwlinks",
        "source": "NOAA CPC",
        "license": LICENSE,
        "unit": "standardized index",
    },
    {
        "id": "ssw_events",
        "label": "SSW — date centrali degli SSW maggiori invernali (storico)",
        "baseline": "date centrali pubblicate nella tabella CSL/SSWC (aggiornata settembre 2023)",
        "url": SSW_URL,
        "kind": "ssw",
        "source": "NOAA CSL / SSWC",
        "license": "NOAA public domain (dati); articolo Butler et al. 2017 CC BY 3.0",
        "unit": "eventi",
        "citation": SSW_CITATION,
        "definition": SSW_DEFINITION,
    },
)

# Fixed-width layouts, misurati sui file reali scaricati il 2026-11-17.
# tele_index.nh: 75 caratteri/linea. Anno [0:4], mese [4:8], poi 11 campi
# da 6 caratteri a partire da 8 (gli ultimi 10 sono gli indici, il 11mo e'
# "explained variance" che non ci serve).
TELE_NH_FIELD_START = 8
TELE_NH_FIELD_WIDTH = 6
TELE_NH_NFIELDS = 10
# wksst9120.for: 62 caratteri/linea. Data [1:10], poi 4 blocchi da 13
# caratteri a partire da 14: SST [start:start+5], SSTA [start+5:start+13].
WKSST_DATE_SLICE = (1, 10)
WKSST_BLOCK_START = 14
WKSST_BLOCK_STEP = 13
# soi: 76 caratteri/linea. Anno [0:4], poi 12 campi da 6 a partire da 4.
SOI_YEAR_END = 4
SOI_FIELD_START = 4
SOI_FIELD_WIDTH = 6

# qbo.u{30,50}.index: 88 caratteri/linea, TRE blocchi da 54 righe ciascuno:
# ORIGINAL DATA (m/s), ANOMALY e STANDARDIZED DATA. Si pubblica SOLO il primo:
# gli altri due hanno unita' diversa e vanno saltati (misurato il 2026-11-18).
QBO_FIELD_START = 4
QBO_FIELD_WIDTH = 7
QBO_NFIELDS = 12
# ersst.v5.amo.dat: "anno mese SSTA" separato da spazi (2 righe di header).

MISSING_FLOOR = -99.0  # -99.9, -99.90, -999.9: tutti < -99


class SourceError(RuntimeError):
    """Errore di rete/HTTP su una sorgente: contiene URL e status esatto."""


def utc_ms(year: int, month: int, day: int = 1) -> int:
    """Epoch millisecondi (UTC) per la mezzanotte della data indicata."""
    dt = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def clean_float(raw: str):
    """Parsa un campo numerico; ritorna None per vuoto/sentinel/malformed.

    Il chiamante SALTA i None: nessun ``null`` viene mai messo nei values.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    if v <= MISSING_FLOOR:
        return None
    return v


def _dedupe(points):
    """Ordina per epoch e rimuove duplicati (ultimo valore wins)."""
    by_ts = {}
    for ts, value in points:
        by_ts[int(ts)] = float(value)
    return [[ts, by_ts[ts]] for ts in sorted(by_ts)]


def parse_cwlinks(text: str):
    """cwlinks AO/NAO: SOLO lead==0 e SOLO member==0 (controllo)."""
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    base = {"lead", "member", "time", "valid_time"}
    # ao/nao/pna hanno lo stesso schema con il nome colonna diverso.
    value_col = next((c for c in ("ao_index", "nao_index", "pna_index")
                      if base | {c} <= fields), None)
    if value_col is None:
        raise ValueError("cwlinks: header inatteso %r" % (reader.fieldnames,))

    points = []
    stats = {"rows": 0, "kept_lead0_member0": 0, "dropped_lead_gt0": 0,
             "dropped_member_gt0": 0, "dropped_bad": 0}
    for row in reader:
        stats["rows"] += 1
        lead = (row.get("lead") or "").strip()
        member = (row.get("member") or "").strip()
        if lead != "0":
            stats["dropped_lead_gt0"] += 1
            continue  # REGOLA OBBLIGATORIA: mai pubblicare lead>0 (GEFS forecast)
        if member != "0":
            stats["dropped_member_gt0"] += 1
            continue  # member 1..30 = analisi perturbate, non l'osservato
        value = clean_float(row.get(value_col))
        tstr = (row.get("time") or "").strip()
        if value is None or len(tstr) != 10:
            stats["dropped_bad"] += 1
            continue
        try:
            ts = utc_ms(int(tstr[0:4]), int(tstr[5:7]), int(tstr[8:10]))
        except ValueError:
            stats["dropped_bad"] += 1
            continue
        points.append((ts, value))
        stats["kept_lead0_member0"] += 1
    return _dedupe(points), stats


def parse_tele_nh(text: str, column: int):
    """tele_index.nh mensile, fixed-width. Ritorna solo i punti validi."""
    if not 0 <= column < TELE_NH_NFIELDS:
        raise ValueError("tele_nh: colonna fuori range")
    points = []
    stats = {"data_lines": 0, "kept": 0, "skipped_missing": 0}
    for line in text.splitlines():
        if len(line) < TELE_NH_FIELD_START + TELE_NH_FIELD_WIDTH:
            continue
        year_raw = line[0:4].strip()
        month_raw = line[4:8].strip()
        if not (year_raw.isdigit() and month_raw.isdigit()):
            continue
        stats["data_lines"] += 1
        start = TELE_NH_FIELD_START + TELE_NH_FIELD_WIDTH * column
        value = clean_float(line[start : start + TELE_NH_FIELD_WIDTH])
        if value is None:
            stats["skipped_missing"] += 1
            continue
        try:
            ts = utc_ms(int(year_raw), int(month_raw))
        except ValueError:
            stats["skipped_missing"] += 1
            continue
        points.append((ts, value))
        stats["kept"] += 1
    return _dedupe(points), stats


def parse_wksst(text: str, column: int):
    """wksst9120.for settimanale, fixed-width: si pubblica la SSTA (anomalia)."""
    if not 0 <= column < 4:
        raise ValueError("wksst: colonna fuori range")
    points = []
    stats = {"data_lines": 0, "kept": 0, "skipped_missing": 0, "skipped_bad_date": 0}
    a, b = WKSST_DATE_SLICE
    for line in text.splitlines():
        if len(line) < b:
            continue
        date_raw = line[a:b].strip()
        if len(date_raw) != 9:
            continue
        stats["data_lines"] += 1
        s = WKSST_BLOCK_START + WKSST_BLOCK_STEP * column
        value = clean_float(line[s + 5 : s + 13])  # SSTA, non SST
        if value is None:
            stats["skipped_missing"] += 1
            continue
        try:
            d = datetime.strptime(date_raw, "%d%b%Y").replace(tzinfo=timezone.utc)
        except ValueError:
            stats["skipped_bad_date"] += 1
            continue
        points.append((int(d.timestamp() * 1000), value))
        stats["kept"] += 1
    return _dedupe(points), stats


def parse_soi(text: str):
    """soi mensile, fixed-width: Tahiti - Darwin.

    Il file contiene DUE tabelle: la prima e' l'anomalia SLP grezza
    ("... SEA LEVEL PRESS ... ANOMALY"), la seconda e' la serie
    STANDARDIZZATA ("... STANDARDIZED DATA"). Si pubblica SOLO la seconda,
    che e' l'SOI; la prima non viene mai usata. I -999.9 futuri sono saltati.
    """
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if "STANDARDIZED" in line.upper():
            start = i + 1
            break
    points = []
    stats = {"data_lines": 0, "kept": 0, "skipped_missing": 0,
             "standardized_block_found": start > 0}
    for line in lines[start:]:
        year_raw = line[0:SOI_YEAR_END].strip()
        if not year_raw.isdigit() or len(line) < 76:
            continue
        stats["data_lines"] += 1
        for month in range(1, 13):
            s = SOI_FIELD_START + SOI_FIELD_WIDTH * (month - 1)
            value = clean_float(line[s : s + SOI_FIELD_WIDTH])
            if value is None:
                stats["skipped_missing"] += 1
                continue
            try:
                ts = utc_ms(int(year_raw), month)
            except ValueError:
                stats["skipped_missing"] += 1
                continue
            points.append((ts, value))
            stats["kept"] += 1
    return _dedupe(points), stats


def parse_qbo(text):
    """QBO mensile: SOLO il blocco ORIGINAL DATA (m/s).

    Il file CPC contiene tre tabelle (ORIGINAL DATA, ANOMALY, STANDARDIZED
    DATA). Senza il gate sui blocchi, per la stessa data resterebbe dopo il
    dedupe il valore STANDARDIZZATO, con unita' diversa da m/s (misurato).
    """
    points = []
    stats = {"data_lines": 0, "kept": 0, "skipped_missing": 0,
             "block": "ORIGINAL", "skipped_other_blocks": 0}
    in_original = False
    for line in text.splitlines():
        up = line.upper()
        if "ORIGINAL" in up:
            in_original = True
            continue
        if "ANOMALY" in up or "STANDARDIZED" in up:
            if in_original:
                stats["skipped_other_blocks"] += 1
            in_original = False
            continue
        if not in_original:
            continue
        year_raw = line[0:4].strip()
        if not year_raw.isdigit():
            continue
        stats["data_lines"] += 1
        for month in range(1, 13):
            s = QBO_FIELD_START + QBO_FIELD_WIDTH * (month - 1)
            value = clean_float(line[s:s + QBO_FIELD_WIDTH])
            if value is None:
                stats["skipped_missing"] += 1
                continue
            try:
                ts = utc_ms(int(year_raw), month)
            except ValueError:
                stats["skipped_missing"] += 1
                continue
            points.append((ts, value))
            stats["kept"] += 1
    return _dedupe(points), stats


def parse_amo(text):
    """AMO mensile ERSSTv5: anomalia SSTA in gradi C, righe 'anno mese SSTA'."""
    points = []
    stats = {"data_lines": 0, "kept": 0, "skipped_missing": 0}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        stats["data_lines"] += 1
        value = clean_float(parts[2])
        if value is None:
            stats["skipped_missing"] += 1
            continue
        try:
            ts = utc_ms(int(parts[0]), int(parts[1]))
        except ValueError:
            stats["skipped_missing"] += 1
            continue
        points.append((ts, value))
        stats["kept"] += 1
    return _dedupe(points), stats


def parse_ssw(text):
    """Timeline storica SSW dalla tabella CSL/SSWC (date centrali).

    Le colonne delle rianalisi sono 6; si sceglie una data per evento con
    precedenza dichiarata ERA5, JRA-55, MERRA2, ERA-Interim, ERA-40,
    NCEP-NCAR. Le celle '****' e vuote NON sono eventi. La definizione e la
    citazione sono nei metadati della serie, non inventate qui.
    """
    columns = ("NCEP-NCAR", "ERA40", "ERA-Interim", "JRA-55", "MERRA2", "ERA5")
    preference = (5, 3, 4, 2, 1, 0)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I)
    points = []
    events = []
    stats = {"rows": 0, "kept": 0, "skipped": 0}
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if len(cells) < 7:
            continue
        clean = [html_mod.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in cells]
        name = clean[0]
        low = name.lower()
        if not name or "total" in low or "frequency" in low:
            continue
        stats["rows"] += 1
        nm = re.match(r"^\s*([A-Za-z]{3})\s+(\d{4})\s*$", name)
        name_year = int(nm.group(2)) if nm else 2000
        chosen = None
        for idx in preference:
            raw = clean[idx + 1] if idx + 1 < len(clean) else ""
            if not raw or "****" in raw:
                continue
            dm = re.match(r"^\s*(\d{1,2})-([A-Za-z]{3})-(\d{2,4})\s*$", raw)
            if not dm:
                continue
            day, mon, yr = int(dm.group(1)), dm.group(2), dm.group(3)
            if len(yr) == 4:
                year = int(yr)
            else:
                # ATTENZIONE: il %y di Python mappa 58 -> 2058 (pivot CNI),
                # mentre qui gli eventi vanno dal 1958 al 2023. Il secolo va
                # scelto dall'anno nell'etichetta dell'evento, NON dal pivot.
                year = min((1900 + int(yr), 2000 + int(yr)),
                           key=lambda y: abs(y - name_year))
            try:
                d = datetime.strptime("%d-%s-%04d" % (day, mon, year),
                                      "%d-%b-%Y").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            chosen = (int(d.timestamp() * 1000), raw, columns[idx])
            break
        if not chosen:
            stats["skipped"] += 1
            continue
        events.append({"ts": chosen[0], "name": name,
                       "date": chosen[1], "reanalysis": chosen[2]})
        points.append((chosen[0], 1.0))
        stats["kept"] += 1
    return _dedupe(points), events, stats


def fetch(url: str, timeout: int = 60):
    """GET singolo: ritorna (testo, Last-Modified header raw).

    In caso di errore HTTP solleva SourceError con URL e status esatto.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            last_modified = resp.headers.get("Last-Modified") or ""
            return raw.decode("utf-8", errors="replace"), last_modified
    except urllib.error.HTTPError as exc:
        raise SourceError("HTTP %s su %s" % (exc.code, url)) from exc
    except urllib.error.URLError as exc:
        raise SourceError("URL error su %s: %s" % (url, exc.reason)) from exc


def _parse_for_source(src, text):
    kind = src["kind"]
    if kind == "cwlinks":
        return parse_cwlinks(text)
    if kind == "tele_nh":
        return parse_tele_nh(text, src["column"])
    if kind == "wksst":
        return parse_wksst(text, src["column"])
    if kind == "soi":
        return parse_soi(text)
    if kind == "qbo":
        return parse_qbo(text)
    if kind == "amo":
        return parse_amo(text)
    raise ValueError("kind sconosciuto: %r" % kind)


def build(out_path, fetcher=fetch, timeout: int = 60):
    """Scarica tutte le sorgenti, costruisce il JSON e lo scrive.

    ``fetcher(url, timeout) -> (text, last_modified)`` e' iniettabile per i test.
    Ritorna (payload, stats_per_source, byte_written).
    """
    # Un solo GET per URL: tele_index.nh e wksst9120.for alimentano piu' serie.
    cache = {}
    series = []
    stats = {}
    for src in SOURCES:
        url = src["url"]
        if url not in cache:
            cache[url] = fetcher(url, timeout)
        text, last_modified = cache[url]
        events = None
        if src["kind"] == "ssw":
            points, events, pstats = parse_ssw(text)
        else:
            points, pstats = _parse_for_source(src, text)
        entry = {
            "id": src["id"],
            "label": src["label"],
            "values": points,
            "baseline": src["baseline"],
            "last_modified": last_modified,
            "source_url": src["url"],
            "source": src.get("source", "NOAA CPC"),
            "license": src.get("license", LICENSE),
            "unit": src.get("unit", "standardized index"),
        }
        if events is not None:
            # Timeline: i valori sono 1.0 per evento, le date/etichette stanno
            # qui. Nessun evento sintetico e nessuna previsione.
            entry["kind"] = "events"
            entry["events"] = events
            entry["citation"] = src["citation"]
            entry["definition"] = src["definition"]
        series.append(entry)
        stats[src["id"]] = dict(pstats, points=len(points),
                                last_modified=last_modified, url=url)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc)
                        .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": SOURCE_LABEL,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "methodology_notice": METHODOLOGY_NOTICE,
        "not_implemented": NOT_IMPLEMENTED,
        "solar_activity": SOLAR_EXCLUSION,
        "series": series,
    }
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    data = raw.encode("utf-8")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return payload, stats, len(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/climate/teleconnections.json",
                    help="path del JSON di output")
    ap.add_argument("--timeout", type=int, default=60, help="timeout HTTP secondi")
    args = ap.parse_args(argv)

    try:
        payload, stats, nbytes = build(args.out, timeout=args.timeout)
    except SourceError as exc:
        print("FERMATO: %s" % exc, file=sys.stderr)
        return 2

    print("scritto %s — %d byte, %d serie" %
          (args.out, nbytes, len(payload["series"])))
    for s in payload["series"]:
        st = stats[s["id"]]
        print("  %-16s %5d punti  LM=%s" %
              (s["id"], len(s["values"]), st["last_modified"] or "(assente)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
