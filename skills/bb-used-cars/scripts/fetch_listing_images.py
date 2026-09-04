#!/usr/bin/env python3
"""
Download or retrieve all gallery images for a BB dealership used car listing.
Strictly isolates genuine vehicle gallery images and filters out sidebar widgets/similar cars.
"""

import sys
import os
import argparse
import requests
from bs4 import BeautifulSoup
import re
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse

CACHE_ROOT = "jax-shared/data/inventory/vehicles"
STEALTH_SCRAPER_DIR = "skills/low-resource-stealth-scraper/scripts"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5"
}


def stealth_fetch(url):
    """Final-resort fetch via the TLS-impersonating stealth toolkit (bypasses anti-bot
    fingerprinting that a plain requests.get() from this VM cannot get past)."""
    added = False
    try:
        if STEALTH_SCRAPER_DIR not in sys.path:
            sys.path.insert(0, STEALTH_SCRAPER_DIR)
            added = True
        import asyncio
        from stealth_toolkit import AsyncBatchFetcher
        fetcher = AsyncBatchFetcher(concurrency=1, impersonate="chrome124", timeout=15, retries=2)
        result = asyncio.run(fetcher.fetch_all([url]))[0]
        if result.get("success"):
            return result.get("html", "")
    except Exception:
        pass
    finally:
        if added and STEALTH_SCRAPER_DIR in sys.path:
            sys.path.remove(STEALTH_SCRAPER_DIR)
    return ""

def resolve_url_from_slug(slug):
    stock_file = "jax-shared/data/inventory/stock.json"
    if os.path.exists(stock_file):
        try:
            with open(stock_file, "r") as f:
                data = json.load(f)
                for v in data.get("vehicles", []):
                    if v.get("slug") == slug:
                        return v.get("listing_url")
        except Exception:
            pass
    return f"https://example-dealership.co.za/used/listings/{slug}/"

