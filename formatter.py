import json
import os
import re

try:
    from db import VOCAB_PATH, DATA_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, "data")
    VOCAB_PATH = os.path.join(DATA_DIR, "vocabulary.json")

DEFAULT_VOCABULARY = {
    # Developer & Cloud Acronyms
    "i d m point": "idempotent",
    "idm point": "idempotent",
    "ci cd": "CI/CD",
    "p ninety nine": "p99",
    "p 99": "p99",
    "vllm": "vLLM",
    "awq": "AWQ",
    "k8s": "K8s",
    "grpc": "gRPC",
    "pr": "PR",
    "prs": "PRs",
    "anti gravity": "Antigravity",
    "whisper flow": "Wispr Flow",
    "github actions": "GitHub Actions",
    "npm install": "npm install",
    "npm run dev": "npm run dev",
    "stripe": "Stripe",
    "joi": "Joi",
    "fast attention": "Flash Attention",
    "group query attention": "Grouped Query Attention",
    "fp8": "FP8",
    "b float16": "Bfloat16",
    "bfloat 16": "Bfloat16",
    "smx": "SXM",
    "h100": "H100",
    "h a r": "HAR",
    "har": "HAR",
    "hsr": "HAR",
    "llm": "LLM",
    "llms": "LLMs",
    "json": "JSON",
    "rest api": "REST API",
    "api": "API",
    "apis": "APIs",
    "http": "HTTP",
    "https": "HTTPS",
    "url": "URL",
    "urls": "URLs",
    "sdk": "SDK",
    "sdks": "SDKs",
    "ui": "UI",
    "gui": "GUI",
    "cli": "CLI",
    "ipc": "IPC",
    "sql": "SQL",
    "jwt": "JWT",
    "oauth": "OAuth"
}

def load_vocabulary() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(VOCAB_PATH):
        with open(VOCAB_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_VOCABULARY, f, indent=2)
        return DEFAULT_VOCABULARY.copy()
    try:
        with open(VOCAB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_VOCABULARY.copy()

def normalize_numbers_and_metrics(text: str) -> str:
    """Normalizes numbers, technical metrics, currencies, and dates."""
    # 1. Currencies: '99 point 95 dollars' or '99.95 dollars' -> '$99.95'
    text = re.sub(r'\b(\d+)\s*(?:point|\.)\s*(\d{2})\s*(?:dollars?|usd)\b', r'$\1.\2', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\d+(?:\.\d{1,2})?)\s*(?:dollars?|bucks)\b', r'$\1', text, flags=re.IGNORECASE)
    
    # 2. Latency & Time Metrics: '85 milliseconds' -> '85 ms'
    text = re.sub(r'\b(\d+(?:\.\d+)?)\s*(?:milliseconds?|millisecs?)\b', r'\1 ms', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\d+(?:\.\d+)?)\s*(?:seconds?|secs?)\b', r'\1 s', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\d+(?:\.\d+)?)\s*(?:minutes?|mins?)\b', r'\1 min', text, flags=re.IGNORECASE)

    # 3. Memory & Compute Units: '512 megabytes' -> '512 MB', '16 gigabytes' -> '16 GB'
    text = re.sub(r'\b(\d+)\s*(?:megabytes?|mb)\b', r'\1 MB', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\d+)\s*(?:gigabytes?|gb)\b', r'\1 GB', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(\d+)\s*(?:terabytes?|tb)\b', r'\1 TB', text, flags=re.IGNORECASE)

    # 4. Spoken quotes: 'in quotes <text>' -> '"<text>"'
    text = re.sub(r'\bin quotes\s+([^,.;\n]+)', r'"\1"', text, flags=re.IGNORECASE)

    # 5. Spoken code: 'in code <text>' -> '`<text>`'
    text = re.sub(r'\bin code\s+([^,.;\n]+)', r'`\1`', text, flags=re.IGNORECASE)

    return text

def apply_app_profile(text: str, category: str) -> str:
    """Applies context-sensitive styling depending on the active application."""
    if category == "AI Prompts":
        # Code & Terminal: Format API routes with HTTP verbs in backticks
        text = re.sub(r'(?<!`)\b(POST|GET|PUT|DELETE|PATCH)\s+(\/[a-zA-Z0-9_\-\/]+)\b(?!`)', r'`\1 \2`', text)
        # Standalone API endpoints
        text = re.sub(r'(?<!`)\b(\/(?:api|v\d+)\/[a-zA-Z0-9_\-\/]+)\b(?!`)', r'`\1`', text)
        # Wrap common CLI commands
        text = re.sub(r'(?<!`)\b(npm (?:install|run dev|test|start|build))\b(?!`)', r'`\1`', text)
        text = re.sub(r'(?<!`)\b(git (?:checkout|commit|push|pull|rebase|status))\b(?!`)', r'`\1`', text)
    elif category == "Documents":
        # Spoken bullet markers
        text = re.sub(r'\b(?:bullet point|bullet)\s*(?:\d+)?[:\s]+', '\n• ', text, flags=re.IGNORECASE)
    return text

