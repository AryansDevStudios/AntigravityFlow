import base64
import http.client
import json
import ssl
import threading
import time
import psutil
import re
import urllib.request
import numpy as np

from accumulator import TranscriptAccumulator
from formatter import format_transcript

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def discover_server():
    server_proc = None
    for p in psutil.process_iter(['name', 'cmdline']):
        if p.info['name'] == 'language_server.exe':
            server_proc = p
            break

    if not server_proc:
        raise RuntimeError("Antigravity language_server.exe is not running.")

    cmd = ' '.join(server_proc.info['cmdline'] or [])
    token_match = re.search(r'--csrf_token\s+([a-f0-9-]+)', cmd)
    if not token_match:
        raise RuntimeError("Could not find CSRF token.")
    csrf_token = token_match.group(1)

    ports = []
    try:
        conns = [c for c in psutil.net_connections(kind='tcp') if c.pid == server_proc.pid and c.status == 'LISTEN']
        ports = [c.laddr.port for c in conns if c.laddr.ip in ('127.0.0.1', '::1', '0.0.0.0')]
    except Exception:
        pass

    if not ports:
        ports = [57071, 57072]

    active_port = None
    for port in ports:
        try:
            req = urllib.request.Request(
                f"https://127.0.0.1:{port}/",
                headers={"x-codeium-csrf-token": csrf_token}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=0.3) as res:
                if res.status == 200:
                    active_port = port
                    break
        except Exception:
            continue

    if not active_port:
        raise RuntimeError(f"Could not connect via HTTPS to any port: {ports}")

    return active_port, csrf_token

def make_grpc_web_frame(payload_bytes: bytes, flag: int = 0) -> bytes:
    """Encodes a payload into a gRPC-Web binary frame with flag and 4-byte big-endian length."""
    return bytes([flag]) + len(payload_bytes).to_bytes(4, byteorder="big") + payload_bytes

def parse_grpc_web_stream(stream):
    """Parses streaming gRPC-Web chunked frames from an HTTP response stream."""
    while True:
        try:
            header = stream.read(5)
            if not header or len(header) < 5:
                break
            flag = header[0]
            length = int.from_bytes(header[1:5], byteorder="big")
            payload = stream.read(length)
            if flag == 0:
                try:
                    yield json.loads(payload.decode("utf-8"))
                except Exception:
                    yield payload
            elif flag == 0x80:
                yield "TRAILER"
        except Exception:
            break

