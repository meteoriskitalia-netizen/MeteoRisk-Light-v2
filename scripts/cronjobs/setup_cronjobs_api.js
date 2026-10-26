/**
 * MeteoRisk Light - Setup automatico dei cronjob su cron-job.org (REST API)
 *
 * FONTE DI VERITA' DEGLI ORARI: `scripts/cronjobs/cronjobs_config.json`.
 * In questo file NON esiste nessuna costante di orario: ore, minuti, fuso,
 * `enabled`, titolo e payload arrivano TUTTI dal config. Le costanti PRE-misura
 * (`[0,3,6,9,12,15,18,21]`, `[7,19]`, `[2,8,14,20]`, ...) sono state rimosse:
 * erano il difetto per cui rieseguire lo script riportava i job agli orari
 * vecchi (residuo chiuso il 2026-10-24, riconfermato con la tabella T17).
 *
 * IDEMPOTENZA: prima di ogni scrittura lo script confronta il job vivo con il
 * config (url, titolo, enabled, saveResponses, requestMethod, schedule
 * hours/minutes/timezone/mdays/months/wdays). Se non c'e' differenza NON fa
 * nessuna chiamata di scrittura: con la tabella gia' applicata il piano e'
 * "0 modifiche" e il secondo passaggio non tocca niente.
 *
 * TOKEN GITHUB: per i job NUOVI gli header (con `Authorization`) vengono
 * copiati da un job esistente dello STESSO workflow, quindi non serve avere il
 * token in ambiente; se `GH_TOKEN`/`GITHUB_TOKEN` e' impostato, quello vince.
 * Le PATCH di sola schedulazione non toccano gli header: il token non viene
 * mai riscritto, ne' stampato.
 *
 * Utilizzo:
 *   node scripts/cronjobs/setup_cronjobs_api.js --dry-run        # nessuna rete
 *   node scripts/cronjobs/setup_cronjobs_api.js --diff           # solo lettura
 *   node scripts/cronjobs/setup_cronjobs_api.js --diff --check-body
 *   CRONJOB_API_KEY=... node scripts/cronjobs/setup_cronjobs_api.js
 */

const path = require('path');

const CONFIG = require(path.join(__dirname, 'cronjobs_config.json'));

const ARGS = process.argv.slice(2);
const DRY_RUN = ARGS.includes('--dry-run');
const DIFF_ONLY = ARGS.includes('--diff');
const CHECK_BODY = ARGS.includes('--check-body');
const CRONJOB_API_KEY = process.env.CRONJOB_API_KEY;
const GH_TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
const API_ROOT = 'https://api.cron-job.org';

if (!DRY_RUN && !CRONJOB_API_KEY) {
  console.error('ERRORE: manca CRONJOB_API_KEY (la API key di cron-job.org).');
  console.error('  PowerShell: $env:CRONJOB_API_KEY="..."');
  console.error('  bash:       export CRONJOB_API_KEY="..."');
  console.error('Per vedere il piano senza rete: node scripts/cronjobs/setup_cronjobs_api.js --dry-run');
  process.exit(1);
}

const REPO = CONFIG.repository;
const API_BASE = CONFIG.base_url;

// ---------------------------------------------------------------------------
// Config -> job. Un errore di schema ferma lo script PRIMA di toccare l'API
// (meglio nessun aggiornamento che un job con orari sbagliati).
// ---------------------------------------------------------------------------
function buildJobs(config) {
  const jobs = [];
  const seen = new Set();
  for (const job of (config.jobs || [])) {
    const where = `job "${job.id || job.cronjob_title || '(senza id)'}"`;
    if (!job.cronjob_title) throw new Error(`${where}: manca cronjob_title`);
    if (!job.workflow_file) throw new Error(`${where}: manca workflow_file`);
    if (!job.target_endpoint || !/\/dispatches$/.test(job.target_endpoint)) {
      throw new Error(`${where}: target_endpoint deve finire con /dispatches`);
    }
    if (!Array.isArray(job.hours) || job.hours.length === 0) {
      throw new Error(`${where}: hours mancante o vuoto`);
    }
    if (!Array.isArray(job.minutes) || job.minutes.length === 0) {
      throw new Error(`${where}: minutes mancante o vuoto`);
    }
    if (job.hours.some((h) => !Number.isInteger(h) || h < 0 || h > 23)) {
      throw new Error(`${where}: hours fuori range 0..23`);
    }
    if (job.minutes.some((m) => !Number.isInteger(m) || m < 0 || m > 59)) {
      throw new Error(`${where}: minutes fuori range 0..59`);
    }
    if (seen.has(job.cronjob_title)) {
      throw new Error(`${where}: cronjob_title duplicato ("${job.cronjob_title}")`);
    }
    seen.add(job.cronjob_title);
    jobs.push({
      id: job.id,
      name: job.name || job.cronjob_title,
      title: job.cronjob_title,
      workflow: job.workflow_file,
      url: job.target_endpoint,
      hours: job.hours.slice().sort((a, b) => a - b),
      minutes: job.minutes.slice().sort((a, b) => a - b),
      timezone: job.timezone || 'UTC',
      enabled: job.enabled !== false,
      body: JSON.stringify(job.payload || { ref: 'main' }),
      slotRole: job.slot_role || '?',
      scheduleUtc: job.schedule_utc || '',
      margin: job.margin_minimo_min
    });
  }
  return jobs;
}

