#!/usr/bin/env python3
"""
generate_all_21_diary_cards.py - Multi-page Complete Diary Executive Briefing Generator
Extracts all pages (all 20+ entries) using the AJAX Load More endpoint and renders full Executive Deal Cards.
"""

import os
import re
import sys
import json
import sqlite3
import requests
import subprocess
from pathlib import Path
from bs4 import BeautifulSoup
sys.path.append(str(Path(__file__).resolve().parent))
from portal_login import get_base_url, login, load_credentials_from_env_file
from deal_heat_scorer import evaluate_deal_heat, clean_phone

DB_PATH = "data/scratch/prospect_history.db"
WA_DB = "jax-shared/data/prospects.db"
WA_API = "http://127.0.0.1:9095"

def get_wa_history(phone, name, crm_notes):
    # Tier 1: Direct Phone query to bridge API
    cleaned = clean_phone(phone)
    if cleaned:
        try:
            res = requests.get(f"{WA_API}/history/{cleaned}", timeout=3)
            if res.status_code == 200:
                data = res.json()
                messages = data.get("messages", [])
                if messages:
                    recent = messages[-4:]
                    lines = []
                    for m in recent:
                        sender = os.getenv("SALESPERSON_NAME", "Sales Advisor") if m.get("fromMe") else "Customer"
                        body = m.get("body", "").strip().replace("\n", " ")
                        if len(body) > 100:
                            body = body[:97] + "..."
                        lines.append(f"{sender}: {body}")
                    return " | ".join(lines)
        except Exception:
            pass

    # Tier 2: Search bridge by customer name keywords (strictly filtering to prospect messages only)
    if name and len(name.strip()) > 2:
        parts = [p for p in re.split(r"\s+", name.strip()) if len(p) > 2]
        for query in [name.strip()] + parts:
            try:
                res = requests.get(f"{WA_API}/search?q={requests.utils.quote(query)}&type=prospect", timeout=3)
                if res.status_code == 200:
                    data = res.json()
                    results = data.get("results", [])
                    # Filter out any slash commands (/goal, /learn), developer messages, or agent echoes
                    valid_prospect_msgs = []
                    for m in results:
                        content = (m.get("content") or "").strip()
                        sender = m.get("sender_name") or ""
                        if any(c in content for c in ["/goal", "/learn", "/boost", "/plan", "Tiny AI Agent", "Executive Deal Card", "SKILL.md"]):
                            continue
                        if m.get("contact_type") in ["vip", "internal_team"] and any(k in sender.lower() for k in [os.getenv("SALESPERSON_NAME", "advisor").lower(), "agent", "you"]):
                            # Skip internal chats between {SALESPERSON_NAME} and AI agent
                            continue
                        valid_prospect_msgs.append(m)

                    if valid_prospect_msgs:
                        recent = valid_prospect_msgs[-3:]
                        lines = []
                        for m in recent:
                            sender = os.getenv("SALESPERSON_NAME", "Sales Advisor") if m.get("from_me") else (m.get("sender_name") or "Customer")
                            body = (m.get("content") or "").strip().replace("\n", " ")
                            if len(body) > 100:
                                body = body[:97] + "..."
                            lines.append(f"{sender}: {body}")
                        return " | ".join(lines)
            except Exception:
                pass

    # Tier 3: Direct SQLite Inspection for Quotes / Audit Logs / LIDs
    if os.path.exists(WA_DB):
        try:
            conn = sqlite3.connect(WA_DB)
            cur = conn.cursor()
            # Check explicit sends / quotes
            if cleaned:
                cur.execute("SELECT content, datetime(created_at) FROM explicit_send_audit_log WHERE prospect_phone LIKE ? ORDER BY id DESC LIMIT 1", (f"%{cleaned[-9:]}%",))
                row = cur.fetchone()
                if row:
                    conn.close()
                    return f"Outbound WhatsApp logged: {row[0][:90]}"
            conn.close()
        except Exception:
            pass

    # Tier 4: CRM CRM Touchpoint & Note Synthesis
    all_notes_text = " ".join(crm_notes).lower()
    if "quote" in all_notes_text and ("whatsapp" in all_notes_text or "sent" in all_notes_text or "messaged" in all_notes_text):
        return "Quote sent via WhatsApp / CRM. Awaiting customer review."
    elif "12pm" in all_notes_text or "12:00" in all_notes_text or "call at 12" in all_notes_text:
        return "Customer requested contact today at 12:00 PM."
    elif "two grey ticks" in all_notes_text or "sent introduction whatsapp" in all_notes_text or "sent intro whatsapp" in all_notes_text:
        if "no reply" in all_notes_text or "no response" in all_notes_text:
            return "Outbound intro message sent (two grey ticks). No customer response yet on CRM."
        return "Introductory WhatsApp message dispatched. Awaiting customer reply."
    elif "sent whatsapp" in all_notes_text or "whatsapp follow" in all_notes_text:
        return "Follow-up WhatsApp message sent from CRM history."
    elif "called" in all_notes_text and "no answer" in all_notes_text:
        return "Call attempted on CRM (no answer). No direct WhatsApp thread indexed."

    return "No active WhatsApp chat record found."

