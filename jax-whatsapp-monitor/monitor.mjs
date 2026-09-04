import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import qrcodeTerminal from 'qrcode-terminal';
import pino from 'pino';
import express from 'express';
import dotenv from 'dotenv';
import path from 'path';
import fs from 'fs';

import {
  saveMessage,
  getProspectHistory,
  listRecentProspects,
  searchMessages,
  logAuditSend,
  upsertProspect,
  tagContact
} from './db.mjs';

import {
  isLeadNotification,
  autoAcceptLeads
} from './lead_auto_accept.mjs';

dotenv.config();

const logger = pino({ level: process.env.LOG_LEVEL || 'info' });
const AUTH_DIR = process.env.AUTH_DIR || path.resolve('./auth_info_monitor');
const PORT = parseInt(process.env.API_PORT || '9095', 10);
const PAIRING_NUMBER = (process.env.PAIRING_PHONE_NUMBER || '').replace(/[^0-9]/g, '');

let sock = null;
let connectionStatus = 'DISCONNECTED';

function unwrapMessage(rawMsg) {
  if (!rawMsg) return null;
  let m = rawMsg.message || rawMsg;
  let depth = 0;
  while (m && depth < 10) {
    if (m.ephemeralMessage?.message) {
      m = m.ephemeralMessage.message;
    } else if (m.viewOnceMessage?.message) {
      m = m.viewOnceMessage.message;
    } else if (m.viewOnceMessageV2?.message) {
      m = m.viewOnceMessageV2.message;
    } else if (m.viewOnceMessageV2Extension?.message) {
      m = m.viewOnceMessageV2Extension.message;
    } else if (m.documentWithCaptionMessage?.message) {
      m = m.documentWithCaptionMessage.message;
    } else if (m.editedMessage?.message?.protocolMessage?.editedMessage) {
      m = m.editedMessage.message.protocolMessage.editedMessage;
    } else {
      break;
    }
    depth++;
  }
  return m;
}

function isProtocolMessage(m) {
  if (!m) return true;
  return Boolean(
    m.protocolMessage || 
    m.senderKeyDistributionMessage || 
    m.keyExchangeMessage ||
    m.appStateSyncKeyShare ||
    m.historySyncNotification
  );
}

function extractMessageContent(rawMsg) {
  const m = unwrapMessage(rawMsg);
  if (!m || isProtocolMessage(m)) return null;

  if (m.conversation) {
    return { text: m.conversation, type: 'text' };
  }
  if (m.extendedTextMessage?.text) {
    return { text: m.extendedTextMessage.text, type: 'text' };
  }
  if (m.imageMessage) {
    return { text: m.imageMessage.caption || '[Image]', type: 'image' };
  }
  if (m.videoMessage) {
    return { text: m.videoMessage.caption || '[Video]', type: 'video' };
  }
  if (m.audioMessage) {
    return { text: '[Audio/Voice Note]', type: 'audio' };
  }
  if (m.documentMessage) {
    return { text: m.documentMessage.fileName || m.documentMessage.caption || '[Document]', type: 'document' };
  }
  if (m.locationMessage) {
    return { text: `[Location: ${m.locationMessage.degreesLatitude}, ${m.locationMessage.degreesLongitude}]`, type: 'location' };
  }
  if (m.liveLocationMessage) {
    return { text: `[Live Location: ${m.liveLocationMessage.degreesLatitude}, ${m.liveLocationMessage.degreesLongitude}]`, type: 'location' };
  }
  if (m.contactMessage || m.contactsArrayMessage) {
    return { text: '[Contact Card]', type: 'contact' };
  }
  if (m.stickerMessage) {
    return { text: '[Sticker]', type: 'sticker' };
  }
  if (m.reactionMessage) {
    return { text: `[Reaction: ${m.reactionMessage.text}]`, type: 'reaction' };
  }
  if (m.buttonsResponseMessage?.selectedDisplayText) {
    return { text: m.buttonsResponseMessage.selectedDisplayText, type: 'text' };
  }
  if (m.templateButtonReplyMessage?.selectedDisplayText) {
    return { text: m.templateButtonReplyMessage.selectedDisplayText, type: 'text' };
  }
  if (m.listResponseMessage?.title) {
    return { text: m.listResponseMessage.title, type: 'text' };
  }

  const keys = Object.keys(m);
  if (keys.length === 0 || keys.every(k => ['messageContextInfo', 'stubType'].includes(k))) {
    return null;
  }

  return { text: '[Media/Message]', type: 'media' };
}

