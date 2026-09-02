"""Unit tests for the REAL providers — with no network, no API key and no SDK installed.

Each real client takes an injected transport (`client=`), so these tests hand it a stub
that returns canned responses. Nothing here touches an external API; if a test in this
file ever needs a key, it is wrong.

What is actually being asserted is the untrusted-input boundary. A real model returns
prose, fences, wrong types and invented indices, and none of that may take a request down:
  * bad extraction rows are dropped, the good ones survive;
  * a garbage assessor reply becomes needs_review, never a crash and never a verdict;
  * a search failure returns no hits so WS5 keeps going.
The mocks remain the default, so none of this runs unless an env var switches it on.
"""
from __future__ import annotations

import pytest

from app.clients.base import AssessedClaim, ExtractedClaim, SearchHit
from app.clients.factory import make_assessor_client, make_llm_client, make_search_client
from app.clients.mock_assessor import MockAssessorClient
from app.clients.mock_llm import MockLLMClient
from app.clients.mock_search import MockSearchClient
from app.clients.openai_assessor import OpenAIAssessorClient
from app.clients.openai_llm import OpenAILLMClient
from app.clients.tavily_search import TavilySearchClient
from app.config import DEFAULT_OPENAI_BASE_URL, Settings
from app.models.contract import AssessmentStatus, Claim, ClaimType, Evidence
from app.services.assessment import assess_claim


# --------------------------------------------------------------------------- #
# Stub transports
# --------------------------------------------------------------------------- #
class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.message = _Message(content)


class _Completion:
    def __init__(self, content: str | None) -> None:
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, parent: _FakeOpenAI) -> None:
        self._parent = parent

    def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        if self._parent.error is not None:
            raise self._parent.error
        return _Completion(self._parent.content)


class _Chat:
    def __init__(self, parent: _FakeOpenAI) -> None:
        self.completions = _Completions(parent)


class _FakeOpenAI:
    """Stands in for `openai.OpenAI` — same `.chat.completions.create` surface."""

    def __init__(self, content: str | None = None, *, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict] = []
        self.chat = _Chat(self)


class _FakeTavily:
    """Stands in for `tavily.TavilyClient`."""

    def __init__(self, resp=None, *, error: Exception | None = None) -> None:
        self.resp = resp
        self.error = error
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.resp


def _settings(**kw) -> Settings:
    return Settings(**kw)


def _evidence(snippet: str, i: int = 0) -> Evidence:
    return Evidence(
        snippet=snippet,
        source_url=f"https://src.example.org/{i}",
        source_title="Source",
        source_domain="src.example.org",
    )


# --------------------------------------------------------------------------- #
# The mocks are still the default (no env set = fully offline)
# --------------------------------------------------------------------------- #
def test_default_providers_are_all_mock():
    s = _settings()
    assert (s.llm_provider, s.search_provider, s.assessor_provider) == ("mock", "mock", "mock")
    assert isinstance(make_llm_client(s), MockLLMClient)
    assert isinstance(make_search_client(s), MockSearchClient)
    assert isinstance(make_assessor_client(s), MockAssessorClient)


def test_default_base_url_is_openai():
    assert _settings().openai_base_url == DEFAULT_OPENAI_BASE_URL


def test_real_providers_require_a_key():
    # Nothing activates by accident: switching the provider without a key fails loudly
    # at construction rather than silently degrading mid-demo.
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_llm_client(_settings(llm_provider="openai", openai_api_key=None))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_assessor_client(_settings(assessor_provider="openai", openai_api_key=None))
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        make_search_client(_settings(search_provider="tavily", tavily_api_key=None))


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="DASFAX_ASSESSOR_PROVIDER"):
        make_assessor_client(_settings(assessor_provider="nope"))


# --------------------------------------------------------------------------- #
# OpenAI-compatible extraction client
# --------------------------------------------------------------------------- #
def test_extraction_parses_claims():
    fake = _FakeOpenAI(
        '{"claims": ['
        '{"text": "Singapore recorded 3,363 impersonation scam cases in 2025.",'
        ' "claim_type": "factual", "checkworthiness": 0.9},'
        '{"text": "The policy is the best in the region.",'
        ' "claim_type": "opinion", "checkworthiness": 0.2}]}'
    )
    client = OpenAILLMClient(_settings(openai_model="gpt-4o-mini"), client=fake)

    claims = client.extract_claims(title="Scams", text="body text")

    assert claims == [
        ExtractedClaim(
            text="Singapore recorded 3,363 impersonation scam cases in 2025.",
            claim_type="factual",
            checkworthiness=0.9,
        ),
        ExtractedClaim(
            text="The policy is the best in the region.",
            claim_type="opinion",
            checkworthiness=0.2,
        ),
    ]
    assert client.name == "openai:gpt-4o-mini"
    assert fake.calls[0]["model"] == "gpt-4o-mini"
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


