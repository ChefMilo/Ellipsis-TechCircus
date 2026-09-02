"""Unit tests for the NATIVE Anthropic providers — no network, no key, no SDK installed.

Each client takes an injected transport (`client=`), so these tests hand it canned Messages
responses shaped like the real ones: content blocks of type `tool_use` (forced-tool output),
`text` with `web_search_result_location` citations, and `web_search_tool_result` carrying
`web_search_result` blocks. Shapes checked against the `anthropic` SDK 1.3.0 types.

What is actually being asserted is the untrusted-input boundary. A real model returns wrong
types, missing blocks and occasionally prose where a tool call was demanded, and none of
that may take a request down:
  * bad extraction rows are dropped, the good ones survive;
  * an unusable assessment becomes needs_review, never a crash and never a verdict;
  * a search that found nothing returns no hits so WS5 keeps going.
The mocks remain the default, so none of this runs unless an env var switches it on.
"""
from __future__ import annotations

import pytest

from app.clients import anthropic_llm, openai_llm
from app.clients.anthropic_assessor import AnthropicAssessorClient
from app.clients.anthropic_compat import normalize_tool_input
from app.clients.anthropic_llm import AnthropicLLMClient
from app.clients.anthropic_search import AnthropicSearchClient, hits_from_message
from app.clients.base import AssessedClaim, AssessorClient, ExtractedClaim, LLMClient, SearchClient
from app.clients.factory import make_assessor_client, make_llm_client, make_search_client
from app.clients.mock_assessor import MockAssessorClient
from app.clients.mock_llm import MockLLMClient
from app.clients.mock_search import MockSearchClient
from app.config import DEFAULT_ANTHROPIC_MODEL, DEFAULT_ANTHROPIC_WEB_SEARCH_TOOL, Settings
from app.models.contract import AssessmentStatus, Claim, ClaimType, Evidence
from app.services.assessment import assess_claim


# --------------------------------------------------------------------------- #
# Canned Messages payloads
# --------------------------------------------------------------------------- #
class _ToolUse:
    type = "tool_use"

    def __init__(self, name: str, payload) -> None:
        self.id = "toolu_1"
        self.name = name
        self.input = payload


class _Text:
    type = "text"

    def __init__(self, text: str, citations: list | None = None) -> None:
        self.text = text
        self.citations = citations


class _Citation:
    def __init__(self, url, cited_text, title="A source", type="web_search_result_location"):
        self.type = type
        self.url = url
        self.cited_text = cited_text
        self.title = title
        self.encrypted_index = "opaque"


class _SearchResult:
    type = "web_search_result"

    def __init__(self, url, title="A page", page_age=None) -> None:
        self.url = url
        self.title = title
        self.page_age = page_age
        self.encrypted_content = "opaque"


class _SearchToolResult:
    type = "web_search_tool_result"

    def __init__(self, content) -> None:
        self.tool_use_id = "srvtoolu_1"
        self.content = content


class _ServerToolUse:
    """The `server_tool_use` block that precedes results in a real payload."""

    type = "server_tool_use"
    name = "web_search"


class _Message:
    def __init__(self, content: list) -> None:
        self.content = content
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, parent: _FakeAnthropic) -> None:
        self._parent = parent

    def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        if self._parent.error is not None:
            raise self._parent.error
        return self._parent.message


class _FakeAnthropic:
    """Stands in for `anthropic.Anthropic` — same `.messages.create` surface."""

    def __init__(self, message=None, *, error: Exception | None = None) -> None:
        self.message = message
        self.error = error
        self.calls: list[dict] = []
        self.messages = _FakeMessages(self)


def _settings(**kw) -> Settings:
    return Settings(**kw)


def _evidence(snippet: str, i: int = 0) -> Evidence:
    return Evidence(
        snippet=snippet,
        source_url=f"https://src.example.org/{i}",
        source_title="Source",
        source_domain="src.example.org",
    )


def _llm(message=None, *, error=None) -> AnthropicLLMClient:
    fake = _FakeAnthropic(message, error=error)
    client = AnthropicLLMClient(_settings(), client=fake)
    client._fake = fake  # type: ignore[attr-defined]
    return client


def _assessor(message=None, *, error=None) -> AnthropicAssessorClient:
    fake = _FakeAnthropic(message, error=error)
    client = AnthropicAssessorClient(_settings(), client=fake)
    client._fake = fake  # type: ignore[attr-defined]
    return client


