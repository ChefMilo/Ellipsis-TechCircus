"""WS5 pipeline tests — run fully offline against the mock clients.

These encode WS5's 'done when' bar from the proposal:
  * at most `max_claims` checkable claims returned
  * each kept claim carries >= evidence_per_claim sources
  * opinions/predictions are not surfaced as factual checkable claims
  * near-duplicate claims are collapsed
  * output validates against the frozen contract
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.clients.base import ExtractedClaim
from app.clients.mock_llm import MockLLMClient
from app.clients.mock_search import MockSearchClient
from app.config import get_settings
from app.models.contract import ArticleInput, ClaimExtractionResult, ClaimType
from app.pipeline.ws5 import run_ws5

FIXTURE = Path(__file__).parent / "fixtures" / "sample_article.txt"


@pytest.fixture
def article() -> ArticleInput:
    text = FIXTURE.read_text(encoding="utf-8")
    title, body = text.split("\n", 1)
    return ArticleInput(
        url="https://news.example.org/sg/scam-losses-2025",
        title=title.strip(),
        text=body.strip(),
        source_domain="news.example.org",
    )


@pytest.fixture
def result(article: ArticleInput) -> ClaimExtractionResult:
    # Explicit mock clients so the test is hermetic regardless of env vars.
    return run_ws5(article, settings=get_settings(), llm=MockLLMClient(), search=MockSearchClient())


def test_result_validates_against_contract(result: ClaimExtractionResult):
    # Round-trips through pydantic == structurally valid for WS6.
    ClaimExtractionResult.model_validate(result.model_dump())


def test_at_most_max_claims(result: ClaimExtractionResult):
    assert len(result.claims) <= get_settings().max_claims


def test_all_returned_claims_are_factual(result: ClaimExtractionResult):
    assert result.claims, "expected at least one checkable claim from the fixture"
    assert all(c.claim_type == ClaimType.FACTUAL for c in result.claims)


def test_each_claim_has_enough_evidence(result: ClaimExtractionResult):
    need = get_settings().evidence_per_claim
    for c in result.claims:
        assert len(c.evidence) >= need, f"claim {c.id} has {len(c.evidence)} < {need} sources"
        for ev in c.evidence:
            assert ev.snippet and ev.source_url


def test_claims_are_rank_ordered(result: ClaimExtractionResult):
    ranks = [c.rank for c in result.claims]
    assert ranks == sorted(ranks)
    cws = [c.checkworthiness for c in result.claims]
    assert cws == sorted(cws, reverse=True), "rank 1 should be the most checkworthy"


def test_opinion_sentence_not_surfaced(result: ClaimExtractionResult):
    # The fixture's opinion sentence ("...the most alarming trend...should do much more")
    # must not appear as a returned factual claim.
    joined = " ".join(c.text.lower() for c in result.claims)
    assert "most alarming trend" not in joined
    assert "should do much more" not in joined


def test_prediction_sentence_not_surfaced(result: ClaimExtractionResult):
    joined = " ".join(c.text.lower() for c in result.claims)
    assert "will double again by 2027" not in joined


def test_duplicate_claim_collapsed(result: ClaimExtractionResult):
    # The fixture repeats the "...up from 1,504 cases in 2024" sentence near-verbatim.
    # Token-Jaccard dedup collapses near-verbatim repeats (NOT paraphrases — that needs
    # embedding-based dedup, tracked as a limitation in the README).
    hits = [c for c in result.claims if "1,504" in c.text]
    assert len(hits) <= 1, "near-verbatim duplicate sentence should be collapsed to one claim"
    assert result.stats.dropped_duplicate >= 1


def test_stats_are_populated(result: ClaimExtractionResult):
    s = result.stats
    assert s.total_claims_extracted > 0
    assert s.dropped_opinion_or_prediction >= 2  # opinion + prediction in fixture
    assert s.claims_with_evidence == len(result.claims)


def test_model_meta_records_backends(result: ClaimExtractionResult):
    assert result.model_meta["llm_backend"].startswith("mock")
    assert result.model_meta["search_backend"].startswith("mock")


def test_evidence_excludes_article_own_domain():
    art = ArticleInput(
        url="https://selfsource.example.org/story",
        title="t",
        text="The agency confirmed that 3,363 cases were recorded in 2025 according to official data.",
        source_domain="selfsource.example.org",
    )
    res = run_ws5(art, llm=MockLLMClient(), search=MockSearchClient())
    for c in res.claims:
        for ev in c.evidence:
            assert ev.source_domain != "selfsource.example.org"


def test_blank_text_rejected():
    with pytest.raises(ValidationError):
        ArticleInput(url="https://x.example.org", text="   ")


def test_determinism(article: ArticleInput):
    r1 = run_ws5(article, llm=MockLLMClient(), search=MockSearchClient())
    r2 = run_ws5(article, llm=MockLLMClient(), search=MockSearchClient())
    assert r1.model_dump() == r2.model_dump()


# --------------------------------------------------------------------------- #
# Anchoring (char_start / char_end / prefix / suffix)
#
# WS2 re-anchors claims onto the live DOM using these offsets; they must index
# ArticleInput.text exactly. See app/services/anchoring.py.
# --------------------------------------------------------------------------- #
class _ScriptedLLMClient:
    """Fake extractor returning whatever claim text a test dictates — stands in for the
    real OpenAI client, which rewrites claims instead of quoting them verbatim."""

    name = "scripted-test-llm"

    def __init__(self, *texts: str) -> None:
        self._texts = texts

    def extract_claims(self, *, title: str | None, text: str) -> list[ExtractedClaim]:
        return [
            ExtractedClaim(text=t, claim_type="factual", checkworthiness=0.9)
            for t in self._texts
        ]


def test_every_claim_is_anchored_on_the_fixture(article: ArticleInput, result: ClaimExtractionResult):
    # The mock LLM emits verbatim sentence splits, so every claim must anchor exactly.
    assert result.claims
    for c in result.claims:
        assert c.char_start is not None and c.char_end is not None, f"claim {c.id} not anchored"
        assert 0 <= c.char_start < c.char_end <= len(article.text)
        assert article.text[c.char_start:c.char_end] == c.text


def test_anchor_context_matches_source_slices(article: ArticleInput, result: ClaimExtractionResult):
    for c in result.claims:
        assert c.char_start is not None and c.char_end is not None
        expected_prefix = article.text[max(0, c.char_start - 32):c.char_start] or None
        expected_suffix = article.text[c.char_end:c.char_end + 32] or None
        assert c.prefix == expected_prefix
        assert c.suffix == expected_suffix


def test_stats_count_anchored_claims(result: ClaimExtractionResult):
    anchored = sum(1 for c in result.claims if c.char_start is not None)
    assert result.stats.claims_anchored == anchored == len(result.claims)


def test_paraphrased_claim_anchors_to_its_source_sentence(article: ArticleInput):
    source_sentence = (
        "Victims lost a total of S$242.9 million to these scams last year, "
        "with an average loss of S$72,229 per victim."
    )
    paraphrase = (
        "Scam victims in Singapore lost S$242.9 million in total last year, "
        "an average loss of S$72,229 each."
    )
    res = run_ws5(
        article,
        llm=_ScriptedLLMClient(paraphrase),
        search=MockSearchClient(),
    )

    assert len(res.claims) == 1
    claim = res.claims[0]
    assert claim.text == paraphrase, "the rewritten claim text is what WS6 sees"
    assert claim.char_start is not None and claim.char_end is not None
    assert article.text[claim.char_start:claim.char_end] == source_sentence


def test_unanchorable_claim_leaves_offsets_null(article: ArticleInput):
    res = run_ws5(
        article,
        llm=_ScriptedLLMClient("The Eiffel Tower was completed in Paris in 1889."),
        search=MockSearchClient(),
    )

    assert len(res.claims) == 1
    claim = res.claims[0]
    assert claim.char_start is None
    assert claim.char_end is None
    assert claim.prefix is None
    assert claim.suffix is None
    assert res.stats.claims_anchored == 0


def test_anchors_are_deterministic(article: ArticleInput):
    r1 = run_ws5(article, llm=MockLLMClient(), search=MockSearchClient())
    r2 = run_ws5(article, llm=MockLLMClient(), search=MockSearchClient())
    assert [(c.char_start, c.char_end, c.prefix, c.suffix) for c in r1.claims] == [
        (c.char_start, c.char_end, c.prefix, c.suffix) for c in r2.claims
    ]
