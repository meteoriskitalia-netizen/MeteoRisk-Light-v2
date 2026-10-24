#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — severe_stack.py (S1 + S1b + S2 + S2b + S3, nessun numero finto)

Cinque componenti indipendenti, ciascuna con la PROPRIA fonte e la PROPRIA
citazione; `combine()` le mette in un unico verdetto SENZA inventare una
probabilita' calibrata (non esistono etichette: docs/METODI_VALIDATI_SEVERO.md,
sez. 4 e 5).

    S1  AMBIENTE      CAPE/CIN/shear 0-6 km/SRH 0-3 km dai modelli Open-Meteo
                      gia' scaricati dalla Fase 2 (environment.py). Riferimento
                      europeo: Pucik et al. 2015, MWR 143:4805-4821. La citazione
                      NON e' una soglia locale: le soglie europee descrivono
                      climatologie, non questa cella.
    S1b BUNKERS       moto RM/LM atteso dal profilo 0-6 km (Bunkers et al. 2000)
                      e scostamento dal moto OSSERVATO dal tracking. E' un
                      DESCRITTORE: non prova la rotazione. Se il profilo non
                      arriva a 6 km il campo e' null con motivo.
    S2  GRANDINE      VIL density (Amburn & Wolf 1997) e MESH (Witt et al. 1998).
                      Dal 1.2.4.18 MESH si calcola davvero: i 10 CAPPI DPC
                      (1..10 km, passo 1 km) danno il profilo dBZ(H) che Witt
                      richiede, e le equazioni (1)-(3) p.288 + (6) p.293 sono
                      TRASCRITTE dal paper nel campo `reference`. Gate: >= 4
                      livelli IN COPERTURA per pixel, altrimenti null con motivo
                      (`cappi_levels<4`, `pixel_coverage_below_4_levels`); se la
                      tabella non e' verificata -> `witt_table_unavailable`.
                      NON si produce un "MESH-like" da VIL: si chiamerebbe MESH
                      senza esserlo.
    S2b OVERHANG      nucleo elevato da CAPPI_2 vs CAPPI_6 per CELLA (dBZ6-dBZ2,
                      dBZ6>=45 e dBZ2<45). Soglie = SCELTA INGEGNERISTICA.
    S3  OVERSHOOTING  riusa phase2/satellite_ot.py (Bedka et al. 2010) e riporta
        TOP           booleano + confidence, con fonte e timestamp del frame.

REGOLA DI ONESTA' (applicata da `_field`): un valore None SENZA motivazione e'
un errore di programmazione -> ValueError. Ogni campo pubblicato porta
`source` (da dove viene il numero) e `reference` (la citazione), e quando e'
null anche `null_reason`. `rotation_measurable` resta False: nessuna di queste
grandezze misura la rotazione.

