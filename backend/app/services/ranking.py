"""Dedup + ranking for extracted claims.

This is the piece the proposal warns people skip: a 900-word article yields 30+
extractable claims; checking them all is slow, expensive, and reads as noise. We keep
only the top few LOAD-BEARING factual claims. That is a deliberate design decision to
state in the pitch, not a limitation.
"""
from __future__ import annotations

import re

from app.clients.base import ExtractedClaim

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "but", "is",
    "are", "was", "were", "be", "been", "that", "this", "it", "as", "at", "by",
    "with", "from", "has", "have", "had", "will", "would", "said",
}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def dedup(claims: list[ExtractedClaim], *, threshold: float) -> tuple[list[ExtractedClaim], int]:
    """Greedy near-duplicate removal by token-Jaccard. Keeps the higher-checkworthiness
    member of each duplicate pair. Returns (deduped, num_dropped)."""
    kept: list[tuple[ExtractedClaim, set[str]]] = []
    dropped = 0
    # Process strongest-first so the survivor of a dup pair is the better claim.
    for claim in sorted(claims, key=lambda c: c.checkworthiness, reverse=True):
        toks = _tokens(claim.text)
        if any(_jaccard(toks, ktoks) >= threshold for _, ktoks in kept):
            dropped += 1
            continue
        kept.append((claim, toks))
    return [c for c, _ in kept], dropped


def rank_factual(
    claims: list[ExtractedClaim],
    *,
    max_claims: int,
    min_checkworthiness: float,
) -> list[ExtractedClaim]:
    """Keep only FACTUAL claims above the checkworthiness floor, sorted strongest-first,
    truncated to max_claims. Non-factual claims are handled separately by the caller."""
    factual = [
        c for c in claims
        if c.claim_type == "factual" and c.checkworthiness >= min_checkworthiness
    ]
    factual.sort(key=lambda c: c.checkworthiness, reverse=True)
    return factual[:max_claims]
