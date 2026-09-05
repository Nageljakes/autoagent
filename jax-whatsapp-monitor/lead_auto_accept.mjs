import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import pino from 'pino';
import { logAuditSend, saveMessage, upsertProspect } from './db.mjs';

const execAsync = promisify(exec);
const logger = pino({ level: process.env.LOG_LEVEL || 'info' });

const LEAD_NOTIFIER_PHONE_SUFFIX = process.env.LEAD_NOTIFIER_PHONE || '';
const SCRIPT_PATH = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../skills/autohub-portal/scripts/accept_lead.py');
const PYTHON_PATH = process.env.PYTHONPATH || '';

const VEHICLE_ASSETS = {
  xtrail: path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../jax-shared/assets/vehicles/xtrail_stock.jpg'),
  magnite: path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../jax-shared/assets/vehicles/magnite_stock.jpg'),
  navara: path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../jax-shared/assets/vehicles/navara_stock.jpg')
};

export function getVehicleImagePath(modelName = '') {
  if (!modelName) return null;
  const m = modelName.toLowerCase();
  if (m.includes('trail') || m.includes('x-trail') || m.includes('xtrail')) {
    return VEHICLE_ASSETS.xtrail;
  }
  if (m.includes('magnite')) {
    return VEHICLE_ASSETS.magnite;
  }
  if (m.includes('navara')) {
    return VEHICLE_ASSETS.navara;
  }
  return null;
}

function sanitizeDashes(text) {
  if (!text) return '';
  return text.replace(/[\u2013\u2014]/g, '-');
}

function normalizePhone(p) {
  if (!p) return '';
  let cleaned = p.replace(/[^0-9]/g, '');
  if (cleaned.startsWith('0')) {
    cleaned = '27' + cleaned.slice(1);
  }
  return cleaned;
}

let isRunning = false;
let lastTriggerTime = 0;
const COOLDOWN_MS = 5000; // 5s debounce to prevent duplicate execution on multiple rapid messages

/**
 * Checks if an incoming message matches the LeadNotifier CRM lead notification pattern
 */
export function isLeadNotification({ senderPhone, pushName, text, isGroup, remoteJid }) {
  if (!text) return false;

  const cleanSender = (senderPhone || '').replace(/[^0-9]/g, '');
  const notifierPhone = (process.env.LEAD_NOTIFIER_PHONE || '').replace(/[^0-9]/g, '');
  const isLeadNotifier = notifierPhone.length > 0 && cleanSender.endsWith(notifierPhone);
  const ownerPhone = (process.env.OWNER_PHONE_NUMBER || '').replace(/[^0-9]/g, '');
  const ownerSuffix = ownerPhone.length >= 9 ? ownerPhone.slice(-9) : ownerPhone;
  const salesName = (process.env.SALESPERSON_NAME || '').toLowerCase();
  const mentionsSalesperson = (salesName && text.toLowerCase().includes(salesName)) ||
                        (ownerPhone && text.includes(ownerPhone)) ||
                        (ownerSuffix && text.includes(ownerSuffix)) ||
                        (process.env.OWNER_PHONE_NUMBER && text.includes(`@${process.env.OWNER_PHONE_NUMBER}`)) ||
                        /\bsalesperson\b/i.test(text);
  const isLeadNotice = /\blead(s)?\b/i.test(text);

  // Match if:
  // 1. From an EXPLICITLY CONFIGURED LeadNotifier and mentions lead
  // 2. Or in a group, mentions {SALESPERSON_NAME} and mentions lead, AND comes from a trusted notifier or owner
  if (isLeadNotifier && isLeadNotice) {
    return true;
  }
  const isOwner = ownerSuffix.length > 0 && cleanSender.endsWith(ownerSuffix);
  if (isGroup && mentionsSalesperson && isLeadNotice && (isLeadNotifier || isOwner)) {
    return true;
  }

  return false;
}

/**
 * Automatically triggers the Dealer CRM lead acceptance script and immediately dispatches WhatsApp outreach
 */