Nessuna funzione qui fa I/O: sono pure e testabili. Le soglie che NON hanno una
citazione sono marcate come SCELTA INGEGNERISTICA nel campo `reference`.
"""

import math

SEVERE_STACK_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# Citazioni (unica fonte: cosi' un refuso non si propaga)
# ---------------------------------------------------------------------------
REF_PUCIK = ("Pucik, Groenemeijer, Ryva & Kolar (2015), Mon. Wea. Rev. "
             "143:4805-4821 — climatologia europea (riferimento, NON soglia "
             "locale)")
REF_TASZAREK = ("Taszarek, Brooks & Czernecki (2017), Mon. Wea. Rev. "
                "145:1511-1528 — WMAXSHEAR come miglior separatore in Europa "
                "(riferimento, NON soglia locale)")
REF_DAVIES_JONES = ("Davies-Jones (1990), SRH da integrale discreto "
                    "sull'hodografo; implementazione dichiarata in questo "
                    "modulo")
REF_LAWRENCE = ("Lawrence (2005): LCL ~ 125*(T-Td) — approssimazione, non "
                "validata localmente")
REF_AMBURN_WOLF = ("Amburn & Wolf (1997), Wea. Forecasting 12(3):473-478 — "
                   "VIL density 3.5 g/m3 identificava il 90% dei casi di "
                   "grandine severa (2 falsi su 36 casi non severi) [abstract]; "
                   "riferimento bibliografico, NON validato localmente")
REF_WITT = ("Witt, Eilts, Stumpf, Johnson, Mitchell & Thomas (1998), Wea. "
            "Forecasting 13(2):286-303 — eq. (1) p.288: E=5e-6*10^(0.084*Z)*W(Z) "
            "con W(Z)=0 per Z<=Z_L, lineare (Z-Z_L)/(Z_U-Z_L) fra Z_L=40 dBZ e "
            "Z_U=50 dBZ (default del paper), 1 sopra Z_U; eq. (2) p.288: W_T(H)=0 "
            "per H<=H0, lineare fino a H(-20 C), 1 sopra; eq. (3) p.288: "
            "SHI=0.1*int(H0..HT) W_T*E dH; eq. (6) p.293: MEHS=2.54*sqrt(SHI) mm "
            "(75% delle 147 segnalazioni sotto la stima). Testo del paper "
            "verificato il 2026-10-23; NESSUNA validazione italiana")
REF_BEDKA = ("Bedka, Brunner, Dworak, Feltz, Otkin & Greenwald (2010), J. Appl. "
             "Meteor. Climatol. 49:181-202 — OT da gradiente IR 10.8 um")
REF_TECHNICAL_CHOICE = "SCELTA INGEGNERISTICA (nessuna citazione: non e' una soglia validata)"

# Amburn & Wolf 1997: soglia CITATA, riportata come stringa nell'output.
VIL_DENSITY_THRESHOLD_G_M3 = 3.5
VIL_DENSITY_THRESHOLD_REF = "Amburn&Wolf 1997 3.5 g/m3"

# Witt et al. 1998 eq. (1)-(3), p. 288 — costanti TRASCRITTE dal paper
# (testo verificato il 2026-10-23, non stimate e non prese da terzi).
WITT_Z_L_DBZ = 40.0          # Z_L default del paper (adattabile, qui fisso)
WITT_Z_U_DBZ = 50.0          # Z_U default del paper
WITT_E_DOT_COEFF = 5.0e-6    # E = 5e-6 * 10^(0.084*Z) * W(Z)
WITT_E_DOT_EXP = 0.084
WITT_SHI_SCALE = 0.1         # SHI = 0.1 * integrale
WITT_MESH_MM_COEFF = 2.54    # MEHS = 2.54 * sqrt(SHI)   [eq. (6), p. 293]
WITT_PAPER_ROUND_MM = 6.35   # il paper arrotonda l'output a 0.25 in (6.35 mm)

# Gate sui livelli: Witt richiede >= 2 componenti 2D (p. 288). Con CAPPI a
# 1 km di passo, 4 livelli sono il minimo per un integrale verticale con una
# risoluzione utile: SCELTA INGEGNERISTICA (piu' restrittiva del paper).
MESH_MIN_CAPPI_LEVELS = 4
MESH_DH_M = 1000.0           # passo verticale REALE dei CAPPI DPC (1 km)

# Sentinelle del prodotto DPC (docs/CAPPI: -9999 fuori copertura, -9998 in
# copertura senza eco). Il motore le ha in preprocess.nodata_values; qui
# servono distinte perche' "-9998" e' un'assenza di eco, non un dato mancante.
DPC_NODATA_OUT_OF_COVERAGE = -9999.0
DPC_NODATA_NO_ECHO = -9998.0

# SRH: numero minimo di livelli di vento per una curva dell'hodografo utile.
SRH_MIN_WIND_LEVELS = 4
SRH_MIN_TOP_M = 2000.0      # serve almeno un livello sopra i 2 km

S1_SOURCE_ENV = "open-meteo forecast API (Fase 2 environment.py)"
S2_SOURCE_DPC = "Radar-DPC VIL/ETM (+ CAPPI se >=4 livelli configurati)"
S3_SOURCE_IR = "Radar-DPC IR_108 (MSG SEVIRI 10.8 um, griglia TM 1 km)"


# ---------------------------------------------------------------------------
# Contratto dei campi
# ---------------------------------------------------------------------------
def field(value, source, reference, null_reason=None, **extra):
    """Campo pubblicato: value + source + reference (+ null_reason se null).

    Un None senza motivazione e' un errore: `null_reason` obbligatorio quando
    value is None o non finito (fail-closed, nessun buco silenzioso)."""
    if isinstance(value, float) and not math.isfinite(value):
        value = None
        null_reason = null_reason or "not_finite"
    if value is None and not null_reason:
        raise ValueError("field_null_without_reason")
    out = {"value": value, "null_reason": null_reason,
           "source": source, "reference": reference}
    out.update(extra)
    return out


# Alias privato storico del costruttore di campo (stessa funzione).
_field = field


# ---------------------------------------------------------------------------
# S1 — AMBIENTE
# ---------------------------------------------------------------------------
def _levels_uv(levels):
    """[(h_m, u, v)] ordinati per quota, ignorando voci incomplete."""
    out = []
    for lv in levels or []:
        try:
            h = float(lv.get("height_m"))
            u = float(lv.get("u_ms"))
            v = float(lv.get("v_ms"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (math.isfinite(h) and math.isfinite(u) and math.isfinite(v)):
            continue
        out.append((h, u, v))
    out.sort(key=lambda t: t[0])
    return out


def _interp_uv(prof, h_target):
    """(u, v) interpolati linearmente a h_target; None fuori dal profilo."""
    if not prof:
        return None
    if h_target < prof[0][0] or h_target > prof[-1][0]:
        return None
    for i in range(len(prof) - 1):
        h1, u1, v1 = prof[i]
        h2, u2, v2 = prof[i + 1]
        if h1 <= h_target <= h2:
            if h2 - h1 <= 1e-9:
                return (u1, v1)
            f = (h_target - h1) / (h2 - h1)
            return (u1 + f * (u2 - u1), v1 + f * (v2 - v1))
    h, u, v = prof[-1]
    return (u, v)


def bulk_shear(prof, top_m=6000.0):
    """Shear bulk 0 -> top_m (m/s): |V(top) - V(superficie)|.

    Richiede un livello a <= 500 m e uno a >= top_m (altrimenti None: nessuna
    estrapolazione oltre il profilo)."""
    if len(prof) < 2:
        return None
    if prof[0][0] > 500.0 or prof[-1][0] < top_m:
        return None
    v0 = _interp_uv(prof, prof[0][0])
    v1 = _interp_uv(prof, top_m)
    if v0 is None or v1 is None:
        return None
    return math.hypot(v1[0] - v0[0], v1[1] - v0[1])


def bunkers_right_mover(prof, deviation=7.5):
    """Storm motion Bunkers right-mover (m/s) dal profilo disponibile.

    mean wind aritmetico + 7.5 m/s a destra del vettore shear (superficie ->
    top del profilo). Formula dichiarata: e' la stessa usata da
    environment.bunkers_right_mover (qui duplicata come funzione PURA perche'
    S1 deve restare testabile senza I/O)."""
    if not prof:
        return (0.0, 0.0)
    mu = sum(p[1] for p in prof) / len(prof)
    mv = sum(p[2] for p in prof) / len(prof)
    if len(prof) < 2:
        return (mu, mv)
    _h0, u0, v0 = prof[0]
    _h1, u1, v1 = prof[-1]
    shx, shy = u1 - u0, v1 - v0
    shmag = math.hypot(shx, shy)
    if shmag < 0.1:
        return (mu, mv)
    return (mu + deviation * shy / shmag, mv - deviation * shx / shmag)


def srh_from_hodograph(prof, top_m=3000.0, storm_motion=None):
    """SRH (m2/s2) 0 -> top_m dall'integrale discreto di Davies-Jones.

    Gate ESPLICITO: servono almeno SRH_MIN_WIND_LEVELS livelli di vento e
    almeno uno sopra SRH_MIN_TOP_M. Sotto quella soglia la curva
    dell'hodografo non e' definita dal dato e si ritorna None con il motivo
    (nessuna estrapolazione, nessun SRH "stimato")."""
    prof = [p for p in prof if p[0] <= top_m + 1e-9]
    if len(prof) < SRH_MIN_WIND_LEVELS:
        return {"value": None, "null_reason": "wind_levels<4",
                "n_levels": len(prof)}
    if prof[-1][0] < SRH_MIN_TOP_M:
        return {"value": None, "null_reason": "no_wind_level_above_2km",
                "n_levels": len(prof)}
    if storm_motion is None:
        storm_motion = bunkers_right_mover(prof)
    cx, cy = storm_motion
    srh = 0.0
    for i in range(len(prof) - 1):
        _h1, u1, v1 = prof[i]
        _h2, u2, v2 = prof[i + 1]
        srh += (u2 - cx) * (v1 - cy) - (u1 - cx) * (v2 - cy)
    return {"value": float(srh), "null_reason": None, "n_levels": len(prof)}


def environment_layer(env_detail=None, levels=None, null_reason=None):
    """S1: indici ambientali con fonte, formula e motivo dei null.

    env_detail: dict di phase2.environment.evaluate_environment (gia' scaricato).
    levels: profilo vento opzionale [{"height_m","u_ms","v_ms"}] per un calcolo
    locale (test e chiamanti che hanno il profilo completo). Se `levels` e'
    assente si usano i valori gia' calcolati da environment.py, con il gate sui
    livelli dichiarato da `flags.srh_low_levels_used`.

    1.2.4.20: `null_reason` FORZA il motivo di ogni sub-campo nullo e aggiunge
    `out["null_reason"]`. Serve al replay: quando l'ambiente manca perche' non
    esiste l'archivio di quel giorno, il motivo non e' "cape mancante nel
    payload corrente" ma `environment_unavailable_for_as_of`."""
    env = env_detail or {}
    prof = _levels_uv(levels)
    out = {"source": S1_SOURCE_ENV,
           "reference": REF_PUCIK,
           "formula": ("shear_0_6 = |V(6 km) - V(sup)|; "
                       "srh_0_3 = integrale discreto Davies-Jones con storm "
                       "motion di Bunkers; WMAXSHEAR = sqrt(2*CAPE)*shear_0_6")}

    cape = env.get("cape")
    out["cape_jkg"] = _field(
        None if cape is None else float(cape), S1_SOURCE_ENV, REF_PUCIK,
        null_reason=None if cape is not None else "open-meteo_cape_missing")
    cin = env.get("cin")
    out["cin_jkg"] = _field(
        None if cin is None else float(cin), S1_SOURCE_ENV, REF_PUCIK,
        null_reason=None if cin is not None else "open-meteo_cin_missing")

    # --- shear 0-6 km ---
    shear = None
    shear_reason = None
    if prof:
        shear = bulk_shear(prof, 6000.0)
        if shear is None:
            shear_reason = "wind_profile_incomplete_0_6km"
    else:
        shear = env.get("ebwd_ms")
        if shear is not None:
            shear = float(shear)
        else:
            shear_reason = "open-meteo_wind_10m_500hPa_missing"
    out["shear_0_6_ms"] = _field(
        shear, S1_SOURCE_ENV,
        REF_TASZAREK + " — proxy 0-5.5 km quando viene da 10m/500hPa",
        null_reason=shear_reason)

    # --- SRH 0-3 km ---
    if prof:
        srh = srh_from_hodograph(prof, 3000.0)
        srh_value, srh_reason = srh["value"], srh["null_reason"]
        n_levels = srh["n_levels"]
    else:
        srh_value = env.get("srh_0_3")
        flags = env.get("flags") or {}
        low = flags.get("srh_low_levels_used") or []
        # Nodi dell'hodografo DICHIARATI dal produttore (superficie + livelli di
        # pressione presenti): `srh_profile_nodes`. Se il payload non lo espone
        # si usano i soli livelli di pressione (conteggio CONSERVATIVO: senza il
        # nodo di superficie 3 livelli non bastano). Non si aggiunge nulla
        # "per stima": un SRH con meno di 4 nodi non e' un integrale su un
        # hodografo, e non si pubblica.
        try:
            n_levels = int(flags.get("srh_profile_nodes") or len(low))
        except (TypeError, ValueError):
            n_levels = len(low)
        if srh_value is not None and n_levels < SRH_MIN_WIND_LEVELS:
            # Il dato c'e' ma la curva non e' definita: il valore NON si
            # pubblica come SRH (sarebbe un integrale su troppi pochi nodi).
            srh_value = None
            srh_reason = "wind_levels<4"
        elif srh_value is None:
            srh_reason = "open-meteo_srh_not_derivable"
        else:
            srh_reason = None
        srh_value = None if srh_value is None else float(srh_value)
    out["srh_0_3_m2s2"] = _field(
        srh_value, S1_SOURCE_ENV,
        REF_DAVIES_JONES + "; " + REF_PUCIK, null_reason=srh_reason,
        n_levels=n_levels)

    lcl = env.get("sb_lcl_m")
    out["lcl_m"] = _field(
        None if lcl is None else float(lcl), S1_SOURCE_ENV, REF_LAWRENCE,
        null_reason=None if lcl is not None else "temperature_dewpoint_missing")

    wmax = None
    wmax_reason = "cape_or_shear_missing"
    if out["cape_jkg"]["value"] is not None and out["shear_0_6_ms"]["value"] is not None:
        wmax = math.sqrt(2.0 * max(out["cape_jkg"]["value"], 0.0)) * \
            out["shear_0_6_ms"]["value"]
        wmax_reason = None
    out["wmaxshear_ms2"] = _field(
        wmax, "calcolato da cape_jkg e shear_0_6_ms", REF_TASZAREK,
        null_reason=wmax_reason)
    if null_reason:
        out["null_reason"] = null_reason
        for key, val in out.items():
            if isinstance(val, dict) and "value" in val and val.get("value") is None:
                val["null_reason"] = null_reason
    return out


# ---------------------------------------------------------------------------
# S2 — GRANDINE
# ---------------------------------------------------------------------------
def vil_density(vil_kgm2, echo_top_m):
    """VIL density (g/m3) = VIL[kg/m2] / echo_top[m] * 1000.

    Unita': 1 kg/m2 su 1000 m di spessore = 1 kg/m3 = 1000 g/m3, quindi il
    fattore 1000 e' la conversione kg -> g. Echo top non valido -> None."""
    try:
        vil = float(vil_kgm2)
        top = float(echo_top_m)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(vil) and math.isfinite(top)) or top <= 0.0:
        return None
    return vil / top * 1000.0


