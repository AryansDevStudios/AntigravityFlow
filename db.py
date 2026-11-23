import json
import os
import re
import sqlite3
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
USER_CACHE_PATH = os.path.join(DATA_DIR, "user_cache.json")
DB_PATH = os.path.join(DATA_DIR, "flow.db")
KEY_RECORDING_LOCK = os.path.join(DATA_DIR, ".key_recording")

from prompts import (
    PROMPT_AUTHENTIC_FLOW, PROMPT_DEFAULT_CUSTOM,
    PROMPT_ASSISTANT_GENERAL, PROMPT_ASSISTANT_CUSTOM
)

DEFAULT_CONFIG = {
    "toggle_hotkey": "windows+alt+h",
    "push_to_talk_hotkey": "windows+alt",
    "paste_last_hotkey": "alt+v",
    "hotkey": "windows+alt+h",
    "dictation_mode": "both",
    "enable_sound": True,
    "enable_context_cascade": True,
    "start_on_login": True,
    "user_name": "",
    "auto_paste": True,
    "enable_ai_polish": False,
    "ai_model": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE",
    "ai_preset_mode": "flow",
    "ai_custom_prompt": PROMPT_DEFAULT_CUSTOM,
    "ai_system_prompt": PROMPT_AUTHENTIC_FLOW,
    "enable_deep_context": True,
    "ctx_include_window": True,
    "ctx_include_cursor": True,
    "ctx_include_focused_control": True,
    "ctx_include_field_content": True,
    "ctx_include_history": True,
    "ctx_max_field_words": 500,
    "ctx_history_limit": 5,
    "enable_ai_hotkey": True,
    "ai_ptt_hotkey": "windows+ctrl",
    "ai_toggle_hotkey": "windows+ctrl+h",
    "ai_hotkey": "windows+ctrl",
    "ai_hotkey_mode": "both",
    "enable_system_commands": True,
    "enable_screen_vision": True,
    "ai_assistant_model": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    "ai_assistant_thinking_level": "high",
    "ai_assistant_mode": "general",
    "ai_assistant_custom_prompt": PROMPT_ASSISTANT_CUSTOM,
    "ai_assistant_system_prompt": PROMPT_ASSISTANT_GENERAL,
    "tts_service": "edge_tts",
    "tts_voice": "en-IN-NeerjaNeural",
    "tts_enabled": True,
    "tts_rate": "+0%",
    "tts_auto_speak_voice": True,
    "theme": "system",
    "screen_native_resolution": True
}

