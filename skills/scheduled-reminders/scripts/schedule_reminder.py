#!/usr/bin/env python3
"""
schedule_reminder.py - Persistent, Timezone-Aware Callback & Reminder Engine for Dealership OS
Handles SAST (UTC+2) timezone calculations, natural language time/date parsing (English & Afrikaans),
persists reminders to SQLite prospects.db, and dispatches WhatsApp alert cards to Jakes' phone 5 minutes prior.
"""

import sys
import os
import re
import json
import sqlite3
import argparse
import requests
from datetime import datetime, timedelta, timezone

SAST_OFFSET_HOURS = 2
SAST_TZ = timezone(timedelta(hours=SAST_OFFSET_HOURS))

PROSPECTS_DB_PATH = os.getenv("PROSPECTS_DB_PATH", os.path.expanduser("~/jax-shared/data/prospects.db"))
AUTOHUB_DB_PATH = os.getenv("AUTOHUB_DB_PATH", os.path.expanduser("~/.gemini/antigravity-cli/scratch/prospect_history.db"))
JAKES_WHATSAPP_PHONE = os.getenv("JAKES_WHATSAPP_PHONE", "27827398595")
BRIDGE_API_URL = os.getenv("BRIDGE_API_URL", "http://127.0.0.1:9095/send")

def get_db_connection(db_path=PROSPECTS_DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_reminders_table():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS scheduled_reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT,
        customer_phone TEXT,
        vehicle_model TEXT,
        source TEXT NOT NULL, -- 'whatsapp_inbound', 'autohub_note', 'jakes_command', 'manual'
        source_ref TEXT,
        requested_time_raw TEXT,
        target_time_utc TEXT NOT NULL,
        target_time_sast TEXT NOT NULL,
        remind_at_utc TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'SENT', 'FAILED', 'CANCELLED'
        notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        sent_at DATETIME
    );
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_reminders_status_remind ON scheduled_reminders(status, remind_at_utc);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_reminders_source_ref ON scheduled_reminders(source_ref);")
    conn.commit()
    conn.close()

def mask_phone(phone: str) -> str:
    if not phone:
        return "Not Listed"
    clean = re.sub(r"[^0-9]", "", phone)
    if len(clean) == 10:
        return f"{clean[:3]} *** {clean[6:]}"
    if len(clean) == 11 and clean.startswith("27"):
        return f"0{clean[2:4]} *** {clean[7:]}"
    if len(clean) > 6:
        return f"{clean[:3]} *** {clean[-3:]}"
    return clean

