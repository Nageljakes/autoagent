/**
 * JAX Shared Persistent Memory Module
 * 
 * Provides Hermes/OpenClaw-grade persistence using JSON file storage:
 * - Conversation history (per user, cross-session)
 * - User preferences & facts
 * - Message deduplication
 * - Dead letter queue for failed messages
 * - Cross-platform identity linking (Telegram ↔ WhatsApp)
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = process.env.MEMORY_DIR || path.resolve(__dirname, 'data');

// Ensure data directories exist
const DIRS = ['conversations', 'users', 'deadletters', 'logs'];
for (const d of DIRS) {
  const dir = path.join(DATA_DIR, d);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

// ===========================
// Single-Instance PID Lock
// ===========================

export function acquireProcessLock(lockName) {
  const lockFile = path.join('/tmp', `${lockName}.pid`);
  if (fs.existsSync(lockFile)) {
    try {
      const existingPid = parseInt(fs.readFileSync(lockFile, 'utf-8').trim(), 10);
      if (existingPid && existingPid !== process.pid) {
        // Check if process is alive
        try {
          process.kill(existingPid, 0);
          console.error(`❌ [MUTEX LOCK] Another instance of ${lockName} is already active (PID: ${existingPid}). Aborting.`);
          process.exit(0);
        } catch (e) {
          if (e.code === 'EPERM') {
            // Process exists but owned by different user — treat as alive, abort
            console.error(`❌ [MUTEX LOCK] Another instance of ${lockName} is active (PID: ${existingPid}, EPERM). Aborting.`);
            process.exit(0);
          }
          // ESRCH = stale lock from dead process — continue
        }
      }
    } catch (e) {}
  }
  try {
    fs.writeFileSync(lockFile, String(process.pid));
    process.on('exit', () => {
      try { fs.unlinkSync(lockFile); } catch (e) {}
    });
  } catch (e) {}
}

// ===========================
// JSON File Helpers
// ===========================

function readJSON(filePath, fallback = null) {
  try {
    if (fs.existsSync(filePath)) {
      return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    }
  } catch (e) {
    console.error(`[MEMORY] Error reading ${filePath}:`, e.message);
  }
  return fallback;
}

function writeJSON(filePath, data) {
  try {
    const dir = path.dirname(filePath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    // Atomic write: write to temp file first, then rename (prevents corrupt reads on crash)
    const tmpFile = filePath + '.tmp.' + process.pid;
    fs.writeFileSync(tmpFile, JSON.stringify(data, null, 2), 'utf-8');
    fs.renameSync(tmpFile, filePath);
  } catch (e) {
    console.error(`[MEMORY] Error writing ${filePath}:`, e.message);
  }
}

// ===========================
// User Profile Store
// ===========================

function getUserFilePath(userId, platform) {
  const safeId = String(userId).replace(/[^a-zA-Z0-9_\-]/g, '_');
  return path.join(DATA_DIR, 'users', `${platform}_${safeId}.json`);
}

export function getUserProfile(userId, platform = 'telegram') {
  const filePath = getUserFilePath(userId, platform);
  return readJSON(filePath, {
    userId: String(userId),
    platform,
    displayName: '',
    isOwner: false,
    isVip: false,
    voiceMode: false,
    voiceSelection: '',
    preferences: {},
    facts: [],
    linkedPlatforms: {},
    firstSeen: new Date().toISOString(),
    lastSeen: new Date().toISOString(),
    messageCount: 0,
    sessionCount: 0
  });
}

export function saveUserProfile(userId, platform, updates) {
  const filePath = getUserFilePath(userId, platform);
  const existing = getUserProfile(userId, platform);
  const merged = { ...existing, ...updates, lastSeen: new Date().toISOString() };
  writeJSON(filePath, merged);
  return merged;
}

export function incrementUserStats(userId, platform) {
  const profile = getUserProfile(userId, platform);
  profile.messageCount = (profile.messageCount || 0) + 1;
  profile.lastSeen = new Date().toISOString();
  saveUserProfile(userId, platform, profile);
}

export function addUserFact(userId, platform, fact) {
  const profile = getUserProfile(userId, platform);
  if (!profile.facts) profile.facts = [];
  // Avoid duplicates
  if (!profile.facts.includes(fact)) {
    profile.facts.push(fact);
    if (profile.facts.length > 50) {
      profile.facts = profile.facts.slice(-50); // Keep last 50 facts
    }
    saveUserProfile(userId, platform, profile);
  }
}

// ===========================
// Conversation History Store
// ===========================

function getConvoFilePath(userId, platform) {
  const safeId = String(userId).replace(/[^a-zA-Z0-9_\-]/g, '_');
  return path.join(DATA_DIR, 'conversations', `${platform}_${safeId}.json`);
}

export function getConversationHistory(userId, platform = 'telegram', limit = 20) {
  const filePath = getConvoFilePath(userId, platform);
  const history = readJSON(filePath, []);
  return history.slice(-limit);
}

export function appendConversation(userId, platform, role, content) {
  const filePath = getConvoFilePath(userId, platform);
  const history = readJSON(filePath, []);
  history.push({
    role,
    content: content.slice(0, 2000), // Cap stored content to 2KB per message
    timestamp: new Date().toISOString()
  });
  // Keep last 100 exchanges max (200 messages user+assistant)
  const trimmed = history.slice(-200);
  writeJSON(filePath, trimmed);
}

export function clearConversationHistory(userId, platform) {
  const filePath = getConvoFilePath(userId, platform);
  writeJSON(filePath, []);
}

/**
 * Build a context summary from recent conversation history.
 * This gets injected into the agy prompt so the LLM has cross-session context.
 */
