# JAX WhatsApp Prospect Monitor Bridge

A dedicated, passive Baileys WhatsApp bridge designed to monitor incoming customer messages, index conversation history into SQLite, and allow the agent to review prospect interactions.

## Key Features

- **Passive Ingestion (Zero Auto-Replies)**: Listens for messages, saves them into SQLite, and never sends automatic replies.
- **Native Node.js SQLite Integration**: Uses high-performance native `node:sqlite` (DatabaseSync) with WAL mode enabled.
- **Prospect Conversation History**: Instantly review full conversation transcripts for any prospect phone number.
- **Strict Outbound Guardrail**: Outbound messaging is locked down and only executable when explicitly commanded (logged to an audit table).
- **Internal REST API (Port 9095)**: Easy integration for agent tools, cron jobs, and CLI scripts.

---

## Directory Structure

```
jax-whatsapp-monitor/
├── .env.example            # Configuration variables
├── db.mjs                  # SQLite database manager & schema
├── monitor.mjs             # Passive Baileys listener + REST API
├── client.mjs              # Agent client library to query history & send
├── auth_info_monitor/      # Baileys multi-device credentials (auto-created)
├── data/
│   └── prospects.db        # SQLite database (auto-created)
└── package.json
```

---

## Setup & Pairing

1. Create your `.env` file:
   ```bash
   cp .env.example .env
   ```

2. Start the monitor manually to pair with your WhatsApp:
   ```bash
   node monitor.mjs
   ```
   - **QR Code**: Scan the QR code rendered in the terminal with your customer WhatsApp account.
   - **Pairing Code (Alternative)**: Set `PAIRING_PHONE_NUMBER=27...` in `.env` to receive an 8-digit pairing code instead.

3. Once connected, the service will log:
   `✅ WhatsApp Monitor Bridge connected successfully! Passively indexing messages.`

---

## Running with PM2 (Side-by-Side with Existing Bridge)

Add to `{INSTALL_DIR}/ecosystem.config.cjs`:

```javascript
{
  name: 'jax-whatsapp-monitor',
  script: 'monitor.mjs',
  cwd: '{INSTALL_DIR}/jax-whatsapp-monitor',
  interpreter: 'node',
  watch: false,
  autorestart: true,
  max_memory_restart: '200M',
  env: {
    NODE_ENV: 'production',
    API_PORT: '9095',
    SQLITE_DB_PATH: 'jax-shared/data/prospects.db'
  }
}
```

Start or reload PM2:
```bash
pm2 start {INSTALL_DIR}/ecosystem.config.cjs
```

---

## Agent Usage (Client Functions)

In any agent module (e.g. `jax-whatsapp-agent` or `jax-telegram-agent`):

```javascript
import { 
  getProspectConversation, 
  listProspects, 
  searchProspectMessages,
  sendProspectExplicitMessage 
} from '../jax-whatsapp-monitor/client.mjs';

// 1. Review conversation history for a prospect
const history = await getProspectConversation('27841234567', 30);
console.log(`Found ${history.messages.length} messages for prospect`);

// 2. Search message logs for keywords
const leads = await searchProspectMessages('Toyota Hilux');

// 3. Send message ONLY when explicitly instructed by user
await sendProspectExplicitMessage(
  '27841234567',
  'Good day, following up on your finance application for the Hilux.',
  'user_explicit_instruction'
);
```
