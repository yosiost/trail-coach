"""Plan/week engine: week boundaries, HR zones, plan parsing — the core logic
the review flagged. No network; Strava is exercised only in its unconfigured
(optional) path."""
from datetime import date
from api import db, strava

_PLAN = ("Week,Date,Day,Phase,Session,Distance_km,Vert_m,HR_Zone\n"
         "W1,2026-03-02,Mon,Base,Easy,8,200,Z2\n"
         "W1,2026-03-07,Sat,Base,Long,20,600,Z2\n")


def test_strava_optional_when_unconfigured():
    assert strava.strava_configured() is False
    acts, err = strava.fetch_activities(date(2026, 1, 1), date(2026, 1, 7))
    assert acts == [] and err is None            # no creds -> empty, no error


def test_hr_zone_fixed_table_without_config():
    assert (strava.hr_zone(100), strava.hr_zone(130), strava.hr_zone(160)) == ("Z1", "Z2", "Z5")


def test_hr_zone_derived_from_configured_max_hr():
    db.set_config_blob("max_hr", "185")
    assert strava.hr_zone(120) == "Z1"           # 0.65 of max
    assert strava.hr_zone(166) == "Z4"           # 0.90 of max


def test_week_start_default_sunday_then_monday():
    wed = date(2026, 1, 7)
    assert strava._week_offset(wed) == 3         # default Sunday: 3 days back to Sun
    db.set_config_blob("week_start", "monday")
    assert strava._week_offset(wed) == 2         # Monday: 2 days back to Mon


def test_plan_reads_from_db_blob():
    db.create_goal("R", "2026-12-31", 20, 600, 7200, 7000, 8000)  # active race
    db.set_race_config_blob("plan_csv", _PLAN)
    weeks = strava.get_all_weeks()
    assert len(weeks) == 1 and weeks[0]["week_num"] == "W1"
    rows = strava.read_plan(date(2026, 3, 1), date(2026, 3, 8))
    assert [r["session"] for r in rows] == ["Easy", "Long"]


def test_this_week_renders_plan_without_strava():
    db.create_goal("R", "2026-12-31", 20, 600, 7200, 7000, 8000)  # active race
    db.set_race_config_blob("plan_csv", _PLAN)
    week = strava.get_this_week(week_start=date(2026, 3, 2), injected_activities=[])
    assert {"Easy", "Long"} <= {r["session"] for r in week["rows"]}
    assert week["summary"]["plan_km"] == 28      # 8 + 20, no Strava needed


def test_week_reports_activity_source(monkeypatch):
    db.create_goal("R", "2026-12-31", 20, 600, 7200, 7000, 8000)
    db.set_race_config_blob("plan_csv", _PLAN)
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "k")
    week = strava.get_this_week(week_start=date(2026, 3, 2), injected_activities=[])
    assert week["source"] == "garmin"  # frontend links/labels Actuals by this


def test_strava_ids_are_strings_and_prefer_id_str(monkeypatch):
    """Strava ids go 64-bit after 2027-06-01; JS can't hold them as numbers."""
    import io, json as _json
    big = 9007199254740993  # 2**53 + 1: not representable as a JS number
    raw = [
        {"id": big, "id_str": str(big), "start_date_local": "2026-03-03T07:00:00Z", "distance": 1000},
        {"id": 42, "start_date_local": "2026-03-04T07:00:00Z", "distance": 1000},  # pre-id_str payload
    ]
    monkeypatch.setattr(strava, "strava_configured", lambda: True)
    monkeypatch.setattr(strava, "_get_access_token", lambda: "t")

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(strava.urllib.request, "urlopen", lambda *a, **k: Resp(_json.dumps(raw).encode()))
    acts, err = strava._fetch_strava_activities(date(2026, 3, 2), date(2026, 3, 8))
    assert err is None
    assert [a["id"] for a in acts] == [str(big), "42"]