def get_db():
    """Returns a high-concurrency SQLite connection with WAL mode enabled."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RECORDINGS_DIR, exist_ok=True)

    # 1. Create SQLite Schema
    conn = get_db()
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS dictations (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            time_str TEXT,
            date_str TEXT,
            text TEXT NOT NULL,
            duration_seconds REAL DEFAULT 0.0,
            word_count INTEGER DEFAULT 0,
            wpm INTEGER DEFAULT 0,
            app_name TEXT DEFAULT 'Desktop',
            window_title TEXT DEFAULT '',
            category TEXT DEFAULT 'AI Prompts',
            session_type TEXT DEFAULT 'dictate',
            audio_file TEXT DEFAULT '',
            words_corrected INTEGER DEFAULT 0,
            dictionary_fixes INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dictations_id ON dictations(id DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dictations_date ON dictations(date_str);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dictations_cat ON dictations(category);")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            user_name TEXT DEFAULT '',
            user_email TEXT DEFAULT '',
            plan_name TEXT DEFAULT '',
            plan_badge TEXT DEFAULT '',
            weekly_percent REAL DEFAULT 100.0,
            five_hour_percent REAL DEFAULT 100.0,
            weekly_reset TEXT DEFAULT 'Active',
            five_hour_reset TEXT DEFAULT 'Active',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.execute("""
        INSERT OR IGNORE INTO user_profile (id, user_name, user_email, plan_name, plan_badge)
        VALUES (1, '', '', '', '');
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS macros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_keyword TEXT UNIQUE NOT NULL,
            expansion_text TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'General',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_macros_trigger ON macros(trigger_keyword);")

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM macros")
        if cursor.fetchone()[0] == 0:
            default_macros = [
                ("macro standup", "## Daily Standup - {date}\n1. **Yesterday:** \n2. **Today:** \n3. **Blockers:** None", "Standup meeting format", "Work"),
                ("macro email", "Hi there,\n\nHope this email finds you well.\n\nBest regards,\n", "Professional email greeting & closing", "Communication"),
                ("macro date", "{date}", "Current ISO Date (YYYY-MM-DD)", "General"),
                ("macro time", "{time}", "Current Time (HH:MM)", "General"),
                ("macro meeting", "## Meeting Notes: {date} {time}\n- **Attendees:** \n- **Key Points:** \n- **Next Steps:**\n  - [ ] ", "Quick meeting notes template", "Work"),
                ("macro component", "import React from 'react';\n\nexport const MyComponent = () => {\n  return (\n    <div className=\"p-4\">\n      {cursor}\n    </div>\n  );\n};\n", "React component snippet", "Coding"),
                ("macro clip", "{clipboard}", "Paste current clipboard buffer", "Utilities")
            ]
            for trig, exp, desc, cat in default_macros:
                conn.execute(
                    "INSERT OR IGNORE INTO macros (trigger_keyword, expansion_text, description, category) VALUES (?, ?, ?, ?)",
                    (trig, exp, desc, cat)
                )

    # 2. Seamless Auto-Migration from history.json into SQLite if database is fresh
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM dictations")
    db_count = cursor.fetchone()[0]

    if db_count == 0 and os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                items = json.load(f)
            with conn:
                for it in items:
                    conn.execute("""
                    INSERT OR REPLACE INTO dictations (
                        id, timestamp, time_str, date_str, text, duration_seconds, word_count, wpm,
                        app_name, window_title, category, session_type, audio_file, words_corrected, dictionary_fixes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        it.get("id"), it.get("timestamp", ""), it.get("time_str", ""), it.get("date_str", ""),
                        it.get("text", ""), it.get("duration_seconds", 0.0), it.get("word_count", 0),
                        it.get("wpm", 0), it.get("app_name", "Desktop"), it.get("window_title", ""),
                        it.get("category", "AI Prompts"), it.get("session_type", "dictate"),
                        it.get("audio_file", ""), it.get("words_corrected", 0), it.get("dictionary_fixes", 0)
                    ))
            print(f"[DB] Successfully migrated {len(items)} dictations into SQLite flow.db!")
        except Exception as e:
            print(f"[DB] Migration notice: {e}")
    conn.close()

    # 3. Ensure fast user cache exists on disk for instant Electron loading
    if not os.path.exists(USER_CACHE_PATH):
        try:
            with open(USER_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "user_name": "",
                    "user_email": "",
                    "plan_name": "",
                    "plan_badge": "",
                    "weekly_percent": 100.0,
                    "five_hour_percent": 100.0,
                    "weekly_reset": "Active",
                    "five_hour_reset": "Active"
                }, f, indent=2)
        except Exception:
            pass

def sync_history_json():
    """Syncs the latest SQLite dictations to history.json for fast external readers."""
    try:
        items = get_history(limit=500)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def get_history(limit=500) -> list:
    init_db()
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT id, timestamp, time_str, date_str, text, duration_seconds, word_count, wpm,
               app_name, window_title, category, session_type, audio_file, words_corrected, dictionary_fixes
        FROM dictations
        ORDER BY id DESC
        LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        items = [dict(r) for r in rows]
        conn.close()
        return items
    except Exception as e:
        print(f"[DB] get_history error: {e}")
        # Fallback to history.json if SQLite query fails
        if os.path.exists(HISTORY_PATH):
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)[:limit]
            except Exception:
                pass
        return []

def get_user_profile() -> dict:
    """Returns cached user identity and plan details in <1ms."""
    if os.path.exists(USER_CACHE_PATH):
        try:
            with open(USER_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "user_name": "",
        "user_email": "",
        "plan_name": "",
        "plan_badge": ""
    }

def save_user_profile(user_data: dict):
    """Saves user identity and plan details to SQLite and disk cache."""
    init_db()
    try:
        conn = get_db()
        with conn:
            conn.execute("""
            UPDATE user_profile SET
                user_name = ?,
                user_email = ?,
                plan_name = ?,
                plan_badge = ?,
                weekly_percent = ?,
                five_hour_percent = ?,
                weekly_reset = ?,
                five_hour_reset = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """, (
                user_data.get("name") or user_data.get("user_name", ""),
                user_data.get("email") or user_data.get("user_email", ""),
                user_data.get("planName") or user_data.get("plan_name", ""),
                user_data.get("planBadge") or user_data.get("plan_badge", ""),
                float(user_data.get("weekly_percent", 100.0)),
                float(user_data.get("five_hour_percent", 100.0)),
                str(user_data.get("weekly_reset", "Soon")),
                str(user_data.get("five_hour_reset", "Soon"))
            ))
        conn.close()
    except Exception as e:
        print(f"[DB] save_user_profile error: {e}")

    try:
        with open(USER_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(user_data, f, indent=2)
    except Exception:
        pass

def categorize_app(app_name: str, window_title: str = "", text: str = "") -> str:
    app_lower = (app_name or "").lower()
    win_lower = (window_title or "").lower()
    text_lower = (text or "").lower()

    # 1. AI Prompts (Coding IDEs, AI tools, LLM web interfaces, coding terminals)
    ai_app_keywords = ["antigravity", "cursor", "code", "devenv", "pycharm", "sublime", "terminal", "powershell", "cmd", "bash", "wt", "craft-anything", "idea", "clion", "webstorm"]
    ai_win_keywords = ["chatgpt", "claude", "gemini", "perplexity", "copilot", "huggingface", "poe", "notebook", "jupyter", "colab", "github", "gitlab", "stackoverflow", "antigravity"]
    ai_text_patterns = [
        r"^(explain|write|generate|how to|can you|create|what is|refactor|debug|fix|prompt|build|implement)\b",
        r"\b(def |function |const |let |var |class |import |return |select |from |where |docker|kubernetes|k8s|api|sql|json|html|css|bash)\b"
    ]
    if any(k in app_lower for k in ai_app_keywords):
        return "AI Prompts"
    if any(k in win_lower for k in ai_win_keywords):
        return "AI Prompts"
    if any(re.search(p, text_lower) for p in ai_text_patterns):
        return "AI Prompts"

    # 2. Emails (Desktop mail clients, webmail)
    email_app_keywords = ["outlook", "thunderbird", "mailbird", "mail"]
    email_win_keywords = ["gmail", "inbox", "mail", "outlook", "proton", "yahoo", "fastmail"]
    email_text_patterns = [
        r"\b(dear|hello|hi team|regards|best regards|sincerely|subject:|please find attached|kindly review)\b"
    ]
    if any(k in app_lower for k in email_app_keywords):
        return "Emails"
    if any(k in win_lower for k in email_win_keywords):
        return "Emails"
    if any(re.search(p, text_lower) for p in email_text_patterns):
        return "Emails"

    # 3. Work Messages (Enterprise chat, project management)
    work_app_keywords = ["slack", "teams", "zoom", "skype", "webex", "mattermost", "chime"]
    work_win_keywords = ["slack", "teams", "jira", "linear", "confluence", "asana", "trello", "monday.com"]
    work_text_patterns = [
        r"\b(sprint|standup|ticket|pr\b|pull request|blocker|jira|linear|deployment|release|roadmap)\b"
    ]
    if any(k in app_lower for k in work_app_keywords):
        return "Work Messages"
    if any(k in win_lower for k in work_win_keywords):
        return "Work Messages"
    if any(re.search(p, text_lower) for p in work_text_patterns):
        return "Work Messages"

    # 4. Personal Messages (Social, messaging, chat)
    personal_app_keywords = ["whatsapp", "telegram", "signal", "messenger", "discord"]
    personal_win_keywords = ["whatsapp", "telegram", "discord", "reddit", "twitter", "x.com", "instagram", "facebook", "messages"]
    personal_text_patterns = [
        r"\b(hey|yo|bro|sup|see you|cool|haha|lol|tomorrow|tonight|party|dinner|movie|weekend)\b"
    ]
    if any(k in app_lower for k in personal_app_keywords):
        return "Personal Messages"
    if any(k in win_lower for k in personal_win_keywords):
        return "Personal Messages"
    if any(re.search(p, text_lower) for p in personal_text_patterns):
        return "Personal Messages"

    # 5. Documents (Word processors, note apps, PDFs, spreadsheets)
    doc_app_keywords = ["word", "winword", "notepad", "notepad++", "obsidian", "notion", "onenote", "acrobat", "excel", "powerpnt"]
    doc_win_keywords = ["google docs", "google sheets", "google slides", "docs.google", "sheets.google", "pdf", "readme", "document", "notes"]
    if any(k in app_lower for k in doc_app_keywords):
        return "Documents"
    if any(k in win_lower for k in doc_win_keywords):
        return "Documents"
    if len(text.split()) > 35:
        return "Documents"

    return "Other Tasks"

def estimate_fixes(raw_word_count: int, text: str) -> dict:
    """Estimates words corrected & punctuation/dictionary fixes formatted by Flow."""
    punct_count = sum(1 for c in text if c in '.,?!:;-"\'`()[]{}')
    capitalized_words = sum(1 for w in text.split() if w and w[0].isupper())
    return {
        "words_corrected": max(1, int(raw_word_count * 0.15) + capitalized_words),
        "dictionary_fixes": max(1, punct_count + int(raw_word_count * 0.08))
    }

def save_dictation(text: str, duration: float, app_name: str = "", pcm_bytes: bytes = None, window_title: str = "", session_type: str = "dictate") -> dict:
    if not text.strip():
        return None
    init_db()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM dictations")
    new_id = cursor.fetchone()[0]

    words = len(text.strip().split())
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    time_str = now.strftime("%I:%M%p").lower()
    if time_str.startswith("0"):
        time_str = time_str[1:]
    date_str = now.strftime("%Y-%m-%d")

    wpm = round((words / (duration / 60.0))) if duration > 0.5 else round(words * 20)
    category = "AI Prompts" if session_type == "ai" else categorize_app(app_name, window_title, text.strip())
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
        "window_title": window_title or "",
        "category": category,
        "session_type": session_type,
        "audio_file": audio_file,
        "words_corrected": fixes["words_corrected"],
        "dictionary_fixes": fixes["dictionary_fixes"]
    }

    with conn:
        conn.execute("""
        INSERT INTO dictations (
            id, timestamp, time_str, date_str, text, duration_seconds, word_count, wpm,
            app_name, window_title, category, session_type, audio_file, words_corrected, dictionary_fixes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry["id"], entry["timestamp"], entry["time_str"], entry["date_str"], entry["text"],
            entry["duration_seconds"], entry["word_count"], entry["wpm"], entry["app_name"],
            entry["window_title"], entry["category"], entry["session_type"], entry["audio_file"],
            entry["words_corrected"], entry["dictionary_fixes"]
        ))
    conn.close()

    # Background async sync to history.json so file readers stay up to date
    sync_history_json()
    return entry

