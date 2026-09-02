"""Wrap Tier 3's result in the client envelope WS2 renders.

WS5 produces a `ClaimExtractionResult` (claims + stats + provenance) and WS6 produces an
`Assessment` per claim. WS2's renderer only accepts an `AnalysisResponse`, and its
`isAnalysisResponse()` guard drops anything else on the floor. This module is the seam —
pure, no I/O.

It also computes the article-level rollup from the real verdicts. Two rules matter there:

  1. Never report OK unless something was actually supported. An unverifiable page must
     not look like a clean bill of health.
  2. OK is a Tier 3 result only. It means "claims were checked and at least one holds
     up", and `article_verdict_for()` is the only thing that may produce it. Any path
     that stops before Tier 3 — Tier 2 screened and cleared, no article text, no
     checkable claims — reports UNRATED, with the reason in `ArticleVerdict.summary`.
     So does a Tier 3 run in which nothing could be verified either way: UNRATED says
     "not checked", CAUTION says "something looks off", and only the second is a warning.
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

# UNRATED is WS2's neutral level for "there was nothing here to fact-check" — no article
# text, zero checkable claims, or a Tier 2 pass that cleared the page without escalating.
# OK would read as a clean bill of health for a page nothing was verified about; CAUTION
# would read as a warning about an innocent non-article page; TRUSTED is reserved for
# Tier 0 whitelisted domains. Callers pass their own `summary` to say which case it is.
PENDING_VERDICT_LEVEL = ArticleVerdictLevel.UNRATED
PENDING_VERDICT_SUMMARY = "There was nothing on this page to fact-check."

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


def unrated_verdict(summary: str = PENDING_VERDICT_SUMMARY) -> ArticleVerdict:
    """The neutral verdict, for any path that ends before a Tier 3 rollup.

    Pass `summary` to say why nothing was fact-checked (no text, no checkable claims,
    Tier 2 cleared it). The default covers the plain "nothing to rate" case.
    """
    return ArticleVerdict(level=PENDING_VERDICT_LEVEL, summary=summary)


# Legacy name kept so existing imports keep working; prefer unrated_verdict().
pending_verdict = unrated_verdict


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
    A page whose claims are all unverified or all opinion is UNRATED, never OK — saying
    "looks fine" about something nobody could check is the one failure mode that would
    actively mislead a reader. UNRATED rather than CAUTION because "we could not check
    this" is not the same statement as "something here looks wrong", and CAUTION on a page
    with nothing against it is a false alarm.
    """
    counts = Counter(a.status for a in assessments)
    if not counts.total():
        return unrated_verdict()

    summary = _summarize(counts)

    if counts[AssessmentStatus.CONTRADICTED]:
        level = ArticleVerdictLevel.HIGH_RISK
    elif counts[AssessmentStatus.PARTIALLY_SUPPORTED]:
        level = ArticleVerdictLevel.CAUTION
    elif counts[AssessmentStatus.SUPPORTED]:
        level = ArticleVerdictLevel.OK
    else:
        # Only NEEDS_REVIEW and/or OPINION remain: nothing was supported, but nothing was
        # disputed either. Neutral, with the reason spelled out in the summary.
        level = ArticleVerdictLevel.UNRATED
        summary = f"{UNVERIFIED_SUMMARY} ({summary})"

    return ArticleVerdict(level=level, summary=summary)


def to_analysis_response(
    result: ClaimExtractionResult,
    *,
    status: AnalysisStatus | str = AnalysisStatus.COMPLETE,
    assessments: dict[str, Assessment] | None = None,
    article_verdict: ArticleVerdict | None = None,
) -> AnalysisResponse:
    """Adapt a `ClaimExtractionResult` (+ optional WS6 assessments) into the
    `AnalysisResponse` WS2 expects.

    `assessments` is keyed by `claim.id`. Omitting it keeps the pre-WS6 behaviour —
    every claim unassessed and the placeholder verdict — which is still what callers
    that only run WS5 should get.

    `article_verdict` overrides the rollup entirely. It is for callers that decided the
    verdict outside Tier 3 — e.g. WS4 handing back an UNRATED envelope for a page it
    screened and did not escalate. When given, `assessments` is still used for the
    per-claim data but not for the article level.

    Claim order is preserved (WS5 already ranks them, rank 1 first) because WS2's panel
    sorts by `claim.rank` and the pill reports counts in this order.
    """
    by_id = assessments or {}
    verified = [
        VerifiedClaim(claim=claim, assessment=by_id.get(claim.id)) for claim in result.claims
    ]

    if article_verdict is not None:
        verdict = article_verdict
    elif by_id:
        verdict = article_verdict_for(
            [vc.assessment for vc in verified if vc.assessment is not None]
        )
    else:
        verdict = unrated_verdict()

    return AnalysisResponse(
        schemaVersion="1.0",
        url=result.url,
        status=AnalysisStatus(status),
        articleVerdict=verdict,
        verifiedClaims=verified,
    )
