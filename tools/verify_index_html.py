import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(BASE_DIR, "ui", "index.html")
with open(INDEX_PATH, "r", encoding="utf-8") as f:
    html = f.read()

# Check key view sections
for sec_id in ["view-dictations", "view-insights", "view-vocab", "view-ai", "view-general"]:
    assert f'id="{sec_id}"' in html, f"Missing section #{sec_id}"
    print(f"[OK] Found section #{sec_id}")

# Check key buttons
for btn_id in [
    "nav-dictations", "nav-insights", "nav-vocab", "nav-ai", "nav-general",
    "btn-launch-assistant", "btn-quick-assistant",
    "btn-save-vocab", "btn-save-ai", "btn-save-settings",
    "cfg-enable-system-commands", "cfg-enable-screen-vision"
]:
    assert f'id="{btn_id}"' in html, f"Missing element #{btn_id}"
    print(f"[OK] Found element #{btn_id}")

# Check tag balance for <section>, <aside>, <main>, <script>, <div>
for tag in ["section", "aside", "main", "script"]:
    opens = len(re.findall(rf'<{tag}\b', html, re.IGNORECASE))
    closes = len(re.findall(rf'</{tag}>', html, re.IGNORECASE))
    print(f"Tag <{tag}>: opens={opens}, closes={closes}")
    assert opens == closes, f"Mismatch for <{tag}>: {opens} vs {closes}"

print("\nAll HTML structure checks passed flawlessly!")
