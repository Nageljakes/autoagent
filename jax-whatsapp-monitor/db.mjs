import { DatabaseSync } from 'node:sqlite';
import path from 'path';
import fs from 'fs';
import { normalizeWhatsAppJid } from '../jax-shared/owner-identity.mjs';

const DB_PATH = process.env.SQLITE_DB_PATH || path.resolve('./data/prospects.db');

// Ensure directory exists
const dbDir = path.dirname(DB_PATH);
if (!fs.existsSync(dbDir)) {
  fs.mkdirSync(dbDir, { recursive: true });
}

export const db = new DatabaseSync(DB_PATH);

// Optimize SQLite for concurrent reads and writes (WAL Mode)
db.exec('PRAGMA journal_mode = WAL;');
db.exec('PRAGMA synchronous = NORMAL;');
db.exec('PRAGMA foreign_keys = ON;');

// Initialize tables
db.exec(`
  CREATE TABLE IF NOT EXISTS prospects (
    jid TEXT PRIMARY KEY,
    phone_number TEXT UNIQUE,
    name TEXT,
    contact_type TEXT DEFAULT 'prospect', -- 'prospect', 'coworker', 'internal_team', 'personal', 'vip'
    tags TEXT,                            -- comma-separated tags or JSON
    notes TEXT,
    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_interaction_at DATETIME,
    message_count INTEGER DEFAULT 0
  );

  CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    prospect_jid TEXT,
    phone_number TEXT,
    from_me INTEGER NOT NULL, -- 0: Contact, 1: Us (Human/Explicit)
    sender_name TEXT,
    message_type TEXT,        -- text, image, document, audio, video, etc.
    content TEXT,             -- Text content or caption
    media_url TEXT,
    timestamp INTEGER NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(prospect_jid) REFERENCES prospects(jid)
  );

  CREATE TABLE IF NOT EXISTS explicit_send_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_phone TEXT,
    content TEXT,
    authorized_by TEXT,       -- e.g. 'owner_instruction', 'agent_explicit_command'
    status TEXT,              -- 'SENT', 'FAILED'
    msg_id TEXT,
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );
`);

// Migration for existing table columns
try {
  db.exec("ALTER TABLE prospects ADD COLUMN contact_type TEXT DEFAULT 'prospect';");
} catch (e) {}
try {
  db.exec("ALTER TABLE prospects ADD COLUMN tags TEXT;");
} catch (e) {}

// Create indexes
db.exec(`
  CREATE INDEX IF NOT EXISTS idx_messages_phone_time ON messages(phone_number, timestamp DESC);
  CREATE INDEX IF NOT EXISTS idx_messages_jid_time ON messages(prospect_jid, timestamp DESC);
  CREATE INDEX IF NOT EXISTS idx_prospects_last_interaction ON prospects(last_interaction_at DESC);
  CREATE INDEX IF NOT EXISTS idx_prospects_contact_type ON prospects(contact_type);
`);

// Parse known creator and internal numbers from environment
const CREATOR_IDENTIFIERS = new Set(
  (process.env.CREATOR_IDENTIFIERS || process.env.OWNER_PHONE_NUMBER || '')
    .split(',')
    .map(s => s.trim().replace(/[^0-9]/g, ''))
    .filter(Boolean)
);

export function getKnownCoworkerSet() {
  const envCoworkers = process.env.COWORKER_PHONE_NUMBERS || '';
  const set = new Set();
  for (const num of envCoworkers.split(',')) {
    const clean = num.replace(/[^0-9]/g, '');
    if (clean) set.add(clean);
  }
  return set;
}

/**
 * Upsert a contact/prospect record with classification
 */
export function upsertProspect(jid, phoneNumber, name, contactType = null, tags = null) {
  const cleanPhone = phoneNumber ? phoneNumber.replace(/[^0-9]/g, '') : (jid ? jid.replace(/[^0-9]/g, '') : null);
  const coworkerSet = getKnownCoworkerSet();
  
  // Auto-detect creator/VIP and coworkers
  const isCreator = cleanPhone && CREATOR_IDENTIFIERS.has(cleanPhone);
  const isEnvCoworker = cleanPhone && coworkerSet.has(cleanPhone);
  const resolvedType = contactType || (isCreator ? 'vip' : (isEnvCoworker ? 'coworker' : null));
  const resolvedTags = tags || (isCreator ? 'creator,owner' : null);

  try {
    const existing = db.prepare(`SELECT jid, phone_number, name, contact_type, tags, message_count FROM prospects WHERE jid = ? OR (phone_number IS NOT NULL AND phone_number != '' AND phone_number = ?)`).get(jid, cleanPhone || '');

    if (existing) {
      const updatedType = resolvedType || existing.contact_type;
      const updatedTags = tags || existing.tags;
      const updatedName = name || existing.name;
      const updatedPhone = cleanPhone || existing.phone_number;
      
      db.prepare(`
        UPDATE prospects SET
          last_interaction_at = datetime('now'),
          phone_number = ?,
          name = ?,
          contact_type = ?,
          tags = ?,
          message_count = message_count + 1
        WHERE jid = ?
      `).run(updatedPhone, updatedName, updatedType, updatedTags, existing.jid);
    } else {
      db.prepare(`
        INSERT INTO prospects (jid, phone_number, name, contact_type, tags, last_interaction_at, message_count)
        VALUES (?, ?, ?, COALESCE(?, 'prospect'), ?, datetime('now'), 1)
      `).run(jid, cleanPhone, name || null, resolvedType, tags || null);
    }
  } catch (err) {
    // Fallback update to prevent any unhandled constraint errors
    try {
      db.prepare("UPDATE prospects SET last_interaction_at = datetime('now'), message_count = message_count + 1 WHERE phone_number = ? OR jid = ?").run(cleanPhone || '', jid);
    } catch (e) {}
  }
}

