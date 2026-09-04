"""First-run onboarding — validate the setup wizard's input and persist it.

Everything a new self-hoster provides (profile, goal, plan, course, activity
source, coaching methodology) is written to the tables/blobs that already exist,
so nothing here is bespoke storage. Kept separate from the Flask routes so the
validation + persistence is unit-testable without a request context.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

from api.db import (
    upsert_athlete_reference, set_config_blob, get_config_blob,
    create_goal, hms_to_sec,
)

_EXAMPLES = Path(__file__).parent.parent / "examples"

# A plan CSV must at least identify the week, date, phase, and session; the
# remaining columns (Distance_km, Vert_m, Duration_min, HR_Zone, RPE, Notes) are
# read with .get() elsewhere, so they're optional.
_REQUIRED_PLAN_COLUMNS = {"Week", "Date", "Phase", "Session"}


# ── Derivations ───────────────────────────────────────────────────────────────

def hr_zones_text(max_hr: int) -> str:
    """Five HR zones as a share of max HR (matches the app's calibrated defaults)."""
    z1 = round(0.72 * max_hr)
    z2 = round(0.82 * max_hr)
    z3 = round(0.87 * max_hr)
    z4 = round(0.92 * max_hr)
    return (
        f"Calibrated HR zones (max HR {max_hr} bpm): "
        f"Z1: <{z1} | Z2: {z1}–{z2} | Z3: {z2 + 1}–{z3} | "
        f"Z4: {z3 + 1}–{z4} | Z5: {z4 + 1}–{max_hr} bpm. "
        f"~80% of weekly volume targets Z1–Z2; VO2max intervals target Z5 "
        f"(verify by RPE, not HR, if the athlete's HR data is flagged unreliable)."
    )


def _profile_text(name: str, weight_kg, height_cm, max_hr, background: str) -> str:
    parts = []
    if name:
        parts.append(f"Name: {name}.")
    if weight_kg:
        parts.append(f"Weight: ~{weight_kg}kg.")
    if height_cm:
        parts.append(f"Height: ~{height_cm}cm.")
    if max_hr:
        parts.append(f"Max HR: {max_hr} bpm.")
    if background:
        parts.append(f"Background: {background}")
    return " ".join(parts) if parts else "Athlete profile not provided."


# ── Validation (raise ValueError with a user-facing message) ──────────────────

def validate_plan_csv(text: str) -> list[dict]:
    if not text or not text.strip():
        raise ValueError("The plan file is empty.")
    reader = csv.DictReader(io.StringIO(text))
    cols = set(reader.fieldnames or [])
    missing = _REQUIRED_PLAN_COLUMNS - cols
    if missing:
        raise ValueError(f"Plan CSV is missing required column(s): {', '.join(sorted(missing))}.")
    rows = list(reader)
    if not rows:
        raise ValueError("The plan CSV has a header but no session rows.")
    for i, r in enumerate(rows, start=2):  # row 1 is the header
        d = (r.get("Date") or "").strip()
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Plan CSV row {i}: Date {d!r} is not YYYY-MM-DD.")
    return rows


def validate_course(obj: dict) -> None:
    if not isinstance(obj, dict):
        raise ValueError("Course must be a JSON object.")
    for key in ("race", "distance_km", "vert_m"):
        if key not in obj or obj[key] in (None, ""):
            raise ValueError(f"Course is missing required field: {key!r}.")
    if not isinstance(obj["distance_km"], (int, float)):
        raise ValueError("Course 'distance_km' must be a number.")
    if not isinstance(obj["vert_m"], (int, float)):
        raise ValueError("Course 'vert_m' must be a number.")
    segs = obj.get("segments")
    if segs is not None and not isinstance(segs, list):
        raise ValueError("Course 'segments' must be a list when present.")


def minimal_course(race: str, distance_km: float, vert_m: int, date: str = "") -> dict:
    """A 'generic race' course: distance + vert only, no segment/elevation detail."""
    course = {"race": race, "distance_km": distance_km, "vert_m": vert_m, "segments": []}
    if date:
        course["date"] = date
    return course


# ── Examples shipped in the repo ──────────────────────────────────────────────

def list_examples() -> dict:
    plans = sorted(p.name for p in (_EXAMPLES / "plans").glob("*.csv")) if (_EXAMPLES / "plans").exists() else []
    courses = sorted(c.name for c in (_EXAMPLES / "courses").glob("*.json")) if (_EXAMPLES / "courses").exists() else []
    return {"plans": plans, "courses": courses}


def _safe_example(subdir: str, name: str) -> Path:
    """Resolve an example file, rejecting path traversal."""
    p = (_EXAMPLES / subdir / name).resolve()
    root = (_EXAMPLES / subdir).resolve()
    if root not in p.parents:
        raise ValueError("Invalid example name.")
    if not p.exists():
        raise ValueError(f"No such example: {name}.")
    return p


def load_example_plan(name: str) -> str:
    return _safe_example("plans", name).read_text(encoding="utf-8")


def load_example_course(name: str) -> dict:
    return json.loads(_safe_example("courses", name).read_text(encoding="utf-8"))


# ── Persistence (each returns nothing; raises ValueError on bad input) ─────────

_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


def persist_profile(name="", weight_kg=None, height_cm=None, max_hr=None,
                    background="", units="km", week_start="monday") -> None:
    upsert_athlete_reference("athlete_profile", "basics",
                             _profile_text(name, weight_kg, height_cm, max_hr, background))
    if max_hr:
        try:
            mh = int(max_hr)
        except (TypeError, ValueError):
            raise ValueError("max_hr must be a number.")
        upsert_athlete_reference("assumptions", "hr_zones", hr_zones_text(mh))
        set_config_blob("max_hr", str(mh))  # HR zones derive from this
    set_config_blob("units", "mi" if str(units).lower() in ("mi", "miles", "imperial") else "km")
    ws = str(week_start).strip().lower()
    if ws in _WEEKDAYS:
        set_config_blob("week_start", ws)


def persist_goal(race_name: str, race_date: str, distance_km, vert_m,
                 aspirational_time: str, realistic_min_time: str, realistic_max_time: str,
                 notes: str = "") -> dict | None:
    if not race_name or not race_date:
        raise ValueError("Goal needs a race name and date.")
    try:
        datetime.strptime(race_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Race date must be YYYY-MM-DD.")
    try:
        asp = hms_to_sec(aspirational_time)
        lo = hms_to_sec(realistic_min_time)
        hi = hms_to_sec(realistic_max_time)
    except ValueError as e:
        raise ValueError(f"Target time format: {e}")
    return create_goal(race_name, race_date, float(distance_km), int(vert_m), asp, lo, hi, notes)


def persist_plan(csv_text: str) -> int:
    rows = validate_plan_csv(csv_text)
    set_config_blob("plan_csv", csv_text)
    return len(rows)


def persist_course(course: dict) -> None:
    validate_course(course)
    set_config_blob("course_json", json.dumps(course))


_VALID_METHODOLOGY = {"generic", "custom"}
_VALID_ACTIVITY = {"manual", "strava", "garmin"}


def seed_demo_blobs() -> None:
    """Demo mode: seed the example plan + course into config_blobs if unset, so a
    SEED_DEMO_DATA instance has a populated plan and course (not just a goal)."""
    if not get_config_blob("plan_csv"):
        plans = list_examples()["plans"]
        if plans:
            set_config_blob("plan_csv", load_example_plan(plans[0]))
    if not get_config_blob("course_json"):
        courses = list_examples()["courses"]
        if courses:
            set_config_blob("course_json", json.dumps(load_example_course(courses[0])))


def complete(activity_source: str = "manual", methodology: str = "generic",
             methodology_text: str = "") -> None:
    activity_source = activity_source if activity_source in _VALID_ACTIVITY else "manual"
    methodology = methodology if methodology in _VALID_METHODOLOGY else "generic"
    set_config_blob("activity_source", activity_source)
    # Coach methodology (dietitian + coach can also be set via /api/persona/config).
    set_config_blob("coach_methodology", methodology)
    if methodology == "custom" and methodology_text.strip():
        set_config_blob("coach_methodology_text", methodology_text.strip())
    set_config_blob("onboarded", "true")
