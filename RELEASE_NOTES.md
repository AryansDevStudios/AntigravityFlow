## AntigravityFlow v1.4.1

**Smart Voice Dictation, Encrypted Audio Vault & Multimodal AI Copilot for Windows.**

### ✨ Highlights in v1.4.1

- **Zero Horizontal Scroll in AntigravityFlow AI Window (`ui/assistant.html`)**: Fixed flex container, header branding, action audit accordion, Thinking Process card, code block, KaTeX math display, and Markdown table horizontal overflow across standard (`340px+`) and ultra-compact (`220px`) window sizes. Added `.table-scroll-wrapper` so wide Markdown tables scroll neatly inside their own container.
- **Full Light/Dark/Auto Theme & Live Config Synchronization**: Unwrapped IPC `getConfig()` payloads cleanly in the Assistant and Dashboard windows, added Crisp Light theme support across `index.html` and prose elements in `assistant.html`, and added real-time `onConfigUpdated` synchronization between windows and daemon (`/reload-config`).
- **Dashboard & AI Window Controls**:
  - Added **All / Dictations / ✦ AI Queries** lane filter pills and `✦ AI` session badges to the Activity stream while preserving active search/lane filters across audio playback, retry, and deletion.
  - Added **🌐 Live Web Search** toggle button, interactive bottom-right corner drag resizing, and an in-app **Image Attachment Lightbox** modal inside the floating AI window.
  - Exposed missing settings UI controls (`#cfg-ai-hotkey-mode`, `#cfg-cascade-enabled`), synced Edge TTS speech rate range slider (`-50%` to `+150%`), dynamic neural voice selector (`getTtsVoices()`), deduplicated model dropdowns across Dictation & AI policies, live **Engine / Language Server status badges**, and dynamic 16-week Insights heatmap month labels.

---

### 🐛 Bug Fixes

- **Backend Database & History Sync (`db.py`, `daemon.py`, `ui/main.js`)**:
  - Fixed a critical bug where clearing all history or deleting the last dictation caused `init_db()` to re-import deleted items back from `history.json` (`app_meta` one-time migration tracking).
  - Routed dashboard history reads, item deletes, and clear-all actions through the daemon SQLite endpoints so `flow.db` and `history.json` stay synchronized.
  - Added `RLock` write serialization (`_DB_LOCK`), atomic `history.json` replacement, full-row returns from `update_dictation_text`, `.enc` cleanup, and aligned `DEFAULT_CONFIG` (`tts_service="edge-tts"`, `theme="auto"`).
- **Edge TTS Rate & Queue Reliability (`edge_tts_service.py`, `ui/assistant.html`)**:
  - Added `normalize_rate()` so bare numeric rate configs (e.g., `+100`) normalize cleanly to `+100%` without failing Edge TTS synthesis, and wired rate support into voice demos (`get_demo_audio`).
  - Fixed SHA-256 cache hashing to prevent collisions past 120 chars and fixed `speakFullText()` paragraph splitting and duplicate queueing on long messages.
- **Hotkeys & Key Recorder (`daemon.py`, `ui/index.html`)**:
  - Replaced hardcoded modifier/key exclusions with dynamic `_blocked_keys_for()` so combos like `Ctrl+Space` trigger reliably, fixed `ai_ptt` vs `ai_toggle` precedence when shortcuts match in `both` mode, reset hotkey state on connect failure, and emitted `voice-state: idle` on session cancel.
  - Fixed `setupKeyRecorder` modifier overwrite bug and `saveAllConfig()` overwriting custom AI shortcuts with `'windows+ctrl'`.
- **Assistant Security, Vision & CLI Reliability (`assistant_service.py`, `retranscribe.py`, `llm_refiner.py`, `ui/main.js`)**:
  - Enforced `enable_screen_vision` and resolution caps across both Python and Electron screenshot capture, removed stale `latest_screenshot.jpg` reuse, guarded explanatory PowerShell code examples against unprompted execution, pruned heavy directories during `search_files`, implemented recursive `list_directory`, and filtered `-disabled` / `tab_*` models.
  - Fixed `trigger-dictation` missing `/dictation/trigger` path, increased `/assistant/query` timeout to `120s`, added `--payload-file` support for large screenshot CLI fallbacks, moved Electron crash logging to `DATA_DIR`, and updated `retranscribe.py` to use `llm_refiner.discover_server()` + `formatter.format_transcript()`.

---

### 📥 Choose Your Windows Processor Architecture

Each installer below is an **all-in-one standalone installer & updater** bundling the UI, native daemon, Edge TTS voices, and AI bridges (no Python installation required):

- **[AntigravityFlow-Installer-1.4.1-x64.exe](https://github.com/AryansDevStudios/AntigravityFlow/releases/download/v1.4.1/AntigravityFlow-Installer-1.4.1-x64.exe)**: Standard 1-click Installer (Windows 64-bit)
- **[AntigravityFlow-Setup-1.4.1-x64.exe](https://github.com/AryansDevStudios/AntigravityFlow/releases/download/v1.4.1/AntigravityFlow-Setup-1.4.1-x64.exe)**: Windows 64-bit (Intel Core / AMD Ryzen)
- **[AntigravityFlow-Setup-1.4.1-arm.exe](https://github.com/AryansDevStudios/AntigravityFlow/releases/download/v1.4.1/AntigravityFlow-Setup-1.4.1-arm.exe)**: Windows ARM (Qualcomm Snapdragon X / Surface Copilot+ PCs)
- **[AntigravityFlow-Setup-1.4.1-x32.exe](https://github.com/AryansDevStudios/AntigravityFlow/releases/download/v1.4.1/AntigravityFlow-Setup-1.4.1-x32.exe)**: Windows 32-bit (x86 PCs)
