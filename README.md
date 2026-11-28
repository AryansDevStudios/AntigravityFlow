<div align="center">

# ⚡ AntigravityFlow

**Real-Time Voice Dictation, Encrypted Local Audio Vault & Multimodal AI Copilot for Google Antigravity on Windows**

[![Version](https://img.shields.io/badge/version-v1.5.0-amber?style=for-the-badge)](https://github.com/AryansDevStudios/AntigravityFlow/releases/tag/v1.5.0)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64%20%7C%20ARM64%20%7C%20x86-0078D6?style=for-the-badge&logo=windows)](https://github.com/AryansDevStudios/AntigravityFlow/releases/latest)
[![Build](https://img.shields.io/github/actions/workflow/status/AryansDevStudios/AntigravityFlow/build-windows.yml?branch=master&style=for-the-badge&label=Windows%20Build)](https://github.com/AryansDevStudios/AntigravityFlow/actions/workflows/build-windows.yml)
[![License](https://img.shields.io/badge/license-MIT-emerald?style=for-the-badge)](LICENSE)

[Download Latest Release](https://github.com/AryansDevStudios/AntigravityFlow/releases/latest) · [Release Notes (v1.5.0)](RELEASE_NOTES.md) · [Changelog](CHANGELOG.md) · [API Catalog](docs/API_CATALOG.md)

</div>

---

## ✨ Overview

**AntigravityFlow** is a Windows desktop application pairing a low-latency **Python background dictation & automation daemon** with an **Electron dashboard & floating AI overlay**. It connects directly to your local authenticated Google Antigravity Language Server session for zero-overhead AI refinement, multimodal screen understanding, multi-step Windows automation, and Microsoft Edge Neural voice synthesis.

### Key Capabilities

- **🎙️ Dual-Lane Global Voice Engine**
  - **Dictation Lane**: Hold (`Win+Alt`) or toggle (`Win+Alt+H`) to dictate anywhere on Windows with context-aware formatting, custom vocabulary replacement, speech macros, and instant clipboard/keystroke injection.
  - **AntigravityFlow AI Lane**: Hold (`Win+Ctrl`) to speak directly to the floating AI assistant, or quick-tap (`< 320ms`) to summon and focus the prompt input box.
  - **Quick-Paste Recovery**: Tap `Alt+V` anytime to re-paste your latest dictation.
  - **Floating Visual Pill ([`pill.py`](pill.py))**: Real-time equalizer pill indicator showing listening, transcribing, AI processing, and error states.

- **✦ Floating AntigravityFlow AI Window ([`ui/assistant.html`](ui/assistant.html))**
  - **Rich Multimodal Formatting**: Renders Markdown with syntax-highlighted soft-wrapping code blocks (1-click **Copy** and **▶ Run** for shell blocks), internally scrollable Markdown tables, and KaTeX LaTeX mathematics/chemistry equations (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`).
  - **Transparent Action Audit & Thinking Drawer**: Expandable **Thinking Process** accordion and step-by-step **Action Audit** cards detailing MCP file/search actions, PowerShell commands, exit codes, and durations.
  - **Screen Vision & Image Attachments**: Capture the active display invisibly via Windows Display Affinity (`WDA_EXCLUDEFROMCAPTURE` so AntigravityFlow excludes itself from captures), paste clipboard images (`Ctrl+V`), or drag-and-drop images with in-app lightbox preview.
  - **Web Search & Citations**: Dedicated `🌐` web search toggle alongside automatic real-time web intelligence with clickable source chips.
  - **Voice-Over Response Engine**: Streaming paragraph-grouped Microsoft Edge Neural TTS playback (`Pause` / `Resume` / `Stop` pill + instant barge-in when typing or dictating).

- **📊 Desktop Dashboard ([`ui/index.html`](ui/index.html))**
  - **Activity History & Search**: Filter across **All**, **Dictations**, and **✦ AI Queries**, search transcripts, play decrypted audio in-memory, export `.wav` files, or re-transcribe recordings.
  - **Insights Analytics**: 16-week activity contribution heatmap, WPM speedometer gauge, current/longest streak tracking, fix estimates, category breakdown, and live Google Antigravity account quota cards.
  - **Custom Vocabulary & Speech Macros**: Define case-sensitive/symbol vocabulary rules (`c++` → `C++`, `.net` → `.NET`, `k8s` → `Kubernetes`) and reusable macro expansions (`{date}`, `{time}`, `{clipboard}`, `{cursor}`).
  - **AI Policy & Deep Context Sensors**: Switch Dictation Modes (`flow`, `generative`, `custom`) and Assistant Modes (`general`, `developer`, `concise`, `custom`), configure reasoning budgets, UIA sensors, IDE Cascade context biasing, Focus Audio during dictation, Low-RAM vs Instant Reopen dashboard mode, Command Execution & Screen Vision safety toggles, and **System Auto / Dark / Crisp Light** themes.

---

## ⌨️ Default Shortcuts

All shortcuts and activation modes (`both`, `hold`, `toggle`) can be customized in **General Settings**:

| Action | Default Shortcut | Behavior |
|---|---|---|
| **Dictate (Push-to-Talk)** | `Win + Alt` | Hold while speaking; release to transcribe & paste |
| **Dictate (Hands-Free Toggle)** | `Win + Alt + H` | Tap to start listening; tap again to stop & paste |
| **AntigravityFlow AI** | `Win + Ctrl` | Hold (`>= 320ms`) to speak to AI; quick tap (`< 320ms`) to focus prompt |
| **Quick-Paste Last Dictation** | `Alt + V` | Pastes the most recent dictation at cursor |

---

## 📦 Installation

### Option 1: Standalone Windows Installer (Recommended)

No Python or Node.js installation is required. Download the installer matching your processor from [**GitHub Releases**](https://github.com/AryansDevStudios/AntigravityFlow/releases/latest):

| Architecture | Installer Asset |
|---|---|
| **Windows x64 (Standard)** | `AntigravityFlow-Installer-1.5.0-x64.exe` / `AntigravityFlow-Setup-1.5.0-x64.exe` |
| **Windows ARM64 (Snapdragon / Surface)** | `AntigravityFlow-Setup-1.5.0-arm.exe` |
| **Windows x86 (32-bit)** | `AntigravityFlow-Setup-1.5.0-x32.exe` |

> Running a newer installer updates existing installations in-place while preserving your local settings, vocabulary, macros, and encrypted recordings vault under `%APPDATA%\AntigravityFlow`.

---

### Option 2: Run from Source (Local Development)

#### Prerequisites
- **Windows 10 / 11**
- **Python 3.11 – 3.14**
- **Node.js 20+** and **npm**
- **Google Antigravity IDE** signed in (provides the local authenticated Language Server session for LLM refinement, assistant queries, and quota telemetry)

#### Setup & Launch
```powershell
# 1. Clone the repository
git clone https://github.com/AryansDevStudios/AntigravityFlow.git
cd AntigravityFlow

# 2. Install Python backend dependencies
pip install -r requirements.txt

# 3. Install Electron UI dependencies
cd ui
npm install
cd ..

# 4. Start the background daemon & UI
.\run.bat
```

Or launch only the Electron UI in development mode (automatically spawns/monitors `daemon.py`):
```powershell
cd ui
npm start
```

---

### Option 3: Build Standalone Executable & Installer

Compile the PyInstaller daemon (`dist/AntigravityFlowDaemon.exe`) and package the NSIS Windows installer via `electron-builder`:

```powershell
python build_app.py
```

Multi-architecture builds (`x64`, `arm64`, `ia32`) are also automated via GitHub Actions in [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml).

---

## 🏗️ Project Architecture

```text
AntigravityFlow/
├── daemon.py                 # Background orchestrator: tray icon, hotkeys, HTTP control server (:48198), CLI
├── assistant_service.py      # Multimodal AI loop, MCP tools, screen vision, PowerShell/CMD exec, web search
├── llm_refiner.py            # Local Language Server discovery & gRPC-Web dictation refinement
├── quota_service.py          # Account plan, prompt/flow credits & 5h/weekly quota telemetry
├── edge_tts_service.py       # Microsoft Edge Neural TTS synthesis, rate normalization & SHA-256 audio cache
├── db.py                     # Thread-safe SQLite vault (flow.db), one-time migration, config & macro store
├── formatter.py              # Symbol-safe vocabulary replacement, numbers & punctuation formatting
├── retranscribe.py           # Encrypted recording recovery & re-transcription pipeline
├── batch_transcriber.py      # Local ASR speech transcription worker
├── system_context.py         # Active window / UIA focused-control context extraction & safe pasting
├── context_peeker.py         # Foreground editor/field peeking helpers
├── crypto_audio.py           # AES-256-CBC encryption/decryption for local voice recordings
├── accumulator.py & dsp_pipeline.py  # Audio buffer capture & DSP helpers
├── pill.py                   # Native desktop floating status indicator pill
├── prompts.py                # Dictation & Assistant system prompt presets
├── build_app.py              # Unified PyInstaller + electron-builder production build script
├── build_daemon.spec         # PyInstaller spec bundling daemon, resources & models catalog
├── tools/                    # HTML structural verifier, API catalog dumper & OpenAI cascade proxy
├── docs/API_CATALOG.md       # Local gRPC-Web / Language Server endpoint reference
└── ui/
    ├── main.js               # Electron main process, IPC bridge, clean screen capture & daemon supervisor
    ├── preload.js            # Context-isolated window.api preload script
    ├── index.html            # Main dashboard (Activity, Insights, Vocabulary, Macros, AI Policy, General)
    ├── assistant.html        # Floating frosted-glass AI overlay window
    └── package.json          # Electron 33, KaTeX, Marked & NSIS builder configuration
```

---

## 🔒 Privacy & Local Data

- **Encrypted Audio Vault**: Dictation audio files are stored locally under `%APPDATA%\AntigravityFlow\recordings\` encrypted with **AES-256-CBC** (`crypto_audio.py`).
- **Local History & Config**: Metadata, custom vocabulary, macros, and settings live locally in `%APPDATA%\AntigravityFlow\` (`flow.db`, `config.json`, `history.json`, `vocabulary.json`).
- **Execution & Vision Guardrails**: Autonomous PowerShell/file commands (`enable_system_commands`) and Screen Vision (`enable_screen_vision`) can be toggled independently at any time in **Settings** or inside the Assistant Policy panel.

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request, and use the issue templates in `.github/ISSUE_TEMPLATE/` for bug reports or feature requests.

---

<div align="center">
Made with ❤️ by <a href="https://github.com/AryansDevStudios">AryansDevStudios</a> · Released under the <a href="LICENSE">MIT License</a>
</div>
