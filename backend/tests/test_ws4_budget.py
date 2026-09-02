"""WS4 latency-budget and partial-result tests.

These guard the difference between "screens a page" and "screens a page in time". They use
sleep-based fakes rather than real models so they stay offline and fast, and they assert
wall-clock bounds — the only way to catch a sequential regression, which is functionally
correct and operationally fatal.
"""
from __future__ import annotations

import time
from collections.abc import Sequence

import pytest

from app.clients.screening_base import FetchedImage, ImageRisk, TextRisk
from app.config import Settings
from app.models.screening import ScreeningInput
from app.pipeline import ws4
from app.services import image_fetch

URLS = [f"https://cdn.example.org/photos/{i}.jpg" for i in range(6)]
ARTICLE = "word " * 60


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class SlowTextScorer:
    name = "fake-slow-text"

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s

    def warmup(self) -> None: ...

    def score(self, *, title: str | None, text: str) -> TextRisk:
        time.sleep(self.delay_s)
        return TextRisk(score=0.0, backend=self.name)


class InstantTextScorer:
    name = "fake-text"

    def __init__(self, score: float = 0.0) -> None:
        self._score = score

    def warmup(self) -> None: ...

    def score(self, *, title: str | None, text: str) -> TextRisk:
        return TextRisk(score=self._score, backend=self.name)


class PixelImageScorer:
    """Stands in for the real CNN: needs pixels, so the pipeline must fetch."""

    name = "fake-pixel-image"
    needs_pixels = True

    def warmup(self) -> None: ...

    def score_batch(self, images: Sequence[FetchedImage]) -> list[ImageRisk]:
        return [
            ImageRisk(i.url, 0.9 if i.ok else 0.0, self.name, i.error or "scored")
            for i in images
        ]


def _page(**kw) -> ScreeningInput:
    body = {"url": "https://news.example.org/a", "text": ARTICLE, "images": URLS}
    body.update(kw)
    return ScreeningInput.model_validate(body)


# --------------------------------------------------------------------------- #
# Defect 3 — image fetching was sequential: 6 images x 4s timeout = 24s worst case.
# --------------------------------------------------------------------------- #
def test_fetch_images_runs_in_parallel(monkeypatch):
    def slow_fetch(url: str, *, timeout_s: float, max_bytes: int) -> FetchedImage:
        time.sleep(0.2)
        return FetchedImage(url=url, image=object(), bytes_read=1)

    monkeypatch.setattr(image_fetch, "fetch_one", slow_fetch)

    started = time.monotonic()
    results = image_fetch.fetch_images(
        URLS,
        deadline=started + 5.0,
        per_request_timeout_s=1.0,
        max_bytes=1024,
        pool=ws4._FETCH_POOL,
    )
    elapsed = time.monotonic() - started

    assert len(results) == 6
    assert all(r.ok for r in results)
    # Sequentially this is 1.2s. Parallel it is ~0.2s; 0.6s leaves generous headroom
    # while still failing loudly if the fetches ever serialise again.
    assert elapsed < 0.6, f"fetches appear to be sequential ({elapsed:.2f}s for 6 x 0.2s)"


