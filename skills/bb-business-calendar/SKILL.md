---
name: bb-business-calendar
description: Resolve BB Motorgroup sales months, working-hour countdowns, and delivery cutoffs.
---

# BB Motorgroup Business Calendar & Month-End Skill

Provides deterministic calculations and authoritative month-end cutoff tracking for BB Motorgroup sales operations. The dealership does not follow calendar months; this skill enforces the 12:00 SAST cutoff, physical handover rules, and trading-hour countdowns.

## When to Use
- User asks about "this month", Month-to-Date (MTD), targets, quotas, or forecasts.
- Calculating remaining dealership working hours to month-end.
- Determining which sales month a vehicle handover counts towards.
- Triaging pending deals as month-end cutoff approaches.
- Validating delivery schedules against dealership trading hours (Mon-Fri 07:30-17:30, Sat 08:00-13:00, Sun closed).
- Do not use for: General calendar arithmetic unrelated to BB Motorgroup dealership operations.

## Source of Truth: 2026 Month-End Table
All dates are 12:00 SAST cutoffs:

| Sales Month  | Month-End (Cutoff 12:00 SAST) | Period Starts After 12:00 On   |
| :--- | :--- | :--- |
| January 2026 | Fri 23 Jan 2026 | Dec 2025 month-end (not on file) |
| February 2026 | Mon 23 Feb 2026 | Fri 23 Jan 2026 |
| March 2026 | Mon 23 Mar 2026 | Mon 23 Feb 2026 |
| April 2026 | Thu 23 Apr 2026 | Mon 23 Mar 2026 |
| May 2026 | Tue 26 May 2026 | Thu 23 Apr 2026 |
| June 2026 | Thu 25 Jun 2026 | Tue 26 May 2026 |
| July 2026 | Fri 24 Jul 2026 | Thu 25 Jun 2026 |
| August 2026 | Tue 25 Aug 2026 | Fri 24 Jul 2026 |
| September 2026 | Wed 23 Sep 2026 | Tue 25 Aug 2026 |
| October 2026 | Fri 23 Oct 2026 | Wed 23 Sep 2026 |
| November 2026 | Mon 23 Nov 2026 | Fri 23 Oct 2026 |
| December 2026 | Tue 22 Dec 2026 | Mon 23 Nov 2026 |
| January 2027 | Not on file (needs 2027 calendar) | Tue 22 Dec 2026 |

*Public Holidays 2026 (Dealership closed unless specified):*
1 Jan, 21 Mar, 3 Apr, 5 Apr, 6 Apr, 27 Apr, 1 May, 16 Jun, 9 Aug, 10 Aug, 24 Sep, 16 Dec, 25 Dec, 26 Dec.

## CLI Execution Tool
Always use the deterministic helper script rather than mental math:

### 1. Current Status & Working-Hours Countdown
Shows the current active sales month, cutoff date, and exact remaining working hours with daily breakdown:
```bash
python3 ~/.gemini/config/skills/bb-business-calendar/scripts/calendar_calc.py --status
```

### 2. Determine Which Month a Delivery Counts For
Specify date and time in SAST (`YYYY-MM-DD HH:MM` or `DD/MM/YYYY HH:MM`):
```bash
python3 ~/.gemini/config/skills/bb-business-calendar/scripts/calendar_calc.py --delivery "2026-09-23 11:30"
python3 ~/.gemini/config/skills/bb-business-calendar/scripts/calendar_calc.py --delivery "2026-09-23 14:00"
```

### 3. Print Full Reference Table
```bash
python3 ~/.gemini/config/skills/bb-business-calendar/scripts/calendar_calc.py --table
```

## Core Behavioral Guardrails
1. **Physical Handover Decides:** A deal counts only when delivered to the customer. Signed OTP or finance approvals do not count.
2. **12:00 SAST Cutoff:** Handover <= 12:00 on month-end date = closing month. Handover > 12:00 = next month.
3. **No Backdating:** Never suggest or alter a delivery timestamp to meet a month cutoff.
4. **Calendar Boundaries:** If a date falls outside 2026 (Dec 2025 or 2027 onwards), state that the calendar is not on file. Do not guess.
5. **Format:** Day-first dates (`Wed 23 Sep 2026`), 24-hour SAST (`12:00`).

## Verification
Validate frontmatter and script execution:
```bash
python3 -c '
import yaml, pathlib
content = pathlib.Path("SKILL.md").read_text()
fm = yaml.safe_load(content.split("---")[1])
assert set(fm) == {"name", "description"}
assert len(fm["description"]) < 120 and fm["description"].endswith(".")
'
```
