#!/usr/bin/env python3
"""
Download a customer's quote PDF from the Dealer CRM / DMS Portalportal.

Usage:
  python3 download_quote.py --custid <custid> [--ref <ref-number-or-substring>]
  python3 download_quote.py --name "Duduzile" [--ref 036278]

If --custid is omitted, looks up the customer by fuzzy name match in
prospect_history.db. If --ref is omitted, downloads the most recent quote
listed in the customer's ERA modal.

How it works (discovered by tracing the actual portal flow for a real quote):
  1. Log in and load customerera_selecttemplate.cfm?custid=<custid> - this
     modal lists each quote as an <a onclick="fViewQuote(quoteId, docType, ver)">
     with the vehicle description and [Ref:NNNNNN] in its link text.
  2. Fetch quote_frame.cfm with those (quoteId, docType, ver) params - this
     HTML page embeds the *actual* generated PDF via
     <embed src=".../workspace/quote_otp/temp/Quote-<ref>-....pdf">
     (NOT the quote_pdf.cfm endpoint directly - that returns a JS stub, not a PDF).
  3. GET that embed src with the same authenticated session to get the real
     PDF bytes.

Saves to jax-shared/data/quotes/<safe-filename>.pdf and prints
the saved path on stdout (last line) so a caller can capture it.
"""
import sys
import os
import re
import argparse
import sqlite3
from pathlib import Path
from curl_cffi import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).parent))
from portal_login import login, load_credentials_from_env_file

PROSPECT_DB = Path("data/scratch/prospect_history.db")
QUOTES_DIR = Path("jax-shared/data/quotes")


def find_custid_by_name(name: str):
    if not PROSPECT_DB.exists():
        return None
    conn = sqlite3.connect(str(PROSPECT_DB))
    try:
        row = conn.execute(
            "SELECT custid, name FROM prospects WHERE name LIKE ? ORDER BY last_updated DESC LIMIT 1",
            (f"%{name}%",)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_quote(custid: str, ref: str = None, impersonate: str = "chrome124"):
    user, pwd = load_credentials_from_env_file()
    session, res = login(user, pwd, impersonate=impersonate)
    if not session:
        print("Login failed", file=sys.stderr)
        return None

    qs_match = re.search(r"sg=([^&]+)", res.url)
    sg = qs_match.group(1) if qs_match else ""
    if not sg:
        soup = BeautifulSoup(res.text, "html.parser")
        sg_input = soup.find("input", {"name": "sg"})
        sg = sg_input["value"] if sg_input else ""

    modal_url = f'{os.getenv("CRM_BASE_URL", "https://egm.dealer-crm.co.za")}/index.cfm?page=pages/customerera_selecttemplate.cfm&sg={sg}&custid={custid}'
    modal_resp = session.get(modal_url, timeout=20)
    modal_soup = BeautifulSoup(modal_resp.text, "html.parser")

    candidates = []
    for a in modal_soup.find_all("a"):
        attrs_text = (a.get("href", "") or "") + " " + (a.get("onclick", "") or "")
        m = re.search(r"fViewQuote\((\d+),\s*(\d+),\s*(\d+)\)", attrs_text)
        if m:
            candidates.append({
                "quoteId": m.group(1),
                "docType": m.group(2),
                "ver": m.group(3),
                "text": a.text.strip(),
            })

    if not candidates:
        print("No quotes found in customer ERA modal.", file=sys.stderr)
        return None

    chosen = None
    if ref:
        for c in candidates:
            if ref in c["text"]:
                chosen = c
                break
    if not chosen:
        chosen = candidates[0]

    frame_url = (
        f'{os.getenv("CRM_BASE_URL", "https://egm.dealer-crm.co.za")}/index.cfm?page=../southafrica/pages/quote_frame.cfm'
        f"&sg={sg}&custId={custid}&documenttype={chosen['docType']}"
        f"&quoteId={chosen['quoteId']}&quote_version={chosen['ver']}"
    )
    frame_resp = session.get(frame_url, timeout=20)
    embed_match = re.search(r'<embed src="([^"]+\.pdf)"', frame_resp.text)
    if not embed_match:
        print("Could not find embedded PDF link in quote frame.", file=sys.stderr)
        return None

    pdf_url = embed_match.group(1)
    pdf_resp = session.get(pdf_url, timeout=20)
    if not pdf_resp.content.startswith(b"%PDF"):
        print(f"Fetched file at {pdf_url} is not a real PDF.", file=sys.stderr)
        return None

    QUOTES_DIR.mkdir(parents=True, exist_ok=True)
    filename = os.path.basename(pdf_url) or f"quote_{chosen['quoteId']}.pdf"
    safe_name = re.sub(r"[^A-Za-z0-9_.\-]", "_", filename)
    out_path = QUOTES_DIR / safe_name
    out_path.write_bytes(pdf_resp.content)

    print(f"Saved quote: {chosen['text']}", file=sys.stderr)
    print(str(out_path))
    return str(out_path)


def main():
    parser = argparse.ArgumentParser(description="Download a customer quote PDF from Dealer CRM")
    parser.add_argument("--custid", help="Dealer CRM customer ID")
    parser.add_argument("--name", help="Customer name to look up custid via prospect_history.db")
    parser.add_argument("--ref", help="Ref number or substring to select which quote (defaults to most recent)")
    args = parser.parse_args()

    custid = args.custid
    if not custid and args.name:
        custid = find_custid_by_name(args.name)
        if not custid:
            print(f"No prospect found matching name '{args.name}'", file=sys.stderr)
            sys.exit(1)

    if not custid:
        print("Must provide --custid or --name", file=sys.stderr)
        sys.exit(1)

    result = get_quote(custid, args.ref)
    if not result:
        sys.exit(1)


if __name__ == "__main__":
    main()
