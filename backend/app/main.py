"""FastAPI surface for the Dasfax backend.

Two tiers are exposed independently so each workstream is demoable on its own:

    POST /tier2/screen  (alias /screen)  — WS4, Tier 2 ML screening -> escalate or stop
    POST /tier3/claims                   — WS5, claim extraction + evidence retrieval

The top-level /analyze orchestration (Tier 0-3 routing) belongs to WS3; it can call these
endpoints or import `run_ws4` / `run_ws5` directly.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.models.analysis import AnalysisResponse
from app.models.contract import ArticleInput, ClaimExtractionResult
from app.models.screening import ScreeningInput, ScreeningResult
from app.pipeline.analyze import run_analysis
from app.pipeline.ws4 import run_ws4
from app.pipeline.ws5 import run_ws5

log = logging.getLogger("dasfax.startup")

# Populated at startup so /health can report whether the real models are actually live.
_WARMUP_STATUS: list[str] = []


def _warm_screening_models() -> list[str]:
    """Load the Tier 2 models and run one forward pass each.

    Eager, not lazy, and one real inference rather than just a load: torch allocates and
    specialises on the first forward pass, so a lazy backend puts 2-10 seconds into the
    FIRST user request's latency_ms. In a live demo that is the request the judges watch,
    and it also poisons any p95 measurement taken afterwards.

    A failure here is reported, never raised — the tier degrades to the heuristic backends
    rather than taking the whole service down.
    """
    from app.clients.factory import make_image_scorer, make_text_scorer

    settings = get_settings()
    status: list[str] = []
    for label, build in (("text", make_text_scorer), ("image", make_image_scorer)):
        try:
            scorer = build(settings)
            scorer.warmup()
            status.append(f"{label}: {scorer.name}")
        except Exception as exc:
            status.append(f"{label}: FAILED ({exc.__class__.__name__}: {exc})")
    return status


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.warmup and settings.screening_mode != "heuristic":
        _WARMUP_STATUS.extend(_warm_screening_models())
        for line in _WARMUP_STATUS:
            log.info("WS4 warmup — %s", line)
    else:
        _WARMUP_STATUS.append(f"skipped (mode={settings.screening_mode}, warmup={settings.warmup})")
    yield


app = FastAPI(
    title="Dasfax backend — Tier 2 screening & Tier 3 claim extraction",
    version="0.2.0",
    summary="WS4: fast ML screening that decides what is worth escalating. WS5: claims + evidence.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "ws": "WS5",
        "llm_provider": s.llm_provider,
        "search_provider": s.search_provider,
        "screening_mode": s.screening_mode,
        "text_threshold": s.text_threshold,
        "image_threshold": s.image_threshold,
        # Which backends are actually live. A degraded tier must be visible here, not
        # only discoverable by reading `reasons` on an individual response.
        "screening_backends": list(_WARMUP_STATUS),
    }


# NOT response_model_exclude_none: `VerifiedClaim.assessment` must serialise as an
# explicit `null`, because WS2's `isVerifiedClaim` guard tests `assessment === null`.
# Dropping it would make the guard reject every claim we return. The TS optionals are
# widened to `| null` instead — see src/shared/contract.ts.
@app.post("/analyze", response_model=AnalysisResponse)
def analyze(page: ScreeningInput) -> AnalysisResponse:
    """The single call the extension makes. Tier 2 screens; escalated pages go to Tier 3.

    Returns the `AnalysisResponse` envelope defined in src/shared/contract.ts — the content
    script validates every reply against it, so drift surfaces immediately as a malformed
    response rather than a silent mis-render.

    WS3 owns this seam; see app/pipeline/analyze.py for what it deliberately does not do.
    """
    return run_analysis(page)


@app.post("/tier2/screen", response_model=ScreeningResult)
def screen(page: ScreeningInput) -> ScreeningResult:
    """Tier 2: score article text and images in parallel, decide whether to escalate.

    Input: an article-like page that already passed WS1's Tier 0/Tier 1 triage.
    Output: ScreeningResult — WS3 routes on `escalate_to_tier3`. Nothing here is a
    verdict; it is a decision about where to spend Tier 3 budget.
    """
    return run_ws4(page)


# Alias: the proposal names this endpoint /screen. Same handler, one implementation.
app.add_api_route(
    "/screen",
    screen,
    methods=["POST"],
    response_model=ScreeningResult,
    include_in_schema=False,
)


@app.post("/tier3/claims", response_model=ClaimExtractionResult)
def extract_claims(article: ArticleInput) -> ClaimExtractionResult:
    """Extract ranked, checkable claims with retrieved evidence for one article.

    Input: cleaned article text (produced upstream by WS1 extraction / routed by WS3).
    Output: ClaimExtractionResult — the exact shape WS6 consumes.
    """
    return run_ws5(article)
