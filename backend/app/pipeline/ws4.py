"""WS4 pipeline: Tier 2 ML screening.

Single entry point for WS4. WS3's tier controller can import `run_ws4` directly (no HTTP
hop), or call it via POST /tier2/screen in app.main.

Flow:
  1. The text model and the image batch run CONCURRENTLY, each against its own budget and
     a shared global deadline.
  2. Each score is compared against its calibrated threshold (see ws4_eval.py).
  3. escalate_to_tier3 = text_flagged OR image_flagged, with plain-language reasons.

Why OR, and why the thresholds are recall-first:
Tier 2 is a router, not a verdict. A Tier 2 false positive costs Tier 3 compute; a Tier 2
false negative means the article is never checked at all and the reader sees nothing. The
"don't cry wolf" constraint from §3.3 binds at Tier 3/WS6, where a status is actually
shown to a reader. So Tier 2 is tuned for recall at an affordable escalation rate, and
that trade is stated explicitly rather than left implicit in a magic number.

Timeout policy follows the same asymmetry:
  * text times out  -> FAIL OPEN (escalate). Better to spend Tier 3 budget than to let an
    unscreened article through unexamined.
  * images time out -> FAIL CLOSED (do not escalate on images alone). Fetch failures
    correlate with CDNs, paywalls and hotlink protection, not with synthetic content, so
    escalating on them would burn Tier 3 budget on a signal that carries no information.

The thread pools are module-level, not per-request. A `with ThreadPoolExecutor(...)` block
calls `shutdown(wait=True)` on exit, which joins every worker — so a scoped pool makes a
deadline impossible: the slow task would be waited on regardless of any timeout.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from app.clients.factory import make_image_scorer, make_text_scorer
from app.clients.screening_base import FetchedImage, ImageRisk, ImageScorer, TextScorer
from app.config import Settings, get_settings
from app.models.screening import ImageScreeningResult, ScreeningInput, ScreeningResult
from app.services.image_fetch import MAX_IMAGE_BYTES, fetch_images

# Two pools, deliberately. FastAPI runs `def` endpoints in Starlette's 40-thread pool, so
# many screens can be in flight at once; if image fetches shared one small pool with the
# task slots they would queue behind each other and every request would time out on a
# starvation problem that looks exactly like a slow network.
_TASK_WORKERS = int(os.environ.get("DASFAX_TASK_WORKERS") or 0) or min(32, (os.cpu_count() or 4) * 2)
_FETCH_WORKERS = int(os.environ.get("DASFAX_FETCH_WORKERS") or 0) or 12

_TASK_POOL = ThreadPoolExecutor(max_workers=_TASK_WORKERS, thread_name_prefix="ws4-task")
_FETCH_POOL = ThreadPoolExecutor(max_workers=_FETCH_WORKERS, thread_name_prefix="ws4-fetch")

_DEADLINE_ERROR = "deadline exceeded"


def _unique(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def _screen_images(
    urls: list[str],
    scorer: ImageScorer,
    *,
    deadline: float,
    settings: Settings,
    fetcher,
    clock,
) -> tuple[list[ImageRisk], bool]:
    """Fetch (only if the backend needs pixels) and score one page's images."""
    if scorer.needs_pixels:
        fetched = fetcher(
            urls,
            deadline=deadline,
            per_request_timeout_s=settings.image_fetch_timeout_s,
            max_bytes=MAX_IMAGE_BYTES,
            pool=_FETCH_POOL,
            clock=clock,
        )
    else:
        # URL-only backend: no network at all, which is what keeps CI offline.
        fetched = [FetchedImage(url=u) for u in urls]

    timed_out = any(item.error == _DEADLINE_ERROR for item in fetched)
    return scorer.score_batch(fetched), timed_out


