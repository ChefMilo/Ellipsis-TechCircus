"""POST /analyze -- WS3's client-facing endpoint.

Pure routing glue: validate the request (FastAPI/Pydantic), hand it to
`app.orchestrator.pipeline.run_analysis()`, return whatever it returns. All the actual
decision-making -- the Tier 2 gate, Tier 3's timeout/exception handling, caching, the
article verdict -- lives in app/orchestrator/; this file does no analysis itself.

Contract: this route ALWAYS returns HTTP 200 with a body that satisfies
`isAnalysisResponse()` (src/shared/contract.ts) -- see docs/ws3/WS3-CONTRACT-AUDIT.md for why
that guard, not the HTTP status, is the thing that actually matters to the extension.
`run_analysis()` already converts every failure mode it knows about into a valid
`AnalysisResponse`; the try/except below is the last line of defense against anything
it does NOT know about (a bug inside run_analysis itself, a Pydantic error building one
of its own fallback envelopes, anything) -- so this route can never hand the extension
a raw 500. (backend-client.ts on the extension side already turns any non-2xx into its
own client-side failure envelope, so this isn't the only safety net in the system -- but
"never return a 500" is the explicit ask for THIS layer, not "trust the client to
recover from one".)
"""
from __future__ import annotations

from fastapi import APIRouter

from app.models.contract import (
    AnalysisError,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisStatus,
    ArticleVerdict,
    ArticleVerdictLevel,
)
from app.orchestrator.pipeline import run_analysis

router = APIRouter()


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest) -> AnalysisResponse:
    try:
        return await run_analysis(request)
    except Exception as err:  # noqa: BLE001 -- absolute last resort; see module docstring
        return AnalysisResponse(
            schemaVersion="1.0",
            url=request.url,
            status=AnalysisStatus.FAILED,
            articleVerdict=ArticleVerdict(
                level=ArticleVerdictLevel.UNRATED,
                summary="An unexpected error occurred, so this page was not checked.",
                confidence=None,
            ),
            verifiedClaims=[],
            errors=[AnalysisError(code="internal_error", message=str(err))],
        )
