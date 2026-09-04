#!/usr/bin/env python3
"""
Dynamic Pre-Owned Stock Scraper & Indexer for Dealership Floors.
Extracts complete vehicle dossier (Description, Body, Mileage, Fuel, Year,
Transmission, Color, MM Code, History, Stock ID, VIN, Price, URL) without images.
Outputs: stock.json, stock_sheet.csv, stock_sheet.json, and stock_sheet.md.
"""

import sys
import os
import re
import csv
import json
import time
import asyncio
from datetime import datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.dirname(SCRIPT_DIR)
INVENTORY_DIR = os.environ.get("INVENTORY_DATA_DIR", os.path.join(SHARED_DIR, "data", "inventory"))
STOCK_JSON_PATH = os.path.join(INVENTORY_DIR, "stock.json")
STOCK_CSV_PATH = os.path.join(INVENTORY_DIR, "stock_sheet.csv")
STOCK_SHEET_JSON_PATH = os.path.join(INVENTORY_DIR, "stock_sheet.json")
STOCK_MD_PATH = os.path.join(INVENTORY_DIR, "stock_sheet.md")

FLOORS = [
    {
        "branch": os.getenv("DEALERSHIP_NAME", "Main Dealership Branch"),
        "base_url": os.getenv("DEALERSHIP_USED_URL", "https://dealership.example.com"),
        "start_url": os.getenv("DEALERSHIP_USED_START_URL", "https://dealership.example.com/used/?sort_order=price_low&min_price=0&max_price=2490000")
    },
    {
        "branch": os.getenv("DEALERSHIP_NAME_ALT", "Pre-Owned Branch"),
        "base_url": os.getenv("DEALERSHIP_USED_URL_ALT", "https://preowned.example.com"),
        "start_url": os.getenv("DEALERSHIP_USED_START_URL_ALT", "https://preowned.example.com/used/?sort_order=price_low&min_price=0&max_price=1710000")
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch_page(session: AsyncSession, url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            resp = await session.get(url, headers=HEADERS, timeout=25)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 404:
                return ""
        except Exception as e:
            if attempt == retries - 1:
                print(f"[WARN] Failed to fetch {url}: {e}", file=sys.stderr)
            await asyncio.sleep(1.0 * (attempt + 1))
    return ""


async def discover_floor_listings(session: AsyncSession, floor_info: dict) -> list:
    branch = floor_info["branch"]
    start_url = floor_info["start_url"]
    base_url = floor_info["base_url"]
    
    print(f"🔍 Discovering listings for {branch}...")
    listings = []
    seen_urls = set()
    
    # Check page 1
    html = await fetch_page(session, start_url)
    if not html:
        print(f"[ERR] Failed to fetch start page for {branch}", file=sys.stderr)
        return listings
        
    soup = BeautifulSoup(html, "html.parser")
    
    # Extract listings on page 1
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0].rstrip("/") + "/"
        if "/used/listings/" in href and href not in seen_urls:
            seen_urls.add(href)
            listings.append({"url": href, "branch": branch})
            
    # Check pagination
    page_urls = set()
    for a in soup.select("ul.page-numbers a, .pagination a, .stm-pagination a"):
        href = a.get("href")
        if href and "/used/page/" in href:
            page_urls.add(href)
            
    # Also inspect total matches to know if more pages exist
    total_matches = 0
    total_text = soup.select_one(".stm-car-listing-sort-units, .total, .count")
    if total_text:
        m = re.search(r'(\d+)\s*matches', total_text.get_text(), re.I)
        if m:
            total_matches = int(m.group(1))
            
    # If more pages exist
    cur_page = 2
    max_page = (total_matches // 20) + 2 if total_matches > 0 else 10
    
    while True:
        # Construct next page url or take from pagination
        parsed = urlparse(start_url)
        query = parsed.query
        next_page_url = f"{base_url}/used/page/{cur_page}/?{query}"
        
        # Only fetch if cur_page was in page_urls or cur_page <= max_page
        if next_page_url not in page_urls and cur_page > max_page and not any(f"/used/page/{cur_page}/" in u for u in page_urls):
            break
            
        page_html = await fetch_page(session, next_page_url)
        if not page_html:
            break
            
        page_soup = BeautifulSoup(page_html, "html.parser")
        new_count = 0
        for a in page_soup.find_all("a", href=True):
            href = a["href"].split("?")[0].rstrip("/") + "/"
            if "/used/listings/" in href and href not in seen_urls:
                seen_urls.add(href)
                listings.append({"url": href, "branch": branch})
                new_count += 1
                
        if new_count == 0:
            break
            
        cur_page += 1
        if cur_page > 20:  # safety break
            break
            
    print(f"✅ Found {len(listings)} listings for {branch}")
    return listings


def parse_vehicle_details(html: str, url: str, branch: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    
    # Title / Description
    title_el = soup.find("h1") or soup.find("title")
    description = ""
    if title_el:
        description = title_el.get_text(strip=True)
        # Clean title if it has site suffixes like " - Dealership Branch"
        description = re.sub(r'\s*[-|]\s*.*$', '', description).strip()
        
    # Price
    price = ""
    for p_el in soup.select(".price, .regular-price, .listing_price, .stm-listing-price, .has-price"):
        text = p_el.get_text(strip=True)
        if "R" in text:
            m = re.search(r'R\s*[\d,]+', text)
            if m:
                price = m.group(0).replace(" ", "")
                break
                
    # Parse numeric price
    price_num = None
    if price:
        digits = re.sub(r'[^\d]', '', price)
        if digits:
            price_num = int(digits)
            
    # Extract slug from URL
    slug = url.strip("/").split("/")[-1]
    
    # Extract specs from li.data-list-item
    data = {
        "Description": description,
        "Body": "",
        "Mileage": "",
        "Fuel type": "",
        "Year": "",
        "Transmission": "",
        "Exterior Color": "",
        "MM Code": "",
        "History": "",
        "Stock id": "",
        "VIN": "",
        "Price": price,
        "url": url,
        "branch": branch,
        "slug": slug,
        "price_num": price_num
    }
    
    # Motor theme data-list items
    for li in soup.select("li.data-list-item"):
        raw_text = li.get_text(separator=" | ", strip=True)
        parts = [p.strip() for p in raw_text.split(" | ")]
        
        label_el = li.select_one(".item-label")
        val_el = li.select_one(".heading-font, .item-value")
        
        label = label_el.get_text(strip=True).rstrip(":") if label_el else parts[0] if parts else ""
        val = val_el.get_text(strip=True) if val_el else parts[1] if len(parts) > 1 else ""
        
        # Check for VIN
        if "VIN" in raw_text:
            m = re.search(r'VIN:?\s*([A-HJ-NPR-Z0-9]+)', raw_text, re.I)
            if m:
                data["VIN"] = m.group(1).strip()
            continue
            
        norm_label = label.lower().strip()
        if "body" in norm_label:
            data["Body"] = val
        elif "mileage" in norm_label:
            data["Mileage"] = val
        elif "fuel" in norm_label:
            data["Fuel type"] = val
        elif "year" in norm_label:
            data["Year"] = val
        elif "transmission" in norm_label:
            data["Transmission"] = val
        elif "color" in norm_label or "colour" in norm_label:
            data["Exterior Color"] = val
        elif "mm code" in norm_label:
            data["MM Code"] = val
        elif "history" in norm_label:
            data["History"] = val
        elif "stock" in norm_label:
            data["Stock id"] = val
            
    # Fallback VIN check from page text if not found in li
    if not data["VIN"]:
        m_vin = re.search(r'VIN:?\s*([A-HJ-NPR-Z0-9]{17})', html, re.I)
        if m_vin:
            data["VIN"] = m_vin.group(1)
            
    # Fallback Year check from title
    if not data["Year"]:
        m_yr = re.search(r'\b(20\d{2})\b', description)
        if m_yr:
            data["Year"] = m_yr.group(1)
            
    return data


async def scrape_all_vehicles(session: AsyncSession, listings: list, concurrency: int = 6) -> list:
    print(f"🚀 Scraping full vehicle specifications for {len(listings)} vehicles (concurrency: {concurrency})...")
    semaphore = asyncio.Semaphore(concurrency)
    results = []
    completed = 0
    total = len(listings)
    
    async def worker(item):
        nonlocal completed
        url = item["url"]
        branch = item["branch"]
        async with semaphore:
            html = await fetch_page(session, url)
            completed += 1
            if completed % 10 == 0 or completed == total:
                print(f"   Progress: {completed}/{total} listings indexed ({completed*100//total}%)")
            if html:
                v = parse_vehicle_details(html, url, branch)
                return v
            else:
                print(f"[WARN] Failed to scrape {url}", file=sys.stderr)
                return None
                
    tasks = [worker(item) for item in listings]
    scraped = await asyncio.gather(*tasks)
    
    valid_vehicles = [v for v in scraped if v is not None]
    return valid_vehicles


async def run_indexing():
    start_time = time.time()
    os.makedirs(INVENTORY_DIR, exist_ok=True)
    
    async with AsyncSession(impersonate="chrome124") as session:
        all_listings = []
        for floor in FLOORS:
            floor_listings = await discover_floor_listings(session, floor)
            all_listings.extend(floor_listings)
            
        print(f"📦 Total discovered inventory listings across both floors: {len(all_listings)}")
        vehicles = await scrape_all_vehicles(session, all_listings, concurrency=6)
        
    # Sort vehicles by price ascending
    vehicles.sort(key=lambda x: (x["price_num"] is None, x["price_num"] or 0))
    
    duration = time.time() - start_time
    print(f"⏱️ Finished scraping and parsing {len(vehicles)} vehicles in {duration:.1f}s")
    
    # 1. Generate Clean Daily Stock Sheet (JSON format)
    # Requested fields: Description, Body, Mileage, Fuel type, Year, Transmission, Exterior Color, MM Code, History, Stock id, VIN, Price, url
    stock_sheet_records = []
    for v in vehicles:
        stock_sheet_records.append({
            "Description": v["Description"],
            "Body": v["Body"],
            "Mileage": v["Mileage"],
            "Fuel type": v["Fuel type"],
            "Year": v["Year"],
            "Transmission": v["Transmission"],
            "Exterior Color": v["Exterior Color"],
            "MM Code": v["MM Code"],
            "History": v["History"],
            "Stock id": v["Stock id"],
            "VIN": v["VIN"],
            "Price": v["Price"],
            "url": v["url"],
            "Floor": v["branch"]
        })
        
    with open(STOCK_SHEET_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "total_vehicles": len(stock_sheet_records),
            "dealerships": [f.get("branch") for f in FLOORS],
            "stock": stock_sheet_records
        }, f, indent=2)
        
    # 2. Generate Daily Stock Sheet CSV
    csv_columns = [
        "Stock id",
        "Description",
        "Year",
        "Body",
        "Mileage",
        "Fuel type",
        "Transmission",
        "Exterior Color",
        "Price",
        "MM Code",
        "VIN",
        "History",
        "Floor",
        "url"
    ]
    
    with open(STOCK_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns)
        writer.writeheader()
        for v in vehicles:
            writer.writerow({
                "Stock id": v.get("Stock id", ""),
                "Description": v.get("Description", ""),
                "Year": v.get("Year", ""),
                "Body": v.get("Body", ""),
                "Mileage": v.get("Mileage", ""),
                "Fuel type": v.get("Fuel type", ""),
                "Transmission": v.get("Transmission", ""),
                "Exterior Color": v.get("Exterior Color", ""),
                "Price": v.get("Price", ""),
                "MM Code": v.get("MM Code", ""),
                "VIN": v.get("VIN", ""),
                "History": v.get("History", ""),
                "Floor": v.get("branch", ""),
                "url": v.get("url", "")
            })
            
    # 3. Generate Backward-Compatible stock.json (for search_stock.py and local photo lookups)
    compatible_vehicles = []
    for v in vehicles:
        slug = v["slug"]
        gallery_dir = os.path.join(INVENTORY_DIR, "vehicles", slug)
        # Check if local photos already exist
        images = []
        if os.path.exists(gallery_dir):
            images = [os.path.join(gallery_dir, f) for f in sorted(os.listdir(gallery_dir)) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
            
        compatible_vehicles.append({
            "slug": slug,
            "title": v["Description"],
            "Description": v["Description"],
            "price": v["Price"],
            "price_num": v["price_num"],
            "mileage": v["Mileage"],
            "fuel": v["Fuel type"],
            "transmission": v["Transmission"],
            "branch": v["branch"],
            "listing_url": v["url"],
            "url": v["url"],
            "body": v["Body"],
            "exterior_color": v["Exterior Color"],
            "mm_code": v["MM Code"],
            "history": v["History"],
            "stock_id": v["Stock id"],
            "vin": v["VIN"],
            "year": v["Year"],
            "gallery_dir": gallery_dir,
            "image_count": len(images),
            "first_image": images[0] if images else None,
            "images": images
        })
        
    stock_payload = {
        "last_updated": datetime.now().isoformat(),
        "total_vehicles": len(compatible_vehicles),
        "dealerships": [f.get("branch") for f in FLOORS],
        "vehicles": compatible_vehicles
    }
    
    with open(STOCK_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(stock_payload, f, indent=2)
        
    # 4. Generate Stock Sheet Markdown Summary
    dealership_title = os.getenv("DEALERSHIP_NAME", "Dealership")
    md_lines = [
        f"# {dealership_title} Pre-Owned Daily Stock Sheet",
        f"**Updated**: {datetime.now().strftime('%Y-%m-%d %H:%M')} | **Total Vehicles**: {len(vehicles)}\n",
        "| Stock ID | Description | Year | Body | Mileage | Price | Trans | Fuel | Color | MM Code | VIN | Floor |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|"
    ]
    for v in vehicles:
        md_lines.append(
            f"| {v.get('Stock id') or '-'} | [{v.get('Description')}]({v.get('url')}) | {v.get('Year') or '-'} | {v.get('Body') or '-'} | {v.get('Mileage') or '-'} | {v.get('Price') or '-'} | {v.get('Transmission') or '-'} | {v.get('Fuel type') or '-'} | {v.get('Exterior Color') or '-'} | {v.get('MM Code') or '-'} | {v.get('VIN') or '-'} | {v.get('branch')} |"
        )
    with open(STOCK_MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
        
    # Set permissions
    for p in [STOCK_JSON_PATH, STOCK_CSV_PATH, STOCK_SHEET_JSON_PATH, STOCK_MD_PATH]:
        try:
            os.chmod(p, 0o777)
        except Exception:
            pass
            
    print(f"\n🎉 Daily stock sheet successfully indexed and updated:")
    print(f"  • JSON Stock Sheet: {STOCK_SHEET_JSON_PATH}")
    print(f"  • CSV Stock Sheet:  {STOCK_CSV_PATH}")
    print(f"  • System Database:  {STOCK_JSON_PATH}")
    print(f"  • Markdown Catalog: {STOCK_MD_PATH}")
    print(f"Total Pre-Owned Units Indexed: {len(vehicles)}")


if __name__ == "__main__":
    asyncio.run(run_indexing())
