#!/usr/bin/env python3
"""
BB Motorgroup Business Calendar & Month-End Calculation Tool
Computes sales month allocation, working hours countdown, and delivery cutoffs.
"""

import argparse
import json
import os
import sys
from datetime import datetime, date, time, timedelta

# Reference path
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CALENDAR_JSON = os.path.join(SCRIPT_DIR, "..", "references", "calendar_2026.json")

def load_calendar_data():
    with open(CALENDAR_JSON, "r") as f:
        return json.load(f)

def parse_iso_or_sast(dt_str):
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except ValueError:
            pass
    raise ValueError(f"Unable to parse datetime '{dt_str}'. Expected format: YYYY-MM-DD HH:MM or DD/MM/YYYY HH:MM.")

def format_day_first(dt):
    # E.g. "Wed 23 Sep 2026, 12:00 SAST"
    return dt.strftime("%a %d %b %Y, %H:%M SAST")

def format_date_day_first(d):
    return d.strftime("%a %d %b %Y")

def get_sales_period_for_delivery(delivery_dt, calendar_data):
    """
    Find which sales month this delivery timestamp belongs to.
    """
    months = calendar_data["sales_months_2026"]
    
    # Check bounds
    first_end = datetime.strptime(months[0]["month_end"], "%Y-%m-%d %H:%M:%S")
    last_end = datetime.strptime(months[-1]["month_end"], "%Y-%m-%d %H:%M:%S")
    
    if delivery_dt > last_end:
        return {
            "status": "NOT_ON_FILE",
            "message": f"Delivery on {format_day_first(delivery_dt)} falls after the 2026 December cutoff ({format_day_first(last_end)}). 2027 calendar is not on file - do not guess."
        }
    
    for m in months:
        m_end = datetime.strptime(m["month_end"], "%Y-%m-%d %H:%M:%S")
        if m["period_starts_after"]:
            m_start = datetime.strptime(m["period_starts_after"], "%Y-%m-%d %H:%M:%S")
            if m_start < delivery_dt <= m_end:
                return {
                    "status": "OK",
                    "month_name": m["month_name"],
                    "month_end": m_end,
                    "period_start": m_start,
                    "start_desc": m["note"]
                }
        else:
            # January 2026
            if delivery_dt <= m_end:
                return {
                    "status": "OK",
                    "month_name": m["month_name"],
                    "month_end": m_end,
                    "period_start": None,
                    "start_desc": m["note"]
                }

    return {
        "status": "NOT_ON_FILE",
        "message": f"Delivery date {format_day_first(delivery_dt)} is before 2026 calendar records (Dec 2025 month-end not on file)."
    }

def get_current_sales_month(current_dt, calendar_data):
    return get_sales_period_for_delivery(current_dt, calendar_data)

def calculate_working_hours_breakdown(start_dt, end_dt, calendar_data):
    """
    Calculate working hours between start_dt and end_dt.
    Monday - Friday: 07:30 to 17:30 (10 hrs)
    Saturday: 08:00 to 13:00 (5 hrs)
    Sunday: Closed
    Public Holidays: Closed
    End date cutoff: terminates at min(normal end, end_dt.time())
    """
    holidays = set()
    for h_str in calendar_data.get("public_holidays_2026", []):
        holidays.add(datetime.strptime(h_str, "%Y-%m-%d").date())
        
    if start_dt >= end_dt:
        return 0.0, []
        
    current = start_dt
    total_seconds = 0
    daily_breakdown = []
    
    while current.date() <= end_dt.date():
        c_date = current.date()
        is_holiday = c_date in holidays
        weekday = c_date.weekday() # 0=Mon, 5=Sat, 6=Sun
        
        if weekday == 6 or is_holiday:
            reason = "Public Holiday" if is_holiday else "Sunday"
            daily_breakdown.append({
                "date": c_date,
                "label": format_date_day_first(c_date),
                "status": f"Closed ({reason})",
                "hours": 0.0
            })
            current = datetime.combine(c_date + timedelta(days=1), time(0, 0))
            continue
            
        if weekday == 5: # Sat
            w_start = time(8, 0)
            w_end = time(13, 0)
        else: # Mon - Fri
            w_start = time(7, 30)
            w_end = time(17, 30)
            
        # If this day is the cutoff day
        if c_date == end_dt.date():
            w_end = min(w_end, end_dt.time())
            
        day_start = max(current, datetime.combine(c_date, w_start))
        day_end = min(datetime.combine(c_date, w_end), end_dt if c_date == end_dt.date() else datetime.combine(c_date, w_end))
        
        if day_start < day_end:
            secs = (day_end - day_start).total_seconds()
            total_seconds += secs
            daily_breakdown.append({
                "date": c_date,
                "label": format_date_day_first(c_date),
                "status": f"{day_start.strftime('%H:%M')} to {day_end.strftime('%H:%M')}",
                "hours": secs / 3600.0
            })
        else:
            daily_breakdown.append({
                "date": c_date,
                "label": format_date_day_first(c_date),
                "status": "Outside Trading Hours",
                "hours": 0.0
            })
            
        current = datetime.combine(c_date + timedelta(days=1), time(0, 0))
        
    return total_seconds / 3600.0, daily_breakdown

