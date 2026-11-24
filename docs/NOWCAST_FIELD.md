# Nowcast del campo di pioggia (SRI DPC) — metodo, costi, verifica, limiti

Modulo: `scripts/radar_engine/nowcast_field.py` ·
Test: `scripts/tests/radar_engine/test_nowcast_field.py` ·
Replay: `scripts/validation/nowcast_replay.py` ·
Output: `data/radar/nowcast.json` + **12** WebP, `nowcast_lead005.webp` …
`nowcast_lead060.webp` (un frame ogni 5 minuti; i lead 15/30/60 sono gli
ufficiali di verifica) ·
Verifica: `data/radar/nowcast_verification.json` · `docs/NOWCAST_REPLAY.md`

## Cosa fa e cosa NON fa

Sposta il **campo di pioggia** osservato dal radar (prodotto DPC **SRI**,
mm/h Float32, griglia 1200x1400 a 1 km) e ne pubblica la posizione prevista con
**un frame ogni 5 minuti da +5 a +60** (12 frame, player fluido come il radar).
I tre lead **ufficiali di verifica** restano +15/+30/+60 e portano
`official: true` nel JSON; gli altri 9 sono per il player e non entrano nel
confronto con la persistenza. Non e' un'estensione di `forecast.py`: quello sposta
il contorno di una **struttura convettiva tracciata** e dipende dal moto della
cella (il 24/11/2026 le strutture erano 0, quindi quel layer non produceva
nulla). Qui non esiste nessuna identita' di cella e nessuna dipendenza da
`tracking.py`.

Vincoli rispettati:

- sorgente **SRI**, mai VMI convertito in mm/h con una Z-R;
- nessun uso di ICON-2I / `nwp_motion.py`, nessun tracking ungherese;
- **solo trasporto**: nessuna crescita o decadimento dell'intensita';
- un vettore sopra **110 km/h viene sostituito** con la mediana del vicinato
  (0 solo se non esiste nessun vicino valido, e il caso e' contato): non e'
  clampato a 110 e non ferma la pioggia;
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
   **Una coppia senza stima vale NaN, non 0**: `estimate_flow` restituisce 0
   fuori dalla `texture` (nessun gradiente, per esempio l'eco e' comparsa dopo)
   e con la mediana di (0, 0, x) il risultato sarebbe 0, cioe' un pezzo di
   pioggia fermo per tutto l'orizzonte. Misurato il 24/11/2026 sullo slot
   19:55Z: 12.843 pixel di pioggia con vettore 0 (12,19%), di cui 9.174
   (71,4%) da questo meccanismo e 3.597 dal cap; 20,4% della pioggia si
   spostava < 0,5 km in 30 minuti.
3. **Cap 110 km/h**: `speed = hypot(u,v) * pixel_km * 3600 / 300`. Sopra il cap
   il vettore e' **sostituito** con la mediana del vicinato dei vettori sotto
   cap (finestra 5, 7, 9 ... fino a 10 px, vicini cercati dentro la pioggia);
   resta 0 solo se non esiste nessun vicino valido. Contatori pubblicati:
   `vectors_zeroed_by_cap` (pixel di PIOGGIA sopra cap, nome storico),
   `vectors_over_cap_total`, `vectors_replaced_by_cap`,
   `vectors_unfilled_by_cap`.
4. **Riempimento**: ogni pixel di pioggia senza vettore prende la mediana del
   vicinato valido (raggio fino a 10 px); gli isolati restano 0 e sono contati
   (`motion_filled_pixels`, `motion_unfilled_pixels`,
   `motion_null_reason: "no_neighbor_motion"`). Poi una mediana 3x3 dentro la
   pioggia (`motion_smoothing_px`), che non tocca le aree secche e non
   reintroduce zeri.
5. **Trasporto semi-Lagrangiano** a passi di 5 minuti:
   `F(x) <- F(x - u)` con campionamento bilineare e maschera del dominio a ogni
   passo. Il bilineare e' una combinazione convessa: il massimo previsto non
   puo' superare il massimo osservato.
6. **Maschera**: `valid_mask` del frame SRI piu' recente (la stessa del resto
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
speed_cap_mode: "replaced_by_neighbor_median",
vectors_zeroed_by_cap, vectors_over_cap_total, vectors_replaced_by_cap,
vectors_unfilled_by_cap, motion_filled_pixels, motion_unfilled_pixels,
motion_null_reason, motion_smoothing_px,
frames_used, frames_slots, domain_mask, motion_stats,
leads: [{lead_min: 5|10|...|60, valid_time, unit: "mm/h", image, image_crs,
         official: bool, stats}],
leads_total: 12, official_leads: [15, 30, 60],
grid: {crs: "EPSG:3857", bounds, width, height, resolution_m},
legend: [{mmh, rgb}], limits,
beats_persistence, verification, output_bytes
```

Le immagini sono WebP RGBA **in EPSG:3857** con alpha=0 fuori dominio, a
**1500 m**/pixel. Non e' un bbox lon/lat: la griglia SRI e' una Transverse
Mercator larga 14 gradi a sud e 16 a nord, quindi un overlay con bbox
geografico sarebbe sbagliato di decine di km. Il JSON dichiara `bounds` in
3857; il client converte con `map.options.crs.unproject(L.point(x, y))`.

Il JSON con i 12 campi grezzi sarebbe ~80 MB/run (12 x 1200 x 1400 float32):
misurato e scartato. Le 12 WebP costano **90.402 byte (88,3 kB)** su un caso
reale di pioggia leggera (24/11 12:45Z) e **324.736 byte (317,1 kB)** sul caso
intenso del 23/11 10:25Z: il costo dipende da quanta pioggia c'e' nel frame.
Il JSON e' **~6 kB** (~12 kB con il riassunto di verifica). La **serie**
completa della verifica (schema 2, una riga per slot x lead x soglia) **non**
viene incorporata: resta in `nowcast_verification.json` (misurato 76.964 B con
63 righe e 8 slot) e `nowcast.json` ne porta solo `series_summary` (righe,
slot, retention). Senza questo taglio il file committato ogni 10 minuti
crescerebbe con la serie fino a ~9 MB (retention 7 giorni) — misurato 67 kB al
primo run con la serie intera.

## Palette: allineata alla scala SRI del client (MISURATA)

Prima di questa modifica il nowcast usava una rampa propria a **8 stop**
(0,05/0,5/1/2/5/10/20/40 mm/h: azzurro, blu, verde, giallo, arancio, rosso,
viola, magenta) mentre il radar **SRI** del client usa `DPC_SRI_STOPS`
(mri-light-1.3.0.77.html righe 10983-10990, tabella Universal Blue: blu ->
azzurro -> ciano -> giallo -> arancio -> rosso) con **6 stop**
1/5/10/20/50/100 mm/h. Non erano gli stessi colori ne' gli stessi breakpoint:
distanza RGB 144 a 0,5, 300 a 2, 340 a 5, 284 a 10 mm/h (verde/giallo/rosso
contro blu/ciano/giallo).

Ora `nowcast_field.PALETTE` e' **la copia esatta** di `DPC_SRI_STOPS`:

| mm/h | nowcast RGB (dopo) | radar SRI RGB (client) | distanza | note |
| ---: | :--- | :--- | ---: | :--- |
| 0,5 | `(0,85,136)` | `(0,85,136)` | **0** | coda: sotto 1 mm/h -> primo colore |
| 2 | `(0,104,158)` | `(0,104,158)` | **0** | interpolazione lineare 1->5 |
| 5 | `(0,163,224)` | `(0,163,224)` | **0** | |
| 10 | `(136,221,238)` | `(136,221,238)` | **0** | |
| 20 | `(255,238,0)` | `(255,238,0)` | **0** | |
| 50 | `(255,170,0)` | `(255,170,0)` | **0** | |
| 100 | `(193,0,0)` | `(193,0,0)` | **0** | |
| >100 | `(193,0,0)` | `(193,0,0)` | **0** | coda dichiarata (SRI si ferma a 100) |

Round-trip nell'encoder: un campo costante a 50 mm/h salvato in WebP lossy
(q80) si rilegge `(254,170,0)` invece di `(255,170,0)` — **delta 1/255 sul
canale R**, alpha esatto; e' la perdita del codec, non la palette.

**Differenza di VISIBILITA' (dichiarata)**: il client rende trasparenti i
pixel grezzi del tile SRI con R,G,B < 25/255, cioe' sotto **~9,8 mm/h**
(`DPC_TRANSPARENT_R = 25`, riga 10976). Il nowcast invece disegna anche la
pioggia leggera (alpha da 0,05 a 0,20 mm/h): a 0,5-5 mm/h il radar osservato
e' vuoto e il nowcast e' colorato. Non e' un errore di colore: e' una soglia
di visibilita' del client, ed e' dichiarata qui. Se si vuole la stessa
visibilita' basta portare `RAIN_MIN_MMH` a 9,8 nel nowcast, ma si perderebbe
tutta la pioggia debole (la maggior parte del prodotto).

**Legenda del client**: il markup della legenda nowcast in
`mri-light-1.3.0.77.html` (righe 2773-2777) ha ancora le etichette
0,05/0,5/2/5/10/20+, scritte a mano. Il JSON ora pubblica gli stop corretti in
`legend` (1/5/10/20/50/100) e i WebP usano quei colori, ma l'HTML non e' stato
toccato in questa release: le etichette della legenda vanno riallineate in una
release successiva (stesse sei voci della legenda SRI).

## Costi misurati (24/11/2026, host di sviluppo, slot reali)

| Voce | Misura |
| :--- | :--- |
| 6 frame SRI dal DPC (fuori pipeline) | 4,2-5,9 s, ~2,0 MB |
| Rete aggiuntiva nel run operativo (bundle fase 2) | **0** |
| Rete aggiuntiva senza candidati (fallback) | 6 download, ~5 s, ~2 MB |
| `compute` (flusso + 12 passi cumulativi + statistiche) | **4,97-5,00 s** |
| 12 WebP (reproject + encode) | **4,61-5,02 s** |
| Totale calcolo+immagini (senza fetch) | **9,6-10,0 s** |
| `nowcast.json` (12 lead) | 6.172-6.275 byte (~12 kB con il riassunto di verifica, senza la serie) |
| 12 WebP, pioggia leggera | **90.402 byte** (media 7.534 B/frame) |
| 12 WebP, pioggia intensa | **324.736 byte** (media 27.061 B/frame) |

Il trasporto e' **cumulativo**: i 12 frame costano 12 passi da 5 minuti in
totale (non 12 trasporti completi), quindi il compute resta ~5 s. Il costo
vero e' la codifica delle immagini (12 reprojection+WebP ~5 s): il totale e'
~10 s contro i ~5 s di prima (3 frame), cioe' **+5 s** su un job da 20 minuti.
Nessuna esplosione: la parte di flusso ottico e' invariata.

Coerenza fra frame consecutivi (misurata sui pixel con pioggia, caso 24/11
12:45Z / caso intenso 23/11 10:25Z): correlazione +5 vs +10 **0,898 / 0,712**,
differenza media assoluta **0,196 / 0,330 mm/h**. Il +10 e' inoltre
**bit-identico** al +5 avanzato di un altro passo da 5 minuti
(`semi_lagrangian(native[5], u, v, 1) == native[10]`, verificato anche a +20):
la cumulativita' non e' una coincidenza numerica, e' la stessa operazione.

Run end-to-end `--as-of 2026-11-23T10:25Z` (3 frame VMI, **0 strutture**, quindi
percorso di fallback) con la versione a 3 frame: `status=ok`, nowcast
`available: true`, `frames_origin: fetch_frames`, 6 frame usati. Con 12 frame
lo stage resta ~10 s di calcolo; il job radar ha un budget di 20 minuti.

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
<!-- NOWCAST PIOGGIA (SRI, 12 frame da +5 a +60) — toggle + player -->
<label id="nowcast-toggle-wrap" style="display:none; align-items:center; gap:6px;"
       title="Pioggia prevista dal trasporto del radar SRI (5-60 min, passo 5)">
  <input type="checkbox" id="nowcast-toggle" onchange="toggleNowcastField(this.checked)">
  <span>Nowcast pioggia</span>
  <button type="button" id="nowcast-play" class="tool-btn" style="display:none"
          onclick="toggleNowcastPlay()" title="Play/pausa del nowcast">&#9654;</button>
  <input type="range" id="nowcast-lead" min="5" max="60" step="5" value="30"
         style="display:none; width:120px" oninput="setNowcastLead(parseInt(this.value,10))"
         title="Orizzonte della previsione (minuti)">
  <span id="nowcast-lead-label" style="display:none; min-width:52px"
        title="Orizzonte della previsione">+30 min</span>
</label>
<div id="nowcast-legend" style="display:none; ...">
  <div class="nowcast-bar"></div>
  <div class="nowcast-ticks"><span>1</span><span>5</span><span>10</span><span>20</span><span>50</span><span>100+</span></div>
  <div class="nowcast-note">mm/h · stessa scala del radar SRI · dopo 30 minuti l'affidabilità cala</div>
</div>

<script>
/* Nowcast del campo di pioggia — SRI. Nessun colore inventato: la legenda usa
   gli stessi stop pubblicati in nowcast.json (legend). */
window.NOWCAST = (function () {
  var URL = 'data/radar/nowcast.json';
  var LEADS = [5,10,15,20,25,30,35,40,45,50,55,60];   /* 12 frame */
  /* stessi RGB di DPC_SRI_STOPS del client (Universal Blue) */
  var PALETTE = [[1,'#005588'],[5,'#00a3e0'],[10,'#88ddee'],[20,'#ffee00'],
                 [50,'#ffaa00'],[100,'#c10000']];
  var st = {data:null, layer:null, lead:30, busy:false, timer:null};
  function img(lead) {                 /* nowcast_lead005 ... nowcast_lead060 */
    return 'data/radar/nowcast_lead' + ('00' + lead).slice(-3) + '.webp';
  }
  function preload() {                 /* player fluido: 12 frame in cache */
    LEADS.forEach(function (lead) { var i = new Image(); i.src = img(lead); });
  }
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
    if (!on) {
      if (st.layer) { map.removeLayer(st.layer); st.layer = null; }
      document.getElementById('nowcast-legend').style.display = 'none';
      return;
    }
    var g = st.data.grid, url = img(st.lead);
    if (st.layer && st.layer.setUrl) {
      st.layer.setUrl(url);              /* nessun flicker fra due frame */
    } else {
      if (st.layer) map.removeLayer(st.layer);
      st.layer = L.imageOverlay(url, bounds3857(g.bounds),
                                {opacity: 0.75, pane: 'overlayPane',
                                 className: 'nowcast-field'}).addTo(map);
    }
    var lbl = document.getElementById('nowcast-lead-label');
    if (lbl) lbl.textContent = '+' + st.lead + ' min';
    document.getElementById('nowcast-legend').style.display = 'block';
  }
  function playIcon(on) {
    var b = document.getElementById('nowcast-play');
    if (b) b.innerHTML = on ? '&#10074;&#10074;' : '&#9654;';
  }
  function stop() {
    if (st.timer) { clearInterval(st.timer); st.timer = null; }
    playIcon(false);
  }
  function next() {
    var i = LEADS.indexOf(st.lead);
    st.lead = LEADS[(i + 1) % LEADS.length];
    var s = document.getElementById('nowcast-lead');
    if (s) s.value = st.lead;
    show(true);
  }
  function play() {                    /* 12 frame, ~800 ms/frame */
    stop();
    st.timer = setInterval(next, 800);
    playIcon(true);
  }
  return {
    load: function (cb) {
      if (st.data) { cb(st.data); return; }
      if (st.busy) return;
      st.busy = true;
      fetch(URL, {cache: 'no-store'}).then(function (r) { return r.json(); })
        .then(function (d) {
          st.data = d; st.busy = false;
          if (d && d.available) preload();
          cb(d);
        })
        .catch(function () { st.busy = false; cb(null); });
    },
    setLead: function (lead) {
      lead = Math.max(5, Math.min(60, Math.round(lead / 5) * 5));
      st.lead = lead;
      var s = document.getElementById('nowcast-lead');
      if (s) s.value = lead;
      var lbl = document.getElementById('nowcast-lead-label');
      if (lbl) lbl.textContent = '+' + lead + ' min';
      if (st.layer) show(true);
    },
    next: next,
    play: play,
    stop: stop,
    isPlaying: function () { return !!st.timer; },
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
      NOWCAST.stop(); NOWCAST.show(false);
      return;
    }
    ['nowcast-play', 'nowcast-lead', 'nowcast-lead-label'].forEach(function (id) {
      document.getElementById(id).style.display = on ? 'inline-block' : 'none';
    });
    if (!on) NOWCAST.stop();
    NOWCAST.show(on);
  });
}
function setNowcastLead(lead) { NOWCAST.setLead(lead); }
function toggleNowcastPlay() {
  if (NOWCAST.isPlaying()) NOWCAST.stop(); else NOWCAST.play();
}

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
parte). E' un player sui **12 frame** (slider 5-60 e play a ~800 ms/frame,
preload dei 12 WebP), non piu' un selettore a 3 valori. La legenda e' in
**mm/h** e la frase "dopo 30 minuti l'affidabilità cala" e' nel testo del
widget.

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
- **Frame del player**: i 9 lead non ufficiali sono lo stesso trasporto
  cumulativo campionato ogni 5 minuti; non hanno una verifica indipendente e
  non entrano in `beats_persistence` (dichiarato in `official: false` e in
  `nowcast_verify.py`). La verifica resta sui 15/30/60.
- **Lacuna di un frame**: se un WebP non si scrive, il lead resta con
  `image: null` e `image_error`; gli altri 11 sono validi e il player salta il
  frame. Se non se ne scrive NESSUNO il prodotto passa ad `available: false`
  con `null_reason: image_write_failed:all_frames`.
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
