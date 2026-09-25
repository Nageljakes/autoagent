#!/usr/bin/env python3
"""
move_diary_entries.py - Batch reschedule diary follow-ups with zero-remaining sweep and high-intent guardrails.
"""

import sys
import os
import re
import argparse
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urljoin
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

try:
    from portal_login import get_base_url, login, load_credentials_from_env_file
except ImportError:
    from nissan_login import login, load_credentials_from_env_file
    get_base_url = lambda: "https://egm.auto-hub.co.za"

try:
    from prospect_db import DB_PATH
except ImportError:
    DB_PATH = os.getenv("CRM_DB_PATH", os.path.expanduser("~/.gemini/antigravity-cli/scratch/prospect_history.db"))

def is_warm_or_finance_lead(custid: str, purpose: str = "", name: str = "") -> bool:
    """
    Checks if a lead is an active finance applicant or high-intent warm lead.
    Warm/finance leads must always be rescheduled to next business day, never distant sweeps.
    """
    text = f"{purpose or ''} {name or ''}".lower()
    finance_keywords = ["finance", "application", "otp", "approved", "payslip", "bank statement", "pre-qual", "test drive", "appointment", "gryp"]
    if any(k in text for k in finance_keywords):
        return True

    if os.path.exists(DB_PATH):
        try:
            with sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT likelihood_score, last_purpose FROM prospects WHERE custid = ? OR (name = ? AND name != 'Customer')", (custid, name))
                row = cur.fetchone()
                if row:
                    score, last_purp = row[0] or 0, (row[1] or "").lower()
                    if score >= 60 or any(k in last_purp for k in finance_keywords):
                        return True
        except Exception:
            pass
    return False

def parse_all_entries(html):
    """
    Extracts all diary entries using dual discovery:
    1. Standard ColdFusion form rows with custid input
    2. Action buttons using inline javascript onclick='submitpage(custid);'
    """
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    seen_ids = set()

    # 1. Parse standard form rows
    for form in soup.find_all("form"):
        action = form.get("action", "")
        if "adddiaryentry.cfm" in action:
            cid_tag = form.find("input", {"name": "custid"})
            name_tag = form.find("input", {"name": "contactname"})
            purp_tag = form.find("input", {"name": "purpose"})
            if cid_tag:
                cid = cid_tag.get("value", "").strip()
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    name = name_tag.get("value", "").strip() if name_tag else "Customer"
                    purpose = purp_tag.get("value", "").strip() if purp_tag else "Follow up regarding - - Interest"
                    entries.append({"custid": cid, "name": name, "purpose": purpose})

    # 2. Parse submitpage(custid) buttons
    submit_ids = re.findall(r'submitpage\((\d+)\)', html)
    for cid in submit_ids:
        if cid not in seen_ids:
            seen_ids.add(cid)
            entries.append({"custid": cid, "name": "Customer", "purpose": "Follow up regarding - - Interest"})

    return entries

def sanitize_dashes(text: str) -> str:
    """Replaces long dashes (em dash, en dash, horizontal bar) with standard short hyphens."""
    if not text:
        return text
    return re.sub(r"[\u2014\u2013\u2015]", "-", text)

