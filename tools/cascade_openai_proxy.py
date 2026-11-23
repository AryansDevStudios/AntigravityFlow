"""
Antigravity Cascade OpenAI-Compatible Proxy Server (Option B)
Zero-dependency implementation using Python's built-in HTTPServer & standard library.

Endpoints supported:
- GET  /v1/models
- POST /v1/chat/completions (OpenAI Chat + Multimodal Vision + Search Grounding)
- POST /v1/completions
- POST /v1/audio/transcriptions (Dual-mode: Google Cloud Native whole-file OR Streaming Conformer ASR)
- GET  /v1/search?q=<query> (Google Vertex AI Search Grounding API)
- POST /v1/search (Google Vertex AI Search Grounding API)
"""

import base64
import email
import http.client
from http.server import HTTPServer, BaseHTTPRequestHandler
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import uuid
import wave
import numpy as np
import psutil

# Ensure parent directory is in path for batch_transcriber utilities
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from batch_transcriber import transcribe_audio_batch

PORT = 8000

# Model mappings (OpenAI model name -> Antigravity internal enum)
MODEL_MAP = {
    # Fast sub-second models
    "gemini-2.5-flash-lite": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE",
    "gemini-3.1-flash-lite": "MODEL_PLACEHOLDER_M50",
    "gemini-3.5-flash-lite": "MODEL_PLACEHOLDER_M198",
    "gemini-2.5-flash": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    "gemini-3-flash": "MODEL_PLACEHOLDER_M18",
    "tab-autocomplete": "MODEL_PLACEHOLDER_M19",
    
    # Multimodal Vision models
    "gemini-3.1-flash-image": "MODEL_PLACEHOLDER_M21",
    "gemini-vision": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    "gpt-4-vision-preview": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    
    # Real-Time Search Grounded models
    "gemini-grounded": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    "gemini-search": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    
    # Audio Speech-to-Text models
    "whisper-1": "MODEL_GOOGLE_CLOUD_NATIVE_AUDIO",
    "gemini-audio": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE",
    "conformer": "MODEL_GOOGLE_CONFORMER_ASR",
    
    # Reasoning & UI models
    "gemini-3.8-flash-low": "MODEL_PLACEHOLDER_M320",
    "gemini-3.8-flash-medium": "MODEL_PLACEHOLDER_M319",
    "gemini-3.8-flash-high": "MODEL_PLACEHOLDER_M318",
    "gemini-3.8-flash": "MODEL_PLACEHOLDER_M318",
    "claude-sonnet-4-6": "MODEL_PLACEHOLDER_M35",
    "claude-opus-4-6": "MODEL_PLACEHOLDER_M26",
    "gpt-oss-120b": "MODEL_OPENAI_GPT_OSS_120B_MEDIUM",
    "gemini-4-hydrogen-sonar": "MODEL_PLACEHOLDER_M578",
    
    # Standard OpenAI aliases
    "gpt-4o-mini": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE",
    "gpt-4o": "MODEL_PLACEHOLDER_M35",
    "gpt-3.5-turbo": "MODEL_PLACEHOLDER_M50",
    "claude-3-5-sonnet": "MODEL_PLACEHOLDER_M35",
    "default": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE"
}

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

AUDIO_MIME_MAP = {
    '.wav': 'audio/wav',
    '.mp3': 'audio/mp3',
    '.m4a': 'audio/m4a',
    '.ogg': 'audio/ogg',
    '.flac': 'audio/flac',
    '.aac': 'audio/aac',
    '.webm': 'audio/webm'
}

