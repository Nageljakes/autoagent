import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {isWhatsAppOwner, normalizeWhatsAppJid} from '../jax-shared/owner-identity.mjs';

const phone = '27820000001';
const lid = '100000000000001';
test('exact configured phone and LID retain their separate namespaces', () => {
  for (const jid of [`${phone}@s.whatsapp.net`, `${phone}:12@s.whatsapp.net`, `${lid}@lid`, `${lid}:7@lid`]) {
    assert.equal(isWhatsAppOwner(jid, {phone, lid}), true, jid);
  }
  for (const jid of [`${phone}@lid`, `${lid}@s.whatsapp.net`, `${phone.slice(1)}@s.whatsapp.net`, `1${phone}@s.whatsapp.net`, `${phone}@g.us`]) {
    assert.equal(isWhatsAppOwner(jid, {phone, lid}), false, jid);
  }
});

test('malformed identities and missing configuration fail closed', () => {
  for (const jid of [null, undefined, {}, '', '@lid', 'abc@s.whatsapp.net', `${phone}:abc@s.whatsapp.net`, `${phone}@s.whatsapp.net.evil`, `${phone}\n@s.whatsapp.net`, `${phone}@s.whatsapp.net\n`]) {
    assert.equal(normalizeWhatsAppJid(jid), null);
    assert.equal(isWhatsAppOwner(jid, {phone, lid}), false);
  }
  assert.equal(isWhatsAppOwner(`${phone}@s.whatsapp.net`), false);
  assert.equal(isWhatsAppOwner(`${phone}@s.whatsapp.net`, {phone:`abc${phone}`}), false);
});

test('trusted configuration permits phone formatting and typed JIDs', () => {
  assert.equal(isWhatsAppOwner(`${phone}@s.whatsapp.net`, {phone:'+27 (82) 000-0001'}), true);
  assert.equal(isWhatsAppOwner(`${phone}@s.whatsapp.net`, {phone:`${phone}:3@s.whatsapp.net`}), true);
  assert.equal(isWhatsAppOwner(`${lid}@lid`, {lid:`${lid}@lid`}), true);
  assert.equal(isWhatsAppOwner(`${lid}@lid`, {lid:`${lid}@s.whatsapp.net`}), false);
});

test('monitor LID fallback requires the configured owner phone', () => {
  const account = {id:`${phone}:4@s.whatsapp.net`, lid:`${lid}@lid`};
  assert.equal(isWhatsAppOwner(`${lid}@lid`, {phone, monitorAccounts:[account]}), true);
  assert.equal(isWhatsAppOwner(`${lid}@lid`, {monitorAccounts:[account]}), false);
  assert.equal(isWhatsAppOwner(`${lid}@lid`, {phone:'27820000002', monitorAccounts:[account]}), false);
  assert.equal(isWhatsAppOwner(`${lid}@lid`, {phone, monitorAccounts:[{...account, lid:`${lid}@s.whatsapp.net`}]}), false);
  assert.equal(isWhatsAppOwner(`${phone}@s.whatsapp.net`, {phone, monitorAccounts:[null, {}]}), true);
});

test('bot adapter handles unreadable credentials and pairing changes without caching authorization', () => {
  const source = fs.readFileSync(new URL('../jax-whatsapp-agent/bot.mjs', import.meta.url), 'utf8');
  const code = source.slice(source.indexOf('function getMonitorOwnerAccounts()'), source.indexOf('function enqueue('));
  let account = {id:`${phone}:1@s.whatsapp.net`, lid:`${lid}@lid`};
  const isOwner = vm.runInNewContext(code + '\nisOwner', {
    process:{env:{OWNER_PHONE_NUMBER:phone}}, path, __dirname:'/synthetic/bot', isWhatsAppOwner,
    fs:{readFileSync() { if (!account) throw new Error('missing'); return JSON.stringify({me:account}); }}
  });
  assert.equal(isOwner(`${lid}@lid`), true);
  account = {id:'27820000002@s.whatsapp.net', lid:`${lid}@lid`};
  assert.equal(isOwner(`${lid}@lid`), false);
  account = null;
  assert.equal(isOwner(`${lid}@lid`), false);
  assert.equal(isOwner(`${phone}@s.whatsapp.net`), true);
});
