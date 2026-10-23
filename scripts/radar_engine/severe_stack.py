#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — severe_stack.py (S1 + S2 + S3, nessun numero finto)

Tre componenti indipendenti, ciascuna con la PROPRIA fonte e la PROPRIA
citazione; `combine()` le mette in un unico verdetto SENZA inventare una
probabilita' calibrata (non esistono etichette: docs/METODI_VALIDATI_SEVERO.md,
sez. 4 e 5).

    S1  AMBIENTE      CAPE/CIN/shear 0-6 km/SRH 0-3 km dai modelli Open-Meteo
                      gia' scaricati dalla Fase 2 (environment.py). Riferimento
                      europeo: Pucik et al. 2015, MWR 143:4805-4821. La citazione
                      NON e' una soglia locale: le soglie europee descrivono
                      climatologie, non questa cella.
    S2  GRANDINE      VIL density (Amburn & Wolf 1997) e MESH (Witt et al. 1998).
                      MESH si calcola SOLO con >= 4 livelli CAPPI: Witt richiede
                      il profilo verticale dBZ(H). Con i 2 livelli configurati di
                      default (CAPPI_2, CAPPI_6) il campo e' null con
                      `mesh_unavailable_reason = "cappi_levels<4"`. NON si produce
                      un "MESH-like" da VIL: si chiamerebbe MESH senza esserlo.
    S3  OVERSHOOTING  riusa phase2/satellite_ot.py (Bedka et al. 2010) e riporta
        TOP           booleano + confidence, con fonte e timestamp del frame.

REGOLA DI ONESTA' (applicata da `_field`): un valore None SENZA motivazione e'
un errore di programmazione -> ValueError. Ogni campo pubblicato porta
`source` (da dove viene il numero) e `reference` (la citazione), e quando e'
null anche `null_reason`.

