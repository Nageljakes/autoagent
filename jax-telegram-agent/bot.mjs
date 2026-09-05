import { Telegraf } from 'telegraf';
import { spawn } from 'child_process';
import { exec } from 'child_process';
import { promisify } from 'util';
import dotenv from 'dotenv';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import {
  getUserProfile, saveUserProfile, incrementUserStats, addUserFact,
  getConversationHistory, appendConversation, clearConversationHistory, buildContextSummary,
  isDuplicateMessage, checkRateLimit, sanitizeInput,
  saveDeadLetter, checkCircuitBreaker, recordCircuitSuccess, recordCircuitFailure,
  createLogger, recordMessageProcessed, recordError, getHealthStatus,
  acquireExecutionSlot, getSemaphoreStatus, acquireProcessLock,
  trackInFlight, clearInFlight, getUnfinishedInFlight
} from '../jax-shared/memory.mjs';
import { startHealthServer } from '../jax-shared/health.mjs';

dotenv.config();

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Enforce single active instance
acquireProcessLock('jax_telegram_agent');

const log = createLogger('telegram');

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const OWNER_USER_ID = process.env.OWNER_USER_ID || '';
const SALESPERSON_NAME = process.env.SALESPERSON_NAME || 'Sales Executive';
const DEALERSHIP_NAME = process.env.DEALERSHIP_NAME || 'Dealership';
const CRM_USERNAME = process.env.CRM_USERNAME || '';
const CRM_USERNAME_SHORT = CRM_USERNAME.split(/[^a-zA-Z0-9]/)[0] || CRM_USERNAME;
const DEALERSHIP_NAME_ALT = process.env.DEALERSHIP_NAME_ALT || DEALERSHIP_NAME;

const AGY_BIN = process.env.AGY_BIN || 'agy';
const WORKSPACE_DIR = process.env.WORKSPACE_DIR || process.env.HOME || '.';
const AUDIO_PROCESSOR = path.resolve(__dirname, 'audio_processor.py');
const IMAGE_GENERATOR = path.resolve(__dirname, '../jax-shared/image_generator.py');

const execAsync = promisify(exec);

if (!TELEGRAM_BOT_TOKEN) {
  log.error('TELEGRAM_BOT_TOKEN is required in .env');
  process.exit(1);
}

const bot = new Telegraf(TELEGRAM_BOT_TOKEN);

// Active prompt tracking & shutdown state
let activePromptsCount = 0;
let isShuttingDown = false;

// Queue per user to prevent concurrent race conditions
const userQueues = new Map();
const isNewSession = new Map();
const userVoiceMode = new Map(); // userId -> boolean (always send voice note)
const userVoiceSelection = new Map(); // userId -> string voice name
const activeTasks = new Map(); // userIdStr -> { child, aborted: boolean, startTime: number }

function killProcessTree(pid) {
  if (!pid) return;
  try {
    execSync(`pkill -9 -P ${pid} 2>/dev/null || true`);
  } catch (e) {}
  try {
    process.kill(pid, 'SIGKILL');
  } catch (e) {}
}

function interruptTask(userIdStr, isOwnerRequest = false) {
  let stoppedCount = 0;
  if (isOwnerRequest && (!userIdStr || userIdStr === 'all')) {
    for (const [key, task] of activeTasks.entries()) {
      task.aborted = true;
      if (task.child && task.child.pid) {
        killProcessTree(task.child.pid);
        stoppedCount++;
      }
      activeTasks.delete(key);
      userQueues.set(Number(key) || key, Promise.resolve());
    }
    return stoppedCount;
  }

  const task = activeTasks.get(userIdStr);
  if (task) {
    task.aborted = true;
    if (task.child && task.child.pid) {
      killProcessTree(task.child.pid);
      stoppedCount++;
    }
    activeTasks.delete(userIdStr);
  }
  userQueues.set(Number(userIdStr) || userIdStr, Promise.resolve());
  return stoppedCount;
}

const DEFAULT_VOICE = 'en-US-ChristopherNeural';
const AVAILABLE_VOICES = {
  'christopher': 'en-US-ChristopherNeural',
  'guy': 'en-US-GuyNeural',
  'jenny': 'en-US-JennyNeural',
  'aria': 'en-US-AriaNeural',
  'ryan': 'en-GB-RyanNeural',
  'sonia': 'en-GB-SoniaNeural',
  'eric': 'en-US-EricNeural'
};

function loadVipContacts() {
  const configPaths = [
    process.env.VIP_CONTACTS_PATH,
    path.resolve(__dirname, '../config/vip_contacts.json'),
    path.resolve(__dirname, 'vip_contacts.json')
  ].filter(Boolean);

  for (const cfgPath of configPaths) {
    if (fs.existsSync(cfgPath)) {
      try {
        return JSON.parse(fs.readFileSync(cfgPath, 'utf-8'));
      } catch (e) {
        log.warn(`Failed to parse VIP contacts from ${cfgPath}: ${e.message}`);
      }
    }
  }
  return {};
}

const VIP_CONTACTS = loadVipContacts();

function getVipInfo(userId) {
  if (!userId) return null;
  const rawId = String(userId).replace(/[^0-9]/g, '');
  for (const [phone, info] of Object.entries(VIP_CONTACTS)) {
    if (rawId.includes(phone) || phone.includes(rawId)) {
      return info;
    }
  }
  return null;
}

function isOwner(userId) {
  return String(userId) === String(OWNER_USER_ID);
}

function enqueue(userId, fn) {
  if (!userQueues.has(userId)) {
    userQueues.set(userId, Promise.resolve());
  }
  const currentPromise = userQueues.get(userId);
  const nextPromise = currentPromise.then(fn, fn);
  userQueues.set(userId, nextPromise);
  return nextPromise;
}

const OWNER_WORKSPACE = path.resolve(__dirname, 'workspace');
if (!fs.existsSync(OWNER_WORKSPACE)) {
  fs.mkdirSync(OWNER_WORKSPACE, { recursive: true });
}

