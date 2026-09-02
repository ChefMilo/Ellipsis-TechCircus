"""Orchestrator pipeline tests: run_analysis() -- the no-content path and which
AnalysisStatus values this orchestrator actually produces, published_at threading, a
cache hit, Tier 3 timeout, and Tier 3 raising.

`run_analysis()` is `async def` (so Tier 3 can be time-boxed with asyncio.wait_for --
see pipeline.py). This repo has neither pytest-asyncio nor a configured anyio pytest
backend installed (confirmed: `python -c "import pytest_asyncio"` fails), and adding
one is out of scope ("no new dependencies") -- so every test here drives it with plain
`asyncio.run(...)` inside an ordinary sync `def test_...():` function instead of
`@pytest.mark.asyncio`. No plugin, no config, works anywhere Python does.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from app.models.contract import (
    AnalysisRequest,
    AnalysisStatus,
    ArticleVerdictLevel,
    ClaimExtractionResult,
)
from app.orchestrator.cache import InMemoryTTLCache
from app.orchestrator.config import OrchestratorSettings
from app.orchestrator.pipeline import _build_article_input, run_analysis

ARTICLE_TEXT = (
    "Singapore recorded 3,363 cases of government-official-impersonation scams in "
    "2025, up from 1,504 cases in 2024, according to police."
)


def _settings(**overrides: object) -> OrchestratorSettings:
    base: dict[str, object] = dict(tier2_enabled=False, tier3_timeout_s=8.0, cache_ttl_s=300.0)
    base.update(overrides)
    return OrchestratorSettings(**base)  # type: ignore[arg-type]


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# task 2: published_at (and everything else) survives request -> ArticleInput
# --------------------------------------------------------------------------- #
def test_published_at_and_source_domain_survive_into_article_input():
    request = AnalysisRequest(
        url="https://news.example.org/sg/story",
        title="Headline",
        text=ARTICLE_TEXT,
        published_at="2026-02-01T00:00:00Z",
        source_domain="news.example.org",
    )
    article = _build_article_input(request)

    # This is the field that was previously silently dropped by extra="ignore"
    # (docs/ws3/WS3-CONTRACT-AUDIT.md Task C; docs/ws3/WS3-MESSAGE-HOP.md task 6) -- must now survive.
    assert article.published_at == datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert article.url == request.url
    assert article.title == request.title
    assert article.text == request.text
    # source_domain is deliberately server-derived from `url`, not copied from the
    # client's own guess -- see pipeline.py's _domain_of() docstring and
    # docs/ws3/WS3-ANALYZE-ENVELOPE.md task 2. They happen to agree here, which is the point.
    assert article.source_domain == "news.example.org"


def test_published_at_absent_stays_none():
    request = AnalysisRequest(url="https://news.example.org/x", text=ARTICLE_TEXT)
    article = _build_article_input(request)
    assert article.published_at is None


# --------------------------------------------------------------------------- #
# no-content path -- and confirmation of which status values this code produces
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [None, "", "   \n  "])
def test_no_content_returns_complete_status_with_unrated_verdict(text):
    request = AnalysisRequest(url="https://news.example.org/x", text=text)
    response = _run(run_analysis(request, settings=_settings(), cache=InMemoryTTLCache(ttl_s=300)))

    # "complete", not a new "no_content" status -- Step 0: src/shared/contract.ts's
    # ANALYSIS_STATUSES is a CLOSED set with no such member.
    assert response.status == AnalysisStatus.COMPLETE
    assert response.articleVerdict.level == ArticleVerdictLevel.UNRATED
    assert response.articleVerdict.confidence is None
    assert response.verifiedClaims == []
    assert len(response.errors) == 1
    assert response.errors[0].code == "no_content"
    assert response.errors[0].message == "No article text supplied; nothing to analyze."


def test_successful_analysis_returns_complete_status():
    request = AnalysisRequest(url="https://news.example.org/y", text=ARTICLE_TEXT)
    response = _run(run_analysis(request, settings=_settings(), cache=InMemoryTTLCache(ttl_s=300)))
    assert response.status == AnalysisStatus.COMPLETE
    assert response.errors == []


# --------------------------------------------------------------------------- #
# cache hit
# --------------------------------------------------------------------------- #
def test_repeat_request_is_served_from_cache_without_rerunning_tier3(monkeypatch):
    calls = {"n": 0}

    def counting_run_ws5(article):
        calls["n"] += 1
        return ClaimExtractionResult(url=article.url, title=article.title, claims=[])

    monkeypatch.setattr("app.orchestrator.pipeline.run_ws5", counting_run_ws5)

    request = AnalysisRequest(url="https://news.example.org/cache-me", text=ARTICLE_TEXT)
    cache: InMemoryTTLCache = InMemoryTTLCache(ttl_s=300)
    settings = _settings()

    first = _run(run_analysis(request, settings=settings, cache=cache))
    second = _run(run_analysis(request, settings=settings, cache=cache))

    assert calls["n"] == 1  # Tier 3 ran exactly once
    assert first == second
    assert first is second  # the literal cached object, not just an equal copy

    # A genuinely different request (different text) must NOT hit the same entry.
    other = AnalysisRequest(url="https://news.example.org/cache-me", text=ARTICLE_TEXT + " More.")
    _run(run_analysis(other, settings=settings, cache=cache))
    assert calls["n"] == 2


def test_a_failed_result_is_never_cached(monkeypatch):
    state = {"fail": True}

    def flaky_run_ws5(article):
        if state["fail"]:
            raise RuntimeError("transient failure")
        return ClaimExtractionResult(url=article.url, title=article.title, claims=[])

    monkeypatch.setattr("app.orchestrator.pipeline.run_ws5", flaky_run_ws5)

    request = AnalysisRequest(url="https://news.example.org/flaky", text=ARTICLE_TEXT)
    cache: InMemoryTTLCache = InMemoryTTLCache(ttl_s=300)
    settings = _settings()

    first = _run(run_analysis(request, settings=settings, cache=cache))
    assert first.status == AnalysisStatus.FAILED

    state["fail"] = False
    second = _run(run_analysis(request, settings=settings, cache=cache))
    assert second.status == AnalysisStatus.COMPLETE  # retried Tier 3, not served a cached failure


# --------------------------------------------------------------------------- #
# Tier 3 timeout
# --------------------------------------------------------------------------- #
def test_tier3_timeout_returns_a_valid_failed_envelope(monkeypatch):
    def slow_run_ws5(article):
        time.sleep(0.5)
        return ClaimExtractionResult(url=article.url, title=article.title, claims=[])

    monkeypatch.setattr("app.orchestrator.pipeline.run_ws5", slow_run_ws5)

    request = AnalysisRequest(url="https://news.example.org/slow", text=ARTICLE_TEXT)
    settings = _settings(tier3_timeout_s=0.05)  # far shorter than the 0.5s sleep above

    response = _run(run_analysis(request, settings=settings, cache=InMemoryTTLCache(ttl_s=300)))

    assert response.status == AnalysisStatus.FAILED
    assert response.articleVerdict.level == ArticleVerdictLevel.UNRATED
    assert response.articleVerdict.confidence is None
    assert response.verifiedClaims == []
    assert len(response.errors) == 1
    assert response.errors[0].code == "tier3_timeout"


# --------------------------------------------------------------------------- #
# Tier 3 raising
# --------------------------------------------------------------------------- #
def test_tier3_raising_returns_a_valid_failed_envelope_never_an_exception(monkeypatch):
    def exploding_run_ws5(article):
        raise RuntimeError("boom -- simulated WS5 failure")

    monkeypatch.setattr("app.orchestrator.pipeline.run_ws5", exploding_run_ws5)

    request = AnalysisRequest(url="https://news.example.org/boom", text=ARTICLE_TEXT)
    # run_analysis() must not raise -- if it did, this call itself would fail the test
    # rather than the assertions below getting a chance to run.
    response = _run(run_analysis(request, settings=_settings(), cache=InMemoryTTLCache(ttl_s=300)))

    assert response.status == AnalysisStatus.FAILED
    assert response.articleVerdict.level == ArticleVerdictLevel.UNRATED
    assert response.verifiedClaims == []
    assert response.errors[0].code == "tier3_error"
    assert "boom" in response.errors[0].message


def test_tier2_disabled_by_default_falls_straight_through_to_tier3():
    settings = OrchestratorSettings()  # real defaults, no overrides
    assert settings.tier2_enabled is False
    request = AnalysisRequest(url="https://news.example.org/z", text=ARTICLE_TEXT)
    response = _run(run_analysis(request, settings=settings, cache=InMemoryTTLCache(ttl_s=300)))
    # Reached Tier 3 (real claims came back), not a Tier-2 short-circuit.
    assert response.status == AnalysisStatus.COMPLETE
    assert len(response.verifiedClaims) > 0
