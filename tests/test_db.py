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


def test_create_goal_activates_new_race_without_archiving_prior():
    a = db.create_goal("First", "2099-01-01", 50, 2400, 1, 2, 3)
    b = db.create_goal("Second", "2099-06-01", 80, 4000, 1, 2, 3)
    # New race becomes active; the prior race is preserved, not archived.
    assert db.get_active_race_id() == b["id"]
    assert db.get_active_goal()["race_name"] == "Second"
    assert db.get_goal_by_id(a["id"])["status"] == "active"  # still here, switchable
    # Switching the pointer brings the prior race back into view.
    db.set_active_race_id(a["id"])
    assert db.get_active_goal()["race_name"] == "First"


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


# ── Multi-race scoping ────────────────────────────────────────────────────────

def _two_races():
    a = db.create_goal("Race A", "2099-01-01", 50, 2400, 1, 2, 3)
    b = db.create_goal("Race B", "2099-06-01", 100, 5000, 1, 2, 3)
    return a["id"], b["id"]


def test_fuel_and_plan_are_isolated_per_race():
    a, b = _two_races()                                   # active = B
    db.set_race_fuel([{"seg": "S", "dur_min": 60, "food": "gels", "carbs": 60}])
    db.set_race_config_blob("plan_csv", "PLAN_B")
    assert len(db.get_race_fuel()) == 1                   # B has its own fuel
    db.set_active_race_id(a)
    assert db.get_race_fuel() == []                       # A unaffected, still empty
    assert db.get_race_config_blob("plan_csv") is None    # A has no plan of its own
    db.set_race_fuel([{"seg": "X", "dur_min": 30, "food": "dates", "carbs": 30},
                      {"seg": "Y", "dur_min": 30, "food": "cola", "carbs": 30}])
    db.set_active_race_id(b)
    assert len(db.get_race_fuel()) == 1                   # B's fuel untouched by A's edits
    assert db.get_race_config_blob("plan_csv") == "PLAN_B"


def test_chat_sessions_and_notes_scope_to_active_race():
    a, b = _two_races()                                   # active = B
    db.upsert_session("s_b", "B chat", [], "t", "t", "coach")
    db.add_coach_note("B note")
    assert [s["id"] for s in db.list_sessions()] == ["s_b"]
    assert len(db.get_coach_notes()) == 1
    db.set_active_race_id(a)
    assert db.list_sessions() == []                       # B's chat does not leak into A
    assert db.get_coach_notes() == []


def test_references_split_shared_vs_race_scoped():
    a, b = _two_races()                                   # active = B
    db.upsert_athlete_reference("assumptions", "hr_zones", "shared")   # athlete-level
    db.upsert_athlete_reference("fueling", "aid_stations", "B aid")    # race-scoped
    names_b = {r["name"]: r["content"] for r in db.get_athlete_references()}
    assert names_b["hr_zones"] == "shared" and names_b["aid_stations"] == "B aid"
    db.set_active_race_id(a)
    names_a = {r["name"]: r["content"] for r in db.get_athlete_references()}
    assert names_a["hr_zones"] == "shared"                # shared row visible under A
    assert "aid_stations" not in names_a                  # B's aid stations do not leak to A
    db.upsert_athlete_reference("fueling", "aid_stations", "A aid")
    db.set_active_race_id(b)
    assert db.get_athlete_references(category="fueling")[0]["content"] == "B aid"  # unchanged


def test_custom_named_course_ref_is_race_scoped():
    a, b = _two_races()                                   # active = B
    db.upsert_athlete_reference("trails", "occ_course_profile", "B course")
    db.set_active_race_id(a)
    names_a = {r["name"] for r in db.get_athlete_references()}
    assert "occ_course_profile" not in names_a            # B's course note does not leak to A


