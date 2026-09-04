---
name: whatsapp-monitor
description: >-
  Query, review, and import indexed prospect/customer WhatsApp conversations, incoming replies, and message history
  captured in real-time by the JAX WhatsApp Monitor bridge (REST API on port 9095 and SQLite database).
---

# JAX WhatsApp Monitor & Prospect History

## Overview
The **JAX WhatsApp Monitor** (`jax-whatsapp-monitor`) is a background Baileys service that passively monitors and indexes WhatsApp conversations into SQLite (`{INSTALL_DIR}/jax-shared/data/prospects.db`). It provides real-time access to prospect/customer messages, contact history, and search capabilities.

## Key Operational Safeguards (Baileys & Multi-Device)
- **Key Store Caching**: Always ensure `makeCacheableSignalKeyStore(state.keys, logger)` is used in `auth.keys` when initializing `makeWASocket` to prevent `Bad MAC` session decryption failures during high-throughput syncs.
- **Message Content Extraction**: Filter out protocol stubs and empty `{}` sync frames to prevent dummy `[Media/Message]` rows from polluting the message database.

## Access Methods

### 1. REST API (Port 9095)
The monitor runs an internal HTTP API at `http://127.0.0.1:9095`:

- **Get Conversation History for a Prospect**:
  `curl -s "http://127.0.0.1:9095/history/<phone_number>?limit=50"`
  Example: `curl -s "http://127.0.0.1:9095/history/27821234567"`

- **Search Messages (Keywords, Vehicle names, customer quotes)**:
  `curl -s "http://127.0.0.1:9095/search?q=<query>&type=all"`
  Example: `curl -s "http://127.0.0.1:9095/search?q=Magnite"`

- **List Indexed Contacts / Prospects**:
  `curl -s "http://127.0.0.1:9095/prospects?limit=30&type=prospect"`

- **Send Outbound WhatsApp Message (Explicit User Authorization Only)**:
  ```bash
  curl -s -X POST http://127.0.0.1:9095/send \
    -H "Content-Type: application/json" \
    -d '{"phone": "27821234567", "message": "Hi..., {SALESPERSON_NAME} here from {DEALERSHIP_NAME}...", "authorizedBy": "user_explicit_command"}'
  ```
  - **Payload**: `phone` (E.164 formatted string without `+`), `message` (UTF-8 text), `authorizedBy` (`"user_explicit_command"`).
  - **Sender Identity & Naming (CRITICAL)**: Always compose and introduce outbound messages from **{SALESPERSON_NAME}** (e.g. '{SALESPERSON_NAME} here from {DEALERSHIP_NAME}' or '{SALESPERSON_NAME} hier van {DEALERSHIP_NAME}'). NEVER refer to him as '{CRM_USERNAME}' under any circumstances.
  - **STRICT LONG DASH BAN (CRITICAL)**: Outbound WhatsApp messages must NEVER contain a long dash (em dash or en dash). Always use standard short hyphens (-) or natural punctuation.
  - **Response**: `{"success": true, "messageId": "3EB0...", "recipient": "...", "timestamp": 178756...}`
  - **Paired Workflow**: Used in tandem with `action_prospect.py` to dispatch prospect follow-ups and automatically move diary dates on Dealer CRM.

### 2. Direct SQLite Database
You can query the SQLite database directly:
- **Path**: `{INSTALL_DIR}/jax-shared/data/prospects.db`
- **Tables**:
  - `prospects` (`jid`, `phone_number`, `name`, `contact_type`, `notes`, `tags`, `message_count`, `last_interaction_at`)
  - `messages` (`id`, `prospect_jid`, `phone_number`, `from_me`, `sender_name`, `message_type`, `content`, `timestamp`)

Example query via CLI:
```bash
sqlite3 {INSTALL_DIR}/jax-shared/data/prospects.db "SELECT from_me, content, datetime(timestamp, 'unixepoch') FROM messages WHERE phone_number LIKE '%640884966%' ORDER BY timestamp DESC LIMIT 20;"
```

### 3. Historical Chat Import & Backfilling
To import or backfill historical chat records from existing conversations, user metadata, and log files:
```bash
node {INSTALL_DIR}/jax-whatsapp-monitor/import_all_chats.mjs
```
- Ingests `{INSTALL_DIR}/jax-shared/data/conversations/*.json`
- Ingests user metadata from `{INSTALL_DIR}/jax-shared/data/users/*.json`
- Extracts incoming user prompts and logs from `{INSTALL_DIR}/jax-shared/data/logs/pm2-whatsapp-out.log`
- Normalizes JIDs and classifies contacts (`vip`, `internal_team`, `prospect`).

### 4. Node.js Client Helper
Module available at `{INSTALL_DIR}/jax-whatsapp-monitor/client.mjs`:
```javascript
import { getProspectConversation, searchProspectMessages, listProspects } from '{INSTALL_DIR}/jax-whatsapp-monitor/client.mjs';
```

