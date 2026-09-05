#!/usr/bin/env python3
"""
Authenticated Session Script for Dealership CRM Portal
Uses curl_cffi to maintain session cookies after login with TLS impersonation.
Dynamically resolves CRM endpoints without hardcoded domain dependencies.
"""

import sys
import os
import getpass
import argparse
from pathlib import Path
from urllib.parse import urlparse, urljoin
from curl_cffi import requests
from bs4 import BeautifulSoup

DEFAULT_CONFIG_PATH = Path(os.path.expanduser("~/.config/dealer_credentials.env"))
LOCAL_ENV_PATH = Path.cwd() / ".env"

def _parse_env_file(path: Path):
    username, password = None, None
    if not path.exists():
        return None, None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("\"'")
            if key not in os.environ:
                os.environ[key] = val
            if key == "CRM_USERNAME":
                username = val
            elif key == "CRM_PASSWORD":
                password = val
    return username, password

def load_credentials_from_env_file(path: Path = None):
    candidates = []
    if path and Path(path).exists():
        candidates.append(Path(path))
    if LOCAL_ENV_PATH.exists():
        candidates.append(LOCAL_ENV_PATH)
    if DEFAULT_CONFIG_PATH.exists():
        candidates.append(DEFAULT_CONFIG_PATH)
    
    for c_path in candidates:
        username, password = _parse_env_file(c_path)
        if username and password:
            return username, password
    return None, None

# Auto-load env configurations on import
load_credentials_from_env_file()

def get_login_url(session: requests.Session = None) -> str:
    """
    Dynamically determines the CRM POST action endpoint from CRM_LOGIN_URL.
    If pointing to a landing page (e.g. dealership portal), inspects the form action.
    """
    raw_url = os.getenv("CRM_LOGIN_URL", "").strip()
    if not raw_url:
        raise ValueError("CRM_LOGIN_URL environment variable is not configured.")
    
    if raw_url.endswith(".cfm") or "/checkserver" in raw_url:
        return raw_url

    # Check landing page for form action if given a portal root
    try:
        s = session or requests.Session(impersonate="chrome124")
        resp = s.get(raw_url, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form")
        if form and form.get("action"):
            return urljoin(raw_url, form.get("action").strip())
    except Exception:
        pass

    return f"{raw_url.rstrip('/')}/checkserver.cfm"

def get_base_url() -> str:
    """Returns the CRM base URL from environment or previously resolved session."""
    base_url = os.getenv("CRM_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return base_url
    
    # Fallback to base of CRM_LOGIN_URL
    raw_login = os.getenv("CRM_LOGIN_URL", "").strip()
    if raw_login:
        parsed = urlparse(raw_login)
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""

def login(username: str, password: str, impersonate: str = "chrome124"):
    session = requests.Session(impersonate=impersonate)
    login_url = get_login_url(session)
    
    payload = {
        "dec1": username,
        "dec2": password,
    }
    
    parsed = urlparse(login_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = {
        "Origin": origin,
        "Referer": f"{origin}/",
    }

    print(f"Connecting to {login_url} with {impersonate} TLS signature...", file=sys.stderr)
    res = session.post(login_url, data=payload, headers=headers, allow_redirects=True, timeout=20)
    
    print(f"Response Status: {res.status_code}", file=sys.stderr)
    print(f"Final URL: {res.url}", file=sys.stderr)

    # Automatically capture and set CRM_BASE_URL from final redirected portal URL
    if res.url:
        final_parsed = urlparse(res.url)
        detected_base = f"{final_parsed.scheme}://{final_parsed.netloc}"
        if not os.getenv("CRM_BASE_URL"):
            os.environ["CRM_BASE_URL"] = detected_base
    
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