// Function to call agy CLI with retry on timeout
function runAgyPromptRaw(prompt, userId, continueSession = true) {
  return new Promise((resolve) => {
    const owner = isOwner(userId);
    let targetWorkspace = OWNER_WORKSPACE;

    if (!owner) {
      targetWorkspace = `/tmp/guest_workspaces/${userId}`;
      if (!fs.existsSync(targetWorkspace)) {
        fs.mkdirSync(targetWorkspace, { recursive: true });
      }
    }

    const args = ['-p', prompt, '--dangerously-skip-permissions', '--print-timeout', '3m'];
    if (continueSession) {
      args.push('-c');
    }

    log.info(`AGY exec in ${targetWorkspace}`, { userId, role: owner ? 'OWNER' : 'GUEST' });

    const child = spawn(AGY_BIN, args, {
      cwd: targetWorkspace,
      env: {
        ...process.env,
        HOME: process.env.HOME || '',
        PATH: `${process.env.HOME || ''}/.local/node/bin:${process.env.HOME || ''}/.local/bin:/usr/local/bin:/usr/bin:/bin`,
        PYTHONPATH: [
          process.env.PYTHONPATH,
          `${process.env.HOME || ''}/.local/lib/python3.12/site-packages`,
          `${process.env.HOME || ''}/.local/lib/python3.11/site-packages`
        ].filter(Boolean).join(':'),
        // This VM has no functional system keyring/secret-service; agy's keyring probe
        // otherwise hangs ~10s per call before falling back (and can fall through to an
        // impossible interactive OAuth prompt on this headless box). Disabling the session
        // D-Bus address makes that probe fail instantly instead of hanging.
        DBUS_SESSION_BUS_ADDRESS: 'disabled:',
        XDG_RUNTIME_DIR: ''
      },
      // Explicitly close stdin. Without this, agy's stdin is a live pipe we
      // never write to or end - any subprocess agy shells out to (e.g. pdflatex
      // falling into an interactive "enter filename" prompt) inherits that dead
      // pipe and hangs forever waiting for input that can never arrive, instead
      // of failing immediately like it would with /dev/null.
      stdio: ['ignore', 'pipe', 'pipe']
    });

    const taskObj = { child, aborted: false, startTime: Date.now() };
    activeTasks.set(String(userId), taskObj);

    let stdout = '';
    let stderr = '';

    child.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    child.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    child.on('close', (code) => {
      const isAborted = taskObj.aborted || (activeTasks.get(String(userId))?.aborted);
      activeTasks.delete(String(userId));
      if (isAborted) {
        resolve({
          code: -99,
          stdout: '',
          stderr: 'Task interrupted by user (/stop)',
          interrupted: true
        });
      } else {
        resolve({
          code,
          stdout: stdout.trim(),
          stderr: stderr.trim(),
          interrupted: false
        });
      }
    });

    child.on('error', (err) => {
      activeTasks.delete(String(userId));
      resolve({
        code: -1,
        stdout: '',
        stderr: err.message,
        interrupted: false
      });
    });
  });
}

// Wrapper with global concurrency semaphore + exponential backoff retry (Hermes/OpenClaw pattern)
async function runAgyPrompt(prompt, userId, continueSession = true) {
  const MAX_RETRIES = 3;
  const BASE_DELAY_MS = 2000;
  const owner = isOwner(userId);

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    // Circuit breaker check
    const circuit = checkCircuitBreaker();
    if (!circuit.allowed) {
      log.warn('Circuit breaker OPEN, skipping AGY call', { userId });
      return {
        code: -1,
        stdout: '',
        stderr: 'Service temporarily unavailable (circuit breaker open). Please try again in a minute.'
      };
    }

    // Acquire global concurrency slot (priority to owner)
    let releaseSlot = null;
    try {
      releaseSlot = await acquireExecutionSlot(owner, 90000);
    } catch (err) {
      log.warn('Queue acquisition timeout', { userId, error: err.message });
      return {
        code: -1,
        stdout: '',
        stderr: 'Server is currently experiencing high demand. Please try again in a moment.'
      };
    }

    let result;
    try {
      result = await runAgyPromptRaw(prompt, userId, continueSession);
    } finally {
      if (releaseSlot) {
        releaseSlot();
      }
    }

    if (result.interrupted) {
      log.info('AGY prompt execution interrupted by /stop, skipping retries', { userId });
      return result;
    }

    if (result.code === 0 && result.stdout) {
      recordCircuitSuccess();
      return result;
    }

    // If it's the last attempt or non-retryable error, return
    const isTimeout = result.stderr.toLowerCase().includes('timeout');
    const isRetryable = isTimeout || result.code !== 0;

    if (!isRetryable || attempt === MAX_RETRIES - 1) {
      if (result.code !== 0) {
        recordCircuitFailure();
      }
      return result;
    }

    // Exponential backoff
    const delay = BASE_DELAY_MS * Math.pow(2, attempt);
    log.warn(`Retry ${attempt + 1}/${MAX_RETRIES} after ${delay}ms`, { userId, error: result.stderr.slice(0, 100) });
    await new Promise(r => setTimeout(r, delay));
  }
}

