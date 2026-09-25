#!/usr/bin/env python3
"""
JAX Audio Processor - Unified Multi-Platform TTS & STT Engine
Platforms: Jakes Personal WhatsApp (Monitor), Bot Dedicated WhatsApp, Telegram

Language Hybrid Routing:
- English (en-ZA / en-US): Fish Audio S2.1 Pro with Jakes' cloned voice (model: b06f33f8d587483dbbae9b45c3b6b665).
  Automatic graceful fallback to Edge TTS en-ZA-LukeNeural if Fish Audio experiences network issues.
- Afrikaans (af-ZA): Microsoft Edge TTS af-ZA-WillemNeural for pure, authentic South African pronunciation.
"""

import socket
import ssl
import hashlib
import time
import uuid
import struct
import base64
import os
import sys
import json
import re
import subprocess
from datetime import datetime as dt
from datetime import timezone as tz

# Microsoft Edge TTS WebSocket Constants
TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
WIN_EPOCH = 11644473600
CHROMIUM_FULL_VERSION = "143.0.3650.75"
SEC_MS_GEC_VERSION = f"1-{CHROMIUM_FULL_VERSION}"

# Fish Audio Default Fallback Credentials (TTS Engine)
DEFAULT_FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "")
DEFAULT_FISH_AUDIO_REFERENCE_ID = os.getenv("FISH_AUDIO_REFERENCE_ID", "")

# Groq Whisper Default Fallback Credentials (Whisper Turbo High-Fidelity STT)
DEFAULT_GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEFAULT_GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

def load_env_fallbacks():
    """Load credentials from known .env and secret files if not already in os.environ."""
    env_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../.env")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".env")),
        os.path.expanduser("~/.config/dealer_credentials.env"),
        os.path.expanduser("~/.secrets.env"),
        "/home/jakes/jax-shared/.env",
        "/home/jakes/.secrets.env"
    ]
    for p in env_paths:
        if os.path.exists(p) and os.access(p, os.R_OK):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("export "):
                            line = line[7:].strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("\"'")
                            if k and k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass

load_env_fallbacks()

# Comprehensive South African Afrikaans Keywords and Phrases
AFRIKAANS_PHRASES = [
    "ek wil", "ek is", "ek het", "ek sal", "ek kan", "ek dink", "ek hoop", "ek volg",
    "kan jy", "kan u", "sal jy", "sal u", "wil jy", "wil u", "moet ek", "moet jy",
    "laat weet", "hoe gaan", "hoe gaan dit", "baie dankie", "goeie dag", "goeie middag", "goeie more",
    "goeie môre", "goeie naand", "as dit", "as jy", "wanneer sal", "vinnige geselsie",
    "vinnige luitjie", "ek volg op", "ek wil hoor", "stuur vir", "kontak my", "bel my",
    "hoeveel kos", "hoe lyk", "jakes hier", "hier van", "hier weer", "dag se", "dagsê", "goeie naand",
    "gesels later", "sterkte met", "alles van die beste", "bly te kenne", "dit is reg", "dit is goed",
    "hallo jakes", "hallo daar", "ja dankie", "nee dankie", "mooi loop", "lekker dag", "veilig ry",
    "skakel my", "praat weer", "wanneer pas dit", "wat dink jy", "hoor graag van jou"
]

