"""Deterministic mock search client.

Fabricates plausible, claim-relevant evidence snippets from a small pool of fake
Singapore-relevant "sources", seeded by the query so results are stable across runs.
Runs with no API key. Swap in the real Tavily client (same interface) later.

The snippets are obviously synthetic (domains under example.org) so nobody mistakes
mock evidence for real citations in the demo.

DEMO MODE (DASFAX_MOCK_DEMO=1, off by default) additionally fixes the stance PATTERN per
claim, cycling through four profiles so a live offline run visibly exercises every
assessable WS6 status instead of whatever the hash happens to produce. It is a
presentation aid for a fixture that is already a fixture: the stances are assigned by
claim position, not by anything about the claim. Default behaviour is untouched.
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


# Stance prefixes. mock_assessor reads these back; keep the wording in sync with
# app/clients/mock_assessor.py (its tests assert the round trip).
_SUPPORT = "Records indicate that"
_CORROBORATE = "Reporting corroborates that"
_DISPUTE = "One analysis disputes that"
_NEUTRAL = None  # no recognisable stance -> the assessor honestly returns needs_review

_DEFAULT_STANCES = (_SUPPORT, _CORROBORATE, _DISPUTE)

# One profile per claim, cycled in order. Chosen so the four assessable statuses all
# appear within the first four claims of any demo article:
_DEMO_PROFILES: tuple[tuple[str | None, ...], ...] = (
    (_SUPPORT, _CORROBORATE, _SUPPORT),      # majority supporting  -> supported
    (_DISPUTE, _DISPUTE, _DISPUTE),          # all disputing        -> contradicted
    (_CORROBORATE, _DISPUTE, _NEUTRAL),      # one each, a real tie -> partially_supported
    (_NEUTRAL, _NEUTRAL, _NEUTRAL),          # no stance at all     -> needs_review
)


def _seed(query: str, i: int) -> int:
    h = hashlib.sha256(f"{query}::{i}".encode()).hexdigest()
    return int(h[:8], 16)


class MockSearchClient(SearchClient):
    name = "mock-search-seeded-v1"

    def __init__(self, *, demo_spread: bool = False) -> None:
        """`demo_spread` switches on the presentation profiles described in the module
        docstring. It makes the client STATEFUL (it counts queries to pick the profile
        for each successive claim), which is fine because the pipeline builds a fresh
        client per request and retrieves claims in rank order — so a given article
        still produces identical output every run."""
        self.demo_spread = demo_spread
        self._query_index = 0
        if demo_spread:
            self.name = "mock-search-demo-spread-v1"

    def _stances_for_next_claim(self) -> tuple[str | None, ...] | None:
        """The stance pattern for this query, or None to use the default hashing."""
        if not self.demo_spread:
            return None
        profile = _DEMO_PROFILES[self._query_index % len(_DEMO_PROFILES)]
        self._query_index += 1
        return profile

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        hits: list[SearchHit] = []
        q = query.strip()
        stances = self._stances_for_next_claim()
        for i in range(max_results):
            seed = _seed(q, i)
            domain, title = _FAKE_SOURCES[seed % len(_FAKE_SOURCES)]
            # Alternate stance so WS6 has both supporting- and questioning-flavoured evidence.
            stance = stances[i % len(stances)] if stances else _DEFAULT_STANCES[seed % 3]
            lead = f"{stance} " if stance else "Coverage of this topic includes: "
            snippet = (
                f"{lead}{q[:120]}. "
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
