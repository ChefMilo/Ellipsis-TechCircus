"""Wrap WS5's result in the client envelope WS2 renders.

WS5 produces a `ClaimExtractionResult` (claims + stats + provenance). WS2's renderer
only accepts an `AnalysisResponse`, and its `isAnalysisResponse()` guard drops anything
else on the floor. This module is the seam between the two — pure, no I/O.

Assessments are `None` for now: WS6 (claim assessment) does not exist yet. WS2 handles
that natively, rendering an unassessed claim with its "not yet verified" treatment and
falling back to `claim.evidence` for sources, so the demo still shows real claims with
real retrieved evidence.
"""
from __future__ import annotations

from app.models.contract import (
    AnalysisResponse,
    AnalysisStatus,
    ArticleVerdict,
    ArticleVerdictLevel,
    ClaimExtractionResult,
    VerifiedClaim,
)

# WS2's ArticleVerdictLevel union has no "unrated"/"pending" member, and inventing one
# would fail its guard. OK is the least-wrong value in the union: CAUTION and HIGH_RISK
# assert risk nobody has measured, and TRUSTED is reserved for Tier 0 whitelisted
# domains. The summary carries the honest caveat — WS2 renders it at the top of the
# panel. Revisit the moment WS6 can produce a real rollup.
PENDING_VERDICT_LEVEL = ArticleVerdictLevel.OK
PENDING_VERDICT_SUMMARY = "Claim verdicts pending - assessment stage not yet enabled."


def pending_verdict() -> ArticleVerdict:
    """The placeholder article-level verdict used until WS6 lands."""
    return ArticleVerdict(level=PENDING_VERDICT_LEVEL, summary=PENDING_VERDICT_SUMMARY)


def to_analysis_response(
    result: ClaimExtractionResult,
    *,
    status: AnalysisStatus | str = AnalysisStatus.COMPLETE,
) -> AnalysisResponse:
    """Adapt a `ClaimExtractionResult` into the `AnalysisResponse` WS2 expects.

    Claim order is preserved (WS5 already ranks them, rank 1 first) because WS2's panel
    sorts by `claim.rank` and the pill reports counts in this order.
    """
    return AnalysisResponse(
        schemaVersion="1.0",
        url=result.url,
        status=AnalysisStatus(status),
        articleVerdict=pending_verdict(),
        verifiedClaims=[VerifiedClaim(claim=claim, assessment=None) for claim in result.claims],
    )