def parse_callback_intent_and_time(text: str, base_datetime_utc=None) -> dict | None:
    """
    Extracts callback intent, target date, and target time in SAST from natural language text.
    Returns parsed dictionary or None.
    """
    if not text:
        return None
    
    clean = text.strip()
    lower = clean.lower()
    
    callback_keywords = [
        r"\bcall\b", r"\bcall\s+back\b", r"\bcallback\b", r"\bcalling\b",
        r"\bphone\b", r"\bring\b", r"\bcontact\b", r"\bget\s+back\b",
        r"\breach\s+out\b", r"\bavailable\b", r"\bfree\b", r"\btalk\b",
        r"\bspeak\b", r"\bremind\b", r"\breminder\b", r"\baround\b", r"\barround\b",
        r"\bbel\b", r"\bskakel\b", r"\bkontak\b", r"\bpraat\b",
        r"\bluitjie\b", r"\bgesels\b", r"\bbeskikbaar\b", r"\bomstreeks\b",
        r"\bomtrent\b"
    ]
    
    has_callback_intent = any(re.search(kw, lower) for kw in callback_keywords)
    is_time_reply = any(re.search(p, lower) for p in [
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
        r"\b\d{1,2}h\d{2}\b",
        r"\b[012]?\d:[0-5]\d\b",
        r"\b\d{1,2}\s*uur\b"
    ])
    
    if not has_callback_intent and not is_time_reply:
        return None
    
    if base_datetime_utc is None:
        base_datetime_utc = datetime.now(timezone.utc)
    base_sast = base_datetime_utc.astimezone(SAST_TZ)
    
    target_date = base_sast.date()
    
    # Tomorrow / Môre
    if re.search(r"\b(tomorrow|môre|more)\b", lower):
        target_date = target_date + timedelta(days=1)
    
    # Weekday detection
    weekdays_en = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
    weekdays_af = {"maandag": 0, "dinsdag": 1, "woensdag": 2, "donderdag": 3, "vrydag": 4, "saterdag": 5, "sondag": 6}
    
    for wname, wday in {**weekdays_en, **weekdays_af}.items():
        if re.search(rf"\b{wname}\b", lower):
            current_wday = base_sast.weekday()
            days_ahead = (wday - current_wday) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_date = base_sast.date() + timedelta(days=days_ahead)
            break
            
    # Explicit date: DD/MM or DD/MM/YYYY or YYYY-MM-DD
    dm_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", clean)
    if dm_match:
        d = int(dm_match.group(1))
        m = int(dm_match.group(2))
        y = int(dm_match.group(3)) if dm_match.group(3) else target_date.year
        if y < 100:
            y += 2000
        try:
            target_date = datetime(y, m, d).date()
        except ValueError:
            pass

    # Detect Time
    hour = None
    minute = 0
    raw_time_str = ""
    
    # Pattern A: 12-hour format with am/pm: "12:30 PM", "12:30pm", "12 PM", "12pm", "1pm", "9am"
    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lower)
    if time_match:
        h = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3)
        if meridiem == "pm" and h < 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
        hour = h
        raw_time_str = time_match.group(0)
    else:
        # Pattern B: "12h30", "14h00", "09h00" (South African notation)
        h_match = re.search(r"\b(\d{1,2})h(\d{2})\b", lower)
        if h_match:
            hour = int(h_match.group(1))
            minute = int(h_match.group(2))
            raw_time_str = h_match.group(0)
        else:
            # Pattern C: 24-hour format: "12:30", "14:00", "09:30", "13:00"
            m24 = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lower)
            if m24:
                hour = int(m24.group(1))
                minute = int(m24.group(2))
                raw_time_str = m24.group(0)
            else:
                # Pattern D: Afrikaans "9 uur", "2 uur", "14:00 uur", "12 uur middag"
                uur_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:uur|o['’]?clock)\b", lower)
                if uur_match:
                    h = int(uur_match.group(1))
                    minute = int(uur_match.group(2) or 0)
                    if h in [1, 2, 3, 4, 5, 6] and not any(x in lower for x in ["oggend", "am", "morning"]):
                        h += 12
                    hour = h
                    raw_time_str = uur_match.group(0)
                else:
                    # Pattern E: Bare hour preceded by at/around/om/omstreeks/by: "at 12", "around 13", "om 2"
                    bare_match = re.search(r"\b(?:at|around|arround|om|omstreeks|by|after)\s*(\d{1,2})\b", lower)
                    if bare_match:
                        h = int(bare_match.group(1))
                        if 1 <= h <= 6 and not any(x in lower for x in ["oggend", "am", "morning"]):
                            h += 12
                        if 7 <= h <= 23:
                            hour = h
                            minute = 0
                            raw_time_str = bare_match.group(0)

    if hour is None:
        return None

    target_sast_dt = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        second=0,
        tzinfo=SAST_TZ
    )
    
    diff_s = (target_sast_dt - base_sast).total_seconds()
    if diff_s < -900 and not any(re.search(rf"\b{w}\b", lower) for w in ["today", "vandag", "tomorrow", "môre", "more"]):
        target_sast_dt += timedelta(days=1)
        diff_s = (target_sast_dt - base_sast).total_seconds()

    target_utc_dt = target_sast_dt.astimezone(timezone.utc)
    
    # 5 MINUTES PRIOR:
    remind_utc_dt = target_utc_dt - timedelta(minutes=5)
    
    delta_to_reminder = (remind_utc_dt - base_datetime_utc).total_seconds()
    delta_to_target = (target_utc_dt - base_datetime_utc).total_seconds()
    
    # Extract phone if present in text
    phone_match = re.search(r"(?:0|\+?27)[\s-]*\d{2}[\s-]*\d{3}[\s-]*\d{4}|\b\d{10}\b", clean)
    extracted_phone = phone_match.group(0).replace(" ", "").replace("-", "") if phone_match else ""
    
    # Extract customer name if preceded by "call", "bel", "for", "to"
    extracted_name = ""
    name_match = re.search(r"(?:call|bel|contact|skakel|to)\s+(?:mr\.?|mrs\.?|dr\.?|ms\.?)?\s*([A-Za-z\s]+?)(?:\s+(?:on|at|\d|\()|$)", clean, re.IGNORECASE)
    if name_match:
        cand = name_match.group(1).strip()
        if cand.lower() not in ["me", "back", "him", "her", "my", "asb", "again", "today", "tomorrow"]:
            extracted_name = cand
    
    return {
        "raw_text": clean,
        "raw_time_str": raw_time_str,
        "extracted_name": extracted_name,
        "extracted_phone": extracted_phone,
        "target_sast_dt": target_sast_dt,
        "target_sast_str": target_sast_dt.strftime("%Y-%m-%d %H:%M:%S SAST"),
        "target_utc_iso": target_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "remind_utc_iso": remind_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minutes_until_target": round(delta_to_target / 60, 1),
        "seconds_until_remind": max(0, int(delta_to_reminder)),
        "is_due_now": delta_to_reminder <= 0
    }

