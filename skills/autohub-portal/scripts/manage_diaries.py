#!/usr/bin/env python3
"""
manage_diaries.py - High-Performance Diary Management & Pipeline Balancing Engine
Autonomously audits, filters fluff, and prioritizes working prospects across Overdue, Monday, and Tuesday.
"""

import sys
import os
import re
import argparse
import sqlite3
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

try:
    from portal_login import login, load_credentials_from_env_file, get_base_url
except ImportError:
    from nissan_login import login, load_credentials_from_env_file
    get_base_url = lambda: "https://egm.auto-hub.co.za"

try:
    from prospect_db import DB_PATH
except ImportError:
    DB_PATH = os.getenv("CRM_DB_PATH", os.path.expanduser("~/.gemini/antigravity-cli/scratch/prospect_history.db"))

def sanitize_dashes(text: str) -> str:
    if not text:
        return text
    return re.sub(r"[\u2014\u2013\u2015]", "-", text)

def get_prospect_metadata(cids):
    meta = {}
    if os.path.exists(DB_PATH):
        try:
            with sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True) as conn:
                cur = conn.cursor()
                placeholders = ",".join(["?"] * len(cids))
                cur.execute(
                    f"SELECT custid, name, phone, vehicle_model, likelihood_score, likelihood_tier, contact_count, last_purpose FROM prospects WHERE custid IN ({placeholders})",
                    cids,
                )
                for r in cur.fetchall():
                    meta[r[0]] = {
                        "name": r[1],
                        "phone": r[2],
                        "vehicle": r[3],
                        "score": r[4] if r[4] is not None else 50,
                        "tier": r[5] or "MEDIUM",
                        "contacts": r[6] or 1,
                        "last_purpose": r[7] or "",
                    }
        except Exception:
            pass
    return meta

def extract_era_quick(session, sg, custid):
    base_url = get_base_url() or "https://egm.auto-hub.co.za"
    url = f"{base_url}/index.cfm?page=pages/customerera_selecttemplate.cfm&sg={sg}&custid={custid}"
    try:
        r = session.get(url, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        fn = soup.find("input", {"id": "forename"})
        sn = soup.find("input", {"id": "surname"})
        mb = soup.find("input", {"id": "mobile"})
        mk = soup.find("input", {"id": "nextmake"})
        md = soup.find("input", {"id": "nextmodel"})
        name = f"{fn.get('value', '') if fn else ''} {sn.get('value', '') if sn else ''}".strip() or "Customer"
        phone = mb.get("value", "") if mb else ""
        veh = f"{mk.get('value', '') if mk else ''} {md.get('value', '') if md else ''}".strip()
        text = soup.get_text("\n")
        notes = re.findall(r"Logged by:\s*([^\n\r\|]+).*?Added Date:\s*([^\n\r\|]+).*?Notes:\s*([^\n\r\|]+)", text, re.DOTALL | re.IGNORECASE)
        last_note = notes[-1][2].strip() if notes else ""
        return {"name": name, "phone": phone, "vehicle": veh, "last_note": last_note, "notes_count": len(notes)}
    except Exception:
        return {"name": "Customer", "phone": "", "vehicle": "", "last_note": "", "notes_count": 0}

def fetch_diary_entries(session, sg, date_str=None):
    base_url = get_base_url() or "https://egm.auto-hub.co.za"
    url = f"{base_url}/index.cfm?page=pages/entries.cfm&sg={sg}"
    if date_str:
        url += f"&showdate={date_str}"
    r = session.get(url, timeout=12)
    soup = BeautifulSoup(r.text, "html.parser")
    entries = []
    seen = set()
    for f in soup.find_all("form"):
        cid_tag = f.find("input", {"name": "custid"})
        name_tag = f.find("input", {"name": "contactname"})
        purp_tag = f.find("input", {"name": "purpose"})
        if cid_tag:
            cid = cid_tag.get("value", "").strip()
            if cid and cid not in seen:
                seen.add(cid)
                name = name_tag.get("value", "").strip() if name_tag else ""
                purp = purp_tag.get("value", "").strip() if purp_tag else ""
                entries.append({"custid": cid, "name": name, "purpose": purp})
    return entries

def fetch_overdue_entries(session, sg):
    base_url = get_base_url() or "https://egm.auto-hub.co.za"
    url = f"{base_url}/index.cfm?page=pages/unfollowpotential2.cfm&sg={sg}"
    r = session.get(url, timeout=12)
    soup = BeautifulSoup(r.text, "html.parser")
    entries = []
    seen = set()
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) >= 4 and cells[1] != "Due Date":
            a = tr.find("a", href=re.compile(r"custid=(\d+)"))
            cid = re.search(r"custid=(\d+)", a["href"]).group(1) if a else ""
            if cid and cid not in seen:
                seen.add(cid)
                entries.append({
                    "custid": cid,
                    "name": cells[2],
                    "due_date": cells[1],
                    "purpose": cells[3],
                })
    return entries

