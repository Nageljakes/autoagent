---
name: Dealer CRM-portal
description: >-
  Automate actions on the Dealer Management Portal / CRM automotive dealer portal,
  including authenticated logins, whiteboard extracts, full 34+ entry diary pagination, ERA customer modal history extraction,
  lead likelihood scoring, and autonomous prospect note logging + diary moving.
---

# DMS / Dealer CRM Portal Automation

## Overview
This skill provides automated workflows for interacting with the DMS / Dealer CRM portal used by {DEALERSHIP_NAME} (`dealer-portal.example.com` / `dealer-portal.example.com`).

## Authentication & Credentials
- **Config File**: `~/.config/dealer_credentials.env` (Permissions: `chmod 600`)
- **Variables**: `CRM_USERNAME` and `CRM_PASSWORD`
- **Auth Endpoint**: `https://login.dealer-portal.example.com/checkserver.cfm`
- **Payload**: `dec1=<username>`, `dec2=<password>`
- **TLS Profile**: Requires Chrome 124 TLS signature via `curl_cffi` to maintain cookies (`CFID`, `CFTOKEN`, `COMPANYID`, `LOGINID`, `TCCODE`, `ISLOGGEDIN`).

## Consultant Identity & Outreach Naming (CRITICAL)
- In Dealer CRM Portal, {SALESPERSON_NAME}' official login username is `{CRM_USERNAME}`. Notes may reflect `[{CRM_USERNAME}]`.
- However, the consultant's name is strictly **{SALESPERSON_NAME}**.
- In all customer messages, follow-up texts, introductions, and WhatsApp outreach, ALWAYS introduce him as **{SALESPERSON_NAME}** (e.g. 'this is {SALESPERSON_NAME} from {DEALERSHIP_NAME}' / '{SALESPERSON_NAME} hier van {DEALERSHIP_NAME}').
- NEVER refer to him as '{CRM_USERNAME}' to customers, prospects, or in any outbound messages.

## Autonomous Customer Updates & Diary Rescheduling (Mandatory Protocol)
Whenever {SALESPERSON_NAME} (or user) shares feedback, contact outcomes, or status updates on a customer (e.g. "Customer did not answer, replied on WhatsApp asking for vehicle...", "Waiting on payslips / ID...", "Booked appointment for Friday..."):
1. **DO NOT** just reply with verbal placeholders like "I've logged that... we can reschedule his diary follow-up to tomorrow".
2. **IMMEDIATELY EXECUTE** the live update and diary move via `action_prospect.py`:
   ```bash
   PYTHONPATH=skills/Dealer CRM-portal/scripts python3 skills/Dealer CRM-portal/scripts/action_prospect.py \
     --query "<Customer Name or Phone>" \
     --note "<Summary of note/call outcome>" \
     --days 1
   ```
3. This single command automatically executes the **Dual-Logging Engine**:
   - Logs the touchpoint note and moves the diary follow-up off today to the target date via the Diary Action Form (`followup3.cfm`).
   - Automatically stamps the permanent note directly into the master ERA record under Customer Notes via the red "Add Note" endpoint (`customer_sa.cfc:addCustomerNotes`).
   - Recalculates lead likelihood score and updates `data/scratch/prospect_history.db`.
   - **STRICT LONG DASH BAN**: Notes logged to Dealer CRM must NEVER contain a long dash (em dash or en dash). Always use standard short hyphens (-) or natural punctuation.
4. Then deliver a crisp, natural confirmation acknowledging the note logged and the new diary follow-up date.

## Inbound Lead Auto-Acceptance & Outreach Protocol (Mandatory)
Whenever a new lead alert/notification is received (e.g. from {LEAD_NOTIFIER_NAME} or dealership CRM group):
1. **Accept Lead on Dealer CRM**: Run `scripts/accept_lead.py --all --json` to automatically claim and index the lead into the local CRM database.
2. **Add Contact to WhatsApp & Sync with Phone**:
   - Save the customer profile (Name, Phone number, and Vehicle Model) to WhatsApp.
   - Ensure "Sync with phone" is enabled so the contact is stored directly in the device address book.
3. **Automated WhatsApp Outreach (DISABLED)**:
   - **DO NOT** dispatch any outbound WhatsApp messages. The automation toggle is OFF. Wait for manual intervention.
