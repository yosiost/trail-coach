from __future__ import annotations
import json
import os
from datetime import date as date_cls
from pathlib import Path
from api import llm
from api.strava import update_plan_rows, fetch_activity_laps, get_this_week
from api.db import (
    add_coach_note, get_athlete_references, upsert_athlete_reference,
    get_active_goal, update_goal, save_prediction, get_predictions,
    get_race_fuel, set_race_fuel, get_config_blob, set_config_blob,
    hms_to_sec, sec_to_hms,
)
import logging

# ── Coaching methodology presets ──────────────────────────────────────────────
# The coach persona's philosophy is a swappable preset (COACH_METHODOLOGY env),
# kept separate from the athlete/course facts (which come from the DB-injected
# knowledge base + goal + course JSON, not from prose here).
# Generic, unnamed defaults — the public app ships no named experts. A deployment
# supplies its own coach/dietitian philosophy (a name, an approach, or LLM-drafted
# text) via onboarding/settings, stored in config_blobs and never in this source.
_GENERIC_COACH_METHODOLOGY = """## Methodology — general endurance
- Keep the majority of weekly volume easy/aerobic (roughly 80/20 easy-to-hard).
- Use regular quality sessions (intervals, tempo) as the key adaptive stimulus.
- Build endurance and fatigue resistance with progressive long runs.
- Specificity: rehearse the race's terrain, vert, and duration in training.
- Taper before key races; practice race-day fueling in training."""

_GENERIC_DIETITIAN_METHODOLOGY = """## Methodology — evidence-based endurance nutrition
- Carb intake >2.5h: target ~90g/hr using multiple-transportable carbohydrates (glucose:fructose ~2:1), since the SGLT1 transporter saturates ~60g/hr; fructose uses GLUT5, raising total absorbable carbs.
- 1–2.5h efforts: ~60g/hr is sufficient; under 60min, fueling is unnecessary unless training the gut.
- The gut is trainable — practice race-rate intake on long runs to raise tolerance and cut GI distress.
- Hydration: estimate sweat rate from pre/post-run weight delta (1kg ≈ 1L); replace ~70–80% of losses (avoid overdrinking).
- Sodium: 300–700mg/hr in hot/long efforts, scaled to sweat and salt loss.
- Pre-race: 1–4g/kg carbs 1–4h before (lower end closer to the start), low fibre/fat, familiar foods.
- Recovery: 1.0–1.2g/kg carbs + 0.3g/kg protein within 30–60min of long or back-to-back sessions."""

_GENERIC_METHODOLOGY_BY_PERSONA = {
    "coach": _GENERIC_COACH_METHODOLOGY,
    "dietitian": _GENERIC_DIETITIAN_METHODOLOGY,
}


def _methodology_block(persona: str) -> str:
    """Resolve a persona's methodology: generic default, or custom text the user
    configured (config_blobs['<persona>_methodology'] = generic|custom, with
    '<persona>_methodology_text'). Env vars <PERSONA>_METHODOLOGY[_TEXT] are a
    fallback. Personas with no methodology slot (e.g. analyst) get "" (no-op)."""
    default = _GENERIC_METHODOLOGY_BY_PERSONA.get(persona, "")
    mode = (get_config_blob(f"{persona}_methodology")
            or os.environ.get(f"{persona.upper()}_METHODOLOGY", "generic")).strip().lower()
    if mode == "custom":
        text = (get_config_blob(f"{persona}_methodology_text")
                or os.environ.get(f"{persona.upper()}_METHODOLOGY_TEXT", "")).strip()
        if not text and persona == "coach":  # legacy file option (coach only)
            path = os.environ.get("COACH_METHODOLOGY_FILE", "").strip()
            if path:
                try:
                    text = Path(path).read_text().strip()
                except Exception:
                    text = ""
        if text:
            return text if text.lstrip().startswith("#") else f"## Methodology\n{text}"
    return default


