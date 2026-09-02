"""One test per compute_article_verdict() branch (app/orchestrator/verdict.py).

Pure unit tests: hand-built Assessment objects in, ArticleVerdict out. No FastAPI, no
run_ws5, no I/O.
"""
from __future__ import annotations

from app.models.contract import ArticleVerdictLevel, Assessment, AssessmentStatus
from app.orchestrator.verdict import UNRATED_SUMMARY, compute_article_verdict


def _assessment(status: AssessmentStatus, claim_id: str = "c1") -> Assessment:
    return Assessment(claim_id=claim_id, status=status, explanation="x", citations=[])


# --------------------------------------------------------------------------- #
# Branch 1: every assessment None -> unrated, confidence None
# --------------------------------------------------------------------------- #
def test_all_none_is_unrated_with_null_confidence():
    verdict = compute_article_verdict([None, None, None])
    assert verdict.level == ArticleVerdictLevel.UNRATED
    assert verdict.confidence is None
    assert verdict.summary == UNRATED_SUMMARY


def test_empty_iterable_is_also_unrated():
    # Zero claims is a degenerate case of "every assessment is None".
    verdict = compute_article_verdict([])
    assert verdict.level == ArticleVerdictLevel.UNRATED
    assert verdict.confidence is None


# --------------------------------------------------------------------------- #
# Branch 2: any CONTRADICTED -> high_risk, regardless of what else is present
# --------------------------------------------------------------------------- #
def test_any_contradicted_is_high_risk():
    verdict = compute_article_verdict(
        [
            _assessment(AssessmentStatus.SUPPORTED, "c1"),
            _assessment(AssessmentStatus.CONTRADICTED, "c2"),
            _assessment(AssessmentStatus.PARTIALLY_SUPPORTED, "c3"),
            None,  # a claim that was never assessed must not dilute/mask the verdict
        ]
    )
    assert verdict.level == ArticleVerdictLevel.HIGH_RISK
    assert verdict.confidence == 0.33  # 1 contradicted / 3 decided, rounded


# --------------------------------------------------------------------------- #
# Branch 3: no contradicted, but any PARTIALLY_SUPPORTED or NEEDS_REVIEW -> caution
# --------------------------------------------------------------------------- #
def test_partially_supported_without_contradicted_is_caution():
    verdict = compute_article_verdict(
        [
            _assessment(AssessmentStatus.SUPPORTED, "c1"),
            _assessment(AssessmentStatus.PARTIALLY_SUPPORTED, "c2"),
        ]
    )
    assert verdict.level == ArticleVerdictLevel.CAUTION
    assert verdict.confidence == 0.5


def test_needs_review_without_contradicted_is_also_caution():
    verdict = compute_article_verdict([_assessment(AssessmentStatus.NEEDS_REVIEW, "c1")])
    assert verdict.level == ArticleVerdictLevel.CAUTION
    assert verdict.confidence == 1.0


# --------------------------------------------------------------------------- #
# Branch 4: everything else (only SUPPORTED and/or OPINION) -> ok
# --------------------------------------------------------------------------- #
def test_all_supported_is_ok():
    verdict = compute_article_verdict(
        [_assessment(AssessmentStatus.SUPPORTED, "c1"), _assessment(AssessmentStatus.SUPPORTED, "c2")]
    )
    assert verdict.level == ArticleVerdictLevel.OK
    assert verdict.confidence == 1.0


def test_supported_plus_opinion_is_ok():
    verdict = compute_article_verdict(
        [_assessment(AssessmentStatus.SUPPORTED, "c1"), _assessment(AssessmentStatus.OPINION, "c2")]
    )
    assert verdict.level == ArticleVerdictLevel.OK
    assert verdict.confidence == 0.5


def test_never_returns_a_bare_bool():
    verdict = compute_article_verdict([_assessment(AssessmentStatus.SUPPORTED)])
    # The task's explicit rule: always the 3-field object, never a binary true/fake.
    assert isinstance(verdict.level, str)
    assert isinstance(verdict.summary, str) and verdict.summary
    assert not isinstance(verdict, bool)
