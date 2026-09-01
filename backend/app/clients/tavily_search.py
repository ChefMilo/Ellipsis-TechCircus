"""Real search client (Tavily). OPTIONAL — the mock is the default.

Only imported when DASFAX_SEARCH_PROVIDER=tavily. Tavily is chosen because it is
built for LLM/RAG use and returns clean snippets in one call. To use another provider
(Serper, Brave, Google PSE), add a sibling file implementing the SearchClient interface
and register it in factory.py — nothing else changes.
"""
from __future__ import annotations

from app.clients.base import SearchClient, SearchHit
from app.config import Settings


class TavilySearchClient(SearchClient):
    name = "tavily"

    def __init__(self, settings: Settings) -> None:
        if not settings.tavily_api_key:
            raise RuntimeError("DASFAX_SEARCH_PROVIDER=tavily but TAVILY_API_KEY is not set.")
        from tavily import TavilyClient  # type: ignore  # lazy import

        self._client = TavilyClient(api_key=settings.tavily_api_key)

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        resp = self._client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=False,
        )
        hits: list[SearchHit] = []
        for r in resp.get("results", []):
            hits.append(
                SearchHit(
                    snippet=(r.get("content") or "")[:500],
                    url=r.get("url", ""),
                    title=r.get("title"),
                    published_at=r.get("published_date"),
                    score=r.get("score"),
                )
            )
        return hits
