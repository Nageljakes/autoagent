---
name: dealership-os-architecture
description: >-
  Architectural patterns, domain models, and workflows for automotive dealership operating systems (DMS/CRM),
  including multi-bot departmental coordination, the 7-milestone workshop lifecycle, SA PDF417 license disc parsing,
  granular quote authorizations, and Dealer CRM integration.
---

# Dealership Operating System & Multi-Agent CRM Architecture

> **⚠️ ROADMAP - NOT IMPLEMENTED.** This document describes a planned future system (workshop milestones, PDF417 license disc parsing, WhatsApp payments, sentiment escalation, the desktop dashboard, etc.). None of it is running today - the only live systems are the WhatsApp/Telegram sales bots, the WhatsApp lead monitor, and the Dealer CRM portal automation described in the `Dealer CRM-portal` and `whatsapp-monitor` skills. Do not tell a customer or {SALESPERSON_NAME} that any capability described below currently exists or is available.

## Core Philosophy
- **Dual-Interface Model**: Fast on-the-fly conversational bots (WhatsApp/Telegram) for floor staff + role-based responsive desktop dashboards (HTMX/Tailwind) for desk staff.
- **Sub-100MB RAM Footprint**: Single-process async FastAPI + SQLite (WAL mode) + event bus.
- **Project Directory**: `data/scratch/tiny_crm/`

## The 7 Core Workshop Milestones
1. `1_CheckedIn`: Driveway reception, license disc scanned, walkaround photos saved.
2. `2_AtServiceBay`: Vehicle in bay, technician clocked in.
3. `3_HealthCheckDone`: eVHC inspection complete, 45-second video/voice note proof uploaded, parts quoted.
4. `4_JobComplete`: Mechanical service and repairs finished, QC signed off.
5. `5_VehicleWashed`: Cleaned, vacuumed, and valeted.
6. `6_ReadyForCollection`: Parked in handover bay, invoice verified.
7. `7_CustomerInformed`: WhatsApp/SMS notification sent with collection PIN and gate pass.

## Advanced Dealership Workflows
- **Granular Line-Item Approvals**: Customers can selectively approve mandatory items (e.g. brakes) while deferring optional items (e.g. wipers) via WhatsApp.
- **VIN Parts Catalog & Counter Approval**: Auto-matches part numbers -> Parts Specialist verifies stock & price -> Bundles with technician labor.
- **SA PDF417 License Disc Parser**: Decodes `%<DiscNo>%...%<RegNo>%...%<VIN>%<EngineNo>%<ExpiryDate>%` directly from windscreen photos.
- **Skill- & Bay-Aware Dispatching**: Routes jobs by technician certification (Master Tech vs Apprentice) and bay equipment (Alignment vs 2-post lift).
- **Bottleneck Threshold Timers**: Automatic escalations if a job sits in parts/wash bay beyond flat-rate targets.
- **Account Ownership Lead Routing**: Cross-references customer numbers before round-robin to preserve sales rep relationships.
- **Automated Lead Pre-Qualification**: Captures trade-in, finance vs cash, deposit, and budget during after-hours/delivery cover.
- **Integrated WhatsApp Payments**: Pre-settlement via instant EFT / card links (Ozow, Peach Payments).
- **Smart Sentiment Escalation**: Real-time frustration detection automatically pauses bot replies and alerts the Service Advisor.

## Specifications Directory
Detailed blueprints for all 15 dealership roles live in:
`data/scratch/tiny_crm/`
