#!/usr/bin/env python3
import sys
import os
import urllib.request
import urllib.parse
import json
import time
import base64
import requests

def get_env_credentials():
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    cf_account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    
    # Try reading from known .env files if missing
    if not cf_token or not cf_account:
        home_dir = os.environ.get("HOME") or os.path.expanduser("~")
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_paths = [
            os.path.join(repo_dir, "jax-whatsapp-agent", ".env"),
            os.path.join(repo_dir, "jax-telegram-agent", ".env"),
            os.path.join(home_dir, "jax-whatsapp-agent", ".env"),
            os.path.join(home_dir, "jax-telegram-agent", ".env"),
            os.path.join(home_dir, ".secrets.env"),
            os.path.join(home_dir, ".env")
        ]
        for p in env_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("export "):
                                line = line[7:].strip()
                            if line.startswith("CLOUDFLARE_API_TOKEN=") and not cf_token:
                                val = line.split("=", 1)[1].strip()
                                cf_token = val.strip("'").strip('"')
                            elif line.startswith("CLOUDFLARE_ACCOUNT_ID=") and not cf_account:
                                val = line.split("=", 1)[1].strip()
                                cf_account = val.strip("'").strip('"')
                except Exception:
                    pass
    return cf_token, cf_account

def generate_with_cloudflare(prompt, output_path, cf_token, cf_account):
    if not cf_token or not cf_account:
        return False
    
    url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    headers = {
        "Authorization": f"Bearer {cf_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "prompt": prompt,
        "steps": 4
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=45)
        if res.status_code == 200 and res.content:
            content_type = res.headers.get("content-type", "")
            # Cloudflare Workers AI returns a JSON response containing base64 encoded image data
            if "application/json" in content_type or res.content.strip().startswith(b"{"):
                try:
                    data = res.json()
                    b64_str = None
                    if isinstance(data, dict):
                        if "result" in data and isinstance(data["result"], dict) and "image" in data["result"]:
                            b64_str = data["result"]["image"]
                        elif "image" in data:
                            b64_str = data["image"]
                        elif "result" in data and isinstance(data["result"], str):
                            b64_str = data["result"]
                    
                    if b64_str:
                        img_bytes = base64.b64decode(b64_str)
                        if len(img_bytes) > 1000:
                            with open(output_path, "wb") as f:
                                f.write(img_bytes)
                            return True
                    print(f"Cloudflare Workers AI JSON response missing valid base64 image: {res.text[:200]}", file=sys.stderr)
                    return False
                except Exception as json_err:
                    print(f"Failed to parse Cloudflare JSON response: {json_err}", file=sys.stderr)
                    return False
            else:
                # Raw binary image
                if len(res.content) > 1000:
                    with open(output_path, "wb") as f:
                        f.write(res.content)
                    return True
                else:
                    print(f"Cloudflare Workers AI returned small binary payload ({len(res.content)} bytes)", file=sys.stderr)
                    return False
        else:
            print(f"Cloudflare Workers AI returned status {res.status_code}: {res.text[:200]}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Cloudflare Workers AI request failed: {e}", file=sys.stderr)
        return False

def generate_with_pollinations(prompt, output_path, model="flux"):
    clean_prompt = prompt.strip()
    encoded = urllib.parse.quote(clean_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&model={model}"
    
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Tiny-AI-Agent-Jaxtech)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
        if len(data) < 1000:
            raise Exception(f"Image generation failed or returned empty data ({len(data)} bytes)")
        with open(output_path, "wb") as f:
            f.write(data)
    return output_path

def generate_image(prompt, output_path, model="flux"):
    cf_token, cf_account = get_env_credentials()
    
    # Try Cloudflare Flux first for highest quality & speed
    if cf_token and cf_account:
        print(f"Attempting generation via Cloudflare Workers AI Flux...", file=sys.stderr)
        if generate_with_cloudflare(prompt, output_path, cf_token, cf_account):
            print(f"Generated via Cloudflare Workers AI Flux successfully.", file=sys.stderr)
            return output_path
        print(f"Cloudflare Workers AI failed, falling back to Pollinations...", file=sys.stderr)
    
    # Fallback to Pollinations
    return generate_with_pollinations(prompt, output_path, model)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: image_generator.py <prompt> <output_path> [model]", file=sys.stderr)
        sys.exit(1)
    prompt = sys.argv[1]
    out_path = sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else "flux"
    try:
        generate_image(prompt, out_path, model)
        print(f"OK:{out_path}")
    except Exception as e:
        print(f"ERROR:{str(e)}", file=sys.stderr)
        sys.exit(1)