def get_audio_mime(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return AUDIO_MIME_MAP.get(ext, 'audio/wav')

def convert_audio_to_pcm16_16k(audio_bytes: bytes) -> bytes:
    """Converts WAV or raw PCM audio to 16kHz 16-bit mono PCM for Conformer."""
    try:
        with wave.open(io.BytesIO(audio_bytes), 'rb') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            raw_frames = wf.readframes(wf.getnframes())

            if sampwidth == 2:
                samples = np.frombuffer(raw_frames, dtype=np.int16)
            elif sampwidth == 1:
                samples = (np.frombuffer(raw_frames, dtype=np.uint8).astype(np.int16) - 128) * 256
            elif sampwidth == 4:
                samples = (np.frombuffer(raw_frames, dtype=np.int32) >> 16).astype(np.int16)
            else:
                return raw_frames

            if n_channels > 1:
                samples = samples.reshape(-1, n_channels).mean(axis=1).astype(np.int16)

            if framerate != 16000 and framerate > 0:
                num_target_samples = int(len(samples) * 16000 / framerate)
                indices = np.linspace(0, len(samples) - 1, num_target_samples)
                samples = np.interp(indices, np.arange(len(samples)), samples).astype(np.int16)

            return samples.tobytes()
    except Exception:
        return audio_bytes


class AntigravitySessionManager:
    def __init__(self):
        self.port = 57071
        self.csrf_token = ""
        self.refresh_credentials()

    def refresh_credentials(self):
        server_proc = None
        for p in psutil.process_iter(['name', 'cmdline']):
            if p.info['name'] == 'language_server.exe':
                server_proc = p
                break
        if not server_proc:
            raise RuntimeError("language_server.exe is not running.")
        
        cmd = ' '.join(server_proc.info['cmdline'] or [])
        match = re.search(r'--csrf_token\s+([a-f0-9-]+)', cmd)
        if match:
            self.csrf_token = match.group(1)
            
        self.port = 57071
        try:
            conns = [c for c in psutil.net_connections(kind='tcp') if c.pid == server_proc.pid and c.status == 'LISTEN']
            ports = [c.laddr.port for c in conns if c.laddr.ip in ('127.0.0.1', '::1', '0.0.0.0')]
            if 57071 in ports:
                self.port = 57071
            elif ports:
                for p in ports:
                    try:
                        conn = http.client.HTTPSConnection('127.0.0.1', p, context=ctx, timeout=0.2)
                        conn.request('GET', '/')
                        res = conn.getresponse()
                        conn.close()
                        self.port = p
                        break
                    except Exception:
                        continue
        except Exception:
            self.port = 57071

    def call_rpc(self, method: str, payload: dict, timeout: float = 15.0) -> dict:
        body = json.dumps(payload).encode('utf-8')
        frame = bytes([0]) + len(body).to_bytes(4, 'big') + body
        headers = {
            'content-type': 'application/grpc-web+json',
            'x-codeium-csrf-token': self.csrf_token,
            'x-grpc-web': '1'
        }
        conn = http.client.HTTPSConnection('127.0.0.1', self.port, context=ctx, timeout=timeout)
        conn.request('POST', f'/exa.language_server_pb.LanguageServerService/{method}', body=frame, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if len(raw) >= 5:
            length = int.from_bytes(raw[1:5], 'big')
            return json.loads(raw[5:5+length].decode('utf-8'))
        return {}

    def save_media_artifact(self, mime_type: str, base64_data: str, display_name: str = "uploaded_media.bin") -> str:
        """Uploads media (image or audio file) to Antigravity internal storage and returns local URI."""
        resp = self.call_rpc('SaveMediaAsArtifact', {
            'media': {
                'mimeType': mime_type,
                'inlineData': base64_data,
                'displayName': display_name
            }
        })
        return resp.get('uri', '')

    def execute_search_grounding(self, query: str, timeout: float = 15.0) -> dict:
        """Executes Google Search Grounding via Vertex AI search provider."""
        cascade_id = str(uuid.uuid4())
        model_enum = "MODEL_GOOGLE_GEMINI_2_5_FLASH"

        self.call_rpc('StartCascade', {
            'source': 'CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT',
            'cascadeId': cascade_id,
            'requestedModel': model_enum,
            'projectEnvConfig': {'projectId': 'outside-of-project', 'defaultProjectEnvironment': {}}
        })

        self.call_rpc('SendUserCascadeMessage', {
            'cascadeId': cascade_id,
            'items': [{'text': f'Use search_web: {query}'}],
            'cascadeConfig': {
                'plannerConfig': {
                    'planModel': model_enum,
                    'useAiCredits': False
                }
            }
        })

        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(0.4)
            steps_data = self.call_rpc('GetCascadeTrajectorySteps', {'cascadeId': cascade_id})
            for s in steps_data.get('steps', []):
                if s.get('type') == 'CORTEX_STEP_TYPE_SEARCH_WEB' and s.get('status') == 'CORTEX_STEP_STATUS_DONE':
                    sw = s.get('searchWeb', {})
                    raw_summary = sw.get('summary', '')
                    
                    sources = []
                    for m in re.finditer(r'\[(\d+)\]\s*\[([^\]]+)\]\(([^)]+)\)', raw_summary):
                        sources.append({
                            "index": int(m.group(1)),
                            "title": m.group(2),
                            "url": m.group(3)
                        })
                    
                    return {
                        "query": sw.get('query', query),
                        "summary": raw_summary,
                        "sources": sources,
                        "provider": sw.get('thirdPartyConfig', {}).get('provider', 'GEMINI_VERTEX_SEARCH')
                    }
        return {"error": "Search timed out", "query": query, "sources": [], "summary": ""}

    def transcribe_file_cloud_native(self, audio_bytes: bytes, filename: str, prompt: str = "") -> str:
        """
        Mode A: Google Cloud Native Whole-File Transcription.
        Zero client-side CPU overhead: uploads raw audio container directly to Google backend.
        """
        mime_type = get_audio_mime(filename)
        audio_b64 = base64.b64encode(audio_bytes).decode('ascii')
        media_uri = self.save_media_artifact(mime_type, audio_b64, display_name=filename)
        if not media_uri:
            raise RuntimeError("Failed to stage audio file in Antigravity media storage.")

        inst = "Transcribe this audio file verbatim without adding any extra commentary, greetings, or explanations."
        if prompt:
            inst += f" Vocabulary or context hint: {prompt}"

        cascade_id = str(uuid.uuid4())
        model_enum = "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE"
        
        self.call_rpc('StartCascade', {
            'source': 'CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT',
            'cascadeId': cascade_id,
            'requestedModel': model_enum,
            'projectEnvConfig': {'projectId': 'outside-of-project', 'defaultProjectEnvironment': {}}
        })

        self.call_rpc('SendUserCascadeMessage', {
            'cascadeId': cascade_id,
            'items': [{'text': inst}],
            'media': [{'mimeType': mime_type, 'uri': media_uri}],
            'cascadeConfig': {
                'plannerConfig': {
                    'planModel': model_enum,
                    'useAiCredits': False
                }
            }
        })

        start_time = time.time()
        while time.time() - start_time < 20.0:
            time.sleep(0.3)
            steps_data = self.call_rpc('GetCascadeTrajectorySteps', {'cascadeId': cascade_id})
            for step in steps_data.get('steps', []):
                if step.get('type') == 'CORTEX_STEP_TYPE_PLANNER_RESPONSE':
                    if step.get('status') == 'CORTEX_STEP_STATUS_DONE':
                        p_resp = step.get('plannerResponse', {})
                        return (p_resp.get('modifiedResponse') or p_resp.get('response', '')).strip()

        return ""

    def transcribe_audio_streaming(self, pcm_bytes: bytes, prompt: str = "") -> str:
        """Mode B: Local streaming Google Conformer ASR engine."""
        return transcribe_audio_batch(
            pcm_data=pcm_bytes,
            port=self.port,
            csrf_token=self.csrf_token,
            pre_cursor_text=prompt
        )

    def execute_cascade_turn(self, model_enum: str, prompt: str, media_items: list = None, timeout: float = 20.0) -> tuple:
        cascade_id = str(uuid.uuid4())
        
        self.call_rpc('StartCascade', {
            'source': 'CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT',
            'cascadeId': cascade_id,
            'requestedModel': model_enum,
            'projectEnvConfig': {'projectId': 'outside-of-project', 'defaultProjectEnvironment': {}}
        })
        
        user_msg = {
            'cascadeId': cascade_id,
            'items': [{'text': prompt}],
            'cascadeConfig': {
                'plannerConfig': {
                    'planModel': model_enum,
                    'toolConfig': {'runCommand': {'autoCommandConfig': {'autoExecutionPolicy': 'CASCADE_COMMANDS_AUTO_EXECUTION_EAGER'}}},
                    'useAiCredits': False
                }
            }
        }
        
        if media_items:
            user_msg['media'] = media_items
        
        self.call_rpc('SendUserCascadeMessage', user_msg)
        
        start_time = time.time()
        final_text = ""
        final_thinking = ""
        p_tok, c_tok = 0, 0

        while time.time() - start_time < timeout:
            time.sleep(0.3)
            steps_data = self.call_rpc('GetCascadeTrajectorySteps', {'cascadeId': cascade_id})
            steps = steps_data.get('steps', [])
            for step in steps:
                if step.get('type') == 'CORTEX_STEP_TYPE_PLANNER_RESPONSE':
                    if step.get('status') == 'CORTEX_STEP_STATUS_DONE':
                        p_resp = step.get('plannerResponse', {})
                        resp_t = p_resp.get('modifiedResponse') or p_resp.get('response', '')
                        if resp_t:
                            final_text = resp_t
                            final_thinking = p_resp.get('thinkingSignature', '')
                            usage = step.get('metadata', {}).get('modelUsage', {})
                            p_tok = int(usage.get('inputTokens', 0))
                            c_tok = int(usage.get('outputTokens', 0))
            if final_text:
                return final_text, final_thinking, p_tok, c_tok
                        
        unary_resp = self.call_rpc('GetModelResponse', {'model': model_enum, 'prompt': prompt})
        return unary_resp.get('response', 'No response generated.'), '', 0, 0


session_mgr = AntigravitySessionManager()


class OpenAIProxyHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        # Endpoint: Models
        if parsed.path in ('/v1/models', '/models'):
            models_data = []
            created_ts = int(time.time())
            for alias in MODEL_MAP.keys():
                models_data.append({
                    "id": alias,
                    "object": "model",
                    "created": created_ts,
                    "owned_by": "antigravity"
                })
            self._send_json(200, {"object": "list", "data": models_data})
            return

        # Endpoint: Google Search Grounding via GET (/v1/search?q=...)
        if parsed.path in ('/v1/search', '/search'):
            query_params = urllib.parse.parse_qs(parsed.query)
            q = query_params.get('q', [''])[0] or query_params.get('query', [''])[0]
            if not q:
                self._send_json(400, {"error": {"message": "Query parameter 'q' or 'query' is required."}})
                return
            search_result = session_mgr.execute_search_grounding(q)
            self._send_json(200, search_result)
            return

        self._send_json(404, {"error": {"message": f"Endpoint not found: {self.path}"}})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        content_type = self.headers.get('Content-Type', '')
        content_len = int(self.headers.get('Content-Length', 0))
        post_body = self.rfile.read(content_len)

        # -------------------------------------------------------------
        # Endpoint: Google Search Grounding via POST (/v1/search)
        # -------------------------------------------------------------
        if parsed.path in ('/v1/search', '/search'):
            try:
                req_data = json.loads(post_body.decode('utf-8'))
                q = req_data.get('query') or req_data.get('q', '')
            except Exception:
                self._send_json(400, {"error": {"message": "Invalid JSON body"}})
                return
            if not q:
                self._send_json(400, {"error": {"message": "'query' or 'q' field is required."}})
                return
            search_result = session_mgr.execute_search_grounding(q)
            self._send_json(200, search_result)
            return

        # -------------------------------------------------------------
        # Endpoint 1: Audio Speech-to-Text (/v1/audio/transcriptions)
        # -------------------------------------------------------------
        if parsed.path in ('/v1/audio/transcriptions', '/audio/transcriptions'):
            if 'multipart/form-data' not in content_type:
                self._send_json(400, {"error": {"message": "Expected multipart/form-data with audio file"}})
                return

            try:
                msg = email.message_from_bytes(f"Content-Type: {content_type}\r\n\r\n".encode('latin-1') + post_body)
                fields = {}
                files = {}
                for part in msg.walk():
                    cd = part.get("Content-Disposition", "")
                    if "filename=" in cd:
                        name_match = re.search(r'name="([^"]+)"', cd)
                        filename_match = re.search(r'filename="([^"]+)"', cd)
                        field_name = name_match.group(1) if name_match else "file"
                        filename = filename_match.group(1) if filename_match else "audio.wav"
                        files[field_name] = (filename, part.get_payload(decode=True))
                    elif "form-data" in cd:
                        name_match = re.search(r'name="([^"]+)"', cd)
                        if name_match:
                            fields[name_match.group(1)] = part.get_payload(decode=True).decode('utf-8', errors='ignore').strip()

                audio_file_tuple = files.get('file') or files.get('audio')
                if not audio_file_tuple or not audio_file_tuple[1]:
                    self._send_json(400, {"error": {"message": "No audio file provided in 'file' parameter"}})
                    return

                filename, audio_bytes = audio_file_tuple
                prompt_hint = fields.get('prompt', '')
                requested_model = fields.get('model', 'whisper-1').lower()

                if requested_model in ('conformer', 'streaming'):
                    pcm_data = convert_audio_to_pcm16_16k(audio_bytes)
                    transcribed_text = session_mgr.transcribe_audio_streaming(pcm_data, prompt=prompt_hint)
                else:
                    transcribed_text = session_mgr.transcribe_file_cloud_native(audio_bytes, filename=filename, prompt=prompt_hint)

                resp_format = fields.get('response_format', 'json').lower()
                if resp_format == 'text':
                    body = transcribed_text.encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                self._send_json(200, {"text": transcribed_text})
                return
            except Exception as e:
                self._send_json(500, {"error": {"message": f"Transcription error: {str(e)}"}})
                return

        # -------------------------------------------------------------
        # Endpoint 2: Chat, Multimodal Vision, and Search Grounding
        # -------------------------------------------------------------
        if parsed.path in ('/v1/chat/completions', '/chat/completions', '/v1/completions'):
            try:
                req_data = json.loads(post_body.decode('utf-8'))
            except Exception:
                self._send_json(400, {"error": {"message": "Invalid JSON"}})
                return
                
            req_model = req_data.get('model', 'default')
            model_enum = MODEL_MAP.get(req_model, req_model)
            
            messages = req_data.get('messages', [])
            media_items = []
            
            if messages:
                sys_msgs = []
                user_msgs = []
                for m in messages:
                    role = m.get('role')
                    content = m.get('content')
                    if role == 'system':
                        if isinstance(content, str):
                            sys_msgs.append(content)
                    elif role == 'user':
                        if isinstance(content, str):
                            user_msgs.append(content)
                        elif isinstance(content, list):
                            text_parts = []
                            for part in content:
                                if isinstance(part, dict):
                                    if part.get('type') == 'text':
                                        text_parts.append(part.get('text', ''))
                                    elif part.get('type') == 'image_url':
                                        url_info = part.get('image_url', {})
                                        url_str = url_info.get('url', '')
                                        if url_str.startswith('data:'):
                                            match = re.match(r'data:(image/[a-zA-Z0-9_\-\.]+);base64,(.+)', url_str)
                                            if match:
                                                mime = match.group(1)
                                                b64 = match.group(2)
                                                media_uri = session_mgr.save_media_artifact(mime, b64)
                                                if media_uri:
                                                    media_items.append({'mimeType': mime, 'uri': media_uri})
                                        elif os.path.isfile(url_str):
                                            mime = 'image/jpeg' if url_str.lower().endswith(('.jpg', '.jpeg')) else 'image/png'
                                            with open(url_str, 'rb') as f:
                                                b64 = base64.b64encode(f.read()).decode('ascii')
                                            media_uri = session_mgr.save_media_artifact(mime, b64)
                                            if media_uri:
                                                media_items.append({'mimeType': mime, 'uri': media_uri})
                            if text_parts:
                                user_msgs.append(' '.join(text_parts))

                last_user = user_msgs[-1] if user_msgs else ''
                full_prompt = f"System:\n{' '.join(sys_msgs)}\n\nUser:\n{last_user}" if sys_msgs else last_user
            else:
                full_prompt = req_data.get('prompt', '')
                
            # Check for Search Grounded model / flag
            is_grounded = req_model in ('gemini-grounded', 'gemini-search') or req_data.get('web_search', False)
            if is_grounded:
                # Add instruction to trigger search_web tool
                full_prompt = f"Use search_web to find real-time live web info and answer: {full_prompt}"
                model_enum = "MODEL_GOOGLE_GEMINI_2_5_FLASH"

            if media_items and model_enum in ("MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE", "MODEL_PLACEHOLDER_M50"):
                model_enum = "MODEL_GOOGLE_GEMINI_2_5_FLASH"

            try:
                resp_text, thinking_sig, p_tok, c_tok = session_mgr.execute_cascade_turn(
                    model_enum, full_prompt, media_items=media_items
                )
            except Exception as e:
                self._send_json(502, {"error": {"message": str(e)}})
                return
                
            msg_payload = {
                "role": "assistant",
                "content": resp_text
            }
            if thinking_sig:
                msg_payload["reasoning_content"] = thinking_sig
                msg_payload["thinking_signature"] = thinking_sig

            res_payload = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": req_model,
                "choices": [
                    {
                        "index": 0,
                        "message": msg_payload,
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": p_tok,
                    "completion_tokens": c_tok,
                    "total_tokens": p_tok + c_tok
                }
            }
            self._send_json(200, res_payload)
            return

        self._send_json(404, {"error": {"message": f"Endpoint not found: {self.path}"}})


def run():
    sys.stdout.reconfigure(encoding='utf-8')
    server = HTTPServer(('127.0.0.1', PORT), OpenAIProxyHandler)
    print(f"[*] Antigravity Option B Proxy running on http://127.0.0.1:{PORT}")
    print(f"[*] Chat / Vision / Grounding: http://127.0.0.1:{PORT}/v1/chat/completions")
    print(f"[*] Search Grounding API:      http://127.0.0.1:{PORT}/v1/search")
    print(f"[*] Audio ASR endpoint:        http://127.0.0.1:{PORT}/v1/audio/transcriptions")
    print(f"[*] Models list:               http://127.0.0.1:{PORT}/v1/models")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == '__main__':
    run()
