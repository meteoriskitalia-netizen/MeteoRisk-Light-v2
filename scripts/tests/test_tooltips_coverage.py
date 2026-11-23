#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""COPERTURA DEI TOOLTIP (release 1.3.0.57).

Ogni controllo interattivo dell'app deve avere un `title` non vuoto (>= 6
caratteri, non un segnaposto). Il test legge l'html attivo (APP_FILE di
VERSION, oppure il percorso passato da riga di comando) e verifica:

  (1) tag SEMPRE obbligati: button, select, summary, textarea, a[href],
      input[type=range|text|search|password], [role=button] e qualunque tag
      con onclick/onchange/oninput;
  (2) `input[type=checkbox]` e' accettato se sta dentro una <label title="...">,
      che e' il pattern dei .mp-switch dell'app;
  (3) allowlist MOTIVATA: #legacy-toolbar-row e #legacy-metric-tabs (solo se
      hanno display:none nello style, quindi non raggiungibili) e #easter-egg;
  (4) anche le stringhe JS che generano `<button ...>` devono portare `title=`.

LIMITI DICHIARATI: il test e' STATICO. Non esegue il JS, quindi non vede i
tooltip aggiunti a runtime da mriTip() (righe candidati, giorni 3-giorni,
ricerca, retry ensemble, intestazioni legenda): quei punti sono coperti dai
test .mjs e dalla verifica manuale, non da questo file. Non misura il
rendering del browser (un title puo' esistere e non essere leggibile).
"""
import io
import os
import re
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIN_LEN = 6
PLACEHOLDERS = {u'\u00b7', u'\u2026', '-', '...'}
ALLOW_IDS = {
    'easter-egg': 'firma/easter egg nel footer: non e\' un controllo',
}
ALLOW_DISPLAY_NONE = {
    'legacy-toolbar-row': 'toolbar storica dismessa, display:none !important',
    'legacy-metric-tabs': 'linguette metrica storiche, display:none (la scelta passa da #metric-select)',
}
VOID = {'input', 'br', 'img', 'meta', 'link', 'hr', 'source', 'area', 'base', 'col', 'embed', 'param', 'track', 'wbr'}


def app_file(use_argv=True):
    if use_argv and len(sys.argv) > 1 and sys.argv[1].endswith('.html'):
        return sys.argv[1]
    src = io.open(os.path.join(ROOT, 'VERSION'), encoding='utf-8').read()
    m = re.search(r'^APP_FILE=(.+)$', src, re.M)
    if not m:
        sys.exit('FATAL: APP_FILE non trovato in VERSION')
    return os.path.join(ROOT, m.group(1).strip())


def good(title):
    t = (title or '').strip()
    return len(t) >= MIN_LEN and t not in PLACEHOLDERS


class Scan(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack = []
        self.bad = []
        self.checked = 0

    def selector(self, tag, a):
        sel = tag
        if a.get('id'):
            sel += '#' + a['id']
        elif a.get('class'):
            sel += '.' + '.'.join(a['class'].split()[:2])
        elif a.get('role'):
            sel += '[role=%s]' % a['role']
        return sel

    def allow(self, tag, a):
        if a.get('id') in ALLOW_IDS:
            return True
        for anc_tag, anc in self.stack:
            anc = dict(anc)
            aid = anc.get('id') or ''
            style = (anc.get('style') or '').replace(' ', '').lower()
            if aid in ALLOW_DISPLAY_NONE and 'display:none' in style:
                return True
        return False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.allow(tag, a):
            if tag not in VOID:
                self.stack.append((tag, attrs))
            return
        need = tag in ('button', 'select', 'summary', 'textarea')
        if tag == 'a' and a.get('href') is not None:
            need = True
        if tag == 'input' and (a.get('type') or '').lower() in ('range', 'text', 'search', 'password', 'checkbox'):
            need = True
        if a.get('role') == 'button':
            need = True
        if any(k in a for k in ('onclick', 'onchange', 'oninput')):
            need = True
        if need:
            self.checked += 1
            if good(a.get('title')):
                pass
            elif tag == 'input' and (a.get('type') or '').lower() == 'checkbox':
                lbl = None
                for anc_tag, anc in self.stack[::-1]:
                    if anc_tag == 'label':
                        lbl = dict(anc)
                        break
                if not (lbl is not None and good(lbl.get('title'))):
                    self.bad.append((self.getpos()[0], self.selector(tag, a), 'checkbox fuori da label[title]'))
            else:
                self.bad.append((self.getpos()[0], self.selector(tag, a),
                                 'title assente o troppo corto: %r' % (a.get('title') or '')))
        if tag not in VOID:
            self.stack.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag and tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def generated_buttons(html):
    """Stringhe JS che costruiscono <button ...> senza title=."""
    out = []
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.S):
        block = m.group(1)
        base = html[:m.start(1)].count('\n') + 1
        block = re.sub(r'/\*.*?\*/', '', block, flags=re.S)
        block = re.sub(r'//[^\r\n]*', '', block)
        for b in re.finditer(r'<button\b[^>]*>', block, re.I):
            if 'title=' not in b.group(0):
                line = base + block[:b.start()].count('\n')
                out.append((line, b.group(0)[:70]))
    return out


def main():
    path = app_file()
    if not os.path.exists(path):
        sys.exit('FATAL: file non trovato: ' + path)
    html = io.open(path, encoding='utf-8', newline='').read()
    scan = Scan()
    scan.feed(html)
    scan.close()
    js = generated_buttons(html)
    name = os.path.basename(path)
    print('file misurato: %s' % name)
    print('controlli obbligati: %d' % scan.checked)
    for line, sel, why in scan.bad:
        print('%s:%d %s -- %s' % (name, line, sel, why))
    for line, frag in js:
        print('%s:%d <button generato in JS> -- manca title= : %s' % (name, line, frag))
    total = len(scan.bad) + len(js)
    print('ALLOWLIST: ' + '; '.join('%s (%s)' % (k, v) for k, v in ALLOW_DISPLAY_NONE.items()))
    if total:
        print('RESULT: FAIL (%d controlli senza tooltip)' % total)
        return 1
    print('RESULT: PASS (0 controlli senza tooltip)')
    return 0


def test_tooltips_have_title():
    """Wrapper pytest: stesso scan del CLI, senza leggere sys.argv."""
    path = app_file(use_argv=False)
    html = io.open(path, encoding='utf-8', newline='').read()
    scan = Scan()
    scan.feed(html)
    scan.close()
    bad = scan.bad + generated_buttons(html)
    detail = '\n'.join('%s:%d %s' % (os.path.basename(path), line, sel) for line, sel, why in scan.bad)
    detail += '\n'.join('%s:%d <button generato in JS>' % (os.path.basename(path), line) for line, frag in generated_buttons(html))
    assert not bad, 'controlli senza tooltip:\n' + detail


SIMPLE_HTML = os.path.join(ROOT, 'simple', 'index.html')


def generated_role_buttons(html):
    """Elementi interattivi generati in JS con role=button/option SENZA title.

    La Simple costruisce cosi' i giorni della striscia a 7 giorni
    (<div role="button">) e le righe della ricerca comune
    (<button role="option">). `generated_buttons` da solo non li vede perche'
    cerca solo `<button`, quindi il controllo equivalente per la Simple li
    include esplicitamente.
    """
    out = []
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.S):
        block = m.group(1)
        base = html[:m.start(1)].count('\n') + 1
        block = re.sub(r'/\*.*?\*/', '', block, flags=re.S)
        block = re.sub(r'//[^\r\n]*', '', block)
        pattern = r'<(?:div|span|li|button)\b[^>]*\brole="(?:button|option)"[^>]*>'
        for b in re.finditer(pattern, block, re.I):
            if 'title=' not in b.group(0):
                out.append((base + block[:b.start()].count('\n'), b.group(0)[:80]))
    return out


def test_simple_tooltips_have_title():
    """Controllo equivalente a `test_tooltips_have_title` per simple/index.html.

    LIMITE DICHIARATO: statico come il gemello sulla Pro. Non esegue il JS, non
    misura il rendering. Copre in piu' gli elementi generati con role=button o
    role=option (giorni della striscia e risultati della ricerca).
    """
    assert os.path.exists(SIMPLE_HTML), 'simple/index.html mancante'
    html = io.open(SIMPLE_HTML, encoding='utf-8', newline='').read()
    scan = Scan()
    scan.feed(html)
    scan.close()
    js = generated_buttons(html)
    roles = generated_role_buttons(html)
    bad = scan.bad + js + roles
    detail = ''.join('simple/index.html:%d %s -- %s\n' % (line, sel, why)
                     for line, sel, why in scan.bad)
    detail += ''.join('simple/index.html:%d <button generato in JS> -- manca title=\n'
                      % line for line, _frag in js)
    detail += ''.join('simple/index.html:%d <elemento role=button/option generato> -- manca title=\n'
                      % line for line, _frag in roles)
    assert not bad, 'controlli senza tooltip nella Simple:\n' + detail


if __name__ == '__main__':
    sys.exit(main())