4. **Log Note & Move Diary Entry**:
   - The newly accepted lead automatically lands on today's diary entries on Dealer CRM.
   - Immediately execute `scripts/action_prospect.py` to log the exact interaction note:
     `Lead accepted. Awaiting manual outreach.`
   - Move and reschedule the diary follow-up to tomorrow (`--days 1`).
5. **Audit & Log**: The outbound outreach, contact indexing, and diary move are logged directly to the audit log and `prospect_history.db`.

## Diary Presentation & Interactive Workflows (Mandatory)

### 1. Standard Diary Listing Protocol
Whenever the user asks to list a day's diary entries:
- Sweep and extract all prospects (via Load More pagination).
- Format every prospect using the strict deal card standard (separator lines go strictly BEFORE and AFTER each prospect card, never cutting through the middle of customer details):
  ```
  ═══════════════════════════════════════════════════════
  👤 {CUSTOMER NAME} | 🔥 {STAGE} (Score: {SCORE})
  📞 {PHONE}
  🚗 VEHICLE OF INTEREST: {VEHICLE}

  🎯 RECOMMENDED NEXT ACTION:
  👉 {Action}

  💬 WHATSAPP SNAPSHOT:
  • {WhatsApp interaction or verified outreach status}

  📌 KEY DEAL FACTS & DOSSIER:
  • Trade-in: {Trade-in status}
  • Finance / OTP: {Finance & OTP details}

  ⏱️ RECENT TOUCHPOINTS (Clean Chronological Timeline):
  • {Date (Time)} - {Note}
  ═══════════════════════════════════════════════════════
  ```

### 2. Interactive 5-by-5 Work-Through Protocol
Whenever the user says "Let's work through a day's diary" (or similar):
1. Query `prospect_history.db` ranked by `likelihood_score DESC` (highest conversion probability first).
2. Present **ONLY the first 5 highest-probability prospects** with:
   - Contact details & vehicle interest
   - Full interaction history & key notes
   - Recommended next action (call, appointment confirmation, WhatsApp)
3. Pause and let the user review, contact, or update those 5 prospects.
4. When the user gives feedback on any prospect, **immediately execute the live action script (`action_prospect.py`)** to update notes & move the diary entry before presenting the next batch.

## Complete Diary Extraction & Load More Pagination
- The initial `entries.cfm` page renders only the first 10 entries.
- To retrieve all diary prospects (e.g., all 34 entries):
  1. Extract active `sg`, `hiddenentriestype`, and `showhiddendate` from `entries.cfm`.
  2. Incrementally query the AJAX Load More endpoint:
     `index.cfm?page=includes/_showtableloadmoreentries.cfm&sg=<sg>&ajx`
     Parameters: `companyid=5784`, `loginid=247088`, `entriestype=today`, `tablecounter=<page_num>`, `start=<page_num>`, `showdate=<date>`, `perpage=10`.
  3. Loop `page_num = 2, 3, ...` until response payload drops below valid table threshold.
  4. Parse BOTH standard form rows (`custid`, `contactname`, `phoneno`, `purpose`, `contno`) and action buttons (`submitpage(<custid>)`).

## Customer ERA Modal & Full Historical Extraction
- **Modal Endpoint**: `index.cfm?page=pages/customerera_selecttemplate.cfm&sg=<active_sg>&custid=<custid>`
- **Vehicle of Interest & Enquiry History (Next Vehicle Tab)**:
  - Inside the Customer ERA profile, the **Next Vehicle / Enquiry History** section contains:
    - **Enquiry History Table**: Past & active vehicle enquiries (e.g. `2026 NEW SUV Model 1.0 AMT`, `2026 USED Double CabA DOUBLE CAB 2.5D PRO-2X`, etc.) with Status, Quote, OTP, and Sale flags.
    - **Next Vehicle Selection**: Dropdowns/fields (`nextmake`, `nextmodel`, `nextspecId`, `nextcarrequiredate`) indicating specific customer model and specification preferences.
