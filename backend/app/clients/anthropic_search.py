"""Native Anthropic web search (WS5 evidence retrieval). OPTIONAL — the mock is the default.

Only imported when DASFAX_SEARCH_PROVIDER=anthropic. ANTHROPIC-NATIVE and needs only
ANTHROPIC_API_KEY: with the extractor and assessor also set to anthropic, the whole real
path runs on ONE key — no OpenAI account, no Tavily account.

`web_search` is a Messages API SERVER TOOL — Anthropic runs the search itself and feeds the
results back into the same turn. It is not part of any OpenAI-compatible surface, which is
why this family is native rather than another base-URL swap.

Mechanism (checked against the installed `anthropic` SDK, 1.3.0). Two block types in the
response carry what we need:

  * `web_search_tool_result` -> `content` is a list of `web_search_result` blocks with
    `url`, `title` and `page_age` (`WebSearchResultBlock`). Its `encrypted_content` is
    opaque to us, so a result on its own gives no readable passage.
  * `text` -> `citations` of type `web_search_result_location`
    (`CitationsWebSearchResultLocation`) with `url`, `title` and `cited_text` — the actual
    passage the model quoted from that page.

So a citation's `cited_text` is the best snippet available, and a result's `title` is the
honest fallback when a page was found but never quoted.

Resilient by contract, exactly like tavily_search.py: any failure, any unexpected shape and
any answer with no results or citations returns [] rather than throwing into the pipeline.
"""
from __future__ import annotations

from typing import Any

from app.clients.anthropic_compat import attr, build_anthropic_client, iter_blocks
from app.clients.base import SearchClient, SearchHit
from app.config import Settings

_MAX_TOKENS = 2048
_MAX_SNIPPET = 500          # match tavily_search.py so evidence looks the same downstream

_SYSTEM = (
    "You are a research assistant retrieving EVIDENCE for a fact-check. Search the web for "
    "independent sources that confirm or refute the user's statement, preferring primary "
    "sources (official statistics, regulators, court records, company filings) and "
    "established news organisations. "
    "Answer in a few short sentences, each quoting what a specific source says about the "
    "statement. Do not speculate: if the sources do not address the statement, say so."
)

_QUERY_TEMPLATE = (
    "Find sources that confirm or refute this statement, and report what each one says "
    "about it:\n\n{query}"
)


def _clean(value: Any) -> str:
    return value.strip()[:_MAX_SNIPPET] if isinstance(value, str) else ""


def _cited_passages(message: Any) -> dict[str, str]:
    """url -> the longest passage the model actually quoted from that url."""
    passages: dict[str, str] = {}
    for block in iter_blocks(message, block_type="text"):
        for citation in attr(block, "citations") or []:
            if attr(citation, "type") != "web_search_result_location":
                continue
            url, cited = attr(citation, "url"), _clean(attr(citation, "cited_text"))
            if not isinstance(url, str) or not url or not cited:
                continue
            # Longest wins: a page quoted twice is best represented by its fuller passage.
            if len(cited) > len(passages.get(url, "")):
                passages[url] = cited
    return passages


def _cited_titles(message: Any) -> dict[str, str]:
    titles: dict[str, str] = {}
    for block in iter_blocks(message, block_type="text"):
        for citation in attr(block, "citations") or []:
            url, title = attr(citation, "url"), attr(citation, "title")
            if isinstance(url, str) and url and isinstance(title, str) and title:
                titles.setdefault(url, title)
    return titles


def _search_results(message: Any) -> list[tuple[str, str | None, str | None]]:
    """(url, title, page_age) for every web_search_result the server tool returned."""
    found: list[tuple[str, str | None, str | None]] = []
    for block in iter_blocks(message, block_type="web_search_tool_result"):
        content = attr(block, "content")
        if not isinstance(content, list):
            continue        # an error object instead of results -> nothing to cite
        for result in content:
            if attr(result, "type") != "web_search_result":
                continue
            url = attr(result, "url")
            if not isinstance(url, str) or not url:
                continue
            title = attr(result, "title")
            page_age = attr(result, "page_age")
            found.append(
                (
                    url,
                    title if isinstance(title, str) and title else None,
                    page_age if isinstance(page_age, str) and page_age else None,
                )
            )
    return found


def hits_from_message(message: Any, *, max_results: int) -> list[SearchHit]:
    """Map a Messages response to SearchHits. Module-level so it is testable on its own,
    and so a shape we do not recognise costs us hits, never an exception."""
    passages = _cited_passages(message)
    titles = _cited_titles(message)

    hits: list[SearchHit] = []
    seen: set[str] = set()

    def add(url: str, title: str | None, page_age: str | None) -> bool:
        """Returns False once max_results is reached."""
        if url in seen:
            return True
        snippet = passages.get(url) or _clean(title)
        if not snippet:
            return True     # a hit with nothing readable is not evidence
        seen.add(url)
        hits.append(
            SearchHit(
                snippet=snippet,
                url=url,
                title=title,
                # page_age is best-effort provenance, not a guaranteed publication date;
                # retrieval.py parses what it can and drops the rest.
                published_at=page_age,
                # The Messages API ranks nothing, so score stays None rather than invented.
            )
        )
        return len(hits) < max_results

    # Search results first (that is the provider's own ordering), then any page the model
    # quoted that never showed up as a result block.
    for url, title, page_age in _search_results(message):
        if not add(url, title or titles.get(url), page_age):
            return hits
    for url in passages:
        if not add(url, titles.get(url), None):
            break
    return hits


class AnthropicSearchClient(SearchClient):
    name = "anthropic-web-search"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        # `client` is an injection seam for tests: pass a stub and no key, SDK or network
        # is needed. Production always goes through build_anthropic_client.
        self._client = client if client is not None else build_anthropic_client(
            settings, env_var="DASFAX_SEARCH_PROVIDER"
        )
        self._model = settings.anthropic_model
        self._tool_version = settings.anthropic_web_search_tool
        self.name = f"anthropic-web-search:{settings.anthropic_model}"

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM,
                messages=[{"role": "user", "content": _QUERY_TEMPLATE.format(query=query)}],
                tools=[
                    {
                        # Dated server-tool version: keys differ on which they accept, so
                        # it is DASFAX_ANTHROPIC_WEB_SEARCH_TOOL rather than hard-coded.
                        "type": self._tool_version,
                        "name": "web_search",
                        "max_uses": max(1, max_results),
                    }
                ],
            )
            return hits_from_message(message, max_results=max_results)
        except Exception:
            return []           # never throw into the pipeline
