## AntigravityFlow v1.5.0

**Smart Voice Dictation, Encrypted Audio Vault & Multimodal AI Copilot for Windows.**

### ✨ Highlights in v1.5.0

- **🔇 Focus Audio During Dictation (Pause & Silent Mute)**: Automatically pauses active media/videos (Chrome, Brave, Edge, Spotify, VLC via Windows SMTC) and silently mutes background app & system sound sessions via WASAPI per-session volume control while dictating—**without altering Windows Master Volume or triggering the OS Mute OSD**—then restores your exact previous state on stop, cancel, or quit (toggleable in **Settings**).
- **⚡ Pre-Compiled Tailwind CSS & Memory Optimization**: Replaced the runtime Tailwind JIT compiler in both Electron windows with a build-time pre-compiled static `tailwind.css` stylesheet, nearly halving Dashboard renderer private memory (`~172 MB` -> `~89 MB`).
- **🧠 Configurable Dashboard Memory Mode**: Keeps the Dashboard pre-warmed by default (`<30ms` instant reopen) while adding a **Keep Dashboard Loaded in Memory** toggle in **Settings** for users who prefer Low-RAM lazy window creation and full window destruction on close (`~150–190 MB` RAM saved).
- **🛰️ Safer, Leaner Language Server Execution**: Applied verified non-breaking `language_server.exe` runtime flags and Go GC limits (`GOGC=50`, `GOMEMLIMIT=128MiB`) alongside thread-safe server discovery and clean exit termination.

---

### 🐛 Stability & Reliability Fixes

- **Edge Neural TTS Retry Resilience (`edge_tts_service.py`, `ui/index.html`)**: Eliminated intermittent `No audio was received. Please verify that your parameters are correct.` errors from Edge-TTS WebSocket endpoints via multi-attempt async retries, control-character/non-speakable input validation, fallback voice protection during live narration, and non-blocking inline Settings feedback.
- **No-Stampede Startup & Zero-Leftover Shutdown (`ui/main.js`, `daemon.py`)**: Deduplicated initial IPC daemon readiness checks (`waitForDaemonReady`) so startup never spawns redundant parallel CLI daemon unpacks, and updated `shutdown()` to request graceful Electron teardown and preserve PyInstaller's parent bootloader for clean `_MEI` cleanup and exit code `0`.
- **Daemon & Assistant Hardening (`daemon.py`, `assistant_service.py`, `quota_service.py`, `pill.py`)**: Enabled `ThreadingHTTPServer`, `RLock` session synchronization, safe broken-pipe JSON output, non-blocking tray/hotkey pill dispatch, PTT release race protection, and cleaner tray/pill exit.

---

### 📥 Choose Your Windows Processor Architecture

Each installer below is an **all-in-one standalone installer & updater** bundling the UI, native daemon, Edge TTS voices, and AI bridges (no Python installation required):

- **[AntigravityFlow-Installer-1.5.0-x64.exe](https://github.com/AryansDevStudios/AntigravityFlow/releases/download/v1.5.0/AntigravityFlow-Installer-1.5.0-x64.exe)**: Standard 1-click Installer (Windows 64-bit)
- **[AntigravityFlow-Setup-1.5.0-x64.exe](https://github.com/AryansDevStudios/AntigravityFlow/releases/download/v1.5.0/AntigravityFlow-Setup-1.5.0-x64.exe)**: Windows 64-bit (Intel Core / AMD Ryzen)
- **[AntigravityFlow-Setup-1.5.0-arm.exe](https://github.com/AryansDevStudios/AntigravityFlow/releases/download/v1.5.0/AntigravityFlow-Setup-1.5.0-arm.exe)**: Windows ARM (Qualcomm Snapdragon X / Surface Copilot+ PCs)
- **[AntigravityFlow-Setup-1.5.0-x32.exe](https://github.com/AryansDevStudios/AntigravityFlow/releases/download/v1.5.0/AntigravityFlow-Setup-1.5.0-x32.exe)**: Windows 32-bit (x86 PCs)