- **Extracted Fields**:
  - Customer Profile: Forename, Surname, Mobile, Email, Address
  - Vehicle Specification: Make (`nextmake`), Model (`nextmodel`), Spec (`nextspecId`), Requirement Date (`nextcarrequiredate`)
  - Lead Source (`soe`): Facebook, Weblead, Showroom Walk-in
  - Historical Notes Feed: Regex parse `Logged by:\s*([^\n\r\|]+).*?Added Date:\s*([^\n\r\|]+).*?Notes:\s*([^\n\r\|]+)` to capture every call log and comment since lead creation.

## Prospect History Database & Lead Likelihood Scoring
- **Database**: SQLite WAL mode at `data/scratch/prospect_history.db`
- **Heuristic Conversion Scoring**:
  - **High Likelihood (75% - 99%)**: Fresh inbound leads (0-1 contacts, clean slate) or explicit buying signals (Appointment set, test drive requested, finance approval pending, WhatsApp active reply).
  - **Medium Likelihood (45% - 74%)**: Mid-stage follow-ups (2-4 contacts) with ongoing two-way communication (stock checks, trade-in valuations).
  - **Low Likelihood / Cold (5% - 44%)**: High contact fatigue (5+ attempts, repeated unresponsiveness, voicemail loops, 7th attempt, prior decline).

## Deal Heat Scoring & Buying Journey Classification
The system continuously calculates a **Deal Heat Score (0-100%)** combining CRM operational milestones and live WhatsApp signals:

- **Stage 1: Hot Money / Closing (Score 80-100%)**
  - High-velocity signals: Physical valuation (`Came in for evaluation`), application submitted (`Application received`), OTP/Quote approved, finance documents sent on WhatsApp.
  - Action: Immediate same-day outreach to finalize delivery and paperwork.
- **Stage 2: Active Evaluation (Score 60-79%)**
  - Signals: Trade-in appraisal requested, vehicle photos shared, active payment discussions, prompt WhatsApp responses.
  - Action: Push for test drive appointment and finalize trade-in numbers.
- **Stage 3: Information & Spec Selection (Score 40-59%)**
  - Signals: Model/spec inquiry in Next Vehicle tab, 1-3 contact attempts, occasional replies.
  - Action: Nurture with WhatsApp media, brochures, and vehicle walkarounds.
- **Stage 4: Cold / Fatigued (Score 20-39%)**
  - Signals: 5+ unanswered CRM calls, unread WhatsApp messages, no recent interaction.
  - Action: Low touch; relegate to batch Friday rescheduling sweeps.
- **Stage 5: Disqualified / Inactive (Score 0-19%)**
  - Signals: Credit decline with no co-signer, budget gap > R100k, bought elsewhere.
  - Action: Move diary out by 6 months or archive.

## Diary Briefing Document Standard (Executive Deal Card)
When generating PDF or text briefings of diary entries:
1. Format each prospect as an **Executive Deal Card**:
   - **Executive Header**: Name, Deal Heat Stage & Score, Phone, Vehicle of Interest (from Next Vehicle Selection / Enquiry History).
   - **Recommended Next Action**: Tactical recommendation placed directly at the top.
   - **WhatsApp Snapshot**: Concise summary of recent inbound/outbound messages.
   - **Clean Touchpoint Timeline**: Meaningful milestone dates & human notes only (stripping out automated repetitive CRM logs like generic `Lead, follow up`).
2. Ensure LaTeX / Pandoc compatibility by sanitizing special symbols (`#`, `&`, `$`, raw unicode emojis).

## WhatsApp Multi-Identifier Reconnaissance & Status Synthesis (Mandatory)
When compiling customer dossiers, deal heat scores, or diary briefings:
1. **Tier 1 - Direct Phone Query**: Query WhatsApp Monitor bridge (`GET http://127.0.0.1:9095/history/<phone>`).
2. **Tier 2 - Name & Message Content Search**: If phone query returns empty, search the bridge via `GET http://127.0.0.1:9095/search?q=<Customer Name>` and query SQLite `messages` table for matching contact names, message text, or customer quotes.
3. **Tier 3 - Audit Log & Quote Dispatch Cross-Check**: Check `explicit_send_audit_log` and `jax-shared/data/quotes` for quotes or messages dispatched to the customer.
4. **Tier 4 - Dealer CRM CRM Touchpoint Synthesis**: If no bridge thread exists, thoroughly cross-reference the customer's Dealer CRM ERA historical notes:
   - If notes state `"Sent quote"`, `"Sent introduction whatsapp"`, `"Two grey ticks"`, or `"Sent whatsapp"`, summarize this exact factual status (e.g. *"Quote dispatched via WhatsApp. Awaiting customer review."* or *"Outbound introduction sent (two grey ticks). Awaiting customer reply."*).
   - NEVER report *"No WhatsApp chat record found"* if Dealer CRM notes or audit logs explicitly document prior WhatsApp outreach or sent quotes.