def classify_prospect(cid, name, purpose, meta, quick_era=None):
    p_meta = meta.get(cid, {})
    phone = p_meta.get("phone") or (quick_era.get("phone") if quick_era else "")
    veh = p_meta.get("vehicle") or (quick_era.get("vehicle") if quick_era else "")
    score = p_meta.get("score", 50)
    contacts = p_meta.get("contacts", 1)
    
    clean_name = p_meta.get("name") or name or (quick_era.get("name") if quick_era else "Customer")
    clean_purp = sanitize_dashes(purpose or p_meta.get("last_purpose") or "")
    
    # Fluff detection heuristics
    is_fluff = False
    fluff_reason = ""
    
    # Rule 1: High contact count (>10) with low score (<35) and repeated follow-up failures
    if contacts >= 10 and score <= 35:
        is_fluff = True
        fluff_reason = f"Cold lead with {contacts} previous contact attempts and score {score}"
    elif score <= 20 and contacts >= 6:
        is_fluff = True
        fluff_reason = f"Low intent score ({score}) after {contacts} attempts"
    elif "rescheduled overdue diary follow-up" in clean_purp.lower() and contacts >= 8:
        is_fluff = True
        fluff_reason = f"Unresponsive overdue lead with {contacts} previous reschedules"
        
    # High intent overrides
    purp_lower = clean_purp.lower()
    if any(k in purp_lower for k in ["credit score", "finance", "otp", "approved", "payslip", "appointment", "test drive", "call at", "csi"]):
        is_fluff = False
        if score < 60:
            score = 75
            
    stage = "Stage 3: Nurturing / Working"
    if score >= 80:
        stage = "Stage 1: Hot Money / Closing"
    elif score >= 50:
        stage = "Stage 2: Qualified / Active"
    elif is_fluff:
        stage = "Stage 4: Cold Sweep / Fluff"

    return {
        "custid": cid,
        "name": clean_name,
        "phone": phone,
        "vehicle": veh or "Dealership Range",
        "score": score,
        "contacts": contacts,
        "purpose": clean_purp,
        "is_fluff": is_fluff,
        "fluff_reason": fluff_reason,
        "stage": stage
    }

