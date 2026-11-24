# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None
base_dir = os.path.abspath(SPECPATH)

sounds_dir = os.path.join(base_dir, "sounds")
datas = []
if os.path.exists(sounds_dir):
    datas.append((sounds_dir, "sounds"))

vocab_file = os.path.join(base_dir, "data", "vocabulary.json")
if os.path.exists(vocab_file):
    datas.append((vocab_file, "data"))

# Include PortAudio DLL for sounddevice if found (supports both 64-bit and 32-bit)
try:
    import _sounddevice_data
    sdd_dir = os.path.dirname(_sounddevice_data.__file__)
    for root, _, files in os.walk(sdd_dir):
        for f in files:
            if f.endswith('.dll'):
                datas.append((os.path.join(root, f), '_sounddevice_data/portaudio-binaries'))
except Exception:
    pass
try:
    import sounddevice
    sd_dir = os.path.dirname(sounddevice.__file__)
    for root, _, files in os.walk(sd_dir):
        for f in files:
            if f.endswith('.dll'):
                datas.append((os.path.join(root, f), 'sounddevice'))
except Exception:
    pass

a = Analysis(
    ['daemon.py'],
    pathex=[base_dir],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'sounddevice',
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageTk',
        'keyboard',
        'psutil',
        'pyperclip',
        'numpy',
        'winreg',
        'ctypes',
        'ctypes.wintypes',
        'crypto_audio',
        'batch_transcriber',
        'context_peeker',
        'formatter',
        'accumulator',
        'dsp_pipeline',
        'pill',
        'db',
        'llm_refiner',
        'assistant_service',
        'edge_tts_service',
        'edge_tts',
        'system_context',
        'prompts',
        'quota_service',
        'uiautomation',
        'retranscribe'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'torch', 'cv2', 'pytest', 'unittest', 'torchvision', 'llvmlite', 'ctranslate2', 'onnxruntime', 'pyarrow', 'pymupdf', 'pikepdf', 'av'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Strip out massive unrelated ML / video / compiler bloat accidentally crawled from host environment
bloat_keywords = [
    'torch', 'llvmlite', 'ctranslate2', 'cv2', 'pymupdf', 'pyarrow',
    'onnxruntime', 'av.libs', 'avcodec', 'avfilter', 'avformat', 'libx265',
    'mupdf', 'parquet', 'pikepdf', 'qpdf', 'arrow', 'torchvision',
    'libsvtav1enc'
]

def is_bloat(name, path):
    low_name = str(name).lower()
    low_path = str(path).lower()
    # Always keep numpy internal runtime DLLs
    if 'numpy.libs' in low_path or 'numpy.libs' in low_name:
        return False
    if any(k in low_name or k in low_path for k in bloat_keywords):
        return True
    if 'scipy' in low_name or 'scipy' in low_path:
        return True
    return False

a.binaries = [
    b for b in a.binaries
    if not is_bloat(b[0], b[1])
]

a.datas = [
    d for d in a.datas
    if not is_bloat(d[0], d[1])
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AntigravityFlowDaemon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # Windowless pythonw mode
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(base_dir, 'ui', 'icon.ico') if os.path.exists(os.path.join(base_dir, 'ui', 'icon.ico')) else None
)