function processIncomingMessage(msg) {
  if (!msg || !msg.key) return;

  const remoteJid = msg.key.remoteJid;
  if (!remoteJid || remoteJid === 'status@broadcast' || remoteJid.endsWith('@newsletter')) {
    return;
  }

  const isGroup = remoteJid.endsWith('@g.us');
  if (isGroup && process.env.IGNORE_GROUPS === 'true') {
    return;
  }

  const extracted = extractMessageContent(msg);
  if (!extracted || !extracted.text) {
    return; // Ignore internal protocol/sync handshakes
  }

  const participant = msg.key.participant || msg.participant || '';
  const participantPhone = participant.split('@')[0].split(':')[0].replace(/[^0-9]/g, '');
  const cleanRemote = remoteJid.replace('@s.whatsapp.net', '').replace('@g.us', '').replace('@lid', '');
  const phoneNumber = isGroup && participantPhone ? participantPhone : cleanRemote;
  const fromMe = Boolean(msg.key.fromMe);

  saveMessage({
    id: msg.key.id,
    jid: remoteJid,
    phoneNumber,
    fromMe,
    senderName: msg.pushName || (fromMe ? 'You / Agent' : null),
    messageType: extracted.type,
    content: extracted.text,
    timestamp: Number(msg.messageTimestamp || Math.floor(Date.now() / 1000))
  });

  logger.debug(`Saved message [${msg.key.id}] from ${phoneNumber} (fromMe: ${fromMe}, isGroup: ${isGroup})`);

  // Check if message is a CRM lead notification (from {LEAD_NOTIFIER_NAME} or on dealership group)
  if (!fromMe && isLeadNotification({
    senderPhone: participantPhone || cleanRemote,
    pushName: msg.pushName,
    text: extracted.text,
    isGroup,
    remoteJid
  })) {
    logger.info(`🎯 Lead alert detected: "${extracted.text.slice(0, 100)}". Triggering Dealer CRM lead acceptance...`);
    autoAcceptLeads({
      msgId: msg.key.id,
      senderPhone: participantPhone || cleanRemote,
      pushName: msg.pushName,
      text: extracted.text,
      isGroup,
      remoteJid
    }, sock);
  }
}

async function startWhatsAppMonitor() {
  if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
  }

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version, isLatest } = await fetchLatestBaileysVersion();
  logger.info(`Starting WhatsApp Monitor using WA v${version.join('.')}, isLatest: ${isLatest}`);

  sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger)
    },
    logger: pino({ level: 'silent' }),
    printQRInTerminal: !PAIRING_NUMBER,
    markOnlineOnConnect: false,
    syncFullHistory: true
  });

  // Pairing code alternative to QR if phone number is supplied
  if (PAIRING_NUMBER && !sock.authState.creds.registered) {
    setTimeout(async () => {
      try {
        const code = await sock.requestPairingCode(PAIRING_NUMBER);
        logger.info(`🔑 WhatsApp Pairing Code: ${code}`);
        console.log(`\n========================================\n🔑 PAIRING CODE FOR MONITOR: ${code}\n========================================\n`);
      } catch (err) {
        logger.error({ err }, 'Failed to generate pairing code');
      }
    }, 4000);
  }

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr && !PAIRING_NUMBER) {
      console.log('\nScan this QR code to connect the Monitoring WhatsApp:\n');
      qrcodeTerminal.generate(qr, { small: true });
    }

    if (connection === 'close') {
      connectionStatus = 'DISCONNECTED';
      const statusCode = (lastDisconnect?.error instanceof Boom) 
        ? lastDisconnect.error.output?.statusCode 
        : null;
      
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      logger.warn(`Connection closed. Status code: ${statusCode}. Reconnecting: ${shouldReconnect}`);

      if (shouldReconnect) {
        setTimeout(startWhatsAppMonitor, 5000);
      } else {
        logger.error('WhatsApp session logged out. Delete auth_info_monitor directory and restart to rescan QR.');
      }
    } else if (connection === 'open') {
      connectionStatus = 'CONNECTED';
      logger.info('✅ WhatsApp Monitor Bridge connected successfully! Passively indexing messages.');
    }
  });

  // SYNC HISTORICAL CONVERSATIONS FROM DEVICE
  sock.ev.on('messaging-history.set', ({ chats, contacts, messages, isLatest }) => {
    logger.info(`📚 Received historical sync: ${messages?.length || 0} messages, ${chats?.length || 0} chats, ${contacts?.length || 0} contacts.`);
    
    if (contacts && Array.isArray(contacts)) {
      for (const contact of contacts) {
        if (contact.id && (contact.name || contact.notify)) {
          upsertProspect(contact.id, null, contact.name || contact.notify);
        }
      }
    }

    if (messages && Array.isArray(messages)) {
      for (const msg of messages) {
        try {
          processIncomingMessage(msg);
        } catch (e) {}
      }
    }
  });

  // CONTACTS UPDATE LISTENER
  sock.ev.on('contacts.upsert', (contacts) => {
    for (const c of contacts) {
      if (c.id && (c.name || c.notify)) {
        upsertProspect(c.id, null, c.name || c.notify);
      }
    }
  });

  // LIVE INGESTION LISTENER - NEVER REPLIES AUTOMATICALLY
  sock.ev.on('messages.upsert', async ({ messages: msgs, type }) => {
    try {
      for (const msg of msgs) {
        processIncomingMessage(msg);
      }
    } catch (err) {
      logger.error({ err }, 'Error processing messages.upsert in monitor bridge');
    }
  });
}

