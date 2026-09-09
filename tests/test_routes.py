"""Route-level tests via Flask's test client — the safety net for auth refactors."""
import pytest

pytest.importorskip("flask")


@pytest.fixture
def client():
    import server
    server.app.config.update(TESTING=True)
    return server.app.test_client()


def test_auth_gate_redirects_when_unauthed(client):
    r = client.get("/api/config/status")
    assert r.status_code == 302                      # -> login


def test_login_page_renders_password_form(client):
    r = client.get("/login")
    assert r.status_code == 200 and b'name="password"' in r.data


def test_wrong_password_rejected(client):
    assert client.post("/login", data={"password": "nope"}).status_code == 401


def test_login_then_authed_api_call(client):
    assert client.post("/login", data={"password": "testpw"}).status_code == 302
    r = client.get("/api/config/status")             # cookie carried by the client
    assert r.status_code == 200 and "onboarded" in r.get_json()


def test_logout_clears_session(client):
    client.post("/login", data={"password": "testpw"})
    assert client.get("/logout").status_code == 302
    assert client.get("/api/config/status").status_code == 302   # gated again


def test_build_context_caps_athlete_references(client):
    import server
    from api import db
    for i in range(30):
        db.upsert_athlete_reference("fueling", f"item_{i:02d}", "content")
    ctx = server.build_context()
    ref_lines = [ln for ln in ctx.splitlines() if ln.strip().startswith("item_")]
    assert len(ref_lines) == server._ATHLETE_REFS_CONTEXT_LIMIT


# ── Multi-race endpoints ──────────────────────────────────────────────────────

def _login(client):
    client.post("/login", data={"password": "testpw"})


def test_races_list_switch_and_create(client):
    from api import db
    _login(client)
    a = db.create_goal("Race A", "2099-01-01", 50, 2400, 1, 2, 3)

    # List shows the one race, flagged active.
    races = client.get("/api/races").get_json()
    assert [r["race_name"] for r in races] == ["Race A"] and races[0]["is_active"]

    # Create a second race with copy-forward of the fuel plan from Race A.
    db.set_race_fuel([{"seg": "S", "dur_min": 60, "food": "gels", "carbs": 60}], race_id=a["id"])
    r = client.post("/api/races", json={
        "race_name": "Race B", "race_date": "2099-06-01", "distance_km": 100, "vert_m": 5000,
        "aspirational_time": "14:00:00", "realistic_min_time": "13:30:00", "realistic_max_time": "15:00:00",
        "copy": {"fuel": True},
    })
    assert r.status_code == 200
    b_id = r.get_json()["goal"]["id"]
    assert db.get_active_race_id() == b_id                 # new race is active
    assert len(db.get_race_fuel(b_id)) == 1                # fuel copied forward
    assert db.get_goal_by_id(a["id"])["status"] == "active"  # Race A preserved

    # Switch back to Race A.
    assert client.post(f"/api/races/{a['id']}/activate").status_code == 200
    assert db.get_active_race_id() == a["id"]
    assert client.get("/api/goal").get_json()["race_name"] == "Race A"


def test_activate_unknown_race_404s(client):
    _login(client)
    assert client.post("/api/races/99999/activate").status_code == 404
