#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TASSONOMIA MULTI-ASSE AGGIUNTIVA per convective_structures.json.

Schema pubblicato: ``meteorisk.taxonomy/1``. Modulo PURO (nessuna rete, nessuna
I/O): legge SOLO descrittori radar osservati (VMI DPC 1 km/5 min, CAPPI DPC,
outline della maschera di cella) e produce campi NUOVI. Nessun campo esistente
viene modificato, nessuna classe viene mai promossa a "confirmed".

REGOLE SCIENTIFICHE (vincolo del capitolato):
  * SOLO campi radar OSSERVATI entrano nelle regole. Il contesto NWP
    (``nwp_environment_context``, ``env``, ``bunkers``, ``nwp_motion``, ...)
    resta separato e NON puo' aumentare la confidenza: è dichiarato
    ``nwp_influence: "none"`` in ogni campo.
  * Nessun campo ``probability``: ``rule_support`` (0-1) e' SUPPORTO DI REGOLE
    dichiarato (quanti gate sono soddisfatti), NON una probabilita' calibrata.
  * Ogni valore ha ``value`` / ``null_reason`` / ``source`` / ``reference`` /
    ``rule_support`` / ``confidence``. Un dato assente produce SEMPRE
    ``value=None`` con motivo esplicito (mai un valore inventato).
  * La confidenza della tassonomia e' SEPARATA dalla confidenza del tracking
    (``confidence``/``confidence_detail`` del record) ed e' al massimo
    ``medium``: non e' mai calcolata da SOI/SOI-v2/NWP.
  * Isteresi: una classe cambia solo se lo stesso gate e' soddisfatto per
    >=2 frame consecutivi; se l'ultimo dato non e' valido la classe TORNA a
    ``unknown`` (nessuna classe conservata oltre l'ultimo dato valido).
    ``valid_time`` e ``hysteresis_frames`` sono sempre dichiarati.

Le soglie 75/100 km, ratio 3 e persistenza 3 frame sono SCELTA INGEGNERISTICA
NON validata su casi italiani (dichiarato in REFERENCE e nei singoli campi).
"""
from __future__ import annotations

import math

TAXONOMY_VERSION = "1.0.0"
SCHEMA = "meteorisk.taxonomy/1"
SOURCE = "radar_engine.taxonomy (VMI/CAPPI DPC osservati; nessun NWP)"
UNKNOWN = "unknown"
CANDIDATE = "candidate"

ECHO_REGIMES = ("convective_like", "stratiform_like", "mixed", "no_echo",
                "unknown")
ORGANIZATION_MODES = (
    "isolated_cell_candidate", "cell_cluster", "broken_line",
    "linear_system_candidate", "embedded_convection",
    "areal_nonlinear_system", "unclassified",
)
SYSTEM_SCALES = ("cell_candidate", "cluster_candidate", "mcs_candidate",
                 "unknown")
MORPHOLOGY_TAG_NAMES = (
    "bow_echo_candidate", "radar_v_shape", "v_notch_reflectivity",
    "enhanced_V_IR", "split_candidate", "merge_candidate",
)
EVOLUTION_TAG_NAMES = ("intensification_candidate", "dissipation_candidate",
                       "steady_candidate")

# Campi che il modulo NON legge MAI (NWP / contesto). Servono solo a dichiarare
# esplicitamente il confine e a renderlo verificabile nei test.
NWP_FIELDS_IGNORED = (
    "nwp_environment_context", "nwp_motion", "nwp_motion_detail",
    "motion_blend", "motion_weights", "ensemble", "env", "env_scp",
    "bunkers", "severe_stack.hail_risk.environment",
)

RULE_SUPPORT_BASIS = (
    "gate radar soddisfatti / gate radar valutabili: SUPPORTO DI REGOLE, "
    "NON una probabilita' calibrata")
CONFIDENCE_BASIS = (
    "completezza dei SOLI descrittori radar osservati + numero di frame con "
    "gate valido; max medium; indipendente da SOI/SOI-v2/tracking/NWP")

DEFAULT_THRESHOLDS = {
    # Gate minimo convettivo: Gallus et al. 2008 (max >=30 dBZ, eco >10 dBZ
    # su area >6x6 km) e pysteps T-DaTing (minmax=41, minsize=50 px a 1 km).
    # Soglia candidata 35 dBZ / 50 km2; 41 dBZ e' la soglia di CONFERMA e NON
    # viene applicata da questo modulo.
    "convective_max_dbz": 35.0,
    "convective_min_area_km2": 50.0,
    "convective_confirm_dbz": 41.0,
    "stratiform_min_dbz": 20.0,
    "stratiform_max_dbz": 35.0,
    "no_echo_max_dbz": 10.0,
    "mixed_mean_ceiling_dbz": 35.0,
    # SCELTA INGEGNERISTICA non validata su casi italiani.
    "linear_min_length_km": 75.0,
    "mcs_min_length_km": 100.0,
    "min_aspect_ratio": 3.0,
    "min_persistence_frames": 3,
    "cell_max_area_km2": 500.0,
    "isolated_aspect_ceiling": 2.0,
    "cluster_min_cell_count": 2,
    "areal_min_area_km2": 1000.0,
    "bow_min_core_dbz": 40.0,
    "bow_score_gate": 0.6,
    "bow_min_frames": 2,
    "notch_min_depth_km": 5.0,
    "vs_shape_min_upstream_frames": 3,
    "vs_shape_min_frames": 2,
    "vs_shape_min_echo_6km_dbz": 35.0,
    "lineage_min_frames": 2,
    "intensify_delta_dbz": 2.0,
    "intensify_growth_pct": 15.0,
    "dissipate_delta_dbz": -3.0,
    "dissipate_growth_pct": -40.0,
    "evolution_min_frames": 2,
    "hysteresis_consecutive_frames": 2,
}

REFERENCE = {
    "convective": (
        "Gallus, Snook & Johnson 2008, WAF 23(1) 101-113, DOI "
        "10.1175/2007WAF2006120.1 (gate evento: durata>=1 h, eco max>=30 dBZ, "
        "eco >10 dBZ su area >6x6 km); pysteps T-DaTing (BSD-3), soglie "
        "tarate per 1 km/5 min: minmax=41 dBZ, minsize=50 px=50 km2, "
        "mintrack=3 passi. Soglia candidata 35 dBZ/50 km2: SCELTA "
        "INGEGNERISTICA conservativa; 41 dBZ resta gate di CONFERMA non "
        "applicato qui."),
    "stratiform": (
        "Rigo & Llasat 2004, NHESS 4 59-68 (pixel convettivo Z>43 dBZ; area "
        "convettiva <30% = stratiforme); soglia 20 dBZ = detect.dbz_threshold "
        "del motore. SCELTA INGEGNERISTICA per il gate candidato."),
    "mixed": (
        "Parker & Johnson, MWR 128(10) 3413-3436 (TS/LS/PS: stratiforme "
        "rispetto alla linea). SCELTA INGEGNERISTICA: core >=35 dBZ con media "
        "sotto soglia nella stessa struttura; la posizione storm-relative "
        "NON e' disponibile sulla VMI 2D."),
    "linear_system": (
        "Gallus et al. 2008 WAF 23(1): linea >=75 km, rapporto lunghezza/"
        "larghezza >=3, persistenza >=2 h; Ashley, Haberlie & Strohm 2019 "
        "WAF 34(6) 1605-1631: QLCS definita da segmentazione+ML+tracking, "
        "non da soglia fissa; BAMS 105(8) E1619-E1643: asse >=100 km, "
        "durata >=3 h, riflettivita' >=40 dBZ. SCELTA INGEGNERISTICA: qui "
        "75 km / ratio 3 / 3 frame (15 min). NON validata su casi italiani; "
        "nessuna classe e' confermata."),
    "mcs": (
        "BAMS 105(8) E1619-E1643 (asse >=100 km, >=3 h) e Parker & Johnson "
        "MWR 128(10) (>=100 km, >=3 h). SCELTA INGEGNERISTICA: 100 km / "
        "ratio 3 / 3 frame. NON validata su casi italiani."),
    "scale": (
        "pysteps T-DaTing: minsize=50 px (=50 km2 a 1 km), mintrack=3 passi "
        "(=15 min). SCELTA INGEGNERISTICA per 500 km2 (cluster) e 1000 km2 "
        "(sistema areale non lineare); nessuna calibrazione locale."),
    "bow_echo_candidate": (
        "Pacey, Schultz & Garcia-Carreras 2021 WAF 36(1) 237-252: solo ~10% "
        "dei venti Estremi e' bow echo, non attendere il bow per allertare. "
        "SCELTA INGEGNERISTICA: bordo di avanguardia convesso misurato su "
        ">=2 frame consecutivi con core >=40 dBZ. SOLO geometria 2D: NON "
        "inferisce raffiche, danno, derecho o mesovortici (servono centraline/"
        "METAR per il vento e Doppler per i mesovortici)."),
    "radar_v_shape": (
        "HESS 26 627-646 2022 (HyMeX SOP1 Italia): V-shaped MCS a rigenerazione "
        "retrograda, vertice quasi stazionario, nuovi core a monte vecchi "
        "advetti a valle, impatto 10-100 km2 in 1-3 h; Khodayar et al. 2021 "
        "ACP 21 17051 e' CONTESTO, non soglia. SCELTA INGEGNERISTICA: "
        "rigenerazione a monte su >=3 frame con vertice su >=2 frame e eco "
        "confermata a 6 km dai CAPPI. NON implica alluvione ne' intensita' "
        "di pioggia senza centraline."),
    "v_notch_reflectivity": (
        "SCELTA INGEGNERISTICA: intaglio/incavo locale sul fianco posteriore "
        "del core, misurato su UN singolo frame VMI 1 km/5 min. Solo "
        "geometria 2D: NON e' back-building, NON e' grandine, NON e' vento "
        "e NON e' enhanced-V."),
    "enhanced_V_IR": (
        "Homeyer 2014, JAS 71(1) 332-348: firma termica a V sopra l'incudine; "
        "studio di MECCANISMO (cirri sopra-anvil), nessuna soglia IR "
        "numerica verificata; per soglie: Brunner et al. 2007 WAF 22:853. "
        "NON calcolabile da VMI/CAPPI: richiede immagine IR valida. Oggi "
        "assente -> null con null_reason. NON e' radar_v_shape."),
    "evolution": (
        "SCELTA INGEGNERISTICA: intensificazione/dissipazione da delta dBZ e "
        "variazione di area su >=2 frame consecutivi; NON tarato, NON una "
        "probabilita'. split/merge richiedono eventi di lineage espliciti "
        "su >=2 frame (pysteps split_frac=merge_frac=0.1 come riferimento)."),
    "hysteresis": (
        "SCELTA INGEGNERISTICA: cambiamento di classe solo dopo 2 frame "
        "consecutivi con lo stesso gate; un datum invalido azzera lo stato "
        "(nessuna classe conservata oltre l'ultimo dato valido)."),
}

LOGICAL_VARIABLES = (
    "echo_regime", "organization_mode", "system_scale", "morphology_tags",
    "evolution_stage", "enhanced_V_IR",
)


def _finite(value):
    """float finito o None (nessun NaN/Inf nei payload JSON)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _dbz(value):
    """dBZ finito o None: esclude le sentinelle nodata -9999/-9998."""
    f = _finite(value)
    if f is None or f in (-9999.0, -9998.0) or f < -40.0:
        return None
    return f


def _get(mapping, *path, default=None):
    cur = mapping
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def thresholds_from_config(cfg=None):
    """Default + override da config['taxonomy'] (solo valori finiti >=0)."""
    out = dict(DEFAULT_THRESHOLDS)
    if isinstance(cfg, dict):
        for key in out:
            if key in cfg:
                v = _finite(cfg[key])
                if v is not None and v >= 0:
                    out[key] = v
    return out


def field(value, *, null_reason=None, rule_support=0.0, confidence="low",
          reference=None, **extra):
    """Campo con value/null_reason/source/reference/rule_support/confidence.

    Un ``None`` senza motivo solleva ValueError (nessun buco silenzioso).
    ``rule_support`` e' supporto di regole in [0,1], NON probabilita'.
    La confidenza e' al massimo ``medium`` e NON dipende dal NWP.
    """
    support = _finite(rule_support)
    support = 0.0 if support is None else max(0.0, min(1.0, support))
    if value is None and not null_reason:
        raise ValueError("taxonomy_field_null_without_reason")
    if confidence not in ("low", "medium"):
        raise ValueError("taxonomy_confidence_out_of_range")
    out = {
        "value": value,
        "null_reason": null_reason,
        "source": SOURCE,
        "reference": reference,
        "rule_support": round(support, 3),
        "rule_support_basis": RULE_SUPPORT_BASIS,
        "confidence": confidence,
        "confidence_basis": CONFIDENCE_BASIS,
        "status": (CANDIDATE if value not in (None, UNKNOWN, [], ())
                   else UNKNOWN),
        "nwp_influence": "none",
    }
    out.update(extra)
    return out


def _support(passed, total):
    n = int(total)
    return round(min(1.0, max(0.0, int(passed) / n)) if n else 0.0, 3)


def _geometry(record):
    """Length/aspect della componente convettiva dall'outline VMI (km).

    Proiezione locale lon/lat -> km e PCA 2x2 del poligono. Nessun dato
    inventato: senza outline valido restituisce {} e i gate che richiedono
    geometria restano null.
    """
    outline = record.get("outline") if isinstance(record, dict) else None
    coords = outline.get("coordinates") if isinstance(outline, dict) else None
    if not isinstance(coords, list) or not coords:
        return {}
    ring = coords[0]
    if not isinstance(ring, list) or len(ring) < 4:
        return {}
    pts = []
    for p in ring:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return {}
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError):
            return {}
    n = len(pts)
    lat0 = sum(p[1] for p in pts) / n
    kx = 111.32 * max(math.cos(math.radians(lat0)), 0.01)
    ky = 110.57
    xy = [(lon * kx, lat * ky) for lon, lat in pts]
    cx = sum(x for x, _ in xy) / n
    cy = sum(y for _, y in xy) / n
    sxx = sum((x - cx) ** 2 for x, _ in xy) / n
    syy = sum((y - cy) ** 2 for _, y in xy) / n
    sxy = sum((x - cx) * (y - cy) for x, y in xy) / n
    if sxx + syy <= 0:
        return {}
    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    ux, uy = math.cos(theta), math.sin(theta)
    pu = [x * ux + y * uy for x, y in xy]
    pv = [-x * uy + y * ux for x, y in xy]
    length = max(pu) - min(pu)
    width = max(max(pv) - min(pv), 0.001)
    return {"length_km": round(length, 2), "width_km": round(width, 2),
            "aspect_ratio": round(length / width, 3),
            "source": "outline (maschera cella VMI, PCA 2D)"}


