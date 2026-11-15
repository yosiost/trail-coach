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
    assert q["startDate"] == ["2026-01-01"] and q["endDate"] == ["2026-01-07"]
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


# ── Server-side polling (Garmin has no webhook) ─────────────────────────────

@pytest.fixture
def srv(monkeypatch):
    import server
    calls = []
    def fake_fetch(start, end):
        calls.append((start, end))
        return [{"id": len(calls)}], None
    monkeypatch.setattr(server, "fetch_activities", fake_fetch)
    monkeypatch.setattr(server, "_save_cache", lambda d: None)
    monkeypatch.setattr(server, "_activity_cache", {})
    monkeypatch.setattr(server, "activity_source", lambda: "garmin")
    return server, calls


def test_poll_refreshes_when_empty_then_respects_ttl(srv):
    server, calls = srv
    server._refresh_if_stale()
    server._refresh_if_stale()
    assert len(calls) == 1 and server._activity_cache["this_week"] == [{"id": 1}]


def test_poll_refreshes_after_ttl_or_week_rollover(srv):
    server, calls = srv
    server._refresh_if_stale()
    server._activity_cache["refreshed_ts"] -= server.ACTIVITY_POLL_SECONDS + 1
    server._refresh_if_stale()
    assert len(calls) == 2
    server._activity_cache["week_start"] = "2000-01-02"   # cache from an old week
    server._refresh_if_stale()
    assert len(calls) == 3


def test_poll_skipped_for_strava_webhook_source(srv, monkeypatch):
    server, calls = srv
    monkeypatch.setattr(server, "activity_source", lambda: "strava")
    server._refresh_if_stale()
    assert calls == []


def test_failed_refresh_keeps_previous_activities(srv, monkeypatch):
    server, calls = srv
    server._refresh_if_stale()
    monkeypatch.setattr(server, "fetch_activities", lambda s, e: ([], "Garmin rate limited"))
    assert server._refresh_cache_now() == "Garmin rate limited"
    assert server._activity_cache["this_week"] == [{"id": 1}]


# ── Activity breakdown (split summaries stand in for laps) ───────────────────

def _breakdown_fake(monkeypatch, detail, listed):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "k123")
    garmin_personal._token_cache.clear()
    urls = []
    def urlopen(req, timeout=None):
        urls.append(req.full_url)
        if req.full_url.endswith(garmin_personal.TOKEN_PATH):
            return _Resp(_jwt(9_999_999_999).encode())
        if garmin_personal.SEARCH_PATH in req.full_url:
            return _Resp(json.dumps(listed).encode())
        return _Resp(json.dumps(detail).encode())
    monkeypatch.setattr(garmin_personal.urllib.request, "urlopen", urlopen)
    return urls


_DETAIL = {
    "activityId": 42,
    "summaryDTO": {"startTimeLocal": "2026-01-03T07:00:00.0"},
    "splitSummaries": [
        {"splitType": "INTERVAL_REST", "noOfSplits": 6, "distance": 1200, "duration": 720,
         "averageHR": 128, "maxHR": 140},
        {"splitType": "INTERVAL_ACTIVE", "noOfSplits": 6, "distance": 6000, "duration": 1500,
         "movingDuration": 1490, "elevationGain": 40, "elevationLoss": 38, "averageHR": 162, "maxHR": 175},
        {"splitType": "TIMING_GATE", "noOfSplits": 1, "distance": 0, "duration": 5},
    ],
}


def test_breakdown_orders_splits_and_adds_zone_times(monkeypatch):
    listed = [_act(41, "2026-01-03"), _act(42, "2026-01-03", lapCount=13,
              hrTimeInZone_1=60, hrTimeInZone_2=600, hrTimeInZone_3=300,
              hrTimeInZone_4=900, hrTimeInZone_5=120)]
    urls = _breakdown_fake(monkeypatch, _DETAIL, listed)
    b = garmin_personal.fetch_activity_breakdown(42)
    assert urls[1].endswith("/activity-service/activity/42")          # no stray "?"
    q = parse_qs(urlparse(urls[2]).query)
    assert q["startDate"] == q["endDate"] == ["2026-01-03"]
    assert [s["type"] for s in b["splits"]] == ["INTERVAL_ACTIVE", "INTERVAL_REST", "TIMING_GATE"]
    work = b["splits"][0]
    assert (work["label"], work["count"], work["distance_km"], work["duration_min"]) == ("work intervals", 6, 6.0, 24.8)
    assert work["pace_min_km"] == 4.14 and work["avg_hr"] == 162 and work["elev_loss_m"] == 38
    assert b["splits"][2]["pace_min_km"] is None and b["splits"][2]["label"] == "timing gate"
    assert b["hr_zone_min"] == [1.0, 10.0, 5.0, 15.0, 2.0] and b["lap_count"] == 13


def test_breakdown_without_list_match_or_splits(monkeypatch):
    _breakdown_fake(monkeypatch, {"summaryDTO": {"startTimeLocal": "2026-01-03T07:00:00"}}, [])
    assert garmin_personal.fetch_activity_breakdown(42) == {"splits": [], "hr_zone_min": None, "lap_count": None}


def test_breakdown_404(monkeypatch):
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "k")
    garmin_personal._token_cache["access_token"] = "t"
    garmin_personal._token_cache["expires_at"] = 9_999_999_999
    def urlopen(req, timeout=None):
        raise OSError("HTTP Error 404: Not Found")
    monkeypatch.setattr(garmin_personal.urllib.request, "urlopen", urlopen)
    assert garmin_personal.fetch_activity_breakdown(7) == {"error": "Garmin has no activity 7"}


def test_laps_tool_formats_garmin_breakdown(monkeypatch):
    from api import chat
    listed = [_act(42, "2026-01-03", lapCount=13, hrTimeInZone_1=60, hrTimeInZone_2=0,
                   hrTimeInZone_3=0, hrTimeInZone_4=0, hrTimeInZone_5=0)]
    _breakdown_fake(monkeypatch, _DETAIL, listed)
    out = chat._handle_get_activity_laps(42)
    assert "13 laps recorded" in out
    assert "work intervals ×6: 6.0km 24.8min 4.14min/km +40/-38m avg 162bpm" in out
    assert "per split ≈ 1.0km 4.1min" in out
    assert "HR time in zone (min): Z1 1.0 · Z2 0.0" in out
    assert chat._handle_get_activity_laps("x") == "Error: invalid activity_id"
