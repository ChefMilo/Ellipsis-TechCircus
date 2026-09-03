"""Tier 2 seam: app/orchestrator/tier2.py, and its wiring into run_analysis.

Covers the three things that would be silently wrong if broken: the escalate/clear
mapping, the honesty rule (a Tier 2 clear is never OK), and fail-open on every failure
path. No models, no network -- the screener is injected.
"""
from __future__ import annotations

import asyncio

from app.models.contract import AnalysisRequest, AnalysisStatus, ArticleVerdictLevel
from app.models.screening import ScreeningInput, ScreeningResult
from app.orchestrator import tier2
from app.orchestrator.cache import InMemoryTTLCache
from app.orchestrator.config import OrchestratorSettings, get_orchestrator_settings
from app.orchestrator.pipeline import run_analysis

TEXT = "The agency said dengue cases fell to 214 in the week ending 30 August."

def _result(*, escalate: bool, text_score: float = 0.1, **kw) -> ScreeningResult:
    return ScreeningResult(
        url="https://example.com/a",
        text_score=text_score,
        text_flagged=escalate,
        image_results=[],
        max_image_score=0.0,
        image_flagged=False,
        escalate_to_tier3=escalate,
        reasons=["test"],
        latency_ms=1.0,
        within_latency_budget=True,
        model_meta={},
        **kw,
    )

def _screener(result=None, *, raises: Exception | None = None, spy: list | None = None):
    def run(page: ScreeningInput) -> ScreeningResult:
        if spy is not None:
            spy.append(page)
        if raises is not None:
            raise raises
        return result
    return run

# --------------------------------------------------------------------------- #
# screen(): the mapping
# --------------------------------------------------------------------------- #
def test_disabled_flag_never_calls_the_screener():
    spy: list = []
    verdict = tier2.screen(
        url="https://example.com/a", title=None, text=TEXT,
        tier2_enabled=False, screener=_screener(_result(escalate=False), spy=spy),
    )
    assert verdict is None          # proceed to Tier 3
    assert spy == []                # and do not pay for a screen we were told to skip

def test_escalate_returns_none_so_tier3_runs():
    verdict = tier2.screen(
        url="https://example.com/a", title="T", text=TEXT,
        tier2_enabled=True, screener=_screener(_result(escalate=True, text_score=0.99)),
    )
    assert verdict is None

def test_clear_short_circuits_as_unrated_not_ok():
    """The honesty rule. Tier 2 clearing a page means 'not worth Tier 3', NOT 'true'.
    ArticleVerdictLevel reserves OK for a Tier 3 rollup where claims were checked."""
    verdict = tier2.screen(
        url="https://example.com/a", title="T", text=TEXT,
        tier2_enabled=True, screener=_screener(_result(escalate=False)),
    )
    assert verdict is not None
    assert verdict.level is ArticleVerdictLevel.UNRATED
    assert verdict.level is not ArticleVerdictLevel.OK
    # No fabricated certainty: WS4 emits P(risk), which is not a verdict confidence.
    assert verdict.confidence is None
    # The summary must say what happened, not imply a finding.
    assert "not verified" in verdict.summary

def test_page_envelope_reaches_the_screener_intact():
    """Regression: the seam used to accept `text` only, so title and images could never
    reach the screener and the image half of Tier 2 was structurally dead."""
    spy: list = []
    tier2.screen(
        url="https://example.com/a", title="Headline", text=TEXT,
        image_urls=["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
        tier2_enabled=True, screener=_screener(_result(escalate=False), spy=spy),
    )
    (page,) = spy
    assert page.url == "https://example.com/a"
    assert page.title == "Headline"
    assert page.text == TEXT
    assert page.image_urls == ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"]

def test_none_images_is_not_an_error():
    verdict = tier2.screen(
        url="https://example.com/a", title=None, text=TEXT, image_urls=None,
        tier2_enabled=True, screener=_screener(_result(escalate=False)),
    )
    assert verdict is not None

def test_screener_raising_fails_open_to_tier3():
    verdict = tier2.screen(
        url="https://example.com/a", title=None, text=TEXT,
        tier2_enabled=True, screener=_screener(raises=RuntimeError("model exploded")),
    )
    assert verdict is None

# --------------------------------------------------------------------------- #
# run_analysis(): the wiring
# --------------------------------------------------------------------------- #
def _request() -> AnalysisRequest:
    return AnalysisRequest(url="https://example.com/a", title="T", text=TEXT)

def _settings(**kw) -> OrchestratorSettings:
    return OrchestratorSettings(**kw)

def test_analysis_skips_tier3_when_tier2_clears(monkeypatch):
    called: list = []
    monkeypatch.setattr(
        tier2, "screen",
        lambda **kw: tier2._verdict_for_clear(_result(escalate=False)),
    )
    monkeypatch.setattr(
        "app.orchestrator.pipeline._run_tier3_sync",
        lambda article: called.append(article),
    )
    resp = asyncio.run(run_analysis(
        _request(), settings=_settings(tier2_enabled=True), cache=InMemoryTTLCache(ttl_s=60),
    ))
    assert called == []                                       # Tier 3 never ran
    assert resp.status is AnalysisStatus.COMPLETE
    assert resp.articleVerdict.level is ArticleVerdictLevel.UNRATED
    assert resp.verifiedClaims == []
    assert resp.errors == []

def test_analysis_runs_tier3_when_tier2_escalates(monkeypatch):
    monkeypatch.setattr(tier2, "screen", lambda **kw: None)
    resp = asyncio.run(run_analysis(
        _request(), settings=_settings(tier2_enabled=True), cache=InMemoryTTLCache(ttl_s=60),
    ))
    assert resp.status is AnalysisStatus.COMPLETE
    assert resp.verifiedClaims                                 # Tier 3 produced claims

def test_analysis_falls_open_when_the_seam_raises(monkeypatch):
    """A broken Tier 2 must cost Tier 3 compute, never the reader's fact-check."""
    def boom(**kw):
        raise RuntimeError("seam exploded")
    monkeypatch.setattr(tier2, "screen", boom)
    resp = asyncio.run(run_analysis(
        _request(), settings=_settings(tier2_enabled=True), cache=InMemoryTTLCache(ttl_s=60),
    ))
    assert resp.status is AnalysisStatus.COMPLETE
    assert resp.verifiedClaims                                 # Tier 3 still ran

def test_analysis_falls_open_when_the_screen_overruns(monkeypatch):
    """The orchestrator's own guard above WS4's internal budget."""
    import time
    monkeypatch.setattr(tier2, "screen", lambda **kw: time.sleep(5))
    resp = asyncio.run(run_analysis(
        _request(),
        settings=_settings(tier2_enabled=True, tier2_timeout_s=0.05),
        cache=InMemoryTTLCache(ttl_s=60),
    ))
    assert resp.status is AnalysisStatus.COMPLETE
    assert resp.verifiedClaims                                 # Tier 3 still ran

def test_tier2_is_on_by_default(monkeypatch):
    """The gate ships ON. Pinned as a test because the default decides whether Tier 3
    runs for every page — flipping it silently changes what the product does, not just
    what it costs."""
    monkeypatch.delenv("DASFAX_TIER2_ENABLED", raising=False)
    assert get_orchestrator_settings().tier2_enabled is True


def test_tier2_can_be_disabled_by_env(monkeypatch):
    """The escape hatch has to work: DASFAX_TIER2_ENABLED=0 routes everything to Tier 3."""
    monkeypatch.setenv("DASFAX_TIER2_ENABLED", "0")
    assert get_orchestrator_settings().tier2_enabled is False