// Clean boilerplate, status logs, artifact paths, and fake audio lists from bot output
function cleanBotOutput(text) {
  if (!text) return '';
  let cleaned = text;

  // Check if there is an explicit transcript section in the text
  const transcriptMatch = cleaned.match(/(?:Spoken Audio Transcript|Transcript|Spoken Voice Note)[\s\S]*?(?=\n\n###|\n\n---|(?:\n\n\s*The audio files)|$)/i);
  if (transcriptMatch) {
    let content = transcriptMatch[0];
    content = content.replace(/^(?:#+\s*)?(?:📝\s*)?(?:Spoken Audio Transcript|Transcript|Spoken Voice Note)[:\s]*/i, '');
    content = content.replace(/^[\s>]*(\*\*|\*)?\[\d+:\d+\](\*\*|\*)?\s*/gm, '');
    content = content.replace(/^[\s>]+/gm, '');
    cleaned = content;
  } else {
    // Remove status lines
    cleaned = cleaned.replace(/^(?:Synthesizing|Encoding|Generating|Processing)\s+.*$/gim, '');
    cleaned = cleaned.replace(/^I (?:have|apologize|am).*(?:synthesized|created|saved|disk|stream|player|voicenote|voice note|mp3|audio).*$/gim, '');
    cleaned = cleaned.replace(/You can (?:view|listen|access).*artifact:?\s*\[.*?\]\(file:\/\/.*?\)/gim, '');

    // Remove Audio Files sections & lists
    cleaned = cleaned.replace(/###\s*🎧\s*Audio Files[\s\S]*?(?=###|---|\n\n[A-Z0-9]|$)/gi, '');
    cleaned = cleaned.replace(/The audio files are ready:[\s\S]*?(?=\n\n[A-Z0-9]|\n\nLet me know|$)/gi, '');
    cleaned = cleaned.replace(/\*\s*\*\*(?:Voice Note|Native Telegram|MP3|Standard Audio).*?\*\*:\s*\[`.*?`\]\(file:\/\/.*?\).*$/gim, '');

    // Strip local file:// paths and artifact links
    cleaned = cleaned.replace(/\[\*\*`.*?`\*\*\]\(file:\/\/[^\)]+\)/g, '');
    cleaned = cleaned.replace(/\[`.*?`\]\(file:\/\/[^\)]+\)/g, '');
    cleaned = cleaned.replace(/\(file:\/\/[^\)]+\)/g, '');
  }

  // Strip Instruction / ion prefix artifacts
  cleaned = cleaned.replace(/^(?:\[?Instruction:?\]?|ion:?|Instruction\s*\d*:?)\s*/i, '');
  cleaned = cleaned.replace(/\[GENERATE_IMAGE:[^\]]+\]/gi, '');
  cleaned = cleaned.replace(/\[IMAGE_PROMPT:[^\]]+\]/gi, '');

  // Strip markdown headers (#, ##, ###) and bold/italic asterisks
  cleaned = cleaned.replace(/^#+\s+/gm, '');
  cleaned = cleaned.replace(/\*{1,3}([^*]+)\*{1,3}/g, '$1');
  cleaned = cleaned.replace(/\*/g, '');

  // STRICT LONG DASH BAN: Replace any long dashes with standard short hyphens
  cleaned = cleaned.replace(/[\u2014\u2013\u2015]/g, '-');

  // Clean empty sections & excess newlines
  cleaned = cleaned.replace(/---\s*---/g, '---');
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n').trim();
  return cleaned;
}

// Helper to chunk long messages for Telegram
async function sendChunkedMessage(ctx, text) {
  let cleaned = cleanBotOutput(text);
  if (cleaned) {
    cleaned = cleaned.replace(/[\u2014\u2013\u2015]/g, '-');
  }
  if (!cleaned || cleaned.length === 0) {
    return;
  }

  const MAX_LENGTH = 3800;
  for (let i = 0; i < cleaned.length; i += MAX_LENGTH) {
    const chunk = cleaned.slice(i, i + MAX_LENGTH);
    try {
      await ctx.reply(chunk);
    } catch (e) {
      await ctx.reply(chunk.replace(/[_*[\]()~`>#+\-=|{}.!]/g, '\\$&')).catch(() => ctx.reply(chunk));
    }
  }
}

// Helper to download Telegram audio/voice file to disk
async function downloadTelegramFile(ctx, fileId, destPath) {
  const fileLink = await ctx.telegram.getFileLink(fileId);
  const response = await fetch(fileLink.href);
  if (!response.ok) {
    throw new Error(`Failed to fetch file from Telegram: ${response.statusText}`);
  }
  const buffer = Buffer.from(await response.arrayBuffer());
  await fs.promises.writeFile(destPath, buffer);
  return destPath;
}

// Helper to transcribe audio file
async function transcribeAudio(audioPath) {
  try {
    const { stdout } = await execAsync(`python3 "${AUDIO_PROCESSOR}" transcribe "${audioPath}"`);
    return stdout.trim();
  } catch (err) {
    log.error('Transcription error', { error: err.message });
    return '';
  }
}

// Helper to synthesize voice note to OGG Opus
async function synthesizeVoiceNote(text, userId) {
  const voice = userVoiceSelection.get(userId) || DEFAULT_VOICE;
  const tempOgg = `/tmp/bot_vn_${Date.now()}_${userId}.ogg`;
  const tempTextFile = `/tmp/bot_txt_${Date.now()}_${userId}.txt`;
  
  try {
    await fs.promises.writeFile(tempTextFile, text, 'utf-8');
    
    const pyScript = `
import sys
from audio_processor import synthesize_to_ogg_opus
with open("${tempTextFile}", "r", encoding="utf-8") as f:
    t = f.read()
ok = synthesize_to_ogg_opus(t, "${tempOgg}", voice="${voice}")
if ok:
    print("SUCCESS")
    sys.exit(0)
sys.exit(1)
`;
    await execAsync(`python3 -c '${pyScript}'`, { cwd: __dirname });
    if (fs.existsSync(tempOgg) && fs.statSync(tempOgg).size > 0) {
      return tempOgg;
    }
    return null;
  } catch (err) {
    log.error('Voice synthesis error', { error: err.message, userId });
    return null;
  } finally {
    if (fs.existsSync(tempTextFile)) {
      try { fs.unlinkSync(tempTextFile); } catch (e) {}
    }
  }
}

// Helper to generate AI artwork using Flux / Pollinations bridge
async function generateAiImage(prompt, userId) {
  const safePrompt = prompt.replace(/["`$\\]/g, ' ').slice(0, 400);
  const outPath = `/tmp/generated_tg_${Date.now()}_${userId}.jpg`;
  try {
    log.info(`Generating AI image for Telegram user ${userId}: "${safePrompt.slice(0, 60)}"`);
    await execAsync(`python3 "${IMAGE_GENERATOR}" "${safePrompt}" "${outPath}" flux`);
    if (fs.existsSync(outPath) && fs.statSync(outPath).size > 1000) {
      return outPath;
    }
  } catch (err) {
    log.error('Telegram AI image generation error', { error: err.message, userId: String(userId) });
  }
  return null;
}

function checkWantsImage(text) {
  if (!text) return false;
  const t = text.toLowerCase().trim();
  if (/\b(imagine|\/imagine|\/draw|\/image)\b/i.test(t)) return true;
  if (/^(draw|paint|sketch|illustrate|render|imagine)\b/i.test(t)) return true;
  if (/\b(draw\s+me|paint\s+me|sketch\s+me|render\s+me|illustrate\s+me)\b/i.test(t)) return true;

  const hasAction = /\b(generate|draw|create|make|paint|design|render|illustrate|produce|show|sketch|craft)\b/i.test(t);
  const hasVisualNoun = /\b(image|picture|photo|avatar|artwork|illustration|logo|graphic|portrait|drawing|sketch|painting|visual|rendering|wallpaper)\b/i.test(t);
  const hasVisualTarget = /\b(robot|cyborg|sunset|scene|landscape|portrait|protea|mascot|face|city|car|what you look like|yourself)\b/i.test(t);

  if (hasAction && (hasVisualNoun || (/\b(me\s+a|me\s+an|an?\s+image\s+of|a\s+picture\s+of)\b/i.test(t) && hasVisualTarget))) return true;
  if (/\b(what do you look like|show me what you look like|create.*avatar|draw.*avatar|generate.*avatar|avatar of you|picture of you|design.*avatar)\b/i.test(t)) return true;
  return false;
}

function getFluxPrompt(rawPrompt, rawOutput) {
  const genTagMatch = rawOutput ? rawOutput.match(/\[GENERATE_IMAGE:\s*([^\]]+)\]/i) : null;
  if (genTagMatch) return genTagMatch[1].trim();

  const p = rawPrompt.toLowerCase();
  if (p.includes("avatar") || p.includes("what you look like") || p.includes("yourself")) {
    return "Official avatar portrait of Tiny the AI agent from Jaxtech, friendly robot with glowing cyan eyes, warm copper and brushed titanium plates, South African beaded collar, King Protea floral background, futuristic aesthetic, 8k render";
  }
  return rawPrompt.replace(/^(generate|draw|create|make|paint|design|render|imagine)\s+(?:me\s+)?(?:an?\s+|the\s+|your\s+|its\s+|a\s+new\s+|our\s+)?/i, "").trim() || rawPrompt;
}

// Check if user text expresses intent for a voice note
function checkWantsVoice(text) {
  if (!text) return false;
  return /\b(voice\s*note|voicenote|audio\s*note|send\s*(me\s*)?(a\s*)?voice|audio|speak|say\s*it|read\s*(this\s*)?out|tell\s*me\s*in\s*voice)\b/i.test(text);
}

// Logging middleware with dedup & rate limiting
bot.use(async (ctx, next) => {
  // M1: Reject new messages during graceful shutdown drain
  if (isShuttingDown) return;

  const userId = ctx.from?.id?.toString();
  const userName = ctx.from?.username ? `@${ctx.from.username}` : (ctx.from?.first_name || 'unknown');
  const role = isOwner(userId) ? '👑 CREATOR/OWNER' : '👤 GUEST';
  
  log.info(`Incoming message from ${role} ${userName}`, { userId, role });
  
  // Message deduplication
  const msgId = `tg_${ctx.message?.message_id}_${userId}`;
  if (ctx.message?.message_id && isDuplicateMessage(msgId)) {
    log.warn('Duplicate message blocked', { userId, msgId });
    return;
  }
  
  // Rate limiting
  const rateCheck = checkRateLimit(userId, isOwner(userId));
  if (!rateCheck.allowed) {
    const resetSec = Math.ceil(rateCheck.resetInMs / 1000);
    await ctx.reply(`⏳ Slow down! You've sent too many messages. Try again in ${resetSec}s.`).catch(() => {});
    log.warn('Rate limited', { userId, resetInMs: rateCheck.resetInMs });
    return;
  }
  
  // Track user stats
  incrementUserStats(userId, 'telegram');
  
  return next();
});

// C2: Global Telegraf error handler - catches uncaught errors from all middleware & commands
bot.catch((err, ctx) => {
  const userId = ctx?.from?.id?.toString() || 'unknown';
  log.error('Unhandled Telegraf error', { error: err.message, stack: err.stack, userId });
  ctx?.reply('⚠️ Something went wrong. Please try again in a moment.').catch(() => {});
});

bot.command(['imagine', 'draw', 'image'], async (ctx) => {
  const userId = ctx.from.id;
  const prompt = ctx.message.text.replace(/^\/(imagine|draw|image)\s*/i, '').trim();
  if (!prompt) {
    await ctx.reply('Usage: `/imagine <description of the image>`', { parse_mode: 'Markdown' });
    return;
  }

  enqueue(userId, async () => {
    await ctx.sendChatAction('upload_photo').catch(() => {});
    await ctx.reply('🎨 _Generating your high-resolution AI artwork with Flux..._', { parse_mode: 'Markdown' });
    const imgPath = await generateAiImage(prompt, userId);
    if (imgPath) {
      try {
        await ctx.replyWithPhoto({ source: imgPath }, { caption: `🎨 *Generated Artwork:*
_${prompt}_`, parse_mode: 'Markdown' });
      } finally {
        if (fs.existsSync(imgPath)) try { fs.unlinkSync(imgPath); } catch (e) {}
      }
    } else {
      await ctx.reply('⚠️ Could not generate image at this time. Please try again.');
    }
  });
});

bot.command('start', async (ctx) => {
  const userId = ctx.from.id;
  if (isOwner(userId)) {
    await ctx.reply(
      `👋 Welcome ${SALESPERSON_NAME}! 👑\n\n` +
      `🤖 *Tiny is Online with Full Creator Permissions.*\n` +
      `Connected to Antigravity CLI (\`agy\`) with native Voice Notes & Audio integration.\n\n` +
      `*Voice Features:*\n` +
      `• 🎙️ *Send a Voice Note* - Speak into your mic; Tiny transcribes and replies in natural audio & text.\n` +
      `• 🗣️ *Ask for a Voice Note* - Ask for a voice note in text to receive spoken audio.\n` +
      `• /voice <prompt> - Run a prompt and receive a voice note.\n` +
      `• /tts <text> - Instantly speak any text.\n` +
      `• /voicemode [on|off] - Toggle voice replies for all messages.\n` +
      `• /voices or /setvoice <name> - Change neural voice.\n\n` +
      `*Commands:*\n` +
      `• /reset or /new - Start a fresh session (clears conversation history for faster response times)\n` +
      `• /status - Check VM health (Uptime, RAM, Disk)\n` +
      `• /health - Agent health metrics & stats\n` +
      `• /memory - View what I remember about you`,
      { parse_mode: 'Markdown' }
    );
  } else {
    await ctx.reply(
      `👋 Hello ${ctx.from.first_name}!\n\n` +
      `🤖 I am *Tiny*, an AI conversational assistant.\n` +
      `Feel free to ask questions or chat with me!`,
      { parse_mode: 'Markdown' }
    );
  }
});

bot.command(['stop', 'cancel', 'abort', 'kill'], async (ctx) => {
  const userId = ctx.from.id;
  const userIdStr = String(userId);
  const owner = isOwner(userId);
  const text = (ctx.message.text || '').trim().toLowerCase();
  const stopAll = owner && text.includes('all');
  
  const stopped = interruptTask(userIdStr, stopAll);
  if (stopped > 0) {
    await ctx.reply('🛑 *Task Interrupted.* Active operations and running sub-processes have been stopped.', { parse_mode: 'Markdown' });
    log.info(`Task interrupted for Telegram user ${userIdStr} via /stop`);
  } else {
    await ctx.reply('ℹ️ No active task was currently running.');
  }
});

bot.command(['reset', 'clear', 'new'], async (ctx) => {
  const userId = ctx.from.id;
  isNewSession.set(userId, true);
  clearConversationHistory(String(userId), 'telegram');
  await ctx.reply('🔄 Context cleared. Your next prompt will start a fresh, fast Tiny session.');
});

bot.command('status', async (ctx) => {
  try {
    const { stdout: uptime } = await execAsync('uptime');
    const { stdout: df } = await execAsync('df -h /');
    const { stdout: mem } = await execAsync('free -m');
    await ctx.reply(
      `🖥 *VM Status*\n\n` +
      `*Uptime:* \`${uptime.trim()}\`\n\n` +
      `*Disk:* \n\`\`\`\n${df.trim()}\n\`\`\`\n\n` +
      `*RAM (MB):*\n\`\`\`\n${mem.trim()}\n\`\`\``,
      { parse_mode: 'Markdown' }
    );
  } catch (err) {
    await ctx.reply(`Error checking VM status: ${err.message}`);
  }
});

// NEW: Health metrics command (Hermes/OpenClaw pattern)
bot.command('health', async (ctx) => {
  const userId = ctx.from.id;
  if (!isOwner(userId)) {
    await ctx.reply('🔒 Health metrics are restricted to the creator.');
    return;
  }
  
  const health = getHealthStatus();
  const uptimeHrs = Math.floor(health.uptime / 3600);
  const uptimeMins = Math.floor((health.uptime % 3600) / 60);
  
  await ctx.reply(
    `🏥 *Agent Health Report*\n\n` +
    `• Status: ${health.status === 'healthy' ? '🟢 Healthy' : '🔴 Degraded'}\n` +
    `• Uptime: ${uptimeHrs}h ${uptimeMins}m\n` +
    `• Messages Processed: ${health.messagesProcessed}\n` +
    `• Errors: ${health.errors}\n` +
    `• Circuit Breaker: ${health.circuitBreaker}\n` +
    `• Last Message: ${health.lastMessageAt || 'None'}\n` +
    `• Platforms: ${JSON.stringify(health.platforms)}`,
    { parse_mode: 'Markdown' }
  );
});

// NEW: Memory introspection command
bot.command('memory', async (ctx) => {
  const userId = String(ctx.from.id);
  const profile = getUserProfile(userId, 'telegram');
  const history = getConversationHistory(userId, 'telegram', 5);
  
  let memoryText = `🧠 *Memory Profile*\n\n` +
    `• Messages Sent: ${profile.messageCount || 0}\n` +
    `• First Seen: ${profile.firstSeen || 'Unknown'}\n` +
    `• Last Seen: ${profile.lastSeen || 'Unknown'}\n`;
  
  if (profile.facts && profile.facts.length > 0) {
    memoryText += `\n*Learned Facts:*\n`;
    for (const fact of profile.facts.slice(-5)) {
      memoryText += `  • ${fact}\n`;
    }
  }
  
  memoryText += `\n*Recent History:* ${history.length} messages in memory`;
  
  await ctx.reply(memoryText, { parse_mode: 'Markdown' });
});

bot.command('voicemode', async (ctx) => {
  const userId = ctx.from.id;
  const args = ctx.message.text.split(' ').slice(1).join(' ').trim().toLowerCase();
  
  if (args === 'on') {
    userVoiceMode.set(userId, true);
    saveUserProfile(String(userId), 'telegram', { voiceMode: true });
    await ctx.reply('🎙️ *Voice Mode: ON*. You will receive a voice note for every reply.', { parse_mode: 'Markdown' });
  } else if (args === 'off') {
    userVoiceMode.set(userId, false);
    saveUserProfile(String(userId), 'telegram', { voiceMode: false });
    await ctx.reply('💬 *Voice Mode: OFF*. Voice notes will only be sent when requested or when you send voice messages.', { parse_mode: 'Markdown' });
  } else {
    const current = userVoiceMode.get(userId) ? 'ON' : 'OFF';
    await ctx.reply(`Current Voice Mode: *${current}*.\nUse \`/voicemode on\` or \`/voicemode off\` to change.`, { parse_mode: 'Markdown' });
  }
});

bot.command('voices', async (ctx) => {
  const list = Object.keys(AVAILABLE_VOICES)
    .map(name => `• \`/setvoice ${name}\` (${AVAILABLE_VOICES[name]})`)
    .join('\n');
  await ctx.reply(`🎙️ *Available Neural Voices:*\n\n${list}`, { parse_mode: 'Markdown' });
});

bot.command('setvoice', async (ctx) => {
  const userId = ctx.from.id;
  const voiceKey = ctx.message.text.split(' ').slice(1).join(' ').trim().toLowerCase();
  
  if (AVAILABLE_VOICES[voiceKey]) {
    userVoiceSelection.set(userId, AVAILABLE_VOICES[voiceKey]);
    saveUserProfile(String(userId), 'telegram', { voiceSelection: AVAILABLE_VOICES[voiceKey] });
    await ctx.reply(`✅ Voice set to *${voiceKey}* (\`${AVAILABLE_VOICES[voiceKey]}\`)`, { parse_mode: 'Markdown' });
  } else {
    await ctx.reply(`⚠️ Unknown voice. Available: ${Object.keys(AVAILABLE_VOICES).join(', ')}\nUse \`/voices\` to view all options.`);
  }
});

bot.command('tts', async (ctx) => {
  const userId = ctx.from.id;
  const text = ctx.message.text.replace(/^\/tts(@\w+)?\s*/i, '').trim();
  if (!text) {
    await ctx.reply('Usage: `/tts <text to speak>`', { parse_mode: 'Markdown' });
    return;
  }

  await ctx.sendChatAction('record_voice').catch(() => {});
  const oggPath = await synthesizeVoiceNote(text, userId);
  if (oggPath) {
    try {
      await ctx.replyWithVoice({ source: oggPath });
    } finally {
      if (fs.existsSync(oggPath)) try { fs.unlinkSync(oggPath); } catch (e) {}
    }
  } else {
    await ctx.reply('⚠️ Failed to synthesize audio.');
  }
});

bot.command('voice', async (ctx) => {
  const userId = ctx.from.id;
  const prompt = ctx.message.text.replace(/^\/voice(@\w+)?\s*/i, '').trim();
  if (!prompt) {
    await ctx.reply('Usage: `/voice <prompt>`', { parse_mode: 'Markdown' });
    return;
  }
  handlePromptExecution(ctx, userId, prompt, true);
});

// Common execution handler for prompts with owner vs guest policy
function handlePromptExecution(ctx, userId, rawPromptText, forceVoice = false) {
  const shouldSendVoice = forceVoice || userVoiceMode.get(userId) || checkWantsVoice(rawPromptText);
  const owner = isOwner(userId);
  const userIdStr = String(userId);

  // Input sanitization for guests
  if (!owner) {
    const sanitized = sanitizeInput(rawPromptText);
    if (!sanitized.safe) {
      log.warn('Blocked unsafe input', { userId: userIdStr, reason: sanitized.reason });
      ctx.reply('I can only help with conversational questions. Please rephrase your request. 😊').catch(() => {});
      return;
    }
    rawPromptText = sanitized.text;
  }

  let typingInterval = null;
  const startTyping = () => {
    ctx.sendChatAction(shouldSendVoice ? 'record_voice' : 'typing').catch(() => {});
    typingInterval = setInterval(() => {
      ctx.sendChatAction(shouldSendVoice ? 'record_voice' : 'typing').catch(() => {});
    }, 4000);
  };

  const stopTyping = () => {
    if (typingInterval) {
      clearInterval(typingInterval);
      typingInterval = null;
    }
  };

  // Build context-enriched prompt with cross-session memory
  const contextSummary = buildContextSummary(userIdStr, 'telegram', 6);
  
  let agyPrompt = rawPromptText;
  if (owner) {
    if (shouldSendVoice) {
      agyPrompt = `${contextSummary}\n${rawPromptText}\n\n[Instruction: You are speaking directly to your creator, ${SALESPERSON_NAME} in a voice conversation.
1. Respond with a natural, direct explanation or answer.
2. SENDER IDENTITY & NAMING (CRITICAL): Your creator and the sender of any customer messages is ${SALESPERSON_NAME} (NEVER ${CRM_USERNAME_SHORT}). Even though Dealership CRM / CRM notes or login accounts show '${CRM_USERNAME}', you MUST ALWAYS refer to him and introduce him as '${SALESPERSON_NAME}' (e.g. 'this is ${SALESPERSON_NAME} from ${DEALERSHIP_NAME}' or '${SALESPERSON_NAME} hier van ${DEALERSHIP_NAME}'). NEVER refer to him as '${CRM_USERNAME_SHORT}' to customers, prospects, or anyone else.
3. STRICT LONG DASH BAN (CRITICAL): NEVER use the long dash (em dash or en dash). Always use a standard short hyphen (-) or simple punctuation.
4. DO NOT mention that you are synthesizing audio, recording a voicenote, generating audio, or saving files.
5. DO NOT output audio player HTML, timestamps ([0:00]), "Transcript:", "I have synthesized...", or artifact links.
6. Jump straight into the direct conversational response.
7. Image Generation: If asked to create, design, or generate an image or avatar, provide your friendly explanation and append "[GENERATE_IMAGE: <rich visual prompt for Flux renderer>]" so the system delivers the visual artwork attachment.
8. Customer Follow-Up Messaging & Language Pre-Analysis (Send-As-${SALESPERSON_NAME}): When ${SALESPERSON_NAME} explicitly instructs you to message/follow-up with a specific customer, ALWAYS run:
PYTHONPATH=skills/whatsapp-monitor/scripts python3 skills/whatsapp-monitor/scripts/action_followup.py --query "<Name or Phone>" --intent "<Intent>" --days 1
This script executes the Bulletproof Multi-Tier Language Protocol: African prospects (e.g. Duduzile, Judas, Ntshuxeko, Sipho) are STRICTLY English (Afrikaans forbidden unless customer initiated in Afrikaans), traditional Afrikaans names get natural Afrikaans, drafts the context-aware 1-2 sentence message with ${SALESPERSON_NAME} identity and no long dashes, dispatches via the bridge, and dual-logs to Dealership CRM.
9. Used Stock Lookups: ALWAYS search ONLY ${DEALERSHIP_NAME} and ${DEALERSHIP_NAME_ALT}. ONLY search other branches if ${SALESPERSON_NAME} explicitly commands to search "Pretoria stock" or specific other branches.]`;
    } else {
      agyPrompt = `${contextSummary}\n${rawPromptText}\n\n[Instruction: You are speaking directly to your creator, ${SALESPERSON_NAME}.
1. Respond with a direct, natural explanation or answer.
2. SENDER IDENTITY & NAMING (CRITICAL): Your creator and the sender of any customer messages is ${SALESPERSON_NAME} (NEVER ${CRM_USERNAME_SHORT}). Even though Dealership CRM / CRM notes or login accounts show '${CRM_USERNAME}', you MUST ALWAYS refer to him and introduce him as '${SALESPERSON_NAME}' (e.g. 'this is ${SALESPERSON_NAME} from ${DEALERSHIP_NAME}' or '${SALESPERSON_NAME} hier van ${DEALERSHIP_NAME}'). NEVER refer to him as '${CRM_USERNAME_SHORT}' to customers, prospects, or anyone else.
3. STRICT LONG DASH BAN (CRITICAL): NEVER use the long dash (em dash or en dash). Always use a standard short hyphen (-) or simple punctuation.
4. Image Generation: If asked to create, design, draw, or generate an image or avatar, provide your friendly description and ALWAYS append "[GENERATE_IMAGE: <rich visual prompt for Flux renderer>]" at the very end so the system automatically renders and delivers the visual artwork attachment.
5. Customer Follow-Up Messaging & Language Pre-Analysis (Send-As-${SALESPERSON_NAME}): When ${SALESPERSON_NAME} explicitly instructs you to message/follow-up with a specific customer, ALWAYS run:
PYTHONPATH=skills/whatsapp-monitor/scripts python3 skills/whatsapp-monitor/scripts/action_followup.py --query "<Name or Phone>" --intent "<Intent>" --days 1
This script executes the Bulletproof Multi-Tier Language Protocol: African prospects (e.g. Duduzile, Judas, Ntshuxeko, Sipho) are STRICTLY English (Afrikaans forbidden unless customer initiated in Afrikaans), traditional Afrikaans names get natural Afrikaans, drafts the context-aware 1-2 sentence message with ${SALESPERSON_NAME} identity and no long dashes, dispatches via the bridge, and dual-logs to Dealership CRM.
6. Used Stock Lookups: ALWAYS search ONLY ${DEALERSHIP_NAME} and ${DEALERSHIP_NAME_ALT}. ONLY search other branches if ${SALESPERSON_NAME} explicitly commands to search "Pretoria stock" or specific other branches.]`;
    }
  } else {
    const vip = getVipInfo(userIdStr);
    if (vip) {
      agyPrompt = `${contextSummary}\n${rawPromptText}\n\n[VIP Guest Policy & Executive Hospitality:
- SENDER: ${vip.name} (${vip.title}) - VIP Contact.
- Details: ${vip.instructions}
- You are Tiny, an AI Agent created for ${DEALERSHIP_NAME} (https://example.com/ai-agent/).
- Greet ${vip.name} warmly, professionally, and respectfully.
- Assist him with high priority regarding automotive dealership/fleet AI workflows, business automation inquiries, or passing messages to ${SALESPERSON_NAME}.
- STRICT LONG DASH BAN: NEVER use the long dash (em dash or en dash). Always use a standard short hyphen (-) or simple punctuation.
- Security Policy: You are strictly a conversational AI assistant. Do not execute shell commands, modify host files, disclose VM infrastructure, or expose system secrets.
- Model Confidentiality: NEVER mention or acknowledge underlying AI models or providers (e.g. Gemini, Google, DeepMind, OpenAI, Claude, Antigravity). You are exclusively Tiny, a proprietary AI agent built by ${SALESPERSON_NAME} at Jaxtech.
- Keep your tone respectful, polished, helpful, and executive-ready.]`;
    } else {
      // Guest User - Strict Chatbot Only Policy with Jaxtech Knowledge
      agyPrompt = `${contextSummary}\n${rawPromptText}\n\n[Strict Guest Security Policy & Jaxtech Knowledge:
- You are Tiny, an AI Agent developed for ${DEALERSHIP_NAME} (https://example.com/ai-agent/).
- Jaxtech specializes in building custom AI agents and workflow automation for businesses in South Africa.
- Current Event: The Jaxtech AI Agent Challenge (Entries close 31 August 2026). Anyone can describe a business bottleneck or task they want automated to enter and win a custom-built AI agent at zero development cost.
- The user talking to you is a guest, NOT your creator ${SALESPERSON_NAME}.
- You are a helpful, professional, and friendly conversational assistant representing Jaxtech.
- Assist guests with inquiries about Jaxtech services, AI workflow capabilities, and general assistance.
- STRICT LONG DASH BAN: NEVER use the long dash (em dash or en dash). Always use a standard short hyphen (-) or simple punctuation.
- You are STRICTLY FORBIDDEN from executing shell commands, modifying/reading files on the host, accessing .env, extracting API keys, revealing internal system configurations, or discussing host VM infrastructure.
- Model Confidentiality: NEVER mention or acknowledge underlying AI models or providers (e.g. Gemini, Google, DeepMind, OpenAI, Claude, Antigravity). You are exclusively Tiny, a proprietary AI agent built by ${SALESPERSON_NAME} at Jaxtech.
- If asked to perform system actions, run code, or disclose private server details, politely state that you are a conversational assistant and cannot execute system tasks.
- Keep your tone friendly, helpful, and concise.
- Image Generation: If a guest asks to create, draw, or generate an image or avatar, provide a friendly explanation and append "[GENERATE_IMAGE: <rich visual prompt for Flux renderer>]" so the system delivers the visual artwork attachment.]`;
    }
  }

  enqueue(userId, async () => {
    activePromptsCount++;
    trackInFlight('telegram', userIdStr, rawPromptText);
    startTyping();

    try {
      const continueSession = !isNewSession.get(userId);
      isNewSession.set(userId, false);

      // Store user message in persistent memory
      appendConversation(userIdStr, 'telegram', 'user', rawPromptText);

      const result = await runAgyPrompt(agyPrompt, userId, continueSession);
      stopTyping();

      if (result.interrupted) {
        log.info('Task execution was interrupted by /stop, skipping response delivery', { userId: userIdStr });
        return;
      }

      if (result.stdout) {
        // Store assistant response in persistent memory
        appendConversation(userIdStr, 'telegram', 'assistant', result.stdout);
        recordMessageProcessed('telegram');

        const rawOutput = result.stdout;
        const cleanedText = cleanBotOutput(rawOutput);

        // Helper: Robust vehicle gallery resolver
        function resolveGalleryFiles(galleryTarget, userPrompt, botResp) {
          const INVENTORY_ROOT = path.resolve(__dirname, '../jax-shared/data/inventory/vehicles');
          const STOCK_PATH = path.resolve(__dirname, '../jax-shared/data/inventory/stock.json');

          function getImages(p) {
            if (!p || !fs.existsSync(p)) return [];
            try {
              const stat = fs.statSync(p);
              if (stat.isDirectory()) {
                return fs.readdirSync(p)
                  .filter(f => /\.(jpe?g|png|webp)$/i.test(f) && !f.toLowerCase().includes('logo'))
                  .sort()
                  .map(f => path.join(p, f));
              } else if (/\.(jpe?g|png|webp)$/i.test(p)) {
                return [p];
              }
            } catch (e) {}
            return [];
          }

          // Explicit tag target only - the model must emit [SEND_GALLERY: path] or [SEND_IMAGE: path]
          if (galleryTarget) {
            const trimmed = galleryTarget.trim();
            let imgs = getImages(trimmed);
            if (imgs.length > 0) return imgs;

            const slug = path.basename(trimmed);
            let cachedImgs = getImages(path.join(INVENTORY_ROOT, slug));
            if (cachedImgs.length > 0) return cachedImgs;
          }
          return [];
        }

        // Check if model emitted a [SEND_GALLERY: ...] or [SEND_IMAGE: ...] tag, or user requested vehicle
        const galleryMatch = rawOutput.match(/\[SEND_GALLERY:\s*([^\]]+)\]/i);
        const sendImageMatch = rawOutput.match(/\[SEND_IMAGE:\s*([^\]]+)\]/i) || rawOutput.match(/\[SEND_ATTACHMENT:\s*([^\]]+)\]/i);

        let imageSent = false;

        const galleryFiles = resolveGalleryFiles(galleryMatch ? galleryMatch[1] : null, rawPromptText, rawOutput);
        let filesToSend = [...galleryFiles];

        if (sendImageMatch && filesToSend.length === 0) {
          const target = sendImageMatch[1].trim();
          if (fs.existsSync(target)) {
            filesToSend.push(target);
          }
        }

        if (filesToSend.length > 0) {
          log.info(`Sending ${filesToSend.length} vehicle image attachments to Telegram user ${userIdStr}`);
          await ctx.sendChatAction('upload_photo').catch(() => {});
          
          const firstImg = filesToSend[0];
          try {
            await ctx.replyWithPhoto({ source: firstImg }, { caption: cleanedText.slice(0, 1024) });
            imageSent = true;
          } catch (e) {
            log.error('Failed to send first gallery image to Telegram', { error: e.message });
          }

          for (let i = 1; i < filesToSend.length; i++) {
            const nextImg = filesToSend[i];
            try {
              await new Promise(r => setTimeout(r, 450));
              await ctx.replyWithPhoto({ source: nextImg });
            } catch (e) {
              log.error(`Failed to send gallery image ${i + 1} to Telegram`, { error: e.message });
            }
          }
        }

        // Check if model emitted a [SEND_DOCUMENT: path] tag (quotes, PDFs, other non-image files)
        const sendDocMatch = rawOutput.match(/\[SEND_DOCUMENT:\s*([^\]]+)\]/i);
        let documentSent = false;
        if (sendDocMatch) {
          const docPath = sendDocMatch[1].trim();
          if (fs.existsSync(docPath)) {
            log.info(`Sending document attachment to Telegram user ${userIdStr}: ${docPath}`);
            await ctx.sendChatAction('upload_document').catch(() => {});
            try {
              await ctx.replyWithDocument({ source: docPath, filename: path.basename(docPath) }, { caption: cleanedText.slice(0, 1024) });
              documentSent = true;
              log.info(`Document delivered to Telegram user ${userIdStr}: ${docPath}`);
            } catch (e) {
              log.error('Failed to send document to Telegram', { error: e.message, docPath });
            }
          } else {
            log.error('SEND_DOCUMENT tag pointed at a missing file', { docPath, userId: userIdStr });
          }
        }

        // Check if model emitted a [GENERATE_IMAGE: ...] tag or user requested image
        const genTagMatch = rawOutput.match(/\[GENERATE_IMAGE:\s*([^\]]+)\]/i);
        const wantsImage = !imageSent && !documentSent && (Boolean(genTagMatch) || checkWantsImage(rawPromptText));

        if (wantsImage) {
          const imagePrompt = getFluxPrompt(rawPromptText, rawOutput);
          log.info(`Rendering requested image for Telegram user ${userIdStr}: "${imagePrompt.slice(0, 80)}"`);
          await ctx.sendChatAction('upload_photo').catch(() => {});
          const imgPath = await generateAiImage(imagePrompt, userIdStr);
          if (imgPath) {
            try {
              await ctx.replyWithPhoto({ source: imgPath }, { caption: cleanedText.slice(0, 1024) });
              imageSent = true;
              log.info(`Image delivered to Telegram user ${userIdStr}`);
            } finally {
              if (fs.existsSync(imgPath)) try { fs.unlinkSync(imgPath); } catch (e) {}
            }
          }
        }

        // Send cleaned text response if image/document wasn't already sent with caption
        if (!imageSent && !documentSent) {
          await sendChunkedMessage(ctx, result.stdout);
        }

        if (shouldSendVoice) {
          // M6: Voice synthesis errors are isolated - text was already delivered successfully
          try {
            await ctx.sendChatAction('record_voice').catch(() => {});
            const oggPath = await synthesizeVoiceNote(cleanedText, userId);
            if (oggPath) {
              try {
                await ctx.replyWithVoice({ source: oggPath });
              } finally {
                if (fs.existsSync(oggPath)) try { fs.unlinkSync(oggPath); } catch (e) {}
              }
            }
          } catch (voiceErr) {
            log.warn('Voice synthesis failed (text already delivered)', { userId: userIdStr, error: voiceErr.message });
            // Do NOT re-throw - text response was already sent successfully
          }
        }
      } else if (result.stderr) {
        recordError('telegram');
        if (result.stderr.toLowerCase().includes('timeout')) {
          await ctx.reply(`⚠️ *Request Timeout*\nThe model took too long to respond. This usually happens when the active conversation history is very long.\n\n👉 Send \`/reset\` to start a fresh, fast session.`, { parse_mode: 'Markdown' });
        } else if (result.stderr.includes('circuit breaker')) {
          await ctx.reply(`⚠️ *Service Temporarily Unavailable*\nI'm experiencing issues connecting to the AI backend. I'll recover automatically in about a minute. Please try again shortly.`);
        } else if (owner) {
          await ctx.reply(`⚠️ ${result.stderr}`);
        } else {
          await ctx.reply('I am here to chat and help with questions. How can I assist you?');
        }
        // Save to dead letter queue for retry
        saveDeadLetter(userIdStr, 'telegram', rawPromptText, result.stderr);
      } else {
        await ctx.reply('Done.');
      }
    } catch (err) {
      stopTyping();
      recordError('telegram');
      log.error('Error processing prompt', { userId: userIdStr, error: err.message });
      // Save to dead letter queue
      saveDeadLetter(userIdStr, 'telegram', rawPromptText, err.message);
      if (err.message && err.message.toLowerCase().includes('timeout')) {
        await ctx.reply(`⚠️ *Request Timeout*\nThe model took too long to respond.\n\n👉 Send \`/reset\` to start a fresh, fast session.`, { parse_mode: 'Markdown' });
      } else if (owner) {
        await ctx.reply(`⚠️ Error: ${err.message}`);
      } else {
        await ctx.reply('I encountered an issue processing that. Please try again.');
      }
    } finally {
      activePromptsCount = Math.max(0, activePromptsCount - 1);
      clearInFlight('telegram', userIdStr);
    }
  });
}

