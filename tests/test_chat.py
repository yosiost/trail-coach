"""Conversation-history sliding window (api.chat._windowed) — the safety net for
the unbounded-history fix. No LLM calls; pure list logic."""
from api import chat


def _msg(role, i):
    return {"role": role, "content": f"{role}-{i}"}


def test_windowed_noop_under_limit():
    msgs = [_msg("user", 0), _msg("assistant", 1)]
    assert chat._windowed(msgs, limit=10) == msgs


def test_windowed_trims_to_last_n():
    msgs = [_msg("user" if i % 2 == 0 else "assistant", i) for i in range(10)]
    out = chat._windowed(msgs, limit=4)
    assert len(out) == 4
    assert out == msgs[-4:]


def test_windowed_disabled_when_limit_zero():
    msgs = [_msg("user", i) for i in range(50)]
    assert chat._windowed(msgs, limit=0) == msgs


def test_windowed_drops_leading_orphaned_assistant():
    # Alternating u,a,u,a,u,a,u (7 messages) sliced to the last 4 would naively
    # start with "assistant" (index 3) — must drop it to keep a valid user-first
    # transcript for providers that require messages[0].role == "user".
    msgs = [_msg("user" if i % 2 == 0 else "assistant", i) for i in range(7)]
    out = chat._windowed(msgs, limit=4)
    assert len(out) == 3
    assert out[0]["role"] == "user"
    assert out == msgs[-3:]
