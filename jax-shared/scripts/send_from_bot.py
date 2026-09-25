#!/usr/bin/env python3
"""
send_from_bot.py - Outbound Dispatch Tool for Bot Dedicated Number (+27 79 395 0395)

Dispatches outbound WhatsApp messages, images, and documents directly from the bot's
own phone number via the jax-whatsapp-agent REST API (http://127.0.0.1:9096/send).

Usage:
  python3 send_from_bot.py --phone "0827398595" --message "Hello from Tiny!"
  python3 send_from_bot.py -p "27827398595" -m "Document attached" --doc "/path/to/doc.pdf"
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.error

API_URL = os.getenv("BOT_API_URL", "http://127.0.0.1:9096/send")
HEALTH_URL = os.getenv("BOT_HEALTH_URL", "http://127.0.0.1:9096/health")

def sanitize_dashes(text: str) -> str:
    if not text:
        return ""
    # STRICT LONG DASH BAN: Em dash, en dash, horizontal bar -> standard hyphen
    return text.replace("—", "-").replace("–", "-").replace("―", "-")

def normalize_phone(phone: str) -> str:
    phone = phone.strip()
    if "@lid" in phone:
        return phone
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "27" + digits[1:]
    return digits

def resolve_lid_for_phone(phone: str) -> str:
    if "@lid" in phone:
        return phone
    db_path = os.getenv("SQLITE_DB_PATH", os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/prospects.db")))
    if not os.path.exists(db_path):
        return phone
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT lid FROM phone_lid_mapping WHERE phone = ?", (phone,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return phone

def check_bot_health():
    try:
        req = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "send_from_bot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("connection") == "CONNECTED"
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Send outbound WhatsApp message from bot number (+27 79 395 0395)")
    parser.add_argument("-p", "--phone", required=True, help="Recipient phone number (e.g. 082..., 2782..., or @lid)")
    parser.add_argument("-m", "--message", default="", help="Message text content")
    parser.add_argument("-i", "--image", default="", help="Path to image file attachment")
    parser.add_argument("-d", "--doc", default="", help="Path to PDF/document file attachment")
    parser.add_argument("-a", "--audio", default="", help="Path to audio file attachment")
    parser.add_argument("-v", "--voice", default="", help="Text to synthesize into a voice note and dispatch (English: Fish Audio cloned voice, Afrikaans: Willem Edge TTS)")
    parser.add_argument("--tts", default="", help="Alias for --voice")
    parser.add_argument("--authorized-by", default="jakes_explicit_command", help="Authorization identifier")
    parser.add_argument("--json", action="store_true", help="Output raw JSON response")

    args = parser.parse_args()

    clean_phone = normalize_phone(args.phone)
    if not clean_phone:
        print("Error: Invalid recipient phone number", file=sys.stderr)
        sys.exit(1)

    target_phone = resolve_lid_for_phone(clean_phone)
    if target_phone != clean_phone:
        print(f"Direct LID routing active: Resolved {clean_phone} -> {target_phone}")

    clean_msg = sanitize_dashes(args.message)
    voice_text = sanitize_dashes(args.voice or args.tts)

    temp_voice_ogg = None
    if voice_text:
        import time
        sys.path.insert(0, "/home/jakes/jax-shared")
        from audio_processor import synthesize_to_ogg_opus
        temp_voice_ogg = f"/tmp/send_bot_vn_{os.getpid()}_{int(time.time())}.ogg"
        if not synthesize_to_ogg_opus(voice_text, temp_voice_ogg, voice="auto"):
            print("Error: Failed to synthesize voice note", file=sys.stderr)
            sys.exit(1)
        args.audio = temp_voice_ogg

    if not clean_msg and not args.image and not args.doc and not args.audio:
        print("Error: Message body or attachment (image, doc, audio, voice) is required", file=sys.stderr)
        sys.exit(1)

    if args.image and not os.path.exists(args.image):
        print(f"Error: Image file not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    if args.doc and not os.path.exists(args.doc):
        print(f"Error: Document file not found: {args.doc}", file=sys.stderr)
        sys.exit(1)

    if args.audio and not os.path.exists(args.audio):
        print(f"Error: Audio file not found: {args.audio}", file=sys.stderr)
        sys.exit(1)

    payload = {
        "phone": target_phone,
        "message": clean_msg,
        "authorizedBy": args.authorized_by
    }
    if args.image:
        payload["imagePath"] = os.path.abspath(args.image)
    if args.doc:
        payload["documentPath"] = os.path.abspath(args.doc)
    if args.audio:
        payload["audioPath"] = os.path.abspath(args.audio)

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=req_data,
        headers={"Content-Type": "application/json", "User-Agent": "send_from_bot/1.0"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            resp_body = resp.read().decode("utf-8")
            data = json.loads(resp_body)
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                msg_id = data.get("messageId", "UNKNOWN")
                recipient = data.get("recipient", clean_phone)
                sender = data.get("sender", "27793950395")
                print(f"Success: Message sent from bot (+{sender}) to {recipient} (ID: {msg_id})")
            sys.exit(0)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
            err_msg = err_json.get("error", err_body)
        except Exception:
            err_msg = err_body
        print(f"HTTP Error {e.code}: {err_msg}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection Error: Could not reach bot API at {API_URL}: {e.reason}", file=sys.stderr)
        print("Ensure jax-whatsapp PM2 process is running.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if temp_voice_ogg and os.path.exists(temp_voice_ogg):
            try:
                os.unlink(temp_voice_ogg)
            except OSError:
                pass

if __name__ == "__main__":
    main()
