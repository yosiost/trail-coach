"""Garmin Connect Personal API — official, per-user activity source.

Each self-hoster creates their own Personal API key in Garmin Connect Web
(Settings → Personal API Key) and sets GARMIN_PERSONAL_API_KEY. The key is
exchanged for a 24-hour bearer token (no refresh token; just re-exchange).

Token exchange follows Garmin's reference client
(garmin/connect-personal-api-python). Endpoints, query params and field names
follow Garmin's Personal API OpenAPI spec (SummarizedActivityDTO): distance and
elevationGain in meters, duration/movingDuration in seconds, averageHR in bpm,
startTimeLocal as "yyyy-MM-dd HH:mm:ss" local time. startDate/endDate are
inclusive local dates. Parsing is still defensive so a missing field degrades
to 0/None instead of failing the week view.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import date

BASE_URL = os.environ.get("GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
SEARCH_PATH = "/activitylist-service/activities/search/activities"
PAGE_SIZE = 99   # the spec's default page size; the maximum is undocumented
MAX_PAGES = 10   # hard stop: ~1,000 activities per window is far beyond one week

_token_cache: dict = {}


def garmin_personal_configured() -> bool:
    return bool(os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip())


def _jwt_exp(token: str) -> float:
    """Expiry (epoch seconds) from the JWT payload. Signature is not verified —
    this is only used to decide when to re-exchange. Falls back to 23h."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return time.time() + 23 * 3600


def _get_access_token() -> str:
    now = time.time()
    if _token_cache.get("access_token") and _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["access_token"]
    key = os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip()
    req = urllib.request.Request(
        BASE_URL + TOKEN_PATH,
        data=b"",
        method="POST",
        headers={"Accept": "*/*", "Authorization": f"ApiKey {key}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        token = resp.read().decode().strip()  # body is the bare JWT, not JSON
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = _jwt_exp(token)
    return token


def _get_json(path: str, params: dict) -> object:
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_get_access_token()}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _normalize(a: dict) -> dict:
    # Imported here: api.strava imports this module for source dispatch.
    from api.strava import hr_zone
    avg_hr = a.get("averageHR")
    atype = a.get("activityType") or {}
    return {
        "id":           a.get("activityId"),
        "date":         str(a.get("startTimeLocal") or "")[:10],
        "name":         a.get("activityName") or "",
        "type":         (atype.get("typeKey") if isinstance(atype, dict) else "") or "",
        "distance_km":  round((a.get("distance") or 0) / 1000, 1),
        "elev_gain_m":  int(a.get("elevationGain") or 0),
        "duration_min": round((a.get("movingDuration") or a.get("duration") or 0) / 60, 1),
        "avg_hr":       int(avg_hr) if avg_hr else None,
        "zone":         hr_zone(int(avg_hr)) if avg_hr else None,
    }


def fetch_activities(start: date, end: date) -> tuple[list[dict], str | None]:
    """Activities dated start..end (inclusive), normalized to the shape the
    plan/week engine expects. Same contract as strava.fetch_activities."""
    if not garmin_personal_configured():
        return [], None
    try:
        raw: list[dict] = []
        for page in range(MAX_PAGES):
            batch = _get_json(SEARCH_PATH, {
                "start": page * PAGE_SIZE, "limit": PAGE_SIZE,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),  # inclusive per the spec
            })
            if not isinstance(batch, list):
                raise ValueError(f"unexpected Garmin response: {type(batch).__name__}")
            raw.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        lo, hi = start.isoformat(), end.isoformat()
        result = [act for act in map(_normalize, raw) if lo <= act["date"] <= hi]
        logging.info("garmin_personal: %d activities for %s – %s", len(result), start, end)
        return result, None
    except Exception as e:
        err = str(e)
        logging.error("garmin_personal fetch_activities error: %s", err)
        if "401" in err:
            _token_cache.clear()
            return [], "Garmin rejected the Personal API key — check it hasn't expired"
        if "429" in err:
            return [], "Garmin rate limited — try again in a moment"
        return [], err
