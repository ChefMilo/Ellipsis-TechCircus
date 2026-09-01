"""Offline demo: run Tier 3 (WS5 extraction + retrieval, then WS6 assessment) and
pretty-print the result. No API keys needed — mock LLM, mock search, mock assessor.

    python run_demo.py                                   # sample article, default mocks
    python run_demo.py --fixture tests/fixtures/demo_article.txt --demo-spread

`--demo-spread` switches the mock search client to its presentation profiles so the run
visibly produces all four assessable statuses (supported / contradicted /
partially_supported / needs_review) instead of whatever the hash happens to give. It is
the CLI equivalent of DASFAX_MOCK_DEMO=1.

To show the same thing through the real endpoint, the flag must be in the environment
BEFORE the server starts (Settings reads env at import time):

    $env:DASFAX_MOCK_DEMO = "1"
    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000

    # then, from another shell:
    $body = @{ url = "https://news.example.org/sg/scam-rules-2025"
               title = "Singapore Tightens Rules on Digital Payment Scams After Record Losses"
               text  = (Get-Content tests/fixtures/demo_article.txt -Raw) } | ConvertTo-Json
    Invoke-RestMethod -Uri http://127.0.0.1:8000/analyze -Method Post -ContentType application/json -Body $body
"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.clients.factory import make_assessor_client, make_llm_client
from app.clients.mock_search import MockSearchClient
from app.models.contract import ArticleInput
from app.pipeline.ws5 import run_ws5
from app.services.assessment import assess_claims
from app.services.envelope import to_analysis_response

BACKEND_DIR = Path(__file__).parent
DEFAULT_FIXTURE = BACKEND_DIR / "tests" / "fixtures" / "sample_article.txt"
DEFAULT_URL = "https://news.example.org/sg/scam-losses-2025"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Article file: title on line 1, body after.")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL to attribute the article to.")
    parser.add_argument(
        "--demo-spread",
        action="store_true",
        help="Use the mock search presentation profiles (all four statuses appear).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    raw = args.fixture.read_text(encoding="utf-8")
    title, body = raw.split("\n", 1)
    article = ArticleInput(
        url=args.url,
        title=title.strip(),
        text=body.strip(),
        source_domain="news.example.org",
    )

    search = MockSearchClient(demo_spread=args.demo_spread)
    result = run_ws5(article, llm=make_llm_client(), search=search)
    assessor = make_assessor_client()
    assessments = assess_claims(result.claims, client=assessor)
    result.model_meta["assessor_backend"] = assessor.name
    envelope = to_analysis_response(result, assessments=assessments)

    print("=" * 78)
    print(f"DASFAX Tier 3 — demo run ({args.fixture.name})")
    print("=" * 78)
    print(f"backends: {result.model_meta['llm_backend']} | {search.name} | {assessor.name}")
    print("NOTE: the assessor is a labelled RENDERING FIXTURE, not real assessment.")
    print(f"stats: {result.stats.model_dump()}")
    print("-" * 78)

    for vc in envelope.verifiedClaims:
        claim, assessment = vc.claim, vc.assessment
        assert assessment is not None
        print(f"[#{claim.rank}] {assessment.status.value.upper()}  (cw={claim.checkworthiness:.2f}, "
              f"{len(assessment.citations)} citation{'s' if len(assessment.citations) != 1 else ''})")
        print(f"     {claim.text}")
        print(f"     -> {assessment.explanation}")
        for citation in assessment.citations:
            print(f"        * {citation.source_url}")
        print()

    print("-" * 78)
    print(f"ARTICLE VERDICT: {envelope.articleVerdict.level.value.upper()}")
    print(f"  {envelope.articleVerdict.summary}")
    statuses = sorted({vc.assessment.status.value for vc in envelope.verifiedClaims if vc.assessment})
    print(f"  statuses present: {', '.join(statuses)}")


if __name__ == "__main__":
    main()
