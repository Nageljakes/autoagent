import os
import sqlite3
import json
from datetime import datetime
import hashlib
import re

def generate_id(custid):
    return int(hashlib.md5(custid.encode()).hexdigest(), 16) % 1000000

def normalize_phone(p):
    if not p: return ""
    p = re.sub(r'\D', '', p)
    if p.startswith('0'):
        return '27' + p[1:]
    return p

db_prospect_path = os.getenv("PROSPECT_HISTORY_DB", os.path.expanduser("~/.gemini/antigravity-cli/scratch/prospect_history.db"))
conn = sqlite3.connect(db_prospect_path)
c = conn.cursor()

db_wa_path = os.getenv("SQLITE_DB_PATH", os.path.abspath(os.path.join(os.path.dirname(__file__), "../jax-shared/data/prospects.db")))
conn_wa = sqlite3.connect(db_wa_path)
c_wa = conn_wa.cursor()

leads = []
c.execute("SELECT custid, name, phone, vehicle_model, likelihood_tier, likelihood_score, last_diary_date, first_seen, status FROM prospects")
for row in c.fetchall():
    custid, name, phone, vehicle, tier, score, follow_up, first_seen, status = row
    
    # Map tier
    temp = 'Cold'
    if tier == 'HIGH': temp = 'Hot'
    elif tier == 'MEDIUM': temp = 'Warm'
    
    # Map stage
    stage = 'enquiry'
    if status == 'CLOSED': stage = 'lost'
    elif status == 'SOLD': stage = 'banked'
    
    # Fix follow_up format to YYYY-MM-DD
    followUpDate = None
    if follow_up:
        fu = follow_up.split('T')[0]
        if '/' in fu:
            parts = fu.split('/')
            if len(parts) == 3:
                followUpDate = f"{parts[2]}-{parts[1]}-{parts[0]}"
        else:
            followUpDate = fu

    # Get notes
    note_history = []
    c.execute("SELECT entry_date, note FROM prospect_notes WHERE custid = ? ORDER BY recorded_at ASC", (custid,))
    for ndate, ntext in c.fetchall():
        note_history.append({
            "date": ndate if ndate else datetime.now().isoformat(),
            "text": ntext
        })

    # WhatsApp Snapshot
    wa_snapshot = "No recent WA interaction."
    norm_phone = normalize_phone(phone)
    if norm_phone:
        c_wa.execute("SELECT content, from_me, timestamp FROM messages WHERE phone_number = ? OR phone_number = ? ORDER BY timestamp DESC LIMIT 1", (norm_phone, phone))
        res = c_wa.fetchone()
        if res:
            content, from_me, ts = res
            sender = "{SALESPERSON_NAME}" if from_me else name
            # Truncate content to 60 chars
            snippet = content[:60] + "..." if len(content) > 60 else content
            wa_snapshot = f"{sender}: {snippet}"
        
    leads.append({
        "id": generate_id(custid),
        "agent": "{SALESPERSON_NAME}",
        "customer": name or "Unknown",
        "phone": phone or "",
        "vehicle": vehicle or "",
        "source": "Dealer CRM",
        "temperature": temp,
        "stage": stage,
        "score": score or 0,
        "created": first_seen or datetime.now().isoformat(),
        "followUpDate": followUpDate,
        "noteHistory": note_history,
        "waSnapshot": wa_snapshot,
        "financeData": {},
        "quoteData": None,
        "otpData": None
    })

output = {
    "leads": leads,
    "agents": {
        "{SALESPERSON_NAME}": {"quizzes": [], "attendance": [], "appraisals": [], "notes": []}
    }
}

crm_sync_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "../jax-shared/data/crm_sync.json"))
with open(crm_sync_file, 'w') as f:
    json.dump(output, f, indent=2)

print(f"Created {crm_sync_file}")
