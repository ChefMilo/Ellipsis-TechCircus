"""Selects concrete LLM/search/assessor clients from Settings. Defaults to mock (no keys needed).

Every real provider is behind a LAZY import, so the default offline path never needs the
`openai`, `tavily-python` or `anthropic` packages installed.

Two real families, and they are not interchangeable halves:
  * "openai" means the OpenAI WIRE FORMAT — DASFAX_OPENAI_BASE_URL points it at
    Gemini/Groq/etc. Search on that path is a separate vendor ("tavily").
  * "anthropic" is NATIVE Messages API and covers all three roles on ONE key, because
    Claude's web_search is a built-in server tool. See app/clients/anthropic_compat.py.
"""
from __future__ import annotations

from app.clients.base import AssessorClient, LLMClient, SearchClient
from app.clients.mock_assessor import MockAssessorClient
from app.clients.mock_llm import MockLLMClient
from app.clients.mock_search import MockSearchClient
from app.config import Settings, get_settings


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
