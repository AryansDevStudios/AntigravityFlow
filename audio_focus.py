"""
Windows Focus Audio Controller for AntigravityFlow.

During active voice dictation:
1. Silently mutes other non-AntigravityFlow Windows audio sessions (including System Sounds,
   browsers, players, and apps) via WASAPI ISimpleAudioVolume::SetMute without changing
   Windows Master Volume or triggering the OS volume/mute OSD popup.
2. Pauses any currently PLAYING media sessions (e.g. YouTube, Spotify, Chrome/Edge, VLC)
   via Windows System Media Transport Controls (SMTC).

When dictation stops, cancels, or shuts down cleanly:
- Resumes only the SMTC sessions paused by AntigravityFlow.
- Unmutes only the WASAPI sessions muted by AntigravityFlow (leaving any user-muted
  sessions untouched).
"""

import asyncio
import atexit
import os
import threading
from ctypes import HRESULT, POINTER, c_float, c_int, c_uint, c_uint32
from ctypes.wintypes import BOOL, DWORD, LPWSTR

import comtypes
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, IUnknown
import psutil

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _SMTCManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _SMTCPlaybackStatus,
    )
    _HAS_SMTC = True
except Exception:
    _SMTCManager = None
    _SMTCPlaybackStatus = None
    _HAS_SMTC = False


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioSessionManager2 = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
IID_IAudioSessionControl2 = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")
IID_ISimpleAudioVolume = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")

DEVICE_STATE_ACTIVE = 0x00000001
E_RENDER = 0
_SMTC_STATUS_PLAYING = int(getattr(_SMTCPlaybackStatus, "PLAYING", 4)) if _HAS_SMTC else 4
_SMTC_STATUS_PAUSED = int(getattr(_SMTCPlaybackStatus, "PAUSED", 5)) if _HAS_SMTC else 5

# Custom event-context GUID so we never confuse Windows or third-party session trackers
_AF_EVENT_CTX = GUID("{9B2C1F5A-6E12-4A4E-8F73-112233445566}")


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD(
            [], HRESULT, "Activate",
            (["in"], POINTER(GUID), "iid"),
            (["in"], DWORD, "dwClsCtx"),
            (["in"], POINTER(c_uint32), "pActivationParams"),
            (["out"], POINTER(POINTER(IUnknown)), "ppInterface"),
        ),
        COMMETHOD(
            [], HRESULT, "OpenPropertyStore",
            (["in"], DWORD, "stgmAccess"),
            (["out"], POINTER(POINTER(IUnknown)), "ppProperties"),
        ),
        COMMETHOD(
            [], HRESULT, "GetId",
            (["out"], POINTER(LPWSTR), "ppstrId"),
        ),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint), "pcDevices")),
        COMMETHOD([], HRESULT, "Item", (["in"], c_uint, "nDevice"), (["out"], POINTER(POINTER(IMMDevice)), "ppDevice")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = IID_IMMDeviceEnumerator
    _methods_ = [
        COMMETHOD(
            [], HRESULT, "EnumAudioEndpoints",
            (["in"], c_int, "dataFlow"),
            (["in"], DWORD, "dwStateMask"),
            (["out"], POINTER(POINTER(IMMDeviceCollection)), "ppDevices"),
        ),
        COMMETHOD(
            [], HRESULT, "GetDefaultAudioEndpoint",
            (["in"], c_int, "dataFlow"),
            (["in"], c_int, "role"),
            (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint"),
        ),
    ]


class IAudioSessionControl(IUnknown):
    _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int), "pRetVal")),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(LPWSTR), "pRetVal")),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], LPWSTR, "Value"), (["in"], POINTER(GUID), "EventContext")),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(LPWSTR), "pRetVal")),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], LPWSTR, "Value"), (["in"], POINTER(GUID), "EventContext")),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID), "pRetVal")),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID), "Override"), (["in"], POINTER(GUID), "EventContext")),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], POINTER(IUnknown), "NewNotifications")),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], POINTER(IUnknown), "NewNotifications")),
    ]


