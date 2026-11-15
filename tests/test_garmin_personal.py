"""Garmin Personal API source: token exchange, paging, normalization, dispatch.
Network is faked by patching urllib.request.urlopen."""
import base64
import json
from datetime import date
from urllib.parse import urlparse, parse_qs

import pytest

from api import garmin_personal, strava


def _jwt(exp: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class _Resp:
    def __init__(self, body: bytes): self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _act(i, day, **kw):
    a = {"activityId": i, "activityName": f"Run {i}", "startTimeLocal": f"{day} 07:00:00",
         "activityType": {"typeKey": "trail_running"}, "distance": 12345.0,
         "duration": 4000.0, "movingDuration": 3600.0, "elevationGain": 512.4, "averageHR": 140.0}
    a.update(kw)
    return a


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "k123")
    garmin_personal._token_cache.clear()
    calls = []
    pages: list[list[dict]] = []

    def urlopen(req, timeout=None):
        calls.append(req)
        if req.full_url.endswith(garmin_personal.TOKEN_PATH):
            return _Resp(_jwt(9_999_999_999).encode())
        return _Resp(json.dumps(pages.pop(0) if pages else []).encode())

    monkeypatch.setattr(garmin_personal.urllib.request, "urlopen", urlopen)
    return calls, pages


def test_unconfigured_is_silent(monkeypatch):
    monkeypatch.delenv("GARMIN_PERSONAL_API_KEY", raising=False)
    assert garmin_personal.fetch_activities(date(2026, 1, 1), date(2026, 1, 7)) == ([], None)
    assert strava.activity_source() == "none"


def test_token_exchange_and_normalized_activity(fake):
    calls, pages = fake
    pages.append([_act(1, "2026-01-03")])
    acts, err = garmin_personal.fetch_activities(date(2026, 1, 1), date(2026, 1, 7))
    assert err is None
    token_req, search_req = calls
    assert token_req.get_method() == "POST"
    assert token_req.get_header("Authorization") == "ApiKey k123"
    assert search_req.get_header("Authorization").startswith("Bearer h.")
    q = parse_qs(urlparse(search_req.full_url).query)
    assert q["startDate"] == ["2026-01-01"] and q["endDate"] == ["2026-01-08"]
    assert acts == [{
        "id": 1, "date": "2026-01-03", "name": "Run 1", "type": "trail_running",
        "distance_km": 12.3, "elev_gain_m": 512, "duration_min": 60.0,
        "avg_hr": 140, "zone": strava.hr_zone(140),
    }]


def test_token_is_cached_and_pages_until_short_batch(fake, monkeypatch):
    calls, pages = fake
    monkeypatch.setattr(garmin_personal, "PAGE_SIZE", 2)
    pages += [[_act(1, "2026-01-02"), _act(2, "2026-01-03")], [_act(3, "2026-01-04")]]
    acts, _ = garmin_personal.fetch_activities(date(2026, 1, 1), date(2026, 1, 7))
    assert [a["id"] for a in acts] == [1, 2, 3]
    assert sum(c.full_url.endswith(garmin_personal.TOKEN_PATH) for c in calls) == 1


def test_filters_out_of_window_and_tolerates_missing_fields(fake):
    _, pages = fake
    pages.append([_act(1, "2026-01-08"), {"activityId": 2, "startTimeLocal": "2026-01-07 06:00:00"}])
    acts, err = garmin_personal.fetch_activities(date(2026, 1, 1), date(2026, 1, 7))
    assert err is None and [a["id"] for a in acts] == [2]
    assert acts[0]["distance_km"] == 0 and acts[0]["avg_hr"] is None and acts[0]["type"] == ""


def test_dispatch_prefers_garmin_and_laps_degrade(fake):
    _, pages = fake
    pages.append([_act(7, "2026-01-02")])
    assert strava.activity_source() == "garmin"
    acts, _ = strava.fetch_activities(date(2026, 1, 1), date(2026, 1, 7))
    assert acts[0]["id"] == 7
    assert "error" in strava.fetch_activity_laps(7)[0]


def test_401_reports_key_problem(monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "bad")
    garmin_personal._token_cache.clear()
    def urlopen(req, timeout=None):
        raise OSError("HTTP Error 401: Unauthorized")
    monkeypatch.setattr(garmin_personal.urllib.request, "urlopen", urlopen)
    acts, err = garmin_personal.fetch_activities(date(2026, 1, 1), date(2026, 1, 7))
    assert acts == [] and "Personal API key" in err
