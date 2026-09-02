"""FastAPI surface for WS5 and WS3.

Two endpoints, deliberately layered:

  POST /tier3/claims  — WS5's own output (`ClaimExtractionResult`): claims, stats,
                        provenance. The shape WS6 will consume. Unchanged.
  POST /analyze       — the CLIENT-facing envelope (`AnalysisResponse`) the extension
                        renders. Now owned by WS3's orchestrator
                        (app/orchestrator/pipeline.py, registered here via
                        app/api/analyze.py's router) rather than defined inline in this
                        file — see docs/ws3/WS3-ANALYZE-ENVELOPE.md.

Splitting them keeps WS5's result honest (stats/provenance stay visible for the pitch)
while giving WS2 exactly the shape its guard accepts.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analyze import router as analyze_router
from app.config import get_settings
from app.models.contract import ArticleInput, ClaimExtractionResult
from app.pipeline.ws5 import run_ws5

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


app.include_router(analyze_router)
