#!/usr/bin/env python3
"""
track_deals.py - Unified Deal Tracking, MTD Performance, and Pipeline Reconciliation
Combines Dealership Sales Calendar, CRM Sales Summary, Whiteboard, and Inbox OTPs.
"""

import sys
import os
import re
import json
import argparse
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOHUB_SCRIPTS = SCRIPT_DIR
CALENDAR_SCRIPTS = os.getenv("CALENDAR_SCRIPTS_DIR", os.path.abspath(os.path.join(SCRIPT_DIR, "../../bb-business-calendar/scripts")))

sys.path.append(AUTOHUB_SCRIPTS)
if os.path.exists(CALENDAR_SCRIPTS):
    sys.path.append(CALENDAR_SCRIPTS)

try:
    from portal_login import login, load_credentials_from_env_file, get_base_url
except ImportError:
    try:
        from nissan_login import login, load_credentials_from_env_file
        get_base_url = lambda: "https://egm.auto-hub.co.za"
    except ImportError:
        login, load_credentials_from_env_file, get_base_url = None, None, lambda: ""

try:
    from calendar_calc import load_calendar_data, get_sales_period_for_delivery, calculate_working_hours_breakdown, format_hours_and_mins, format_day_first
except ImportError:
    load_calendar_data = None
    format_day_first = lambda dt: dt.strftime("%a %d %b %Y, %H:%M SAST")
    format_hours_and_mins = lambda hrs: f"{int(hrs)}h {int((hrs % 1) * 60)}m"

def get_current_sales_period():
    if not load_calendar_data:
        return None
    try:
        cal_data = load_calendar_data()
        now_utc = datetime.utcnow()
        now_sast = now_utc + timedelta(hours=2)
        period = get_sales_period_for_delivery(now_sast, cal_data)
        total_hours, breakdown = calculate_working_hours_breakdown(now_sast, period["month_end"], cal_data)
        return {
            "now_sast": now_sast,
            "period": period,
            "total_hours": total_hours,
            "breakdown": breakdown
        }
    except Exception:
        return None