def _searcher(message=None, *, error=None) -> AnthropicSearchClient:
    fake = _FakeAnthropic(message, error=error)
    client = AnthropicSearchClient(_settings(), client=fake)
    client._fake = fake  # type: ignore[attr-defined]
    return client


# --------------------------------------------------------------------------- #
# Defaults and wiring — mock stays default, nothing activates by accident
# --------------------------------------------------------------------------- #
def test_defaults_are_still_all_mock():
    s = _settings()
    assert (s.llm_provider, s.search_provider, s.assessor_provider) == ("mock", "mock", "mock")
    assert isinstance(make_llm_client(s), MockLLMClient)
    assert isinstance(make_search_client(s), MockSearchClient)
    assert isinstance(make_assessor_client(s), MockAssessorClient)


def test_all_three_roles_require_the_anthropic_key_and_name_their_own_switch():
    for factory, switch, field in (
        (make_llm_client, "DASFAX_LLM_PROVIDER", "llm_provider"),
        (make_assessor_client, "DASFAX_ASSESSOR_PROVIDER", "assessor_provider"),
        (make_search_client, "DASFAX_SEARCH_PROVIDER", "search_provider"),
    ):
        with pytest.raises(RuntimeError, match=f"{switch}=anthropic but ANTHROPIC_API_KEY"):
            factory(_settings(**{field: "anthropic"}, anthropic_api_key=None))


def test_the_existing_providers_are_still_accepted():
    # This change is additive: openai/tavily must keep working (they fail on their own
    # missing key, not on an unknown-provider error).
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_llm_client(_settings(llm_provider="openai", openai_api_key=None))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_assessor_client(_settings(assessor_provider="openai", openai_api_key=None))
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        make_search_client(_settings(search_provider="tavily", tavily_api_key=None))


def test_unknown_providers_list_every_accepted_value():
    with pytest.raises(ValueError, match="mock', 'openai' or 'anthropic"):
        make_llm_client(_settings(llm_provider="nope"))
    with pytest.raises(ValueError, match="mock', 'openai' or 'anthropic"):
        make_assessor_client(_settings(assessor_provider="nope"))
    with pytest.raises(ValueError, match="mock', 'tavily' or 'anthropic"):
        make_search_client(_settings(search_provider="nope"))


def test_the_clients_satisfy_their_protocols():
    assert isinstance(_llm(), LLMClient)
    assert isinstance(_assessor(), AssessorClient)
    assert isinstance(_searcher(), SearchClient)


def test_one_key_covers_all_three_roles():
    # The point of the native family: no OPENAI_API_KEY, no TAVILY_API_KEY anywhere.
    s = _settings(anthropic_api_key=None, openai_api_key=None, tavily_api_key=None)
    for client in (_llm(), _assessor(), _searcher()):
        assert client.name.startswith("anthropic")
        assert s.anthropic_model == DEFAULT_ANTHROPIC_MODEL


# --------------------------------------------------------------------------- #
# Extraction ("read")
# --------------------------------------------------------------------------- #
def test_extraction_parses_claims_from_the_tool_call():
    message = _Message(
        [
            _ToolUse(
                "record_claims",
                {
                    "claims": [
                        {
                            "text": "Singapore recorded 3,363 impersonation scam cases in 2025.",
                            "claim_type": "factual",
                            "checkworthiness": 0.9,
                        },
                        {
                            "text": "The policy is the best in the region.",
                            "claim_type": "opinion",
                            "checkworthiness": 0.2,
                        },
                    ]
                },
            )
        ]
    )
    client = _llm(message)

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
    assert client.name == f"anthropic:{DEFAULT_ANTHROPIC_MODEL}"


def test_extraction_forces_the_tool_call():
    client = _llm(_Message([_ToolUse("record_claims", {"claims": []})]))
    client.extract_claims(title=None, text="x")

    call = client._fake.calls[0]
    assert call["model"] == DEFAULT_ANTHROPIC_MODEL
    assert call["tool_choice"] == {"type": "tool", "name": "record_claims"}
    assert call["tools"][0]["name"] == "record_claims"
    assert call["messages"][0]["role"] == "user"
    assert isinstance(call["max_tokens"], int)


def test_the_anchoring_instruction_matches_the_openai_extractor():
    # Both extractors must carry WS5's single-source-sentence guard, or an A/B between
    # providers is comparing prompts instead of models — and unanchored claims cannot be
    # highlighted in the DOM by WS2.
    for system in (anthropic_llm._SYSTEM, openai_llm._SYSTEM):
        assert "SINGLE source sentence" in system
        assert "close to the original" in system


