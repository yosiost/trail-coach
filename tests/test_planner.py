"""Plan generation: deterministic CSV rendering + tolerant LLM-output parsing."""
from datetime import date
import csv
import io
from api import planner


def _rows(csv_text):
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_render_assigns_dates_per_weekday_and_week():
    weeks = [
        {"phase": "Base", "sessions": [
            {"day": "Mon", "session": "Rest", "distance_km": 0},
            {"day": "Wed", "session": "Intervals", "distance_km": 10, "vert_m": 300,
             "duration_min": 60, "hr_zone": "Z4", "rpe": 7, "notes": "6x3min"},
            {"day": "Sat", "session": "Long run", "distance_km": 25}]},
        {"phase": "Build", "sessions": [{"day": "Sunday", "session": "Recovery"}]},
    ]
    text = planner.render_plan_csv(weeks, date(2026, 1, 5), 0)  # Monday-start week
    rows = _rows(text)
    assert list(rows[0].keys())[:5] == ["Week", "Date", "Day", "Phase", "Session"]
    by = {(r["Week"], r["Day"]): r for r in rows}
    assert by[("W1", "Wed")]["Date"] == "2026-01-07"   # Wed of wk starting Mon 1/5
    assert by[("W1", "Sat")]["Date"] == "2026-01-10"
    assert by[("W2", "Sun")]["Date"] == "2026-01-18"   # full name "Sunday" resolves too


def test_render_skips_unknown_days():
    text = planner.render_plan_csv(
        [{"phase": "Base", "sessions": [{"day": "Someday", "session": "X"},
                                        {"day": "Tue", "session": "Ok"}]}], date(2026, 1, 5), 0)
    assert len(_rows(text)) == 1


def test_parse_weeks_clean_and_fenced():
    assert planner._parse_weeks('{"weeks":[{"phase":"Base","sessions":[{"day":"Mon"}]}]}')[0]["phase"] == "Base"
    fenced = '```json\n{"weeks":[{"phase":"Build","sessions":[{"day":"Wed"}]}]}\n```'
    assert planner._parse_weeks(fenced)[0]["phase"] == "Build"


def test_parse_weeks_salvages_truncation():
    trunc = ('{"weeks":[{"phase":"Base","sessions":[{"day":"Mon"}]},'
             '{"phase":"Build","sessions":[{"day":"Wed","sess')  # cut off mid-object
    got = planner._parse_weeks(trunc)
    assert [w["phase"] for w in got] == ["Base"]   # keeps the complete week, drops the partial


def test_parse_weeks_bare_array_and_garbage():
    assert planner._parse_weeks('[{"phase":"Peak","sessions":[{}]}]')[0]["phase"] == "Peak"
    assert planner._parse_weeks("sorry, here's your plan") is None
