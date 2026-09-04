"""Run once to authenticate with Garmin and save tokens to ~/.garmin_tokens/"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import os
from garminconnect import Garmin

TOKEN_DIR = Path.home() / ".garmin_tokens"
TOKEN_DIR.mkdir(exist_ok=True)

email    = os.environ.get("GARMIN_EMAIL", "")
password = os.environ.get("GARMIN_PASSWORD", "")

if not email or not password:
    print("ERROR: GARMIN_EMAIL and GARMIN_PASSWORD must be set in .env")
    sys.exit(1)

def mfa_prompt():
    return input("Enter Garmin MFA code: ").strip()

print(f"Logging in as {email}…")
client = Garmin(email, password, prompt_mfa=mfa_prompt)
client.login(tokenstore=str(TOKEN_DIR))
print(f"Done — tokens saved to {TOKEN_DIR}")
