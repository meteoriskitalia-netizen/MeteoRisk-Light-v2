/**
 * MeteoRisk Light - Setup automatico dei cronjob su cron-job.org (REST API)
 *
 * FONTE DI VERITA' DEGLI ORARI: `scripts/cronjobs/cronjobs_config.json`.
 * Le ore e i minuti NON sono piu' costanti in questo file (era il difetto della
 * 1.2.4.19: costanti PRE-misura che, rieseguite, avrebbero riportato i job agli
 * orari vecchi). Ogni job del config porta `cronjob_hours` / `cronjob_minutes` /
 * `cronjob_title` / `target_endpoint` / `payload`, e questo script APPLICA
 * esattamente quei valori. Se il config e' sbagliato, lo script e' sbagliato:
 * una sola tabella da mantenere.
 *
 * Tabella P2 (2026-10-24): 2 slot per run = 2 job per provider
 *   - "(parziale)": subito dopo il PRIMO STEP di pubblicazione (run parziale P1)
 *   - "(completo)" : dopo la disponibilita completa del run
 * Il cron nativo dei 5 workflow e' solo rete di sicurezza (+57').
 *
 * Utilizzo:
 *   export CRONJOB_API_KEY="tua_chiave_cronjob_org"
 *   export GH_TOKEN="tuo_pat_github"
 *   node scripts/cronjobs/setup_cronjobs_api.js
 *
 *   node scripts/cronjobs/setup_cronjobs_api.js --dry-run
 *     -> stampa titoli/orari/corpi che verrebbero applicati, senza rete
 *        (non richiede ne' API key ne' token).
 */

const path = require('path');

const CONFIG = require(path.join(__dirname, 'cronjobs_config.json'));

const DRY_RUN = process.argv.includes('--dry-run');
const CRONJOB_API_KEY = process.env.CRONJOB_API_KEY;
const GH_TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;

if (!DRY_RUN && (!CRONJOB_API_KEY || !GH_TOKEN)) {
  console.error('ERRORE: Variabili d\'ambiente mancanti!');
  console.error('Assicurati di impostare sia CRONJOB_API_KEY che GH_TOKEN prima di eseguire lo script:');
  console.error('  export CRONJOB_API_KEY="la_tua_api_key_cronjob"');
  console.error('  export GH_TOKEN="il_tuo_pat_github"');
  console.error('  node scripts/cronjobs/setup_cronjobs_api.js');
  console.error('(oppure: node scripts/cronjobs/setup_cronjobs_api.js --dry-run)');
  process.exit(1);
}

const REPO = CONFIG.repository;
const API_BASE = CONFIG.base_url;

// ---------------------------------------------------------------------------
// Validazione del config: un errore di schema deve fermare lo script PRIMA di
// toccare l'API (meglio nessun aggiornamento che un job con orari sbagliati).
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
    if (!Array.isArray(job.cronjob_hours) || job.cronjob_hours.length === 0) {
      throw new Error(`${where}: cronjob_hours mancante o vuoto`);
    }
    if (!Array.isArray(job.cronjob_minutes) || job.cronjob_minutes.length === 0) {
      throw new Error(`${where}: cronjob_minutes mancante o vuoto`);
    }
    if (job.cronjob_hours.some((h) => !Number.isInteger(h) || h < 0 || h > 23)) {
      throw new Error(`${where}: cronjob_hours fuori range 0..23`);
    }
    if (job.cronjob_minutes.some((m) => !Number.isInteger(m) || m < 0 || m > 59)) {
      throw new Error(`${where}: cronjob_minutes fuori range 0..59`);
    }
    if (seen.has(job.cronjob_title)) {
      throw new Error(`${where}: cronjob_title duplicato ("${job.cronjob_title}")`);
    }
    seen.add(job.cronjob_title);
    jobs.push({
      title: job.cronjob_title,
      workflow: job.workflow_file,
      url: job.target_endpoint,
      hours: job.cronjob_hours,
      minutes: job.cronjob_minutes,
      body: JSON.stringify(job.payload || { ref: 'main' }),
      slotRole: job.slot_role || '?',
      scheduleUtc: job.schedule_utc || ''
    });
  }
  return jobs;
}