def schedule_reminder(
    time_str: str,
    date_str: str = "today",
    name: str = "",
    phone: str = "",
    vehicle: str = "",
    notes: str = "",
    source: str = "manual",
    source_ref: str = None
) -> dict:
    """
    Schedules a reminder into prospects.db scheduled_reminders table.
    Computes exact target time and sets remind_at_utc to 5 minutes prior.
    """
    init_reminders_table()
    
    full_query_text = f"{time_str} {date_str}"
    parsed = parse_callback_intent_and_time(full_query_text)
    
    if not parsed:
        # Fallback to explicit parser if natural parser missed
        from schedule_reminder import SAST_TZ
        now_sast = datetime.now(timezone.utc).astimezone(SAST_TZ)
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", time_str.strip().lower())
        if not m:
            raise ValueError(f"Unable to parse time string: '{time_str}'")
        hr = int(m.group(1))
        mn = int(m.group(2) or 0)
        meridiem = m.group(3)
        if meridiem == "pm" and hr < 12:
            hr += 12
        elif meridiem == "am" and hr == 12:
            hr = 0
        
        target_date = now_sast.date()
        if date_str.lower() in ["tomorrow", "next_day"]:
            target_date += timedelta(days=1)
        
        target_sast_dt = datetime(target_date.year, target_date.month, target_date.day, hr, mn, 0, tzinfo=SAST_TZ)
        target_utc_dt = target_sast_dt.astimezone(timezone.utc)
        remind_utc_dt = target_utc_dt - timedelta(minutes=5)
        parsed = {
            "target_sast_str": target_sast_dt.strftime("%Y-%m-%d %H:%M:%S SAST"),
            "target_utc_iso": target_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "remind_utc_iso": remind_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "minutes_until_target": round((target_utc_dt - datetime.now(timezone.utc)).total_seconds() / 60, 1),
            "seconds_until_remind": max(0, int((remind_utc_dt - datetime.now(timezone.utc)).total_seconds()))
        }

    conn = get_db_connection()
    c = conn.cursor()
    
    # Check if duplicate already pending for this source_ref or phone+target
    if source_ref:
        c.execute("SELECT id FROM scheduled_reminders WHERE source_ref = ? AND status = 'PENDING'", (source_ref,))
        existing = c.fetchone()
        if existing:
            conn.close()
            return {"success": True, "id": existing[0], "status": "ALREADY_SCHEDULED", **parsed}

    c.execute("""
    INSERT INTO scheduled_reminders (
        customer_name, customer_phone, vehicle_model, source, source_ref,
        requested_time_raw, target_time_utc, target_time_sast, remind_at_utc,
        status, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
    """, (
        name or "Customer",
        phone,
        vehicle or "Nissan Range",
        source,
        source_ref or f"cmd_{int(datetime.now().timestamp())}",
        time_str,
        parsed["target_utc_iso"],
        parsed["target_sast_str"],
        parsed["remind_utc_iso"],
        notes or f"Scheduled callback for {name} ({mask_phone(phone)}) at {time_str}"
    ))
    reminder_id = c.lastrowid
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "id": reminder_id,
        "customer_name": name,
        "customer_phone": mask_phone(phone),
        "vehicle_model": vehicle,
        "target_sast": parsed["target_sast_str"],
        "remind_at_utc": parsed["remind_utc_iso"],
        "minutes_remaining": parsed["minutes_until_target"],
        "status": "PENDING"
    }

