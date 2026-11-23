"""
AntigravityFlow - Active System Context & Non-Polluting Input Injection
======================================================================
Extracts rich foreground application metadata, mouse cursor target, focused control,
and entire in-memory text field contents (via UI Automation) without clipboard snooping.
Provides private text injection that never pollutes Windows Clipboard History (Win+V).
"""

import ctypes
from ctypes import wintypes
import time
import psutil
import uiautomation as auto

# Win32 Clipboard Formats for Windows 10/11 Clipboard History Exclusion
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040

try:
    CF_EXCLUDE_CLIPBOARD = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
    CF_CAN_INCLUDE_HISTORY = user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
    CF_CAN_UPLOAD_CLOUD = user32.RegisterClipboardFormatW("CanUploadToCloudClipboard")
except Exception:
    CF_EXCLUDE_CLIPBOARD = 0
    CF_CAN_INCLUDE_HISTORY = 0
    CF_CAN_UPLOAD_CLOUD = 0


def get_active_system_context(
    include_window: bool = True,
    include_cursor: bool = True,
    include_focused: bool = True,
    include_field: bool = True,
    max_field_words: int = 500
) -> dict:
    """Extracts foreground application, active window, mouse cursor coordinates/target,
    currently focused control, and the trailing text field contents (in-memory via UIA)
    without copy-pasting or notifying the user, respecting user-configured limits.
    """
    ctx = {
        "app_name": "Desktop",
        "window_title": "",
        "cursor_pos": (0, 0),
        "cursor_element": "",
        "focused_element": "",
        "field_content": "",
        "is_editable": False
    }

    com_inited = False
    try:
        hr = ctypes.windll.ole32.CoInitialize(None)
        com_inited = (hr in (0, 1))  # S_OK or S_FALSE (already initialized on this thread)
    except Exception:
        pass

    try:
        # 1. Foreground Application & Window Title (Pure Win32, zero COM dependency)
        if include_window:
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        ctx["window_title"] = buff.value
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value:
                        ctx["app_name"] = psutil.Process(pid.value).name()
            except Exception:
                pass

        # 2. Mouse Cursor Position & Target Element Under Cursor
        if include_cursor:
            try:
                # Fast native Win32 GetCursorPos
                pt = wintypes.POINT()
                if user32.GetCursorPos(ctypes.byref(pt)):
                    cx, cy = pt.x, pt.y
                    ctx["cursor_pos"] = (cx, cy)
                    try:
                        c_ctrl = auto.ControlFromPoint(cx, cy)
                        if c_ctrl:
                            ctx["cursor_element"] = f"{c_ctrl.ControlTypeName} ('{c_ctrl.Name or 'Unlabeled'}')"
                            if any(t in c_ctrl.ControlTypeName for t in ["Edit", "Document", "RichEdit", "ComboBox"]):
                                ctx["is_editable"] = True
                    except Exception:
                        pass
            except Exception:
                pass

        # 3. Focused UI Element & Trailing Text Box Content Extraction (in-memory via UIA)
        if include_focused or include_field:
            try:
                f_ctrl = auto.GetFocusedControl()
                if f_ctrl:
                    type_name = f_ctrl.ControlTypeName or "Control"
                    name_label = f_ctrl.Name or ""
                    auto_id = f_ctrl.AutomationId or ""
                    if include_focused:
                        ctx["focused_element"] = f"{type_name} (Name: '{name_label}', Id: '{auto_id}')"

                    if any(t in type_name for t in ["Edit", "Document", "RichEdit", "ComboBox"]):
                        ctx["is_editable"] = True

                    if include_field:
                        extracted = ""

                        # Pattern A: ValuePattern (Standard text boxes, search fields, address bars)
                        try:
                            vp = f_ctrl.GetPattern(auto.PatternId.ValuePattern)
                            if vp:
                                val = auto.ValuePattern(vp).Value
                                if val and val.strip():
                                    extracted = val.strip()
                        except Exception:
                            pass

                        # Pattern B: TextPattern (Rich text editors, VS Code, Word, document controls)
                        if not extracted:
                            try:
                                tp = f_ctrl.GetPattern(auto.PatternId.TextPattern)
                                if tp:
                                    val = auto.TextPattern(tp).DocumentRange.GetText(-1)
                                    if val and val.strip():
                                        extracted = val.strip()
                            except Exception:
                                pass

                        # Pattern C: LegacyIAccessiblePattern (Older Win32 / custom controls)
                        if not extracted:
                            try:
                                lp = f_ctrl.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
                                if lp:
                                    val = auto.LegacyIAccessiblePattern(lp).Value
                                    if val and val.strip():
                                        extracted = val.strip()
                            except Exception:
                                pass

                        # Pattern D: Win32 Edit Handle direct WM_GETTEXT
                        if not extracted and f_ctrl.NativeWindowHandle:
                            try:
                                val = auto.GetEditText(f_ctrl.NativeWindowHandle)
                                if val and val.strip():
                                    extracted = val.strip()
                            except Exception:
                                pass

                        if extracted:
                            ctx["is_editable"] = True
                            words = extracted.split()
                            limit = max(50, min(2000, max_field_words))
                            if len(words) > limit:
                                ctx["field_content"] = " ".join(words[-limit:])
                            else:
                                ctx["field_content"] = extracted
            except Exception:
                pass

    finally:
        if com_inited:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

    return ctx


