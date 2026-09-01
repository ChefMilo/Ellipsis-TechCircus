"""Offline heuristic backends for Tier 2. No weights, no network, no API keys.

These wrap the pure functions in `app/services/` behind the TextScorer / ImageScorer
Protocols so the pipeline cannot tell them apart from the real checkpoints. They exist so
CI, `pytest` and an offline demo work — not to be accurate.

`degraded_reason` is threaded through deliberately: when the factory falls back to these
because the real model would not load, every response says so, so a fallback can never be
quietly presented as BERT output.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.clients.screening_base import FetchedImage, ImageRisk, TextRisk
from app.services.image_detector import BACKEND_NAME as IMAGE_BACKEND
from app.services.image_detector import heuristic_image_score
from app.services.text_classifier import BACKEND_NAME as TEXT_BACKEND
from app.services.text_classifier import heuristic_score


class HeuristicTextScorer:
    name = TEXT_BACKEND

    def __init__(self, degraded_reason: str | None = None) -> None:
        self.degraded_reason = degraded_reason

    def warmup(self) -> None:
        """Nothing to load."""

    def score(self, *, title: str | None, text: str) -> TextRisk:
        score, features = heuristic_score(title, text)
        return TextRisk(
            score=score,
            backend=self.name,
            features=features,
            degraded_reason=self.degraded_reason,
        )


class HeuristicImageScorer:
    name = IMAGE_BACKEND
    # Scores from the URL alone, so the pipeline skips downloading entirely. This is what
    # keeps the default test run at zero network with no test-only branching upstream.
    needs_pixels = False

    def __init__(self, degraded_reason: str | None = None) -> None:
        self.degraded_reason = degraded_reason

    def warmup(self) -> None:
        """Nothing to load."""

    def score_batch(self, images: Sequence[FetchedImage]) -> list[ImageRisk]:
        results = [heuristic_image_score(item.url) for item in images]
        if not self.degraded_reason:
            return results
        return [
            ImageRisk(r.image_url, r.score, r.backend, f"{self.degraded_reason}: {r.reason}")
            for r in results
        ]