Nessuna funzione qui fa I/O: sono pure e testabili. Le soglie che NON hanno una
citazione sono marcate come SCELTA INGEGNERISTICA nel campo `reference`.
"""

import math

SEVERE_STACK_VERSION = "1.0.0"

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
            "Forecasting 13(2):286-303 — SHI/MESH con W(Z) 40-50 dBZ e W_T "
            "fra 0 e -20 C [riferimento bibliografico]")
REF_BEDKA = ("Bedka, Brunner, Dworak, Feltz, Otkin & Greenwald (2010), J. Appl. "
             "Meteor. Climatol. 49:181-202 — OT da gradiente IR 10.8 um")
REF_TECHNICAL_CHOICE = "SCELTA INGEGNERISTICA (nessuna citazione: non e' una soglia validata)"

# Amburn & Wolf 1997: soglia CITATA, riportata come stringa nell'output.
VIL_DENSITY_THRESHOLD_G_M3 = 3.5
VIL_DENSITY_THRESHOLD_REF = "Amburn&Wolf 1997 3.5 g/m3"

# Witt et al. 1998: minimo di livelli CAPPI per costruire dBZ(H).
MESH_MIN_CAPPI_LEVELS = 4
MESH_W_Z_MIN_DBZ = 40.0     # W(Z) = 0 sotto 40 dBZ
MESH_W_Z_FULL_DBZ = 50.0    # W(Z) = 1 sopra 50 dBZ
MESH_SCALE_MM = 2.54        # MESH[mm] = 2.54 * sqrt(SHI)

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


def environment_layer(env_detail=None, levels=None):
    """S1: indici ambientali con fonte, formula e motivo dei null.

    env_detail: dict di phase2.environment.evaluate_environment (gia' scaricato).
    levels: profilo vento opzionale [{"height_m","u_ms","v_ms"}] per un calcolo
    locale (test e chiamanti che hanno il profilo completo). Se `levels` e'
    assente si usano i valori gia' calcolati da environment.py, con il gate sui
    livelli dichiarato da `flags.srh_low_levels_used`.
    """
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
    """W(Z) di Witt: 0 sotto 40 dBZ, lineare 40-50, 1 sopra 50."""
    import numpy as np
    z = np.asarray(dbz, dtype="float64")
    return np.clip((z - MESH_W_Z_MIN_DBZ) /
                   (MESH_W_Z_FULL_DBZ - MESH_W_Z_MIN_DBZ), 0.0, 1.0)


def mesh_from_cappi(levels, h0_km, h20_km, top_km=None):
    """MESH (mm) dal profilo verticale dBZ(H) — Witt et al. 1998.

    levels: lista ordinata [(quota_km, griglia_dBZ)] con >= MESH_MIN_CAPPI_LEVELS
    livelli; h0_km / h20_km: quote dello 0 C e del -20 C (da NWP).
    SHI = 0.1 * integrale W(Z)*W_T(H) dH  (H in km, regola del trapezio fra
    livelli consecutivi), MESH = 2.54 * sqrt(SHI) in mm.
    Difetti dichiarati: passo verticale 1 km (Witt usa gate 250 m-1 km su ~14
    elevazioni), quote termiche da NWP e non da sounding.

    Ritorna {"value": mm|None, "shi": ..., "null_reason": str|None,
             "cappi_levels": n, "levels_used": [...]}."""
    import numpy as np
    n = len(levels or [])
    if n < MESH_MIN_CAPPI_LEVELS:
        return {"value": None, "shi": None, "null_reason": "cappi_levels<4",
                "cappi_levels": n, "levels_used": [round(h, 3) for h, _z in
                                                   (levels or [])]}
    if h0_km is None or h20_km is None:
        return {"value": None, "shi": None, "null_reason": "thermal_levels_missing",
                "cappi_levels": n,
                "levels_used": [round(h, 3) for h, _z in levels]}
    try:
        h0 = float(h0_km)
        h20 = float(h20_km)
    except (TypeError, ValueError):
        return {"value": None, "shi": None, "null_reason": "thermal_levels_invalid",
                "cappi_levels": n,
                "levels_used": [round(h, 3) for h, _z in levels]}
    if not (math.isfinite(h0) and math.isfinite(h20)) or not (0.0 < h0 < h20):
        return {"value": None, "shi": None, "null_reason": "thermal_levels_invalid",
                "cappi_levels": n,
                "levels_used": [round(h, 3) for h, _z in levels]}

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
            return {"value": None, "shi": None, "null_reason": "cappi_grid_mismatch",
                    "cappi_levels": n,
                    "levels_used": [round(hh, 3) for hh, _z in levels]}
        grids.append((float(h), g))

    if top_km is not None:
        try:
            top = float(top_km)
        except (TypeError, ValueError):
            top = None
        if top is not None and math.isfinite(top):
            grids = [(h, g) for h, g in grids if h <= top + 1e-9]

    shi = np.zeros(shape, dtype="float64")
    for i in range(len(grids) - 1):
        h1, z1 = grids[i]
        h2, z2 = grids[i + 1]
        dh = h2 - h1
        if dh <= 0:
            continue
        hm = 0.5 * (h1 + h2)
        if hm < h0:
            continue
        wt = min(1.0, (hm - h0) / (h20 - h0))
        shi += 0.1 * (0.5 * (_w_z(z1) + _w_z(z2))) * wt * dh
    shi = np.where(np.isfinite(shi), shi, 0.0)
    mesh = MESH_SCALE_MM * np.sqrt(np.maximum(shi, 0.0))
    if not np.isfinite(mesh).any():
        return {"value": None, "shi": None, "null_reason": "no_valid_dbz",
                "cappi_levels": n,
                "levels_used": [round(hh, 3) for hh, _z in levels]}
    return {"value": float(np.nanmax(mesh)), "shi": float(np.nanmax(shi)),
            "null_reason": None, "cappi_levels": n,
            "levels_used": [round(hh, 3) for hh, _z in grids]}


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
               h0_m=None, h20_m=None):
    """S2: VIL density + MESH, ciascuno con motivo esplicito se null.

    cappi_levels: {quota_km: griglia_dBZ} oppure [(quota_km, griglia)].
    vil_grid / etm_m_grid: griglie DPC (VIL in kg/m2, ETM in METRI).
    h0_m / h20_m: quote termiche (m ASL) dal profilo NWP.
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
        vd = _max_vil_density(vil_grid, etm_m_grid)
        if vd is None:
            vd_reason = "no_valid_vil_or_echo_top"
    out["vil_density_g_m3"] = _field(
        vd, S2_SOURCE_DPC, REF_AMBURN_WOLF, null_reason=vd_reason,
        threshold_check=(">= {:.1f} g/m3".format(VIL_DENSITY_THRESHOLD_G_M3)
                         if vd is not None else None),
        above_threshold=(None if vd is None
                         else bool(vd >= VIL_DENSITY_THRESHOLD_G_M3)))

    # --- MESH (solo con >=4 livelli CAPPI) ---
    levels = []
    if isinstance(cappi_levels, dict):
        levels = sorted(((float(k), v) for k, v in cappi_levels.items()),
                        key=lambda t: t[0])
    elif cappi_levels:
        levels = sorted(cappi_levels, key=lambda t: float(t[0]))
    h0_km = None if h0_m is None else float(h0_m) / 1000.0
    h20_km = None if h20_m is None else float(h20_m) / 1000.0
    mesh = mesh_from_cappi(levels, h0_km, h20_km)
    out["mesh_unavailable_reason"] = mesh["null_reason"]
    out["mesh_mm"] = _field(
        mesh["value"], S2_SOURCE_DPC, REF_WITT, null_reason=mesh["null_reason"],
        cappi_levels=mesh["cappi_levels"], cappi_levels_used=mesh["levels_used"],
        shi=mesh["shi"],
        note=("diametro massimo ATTESO, sovrastima nota (Picca & Ryzhkov 2012 "
              "in Murillo & Homeyer 2019); nessuna verifica italiana"))
    return out


def _max_vil_density(vil_grid, etm_m_grid):
    """Massimo VIL density valido (g/m3) sulle due griglie; None se nessuno."""
    vg = _as_grid(vil_grid)
    eg = _as_grid(etm_m_grid)
    if vg is None or eg is None or vg.shape != eg.shape:
        return None
    import numpy as np
    with np.errstate(divide="ignore", invalid="ignore"):
        dens = np.where(np.isfinite(vg) & np.isfinite(eg) & (eg > 0.0),
                        vg / eg * 1000.0, np.nan)
    if not np.isfinite(dens).any():
        return None
    return float(np.nanmax(dens))


# ---------------------------------------------------------------------------
# S3 — OVERSHOOTING TOP (riuso di phase2.satellite_ot)
# ---------------------------------------------------------------------------
def ot_layer_from_details(ot_details, timestamp_iso=None):
    """S3 da un risultato GIA' calcolato di satellite_ot.evaluate_ir_ot."""
    if not ot_details:
        return _field(None, S3_SOURCE_IR, REF_BEDKA,
                      null_reason="ir108_frame_unavailable",
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
def combine(structure=None, hail=None, environment=None, ot=None):
    """Compone S1/S2/S3 in un unico oggetto DICHIARATIVO.

    Non esiste un numero di probabilita': senza etichette (ESWD QC1) e senza
    >= 62 eventi / 380 allarmi una probabilita' non e' stimabile
    (docs/METODI_VALIDATI_SEVERO.md, sez. 4.1). Il campo `probability` e'
    quindi null con il suo motivo, e `rotation_measurable` e' false con la
    motivazione fisica."""
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
        "environment": environment,
        "ot": ot,
        "rotation_measurable": False,
        "rotation_null_reason": "no_radial_velocity_in_dpc_2d_mosaic",
        "probability": None,
        "probability_null_reason": ("uncalibrated: nessuna etichetta ESWD QC1 "
                                    "e nessuna numerosita' minima (>=62 eventi "
                                    "/ >=380 allarmi)"),
    }
