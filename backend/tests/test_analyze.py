"""Tests for POST /analyze — the single call the extension makes.

The wire shape matters as much as the logic here: the content script validates every
reply with `isAnalysisResponse()`, so a renamed key is a user-visible failure. The
camelCase assertions below are deliberate, not incidental.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

REAL = (
    "The National Environment Agency said dengue cases fell to 214 in the week ending "
    "8 March, down from 287 the week before. According to the agency, 91 active clusters "
    "remain islandwide. A spokesperson said inspections of about 12,000 premises in "
    "February found mosquito breeding at 380 of them."
)
FAKE = (
    "WAKE UP! Sources say a secret report has been buried for months. Insiders claim "
    "officials have known ALL ALONG and did nothing!! Everyone knows the mainstream media "
    "will never touch this story. Allegedly the levels are 100% higher than they admit. "
    "Share this before it is deleted!! They are already censoring posts about it!!!"
)


def _analyze(**payload) -> dict:
    body = {"url": "https://news.example.org/a"}
    body.update(payload)
    r = client.post("/analyze", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Wire shape — what isAnalysisResponse() checks
# --------------------------------------------------------------------------- #
def test_response_uses_the_camelcase_keys_the_client_validates():
    body = _analyze(title="Dengue cases fall", text=REAL)
    assert body["schemaVersion"] == "1.0"
    assert {"url", "status", "articleVerdict", "verifiedClaims"} <= set(body)
    assert body["status"] in {"complete", "processing", "failed", "skipped"}
    assert body["articleVerdict"]["level"] in {"trusted", "ok", "caution", "high_risk"}
    assert isinstance(body["articleVerdict"]["summary"], str)


def test_client_image_key_is_accepted_here_too():
    body = _analyze(title="T", text=REAL, images=["https://cdn.example.org/a.jpg"])
    assert body["status"] == "complete"


# --------------------------------------------------------------------------- #
# The cascade
# --------------------------------------------------------------------------- #
def test_low_risk_article_stops_at_tier2_with_no_claims():
    body = _analyze(title="Dengue cases fall", text=REAL)
    assert body["tier2"]["escalated"] is False
    assert body["verifiedClaims"] == []
    assert body["articleVerdict"]["level"] == "ok"


def test_high_risk_article_escalates_and_returns_claims():
    body = _analyze(title="SHOCKING truth EXPOSED", text=FAKE)
    assert body["tier2"]["escalated"] is True
    assert len(body["verifiedClaims"]) > 0
    assert body["articleVerdict"]["level"] in {"caution", "high_risk"}


def test_escalated_claims_carry_an_explicit_null_assessment():
    """WS6 does not exist yet. `isVerifiedClaim` tests `assessment === null`, so the key
    must be present and null — omitting it makes the client reject every claim."""
    body = _analyze(title="SHOCKING truth EXPOSED", text=FAKE)
    for vc in body["verifiedClaims"]:
        assert "assessment" in vc
        assert vc["assessment"] is None
        assert vc["claim"]["claim_type"] == "factual"


def test_tier2_summary_explains_the_decision():
    body = _analyze(title="Dengue cases fall", text=REAL)
    tier2 = body["tier2"]
    assert {"escalated", "textScore", "maxImageScore", "reasons", "latencyMs"} <= set(tier2)
    assert tier2["reasons"], "a tier decision with no stated reason is not reviewable"


# --------------------------------------------------------------------------- #
# Degraded inputs must not 500 — WS1 sends these in the real world
# --------------------------------------------------------------------------- #
def test_page_with_no_text_is_reported_not_crashed():
    body = _analyze(images=["https://cdn.example.org/a.jpg"])
    assert body["status"] in {"complete", "failed"}
    assert body["articleVerdict"]["summary"]


def test_page_with_no_text_and_a_synthetic_image_says_it_could_not_read_the_text():
    body = _analyze(images=["https://cdn.example.org/midjourney/x.png"])
    assert body["tier2"]["escalated"] is True
    assert body["status"] == "failed"
    assert body["errors"] and body["errors"][0]["code"] == "no_article_text"
    assert body["articleVerdict"]["level"] == "caution"


def test_missing_url_is_a_validation_error_not_a_crash():
    assert client.post("/analyze", json={"text": REAL}).status_code == 422