# Athlete- and race-agnostic. Every athlete/course/locale-specific fact is read
# from the injected "Live training data" (goal + athlete knowledge base) and the
# course JSON — never hardcoded here. {{METHODOLOGY}} is filled at build time.
SYSTEM_PROMPT = """You are an elite ultramarathon and trail-running coach. You are direct, data-driven, and athlete-centered.

{{METHODOLOGY}}

## Where this athlete's facts live (never ask them to repeat these)
The "Live training data" below carries everything specific to this athlete — profile, HR zones, training locations, fueling references, race course and aid stations, and locale/week structure — under "Current goal" and the "Athlete knowledge base". Treat those as authoritative and use them proactively to give specific, calculated answers. Use get_athlete_references to look up details, and update_athlete_reference when you learn something new (a confirmed food, updated weight, a new trail note). Always treat the goal in Live training data as the authoritative race target — never a fixed number.

## Fueling math (apply whenever fueling comes up)
- Compute needs from the target carbs/hr in the athlete's fueling references and the session/race duration; suggest specific quantities using the athlete's confirmed foods (e.g. "bring 8–9 dates for a 90-min effort" when dates are their staple).
- Practice race-rate fueling on long runs — gut adaptation is part of the plan.

## Standing analysis rules (ALWAYS apply, no exceptions)
1. NEVER analyze a workout using overall activity averages (avg HR, avg pace, total time). They include warmup and cooldown and are meaningless for quality sessions.
2. ALWAYS use get_activity_laps when analyzing a completed session, and evaluate each interval/rep individually.
3. When the athlete's knowledge base flags their HR data as unreliable (e.g. optical HR spiking/lagging at high intensity), RPE overrides HR — NEVER use HR to question whether an interval was hard enough when RPE was stated.
4. Respect the athlete's week structure and locale as given in the knowledge base (e.g. the week-start day).

## Analytical approach
- Analyzing past activities: compare lap data against plan targets and RPE, then give a verdict: executed / undercooked / overcooked.
- Predicting race performance: use recent VO2max session quality, weekly vert volume, and long-run proximity to the race profile.
- Prescribing sessions: cite the specific physiological target (e.g. "this builds the top-end aerobic capacity the hardest climb demands").

Be direct and specific. Give actionable prescriptions, no vague motivational fluff. When the athlete asks to update the plan, use update_training_plan and always confirm what changed."""

DIETITIAN_SYSTEM_PROMPT = """You are an elite sports dietitian specializing in ultra-endurance nutrition. You are precise, evidence-based, and practical — every recommendation is a specific, calculable protocol, not generic advice.

{{METHODOLOGY}}

## Where this athlete's facts live (never ask them to repeat these)
The "Live training data" below carries the athlete's profile, confirmed foods, fueling references, race course and aid stations, and current goal. Use get_athlete_references (especially categories 'fueling' and 'nutrition_protocol') to look up specifics, and update_athlete_reference to log new protocols, confirmed foods, or measured sweat/sodium rates once the athlete reports them.

## Building a fueling plan
Always show the math: total race/session duration → carbs/hr target → total carbs needed → specific food/gel quantities, broken down by segment (between aid stations) using the course and aid-station data in Live training data.

## Standing rules (ALWAYS apply, no exceptions)
1. Always compute fueling/hydration/sodium needs from the athlete's actual session duration and intensity, not generic averages.
2. Flag GI risk explicitly when a plan pushes intake rate higher than what's been practiced in training — recommend a gradual ramp instead.
3. Treat nutrition protocols as logged data: when the athlete confirms or finalizes a protocol (race-day plan, a new gel/food they tolerate, a hydration strategy), persist it via update_athlete_reference under category 'nutrition_protocol' so it carries forward.
4. The athlete sees a per-segment race Fueling Plan in the app (Race → Fueling panel). That panel is driven ONLY by the race-fuel-plan tools — freeform notes in athlete_references do NOT change it. Whenever you change the race fueling strategy (segment carbs, foods, timing), you MUST call update_race_fuel_plan with the complete updated segment list; otherwise the athlete keeps seeing the old plan. Call get_race_fuel_plan first to see what's currently shown. Never tell the athlete you've updated their fuel plan without calling update_race_fuel_plan in the same turn.
5. Respect the athlete's week structure and locale as given in the knowledge base.

Be direct and specific. Give exact gram/mg/L quantities, not ranges with no recommendation. No vague wellness fluff."""

