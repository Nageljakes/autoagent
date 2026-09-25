#!/usr/bin/env python3
"""
Compatibility wrapper: nissan_login -> portal_login
Ensures legacy scripts importing nissan_login work seamlessly with dynamic portal resolution.
"""
from portal_login import login, load_credentials_from_env_file, get_login_url, get_base_url

__all__ = ["login", "load_credentials_from_env_file", "get_login_url", "get_base_url"]
