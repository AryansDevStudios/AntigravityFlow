import json
import os
import sys

import crypto_audio
import db
import batch_transcriber
import context_peeker
import formatter
import llm_refiner

def discover_server():
    return llm_refiner.discover_server()

def retranscribe(item_id: int):
    """Zero-Loss 1-Click Dashboard Retry (🔄 button) & Background Recovery.
    Uses unified single-shot batch transcription on decrypted audio buffer.
    """
    rec_path = os.path.join(db.RECORDINGS_DIR, f"{item_id}.enc")
    if not os.path.exists(rec_path):
        raise FileNotFoundError(f"Recording file not found for ID: {item_id}")

    pcm_data = crypto_audio.load_decrypted_pcm(rec_path)
    if not pcm_data:
        raise ValueError("Decrypted audio is empty")

    port, csrf_token = discover_server()
    cfg = db.load_config()

    # Determine context cascade if enabled
    cascade_id = ""
    if cfg.get("enable_context_cascade", True):
        cascade_id = context_peeker.get_active_cascade_id(port, csrf_token)

    # Determine app metadata from past record
    category = "Other Tasks"
    app_name = "Desktop"
    window_title = ""
    try:
        history = db.get_history()
        for h in history:
            if h.get("id") == item_id:
                category = h.get("category", "Other Tasks")
                app_name = h.get("app_name", "Desktop")
                window_title = h.get("window_title", "")
                break
    except Exception:
        pass

    # Batch transcription
    final_text = batch_transcriber.transcribe_audio_batch(
        pcm_data=pcm_data,
        port=port,
        csrf_token=csrf_token,
        cascade_id=cascade_id,
        category=category,
        timeout=8.0
    )

    if not final_text or not final_text.strip():
        raise RuntimeError("No transcription received from batch audio decoding.")

    final_text = formatter.format_transcript(final_text.strip(), category)

    # AI Polish pass if enabled
    if cfg.get("enable_ai_polish", False):
        try:
            preset_mode = cfg.get("ai_preset_mode", "flow")
            sys_prompt = cfg.get("ai_system_prompt", "") if preset_mode == "custom" else ""
            polished = llm_refiner.polish_with_gemini(
                final_text,
                model=cfg.get("ai_model", "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE"),
                preset_mode=preset_mode,
                system_prompt=sys_prompt,
                app_name=app_name,
                window_title=window_title,
                config=cfg
            )
            if polished and polished.strip():
                final_text = polished.strip()
        except Exception as err:
            print(f"[Retranscribe] AI Polish fallback: {err}")

    # Update database and return full updated row
    updated = db.update_dictation_text(item_id, final_text)
    return updated

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "Missing dictation ID argument"}))
        sys.exit(1)

    try:
        dict_id = int(sys.argv[1])
        updated_item = retranscribe(dict_id)
        print(json.dumps({"success": True, "data": updated_item}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