def format_reminder_card(reminder: dict) -> str:
    """
    Constructs the exact callback reminder card matching Jakes' inbound lead notification structure.
    """
    name = reminder.get("customer_name") or "Customer"
    phone = reminder.get("customer_phone") or "Not Listed"
    masked = mask_phone(phone)
    target_sast = reminder.get("target_time_sast") or ""
    time_display = target_sast.split()[1][:5] if " " in target_sast else target_sast
    vehicle = reminder.get("vehicle_model") or "Nissan Range"
    notes = reminder.get("notes") or "Scheduled callback window is opening in 5 minutes."

    card = (
        f"🚨 *Callback Reminder*\n\n"
        f"*Customer:* {name}\n"
        f"*Phone:* {masked} ({phone})\n"
        f"*Time:* {time_display} SAST (in 5 minutes)\n"
        f"*Vehicle:* {vehicle}\n\n"
        f"_{notes}_"
    )
    return card

def send_whatsapp_card(reminder: dict) -> dict:
    """
    Dispatches reminder card directly to Jakes (+27 82 739 8595) via the JAX WhatsApp Monitor bridge.
    """
    card_text = format_reminder_card(reminder)
    payload = {
        "phone": JAKES_WHATSAPP_PHONE,
        "message": card_text,
        "authorizedBy": "jakes_callback_reminder"
    }
    try:
        r = requests.post(BRIDGE_API_URL, json=payload, timeout=12)
        return r.json()
    except Exception as e:
        return {"success": False, "error": str(e)}

def check_and_dispatch_due_reminders() -> list:
    """
    Polls scheduled_reminders table for pending reminders where remind_at_utc <= current_time_utc.
    Dispatches WhatsApp cards and marks status as SENT.
    """
    init_reminders_table()
    now_utc_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
    SELECT * FROM scheduled_reminders
    WHERE status = 'PENDING' AND remind_at_utc <= ?
    ORDER BY remind_at_utc ASC
    """, (now_utc_iso,))
    
    due_rows = [dict(r) for r in c.fetchall()]
    dispatched = []
    
    for row in due_rows:
        res = send_whatsapp_card(row)
        if res.get("success"):
            c.execute("UPDATE scheduled_reminders SET status = 'SENT', sent_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            conn.commit()
            dispatched.append({"id": row["id"], "customer": row["customer_name"], "status": "SENT", "res": res})
        else:
            # Check if socket disconnected
            c.execute("UPDATE scheduled_reminders SET status = 'FAILED' WHERE id = ?", (row["id"],))
            conn.commit()
            dispatched.append({"id": row["id"], "customer": row["customer_name"], "status": "FAILED", "error": res.get("error")})
            
    conn.close()
    return dispatched

def list_pending_reminders() -> list:
    init_reminders_table()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
    SELECT id, customer_name, customer_phone, vehicle_model, source, target_time_sast, remind_at_utc, status, notes
    FROM scheduled_reminders
    WHERE status = 'PENDING'
    ORDER BY target_time_utc ASC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    
    now_utc = datetime.now(timezone.utc)
    for r in rows:
        try:
            remind_dt = datetime.strptime(r["remind_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            r["seconds_until_remind"] = int((remind_dt - now_utc).total_seconds())
            r["minutes_until_remind"] = round(r["seconds_until_remind"] / 60, 1)
        except Exception:
            r["minutes_until_remind"] = "N/A"
    return rows

def scan_autohub_notes_for_callbacks() -> list:
    """
    Scans autoHUB prospect_notes table for any customer notes containing callback requests
    and schedules reminders if not already scheduled.
    """
    if not os.path.exists(AUTOHUB_DB_PATH):
        return []
    
    conn_crm = sqlite3.connect(AUTOHUB_DB_PATH)
    conn_crm.row_factory = sqlite3.Row
    c_crm = conn_crm.cursor()
    
    c_crm.execute("""
    SELECT n.id, n.custid, n.entry_date, n.note, p.name, p.phone, p.vehicle_model
    FROM prospect_notes n
    LEFT JOIN prospects p ON n.custid = p.custid
    WHERE n.note LIKE '%call%' OR n.note LIKE '%bel%' OR n.note LIKE '%skakel%' OR n.note LIKE '%time%' OR n.note LIKE '%pm%' OR n.note LIKE '%am%'
    ORDER BY n.id DESC LIMIT 50
    """)
    notes = c_crm.fetchall()
    conn_crm.close()
    
    scheduled = []
    for n in notes:
        text = n["note"]
        parsed = parse_callback_intent_and_time(text)
        if parsed:
            name = n["name"] or parsed.get("extracted_name") or "autoHUB Prospect"
            phone = n["phone"] or parsed.get("extracted_phone") or ""
            vehicle = n["vehicle_model"] or "Nissan Range"
            source_ref = f"autohub_note_{n['id']}"
            
            res = schedule_reminder(
                time_str=parsed["raw_time_str"],
                name=name,
                phone=phone,
                vehicle=vehicle,
                notes=text,
                source="autohub_note",
                source_ref=source_ref
            )
            if res.get("status") != "ALREADY_SCHEDULED":
                scheduled.append(res)
    return scheduled

def scan_whatsapp_messages_for_callbacks() -> list:
    """
    Scans inbound WhatsApp messages for callback requests and schedules reminders.
    """
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
    SELECT m.id, m.phone_number, m.sender_name, m.content, p.name as prospect_name
    FROM messages m
    LEFT JOIN prospects p ON m.phone_number = p.phone_number
    WHERE m.from_me = 0
    ORDER BY m.timestamp DESC LIMIT 60
    """)
    msgs = c.fetchall()
    conn.close()
    
    scheduled = []
    CREATOR_IDENTIFIERS = {'112528730407032', '112730778411197', '27793950395', '27847398595', '27827398595', '81647965864013'}
    for m in msgs:
        phone = m["phone_number"] or ""
        clean_p = re.sub(r"[^0-9]", "", phone)
        if clean_p in CREATOR_IDENTIFIERS or any(cid in clean_p for cid in CREATOR_IDENTIFIERS):
            continue
        text = m["content"] or ""
        parsed = parse_callback_intent_and_time(text)
        if parsed:
            name = m["prospect_name"] or m["sender_name"] or parsed.get("extracted_name") or "WhatsApp Customer"
            phone = m["phone_number"] or ""
            source_ref = f"wa_msg_{m['id']}"
            
            res = schedule_reminder(
                time_str=parsed["raw_time_str"],
                name=name,
                phone=phone,
                vehicle="Nissan Range",
                notes=f"Inbound WhatsApp callback request: \"{text}\"",
                source="whatsapp_inbound",
                source_ref=source_ref
            )
            if res.get("status") != "ALREADY_SCHEDULED":
                scheduled.append(res)
    return scheduled

