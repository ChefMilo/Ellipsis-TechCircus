"""Wrap Tier 3's result in the client envelope WS2 renders.

WS5 produces a `ClaimExtractionResult` (claims + stats + provenance) and WS6 produces an
`Assessment` per claim. WS2's renderer only accepts an `AnalysisResponse`, and its
`isAnalysisResponse()` guard drops anything else on the floor. This module is the seam —
pure, no I/O.

It also computes the article-level rollup from the real verdicts. The one rule that
matters there: never report OK unless something was actually supported. An unverifiable
page must not look like a clean bill of health.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.models.contract import (
    AnalysisResponse,
    AnalysisStatus,
    ArticleVerdict,
    ArticleVerdictLevel,
    Assessment,
    AssessmentStatus,
    ClaimExtractionResult,
    VerifiedClaim,
)

# WS2's ArticleVerdictLevel union has no "unrated"/"pending" member, and inventing one
# would fail its guard (a dedicated level is being requested from WS2 separately). Until
# then CAUTION is the honest stand-in for "we cannot rate this": OK would read as a clean
# bill of health for a page nothing is known about, and TRUSTED is reserved for Tier 0
# whitelisted domains.
PENDING_VERDICT_LEVEL = ArticleVerdictLevel.CAUTION
PENDING_VERDICT_SUMMARY = "Nothing on this page has been verified."

UNVERIFIED_SUMMARY = "No claim on this page could be verified against retrieved sources."

# Order used when wording the rollup, worst first — the first clause is what the user
# most needs to see.
_SUMMARY_LABELS: tuple[tuple[AssessmentStatus, str], ...] = (
    (AssessmentStatus.CONTRADICTED, "contradicted"),
    (AssessmentStatus.PARTIALLY_SUPPORTED, "partially supported"),
    (AssessmentStatus.SUPPORTED, "supported"),
    (AssessmentStatus.NEEDS_REVIEW, "unverified"),
    (AssessmentStatus.OPINION, "opinion"),
)


def pending_verdict() -> ArticleVerdict:
    """Verdict used when there is nothing to rate at all (no text, or no claims)."""
    return ArticleVerdict(level=PENDING_VERDICT_LEVEL, summary=PENDING_VERDICT_SUMMARY)


def _summarize(counts: Counter[AssessmentStatus]) -> str:
    """e.g. "1 claim contradicted, 2 supported"."""
    parts: list[str] = []
    for status, label in _SUMMARY_LABELS:
        n = counts.get(status, 0)
        if not n:
            continue
        # Only the first clause carries the noun, so the whole reads as one sentence.
        noun = f" claim{'s' if n != 1 else ''}" if not parts else ""
        parts.append(f"{n}{noun} {label}")
    return ", ".join(parts) + "." if parts else PENDING_VERDICT_SUMMARY


def article_verdict_for(assessments: Iterable[Assessment]) -> ArticleVerdict:
    """Roll per-claim verdicts up into the article-level verdict WS2's pill shows.

    Worst-wins, with one hard floor: OK requires at least one genuinely SUPPORTED claim.
    A page whose claims are all unverified or all opinion is CAUTION, never OK — saying
    "looks fine" about something nobody could check is the one failure mode that would
    actively mislead a reader.
    """
    counts = Counter(a.status for a in assessments)
    if not counts.total():
        return pending_verdict()

    summary = _summarize(counts)

    if counts[AssessmentStatus.CONTRADICTED]:
        level = ArticleVerdictLevel.HIGH_RISK
    elif counts[AssessmentStatus.PARTIALLY_SUPPORTED]:
        level = ArticleVerdictLevel.CAUTION
    elif counts[AssessmentStatus.SUPPORTED]:
        level = ArticleVerdictLevel.OK
    else:
        # Only NEEDS_REVIEW and/or OPINION remain.
        level = ArticleVerdictLevel.CAUTION
        summary = f"{UNVERIFIED_SUMMARY} ({summary})"

    return ArticleVerdict(level=level, summary=summary)


def to_analysis_response(
    result: ClaimExtractionResult,
    *,
    status: AnalysisStatus | str = AnalysisStatus.COMPLETE,
    assessments: dict[str, Assessment] | None = None,
) -> AnalysisResponse:
    """Adapt a `ClaimExtractionResult` (+ optional WS6 assessments) into the
    `AnalysisResponse` WS2 expects.

    `assessments` is keyed by `claim.id`. Omitting it keeps the pre-WS6 behaviour —
    every claim unassessed and the placeholder verdict — which is still what callers
    that only run WS5 should get.

    Claim order is preserved (WS5 already ranks them, rank 1 first) because WS2's panel
    sorts by `claim.rank` and the pill reports counts in this order.
    """
    by_id = assessments or {}
    verified = [
        VerifiedClaim(claim=claim, assessment=by_id.get(claim.id)) for claim in result.claims
    ]

    verdict = (
        article_verdict_for([vc.assessment for vc in verified if vc.assessment is not None])
        if by_id
        else pending_verdict()
    )

    return AnalysisResponse(
        schemaVersion="1.0",
        url=result.url,
        status=AnalysisStatus(status),
        articleVerdict=verdict,
        verifiedClaims=verified,
    )
