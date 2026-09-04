#!/usr/bin/env python3
"""
Lightweight Stealth Scraping Toolkit
Includes:
- Proxy rotation manager
- Async batch fetcher with concurrency controls
- Modular data extractors (tables, links, text, structured metadata)
- Output formats: JSON, CSV, Clean Text
"""

import sys
import os
import json
import csv
import asyncio
import itertools
import random
import argparse
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

from curl_cffi import requests
from bs4 import BeautifulSoup

AVAILABLE_IMPERSONATIONS = [
    "chrome124",
    "chrome120",
    "safari17_0",
    "safari15_5",
    "edge101",
]


class ProxyRotator:
    def __init__(self, proxy_list: Optional[List[str]] = None, proxy_file: Optional[str] = None):
        self.proxies = []
        if proxy_file and os.path.exists(proxy_file):
            with open(proxy_file, "r", encoding="utf-8") as f:
                self.proxies = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        elif proxy_list:
            self.proxies = [p.strip() for p in proxy_list if p.strip()]

        self._cycle = itertools.cycle(self.proxies) if self.proxies else None

    def get_next(self) -> Optional[str]:
        if not self._cycle:
            return None
        return next(self._cycle)

    def get_random(self) -> Optional[str]:
        if not self.proxies:
            return None
        return random.choice(self.proxies)

    def has_proxies(self) -> bool:
        return bool(self.proxies)


