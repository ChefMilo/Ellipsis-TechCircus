"""The client-facing envelope: what the extension actually renders.

MIRROR OF `src/shared/contract.ts` (`AnalysisResponse`). That envelope is WS2-owned and
its header says WS3 will "either ratify it or hand us theirs" — this ratifies it. The
content script validates every reply with `isAnalysisResponse()`, so any drift here shows
up as "dasfax: malformed analysis response" in the page rather than as a type error.

Field names are camelCase on the wire because that is what WS2 already parses. Python
keeps snake_case and serialises through aliases; FastAPI serialises by alias by default.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.contract import VerifiedClaim

AnalysisStatus = Literal["complete", "processing", "failed", "skipped"]
ArticleVerdictLevel = Literal["trusted", "ok", "caution", "high_risk"]


class ArticleVerdict(BaseModel):
    """The one-line summary behind the pill WS2 renders."""

    level: ArticleVerdictLevel
    summary: str
    confidence: float | None = None


class AnalysisError(BaseModel):
    code: str
    message: str


class Tier2Summary(BaseModel):
    """Why the cascade did what it did. Not required by WS2's guard (it ignores unknown
    keys), but it makes the tier decision visible in a demo instead of invisible — the
    whole point of the cascade is that most pages stop early, and that is worth showing."""

    model_config = ConfigDict(populate_by_name=True)

    escalated: bool
    text_score: float = Field(serialization_alias="textScore")
    max_image_score: float = Field(serialization_alias="maxImageScore")
    reasons: list[str] = Field(default_factory=list)
    degraded: bool = False
    latency_ms: float = Field(0.0, serialization_alias="latencyMs")


class AnalysisResponse(BaseModel):
    """Exactly the shape `isAnalysisResponse()` accepts."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["1.0"] = Field("1.0", serialization_alias="schemaVersion")
    url: str
    status: AnalysisStatus
    article_verdict: ArticleVerdict = Field(serialization_alias="articleVerdict")
    verified_claims: list[VerifiedClaim] = Field(
        default_factory=list, serialization_alias="verifiedClaims"
    )
    errors: list[AnalysisError] | None = None
    tier2: Tier2Summary | None = None