// ==========================================
// REST API FOR AGENTS & HISTORY RETRIEVAL
// ==========================================
const app = express();
app.use(express.json());

// Health Check
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    connection: connectionStatus,
    pairingConfigured: Boolean(PAIRING_NUMBER),
    uptime: process.uptime()
  });
});

// Get Conversation History (Prospect or Coworker)
app.get('/history/:phone', (req, res) => {
  try {
    const { phone } = req.params;
    const limit = parseInt(req.query.limit || '50', 10);
    const offset = parseInt(req.query.offset || '0', 10);

    const history = getProspectHistory(phone, limit, offset);
    res.json({ success: true, ...history });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Full Prospect Context & Language Preference Analysis
app.get('/context/:query', (req, res) => {
  try {
    const { query } = req.params;
    const limit = parseInt(req.query.limit || '50', 10);
    const history = getProspectHistory(query, limit, 0);

    const AFRICAN_FIRST_NAMES = new Set([
      'duduzile', 'dudu', 'sipho', 'thabo', 'nomvula', 'bongani', 'nthabiseng', 'kagiso',
      'lerato', 'tebogo', 'khanyisile', 'zanele', 'bongiwe', 'sifiso', 'mpho', 'sinethemba',
      'ntshuxeko', 'thato', 'lydia', 'nkosana', 'joseph', 'judas', 'zwelithini', 'mfundisi',
      'elizabeth', 'lorraine', 'paulinah', 'given', 'nkosikhona', 'itumeleng', 'mawande',
      'mawina', 'tracey', 'annikie', 'mecha', 'samuel', 'gershom', 'nkamoheleng', 'jonas',
      'skhombuzo', 'mamokone', 'matlhatsi', 'khathu', 'balbina', 'jane', 'neo', 'sophy',
      'lindiwe', 'busisiwe', 'themba', 'mandla', 'vusi', 'dumi', 'simphiwe', 'mbali',
      'ayanda', 'bandile', 'senzo', 'lungelo', 'sibusiso', 'phumzile', 'nonhlanhla', 'refilwe',
      'lebogang', 'dineo', 'tshepo', 'karabo', 'katlego', 'kabelo', 'kgotso', 'lesego',
      'dimakatso', 'puleng', 'palesa', 'keabetswe', 'koketso', 'boitumelo', 'sibongile',
      'nomsa', 'thandiwe', 'thandi', 'zodwa', 'zola', 'nokuthula', 'gugu', 'precious',
      'thobile', 'thandeka', 'nokwanda', 'sindisiwe', 'slindile', 'hlengiwe', 'nompumelelo',
      'mpume', 'babalwa', 'funeka', 'nonkanyiso', 'zintle', 'asanda', 'bulelwa', 'unathi',
      'noluthando', 'siphokazi', 'nomfundo', 'nontando', 'nontobeko', 'khethiwe', 'makhosi',
      'minenhle', 'aphiwe', 'andiswa', 'mihlali', 'siyamthanda', 'onkarabile', 'reatlegile',
      'malebo', 'boipelo', 'tshegofatso', 'naledi', 'bokamoso', 'amogelang', 'oratile',
      'tshepang', 'bontle', 'masego', 'rorisang', 'keneilwe', 'kgomotso', 'matshepo',
      'mphoentle', 'mamello', 'molebogeng', 'tebatso', 'mahlatse', 'makgabo', 'khomotso',
      'morongwa', 'mokgadi', 'tsakani', 'kulani', 'ntsako', 'vonani', 'tintswalo', 'nhlalala',
      'hlamalani', 'langavi', 'rhulani', 'ntshembo', 'khensani', 'nsovo', 'ntsumi', 'vutomi',
      'vutlhari', 'ntsakisi', 'tinyiko', 'rirhandzu', 'dzunisani', 'nwanati', 'ndivhuwo',
      'mulalo', 'takalani', 'rendani', 'vhahangwele', 'tshilidzi', 'zwivhuya', 'rabelani',
      'fulufhelo', 'fhulufhelo', 'dakalo', 'rolivhuwa', 'elelwani', 'livhuwani', 'rotondwa',
      'vhutshilo', 'muofhe', 'mashudu', 'lufuno', 'khathutshelo', 'mmbengeni', 'nkosinathi',
      'thabiso', 'mzwandile', 'siyabonga', 'jabulani', 'bheki', 'bhekisisa', 'sandile',
      'nhlanhla', 'menzi', 'sphiwe', 'mthokozisi', 'philani', 'xolani', 'mlungisi', 'lwazi',
      'andile', 'anele', 'luyanda', 'wandile', 'ayakhanya', 'sive', 'lwandle', 'melokuhle',
      'kwanele', 'musawenkosi', 'nhlakanipho', 'sizwe', 'dumisani', 'mthunzi', 'vuyo',
      'luvuyo', 'zolani', 'loyiso', 'sonwabo', 'akhona', 'yamkela', 'avela', 'siyanda',
      'olwethu', 'thulani', 'khaya', 'mkhululi', 'mongezi', 'lonwabo', 'babalo', 'luvo',
      'siseko', 'lulamile', 'thabang', 'tshepiso', 'tumelo', 'tumi', 'lesiba', 'lehlogonolo',
      'khumo', 'kutlwano', 'moeketsi', 'molefi', 'modise', 'mohau', 'moseki', 'motlatsi',
      'pule', 'rapelang', 'sello', 'tau', 'teboho', 'thapelo', 'tokelo', 'tshediso', 'tsepo',
      'tsietsi', 'dithebe', 'boikanyo', 'gontse', 'kgosi', 'kago', 'kegomoditswe', 'khumoetsile',
      'odirile', 'omphile', 'onalethata', 'onkgopotse', 'orefile', 'osegofetse', 'oteng',
      'phenyo', 'refentse', 'resego', 'tlotlo', 'mogomotsi', 'tiro', 'thuto', 'letlhogonolo',
      'boitshepo', 'malatji', 'matome', 'kgabo', 'lesetja', 'phetole', 'ngoako', 'mamabolo',
      'mashishi', 'mokgana', 'phaswane', 'phuti', 'ramokone', 'sebola', 'sekgobela',
      'senoamadi', 'seshoka', 'tlou', 'makgatho', 'mahlatsi', 'rasefate', 'ramphelane',
      'maphuti', 'tiisetso', 'morapedi', 'blessing', 'gift', 'prince', 'innocent', 'lucky',
      'promise', 'patience', 'faith', 'hope', 'grace', 'peace', 'mercy', 'joy', 'justice',
      'wisdom', 'bright', 'clever', 'goodness', 'wonder', 'marvel', 'shepherd', 'doctor', 'witness'
    ]);

    const AFRICAN_SURNAMES = new Set([
      'ngcobo', 'dlamini', 'zulu', 'ndlovu', 'khumalo', 'sithole', 'mthembu', 'molefe',
      'modise', 'baloyi', 'chauke', 'mabaso', 'mokoena', 'radebe', 'nkosi', 'manzini',
      'lieta', 'gumede', 'cele', 'buthelezi', 'zungu', 'ntuli', 'hlongwane', 'khoza',
      'sibiya', 'zwane', 'mkhize', 'mbatha', 'nxumalo', 'shabalala', 'masondo', 'hadebe',
      'bhengu', 'majola', 'mhlongo', 'zondi', 'gwala', 'maphumulo', 'mathebula', 'maluleke',
      'mabunda', 'rikhotso', 'nhlapo', 'mahlangu', 'sibanyoni', 'masombuka', 'skosana',
      'mtsweni', 'mnguni', 'mabena', 'morake', 'tsotetsi', 'motaung', 'moloi', 'mofokeng',
      'mosia', 'motloung', 'sebele', 'tau', 'phiri', 'ledwaba', 'mamabolo', 'mashishi',
      'mokwena', 'malatji', 'mogale', 'matlala', 'letsoalo', 'magaga', 'tjie', 'mocheke',
      'thsehlo', 'ratona', 'busi', 'moredi', 'nikelo', 'mawina', 'dikutle', 'makhwela',
      'ramorula', 'sepeng', 'polo', 'shili', 'zondo', 'mamokone', 'musutua', 'tswai',
      'khaaha', 'manaka', 'mndaba', 'moyo', 'sibanda', 'ncube', 'nkomo', 'tshuma', 'mpofu',
      'nyoni', 'dube', 'gumbo', 'ndaba', 'khanye', 'mtshali', 'vilakazi', 'ziqubu',
      'khuzwayo', 'mpanza', 'msimang', 'mthethwa', 'xulu', 'ngubane', 'langa', 'jiyane',
      'mvelase', 'fakude', 'mavuso', 'shabangu', 'lukhele', 'tsabedze', 'dladla', 'nene',
      'mchunu', 'sosibo', 'mkhwanazi', 'kunene', 'mncube', 'mnisi', 'mavimbela', 'nkuna',
      'ngoveni', 'shivambu', 'khosa', 'hlungwani', 'bila', 'mongwe', 'makukule', 'risenga',
      'maringa', 'maswanganyi', 'mabasa', 'ntimane', 'munyai', 'ramabulana', 'netshifhefhe',
      'nemudzivhadi', 'tshivhase', 'ravele', 'ramovha', 'nelwamondo', 'sinthumule', 'kutama',
      'madzivhandila', 'ligege', 'mphaphuli', 'tshikovhi', 'tshirando', 'mudau', 'singo',
      'mulaudzi', 'khorommbi', 'rambuda', 'maake', 'mabuela', 'madisha', 'makgoba', 'malahlela',
      'maleka', 'mametja', 'mangena', 'maphanga', 'maraba', 'masenya', 'mashego', 'mathabatha',
      'matlou', 'matsepe', 'mojapelo', 'moloto', 'morudu', 'moselakgomo', 'mothiba', 'mothapo',
      'mphahlele', 'nchabeleng', 'ngoepe', 'phasha', 'phatudi', 'ramahlale', 'rammutla', 'ratau',
      'seakamela', 'sebati', 'segooa', 'sekhukhune', 'selepe', 'selolo', 'semenya', 'senyolo',
      'seroka', 'sethunya', 'thobejane', 'tleane', 'dikutle', 'tswai', 'ramorula', 'shili'
    ]);

    const AFRICAN_STEM_PREFIXES = [
      'nko', 'nts', 'nth', 'nom', 'non', 'nto', 'mph', 'mkh', 'mth', 'mzw', 'siy',
      'sim', 'sip', 'sif', 'skh', 'sbu', 'sibu', 'zwe', 'kga', 'kgo', 'tsh', 'leh',
      'kha', 'dudu', 'bong', 'bhek', 'lindi', 'busis', 'thab', 'teb', 'katl', 'dima',
      'pule', 'pale', 'moko', 'mofo', 'maba', 'mabu', 'mabe', 'mala', 'malu', 'math',
      'matl', 'maso', 'mash', 'balo', 'chau', 'rikh', 'muda', 'nets', 'nemu', 'itum',
      'boit', 'kabe', 'kgot', 'refi', 'refe', 'lebo', 'dine', 'kara', 'orat', 'amog',
      'keab', 'koke', 'tume', 'dumi', 'mand', 'vusi', 'phum', 'xola', 'hlong', 'shab',
      'mkhiz', 'ndlov', 'khuma', 'sitho', 'dlami', 'ngcob'
    ];

    const AFRIKAANS_NAMES = new Set([
      'armand', 'corne', 'corné', 'jaco', 'willem', 'dirk', 'kobus', 'pieter', 'johan', 
      'johannes', 'willem', 'frikkie', 'frik', 'riaan', 'christo', 'schalk', 'carel',
      'bennie', 'francois', 'gert', 'henk', 'koos', 'louw', 'ockert', 'roelof', 'tiaan',
      'wouter', 'andre', 'andré', 'werner', 'joggie', 'stephan', 'marthinus', 'tinus',
      'eben', 'danie', 'daniel', 'herman', 'morne', 'morné', 'gerhard', 'peet', 'ryno',
      'renier', 'dewald', 'deon', 'braam', 'dries', 'andries', 'hendrik', 'ernst', 'eugene',
      'eugéne', 'leon', 'nico', 'anton', 'chris', 'paul', 'alwyn', 'wynand', 'charl',
      'coenraad', 'gustav', 'hennie', 'izak', 'jurie', 'luan', 'mornay', 'sarel', 'theuns',
      'waldo', 'zander', 'annelize', 'annelie', 'elize', 'marilize', 'liezel', 'liezl',
      'sanet', 'ronel', 'rina', 'martie', 'susan', 'wilma', 'hannetjie', 'magda', 'marietjie',
      'daleen', 'alta', 'elmarie', 'yolande', 'charmaine', 'petro', 'estelle', 'lizette',
      'corrie', 'bettie', 'heleen', 'ilse', 'sunette', 'carina', 'lizelle',
      'andorette', 'natassja', 'mulder', 'botha', 'matthee', 'van der merwe', 'du plessis',
      'venter', 'coetzee', 'fourie', 'pretorius', 'van wyk', 'steyn', 'de jager', 'nel',
      'smit', 'kruger', 'oosthuizen', 'marais', 'erasmus', 'labuschagne', 'oberholzer',
      'potgieter', 'cloete', 'joubert', 'viljoen', 'bezuidenhout', 'le roux', 'meyer',
      'boshoff', 'cronje', 'rossouw', 'swanepoel', 'snyman', 'bester', 'prinsloo',
      'jansen van rensburg', 'engelbrecht', 'van zyl', 'du toit', 'van niekerk', 'grobler',
      'van staden', 'badenhorst', 'myburgh', 'olivier', 'wentzel', 'van heerden',
      'van deventer', 'van rensburg', 'van vuuren', 'van rooyen', 'van jaarsveld', 'van dyk',
      'van biljon', 'van aardt', 'du preez', 'de wet', 'de beer', 'de klerk', 'de villiers',
      'de bruyn', 'de lange', 'de vos', 'de kock', 'naude', 'naudé', 'pienaar', 'theron',
      'strydom', 'swart', 'hattingh', 'basson', 'botes', 'vorster', 'visagie', 'crafford',
      'jooste', 'janse van vuuren', 'van der walt', 'van der westhuizen', 'van der linde',
      'kriel', 'scholtz', 'buys', 'scheepers', 'terblanche', 'brits', 'greyling', 'gous',
      'briel', 'uys', 'roets', 'nortje', 'nortjé', 'senekal', 'gouws', 'blignaut', 'loots',
      'lategan', 'minnaar', 'merwe', 'plessis', 'toit', 'zyl', 'wyk', 'klerk', 'villiers',
      'beer', 'wet', 'bruyn', 'lange', 'vos', 'kock', 'ruyter', 'heever', 'heerden',
      'deventer', 'rensburg', 'vuuren', 'rooyen', 'jaarsveld', 'dyk', 'biljon', 'aardt',
      'walt', 'westhuizen', 'linde'
    ]);

    const AFRIKAANS_EXCLUSIVE_WORDS = new Set([
      'baie', 'asseblief', 'dankie', 'goeiedag', 'goeiemore', 'goeiemôre', 'goeienaand',
      'gesels', 'geselsie', 'skakel', 'boodskap', 'vinnige', 'wanneer', 'hoeveel',
      'vandag', 'môre', 'na-ure', 'rustiger', 'onderwys', 'onderwyser', 'onderwyseres',
      'inruil', 'inruilwaarde', 'kwotasie', 'toetsrit', 'voertuig', 'hoor', 'sommer',
      'graag', 'besig', 'hulle', 'julle', 'niemand', 'niks', 'altyd', 'moontlik',
      'seblief', 'luitjie', 'groete', 'lekker', 'saam', 'praat', 'luister', 'kyk',
      'koop', 'verkoop', 'nuwe', 'gebruikte', 'kontak', 'beskikbaar', 'finansiering',
      'deposito', 'aflewer', 'goeie', 'hier', 'ek', 'jy', 'jou', 'sal', 'kan', 'moet',
      'wil', 'nie', 'wat', 'hoe', 'wees', 'lyk', 'program', 'weet', 'voel', 'gou'
    ]);

    const AFRIKAANS_PHRASES = [
      'ek wil', 'ek is', 'ek het', 'ek sal', 'ek kan', 'ek dink', 'ek hoop', 'ek volg',
      'kan jy', 'kan u', 'sal jy', 'sal u', 'wil jy', 'wil u', 'moet ek', 'moet jy',
      'laat weet', 'hoe gaan', 'baie dankie', 'goeie dag', 'goeie middag', 'goeie more',
      'goeie môre', 'goeie naand', 'as dit', 'as jy', 'wanneer sal', 'vinnige geselsie',
      'vinnige luitjie', 'ek volg op', 'ek wil hoor', 'stuur vir', 'kontak my', 'bel my',
      'skakel my', 'praat met', 'gee my', 'oor whatsapp', 'hoe lyk', 'wat is', 'wat kos',
      'hoeveel kos', 'hoe lyk jou', '{SALESPERSON_NAME_LOWER} hier van', '{SALESPERSON_NAME_LOWER} hier weer'
    ];

    const ENGLISH_EXCLUSIVE_WORDS = new Set([
      'hello', 'hi', 'thanks', 'thank', 'please', 'regards', 'morning', 'afternoon',
      'evening', 'looking', 'interested', 'quote', 'pricing', 'finance', 'application',
      'delivery', 'deposit', 'quick', 'check', 'checking', 'drive', 'would', 'could',
      'should', 'schedule', 'busy', 'settles', 'assist', 'details', 'vehicle', 'models',
      'particular', 'arrange', 'together', 'whenever', 'ready', 'tomorrow', 'today',
      'suit', 'prefer', 'settle', 'hours', 'chat', 'call', 'speak', 'happy', 'there',
      'search', 'hope', 'having', 'great', 'week', 'chance', 'consider', 'about',
      'english', 'email', 'send', 'message', 'price', 'specs', 'available'
    ]);

    const ENGLISH_PHRASES = [
      'good day', 'good morning', 'good afternoon', 'good evening',
      'let me know', 'i am', 'i would', 'i will', 'i have', 'can you', 'could you',
      'would you', 'are you', 'do you', 'have you', 'will you', 'when can', 'how are',
      'how is', 'hope you', 'how your', 'your schedule', 'good time', 'quick check',
      'happy to assist', 'give you a call', 'give me a call', 'right here', 'after hours',
      'trade in', 'test drive', 'vehicle search', 'hear from you', 'looking for',
      '{SALESPERSON_NAME_LOWER} here from', '{SALESPERSON_NAME_LOWER} here again', 'in english', 'please send', 'send me'
    ];

    function scoreText(text) {
      if (!text || text.startsWith('[') && text.endsWith(']')) return { afr: 0, eng: 0 };
      const t = text.toLowerCase();
      let afr = 0;
      let eng = 0;
      for (const p of AFRIKAANS_PHRASES) if (t.includes(p)) afr += 4;
      for (const p of ENGLISH_PHRASES) if (t.includes(p)) eng += 4;
      const words = t.match(/\b\w+\b/g) || [];
      for (const w of words) {
        if (AFRIKAANS_EXCLUSIVE_WORDS.has(w)) afr++;
        if (ENGLISH_EXCLUSIVE_WORDS.has(w)) eng++;
      }
      return { afr, eng };
    }

    const contactName = (history.contact?.name || query || '').toLowerCase().replace(/[\/\\-]/g, ' ');
    const nameTokens = contactName.split(/\s+/).map(t => t.replace(/[^a-z]/g, '')).filter(Boolean);
    
    let isAfrican = false;
    let isAfrikaansName = false;

    for (const token of nameTokens) {
      if (AFRICAN_FIRST_NAMES.has(token) || AFRICAN_SURNAMES.has(token)) {
        isAfrican = true;
        break;
      }
      if (token.length >= 4 && AFRICAN_STEM_PREFIXES.some(p => token.startsWith(p))) {
        isAfrican = true;
        break;
      }
    }

    if (!isAfrican) {
      const fullStr = nameTokens.join(' ');
      if (['van der', 'van den', 'du plessis', 'du preez', 'du toit', 'de wet', 'de beer', 'de villiers', 'van zyl', 'van niekerk'].some(p => fullStr.includes(p))) {
        isAfrikaansName = true;
      } else {
        for (const token of nameTokens) {
          if (AFRIKAANS_NAMES.has(token)) {
            isAfrikaansName = true;
            break;
          }
        }
      }
    }

    const reasons = [];
    const messages = history.messages || [];

    // Tier 1: Inbound customer messages
    let customerAfr = 0;
    let customerEng = 0;
    for (const msg of messages) {
      if (!msg.from_me) {
        const s = scoreText(msg.content);
        customerAfr += s.afr;
        customerEng += s.eng;
      }
    }

    let detectedLanguage = 'english';
    let confidence = 'STANDARD';
    let swungToAfrikaans = false;

    if (customerAfr >= 2 && customerAfr > customerEng) {
      detectedLanguage = 'afrikaans';
      confidence = 'HIGH';
      swungToAfrikaans = true;
      reasons.push(`Customer actively communicated in Afrikaans (Afr score ${customerAfr} vs Eng ${customerEng})`);
    } else if (customerEng >= 2 && customerEng > customerAfr) {
      detectedLanguage = 'english';
      confidence = 'HIGH';
      swungToAfrikaans = false;
      reasons.push(`Customer actively communicated in English (Eng score ${customerEng} vs Afr ${customerAfr})`);
    } else if (isAfrican) {
      detectedLanguage = 'english';
      confidence = 'HIGH';
      swungToAfrikaans = false;
      reasons.push(`Customer name "${contactName}" is culturally African. Standard business language in SA is English. Afrikaans strictly prohibited.`);
    } else if (isAfrikaansName) {
      detectedLanguage = 'afrikaans';
      confidence = 'HIGH';
      swungToAfrikaans = true;
      reasons.push(`Customer name "${contactName}" is an established Afrikaans cultural name and customer has not requested English.`);
    } else {
      // Outgoing checks for non-African prospects
      let outAfr = 0;
      let outEng = 0;
      for (const msg of messages) {
        if (msg.from_me) {
          const s = scoreText(msg.content);
          outAfr += s.afr;
          outEng += s.eng;
        }
      }
      if (outAfr >= 4 && outAfr > outEng) {
        detectedLanguage = 'afrikaans';
        confidence = 'MEDIUM';
        swungToAfrikaans = true;
        reasons.push(`Prior outgoing conversation conducted in Afrikaans (Afr score ${outAfr} vs Eng ${outEng})`);
      } else {
        detectedLanguage = 'english';
        confidence = 'STANDARD';
        swungToAfrikaans = false;
        reasons.push('Universal South African automotive dealership default is English.');
      }
    }

    res.json({
      success: true,
      query,
      contact: history.contact,
      matched_jids: history.matched_jids,
      matched_phones: history.matched_phones,
      message_count: messages.length,
      recent_messages: messages.slice(-5),
      language_analysis: {
        detected_language: detectedLanguage,
        confidence,
        swung_to_afrikaans: swungToAfrikaans,
        scores: { afrikaans: customerAfr, english: customerEng },
        reasons
      }
    });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// List Contacts (Filterable by type: 'prospect', 'coworker', 'vip', 'all')
app.get('/prospects', (req, res) => {
  try {
    const limit = parseInt(req.query.limit || '20', 10);
    const filterType = req.query.type || 'all';
    const prospects = listRecentProspects(limit, filterType);
    res.json({ success: true, count: prospects.length, filterType, prospects });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Search Message History with optional filter
app.get('/search', (req, res) => {
  try {
    const query = req.query.q || '';
    if (!query) {
      return res.status(400).json({ success: false, error: 'Query parameter ?q= is required' });
    }
    const limit = parseInt(req.query.limit || '20', 10);
    const filterType = req.query.type || 'all';
    const results = searchMessages(query, limit, filterType);
    res.json({ success: true, count: results.length, filterType, results });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Tag / Categorize a Contact (Coworker, Prospect, VIP, notes)
app.post('/tag', (req, res) => {
  try {
    const { phone, contactType, tags, notes, name } = req.body;
    if (!phone) {
      return res.status(400).json({ success: false, error: 'Missing phone parameter' });
    }
    const updated = tagContact(phone, { contactType, tags, notes, name });
    res.json({ success: true, contact: updated });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Trigger Dealer CRM Lead Acceptance Manually via API
app.post('/accept-leads', async (req, res) => {
  try {
    const result = await autoAcceptLeads({ source: 'manual_api_trigger', requestedBy: req.body.requestedBy || 'agent' }, sock);
    res.json(result);
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

function sanitizeDashes(str) {
  if (!str) return str;
  return String(str).replace(/[\u2014\u2013\u2015]/g, '-');
}

// EXPLICIT-ONLY SEND ENDPOINT
app.post('/send', async (req, res) => {
  try {
    const { phone, message, imagePath, documentPath, authorizedBy } = req.body;

    if (!phone || (!message && !imagePath)) {
      return res.status(400).json({ success: false, error: 'Missing phone, message body, or imagePath' });
    }

    if (!authorizedBy) {
      return res.status(403).json({
        success: false,
        error: 'Forbidden: Outbound messages require explicit authorizedBy identifier (e.g. user_instruction)'
      });
    }

    if (connectionStatus !== 'CONNECTED' || !sock) {
      return res.status(503).json({ success: false, error: 'WhatsApp monitor bridge is not currently connected' });
    }

    const cleanPhone = phone.replace(/[^0-9]/g, '');
    const jid = cleanPhone.includes('@') ? cleanPhone : `${cleanPhone}@s.whatsapp.net`;
    const cleanMsg = sanitizeDashes(message);

    logger.info(`Sending explicit outbound message to ${cleanPhone}, authorized by: ${authorizedBy}`);

    let result;
    if (documentPath && fs.existsSync(documentPath)) {
      const buffer = fs.readFileSync(documentPath);
      result = await sock.sendMessage(jid, { document: buffer, mimetype: "application/pdf", fileName: documentPath.split("/").pop(), caption: cleanMsg || "" });
    } else if (imagePath && fs.existsSync(imagePath)) {
      const buffer = fs.readFileSync(imagePath);
      result = await sock.sendMessage(jid, { image: buffer, caption: cleanMsg || '' });
    } else {
      result = await sock.sendMessage(jid, { text: cleanMsg });
    }

    logAuditSend({
      prospectPhone: cleanPhone,
      content: cleanMsg || '[Image Attachment]',
      authorizedBy,
      status: 'SENT',
      msgId: result?.key?.id
    });

    saveMessage({
      id: result?.key?.id || `out_${Date.now()}`,
      jid,
      phoneNumber: cleanPhone,
      fromMe: true,
      senderName: '{SALESPERSON_NAME} / {DEALERSHIP_NAME}',
      messageType: imagePath ? 'image' : 'text',
      content: cleanMsg || '[Image Attachment]',
      timestamp: Math.floor(Date.now() / 1000)
    });

    res.json({
      success: true,
      messageId: result?.key?.id,
      recipient: cleanPhone,
      timestamp: Date.now()
    });
  } catch (err) {
    logger.error({ err }, 'Failed to send explicit WhatsApp message');
    
    if (req.body.phone) {
      logAuditSend({
        prospectPhone: req.body.phone,
        content: req.body.message || '',
        authorizedBy: req.body.authorizedBy || 'unknown',
        status: 'FAILED',
        errorMessage: err.message
      });
    }

    res.status(500).json({ success: false, error: err.message });
  }
});

app.listen(PORT, '127.0.0.1', () => {
  logger.info(`🚀 WhatsApp Monitor REST API listening on http://127.0.0.1:${PORT}`);
});

startWhatsAppMonitor();
