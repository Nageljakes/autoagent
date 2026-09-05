#!/usr/bin/env python3
"""
action_prospect.py - Automatically log interaction notes and move diary follow-ups on Dealer CRM portal & local database.
"""

import sys
import os
import re
import argparse
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urljoin
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from portal_login import get_base_url, login, load_credentials_from_env_file
from prospect_db import init_db, upsert_prospect, DB_PATH
from customer_identity import lookup_customer, AmbiguousCustomerError

def find_prospect_in_db(query: str):
    return lookup_customer(DB_PATH, query)


def find_prospect_in_diary(session, query: str):
    """Searches active diary page HTML for a customer matching query."""
    r_diary = session.get((get_base_url() + "/index.cfm?page=pages/entries.cfm"), timeout=20)
    soup = BeautifulSoup(r_diary.text, "html.parser")
    
    if not query or not query.strip():
        raise ValueError("Customer query must not be empty")
    matches = {}
    for form in soup.find_all("form"):
        action = form.get("action", "")
        if "adddiaryentry.cfm" in action:
            cid_tag = form.find("input", {"name": "custid"})
            name_tag = form.find("input", {"name": "contactname"})
            phone_tag = form.find("input", {"name": "phoneno"})
            purpose_tag = form.find("input", {"name": "purpose"})
            contno_tag = form.find("input", {"name": "contno"})
            
            if cid_tag:
                cid = cid_tag.get("value", "").strip()
                name = name_tag.get("value", "").strip() if name_tag else ""
                phone = phone_tag.get("value", "").strip() if phone_tag else ""
                purpose = purpose_tag.get("value", "").strip() if purpose_tag else ""
                contno = int(contno_tag.get("value")) if contno_tag and contno_tag.get("value").isdigit() else 1
                
                if (query.lower() in name.lower()) or (query in phone) or (query == cid):
                    matches[cid] = {"custid": cid, "name": name, "phone": phone, "purpose": purpose, "contact_count": contno}
    if len(matches) > 1:
        raise AmbiguousCustomerError("Multiple diary customers match; specify the exact customer ID.")
    return next(iter(matches.values()), None)

def sanitize_dashes(text: str) -> str:
    """Replaces long dashes (em dash, en dash, horizontal bar) with standard short hyphens."""
    if not text:
        return text
    return re.sub(r"[\u2014\u2013\u2015]", "-", text)

def determine_purpose(note: str, fallback_purpose: str = "") -> str:
    """Generates a valid Dealer CRM purpose enum based on note context."""
    n = sanitize_dashes(note).lower()
    if "voicenote" in n or "whatsapp" in n or "replied" in n or "message" in n:
        return "Follow up regarding - - Follow up on WhatsApp reply"
    if fallback_purpose and fallback_purpose in ["Follow up regarding - - Interest", "Follow up regarding - - Follow up on WhatsApp reply", "Follow up regarding - - Follow up regarding interest", "Lead,follow up"]:
        return fallback_purpose
    return "Follow up regarding - - Interest"

def require_crm_confirmation(response):
    """Reject HTTP failures, login pages and explicit application failures."""
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"CRM update failed (HTTP {response.status_code})")
    text = response.text.lower()
    if any(marker in text for marker in ('an error has occur', 'oops!', 'type="password"', "type='password'")):
        raise RuntimeError("CRM returned an error or login page")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    def normalized(value):
        return value.strip().casefold() if isinstance(value, str) else value

    if isinstance(payload, dict):
        # Keep every field so conflicting capitalization cannot hide a failure.
        fields = [(str(key).strip().casefold(), normalized(value)) for key, value in payload.items()]
        failed = any(
            (key == "error" and value)
            or (key == "success" and value in (False, "false"))
            or (key == "status" and value in ("error", "failed"))
            for key, value in fields
        )
        if failed:
            raise RuntimeError("CRM rejected the update")
        confirmed = any(
            (key == "success" and (value is True or value == "true"))
            or (key == "status" and value in ("ok", "success"))
            for key, value in fields
        )
    else:
        payload = normalized(payload)
        if payload is False or payload == "false":
            raise RuntimeError("CRM rejected the update")
        confirmed = payload is True or payload == "true"
    if response.status_code != 200 and not confirmed:
        raise RuntimeError("CRM did not confirm the update")


