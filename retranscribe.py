import json
import os
import ssl
import sys
import psutil
import re

import crypto_audio
import db
import batch_transcriber
import context_peeker
import llm_refiner

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

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

    import urllib.request
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

def retranscribe(item_id: int):
    """Zero-Loss 1-Click Dashboard Retry (🔄 button) & Background Recovery.
    Uses unified single-shot batch transcription on decrypted audio buffer.
    """
    rec_path = os.path.join(db.RECORDINGS_DIR, f"{item_id}.enc")
    if not os.path.exists(rec_path):
        raise FileNotFoundError(f"Recording file not found for ID: {item_id}")

    pcm_data = crypto_audio.load_decrypted_pcm(rec_path)
    if not pcm_data:
        raise ValueError("Decrypted audio is empty")

    port, csrf_token = discover_server()
    cfg = db.load_config()

    # Determine context cascade if enabled
    cascade_id = ""
    if cfg.get("enable_context_cascade", True):
        cascade_id = context_peeker.get_active_cascade_id(port, csrf_token)

    # Determine app category from past record
    category = "Other Tasks"
    try:
        history = db.get_history()
        for h in history:
            if h.get("id") == item_id:
                category = h.get("category", "Other Tasks")
                break
    except Exception:
        pass

    # Batch transcription
    final_text = batch_transcriber.transcribe_audio_batch(
        pcm_data=pcm_data,
        port=port,
        csrf_token=csrf_token,
        cascade_id=cascade_id,
        category=category,
        timeout=8.0
    )

    if not final_text or not final_text.strip():
        raise RuntimeError("No transcription received from batch audio decoding.")

    # AI Polish pass if enabled
    if cfg.get("enable_ai_polish", False):
        try:
            polished = llm_refiner.polish_with_gemini(
                final_text,
                model=cfg.get("ai_model", "MODEL_PLACEHOLDER_M318"),
                system_prompt=cfg.get("ai_system_prompt", "")
            )
            if polished and polished.strip():
                final_text = polished.strip()
        except Exception as err:
            print(f"[Retranscribe] AI Polish fallback: {err}")

    # Update database
    updated = db.update_dictation_text(item_id, final_text)
    return updated

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "Missing dictation ID argument"}))
        sys.exit(1)

    try:
        dict_id = int(sys.argv[1])
        updated_item = retranscribe(dict_id)
        print(json.dumps({"success": True, "data": updated_item}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
