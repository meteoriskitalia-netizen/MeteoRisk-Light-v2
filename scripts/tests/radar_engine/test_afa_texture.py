# -*- coding: utf-8 -*-
"""1.3.0.55 - AFA CON CROCETTE: la texture di lettura non cambia la forma.

Il test genera con Pillow (la stessa libreria del motore) un PNG sintetico con
una macchia opaca su fondo trasparente, applica `apply_afa_texture` /
`afa_to_webp` e misura:
  - la maschera alpha e' IDENTICA pixel per pixel (nessun trasparente diventa
    attivo, nessun attivo diventa trasparente, nessun buco viene riempito);
  - il conteggio dei pixel attivi di `afa_frame_stats` non cambia;
  - la texture CAMBIA alcuni pixel ATTIVI (pixel scuriti > 0) e nessun pixel
    trasparente;
  - un frame completamente trasparente resta bit-identico (0 crocette).

Nessun mock: sono immagini reali, il prodotto e' il WebP che il motore pubblica.
"""

import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import satellite_engine as SE  # noqa: E402


def _blob_png(size=(96, 96), box=(20, 20, 76, 76), color=(250, 240, 150, 230)):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    img.paste(color, box)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _rgba(body):
    with Image.open(io.BytesIO(body)) as src:
        return src.convert("RGBA")


def test_texture_keeps_alpha_pixel_per_pixel():
    before = _rgba(_blob_png())
    after = SE.apply_afa_texture(before)
    assert after.size == before.size
    assert after.mode == "RGBA"
    assert list(after.getchannel("A").getdata()) == list(before.getchannel("A").getdata())


def test_texture_changes_only_active_pixels():
    before = _rgba(_blob_png())
    after = SE.apply_afa_texture(before)
    b = list(before.getdata())
    a = list(after.getdata())
    changed = [i for i in range(len(b)) if a[i][:3] != b[i][:3]]
    assert changed, "la texture non ha cambiato nessun pixel: crocette assenti"
    # Ogni pixel toccato era gia' attivo: i trasparenti restano trasparenti.
    assert all(b[i][3] > 0 for i in changed)
    darker = [i for i in changed if sum(a[i][:3]) < sum(b[i][:3])]
    assert darker, "nessun pixel scurito: la texture non e' scura"


def test_texture_does_not_fill_transparent_holes():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    # Anello: un buco trasparente circondato da pixel attivi.
    for x in range(10, 54):
        img.putpixel((x, 10), (250, 240, 150, 230))
        img.putpixel((x, 53), (250, 240, 150, 230))
    for y in range(10, 54):
        img.putpixel((10, y), (250, 240, 150, 230))
        img.putpixel((53, y), (250, 240, 150, 230))
    after = SE.apply_afa_texture(img)
    assert after.getpixel((32, 32))[3] == 0, "il buco trasparente e' stato riempito"
    assert list(after.getchannel("A").getdata()) == list(img.getchannel("A").getdata())


def test_transparent_frame_is_untouched():
    body = _blob_png(box=(0, 0, 0, 0), color=(0, 0, 0, 0))
    before = _rgba(body)
    after = SE.apply_afa_texture(before)
    assert list(after.getdata()) == list(before.getdata())


def test_afa_to_webp_preserves_active_pixels_and_adds_dark_crosses():
    body = _blob_png()
    webp = SE.afa_to_webp(body)
    assert SE.is_webp(webp), "il frame AFA non e' un WebP"
    active_before, has_before = SE.afa_frame_stats(body)
    active_after, has_after = SE.afa_frame_stats(webp)
    assert has_before is True and has_after is True
    assert active_after == active_before, (active_before, active_after)
    img = _rgba(webp)
    dark = [p for p in img.getdata()
            if p[3] > 0 and p[0] < 80 and p[1] < 80 and p[2] < 90]
    assert len(dark) > 0, "nessun pixel scuro nel WebP: texture persa nell'encode"
    # E i pixel trasparenti del WebP non sono diventati attivi.
    transparent = [p for p in img.getdata() if p[3] == 0]
    assert transparent, "il frame WebP non ha piu' pixel trasparenti"


def test_texture_version_constant_is_exposed():
    # Il manifest porta texture_version: serve per ri-processare gli slot vecchi.
    assert isinstance(SE.AFA_TEXTURE_VERSION, int) and SE.AFA_TEXTURE_VERSION >= 1
    assert SE.AFA_TEXTURE_STEP_PX >= 8