def _bow_metric(record, geometry):
    """Convessita' del bordo di avanguardia (0-1) o None se non misurabile.

    SCELTA INGEGNERISTICA: bordo suddiviso in 5 bande cross-track; il scoring
    confronta l'avanzamento della banda centrale con le due esterne. E' una
    misura su UN frame: l'isteresi a 2 frame e' gestita dai tag.
    """
    direction = _finite(_get(record, "motion", "direction_toward_deg"))
    outline = record.get("outline") if isinstance(record, dict) else None
    coords = outline.get("coordinates") if isinstance(outline, dict) else None
    if direction is None or not isinstance(coords, list) or not coords:
        return None
    if not geometry:
        return None
    ring = coords[0]
    pts = []
    for p in ring:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return None
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError):
            return None
    lat0 = sum(p[1] for p in pts) / len(pts)
    kx = 111.32 * max(math.cos(math.radians(lat0)), 0.01)
    xy = [(lon * kx, lat * 110.57) for lon, lat in pts]
    th = math.radians(direction)
    along = [x * math.cos(th) + y * math.sin(th) for x, y in xy]
    cross = [-x * math.sin(th) + y * math.cos(th) for x, y in xy]
    lo, hi = min(cross), max(cross)
    if hi - lo <= 0:
        return None
    bins = [[] for _ in range(5)]
    for c, a in zip(cross, along):
        idx = min(4, int(5.0 * (c - lo) / (hi - lo)))
        bins[idx].append(a)
    front = [max(b) for b in bins if b]
    if len(front) < 5:
        return None
    bulge = front[2] - (front[0] + front[4]) / 2.0
    if bulge <= 0:
        return 0.0
    return round(min(1.0, bulge / 10.0), 3)


