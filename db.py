import json
import os
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DATA = os.path.join(BASE_DIR, "data")

# Persistent User Data Vault in %APPDATA% so updates never overwrite history or recordings
appdata = os.environ.get("APPDATA")
if appdata:
    DATA_DIR = os.path.join(appdata, "AntigravityFlow")
else:
    DATA_DIR = LOCAL_DATA

RECORDINGS_DIR = os.path.join(DATA_DIR, "recordings")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
VOCAB_PATH = os.path.join(DATA_DIR, "vocabulary.json")
KEY_RECORDING_LOCK = os.path.join(DATA_DIR, ".key_recording")

DEFAULT_CONFIG = {
    "toggle_hotkey": "windows+alt+h",
    "push_to_talk_hotkey": "windows+alt",
    "hotkey": "windows+alt+h",
    "dictation_mode": "both",
    "enable_sound": True,
    "enable_context_cascade": True,
    "start_on_login": True,
    "user_name": "Copy",
    "auto_paste": True,
    "enable_ai_polish": False,
    "ai_model": "MODEL_PLACEHOLDER_M318",
    "ai_system_prompt": "You are an expert voice dictation assistant. Clean up grammatical slips, remove stuttering, filler words (um, uh, repeats), and fix punctuation. Preserve the exact meaning, technical terms, and speaker tone. Return ONLY the final polished text with no surrounding quotes, markdown fences, preamble, or commentary."
}

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RECORDINGS_DIR, exist_ok=True)

    # Automatic seamless migration from local data dir to %APPDATA% if needed
    if DATA_DIR != LOCAL_DATA and os.path.exists(LOCAL_DATA):
        import shutil
        local_hist = os.path.join(LOCAL_DATA, "history.json")
        if os.path.exists(local_hist) and not os.path.exists(HISTORY_PATH):
            try:
                shutil.copy2(local_hist, HISTORY_PATH)
            except Exception:
                pass
        local_cfg = os.path.join(LOCAL_DATA, "config.json")
        if os.path.exists(local_cfg) and not os.path.exists(CONFIG_PATH):
            try:
                shutil.copy2(local_cfg, CONFIG_PATH)
            except Exception:
                pass
        local_vocab = os.path.join(LOCAL_DATA, "vocabulary.json")
        if os.path.exists(local_vocab) and not os.path.exists(VOCAB_PATH):
            try:
                shutil.copy2(local_vocab, VOCAB_PATH)
            except Exception:
                pass
        local_recs = os.path.join(LOCAL_DATA, "recordings")
        if os.path.exists(local_recs):
            for f in os.listdir(local_recs):
                src = os.path.join(local_recs, f)
                dst = os.path.join(RECORDINGS_DIR, f)
                if os.path.isfile(src) and not os.path.exists(dst):
                    try:
                        shutil.copy2(src, dst)
                    except Exception:
                        pass

    if not os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)

def get_history(limit=500):
    init_db()
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
            return sorted(items, key=lambda x: x.get("id", 0), reverse=True)[:limit]
    except Exception:
        return []

def categorize_app(app_name: str) -> str:
    app_lower = (app_name or "").lower()
    if any(k in app_lower for k in ["code", "cursor", "devenv", "pycharm", "sublime", "terminal", "powershell", "cmd", "bash", "wt"]):
        return "AI Prompts"
    elif any(k in app_lower for k in ["slack", "teams", "discord", "zoom", "skype"]):
        return "Work Messages"
    elif any(k in app_lower for k in ["whatsapp", "telegram", "signal", "messenger"]):
        return "Personal Messages"
    elif any(k in app_lower for k in ["word", "notepad", "obsidian", "notion", "onenote", "acrobat", "excel"]):
        return "Documents"
    elif any(k in app_lower for k in ["outlook", "thunderbird", "mail"]):
        return "Emails"
    else:
        return "Other Tasks"

def estimate_fixes(raw_word_count: int, text: str) -> dict:
    """Estimates words corrected & punctuation/dictionary fixes formatted by Flow."""
    # Count punctuation characters, casing changes, etc.
    punct_count = sum(1 for c in text if c in '.,?!:;-"\'`()[]{}')
    capitalized_words = sum(1 for w in text.split() if w and w[0].isupper())
    return {
        "words_corrected": max(1, int(raw_word_count * 0.15) + capitalized_words),
        "dictionary_fixes": max(1, punct_count + int(raw_word_count * 0.08))
    }

def save_dictation(text: str, duration: float, app_name: str = "", pcm_bytes: bytes = None) -> dict:
    if not text.strip():
        return None
    init_db()
    items = []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        items = []

    new_id = (max([i.get("id", 0) for i in items]) + 1) if items else 1
    words = len(text.strip().split())
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    time_str = now.strftime("%I:%M%p").lower()
    if time_str.startswith("0"):
        time_str = time_str[1:]
    date_str = now.strftime("%Y-%m-%d")

    wpm = round((words / (duration / 60.0))) if duration > 0.5 else round(words * 20)
    category = categorize_app(app_name)
    fixes = estimate_fixes(words, text.strip())

    audio_file = ""
    if pcm_bytes:
        from crypto_audio import save_encrypted_recording
        enc_filename = f"{new_id}.enc"
        full_enc_path = os.path.join(RECORDINGS_DIR, enc_filename)
        save_encrypted_recording(pcm_bytes, full_enc_path)
        audio_file = f"recordings/{enc_filename}"

    entry = {
        "id": new_id,
        "timestamp": now_str,
        "time_str": time_str,
        "date_str": date_str,
        "text": text.strip(),
        "duration_seconds": round(duration, 2),
        "word_count": words,
        "wpm": wpm,
        "app_name": app_name or "Desktop",
        "category": category,
        "audio_file": audio_file,
        "words_corrected": fixes["words_corrected"],
        "dictionary_fixes": fixes["dictionary_fixes"]
    }
    items.append(entry)

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

    return entry

def update_dictation_text(item_id: int, new_text: str) -> dict:
    init_db()
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return None

    target = None
    for item in items:
        if item.get("id") == item_id:
            target = item
            break

    if target:
        target["text"] = new_text.strip()
        words = len(new_text.strip().split())
        target["word_count"] = words
        dur = target.get("duration_seconds", 1.0)
        target["wpm"] = round((words / (dur / 60.0))) if dur > 0.5 else 90
        fixes = estimate_fixes(words, new_text.strip())
        target["words_corrected"] = fixes["words_corrected"]
        target["dictionary_fixes"] = fixes["dictionary_fixes"]

        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)

    return target

def delete_dictation(item_id: int):
    init_db()
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return False

    new_items = []
    for item in items:
        if item.get("id") == item_id:
            audio_rel = item.get("audio_file")
            if audio_rel:
                full_path = os.path.join(DATA_DIR, audio_rel)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except Exception:
                        pass
        else:
            new_items.append(item)

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(new_items, f, indent=2, ensure_ascii=False)
    return True

def clear_all_history():
    init_db()
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump([], f)
    # clean recordings folder
    if os.path.exists(RECORDINGS_DIR):
        for fname in os.listdir(RECORDINGS_DIR):
            fpath = os.path.join(RECORDINGS_DIR, fname)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
    return True

def load_config():
    init_db()
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(cfg: dict):
    init_db()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

if __name__ == "__main__":
    init_db()
    print("Database & Config initialized.")