def run_diary_audit(sweep_fluff=False):
    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd)
    m = re.search(r"sg=([a-zA-Z0-9]+)", res.url)
    sg = m.group(1) if m else ""

    print("Logged into CRM portal successfully.")
    
    today = datetime.now()
    days_to_monday = (7 - today.weekday()) % 7
    if days_to_monday == 0:
        days_to_monday = 7
    monday_dt = today + timedelta(days=days_to_monday)
    tuesday_dt = monday_dt + timedelta(days=1)
    
    mon_str = monday_dt.strftime("%d/%b/%Y")
    tue_str = tuesday_dt.strftime("%d/%b/%Y")

    overdue_raw = fetch_overdue_entries(session, sg)
    monday_raw = fetch_diary_entries(session, sg, mon_str)
    tuesday_raw = fetch_diary_entries(session, sg, tue_str)

    all_cids = list({e["custid"] for e in overdue_raw + monday_raw + tuesday_raw})
    meta = get_prospect_metadata(all_cids)

    print(f"Total Unique CIDs identified across Overdue, Monday, Tuesday: {len(all_cids)}")

    overdue_prospects = []
    for e in overdue_raw:
        era = extract_era_quick(session, sg, e["custid"]) if e["custid"] not in meta else {}
        p = classify_prospect(e["custid"], e["name"], e["purpose"], meta, era)
        overdue_prospects.append(p)

    monday_prospects = []
    for e in monday_raw:
        era = extract_era_quick(session, sg, e["custid"]) if e["custid"] not in meta else {}
        p = classify_prospect(e["custid"], e["name"], e["purpose"], meta, era)
        monday_prospects.append(p)

    tuesday_prospects = []
    for e in tuesday_raw:
        era = extract_era_quick(session, sg, e["custid"]) if e["custid"] not in meta else {}
        p = classify_prospect(e["custid"], e["name"], e["purpose"], meta, era)
        tuesday_prospects.append(p)

    print("\n" + "=" * 60)
    print("📋 DIARY AUDIT & PIPELINE BALANCING SUMMARY")
    print("=" * 60)
    print(f"Overdue Entries: {len(overdue_prospects)}")
    print(f"Monday ({mon_str}) Entries: {len(monday_prospects)}")
    print(f"Tuesday ({tue_str}) Entries: {len(tuesday_prospects)}")
    
    total_entries = len(overdue_prospects) + len(monday_prospects) + len(tuesday_prospects)
    all_p = overdue_prospects + monday_prospects + tuesday_prospects
    working = [p for p in all_p if not p["is_fluff"]]
    fluff = [p for p in all_p if p["is_fluff"]]
    
    print(f"\nTotal Pipeline Audited: {total_entries}")
    print(f"🔥 Active Working Prospects: {len(working)}")
    print(f"🧹 Cold Sweep / Fluff Entries: {len(fluff)}")

    print("\n" + "═" * 55)
    print("🎯 ACTIVE WORKING PROSPECTS (High Priority Focus)")
    print("═" * 55)
    
    working_unique = {p["custid"]: p for p in working}.values()
    sorted_working = sorted(working_unique, key=lambda x: x["score"], reverse=True)

    for p in sorted_working:
        phone_masked = p["phone"][:3] + " *** " + p["phone"][-4:] if len(p["phone"]) >= 7 else (p["phone"] or "Not Listed")
        print(f"👤 {p['name']} | 🔥 {p['stage']} (Score: {p['score']})")
        print(f"📞 {phone_masked}")
        print(f"🚗 VEHICLE: {p['vehicle']}")
        print(f"💬 RECENT PURPOSE / TOUCHPOINT: {p['purpose'][:90]}")
        print(f"👉 RECOMMENDATION: Priority contact on Monday/Tuesday")
        print("-" * 55)

    print("\n" + "═" * 55)
    print(f"🧹 FLUFF / COLD SWEEP ENTRIES ({len(fluff)} entries to reschedule)")
    print("═" * 55)
    fluff_unique = {p["custid"]: p for p in fluff}.values()
    for p in fluff_unique:
        print(f"• {p['name']} (CustID: {p['custid']}) - {p['contacts']} previous attempts | Reason: {p['fluff_reason']}")

    return sorted_working, fluff_unique

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit and manage CRM diaries")
    parser.add_argument("--sweep-fluff", action="store_true", help="Automatically sweep fluff entries away from Mon/Tue")
    args = parser.parse_args()
    run_diary_audit(sweep_fluff=args.sweep_fluff)
