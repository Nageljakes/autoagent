#!/usr/bin/env python3
"""
accept_lead.py - Automatically accept unactioned/transferred leads on DMS/Dealer CRM portal,
and index them into the local SQLite prospect databases.
"""

import sys
import os
import re
import json
import argparse
from urllib.parse import urljoin
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from portal_login import get_base_url, login, load_credentials_from_env_file
from prospect_db import init_db, upsert_prospect, DB_PATH

def sanitize_dashes(text: str) -> str:
    """Replaces long dashes (em dash, en dash, horizontal bar) with standard short hyphens."""
    if not text:
        return text
    return re.sub(r"[\u2014\u2013\u2015]", "-", text)

def get_unactioned_leads(session):
    """Fetches inbox and returns list of unactioned leads."""
    inbox_url = (get_base_url() + "/index.cfm?page=pages/inbox.cfm")
    r = session.get(inbox_url, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    
    leads = []
    # Find links to viewleadcustomer.cfm
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "viewleadcustomer.cfm" in href and "ileadcustid=" in href:
            m = re.search(r"ileadcustid=(\d+)", href)
            if m:
                lead_id = m.group(1)
                row = a.find_parent("tr")
                name = ""
                cell = ""
                model = ""
                source = ""
                status = ""
                datetime_str = ""
                if row:
                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cols) >= 5:
                        name = cols[1] if len(cols) > 1 else ""
                        cell = cols[2] if len(cols) > 2 else ""
                        datetime_str = cols[3] if len(cols) > 3 else ""
                        model = cols[4] if len(cols) > 4 else ""
                        source = cols[5] if len(cols) > 5 else ""
                        status = cols[6] if len(cols) > 6 else ""
                
                if not any(l["ileadcustid"] == lead_id for l in leads):
                    leads.append({
                        "ileadcustid": lead_id,
                        "url": urljoin(r.url, href),
                        "name": name or a.get_text(strip=True),
                        "phone": cell,
                        "datetime": datetime_str,
                        "model": model,
                        "source": source,
                        "status": status
                    })
    return leads

def accept_lead(session, lead_info):
    """Opens viewleadcustomer.cfm and posts saveleadcustomer.cfm to accept the lead."""
    lead_url = lead_info.get("url") or f'{get_base_url()}/index.cfm?page=pages/viewleadcustomer.cfm&ileadcustid={lead_info['ileadcustid']}&duplicate='
    
    r_view = session.get(lead_url, timeout=20)
    soup = BeautifulSoup(r_view.text, "html.parser")
    
    form = soup.find("form", {"name": "savelead"}) or soup.find("form", action=lambda a: a and "saveleadcustomer" in a)
    if not form:
        raise RuntimeError(f"Could not find savelead form for lead {lead_info.get('ileadcustid')}")
    
    action_url = urljoin(r_view.url, form.get("action", ""))
    
    # Extract customer profile from page if missing
    name = lead_info.get("name", "")
    phone = lead_info.get("phone", "")
    email = lead_info.get("email", "")
    model = lead_info.get("model", "")
    
    for tr in soup.find_all("tr"):
        row_text = tr.get_text(separator=" ", strip=True)
        if "Mobile No" in row_text and not phone:
            tds = tr.find_all("td")
            if len(tds) >= 2:
                phone = tds[-1].get_text(strip=True)
        if "E-mail" in row_text and not email:
            tds = tr.find_all("td")
            if len(tds) >= 2:
                email = tds[-1].get_text(strip=True)
        if "Make Model Captured" in row_text and not model:
            tds = tr.find_all("td")
            if len(tds) >= 2:
                model = tds[-1].get_text(strip=True)

    # Extract all hidden inputs from the form
    payload = {}
    for inp in form.find_all("input"):
        n = inp.get("name")
        if n:
            payload[n] = inp.get("value", "")
    
    payload["pagetype"] = "accept"
    
    headers = {
        "Origin": get_base_url(),
        "Referer": lead_url,
    }
    
    r_post = session.post(action_url, data=payload, headers=headers, allow_redirects=True, timeout=20)
    
    if "Oops!" in r_post.text or "An error has occured" in r_post.text:
        raise RuntimeError(f"Dealer CRM returned error when accepting lead {lead_info.get('ileadcustid')}")
    
    # Record in local SQLite databases
    try:
        init_db()
        upsert_prospect(
            custid=lead_info.get("ileadcustid"),
            name=name,
            phone=phone,
            vehicle=model,
            contact_count=0,
            purpose=sanitize_dashes("Inbound Lead Accepted via WhatsApp CRM Alert"),
            notes=[sanitize_dashes(f"Accepted from Dealer CRM inbox (Source: {lead_info.get('source', 'Online/Website')}, Date: {lead_info.get('datetime', 'Today')})")],
            likelihood_score=85,
            likelihood_tier="HIGH",
            likelihood_reason=sanitize_dashes(f"Fresh inbound lead: {model}")
        )
    except Exception as e:
        print(f"Warning: Failed to index in prospect_history.db: {e}", file=sys.stderr)

    return {
        "success": True,
        "ileadcustid": lead_info.get("ileadcustid"),
        "name": name,
        "phone": phone,
        "email": email,
        "model": model,
        "source": lead_info.get("source"),
        "status": "Accepted"
    }

def main():
    parser = argparse.ArgumentParser(description="Accept Leads on Dealer CRM")
    parser.add_argument("--lead-id", help="Specific ileadcustid to accept")
    parser.add_argument("--query", "-q", help="Filter leads by customer name or phone")
    parser.add_argument("--all", action="store_true", help="Accept all unactioned leads in inbox")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd)
    
    leads = get_unactioned_leads(session)
    
    if not leads:
        if args.json:
            print(json.dumps({"success": True, "count": 0, "accepted": [], "message": "No pending unactioned leads found."}))
        else:
            print("Found 0 unactioned lead(s) in inbox. No pending unactioned leads found.")
        return

    targets = []
    if args.lead_id:
        targets = [l for l in leads if l["ileadcustid"] == args.lead_id]
    elif args.query:
        q = args.query.lower()
        targets = [l for l in leads if q in l["name"].lower() or q in l["phone"]]
    elif args.all or len(leads) >= 1:
        targets = leads

    results = []
    for t in targets:
        try:
            r = accept_lead(session, t)
            results.append(r)
            if not args.json:
                print(f"✅ Successfully accepted lead for {r['name']} (ID: {r['ileadcustid']}, Phone: {r['phone']}, Vehicle: {r['model']})")
        except Exception as e:
            if not args.json:
                print(f"❌ Failed to accept lead {t.get('ileadcustid')}: {e}")

    if args.json:
        print(json.dumps({"success": True, "count": len(results), "accepted": results}))

if __name__ == "__main__":
    main()
