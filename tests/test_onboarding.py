"""Onboarding: validation, persistence, examples, demo seeding."""
import json
import pytest
from api import db, onboarding as ob


def test_hr_zones_derived_from_max_hr():
    z = ob.hr_zones_text(185)
    assert "max HR 185" in z and "Z5:" in z


def test_persist_profile_writes_refs_and_config():
    ob.persist_profile(name="Alex", weight_kg=70, height_cm=175, max_hr=185,
                       background="road->trail", units="mi", week_start="monday")
    refs = {r["name"]: r["content"] for r in db.get_athlete_references()}
    assert "Alex" in refs["basics"]
    assert "max HR 185" in refs["hr_zones"]
    assert db.get_config_blob("max_hr") == "185"
    assert db.get_config_blob("units") == "mi"
    assert db.get_config_blob("week_start") == "monday"


def test_persist_goal_creates_active_goal():
    g = ob.persist_goal("Skyline 50K", "2099-06-14", 50, 2400, "6:30:00", "6:15:00", "7:00:00", "notes")
    assert g["race_name"] == "Skyline 50K"
    assert db.sec_to_hms(g["aspirational_time_sec"]) == "6:30:00"


@pytest.mark.parametrize("bad", ["", "Week,Date\nW1,nope\n", "Week,Date,Phase,Session\n"])
def test_validate_plan_csv_rejects_bad(bad):
    with pytest.raises(ValueError):
        ob.validate_plan_csv(bad)


def test_validate_plan_csv_accepts_good():
    rows = ob.validate_plan_csv("Week,Date,Phase,Session\nW1,2099-01-05,Base,Easy\n")
    assert len(rows) == 1


@pytest.mark.parametrize("bad", [{}, {"race": "x"}, {"race": "x", "distance_km": "NaN", "vert_m": 1}])
def test_validate_course_rejects_bad(bad):
    with pytest.raises(ValueError):
        ob.validate_course(bad)


def test_examples_load_and_block_traversal():
    ex = ob.list_examples()
    assert ex["plans"] and ex["courses"]
    assert ob.load_example_plan(ex["plans"][0]).startswith("Week,")
    with pytest.raises(ValueError):
        ob.load_example_plan("../../server.py")


def test_complete_sets_onboarded_and_methodology():
    ob.complete(activity_source="strava", methodology="custom", methodology_text="  keep it minimal  ")
    assert db.is_onboarded() is True
    assert db.get_config_blob("coach_methodology") == "custom"
    assert db.get_config_blob("coach_methodology_text") == "keep it minimal"
    assert db.get_config_blob("activity_source") == "strava"


def test_seed_demo_blobs_is_idempotent():
    ob.seed_demo_blobs()
    assert json.loads(db.get_config_blob("course_json"))["race"].startswith("Skyline")
    assert db.get_config_blob("plan_csv").startswith("Week,")
    db.set_config_blob("plan_csv", "CUSTOM")
    ob.seed_demo_blobs()                                   # must not overwrite
    assert db.get_config_blob("plan_csv") == "CUSTOM"
