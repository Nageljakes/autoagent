---
name: low-resource-stealth-scraper
description: >-
  Scrape web pages, bypass anti-bot fingerprinting (Cloudflare, Akamai, JA3/JA4), and perform async batch requests
  on low-memory environments (under 1GB RAM) without launching heavy browser engines.
---

# Low-Resource Stealth Scraping Guide

## Overview
Standard browser automation tools (Selenium, Puppeteer, Playwright) require 300MB to 800MB+ of RAM per instance, causing Out-Of-Memory (OOM) crashes on low-memory servers. This skill provides an ultra-lightweight alternative using `curl_cffi` (~20MB to 40MB total memory footprint).

## Core Capabilities
- **TLS Fingerprint Spoofing**: Impersonates real browser JA3, JA4, GREASE, and Akamai HTTP/2 frame headers (Chrome 124, Safari 17, Edge 101).
- **Concurrency Control**: Async batch fetching with `asyncio.Semaphore` to process dozens of URLs concurrently without CPU/RAM spikes.
- **Proxy Management**: Cycle and rotate HTTP, HTTPS, and SOCKS5 proxies automatically.
- **Lightweight Extractors**: Fast text, link, and table parsing via BeautifulSoup.

## Quick Usage

### 1. Single Page Fetch (CLI)
```bash
python3 scripts/stealth_toolkit.py fetch "https://target-site.com" --text --output page_text.txt
```

### 2. Async Batch Fetch (CLI)
```bash
python3 scripts/stealth_toolkit.py batch urls.txt --concurrency 5 --rotate-browsers --output results.json
```

### 3. Python Library Usage
```python
import asyncio
from stealth_toolkit import AsyncBatchFetcher, ProxyRotator, DataExtractor

async def main():
    fetcher = AsyncBatchFetcher(concurrency=5, impersonate="chrome124")
    results = await fetcher.fetch_all(["https://example.com/item1", "https://example.com/item2"])
    for res in results:
        if res["success"]:
            text = DataExtractor.extract_text(res["html"])
            print(text[:200])

asyncio.run(main())
```

## Helper Scripts
- `scripts/stealth_toolkit.py`: Main CLI & module with async batching, proxy rotation, and data extraction.
- `scripts/stealth_fetcher.py`: Lightweight standalone TLS fetch utility.
