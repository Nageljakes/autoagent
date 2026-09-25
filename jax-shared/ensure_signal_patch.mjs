import fs from 'fs';
import path from 'path';

/**
 * JAX Baileys Multi-Device Stability & E2EE Patch Engine
 * 
 * 1. Reverts destructive session-nullifying catch blocks in libsignal.js.
 * 2. Patches messages-send.js to properly identify sender's LID alongside phone number
 *    so deviceSentMessage peer sync messages are properly dispatched to the primary phone.
 * 3. Patches auth-utils.js to delete memory-cached keys on removal instead of caching null.
 */
export function ensureSignalPatch(projectDir) {
  try {
    // -------------------------------------------------------------
    // 1. Revert destructive libsignal session purge
    // -------------------------------------------------------------
    const libsignalPath = path.join(projectDir, 'node_modules/@whiskeysockets/baileys/lib/Signal/libsignal.js');
    if (fs.existsSync(libsignalPath)) {
      let libsignalContent = fs.readFileSync(libsignalPath, 'utf8');
      if (libsignalContent.includes('[SIGNAL AUTO-HEALING]')) {
        const patchedBlock = `        async decryptMessage({ jid, type, ciphertext }) {
            const addr = jidToSignalProtocolAddress(jid);
            const session = new libsignal.SessionCipher(storage, addr);
            let result;
            try {
                switch (type) {
                    case 'pkmsg':
                        result = await session.decryptPreKeyWhisperMessage(ciphertext);
                        break;
                    case 'msg':
                        result = await session.decryptWhisperMessage(ciphertext);
                        break;
                    default:
                        throw new Error(\`Unknown message type: \${type}\`);
                }
                return result;
            } catch (err) {
                const errMsg = err?.message || String(err);
                if (errMsg.includes('Bad MAC') || errMsg.includes('No matching sessions') || errMsg.includes('SessionError') || errMsg.includes('closed session') || errMsg.includes('No session record')) {
                    const addrStr = addr.toString();
                    console.warn(\`[SIGNAL AUTO-HEALING] Purging corrupted session for \${jid} (\${addrStr}) due to: \${errMsg}\`);
                    try {
                        await auth.keys.set({ session: { [addrStr]: null } });
                    } catch (purgeErr) {
                        console.error(\`[SIGNAL AUTO-HEALING] Failed to purge session for \${addrStr}:\`, purgeErr);
                    }
                }
                throw err;
            }
        },`;

        const cleanBlock = `        async decryptMessage({ jid, type, ciphertext }) {
            const addr = jidToSignalProtocolAddress(jid);
            const session = new libsignal.SessionCipher(storage, addr);
            let result;
            switch (type) {
                case 'pkmsg':
                    result = await session.decryptPreKeyWhisperMessage(ciphertext);
                    break;
                case 'msg':
                    result = await session.decryptWhisperMessage(ciphertext);
                    break;
                default:
                    throw new Error(\`Unknown message type: \${type}\`);
            }
            return result;
        },`;

        if (libsignalContent.includes(patchedBlock)) {
          libsignalContent = libsignalContent.replace(patchedBlock, cleanBlock);
          fs.writeFileSync(libsignalPath, libsignalContent, 'utf8');
          console.log(`[BOOT] Reverted destructive session-purging patch in ${libsignalPath}`);
        }
      }
    }

    // -------------------------------------------------------------
    // 2. Patch messages-send.js for LID deviceSentMessage routing
    // -------------------------------------------------------------
    const sendPath = path.join(projectDir, 'node_modules/@whiskeysockets/baileys/lib/Socket/messages-send.js');
    if (fs.existsSync(sendPath)) {
      let sendContent = fs.readFileSync(sendPath, 'utf8');
      if (!sendContent.includes('// [JAX MULTI-DEVICE LID FIX]')) {
        const sendNeedle = `            else {
                const { user: meUser } = jidDecode(meId);
                if (!participant) {
                    devices.push({ user });
                    if (user !== meUser) {
                        devices.push({ user: meUser });
                    }
                    if (additionalAttributes?.['category'] !== 'peer') {
                        const additionalDevices = await getUSyncDevices([meId, jid], !!useUserDevicesCache, true);
                        devices.push(...additionalDevices);
                    }
                }
                const allJids = [];
                const meJids = [];
                const otherJids = [];
                for (const { user, device } of devices) {
                    const isMe = user === meUser;
                    const jid = jidEncode(isMe && isLid ? authState.creds?.me?.lid.split(':')[0] || user : user, isLid ? 'lid' : 's.whatsapp.net', device);
                    if (isMe) {
                        meJids.push(jid);
                    }
                    else {
                        otherJids.push(jid);
                    }
                    allJids.push(jid);
                }`;

        const sendReplacement = `            else {
                // [JAX MULTI-DEVICE LID FIX]
                const { user: meUser } = jidDecode(meId);
                const meLid = authState.creds?.me?.lid;
                const meLidUser = meLid ? jidDecode(meLid)?.user : undefined;
                if (!participant) {
                    devices.push({ user });
                    if (user !== meUser && (!meLidUser || user !== meLidUser)) {
                        devices.push({ user: meUser });
                    }
                    if (additionalAttributes?.['category'] !== 'peer') {
                        const additionalDevices = await getUSyncDevices([meId, jid], !!useUserDevicesCache, true);
                        devices.push(...additionalDevices);
                    }
                }
                const allJids = [];
                const meJids = [];
                const otherJids = [];
                for (const { user, device } of devices) {
                    const isMe = user === meUser || (meLidUser && user === meLidUser);
                    const targetUser = isMe
                        ? (isLid ? (authState.creds?.me?.lid?.split(':')[0] || meLidUser || user) : meUser)
                        : user;
                    const jid = jidEncode(targetUser, isLid ? 'lid' : 's.whatsapp.net', device);
                    if (isMe) {
                        meJids.push(jid);
                    }
                    else {
                        otherJids.push(jid);
                    }
                    allJids.push(jid);
                }`;

        if (sendContent.includes(sendNeedle)) {
          sendContent = sendContent.replace(sendNeedle, sendReplacement);
          fs.writeFileSync(sendPath, sendContent, 'utf8');
          console.log(`[BOOT] Applied LID deviceSentMessage routing fix in ${sendPath}`);
        }
      }
    }

    // -------------------------------------------------------------
    // 3. Patch auth-utils.js for in-memory cache eviction on delete
    // -------------------------------------------------------------
    const authUtilsPath = path.join(projectDir, 'node_modules/@whiskeysockets/baileys/lib/Utils/auth-utils.js');
    if (fs.existsSync(authUtilsPath)) {
      let authContent = fs.readFileSync(authUtilsPath, 'utf8');
      if (!authContent.includes('// [JAX CACHE DEL FIX]')) {
        const cacheNeedle = `            for (const type in data) {
                for (const id in data[type]) {
                    cache.set(getUniqueId(type, id), data[type][id]);
                    keys += 1;
                }
            }`;

        const cacheReplacement = `            for (const type in data) {
                // [JAX CACHE DEL FIX]
                for (const id in data[type]) {
                    const val = data[type][id];
                    const uid = getUniqueId(type, id);
                    if (val) {
                        cache.set(uid, val);
                    } else {
                        cache.del(uid);
                    }
                    keys += 1;
                }
            }`;

        if (authContent.includes(cacheNeedle)) {
          authContent = authContent.replace(cacheNeedle, cacheReplacement);
          fs.writeFileSync(authUtilsPath, authContent, 'utf8');
          console.log(`[BOOT] Applied memory cache eviction fix in ${authUtilsPath}`);
        }
      }
    }
  } catch (err) {
    console.error(`[BOOT] Failed to run Baileys patch routine for ${projectDir}:`, err);
  }
}
