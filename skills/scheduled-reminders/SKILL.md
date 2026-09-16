---
name: scheduled-reminders
description: Schedule persistent timezone-aware customer callback reminders and autoHUB diary alerts with 5-minute prior dispatches.
---

# Scheduled Reminders & Customer Callback System

## Overview
This skill provides persistent, automated time-based reminder scheduling for BB Gezina Nissan sales operations. Whenever a customer requests a callback window (e.g. "call me at 12 PM", "available around 13:00", "bel my asb môre 9 uur") or Jakes requests an operational reminder, this skill ensures accurate SAST (UTC+2) timezone calculation, persistent SQLite queuing in `prospects.db`, autoHUB CRM synchronization, and automatic delivery of a structured WhatsApp alert card to Jakes' phone 5 minutes prior to the scheduled callback time.

## Core Timezone Architecture
- **Server Operating System Clock**: UTC (Coordinated Universal Time).
- **Dealership Local Clock**: South Africa Standard Time (SAST, UTC+2).
- **Time Offset Calculation**:
  - SAST is always 2 hours ahead of UTC (`UTC = SAST - 2 hours`).
  - There is no Daylight Saving Time in South Africa.
  - Examples:
    - 09:30 AM SAST = 07:30 AM UTC
    - 12:00 PM SAST = 10:00 AM UTC
    - 13:00 (1:00 PM) SAST = 11:00 AM UTC
    - 15:30 (3:30 PM) SAST = 13:30 UTC

## Persistent Storage & 5-Minute Prior Protocol
- **Persistent Database**: `/home/jakes/jax-shared/data/prospects.db` -> Table `scheduled_reminders`.
- **Trigger Window**: Every reminder is scheduled with `remind_at_utc = target_time_utc - 5 minutes`.
- **Background Daemon**: `jax-whatsapp-monitor` runs a 15-second poller under PM2. When `remind_at_utc <= current_time`, the daemon automatically dispatches the alert card directly to Jakes' personal WhatsApp device (+27 82 739 8595).
- **STRICT BAN ON IN-MEMORY TIMERS**: Never use the Antigravity in-memory `schedule` tool for operational reminders. Headless CLI agent processes terminate immediately after responding, destroying in-memory timers. Always invoke `schedule_reminder.py --schedule`.

## Autonomous Ingestion & Hook Integrations

### 1. Inbound WhatsApp Customer Messages
In `jax-whatsapp-monitor/monitor.mjs`, every inbound customer WhatsApp reply is inspected for callback intent and time expressions. When detected:
- Target SAST time and date are parsed automatically.
- Customer name and phone are linked to `prospects.db`.
- The reminder is queued with `source: 'whatsapp_inbound'`.
- An immediate confirmation is sent to Jakes:
  `📅 Callback Scheduled: Customer {name} requested callback for {time} SAST. I will remind you 5 minutes prior on WhatsApp.`

### 2. autoHUB CRM Diary & Action Notes
In `action_prospect.py` (and CRM sync scripts), whenever an interaction note is logged:
- If the note mentions a callback window (e.g. "Customer asked I should call him back at 1PM"), the callback engine automatically queues the reminder with `source: 'autohub_note'`.

### 3. Explicit Commands from Jakes
When Jakes instructs you to schedule a reminder in chat:
Always execute the dedicated CLI engine:
```bash
PYTHONPATH=/home/jakes/.local/lib/python3.11/site-packages python3 /home/jakes/.gemini/config/skills/scheduled-reminders/scripts/schedule_reminder.py \
  --schedule \
  --time "12:30 PM" \
  [--date "today" | "tomorrow" | "DD/MM/YYYY"] \
  --name "Customer Name" \
  --phone "0827398595" \
  --topic "Vehicle Model / Topic" \
  --notes "Context notes"
```

## CLI Helper Utility Reference
Script location: `/home/jakes/.gemini/config/skills/scheduled-reminders/scripts/schedule_reminder.py`

Commands:
- `--schedule`, `-s`: Schedule a new persistent reminder.
- `--list`, `-l`: List active pending reminders with countdowns.
- `--check-due`: Immediately check and dispatch due reminders.
- `--scan-notes`: Scan autoHUB `prospect_notes` for unscheduled callbacks.
- `--scan-messages`: Scan inbound WhatsApp messages for unscheduled callbacks.
- `--cancel <id>`: Cancel a pending reminder.
- `--parse "<text>"`: Test natural language date/time parsing.

## WhatsApp Card Format Delivered to Jakes
Delivered to: `27827398595@s.whatsapp.net` via `POST http://127.0.0.1:9095/send`.
```text
🚨 *Callback Reminder*

*Customer:* {Customer Name}
*Phone:* {Customer Phone}
*Time:* {Scheduled Time SAST} (in 5 minutes)
*Vehicle:* {Vehicle Model}

_{Context / Action required}_
```

## Verification Checklist
- [x] Database table `scheduled_reminders` created and indexed in `prospects.db`.
- [x] Natural language parser handles 12h/24h, English, and Afrikaans.
- [x] Target SAST converted accurately to UTC and 5-minute prior trigger.
- [x] Background 15s poller active in `jax-whatsapp-monitor` (PM2).
- [x] Hook added to `action_prospect.py` for autoHUB notes.
- [x] Inbound message hook added to `monitor.mjs`.
- [x] Live field verification confirmed via WhatsApp dispatches.