def main():
    parser = argparse.ArgumentParser(description="Dealership OS Scheduled Reminders Engine")
    parser.add_argument("--schedule", "-s", action="store_true", help="Schedule a new reminder")
    parser.add_argument("--time", "-t", help="Target time string (e.g. '12:30 PM', '13:00', '09:30am')")
    parser.add_argument("--date", "-d", default="today", help="Target date ('today', 'tomorrow', 'DD/MM/YYYY')")
    parser.add_argument("--name", "-n", default="", help="Customer name")
    parser.add_argument("--phone", "-p", default="", help="Customer phone number")
    parser.add_argument("--topic", help="Topic or vehicle model")
    parser.add_argument("--notes", help="Detailed notes or context")
    parser.add_argument("--source", default="command", help="Source: command, whatsapp_inbound, autohub_note")
    parser.add_argument("--source-ref", help="Unique reference identifier")
    
    parser.add_argument("--list", "-l", action="store_true", help="List all pending reminders")
    parser.add_argument("--check-due", action="store_true", help="Check and dispatch due reminders right now")
    parser.add_argument("--parse", help="Test natural language callback and time parser on arbitrary text")
    parser.add_argument("--scan-notes", action="store_true", help="Scan autoHUB CRM notes for callback mentions")
    parser.add_argument("--scan-messages", action="store_true", help="Scan WhatsApp inbound messages for callback mentions")
    parser.add_argument("--cancel", type=int, help="Cancel pending reminder by ID")
    parser.add_argument("--send-whatsapp", action="store_true", help="Send WhatsApp alert immediately (for testing)")
    
    args = parser.parse_args()

    if args.parse:
        res = parse_callback_intent_and_time(args.parse)
        if res:
            print("✅ Callback Detected:")
            print(f"• Raw Text: {res['raw_text']}")
            print(f"• Extracted Time: {res['raw_time_str']}")
            print(f"• Target SAST: {res['target_sast_str']}")
            print(f"• Remind UTC (5m prior): {res['remind_utc_iso']}")
            print(f"• Minutes until target: {res['minutes_until_target']} min")
            print(f"• Seconds until remind: {res['seconds_until_remind']} s")
            print(f"• Due Now: {res['is_due_now']}")
            if res.get("extracted_name"):
                print(f"• Extracted Name: {res['extracted_name']}")
            if res.get("extracted_phone"):
                print(f"• Extracted Phone: {res['extracted_phone']}")
        else:
            print(f"❌ No callback intent or time detected in: \"{args.parse}\"")
        return

    if args.list:
        pending = list_pending_reminders()
        print(f"\n📋 Active Scheduled Reminders ({len(pending)} pending):")
        print("=" * 70)
        if not pending:
            print("No pending reminders.")
        for p in pending:
            print(f"[{p['id']}] 👤 {p['customer_name']} | 📞 {mask_phone(p['customer_phone'])}")
            print(f"     ⏰ Target: {p['target_time_sast']} (Remind in {p['minutes_until_remind']} min)")
            print(f"     🚗 Vehicle: {p['vehicle_model']} | 🏷️ Source: {p['source']}")
            print(f"     📝 Notes: {p['notes']}")
            print("-" * 70)
        return

    if args.check_due:
        dispatched = check_and_dispatch_due_reminders()
        print(f"Processed due reminders: {len(dispatched)} dispatched.")
        for d in dispatched:
            print(f"• [{d['id']}] {d['customer']}: {d['status']}")
        return

    if args.scan_notes:
        found = scan_autohub_notes_for_callbacks()
        print(f"autoHUB Note Scan complete. Scheduled {len(found)} new reminders.")
        for f in found:
            print(f"• [{f.get('id')}] {f.get('customer_name')} at {f.get('target_sast')}")
        return

    if args.scan_messages:
        found = scan_whatsapp_messages_for_callbacks()
        print(f"WhatsApp Message Scan complete. Scheduled {len(found)} new reminders.")
        for f in found:
            print(f"• [{f.get('id')}] {f.get('customer_name')} at {f.get('target_sast')}")
        return

    if args.cancel:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE scheduled_reminders SET status = 'CANCELLED' WHERE id = ?", (args.cancel,))
        conn.commit()
        conn.close()
        print(f"Reminder [{args.cancel}] cancelled.")
        return

    if args.schedule or (args.time and not args.list and not args.check_due):
        if not args.time and args.notes:
            # Auto-parse from notes if time not specified
            parsed = parse_callback_intent_and_time(args.notes)
            if parsed:
                args.time = parsed["raw_time_str"]
                if not args.name and parsed.get("extracted_name"):
                    args.name = parsed["extracted_name"]
                if not args.phone and parsed.get("extracted_phone"):
                    args.phone = parsed["extracted_phone"]
        
        if not args.time:
            print("❌ Error: --time is required to schedule a reminder.", file=sys.stderr)
            sys.exit(1)

        res = schedule_reminder(
            time_str=args.time,
            date_str=args.date,
            name=args.name,
            phone=args.phone,
            vehicle=args.topic or "Nissan Range",
            notes=args.notes or "",
            source=args.source,
            source_ref=args.source_ref
        )
        print("⏰ Scheduled Reminder Successfully Created:")
        print(f"• ID: {res.get('id')}")
        print(f"• Customer: {res.get('customer_name')}")
        print(f"• Phone: {res.get('customer_phone')}")
        print(f"• Target SAST Time: {res.get('target_sast')}")
        print(f"• Remind At (5 min prior): {res.get('remind_at_utc')}")
        print(f"• Minutes Remaining: {res.get('minutes_remaining')} min")
        print(f"• Status: {res.get('status')}")
        
        if args.send_whatsapp:
            print(f"\n📲 Sending immediate test notification to Jakes (+27 82 739 8595)...")
            send_res = send_whatsapp_card({
                "customer_name": res.get("customer_name"),
                "customer_phone": args.phone,
                "target_time_sast": res.get("target_sast"),
                "vehicle_model": args.topic,
                "notes": args.notes or "Scheduled callback reminder."
            })
            print(f"WhatsApp Dispatch Result: {send_res}")
        return

    parser.print_help()

if __name__ == "__main__":
    main()