class DataExtractor:
    @staticmethod
    def extract_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)

    @staticmethod
    def extract_links(html: str, base_url: str) -> List[Dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            full_url = urljoin(base_url, href)
            if full_url not in seen:
                seen.add(full_url)
                text = a.get_text(strip=True)
                links.append({"text": text, "url": full_url})
        return links

    @staticmethod
    def extract_tables(html: str) -> List[List[Dict[str, str]]]:
        soup = BeautifulSoup(html, "html.parser")
        all_tables = []

        for table in soup.find_all("table"):
            headers = []
            header_row = table.find("tr")
            if not header_row:
                continue

            th_tags = header_row.find_all(["th", "td"])
            for idx, th in enumerate(th_tags):
                name = th.get_text(strip=True) or f"column_{idx+1}"
                headers.append(name)

            table_data = []
            rows = table.find_all("tr")[1:] if table.find("th") else table.find_all("tr")
            for tr in rows:
                cols = tr.find_all(["td", "th"])
                if not cols:
                    continue
                row_dict = {}
                for idx, col in enumerate(cols):
                    col_name = headers[idx] if idx < len(headers) else f"column_{idx+1}"
                    row_dict[col_name] = col.get_text(strip=True)
                if row_dict:
                    table_data.append(row_dict)

            if table_data:
                all_tables.append(table_data)

        return all_tables

    @staticmethod
    def extract_custom(html: str, selector: str, attr: Optional[str] = None) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        elements = soup.select(selector)
        results = []
        for el in elements:
            if attr:
                val = el.get(attr)
                if val:
                    results.append(str(val).strip())
            else:
                results.append(el.get_text(strip=True))
        return results


class AsyncBatchFetcher:
    def __init__(
        self,
        concurrency: int = 5,
        impersonate: str = "chrome124",
        rotate_browsers: bool = False,
        proxy_rotator: Optional[ProxyRotator] = None,
        timeout: int = 15,
        retries: int = 2,
    ):
        self.concurrency = concurrency
        self.impersonate = impersonate
        self.rotate_browsers = rotate_browsers
        self.proxy_rotator = proxy_rotator
        self.timeout = timeout
        self.retries = retries
        self.semaphore = asyncio.Semaphore(concurrency)

    async def fetch_one(
        self,
        session: requests.AsyncSession,
        url: str,
    ) -> Dict[str, Any]:
        async with self.semaphore:
            target_impersonate = (
                random.choice(AVAILABLE_IMPERSONATIONS) if self.rotate_browsers else self.impersonate
            )
            proxy = self.proxy_rotator.get_next() if self.proxy_rotator else None
            proxies = {"http": proxy, "https": proxy} if proxy else None

            for attempt in range(1, self.retries + 1):
                try:
                    res = await session.get(
                        url,
                        impersonate=target_impersonate,
                        proxies=proxies,
                        timeout=self.timeout,
                    )
                    return {
                        "url": url,
                        "status": res.status_code,
                        "success": res.status_code == 200,
                        "html": res.text,
                        "impersonate": target_impersonate,
                        "proxy": proxy,
                        "error": None,
                    }
                except Exception as e:
                    if attempt == self.retries:
                        return {
                            "url": url,
                            "status": 0,
                            "success": False,
                            "html": "",
                            "impersonate": target_impersonate,
                            "proxy": proxy,
                            "error": str(e),
                        }
                    await asyncio.sleep(1)

    async def fetch_all(self, urls: List[str]) -> List[Dict[str, Any]]:
        async with requests.AsyncSession() as session:
            tasks = [self.fetch_one(session, url) for url in urls]
            return await asyncio.gather(*tasks)


def save_to_csv(filepath: str, data: List[Dict[str, Any]]):
    if not data:
        print("No data to save.", file=sys.stderr)
        return
    fieldnames = list(data[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    print(f"Saved {len(data)} records to {filepath}")


def save_to_json(filepath: str, data: Any):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved JSON data to {filepath}")


def main():
    parser = argparse.ArgumentParser(description="Stealth Scraping Toolkit")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Command: fetch
    p_fetch = subparsers.add_parser("fetch", help="Fetch a single URL")
    p_fetch.add_argument("url", help="Target URL")
    p_fetch.add_argument("--impersonate", default="chrome124", help="Browser to impersonate")
    p_fetch.add_argument("--proxy", default=None, help="Proxy URL")
    p_fetch.add_argument("--text", action="store_true", help="Extract plain text")
    p_fetch.add_argument("--links", action="store_true", help="Extract links")
    p_fetch.add_argument("--tables", action="store_true", help="Extract tables")
    p_fetch.add_argument("--selector", default=None, help="CSS selector")
    p_fetch.add_argument("--attr", default=None, help="Attribute for selector")
    p_fetch.add_argument("--output", default=None, help="Save result to file (.json/.csv/.txt)")

    # Command: batch
    p_batch = subparsers.add_parser("batch", help="Async batch fetch multiple URLs")
    p_batch.add_argument("input_file", help="File with 1 URL per line")
    p_batch.add_argument("--concurrency", type=int, default=5, help="Concurrent workers (default 5)")
    p_batch.add_argument("--rotate-browsers", action="store_true", help="Randomize browser signature per request")
    p_batch.add_argument("--proxies", default=None, help="File with proxy list (1 per line)")
    p_batch.add_argument("--output", default="batch_results.json", help="Output JSON file")
    p_batch.add_argument("--extract-text", action="store_true", help="Parse text instead of raw HTML")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "fetch":
        rotator = ProxyRotator(proxy_list=[args.proxy]) if args.proxy else None
        fetcher = AsyncBatchFetcher(
            concurrency=1,
            impersonate=args.impersonate,
            proxy_rotator=rotator,
        )
        res = asyncio.run(fetcher.fetch_all([args.url]))[0]

        if not res["success"]:
            print(f"Fetch failed: {res['error']} (Status: {res['status']})", file=sys.stderr)
            sys.exit(1)

        html = res["html"]

        if args.links:
            data = DataExtractor.extract_links(html, args.url)
            if args.output:
                if args.output.endswith(".csv"):
                    save_to_csv(args.output, data)
                else:
                    save_to_json(args.output, data)
            else:
                print(json.dumps(data, indent=2))

        elif args.tables:
            tables = DataExtractor.extract_tables(html)
            if args.output:
                if args.output.endswith(".csv") and tables:
                    save_to_csv(args.output, tables[0])
                else:
                    save_to_json(args.output, tables)
            else:
                print(json.dumps(tables, indent=2))

        elif args.selector:
            data = DataExtractor.extract_custom(html, args.selector, args.attr)
            if args.output:
                save_to_json(args.output, data)
            else:
                print(json.dumps(data, indent=2))

        elif args.text:
            text = DataExtractor.extract_text(html)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(text)
                print(f"Saved text to {args.output}")
            else:
                print(text)

        else:
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"Saved HTML to {args.output}")
            else:
                print(html)

    elif args.command == "batch":
        if not os.path.exists(args.input_file):
            print(f"Input file not found: {args.input_file}", file=sys.stderr)
            sys.exit(1)

        with open(args.input_file, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        print(f"Starting batch fetch of {len(urls)} URLs with concurrency {args.concurrency}...")

        rotator = ProxyRotator(proxy_file=args.proxies) if args.proxies else None
        fetcher = AsyncBatchFetcher(
            concurrency=args.concurrency,
            rotate_browsers=args.rotate_browsers,
            proxy_rotator=rotator,
        )

        results = asyncio.run(fetcher.fetch_all(urls))

        if args.extract_text:
            for item in results:
                if item["success"]:
                    item["text"] = DataExtractor.extract_text(item["html"])
                    del item["html"]

        save_to_json(args.output, results)


if __name__ == "__main__":
    main()