def observation_from_record(record):
    """Osservazione radar-observed di un record (NON legge alcun campo NWP)."""
    rec = record if isinstance(record, dict) else {}
    geom = _geometry(rec)
    area = _finite(rec.get("area_km2"))
    if area is None:
        area = _finite(rec.get("outline_area_km2"))
    n_points = _finite(rec.get("n_points"))
    if n_points is None:
        n_points = _finite(rec.get("track_points"))
    obs = {
        "valid_time": (rec.get("last_seen") or rec.get("time_window")
                       or rec.get("generated_at")),
        "max_dbz": _dbz(_get(rec, "intensity", "max_dbz")),
        "mean_dbz": _dbz(_get(rec, "intensity", "mean_dbz")),
        "delta_dbz": _finite(_get(rec, "intensity", "delta_dbz")),
        "area_km2": area,
        "area_growth_pct": _finite(_get(rec, "motion", "area_growth_pct")),
        "persistence_frames": int(n_points or 0),
        "cell_count": _finite(rec.get("cell_count")),
        "length_km": geom.get("length_km"),
        "width_km": geom.get("width_km"),
        "aspect_ratio": geom.get("aspect_ratio"),
        "bow_score": _bow_metric(rec, geom),
        # I seguenti sono disponibili SOLO come evidenza esplicita multi-frame
        # passata dal chiamante: mai inferiti da un singolo record.
        "notch_depth_km": None,
        "upstream_regeneration_frames": None,
        "vs_anchor_frames": None,
        "echo_6km_dbz": None,
        "ir_image_valid": False,
        "enhanced_v_signature": None,
        "lineage_events": None,
    }
    obs["valid"] = obs["max_dbz"] is not None or obs["area_km2"] is not None
    return obs


