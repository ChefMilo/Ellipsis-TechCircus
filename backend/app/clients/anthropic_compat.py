"""Shared plumbing for the NATIVE Anthropic providers (read, verify, search).

OPTIONAL — the mocks are the default and need none of this.

Native, not the OpenAI-compat shim, for two reasons: Claude's `web_search` is a Messages
API *server tool* that the compat endpoint does not expose at all, and a key you are
A/B-testing should be exercised against the API it is actually for.

    ANTHROPIC_API_KEY         the key (this family reads nothing else)
    DASFAX_ANTHROPIC_MODEL    e.g. claude-sonnet-5

The `anthropic` SDK is imported LAZILY inside `build_anthropic_client`, so the mock path
never needs it installed. Every client here also accepts an already-built `client=`, which
is how the tests exercise them with no SDK, no key and no network.

Deliberately self-contained: it duplicates a little of openai_compat.py (JSON coercion)
rather than importing it, so the anthropic_* and openai_* families can each be deleted
without touching the other once the team picks a provider.
"""
from __future__ import annotations

import json
from typing import Any

from app.config import Settings


def build_anthropic_client(settings: Settings, *, env_var: str) -> Any:
    """Build an Anthropic SDK client. `env_var` names the switch that got us here, so a
    half-configured run says which provider is missing its key."""
    if not settings.anthropic_api_key:
        raise RuntimeError(f"{env_var}=anthropic but ANTHROPIC_API_KEY is not set.")
    from anthropic import Anthropic  # type: ignore  # lazy: real path only

    return Anthropic(api_key=settings.anthropic_api_key)


def attr(obj: Any, name: str) -> Any:
    """Read `name` off an SDK model or a plain dict — responses arrive as pydantic
    objects, but a caller handing us `message.model_dump()` should work too."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def iter_blocks(message: Any, *, block_type: str | None = None):
    """Yield the response's content blocks, optionally only those of one `type`."""
    for block in attr(message, "content") or []:
        if block_type is None or attr(block, "type") == block_type:
            yield block


def text_of(message: Any) -> str:
    """Concatenate the assistant's text blocks."""
    parts = [attr(b, "text") for b in iter_blocks(message, block_type="text")]
    return "\n".join(p for p in parts if isinstance(p, str))


def loads_object(raw: str | None) -> dict[str, Any]:
    """Parse a model reply into a dict, returning {} for anything unusable.

    Mirrors openai_compat.loads_object on purpose (see the module docstring): strict about
    the result being a JSON object, tolerant of a ```json fence around it.
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


def json_via_tool(
    client: Any,
    *,
    model: str,
    max_tokens: int,
    system: str,
    user: str,
    tool: dict[str, Any],
) -> dict[str, Any]:
    """One deterministic Messages turn that must answer through a tool, returning the
    tool's `input` dict.

    A forced `tool_choice` is how you get schema-shaped output out of the Messages API —
    the model fills in the tool's `input_schema` instead of writing prose that happens to
    look like JSON. If the model answers with text anyway (or the block is missing), fall
    back to parsing that text, and return {} if even that is unusable. Callers treat {} as
    "no usable answer" and degrade; nothing here raises on a bad shape.
    """
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
    )

    for block in iter_blocks(message, block_type="tool_use"):
        if attr(block, "name") != tool["name"]:
            continue
        payload = attr(block, "input")
        if isinstance(payload, dict):
            return payload

    return loads_object(text_of(message))
