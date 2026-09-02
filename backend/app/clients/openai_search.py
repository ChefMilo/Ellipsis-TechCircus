"""Real search client using OPENAI'S BUILT-IN WEB SEARCH. OPTIONAL — the mock is the default.

Only imported when DASFAX_SEARCH_PROVIDER=openai. Its point is one fewer account to set up
on demo day: with the OpenAI extractor and assessor already configured, this makes the
whole real path run on OPENAI_API_KEY alone — no Tavily key. The Tavily client remains as
the alternative and is unchanged.

!! OPENAI-SPECIFIC, unlike the other openai_* clients. !!
The extractor and assessor only speak the OpenAI WIRE FORMAT, so DASFAX_OPENAI_BASE_URL can
point them at Gemini or Groq. Web search is not part of that wire format — it is a hosted
OpenAI tool. Pointing the base URL at Gemini/Groq and selecting this provider will simply
fail the call, and (by the contract below) yield no evidence rather than an error. This
provider assumes the real OpenAI endpoint; use Tavily if the LLM lives somewhere else.

Mechanism (checked against the installed `openai` SDK, 3.7.0): the Responses API with the
hosted `{"type": "web_search"}` tool. The model answers the query and attaches
`url_citation` annotations naming the pages it used —
`openai/types/responses/response_output_text.py::AnnotationURLCitation` carries `url`,
`title`, and `start_index`/`end_index` into the answer text. Those citations ARE the search
results: each one becomes a SearchHit whose snippet is the answer span it supports.

Resilient by contract, exactly like tavily_search.py: any failure, any unexpected shape and
any answer without citations returns [] rather than throwing into the pipeline.
"""
from __future__ import annotations

from typing import Any

from app.clients.base import SearchClient, SearchHit
from app.clients.openai_compat import build_chat_client
from app.config import Settings

_MAX_SNIPPET = 500          # match tavily_search.py so evidence looks the same downstream
_MIN_SNIPPET = 40           # below this the annotated span is a fragment, not evidence

_INSTRUCTIONS = (
    "You are a research assistant retrieving EVIDENCE for a fact-check. Search the web for "
    "independent sources that confirm or refute the user's statement, preferring primary "
    "sources (official statistics, regulators, court records, company filings) and "
    "established news organisations. "
    "Answer in a few short sentences, each stating what a specific source says about the "
    "statement, and cite every source you use. Do not speculate: if the sources do not "
    "address the statement, say so plainly."
)

_QUERY_TEMPLATE = (
    "Find sources that confirm or refute this statement, and report what each one says "
    "about it:\n\n{query}"
)


def _attr(obj: Any, name: str) -> Any:
    """Read `name` off an SDK model or a plain dict — the Responses payload arrives as
    pydantic objects, but a caller handing us `response.model_dump()` should work too."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _iter_output_texts(response: Any):
    """Yield every (text, annotations) pair in the response's assistant messages."""
    for item in _attr(response, "output") or []:
        if _attr(item, "type") != "message":
            continue
        for part in _attr(item, "content") or []:
            if _attr(part, "type") != "output_text":
                continue
            text = _attr(part, "text")
            if isinstance(text, str):
                yield text, _attr(part, "annotations") or []


def _snippet_for(text: str, annotation: Any) -> str:
    """The most source-representative text available for one citation.

    A url_citation marks the span of the answer that the cited page supports, so that span
    is the closest thing the Responses API gives us to a retrieved passage. When the span
    is missing or too short to stand alone as evidence (models sometimes annotate just the
    trailing clause), fall back to the whole answer segment the citation sits in.
    """
    start, end = _attr(annotation, "start_index"), _attr(annotation, "end_index")
    span = ""
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        span = text[start:end].strip()
    if len(span) < _MIN_SNIPPET:
        span = text.strip()
    return span[:_MAX_SNIPPET]


def hits_from_response(response: Any, *, max_results: int) -> list[SearchHit]:
    """Map url_citation annotations to SearchHits. Module-level so it is testable on its
    own, and so a shape we do not recognise costs us hits, never an exception."""
    hits: list[SearchHit] = []
    seen: set[str] = set()

    for text, cited in _iter_output_texts(response):
        for annotation in cited:
            if _attr(annotation, "type") != "url_citation":
                continue
            url = _attr(annotation, "url")
            if not isinstance(url, str) or not url or url in seen:
                continue          # no url means nothing citable; §2.5 needs a real source
            snippet = _snippet_for(text, annotation)
            if not snippet:
                continue
            seen.add(url)
            title = _attr(annotation, "title")
            hits.append(
                SearchHit(
                    snippet=snippet,
                    url=url,
                    title=title if isinstance(title, str) and title else None,
                    # The Responses API dates nothing and ranks nothing, so both stay None
                    # rather than being invented. retrieval.py already treats them as optional.
                )
            )
            if len(hits) >= max_results:
                return hits
    return hits


def _is_unsupported_tool_error(exc: Exception) -> bool:
    """True when the endpoint rejected the tool TYPE rather than the request.

    `web_search` (2025-08-26) superseded `web_search_preview`, but which one an account or
    model accepts still varies. A tool-type rejection is worth one retry with the older
    name; anything else (auth, rate limit, network) is not.
    """
    message = str(exc).lower()
    return "web_search" in message and any(
        phrase in message for phrase in ("not supported", "unsupported", "invalid", "unknown")
    )


class OpenAISearchClient(SearchClient):
    name = "openai-web-search"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        # `client` is an injection seam for tests: pass a stub and no key, SDK or network
        # is needed. Production always goes through build_chat_client.
        self._client = client if client is not None else build_chat_client(
            settings, env_var="DASFAX_SEARCH_PROVIDER"
        )
        # Its own model knob: web search is only offered on some OpenAI models, and
        # DASFAX_OPENAI_MODEL may legitimately name a Gemini/Groq model for the other two
        # clients, which would be meaningless here.
        self._model = settings.openai_search_model
        self.name = f"openai-web-search:{self._model}"

    def _respond(self, query: str, *, tool_type: str) -> Any:
        return self._client.responses.create(
            model=self._model,
            instructions=_INSTRUCTIONS,
            input=_QUERY_TEMPLATE.format(query=query),
            tools=[{"type": tool_type}],
        )

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        try:
            try:
                response = self._respond(query, tool_type="web_search")
            except Exception as exc:
                if not _is_unsupported_tool_error(exc):
                    raise
                response = self._respond(query, tool_type="web_search_preview")
            return hits_from_response(response, max_results=max_results)
        except Exception:
            return []           # never throw into the pipeline
