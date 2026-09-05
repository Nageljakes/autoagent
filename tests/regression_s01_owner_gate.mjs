import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

function read(p) {
  return fs.readFileSync(path.join(root, p), 'utf8').replace(/\r\n/g, '\n');
}

test('S01 - WhatsApp Phase 1 Owner Gate', async (t) => {
  const wa = read('jax-whatsapp-agent/bot.mjs');
  
  // Test 1: Verify /claimowner and /auth are completely removed from the file
  assert.ok(
    !wa.includes("trimmedText.startsWith('/claimowner')") &&
    !wa.includes("trimmedText.startsWith('/auth')"),
    'The /claimowner and /auth commands must be completely removed from jax-whatsapp-agent/bot.mjs'
  );

  // Test 2: Ensure the customer chat guard still correctly blocks non-owners
  const chatGuardBlock = wa.match(/if\s*\(RESTRICT_TO_OWNER\s*&&\s*!owner\)\s*\{[\s\S]*?log\.warn[\s\S]*?continue;\s*\}/);
  assert.ok(chatGuardBlock, 'The customer chat guard must immediately block and continue for non-owners');
});

test('S01 - Telegram Phase 1 Owner Gate', async (t) => {
  const tg = read('jax-telegram-agent/bot.mjs');

  // Test 3: Verify the owner gate exists in the middleware
  assert.ok(
    tg.includes('if (RESTRICT_TO_OWNER && !isOwner(userId)) {') &&
    tg.includes('log.warn(`[BLOCKED] Message from non-owner Telegram user'),
    'Telegram must have an owner-only gate in the middleware'
  );
});