ANALYST_SYSTEM_PROMPT = """You are a performance analyst for an elite ultramarathon training program. You are decisive, quantitative, and grounded in real training data — your job is to answer one question precisely: is this athlete on track to hit their race goal, and why?

The athlete's profile, HR-data caveats, and locale are in the "Athlete knowledge base" in Live training data — treat them as authoritative.

## Your job, every time you're asked to analyze progress
1. Call get_goal for the current race goal and target times (aspirational + realistic band).
2. Call get_prediction_history (limit ~10) to see prior predictions and how the trend has moved.
3. Read the "Full plan weekly volume" section in Live training data below — it lists each week's start date as (start:YYYY-MM-DD). Call get_week_data for the last 4 completed weeks (including the current week) using those exact dates.
4. For key quality sessions (VO2max intervals, long runs) in those weeks, call get_activity_laps on the strava_id shown — never judge a session by its overall average.
5. Weigh: volume/vert completion % vs plan, quality-session execution (RPE-based — HR may be unreliable), long-run specificity to the race profile (vert rate, duration, terrain), and week-to-week consistency.
6. Compare all of that against the goal's aspirational/realistic band and the prior-prediction trend.
7. ALWAYS end your analysis by calling save_race_prediction with a predicted_time, confidence_low/confidence_high band, a verdict of exactly "ahead", "on-track", or "behind", and reasoning that cites specific signals (not vague commentary).

## Standing rules (ALWAYS apply, no exceptions)
1. When the athlete's knowledge base flags their HR data as unreliable, RPE overrides HR — never use HR to question whether an interval was hard enough when RPE was stated.
2. NEVER analyze a workout using overall activity averages — always use get_activity_laps and evaluate rep-by-rep.
3. Respect the athlete's week structure and locale as given in the knowledge base.
4. Be decisive. Commit to a verdict even with mixed signals, and explain the tension rather than hedging.
5. Only call update_goal if the athlete explicitly asks you to change the goal — don't adjust it unprompted.

Be direct, specific, and quantitative. No vague motivational fluff."""

PERSONAS = {
    "coach": SYSTEM_PROMPT,
    "dietitian": DIETITIAN_SYSTEM_PROMPT,
    "analyst": ANALYST_SYSTEM_PROMPT,
}