def _observation_from_evidence(base, evidence):
    """Unisce un'osservazione esplicita ai descrittori radar del record."""
    out = dict(base)
    if isinstance(evidence, dict):
        for key, value in evidence.items():
            if key in out:
                out[key] = value
    for k in ("max_dbz", "mean_dbz", "delta_dbz", "area_km2",
              "area_growth_pct", "cell_count", "length_km", "width_km",
              "aspect_ratio", "bow_score", "notch_depth_km",
              "upstream_regeneration_frames", "vs_anchor_frames",
              "echo_6km_dbz"):
        out[k] = _finite(out.get(k))
    out["max_dbz"] = _dbz(out["max_dbz"])
    out["mean_dbz"] = _dbz(out["mean_dbz"])
    out["echo_6km_dbz"] = _dbz(out["echo_6km_dbz"])
    out["valid"] = out["max_dbz"] is not None or out["area_km2"] is not None
    return out


def raw_echo_regime(obs, th=None):
    """Regime eco candidato da UNA osservazione (nessuna conferma)."""
    t = th or DEFAULT_THRESHOLDS
    if not obs.get("valid"):
        return None, "no_valid_radar_datum", 0
    mx = obs.get("max_dbz")
    mean = obs.get("mean_dbz")
    area = obs.get("area_km2")
    if mx is None:
        return None, "max_dbz_missing", 0
    if mx < t["no_echo_max_dbz"]:
        return "no_echo", None, 2
    if mx >= t["convective_max_dbz"]:
        if area is None:
            return None, "area_missing_for_convective_gate", 1
        if area < t["convective_min_area_km2"]:
            return None, "convective_area_below_gate", 1
        if (mean is not None
                and mean < t["mixed_mean_ceiling_dbz"]):
            return "mixed", None, 3
        return "convective_like", None, 2
    if t["stratiform_min_dbz"] <= mx < t["stratiform_max_dbz"]:
        if area is None or area < t["convective_min_area_km2"]:
            return None, "stratiform_area_below_gate", 1
        return "stratiform_like", None, 2
    return None, "echo_regime_gate_not_satisfied", 0


