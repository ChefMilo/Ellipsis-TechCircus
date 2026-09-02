"""Tier 2 screening seam: the gate that decides whether a page is worth Tier 3.

Now backed by the real WS4 screener (app/pipeline/ws4.py): a fine-tuned BERT text
classifier and an AI-generated-image CNN, run in parallel against a wall-clock deadline.
`screen()` adapts WS4's `ScreeningResult` onto the contract this seam already had --
an `ArticleVerdict` to short-circuit Tier 3, or `None` to proceed to it.

WHAT A CLEARED PAGE MEANS. Tier 2 not escalating is NOT a finding that the article is
true; it is a decision not to spend Tier 3's seconds and API budget on it. So a clear
returns UNRATED, never OK -- `ArticleVerdictLevel`'s own docstring reserves OK for a
Tier 3 rollup where claims were actually checked and at least one held up, and names
"a Tier 2 screen that cleared the page without escalating" as an UNRATED path. The
reason rides in `summary`, per that contract. `confidence` stays None: WS4 produces a
risk score, not a confidence in a verdict, and populating it here would read as Tier 3
certainty that nobody measured.

FAIL OPEN, ALWAYS. Every failure path -- the flag off, the screener raising, the
screener overrunning -- returns None and lets Tier 3 run. A screening layer that
suppressed fact-checking when it broke would turn its own outage into a silent product
regression: the reader would see "not escalated" for a page nothing ever looked at.
The asymmetry is deliberate and matches WS4's internal policy (app/pipeline/ws4.py),
where a text-model timeout escalates rather than passing an unscreened article.

BLOCKING. `screen()` is synchronous and does network I/O (image fetches) inside WS4's
budget. Callers on an event loop must run it off-thread -- app/orchestrator/pipeline.py
uses asyncio.to_thread, the same way it already treats Tier 3.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from app.models.contract import ArticleVerdict, ArticleVerdictLevel
from app.models.screening import ScreeningInput, ScreeningResult

log = logging.getLogger("dasfax.tier2")

# Shown to the reader when Tier 2 cleared the page. Deliberately says what happened
# ("screened", "not checked") rather than implying a finding ("looks fine", "no issues").
CLEARED_SUMMARY = (
    "This page was screened and did not show the signals that trigger a full fact-check, "
    "so its individual claims were not verified."
)


def _verdict_for_clear(result: ScreeningResult) -> ArticleVerdict:
    return ArticleVerdict(
        level=ArticleVerdictLevel.UNRATED,
        summary=CLEARED_SUMMARY,
        # Not WS4's text_score: that is P(risk), and rendering it as `confidence` would
        # invert its meaning on a page Tier 2 just cleared.
        confidence=None,
    )


def screen(
    *,
    url: str,
    title: str | None,
    text: str,
    image_urls: Sequence[str] | None = None,
    tier2_enabled: bool,
    screener=None,
) -> ArticleVerdict | None:
    """Screen one page at Tier 2.

    Returns an `ArticleVerdict` to short-circuit Tier 3, or `None` to proceed to it.
    `screener` is an injection seam for tests; production uses `run_ws4`.
    """
    if not tier2_enabled:
        return None

    if screener is None:
        from app.pipeline.ws4 import run_ws4

        screener = run_ws4

    try:
        result: ScreeningResult = screener(
            ScreeningInput(
                url=url,
                title=title,
                text=text,
                image_urls=list(image_urls or []),
            )
        )
    except Exception as err:  # noqa: BLE001 -- fail open: see module docstring
        log.warning("Tier 2 screening failed (%s: %s); falling through to Tier 3", type(err).__name__, err)
        return None

    if result.escalate_to_tier3:
        log.info(
            "Tier 2 ESCALATE %s — text %.3f, images max %.2f, %.0fms — %s",
            url, result.text_score, result.max_image_score, result.latency_ms,
            "; ".join(result.reasons) or "no reason given",
        )
        return None

    log.info(
        "Tier 2 STOP %s — text %.3f, images max %.2f, %.0fms — Tier 3 not run",
        url, result.text_score, result.max_image_score, result.latency_ms,
    )
    return _verdict_for_clear(result)