AFRIKAANS_KEYWORDS = {
    "ek", "jy", "ons", "hulle", "julle", "nie", "baie", "goeiedag", "goeiemôre", "goeiemore",
    "goeienaand", "goeiemiddag", "asseblief", "groete", "bakkie", "kar", "karre", "mooi", "lekker", "netjies",
    "vandag", "gister", "môre", "more", "nou", "hierdie", "daardie", "hier", "daar", "maar",
    "want", "omdat", "sodat", "soos", "saam", "deur", "oor", "teen", "nuwe", "goeie", "ander",
    "wees", "word", "geword", "het", "sal", "wil", "moet", "kan", "sou", "wou", "kon",
    "met", "vir", "tot", "na", "wat", "wie", "waar", "wanneer", "hoe", "waarom",
    "enige", "kwotasie", "voorraad", "aflewering", "kliënt", "klient", "kontak", "prys",
    "dankie", "terugvoer", "praat", "later", "weer", "beskikbaar", "stuur", "gou",
    "boodskap", "nommer", "voertuig", "diens", "vanoggend", "vanmiddag", "gesels", "skakel",
    "vinnige", "hoeveel", "inruil", "toetsrit", "finansiering", "deposito", "hoor", "sommer",
    "graag", "besig", "seblief", "luitjie", "koop", "verkoop", "gebruikte", "aflewer", "program",
    "hallo", "ja", "nee", "dit", "reg", "alweer", "dagsê", "dagse", "oggend", "hartlik", "luister",
    "kyk", "alles", "almal", "wonderlik", "puik"
}

ENGLISH_PHRASES = [
    "good day", "good morning", "good afternoon", "good evening",
    "let me know", "i am", "i would", "i will", "i have", "can you", "could you",
    "would you", "are you", "do you", "have you", "will you", "when can", "how are",
    "how is", "hope you", "how your", "your schedule", "good time", "quick check",
    "happy to assist", "give you a call", "give me a call", "right here", "after hours",
    "trade in", "test drive", "vehicle search", "hear from you", "looking for",
    "jakes here", "here from", "here again", "please send", "send me"
]

ENGLISH_KEYWORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for", "not", "on",
    "with", "he", "as", "you", "do", "at", "this", "but", "his", "by", "from", "they", "we",
    "say", "her", "she", "or", "an", "will", "my", "one", "all", "would", "there", "their",
    "what", "so", "up", "out", "if", "about", "who", "get", "which", "go", "me", "when",
    "make", "can", "like", "time", "no", "just", "him", "know", "take", "people", "into",
    "year", "your", "good", "some", "could", "them", "see", "other", "than", "then", "now",
    "look", "only", "come", "its", "over", "think", "also", "back", "after", "use", "two",
    "how", "our", "work", "first", "well", "way", "even", "new", "want", "because", "any",
    "these", "give", "day", "most", "us", "quotation", "available", "inventory", "feedback",
    "customer", "morning", "afternoon", "evening", "please", "thanks", "hello", "quote",
    "pricing", "finance", "application", "delivery", "deposit", "quick", "check", "checking"
}

def detect_language(text):
    """Detect whether text is Afrikaans (af-ZA) or English (en-ZA)."""
    if not text:
        return "en-ZA"
    t = text.lower()
    
    af_score = 0
    en_score = 0
    
    # 1. Check multi-word phrase patterns (+4 weight)
    for p in AFRIKAANS_PHRASES:
        if p in t:
            af_score += 4
    for p in ENGLISH_PHRASES:
        if p in t:
            en_score += 4
            
    # 2. Check individual words (+1 weight)
    words = re.findall(r"\b[a-zA-Záéíóúäëïöüâêîôû\'-]+\b", t)
    for w in words:
        if w in AFRIKAANS_KEYWORDS:
            af_score += 1
        elif w in ENGLISH_KEYWORDS:
            en_score += 1
        # Check Afrikaans past tense verb prefix (ge-)
        elif len(w) > 4 and w.startswith("ge") and not w.startswith("get"):
            af_score += 1

    return "af-ZA" if af_score > en_score else "en-ZA"

