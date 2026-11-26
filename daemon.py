import base64
import ctypes
from ctypes import wintypes
import winreg
import http.client
import json
import sys
import os

# Windows console encoding safeguard & persistent fallback logger
_appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
_log_dir = os.path.join(_appdata, "AntigravityFlow")
os.makedirs(_log_dir, exist_ok=True)
DAEMON_OUT_LOG = os.path.join(_log_dir, "daemon_stdout.log")
CRASH_LOG = os.path.join(_log_dir, "daemon_crash.log")

def _has_valid_win_fd(fd_num: int) -> bool:
    if os.name != 'nt':
        return True
    try:
        import msvcrt
        h = msvcrt.get_osfhandle(fd_num)
        return h not in (0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF)
    except Exception:
        return False

if sys.stderr is None:
    if _has_valid_win_fd(2):
        try:
            sys.stderr = open(2, 'w', encoding='utf-8', buffering=1, errors='replace')
        except Exception:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = open(DAEMON_OUT_LOG, 'a', encoding='utf-8', buffering=1, errors='replace')
        except Exception:
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')
elif hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass

if sys.stdout is None:
    if _has_valid_win_fd(1):
        try:
            sys.stdout = open(1, 'w', encoding='utf-8', errors='replace')
        except Exception:
            pass
    if sys.stdout is None:
        try:
            sys.stdout = open(DAEMON_OUT_LOG, 'a', encoding='utf-8', buffering=1, errors='replace')
        except Exception:
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')
elif hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_CLI_SUBCOMMANDS = {
    "quota", "retranscribe", "vocab-load", "vocab-get",
    "history-get", "history-delete", "history-clear",
    "macro-get", "macros-get", "macro-save", "macros-save",
    "macro-delete", "macros-delete", "macro-expand", "macros-expand",
    "query", "models", "search", "exec", "screenshot", "paste", "refine",
    "tts-synth", "tts-demo", "tts-voices"
}
_IS_CLI_MODE = len(sys.argv) > 1 and sys.argv[1].lower() in _CLI_SUBCOMMANDS
_CLI_JSON_STDOUT = sys.stdout
if _IS_CLI_MODE:
    # Keep stdout purely for structured JSON output; route all diagnostic print() calls to stderr
    sys._CLI_JSON_STDOUT = _CLI_JSON_STDOUT
    sys.stdout = sys.stderr

import queue
import re
import ssl
import subprocess
import threading
import time
import urllib.request
import winsound
import keyboard
import psutil
import sounddevice as sd
import numpy as np
import pyperclip
from PIL import Image, ImageDraw
import pystray

import db
import crypto_audio
from pill import FloatingPill
from dsp_pipeline import AudioDSP
from context_peeker import get_precursor_context, get_active_cascade_id
from formatter import format_transcript
from accumulator import TranscriptAccumulator
import batch_transcriber
import llm_refiner
import system_context
import prompts
import traceback

def log_uncaught_exception(exctype, value, tb):
    err_msg = "".join(traceback.format_exception(exctype, value, tb))
    try:
        if sys.stderr:
            sys.stderr.write(f"\n[FATAL EXCEPTION] {err_msg}\n")
            sys.stderr.flush()
    except Exception:
        pass
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.ctime()} ---\n{err_msg}\n")
    except Exception:
        pass

sys.excepthook = log_uncaught_exception
if hasattr(threading, 'excepthook'):
    threading.excepthook = lambda args: log_uncaught_exception(args.exc_type, args.exc_value, args.exc_traceback)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
try:
    os.chdir(BASE_DIR)
except Exception:
    pass

UI_DIR = os.path.join(BASE_DIR, "ui")
SOUNDS_DIR = os.path.join(BASE_DIR, "sounds")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------------------
# 1. Fast In-Memory Discovery via psutil (< 25ms)
# ---------------------------------------------------------------------------
def discover_antigravity_server(raise_on_error=False, auto_start=True):
    return llm_refiner.discover_server(raise_on_error=raise_on_error, auto_start=auto_start)


# ---------------------------------------------------------------------------
# 2. Helper Utilities: Windows Paste, App Detection, Sound
# ---------------------------------------------------------------------------
def make_grpc_web_frame(payload_bytes: bytes, flag: int = 0) -> bytes:
    return bytes([flag]) + len(payload_bytes).to_bytes(4, byteorder="big") + payload_bytes

def parse_grpc_web_stream(stream):
    while True:
        try:
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
        except Exception:
            break

KEY_RECORDING_LOCK = db.KEY_RECORDING_LOCK

