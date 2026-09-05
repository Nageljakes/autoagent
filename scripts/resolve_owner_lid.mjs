import fs from 'node:fs';
import {resolveVerifiedOwnerLid} from '../jax-shared/owner-identity.mjs';

const [phone, ...credentialPaths] = process.argv.slice(2);
const accounts = credentialPaths.map(file => {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'))?.me;
  } catch {
    return null;
  }
});
const result = resolveVerifiedOwnerLid(phone, accounts);
if (result.status !== 'verified') {
  console.error(result.status === 'conflict'
    ? 'Owner LID cleared: owner pairings disagree. Re-pair the correct owner phone.'
    : 'Owner LID cleared: no verified owner pairing. Pair the configured owner phone to enable LID access.');
}
console.log(result.lid);
