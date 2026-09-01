"""WS5 pipeline: extract -> dedup -> rank -> retrieve -> ClaimExtractionResult.

This is the single entry point for WS5. WS3's orchestrator can import `run_ws5`
directly (no HTTP needed), or call it via the FastAPI endpoint in app.main.

Flow:
  1. LLM extracts candidate claims from cleaned text (mock heuristic by default).
  2. Near-duplicate claims are removed.
  3. Factual claims above the checkworthiness floor are ranked; top-N kept (proposal: 3–5).
  4. Each kept claim gets a search query + retrieved evidence snippets.
  5. Result is assembled with stats + provenance for observability.

Non-factual claims (opinion/prediction) are NOT retrieved on, but the counts are
recorded so WS6/WS2 can still surface an 'Opinion' treatment if desired.
"""
from __future__ import annotations

from app.clients.base import LLMClient, SearchClient
from app.clients.factory import make_llm_client, make_search_client
from app.config import Settings, get_settings
from app.models.contract import (
    ArticleInput,
    Claim,
    ClaimExtractionResult,
    ClaimType,
    ExtractionStats,
)
from app.services.ranking import dedup, rank_factual
from app.services.retrieval import retrieve_evidence


def run_ws5(
    article: ArticleInput,
    *,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    search: SearchClient | None = None,
) -> ClaimExtractionResult:
    """Run WS5 over one article. Clients are injectable for testing; otherwise built
    from Settings (mock by default)."""
    settings = settings or get_settings()
    llm = llm or make_llm_client(settings)
    search = search or make_search_client(settings)

    stats = ExtractionStats()

    # 1. Extract candidate claims.
    extracted = llm.extract_claims(title=article.title, text=article.text)
    stats.total_claims_extracted = len(extracted)
    stats.factual_claims = sum(1 for c in extracted if c.claim_type == "factual")
    stats.dropped_opinion_or_prediction = sum(1 for c in extracted if c.claim_type != "factual")

    # 2. Dedup (across all claims, so a factual claim doesn't lose to an opinion dup).
    deduped, dropped_dup = dedup(extracted, threshold=settings.dedup_threshold)
    stats.dropped_duplicate = dropped_dup

    # 3. Rank + truncate to the load-bearing factual claims.
    top_factual = rank_factual(
        deduped,
        max_claims=settings.max_claims,
        min_checkworthiness=settings.min_checkworthiness,
    )
    stats.kept_after_ranking = len(top_factual)

    # 4. Retrieve evidence per kept claim, and build contract Claims.
    claims: list[Claim] = []
    for rank, ec in enumerate(top_factual, start=1):
        query, evidence = retrieve_evidence(
            ec.text,
            client=search,
            max_results=settings.evidence_per_claim,
            article_url=article.url,
        )
        if evidence:
            stats.claims_with_evidence += 1
        claims.append(
            Claim(
                id=f"c{rank}",
                text=ec.text,
                claim_type=ClaimType.FACTUAL,
                checkworthiness=ec.checkworthiness,
                rank=rank,
                search_query=query,
                evidence=evidence,
            )
        )

    return ClaimExtractionResult(
        url=article.url,
        title=article.title,
        claims=claims,
        stats=stats,
        model_meta={
            "ws": "WS5",
            "llm_backend": getattr(llm, "name", "unknown"),
            "search_backend": getattr(search, "name", "unknown"),
            "max_claims": settings.max_claims,
            "min_checkworthiness": settings.min_checkworthiness,
            "evidence_per_claim": settings.evidence_per_claim,
        },
    )
