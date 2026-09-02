"""HTTP-level tests for WS3's POST /analyze (app/api/analyze.py + app/orchestrator/),
via FastAPI's TestClient against the real `app` from app/main.py -- exercising the
router registration, not just the orchestrator functions directly.

The load-bearing assertion here is the last one: a full, real /analyze response run
through `is_analysis_response()`, the Python port of Darren's isAnalysisResponse()
guard from backend/tests/test_ws3_envelope_guard_port.py (imported from there, not
re-implemented -- one guard port, reused). That file's own header explains it is a
PORT, not the real TS guard; the same caveat applies here by inheritance. See
docs/ws3/WS3-CONTRACT-AUDIT.md Task B and docs/ws3/WS3-ANALYZE-ENVELOPE.md for the full story.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from tests.test_ws3_envelope_guard_port import is_analysis_response

client = TestClient(app)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_article.txt"


def _article_text() -> str:
    return FIXTURE.read_text(encoding="utf-8").split("\n", 1)[1].strip()


def test_curl_style_real_article_returns_claims_and_passes_the_guard_port():
    payload = {
        "url": "https://news.example.org/sg/ws3-analyze-envelope-real-article",
        "title": "Singapore Reports Sharp Rise in Impersonation Scam Losses in 2025",
        "text": _article_text(),
        "published_at": "2026-02-01T00:00:00Z",
        "source_domain": "news.example.org",
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200, r.text

    body = r.json()
    assert is_analysis_response(body) is True
    assert body["status"] == "complete"
    assert len(body["verifiedClaims"]) > 0
    assert body["errors"] == []


def test_curl_style_no_content_returns_the_no_content_envelope_and_passes_the_guard_port():
    payload = {"url": "https://news.example.org/sg/empty"}
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200, r.text

    body = r.json()
    assert is_analysis_response(body) is True
    assert body["status"] == "complete"  # not a new status -- Step 0
    assert body["articleVerdict"]["level"] == "unrated"
    assert body["verifiedClaims"] == []
    assert body["errors"] == [
        {"code": "no_content", "message": "No article text supplied; nothing to analyze."}
    ]


def test_never_returns_a_5xx_even_when_the_request_is_malformed():
    # Missing the one required field (url) is a 422 from Pydantic/FastAPI itself, at
    # the request-validation layer -- not a 500, and not this route's problem to fix.
    r = client.post("/analyze", json={"text": "no url here"})
    assert r.status_code == 422
    assert r.status_code < 500


def test_response_still_carries_cors_header_for_the_extension_origin():
    # /analyze moved out of app/main.py into a router; confirm the app-level CORS
    # middleware (app/main.py, untouched) still applies to it.
    r = client.post(
        "/analyze",
        json={"url": "https://news.example.org/x"},
        headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") is not None