export async function autoAcceptLeads(triggerContext = {}, sock = null) {
  const now = Date.now();
  if (isRunning) {
    logger.warn('Dealer CRM lead acceptance already in progress. Skipping duplicate invocation.');
    return { success: false, inProgress: true };
  }
  if (now - lastTriggerTime < COOLDOWN_MS) {
    logger.warn('Dealer CRM lead acceptance triggered within cooldown period. Skipping.');
    return { success: false, rateLimited: true };
  }

  isRunning = true;
  lastTriggerTime = now;

  logger.info({ triggerContext }, '🚀 Auto-accepting leads on Dealer CRM...');

  try {
    const ACTION_SCRIPT_PATH = process.env.ACTION_SCRIPT_PATH || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../skills/autohub-portal/scripts/action_prospect.py');

    const cmd = `PYTHONPATH=${PYTHON_PATH} python3 ${SCRIPT_PATH} --all --json`;
    const { stdout, stderr } = await execAsync(cmd, { timeout: 45000 });
    
    let result = null;
    try {
      result = JSON.parse(stdout.trim());
    } catch (e) {
      const jsonMatch = stdout.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        try {
          result = JSON.parse(jsonMatch[0]);
        } catch (e2) {
          result = { raw: stdout.trim() };
        }
      } else {
        result = { raw: stdout.trim() };
      }
    }

    if (result && result.accepted && result.accepted.length > 0) {
      logger.info({ acceptedCount: result.accepted.length, leads: result.accepted }, '✅ Dealer CRM lead(s) accepted successfully!');

      // Immediate outbound outreach per accepted lead
      const salesperson = process.env.SALESPERSON_NAME || 'Sales Executive';
      const dealership = process.env.DEALERSHIP_NAME || 'Dealership';

      for (const lead of result.accepted) {
        const customerName = (lead.name || 'there').trim();
        const outreachMessage = sanitizeDashes(`Good day ${customerName}, this is ${salesperson}. I am reaching out to you from ${dealership}. When would be the best time to call?`);
        const vehicleImage = getVehicleImagePath(lead.model);
        const cleanPhone = normalizePhone(lead.phone);

        if (!cleanPhone) {
          logger.warn({ lead }, '⚠️ Skipping automated WhatsApp outreach: No valid phone number.');
          continue;
        }

        const jid = `${cleanPhone}@s.whatsapp.net`;

        // Step 2: Add contact to WhatsApp and enable sync with phone
        try {
          // Send vCard contact to self/owner or trigger contact sync if supported
          upsertProspect(jid, cleanPhone, customerName, 'prospect', `inbound_lead,sync_with_phone,${lead.model || 'vehicle'}`);
          logger.info({ cleanPhone, customerName }, '📇 Registering contact in WhatsApp address book');
        } catch (contactErr) {
          logger.warn({ err: contactErr.message }, 'Failed to save contact');
        }

        // Check toggle: OUTBOUND_AUTO_OUTREACH
        const autoOutreachEnabled = process.env.OUTBOUND_AUTO_OUTREACH !== 'false';

        // Step 3: Outbound WhatsApp outreach (or notification to salesperson if disabled)
        if (sock && typeof sock.sendMessage === 'function') {
          if (autoOutreachEnabled) {
            try {
              let sentMsg;
              if (vehicleImage && fs.existsSync(vehicleImage)) {
                sentMsg = await sock.sendMessage(jid, {
                  image: fs.readFileSync(vehicleImage),
                  caption: outreachMessage
                });
              } else {
                sentMsg = await sock.sendMessage(jid, {
                  text: outreachMessage
                });
              }

              const msgId = sentMsg?.key?.id;
              logger.info({ jid, customerName, msgId }, '📤 Automated initial outreach dispatched successfully.');

              logAuditSend({
                prospectPhone: cleanPhone,
                content: outreachMessage,
                authorizedBy: 'inbound_lead_auto_outreach',
                status: 'SENT',
                msgId
              });

              saveMessage({
                id: msgId || `out_${Date.now()}`,
                jid,
                phoneNumber: cleanPhone,
                fromMe: true,
                senderName: `${salesperson} / ${dealership}`,
                messageType: vehicleImage ? 'image' : 'text',
                content: outreachMessage,
                timestamp: Math.floor(Date.now() / 1000)
              });
            } catch (sendErr) {
              logger.error({ err: sendErr.message, phone: cleanPhone }, '❌ Failed to send automated outreach WhatsApp');
              logAuditSend({
                prospectPhone: cleanPhone,
                content: outreachMessage,
                authorizedBy: 'inbound_lead_auto_outreach',
                status: 'FAILED',
                errorMessage: sendErr.message
              });
            }
          } else {
            // Toggle is OFF - Send notification to salesperson instead
            const OWNER_JID = `${process.env.OWNER_PHONE_NUMBER || ''}@s.whatsapp.net`;
            const adminNotifyMessage = sanitizeDashes(`🚨 *New Lead Accepted*\n\n*Name:* ${customerName}\n*Phone:* ${cleanPhone}\n*Vehicle:* ${lead.model || 'Not Specified'}\n\n_Auto-outreach is currently disabled. Please contact the customer manually._`);
            logger.info({ customerName, model: lead.model }, `📱 Auto-outreach disabled. Sending notification to ${salesperson} instead.`);
            try {
              await sock.sendMessage(OWNER_JID, { text: adminNotifyMessage });
            } catch (notifyErr) {
              logger.error({ err: notifyErr.message }, `❌ Failed to send admin notification to ${salesperson}`);
            }
          }
        } else {
          logger.warn('WhatsApp socket instance not active or supplied to autoAcceptLeads. Outreach message not dispatched.');
        }

        // Step 4: Automatically log interaction note to Dealer CRM and reschedule diary entry to tomorrow
        try {
          const rawNote = autoOutreachEnabled
            ? 'Lead accepted and automatic customer greeting whatsapp sent.'
            : 'Lead accepted. Auto-outreach disabled. Pending manual contact.';
          const actionNote = sanitizeDashes(rawNote);
          const actionCmd = `PYTHONPATH=${PYTHON_PATH} python3 ${ACTION_SCRIPT_PATH} --custid "${lead.ileadcustid}" --query "${cleanPhone}" --note "${actionNote}" --days 1`;
          logger.info({ custid: lead.ileadcustid, cleanPhone }, '📝 Logging Dealer CRM diary note and rescheduling...');
          const actionRes = await execAsync(actionCmd, { timeout: 30000 });
          logger.info({ custid: lead.ileadcustid, cleanPhone, out: actionRes.stdout.trim() }, '✅ Dealer CRM note logged and diary moved to tomorrow');
        } catch (actionErr) {
          logger.error({ error: actionErr.message, custid: lead.ileadcustid, cleanPhone }, '⚠️ Failed to log note on Dealer CRM');
        }
      }
    } else {
      logger.info({ result }, 'ℹ️ Dealer CRM lead acceptance completed. No new leads found in inbox.');
    }

    return { success: true, result };
  } catch (err) {
    logger.error({ error: err.message, stderr: err.stderr }, '❌ Error running Dealer CRM accept_lead script');
    return { success: false, error: err.message };
  } finally {
    isRunning = false;
  }
}