def _as_grid(value):
    """ndarray 2D oppure None (accetta scalari per i test)."""
    if value is None:
        return None
    try:
        import numpy as np
    except Exception:                                   # pragma: no cover
        return None
    arr = np.asarray(value, dtype="float64")
    return arr if arr.ndim == 2 else None


def _w_z(dbz):
    """W(Z) di Witt (eq. 1, p. 288): 0 sotto Z_L=40, lineare 40-50, 1 sopra 50."""
    import numpy as np
    z = np.asarray(dbz, dtype="float64")
    return np.clip((z - WITT_Z_L_DBZ) /
                   (WITT_Z_U_DBZ - WITT_Z_L_DBZ), 0.0, 1.0)


def _e_dot(dbz):
    """E(Z) di Witt (eq. 1, p. 288): 5e-6 * 10^(0.084*Z) * W(Z) [J m-2 s-1].

    Il fattore 10^(0.084*Z) e' quello che distingue Witt dal VIL: senza di
    esso l'integrale sarebbe un peso adimensionale (ed e' l'errore che la
    1.2.4.17 commetteva)."""
    import numpy as np
    z = np.asarray(dbz, dtype="float64")
    return WITT_E_DOT_COEFF * np.power(10.0, WITT_E_DOT_EXP * z) * _w_z(z)


def _w_t(height_km, h0_km, h20_km):
    """W_T(H) di Witt (eq. 2, p. 288): 0 sotto H0, lineare fino a H(-20 C), 1 sopra."""
    import numpy as np
    h = np.asarray(height_km, dtype="float64")
    span = max(h20_km - h0_km, 1e-9)
    return np.clip((h - h0_km) / span, 0.0, 1.0)


def _dpc_level_mask(grid):
    """(in_copertura, con_eco) per un livello CAPPI secondo le sentinelle DPC.

    -9999 = fuori copertura a quella quota  -> NOT in coverage (dato assente)
    -9998 = dentro copertura ma senza eco   -> in coverage, NESSUN eco
    Un NaN/non finito vale come fuori copertura. Ritorna due maschere bool."""
    import numpy as np
    g = np.asarray(grid, dtype="float64")
    finite = np.isfinite(g)
    out_of_cov = finite & (np.abs(g - DPC_NODATA_OUT_OF_COVERAGE) <= 1e-6)
    no_echo = finite & (np.abs(g - DPC_NODATA_NO_ECHO) <= 1e-6)
    in_cov = finite & (~out_of_cov)
    echo = in_cov & (~no_echo)
    return in_cov, echo


