"""Guardia: il merge dei WebP e' un OVERLAY e il render e' incrementale.

PERCHE' ESISTE (difetto reale, evitato prima del rilascio). Nel job
`render-merge` dei 5 workflow per-fornitore il merge sostituiva la directory
`<regione>/<dominio>` con lo shard (`rm -rf` + `cp -a`) e registrava l'indice
con `git add -A`. Finchè lo shard conteneva la run INTERA funzionava. Con
`--existing-list` (render incrementale) lo shard contiene SOLO il delta, e
`git add -A` registra come CANCELLAZIONI i WebP che sono nell'indice (portati
da `read-tree --prefix` del tree del branch) e non esistono nello shard: i
frame gia' pubblicati sparirebbero dal branch, e il renderer non puo'
impedirlo perche' non li ha su disco.

Questo test e' testuale di proposito: non serve PyYAML e non si rompe se il
workflow viene riformattato, ma scatta se torna uno dei due pattern che
cancellano i WebP pubblicati.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF = os.path.join(ROOT, ".github", "workflows")

PROVIDERS = (
    "forecast-maps-dwd.yml",
    "forecast-maps-dwdicon.yml",
    "forecast-maps-ecmwf.yml",
    "forecast-maps-ncep.yml",
    "forecast-maps-meteofrance.yml",
)

# Il blocco incrementale del job `engine`: identico nei 5 a meno del dominio,
# che nel workflow e' sempre `${{ matrix.domain }}`.
INCR_START = "          # RENDER INCREMENTALE - 1: elenco dei WebP GIA' PUBBLICATI"
INCR_END = "          RENDER_PID=$!\n"


def read(name):
    with open(os.path.join(WF, name), "r", encoding="utf-8") as fh:
        return fh.read()


def engine_block(text):
    i = text.find(INCR_START)
    assert i >= 0, "blocco del render incrementale assente"
    j = text.find(INCR_END, i)
    assert j >= 0, "fine del blocco incrementale non trovata"
    return text[i:j + len(INCR_END)]


def test_render_e_incrementale_e_il_merge_e_un_overlay():
    blocks = {}
    for name in PROVIDERS:
        text = read(name)

        # 1) il render riceve l'elenco dei path gia' pubblicati + la palette
        assert "--existing-list" in text, name
        assert 'INCR_ARGS="--existing-list $EXIST_FILE' in text, name
        assert "--existing-palette-sha" in text, name
        # una sola COSTRUZIONE degli argomenti (i commenti possono citarli)
        assert text.count('INCR_ARGS="--existing-list') == 1, name
        assert text.count("--existing-palette-sha $PALETTE_SHA") == 1, name

        # 2) il run_id NON si indovina: viene dal progress manifest del motore
        assert "_run_id(sys.argv[1])" in text, name

        # 3) niente `rm -rf` della directory dominio nel merge (overlay)
        assert 'rm -rf "data/fm2-rendered/$region' not in text, (
            "%s: il merge deve essere un overlay, non una sostituzione" % name)
        assert 'cp -a "$src/." "data/fm2-rendered/$region/' in text, (
            "%s: copia overlay dello shard assente" % name)

        # 4) niente `git add -A` sull'indice preparato (registrerebbe le
        #    cancellazioni dei WebP gia' pubblicati)
        assert 'git add -A -- "$rd"' not in text, (
            "%s: git add -A registra come cancellazioni i WebP pubblicati" % name)
        assert 'git add --ignore-removal -- "$rd"' in text, name

        # 5) il gate di `changed` guarda il filesystem dello shard: con
        #    `up-to-date` il renderer esce 0 senza scrivere nulla
        assert 'WEBP_SHARD="$(find data/fm2-rendered' in text, name
        assert '"$WEBP_SHARD" -gt 0' in text, name

        # 6) la retention delle run resta attiva dopo il merge
        assert "prune_fm2_run_index.py --index \"$PREP_IDX\" --path \"$d\" --keep 3" in text, name

        blocks[name] = engine_block(text)

    ref = blocks[PROVIDERS[0]]
    for name in PROVIDERS[1:]:
        assert blocks[name] == ref, (
            "%s: il blocco incrementale differisce da %s" % (name, PROVIDERS[0]))


def test_il_gate_dell_indice_non_dispatcha_su_no_op():
    for name in PROVIDERS:
        text = read(name)
        # l'indice aggregato si aggiorna solo se il merge ha davvero pubblicato
        assert "steps.commit_webp.outputs.published == 'true'" in text, name
