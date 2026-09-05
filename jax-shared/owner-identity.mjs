// Phone and LID identifiers belong to different namespaces. Never compare their
// digit strings across namespaces or infer ownership from a substring.
export function normalizeWhatsAppJid(value) {
  if (typeof value !== 'string') return null;
  const match = /^(\d+)(?::\d+)?@(s\.whatsapp\.net|lid)$/.exec(value);
  return match && match[0] === value ? `${match[1]}@${match[2]}` : null;
}

function configuredIdentity(value, domain) {
  if (typeof value !== 'string' || !value.trim()) return null;
  const raw = value.trim();
  if (raw.includes('@')) {
    const jid = normalizeWhatsAppJid(raw);
    return jid?.endsWith(`@${domain}`) ? jid : null;
  }
  if (domain === 'lid') return /^\d+$/.test(raw) ? `${raw}@lid` : null;
  // Allow ordinary international phone formatting in trusted configuration.
  if (!/^\+?\d[\d ()-]*$/.test(raw)) return null;
  return `${raw.replace(/\D/g, '')}@s.whatsapp.net`;
}

export function isWhatsAppOwner(jid, {phone, lid, monitorAccounts = []} = {}) {
  const candidate = normalizeWhatsAppJid(jid);
  if (!candidate) return false;
  const ownerPhone = configuredIdentity(phone, 's.whatsapp.net');
  const ownerLid = configuredIdentity(lid, 'lid');
  if (candidate === ownerPhone || candidate === ownerLid) return true;
  // A paired monitor can supply the owner's LID only when its phone is the
  // explicitly configured owner phone. Old/unrelated pairings grant no access.
  if (!ownerPhone) return false;
  return monitorAccounts.some(account =>
    normalizeWhatsAppJid(account?.id) === ownerPhone &&
    candidate.endsWith('@lid') && normalizeWhatsAppJid(account?.lid) === candidate
  );
}