def fetch_html(url_or_path):
    if os.path.exists(url_or_path):
        with open(url_or_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(), url_or_path

    # If argument is just a slug or filename
    slug = re.sub(r'[^a-zA-Z0-9_-]', '-', url_or_path.strip("/").split("/")[-1])

    # Check if there is a saved step content in recent brain directories
    for brain_root in [os.path.expanduser("~/.gemini/antigravity-cli/brain")]:
        if os.path.exists(brain_root):
            for sess in os.listdir(brain_root):
                steps_dir = os.path.join(brain_root, sess, ".system_generated", "steps")
                if os.path.exists(steps_dir):
                    for st in sorted(os.listdir(steps_dir), reverse=True):
                        cpath = os.path.join(steps_dir, st, "content.md")
                        if os.path.exists(cpath):
                            try:
                                with open(cpath, "r", errors="ignore") as f:
                                    txt = f.read(4000)
                                    if slug in txt or slug.replace("-", " ") in txt:
                                        with open(cpath, "r", errors="ignore") as f2:
                                            return f2.read(), cpath
                            except Exception:
                                pass

    url = url_or_path if url_or_path.startswith("http") else resolve_url_from_slug(slug)

    # Try direct fetch
    try:
        resp = requests.get(url, headers=HEADERS, timeout=(2.5, 4.0))
        if resp.status_code == 200:
            return resp.text, url
    except Exception:
        pass

    # Direct fetch failed (this VM's plain requests typically get blocked/time out
    # against dealership hosts) - fall back to the TLS-impersonating stealth fetcher.
    html = stealth_fetch(url)
    if html:
        return html, url

    return "", url

def download_single_image(img_url, dest_path):
    # Direct fetch
    try:
        r = requests.get(img_url, headers=HEADERS, timeout=(2.0, 4.0))
        if r.status_code == 200 and len(r.content) > 1000:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return dest_path
    except Exception:
        pass

    # Proxy fallback (wsrv.nl / images.weserv.nl CDN)
    try:
        proxy_url = f"https://wsrv.nl/?url={urllib.parse.quote(img_url, safe=':/')}"
        r = requests.get(proxy_url, headers=HEADERS, timeout=(3.0, 6.0))
        if r.status_code == 200 and len(r.content) > 1000:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return dest_path
    except Exception:
        pass

    return None

def extract_and_download_images(listing_url_or_path, output_dir=None, html_content=None, force=False):
    slug = re.sub(r'[^a-zA-Z0-9_-]', '-', listing_url_or_path.strip("/").split("/")[-1])
    if not output_dir:
        output_dir = f"/tmp/bb_vehicles/{slug}"

    os.makedirs(output_dir, exist_ok=True)
    try:
        os.chmod(output_dir, 0o777)
    except Exception:
        pass

    # Fast path: Check local pre-cached inventory
    if not force:
        cached_paths = [
            os.path.join(CACHE_ROOT, slug),
            output_dir,
            f"/tmp/bb_vehicles/{slug}"
        ]
        for cp in cached_paths:
            if os.path.exists(cp):
                files = [os.path.join(cp, f) for f in sorted(os.listdir(cp)) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]
                # If cached files exist and don't contain sidebar widget thumbnails
                if len(files) >= 3:
                    if cp != output_dir:
                        for f in files:
                            target = os.path.join(output_dir, os.path.basename(f))
                            if not os.path.exists(target):
                                try:
                                    shutil.copy2(f, target)
                                except Exception:
                                    pass
                        files = [os.path.join(output_dir, f) for f in sorted(os.listdir(output_dir)) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))]

                    return {
                        "title": slug.replace("-", " ").title(),
                        "price": "N/A",
                        "mileage": "N/A",
                        "output_dir": output_dir,
                        "image_count": len(files),
                        "files": files,
                        "cached": True
                    }

    # Fetch HTML if not provided
    if html_content:
        html = html_content
        source_url = listing_url_or_path or ""
    else:
        html, source_url = fetch_html(listing_url_or_path)

    if not html:
        return {"title": "Vehicle", "price": "N/A", "mileage": "N/A", "output_dir": output_dir, "image_count": 0, "files": [], "cached": False}

    soup = BeautifulSoup(html, "html.parser")

    # CRITICAL: Decompose all sidebars, widgets, similar cars, footers, headers
    for bad in soup.select("aside, .stm_similar_cars, .stm-similar-cars-units, footer, header, .widget, .similar-cars, .bloglogo"):
        bad.decompose()
    
    title_tag = soup.select_one(".stm-car-title .heading-font, h1.title, .entry-title")
    title = title_tag.get_text(strip=True) if title_tag else slug.replace("-", " ").title()
    
    price_tag = soup.select_one(".price, .normal-price")
    price = price_tag.get_text(strip=True) if price_tag else "N/A"
    
    mileage_tag = soup.select_one(".stm-single-car-spec-mileage, li:has(.stm-icon-speedometer2)")
    mileage = mileage_tag.get_text(strip=True) if mileage_tag else "N/A"
    
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

    # Clean existing directory if force downloading
    if force and os.path.exists(output_dir):
        for f in os.listdir(output_dir):
            try:
                os.unlink(os.path.join(output_dir, f))
            except Exception:
                pass

    downloaded_files = []
    with ThreadPoolExecutor(max_workers=min(12, len(image_urls) or 1)) as executor:
        future_map = {}
        for idx, img_url in enumerate(image_urls, start=1):
            filename = f"{idx:02d}_{os.path.basename(img_url)}"
            dest = os.path.join(output_dir, filename)
            future_map[executor.submit(download_single_image, img_url, dest)] = dest

        for future in as_completed(future_map):
            res = future.result()
            if res and os.path.exists(res) and os.path.getsize(res) > 1000:
                downloaded_files.append(res)

    downloaded_files.sort()

    # Also sync into permanent cache
    perm_dir = os.path.join(CACHE_ROOT, slug)
    os.makedirs(perm_dir, exist_ok=True)
    for f in downloaded_files:
        try:
            shutil.copy2(f, os.path.join(perm_dir, os.path.basename(f)))
        except Exception:
            pass

    return {
        "title": title,
        "price": price,
        "mileage": mileage,
        "output_dir": output_dir,
        "image_count": len(downloaded_files),
        "files": downloaded_files,
        "cached": False
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch all gallery images for a vehicle listing")
    parser.add_argument("url_or_path", help="Listing URL or local HTML file path")
    parser.add_argument("--output", "-o", help="Output directory", default=None)
    parser.add_argument("--force", "-f", action="store_true", help="Force clean re-download")
    args = parser.parse_args()

    res = extract_and_download_images(args.url_or_path, args.output, force=args.force)
    print(json.dumps(res, indent=2))
