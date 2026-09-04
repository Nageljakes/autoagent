/**
 * Prospect & Contact WhatsApp Monitor Client
 * Helper module for agents to review prospect/coworker conversations and send explicit messages.
 */

const API_BASE = process.env.MONITOR_API_BASE || 'http://127.0.0.1:9095';

/**
 * Fetch conversational history for a contact (prospect or coworker)
 * @param {string} phone - Phone number
 * @param {number} limit - Number of recent messages to fetch
 */
export async function getProspectConversation(phone, limit = 50) {
  const cleanPhone = phone.replace(/[^0-9]/g, '');
  const res = await fetch(`${API_BASE}/history/${cleanPhone}?limit=${limit}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch history: ${res.statusText}`);
  }
  return await res.json();
}

/**
 * List contacts with optional filter ('all', 'prospect', 'coworker', 'vip')
 * @param {number} limit - Number of contacts to list
 * @param {string} type - 'all', 'prospect', 'coworker'
 */
export async function listProspects(limit = 20, type = 'all') {
  const res = await fetch(`${API_BASE}/prospects?limit=${limit}&type=${type}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch contacts: ${res.statusText}`);
  }
  return await res.json();
}

/**
 * List only sales prospects/customers
 */
export async function listProspectsOnly(limit = 20) {
  return listProspects(limit, 'prospect');
}

/**
 * List only coworkers/internal team members
 */
export async function listCoworkers(limit = 20) {
  return listProspects(limit, 'coworker');
}

/**
 * Search all message logs with optional contact type filtering
 * @param {string} query - Keyword or phrase to search
 * @param {string} type - 'all', 'prospect', 'coworker'
 */
export async function searchProspectMessages(query, type = 'all') {
  const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query)}&type=${type}`);
  if (!res.ok) {
    throw new Error(`Failed to search messages: ${res.statusText}`);
  }
  return await res.json();
}

/**
 * Tag or categorize a contact (e.g. mark as coworker, add notes/tags)
 * @param {string} phone - Contact phone number
 * @param {object} metadata - { contactType: 'coworker'|'prospect'|'vip', tags, notes, name }
 */
export async function tagContact(phone, { contactType, tags, notes, name } = {}) {
  const res = await fetch(`${API_BASE}/tag`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      phone,
      contactType,
      tags,
      notes,
      name
    })
  });
  const data = await res.json();
  if (!res.ok || !data.success) {
    throw new Error(data.error || 'Failed to tag contact');
  }
  return data.contact;
}

/**
 * Send an outbound message strictly under explicit user authorization
 * @param {string} phone - Recipient phone number
 * @param {string} message - Message text
 * @param {string} authorizedBy - Reason or instruction reference (e.g. 'user_command')
 */
export async function sendProspectExplicitMessage(phone, message, authorizedBy = 'user_explicit_command') {
  const res = await fetch(`${API_BASE}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      phone,
      message,
      authorizedBy
    })
  });
  const data = await res.json();
  if (!res.ok || !data.success) {
    throw new Error(data.error || 'Failed to send message');
  }
  return data;
}

export default {
  getProspectConversation,
  listProspects,
  listProspectsOnly,
  listCoworkers,
  searchProspectMessages,
  tagContact,
  sendProspectExplicitMessage
};