5. **Strict Rule - Zero Hardcoded Hacks**: Never hardcode individual customer names or fabricated conversational states in generator scripts. Always resolve dynamically via the multi-tier engine.

## Note Architecture: When and Where to Put Notes

Dealer CRM / DMS provides two distinct entry points for adding notes. The choice of interface depends on whether the update is an operational touchpoint or persistent customer intelligence.

### 1. Diary Action Notes (Operational Touchpoint & Rescheduling)
- **Interface**: Diary Action Form (`adddiaryentry.cfm` -> `followup3.cfm`).
- **When to Use**:
  - Routine outreach attempts (unanswered calls, voicemail drops, WhatsApp sent).
  - Incoming customer replies or short status checks.
  - Advancing the sales cycle and setting the next contact milestone.
- **Operational Effect**:
  - Increments the customer contact count (`contno`).
  - Moves the prospect off today's diary queue to the new follow-up date (`days` / `target_date`).
  - Updates the active timeline in `prospect_history.db`.
- **Example Content**:
  > "Attempted call, no answer. Sent WhatsApp follow-up regarding Magnite Acenta CVT stock. Follow up tomorrow."

### 2. Customer Modal Detailed Notes (The Deal & Profile Dossier)
- **Interface**: Customer Modal -> Notes Section (`customerera_selecttemplate.cfm` / Customer ERA record).
- **When to Use**:
  - Recording permanent deal intelligence and customer preferences.
  - Trade-in vehicle particulars (Make, Model, Year, Mileage, Settlement, Condition).
  - Detailed finance & affordability context (Salary/Self-employed, Budget cap, Pre-approval status, Documents received).
  - Specific vehicle requirements (Trim, Colour, Transmission, Accessory requests).
  - Buyer persona and lifestyle context (Business vs private, family requirements).
- **Operational Effect**:
  - Enriches the permanent master ERA customer file visible to sales managers and consultants.
  - Does NOT alter, reschedule, or disrupt scheduled diary follow-up dates.
- **Example Content**:
  > "Deal Dossier: Interested in Compact Crossover 1.0T Acenta CVT in Pearl White. Trade-in: 2019 Ford EcoSport 1.5 TDCi (~85k km, settlement approx R95k). Payslips and ID received; finance application pre-check pending."

### 3. Automated Dual-Logging Engine (`action_prospect.py`)
To prevent deal dossiers and touchpoint notes from being fragmented:
- `action_prospect.py` automatically submits every update to BOTH interfaces simultaneously:
  1. **Diary Action Form (`followup3.cfm`)**: Increments contact count, advances diary cadence, and clears the lead off today's list.
  2. **Customer Notes (`customer_sa.cfc:addCustomerNotes`)**: Posts the note directly into the master ERA file (equivalent to clicking the red "Add Note" button under Customer Notes).
- Dealership staff and managers opening the customer file immediately see all logged interactions without requiring separate manual entries.

## Helper Scripts
- `scripts/action_followup.py`: Autonomous customer follow-up engine that performs 4-tier context & language pre-analysis (Afrikaans vs. English), unifies mobile LIDs, drafts 1-2 sentence messages, dispatches via WhatsApp bridge, and dual-logs to Dealer CRM.
- `scripts/action_prospect.py`: Automatically log interaction notes, reschedule diary follow-ups, and update likelihood scores in a single command.
- `scripts/portal_login.py`: Authenticate and start persistent session.
- `scripts/move_diary_entries.py`: Batch reschedule diary follow-ups with zero-remaining sweep.
- `scripts/prospect_db.py`: SQLite schema and lead likelihood evaluation engine.
- `scripts/populate_all_34_diaries.py`: Extract all 34 diary entries via Load More and populate prospect histories.
- `scripts/explore_portal.py`: Map all menu items, tools, and endpoints.
