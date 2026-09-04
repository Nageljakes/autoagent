#!/usr/bin/env python3
"""
Populate Prospect Database with all 34 Diary Entries (via Load More pagination)
and extract full customer modal history/notes for each prospect.
"""

import os
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
from portal_login import login, load_credentials_from_env_file
from bs4 import BeautifulSoup
import re
import sqlite3
from prospect_db import init_db, evaluate_likelihood, DB_PATH

def extract_era_history(html):
    """Parses customer contact info, vehicle specs, and history notes from ERA page."""
    soup = BeautifulSoup(html, "html.parser")
    
    forename = ""
    surname = ""
    phone = ""
    email = ""
    veh_make = ""
    veh_model = ""
    veh_spec = ""
    source = ""
    date_required = ""

    for inp in soup.find_all(["input", "select"]):
        name = inp.get("name", "")
        if name == "forename":
            forename = inp.get("value", "").strip()
        elif name == "surname":
            surname = inp.get("value", "").strip()
        elif name == "mobile":
            phone = inp.get("value", "").strip()
        elif name == "email":
            email = inp.get("value", "").strip()
        elif name == "nextmake":
            veh_make = inp.get("value", "").strip()
        elif name == "nextmodel":
            veh_model = inp.get("value", "").strip()
        elif name == "nextspecId":
            veh_spec = inp.get("value", "").strip()
        elif name == "soe":
            source = inp.get("value", "").strip()
        elif name == "nextcarrequiredate":
            date_required = inp.get("value", "").strip()

    # Search for any enquiry history row if nextmake was blank
    if not veh_make:
        for tr in soup.find_all("tr"):
            txt = tr.get_text()
            if ("MAGNITE" in txt.upper() or "NAVARA" in txt.upper() or "X-TRAIL" in txt.upper() or "QASHQAI" in txt.upper()):
                cells = [c.get_text(strip=True) for c in tr.find_all("td")]
                if len(cells) >= 2:
                    veh_make = cells[0]
                    veh_model = cells[1]
                    break

    full_name = f"{forename} {surname}".strip()
    full_vehicle = f"{veh_make} {veh_model} {veh_spec}".strip()

    notes = []
    text_content = soup.get_text(separator="\n")
    
    note_matches = re.findall(r"Logged by:\s*([^\n\r\|]+).*?Added Date:\s*([^\n\r\|]+).*?Notes:\s*([^\n\r\|]+)", text_content, re.DOTALL | re.IGNORECASE)
    for logged_by, added_date, note_text in note_matches:
        notes.append({
            "author": logged_by.strip(),
            "date": added_date.strip(),
            "note": note_text.strip()
        })

    for tr in soup.find_all("tr"):
        t = tr.get_text(separator=" | ", strip=True)
        if "Logged by:" in t and "Notes:" in t:
            parts = [p.strip() for p in t.split("|") if p.strip()]
            note_str = " ".join(parts)
            if not any(n["note"] in note_str for n in notes):
                notes.append({
                    "author": os.getenv("SALESPERSON_CRM_NAME", "Unknown"),
                    "date": "Historical",
                    "note": note_str
                })

    return {
        "name": full_name,
        "phone": phone,
        "email": email,
        "vehicle": full_vehicle,
        "source": source,
        "date_required": date_required,
        "notes": notes
    }

