"""Persistence layer: config blobs, goals, onboarding flag, time helpers."""
from datetime import date
from api import db


def test_config_blob_roundtrip_and_upsert():
    assert db.get_config_blob("k") is None
    db.set_config_blob("k", "v1")
    assert db.get_config_blob("k") == "v1"
    db.set_config_blob("k", "v2")            # upsert, not duplicate
    assert db.get_config_blob("k") == "v2"


def test_hms_seconds_roundtrip():
    for hms in ("0:00:00", "6:30:00", "10:05:09"):
        assert db.sec_to_hms(db.hms_to_sec(hms)) == hms
    assert db.hms_to_sec("1:30") == 5400      # H:MM form


def test_onboarded_heals_only_when_goal_exists():
    assert db.is_onboarded() is False
    db.heal_onboarded_flag()                  # no active goal -> stays false
    assert db.is_onboarded() is False
    db.create_goal("R", "2099-01-01", 50, 2400, 1, 2, 3)
    db.heal_onboarded_flag()                  # goal exists -> heals true
    assert db.is_onboarded() is True


def test_create_goal_is_active_and_archives_previous():
    db.create_goal("First", "2099-01-01", 50, 2400, 1, 2, 3)
    g = db.create_goal("Second", "2099-06-01", 80, 4000, 1, 2, 3)
    active = db.get_active_goal()
    assert active["race_name"] == "Second"    # newest active
    assert db.get_goal_by_id(g["id"])["status"] == "active"


def test_update_goal_edits_full_race():
    g = db.create_goal("R", "2099-01-01", 50, 2400, 100, 90, 110)
    u = db.update_goal(g["id"], race_name="Ultra", race_date="2099-09-09",
                       distance_km=161.0, vert_m=6000)
    assert (u["race_name"], u["race_date"], u["distance_km"], u["vert_m"]) == \
           ("Ultra", "2099-09-09", 161.0, 6000)


def test_init_goals_seeds_a_future_race():
    db.init_goals()
    g = db.get_active_goal()
    assert date.fromisoformat(g["race_date"]) > date.today()


def test_athlete_references_limit_orders_by_recency_and_caps_count():
    for i in range(25):
        db.upsert_athlete_reference("fueling", f"item_{i:02d}", "content")
    all_refs = db.get_athlete_references()
    assert len(all_refs) == 25
    assert [r["name"] for r in all_refs] == sorted(r["name"] for r in all_refs)  # alphabetical, unlimited

    capped = db.get_athlete_references(limit=5)
    assert len(capped) == 5
    assert [r["name"] for r in capped] == ["item_24", "item_23", "item_22", "item_21", "item_20"]  # most-recent first


def test_athlete_references_limit_combines_with_category():
    db.upsert_athlete_reference("fueling", "gel", "content")
    db.upsert_athlete_reference("trails", "cell_1", "content")
    db.upsert_athlete_reference("fueling", "dates", "content")
    capped = db.get_athlete_references(category="fueling", limit=1)
    assert len(capped) == 1 and capped[0]["name"] == "dates"  # most recently upserted fueling item
