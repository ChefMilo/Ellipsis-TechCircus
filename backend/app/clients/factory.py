"""Selects concrete backends from Settings. Defaults to mock/heuristic (no keys needed).

Every real provider is behind a LAZY import, so the default offline path never needs the
`openai`, `tavily-python`, `anthropic`, `torch` or `transformers` packages installed.

WS5/WS6 (LLM / search / assessor) — two real families, not interchangeable halves:
  * "openai" means the OpenAI WIRE FORMAT — DASFAX_OPENAI_BASE_URL points it at
    Gemini/Groq/etc. Search on that path is a separate vendor ("tavily").
  * "anthropic" is NATIVE Messages API and covers all three roles on ONE key, because
    Claude's web_search is a built-in server tool. See app/clients/anthropic_compat.py.

WS4 (Tier 2 screening) defaults to `auto` — the real checkpoints when their weights are
available, the offline heuristics when they are not.
"""
from __future__ import annotations

from app.clients.base import AssessorClient, LLMClient, SearchClient
from app.clients.mock_assessor import MockAssessorClient
from app.clients.mock_llm import MockLLMClient
from app.clients.mock_search import MockSearchClient
from app.clients.screening_base import ImageScorer, TextScorer
from app.config import Settings, effective_image_mode, effective_text_mode, get_settings

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
    if settings.llm_provider == "anthropic":
        from app.clients.anthropic_llm import AnthropicLLMClient
        return AnthropicLLMClient(settings)
    raise ValueError(
        f"Unknown DASFAX_LLM_PROVIDER={settings.llm_provider!r} "
        "(expected 'mock', 'openai' or 'anthropic')"
    )


def make_search_client(settings: Settings | None = None) -> SearchClient:
    settings = settings or get_settings()
    if settings.search_provider == "mock":
        return MockSearchClient(demo_spread=settings.mock_demo)
    if settings.search_provider == "tavily":
        from app.clients.tavily_search import TavilySearchClient
        return TavilySearchClient(settings)
    if settings.search_provider == "anthropic":
        # Claude's built-in web_search server tool: no second vendor, no Tavily key.
        from app.clients.anthropic_search import AnthropicSearchClient
        return AnthropicSearchClient(settings)
    raise ValueError(
        f"Unknown DASFAX_SEARCH_PROVIDER={settings.search_provider!r} "
        "(expected 'mock', 'tavily' or 'anthropic')"
    )


def make_assessor_client(settings: Settings | None = None) -> AssessorClient:
    """WS6's assessor. Real providers drop in behind the same Protocol without touching
    the assessment service, which keeps citation-binding and the §2.5 downgrade."""
    settings = settings or get_settings()
    if settings.assessor_provider == "mock":
        return MockAssessorClient()
    if settings.assessor_provider == "openai":
        from app.clients.openai_assessor import OpenAIAssessorClient
        return OpenAIAssessorClient(settings)
    if settings.assessor_provider == "anthropic":
        from app.clients.anthropic_assessor import AnthropicAssessorClient
        return AnthropicAssessorClient(settings)
    raise ValueError(
        f"Unknown DASFAX_ASSESSOR_PROVIDER={settings.assessor_provider!r} "
        "(expected 'mock', 'openai' or 'anthropic')"
    )


# --------------------------------------------------------------------------- #
# WS4 — Tier 2 screening backends
# --------------------------------------------------------------------------- #
_SCREENING_MODES = ("auto", "huggingface", "heuristic")


def _check_mode(mode: str, var: str) -> str:
    if mode not in _SCREENING_MODES:
        raise ValueError(f"Unknown {var}={mode!r} (expected one of {', '.join(_SCREENING_MODES)})")
    return mode


def make_text_scorer(settings: Settings | None = None, *, strict: bool = False) -> TextScorer:
    """Build the Tier 2 text scorer.

    `strict=True` refuses to return a heuristic fallback. Evaluation and benchmarking pass
    it, because a silent degrade would otherwise produce a confusion matrix labelled "BERT"
    that is actually a regex — the single easiest way to publish a number that is a lie.
    """
    settings = settings or get_settings()
    mode = _check_mode(effective_text_mode(settings), "DASFAX_TEXT_MODE")
    from app.clients.heuristic_screening import HeuristicTextScorer

    if mode == "heuristic":
        if strict:
            raise RuntimeError("strict=True but this component is in heuristic mode; refusing to report on the fallback")
        return HeuristicTextScorer(threshold=settings.heuristic_text_threshold)

    try:
        from app.clients.hf_text import HFTextScorer
        return HFTextScorer(settings)
    except Exception as exc:
        # The shipped checkpoint is a local path produced by train_ws4.py, and its weights
        # are deliberately not in the repo (438MB, past GitHub's limit). A fresh clone
        # lands here, so say what to run rather than surfacing a raw HuggingFace error
        # about a "model identifier" that was never meant to be one.
        looks_local = "/" in settings.bert_model_name and not settings.bert_model_name.count("/") == 1
        if looks_local or settings.bert_model_name.startswith("models/"):
            reason = (
                f"text model not built yet at {settings.bert_model_name!r} — "
                f"run `python train_ws4.py` (~27 min, fixed seed) to produce it. "
                f"Using the heuristic until then."
            )
        else:
            reason = f"real text model unavailable ({exc.__class__.__name__}: {exc})"
        if strict or mode == "huggingface":
            raise RuntimeError(reason) from exc
        return HeuristicTextScorer(degraded_reason=reason, threshold=settings.heuristic_text_threshold)


def make_image_scorer(settings: Settings | None = None, *, strict: bool = False) -> ImageScorer:
    """Build the Tier 2 image scorer. See `make_text_scorer` for the strict contract."""
    settings = settings or get_settings()
    mode = _check_mode(effective_image_mode(settings), "DASFAX_IMAGE_MODE")
    from app.clients.heuristic_screening import HeuristicImageScorer

    if mode == "heuristic":
        if strict:
            raise RuntimeError("strict=True but this component is in heuristic mode; refusing to report on the fallback")
        return HeuristicImageScorer()

    try:
        from app.clients.hf_image import HFImageScorer
        return HFImageScorer(settings)
    except Exception as exc:
        reason = f"real image model unavailable ({exc.__class__.__name__}: {exc})"
        if strict or mode == "huggingface":
            raise RuntimeError(reason) from exc
        return HeuristicImageScorer(degraded_reason=reason)