def test_extraction_drops_only_the_bad_rows():
    message = _Message(
        [
            _ToolUse(
                "record_claims",
                {
                    "claims": [
                        {"claim_type": "factual"},                              # no text
                        {"text": "", "checkworthiness": 0.9},                   # empty text
                        {"text": "Good claim.", "checkworthiness": "high"},     # bad number
                        "a bare string",                                        # not an object
                        {
                            "text": "Kept claim.",
                            "claim_type": "FACTUAL",
                            "checkworthiness": 0.8,
                        },
                    ]
                },
            )
        ]
    )
    claims = _llm(message).extract_claims(title="t", text="body")
    assert [c.text for c in claims] == ["Kept claim."]
    assert claims[0].claim_type == "factual"   # case-normalised


def test_extraction_clamps_and_defaults_fields():
    message = _Message(
        [
            _ToolUse(
                "record_claims",
                {
                    "claims": [
                        {"text": "A.", "claim_type": "speculation", "checkworthiness": 4.2},
                        {"text": "B."},
                    ]
                },
            )
        ]
    )
    claims = _llm(message).extract_claims(title=None, text="body")
    assert claims[0].claim_type == "factual"       # unknown type falls back
    assert claims[0].checkworthiness == 1.0        # clamped into 0..1
    assert claims[1].checkworthiness == 0.5        # missing -> neutral default


def test_extraction_falls_back_to_json_in_a_text_block():
    # The model answered in prose despite the forced tool. Parse it rather than lose it.
    message = _Message([_Text('```json\n{"claims": [{"text": "A.", "checkworthiness": 0.7}]}\n```')])
    assert [c.text for c in _llm(message).extract_claims(title=None, text="b")] == ["A."]


@pytest.mark.parametrize(
    "message",
    [
        _Message([]),                                              # no blocks at all
        _Message([_Text("I could not find any claims.")]),         # prose, not JSON
        _Message([_ToolUse("some_other_tool", {"claims": [{"text": "A."}]})]),
        _Message([_ToolUse("record_claims", "not a dict")]),
        _Message([_ToolUse("record_claims", {"claims": "not a list"})]),
        _Message([_ToolUse("record_claims", {})]),
        None,
        "a bare string",
    ],
)
def test_extraction_survives_garbage(message):
    assert _llm(message).extract_claims(title="t", text="body") == []


# --------------------------------------------------------------------------- #
# Assessment ("verify")
# --------------------------------------------------------------------------- #
def _verdict(payload) -> _Message:
    return _Message([_ToolUse("record_verdict", payload)])


def test_assessment_parses_a_verdict():
    message = _verdict(
        {
            "status": "supported",
            "evidence_indices": [0, 2],
            "confidence": 0.82,
            "explanation": "Both cited sources report the same figure.",
        }
    )
    client = _assessor(message)

    result = client.assess("A claim.", [_evidence("s", i) for i in range(3)])

    assert result == AssessedClaim(
        status="supported",
        evidence_indices=[0, 2],
        confidence=0.82,
        explanation="Both cited sources report the same figure.",
    )
    assert client.name == f"anthropic-assessor:{DEFAULT_ANTHROPIC_MODEL}"


def test_assessment_numbers_the_evidence_and_forbids_invented_sources():
    client = _assessor(_verdict({"status": "needs_review", "evidence_indices": []}))
    client.assess("A claim.", [_evidence("First snippet.", 0), _evidence("Second snippet.", 1)])

    call = client._fake.calls[0]
    assert "[0] Source: First snippet." in call["messages"][0]["content"]
    assert "[1] Source: Second snippet." in call["messages"][0]["content"]
    assert "ONLY the numbered evidence" in call["system"]
    assert "Never output a URL" in call["system"]
    assert call["tool_choice"] == {"type": "tool", "name": "record_verdict"}


@pytest.mark.parametrize(
    "message",
    [
        _Message([]),
        _Message([_Text("The claim looks true to me.")]),
        _verdict({"evidence_indices": [0]}),                        # no status
        _verdict({"status": "probably_true", "evidence_indices": [0]}),
        _verdict({"status": "opinion", "evidence_indices": []}),    # not the assessor's call
        _verdict("not a dict"),
        None,
    ],
)
def test_assessment_degrades_to_needs_review(message):
    result = _assessor(message).assess("A claim.", [_evidence("s")])
    assert result.status == AssessmentStatus.NEEDS_REVIEW
    assert result.evidence_indices == []
    assert result.confidence is None
    assert result.explanation


