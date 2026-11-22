import http.client
import io
import json
import os
import re
import ssl
import time
import urllib.request
import uuid
import psutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

DEFAULT_SYSTEM_PROMPT = (
    "You are an expert voice dictation assistant. "
    "Clean up grammatical slips, remove stuttering, filler words (um, uh, repeats), and fix punctuation. "
    "Preserve the exact meaning, technical terms, and speaker tone. "
    "Return ONLY the final polished text with no surrounding quotes, markdown fences, preamble, or commentary."
)

def discover_server():
    server_proc = None
    for p in psutil.process_iter(['name', 'cmdline']):
        if p.info['name'] == 'language_server.exe':
            server_proc = p
            break

    if not server_proc:
        raise RuntimeError("Antigravity language_server.exe is not running.")

    cmd = ' '.join(server_proc.info['cmdline'] or [])
    token_match = re.search(r'--csrf_token\s+([a-f0-9-]+)', cmd)
    if not token_match:
        raise RuntimeError("Could not find CSRF token.")
    csrf_token = token_match.group(1)

    ports = []
    try:
        conns = [c for c in psutil.net_connections(kind='tcp') if c.pid == server_proc.pid and c.status == 'LISTEN']
        ports = [c.laddr.port for c in conns if c.laddr.ip in ('127.0.0.1', '::1', '0.0.0.0')]
    except Exception:
        pass

    if not ports:
        ports = [57071, 57072]

    active_port = None
    for port in ports:
        try:
            req = urllib.request.Request(
                f"https://127.0.0.1:{port}/",
                headers={"x-codeium-csrf-token": csrf_token}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=0.3) as res:
                if res.status == 200:
                    active_port = port
                    break
        except Exception:
            continue

    if not active_port:
        raise RuntimeError(f"Could not connect via HTTPS to any port: {ports}")

    return active_port, csrf_token

def make_grpc_web_frame(payload_bytes: bytes, flag: int = 0) -> bytes:
    return bytes([flag]) + len(payload_bytes).to_bytes(4, byteorder="big") + payload_bytes

def parse_grpc_web_stream(stream):
    while True:
        header = stream.read(5)
        if not header or len(header) < 5:
            break
        flag = header[0]
        length = int.from_bytes(header[1:5], byteorder="big")
        payload = stream.read(length)
        if flag == 0:
            try:
                yield json.loads(payload.decode("utf-8"))
            except Exception:
                yield payload
        elif flag == 0x80:
            yield f"TRAILER: {payload.decode('utf-8', errors='replace')}"

def polish_with_gemini(raw_text: str, model: str = "MODEL_PLACEHOLDER_M318", system_prompt: str = "") -> str:
    """Passes raw dictation text through local Antigravity Gemini Flash to clean grammar,
    remove disfluencies, and format output according to system instructions.
    
    If server is unreachable or times out, immediately falls back to raw_text (zero data loss).
    """
    if not raw_text or not raw_text.strip():
        return raw_text

    clean_input = raw_text.strip()
    # Don't waste an LLM call on tiny 1-2 word utterances like 'yes' or 'stop'
    if len(clean_input.split()) < 3:
        return clean_input

    try:
        port, csrf_token = discover_server()
    except Exception as e:
        print(f"[LLM Refiner] Discovery error: {e}")
        return clean_input

    headers = {
        "content-type": "application/grpc-web+json",
        "x-codeium-csrf-token": csrf_token,
        "x-grpc-web": "1"
    }

    instruction = system_prompt.strip() if system_prompt and system_prompt.strip() else DEFAULT_SYSTEM_PROMPT
    full_prompt = f"{instruction}\n\nRaw text to polish:\n{clean_input}"

    # Priority 1: Direct single-shot GetModelResponse (fast, fallback immediately if unconfigured)
    try:
        conn = http.client.HTTPSConnection('127.0.0.1', port, context=ctx, timeout=1.5)
        body = {"prompt": full_prompt, "model": model}
        frame = make_grpc_web_frame(json.dumps(body).encode("utf-8"))
        conn.request("POST", "/exa.language_server_pb.LanguageServerService/GetModelResponse", body=frame, headers=headers)
        res = conn.getresponse()
        hdr = res.read(5)
        if hdr and len(hdr) >= 5:
            length = int.from_bytes(hdr[1:5], "big")
            data = json.loads(res.read(length).decode("utf-8"))
            conn.close()
            resp_text = data.get("response", "").strip()
            if resp_text:
                return resp_text.strip('"').strip("'")
    except Exception as e:
        # Expected if language_server.exe requires Cascade session context for LLM completion
        print(f"[LLM Refiner] Local LLM pass bypassed ({e}); using raw transcript.")

    # Fallback to raw text
    return clean_input