/**
 * Explicitly tag or categorize a contact (e.g. coworker, prospect, VIP)
 */
export function tagContact(phoneOrJid, { contactType, tags, notes, name } = {}) {
  const cleanPhone = phoneOrJid.replace(/[^0-9]/g, '');
  const jid = phoneOrJid.includes('@') ? phoneOrJid : `${cleanPhone}@s.whatsapp.net`;

  upsertProspect(jid, cleanPhone, name, contactType, tags);

  const updates = [];
  const params = [];

  if (contactType !== undefined) {
    updates.push("contact_type = ?");
    params.push(contactType);
  }
  if (tags !== undefined) {
    updates.push("tags = ?");
    params.push(tags);
  }
  if (notes !== undefined) {
    updates.push("notes = ?");
    params.push(notes);
  }
  if (name !== undefined) {
    updates.push("name = ?");
    params.push(name);
  }

  if (updates.length > 0) {
    params.push(cleanPhone, jid);
    const sql = `UPDATE prospects SET ${updates.join(', ')} WHERE phone_number = ? OR jid = ?`;
    db.prepare(sql).run(...params);
  }

  return db.prepare("SELECT * FROM prospects WHERE phone_number = ? OR jid = ?").get(cleanPhone, jid);
}

/**
 * Save an incoming or outgoing message
 */
export function saveMessage({ id, jid, phoneNumber, fromMe, senderName, messageType, content, mediaUrl, timestamp }) {
  const cleanPhone = phoneNumber ? phoneNumber.replace(/[^0-9]/g, '') : (jid ? jid.replace(/[^0-9]/g, '') : null);

  // Ensure contact exists first
  upsertProspect(jid, cleanPhone, fromMe ? null : senderName);

  // upsertProspect may have matched (and updated) a different existing row by
  // phone_number rather than jid - e.g. a group participant who already has a
  // 1:1 prospect record. Re-resolve the actual row here so prospect_jid always
  // references a row that exists, otherwise the FOREIGN KEY insert below fails
  // and the message is silently lost.
  const prospectRow = db.prepare(
    `SELECT jid FROM prospects WHERE jid = ? OR (phone_number IS NOT NULL AND phone_number != '' AND phone_number = ?)`
  ).get(jid, cleanPhone || '');
  const resolvedJid = prospectRow ? prospectRow.jid : jid;

  const stmt = db.prepare(`
    INSERT OR IGNORE INTO messages (
      id, prospect_jid, phone_number, from_me, sender_name, message_type, content, media_url, timestamp
    ) VALUES (
      ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
  `);

  return stmt.run(
    id,
    resolvedJid,
    cleanPhone,
    fromMe ? 1 : 0,
    senderName || (fromMe ? 'You / Agent' : 'Contact'),
    messageType || 'text',
    content || '',
    mediaUrl || null,
    timestamp || Math.floor(Date.now() / 1000)
  );
}

/**
 * Get conversation history for a prospect/coworker by phone number, JID, LID, or Name
 */