def set_windows_autostart(enable: bool):
    """Configures application to start silently on Windows boot/login via Run registry key."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    val_name = "AntigravityFlow"
    approved_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"

    # Always clean up legacy com.antigravityflow.app value to prevent duplicates
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, "com.antigravityflow.app")
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except Exception:
        pass

    # Resolve primary executable: prioritize installed AntigravityFlow.exe if present
    appdata_local = os.environ.get('LOCALAPPDATA', '')
    installed_app_exe = os.path.join(appdata_local, 'Programs', 'AntigravityFlow', 'AntigravityFlow.exe')

    if os.path.exists(installed_app_exe):
        cmd = f'"{installed_app_exe}" --hidden'
    elif getattr(sys, 'frozen', False):
        cmd = f'"{sys.executable}"'
    else:
        python_exe = sys.executable
        pythonw_exe = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        exec_bin = pythonw_exe if os.path.exists(pythonw_exe) else python_exe
        script_path = os.path.abspath(__file__)
        cmd = f'"{exec_bin}" "{script_path}"'

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, val_name, 0, winreg.REG_SZ, cmd)
            # Remove any disabled flag in StartupApproved so Task Manager shows Enabled
            try:
                appr_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_path, 0, winreg.KEY_SET_VALUE)
                try:
                    winreg.DeleteValue(appr_key, val_name)
                except FileNotFoundError:
                    pass
                try:
                    winreg.DeleteValue(appr_key, "com.antigravityflow.app")
                except FileNotFoundError:
                    pass
                winreg.CloseKey(appr_key)
            except Exception:
                pass
        else:
            try:
                winreg.DeleteValue(key, val_name)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[Autostart] Error updating registry: {e}")
        return False

def play_sound(cue_type, enabled):
    if not enabled:
        return
    try:
        if cue_type == "start":
            wav_file = os.path.join(SOUNDS_DIR, "start.wav")
            if os.path.exists(wav_file):
                winsound.PlaySound(wav_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        elif cue_type == "switch":
            wav_file = os.path.join(SOUNDS_DIR, "switch.wav")
            if os.path.exists(wav_file):
                winsound.PlaySound(wav_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        else: # "stop" or "done"
            wav_file = os.path.join(SOUNDS_DIR, "stop.wav")
            if os.path.exists(wav_file):
                winsound.PlaySound(wav_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass

def get_foreground_window_info():
    app_name = "Desktop"
    window_title = ""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                window_title = buff.value
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                app_name = psutil.Process(pid.value).name()
    except Exception:
        pass
    return app_name, window_title

def get_foreground_app():
    return get_foreground_window_info()[0]

def paste_all_at_once(text: str):
    """Puts final formatted text on Windows clipboard with Win+V exclusion flags and sends Ctrl+V."""
    system_context.paste_cleanly(text)

def notify_assistant_bridge(action: str, text: str = "", state: str = "idle"):
    """Notifies Electron Desktop Assistant window via local HTTP bridge."""
    try:
        data = json.dumps({"action": action, "text": text, "state": state}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:48199", data=data, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=0.35) as res:
            pass
    except Exception:
        pass

def is_conversational_query(text: str) -> bool:
    """Detects whether spoken input is a conversational inquiry vs an active write/paste directive."""
    t = text.lower().strip()
    write_prefixes = ("write ", "type ", "insert ", "draft ", "paste ", "generate code ", "create a function ")
    if any(t.startswith(p) for p in write_prefixes):
        return False
    question_starters = ("what ", "what's ", "why ", "how ", "who ", "when ", "where ", "can you explain", "explain ", "tell me ", "is there ", "should i ", "which ", "describe ")
    if any(t.startswith(s) for s in question_starters) or t.endswith("?"):
        return True
    return False

def start_daemon_control_server(daemon_instance, port=48198):
    """Starts a multi-threaded local HTTP server for Electron to trigger or toggle dictation,
    or accelerate assistant queries in-process with zero cold start."""
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    class DaemonControlHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            if self.path == "/assistant/macros":
                macros = db.get_macros()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "data": macros}, ensure_ascii=False).encode('utf-8'))
                return
            elif self.path == "/assistant/history":
                items = db.get_history(limit=500)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "data": items}, ensure_ascii=False).encode('utf-8'))
                return
            elif self.path == "/assistant/models":
                import assistant_service
                res = assistant_service.get_available_models_catalog()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                return
            elif self.path == "/tts/voices":
                import edge_tts_service
                voices = edge_tts_service.get_voices_catalog()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "voices": voices}, ensure_ascii=False).encode('utf-8'))
                return
            elif self.path in ("/status", "/health"):
                status_payload = {
                    "status": "ok",
                    "app": "AntigravityFlow",
                    "ls_connected": bool(daemon_instance.port and daemon_instance.csrf_token),
                    "ls_port": daemon_instance.port,
                    "recording": bool(daemon_instance.is_recording)
                }
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(status_payload).encode('utf-8'))
                return
            elif self.path == "/shutdown":
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "shutting_down"}')
                threading.Thread(target=daemon_instance.shutdown, daemon=True).start()
                return
            elif self.path == "/assistant/quota":
                import quota_service
                res = quota_service.get_quota_summary()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                return
            elif self.path == "/assistant/vocabulary":
                import formatter
                res = formatter.load_vocabulary()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "data": res}, ensure_ascii=False).encode('utf-8'))
                return
            elif self.path == "/assistant/screenshot":
                import assistant_service
                res = assistant_service.capture_screen()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                return
            self.send_response(404)
            self.end_headers()
        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length > 0 else b'{}'
            try:
                data = json.loads(body.decode('utf-8') or '{}')
                if self.path in ("/status", "/health"):
                    status_payload = {
                        "status": "ok",
                        "app": "AntigravityFlow",
                        "ls_connected": bool(daemon_instance.port and daemon_instance.csrf_token),
                        "ls_port": daemon_instance.port,
                        "recording": bool(daemon_instance.is_recording)
                    }
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(status_payload).encode('utf-8'))
                    return
                elif self.path == "/reload-config":
                    threading.Thread(target=daemon_instance.reload_config, kwargs={"connect_async": True}, daemon=True).start()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"success": true}')
                    return
                elif self.path == "/shutdown":
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"status": "shutting_down"}')
                    threading.Thread(target=daemon_instance.shutdown, daemon=True).start()
                    return
                elif self.path == "/assistant/history":
                    items = db.get_history(limit=500)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "data": items}, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/history/delete":
                    item_id = data.get("id")
                    ok = db.delete_dictation(int(item_id)) if item_id is not None else False
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": bool(ok)}).encode('utf-8'))
                    return
                elif self.path == "/assistant/history/clear":
                    ok = db.clear_all_history()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": bool(ok)}).encode('utf-8'))
                    return
                elif self.path == "/assistant/query":
                    import assistant_service
                    res = assistant_service.query_gemini_assistant(
                        prompt=data.get("prompt") or data.get("message", ""),
                        history=data.get("history", []),
                        model=data.get("model"),
                        system_instruction=data.get("system_instruction") or data.get("system_prompt"),
                        screenshot_info=data.get("screenshot_info"),
                        enable_web_search=data.get("enable_web_search") or data.get("web_search", False),
                        mode=data.get("mode")
                    )
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/models":
                    import assistant_service
                    res = assistant_service.get_available_models_catalog()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/screenshot":
                    import assistant_service
                    res = assistant_service.capture_screen()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/exec":
                    import assistant_service
                    res = assistant_service.execute_system_command(data.get("command", ""), data.get("shell", "powershell"))
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/macros":
                    macros = db.get_macros()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "data": macros}, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/macros/save":
                    res = db.save_macro(
                        trigger_keyword=data.get("trigger_keyword", data.get("trigger", "")),
                        expansion_text=data.get("expansion_text", data.get("expansion", "")),
                        description=data.get("description", ""),
                        category=data.get("category", "General"),
                        macro_id=data.get("id")
                    )
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/macros/delete":
                    macro_id = data.get("id")
                    res = db.delete_macro(int(macro_id)) if macro_id is not None else False
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": res}, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/tts/synth":
                    import edge_tts_service
                    res = edge_tts_service.synthesize_text(
                        text=data.get("text", ""),
                        voice=data.get("voice", "en-IN-NeerjaNeural"),
                        rate=data.get("rate", "+0%")
                    )
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/tts/demo":
                    import edge_tts_service
                    res = edge_tts_service.get_demo_audio(
                        voice=data.get("voice", "en-IN-NeerjaNeural"),
                        rate=data.get("rate", "+0%")
                    )
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/refine":
                    import assistant_service
                    res = assistant_service.refine_voice_prompt(
                        raw_text=data.get("text", ""),
                        model=data.get("model")
                    )
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/search":
                    import assistant_service
                    res = assistant_service.search_web_unified(data.get("query", ""))
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/paste":
                    import assistant_service
                    res = assistant_service.paste_to_active_window(data.get("text", ""))
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/macros/expand":
                    exp = db.expand_macro_text(data.get("text", ""))
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "expanded": exp}, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/retranscribe":
                    import retranscribe
                    rec_id = int(data.get("id", 0))
                    item = retranscribe.retranscribe(rec_id)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": bool(item), "data": item}, ensure_ascii=False).encode('utf-8'))
                    return
                elif self.path == "/assistant/quota":
                    import quota_service
                    res = quota_service.get_quota_summary()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
                    return

                action = data.get('action', 'toggle')
                session_type = data.get('session_type', 'ai')
                if action == 'start':
                    if not daemon_instance.is_recording:
                        daemon_instance.start_session("toggle", session_type)
                elif action == 'stop':
                    if daemon_instance.is_recording:
                        daemon_instance.stop_session()
                elif action == 'toggle':
                    if daemon_instance.is_recording:
                        daemon_instance.stop_session()
                    else:
                        daemon_instance.start_session("toggle", session_type)
                elif action == 'cancel':
                    daemon_instance.cancel_session()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "recording": daemon_instance.is_recording}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())

    try:
        ThreadingHTTPServer.allow_reuse_address = True
        ThreadingHTTPServer.daemon_threads = True
        server = ThreadingHTTPServer(('127.0.0.1', port), DaemonControlHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[Daemon] Local Control Server listening on 127.0.0.1:{port} (multi-threaded)")
    except Exception as e:
        print(f"[Daemon] Local Control Server error: {e}")

# 3. Dual-Lane Hotkey Manager (Thought Dictation + AI Execution Hotkeys)
# ---------------------------------------------------------------------------
class HotkeyManager:
    def __init__(
        self,
        toggle_hotkey="windows+alt+h",
        ptt_hotkey="windows+alt",
        paste_last_hotkey="alt+v",
        mode="both",
        ai_ptt_hotkey="windows+ctrl",
        ai_toggle_hotkey="windows+ctrl+h",
        ai_hotkey="windows+ctrl+a",
        ai_mode="both",
        enable_ai_hotkey=True,
        on_start=None,
        on_stop=None,
        on_mode_change=None,
        on_paste_last=None,
        on_cancel=None
    ):
        self.mode_setting = mode  # "both", "push_to_talk", "toggle"
        self.dictate_toggle_keys = self._normalize_combo(toggle_hotkey)
        self.dictate_ptt_keys = self._normalize_combo(ptt_hotkey)
        self.paste_last_keys = self._normalize_combo(paste_last_hotkey)
        self.dictate_toggle_raw = toggle_hotkey
        self.dictate_ptt_raw = ptt_hotkey
        self.paste_last_raw = paste_last_hotkey

        # Dedicated AI Assistant Hotkey parameters
        self.ai_ptt_raw = ai_ptt_hotkey
        self.ai_toggle_raw = ai_toggle_hotkey
        self.ai_hotkey_raw = ai_hotkey
        self.ai_ptt_keys = self._normalize_combo(ai_ptt_hotkey)
        self.ai_toggle_keys = self._normalize_combo(ai_toggle_hotkey)
        self.ai_alias_keys = self._normalize_combo(ai_hotkey)
        self.ai_mode = ai_mode  # "both", "hold", "toggle"
        self.enable_ai_hotkey = enable_ai_hotkey

        self.on_start = on_start  # callback(mode, session_type="dictate"|"ai")
        self.on_stop = on_stop    # callback(mode, session_type="dictate"|"ai")
        self.on_mode_change = on_mode_change
        self.on_paste_last = on_paste_last
        self.on_cancel = on_cancel

        self.currently_pressed = set()
        self.current_session_mode = None  # "ptt", "toggle", "ai_ptt", "ai_toggle"
        self.current_session_type = "dictate"  # "dictate" or "ai"
        self.is_recording = False
        self.toggle_active_press = False
        self.ai_toggle_active_press = False
        self.is_paused = False

    def update_hotkeys(
        self,
        toggle_hotkey="windows+alt+h",
        ptt_hotkey="windows+alt",
        mode="both",
        paste_last_hotkey="alt+v",
        ai_ptt_hotkey="windows+ctrl",
        ai_toggle_hotkey="windows+ctrl+h",
        ai_hotkey="windows+ctrl+a",
        ai_mode="both",
        enable_ai_hotkey=True
    ):
        self.mode_setting = mode
        self.dictate_toggle_keys = self._normalize_combo(toggle_hotkey)
        self.dictate_ptt_keys = self._normalize_combo(ptt_hotkey)
        self.paste_last_keys = self._normalize_combo(paste_last_hotkey)
        self.dictate_toggle_raw = toggle_hotkey
        self.dictate_ptt_raw = ptt_hotkey
        self.paste_last_raw = paste_last_hotkey

        self.ai_ptt_raw = ai_ptt_hotkey
        self.ai_toggle_raw = ai_toggle_hotkey
        self.ai_hotkey_raw = ai_hotkey
        self.ai_ptt_keys = self._normalize_combo(ai_ptt_hotkey)
        self.ai_toggle_keys = self._normalize_combo(ai_toggle_hotkey)
        self.ai_alias_keys = self._normalize_combo(ai_hotkey)
        self.ai_mode = ai_mode
        self.enable_ai_hotkey = enable_ai_hotkey

    def _normalize_combo(self, combo_str):
        if not combo_str:
            return set()
        keys = combo_str.lower().replace(" ", "").split("+")
        mapped = set()
        for k in keys:
            k = k.strip()
            if not k:
                continue
            if k in ("ctrl", "control"):
                mapped.add("ctrl")
            elif k in ("alt", "option", "menu"):
                mapped.add("alt")
            elif k in ("shift",):
                mapped.add("shift")
            elif k in ("win", "windows", "cmd", "super", "meta"):
                mapped.add("windows")
            elif k in ("caps", "capslock"):
                mapped.add("caps lock")
            else:
                mapped.add(k)
        return mapped

    def _blocked_keys_for(self, active_keys: set) -> set:
        """Returns modifier/superset keys from other enabled combos that should prevent false triggers."""
        other_combos = []
        if self.mode_setting in ("both", "toggle") and self.dictate_toggle_keys:
            other_combos.append(self.dictate_toggle_keys)
        if self.mode_setting in ("both", "push_to_talk") and self.dictate_ptt_keys:
            other_combos.append(self.dictate_ptt_keys)
        if self.enable_ai_hotkey:
            if self.ai_mode in ("both", "toggle") and self.ai_toggle_keys:
                other_combos.append(self.ai_toggle_keys)
            if self.ai_mode in ("both", "hold") and self.ai_ptt_keys:
                other_combos.append(self.ai_ptt_keys)
            if self.ai_alias_keys:
                other_combos.append(self.ai_alias_keys)
        if self.paste_last_keys:
            other_combos.append(self.paste_last_keys)

        modifiers = {"windows", "alt", "ctrl", "shift"}
        blocked = set()
        for c in other_combos:
            if c == active_keys:
                continue
            blocked.update((c & modifiers) - active_keys)
            if active_keys < c:
                blocked.update(c - active_keys)
        return blocked

    def handle_event(self, event):
        # 0. Temporarily disable dictation trigger while user is recording new keys in settings or actively pasting
        if self.is_paused or os.path.exists(KEY_RECORDING_LOCK) or getattr(system_context, "IS_PASTING", False):
            return

        name = event.name.lower() if event.name else ""
        sc = getattr(event, 'scan_code', None)

        if sc in (91, 92, 57435, 57436) or "windows" in name or "win" in name or "meta" in name:
            name = "windows"
        elif sc in (56, 57400) or "alt" in name or "menu" in name:
            name = "alt"
        elif sc in (29, 57629) or "ctrl" in name or "control" in name:
            name = "ctrl"
        elif sc in (42, 54) or "shift" in name:
            name = "shift"
        elif sc in (58,) or "caps" in name:
            name = "caps lock"

        if event.event_type == "down":
            # Hardware-level modifier synchronization to prevent ghost keys from Windows key-up drops
            try:
                u32 = ctypes.windll.user32
                if not ((u32.GetAsyncKeyState(0x5B) & 0x8000) or (u32.GetAsyncKeyState(0x5C) & 0x8000)):
                    self.currently_pressed.discard("windows")
                if not (u32.GetAsyncKeyState(0x12) & 0x8000):
                    self.currently_pressed.discard("alt")
                if not (u32.GetAsyncKeyState(0x11) & 0x8000):
                    self.currently_pressed.discard("ctrl")
                if not (u32.GetAsyncKeyState(0x10) & 0x8000):
                    self.currently_pressed.discard("shift")
            except Exception:
                pass

            is_new_press = name not in self.currently_pressed
            self.currently_pressed.add(name)

            # Quick-Paste Last Dictation check (e.g. Alt+V) - Absolute Top Priority
            if is_new_press and self.paste_last_keys and self.paste_last_keys.issubset(self.currently_pressed):
                if not any(k in self.currently_pressed for k in self._blocked_keys_for(self.paste_last_keys)):
                    self.currently_pressed.clear()
                    if self.on_paste_last:
                        threading.Thread(target=self.on_paste_last, daemon=True).start()
                    return

            # A. Dynamic Promotion: User was in normal dictation and pressed AI keys -> Switch to AI Execution!
            if self.is_recording and self.current_session_type == "dictate":
                is_ai_hit = False
                if self.enable_ai_hotkey:
                    if self.ai_ptt_keys and self.ai_ptt_keys.issubset(self.currently_pressed):
                        is_ai_hit = True
                    elif self.ai_alias_keys and self.ai_alias_keys.issubset(self.currently_pressed):
                        is_ai_hit = True

                if is_ai_hit:
                    self.current_session_type = "ai"
                    if self.current_session_mode == "ptt":
                        self.current_session_mode = "ai_ptt" if self.ai_mode in ("both", "hold") else "ai_toggle"
                        self.ai_ptt_press_time = time.time()
                    else:
                        self.current_session_mode = "ai_toggle"
                    self.ai_toggle_active_press = True
                    if self.on_mode_change:
                        threading.Thread(target=self.on_mode_change, args=(self.current_session_mode, "ai"), daemon=True).start()
                    return

            # B. Dynamic Switch: From AI PTT to AI Hands-Free Toggle (e.g. tapping 'H' while holding Win+Ctrl)
            if self.is_recording and self.current_session_mode == "ai_ptt":
                if (self.ai_toggle_keys and self.ai_toggle_keys.issubset(self.currently_pressed)) or name == "h":
                    self.current_session_mode = "ai_toggle"
                    self.ai_toggle_active_press = True
                    if self.on_mode_change:
                        threading.Thread(target=self.on_mode_change, args=("ai_toggle", "ai"), daemon=True).start()
                    return

            # C. Dynamic Switch: From Dictate PTT to Dictate Hands-Free Toggle (e.g. tapping 'H' while holding Win+Alt)
            if self.is_recording and self.current_session_mode == "ptt":
                if (self.dictate_toggle_keys and self.dictate_toggle_keys.issubset(self.currently_pressed)) or name == "h":
                    self.current_session_mode = "toggle"
                    self.toggle_active_press = True
                    if self.on_mode_change:
                        threading.Thread(target=self.on_mode_change, args=("toggle", "dictate"), daemon=True).start()
                    return

            # D. If in AI Hands-Free Toggle mode, pressing AI keys stops it!
            if self.is_recording and self.current_session_mode == "ai_toggle":
                if is_new_press and not self.ai_toggle_active_press:
                    hit_stop = False
                    if self.ai_toggle_keys and self.ai_toggle_keys.issubset(self.currently_pressed):
                        hit_stop = True
                    elif self.ai_ptt_keys and self.ai_ptt_keys.issubset(self.currently_pressed):
                        hit_stop = True
                    elif self.ai_alias_keys and self.ai_alias_keys.issubset(self.currently_pressed):
                        hit_stop = True

                    if hit_stop:
                        self.is_recording = False
                        self.current_session_mode = None
                        self.ai_toggle_active_press = True
                        if self.on_stop:
                            threading.Thread(target=self.on_stop, args=("ai_toggle", "ai"), daemon=True).start()
                        return

            # E. If in Dictate Hands-Free Toggle mode, pressing Dictate combos stops it!
            if self.is_recording and self.current_session_mode == "toggle":
                if is_new_press and not self.toggle_active_press:
                    hit_stop = False
                    if self.dictate_toggle_keys and self.dictate_toggle_keys.issubset(self.currently_pressed):
                        hit_stop = True
                    elif self.dictate_ptt_keys and self.dictate_ptt_keys.issubset(self.currently_pressed):
                        hit_stop = True

                    if hit_stop:
                        self.is_recording = False
                        self.current_session_mode = None
                        self.toggle_active_press = True
                        if self.on_stop:
                            threading.Thread(target=self.on_stop, args=("toggle", "dictate"), daemon=True).start()
                        return

            # F. Idle -> Start recording
            if not self.is_recording:
                # 1. AI Assistant Hands-Free Toggle (skip when ai_toggle_keys == ai_ptt_keys in 'both' mode so PTT hold/tap works)
                if self.enable_ai_hotkey and self.ai_mode in ("both", "toggle"):
                    if self.ai_toggle_keys and (self.ai_mode == "toggle" or self.ai_toggle_keys != self.ai_ptt_keys) and self.ai_toggle_keys.issubset(self.currently_pressed):
                        if is_new_press and not self.ai_toggle_active_press and not any(k in self.currently_pressed for k in self._blocked_keys_for(self.ai_toggle_keys)):
                            self.is_recording = True
                            self.current_session_type = "ai"
                            self.current_session_mode = "ai_toggle"
                            self.ai_toggle_active_press = True
                            if self.on_start:
                                threading.Thread(target=self.on_start, args=("ai_toggle", "ai"), daemon=True).start()
                            return

                # 2. AI Assistant Push-to-Talk Hold (e.g. Win + Ctrl)
                if self.enable_ai_hotkey and self.ai_mode in ("both", "hold"):
                    if self.ai_ptt_keys and self.ai_ptt_keys.issubset(self.currently_pressed):
                        if not any(k in self.currently_pressed for k in self._blocked_keys_for(self.ai_ptt_keys)):
                            self.ai_ptt_press_time = time.time()
                            self.is_recording = True
                            self.current_session_type = "ai"
                            self.current_session_mode = "ai_ptt"
                            if self.on_start:
                                threading.Thread(target=self.on_start, args=("ai_ptt", "ai"), daemon=True).start()
                            return

                # 3. AI Assistant Shortcut Alias (e.g. Win + Ctrl + A) when distinct from ai_ptt/ai_toggle
                if self.enable_ai_hotkey and self.ai_alias_keys and self.ai_alias_keys != self.ai_ptt_keys and self.ai_alias_keys != self.ai_toggle_keys:
                    if self.ai_alias_keys.issubset(self.currently_pressed):
                        if is_new_press and not self.ai_toggle_active_press and not any(k in self.currently_pressed for k in self._blocked_keys_for(self.ai_alias_keys)):
                            self.is_recording = True
                            self.current_session_type = "ai"
                            self.current_session_mode = "ai_ptt" if self.ai_mode in ("both", "hold") else "ai_toggle"
                            if self.current_session_mode == "ai_ptt":
                                self.ai_ptt_press_time = time.time()
                            self.ai_toggle_active_press = True
                            if self.on_start:
                                threading.Thread(target=self.on_start, args=(self.current_session_mode, "ai"), daemon=True).start()
                            return

                # 4. Dictation Hands-Free Toggle (e.g. Win + Alt + H)
                if self.mode_setting in ("both", "toggle"):
                    if self.dictate_toggle_keys and self.dictate_toggle_keys.issubset(self.currently_pressed):
                        if is_new_press and not self.toggle_active_press and not any(k in self.currently_pressed for k in self._blocked_keys_for(self.dictate_toggle_keys)):
                            self.is_recording = True
                            self.current_session_type = "dictate"
                            self.current_session_mode = "toggle"
                            self.toggle_active_press = True
                            if self.on_start:
                                threading.Thread(target=self.on_start, args=("toggle", "dictate"), daemon=True).start()
                            return

                # 5. Dictation Push-to-Talk Hold (e.g. Win + Alt or Ctrl + Space)
                if self.mode_setting in ("both", "push_to_talk"):
                    if self.dictate_ptt_keys and self.dictate_ptt_keys.issubset(self.currently_pressed):
                        if not any(k in self.currently_pressed for k in self._blocked_keys_for(self.dictate_ptt_keys)):
                            self.is_recording = True
                            self.current_session_type = "dictate"
                            self.current_session_mode = "ptt"
                            if self.on_start:
                                threading.Thread(target=self.on_start, args=("ptt", "dictate"), daemon=True).start()
                            return

        elif event.event_type == "up":
            # If AI Push-to-Talk was recording and ANY of its combo keys is released, stop immediately!
            if self.is_recording and self.current_session_mode == "ai_ptt":
                if name in self.ai_ptt_keys or (self.ai_alias_keys and name in self.ai_alias_keys):
                    self.is_recording = False
                    self.current_session_mode = None
                    press_dur = time.time() - getattr(self, "ai_ptt_press_time", 0)
                    if press_dur < 0.32:
                        # Quick tap (< 320ms): Summon assistant and focus prompt for typing!
                        if self.on_cancel:
                            threading.Thread(target=self.on_cancel, daemon=True).start()
                        threading.Thread(target=notify_assistant_bridge, args=("summon", ""), daemon=True).start()
                    else:
                        if self.on_stop:
                            threading.Thread(target=self.on_stop, args=("ai_ptt", "ai"), daemon=True).start()

            # If Dictation Push-to-Talk was recording and ANY of its combo keys is released, stop immediately!
            elif self.is_recording and self.current_session_mode == "ptt":
                if name in self.dictate_ptt_keys:
                    self.is_recording = False
                    self.current_session_mode = None
                    if self.on_stop:
                        threading.Thread(target=self.on_stop, args=("ptt", "dictate"), daemon=True).start()

            self.currently_pressed.discard(name)
            if self.dictate_toggle_keys and not self.dictate_toggle_keys.issubset(self.currently_pressed):
                if self.dictate_ptt_keys and not self.dictate_ptt_keys.issubset(self.currently_pressed):
                    self.toggle_active_press = False
            if self.ai_toggle_keys and not self.ai_toggle_keys.issubset(self.currently_pressed):
                if self.ai_ptt_keys and not self.ai_ptt_keys.issubset(self.currently_pressed):
                    self.ai_toggle_active_press = False


# ---------------------------------------------------------------------------
# 4. Core Dictation Engine: Real-Time gRPC-Web Audio Streaming
# ---------------------------------------------------------------------------
class DictationDaemon:
    def __init__(self, pill_widget: FloatingPill = None, defer_connect: bool = False):
        self.cfg = db.load_config()
        self.enable_sound = self.cfg.get("enable_sound", True)
        self.enable_context_cascade = self.cfg.get("enable_context_cascade", True)
        self.toggle_hotkey = self.cfg.get("toggle_hotkey", self.cfg.get("hotkey", "windows+alt+h"))
        self.ptt_hotkey = self.cfg.get("push_to_talk_hotkey", "windows+alt")
        self.dictation_mode = self.cfg.get("dictation_mode", "both")

        self.port = None
        self.csrf_token = None
        self.headers = {}
        self.base_url = ""

        self.pill = pill_widget
        self.is_recording = False
        self.session_lock = threading.RLock()

        self.audio_queue = queue.Queue()
        self.accumulator = TranscriptAccumulator()
        self.session_ready_event = threading.Event()
        self.sender_done_event = threading.Event()
        self.stream_done_event = threading.Event()
        self.stop_requested = threading.Event()
        self.session_id = None
        self.session_seq = 0
        self.stream_response = None

        self.session_pcm_bytes = bytearray()
        self.dsp = AudioDSP()
        self.active_app_at_start = "Desktop"
        self.active_window_title = ""

        self.input_rate = 48000
        self.channels = 2
        self.block_size = 1920
        self.start_time = 0.0
        self.enable_ai_polish = self.cfg.get("enable_ai_polish", False)
        self.ai_model = self.cfg.get("ai_model", "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE")
        self.ai_preset_mode = self.cfg.get("ai_preset_mode", "flow")
        self.ai_system_prompt = self.cfg.get("ai_system_prompt", "")
        self.paste_last_hotkey = self.cfg.get("paste_last_hotkey", "alt+v")
        self.last_dictated_text = ""
        self.active_system_ctx = None

        # Dedicated AI Assistant Shortcut Configuration
        self.enable_ai_hotkey = self.cfg.get("enable_ai_hotkey", True)
        self.ai_ptt_hotkey = self.cfg.get("ai_ptt_hotkey", "windows+ctrl")
        self.ai_toggle_hotkey = self.cfg.get("ai_toggle_hotkey", "windows+ctrl+h")
        self.ai_hotkey = self.cfg.get("ai_hotkey", "windows+ctrl+a")
        self.ai_hotkey_mode = self.cfg.get("ai_hotkey_mode", "both")
        self.active_session_type = "dictate"

        # Deep Contextual Awareness & Granular Limits
        self.enable_deep_context = self.cfg.get("enable_deep_context", True)
        self.ctx_include_window = self.cfg.get("ctx_include_window", True)
        self.ctx_include_cursor = self.cfg.get("ctx_include_cursor", True)
        self.ctx_include_focused_control = self.cfg.get("ctx_include_focused_control", True)
        self.ctx_include_field_content = self.cfg.get("ctx_include_field_content", True)
        self.ctx_include_history = self.cfg.get("ctx_include_history", True)
        self.ctx_max_field_words = int(self.cfg.get("ctx_max_field_words", 500))
        self.ctx_history_limit = int(self.cfg.get("ctx_history_limit", 5))

        self.tray_icon = None
        self.input_stream = None

        # Safe connection initialization after all attributes are loaded
        if not defer_connect:
            self.ensure_connected(auto_start=True)
        threading.Thread(target=self._connection_watcher, daemon=True, name="LSWatcher").start()

        # Context cascade cache (polled periodically in background)
        self.cached_cascade_id = ""
        threading.Thread(target=self._cascade_poller, daemon=True).start()

        # Config file watcher (detects UI setting changes dynamically)
        self._last_config_mtime = 0
        threading.Thread(target=self._config_file_watcher, daemon=True).start()

        # Initialize Dual-Mode Hotkey Manager
        self.hotkey_mgr = HotkeyManager(
            toggle_hotkey=self.toggle_hotkey,
            ptt_hotkey=self.ptt_hotkey,
            paste_last_hotkey=self.paste_last_hotkey,
            mode=self.dictation_mode,
            ai_ptt_hotkey=self.ai_ptt_hotkey,
            ai_toggle_hotkey=self.ai_toggle_hotkey,
            ai_hotkey=self.ai_hotkey,
            ai_mode=self.ai_hotkey_mode,
            enable_ai_hotkey=self.enable_ai_hotkey,
            on_start=self.start_session,
            on_stop=self.stop_session,
            on_mode_change=self.switch_to_hands_free_mode,
            on_paste_last=self.trigger_paste_last,
            on_cancel=self.cancel_session
        )

    def trigger_paste_last(self):
        """Immediately pastes the most recent dictation into the currently focused window."""
        with self.session_lock:
            if self.is_recording:
                print("[Daemon] Canceling active recording to fulfill Quick-Paste (Alt+V)...")
                self.cancel_session()

        text = getattr(self, "last_dictated_text", "")
        if not text:
            # Query persistent history for the latest dictation
            recent = db.get_history(limit=1)
            if recent and recent[0].get("text"):
                text = recent[0]["text"]
                self.last_dictated_text = text

        if not text:
            print("[Daemon] No prior dictation to paste.")
            if self.pill:
                self.pill.root.after(0, lambda: [
                    self.pill.show(),
                    self.pill.set_text("No history to paste"),
                    self.pill.root.after(1200, self.pill.hide)
                ])
            return

        print(f"[Daemon] Quick-Pasting last dictation ({len(text.split())} words)...")
        paste_all_at_once(text)
        play_sound("done", self.enable_sound)
        if self.pill:
            self.pill.root.after(0, lambda: [
                self.pill.show(),
                self.pill.set_text("Re-Pasted!"),
                self.pill.root.after(1000, self.pill.hide)
            ])

    def switch_to_hands_free_mode(self, mode="toggle", session_type=None):
        if session_type:
            self.active_session_type = session_type
        play_sound("switch", self.enable_sound)
        if self.active_session_type == "ai":
            self.update_tray_tooltip("AntigravityFlow: AI Execution (HANDS-FREE)... ⚡🎙️")
        else:
            self.update_tray_tooltip("AntigravityFlow: Recording (HANDS-FREE)... 🎙️")
        if self.pill:
            self.pill.root.after(0, lambda: [
                self.pill.set_session_type(self.active_session_type),
                self.pill.set_mode("toggle")
            ])

    def is_server_alive(self):
        """Checks if current port and token are alive and accepting connections."""
        if not self.port or not self.csrf_token:
            return False
        try:
            req = urllib.request.Request(
                f"https://127.0.0.1:{self.port}/",
                headers={"x-codeium-csrf-token": self.csrf_token}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=0.25) as res:
                return res.status == 200
        except Exception:
            return False

    def ensure_connected(self, auto_start=True):
        """Ensures active connection to language_server.exe, discovering or launching it dynamically."""
        if self.is_server_alive():
            return True

        port, token = discover_antigravity_server(raise_on_error=False, auto_start=auto_start)
        if port and token:
            self.port = port
            self.csrf_token = token
            self.headers = {
                "content-type": "application/grpc-web+json",
                "x-codeium-csrf-token": self.csrf_token,
                "x-grpc-web": "1"
            }
            self.base_url = f"https://127.0.0.1:{self.port}/exa.language_server_pb.LanguageServerService"
            print(f"[Daemon] [+] Connected to Antigravity Language Server on port {self.port}")
            self.update_tray_tooltip(f"AntigravityFlow (Ready • Dictate: {self.ptt_hotkey.upper()}, AI: {self.ai_ptt_hotkey.upper()})")
            return True
        else:
            self.port = None
            self.csrf_token = None
            self.headers = {}
            self.base_url = ""
            self.update_tray_tooltip("AntigravityFlow (Waiting for Antigravity IDE...)")
            return False

    def _connection_watcher(self):
        """Background daemon thread that monitors and auto-connects to language_server when Antigravity starts."""
        while True:
            time.sleep(3.5)
            if not self.port or not self.is_server_alive():
                self.ensure_connected(auto_start=False)

    def _cascade_poller(self):
        while True:
            if not self.is_recording and self.enable_context_cascade and self.port and self.csrf_token:
                try:
                    cid = get_active_cascade_id(self.port, self.csrf_token)
                    if cid:
                        self.cached_cascade_id = cid
                except Exception:
                    pass
            time.sleep(4.0)

    def _config_file_watcher(self):
        config_path = db.CONFIG_PATH
        while True:
            try:
                if os.path.exists(config_path):
                    mtime = os.path.getmtime(config_path)
                    if self._last_config_mtime and mtime != self._last_config_mtime:
                        print("[Daemon] Configuration updated in background; reloading settings...")
                        self.reload_config()
                    self._last_config_mtime = mtime
            except Exception:
                pass
            time.sleep(1.0)

    def start_session(self, mode="toggle", session_type="dictate"):
        with self.session_lock:
            if self.is_recording:
                return

            if not self.ensure_connected(auto_start=True):
                print("[Daemon] Cannot start dictation: Language server is not running.")
                self.hotkey_mgr.is_recording = False
                self.hotkey_mgr.current_session_mode = None
                play_sound("error", self.enable_sound)
                if self.pill:
                    self.pill.root.after(0, lambda: [
                        self.pill.set_state("idle"),
                        self.pill.hide()
                    ])
                return

            # Guard against race where user released PTT shortcut before server connection finished
            if mode in ("ptt", "ai_ptt") and not self.hotkey_mgr.is_recording:
                if self.pill:
                    self.pill.root.after(0, lambda: [
                        self.pill.set_state("idle"),
                        self.pill.hide()
                    ])
                return

            self.active_session_type = session_type
            self.start_time = time.time()
            self.stop_requested.clear()
            self.session_ready_event.clear()
            self.sender_done_event.clear()
            self.stream_done_event.clear()
            self.session_id = None
            self.session_seq = 0
            self.accumulator.reset()
            self.session_pcm_bytes = bytearray()
            self.dsp.reset()
            if self.enable_deep_context:
                self.active_system_ctx = system_context.get_active_system_context(
                    include_window=self.ctx_include_window,
                    include_cursor=self.ctx_include_cursor,
                    include_focused=self.ctx_include_focused_control,
                    include_field=self.ctx_include_field_content,
                    max_field_words=self.ctx_max_field_words
                )
            else:
                self.active_system_ctx = system_context.get_active_system_context(
                    include_window=True,
                    include_cursor=False,
                    include_focused=False,
                    include_field=False,
                    max_field_words=0
                )
            self.active_app_at_start = self.active_system_ctx.get("app_name", "Desktop")
            self.active_window_title = self.active_system_ctx.get("window_title", "")
            self.is_recording = True

            # Clear queue
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except queue.Empty:
                    break

            # 1. Open Microphone on-demand immediately (< 30ms)
            def audio_callback(indata, frames, time_info, status):
                if self.is_recording:
                    peak = float(np.max(np.abs(indata)))
                    if self.pill:
                        self.pill.set_volume(peak * 3.0)
                    mono_48k = (indata[:, 0] + indata[:, 1]) / 2.0
                    pcm_bytes = self.dsp.process(mono_48k)
                    if pcm_bytes:
                        self.session_pcm_bytes.extend(pcm_bytes)
                        self.audio_queue.put(pcm_bytes)

            try:
                self.input_stream = sd.InputStream(
                    samplerate=self.input_rate,
                    channels=self.channels,
                    dtype='float32',
                    blocksize=self.block_size,
                    callback=audio_callback
                )
                self.input_stream.start()
            except Exception as e:
                print(f"[Daemon] Failed to open microphone: {e}")
                self.input_stream = None

            play_sound("start", self.enable_sound)
            if session_type == "ai":
                self.update_tray_tooltip(f"AntigravityFlow: AI Assistant ({mode.upper()})... ⚡🎙️")
                threading.Thread(target=notify_assistant_bridge, args=("voice-state", "", "recording"), daemon=True).start()
                if self.pill:
                    self.pill.root.after(0, lambda: [self.pill.set_state("idle"), self.pill.hide()])
            else:
                self.update_tray_tooltip(f"AntigravityFlow: Recording ({mode.upper()})... 🎙️")
                # Show Floating Pill Widget only in dictation mode
                if self.pill:
                    self.pill.root.after(0, lambda: [
                        self.pill.set_session_type("dictate"),
                        self.pill.set_mode(mode),
                        self.pill.set_state("recording"),
                        self.pill.show()
                    ])

            # Context discovery
            cascade_id = self.cached_cascade_id if self.enable_context_cascade else ""
            pre_ctx = get_precursor_context()

            # Connect live StreamAudioTranscription with seamless auto-renewal across speech pauses
            def connect_and_listen():
                while not self.stop_requested.is_set():
                    self.session_ready_event.clear()
                    current_context = self.accumulator.get_full_text() or pre_ctx or ""
                    init_json = json.dumps({
                        "mimeType": "audio/pcm;rate=16000",
                        "preCursorText": current_context,
                        "postCursorText": "",
                        "cascadeId": cascade_id or "",
                        "continuous": False
                    }).encode("utf-8")

                    stream_req = urllib.request.Request(
                        f"{self.base_url}/StreamAudioTranscription",
                        data=make_grpc_web_frame(init_json),
                        headers=self.headers,
                        method="POST"
                    )
                    try:
                        self.stream_response = urllib.request.urlopen(stream_req, context=ctx, timeout=6.0)
                        for msg in parse_grpc_web_stream(self.stream_response):
                            if isinstance(msg, dict):
                                if "ready" in msg:
                                    self.session_id = msg["ready"].get("sessionId")
                                    self.session_seq = 0
                                    self.session_ready_event.set()
                                elif "transcription" in msg:
                                    self.accumulator.process_message(msg)
                                elif "complete" in msg:
                                    # Google Conformer endpointed silence segment; finalize text accumulated so far
                                    self.accumulator.finalize_current()
                                    break
                    except Exception as e:
                        if not self.stop_requested.is_set():
                            time.sleep(0.04)
                    finally:
                        if self.stop_requested.is_set():
                            self.session_ready_event.set()
                            self.stream_done_event.set()
                    if self.stop_requested.is_set():
                        break

            threading.Thread(target=connect_and_listen, daemon=True).start()

            # Worker that streams audio chunks directly to Google with zero delay
            def sender_worker():
                conn = None
                chunk_path = "/exa.language_server_pb.LanguageServerService/SendAudioChunk"

                try:
                    conn = http.client.HTTPSConnection('127.0.0.1', self.port, context=ctx, timeout=4.0)
                    while True:
                        # Wait for session readiness or renewal
                        if not self.session_ready_event.is_set():
                            if not self.session_ready_event.wait(timeout=2.0) or not self.session_id:
                                if not self.is_recording and self.audio_queue.empty():
                                    break
                                continue

                        try:
                            raw_chunk = self.audio_queue.get(timeout=0.04)
                        except queue.Empty:
                            if not self.is_recording and self.audio_queue.empty():
                                break
                            continue

                        if raw_chunk is None:
                            # Sentinel indicating audio recording finished
                            break

                        current_session = self.session_id
                        seq = self.session_seq
                        self.session_seq += 1

                        body_dict = {
                            "sessionId": current_session,
                            "data": base64.b64encode(raw_chunk).decode("ascii")
                        }
                        if seq > 0:
                            body_dict["sequenceNumber"] = seq

                        chunk_bytes = make_grpc_web_frame(json.dumps(body_dict).encode("utf-8"))
                        try:
                            conn.request("POST", chunk_path, body=chunk_bytes, headers=self.headers)
                            res = conn.getresponse()
                            res.read()
                        except Exception:
                            try:
                                conn.close()
                                conn = http.client.HTTPSConnection('127.0.0.1', self.port, context=ctx, timeout=4.0)
                                conn.request("POST", chunk_path, body=chunk_bytes, headers=self.headers)
                                res = conn.getresponse()
                                res.read()
                            except Exception:
                                pass
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    self.sender_done_event.set()

            threading.Thread(target=sender_worker, daemon=True).start()

    def stop_session(self, mode=None, session_type=None):
        with self.session_lock:
            if not self.is_recording:
                return

            if session_type:
                self.active_session_type = session_type

            # 1. Immediately switch pill visual cue to rotating processing animation!
            if self.pill:
                self.pill.root.after(0, lambda: self.pill.set_state("processing"))

            play_sound("stop", self.enable_sound)
            if self.active_session_type == "ai":
                threading.Thread(target=notify_assistant_bridge, args=("voice-state", "", "idle"), daemon=True).start()

            try:
                # 2. 500ms background listening window to capture trailing words/syllables
                # Visual cue happened immediately at t=0ms, while mic continues capturing for 500ms
                time.sleep(0.50)

                # 3. Stop microphone immediately (100% Privacy - Mic is OFF)
                self.stop_requested.set()
                self.is_recording = False
                if self.input_stream:
                    try:
                        self.input_stream.stop()
                        self.input_stream.close()
                    except Exception:
                        pass
                    self.input_stream = None

                # 4. Inject 4 frames (160ms) of comfort trailing silence to cleanly resolve final phonemes
                silence_frame = b'\x00' * 1280
                for _ in range(4):
                    self.audio_queue.put(silence_frame)

                # 5. Sentinel in queue to let sender_worker finish
                self.audio_queue.put(None)

                # 6. Wait for sender_worker to flush chunks (< 200ms)
                self.sender_done_event.wait(timeout=1.2)

                # 7. Dispatch /EndAudioSession
                if self.session_id and self.port:
                    end_path = "/exa.language_server_pb.LanguageServerService/EndAudioSession"
                    end_bytes = make_grpc_web_frame(json.dumps({"sessionId": self.session_id}).encode("utf-8"))
                    try:
                        conn = http.client.HTTPSConnection('127.0.0.1', self.port, context=ctx, timeout=2.0)
                        conn.request("POST", end_path, body=end_bytes, headers=self.headers)
                        res = conn.getresponse()
                        res.read()
                        conn.close()
                    except Exception as e:
                        print(f"[Daemon] EndAudioSession error: {e}")

                # 8. Wait for Google stream to complete (~150-200ms)
                if not self.stream_done_event.wait(timeout=2.0):
                    if self.stream_response:
                        try:
                            self.stream_response.close()
                        except Exception:
                            pass

                self.update_tray_tooltip("AntigravityFlow: Transcribing... ⏳")

                duration = max(0.3, time.time() - self.start_time)
                recorded_pcm = bytes(self.session_pcm_bytes)
                category = db.categorize_app(self.active_app_at_start)
                self.accumulator.finalize_current()
                raw_text = self.accumulator.get_full_text().strip()
                raw_words = len(raw_text.split()) if raw_text else 0

                # 9. Intelligent Zero-Loss Fallback & Discrepancy Recovery:
                needs_recovery = False
                if not raw_text and len(recorded_pcm) > 8000:
                    needs_recovery = True
                elif duration >= 4.5 and len(recorded_pcm) > 144000:
                    expected_min_words = int(duration * 0.7)
                    if raw_words < expected_min_words:
                        needs_recovery = True

                if needs_recovery and self.port and self.csrf_token:
                    print(f"[Daemon] Activating zero-loss recovery (duration: {duration:.1f}s, streamed words: {raw_words})...")
                    cascade_id = self.cached_cascade_id if self.enable_context_cascade else ""
                    pre_ctx = get_precursor_context()
                    recovered_text = batch_transcriber.transcribe_audio_batch(
                        pcm_data=recorded_pcm,
                        port=self.port,
                        csrf_token=self.csrf_token,
                        cascade_id=cascade_id,
                        pre_cursor_text=pre_ctx,
                        category=category,
                        timeout=5.0
                    )
                    recovered_words = len(recovered_text.split()) if recovered_text else 0
                    if recovered_text and recovered_words >= raw_words:
                        raw_text = recovered_text

                final_text = raw_text.strip() if raw_text else ""

                if final_text:
                    # 1. Apply Custom Vocabulary & Acronym Formatting
                    final_text = format_transcript(final_text, category)

                    # 2. Speech Macro & Snippet Expansion Engine
                    macro_expanded = db.expand_macro_text(final_text)
                    if macro_expanded != final_text:
                        print(f"[Daemon] Expanded speech macro: '{final_text}' -> '{macro_expanded[:60]}...'")
                        final_text = macro_expanded

                    is_ai_lane = (self.active_session_type == "ai")

                    # DUAL-LANE INTELLIGENCE:
                    # If session_type is 'ai', ALWAYS route to Floating Gemini Assistant!
                    if is_ai_lane:
                        print(f"[Daemon] Instantaneous routing to AntigravityFlow AI Assistant: '{final_text}'")
                        threading.Thread(target=notify_assistant_bridge, args=("query", final_text), daemon=True).start()
                        db.save_dictation(
                            text=final_text,
                            duration=duration,
                            app_name=self.active_app_at_start,
                            pcm_bytes=recorded_pcm,
                            window_title=getattr(self, "active_window_title", ""),
                            session_type="ai"
                        )
                    else:
                        # Direct writing / coding / dictation lane: execute and paste to foreground window
                        if self.enable_ai_polish:
                            try:
                                preset = getattr(self, "ai_preset_mode", "flow")
                                prompt = ""
                                if preset == "custom":
                                    prompt = self.ai_system_prompt

                                tooltip_text = "AntigravityFlow: AI Polishing... 🤖"
                                self.update_tray_tooltip(tooltip_text)

                                polished = llm_refiner.polish_with_gemini(
                                    final_text,
                                    model=self.ai_model,
                                    preset_mode=preset,
                                    system_prompt=prompt,
                                    app_name=self.active_app_at_start,
                                    window_title=getattr(self, "active_window_title", ""),
                                    system_ctx=getattr(self, "active_system_ctx", None),
                                    config=self.cfg
                                )
                                if polished and polished.strip():
                                    final_text = polished.strip()
                            except Exception as err:
                                print(f"[Daemon] AI Polish/Execution fallback: {err}")

                        # Save as last dictated text for instant Alt+V replay
                        self.last_dictated_text = final_text

                        # Save dictation & encrypted audio recording with window title for categorization
                        db.save_dictation(
                            text=final_text,
                            duration=duration,
                            app_name=self.active_app_at_start,
                            pcm_bytes=recorded_pcm,
                            window_title=getattr(self, "active_window_title", ""),
                            session_type=self.active_session_type
                        )
                        # Paste cleanly with Win+V exclusion flags (zero clipboard pollution)
                        paste_all_at_once(final_text)
            except Exception as e:
                print(f"[Daemon] Error during stop_session processing: {e}")
            finally:
                self.stop_requested.set()
                self.is_recording = False
                if self.input_stream:
                    try:
                        self.input_stream.stop()
                        self.input_stream.close()
                    except Exception:
                        pass
                    self.input_stream = None
                if self.pill:
                    self.pill.root.after(0, lambda: [self.pill.set_state("idle"), self.pill.hide()])
                self.update_tray_tooltip(f"AntigravityFlow: Idle ({self.toggle_hotkey.upper()} / {self.ptt_hotkey.upper()})")

    def cancel_session(self):
        with self.session_lock:
            if not self.is_recording:
                return
            self.stop_requested.set()
            if self.active_session_type == "ai":
                threading.Thread(target=notify_assistant_bridge, args=("voice-state", "", "idle"), daemon=True).start()
            if self.stream_response:
                try:
                    self.stream_response.close()
                except Exception:
                    pass
            if self.pill:
                self.pill.root.after(0, lambda: [self.pill.set_state("idle"), self.pill.hide()])
            self.is_recording = False
            if self.input_stream:
                try:
                    self.input_stream.stop()
                    self.input_stream.close()
                except Exception:
                    pass
                self.input_stream = None

            try:
                self.audio_queue.put(None)
            except Exception:
                pass

            if self.session_id:
                try:
                    end_path = "/exa.language_server_pb.LanguageServerService/EndAudioSession"
                    end_bytes = make_grpc_web_frame(json.dumps({"sessionId": self.session_id}).encode("utf-8"))
                    conn = http.client.HTTPSConnection('127.0.0.1', self.port, context=ctx, timeout=1.0)
                    conn.request("POST", end_path, body=end_bytes, headers=self.headers)
                    conn.getresponse().read()
                    conn.close()
                except Exception:
                    pass

            self.session_pcm_bytes.clear()
            self.accumulator.reset()
            self.dsp.reset()
            self.update_tray_tooltip(f"AntigravityFlow: Idle ({self.toggle_hotkey.upper()} / {self.ptt_hotkey.upper()})")

    def shutdown(self):
        """Clean shutdown of daemon services, tray icon, floating pill, and child processes."""
        print("[Daemon] Initiating clean shutdown sequence...")
        try:
            self.stop_requested.set()
            self.is_recording = False
            if self.input_stream:
                try:
                    self.input_stream.stop()
                    self.input_stream.close()
                except Exception:
                    pass
                self.input_stream = None
        except Exception:
            pass
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            if self.pill and self.pill.root:
                self.pill.root.after(0, self.pill.root.destroy)
        except Exception:
            pass
        try:
            llm_refiner.terminate_spawned_language_server()
        except Exception:
            pass
        try:
            current_pid = os.getpid()
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if p.pid == current_pid:
                        continue
                    pname = (p.info.get('name') or '').lower()
                    pcmd = ' '.join(p.info.get('cmdline') or []).lower()
                    if pname == 'antigravityflow.exe':
                        p.kill()
                    elif 'electron.exe' in pname and 'antigravityflow' in pcmd:
                        p.kill()
                    elif pname == 'antigravityflowdaemon.exe':
                        p.kill()
                except Exception:
                    pass
        except Exception:
            pass
        os._exit(0)

    def reload_config(self, connect_async: bool = False):
        self.cfg = db.load_config()
        self.toggle_hotkey = self.cfg.get("toggle_hotkey", self.cfg.get("hotkey", "windows+alt+h"))
        self.ptt_hotkey = self.cfg.get("push_to_talk_hotkey", "windows+alt")
        self.dictation_mode = self.cfg.get("dictation_mode", "both")
        self.enable_sound = self.cfg.get("enable_sound", True)
        self.enable_context_cascade = self.cfg.get("enable_context_cascade", True)
        self.enable_ai_polish = self.cfg.get("enable_ai_polish", False)
        self.ai_model = self.cfg.get("ai_model", "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE")
        self.ai_assistant_model = self.cfg.get("ai_assistant_model", "MODEL_GOOGLE_GEMINI_2_5_FLASH")
        self.ai_preset_mode = self.cfg.get("ai_preset_mode", "flow")
        self.ai_system_prompt = self.cfg.get("ai_system_prompt", "")
        self.start_on_login = self.cfg.get("start_on_login", True)

        # Deep Contextual Awareness & Granular Limits
        self.enable_deep_context = self.cfg.get("enable_deep_context", True)
        self.ctx_include_window = self.cfg.get("ctx_include_window", True)
        self.ctx_include_cursor = self.cfg.get("ctx_include_cursor", True)
        self.ctx_include_focused_control = self.cfg.get("ctx_include_focused_control", True)
        self.ctx_include_field_content = self.cfg.get("ctx_include_field_content", True)
        self.ctx_include_history = self.cfg.get("ctx_include_history", True)
        self.ctx_max_field_words = int(self.cfg.get("ctx_max_field_words", 500))
        self.ctx_history_limit = int(self.cfg.get("ctx_history_limit", 5))

        # Sync Windows Startup
        set_windows_autostart(self.start_on_login)

        if connect_async:
            threading.Thread(target=lambda: self.ensure_connected(auto_start=True), daemon=True).start()
        else:
            self.ensure_connected(auto_start=True)

        self.enable_ai_hotkey = self.cfg.get("enable_ai_hotkey", True)
        self.ai_ptt_hotkey = self.cfg.get("ai_ptt_hotkey", "windows+ctrl")
        self.ai_toggle_hotkey = self.cfg.get("ai_toggle_hotkey", "windows+ctrl+h")
        self.ai_hotkey = self.cfg.get("ai_hotkey", "windows+ctrl+a")
        self.ai_hotkey_mode = self.cfg.get("ai_hotkey_mode", "both")

        self.paste_last_hotkey = self.cfg.get("paste_last_hotkey", "alt+v")
        self.hotkey_mgr.update_hotkeys(
            toggle_hotkey=self.toggle_hotkey,
            ptt_hotkey=self.ptt_hotkey,
            mode=self.dictation_mode,
            paste_last_hotkey=self.paste_last_hotkey,
            ai_ptt_hotkey=self.ai_ptt_hotkey,
            ai_toggle_hotkey=self.ai_toggle_hotkey,
            ai_hotkey=self.ai_hotkey,
            ai_mode=self.ai_hotkey_mode,
            enable_ai_hotkey=self.enable_ai_hotkey
        )
        self.update_tray_tooltip(f"AntigravityFlow: Idle (Dictate: {self.ptt_hotkey.upper()}, AI: {self.ai_ptt_hotkey.upper()})")
        print(f"[Daemon] Configuration reloaded in real time (Dictate PTT: {self.ptt_hotkey.upper()}, Dictate Toggle: {self.toggle_hotkey.upper()}, AI PTT: {self.ai_ptt_hotkey.upper()}, AI Toggle: {self.ai_toggle_hotkey.upper()}, Quick-Paste: {self.paste_last_hotkey.upper()})")

    def update_tray_tooltip(self, text):
        if self.tray_icon:
            self.tray_icon.title = text


# ---------------------------------------------------------------------------
# 5. Tray Icon & Application Loop
# ---------------------------------------------------------------------------
def create_tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(20, 22, 28, 255))
    # Elegant gold emblem
    draw.rounded_rectangle((24, 14, 40, 36), radius=8, fill=(217, 163, 62, 255))
    draw.arc((18, 22, 46, 44), start=0, end=180, fill=(245, 235, 215, 255), width=3)
    draw.line((32, 44, 32, 52), fill=(245, 235, 215, 255), width=3)
    draw.line((24, 52, 40, 52), fill=(245, 235, 215, 255), width=3)
    return img

ELECTRON_EXE = os.path.join(UI_DIR, "node_modules", "electron", "dist", "electron.exe")

def prewarm_ui():
    """Launches UI pre-warmed in background with show:false for instant <30ms opening."""
    if os.path.exists(ELECTRON_EXE):
        subprocess.Popen([ELECTRON_EXE, ".", "--hidden"], cwd=UI_DIR)

def open_ui():
    """Triggers instant reveal of pre-warmed dashboard window."""
    try:
        data = json.dumps({"action": "show-dashboard"}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:48199", data=data, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=0.35) as res:
            if res.status == 200:
                return
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        candidate = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "..", "AntigravityFlow.exe"))
        if os.path.exists(candidate):
            subprocess.Popen([candidate], cwd=os.path.dirname(candidate))
            return
    if os.path.exists(ELECTRON_EXE):
        subprocess.Popen([ELECTRON_EXE, "."], cwd=UI_DIR)
        return
    local_programs = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "AntigravityFlow", "AntigravityFlow.exe")
    if os.path.exists(local_programs):
        subprocess.Popen([local_programs], cwd=os.path.dirname(local_programs))
    else:
        subprocess.Popen("npx electron .", shell=True, cwd=UI_DIR)

_DAEMON_MUTEX = None

def acquire_single_instance_mutex():
    """Acquires a global Windows named mutex to atomically prevent multiple daemon instances."""
    global _DAEMON_MUTEX
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            mutex_name = "Global\\AntigravityFlow_Daemon_SingleInstance_Mutex"
            mutex = kernel32.CreateMutexW(None, False, mutex_name)
            last_error = kernel32.GetLastError()
            if last_error == 183:  # ERROR_ALREADY_EXISTS
                return False
            _DAEMON_MUTEX = mutex
            return True
        except Exception:
            return True
    return True

def check_existing_instance():
    """Checks if an existing AntigravityFlow daemon is already running and serving on port 48198."""
    for endpoint in ("http://127.0.0.1:48198/status", "http://127.0.0.1:48198/health"):
        try:
            req = urllib.request.Request(endpoint)
            with urllib.request.urlopen(req, timeout=0.4) as res:
                if res.status == 200:
                    return True
        except Exception:
            pass
    return False

def safe_write_json(obj):
    """Safely writes single-line JSON to CLI stdout without raising OSError/BrokenPipeError on closed pipes."""
    target = getattr(sys, "_CLI_JSON_STDOUT", None) or _CLI_JSON_STDOUT or sys.stdout
    if target is None:
        return
    try:
        payload = json.dumps(obj, ensure_ascii=False)
        target.write(payload + "\n")
        target.flush()
    except (OSError, BrokenPipeError, ValueError):
        pass

def main():
    try:
        if not acquire_single_instance_mutex():
            print("[+] Another AntigravityFlow daemon instance is already active. Exiting cleanly.")
            sys.exit(0)

        if check_existing_instance():
            print("[+] AntigravityFlow daemon is already running on port 48198. Exiting cleanly.")
            sys.exit(0)

        pill = FloatingPill()
        pill.hide()

        daemon = DictationDaemon(pill_widget=pill, defer_connect=True)

        # Start local HTTP control server immediately on 127.0.0.1:48198 before connecting to LS
        start_daemon_control_server(daemon, port=48198)
        daemon.reload_config(connect_async=True)

        # Pre-warm AntigravityFlow dashboard in background for instant opening
        prewarm_ui()

        # Wire pill buttons cleanly off the Tkinter UI thread so UI never blocks
        pill.on_cancel = lambda: threading.Thread(target=daemon.cancel_session, daemon=True).start()
        pill.on_confirm = lambda: threading.Thread(target=daemon.stop_session, daemon=True).start()

        # Hook dual-mode keyboard listener
        keyboard.hook(daemon.hotkey_mgr.handle_event)

        def on_open_ui(icon, item):
            open_ui()

        def on_reload(icon, item):
            threading.Thread(target=daemon.reload_config, kwargs={"connect_async": True}, daemon=True).start()

        def on_quit(icon, item):
            threading.Thread(target=daemon.shutdown, daemon=True).start()

        menu = pystray.Menu(
            pystray.MenuItem("🎙️ AntigravityFlow", lambda: None, enabled=False),
            pystray.MenuItem(f"Dictate: Hold {daemon.ptt_hotkey.upper()} / Tap {daemon.toggle_hotkey.upper()}", lambda: None, enabled=False),
            pystray.MenuItem(f"AntigravityFlow AI: Hold {daemon.ai_ptt_hotkey.upper()} / Tap {daemon.ai_toggle_hotkey.upper()}", lambda: None, enabled=False),
            pystray.MenuItem(f"Quick Paste: Tap {daemon.paste_last_hotkey.upper()}", lambda: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("✨ AntigravityFlow Dashboard", on_open_ui, default=True),
            pystray.MenuItem("🔄 Reload Config", on_reload),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Exit", on_quit)
        )

        daemon.tray_icon = pystray.Icon("AntigravityFlow", create_tray_image(), f"AntigravityFlow (Dictate: {daemon.ptt_hotkey.upper()}, AI: {daemon.ai_ptt_hotkey.upper()})", menu)
        daemon.tray_icon.run_detached()

        print(f"\n[+] AntigravityFlow Dual-Lane Engine Active")
        print(f"[>] Dictation: Hold [{daemon.ptt_hotkey.upper()}] to speak & paste, Tap [{daemon.toggle_hotkey.upper()}] for hands-free.")
        print(f"[>] AntigravityFlow AI: Hold [{daemon.ai_ptt_hotkey.upper()}] to speak to AI, Tap [{daemon.ai_toggle_hotkey.upper()}] for hands-free AI.")
        print(f"[>] Quick-Paste: Tap [{daemon.paste_last_hotkey.upper()}] to instantly paste last dictation.")

        pill.root.mainloop()
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        try:
            with open(DAEMON_OUT_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n[FATAL DAEMON CRASH]:\n{err_msg}\n")
        except Exception:
            pass
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        try:
            if cmd == "quota":
                import quota_service
                res = quota_service.get_quota_summary()
                safe_write_json(res)
                sys.exit(0)
            elif cmd == "retranscribe":
                import retranscribe
                rec_id = int(sys.argv[2])
                res = retranscribe.retranscribe(rec_id)
                safe_write_json({"success": bool(res), "data": res} if res else {"success": False, "error": "Retranscription failed or recording not found."})
                sys.exit(0)
            elif cmd in ("history-get", "history-load"):
                safe_write_json({"success": True, "data": db.get_history()})
                sys.exit(0)
            elif cmd == "history-delete":
                rec_id = int(sys.argv[2])
                ok = db.delete_dictation(rec_id)
                safe_write_json({"success": bool(ok)})
                sys.exit(0)
            elif cmd == "history-clear":
                ok = db.clear_all_history()
                safe_write_json({"success": bool(ok)})
                sys.exit(0)
            elif cmd in ("vocab-load", "vocab-get"):
                import formatter
                safe_write_json({"success": True, "data": formatter.load_vocabulary()})
                sys.exit(0)
            elif cmd in ("macro-get", "macros-get"):
                safe_write_json({"success": True, "data": db.get_macros()})
                sys.exit(0)
            elif cmd in ("macro-save", "macros-save"):
                raw_input = sys.argv[2] if len(sys.argv) > 2 else "{}"
                m = json.loads(raw_input)
                res = db.save_macro(
                    m.get("trigger_keyword", m.get("trigger", "")),
                    m.get("expansion_text", m.get("expansion", "")),
                    m.get("description", ""),
                    m.get("category", "General"),
                    m.get("id")
                )
                safe_write_json(res)
                sys.exit(0)
            elif cmd in ("macro-delete", "macros-delete"):
                rec_id = int(sys.argv[2])
                res = db.delete_macro(rec_id)
                safe_write_json({"success": bool(res)})
                sys.exit(0)
            elif cmd in ("macro-expand", "macros-expand"):
                raw_input = sys.argv[2] if len(sys.argv) > 2 else '""'
                try:
                    t = json.loads(raw_input)
                except Exception:
                    t = raw_input
                exp = db.expand_macro_text(t)
                safe_write_json({"success": True, "expanded": exp})
                sys.exit(0)
            elif cmd in ("query", "models", "search", "exec", "screenshot", "paste", "refine", "tts-synth", "tts-demo", "tts-voices"):
                import assistant_service
                sys.argv.pop(0)  # shift so assistant_service receives subcommand as sys.argv[1]
                sys.argv.insert(0, "assistant_service")
                assistant_service.main()
                sys.exit(0)
        except Exception as e:
            err_msg = traceback.format_exc()
            try:
                with open(CRASH_LOG, "a", encoding="utf-8") as f:
                    f.write(f"\n--- {time.ctime()} CLI '{cmd}' error ---\n{err_msg}\n")
            except Exception:
                pass
            safe_write_json({"success": False, "error": str(e)})
            sys.exit(1)
    main()
