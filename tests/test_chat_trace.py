"""The per-turn trace behind /api/chat/debug: inputs, tool calls, timings."""
from types import SimpleNamespace as NS

import pytest

from api import chat


def _chunk(text=None, tool=None, usage=None, choices=True):
    tcs = None
    if tool:
        tcs = [NS(index=0, id="c1", function=NS(name=tool[0], arguments=tool[1]))]
    ch = [NS(delta=NS(content=text, tool_calls=tcs))] if choices else []
    return NS(choices=ch, usage=usage)


@pytest.fixture
def fake_llm(monkeypatch):
    rounds = [
        [_chunk(tool=("get_goal", "{}"))],
        [_chunk(text="Hi "), _chunk(text="there"),
         _chunk(choices=False, usage={"prompt_tokens": 900, "completion_tokens": 5,
                                      "prompt_tokens_details": {"cached_tokens": 800}})],
    ]
    monkeypatch.setattr(chat.llm, "completion", lambda *a, **k: iter(rounds.pop(0)))
    monkeypatch.setattr(chat, "build_system", lambda p, c: ("SYS", "CTX"))
    monkeypatch.setattr(chat, "last_trace", None)


def test_stream_records_trace(fake_llm):
    out = "".join(chat.chat_stream([{"role": "user", "content": "hey"}],
                                   meta={"context_ms": 12, "context_cached": False}))
    assert '"done": true' in out
    t = chat.last_trace
    assert t["system"] == "SYS" and t["context"] == "CTX"
    assert t["messages"] == [{"role": "user", "content": "hey"}]
    assert t["context_ms"] == 12 and t["reply"] == "Hi there"
    assert [x["name"] for x in t["rounds"][0]["tools"]] == ["get_goal"]
    assert t["rounds"][0]["tools"][0]["result"]  # result text captured
    assert t["rounds"][1]["usage"] == {"prompt": 900, "completion": 5, "cached": 800}
    assert all("llm_ms" in r and "ttft_ms" in r for r in t["rounds"])
    assert "t0" not in t and t["total_ms"] >= 0


def test_debug_endpoint_previews_before_first_turn(monkeypatch):
    import server
    monkeypatch.setattr(chat, "last_trace", None)
    server.app.config.update(TESTING=True)
    c = server.app.test_client()
    c.post("/login", data={"password": "testpw"})
    d = c.get("/api/chat/debug?persona=coach").get_json()
    assert d["preview"] is True
    assert "trail-running coach" in d["system"]
    assert d["context"].startswith("## Live training data")
