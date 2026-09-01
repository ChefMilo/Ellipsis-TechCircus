"""Tier 2 -> Tier 3 orchestration: the one call the extension makes.

WS3 OWNS THIS SEAM. This is a working placeholder so the product runs end to end today,
not a claim on the design — WS3 should extend or replace it. What it deliberately does
NOT do yet: Tier 0/1 triage (WS1 already does that in the content script and simply does
not call us), a result cache, rate limiting, or per-tier retries.

Flow:
  1. Tier 2 screens the page (WS4). Most pages stop here — that is the entire point of
     the cascade, and `tier2.reasons` says why.
  2. Escalated pages go to Tier 3 claim extraction + evidence retrieval (WS5).
  3. WS6 assessment does not exist yet, so each claim is returned with `assessment: null`.
     WS2's contract already models that as a legitimate state, and its renderer shows the
     claim without a verdict.

The status vocabulary needed one decision. `AnalysisStatus` has no value meaning "Tier 2
looked and stopped" — `skipped` is documented as Tier 0 trusted-source only. Rather than
widen an enum three workstreams parse, a non-escalated page returns `complete` with
`articleVerdict.level = "ok"` and no claims. WS2/WS3 should ratify or change that.
"""
from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.models.analysis import AnalysisError, AnalysisResponse, ArticleVerdict, Tier2Summary
from app.models.contract import ArticleInput, VerifiedClaim
from app.models.screening import ScreeningInput
from app.pipeline.ws4 import run_ws4
from app.pipeline.ws5 import run_ws5

log = logging.getLogger("dasfax.analyze")

# Above this Tier 2 text score, present the page as high risk rather than merely worth
# a look. Only affects the pill's wording, never whether Tier 3 runs.
_HIGH_RISK_TEXT_SCORE = 0.85


def _no_text_response(page: ScreeningInput) -> AnalysisResponse:
    return AnalysisResponse(
        url=page.url,
        status="failed",
        article_verdict=ArticleVerdict(
            level="ok",
            summary="Could not read this page's article text, so it was not checked.",
        ),
        errors=[AnalysisError(code="no_article_text", message="No extractable body text.")],
    )


def run_analysis(page: ScreeningInput, *, settings: Settings | None = None) -> AnalysisResponse:
    """Screen, escalate if warranted, and return the envelope the extension renders."""
    settings = settings or get_settings()

    screening = run_ws4(page, settings=settings)
    tier2 = Tier2Summary(
        escalated=screening.escalate_to_tier3,
        text_score=screening.text_score,
        max_image_score=screening.max_image_score,
        reasons=screening.reasons,
        degraded=screening.degraded,
        latency_ms=screening.latency_ms,
    )
    log.info(
        "tier2 url=%s escalate=%s text=%.3f image=%.3f %.0fms",
        page.url, screening.escalate_to_tier3, screening.text_score,
        screening.max_image_score, screening.latency_ms,
    )

    if not screening.escalate_to_tier3:
        return AnalysisResponse(
            url=page.url,
            status="complete",
            article_verdict=ArticleVerdict(
                level="ok",
                summary="No elevated risk signals found; this page was not checked in depth.",
            ),
            verified_claims=[],
            tier2=tier2,
        )

    if not page.text or not page.text.strip():
        # Escalated on images alone. There is no text for Tier 3 to extract claims from,
        # so say that rather than returning an empty claim list that reads as "all clear".
        response = _no_text_response(page)
        response.article_verdict = ArticleVerdict(
            level="caution",
            summary="An image on this page looks synthetic, but the article text could not be read.",
        )
        response.tier2 = tier2
        return response

    try:
        extraction = run_ws5(
            ArticleInput(
                url=page.url, title=page.title, text=page.text,
                lang=page.lang, source_domain=page.source_domain,
                published_at=page.published_at,
            ),
            settings=settings,
        )
    except Exception as exc:
        log.exception("tier3 failed for %s", page.url)
        return AnalysisResponse(
            url=page.url,
            status="failed",
            article_verdict=ArticleVerdict(
                level="caution",
                summary="This page was flagged for review, but the detailed check could not complete.",
            ),
            errors=[AnalysisError(code="tier3_failed", message=exc.__class__.__name__)],
            tier2=tier2,
        )

    level = "high_risk" if screening.text_score >= _HIGH_RISK_TEXT_SCORE else "caution"
    claim_count = len(extraction.claims)
    summary = (
        f"Flagged for review — {claim_count} claim{'s' if claim_count != 1 else ''} checked."
        if claim_count
        else "Flagged for review, but no clearly checkable factual claims were found."
    )
    return AnalysisResponse(
        url=page.url,
        status="complete",
        article_verdict=ArticleVerdict(level=level, summary=summary),
        # WS6 has not been built, so no claim carries a verdict yet. `assessment: null` is
        # a state WS2's contract already models.
        verified_claims=[VerifiedClaim(claim=c, assessment=None) for c in extraction.claims],
        tier2=tier2,
    )