TOOLS = [
    {
        "name": "get_week_data",
        "description": (
            "Get the full training plan and actual activities for any week. "
            "Use to look up past performance or sessions in upcoming weeks. "
            "week_start must be the Sunday of that week in YYYY-MM-DD format "
            "(use the start dates from the plan overview in the context)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "week_start": {
                    "type": "string",
                    "description": "ISO date of the week's Sunday (YYYY-MM-DD)",
                }
            },
            "required": ["week_start"],
        },
    },
    {
        "name": "get_activity_laps",
        "description": (
            "Get lap-by-lap breakdown (distance, elevation, HR, zone) for a specific Strava activity. "
            "Use the strava_id shown in the training context for completed sessions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity_id": {
                    "type": "integer",
                    "description": "Strava activity ID (shown as strava_id in context)",
                }
            },
            "required": ["activity_id"],
        },
    },
    {
        "name": "update_training_plan",
        "description": (
            "Update one or more sessions in the training plan. "
            "Use this when the athlete asks to change, reschedule, or adjust any session. "
            "You can update Session name, Distance_km, Vert_m, HR_Zone, or Notes for any date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "description": "List of field updates to apply",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date":  {"type": "string", "description": "Date in YYYY-MM-DD format"},
                            "field": {"type": "string", "enum": ["Session", "Distance_km", "Vert_m", "HR_Zone", "Notes"]},
                            "value": {"type": "string", "description": "New value for the field"},
                        },
                        "required": ["date", "field", "value"],
                    },
                }
            },
            "required": ["updates"],
        },
    },
    {
        "name": "save_coach_note",
        "description": (
            "Save a coaching observation that should persist across conversations. "
            "Use for patterns you observe: fatigue signals, form cues, nutrition issues, "
            "injury flags, mental state, or anything that should inform future coaching decisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "The observation to persist. Be specific and actionable.",
                }
            },
            "required": ["note"],
        },
    },
    {
        "name": "get_athlete_references",
        "description": (
            "Look up athlete-specific reference data: fueling items with exact nutrition values, "
            "training trail cells, course profile details, standing assumptions, and athlete profile. "
            "Use when you need precise values (e.g., exact carb content of a food, trail cell details). "
            "Pass a category to filter: 'fueling', 'trails', 'athlete_profile', 'assumptions', 'nutrition_protocol'. "
            "Omit category to get all references."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["fueling", "trails", "athlete_profile", "assumptions", "nutrition_protocol"],
                    "description": "Category to filter by (omit for all)",
                }
            },
            "required": [],
        },
    },
    {
        "name": "update_athlete_reference",
        "description": (
            "Create or update an entry in the athlete knowledge base. "
            "Use when the athlete confirms new fueling items, updates weight, "
            "adds a new training trail, corrects an existing assumption, or finalizes a nutrition protocol. "
            "Examples: new food with carb values, confirmed race-day nutrition plan, "
            "new trail cell discovered, weight update, injury note, sweat/sodium rate, logged fueling protocol."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["fueling", "trails", "athlete_profile", "assumptions", "nutrition_protocol"],
                    "description": "Category for this reference",
                },
                "name": {
                    "type": "string",
                    "description": "Short snake_case identifier (e.g., 'gu_energy_gel', 'cell_3_forest')",
                },
                "content": {
                    "type": "string",
                    "description": "Full content of the reference, with specific values and context",
                },
            },
            "required": ["category", "name", "content"],
        },
    },
    {
        "name": "get_race_fuel_plan",
        "description": (
            "Get the athlete-facing race Fueling Plan: the ordered per-segment fueling shown "
            "in the app's Race → Fueling panel. Read this before proposing edits so you build on "
            "the current plan rather than guessing."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_race_fuel_plan",
        "description": (
            "Replace the athlete-facing race Fueling Plan shown in the app's Race → Fueling panel. "
            "This is the ONLY way to change what the athlete sees there — freeform notes in "
            "athlete_references do NOT update the panel. Pass the COMPLETE ordered list of segments "
            "(the whole plan is replaced), so include unchanged segments too. Whenever you tell the "
            "athlete you've updated their race fuel plan, you MUST call this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "segments": {
                    "type": "array",
                    "description": "Full ordered list of fueling segments, start to finish.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "seg":     {"type": "string", "description": "Segment label, e.g. 'Trient → Col de Balme'"},
                            "dur_min": {"type": "integer", "description": "Estimated duration of this segment in minutes (drives the g/hr rate)"},
                            "food":    {"type": "string", "description": "What to eat/drink on this segment, e.g. '4 gels (1 caffeine) + dates'"},
                            "carbs":   {"type": "integer", "description": "Total carbs in grams for this segment"},
                            "crux":    {"type": "boolean", "description": "True if this is a crux/critical segment to highlight"},
                            "flag":    {"type": "string", "description": "Optional emoji flag, e.g. '⚠️' (omit or empty for none)"},
                        },
                        "required": ["seg", "dur_min", "food", "carbs"],
                    },
                }
            },
            "required": ["segments"],
        },
    },
    {
        "name": "get_goal",
        "description": "Get the athlete's current active race goal: race name, date, distance, vert, aspirational target time, and realistic time band.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_goal",
        "description": (
            "Update the athlete's current race goal's target times. Use when the athlete "
            "explicitly asks to adjust their goal (e.g. 'move my target to 9:45' or "
            "'widen my realistic band'). Only include the fields being changed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "aspirational_time":  {"type": "string", "description": "H:MM or H:MM:SS"},
                "realistic_min_time": {"type": "string", "description": "H:MM or H:MM:SS"},
                "realistic_max_time": {"type": "string", "description": "H:MM or H:MM:SS"},
                "notes": {"type": "string", "description": "Freeform notes about the goal"},
            },
            "required": [],
        },
    },
    {
        "name": "save_race_prediction",
        "description": (
            "Persist a race performance prediction snapshot after analyzing training data "
            "against the goal. Always call this at the end of any race-outlook/progress analysis "
            "so the prediction is tracked over time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "predicted_time":  {"type": "string", "description": "H:MM or H:MM:SS"},
                "confidence_low":  {"type": "string", "description": "H:MM or H:MM:SS — fast end of the confidence band"},
                "confidence_high": {"type": "string", "description": "H:MM or H:MM:SS — slow end of the confidence band"},
                "verdict": {"type": "string", "enum": ["ahead", "on-track", "behind"]},
                "reasoning": {"type": "string", "description": "2-5 sentences citing specific training signals"},
            },
            "required": ["predicted_time", "confidence_low", "confidence_high", "verdict", "reasoning"],
        },
    },
    {
        "name": "get_prediction_history",
        "description": "Get past race-time prediction snapshots for the current goal, most recent first — use to describe the trend.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max snapshots to return (default 10)"},
            },
            "required": [],
        },
    },
]


