"""Tests for POST /analyze — the client-facing envelope the extension renders.

The bar here is not "does FastAPI return 200". It is "would WS2's isAnalysisResponse()
guard accept this?", because a response that fails that guard is silently dropped by the
renderer with no error anywhere. `assert_passes_ws2_guard` below re-encodes that guard
(src/shared/contract.ts) field for field, so this suite fails if the envelope drifts.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.contract import ArticleInput, ClaimExtractionResult
from app.pipeline.ws5 import run_ws5
from app.services.envelope import (
    PENDING_VERDICT_LEVEL,
    PENDING_VERDICT_SUMMARY,
    to_analysis_response,
    unrated_verdict,
)

client = TestClient(app)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_article.txt"

# Copied verbatim from src/shared/contract.ts. If WS2 changes a union, these fail loudly
# rather than the renderer failing silently.
CLAIM_TYPES = {"factual", "opinion", "prediction"}
ASSESSMENT_STATUSES = {
    "supported",
    "partially_supported",
    "contradicted",
    "needs_review",
    "opinion",
}
ANALYSIS_STATUSES = {"complete", "processing", "failed", "skipped"}
VERDICT_LEVELS = {"trusted", "ok", "caution", "high_risk", "unrated"}


def assert_passes_ws2_guard(body: object) -> None:
    """Port of WS2's isAnalysisResponse(). Mirrors every check, in the same order."""
    assert isinstance(body, dict)
    assert body.get("schemaVersion") == "1.0"
    assert isinstance(body.get("url"), str)
    assert isinstance(body.get("status"), str) and body["status"] in ANALYSIS_STATUSES

    verdict = body.get("articleVerdict")
    assert isinstance(verdict, dict)
    assert isinstance(verdict.get("level"), str) and verdict["level"] in VERDICT_LEVELS
    assert isinstance(verdict.get("summary"), str)

    claims = body.get("verifiedClaims")
    assert isinstance(claims, list)
    for vc in claims:
        assert isinstance(vc, dict)
        # isClaim()
        claim = vc.get("claim")
        assert isinstance(claim, dict)
        assert isinstance(claim.get("id"), str)
        assert isinstance(claim.get("text"), str)
        assert isinstance(claim.get("claim_type"), str) and claim["claim_type"] in CLAIM_TYPES
        assert isinstance(claim.get("rank"), int)
        assert isinstance(claim.get("evidence"), list)
        for ev in claim["evidence"]:
            # isEvidence()
            assert isinstance(ev, dict)
            assert isinstance(ev.get("snippet"), str)
            assert isinstance(ev.get("source_url"), str)
        # isVerifiedClaim(): assessment is null or a well-formed Assessment
        assessment = vc.get("assessment")
        if assessment is not None:
            assert isinstance(assessment.get("claim_id"), str)
            assert assessment.get("status") in ASSESSMENT_STATUSES
            assert isinstance(assessment.get("explanation"), str)
            assert isinstance(assessment.get("citations"), list)


@pytest.fixture
def article_text() -> str:
    return FIXTURE.read_text(encoding="utf-8").split("\n", 1)[1].strip()


@pytest.fixture
def analyzed(article_text: str) -> dict:
    r = client.post(
        "/analyze",
        json={
            "url": "https://news.example.org/sg/scam-losses-2025",
            "title": "Singapore Reports Sharp Rise in Impersonation Scam Losses in 2025",
            "text": article_text,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_analyze_returns_a_valid_envelope(analyzed: dict):
    assert_passes_ws2_guard(analyzed)
    assert analyzed["schemaVersion"] == "1.0"
    assert analyzed["status"] == "complete"
    assert analyzed["url"] == "https://news.example.org/sg/scam-losses-2025"
    # `errors?: AnalysisError[]` in WS2 — an empty list, never null.
    assert analyzed["errors"] == []


def test_analyze_returns_claims_with_real_assessments(analyzed: dict):
    # Was: assessments had to be null (pre-WS6). WS6 now fills them.
    assert analyzed["verifiedClaims"], "expected claims from the sample article"
    for vc in analyzed["verifiedClaims"]:
        assert set(vc) == {"claim", "assessment"}
        assessment = vc["assessment"]
        assert assessment is not None, "WS6 should have assessed this claim"
        assert assessment["claim_id"] == vc["claim"]["id"]
        assert assessment["status"] in ASSESSMENT_STATUSES
        assert assessment["explanation"].strip()
        # §2.5, over the wire: an affirmative verdict always carries a citation.
        if assessment["status"] in {"supported", "partially_supported", "contradicted"}:
            assert assessment["citations"], f"{assessment['status']} with no citation"
        for citation in assessment["citations"]:
            assert citation["source_url"] in {e["source_url"] for e in vc["claim"]["evidence"]}


def test_article_verdict_is_computed_from_the_assessments(analyzed: dict):
    # Was: asserted the pre-WS6 placeholder verdict. The rollup is real now.
    verdict = analyzed["articleVerdict"]
    assert verdict["level"] in VERDICT_LEVELS
    assert verdict["summary"] != PENDING_VERDICT_SUMMARY, "should no longer be the placeholder"

    statuses = [vc["assessment"]["status"] for vc in analyzed["verifiedClaims"]]
    if "contradicted" in statuses:
        assert verdict["level"] == "high_risk"
    elif "partially_supported" in statuses:
        assert verdict["level"] == "caution"
    elif "supported" in statuses:
        assert verdict["level"] == "ok"
    else:
        assert verdict["level"] != "ok", "never OK when nothing was supported"


def test_envelope_claims_match_run_ws5_output(article_text: str, analyzed: dict):
    direct = run_ws5(
        ArticleInput(
            url="https://news.example.org/sg/scam-losses-2025",
            title="Singapore Reports Sharp Rise in Impersonation Scam Losses in 2025",
            text=article_text,
            source_domain="news.example.org",
        )
    )
    envelope_claims = [vc["claim"] for vc in analyzed["verifiedClaims"]]

    assert len(envelope_claims) == len(direct.claims)
    # Same claims, same order (rank 1 first) — the panel sorts by rank and the pill
    # counts in this order.
    assert [c["id"] for c in envelope_claims] == [c.id for c in direct.claims]
    assert [c["text"] for c in envelope_claims] == [c.text for c in direct.claims]
    assert [c["rank"] for c in envelope_claims] == [c.rank for c in direct.claims]


def test_anchor_fields_survive_the_envelope(analyzed: dict, article_text: str):
    # The offsets index the text we POSTed; the envelope must not disturb them.
    for vc in analyzed["verifiedClaims"]:
        claim = vc["claim"]
        assert claim["char_start"] is not None and claim["char_end"] is not None
        assert article_text[claim["char_start"]:claim["char_end"]] == claim["text"]


# --------------------------------------------------------------------------- #
# No-content path (WS3 can wire the fetch before WS1 sends text)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        {"url": "https://news.example.org/x"},
        {"url": "https://news.example.org/x", "text": None},
        {"url": "https://news.example.org/x", "text": ""},
        {"url": "https://news.example.org/x", "text": "   \n  "},
        {"url": "https://news.example.org/x", "title": "T", "text": "  "},
    ],
)
def test_analyze_without_text_still_returns_a_valid_empty_envelope(payload: dict):
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200, r.text  # never 4xx/5xx: WS3 must be able to wire early

    body = r.json()
    assert_passes_ws2_guard(body)
    assert body["verifiedClaims"] == []
    assert body["status"] == "complete"
    # Not "skipped": WS2 reads that as a Tier 0 trusted source and paints the trusted pill.
    assert body["status"] != "skipped"
    assert body["errors"] == [
        {"code": "no_content", "message": "No article text supplied; nothing to analyze."}
    ]


