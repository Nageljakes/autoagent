#!/usr/bin/env python3
"""
batch_followup.py - High-speed multi-lead WhatsApp outreach and Dealership CRM dual-logging engine.
Processes multiple leads in a single unified execution, preventing CLI tool backgrounding and timeouts.
"""

import sys
import os
import re
import json
import sqlite3
import argparse
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOHUB_SCRIPTS_DIR = os.getenv("AUTOHUB_SCRIPTS_DIR", os.path.abspath(os.path.join(SCRIPT_DIR, "../../autohub-portal/scripts")))

sys.path.append(SCRIPT_DIR)
if os.path.exists(AUTOHUB_SCRIPTS_DIR):
    sys.path.append(AUTOHUB_SCRIPTS_DIR)

import action_followup
from action_followup import (
    resolve_customer_context,
    evaluate_language_preference,
    synthesize_followup_message,
    guard_and_adapt_message,
    dispatch_whatsapp_message,
    log_to_portal_crm,
    mask_phone,
    sanitize_dashes,
    query_bridge_api,
    fetch_phone_from_crm_era,
    clean_customer_name,
    clean_first_name,
    SALESPERSON_NAME,
    DEALERSHIP_NAME
)

try:
    from prospect_db import init_db, upsert_prospect, DB_PATH
except ImportError:
    DB_PATH = os.getenv("CRM_DB_PATH", os.path.expanduser("~/.gemini/antigravity-cli/scratch/prospect_history.db"))
    upsert_prospect = None

try:
    from portal_login import login, load_credentials_from_env_file, get_base_url
except ImportError:
    try:
        from nissan_login import login, load_credentials_from_env_file
        get_base_url = lambda: "https://egm.auto-hub.co.za"
    except ImportError:
        login = None
        get_base_url = lambda: ""

def parse_lead_entry(raw_str: str) -> Dict[str, str]:
    """Parses a lead entry like 'Wisani - Magnite' or 'Kensley - Navara Single cab'."""
    raw_str = raw_str.strip()
    # Strip leading dashes or bullets like '- Wisani - Magnite'
    raw_str = re.sub(r"^[\*\-\•\d\.\)\s]+", "", raw_str)
    
    parts = re.split(r"\s*[-–—:]\s*", raw_str, maxsplit=1)
    name = parts[0].strip()
    vehicle = parts[1].strip() if len(parts) > 1 else ""
    
    # Normalize vehicle if present
    if vehicle and not any(vehicle.lower().startswith(b) for b in ["nissan", "suzuki", "toyota", "vw", "ford"]):
        vehicle = f"{vehicle}"
        
    return {"query": name, "name": name, "vehicle": vehicle}

def find_lead_in_local_history(query: str) -> Optional[Dict[str, Any]]:
    """Looks up lead in prospect_history.db."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        with sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True) as conn:
            cur = conn.cursor()
            q_wild = f"%{query.strip()}%"
            cur.execute("""
                SELECT custid, name, phone, vehicle_model, likelihood_score, likelihood_tier, last_diary_date 
                FROM prospects 
                WHERE name LIKE ? OR custid = ? OR phone LIKE ?
                LIMIT 1
            """, (q_wild, query.strip(), q_wild))
            row = cur.fetchone()
            if row:
                return {
                    "custid": row[0],
                    "name": row[1],
                    "phone": row[2],
                    "vehicle": row[3],
                    "score": row[4],
                    "tier": row[5],
                    "last_date": row[6]
                }
    except Exception:
        pass
    return None

def fetch_today_diary_leads_from_autohub() -> List[Dict[str, Any]]:
    """Fetches today's active diary entries from CRM, accepting any pending unactioned leads first."""
    if not login:
        return []
    try:
        from bs4 import BeautifulSoup
        user, pwd = load_credentials_from_env_file()
        session, _ = login(user, pwd)
        base_url = get_base_url() or "https://egm.auto-hub.co.za"

        # Check and auto-accept any pending unaccepted leads in inbox first
        try:
            from accept_lead import get_unactioned_leads, accept_lead
            unactioned = get_unactioned_leads(session)
            if unactioned:
                print(f"📥 Found {len(unactioned)} pending unaccepted lead(s) in inbox. Auto-accepting...")
                for ld in unactioned:
                    try:
                        accept_lead(session, ld)
                        print(f"   ✅ Auto-accepted lead: {ld.get('name')}")
                    except Exception as e:
                        print(f"   ⚠️ Failed to accept lead {ld.get('name')}: {e}")
        except Exception:
            pass

        url = f"{base_url}/index.cfm?page=pages/entries.cfm"
        r = session.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        
        leads = []
        for form in soup.find_all("form"):
            action = form.get("action", "")
            if "adddiaryentry.cfm" in action:
                cid = form.find("input", {"name": "custid"})
                cname = form.find("input", {"name": "contactname"})
                phone = form.find("input", {"name": "phoneno"})
                purpose = form.find("input", {"name": "purpose"})
                if cid and cname:
                    leads.append({
                        "custid": cid.get("value", "").strip(),
                        "name": cname.get("value", "").strip(),
                        "phone": phone.get("value", "").strip() if phone else "",
                        "purpose": purpose.get("value", "").strip() if purpose else ""
                    })
        return leads
    except Exception as e:
        print(f"⚠️ Failed to fetch live diary from CRM portal: {e}", file=sys.stderr)
        return []

