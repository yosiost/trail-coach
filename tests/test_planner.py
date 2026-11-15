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


# ── Plan blocks, merging, count-back ─────────────────────────────────────────
from datetime import timedelta
from types import SimpleNamespace
from api import db, strava

_WEEK = {"phase": "Base", "sessions": [
    {"day": "Mon", "session": "Rest", "distance_km": 0},
    {"day": "Wed", "session": "Threshold 4x8min", "distance_km": 12, "vert_m": 200},
    {"day": "Sat", "session": "Long run", "distance_km": 25, "vert_m": 900}]}


def _race(days_out=200):
    return db.create_goal("Monte Rosa", (date.today() + timedelta(days=days_out)).isoformat(),
                          60, 4000, 1, 2, 3)["id"]


def test_merge_replaces_range_keeps_rest_and_renumbers_weeks():
    old = planner.render_plan_csv([_WEEK, _WEEK, _WEEK], date(2026, 1, 5), 0)   # W1..W3
    new = planner.render_plan_csv([{"phase": "Recovery", "sessions": [
        {"day": "Tue", "session": "Easy"}]}], date(2026, 1, 12), 0)
    rows = _rows(planner.merge_plan_csv(old, new, date(2026, 1, 12), date(2026, 1, 18), 0))
    assert [(r["Week"], r["Date"], r["Session"]) for r in rows if r["Week"] == "W2"] == \
        [("W2", "2026-01-13", "Easy")]                       # week 2 fully replaced
    assert {r["Week"] for r in rows} == {"W1", "W2", "W3"}  # labels stay chronological
    later = planner.render_plan_csv([_WEEK], date(2026, 2, 2), 0)                # labelled W1
    rows = _rows(planner.merge_plan_csv(old, later, date(2026, 2, 2), None, 0))
    assert rows[-1]["Week"] == "W5"                          # renumbered, no W1 clash


def test_write_block_creates_visible_weeks_and_clears_stale_edits():
    _race()
    db.set_config_blob("week_start", "monday")
    db.set_plan_overrides([{"date": "2026-11-18", "field": "Session", "value": "stale"}])
    s = planner.write_plan_block([_WEEK, _WEEK], "2026-11-18")   # mid-week -> snapped to Mon
    assert (s["start"], s["end"], s["sessions"]) == ("2026-11-16", "2026-11-29", 6)
    plan = strava.read_plan(date(2026, 11, 16), date(2026, 11, 29))
    assert [p["session"] for p in plan if p["date"] == "2026-11-18"] == ["Threshold 4x8min"]
    assert db.get_plan_overrides() == {}


def test_write_block_errors():
    _race()
    for weeks, start in (([], "2026-11-16"), ([_WEEK], "nope"), ([{"phase": "x"}], "2026-11-16")):
        try:
            planner.write_plan_block(weeks, start)
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_update_training_plan_rejects_dates_without_sessions():
    _race()
    planner.write_plan_block([_WEEK], "2026-11-16")
    db.set_config_blob("week_start", "monday")
    out = strava.update_plan_rows([{"date": "2026-12-01", "field": "Session", "value": "X"}])
    assert out.startswith("Error: nothing applied") and "set_training_block" in out
    assert db.get_plan_overrides() == {}


def test_generate_counts_back_from_race_and_keeps_earlier_block(monkeypatch):
    import sys
    rid = _race(days_out=41 * 7)                              # > 30 weeks away
    db.set_config_blob("week_start", "monday")
    this_week = planner.week_start_of(date.today(), 0)
    planner.write_plan_block([_WEEK], this_week.isoformat())  # coach's base block
    weeks_json = '{"weeks":[' + ",".join(['{"phase":"Build","sessions":[{"day":"Tue","session":"Hills"}]}'] * 30) + "]}"
    fake = SimpleNamespace(completion=lambda *a, **k: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=weeks_json))]))
    monkeypatch.setitem(sys.modules, "api.llm", fake)
    import api
    monkeypatch.setattr(api, "llm", fake, raising=False)
    s = planner.generate_plan()
    race_week = planner.week_start_of(date.fromisoformat(db.get_goal_by_id(rid)["race_date"]), 0)
    assert s["weeks"] == 30 and s["start"] == (race_week - timedelta(days=7 * 29)).isoformat()
    rows = _rows(db.get_race_config_blob("plan_csv"))
    assert rows[0]["Date"] == this_week.isoformat()           # base block kept
    assert rows[-1]["Date"] >= race_week.isoformat()          # plan reaches race week


def test_coach_tool_dispatch_writes_block():
    from api import chat
    _race()
    out, flags = chat._dispatch_tool("set_training_block", {"start_date": "2026-11-16", "weeks": [_WEEK]})
    assert out.startswith("Wrote 1 week(s), 3 sessions") and flags.get("plan_updated")
    out, _ = chat._dispatch_tool("set_training_block", {"start_date": "2026-11-16", "weeks": []})
    assert out.startswith("Error: nothing written.")
    assert any(t["name"] == "set_training_block" for t in chat.TOOLS)