def update_dictation_text(item_id: int, new_text: str) -> dict:
    init_db()
    words = len(new_text.strip().split())
    fixes = estimate_fixes(words, new_text.strip())

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT duration_seconds FROM dictations WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    dur = row[0] or 1.0
    wpm = round((words / (dur / 60.0))) if dur > 0.5 else 90

    with conn:
        conn.execute("""
        UPDATE dictations SET
            text = ?,
            word_count = ?,
            wpm = ?,
            words_corrected = ?,
            dictionary_fixes = ?
        WHERE id = ?
        """, (new_text.strip(), words, wpm, fixes["words_corrected"], fixes["dictionary_fixes"], item_id))
    conn.close()

    sync_history_json()
    return {"id": item_id, "text": new_text.strip(), "word_count": words, "wpm": wpm}

def delete_dictation(item_id: int) -> bool:
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT audio_file FROM dictations WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if row and row[0]:
        audio_rel = row[0]
        full_path = os.path.join(DATA_DIR, audio_rel)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception:
                pass

    with conn:
        conn.execute("DELETE FROM dictations WHERE id = ?", (item_id,))
    conn.close()

    sync_history_json()
    return True

def clear_all_history() -> bool:
    init_db()
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM dictations;")
    conn.close()

    if os.path.exists(RECORDINGS_DIR):
        for fname in os.listdir(RECORDINGS_DIR):
            fpath = os.path.join(RECORDINGS_DIR, fname)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass

    sync_history_json()
    return True