def mesh_from_cappi(levels, h0_km, h20_km, top_km=None, table_available=True,
                    dh_m=MESH_DH_M, min_levels=MESH_MIN_CAPPI_LEVELS):
    """MESH (mm) dal profilo verticale dBZ(H) — Witt et al. 1998, eq. (1)-(3),(6).

    levels: lista ordinata [(quota_km, griglia_dBZ)] (griglie allineate).
    h0_km / h20_km: quote dello 0 C e del -20 C (m ASL / 1000), dall'ambiente.
    table_available: False -> MESH NON si calcola (null_reason
      "witt_table_unavailable"): il gate esiste perche' una tabella W(Z) non
      verificata non deve mai produrre un numero.

    Formula (trascritta dal paper, p. 288 e p. 293):
        E(Z)   = 5e-6 * 10^(0.084*Z) * W(Z)
        W(Z)   = 0 (Z<=40), (Z-40)/10 (40<Z<50), 1 (Z>=50)     [dBZ]
        W_T(H) = 0 (H<=H0), (H-H0)/(Hm20-H0) (H0<H<Hm20), 1 (H>=Hm20)
        SHI    = 0.1 * somma_i E(Z_i) * W_T(H_i) * dh          [J m-2 s-1]
        MEHS   = 2.54 * sqrt(SHI)                               [mm]

    Discretizzazione DICHIARATA: dh = 1000 m fisso (il passo reale dei CAPPI
    DPC), W_T valutato alla quota del livello; Witt usa componenti 2D con
    spessore adattivo e gate di 250 m-1 km. H0/Hm20 sono quote ASL da NWP
    (Witt le vuole ARL dal sounding). Nessun livello sotto 1 km: irrilevante
    per MESH (W=0 sotto 40 dBZ e H0 sta piu' in alto) ma dichiarato.

    Ritorna {"value": mm|None, "shi": ..., "null_reason": str|None,
             "cappi_levels": n, "levels_used": [...], "pixels": n,
             "min_levels_per_pixel": int|None}."""
    import numpy as np
    n = len(levels or [])
    levels_used = [round(float(h), 3) for h, _z in (levels or [])]
    if not table_available:
        return {"value": None, "shi": None,
                "null_reason": "witt_table_unavailable",
                "cappi_levels": n, "levels_used": levels_used,
                "pixels": 0, "min_levels_per_pixel": None}
    if n < int(min_levels):
        return {"value": None, "shi": None,
                "null_reason": "cappi_levels<{}".format(int(min_levels)),
                "cappi_levels": n, "levels_used": levels_used,
                "pixels": 0, "min_levels_per_pixel": None}
    if h0_km is None or h20_km is None:
        return {"value": None, "shi": None, "null_reason": "thermal_levels_missing",
                "cappi_levels": n, "levels_used": levels_used,
                "pixels": 0, "min_levels_per_pixel": None}
    try:
        h0 = float(h0_km)
        h20 = float(h20_km)
    except (TypeError, ValueError):
        return {"value": None, "shi": None, "null_reason": "thermal_levels_invalid",
                "cappi_levels": n, "levels_used": levels_used,
                "pixels": 0, "min_levels_per_pixel": None}
    if not (math.isfinite(h0) and math.isfinite(h20)) or not (0.0 < h0 < h20):
        return {"value": None, "shi": None, "null_reason": "thermal_levels_invalid",
                "cappi_levels": n, "levels_used": levels_used,
                "pixels": 0, "min_levels_per_pixel": None}

    # --- griglie + maschere -------------------------------------------------
    grids = []
    shape = None
    for h, z in levels:
        g = _as_grid(z)
        if g is None:
            # scalare -> griglia 1x1 (i test possono passare un massimo)
            g = np.asarray([[float(z)]], dtype="float64")
        if shape is None:
            shape = g.shape
        elif g.shape != shape:
            return {"value": None, "shi": None,
                    "null_reason": "cappi_grid_mismatch",
                    "cappi_levels": n, "levels_used": levels_used,
                    "pixels": 0, "min_levels_per_pixel": None}
        grids.append((float(h), g))
    grids.sort(key=lambda t: t[0])
    if top_km is not None:
        try:
            top = float(top_km)
        except (TypeError, ValueError):
            top = None
        if top is not None and math.isfinite(top):
            grids = [(h, g) for h, g in grids if h <= top + 1e-9]
    if len(grids) < 2:
        return {"value": None, "shi": None, "null_reason": "cappi_levels<2",
                "cappi_levels": n, "levels_used": levels_used,
                "pixels": 0, "min_levels_per_pixel": None}

    # --- integrale: E(Z_i) * W_T(H_i) * dh, solo dove il livello e' in copertura
    shi = np.zeros(shape, dtype="float64")
    n_in_cov = np.zeros(shape, dtype="int16")
    for h, g in grids:
        in_cov, echo = _dpc_level_mask(g)
        n_in_cov += in_cov.astype("int16")
        z = np.where(echo, g, -np.inf)
        contrib = _e_dot(z) * float(_w_t(h, h0, h20)) * float(dh_m)
        # fuori copertura: nessun contributo (il livello non esiste li')
        shi += np.where(in_cov, contrib * WITT_SHI_SCALE, 0.0)
    shi = np.where(np.isfinite(shi), shi, 0.0)

    usable = n_in_cov >= int(min_levels)
    if not bool(usable.any()):
        reason = ("pixel_coverage_below_{}_levels".format(int(min_levels))
                  if (n_in_cov.size and int(n_in_cov.max()) < int(min_levels))
                  else "no_valid_dbz")
        return {"value": None, "shi": None, "null_reason": reason,
                "cappi_levels": n, "levels_used": [round(h, 3) for h, _ in grids],
                "pixels": 0,
                "min_levels_per_pixel": (int(n_in_cov.max())
                                         if n_in_cov.size else None)}
    mesh = WITT_MESH_MM_COEFF * np.sqrt(np.maximum(shi, 0.0))
    mesh = np.where(usable, mesh, np.nan)
    if not np.isfinite(mesh).any():
        return {"value": None, "shi": None, "null_reason": "no_valid_dbz",
                "cappi_levels": n, "levels_used": [round(h, 3) for h, _ in grids],
                "pixels": 0, "min_levels_per_pixel": None}
    return {"value": float(np.nanmax(mesh)),
            "shi": float(np.nanmax(np.where(usable, shi, np.nan))),
            "null_reason": None, "cappi_levels": n,
            "levels_used": [round(h, 3) for h, _ in grids],
            "pixels": int(np.isfinite(mesh).sum()),
            "min_levels_per_pixel": int(n_in_cov[usable].min())}


def minus20_from_lapse(h0_m, lapse_c_per_km):
    """Quota del -20 C dalla quota dello 0 C e dal lapse rate (m ASL).

    Formula dichiarata: h(-20) = h(0) + 20/LR * 1000, con LR in C/km
    (lr_700_500 gia' calcolato da environment.py sul profilo NWP). E'
    un'estrapolazione LINEARE sopra il livello di congelamento: non una
    misura. None se h0 o LR non sono utilizzabili (nessuna quota inventata)."""
    try:
        h0 = float(h0_m)
        lr = float(lapse_c_per_km)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(h0) and math.isfinite(lr)) or lr <= 0.5 or h0 < 0.0:
        return None
    h20 = h0 + 20.0 / lr * 1000.0
    if not math.isfinite(h20) or h20 > 20000.0:
        return None
    return h20