class IAudioSessionControl2(IAudioSessionControl):
    _iid_ = IID_IAudioSessionControl2
    _methods_ = [
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(LPWSTR), "pRetVal")),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(LPWSTR), "pRetVal")),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(DWORD), "pRetVal")),
        COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
        COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], BOOL, "optOut")),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "SessionCount")),
        COMMETHOD([], HRESULT, "GetSession", (["in"], c_int, "SessionCount"), (["out"], POINTER(POINTER(IAudioSessionControl)), "Session")),
    ]


class IAudioSessionManager(IUnknown):
    _iid_ = GUID("{BFA971F1-4D5E-40BB-935E-967039BFBEE4}")
    _methods_ = [
        COMMETHOD(
            [], HRESULT, "GetAudioSessionControl",
            (["in"], POINTER(GUID), "AudioSessionGuid"),
            (["in"], DWORD, "StreamFlags"),
            (["out"], POINTER(POINTER(IAudioSessionControl)), "SessionControl"),
        ),
        COMMETHOD(
            [], HRESULT, "GetSimpleAudioVolume",
            (["in"], POINTER(GUID), "AudioSessionGuid"),
            (["in"], DWORD, "StreamFlags"),
            (["out"], POINTER(POINTER(IUnknown)), "AudioVolume"),
        ),
    ]


class IAudioSessionManager2(IAudioSessionManager):
    _iid_ = IID_IAudioSessionManager2
    _methods_ = [
        COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(IAudioSessionEnumerator)), "SessionEnum")),
    ]


class ISimpleAudioVolume(IUnknown):
    _iid_ = IID_ISimpleAudioVolume
    _methods_ = [
        COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "EventContext")),
        COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], POINTER(c_float), "pfLevel")),
        COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "bMute"), (["in"], POINTER(GUID), "EventContext")),
        COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL), "pbMute")),
    ]


def _is_own_audio_process(pid: int) -> bool:
    """Keep AntigravityFlow's own acoustic chimes and Electron UI unmuted."""
    if not pid:
        return False
    if pid == os.getpid() or pid == os.getppid():
        return True
    try:
        name = psutil.Process(pid).name().lower()
        return name in ("antigravityflowdaemon.exe", "antigravityflow.exe")
    except Exception:
        return False


def _iter_active_session_volumes():
    """Yields (session_instance_id, pid, simple_audio_volume) across all active render devices."""
    enumerator = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, interface=IMMDeviceEnumerator, clsctx=CLSCTX_ALL)
    devices = enumerator.EnumAudioEndpoints(E_RENDER, DEVICE_STATE_ACTIVE)
    dev_count = devices.GetCount()
    for dev_idx in range(dev_count):
        try:
            dev = devices.Item(dev_idx)
            mgr_unk = dev.Activate(IID_IAudioSessionManager2, CLSCTX_ALL, None)
            mgr = mgr_unk.QueryInterface(IAudioSessionManager2)
            sess_enum = mgr.GetSessionEnumerator()
            sess_count = sess_enum.GetCount()
            for sess_idx in range(sess_count):
                try:
                    ctrl = sess_enum.GetSession(sess_idx)
                    c2 = ctrl.QueryInterface(IAudioSessionControl2)
                    pid = int(c2.GetProcessId())
                    sid = c2.GetSessionInstanceIdentifier() or f"pid:{pid}"
                    sav = ctrl.QueryInterface(ISimpleAudioVolume)
                    yield sid, pid, sav
                except Exception:
                    continue
        except Exception:
            continue


