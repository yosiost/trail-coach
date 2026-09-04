"""Strava API client — replaces Garmin integration for activity fetching."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

from api.db import get_plan_overrides, set_plan_overrides, get_config_blob, set_config_blob

# Legacy fallback path. The training plan is stored in the DB
# (config_blobs['plan_csv']); this local file is only read if that blob is empty
# (a fresh clone before onboarding/demo seeding). Not committed to the repo.
PLAN_CSV = Path(__file__).parent.parent / "training_plan.csv"


def _plan_text() -> str:
    """Return the training-plan CSV text: DB blob first, else the legacy file."""
    text = get_config_blob("plan_csv")
    if text:
        return text
    if PLAN_CSV.exists():
        return PLAN_CSV.read_text(encoding="utf-8")
    return ""


def _plan_rows() -> list[dict]:
    """Parse the plan CSV into rows (empty list when no plan is configured)."""
    text = _plan_text()
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text)))


def seed_plan_blob_from_file() -> None:
    """One-time migration: import the legacy committed plan CSV into the DB if the
    blob is empty. No-ops once the file is removed (onboarding PR B-2)."""
    if get_config_blob("plan_csv"):
        return
    try:
        if PLAN_CSV.exists():
            set_config_blob("plan_csv", PLAN_CSV.read_text(encoding="utf-8"))
            logging.info("Migrated legacy plan CSV into the DB (config_blobs['plan_csv']).")
    except Exception as e:
        logging.warning("plan blob migration skipped: %s", e)


def plan_source() -> str:
    """Where the plan is read from: 'db' | 'file' | 'none' (for status/diagnostics)."""
    if get_config_blob("plan_csv"):
        return "db"
    return "file" if PLAN_CSV.exists() else "none"

SKIP = ("Meditation", "Foam Roll", "Breathwork", "Flexibility", "Stretch")

# Fallback fixed HR-zone table (used when no max HR is configured). Preserves the
# original behaviour for installs that predate the derived-zone config.
HR_ZONES = [
    (0,   117, "Z1"),
    (118, 135, "Z2"),
    (136, 143, "Z3"),
    (144, 151, "Z4"),
    (152, 999, "Z5"),
]

# Zone boundaries as a fraction of max HR (matches onboarding.hr_zones_text).
_ZONE_PCT = [(0.72, "Z1"), (0.82, "Z2"), (0.87, "Z3"), (0.92, "Z4"), (1e9, "Z5")]


def hr_zone(bpm: int) -> str:
    max_hr = get_config_blob("max_hr")
    if max_hr:
        try:
            frac = bpm / int(max_hr)
            for cutoff, z in _ZONE_PCT:
                if frac < cutoff:
                    return z
        except (ValueError, ZeroDivisionError):
            pass
    for lo, hi, z in HR_ZONES:
        if lo <= bpm <= hi:
            return z
    return "Z5"


SPORT_TYPE_MAP = {
    "Run":            "running",
    "TrailRun":       "trail_running",
    "WeightTraining": "strength_training",
    "VirtualRun":     "treadmill_running",
    "Treadmill":      "treadmill_running",
    "Hike":           "hiking",
    "Walk":           "hiking",
    "Ride":           "cycling",
    "VirtualRide":    "cycling",
    "Elliptical":     "elliptical",
    "CrossFit":       "strength_training",
}

ACTIVITY_TYPES = set(SPORT_TYPE_MAP.values())

_token_cache: dict = {}


def strava_configured() -> bool:
    """Strava is an optional bonus (real activity data). When its creds are absent
    the app renders the plan without actuals — no fetch attempt, no error."""
    return all(os.environ.get(k) for k in
               ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN"))


def _get_access_token() -> str:
    now = time.time()
    if _token_cache.get("access_token") and _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["access_token"]

    payload = urllib.parse.urlencode({
        "client_id":     os.environ.get("STRAVA_CLIENT_ID", ""),
        "client_secret": os.environ.get("STRAVA_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("STRAVA_REFRESH_TOKEN", ""),
        "grant_type":    "refresh_token",
    }).encode()

    req = urllib.request.Request(
        "https://www.strava.com/oauth/token",
        data=payload,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"]   = data["expires_at"]
    return data["access_token"]


def fetch_activities(start: date, end: date) -> tuple[list[dict], str | None]:
    if not strava_configured():
        return [], None  # optional integration — render the plan without actuals
    try:
        token  = _get_access_token()
        after  = int(datetime.combine(start, datetime.min.time()).timestamp())
        before = int(datetime.combine(end + timedelta(days=1), datetime.min.time()).timestamp())

        url = (
            f"https://www.strava.com/api/v3/athlete/activities"
            f"?after={after}&before={before}&per_page=100"
        )
        logging.info("fetch_activities: GET %s", url)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())

        logging.info("fetch_activities: Strava returned %d raw activities for %s – %s", len(raw), start, end)
        result = []
        for a in raw:
            sport    = a.get("sport_type") or a.get("type", "")
            avg_hr   = a.get("average_heartrate")
            act = {
                "id":           a.get("id"),
                "date":         a.get("start_date_local", "")[:10],
                "name":         a.get("name", ""),
                "type":         SPORT_TYPE_MAP.get(sport, sport.lower()),
                "distance_km":  round((a.get("distance") or 0) / 1000, 1),
                "elev_gain_m":  int(a.get("total_elevation_gain") or 0),
                "duration_min": round((a.get("moving_time") or 0) / 60, 1),
                "avg_hr":       int(avg_hr) if avg_hr else None,
                "zone":         hr_zone(int(avg_hr)) if avg_hr else None,
            }
            logging.info("fetch_activities: activity id=%s date=%s name=%s type=%s", act["id"], act["date"], act["name"], act["type"])
            result.append(act)
        return result, None
    except Exception as e:
        err = str(e)
        logging.error("fetch_activities error: %s", err)
        if "429" in err:
            return [], "Strava rate limited — try again in a moment"
        return [], err


# Week-start day is configurable (config_blobs['week_start']); defaults to Sunday
# to preserve existing installs. Python weekday(): Mon=0 … Sun=6.
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _week_start_idx() -> int:
    return _WEEKDAYS.get((get_config_blob("week_start") or "sunday").strip().lower(), 6)


def _week_offset(d: date) -> int:
    """Days from d back to the configured week-start day."""
    return (d.weekday() - _week_start_idx()) % 7


def week_bounds(next_week: bool = False) -> tuple[date, date]:
    today  = date.today()
    start  = today - timedelta(days=_week_offset(today))
    if next_week:
        start += timedelta(days=7)
    return start, start + timedelta(days=6)


def get_all_weeks() -> list[dict]:
    """Return all unique weeks from the plan, sorted chronologically."""
    week_min: dict[str, dict] = {}
    for row in _plan_rows():
        w = row["Week"]
        d = datetime.strptime(row["Date"], "%Y-%m-%d").date()
        if w not in week_min or d < week_min[w]["date"]:
            week_min[w] = {"date": d, "phase": row["Phase"], "week_num": w}
    result = []
    for info in sorted(week_min.values(), key=lambda x: x["date"]):
        d      = info["date"]
        start  = d - timedelta(days=_week_offset(d))
        result.append({
            "week_num": info["week_num"],
            "phase":    info["phase"],
            "start":    start.isoformat(),
            "end":      (start + timedelta(days=6)).isoformat(),
        })
    return result


def read_plan(start: date, end: date) -> list[dict]:
    overrides = get_plan_overrides()
    rows = []
    for row in _plan_rows():
        d = datetime.strptime(row["Date"], "%Y-%m-%d").date()
        if not (start <= d <= end):
            continue
        if any(kw in row["Session"] for kw in SKIP):
            continue
        if row["Date"] in overrides:
            for field, value in overrides[row["Date"]].items():
                row[field] = value
        rows.append({
            "date":     row["Date"],
            "week":     row["Week"],
            "phase":    row["Phase"],
            "day":      row.get("Day", ""),
            "session":  row["Session"],
            "km":       row.get("Distance_km", ""),
            "vert":     row.get("Vert_m", ""),
            "duration": row.get("Duration_min", ""),
            "zone":     row.get("HR_Zone", ""),
            "rpe":      row.get("RPE", ""),
            "notes":    row.get("Notes", ""),
        })
    return rows


def get_this_week(next_week: bool = False, injected_activities: list | None = None, week_start: date | None = None) -> dict:
    if week_start is not None:
        start, end = week_start, week_start + timedelta(days=6)
    else:
        start, end = week_bounds(next_week)
    plan = read_plan(start, end)

    is_future = start > date.today()
    if injected_activities is not None:
        activities, error = injected_activities, None
    elif is_future:
        activities, error = [], None
    else:
        activities, error = fetch_activities(start, end)

    act_map: dict[str, list] = {}
    for a in activities:
        act_map.setdefault(a["date"], []).append(a)

    rows = []
    done_km, done_vert = 0.0, 0
    plan_km   = sum(float(s["km"])   for s in plan if s["km"]   not in ("0", ""))
    plan_vert = sum(int(s["vert"])   for s in plan if s["vert"] not in ("0", ""))

    for s in plan:
        acts    = act_map.get(s["date"], [])
        d       = datetime.strptime(s["date"], "%Y-%m-%d").date()
        today   = date.today()
        is_rest = "REST" in s["session"].upper()

        # Collect all relevant activities for this day
        day_acts = [a for a in acts if a["type"] in ACTIVITY_TYPES] or acts

        status = "rest"
        if not is_rest:
            if day_acts:
                status = "done"
            elif d < today:
                status = "missed"
            elif d == today:
                status = "today"
            else:
                status = "future"

        # Count all activities toward totals (including rest-day activities)
        for a in day_acts:
            done_km   += a["distance_km"]
            done_vert += a["elev_gain_m"]

        rows.append({
            "date":         s["date"],
            "day":          d.strftime("%a %b %d"),
            "session":      s["session"],
            "planned_km":   s["km"],
            "planned_vert": s["vert"],
            "planned_zone": s["zone"],
            "notes":        s["notes"],
            "status":       status,
            "actuals":      day_acts,
            "actual":       day_acts[0] if day_acts else None,
        })

    return {
        "week_label":   f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}",
        "phase":        plan[0]["phase"] if plan else "",
        "week_num":     plan[0]["week"]  if plan else "",
        "rows":         rows,
        "garmin_error": error,  # key kept for frontend compatibility
        "summary": {
            "done_km":   round(done_km, 1),
            "done_vert": done_vert,
            "plan_km":   round(plan_km, 1),
            "plan_vert": plan_vert,
            "pct":       int(done_km / plan_km * 100) if plan_km else 0,
        },
    }


def update_plan_rows(updates: list[dict]) -> str:
    ALLOWED = {"Session", "Distance_km", "Vert_m", "HR_Zone", "Notes"}
    for u in updates:
        if u.get("field", "") not in ALLOWED:
            return f"Error: field '{u.get('field')}' not allowed. Use one of: {', '.join(ALLOWED)}"
    return set_plan_overrides(updates)


def get_full_plan() -> list[dict]:
    overrides = get_plan_overrides()
    rows = []
    for row in _plan_rows():
        if any(kw in row["Session"] for kw in SKIP):
            continue
        if row["Date"] in overrides:
            for field, value in overrides[row["Date"]].items():
                row[field] = value
        rows.append({
            "date":    row["Date"],
            "week":    row["Week"],
            "phase":   row["Phase"],
            "session": row["Session"],
            "km":      row.get("Distance_km", ""),
            "vert":    row.get("Vert_m", ""),
            "zone":    row.get("HR_Zone", ""),
            "notes":   row.get("Notes", ""),
        })
    return rows


def fetch_activity_laps(activity_id: int) -> list[dict]:
    """Fetch lap-by-lap breakdown for a specific Strava activity."""
    if not strava_configured():
        return []
    try:
        token = _get_access_token()
        url = f"https://www.strava.com/api/v3/activities/{activity_id}/laps"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        # Prefer manual (watch/workout) laps over auto-distance laps, matching
        # what Strava's web UI shows on the dedicated Laps tab.
        manual = [l for l in raw if l.get("lap_trigger") == "manual"]
        source = manual if manual else raw

        laps = []
        for lap in source:
            avg_hr = lap.get("average_heartrate")
            laps.append({
                "lap":          lap.get("lap_index", len(laps) + 1),
                "distance_km":  round((lap.get("distance") or 0) / 1000, 2),
                "duration_min": round((lap.get("moving_time") or 0) / 60, 1),
                "elev_gain_m":  int(lap.get("total_elevation_gain") or 0),
                "avg_hr":       int(avg_hr) if avg_hr else None,
                "zone":         hr_zone(int(avg_hr)) if avg_hr else None,
            })
        return laps
    except Exception as e:
        err = str(e)
        if "429" in err:
            return [{"error": "Strava rate limited — try again in a moment"}]
        return [{"error": err}]
