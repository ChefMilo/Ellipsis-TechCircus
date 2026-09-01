"""Selects concrete LLM/search clients from Settings. Defaults to mock (no keys needed)."""
from __future__ import annotations

from app.clients.base import LLMClient, SearchClient
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
    raise ValueError(f"Unknown DASFAX_LLM_PROVIDER={settings.llm_provider!r} (expected 'mock' or 'openai')")


def make_search_client(settings: Settings | None = None) -> SearchClient:
    settings = settings or get_settings()
    if settings.search_provider == "mock":
        return MockSearchClient()
    if settings.search_provider == "tavily":
        from app.clients.tavily_search import TavilySearchClient
        return TavilySearchClient(settings)
    raise ValueError(f"Unknown DASFAX_SEARCH_PROVIDER={settings.search_provider!r} (expected 'mock' or 'tavily')")