def minus20_height_m(t700_c, z700_m, t500_c, z500_m):
    """Quota del -20 C stimata dal lapse rate 700-500 hPa (m).

    Formula dichiarata: LR = (T700 - T500)/(z500 - z700); quota del -20 C per
    estrapolazione lineare sotto 700 hPa e sopra 500 hPa. None se il profilo non
    e' utilizzabile (nessuna quota inventata)."""
    try:
        t7 = float(t700_c)
        z7 = float(z700_m)
        t5 = float(t500_c)
        z5 = float(z500_m)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (t7, z7, t5, z5)) or z5 <= z7:
        return None
    lr = (t7 - t5) / (z5 - z7)                 # C per metro (positivo)
    if lr <= 1e-6:
        return None
    h20 = z5 + (t5 + 20.0) / lr
    if not math.isfinite(h20) or h20 < z5 - 1e-9 or h20 > 20000.0:
        return None
    return h20


def hail_layer(cappi_levels=None, vil_grid=None, etm_m_grid=None,
               h0_m=None, h20_m=None, mesh_table_available=True,
               mesh_dh_m=MESH_DH_M, mask=None, footprint=None,
               h0_source=None, h20_source=None, slot_iso=None,
               slot_offset_s=None):
    """S2: VIL density + MESH, ciascuno con motivo esplicito se null.

    cappi_levels: {quota_km: griglia_dBZ} oppure [(quota_km, griglia)].
    vil_grid / etm_m_grid: griglie DPC (VIL in kg/m2, ETM in METRI).
    h0_m / h20_m: quote termiche (m ASL) dal profilo NWP; h0_source/h20_source
    dichiarano DA DOVE vengono (freezing level Open-Meteo, interpolazione del
    -20 C sul profilo T, oppure estrapolazione dal lapse rate).
    mask/footprint: maschera bool (stessa forma delle griglie) che limita il
    calcolo ai pixel della CELLA (identita' del cell_id, nessun blob match).
    Le soglie grandine NON vengono applicate: si pubblica il valore e la
    soglia CITATA, perche' nessuna validazione locale esiste."""
    out = {"threshold_ref": VIL_DENSITY_THRESHOLD_REF,
           "vil_density_threshold": VIL_DENSITY_THRESHOLD_G_M3,
           "mesh_unavailable_reason": None}

    # --- VIL density ---
    vd = None
    vd_reason = None
    if vil_grid is None:
        vd_reason = "vil_product_missing"
    elif etm_m_grid is None:
        vd_reason = "etm_product_missing"
    else:
        vd = _max_vil_density(vil_grid, etm_m_grid, mask=mask)
        if vd is None:
            vd_reason = "no_valid_vil_or_echo_top"
    out["vil_density_g_m3"] = _field(
        vd, S2_SOURCE_DPC, REF_AMBURN_WOLF, null_reason=vd_reason,
        threshold_check=(">= {:.1f} g/m3".format(VIL_DENSITY_THRESHOLD_G_M3)
                         if vd is not None else None),
        above_threshold=(None if vd is None
                         else bool(vd >= VIL_DENSITY_THRESHOLD_G_M3)))

    # --- MESH (Witt 1998; gate sui livelli e sulla tabella verificata) ---
    levels = []
    if isinstance(cappi_levels, dict):
        levels = sorted(((float(k), v) for k, v in cappi_levels.items()),
                        key=lambda t: t[0])
    elif cappi_levels:
        levels = sorted(cappi_levels, key=lambda t: float(t[0]))
    if mask is not None and levels:
        import numpy as np
        masked = []
        for h, g in levels:
            arr = _as_grid(g)
            if arr is None:
                arr = np.asarray([[float(g)]], dtype="float64")
            masked.append((h, _apply_mask(arr, mask)))
        levels = masked
    h0_km = None if h0_m is None else float(h0_m) / 1000.0
    h20_km = None if h20_m is None else float(h20_m) / 1000.0
    mesh = mesh_from_cappi(levels, h0_km, h20_km,
                           table_available=mesh_table_available, dh_m=mesh_dh_m)
    out["mesh_unavailable_reason"] = mesh["null_reason"]
    out["mesh_mm"] = _field(
        mesh["value"], S2_SOURCE_DPC, REF_WITT, null_reason=mesh["null_reason"],
        cappi_levels=mesh["cappi_levels"], cappi_levels_used=mesh["levels_used"],
        shi=mesh["shi"], dh_m=float(mesh_dh_m), h0_m=h0_m, h20_m=h20_m,
        h0_source=h0_source, h20_source=h20_source,
        cappi_slot_iso=slot_iso, cappi_slot_offset_s=slot_offset_s,
        slot_mismatch=(None if slot_offset_s is None
                       else bool(int(slot_offset_s) != 0)),
        pixels=mesh["pixels"],
        min_levels_per_pixel=mesh["min_levels_per_pixel"],
        paper_rounding_step_mm=WITT_PAPER_ROUND_MM,
        note=("diametro massimo ATTESO (MEHS di Witt eq. 6), sovrastima nota "
              "(Picca & Ryzhkov 2012 in Murillo & Homeyer 2019); nessuna "
              "verifica italiana; il paper arrotonda l'output a 6.35 mm"))
    return out


def _apply_mask(grid, mask):
    """Griglia con i pixel fuori maschera portati a -9999 (fuori copertura).

    Cosi' i pixel della cella sono gli unici che contribuiscono e quelli fuori
    NON entrano come "zero" (che sarebbe un valore inventato)."""
    import numpy as np
    g = np.asarray(grid, dtype="float64").copy()
    m = np.asarray(mask, dtype=bool)
    if g.shape != m.shape:
        return g
    g[~m] = DPC_NODATA_OUT_OF_COVERAGE
    return g


def _max_vil_density(vil_grid, etm_m_grid, mask=None):
    """Massimo VIL density valido (g/m3) sulle due griglie; None se nessuno.

    mask: maschera bool opzionale (footprint della cella); i pixel fuori
    maschera non entrano nel massimo."""
    vg = _as_grid(vil_grid)
    eg = _as_grid(etm_m_grid)
    if vg is None or eg is None or vg.shape != eg.shape:
        return None
    import numpy as np
    ok = np.isfinite(vg) & np.isfinite(eg) & (eg > 0.0)
    if mask is not None:
        m = np.asarray(mask, dtype=bool)
        if m.shape == vg.shape:
            ok = ok & m
    with np.errstate(divide="ignore", invalid="ignore"):
        dens = np.where(ok, vg / eg * 1000.0, np.nan)
    if not np.isfinite(dens).any():
        return None
    return float(np.nanmax(dens))


# ---------------------------------------------------------------------------
# S2b — OVERHANG / NUCLEO ELEVATO (1.2.4.18) — misura, non diagnosi
# ---------------------------------------------------------------------------
# Livelli usati: CAPPI_2 (2 km) e CAPPI_6 (6 km), i due product type DPC che
# config.py dichiara. Le soglie 45/50 dBZ sono le stesse della sonda CAPPI del
# 2026-10-23 (che misuro' 381 colonne con dBZ6>=45 & dBZ2<45 e 583 con
# dBZ6>=50 sul frame 2026-07-01T14:00Z): sono una SCELTA INGEGNERISTICA, non
# una soglia validata, e non una diagnosi di rotazione.
OVERHANG_LOW_KM = 2.0
OVERHANG_HIGH_KM = 6.0
OVERHANG_CORE_DBZ = 45.0
OVERHANG_STRONG_DBZ = 50.0
REF_OVERHANG = ("SCELTA INGEGNERISTICA (nessuna soglia validata): colonna con "
                "dBZ(6 km) >= 45 e dBZ(2 km) < 45 = nucleo elevato; >= 50 dBZ a "
                "6 km = nucleo elevato forte. Valori di riferimento MISURATI "
                "sulla sonda CAPPI del 2026-10-23 (381 e 583 colonne su "
                "2026-07-01T14:00Z). Descrive una struttura, NON la rotazione")
