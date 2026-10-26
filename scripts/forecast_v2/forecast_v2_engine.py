#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forecast_v2_engine.py — MeteoRisk "Mappe previsionali V2" (ENGINE-ONLY, server).

Motore che costruisce in GitHub Actions una CACHE GITHUB di mappe previsionali
MULTI-DOMINIO: per ogni dominio richiesto e per ogni istante di previsione
produce UN file `.om` multi-variabile croppato sull'Italia, leggibile dal reader
WML 0.2.2 (stesso formato del POC `spike_omfiles/`, verificato bit-exact).

    python scripts/forecast_v2/forecast_v2_engine.py \
        [--domains dwd_icon_eu ecmwf_ifs025 ...] [--valid-frames 0] \
        [--out data/fm2] [--once] [--no-prune] [--dry-run] [--force] \
        [--frames-cache .cache_fm2_frames] [--no-range]

    --valid-frames 0 (default) o negativo = pubblica l'INTERO orizzonte di ogni
    dominio (tutti i valid_times del rispettivo manifest). N > 0 = primi N frame.

Dati sorgente (Open-Meteo "spatial" files, accesso S3 anonimo):
    https://openmeteo.s3.amazonaws.com/data_spatial/<dominio>/<YYYY>/<MM>/<DD>/<HH00Z>/<YYYY-MM-DDTHH00.om
    https://openmeteo.s3.amazonaws.com/data_spatial/<dominio>/latest.json   (manifest)

