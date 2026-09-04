#!/usr/bin/env python3
"""
Explore and Map the DMS/Dealer CRM Dealer Portal Portal
Crawls the main navigation, submenus, and modules.
"""

import os
import sys
import json
from urllib.parse import urljoin
from pathlib import Path
from bs4 import BeautifulSoup
from portal_login import login, load_credentials_from_env_file

def explore():
    user, pwd = load_credentials_from_env_file()
    if not user or not pwd:
        print("Missing credentials in env file.", file=sys.stderr)
        sys.exit(1)

    print("Logging into portal...")
    session, res = login(user, pwd)
    base_url = res.url

    print("\n--- Parsing Main Dashboard ---")
    soup = BeautifulSoup(res.text, "html.parser")

    # Extract user / dealer profile info if present
    user_info = {}
    for selector in [".user-info", ".profile-info", ".dealer-name", "#user-details", ".header-user"]:
        el = soup.select_one(selector)
        if el:
            user_info[selector] = el.get_text(strip=True)

    # Extract all navigation links & menus
    nav_links = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(separator=" ", strip=True)
        if not href or href.startswith(("#", "javascript:void(0)", "javascript:;", "mailto:")):
            continue
        
        full_url = urljoin(base_url, href)
        if full_url not in seen:
            seen.add(full_url)
            # Find parent menu if any
            parent_menu = ""
            parent_li = a.find_parent("li")
            if parent_li and parent_li.find_parent("ul"):
                grandparent = parent_li.find_parent("ul").find_parent("li")
                if grandparent and grandparent.find("a"):
                    parent_menu = grandparent.find("a").get_text(strip=True)

            nav_links.append({
                "category": parent_menu or "Main / Direct",
                "label": text or "[Icon/No Text]",
                "href": href,
                "full_url": full_url
            })

    # Extract menu containers specifically
    menu_sections = {}
    for nav in soup.find_all(["nav", "div"], class_=lambda c: c and any(k in c.lower() for k in ["nav", "menu", "sidebar", "subnav"])):
        heading = nav.find(["h2", "h3", "h4", "span", "a"])
        heading_text = heading.get_text(strip=True) if heading else "Menu"
        items = [a.get_text(strip=True) for a in nav.find_all("a") if a.get_text(strip=True)]
        if items:
            menu_sections[heading_text] = items

    # Check for iframes or embedded frames
    iframes = [iframe.get("src") for iframe in soup.find_all("iframe") if iframe.get("src")]

    print("\n--- Portal Structure Summary ---")
    print(f"Total Navigation Links Discovered: {len(nav_links)}")
    print(f"Discovered Menu Sections: {len(menu_sections)}")
    if iframes:
        print(f"Embedded iframes: {iframes}")

    results = {
        "landing_url": res.url,
        "title": soup.title.string.strip() if soup.title else "No Title",
        "user_info": user_info,
        "menu_sections": menu_sections,
        "links": nav_links,
        "iframes": iframes
    }

    output_path = "data/scratch/portal_map.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Saved complete portal map to {output_path}")

    # Crawl first 5 key subpages to check access and content types
    print("\n--- Sampling Subpages ---")
    sample_targets = [l for l in nav_links if "page=" in l["full_url"] or "cfm" in l["full_url"]][:6]
    for target in sample_targets:
        try:
            sub_res = session.get(target["full_url"], timeout=10)
            sub_soup = BeautifulSoup(sub_res.text, "html.parser")
            sub_title = sub_soup.title.string.strip() if sub_soup.title and sub_soup.title.string else "No Title"
            print(f"[{sub_res.status_code}] {target['label']} -> {target['full_url']}")
            print(f"    Title: {sub_title}")
        except Exception as e:
            print(f"[Err] {target['label']}: {e}")

if __name__ == "__main__":
    explore()
