"""WS6 assessment tests.

The load-bearing ones are the invariants, not the happy path:
  * proposal §2.5 — an affirmative verdict without a citation is impossible, whatever
    the provider returns (enforced in the service, so this holds for the real assessor
    too);
  * citations are BOUND to the claim's own evidence — a provider cannot invent a source;
  * the article rollup never says OK about a page nothing was supported on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.clients.base import AssessedClaim
from app.clients.mock_assessor import MockAssessorClient
from app.models.contract import (
    ArticleInput,
    ArticleVerdictLevel,
    Assessment,
    AssessmentStatus,
    Claim,
    ClaimType,
    Evidence,
)
from app.pipeline.ws5 import run_ws5
from app.services.assessment import (
    CITATION_REQUIRED_STATUSES,
    assess_claim,
    assess_claims,
)
from app.services.envelope import (
    PENDING_VERDICT_LEVEL,
    UNVERIFIED_SUMMARY,
    article_verdict_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_article.txt"


def _evidence(snippet: str, url: str = "https://src.example.org/a") -> Evidence:
    return Evidence(snippet=snippet, source_url=url, source_title="T", source_domain="src.example.org")


def _claim(
    claim_id: str = "c1",
    *,
    evidence: list[Evidence] | None = None,
    claim_type: ClaimType = ClaimType.FACTUAL,
) -> Claim:
    return Claim(
        id=claim_id,
        text="Singapore recorded 3,363 impersonation scam cases in 2025.",
        claim_type=claim_type,
        checkworthiness=0.9,
        rank=1,
        evidence=evidence if evidence is not None else [],
    )


class _ScriptedAssessor:
    """Returns whatever a test dictates — stands in for a misbehaving provider."""

    name = "scripted-test-assessor"

    def __init__(self, result: AssessedClaim) -> None:
        self._result = result

    def assess(self, claim_text: str, evidence: list[Evidence]) -> AssessedClaim:
        return self._result


@pytest.fixture
def real_claims() -> list[Claim]:
    body = FIXTURE.read_text(encoding="utf-8").split("\n", 1)[1].strip()
    return run_ws5(ArticleInput(url="https://news.example.org/sg/x", text=body)).claims


# --------------------------------------------------------------------------- #
# End-to-end over real WS5 output
# --------------------------------------------------------------------------- #
def test_every_claim_gets_an_assessment(real_claims: list[Claim]):
    assessments = assess_claims(real_claims)
    assert set(assessments) == {c.id for c in real_claims}
    for claim in real_claims:
        a = assessments[claim.id]
        assert a.claim_id == claim.id
        assert a.status in set(AssessmentStatus)
        assert a.explanation.strip()


def test_section_2_5_invariant_holds(real_claims: list[Claim]):
    for a in assess_claims(real_claims).values():
        if a.status in CITATION_REQUIRED_STATUSES:
            assert a.citations, f"{a.status} with no citation violates §2.5"
        else:
            assert a.status in (AssessmentStatus.NEEDS_REVIEW, AssessmentStatus.OPINION)


def test_citations_are_bound_to_that_claims_own_evidence(real_claims: list[Claim]):
    assessments = assess_claims(real_claims)
    for claim in real_claims:
        own_urls = {e.source_url for e in claim.evidence}
        own_snippets = {e.snippet for e in claim.evidence}
        for citation in assessments[claim.id].citations:
            assert citation.source_url in own_urls, "citation source not in this claim's evidence"
            assert citation.snippet in own_snippets, "citation snippet was not copied verbatim"


def test_mock_produces_varied_statuses(real_claims: list[Claim]):
    # The whole point of the fixture: exercise more than one branch of WS2's UI.
    statuses = {a.status for a in assess_claims(real_claims).values()}
    assert len(statuses) > 1, f"expected variety, got {statuses}"


def test_deterministic(real_claims: list[Claim]):
    a = assess_claims(real_claims)
    b = assess_claims(real_claims)
    assert {k: v.model_dump() for k, v in a.items()} == {k: v.model_dump() for k, v in b.items()}


# --------------------------------------------------------------------------- #
# Mock assessor stance reading
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("snippets", "expected"),
    [
        (["Records indicate that X.", "Reporting corroborates that X."], AssessmentStatus.SUPPORTED),
        (["One analysis disputes that X.", "One analysis disputes that X."], AssessmentStatus.CONTRADICTED),
        (["Records indicate that X.", "One analysis disputes that X."], AssessmentStatus.PARTIALLY_SUPPORTED),
        (["Records indicate that X.", "Records indicate that X.", "One analysis disputes that X."], AssessmentStatus.SUPPORTED),
        (["A totally ordinary snippet with no fixture prefix."], AssessmentStatus.NEEDS_REVIEW),
    ],
)
def test_stance_counting(snippets: list[str], expected: AssessmentStatus):
    claim = _claim(evidence=[_evidence(s, f"https://src.example.org/{i}") for i, s in enumerate(snippets)])
    assert assess_claim(claim, client=MockAssessorClient()).status == expected


def test_real_snippets_yield_needs_review_not_a_fake_verdict():
    # A real Tavily snippet has no fixture prefix; the fixture must say so rather than guess.
    claim = _claim(evidence=[_evidence("Singapore's police reported a rise in scam cases last year.")])
    a = assess_claim(claim, client=MockAssessorClient())
    assert a.status == AssessmentStatus.NEEDS_REVIEW
    assert a.citations == []


# --------------------------------------------------------------------------- #
# Service-enforced rules
# --------------------------------------------------------------------------- #
def test_non_factual_claim_is_opinion_without_citations():
    for claim_type in (ClaimType.OPINION, ClaimType.PREDICTION):
        claim = _claim(claim_type=claim_type, evidence=[_evidence("Records indicate that X.")])
        a = assess_claim(claim, client=MockAssessorClient())
        assert a.status == AssessmentStatus.OPINION
        assert a.citations == []


def test_claim_with_no_evidence_is_needs_review():
    a = assess_claim(_claim(evidence=[]), client=MockAssessorClient())
    assert a.status == AssessmentStatus.NEEDS_REVIEW
    assert a.citations == []


def test_affirmative_verdict_without_citations_is_downgraded():
    # A provider claiming SUPPORTED while citing nothing must not be trusted.
    rogue = _ScriptedAssessor(AssessedClaim(status="supported", evidence_indices=[], confidence=0.99))
    a = assess_claim(_claim(evidence=[_evidence("Records indicate that X.")]), client=rogue)
    assert a.status == AssessmentStatus.NEEDS_REVIEW
    assert a.citations == []
    assert a.confidence is None


def test_out_of_range_evidence_indices_are_dropped_then_downgraded():
    rogue = _ScriptedAssessor(AssessedClaim(status="contradicted", evidence_indices=[7, -1]))
    a = assess_claim(_claim(evidence=[_evidence("Records indicate that X.")]), client=rogue)
    assert a.status == AssessmentStatus.NEEDS_REVIEW


def test_partially_valid_indices_keep_the_verdict():
    rogue = _ScriptedAssessor(AssessedClaim(status="supported", evidence_indices=[0, 99]))
    a = assess_claim(_claim(evidence=[_evidence("Records indicate that X.")]), client=rogue)
    assert a.status == AssessmentStatus.SUPPORTED
    assert len(a.citations) == 1


# --------------------------------------------------------------------------- #
# Article-level rollup
# --------------------------------------------------------------------------- #
def _assessment(status: AssessmentStatus, claim_id: str = "c1") -> Assessment:
    return Assessment(claim_id=claim_id, status=status, explanation="x")


def test_rollup_any_contradicted_is_high_risk():
    v = article_verdict_for([
        _assessment(AssessmentStatus.SUPPORTED),
        _assessment(AssessmentStatus.CONTRADICTED),
    ])
    assert v.level == ArticleVerdictLevel.HIGH_RISK
    assert "contradicted" in v.summary


def test_rollup_partial_is_caution():
    v = article_verdict_for([
        _assessment(AssessmentStatus.SUPPORTED),
        _assessment(AssessmentStatus.PARTIALLY_SUPPORTED),
    ])
    assert v.level == ArticleVerdictLevel.CAUTION


def test_rollup_all_supported_is_ok():
    v = article_verdict_for([_assessment(AssessmentStatus.SUPPORTED) for _ in range(3)])
    assert v.level == ArticleVerdictLevel.OK
    assert v.summary == "3 claims supported."


def test_rollup_all_needs_review_is_unrated():
    # Nothing supported and nothing disputed: neutral, not a warning. CAUTION here would
    # false-alarm on a page there is no evidence against.
    v = article_verdict_for([_assessment(AssessmentStatus.NEEDS_REVIEW) for _ in range(2)])
    assert v.level == ArticleVerdictLevel.UNRATED
    assert v.level != ArticleVerdictLevel.OK
    assert v.level != ArticleVerdictLevel.CAUTION
    assert UNVERIFIED_SUMMARY in v.summary, "the summary must still say why"


def test_rollup_all_opinion_is_unrated():
    v = article_verdict_for([_assessment(AssessmentStatus.OPINION)])
    assert v.level == ArticleVerdictLevel.UNRATED
    assert v.level != ArticleVerdictLevel.OK


def test_rollup_mixed_unverified_and_opinion_is_unrated():
    v = article_verdict_for([
        _assessment(AssessmentStatus.NEEDS_REVIEW),
        _assessment(AssessmentStatus.OPINION),
    ])
    assert v.level == ArticleVerdictLevel.UNRATED


def test_rollup_unrated_still_passes_the_ws2_guard():
    # WS2's VERDICT_LEVELS now includes "unrated"; the serialized level must be that
    # exact spelling or isAnalysisResponse() drops the whole envelope.
    v = article_verdict_for([_assessment(AssessmentStatus.NEEDS_REVIEW)])
    assert v.model_dump(mode="json")["level"] == "unrated"


def test_rollup_supported_plus_needs_review_is_ok():
    # Something was genuinely supported and nothing was worse than unverified.
    v = article_verdict_for([
        _assessment(AssessmentStatus.SUPPORTED),
        _assessment(AssessmentStatus.NEEDS_REVIEW),
    ])
    assert v.level == ArticleVerdictLevel.OK


def test_rollup_of_nothing_is_the_unrated_verdict():
    v = article_verdict_for([])
    assert v.level == PENDING_VERDICT_LEVEL == ArticleVerdictLevel.UNRATED
    assert v.level != ArticleVerdictLevel.OK


def test_summary_pluralisation():
    assert article_verdict_for([_assessment(AssessmentStatus.CONTRADICTED)]).summary == "1 claim contradicted."
