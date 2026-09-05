#!/usr/bin/env python3
"""
generate_diary_cards.py - Executive Deal Card Briefing Generator
Generates clean, actionable, noise-filtered PDF/Markdown briefings for diary entries.
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
from move_diary_entries import parse_all_entries
from deal_heat_scorer import evaluate_deal_heat, clean_phone

DB_PATH = "data/scratch/prospect_history.db"
WA_DB = "jax-shared/data/prospects.db"
WA_API = "http://127.0.0.1:9095"

def get_wa_history(phone, name="", crm_notes=None):
    if crm_notes is None:
        crm_notes = []
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
                        sender = os.getenv("SALESPERSON_NAME", "Sales Advisor") if m.get("from_me") else "Customer"
                        body = (m.get("content") or "").strip().replace("\n", " ")
                        if len(body) > 100:
                            body = body[:97] + "..."
                        lines.append(f"{sender}: {body}")
                    return " | ".join(lines)
        except Exception:
            pass

    # Tier 2: Search bridge by name (filtering to prospect messages only)
    if name and len(name.strip()) > 2:
        parts = [p for p in re.split(r"\s+", name.strip()) if len(p) > 2]
        for query in [name.strip()] + parts:
            try:
                res = requests.get(f"{WA_API}/search?q={requests.utils.quote(query)}&type=prospect", timeout=3)
                if res.status_code == 200:
                    data = res.json()
                    results = data.get("results", [])
                    valid_prospect_msgs = []
                    for m in results:
                        content = (m.get("content") or "").strip()
                        sender = m.get("sender_name") or ""
                        if any(c in content for c in ["/goal", "/learn", "/boost", "/plan", "Tiny AI Agent", "Executive Deal Card", "SKILL.md"]):
                            continue
                        if m.get("contact_type") in ["vip", "internal_team"] and any(k in sender.lower() for k in [os.getenv("SALESPERSON_NAME", "advisor").lower(), "agent", "you"]):
                            continue
                        valid_prospect_msgs.append(m)

                    if valid_prospect_msgs:
                        recent = list(reversed(valid_prospect_msgs[:3]))
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

    # Tier 3: CRM Note Synthesis
    all_notes_text = " ".join(crm_notes).lower()
    if "quote" in all_notes_text and ("whatsapp" in all_notes_text or "sent" in all_notes_text or "messaged" in all_notes_text):
        return "Quote sent via WhatsApp / CRM. Awaiting customer review."
    elif "two grey ticks" in all_notes_text or "sent introduction whatsapp" in all_notes_text or "sent intro whatsapp" in all_notes_text:
        if "no reply" in all_notes_text or "no response" in all_notes_text:
            return "Outbound intro message sent (two grey ticks). No customer response yet on CRM."
        return "Introductory WhatsApp message dispatched. Awaiting customer reply."
    elif "sent whatsapp" in all_notes_text or "whatsapp follow" in all_notes_text:
        return "Follow-up WhatsApp message sent from CRM history."

    return "No active WhatsApp chat record found."

def parse_era_profile(html):
    soup = BeautifulSoup(html, "html.parser")
    
    # 1. Enquiry History & Next Vehicle
    enquiries = []
    for tr in soup.find_all("tr"):
        t = tr.get_text(separator=" | ", strip=True)
        if ("NEW |" in t or "USED |" in t) and ("Open" in t or "Yes" in t or "No" in t):
            if "Year" not in t and "Specification" not in t:
                parts = [p.strip() for p in t.split("|") if p.strip()]
                clean_row = " | ".join(parts)
                if clean_row not in enquiries:
                    enquiries.append(clean_row)
                    
    # Next vehicle selection dropdowns
    next_sel = []
    for name in ["nextmake", "nextmodel", "nextspecid", "nextspecId"]:
        sel = soup.find("select", {"name": name})
        if sel:
            opt = sel.find("option", selected=True)
            if opt and opt.text.strip() and not opt.text.strip().startswith("--") and opt.text.strip().lower() not in ["none", "select", "please select make"]:
                next_sel.append(opt.text.strip())

    # 2. Filtered Clean Notes Timeline
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

    # Filter out CRM noise
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
        "enquiries": enquiries,
        "next_sel": " ".join(next_sel) if next_sel else "",
        "notes": notes if notes else raw_notes[:3]
    }

def generate_cards_briefing(output_md, output_pdf):
    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd)
    
    r_diary = session.get(f"{get_base_url()}/index.cfm?page=pages/entries.cfm")
    soup_diary = BeautifulSoup(r_diary.text, "html.parser")
    sg_input = soup_diary.find("input", {"id": "sg"})
    sg = sg_input.get("value") if sg_input else ""
    
    today_entries = parse_all_entries(r_diary.text)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    cards = []
    
    for idx, e in enumerate(today_entries, 1):
        cid = e["custid"]
        name = e["name"]
        
        c.execute("SELECT phone, contact_count, last_purpose FROM prospects WHERE custid = ?", (cid,))
        row = c.fetchone()
        phone = row[0] if row and row[0] else ""
        count = row[1] if row and row[1] else 1
        purpose = row[2] if row and row[2] else e.get("purpose", "")
        
        url_era = f"{get_base_url()}/index.cfm?page=pages/customerera_selecttemplate.cfm&sg={sg}&custid={cid}"
        r_era = session.get(url_era, timeout=15)
        parsed = parse_era_profile(r_era.text)
        
        veh = "Not Specified"
        if parsed["enquiries"]:
            veh = parsed["enquiries"][0]
        elif parsed["next_sel"]:
            veh = parsed["next_sel"]
            
        wa_snap = get_wa_history(phone)
        
        p_dict = {
            "name": name,
            "custid": cid,
            "phone": phone,
            "vehicle": veh,
            "contact_count": count,
            "purpose": purpose
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
        
    conn.close()
    cards.sort(key=lambda x: x["score"], reverse=True)
    
    with open(output_md, "w") as f:
        f.write("# Executive Diary Briefing - Deal Cards\n")
        f.write(f"Generated: 01/09/2026 | Total Prospects: {len(cards)}\n\n")
        
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
            
    cmd = f'timeout 60 pandoc "{output_md}" -o "{output_pdf}" </dev/null'
    subprocess.run(cmd, shell=True)
    print(f"SUCCESS: {output_pdf}")

if __name__ == "__main__":
    out_md = "~/.gemini/antigravity-cli/brain/d2a4f658-a838-4466-832a-67dd1042982d/executive_diary_cards.md"
    out_pdf = "~/.gemini/antigravity-cli/brain/d2a4f658-a838-4466-832a-67dd1042982d/executive_diary_cards.pdf"
    generate_cards_briefing(out_md, out_pdf)
