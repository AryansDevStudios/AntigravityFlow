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

import db

def discover_server():
    # 1. Ultra-fast Cache Check: Validate cached port and token (< 15ms)
    cache_path = os.path.join(db.DATA_DIR, ".server_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                c = json.load(f)
            cached_port = c.get("port")
            cached_token = c.get("csrf_token")
            if cached_port and cached_token:
                req = urllib.request.Request(
                    f"https://127.0.0.1:{cached_port}/",
                    headers={"x-codeium-csrf-token": cached_token}
                )
                with urllib.request.urlopen(req, context=ctx, timeout=0.25) as res:
                    if res.status == 200:
                        return cached_port, cached_token
        except Exception:
            pass

    server_proc = None
    for p in psutil.process_iter(['name', 'cmdline', 'pid']):
        try:
            name = (p.info.get('name') or '').lower()
            if 'language_server' in name:
                server_proc = p
                break
        except Exception:
            continue

    if not server_proc:
        raise RuntimeError("Antigravity language_server.exe is not running.")

    cmd = ' '.join(server_proc.info.get('cmdline') or [])
    token_match = re.search(r'--csrf_token\s+([a-f0-9-]+)', cmd)
    if not token_match:
        raise RuntimeError("Could not find CSRF token.")
    csrf_token = token_match.group(1)

    ports = []
    # 1. Check host bridge hint: --host_bridge_url=http://127.0.0.1:(\d+) -> https is usually bridge + 1
    hb_match = re.search(r'--host_bridge_url=https?://127\.0\.0\.1:(\d+)', cmd)
    if hb_match:
        try:
            hb_port = int(hb_match.group(1))
            ports.extend([hb_port + 1, hb_port])
        except Exception:
            pass

    # 2. Try netstat -ano for server_proc.pid (100% reliable on Windows, zero admin elevation required)
    try:
        import subprocess
        out = subprocess.check_output('netstat -ano', shell=True, text=True, timeout=2.0)
        for line in out.splitlines():
            if str(server_proc.pid) in line and 'LISTENING' in line:
                m = re.search(r'(?:127\.0\.0\.1|0\.0\.0\.0):(\d+)', line)
                if m:
                    p_val = int(m.group(1))
                    if p_val not in ports:
                        ports.append(p_val)
    except Exception:
        pass

    # 3. Fallback: psutil net connections if available
    if not ports:
        try:
            conns = [c for c in psutil.net_connections(kind='tcp') if c.pid == server_proc.pid and c.status == 'LISTEN']
            for c in conns:
                if c.laddr.ip in ('127.0.0.1', '::1', '0.0.0.0') and c.laddr.port not in ports:
                    ports.append(c.laddr.port)
        except Exception:
            pass

    if not ports:
        ports = [61906, 61907, 57071, 57072]

    active_port = None
    for port in ports:
        try:
            req = urllib.request.Request(
                f"https://127.0.0.1:{port}/",
                headers={"x-codeium-csrf-token": csrf_token}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=0.35) as res:
                if res.status == 200:
                    active_port = port
                    break
        except Exception:
            continue

    if not active_port:
        raise RuntimeError(f"Could not connect via HTTPS to any port: {ports}")

    # Persist in cache for instant sub-millisecond reuse
    cache_dirs = [db.DATA_DIR]
    appdata = os.environ.get('APPDATA')
    if appdata:
        cache_dirs.append(os.path.join(appdata, 'AntigravityFlow'))

    for cd in cache_dirs:
        try:
            os.makedirs(cd, exist_ok=True)
            with open(os.path.join(cd, ".server_cache.json"), "w", encoding="utf-8") as f:
                json.dump({"port": active_port, "csrf_token": csrf_token}, f)
        except Exception:
            pass

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

from prompts import get_preset_prompt, build_context_envelope, PROMPT_AUTHENTIC_FLOW
import db

PRIMARY_MODEL = "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE"
FALLBACK_MODEL = "MODEL_PLACEHOLDER_M50"

def polish_with_gemini(
    raw_text: str,
    model: str = PRIMARY_MODEL,
    preset_mode: str = "flow",
    system_prompt: str = "",
    app_name: str = "",
    window_title: str = "",
    system_ctx: dict = None,
    config: dict = None
) -> str:
    """Passes raw dictation text through local Antigravity Gemini Flash Lite to clean grammar,
    execute commands, or apply contextual user formatting according to active mode and context settings.
    
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

    target_model = model if model else PRIMARY_MODEL
    
    # 1. Resolve instruction: explicit system_prompt > mode preset prompt
    if system_prompt and system_prompt.strip():
        instruction = system_prompt.strip()
    else:
        instruction = get_preset_prompt(preset_mode)

    # 2. Fetch past conversation/dictations context (~1,000 - 2,000 words memory)
    recent_history = []
    cfg = config or {}
    hist_limit = int(cfg.get("ctx_history_limit", 5))
    try:
        if cfg.get("ctx_include_history", True) and cfg.get("enable_deep_context", True):
            recent_history = db.get_history(limit=hist_limit)
    except Exception:
        pass

    # 3. Build rich context envelope
    full_prompt = build_context_envelope(
        clean_input=clean_input,
        system_instruction=instruction,
        app_name=app_name,
        window_title=window_title,
        recent_history=recent_history,
        system_ctx=system_ctx,
        config=cfg
    )

    # Fast direct single-shot GetModelResponse (~720ms - 1000ms on Flash Lite)
    try:
        conn = http.client.HTTPSConnection('127.0.0.1', port, context=ctx, timeout=4.0)
        body = {"prompt": full_prompt, "model": target_model}
        frame = make_grpc_web_frame(json.dumps(body).encode("utf-8"))
        conn.request("POST", "/exa.language_server_pb.LanguageServerService/GetModelResponse", body=frame, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if len(raw) >= 5:
            length = int.from_bytes(raw[1:5], "big")
            data = json.loads(raw[5:5+length].decode("utf-8"))
            resp_text = data.get("response", "").strip()
            if resp_text:
                return resp_text.strip('"').strip("'")
    except Exception as e:
        print(f"[LLM Refiner] GetModelResponse ({target_model}) failed: {e}; using raw transcript.")

    # Fallback to raw text
    return clean_input
