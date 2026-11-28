# Changelog

All notable changes to **AntigravityFlow** are documented in this file.

## [1.5.0] - 2026-11-28

### Added
- **Focus Audio During Dictation (`audio_focus.py`, `daemon.py`, `ui/index.html`)**: Automatically pauses active playing Windows media sessions via WinRT SMTC (`winrt.windows.media.control`) and silently mutes background app & system sound sessions via WASAPI `ISimpleAudioVolume::SetMute` while dictating (without changing Windows Master Volume or triggering Mute OSD), restoring previous play/mute states on stop, cancel, or shutdown. Controlled by the new **Focus Audio During Dictation** Settings toggle (`auto_mute_media_on_dictate`).
- **Configurable Dashboard Memory Mode (`ui/main.js`, `db.py`, `ui/index.html`)**: Keeps the Dashboard pre-warmed by default (`keep_dashboard_in_memory: true`) for instant `<30ms` reopen, with a Settings opt-out (**Keep Dashboard Loaded in Memory**) enabling Low-RAM lazy creation & window destruction on close.
- **Pre-Compiled Static Tailwind CSS (`ui/tailwind.css`, `ui/package.json`)**: Replaced the runtime `tailwind.min.js` JIT compiler with a build-time minified `tailwind.css` bundle, significantly cutting Dashboard & Assistant renderer memory usage and startup CPU work.

### Improved & Fixed
- **Standalone Language Server Optimization (`llm_refiner.py`)**: Added verified non-breaking flags (`--disable_telemetry=true`, `--use_ls_chrome_devtools_mcp=false`, `--use_custom_page_actions=false`, `--mquery_for_context_module=false`, `--max_num_tracked_workspaces=2`, `--limit_go_max_procs=2`) and `GOGC=50` / `GOMEMLIMIT=128MiB` while fixing headless startup issues and startup discovery locking (`_LS_START_LOCK` + spawned PID lifecycle cleanup).
- **Resilient Edge Neural TTS (`edge_tts_service.py`, `ui/index.html`)**: Added multi-attempt async WebSocket retries (`_synthesize_with_retries_async`), speakable-text & control-byte guards, and optional fallback voice recovery to eliminate intermittent `NoAudioReceived` synthesis failures, alongside non-blocking inline error status feedback in Settings.
- **Startup & Process Lifecycle (`ui/main.js`, `daemon.py`, `assistant_service.py`, `quota_service.py`, `pill.py`)**: Deduplicated daemon readiness polling across startup IPCs to eliminate cold CLI unpack stampedes, preserved PyInstaller parent bootloader exit cleanup in `shutdown()`, switched tray exit to graceful Electron shutdown over bridge port `48199`, added thread-safe `RLock` and non-blocking callbacks, and resolved PTT release races.

---

## [1.4.1] - 2026-11-26

### Added
- **Dashboard & Assistant Feature Parity**:
  - Activity lane filter pills (**All**, **Dictations**, **✦ AI Queries**) and `✦ AI` session badges in `ui/index.html` preserving active filters across playback, retries, and deletions.
  - Live **Engine Status** (`#engine-status-badge`) and **Local Language Server** (`#ls-status-badge`) indicators powered by `GET /status`.
  - **🌐 Force Live Web Search** toggle button, interactive corner drag-resize handle, and in-app **Image Attachment Lightbox** modal in `ui/assistant.html`.
  - General Settings controls for AI Assistant trigger mode (`#cfg-ai-hotkey-mode`), IDE Cascade context biasing (`#cfg-cascade-enabled`), synced Speech Rate range slider (`-50%` to `+150%`), dynamic neural voice selector (`getTtsVoices()`), and dynamic model catalog population across both Dictation & Assistant dropdowns.
  - Real-time `onConfigUpdated` IPC broadcast and daemon `/reload-config` synchronization.
  - Crisp Light theme support across Dashboard cards and Floating Assistant typography.

### Fixed
- **Assistant Window Horizontal Overflow (`ui/assistant.html`)**: Eliminated horizontal page scrolling at standard (`340px`) and ultra-compact (`220px`) widths by adding `min-w-0` / `overflow-x-hidden` flex constraints, `overflow-wrap: anywhere`, `.table-scroll-wrapper` containers around rendered Markdown tables, responsive `grid-cols-2 sm:grid-cols-4` policy presets, and header/audit-card truncation.
- **SQLite Deletion & Migration Bug (`db.py`, `daemon.py`, `ui/main.js`)**: Prevented legacy `history.json` auto-migration from re-importing deleted items after `clear_all_history()` or deleting the final dictation record (`app_meta` flag + direct `_query_history_rows()`), synchronized dashboard history operations with SQLite, and serialized DB writes with `_DB_LOCK`.
- **Edge Neural TTS (`edge_tts_service.py`, `ui/assistant.html`)**: Normalized numeric rate values (`+100` → `+100%`), fixed `_AUDIO_CACHE` SHA-256 key collisions, stripped math/HTML/URLs cleanly, passed configured speech rates during Voice Demos, and fixed `speakFullText()` paragraph splitting & duplicate sentence queueing.
- **Hotkeys & Key Recorder (`daemon.py`, `ui/index.html`)**: Replaced hardcoded key exclusions with dynamic `_blocked_keys_for()`, fixed `ai_ptt` vs `ai_toggle` precedence when shortcuts match in `both` mode, fixed `setupKeyRecorder` multi-modifier key loss (`event.code` mapping), fixed `saveAllConfig()` overwriting AI hotkeys, reset state cleanly when LS is offline, and added `voice-state: idle` on cancel.
- **AI Service, Vision & IPC (`assistant_service.py`, `retranscribe.py`, `llm_refiner.py`, `quota_service.py`, `ui/main.js`)**:
  - Unwrapped `getConfig()` `{ success, data }` payloads cleanly in `ui/assistant.html` and stopped overwriting custom system prompts on startup.
  - Added Markdown math protection (`parseMarkdownWithMath`) so underscores/delimiters are preserved for KaTeX.
  - Fixed missing `/dictation/trigger` path in `trigger-dictation`, increased `/assistant/query` timeout to `120s`, added `--payload-file` CLI fallback for large image payloads, and relocated `uncaughtException` crash logs to `DATA_DIR`.
  - Enforced `enable_screen_vision` and resolution caps across Python & Electron capture, prevented unprompted execution on explanatory shell examples, updated `retranscribe.py` to use `llm_refiner.discover_server()` + `formatter.format_transcript()`, fixed symbol-boundary vocabulary regex (`c++`, `.net`), allowed `< 3`-word `generative`/`custom` refinements, preserved credit/3P quota fields, and bundled `avail_models.json` in `build_daemon.spec`.

---

## [1.4.0] - 2026-11-24
- Added user **Action Audit** accordion bar in the floating AI window and **Command Execution / Screen Vision** control toggles in Settings.

## [1.3.2] - 2026-11-24
- Resolved agent syntax error, removed unprompted raw gRPC bypass, and enabled non-blocking GUI process execution.

## [1.3.1] - 2026-11-24
- Upgraded autonomous execution engine, anti-hallucination guardrails, and default system prompt presets.

## [1.2.0] - 2026-11-24
- Eliminated external Python runtime dependency in standalone installers, unified Windows Task Manager autostart, and prevented Language Server startup crashes.

## [1.1.2] - 2026-11-23
- Resolved empty model response with immediate cascade delivery, robust port discovery, and daemon instance deduplication.

## [1.1.0] - 2026-11-23
- Added Edge Neural TTS full-response speech playback and native clipboard image paste / screen capture / Windows OCR multimodal vision pipeline.
