import makeWASocket, { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, makeCacheableSignalKeyStore } from '@whiskeysockets/baileys';
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

if (!fs.existsSync(authDir)) {
  fs.mkdirSync(authDir, { recursive: true });
}

const logger = pino({ level: 'silent' });

async function runPairing() {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    logger,
    printQRInTerminal: false,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger)
    },
    browser: isMonitor ? ['JAX Companion Monitor', 'Chrome', '143.0.0.0'] : ['JAX Sales Agent', 'Chrome', '143.0.0.0'],
    syncFullHistory: false,
    markOnlineOnConnect: true
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
      console.log(`\n✅ SUCCESS: ${label} connected and authenticated successfully!`);
      console.log("Flushing session credentials to disk...\n");
      await saveCreds();
      setTimeout(() => {
        try {
          sock.ev.removeAllListeners();
          sock.ws?.close();
        } catch (e) {}
        process.exit(0);
      }, 2500);
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      if (!shouldReconnect) {
        console.error(`❌ Connection logged out. Please re-run to generate a fresh QR.`);
        process.exit(1);
      }
    }
  });
}

runPairing().catch(err => {
  console.error("Pairing error:", err.message);
  process.exit(1);
});
