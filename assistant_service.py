import os
import sys
import json
import time
import base64
import subprocess
import urllib.request
import ssl
import http.client
import ctypes
from ctypes import wintypes
import uuid
from PIL import Image, ImageGrab

if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    except Exception:
        pass
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')
    except Exception:
        pass
elif hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import db
import llm_refiner
import system_context
import prompts

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

ASSISTANT_SYSTEM_PROMPT = prompts.PROMPT_ASSISTANT_GENERAL

def capture_screen(save_path: str = None) -> dict:
    """Captures the primary monitor screen and returns base64 JPEG and resolution."""
    try:
        # Flash visual feedback on desktop
        try:
            import winsound
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            pass

        captured_box = []

        def _worker():
            try:
                user32 = ctypes.windll.user32
                gdi32 = ctypes.windll.gdi32

                user32.OpenDesktopW.restype = wintypes.HANDLE
                user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                user32.CloseDesktop.restype = wintypes.BOOL
                user32.CloseDesktop.argtypes = [wintypes.HANDLE]
                user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]

                desk_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
                if desk_default:
                    user32.SetThreadDesktop(desk_default)

                user32.GetDC.restype = wintypes.HDC
                user32.GetDC.argtypes = [wintypes.HWND]
                user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
                gdi32.CreateCompatibleDC.restype = wintypes.HDC
                gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
                gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
                gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
                gdi32.SelectObject.restype = wintypes.HGDIOBJ
                gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
                gdi32.BitBlt.restype = wintypes.BOOL
                gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
                gdi32.GetDIBits.restype = ctypes.c_int
                gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
                gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
                gdi32.DeleteDC.argtypes = [wintypes.HDC]

                w = user32.GetSystemMetrics(0)
                h = user32.GetSystemMetrics(1)

                hdc_screen = user32.GetDC(0)
                hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
                try:
                    gdi32.SelectObject(hdc_mem, hbmp)

                    # Dynamically exclude AntigravityFlow windows (pill and assistant) from the capture (< 1ms)
                    excluded_hwnds = []
                    try:
                        def enum_cb(hwnd, lparam):
                            if user32.IsWindowVisible(hwnd):
                                cls_buff = ctypes.create_unicode_buffer(256)
                                user32.GetClassNameW(hwnd, cls_buff, 256)
                                cls_name = cls_buff.value
                                length = user32.GetWindowTextLengthW(hwnd)
                                buff = ctypes.create_unicode_buffer(length + 1)
                                user32.GetWindowTextW(hwnd, buff, length + 1)
                                title = buff.value
                                if "TkTopLevel" in cls_name or "AntigravityFlow" in title or "AntigravityFlow" in cls_name:
                                    if user32.SetWindowDisplayAffinity(hwnd, 0x00000011):
                                        excluded_hwnds.append(hwnd)
                            return True
                        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
                        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
                    except Exception:
                        pass

                    # SRCCOPY (0x00CC0020) | CAPTUREBLT (0x40000000) = 0x40CC0020
                    gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, 0, 0, 0x40CC0020)

                    # Restore display affinity immediately so windows remain normal for regular desktop screen recording
                    for ehwnd in excluded_hwnds:
                        try:
                            user32.SetWindowDisplayAffinity(ehwnd, 0x00000000)
                        except Exception:
                            pass

                    class BITMAPINFOHEADER(ctypes.Structure):
                        _fields_ = [
                            ('biSize', wintypes.DWORD),
                            ('biWidth', wintypes.LONG),
                            ('biHeight', wintypes.LONG),
                            ('biPlanes', wintypes.WORD),
                            ('biBitCount', wintypes.WORD),
                            ('biCompression', wintypes.DWORD),
                            ('biSizeImage', wintypes.DWORD),
                            ('biXPelsPerMeter', wintypes.LONG),
                            ('biYPelsPerMeter', wintypes.LONG),
                            ('biClrUsed', wintypes.DWORD),
                            ('biClrImportant', wintypes.DWORD)
                        ]

                    bi = BITMAPINFOHEADER()
                    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                    bi.biWidth = w
                    bi.biHeight = -h
                    bi.biPlanes = 1
                    bi.biBitCount = 32
                    bi.biCompression = 0

                    buf = ctypes.create_string_buffer(w * h * 4)
                    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), 0)

                    raw_img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
                    captured_box.append(raw_img)
                finally:
                    if hbmp:
                        gdi32.DeleteObject(hbmp)
                    if hdc_mem:
                        gdi32.DeleteDC(hdc_mem)
                    if hdc_screen:
                        user32.ReleaseDC(0, hdc_screen)
                    if desk_default:
                        user32.CloseDesktop(desk_default)
            except Exception:
                pass

        import threading
        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=3.0)

        img = captured_box[0] if captured_box else None
        if img is None:
            try:
                img = ImageGrab.grab()
            except Exception:
                pass

        if img is None:
            return {"success": False, "error": "Screen capture failed"}

        # Native resolution (1080p pixel-perfect for crisp code, punctuation, and math).
        # Only clamp if display is 4K (>1920x1080) to avoid excessive file size.
        if img.width > 1920 or img.height > 1080:
            img.thumbnail((1920, 1080), Image.Resampling.BILINEAR)
        
        target_file = save_path or os.path.join(db.DATA_DIR, "latest_screenshot.jpg")
        img.save(target_file, "JPEG", quality=85)

        with open(target_file, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")

        return {
            "success": True,
            "width": img.width,
            "height": img.height,
            "file_path": target_file,
            "data_url": f"data:image/jpeg;base64,{b64}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def execute_system_command(command: str, shell: str = "powershell") -> dict:
    """Runs unrestricted PowerShell or CMD commands and returns stdout, stderr, and execution time.
    Automatically handles interactive GUI apps with Start-Process to prevent synchronous pipeline hangs."""
    start_time = time.time()
    clean_cmd = (command or "").strip()
    if not clean_cmd:
        return {"success": False, "stdout": "", "stderr": "Empty command", "exit_code": 1, "duration": 0.0, "command": ""}

    # Normalize interactive GUI binaries so they execute non-blocking
    gui_apps = (
        "notepad", "notepad.exe", "explorer", "explorer.exe", "code", "code.cmd", "code.exe",
        "calc", "calc.exe", "mspaint", "mspaint.exe", "taskmgr", "taskmgr.exe", "chrome", "msedge"
    )

    tokens = clean_cmd.split(None, 1)
    first_token = tokens[0].lower() if tokens else ""
    first_base = os.path.basename(first_token)

    if first_base in gui_apps and not clean_cmd.lower().startswith(("start-process", "start ", "& ")):
        arg = tokens[1].strip() if len(tokens) > 1 else ""
        if shell == "powershell":
            if arg:
                clean_cmd = f"Start-Process {first_token} -ArgumentList '{arg.replace(\"'\", \"''\")}'"
            else:
                clean_cmd = f"Start-Process {first_token}"
        else:
            clean_cmd = f"start \"\" {clean_cmd}"

    try:
        shell_cmd = ["powershell.exe", "-NoProfile", "-Command", clean_cmd] if shell == "powershell" else ["cmd.exe", "/c", clean_cmd]
        res = subprocess.run(shell_cmd, capture_output=True, text=True, timeout=15.0)
        dur = round(time.time() - start_time, 2)
        return {
            "success": res.returncode == 0,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
            "exit_code": res.returncode,
            "duration": dur,
            "command": clean_cmd
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Command timed out after 15 seconds.",
            "exit_code": -1,
            "duration": 15.0,
            "command": clean_cmd
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": 1,
            "duration": round(time.time() - start_time, 2),
            "command": clean_cmd
        }

def execute_mcp_tool(tool_name: str, arguments: dict = None) -> dict:
    """Executes a standard Model Context Protocol (MCP) tool securely and returns structured JSON."""
    args = arguments or {}
    start_time = time.time()
    
    try:
        if tool_name == "search_files":
            search_path = os.path.expandvars(os.path.expanduser(args.get("path", ".")))
            pattern = args.get("pattern", "*")
            max_results = int(args.get("max_results", 50))
            import fnmatch
            matches = []
            if os.path.exists(search_path):
                for root, dirs, files in os.walk(search_path):
                    for fname in files:
                        if fnmatch.fnmatch(fname.lower(), pattern.lower()):
                            full_p = os.path.join(root, fname)
                            try:
                                sz = os.path.getsize(full_p)
                                mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(full_p)))
                            except Exception:
                                sz = 0
                                mtime = ""
                            matches.append({
                                "name": fname,
                                "path": full_p,
                                "size_bytes": sz,
                                "modified": mtime
                            })
                            if len(matches) >= max_results:
                                break
                    if len(matches) >= max_results:
                        break
            return {
                "success": True,
                "tool": tool_name,
                "path": search_path,
                "pattern": pattern,
                "total_found": len(matches),
                "matches": matches,
                "duration": round(time.time() - start_time, 2)
            }
            
        elif tool_name == "list_directory":
            dir_path = os.path.expandvars(os.path.expanduser(args.get("path", ".")))
            recursive = bool(args.get("recursive", False))
            items = []
            if os.path.exists(dir_path) and os.path.isdir(dir_path):
                entries = os.scandir(dir_path)
                for entry in entries:
                    try:
                        is_dir = entry.is_dir()
                        sz = entry.stat().st_size if not is_dir else 0
                        items.append({
                            "name": entry.name,
                            "is_dir": is_dir,
                            "size_bytes": sz
                        })
                    except Exception:
                        pass
                    if len(items) >= 100:
                        break
            return {
                "success": True,
                "tool": tool_name,
                "path": dir_path,
                "count": len(items),
                "items": items,
                "duration": round(time.time() - start_time, 2)
            }

        elif tool_name == "read_file":
            fpath = os.path.expandvars(os.path.expanduser(args.get("path", "")))
            max_bytes = int(args.get("max_bytes", 50000))
            if not os.path.exists(fpath):
                return {"success": False, "tool": tool_name, "error": f"File not found: {fpath}"}
            size = os.path.getsize(fpath)
            with open(fpath, "rb") as f:
                raw_bytes = f.read(max_bytes)
            try:
                content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                content = raw_bytes.decode("latin-1", errors="replace")
            return {
                "success": True,
                "tool": tool_name,
                "path": fpath,
                "size_bytes": size,
                "truncated": size > max_bytes,
                "content": content,
                "duration": round(time.time() - start_time, 2)
            }

        elif tool_name == "write_file":
            fpath = os.path.expandvars(os.path.expanduser(args.get("path", "")))
            content = args.get("content", "")
            parent_dir = os.path.dirname(os.path.abspath(fpath))
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return {
                "success": True,
                "tool": tool_name,
                "path": fpath,
                "bytes_written": len(content.encode("utf-8")),
                "duration": round(time.time() - start_time, 2)
            }

        elif tool_name in ("open_file", "launch_app"):
            target = os.path.expandvars(os.path.expanduser(args.get("path") or args.get("app") or args.get("target", "")))
            if not target:
                return {"success": False, "tool": tool_name, "error": "Path or application name required"}
            res = execute_system_command(f"Start-Process '{target}'")
            return {
                "success": res["success"],
                "tool": tool_name,
                "target": target,
                "stdout": res["stdout"],
                "stderr": res["stderr"],
                "exit_code": res["exit_code"],
                "duration": res["duration"]
            }

        elif tool_name == "get_system_info":
            import platform
            sys_info = {
                "platform": platform.platform(),
                "processor": platform.processor(),
                "python_version": platform.python_version(),
                "user": os.environ.get("USERNAME", ""),
                "computer_name": os.environ.get("COMPUTERNAME", "")
            }
            try:
                import psutil
                mem = psutil.virtual_memory()
                disk = psutil.disk_usage('C:')
                sys_info["ram_total_gb"] = round(mem.total / (1024**3), 2)
                sys_info["ram_available_gb"] = round(mem.available / (1024**3), 2)
                sys_info["disk_c_free_gb"] = round(disk.free / (1024**3), 2)
                sys_info["cpu_percent"] = psutil.cpu_percent(interval=None)
            except Exception:
                pass
            return {
                "success": True,
                "tool": tool_name,
                "info": sys_info,
                "duration": round(time.time() - start_time, 2)
            }

        elif tool_name == "fetch_url":
            target_url = args.get("url", "")
            max_chars = int(args.get("max_chars", 10000))
            if not target_url.startswith(("http://", "https://")):
                return {"success": False, "tool": tool_name, "error": "Invalid URL protocol"}
            req = urllib.request.Request(target_url, headers={"User-Agent": "AntigravityFlowAssistant/2.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                raw_html = resp.read().decode("utf-8", errors="replace")
                import re
                clean = re.sub(r'<script[\s\S]*?</script>', '', raw_html, flags=re.I)
                clean = re.sub(r'<style[\s\S]*?</style>', '', clean, flags=re.I)
                clean = re.sub(r'<[^>]+>', ' ', clean)
                clean = re.sub(r'\s+', ' ', clean).strip()
            return {
                "success": True,
                "tool": tool_name,
                "url": target_url,
                "content": clean[:max_chars],
                "duration": round(time.time() - start_time, 2)
            }

        elif tool_name == "execute_command":
            cmd = args.get("command", "")
            shell = args.get("shell", "powershell")
            cmd_res = execute_system_command(cmd, shell=shell)
            return {
                "success": cmd_res["success"],
                "tool": tool_name,
                "command": cmd,
                "stdout": cmd_res["stdout"],
                "stderr": cmd_res["stderr"],
                "exit_code": cmd_res["exit_code"],
                "duration": cmd_res["duration"]
            }
        else:
            return {"success": False, "tool": tool_name, "error": f"Unknown MCP tool: {tool_name}"}
    except Exception as e:
        return {
            "success": False,
            "tool": tool_name,
            "error": str(e),
            "duration": round(time.time() - start_time, 2)
        }

def search_web_unified(query: str, max_results: int = 4) -> dict:
    """Searches DuckDuckGo Instant Answers and Wikipedia for real-time web citations."""
    if not query or not query.strip():
        return {"query": query, "results": []}

    clean_q = query.strip()
    # Strip common conversational preambles
    preambles = (
        "can you search the web for ", "can you search web for ", "can you search for ",
        "please search the web for ", "please search web for ", "please search for ",
        "search the web for ", "search web for ", "search the internet for ", "search for ",
        "find on the web ", "find online ", "look up on the web ", "look up online ",
        "google ", "look up ", "who is ", "what is ", "what are ", "tell me about "
    )
    for p in preambles:
        if clean_q.lower().startswith(p):
            clean_q = clean_q[len(p):].strip()
            break
    clean_q = clean_q.rstrip("?!. ")

    results = []

    # 1. DuckDuckGo Instant Answers
    try:
        import urllib.parse
        q_enc = urllib.parse.quote(clean_q)
        url = f"https://api.duckduckgo.com/?q={q_enc}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            abstract = data.get("AbstractText") or data.get("Abstract")
            abstract_url = data.get("AbstractURL")
            heading = data.get("Heading") or clean_q
            if abstract:
                results.append({
                    "title": heading,
                    "snippet": abstract,
                    "url": abstract_url or "https://duckduckgo.com",
                    "source": "DuckDuckGo"
                })
            for topic in data.get("RelatedTopics", [])[:3]:
                if isinstance(topic, dict) and "Text" in topic:
                    results.append({
                        "title": topic.get("FirstURL", "").split("/")[-1].replace("_", " ") or clean_q,
                        "snippet": topic["Text"],
                        "url": topic.get("FirstURL") or "https://duckduckgo.com",
                        "source": "Web Reference"
                    })
    except Exception:
        pass

    # 2. Wikipedia Search API
    try:
        import urllib.parse
        import re
        q_enc = urllib.parse.quote(clean_q)
        url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q_enc}&format=json&utf8=1"
        req = urllib.request.Request(url, headers={"User-Agent": "AntigravityFlowAssistant/2.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            items = data.get("query", {}).get("search", [])
            for item in items[:3]:
                title = item.get("title", "")
                snippet = re.sub(r'<[^>]+>', '', item.get("snippet", "")).strip()
                article_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                results.append({
                    "title": title,
                    "snippet": snippet,
                    "url": article_url,
                    "source": "Wikipedia"
                })
    except Exception:
        pass

    return {
        "success": True,
        "query": clean_q,
        "results": results[:max_results]
    }

def get_available_models_catalog() -> dict:
    """Returns normalized catalog of all 22 detected models with primary recommendations and thinking budgets."""
    catalog_path = os.path.join(db.DATA_DIR, "models_cache.json")
    raw_models = {}
    try:
        port, csrf_token = llm_refiner.discover_server()
        headers = {
            "content-type": "application/grpc-web+json",
            "x-codeium-csrf-token": csrf_token,
            "x-grpc-web": "1"
        }
        conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=2.5)
        frame = llm_refiner.make_grpc_web_frame(b"{}")
        conn.request("POST", "/exa.language_server_pb.LanguageServerService/GetAvailableModels", body=frame, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if res.status == 200 and len(raw) >= 5:
            l = int.from_bytes(raw[1:5], "big")
            d = json.loads(raw[5:5+l].decode("utf-8"))
            raw_models = d.get("response", {}).get("models", {})
    except Exception:
        pass

    if not raw_models:
        local_avail = os.path.join(BASE_DIR, "avail_models.json")
        if os.path.exists(local_avail):
            try:
                with open(local_avail, "r", encoding="utf-8") as f:
                    raw_models = json.load(f).get("response", {}).get("models", {})
            except Exception:
                pass

    def clean_name(key, info):
        overrides = {
            "gemini-2.5-flash": "Gemini 2.5 Flash",
            "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite",
            "gemini-3.1-flash-lite": "Gemini 3.1 Flash",
            "gemini-3.8-flash-high": "Gemini 3.8 Flash (High)",
            "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)",
            "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low)",
            "gemini-3.8-flash-tiered": "Gemini 3.8 Flash Tiered",
            "gemini-4-carbon-disabled": "Gemini 4 Carbon",
            "gemini-4-hydrogen-sonar": "Gemini 4 Hydrogen Sonar",
            "gemini-4-argon-disabled": "Gemini 4 Argon",
            "gemini-4-hydrogen-disabled": "Gemini 4 Hydrogen",
            "gemini-4-neon-disabled": "Gemini 4 Neon",
            "tab_flash_lite_preview": "Tab Flash Lite Preview",
            "tab_jump_flash_lite_preview": "Tab Jump Flash Lite Preview"
        }
        if key in overrides:
            return overrides[key]
        disp = info.get("displayName")
        if disp and disp != key:
            return disp
        parts = key.replace("-", " ").replace("_", " ").split()
        return " ".join(p.capitalize() for p in parts)

    primary_models = [
        {
            "key": "gemini-2.5-flash",
            "label": "Gemini 2.5 Flash",
            "modelId": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
            "badge": "⚡ Ultra Fast (~600ms)",
            "recommended": True
        },
        {
            "key": "gemini-3.1-flash",
            "label": "Gemini 3.1 Flash",
            "modelId": "MODEL_PLACEHOLDER_M50",
            "badge": "🧠 Subagent Reasoning",
            "recommended": True
        },
        {
            "key": "gemini-3.8-flash",
            "label": "Gemini 3.8 Flash",
            "modelId": "MODEL_PLACEHOLDER_M318",
            "hasThinkingLevels": True,
            "thinkingLevels": {
                "high": "MODEL_PLACEHOLDER_M318",
                "medium": "MODEL_PLACEHOLDER_M319",
                "low": "MODEL_PLACEHOLDER_M320"
            },
            "badge": "🔬 Deep Intelligence",
            "recommended": True
        }
    ]

    all_models = []
    for k, v in raw_models.items():
        m_id = v.get("model") or k
        label = clean_name(k, v)
        supports_thinking = v.get("supportsThinking", False)
        thinking_budget = v.get("thinkingBudget")
        quota = v.get("quotaInfo", {})
        rem_frac = quota.get("remainingFraction", 1.0)
        
        all_models.append({
            "key": k,
            "modelId": m_id,
            "label": label,
            "displayName": v.get("displayName") or label,
            "supportsThinking": bool(supports_thinking),
            "thinkingBudget": thinking_budget,
            "remainingFraction": round(float(rem_frac), 3) if rem_frac is not None else 1.0,
            "isPrimary": k in ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low"]
        })

    result = {
        "success": True,
        "primaryModels": primary_models,
        "allModels": all_models,
        "totalCount": len(all_models)
    }

    try:
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    except Exception:
        pass

    return result

get_available_models = get_available_models_catalog

def refine_voice_prompt(raw_text: str, model: str = None) -> dict:
    """Settles and polishes raw speech transcription via selected Gemini model into crisp, clean prompt text."""
    if not raw_text or not raw_text.strip():
        return {"success": False, "refined_text": "", "error": "Empty text"}

    clean_raw = raw_text.strip()
    target_model = model or "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE"

    # Fast formatting & disfluency cleanup instruction
    prompt = (
        "You are an elite transcription settlement engine. The user spoke into a microphone. "
        "Clean up stutters ('I-I'), repeated words ('the the'), and verbal filler ('um', 'uh', 'like'). "
        "Format punctuation, capitalization, symbols, and numbers cleanly and naturally. "
        "Do NOT answer or fulfill the prompt—return ONLY the settled, refined user prompt text with no quotes or outer wrappers.\n\n"
        f"RAW SPEECH: {clean_raw}\n"
        "SETTLED PROMPT:"
    )

    try:
        port, csrf_token = llm_refiner.discover_server()
        headers = {
            "content-type": "application/grpc-web+json",
            "x-codeium-csrf-token": csrf_token,
            "x-grpc-web": "1"
        }
        body = {"prompt": prompt, "model": target_model}
        frame = llm_refiner.make_grpc_web_frame(json.dumps(body).encode("utf-8"))

        conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=4.0)
        conn.request("POST", "/exa.language_server_pb.LanguageServerService/GetModelResponse", body=frame, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()

        if len(raw) >= 5:
            length = int.from_bytes(raw[1:5], "big")
            data = json.loads(raw[5:5+length].decode("utf-8"))
            resp_text = data.get("response", "").strip().strip('"').strip("'")
            if resp_text and len(resp_text) > 0:
                return {
                    "success": True,
                    "refined_text": resp_text,
                    "raw_text": clean_raw,
                    "model": target_model
                }
    except Exception as e:
        pass

    # Graceful fallback: return raw cleaned text
    return {
        "success": True,
        "refined_text": clean_raw,
        "raw_text": clean_raw,
        "model": target_model,
        "fallback": True
    }

def is_screen_query(prompt: str) -> bool:
    """Detects whether a user prompt is requesting to analyze or solve something on their screen."""
    if not prompt or not prompt.strip():
        return False
    lower = prompt.lower()
    screen_triggers = (
        "on my screen", "on the screen", "on screen", "this screen", "my screen",
        "what is happening", "what's happening", "what is on", "what's on",
        "solve this question", "solve the question", "solve this problem", "solve the problem",
        "solve question", "solve problem", "look at my screen", "look at the screen",
        "look at screen", "read my screen", "read screen", "check my screen", "check screen",
        "what's on my screen", "what is on my screen", "what is on the screen",
        "what's on screen", "see my screen", "help with this question", "solve this",
        "analyze my screen", "inspect screen", "what question", "solve code on screen",
        "solve the code on my screen", "read what is on screen", "question on my screen",
        "problem on my screen", "error on my screen", "fix my screen"
    )
    return any(t in lower for t in screen_triggers)

def extract_screen_intelligence(screenshot_path: str = None) -> dict:
    """Extracts active application metadata, focused UI element,
    and text field contents from the user's active screen without OCR overhead."""
    target_img = screenshot_path or os.path.join(db.DATA_DIR, "latest_screenshot.jpg")

    sys_ctx = {}
    try:
        sys_ctx = system_context.get_active_system_context(
            include_window=True,
            include_cursor=True,
            include_focused=True,
            include_field=True,
            max_field_words=1000
        )
    except Exception as e:
        print(f"[Assistant] System context notice: {e}")

    field_content = sys_ctx.get("field_content", "")
    lines_count = len(field_content.splitlines()) if field_content else 0
    words_count = len(field_content.split()) if field_content else 0

    return {
        "app_name": sys_ctx.get("app_name", "Desktop"),
        "window_title": sys_ctx.get("window_title", ""),
        "focused_element": sys_ctx.get("focused_element", ""),
        "field_content": field_content,
        "lines_count": lines_count,
        "words_count": words_count,
        "screenshot_path": target_img if os.path.exists(target_img) else None
    }

MODEL_KEY_TO_ID = {
    "gemini-2.5-flash": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    "gemini-2.5-flash-lite": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE",
    "gemini-3.1-flash": "MODEL_PLACEHOLDER_M50",
    "gemini-3.1-flash-lite": "MODEL_PLACEHOLDER_M50",
    "gemini-3.8-flash": "MODEL_PLACEHOLDER_M318",
    "gemini-3.8-flash-high": "MODEL_PLACEHOLDER_M318",
    "gemini-3.8-flash-medium": "MODEL_PLACEHOLDER_M319",
    "gemini-3.8-flash-low": "MODEL_PLACEHOLDER_M320",
}

def call_language_server_rpc(method: str, payload: dict, timeout: float = 35.0) -> dict:
    """Unified Connect-RPC / gRPC-web client for Codeium / Antigravity Language Server."""
    try:
        port, csrf_token = llm_refiner.discover_server()
        headers = {
            "content-type": "application/grpc-web+json",
            "x-codeium-csrf-token": csrf_token,
            "x-grpc-web": "1"
        }
        body = json.dumps(payload).encode("utf-8")
        frame = bytes([0]) + len(body).to_bytes(4, "big") + body
        conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=timeout)
        conn.request("POST", f"/exa.language_server_pb.LanguageServerService/{method}", body=frame, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if len(raw) >= 5:
            length = int.from_bytes(raw[1:5], "big")
            return json.loads(raw[5:5+length].decode("utf-8"))
    except Exception as e:
        print(f"[LanguageServer] RPC error for {method}: {e}")
    return {}

def stage_media_artifact(b64_data: str, mime_type: str = "image/jpeg", display_name: str = "screen.jpg") -> str:
    """Stages an image base64 buffer into tempmediaStorage artifact and returns local URI."""
    try:
        res = call_language_server_rpc("SaveMediaAsArtifact", {
            "media": {
                "mimeType": mime_type,
                "inlineData": b64_data,
                "displayName": display_name
            }
        }, timeout=15.0)
        return res.get("uri") or ""
    except Exception as e:
        print(f"[Assistant] Error staging media artifact: {e}")
        return ""

def call_model_with_cascade(prompt_text: str, current_model: str, media_items: list = None, timeout_seconds: float = 35.0) -> tuple:
    """Invokes LanguageServer via StartCascade + SendUserCascadeMessage,
    capturing both the response content and the thinking process (for reasoning models).
    Gracefully falls back to unary GetModelResponse and base Flash model if needed."""
    resolved_model = MODEL_KEY_TO_ID.get(current_model, current_model)
    cid = str(uuid.uuid4())
    cascade_failed = False

    try:
        start_res = call_language_server_rpc("StartCascade", {
            "source": "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT",
            "cascadeId": cid,
            "requestedModel": resolved_model,
            "projectEnvConfig": {"projectId": "outside-of-project", "defaultProjectEnvironment": {}}
        }, timeout=8.0)

        if start_res and "cascadeId" in start_res:
            msg_payload = {
                "cascadeId": cid,
                "items": [{"text": prompt_text}],
                "customAgentSpec": {
                    "builtinAgent": {
                        "defaultAgent": {
                            "isGoogle": False,
                            "isInteractive": True
                        }
                    }
                },
                "cascadeConfig": {
                    "plannerConfig": {
                        "planModel": resolved_model,
                        "useAiCredits": False
                    }
                },
                "deliveryStrategy": "MESSAGE_DELIVERY_STRATEGY_IMMEDIATE"
            }
            if media_items:
                msg_payload["media"] = media_items

            call_language_server_rpc("SendUserCascadeMessage", msg_payload, timeout=8.0)

            start_poll = time.time()
            while time.time() - start_poll < timeout_seconds:
                time.sleep(0.35)
                steps_data = call_language_server_rpc("GetCascadeTrajectorySteps", {"cascadeId": cid}, timeout=8.0)
                for s in steps_data.get("steps", []):
                    stype = s.get("type")
                    status = s.get("status")
                    if stype in ("CORTEX_STEP_TYPE_PLANNER_RESPONSE", "CORTEX_STEP_TYPE_MODEL_RESPONSE"):
                        if status == "CORTEX_STEP_STATUS_DONE":
                            pr = s.get("plannerResponse") or s.get("modelResponse") or {}
                            resp_text = (pr.get("response") or pr.get("modifiedResponse") or s.get("content") or "").strip()
                            thinking_text = (pr.get("thinking") or "").strip()
                            if resp_text:
                                return resp_text, thinking_text
                        elif status == "CORTEX_STEP_STATUS_ERROR":
                            print(f"[Assistant] Cascade step status error: {status}")
                            cascade_failed = True
                            break
                if cascade_failed:
                    break
    except Exception as e:
        print(f"[Assistant] Cascade execution notice: {e}")

    # Fallback 1: Unary GetModelResponse with resolved_model
    try:
        body = {"prompt": prompt_text, "model": resolved_model}
        res_data = call_language_server_rpc("GetModelResponse", body, timeout=15.0)
        resp = (res_data.get("response") or "").strip()
        if resp:
            return resp, ""
    except Exception as e:
        print(f"[Assistant] Fallback unary execution notice: {e}")

    # Fallback 2: Unary GetModelResponse with base Gemini 2.5 Flash
    if resolved_model != "MODEL_GOOGLE_GEMINI_2_5_FLASH":
        try:
            body = {"prompt": prompt_text, "model": "MODEL_GOOGLE_GEMINI_2_5_FLASH"}
            res_data = call_language_server_rpc("GetModelResponse", body, timeout=12.0)
            resp = (res_data.get("response") or "").strip()
            if resp:
                return resp, ""
        except Exception:
            pass

    return "", ""

def query_gemini_assistant(
    prompt: str,
    history: list = None,
    model: str = None,
    system_instruction: str = None,
    screenshot_info: dict = None,
    enable_web_search: bool = False
) -> dict:
    """Queries Gemini via local Language Server with multi-turn memory, system prompt, vision, web citations,
    and autonomous multi-step execution loop for system/file inspections."""
    if not prompt or not prompt.strip():
        return {"success": False, "error": "Empty prompt"}

    clean_prompt = prompt.strip()

    # Load persistent AI assistant configuration defaults from SQLite / config.json
    cfg = db.load_config() if hasattr(db, "load_config") else {}
    target_model = model or cfg.get("ai_assistant_model") or "MODEL_GOOGLE_GEMINI_2_5_FLASH"

    if not system_instruction:
        mode = cfg.get("ai_assistant_mode", "general")
        custom_prompt = cfg.get("ai_assistant_custom_prompt", "")
        instruction = prompts.get_assistant_preset_prompt(mode, custom_prompt)
    else:
        instruction = system_instruction

    # Check for direct system command trigger (e.g. prompt starts with '$ ' or '> ' or 'run: ')
    is_direct_command = False
    cmd_to_run = ""
    for prefix in ("$ ", "> ", "run: ", "powershell: ", "cmd: "):
        if clean_prompt.lower().startswith(prefix):
            is_direct_command = True
            cmd_to_run = clean_prompt[len(prefix):].strip()
            break

    if is_direct_command and cmd_to_run:
        cmd_result = execute_system_command(cmd_to_run)
        output_body = (
            f"```powershell\n$ {cmd_to_run}\n"
            f"{cmd_result['stdout'] if cmd_result['stdout'] else (cmd_result['stderr'] or '(Command finished with no output)')}\n"
            f"```\n"
            f"*(Execution time: {cmd_result['duration']}s • Exit Code: {cmd_result['exit_code']})*"
        )
        return {
            "success": True,
            "response": output_body,
            "is_system_command": True,
            "command_result": cmd_result,
            "steps": []
        }

    # Auto-detect screen analysis & question-solving intent
    is_screen_req = False
    if screenshot_info and (screenshot_info.get("file_path") or screenshot_info.get("data_url")):
        is_screen_req = True
    elif is_screen_query(clean_prompt):
        is_screen_req = True

    # 1. Resolve raw image bytes and file path for multimodal vision & OCR
    media_items = []
    raw_b64 = None
    snap_path = None

    if screenshot_info:
        if screenshot_info.get("data_url") and "," in screenshot_info["data_url"]:
            raw_b64 = screenshot_info["data_url"].split(",", 1)[-1]
            try:
                snap_path = os.path.join(db.DATA_DIR, "latest_screenshot.jpg")
                raw_bytes = base64.b64decode(raw_b64)
                with open(snap_path, "wb") as f:
                    f.write(raw_bytes)
            except Exception as b64_err:
                print(f"[Assistant] Error saving data_url screenshot: {b64_err}")
        elif screenshot_info.get("file_path") and os.path.exists(screenshot_info["file_path"]):
            snap_path = screenshot_info["file_path"]
            try:
                with open(snap_path, "rb") as f:
                    raw_b64 = base64.b64encode(f.read()).decode("ascii")
            except Exception:
                pass

    if not raw_b64 and is_screen_req:
        default_snap = os.path.join(db.DATA_DIR, "latest_screenshot.jpg")
        if os.path.exists(default_snap):
            snap_path = default_snap
        else:
            # Capture screen directly if not already on disk
            captured = capture_screen()
            if captured.get("success"):
                snap_path = captured.get("file_path")
        if snap_path and os.path.exists(snap_path):
            try:
                with open(snap_path, "rb") as f:
                    raw_b64 = base64.b64encode(f.read()).decode("ascii")
            except Exception:
                pass

    # 2. Extract OCR text and stage multimodal artifact via Language Server
    ocr_extracted_text = ""
    if raw_b64:
        try:
            import io
            img_bytes = base64.b64decode(raw_b64)
            pil_img = Image.open(io.BytesIO(img_bytes))
            try:
                import winocr
                ocr_res = winocr.recognize_pil_sync(pil_img)
                if ocr_res and isinstance(ocr_res, dict) and ocr_res.get("text"):
                    ocr_extracted_text = ocr_res["text"].strip()
            except Exception as ocr_err:
                print(f"[Assistant] winocr notice: {ocr_err}")
        except Exception as img_err:
            print(f"[Assistant] Image decoding notice: {img_err}")

        art_uri = stage_media_artifact(raw_b64, mime_type="image/jpeg", display_name="user_screenshot.jpg")
        if art_uri:
            media_items.append({"mimeType": "image/jpeg", "uri": art_uri})

    # 3. Screen intelligence (foreground application & active control)
    screen_intel = None
    if is_screen_req:
        screen_intel = extract_screen_intelligence(snap_path)

    # Auto-detect or honor web search request (strictly when needed)
    web_citations = []
    should_search = False
    lower_p = clean_prompt.lower()
    web_search_triggers = (
        "search web", "search the web", "search internet", "search the internet",
        "google ", "latest news", "who won", "current price", "weather in", "stock price",
        "recent news", "what happened today", "live score", "news on", "breakthroughs in"
    )
    # Only search if user explicitly requested search, and NEVER for screen analysis or code questions
    if not is_screen_req:
        if enable_web_search or any(k in lower_p for k in web_search_triggers):
            should_search = True

    if should_search:
        search_res = search_web_unified(clean_prompt)
        web_citations = search_res.get("results", [])

    # Build prompt envelope with history, vision intelligence, web citations & MCP instructions
    mcp_instruction = (
        "\n--- MODEL CONTEXT PROTOCOL (MCP) & SYSTEM TOOL CAPABILITIES ---\n"
        "You have direct access to deterministic system tools via MCP. When you need to inspect files, search directories, "
        "read or write files, open apps, check system status, or run commands, output either a ```call_mcp block with JSON arguments, or a ```execute_powershell block:\n"
        "1. Search Files: ```call_mcp\n{\"tool\": \"search_files\", \"arguments\": {\"path\": \"...\", \"pattern\": \"*.pdf\"}}\n```\n"
        "2. List Directory: ```call_mcp\n{\"tool\": \"list_directory\", \"arguments\": {\"path\": \"...\"}}\n```\n"
        "3. Read File: ```call_mcp\n{\"tool\": \"read_file\", \"arguments\": {\"path\": \"...\", \"max_bytes\": 50000}}\n```\n"
        "4. Write File: ```call_mcp\n{\"tool\": \"write_file\", \"arguments\": {\"path\": \"...\", \"content\": \"...\"}}\n```\n"
        "5. Launch App / Open File: ```call_mcp\n{\"tool\": \"launch_app\", \"arguments\": {\"path\": \"notepad.exe\"}}\n```\n"
        "6. System Info: ```call_mcp\n{\"tool\": \"get_system_info\", \"arguments\": {}}\n```\n"
        "7. Fetch URL: ```call_mcp\n{\"tool\": \"fetch_url\", \"arguments\": {\"url\": \"https://...\"}}\n```\n"
        "8. Terminal / PowerShell: ```execute_powershell\n<command>\n```\n"
        "Output ONLY the tool block when an action or tool is required. The system will execute it and return the verified result for your next turn.\n"
        "--- END MCP CAPABILITIES ---\n"
    )
    envelope_parts = [instruction.strip(), mcp_instruction, "\n"]

    if history and len(history) > 0:
        envelope_parts.append("\n--- CONVERSATION HISTORY ---")
        for turn in history[-8:]:  # Keep past 8 turns for instant recall
            role = turn.get("role", "user").upper()
            content = turn.get("content", "")
            envelope_parts.append(f"{role}: {content}")
        envelope_parts.append("--- END CONVERSATION HISTORY ---\n")

    if web_citations and len(web_citations) > 0:
        envelope_parts.append("\n--- REAL-TIME WEB SEARCH INTELLIGENCE ---")
        for i, c in enumerate(web_citations, 1):
            envelope_parts.append(f"[{i}] {c['title']} ({c.get('source', 'Web')})\nSummary: {c['snippet']}\nURL: {c['url']}")
        envelope_parts.append("--- END WEB SEARCH INTELLIGENCE ---")
        envelope_parts.append("INSTRUCTION: Use the verified real-time web intelligence above to provide an accurate, up-to-date answer with citations [1], [2] where relevant.\n")

    if screen_intel:
        envelope_parts.append("\n--- LIVE SCREEN VISION & CONTEXT INTELLIGENCE ---")
        envelope_parts.append(f"Active Foreground Application: {screen_intel['app_name']}")
        envelope_parts.append(f"Active Window Title: {screen_intel['window_title'] or 'Main Workspace'}")
        if screen_intel.get("focused_element"):
            envelope_parts.append(f"Focused UI Control: {screen_intel['focused_element']}")

        if screen_intel.get("field_content"):
            envelope_parts.append(f"\nACTIVE EDITOR / TEXT FIELD CONTENT:\n\"\"\"\n{screen_intel['field_content']}\n\"\"\"")
        envelope_parts.append("\n[NATIVE SCREENSHOT ATTACHED: High-resolution pixel-perfect screen capture is available. Read all text, code, questions, and UI elements directly from the image.]")
        envelope_parts.append("--- END LIVE SCREEN INTELLIGENCE ---\n")

    if ocr_extracted_text:
        envelope_parts.append(
            "\n--- LIVE SCREEN / DOCUMENT / PDF OCR EXTRACTED TEXT ---\n"
            f"{ocr_extracted_text}\n"
            "--- END EXTRACTED OCR TEXT ---\n"
            "NOTE: The exact raw text, questions, code, and symbols above were extracted directly from the attached screen/document using Windows OCR engine. "
            "Use both this extracted text and the high-resolution visual image to answer with 100% accuracy.\n"
        )

    if screen_intel or ocr_extracted_text or media_items:
        envelope_parts.append(
            "MANDATORY INSTRUCTIONS FOR SCREEN QUESTION SOLVING & ANALYSIS:\n"
            "1. **Analyze What is Happening**: Summarize what is currently displayed on the user's screen (the application, open question/test, document, or code environment).\n"
            "2. **Identify Questions or Issues**: Identify and transcribe the exact question(s), problem statements, multiple-choice options (A, B, C, D), test cases, or errors shown on screen.\n"
            "3. **Autonomous Tool Execution (If Needed)**: If solving the problem, testing code, calculating results, or investigating files requires executing PowerShell commands or MCP tools, output the tool block.\n"
            "   The system will automatically execute it and return the output before you finalize your answer.\n"
            "4. **Deliver Step-by-Step Solution & Definitive Answer**:\n"
            "   - Walk through the mathematical/logical/programming reasoning step-by-step.\n"
            "   - State the definitive answer clearly (e.g. **Correct Answer: (Option ...)** or provide the fixed code block).\n"
            "   - Conclude with an analysis of what actions were taken and why.\n"
        )
    elif screenshot_info and screenshot_info.get("file_path"):
        envelope_parts.append(
            f"[SCREENSHOT ATTACHED: User took a screenshot of their active screen ({screenshot_info.get('width')}x{screenshot_info.get('height')}). "
            f"Screen context indicates active desktop workflow.]"
        )

    envelope_parts.append(f"USER: {clean_prompt}")
    envelope_parts.append("ASSISTANT:")
    full_prompt = "\n".join(envelope_parts)

    try:
        resp_text, thinking_text = call_model_with_cascade(full_prompt, target_model, media_items=media_items)
        if not resp_text:
            return {"success": False, "error": "Empty model response"}

        # Autonomous Multi-Step Execution Loop (up to 6 sequential steps)
        steps_executed = []
        current_resp = resp_text
        current_ctx_prompt = full_prompt

        import re
        action_verbs = ("create", "make", "write", "open", "launch", "run", "execute", "delete", "remove", "save", "start", "kill", "search", "list", "check", "find", "count")
        user_wants_action = any(re.search(r'\b' + v + r'\b', clean_prompt.lower()) for v in action_verbs)

        for step_idx in range(1, 7):
            is_tool_call = False
            tool_type = ""
            tool_name = ""
            tool_args = {}
            cmd = ""

            # 1. Check for MCP call: ```call_mcp ... ``` or ```mcp ... ```
            mcp_match = re.search(r"```(?:call_mcp|mcp)\s*([\s\S]*?)\s*```", current_resp, re.IGNORECASE)
            if mcp_match:
                raw_json = mcp_match.group(1).strip()
                try:
                    payload = json.loads(raw_json)
                    tool_name = payload.get("tool", "")
                    tool_args = payload.get("arguments", {})
                    if tool_name:
                        is_tool_call = True
                        tool_type = "mcp"
                except Exception:
                    pass

            # 2. Check for execute_powershell / execute_command / execute_cmd call
            if not is_tool_call:
                exec_match = re.search(r"```(?:execute_powershell|execute_command|execute_cmd|run_command)\s*([\s\S]*?)\s*```", current_resp, re.IGNORECASE)
                if exec_match:
                    candidate_cmd = exec_match.group(1).strip()
                    if candidate_cmd:
                        is_tool_call = True
                        tool_type = "powershell"
                        cmd = candidate_cmd

            # 3. Check for actionable ```powershell, ```pwsh, ```cmd, or ```bash blocks
            if not is_tool_call:
                ps_match = re.search(r"```(?:powershell|pwsh|cmd|bash|bat)\s*([\s\S]*?)\s*```", current_resp, re.IGNORECASE)
                if ps_match:
                    candidate_cmd = ps_match.group(1).strip()
                    action_triggers = (
                        "new-item", "set-content", "add-content", "start-process", "get-childitem", "get-content",
                        "remove-item", "get-process", "stop-process", "notepad", "explorer", "echo ", "mkdir ", "dir ",
                        "type ", "cat ", "ls ", "python ", "pip ", "curl ", "invoke-webrequest"
                    )
                    candidate_lower = candidate_cmd.lower()
                    if user_wants_action or any(t in candidate_lower for t in action_triggers):
                        is_tool_call = True
                        tool_type = "powershell"
                        cmd = candidate_cmd

            # 4. Anti-Hallucination Guardrail:
            # If no tool call detected on step 1, but user requested an action, and model claimed it did it
            if not is_tool_call and step_idx == 1 and user_wants_action and len(steps_executed) == 0:
                claim_patterns = [
                    r"\bi (have|already) (created|made|written|opened|launched|executed|saved|deleted|started)\b",
                    r"\bi've (created|made|written|opened|launched|executed|saved|deleted|started)\b",
                    r"\b(created|saved|written) the file\b",
                    r"\bopened (notepad|file explorer|explorer|the application)\b"
                ]
                has_hallucinated_claim = any(re.search(pat, current_resp.lower()) for pat in claim_patterns)
                if has_hallucinated_claim:
                    nudge_prompt = (
                        f"{current_ctx_prompt}\n\n"
                        f"ASSISTANT: {current_resp}\n\n"
                        f"[SYSTEM ENFORCEMENT - ANTI-HALLUCINATION GUARDRAIL]\n"
                        f"You stated that you performed an action, but you did NOT execute any tool block. "
                        f"In AntigravityFlow, you MUST execute the action first using ```execute_powershell or ```call_mcp. "
                        f"Emit ONLY the required tool block now to perform the user's requested action.\n"
                        f"ASSISTANT:"
                    )
                    nudge_resp, nudge_thinking = call_model_with_cascade(nudge_prompt, target_model)
                    if nudge_resp:
                        current_resp = nudge_resp
                        if nudge_thinking and not thinking_text:
                            thinking_text = nudge_thinking

                        mcp_match2 = re.search(r"```(?:call_mcp|mcp)\s*([\s\S]*?)\s*```", current_resp, re.IGNORECASE)
                        if mcp_match2:
                            try:
                                payload = json.loads(mcp_match2.group(1).strip())
                                tool_name = payload.get("tool", "")
                                tool_args = payload.get("arguments", {})
                                if tool_name:
                                    is_tool_call = True
                                    tool_type = "mcp"
                            except Exception:
                                pass
                        if not is_tool_call:
                            exec_match2 = re.search(r"```(?:execute_powershell|execute_command|powershell|pwsh|cmd)\s*([\s\S]*?)\s*```", current_resp, re.IGNORECASE)
                            if exec_match2:
                                cmd = exec_match2.group(1).strip()
                                if cmd:
                                    is_tool_call = True
                                    tool_type = "powershell"

            if not is_tool_call:
                break

            step_record = {"step": step_idx, "type": tool_type}

            if tool_type == "mcp":
                mcp_res = execute_mcp_tool(tool_name, tool_args)
                step_record.update({
                    "tool": tool_name,
                    "arguments": tool_args,
                    "command": f"mcp:{tool_name}({json.dumps(tool_args)})",
                    "stdout": json.dumps(mcp_res.get("matches") or mcp_res.get("items") or mcp_res.get("content") or mcp_res.get("info") or mcp_res, indent=2) if mcp_res.get("success") else "",
                    "stderr": mcp_res.get("error", "") if not mcp_res.get("success") else "",
                    "exit_code": 0 if mcp_res.get("success") else 1,
                    "duration": mcp_res.get("duration", 0.0)
                })
                steps_executed.append(step_record)

                formatted_result = json.dumps(mcp_res, indent=2)
                follow_up_prompt = (
                    f"{current_ctx_prompt}\n\n"
                    f"ASSISTANT:\n```call_mcp\n{json.dumps({'tool': tool_name, 'arguments': tool_args}, indent=2)}\n```\n\n"
                    f"[MCP TOOL RESULT FOR STEP {step_idx} - {tool_name}]\n"
                    f"{formatted_result}\n\n"
                    f"INSTRUCTION: Synthesize the final formatted answer for the user using the tool output above. If another tool is required to complete the task, output another ```call_mcp or ```execute_powershell block.\n"
                    f"ASSISTANT:"
                )
            else:
                exec_res = execute_system_command(cmd)
                step_record.update({
                    "command": cmd,
                    "stdout": exec_res["stdout"],
                    "stderr": exec_res["stderr"],
                    "exit_code": exec_res["exit_code"],
                    "duration": exec_res["duration"]
                })
                steps_executed.append(step_record)

                err_msg = f"Error: {exec_res['stderr']}\n" if exec_res.get("stderr") else ""
                follow_up_prompt = (
                    f"{current_ctx_prompt}\n\n"
                    f"ASSISTANT:\n```execute_powershell\n{cmd}\n```\n\n"
                    f"[SYSTEM TOOL EXECUTION RESULT FOR STEP {step_idx}]\n"
                    f"Command: {cmd}\n"
                    f"Output:\n{exec_res['stdout'] or '(Command executed with no stdout)'}\n"
                    f"{err_msg}"
                    f"Exit Code: {exec_res['exit_code']} (Duration: {exec_res['duration']}s)\n\n"
                    f"INSTRUCTION: Synthesize the final formatted answer for the user using the tool output above. If another command or MCP tool is required, output the tool block.\n"
                    f"ASSISTANT:"
                )

            next_resp, next_thinking = call_model_with_cascade(follow_up_prompt, target_model)
            if not next_resp:
                break
            current_resp = next_resp
            if next_thinking and not thinking_text:
                thinking_text = next_thinking
            current_ctx_prompt = follow_up_prompt

        return {
            "success": True,
            "response": current_resp.strip('"').strip("'"),
            "thinking": thinking_text,
            "model": target_model,
            "web_search_active": len(web_citations) > 0,
            "citations": web_citations,
            "steps": steps_executed,
            "screen_analysis_active": bool(screen_intel or media_items),
            "screen_intel": {
                "app_name": screen_intel.get("app_name", ""),
                "window_title": screen_intel.get("window_title", ""),
                "lines_count": screen_intel.get("lines_count", 0),
                "words_count": screen_intel.get("words_count", 0)
            } if screen_intel else None
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def paste_to_active_window(text: str) -> dict:
    """Pastes the specified text directly into the foreground window using private clipboard."""
    try:
        system_context.paste_cleanly(text)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "query":
            raw_input = sys.argv[2] if len(sys.argv) > 2 else "{}"
            try:
                params = json.loads(raw_input)
            except Exception:
                params = {"prompt": raw_input}
            result = query_gemini_assistant(
                prompt=params.get("prompt") or params.get("message", ""),
                history=params.get("history", []),
                model=params.get("model"),
                system_instruction=params.get("system_instruction") or params.get("system_prompt"),
                screenshot_info=params.get("screenshot_info"),
                enable_web_search=params.get("enable_web_search") or params.get("web_search", False)
            )
            print(json.dumps(result, ensure_ascii=False))

        elif cmd == "models":
            result = get_available_models_catalog()
            print(json.dumps(result, ensure_ascii=False))

        elif cmd == "refine":
            raw_input = sys.argv[2] if len(sys.argv) > 2 else "{}"
            try:
                params = json.loads(raw_input)
            except Exception:
                params = {"text": raw_input}
            result = refine_voice_prompt(
                raw_text=params.get("text", ""),
                model=params.get("model")
            )
            print(json.dumps(result, ensure_ascii=False))

        elif cmd == "search":
            raw_input = sys.argv[2] if len(sys.argv) > 2 else "{}"
            try:
                params = json.loads(raw_input)
            except Exception:
                params = {"query": raw_input}
            result = search_web_unified(params.get("query", ""))
            print(json.dumps(result, ensure_ascii=False))

        elif cmd == "exec":
            raw_input = sys.argv[2] if len(sys.argv) > 2 else "{}"
            try:
                params = json.loads(raw_input)
                command_str = params.get("command", "")
                shell_type = params.get("shell", "powershell")
            except Exception:
                command_str = raw_input
                shell_type = "powershell"
            res = execute_system_command(command_str, shell=shell_type)
            print(json.dumps(res, ensure_ascii=False))

        elif cmd == "screenshot":
            res = capture_screen()
            print(json.dumps(res, ensure_ascii=False))

        elif cmd == "paste":
            raw_input = sys.argv[2] if len(sys.argv) > 2 else "{}"
            try:
                params = json.loads(raw_input)
                text_to_paste = params.get("text", "")
            except Exception:
                text_to_paste = raw_input
            res = paste_to_active_window(text_to_paste)
            print(json.dumps(res, ensure_ascii=False))
        elif cmd == "tts-synth":
            import edge_tts_service
            raw_input = sys.argv[2] if len(sys.argv) > 2 else "{}"
            try:
                params = json.loads(raw_input)
            except Exception:
                params = {"text": raw_input}
            res = edge_tts_service.synthesize_text(
                text=params.get("text", ""),
                voice=params.get("voice", "en-IN-NeerjaNeural"),
                rate=params.get("rate", "+0%")
            )
            print(json.dumps(res, ensure_ascii=False))

        elif cmd == "tts-demo":
            import edge_tts_service
            raw_input = sys.argv[2] if len(sys.argv) > 2 else "{}"
            try:
                params = json.loads(raw_input)
                voice = params.get("voice", "en-IN-NeerjaNeural")
            except Exception:
                voice = raw_input or "en-IN-NeerjaNeural"
            res = edge_tts_service.get_demo_audio(voice=voice)
            print(json.dumps(res, ensure_ascii=False))

        elif cmd == "tts-voices":
            import edge_tts_service
            voices = edge_tts_service.get_voices_catalog()
            print(json.dumps({"success": True, "voices": voices}, ensure_ascii=False))
        else:
            print(json.dumps({"success": False, "error": f"Unknown command: {cmd}"}))
    else:
        print(json.dumps({"success": True, "status": "Assistant Service Ready"}))

if __name__ == "__main__":
    main()

