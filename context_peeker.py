import ctypes
from ctypes import wintypes
import psutil

def get_precursor_context() -> str:
    """Extracts non-invasive semantic context from the active foreground window
    to populate preCursorText in Google Antigravity's StreamAudioTranscription.
    This primes the acoustic language model with grammar and domain context.
    """
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""

        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""

        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value.strip()

        # Ignore AntigravityFlow's own UI windows
        if any(ign in title.lower() for ign in ["antigravityflow", "task switching", "cortana", "search"]):
            return ""

        # Extract process name
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = ""
        if pid.value:
            try:
                proc_name = psutil.Process(pid.value).name()
            except Exception:
                pass

        context_parts = []
        if proc_name:
            context_parts.append(proc_name)
        if title:
            # Clean up common window title noise
            clean_title = title.split(" - Visual Studio Code")[0].split(" - Google Chrome")[0].strip()
            context_parts.append(clean_title)

        return " | ".join(context_parts)[:120]
    except Exception:
        return ""

def get_active_cascade_id(port: int, csrf_token: str) -> str:
    """Queries /GetAllCascadeTrajectories to locate the active Antigravity conversation
    or IDE workspace trajectory for contextual acoustic biasing."""
    try:
        import http.client, ssl, json
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        conn = http.client.HTTPSConnection('127.0.0.1', port, context=ctx, timeout=2.0)
        headers = {
            'content-type': 'application/grpc-web+json',
            'x-codeium-csrf-token': csrf_token,
            'x-grpc-web': '1'
        }
        frame = bytes([0, 0, 0, 0, 2]) + b'{}'
        conn.request('POST', '/exa.language_server_pb.LanguageServerService/GetAllCascadeTrajectories', body=frame, headers=headers)
        res = conn.getresponse()
        hdr = res.read(5)
        if not hdr or len(hdr) < 5:
            return ""
        l = int.from_bytes(hdr[1:5], 'big')
        data = json.loads(res.read(l).decode('utf-8'))
        summaries = data.get('trajectorySummaries', {})

        # 1. Running trajectory has highest priority
        for traj_id, info in summaries.items():
            if info.get('status') == 'CASCADE_RUN_STATUS_RUNNING':
                return info.get('trajectoryId') or traj_id

        # 2. Most recent trajectory as secondary
        latest_id = ""
        latest_time = ""
        for traj_id, info in summaries.items():
            mod_time = info.get('lastModifiedTime') or info.get('createdTime') or ''
            if mod_time > latest_time:
                latest_time = mod_time
                latest_id = info.get('trajectoryId') or traj_id

        return latest_id
    except Exception:
        return ""
