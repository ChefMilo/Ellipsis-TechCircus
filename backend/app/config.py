"""Runtime configuration for WS5.

Everything defaults to the MOCK backends so the whole pipeline runs with no API keys.
Set the env vars below to swap in real providers without touching pipeline code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# The OpenAI-compatible endpoint used when DASFAX_OPENAI_BASE_URL is unset. Any provider
# that speaks the same /chat/completions shape (Gemini, Groq, ...) is a base-URL swap.
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# Default model for the NATIVE anthropic providers (read + verify + search). Set
# DASFAX_ANTHROPIC_MODEL to a model your key actually has access to.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"

# Anthropic's web_search is a dated SERVER TOOL and keys differ on which versions they
# accept. The SDK also ships web_search_20260209 / web_search_20260318; this is the
# long-standing one, overridable with DASFAX_ANTHROPIC_WEB_SEARCH_TOOL.
DEFAULT_ANTHROPIC_WEB_SEARCH_TOOL = "web_search_20250305"


@dataclass(frozen=True)
class Settings:
    # Which backends to use. "mock" (default) needs no keys.
    llm_provider: str = os.environ.get("DASFAX_LLM_PROVIDER", "mock")        # mock | openai | anthropic
    search_provider: str = os.environ.get("DASFAX_SEARCH_PROVIDER", "mock")  # mock | tavily | anthropic
    assessor_provider: str = os.environ.get("DASFAX_ASSESSOR_PROVIDER", "mock")  # mock | openai | anthropic

    # Demo/presentation only. OFF by default; changes nothing about the normal mock run.
    # See app/clients/mock_search.py for exactly what it does and why.
    mock_demo: bool = _get_bool("DASFAX_MOCK_DEMO")

    # Credentials (only read by the real providers).
    # "openai" here means the OpenAI WIRE FORMAT, not the vendor: point base_url at
    # Gemini/Groq/vLLM and the same client works with only these two vars changed.
    openai_api_key: str | None = os.environ.get("OPENAI_API_KEY")
    openai_base_url: str = os.environ.get("DASFAX_OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
    openai_model: str = os.environ.get("DASFAX_OPENAI_MODEL", "gpt-4o-mini")
    tavily_api_key: str | None = os.environ.get("TAVILY_API_KEY")

    # NATIVE Anthropic (Messages API), used by all three "anthropic" providers. One key
    # covers read + verify + search, because Claude's web_search is a built-in server tool
    # — no OpenAI key and no Tavily key are read on that path.
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY")
    anthropic_model: str = os.environ.get("DASFAX_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
    anthropic_web_search_tool: str = os.environ.get(
        "DASFAX_ANTHROPIC_WEB_SEARCH_TOOL", DEFAULT_ANTHROPIC_WEB_SEARCH_TOOL
    )

    # WS5 tuning knobs. These are the numbers to defend in the pitch.
    max_claims: int = _get_int("DASFAX_MAX_CLAIMS", 5)                       # top-N load-bearing claims (proposal: 3–5)
    min_checkworthiness: float = _get_float("DASFAX_MIN_CHECKWORTHINESS", 0.35)
    evidence_per_claim: int = _get_int("DASFAX_EVIDENCE_PER_CLAIM", 3)       # proposal "done when": >=3 sources/claim
    dedup_threshold: float = _get_float("DASFAX_DEDUP_THRESHOLD", 0.85)      # token-Jaccard above this = duplicate
    anchor_min_similarity: float = _get_float("DASFAX_ANCHOR_MIN_SIMILARITY", 0.5)  # min Dice to anchor a rewritten claim


def get_settings() -> Settings:
    return Settings()