def clean_text_for_speech(text):
    """Clean markdown, code artifacts, file links, and synthesis boilerplate for natural speech."""
    if not text:
        return ""
    
    # Check if there is an explicit transcript section in the text (e.g. ### Spoken Audio Transcript)
    transcript_match = re.search(r"(?:Spoken Audio Transcript|Transcript|Spoken Voice Note)[\s\S]*?(?=\n\n###|\n\n---|(?:\n\n\s*The audio files)|$)", text, re.IGNORECASE)
    if transcript_match:
        content = transcript_match.group(0)
        content = re.sub(r"^(?:#+\s*)?(?:📝\s*)?(?:Spoken Audio Transcript|Transcript|Spoken Voice Note)[:\s]*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"^[\s>]*(\*\*|\*)?\[\d+:\d+\](\*\*|\*)?\s*", "", content, flags=re.MULTILINE)
        content = re.sub(r"^[\s>]+", "", content, flags=re.MULTILINE)
        text = content
    else:
        # Strip all audio player sections, links, and status logs
        text = re.sub(r"<audio[\s\S]*?</audio>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"###\s*🎧\s*Audio Files[\s\S]*?(?=###|---|\n\n[A-Z0-9]|$)", "", text, flags=re.IGNORECASE)
        text = re.sub(r"The audio files are ready:[\s\S]*?(?=\n\n[A-Z0-9]|\n\nLet me know|$)", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\*\s*\*\*(?:Voice Note|Native Telegram|MP3|Standard Audio).*?\*\*:\s*\[`.*?`\]\(file:\/\/.*?\).*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r"^(?:Synthesizing|Encoding|Generating|Processing)\s+.*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r"^I (?:have|apologize|am).*(?:synthesized|created|saved|disk|stream|player|voicenote|voice note|mp3|audio).*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r"^You can (?:view|listen|access).*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r"^Here is (?:the|your|a) (?:voice note|voicenote|audio).*?:?", "", text, flags=re.MULTILINE | re.IGNORECASE)
        text = re.sub(r"^In this (?:voice note|voicenote|audio),?\s*", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # Clean markdown and formatting
    text = re.sub(r"```[a-zA-Z]*\n[\s\S]*?\n```", " [Code block omitted in audio; see text message] ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Strip raw URLs & file paths
    text = re.sub(r"file:///\S+", "", text)
    text = re.sub(r"https?://\S+", "", text)
    # Strip headers, bullet symbols, bold/italics, quotes
    text = re.sub(r"^[#>\-\*\+\s]+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[\*_~#\|\"\']", "", text)
    text = re.sub(r"---", "", text)
    # Remove timestamps if any remain
    text = re.sub(r"\[\d+:\d+\]", "", text)
    # STRICT LONG DASH BAN: convert em/en dashes
    text = text.replace("—", "-").replace("–", "-").replace("―", "-")
    # Clean whitespace
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    
    # Trim overly long text for voice synthesis if necessary (keep within ~1500 chars)
    if len(text) > 1500:
        truncated = text[:1500]
        last_period = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
        if last_period > 800:
            text = truncated[:last_period + 1] + " I've included the rest of the details in the text message."
        else:
            text = truncated + "... I've sent the complete details in the text message."
    return text

def raw_edge_tts(text, output_mp3, voice="af-ZA-WillemNeural"):
    """Synthesize text to MP3 using Microsoft Edge TTS WebSocket protocol."""
    unix_ts = dt.now(tz.utc).timestamp()
    ticks = unix_ts + WIN_EPOCH
    ticks -= ticks % 300
    ticks *= 10_000_000
    str_to_hash = f"{ticks:.0f}{TRUSTED_CLIENT_TOKEN}"
    sec_ms_gec = hashlib.sha256(str_to_hash.encode("ascii")).hexdigest().upper()

    conn_id = uuid.uuid4().hex
    host = "speech.platform.bing.com"
    path = f"/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken={TRUSTED_CLIENT_TOKEN}&Sec-MS-GEC={sec_ms_gec}&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}&ConnectionId={conn_id}"

    ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
    headers = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {ws_key}",
        "Sec-WebSocket-Version: 13",
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
        "Pragma: no-cache",
        "Cache-Control: no-cache",
        "Origin: chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
        "Accept-Encoding: gzip, deflate, br",
        "Accept-Language: en-US,en;q=0.9",
        "\r\n"
    ]
    req = "\r\n".join(headers).encode("ascii")

    try:
        raw_sock = socket.create_connection((host, 443), timeout=15)
        ssock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host)
        ssock.sendall(req)

        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = ssock.recv(1024)
            if not chunk:
                break
            resp += chunk

        def encode_ws_frame(payload, opcode=0x1):
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            length = len(payload)
            frame = bytearray()
            frame.append(0x80 | (opcode & 0x0F))
            mask_key = os.urandom(4)
            if length <= 125:
                frame.append(0x80 | length)
            elif length <= 65535:
                frame.append(0x80 | 126)
                frame.extend(struct.pack(">H", length))
            else:
                frame.append(0x80 | 127)
                frame.extend(struct.pack(">Q", length))
            frame.extend(mask_key)
            masked_payload = bytearray(length)
            for i in range(length):
                masked_payload[i] = payload[i] ^ mask_key[i % 4]
            frame.extend(masked_payload)
            return bytes(frame)

        def recv_exact(n):
            buf = bytearray()
            while len(buf) < n:
                c = ssock.recv(n - len(buf))
                if not c:
                    raise EOFError("closed")
                buf.extend(c)
            return bytes(buf)

        def decode_frame():
            head = recv_exact(2)
            b1, b2 = head[0], head[1]
            opcode = b1 & 0x0F
            masked = (b2 & 0x80) != 0
            payload_len = b2 & 0x7F
            if payload_len == 126:
                payload_len = struct.unpack(">H", recv_exact(2))[0]
            elif payload_len == 127:
                payload_len = struct.unpack(">Q", recv_exact(8))[0]
            mask_key = recv_exact(4) if masked else None
            data = recv_exact(payload_len)
            if masked:
                unmasked = bytearray(len(data))
                for i in range(len(data)):
                    unmasked[i] = data[i] ^ mask_key[i % 4]
                data = bytes(unmasked)
            return opcode, data

        config_body = json.dumps({
            "context": {
                "synthesis": {
                    "audio": {
                        "metadataoptions": {"sentenceBoundaryEnabled": "false", "wordBoundaryEnabled": "false"},
                        "outputFormat": "audio-24khz-48kbitrate-mono-mp3"
                    }
                }
            }
        })
        ssock.sendall(encode_ws_frame(f"Content-Type:application/json; charset=utf-8\r\nPath:speech.config\r\n\r\n{config_body}"))

        req_id = uuid.uuid4().hex
        clean_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&apos;")
        lang_code = "-".join(voice.split("-")[:2]) if "-" in voice else "en-US"
        ssml = f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{lang_code}'><voice name='{voice}'>{clean_text}</voice></speak>"
        ssml_msg = f"X-RequestId:{req_id}\r\nContent-Type:application/ssml+xml\r\nPath:ssml\r\n\r\n{ssml}"
        ssock.sendall(encode_ws_frame(ssml_msg))

        audio_chunks = []
        while True:
            opcode, data = decode_frame()
            if opcode == 0x8:
                break
            elif opcode == 0x1:
                txt = data.decode("utf-8", errors="ignore")
                if "Path:turn.end" in txt:
                    break
            elif opcode == 0x2:
                if len(data) > 2:
                    header_len = struct.unpack(">H", data[:2])[0]
                    audio_chunks.append(data[2 + header_len:])

        ssock.close()
        if audio_chunks:
            full_audio = b"".join(audio_chunks)
            os.makedirs(os.path.dirname(os.path.abspath(output_mp3)), exist_ok=True)
            with open(output_mp3, "wb") as f:
                f.write(full_audio)
            return True
        return False
    except Exception as e:
        print(f"[Edge TTS Error] Synthesis failed: {e}", file=sys.stderr)
        return False

def raw_fish_audio_tts(text, output_mp3, api_key=None, reference_id=None):
    """Synthesize text using Fish Audio S2.1 Pro Free API with Jakes' cloned voice.
    Returns True if audio successfully written, False otherwise."""
    import urllib.request
    import urllib.error

    api_key = api_key or os.environ.get("FISH_AUDIO_API_KEY") or DEFAULT_FISH_AUDIO_API_KEY
    reference_id = reference_id or os.environ.get("FISH_AUDIO_REFERENCE_ID") or DEFAULT_FISH_AUDIO_REFERENCE_ID
    if not api_key or not reference_id:
        return False

    url = "https://api.fish.audio/v1/tts"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": "s2.1-pro-free"
    }
    payload = {
        "text": text,
        "reference_id": reference_id,
        "format": "mp3"
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=25) as resp:
            if resp.status == 200:
                audio_bytes = resp.read()
                if audio_bytes and len(audio_bytes) > 100:
                    os.makedirs(os.path.dirname(os.path.abspath(output_mp3)), exist_ok=True)
                    with open(output_mp3, "wb") as f:
                        f.write(audio_bytes)
                    return True
        return False
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")[:150]
        print(f"[TTS Warning] Fish Audio HTTP {e.code}: {err_body}. Falling back to Edge TTS...", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[TTS Warning] Fish Audio connection failed: {e}. Falling back to Edge TTS...", file=sys.stderr)
        return False

def synthesize_to_ogg_opus(raw_text, output_ogg, voice="auto"):
    """Synthesize text and convert to native voice note format (.ogg with opus).
    Bulletproof Multi-Platform Voice Standard:
    1. Afrikaans (af-ZA): Always routed to Microsoft Edge TTS af-ZA-WillemNeural (authentic South African Afrikaans accent).
    2. English (en-ZA): Always synthesized in Jakes' cloned voice via Fish Audio S2.1 Pro (with LukeNeural fallback).
    3. Explicit voice overrides (e.g. 'willem', 'luke', 'fish', 'cloned') are always respected."""
    spoken_text = clean_text_for_speech(raw_text)
    if not spoken_text:
        return False

    detected_lang = detect_language(spoken_text)
    
    # Determine appropriate Edge TTS fallback voice
    if not voice or voice in ("auto", "default"):
        if detected_lang == "af-ZA":
            target_edge_voice = "af-ZA-WillemNeural"
        else:
            target_edge_voice = "en-ZA-LukeNeural"
    elif voice in ("fish", "cloned", "jakes", "fishaudio"):
        target_edge_voice = "af-ZA-WillemNeural" if detected_lang == "af-ZA" else "en-ZA-LukeNeural"
    elif voice in ("willem", "af-ZA-WillemNeural"):
        target_edge_voice = "af-ZA-WillemNeural"
    else:
        # If user explicitly requested an English voice but the text is Afrikaans, force Willem
        # because English neural voices cannot pronounce Afrikaans words.
        if detected_lang == "af-ZA" and not voice.startswith("af-"):
            target_edge_voice = "af-ZA-WillemNeural"
        else:
            target_edge_voice = voice

    temp_mp3 = output_ogg + ".temp.mp3"
    try:
        success = False
        
        # Fish Audio with Jakes' cloned voice is used for:
        # 1. English text on 'auto' / 'default' mode
        # 2. When explicitly requested via voice in ('fish', 'cloned', 'jakes')
        # Afrikaans is NEVER sent to Fish Audio (voice model is English-only)
        fish_key = os.environ.get("FISH_AUDIO_API_KEY") or DEFAULT_FISH_AUDIO_API_KEY
        fish_ref = os.environ.get("FISH_AUDIO_REFERENCE_ID") or DEFAULT_FISH_AUDIO_REFERENCE_ID
        wants_fish = (
            fish_key and fish_ref and detected_lang != "af-ZA" and (
                voice in ("fish", "cloned", "jakes", "fishaudio", "auto", "default", None)
            )
        )
        if wants_fish:
            try:
                success = raw_fish_audio_tts(spoken_text, temp_mp3, api_key=fish_key, reference_id=fish_ref)
                if success:
                    print(f"[TTS] Synthesized via Fish Audio S2.1 Pro (Jakes cloned voice: {fish_ref})", file=sys.stderr)
            except Exception as ex:
                print(f"[TTS Warning] Fish Audio error: {ex}. Falling back to Edge TTS...", file=sys.stderr)
                success = False

        # If Fish Audio wasn't requested (Afrikaans) or failed, use Edge TTS
        # (Willem for Afrikaans, Luke for English fallback)
        if not success:
            success = raw_edge_tts(spoken_text, temp_mp3, voice=target_edge_voice)
            if success:
                print(f"[TTS] Synthesized via Edge TTS (voice: {target_edge_voice}, lang: {detected_lang})", file=sys.stderr)

        if not success or not os.path.exists(temp_mp3) or os.path.getsize(temp_mp3) == 0:
            return False

        # Convert to OGG OPUS using ffmpeg with 32kbps mono (optimal for Telegram & WhatsApp voice notes)
        cmd = [
            "ffmpeg", "-y", "-nostdin", "-threads", "2", "-i", temp_mp3,
            "-c:a", "libopus", "-b:a", "32k", "-vbr", "on",
            output_ogg
        ]
        try:
            res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            return res.returncode == 0 and os.path.exists(output_ogg) and os.path.getsize(output_ogg) > 0
        except subprocess.TimeoutExpired:
            print("[TTS Error] ffmpeg Opus conversion timed out", file=sys.stderr)
            return False
    finally:
        if os.path.exists(temp_mp3):
            try:
                os.remove(temp_mp3)
            except OSError:
                pass

def transcribe_with_groq_whisper(audio_input_path, lang=None, api_key=None, model=None):
    """
    High-fidelity transcription using Groq's Whisper Large V3 Turbo.
    Sub-second latency with exceptional accuracy across English and Afrikaans.
    """
    import requests

    key = api_key or os.environ.get("GROQ_API_KEY") or DEFAULT_GROQ_API_KEY
    if not key or not os.path.exists(audio_input_path) or os.path.getsize(audio_input_path) == 0:
        return None

    target_model = model or os.environ.get("GROQ_WHISPER_MODEL") or DEFAULT_GROQ_WHISPER_MODEL

    ext = os.path.splitext(audio_input_path)[1].lower()
    content_type_map = {
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".mpga": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/m4a",
        ".webm": "audio/webm",
        ".mp4": "audio/mp4"
    }
    content_type = content_type_map.get(ext, "audio/ogg")
    upload_filename = "audio" + (ext if ext in content_type_map else ".ogg")

    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {key}"
    }

    data = {
        "model": target_model,
        "temperature": "0"
    }

    # ISO 639-1 language code formatting if specified (e.g. 'af' or 'en')
    # If lang is 'auto', 'en-US', None, etc., let Whisper auto-detect
    if lang and lang.lower() not in ("auto", "default", "none", "en-us"):
        clean_lang = lang.split("-")[0].lower()
        if len(clean_lang) == 2:
            data["language"] = clean_lang

    try:
        with open(audio_input_path, "rb") as f:
            files = {
                "file": (upload_filename, f, content_type)
            }
            res = requests.post(url, headers=headers, files=files, data=data, timeout=30)

        if res.status_code == 200:
            result_json = res.json()
            text = (result_json.get("text") or "").strip()
            if text:
                return text
        else:
            print(f"[STT Warning] Groq API returned status {res.status_code}: {res.text}", file=sys.stderr)
    except Exception as e:
        print(f"[STT Warning] Groq Whisper request failed: {e}", file=sys.stderr)

    return None

def transcribe_audio_file(audio_input_path, lang="auto"):
    """
    Transcribe any input audio file (OGG/OGA/MP3/WAV) to text.
    Primary engine: Groq Whisper Large V3 Turbo (sub-second, high-fidelity STT).
    Fallback engine: Google SpeechRecognition.
    """
    if not os.path.exists(audio_input_path) or os.path.getsize(audio_input_path) == 0:
        return ""

    # 1. Primary: Groq Whisper Large V3 Turbo
    groq_text = transcribe_with_groq_whisper(audio_input_path, lang=lang)
    if groq_text:
        return groq_text

    # 2. Fallback: Google SpeechRecognition if Groq fails or rate limits
    print("[STT Fallback] Falling back to Google SpeechRecognition...", file=sys.stderr)
    temp_wav = audio_input_path + ".temp.wav"
    try:
        cmd = [
            "ffmpeg", "-y", "-nostdin", "-threads", "2", "-i", audio_input_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            temp_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        if res.returncode != 0 or not os.path.exists(temp_wav):
            return ""

        try:
            import speech_recognition as sr
        except ImportError:
            return ""

        r = sr.Recognizer()
        with sr.AudioFile(temp_wav) as source:
            audio_data = r.record(source)

        google_lang = "en-US"
        if lang and lang.lower() in ("af", "af-za"):
            google_lang = "af-ZA"
        elif lang and lang.lower() not in ("auto", "default", "none"):
            google_lang = lang

        try:
            text = r.recognize_google(audio_data, language=google_lang)
            return text.strip()
        except Exception as e:
            print(f"STT Service Error: {e}", file=sys.stderr)
            return ""
    finally:
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except OSError:
                pass

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 audio_processor.py synth <text> <output.ogg> [voice]")
        print("  python3 audio_processor.py synth-file <input_txt_file> <output.ogg> [voice]")
        print("  python3 audio_processor.py transcribe <input_audio_file> [lang]")
        sys.exit(1)

    action = sys.argv[1]
    if action == "synth-file":
        if len(sys.argv) < 4:
            print("Usage: python3 audio_processor.py synth-file <input_txt_file> <output.ogg> [voice]")
            sys.exit(1)
        in_txt_path = sys.argv[2]
        out_ogg = sys.argv[3]
        v = sys.argv[4] if len(sys.argv) > 4 else "auto"
        try:
            with open(in_txt_path, "r", encoding="utf-8") as f:
                text_arg = f.read()
        except Exception as e:
            print(f"Failed to read input text file {in_txt_path}: {e}", file=sys.stderr)
            sys.exit(1)
        ok = synthesize_to_ogg_opus(text_arg, out_ogg, voice=v)
        if ok:
            print(f"SUCCESS:{out_ogg}")
            sys.exit(0)
        else:
            print("FAILED", file=sys.stderr)
            sys.exit(1)
    elif action == "synth":
        if len(sys.argv) < 4:
            print("Usage: python3 audio_processor.py synth <text> <output.ogg> [voice]")
            sys.exit(1)
        text_arg = sys.argv[2]
        out_ogg = sys.argv[3]
        v = sys.argv[4] if len(sys.argv) > 4 else "auto"
        ok = synthesize_to_ogg_opus(text_arg, out_ogg, voice=v)
        if ok:
            print(f"SUCCESS:{out_ogg}")
            sys.exit(0)
        else:
            print("FAILED", file=sys.stderr)
            sys.exit(1)
    elif action == "transcribe":
        if len(sys.argv) < 3:
            print("Usage: python3 audio_processor.py transcribe <input_audio_file> [lang]")
            sys.exit(1)
        in_audio = sys.argv[2]
        l = sys.argv[3] if len(sys.argv) > 3 else "auto"
        transcription = transcribe_audio_file(in_audio, lang=l)
        print(transcription)
        sys.exit(0 if transcription else 1)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)

