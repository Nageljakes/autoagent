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
  const isLeadNotifier = cleanSender.endsWith(LEAD_NOTIFIER_PHONE_SUFFIX) || (pushName && /(lead|crm|portal|notifier)/i.test(pushName));
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
  // 1. From LeadNotifier and mentions lead (direct or in group)
  // 2. Or in group, mentions {SALESPERSON_NAME} and mentions lead
  if (isLeadNotifier && isLeadNotice) {
    return true;
  }
  if (isGroup && mentionsSalesperson && isLeadNotice) {
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
      for (const lead of result.accepted) {
        const customerName = (lead.name || 'there').trim();
        const outreachMessage = sanitizeDashes(`Good day ${customerName}, this is {SALESPERSON_NAME}. I am reaching out to you from {DEALERSHIP_NAME}. When would be the best time to call?`);
        const vehicleImage = getVehicleImagePath(lead.model);
        const cleanPhone = normalizePhone(lead.phone);

        if (!cleanPhone) {
          logger.warn({ lead }, '⚠️ Skipping automated WhatsApp outreach: No valid phone number.');
          continue;
        }

        const jid = `${cleanPhone}@s.whatsapp.net`;

        // Step 2: Add contact to WhatsApp and enable sync with phone
        try {
          upsertProspect(jid, cleanPhone, customerName, 'prospect', `inbound_lead,sync_with_phone,${lead.model || 'vehicle'}`);
          logger.info({ cleanPhone, customerName, model: lead.model }, '📇 Added contact to WhatsApp with phone sync enabled');
        } catch (contactErr) {
          logger.warn({ error: contactErr.message, cleanPhone }, '⚠️ Failed to index contact locally before sending outreach');
        }

        // Dynamic toggle read for live switching without restart
        let autoOutreachEnabled = false;
        try {
          const envContent = fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '.env'), 'utf-8');
          const match = envContent.match(/^AUTO_OUTREACH_ENABLED=(true|false)/m);
          if (match) {
            autoOutreachEnabled = match[1] === 'true';
          }
        } catch (e) {
          // Ignore, defaults to false
        }

        // Step 3: Outbound WhatsApp outreach (or notification to {SALESPERSON_NAME} if disabled)
        if (sock) {
          if (autoOutreachEnabled) {
            logger.info({ cleanPhone, customerName, model: lead.model, vehicleImage }, '📱 Initiating inbound lead WhatsApp outreach...');
            try {
              let sendResult;
              if (vehicleImage && fs.existsSync(vehicleImage)) {
                const imgBuffer = fs.readFileSync(vehicleImage);
                sendResult = await sock.sendMessage(jid, {
                  image: imgBuffer,
                  caption: outreachMessage
                });
                logger.info({ phone: cleanPhone, msgId: sendResult?.key?.id, vehicleImage }, '📸 Sent outreach message with vehicle stock poster!');
              } else {
                sendResult = await sock.sendMessage(jid, {
                  text: outreachMessage
                });
                logger.info({ phone: cleanPhone, msgId: sendResult?.key?.id }, '💬 Sent outreach text message!');
              }

              const msgId = sendResult?.key?.id;
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
                senderName: '{SALESPERSON_NAME} / {DEALERSHIP_NAME}',
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
            // Toggle is OFF - Send notification to {SALESPERSON_NAME} instead
            const OWNER_JID = `${process.env.OWNER_PHONE_NUMBER || ''}@s.whatsapp.net`;
            const adminNotifyMessage = sanitizeDashes(`🚨 *New Lead Accepted*\n\n*Name:* ${customerName}\n*Phone:* ${cleanPhone}\n*Vehicle:* ${lead.model || 'Not Specified'}\n\n_Auto-outreach is currently disabled. Please contact the customer manually._`);
            logger.info({ customerName, model: lead.model }, '📱 Auto-outreach disabled. Sending notification to {SALESPERSON_NAME} instead.');
            try {
              await sock.sendMessage(OWNER_JID, { text: adminNotifyMessage });
            } catch (notifyErr) {
              logger.error({ err: notifyErr.message }, '❌ Failed to send admin notification to {SALESPERSON_NAME}');
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