def test_assessment_survives_an_api_error():
    # A rate limit on one claim costs that claim a verdict, not the whole article.
    result = _assessor(error=RuntimeError("429 rate limited")).assess("A claim.", [_evidence("s")])
    assert result.status == AssessmentStatus.NEEDS_REVIEW


def test_assessment_coerces_sloppy_indices_and_confidence():
    message = _verdict(
        {
            "status": "contradicted",
            "evidence_indices": [0, "1", None, "x", True],
            "confidence": "1.7",
            "explanation": "   ",
        }
    )
    result = _assessor(message).assess("A claim.", [_evidence("s")])
    assert result.evidence_indices == [0, 1]
    assert result.confidence == 1.0        # clamped
    assert result.explanation is None      # blank -> the service supplies the wording


def test_assessment_with_no_evidence_does_not_call_the_model():
    client = _assessor(_verdict({"status": "supported", "evidence_indices": [0]}))
    result = client.assess("A claim.", [])
    assert result.status == AssessmentStatus.NEEDS_REVIEW
    assert client._fake.calls == []


# --------------------------------------------------------------------------- #
# Through the service: §2.5 binds this provider exactly as it binds the mock
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


def test_service_threads_the_models_explanation_through():
    message = _verdict(
        {
            "status": "supported",
            "evidence_indices": [0],
            "confidence": 0.8,
            "explanation": "The cited police statement gives the same 3,363 figure.",
        }
    )
    a = assess_claim(
        _factual_claim([_evidence("Police reported 3,363 cases.")]), client=_assessor(message)
    )
    assert a.status == AssessmentStatus.SUPPORTED
    assert a.explanation == "The cited police statement gives the same 3,363 figure."
    assert [c.snippet for c in a.citations] == ["Police reported 3,363 cases."]


def test_an_uncited_verdict_is_downgraded_by_the_service():
    # §2.5 lives in the service, so it constrains this provider too: an affirmative status
    # with no usable index becomes needs_review and the invented reasoning is discarded.
    message = _verdict(
        {
            "status": "supported",
            "evidence_indices": [9],
            "confidence": 0.99,
            "explanation": "A source I made up says so.",
        }
    )
    a = assess_claim(_factual_claim([_evidence("Unrelated snippet.")]), client=_assessor(message))

    assert a.status == AssessmentStatus.NEEDS_REVIEW
    assert a.citations == []
    assert a.confidence is None
    assert "A source I made up" not in a.explanation


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
POLICE = "https://police.gov.example/annual-2024"
BANK = "https://centralbank.example/review"


def _search_message() -> _Message:
    return _Message(
        [
            _ServerToolUse(),
            _SearchToolResult(
                [
                    _SearchResult(POLICE, "Annual scam statistics 2024", page_age="2025-02-18"),
                    _SearchResult(BANK, "Payments review"),
                ]
            ),
            _Text(
                "Police figures put losses at S$1.1 billion.",
                [_Citation(POLICE, "Victims lost S$1.1 billion to scams in 2024.", "Annual scam statistics 2024")],
            ),
        ]
    )


def test_search_maps_results_and_citations_to_hits():
    hits = _searcher(_search_message()).search("scam losses 2024", max_results=5)

    assert [h.url for h in hits] == [POLICE, BANK]
    # A quoted page is represented by the passage the model actually cited...
    assert hits[0].snippet == "Victims lost S$1.1 billion to scams in 2024."
    assert hits[0].title == "Annual scam statistics 2024"
    assert hits[0].published_at == "2025-02-18"
    # ...and a page found but never quoted falls back to its title.
    assert hits[1].snippet == "Payments review"
    assert hits[1].published_at is None
    assert all(h.score is None for h in hits)


def test_search_asks_for_the_web_search_server_tool():
    client = _searcher(_search_message())
    client.search("Singapore scam losses", max_results=3)

    call = client._fake.calls[0]
    assert call["tools"] == [
        {"type": DEFAULT_ANTHROPIC_WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 3}
    ]
    assert "Singapore scam losses" in call["messages"][0]["content"]
    assert "primary sources" in call["system"]
    assert client.name == f"anthropic-web-search:{DEFAULT_ANTHROPIC_MODEL}"