def raw_organization_mode(obs, th=None):
    """Modalita' organizzativa candidata da UNA osservazione."""
    t = th or DEFAULT_THRESHOLDS
    if not obs.get("valid"):
        return None, "no_valid_radar_datum", 0
    length = obs.get("length_km")
    aspect = obs.get("aspect_ratio")
    area = obs.get("area_km2")
    cells = obs.get("cell_count")
    frames = int(obs.get("persistence_frames") or 0)
    if (length is not None and aspect is not None
            and length >= t["linear_min_length_km"]
            and aspect >= t["min_aspect_ratio"]
            and frames >= t["min_persistence_frames"]):
        return "linear_system_candidate", None, 5
    if (area is not None and aspect is not None
            and area >= t["areal_min_area_km2"]
            and aspect < t["min_aspect_ratio"]
            and frames >= t["min_persistence_frames"]):
        return "areal_nonlinear_system", None, 4
    if (aspect is not None and aspect >= t["min_aspect_ratio"]
            and frames >= t["min_persistence_frames"]):
        # Allineata ma senza evidenza esplicita di separazione fra segmenti:
        # NON viene etichettata "broken_line" (gate dedicato nei tag).
        return "unclassified", "linear_shape_without_component_gap_evidence", 1
    mx, mean = obs.get("max_dbz"), obs.get("mean_dbz")
    if (mx is not None and mean is not None
            and mx >= t["convective_max_dbz"]
            and t["stratiform_min_dbz"] <= mean < t["stratiform_max_dbz"]):
        # Prima dell'isolata: un core convettivo dentro eco stratiforme e'
        # una condizione piu' specifica di "cella compatta".
        return "embedded_convection", None, 2
    if cells is not None and cells >= t["cluster_min_cell_count"]:
        return "cell_cluster", None, 3
    if (cells is not None and cells <= 1 and area is not None
            and area <= t["cell_max_area_km2"]
            and (aspect is None or aspect < t["isolated_aspect_ceiling"])):
        return "isolated_cell_candidate", None, 3
    return "unclassified", "organization_gate_not_satisfied", 0


def raw_system_scale(obs, th=None):
    """Scala di sistema candidata da UNA osservazione."""
    t = th or DEFAULT_THRESHOLDS
    if not obs.get("valid"):
        return None, "no_valid_radar_datum", 0
    length = obs.get("length_km")
    aspect = obs.get("aspect_ratio")
    area = obs.get("area_km2")
    cells = obs.get("cell_count")
    frames = int(obs.get("persistence_frames") or 0)
    if (length is not None and aspect is not None
            and length >= t["mcs_min_length_km"]
            and aspect >= t["min_aspect_ratio"]
            and frames >= t["min_persistence_frames"]):
        return "mcs_candidate", None, 5
    if (area is not None and area >= t["convective_min_area_km2"] * 10
            and cells is not None and cells >= t["cluster_min_cell_count"]
            and frames >= t["min_persistence_frames"]):
        return "cluster_candidate", None, 4
    if (area is not None and t["convective_min_area_km2"] <= area
            <= t["cell_max_area_km2"]
            and frames >= t["min_persistence_frames"]):
        return "cell_candidate", None, 3
    return None, "scale_gate_not_satisfied", 0


class Hysteresis:
    """Isteresi per-entita': 2 frame consecutivi per cambiare classe.

    Uno stato con dato non valido (o gate non soddisfatto) azzera la classe:
    l'isteresi NON conserva una classe oltre l'ultimo dato radar valido.
    """

    def __init__(self, consecutive=2):
        self.consecutive = max(2, int(consecutive))
        self._state = {}

    def update(self, key, raw_value, valid_time, datum_valid=False):
        st = self._state.get(key)
        if raw_value is None:
            # Dato radar invalido e gate non soddisfatto sono motivi DIVERSI,
            # ma in entrambi i casi la classe NON viene conservata.
            reason = ("no_valid_datum_after_last_observation" if not datum_valid
                      else "gate_not_satisfied_on_last_frame")
            self._state[key] = {"class": UNKNOWN, "run_length": 0,
                                "valid_frames": 0, "valid_time": valid_time}
            return (UNKNOWN, reason,
                    {"hysteresis_frames": 0, "valid_time": valid_time,
                     "datum_valid": bool(datum_valid)})
        if st is None:
            st = {"class": UNKNOWN, "run_length": 0, "valid_frames": 0,
                  "valid_time": valid_time}
        if raw_value == st["class"]:
            st["run_length"] += 1
        else:
            st["class"] = raw_value
            st["run_length"] = 1
        st["valid_frames"] += 1
        st["valid_time"] = valid_time
        self._state[key] = st
        if st["run_length"] >= self.consecutive:
            return (st["class"], None,
                    {"hysteresis_frames": st["run_length"],
                     "valid_frames": st["valid_frames"],
                     "valid_time": valid_time, "datum_valid": True})
        return (UNKNOWN, "hysteresis_requires_2_consecutive_frames",
                {"hysteresis_frames": st["run_length"],
                 "valid_frames": st["valid_frames"],
                 "valid_time": valid_time, "datum_valid": True})