def get_macros() -> list:
    """Returns all saved speech macros and snippets."""
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, trigger_keyword, expansion_text, description, category, created_at FROM macros ORDER BY trigger_keyword ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def save_macro(trigger_keyword: str, expansion_text: str, description: str = "", category: str = "General", macro_id: int = None) -> dict:
    """Creates or updates a speech macro snippet."""
    init_db()
    trig = trigger_keyword.strip().lower()
    if not trig or not expansion_text:
        return {"success": False, "error": "Trigger and expansion text are required"}
    conn = get_db()
    with conn:
        if macro_id:
            conn.execute("""
            UPDATE macros SET
                trigger_keyword = ?,
                expansion_text = ?,
                description = ?,
                category = ?
            WHERE id = ?
            """, (trig, expansion_text, description.strip(), category.strip(), int(macro_id)))
        else:
            conn.execute("""
            INSERT INTO macros (trigger_keyword, expansion_text, description, category)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(trigger_keyword) DO UPDATE SET
                expansion_text = excluded.expansion_text,
                description = excluded.description,
                category = excluded.category
            """, (trig, expansion_text, description.strip(), category.strip()))
    conn.close()
    return {"success": True, "trigger": trig}

def delete_macro(macro_id: int) -> bool:
    """Deletes a speech macro snippet by ID."""
    init_db()
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM macros WHERE id = ?", (macro_id,))
    conn.close()
    return True