def test_fetch_images_abandons_work_past_the_deadline(monkeypatch):
    def hanging_fetch(url: str, *, timeout_s: float, max_bytes: int) -> FetchedImage:
        time.sleep(5.0)
        return FetchedImage(url=url, image=object())

    monkeypatch.setattr(image_fetch, "fetch_one", hanging_fetch)

    started = time.monotonic()
    results = image_fetch.fetch_images(
        URLS,
        deadline=started + 0.3,
        per_request_timeout_s=5.0,
        max_bytes=1024,
        pool=ws4._FETCH_POOL,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"deadline not honoured ({elapsed:.2f}s)"
    assert all(r.error == "deadline exceeded" for r in results)
    assert [r.url for r in results] == URLS  # order preserved


def test_fetch_images_preserves_order_with_mixed_outcomes(monkeypatch):
    def mixed(url: str, *, timeout_s: float, max_bytes: int) -> FetchedImage:
        if url.endswith("3.jpg"):
            return FetchedImage(url=url, error="fetch failed: URLError")
        return FetchedImage(url=url, image=object())

    monkeypatch.setattr(image_fetch, "fetch_one", mixed)
    results = image_fetch.fetch_images(
        URLS, deadline=time.monotonic() + 5.0, per_request_timeout_s=1.0,
        max_bytes=1024, pool=ws4._FETCH_POOL,
    )
    assert [r.url for r in results] == URLS
    assert results[3].error is not None and results[3].image is None


# --------------------------------------------------------------------------- #
# Timeout policy: text fails OPEN, images fail CLOSED.
# --------------------------------------------------------------------------- #
def test_text_timeout_escalates_rather_than_passing_unscreened():
    settings = Settings(screening_mode="heuristic", text_budget_ms=50.0, screen_budget_ms=300.0)
    result = ws4.run_ws4(
        _page(images=[]),
        settings=settings,
        text_scorer=SlowTextScorer(delay_s=2.0),
    )
    assert result.escalate_to_tier3 is True, "a text timeout must fail open"
    assert result.text_scored is False
    assert result.degraded is True
    assert any("budget" in r for r in result.reasons)


def test_image_timeout_does_not_escalate_on_its_own():
    def timing_out_fetcher(urls, **_kw):
        return [FetchedImage(url=u, error="deadline exceeded") for u in urls]

    settings = Settings(screening_mode="heuristic")
    result = ws4.run_ws4(
        _page(),
        settings=settings,
        text_scorer=InstantTextScorer(score=0.0),
        image_scorer=PixelImageScorer(),
        fetcher=timing_out_fetcher,
    )
    assert result.images_timed_out is True
    assert result.image_flagged is False
    assert result.escalate_to_tier3 is False, "unfetchable images carry no signal"
    assert result.degraded is True


def test_pixel_backend_scores_successfully_fetched_images():
    def ok_fetcher(urls, **_kw):
        return [FetchedImage(url=u, image=object(), bytes_read=10) for u in urls]

    result = ws4.run_ws4(
        _page(),
        settings=Settings(screening_mode="heuristic"),
        text_scorer=InstantTextScorer(score=0.0),
        image_scorer=PixelImageScorer(),
        fetcher=ok_fetcher,
    )
    assert result.images_timed_out is False
    assert result.image_flagged is True
    assert result.escalate_to_tier3 is True


def test_url_only_backend_never_fetches():
    """The heuristic backend declares needs_pixels=False, which is what keeps CI offline.

    image_mode is pinned explicitly: the shipped default is "auto" (the CNN works even
    though no text checkpoint does), so relying on screening_mode alone would build a
    pixel backend here and reach the network.
    """

    def exploding_fetcher(urls, **_kw):
        raise AssertionError("a URL-only backend must not trigger any network fetch")

    result = ws4.run_ws4(
        _page(),
        settings=Settings(screening_mode="heuristic", image_mode="heuristic"),
        fetcher=exploding_fetcher,
    )
    assert result.model_meta["images_screened"] == 6


# --------------------------------------------------------------------------- #
# Caps and hygiene
# --------------------------------------------------------------------------- #
def test_images_are_capped_and_deduplicated():
    many = [f"https://cdn.example.org/{i}.jpg" for i in range(20)] + URLS[:1] * 3
    result = ws4.run_ws4(
        _page(images=many), settings=Settings(screening_mode="heuristic", max_images_screened=6)
    )
    assert result.model_meta["images_screened"] == 6
    assert result.model_meta["images_available"] == len(many)
    assert len({r.image_url for r in result.image_results}) == 6


def test_latency_is_reported_and_checked_against_the_budget():
    result = ws4.run_ws4(_page(), settings=Settings(screening_mode="heuristic"))
    assert result.latency_ms >= 0.0
    assert result.within_latency_budget is True


@pytest.mark.parametrize("bad_url", ["data:image/png;base64,iVBOR", "file:///etc/passwd", "ftp://x/y.jpg"])
def test_non_http_urls_are_never_dereferenced(bad_url):
    assert image_fetch.fetch_one(bad_url, timeout_s=1.0, max_bytes=1024).error == "not an http(s) URL"