def _axis_field(raw_fn, observations, key, th, reference, total_gates):
    """Valuta un asse su tutta la sequenza e applica l'isteresi."""
    hyst = Hysteresis(th["hysteresis_consecutive_frames"])
    last = None
    last_null = None
    last_detail = {}
    raw_counts = {}
    for obs in observations:
        raw, why, passed = raw_fn(obs, th)
        last, last_null, last_detail = hyst.update(
            key, raw, obs.get("valid_time"),
            datum_valid=bool(obs.get("valid")))
        if raw is not None:
            raw_counts[raw] = raw_counts.get(raw, 0) + 1
    null_reason = last_null
    if null_reason is None and not observations:
        null_reason = "no_observation"
    support = _support(total_gates if last not in (None, UNKNOWN) else 0,
                       total_gates)
    confidence = "medium" if (last_detail.get("hysteresis_frames", 0) >= 2
                              and len(observations) >= 2
                              and observations[-1].get("max_dbz") is not None
                              and observations[-1].get("area_km2") is not None) else "low"
    f = field(last if last not in (None, UNKNOWN) else UNKNOWN,
              null_reason=null_reason, rule_support=support,
              confidence=confidence, reference=reference,
              raw_candidate_counts=raw_counts,
              valid_time=last_detail.get("valid_time"),
              hysteresis_frames=int(last_detail.get("hysteresis_frames") or 0),
              datum_valid=bool(last_detail.get("datum_valid")))
    return f


def _tag_entry(name, descriptor, obs, support, reference):
    return {
        "tag": name,
        "value": name,
        "status": CANDIDATE,
        "null_reason": None,
        "descriptor": descriptor,
        "valid_time": obs.get("valid_time"),
        "source": SOURCE,
        "reference": reference,
        "rule_support": round(max(0.0, min(1.0, support)), 3),
        "confidence": "low",
        "nwp_influence": "none",
    }


def evaluate_tags(observations, th=None, config=None):
    """Tag morfologici ed evolutivi candidati + motivi dei tag non emessi."""
    t = th or DEFAULT_THRESHOLDS
    last = observations[-1] if observations else {}
    tags, not_eval = [], []

    # bow echo: curvatura su >=2 frame + core >=40 dBZ; solo geometria.
    scores = [o.get("bow_score") for o in observations
              if o.get("bow_score") is not None]
    core = last.get("max_dbz")
    if (len(scores) >= t["bow_min_frames"]
            and all(s >= t["bow_score_gate"] for s in scores[-2:])
            and core is not None and core >= t["bow_min_core_dbz"]):
        tags.append(_tag_entry(
            "bow_echo_candidate",
            {"bow_score_last": scores[-1], "frames_measured": len(scores),
             "max_dbz": core,
             "note": "solo curvatura della VMI: nessuna inferenza su raffiche "
                     "o danno"}, last, _support(2, 2),
            REFERENCE["bow_echo_candidate"]))
    else:
        not_eval.append({"tag": "bow_echo_candidate",
                         "null_reason": "bow_curvature_lt_2_frames_or_no_core"})

    # v_notch: singolo frame, geometria locale; distinto da radar_v_shape.
    notch = _finite(last.get("notch_depth_km"))
    if notch is not None and notch >= t["notch_min_depth_km"]:
        tags.append(_tag_entry(
            "v_notch_reflectivity",
            {"notch_depth_km": notch, "frames": 1,
             "note": "solo incavo 2D su singolo frame; NON e' back-building "
                     "e NON e' enhanced-V"}, last, _support(1, 1),
            REFERENCE["v_notch_reflectivity"]))
    else:
        not_eval.append({"tag": "v_notch_reflectivity",
                         "null_reason": "v_notch_not_measured"})

    # radar_v_shape: rigenerazione retrograda su >=3 frame, vertice su >=2,
    # eco profonda confermata CAPPI a 6 km.
    ups = [o for o in observations
           if int(o.get("upstream_regeneration_frames") or 0)
           >= t["vs_shape_min_upstream_frames"]]
    anchors = [o for o in observations
               if int(o.get("vs_anchor_frames") or 0)
               >= t["vs_shape_min_frames"]]
    echo6 = last.get("echo_6km_dbz")
    if (len(ups) >= t["vs_shape_min_frames"] and anchors
            and echo6 is not None
            and echo6 >= t["vs_shape_min_echo_6km_dbz"]):
        tags.append(_tag_entry(
            "radar_v_shape",
            {"upstream_frames": len(ups), "anchor_frames": len(anchors),
             "echo_6km_dbz": echo6,
             "note": "rigenerazione a monte + CAPPI 6 km: NON implica "
                     "pioggia estrema senza centraline"}, last,
            _support(3, 3), REFERENCE["radar_v_shape"]))
    else:
        not_eval.append({
            "tag": "radar_v_shape",
            "null_reason": ("v_shape_requires_multiframe_upstream_regeneration_"
                            "and_6km_cappi_echo")})

    # enhanced_V_IR: SOLO con immagine IR valida (oggi assente).
    if last.get("ir_image_valid") is True and last.get("enhanced_v_signature") is True:
        tags.append(_tag_entry(
            "enhanced_V_IR",
            {"ir_image_valid": True, "signature": True,
             "note": "firma termica IR: campo SEPARATO da radar_v_shape"},
            last, _support(1, 1), REFERENCE["enhanced_V_IR"]))
    else:
        not_eval.append({"tag": "enhanced_V_IR",
                         "null_reason": "ir_image_not_available"})

    # split / merge: eventi di lineage espliciti su >=2 frame.
    for name, event in (("split_candidate", "split"), ("merge_candidate", "merge")):
        hits = [o for o in observations
                if event in (o.get("lineage_events") or [])]
        if len(hits) >= t["lineage_min_frames"]:
            tags.append(_tag_entry(
                name, {"lineage_event": event, "frames": len(hits)},
                last, _support(len(hits), t["lineage_min_frames"]),
                REFERENCE["evolution"]))
        else:
            not_eval.append({"tag": name,
                             "null_reason": event + "_events_lt_2_frames"})

    evo_tags, evo_not_eval = evaluate_evolution(observations, th)
    return tags, evo_tags, not_eval + evo_not_eval


