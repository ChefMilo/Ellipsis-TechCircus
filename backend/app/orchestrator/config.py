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
    # /analyze call. Generous for the mock backends (near-instant) while still
    # bounding a hung real provider call (OpenAI / Tavily) to something the
    # extension's own UX can tolerate (backend-client.ts's own client-side timeout
    # defaults to 15s -- this is deliberately shorter so the backend fails the request
    # itself, with a real errors[] entry, well before the extension's own timeout
    # would fire and mask it as a generic "unreachable").
    tier3_timeout_s: float = _get_float("DASFAX_TIER3_TIMEOUT_S", 8.0)

    # How long a successfully-assembled AnalysisResponse is served from the in-memory
    # cache before a repeat (url, text) request re-runs Tier 3. Failed results are
    # never cached (see pipeline.py) regardless of this TTL.
    cache_ttl_s: float = _get_float("DASFAX_CACHE_TTL_S", 300.0)


def get_orchestrator_settings() -> OrchestratorSettings:
    return OrchestratorSettings()