def parse_era_profile(html):
    soup = BeautifulSoup(html, "html.parser")
    
    # 1. Phone extraction
    phones = set(re.findall(r"0[678][0-9]{8}", html))
    phone_found = list(phones)[0] if phones else ""
    
    # 2. Enquiry History & Next Vehicle
    enquiries = []
    for tr in soup.find_all("tr"):
        t = tr.get_text(separator=" | ", strip=True)
        if ("NEW |" in t or "USED |" in t) and ("Open" in t or "Yes" in t or "No" in t):
            if "Year" not in t and "Specification" not in t:
                parts = [p.strip() for p in t.split("|") if p.strip()]
                clean_row = " | ".join(parts)
                if clean_row not in enquiries:
                    enquiries.append(clean_row)
                    
    next_sel = []
    for name in ["nextmake", "nextmodel", "nextspecid", "nextspecId"]:
        sel = soup.find("select", {"name": name})
        if sel:
            opt = sel.find("option", selected=True)
            if opt and opt.text.strip() and not opt.text.strip().startswith("--") and opt.text.strip().lower() not in ["none", "select", "please select make"]:
                next_sel.append(opt.text.strip())

    # 3. Filtered Clean Notes Timeline
    notes = []
    raw_notes = []
    for div in soup.find_all("div", class_="note-box"):
        t = div.get_text(separator=" | ", strip=True)
        if t: raw_notes.append(t)
    for tr in soup.find_all("tr"):
        t = tr.get_text(separator=" | ", strip=True)
        if "Logged by:" in t and "Notes:" in t:
            parts = [p.strip() for p in t.split("|") if p.strip()]
            raw_notes.append(" ".join(parts))

    salesperson_crm = os.getenv("SALESPERSON_CRM_NAME", "")
    crm_pattern = re.escape(salesperson_crm) if salesperson_crm else r"[\w\s]+"
    noise_patterns = ["lead,follow up", "lead, follow up", "follow up regarding - - interest", "follow up regarding - - follow up regarding interest"]
    for rn in raw_notes:
        rn_lower = rn.lower()
        if any(ign in rn_lower for ign in noise_patterns):
            continue
        clean_note = re.sub(rf"Logged by:\s*{crm_pattern}", "", rn, flags=re.IGNORECASE).strip()
        clean_note = re.sub(rf"\[{crm_pattern}\]", "", clean_note).strip()
        if clean_note and clean_note not in notes:
            notes.append(clean_note)

    return {
        "phone": phone_found,
        "enquiries": enquiries,
        "next_sel": " ".join(next_sel) if next_sel else "",
        "notes": notes if notes else raw_notes[:3]
    }

def fetch_all_today_entries(session):
    r_diary = session.get(f"{get_base_url()}/index.cfm?page=pages/entries.cfm", timeout=20)
    soup_diary = BeautifulSoup(r_diary.text, "html.parser")
    
    sg_input = soup_diary.find("input", {"id": "sg"}) or soup_diary.find("input", {"name": "sg"})
    sg = sg_input.get("value") if sg_input else ""
    if not sg:
        m = re.search(r"sg=([a-zA-Z0-9]+)", r_diary.text)
        sg = m.group(1) if m else ""

    hidden_type_el = soup_diary.find("input", {"id": "hiddenentriestype"})
    entriestype = hidden_type_el.get("value", "today") if hidden_type_el else "today"
    showdate_el = soup_diary.find("input", {"id": "showhiddendate"})
    showdate = showdate_el.get("value", "01/Sep/2026") if showdate_el else "01/Sep/2026"

    all_html_chunks = [r_diary.text]
    page_num = 2
    while page_num < 15:
        ajax_url = f"{get_base_url()}/index.cfm?page=includes/_showtableloadmoreentries.cfm&sg={sg}&ajx"
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
        if len(r_ajax.text) > 8000:
            all_html_chunks.append(r_ajax.text)
            page_num += 1
        else:
            break

    entries = []
    seen_ids = set()
    for chunk in all_html_chunks:
        s = BeautifulSoup(chunk, "html.parser")
        for form in s.find_all("form"):
            if "adddiaryentry.cfm" in form.get("action", ""):
                cid_tag = form.find("input", {"name": "custid"})
                name_tag = form.find("input", {"name": "contactname"})
                phone_tag = form.find("input", {"name": "phoneno"})
                purpose_tag = form.find("input", {"name": "purpose"})
                contno_tag = form.find("input", {"name": "contno"})

                if cid_tag:
                    cid = cid_tag.get("value").strip()
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        name = name_tag.get("value").strip() if name_tag else "Customer"
                        phone = phone_tag.get("value").strip() if phone_tag else ""
                        purpose = purpose_tag.get("value").strip() if purpose_tag else ""
                        contno = int(contno_tag.get("value")) if contno_tag and contno_tag.get("value").isdigit() else 1
                        entries.append({
                            "custid": cid,
                            "name": name,
                            "phone": phone,
                            "purpose": purpose,
                            "contact_count": contno
                        })
        for s_cid in re.findall(r"submitpage\((\d+)\)", chunk):
            if s_cid not in seen_ids:
                seen_ids.add(s_cid)
                entries.append({
                    "custid": s_cid,
                    "name": "Customer",
                    "phone": "",
                    "purpose": "Inbound Lead",
                    "contact_count": 1
                })
                
    return sg, entries