def find_audio_split_points(
    pcm_data: bytes,
    sample_rate: int = 16000,
    target_seg_sec: float = 24.0,
    min_seg_sec: float = 12.0
) -> list:
    """Splits long PCM audio buffers at natural silence valleys between sentences.
    
    Prevents Google Conformer acoustic engine from triggering silence timeouts
    during long dictations or natural pauses.
    """
    samples = np.frombuffer(pcm_data, dtype=np.int16)
    total_sec = len(samples) / sample_rate
    if total_sec <= target_seg_sec * 1.25:
        return [pcm_data]

    frame_len = int(sample_rate * 0.1) # 100ms analysis frame
    num_frames = len(samples) // frame_len
    if num_frames == 0:
        return [pcm_data]

    rms = np.zeros(num_frames, dtype=np.float32)
    for i in range(num_frames):
        chunk = samples[i * frame_len : (i + 1) * frame_len]
        rms[i] = np.sqrt(np.mean(chunk.astype(np.float32)**2))

    split_indices = []
    current_sample = 0
    total_samples = len(samples)
    min_samples = int(min_seg_sec * sample_rate)
    target_samples = int(target_seg_sec * sample_rate)

    while total_samples - current_sample > target_samples * 1.2:
        search_start = current_sample + min_samples
        search_end = min(total_samples, current_sample + target_samples + int(8.0 * sample_rate))
        start_frame = search_start // frame_len
        end_frame = min(num_frames, search_end // frame_len)
        if start_frame >= end_frame:
            break
        best_frame = start_frame + int(np.argmin(rms[start_frame:end_frame]))
        split_sample = best_frame * frame_len
        split_indices.append(split_sample)
        current_sample = split_sample

    segments = []
    prev = 0
    for s in split_indices:
        segments.append(pcm_data[prev * 2 : s * 2])
        prev = s
    segments.append(pcm_data[prev * 2 :])
    return segments

def _transcribe_single_segment(
    pcm_data: bytes,
    port: int,
    csrf_token: str,
    cascade_id: str = "",
    pre_cursor_text: str = "",
    timeout: float = 8.0
) -> str:
    """Transcribes an audio segment (<= 30s) in a single fast gRPC-Web burst."""
    if not pcm_data or len(pcm_data) < 1600:
        return ""

    headers = {
        "content-type": "application/grpc-web+json",
        "x-codeium-csrf-token": csrf_token,
        "x-grpc-web": "1",
        "Connection": "keep-alive"
    }

    # 1. Initialize transcription session with active cascadeId
    init_payload = {
        "mimeType": "audio/pcm;rate=16000",
        "preCursorText": pre_cursor_text or "",
        "postCursorText": "",
        "cascadeId": cascade_id or "",
        "continuous": True
    }
    init_bytes = make_grpc_web_frame(json.dumps(init_payload).encode("utf-8"))

    conn_stream = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=timeout)
    conn_stream.request(
        "POST",
        "/exa.language_server_pb.LanguageServerService/StreamAudioTranscription",
        body=init_bytes,
        headers=headers
    )
    res_stream = conn_stream.getresponse()

    session_id = None
    session_ready_event = threading.Event()
    complete_event = threading.Event()
    accumulator = TranscriptAccumulator()

    def stream_reader():
        nonlocal session_id
        for msg in parse_grpc_web_stream(res_stream):
            if isinstance(msg, dict):
                if "ready" in msg:
                    session_id = msg["ready"]["sessionId"]
                    session_ready_event.set()
                elif "transcription" in msg:
                    accumulator.process_message(msg)
                elif "complete" in msg:
                    accumulator.finalize_current()
                    complete_event.set()

    reader_thread = threading.Thread(target=stream_reader, daemon=True)
    reader_thread.start()

    # Wait for session readiness (< 1.5s)
    if not session_ready_event.wait(timeout=3.0) or not session_id:
        try:
            conn_stream.close()
        except Exception:
            pass
        return ""

    # 2. Rapid batch burst of audio chunks (16KB chunks ~ 512ms of audio per chunk)
    conn_sender = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=timeout)
    chunk_path = "/exa.language_server_pb.LanguageServerService/SendAudioChunk"
    chunk_size = 16384
    seq = 0

    for offset in range(0, len(pcm_data), chunk_size):
        chunk = pcm_data[offset:offset + chunk_size]
        body_dict = {
            "sessionId": session_id,
            "data": base64.b64encode(chunk).decode("ascii"),
            "sequenceNumber": seq
        }
        seq += 1
        frame = make_grpc_web_frame(json.dumps(body_dict).encode("utf-8"))
        conn_sender.request("POST", chunk_path, body=frame, headers=headers)
        conn_sender.getresponse().read()

    # 3. Comfort silence lookahead padding (320ms = 10240 bytes of 16-bit 16kHz silence)
    # Allows acoustic decoder to cleanly capture trailing syllables of the final word
    silence = b"\x00" * 10240
    silence_body = {
        "sessionId": session_id,
        "data": base64.b64encode(silence).decode("ascii"),
        "sequenceNumber": seq
    }
    frame_silence = make_grpc_web_frame(json.dumps(silence_body).encode("utf-8"))
    conn_sender.request("POST", chunk_path, body=frame_silence, headers=headers)
    conn_sender.getresponse().read()

    # 4. Dispatch EndAudioSession
    end_path = "/exa.language_server_pb.LanguageServerService/EndAudioSession"
    end_frame = make_grpc_web_frame(json.dumps({"sessionId": session_id}).encode("utf-8"))
    conn_sender.request("POST", end_path, body=end_frame, headers=headers)
    conn_sender.getresponse().read()
    conn_sender.close()

    # 5. Wait for engine to finish decoding
    complete_event.wait(timeout=timeout)
    try:
        conn_stream.close()
    except Exception:
        pass

    accumulator.finalize_current()
    return accumulator.get_full_text().strip()

def transcribe_audio_batch(
    pcm_data: bytes,
    port: int = None,
    csrf_token: str = None,
    cascade_id: str = "",
    pre_cursor_text: str = "",
    category: str = "General",
    timeout: float = 8.0
) -> str:
    """Performs zero-loss batch transcription of an entire recorded PCM buffer.
    
    Automatically segments long recordings (> 28s) at natural silence valleys
    to prevent Google's Conformer acoustic silence endpointer from dropping sentences.
    """
    if not pcm_data or len(pcm_data) < 1600:
        return ""

    if not port or not csrf_token:
        port, csrf_token = discover_server()

    # If audio is longer than 28 seconds (28 * 16000 * 2 = 896,000 bytes), segment it
    if len(pcm_data) > 896000:
        segments = find_audio_split_points(pcm_data)
        segment_texts = []
        for seg in segments:
            seg_text = _transcribe_single_segment(
                pcm_data=seg,
                port=port,
                csrf_token=csrf_token,
                cascade_id=cascade_id,
                pre_cursor_text=pre_cursor_text,
                timeout=timeout
            )
            if seg_text:
                segment_texts.append(seg_text)
        raw_text = " ".join(segment_texts).strip()
    else:
        raw_text = _transcribe_single_segment(
            pcm_data=pcm_data,
            port=port,
            csrf_token=csrf_token,
            cascade_id=cascade_id,
            pre_cursor_text=pre_cursor_text,
            timeout=timeout
        )

    if not raw_text:
        return ""

    return format_transcript(raw_text, category=category)

# Backwards compatible alias
def batch_transcribe_pcm(pcm_bytes: bytes, cascade_id: str = "", pre_cursor_text: str = "", timeout: float = 8.0) -> str:
    return transcribe_audio_batch(
        pcm_data=pcm_bytes,
        cascade_id=cascade_id,
        pre_cursor_text=pre_cursor_text,
        timeout=timeout
    )
