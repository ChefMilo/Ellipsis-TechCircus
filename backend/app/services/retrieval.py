"""Evidence retrieval: turn a claim into a search query, fetch snippets, shape Evidence.

Kept deliberately small — the search provider is injected (mock or Tavily), so this
module is pure orchestration and query construction.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.clients.base import SearchClient, SearchHit
from app.models.contract import Evidence

_QUOTE_STRIP = re.compile(r'^[\s"\'“”]+|[\s"\'“”]+$')
_WS = re.compile(r"\s+")


def build_query(claim_text: str) -> str:
    """A claim rewritten as a self-contained sentence already makes a decent query.
    We trim length and surrounding quotes so providers don't treat it as an exact-phrase
    match (which would over-narrow)."""
    q = _QUOTE_STRIP.sub("", claim_text)
    q = _WS.sub(" ", q).strip()
    # Keep it query-length; very long claims dilute retrieval.
    words = q.split()
    if len(words) > 32:
        q = " ".join(words[:32])
    return q


def _domain(url: str) -> str | None:
    m = re.match(r"https?://([^/]+)/?", url)
    return m.group(1).lower() if m else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value[: len(fmt) + 2], fmt)
        except ValueError:
            continue
    return None


def _hit_to_evidence(hit: SearchHit) -> Evidence:
    return Evidence(
        snippet=hit.snippet,
        source_url=hit.url,
        source_title=hit.title,
        source_domain=_domain(hit.url),
        published_at=_parse_dt(hit.published_at),
        relevance_score=hit.score,
    )


def retrieve_evidence(
    claim_text: str,
    *,
    client: SearchClient,
    max_results: int,
    article_url: str | None = None,
) -> tuple[str, list[Evidence]]:
    """Return (query, evidence[]). Filters out sources on the same domain as the article
    itself (an article is not evidence for its own claim). Never raises on provider error
    — returns empty evidence so a single flaky query can't sink the whole request."""
    query = build_query(claim_text)
    try:
        hits = client.search(query, max_results=max_results + 1)
    except Exception:  # noqa: BLE001 — resilience: retrieval failure -> no evidence, not a crash
        return query, []

    article_domain = _domain(article_url) if article_url else None
    evidence: list[Evidence] = []
    for hit in hits:
        if article_domain and _domain(hit.url) == article_domain:
            continue
        evidence.append(_hit_to_evidence(hit))
        if len(evidence) >= max_results:
            break
    return query, evidence
