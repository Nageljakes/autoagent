#!/usr/bin/env python3
"""
BB Dealerships Used / Pre-Owned Inventory Search Tool
Instant search using local pre-cached inventory (stock.json).
Supports price range filtering and vehicle gallery paths.
"""

import sys
import os
import argparse
import json
import re

STOCK_FILE = "jax-shared/data/inventory/stock.json"

def search_cached_stock(query=None, min_price=None, max_price=None, dealer=None):
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

        # Price filter
        p_num = v.get("price_num")
        if min_price is not None and p_num is not None and p_num < min_price:
            continue
        if max_price is not None and p_num is not None and p_num > max_price:
            continue

        # Text query
        if q_terms:
            blob = f"{v.get('title', '')} {v.get('fuel', '')} {v.get('transmission', '')} {v.get('branch', '')}".lower()
            if not all(term in blob for term in q_terms):
                continue

        filtered.append({
            "dealership": v.get("branch", os.getenv("DEALERSHIP_NAME", "Dealership Branch")),
            "title": v.get("title", ""),
            "price": v.get("price", "N/A"),
            "price_num": v.get("price_num"),
            "mileage": v.get("mileage", "N/A"),
            "fuel": v.get("fuel", "N/A"),
            "transmission": v.get("transmission", "N/A"),
            "link": v.get("listing_url", ""),
            "gallery_dir": v.get("gallery_dir", ""),
            "image_count": v.get("image_count", 0)
        })

    return filtered

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search BB Pre-Owned Stock")
    parser.add_argument("--query", "-q", type=str, help="Search query (model, make, fuel, etc.)", default=None)
    parser.add_argument("--all", "-a", action="store_true", help="Search all regional BB branches")
    parser.add_argument("--dealer", "-d", type=str, help="Specific dealer key", default=None)
    parser.add_argument("--min-price", type=int, help="Minimum price in Rands", default=None)
    parser.add_argument("--max-price", type=int, help="Maximum price in Rands", default=None)
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    results = search_cached_stock(
        query=args.query,
        min_price=args.min_price,
        max_price=args.max_price,
        dealer=args.dealer
    )
    
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Found {len(results)} vehicles matching '{args.query or 'ALL'}':\n")
        for r in results:
            print(f"[{r['dealership']}] {r['title']}")
            print(f"  Price: {r['price']} | Mileage: {r['mileage']} | Fuel: {r['fuel']} | Trans: {r['transmission']}")
            print(f"  Gallery: {r['gallery_dir']} ({r.get('image_count', 0)} photos)")
            print(f"  Link: {r['link']}\n")