def set_private_clipboard(text: str) -> bool:
    """Sets clipboard text with Windows 10/11 exclusion flags so Windows Clipboard History
    (Win+V) and Cloud Clipboard never record this transaction.
    """
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        # 1. Unicode text content
        encoded = (text + '\0').encode('utf-16le')
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        ptr = kernel32.GlobalLock(h_mem)
        ctypes.memmove(ptr, encoded, len(encoded))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)

        # 2. Exclude from monitor processing (Win+V filter)
        if CF_EXCLUDE_CLIPBOARD:
            h_ex = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, 4)
            user32.SetClipboardData(CF_EXCLUDE_CLIPBOARD, h_ex)

        # 3. CanIncludeInClipboardHistory = 0 (Explicitly disables Win+V recording)
        if CF_CAN_INCLUDE_HISTORY:
            h_hist = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, 4)
            ptr_hist = kernel32.GlobalLock(h_hist)
            ctypes.memset(ptr_hist, 0, 4)
            kernel32.GlobalUnlock(h_hist)
            user32.SetClipboardData(CF_CAN_INCLUDE_HISTORY, h_hist)

        # 4. CanUploadToCloudClipboard = 0
        if CF_CAN_UPLOAD_CLOUD:
            h_cloud = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, 4)
            ptr_cloud = kernel32.GlobalLock(h_cloud)
            ctypes.memset(ptr_cloud, 0, 4)
            kernel32.GlobalUnlock(h_cloud)
            user32.SetClipboardData(CF_CAN_UPLOAD_CLOUD, h_cloud)

        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()


IS_PASTING = False

def paste_cleanly(text: str):
    """Puts text on clipboard tagged with Win+V exclusion flags, sends Ctrl+V to paste into
    the active focused field, and immediately restores user's prior clipboard.
    Ensures zero clipboard pollution and prevents synthetic key feedback.
    """
    global IS_PASTING
    if not text:
        return

    import pyperclip

    # Capture prior user clipboard
    prior_clip = ""
    try:
        prior_clip = pyperclip.paste()
    except Exception:
        pass

    IS_PASTING = True
    try:
        # 0. Release any physically held modifier keys (Alt, Win) so Alt+V doesn't become Ctrl+Alt+V
        VK_MENU = 0x12
        VK_LWIN = 0x5B
        VK_RWIN = 0x5C
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_RWIN, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)

        # 1. Set private clipboard (excluded from Win+V)
        if not set_private_clipboard(text):
            pyperclip.copy(text)

        time.sleep(0.03)

        # 2. Fire Ctrl+V
        VK_CONTROL = 0x11
        VK_V = 0x56

        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.04)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

        # 3. Restore user's prior clipboard content after 200ms
        import threading
        def _restore():
            time.sleep(0.20)
            try:
                if prior_clip:
                    set_private_clipboard(prior_clip)
                else:
                    if user32.OpenClipboard(0):
                        user32.EmptyClipboard()
                        user32.CloseClipboard()
            except Exception:
                pass

        threading.Thread(target=_restore, daemon=True).start()

    except Exception as e:
        print(f"[SystemContext] Paste error: {e}")
    finally:
        time.sleep(0.08)
        IS_PASTING = False
