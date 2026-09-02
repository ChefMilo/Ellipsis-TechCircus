"""Shared plumbing for the OpenAI-COMPATIBLE providers (WS5 extraction, WS6 assessment).

OPTIONAL — the mocks are the default and need none of this.

Provider-agnostic on purpose. OpenAI, Google Gemini and Groq all expose the same
`/chat/completions` shape, so the only thing that differs between them is the base URL
and the model name — both env vars:

    OPENAI_API_KEY            the key for whichever endpoint you point at
    DASFAX_OPENAI_BASE_URL    default https://api.openai.com/v1
                              Gemini: https://generativelanguage.googleapis.com/v1beta/openai/
    DASFAX_OPENAI_MODEL       e.g. gpt-4o-mini | gemini-2.0-flash | llama-3.3-70b-versatile

The `openai` SDK is imported LAZILY inside `build_chat_client`, so the mock path never
needs it installed. Both clients also accept an already-built `client=` object, which is
how the tests exercise them with no SDK, no key and no network.
"""
from __future__ import annotations

import json
from typing import Any

from app.config import DEFAULT_OPENAI_BASE_URL, Settings


def build_chat_client(settings: Settings, *, env_var: str) -> Any:
    """Build an OpenAI SDK client pointed at the configured (possibly non-OpenAI) endpoint.

    `env_var` is only used to name the switch that got us here in the error message.
    """
    if not settings.openai_api_key:
        raise RuntimeError(f"{env_var}=openai but OPENAI_API_KEY is not set.")
    from openai import OpenAI  # type: ignore  # lazy: real path only

    return OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or DEFAULT_OPENAI_BASE_URL,
    )


def chat_json(client: Any, *, model: str, system: str, user: str) -> str:
    """One deterministic JSON-mode chat turn. Returns the raw content string."""
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def loads_object(raw: str | None) -> dict[str, Any]:
    """Parse a model reply into a dict, returning {} for anything unusable.

    Strict about the result (it must be a JSON object) but tolerant of the one cosmetic
    habit several OpenAI-compatible endpoints have even in JSON mode: wrapping the object
    in a ```json fence. Everything else — truncated output, prose, a bare list — is
    garbage as far as callers are concerned, and they degrade rather than crash.
    """
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}
