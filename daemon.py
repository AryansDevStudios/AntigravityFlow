import base64
import ctypes
from ctypes import wintypes
import winreg
import http.client
import json
import sys
import os

# Windows console encoding safeguard & pythonw support
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
elif hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')
elif hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(BASE_DIR, "ui")
SOUNDS_DIR = os.path.join(BASE_DIR, "sounds")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------------------
# 1. Fast In-Memory Discovery via psutil (< 25ms)
# ---------------------------------------------------------------------------
def discover_antigravity_server():
    server_proc = None
    for p in psutil.process_iter(['name', 'cmdline']):
        if p.info['name'] == 'language_server.exe':
            server_proc = p
            break

    if not server_proc:
        raise RuntimeError("Antigravity is not running. Please launch Antigravity first.")

    cmd = ' '.join(server_proc.info['cmdline'] or [])
    token_match = re.search(r'--csrf_token\s+([a-f0-9-]+)', cmd)
    if not token_match:
        raise RuntimeError("Could not find CSRF token in language_server arguments.")
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
        raise RuntimeError(f"Could not connect via HTTPS to any detected port: {ports}")

    return active_port, csrf_token


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
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enable:
            python_exe = sys.executable
            pythonw_exe = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
            exec_bin = pythonw_exe if os.path.exists(pythonw_exe) else python_exe
            script_path = os.path.abspath(__file__)
            cmd = f'"{exec_bin}" "{script_path}"'
            winreg.SetValueEx(key, val_name, 0, winreg.REG_SZ, cmd)
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
        else: # "stop"
            wav_file = os.path.join(SOUNDS_DIR, "stop.wav")
            if os.path.exists(wav_file):
                winsound.PlaySound(wav_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass

def get_foreground_app():
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "Desktop"
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            return psutil.Process(pid.value).name()
    except Exception:
        pass
    return "Desktop"

def paste_all_at_once(text: str):
    """Puts final formatted text on Windows clipboard and sends Ctrl+V in one shot."""
    if not text:
        return

    old_clip = ""
    try:
        old_clip = pyperclip.paste()
    except Exception:
        pass

    try:
        pyperclip.copy(text)
        time.sleep(0.02)

        VK_CONTROL = 0x11
        VK_V = 0x56
        KEYEVENTF_KEYUP = 0x0002

        user32 = ctypes.windll.user32
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

        # Restore clipboard after 200ms
        def restore():
            time.sleep(0.20)
            try:
                pyperclip.copy(old_clip)
            except Exception:
                pass
        threading.Thread(target=restore, daemon=True).start()
    except Exception as e:
        print(f"[Paste Error] {e}")


# ---------------------------------------------------------------------------
# 3. Dual-Mode Hotkey Manager (Push-to-Talk + Hands-Free Toggle)
# ---------------------------------------------------------------------------
class HotkeyManager:
    def __init__(self, toggle_hotkey="windows+alt+h", ptt_hotkey="windows+alt", mode="both", on_start=None, on_stop=None, on_mode_change=None):
        self.mode_setting = mode  # "both", "push_to_talk", "toggle"
        self.toggle_keys = self._normalize_combo(toggle_hotkey)
        self.ptt_keys = self._normalize_combo(ptt_hotkey)
        self.toggle_raw = toggle_hotkey
        self.ptt_raw = ptt_hotkey
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_mode_change = on_mode_change

        self.currently_pressed = set()
        self.current_session_mode = None
        self.is_recording = False
        self.toggle_active_press = False
        self.is_paused = False

    def update_hotkeys(self, toggle_hotkey, ptt_hotkey, mode="both"):
        self.mode_setting = mode
        self.toggle_keys = self._normalize_combo(toggle_hotkey)
        self.ptt_keys = self._normalize_combo(ptt_hotkey)
        self.toggle_raw = toggle_hotkey
        self.ptt_raw = ptt_hotkey

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

    def handle_event(self, event):
        # 0. Temporarily disable dictation trigger while user is recording new keys in settings
        if self.is_paused or os.path.exists(KEY_RECORDING_LOCK):
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
            is_new_press = name not in self.currently_pressed
            self.currently_pressed.add(name)

            # A. Dynamic Switch / Promotion: from active PTT to Hands-Free Toggle
            # If user is holding PTT (e.g. Windows+Alt) and presses 'H' (or the remaining toggle keys):
            if self.is_recording and self.current_session_mode == "ptt":
                is_toggle_trigger = False
                if self.toggle_keys and self.toggle_keys.issubset(self.currently_pressed):
                    is_toggle_trigger = True
                elif self.toggle_keys and (self.toggle_keys - self.ptt_keys) and (name in (self.toggle_keys - self.ptt_keys)):
                    is_toggle_trigger = True

                if is_toggle_trigger:
                    self.current_session_mode = "toggle"
                    self.toggle_active_press = True
                    if self.on_mode_change:
                        threading.Thread(target=self.on_mode_change, args=("toggle",), daemon=True).start()
                    return

            # B. If already in Hands-Free Toggle mode, pressing either Toggle combo or PTT combo stops it!
            if self.is_recording and self.current_session_mode == "toggle":
                if is_new_press and not self.toggle_active_press:
                    hit_stop = False
                    if self.toggle_keys and self.toggle_keys.issubset(self.currently_pressed):
                        hit_stop = True
                    elif self.ptt_keys and self.ptt_keys.issubset(self.currently_pressed):
                        hit_stop = True

                    if hit_stop:
                        self.is_recording = False
                        self.current_session_mode = None
                        self.toggle_active_press = True
                        if self.on_stop:
                            threading.Thread(target=self.on_stop, args=("toggle",), daemon=True).start()
                        return

            # C. Idle -> Start recording
            if not self.is_recording:
                # 1. Toggle check (e.g. Windows+Alt+H pressed together from idle)
                if self.mode_setting in ("both", "toggle"):
                    if self.toggle_keys and self.toggle_keys.issubset(self.currently_pressed):
                        if is_new_press and not self.toggle_active_press:
                            self.is_recording = True
                            self.current_session_mode = "toggle"
                            self.toggle_active_press = True
                            if self.on_start:
                                threading.Thread(target=self.on_start, args=("toggle",), daemon=True).start()
                            return

                # 2. Push-to-Talk check (e.g. Windows+Alt held from idle)
                if self.mode_setting in ("both", "push_to_talk"):
                    if self.ptt_keys and self.ptt_keys.issubset(self.currently_pressed):
                        self.is_recording = True
                        self.current_session_mode = "ptt"
                        if self.on_start:
                            threading.Thread(target=self.on_start, args=("ptt",), daemon=True).start()
                        return

        elif event.event_type == "up":
            # If Push-to-Talk was recording and ANY of its combo keys is released, stop immediately!
            if self.is_recording and self.current_session_mode == "ptt":
                if name in self.ptt_keys:
                    self.is_recording = False
                    self.current_session_mode = None
                    if self.on_stop:
                        threading.Thread(target=self.on_stop, args=("ptt",), daemon=True).start()

            self.currently_pressed.discard(name)
            if self.toggle_keys and not self.toggle_keys.issubset(self.currently_pressed):
                if self.ptt_keys and not self.ptt_keys.issubset(self.currently_pressed):
                    self.toggle_active_press = False


# ---------------------------------------------------------------------------
# 4. Core Dictation Engine: Real-Time gRPC-Web Audio Streaming
# ---------------------------------------------------------------------------
class DictationDaemon:
    def __init__(self, pill_widget: FloatingPill = None):
        self.cfg = db.load_config()
        self.enable_sound = self.cfg.get("enable_sound", True)
        self.enable_context_cascade = self.cfg.get("enable_context_cascade", True)
        self.toggle_hotkey = self.cfg.get("toggle_hotkey", self.cfg.get("hotkey", "ctrl+shift+s"))
        self.ptt_hotkey = self.cfg.get("push_to_talk_hotkey", "ctrl+space")
        self.dictation_mode = self.cfg.get("dictation_mode", "both")

        self.port, self.csrf_token = discover_antigravity_server()
        self.headers = {
            "content-type": "application/grpc-web+json",
            "x-codeium-csrf-token": self.csrf_token,
            "x-grpc-web": "1"
        }
        self.base_url = f"https://127.0.0.1:{self.port}/exa.language_server_pb.LanguageServerService"

        self.pill = pill_widget
        self.is_recording = False
        self.session_lock = threading.Lock()

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

        self.input_rate = 48000
        self.channels = 2
        self.block_size = 1920
        self.start_time = 0.0
        self.enable_ai_polish = self.cfg.get("enable_ai_polish", False)
        self.ai_model = self.cfg.get("ai_model", "MODEL_PLACEHOLDER_M318")
        self.ai_system_prompt = self.cfg.get("ai_system_prompt", "")

        self.tray_icon = None
        self.input_stream = None

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
            mode=self.dictation_mode,
            on_start=self.start_session,
            on_stop=self.stop_session,
            on_mode_change=self.switch_to_hands_free_mode
        )

    def switch_to_hands_free_mode(self, mode="toggle"):
        play_sound("switch", self.enable_sound)
        self.update_tray_tooltip("AntigravityFlow: Recording (HANDS-FREE)... 🎙️")
        if self.pill:
            self.pill.root.after(0, lambda: self.pill.set_mode("toggle"))

    def _cascade_poller(self):
        while True:
            if not self.is_recording and self.enable_context_cascade:
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
            time.sleep(0.20)

    def start_session(self, mode="toggle"):
        with self.session_lock:
            if self.is_recording:
                return
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
            self.active_app_at_start = get_foreground_app()
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
            self.update_tray_tooltip(f"AntigravityFlow: Recording ({mode.upper()})... 🎙️")

            # Show Floating Pill Widget in recording mode with proper mode layout (PTT vs Hands-Free)
            if self.pill:
                self.pill.root.after(0, lambda: [
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
                        "continuous": True
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

    def stop_session(self, mode=None):
        with self.session_lock:
            if not self.is_recording:
                return

            # 1. Immediately switch pill visual cue to rotating processing animation!
            if self.pill:
                self.pill.root.after(0, lambda: self.pill.set_state("processing"))

            play_sound("stop", self.enable_sound)

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
            if self.session_id:
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
            # If stream was completely empty, OR duration > 4.5s and word count is suspiciously low
            # (indicating speech pauses caused Google Conformer to drop trailing sentences)
            needs_recovery = False
            if not raw_text and len(recorded_pcm) > 8000:
                needs_recovery = True
            elif duration >= 4.5 and len(recorded_pcm) > 144000:
                expected_min_words = int(duration * 0.7)
                if raw_words < expected_min_words:
                    needs_recovery = True

            if needs_recovery:
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

            final_text = format_transcript(raw_text, category=category) if raw_text else ""

            if final_text:
                if self.enable_ai_polish:
                    try:
                        self.update_tray_tooltip("AntigravityFlow: AI Polishing... 🤖")
                        polished = llm_refiner.polish_with_gemini(
                            final_text,
                            model=self.ai_model,
                            system_prompt=self.ai_system_prompt
                        )
                        if polished and polished.strip():
                            final_text = polished.strip()
                    except Exception as err:
                        print(f"[Daemon] AI Polish fallback: {err}")

                # Save dictation & encrypted audio recording
                db.save_dictation(
                    text=final_text,
                    duration=duration,
                    app_name=self.active_app_at_start,
                    pcm_bytes=recorded_pcm
                )
                # Paste entire formatted text at once at cursor location
                paste_all_at_once(final_text)

            # 10. Hide Floating Pill peacefully AFTER pasting is complete!
            if self.pill:
                self.pill.root.after(0, lambda: [self.pill.set_state("idle"), self.pill.hide()])

            self.update_tray_tooltip(f"AntigravityFlow: Idle ({self.toggle_hotkey.upper()} / {self.ptt_hotkey.upper()})")

    def cancel_session(self):
        with self.session_lock:
            if not self.is_recording:
                return
            self.stop_requested.set()
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

    def reload_config(self):
        self.cfg = db.load_config()
        self.toggle_hotkey = self.cfg.get("toggle_hotkey", self.cfg.get("hotkey", "windows+alt+h"))
        self.ptt_hotkey = self.cfg.get("push_to_talk_hotkey", "windows+alt")
        self.dictation_mode = self.cfg.get("dictation_mode", "both")
        self.enable_sound = self.cfg.get("enable_sound", True)
        self.enable_context_cascade = self.cfg.get("enable_context_cascade", True)
        self.enable_ai_polish = self.cfg.get("enable_ai_polish", False)
        self.ai_model = self.cfg.get("ai_model", "MODEL_PLACEHOLDER_M318")
        self.ai_system_prompt = self.cfg.get("ai_system_prompt", "")
        self.start_on_login = self.cfg.get("start_on_login", True)

        # Sync Windows Startup
        set_windows_autostart(self.start_on_login)

        try:
            self.port, self.csrf_token = discover_antigravity_server()
            self.headers = {
                "content-type": "application/grpc-web+json",
                "x-codeium-csrf-token": self.csrf_token,
                "x-grpc-web": "1"
            }
            self.base_url = f"https://127.0.0.1:{self.port}/exa.language_server_pb.LanguageServerService"
        except Exception:
            pass

        self.hotkey_mgr.update_hotkeys(
            toggle_hotkey=self.toggle_hotkey,
            ptt_hotkey=self.ptt_hotkey,
            mode=self.dictation_mode
        )
        self.update_tray_tooltip(f"AntigravityFlow: Idle ({self.toggle_hotkey.upper()} / {self.ptt_hotkey.upper()})")
        print(f"[Daemon] Configuration reloaded in real time (PTT: {self.ptt_hotkey.upper()}, Toggle: {self.toggle_hotkey.upper()})")

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
    if os.path.exists(ELECTRON_EXE):
        subprocess.Popen([ELECTRON_EXE, "."], cwd=UI_DIR)
    else:
        subprocess.Popen("npx electron .", shell=True, cwd=UI_DIR)

def main():
    pill = FloatingPill()
    pill.hide()

    daemon = DictationDaemon(pill_widget=pill)
    daemon.reload_config()

    # Pre-warm Wispr Flow dashboard in background for instant opening
    prewarm_ui()

    # Wire pill buttons
    pill.on_cancel = daemon.cancel_session
    pill.on_confirm = daemon.stop_session

    # Hook dual-mode keyboard listener
    keyboard.hook(daemon.hotkey_mgr.handle_event)

    def on_open_ui(icon, item):
        open_ui()

    def on_reload(icon, item):
        daemon.reload_config()

    def on_quit(icon, item):
        icon.stop()
        pill.root.after(0, pill.root.destroy)
        try:
            for p in psutil.process_iter(['name', 'cmdline']):
                cmd = ' '.join(p.info['cmdline'] or [])
                if 'electron' in (p.info['name'] or '').lower() and 'AntigravityFlow' in cmd:
                    p.kill()
        except Exception:
            pass
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("🎙️ AntigravityFlow", lambda: None, enabled=False),
        pystray.MenuItem(f"PTT: {daemon.ptt_hotkey.upper()} (Hold)", lambda: None, enabled=False),
        pystray.MenuItem(f"Toggle: {daemon.toggle_hotkey.upper()}", lambda: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("✨ Wispr Flow Dashboard", on_open_ui, default=True),
        pystray.MenuItem("🔄 Reload Config", on_reload),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("❌ Exit", on_quit)
    )

    daemon.tray_icon = pystray.Icon("AntigravityFlow", create_tray_image(), f"AntigravityFlow ({daemon.ptt_hotkey.upper()})", menu)
    daemon.tray_icon.run_detached()

    print(f"\n[+] AntigravityFlow Dual-Mode Engine Active")
    print(f"[>] Push-To-Talk: Hold [{daemon.ptt_hotkey.upper()}] to speak, release to paste.")
    print(f"[>] Hands-Free: Tap [{daemon.toggle_hotkey.upper()}] to start/stop.")

    pill.root.mainloop()

if __name__ == "__main__":
    main()
