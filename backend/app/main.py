"""FastAPI surface for the Dasfax backend.

Three endpoints, deliberately layered so each workstream stays demoable on its own:

  POST /tier2/screen  (alias /screen)  — WS4, Tier 2 ML screening -> escalate or stop.
  POST /tier3/claims                   — WS5's own output (claims, evidence, stats).
  POST /analyze                        — the CLIENT-facing envelope WS2 renders. Owned
                                         by WS3's orchestrator (app/orchestrator/
                                         pipeline.py) and registered via the router in
                                         app/api/analyze.py, NOT defined in this file.

Tier 2 is exposed directly as well as through /analyze because it is a decision, not a
verdict: `escalate_to_tier3` is what WS3 routes on, and being able to call it alone is
what makes the cascade's cost argument measurable.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analyze import router as analyze_router
from app.config import effective_image_mode, effective_text_mode, get_settings
from app.models.contract import ArticleInput, ClaimExtractionResult
from app.models.screening import ScreeningInput, ScreeningResult
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


def _configure_app_logging() -> None:
    """Route `dasfax.*` INFO logs to wherever uvicorn is already writing.

    Uvicorn configures its own loggers and leaves application loggers alone, so without
    this every log.info() in the pipeline is dropped in silence: the root logger has no
    handler, and Python's last-resort handler only emits WARNING and above. The tier
    decision would be invisible in the one place an operator is actually looking.

    Called from lifespan rather than at import, because uvicorn installs its handlers
    after importing the app.
    """
    app_log = logging.getLogger("dasfax")
    if app_log.handlers:
        return

    # The handler lives on the "uvicorn" logger, NOT on "uvicorn.error" — the latter is
    # configured with a level only and propagates upward. Reading the wrong one finds an
    # empty list, and pairing that with propagate=False silences the logger completely.
    handlers = logging.getLogger("uvicorn").handlers or logging.getLogger("uvicorn.error").handlers

    app_log.setLevel(logging.INFO)
    if handlers:
        app_log.handlers = list(handlers)  # match uvicorn's formatting
        app_log.propagate = False          # safe: we own a handler now
    else:
        # Standalone (pytest, `python -m app`). Let root handle it — do NOT disable
        # propagation here, or the handler basicConfig just installed is unreachable.
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
        app_log.propagate = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_app_logging()
    settings = get_settings()
    # Warm if EITHER half runs a real model. Keying this on the global screening_mode was
    # a bug once per-component modes existed: text_mode=auto loaded a real checkpoint that
    # was never warmed, so the first request paid the cold-start cost, blew the 250ms text
    # budget, and fail-open escalated a perfectly ordinary article.
    modes = (effective_text_mode(settings), effective_image_mode(settings))
    needs_warm = any(mode != "heuristic" for mode in modes)
    if settings.warmup and needs_warm:
        _WARMUP_STATUS.extend(_warm_screening_models())
        for line in _WARMUP_STATUS:
            log.info("WS4 warmup — %s", line)
    else:
        _WARMUP_STATUS.append(f"skipped (text={effective_text_mode(settings)}, image={effective_image_mode(settings)}, warmup={settings.warmup})")
    yield


app = FastAPI(
    title="Dasfax backend — Tier 2 screening & Tier 3 claim extraction",
    version="0.2.0",
    summary="WS4: fast ML screening that decides what is worth escalating. WS5: claims + evidence.",
    lifespan=lifespan,
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
        "screening_mode": s.screening_mode,
        "text_mode": effective_text_mode(s),
        "image_mode": effective_image_mode(s),
        "bert_model": s.bert_model_name,
        "text_threshold": s.text_threshold,
        "image_threshold": s.image_threshold,
        # Which backends are actually live. A degraded tier must be visible here, not
        # only discoverable by reading `reasons` on an individual response.
        "screening_backends": list(_WARMUP_STATUS),
    }


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


app.include_router(analyze_router)
