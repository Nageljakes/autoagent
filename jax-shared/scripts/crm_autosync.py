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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.dirname(SCRIPT_DIR)
from database_paths import PROSPECT_HISTORY_DB, SQLITE_DB_PATH as PROSPECTS_DB
CRM_SYNC_JSON = os.environ.get("CRM_SYNC_JSON", os.path.join(SHARED_DIR, "data", "crm_sync.json"))
SALESPERSON_NAME = os.environ.get("SALESPERSON_NAME", "Salesperson")

def main(sync_remote=True):
    conn = sqlite3.connect(PROSPECT_HISTORY_DB)
    c = conn.cursor()

    conn_wa = sqlite3.connect(PROSPECTS_DB)
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
                sender = SALESPERSON_NAME if from_me else name
                # Truncate content to 60 chars
                snippet = content[:60] + "..." if len(content) > 60 else content
                wa_snapshot = f"{sender}: {snippet}"

        leads.append({
            "id": generate_id(custid),
            "agent": SALESPERSON_NAME,
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
            SALESPERSON_NAME: {"quizzes": [], "attendance": [], "appraisals": [], "notes": []}
        }
    }

    os.makedirs(os.path.dirname(os.path.abspath(CRM_SYNC_JSON)), exist_ok=True)
    with open(CRM_SYNC_JSON, 'w') as f:
        json.dump(output, f, indent=2)

    conn.close()
    conn_wa.close()
    print(f"Created {CRM_SYNC_JSON}")

    import subprocess

    CRM_GIST_ID = os.environ.get("CRM_GIST_ID", "")
    if sync_remote and CRM_GIST_ID:
        try:
            print("Pushing updates to CRM Gist...")
            env = os.environ.copy()
            subprocess.run(["gh", "gist", "edit", CRM_GIST_ID, "-f", "crm_sync.json", CRM_SYNC_JSON], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("Successfully synced to remote CRM storage.")
        except Exception as e:
            print("Failed to sync to remote CRM:", e)
    else:
        print("CRM_GIST_ID not set, skipping remote sync.")


if __name__ == "__main__":
    main()