class AudioFocusController:
    """Coordinates temporary SMTC media pause/resume and silent WASAPI session muting."""

    def __init__(self):
        self._lock = threading.RLock()
        self._active = False
        self._generation = 0
        self._muted_session_ids = set()
        self._paused_app_ids = []
        self._smtc_thread = None

    def acquire(self):
        """Synchronously silence non-AntigravityFlow sessions and asynchronously pause playing SMTC media."""
        with self._lock:
            if self._active:
                return
            self._active = True
            self._generation += 1
            my_gen = self._generation
            self._muted_session_ids.clear()
            self._paused_app_ids = []

            self._mute_other_sessions()

            t = threading.Thread(target=self._pause_playing_media, args=(my_gen,), daemon=True)
            self._smtc_thread = t
            t.start()

    def restore(self, wait_smtc: bool = False):
        """Unmutes sessions we muted and resumes SMTC media we paused."""
        with self._lock:
            pause_thread = self._smtc_thread
            was_active = self._active
            self._active = False
            if was_active or self._muted_session_ids or self._paused_app_ids:
                self._generation += 1
            to_unmute = set(self._muted_session_ids)
            to_resume = list(self._paused_app_ids)
            self._muted_session_ids.clear()
            self._paused_app_ids = []

        if to_unmute:
            self._unmute_sessions(to_unmute)
        if to_resume:
            if wait_smtc:
                self._resume_media(to_resume)
            else:
                rt = threading.Thread(target=self._resume_media, args=(to_resume,), daemon=True)
                rt.start()
        elif wait_smtc and pause_thread and pause_thread.is_alive():
            pause_thread.join(timeout=1.0)

    def _mute_other_sessions(self):
        comtypes.CoInitialize()
        try:
            for sid, pid, sav in _iter_active_session_volumes():
                try:
                    if _is_own_audio_process(pid):
                        continue
                    if bool(sav.GetMute()):
                        continue  # Respect already-muted apps/tabs
                    sav.SetMute(True, _AF_EVENT_CTX)
                    self._muted_session_ids.add(sid)
                except Exception:
                    continue
        except Exception as e:
            print(f"[AudioFocus] WASAPI mute error: {e}")
        finally:
            comtypes.CoUninitialize()

    def _unmute_sessions(self, session_ids: set):
        comtypes.CoInitialize()
        try:
            for sid, _pid, sav in _iter_active_session_volumes():
                try:
                    if sid in session_ids:
                        sav.SetMute(False, _AF_EVENT_CTX)
                except Exception:
                    continue
        except Exception as e:
            print(f"[AudioFocus] WASAPI unmute error: {e}")
        finally:
            comtypes.CoUninitialize()

    def _pause_playing_media(self, expected_gen: int):
        if not _HAS_SMTC:
            return
        paused_here = []
        try:
            async def _do_pause():
                mgr = await _SMTCManager.request_async()
                sessions = mgr.get_sessions()
                for s in sessions:
                    try:
                        pb = s.get_playback_info()
                        if int(pb.playback_status) == _SMTC_STATUS_PLAYING:
                            app_id = s.source_app_user_model_id or ""
                            ok = await s.try_pause_async()
                            if ok and app_id:
                                paused_here.append(app_id)
                    except Exception:
                        continue

            asyncio.run(_do_pause())
        except Exception as e:
            print(f"[AudioFocus] SMTC pause error: {e}")
            return

        if not paused_here:
            return

        # Re-check generation in case dictation already stopped before async SMTC completed
        need_immediate_resume = False
        with self._lock:
            if self._active and self._generation == expected_gen:
                self._paused_app_ids = paused_here
            else:
                need_immediate_resume = True

        if need_immediate_resume:
            self._resume_media(paused_here)

    def _resume_media(self, app_ids: list):
        if not _HAS_SMTC or not app_ids:
            return
        targets = set(app_ids)
        try:
            async def _do_resume():
                mgr = await _SMTCManager.request_async()
                sessions = mgr.get_sessions()
                for s in sessions:
                    try:
                        app_id = s.source_app_user_model_id or ""
                        if app_id in targets:
                            pb = s.get_playback_info()
                            if int(pb.playback_status) == _SMTC_STATUS_PAUSED:
                                await s.try_play_async()
                    except Exception:
                        continue

            asyncio.run(_do_resume())
        except Exception as e:
            print(f"[AudioFocus] SMTC resume error: {e}")


_GLOBAL_CONTROLLER = AudioFocusController()
atexit.register(_GLOBAL_CONTROLLER.restore, True)


def get_controller() -> AudioFocusController:
    return _GLOBAL_CONTROLLER