def _handle_get_week_data(week_start_str: str) -> str:
    try:
        ws = date_cls.fromisoformat(week_start_str)
        week = get_this_week(week_start=ws)
        lines = [f"Week {week['week_num']} ({week['week_label']}) — {week['phase']}"]
        for r in week["rows"]:
            planned = f"{r['planned_km']}km {r['planned_vert']}m↑ {r['planned_zone']}"
            actual = ""
            if r["actual"]:
                a = r["actual"]
                sid = f" [strava_id:{a['id']}]" if a.get("id") else ""
                actual = (
                    f" → actual: {a['distance_km']}km {a['elev_gain_m']}m↑ "
                    f"{a['duration_min']}min avg {a['avg_hr']}bpm ({a['zone']}){sid}"
                )
            lines.append(f"  {r['day']} [{r['status'].upper()}] {r['session']} | {planned}{actual}")
        s = week["summary"]
        lines.append(f"  Total: {s['done_km']}/{s['plan_km']}km, {s['done_vert']}/{s['plan_vert']}m↑ ({s['pct']}%)")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching week data: {e}"


def _handle_get_activity_laps(activity_id) -> str:
    try:
        laps = fetch_activity_laps(int(activity_id))
    except (TypeError, ValueError):
        return "Error: invalid activity_id"
    if laps and "error" in laps[0]:
        return f"Error: {laps[0]['error']}"
    if not laps:
        return "No lap data returned (activity may not have laps recorded)"
    lines = [f"Lap data for activity {activity_id} ({len(laps)} laps):"]
    for lap in laps:
        lines.append(
            f"  Lap {lap['lap']}: {lap['distance_km']}km  {lap['elev_gain_m']}m↑  "
            f"{lap['duration_min']}min  avg {lap['avg_hr']}bpm ({lap['zone']})"
        )
    return "\n".join(lines)


