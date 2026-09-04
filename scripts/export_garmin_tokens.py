"""Print GARMIN_TOKEN_JSON env var value from local ~/.garmin_tokens/.
Paste the output into Railway's environment variables.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json

TOKEN_DIR = Path.home() / ".garmin_tokens"

if not TOKEN_DIR.exists():
    print("ERROR: ~/.garmin_tokens/ not found. Run garmin_login.py first.")
    sys.exit(1)

tokens = {}
for f in TOKEN_DIR.iterdir():
    if f.is_file():
        try:
            tokens[f.name] = json.loads(f.read_text())
        except Exception:
            tokens[f.name] = f.read_text()

print("Set this as GARMIN_TOKEN_JSON in Railway:\n")
print(json.dumps(tokens))
