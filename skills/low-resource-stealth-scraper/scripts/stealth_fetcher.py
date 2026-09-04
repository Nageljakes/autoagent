#!/usr/bin/env python3
"""
Lightweight Stealth Web Fetcher & Scraper
Uses curl_cffi for JA3/JA4 TLS fingerprint impersonation with minimal RAM usage.
"""

import sys
import json
import argparse
from typing import Optional, Dict, Any
from curl_cffi import requests
from bs4 import BeautifulSoup

DEFAULT_IMPERSONATE = "chrome124"

class StealthSession:
    def __init__(self, impersonate: str = DEFAULT_IMPERSONATE, proxy: Optional[str] = None):
        self.impersonate = impersonate
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.session = requests.Session(impersonate=self.impersonate, proxies=self.proxies)

    def get(self, url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
        return self.session.get(url, headers=headers, timeout=15, **kwargs)

    def post(self, url: str, data: Any = None, json_data: Any = None, headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
        return self.session.post(url, data=data, json=json_data, headers=headers, timeout=15, **kwargs)

    def fetch_text(self, url: str) -> str:
        res = self.get(url)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        
        # Remove non-content tags
        for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            element.decompose()
            
        return soup.get_text(separator="\n", strip=True)

    def close(self):
        self.session.close()

def quick_fetch(url: str, impersonate: str = DEFAULT_IMPERSONATE, proxy: Optional[str] = None) -> requests.Response:
    proxies = {"http": proxy, "https": proxy} if proxy else None
    return requests.get(url, impersonate=impersonate, proxies=proxies, timeout=15)

def main():
    parser = argparse.ArgumentParser(description="Stealth Fetcher (curl_cffi)")
    parser.add_argument("url", help="Target URL to fetch")
    parser.add_argument("--impersonate", default="chrome124", help="Browser to impersonate (chrome124, safari17_0, edge101, etc.)")
    parser.add_argument("--proxy", default=None, help="Proxy URL (e.g. http://user:pass@host:port or socks5://host:port)")
    parser.add_argument("--text-only", action="store_true", help="Extract readable text content from HTML")
    parser.add_argument("--json", action="store_true", help="Parse and output formatted JSON")
    parser.add_argument("--headers-only", action="store_true", help="Display only response status and headers")

    args = parser.parse_args()

    session = StealthSession(impersonate=args.impersonate, proxy=args.proxy)
    try:
        if args.text_only:
            text = session.fetch_text(args.url)
            print(text)
            return

        res = session.get(args.url)
        
        if args.headers_only:
            print(f"Status: {res.status_code}")
            for k, v in res.headers.items():
                print(f"{k}: {v}")
            return

        if args.json:
            print(json.dumps(res.json(), indent=2))
        else:
            print(res.text)

    except Exception as e:
        print(f"Error fetching URL: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()

if __name__ == "__main__":
    main()
