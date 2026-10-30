# -*- coding: utf-8 -*-
"""Scale divergenti per le mappe di anomalia in ``color_scales.json``.

Perche' esiste: le tre scale ``*_anomaly`` sono un PRODOTTO, non un dato: se
spariscono dal JSON (merge, rigenerazione, copia stantia) il renderer non ha
piu' una tavolozza da usare e la mappa di anomalia esce senza colori, senza che
nulla fallisca. Questo test le fissa: chiavi, breakpoint, un colore per
breakpoint, tutte le bande OPACHE e le bande vicine distinguibili.

Convenzioni del file, MISURATE su ``color_scales.json`` (non assunte):
  * ogni scala e' ``{"dark": {...}, "light": {...}}`` e ogni variante ha le sei
    chiavi ``type``/``unit``/``min``/``max``/``breakpoints``/``colors``;
  * ``type`` e' sempre ``"breakpoint"`` e ``min``/``max`` sempre ``null``;
  * ``len(colors) == len(breakpoints)`` in tutte e 35 le scale preesistenti;
  * il canale alpha e' un float ``0..1``: il renderer fa
    ``alpha_uint8 = rint(colors[:, 3] * 255)`` (``make_lookup``), quindi
    ``1`` = 255 = banda opaca.

Il contrasto si misura in CIEDE2000. L'implementazione e' verificata contro i
vettori di riferimento di Sharma, Wu & Dalal (2005) nello stesso file: senza
quel controllo un ``delta_e`` sbagliato potrebbe far passare le soglie.
"""
import json
import math
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCALES_PATH = os.path.join(ROOT, "scripts", "forecast_v2", "color_scales.json")

# Chiave -> (unita', breakpoint attesi). I colori sono fissati dal test di
# contrasto e di luminanza, non da una tabella copiata: qui si fissa il
# SIGNIFICATO della scala (dove sta lo zero e quanto e' fitta).
EXPECTED = {
    "temperature_2m_anomaly": (
        "\u00b0C (anomalia)",
        [-12, -9, -7, -5, -3, -2, -1, 0, 1, 2, 3, 5, 7, 9, 12],
    ),
    "geopotential_height_500hPa_anomaly": (
        "dam (anomalia)",
        [-24, -18, -14, -10, -6, -3, 0, 3, 6, 10, 14, 18, 24],
    ),
    "pressure_msl_anomaly": (
        "hPa (anomalia)",
        [-20, -15, -11, -8, -5, -3, -1, 0, 1, 3, 5, 8, 11, 15, 20],
    ),
}

# Soglie di prodotto (scelte, non ereditate da una norma).
MIN_DE_ADJACENT_TRIO = 10.0   # banda -1 / 0 / +1: devono leggersi come 3 bande
MIN_DE_WHITE_VS_EXTREME = 40.0  # il bianco a zero non e' un estremo


# --------------------------------------------------------------------------- #
# CIEDE2000 (sRGB -> CIELAB -> delta E), senza dipendenze esterne
# --------------------------------------------------------------------------- #
XN, YN, ZN = 95.047, 100.000, 108.883


def _srgb_to_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb):
    r, g, b = (_srgb_to_lin(float(v)) for v in rgb[:3])
    X = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) * 100.0 / XN
    Y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750) * 100.0 / YN
    Z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) * 100.0 / ZN

    def f(t):
        return t ** (1.0 / 3.0) if t > 216.0 / 24389.0 else (841.0 / 108.0) * t + 4.0 / 29.0

    fx, fy, fz = f(X), f(Y), f(Z)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def delta_e2000(lab1, lab2):
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    Cb = (C1 + C2) / 2.0
    G = 0.5 * (1.0 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25.0 ** 7))) if Cb > 0 else 0.5
    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = math.hypot(a1p, b1)
    C2p = math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360.0 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360.0 if (a2p or b2) else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dHp = 2.0 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2.0)
    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2.0
    elif (h1p + h2p) < 360:
        hbp = (h1p + h2p + 360.0) / 2.0
    else:
        hbp = (h1p + h2p - 360.0) / 2.0
    T = (1.0 - 0.17 * math.cos(math.radians(hbp - 30.0))
         + 0.24 * math.cos(math.radians(2.0 * hbp))
         + 0.32 * math.cos(math.radians(3.0 * hbp + 6.0))
         - 0.20 * math.cos(math.radians(4.0 * hbp - 63.0)))
    dtheta = 30.0 * math.exp(-(((hbp - 275.0) / 25.0) ** 2))
    RC = 2.0 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7)) if Cbp > 0 else 0.0
    SL = 1.0 + (0.015 * (Lbp - 50.0) ** 2) / math.sqrt(20.0 + (Lbp - 50.0) ** 2)
    SC = 1.0 + 0.045 * Cbp
    SH = 1.0 + 0.015 * Cbp * T
    RT = -math.sin(math.radians(2.0 * dtheta)) * RC
    return math.sqrt((dLp / SL) ** 2 + (dCp / SC) ** 2 + (dHp / SH) ** 2
                     + RT * (dCp / SC) * (dHp / SH))


