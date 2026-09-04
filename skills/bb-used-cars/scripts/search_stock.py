#!/usr/bin/env python3
"""
BB Dealerships Used / Pre-Owned Inventory Search Tool
Instant search using local pre-cached inventory (stock.json).
Supports price range filtering, keyword matching, and vehicle dossier fields
(Stock ID, VIN, MM Code, Body, Fuel, Transmission, Color).
"""

import sys
import os
import argparse
import json
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STOCK = os.path.abspath(os.path.join(SCRIPT_DIR, "../../../jax-shared/data/inventory/stock.json"))
STOCK_FILE = os.environ.get("STOCK_FILE", DEFAULT_STOCK if os.path.exists(DEFAULT_STOCK) else "jax-shared/data/inventory/stock.json")

def search_cached_stock(query=None, min_price=None, max_price=None, dealer=None,
                        stock_id=None, vin=None, mm_code=None, body=None, color=None):
    if not os.path.exists(STOCK_FILE):
        return []

    try:
        with open(STOCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    vehicles = data.get("vehicles", [])
    filtered = []
    q_terms = query.lower().split() if query else []

    for v in vehicles:
        # Dealership filter
        if dealer:
            if dealer.lower() not in v.get("branch", "").lower():
                continue

        # Specific field filters
        if stock_id and stock_id.lower() not in str(v.get("stock_id", "")).lower():
            continue
        if vin and vin.lower() not in str(v.get("vin", "")).lower():
            continue
        if mm_code and mm_code.lower() not in str(v.get("mm_code", "")).lower():
            continue
        if body and body.lower() not in str(v.get("body", "")).lower():
            continue
        if color and color.lower() not in str(v.get("exterior_color", "")).lower():
            continue

        # Price filter
        p_num = v.get("price_num")
        if min_price is not None and p_num is not None and p_num < min_price:
            continue
        if max_price is not None and p_num is not None and p_num > max_price:
            continue

        # Text query matching across all text attributes
        if q_terms:
            blob = f"{v.get('title', '')} {v.get('body', '')} {v.get('fuel', '')} {v.get('transmission', '')} {v.get('exterior_color', '')} {v.get('stock_id', '')} {v.get('vin', '')} {v.get('mm_code', '')} {v.get('branch', '')}".lower()
            if not all(term in blob for term in q_terms):
                continue

        filtered.append({
            "dealership": v.get("branch", os.getenv("DEALERSHIP_NAME", "Pre-Owned Branch")),
            "title": v.get("title", ""),
            "year": v.get("year", ""),
            "body": v.get("body", "N/A"),
            "price": v.get("price", "N/A"),
            "price_num": v.get("price_num"),
            "mileage": v.get("mileage", "N/A"),
            "fuel": v.get("fuel", "N/A"),
            "transmission": v.get("transmission", "N/A"),
            "color": v.get("exterior_color", "N/A"),
            "mm_code": v.get("mm_code", "N/A"),
            "stock_id": v.get("stock_id", "N/A"),
            "vin": v.get("vin", "N/A"),
            "history": v.get("history", "N/A"),
            "link": v.get("listing_url", ""),
            "gallery_dir": v.get("gallery_dir", ""),
            "image_count": v.get("image_count", 0)
        })

    return filtered

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search BB Pre-Owned Stock")
    parser.add_argument("--query", "-q", type=str, help="Search query (model, make, fuel, color, etc.)", default=None)
    parser.add_argument("--all", "-a", action="store_true", help="Search all regional BB branches")
    parser.add_argument("--dealer", "-d", type=str, help="Specific dealer key (Nissan or Suzuki)", default=None)
    parser.add_argument("--stock-id", type=str, help="Search by Stock ID", default=None)
    parser.add_argument("--vin", type=str, help="Search by VIN", default=None)
    parser.add_argument("--mm-code", type=str, help="Search by MM Code", default=None)
    parser.add_argument("--body", type=str, help="Filter by Body style (Hatchback, SUV, etc.)", default=None)
    parser.add_argument("--color", type=str, help="Filter by Exterior Color", default=None)
    parser.add_argument("--min-price", type=int, help="Minimum price in Rands", default=None)
    parser.add_argument("--max-price", type=int, help="Maximum price in Rands", default=None)
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    results = search_cached_stock(
        query=args.query,
        min_price=args.min_price,
        max_price=args.max_price,
        dealer=args.dealer,
        stock_id=args.stock_id,
        vin=args.vin,
        mm_code=args.mm_code,
        body=args.body,
        color=args.color
    )
    
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Found {len(results)} vehicles matching criteria:\n")
        for r in results:
            print(f"[{r['dealership']}] {r['title']}")
            print(f"  Price: {r['price']} | Mileage: {r['mileage']} | Body: {r['body']} | Trans: {r['transmission']} | Fuel: {r['fuel']}")
            print(f"  Color: {r['color']} | Stock ID: {r['stock_id']} | MM Code: {r['mm_code']} | VIN: {r['vin']}")
            print(f"  Link: {r['link']}\n")