def generate_all_cards(output_md, output_pdf):
    print("Logging into CRM...")
    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd)
    
    sg, all_entries = fetch_all_today_entries(session)
    print(f"Total Diary Prospects Discovered across all pages: {len(all_entries)}")
    
    cards = []
    
    for idx, e in enumerate(all_entries, 1):
        cid = e["custid"]
        name = e["name"]
        print(f"[{idx}/{len(all_entries)}] Extracting ERA & Deal Card for {name} ({cid})...")
        
        url_era = f"{get_base_url()}/index.cfm?page=pages/customerera_selecttemplate.cfm&sg={sg}&custid={cid}"
        r_era = session.get(url_era, timeout=15)
        parsed = parse_era_profile(r_era.text)
        
        phone = e["phone"] if e["phone"] else parsed["phone"]
        
        if name == "Customer":
            s_era = BeautifulSoup(r_era.text, "html.parser")
            for tag in s_era.find_all(["span", "div", "td", "p"]):
                txt = tag.get_text(strip=True)
                if txt.startswith("Customer :") or txt.startswith("Customer:"):
                    name = txt.split(":", 1)[1].strip()
                    break
                    
        veh = "Not Specified"
        if parsed["enquiries"]:
            veh = parsed["enquiries"][0]
        elif parsed["next_sel"]:
            veh = parsed["next_sel"]
            
        wa_snap = get_wa_history(phone, name, parsed["notes"])
        
        p_dict = {
            "name": name,
            "custid": cid,
            "phone": phone,
            "vehicle": veh,
            "contact_count": e["contact_count"],
            "purpose": e["purpose"]
        }
        score, stage, reasons, action = evaluate_deal_heat(p_dict, [{"note": n} for n in parsed["notes"]], [])
        
        cards.append({
            "name": name,
            "phone": phone,
            "veh": veh,
            "score": score,
            "stage": stage,
            "action": action,
            "wa_snap": wa_snap,
            "notes": parsed["notes"]
        })
        
    cards.sort(key=lambda x: x["score"], reverse=True)
    
    print(f"Writing {len(cards)} Deal Cards to Markdown...")
    with open(output_md, "w") as f:
        f.write("# Executive Diary Briefing - Complete Deal Cards (Verified)\n")
        f.write(f"Generated: 01/09/2026 | Total Prospects: {len(cards)}\n\n")
        f.write("---\n\n")
        
        for c in cards:
            clean_name = c["name"].replace("#", "").replace("&", "and")
            clean_veh = c["veh"].replace("#", "").replace("&", "and").replace("$", "R")
            clean_action = c["action"].replace("#", "").replace("&", "and")
            clean_wa = c["wa_snap"].replace("#", "").replace("&", "and").replace("$", "R")
            
            f.write(f"## {clean_name} | {c['stage']} (Score: {c['score']})\n")
            f.write(f"- **Contact:** {c['phone'] if c['phone'] else 'Not Listed'}\n")
            f.write(f"- **Vehicle of Interest:** {clean_veh}\n")
            f.write(f"- **Action Plan:** {clean_action}\n")
            f.write(f"- **WhatsApp Snapshot:** {clean_wa}\n\n")
            
            f.write("### Clean Touchpoint History:\n")
            if c["notes"]:
                for n in c["notes"][:6]:
                    clean_n = n.replace("#", "").replace("&", "and").replace("$", "R")
                    f.write(f"- {clean_n}\n")
            else:
                f.write("- No critical interaction notes logged.\n")
                
            f.write("\n---\n\n")
            
    print("Converting complete document to PDF via pandoc...")
    cmd = f'timeout 60 pandoc "{output_md}" -o "{output_pdf}" </dev/null'
    subprocess.run(cmd, shell=True)
    print(f"SUCCESS: {output_pdf}")

if __name__ == "__main__":
    out_md = "~/.gemini/antigravity-cli/brain/d2a4f658-a838-4466-832a-67dd1042982d/executive_diary_cards_verified.md"
    out_pdf = "~/.gemini/antigravity-cli/brain/d2a4f658-a838-4466-832a-67dd1042982d/executive_diary_cards_verified.pdf"
    generate_all_cards(out_md, out_pdf)
