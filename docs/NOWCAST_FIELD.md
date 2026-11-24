# Nowcast del campo di pioggia (SRI DPC) — metodo, costi, verifica, limiti

Modulo: `scripts/radar_engine/nowcast_field.py` ·
Test: `scripts/tests/radar_engine/test_nowcast_field.py` ·
Replay: `scripts/validation/nowcast_replay.py` ·
Output: `data/radar/nowcast.json` + `nowcast_lead015.webp`, `nowcast_lead030.webp`,
`nowcast_lead060.webp` ·
Verifica: `data/radar/nowcast_verification.json` · `docs/NOWCAST_REPLAY.md`

## Cosa fa e cosa NON fa

Sposta il **campo di pioggia** osservato dal radar (prodotto DPC **SRI**,
mm/h Float32, griglia 1200x1400 a 1 km) e ne pubblica la posizione prevista a
**+15, +30, +60 minuti**. Non e' un'estensione di `forecast.py`: quello sposta
il contorno di una **struttura convettiva tracciata** e dipende dal moto della
cella (il 24/11/2026 le strutture erano 0, quindi quel layer non produceva
nulla). Qui non esiste nessuna identita' di cella e nessuna dipendenza da
`tracking.py`.

Vincoli rispettati:

- sorgente **SRI**, mai VMI convertito in mm/h con una Z-R;
- nessun uso di ICON-2I / `nwp_motion.py`, nessun tracking ungherese;
- **solo trasporto**: nessuna crescita o decadimento dell'intensita';
- un vettore sopra **110 km/h viene azzerato** (0,0), non clampato a 110;
- fuori dal dominio radar non viene disegnato nulla.

## Ingresso

Ultimi **6 fotogrammi SRI** (passo 5 minuti, 25 minuti di storia). Due percorsi,
lo stesso downloader del motore:

- se la fase 2 ha girato, `fetch.fetch_product_bundle` trattiene gia' SRI su
  tutti gli slot (`retain_non_reference`) e il nowcast legge
  `bundle.dpc_bundle`: **costo di rete zero**;
- se la fase 2 non ha candidati (0 strutture: il caso del 24/11/2026) o il
  bundle non c'e', usa `fetch.fetch_frames(product="SRI")`, che passa da
  `source_dpc.get_latest_product` / `download_product`: **6 download, ~5 s e
  ~2 MB** (misurato). In entrambi i casi nessun secondo downloader.

Se i fotogrammi utili sono **< 3** il file viene scritto con `available: false`
e `null_reason` = `sri_unavailable` (fetch fallito) oppure `sri_frames_lt_3`.
Il run radar non fallisce: la chiamata in `main.py` e' dentro `try/except`
(errore -> warning `nowcast layer failed: ...`, `rc` invariato).

Altri motivi di assenza dichiarati: `no_rain_in_latest_frame` (trasporto puro:
se l'ultimo campo e' asciutto non c'e' nulla da spostare), `motion_unavailable`,
`sri_grid_unavailable`, `image_write_failed:*`.

## Calcolo

1. **Flusso ottico denso** fra frame consecutivi: Lucas-Kanade piramidale
   (finestra gaussiana, piramide 4x e 2x, warp incrementale) implementato in
   numpy/scipy. Il vettore e' poi riportato a 1 km **scalando i valori** con il
   rapporto di risoluzione (senza quello scaling il moto risultava diviso per 4:
   misurato sul test sintetico 6 px -> 1,45 px).
2. Il moto usato e' la **mediana per componente** delle ultime 3 coppie
   consecutive (robusta a una coppia anomala), normalizzata al passo di 5 minuti.
   Coppie con buco temporale > 15 min sono escluse (dichiarato nei warning).
3. **Cap 110 km/h**: `speed = hypot(u,v) * pixel_km * 3600 / 300`. Sopra il cap
   il vettore diventa (0,0). Il numero di vettori azzerati e' pubblicato
   (`vectors_zeroed_by_cap`).
4. **Trasporto semi-Lagrangiano** a passi di 5 minuti:
   `F(x) <- F(x - u)` con campionamento bilineare e maschera del dominio a ogni
   passo. Il bilineare e' una combinazione convessa: il massimo previsto non
   puo' superare il massimo osservato.
