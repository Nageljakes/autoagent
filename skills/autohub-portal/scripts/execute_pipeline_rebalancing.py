#!/usr/bin/env python3
"""
execute_pipeline_rebalancing.py - Live Execution of Diary Management & Fluff Sweeping
Moves Saturday entries to Monday, re-engages overdue working prospects, and relegates fluff to Friday.
"""

import sys
import os
import re
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

try:
    from portal_login import login, load_credentials_from_env_file, get_base_url
except ImportError:
    from nissan_login import login, load_credentials_from_env_file
    get_base_url = lambda: "https://egm.auto-hub.co.za"

def sanitize_dashes(text: str) -> str:
    if not text:
        return text
    return re.sub(r"[\u2014\u2013\u2015]", "-", text)

def fast_move_entry(session, sg, custid, name, target_date_str, target_day, target_month, target_year, current_purpose="Follow up regarding interest"):
    base_url = get_base_url() or "https://egm.auto-hub.co.za"
    url_add = f"{base_url}/index.cfm?page=pages/adddiaryentry.cfm&custid={custid}&sg={sg}"
    try:
        r_add = session.get(url_add, timeout=12)
        soup_add = BeautifulSoup(r_add.text, "html.parser")

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
            return False, f"Could not find action form for {name} ({custid})"

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

        r_post = session.post(followup_url, data=payload, headers=headers, allow_redirects=True, timeout=15)
        if "An error has occured" in r_post.text or ("Oops!" in r_post.text and "followup3" not in r_post.url and "entries" not in r_post.url):
            return False, f"Failed to reschedule: Portal rejected form submission"

        return True, f"Successfully rescheduled {name} ({custid}) to {target_date_str}"
    except Exception as e:
        return False, f"Exception moving {name} ({custid}): {str(e)}"
