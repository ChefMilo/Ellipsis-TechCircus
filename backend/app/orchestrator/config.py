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
    # fine-tuned text classifier plus an AI-generated-image CNN, in parallel, inside a
    # ~750ms budget. When Tier 2 clears a page, Tier 3 NEVER RUNS for it and the reader
    # gets UNRATED with the reason in the summary.
    #
    # ON by default. This was False while the screener was a no-op and while its numbers
    # could not be trusted; both have changed. Measured on 1,200 articles (600 fake, 600
    # BBC published 2024-25 — after the model was trained, so the real half is unseen):
    # AUROC 0.992, and at the shipped 0.995 threshold precision 99.8% / recall 90.2% —
    # ONE false positive in 600 real articles. Against Tier 3's ~120s and ~$0.35 per
    # article, gating is what makes the cascade's economics real.
    #
    # THE HONEST RESIDUAL RISK, because turning this on is not free: recall is measured
    # against ISOT, which WELFake absorbs and this model trained on, so 90.2% is partly
    # memory. Recall on fake articles it has NEVER seen is unmeasured — no public corpus
    # of post-2023 labelled fake articles exists (backend/WS4.md). A page Tier 2 clears
    # is a page nobody checks, so the cost of that unknown falls on the reader, not the
    # budget. Set DASFAX_TIER2_ENABLED=0 to route everything to Tier 3 unconditionally.
    tier2_enabled: bool = True

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
    tier3_timeout_s: float = 120.0

    # Outer wall-clock bound on the Tier 2 screen, in seconds. WS4 already enforces its
    # own deadline internally (DASFAX_SCREEN_BUDGET_MS, 750ms by default); this is the
    # orchestrator's independent guard for the case where that deadline does not hold --
    # a wedged image fetch, a model load on a cold cache. Comfortably above WS4's budget
    # so it never fires in normal operation, and low enough that a hung screen costs the
    # request far less than Tier 3's own 120s. Exceeding it fails open to Tier 3.
    tier2_timeout_s: float = 5.0

    # How long a successfully-assembled AnalysisResponse is served from the in-memory
    # cache before a repeat (url, text) request re-runs Tier 3. Failed results are
    # never cached (see pipeline.py) regardless of this TTL.
    cache_ttl_s: float = 300.0


def get_orchestrator_settings() -> OrchestratorSettings:
    """Build settings from the CURRENT environment.

    Env is read here, not in the dataclass field defaults: defaults in a class body are
    evaluated once at import, which would freeze DASFAX_TIER2_ENABLED at whatever it was
    when this module first loaded. That makes the documented escape hatch unreliable and
    untestable — the field defaults below are the documented fallbacks, and this overlays
    the environment on top of them.
    """
    return OrchestratorSettings(
        tier2_enabled=_get_bool("DASFAX_TIER2_ENABLED", True),
        tier2_timeout_s=_get_float("DASFAX_TIER2_TIMEOUT_S", 5.0),
        tier3_timeout_s=_get_float("DASFAX_TIER3_TIMEOUT_S", 120.0),
        cache_ttl_s=_get_float("DASFAX_CACHE_TTL_S", 300.0),
    )
