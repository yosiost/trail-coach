"""Fetch Garmin data locally and push to Render.
Run after each workout: python3 push_garmin.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import os
import json
import ssl
import urllib.request
import certifi
from api.garmin import connect, week_bounds, read_plan, hr_zone, ACTIVITY_TYPES

RENDER_URL  = os.environ.get("RENDER_URL", "").rstrip("/")
PUSH_SECRET = os.environ.get("PUSH_SECRET", "")

if not RENDER_URL:
    print("ERROR: Set RENDER_URL in .env (e.g. https://trail-coach-xxx.onrender.com)")
    sys.exit(1)


def fetch_week(next_week: bool = False) -> list[dict]:
    start, end = week_bounds(next_week)
    client = connect()
    raw = client.get_activities_by_date(start.isoformat(), end.isoformat(), activitytype="")
    result = []
    for a in raw:
        avg_hr = a.get("averageHR") or 0
        result.append({
            "date":         a.get("startTimeLocal", "")[:10],
            "name":         a.get("activityName", ""),
            "type":         a.get("activityType", {}).get("typeKey", ""),
            "distance_km":  round((a.get("distance") or 0) / 1000, 1),
            "elev_gain_m":  int(a.get("elevationGain") or 0),
            "duration_min": round((a.get("duration") or 0) / 60, 1),
            "avg_hr":       int(avg_hr) if avg_hr else None,
            "zone":         hr_zone(int(avg_hr)) if avg_hr else None,
        })
    return result


ssl_ctx = ssl.create_default_context(cafile=certifi.where())

print(f"Waking Render server at {RENDER_URL}…")
try:
    wake_req = urllib.request.Request(f"{RENDER_URL}/", method="GET")
    with urllib.request.urlopen(wake_req, timeout=180, context=ssl_ctx) as r:
        r.read()
    print("  Server awake.")
except Exception:
    print("  (wake ping timed out, continuing anyway)")

print("Fetching this week from Garmin…")
this_week = fetch_week(False)
print(f"  {len(this_week)} activities")

print("Fetching next week from Garmin…")
next_week = fetch_week(True)
print(f"  {len(next_week)} activities")

payload = json.dumps({"this_week": this_week, "next_week": next_week}).encode()
req = urllib.request.Request(
    f"{RENDER_URL}/api/push",
    data=payload,
    headers={
        "Content-Type": "application/json",
        "X-Push-Secret": PUSH_SECRET,
    },
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
        result = json.loads(resp.read())
        print(f"\nPushed to Render — {result}")
except Exception as e:
    print(f"\nERROR pushing to Render: {e}")
    sys.exit(1)
