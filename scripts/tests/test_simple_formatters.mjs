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
  ok('selettore variabili centraline con 8 voci',
    (html.match(/<option value="(temp|tmax|tmin|rh|rain1h|rain3h|wind|gust)"/g) || []).length === 8,
    String((html.match(/<option value="(temp|tmax|tmin|rh|rain1h|rain3h|wind|gust)"/g) || []).length));
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

// ---------- centraline: variabile + finestra ----------
{
  ok('8 variabili delle centraline', Object.keys(F.STATION_VARS).join(',') === 'temp,tmax,tmin,rh,rain1h,rain3h,wind,gust',
    Object.keys(F.STATION_VARS).join(','));

  const sd = F.stationSpec('tmax', 'day');
  ok('tmax+day: campo temp_max_day_c', sd.field === 'temp_max_day_c', sd.field);
  ok('tmax+day: flag parzialita temp_day_partial', sd.partialField === 'temp_day_partial', sd.partialField);
  ok('tmax+day: ora del picco temp_max_day_hour', sd.peakField === 'temp_max_day_hour', sd.peakField);
  ok('tmax+day: unita e scala dichiarate', sd.unit === '°C' && sd.decimals === 1 && sd.scale === 'thermal',
    `${sd.unit} ${sd.decimals} ${sd.scale}`);
  ok('tmax+day: etichetta finestra "oggi (dalle 00:00)"', sd.windowLabel === 'oggi (dalle 00:00)', sd.windowLabel);
  ok('tmax+day: sorgente daily, nessun gate di copertura, parziale non bloccante',
    sd.source === 'daily' && sd.coverageGate === false && sd.partialBlocks === false,
    `${sd.source} gate=${sd.coverageGate} blocks=${sd.partialBlocks}`);
  const sn = F.stationSpec('tmin', 'day');
  ok('tmin+day: campo temp_min_day_c', sn.field === 'temp_min_day_c', sn.field);
  ok('tmin+day: ora del picco temp_min_day_hour', sn.peakField === 'temp_min_day_hour', sn.peakField);
  ok('tmax 1h/3h: temp_max_1h_c / temp_max_3h_c',
    F.stationSpec('tmax', '1h').field === 'temp_max_1h_c' && F.stationSpec('tmax', '3h').field === 'temp_max_3h_c');
  ok('tmin 1h/3h: temp_min_1h_c / temp_min_3h_c',
    F.stationSpec('tmin', '1h').field === 'temp_min_1h_c' && F.stationSpec('tmin', '3h').field === 'temp_min_3h_c');
  ok('rh: relative_humidity_2m e scala umidita',
    F.stationSpec('rh').field === 'relative_humidity_2m' && F.stationSpec('rh').scale === 'humidity');
  ok('rain1h/rain3h: campi e coperture',
    F.stationSpec('rain1h').field === 'rain_1h_mm' && F.stationSpec('rain1h').coverageField === 'rain_1h_coverage_frac' &&
    F.stationSpec('rain3h').field === 'rain_3h_mm' && F.stationSpec('rain3h').coverageField === 'rain_3h_coverage_frac');
  ok('wind 1h/3h: wind_mean_1h_ms / wind_mean_3h_ms',
    F.stationSpec('wind', '1h').field === 'wind_mean_1h_ms' && F.stationSpec('wind', '3h').field === 'wind_mean_3h_ms');
  ok('gust 1h/3h: gust_max_1h_ms / gust_max_3h_ms',
    F.stationSpec('gust', '1h').field === 'gust_max_1h_ms' && F.stationSpec('gust', '3h').field === 'gust_max_3h_ms');
  ok('vento senza finestra "day": ricade su 1h', F.stationSpec('wind', 'day').window === '1h', F.stationSpec('wind', 'day').window);
  ok('temperatura istantanea: finestra non applicata',
    F.stationSpec('temp', '3h').window === '' && F.stationSpec('temp').windowApplies === false);

  // ---- dati REALI del dataset (campi presenti e assenti, nessun valore inventato)
  const ST = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'stations', 'latest.json'), 'utf8'));
  const gen = ST.generated_at;
  const absent = F.stationFieldStats(F.stationSpec('tmax', 'day'), ST.stations, gen);
  ok('tmax+day sul dataset reale: campo assente, zero valori', absent.total === 0 && absent.available === false,
    JSON.stringify({ total: absent.total, used: absent.used }));
  ok('tmax+day: il motivo nomina temp_max_day_c', /temp_max_day_c/.test(absent.reason || ''), absent.reason);
  const stTemp = F.stationFieldStats(F.stationSpec('temp'), ST.stations, gen);
  ok('temperatura reale: disponibile con N>100', stTemp.available === true && stTemp.used > 100,
    `${stTemp.used}/${stTemp.total}`);
  const tmax1h = F.stationFieldStats(F.stationSpec('tmax', '1h'), ST.stations, gen);
  ok('tmax+1h reale: campo temp_max_1h_c disponibile con N>0', tmax1h.available === true && tmax1h.used > 0,
    `${tmax1h.used}/${tmax1h.total} · ${tmax1h.reason || ''}`);
  const tmin3h = F.stationFieldStats(F.stationSpec('tmin', '3h'), ST.stations, gen);
  ok('tmin+3h reale: campo temp_min_3h_c disponibile con N>0', tmin3h.available === true && tmin3h.used > 0,
    `${tmin3h.used}/${tmin3h.total} · ${tmin3h.reason || ''}`);
  ok('temperatura reale: usate <= con il campo', stTemp.used <= stTemp.total);
  const stRh = F.stationFieldStats(F.stationSpec('rh'), ST.stations, gen);
  ok('umidita relativa reale: disponibile con N>100', stRh.available === true && stRh.used > 100,
    `${stRh.used}/${stRh.total}`);
  const stRain = F.stationFieldStats(F.stationSpec('rain1h'), ST.stations, gen);
  ok('pioggia 1h reale: disponibile, copertura applicata', stRain.available === true && stRain.used <= stRain.total,
    `${stRain.used}/${stRain.total}`);
  ok('ogni stats porta il campo usato', stRain.field === 'rain_1h_mm' && stRh.field === 'relative_humidity_2m');

  // ---- valori e ora del picco
  ok('stationValueOf legge il campo esatto', F.stationValueOf({ temperature_2m: 7.5 }, F.stationSpec('temp')) === 7.5);
  ok('stationValueOf: campo assente -> null', F.stationValueOf({}, F.stationSpec('tmax', 'day')) === null);
  ok('stationValueOf: copertura 0,4 -> null',
    F.stationValueOf({ rain_1h_mm: 2, rain_1h_coverage_frac: 0.4 }, F.stationSpec('rain1h')) === null);
  ok('stationValueOf: copertura 0,5 -> valore',
    F.stationValueOf({ rain_1h_mm: 2, rain_1h_coverage_frac: 0.5 }, F.stationSpec('rain1h')) === 2);
  ok('peak hour ISO -> HH:MM (Europe/Rome)', F.stationPeakHourLabel('2026-11-24T14:00:00Z') === '15:00',
    F.stationPeakHourLabel('2026-11-24T14:00:00Z'));
  ok('peak hour "14:05" -> "14:05"', F.stationPeakHourLabel('14:05') === '14:05');
  ok('peak hour numero 14 -> "14:00"', F.stationPeakHourLabel(14) === '14:00');
  ok('peak hour numero 9 -> "09:00"', F.stationPeakHourLabel(9) === '09:00');
  ok('peak hour ignoto -> null (nessuna ora inventata)',
    F.stationPeakHourLabel('boh') === null && F.stationPeakHourLabel(999) === null);

  // ---- tmax+day e tmin+day su un caso sintetico (forma del blob giornaliero)
  const fake = [
    { name: 'A', obs_time: '2026-11-23T20:00:00Z', temp_max_day_c: 12.5, temp_day_partial: true,
      temp_max_day_hour: '13', temp_day_coverage_frac: 0.9 },
    { name: 'B', obs_time: '2026-11-23T20:00:00Z', temp_max_day_c: 9.0, temp_day_partial: true,
      temp_max_day_hour: '11', temp_day_coverage_frac: 0.7 },
    { name: 'C', obs_time: '2026-11-23T20:00:00Z' }
  ];
  const fs1 = F.stationFieldStats(F.stationSpec('tmax', 'day'), fake, '2026-11-23T20:10:00Z');
  ok('tmax+day sintetico: 2 con il campo, 2 usabili, 2 parziali',
    fs1.total === 2 && fs1.used === 2 && fs1.partial === 2,
    JSON.stringify({ total: fs1.total, used: fs1.used, partial: fs1.partial }));
  ok('tmax+day sintetico: top = A 12,5 con picco 13:00',
    !!fs1.top && fs1.top.name === 'A' && fs1.top.value === 12.5 && fs1.top.peak === '13:00', JSON.stringify(fs1.top));
  ok('tmax+day sintetico: la parzialita\u2019 non blocca il disegno (finestra dichiarata)',
    fs1.available === true, fs1.reason || '');
  ok('tmax+day sintetico: copertura media 80%', fs1.coverageAvg !== null && Math.abs(fs1.coverageAvg - 0.8) < 1e-9,
    String(fs1.coverageAvg));
  const fs3 = F.stationFieldStats(F.stationSpec('tmin', 'day'),
    fake.map((x) => Object.assign({}, x, { temp_min_day_c: 3, temp_min_day_hour: 5 })), '2026-11-23T20:10:00Z');
  ok('tmin+day sintetico: campo e picco numerico', fs3.available === true && fs3.top && fs3.top.peak === '05:00',
    JSON.stringify(fs3.top));
  const stale = F.stationFieldStats(F.stationSpec('tmax', 'day'),
    [{ name: 'S', obs_time: '2026-11-23T10:00:00Z', temp_max_day_c: 20 }], '2026-11-23T20:10:00Z');
  ok('osservazione piu vecchia di 90 min: scartata', stale.used === 0 && stale.total === 1, JSON.stringify({ used: stale.used, total: stale.total }));
  ok('stationIsFresh: 60 min vero, 120 min falso',
    F.stationIsFresh('2026-11-23T19:10:00Z', '2026-11-23T20:10:00Z') === true &&
    F.stationIsFresh('2026-11-23T18:10:00Z', '2026-11-23T20:10:00Z') === false);

  // ---- umidita: classi dichiarate
  ok('umidita: 6 classi 0-100 %', F.HUMIDITY_CLASSES.length === 6);
  ok('umidita: 0 % e 100 % su colori diversi', F.humidityColor(0) !== F.humidityColor(100));
  ok('umidita: 50 % nella classe 40-60', F.humidityClass(50) === 2);
  ok('umidita: valore nullo -> nessun colore di classe', F.humidityColor(null) === '#475569');

  // ---- UI: i due selettori
  const varSel = /<select id="var-select"[\s\S]*?<\/select>/.exec(html)[0];
  ok('8 opzioni nel selettore variabile', (varSel.match(/<option /g) || []).length === 8,
    String((varSel.match(/<option /g) || []).length));
  ok('opzioni tmax/tmin/rh/rain1h presenti',
    ['tmax', 'tmin', 'rh', 'rain1h'].every((v) => new RegExp('value="' + v + '"').test(varSel)));
  ok('selettore variabile con title', /title="[^"]{20,}"/.test(varSel.split('>')[0]));
  const winSel = /<select id="station-window-select"[\s\S]*?<\/select>/.exec(html)[0];
  ok('finestra: day/1h/3h', ['day', '1h', '3h'].every((v) => new RegExp('value="' + v + '"').test(winSel)));
  ok('finestra: etichetta "Oggi (dalle 00:00)"', /Oggi \(dalle 00:00\)/.test(winSel));
  ok('finestra: title e onchange', /title="[^"]{20,}"/.test(winSel) && /onchange="stationsWindowChanged/.test(winSel));
}

console.log(`\nRESULT: ${failures === 0 ? 'PASS' : 'FAIL'} (${failures} errori)`);
process.exit(failures === 0 ? 0 : 1);