def test_a_citation_only_page_still_becomes_a_hit():
    # The model quoted a page the result block never listed. It is still a real source.
    message = _Message([_Text("Quoted.", [_Citation(BANK, "A passage from the review.")])])
    hits = hits_from_message(message, max_results=5)
    assert [h.url for h in hits] == [BANK]
    assert hits[0].snippet == "A passage from the review."


def test_the_longest_quoted_passage_wins():
    message = _Message(
        [
            _Text("One.", [_Citation(POLICE, "Short.")]),
            _Text("Two.", [_Citation(POLICE, "A considerably longer quoted passage.")]),
        ]
    )
    assert hits_from_message(message, max_results=5)[0].snippet == (
        "A considerably longer quoted passage."
    )


def test_search_respects_max_results():
    message = _Message(
        [_SearchToolResult([_SearchResult(f"https://src.example/{i}") for i in range(6)])]
    )
    assert len(hits_from_message(message, max_results=2)) == 2


def test_search_snippets_are_truncated_like_the_tavily_client():
    message = _Message([_Text("x", [_Citation(POLICE, "y" * 900)])])
    assert len(hits_from_message(message, max_results=5)[0].snippet) == 500


def test_search_drops_hits_with_no_url_or_nothing_readable():
    message = _Message(
        [
            _SearchToolResult(
                [
                    _SearchResult(None, "no url"),
                    _SearchResult("", "empty url"),
                    _SearchResult("https://x.example/1", None),   # nothing readable
                ]
            ),
            _Text("t", [_Citation(None, "no url"), _Citation("https://x.example/2", "")]),
        ]
    )
    assert hits_from_message(message, max_results=5) == []


def test_a_web_search_error_block_yields_nothing():
    # content is an error object rather than a list of results.
    class _Error:
        type = "web_search_tool_result_error"
        error_code = "max_uses_exceeded"

    message = _Message([_SearchToolResult(_Error())])
    assert hits_from_message(message, max_results=5) == []


@pytest.mark.parametrize(
    "message",
    [
        None,
        "a bare string",
        object(),
        _Message([]),
        _Message([_ServerToolUse()]),                      # searched, never reported
        _Message([_Text("No sources found.", None)]),
        _Message([_SearchToolResult(None)]),
        {"content": "not a list"},
        {"content": [{"type": "text", "text": "hi", "citations": None}]},
    ],
)
def test_search_never_throws_into_the_pipeline(message):
    assert hits_from_message(message, max_results=3) == []
    assert _searcher(message).search("q", max_results=3) == []


def test_search_returns_nothing_on_an_api_error():
    assert _searcher(error=RuntimeError("connection reset")).search("q", max_results=3) == []


def test_a_message_in_dict_form_works_too():
    payload = {
        "content": [
            {
                "type": "web_search_tool_result",
                "content": [
                    {"type": "web_search_result", "url": POLICE, "title": "T", "page_age": None}
                ],
            },
            {
                "type": "text",
                "text": "Quoted.",
                "citations": [
                    {
                        "type": "web_search_result_location",
                        "url": POLICE,
                        "cited_text": "A cited passage.",
                        "title": "T",
                    }
                ],
            },
        ]
    }
    hits = hits_from_message(payload, max_results=5)
    assert [(h.url, h.snippet) for h in hits] == [(POLICE, "A cited passage.")]


# --------------------------------------------------------------------------- #
# Regression: double-encoded tool output (observed live, claude-sonnet-5)
#
# The model sometimes fills a forced tool_use input with the WHOLE tool input again,
# JSON-encoded as a string under one of its own schema keys. Before the fix this cost the
# extractor every claim (an empty panel) and would cost the assessor its verdict
# (needs_review for a claim it actually judged) — both silently, because every layer here
# is built to degrade rather than raise.
# --------------------------------------------------------------------------- #

# Captured verbatim from a real run: input["claims"] is a STRING containing {"claims":[...]}.
CAPTURED_DOUBLE_ENCODED_CLAIMS = {
    "claims": (
        '{"claims":[{"text":"Singapore recorded 3,363 impersonation scam cases in 2025.",'
        '"claim_type":"factual","checkworthiness":0.9},'
        '{"text":"Victims lost S$1.1 billion to scams in 2024.",'
        '"claim_type":"factual","checkworthiness":0.85}]}'
    )
}


