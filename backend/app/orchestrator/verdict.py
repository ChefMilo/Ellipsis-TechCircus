"""Pure article-level verdict rollup for WS3's /analyze envelope.

Deliberately independent of app/services/envelope.py's `article_verdict_for()` (out of
scope to modify -- backend/app/services/ is WS5/WS6 territory) and deliberately a
DIFFERENT rule from it: this uses status COUNTS only, not "OK requires at least one
literally-SUPPORTED claim" the way services/envelope.py's does. That's fine -- the two
serve different callers (direct WS5-only callers of `to_analysis_response()` vs this
orchestrator's /analyze) and are allowed to diverge; see docs/ws3/WS3-ANALYZE-ENVELOPE.md for
the full reasoning, including the one edge case where the two rules could disagree
(a page whose only checkable claims are OPINION) and why that edge case does not
actually occur through the real pipeline today.

No I/O, no settings, no logging, no FastAPI/Pydantic request objects -- pure function
of the assessments it's given, so it's testable with plain hand-built `Assessment`
objects and nothing else.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.models.contract import ArticleVerdict, ArticleVerdictLevel, Assessment, AssessmentStatus

UNRATED_SUMMARY = "Claims were extracted but have not been assessed yet."

_SUMMARY = {
    ArticleVerdictLevel.HIGH_RISK: "At least one claim on this page is contradicted by retrieved sources.",
    ArticleVerdictLevel.CAUTION: (
        "This page has claims that are only partially supported, or that could not be "
        "conclusively checked."
    ),
    ArticleVerdictLevel.OK: "The claims checked on this page are supported (or are opinions, not factual claims).",
}


def compute_article_verdict(assessments: Iterable[Assessment | None]) -> ArticleVerdict:
    """Roll a set of per-claim Assessments (one per VerifiedClaim; `None` where that
    claim was never assessed) up into the single article-level verdict WS2's summary
    pill renders. Never returns a bare true/false -- always the 3-field
    (level, summary, confidence) object, per the task's explicit "never emit a binary
    true/fake verdict at article level" rule.

    Rule, exactly as specified:
      any CONTRADICTED                              -> high_risk (highest severity)
      else any PARTIALLY_SUPPORTED or NEEDS_REVIEW   -> caution   (mixed)
      else                                            -> ok        (clean)
    ...with one override that takes priority over all three: if every assessment is
    `None` -- no claims were assessed at all, whether because there are zero claims or
    because WS6 was never invoked for this response -- the result is UNRATED with
    confidence `None`. This is the exact shape `AnalysisResponse`'s pre-WS6 / no-content
    paths already produce, and the exact shape src/shared/contract.test.ts's "accepts
    the unrated verdict level" test already proves the guard accepts (matched exactly,
    per the task).

    `confidence` for the other three branches is the fraction of assessed claims whose
    status drove that branch's decision (e.g. for high_risk: contradicted / total
    assessed), rounded to 2dp. One honest edge case: a page whose only assessed claims
    are OPINION (no SUPPORTED, no problems either) falls into the "ok" branch with
    confidence 0.0 -- correct in spirit (nothing was actually verified as true, only
    found to be non-factual) but worth knowing about. This does not occur through the
    real pipeline today: WS5's `rank_factual()` (app/pipeline/ws5.py, out of scope to
    modify) drops every non-FACTUAL claim before it is ever ranked, so `Claim` objects
    -- and therefore `Assessment`s -- are never built for opinions in practice; see
    backend/tests/test_demo_fixture.py's own docstring for the same observation.
    """
    decided = [a for a in assessments if a is not None]

    if not decided:
        return ArticleVerdict(
            level=ArticleVerdictLevel.UNRATED, summary=UNRATED_SUMMARY, confidence=None
        )

    statuses = [a.status for a in decided]
    total = len(decided)

    contradicted = statuses.count(AssessmentStatus.CONTRADICTED)
    if contradicted:
        return ArticleVerdict(
            level=ArticleVerdictLevel.HIGH_RISK,
            summary=_SUMMARY[ArticleVerdictLevel.HIGH_RISK],
            confidence=round(contradicted / total, 2),
        )

    mixed = statuses.count(AssessmentStatus.PARTIALLY_SUPPORTED) + statuses.count(
        AssessmentStatus.NEEDS_REVIEW
    )
    if mixed:
        return ArticleVerdict(
            level=ArticleVerdictLevel.CAUTION,
            summary=_SUMMARY[ArticleVerdictLevel.CAUTION],
            confidence=round(mixed / total, 2),
        )

    return ArticleVerdict(
        level=ArticleVerdictLevel.OK,
        summary=_SUMMARY[ArticleVerdictLevel.OK],
        confidence=round(statuses.count(AssessmentStatus.SUPPORTED) / total, 2),
    )
