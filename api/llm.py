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

import os

import litellm

# Silently drop request params a given provider doesn't support, so the same
# call works across providers without provider-specific branching here.
litellm.drop_params = True

_DEFAULT_PROVIDER = "anthropic"
_DEFAULT_MODEL = "claude-sonnet-4-6"


def model_string() -> str:
    """Return the LiteLLM 'provider/model' string from env.

    If LLM_MODEL already contains a '/', it is used verbatim (lets advanced users
    specify a fully-qualified LiteLLM model id).
    """
    provider = os.environ.get("LLM_PROVIDER", _DEFAULT_PROVIDER).strip() or _DEFAULT_PROVIDER
    model = os.environ.get("LLM_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    return model if "/" in model else f"{provider}/{model}"


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
    """Build the system message as a single OpenAI-style message.

    Kept as plain text for cross-provider robustness. (Anthropic prompt-caching
    via cache_control blocks can be layered back in as a provider-conditional
    optimization later — see roadmap §3.6.)
    """
    text = base if not context else f"{base}\n\n{context}"
    return {"role": "system", "content": text}


def completion(messages: list[dict], tools: list[dict], max_tokens: int, stream: bool = False):
    """Call the configured model. Returns a ModelResponse (stream=False) or an
    iterable stream wrapper (stream=True), both in OpenAI-normalized shape."""
    return litellm.completion(
        model=model_string(),
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
        stream=stream,
        **_auth_kwargs(),
    )