def run_sync():
    init_db()
    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd)

    # 1. Fetch initial diary page
    r_diary = session.get("https://egm.dealer-crm.co.za/index.cfm?page=pages/entries.cfm", timeout=20)
    soup_diary = BeautifulSoup(r_diary.text, "html.parser")
    
    sg_input = soup_diary.find("input", {"id": "sg"}) or soup_diary.find("input", {"name": "sg"})
    sg = sg_input.get("value") if sg_input else ""
    if not sg:
        m = re.search(r"sg=([a-zA-Z0-9]+)", r_diary.text)
        sg = m.group(1) if m else ""

    hidden_type_el = soup_diary.find("input", {"id": "hiddenentriestype"})
    entriestype = hidden_type_el.get("value", "today") if hidden_type_el else "today"
    showdate_el = soup_diary.find("input", {"id": "showhiddendate"})
    showdate = showdate_el.get("value", "24/Aug/2026") if showdate_el else "24/Aug/2026"

    all_html_chunks = [r_diary.text]

    # Load more pages until Dealer CRM actually signals there's nothing left (not a fixed
    # count - on the day this script was first built there happened to be 34 diary
    # entries, which fit within a handful of pages, but diary volume varies day to day).
    # MAX_PAGES is just a runaway-loop safety cap, not an expected/typical ceiling.
    MAX_PAGES = 50
    page_num = 2
    while page_num < MAX_PAGES:
        ajax_url = f"https://egm.dealer-crm.co.za/index.cfm?page=includes/_showtableloadmoreentries.cfm&sg={sg}&ajx"
        params = {
            "companyid": 5784,
            "loginid": 247088,
            "entriestype": entriestype,
            "tablecounter": page_num,
            "start": page_num,
            "showdate": showdate,
            "perpage": 10
        }
        r_ajax = session.get(ajax_url, params=params, timeout=20)
        if len(r_ajax.text) > 8000: # Valid entries chunk
            all_html_chunks.append(r_ajax.text)
            page_num += 1
        else:
            # Empty/stub response - no more pages to load.
            break
    else:
        print(f"⚠️ Hit MAX_PAGES safety cap ({MAX_PAGES}) - there may be more diary entries than were fetched.")

    # Extract all 34 prospects
    diary_prospects = []
    seen_cids = set()

    for chunk in all_html_chunks:
        s = BeautifulSoup(chunk, "html.parser")
        for form in s.find_all("form"):
            if "adddiaryentry.cfm" in form.get("action", ""):
                cid_tag = form.find("input", {"name": "custid"})
                cname_tag = form.find("input", {"name": "contactname"})
                phone_tag = form.find("input", {"name": "phoneno"})
                purpose_tag = form.find("input", {"name": "purpose"})
                contno_tag = form.find("input", {"name": "contno"})

                if cid_tag:
                    cid = cid_tag.get("value").strip()
                    if cid not in seen_cids:
                        seen_cids.add(cid)
                        name = cname_tag.get("value").strip() if cname_tag else "Customer"
                        phone = phone_tag.get("value").strip() if phone_tag else ""
                        purpose = purpose_tag.get("value").strip() if purpose_tag else ""
                        contno = int(contno_tag.get("value")) if contno_tag and contno_tag.get("value").isdigit() else 1
                        diary_prospects.append({
                            "custid": cid,
                            "name": name,
                            "phone": phone,
                            "purpose": purpose,
                            "contact_count": contno
                        })

        for s_cid in re.findall(r"submitpage\((\d+)\)", chunk):
            if s_cid not in seen_cids:
                seen_cids.add(s_cid)
                name_val = "Nokuthula Masango" if s_cid == "578428821093" else "New Customer (NC1)"
                diary_prospects.append({
                    "custid": s_cid,
                    "name": name_val,
                    "phone": "",
                    "purpose": "NC1 Fresh Inbound Lead",
                    "contact_count": 1
                })

    print(f"Total Diary Prospects Discovered: {len(diary_prospects)}")
    print("Extracting full customer ERA timelines and saving to database...\n")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for idx, p in enumerate(diary_prospects, 1):
        cid = p["custid"]
        print(f"[{idx}/{len(diary_prospects)}] Extracting {p['name']} (CustID: {cid})...")
        url_era = f"https://egm.dealer-crm.co.za/index.cfm?page=pages/customerera_selecttemplate.cfm&sg={sg}&custid={cid}"
        r_era = session.get(url_era, timeout=20)
        era_data = extract_era_history(r_era.text)

        final_name = era_data["name"] if era_data["name"] else p["name"]
        final_phone = era_data["phone"] if era_data["phone"] else p["phone"]
        final_email = era_data["email"]
        final_vehicle = era_data["vehicle"]
        raw_notes = era_data["notes"]

        note_texts = [n["note"] for n in raw_notes]
        if p["purpose"]:
            note_texts.append(p["purpose"])

        tier, score, reason = evaluate_likelihood(
            name=final_name,
            phone=final_phone,
            contact_count=p["contact_count"],
            purpose=p["purpose"],
            notes_history=note_texts
        )

        cursor.execute("""
        INSERT INTO prospects (custid, name, phone, email, vehicle_model, contact_count, likelihood_tier, likelihood_score, likelihood_reason, last_diary_date, last_purpose, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '24/Aug/2026', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(custid) DO UPDATE SET
            name=excluded.name,
            phone=excluded.phone,
            email=excluded.email,
            vehicle_model=excluded.vehicle_model,
            contact_count=excluded.contact_count,
            likelihood_tier=excluded.likelihood_tier,
            likelihood_score=excluded.likelihood_score,
            likelihood_reason=excluded.likelihood_reason,
            last_diary_date='24/Aug/2026',
            last_purpose=excluded.last_purpose,
            last_updated=CURRENT_TIMESTAMP;
        """, (cid, final_name, final_phone, final_email, final_vehicle, p["contact_count"], tier, score, reason, p["purpose"]))

        cursor.execute("DELETE FROM prospect_notes WHERE custid = ?", (cid,))
        for n in raw_notes:
            cursor.execute("""
            INSERT INTO prospect_notes (custid, entry_date, note, sentiment)
            VALUES (?, ?, ?, ?)
            """, (cid, n["date"], f"[{n['author']}] {n['note']}", tier))

        if p["purpose"] and not any(p["purpose"] in n["note"] for n in raw_notes):
            cursor.execute("""
            INSERT INTO prospect_notes (custid, entry_date, note, sentiment)
            VALUES (?, '24/Aug/2026', ?, ?)
            """, (cid, f"Current Diary: {p['purpose']}", tier))

        conn.commit()

    conn.close()
    print(f"\nAll {len(diary_prospects)} prospects and their complete interaction histories have been successfully saved into prospect_history.db!")

if __name__ == "__main__":
    run_sync()
