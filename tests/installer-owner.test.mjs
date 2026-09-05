import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {resolveVerifiedOwnerLid} from '../jax-shared/owner-identity.mjs';

const phone = '27820000001';
const lid = '100000000000001';
const account = {id: `${phone}:4@s.whatsapp.net`, lid: `${lid}:7@lid`};

test('installer accepts only valid LIDs paired to the exact configured phone', () => {
  for (const configured of [phone, '+27 (82) 000-0001', `${phone}:3@s.whatsapp.net`]) {
    assert.deepEqual(resolveVerifiedOwnerLid(configured, [account, account]), {lid, status: 'verified'});
  }
  for (const invalid of [null, {}, {...account, id: '27820000002@s.whatsapp.net'},
    {...account, id: `${phone}@lid`}, {...account, lid: `${lid}@s.whatsapp.net`},
    {...account, lid: `${lid}@lid\n`}, {...account, id: `1${phone}@s.whatsapp.net`}]) {
    assert.equal(resolveVerifiedOwnerLid(phone, [invalid]).lid, '');
  }
  assert.equal(resolveVerifiedOwnerLid('', [account]).lid, '');
  assert.equal(resolveVerifiedOwnerLid(`invalid${phone}`, [account]).lid, '');
});

test('installer clears conflicting verified LIDs and ignores unrelated pairings', () => {
  const other = {...account, lid: '100000000000002@lid'};
  assert.deepEqual(resolveVerifiedOwnerLid(phone, [account, other]), {lid: '', status: 'conflict'});
  assert.deepEqual(resolveVerifiedOwnerLid(phone, [other, account]), {lid: '', status: 'conflict'});
  assert.deepEqual(resolveVerifiedOwnerLid(phone, [account, {...other, id: '27820000002@s.whatsapp.net'}]), {lid, status: 'verified'});
});

test('installer command clears stale environment LIDs and handles malformed credential files', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "owner's $(ignored) "));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const credential = path.join(dir, 'creds.json');
  const missing = path.join(dir, 'missing.json');
  const command = fileURLToPath(new URL('../scripts/resolve_owner_lid.mjs', import.meta.url));
  const run = () => spawnSync(process.execPath, [command, phone, credential, missing], {
    encoding: 'utf8', env: {...process.env, OWNER_LID: '999999999999999'}
  });
  for (const contents of ['{broken', 'null', '{}', JSON.stringify({me: {...account, id: '27820000002@s.whatsapp.net'}})]) {
    fs.writeFileSync(credential, contents);
    const result = run();
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), '');
    assert.match(result.stderr, /Owner LID cleared/);
  }
  fs.writeFileSync(credential, JSON.stringify({me: account}));
  const result = run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), lid);
});

test('deployment recomputes owner LID before initial and final configuration writes', () => {
  const source = fs.readFileSync(new URL('../deploy.sh', import.meta.url), 'utf8');
  const resolve = 'OWNER_LID="$(resolve_owner_lid)"';
  assert.ok(source.indexOf(resolve) < source.indexOf('cat << ENV_LOCAL'));
  assert.ok(source.lastIndexOf(resolve) > source.indexOf('# Part B:'));
  assert.ok(source.lastIndexOf(resolve) < source.indexOf('cat << ENV_EOF'));
  assert.doesNotMatch(source, /OWNER_LID="\$(?:EXISTING_OWNER_LID|DETECTED_LID|AGENT_LID)"/);
});
