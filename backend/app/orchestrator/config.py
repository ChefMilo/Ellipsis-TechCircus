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
    # Tier 2 (BERT text classifier + AI-generated-image detector) does not exist in
    # this tree -- see docs/ws3/RECON.md §6 / §10.2. The branch that has it
    # (ws4-tier2-screening) is 11 commits ahead of main, 13 behind, unmerged, and its
    # own commit history says the fine-tuned text checkpoint flags 83% of real news as
    # fake (docs/ws3/RECON.md §10.3). Defaulting this OFF is a safety decision, not a
    # placeholder oversight: flipping it on today does not run real Tier 2 screening
    # (see orchestrator/tier2.py's module docstring), it only activates a seam that
    # still falls straight through to Tier 3.
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

    # How long a successfully-assembled AnalysisResponse is served from the in-memory
    # cache before a repeat (url, text) request re-runs Tier 3. Failed results are
    # never cached (see pipeline.py) regardless of this TTL.
    cache_ttl_s: float = _get_float("DASFAX_CACHE_TTL_S", 300.0)


def get_orchestrator_settings() -> OrchestratorSettings:
    return OrchestratorSettings()