def move_entry(session, custid, name, target_date_str, target_day, target_month, target_year, current_purpose="Follow up regarding - - Interest"):
    base_url = get_base_url() or "https://egm.auto-hub.co.za"
    # 1. Fetch fresh session key
    r_diary = session.get(f"{base_url}/index.cfm?page=pages/entries.cfm")
    soup_diary = BeautifulSoup(r_diary.text, "html.parser")
    sg_input = soup_diary.find("input", {"id": "sg"})
    sg = sg_input.get("value") if sg_input else ""

    print(f"Processing {name} (CustID: {custid})...")

    # 2. GET adddiaryentry.cfm
    url_add = f"{base_url}/index.cfm?page=pages/adddiaryentry.cfm&custid={custid}&sg={sg}"
    r_add = session.get(url_add)
    soup_add = BeautifulSoup(r_add.text, "html.parser")

    # Extract customer name if unknown
    if name == "Customer":
        for tag in soup_add.find_all(["span", "div", "p", "td"]):
            t = tag.get_text(strip=True)
            if t.startswith("Customer :") or t.startswith("Customer:"):
                name = t.split(":", 1)[1].strip()
                break

    nxt_input = soup_add.find("input", {"id": "nextvehicleid"})
    nextvehicleid = nxt_input.get("value", "") if nxt_input else ""

    form1 = soup_add.find("form", {"name": "form1"}) or soup_add.find("form", action=lambda a: a and "followup3" in a)
    if not form1:
        print(f"Could not find action form for {name} ({custid})")
        return False

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
        "purpnotes": sanitize_dashes(f"Rescheduled overdue diary follow-up. Previous purpose: {current_purpose}"),
        "appointmenttime": "",
        "hrs1": "00",
        "mns": "00",
        "hrs2": "00",
        "mns2": "00",
        "dateid": "1",
        "day": str(target_day),
        "month": str(target_month),
        "year": str(target_year),
        "sg": sg,
        "custid": custid,
    }

    headers = {
        "Origin": base_url,
        "Referer": url_add,
    }

    r_post = session.post(followup_url, data=payload, headers=headers, allow_redirects=True)
    if "An error has occured" in r_post.text or ("Oops!" in r_post.text and "followup3" not in r_post.url and "entries" not in r_post.url):
        print(f"Failed to move {name} ({custid}): error page returned")
        return False
    else:
        print(f"Successfully moved {name} ({custid}) to {target_date_str}")
        return True

def reschedule_all(target_date: datetime = None):
    now = datetime.now()
    if not target_date:
        target_date = now + timedelta(days=1)

    # Calculate following business day (1 day, or Monday if Friday/Saturday)
    if now.weekday() == 4:
        next_biz_dt = now + timedelta(days=3)
    elif now.weekday() == 5:
        next_biz_dt = now + timedelta(days=2)
    else:
        next_biz_dt = now + timedelta(days=1)

    next_biz_str = next_biz_dt.strftime("%d/%m/%Y")
    next_biz_day = str(next_biz_dt.day)
    next_biz_month = str(next_biz_dt.month)
    next_biz_year = str(next_biz_dt.year)

    target_date_str = target_date.strftime("%d/%m/%Y")
    target_day = str(target_date.day)
    target_month = str(target_date.month)
    target_year = str(target_date.year)

    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd)
    base_url = get_base_url() or "https://egm.auto-hub.co.za"

    iteration = 0
    total_moved = 0

    while True:
        print(f"\n--- Sweep Pass #{iteration} ---")
        r_diary = session.get(f"{base_url}/index.cfm?page=pages/entries.cfm")
        entries = parse_all_entries(r_diary.text)

        if not entries:
            print("No entries left to move. Complete!")
            break

        print(f"Discovered {len(entries)} entries in this pass.")
        for e in entries:
            # High-Intent & Finance Diary Rescheduling Guardrail:
            # If lead is warm or finance applicant and sweep target is > next business day, clamp to tomorrow/next business day!
            if is_warm_or_finance_lead(e["custid"], e["purpose"], e["name"]) and target_date > next_biz_dt:
                print(f"  [GUARDRAIL] {e['name']} ({e['custid']}) is a high-intent / finance lead! Clamping from {target_date_str} to following business day ({next_biz_str}).")
                entry_date_str = next_biz_str
                entry_day = next_biz_day
                entry_month = next_biz_month
                entry_year = next_biz_year
            else:
                entry_date_str = target_date_str
                entry_day = target_day
                entry_month = target_month
                entry_year = target_year

            ok = move_entry(session, e["custid"], e["name"], entry_date_str, entry_day, entry_month, entry_year, e["purpose"])
            if ok:
                total_moved += 1

        iteration += 1
        if iteration > 20:
            print("Reached safety limit of 20 iterations. Exiting loop.")
            break

    print(f"\nTotal entries moved: {total_moved}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Move all current diary entries to another date.")
    parser.add_argument("--date", help="Target date in DD/MM/YYYY format. Default is tomorrow (or next Monday if weekend).")
    args = parser.parse_args()

    target = None
    if args.date:
        target = datetime.strptime(args.date, "%d/%m/%Y")

    reschedule_all(target)
