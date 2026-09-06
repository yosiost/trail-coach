"""Prompt-caching gating (api.llm) — provider allowlist, system_message()/
with_cache_control() shape. No real API calls: none of these touch the lazily-
imported litellm module."""
import pytest
from api import llm


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    for key in ("LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("provider", [
    "anthropic", "openai", "gemini", "vertex_ai", "bedrock", "deepseek", "xai",
])
def test_system_message_cached_on_allowlisted_providers(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    msg = llm.system_message("base", "ctx")
    assert msg["role"] == "system"
    assert msg["content"] == [
        {"type": "text", "text": "base\n\nctx", "cache_control": {"type": "ephemeral"}}
    ]


@pytest.mark.parametrize("provider", ["ollama", "groq", "some-custom-provider"])
def test_system_message_plain_string_on_unsupported_providers(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    msg = llm.system_message("base", "ctx")
    assert msg == {"role": "system", "content": "base\n\nctx"}


def test_system_message_no_context_omits_blank_line():
    msg = llm.system_message("base", None)
    # default provider is anthropic -> cached shape, but no "\n\n" separator
    assert msg["content"][0]["text"] == "base"


def test_resolved_provider_extracts_from_fully_qualified_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openrouter/some-model")
    assert llm.resolved_provider() == "openrouter"
    assert llm.cache_control_enabled() is False  # openrouter not in the allowlist


def test_resolved_provider_falls_back_to_llm_provider_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-pro")
    assert llm.resolved_provider() == "gemini"
    assert llm.cache_control_enabled() is True


def test_with_cache_control_marks_only_last_tool(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    tools = [{"type": "function", "function": {"name": "a"}},
              {"type": "function", "function": {"name": "b"}}]
    out = llm.with_cache_control(tools)
    assert "cache_control" not in out[0]
    assert out[1]["cache_control"] == {"type": "ephemeral"}
    assert tools[1] == {"type": "function", "function": {"name": "b"}}  # input untouched


def test_with_cache_control_noop_for_unsupported_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    tools = [{"type": "function", "function": {"name": "a"}}]
    assert llm.with_cache_control(tools) == tools
    assert llm.with_cache_control(tools) is tools  # returned unchanged, not copied


def test_with_cache_control_noop_for_empty_tools(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert llm.with_cache_control([]) == []


def test_model_string_uses_resolved_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    assert llm.model_string() == "openai/gpt-4o"


def test_model_string_passthrough_when_already_qualified(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openrouter/some-model")
    assert llm.model_string() == "openrouter/some-model"
