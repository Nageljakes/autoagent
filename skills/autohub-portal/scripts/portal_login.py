#!/usr/bin/env python3
"""
Authenticated Session Script for Dealer Portal (dealer-crm.co.za)
Uses curl_cffi to maintain session cookies after login with TLS impersonation.
"""

import sys
import os
import getpass
import argparse
from pathlib import Path
from curl_cffi import requests
from bs4 import BeautifulSoup

LOGIN_URL = (os.getenv("CRM_LOGIN_URL", "https://login.dealer-crm.co.za") + "/checkserver.cfm")
DEFAULT_CONFIG_PATH = Path(os.path.expanduser("~/.config/dealer_credentials.env"))
LOCAL_ENV_PATH = Path.cwd() / ".env"

def load_credentials_from_env_file(path: Path = None):
    candidates = []
    if path and Path(path).exists():
        candidates.append(Path(path))
    if DEFAULT_CONFIG_PATH.exists():
        candidates.append(DEFAULT_CONFIG_PATH)
    if LOCAL_ENV_PATH.exists():
        candidates.append(LOCAL_ENV_PATH)
    
    for c_path in candidates:
        username, password = _parse_env_file(c_path)
        if username and password:
            return username, password
    return None, None

def _parse_env_file(path: Path):
    username, password = None, None
    username, password = None, None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("\"'")
            if key == "CRM_USERNAME":
                username = val
            elif key == "CRM_PASSWORD":
                password = val
    return username, password

def login(username: str, password: str, impersonate: str = "chrome124"):
    session = requests.Session(impersonate=impersonate)
    
    payload = {
        "dec1": username,
        "dec2": password,
    }
    
    headers = {
        "Origin": "https://dealer-portal.example.com",
        "Referer": "https://dealer-portal.example.com/",
    }

    print(f"Connecting to {LOGIN_URL} with {impersonate} TLS signature...", file=sys.stderr)
    res = session.post(LOGIN_URL, data=payload, headers=headers, allow_redirects=True, timeout=20)
    
    print(f"Response Status: {res.status_code}", file=sys.stderr)
    print(f"Final URL: {res.url}", file=sys.stderr)
    
    # Check if cookies were assigned
    cookies = session.cookies.get_dict()
    print(f"Session Cookies: {list(cookies.keys())}", file=sys.stderr)
    
    soup = BeautifulSoup(res.text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else "No Title"
    print(f"Page Title: {title}", file=sys.stderr)
    
    return session, res

def main():
    parser = argparse.ArgumentParser(description="Dealer Portal Login Script")
    parser.add_argument("--username", help="Portal Username (optional)")
    parser.add_argument("--password", help="Portal Password (optional)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to credentials env file")
    args = parser.parse_args()

    username = args.username or os.getenv("CRM_USERNAME")
    password = args.password or os.getenv("CRM_PASSWORD")

    if not username or not password:
        file_user, file_pass = load_credentials_from_env_file(Path(args.config))
        username = username or file_user
        password = password or file_pass

    if not username:
        username = input("Username: ")
    if not password:
        password = getpass.getpass("Password (hidden): ")

    if not username or not password:
        print("Username and password are required.", file=sys.stderr)
        sys.exit(1)

    session, response = login(username, password)
    
    print("\nInitial post-login content preview:")
    lines = [line.strip() for line in response.text.splitlines() if line.strip()][:15]
    print("\n".join(lines))

if __name__ == "__main__":
    main()
