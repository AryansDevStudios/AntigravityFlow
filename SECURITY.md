# Security Policy

## Local Data & Privacy Model

- **Local Storage**: Configuration, transcription metadata, vocabulary, and macros remain local under `%APPDATA%\AntigravityFlow\`.
- **Encrypted Audio Vault**: Dictation audio recordings are stored locally encrypted with **AES-256-CBC** (`crypto_audio.py`).
- **Execution & Vision Policy Controls**:
  - Autonomous PowerShell/terminal execution (`enable_system_commands`) and Screen Vision capture (`enable_screen_vision`) can be disabled at any time in **General Settings** or in the Assistant Policy drawer.
- **Local Services**: Daemon HTTP control (`127.0.0.1:48198`) and UI bridge (`127.0.0.1:48199`) bind strictly to localhost loopback (`127.0.0.1`).

## Reporting a Vulnerability

If you discover a security issue, please avoid disclosing exploit details publicly in standard issues. Open a private report via GitHub Security Advisories on [`AryansDevStudios/AntigravityFlow`](https://github.com/AryansDevStudios/AntigravityFlow/security/advisories) or contact the maintainers directly with steps to reproduce and affected versions.