def expand_macro_text(text: str) -> str:
    """Detects speech macro triggers in transcribed text and expands dynamic tags."""
    if not text or not text.strip():
        return text

    clean = text.strip()

    macros = get_macros()
    if not macros:
        return text

    # Longest trigger first to avoid substring collisions
    macros.sort(key=lambda m: len(m["trigger_keyword"]), reverse=True)

    matched_macro = None
    for m in macros:
        trig = m["trigger_keyword"].strip().lower()
        if not trig:
            continue
        # Check boundary match
        pattern = re.compile(r'\b' + re.escape(trig) + r'\b', re.IGNORECASE)
        if pattern.search(clean):
            matched_macro = m
            break

    if not matched_macro:
        return text

    expansion = matched_macro["expansion_text"]

    # Dynamic variable substitution
    now = datetime.now()
    expansion = expansion.replace("{date}", now.strftime("%Y-%m-%d"))
    expansion = expansion.replace("{time}", now.strftime("%H:%M"))
    expansion = expansion.replace("{datetime}", now.strftime("%Y-%m-%d %H:%M:%S"))

    if "{clipboard}" in expansion:
        clip_content = ""
        try:
            import pyperclip
            clip_content = pyperclip.paste() or ""
        except Exception:
            pass
        expansion = expansion.replace("{clipboard}", clip_content)

    expansion = expansion.replace("{cursor}", "")

    # If the user spoke solely the macro trigger (with possible trailing/leading punctuation)
    stripped_spoken = re.sub(r'^[.,!?;:\s]+|[.,!?;:\s]+$', '', clean).strip()
    if stripped_spoken.lower() == matched_macro["trigger_keyword"].strip().lower():
        return expansion

    # Replace trigger phrase in the spoken text preserving surrounding words
    trig_raw = matched_macro["trigger_keyword"].strip()
    pattern = re.compile(r'\b' + re.escape(trig_raw) + r'\b', re.IGNORECASE)
    expanded_result = pattern.sub(lambda _: expansion, clean, count=1).strip()
    return expanded_result if expanded_result else expansion

def load_config() -> dict:
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
    h = get_history(limit=5)
    print(f"SQLite flow.db active. Total latest records: {len(h)}")
