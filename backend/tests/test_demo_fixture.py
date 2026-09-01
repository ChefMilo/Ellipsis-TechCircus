"""Locks the pitch demo.

The live demo runs offline on `fixtures/demo_article.txt` with the mock search client's
presentation profiles switched on, and must visibly produce all four assessable statuses.
That spread depends on the whole chain — sentence splitting, checkworthiness ranking,
stance profiles, the assessor's majority rule, the §2.5 downgrade — so any of those
drifting would quietly flatten the demo. These tests fail instead.

`opinion` is deliberately out of scope: WS5's rank_factual drops non-factual claims
before retrieval, so it is unreachable end to end today.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.clients.mock_assessor import MockAssessorClient
from app.clients.mock_llm import MockLLMClient
from app.clients.mock_search import MockSearchClient
from app.models.contract import AnalysisStatus, ArticleInput, ArticleVerdictLevel, AssessmentStatus
from app.pipeline.ws5 import run_ws5
from app.services.assessment import assess_claims
from app.services.envelope import to_analysis_response
from tests.test_analyze import assert_passes_ws2_guard

DEMO_FIXTURE = Path(__file__).parent / "fixtures" / "demo_article.txt"
DEMO_URL = "https://news.example.org/sg/scam-rules-2025"

# The four statuses a live offline run must show. OPINION is excluded (see module docstring).
REQUIRED_STATUSES = {
    AssessmentStatus.SUPPORTED,
    AssessmentStatus.CONTRADICTED,
    AssessmentStatus.PARTIALLY_SUPPORTED,
    AssessmentStatus.NEEDS_REVIEW,
}


def _run_demo():
    raw = DEMO_FIXTURE.read_text(encoding="utf-8")
    title, body = raw.split("\n", 1)
    article = ArticleInput(
        url=DEMO_URL, title=title.strip(), text=body.strip(), source_domain="news.example.org"
    )
    result = run_ws5(article, llm=MockLLMClient(), search=MockSearchClient(demo_spread=True))
    assessments = assess_claims(result.claims, client=MockAssessorClient())
    return to_analysis_response(result, status=AnalysisStatus.COMPLETE, assessments=assessments)


@pytest.fixture
def envelope():
    return _run_demo()


def test_demo_shows_every_assessable_status(envelope):
    present = {vc.assessment.status for vc in envelope.verifiedClaims if vc.assessment}
    missing = REQUIRED_STATUSES - present
    assert not missing, f"demo no longer shows {sorted(s.value for s in missing)}"


def test_demo_envelope_passes_the_ws2_guard(envelope):
    assert_passes_ws2_guard(envelope.model_dump(mode="json"))


def test_demo_article_verdict_is_not_a_clean_bill_of_health(envelope):
    # At least one claim is contradicted, so the rollup must be high_risk — and under no
    # circumstances "ok".
    assert envelope.articleVerdict.level != ArticleVerdictLevel.OK
    assert envelope.articleVerdict.level == ArticleVerdictLevel.HIGH_RISK
    assert envelope.articleVerdict.summary.strip()


def test_demo_yields_enough_claims_to_cover_the_profiles(envelope):
    # Four profiles are cycled one per claim, so fewer than four claims cannot span them.
    assert len(envelope.verifiedClaims) >= 4


def test_demo_is_deterministic():
    assert _run_demo().model_dump(mode="json") == _run_demo().model_dump(mode="json")


def test_section_2_5_still_holds_on_the_demo(envelope):
    for vc in envelope.verifiedClaims:
        assessment = vc.assessment
        assert assessment is not None
        if assessment.status in {
            AssessmentStatus.SUPPORTED,
            AssessmentStatus.PARTIALLY_SUPPORTED,
            AssessmentStatus.CONTRADICTED,
        }:
            assert assessment.citations
        elif assessment.status is AssessmentStatus.NEEDS_REVIEW:
            # The needs_review claim is the unstanced one; it must cite nothing rather
            # than dress up evidence it could not read.
            assert assessment.citations == []


def test_demo_mode_is_opt_in_and_default_search_is_unchanged():
    default = MockSearchClient()
    assert default.name == "mock-search-seeded-v1"
    assert default.demo_spread is False
    # Every default snippet still carries one of the three original stance prefixes.
    for hit in default.search("some claim text", max_results=3):
        assert hit.snippet.startswith(
            ("Records indicate that", "Reporting corroborates that", "One analysis disputes that")
        )