export function getProspectHistory(phoneOrQuery, limit = 50, offset = 0) {
  const query = String(phoneOrQuery || '').trim();
  const empty = { contact: null, matched_contacts: [], matched_jids: [], matched_phones: [], count: 0, messages: [] };
  if (!query) return empty;
  if (!Number.isInteger(limit) || limit < 1 || limit > 500 || !Number.isInteger(offset) || offset < 0) {
    const error = new Error('limit must be 1..500 and offset must be nonnegative');
    error.status = 400;
    throw error;
  }
  let contacts = [];
  const jids = new Set();
  if (query.includes('@')) {
    const jid = normalizeWhatsAppJid(query);
    if (!jid) {
      const error = new Error('A direct phone or LID identity is required');
      error.status = 400;
      throw error;
    }
    jids.add(jid);
    contacts = db.prepare('SELECT * FROM prospects WHERE jid = ?').all(jid);
  } else if (/^\+?\d[\d ()-]*$/.test(query)) {
    const digits = query.replace(/\D/g, '');
    const variants = [digits];
    if (digits.startsWith('0') && digits.length === 10) variants.push('27' + digits.slice(1));
    if (digits.startsWith('27') && digits.length === 11) variants.push('0' + digits.slice(2));
    for (const phone of variants) jids.add(`${phone}@s.whatsapp.net`);
    const placeholders = variants.map(() => '?').join(',');
    // Only phone identities can match phone digits. LIDs are never phone numbers.
    contacts = db.prepare(`SELECT * FROM prospects WHERE jid LIKE '%@s.whatsapp.net'
      AND (phone_number IN (${placeholders}) OR jid IN (${placeholders}))`).all(...variants, ...jids);
  } else {
    // Names locate candidates; they never establish links between identities.
    contacts = db.prepare(`SELECT * FROM prospects WHERE instr(lower(name), lower(?)) > 0
      AND (jid LIKE '%@s.whatsapp.net' OR jid LIKE '%@lid') LIMIT 2`).all(query);
    if (contacts.length > 1) {
      const error = new Error('Multiple customers match. Use an exact phone number or LID.');
      error.status = 409;
      error.code = 'AMBIGUOUS_CUSTOMER';
      throw error;
    }
    if (!contacts.length) return empty;
  }
  for (const contact of contacts) jids.add(contact.jid);
  const ids = [...jids];
  const placeholders = ids.map(() => '?').join(',');
  const messages = db.prepare(`SELECT id, from_me, sender_name, message_type, content, media_url,
      timestamp, datetime(timestamp, 'unixepoch', 'localtime') as message_time
    FROM messages WHERE prospect_jid IN (${placeholders})
    ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?`).all(...ids, limit, offset).reverse();
  return {
    contact: contacts[0] || {jid:ids[0], phone_number:query.includes('@') ? null : query.replace(/\D/g, ''), contact_type:'prospect'},
    matched_contacts: contacts,
    matched_jids: ids,
    matched_phones: contacts.filter(c => c.jid.endsWith('@s.whatsapp.net')).map(c => c.phone_number).filter(Boolean),
    count: messages.length,
    messages
  };
}

/**
 * List contacts with optional filtering by contact_type
 * @param {string} filterType - 'all', 'prospect', 'coworker', 'vip'
 */
export function listRecentProspects(limit = 20, filterType = 'all') {
  let whereClause = "";
  const params = [];

  if (filterType && filterType !== 'all') {
    whereClause = "WHERE p.contact_type = ?";
    params.push(filterType);
  }

  params.push(limit);

  const stmt = db.prepare(`
    SELECT 
      p.jid,
      p.phone_number,
      p.name,
      p.contact_type,
      p.tags,
      p.notes,
      p.message_count,
      p.last_interaction_at,
      (
        SELECT content 
        FROM messages m 
        WHERE m.prospect_jid = p.jid 
        ORDER BY timestamp DESC 
        LIMIT 1
      ) as last_message
    FROM prospects p
    ${whereClause}
    ORDER BY p.last_interaction_at DESC
    LIMIT ?
  `);
  return stmt.all(...params);
}

/**
 * Search message content with optional contact_type filter
 */
export function searchMessages(query, limit = 20, filterType = 'all') {
  let whereClause = "WHERE m.content LIKE ?";
  const params = [`%${query}%`];

  if (filterType && filterType !== 'all') {
    whereClause += " AND p.contact_type = ?";
    params.push(filterType);
  }

  params.push(limit);

  const stmt = db.prepare(`
    SELECT 
      m.id,
      m.phone_number,
      m.sender_name,
      m.from_me,
      m.content,
      m.timestamp,
      datetime(m.timestamp, 'unixepoch', 'localtime') as message_time,
      p.name as contact_name,
      p.contact_type,
      p.tags
    FROM messages m
    LEFT JOIN prospects p ON m.prospect_jid = p.jid
    ${whereClause}
    ORDER BY m.timestamp DESC
    LIMIT ?
  `);
  return stmt.all(...params);
}

/**
 * Log an explicitly authorized outbound message
 */
export function logAuditSend({ prospectPhone, content, authorizedBy, status, msgId, errorMessage }) {
  const cleanPhone = prospectPhone.replace(/[^0-9]/g, '');
  const stmt = db.prepare(`
    INSERT INTO explicit_send_audit_log (
      prospect_phone, content, authorized_by, status, msg_id, error_message
    ) VALUES (
      ?, ?, ?, ?, ?, ?
    )
  `);
  return stmt.run(cleanPhone, content, authorizedBy, status, msgId || null, errorMessage || null);
}

export default db;
