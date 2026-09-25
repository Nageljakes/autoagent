#!/usr/bin/env python3
"""
send_from_jakes.py - Outbound Dispatch Tool for Jakes Personal WhatsApp (+27 82 739 8595)

Dispatches outbound WhatsApp messages, images, documents, and voice notes directly from
Jakes' personal number via the jax-whatsapp-monitor REST API (http://127.0.0.1:9095/send).

Voice Note Standard:
- English text: Fish Audio S2.1 Pro in Jakes' own cloned voice (model: b06f33f8d587483dbbae9b45c3b6b665).
- Afrikaans text: Microsoft Edge TTS Willem (af-ZA-WillemNeural).

Usage:
  python3 send_from_jakes.py --phone "0821234567" --message "Good day! Jakes here."
  python3 send_from_jakes.py --phone "0821234567" --voice "Hi there, just following up regarding your vehicle inquiry."
  python3 send_from_jakes.py --phone "0821234567" --voice "Goeiedag, Jakes hier van BB Gezina Nissan."
"""

import sys
import os
import json
import time
import argparse
import urllib.request
import urllib.error

API_URL = os.getenv("MONITOR_API_URL", "http://127.0.0.1:9095/send")
HEALTH_URL = os.getenv("MONITOR_HEALTH_URL", "http://127.0.0.1:9095/health")

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

def check_bridge_health():
    try:
        req = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "send_from_jakes/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("connection") == "CONNECTED"
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Send outbound WhatsApp message from Jakes' personal number (+27 82 739 8595)")
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
        sys.path.insert(0, "/home/jakes/jax-shared")
        try:
            from audio_processor import synthesize_to_ogg_opus
            temp_voice_ogg = f"/tmp/send_jakes_vn_{os.getpid()}_{int(time.time())}.ogg"
            if not synthesize_to_ogg_opus(voice_text, temp_voice_ogg, voice="auto"):
                print("Error: Failed to synthesize voice note", file=sys.stderr)
                sys.exit(1)
            args.audio = temp_voice_ogg
        except Exception as e:
            print(f"Error during voice note synthesis: {e}", file=sys.stderr)
            sys.exit(1)

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
        headers={"Content-Type": "application/json", "User-Agent": "send_from_jakes/1.0"},
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
                media_info = " (Voice Note)" if args.audio else (" (Image)" if args.image else (" (Document)" if args.doc else ""))
                print(f"Success: Message sent from Jakes (+27 82 739 8595) to {recipient}{media_info} (ID: {msg_id})")
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
        print(f"Connection Error: Could not reach WhatsApp monitor API at {API_URL}: {e.reason}", file=sys.stderr)
        print("Ensure jax-whatsapp-monitor PM2 process is running.", file=sys.stderr)
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