def fetch_performance_and_deals(salesman=None):
    if not salesman:
        salesman = os.getenv("CRM_USERNAME", "Jacobus Nagel")
    
    user, pwd = load_credentials_from_env_file()
    if not user or not pwd:
        # Fallback to standard config paths
        for fallback_path in [
            Path(os.path.expanduser("~/.config/dealer_credentials.env")),
            Path(os.path.expanduser("~/.config/nissan_credentials.env")),
            Path.cwd() / ".env"
        ]:
            if fallback_path.exists():
                user, pwd = load_credentials_from_env_file(fallback_path)
                if user and pwd:
                    break
    
    if not login or not user:
        raise RuntimeError("Dealership CRM credentials not configured. Run deploy.sh or set in .env")

    session, res = login(user, pwd)
    m = re.search(r"sg=([a-zA-Z0-9]+)", res.url)
    sg = m.group(1) if m else ""
    base_url = get_base_url() or "https://egm.auto-hub.co.za"

    cal = get_current_sales_period()
    if cal:
        start_dt = cal["period"]["period_start"]
        end_dt = cal["now_sast"]
    else:
        start_dt = datetime(2026, 8, 25, 12, 0)
        end_dt = datetime.now()

    # Format dates for portal
    start_str = start_dt.strftime("%d-%b-%Y")
    end_str = end_dt.strftime("%d-%b-%Y")

    post_data = {
        "startdate": start_str,
        "startday": str(start_dt.day),
        "startmonth": str(start_dt.month),
        "startyear": str(start_dt.year),
        "enddate": end_str,
        "endday": str(end_dt.day),
        "endmonth": str(end_dt.month),
        "endyear": str(end_dt.year),
        "newused": "Both",
        "brand": "0",
        "model": "0"
    }

    # 1. Fetch Sales Summary
    r_sales = session.post(f"{base_url}/index.cfm?page=reports/salessummary.cfm&sg={sg}", data=post_data, timeout=25)
    soup_s = BeautifulSoup(r_sales.text, "html.parser")

    sales_metrics = {}
    totals_metrics = {}
    for t in soup_s.find_all("table"):
        for row in t.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) >= 20:
                if salesman.lower() in cells[0].lower():
                    sales_metrics = {
                        "salesperson": cells[0],
                        "tel": cells[1],
                        "visit": cells[2],
                        "email": cells[3],
                        "web": cells[4],
                        "total_contacts": cells[7],
                        "follow_ups": cells[8],
                        "follow_up_ratio": cells[9],
                        "test_drives": cells[10],
                        "test_drive_pct": cells[11],
                        "quotes": cells[12],
                        "otps": cells[13],
                        "apps": cells[14],
                        "appr": cells[15],
                        "orders_new": cells[16],
                        "orders_used": cells[17],
                        "total_orders": cells[18],
                        "conversion_pct": cells[19],
                        "delivery_new": cells[20],
                        "delivery_used": cells[21],
                        "total_deliveries": cells[22],
                        "lost_opps": cells[23] if len(cells) > 23 else "0"
                    }
                elif "total" in cells[0].lower():
                    totals_metrics = {
                        "total_contacts": cells[7] if len(cells) > 7 else "",
                        "orders_new": cells[16] if len(cells) > 16 else "",
                        "orders_used": cells[17] if len(cells) > 17 else "",
                        "total_orders": cells[18] if len(cells) > 18 else "",
                        "delivery_new": cells[20] if len(cells) > 20 else "",
                        "delivery_used": cells[21] if len(cells) > 21 else "",
                        "total_deliveries": cells[22] if len(cells) > 22 else ""
                    }

    # 2. Fetch Whiteboard Activity
    r_wb = session.get(f"{base_url}/index.cfm?page=reports/dailyshowroom_v1.cfm&sg={sg}", timeout=25)
    soup_wb = BeautifulSoup(r_wb.text, "html.parser")
    whiteboard_entries = []
    for t in soup_wb.find_all("table"):
        for row in t.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) > 10 and cells[0].isdigit():
                whiteboard_entries.append({
                    "customer": cells[1],
                    "sale": cells[4],
                    "delivery": cells[5],
                    "trade_in_reg": cells[6],
                    "trade_in": cells[7],
                    "vehicle": cells[8],
                    "new_used": cells[9],
                    "salesperson": cells[13] if len(cells) > 13 else "",
                    "next_date": cells[15] if len(cells) > 15 else "",
                    "purpose": cells[16] if len(cells) > 16 else "",
                    "notes": cells[17] if len(cells) > 17 else ""
                })

    # 3. Fetch Inbox Approved OTPs & Quotes
    r_inbox = session.get(f"{base_url}/index.cfm?page=pages/inbox.cfm&sg={sg}", timeout=25)
    soup_inbox = BeautifulSoup(r_inbox.text, "html.parser")
    inbox_approvals = []
    seen_approvals = set()
    for tr in soup_inbox.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 3:
            d_txt = tds[0].get_text(strip=True)
            msg_txt = tds[1].get_text(strip=True)
            a_tag = tds[2].find("a")
            href = a_tag["href"] if a_tag and a_tag.has_attr("href") else ""
            m_cid = re.search(r"fViewQuote\(\s*\d+\s*,\s*[^,]+,\s*\d+\s*,\s*(\d+)\s*\)", href)
            if m_cid:
                cid = m_cid.group(1)
                key = f"{cid}_{msg_txt}"
                if key not in seen_approvals:
                    seen_approvals.add(key)
                    inbox_approvals.append({
                        "date": d_txt,
                        "type": "OTP" if "OTP" in msg_txt else "Quote",
                        "message": msg_txt,
                        "custid": cid
                    })

    # Look up details for customer IDs in approvals (top 5 most recent)
    enriched_approvals = []
    for app in inbox_approvals[:5]:
        cid = app["custid"]
        try:
            url_era = f"{base_url}/index.cfm?page=pages/customerera_selecttemplate.cfm&sg={sg}&custid={cid}"
            r_era = session.get(url_era, timeout=15)
            soup_era = BeautifulSoup(r_era.text, "html.parser")
            fn = soup_era.find("input", {"id": "forename"})
            sn = soup_era.find("input", {"id": "surname"})
            mb = soup_era.find("input", {"id": "mobile"})
            name = f"{fn.get('value', '') if fn else ''} {sn.get('value', '') if sn else ''}".strip()
            phone = mb.get("value", "") if mb else ""
            
            # Find vehicle in tables
            vehicles = []
            for tr_v in soup_era.find_all("tr"):
                txt_v = tr_v.get_text(strip=True)
                if "OTP Status" in txt_v or "Quote Status" in txt_v:
                    vehicles.append(txt_v)
            
            enriched_approvals.append({
                "custid": cid,
                "name": name or "Customer",
                "phone": phone,
                "date": app["date"],
                "type": app["type"],
                "deal_details": vehicles[0] if vehicles else ""
            })
        except Exception:
            pass

    return {
        "calendar": cal,
        "sales_metrics": sales_metrics,
        "totals_metrics": totals_metrics,
        "whiteboard": whiteboard_entries,
        "inbox_approvals": enriched_approvals
    }