def action_prospect(
    custid: str = None,
    query: str = None,
    note: str = "",
    purpose: str = "",
    target_date_str: str = None,
    days_ahead: int = 1,
    likelihood_score: int = None,
    likelihood_reason: str = None
) -> dict:
    """
    Logs note on Dealer CRM, reschedules diary follow-up, and updates SQLite prospect database.
    """
    # Strictly sanitize all input text so no long dash (em dash or en dash) ever enters Dealer CRM
    if note:
        note = sanitize_dashes(note)
    if purpose:
        purpose = sanitize_dashes(purpose)
    if likelihood_reason:
        likelihood_reason = sanitize_dashes(likelihood_reason)
    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd)

    # 1. Resolve prospect identity
    cust_info = None
    if custid:
        cust_info = find_prospect_in_db(custid)
    if not cust_info and query:
        cust_info = find_prospect_in_db(query)
        if not cust_info:
            cust_info = find_prospect_in_diary(session, query)

    if not cust_info:
        if custid:
            cust_info = {"custid": custid, "name": "Customer", "phone": "", "vehicle": "", "contact_count": 1}
        else:
            raise ValueError(f"Could not resolve prospect from query '{query or custid}'")

    cid = cust_info["custid"]
    raw_name = cust_info.get("name", "Customer")
    phone = cust_info.get("phone", "")
    vehicle = cust_info.get("vehicle", "")

    # Clean name and vehicle suffix
    clean_name = raw_name
    if "/" in raw_name or " - " in raw_name:
        parts = re.split(r"\s*[/\\-]\s*", raw_name)
        if len(parts) >= 2:
            clean_name = parts[0].strip()
            if not vehicle:
                vehicle = " ".join(parts[1:]).strip()

    name = clean_name
    contact_count = cust_info.get("contact_count", 1) + 1

    # If phone is empty, fetch mobile directly from Dealer CRM ERA
    if not phone and cid:
        try:
            url_era = f'{get_base_url()}/index.cfm?page=pages/customerera_selecttemplate.cfm&custid={cid}'
            r_era = session.get(url_era, timeout=15)
            soup_era = BeautifulSoup(r_era.text, "html.parser")
            mobile_inp = soup_era.find("input", {"name": "mobile"})
            if mobile_inp and mobile_inp.get("value", "").strip():
                phone = mobile_inp.get("value", "").strip()
        except Exception:
            pass

    # 2. Determine target date
    if target_date_str:
        try:
            target_dt = datetime.strptime(target_date_str, "%d/%m/%Y")
        except ValueError:
            target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
            target_date_str = target_dt.strftime("%d/%m/%Y")
    else:
        now = datetime.now()
        target_dt = now + timedelta(days=days_ahead)
        target_date_str = target_dt.strftime("%d/%m/%Y")

    # 3. Determine purpose
    if not purpose:
        purpose = determine_purpose(note, cust_info.get("purpose", ""))

    # 4. Fetch fresh session key (sg)
    r_diary = session.get((get_base_url() + "/index.cfm?page=pages/entries.cfm"), timeout=20)
    soup_diary = BeautifulSoup(r_diary.text, "html.parser")
    sg_input = soup_diary.find("input", {"id": "sg"}) or soup_diary.find("input", {"name": "sg"})
    sg = sg_input.get("value") if sg_input else ""
    if not sg:
        m = re.search(r"sg=([a-zA-Z0-9]+)", r_diary.text)
        sg = m.group(1) if m else ""

    # 5. GET adddiaryentry.cfm
    url_add = f'{get_base_url()}/index.cfm?page=pages/adddiaryentry.cfm&custid={cid}&sg={sg}'
    r_add = session.get(url_add, timeout=20)
    soup_add = BeautifulSoup(r_add.text, "html.parser")

    # Refresh customer name from header if generic
    if name in ["Customer", "New Customer (NC1)"]:
        for tag in soup_add.find_all(["span", "div", "p", "td", "h3", "h4"]):
            t = tag.get_text(strip=True)
            if t.startswith("Customer :") or t.startswith("Customer:"):
                name = t.split(":", 1)[1].strip()
                break

    nxt_input = soup_add.find("input", {"id": "nextvehicleid"}) or soup_add.find("input", {"name": "nextvehicleid"})
    nextvehicleid = nxt_input.get("value", "") if nxt_input else ""

    form1 = soup_add.find("form", {"name": "form1"}) or soup_add.find("form", action=lambda a: a and "followup3" in a)
    if not form1:
        raise RuntimeError(f"Could not locate action form for {name} (CustID: {cid})")

    followup_url = urljoin(r_add.url, form1["action"])

    payload = {
        "nextvehicleid": nextvehicleid,
        "choice": "t",
        "outintel": "1",
        "quoteval": "",
        "t": "1",
        "v": "0",
        "f": "0",
        "c": "0",
        "a": "0",
        "e": "0",
        "update_regno": "false",
        "nextdate2": target_date_str,
        "nextdate": target_date_str,
        "purposeid": "2",
        "exitpurpose": "Follow up regarding -",
        "purpnotes": sanitize_dashes((note or purpose)[:240]),
        "appointmenttime": "",
        "hrs1": "00",
        "mns": "00",
        "exitd": str(target_dt.day),
        "exitm": str(target_dt.month),
        "exity": str(target_dt.year),
        "custid": cid,
        "delivery": "0",
        "purpose": purpose,
        "nextreg": "",
        "vehicle_reportid": "0",
        "savebut": "SAVE"
    }

    headers = {
        "Origin": get_base_url(),
        "Referer": url_add,
    }

    r_post = session.post(followup_url, data=payload, headers=headers, allow_redirects=True, timeout=20)
    require_crm_confirmation(r_post)

    # Log the permanent note and confirm it before updating the local database.
    if note:
        url_cfc = get_base_url() + "/model/com/southafrica/customer/customer_sa.cfc"
        note_response = session.post(url_cfc, data={
            "method": "addCustomerNotes",
            "companyId": 5784,
            "custid": cid,
            "notes": note,
            "loginid": 247088
        }, timeout=15)
        require_crm_confirmation(note_response)

    # 7. Update local SQLite Database
    upsert_prospect(
        custid=cid,
        name=name,
        phone=phone,
        vehicle=vehicle,
        contact_count=contact_count,
        purpose=purpose,
        notes=[note] if note else [],
        last_date=target_date_str
    )

    # Optional manual score / reason override if provided
    if likelihood_score is not None:
        tier = "HIGH" if likelihood_score >= 75 else ("MEDIUM" if likelihood_score >= 45 else "LOW")
        reason = likelihood_reason or f"Updated via interaction: {note[:60]}"
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                UPDATE prospects
                SET likelihood_score = ?, likelihood_tier = ?, likelihood_reason = ?, last_updated = CURRENT_TIMESTAMP
                WHERE custid = ?
            """, (likelihood_score, tier, reason, cid))

    result = {
        "success": True,
        "custid": cid,
        "name": name,
        "phone": phone,
        "rescheduled_to": target_date_str,
        "purpose": purpose,
        "note_logged": note,
        "contact_count": contact_count
    }
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Action Prospect on Dealer CRM and Local DB")
    parser.add_argument("--custid", help="Customer ID on Dealer CRM")
    parser.add_argument("--query", "-q", help="Customer name or phone search query")
    parser.add_argument("--note", "-n", required=True, help="Interaction note to log")
    parser.add_argument("--purpose", "-p", help="Diary follow-up purpose")
    parser.add_argument("--date", "-d", help="Target rescheduled date (DD/MM/YYYY)")
    parser.add_argument("--days", type=int, default=1, help="Days ahead to reschedule (default: 1)")
    parser.add_argument("--score", type=int, help="Optional likelihood score override (0-100)")
    parser.add_argument("--reason", help="Optional likelihood reason explanation")

    args = parser.parse_args()

    if not args.custid and not args.query:
        print("Error: Either --custid or --query is required.")
        sys.exit(1)

    try:
        res = action_prospect(
            custid=args.custid,
            query=args.query,
            note=args.note,
            purpose=args.purpose,
            target_date_str=args.date,
            days_ahead=args.days,
            likelihood_score=args.score,
            likelihood_reason=args.reason
        )
        print(f"✅ Successfully updated {res['name']} (CustID: {res['custid']})")
        print(f"   Note Logged: {res['note_logged']}")
        print(f"   Purpose: {res['purpose']}")
        print(f"   Rescheduled Diary To: {res['rescheduled_to']}")
        # --- CRM AUTO-SYNC TRIGGER ---
        import subprocess
        try:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[3] / "jax-shared" / "scripts" / "crm_autosync.py")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    except Exception as e:
        print(f"❌ Failed to action prospect: {e}")
        sys.exit(1)