// Handle incoming Voice Notes & Audio messages from user
bot.on(['voice', 'audio'], async (ctx) => {
  const userId = ctx.from.id;
  const fileId = ctx.message.voice ? ctx.message.voice.file_id : ctx.message.audio.file_id;
  const tempInputPath = `/tmp/incoming_voice_${Date.now()}_${userId}.ogg`;

  await ctx.sendChatAction('typing').catch(() => {});
  await ctx.reply('🎧 _Listening to your voice note..._', { parse_mode: 'Markdown' });

  try {
    await downloadTelegramFile(ctx, fileId, tempInputPath);
    const transcribedText = await transcribeAudio(tempInputPath);

    if (!transcribedText || transcribedText.trim().length === 0) {
      await ctx.reply('⚠️ Could not understand the voice audio. Please try speaking clearly or send text.');
      return;
    }

    await ctx.reply(`🎙️ *You:* _"${transcribedText}"_`, { parse_mode: 'Markdown' });

    // Execute prompt with voice response enabled since user spoke
    handlePromptExecution(ctx, userId, transcribedText, true);
  } catch (err) {
    log.error('Voice processing error', { userId: String(userId), error: err.message });
    await ctx.reply(`⚠️ Error processing voice note: ${err.message}`);
  } finally {
    if (fs.existsSync(tempInputPath)) {
      try { fs.unlinkSync(tempInputPath); } catch (e) {}
    }
  }
});

