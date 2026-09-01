"""FastAPI surface for WS5.

Two endpoints, deliberately layered:

  POST /tier3/claims  — WS5's own output (`ClaimExtractionResult`): claims, stats,
                        provenance. The shape WS6 will consume. Unchanged.
  POST /analyze       — the CLIENT-facing envelope (`AnalysisResponse`) the extension
                        renders: /tier3/claims run through WS6 assessment and wrapped for
                        WS2. This is what the service worker fetches.

Splitting them keeps WS5's result honest (stats/provenance stay visible for the pitch)
while giving WS2 exactly the shape its guard accepts. Tier 0/1 routing still lives in the
extension; when a fuller WS3 orchestrator appears it can own /analyze or call `run_ws5`
directly.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients.factory import make_assessor_client
from app.config import get_settings
from app.models.contract import (
    AnalysisError,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisStatus,
    ArticleInput,
    ClaimExtractionResult,
)
from app.pipeline.ws5 import run_ws5
from app.services.assessment import assess_claims
from app.services.envelope import pending_verdict, to_analysis_response

app = FastAPI(
    title="Dasfax WS5 — Claim Extraction & Evidence Retrieval",
    version="0.1.0",
    summary="Tier 3 (first half): extracts load-bearing factual claims and retrieves evidence.",
)

# The extension's service worker fetches this from a chrome-extension:// origin, which is
# cross-origin, so without CORS the browser drops the response before Terry's code sees it.
# DEV ONLY: tighten `allow_origins` to the published extension origin
# ("chrome-extension://<id>") before this is exposed anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "ws": "WS5",
        "llm_provider": s.llm_provider,
        "search_provider": s.search_provider,
    }


@app.post("/tier3/claims", response_model=ClaimExtractionResult)
def extract_claims(article: ArticleInput) -> ClaimExtractionResult:
    """Extract ranked, checkable claims with retrieved evidence for one article.

    Input: cleaned article text (produced upstream by WS1 extraction / routed by WS3).
    Output: ClaimExtractionResult — the exact shape WS6 consumes.
    """
    return run_ws5(article)


def _domain_of(url: str) -> str | None:
    """Hostname for `ArticleInput.source_domain`, so retrieval can drop self-citations."""
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


@app.post("/analyze", response_model=AnalysisResponse)
def analyze(request: AnalysisRequest) -> AnalysisResponse:
    """Client-facing analysis for one page — the endpoint the extension calls.

    Returns the `AnalysisResponse` envelope WS2 renders, with a WS6 assessment per claim
    and an article-level verdict rolled up from those assessments.

    A request with no body text is NOT an error: it answers with a valid, empty envelope
    so WS3 can wire and exercise the fetch before WS1 forwards extracted text. This path
    must never 5xx — a broken envelope means WS2's guard silently drops the response.
    """
    if not (request.text or "").strip():
        return AnalysisResponse(
            schemaVersion="1.0",
            url=request.url,
            # Not SKIPPED: WS2 reads that as "Tier 0 trusted source" and paints the
            # trusted pill. COMPLETE with zero claims renders "No checkable claims found".
            status=AnalysisStatus.COMPLETE,
            articleVerdict=pending_verdict(),
            verifiedClaims=[],
            errors=[
                AnalysisError(
                    code="no_content",
                    message="No article text supplied; nothing to analyze.",
                )
            ],
        )

    # Pass `text` through verbatim (not stripped): WS5's claim anchor offsets index this
    # exact string, so trimming it here would shift every char_start/char_end by the
    # length of the leading whitespace.
    article = ArticleInput(
        url=request.url,
        title=request.title,
        text=request.text,
        source_domain=_domain_of(request.url),
    )
    result = run_ws5(article)

    # WS6: judge each claim against the evidence WS5 retrieved for it. Mock-backed by
    # default (no API key), same as extraction and search. The backend name goes into
    # model_meta alongside the others so provenance stays honest — the default
    # "mock-assessor-stance-fixture-v1" is a rendering fixture, not real assessment.
    assessor = make_assessor_client()
    assessments = assess_claims(result.claims, client=assessor)
    result.model_meta["assessor_backend"] = assessor.name
    return to_analysis_response(
        result, status=AnalysisStatus.COMPLETE, assessments=assessments
    )
