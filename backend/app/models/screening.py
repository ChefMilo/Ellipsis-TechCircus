"""
Dasfax — Tier 2 screening contract (WS4).

THIS FILE IS THE CONTRACT for the Tier 2 seam:

    WS1/WS3 (extracted page)  --produces-->  ScreeningInput
    WS4 (Tier 2 screening)    --produces-->  ScreeningResult
    WS3 (tier controller)     --reads-->     ScreeningResult.escalate_to_tier3

`ScreeningInput` extends `PageEnvelope` from the Tier 3 contract rather than redefining
url/title/lang/source_domain — Tier 2 and Tier 3 receive the same page envelope. It does
NOT extend `ArticleInput`, because Tier 2 must tolerate a page whose body text could not
be extracted (it can still screen the images), while Tier 3 cannot.

Pydantic v2.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field

from app.models.contract import PageEnvelope


def _none_to_empty(v: object) -> object:
    """Treat a missing/null image list as an empty one.

    The client omits the field entirely when Readability finds no images, and a JS worker
    marshalling `undefined` can turn that into an explicit `null`. Neither should be a 422.
    """
    return [] if v is None else v


# --------------------------------------------------------------------------- #
# Input (what WS1/WS3 hand to WS4)
# --------------------------------------------------------------------------- #
class ScreeningInput(PageEnvelope):
    """One page that survived Tier 1 article detection, ready for cheap ML screening."""

    text: str | None = Field(
        None,
        description=(
            "Cleaned article body text. OPTIONAL at Tier 2: when extraction fails the page "
            "is still screened on its images, and the result reports text_scored=false."
        ),
    )

    # The extension sends `images` (src/content/bootstrap.ts); this contract's own name is
    # `image_urls`. Both are accepted so the two spellings cannot silently diverge — an
    # unaliased mismatch is invisible, because pydantic drops unknown keys and every page
    # would screen image-clean.
    image_urls: Annotated[list[str], BeforeValidator(_none_to_empty)] = Field(
        default_factory=list,
        validation_alias=AliasChoices("image_urls", "images"),
        description="Absolute image URLs collected from the page by WS1. May be empty.",
    )


# --------------------------------------------------------------------------- #
# Output (WS4 -> WS3)
# --------------------------------------------------------------------------- #
class ImageScreeningResult(BaseModel):
    """Per-image synthetic-media score. One of these per screened image URL."""

    image_url: str
    is_synthetic_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="P(image was produced by a generative model). Higher = more likely synthetic.",
    )
    flagged: bool = Field(..., description="True when is_synthetic_score >= the image threshold.")
    reason: str | None = Field(
        None, description="Short human-readable justification (which signal fired, or why it was skipped)."
    )


class ScreeningResult(BaseModel):
    """WS4's deliverable. WS3's tier controller routes on `escalate_to_tier3`.

    Tier 2 is a *router*, not a verdict: nothing in here is ever shown to the user as a
    judgement. A high text_score means "spend Tier 3 budget here", not "this is fake".
    """

    url: str
    text_score: float = Field(..., ge=0.0, le=1.0, description="P(article text is misleading/fake-news-like).")
    text_flagged: bool = Field(..., description="text_score >= text threshold.")

    image_results: list[ImageScreeningResult] = Field(default_factory=list)
    max_image_score: float = Field(
        0.0, ge=0.0, le=1.0, description="Highest is_synthetic_score across screened images; 0.0 when none."
    )
    image_flagged: bool = Field(False, description="Any image scored >= the image threshold.")

    escalate_to_tier3: bool = Field(..., description="text_flagged OR image_flagged.")
    reasons: list[str] = Field(
        default_factory=list,
        description="Why this decision was made, in plain language. Empty means 'nothing fired'.",
    )

    # --- Degradation signals (WS4 -> WS3) ---------------------------------- #
    # Promoted to real fields rather than buried in model_meta because WS3 routes on them:
    # a degraded screen is not the same evidence as a clean one.
    degraded: bool = Field(
        False, description="A component fell back or timed out; treat the decision as lower-confidence."
    )
    text_scored: bool = Field(
        True, description="False when there was no usable body text and the text model never ran."
    )
    images_timed_out: bool = Field(
        False, description="At least one image was abandoned at the deadline rather than scored."
    )

    latency_ms: float = Field(..., ge=0.0, description="Wall-clock time for the whole parallel screen.")
    within_latency_budget: bool = Field(
        True, description="latency_ms <= DASFAX_SCREEN_BUDGET_MS. False is a perf regression signal."
    )
    model_meta: dict = Field(
        default_factory=dict,
        description="Which backends/thresholds produced this (heuristic vs huggingface, model names, timings).",
    )