def process_batch_leads(
    lead_items: List[Dict[str, str]],
    intent: str,
    days: int = 1,
    dry_run: bool = False,
    explicit_message: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Processes a batch of leads through context resolution, language analysis, dispatch, and CRM logging."""
    results = []
    
    print(f"\n🚀 Batch Follow-Up Engine Active: Processing {len(lead_items)} lead(s)...")
    print(f"📅 Target Diary Reschedule: {days} day(s) ahead {'(Keeping on today)' if days == 0 else ''}")
    print(f"🔍 Mode: {'DRY RUN (Preview only)' if dry_run else 'LIVE DISPATCH & CRM DUAL-LOG'}\n")
    
    for idx, item in enumerate(lead_items, 1):
        q = item.get("query") or item.get("name")
        hint_vehicle = item.get("vehicle", "")
        print(f"[{idx}/{len(lead_items)}] Processing '{q}'...")
        
        # 1. Resolve context
        context = resolve_customer_context(q, explicit_name=item.get("name"))
        if not context["vehicle"] and hint_vehicle:
            context["vehicle"] = hint_vehicle

        # Preserve custid and phone from item (e.g. from live entries) if missing in context
        if item.get("custid") and not context.get("custid"):
            context["custid"] = item["custid"]
        if item.get("phone") and not context.get("phone"):
            context["phone"] = item["phone"]
            
        # If still missing phone or custid, check local prospect_history.db
        if not context["phone"] or not context["custid"]:
            hist = find_lead_in_local_history(q)
            if hist:
                if not context["custid"]: context["custid"] = hist["custid"]
                if not context["name"]: context["name"] = hist["name"]
                if not context["phone"]: context["phone"] = hist["phone"]
                if not context["vehicle"] and hist["vehicle"]: context["vehicle"] = hist["vehicle"]
        
        # If still missing phone but custid is known, fetch directly from ERA
        if not context["phone"] and context["custid"]:
            era_phone, era_name = fetch_phone_from_crm_era(context["custid"])
            if era_phone: context["phone"] = era_phone
            if era_name and not context["name"]: context["name"] = era_name
            
        phone = context["clean_phone"] or re.sub(r"[^0-9]", "", context.get("phone", ""))
        first_name = context.get("first_name") or clean_first_name(context.get("name") or q) or (context["name"].split()[0] if context.get("name") else q.split()[0])
        vehicle = context.get("vehicle") or hint_vehicle or "Vehicle"
        
        # 2. Evaluate language preference
        lang_pref = evaluate_language_preference(context, intent=intent)
        detected_lang = lang_pref["detected_language"]
        
        # 3. Draft personalized message
        if explicit_message:
            final_message = guard_and_adapt_message(explicit_message, first_name, detected_lang, intent, vehicle)
        else:
            if detected_lang == "afrikaans":
                final_message = f"Goeiedag {first_name}, {SALESPERSON_NAME} hier van {DEALERSHIP_NAME} 😊 Ek kontak jou graag oor die {vehicle}. Wanneer sal die beste tyd wees om jou vinnig te skakel? 🚗"
            else:
                final_message = f"Good day {first_name}, {SALESPERSON_NAME} here from {DEALERSHIP_NAME} 😊 Reaching out regarding your interest in the {vehicle}. When would be the best time to give you a quick call? 🚗"
                
        final_message = sanitize_dashes(final_message)
        
        record = {
            "index": idx,
            "query": q,
            "custid": context.get("custid", ""),
            "name": context.get("name") or q,
            "first_name": first_name,
            "phone_raw": phone,
            "phone_masked": mask_phone(phone),
            "vehicle": vehicle,
            "language": detected_lang,
            "confidence": lang_pref["confidence"],
            "reasons": lang_pref.get("reasons", []),
            "message": final_message,
            "status": "PENDING",
            "dispatch_success": False,
            "crm_logged": False
        }
        
        if not phone:
            record["status"] = "SKIPPED_NO_PHONE"
            record["error"] = f"Could not find valid phone number for customer '{q}'"
            print(f"   ⚠️ Skipped: No phone number resolved.")
            results.append(record)
            continue
            
        # 4. Check WhatsApp registration
        api_check = query_bridge_api(f"/check-number/{phone}")
        on_whatsapp = api_check.get("exists", True) if api_check.get("success") else True
        record["on_whatsapp"] = on_whatsapp
        
        if not on_whatsapp:
            record["status"] = "NOT_ON_WHATSAPP"
            print(f"   ⚠️ Customer is NOT registered on WhatsApp.")
            if not dry_run:
                crm_note = f"Follow-up WhatsApp aborted: customer phone ({mask_phone(phone)}) is not registered on WhatsApp. Direct phone call required."
                crm_res = log_to_portal_crm(context["custid"], context["name"] or q, crm_note, days=days)
                record["crm_logged"] = crm_res.get("success", False)
            results.append(record)
            continue
            
        if dry_run:
            record["status"] = "DRY_RUN_OK"
            print(f"   ✅ [DRY RUN] {detected_lang.upper()} message prepared: \"{final_message[:60]}...\"")
            results.append(record)
            continue
            
        # 5. Live WhatsApp bridge dispatch
        dispatch_res = dispatch_whatsapp_message(phone, final_message)
        record["dispatch_res"] = dispatch_res
        
        if dispatch_res.get("success"):
            record["dispatch_success"] = True
            print(f"   📲 WhatsApp message delivered via bridge.")
            
            # 6. Live CRM dual-log
            crm_note = f"Sent follow-up WhatsApp ({detected_lang.capitalize()}): {final_message[:90]}..."
            crm_res = log_to_portal_crm(context["custid"], context["name"] or q, crm_note, days=days)
            record["crm_logged"] = crm_res.get("success", False)
            record["status"] = "COMPLETED"
            print(f"   📅 CRM note logged & diary set to {days} day(s) ahead.")
        else:
            record["status"] = "DISPATCH_FAILED"
            record["error"] = dispatch_res.get("error", "Unknown dispatch failure")
            print(f"   ❌ WhatsApp dispatch failed: {record['error']}")
            
        results.append(record)
        
    return results

def print_presentation_summary(results: List[Dict[str, Any]], days: int, dry_run: bool):
    """Prints a beautiful presentation card for each lead."""
    print("\n" + "═" * 55)
    print(f"📊 BATCH LEAD OUTREACH SUMMARY ({'DRY RUN' if dry_run else 'LIVE EXECUTED'})")
    print(f"📅 Diary Target: {'Today (Kept on today)' if days == 0 else f'Moved {days} day(s) ahead'}")
    print("═" * 55 + "\n")
    
    for r in results:
        status_emoji = "✅" if r.get("status") in ["COMPLETED", "DRY_RUN_OK"] else "⚠️" if "NOT_ON_WHATSAPP" in r.get("status", "") else "❌"
        print(f"{status_emoji} {r['name']} | 🚗 {r['vehicle']}")
        print(f"📞 {r['phone_masked']} | 🗣️ Language: {r['language'].upper()} ({r['confidence']})")
        print(f"💬 Message:")
        print(f"\"{r['message']}\"")
        if dry_run:
            print(f"📌 Status: Ready to dispatch & log note (Diary -> {days} days)")
        else:
            wa_status = "Delivered" if r.get("dispatch_success") else "Not sent"
            crm_status = "Logged & diary moved" if r.get("crm_logged") else "Failed/Skipped"
            print(f"📌 WhatsApp: {wa_status} | CRM: {crm_status}")
        print("-" * 55)
        
    successful = sum(1 for r in results if r.get("status") in ["COMPLETED", "DRY_RUN_OK"])
    print(f"\n✨ Completed: {successful}/{len(results)} lead(s) successfully processed.\n")

def main():
    parser = argparse.ArgumentParser(description="High-speed multi-lead WhatsApp outreach and CRM engine.")
    parser.add_argument("--names", "-n", type=str, help="Comma-separated or newline-separated customer names/leads.")
    parser.add_argument("--today-leads", action="store_true", help="Auto-detect fresh leads from today's diary.")
    parser.add_argument("--intent", "-i", type=str, default="checking when is the best time to contact them", help="Follow-up intent.")
    parser.add_argument("--message", "-m", type=str, help="Uniform message text override.")
    parser.add_argument("--days", "-d", type=int, default=1, help="Diary days ahead to reschedule (default: 1; 0 keeps on today).")
    parser.add_argument("--dry-run", action="store_true", help="Preview mode without sending messages or updating CRM.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON.")
    
    args = parser.parse_args()
    
    lead_items = []
    
    if args.names:
        raw_items = re.split(r"[,;\n]+", args.names)
        for it in raw_items:
            it = it.strip()
            if it:
                lead_items.append(parse_lead_entry(it))
    elif args.today_leads:
        diary_leads = fetch_today_diary_leads_from_autohub()
        for dl in diary_leads:
            lead_items.append({"query": dl["name"], "name": dl["name"], "custid": dl["custid"], "phone": dl.get("phone", "")})
    else:
        print("❌ Error: Must specify --names or --today-leads.", file=sys.stderr)
        parser.print_help()
        sys.exit(1)
        
    if not lead_items:
        print("⚠️ No leads found to process.", file=sys.stderr)
        sys.exit(0)
        
    results = process_batch_leads(
        lead_items=lead_items,
        intent=args.intent,
        days=args.days,
        dry_run=args.dry_run,
        explicit_message=args.message
    )
    
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_presentation_summary(results, days=args.days, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
