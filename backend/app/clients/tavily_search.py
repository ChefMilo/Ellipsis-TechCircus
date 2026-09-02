"""Real search client (Tavily). OPTIONAL — the mock is the default.

Only imported when DASFAX_SEARCH_PROVIDER=tavily. Tavily is chosen because it is
built for LLM/RAG use and returns clean snippets in one call. To use another provider
(Serper, Brave, Google PSE), add a sibling file implementing the SearchClient interface
and register it in factory.py — nothing else changes.

Retrieval is best-effort by contract: a search that fails returns NO hits rather than
raising. WS5 then produces a claim with no evidence, and WS6 reports needs_review for it
(services/assessment.py) — one unverifiable claim instead of a dead request.
"""
from __future__ import annotations

from typing import Any

from app.clients.base import SearchClient, SearchHit
from app.config import Settings


class TavilySearchClient(SearchClient):
    name = "tavily"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        # `client` is an injection seam for tests: pass a stub and no key, SDK or network
        # is needed.
        if client is not None:
            self._client = client
            return
        if not settings.tavily_api_key:
            raise RuntimeError("DASFAX_SEARCH_PROVIDER=tavily but TAVILY_API_KEY is not set.")
        from tavily import TavilyClient  # type: ignore  # lazy import: real path only

        self._client = TavilyClient(api_key=settings.tavily_api_key)

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        try:
            resp = self._client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",   # cheap + fast; the snippets are what we need
                include_answer=False,   # we cite sources, never a search engine's summary
            )
        except Exception:
            return []                   # never throw into the pipeline

        if not isinstance(resp, dict):
            return []

        hits: list[SearchHit] = []
        for r in resp.get("results", []) or []:
            if not isinstance(r, dict):
                continue
            url = r.get("url")
            snippet = r.get("content") or ""
            if not url or not snippet:
                continue                # a hit we cannot cite is not evidence
            hits.append(
                SearchHit(
                    snippet=str(snippet)[:500],
                    url=str(url),
                    title=r.get("title"),
                    published_at=r.get("published_date"),
                    score=r.get("score"),
                )
            )
        return hits[:max_results]