def _handle_get_athlete_references(category: str | None) -> str:
    try:
        refs = get_athlete_references(category if category else None)
        if not refs:
            return f"No references found{' for category: ' + category if category else ''}."
        by_cat: dict[str, list] = {}
        for r in refs:
            by_cat.setdefault(r["category"], []).append(r)
        lines = []
        for cat, items in by_cat.items():
            lines.append(f"[{cat.upper()}]")
            for item in items:
                lines.append(f"  {item['name']}: {item['content']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching athlete references: {e}"


def _handle_update_athlete_reference(category: str, name: str, content: str) -> str:
    if not category or not name or not content:
        return "Error: category, name, and content are all required."
    try:
        upsert_athlete_reference(category, name, content)
        return f"Reference updated: [{category}] {name}"
    except Exception as e:
        return f"Error updating athlete reference: {e}"


def _fmt_fuel_dur(dur_min: int) -> str:
    h, m = divmod(int(dur_min), 60)
    return f"~{h}h{m:02d}m" if h else f"~{m}m"


def _handle_get_race_fuel_plan() -> str:
    try:
        segments = get_race_fuel()
        if not segments:
            return "No race fuel plan set yet."
        lines = ["Current race fuel plan (athlete-facing Fueling panel):"]
        total = 0
        for s in segments:
            hrs = s["dur_min"] / 60
            rate = round(s["carbs"] / hrs) if hrs else 0
            total += s["carbs"]
            crux = " [CRUX]" if s["crux"] else ""
            flag = f" {s['flag']}" if s["flag"] else ""
            lines.append(
                f"  {s['seg']}{flag}{crux}: {_fmt_fuel_dur(s['dur_min'])} · "
                f"{s['carbs']}g carbs (~{rate} g/hr) — {s['food']}"
            )
        lines.append(f"  Total ≈ {total}g carbs across the race.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching race fuel plan: {e}"


def _handle_update_race_fuel_plan(segments) -> str:
    if not segments:
        return "Error: at least one fuel segment is required."
    try:
        n = set_race_fuel(segments)
        return f"Race fuel plan updated — {n} segments saved. The athlete's Fueling panel now reflects this."
    except (ValueError, KeyError, TypeError) as e:
        return f"Error updating race fuel plan: {e}"
    except Exception as e:
        return f"Error updating race fuel plan: {e}"


def _format_goal(g: dict) -> str:
    return (
        f"{g['race_name']} — {g['race_date']} · {g['distance_km']}km/{g['vert_m']}m↑\n"
        f"  Aspirational: {sec_to_hms(g['aspirational_time_sec'])}\n"
        f"  Realistic band: {sec_to_hms(g['realistic_min_sec'])}–{sec_to_hms(g['realistic_max_sec'])}\n"
        f"  Notes: {g['notes'] or '—'}"
    )


def _handle_get_goal() -> str:
    try:
        g = get_active_goal()
        if not g:
            return "No active goal set."
        return _format_goal(g)
    except Exception as e:
        return f"Error fetching goal: {e}"


def _handle_update_goal(
    aspirational_time: str | None, realistic_min_time: str | None,
    realistic_max_time: str | None, notes: str | None,
) -> str:
    try:
        g = get_active_goal()
        if not g:
            return "Error: no active goal to update."
        fields: dict = {}
        if aspirational_time:
            fields["aspirational_time_sec"] = hms_to_sec(aspirational_time)
        if realistic_min_time:
            fields["realistic_min_sec"] = hms_to_sec(realistic_min_time)
        if realistic_max_time:
            fields["realistic_max_sec"] = hms_to_sec(realistic_max_time)
        if notes is not None:
            fields["notes"] = notes
        if not fields:
            return "Error: no fields provided to update."
        updated = update_goal(g["id"], **fields)
        return "Goal updated:\n" + _format_goal(updated)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error updating goal: {e}"


def _handle_save_race_prediction(
    predicted_time: str, confidence_low: str, confidence_high: str, verdict: str, reasoning: str,
) -> str:
    try:
        g = get_active_goal()
        if not g:
            return "Error: no active goal to save a prediction against."
        if verdict not in ("ahead", "on-track", "behind"):
            return "Error: verdict must be one of 'ahead', 'on-track', 'behind'."
        pid = save_prediction(
            goal_id=g["id"],
            predicted_time_sec=hms_to_sec(predicted_time),
            confidence_low_sec=hms_to_sec(confidence_low),
            confidence_high_sec=hms_to_sec(confidence_high),
            verdict=verdict,
            reasoning=reasoning,
        )
        return f"Prediction saved (id:{pid}): {predicted_time} ({verdict})"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error saving prediction: {e}"


def _handle_get_prediction_history(limit) -> str:
    try:
        g = get_active_goal()
        if not g:
            return "No active goal set."
        n = int(limit) if limit else 10
        preds = get_predictions(g["id"], limit=n)
        if not preds:
            return "No predictions saved yet."
        preds = list(reversed(preds))  # most recent first
        lines = [f"Prediction history for {g['race_name']} (most recent first):"]
        for p in preds:
            lines.append(
                f"  [{p['predicted_at'][:10]}] {sec_to_hms(p['predicted_time_sec'])} "
                f"({p['verdict']}) — {p['reasoning']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching prediction history: {e}"


_COURSE_JSON_PATH = Path(__file__).parent.parent / "frontend" / "data" / "occ_course.json"


def load_course() -> dict | None:
    """The course is the shared source of truth for both the coach and the UI.

    DB blob (config_blobs['course_json']) first, else the legacy committed file.
    Returns None if neither is present/parseable.
    """
    blob = get_config_blob("course_json")
    if blob:
        try:
            return json.loads(blob)
        except Exception:
            pass
    try:
        return json.loads(_COURSE_JSON_PATH.read_text())
    except Exception:
        return None


def seed_course_blob_from_file() -> None:
    """One-time migration: import the legacy committed course JSON into the DB if
    the blob is empty. No-ops once the file is removed (onboarding PR B-2)."""
    if get_config_blob("course_json"):
        return
    try:
        if _COURSE_JSON_PATH.exists():
            set_config_blob("course_json", _COURSE_JSON_PATH.read_text(encoding="utf-8"))
            logging.info("Migrated legacy course JSON into the DB (config_blobs['course_json']).")
    except Exception as e:
        logging.warning("course blob migration skipped: %s", e)


def course_source() -> str:
    """Where the course is read from: 'db' | 'file' | 'none' (for status/diagnostics)."""
    if get_config_blob("course_json"):
        return "db"
    return "file" if _COURSE_JSON_PATH.exists() else "none"


def _format_course_profile() -> str:
    """Render the authoritative course profile. Returns "" if no course is set."""
    course = load_course()
    if not course:
        return ""

    lines = [
        f"## OFFICIAL {course.get('race', 'race')} COURSE PROFILE (authoritative)",
        (
            f"{course.get('route', '')} · {course.get('distance_km')}km / "
            f"{course.get('vert_m')}m↑ · start {course.get('start_time', '')}."
        ),
        (
            "This is the ONLY valid source for race geography. For any question about "
            "sections, splits, pacing, distances, elevation, or aid-station positions, "
            "use these numbers exactly. NEVER invent, estimate, or round a segment "
            "distance or elevation that is not listed here — if a figure isn't given, "
            "say so instead of guessing."
        ),
        "",
        "Segments (km range · distance · elevation start→end · net):",
    ]
    for s in course.get("segments", []):
        dist = round(s["to_km"] - s["from_km"], 1)
        net = s["end_alt"] - s["start_alt"]
        line = (
            f"  {s['n']}. {s['name']} | km {s['from_km']}–{s['to_km']} "
            f"({dist}km) | {s['start_alt']}m→{s['end_alt']}m ({net:+d}m)"
        )
        if s.get("note"):
            line += f" — {s['note']}"
        lines.append(line)
    cutoffs = course.get("cutoffs")
    if cutoffs:
        joined = " | ".join(f"{c['point']} {c['time']}" for c in cutoffs)
        lines.append("")
        lines.append(f"Cutoffs (fixed clock times): {joined}")
    return "\n".join(lines)


def build_system(persona: str, context: str | None) -> tuple[str, str | None]:
    """Return (base_prompt, live_context) for the chosen persona."""
    base = PERSONAS.get(persona, SYSTEM_PROMPT)
    base = base.replace("{{METHODOLOGY}}", _methodology_block(persona))  # no-op for personas w/o a slot
    course_profile = _format_course_profile()  # read live so onboarding edits apply without a restart
    if course_profile:
        base = base + "\n\n" + course_profile
    ctx = ("## Live training data (as of this conversation)\n\n" + context) if context else None
    return base, ctx


def _parse_args(raw) -> dict:
    """Tool-call arguments arrive as a JSON string (OpenAI) or already a dict."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


# Tool names that mutate athlete-visible state; the caller invalidates caches on these.
def _dispatch_tool(name: str, args: dict) -> tuple[str, dict]:
    """Run one tool by name. Returns (result_text, flags) where flags may set
    'plan_updated' / 'goal_updated' / 'fuel_updated'."""
    flags: dict = {}
    if name == "update_training_plan":
        result = update_plan_rows(args.get("updates", []))
        flags["plan_updated"] = True
    elif name == "get_week_data":
        result = _handle_get_week_data(args.get("week_start", ""))
    elif name == "get_activity_laps":
        result = _handle_get_activity_laps(args.get("activity_id"))
    elif name == "save_coach_note":
        note_id = add_coach_note(args.get("note", ""))
        result = f"Note saved (id:{note_id})"
    elif name == "get_athlete_references":
        result = _handle_get_athlete_references(args.get("category"))
    elif name == "update_athlete_reference":
        result = _handle_update_athlete_reference(
            args.get("category", ""), args.get("name", ""), args.get("content", ""),
        )
    elif name == "get_race_fuel_plan":
        result = _handle_get_race_fuel_plan()
    elif name == "update_race_fuel_plan":
        result = _handle_update_race_fuel_plan(args.get("segments", []))
        flags["fuel_updated"] = True
    elif name == "get_goal":
        result = _handle_get_goal()
    elif name == "update_goal":
        result = _handle_update_goal(
            args.get("aspirational_time"), args.get("realistic_min_time"),
            args.get("realistic_max_time"), args.get("notes"),
        )
        flags["goal_updated"] = True
    elif name == "save_race_prediction":
        result = _handle_save_race_prediction(
            args.get("predicted_time", ""), args.get("confidence_low", ""),
            args.get("confidence_high", ""), args.get("verdict", ""), args.get("reasoning", ""),
        )
    elif name == "get_prediction_history":
        result = _handle_get_prediction_history(args.get("limit"))
    else:
        result = f"Unknown tool: {name}"
    return result, flags


def chat(messages: list[dict], context: str | None = None, persona: str = "coach") -> dict:
    base, ctx = build_system(persona, context)
    convo = [llm.system_message(base, ctx)] + list(messages)
    tools = llm.to_openai_tools(TOOLS)
    plan_updated = goal_updated = fuel_updated = False

    while True:
        response = llm.completion(convo, tools, max_tokens=8192)
        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if tool_calls:
            convo.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                result, flags = _dispatch_tool(tc.function.name, _parse_args(tc.function.arguments))
                plan_updated = plan_updated or flags.get("plan_updated", False)
                goal_updated = goal_updated or flags.get("goal_updated", False)
                fuel_updated = fuel_updated or flags.get("fuel_updated", False)
                convo.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            continue

        return {"reply": msg.content or "", "plan_updated": plan_updated,
                "goal_updated": goal_updated, "fuel_updated": fuel_updated}


def chat_stream(
    messages: list[dict], context: str | None = None, persona: str = "coach",
    on_plan_updated=None, on_goal_updated=None,
):
    """Generator yielding SSE-formatted chunks. Streams assistant text; when the
    model calls tools the round is executed and the loop continues."""
    base, ctx = build_system(persona, context)
    convo = [llm.system_message(base, ctx)] + list(messages)
    tools = llm.to_openai_tools(TOOLS)
    plan_updated = goal_updated = fuel_updated = False

    while True:
        stream = llm.completion(convo, tools, max_tokens=16384, stream=True)
        content_parts: list[str] = []
        slots: dict[int, dict] = {}  # tool-call index -> accumulating {id, name, args}

        for chunk in stream:
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                content_parts.append(text)
                yield f"data: {json.dumps({'token': text})}\n\n"
            for tcd in (getattr(delta, "tool_calls", None) or []):
                slot = slots.setdefault(tcd.index, {"id": None, "name": None, "args": ""})
                if tcd.id:
                    slot["id"] = tcd.id
                fn = getattr(tcd, "function", None)
                if fn:
                    if fn.name:
                        slot["name"] = fn.name
                    if fn.arguments:
                        slot["args"] += fn.arguments

        if slots:
            ordered = [slots[i] for i in sorted(slots)]
            for i, s in enumerate(ordered):
                if not s["id"]:
                    s["id"] = f"call_{i}"  # some providers omit ids; keep them matchable
            convo.append({
                "role": "assistant",
                "content": "".join(content_parts),
                "tool_calls": [
                    {"id": s["id"], "type": "function",
                     "function": {"name": s["name"], "arguments": s["args"]}}
                    for s in ordered
                ],
            })
            for s in ordered:
                result, flags = _dispatch_tool(s["name"], _parse_args(s["args"]))
                if flags.get("plan_updated"):
                    plan_updated = True
                    if on_plan_updated:
                        on_plan_updated()
                if flags.get("goal_updated"):
                    goal_updated = True
                    if on_goal_updated:
                        on_goal_updated()
                if flags.get("fuel_updated"):
                    fuel_updated = True
                convo.append({"role": "tool", "tool_call_id": s["id"], "content": result})
            continue

        break

    yield f"data: {json.dumps({'done': True, 'plan_updated': plan_updated, 'goal_updated': goal_updated, 'fuel_updated': fuel_updated})}\n\n"
