"""AntigravityFlow Automated Build & Packaging Pipeline

Compiles:
1. Python Daemon -> standalone AntigravityFlowDaemon.exe (via PyInstaller)
2. Electron UI + Bundled Daemon -> standalone Windows Installer (.exe) (via electron-builder)

User history, recordings vault, and settings remain permanently preserved
in %APPDATA%/AntigravityFlow across all updates and reinstalls.
"""

import os
import sys
import subprocess
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(BASE_DIR, "ui")
DIST_DIR = os.path.join(BASE_DIR, "dist")
SPEC_FILE = os.path.join(BASE_DIR, "build_daemon.spec")

def run(cmd, cwd=BASE_DIR):
    print(f"\n[>] Running: {cmd} (cwd: {cwd})")
    res = subprocess.run(cmd, shell=True, cwd=cwd)
    if res.returncode != 0:
        print(f"[!] Error: Command failed with exit code {res.returncode}")
        sys.exit(res.returncode)

def main():
    print("=" * 60)
    print(" AntigravityFlow Production Build Pipeline")
    print("=" * 60)

    # 1. Ensure PyInstaller is installed
    try:
        import PyInstaller
        print(f"[+] Found PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("[*] Installing PyInstaller...")
        run(f'"{sys.executable}" -m pip install pyinstaller')

    # 2. Build Python Daemon
    print("\n[*] Step 1: Compiling AntigravityFlowDaemon.exe with PyInstaller...")
    run(f'"{sys.executable}" -m PyInstaller "{SPEC_FILE}" --clean -y')

    daemon_exe = os.path.join(DIST_DIR, "AntigravityFlowDaemon.exe")
    if not os.path.exists(daemon_exe):
        print(f"[!] Failed to locate built executable at: {daemon_exe}")
        sys.exit(1)
    print(f"[+] Daemon compiled successfully: {daemon_exe}")

    # 3. Check for electron-builder in ui/
    print("\n[*] Step 2: Packaging Electron Windows Setup...")
    run("npm run dist", cwd=UI_DIR)

    # 4. Copy to Downloads folder
    ui_dist = os.path.join(UI_DIR, "dist")
    downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
    copied_installers = []

    if os.path.exists(ui_dist):
        for f in os.listdir(ui_dist):
            if f.endswith(".exe") and not f.startswith("builder-"):
                src = os.path.join(ui_dist, f)
                dst = os.path.join(downloads_dir, f)
                shutil.copy2(src, dst)
                size_mb = os.path.getsize(dst) / (1024 * 1024)
                copied_installers.append((dst, size_mb))
                print(f"[+] Copied installer to Downloads: {dst} ({size_mb:.2f} MB)")

    print("\n" + "=" * 60)
    print(" BUILD COMPLETE!")
    if copied_installers:
        for path, size in copied_installers:
            print(f" Saved in Downloads: {path} ({size:.2f} MB)")
    else:
        print(f" Installer available in: {ui_dist}")
    print("=" * 60)

if __name__ == "__main__":
    main()
