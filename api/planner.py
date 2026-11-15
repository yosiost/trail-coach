"""Generate a training plan from the athlete's goal, course, and profile.

The LLM produces the *training content* as structured JSON (weeks -> sessions);
this module does all the calendar math and renders the plan CSV the engine reads.
Keeping dates in code (not the LLM) makes generation reliable. `llm` is imported
lazily so the deterministic helpers stay testable without the LLM stack.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta

from api.db import (
    get_active_goal, get_athlete_references, get_config_blob, set_config_blob,
    get_race_config_blob, set_race_config_blob, delete_plan_overrides,
)

# 3-letter keys so both "Mon" and "Monday" (and the week_start config) resolve.
_WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day_idx(name) -> int | None:
    return _WEEKDAY.get(str(name).strip().lower()[:3])
_PLAN_COLUMNS = ["Week", "Date", "Day", "Phase", "Session",
                 "Distance_km", "Vert_m", "Duration_min", "HR_Zone", "RPE", "Notes"]
_MAX_WEEKS = 30


def _week_start_idx() -> int:
    return _WEEKDAY.get((get_config_blob("week_start") or "sunday").strip().lower()[:3], 6)


def _coach_brief() -> str:
    """A short methodology note for the prompt (respects the configured coach)."""
    if (get_config_blob("coach_methodology") or "generic").strip().lower() == "custom":
        txt = (get_config_blob("coach_methodology_text") or "").strip()
        if txt:
            return txt
    return ("General endurance methodology: ~80/20 easy-to-hard, one or two quality "
            "sessions a week (intervals / hills / tempo), a weekend long run that builds "
            "gradually, back-to-back long days as the race nears, and a taper before race day.")


def _profile_brief() -> str:
    for r in get_athlete_references("athlete_profile"):
        if r["name"] == "basics":
            return r["content"]
    return "No athlete profile provided."


def render_plan_csv(weeks: list[dict], plan_start: date, week_start_idx: int) -> str:
    """Render LLM weeks -> the plan CSV, assigning real dates. Pure/testable.

    weeks: [{phase, sessions: [{day, session, distance_km, vert_m, duration_min,
             hr_zone, rpe, notes}]}]  (day = Mon..Sun)
    """
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=_PLAN_COLUMNS)
    w.writeheader()
    for i, wk in enumerate(weeks):
        monday_offset_week = plan_start + timedelta(days=7 * i)
        phase = str(wk.get("phase", "")).strip()
        for s in wk.get("sessions", []):
            wd = _day_idx(s.get("day", ""))
            if wd is None:
                continue
            d = monday_offset_week + timedelta(days=(wd - week_start_idx) % 7)
            w.writerow({
                "Week": f"W{i + 1}", "Date": d.isoformat(), "Day": _DAY_ABBR[wd],
                "Phase": phase, "Session": str(s.get("session", "")).strip(),
                "Distance_km": s.get("distance_km", "") or "", "Vert_m": s.get("vert_m", "") or "",
                "Duration_min": s.get("duration_min", "") or "", "HR_Zone": s.get("hr_zone", "") or "",
                "RPE": s.get("rpe", "") or "", "Notes": str(s.get("notes", "")).strip(),
            })
    return out.getvalue()


def week_start_of(d: date, week_start_idx: int) -> date:
    return d - timedelta(days=(d.weekday() - week_start_idx) % 7)


def merge_plan_csv(existing_csv: str, new_csv: str, start: date, end: date | None,
                   week_start_idx: int) -> str:
    """Replace existing plan rows dated start..end (open-ended when end is None)
    with new_csv's rows, keep everything else, and renumber Week labels W1..
    chronologically so separately written blocks never share a label. Pure."""
    def rows(text: str) -> list[dict]:
        return list(csv.DictReader(io.StringIO(text))) if text and text.strip() else []

    def in_range(r: dict) -> bool:
        try:
            d = date.fromisoformat(r.get("Date", ""))
        except ValueError:
            return False
        return start <= d and (end is None or d <= end)

    old = rows(existing_csv)
    merged = [r for r in old if not in_range(r)] + rows(new_csv)
    merged = [r for r in merged if r.get("Date")]
    merged.sort(key=lambda r: r["Date"])
    fields = list(_PLAN_COLUMNS)
    for r in old:
        fields += [k for k in r if k and k not in fields]
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", restval="")
    w.writeheader()
    if merged:
        first = week_start_of(date.fromisoformat(merged[0]["Date"]), week_start_idx)
        for r in merged:
            wk = week_start_of(date.fromisoformat(r["Date"]), week_start_idx)
            r["Week"] = f"W{(wk - first).days // 7 + 1}"
            w.writerow(r)
    return out.getvalue()


def _save_plan_range(new_csv: str, start: date, end: date | None) -> int:
    """Merge new rows into the active race's plan over start..end, drop the
    edits made on those dates (they belonged to the replaced sessions), and
    return the number of new rows."""
    n = len(list(csv.DictReader(io.StringIO(new_csv))))
    if n < 1:
        raise ValueError("The plan had no sessions with a valid day (Mon..Sun).")
    merged = merge_plan_csv(get_race_config_blob("plan_csv") or "", new_csv, start, end,
                            _week_start_idx())
    set_race_config_blob("plan_csv", merged)
    delete_plan_overrides(start.isoformat(), end.isoformat() if end else None)
    return n


def write_plan_block(weeks: list, start_date: str) -> dict:
    """Write whole weeks (the coach's block) into the active race's plan from the
    week containing start_date, replacing whatever was planned in those weeks."""
    if not get_active_goal():
        raise ValueError("No active race — create one in Settings first.")
    try:
        start = date.fromisoformat(str(start_date))
    except ValueError:
        raise ValueError("start_date must be YYYY-MM-DD.")
    if not isinstance(weeks, list) or not weeks:
        raise ValueError("weeks must be a non-empty list.")
    if len(weeks) > _MAX_WEEKS:
        raise ValueError(f"At most {_MAX_WEEKS} weeks per call.")
    weeks = [w for w in weeks if isinstance(w, dict) and isinstance(w.get("sessions"), list)]
    if not weeks:
        raise ValueError("Each week needs a 'sessions' list.")
    wsi = _week_start_idx()
    start = week_start_of(start, wsi)
    end = start + timedelta(days=7 * len(weeks) - 1)
    n = _save_plan_range(render_plan_csv(weeks, start, wsi), start, end)
    return {"weeks": len(weeks), "sessions": n, "start": start.isoformat(), "end": end.isoformat()}


def _build_messages(goal: dict, weeks: int, course: dict | None,
                    plan_start: date | None = None) -> list[dict]:
    course_line = ""
    if course:
        course_line = (f"\nRace course: {course.get('distance_km')}km / {course.get('vert_m')}m vert, "
                       f"{course.get('route', '')}. Rehearse this terrain and vert in training.")
    system = {
        "role": "system",
        "content": (
            "You are an expert trail/ultra running coach. Produce a periodized training plan as "
            "STRICT JSON only (no prose, no code fences), shaped exactly:\n"
            '{"weeks":[{"phase":"Base|Build|Peak|Taper","sessions":['
            '{"day":"Mon","session":"...","distance_km":10,"vert_m":300,"duration_min":60,'
            '"hr_zone":"Z2","rpe":4,"notes":"..."}]}]}\n'
            "Rules: exactly the requested number of weeks; 5 sessions per week plus 2 rest days "
            "(a rest day is a session named 'Rest' with distance_km 0); progress volume through Base "
            "and Build, peak ~2-3 weeks out, then Taper into race week; the final week ends with "
            "the race. Keep ~80% easy (Z1-Z2). day is one of Mon,Tue,Wed,Thu,Fri,Sat,Sun. "
            "Keep every 'notes' value to at most 6 words. Output ONLY compact minified JSON on a "
            "single line — no whitespace, no newlines, no trailing commentary."
        ),
    }
    user = {
        "role": "user",
        "content": (
            f"Goal race: {goal['race_name']} on {goal['race_date']}, "
            f"{goal['distance_km']}km / {goal['vert_m']}m vert.{course_line}\n"
            f"Athlete: {_profile_brief()}\n"
            f"Methodology: {_coach_brief()}\n"
            f"Generate exactly {weeks} weeks, starting the week of "
            f"{(plan_start or date.today()).isoformat()} and ending race week."
        ),
    }
    return [system, user]


def _parse_weeks(raw: str) -> list[dict] | None:
    """Pull the list of week objects out of the model output — tolerant of code
    fences, a wrapper key, and truncation (keeps every complete week that parsed)."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1 and s[:nl].strip().lower() in ("json", ""):
            s = s[nl + 1:]
    # 1) clean whole-value parse
    for lo, hi in ((s.find("{"), s.rfind("}") + 1), (s.find("["), s.rfind("]") + 1)):
        if lo != -1 and hi > lo:
            try:
                obj = json.loads(s[lo:hi])
                wk = (obj.get("weeks") or obj.get("plan")) if isinstance(obj, dict) else obj
                if isinstance(wk, list) and wk:
                    return [w for w in wk if isinstance(w, dict) and "sessions" in w]
            except Exception:
                pass
    # 2) salvage: scan the weeks array, collecting complete {...} objects (drops a
    #    truncated final one instead of failing the whole plan)
    key = next((s.find(k) for k in ('"weeks"', '"plan"') if s.find(k) != -1), -1)
    arr = s.find("[", key) if key != -1 else s.find("[")
    if arr == -1:
        return None
    weeks, depth, start = [], 0, None
    for i in range(arr + 1, len(s)):
        ch = s[i]
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    w = json.loads(s[start:i + 1])
                    if isinstance(w, dict) and "sessions" in w:
                        weeks.append(w)
                except Exception:
                    pass
                start = None
        elif ch == "]" and depth == 0:
            break
    return weeks or None


def generate_plan() -> dict:
    """Generate + persist a training plan for the active goal. Returns a summary.
    Raises ValueError on a bad state (no goal / race in the past / bad LLM output)."""
    goal = get_active_goal()
    if not goal:
        raise ValueError("Set a goal race first, then generate a plan.")
    try:
        race_date = date.fromisoformat(goal["race_date"])
    except (ValueError, KeyError):
        raise ValueError("The goal has no valid race date.")
    wsi = _week_start_idx()
    this_week = week_start_of(date.today(), wsi)
    if (race_date - this_week).days < 7:
        raise ValueError("Your race date must be at least a week away to generate a plan.")
    race_week = week_start_of(race_date, wsi)
    weeks_n = (race_week - this_week).days // 7 + 1
    plan_start = this_week
    if weeks_n > _MAX_WEEKS:
        # Count back from race day so build, peak and taper are never cut off;
        # weeks before plan_start are left for base blocks (set_training_block).
        weeks_n = _MAX_WEEKS
        plan_start = race_week - timedelta(days=7 * (_MAX_WEEKS - 1))

    course = None
    blob = get_race_config_blob("course_json")
    if blob:
        try:
            course = json.loads(blob)
        except Exception:
            course = None

    from api import llm  # lazy
    messages = _build_messages(goal, weeks_n, course, plan_start)
    weeks = None
    for attempt in range(2):  # one retry — the LLM output is occasionally malformed
        try:
            resp = llm.completion(messages, None, max_tokens=8000)
            weeks = _parse_weeks(resp.choices[0].message.content or "")
            if weeks:
                break
        except Exception:
            weeks = None
    if not weeks:
        raise ValueError("The model returned an unusable plan — try generating again.")

    # Replace from plan_start onward; earlier weeks (e.g. a coach-written base
    # block) are kept.
    try:
        row_count = _save_plan_range(render_plan_csv(weeks, plan_start, wsi), plan_start, None)
    except ValueError:
        raise ValueError("The generated plan had no sessions — try again.")
    return {"weeks": len(weeks), "sessions": row_count,
            "start": plan_start.isoformat(), "race_date": goal["race_date"]}
