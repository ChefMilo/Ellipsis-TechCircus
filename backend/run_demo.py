"""Offline demo: run WS5 over the sample article and pretty-print the result.

    python run_demo.py

No API keys needed (uses the mock LLM + mock search). Handy for eyeballing the
contract shape and for the pitch.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.models.contract import ArticleInput
from app.pipeline.ws5 import run_ws5

FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "sample_article.txt"


def main() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")
    title, body = raw.split("\n", 1)
    article = ArticleInput(
        url="https://news.example.org/sg/scam-losses-2025",
        title=title.strip(),
        text=body.strip(),
        source_domain="news.example.org",
    )
    result = run_ws5(article)

    print("=" * 78)
    print("DASFAX WS5 — demo run (mock backends)")
    print("=" * 78)
    print(f"backends: {result.model_meta['llm_backend']} | {result.model_meta['search_backend']}")
    print(f"stats: {result.stats.model_dump()}")
    print("-" * 78)
    for c in result.claims:
        print(f"[#{c.rank}] (cw={c.checkworthiness:.2f}) {c.text}")
        print(f"     query: {c.search_query}")
        for ev in c.evidence:
            print(f"       - {ev.source_domain}: {ev.snippet[:90]}...")
        print()
    print("Full JSON (the exact shape WS6 receives):")
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)[:1500] + "\n...")


if __name__ == "__main__":
    main()