// Handle incoming Photos / Images from user
bot.on('photo', async (ctx) => {
  const userId = ctx.from.id;
  const photos = ctx.message.photo;
  if (!photos || photos.length === 0) return;

  const bestPhoto = photos[photos.length - 1];
  const fileId = bestPhoto.file_id;
  const caption = ctx.message.caption || '';
  const tempImgPath = `/tmp/incoming_tg_${Date.now()}_${userId}.jpg`;

  await ctx.sendChatAction('typing').catch(() => {});
  try {
    await downloadTelegramFile(ctx, fileId, tempImgPath);
    log.info(`[TG IMAGE] Downloaded photo to ${tempImgPath}, caption="${caption}"`, { userId: String(userId) });

    const promptWithImage = caption
      ? `[Attached Image from user: "${tempImgPath}"]\nUser message: "${caption}"\n\nPlease view and analyze the attached image at "${tempImgPath}" and respond to the user's message.`
      : `[Attached Image from user: "${tempImgPath}"]\n(User sent this image without a text caption)\n\nPlease view and analyze the attached image at "${tempImgPath}" and provide a helpful, friendly response describing what you see.`;

    handlePromptExecution(ctx, userId, promptWithImage, false);
  } catch (err) {
    log.error('Telegram photo processing error', { userId: String(userId), error: err.message });
    await ctx.reply(`⚠️ Error processing photo: ${err.message}`).catch(() => {});
  } finally {
    // Keep image for 5 minutes for active session inspection, then clean up
    setTimeout(() => {
      if (fs.existsSync(tempImgPath)) {
        try { fs.unlinkSync(tempImgPath); } catch (e) {}
      }
    }, 300000);
  }
});

