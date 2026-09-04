"""Per-persona methodology: read/write the config, and generate a methodology
brief from a short description using the deployment's configured LLM.

The coach/dietitian philosophy is user-owned config (config_blobs), never named
experts in the source. `llm` is imported lazily so the config helpers stay
importable without the LLM stack.
"""
from __future__ import annotations

from api.db import get_config_blob, set_config_blob

# Personas that have a configurable methodology slot in their prompt.
PERSONAS = {"coach", "dietitian"}

_ROLE = {
    "coach": "elite trail / ultra-running coach",
    "dietitian": "elite sports dietitian for endurance athletes",
}


def get_persona_config() -> dict:
    """Current methodology config for each configurable persona."""
    out = {}
    for p in PERSONAS:
        out[p] = {
            "mode": (get_config_blob(f"{p}_methodology") or "generic"),
            "text": (get_config_blob(f"{p}_methodology_text") or ""),
        }
    return out


def set_persona_config(persona: str, mode: str, text: str = "") -> None:
    if persona not in PERSONAS:
        raise ValueError(f"Unknown persona: {persona!r}.")
    mode = (mode or "generic").strip().lower()
    if mode not in ("generic", "custom"):
        raise ValueError("mode must be 'generic' or 'custom'.")
    set_config_blob(f"{persona}_methodology", mode)
    if mode == "custom":
        if not text.strip():
            raise ValueError("Custom methodology needs some text.")
        set_config_blob(f"{persona}_methodology_text", text.strip())


def generate_methodology(persona: str, description: str) -> str:
    """Draft a methodology brief for a persona from a free-text description, using
    the configured LLM. Returns markdown; the caller lets the user edit + save it."""
    if persona not in PERSONAS:
        raise ValueError(f"Unknown persona: {persona!r}.")
    description = (description or "").strip()
    if not description:
        raise ValueError("Describe the coach or dietitian you want.")
    if len(description) > 2000:
        raise ValueError("Description is too long (max 2000 characters).")

    from api import llm  # lazy: keeps config helpers importable without the LLM stack

    role = _ROLE[persona]
    system = {
        "role": "system",
        "content": (
            f"You write a concise methodology brief for an AI {role}. "
            "Output ONLY a short markdown section that starts with the line "
            "'## Methodology' followed by 4–7 imperative bullet points capturing the "
            "described training/nutrition philosophy. Be specific and practical. "
            "No preamble, no introduction, no closing remarks, no code fences."
        ),
    }
    user = {"role": "user", "content": f"Describe this {role}'s methodology:\n\n{description}"}
    resp = llm.completion([system, user], None, max_tokens=600)
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("The model returned nothing — try again or edit manually.")
    if not text.lstrip().startswith("#"):
        text = "## Methodology\n" + text
    return text