let JOBS;
try {
  JOBS = buildJobs(CONFIG);
} catch (err) {
  console.error(`ERRORE config scripts/cronjobs/cronjobs_config.json: ${err.message}`);
  process.exit(1);
}
const RETIRED_TITLES = CONFIG.retired_job_titles || [];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

async function api(method, apiPath, body) {
  const headers = { Authorization: `Bearer ${CRONJOB_API_KEY}` };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(API_ROOT + apiPath, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  return { status: res.status, ok: res.ok, data };
}

async function getExistingJobs() {
  const res = await api('GET', '/jobs');
  if (!res.ok) {
    console.error(`ERRORE lettura job esistenti (HTTP ${res.status}):`, res.data);
    process.exit(1);
  }
  return (res.data && res.data.jobs) || [];
}

async function getDetails(jobId) {
  const res = await api('GET', `/jobs/${jobId}`);
  if (!res.ok) return null;
  return (res.data && (res.data.jobDetails || res.data)) || null;
}

function desiredSchedule(job) {
  return {
    timezone: job.timezone,
    hours: job.hours,
    minutes: job.minutes,
    mdays: [-1],
    months: [-1],
    wdays: [-1]
  };
}

function jobDiff(current, job, details) {
  const s = (current && current.schedule) || {};
  const want = desiredSchedule(job);
  const diffs = [];
  if (!same(s.hours || [], want.hours)) diffs.push(`hours ${JSON.stringify(s.hours)} -> ${JSON.stringify(want.hours)}`);
  if (!same(s.minutes || [], want.minutes)) diffs.push(`minutes ${JSON.stringify(s.minutes)} -> ${JSON.stringify(want.minutes)}`);
  if ((s.timezone || 'UTC') !== want.timezone) diffs.push(`timezone ${s.timezone} -> ${want.timezone}`);
  if (!same(s.mdays || [-1], [-1])) diffs.push(`mdays ${JSON.stringify(s.mdays)} -> [-1]`);
  if (!same(s.months || [-1], [-1])) diffs.push(`months ${JSON.stringify(s.months)} -> [-1]`);
  if (!same(s.wdays || [-1], [-1])) diffs.push(`wdays ${JSON.stringify(s.wdays)} -> [-1]`);
  if (current.url !== job.url) diffs.push(`url ${current.url} -> ${job.url}`);
  if (current.title !== job.title) diffs.push(`title "${current.title}" -> "${job.title}"`);
  if (Boolean(current.enabled) !== job.enabled) diffs.push(`enabled ${current.enabled} -> ${job.enabled}`);
  if (current.saveResponses !== true) diffs.push('saveResponses -> true');
  if (current.requestMethod !== 1) diffs.push('requestMethod -> 1 (POST)');
  if (details && details.extendedData && typeof details.extendedData.body === 'string' &&
      details.extendedData.body !== job.body) {
    diffs.push(`body ${details.extendedData.body} -> ${job.body}`);
  }
  return diffs;
}

async function planFor(existingByTitle) {
  const plan = [];
  for (const job of JOBS) {
    const current = existingByTitle.get(job.title);
    if (!current) {
      plan.push({ job, current: null, details: null, action: 'PUT', diffs: ['job assente: da creare'] });
      continue;
    }
    const details = CHECK_BODY ? await getDetails(current.jobId) : null;
    const diffs = jobDiff(current, job, details);
    plan.push({ job, current, details, action: diffs.length ? 'PATCH' : 'SAME', diffs });
  }
  return plan;
}

function describePlan(plan) {
  let changed = 0;
  for (const row of plan) {
    const times = [];
    for (const h of row.job.hours) for (const m of row.job.minutes) times.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
    const tag = row.action === 'SAME' ? 'OK   ' : row.action === 'PUT' ? 'NEW  ' : 'PATCH';
    if (row.action !== 'SAME') changed++;
    console.log(`${tag} ${row.current ? `#${row.current.jobId} ` : ''}${row.job.title}`);
    console.log(`      workflow : ${row.job.workflow}`);
    console.log(`      orari UTC: ${times.join(', ')}  (${times.length}/giorno, ${row.job.slotRole}, margine ${row.job.margin}')`);
    if (row.diffs.length) console.log(`      azione   : ${row.diffs.join(' | ')}`);
  }
  return changed;
}

// ---------------------------------------------------------------------------
// Header per un job nuovo: GH_TOKEN se c'e', altrimenti copia quelli di un job
// vivo dello stesso workflow (il token resta dov'e' e non viene stampato).
// ---------------------------------------------------------------------------
const HEADER_CACHE = new Map();

async function headersFor(job, existingJobs) {
  if (GH_TOKEN) {
    return {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${GH_TOKEN}`,
      'Content-Type': 'application/json',
      'X-GitHub-Api-Version': '2022-11-28'
    };
  }
  if (HEADER_CACHE.has(job.url)) return HEADER_CACHE.get(job.url);
  // il listing /jobs non porta extendedData: serve GET /jobs/<id> del template
  for (const candidate of existingJobs.filter((j) => j.url === job.url)) {
    const details = await getDetails(candidate.jobId);
    const headers = details && details.extendedData && details.extendedData.headers;
    if (headers && headers.Authorization) {
      const copy = Object.assign({}, headers);
      HEADER_CACHE.set(job.url, copy);
      return copy;
    }
  }
  throw new Error(`nessun job esistente per ${job.workflow} con Authorization negli header, e GH_TOKEN non impostato`);
}

async function patchJob(row, existingJobs) {
  const { job, current, details, diffs } = row;
  const s = current.schedule || {};
  const patch = {};
  // cron-job.org accetta la `schedule` come OGGETTO INTERO (non i singoli campi):
  // se cambia un solo minuto si rimanda tutta la schedule.
  const scheduleChanged =
    !same(s.hours || [], job.hours) || !same(s.minutes || [], job.minutes) ||
    (s.timezone || 'UTC') !== job.timezone || !same(s.mdays || [-1], [-1]) ||
    !same(s.months || [-1], [-1]) || !same(s.wdays || [-1], [-1]);
  if (scheduleChanged) patch.schedule = desiredSchedule(job);
  if (current.url !== job.url) patch.url = job.url;
  if (current.title !== job.title) patch.title = job.title;
  if (Boolean(current.enabled) !== job.enabled) patch.enabled = job.enabled;
  if (current.saveResponses !== true) patch.saveResponses = true;
  if (current.requestMethod !== 1) patch.requestMethod = 1;
  if (details && details.extendedData && details.extendedData.body !== job.body) {
    patch.extendedData = { headers: await headersFor(job, existingJobs), body: job.body };
  }

  console.log(`PATCH #${current.jobId} "${job.title}" -> ${diffs.join(' | ')}`);
  const res = await api('PATCH', `/jobs/${current.jobId}`, { job: patch });
  if (!res.ok) {
    console.error(`  ERRORE PATCH (HTTP ${res.status}):`, res.data);
    return false;
  }
  console.log(`  OK (HTTP ${res.status})`);
  return true;
}

async function putJob(row, existingJobs) {
  const { job } = row;
  const payload = {
    job: {
      url: job.url,
      enabled: job.enabled,
      title: job.title,
      saveResponses: true,
      schedule: desiredSchedule(job),
      requestMethod: 1, // POST
      extendedData: { headers: await headersFor(job, existingJobs), body: job.body }
    }
  };
  console.log(`PUT    "${job.title}" (${job.workflow})`);
  const res = await api('PUT', '/jobs', payload);
  if (!res.ok) {
    console.error(`  ERRORE PUT (HTTP ${res.status}):`, res.data);
    return false;
  }
  const id = res.data && (res.data.jobId || (res.data.job && res.data.job.jobId));
  console.log(`  OK (HTTP ${res.status}), nuovo jobId ${id}`);
  return true;
}

async function disableRetired(title, current) {
  console.log(`PATCH #${current.jobId} "${title}" -> enabled=false (titolo superato)`);
  const res = await api('PATCH', `/jobs/${current.jobId}`, { job: { enabled: false } });
  if (!res.ok) {
    console.error(`  ERRORE (HTTP ${res.status}):`, res.data);
    console.error('  -> disabilitare a mano dalla dashboard cron-job.org.');
    return false;
  }
  console.log(`  OK (HTTP ${res.status})`);
  return true;
}

async function main() {
  console.log('=== Configurazione Automatica Cron-Job.org per MeteoRisk Light ===');
  console.log(`Config: scripts/cronjobs/cronjobs_config.json (${JOBS.length} job, repository ${REPO})`);
  if (DRY_RUN) {
    console.log('MODALITA DRY-RUN: nessuna chiamata di rete.\n');
    for (const job of JOBS) {
      const times = [];
      for (const h of job.hours) for (const m of job.minutes) times.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
      console.log(`- ${job.title}  [${job.slotRole}]`);
      console.log(`  nome     : ${job.name}`);
      console.log(`  workflow : ${job.workflow}`);
      console.log(`  url      : ${job.url}`);
      console.log(`  cron     : ${job.scheduleUtc}  (${job.timezone})`);
      console.log(`  orari    : ${times.join(', ')}  (${times.length}/giorno)`);
      console.log(`  body     : ${job.body}`);
      console.log(`  margine  : ${job.margin}'`);
    }
    if (RETIRED_TITLES.length) console.log(`\nDa disabilitare se presenti: ${RETIRED_TITLES.join(' | ')}`);
    return;
  }

  const existing = await getExistingJobs();
  const existingByTitle = new Map(existing.map((j) => [j.title, j]));
  console.log(`Job presenti su cron-job.org: ${existing.length}\n`);

  const plan = await planFor(existingByTitle);
  const changed = describePlan(plan);
  const nNew = plan.filter((r) => r.action === 'PUT').length;
  const nPatch = plan.filter((r) => r.action === 'PATCH').length;
  const nSame = plan.filter((r) => r.action === 'SAME').length;
  console.log(`\nPiano: ${nNew} nuovi, ${nPatch} da aggiornare, ${nSame} gia' allineati.`);

  if (DIFF_ONLY) {
    console.log('MODALITA DIFF: nessuna scrittura.');
    return;
  }
  if (changed === 0) {
    console.log('\nNessuna modifica da applicare (idempotente).');
  } else {
    let ok = 0;
    for (let i = 0; i < plan.length; i++) {
      const row = plan[i];
      if (row.action === 'SAME') continue;
      const done = row.action === 'PUT' ? await putJob(row, existing) : await patchJob(row, existing);
      if (done) ok++;
      if (i < plan.length - 1) await sleep(row.action === 'PUT' ? 13000 : 1500);
    }
    console.log(`\nApplicati ${ok} job su ${changed} da cambiare.`);
  }

  let retired = 0;
  for (const title of RETIRED_TITLES) {
    const current = existingByTitle.get(title);
    if (!current) {
      console.log(`Job superato "${title}" non presente: niente da disabilitare.`);
      continue;
    }
    if (current.enabled === false) {
      console.log(`Job superato "${title}" gia' disabilitato (#${current.jobId}).`);
      retired++;
      continue;
    }
    await sleep(1500);
    if (await disableRetired(title, current)) retired++;
  }
  if (RETIRED_TITLES.length) console.log(`Job superati disabilitati: ${retired} su ${RETIRED_TITLES.length}.`);

  // Verifica: rilegge e confronta, non si fida della risposta delle PATCH.
  const after = await getExistingJobs();
  const afterByTitle = new Map(after.map((j) => [j.title, j]));
  let mismatched = 0;
  for (const job of JOBS) {
    const cur = afterByTitle.get(job.title);
    if (!cur) { console.error(`VERIFICA FALLITA: "${job.title}" non esiste dopo l'applicazione`); mismatched++; continue; }
    if (CHECK_BODY) {
      const details = await getDetails(cur.jobId);
      const diffs = jobDiff(cur, job, details);
      if (diffs.length) { console.error(`VERIFICA FALLITA: "${job.title}" -> ${diffs.join(' | ')}`); mismatched++; }
    } else {
      const diffs = jobDiff(cur, job, null);
      if (diffs.length) { console.error(`VERIFICA FALLITA: "${job.title}" -> ${diffs.join(' | ')}`); mismatched++; }
    }
  }
  for (const title of RETIRED_TITLES) {
    const cur = afterByTitle.get(title);
    if (cur && cur.enabled !== false) { console.error(`VERIFICA FALLITA: "${title}" e' ancora attivo`); mismatched++; }
  }
  console.log(mismatched === 0
    ? `\nVERIFICA OK: ${JOBS.length} job del config allineati, ${RETIRED_TITLES.length} titoli superati disabilitati.`
    : `\nVERIFICA: ${mismatched} discrepanze.`);
  if (mismatched) process.exitCode = 1;
}

main().catch((err) => {
  console.error('ERRORE non gestito:', err.message);
  process.exit(1);
});
