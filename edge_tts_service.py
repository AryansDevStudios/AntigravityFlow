import asyncio
import base64
import os
import json
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
    cache_key = f"{voice}_{rate}_{pitch}_{clean_text[:120]}"
    if cache_key in _AUDIO_CACHE:
        return {
            "success": True,
            "voice": voice,
            "data_url": _AUDIO_CACHE[cache_key],
            "cached": True
        }

    try:
        t0 = time.time()
        # Clean markdown code blocks and LaTeX math before speaking so it sounds natural
        spoken_text = clean_text_for_speech(clean_text)
        if not spoken_text:
            spoken_text = clean_text

        raw_mp3 = asyncio.run(_synthesize_async(spoken_text, voice=voice, rate=rate, pitch=pitch))
        if not raw_mp3:
            return {"success": False, "error": "Empty audio generated"}

        b64 = base64.b64encode(raw_mp3).decode("ascii")
        data_url = f"data:audio/mp3;base64,{b64}"
        
        # Cache small snippets (under 200 chars)
        if len(clean_text) < 200 and len(_AUDIO_CACHE) < 50:
            _AUDIO_CACHE[cache_key] = data_url

        return {
            "success": True,
            "voice": voice,
            "data_url": data_url,
            "bytes_length": len(raw_mp3),
            "duration_sec": round(time.time() - t0, 2)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_demo_audio(voice: str = "en-IN-NeerjaNeural") -> dict:
    matched = next((v for v in EDGE_VOICES if v["id"] == voice), None)
    if not matched:
        matched = EDGE_VOICES[0]
    sample_text = matched["sample"]
    return synthesize_text(sample_text, voice=matched["id"])

def get_voices_catalog() -> list:
    return EDGE_VOICES

def clean_text_for_speech(text: str) -> str:
    """Prepares markdown, math, and code blocks for fluid natural spoken output."""
    import re
    # Remove code blocks ```...``` -> say "code snippet"
    s = re.sub(r'```[a-zA-Z]*\n[\s\S]*?```', ' [code snippet omitted] ', text)
    # Remove inline code `...`
    s = re.sub(r'`([^`]+)`', r'\1', s)
    # Remove math $$...$$ and $...$
    s = re.sub(r'\$\$[\s\S]*?\$\$', ' [equation] ', s)
    s = re.sub(r'\$([^\$]+)\$', r'\1', s)
    # Remove markdown links [text](url) -> text
    s = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', s)
    # Remove markdown headers #, ##, ###
    s = re.sub(r'#+\s*', '', s)
    # Remove bold/italic **text** or *text*
    s = re.sub(r'\*\*([^*]+)\*\*', r'\1', s)
    s = re.sub(r'\*([^*]+)\*', r'\1', s)
    # Remove multiple spaces
    s = re.sub(r'\s+', ' ', s).strip()
    return s

if __name__ == "__main__":
    res = get_demo_audio("en-IN-NeerjaNeural")
    print("Demo synthesized:", res.get("success"), "Length:", len(res.get("data_url", "")))
