"""WS3's /analyze orchestrator entry point: Tier 2 (flagged, off by default) -> Tier 3
-> assembled client envelope.

Pure orchestration. `run_ws5()` (WS5) and `assess_claims()` (WS6) are called exactly as
they're defined elsewhere in this codebase -- as library functions -- and are never
reached into, refactored, or "improved" here. Every failure mode this module knows
about (no article text, Tier 3 raising, Tier 3 exceeding its timeout) is converted into
a valid `AnalysisResponse`; `run_analysis()` does not raise for any of them. See
app/api/analyze.py's module docstring for the one further layer of defense above this.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from app.clients.factory import make_assessor_client
from app.models.contract import (
    AnalysisError,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisStatus,
    ArticleInput,
    ArticleVerdict,
    ArticleVerdictLevel,
    VerifiedClaim,
)
from app.pipeline.ws5 import run_ws5
from app.services.assessment import assess_claims

from . import tier2
from .cache import InMemoryTTLCache, cache_key
from .config import OrchestratorSettings, get_orchestrator_settings
from .verdict import compute_article_verdict

NO_CONTENT_SUMMARY = "No article text was supplied, so nothing was fact-checked."
TIER3_TIMEOUT_SUMMARY = "The fact-check pipeline took too long, so this page was not checked."
TIER3_FAILURE_SUMMARY = "The fact-check pipeline hit an internal error, so this page was not checked."

# One process-wide cache instance, lazily created -- exactly like a real deployment
# would hold one. This is the cache's OWN intentional persistent state (that is the
# entire point of Task 5), not "request state" left lying around between an individual
# request and its response -- contrast with the per-request-state warning in
# docs/ws3/WS3-MESSAGE-HOP.md, which is about a different runtime (an MV3 service worker that
# Chrome can kill and restart mid-request) that this backend process is not. Tests
# never touch this singleton: they inject their own `InMemoryTTLCache` via
# `run_analysis(..., cache=...)` so cache state can never leak between tests.
_cache: InMemoryTTLCache[AnalysisResponse] | None = None


def _get_cache(settings: OrchestratorSettings) -> InMemoryTTLCache[AnalysisResponse]:
    global _cache
    if _cache is None:
        _cache = InMemoryTTLCache(ttl_s=settings.cache_ttl_s)
    return _cache


def _domain_of(url: str) -> str | None:
    """Hostname for ArticleInput.source_domain, derived server-side from the canonical
    url. Verbatim behaviour of the helper this replaces (previously app/main.py's own
    module-level `_domain_of`, before /analyze moved into this orchestrator) --
    `AnalysisRequest.source_domain` (the client's own guess, added in this same branch)
    is intentionally NOT used here; see docs/ws3/WS3-ANALYZE-ENVELOPE.md task 2 for why."""
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _no_content_response(request: AnalysisRequest) -> AnalysisResponse:
    return AnalysisResponse(
        schemaVersion="1.0",
        url=request.url,
        # "complete", not a dedicated "no_content" status: ANALYSIS_STATUSES in
        # src/shared/contract.ts is a CLOSED set (docs/ws3/WS3-ANALYZE-ENVELOPE.md Step 0) that
        # does not include one. "complete" + UNRATED + errors[] is the pre-existing,
        # already-guard-valid pattern this preserves exactly (message text included).
        status=AnalysisStatus.COMPLETE,
        articleVerdict=ArticleVerdict(
            level=ArticleVerdictLevel.UNRATED, summary=NO_CONTENT_SUMMARY, confidence=None
        ),
        verifiedClaims=[],
        errors=[
            AnalysisError(code="no_content", message="No article text supplied; nothing to analyze.")
        ],
    )


def _failure_response(request: AnalysisRequest, *, code: str, message: str) -> AnalysisResponse:
    summary = TIER3_TIMEOUT_SUMMARY if code == "tier3_timeout" else TIER3_FAILURE_SUMMARY
    return AnalysisResponse(
        schemaVersion="1.0",
        url=request.url,
        status=AnalysisStatus.FAILED,
        articleVerdict=ArticleVerdict(
            level=ArticleVerdictLevel.UNRATED, summary=summary, confidence=None
        ),
        verifiedClaims=[],
        errors=[AnalysisError(code=code, message=message)],
    )


def _build_article_input(request: AnalysisRequest) -> ArticleInput:
    """Adapt WS3's inbound `AnalysisRequest` into WS5's `ArticleInput`. Pulled out as
    its own function specifically so a test can assert `published_at` (and every other
    field) survives the conversion without needing to monkeypatch `run_ws5` -- see
    docs/ws3/WS3-ANALYZE-ENVELOPE.md task 2 and test_orchestrator_pipeline.py."""
    return ArticleInput(
        url=request.url,
        title=request.title,
        # `text` here is the ORIGINAL request.text, not stripped: WS5's claim
        # char_start/char_end offsets index ArticleInput.text verbatim
        # (app/services/anchoring.py), so trimming would shift every offset by the
        # length of any leading whitespace. Same rule bootstrap.ts follows client-side
        # (docs/ws3/WS3-MESSAGE-HOP.md).
        text=request.text,
        source_domain=_domain_of(request.url),
        # Threaded through per docs/ws3/WS3-ANALYZE-ENVELOPE.md task 2 -- previously dropped
        # silently by extra="ignore" (docs/ws3/WS3-CONTRACT-AUDIT.md Task C;
        # docs/ws3/WS3-MESSAGE-HOP.md task 6).
        published_at=request.published_at,
    )


def _run_tier3_sync(article: ArticleInput) -> AnalysisResponse:
    """Everything Tier 3 does for one article: WS5 extraction, then WS6 assessment,
    assembled into a full envelope with a real verdict. Synchronous by design -- run
    off the event loop by the caller (run_analysis, via asyncio.to_thread) so it can be
    time-boxed with asyncio.wait_for."""
    result = run_ws5(article)  # WS5, called as a library function -- never modified

    assessor = make_assessor_client()
    try:
        assessments = assess_claims(result.claims, client=assessor)
    except Exception:
        # Extraction (WS5) succeeded but assessment (WS6) itself blew up. Still return
        # the claims WS5 found rather than failing the whole request -- every claim
        # just keeps assessment: None, which compute_article_verdict() reports as
        # UNRATED, an honest reflection of "extracted, not assessed".
        assessments = {}

    verified = [VerifiedClaim(claim=c, assessment=assessments.get(c.id)) for c in result.claims]
    verdict = compute_article_verdict(vc.assessment for vc in verified)

    return AnalysisResponse(
        schemaVersion="1.0",
        url=result.url,
        status=AnalysisStatus.COMPLETE,
        articleVerdict=verdict,
        verifiedClaims=verified,
        errors=[],
    )


async def run_analysis(
    request: AnalysisRequest,
    *,
    settings: OrchestratorSettings | None = None,
    cache: InMemoryTTLCache[AnalysisResponse] | None = None,
) -> AnalysisResponse:
    """WS3's orchestrator entry point: no-content short-circuit -> cache lookup ->
    Tier 2 (flagged, off by default) -> Tier 3 (WS5 + WS6, time-boxed) -> cache store
    -> return. Never raises: every failure mode this function knows about is converted
    to a valid AnalysisResponse (see app/api/analyze.py for the outermost safety net
    covering everything this function does NOT know about).

    Only successful ("complete") results are cached -- a transient Tier 3 failure is
    never cached, so a retry after the backend recovers is not stuck replaying the old
    error for the cache's TTL.
    """
    settings = settings or get_orchestrator_settings()
    cache = cache if cache is not None else _get_cache(settings)

    text = (request.text or "").strip()
    if not text:
        return _no_content_response(request)

    key = cache_key(request.url, text)
    cached = cache.get(key)
    if cached is not None:
        return cached

    # Off-thread, and time-boxed. tier2.screen() is synchronous and does network I/O
    # (image fetches) inside WS4's own budget, so calling it inline would block the event
    # loop for the length of that budget on every request. The wait_for is belt-and-braces
    # over WS4's internal deadline: a screener that hung past its own budget would
    # otherwise stall a request that Tier 3 could have answered.
    try:
        tier2_verdict = await asyncio.wait_for(
            asyncio.to_thread(
                tier2.screen,
                url=request.url,
                title=request.title,
                text=text,
                image_urls=request.images,
                tier2_enabled=settings.tier2_enabled,
            ),
            timeout=settings.tier2_timeout_s,
        )
    except Exception:  # noqa: BLE001 -- fail open; TimeoutError is covered here too
        # Tier 2 is an optimisation. Anything it does wrong costs Tier 3 budget; nothing
        # it does wrong may cost the reader a fact-check. See tier2.py's module docstring.
        tier2_verdict = None

    if tier2_verdict is not None:
        # Tier 2 cleared the page: Tier 3 never runs, and the envelope says UNRATED with
        # the reason in `summary` -- never OK, which is reserved for a real Tier 3 rollup.
        response = AnalysisResponse(
            schemaVersion="1.0",
            url=request.url,
            status=AnalysisStatus.COMPLETE,
            articleVerdict=tier2_verdict,
            verifiedClaims=[],
            errors=[],
        )
        cache.set(key, response)
        return response

    article = _build_article_input(request)

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_run_tier3_sync, article), timeout=settings.tier3_timeout_s
        )
    except TimeoutError:
        return _failure_response(
            request,
            code="tier3_timeout",
            message=f"Tier 3 exceeded its {settings.tier3_timeout_s}s budget.",
        )
    except Exception as err:  # noqa: BLE001 -- resilience: Tier 3 must never 500 the caller
        return _failure_response(request, code="tier3_error", message=f"Tier 3 raised: {err}")

    cache.set(key, response)
    return response