let JOBS;
try {
  JOBS = buildJobs(CONFIG);
} catch (err) {
  console.error(`ERRORE config ${'scripts/cronjobs/cronjobs_config.json'}: ${err.message}`);
  process.exit(1);
}
const RETIRED_TITLES = CONFIG.retired_job_titles || [];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function getExistingJobs() {
  try {
    const res = await fetch('https://api.cron-job.org/jobs', {
      headers: { 'Authorization': `Bearer ${CRONJOB_API_KEY}` }
    });
    if (!res.ok) {
      const errText = await res.text().catch(() => '');
      console.error(`ERRORE lettura job esistenti (HTTP ${res.status}): ${errText}`);
      process.exit(1);
    }
    const text = await res.text();
    if (!text) return [];
    const data = JSON.parse(text);
    return data.jobs || [];
  } catch (e) {
    console.error(`ERRORE di rete durante getExistingJobs: ${e.message}`);
    process.exit(1);
  }
}

function scheduleOf(jobConfig) {
  return {
    timezone: 'UTC',
    hours: jobConfig.hours,
    minutes: jobConfig.minutes,
    mdays: [-1],
    months: [-1],
    wdays: [-1]
  };
}

function extendedDataOf(jobConfig) {
  return {
    headers: {
      'Accept': 'application/vnd.github+json',
      'Authorization': `Bearer ${GH_TOKEN}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json'
    },
    body: jobConfig.body
  };
}

async function createOrUpdateJob(jobConfig, existingByTitle) {
  const url = jobConfig.url;
  const schedule = scheduleOf(jobConfig);
  const extendedData = extendedDataOf(jobConfig);

  const existingJob = existingByTitle.get(jobConfig.title);
  if (existingJob && existingJob.jobId) {
    console.log(`Aggiornamento job esistente: "${jobConfig.title}" (ID: ${existingJob.jobId}, ${jobConfig.workflow})...`);
    try {
      const res = await fetch(`https://api.cron-job.org/jobs/${existingJob.jobId}`, {
        method: 'PATCH',
        headers: {
          'Authorization': `Bearer ${CRONJOB_API_KEY}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          job: {
            url: url,
            enabled: true,
            title: jobConfig.title,
            saveResponses: true,
            schedule: schedule,
            requestMethod: 1, // POST
            extendedData: extendedData
          }
        })
      });
      if (res.ok) {
        console.log(`OK: job aggiornato (HTTP ${res.status}), ID ${existingJob.jobId}\n`);
        return { ok: true, method: 'PATCH' };
      }
      const errText = await res.text().catch(() => '');
      console.error(`ERRORE aggiornamento job (HTTP ${res.status}): ${errText}\n`);
      return { ok: false, method: 'PATCH' };
    } catch (err) {
      console.error('ERRORE di rete durante PATCH:', err.message);
      return { ok: false, method: 'PATCH' };
    }
  }

  const jobData = {
    job: {
      url: url,
      enabled: true,
      title: jobConfig.title,
      saveResponses: true,
      schedule: schedule,
      requestMethod: 1, // POST
      extendedData: extendedData
    }
  };

  console.log(`Creazione job: "${jobConfig.title}" (${jobConfig.workflow})...`);

  try {
    const res = await fetch('https://api.cron-job.org/jobs', {
      method: 'PUT',
      headers: {
        'Authorization': `Bearer ${CRONJOB_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(jobData)
    });

    const text = await res.text();
    if (!text) {
      console.error(`ERRORE: risposta vuota dal server (HTTP ${res.status})\n`);
      return { ok: false, method: 'PUT' };
    }

    const data = JSON.parse(text);
    if (res.ok && (data.jobId || data.job)) {
      const id = data.jobId || (data.job && data.job.jobId);
      console.log(`OK: job creato (HTTP ${res.status}), ID ${id}\n`);
      return { ok: true, method: 'PUT' };
    }
    console.error(`ERRORE creazione job (HTTP ${res.status}):`, data);
    return { ok: false, method: 'PUT' };
  } catch (err) {
    console.error('ERRORE di rete:', err.message);
    return { ok: false, method: 'PUT' };
  }
}

/**
 * I job di una tabella superata restano attivi finche' non vengono disabilitati:
 * senza questo passaggio gli slot VECCHI continuerebbero a dispatchare (due run
 * per lo stesso run nominale, uno con orari sbagliati). Si DISABILITA (non si
 * cancella): reversibile e senza perdita di storico risposte.
 */
async function disableRetiredJob(title, existingJob) {
  console.log(`Disabilitazione job superato: "${title}" (ID: ${existingJob.jobId})...`);
  try {
    const res = await fetch(`https://api.cron-job.org/jobs/${existingJob.jobId}`, {
      method: 'PATCH',
      headers: {
        'Authorization': `Bearer ${CRONJOB_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ job: { enabled: false } })
    });
    if (res.ok) {
      console.log(`OK: job superato disabilitato (HTTP ${res.status}), ID ${existingJob.jobId}\n`);
      return true;
    }
    const errText = await res.text().catch(() => '');
    console.error(`ERRORE disabilitazione (HTTP ${res.status}): ${errText}`);
    console.error('  -> disabilitare a mano dalla dashboard cron-job.org.\n');
    return false;
  } catch (err) {
    console.error('ERRORE di rete durante la disabilitazione:', err.message);
    console.error('  -> disabilitare a mano dalla dashboard cron-job.org.\n');
    return false;
  }
}

async function run() {
  console.log('=== Configurazione Automatica Cron-Job.org per MeteoRisk Light ===');
  console.log(`Config: scripts/cronjobs/cronjobs_config.json (${JOBS.length} job)`);
  if (DRY_RUN) {
    console.log('MODALITA DRY-RUN: nessuna chiamata di rete.\n');
    for (const job of JOBS) {
      const pad = (n) => String(n).padStart(2, '0');
      const times = [];
      for (const h of job.hours) for (const m of job.minutes) times.push(`${pad(h)}:${pad(m)}`);
      console.log(`- ${job.title}  [${job.slotRole}]`);
      console.log(`  workflow : ${job.workflow}`);
      console.log(`  url      : ${job.url}`);
      console.log(`  cron     : ${job.scheduleUtc}`);
      console.log(`  orari UTC: ${times.join(', ')}  (${times.length}/giorno)`);
      console.log(`  body     : ${job.body}`);
    }
    if (RETIRED_TITLES.length) {
      console.log(`\nJob da disabilitare (tabella superata): ${RETIRED_TITLES.join(' | ')}`);
    }
    return;
  }

  const existing = await getExistingJobs();
  const existingByTitle = new Map(existing.map((j) => [j.title, j]));
  console.log(`Job attualmente configurati su cron-job.org: ${existing.length}\n`);

  let successCount = 0;
  for (let i = 0; i < JOBS.length; i++) {
    const job = JOBS[i];
    const res = await createOrUpdateJob(job, existingByTitle);
    if (res.ok) successCount++;
    if (i < JOBS.length - 1) {
      await sleep(res.method === 'PUT' ? 13000 : 1500);
    }
  }
  console.log(`\nCompletato: ${successCount} su ${JOBS.length} cronjob configurati.`);

  let retired = 0;
  for (let i = 0; i < RETIRED_TITLES.length; i++) {
    const title = RETIRED_TITLES[i];
    const job = existingByTitle.get(title);
    if (!job || !job.jobId) {
      console.log(`Job superato "${title}" non presente: niente da disabilitare.`);
      continue;
    }
    if (job.enabled === false) {
      console.log(`Job superato "${title}" gia' disabilitato.`);
      retired++;
      continue;
    }
    await sleep(1500);
    if (await disableRetiredJob(title, job)) retired++;
  }
  if (RETIRED_TITLES.length) {
    console.log(`Job superati disabilitati: ${retired} su ${RETIRED_TITLES.length}.`);
  }
}

run();