def test_ciede2000_contro_i_riferimenti_sharma():
    """Se il metro e' sbagliato il contrasto non dice nulla: si verifica prima."""
    cases = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
        ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
        ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
        ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
        ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0011), 7.2195),
    ]
    for lab1, lab2, expected in cases:
        got = delta_e2000(lab1, lab2)
        assert abs(got - expected) < 1e-3, (
            "CIEDE2000 fuori riferimento: %.4f != %.4f per %s/%s"
            % (got, expected, lab1, lab2))


# --------------------------------------------------------------------------- #
# Caricamento
# --------------------------------------------------------------------------- #
def _scales():
    assert os.path.exists(SCALES_PATH), "color_scales.json non trovato: %s" % SCALES_PATH
    with open(SCALES_PATH, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    scales = data.get("scales")
    assert isinstance(scales, dict) and scales, "color_scales.json: 'scales' mancante/vuoto"
    return scales


def _variant(scales, key, theme):
    """Variante ``theme`` della scala; fallisce con un messaggio utile se manca."""
    assert key in scales, "scala %r assente da color_scales.json" % key
    entry = scales[key]
    assert isinstance(entry, dict), "scala %r non e' un oggetto" % key
    assert theme in entry, "scala %r senza variante %r" % (key, theme)
    return entry[theme]


# --------------------------------------------------------------------------- #
# Le tre scale
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_scala_presente_con_le_due_varianti(key):
    scales = _scales()
    for theme in ("dark", "light"):
        v = _variant(scales, key, theme)
        assert isinstance(v, dict)
        assert isinstance(v.get("breakpoints"), list) and v["breakpoints"]
        assert isinstance(v.get("colors"), list) and v["colors"]


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_breakpoints(key):
    unit, expected_bps = EXPECTED[key]
    for theme in ("dark", "light"):
        v = _variant(_scales(), key, theme)
        assert v["breakpoints"] == expected_bps, (
            "%s/%s: breakpoint diversi da quelli dichiarati" % (key, theme))
        assert 0 in v["breakpoints"], "%s/%s: manca lo zero" % (key, theme)
        assert v["breakpoints"] == sorted(v["breakpoints"])
        assert len(set(v["breakpoints"])) == len(v["breakpoints"]), (
            "%s/%s: breakpoint duplicati" % (key, theme))


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_un_colore_per_breakpoint_e_bande_opache(key):
    unit, expected_bps = EXPECTED[key]
    for theme in ("dark", "light"):
        v = _variant(_scales(), key, theme)
        bps, cols = v["breakpoints"], v["colors"]
        assert len(cols) == len(bps), (
            "%s/%s: %d colori per %d breakpoint" % (key, theme, len(cols), len(bps)))
        assert len(cols) == len(expected_bps)
        for i, c in enumerate(cols):
            assert isinstance(c, list) and len(c) == 4, (
                "%s/%s: colore %d non e' [r,g,b,a]" % (key, theme, i))
            assert all(isinstance(x, (int, float)) for x in c)
            assert all(0 <= x <= 255 for x in c[:3]), (
                "%s/%s: componente RGB fuori 0..255 nel colore %d" % (key, theme, i))
            # alpha del file e' 0..1 (renderer: rint(a * 255)); 1 == 255 == opaco
            assert c[3] == 1, (
                "%s/%s: banda %d non opaca (alpha=%r): l'anomalia deve coprire "
                "il dominio, una banda trasparente sembra un buco" % (key, theme, i, c[3]))


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_campi_come_le_altre_scale(key):
    """Le sei chiavi e i valori di ``type``/``min``/``max`` come le 35 esistenti."""
    scales = _scales()
    for theme in ("dark", "light"):
        v = _variant(scales, key, theme)
        assert list(v.keys()) == ["type", "unit", "min", "max", "breakpoints", "colors"], (
            "%s/%s: chiavi %r" % (key, theme, list(v.keys())))
        assert v["type"] == "breakpoint"
        assert v["min"] is None and v["max"] is None
        assert v["unit"] == EXPECTED[key][0]


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_bianco_a_zero_e_lati_divergenti(key):
    """Zero = bianco puro, lati blu (negativo) e rosso (positivo)."""
    for theme in ("dark", "light"):
        v = _variant(_scales(), key, theme)
        z = v["breakpoints"].index(0)
        zero = tuple(v["colors"][z][:3])
        neg = tuple(v["colors"][0][:3])
        pos = tuple(v["colors"][-1][:3])
        assert zero == (255, 255, 255), "%s/%s: lo zero non e' bianco: %r" % (key, theme, zero)
        lab_neg, lab_pos = rgb_to_lab(neg), rgb_to_lab(pos)
        assert lab_neg[2] < -20, "estremo negativo non blu (b*=%.1f)" % lab_neg[2]
        assert lab_pos[0] > lab_neg[0], "estremo positivo non piu' chiaro del blu profondo"
        assert lab_pos[1] > 20, "estremo positivo non rosso (a*=%.1f)" % lab_pos[1]


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_contrasto_bande_adiacenti(key):
    """-1 / 0 / +1 distinguibili, e il bianco a zero lontano dai due estremi."""
    for theme in ("dark", "light"):
        v = _variant(_scales(), key, theme)
        bps, cols = v["breakpoints"], v["colors"]
        z = bps.index(0)
        labs = [rgb_to_lab(c) for c in cols]
        de_neg = delta_e2000(labs[z - 1], labs[z])
        de_pos = delta_e2000(labs[z], labs[z + 1])
        assert de_neg >= MIN_DE_ADJACENT_TRIO, (
            "%s/%s: dE(banda %s, 0) = %.2f < %.1f"
            % (key, theme, bps[z - 1], de_neg, MIN_DE_ADJACENT_TRIO))
        assert de_pos >= MIN_DE_ADJACENT_TRIO, (
            "%s/%s: dE(0, banda %s) = %.2f < %.1f"
            % (key, theme, bps[z + 1], de_pos, MIN_DE_ADJACENT_TRIO))
        assert delta_e2000(labs[0], labs[z]) >= MIN_DE_WHITE_VS_EXTREME
        assert delta_e2000(labs[-1], labs[z]) >= MIN_DE_WHITE_VS_EXTREME
        # nessuna coppia di bande adiacenti coincide (un colore ripetuto e' un
        # gradino che sparisce dalla mappa)
        for i in range(len(labs) - 1):
            assert delta_e2000(labs[i], labs[i + 1]) >= 5.0, (
                "%s/%s: bande %s e %s quasi identiche (dE=%.2f)"
                % (key, theme, bps[i], bps[i + 1], delta_e2000(labs[i], labs[i + 1])))


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_luminanza_monotona_verso_il_bianco(key):
    """Da ogni estremo al bianco la L* sale (o resta uguale) senza inversioni."""
    for theme in ("dark", "light"):
        v = _variant(_scales(), key, theme)
        z = v["breakpoints"].index(0)
        labs = [rgb_to_lab(c) for c in v["colors"]]
        neg = [labs[i][0] for i in range(z, -1, -1)]     # bianco -> estremo blu
        pos = [labs[i][0] for i in range(z, len(labs))]  # bianco -> estremo rosso
        for side, name in ((neg, "negativo"), (pos, "positivo")):
            for i in range(len(side) - 1):
                assert side[i] > side[i + 1], (
                    "%s/%s: luminanza non decrescente verso il lato %s "
                    "(L*=%.2f poi %.2f)" % (key, theme, name, side[i], side[i + 1]))
