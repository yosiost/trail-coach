"""Request-side protections for a public/demo instance:

- a lightweight in-memory rate limiter for the LLM endpoints (per client IP +
  a global ceiling), so nobody can hammer the deployment's API key;
- redaction of any secret from JSON response bodies, belt-and-suspenders against
  a secret ever leaking via an error message.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import jsonify, request

# ── Rate limiting ─────────────────────────────────────────────────────────────
# Generous defaults so a single self-hoster never notices; set
# RATE_LIMIT_PER_MIN=0 to disable. Tighten both for a public demo.
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


# ── Secret redaction ──────────────────────────────────────────────────────────
_SECRETS_CACHE = None


def _secrets() -> set:
    global _SECRETS_CACHE
    if _SECRETS_CACHE is None:
        keys = ("LLM_API_KEY", "ANTHROPIC_API_KEY", "APP_PASSWORD", "SECRET_KEY",
                "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN", "GOOGLE_CLIENT_SECRET",
                "DATABASE_URL")
        _SECRETS_CACHE = {v for v in (os.environ.get(k, "").strip() for k in keys) if len(v) >= 8}
    return _SECRETS_CACHE


def install_security(app):
    """Register the response-body secret redaction on the app."""
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
    return app
