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
VERDICT_LEVELS = {"trusted", "ok", "caution", "high_risk"}


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


def test_analyze_returns_claims_with_null_assessments(analyzed: dict):
    assert analyzed["verifiedClaims"], "expected claims from the sample article"
    for vc in analyzed["verifiedClaims"]:
        assert set(vc) == {"claim", "assessment"}
        assert vc["assessment"] is None, "WS6 does not exist yet; assessments must be null"


def test_article_verdict_is_the_documented_placeholder(analyzed: dict):
    verdict = analyzed["articleVerdict"]
    assert verdict["level"] == PENDING_VERDICT_LEVEL.value
    assert verdict["summary"] == PENDING_VERDICT_SUMMARY


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
