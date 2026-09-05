#!/usr/bin/env python3
"""
BB Dealership Inventory & Photo Synchronizer
Daily sync for {DEALERSHIP_NAME} and {DEALERSHIP_NAME_ALT} pre-owned vehicles.
Maintains local cache of vehicle metadata and full high-resolution photo galleries.
"""

import os
import sys
import json
import re
import time
import argparse
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.environ.get("INVENTORY_DATA_DIR", os.path.join(SHARED_DIR, "data", "inventory"))
VEHICLES_DIR = os.path.join(DATA_DIR, "vehicles")
STOCK_FILE = os.path.join(DATA_DIR, "stock.json")
LOG_FILE = os.environ.get("INVENTORY_LOG_FILE", os.path.join(SHARED_DIR, "data", "logs", "inventory_sync.log"))

DEALERSHIPS = [
    {
        "id": "main_branch",
        "name": os.environ.get("DEALERSHIP_NAME", "Main Dealership"),
        "base_url": "https://dealership.example.com/used/"
    },
    {
        "id": "preowned_branch",
        "name": os.environ.get("DEALERSHIP_NAME_ALT", f"{os.environ.get('DEALERSHIP_NAME', 'Dealership')} Pre-Owned"),
        "base_url": "https://preowned.example.com/used/"
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5"
}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def fetch_url(url, timeout=(4.0, 8.0)):
    """Fetch HTML with direct attempt followed by CORS proxy fallback."""
    # 1. Direct attempt
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200 and len(resp.text) > 1000:
            return resp.text
    except Exception:
        pass

    # 2. Proxy fallback via corsproxy.io
    try:
        proxy_url = "https://corsproxy.io/?" + urllib.parse.quote(url)
        resp = requests.get(proxy_url, headers=HEADERS, timeout=(5.0, 10.0))
        if resp.status_code == 200 and len(resp.text) > 1000:
            return resp.text
    except Exception:
        pass

    return None

def download_image(img_url, dest_path):
    """Download single image file with proxy fallback."""
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 1000:
        return dest_path  # Already downloaded

    # Direct download
    try:
        r = requests.get(img_url, headers=HEADERS, timeout=(2.5, 5.0))
        if r.status_code == 200 and len(r.content) > 1000:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return dest_path
    except Exception:
        pass

    # CDN / weserv proxy download
    try:
        proxy_url = f"https://wsrv.nl/?url={urllib.parse.quote(img_url, safe=':/')}"
        r = requests.get(proxy_url, headers=HEADERS, timeout=(3.5, 7.0))
        if r.status_code == 200 and len(r.content) > 1000:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return dest_path
    except Exception:
        pass

    return None

def parse_clean_price(price_str):
    if not price_str:
        return None
    cleaned = re.sub(r'[^\d]', '', str(price_str))
    return int(cleaned) if cleaned else None

def parse_listings_from_html(html, dealer_name):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    seen = set()

    items = soup.find_all("div", class_=lambda c: c and ("stm-isotope-listing-item" in c or "stm-directory-grid-loop" in c or "stm-listing-directory-list-loop" in c))
    if not items:
        items = soup.find_all("div", class_=re.compile(r"listing-car-item|car-listing-modern-grid"))

    for item in items:
        link_tag = item.find("a", href=lambda h: h and "/listings/" in h)
        if not link_tag:
            continue
        href = link_tag["href"]
        if href in seen:
            continue
        seen.add(href)

        slug = href.strip("/").split("/")[-1]

        # Title
        title = ""
        img = item.find("img", alt=True)
        if img and img.get("alt") and len(img["alt"].strip()) > 3:
            title = img["alt"].strip()
        if not title:
            for sel in [".car-title", ".title", ".heading-font", "h4", "h3"]:
                t_tag = item.select_one(sel)
                if t_tag:
                    txt = re.sub(r'\s+', ' ', t_tag.get_text()).strip()
                    txt = re.sub(r'^\d+\s*more\s*photos\d*', '', txt, flags=re.I).strip()
                    txt = re.sub(r'R\s*[\d,]+', '', txt).strip()
                    if len(txt) > 3:
                        title = txt
                        break
        if not title:
            title = slug.replace("-", " ").title()

        # Price
        price = "N/A"
        price_num = None
        data_price = item.get("data-price")
        if data_price and data_price.isdigit():
            price_num = int(data_price)
            price = f"R{price_num:,}"
        else:
            price_tag = item.select_one(".price, .normal-price, .heading-font .price")
            if price_tag:
                m = re.search(r'R\s*[\d,]+', price_tag.get_text())
                if m:
                    price = m.group(0)
                    price_num = parse_clean_price(price)

        # Mileage
        mileage = "N/A"
        data_mileage = item.get("data-mileage", "").strip()
        if data_mileage and data_mileage.isdigit():
            mileage = f"{int(data_mileage):,} km"
        else:
            for li in item.find_all("li"):
                txt = li.get_text(strip=True)
                if re.search(r'\bkm\b', txt, re.I):
                    mileage = txt
                    break

        # Fuel & Transmission
        fuel = "N/A"
        transmission = "N/A"
        class_str = " ".join(item.get("class", []))
        if "petrol" in class_str:
            fuel = "Petrol"
        elif "diesel" in class_str:
            fuel = "Diesel"
        elif "hybrid" in class_str:
            fuel = "Hybrid"
        elif "electric" in class_str:
            fuel = "Electric"

        if "manual" in class_str:
            transmission = "Manual"
        elif "auto" in class_str or "cvt" in class_str or "amt" in class_str:
            transmission = "Automatic"

        for li in item.find_all("li"):
            txt = li.get_text(strip=True)
            if fuel == "N/A" and re.search(r'\b(petrol|diesel|hybrid|electric)\b', txt, re.I):
                fuel = txt
            if transmission == "N/A" and re.search(r'\b(manual|automatic|cvt|amt|5mt|6mt)\b', txt, re.I):
                transmission = txt

        listings.append({
            "slug": slug,
            "title": title,
            "price": price,
            "price_num": price_num,
            "mileage": mileage,
            "fuel": fuel,
            "transmission": transmission,
            "branch": dealer_name,
            "listing_url": href,
            "gallery_dir": os.path.join(VEHICLES_DIR, slug)
        })

    return listings

def extract_gallery_image_urls(listing_html):
    soup = BeautifulSoup(listing_html, "html.parser")
    for bad in soup.select("aside, .stm_similar_cars, .stm-similar-cars-units, footer, header, .widget, .similar-cars, .bloglogo"):
        bad.decompose()

    gallery = soup.select_one(".mosaic-gallery, .motors-elementor-single-listing-gallery-mosaic, .stm-car-gallery, .stm-single-car-photos")
    scope = gallery if gallery else soup

    image_urls = []
    seen = set()

    for tag in scope.find_all(["a", "img"]):
        for attr in ["href", "data-src", "src"]:
            val = tag.get(attr)
            if val and re.search(r'\.(jpe?g|png|webp)(\?.*)?$', val, re.I):
                clean_url = re.sub(r'-\d+x\d+\.(jpe?g|png|webp)', r'.\1', val).split("?")[0]
                if clean_url.startswith("//"):
                    clean_url = "https:" + clean_url
                if "/uploads/" in clean_url and clean_url not in seen:
                    lower = clean_url.lower()
                    if not any(bad in lower for bad in ["logo", "badge", "icon", "banner", "avatar", "favicon", "dealer"]):
                        seen.add(clean_url)
                        image_urls.append(clean_url)

    image_urls.sort(key=lambda u: os.path.basename(u))
    return image_urls

def sync_vehicle_gallery(vehicle, force=False):
    """Sync high-res photos for a vehicle listing."""
    slug = vehicle["slug"]
    v_dir = vehicle["gallery_dir"]
    os.makedirs(v_dir, exist_ok=True)
    try:
        os.chmod(v_dir, 0o777)
    except Exception:
        pass

    existing_files = [f for f in os.listdir(v_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
    if len(existing_files) >= 5 and not force:
        vehicle["image_count"] = len(existing_files)
        vehicle["first_image"] = os.path.join(v_dir, sorted(existing_files)[0])
        vehicle["images"] = [os.path.join(v_dir, f) for f in sorted(existing_files)]
        return vehicle

    # Fetch listing detail page
    html = fetch_url(vehicle["listing_url"])
    if not html:
        log(f"  ⚠️ Could not fetch detail page for {slug}")
        vehicle["image_count"] = len(existing_files)
        vehicle["first_image"] = os.path.join(v_dir, sorted(existing_files)[0]) if existing_files else None
        vehicle["images"] = [os.path.join(v_dir, f) for f in sorted(existing_files)]
        return vehicle

    # Extract image URLs
    img_urls = extract_gallery_image_urls(html)
    if not img_urls:
        log(f"  ⚠️ No gallery images found for {slug}")
        vehicle["image_count"] = len(existing_files)
        vehicle["first_image"] = os.path.join(v_dir, sorted(existing_files)[0]) if existing_files else None
        vehicle["images"] = [os.path.join(v_dir, f) for f in sorted(existing_files)]
        return vehicle

    log(f"  📥 Downloading {len(img_urls)} photos for {vehicle['title']} ({slug})...")
    downloaded = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {}
        for idx, img_url in enumerate(img_urls, start=1):
            ext = img_url.split(".")[-1]
            filename = f"{idx:02d}_{os.path.basename(img_url)}"
            dest = os.path.join(v_dir, filename)
            future_map[executor.submit(download_image, img_url, dest)] = dest

        for future in as_completed(future_map):
            dest = future.result()
            if dest and os.path.exists(dest) and os.path.getsize(dest) > 1000:
                downloaded.append(dest)

    downloaded.sort()
    vehicle["image_count"] = len(downloaded)
    vehicle["first_image"] = downloaded[0] if downloaded else None
    vehicle["images"] = downloaded
    log(f"  ✅ Saved {len(downloaded)} images to {v_dir}")
    return vehicle

def sync_dealership_inventory(force=False):
    log("=" * 60)
    log("🚀 Starting Daily {DEALERSHIP_NAME} Inventory & Photo Sync...")
    os.makedirs(VEHICLES_DIR, exist_ok=True)

    # Load existing stock database
    existing_stock = {}
    if os.path.exists(STOCK_FILE):
        try:
            with open(STOCK_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                for v in old_data.get("vehicles", []):
                    existing_stock[v["slug"]] = v
        except Exception as e:
            log(f"Error loading existing stock.json: {e}")

    # Crawl live listings from {DEALERSHIP_NAME} and Suzuki
    live_listings = []
    for dealer in DEALERSHIPS:
        log(f"🔍 Fetching live listings from {dealer['name']} ({dealer['base_url']})...")
        # Page 1
        html = fetch_url(dealer["base_url"])
        if html:
            page1_items = parse_listings_from_html(html, dealer["name"])
            live_listings.extend(page1_items)
            log(f"  Found {len(page1_items)} vehicles on page 1")

            # Check pagination (page 2)
            if 'class="page-numbers"' in html or '/page/2/' in html:
                page2_url = urllib.parse.urljoin(dealer["base_url"], "page/2/")
                html_p2 = fetch_url(page2_url)
                if html_p2:
                    p2_items = parse_listings_from_html(html_p2, dealer["name"])
                    live_listings.extend(p2_items)
                    log(f"  Found {len(p2_items)} vehicles on page 2")
        else:
            log(f"  ⚠️ Could not reach {dealer['name']}")

    log(f"📊 Total live listings discovered across inventory: {len(live_listings)}")

    # Deduplicate
    unique_map = {}
    for item in live_listings:
        unique_map[item["slug"]] = item

    all_vehicles = list(unique_map.values())

    # Sync galleries for all vehicles concurrently
    log("📸 Syncing vehicle photo sets...")
    synced_vehicles = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {executor.submit(sync_vehicle_gallery, v, force): v["slug"] for v in all_vehicles}
        for future in as_completed(future_map):
            try:
                res = future.result()
                res["last_synced"] = datetime.now().isoformat()
                synced_vehicles.append(res)
            except Exception as e:
                log(f"Error syncing vehicle gallery: {e}")

    # Sort vehicles by price
    synced_vehicles.sort(key=lambda v: (v["price_num"] is None, v["price_num"] or 0))

    # Build database payload
    stock_payload = {
        "last_updated": datetime.now().isoformat(),
        "total_vehicles": len(synced_vehicles),
        "dealerships": [d["name"] for d in DEALERSHIPS],
        "vehicles": synced_vehicles
    }

    # Write atomically to stock.json
    temp_file = STOCK_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(stock_payload, f, indent=2)
    os.replace(temp_file, STOCK_FILE)
    try:
        os.chmod(STOCK_FILE, 0o777)
    except Exception:
        pass

    log(f"🎉 Inventory sync completed successfully! {len(synced_vehicles)} vehicles cached in {STOCK_FILE}")
    log("=" * 60)
    return stock_payload

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync {DEALERSHIP_NAME} Pre-Owned Inventory & Images")
    parser.add_argument("--force", action="store_true", help="Force re-download all gallery images")
    parser.add_argument("--status", action="store_true", help="Print local cache status")
    parser.add_argument("--query", "-q", type=str, help="Search cached stock", default=None)
    args = parser.parse_args()

    if args.status:
        if os.path.exists(STOCK_FILE):
            with open(STOCK_FILE, "r") as f:
                data = json.load(f)
            print(f"Inventory Cache Status:")
            print(f"  Last Updated: {data.get('last_updated')}")
            print(f"  Total Vehicles: {data.get('total_vehicles')}")
            for v in data.get("vehicles", []):
                print(f"  - [{v['branch']}] {v['title']} -> {v['price']} | {v['mileage']} | {v.get('image_count', 0)} photos")
        else:
            print("No local inventory cache found. Run sync to initialize.")
        sys.exit(0)

    if args.query:
        if os.path.exists(STOCK_FILE):
            with open(STOCK_FILE, "r") as f:
                data = json.load(f)
            q_terms = args.query.lower().split()
            matches = []
            for v in data.get("vehicles", []):
                blob = f"{v['title']} {v['fuel']} {v['transmission']} {v['branch']}".lower()
                if all(t in blob for t in q_terms):
                    matches.append(v)
            print(f"Found {len(matches)} cached vehicles matching '{args.query}':\n")
            for v in matches:
                print(f"[{v['branch']}] {v['title']}")
                print(f"  Price: {v['price']} | Mileage: {v['mileage']} | Fuel: {v['fuel']} | Trans: {v['transmission']}")
                print(f"  Gallery: {v['gallery_dir']} ({v.get('image_count', 0)} photos)")
                print(f"  Link: {v['listing_url']}\n")
        sys.exit(0)

    sync_dealership_inventory(force=args.force)