S2B_SOURCE_DPC = "Radar-DPC CAPPI_2 + CAPPI_6 (GeoTIFF dBZ, griglia TM 1 km)"


def overhang_layer(low_grid, high_grid, mask=None, low_km=OVERHANG_LOW_KM,
                   high_km=OVERHANG_HIGH_KM, core_dbz=OVERHANG_CORE_DBZ,
                   strong_dbz=OVERHANG_STRONG_DBZ, slot_iso=None,
                   slot_offset_s=None):
    """Nucleo elevato / gradiente verticale fra due CAPPI, per CELLA.

    low_grid / high_grid: griglie dBZ dei due livelli (stessa forma).
    mask: footprint della cella (maschera bool). I pixel fuori maschera non
    entrano in nessun conteggio.

    Maschere: -9999 (fuori copertura a quella quota) -> colonna NON valutabile;
    -9998 (in copertura, senza eco) -> "nessun eco", quindi dBZ < soglia e
    contributo 0 al gradiente (non e' uno 0 dBZ!).

    Ritorna un dict di campi con value/null_reason/source/reference piu' i
    conteggi: elevated_core, strong_elevated_core, gradient_max_dbz,
    gradient_p90_dbz, columns, elevated, strong."""
    import numpy as np
    out = {"levels": {"low_km": float(low_km), "high_km": float(high_km)},
           "core_dbz": float(core_dbz), "strong_dbz": float(strong_dbz),
           "cappi_slot_iso": slot_iso,
           "cappi_slot_offset_s": slot_offset_s}
    lo = _as_grid(low_grid)
    hi = _as_grid(high_grid)
    null_base = {}
    if lo is None or hi is None or lo.shape != hi.shape:
        reason = ("cappi_low_or_high_missing"
                  if lo is None or hi is None else "cappi_grid_mismatch")
        for key in ("elevated_core", "strong_elevated_core",
                    "gradient_max_dbz", "gradient_p90_dbz"):
            out[key] = _field(None, S2B_SOURCE_DPC, REF_OVERHANG,
                              null_reason=reason, **null_base)
        out.update({"columns": 0, "elevated": 0, "strong": 0})
        return out

    lo_cov, lo_echo = _dpc_level_mask(lo)
    hi_cov, hi_echo = _dpc_level_mask(hi)
    both = lo_cov & hi_cov
    if mask is not None:
        m = np.asarray(mask, dtype=bool)
        if m.shape == both.shape:
            both = both & m
    columns = int(both.sum())
    if columns == 0:
        for key in ("elevated_core", "strong_elevated_core",
                    "gradient_max_dbz", "gradient_p90_dbz"):
            out[key] = _field(None, S2B_SOURCE_DPC, REF_OVERHANG,
                              null_reason="no_overlapping_cappi_coverage",
                              **null_base)
        out.update({"columns": 0, "elevated": 0, "strong": 0})
        return out

    # dBZ "assente" = sotto qualunque soglia (mai 0 dBZ)
    lo_z = np.where(lo_echo, lo, -np.inf)
    hi_z = np.where(hi_echo, hi, -np.inf)
    elevated = both & hi_echo & (hi >= float(core_dbz)) & (lo_z < float(core_dbz))
    strong = both & hi_echo & (hi >= float(strong_dbz))
    grad_ok = both & lo_echo & hi_echo
    grad = np.where(grad_ok, hi - lo, np.nan)
    n_elev = int(elevated.sum())
    n_strong = int(strong.sum())
    gmax = float(np.nanmax(grad)) if np.isfinite(grad).any() else None
    gp90 = float(np.nanpercentile(grad, 90)) if np.isfinite(grad).any() else None

    out["elevated_core"] = _field(
        bool(n_elev > 0), S2B_SOURCE_DPC, REF_OVERHANG, null_reason=None,
        columns=columns, elevated_columns=n_elev,
        fraction=(round(n_elev / float(columns), 6) if columns else None))
    out["strong_elevated_core"] = _field(
        bool(n_strong > 0), S2B_SOURCE_DPC, REF_OVERHANG, null_reason=None,
        columns=columns, strong_columns=n_strong,
        fraction=(round(n_strong / float(columns), 6) if columns else None))
    out["gradient_max_dbz"] = _field(
        gmax, S2B_SOURCE_DPC, REF_OVERHANG,
        null_reason=None if gmax is not None else "no_column_with_echo_both_levels",
        columns_with_echo=int(grad_ok.sum()))
    out["gradient_p90_dbz"] = _field(
        gp90, S2B_SOURCE_DPC, REF_OVERHANG,
        null_reason=None if gp90 is not None else "no_column_with_echo_both_levels")
    out.update({"columns": columns, "elevated": n_elev, "strong": n_strong})
    return out


# ---------------------------------------------------------------------------
# S1b — BUNKERS RM/LM (1.2.4.18) — DESCRITTORE del moto atteso, non rotazione
# ---------------------------------------------------------------------------
REF_BUNKERS = ("Bunkers et al. 2000, euristico, non validato quantitativamente "
               "(Bunkers, Richardson, Thompson, Hart & Markowski 2000, Wea. "
               "Forecasting 15:61-79)")
BUNKERS_DEV_MS = 7.5           # offset laterale dell'euristica (7.5 m/s)
BUNKERS_TOP_M = 6000.0         # 0-6 km: se il profilo non lo copre -> null
BUNKERS_MIN_SHEAR_MS = 1.0     # sotto questo shear la perpendicolare e' rumore
S1B_SOURCE = "Open-Meteo hourly (profilo vento con quote) + moto osservato dal tracking"

_BUNKERS_NOTE = ("descrittore ambientale del moto atteso: NON e' una prova di "
                 "rotazione, NON identifica una struttura rotante e non e' una "
                 "probabilita'. rotation_measurable resta false")


def _trapz_mean_wind(prof, top_m):
    """Media del vento sullo strato [base, top_m] (regola del trapezio, m/s).

    Le quote sono quelle dichiarate dal profilo: nessuna estrapolazione oltre
    l'ultimo nodo (profilo che non arriva a top_m -> None)."""
    if not prof or len(prof) < 2:
        return None
    nodes = []
    for i, (h, u, v) in enumerate(prof):
        if h <= top_m:
            nodes.append((h, u, v))
            continue
        h1, u1, v1 = prof[i - 1]
        if h <= h1:
            return None
        f = (top_m - h1) / (h - h1)
        nodes.append((top_m, u1 + f * (u - u1), v1 + f * (v - v1)))
        break
    if len(nodes) < 2 or nodes[-1][0] < top_m - 1e-6:
        return None
    num_u = num_v = den = 0.0
    for i in range(len(nodes) - 1):
        h1, u1, v1 = nodes[i]
        h2, u2, v2 = nodes[i + 1]
        dh = h2 - h1
        if dh <= 0:
            continue
        num_u += 0.5 * (u1 + u2) * dh
        num_v += 0.5 * (v1 + v2) * dh
        den += dh
    if den <= 0:
        return None
    return (num_u / den, num_v / den)


