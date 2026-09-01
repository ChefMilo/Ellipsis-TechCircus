"""Deterministic mock search client.

Fabricates plausible, claim-relevant evidence snippets from a small pool of fake
Singapore-relevant "sources", seeded by the query so results are stable across runs.
Runs with no API key. Swap in the real Tavily client (same interface) later.

The snippets are obviously synthetic (domains under example.org) so nobody mistakes
mock evidence for real citations in the demo.
"""
from __future__ import annotations

import hashlib

from app.clients.base import SearchClient, SearchHit

# Fake but realistic-looking source pool. example.* is reserved for docs/tests, so
# these can never be confused with live citations.
_FAKE_SOURCES = [
    ("factcheck.example.org", "Fact Check SG"),
    ("archive.example.org", "National Records Archive"),
    ("wire.example.org", "Newswire Asia"),
    ("gov-data.example.org", "Open Government Data"),
    ("research.example.org", "Policy Research Institute"),
    ("press.example.org", "Regional Press Pool"),
]


def _seed(query: str, i: int) -> int:
    h = hashlib.sha256(f"{query}::{i}".encode()).hexdigest()
    return int(h[:8], 16)


class MockSearchClient(SearchClient):
    name = "mock-search-seeded-v1"

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        hits: list[SearchHit] = []
        q = query.strip()
        for i in range(max_results):
            seed = _seed(q, i)
            domain, title = _FAKE_SOURCES[seed % len(_FAKE_SOURCES)]
            # Alternate stance so WS6 has both supporting- and questioning-flavoured evidence.
            stance = ["Records indicate that", "Reporting corroborates that", "One analysis disputes that"][seed % 3]
            snippet = (
                f"{stance} {q[:120]}. "
                f"[MOCK EVIDENCE #{i + 1} — synthetic snippet for offline development; replace with Tavily/Serper.]"
            )
            hits.append(
                SearchHit(
                    snippet=snippet,
                    url=f"https://{domain}/article/{seed % 10000}",
                    title=f"{title}: {q[:60]}",
                    published_at=None,
                    score=round(1.0 - i * (0.5 / max(max_results, 1)), 3),
                )
            )
        return hits
