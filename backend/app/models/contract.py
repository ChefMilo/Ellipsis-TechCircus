"""
Dasfax — Tier 3 shared contract.

THIS FILE IS THE CONTRACT for the two halves of Tier 3:

    WS5 (claim extraction + evidence retrieval)  --produces-->  ClaimExtractionResult
    WS6 (assessment)                             --consumes-->  ClaimExtractionResult
                                                 --produces-->  Assessment (one per Claim)

It is owned by WS5 by default (recon found no types, no backend, no mock anywhere in
the repo). WS6 and WS3 should import these models rather than redefining shapes, so the
seam stays frozen. If a field needs to change, change it HERE and tell WS6/WS3 — do not
fork a parallel definition.

Field ownership is annotated inline:
    [WS5] populated by WS5.        [WS6] populated by WS6.        [WS3] envelope/routing.

Pydantic v2.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class ClaimType(StrEnum):
    """What kind of statement a sentence is. WS5 only checks FACTUAL claims;
    OPINION / PREDICTION are extracted so WS6 can surface an 'Opinion' treatment
    without WS5 wasting retrieval budget on them."""

    FACTUAL = "factual"          # a verifiable, checkable assertion about the world
    OPINION = "opinion"          # value judgement / subjective statement
    PREDICTION = "prediction"    # claim about the future, not yet checkable


class AssessmentStatus(StrEnum):
    """WS6 verdict vocabulary (proposal §2.3). Defined here so the whole Tier 3
    contract lives in one file; WS5 never sets these."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    NEEDS_REVIEW = "needs_review"      # insufficient evidence -> honest fallback
    OPINION = "opinion"               # not a factual claim; nothing to verify


# --------------------------------------------------------------------------- #
# Inputs (what WS3/WS1 hand to WS5)
# --------------------------------------------------------------------------- #
class ArticleInput(BaseModel):
    """Cleaned article text handed to WS5. Text is expected to be already
    extracted/cleaned upstream (WS1 article extraction). WS5 does not fetch pages."""

    url: str = Field(..., description="Canonical URL of the article (for logging/citation dedup).")
    title: str | None = Field(None, description="Article headline, if available.")
    text: str = Field(..., min_length=1, description="Cleaned article body text.")
    lang: str | None = Field(None, description="BCP-47 language tag, e.g. 'en'. Advisory only.")
    source_domain: str | None = Field(None, description="Hostname, e.g. 'straitstimes.com'.")
    published_at: datetime | None = Field(None, description="Publish timestamp if known.")

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v


# --------------------------------------------------------------------------- #
# Evidence + Claim (WS5 output)
# --------------------------------------------------------------------------- #
class Evidence(BaseModel):
    """One retrieved snippet that bears on a claim. [WS5]"""

    snippet: str = Field(..., description="Short retrieved passage relevant to the claim.")
    source_url: str = Field(..., description="Resolvable URL the snippet came from.")
    source_title: str | None = Field(None, description="Title of the source page.")
    source_domain: str | None = Field(None, description="Hostname of the source.")
    published_at: datetime | None = Field(None, description="Source publish date if known.")
    relevance_score: float | None = Field(
        None, ge=0.0, le=1.0,
        description="Retrieval relevance in [0,1]. Advisory; WS6 may re-rank.",
    )


class Claim(BaseModel):
    """A single extracted claim with its retrieved evidence.

    WS5 fills everything here. WS6 reads `text` + `evidence` and writes a separate
    Assessment keyed by `id` — WS6 does NOT mutate the Claim.
    """

    id: str = Field(..., description="Stable id within one article (e.g. 'c1'). Assessment.claim_id references this.")
    text: str = Field(..., description="The claim as a self-contained sentence. WS2 fuzzy-matches this back onto the DOM.")
    claim_type: ClaimType = Field(..., description="factual / opinion / prediction.")
    checkworthiness: float = Field(
        ..., ge=0.0, le=1.0,
        description="How load-bearing/verifiable this claim is. Used for ranking; higher = check first.",
    )
    rank: int = Field(..., ge=1, description="1 = most important claim to check. Only ranked FACTUAL claims carry evidence.")
    search_query: str | None = Field(None, description="Query WS5 issued to retrieve evidence (for transparency/debug).")
    evidence: list[Evidence] = Field(default_factory=list, description="Retrieved snippets. Empty for non-factual or unretrieved claims.")

    # --- Anchoring hints (WS5 -> WS2) -------------------------------------- #
    # Offsets index ArticleInput.text — the same cleaned body text WS1 produces as
    # `bodyText` — so WS2 can map the span onto the live DOM:
    # `ArticleInput.text[char_start:char_end]` is the source span this claim came from.
    # All four are None when WS5 could not place the claim confidently; WS2 then falls
    # back to fuzzy matching on `text`.
    char_start: int | None = Field(None, ge=0, description="Start offset of the claim's source span within ArticleInput.text.")
    char_end: int | None = Field(None, ge=0, description="End offset (exclusive) of the source span within ArticleInput.text.")
    prefix: str | None = Field(None, description="Up to 32 chars of ArticleInput.text immediately BEFORE the span (disambiguates repeats).")
    suffix: str | None = Field(None, description="Up to 32 chars of ArticleInput.text immediately AFTER the span.")


