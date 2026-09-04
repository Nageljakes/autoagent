#!/usr/bin/env python3
"""
deal_heat_scorer.py - Dual-Signal Lead Prioritization Engine
Combines Dealer CRM CRM Milestones (Quotes, OTPs, Contacts, Notes) + Live WhatsApp Signals (Replies, Docs, Media, Latency)
"""

import os
import re
import sys
import json
import sqlite3
import requests
from datetime import datetime, timedelta

CRM_DB = "data/scratch/prospect_history.db"
WA_DB = "jax-shared/data/prospects.db"
WA_API = "http://127.0.0.1:9095"

def clean_phone(phone_str):
    if not phone_str:
        return ""
    digits = re.sub(r"\D", "", str(phone_str))
    if digits.startswith("0") and len(digits) == 10:
        return "27" + digits[1:]
    return digits

def evaluate_deal_heat(prospect, crm_notes, wa_messages):
    score = 50 # Baseline starting point
    reasons = []
    
    name = prospect.get("name", "")
    contact_count = prospect.get("contact_count", 1)
    purpose = (prospect.get("purpose") or "").lower()
    
    # 1. dealer-crm CRM SIGNALS
    all_crm_text = " ".join([n.get("note", "") for n in crm_notes]).lower() + " " + purpose
    
    # High Velocity Physical Valuation / Direct Application (95+ boost)
    if any(k in all_crm_text for k in ["came in for evaluation", "application received", "taking delivery"]):
        score += 45
        reasons.append("CRM: High-Velocity in-person evaluation / Application active")
    elif any(k in all_crm_text for k in ["otp", "approved", "finance approved", "pre-approval", "contract"]):
        score += 35
        reasons.append("CRM: OTP / Finance approval in progress")
    elif any(k in all_crm_text for k in ["quote", "application sent", "payslip", "bank statement", "id copy", "f&i"]):
        score += 25
        reasons.append("CRM: Quote / Finance documents in play")
        
    if any(k in all_crm_text for k in ["test drive", "viewing", "appointment", "coming in", "coming to pta", "saturday"]):
        score += 20
        reasons.append("CRM: Test drive / Showroom appointment noted")
        
    if any(k in all_crm_text for k in ["trade in", "trade-in", "swop", "valuation", "settlement"]):
        score += 15
        reasons.append("CRM: Active trade-in / valuation discussed")

    if any(k in all_crm_text for k in ["declined", "blacklisted", "debt", "too low budget", "budget mismatch", "cant do deal", "cannot assist"]):
        score -= 40
        reasons.append("CRM: Affordability / Credit decline recorded")
        
    if any(k in all_crm_text for k in ["bought elsewhere", "no longer interested", "cancel", "archive"]):
        score -= 50
        reasons.append("CRM: Lost lead / Bought elsewhere")

    if contact_count >= 6:
        score -= 25
        reasons.append(f"CRM: High contact fatigue ({contact_count} attempts)")
    elif contact_count >= 4:
        score -= 10
        reasons.append(f"CRM: Moderate contact count ({contact_count} attempts)")
    elif contact_count <= 2:
        score += 10
        reasons.append("CRM: Fresh lead (<= 2 contacts)")

    # 2. LIVE WHATSAPP SIGNALS
    if wa_messages:
        inbound_msgs = [m for m in wa_messages if not m.get("fromMe")]
        outbound_msgs = [m for m in wa_messages if m.get("fromMe")]
        
        if inbound_msgs:
            score += 20
            reasons.append(f"WhatsApp: Active two-way chat ({len(inbound_msgs)} customer replies)")
            latest_customer_text = " ".join([m.get("body", "") for m in inbound_msgs[-3:]]).lower()
            
            if any(m.get("hasMedia") or m.get("type") in ["image", "document"] for m in inbound_msgs):
                score += 25
                reasons.append("WhatsApp: Customer sent photos/document attachment")
                
            if any(k in latest_customer_text for k in ["payslip", "statement", "id", "quote", "price", "discount", "available", "when"]):
                score += 15
                reasons.append("WhatsApp: High buying intent keywords in recent messages")
        else:
            if len(outbound_msgs) >= 2:
                score -= 15
                reasons.append("WhatsApp: Outbound messages sent with zero customer replies")
    else:
        reasons.append("WhatsApp: No chat history found")

    final_score = max(0, min(100, score))
    
    if final_score >= 80:
        stage = "Stage 1: Hot Money / Closing"
        action = "Call immediately or follow up on F&I/Delivery paperwork."
    elif final_score >= 60:
        stage = "Stage 2: Active Evaluation"
        action = "Push for test drive appointment or finalize trade-in numbers."
    elif final_score >= 40:
        stage = "Stage 3: Info & Selection"
        action = "Send vehicle photos/specs on WhatsApp to nurture."
    elif final_score >= 20:
        stage = "Stage 4: Cold / Fatigued"
        action = "Batch move to Friday sweep or light WhatsApp check-in."
    else:
        stage = "Stage 5: Disqualified / Inactive"
        action = "Move diary out by 6 months or archive."

    return final_score, stage, reasons, action

