import { DatabaseSync } from 'node:sqlite';
import fs from 'fs';
import path from 'path';
import { BufferJSON, initAuthCreds, proto } from '@whiskeysockets/baileys';

/**
 * Transactional SQLite Auth Store for Baileys
 * Replaces hundreds of loose JSON files with a single, high-speed, WAL-mode SQLite database.
 * Prevents file-system lock contention, disk write latency, and ratchet rollbacks on process restarts.
 */
export async function useSqliteAuthState(dbPath, legacyFolder = null) {
  const dir = path.dirname(dbPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA synchronous = NORMAL;');
  db.exec('PRAGMA temp_store = MEMORY;');

  db.exec(`
    CREATE TABLE IF NOT EXISTS auth_creds (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      data TEXT NOT NULL,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS auth_keys (
      category TEXT NOT NULL,
      key_id TEXT NOT NULL,
      value TEXT NOT NULL,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (category, key_id)
    );
    CREATE INDEX IF NOT EXISTS idx_auth_keys_cat ON auth_keys(category);
  `);

  const selectCredsStmt = db.prepare('SELECT data FROM auth_creds WHERE id = 1');
  const upsertCredsStmt = db.prepare(`
    INSERT INTO auth_creds (id, data, updated_at) VALUES (1, ?, datetime('now'))
    ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
  `);

  const selectKeyStmt = db.prepare('SELECT value FROM auth_keys WHERE category = ? AND key_id = ?');
  const upsertKeyStmt = db.prepare(`
    INSERT INTO auth_keys (category, key_id, value, updated_at) VALUES (?, ?, ?, datetime('now'))
    ON CONFLICT(category, key_id) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `);
  const deleteKeyStmt = db.prepare('DELETE FROM auth_keys WHERE category = ? AND key_id = ?');

  // Check if initial migration from legacy JSON folder is needed
  const existingCredsRow = selectCredsStmt.get();
  if (!existingCredsRow && legacyFolder && fs.existsSync(legacyFolder)) {
    console.log(`[SQLITE AUTH] Migrating auth state from ${legacyFolder} into ${dbPath}...`);
    const credsFile = path.join(legacyFolder, 'creds.json');
    if (fs.existsSync(credsFile)) {
      try {
        const rawCreds = fs.readFileSync(credsFile, 'utf8');
        JSON.parse(rawCreds, BufferJSON.reviver);
        upsertCredsStmt.run(rawCreds);
        console.log(`[SQLITE AUTH] Migrated creds.json into SQLite`);
      } catch (e) {
        console.error(`[SQLITE AUTH] Failed to migrate creds.json:`, e);
      }
    }

    try {
      const files = fs.readdirSync(legacyFolder);
      let migratedKeys = 0;
      db.exec('BEGIN IMMEDIATE;');
      for (const file of files) {
        if (!file.endsWith('.json') || file === 'creds.json') continue;
        const dashIdx = file.indexOf('-');
        if (dashIdx === -1) continue;
        const category = file.slice(0, dashIdx);
        const keyId = file.slice(dashIdx + 1, -5).replace(/__/g, '/').replace(/-/g, ':');
        try {
          const raw = fs.readFileSync(path.join(legacyFolder, file), 'utf8');
          upsertKeyStmt.run(category, keyId, raw);
          migratedKeys++;
        } catch (e) {}
      }
      db.exec('COMMIT;');
      console.log(`[SQLITE AUTH] Successfully migrated ${migratedKeys} keys into SQLite database`);
    } catch (e) {
      try { db.exec('ROLLBACK;'); } catch (_) {}
      console.error(`[SQLITE AUTH] Key migration error:`, e);
    }
  }

  let creds;
  const credsRow = selectCredsStmt.get();
  if (credsRow && credsRow.data) {
    try {
      creds = JSON.parse(credsRow.data, BufferJSON.reviver);
    } catch (e) {
      console.error('[SQLITE AUTH] Failed to parse creds from SQLite, reinitializing:', e);
      creds = initAuthCreds();
    }
  } else {
    creds = initAuthCreds();
  }

  return {
    state: {
      creds,
      keys: {
        get: async (type, ids) => {
          const data = {};
          for (const id of ids) {
            try {
              const row = selectKeyStmt.get(type, String(id));
              if (row && row.value) {
                let value = JSON.parse(row.value, BufferJSON.reviver);
                if (type === 'app-state-sync-key' && value) {
                  value = proto.Message.AppStateSyncKeyData.fromObject(value);
                }
                data[id] = value;
              } else {
                data[id] = null;
              }
            } catch (e) {
              data[id] = null;
            }
          }
          return data;
        },
        set: async (data) => {
          db.exec('BEGIN IMMEDIATE;');
          try {
            for (const category in data) {
              for (const id in data[category]) {
                const value = data[category][id];
                if (value) {
                  const serialized = JSON.stringify(value, BufferJSON.replacer);
                  upsertKeyStmt.run(category, String(id), serialized);
                } else {
                  deleteKeyStmt.run(category, String(id));
                }
              }
            }
            db.exec('COMMIT;');
          } catch (err) {
            try { db.exec('ROLLBACK;'); } catch (_) {}
            throw err;
          }
        }
      }
    },
    saveCreds: async () => {
      const serialized = JSON.stringify(creds, BufferJSON.replacer);
      upsertCredsStmt.run(serialized);
    },
    close: () => {
      try { db.close(); } catch (_) {}
    }
  };
}