### 5. Prospect Context & Language Preference Analysis Endpoint
- Endpoint: `GET http://127.0.0.1:9095/context/<phone_or_name>`
- Unifies conversations across phone numbers, South African format variants (072... vs 2772...), and WhatsApp mobile LIDs (`@lid`).
- Computes comprehensive language analysis and swing detection:
  - `detected_language`: 'afrikaans' | 'english'
  - `confidence`: 'HIGH' | 'MEDIUM' | 'STANDARD'
  - `swung_to_afrikaans`: boolean
  - `reasons`: detailed list of linguistic and cultural markers

### 6. Autonomous Customer Follow-Up Tool (`action_followup.py`)
- Location: `{INSTALL_DIR}/.gemini/config/skills/whatsapp-monitor/scripts/action_followup.py`
  (also accessible via `{INSTALL_DIR}/.gemini/config/skills/crm-portal/scripts/action_followup.py`)
- Usage:
  ```bash
  PYTHONPATH={INSTALL_DIR}/.local/lib/python3.11/site-packages python3 {INSTALL_DIR}/.gemini/config/skills/whatsapp-monitor/scripts/action_followup.py --query "<Name or Phone>" --intent "<Intent>" --days 1
  ```
- Capabilities:
  - Gathers full context across phone numbers, mobile LIDs, and Dealer CRM CRM notes.
  - Automatically detects preferred language (Afrikaans vs English) and swing history.
  - Drafts natural 1-2 sentence follow-up matching {SALESPERSON_NAME}' voice and intent.
  - Enforces {SALESPERSON_NAME} sender identity (never {CRM_USERNAME}) and strict long dash ban.
  - Guards against sending English messages to verified Afrikaans customers.
  - Dispatches via the monitor bridge and dual-logs to Dealer CRM CRM while rescheduling the diary.
- Flags:
  - `--query`, `-q`: Customer name, surname, or phone number.
  - `--name`, `-n`: Explicit customer name if known (ensures 100% accurate cultural name evaluation even when querying by phone).
  - `--phone`, `-p`: Explicit customer phone number if known.
  - `--intent`, `-i`: Purpose or instruction from {SALESPERSON_NAME} (e.g. check-in, trade-in, callback).
  - `--message`, `-m`: Optional draft text (auto-adapted to Afrikaans if customer is Afrikaans).
  - `--language`, `-l`: Explicit override ('afrikaans', 'english', 'auto').
  - `--days`, `-d`: Diary reschedule days (default: 1).
  - `--dry-run`: Test context analysis and draft without dispatching.
  - `--json`: Output raw JSON analysis.
- Safeguards:
  - STRICT BAN ON NUMERIC GREETINGS: Never greet a customer by their phone number ('Hi 082...').
  - TRUTHFUL COMPLETION REPORTING: Always quote the exact message delivered by the tool; never hallucinate or alter delivered text.

## Live WhatsApp Reconnaissance & Bridge Health Protocol (MANDATORY)
Before generating diary summaries, executive briefings, or customer recommendations:
1. **Bridge Verification**: Ensure the WhatsApp monitor process is actively running and connected (`GET http://127.0.0.1:9095/health` or `pm2 status jax-whatsapp-monitor`). If stopped or disconnected, restart via `pm2 restart jax-whatsapp-monitor`.
2. **True Phone Resolution**: Always extract all possible phone numbers from the Customer ERA profile (`customerera_selecttemplate.cfm`) so queries match the bridge accurately.
3. **Signal Precedence**: Live WhatsApp conversation history ALWAYS supersedes older Dealer CRM notes. Never report "no response" if the customer sent incoming messages via WhatsApp.

## Creator & Internal Message Isolation (Prompt Leakage Prevention) (MANDATORY)
To prevent internal developer prompts, slash commands (`/goal`, `/learn`, `/plan`), and AI agent messages from leaking into customer briefings:
1. **Strict Type Filtering**: All message searches and prospect queries MUST supply `type=prospect` (e.g. `GET http://127.0.0.1:9095/search?q=<name>&type=prospect` or `SELECT ... WHERE p.contact_type = 'prospect'`).
2. **Creator Identification**: Creator LIDs (`{OWNER_LID}`) and personal numbers (`{OWNER_PHONE}`) are strictly classified as `vip` / `internal_team` and must never be queried as customer prospects.
3. **Directive & Syntax Stripping**: Generator scripts must automatically discard any message text containing slash directives (`/goal`, `/learn`, `/boost`, `/plan`), bot identities (`Tiny AI Agent`), or skill names (`SKILL.md`).

## Integration with Dealer CRM Portal & Diary Workflows
- **Cross-Referencing**: When executing diary extractions or working through the daily 5-by-5 protocol (`crm-portal` skill), cross-reference customer phone numbers against `http://127.0.0.1:9095/history/<phone>`.
- **Pre-Call WhatsApp Context**: Inspect whether the customer has already received an introduction, viewed quotes, or sent specific objections/requests via WhatsApp before calling.
- **Dynamic Likelihood Scoring**: Real-time WhatsApp responsiveness (replies, inquiries, trade-in details) directly increases lead conversion probability in the daily diary ranking.
- Always check the WhatsApp monitor when the user asks if a customer has replied, what was discussed in WhatsApp, or whether messages were delivered.
