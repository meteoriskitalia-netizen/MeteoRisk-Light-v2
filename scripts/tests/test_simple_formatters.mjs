#!/usr/bin/env node
// TEST 1.3.0.59 — FORMATTER DELLA VISTA SIMPLE (simple/index.html).
// Estrae il blocco <script id="simple-formatters"> (funzioni PURE, nessun DOM
// al caricamento), lo valuta in un vm con window={} e verifica i formati,
// le classi dichiarate e le scale. Poi controlla la struttura della pagina
// (selettori, URL dati, parametri IDW) senza navigare.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HTML_PATH = path.join(ROOT, 'simple', 'index.html');
const html = fs.readFileSync(HTML_PATH, 'utf8');

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ` · ${detail}` : ''}`);
  if (!cond) failures++;
}
function failFast(label, cond) {
  if (!cond) { console.error(`FATAL: ${label}`); process.exit(1); }
}

// ---------- estrazione blocco puro ----------
const m = /<script id="simple-formatters">([\s\S]*?)<\/script>/.exec(html);
failFast('blocco <script id="simple-formatters"> non trovato', !!m);
const ctx = { window: {}, Intl, Math, Date, isFinite, parseInt };
vm.runInNewContext(m[1], ctx);
const F = ctx.SimpleFmt || ctx.window.SimpleFmt;
failFast('SimpleFmt non esposto', F && typeof F.fmtNum === 'function');

// ---------- numeri ----------
{
  ok('fmtNum(12.345,1) === "12,3"', F.fmtNum(12.345, 1) === '12,3', F.fmtNum(12.345, 1));
  ok('fmtNum(0,1) === "0,0"', F.fmtNum(0, 1) === '0,0', F.fmtNum(0, 1));
  ok('fmtInt(62.7) === "63"', F.fmtInt(62.7) === '63', F.fmtInt(62.7));
  ok('fmtNum(null,1) === "—"', F.fmtNum(null, 1) === '—', F.fmtNum(null, 1));
  ok('fmtNum(NaN,1) === "—"', F.fmtNum(NaN, 1) === '—', F.fmtNum(NaN, 1));
  ok('fmtNum(Infinity,1) === "—"', F.fmtNum(Infinity, 1) === '—', F.fmtNum(Infinity, 1));
}

// ---------- WMO ----------
{
  ok('wmoIcon(0) === "☀️"', F.wmoIcon(0) === '☀️', F.wmoIcon(0));
  ok('wmoIcon(95) === "⛈️"', F.wmoIcon(95) === '⛈️', F.wmoIcon(95));
  ok('wmoLabel(65) cita "forte"', /forte/i.test(F.wmoLabel(65)), F.wmoLabel(65));
  ok('codice ignoto non inventa una descrizione', /Codice WMO 123/.test(F.wmoLabel(123)), F.wmoLabel(123));
}

// ---------- classi precipitazione DICHIARATE ----------
{
  ok('precipClass(0) === 0', F.precipClass(0) === 0);
  ok('precipClass(0.1) === 1', F.precipClass(0.1) === 1);
  ok('precipClass(1) === 1 (estremo superiore incluso)', F.precipClass(1) === 1);
  ok('precipClass(1.01) === 2', F.precipClass(1.01) === 2);
  ok('precipClass(5) === 2 (estremo superiore incluso)', F.precipClass(5) === 2);
  ok('precipClass(5.01) === 3', F.precipClass(5.01) === 3);
  ok('precipClass(15) === 3 (estremo superiore incluso)', F.precipClass(15) === 3);
  ok('precipClass(15.01) === 4', F.precipClass(15.01) === 4);
  ok('precipClass(30) === 4 (estremo superiore incluso)', F.precipClass(30) === 4);
  ok('precipClass(30.1) === 5', F.precipClass(30.1) === 5);
  ok('precipClass(null) === -1 (nessuna classe inventata)', F.precipClass(null) === -1);
  ok('6 classi dichiarate', F.PRECIP_CLASSES.length === 6, String(F.PRECIP_CLASSES.length));
  ok('precipColor(null) non usa un colore di classe', F.precipColor(null) === '#475569', F.precipColor(null));
}

// ---------- classi vento 5/10/15/20/25 ----------
{
  ok('windClass(4.9) === 0', F.windClass(4.9) === 0);
  ok('windClass(5) === 1', F.windClass(5) === 1);
  ok('windClass(10) === 2', F.windClass(10) === 2);
  ok('windClass(15) === 3', F.windClass(15) === 3);
  ok('windClass(20) === 4', F.windClass(20) === 4);
  ok('windClass(25) === 5', F.windClass(25) === 5);
  ok('windClass(null) === -1', F.windClass(null) === -1);
  ok('6 classi vento', F.WIND_CLASSES.length === 6);
}

// ---------- scale colore ----------
{
  const t0 = F.thermalColor(-10), t35 = F.thermalColor(35);
  ok('thermalColor(-10) === [49,54,149]', JSON.stringify(t0) === '[49,54,149]', JSON.stringify(t0));
  ok('thermalColor(35) === [215,48,39]', JSON.stringify(t35) === '[215,48,39]', JSON.stringify(t35));
  ok('rainColor(0) === null (niente colore per 0 mm)', F.rainColor(0) === null);
  ok('rainColor(30) === [219,39,119]', JSON.stringify(F.rainColor(30)) === '[219,39,119]', JSON.stringify(F.rainColor(30)));
}

// ---------- date ----------
{
  ok('fmtHour("2026-11-23T05:00") === "05:00"', F.fmtHour('2026-11-23T05:00') === '05:00');
  ok('fmtDayMonth("2026-11-23") === "11/23"', F.fmtDayMonth('2026-11-23') === '11/23', F.fmtDayMonth('2026-11-23'));
  ok('dowShort("2026-11-23") === "LUN"', F.dowShort('2026-11-23') === 'LUN', F.dowShort('2026-11-23'));
  ok('fmtClockRome(null) === "—"', F.fmtClockRome(null) === '—');
}

// ---------- distanza (IDW cutoff 30 km) ----------
{
  const d = F.haversineKm(41.89332, 12.482932, 45.4642, 9.19); // Roma-Milano
  ok('haversine Roma-Milano ≈ 477 km', d > 470 && d < 485, d.toFixed(2));
  ok('haversine stesso punto = 0', F.haversineKm(42, 12, 42, 12) === 0);
}

// ---------- struttura della pagina ----------
{
  ok('selettore "Cosa mostrare" con 4 modalità',
    /id="layer-select"[\s\S]*?value="precip"[\s\S]*?value="hydro"[\s\S]*?value="radar"[\s\S]*?value="stations"/.test(html));
  ok('selettore variabili centraline con 4 voci',
    /id="var-select"[\s\S]*?value="temp"[\s\S]*?value="rain3h"[\s\S]*?value="wind"[\s\S]*?value="gust"/.test(html));
  ok('legge i JSON reali (provinces/weekly/simple_hourly/hydro/radar/stations)',
    ['meteorisk-provinces.json', 'province_italiane.geojson', 'weekly.json',
     'simple_hourly.json', 'criticality.json', 'radar/latest.json', 'stations/latest.json']
      .every((n) => html.includes(n)));
  ok('IDW k=6 e cutoff 30 km dichiarati', /IDW k=6/.test(html) && /d > 30/.test(html));
  ok('canvas IDW 256x227', /var W = 256, H = 227/.test(html));
  ok('maschera terra via destination-in', /destination-in/.test(html));
  ok('nessun Service Worker registrato', !/serviceWorker\.register/.test(html));
  ok('link alla Pro relativo "../" (sottopercorso Pages)', /href="\.\.\/"/.test(html));
  ok('nessun link assoluto "/" (404 su GitHub Pages in sottopercorso)', !/href="\/"/.test(html));
}

// ---------- app-mode: percorsi RELATIVI (bug 1.3.0.6x) ----------
{
  const m2 = /<script id="app-mode">([\s\S]*?)<\/script>/.exec(html);
  failFast('blocco app-mode non trovato', !!m2);
  const c2 = { window: { location: { href: '' } }, document: { getElementById: () => null } };
  vm.runInNewContext(m2[1], c2);
  ok('appModeHref("pro") === "../"', c2.appModeHref('pro') === '../', c2.appModeHref('pro'));
  ok('appModeHref("dev") === "../?dev=1"', c2.appModeHref('dev') === '../?dev=1', c2.appModeHref('dev'));
  ok('appModeHref("simple") === null', c2.appModeHref('simple') === null);
  ok('i percorsi non iniziano con "/" (relativi al sottopercorso)', ['pro', 'dev'].every((m) => !/^\//.test(c2.appModeHref(m))));
}

// ---------- ricerca comune: mapping risultati -> riga lista ----------
{
  const provPath = path.join(ROOT, 'data', 'latest', 'meteorisk-provinces.json');
  failFast('dataset province mancante: ' + provPath, fs.existsSync(provPath));
  const PROV = JSON.parse(fs.readFileSync(provPath, 'utf8')).provinces;
  ok('107 province nel dataset reale', PROV.length === 107, String(PROV.length));

  // Risposte REALI dell'API Open-Meteo misurate il 2026-11-23 (HTTP 200).
  const roma = { name: 'Roma', admin1: 'Lazio', admin2: 'Provincia di Roma', latitude: 41.89193, longitude: 12.51133 };
  const milano = { name: 'Milano', admin1: 'Lombardia', admin2: 'Provincia di Milano', latitude: 45.46427, longitude: 9.18951 };
  const aquila = { name: "L'Aquila", admin1: 'Abruzzo', admin2: 'Provincia dell\u2019Aquila', latitude: 42.35055, longitude: 13.39954 };
  const reggio = { name: "Reggio nell'Emilia", admin1: 'Emilia-Romagna', admin2: 'Provincia di Reggio Emilia', latitude: 44.69825, longitude: 10.63125 };
  const bolzano = { name: 'Bolzano', admin1: 'Trentino-Alto Adige', admin2: 'Provincia Autonoma di Bolzano', latitude: 46.49, longitude: 11.34 };
  const forli = { name: 'Forlì', admin1: 'Emilia-Romagna', admin2: 'Provincia di Forlì-Cesena', latitude: 44.22, longitude: 12.04 };

  const r1 = F.geoResultRow(roma, PROV);
  ok('Roma -> sigla RM', r1 && r1.sigla === 'RM', r1 && r1.sigla);
  ok('riga lista: testo "Provincia di Roma (RM)"', r1 && /Provincia di Roma \(RM\)/.test(r1.sub), r1 && r1.sub);
  ok('title della riga non e\u2019 un segnaposto', r1 && /^Vai a Roma/.test(r1.title) && r1.title.length > 20);
  ok('lat/lon del risultato conservate', r1.lat === 41.89193 && r1.lon === 12.51133);
  ok('Milano -> MI', F.geoResultRow(milano, PROV).sigla === 'MI');
  ok("L'Aquila (apostrofo tipografico U+2019) -> AQ", F.geoResultRow(aquila, PROV).sigla === 'AQ', F.geoResultRow(aquila, PROV).sigla);
  ok("Reggio nell'Emilia -> RE (sottoinsieme di token)", F.geoResultRow(reggio, PROV).sigla === 'RE', F.geoResultRow(reggio, PROV).sigla);
  ok('Bolzano (Provincia Autonoma) -> BZ', F.geoResultRow(bolzano, PROV).sigla === 'BZ');
  ok('Forli-Cesena (accento e trattino) -> FC', F.geoResultRow(forli, PROV).sigla === 'FC');

  ok('risultato senza nome -> null (nessuna riga inventata)', F.geoResultRow({ admin2: 'Provincia di Roma' }, PROV) === null);
  ok('admin2 vuoto -> provincia non risolta (sigla null)', F.geoResultRow({ name: 'X', admin1: 'Lazio' }, PROV).sigla === null);

  // Nessun risultato
  ok('geoNoResults cita la query', F.geoNoResults('zzzzqqqxx').indexOf('Nessun comune trovato') === 0 &&
    F.geoNoResults('zzzzqqqxx').indexOf('zzzzqqqxx') > 0, F.geoNoResults('zzzzqqqxx'));
  ok('geoNoResults con query vuota resta chiaro', F.geoNoResults('') === 'Nessun comune trovato.');

  // Fallback DICHIARATO sulle 107 province (API non raggiungibile)
  const fb = F.localProvinceRows('reggio', PROV);
  ok('fallback: "reggio" trova RC e RE', fb.length === 2 && fb.map((x) => x.sigla).sort().join(',') === 'RC,RE',
    fb.map((x) => x.sigla).join(','));
  ok('fallback: la riga dichiara regione e sigla', /sigla RE/.test((fb.find((x) => x.sigla === 'RE') || {}).sub || ''));
  ok('fallback: nessun match -> lista vuota', F.localProvinceRows('zzzz', PROV).length === 0);
  ok('fallback: 1 carattere -> nessuna ricerca', F.localProvinceRows('r', PROV).length === 0);
  ok('fallback: nessun punto inventato (lat/lon null)', fb.every((x) => x.lat === null && x.lon === null));
}

// ---------- linguette della card e giorno selezionato ----------
{
  ok('tab: default "3 giorni"', F.paneVisible('d3', 'd3') && !F.paneVisible('d3', 'd7') && !F.paneVisible('d3', 'hour'));
  ok('tab: "7 giorni" attiva solo su d7', F.paneVisible('d7', 'd7') && !F.paneVisible('d7', 'd3'));
  ok('tab: "Ora per ora" attiva solo su hour', F.paneVisible('hour', 'hour') && !F.paneVisible('hour', 'd7'));
  ok('tab sconosciuta -> default 3 giorni', F.normalizeTab('boh') === 'd3');
  ok('tre tab dichiarate', F.TABS.join(',') === 'd3,d7,hour', F.TABS.join(','));

  const tabs = [...html.matchAll(/<button[^>]*data-tab="(d3|d7|hour)"[^>]*>/g)].map((m) => m[0]);
  ok('3 linguette statiche con role=tab e title', tabs.length === 3 &&
    tabs.every((t) => /role="tab"/.test(t) && /title="[^"]{10,}"/.test(t)), String(tabs.length));
  ok('la linguetta attiva ha aria-selected=true nel markup', /id="tab-d3"[^>]*aria-selected="true"/.test(html));

  ok('ogni giorno della striscia e\u2019 un pulsante (role/tabindex/click/Enter)',
    /role="button" tabindex="0" data-day="' \+ i \+ '"/.test(html) &&
    /onclick="selectDay\(' \+ i \+ '\)"/.test(html) &&
    /onkeydown="dayKey\(event,' \+ i \+ '\)"/.test(html));
  ok('il giorno attivo si evidenzia (classe on + aria-pressed)',
    /var on = \(i === state\.dayIndex\)/.test(html) && /aria-pressed=/.test(html));
  ok('giorni 3-6: messaggio di indisponibilita\u2019 esatto, nessuna riga inventata',
    html.indexOf('Orari non disponibili per questo giorno: il dataset copre le prime 72 ore. Sono disponibili solo i valori giornalieri.') > 0);
  ok('intestazione del giorno con data esplicita (giorno/mese)',
    /"Ore di " \+ F\.dowLong\(date\) \+ " " \+ F\.fmtDayMonthIt\(date\)/.test(html));
  ok('fmtDayMonthIt("2026-11-24") === "24/11"', F.fmtDayMonthIt('2026-11-24') === '24/11', F.fmtDayMonthIt('2026-11-24'));
  ok('dowLong("2026-11-24") === "martedì"', F.dowLong('2026-11-24') === 'martedì', F.dowLong('2026-11-24'));
  ok('24h/48h restano per la vista scorrevole', /setHourlyLimit\(24\)/.test(html) && /setHourlyLimit\(48\)/.test(html));
  ok('default orario 24 (giorno selezionato)', /hourlyLimit: 24/.test(html));
}

// ---------- radar: palette condivisa e legenda ----------
{
  ok('13 stop Universal Blue nella Simple', (html.match(/\{ dbz: \d+,\s+color: "rgba\(/g) || []).length === 13,
    String((html.match(/\{ dbz: \d+,\s+color: "rgba\(/g) || []).length));
  ok('soglia di trasparenza R<25 dichiarata', /DPC_TRANSPARENT_R = 25/.test(html));
  ok('legenda dBZ con mm/h (stima Z=200\u00b7R^1,6)', /Z=200\u00b7R<sup>1,6<\/sup>/.test(html));
  ok('legenda dichiara tile lossy e VMI come massimo sulla verticale',
    /lossy/.test(html) && /massimo sulla verticale/.test(html));
  ok('layer canvas con crossOrigin per leggere i pixel', /img\.crossOrigin = "anonymous"/.test(html));
  ok('ricolorazione VMI dBZ = R*60/255', /var dbz = r \* 60 \/ 255/.test(html));
}

// ---------- campo di ricerca comune ----------
{
  const geoTag = (/<input id="geo-input"[\s\S]*?\/>/.exec(html) || [''])[0];
  ok('campo #geo-input type=search con placeholder', /type="search"/.test(geoTag) && /placeholder="Cerca comune…"/.test(geoTag));
  ok('campo #geo-input con title e aria-label', /title="[^"]{20,}"/.test(geoTag) && /aria-label="Cerca comune"/.test(geoTag));
  ok('endpoint di geocoding Open-Meteo dichiarato', /geocoding-api\.open-meteo\.com\/v1\/search/.test(html));
  ok('ricerca filtrata all\u2019Italia (countryCode=IT)', /countryCode=IT/.test(html));
  ok('attribuzione GeoNames CC BY 4.0 nel footer', /geonames\.org/.test(html) && /CC BY 4\.0/.test(html));
  ok('lista risultati con role=listbox', /id="geo-list" role="listbox"/.test(html));
}

console.log(`\nRESULT: ${failures === 0 ? 'PASS' : 'FAIL'} (${failures} errori)`);
process.exit(failures === 0 ? 0 : 1);
