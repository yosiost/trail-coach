"""Provider-agnostic LLM access via LiteLLM.

The coach talks to whatever model the deployment configures — not to a single
vendor. Configure with env vars:

  LLM_PROVIDER   provider slug (anthropic | openai | gemini | groq | ollama | ...)
  LLM_MODEL      model id for that provider (e.g. claude-sonnet-4-6, gpt-4o)
  LLM_API_KEY    api key for the provider. Optional: if unset, LiteLLM falls back
                 to the provider's own env var (e.g. ANTHROPIC_API_KEY), so an
                 existing Anthropic-only setup keeps working unchanged.
  LLM_BASE_URL   base URL for OpenAI-compatible / local endpoints (Ollama, LM
                 Studio, OpenRouter, …). Optional.

Tools are authored once in Anthropic's schema (see api/chat.py) and translated
here to the OpenAI function-calling schema that LiteLLM normalizes on.
"""

from __future__ import annotations

import os

# litellm is imported lazily (it's a heavy import) so the app — and the test
# suite — can import this module without the LLM stack installed. It's only
# needed when a completion is actually made.
_DEFAULT_PROVIDER = "anthropic"
_DEFAULT_MODEL = "claude-sonnet-4-6"

# Providers LiteLLM documents cache_control (prompt caching) support for. Any
# other provider (Ollama, a custom OpenAI-compatible LLM_BASE_URL, an
# unrecognized fully-qualified model, ...) gets today's uncached behavior
# unchanged — no regression risk for self-hosters on those.
_CACHE_CONTROL_PROVIDERS = frozenset({
    "anthropic", "openai", "gemini", "vertex_ai", "bedrock", "deepseek", "xai",
})


def _litellm():
    import litellm
    # Silently drop request params a given provider doesn't support, so the same
    # call works across providers without provider-specific branching here.
    litellm.drop_params = True
    return litellm


def resolved_provider() -> str:
    """The provider slug in effect: parsed from LLM_MODEL if it's already
    'provider/model', else LLM_PROVIDER (or the default)."""
    model = os.environ.get("LLM_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    if "/" in model:
        return model.split("/", 1)[0]
    return os.environ.get("LLM_PROVIDER", _DEFAULT_PROVIDER).strip() or _DEFAULT_PROVIDER


def model_string() -> str:
    """Return the LiteLLM 'provider/model' string from env.

    If LLM_MODEL already contains a '/', it is used verbatim (lets advanced users
    specify a fully-qualified LiteLLM model id).
    """
    model = os.environ.get("LLM_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    return model if "/" in model else f"{resolved_provider()}/{model}"


def cache_control_enabled() -> bool:
    return resolved_provider() in _CACHE_CONTROL_PROVIDERS


def _auth_kwargs() -> dict:
    """Explicit api_key / api_base only when configured; otherwise let LiteLLM
    read the provider's own env var (backward-compatible with ANTHROPIC_API_KEY)."""
    kw: dict = {}
    key = os.environ.get("LLM_API_KEY", "").strip()
    if key:
        kw["api_key"] = key
    base = os.environ.get("LLM_BASE_URL", "").strip()
    if base:
        kw["api_base"] = base
    return kw


def to_openai_tools(tools: list[dict]) -> list[dict]:
    """Translate Anthropic-style tool defs (name/description/input_schema) into
    the OpenAI function-calling schema LiteLLM expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def system_message(base: str, context: str | None) -> dict:
    """Build the system message. Plain string on providers without documented
    cache_control support (unchanged from before); a single cache_control-marked
    content block on providers that do, so the ~5K-token system+context prefix
    (mostly static turn-to-turn) is cached instead of re-billed every request."""
    text = base if not context else f"{base}\n\n{context}"
    if cache_control_enabled():
        return {"role": "system", "content": [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
        ]}
    return {"role": "system", "content": text}


def with_cache_control(tools: list[dict]) -> list[dict]:
    """Mark the last tool with cache_control so the tools+system prefix caches as
    one block (Anthropic-style caching covers everything up to and including a
    marked block; capped to a few breakpoints, so mark sparingly). No-op when the
    provider doesn't support it, or there are no tools."""
    if not tools or not cache_control_enabled():
        return tools
    tools = [dict(t) for t in tools]
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def completion(messages: list[dict], tools: list[dict], max_tokens: int, stream: bool = False):
    """Call the configured model. Returns a ModelResponse (stream=False) or an
    iterable stream wrapper (stream=True), both in OpenAI-normalized shape."""
    return _litellm().completion(
        model=model_string(),
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
        stream=stream,
        **_auth_kwargs(),
    )
