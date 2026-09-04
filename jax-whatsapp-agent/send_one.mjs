import makeWASocket, { useMultiFileAuthState } from '@whiskeysockets/baileys';
import pino from 'pino';

async function send() {
    const { state, saveCreds } = await useMultiFileAuthState('./auth_info_baileys');
    const sock = makeWASocket({
        auth: state,
        logger: pino({ level: 'silent' }),
        syncFullHistory: false
    });
    
    sock.ev.on('creds.update', saveCreds);
    
    sock.ev.on('connection.update', async (update) => {
        const { connection } = update;
        if (connection === 'open') {
            const jid = process.env.TARGET_JID || process.argv[2] || '27820000000@s.whatsapp.net';
            const msg = process.env.OUTREACH_MESSAGE || process.argv[3] || "Good day, thank you for reaching out to us. We have received your message and will follow up shortly.";
            console.log(`Sending to ${jid}...`);
            await sock.sendMessage(jid, { text: msg });
            console.log("Sent successfully.");
            process.exit(0);
        }
    });
}
send();