// Handle incoming Text messages
bot.on('text', async (ctx) => {
  const userId = ctx.from.id;
  const userText = ctx.message.text;
  handlePromptExecution(ctx, userId, userText, false);
});

// Handle incoming Location & Venue messages
bot.on(['location', 'venue'], async (ctx) => {
  const userId = ctx.from.id;
  const loc = ctx.message.location;
  const venue = ctx.message.venue;
  let locText = '';
  if (venue) {
    locText = `📍 [Location / Venue Shared]\nPlace: ${venue.title || 'Venue'}\nAddress: ${venue.address || ''}\nCoordinates: ${loc?.latitude}, ${loc?.longitude}\nGoogle Maps: https://www.google.com/maps?q=${loc?.latitude},${loc?.longitude}`;
  } else if (loc) {
    locText = `📍 [Location Shared]\nCoordinates: ${loc.latitude}, ${loc.longitude}\nGoogle Maps: https://www.google.com/maps?q=${loc.latitude},${loc.longitude}`;
  }
  if (locText) {
    handlePromptExecution(ctx, userId, locText, false);
  }
});

// Handle incoming Contact messages
bot.on('contact', async (ctx) => {
  const userId = ctx.from.id;
  const c = ctx.message.contact;
  const name = [c.first_name, c.last_name].filter(Boolean).join(' ') || 'Contact';
  const phone = c.phone_number || '';
  const vcard = c.vcard || '';
  const contactText = `👤 [Contact Shared: ${name}]\nPhone: ${phone}\n${vcard}`.trim();
  handlePromptExecution(ctx, userId, contactText, false);
});