def bunkers_rm_lm(prof, top_m=BUNKERS_TOP_M, deviation=BUNKERS_DEV_MS):
    """Bunkers right-mover / left-mover dal profilo con quote (m/s).

    Formula: moto medio 0-top (trapezio sulle quote), shear = V(top)-V(base),
    deviazione = `deviation` m/s lungo la perpendicolare DESTRA dello shear
    (shear_v, -shear_u)/|shear|. Il moto sinistro e' la deviazione opposta.

    Gate: il profilo deve partire da <= 500 m e arrivare a >= top_m; altrimenti
    {"value": None, "null_reason": "wind_profile_incomplete_0_6km"} — nessuna
    estrapolazione oltre il dato (il 500 hPa da solo si ferma a ~5,5 km).
    Se |shear| < BUNKERS_MIN_SHEAR_MS la perpendicolare non e' definita:
    null_reason "shear_below_1ms"."""
    if not prof or len(prof) < 2:
        return {"value": None, "null_reason": "open-meteo_wind_profile_missing",
                "profile_top_m": (prof[-1][0] if prof else None), "n_levels": 0}
    profile_top = prof[-1][0]
    if prof[0][0] > 500.0 or profile_top < top_m - 1e-6:
        return {"value": None, "null_reason": "wind_profile_incomplete_0_6km",
                "profile_top_m": profile_top, "n_levels": len(prof)}
    base = _interp_uv(prof, prof[0][0])
    top = _interp_uv(prof, top_m)
    if base is None or top is None:
        return {"value": None, "null_reason": "wind_profile_incomplete_0_6km",
                "profile_top_m": profile_top, "n_levels": len(prof)}
    mean = _trapz_mean_wind(prof, top_m)
    if mean is None:
        return {"value": None, "null_reason": "wind_profile_incomplete_0_6km",
                "profile_top_m": profile_top, "n_levels": len(prof)}
    shx, shy = top[0] - base[0], top[1] - base[1]
    shmag = math.hypot(shx, shy)
    if shmag < BUNKERS_MIN_SHEAR_MS:
        return {"value": None, "null_reason": "shear_below_1ms",
                "profile_top_m": profile_top, "n_levels": len(prof),
                "mean_wind_uv": mean, "shear_ms": shmag}
    px, py = shy / shmag, -shx / shmag          # perpendicolare a destra
    rm = (mean[0] + deviation * px, mean[1] + deviation * py)
    lm = (mean[0] - deviation * px, mean[1] - deviation * py)
    return {"value": {"mean": mean, "shear_uv": (shx, shy), "shear_ms": shmag,
                      "rm": rm, "lm": lm, "deviation_ms": float(deviation),
                      "top_m": float(top_m)},
            "null_reason": None, "profile_top_m": profile_top,
            "n_levels": len(prof)}


def vector_to_speed_dir(u, v):
    """(u,v) m/s -> (velocita' m/s, direzione VERSO cui, gradi da nord)."""
    try:
        u, v = float(u), float(v)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(u) and math.isfinite(v)):
        return None
    speed = math.hypot(u, v)
    toward = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0
    return speed, toward


def observed_motion_uv(velocity_kmh, direction_toward_deg):
    """Moto osservato dal tracking -> (u, v) in m/s (None se non utilizzabile)."""
    try:
        speed = float(velocity_kmh) / 3.6
        toward = float(direction_toward_deg)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(speed) and math.isfinite(toward)):
        return None
    rad = math.radians(toward)
    return (speed * math.sin(rad), speed * math.cos(rad))


def motion_deviation(obs_uv, ref_uv):
    """Scostamento fra moto osservato e moto di riferimento Bunkers.

    Ritorna {"angle_deg": float, "magnitude_ms": float}: `angle_deg` e' firmato
    e positivo quando il moto osservato e' a DESTRA del riferimento (senso
    orario), `magnitude_ms` = |V_obs - V_rif|. None se uno dei due manca."""
    if obs_uv is None or ref_uv is None:
        return None
    (ou, ov), (ru, rv) = obs_uv, ref_uv
    if not all(math.isfinite(x) for x in (ou, ov, ru, rv)):
        return None
    cross = ru * ov - rv * ou
    dot = ru * ou + rv * ov
    if math.hypot(ru, rv) <= 1e-9 or math.hypot(ou, ov) <= 1e-9:
        return None
    angle = math.degrees(math.atan2(cross, dot))
    return {"angle_deg": float(angle),
            "magnitude_ms": float(math.hypot(ou - ru, ov - rv))}


def bunkers_layer(levels=None, env_detail=None, observed_motion=None,
                  top_m=BUNKERS_TOP_M, deviation=BUNKERS_DEV_MS,
                  max_observed_speed_kmh=None):
    """S1b: moto Bunkers RM/LM + scostamento dal moto osservato.

    levels: profilo [{"height_m","u_ms","v_ms"}] (test / chiamanti con profilo).
    env_detail: dict di phase2.environment.evaluate_environment (usa
    `wind_profile`, che porta le quote).
    observed_motion: dict {"velocity_kmh", "direction_toward_deg"} dal tracking
    (identita' del candidato, non un matching). Senza moto osservato la
    deviazione e' null con motivo, il moto Bunkers resta pubblicato."""
    prof = _levels_uv(levels if levels is not None
                      else ((env_detail or {}).get("wind_profile") or []))
    res = bunkers_rm_lm(prof, top_m=top_m, deviation=deviation)
    out = {"reference": REF_BUNKERS, "note": _BUNKERS_NOTE,
           "profile_levels": res["n_levels"],
           "profile_top_m": res["profile_top_m"]}
    val = res["value"]
    out["rm_ms"] = _field(
        None if val is None else _vec_field(val["rm"]),
        S1B_SOURCE, REF_BUNKERS, null_reason=res["null_reason"],
        top_m=float(top_m), deviation_ms=float(deviation))
    out["lm_ms"] = _field(
        None if val is None else _vec_field(val["lm"]),
        S1B_SOURCE, REF_BUNKERS, null_reason=res["null_reason"])
    out["mean_wind_0_6_ms"] = _field(
        None if val is None else _vec_field(val["mean"]),
        S1B_SOURCE, REF_BUNKERS, null_reason=res["null_reason"],
        method="media del vento per trapezio sulle quote del profilo")
    out["shear_0_6_ms"] = _field(
        None if val is None else float(val["shear_ms"]),
        S1B_SOURCE, REF_BUNKERS, null_reason=res["null_reason"])

    obs = observed_motion or {}
    obs_uv = observed_motion_uv(obs.get("velocity_kmh"),
                                obs.get("direction_toward_deg"))
    obs_reason = None if obs_uv is not None else "observed_motion_missing"
    obs_speed = obs.get("velocity_kmh")
    # GATE CINEMATICO: un moto osservato oltre la soglia del gate Fase 1.9
    # (config storm.motion.max_validated_velocity_kmh, 200 km/h) NON e'
    # utilizzabile: pubblicare una deviazione da 226 km/h sarebbe un numero
    # senza significato fisico (misurato in un run reale il 2026-10-23).
    if obs_uv is not None and max_observed_speed_kmh is not None:
        try:
            if float(obs_speed) > float(max_observed_speed_kmh):
                obs_uv = None
                obs_reason = "observed_motion_above_kinematic_gate"
        except (TypeError, ValueError):
            obs_uv = None
            obs_reason = "observed_motion_missing"
    out["observed_motion_ms"] = _field(
        None if obs_uv is None else _vec_field(obs_uv),
        "radar_engine tracking (moto del centroide osservato)",
        "misura del tracking, non un modello", null_reason=obs_reason,
        kinematic_gate_kmh=max_observed_speed_kmh,
        observed_speed_kmh=obs_speed)
    dev = None
    dev_reason = obs_reason
    if val is not None and obs_uv is not None:
        dev = motion_deviation(obs_uv, val["rm"])
        dev_reason = None if dev is not None else "deviation_not_computable"
    elif val is None:
        dev_reason = res["null_reason"]
    out["deviation_angle_deg"] = _field(
        None if dev is None else float(dev["angle_deg"]),
        S1B_SOURCE + " (osservato - RM)",
        REF_BUNKERS + " — angolo firmato: positivo = osservato a destra del RM",
        null_reason=dev_reason)
    out["deviation_magnitude_ms"] = _field(
        None if dev is None else float(dev["magnitude_ms"]),
        S1B_SOURCE + " (osservato - RM)", REF_BUNKERS,
        null_reason=dev_reason)
    return out


