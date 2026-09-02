"""Runtime configuration for WS3's /analyze orchestrator.

Deliberately separate from app/config.py (WS5's Settings): app/config.py is out of
scope for this branch (backend/app/services/ and backend/app/pipeline/ own its
consumers), and every knob here is specific to orchestration -- Tier 2 gating, Tier 3's
timeout, the result cache's TTL -- not to WS5's extraction/retrieval tuning. Same
env-var-with-default pattern as app/config.py so the two read the same way at a glance.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class OrchestratorSettings:
    # Tier 2 screening gate (app/orchestrator/tier2.py -> app/pipeline/ws4.py): a
    # fine-tuned BERT text classifier plus an AI-generated-image CNN, in parallel, inside
    # a ~750ms budget. When it clears a page, Tier 3 NEVER RUNS for that page and the
    # reader gets UNRATED with the reason in the summary.
    #
    # OFF by default, and that is a product decision rather than a doubt about the wiring.
    # Turning it on is what the cascade is for -- it is the only thing that makes Tier 3's
    # ~120s and per-article API spend conditional instead of universal -- but it changes
    # what the product DOES: the text half flags roughly 0-11% of ordinary news (0.0% on
    # the heuristic, 10.6% on our fine-tune, measured over 480 AG News articles), so with
    # this on, most real articles stop here and are never fact-checked at all. That is
    # correct cascade behaviour and the wrong thing to discover during a live demo.
    #
    # The 83% figure in docs/ws3/RECON.md §10.3 refers to omykhailiv/bert-fake-news-
    # recognition off the shelf, which is why that checkpoint was abandoned; it does not
    # describe what runs today. See backend/WS4.md for what the shipped models do and do
    # not detect. 1/true/yes/on to enable.
    tier2_enabled: bool = _get_bool("DASFAX_TIER2_ENABLED", False)

    # Wall-clock budget for the Tier 3 step (run_ws5 + WS6 assessment) inside one
    # /analyze call. Near-instant on the mock backends; sized for the REAL provider
    # path, which is sequential by construction -- app/pipeline/ws5.py loops one search
    # per kept claim and app/services/assessment.py loops one assessment per claim, so
    # DASFAX_MAX_CLAIMS=5 means ~11 model calls end to end, several of them web
    # searches. The previous 8s budget could not finish that, and a timeout is not a
    # loud failure here: pipeline.py returns a perfectly valid envelope reading
    # UNRATED / "nothing checked", which on screen is indistinguishable from "this
    # article had no checkable claims". Tune per venue with DASFAX_TIER3_TIMEOUT_S.
    #
    # Stays BELOW the extension's own client-side timeout (src/background/config.ts's
    # DEFAULT_TIMEOUT_MS, 130000ms) on purpose, so the backend fails the request first
    # and answers with errors[{code: "tier3_timeout"}] -- a diagnosable cause -- rather
    # than the client aborting blind and reporting a generic "unreachable". Raise both,
    # in that order, if you raise either.
    tier3_timeout_s: float = _get_float("DASFAX_TIER3_TIMEOUT_S", 120.0)

    # Outer wall-clock bound on the Tier 2 screen, in seconds. WS4 already enforces its
    # own deadline internally (DASFAX_SCREEN_BUDGET_MS, 750ms by default); this is the
    # orchestrator's independent guard for the case where that deadline does not hold --
    # a wedged image fetch, a model load on a cold cache. Comfortably above WS4's budget
    # so it never fires in normal operation, and low enough that a hung screen costs the
    # request far less than Tier 3's own 120s. Exceeding it fails open to Tier 3.
    tier2_timeout_s: float = _get_float("DASFAX_TIER2_TIMEOUT_S", 5.0)

    # How long a successfully-assembled AnalysisResponse is served from the in-memory
    # cache before a repeat (url, text) request re-runs Tier 3. Failed results are
    # never cached (see pipeline.py) regardless of this TTL.
    cache_ttl_s: float = _get_float("DASFAX_CACHE_TTL_S", 300.0)


def get_orchestrator_settings() -> OrchestratorSettings:
    return OrchestratorSettings()
