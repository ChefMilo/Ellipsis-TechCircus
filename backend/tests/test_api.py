"""Endpoint smoke tests via FastAPI's TestClient (still fully offline / mock)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ws"] == "WS5"


def test_extract_endpoint_returns_contract_shape():
    payload = {
        "url": "https://news.example.org/sg/story",
        "title": "Test headline",
        "text": (
            "Singapore recorded 3,363 impersonation scam cases in 2025 according to police. "
            "Victims lost S$242.9 million last year. "
            "This is clearly the worst trend of the decade."
        ),
        "source_domain": "news.example.org",
    }
    r = client.post("/tier3/claims", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == payload["url"]
    assert "claims" in body and "stats" in body
    for c in body["claims"]:
        assert c["claim_type"] == "factual"
        assert {"id", "text", "rank", "checkworthiness", "evidence"} <= set(c)


def test_extract_endpoint_rejects_blank_text():
    r = client.post("/tier3/claims", json={"url": "https://x.example.org", "text": "   "})
    assert r.status_code == 422  # pydantic validation error surfaced by FastAPI