def format_hours_and_mins(hours_float):
    hrs = int(hours_float)
    mins = int(round((hours_float - hrs) * 60))
    if mins == 60:
        hrs += 1
        mins = 0
    return f"{hrs}h {mins:02d}m ({hours_float:.2f} hrs)"

def cmd_status(current_dt, calendar_data):
    curr_month = get_current_sales_month(current_dt, calendar_data)
    print("=" * 65)
    print(f"BB MOTORGROUP SALES CALENDAR STATUS")
    print("=" * 65)
    print(f"Current Timestamp:     {format_day_first(current_dt)}")
    
    if curr_month["status"] != "OK":
        print(f"Status:                {curr_month['message']}")
        return
        
    print(f"Active Sales Month:    {curr_month['month_name']}")
    start_str = format_day_first(curr_month['period_start']) if curr_month['period_start'] else curr_month['start_desc']
    print(f"Sales Period:          {start_str}  --->  {format_day_first(curr_month['month_end'])}")
    print(f"Month-End Cutoff:      {format_day_first(curr_month['month_end'])}")
    print("-" * 65)
    
    tot_hours, breakdown = calculate_working_hours_breakdown(current_dt, curr_month['month_end'], calendar_data)
    print(f"Total Working Time:    {format_hours_and_mins(tot_hours)}")
    print("\nDaily Trading Breakdown:")
    for b in breakdown:
        print(f"  * {b['label']:<16}: {b['status']:<25} ({b['hours']:.2f} hrs)")
    print("=" * 65)

def cmd_delivery(delivery_str, calendar_data):
    # If only date given, check if it's a month-end date
    is_date_only = len(delivery_str.strip().split()) == 1
    delivery_dt = parse_iso_or_sast(delivery_str)
    
    # Check if this date matches any month-end date
    d_date = delivery_dt.date()
    matched_month_end = None
    for m in calendar_data["sales_months_2026"]:
        m_end = datetime.strptime(m["month_end"], "%Y-%m-%d %H:%M:%S")
        if m_end.date() == d_date:
            matched_month_end = m
            break
            
    if is_date_only and matched_month_end:
        print(f"⚠️  AMBIGUOUS DELIVERY TIME:")
        print(f"'{format_date_day_first(d_date)}' is an official month-end date for {matched_month_end['month_name']}!")
        print(f"The 12:00 SAST cutoff determines whether this unit counts for {matched_month_end['month_name']} or the following month.")
        print("Please provide the exact handover time (e.g. '11:30' or '14:00').")
        return
        
    result = get_sales_period_for_delivery(delivery_dt, calendar_data)
    print("=" * 65)
    print(f"DELIVERY SALES MONTH ALLOCATION")
    print("=" * 65)
    print(f"Delivery Timestamp:    {format_day_first(delivery_dt)}")
    if result["status"] != "OK":
        print(f"Result:                {result['message']}")
        return
        
    start_str = format_day_first(result['period_start']) if result['period_start'] else result['start_desc']
    print(f"Counts For:            {result['month_name']}")
    print(f"Sales Month Window:    {start_str}  to  {format_day_first(result['month_end'])}")
    
    # Clarify if delivered on month end
    if delivery_dt.date() == result['month_end'].date():
        print(f"Note:                  Delivered on month-end date before 12:00 cutoff -> counts for {result['month_name']}.")
    elif result['period_start'] and delivery_dt.date() == result['period_start'].date():
        print(f"Note:                  Delivered on previous month-end date after 12:00 cutoff -> counts for {result['month_name']}.")
    print("=" * 65)

def cmd_table(calendar_data):
    print("=" * 70)
    print(f"{'Sales Month':<16} | {'Month-End (12:00 Cutoff)':<22} | {'Period Starts After':<24}")
    print("=" * 70)
    for m in calendar_data["sales_months_2026"]:
        m_end = datetime.strptime(m["month_end"], "%Y-%m-%d %H:%M:%S")
        m_end_str = format_day_first(m_end).replace(" SAST", "")
        start_desc = m["note"]
        print(f"{m['month_name']:<16} | {m_end_str:<22} | {start_desc:<24}")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="BB Motorgroup Business Calendar Engine")
    parser.add_argument("--status", action="store_true", help="Display current sales month and working hours countdown")
    parser.add_argument("--countdown", action="store_true", help="Display remaining working hours to month-end")
    parser.add_argument("--delivery", type=str, help="Determine sales month for a delivery timestamp")
    parser.add_argument("--table", action="store_true", help="Show full 2026 month-end calendar")
    parser.add_argument("--now", type=str, help="Override current timestamp (for testing/simulations)")
    
    args = parser.parse_args()
    data = load_calendar_data()
    
    if args.now:
        current_dt = parse_iso_or_sast(args.now)
    else:
        # Dealership operates in SAST (UTC+2)
        utc_now = datetime.utcnow()
        current_dt = utc_now + timedelta(hours=2)
        
    if args.delivery:
        cmd_delivery(args.delivery, data)
    elif args.table:
        cmd_table(data)
    elif args.countdown:
        curr_month = get_current_sales_month(current_dt, data)
        if curr_month["status"] == "OK":
            tot, _ = calculate_working_hours_breakdown(current_dt, curr_month["month_end"], data)
            print(f"Remaining working time for {curr_month['month_name']}: {format_hours_and_mins(tot)}")
        else:
            print(curr_month["message"])
    else:
        cmd_status(current_dt, data)

if __name__ == "__main__":
    main()
