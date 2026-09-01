"""FastAPI surface for WS5.

WS5 exposes ONE endpoint — claim extraction + evidence retrieval for a single article.
The top-level /analyze orchestration (Tier 0-3 routing) belongs to WS3; when it exists,
WS3 can either call this endpoint or import `run_ws5` directly. Keeping WS5 independently
runnable means it can be demoed and tested on its own.
"""
from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings
from app.models.contract import ArticleInput, ClaimExtractionResult
from app.pipeline.ws5 import run_ws5

app = FastAPI(
    title="Dasfax WS5 — Claim Extraction & Evidence Retrieval",
    version="0.1.0",
    summary="Tier 3 (first half): extracts load-bearing factual claims and retrieves evidence.",
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
