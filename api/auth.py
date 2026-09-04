"""Authentication — a single-password gate (default) or Google OAuth.

Default: a shared password (no external account, no cloud project).
  AUTH_MODE=password  + APP_PASSWORD=<secret>
Optional: Google OAuth restricted to an allowlist.
  AUTH_MODE=oauth     + GOOGLE_CLIENT_ID/SECRET + ALLOWED_EMAILS=a@x.com,b@y.com
Escape hatch: AUTH_MODE=none leaves the app open (local dev / public demo only).
If AUTH_MODE is unset, defaults to oauth when GOOGLE_CLIENT_ID is present, else
password (so an existing OAuth deploy keeps working unchanged).

`init_auth(app)` wires the routes and OAuth onto the Flask app.
"""
from __future__ import annotations

import hmac
import logging
import os
from functools import wraps

from flask import Blueprint, redirect, request, session, url_for

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
# Comma-separated allowlist for OAuth mode (back-compat with singular ALLOWED_EMAIL).
ALLOWED_EMAILS = {
    e.strip().lower()
    for e in (os.environ.get("ALLOWED_EMAILS", "") + "," + os.environ.get("ALLOWED_EMAIL", "")).split(",")
    if e.strip()
}

AUTH_MODE = os.environ.get("AUTH_MODE", "").strip().lower()
if not AUTH_MODE:
    AUTH_MODE = "oauth" if GOOGLE_CLIENT_ID else "password"
if AUTH_MODE not in ("password", "oauth", "none"):
    raise RuntimeError(f"AUTH_MODE must be password | oauth | none (got {AUTH_MODE!r}).")
if AUTH_MODE == "none":
    logging.warning("AUTH_MODE=none — the app is UNAUTHENTICATED. Use only for local dev or a public demo.")

auth_bp = Blueprint("auth", __name__)
_google = None  # set by init_auth() in oauth mode


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if AUTH_MODE == "none" or session.get("authed"):
            return f(*args, **kwargs)
        return redirect(url_for("auth.login"))
    return decorated


def _login_page(error: str = "") -> str:
    """Minimal, self-contained password-login page (matches the app's dark theme)."""
    err_html = f'<p class="err">{error}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trail Coach — Sign in</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: #16181c; color: #e8e8e8;
    font: 15px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
  form {{ width: 320px; max-width: 90vw; padding: 32px 28px; background: #1f2227;
    border: 1px solid #2c2f36; border-radius: 14px; box-shadow: 0 12px 40px rgba(0,0,0,.4); }}
  h1 {{ margin: 0 0 4px; font-size: 20px; }}
  .sub {{ margin: 0 0 22px; color: #9aa0aa; font-size: 13px; }}
  input {{ width: 100%; padding: 11px 13px; margin-bottom: 14px; font-size: 15px;
    background: #16181c; color: #e8e8e8; border: 1px solid #3a3e46; border-radius: 9px; }}
  input:focus {{ outline: none; border-color: #ff7a00; }}
  button {{ width: 100%; padding: 11px; font-size: 15px; font-weight: 600; cursor: pointer;
    background: #ff7a00; color: #16181c; border: none; border-radius: 9px; }}
  button:hover {{ background: #ff9433; }}
  .err {{ margin: 0 0 14px; padding: 9px 11px; font-size: 13px;
    background: #3a1d1d; color: #ff9a9a; border: 1px solid #5c2a2a; border-radius: 8px; }}
</style></head>
<body>
  <form method="post" action="/login">
    <h1>🏔️ Trail Coach</h1>
    <p class="sub">Enter the app password to continue.</p>
    {err_html}
    <input type="password" name="password" placeholder="Password" autofocus required>
    <button type="submit">Sign in</button>
  </form>
</body></html>"""


@auth_bp.get("/login")
def login():
    if AUTH_MODE == "none" or session.get("authed"):
        return redirect("/")
    if AUTH_MODE == "oauth":
        return _google.authorize_redirect(url_for("auth.auth_callback", _external=True))
    return _login_page()


@auth_bp.post("/login")
def login_post():
    if AUTH_MODE != "password":
        return redirect(url_for("auth.login"))
    if not APP_PASSWORD:
        return _login_page("Auth is not configured — set APP_PASSWORD in the environment."), 500
    if hmac.compare_digest(request.form.get("password", "").encode(), APP_PASSWORD.encode()):
        session["authed"] = True
        return redirect("/")
    return _login_page("Incorrect password."), 401


@auth_bp.get("/auth/callback")
def auth_callback():
    if AUTH_MODE != "oauth":
        return redirect(url_for("auth.login"))
    token = _google.authorize_access_token()
    user  = token.get("userinfo", {})
    email = (user.get("email") or "").lower()
    if not ALLOWED_EMAILS:
        return "<h2>Access denied.</h2><p>No ALLOWED_EMAILS configured for OAuth mode.</p>", 403
    if email not in ALLOWED_EMAILS:
        return f"<h2>Access denied.</h2><p>{email} is not authorised.</p>", 403
    session["authed"]     = True
    session["user_email"] = email
    session["user_name"]  = user.get("name", email)
    return redirect("/")


@auth_bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


def init_auth(app):
    """Register OAuth (when used) and the auth routes onto the Flask app."""
    global _google
    if AUTH_MODE == "oauth":
        from authlib.integrations.flask_client import OAuth
        oauth = OAuth(app)
        _google = oauth.register(
            name="google",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    app.register_blueprint(auth_bp)
