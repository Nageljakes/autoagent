import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { fileURLToPath } from 'url';
import { DatabaseSync } from 'node:sqlite';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DB_PATH = process.env.SQLITE_DB_PATH || 'jax-shared/data/prospects.db';
const SHARED_DIR = process.env.SHARED_DATA_DIR || path.resolve(__dirname, '..', 'jax-shared', 'data');
const db = new DatabaseSync(DB_PATH);

console.log('🚀 Starting full chat history import into SQLite...');

db.exec('PRAGMA journal_mode = WAL;');
db.exec('PRAGMA synchronous = NORMAL;');

// Clean up dummy [Media/Message] messages from failed decryptions
const deleteResult = db.prepare("DELETE FROM messages WHERE content = '[Media/Message]'").run();
console.log(`🧹 Cleaned up ${deleteResult.changes} empty/dummy [Media/Message] artifact rows.`);

function makeMsgId(prefix, phoneOrJid, text, timestamp) {
  const hash = crypto.createHash('md5').update(`${phoneOrJid}_${text}_${timestamp}`).digest('hex');
  return `${prefix}_${hash.slice(0, 16)}`;
}

// 1. Seed Known Contacts
const KNOWN_CONTACTS = [
];

const upsertContactStmt = db.prepare(`
  INSERT INTO prospects (jid, phone_number, name, contact_type, tags, notes, last_interaction_at, message_count)
  VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 0)
  ON CONFLICT(jid) DO UPDATE SET
    phone_number = COALESCE(excluded.phone_number, prospects.phone_number),
    name = COALESCE(excluded.name, prospects.name),
    contact_type = COALESCE(excluded.contact_type, prospects.contact_type),
    tags = COALESCE(excluded.tags, prospects.tags),
    notes = COALESCE(excluded.notes, prospects.notes)
`);

for (const c of KNOWN_CONTACTS) {
  upsertContactStmt.run(c.jid, c.phone, c.name, c.type, c.tags, c.notes);
}

// 2. Import from /data/users/
const usersDir = path.join(SHARED_DIR, 'users');
if (fs.existsSync(usersDir)) {
  const userFiles = fs.readdirSync(usersDir);
  for (const file of userFiles) {
    if (!file.endsWith('.json')) continue;
    try {
      const filePath = path.join(usersDir, file);
      const user = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      const phone = user.userId ? user.userId.replace(/[^0-9]/g, '') : null;
      if (phone) {
        const jid = phone.length > 13 ? `${phone}@lid` : `${phone}@s.whatsapp.net`;
        const contactType = user.isOwner || user.isVip ? 'vip' : 'prospect';
        upsertContactStmt.run(jid, phone, name, contactType, null, null);
      }
    } catch (e) {
      console.error(`Error reading user file ${file}:`, e.message);
    }
  }
}

const insertMsgStmt = db.prepare(`
  INSERT OR IGNORE INTO messages (
    id, prospect_jid, phone_number, from_me, sender_name, message_type, content, media_url, timestamp
  ) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?
  )
`);

let importedCount = 0;

// 3. Import from /data/conversations/
const convDir = path.join(SHARED_DIR, 'conversations');
if (fs.existsSync(convDir)) {
  const files = fs.readdirSync(convDir);
  for (const file of files) {
    if (!file.startsWith('whatsapp_') || !file.endsWith('.json')) continue;
    const phone = file.replace('whatsapp_', '').replace('.json', '');
    const jid = phone.length > 13 ? `${phone}@lid` : `${phone}@s.whatsapp.net`;

    try {
      const raw = fs.readFileSync(path.join(convDir, file), 'utf8');
      const conv = JSON.parse(raw);
      if (Array.isArray(conv)) {
        for (const item of conv) {
          if (!item.content || !item.content.trim()) continue;
          const isAssistant = item.role === 'assistant';
          const ts = item.timestamp ? Math.floor(new Date(item.timestamp).getTime() / 1000) : Math.floor(Date.now() / 1000);
          const msgId = makeMsgId('conv', phone, item.content, ts);

          insertMsgStmt.run(
            msgId,
            jid,
            phone,
            isAssistant ? 1 : 0,
            isAssistant ? 'You / Agent' : senderName,
            'text',
            item.content.trim(),
            null,
            ts
          );
          importedCount++;
        }
      }
    } catch (e) {
      console.error(`Error reading conversation file ${file}:`, e.message);
    }
  }
}

// 4. Import from PM2 logs (/data/logs/pm2-whatsapp-out.log)
const pm2LogPath = path.join(SHARED_DIR, 'logs/pm2-whatsapp-out.log');
if (fs.existsSync(pm2LogPath)) {
  const lines = fs.readFileSync(pm2LogPath, 'utf8').split('\n');
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const entry = JSON.parse(line);
      const text = entry.message || '';
      const ts = entry.timestamp ? Math.floor(new Date(entry.timestamp).getTime() / 1000) : Math.floor(Date.now() / 1000);

      // Match [WA MSG IN] pattern
      const inMatch = text.match(/\[WA MSG IN\] from=(\d+)\s*\(([^)]*)\).*text="([^"]+)"/);
      if (inMatch) {
        const phone = inMatch[1];
        const rawContent = inMatch[3].trim();
        if (rawContent && rawContent.length > 0) {
          const jid = phone.length > 13 ? `${phone}@lid` : `${phone}@s.whatsapp.net`;
          const msgId = makeMsgId('log_in', phone, rawContent, ts);

          insertMsgStmt.run(
            msgId,
            jid,
            phone,
            0,
            senderName,
            'text',
            rawContent,
            null,
            ts
          );
          importedCount++;
        }
      }

      // Match Message from pattern
      const msgFromMatch = text.match(/Message from [^:]*?(\d+):\s*(.+)$/);
      if (msgFromMatch) {
        const phone = msgFromMatch[1];
        const rawContent = msgFromMatch[2].trim();
        if (rawContent && rawContent.length > 0 && !rawContent.startsWith('AGY exec')) {
          const jid = phone.length > 13 ? `${phone}@lid` : `${phone}@s.whatsapp.net`;
          const msgId = makeMsgId('log_msg', phone, rawContent, ts);

          insertMsgStmt.run(
            msgId,
            jid,
            phone,
            0,
            senderName,
            'text',
            rawContent,
            null,
            ts
          );
          importedCount++;
        }
      }
    } catch (e) {}
  }
}

// 5. Update message counts & last interaction timestamps for prospects
db.exec(`
  UPDATE prospects
  SET 
    message_count = (SELECT COUNT(*) FROM messages WHERE messages.prospect_jid = prospects.jid OR messages.phone_number = prospects.phone_number),
    last_interaction_at = (SELECT datetime(MAX(timestamp), 'unixepoch') FROM messages WHERE messages.prospect_jid = prospects.jid OR messages.phone_number = prospects.phone_number)
  WHERE (SELECT COUNT(*) FROM messages WHERE messages.prospect_jid = prospects.jid OR messages.phone_number = prospects.phone_number) > 0;
`);

const finalStats = db.prepare('SELECT count(*) as msg_count FROM messages').get();
const contactStats = db.prepare('SELECT count(*) as contact_count FROM prospects WHERE message_count > 0').get();
const prospectsList = db.prepare('SELECT name, phone_number, contact_type, message_count, last_interaction_at FROM prospects WHERE message_count > 0').all();

console.log(`\n🎉 Import completed successfully!`);
console.log(`📊 Total Messages in Database: ${finalStats.msg_count}`);
console.log(`👥 Contacts with Conversations: ${contactStats.contact_count}`);
console.table(prospectsList);
