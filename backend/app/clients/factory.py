"""Selects concrete backends from Settings.

WS5 (LLM/search) defaults to mock; WS4 (Tier 2 screening) defaults to `auto` — the real
checkpoints when their weights are available, the offline heuristics when they are not.
"""
from __future__ import annotations

from app.clients.base import LLMClient, SearchClient
from app.clients.mock_llm import MockLLMClient
from app.clients.mock_search import MockSearchClient
from app.clients.screening_base import ImageScorer, TextScorer
from app.config import Settings, get_settings

# The heuristic screening backends are imported lazily inside the factory functions below,
# like the other concrete providers in this file. Importing them at module level would
# close a cycle: app/clients/__init__.py eagerly imports this module, and the heuristic
# backends import app.services.*, which import back into app.clients.


def make_llm_client(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    if settings.llm_provider == "mock":
        return MockLLMClient()
    if settings.llm_provider == "openai":
        from app.clients.openai_llm import OpenAILLMClient
        return OpenAILLMClient(settings)
    raise ValueError(f"Unknown DASFAX_LLM_PROVIDER={settings.llm_provider!r} (expected 'mock' or 'openai')")


def make_search_client(settings: Settings | None = None) -> SearchClient:
    settings = settings or get_settings()
    if settings.search_provider == "mock":
        return MockSearchClient()
    if settings.search_provider == "tavily":
        from app.clients.tavily_search import TavilySearchClient
        return TavilySearchClient(settings)
    raise ValueError(f"Unknown DASFAX_SEARCH_PROVIDER={settings.search_provider!r} (expected 'mock' or 'tavily')")


# --------------------------------------------------------------------------- #
# WS4 — Tier 2 screening backends
# --------------------------------------------------------------------------- #
_SCREENING_MODES = ("auto", "huggingface", "heuristic")


def _check_mode(settings: Settings) -> str:
    if settings.screening_mode not in _SCREENING_MODES:
        raise ValueError(
            f"Unknown DASFAX_SCREENING_MODE={settings.screening_mode!r} "
            f"(expected one of {', '.join(_SCREENING_MODES)})"
        )
    return settings.screening_mode


def make_text_scorer(settings: Settings | None = None, *, strict: bool = False) -> TextScorer:
    """Build the Tier 2 text scorer.

    `strict=True` refuses to return a heuristic fallback. Evaluation and benchmarking pass
    it, because a silent degrade would otherwise produce a confusion matrix labelled "BERT"
    that is actually a regex — the single easiest way to publish a number that is a lie.
    """
    settings = settings or get_settings()
    mode = _check_mode(settings)
    from app.clients.heuristic_screening import HeuristicTextScorer

    if mode == "heuristic":
        if strict:
            raise RuntimeError("strict=True but DASFAX_SCREENING_MODE=heuristic; refusing to report on the fallback")
        return HeuristicTextScorer()

    try:
        from app.clients.hf_text import HFTextScorer
        return HFTextScorer(settings)
    except Exception as exc:
        reason = f"real text model unavailable ({exc.__class__.__name__}: {exc})"
        if strict or mode == "huggingface":
            raise RuntimeError(reason) from exc
        return HeuristicTextScorer(degraded_reason=reason)


def make_image_scorer(settings: Settings | None = None, *, strict: bool = False) -> ImageScorer:
    """Build the Tier 2 image scorer. See `make_text_scorer` for the strict contract."""
    settings = settings or get_settings()
    mode = _check_mode(settings)
    from app.clients.heuristic_screening import HeuristicImageScorer

    if mode == "heuristic":
        if strict:
            raise RuntimeError("strict=True but DASFAX_SCREENING_MODE=heuristic; refusing to report on the fallback")
        return HeuristicImageScorer()

    try:
        from app.clients.hf_image import HFImageScorer
        return HFImageScorer(settings)
    except Exception as exc:
        reason = f"real image model unavailable ({exc.__class__.__name__}: {exc})"
        if strict or mode == "huggingface":
            raise RuntimeError(reason) from exc
        return HeuristicImageScorer(degraded_reason=reason)
