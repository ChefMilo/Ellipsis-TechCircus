"""Provider-agnostic interfaces for WS4's two Tier 2 models.

Mirrors the structure of `app/clients/base.py` (WS5's LLM/search seam): the pipeline
depends ONLY on these Protocols, never on a concrete backend, so the real HuggingFace
checkpoints and the offline heuristics are drop-in interchangeable.

`ImageScorer.needs_pixels` is the load-bearing detail. The heuristic image backend reads
URL provenance markers and never looks at an image, so declaring `needs_pixels = False`
lets the pipeline skip downloading entirely — which is what keeps the default test run at
zero network without any test-only branching in the pipeline itself.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TextRisk:
    """One text screening outcome, with the feature breakdown that produced it."""

    score: float
    backend: str
    features: dict[str, float] = field(default_factory=dict)
    degraded_reason: str | None = None


@dataclass(frozen=True)
class ImageRisk:
    """One image screening outcome.

    `scored=False` means the backend never got to look at this image (unfetchable, decode
    failure, unknown label scheme). Its 0.0 is "no evidence", NOT "authentic" — the
    distinction matters, because a page whose images all failed to fetch has been given a
    clean bill of health it never actually earned.
    """

    image_url: str
    score: float
    backend: str
    reason: str
    scored: bool = True


@dataclass(frozen=True)
class FetchedImage:
    """An image URL after the download attempt.

    `image` is a decoded PIL Image when the fetch succeeded and pixels were requested,
    None otherwise. `error` explains why there are no pixels — a scorer that needs them
    must treat an errored entry as "no evidence", never as "authentic".
    """

    url: str
    image: object | None = None
    error: str | None = None
    bytes_read: int = 0
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.image is not None and self.error is None


@runtime_checkable
class TextScorer(Protocol):
    """Scores article text for fake-news risk in [0,1].

    `threshold` belongs to the scorer, not the pipeline: the heuristic and the fine-tuned
    model have completely different score distributions (the heuristic operates at 0.40,
    the model at 0.995), so one shared number would leave whichever backend is running
    either inert or hair-trigger.
    """

    name: str
    threshold: float

    def warmup(self) -> None:
        """Load weights and run one forward pass. No-op for offline backends."""
        ...

    def score(self, *, title: str | None, text: str) -> TextRisk:
        ...


@runtime_checkable
class ImageScorer(Protocol):
    """Scores images for synthetic-media likelihood in [0,1]."""

    name: str
    needs_pixels: bool

    def warmup(self) -> None:
        ...

    def score_batch(self, images: Sequence[FetchedImage]) -> list[ImageRisk]:
        ...