export function buildContextSummary(userId, platform, maxEntries = 6) {
  const history = getConversationHistory(userId, platform, maxEntries);
  if (history.length === 0) return '';

  const lines = history.map(h => {
    const role = h.role === 'user' ? 'User' : 'Tiny';
    const content = h.content.slice(0, 300);
    return `${role}: ${content}`;
  });
  return `[Recent conversation context (last ${lines.length} exchanges):\n${lines.join('\n')}\n]`;
}

// ===========================
// Message Deduplication
// ===========================

const processedMessages = new Map(); // msgId -> timestamp
const DEDUP_WINDOW_MS = 60 * 1000; // 60 second dedup window

export function isDuplicateMessage(msgId) {
  const now = Date.now();
  // Clean old entries
  for (const [id, ts] of processedMessages) {
    if (now - ts > DEDUP_WINDOW_MS) {
      processedMessages.delete(id);
    }
  }
  if (processedMessages.has(msgId)) {
    return true;
  }
  processedMessages.set(msgId, now);
  return false;
}

// ===========================
// Rate Limiter
// ===========================

const rateLimitBuckets = new Map(); // userId -> { count, windowStart }
const RATE_LIMIT_WINDOW_MS = 60 * 1000; // 1 minute
const RATE_LIMIT_MAX_OWNER = 30;
const RATE_LIMIT_MAX_GUEST = 10;

export function checkRateLimit(userId, isOwner = false) {
  const now = Date.now();
  const maxMessages = isOwner ? RATE_LIMIT_MAX_OWNER : RATE_LIMIT_MAX_GUEST;
  
  let bucket = rateLimitBuckets.get(userId);
  if (!bucket || (now - bucket.windowStart) > RATE_LIMIT_WINDOW_MS) {
    bucket = { count: 0, windowStart: now };
    rateLimitBuckets.set(userId, bucket);
  }
  
  bucket.count++;
  
  if (bucket.count > maxMessages) {
    return {
      allowed: false,
      remaining: 0,
      resetInMs: RATE_LIMIT_WINDOW_MS - (now - bucket.windowStart)
    };
  }
  
  return {
    allowed: true,
    remaining: maxMessages - bucket.count,
    resetInMs: RATE_LIMIT_WINDOW_MS - (now - bucket.windowStart)
  };
}

// ===========================
// Dead Letter Queue
// ===========================

export function saveDeadLetter(userId, platform, prompt, error) {
  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  const safeId = String(userId).replace(/[^a-zA-Z0-9_\-]/g, '_');
  const dlPath = path.join(DATA_DIR, 'deadletters', `${platform}_${safeId}_${ts}.json`);
  writeJSON(dlPath, {
    userId,
    platform,
    prompt: prompt.slice(0, 2000),
    error: String(error).slice(0, 500),
    timestamp: new Date().toISOString(),
    retried: false
  });
}

// ===========================
// Circuit Breaker
// ===========================

const circuitState = {
  failures: 0,
  lastFailure: 0,
  state: 'CLOSED' // CLOSED (normal), OPEN (blocking), HALF_OPEN (testing)
};

const CIRCUIT_FAILURE_THRESHOLD = 5;
const CIRCUIT_RECOVERY_MS = 60 * 1000; // 1 minute cooldown

export function checkCircuitBreaker() {
  const now = Date.now();
  
  if (circuitState.state === 'OPEN') {
    if (now - circuitState.lastFailure > CIRCUIT_RECOVERY_MS) {
      circuitState.state = 'HALF_OPEN';
      return { allowed: true, state: 'HALF_OPEN' };
    }
    return { allowed: false, state: 'OPEN' };
  }
  
  return { allowed: true, state: circuitState.state };
}

export function recordCircuitSuccess() {
  circuitState.failures = 0;
  circuitState.state = 'CLOSED';
}