def _vec_field(uv):
    """Rappresentazione pubblica di un vettore vento (m/s + direzione verso)."""
    sd = vector_to_speed_dir(uv[0], uv[1])
    return {"u_ms": round(float(uv[0]), 2), "v_ms": round(float(uv[1]), 2),
            "speed_ms": None if sd is None else round(sd[0], 2),
            "toward_deg": None if sd is None else round(sd[1], 1)}


# ---------------------------------------------------------------------------
# S3 — OVERSHOOTING TOP (riuso di phase2.satellite_ot)
# ---------------------------------------------------------------------------
def ot_layer_from_details(ot_details, timestamp_iso=None, null_reason=None):
    """S3 da un risultato GIA' calcolato di satellite_ot.evaluate_ir_ot.

    1.2.4.20: `null_reason` sostituisce il motivo quando l'assenza del frame
    NON e' "non pubblicato" ma "non esiste in archivio" (replay --as-of:
    `ir108_history_unavailable`). Default invariato."""
    if not ot_details:
        return _field(None, S3_SOURCE_IR, REF_BEDKA,
                      null_reason=null_reason or "ir108_frame_unavailable",
                      timestamp=timestamp_iso)
    valid = int(ot_details.get("valid_pixels") or 0)
    if valid <= 0:
        return _field(None, S3_SOURCE_IR, REF_BEDKA,
                      null_reason="no_valid_ir_pixels",
                      timestamp=timestamp_iso)
    flag = bool(ot_details.get("ot_flag"))
    n_flags = int(ot_details.get("n_flags") or 0)
    # Confidence: funzione della quantita' di pixel OT e della CTT minima.
    # SCELTA INGEGNERISTICA (nessuna calibrazione): 3 pixel OT -> "high",
    # 1-2 -> "medium", nessun flag -> "low" (assenza di flag NON e' assenza di
    # updraft: e' assenza di firma a 1 km/10.8 um).
    if flag and n_flags >= 3:
        conf = "high"
    elif flag:
        conf = "medium"
    else:
        conf = "low"
    return _field(
        flag, S3_SOURCE_IR, REF_BEDKA,
        null_reason=None, timestamp=timestamp_iso, confidence=conf,
        n_flags=n_flags, valid_pixels=valid,
        ctt_min_c=ot_details.get("ctt_min_c"),
        cold_top=bool(ot_details.get("cold_top")),
        thresholds={"ctt_c": ot_details.get("ctt_threshold_c"),
                    "anvil_c": ot_details.get("anvil_threshold_c"),
                    "anomaly_c": ot_details.get("anomaly_threshold_c"),
                    "ring_radius_px": ot_details.get("ring_radius_px")},
        confidence_note=REF_TECHNICAL_CHOICE)


def ot_layer_from_grid(ctt_grid, valid_mask=None, pixel_area_km2=None,
                       timestamp_iso=None, **kwargs):
    """S3 da griglia IR: riusa satellite_ot.evaluate_ir_ot (nessuna riscrittura)."""
    from .phase2 import satellite_ot as _ot
    if ctt_grid is None:
        return ot_layer_from_details(None, timestamp_iso)
    try:
        details = _ot.evaluate_ir_ot(ctt_grid, valid_mask=valid_mask,
                                     pixel_area_km2=pixel_area_km2, **kwargs)
    except Exception:
        return _field(None, S3_SOURCE_IR, REF_BEDKA,
                      null_reason="ot_evaluation_failed",
                      timestamp=timestamp_iso)
    return ot_layer_from_details(details, timestamp_iso)


# ---------------------------------------------------------------------------
# Verdetto unico (nessuna probabilita' finta)
# ---------------------------------------------------------------------------
def combine(structure=None, hail=None, environment=None, ot=None,
            overhang=None, bunkers=None):
    """Compone S1/S1b/S2/S2b/S3 in un unico oggetto DICHIARATIVO.

    Non esiste un numero di probabilita': senza etichette (ESWD QC1) e senza
    >= 62 eventi / 380 allarmi una probabilita' non e' stimabile
    (docs/METODI_VALIDATI_SEVERO.md, sez. 4.1). Il campo `probability` e'
    quindi null con il suo motivo, e `rotation_measurable` e' false con la
    motivazione fisica.

    1.2.4.18: `overhang` (nucleo elevato da CAPPI_2 vs CAPPI_6) e `bunkers`
    (moto atteso RM/LM + scostamento osservato) sono DESCRITTORI: nessuno dei
    due cambia `rotation_measurable`, che resta false."""
    hail = hail or {}
    return {
        "version": SEVERE_STACK_VERSION,
        "structure": structure,
        "hail_risk": {
            "vil_density": hail.get("vil_density_g_m3"),
            "mesh": hail.get("mesh_mm"),
            "threshold_ref": VIL_DENSITY_THRESHOLD_REF,
            "mesh_unavailable_reason": hail.get("mesh_unavailable_reason"),
        },
        "overhang": overhang,
        "bunkers": bunkers,
        "environment": environment,
        "ot": ot,
        "rotation_measurable": False,
        "rotation_null_reason": "no_radial_velocity_in_dpc_2d_mosaic",
        "probability": None,
        "probability_null_reason": ("uncalibrated: nessuna etichetta ESWD QC1 "
                                    "e nessuna numerosita' minima (>=62 eventi "
                                    "/ >=380 allarmi)"),
    }