def generate_heat_report():
    conn_crm = sqlite3.connect(CRM_DB)
    c_crm = conn_crm.cursor()
    
    c_crm.execute("""
        SELECT custid, name, phone, vehicle_model, contact_count, last_purpose 
        FROM prospects 
        WHERE name != 'New Customer (NC1)'
    """)
    prospect_rows = c_crm.fetchall()
    
    wa_history_by_phone = {}
    if os.path.exists(WA_DB):
        try:
            conn_wa = sqlite3.connect(WA_DB)
            c_wa = conn_wa.cursor()
            c_wa.execute("SELECT from_phone, body, is_from_me, timestamp FROM messages ORDER BY timestamp ASC")
            for r in c_wa.fetchall():
                p_clean = clean_phone(r[0])
                if p_clean not in wa_history_by_phone:
                    wa_history_by_phone[p_clean] = []
                wa_history_by_phone[p_clean].append({
                    "fromMe": bool(r[2]),
                    "body": r[1],
                    "timestamp": r[3]
                })
            conn_wa.close()
        except Exception as e:
            pass
            
    scored_prospects = []
    
    for row in prospect_rows:
        cid, name, phone, veh, count, purpose = row
        clean_p = clean_phone(phone)
        
        c_crm.execute("SELECT entry_date, note FROM prospect_notes WHERE custid = ? ORDER BY id ASC", (cid,))
        notes = [{"date": n[0], "note": n[1]} for n in c_crm.fetchall()]
        wa_msgs = wa_history_by_phone.get(clean_p, [])
        
        p_dict = {
            "name": name,
            "custid": cid,
            "phone": phone,
            "vehicle": veh if veh else "Unspecified",
            "contact_count": count if count else 1,
            "purpose": purpose
        }
        
        score, stage, reasons, action = evaluate_deal_heat(p_dict, notes, wa_msgs)
        
        scored_prospects.append({
            "name": name,
            "phone": phone,
            "vehicle": veh,
            "score": score,
            "stage": stage,
            "reasons": reasons,
            "action": action
        })
        
    conn_crm.close()
    scored_prospects.sort(key=lambda x: x["score"], reverse=True)
    return scored_prospects

if __name__ == "__main__":
    results = generate_heat_report()
    print(f"\n=======================================================")
    print(f"       TOP 5 HIGH-HEAT PROSPECTS TO TARGET NOW         ")
    print(f"=======================================================\n")
    for idx, p in enumerate(results[:5], 1):
        print(f"#{idx} {p['name']} | Score: {p['score']} | {p['stage']}")
        print(f"   Vehicle: {p['vehicle']}")
        print(f"   Action:  {p['action']}")
        print(f"   Signals: {', '.join(p['reasons'][:3])}")
        print("-" * 55)
