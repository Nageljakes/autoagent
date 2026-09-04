#!/usr/bin/env python3
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

TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
WIN_EPOCH = 11644473600
CHROMIUM_FULL_VERSION = "143.0.3650.75"
SEC_MS_GEC_VERSION = f"1-{CHROMIUM_FULL_VERSION}"

def clean_text_for_speech(text):
    """Clean markdown, code artifacts, file links, and synthesis boilerplate for natural speech."""
    if not text:
        return ""
    
    # Check if there is an explicit transcript section in the text (e.g. ### Spoken Audio Transcript)
    transcript_match = re.search(r"(?:Spoken Audio Transcript|Transcript|Spoken Voice Note)[\s\S]*?(?=\n\n###|\n\n---|(?:\n\n\s*The audio files)|$)", text, re.IGNORECASE)
    if transcript_match:
        content = transcript_match.group(0)
        # Remove the header
        content = re.sub(r"^(?:#+\s*)?(?:📝\s*)?(?:Spoken Audio Transcript|Transcript|Spoken Voice Note)[:\s]*", "", content, flags=re.IGNORECASE)
        # Remove blockquote markers and timestamps like > [0:00] or > **[0:00]**
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
    # Replace code blocks
    text = re.sub(r"```[a-zA-Z]*\n[\s\S]*?\n```", " [Code block omitted in audio; see text message] ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Replace links [text](url) with just text
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
    # Clean whitespace
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    
    # 6. Trim overly long text for voice synthesis if necessary (keep within ~1500 chars)
    if len(text) > 1500:
        truncated = text[:1500]
        last_period = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
        if last_period > 800:
            text = truncated[:last_period + 1] + " I've included the rest of the details in the text message."
        else:
            text = truncated + "... I've sent the complete details in the text message."
    return text

def raw_edge_tts(text, output_mp3, voice="en-US-ChristopherNeural"):
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
    ssml = f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'><voice name='{voice}'>{clean_text}</voice></speak>"
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

def synthesize_to_ogg_opus(raw_text, output_ogg, voice="en-US-ChristopherNeural"):
    """Synthesize text and convert to native Telegram voice note format (.ogg with opus)."""
    spoken_text = clean_text_for_speech(raw_text)
    if not spoken_text:
        return False

    temp_mp3 = output_ogg + ".temp.mp3"
    try:
        success = raw_edge_tts(spoken_text, temp_mp3, voice=voice)
        if not success or not os.path.exists(temp_mp3) or os.path.getsize(temp_mp3) == 0:
            return False

        # Convert to OGG OPUS using ffmpeg with 32kbps mono (optimal for Telegram voice notes)
        cmd = [
            "ffmpeg", "-y", "-i", temp_mp3,
            "-c:a", "libopus", "-b:a", "32k", "-vbr", "on",
            output_ogg
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0 and os.path.exists(output_ogg) and os.path.getsize(output_ogg) > 0
    finally:
        if os.path.exists(temp_mp3):
            try:
                os.remove(temp_mp3)
            except OSError:
                pass

def transcribe_audio_file(audio_input_path, lang="en-US"):
    """Transcribe any input audio file (OGG/OGA/MP3/WAV) to text using SpeechRecognition."""
    import speech_recognition as sr

    temp_wav = audio_input_path + ".temp.wav"
    try:
        # Convert audio to 16kHz mono WAV format for recognizer
        cmd = [
            "ffmpeg", "-y", "-i", audio_input_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            temp_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0 or not os.path.exists(temp_wav):
            return ""

        r = sr.Recognizer()
        with sr.AudioFile(temp_wav) as source:
            audio_data = r.record(source)

        try:
            text = r.recognize_google(audio_data, language=lang)
            return text.strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
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
        print("  python3 audio_processor.py transcribe <input_audio_file> [lang]")
        sys.exit(1)

    action = sys.argv[1]
    if action == "synth":
        if len(sys.argv) < 4:
            print("Usage: python3 audio_processor.py synth <text> <output.ogg> [voice]")
            sys.exit(1)
        text_arg = sys.argv[2]
        out_ogg = sys.argv[3]
        v = sys.argv[4] if len(sys.argv) > 4 else "en-US-ChristopherNeural"
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
        l = sys.argv[3] if len(sys.argv) > 3 else "en-US"
        transcription = transcribe_audio_file(in_audio, lang=l)
        print(transcription)
        sys.exit(0 if transcription else 1)
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