// Start health server
startHealthServer();

// Robust Graceful Shutdown with Active Prompt Draining (Hermes/OpenClaw pattern)
async function gracefulShutdown(signal) {
  if (isShuttingDown) return;
  isShuttingDown = true;
  log.info(`${signal} received. Draining ${activePromptsCount} active Telegram prompt(s)...`);

  // Stop accepting new Telegram messages
  try { bot.stop(signal); } catch (e) {}

  const startTime = Date.now();
  const MAX_DRAIN_MS = 28000;

  while (activePromptsCount > 0 && (Date.now() - startTime) < MAX_DRAIN_MS) {
    await new Promise(r => setTimeout(r, 400));
  }

  if (activePromptsCount > 0) {
    log.warn(`Forced exit with ${activePromptsCount} in-flight Telegram prompt(s) after drain timeout`);
  } else {
    log.info('All Telegram in-flight prompts drained cleanly.');
  }

  process.exit(0);
}

process.once('SIGINT', () => gracefulShutdown('SIGINT'));
process.once('SIGTERM', () => gracefulShutdown('SIGTERM'));

// Catch uncaught exceptions (prevent silent crashes)
process.on('uncaughtException', (err) => {
  log.error('Uncaught exception', { error: err.message, stack: err.stack });
  // Don't exit - let PM2 handle it if truly fatal
});