def test_regression_double_encoded_extraction_still_yields_claims():
    claims = _llm(_Message([_ToolUse("record_claims", CAPTURED_DOUBLE_ENCODED_CLAIMS)])).extract_claims(
        title="Scams", text="body text"
    )

    assert claims != [], "the captured payload must not silently produce zero claims"
    assert [c.text for c in claims] == [
        "Singapore recorded 3,363 impersonation scam cases in 2025.",
        "Victims lost S$1.1 billion to scams in 2024.",
    ]
    assert [c.claim_type for c in claims] == ["factual", "factual"]
    assert [c.checkworthiness for c in claims] == [0.9, 0.85]


def test_regression_double_encoded_verdict_is_still_a_real_verdict():
    # The same quirk applied to record_verdict: the whole verdict encoded as a string
    # under "status". Collapsing to parsed["status"] alone would keep the status but lose
    # the indices, which §2.5 would then downgrade to needs_review — a real verdict thrown
    # away. All four fields must survive.
    payload = {
        "status": (
            '{"status":"supported","evidence_indices":[0,1],"confidence":0.82,'
            '"explanation":"Both cited sources report the same figure."}'
        )
    }
    result = _assessor(_Message([_ToolUse("record_verdict", payload)])).assess(
        "A claim.", [_evidence("s", 0), _evidence("s", 1)]
    )

    assert result.status == AssessmentStatus.SUPPORTED
    assert result.status != AssessmentStatus.NEEDS_REVIEW, "must not degrade to a parse failure"
    assert result.evidence_indices == [0, 1]
    assert result.confidence == 0.82
    assert result.explanation == "Both cited sources report the same figure."


def test_regression_double_encoded_verdict_survives_the_service():
    # End to end: the verdict keeps its citations instead of being §2.5-downgraded.
    payload = {
        "status": (
            '{"status":"supported","evidence_indices":[0],"confidence":0.8,'
            '"explanation":"The cited police statement gives the same figure."}'
        )
    }
    a = assess_claim(
        _factual_claim([_evidence("Police reported 3,363 cases.")]),
        client=_assessor(_Message([_ToolUse("record_verdict", payload)])),
    )
    assert a.status == AssessmentStatus.SUPPORTED
    assert [c.snippet for c in a.citations] == ["Police reported 3,363 cases."]


# --------------------------------------------------------------------------- #
# normalize_tool_input, on its own
# --------------------------------------------------------------------------- #
def test_normalize_collapses_self_nesting():
    assert normalize_tool_input({"claims": '{"claims": [1, 2]}'}) == {"claims": [1, 2]}


def test_normalize_merges_rather_than_dropping_sibling_fields():
    # The load-bearing difference for the assessor: everything the wrapper carried survives.
    payload = {"status": '{"status": "supported", "evidence_indices": [0], "confidence": 0.5}'}
    assert normalize_tool_input(payload) == {
        "status": "supported",
        "evidence_indices": [0],
        "confidence": 0.5,
    }


def test_normalize_decodes_a_plain_encoded_value():
    assert normalize_tool_input({"evidence_indices": "[0, 2]"}) == {"evidence_indices": [0, 2]}


def test_normalize_flattens_a_nested_dict_too():
    # Same quirk without the encoding: the value is already a dict repeating the key.
    assert normalize_tool_input({"claims": {"claims": [1]}}) == {"claims": [1]}


@pytest.mark.parametrize(
    "payload",
    [
        {"explanation": "Both sources agree."},          # an ordinary sentence
        {"explanation": "true"},                          # decodes to a bool -> leave alone
        {"explanation": "42"},                            # decodes to a number -> leave alone
        {"explanation": '"quoted"'},                      # decodes to a string -> leave alone
        {"status": "supported"},
        {"claims": "{not valid json"},
        {"claims": []},
        {"confidence": 0.5},
    ],
)
def test_normalize_leaves_ordinary_values_untouched(payload):
    assert normalize_tool_input(payload) == payload


def test_normalize_tolerates_a_non_dict():
    assert normalize_tool_input(None) == {}
    assert normalize_tool_input("nope") == {}


def test_normalize_also_applies_to_the_text_fallback():
    # The quirk is about how the model encodes its answer, not which block carried it.
    message = _Message([_Text('{"claims": "{\\"claims\\": [{\\"text\\": \\"A.\\"}]}"}')])
    assert [c.text for c in _llm(message).extract_claims(title=None, text="b")] == ["A."]
