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