def run_ws4(
    page: ScreeningInput,
    *,
    settings: Settings | None = None,
    text_scorer: TextScorer | None = None,
    image_scorer: ImageScorer | None = None,
    fetcher=fetch_images,
    clock=time.monotonic,
) -> ScreeningResult:
    """Screen one page at Tier 2. Components are injectable for testing; otherwise built
    from Settings."""
    settings = settings or get_settings()
    text_scorer = text_scorer or make_text_scorer(settings)
    image_scorer = image_scorer or make_image_scorer(settings)

    started = clock()
    deadline = started + settings.screen_budget_ms / 1000.0

    text_scored = bool(page.text and page.text.strip())
    urls = _unique(page.image_urls)[: settings.max_images_screened]

    text_future: Future | None = None
    image_future: Future | None = None
    if text_scored:
        text_future = _TASK_POOL.submit(text_scorer.score, title=page.title, text=page.text)
    if urls:
        image_deadline = min(deadline, started + settings.image_budget_ms / 1000.0)
        image_future = _TASK_POOL.submit(
            _screen_images,
            urls,
            image_scorer,
            deadline=image_deadline,
            settings=settings,
            fetcher=fetcher,
            clock=clock,
        )

    # --- collect, bounded by each task's budget and the global deadline ---------- #
    text_risk = None
    text_timed_out = False
    if text_future is not None:
        text_wait = min(deadline, started + settings.text_budget_ms / 1000.0) - clock()
        try:
            text_risk = text_future.result(timeout=max(0.0, text_wait))
        except FutureTimeoutError:
            text_timed_out = True
            text_future.cancel()

    image_risks: list[ImageRisk] = []
    images_timed_out = False
    images_requested = len(urls)
    if image_future is not None:
        try:
            image_risks, images_timed_out = image_future.result(timeout=max(0.0, deadline - clock()))
        except FutureTimeoutError:
            images_timed_out = True
            image_future.cancel()

    images_scored = sum(1 for r in image_risks if r.scored)
    # Images that were requested but never actually examined (all unfetchable, decode
    # failures, a dead TLS trust store) are not the same as a page with no images. Without
    # this the response would read "nothing flagged" for a check that never ran.
    images_unexamined = images_requested > 0 and images_scored == 0

    latency_ms = round((clock() - started) * 1000, 3)

    # --- decide ------------------------------------------------------------------ #
    # No body text, or a text model that overran, means score 0.0 = "no evidence", NOT
    # "looks safe". The escalation decision below handles the two cases differently.
    text_score = text_risk.score if text_risk else 0.0
    text_flagged = text_risk is not None and text_score >= settings.text_threshold

    image_results = [
        ImageScreeningResult(
            image_url=r.image_url,
            is_synthetic_score=r.score,
            flagged=r.score >= settings.image_threshold,
            reason=r.reason,
        )
        for r in image_risks
    ]
    max_image_score = max((r.is_synthetic_score for r in image_results), default=0.0)
    image_flagged = any(r.flagged for r in image_results)

    escalate = text_flagged or image_flagged or text_timed_out

    reasons: list[str] = []
    if text_flagged:
        reasons.append(f"text risk {text_score:.2f} >= threshold {settings.text_threshold:.2f}")
    if image_flagged:
        flagged_count = sum(1 for r in image_results if r.flagged)
        reasons.append(
            f"{flagged_count} image(s) scored >= threshold {settings.image_threshold:.2f} "
            f"(max {max_image_score:.2f})"
        )
    if text_timed_out:
        reasons.append(
            f"text model exceeded its {settings.text_budget_ms:.0f}ms budget; escalating "
            "rather than passing an unscreened article"
        )
    if not text_scored:
        reasons.append("no extracted body text; screened on images only")
    if images_timed_out:
        reasons.append("some images were abandoned at the deadline; not escalating on images alone")
    elif images_unexamined:
        first = next((r.reason for r in image_risks if not r.scored), "unknown")
        reasons.append(
            f"none of the {images_requested} image(s) could be examined ({first}); "
            "this page was NOT cleared on images"
        )
    degraded_reason = text_risk.degraded_reason if text_risk else None
    if degraded_reason:
        reasons.append(f"text backend degraded: {degraded_reason}")
    if not escalate and not reasons:
        reasons.append(
            f"text {text_score:.2f} and images (max {max_image_score:.2f}) both below "
            "threshold; stopping at Tier 2"
        )

    return ScreeningResult(
        url=page.url,
        text_score=text_score,
        text_flagged=text_flagged,
        image_results=image_results,
        max_image_score=max_image_score,
        image_flagged=image_flagged,
        escalate_to_tier3=escalate,
        reasons=reasons,
        degraded=(
            bool(degraded_reason)
            or not text_scored
            or text_timed_out
            or images_timed_out
            or images_unexamined
        ),
        text_scored=text_scored and text_risk is not None,
        images_timed_out=images_timed_out,
        latency_ms=latency_ms,
        within_latency_budget=latency_ms <= settings.screen_budget_ms,
        model_meta={
            "ws": "WS4",
            "tier": 2,
            "screening_mode": settings.screening_mode,
            "text_backend": text_risk.backend if text_risk else "not-run",
            "image_backend": image_scorer.name,
            "text_threshold": settings.text_threshold,
            "image_threshold": settings.image_threshold,
            "images_screened": len(image_results),
            "images_scored": images_scored,
            "images_available": len(page.image_urls),
            "budget_ms": settings.screen_budget_ms,
            "text_features": text_risk.features if text_risk else {},
        },
    )
