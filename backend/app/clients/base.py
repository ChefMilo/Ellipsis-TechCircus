"""Provider-agnostic interfaces for the external dependencies Tier 3 has:
an LLM (WS5 claim extraction), a web-search API (WS5 evidence retrieval), and an
assessor (WS6 per-claim verdicts).

The pipeline depends ONLY on these Protocols, never on a concrete provider, so mock
and real are drop-in interchangeable. Concrete impls live alongside this file.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.models.contract import Evidence


class ExtractedClaim(BaseModel):
    """Raw claim as returned by the LLM extractor, before dedup/ranking/retrieval.
    Deliberately smaller than the contract's Claim — the pipeline enriches it."""

    text: str
    claim_type: str          # "factual" | "opinion" | "prediction"
    checkworthiness: float    # 0..1, model's own estimate


class SearchHit(BaseModel):
    """One raw search result from a search provider."""

    snippet: str
    url: str
    title: str | None = None
    published_at: str | None = None   # ISO string if the provider gives one
    score: float | None = None        # provider relevance, if any


@runtime_checkable
class LLMClient(Protocol):
    """Extracts candidate claims from cleaned article text."""

    name: str

    def extract_claims(self, *, title: str | None, text: str) -> list[ExtractedClaim]:
        ...


@runtime_checkable
class SearchClient(Protocol):
    """Returns web-search hits for a query."""

    name: str

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        ...


class AssessedClaim(BaseModel):
    """Raw verdict from an assessor, BEFORE the service turns it into a contract
    `Assessment`.

    Deliberately does not carry Citations: an assessor names which of the claim's own
    evidence items justify its verdict (by index) and the service binds the actual
    snippets. A provider therefore cannot invent a source — it can only point at
    evidence WS5 really retrieved.
    """

    status: str                          # an AssessmentStatus value
    evidence_indices: list[int] = []     # indices into the claim's `evidence` list
    confidence: float | None = None
    explanation: str | None = None       # the service supplies one when this is None


@runtime_checkable
class AssessorClient(Protocol):
    """Judges one claim against the evidence WS5 retrieved for it."""

    name: str

    def assess(self, claim_text: str, evidence: list[Evidence]) -> AssessedClaim:
        ...
