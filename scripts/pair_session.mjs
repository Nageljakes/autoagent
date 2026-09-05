import makeWASocket, { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, makeCacheableSignalKeyStore, Browsers } from '@whiskeysockets/baileys';
import qrcodeTerminal from 'qrcode-terminal';
import pino from 'pino';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');

const target = process.argv[2] || '--target=agent';
const isMonitor = target.includes('monitor');

const authDir = isMonitor 
  ? path.resolve(REPO_ROOT, 'jax-whatsapp-monitor', 'auth_info_monitor')
  : path.resolve(REPO_ROOT, 'jax-whatsapp-agent', 'auth_info_baileys');

const label = isMonitor 
  ? "Salesperson WhatsApp Companion Monitor"
  : "AI Agent WhatsApp Bot";

const roleDescription = isMonitor
  ? "Links to the SALESPERSON'S phone to mirror chats and dispatch approved outreach."
  : "Dedicated AI BOT number that interacts with incoming customer leads.";

console.log("\n========================================================");
console.log(`📱 WhatsApp Pairing: ${label}`);
console.log(`ℹ️  Role: ${roleDescription}`);
console.log(`📁 Auth Directory: ${authDir}`);
console.log("========================================================\n");

// If existing creds exist but are not registered, clean them up to ensure fresh pairing
const credsPath = path.join(authDir, 'creds.json');
if (fs.existsSync(credsPath)) {
  try {
    const creds = JSON.parse(fs.readFileSync(credsPath, 'utf-8'));
    const isAuthed = creds.registered === true || Boolean(creds.me && creds.me.id);
    if (!isAuthed) {
      console.log("🧹 Cleaning up incomplete prior pairing attempt...");
      fs.rmSync(authDir, { recursive: true, force: true });
    }
  } catch (e) {
    fs.rmSync(authDir, { recursive: true, force: true });
  }
}

if (!fs.existsSync(authDir)) {
  fs.mkdirSync(authDir, { recursive: true });
}

const logger = pino({ level: 'silent' });
let sock = null;
let isClosingCleanly = false;

async function runPairing() {
  if (sock) {
    try {
      sock.ev.removeAllListeners();
      sock.ws?.close();
    } catch (e) {}
    sock = null;
  }

  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    logger,
    printQRInTerminal: false,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger)
    },
    browser: Browsers.ubuntu('Chrome'),
    syncFullHistory: false,
    markOnlineOnConnect: true,
    connectTimeoutMs: 60000,
    defaultQueryTimeoutMs: 60000
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log(`\n👉 SCAN THIS QR CODE WITH ${label.toUpperCase()}:`);
      console.log("   1. Open WhatsApp on the phone");
      console.log("   2. Tap Settings (or ⋮) > Linked Devices > Link a Device");
      console.log("   3. Point camera at the code below:\n");
      qrcodeTerminal.generate(qr, { small: true });
      console.log("\nWaiting for device scan...\n");
    }

    if (connection === 'open') {
      isClosingCleanly = true;
      const myId = sock.user?.id?.split(':')[0] || sock.user?.id?.split('@')[0] || '';
      console.log(`\n✅ SUCCESS: ${label} (${myId ? '+' + myId : 'registered'}) connected and authenticated successfully!`);
      console.log("Finalizing multi-device registration & syncing session...\n");
      state.creds.registered = true;
      await saveCreds();

      // Wait a few seconds to let WhatsApp finish the device naming & initial sync with the phone
      setTimeout(async () => {
        try {
          state.creds.registered = true;
          await saveCreds();
          sock.ev.removeAllListeners();
          sock.ws?.close();
        } catch (e) {}
        console.log(`✓ Device paired and credentials saved. Ready!\n`);
        process.exit(0);
      }, 4000);
    }

    if (connection === 'close' && !isClosingCleanly) {
      const statusCode = lastDisconnect?.error?.output?.statusCode || lastDisconnect?.error?.statusCode;
      const isLoggedOut = statusCode === DisconnectReason.loggedOut || statusCode === 401;
      const shouldReconnect = !isLoggedOut;

      if (shouldReconnect) {
        console.log(`🔄 Handshake in progress (status: ${statusCode || 'reconnect'}). Finalizing pairing with phone...`);
        // Small delay before reconnecting to let filesystem state flush
        setTimeout(() => {
          runPairing().catch(err => {
            console.error("Reconnection error:", err.message);
            process.exit(1);
          });
        }, 1500);
      } else {
        console.error(`❌ Connection logged out or expired. Please re-run to generate a fresh QR.`);
        try {
          fs.rmSync(authDir, { recursive: true, force: true });
        } catch (e) {}
        process.exit(1);
      }
    }
  });
}

process.on('SIGINT', () => {
  console.log("\n⚠️ Pairing cancelled by user.");
  try {
    const credsPath = path.join(authDir, 'creds.json');
    if (fs.existsSync(credsPath)) {
      const creds = JSON.parse(fs.readFileSync(credsPath, 'utf-8'));
      const isAuthed = creds.registered === true || Boolean(creds.me && creds.me.id);
      if (!isAuthed) {
        fs.rmSync(authDir, { recursive: true, force: true });
      }
    }
  } catch (e) {}
  process.exit(130);
});

runPairing().catch(err => {
  console.error("Pairing error:", err.message);
  process.exit(1);
});