export function recordCircuitFailure() {
  circuitState.failures++;
  circuitState.lastFailure = Date.now();
  if (circuitState.failures >= CIRCUIT_FAILURE_THRESHOLD) {
    circuitState.state = 'OPEN';
  }
}

// ===========================
// Input Sanitization
// ===========================

const MAX_PROMPT_LENGTH = 4000;
const INJECTION_PATTERNS = [
  /ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)/i,
  /system\s*:\s*you\s+are/i,
  /\[SYSTEM\]/i,
  /\<\|im_start\|\>/i,
  /\<\|endoftext\|\>/i
];

export function sanitizeInput(text) {
  if (!text) return { safe: false, text: '', reason: 'empty' };
  
  let cleaned = text.trim();
  
  // Length check
  if (cleaned.length > MAX_PROMPT_LENGTH) {
    cleaned = cleaned.slice(0, MAX_PROMPT_LENGTH);
  }
  
  // Injection check (for guests only — owner can do anything)
  for (const pattern of INJECTION_PATTERNS) {
    if (pattern.test(cleaned)) {
      return { safe: false, text: cleaned, reason: 'injection_attempt' };
    }
  }
  
  return { safe: true, text: cleaned, reason: 'ok' };
}

// ===========================
// Structured Logger
// ===========================

export function createLogger(platform) {
  const logDir = path.join(DATA_DIR, 'logs');
  
  function log(level, message, meta = {}) {
    const entry = {
      timestamp: new Date().toISOString(),
      level,
      platform,
      message,
      ...meta
    };
    
    // Console output
    const prefix = level === 'ERROR' ? '❌' : level === 'WARN' ? '⚠️' : '📋';
    console.log(`${prefix} [${platform.toUpperCase()}] ${message}`, meta.userId ? `(${meta.userId})` : '');
    
    // File output (daily rotation)
    const dateStr = new Date().toISOString().slice(0, 10);
    const logFile = path.join(logDir, `${platform}_${dateStr}.jsonl`);
    try {
      fs.appendFileSync(logFile, JSON.stringify(entry) + '\n');
    } catch (e) {
      // Silent fail for logging
    }
    
    // Cleanup old logs (keep last 7 days)
    try {
      const files = fs.readdirSync(logDir).filter(f => f.startsWith(`${platform}_`) && f.endsWith('.jsonl'));
      if (files.length > 7) {
        const sorted = files.sort();
        for (const old of sorted.slice(0, files.length - 7)) {
          fs.unlinkSync(path.join(logDir, old));
        }
      }
    } catch (e) {
      // Silent fail
    }
  }
  
  return {
    info: (msg, meta) => log('INFO', msg, meta),
    warn: (msg, meta) => log('WARN', msg, meta),
    error: (msg, meta) => log('ERROR', msg, meta),
    debug: (msg, meta) => log('DEBUG', msg, meta)
  };
}

// ===========================
// Health Status
// ===========================

const healthStatus = {
  startTime: Date.now(),
  messagesProcessed: 0,
  errors: 0,
  lastMessageAt: null,
  platforms: {}
};

export function recordMessageProcessed(platform) {
  healthStatus.messagesProcessed++;
  healthStatus.lastMessageAt = new Date().toISOString();
  if (!healthStatus.platforms[platform]) {
    healthStatus.platforms[platform] = { messages: 0, errors: 0 };
  }
  healthStatus.platforms[platform].messages++;
}

export function recordError(platform) {
  healthStatus.errors++;
  if (healthStatus.platforms[platform]) {
    healthStatus.platforms[platform].errors++;
  }
}

export function getHealthStatus() {
  return {
    status: circuitState.state === 'OPEN' ? 'degraded' : 'healthy',
    uptime: Math.floor((Date.now() - healthStatus.startTime) / 1000),
    messagesProcessed: healthStatus.messagesProcessed,
    errors: healthStatus.errors,
    lastMessageAt: healthStatus.lastMessageAt,
    circuitBreaker: circuitState.state,
    semaphore: getSemaphoreStatus(),
    platforms: healthStatus.platforms
  };
}

// ===========================
// Global Concurrency Limiter / Priority Semaphore (Cross-Process File-Backed)
// ===========================

const MAX_CONCURRENT_AGY = parseInt(process.env.MAX_CONCURRENT_AGY || '1', 10);
const SLOTS_DIR = '/tmp/jax_agy_slots';
if (!fs.existsSync(SLOTS_DIR)) {
  try { fs.mkdirSync(SLOTS_DIR, { recursive: true }); } catch (e) {}
}

