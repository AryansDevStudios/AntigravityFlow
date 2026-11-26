# Contributing to AntigravityFlow

Thank you for helping improve **AntigravityFlow**!

## Development Setup

1. Fork and clone the repository:
   ```powershell
   git clone https://github.com/AryansDevStudios/AntigravityFlow.git
   cd AntigravityFlow
   ```
2. Install Python dependencies (Python 3.11 – 3.14 on Windows):
   ```powershell
   pip install -r requirements.txt
   ```
3. Install Electron UI dependencies (Node.js 20+):
   ```powershell
   cd ui
   npm install
   cd ..
   ```
4. Launch the daemon & UI locally:
   - Run `.\run.bat` for the full daemon + pre-warmed UI, or `cd ui && npm start` for UI development.

## Verification Checklist Before Opening a PR

Please run the structural and syntax checks before committing:

```powershell
# Python compilation & dashboard HTML integrity check
python -m py_compile daemon.py assistant_service.py db.py llm_refiner.py quota_service.py edge_tts_service.py formatter.py retranscribe.py system_context.py batch_transcriber.py accumulator.py dsp_pipeline.py context_peeker.py pill.py prompts.py crypto_audio.py build_app.py
python tools/verify_index_html.py
```

When modifying UI files (`ui/main.js`, `ui/preload.js`, `ui/index.html`, `ui/assistant.html`), ensure zero uncaught JavaScript syntax errors and verify the floating AI window remains free of horizontal page scrollbars at compact widths (`220px–340px`).

## Pull Request Guidelines

- Keep changes focused and modular.
- Update `CHANGELOG.md` and `RELEASE_NOTES.md` when bumping `ui/package.json` version numbers.
- Do not commit local user artifacts (`data/flow.db*`, `data/config.json`, `data/recordings/`, `*.log`, `dist/`, `build/`).
