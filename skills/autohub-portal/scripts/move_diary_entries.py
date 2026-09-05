import os
#!/usr/bin/env python3
import sys
import re
import argparse
from datetime import datetime, timedelta
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from portal_login import get_base_url, login, load_credentials_from_env_file

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
    # 1. Fetch fresh session key
    r_diary = session.get(f"{get_base_url()}/index.cfm?page=pages/entries.cfm")
    soup_diary = BeautifulSoup(r_diary.text, "html.parser")
    sg_input = soup_diary.find("input", {"id": "sg"})
    sg = sg_input.get("value") if sg_input else ""

    print(f"Processing {name} (CustID: {custid})...")

    # 2. GET adddiaryentry.cfm
    url_add = f"{get_base_url()}/index.cfm?page=pages/adddiaryentry.cfm&custid={custid}&sg={sg}"
    r_add = session.get(url_add)
    soup_add = BeautifulSoup(r_add.text, "html.parser")

    # Find customer name from header if generic
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
        print(f"  [FAIL] Could not find action form for {name} ({custid})")
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
        "purpnotes": sanitize_dashes("Follow up regarding interest"),
        "appointmenttime": "",
        "hrs1": "00",
        "mns": "00",
        "exitd": target_day,
        "exitm": target_month,
        "exity": target_year,
        "custid": custid,
        "delivery": "0",
        "purpose": sanitize_dashes(current_purpose),
        "nextreg": "",
        "vehicle_reportid": "0",
        "savebut": "SAVE"
    }

    headers = {
        "Origin": get_base_url(),
        "Referer": url_add,
    }

    r_post = session.post(followup_url, data=payload, headers=headers, allow_redirects=True)
    is_err = "Oops!" in r_post.text or "An error has occured" in r_post.text
    if is_err:
        print(f"  [FAIL] Failed to update {name}")
        return False
    else:
        print(f"  [SUCCESS] Moved {name} to {target_date_str}!")
        return True

def reschedule_all(target_date: datetime = None):
    if not target_date:
        # Default to next Monday if today is Sat/Sun, or next day
        now = datetime.now()
        target_date = now + timedelta(days=1)

    target_date_str = target_date.strftime("%d/%m/%Y")
    target_day = str(target_date.day)
    target_month = str(target_date.month)
    target_year = str(target_date.year)

    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd)

    total_moved = 0
    iteration = 1

    while True:
        print(f"\n--- Sweep Pass #{iteration} ---")
        r_diary = session.get(f"{get_base_url()}/index.cfm?page=pages/entries.cfm")
        entries = parse_all_entries(r_diary.text)

        if not entries:
            print("Zero remaining entries found on diary page.")
            break

        print(f"Discovered {len(entries)} entries in this pass.")
        for e in entries:
            ok = move_entry(session, e["custid"], e["name"], target_date_str, target_day, target_month, target_year, e["purpose"])
            if ok:
                total_moved += 1

        iteration += 1
        if iteration > 10:  # safety bound
            print("Reached maximum sweep iterations.")
            break

    print(f"\nCompleted! Total entries rescheduled: {total_moved}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch Reschedule All Diary Entries")
    parser.add_argument("--date", help="Target date DD/MM/YYYY (defaults to next Monday / next business day)")
    args = parser.parse_args()

    target_dt = None
    if args.date:
        target_dt = datetime.strptime(args.date, "%d/%m/%Y")
    else:
        # Default next Monday
        today = datetime.now()
        days_ahead = 7 - today.weekday() if today.weekday() >= 5 else 1
        target_dt = today + timedelta(days=days_ahead)

    reschedule_all(target_dt)