def clean_punctuation_and_flow(text: str) -> str:
    """Fixes ASR artifacts: glued words, split questions, missing spaces after punctuation, and broken pause-periods."""
    # 1. Glued words (e.g. thatThis -> that this, ButCan -> But can)
    text = re.sub(r'([a-z])([A-Z][a-z])', r'\1 \2', text)
    # Lowercase second word if preceded by common conjunctions/relative pronouns
    text = re.sub(r'\b(that|which|who|if|as|like|because|then|and|but|or)\s+([A-Z][a-z]+)\b',
                  lambda m: f"{m.group(1)} {m.group(2).lower()}" if m.group(2) not in ['I', 'Google', 'Windows', 'Antigravity'] else m.group(0),
                  text)

    # 2. Fix split questions caused by acoustic pause before predicate/object
    # e.g., "Can I use this anti-gravity? API itself to get the results of the chat." -> "Can I use this anti-gravity API itself to get the results of the chat?"
    pattern_q = r'(\b(?:can|could|would|should|how|what|why|where|when|is|are|do|does|did)\b[^?!.]+?)\?\s+([A-Za-z0-9][^?!.]+?\.)'
    def repl_q(m):
        f2 = m.group(2).strip()
        if not re.match(r'^(Why|How|What|Where|When|Can|Could|Would|Should|Is|Are|Do|Does|Did)\b', f2, re.IGNORECASE):
            parts = f2.split(maxsplit=1)
            w0 = parts[0]
            rest = parts[1][:-1].strip() if len(parts) > 1 else ''
            if not w0.isupper():
                w0 = w0.lower()
            joined = f'{w0} {rest}'.strip() if rest else w0
            return f'{m.group(1)} {joined}?'
        return m.group(0)
    text = re.sub(pattern_q, repl_q, text, flags=re.IGNORECASE)

    # 3. Ensure space after punctuation if missing (e.g. "again.Out" -> "again. Out", "working?Like" -> "working? Like")
    text = re.sub(r'([.?!,;:])([A-Za-z0-9])', r'\1 \2', text)

    # 4. Remove space before punctuation (e.g. "word , " -> "word, ")
    text = re.sub(r'\s+([,.:;?!])', r'\1', text)

    # 5. Clean broken mid-clause pause periods (e.g. "like it is. Just. Messing" -> "like it is just messing")
    pattern_pause = r'\b(is|was|are|were|be|been|that|which|if|as|like|because|just|kind of)\.\s+([A-Za-z]+)'
    for _ in range(3):
        text = re.sub(pattern_pause, lambda m: f"{m.group(1)} {m.group(2).lower()}", text)

    # 6. Conjunction soft transitions (e.g. "broken. And some" -> "broken, and some")
    text = re.sub(r'\b([a-z0-9]+)\.\s+(and|or|so)\s+([a-z]+)', r'\1, \2 \3', text, flags=re.IGNORECASE)

    # 7. Fix multiple consecutive periods (except intentional ellipses)
    text = re.sub(r'\.{2,}', '...', text)

    # 8. Collapse extra spaces
    text = re.sub(r'[ \t]+', ' ', text)

    return text.strip()

def format_transcript(raw_text: str, category: str = "Other Tasks") -> str:
    """Master formatting pipeline:
    1. Punctuation & Flow smoothing (fixes ASR pause artifacts & spacing)
    2. Vocabulary & Technical Acronym mapping
    3. Smart number & metric normalization
    4. Application-specific profile rules
    """
    if not raw_text or not raw_text.strip():
        return raw_text

    text = raw_text.strip()

    # Step 1: Punctuation & Flow smoothing
    text = clean_punctuation_and_flow(text)

    # Step 2: Vocabulary mapping
    vocab = load_vocabulary()
    for phrase, target in vocab.items():
        pattern = r'\b' + re.escape(phrase) + r'\b'
        text = re.sub(pattern, target, text, flags=re.IGNORECASE)

    # Step 3: Numbers & Metrics normalization
    text = normalize_numbers_and_metrics(text)

    # Step 4: Application-specific profile
    text = apply_app_profile(text, category)

    return text.strip()
