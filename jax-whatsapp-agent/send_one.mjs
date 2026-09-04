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
            const jid = '27820000000@s.whatsapp.net';
            const msg = "Good day, thank you for reaching out to us. We have received your message and will follow up shortly.";
            console.log("Sending...");
            await sock.sendMessage(jid, { text: msg });
            console.log("Sent successfully.");
            process.exit(0);
        }
    });
}
send();
