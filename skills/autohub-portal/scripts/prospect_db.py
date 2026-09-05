#!/usr/bin/env python3
"""
Lightweight Prospect History & Lead Probability Database
Stores interaction histories, attempts, and automatically evaluates
sale likelihood (Hot Fresh Leads vs. Fatigued/Declined Prospects).
"""

import sys
import sqlite3
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "jax-shared" / "scripts"))
from database_paths import PROSPECT_HISTORY_DB as DB_PATH, SQLITE_DB_PATH as WA_DB_PATH

def init_db(db_path: Path = DB_PATH):
    """Initializes SQLite database with WAL mode and necessary tables."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        
        # Prospects master table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS prospects (
            custid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            vehicle_model TEXT,
            contact_count INTEGER DEFAULT 1,
            status TEXT DEFAULT 'ACTIVE',
            likelihood_tier TEXT DEFAULT 'MEDIUM',
            likelihood_score INTEGER DEFAULT 50,
            likelihood_reason TEXT,
            last_diary_date TEXT,
            last_purpose TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Detailed history logs per prospect
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS prospect_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            custid TEXT NOT NULL,
            entry_date TEXT,
            contact_type TEXT,
            note TEXT,
            sentiment TEXT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(custid) REFERENCES prospects(custid)
        );
        """)

        # Fast indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prospects_score ON prospects(likelihood_score DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prospects_phone ON prospects(phone);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_custid ON prospect_notes(custid);")

def evaluate_likelihood(name: str, phone: str, contact_count: int, purpose: str, notes_history: List[str] = None) -> Tuple[str, int, str]:
    """
    Evaluates conversion likelihood based on history, contact count, and note sentiment.
    Returns: (Tier: HIGH/MEDIUM/LOW, Score: 0-100, Reason: str)
    """
    if notes_history is None:
        notes_history = []
    
    combined_text = (purpose + " " + " ".join(notes_history)).lower()
    score = 60 # Base score

    # 1. Freshness Bonus / Penalty based on contact count
    if contact_count <= 1:
        score += 30
        freshness_tag = "Fresh lead (0-1 contacts)"
    elif contact_count == 2:
        score += 15
        freshness_tag = "Early prospect (2 contacts)"
    elif contact_count <= 4:
        score -= 5
        freshness_tag = f"Mid-stage follow-up ({contact_count} contacts)"
    elif contact_count <= 6:
        score -= 25
        freshness_tag = f"Contact fatigue ({contact_count} contacts)"
    else:
        score -= 40
        freshness_tag = f"High fatigue ({contact_count}+ attempts)"

    # 2. Positive Intent Keywords
    positive_signals = []
    if any(k in combined_text for k in ["appointment", "set appointment", "coming in", "visit"]):
        score += 25
        positive_signals.append("Appointment scheduled")
    if any(k in combined_text for k in ["quote", "otp", "payslip", "finance", "re-apply", "re apply", "credit"]):
        score += 15
        positive_signals.append("Active finance/buying step")
    if any(k in combined_text for k in ["stock", "test drive", "used option"]):
        score += 10
        positive_signals.append("Product/stock interest")

    # 3. Negative / Resistance Keywords
    negative_signals = []
    if any(k in combined_text for k in ["declined", "declined before", "not interested", "bought elsewhere", "cancelled"]):
        score -= 50
        negative_signals.append("Prior decline")
    if any(k in combined_text for k in ["7th attempt", "no answer", "unreachable", "ghosting", "voicemail", "left message", "no response"]):
        score -= 30
        negative_signals.append("Repeated no-response")
    if any(k in combined_text for k in ["bad credit", "poor credit", "blacklisted", "rejected"]):
        score -= 40
        negative_signals.append("Credit issue")

    # Bound score between 5 and 99
    score = max(5, min(99, score))

    if score >= 75:
        tier = "HIGH"
    elif score >= 45:
        tier = "MEDIUM"
    else:
        tier = "LOW"

    reasons = [freshness_tag] + positive_signals + negative_signals
    reason_str = " | ".join(reasons)

    return tier, score, reason_str

def upsert_prospect(custid: str, name: str, phone: str = "", vehicle: str = "", contact_count: int = 1, purpose: str = "", notes: List[str] = None, last_date: str = "") -> Dict:
    """Inserts or updates a prospect and recalculates their sale likelihood."""
    init_db()
    tier, score, reason = evaluate_likelihood(name, phone, contact_count, purpose, notes)
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT INTO prospects (custid, name, phone, vehicle_model, contact_count, likelihood_tier, likelihood_score, likelihood_reason, last_diary_date, last_purpose, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(custid) DO UPDATE SET
            name=excluded.name,
            phone=COALESCE(NULLIF(excluded.phone, ''), prospects.phone),
            vehicle_model=COALESCE(NULLIF(excluded.vehicle_model, ''), prospects.vehicle_model),
            contact_count=excluded.contact_count,
            likelihood_tier=excluded.likelihood_tier,
            likelihood_score=excluded.likelihood_score,
            likelihood_reason=excluded.likelihood_reason,
            last_diary_date=excluded.last_diary_date,
            last_purpose=excluded.last_purpose,
            last_updated=CURRENT_TIMESTAMP;
        """, (custid, name, phone, vehicle, contact_count, tier, score, reason, last_date, purpose))

        if purpose:
            cursor.execute("""
            INSERT INTO prospect_notes (custid, entry_date, note, sentiment)
            VALUES (?, ?, ?, ?)
            """, (custid, last_date, purpose, tier))

    return {
        "custid": custid,
        "name": name,
        "phone": phone,
        "vehicle": vehicle,
        "tier": tier,
        "score": score,
        "reason": reason
    }

def get_prospect(custid: str) -> Optional[Dict]:
    """Retrieves full profile and history for a given prospect."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        row = cursor.execute("SELECT * FROM prospects WHERE custid = ?", (custid,)).fetchone()
        if not row:
            return None
        
        prospect = dict(row)
        notes = cursor.execute("SELECT entry_date, note, sentiment, recorded_at FROM prospect_notes WHERE custid = ? ORDER BY id DESC", (custid,)).fetchall()
        prospect["notes_history"] = [dict(n) for n in notes]
    return prospect

def get_scored_diary_list() -> List[Dict]:
    """Returns all prospects ranked by likelihood score (Highest conversion first)."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute("SELECT * FROM prospects ORDER BY likelihood_score DESC, contact_count ASC").fetchall()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully!")
