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

_SPAWNED_LS_PROC = None
_SPAWNED_LS_PORT = None
_SPAWNED_LS_TOKEN = None

def find_available_port(start_port=57071, max_attempts=20):
    """Finds an available local port for standalone language_server binding."""
    import socket
    for p in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', p))
                return p
        except Exception:
            continue
    return start_port

def _save_server_cache(port, csrf_token):
    """Persists active port & token to cache for instant sub-millisecond reuse."""
    cache_dirs = [db.DATA_DIR]
    appdata = os.environ.get('APPDATA')
    if appdata:
        cache_dirs.append(os.path.join(appdata, 'AntigravityFlow'))

    for cd in cache_dirs:
        try:
            os.makedirs(cd, exist_ok=True)
            with open(os.path.join(cd, ".server_cache.json"), "w", encoding="utf-8") as f:
                json.dump({"port": port, "csrf_token": csrf_token}, f)
        except Exception:
            pass

def find_language_server_binary():
    """Finds installed language_server.exe binary path on Windows."""
    candidates = [
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Antigravity', 'resources', 'bin', 'language_server.exe'),
        os.path.join(os.environ.get('USERPROFILE', ''), '.gemini', 'bin', 'language_server.exe'),
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Antigravity', 'resources', 'bin', 'language_server.exe'),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None

def terminate_spawned_language_server():
    """Cleanly terminates the language_server process and its children if launched by AntigravityFlow."""
    global _SPAWNED_LS_PROC
    if _SPAWNED_LS_PROC:
        try:
            pid = _SPAWNED_LS_PROC.pid
            try:
                p = psutil.Process(pid)
                for child in p.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                p.kill()
            except Exception:
                pass
            _SPAWNED_LS_PROC.terminate()
            _SPAWNED_LS_PROC.wait(timeout=1.0)
        except Exception:
            try:
                _SPAWNED_LS_PROC.kill()
            except Exception:
                pass
        _SPAWNED_LS_PROC = None

def start_language_server_process():
    """Attempts to auto-launch language_server.exe with explicit port and token if not running."""
    global _SPAWNED_LS_PROC, _SPAWNED_LS_PORT, _SPAWNED_LS_TOKEN
    ls_bin = find_language_server_binary()
    if not ls_bin:
        print("[LLM Refiner] Notice: language_server.exe binary not found in standard paths.")
        return None
    try:
        import uuid
        csrf_token = str(uuid.uuid4())
        chosen_port = find_available_port(start_port=57071)
        cmd = [
            ls_bin,
            "--standalone",
            "--override_ide_name", "antigravity",
            "--subclient_type", "hub",
            "--override_ide_version", "2.19.1",
            "--override_user_agent_name", "antigravity",
            "--https_server_port", str(chosen_port),
            "--csrf_token", csrf_token,
            "--app_data_dir", "antigravity",
            "--api_server_url", "https://generativelanguage.googleapis.com",
            "--cloud_code_endpoint", "https://daily-cloudcode-pa.googleapis.com",
            "--enable_sidecars"
        ]
        flags = 0
        if os.name == 'nt':
            flags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        _SPAWNED_LS_PROC = proc
        _SPAWNED_LS_PORT = chosen_port
        _SPAWNED_LS_TOKEN = csrf_token
        print(f"[LLM Refiner] Auto-started language_server.exe (PID: {proc.pid}) on port {chosen_port}")
        return proc
    except Exception as e:
        print(f"[LLM Refiner] Notice: Could not auto-start language_server: {e}")
        return None

def discover_server(raise_on_error=True, auto_start=True):
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

    # 2. Check if language_server is already running anywhere on the system
    server_proc = None
    for p in psutil.process_iter(['name', 'cmdline', 'pid']):
        try:
            name = (p.info.get('name') or '').lower()
            if 'language_server' in name:
                server_proc = p
                break
        except Exception:
            continue

    if server_proc:
        cmd_list = server_proc.info.get('cmdline') or []
        cmd = ' '.join(cmd_list)
        token_match = re.search(r'--csrf_token\s+([a-f0-9-]+)', cmd)
        csrf_token = token_match.group(1) if token_match else None

        ports = []
        # Check explicit --https_server_port if present and != 0
        p_match = re.search(r'--https_server_port\s+(\d+)', cmd)
        if p_match:
            try:
                p_val = int(p_match.group(1))
                if p_val != 0:
                    ports.append(p_val)
            except Exception:
                pass

        # Check host bridge hint: --host_bridge_url=http://127.0.0.1:(\d+) -> https is bridge + 1
        hb_match = re.search(r'--host_bridge_url=https?://127\.0\.0\.1:(\d+)', cmd)
        if hb_match:
            try:
                hb_port = int(hb_match.group(1))
                ports.extend([hb_port + 1, hb_port])
            except Exception:
                pass

        # Check netstat -ano for server_proc.pid (100% reliable on Windows)
        try:
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

        # Fallback candidate ports
        for candidate in [57071, 57072, 57073, 57074, 57075, 61906, 61907]:
            if candidate not in ports:
                ports.append(candidate)

        if csrf_token:
            for port in ports:
                try:
                    req = urllib.request.Request(
                        f"https://127.0.0.1:{port}/",
                        headers={"x-codeium-csrf-token": csrf_token}
                    )
                    with urllib.request.urlopen(req, context=ctx, timeout=0.35) as res:
                        if res.status == 200:
                            _save_server_cache(port, csrf_token)
                            return port, csrf_token
                except Exception:
                    continue

    # 3. Not running or unresponsive: Auto-launch standalone process if requested
    if auto_start:
        proc = start_language_server_process()
        if proc and _SPAWNED_LS_PORT and _SPAWNED_LS_TOKEN:
            port = _SPAWNED_LS_PORT
            csrf_token = _SPAWNED_LS_TOKEN
            # Poll port with backoff for up to 6.0 seconds
            start_poll = time.time()
            while time.time() - start_poll < 6.0:
                time.sleep(0.25)
                try:
                    req = urllib.request.Request(
                        f"https://127.0.0.1:{port}/",
                        headers={"x-codeium-csrf-token": csrf_token}
                    )
                    with urllib.request.urlopen(req, context=ctx, timeout=0.35) as res:
                        if res.status == 200:
                            _save_server_cache(port, csrf_token)
                            print(f"[LLM Refiner] Connected to standalone language_server on port {port} in {time.time() - start_poll:.2f}s")
                            return port, csrf_token
                except Exception:
                    continue

    if raise_on_error:
        raise RuntimeError("Antigravity language_server.exe is not running or responsive.")
    return None, None

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