def evaluate_evolution(observations, th=None):
    """Lista di tag di evoluzione candidati su >=2 frame consecutivi."""
    t = th or DEFAULT_THRESHOLDS
    obs = [o for o in observations
           if o.get("delta_dbz") is not None
           or o.get("area_growth_pct") is not None]
    if len(obs) < t["evolution_min_frames"]:
        return [], [{"tag": "evolution_stage",
                     "null_reason": "insufficient_consecutive_evolution_observations"}]
    out = []
    for name in EVOLUTION_TAG_NAMES:
        hits = []
        for o in obs:
            d = o.get("delta_dbz")
            g = o.get("area_growth_pct")
            if name == "intensification_candidate":
                ok = ((d is not None and d >= t["intensify_delta_dbz"])
                      or (g is not None and g >= t["intensify_growth_pct"]))
            elif name == "dissipation_candidate":
                ok = ((d is not None and d <= t["dissipate_delta_dbz"])
                      or (g is not None and g <= t["dissipate_growth_pct"]))
            else:
                ok = (d is not None and g is not None
                      and abs(d) < t["intensify_delta_dbz"]
                      and abs(g) < t["intensify_growth_pct"])
            if ok:
                hits.append(o)
        if len(hits) >= t["evolution_min_frames"]:
            out.append(_tag_entry(
                name, {"frames": len(hits),
                       "delta_dbz_last": obs[-1].get("delta_dbz"),
                       "area_growth_pct_last": obs[-1].get("area_growth_pct")},
                obs[-1], _support(len(hits), t["evolution_min_frames"]),
                REFERENCE["evolution"]))
    if not out:
        return [], [{"tag": "evolution_stage",
                     "null_reason": "evolution_gate_not_satisfied_on_2_frames"}]
    return out, []