Disegno (V2 del motore, scelte DICHIARATE):
  - MULTI-DOMINIO INDIPENDENTE: ogni dominio elencato in `--domains` e' trattato
    in modo AUTONOMO (fine del paradigma primario+secondario). Ogni dominio
    produce la PROPRIA cache, sulla PROPRIA griglia, nel proprio sottodirectory
    `data/fm2/<dominio>/`, con il proprio `latest.json`. Un dominio non fornisce
    mai frame a un altro.
  - RICONOSCIMENTO CRS: dal `crs_wkt` del manifest (e del frame) si riconosce il
    sistema di riferimento. Se NON inizia per `GEOGCRS` (es. il `PROJCRS`
    Lambert del `chmi_aladin_central_europe_2km`) il dominio viene SKIPPATO con
    log esplicito `SKIP dominio '<d>': griglia proiettata non supportata
    (PROJCRS/Lambert)` e la run prosegue con gli altri.
  - GRIGLIA DERIVATA: `latMin/lonMin/latMax/lonMax` vengono letti dal `BBOX[...]`
    del `crs_wkt` (regex); `nx,ny` dal `.shape == (ny,nx)` del PRIMO frame `.om`
    del dominio (via `OmFileReader`, stesso `fetch_frame` -> va in frames-cache).
    Poi `dx=(lonMax-lonMin)/(nx-1)`, `dy=(latMax-latMin)/(ny-1)`. Il dict griglia
    e' memorizzato in una cache per-run. La tabella `GRIDS` resta solo come
    FALLBACK (dry-run, dove il frame non si scarica) e come GUARDIA di coerenza:
    se i dx/dy derivati si scostano >1% dai valori noti (e il dominio e' in
    GRIDS) si logga un warning ma si USANO i valori derivati.
  - CROP PER-DOMINIO: ogni dominio e' croppato sul PROPRIO bbox via `bbox_for(domain)`/`DOMAIN_BBOX`
    (default/fallback `V2_BBOX` = Italia (36.5, 6.5, 47.2, 18.6)), applicato alla griglia via `bbox_to_indices`/`target_latlon` (generici).
  - ORIZZONTE INTERO: si pubblicano TUTTI i valid_times del manifest del dominio
    (nessuna troncatura a un numero fisso di frame). --valid-frames 0/negativo
    (default) = intero orizzonte; N > 0 = primi N.
  - VARIABILI: lista `V2_RISK_VARS` (41: 28 dirette + 13 derivate). TRE gruppi
    hanno un'ALLOWLIST PER DOMINIO esplicita nel codice (mai "se il file ce
    l'ha"): il livello `10 hPa` (6 voci, `VARS_10HPA`/`VAR_10HPA_DOMAINS`:
    ecmwf_ifs025, ncep_gfs025), la FASE NEVE (2 voci, `VARS_SWE`/
    `VAR_SWE_DOMAINS`) e la FINESTRA 6 ORE (2 voci, `VARS_WINDOW6H`, stessa
    allowlist della neve: 6 domini su 7, tutti tranne `ncep_gfs025`, che non ha
    alcuna variabile di superficie). Per i domini fuori allowlist le voci sono
    FILTRATE VIA prima della lettura (non si leggono e non compaiono nel
    manifest).
    Per il resto la presenza reale e' decisa FILE-PER-FILE dai child del `.om`:
    le variabili assenti nella sorgente sono OMESSE con warning (es.
    `surface_pressure`, spesso non disponibile). Le DERIVATE (dew_point_2m,
    wind_speed_10m/850hPa/300hPa, wind_speed_10hPa,
    wind_direction_10m/850hPa/300hPa/10hPa, precipitation_cum, snow_fraction,
    precipitation_6h, snow_fraction_6h)
    sono CALCOLATE dal motore (registro `DERIVED_VARS`); `V2_INPUT_VARS` elenca
    le variabili di INPUT (componenti u/v a 10 m, 850 e 300 hPa) lette ma NON
    pubblicate.
    Il vento si pubblica come COPPIA velocita'+direzione: `wind_direction_*`
    e' la secondaria pretesa dalla libreria client per ogni `wind_speed_*`
    (le u/v non sono piu' pubblicate; al livello 10 hPa le componenti u/v sono
    IN PIU' pubblicate come variabili a se', vedi `VARS_10HPA`).
    `precipitation_6h` / `snow_fraction_6h` sono le uniche derivate STATEFUL su
    una FINESTRA (`precipitation_cum` e' stateful su tutta la serie): la
    finestra e' di 6 ORE di valid_time, tassellata dai passi REALI della serie
    (1 h -> 6 frame, 3 h -> 2, 6 h -> 1), NON "gli ultimi 6 frame" - vedi
    `window6h_plan` e il commento di `WINDOW6H_HOURS` per le misure che lo
    motivano. Guardia obbligatoria: `forecast_reference_time` di ogni frame
    della finestra deve essere quello della run corrente, altrimenti null con
    `mixed_reference_time` (la cache dei frame e' piatta e chiavata sul nome).
    Memoria: fino a 6 frame x 2 array in piu' nel processo (dwd_icon a 0,125°:
    ~78 MB; ICON-EU: ~19 MB).
  - NO-OP GUARD PER-DOMINIO: se `data/fm2/<dominio>/latest.json` esiste con lo
    stesso `reference_time` e gli stessi `valid_times` (e non --force) il dominio
    viene saltato SENZA scaricare/riscrivere (evita commit inutili).
  - RUN PARZIALE (P1, `--allow-partial-run`, DEFAULT OFF): il motore puo' partire
    quando il run ha APPENA INIZIATO a pubblicarsi, senza aspettare che
    `latest.json` venga riscritto a run completo. Scoperta del run in corso:
    `<dominio>/in-progress.json` (fonte primaria, 1 GET) e, in fallback, listing
    del prefix `<dominio>/<YYYY>/<MM>/<DD>/` via ListObjectsV2 + `<run>/meta.json`
    (MISURATO il 2026-10-24: `in-progress.json` e `<run>/meta.json` hanno lo
    stesso schema di `latest.json` e i loro `valid_times` coincidono ESATTAMENTE
    con i `.om` gia' presenti nel run). I frame pubblicati sono trattati come
    PREFISSO di `valid_times`: al primo `.om` assente ci si FERMA, non si salta
    (il client risolve il file per INDICE POSIZIONALE: `valid = times[idx]` vs
    `frames[idx].file`, quindi un sottoinsieme non contiguo mostrerebbe l'ora
    sbagliata in silenzio). `valid_times` nel manifest pubblicato e' quindi
    TRONCATO ai soli frame scritti, i mancanti sono dichiarati in
    `partial.missing` e il dominio fallisce solo se manca il PRIMO frame o se i
    frame presi sono sotto `--partial-min-frames`. Con il flag OFF non viene
    fatta NESSUNA richiesta aggiuntiva e il comportamento e' quello storico.
  - PRUNING PER-DOMINIO: in `data/fm2/<dominio>/` resta ESATTAMENTE la finestra
    corrente (le ancore `valid_times` pubblicate in questo run); ogni `.om`
    estraneo (reference_time precedenti) viene rimosso. `V2_MAX_FRAMES` e' un cap
    OPZIONALE di sicurezza del pruning (0 default = nessun cap).
  - FRAMES-CACHE (--frames-cache): cache PERSISTENTE dei frame `.om` full-size
    (ripristinata in CI via actions/cache), layout `<dir>/<dominio>/<VALID>.om`.
    Un frame in cache viene riusato SENZA download se la sua Content-Length
    combacia con quella remota (HEAD leggero; fallback: size locale).
  - LETTURA SELETTIVA (default): il frame sorgente NON viene scaricato per
    intero. Aprendo il `.om` remoto via fsspec (HTTP Range) si leggono SOLO le
    variabili di `V2_READ_VARS` (pubblicate + input) presenti (risoluzione nomi,
    crop e `scale_factor` INVARIATI) + `crs_wkt` e la shape (header, letture
    piccole).
    I frame sorgente contengono molte piu' variabili di quelle pubblicate
    (es. `ncep_gfs025`: ~315 array -> ~10 pubblicate). Ordine I/O per frame:
    frame in frames-cache -> lettura a intervalli -> DOWNLOAD COMPLETO come
    FALLBACK ESPLICITO. Ogni fallback emette `[WARN]` (dominio/frame/motivo) e
    incrementa `download.fallback_count` nel manifest: mai silenzioso.
    `--no-range` disabilita del tutto la lettura a intervalli.
  - EXIT: se TUTTI i domini falliscono -> exit 1. Se almeno UN dominio e' `ok`
    (anche solo NO-OP) -> exit 0, con summary esplicito `ok`/`skip`/`fail` (il
    workflow deve poter committare la cache dei domini riusciti).
  - Scrittura ATOMICA (.tmp -> os.replace) e nessun segreto: l'accesso S3 e'
    anonimo. Dipendenze: `omfiles`, `numpy`, `Pillow` (mappe) e `fsspec` (+ il
    backend HTTP `aiohttp`) per la lettura a intervalli; se fsspec manca si usa
    il download completo (fallback esplicito).

LICENZE / ATTRIBUZIONE:
  - Dati meteo: Open-Meteo (https://open-meteo.com), licenza CC BY 4.0 ->
    attribuzione a Open-Meteo richiesta in ogni ridistribuzione.
  - `omfiles` (GPL-2.0-only) e' usato SOLO a runtime/CI per leggere e scrivere
    il formato `.om`; NON viene venduto ne' incluso nel repository (dipendenza
    pip dichiarata in requirements.txt).
"""

import argparse
import asyncio
import datetime as _dt
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

try:
    from omfiles import OmFileReader, OmFileWriter
except ImportError:  # pragma: no cover - dipendenza CI
    print("[forecast_v2] ERRORE: modulo 'omfiles' assente. "
          "Eseguire: python -m pip install -r scripts/forecast_v2/requirements.txt")
    raise

# Reader ASINCRONO (lettura a intervalli). Opzionale: se l'omfiles installato
# non lo espone, la lettura selettiva non e' disponibile e si usa il download
# completo (fallback esplicito, vedi `read_frame_selective`).
try:
    from omfiles import OmFileReaderAsync
except ImportError:  # pragma: no cover - omfiles piu' vecchio
    OmFileReaderAsync = None

# fsspec (backend HTTP con Range). Opzionale: se assente idem sopra.
try:
    import fsspec as _fsspec
except ImportError:  # pragma: no cover - dipendenza opzionale
    _fsspec = None

LOG = "[forecast_v2]"

# ---------------------------------------------------------------------------
# Configurazione (costanti dichiarate)
# ---------------------------------------------------------------------------
S3_HOST = "https://openmeteo.s3.amazonaws.com"
S3_BASE = S3_HOST + "/data_spatial"
# Prefisso relativo al BUCKET per ListObjectsV2 (il listing non usa `/data_spatial`
# nel path: e' `https://openmeteo.s3.amazonaws.com/?list-type=2&prefix=...`).
S3_LIST_PREFIX = "data_spatial"

# ---------------------------------------------------------------------------
# RUN PARZIALE (P1) — CONFIGURAZIONE. Default: DISATTIVATO (comportamento storico)
# ---------------------------------------------------------------------------
# `ALLOW_PARTIAL_RUN = True` (oppure `--allow-partial-run`) abilita la scoperta
# del run in corso (`<dominio>/in-progress.json` / `<run>/meta.json`) e la
# pubblicazione di un PREFISSO di frame. Con False non si fa NESSUNA richiesta
# aggiuntiva, il percorso e' quello storico e il manifest non cambia formato.
ALLOW_PARTIAL_RUN = False
# Minimo numero di frame pubblicati perche' un run parziale sia pubblicabile
# (sotto questa soglia il dominio FALLISCE e i .om scritti vengono rimossi:
# meglio nessun aggiornamento che una mappa con 1 frame di cui 0 utili).
PARTIAL_MIN_FRAMES = 3
# Finestra (ore) entro cui si cercano directory di run via listing del prefix.
# Minimo 2 giorni (oggi + ieri): un run notturno puo' essere ancora in
# pubblicazione dopo mezzanotte.
PARTIAL_PROBE_HOURS = 12

# (lat_min, lon_min, lat_max, lon_max) — bbox Italia.
V2_BBOX = (36.5, 6.5, 47.2, 18.6)

# Per-domain crop window [lat_min, lon_min, lat_max, lon_max].
# Each domain is cut to its own real area (prima: tutti tagliati sull'Italia).
#
# EMISFERO NORD INTERO (1.2.4.13): i 4 domini GLOBALI (ecmwf_ifs025,
# ncep_gfs025, dwd_icon, meteofrance_arpege_world025) sorgono con BBOX
# [-90,-180,90,179.75] (pianeta INTERO). Pubblicare/renderizzare il globo e'
# inutile per la meteorologia italiana: il bbox e' quindi ritagliato
# sull'EMISFERO NORD INTERO, `(20.0, -180.0, 90.0, 180.0)` = lat 20..90N,
# longitudine intera (-180..180).
# Scelta (due motivi reali):
#   (a) vedere le incursioni fredde dalla SIBERIA: il freddo deve vedersi
#       NASCERE (irraggiamento continentale sull'Asia settentrionale) e non
#       solo ARRIVARE in Europa gia' formato, perche' e' li' che si origina
#       la maggior parte delle ondate di gelo italiane;
#   (b) predisporre la futura mappa "emisfero nord stile Meteociel", che ha
#       bisogno della copertura completa dell'emisfero nord (non solo della
#       finestra -60..60 di longitudine).
# Si resta interamente sopra l'equatore (nessun emisfero sud) e il bbox e' un
# superset del bbox europeo dei regionali `(34,-15,62,40)`, quindi la vista
# "Europa" resta interamente disponibile. Il bbox e' ALLINEATO ALLA GRIGLIA di
# tutti e 4 i domini: gli angoli cadono su multipli esatti di dx/dy a partire
# dagli angoli del sorgente, quindi `bbox_to_indices` non arrotonda celle
# parziali (gli indici risultano tutti interi).
NORTH_HEMISPHERE_BBOX = (20.0, -180.0, 90.0, 180.0)
DOMAIN_BBOX = {
    "dwd_icon_eu": (34.0, -15.0, 62.0, 40.0),
    "dwd_icon_d2": (43.18, -3.94, 58.08, 20.34),
    "ecmwf_ifs025": NORTH_HEMISPHERE_BBOX,
    "ncep_gfs025": NORTH_HEMISPHERE_BBOX,
    # `ncep_gfs013` (0.13 deg) usa lo STESSO bbox europeo di `dwd_icon_eu`.
    # La voce e' OBBLIGATORIA: senza di essa `bbox_for()` ricadrebbe IN SILENZIO
    # su `V2_BBOX` (Italia) e il crop sarebbe 104x92 invece di ~470x239.
    "ncep_gfs013": (34.0, -15.0, 62.0, 40.0),
    "dwd_icon": NORTH_HEMISPHERE_BBOX,
    "meteofrance_arpege_world025": NORTH_HEMISPHERE_BBOX,
}

def bbox_for(domain):
    return DOMAIN_BBOX.get(domain, V2_BBOX)

# Domini di default (MULTI-DOMINIO INDIPENDENTE: nessun ordine di priorita').
V2_DOMAINS = ["dwd_icon_eu", "dwd_icon_d2", "ecmwf_ifs025", "ncep_gfs025", "ncep_gfs013", "dwd_icon", "meteofrance_arpege_world025"]

# Lista ORDINATA delle variabili di rischio desiderate, con sinonimi/forme.
# name = nome pubblicato nel `.om`; sources = nomi candidati nel file sorgente
# (il primo presente vince). La presenza reale e' determinata per-file.
# NOTA (1.2.4.11): le voci con `sources: []` sono DERIVATE (non esistono nella
# sorgente): compaiono nel registro `DERIVED_VARS` piu' sotto, che ne dichiara
# le variabili sorgente e la funzione. `resolve_direct` non le risolve (nessun
# candidato) e vengono aggiunte dopo la lettura diretta (vedi `derive_per_frame`
# per le derivate per-frame e l'accumulo stateful per precipitation_cum).
V2_RISK_VARS = [
    {"name": "temperature_2m",       "sources": ["temperature_2m"]},
    {"name": "dew_point_2m",         "sources": []},                          # DERIVATA (T2m + RH2m)
    {"name": "temperature_850hPa",   "sources": ["temperature_850hPa"]},      # FM_CACHE core
    {"name": "temperature_500hPa",   "sources": ["temperature_500hPa"]},      # FM_CACHE core
    {"name": "relative_humidity_2m", "sources": ["relative_humidity_2m"]},
    {"name": "relative_humidity_850hPa", "sources": ["relative_humidity_850hPa"]},  # FM_CACHE core
    {"name": "relative_humidity_700hPa", "sources": ["relative_humidity_700hPa"]},  # diretta (10/11 domini)
    {"name": "pressure_msl",         "sources": ["pressure_msl", "msl_pressure"]},
    {"name": "geopotential_height_500hPa", "sources": ["geopotential_height_500hPa"]},   # FM_CACHE core
    {"name": "geopotential_height_850hPa", "sources": ["geopotential_height_850hPa"]},   # FM_CACHE core
    {"name": "geopotential_height_1000hPa", "sources": ["geopotential_height_1000hPa"]},  # FM_CACHE core
    {"name": "surface_pressure",     "sources": ["surface_pressure"]},        # spesso assente
    {"name": "wind_speed_10m",       "sources": []},                          # DERIVATA (u/v 10m, INPUT)
    {"name": "wind_direction_10m",   "sources": []},                          # DERIVATA (u/v 10m, INPUT)
    {"name": "wind_speed_850hPa",    "sources": []},                          # DERIVATA (u/v 850hPa, INPUT)
    {"name": "wind_direction_850hPa", "sources": []},                         # DERIVATA (u/v 850hPa, INPUT)
    {"name": "wind_speed_300hPa",    "sources": []},                          # DERIVATA (u/v 300hPa, INPUT)
    {"name": "wind_direction_300hPa", "sources": []},                         # DERIVATA (u/v 300hPa, INPUT)
    {"name": "wind_gusts_10m",       "sources": ["wind_gusts_10m"]},
    {"name": "cloud_cover",          "sources": ["cloud_cover"]},
    {"name": "cloud_cover_low",      "sources": ["cloud_cover_low"]},        # FM_CACHE core
    {"name": "cloud_cover_mid",      "sources": ["cloud_cover_mid"]},        # FM_CACHE core
    {"name": "cloud_cover_high",     "sources": ["cloud_cover_high"]},       # FM_CACHE core
    {"name": "cape",                 "sources": ["cape"]},
    {"name": "snow_depth",           "sources": ["snow_depth"]},
    {"name": "precipitation",        "sources": ["precipitation"]},
    {"name": "precipitation_cum",    "sources": []},                          # DERIVATA STATEFUL (somma precipitation)
    {"name": "rain",                 "sources": ["rain"]},
    {"name": "showers",              "sources": ["showers"]},
    {"name": "weather_code",         "sources": ["weather_code"]},
    {"name": "freezing_level_height", "sources": ["freezing_level_height"]},  # FM_CACHE core
    # --- LIVELLO 10 hPa (SOLO domini di `VAR_10HPA_DOMAINS`) ---------------
    # Aggiunte in CODA alla lista: il filtro per dominio toglie queste voci e
    # lascia la lista identica (stesso contenuto e STESSO ORDINE) a quella
    # storica, quindi `var_order` nel manifest dei domini non ammessi resta
    # byte-identico a prima (test: test_non_allowed_domains_keep_historical_var_set).
    {"name": "temperature_10hPa",    "sources": ["temperature_10hPa"]},
    {"name": "geopotential_height_10hPa", "sources": ["geopotential_height_10hPa"]},
    {"name": "wind_u_component_10hPa", "sources": ["wind_u_component_10hPa"]},
    {"name": "wind_v_component_10hPa", "sources": ["wind_v_component_10hPa"]},
    {"name": "wind_speed_10hPa",     "sources": []},                          # DERIVATA (u/v 10hPa)
    {"name": "wind_direction_10hPa", "sources": []},                          # DERIVATA (u/v 10hPa)
    # --- FASE NEVE / PIOGGIA (SOLO domini di `VAR_SWE_DOMAINS`) -------------
    # `snowfall_water_equivalent` e' un DATO DEL MODELLO (mm di acqua
    # equivalente caduta come neve); `snow_fraction` e' una NOSTRA derivata
    # (rapporto fra due campi del modello), NON una classificazione del modello.
    {"name": "snowfall_water_equivalent", "sources": ["snowfall_water_equivalent"]},
    {"name": "snow_fraction",         "sources": []},                          # DERIVATA (swe / precipitation)
    # --- FINESTRA 6 ORE (SOLO domini di `VAR_SWE_DOMAINS`, vedi VARS_WINDOW6H)
    # Somma della precipitazione sulle ULTIME 6 ORE di valid_time (non "6 frame":
    # la finestra e' tassellata dai passi REALI della serie) e frazione nevosa
    # PESATA sulla stessa finestra. Entrambe DERIVATE STATEFUL NOSTRE: le
    # accumula `process_domain` (finestra scorrevole), non `derive_per_frame`.
    {"name": "precipitation_6h",      "sources": []},                          # DERIVATA STATEFUL (finestra 6 h)
    {"name": "snow_fraction_6h",      "sources": []},                          # DERIVATA STATEFUL (finestra 6 h)
]

# Variabili di INPUT: lette dal frame sorgente ma NON pubblicate. Sono gli input
# delle derivate wind_speed_*/wind_direction_* (componenti u/v a 10 m, 850 e
# 300 hPa). Le u/v a 10 m erano PUBBLICATE fino alla 1.2.4.10: dalla 1.2.4.11
# NON si pubblicano piu' (la libreria client vuole la COPPIA
# speed+direction). Devono essere lette ANCHE dal percorso a intervalli, quindi
# entrano nell'unione `V2_READ_VARS`.
V2_INPUT_VARS = [
    {"name": "wind_u_component_10m", "sources": ["wind_u_component_10m"]},
    {"name": "wind_v_component_10m", "sources": ["wind_v_component_10m"]},
    {"name": "wind_u_component_850hPa", "sources": ["wind_u_component_850hPa"]},
    {"name": "wind_v_component_850hPa", "sources": ["wind_v_component_850hPa"]},
    {"name": "wind_u_component_300hPa", "sources": ["wind_u_component_300hPa"]},
    {"name": "wind_v_component_300hPa", "sources": ["wind_v_component_300hPa"]},
]
V2_INPUT_VAR_NAMES = frozenset(v["name"] for v in V2_INPUT_VARS)

# Unione passata ai reader (`load_frame`, `read_frame_selective`): pubblicate +
# INPUT. Va passato QUESTO insieme ai reader, altrimenti la lettura a intervalli
# non legge le componenti u/v a 850/300 hPa e la derivazione fallisce solo su
# quel percorso.
V2_READ_VARS = V2_RISK_VARS + V2_INPUT_VARS

# ---------------------------------------------------------------------------
# LIVELLO 10 hPa — ALLOWLIST PER DOMINIO (scelta di PRODOTTO, non tecnica)
# ---------------------------------------------------------------------------
# Il livello 10 hPa (~30-32 km, bassa stratosfera) e' disponibile nella
# sorgente Open-Meteo per 3 domini. MISURATO il 2026-10-25 leggendo i child dei
# frame reali su S3 (primo `valid_time` di ogni `latest.json`):
#
#   ecmwf_ifs025                  temperature/geopotential_height/wind_u/wind_v_10hPa
#   ncep_gfs025                   idem
#   meteofrance_arpege_world025   idem  <-- il `.om` le CONTIENE
#   dwd_icon, dwd_icon_eu, dwd_icon_d2, ncep_gfs013   NESSUNA variabile 10 hPa
#
# Il criterio NON e' "se la variabile esiste nel file": e' questa allowlist.
# Motivo (decisione dell'utente, 2026-10-25): per il livello 10 hPa DUE modelli
# bastano come secondo/terzo parere sulla stratosfera, e pubblicare il terzo
# (ARPEGE) costerebbe ~+68 MB per run di frame `.om` (stima dichiarata, non
# rimisurata qui) senza aggiungere informazione indipendente: ARPEGE e IFS
# condividono le stesse analisi di base a quella quota. Il filtro e' quindi
# ESPLICITO e vive qui, non nella presenza dei child del file.
#
# Conseguenza voluta: per i domini NON elencati la lista delle variabili resta
# ESATTAMENTE quella storica (stesso contenuto, stesso ordine), quindi
# `var_order` nei loro manifest e i byte dei loro `.om` non cambiano a parita'
# di input. Le 6 voci sono in CODA a `V2_RISK_VARS`, cosi' il filtro e' una
# semplice sottrazione che preserva l'ordine.
VAR_10HPA_DOMAINS = frozenset({"ecmwf_ifs025", "ncep_gfs025"})

# Le 6 variabili del livello 10 hPa: 4 DIRETTE (lette dal frame) + 2 DERIVATE
# (velocita' e direzione dalla coppia u/v dello stesso livello).
VARS_10HPA = (
    "temperature_10hPa",
    "geopotential_height_10hPa",
    "wind_u_component_10hPa",
    "wind_v_component_10hPa",
    "wind_speed_10hPa",
    "wind_direction_10hPa",
)
VARS_10HPA_SET = frozenset(VARS_10HPA)


def domain_supports_10hpa(domain):
    """True se `domain` e' nell'allowlist del livello 10 hPa."""
    return domain in VAR_10HPA_DOMAINS


# ---------------------------------------------------------------------------
# FASE NEVE / PIOGGIA — ALLOWLIST PER DOMINIO
# ---------------------------------------------------------------------------
# `snowfall_water_equivalent` (mm di acqua equivalente caduta come NEVE) e' il
# campo che permette di distinguere pioggia da neve. MISURATO sui child dei
# frame reali S3 (primo `valid_time` di ogni `latest.json`):
#
#   dwd_icon, dwd_icon_eu, dwd_icon_d2, ecmwf_ifs025,
#   ncep_gfs013, meteofrance_arpege_world025      -> PRESENTE
#   ncep_gfs025                                   -> ASSENTE
#
# `ncep_gfs025` non ha NESSUNA variabile di superficie (ne' precipitazione ne'
# T2m, umidita', nuvole): non e' un caso "se il file ce l'ha", e' un dominio
# che non puo' avere il campo, quindi l'allowlist e' ESPLICITA come per il
# 10 hPa e non un criterio implicito di presenza.
#
# ATTENZIONE (misurato): a T+0 i campi ACCUMULATI non esistono. Il frame al
# `reference_time` ha 111 array, il T+1h ne ha 121; `snowfall_water_equivalent`
# compare quindi SOLO dal secondo frame in poi, esattamente come
# `precipitation`. Il motore NON fallisce: la variabile e' OMESSA su quel frame
# con il warning di `missing_published_warnings` (`snow_fraction` non
# calcolabile perche' mancano le sorgenti) e, se mancasse su TUTTI i frame del
# dominio, un warning di sintesi lo dichiara (vedi `swe_seen` in
# `process_domain`).
#
# ECMWF (ATTENZIONE): il campo e' una STIMA DEL FORNITORE (derivata da
# precipitazione + temperature), non un accumulo fisico coerente con
# `precipitation`: su 661 celle reali `precipitation - showers - swe` e'
# NEGATIVO in 40 celle (fino a -3.5 mm). Per questo il motore NON deriva mai
# `pioggia = precip - swe`: pubblica `swe` e la frazione, e la frazione e'
# limitata a [0,1].
VAR_SWE_DOMAINS = frozenset({
    "dwd_icon",
    "dwd_icon_eu",
    "dwd_icon_d2",
    "ecmwf_ifs025",
    "ncep_gfs013",
    "meteofrance_arpege_world025",
})

# Le 2 variabili della fase neve: `snowfall_water_equivalent` e' DIRETTA (dato
# del modello), `snow_fraction` e' DERIVATA (swe/precipitation, NOSTRA).
VARS_SWE = (
    "snowfall_water_equivalent",
    "snow_fraction",
)
VARS_SWE_SET = frozenset(VARS_SWE)


# ---------------------------------------------------------------------------
# FINESTRA 6 ORE — `precipitation_6h` e `snow_fraction_6h`
# ---------------------------------------------------------------------------
# DUE DERIVATE STATEFUL NOSTRE (registro `DERIVED_VARS`, accumulate da
# `process_domain` sulla serie ORDINATA dei valid_times): non esistono nella
# sorgente, non sono calcolabili frame-per-frame e non sono una classificazione
# del modello (nessuna soglia neve/pioggia qui dentro).
#
# PERCHE' UNA FINESTRA E NON "6 FRAME" — MISURATO il 2026-10-26 sui frame reali:
# il campo `precipitation` di Open-Meteo NON e' "l'ora precedente": e' l'accumulo
# dell'intervallo fra il valid_time PRECEDENTE e quello corrente, e la serie ha
# passo che CAMBIA dentro la stessa run (1 h -> 3 h, oppure 3 h -> 6 h).
#   - ECMWF, run 2026-10-25T12Z, frame `2026-10-31T18:00Z` (passo 6 h): punto
#     21,50N 142,00E = 216,6 mm = `hourly_6` dell'API, e la cornice copre 6 h;
#   - ECMWF, PRIMO frame del tratto a 6 h `2026-10-31T12:00Z`: massimo di griglia
#     108,6 mm = `hourly_3` (NON `hourly_6` = 165,7). Quella cornice ha passo
#     nominale 6 h ma copre 3 h, perche' il valid_time precedente era 09Z.
#     Sommandola al frame 09Z si ottiene 165,70 mm = ESATTAMENTE `hourly_6`;
#     prendendo la sola cornice "6 h" si perde il 34% della pioggia;
#   - ARPEGE alla transizione 1 h -> 3 h: media 0,0857 mm (18Z, 1 h) ->
#     0,2579 mm (21Z, 3 h) = 3,01x; ICON-EU 0,0548 -> 0,1678 = 3,06x;
#     GFS 0.13 0,0517 -> 0,1825 = 3,4x.
# Quindi la regola e' TEMPORALE e non "N frame": si somma all'indietro finche'
# `Σ(t_i − t_{i−1})` e' ESATTAMENTE 6 h (±1 s). Poiche' 1, 3 e 6 dividono 6:
# 1 h -> 6 frame, 3 h -> 2 frame, 6 h -> 1 frame, e una transizione 3 h -> 6 h
# produce la finestra mista corretta (es. 3 h + 1 h + 1 h + 1 h = 6 h).
# La somma "dei 6 frame" a passo 3 h coprirebbe 18 h: misurato su ECMWF
# 2026-10-30T21Z..2026-10-31T12Z, media 1,6136 mm contro 0,5708 mm della
# finestra esatta = 2,83x (18 h / 6 h = 3, il resto e' la variabilita' del tempo).
#
# MEMORIA DICHIARATA: la finestra tiene al massimo `WINDOW6H_MAX_FRAMES` frame
# con DUE array ciascuno (`precipitation` e `snowfall_water_equivalent`, gia'
# letti per le altre variabili: nessuna lettura aggiuntiva). Sul crop di
# produzione: dwd_icon (0,125°, 561x2880 celle) = 6,5 MB per array ->
# 2 x 6 x 6,5 MB = ~78 MB nel caso peggiore; dwd_icon_eu (881x449) ~ 1,6 MB per
# array -> ~19 MB. Accettabile per il runner, ma e' un costo REALE e va tenuto
# presente se si allarga la finestra.
#
# RUN PARZIALI / PRIMI FRAME: i primi frame di una run NON hanno la finestra
# completa (T+0 non ha nemmeno i campi accumulati) -> null con motivo. E' il
# comportamento corretto: NON si riempie con zeri e NON si usano frame di run
# precedenti (vedi la guardia `mixed_reference_time`).
WINDOW6H_HOURS = 6.0
# Tolleranza sul totale: i passi sono secondi interi, 1 s assorbe l'aritmetica
# in virgola mobile senza accettare una finestra sbagliata di un passo.
WINDOW6H_TOLERANCE_S = 1
# Numero massimo di frame che possono tassellare 6 h (tutti a passo 1 h).
WINDOW6H_MAX_FRAMES = 6
VARS_WINDOW6H = (
    "precipitation_6h",
    "snow_fraction_6h",
)
VARS_WINDOW6H_SET = frozenset(VARS_WINDOW6H)


def domain_supports_swe(domain):
    """True se `domain` e' nell'allowlist della fase neve."""
    return domain in VAR_SWE_DOMAINS


def domain_supports_window6h(domain):
    """True se `domain` riceve la finestra 6 h.

    L'allowlist e' la STESSA della fase neve (`VAR_SWE_DOMAINS`) e non una
    presenza implicita: `precipitation_6h` dipende solo da `precipitation`, e
    l'unico dominio escluso (`ncep_gfs025`) non ha NESSUNA variabile di
    superficie - quindi ne' `precipitation` ne' `snowfall_water_equivalent`.
    `snow_fraction_6h` richiede `swe`: senza, la voce non deve comparire.
    """
    return domain_supports_swe(domain)


def _filter_swe(var_defs, domain):
    """Toglie le voci neve e finestra 6 h se il dominio non e' in `VAR_SWE_DOMAINS`.

    Stesso contratto di `_filter_10hpa`: le voci stanno in CODA alla lista,
    quindi per `ncep_gfs025` la lista resta identica (contenuto E ORDINE) a
    quella storica e `var_order` nel suo manifest non cambia.
    """
    if domain_supports_swe(domain):
        return list(var_defs)
    drop = VARS_SWE_SET | VARS_WINDOW6H_SET
    return [v for v in var_defs if v["name"] not in drop]


def _filter_10hpa(var_defs, domain):
    """Toglie le voci 10 hPa se il dominio non e' in `VAR_10HPA_DOMAINS`.

    L'ordine delle voci rimanenti e' INVARIATO (le 6 voci stanno in coda), cosi'
    il risultato per i domini non ammessi e' identico alla lista storica.
    """
    if domain_supports_10hpa(domain):
        return list(var_defs)
    return [v for v in var_defs if v["name"] not in VARS_10HPA_SET]


def risk_vars_for(domain):
    """Variabili PUBBLICATE ammesse per `domain` (allowlist 10 hPa e neve)."""
    return _filter_swe(_filter_10hpa(V2_RISK_VARS, domain), domain)


def input_vars_for(domain):
    """Variabili di INPUT ammesse per `domain` (allowlist 10 hPa e neve)."""
    return _filter_swe(_filter_10hpa(V2_INPUT_VARS, domain), domain)


def read_vars_for(domain):
    """Unione pubblicate + input ammesse per `domain` (da passare ai reader).

    Sostituisce `V2_READ_VARS` in OGNI punto per-dominio: passare l'unione
    globale a un dominio non ammesso farebbe leggere (e pubblicare) le
    variabili 10 hPa presenti nel file, cioe' esattamente cio' che l'allowlist
    deve impedire.
    """
    return risk_vars_for(domain) + input_vars_for(domain)


# Cap OPZIONALE di sicurezza del pruning (numero massimo di frame `.om`
# mantenuti in una dir di dominio). 0 = NESSUN CAP: si mantiene l'orizzonte
# INTERO del manifest (tutti i valid_times). E' anche il default di
# --valid-frames (0 = intero).
V2_MAX_FRAMES = 0
V2_OUT = "data/fm2"

# Passo di quantizzazione voluto per variabile pubblicata (.om).
# CONVENZIONE omfiles: il writer memorizza int = round(valore * scale_factor)
# e il lettore rilegge valore = int / scale_factor: quindi il passo effettivo
# e' 1/scale_factor. Qui si dichiarano i PASSI (unita' della variabile); il
# writer riceve scale_factor = 1.0 / passo (vedi write_frame_om).
# Le wind_direction_* (direzione meteorologica in gradi, intervallo [0,360))
# usano passo 1 grado: e' il massimo di risoluzione utile (il valore e' un
# intero di gradi) e con scale_factor = 1/1 = 1 il massimo memorizzabile int16
# e' 32767 gradi, ampiamente sopra il range [0,360): nessun rischio di
# saturazione/NaN.
# ATTENZIONE (verificato su dati reali): la quantizzazione a 1 grado fa
# arrotondare 359.5..360.0 a 360, che e' FUORI da [0,360). Per non pubblicare
# mai 360 il writer pubblico passa da `wrap_wind_direction_for_step`, che mappa
# le direzioni >= 360 - passo/2 a 0 (360 equivale a 0).
WIND_DIRECTION_STEP = 1.0
# Passo della frazione nevosa: 0.01 = 1 punto percentuale, la STESSA
# risoluzione dei campi percentuali gia' pubblicati (relative_humidity_*,
# cloud_cover_*, passo 1.0 su una scala 0-100). Il valore pubblicato resta un
# numero CONTINUO in [0,1]: il motore non applica alcuna soglia neve/pioggia
# (la decisione visiva e' del renderer/client). int16 con scale_factor = 100
# rappresenta 0.00..1.00 con 101 livelli: nessun rischio di saturazione.
SNOW_FRACTION_STEP = 0.01
VARIABLE_STEP = {
    "temperature_2m": 0.1,              # 0.1 degC
    "temperature_850hPa": 0.1,          # 0.1 degC
    "temperature_500hPa": 0.1,          # 0.1 degC
    "relative_humidity_2m": 1.0,        # 1 %
    "relative_humidity_850hPa": 1.0,    # 1 %
    "relative_humidity_700hPa": 1.0,    # 1 %
    "dew_point_2m": 0.1,                # 0.1 degC (DERIVATA, Magnus)
    "pressure_msl": 0.1,                # 0.1 hPa
    "geopotential_height_500hPa": 1.0,  # 1 m
    "geopotential_height_850hPa": 1.0,  # 1 m
    "geopotential_height_1000hPa": 1.0, # 1 m
    "surface_pressure": 0.1,            # 0.1 hPa
    # wind_u/v_component_* NON sono piu' pubblicate (INPUT): nessun passo qui.
    "wind_speed_10m": 0.1,              # 0.1 m/s (DERIVATA da u/v 10m, INPUT)
    "wind_direction_10m": WIND_DIRECTION_STEP,    # 1 grado (DERIVATA da u/v 10m, INPUT)
    "wind_speed_850hPa": 0.1,           # 0.1 m/s (DERIVATA da u/v 850hPa, INPUT)
    "wind_direction_850hPa": WIND_DIRECTION_STEP, # 1 grado (DERIVATA da u/v 850hPa, INPUT)
    "wind_speed_300hPa": 0.1,           # 0.1 m/s (DERIVATA da u/v 300hPa, INPUT)
    "wind_direction_300hPa": WIND_DIRECTION_STEP, # 1 grado (DERIVATA da u/v 300hPa, INPUT)
    "wind_gusts_10m": 0.1,              # 0.1 m/s
    "cloud_cover": 1.0,                 # 1 %
    "cloud_cover_low": 1.0,             # 1 %
    "cloud_cover_mid": 1.0,             # 1 %
    "cloud_cover_high": 1.0,            # 1 %
    "cape": 10.0,                       # 10 J/kg
    "snow_depth": 0.1,                  # 0.1 m
    "precipitation": 0.1,               # 0.1 mm
    # precipitation_cum (DERIVATA STATEFUL): cumulata lungo la serie dei
    # valid_times. LIMITE int16 (compressione pfor_delta_2d_int16): il writer
    # passa scale_factor = 1/passo = 10, quindi int = round(mm*10) e il massimo
    # int16 (32767) corrisponde a 3276.7 mm; OLTRE, omfiles produce NaN (non
    # wrap: comportamento MISURATO). Massimo osservato su dati reali: 1128.7 mm
    # (ciclone tropicale, dominio globale ECMWF) -> 11287, sotto il limite.
    "precipitation_cum": 0.1,           # 0.1 mm (DERIVATA STATEFUL)
    "rain": 0.1,                        # 0.1 mm
    "showers": 0.1,                     # 0.1 mm
    "weather_code": 1.0,                # 1 (codice WMO)
    "freezing_level_height": 10.0,      # 10 m
    # --- livello 10 hPa (solo domini in VAR_10HPA_DOMAINS) ---
    "temperature_10hPa": 0.1,           # 0.1 degC
    "geopotential_height_10hPa": 1.0,   # 1 m
    # Le componenti u/v a 10 hPa sono PUBBLICATE (a differenza di 10 m/850/300,
    # dove sono solo INPUT): servono al client come campo vettoriale a se'.
    "wind_u_component_10hPa": 0.1,      # 0.1 m/s
    "wind_v_component_10hPa": 0.1,      # 0.1 m/s
    "wind_speed_10hPa": 0.1,            # 0.1 m/s (DERIVATA da u/v 10hPa)
    "wind_direction_10hPa": WIND_DIRECTION_STEP,  # 1 grado (DERIVATA da u/v 10hPa)
    # --- fase neve (solo domini in VAR_SWE_DOMAINS) ---
    "snowfall_water_equivalent": 0.1,   # 0.1 mm (DATO DEL MODELLO, non derivato)
    "snow_fraction": SNOW_FRACTION_STEP,  # 0.01 (DERIVATA NOSTRA: swe/precipitation)
    # --- finestra 6 ore (solo domini in VAR_SWE_DOMAINS) ---
    # Stesso passo di `precipitation` (0.1 mm): con scale_factor = 10 il massimo
    # int16 (32767) copre 3276.7 mm in 6 ore, molto sopra qualunque accumulo
    # osservato (il massimo di griglia misurato su ECMWF e' 216.6 mm).
    "precipitation_6h": 0.1,            # 0.1 mm (DERIVATA STATEFUL, finestra 6 h)
    "snow_fraction_6h": SNOW_FRACTION_STEP,  # 0.01 (DERIVATA STATEFUL, pesata su 6 h)
}
DEFAULT_STEP = 1.0
DEFAULT_CHUNKS = [32, 32]
DEFAULT_COMPRESSION = "pfor_delta_2d_int16"

# Prefisso WKT dei CRS geografici supportati. Tutto cio' che non inizia per
# GEOGCRS (es. PROJCRS/Lambert) viene SKIPPATO.
GEOGRAPHIC_CRS_PREFIX = "GEOGCRS"

# Regex del BBOX nel WKT (ordine WKT: [latMin, lonMin, latMax, lonMax]).
_BBOX_RE = re.compile(
    r"BBOX\s*\[\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*,\s*"
    r"([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*\]")

# Tabella delle griglie NOTE dei 7 domini GEOGCRS. NON e' la fonte unica:
# la griglia di run e' DERIVATA da crs_wkt (BBOX) + shape del frame. Questa
# tabella serve SOLO come (a) FALLBACK nel --dry-run (dove il frame non si
# scarica, quindi nx/ny non e' derivabile) e (b) GUARDIA di coerenza sui dx/dy.
GRIDS = {
    "dwd_icon_eu":                  {"nx": 1377, "ny": 657,  "dx": 0.0625,    "dy": 0.0625},
    "ecmwf_ifs025":                 {"nx": 1440, "ny": 721,  "dx": 0.25,      "dy": 0.25},
    "ncep_gfs025":                  {"nx": 1440, "ny": 721,  "dx": 0.25,      "dy": 0.25},
    "ncep_gfs013":                  {"nx": 3072, "ny": 1536, "dx": 0.1171875, "dy": 0.1171493},
    "dwd_icon":                     {"nx": 2879, "ny": 1441, "dx": 0.125,     "dy": 0.125},
    "meteofrance_arpege_world025":  {"nx": 1440, "ny": 721,  "dx": 0.25,      "dy": 0.25},
    "dwd_icon_d2":                  {"nx": 1215, "ny": 746,  "dx": 0.02,      "dy": 0.02},
}


# ---------------------------------------------------------------------------
# Utility I/O di rete
# ---------------------------------------------------------------------------
def _urlopen(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "MeteoRisk/1.2.5 multi-domain"})
    return urllib.request.urlopen(req, timeout=timeout)


def http_read_json(url, retries=3):
    """GET+parse JSON con retry/backoff. Ritorna (data, None) o (None, errore)."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            with _urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode("utf-8")), None
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(2 * attempt)
    return None, last


def download(url, dest, retries=3):
    """Scarica `url` in `dest` (atomico .part -> replace). Ritorna True/False."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    last = None
    for attempt in range(1, retries + 1):
        try:
            with _urlopen(url, timeout=180) as r, open(part, "wb") as fh:
                shutil.copyfileobj(r, fh, length=1 << 20)
            os.replace(part, dest)
            return True
        except Exception as exc:  # noqa: BLE001
            last = exc
            try:
                os.remove(part)
            except OSError:
                pass
            if attempt < retries:
                time.sleep(3 * attempt)
    print("%s [WARN] download fallito %s: %s" % (LOG, url, last))
    return False


def http_status(url, timeout=30):
    """Codice HTTP di una HEAD (200/404/403/5xx) o None se la rete non risponde.

    Serve SOLO al percorso `--allow-partial-run`: distingue un `.om` NON ANCORA
    PUBBLICATO (404: e' la fine del prefisso, ci si ferma) da un guasto di
    lettura su un oggetto che invece esiste (200: un retry e' legittimo, e se
    fallisce ancora va dichiarato come tale, non come "frame mancante").
    """
    try:
        req = urllib.request.Request(
            url, method="HEAD",
            headers={"User-Agent": "MeteoRisk/1.2.5 multi-domain"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:  # noqa: BLE001
        return None


def http_content_length(url, timeout=30):
    """HEAD leggero: Content-Length remoto, o None se non ottenibile.

    Usato SOLO nel ramo `--frames-cache` per decidere se un frame gia' in cache
    e' aggiornato. Se la HEAD fallisce (rete/endpoint) ritorna None e il chiamante
    ricade sulla dimensione del file locale (nessun download forzato).
    """
    try:
        req = urllib.request.Request(
            url, method="HEAD",
            headers={"User-Agent": "MeteoRisk/1.2.5 multi-domain"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) if cl is not None else None
    except Exception:  # noqa: BLE001
        return None


def fetch_frame(url, domain, valid_name, cache_dir, frames_cache, label="frame"):
    """Percorso locale del frame, riusando la cache quando valida.

    Con `frames_cache` attivo (cache persistente, ripristinata via
    actions/cache): file in `<frames_cache>/<domain>/<valid_name>.om` con un
    sidecar `.url` che lega il contenuto all'URL esatto della run. La sola
    Content-Length non identifica una run: URL diverso forza il download anche
    quando la dimensione coincide. Una HEAD controlla inoltre la completezza.

    Senza `frames_cache`: comportamento INVARIATO su `cache_dir`
    (`<cache_dir>/<domain>__<valid_name>.om`, flat, nessuna HEAD).

    Ritorna (percorso, riusato) oppure (None, False) se il download fallisce.
    """
    if frames_cache:
        dst = os.path.join(frames_cache, domain, valid_name + ".om")
        if _cached_path(url, domain, valid_name, frames_cache):
            return dst, True
        if download(url, dst):
            marker = dst + ".url"
            marker_tmp = marker + ".tmp"
            with open(marker_tmp, "w", encoding="utf-8") as fh:
                fh.write(url)
            os.replace(marker_tmp, marker)
            return dst, False
        return None, False

    dst = os.path.join(cache_dir, "%s__%s.om" % (domain, valid_name))
    if os.path.isfile(dst):
        return dst, True
    if download(url, dst):
        return dst, False
    return None, False


# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------
def parse_iso(ts):
    """'2026-10-11T15:00Z' -> datetime UTC (aware)."""
    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fmt_iso(dt):
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def valid_time_to_filename(vt):
    """'2026-10-11T15:00Z' -> '2026-10-11T1500' (nome file locale)."""
    return vt.replace(":", "").replace("Z", "")


def s3_url(domain, ref_time_iso, valid_time_iso):
    d = parse_iso(ref_time_iso)
    name = valid_time_to_filename(valid_time_iso)
    return "%s/%s/%04d/%02d/%02d/%02d00Z/%s.om" % (
        S3_BASE, domain, d.year, d.month, d.day, d.hour, name)


def s3_manifest_url(domain):
    return "%s/%s/latest.json" % (S3_BASE, domain)


# ---------------------------------------------------------------------------
# RUN PARZIALE (P1): scoperta del run in corso
# ---------------------------------------------------------------------------
# Regex sul XML di ListObjectsV2 (nessuna dipendenza da xml.etree: il listing
# e' l'unico punto che parla XML e bastano due campi).
_RE_LIST_KEY = re.compile(r"<Key>([^<]*)</Key>")
_RE_LIST_PREFIX = re.compile(
    r"<CommonPrefixes>\s*<Prefix>([^<]*)</Prefix>", re.DOTALL)
_RE_RUN_DIR = re.compile(r"^(\d{2})00Z$")


def s3_in_progress_url(domain):
    """`<dominio>/in-progress.json`: manifest del run IN PUBBLICAZIONE.

    MISURATO il 2026-10-24 su `ncep_gfs025`/`dwd_icon_eu`/`ecmwf_ifs025`: stesso
    schema di `latest.json` (`reference_time`, `valid_times`, `completed`,
    `last_modified_time`, `crs_wkt`) aggiornato mentre gli oggetti compaiono, e
    i suoi `valid_times` coincidono con i `.om` gia' presenti nel run (28/28 su
    `ncep_gfs025` 12Z).
    """
    return "%s/%s/in-progress.json" % (S3_BASE, domain)


def s3_run_meta_url(domain, ref_time_iso):
    """`<dominio>/<YYYY>/<MM>/<DD>/<HH>00Z/meta.json` del run (cresce in diretta)."""
    d = parse_iso(ref_time_iso)
    return "%s/%s/%04d/%02d/%02d/%02d00Z/meta.json" % (
        S3_BASE, domain, d.year, d.month, d.day, d.hour)


def s3_list_prefix(prefix, delimiter="/", timeout=60):
    """ListObjectsV2 anonimo sul bucket pubblico. `prefix` relativo al bucket.

    Ritorna `(keys, common_prefixes, errore)`; su 403/404/rete ritorna
    `(None, None, eccezione)` senza propagare: chi chiama decide il fallback
    (il percorso del flag OFF non arriva MAI qui).
    """
    url = "%s/?list-type=2&prefix=%s" % (
        S3_HOST, urllib.parse.quote(prefix, safe=""))
    if delimiter:
        url += "&delimiter=%s" % urllib.parse.quote(delimiter, safe="")
    try:
        with _urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, None, exc
    return (list(_RE_LIST_KEY.findall(body)),
            list(_RE_LIST_PREFIX.findall(body)), None)


def _run_candidate(payload, source, url):
    """Normalizza un manifest di run (latest/in-progress/meta). None se inutile.

    Richiede `reference_time` leggibile e `valid_times` NON vuoto: un run appena
    nato senza ancora un solo valid_time non e' una scoperta, e' un run che non
    c'e' (si resta su `latest.json`).
    """
    if not isinstance(payload, dict):
        return None
    ref = payload.get("reference_time")
    valid = payload.get("valid_times") or []
    if not ref or not valid:
        return None
    try:
        parse_iso(ref)
    except (ValueError, TypeError):
        return None
    return {
        "source": source,
        "url": url,
        "reference_time": ref,
        "valid_times": sorted(set(valid)),
        "completed": payload.get("completed"),
        "last_modified_time": payload.get("last_modified_time"),
        "crs_wkt": payload.get("crs_wkt") or "",
        "n_listed": len(valid),
        "discovered_at": _now_iso(),
    }


def _is_newer_run(ref_time, latest_ref):
    """True se `ref_time` e' STRETTAMENTE piu' recente di `latest.json`."""
    if not ref_time:
        return False
    if not latest_ref:
        return True
    try:
        return parse_iso(ref_time) > parse_iso(latest_ref)
    except (ValueError, TypeError):
        return False


def discover_partial_run(domain, latest=None, now=None,
                         probe_hours=PARTIAL_PROBE_HOURS):
    """Run piu' recente anche se `latest.json` NON e' ancora stato aggiornato.

    Ordine (il primo che produce un `reference_time` STRETTAMENTE piu' nuovo di
    quello di `latest.json` vince):
      1. `<dominio>/in-progress.json` — manifest del run in pubblicazione, 1 GET;
      2. listing del prefix `<dominio>/<YYYY>/<MM>/<DD>/` (ListObjectsV2,
         `delimiter=/`) -> directory `<HH>00Z/` -> `<run>/meta.json`, dalla piu'
         recente; copre `probe_hours` (minimo 2 giorni: oggi + ieri);
      3. nessuna scoperta -> None: si usa `latest.json` (comportamento storico).

    Il run scoperto puo' essere `completed: false`: in quel caso il chiamante
    pubblica un PREFISSO e vieta il NO-OP finche' la sorgente non e' completa.
    """
    latest_ref = (latest or {}).get("reference_time")

    # 1) in-progress.json (1 richiesta, nessun listing).
    url = s3_in_progress_url(domain)
    payload, _err = http_read_json(url)
    cand = _run_candidate(payload, "in-progress.json", url)
    if cand and _is_newer_run(cand["reference_time"], latest_ref):
        return cand

    # 2) listing per giorno -> <run>/meta.json.
    now = now or _dt.datetime.now(_dt.timezone.utc)
    ndays = max(2, int(probe_hours // 24) + 1)
    for back in range(ndays):
        day = now - _dt.timedelta(days=back)
        prefix = "%s/%s/%04d/%02d/%02d/" % (
            S3_LIST_PREFIX, domain, day.year, day.month, day.day)
        _keys, prefixes, err = s3_list_prefix(prefix, "/")
        if err is not None:
            print("%s [WARN] listing prefix non disponibile per '%s' (%s): %s"
                  % (LOG, domain, prefix, err))
            continue
        runs = []
        for p in (prefixes or []):
            name = p.rstrip("/").rsplit("/", 1)[-1]
            if _RE_RUN_DIR.match(name):
                runs.append(name)
        for name in sorted(runs, reverse=True):
            ref = "%04d-%02d-%02dT%s:%s:00Z" % (
                day.year, day.month, day.day, name[:2], name[2:4])
            meta_url = s3_run_meta_url(domain, ref)
            payload, _err = http_read_json(meta_url)
            cand = _run_candidate(payload, "prefix-listing+meta.json", meta_url)
            if cand and _is_newer_run(cand["reference_time"], latest_ref):
                return cand
    return None


# ---------------------------------------------------------------------------
# Griglie e crop (derivazione da crs_wkt + frame; GRIDS solo fallback/guardia)
# ---------------------------------------------------------------------------
def is_geographic_crs(crs_wkt):
    """True se il CRS e' geografico (WKT GEOGCRS...). PROJCRS/Lambert -> False."""
    return (crs_wkt or "").lstrip().upper().startswith(GEOGRAPHIC_CRS_PREFIX)


def crs_kind(crs_wkt):
    """Etichetta leggibile del tipo di CRS (per log/report SKIP)."""
    head = (crs_wkt or "").lstrip().split("[", 1)[0].strip()
    return head or "(assente)"


def parse_bbox(crs_wkt):
    """Estrae (latMin, lonMin, latMax, lonMax) dal `BBOX[...]` del WKT."""
    m = _BBOX_RE.search(crs_wkt or "")
    if not m:
        return None
    lat_min, lon_min, lat_max, lon_max = (float(x) for x in m.groups())
    return lat_min, lon_min, lat_max, lon_max


def derive_grid(crs_wkt, nx, ny):
    """Griglia RegularGridFromOrigin derivata da crs_wkt (BBOX) + nx/ny (shape).

    Ritorna il dict {nx,ny,lonMin,latMin,dx,dy} oppure None se BBOX assente o
    dimensioni non valide.
    """
    bbox = parse_bbox(crs_wkt)
    if bbox is None:
        return None
    lat_min, lon_min, lat_max, lon_max = bbox
    if nx is None or ny is None or int(nx) < 2 or int(ny) < 2:
        return None
    nx, ny = int(nx), int(ny)
    dx = (lon_max - lon_min) / (nx - 1)
    dy = (lat_max - lat_min) / (ny - 1)
    return {"nx": nx, "ny": ny, "lonMin": lon_min, "latMin": lat_min,
            "dx": dx, "dy": dy}


def grid_guard(domain, grid):
    """Confronta la griglia derivata con quella nota (GRIDS). Ritorna warning.

    Se il dominio NON e' in GRIDS non c'e' nulla da confrontare (nessun log).
    Scarto > 1% su dx/dy -> warning, ma il chiamante usa comunque i derivati.
    """
    known = GRIDS.get(domain)
    if not known:
        return []
    msgs = []
    for key in ("dx", "dy"):
        kv = known.get(key)
        gv = grid.get(key)
        if kv and gv and abs(gv - kv) / kv > 0.01:
            msgs.append("griglia derivata %s=%.6g != noto %.6g (>1%%): uso derivato"
                        % (key, gv, kv))
    return msgs


def _reader_shape(reader):
    """(ny, nx) dal PRIMO child array di un reader SYNC (o None)."""
    for i in range(reader.num_children):
        c = reader.get_child_by_index(i)
        if not c.is_scalar:
            shp = getattr(c, "shape", None)
            if shp is not None and len(shp) == 2:
                return int(shp[0]), int(shp[1])
    return None


def frame_shape(path):
    """(ny, nx) dal PRIMO child array del frame `.om` (o None)."""
    reader = OmFileReader.from_path(path)
    try:
        return _reader_shape(reader)
    finally:
        reader.close()


def bbox_to_indices(grid, bbox):
    """Converte bbox (lat_min, lon_min, lat_max, lon_max) in slice di indice.

    Ritorna dict con i0/i1 (lon, [i0,i1] inclusivo), j0/j1 (lat, [j0,j1]
    inclusivo) e la griglia del crop (out_grid). Clamp ai limiti della griglia.
    """
    lat_min, lon_min, lat_max, lon_max = bbox
    dx, dy = grid["dx"], grid["dy"]
    i0 = int(round((lon_min - grid["lonMin"]) / dx))
    i1 = int(math.floor((lon_max - grid["lonMin"]) / dx))
    j0 = int(round((lat_min - grid["latMin"]) / dy))
    j1 = int(math.floor((lat_max - grid["latMin"]) / dy))
    i0 = max(0, min(grid["nx"] - 1, i0))
    i1 = max(i0, min(grid["nx"] - 1, i1))
    j0 = max(0, min(grid["ny"] - 1, j0))
    j1 = max(j0, min(grid["ny"] - 1, j1))
    out_grid = {
        "nx": i1 - i0 + 1,
        "ny": j1 - j0 + 1,
        "lonMin": round(grid["lonMin"] + i0 * dx, 10),
        "latMin": round(grid["latMin"] + j0 * dy, 10),
        "dx": dx,
        "dy": dy,
    }
    return {"i0": i0, "i1": i1, "j0": j0, "j1": j1, "out_grid": out_grid}


def target_latlon(grid, idx):
    """Array lat/lon dei centri-cella del crop (ordine riga=lat)."""
    lats = grid["latMin"] + np.arange(idx["j0"], idx["j1"] + 1) * grid["dy"]
    lons = grid["lonMin"] + np.arange(idx["i0"], idx["i1"] + 1) * grid["dx"]
    return lats.astype(np.float64), lons.astype(np.float64)


def nearest_indices(grid, lats, lons):
    """Indici (jj, ii) della cella piu' vicina di `grid` a ogni (lat, lon)."""
    jj = np.rint((lats - grid["latMin"]) / grid["dy"]).astype(np.int64)
    ii = np.rint((lons - grid["lonMin"]) / grid["dx"]).astype(np.int64)
    jj = np.clip(jj, 0, grid["ny"] - 1)
    ii = np.clip(ii, 0, grid["nx"] - 1)
    return jj, ii


def _resample_window(window, jj, ii, jmin, imin):
    """Riduce una finestra alla griglia target (nearest). STESSA aritmetica per
    la lettura locale (sync) e per quella a intervalli (async)."""
    window = np.asarray(window, dtype=np.float32)
    return np.ascontiguousarray(window[np.ix_(jj - jmin, ii - imin)]).astype(np.float32)


def read_and_resample(reader, name, src_grid, tgt_lats, tgt_lons):
    """Legge `name` dal reader e lo porta sulla griglia target (nearest).

    Nel multi-dominio indipendente la griglia target E' la griglia del dominio
    stesso (per-domain crop): nearest = identita' perche' i centri-cella del target
    coincidono con quelli della sorgente, quindi il risultato e' il crop esatto.
    """
    jj, ii = nearest_indices(src_grid, tgt_lats, tgt_lons)
    jmin, jmax = int(jj.min()), int(jj.max())
    imin, imax = int(ii.min()), int(ii.max())
    child = reader.get_child_by_name(name)
    if child is None:
        raise KeyError(name)
    window = child.read_array((slice(jmin, jmax + 1), slice(imin, imax + 1)))
    return _resample_window(window, jj, ii, jmin, imin)


def array_child_names(reader):
    names = []
    for i in range(reader.num_children):
        c = reader.get_child_by_index(i)
        if not c.is_scalar:
            names.append(c.name)
    return set(names)


# ---------------------------------------------------------------------------
# Risoluzione variabili per frame (multi-dominio INDIPENDENTE, no secondario)
# ---------------------------------------------------------------------------
def resolve_direct(risk, child_names):
    """Primo candidato presente nei child; ritorna nome sorgente o None."""
    for cand in risk["sources"]:
        if cand in child_names:
            return cand
    return None


# ---------------------------------------------------------------------------
# Derivazioni (1.2.4.11): variabili PUBBLICATE che NON esistono nella sorgente.
# ---------------------------------------------------------------------------
# Coefficienti Magnus (formula richiesta): T in degC, RH in %.
MAGNUS_A = 17.62
MAGNUS_B = 243.12


def dew_point_magnus(t, rh):
    """Punto di rugiada (Magnus). T in degC, RH in %.

    alpha = ln(RH/100) + a*T/(b+T);  Td = b*alpha/(a-alpha).
    Casi limite DICHIARATI:
      - RH <= 0: ln(0) = -inf -> RH viene CLIPPATA a 0.1% (il risultato resta
        finito, il valore piu' basso ottenibile);
      - RH > 100: RH viene CLIPPATA a 100 (saturazione, Td = T);
      - T/RH assenti: la derivata NON viene calcolata (gestito da `derive_per_frame`,
        che salta la variabile con warning).
    T fuori intervallo fisico non e' atteso (per T <= -b il denominatore
    degenera): non gestito, i dati reali stanno in [-90, 60] degC.
    """
    t = np.asarray(t, dtype=np.float64)
    rh = np.asarray(rh, dtype=np.float64)
    rh = np.clip(rh, 0.1, 100.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        alpha = np.log(rh / 100.0) + MAGNUS_A * t / (MAGNUS_B + t)
        td = MAGNUS_B * alpha / (MAGNUS_A - alpha)
    return td.astype(np.float32)


def wind_speed_from_uv(u, v):
    """Velocita' del vento = sqrt(u^2 + v^2) (il segno delle componenti non conta)."""
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    return np.sqrt(u * u + v * v).astype(np.float32)


def wind_direction_from_uv(u, v):
    """Direzione METEOROLOGICA del vento (gradi, [0,360)): quella DA cui soffia.

    Convenzione della libreria client `@openmeteo/weather-map-layer`
    (getSourceVars per i nomi `_(?:speed|direction)_`): la direzione e' la
    secondaria della velocita'

        direction = (degrees(atan2(u, v)) + 180) % 360

    dove u = componente zonale (positiva verso EST) e v = componente
    meridionale (positiva verso NORD). Risultato: 0 = vento da NORD,
    90 = da OVEST, 180 = da SUD, 270 = da EST.
    """
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    return ((np.degrees(np.arctan2(u, v)) + 180.0) % 360.0).astype(np.float32)


def wrap_wind_direction_for_step(d):
    """Riporta a 0 le direzioni che il passo dichiarato quantizzerebbe a 360.

    La formula pura (`wind_direction_from_uv`) produce valori in [0,360), ma il
    writer memorizza int = round(valore / passo) ... nel nostro caso
    scale_factor = 1/passo, quindi valore = int/passo e una direzione in
    [360 - passo/2, 360) viene arrotondata a 360, FUORI dall'intervallo
    dichiarato. Poiche' 360 e 0 sono la stessa direzione, qui le si mappa a 0:
    il `.om` pubblicato resta sempre in [0,360). MISURATO su dwd_icon_eu reale:
    senza questo wrap 5 celle su 395569 contenevano 360.0.
    """
    d = np.asarray(d, dtype=np.float32)
    limit = np.float32(360.0 - 0.5 * WIND_DIRECTION_STEP)
    return np.where(d >= limit, np.float32(0.0), d).astype(np.float32)


def accumulate_precip_cum(cum, precip, shape):
    """Cumulata di precipitazione (STATEFUL): somma per cella in ordine di valid_time.

    `cum` = cumulata precedente (None al primo frame). `precip` = array del frame
    corrente o None se il frame NON espone `precipitation` (contribuisce 0: e' il
    caso del frame al reference_time). Ritorna la cumulata aggiornata np.float64.
    Il frame senza precipitazione inizializza comunque la cumulata a 0 (scelta
    DICHIARATA: precipitation_cum e' pubblicata su TUTTI i frame, cosi' l'insieme
    delle variabili resta coerente tra i frame).
    """
    if cum is None:
        cum = np.zeros(shape, dtype=np.float64)
    if precip is not None:
        cum = cum + np.asarray(precip, dtype=np.float64)
    return cum


def _derive_dew_point_2m(arrays):
    return dew_point_magnus(arrays["temperature_2m"],
                            arrays["relative_humidity_2m"])


def _derive_wind_speed_10m(arrays):
    return wind_speed_from_uv(arrays["wind_u_component_10m"],
                              arrays["wind_v_component_10m"])


def _derive_wind_speed_850hPa(arrays):
    return wind_speed_from_uv(arrays["wind_u_component_850hPa"],
                              arrays["wind_v_component_850hPa"])


def _derive_wind_speed_300hPa(arrays):
    return wind_speed_from_uv(arrays["wind_u_component_300hPa"],
                              arrays["wind_v_component_300hPa"])


def _derive_wind_direction_10m(arrays):
    return wrap_wind_direction_for_step(
        wind_direction_from_uv(arrays["wind_u_component_10m"],
                               arrays["wind_v_component_10m"]))


def _derive_wind_direction_850hPa(arrays):
    return wrap_wind_direction_for_step(
        wind_direction_from_uv(arrays["wind_u_component_850hPa"],
                               arrays["wind_v_component_850hPa"]))


def _derive_wind_direction_300hPa(arrays):
    return wrap_wind_direction_for_step(
        wind_direction_from_uv(arrays["wind_u_component_300hPa"],
                               arrays["wind_v_component_300hPa"]))


def _derive_wind_speed_10hPa(arrays):
    return wind_speed_from_uv(arrays["wind_u_component_10hPa"],
                              arrays["wind_v_component_10hPa"])


def _derive_wind_direction_10hPa(arrays):
    return wrap_wind_direction_for_step(
        wind_direction_from_uv(arrays["wind_u_component_10hPa"],
                               arrays["wind_v_component_10hPa"]))


def snow_fraction(swe, precipitation):
    """Frazione di precipitazione caduta come NEVE: `swe / precipitation`.

    DERIVATA NOSTRA, non una classificazione del modello: e' il RAPPORTO fra
    due campi del modello (`snowfall_water_equivalent` in mm di acqua
    equivalente e `precipitation` in mm), pubblicato come numero CONTINUO in
    [0,1]. Qui NON c'e' alcuna soglia neve/pioggia: la soglia ("neve se
    frazione >= 0.5") e' una decisione di resa e vive nel renderer/client.

    Regole dichiarate (e verificate sui frame reali, vedi report):
      - `precipitation <= 0` o NaN -> NaN ("null"): senza precipitazione la
        fase NON e' definibile; 0.0 sarebbe l'affermazione "piove", che e' una
        cosa diversa.
      - `precipitation > 0` e `swe == 0` -> 0.0 (tutta pioggia).
      - `swe == precipitation` -> 1.0 (tutta neve).
      - `swe` NaN con `precipitation > 0` -> NaN: dato assente, non "0 neve".
      - risultato LIMITATO a [0,1]: su ECMWF il campo neve e' una STIMA DEL
        FORNITORE e puo' superare la precipitazione (misurato: 40 celle su 661
        con `precipitation - showers - swe` negativo, fino a -3.5 mm). Il
        limite dichiara "tutta neve" senza inventare frazioni > 1; e non si
        deriva MAI `pioggia = precipitation - swe` (su ECMWF i campi non sono
        coerenti fra loro).
    """
    pr = np.asarray(precipitation, dtype=np.float32)
    sw = np.asarray(swe, dtype=np.float32)
    out = np.full(pr.shape, np.nan, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(sw, pr, out=out, where=(pr > 0))
        out = np.clip(out, np.float32(0.0), np.float32(1.0))
    return out.astype(np.float32)


def _derive_snow_fraction(arrays):
    return snow_fraction(arrays["snowfall_water_equivalent"],
                         arrays["precipitation"])


# ---------------------------------------------------------------------------
# FINESTRA 6 ORE (2026-10-26): funzioni PURE, testabili senza rete ne' frame.
# ---------------------------------------------------------------------------
def window6h_plan(valid_times, index, hours=WINDOW6H_HOURS,
                  tol_s=WINDOW6H_TOLERANCE_S):
    """Indici dei frame che tassellano ESATTAMENTE `[t_index - hours, t_index]`.

    Regola (dichiarata e verificata, vedi commento di `VAR_SWE_DOMAINS`/
    `WINDOW6H_HOURS`): si somma all'indietro il passo `t_i - t_{i-1}` finche' il
    totale non e' ESATTAMENTE `hours` (±`tol_s`). Non e' "gli ultimi N frame":
    con una serie a passo variabile (1 h, 3 h, 6 h) il numero di frame cambia e
    la somma dei passi e' l'unica cosa che dice quante ore copre la finestra.

    `valid_times`: lista ORDINATA di datetime (aware) del dominio.
    `index`: posizione del frame di riferimento (il piu' recente della finestra).

    Ritorna `(indici, None)` con gli indici dal PIU' VECCHIO al piu' recente,
    oppure `(None, "window_not_tileable")`. "Non tassellabile" copre i tre casi
    reali: (a) la run inizia a meno di 6 h dal frame e non ci sono abbastanza
    frame precedenti; (b) il passo successivo andrebbe OLTRE 6 h (es. passo 4 h:
    4 + 4 = 8 > 6, nessun sottoinsieme da 6 h); (c) passi non positivi.
    """
    if index <= 0 or index >= len(valid_times):
        return None, "window_not_tileable"
    target = float(hours) * 3600.0
    total = 0.0
    i = index
    while i > 0:
        step = (valid_times[i] - valid_times[i - 1]).total_seconds()
        if step <= 0:
            return None, "window_not_tileable"
        if total + step > target + tol_s:
            return None, "window_not_tileable"
        total += step
        i -= 1
        if abs(total - target) <= tol_s:
            # `step` appena sommato e' quello del frame `i+1` (copre
            # `(t_i, t_i+1]`): la finestra e' quindi `i+1 .. index`, NON
            # `i .. index` (includerebbe un frame di troppo, cioe' ore in piu').
            return list(range(i + 1, index + 1)), None
    return None, "window_not_tileable"


def window6h_entries(valid_times, index, buffer, hours=WINDOW6H_HOURS,
                     tol_s=WINDOW6H_TOLERANCE_S):
    """Entry (dict) della finestra 6 h, oppure `(None, motivo)`.

    `buffer` = frame gia' processati, ciascuno un dict con almeno `index`
    (posizione in `valid_times`), `valid_time`, `step_s`, `ref_unix`,
    `precipitation`, `snowfall_water_equivalent`. Un frame della finestra che
    NON e' nel buffer (letto male, saltato) rende la finestra non calcolabile:
    meglio un null dichiarato che una somma su 5 frame spacciata per 6 h.
    """
    plan, reason = window6h_plan(valid_times, index, hours, tol_s)
    if plan is None:
        return None, reason
    by_index = {e["index"]: e for e in buffer}
    if any(i not in by_index for i in plan):
        return None, "frame_missing_in_window"
    return [by_index[i] for i in plan], None


def window6h_reason(entries, run_ref_unix):
    """Motivo per cui la finestra NON e' utilizzabile (None = utilizzabile).

    Controlli in QUEST'ORDINE (il primo che scatta decide il motivo):
      1. `reference_time_unreadable`: in un frame manca lo scalare
         `forecast_reference_time` (non e' verificabile la provenienza);
      2. `mixed_reference_time`: quel frame viene da un'ALTRA run. GUARDIA
         OBBLIGATORIA: la cache dei frame e' piatta e chiavata sul solo nome
         (`<cache_dir>/<dominio>__<VALID>.om`), quindi un file vecchio con lo
         stesso valid_time potrebbe essere riusato in silenzio: senza questo
         confronto la finestra mescolerebbe due run;
      3. `frame_missing_in_window`: un frame della finestra non ha
         `precipitation` (a T+0 i campi accumulati non esistono);
      4. `window_shape_mismatch`: griglie di uscita diverse fra i frame (non
         deve accadere; se accade, la somma sarebbe fra celle diverse).
    """
    if not entries:
        return "window_not_tileable"
    for e in entries:
        ref = e.get("ref_unix")
        if ref is None:
            return "reference_time_unreadable"
        if int(ref) != int(run_ref_unix):
            return "mixed_reference_time"
    for e in entries:
        if e.get("precipitation") is None:
            return "frame_missing_in_window"
    shape = None
    for e in entries:
        p_shape = tuple(np.shape(e["precipitation"]))
        if shape is None:
            shape = p_shape
        elif p_shape != shape:
            return "window_shape_mismatch"
    return None


def precipitation_6h(entries):
    """Somma per cella di `precipitation` sui frame della finestra (mm).

    Nessun riempimento: i frame a monte non devono arrivare qui (li esclude
    `window6h_reason`). Accumulo in float64 e uscita float32, come il resto del
    motore: 6 valori da 0.1 mm in float32 accumulerebbero errore visibile.
    """
    acc = None
    for e in entries:
        p = np.asarray(e["precipitation"], dtype=np.float64)
        acc = p if acc is None else acc + p
    return np.asarray(acc, dtype=np.float32)


def snow_fraction_6h(entries):
    """Frazione nevosa PESATA sulla finestra: `Σ swe / Σ precipitation`.

    NON e' la media delle frazioni dei singoli frame (quella darebbe lo stesso
    peso a un'ora con 0,1 mm e a una con 30 mm). MISURATO su ARPEGE
    2026-10-26T12..17Z (6 frame a 1 h, 113.362 celle bagnate): differenza
    pesata-media mediana 0,0000 ma p99 0,1250 e massimo 1,0000; 1.443 celle
    oltre 0,10; 254 celle con pesata >= 0,5 e media < 0,5 (tratteggio neve
    acceso con la pesata, spento con la media) e 44 nel verso opposto. Caso
    reale 42,75N 31,75W: precip 0,1/0,2/0,2/0,5/1,6/2,7 mm, swe 0/0/0/0,2/1,6/2,7
    -> pesata 0,849, media 0,400.

    Regole dichiarate:
      - `swe` assente in un frame: il numeratore e' ignoto SOLO dove QUEL frame
        ha `precipitation > 0` (dove non piove ne' nevica quel frame contribuisce
        0 a entrambi i termini) -> NaN in quelle celle;
      - `Σ precipitation == 0` -> NaN: 0/0 non e' "0% neve", e' "non definito";
      - risultato LIMITATO a [0,1]: il campo neve del fornitore puo' superare la
        precipitazione (misurato su ARPEGE: cella 32,75N 16,50E con swe 0,1 mm e
        precip 0 nei primi 4 frame -> rapporto 2,5 prima del limite).
    """
    num = None
    den = None
    unknown = None
    for e in entries:
        p = np.asarray(e["precipitation"], dtype=np.float64)
        if den is None:
            den = np.zeros(p.shape, dtype=np.float64)
            num = np.zeros(p.shape, dtype=np.float64)
            unknown = np.zeros(p.shape, dtype=bool)
        den = den + p
        swe = e.get("snowfall_water_equivalent")
        if swe is None:
            unknown |= (p > 0)
            continue
        num = num + np.asarray(swe, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = num / np.where(den != 0.0, den, 1.0)
    out = np.where((den > 0) & ~unknown, ratio, np.nan)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def compute_window6h(entries, run_ref_unix, reason=None):
    """Calcola le DUE variabili della finestra 6 h, con motivo del null.

    Ritorna `{nome: {"array": np.ndarray|None, "null_reason": str|None},
    "window": {...}|None}`. `reason` = motivo GIA' accertato dal chiamante
    (finestra non tassellabile / frame mancante nel buffer): se c'e', le due
    variabili sono null con quel motivo e non si calcola nulla.
    """
    info = {
        "precipitation_6h": {"array": None, "null_reason": None},
        "snow_fraction_6h": {"array": None, "null_reason": None},
        "window": None,
    }
    if reason is None:
        reason = window6h_reason(entries, run_ref_unix)
    if reason is not None:
        for name in VARS_WINDOW6H:
            info[name]["null_reason"] = reason
        return info
    total_s = 0.0
    for e in entries:
        step = e.get("step_s")
        total_s += float(step) if step is not None else 0.0
    info["window"] = {
        "start": entries[0]["valid_time"],
        "end": entries[-1]["valid_time"],
        "n_frames": len(entries),
        "hours": round(total_s / 3600.0, 6),
    }
    info["precipitation_6h"]["array"] = precipitation_6h(entries)
    frac = snow_fraction_6h(entries)
    info["snow_fraction_6h"]["array"] = frac
    if np.all(np.isnan(frac)):
        # null su TUTTE le celle: si dichiara il perche' (se invece e' null solo
        # in parte, il chiamante registra `null_cells` e non un motivo globale).
        info["snow_fraction_6h"]["null_reason"] = (
            "swe_missing_in_window"
            if any(e.get("snowfall_water_equivalent") is None for e in entries)
            else "zero_precipitation_in_window")
    return info


# Registro delle derivazioni: nome PUBBLICATO -> {sources, func, stateful}.
# `sources` = nomi (pubblicati o di INPUT) che devono essere presenti; `func`
# (arrays) = derivata per-frame; `stateful: True` = NON per-frame ma accumulata
# dal chiamante lungo la serie (`process_domain`).
# ATOMICITA' (velocita' + direzione): `wind_speed_X` e `wind_direction_X`
# dichiarano le STESSE due componenti u/v come `sources`, quindi
# `derive_per_frame` calcola la coppia o la OMETTE ENTRAMBI (se manca anche una
# sola componente, per entrambe `missing` e' non vuoto). La coppia e' percio'
# sempre o tutta presente o tutta assente nel `.om` pubblicato: nessuna
# variabile orfana che manderebbe in errore il reader del client (che pretende
# la secondaria `wind_direction_*` per ogni `wind_speed_*`).
DERIVED_VARS = {
    "dew_point_2m": {
        "sources": ["temperature_2m", "relative_humidity_2m"],
        "func": _derive_dew_point_2m,
    },
    "wind_speed_10m": {
        "sources": ["wind_u_component_10m", "wind_v_component_10m"],
        "func": _derive_wind_speed_10m,
    },
    "wind_direction_10m": {
        "sources": ["wind_u_component_10m", "wind_v_component_10m"],
        "func": _derive_wind_direction_10m,
    },
    "wind_speed_850hPa": {
        "sources": ["wind_u_component_850hPa", "wind_v_component_850hPa"],
        "func": _derive_wind_speed_850hPa,
    },
    "wind_direction_850hPa": {
        "sources": ["wind_u_component_850hPa", "wind_v_component_850hPa"],
        "func": _derive_wind_direction_850hPa,
    },
    "wind_speed_300hPa": {
        "sources": ["wind_u_component_300hPa", "wind_v_component_300hPa"],
        "func": _derive_wind_speed_300hPa,
    },
    "wind_direction_300hPa": {
        "sources": ["wind_u_component_300hPa", "wind_v_component_300hPa"],
        "func": _derive_wind_direction_300hPa,
    },
    # --- livello 10 hPa (calcolate SOLO per i domini di VAR_10HPA_DOMAINS:
    # il gate e' applicato DUE volte, in `risk_vars_for`/`read_vars_for`
    # (nessuna lettura) e in `derive_per_frame` (nessuna derivazione anche se
    # le componenti fossero presenti negli `arrays` per un altro motivo).
    "wind_speed_10hPa": {
        "sources": ["wind_u_component_10hPa", "wind_v_component_10hPa"],
        "func": _derive_wind_speed_10hPa,
    },
    "wind_direction_10hPa": {
        "sources": ["wind_u_component_10hPa", "wind_v_component_10hPa"],
        "func": _derive_wind_direction_10hPa,
    },
    "precipitation_cum": {
        "sources": ["precipitation"],
        "func": None,
        "stateful": True,
    },
    # --- FASE NEVE (2026-10-25) — DERIVATA NOSTRA, non dato del modello.
    # `snow_fraction = snow_fraction(swe, precipitation)`: rapporto fra due
    # campi del modello, continuo in [0,1], nessuna soglia (vedi docstring di
    # `snow_fraction`). Calcolata SOLO per i domini di `VAR_SWE_DOMAINS`: il
    # gate e' applicato DUE volte, in `risk_vars_for`/`read_vars_for`
    # (nessuna lettura, nessuna voce nel manifest) e qui sotto (nessuna
    # derivazione anche se le sorgenti fossero presenti negli `arrays` per un
    # altro motivo).
    "snow_fraction": {
        "sources": ["snowfall_water_equivalent", "precipitation"],
        "func": _derive_snow_fraction,
    },
    # --- FINESTRA 6 ORE (2026-10-26) — DERIVATE STATEFUL NOSTRE ------------
    # Non sono calcolabili frame-per-frame: servono i frame PRECEDENTI, quindi
    # `func` e' None e l'accumulo lo fa `process_domain` (finestra scorrevole,
    # vedi `window6h_plan`/`compute_window6h`). Registrate qui perche' cosi'
    # `missing_published_warnings` non le segnala come "assenti nella sorgente".
    # Gate per dominio: `domain_supports_window6h` (stessa allowlist della neve,
    # applicata anche da `_filter_swe` sulla lista delle variabili).
    "precipitation_6h": {
        "sources": ["precipitation"],
        "func": None,
        "stateful": True,
    },
    "snow_fraction_6h": {
        "sources": ["snowfall_water_equivalent", "precipitation"],
        "func": None,
        "stateful": True,
    },
}


def derive_per_frame(arrays, meta, domain):
    """Applica le derivazioni STATELESS e aggiorna `arrays`/`meta` (in place).

    Unica implementazione condivisa dai due percorsi di lettura (locale/reader e
    range): NON duplicata nei due punti. Ritorna i warning per le derivate non
    calcolabili (una sorgente assente). Le derivate stateful (precipitation_cum)
    sono escluse: le accumula `process_domain`.
    """
    warnings = []
    for name, spec in DERIVED_VARS.items():
        if spec.get("stateful"):
            continue
        if name in VARS_10HPA_SET and not domain_supports_10hpa(domain):
            # ALLOWLIST 10 hPa: per un dominio non ammesso la derivata non deve
            # esistere. Non e' un errore e NON produce un warning (la variabile
            # non e' richiesta per quel dominio): stesso motivo di
            # `risk_vars_for`/`read_vars_for`, applicato qui come seconda
            # barriera nel caso gli `arrays` contengano le componenti u/v a
            # 10 hPa per un percorso diverso.
            continue
        if name in VARS_SWE_SET and not domain_supports_swe(domain):
            # ALLOWLIST fase neve: `ncep_gfs025` non ha alcuna variabile di
            # superficie. Se per un percorso diverso gli `arrays` contenessero
            # `precipitation` + `snowfall_water_equivalent`, la frazione NON
            # deve essere pubblicata e non deve produrre warning (stessa
            # seconda barriera del 10 hPa).
            continue
        srcs = spec["sources"]
        missing = [s for s in srcs if s not in arrays]
        if missing:
            warnings.append(
                "variabile derivata '%s' non calcolabile nel dominio '%s': "
                "manca %s" % (name, domain, ",".join(missing)))
            continue
        arrays[name] = spec["func"](arrays)
        meta[name] = {"source_domain": domain, "derived": True,
                      "derived_from": list(srcs)}
    return warnings


def missing_published_warnings(risk_vars, arrays, domain):
    """Warning per le variabili PUBBLICATE DIRETTE non risolte (file-per-file).

    Esclude gli INPUT (letti ma non pubblicati) e le derivate (gestite da
    `derive_per_frame` / dall'accumulo stateful).
    """
    warns = []
    for rv in risk_vars:
        name = rv["name"]
        if name in V2_INPUT_VAR_NAMES or name in DERIVED_VARS:
            continue
        if name not in arrays:
            warns.append("variabile '%s' assente nel dominio '%s' (%s): omessa"
                         % (name, domain, ",".join(rv["sources"])))
    return warns


def resolve_domain_frame(reader, grid, risk_vars, tgt_lats, tgt_lons, idx, domain):
    """Costruisce gli array di output del frame del dominio + metadati.

    Ritorna (arrays, variables_meta, warnings). `arrays` e' dict
    nome -> np.float32 crop: variabili PUBBLICATE (dirette + derivate) piu' gli
    INPUT (letti per le derivate, NON pubblicati e assenti da `meta`).
    `risk_vars` e' l'UNIONE pubblicate + input (`V2_READ_VARS`). Nessun dominio
    secondario: la griglia sorgente E' la griglia del dominio (crop esatto).
    """
    arrays = {}
    meta = {}
    warnings = []

    names = array_child_names(reader)

    # 1) risoluzione diretta (file-per-file) dalla griglia del dominio
    for rv in risk_vars:
        src = resolve_direct(rv, names)
        if src is None:
            continue
        arrays[rv["name"]] = read_and_resample(
            reader, src, grid, tgt_lats, tgt_lons)
        if rv["name"] not in V2_INPUT_VAR_NAMES:
            meta[rv["name"]] = {"source_domain": domain, "source_var": src,
                                "derived": False}

    # 2) derivazione per-frame (stateless), condivisa coi due percorsi di lettura
    warnings.extend(derive_per_frame(arrays, meta, domain))

    # 3) variabili PUBBLICATE dirette richieste ma irrisolte -> warning
    warnings.extend(missing_published_warnings(risk_vars, arrays, domain))

    return arrays, meta, warnings


# ---------------------------------------------------------------------------
# Lettura SELETTIVA a intervalli (range GET) delle SOLE variabili pubblicate
# ---------------------------------------------------------------------------
# Ogni frame sorgente contiene molte piu' variabili di quelle che pubblichiamo
# (es. `ncep_gfs025`: ~315 array, ne pubblichiamo ~10; `ncep_gfs013`: 16 array,
# ne pubblichiamo ~5) e pesa decine/centinaia di MB. `omfiles` sa leggere un
# `.om` remoto a INTERVALLI via fsspec (HTTP Range): si apre il frame remoto e
# si leggono SOLO i child richiesti, senza scaricare l'intero file. `crs_wkt` e
# la shape della griglia (letture di header, piccole) vengono lette comunque.
#
# FALLBACK ESPLICITO: se fsspec/HTTP o il formato remoto non funzionano il
# motore RICADE sul download completo, con `[WARN]` inequivocabile (dominio/
# frame/motivo) e contatori registrati nel manifest (`download.mode`,
# `download.fallback_count`, ...). Il fallback NON e' mai silenzioso.
class RangeStats(object):
    """Byte e numero di richieste HTTP di un frame letto a intervalli."""

    __slots__ = ("requests", "bytes")

    def __init__(self):
        self.requests = 0
        self.bytes = 0


def range_supported():
    """True se la lettura a intervalli e' disponibile (fsspec + reader async)."""
    return _fsspec is not None and OmFileReaderAsync is not None


# Un SOLO event loop per processo. fsspec tiene in cache l'istanza del filesystem
# (e la sua sessione aiohttp): con `asyncio.run()` per-frame una sessione creata
# in un loop chiuso verrebbe riusata -> errore reale "Event loop is closed" gia'
# osservato. Qui il loop e' persistente e OGNI lettura apre un fs NUOVO (sessione
# fresca), chiuso a fine lettura: niente sessioni stantie tra frame.
_HTTP_LOOP = None
_ACTIVE_STATS = None
_DOMAIN_CHILD_MAP_CACHE = {}
# Scalari obbligatori letti da OGNI frame (oltre agli array delle variabili):
# `crs_wkt` (crop) e `forecast_reference_time` (guardia `mixed_reference_time`
# della finestra 6 h). Costo misurato sul percorso a cache calda: +1 richiesta
# Range da ~39 B per frame; gli array della finestra NON costano richieste
# aggiuntive (`precipitation` e `swe` sono gia' letti per le altre variabili).
_FRAME_SCALARS = ("crs_wkt", "forecast_reference_time")


def _http_loop():
    global _HTTP_LOOP
    if _HTTP_LOOP is None or _HTTP_LOOP.is_closed():
        _HTTP_LOOP = asyncio.new_event_loop()
    return _HTTP_LOOP


def _new_http_fs():
    """fs HTTP asincrono NUOVO e strumentato (conta byte/richieste HTTP)."""
    fs = _fsspec.filesystem("http", asynchronous=True,
                            skip_instance_cache=True)
    orig = fs._cat_file

    async def _cat_file(path, start=None, end=None, **kwargs):
        data = await orig(path, start=start, end=end, **kwargs)
        if _ACTIVE_STATS is not None:
            _ACTIVE_STATS.requests += 1
            _ACTIVE_STATS.bytes += len(data)
        return data

    fs._cat_file = _cat_file
    return fs


async def _close_http_fs(fs):
    """Chiude la sessione aiohttp del fs (best-effort)."""
    try:
        sess = getattr(fs, "_session", None)
        if sess is not None:
            await sess.close()
    except Exception:  # noqa: BLE001
        pass


def _selective_run(coro, stats):
    """Esegue `coro` sul loop persistente, contando byte/richieste in `stats`."""
    global _ACTIVE_STATS
    _ACTIVE_STATS = stats
    try:
        return _http_loop().run_until_complete(coro)
    finally:
        _ACTIVE_STATS = None


def close_http():
    """Chiude il loop persistente (best-effort, a fine run)."""
    global _HTTP_LOOP
    try:
        if _HTTP_LOOP is not None and not _HTTP_LOOP.is_closed():
            _HTTP_LOOP.close()
    except Exception:  # noqa: BLE001
        pass
    _HTTP_LOOP = None


async def _areader_shape(reader):
    """(ny, nx) dal PRIMO child array di un reader ASYNC (o None)."""
    for i in range(reader.num_children):
        c = await reader.get_child_by_index(i)
        if not c.is_scalar:
            shp = getattr(c, "shape", None)
            if shp is not None and len(shp) == 2:
                return int(shp[0]), int(shp[1])
    return None


async def _selective_shape_async(url):
    fs = _new_http_fs()
    try:
        root = await OmFileReaderAsync.from_fsspec(fs, url)
        try:
            return await _areader_shape(root)
        finally:
            root.close()
    finally:
        await _close_http_fs(fs)


async def _selective_arrays_async(url, domain, risk_vars, grid, tgt_lats,
                                  tgt_lons):
    """Legge dal frame remoto SOLO le variabili di `risk_vars` presenti.

    Risoluzione nomi, finestra di crop e conversione sono IDENTICHE al percorso
    locale: `resolve_direct`/`nearest_indices`/`_resample_window` sono gli stessi
    (l'unica differenza e' l'I/O, che qui avviene a intervalli sul file remoto).
    """
    fs = _new_http_fs()
    try:
        root = await OmFileReaderAsync.from_fsspec(fs, url)
    except BaseException:  # noqa: BLE001
        await _close_http_fs(fs)
        raise
    try:
        sem = asyncio.Semaphore(32)
        cache_key = (domain, int(root.num_children))
        cached = _DOMAIN_CHILD_MAP_CACHE.get(cache_key)
        children_by_name = {}
        names = None
        shape = None

        if cached is not None:
            idx_map = cached.get("indices") or {}
            cand_names = set(idx_map.keys()) - {"crs_wkt"}
            needed_names = []
            seen_needed = set()
            for rv in risk_vars:
                src = resolve_direct(rv, cand_names)
                if src is not None and src in idx_map and src not in seen_needed:
                    seen_needed.add(src)
                    needed_names.append(src)
            for nm in _FRAME_SCALARS:
                if nm in idx_map and nm not in seen_needed:
                    seen_needed.add(nm)
                    needed_names.append(nm)

            async def _get_cached_child(nm):
                async with sem:
                    return await root.get_child_by_index(idx_map[nm])

            fetched = await asyncio.gather(
                *[_get_cached_child(nm) for nm in needed_names],
                return_exceptions=True)
            cache_ok = True
            for nm, c in zip(needed_names, fetched):
                if (isinstance(c, BaseException) or c is None
                        or getattr(c, "name", None) != nm):
                    cache_ok = False
                    break
                children_by_name[nm] = c
            if cache_ok:
                names = cand_names
                shape = cached.get("shape")
            else:
                _DOMAIN_CHILD_MAP_CACHE.pop(cache_key, None)
                children_by_name = {}
                names = None
                shape = None

        if names is None:
            async def _get_child_idx(i):
                async with sem:
                    return await root.get_child_by_index(i)

            all_children = await asyncio.gather(
                *[_get_child_idx(i) for i in range(root.num_children)],
                return_exceptions=True)
            for c in all_children:
                if isinstance(c, BaseException):
                    raise c
            idx_map = {}
            names = set()
            shape = None
            for i, c in enumerate(all_children):
                if c is None:
                    continue
                children_by_name[c.name] = c
                idx_map[c.name] = i
                if c.is_scalar:
                    continue
                names.add(c.name)
                if shape is None:
                    shp = getattr(c, "shape", None)
                    if shp is not None and len(shp) == 2:
                        shape = (int(shp[0]), int(shp[1]))
            if shape is not None and idx_map:
                _DOMAIN_CHILD_MAP_CACHE[cache_key] = {
                    "shape": shape,
                    "indices": idx_map,
                }

        crs_frame = ""
        try:
            crs = children_by_name.get("crs_wkt")
            if crs is not None:
                crs_frame = crs.read_scalar()
        except Exception:  # noqa: BLE001
            crs_frame = ""
        # Provenienza del frame (guardia `mixed_reference_time` della finestra
        # 6 h): in cache calda il child e' in `needed_names`, quindi qui non
        # serve nessuna richiesta in piu'; in cache fredda arriva col resto.
        ref_child = children_by_name.get("forecast_reference_time")
        if ref_child is None:
            try:
                async with sem:
                    ref_child = await root.get_child_by_name(
                        "forecast_reference_time")
                if ref_child is not None:
                    children_by_name["forecast_reference_time"] = ref_child
            except Exception:  # noqa: BLE001
                ref_child = None
        ref_unix = _ref_unix_from_scalar(ref_child)

        arrays = {}
        meta = {}
        warnings = []
        jj, ii = nearest_indices(grid, tgt_lats, tgt_lons)
        jmin, jmax = int(jj.min()), int(jj.max())
        imin, imax = int(ii.min()), int(ii.max())
        sel = (slice(jmin, jmax + 1), slice(imin, imax + 1))
        plan = []
        for rv in risk_vars:
            src = resolve_direct(rv, names)
            if src is not None:
                plan.append((rv["name"], src))

        async def _read_one(src):
            child = children_by_name.get(src)
            if child is None:
                async with sem:
                    child = await root.get_child_by_name(src)
            async with sem:
                window = await child.read_array(sel)
            return _resample_window(window, jj, ii, jmin, imin)

        # return_exceptions=True: TUTTE le letture della finestra arrivano a
        # termine prima di procedere, cosi' non restano task pendenti che
        # terrebbero aperta la sessione HTTP (chiusa nel finally).
        results = await asyncio.gather(*[_read_one(src) for _, src in plan],
                                       return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                raise r
        windows = results
        for (name, src), arr in zip(plan, windows):
            arrays[name] = arr
            if name not in V2_INPUT_VAR_NAMES:
                meta[name] = {"source_domain": domain, "source_var": src,
                              "derived": False}
        # derivazione per-frame (stateless) + warning delle PUBBLICATE irrisolte:
        # STESSA logica del percorso locale (`resolve_domain_frame`).
        warnings.extend(derive_per_frame(arrays, meta, domain))
        warnings.extend(missing_published_warnings(risk_vars, arrays, domain))
        return {"shape": shape, "crs_frame": crs_frame, "arrays": arrays,
                "meta": meta, "warnings": warnings, "ref_unix": ref_unix}
    finally:
        root.close()
        await _close_http_fs(fs)


def probe_frame_shape_range(url, retries=3):
    """Shape del frame via lettura a intervalli. (shape, RangeStats, errore)."""
    stats = RangeStats()
    last = None
    for attempt in range(1, retries + 1):
        try:
            shape = _selective_run(_selective_shape_async(url), stats)
            return shape, stats, None
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None, stats, last


def read_frame_selective(url, domain, risk_vars, grid, tgt_lats, tgt_lons,
                         retries=3):
    """Legge SOLO le variabili pubblicate via range GET.

    Ritorna (dict|None, RangeStats, errore). Un numero LIMITATO di tentativi
    (stesso pattern di `download`) assorbe disconnessioni transitorie; esauriti
    i tentativi il chiamante RICADE sul download completo (fallback esplicito).
    """
    stats = RangeStats()
    last = None
    for attempt in range(1, retries + 1):
        try:
            res = _selective_run(_selective_arrays_async(
                url, domain, risk_vars, grid, tgt_lats, tgt_lons), stats)
            return res, stats, None
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None, stats, last


def _ref_unix_from_scalar(child):
    """`forecast_reference_time` di un frame (unix int) o None se illeggibile.

    Serve alla guardia `mixed_reference_time` della finestra 6 h: la cache dei
    frame e' piatta e chiavata sul solo nome, quindi la PROVENIENZA va letta dal
    contenuto del file, non dedotta dal percorso. Misurato il 2026-10-26: lo
    scalare esiste nei frame SORGENTE di TUTTI e 7 i domini e coincide col
    `reference_time` del manifest.
    """
    try:
        if child is None:
            return None
        return int(child.read_scalar())
    except Exception:  # noqa: BLE001
        return None


def _read_local_frame(path, domain, risk_vars, grid, tgt_lats, tgt_lons, idx):
    """Legge il frame da un file LOCALE (percorso sync invariato). Ritorna dict."""
    reader = OmFileReader.from_path(path)
    try:
        shape = _reader_shape(reader)
        crs_frame = ""
        ref_unix = None
        try:
            crs = reader.get_child_by_name("crs_wkt")
            if crs is not None:
                crs_frame = crs.read_scalar()
        except Exception:  # noqa: BLE001
            crs_frame = ""
        try:
            ref_unix = _ref_unix_from_scalar(
                reader.get_child_by_name("forecast_reference_time"))
        except Exception:  # noqa: BLE001
            ref_unix = None
        arrays, meta, warnings = resolve_domain_frame(
            reader, grid, risk_vars, tgt_lats, tgt_lons, idx, domain)
        return {"shape": shape, "crs_frame": crs_frame, "arrays": arrays,
                "meta": meta, "warnings": warnings, "ref_unix": ref_unix}
    finally:
        reader.close()


def _cached_path(url, domain, valid_name, frames_cache):
    """Riusa la cache solo se URL sorgente e dimensione corrispondono."""
    if not frames_cache:
        return None
    dst = os.path.join(frames_cache, domain, valid_name + ".om")
    if not os.path.isfile(dst):
        return None
    marker = dst + ".url"
    try:
        with open(marker, "r", encoding="utf-8") as fh:
            cached_url = fh.read()
    except OSError:
        cached_url = ""
    if cached_url != url:
        print("%s   frame cache di altra run (%s): non riusato"
              % (LOG, dst))
        return None
    expected = http_content_length(url)
    local = os.path.getsize(dst)
    if expected is None or local == expected:
        print("%s   frame reuse (frames-cache %s, %d byte%s)"
              % (LOG, dst, local,
                 "" if expected is None else ", Content-Length ok"))
        return dst
    print("%s   frame cache stale (%d != %d byte, %s): non riusato"
          % (LOG, local, expected, dst))
    return None


def _record_fallback(dl, domain, valid_name, reason):
    """Registra un fallback da range a download completo (WARN + contatore)."""
    dl["fallback_count"] += 1
    detail = "dominio=%s frame=%s: %s" % (domain, valid_name, reason)
    if detail not in dl["fallback_reasons"]:
        dl["fallback_reasons"].append(detail)
    print("%s [WARN] lettura a intervalli fallita -> download completo (%s)"
          % (LOG, detail))


def probe_frame_shape(url, domain, valid_name, cache_dir, frames_cache, dl,
                      allow_range=True):
    """(shape, mode) del PRIMO frame: cache locale -> range -> download completo."""
    path = _cached_path(url, domain, valid_name, frames_cache)
    if path is not None:
        dl["grid_probe_mode"] = "cache"
        return frame_shape(path), "cache"
    if allow_range and range_supported():
        shape, stats, err = probe_frame_shape_range(url)
        dl["range_requests"] += stats.requests
        dl["range_bytes"] += stats.bytes
        if err is None and shape:
            dl["grid_probe_mode"] = "range"
            return shape, "range"
        if err is None:
            _record_fallback(dl, domain, valid_name, "shape non leggibile via range")
        else:
            _record_fallback(dl, domain, valid_name,
                             "%s: %s" % (type(err).__name__, err))
    elif allow_range:
        _record_fallback(dl, domain, valid_name,
                         "lettura a intervalli non disponibile "
                         "(fsspec/reader assente)")
    fpath, _ = fetch_frame(url, domain, valid_name, cache_dir, frames_cache,
                           label="griglia")
    if fpath is None:
        return None, "fail"
    dl["download_bytes"] += os.path.getsize(fpath)
    dl["grid_probe_mode"] = "download"
    return frame_shape(fpath), "download"


def load_frame(url, domain, valid_name, cache_dir, frames_cache, risk_vars,
               grid, tgt_lats, tgt_lons, idx, dl, allow_range=True):
    """Ottiene un frame sorgente con i dati pubblicati.

    Ordine: (1) frame locale in frames-cache (gratis), (2) lettura selettiva a
    intervalli, (3) FALLBACK download completo (con [WARN] e contatore).
    Ritorna un dict {shape, arrays, meta, warnings, crs_frame, mode} o None.
    """
    path = _cached_path(url, domain, valid_name, frames_cache)
    if path is not None:
        dl["cache_frames"] += 1
        d = _read_local_frame(path, domain, risk_vars, grid, tgt_lats, tgt_lons,
                              idx)
        d["mode"] = "cache"
        return d
    if allow_range and range_supported():
        d, stats, err = read_frame_selective(url, domain, risk_vars, grid,
                                             tgt_lats, tgt_lons)
        dl["range_requests"] += stats.requests
        dl["range_bytes"] += stats.bytes
        if err is None:
            dl["range_frames"] += 1
            d["mode"] = "range"
            return d
        _record_fallback(dl, domain, valid_name,
                         "%s: %s" % (type(err).__name__, err))
    elif allow_range:
        _record_fallback(dl, domain, valid_name,
                         "lettura a intervalli non disponibile "
                         "(fsspec/reader assente)")
    fpath, _ = fetch_frame(url, domain, valid_name, cache_dir, frames_cache,
                           label="frame")
    if fpath is None:
        return None
    dl["download_frames"] += 1
    dl["download_bytes"] += os.path.getsize(fpath)
    d = _read_local_frame(fpath, domain, risk_vars, grid, tgt_lats, tgt_lons, idx)
    d["mode"] = "download"
    return d


# ---------------------------------------------------------------------------
# Scrittura frame + manifest
# ---------------------------------------------------------------------------
def write_frame_om(path, ordered, arrays, crs_wkt, coordinates,
                   valid_unix, ref_unix):
    """Scrive il `.om` multi-variabile (atomico). Ritorna (bytes, n_vars)."""
    tmp = path + ".tmp"
    w = OmFileWriter.at_path(tmp)
    children = []
    for name in ordered:
        if name not in arrays:
            continue
        arr = np.ascontiguousarray(np.asarray(arrays[name], dtype=np.float32))
        # I domini a griglia grossa producono crop piccoli (es. jma_gsm 25x22):
        # il chunk non puo' eccedere la dimensione dell'array, quindi lo si
        # clampa al minimo tra DEFAULT_CHUNKS e la shape effettiva.
        chunks = [min(DEFAULT_CHUNKS[0], int(arr.shape[0])),
                  min(DEFAULT_CHUNKS[1], int(arr.shape[1]))]
        # omfiles memorizza int = round(valore * scale_factor): per ottenere il
        # passo voluto (VARIABLE_STEP) si passa scale_factor = 1.0 / passo.
        c = w.write_array(arr, chunks=chunks,
                          scale_factor=float(1.0 / VARIABLE_STEP.get(name, DEFAULT_STEP)),
                          add_offset=0.0, compression=DEFAULT_COMPRESSION, name=name)
        children.append(c)
    children.append(w.write_scalar(str(crs_wkt), name="crs_wkt"))
    children.append(w.write_scalar(str(coordinates), name="coordinates"))
    children.append(w.write_scalar(int(valid_unix), name="valid_time"))
    children.append(w.write_scalar(int(ref_unix), name="forecast_reference_time"))
    root = w.write_group(name="", children=children)
    w.close(root)
    os.replace(tmp, path)
    return os.path.getsize(path), len(children) - 4


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def prune_frames(out_dir, keep_files, max_frames):
    """Rimuove i `.om` estranei alla finestra corrente (`keep_files`).

    `keep_files` e' l'insieme dei frame pubblicati in questo run (= le ancore
    `valid_times`). Qualsiasi altro `.om` rimasto da run precedenti (reference
    time piu' vecchi) viene rimosso: cosi' la cache del dominio contiene
    ESATTAMENTE la finestra corrente e non si cancella mai un frame appena
    scritto. Cap OPZIONALE di sicurezza: se `max_frames > 0` e `keep_files` lo
    supera, tiene i piu' recenti per nome; `max_frames <= 0` = NESSUN cap.
    """
    keep = set(keep_files)
    all_files = sorted([f for f in os.listdir(out_dir) if f.endswith(".om")])
    removed = []
    for f in all_files:
        if f in keep:
            continue
        try:
            os.remove(os.path.join(out_dir, f))
            removed.append(f)
        except OSError as exc:  # noqa: BLE001
            print("%s [WARN] prune fallito %s: %s" % (LOG, f, exc))
    kept = sorted(keep)
    if max_frames > 0 and len(kept) > max_frames:
        for f in kept[: len(kept) - max_frames]:
            if f in removed:
                continue
            try:
                os.remove(os.path.join(out_dir, f))
                removed.append(f)
            except OSError as exc:  # noqa: BLE001
                print("%s [WARN] prune-cap fallito %s: %s" % (LOG, f, exc))
    return removed


# ---------------------------------------------------------------------------
# Pianificazione / orchestrazione
# ---------------------------------------------------------------------------
def load_manifest(domain):
    data, err = http_read_json(s3_manifest_url(domain))
    if data is None:
        print("%s [WARN] manifest non disponibile per %s: %s" % (LOG, domain, err))
    return data


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def process_domain(domain, args, cache_dir, frames_cache, grid_cache, dry_run):
    """Elabora UN dominio in modo indipendente. Ritorna un dict di esito.

    Status: "ok" (prodotto o NO-OP), "skip" (CRS non supportato), "fail".
    In --dry-run non scarica nulla: la griglia usa il fallback GRIDS per nx/ny.
    """
    res = {"domain": domain, "status": "fail", "reason": None}
    allow_partial = bool(getattr(args, "allow_partial_run", ALLOW_PARTIAL_RUN))
    min_frames = int(getattr(args, "partial_min_frames", PARTIAL_MIN_FRAMES))

    m = load_manifest(domain)

    # --- RUN PARZIALE (P1): scoperta del run in corso (SOLO se abilitato) ---
    # Con il flag OFF questo blocco non esegue NESSUNA richiesta: `run_source`
    # resta descrittivo e non finisce nel manifest pubblicato.
    run_source = {
        "name": "latest.json",
        "url": s3_manifest_url(domain),
        "reference_time": (m or {}).get("reference_time"),
        "completed": (m or {}).get("completed"),
        "last_modified_time": (m or {}).get("last_modified_time"),
        "discovered_at": _now_iso(),
        "partial": False,
    }
    if allow_partial:
        latest_ref = (m or {}).get("reference_time")
        try:
            disc = discover_partial_run(
                domain, latest=m,
                probe_hours=int(getattr(args, "partial_probe_hours",
                                        PARTIAL_PROBE_HOURS)))
        except Exception as exc:  # noqa: BLE001 - la scoperta non fa fallire il dominio
            disc = None
            print("%s [WARN] dominio '%s': scoperta run parziale fallita: %s"
                  % (LOG, domain, exc))
        if disc:
            base = dict(m or {})
            base["reference_time"] = disc["reference_time"]
            base["valid_times"] = disc["valid_times"]
            base["completed"] = disc["completed"]
            if disc.get("crs_wkt"):
                base["crs_wkt"] = disc["crs_wkt"]
            m = base
            run_source = {
                "name": disc["source"],
                "url": disc["url"],
                "reference_time": disc["reference_time"],
                "completed": disc["completed"],
                "last_modified_time": disc.get("last_modified_time"),
                "discovered_at": disc["discovered_at"],
                "partial": True,
            }
            print("%s   [partial] run scoperto da %s: ref=%s (%s), %d valid_times, "
                  "completed=%s"
                  % (LOG, disc["source"], disc["reference_time"],
                     "nuovo rispetto a latest.json" if _is_newer_run(
                         disc["reference_time"], latest_ref) else "invariato",
                     len(disc["valid_times"]), disc["completed"]))

    if not m:
        res["reason"] = "manifest assente"
        print("%s [FAIL] dominio '%s': manifest assente." % (LOG, domain))
        return res

    crs_wkt = m.get("crs_wkt", "") or ""
    ref_time = m.get("reference_time")
    # ORIZZONTE INTERO: si pubblicano TUTTI i valid_times del manifest, cosi'
    # come arrivano dalla sorgente (nessuna decimazione). Il vecchio THINNING
    # (entro 48h passo 1h, fino a 120h passo 3h, oltre passo 6h) e' stato
    # RIMOSSO in 1.2.4.9: la cadenza dei frame e' quella nativa del manifest.
    valid_times = sorted(set(m.get("valid_times", [])))

    res["reference_time"] = ref_time
    res["completed"] = m.get("completed")
    res["n_valid_times"] = len(valid_times)
    res["crs_kind"] = crs_kind(crs_wkt)
    if allow_partial:
        res["run_source"] = dict(run_source)

    # --- Riconoscimento CRS ---
    if not is_geographic_crs(crs_wkt):
        res["status"] = "skip"
        res["reason"] = "griglia proiettata non supportata (%s)" % crs_kind(crs_wkt)
        print("%s SKIP dominio '%s': griglia proiettata non supportata "
              "(PROJCRS/Lambert)" % (LOG, domain))
        return res

    if not ref_time or not valid_times:
        res["reason"] = "manifest privo di reference_time/valid_times"
        print("%s [FAIL] dominio '%s': %s." % (LOG, domain, res["reason"]))
        return res

    if args.valid_frames > 0:
        anchors = valid_times[: args.valid_frames]
    else:
        anchors = list(valid_times)
    res["anchors"] = anchors

    out_dir = os.path.join(os.path.abspath(args.out), domain)
    latest_path = os.path.join(out_dir, "latest.json")
    res["out_dir"] = out_dir
    res["latest_path"] = latest_path

    # Contatori di provenienza dei frame (registrati nel manifest: vedi
    # `download` piu' sotto). `mode` = "range" | "mixed" | "download" |
    # "frames-cache"; `fallback_count` conta i frame che, per un guasto della
    # lettura a intervalli, sono stati scaricati per intero.
    dl = {
        "mode": "range",
        "grid_probe_mode": None,
        "range_frames": 0,
        "cache_frames": 0,
        "download_frames": 0,
        "fallback_count": 0,
        "fallback_reasons": [],
        "range_requests": 0,
        "range_bytes": 0,
        "download_bytes": 0,
    }
    res["download"] = dl

    # --- DRY-RUN: pianifica SENZA scaricare (nx/ny da fallback GRIDS) ---
    if dry_run:
        known = GRIDS.get(domain)
        grid = None
        if known:
            grid = derive_grid(crs_wkt, known["nx"], known["ny"])
        res["grid"] = grid
        res["grid_source"] = "fallback(GRIDS)" if grid else "unknown"
        if grid:
            idx = bbox_to_indices(grid, bbox_for(domain))
            res["crop"] = idx["out_grid"]
        # PIANO DELLE VARIABILI (solo DICHIARATE: in dry-run nessun frame viene
        # aperto, quindi la presenza reale nel `.om` non e' verificabile qui).
        # Serve a rendere VISIBILE l'allowlist 10 hPa: i domini non ammessi non
        # devono mostrare nessuna delle 6 voci.
        dry_risk = [rv["name"] for rv in risk_vars_for(domain)]
        dry_read = [rv["name"] for rv in read_vars_for(domain)]
        res["vars_published"] = dry_risk
        res["vars_read"] = dry_read
        res["vars_10hpa"] = [n for n in dry_risk if n in VARS_10HPA_SET]
        res["vars_10hpa_domain_allowed"] = domain_supports_10hpa(domain)
        # Stessa visibilita' per l'allowlist della fase neve: `ncep_gfs025` non
        # deve mostrare ne' `snowfall_water_equivalent` ne' `snow_fraction`.
        res["vars_snow"] = [n for n in dry_risk if n in VARS_SWE_SET]
        res["vars_snow_domain_allowed"] = domain_supports_swe(domain)
        # E per la finestra 6 ore: `precipitation_6h` e `snow_fraction_6h`
        # compaiono SOLO dove esiste `swe` (stessa allowlist).
        res["vars_window6h"] = [n for n in dry_risk if n in VARS_WINDOW6H_SET]
        res["vars_window6h_domain_allowed"] = domain_supports_window6h(domain)
        res["status"] = "ok"
        nx = grid["nx"] if grid else "?"
        ny = grid["ny"] if grid else "?"
        crop = res.get("crop")
        crop_s = ("%dx%d" % (crop["nx"], crop["ny"])) if crop else "?"
        print("%s [PLAN] dominio '%s': ref=%s griglia=%sx%s (%s) dx=%.6g dy=%.6g "
              "crop=%s frame=%d out=%s"
              % (LOG, domain, ref_time, nx, ny, res["grid_source"],
                 grid["dx"] if grid else float("nan"),
                 grid["dy"] if grid else float("nan"), crop_s, len(anchors),
                 os.path.join(out_dir, "<VALID>.om")))
        print("%s [PLAN] dominio '%s': var pubblicate=%d lette=%d | 10hPa "
              "ammesso=%s (%d: %s)"
              % (LOG, domain, len(dry_risk), len(dry_read),
                 "si" if domain_supports_10hpa(domain) else "no",
                 len(res["vars_10hpa"]),
                 ", ".join(res["vars_10hpa"]) or "-"))
        print("%s [PLAN] dominio '%s': neve ammessa=%s (%d: %s)"
              % (LOG, domain, "si" if domain_supports_swe(domain) else "no",
                 len(res["vars_snow"]), ", ".join(res["vars_snow"]) or "-"))
        print("%s [PLAN] dominio '%s': finestra 6h ammessa=%s (%d: %s)"
              % (LOG, domain,
                 "si" if domain_supports_window6h(domain) else "no",
                 len(res["vars_window6h"]),
                 ", ".join(res["vars_window6h"]) or "-"))
        return res

    # --- NO-OP GUARD per-dominio (prima di scaricare i frame) ---
    # RUN PARZIALE: finche' il run non e' `completed: true` il NO-OP e' VIETATO.
    # Motivo MISURATO: in 2 casi su 6 il flip di `latest.json` avviene PRIMA
    # dell'ultimo `.om`; un dominio gia' pubblicato su un prefisso verrebbe
    # saltato mentre la sorgente continua a pubblicare e i frame nuovi non
    # arriverebbero MAI (il guard confronta solo reference_time/valid_times/bbox).
    # Costo dichiarato: nei minuti in cui il run si completa il dominio si
    # rielabora a ogni trigger; i frame gia' scaricati restano in --frames-cache.
    partial_open = bool(allow_partial and run_source.get("partial")
                        and m.get("completed") is not True)
    if not args.force and os.path.isfile(latest_path):
        try:
            with open(latest_path, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = None
        bb_cur = bbox_for(domain)
        old_bb = old.get("bbox") if old else None
        bb_same = (old_bb and abs(old_bb.get("lat_min",1e18)-bb_cur[0])<1e-6 and abs(old_bb.get("lon_min",1e18)-bb_cur[1])<1e-6 and abs(old_bb.get("lat_max",-1e18)-bb_cur[2])<1e-6 and abs(old_bb.get("lon_max",-1e18)-bb_cur[3])<1e-6)
        if (old and old.get("reference_time") == ref_time
                and list(old.get("valid_times", [])) == list(anchors)
                and bb_same
                and not partial_open):
            if args.progress_manifest:
                progress = dict(old)
                progress["frames"] = []
                progress["noop"] = True
                progress["complete"] = True
                write_json_atomic(args.progress_manifest, progress)
            res["status"] = "ok"
            res["noop"] = True
            res["reason"] = "manifest invariato"
            print("%s NO-OP dominio '%s': manifest invariato "
                  "(reference_time/valid_times/bbox identici). Salto." % (LOG, domain))
            return res

    # --- Griglia derivata dal PRIMO frame (cache per-run) ---
    grid = grid_cache.get(domain)
    if grid is None:
        first = anchors[0]
        shape, src_mode = probe_frame_shape(
            s3_url(domain, ref_time, first), domain,
            valid_time_to_filename(first), cache_dir, frames_cache, dl,
            allow_range=not args.no_range)
        if shape is None:
            if allow_partial:
                res["reason"] = ("primo frame non disponibile (%s, mode=%s): run "
                                 "parziale non pubblicabile" % (first, src_mode))
            else:
                res["reason"] = ("shape del primo frame non leggibile (mode=%s)"
                                 % src_mode)
            print("%s [FAIL] dominio '%s': %s." % (LOG, domain, res["reason"]))
            return res
        ny, nx = shape
        grid = derive_grid(crs_wkt, nx, ny)
        if grid is None:
            res["reason"] = "crs_wkt privo di BBOX o dimensioni non valide"
            print("%s [FAIL] dominio '%s': %s." % (LOG, domain, res["reason"]))
            return res
        print("%s   griglia dal PRIMO frame (%s)" % (LOG, src_mode))
        for w in grid_guard(domain, grid):
            print("%s [WARN] dominio '%s': %s" % (LOG, domain, w))
        grid_cache[domain] = grid

    res["grid"] = grid
    res["grid_source"] = "crs_wkt+frame"
    idx = bbox_to_indices(grid, bbox_for(domain))
    tgt_lats, tgt_lons = target_latlon(grid, idx)
    out_grid = idx["out_grid"]
    res["crop"] = out_grid

    print("%s dominio '%s' ref=%s griglia nx=%d ny=%d dx=%.6g dy=%.6g -> "
          "crop %dx%d" % (LOG, domain, ref_time, grid["nx"], grid["ny"],
                          grid["dx"], grid["dy"], out_grid["nx"], out_grid["ny"]))

    os.makedirs(out_dir, exist_ok=True)

    # Variabili AMMESSE per questo dominio: `risk_vars` (pubblicate) e
    # `read_vars` (pubblicate + input) sono filtrate dall'allowlist 10 hPa.
    # `ordered_names` determina l'ORDINE di scrittura nel `.om` e il `var_order`
    # del manifest: per i domini non ammessi coincide esattamente con la lista
    # storica.
    risk_vars = risk_vars_for(domain)
    read_vars = read_vars_for(domain)
    ordered_names = [rv["name"] for rv in risk_vars]
    print("%s dominio '%s': variabili pubblicate=%d lette=%d (10hPa=%s)"
          % (LOG, domain, len(risk_vars), len(read_vars),
             "si" if domain_supports_10hpa(domain) else "no"))

    # Cumulata di precipitazione (STATE) sulla griglia di OUTPUT (dopo
    # crop/resample): accumulata lungo la serie ORDINATA dei valid_times.
    # precipitation_cum e' pubblicata su TUTTI i frame (frame 0 = 0.0).
    out_shape = (out_grid["ny"], out_grid["nx"])
    precip_cum = None
    precip_seen = False
    # Fase neve: `snowfall_water_equivalent` e' un accumulato e NON esiste a
    # T+0 (come `precipitation`). Se mancasse su TUTTI i frame lo si dichiara
    # con un warning di sintesi (i warning per-frame sono gia' emessi da
    # `missing_published_warnings`).
    swe_seen = False

    # FINESTRA 6 ORE (`precipitation_6h`, `snow_fraction_6h`): buffer
    # SCORREVOLE degli ultimi frame processati con le DUE sorgenti gia' lette.
    # MEMORIA: fino a WINDOW6H_MAX_FRAMES x 2 array (dwd_icon 0,125°: ~78 MB;
    # ICON-EU: ~19 MB). Nessuna lettura aggiuntiva: `precipitation` e
    # `snowfall_water_equivalent` sono gia' in `arrays` per le altre variabili.
    window6h = []
    window6h_null = {name: 0 for name in VARS_WINDOW6H}
    window6h_reasons = {name: {} for name in VARS_WINDOW6H}
    run_ref_unix = int(parse_iso(ref_time).timestamp())
    anchor_dts = [parse_iso(a) for a in anchors]

    frames = []
    variables_map = {}
    sizes_map = {}
    all_warnings = []

    # RUN PARZIALE: `n_expected` = valid_times DICHIARATI dalla sorgente;
    # `missing` = quelli che non si sono potuti leggere (coda del prefisso).
    n_expected = len(anchors)
    missing = []
    missing_reason = None

    def publish_progress(complete=False):
        if not args.progress_manifest:
            return
        bb_progress = bbox_for(domain)
        obj = {
            "version": "fm2-progress-1",
            "domain": domain,
            "reference_time": ref_time,
            "valid_times": anchors,
            "bbox": {"lat_min": bb_progress[0], "lon_min": bb_progress[1],
                     "lat_max": bb_progress[2], "lon_max": bb_progress[3]},
            "grid": out_grid,
            "var_order": ordered_names,
            "frames": list(frames),
            "complete": bool(complete),
        }
        if allow_partial:
            obj["run_source"] = dict(run_source)
            obj["partial"] = {
                "n_expected": n_expected,
                "n_published": len(frames),
                "missing": list(missing),
                "missing_reason": missing_reason,
                "min_frames": min(min_frames, n_expected),
                "completed": m.get("completed"),
            }
        write_json_atomic(args.progress_manifest, obj)

    # Il consumer di render puo' partire appena il motore ha determinato la
    # griglia; un frame viene annunciato solo dopo la scrittura atomica del .om.
    publish_progress(complete=False)

    for vi, anchor in enumerate(anchors):
        print("%s   frame %d/%d valid_time=%s" % (LOG, vi + 1, len(anchors), anchor))
        vname = valid_time_to_filename(anchor)
        # `read_vars` = pubblicate + INPUT ammesse per QUESTO dominio (allowlist
        # 10 hPa applicata): la lettura a intervalli deve leggere ANCHE le
        # componenti u/v a 850/300 hPa (altrimenti le derivate
        # wind_speed_850hPa/300hPa falliscono solo su quel percorso) ma NON le
        # variabili 10 hPa per i domini che non le ammettono.
        frame = load_frame(s3_url(domain, ref_time, anchor), domain, vname,
                           cache_dir, frames_cache, read_vars, grid,
                           tgt_lats, tgt_lons, idx, dl,
                           allow_range=not args.no_range)
        if frame is None and allow_partial:
            # Distinzione DICHIARATA: 200 = l'oggetto esiste ma la lettura e'
            # fallita (un retry e' legittimo); 404/None = non ancora pubblicato
            # (e' la fine del prefisso). Senza questa HEAD un guasto di rete
            # verrebbe registrato come "frame mancante" e troncherebbe la mappa.
            st = http_status(s3_url(domain, ref_time, anchor))
            if st == 200:
                frame = load_frame(s3_url(domain, ref_time, anchor), domain,
                                   vname, cache_dir, frames_cache, read_vars,
                                   grid, tgt_lats, tgt_lons, idx, dl,
                                   allow_range=not args.no_range)
                if frame is None:
                    missing_reason = "head_200_read_failed"
            elif st is None:
                missing_reason = "head_unavailable"
            else:
                missing_reason = "head_%d" % st
            if frame is not None:
                missing_reason = None
        if frame is None:
            if allow_partial:
                # PREFISSO, non buchi: al primo frame non leggibile ci si FERMA.
                # Saltarlo produrrebbe un manifest con `valid_times` piu' lungo
                # di `frames`, e il client risolve il file per indice posizionale
                # (`valid = times[idx]` vs `frames[idx].file`): mostrerebbe l'ora
                # sbagliata in silenzio.
                missing = list(anchors[vi:])
                print("%s [PARTIAL] frame %s non disponibile (%s): mi fermo qui. "
                      "%d/%d frame presi, %d dichiarati mancanti."
                      % (LOG, anchor, missing_reason, len(frames), n_expected,
                         len(missing)))
                all_warnings.append(
                    "run parziale: fermato al primo frame non disponibile (%s); "
                    "mancanti %d" % (anchor, len(missing)))
                break
            print("%s [WARN] salto frame %s (frame non ottenibile)." % (LOG, anchor))
            all_warnings.append("frame %s: frame non ottenibile" % anchor)
            continue

        crs_frame = frame["crs_frame"] or crs_wkt
        coordinates = "lat lon"
        arrays = frame["arrays"]
        vmeta = frame["meta"]
        warns = frame["warnings"]

        # Cumulata stateful: il frame senza `precipitation` (reference_time)
        # contribuisce 0 e NON interrompe l'accumulo.
        precip = arrays.get("precipitation")
        if precip is not None:
            precip_seen = True
        if "snowfall_water_equivalent" in arrays:
            swe_seen = True
        precip_cum = accumulate_precip_cum(precip_cum, precip, out_shape)
        arrays["precipitation_cum"] = precip_cum.astype(np.float32)
        vmeta["precipitation_cum"] = {
            "source_domain": domain, "derived": True,
            "derived_from": ["precipitation"]}

        # --- FINESTRA 6 ORE: `precipitation_6h` / `snow_fraction_6h` ---------
        # Il frame CORRENTE entra nella finestra con le due sorgenti GIA' lette.
        # Le due variabili si pubblicano SEMPRE (array di NaN quando la finestra
        # non e' calcolabile) cosi' l'insieme delle variabili del `.om` resta
        # omogeneo fra i frame; il motivo del null sta in `variables[<vt>]`.
        if domain_supports_window6h(domain):
            window6h.append({
                "index": vi,
                "valid_time": anchor,
                "vt": anchor_dts[vi],
                "step_s": ((anchor_dts[vi] - anchor_dts[vi - 1]).total_seconds()
                           if vi > 0 else None),
                "ref_unix": frame.get("ref_unix"),
                "precipitation": arrays.get("precipitation"),
                "snowfall_water_equivalent":
                    arrays.get("snowfall_water_equivalent"),
            })
            if len(window6h) > WINDOW6H_MAX_FRAMES:
                del window6h[0]
            entries6h, reason6h = window6h_entries(anchor_dts, vi, window6h)
            w6 = compute_window6h(entries6h, run_ref_unix, reason6h)
            for name6 in VARS_WINDOW6H:
                item6 = w6[name6]
                arr6 = item6["array"]
                if arr6 is None:
                    arr6 = np.full(out_shape, np.nan, dtype=np.float32)
                arrays[name6] = arr6
                meta6 = {"source_domain": domain, "derived": True,
                         "derived_from": list(DERIVED_VARS[name6]["sources"])}
                if w6["window"]:
                    meta6["window"] = dict(w6["window"])
                n_null = int(np.isnan(arr6).sum())
                if n_null:
                    meta6["null_cells"] = n_null
                if n_null == int(arr6.size):
                    meta6["null_reason"] = (item6["null_reason"]
                                            or "all_cells_null")
                    window6h_null[name6] += 1
                    r = meta6["null_reason"]
                    window6h_reasons[name6][r] = (
                        window6h_reasons[name6].get(r, 0) + 1)
                vmeta[name6] = meta6

        vt_dt = parse_iso(anchor)
        ref_dt = parse_iso(ref_time)
        fname = vname + ".om"
        fpath_out = os.path.join(out_dir, fname)
        size, nvars = write_frame_om(
            fpath_out, ordered_names, arrays, crs_frame, coordinates,
            int(vt_dt.timestamp()), int(ref_dt.timestamp()))
        frames.append({"valid_time": anchor, "file": fname, "bytes": size,
                       "variables": nvars, "source_mode": frame["mode"]})
        variables_map[anchor] = vmeta
        sizes_map[anchor] = size
        all_warnings.extend(["%s: %s" % (anchor, w) for w in warns])
        publish_progress(complete=False)
        print("%s   scritto %s (%d byte, %d var, src=%s)"
              % (LOG, fname, size, nvars, frame["mode"]))

    if not frames:
        publish_progress(complete=True)
        if allow_partial:
            res["reason"] = ("primo frame non disponibile (%s): run parziale "
                             "non pubblicabile" % (anchors[0] if anchors else "?"))
        else:
            res["reason"] = "nessun frame scritto (tutti i download falliti?)"
        print("%s [FAIL] dominio '%s': %s." % (LOG, domain, res["reason"]))
        return res

    if allow_partial and len(frames) < min(min_frames, n_expected):
        # Soglia minima: un run con 1-2 frame non e' una mappa. Meglio nessun
        # aggiornamento che un manifest che accorcia l'orizzonte a 1 frame.
        # La soglia e' limitata a `n_expected` (che tiene conto di
        # `--valid-frames N`): un orizzonte VOLUTO di N frame non deve fallire
        # solo perche' N < --partial-min-frames.
        # I .om scritti in questo tentativo vengono RIMOSSI: out_dir resta
        # coerente con il `latest.json` precedente (nessun file orfano in commit).
        removed_partial = []
        for f in frames:
            try:
                os.remove(os.path.join(out_dir, f["file"]))
                removed_partial.append(f["file"])
            except OSError:
                pass
        publish_progress(complete=True)
        res["reason"] = ("frame pubblicati insufficienti: %d < %d (minimo "
                         "configurabile)" % (len(frames), min(min_frames,
                                                              n_expected)))
        res["partial"] = {"enabled": True, "n_expected": n_expected,
                          "n_published": len(frames), "missing": missing,
                          "min_frames": min(min_frames, n_expected),
                          "completed": m.get("completed")}
        print("%s [FAIL] dominio '%s': %s; rimossi %d .om scritti."
              % (LOG, domain, res["reason"], len(removed_partial)))
        return res

    if allow_partial and missing:
        # PREFISSO: `valid_times` pubblicato = SOLO i frame scritti, cosi'
        # `len(valid_times) == len(frames)` e l'indice posizionale del client
        # resta allineato. I mancanti sono dichiarati in `partial.missing`.
        anchors = anchors[: len(frames)]
        res["anchors"] = anchors

    if not precip_seen:
        all_warnings.append(
            "precipitation_cum: variabile 'precipitation' assente in TUTTI i "
            "frame del dominio '%s': la cumulata resta a 0" % domain)
    if domain_supports_swe(domain) and not swe_seen:
        all_warnings.append(
            "snowfall_water_equivalent: assente in TUTTI i frame del dominio "
            "'%s': nessuna informazione di fase neve/pioggia in questo run "
            "(a T+0 e' ATTESO: i campi accumulati non esistono)" % domain)
    if domain_supports_window6h(domain) and frames:
        # Sintesi dei null della finestra 6 h: i primi frame di una run NON
        # hanno 6 h di storia (a T+0 manca anche `precipitation`), quindi un
        # certo numero di null e' ATTESO e non e' un guasto.
        for name6 in VARS_WINDOW6H:
            if not window6h_null[name6]:
                continue
            det = ", ".join("%s=%d" % kv
                            for kv in sorted(window6h_reasons[name6].items()))
            all_warnings.append(
                "'%s': null (tutte le celle) su %d frame su %d (motivi: %s). "
                "I primi frame della run non hanno 6 h di storia: nessun "
                "riempimento con zeri, nessun frame da run precedenti"
                % (name6, window6h_null[name6], len(frames), det))

    removed = []
    if not args.no_prune:
        keep_files = [valid_time_to_filename(a) + ".om" for a in anchors]
        removed = prune_frames(out_dir, keep_files, args.valid_frames)

    # Modalita' di download del dominio (registrata nel manifest/report).
    used_range = dl["range_frames"] > 0 or dl["grid_probe_mode"] == "range"
    used_dl = dl["download_frames"] > 0 or dl["grid_probe_mode"] == "download"
    if used_range and used_dl:
        dl["mode"] = "mixed"
    elif used_dl:
        dl["mode"] = "download"
    elif used_range:
        dl["mode"] = "range"
    elif dl["cache_frames"]:
        dl["mode"] = "frames-cache"
    else:
        dl["mode"] = "range"

    bb = bbox_for(domain)
    manifest = {
        "version": "fm2-1",
        "generated_at": _now_iso(),
        "domain": domain,
        "reference_time": ref_time,
        "valid_times": anchors,
        "bbox": {"lat_min": bb[0], "lon_min": bb[1],
                 "lat_max": bb[2], "lon_max": bb[3]},
        "grid": out_grid,
        "source_grid": {k: grid[k] for k in ("nx", "ny", "lonMin", "latMin", "dx", "dy")},
        "grid_source": "crs_wkt+frame",
        "var_order": ordered_names,
        "resampling": "per-domain nearest (per-domain crop)",
        "license": "Open-Meteo (CC BY 4.0) — attribuzione richiesta",
        "frames": frames,
        "variables": variables_map,
        "sizes": sizes_map,
        "pruned": removed,
        "warnings": all_warnings,
        "download": dl,
    }
    if allow_partial:
        # Provenienza del run pubblicato: fonte, URL, timestamp, completamento.
        # Con il flag OFF queste chiavi NON esistono (manifest byte-identico
        # nella struttura a quello storico, a meno di generated_at/frames).
        manifest["run_source"] = {
            "name": run_source["name"],
            "url": run_source["url"],
            "reference_time": ref_time,
            "completed": m.get("completed"),
            "last_modified_time": run_source.get("last_modified_time"),
            "discovered_at": run_source.get("discovered_at") or _now_iso(),
            "partial": bool(run_source.get("partial")),
        }
        manifest["partial"] = {
            "enabled": True,
            "n_expected": n_expected,
            "n_published": len(frames),
            "missing": missing,
            "missing_reason": missing_reason,
            "min_frames": min(min_frames, n_expected),
            "stopped_at_first_gap": bool(missing),
            "completed": m.get("completed"),
        }
    write_json_atomic(latest_path, manifest)
    publish_progress(complete=True)

    total_bytes = sum(f["bytes"] for f in frames)
    print("%s [OK] dominio '%s': %d frame, %d byte totali (%.1f KiB)."
          % (LOG, domain, len(frames), total_bytes, total_bytes / 1024.0))
    if allow_partial:
        res["partial"] = {
            "enabled": True,
            "n_expected": n_expected,
            "n_published": len(frames),
            "missing": missing,
            "missing_reason": missing_reason,
            "min_frames": min(min_frames, n_expected),
            "completed": m.get("completed"),
        }
        if missing:
            print("%s   [partial] run %s: %d/%d frame pubblicati; mancanti %d "
                  "(%s .. %s), motivo=%s"
                  % (LOG, ref_time, len(frames), n_expected, len(missing),
                     missing[0], missing[-1], missing_reason))
        else:
            print("%s   [partial] run %s: %d/%d frame pubblicati (nessun buco), "
                  "completed=%s" % (LOG, ref_time, len(frames), n_expected,
                                    m.get("completed")))
    print("%s   download: mode=%s (griglia=%s) | range=%d frame (%d req, %d byte) | "
          "download=%d frame (%d byte) | cache=%d | fallback=%d"
          % (LOG, dl["mode"], dl["grid_probe_mode"], dl["range_frames"],
             dl["range_requests"], dl["range_bytes"], dl["download_frames"],
             dl["download_bytes"], dl["cache_frames"], dl["fallback_count"]))
    if removed:
        print("%s   prune: rimossi %d frame vecchi." % (LOG, len(removed)))
    uniq_warn = sorted(set(all_warnings))
    if uniq_warn:
        print("%s   warning (%d):" % (LOG, len(uniq_warn)))
        for w in uniq_warn:
            print("%s     - %s" % (LOG, w))

    res["status"] = "ok"
    res["n_frames"] = len(frames)
    res["bytes"] = total_bytes
    return res


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_parser():
    """Parser CLI del motore (estratto da `main` per essere testabile)."""
    ap = argparse.ArgumentParser(
        description="MeteoRisk Forecast V2 — cache GitHub multi-dominio di mappe .om")
    ap.add_argument("--domains", nargs="+", default=list(V2_DOMAINS),
                    help="Domini da elaborare, ciascuno INDIPENDENTE (nessun ordine "
                         "di priorita'; ognuno produce data/fm2/<dominio>/).")
    ap.add_argument("--valid-frames", type=int, default=V2_MAX_FRAMES,
                    help="Numero di frame (valid_times) da pubblicare a partire dal "
                         "primo, PER OGNI dominio. 0 (default) o negativo = INTERO "
                         "orizzonte del manifest del dominio (tutti i valid_times).")
    ap.add_argument("--out", default=V2_OUT,
                    help="Directory di output radice (default %s); la cache di ogni "
                         "dominio va in <out>/<dominio>/." % V2_OUT)
    ap.add_argument("--once", action="store_true",
                    help="Esegue un solo ciclo ed esce (default/compat).")
    ap.add_argument("--no-prune", action="store_true", help="Non rimuove i frame oltre il limite.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Stampa il piano di TUTTI i domini senza scaricare/scrivere.")
    ap.add_argument("--force", action="store_true", help="Ignora il NO-OP guard (riscrive sempre).")
    ap.add_argument("--no-range", action="store_true",
                    help="Disabilita la lettura a intervalli (range GET): scarica "
                         "sempre il frame COMPLETO. Usato per debug/confronto; "
                         "default = lettura selettiva con fallback esplicito.")
    ap.add_argument("--cache-dir", default=None,
                    help="Directory cache dei download (default: temp di sistema).")
    ap.add_argument("--frames-cache", default="",
                    help="Cache PERSISTENTE dei frame .om (<dir>/<dominio>/<VALID>.om), "
                         "riusata se la Content-Length remota combacia (default: vuoto = "
                         "disabilitata, comportamento invariato).")
    ap.add_argument("--progress-manifest", default="",
                    help="Scrive un manifest atomico dei frame pronti per il render streaming.")
    ap.add_argument("--allow-partial-run", action="store_true",
                    default=ALLOW_PARTIAL_RUN,
                    help="RUN PARZIALE (default OFF): scopre il run in corso "
                         "(`<dominio>/in-progress.json`, poi listing + "
                         "`<run>/meta.json`) e pubblica il PREFISSO di frame gia' "
                         "disponibili, fermandosi al primo buco. Con il flag OFF il "
                         "comportamento e' quello storico e non si fa nessuna "
                         "richiesta aggiuntiva.")
    ap.add_argument("--partial-min-frames", type=int, default=PARTIAL_MIN_FRAMES,
                    help="Con --allow-partial-run: minimo numero di frame perche' "
                         "il run parziale sia pubblicabile (default %d). Sotto la "
                         "soglia il dominio fallisce e i .om scritti sono rimossi."
                         % PARTIAL_MIN_FRAMES)
    ap.add_argument("--partial-probe-hours", type=int, default=PARTIAL_PROBE_HOURS,
                    help="Con --allow-partial-run: finestra in ore per la ricerca "
                         "delle directory di run via listing (minimo 2 giorni).")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    if not args.domains:
        print("%s ERRORE: --domains vuoto" % LOG)
        return 1

    print("%s multi-dominio: %d domini -> %s"
          % (LOG, len(args.domains), ", ".join(args.domains)))
    print("%s valid-frames=%d (%s) out=%s"
          % (LOG, args.valid_frames,
             "INTERO orizzonte" if args.valid_frames <= 0 else "primi N",
             os.path.abspath(args.out)))
    if args.allow_partial_run:
        print("%s run parziale ABILITATO: min-frames=%d, probe-hours=%d "
              "(fonte: in-progress.json -> listing+meta.json -> latest.json)"
              % (LOG, args.partial_min_frames, args.partial_probe_hours))

    # ---- cache download / frames-cache (non in dry-run: nessuna directory) ----
    cache_dir = args.cache_dir or os.path.join(tempfile.gettempdir(), "mri_fm2_cache")
    frames_cache = os.path.abspath(args.frames_cache) if args.frames_cache else ""
    if not args.dry_run:
        os.makedirs(cache_dir, exist_ok=True)
        if frames_cache:
            os.makedirs(frames_cache, exist_ok=True)
            print("%s frames-cache attiva: %s" % (LOG, frames_cache))
        os.makedirs(os.path.abspath(args.out), exist_ok=True)

    grid_cache = {}
    results = []
    for d in args.domains:
        results.append(process_domain(d, args, cache_dir, frames_cache, grid_cache,
                                      args.dry_run))

    ok = [r for r in results if r["status"] == "ok"]
    skip = [r for r in results if r["status"] == "skip"]
    fail = [r for r in results if r["status"] == "fail"]

    if args.dry_run:
        plan = {
            "dry_run": True,
            "out": os.path.abspath(args.out),
            "valid_frames": args.valid_frames,
            "all_horizon": args.valid_frames <= 0,
            "max_frames_cap": V2_MAX_FRAMES,
            "prune": not args.no_prune,
            "var_10hpa_domains": sorted(VAR_10HPA_DOMAINS),
            "var_swe_domains": sorted(VAR_SWE_DOMAINS),
            "domains": [
                {
                    "domain": r["domain"],
                    "status": r["status"],
                    "reason": r["reason"],
                    "reference_time": r.get("reference_time"),
                    "completed": r.get("completed"),
                    "n_valid_times": r.get("n_valid_times"),
                    "n_frames": len(r.get("anchors", [])) if r["status"] == "ok" else 0,
                    "crs_kind": r.get("crs_kind"),
                    "grid_source": r.get("grid_source"),
                    "source_grid": r.get("grid"),
                    "crop": r.get("crop"),
                    # Variabili DICHIARATE per il dominio (allowlist 10 hPa
                    # applicata). In dry-run nessun frame e' aperto: qui non si
                    # afferma che la variabile esista nel `.om`.
                    "vars_published": r.get("vars_published"),
                    "n_vars_published": len(r.get("vars_published") or []),
                    "vars_read": r.get("vars_read"),
                    "vars_10hpa": r.get("vars_10hpa"),
                    "vars_10hpa_domain_allowed": r.get("vars_10hpa_domain_allowed"),
                    "vars_snow": r.get("vars_snow"),
                    "vars_snow_domain_allowed": r.get("vars_snow_domain_allowed"),
                    "out_dir": r.get("out_dir"),
                    "frames_glob": (os.path.join(r["out_dir"], "<VALID>.om")
                                    if r.get("out_dir") else None),
                    "latest_json": r.get("latest_path"),
                }
                for r in results
            ],
        }
        print("%s DRY-RUN plan:\n%s"
              % (LOG, json.dumps(plan, indent=2, ensure_ascii=False)))

    print("%s ===== SUMMARY =====" % LOG)
    print("%s ok   (%d): %s" % (LOG, len(ok), ", ".join(r["domain"] for r in ok) or "-"))
    print("%s skip (%d): %s" % (LOG, len(skip), ", ".join(
        "%s [%s]" % (r["domain"], r["reason"]) for r in skip) or "-"))
    print("%s fail (%d): %s" % (LOG, len(fail), ", ".join(
        "%s [%s]" % (r["domain"], r["reason"]) for r in fail) or "-"))
    if args.allow_partial_run:
        parts = [r for r in results if r.get("partial")]
        if parts:
            print("%s partial (%d): %s" % (LOG, len(parts), ", ".join(
                "%s %d/%d%s" % (r["domain"], r["partial"]["n_published"],
                                r["partial"]["n_expected"],
                                "" if r["partial"].get("completed") is not False
                                else " (run non completo)")
                for r in parts)))

    # Exit: 0 se almeno un dominio e' ok (anche solo NO-OP); 1 se TUTTI falliscono.
    if not ok:
        print("%s ERRORE: nessun dominio ok (tutti falliti)." % LOG)
        close_http()
        return 1
    print("%s FATTO: %d/%d domini ok." % (LOG, len(ok), len(results)))
    close_http()
    return 0


if __name__ == "__main__":
    sys.exit(main())