5. **Maschera**: `valid_mask` del frame SRI piu' recente (la stessa del resto
   del motore, -9999 = fuori copertura). Il motore non ha una maschera
   terra/mare separata per il radar: il SRI e' un mosaico che copre anche il
   mare entro la portata. Il nowcast non aggiunge nessun pixel fuori da quella
   maschera: nessuna geometria inventata oltre il dominio radar.

Perche' non pySTEPS/OpenCV: nel runner **non sono installati** (requirements:
numpy, rasterio, scipy, pyproj, h5py, matplotlib) e OpenCV sarebbe una
dipendenza binaria pesante sul percorso del run radar. Il prompt ammette
Lucas-Kanade; l'implementazione e' verificata dal test sintetico (displacement
noto) e confrontata con una cross-correlazione FFT indipendente sui casi reali.
Costo: **zero dipendenze nuove** oltre a Pillow (gia' dipendenza di matplotlib,
qui pinnata per l'encoder WebP).

## Output

`nowcast.json` (schema `meteorisk.nowcast_field/1`):

```
available, null_reason, source: "DPC_SRI", source_slot, generated_at,
motion: "optical_flow", motion_algorithm, speed_cap_kmh: 110,
frames_used, frames_slots, domain_mask, motion_stats,
leads: [{lead_min: 15|30|60, valid_time, unit: "mm/h", image, image_crs, stats}],
grid: {crs: "EPSG:3857", bounds, width, height, resolution_m},
legend: [{mmh, rgb}], limits,
beats_persistence, verification, output_bytes
```

Le immagini sono WebP RGBA **in EPSG:3857** con alpha=0 fuori dominio, a
**1500 m**/pixel. Non e' un bbox lon/lat: la griglia SRI e' una Transverse
Mercator larga 14 gradi a sud e 16 a nord, quindi un overlay con bbox
geografico sarebbe sbagliato di decine di km. Il JSON dichiara `bounds` in
3857; il client converte con `map.options.crs.unproject(L.point(x, y))`.

Il JSON con i tre campi grezzi sarebbe ~20 MB/run (3 x 1200 x 1400 float32):
misurato e scartato. Le tre WebP sullo stesso caso costano **80.330 byte** in
totale, il JSON **~3 kB** (8.782 byte con il blocco di verifica incorporato).

## Costi misurati (24/11/2026, host di sviluppo, slot reali)

| Voce | Misura |
| :--- | :--- |
| 6 frame SRI dal DPC (fuori pipeline) | 4,2-5,9 s, ~2,0 MB |
| Rete aggiuntiva nel run operativo (bundle fase 2) | **0** |
| Rete aggiuntiva senza candidati (fallback) | 6 download, ~5 s, ~2 MB |
| `compute` (flusso + trasporto + statistiche) | 17,8-23,4 s |
| 3 WebP + JSON | 4,7-6,2 s |
| `nowcast.json` | 2.969-2.972 byte (8.782 con la verifica) |
| WebP +15 / +30 / +60 | 28.028 / 26.756 / 25.546 byte |

Run end-to-end `--as-of 2026-11-23T10:25Z` (3 frame VMI, **0 strutture**, quindi
percorso di fallback): `status=ok`, nowcast `available: true`,
`frames_origin: fetch_frames`, 6 frame usati, stage `nowcast_field`
**29,7 s** (misurato sotto carico concorrente; in isolamento ~23 s). Il job
radar ha un budget di 20 minuti.

Controllo indipendente del moto (cross-correlazione FFT globale su una finestra
centrale vs mediana del flusso Lucas-Kanade sui pixel di pioggia, stesse coppie):

| slot | xcorr globale | LK mediana | LK p90 |
| :--- | ---: | ---: | ---: |
| 2026-11-23T10:25Z | 27 km/h | 34 km/h | 78 km/h |
| 2026-11-22T10:25Z | 73 km/h | 46 km/h | 99 km/h |
| 2026-11-22T15:00Z | 49 km/h | 40 km/h | 87 km/h |

Non sono la stessa statistica (un vettore globale vs la mediana spaziale di un
campo che varia), ma sono nello stesso ordine di grandezza: il vecchio difetto
di scala (moto diviso per 4) e' stato corretto e verificato. Il difetto e' stato
trovato dal test sintetico a spostamento noto (6 px/frame -> 1,45 prima,
5,81 dopo).

## Verifica contro la persistenza (MISURATA)

Regola dichiarata **prima** della misura (in `nowcast_field.py`):

- `beats_persistence` calcolato con >= 5 casi validi;
- punteggi (CSI/POD/FAR) pubblicati solo con >= 20 casi validi e >= 10.000
  pixel di pioggia osservata accumulati;
- decisione: media del CSI pooled sui lead 15/30/60 e sulle soglie 0,5/2,0 mm/h.

Replay: **49 ancore orarie** dal 2026-11-22T06:25Z al 2026-11-24T06:25Z, tutte
con pioggia osservata; 9.244.031 pixel di pioggia osservata (contati una volta
per caso e lead). Risultato:

| | nowcast | persistenza |
| :--- | ---: | ---: |
| CSI medio (lead 15/30/60 x soglie 0.5/2.0) | **0,499** | 0,478 |
| CSI +15 / 0.5 mm/h | **0,678** | 0,650 |
| CSI +30 / 0.5 mm/h | **0,575** | 0,558 |
| CSI +60 / 0.5 mm/h | 0,464 | **0,470** |
| CSI +60 / 2.0 mm/h | **0,313** | 0,299 |
| CSI +60 / 5.0 mm/h | **0,170** | 0,130 |

`beats_persistence: true` (delta +0,021), ma a **+60 minuti e 0,5 mm/h la
persistenza vince di 0,006**: e' coerente con il trasporto puro e con il limite
"oltre 30 minuti l'affidabilita' cala". I numeri completi (POD/FAR per lead e
soglia, caso per caso) sono in `docs/NOWCAST_REPLAY.md`;
`data/radar/nowcast_verification.json` e' la versione compatta letta dal run.

**Limite statistico dichiarato**: le 49 ancore sono su **due giorni** e le
finestre si sovrappongono, quindi non sono indipendenti. Il verdetto descrive
quei due giorni, non una climatologia. Per un campione indipendente servono
giorni/eventi separati (l'archivio SRI DPC accessibile via `downloadProduct`
copre almeno 48 ore; verificato con richieste reali a -5, -60, -360, -1440,
-2880 minuti).

## Frammento per il toggle (da applicare in una release successiva)

Da inserire **nello stesso scope** di `isSviluppoActive` (accanto a
`toggleNewIndices`, dentro lo script principale dell'app): il gate di modo usa
quella variabile, che non e' globale. Se il blocco va in uno script separato,
sostituire `nowcastDevAllowed()` con il segnale di modo dell'app.

```html
<!-- NOWCAST PIOGGIA (SRI, 60 min) — toggle layer -->
<label id="nowcast-toggle-wrap" style="display:none; align-items:center; gap:6px;"
       title="Pioggia prevista dal trasporto del radar SRI (15/30/60 min)">
  <input type="checkbox" id="nowcast-toggle" onchange="toggleNowcastField(this.checked)">
  <span>Nowcast pioggia</span>
  <select id="nowcast-lead" onchange="setNowcastLead(parseInt(this.value,10))"
          style="display:none" title="Orizzonte della previsione">
    <option value="15">+15 min</option>
    <option value="30" selected>+30 min</option>
    <option value="60">+60 min</option>
  </select>
</label>
<div id="nowcast-legend" style="display:none; ...">
  <div class="nowcast-bar"></div>
  <div class="nowcast-ticks"><span>0,05</span><span>0,5</span><span>2</span><span>5</span><span>10</span><span>20+</span></div>
  <div class="nowcast-note">mm/h · dopo 30 minuti l'affidabilità cala</div>
</div>

<script>
/* Nowcast del campo di pioggia — SRI. Nessun colore inventato: la legenda usa
   gli stessi stop pubblicati in nowcast.json (legend). */
window.NOWCAST = (function () {
  var URL = 'data/radar/nowcast.json';
  var IMG = {15:'nowcast_lead015.webp', 30:'nowcast_lead030.webp', 60:'nowcast_lead060.webp'};
  var PALETTE = [[0.05,'#9ad0ff'],[0.5,'#3d8bff'],[1,'#00c878'],[2,'#ffdd33'],
                 [5,'#ff9100'],[10,'#e62828'],[20,'#b000b0'],[40,'#ff00ff']];
  var st = {data:null, layer:null, lead:30, busy:false};
  function nowcastDevAllowed() {
    return (typeof isSviluppoActive !== 'undefined' && isSviluppoActive) ||
           window.MRI_DEV_MODE === true;
  }
  function bounds3857(b) {
    var crs = map.options.crs;
    return L.latLngBounds(crs.unproject(L.point(b[0], b[1])),
                          crs.unproject(L.point(b[2], b[3])));
  }
  function legend() {
    var stops = PALETTE.map(function (p, i) {
      return p[1] + ' ' + (100 * i / (PALETTE.length - 1)) + '%';
    });
    document.querySelector('#nowcast-legend .nowcast-bar').style.background =
      'linear-gradient(90deg,' + stops.join(',') + ')';
  }
  function show(on) {                      /* il layer e' UNO solo */
    if (st.layer) { map.removeLayer(st.layer); st.layer = null; }
    if (!on) { document.getElementById('nowcast-legend').style.display = 'none'; return; }
    var g = st.data.grid;
    st.layer = L.imageOverlay('data/radar/' + IMG[st.lead],
                              bounds3857(g.bounds),
                              {opacity: 0.75, pane: 'overlayPane',
                               className: 'nowcast-field'}).addTo(map);
    document.getElementById('nowcast-legend').style.display = 'block';
  }
  return {
    load: function (cb) {
      if (st.data) { cb(st.data); return; }
      if (st.busy) return;
      st.busy = true;
      fetch(URL, {cache: 'no-store'}).then(function (r) { return r.json(); })
        .then(function (d) { st.data = d; st.busy = false; cb(d); })
        .catch(function () { st.busy = false; cb(null); });
    },
    setLead: function (lead) { st.lead = lead; if (st.layer) show(true); },
    show: show
  };
})();

function toggleNowcastField(on) {
  var wrap = document.getElementById('nowcast-toggle-wrap');
  NOWCAST.load(function (d) {
    var allowed = d && d.available &&
      (d.beats_persistence !== false || nowcastDevAllowed());
    if (!allowed) {
      wrap.style.display = 'none';
      document.getElementById('nowcast-toggle').checked = false;
      NOWCAST.show(false);
      return;
    }
    document.getElementById('nowcast-lead').style.display = on ? 'inline-block' : 'none';
    NOWCAST.show(on);
  });
}
function setNowcastLead(lead) { NOWCAST.setLead(lead); }

/* Visibilita' del toggle: solo se il prodotto esiste e la verifica lo consente
   (oppure si e' in Sviluppo). Il fetch e' una volta sola al boot. */
document.addEventListener('DOMContentLoaded', function () {
  NOWCAST.load(function (d) {
    var ok = d && d.available &&
      (d.beats_persistence !== false || nowcastDevAllowed());
    document.getElementById('nowcast-toggle-wrap').style.display =
      ok ? 'inline-flex' : 'none';
    if (ok) legend();
  });
});
</script>
```

Il frammento non e' stato applicato all'HTML (la release e' in corso d'altra
parte). La legenda e' in **mm/h** e la frase "dopo 30 minuti l'affidabilità
cala" e' nel testo del widget.

## Limiti dichiarati

- **Trasporto puro**: nessuna crescita/decadimento; la pioggia che nasce o
  muore non e' prevista. E' il motivo per cui a +60 min e bassa soglia la
  persistenza puo' vincere (misurato: +60/0,5 mm/h, 0,464 vs 0,470).
- **Diffusione numerica**: 12 passi bilineari smorzano i massimi (misurato:
  max 76,2 -> 25,7 mm/h a +60 su un caso reale). Lo schema cubico riduce lo
  smorzamento ma non migliora il CSI a 0,5 mm/h (misurato: 0,304 vs 0,301 a
  +60), quindi resta il bilineare, che non puo' sovrastimare.
- **Moto a 2 km**: il flusso e' risolto a 2 km e interpolato a 1 km.
- **Nessun Doppler**: il moto e' un flusso ottico, non una velocita' radiale.
- **Finestra 25 min**: un moto che cambia dentro la finestra viene mediato.
- **Campione**: 49 ancore su 2 giorni, sovrapposte (v. sopra).
- La maschera e' il dominio radar: sopra il mare dentro la copertura il campo
  e' mostrato (e' il dato reale del mosaico), fuori copertura no.

## Raccolta e verifica automatica (2026-11-24)

Il nowcast emesso viene ora archiviato per slot (`nowcast_archive`,
branch orphan `nowcast-archive`, retention 7 giorni) e verificato a posteriori
contro l'SRI osservato; la serie finisce in
`data/radar/nowcast_verification.json` (schema
`meteorisk.nowcast_verification/2`, riassunto schema 1 intatto). Dettagli,
misure e limiti: **`docs/NOWCAST_VERIFICATION.md`**.