def annotate_records(records, *, frame_observations=None, config=None):
    """Aggiunge ``record['taxonomy']`` (schema .../1) a ogni record.

    ``frame_observations``: dizionario opzionale
    ``{structure_id: [obs_frame_1, obs_frame_2, ...]}`` con evidenze radar
    esplicite multi-frame. Se assente si usa una sola osservazione per record:
    l'isteresi lascia allora le classi a ``unknown`` con motivo dichiarato
    (nessuna promozione da un singolo frame).
    Ritorna ``(records, summary)``.
    """
    cfg = config.get("taxonomy") if isinstance(config, dict) else None
    th = thresholds_from_config(cfg)
    evidence_map = frame_observations if isinstance(frame_observations, dict) else {}
    recs = list(records or [])
    summary = {
        "schema": SCHEMA,
        "version": TAXONOMY_VERSION,
        "source": SOURCE,
        "structures_total": len(recs),
        "echo_regime_counts": {k: 0 for k in ECHO_REGIMES},
        "organization_mode_counts": {k: 0 for k in ORGANIZATION_MODES},
        "system_scale_counts": {k: 0 for k in SYSTEM_SCALES},
        "morphology_tag_counts": {},
        "evolution_tag_counts": {},
        "unknown_by_axis": {k: 0 for k in LOGICAL_VARIABLES},
        "null_reason_counts": {},
        "confirmed_count": 0,
        "nwp_influence": "none",
        "rule_support_is_probability": False,
        "reference": dict(REFERENCE),
    }
    for rec in recs:
        if not isinstance(rec, dict):
            continue
        key = rec.get("structure_id") or rec.get("system_key") or id(rec)
        base = observation_from_record(rec)
        evs = evidence_map.get(key)
        observations = ([_observation_from_evidence(base, e) for e in evs]
                        if isinstance(evs, list) and evs else [base])
        echo = _axis_field(raw_echo_regime, observations,
                           "echo:" + str(key), th, REFERENCE["convective"], 3)
        org = _axis_field(raw_organization_mode, observations,
                          "org:" + str(key), th, REFERENCE["linear_system"], 5)
        scale = _axis_field(raw_system_scale, observations,
                            "scale:" + str(key), th, REFERENCE["mcs"], 5)
        tags, evo_tags, not_eval = evaluate_tags(observations, th, cfg)
        ir_null = next((d["null_reason"] for d in not_eval
                        if d["tag"] == "enhanced_V_IR"), None)
        ir_field = field(
            "enhanced_V_IR" if not ir_null else None,
            null_reason=ir_null,
            rule_support=1.0 if not ir_null else 0.0,
            confidence="low", reference=REFERENCE["enhanced_V_IR"],
            note=("firma IR separata da radar_v_shape; nessuna soglia IR "
                  "verificata in questo modulo"))
        tag_names = [d["tag"] for d in tags]
        evo_names = [d["tag"] for d in evo_tags]
        taxonomy = {
            "schema": SCHEMA,
            "version": TAXONOMY_VERSION,
            "status": CANDIDATE,
            "confirmed": False,
            "nwp_influence": "none",
            "rule_support_is_probability": False,
            "echo_regime": echo,
            "organization_mode": org,
            "system_scale": scale,
            "morphology_tags": field(
                tag_names, null_reason=(None if tag_names
                                        else "no_qualifying_morphology_tag"),
                rule_support=(max([d["rule_support"] for d in tags], default=0.0)),
                confidence="low", reference=REFERENCE["bow_echo_candidate"],
                tags=tags, not_evaluated=not_eval),
            "evolution_stage": field(
                evo_names, null_reason=(None if evo_names
                                        else "no_qualifying_evolution_tag"),
                rule_support=(max([d["rule_support"] for d in evo_tags], default=0.0)),
                confidence="low", reference=REFERENCE["evolution"],
                tags=evo_tags),
            "enhanced_V_IR": ir_field,
            "observation_count": len(observations),
            "hysteresis_consecutive_frames": th["hysteresis_consecutive_frames"],
            "nwp_fields_ignored": list(NWP_FIELDS_IGNORED),
        }
        rec["taxonomy"] = taxonomy

        for axis, f in (("echo_regime", echo),
                        ("organization_mode", org),
                        ("system_scale", scale)):
            v = f["value"]
            counts_key = {"echo_regime": "echo_regime_counts",
                          "organization_mode": "organization_mode_counts",
                          "system_scale": "system_scale_counts"}[axis]
            if isinstance(v, str):
                # Partizione esatta: uno stato "unknown" (non classe) viene
                # conteggiato a parte con la stessa chiave, mai saltato.
                counts = summary[counts_key]
                counts[v] = counts.get(v, 0) + 1
            if v == UNKNOWN or v is None:
                summary["unknown_by_axis"][axis] += 1
            if f.get("null_reason"):
                r = f["null_reason"]
                summary["null_reason_counts"][r] = (
                    summary["null_reason_counts"].get(r, 0) + 1)
        for d in tags:
            summary["morphology_tag_counts"][d["tag"]] = (
                summary["morphology_tag_counts"].get(d["tag"], 0) + 1)
        for d in evo_tags:
            summary["evolution_tag_counts"][d["tag"]] = (
                summary["evolution_tag_counts"].get(d["tag"], 0) + 1)
        if not tag_names:
            summary["unknown_by_axis"]["morphology_tags"] += 1
        if not evo_names:
            summary["unknown_by_axis"]["evolution_stage"] += 1
        if ir_null:
            summary["unknown_by_axis"]["enhanced_V_IR"] += 1
            summary["null_reason_counts"][ir_null] = (
                summary["null_reason_counts"].get(ir_null, 0) + 1)
    n = max(1, len(recs))
    summary["unknown_rate_echo_regime"] = round(
        summary["unknown_by_axis"]["echo_regime"] / n, 4)
    summary["unknown_rate_all_axes"] = round(
        sum(summary["unknown_by_axis"].values())
        / (n * len(LOGICAL_VARIABLES)), 4)
    return recs, summary


def attach(bundle, config=None):
    """Wiring additivo: scrive i campi SOLO se ``config['taxonomy']['enabled']``.

    Nessun campo esistente viene toccato. Ritorna ``None`` se disabilitato.
    """
    cfg = config.get("taxonomy") if isinstance(config, dict) else None
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return None
    structures = getattr(bundle, "convective_structures", None) or []
    evidence = getattr(bundle, "taxonomy_frame_evidence", None)
    _, summary = annotate_records(
        structures, frame_observations=evidence, config=config)
    bundle.taxonomy_summary = summary
    return structures, summary
