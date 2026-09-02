"""Tier 2 screening seam -- NOT a real implementation.

Real Tier 2 (BERT text classifier + AI-generated-image detector) lives only on the
unmerged `ws4-tier2-screening` branch (11 commits ahead of main, 13 behind, and its
own commit history documents the fine-tuned text checkpoint flagging 83% of real news
as fake -- docs/ws3/RECON.md §6, §10.2, §10.3). Reimplementing or importing that here is out of
scope for this branch (backend/app/services/ and backend/app/pipeline/ are off limits,
and that's where real Tier 2 code would have to live) and would be irresponsible given
its own authors' stated results.

`screen()` is therefore an honest no-op: whether `tier2_enabled` is True or False, it
returns `None` ("no verdict decided by Tier 2, proceed to Tier 3") every time. The seam
-- the config flag, the call site in pipeline.py, the return-type contract a real
screener would have to satisfy (an `ArticleVerdict` to short-circuit Tier 3, or `None`
to fall through) -- exists so wiring in a real Tier 2 later is a one-function change in
this file, not a pipeline change.
"""
from __future__ import annotations

from app.models.contract import ArticleVerdict


def screen(*, text: str, tier2_enabled: bool) -> ArticleVerdict | None:
    """Return a decided ArticleVerdict to short-circuit Tier 3, or None to proceed to
    it. Always returns None today -- see module docstring. `text` is accepted (and
    otherwise unused) so the signature already matches what a real screener needs."""
    del text  # unused today; kept in the signature for the future real implementation
    if not tier2_enabled:
        return None
    # Flag is on, but there is no real screener behind it yet (see module docstring).
    # Fall through to Tier 3 rather than fabricate a verdict.
    return None
