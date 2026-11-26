import asyncio
import base64
import hashlib
import os
import json
import re
import time
import edge_tts

EDGE_VOICES = [
    {
        "id": "en-IN-NeerjaNeural",
        "name": "Neerja",
        "locale": "en-IN",
        "region": "India",
        "gender": "Female",
        "label": "🇮🇳 Neerja (English - India, Natural Female)",
        "sample": "Hello! I am Neerja, your conversational AI voice for India."
    },
    {
        "id": "en-IN-PrabhatNeural",
        "name": "Prabhat",
        "locale": "en-IN",
        "region": "India",
        "gender": "Male",
        "label": "🇮🇳 Prabhat (English - India, Natural Male)",
        "sample": "Hello! I am Prabhat. I will read your AI answers clearly."
    },
    {
        "id": "en-US-JennyNeural",
        "name": "Jenny",
        "locale": "en-US",
        "region": "United States",
        "gender": "Female",
        "label": "🇺🇸 Jenny (English - US, Natural Female)",
        "sample": "Hello there! I am Jenny, your friendly American assistant voice."
    },
    {
        "id": "en-US-GuyNeural",
        "name": "Guy",
        "locale": "en-US",
        "region": "United States",
        "gender": "Male",
        "label": "🇺🇸 Guy (English - US, Natural Male)",
        "sample": "Hey! I am Guy, ready to assist you with quick, clear spoken output."
    },
    {
        "id": "en-US-AriaNeural",
        "name": "Aria",
        "locale": "en-US",
        "region": "United States",
        "gender": "Female",
        "label": "🇺🇸 Aria (English - US, Expressive Female)",
        "sample": "Greetings! I am Aria, with rich and expressive neural speech."
    },
    {
        "id": "en-US-ChristopherNeural",
        "name": "Christopher",
        "locale": "en-US",
        "region": "United States",
        "gender": "Male",
        "label": "🇺🇸 Christopher (English - US, Deep Male)",
        "sample": "Hello. I am Christopher, providing professional and calm narration."
    },
    {
        "id": "en-GB-SoniaNeural",
        "name": "Sonia",
        "locale": "en-GB",
        "region": "United Kingdom",
        "gender": "Female",
        "label": "🇬🇧 Sonia (English - UK, British Female)",
        "sample": "Good day! I am Sonia, speaking in crisp British English."
    },
    {
        "id": "en-GB-RyanNeural",
        "name": "Ryan",
        "locale": "en-GB",
        "region": "United Kingdom",
        "gender": "Male",
        "label": "🇬🇧 Ryan (English - UK, British Male)",
        "sample": "Hello! I am Ryan. Ready to help you with your daily tasks."
    }
]

# In-memory LRU cache for audio snippets
_AUDIO_CACHE = {}

def normalize_rate(rate: str) -> str:
    """Normalizes user-supplied TTS rate strings (e.g. '100', '+100', '-20%') to valid Edge TTS '[+-]N%' format."""
    if rate is None:
        return "+0%"
    s = str(rate).strip().rstrip('%').strip()
    if not s:
        return "+0%"
    m = re.match(r'^([+-]?)(\d+(?:\.\d+)?)$', s)
    if not m:
        return "+0%"
    sign = m.group(1) or "+"
    val = int(round(float(m.group(2))))
    if sign == "-":
        val = min(80, val)
        return f"-{val}%" if val > 0 else "+0%"
    val = min(150, val)
    return f"+{val}%"

async def _synthesize_async(text: str, voice: str = "en-IN-NeerjaNeural", rate: str = "+0%", pitch: str = "+0Hz") -> bytes:
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    audio_data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])
    return bytes(audio_data)

def synthesize_text(text: str, voice: str = "en-IN-NeerjaNeural", rate: str = "+0%", pitch: str = "+0Hz") -> dict:
    if not text or not text.strip():
        return {"success": False, "error": "Empty text"}

    clean_text = text.strip()
    norm_voice = (voice or "en-IN-NeerjaNeural").strip()
    norm_rate = normalize_rate(rate)
    text_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()[:24]
    cache_key = f"{norm_voice}_{norm_rate}_{pitch}_{text_hash}"
    if cache_key in _AUDIO_CACHE:
        cached_entry = _AUDIO_CACHE[cache_key]
        if isinstance(cached_entry, dict):
            return {
                "success": True,
                "voice": norm_voice,
                "data_url": cached_entry.get("data_url", ""),
                "audio_b64": cached_entry.get("audio_b64", ""),
                "format": cached_entry.get("format", "audio/mp3"),
                "cached": True
            }
        else:
            b64_part = cached_entry.split(",")[-1] if "," in cached_entry else cached_entry
            return {
                "success": True,
                "voice": norm_voice,
                "data_url": cached_entry,
                "audio_b64": b64_part,
                "format": "audio/mp3",
                "cached": True
            }

    try:
        t0 = time.time()
        spoken_text = clean_text_for_speech(clean_text)
        if not spoken_text:
            spoken_text = clean_text

        raw_mp3 = asyncio.run(_synthesize_async(spoken_text, voice=norm_voice, rate=norm_rate, pitch=pitch))
        if not raw_mp3:
            return {"success": False, "error": "Empty audio generated"}

        b64 = base64.b64encode(raw_mp3).decode("ascii")
        data_url = f"data:audio/mp3;base64,{b64}"

        if len(clean_text) < 250 and len(_AUDIO_CACHE) < 60:
            _AUDIO_CACHE[cache_key] = {
                "data_url": data_url,
                "audio_b64": b64,
                "format": "audio/mp3"
            }

        return {
            "success": True,
            "voice": norm_voice,
            "rate": norm_rate,
            "data_url": data_url,
            "audio_b64": b64,
            "format": "audio/mp3",
            "bytes_length": len(raw_mp3),
            "duration_sec": round(time.time() - t0, 2)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_demo_audio(voice: str = "en-IN-NeerjaNeural", rate: str = "+0%") -> dict:
    matched = next((v for v in EDGE_VOICES if v["id"] == voice), None)
    if not matched:
        matched = EDGE_VOICES[0]
    sample_text = matched["sample"]
    return synthesize_text(sample_text, voice=matched["id"], rate=rate)

def get_voices_catalog() -> list:
    return EDGE_VOICES

def clean_text_for_speech(text: str) -> str:
    """Prepares markdown, math, and code blocks for fluid natural spoken output."""
    s = re.sub(r'```[\s\S]*?```', ' ', text)
    s = re.sub(r'`([^`]+)`', r'\1', s)
    s = re.sub(r'\$\$[\s\S]*?\$\$', ' ', s)
    s = re.sub(r'\$([^\$]+)\$', r'\1', s)
    s = re.sub(r'\\\[[\s\S]*?\\\]', ' ', s)
    s = re.sub(r'\\\([^\)]*?\\\)', ' ', s)
    s = re.sub(r'https?://\S+', ' ', s)
    s = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', s)
    s = re.sub(r'^[#>\s*+-]+', ' ', s, flags=re.MULTILINE)
    s = re.sub(r'(\*\*|__)(.*?)\1', r'\2', s)
    s = re.sub(r'(\*|_)(.*?)\1', r'\2', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

if __name__ == "__main__":
    res = get_demo_audio("en-IN-NeerjaNeural")
    print("Demo synthesized:", res.get("success"), "Length:", len(res.get("data_url", "")))
