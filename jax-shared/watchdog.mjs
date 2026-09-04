/**
 * JAX 24/7 Autonomous Watchdog & Sentinel Daemon (Enhanced Resiliency)
 * 
 * Standalone Node.js monitor:
 * 1. Monitors PM2 process state (Telegram & WhatsApp) with PID fallback
 * 2. Probes HTTP health endpoint (http://127.0.0.1:9090/health)
 * 3. Independent service tracking: never blanket-restarts healthy services
 * 4. High failure threshold (5 consecutive fails = 2.5 mins) to prevent false positives during heavy AI inference
 * 5. Sends Telegram alerts to the administrator upon failure/recovery
 */

import http from 'http';
import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const execAsync = promisify(exec);

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const OWNER_TELEGRAM_ID = process.env.OWNER_USER_ID || '';
const HEALTH_URL = 'http://127.0.0.1:9090/health';
const CHECK_INTERVAL_MS = 30000; // Check every 30 seconds
const FAILURES_BEFORE_RESTART = 5; // Require 5 consecutive fails (2.5 minutes) before auto-restart

const PM2_BIN = process.env.PM2_BIN || (process.env.HOME ? `${process.env.HOME}/.local/bin/pm2` : 'pm2');
const EXEC_OPTS = {
  env: {
    ...process.env,
    PM2_HOME: process.env.PM2_HOME || (process.env.HOME ? `${process.env.HOME}/.pm2` : undefined),
    PATH: `${process.env.HOME || ''}/.local/bin:/usr/local/bin:/usr/bin:/bin`
  }
};

let tgFailures = 0;
let waFailures = 0;
let apiFailures = 0;
let lastAlertSentAt = 0;
const ALERT_COOLDOWN_MS = 10 * 60 * 1000; // Max 1 alert every 10 min

