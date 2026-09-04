"""Flask API server — runs on localhost:7432 (local) or $PORT (cloud)."""

import hmac
import logging
import os
from pathlib import Path
from functools import wraps
from flask import Flask, jsonify, request, session, redirect, url_for, Response, stream_with_context
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

load_dotenv(Path(__file__).parent / ".env")

from datetime import date, timedelta
from api.strava import (
    get_this_week, get_full_plan, fetch_activities, week_bounds, get_all_weeks,
    seed_plan_blob_from_file, plan_source,
)
from api.chat import (
    chat as ai_chat, chat_stream as ai_chat_stream, load_course,
    seed_course_blob_from_file, course_source,
)
from api import onboarding
from api import persona
from api import planner
from api.db import (
    init_db, init_athlete_references, get_coach_notes, get_athlete_references,
    list_sessions, get_session, upsert_session, delete_session,
    init_goals, get_active_goal, update_goal, get_predictions, hms_to_sec, sec_to_hms,
    sync_course_data, init_race_fuel, get_race_fuel,
    heal_onboarded_flag, is_onboarded,
)

app = Flask(__name__, static_folder="frontend", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB cap on uploads (plan CSV / course JSON)

# ── Secret key ───────────────────────────────────────────────────────────────
# Sessions are signed with this. Required in production; in debug we mint an
# ephemeral key so local dev works with zero setup (sessions reset on restart).
_DEBUG = os.environ.get("FLASK_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
_secret = os.environ.get("SECRET_KEY", "").strip()
if not _secret or _secret == "dev-secret-change-in-prod":
    if _DEBUG:
        _secret = os.urandom(32).hex()
        logging.warning("SECRET_KEY unset — using an ephemeral dev key (sessions reset on restart).")
    else:
        raise RuntimeError(
            "SECRET_KEY is required in production. Generate one with "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` and set it in the "
            "environment, or set FLASK_DEBUG=1 for local development."
        )
app.secret_key = _secret

init_db()  # always: create tables
# Seed demo data only when explicitly requested (SEED_DEMO_DATA=1). By default a
# fresh install starts empty and goes through onboarding instead of inheriting
# a set of default athlete/goal/course data.
if os.environ.get("SEED_DEMO_DATA", "").strip().lower() in ("1", "true", "yes", "on"):
    init_athlete_references()
    init_goals()
    init_race_fuel()
    sync_course_data()  # heal seeded DBs when course facts change
    onboarding.seed_demo_blobs()  # example plan + course so the demo isn't empty
seed_plan_blob_from_file()    # onboarding PR B: migrate the legacy plan CSV into the DB (once)
seed_course_blob_from_file()  # onboarding PR B: migrate the legacy course JSON into the DB (once)
heal_onboarded_flag()  # mark already-configured installs onboarded (skip the setup wizard)

# ── Auth ─────────────────────────────────────────────────────────────────────
# Default: a single-password gate — no external account, no cloud project.
#   AUTH_MODE=password  + APP_PASSWORD=<secret>
# Optional: Google OAuth for those who want it.
#   AUTH_MODE=oauth     + GOOGLE_CLIENT_ID/SECRET + ALLOWED_EMAILS=a@x.com,b@y.com
# Escape hatch: AUTH_MODE=none leaves the app open (local dev / public demo only).
# If AUTH_MODE is unset, we default to oauth when Google creds are present (so an
# existing OAuth deploy keeps working unchanged), otherwise to the password gate.
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

google = None
if AUTH_MODE == "oauth":
    oauth = OAuth(app)
    google = oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if AUTH_MODE == "none" or session.get("authed"):
            return f(*args, **kwargs)
        return redirect(url_for("login"))
    return decorated


import json
import threading
import time
from collections import defaultdict, deque

# ── Rate limiting for the LLM endpoints ──────────────────────────────────────
# Protects a public/demo instance from abusing the deployment's API key. Sliding
# 60s window, per client IP plus a global ceiling. Generous defaults so a single
# self-hoster never notices; set RATE_LIMIT_PER_MIN=0 to disable.
_RATE_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "20") or 0)
_RATE_GLOBAL_PER_MIN = int(os.environ.get("RATE_LIMIT_GLOBAL_PER_MIN", "120") or 0)
_rate_hits: dict = defaultdict(deque)
_rate_global: deque = deque()
_rate_lock = threading.Lock()


def _client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "unknown")


def rate_limited(f):
    """429 when a client (or the instance) exceeds the per-minute LLM-call budget."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if _RATE_PER_MIN <= 0 and _RATE_GLOBAL_PER_MIN <= 0:
            return f(*args, **kwargs)
        now = time.time()
        cutoff = now - 60
        ip = _client_ip()
        with _rate_lock:
            if _RATE_PER_MIN > 0:
                dq = _rate_hits[ip]
                while dq and dq[0] < cutoff:
                    dq.popleft()
                if len(dq) >= _RATE_PER_MIN:
                    return jsonify({"error": "Rate limit reached — wait a minute and try again."}), 429
            if _RATE_GLOBAL_PER_MIN > 0:
                while _rate_global and _rate_global[0] < cutoff:
                    _rate_global.popleft()
                if len(_rate_global) >= _RATE_GLOBAL_PER_MIN:
                    return jsonify({"error": "This instance is busy right now — try again shortly."}), 429
            if _RATE_PER_MIN > 0:
                _rate_hits[ip].append(now)
            if _RATE_GLOBAL_PER_MIN > 0:
                _rate_global.append(now)
        return f(*args, **kwargs)
    return decorated


# ── Secret redaction — belt-and-suspenders so no secret can leak in a JSON body
# (e.g. an unexpected provider exception echoed via {"error": str(e)}).
_SECRETS_CACHE = None


def _secrets() -> set:
    global _SECRETS_CACHE
    if _SECRETS_CACHE is None:
        keys = ("LLM_API_KEY", "ANTHROPIC_API_KEY", "APP_PASSWORD", "SECRET_KEY",
                "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN", "GOOGLE_CLIENT_SECRET",
                "DATABASE_URL")
        _SECRETS_CACHE = {v for v in (os.environ.get(k, "").strip() for k in keys) if len(v) >= 8}
    return _SECRETS_CACHE


@app.after_request
def _redact_secrets(resp):
    try:
        if resp.mimetype == "application/json" and not resp.direct_passthrough:
            body = resp.get_data(as_text=True)
            red = body
            for sec in _secrets():
                if sec in red:
                    red = red.replace(sec, "***REDACTED***")
            if red != body:
                resp.set_data(red)
    except Exception:
        pass
    return resp


_CACHE_FILE = Path(__file__).parent / "activity_cache.json"

def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}

def _save_cache(data: dict) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps(data))
    except Exception:
        pass

_activity_cache: dict = _load_cache()
STRAVA_WEBHOOK_VERIFY_TOKEN = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN", "")

_context_cache: dict = {"text": None, "expires_at": 0.0}
_context_lock = threading.Lock()
_CONTEXT_TTL = 60  # seconds


def _invalidate_context_cache() -> None:
    with _context_lock:
        _context_cache["expires_at"] = 0.0


def get_cached_context() -> str:
    with _context_lock:
        if time.monotonic() < _context_cache["expires_at"] and _context_cache["text"] is not None:
            return _context_cache["text"]
    text = build_context()
    with _context_lock:
        _context_cache["text"] = text
        _context_cache["expires_at"] = time.monotonic() + _CONTEXT_TTL
    return text


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


@app.get("/login")
def login():
    if AUTH_MODE == "none" or session.get("authed"):
        return redirect("/")
    if AUTH_MODE == "oauth":
        return google.authorize_redirect(url_for("auth_callback", _external=True))
    return _login_page()


@app.post("/login")
def login_post():
    if AUTH_MODE != "password":
        return redirect(url_for("login"))
    if not APP_PASSWORD:
        return _login_page("Auth is not configured — set APP_PASSWORD in the environment."), 500
    if hmac.compare_digest(request.form.get("password", "").encode(), APP_PASSWORD.encode()):
        session["authed"] = True
        return redirect("/")
    return _login_page("Incorrect password."), 401


@app.get("/auth/callback")
def auth_callback():
    if AUTH_MODE != "oauth":
        return redirect(url_for("login"))
    token = google.authorize_access_token()
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


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    return app.send_static_file("index.html")


@app.get("/api/strava/webhook")
def strava_webhook_verify():
    mode      = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")
    token     = request.args.get("hub.verify_token")
    if mode == "subscribe" and token == STRAVA_WEBHOOK_VERIFY_TOKEN:
        return jsonify({"hub.challenge": challenge})
    return jsonify({"error": "forbidden"}), 403


@app.post("/api/strava/webhook")
def strava_webhook_event():
    event = request.json or {}
    obj_type   = event.get("object_type")
    aspect     = event.get("aspect_type")
    logging.info("Strava webhook received: object_type=%s aspect_type=%s", obj_type, aspect)
    if obj_type == "activity" and aspect in ("create", "update"):
        try:
            start, end = week_bounds(False)
            logging.info("Webhook: fetching this-week activities %s – %s", start, end)
            this_week, err = fetch_activities(start, end)
            logging.info("Webhook: this_week fetched %d activities, error=%s", len(this_week), err)
            _activity_cache["this_week"] = this_week
            _activity_cache["refreshed_at"] = date.today().isoformat()
            _save_cache(_activity_cache)
            _invalidate_context_cache()
            logging.info("Webhook: cache saved OK")
        except Exception as e:
            logging.exception("Webhook: cache update failed: %s", e)
    return jsonify({"ok": True})


@app.get("/api/weeks")
@login_required
def weeks_list():
    try:
        return jsonify(get_all_weeks())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _refresh_cache_now() -> str | None:
    """Fetch fresh activities from Strava, update cache, return error string or None."""
    try:
        start, end = week_bounds(False)
        logging.info("Force-refresh: fetching this-week activities %s – %s", start, end)
        this_week, err = fetch_activities(start, end)
        logging.info("Force-refresh: fetched %d activities, error=%s", len(this_week), err)
        _activity_cache["this_week"] = this_week
        _activity_cache["refreshed_at"] = date.today().isoformat()
        _save_cache(_activity_cache)
        return err
    except Exception as e:
        logging.exception("Force-refresh failed: %s", e)
        return str(e)


@app.get("/api/week")
@login_required
def week():
    start_str = request.args.get("start")
    force     = request.args.get("force") == "1"
    if start_str:
        try:
            from datetime import date as date_cls
            week_start = date_cls.fromisoformat(start_str)
        except ValueError:
            return jsonify({"error": "invalid date"}), 400
        current_start, _ = week_bounds(False)
        is_current = week_start == current_start
        if force and is_current:
            logging.info("Force-refresh requested for current week")
            _refresh_cache_now()
        cached = _activity_cache.get("this_week") if is_current else None
        logging.info("week endpoint: start=%s force=%s is_current=%s cache_hit=%s cache_size=%s",
                     start_str, force, is_current, cached is not None, len(cached) if cached else 0)
        try:
            return jsonify(get_this_week(injected_activities=cached, week_start=week_start))
        except Exception as e:
            logging.exception("get_this_week failed: %s", e)
            return jsonify({"error": str(e)}), 500
    else:
        # Legacy: ?next=1 still works
        next_week_flag = request.args.get("next") == "1"
        key = "next_week" if next_week_flag else "this_week"
        if force and not next_week_flag:
            _refresh_cache_now()
        cached_activities = _activity_cache.get(key)  # always use cache after refresh
        logging.info("week endpoint (legacy): next=%s force=%s cache_size=%s",
                     next_week_flag, force, len(cached_activities) if cached_activities else 0)
        try:
            return jsonify(get_this_week(next_week_flag, injected_activities=cached_activities))
        except Exception as e:
            logging.exception("get_this_week (legacy) failed: %s", e)
            return jsonify({"error": str(e)}), 500


@app.get("/api/plan")
@login_required
def plan():
    try:
        return jsonify(get_full_plan())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _format_dur(dur_min: int) -> str:
    h, m = divmod(int(dur_min), 60)
    return f"~{h}h{m:02d}m" if h else f"~{m}m"


def _serialize_fuel(s: dict) -> dict:
    hrs = s["dur_min"] / 60
    return {
        "seg":   s["seg"],
        "dur":   _format_dur(s["dur_min"]),
        "food":  s["food"],
        "carbs": s["carbs"],
        "rate":  round(s["carbs"] / hrs) if hrs else 0,
        "crux":  s["crux"],
        "flag":  s["flag"],
    }


@app.get("/api/race/fuel")
@login_required
def race_fuel():
    try:
        segments = [_serialize_fuel(s) for s in get_race_fuel()]
        total = sum(s["carbs"] for s in segments)
        return jsonify({"segments": segments, "total_carbs": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/references")
@login_required
def references():
    """The athlete knowledge base (optionally by category) — powers the Locations view."""
    cat = request.args.get("category")
    try:
        refs = get_athlete_references(cat) if cat else get_athlete_references()
        return jsonify([{"category": r["category"], "name": r["name"], "content": r["content"]}
                        for r in refs if r["category"] != "_meta"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/course")
@login_required
def course():
    """Serve the athlete's course (DB blob, else legacy file). 404 when unset."""
    data = load_course()
    if not data:
        return jsonify({"error": "no course configured"}), 404
    return jsonify(data)


@app.get("/api/config/status")
@login_required
def config_status():
    """First-run detection for the frontend: is this instance configured yet?"""
    try:
        return jsonify({
            "onboarded":     is_onboarded(),
            "has_goal":      get_active_goal() is not None,
            "has_plan":      len(get_all_weeks()) > 0,
            "has_course":    load_course() is not None,
            "plan_source":   plan_source(),
            "course_source": course_source(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Onboarding (first-run setup wizard) ──────────────────────────────────────

@app.get("/api/onboarding/examples")
@login_required
def onboarding_examples():
    return jsonify(onboarding.list_examples())


@app.post("/api/onboarding/profile")
@login_required
def onboarding_profile():
    b = request.json or {}
    try:
        onboarding.persist_profile(
            name=b.get("name", ""), weight_kg=b.get("weight_kg"),
            height_cm=b.get("height_cm"), max_hr=b.get("max_hr"),
            background=b.get("background", ""), units=b.get("units", "km"),
            week_start=b.get("week_start", "monday"),
        )
        _invalidate_context_cache()
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/onboarding/goal")
@login_required
def onboarding_goal():
    b = request.json or {}
    try:
        g = onboarding.persist_goal(
            race_name=b.get("race_name", ""), race_date=b.get("race_date", ""),
            distance_km=b.get("distance_km", 0), vert_m=b.get("vert_m", 0),
            aspirational_time=b.get("aspirational_time", "0:00"),
            realistic_min_time=b.get("realistic_min_time", "0:00"),
            realistic_max_time=b.get("realistic_max_time", "0:00"),
            notes=b.get("notes", ""),
        )
        _invalidate_context_cache()
        return jsonify({"ok": True, "goal": _serialize_goal(g) if g else None})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/onboarding/plan")
@login_required
def onboarding_plan():
    try:
        if "file" in request.files:
            text = request.files["file"].read().decode("utf-8")
        else:
            b = request.json or {}
            if b.get("skip"):
                return jsonify({"ok": True, "skipped": True})
            text = onboarding.load_example_plan(b["example"]) if b.get("example") else b.get("csv", "")
        n = onboarding.persist_plan(text)
        _invalidate_context_cache()
        return jsonify({"ok": True, "sessions": n})
    except (ValueError, UnicodeDecodeError) as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/onboarding/course")
@login_required
def onboarding_course():
    try:
        b = request.json or {}
        if b.get("skip"):
            return jsonify({"ok": True, "skipped": True})
        if b.get("example"):
            course = onboarding.load_example_course(b["example"])
        elif b.get("minimal"):
            m = b["minimal"]
            course = onboarding.minimal_course(
                m.get("race", ""), m.get("distance_km", 0), m.get("vert_m", 0), m.get("date", ""),
            )
        else:
            course = b.get("course")
        onboarding.persist_course(course)
        _invalidate_context_cache()
        return jsonify({"ok": True})
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/persona/config")
@login_required
def persona_config_get():
    return jsonify(persona.get_persona_config())


@app.post("/api/persona/config")
@login_required
def persona_config_set():
    b = request.json or {}
    try:
        persona.set_persona_config(b.get("persona", ""), b.get("mode", "generic"), b.get("text", ""))
        _invalidate_context_cache()
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/plan/generate")
@login_required
@rate_limited
def plan_generate():
    """Generate a training plan from the active goal via the configured LLM."""
    try:
        summary = planner.generate_plan()
        _invalidate_context_cache()
        return jsonify({"ok": True, **summary})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Plan generation failed: {e}"}), 502


@app.post("/api/persona/generate")
@login_required
@rate_limited
def persona_generate():
    """Draft a persona methodology from a description via the configured LLM."""
    b = request.json or {}
    try:
        text = persona.generate_methodology(b.get("persona", ""), b.get("description", ""))
        return jsonify({"ok": True, "methodology": text})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Generation failed: {e}"}), 502


@app.post("/api/onboarding/complete")
@login_required
def onboarding_complete():
    b = request.json or {}
    onboarding.complete(
        activity_source=b.get("activity_source", "manual"),
        methodology=b.get("methodology", "generic"),
        methodology_text=b.get("methodology_text", ""),
    )
    _invalidate_context_cache()
    return jsonify({"ok": True, "onboarded": True})


def _serialize_goal(g: dict) -> dict:
    days_to_race = (date.fromisoformat(g["race_date"]) - date.today()).days
    return {
        **g,
        "aspirational_time_hms": sec_to_hms(g["aspirational_time_sec"]),
        "realistic_min_hms":     sec_to_hms(g["realistic_min_sec"]),
        "realistic_max_hms":     sec_to_hms(g["realistic_max_sec"]),
        "days_to_race": days_to_race,
    }


def _serialize_prediction(p: dict) -> dict:
    return {
        **p,
        "predicted_time_hms":  sec_to_hms(p["predicted_time_sec"]),
        "confidence_low_hms":  sec_to_hms(p["confidence_low_sec"]),
        "confidence_high_hms": sec_to_hms(p["confidence_high_sec"]),
    }


@app.get("/api/goal")
@login_required
def goal_get():
    g = get_active_goal()
    if not g:
        return jsonify({"error": "no active goal"}), 404
    return jsonify(_serialize_goal(g))


@app.put("/api/goal")
@login_required
def goal_update():
    body = request.json or {}
    g = get_active_goal()
    if not g:
        return jsonify({"error": "no active goal"}), 404
    try:
        fields: dict = {}
        if "aspirational_time" in body:
            fields["aspirational_time_sec"] = hms_to_sec(body["aspirational_time"])
        if "realistic_min_time" in body:
            fields["realistic_min_sec"] = hms_to_sec(body["realistic_min_time"])
        if "realistic_max_time" in body:
            fields["realistic_max_sec"] = hms_to_sec(body["realistic_max_time"])
        if "notes" in body:
            fields["notes"] = body["notes"]
        if body.get("race_name", "").strip():
            fields["race_name"] = body["race_name"].strip()
        if "race_date" in body and body["race_date"]:
            date.fromisoformat(body["race_date"])  # validate YYYY-MM-DD
            fields["race_date"] = body["race_date"]
        if body.get("distance_km") not in (None, ""):
            fields["distance_km"] = float(body["distance_km"])
        if body.get("vert_m") not in (None, ""):
            fields["vert_m"] = int(body["vert_m"])
        updated = update_goal(g["id"], **fields)
        _invalidate_context_cache()
        return jsonify(_serialize_goal(updated))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/goal/predictions")
@login_required
def goal_predictions():
    g = get_active_goal()
    if not g:
        return jsonify([])
    limit = int(request.args.get("limit", 50))
    try:
        return jsonify([_serialize_prediction(p) for p in get_predictions(g["id"], limit=limit)])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def build_context() -> str:
    cached_activities = _activity_cache.get("this_week")
    lines = [f"Today: {date.today().isoformat()}"]
    if _activity_cache.get("refreshed_at"):
        lines.append(f"Activity data last refreshed: {_activity_cache['refreshed_at']}")

    try:
        goal = get_active_goal()
        if goal:
            days_left = (date.fromisoformat(goal["race_date"]) - date.today()).days
            lines.append(
                f"Current goal: {goal['race_name']} — {goal['race_date']} ({days_left} days away) · "
                f"{goal['distance_km']}km/{goal['vert_m']}m↑ · "
                f"Aspirational: {sec_to_hms(goal['aspirational_time_sec'])} · "
                f"Realistic band: {sec_to_hms(goal['realistic_min_sec'])}–{sec_to_hms(goal['realistic_max_sec'])}"
            )
    except Exception as e:
        lines.append(f"(could not load goal: {e})")

    try:
        week = get_this_week(injected_activities=cached_activities)
        lines.append(f"Current week: {week['week_num']} · {week['phase']} · {week['week_label']}")
        s = week["summary"]
        lines.append(f"Week progress: {s['done_km']}/{s['plan_km']} km, {s['done_vert']}/{s['plan_vert']}m↑ ({s['pct']}%)")
        lines.append("")
        lines.append("Sessions this week (use strava_id with get_activity_laps for lap detail):")
        for r in week["rows"]:
            status = r["status"]
            planned = f"{r['planned_km']}km {r['planned_vert']}m↑ {r['planned_zone']}"
            actuals_str = ""
            for a in r.get("actuals", [r["actual"]] if r.get("actual") else []):
                sid = f" [strava_id:{a['id']}]" if a.get("id") else ""
                actuals_str += f" → {a['distance_km']}km {a['elev_gain_m']}m↑ {a['duration_min']}min avg {a['avg_hr']}bpm ({a['zone']}){sid}"
            lines.append(f"  {r['day']} [{status.upper()}] {r['session']} | planned: {planned}{actuals_str}")
    except Exception as e:
        lines.append(f"(could not load week data: {e})")

    try:
        all_weeks = get_all_weeks()
        full_plan = get_full_plan()
        by_week: dict[str, dict] = {}
        for row in full_plan:
            w = row["week"]
            if w not in by_week:
                by_week[w] = {"phase": row["phase"], "km": 0.0, "vert": 0}
            try:
                by_week[w]["km"] += float(row["km"]) if row["km"] else 0
                by_week[w]["vert"] += int(row["vert"]) if row["vert"] else 0
            except (ValueError, TypeError):
                pass
        lines.append("")
        lines.append("Full plan weekly volume (use week_start with get_week_data for session detail):")
        for wk in all_weeks:
            w = wk["week_num"]
            info = by_week.get(w, {})
            lines.append(
                f"  {w} (start:{wk['start']}) {info.get('phase', wk['phase'])}: "
                f"{info.get('km', 0):.0f}km / {info.get('vert', 0)}m↑"
            )
    except Exception as e:
        lines.append(f"(could not load plan overview: {e})")

    try:
        notes = get_coach_notes(limit=8)
        if notes:
            lines.append("")
            lines.append("Persisted coach observations (most recent first):")
            for n in notes:
                lines.append(f"  [{n['created_at'][:10]}] {n['content']}")
    except Exception as e:
        lines.append(f"(could not load coach notes: {e})")

    try:
        refs = get_athlete_references()
        if refs:
            by_cat: dict[str, list] = {}
            for r in refs:
                if r["category"] == "_meta":
                    continue
                by_cat.setdefault(r["category"], []).append(r)
            lines.append("")
            lines.append("Athlete knowledge base (always authoritative — no need to re-ask athlete):")
            for cat, items in by_cat.items():
                lines.append(f"  [{cat.upper()}]")
                for item in items:
                    lines.append(f"    {item['name']}: {item['content']}")
    except Exception as e:
        lines.append(f"(could not load athlete references: {e})")

    return "\n".join(lines)


@app.post("/api/chat")
@login_required
@rate_limited
def chat():
    body     = request.json or {}
    messages = body.get("messages", [])
    persona  = body.get("persona", "coach")
    if not messages or messages[-1].get("role") != "user":
        return jsonify({"error": "no user message"}), 400
    try:
        context = get_cached_context()
        result  = ai_chat(messages, context=context, persona=persona)
        if result["plan_updated"] or result.get("goal_updated"):
            _invalidate_context_cache()
        return jsonify({
            "reply": result["reply"],
            "plan_updated": result["plan_updated"],
            "goal_updated": result.get("goal_updated", False),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/chat/stream")
@login_required
@rate_limited
def chat_stream():
    body     = request.json or {}
    messages = body.get("messages", [])
    persona  = body.get("persona", "coach")
    if not messages or messages[-1].get("role") != "user":
        return jsonify({"error": "no user message"}), 400
    try:
        context = get_cached_context()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def generate():
        try:
            yield from ai_chat_stream(
                messages, context=context, persona=persona,
                on_plan_updated=_invalidate_context_cache,
                on_goal_updated=_invalidate_context_cache,
            )
        except Exception as e:
            import json
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/sessions")
@login_required
def sessions_list():
    try:
        return jsonify(list_sessions())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/sessions/<session_id>")
@login_required
def session_get(session_id):
    s = get_session(session_id)
    if s is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(s)


@app.post("/api/sessions/<session_id>")
@login_required
def session_upsert(session_id):
    body = request.json or {}
    try:
        upsert_session(
            session_id=session_id,
            title=body.get("title", ""),
            messages=body.get("messages", []),
            created_at=body.get("created_at", ""),
            updated_at=body.get("updated_at", ""),
            persona=body.get("persona", "coach"),
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.delete("/api/sessions/<session_id>")
@login_required
def session_delete(session_id):
    try:
        delete_session(session_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run(port: int = 7432) -> None:
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7432))
    run(port=port)