def test_analyze_requires_a_url():
    # url is the one genuinely required field; a malformed request is still a 422.
    r = client.post("/analyze", json={"text": "Some article body text here."})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Adapter unit test
# --------------------------------------------------------------------------- #
def test_to_analysis_response_wraps_every_claim(article_text: str):
    result = run_ws5(
        ArticleInput(url="https://news.example.org/story", text=article_text)
    )
    envelope = to_analysis_response(result, status="complete")

    assert envelope.schemaVersion == "1.0"
    assert envelope.url == result.url
    assert envelope.status == "complete"
    assert envelope.articleVerdict.level == PENDING_VERDICT_LEVEL
    assert len(envelope.verifiedClaims) == len(result.claims)
    assert all(vc.assessment is None for vc in envelope.verifiedClaims)
    assert [vc.claim.id for vc in envelope.verifiedClaims] == [c.id for c in result.claims]
    # And the serialized form is what WS2 actually receives.
    assert_passes_ws2_guard(envelope.model_dump(mode="json"))


def test_to_analysis_response_on_empty_result():
    envelope = to_analysis_response(
        ClaimExtractionResult(url="https://news.example.org/empty"), status="complete"
    )
    assert envelope.verifiedClaims == []
    assert envelope.articleVerdict.level == "unrated"
    assert_passes_ws2_guard(envelope.model_dump(mode="json"))


def test_to_analysis_response_accepts_an_article_verdict_override():
    # WS4's non-escalated path: the verdict is decided outside Tier 3, and the caller's
    # reason string rides along.
    reason = "A quick scan found nothing that needed a full fact-check."
    envelope = to_analysis_response(
        ClaimExtractionResult(url="https://news.example.org/screened"),
        status="complete",
        article_verdict=unrated_verdict(reason),
    )
    assert envelope.articleVerdict.level == "unrated"
    assert envelope.articleVerdict.summary == reason
    assert_passes_ws2_guard(envelope.model_dump(mode="json"))


# --------------------------------------------------------------------------- #
# CORS — without this the service worker's fetch fails before WS2 sees anything
# --------------------------------------------------------------------------- #
def test_cors_preflight_allows_the_extension_origin():
    r = client.options(
        "/analyze",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code in (200, 204), r.text
    assert r.headers.get("access-control-allow-origin") is not None


def test_cors_header_present_on_the_actual_response():
    r = client.post(
        "/analyze",
        json={"url": "https://news.example.org/x"},
        headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") is not None


# --------------------------------------------------------------------------- #
# WS6 assessment over the wire
# --------------------------------------------------------------------------- #
def test_assessed_envelope_still_passes_the_ws2_guard(analyzed: dict):
    # The guard validates assessments too (isVerifiedClaim -> isAssessment); populating
    # them must not break it.
    assert_passes_ws2_guard(analyzed)
    assert all(vc["assessment"] is not None for vc in analyzed["verifiedClaims"])


def test_analyze_is_deterministic(article_text: str):
    payload = {"url": "https://news.example.org/sg/x", "text": article_text}
    first = client.post("/analyze", json=payload).json()
    second = client.post("/analyze", json=payload).json()
    assert first == second


def test_no_content_envelope_is_never_a_clean_bill_of_health():
    body = client.post("/analyze", json={"url": "https://news.example.org/x"}).json()
    assert body["verifiedClaims"] == []
    # Nothing was fact-checked, so the neutral level, never "ok".
    assert body["articleVerdict"]["level"] == "unrated"
    assert body["articleVerdict"]["level"] != "ok"
    # A real reason, not the empty string — the exact wording is the caller's to set.
    assert body["articleVerdict"]["summary"].strip()
