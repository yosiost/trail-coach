"""Run once to get a Strava refresh token. Paste the resulting token into .env / Render."""

import os
import json
import urllib.request
import urllib.parse
import webbrowser

CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID") or input("Strava Client ID: ").strip()
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET") or input("Strava Client Secret: ").strip()

auth_url = (
    f"https://www.strava.com/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    f"&response_type=code"
    f"&redirect_uri=http://localhost"
    f"&approval_prompt=force"
    f"&scope=activity:read_all"
)

print("Opening Strava authorization in browser…")
print(f"\n{auth_url}\n")
webbrowser.open(auth_url)

print("After authorizing, Strava redirects to http://localhost/?code=XXXX&...")
code = input("Paste the 'code' parameter value here: ").strip()

payload = urllib.parse.urlencode({
    "client_id":     CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code":          code,
    "grant_type":    "authorization_code",
}).encode()

req = urllib.request.Request(
    "https://www.strava.com/oauth/token",
    data=payload,
    method="POST",
)
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())

print("\nSuccess! Add this to .env and Render environment variables:")
print(f"\n  STRAVA_REFRESH_TOKEN={data['refresh_token']}\n")
print(f"Athlete: {data['athlete']['firstname']} {data['athlete']['lastname']}")