function cleanStaleSlots() {
  try {
    const files = fs.readdirSync(SLOTS_DIR).filter(f => f.startsWith('slot_') && f.endsWith('.json'));
    const now = Date.now();
    for (const f of files) {
      const p = path.join(SLOTS_DIR, f);
      try {
        const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
        let isAlive = false;
        if (data.pid) {
          try {
            process.kill(data.pid, 0);
            isAlive = true;
          } catch (e) {
            // EPERM = process exists but owned by different user — treat as alive
            // ESRCH = process truly dead — treat as dead (isAlive stays false)
            if (e.code === 'EPERM') isAlive = true;
          }
        }
        // If process is dead OR lock is older than 8 minutes (long LLM query safety margin)
        if (!isAlive || (now - (data.timestamp || 0)) > 480000) {
          fs.unlinkSync(p);
        }
      } catch (e) {
        try { fs.unlinkSync(p); } catch (err) {}
      }
    }
  } catch (e) {}
}

export function getSemaphoreStatus() {
  cleanStaleSlots();
  let activeSlots = 0;
  try {
    activeSlots = fs.readdirSync(SLOTS_DIR).filter(f => f.startsWith('slot_') && f.endsWith('.json')).length;
  } catch (e) {}
  return {
    maxConcurrency: MAX_CONCURRENT_AGY,
    activeSlots,
    queuedRequests: 0
  };
}

export async function acquireExecutionSlot(isOwner = false, timeoutMs = 120000) {
  const startTime = Date.now();
  const pollInterval = isOwner ? 200 : 400;

  while (Date.now() - startTime < timeoutMs) {
    cleanStaleSlots();

    for (let i = 0; i < MAX_CONCURRENT_AGY; i++) {
      const slotPath = path.join(SLOTS_DIR, `slot_${i}.json`);
      try {
        const slotData = {
          pid: process.pid,
          isOwner,
          timestamp: Date.now()
        };
        fs.writeFileSync(slotPath, JSON.stringify(slotData), { flag: 'wx' });
        
        // Successfully acquired slot — capture our PID at acquisition time
        const acquiredPid = process.pid;
        return () => {
          try {
            if (fs.existsSync(slotPath)) {
              // Only release if this slot still belongs to us (not stolen by cleanStaleSlots)
              const current = JSON.parse(fs.readFileSync(slotPath, 'utf-8'));
              if (current.pid === acquiredPid) {
                fs.unlinkSync(slotPath);
              }
            }
          } catch (e) {}
        };
      } catch (e) {
        // Slot i is occupied, check next
      }
    }

    // Wait before retrying
    await new Promise(r => setTimeout(r, pollInterval));
  }

  throw new Error('Queue timeout: server is busy with concurrent requests.');
}

// ===========================
// In-Flight Request Tracking
// ===========================

const IN_FLIGHT_DIR = path.join(DATA_DIR, 'inflight');
if (!fs.existsSync(IN_FLIGHT_DIR)) {
  try { fs.mkdirSync(IN_FLIGHT_DIR, { recursive: true }); } catch (e) {}
}

export function trackInFlight(platform, userId, prompt) {
  const safeId = String(userId).replace(/[^a-zA-Z0-9_\-]/g, '_');
  const filePath = path.join(IN_FLIGHT_DIR, `${platform}_${safeId}.json`);
  writeJSON(filePath, {
    platform,
    userId: String(userId),
    prompt: (prompt || '').slice(0, 1000),
    timestamp: Date.now()
  });
}

export function clearInFlight(platform, userId) {
  const safeId = String(userId).replace(/[^a-zA-Z0-9_\-]/g, '_');
  const filePath = path.join(IN_FLIGHT_DIR, `${platform}_${safeId}.json`);
  try {
    if (fs.existsSync(filePath)) fs.unlinkSync(filePath);
  } catch (e) {}
}

export function getUnfinishedInFlight(platform, maxAgeMs = 5 * 60 * 1000) {
  const results = [];
  try {
    const files = fs.readdirSync(IN_FLIGHT_DIR).filter(f => f.startsWith(`${platform}_`) && f.endsWith('.json'));
    const now = Date.now();
    for (const f of files) {
      const p = path.join(IN_FLIGHT_DIR, f);
      const data = readJSON(p);
      if (data && (now - (data.timestamp || 0)) < maxAgeMs) {
        results.push(data);
      }
      try { fs.unlinkSync(p); } catch (e) {}
    }
  } catch (e) {}
  return results;
}

// ===========================
// Cross-Platform Identity
// ===========================

const identityMapFile = path.join(DATA_DIR, 'identity_map.json');

export function linkPlatformIdentity(telegramId, whatsappJid) {
  const map = readJSON(identityMapFile, {});
  map[`telegram_${telegramId}`] = whatsappJid;
  map[`whatsapp_${whatsappJid}`] = telegramId;
  writeJSON(identityMapFile, map);
}

export function getLinkedIdentity(userId, fromPlatform) {
  const map = readJSON(identityMapFile, {});
  return map[`${fromPlatform}_${userId}`] || null;
}