async function sendTelegramAlert(message) {
  const now = Date.now();
  if (now - lastAlertSentAt < ALERT_COOLDOWN_MS) {
    return;
  }
  lastAlertSentAt = now;

  try {
    const text = encodeURIComponent(`🚨 *JAX 24/7 Sentinel Alert*\n\n${message}`);
    const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage?chat_id=${OWNER_TELEGRAM_ID}&text=${text}&parse_mode=Markdown`;
    await execAsync(`curl -s "${url}"`);
    console.log('[WATCHDOG ALERT SENT TO TELEGRAM]');
  } catch (err) {
    console.error('[WATCHDOG ALERT FAILED]:', err.message);
  }
}

function checkPidAlive(pidFilePath) {
  try {
    if (fs.existsSync(pidFilePath)) {
      const pid = parseInt(fs.readFileSync(pidFilePath, 'utf-8').trim(), 10);
      if (pid) {
        process.kill(pid, 0);
        return true;
      }
    }
  } catch (e) {}
  return false;
}

function checkHealthEndpoint() {
  return new Promise((resolve) => {
    const req = http.get(HEALTH_URL, { timeout: 8000 }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          resolve({ ok: res.statusCode === 200, data: json });
        } catch (e) {
          resolve({ ok: false, error: 'Invalid JSON response' });
        }
      });
    });

    req.on('error', (err) => {
      resolve({ ok: false, error: err.message });
    });

    req.on('timeout', () => {
      req.destroy();
      resolve({ ok: false, error: 'Health endpoint timeout' });
    });
  });
}

async function checkProcesses() {
  let telegramOnline = false;
  let whatsappOnline = false;

  try {
    const { stdout } = await execAsync(`${PM2_BIN} jlist`, EXEC_OPTS);
    const startIdx = stdout.indexOf('[');
    const endIdx = stdout.lastIndexOf(']');
    
    if (startIdx !== -1 && endIdx !== -1) {
      const jsonStr = stdout.substring(startIdx, endIdx + 1);
      const apps = JSON.parse(jsonStr);
      for (const app of apps) {
        if (app.name === 'jax-telegram' && app.pm2_env.status === 'online') {
          telegramOnline = true;
        }
        if (app.name === 'jax-whatsapp' && app.pm2_env.status === 'online') {
          whatsappOnline = true;
        }
      }
    }
  } catch (err) {
    console.warn('[WATCHDOG WARNING] Error querying PM2 CLI, checking PID locks directly...', err.message);
  }

  // Backup check: PID liveness
  if (!telegramOnline) {
    telegramOnline = checkPidAlive('/tmp/jax_telegram_agent.pid');
  }
  if (!whatsappOnline) {
    whatsappOnline = checkPidAlive('/tmp/jax_whatsapp_agent.pid');
  }

  return { telegramOnline, whatsappOnline };
}

async function performHealthCheck() {
  const procs = await checkProcesses();
  const health = await checkHealthEndpoint();

  // Track Telegram failures
  if (!procs.telegramOnline) {
    tgFailures++;
    console.warn(`⚠️ [WATCHDOG] Telegram offline (${tgFailures}/${FAILURES_BEFORE_RESTART})`);
  } else {
    tgFailures = 0;
  }

  // Track WhatsApp failures
  if (!procs.whatsappOnline) {
    waFailures++;
    console.warn(`⚠️ [WATCHDOG] WhatsApp offline (${waFailures}/${FAILURES_BEFORE_RESTART})`);
  } else {
    waFailures = 0;
  }

  // Track Health API failures
  if (!health.ok) {
    apiFailures++;
    console.warn(`⚠️ [WATCHDOG] Health API unresponsive (${apiFailures}/${FAILURES_BEFORE_RESTART}): ${health.error || 'status ' + health.statusCode}`);
  } else {
    apiFailures = 0;
  }

  const actions = [];

  // Restart WhatsApp ONLY if WhatsApp itself is confirmed down for 5 consecutive checks
  if (waFailures >= FAILURES_BEFORE_RESTART) {
    try {
      console.log('🔄 [WATCHDOG AUTO-HEAL] Restarting failed jax-whatsapp service...');
      await execAsync(`${PM2_BIN} restart jax-whatsapp`, EXEC_OPTS);
      actions.push('WhatsApp restarted');
      waFailures = 0;
    } catch (e) {
      console.error('[WATCHDOG RECOVERY ERROR WhatsApp]:', e.message);
    }
  }

  // Restart Telegram ONLY if Telegram or its Health server is confirmed down for 5 consecutive checks
  if (tgFailures >= FAILURES_BEFORE_RESTART || apiFailures >= FAILURES_BEFORE_RESTART) {
    try {
      console.log('🔄 [WATCHDOG AUTO-HEAL] Restarting failed jax-telegram service...');
      await execAsync(`${PM2_BIN} restart jax-telegram`, EXEC_OPTS);
      actions.push('Telegram restarted');
      tgFailures = 0;
      apiFailures = 0;
    } catch (e) {
      console.error('[WATCHDOG RECOVERY ERROR Telegram]:', e.message);
    }
  }

  if (actions.length > 0) {
    await sendTelegramAlert(
      `⚠️ *Isolated Auto-Recovery Triggered*\n` +
      `• Actions taken: ${actions.join(', ')}\n` +
      `• Telegram: ${procs.telegramOnline ? '🟢 OK' : '🔴 Down'}\n` +
      `• WhatsApp: ${procs.whatsappOnline ? '🟢 OK' : '🔴 Down'}\n` +
      `• Health API: ${health.ok ? '🟢 OK' : '🔴 ' + (health.error || 'Failed')}\n\n` +
      `Healthy services were kept running without interruption.`
    );
  }
}

// ===========================
// Dead-Letter Watcher
// ===========================
// Failed bot messages get written to data/deadletters/ but nothing ever consumed
// them — they sat there silently forever. Auto-retrying isn't safe here (a message
// that partially executed a state-changing action before failing could get double-run
// if blindly replayed), so instead: alert the administrator the moment a new one appears so he can
// decide whether to resend it himself. Each dead letter is alerted exactly once.

const DEADLETTERS_DIR = process.env.DEADLETTERS_DIR || path.resolve(__dirname, 'data/deadletters');
const ALERTED_LOG = path.join(DEADLETTERS_DIR, '.alerted.json');

function loadAlertedSet() {
  try {
    return new Set(JSON.parse(fs.readFileSync(ALERTED_LOG, 'utf-8')));
  } catch (e) {
    return new Set();
  }
}

function saveAlertedSet(set) {
  try {
    fs.writeFileSync(ALERTED_LOG, JSON.stringify([...set]));
  } catch (e) {
    console.error('[WATCHDOG] Failed to persist dead-letter alert log:', e.message);
  }
}

async function checkDeadLetters() {
  if (!fs.existsSync(DEADLETTERS_DIR)) return;

  let files;
  try {
    files = fs.readdirSync(DEADLETTERS_DIR).filter(f => f.endsWith('.json') && f !== '.alerted.json');
  } catch (e) {
    return;
  }

  const alerted = loadAlertedSet();
  const newOnes = files.filter(f => !alerted.has(f));
  if (newOnes.length === 0) return;

  for (const f of newOnes) {
    try {
      const data = JSON.parse(fs.readFileSync(path.join(DEADLETTERS_DIR, f), 'utf-8'));
      const promptPreview = (data.prompt || '').slice(0, 200).replace(/\n/g, ' ');
      await sendTelegramAlert(
        `📪 *Dropped Message Needs Your Attention*\n` +
        `• Platform: ${data.platform || 'unknown'}\n` +
        `• From: ${data.userId || 'unknown'}\n` +
        `• Error: ${data.error || 'unknown'}\n` +
        `• Message: "${promptPreview}${promptPreview.length >= 200 ? '...' : ''}"\n\n` +
        `This never got a reply. Resend it yourself if it still matters.`
      );
      alerted.add(f);
    } catch (e) {
      console.error(`[WATCHDOG] Failed to process dead letter ${f}:`, e.message);
    }
  }

  saveAlertedSet(alerted);
}

console.log('🛡️ JAX 24/7 Autonomous Watchdog & Sentinel Active (Resilient Mode).');
setInterval(performHealthCheck, CHECK_INTERVAL_MS);
setInterval(checkDeadLetters, CHECK_INTERVAL_MS);
performHealthCheck();
checkDeadLetters();