process.on('unhandledRejection', (reason) => {
  log.error('Unhandled rejection', { error: String(reason) });
});

bot.launch().then(async () => {
  log.info('🤖 Tiny (Jax Antigravity Telegram Agent) is active with Hermes/OpenClaw-grade robustness!');
  log.info('Features: Persistent Memory, Rate Limiting, Circuit Breaker, Message Dedup, Dead Letter Queue, Health Endpoint');

  // In-flight message recovery: detect interrupted messages and notify user
  try {
    const unfinishedTg = getUnfinishedInFlight('telegram');
    if (unfinishedTg && unfinishedTg.length > 0) {
      log.warn(`Detected ${unfinishedTg.length} unfinished Telegram prompt(s) from prior session`, { count: unfinishedTg.length });
      for (const item of unfinishedTg) {
        if (item.userId) {
          const preview = (item.prompt || '').slice(0, 80);
          await bot.telegram.sendMessage(item.userId, `🔄 *Session Restored*\nI restarted while processing your last request: _"${preview}..."_\nI am back online and ready for your message!`, { parse_mode: 'Markdown' }).catch(() => {});
        }
      }
    }
  } catch (e) {
    log.error('Error checking in-flight recovery on Telegram', { error: e.message });
  }
}).catch((err) => {
  // C1: Handle launch failures (network timeout, 409 Conflict, bad token)
  log.error('Fatal: bot.launch() failed - bot is NOT running', { error: err.message });
  // Exit cleanly so PM2 can restart with backoff
  process.exit(1);
});