def test_extraction_prompt_asks_for_single_sentence_anchorable_claims():
    # The anchoring guard is load-bearing: services/anchoring.py has to find the claim
    # back in the source text, so a claim fused from several sentences anchors to nothing.
    fake = _FakeOpenAI('{"claims": []}')
    OpenAILLMClient(_settings(), client=fake).extract_claims(title=None, text="x")
    system = fake.calls[0]["messages"][0]["content"]
    assert "SINGLE source sentence" in system
    assert "close to the original" in system


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "",
        None,
        "[1, 2, 3]",                       # valid JSON, wrong shape
        '{"claims": "not a list"}',
        '{"claims": [{"text": "x", "checkworthiness": 0.5}',  # truncated
    ],
)
def test_extraction_survives_garbage_output(content):
    client = OpenAILLMClient(_settings(), client=_FakeOpenAI(content))
    assert client.extract_claims(title="t", text="body") == []


def test_extraction_drops_only_the_bad_rows():
    fake = _FakeOpenAI(
        '{"claims": ['
        '{"claim_type": "factual"},'                                 # no text
        '{"text": "", "checkworthiness": 0.9},'                      # empty text
        '{"text": "Good claim.", "checkworthiness": "high"},'        # unparseable number
        '"a bare string",'                                           # not an object
        '{"text": "Kept claim.", "claim_type": "FACTUAL", "checkworthiness": 0.8}]}'
    )
    claims = OpenAILLMClient(_settings(), client=fake).extract_claims(title="t", text="body")
    assert [c.text for c in claims] == ["Kept claim."]
    assert claims[0].claim_type == "factual"   # case-normalised


def test_extraction_clamps_and_defaults_fields():
    fake = _FakeOpenAI(
        '{"claims": [{"text": "A.", "claim_type": "speculation", "checkworthiness": 4.2},'
        '{"text": "B."}]}'
    )
    claims = OpenAILLMClient(_settings(), client=fake).extract_claims(title=None, text="body")
    assert claims[0].claim_type == "factual"       # unknown type falls back
    assert claims[0].checkworthiness == 1.0        # clamped into 0..1
    assert claims[1].checkworthiness == 0.5        # missing -> neutral default


def test_extraction_tolerates_a_fenced_json_reply():
    # Some OpenAI-compatible endpoints fence the object even in JSON mode.
    fake = _FakeOpenAI('```json\n{"claims": [{"text": "A.", "checkworthiness": 0.7}]}\n```')
    claims = OpenAILLMClient(_settings(), client=fake).extract_claims(title=None, text="body")
    assert [c.text for c in claims] == ["A."]


# --------------------------------------------------------------------------- #
# OpenAI-compatible assessor client
# --------------------------------------------------------------------------- #
def test_assessor_parses_a_verdict():
    fake = _FakeOpenAI(
        '{"status": "supported", "evidence_indices": [0, 2], "confidence": 0.82,'
        ' "explanation": "Both cited sources report the same figure."}'
    )
    client = OpenAIAssessorClient(_settings(openai_model="gemini-2.0-flash"), client=fake)

    result = client.assess("A claim.", [_evidence("s", i) for i in range(3)])

    assert result == AssessedClaim(
        status="supported",
        evidence_indices=[0, 2],
        confidence=0.82,
        explanation="Both cited sources report the same figure.",
    )
    assert client.name == "openai-assessor:gemini-2.0-flash"


def test_assessor_prompt_numbers_the_evidence_and_forbids_invented_sources():
    fake = _FakeOpenAI('{"status": "needs_review", "evidence_indices": []}')
    evidence = [_evidence("First snippet.", 0), _evidence("Second snippet.", 1)]
    OpenAIAssessorClient(_settings(), client=fake).assess("A claim.", evidence)

    system = fake.calls[0]["messages"][0]["content"]
    user = fake.calls[0]["messages"][1]["content"]
    assert "[0] Source: First snippet." in user
    assert "[1] Source: Second snippet." in user
    assert "ONLY the numbered evidence" in system
    assert "Never output a URL" in system


@pytest.mark.parametrize(
    "content",
    [
        "the claim looks true to me",       # prose
        "",
        None,
        '{"evidence_indices": [0]}',        # no status
        '{"status": "probably_true", "evidence_indices": [0]}',   # off-contract status
        '{"status": "opinion", "evidence_indices": []}',          # not the assessor's call
        '{"status": "supported", ',         # truncated
    ],
)
def test_assessor_degrades_to_needs_review_on_bad_output(content):
    result = OpenAIAssessorClient(_settings(), client=_FakeOpenAI(content)).assess(
        "A claim.", [_evidence("s")]
    )
    assert result.status == AssessmentStatus.NEEDS_REVIEW
    assert result.evidence_indices == []
    assert result.confidence is None
    assert result.explanation


def test_assessor_survives_an_api_error():
    # A rate limit on one claim costs that claim a verdict, not the whole article.
    fake = _FakeOpenAI(error=RuntimeError("429 rate limited"))
    result = OpenAIAssessorClient(_settings(), client=fake).assess("A claim.", [_evidence("s")])
    assert result.status == AssessmentStatus.NEEDS_REVIEW


