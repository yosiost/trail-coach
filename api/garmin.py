import csv
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from garminconnect import Garmin

PLAN_CSV  = Path(__file__).parent.parent / "training_plan.csv"
TOKEN_DIR = Path(os.environ.get("GARMIN_TOKEN_DIR", str(Path.home() / ".garmin_tokens")))


def _bootstrap_tokens() -> None:
    """Write Garmin token files from env vars (used on cloud deployments)."""
    token_json = os.environ.get("GARMIN_TOKEN_JSON")
    if not token_json:
        return
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        tokens = json.loads(token_json)
        for filename, content in tokens.items():
            (TOKEN_DIR / filename).write_text(
                json.dumps(content) if isinstance(content, dict) else content
            )
    except Exception:
        pass


_bootstrap_tokens()

import threading

_mfa_event = threading.Event()
_mfa_code: list[str] = []


def _web_mfa_callback() -> str:
    _mfa_event.clear()
    _mfa_code.clear()
    _mfa_event.wait(timeout=180)
    return _mfa_code[0] if _mfa_code else ""


def submit_mfa(code: str) -> None:
    _mfa_code.clear()
    _mfa_code.append(code)
    _mfa_event.set()


SKIP = ("Meditation", "Foam Roll", "Breathwork", "Flexibility", "Stretch")

HR_ZONES = [
    (0,   117, "Z1"),
    (118, 135, "Z2"),
    (136, 143, "Z3"),
    (144, 151, "Z4"),
    (152, 999, "Z5"),
]

def hr_zone(bpm: int) -> str:
    for lo, hi, z in HR_ZONES:
        if lo <= bpm <= hi:
            return z
    return "Z5"


def connect() -> Garmin:
    email    = os.environ.get("GARMIN_EMAIL", "")
    password = os.environ.get("GARMIN_PASSWORD", "")
    client   = Garmin(email, password, prompt_mfa=_web_mfa_callback)
    client.login(tokenstore=str(TOKEN_DIR))
    return client


def week_bounds(next_week: bool = False) -> tuple[date, date]:
    today  = date.today()
    offset = (today.weekday() + 1) % 7
    start  = today - timedelta(days=offset)
    if next_week:
        start += timedelta(days=7)
    return start, start + timedelta(days=6)


def read_plan(start: date, end: date) -> list[dict]:
    if not PLAN_CSV.exists():
        return []
    rows = []
    with open(PLAN_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            if not (start <= d <= end):
                continue
            if any(kw in row["Session"] for kw in SKIP):
                continue
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


ACTIVITY_TYPES = {
    "running", "trail_running", "strength_training",
    "treadmill_running", "hiking", "indoor_cardio",
    "cycling", "elliptical",
}

def _do_fetch(start: date, end: date) -> tuple[list[dict], str | None]:
    client = connect()
    raw = client.get_activities_by_date(
        start.isoformat(), end.isoformat(), activitytype=""
    )
    result = []
    for a in raw:
        avg_hr = a.get("averageHR") or 0
        result.append({
            "date":        a.get("startTimeLocal", "")[:10],
            "name":        a.get("activityName", ""),
            "type":        a.get("activityType", {}).get("typeKey", ""),
            "distance_km": round((a.get("distance") or 0) / 1000, 1),
            "elev_gain_m": int(a.get("elevationGain") or 0),
            "duration_min": round((a.get("duration") or 0) / 60, 1),
            "avg_hr":      int(avg_hr) if avg_hr else None,
                "zone":        hr_zone(int(avg_hr)) if avg_hr else None,
            })
    return result, None


def fetch_activities(start: date, end: date) -> tuple[list[dict], str | None]:
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_do_fetch, start, end)
            return future.result(timeout=20)
    except concurrent.futures.TimeoutError:
        return [], "Garmin request timed out — try refreshing"
    except Exception as e:
        err = str(e)
        if "429" in err or "rate" in err.lower():
            return [], "Garmin rate limited — wait a few minutes and refresh"
        if "MFA" in err or "mfa" in err.lower():
            return [], "MFA_REQUIRED"
        return [], err


def get_this_week(next_week: bool = False, injected_activities: list | None = None) -> dict:
    start, end = week_bounds(next_week)
    plan = read_plan(start, end)
    if injected_activities is not None:
        activities, garmin_error = injected_activities, None
    else:
        activities, garmin_error = fetch_activities(start, end)

    act_map: dict[str, list] = {}
    for a in activities:
        act_map.setdefault(a["date"], []).append(a)

    rows = []
    done_km, done_vert = 0.0, 0
    plan_km   = sum(float(s["km"])   for s in plan if s["km"]   not in ("0", ""))
    plan_vert = sum(int(s["vert"])   for s in plan if s["vert"] not in ("0", ""))

    for s in plan:
        acts   = act_map.get(s["date"], [])
        d      = datetime.strptime(s["date"], "%Y-%m-%d").date()
        today  = date.today()
        is_rest = "REST" in s["session"].upper()

        status = "rest"
        best   = None
        if not is_rest:
            best = next((a for a in acts if a["type"] in ACTIVITY_TYPES), acts[0] if acts else None)
            if best:
                status = "done"
                done_km   += best["distance_km"]
                done_vert += best["elev_gain_m"]
            elif d < today:
                status = "missed"
            elif d == today:
                status = "today"
            else:
                status = "future"

        rows.append({
            "date":    s["date"],
            "day":     d.strftime("%a %b %d"),
            "session": s["session"],
            "planned_km":   s["km"],
            "planned_vert": s["vert"],
            "planned_zone": s["zone"],
            "notes":   s["notes"],
            "status":  status,
            "actual":  best,
        })

    return {
        "week_label": f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}",
        "phase":      plan[0]["phase"] if plan else "",
        "week_num":   plan[0]["week"]  if plan else "",
        "rows":       rows,
        "garmin_error": garmin_error,
        "summary": {
            "done_km":   round(done_km, 1),
            "done_vert": done_vert,
            "plan_km":   round(plan_km, 1),
            "plan_vert": plan_vert,
            "pct":       int(done_km / plan_km * 100) if plan_km else 0,
        },
    }


def update_plan_rows(updates: list[dict]) -> str:
    """Apply a list of {date, field, value} updates to the CSV. Returns a summary."""
    if not PLAN_CSV.exists():
        return "Error: plan CSV not found"

    ALLOWED = {"Session", "Distance_km", "Vert_m", "HR_Zone", "Notes"}
    update_map: dict[str, dict] = {}
    for u in updates:
        d, field, value = u.get("date", ""), u.get("field", ""), u.get("value", "")
        if field not in ALLOWED:
            return f"Error: field '{field}' not allowed. Use one of: {', '.join(ALLOWED)}"
        update_map.setdefault(d, {})[field] = value

    rows = []
    with open(PLAN_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row["Date"] in update_map:
                row.update(update_map[row["Date"]])
            rows.append(row)

    with open(PLAN_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    applied = [f"{d}: {', '.join(f'{k}={v}' for k, v in changes.items())}" for d, changes in update_map.items()]
    return "Updated:\n" + "\n".join(applied)


def get_full_plan() -> list[dict]:
    if not PLAN_CSV.exists():
        return []
    rows = []
    with open(PLAN_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if any(kw in row["Session"] for kw in SKIP):
                continue
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
