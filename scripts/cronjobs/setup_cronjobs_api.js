/**
 * MeteoRisk Light - Setup automatico cronjob su cron-job.org tramite REST API
 * 
 * Utilizzo:
 *   export CRONJOB_API_KEY="tua_chiave_cronjob_org"
 *   export GH_TOKEN="tuo_pat_github"
 *   node scripts/cronjobs/setup_cronjobs_api.js
 */

const CRONJOB_API_KEY = process.env.CRONJOB_API_KEY;
const GH_TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;

if (!CRONJOB_API_KEY || !GH_TOKEN) {
  console.error('\x1b[31mERRORE: Variabili d\'ambiente mancanti!\x1b[0m');
  console.error('Assicurati di impostare sia CRONJOB_API_KEY che GH_TOKEN prima di eseguire lo script:');
  console.error('  export CRONJOB_API_KEY="la_tua_api_key_cronjob"');
  console.error('  export GH_TOKEN="il_tuo_pat_github"');
  console.error('  node scripts/cronjobs/setup_cronjobs_api.js');
  process.exit(1);
}

const REPO = 'meteoriskitalia-netizen/MeteoRisk-Light-v2';
const API_BASE = `https://api.github.com/repos/${REPO}/actions/workflows`;

const JOBS = [
  {
    title: 'MeteoRisk - DWD ICON EU + D2',
    workflow: 'forecast-maps-dwd.yml',
    hours: [0, 3, 6, 9, 12, 15, 18, 21],
    minutes: [30],
    hasForceInput: true
  },
  {
    title: 'MeteoRisk - DWD ICON Global',
    workflow: 'forecast-maps-dwdicon.yml',
    hours: [4, 10, 16, 22],
    minutes: [30],
    hasForceInput: true
  },
  {
    title: 'MeteoRisk - ARPEGE 0.25',
    workflow: 'forecast-maps-meteofrance.yml',
    hours: [4, 10, 16, 22],
    minutes: [15],
    hasForceInput: true
  },
  {
    title: 'MeteoRisk - NCEP GFS',
    workflow: 'forecast-maps-ncep.yml',
    hours: [0, 6, 12, 18],
    minutes: [15],
    hasForceInput: true
  },
  {
    title: 'MeteoRisk - ECMWF IFS 0.25',
    workflow: 'forecast-maps-ecmwf.yml',
    hours: [7, 19],
    minutes: [45],
    hasForceInput: true
  },
  {
    title: 'MeteoRisk - WebP Index Backup',
    workflow: 'forecast-maps-index.yml',
    hours: [2, 8, 14, 20],
    minutes: [50],
    hasForceInput: false
  }
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function getExistingJobs() {
  try {
    const res = await fetch('https://api.cron-job.org/jobs', {
      headers: { 'Authorization': `Bearer ${CRONJOB_API_KEY}` }
    });
    if (!res.ok) {
      const errText = await res.text().catch(() => '');
      console.error(`\x1b[31m✖ Errore lettura job esistenti (HTTP ${res.status}):\x1b[0m ${errText}`);
      process.exit(1);
    }
    const text = await res.text();
    if (!text) return [];
    const data = JSON.parse(text);
    return data.jobs || [];
  } catch (e) {
    console.error(`\x1b[31m✖ Errore di rete durante getExistingJobs:\x1b[0m ${e.message}`);
    process.exit(1);
  }
}

async function createOrUpdateJob(jobConfig, existingByTitle) {
  const url = `${API_BASE}/${jobConfig.workflow}/dispatches`;
  const bodyPayload = jobConfig.hasForceInput !== false
    ? JSON.stringify({ ref: 'main', inputs: { force_update: false } })
    : JSON.stringify({ ref: 'main' });

  const schedule = {
    timezone: 'UTC',
    hours: jobConfig.hours,
    minutes: jobConfig.minutes,
    mdays: [-1],
    months: [-1],
    wdays: [-1]
  };

  const extendedData = {
    headers: {
      'Accept': 'application/vnd.github+json',
      'Authorization': `Bearer ${GH_TOKEN}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json'
    },
    body: bodyPayload
  };

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
            requestMethod: 1,
            extendedData: extendedData
          }
        })
      });
      if (res.ok) {
        console.log(`\x1b[32m✔ Job aggiornato con successo! ID: ${existingJob.jobId}\x1b[0m\n`);
        return { ok: true, method: 'PATCH' };
      }
      const errText = await res.text().catch(() => '');
      console.error(`\x1b[31m✖ Errore aggiornamento job (HTTP ${res.status}):\x1b[0m ${errText}\n`);
      return { ok: false, method: 'PATCH' };
    } catch (err) {
      console.error(`\x1b[31m✖ Errore di rete durante PATCH:\x1b[0m`, err.message);
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
      console.error(`\x1b[31m✖ Risposta vuota dal server (HTTP ${res.status})\x1b[0m\n`);
      return { ok: false, method: 'PUT' };
    }

    const data = JSON.parse(text);
    if (res.ok && (data.jobId || data.job)) {
      const id = data.jobId || (data.job && data.job.jobId);
      console.log(`\x1b[32m✔ Job creato con successo! ID: ${id}\x1b[0m\n`);
      return { ok: true, method: 'PUT' };
    } else {
      console.error(`\x1b[31m✖ Errore creazione job (HTTP ${res.status}):\x1b[0m`, data);
      return { ok: false, method: 'PUT' };
    }
  } catch (err) {
    console.error(`\x1b[31m✖ Errore di rete:\x1b[0m`, err.message);
    return { ok: false, method: 'PUT' };
  }
}

async function run() {
  console.log('=== Configurazione Automatica Cron-Job.org per MeteoRisk Light ===\n');
  const existing = await getExistingJobs();
  const existingByTitle = new Map(existing.map(j => [j.title, j]));
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
}

run();