def test_assessor_coerces_sloppy_indices_and_confidence():
    fake = _FakeOpenAI(
        '{"status": "contradicted", "evidence_indices": [0, "1", null, "x", true],'
        ' "confidence": "1.7", "explanation": "   "}'
    )
    result = OpenAIAssessorClient(_settings(), client=fake).assess("A claim.", [_evidence("s")])
    assert result.evidence_indices == [0, 1]
    assert result.confidence == 1.0        # clamped
    assert result.explanation is None      # blank -> the service supplies the wording


def test_assessor_with_no_evidence_returns_needs_review_without_calling_the_model():
    fake = _FakeOpenAI('{"status": "supported", "evidence_indices": [0]}')
    result = OpenAIAssessorClient(_settings(), client=fake).assess("A claim.", [])
    assert result.status == AssessmentStatus.NEEDS_REVIEW
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# The real assessor through the service: the §2.5 invariant still binds it
# --------------------------------------------------------------------------- #
def _factual_claim(evidence: list[Evidence]) -> Claim:
    return Claim(
        id="c1",
        text="Singapore recorded 3,363 impersonation scam cases in 2025.",
        claim_type=ClaimType.FACTUAL,
        checkworthiness=0.9,
        rank=1,
        evidence=evidence,
    )


def test_service_threads_the_models_explanation_into_the_assessment():
    fake = _FakeOpenAI(
        '{"status": "supported", "evidence_indices": [0], "confidence": 0.8,'
        ' "explanation": "The cited police statement gives the same 3,363 figure."}'
    )
    client = OpenAIAssessorClient(_settings(), client=fake)

    a = assess_claim(_factual_claim([_evidence("Police reported 3,363 cases.")]), client=client)

    assert a.status == AssessmentStatus.SUPPORTED
    assert a.explanation == "The cited police statement gives the same 3,363 figure."
    assert [c.snippet for c in a.citations] == ["Police reported 3,363 cases."]


def test_real_assessor_cannot_assert_a_verdict_it_did_not_cite():
    # §2.5 lives in the service, so it constrains the real provider exactly as it does
    # the mock: an affirmative status with no usable index is downgraded.
    fake = _FakeOpenAI(
        '{"status": "supported", "evidence_indices": [9], "confidence": 0.99,'
        ' "explanation": "A source I made up says so."}'
    )
    client = OpenAIAssessorClient(_settings(), client=fake)

    a = assess_claim(_factual_claim([_evidence("Unrelated snippet.")]), client=client)

    assert a.status == AssessmentStatus.NEEDS_REVIEW
    assert a.citations == []
    assert a.confidence is None
    assert "A source I made up" not in a.explanation


def test_mock_assessor_also_supplies_an_explanation():
    # Both providers now behave the same way through the service.
    raw = MockAssessorClient().assess("A claim.", [_evidence("Records indicate that X.")])
    assert raw.explanation == "1 retrieved source supports this claim."


# --------------------------------------------------------------------------- #
# Tavily search client
# --------------------------------------------------------------------------- #
def test_search_maps_results_to_hits():
    fake = _FakeTavily(
        {
            "results": [
                {
                    "url": "https://police.gov.example/report",
                    "content": "Police reported 3,363 impersonation scam cases.",
                    "title": "Annual scam report",
                    "published_date": "2026-01-15",
                    "score": 0.91,
                }
            ]
        }
    )
    hits = TavilySearchClient(_settings(), client=fake).search("scam cases", max_results=3)

    assert hits == [
        SearchHit(
            snippet="Police reported 3,363 impersonation scam cases.",
            url="https://police.gov.example/report",
            title="Annual scam report",
            published_at="2026-01-15",
            score=0.91,
        )
    ]
    assert fake.calls[0] == {
        "query": "scam cases",
        "max_results": 3,
        "search_depth": "basic",
        "include_answer": False,
    }


def test_search_drops_uncitable_hits_and_truncates_snippets():
    fake = _FakeTavily(
        {
            "results": [
                {"content": "No url here.", "title": "T"},
                {"url": "https://x.example/1", "content": ""},
                "not a dict",
                {"url": "https://x.example/2", "content": "y" * 900, "title": None},
            ]
        }
    )
    hits = TavilySearchClient(_settings(), client=fake).search("q", max_results=5)
    assert [h.url for h in hits] == ["https://x.example/2"]
    assert len(hits[0].snippet) == 500


def test_search_respects_max_results():
    fake = _FakeTavily(
        {"results": [{"url": f"https://x.example/{i}", "content": "snippet"} for i in range(6)]}
    )
    assert len(TavilySearchClient(_settings(), client=fake).search("q", max_results=2)) == 2


@pytest.mark.parametrize(
    "fake",
    [
        _FakeTavily(error=RuntimeError("connection reset")),
        _FakeTavily(None),
        _FakeTavily({}),
        _FakeTavily({"results": None}),
    ],
)
def test_search_never_throws_into_the_pipeline(fake):
    assert TavilySearchClient(_settings(), client=fake).search("q", max_results=3) == []