# --------------------------------------------------------------------------- #
# WS5 result envelope (WS5 -> WS6)
# --------------------------------------------------------------------------- #
class ExtractionStats(BaseModel):
    """Observability for WS5. Lets the pitch quote real numbers and lets WS3 log tiers."""

    total_claims_extracted: int = 0
    factual_claims: int = 0
    dropped_opinion_or_prediction: int = 0
    dropped_duplicate: int = 0
    kept_after_ranking: int = 0
    claims_with_evidence: int = 0
    claims_anchored: int = 0


class ClaimExtractionResult(BaseModel):
    """WS5's deliverable. This is exactly what WS6 receives."""

    url: str
    title: str | None = None
    claims: list[Claim] = Field(default_factory=list, description="Ranked, deduped. At most `max_claims` factual claims carry evidence.")
    stats: ExtractionStats = Field(default_factory=ExtractionStats)
    model_meta: dict = Field(
        default_factory=dict,
        description="Freeform: which LLM/search backend + version produced this (mock vs real).",
    )


# --------------------------------------------------------------------------- #
# Assessment (WS6 output) — defined here so the seam is visible; WS5 never writes it.
# --------------------------------------------------------------------------- #
class Citation(BaseModel):
    """A specific piece of evidence WS6 leaned on for its verdict. [WS6]"""

    snippet: str
    source_url: str
    source_title: str | None = None


class Assessment(BaseModel):
    """WS6's verdict for one claim. [WS6] — included in the contract for completeness.

    Rule WS6 must honour (proposal §2.5): every non-OPINION assessment carries at least
    one Citation, or its status is NEEDS_REVIEW. Malformed output must never reach the client.
    """

    claim_id: str = Field(..., description="References Claim.id.")
    status: AssessmentStatus
    explanation: str = Field(..., description="Plain-language reason, grounded in the citations.")
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    citations: list[Citation] = Field(default_factory=list)


# Convenience: the shape WS3 would return to the extension (WS2) once WS6 has run.
class VerifiedClaim(BaseModel):
    """Claim + its Assessment, joined by WS3 for the client. Neither WS5 nor WS6 build this alone."""

    claim: Claim
    assessment: Assessment | None = None


# --------------------------------------------------------------------------- #
# Client envelope (backend -> WS2)
#
# MIRRORS src/shared/contract.ts, where WS2 authored these shapes first as its
# own "client envelope". Now that the backend actually produces them, THIS file
# is the source of truth for the envelope too — Darren / Terry, sync
# src/shared/contract.ts from here and drop its "proposed to WS3" caveat.
#
# Field names are camelCase ON PURPOSE: they are the literal wire format WS2's
# isAnalysisResponse() guard checks. Renaming them to snake_case breaks the
# renderer silently (the guard returns false and the response is dropped).
# --------------------------------------------------------------------------- #
class AnalysisStatus(StrEnum):
    """WS2's `AnalysisStatus` union, verbatim."""

    COMPLETE = "complete"
    PROCESSING = "processing"
    FAILED = "failed"
    SKIPPED = "skipped"      # Tier 0 trusted source -> WS2 renders the "trusted" pill


class ArticleVerdictLevel(StrEnum):
    """WS2's `ArticleVerdictLevel` union, verbatim.

    OK/CAUTION/HIGH_RISK are Tier 3 rollups: `article_verdict_for()` derives them from
    the per-claim verdicts and nothing else may produce OK — it means "claims were
    checked and at least one holds up". UNRATED is the neutral state for every path that
    ends before a Tier 3 rollup: no article text, zero checkable claims, or a Tier 2
    screen that cleared the page without escalating. It is not a clean bill of health and
    not a warning; the reason for it rides in `ArticleVerdict.summary`. TRUSTED is Tier 0
    (whitelisted domain). See app/services/envelope.py.
    """

    TRUSTED = "trusted"
    OK = "ok"
    CAUTION = "caution"
    HIGH_RISK = "high_risk"
    UNRATED = "unrated"


class ArticleVerdict(BaseModel):
    """Article-level rollup shown on WS2's summary pill and at the top of the panel."""

    level: ArticleVerdictLevel
    summary: str
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class AnalysisError(BaseModel):
    """Non-fatal problem to surface alongside a still-valid envelope."""

    code: str
    message: str


class AnalysisRequest(BaseModel):
    """What the extension POSTs to /analyze.

    `text` is OPTIONAL so WS3 can wire and test the fetch before WS1 forwards
    extracted body text; the endpoint answers with a valid empty envelope until it
    arrives, and lights up on its own once it does.
    """

    url: str = Field(..., description="Canonical URL of the page being analyzed.")
    title: str | None = Field(None, description="Page/article title, if the content script has one.")
    text: str | None = Field(None, description="Cleaned article body text (WS1 `bodyText`). Absent until WS1 forwards it.")
    images: list[str] | None = Field(None, description="Article image URLs. Unused by WS5 today; carried for WS6.")


class AnalysisResponse(BaseModel):
    """The client-facing envelope WS2 renders. Must satisfy isAnalysisResponse()."""

    schemaVersion: Literal["1.0"] = "1.0"
    url: str
    status: AnalysisStatus
    articleVerdict: ArticleVerdict
    verifiedClaims: list[VerifiedClaim] = Field(default_factory=list)
    # WS2 types this `errors?: AnalysisError[]`; `null` is not a member of that type,
    # so default to an empty list. Do not switch this model to exclude_none — that
    # would also drop `assessment: null`, which WS2's isVerifiedClaim() requires.
    errors: list[AnalysisError] = Field(default_factory=list)