def test_migration_moves_leaked_custom_course_ref_to_its_race():
    a, b = _two_races()
    with db._conn() as conn:                              # simulate pre-fix state: leaked into shared bucket
        db._run(conn,
                "INSERT INTO athlete_references (race_id, category, name, content, updated_at) "
                "VALUES (0, 'trails', 'occ_course_profile', 'A course', '2000-01-01')")
        db._run(conn, "DELETE FROM config_blobs WHERE key = 'migrated_custom_course_refs'")
    db.init_db()
    db.set_active_race_id(b)
    assert "occ_course_profile" not in {r["name"] for r in db.get_athlete_references()}
    db.set_active_race_id(a)                              # predates both races -> oldest race
    assert "occ_course_profile" in {r["name"] for r in db.get_athlete_references()}


def _legacy_blobs():
    """Simulate a pre-multi-race DB: single unscoped course/plan blobs."""
    with db._conn() as conn:
        for k, v in (("course_json", '{"race":"OCC"}'), ("plan_csv", "OCC PLAN")):
            db._run(conn, "INSERT INTO config_blobs (key, value, updated_at) VALUES (?,?,?)",
                    (k, v, "2026-06-01T00:00:00+00:00"))


def test_legacy_blobs_migrate_once_and_never_leak_into_new_race():
    occ = db.create_goal("OCC", "2026-08-27", 55, 3500, 1, 2, 3)["id"]
    _legacy_blobs()
    db.init_db()                                          # first deploy with multi-race
    assert db.get_race_config_blob("course_json", occ) == '{"race":"OCC"}'
    assert db.get_config_blob("course_json") is None      # legacy key dropped
    mr = db.create_goal("Monte Rosa", "2027-07-01", 60, 4000, 1, 2, 3)["id"]
    db.init_db()                                          # next deploy
    assert db.get_race_config_blob("course_json", mr) is None
    assert db.get_race_config_blob("plan_csv", mr) is None


def test_heals_copies_leaked_by_earlier_versions():
    occ = db.create_goal("OCC", "2026-08-27", 55, 3500, 1, 2, 3)["id"]
    mr = db.create_goal("Monte Rosa", "2027-07-01", 60, 4000, 1, 2, 3)["id"]
    _legacy_blobs()
    with db._conn() as conn:                              # state left by the old migration
        for rid in (occ, mr):
            for k, v in (("course_json", '{"race":"OCC"}'), ("plan_csv", "OCC PLAN")):
                db._run(conn, "INSERT INTO config_blobs (key, value, updated_at) VALUES (?,?,?)",
                        (f"{k}:{rid}", v, "2026-06-01T00:00:00+00:00"))
    db.init_db()
    assert db.get_race_config_blob("course_json", occ) == '{"race":"OCC"}'   # original kept
    assert db.get_race_config_blob("plan_csv", occ) == "OCC PLAN"
    assert db.get_race_config_blob("course_json", mr) is None               # leak removed
    assert db.get_race_config_blob("plan_csv", mr) is None
    assert db.get_config_blob("plan_csv") is None


def test_heal_keeps_deliberate_copy_forward_and_edits():
    occ = db.create_goal("OCC", "2026-08-27", 55, 3500, 1, 2, 3)["id"]
    mr = db.create_goal("Monte Rosa", "2027-07-01", 60, 4000, 1, 2, 3)["id"]
    _legacy_blobs()
    with db._conn() as conn:
        db._run(conn, "INSERT INTO config_blobs (key, value, updated_at) VALUES (?,?,?)",
                (f"plan_csv:{occ}", "OCC PLAN", "2026-06-01T00:00:00+00:00"))
    db.copy_race_data(occ, mr, plan=True)                 # user ticked "copy plan" -> fresh timestamp
    db.set_race_config_blob("course_json", '{"race":"MR"}', race_id=mr)
    db.init_db()
    assert db.get_race_config_blob("plan_csv", mr) == "OCC PLAN"
    assert db.get_race_config_blob("course_json", mr) == '{"race":"MR"}'