def print_report(data):
    cal = data["calendar"]
    sm = data["sales_metrics"]
    wb = data["whiteboard"]
    apps = data["inbox_approvals"]

    print("=" * 65)
    print("DEALERSHIP SALES & PIPELINE AUDIT REPORT")
    print("=" * 65)

    if cal:
        p = cal["period"]
        tot_hrs = cal["total_hours"]
        print(f"Active Sales Month:    {p['month_name']}")
        print(f"Month-End Cutoff:      {format_day_first(p['month_end'])}")
        print(f"Remaining Trading Time: {format_hours_and_mins(tot_hrs)}")
        print("-" * 65)

    if sm:
        print("OFFICIAL MTD SALES PERFORMANCE (CRM)")
        print(f"Consultant:            {sm.get('salesperson', 'Sales Executive')}")
        print(f"Total Enquiries:       {sm.get('total_contacts', '0')} (Web: {sm.get('web')}, Tel: {sm.get('tel')}, Visit: {sm.get('visit')})")
        print(f"Follow-Up Touchpoints: {sm.get('follow_ups', '0')} (Ratio: {sm.get('follow_up_ratio')})")
        print(f"Test Drives:           {sm.get('test_drives', '0')} ({sm.get('test_drive_pct')})")
        print(f"Quotes Issued:         {sm.get('quotes', '0')}")
        print(f"OTPs Generated:        {sm.get('otps', '0')}")
        print(f"Finance Applications:  {sm.get('apps', '0')} (Approved: {sm.get('appr', '0')})")
        print(f"Total Orders (Sales):  {sm.get('total_orders', '0')} (New: {sm.get('orders_new')}, Used: {sm.get('orders_used')})")
        print(f"Official Deliveries:   {sm.get('total_deliveries', '0')} (New: {sm.get('delivery_new')}, Used: {sm.get('delivery_used')})")
        print(f"Conversion Rate:       {sm.get('conversion_pct', '0%')}")
        print("-" * 65)

    if wb:
        print("WHITEBOARD DELIVERIES & HANDOVERS")
        for e in wb:
            status_desc = "Delivered" if "deliver" in e['notes'].lower() or e['delivery'] == 'Yes' else "Sale Logged"
            print(f"• {e['customer']} - {e['vehicle']} ({e['new_used']})")
            print(f"  Status: {status_desc} | Notes: {e['notes'] or e['purpose']}")
            if e['trade_in']:
                print(f"  Trade-In: {e['trade_in']} (Reg: {e['trade_in_reg']})")
        print("-" * 65)

    if apps:
        print("RECENT APPROVED OTPS & QUOTES (HOT PIPELINE)")
        for a in apps[:6]:
            clean_deal = re.sub(r"(Quote Status|OTP Status|Submitted to F&I).*", "", a['deal_details']).strip()
            print(f"• {a['name']} (CustID: {a['custid']})")
            print(f"  Approval: {a['type']} on {a['date']}")
            if clean_deal:
                print(f"  Vehicle/Deal: {clean_deal}")
        print("-" * 65)

    print("=" * 65)

def main():
    parser = argparse.ArgumentParser(description="Track Deals & Performance Audit")
    parser.add_argument("--salesman", default=None, help="Salesperson name to query")
    parser.add_argument("--json", action="store_true", help="Output raw JSON data")
    args = parser.parse_args()

    data = fetch_performance_and_deals(salesman=args.salesman)
    if args.json:
        def json_serial(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")
        print(json.dumps(data, default=json_serial, indent=2))
    else:
        print_report(data)

if __name__ == "__main__":
    main()
