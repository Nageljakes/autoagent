import test, {after, beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'autoagent-history-'));
process.env.SQLITE_DB_PATH = path.join(dir, 'synthetic.db');
const {db, saveMessage, getProspectHistory} = await import('../jax-whatsapp-monitor/db.mjs');
after(() => { db.close(); for (const file of fs.readdirSync(dir)) fs.unlinkSync(path.join(dir,file)); fs.rmdirSync(dir); });
beforeEach(() => { db.exec('DELETE FROM messages; DELETE FROM prospects;'); });
function message(phone, name, id, timestamp = 1) {
  saveMessage({id, jid:`${phone}@s.whatsapp.net`, phoneNumber:phone, senderName:name, fromMe:false, content:id, timestamp});
}

test('phone history never expands through shared names, notes or linking tags', () => {
  message('27820000001','Alex Synthetic','one');
  message('27820000002','Alex Synthetic','two');
  db.prepare('UPDATE prospects SET tags = ?, notes = ? WHERE phone_number = ?').run('lid_link:27820000002','Alex Synthetic','27820000001');
  assert.deepEqual(getProspectHistory('27820000001').messages.map(m=>m.id), ['one']);
  assert.deepEqual(getProspectHistory('0820000001').messages.map(m=>m.id), ['one']);
});
test('ambiguous names fail closed and SQL wildcards are literal', () => {
  message('27820000001','Alex Synthetic','one');
  message('27820000002','Alex Synthetic','two');
  assert.throws(()=>getProspectHistory('Alex'), {code:'AMBIGUOUS_CUSTOMER',status:409});
  assert.equal(getProspectHistory('%').messages.length,0);
});
test('typed LID is separate from a phone with identical digits', () => {
  message('27820000001','Phone Customer','phone');
  db.prepare('INSERT INTO prospects (jid, name) VALUES (?, ?)').run('27820000001@lid','LID Customer');
  db.prepare('INSERT INTO messages (id,prospect_jid,phone_number,from_me,content,timestamp) VALUES (?,?,?,?,?,?)').run('lid','27820000001@lid','27820000001',0,'private LID',1);
  assert.deepEqual(getProspectHistory('27820000001').messages.map(m=>m.id),['phone']);
  assert.deepEqual(getProspectHistory('27820000001:2@lid').messages.map(m=>m.id),['lid']);
});
test('history returns newest window chronologically and paginates backwards', () => {
  for (let i=1;i<=6;i++) message('27820000001','Alex',String(i),i);
  assert.deepEqual(getProspectHistory('27820000001',2).messages.map(m=>m.id),['5','6']);
  assert.deepEqual(getProspectHistory('27820000001',2,2).messages.map(m=>m.id),['3','4']);
});
test('unique name and phone JID remain usable and input bounds are enforced', () => {
  message('27820000001','Alex Synthetic','one');
  assert.equal(getProspectHistory('Alex').count,1);
  assert.equal(getProspectHistory('27820000001:2@s.whatsapp.net').count,1);
  assert.throws(()=>getProspectHistory('27820000001',-1),{status:400});
  assert.throws(()=>getProspectHistory('27820000001',501),{status:400});
  assert.throws(()=>getProspectHistory('27820000001',10,-1),{status:400});
  assert.throws(()=>getProspectHistory('group@g.us'),{status:400});
});
