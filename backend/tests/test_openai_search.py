"""Unit tests for the OpenAI web-search client — no network, no API key, no SDK needed.

The client takes an injected transport (`client=`), so these tests hand it a canned
Responses payload shaped like the real one: `output` -> `message` -> `output_text` parts
carrying `url_citation` annotations (openai/types/responses/response_output_text.py).

The interesting assertions are the failure modes. This provider's evidence comes out of a
model's answer, so the shape is only as stable as the model's behaviour: an answer with no
citations, a citation with no url, or a payload we do not recognise must all cost us hits
and nothing else. Returning [] keeps the pipeline honest — WS5 reports a claim with no
evidence and WS6 reports needs_review, which is the truth.
"""
from __future__ import annotations

import pytest

from app.clients.base import SearchClient, SearchHit
from app.clients.factory import make_search_client
from app.clients.mock_search import MockSearchClient
from app.clients.openai_search import OpenAISearchClient, hits_from_response
from app.config import Settings


# --------------------------------------------------------------------------- #
# Canned Responses-API payloads
# --------------------------------------------------------------------------- #
class _Annotation:
    def __init__(self, url, title="A title", start_index=0, end_index=0, type="url_citation"):
        self.type = type
        self.url = url
        self.title = title
        self.start_index = start_index
        self.end_index = end_index


class _OutputText:
    type = "output_text"

    def __init__(self, text: str, annotations: list) -> None:
        self.text = text
        self.annotations = annotations


class _OutputMessage:
    type = "message"

    def __init__(self, content: list) -> None:
        self.content = content


class _WebSearchCall:
    """The tool-call item that sits alongside the message in a real payload."""

    type = "web_search_call"
    status = "completed"


class _Response:
    def __init__(self, output: list) -> None:
        self.output = output


class _FakeResponses:
    def __init__(self, parent: _FakeOpenAI) -> None:
        self._parent = parent

    def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        errors = self._parent.errors
        if errors:
            error = errors.pop(0)
            if error is not None:
                raise error
        return self._parent.response


class _FakeOpenAI:
    """Stands in for `openai.OpenAI` — same `.responses.create` surface.

    `errors` is a per-call script: an exception is raised for that call, None lets it
    through. That is how the tool-type retry is exercised.
    """

    def __init__(self, response=None, *, errors: list | None = None) -> None:
        self.response = response
        self.errors = list(errors or [])
        self.calls: list[dict] = []
        self.responses = _FakeResponses(self)


ANSWER = (
    "Police figures put impersonation scam losses at S$1.1 billion in 2024. "
    "A separate review by the central bank reported a smaller recovery figure."
)
FIRST = ANSWER.index("Police")
FIRST_END = ANSWER.index(" A separate")
SECOND = ANSWER.index("A separate")
SECOND_END = len(ANSWER)


def _two_citation_response() -> _Response:
    return _Response(
        [
            _WebSearchCall(),
            _OutputMessage(
                [
                    _OutputText(
                        ANSWER,
                        [
                            _Annotation(
                                "https://police.gov.example/annual-2024",
                                "Annual scam statistics 2024",
                                FIRST,
                                FIRST_END,
                            ),
                            _Annotation(
                                "https://centralbank.example/review",
                                "Payments review",
                                SECOND,
                                SECOND_END,
                            ),
                        ],
                    )
                ]
            ),
        ]
    )


def _settings(**kw) -> Settings:
    return Settings(**kw)


def _client(response=None, *, errors: list | None = None) -> OpenAISearchClient:
    fake = _FakeOpenAI(response, errors=errors)
    client = OpenAISearchClient(_settings(), client=fake)
    client._fake = fake  # type: ignore[attr-defined]  # handle for call assertions
    return client


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_citations_become_search_hits():
    hits = _client(_two_citation_response()).search("scam losses 2024", max_results=5)

    assert hits == [
        SearchHit(
            snippet="Police figures put impersonation scam losses at S$1.1 billion in 2024.",
            url="https://police.gov.example/annual-2024",
            title="Annual scam statistics 2024",
            published_at=None,
            score=None,
        ),
        SearchHit(
            snippet="A separate review by the central bank reported a smaller recovery figure.",
            url="https://centralbank.example/review",
            title="Payments review",
            published_at=None,
            score=None,
        ),
    ]


def test_it_asks_for_the_hosted_web_search_tool():
    client = _client(_two_citation_response())
    client.search("Singapore scam losses", max_results=3)

    call = client._fake.calls[0]
    assert call["tools"] == [{"type": "web_search"}]
    assert call["model"] == _settings().openai_search_model
    assert "Singapore scam losses" in call["input"]
    assert "primary sources" in call["instructions"]
    assert client.name == f"openai-web-search:{_settings().openai_search_model}"


def test_a_response_in_dict_form_works_too():
    # e.g. a caller handing us response.model_dump().
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": ANSWER,
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://police.gov.example/annual-2024",
                                "title": "Annual scam statistics 2024",
                                "start_index": FIRST,
                                "end_index": FIRST_END,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    hits = hits_from_response(payload, max_results=5)
    assert [h.url for h in hits] == ["https://police.gov.example/annual-2024"]
    assert hits[0].snippet.startswith("Police figures")


def test_duplicate_urls_are_collapsed():
    url = "https://police.gov.example/annual-2024"
    response = _Response(
        [
            _OutputMessage(
                [
                    _OutputText(
                        ANSWER,
                        [
                            _Annotation(url, "T", FIRST, FIRST_END),
                            _Annotation(url, "T", SECOND, SECOND_END),
                        ],
                    )
                ]
            )
        ]
    )
    assert [h.url for h in hits_from_response(response, max_results=5)] == [url]


def test_max_results_is_respected():
    annotations = [
        _Annotation(f"https://src.example/{i}", f"T{i}", FIRST, FIRST_END) for i in range(6)
    ]
    response = _Response([_OutputMessage([_OutputText(ANSWER, annotations)])])
    assert len(hits_from_response(response, max_results=2)) == 2


def test_a_short_span_falls_back_to_the_answer_segment():
    # Models often annotate only a trailing clause; a 9-character fragment is not evidence.
    response = _Response(
        [_OutputMessage([_OutputText(ANSWER, [_Annotation("https://x.example/1", "T", 0, 9)])])]
    )
    hits = hits_from_response(response, max_results=5)
    assert hits[0].snippet == ANSWER


def test_snippets_are_truncated_like_the_tavily_client():
    long_answer = "y" * 900
    response = _Response(
        [
            _OutputMessage(
                [_OutputText(long_answer, [_Annotation("https://x.example/1", "T", 0, 900)])]
            )
        ]
    )
    assert len(hits_from_response(response, max_results=5)[0].snippet) == 500


def test_a_missing_title_is_none_not_empty_string():
    response = _Response(
        [
            _OutputMessage(
                [_OutputText(ANSWER, [_Annotation("https://x.example/1", None, FIRST, FIRST_END)])]
            )
        ]
    )
    assert hits_from_response(response, max_results=5)[0].title is None


# --------------------------------------------------------------------------- #
# Nothing citable -> no hits (never an exception)
# --------------------------------------------------------------------------- #
def test_an_answer_with_no_citations_yields_nothing():
    # The model answered from memory instead of searching. That is not evidence.
    response = _Response([_OutputMessage([_OutputText("I could not find sources.", [])])])
    assert _client(response).search("q", max_results=3) == []


def test_citations_without_a_url_are_dropped():
    response = _Response(
        [
            _OutputMessage(
                [
                    _OutputText(
                        ANSWER,
                        [
                            _Annotation(None, "no url", FIRST, FIRST_END),
                            _Annotation("", "empty url", FIRST, FIRST_END),
                            _Annotation(123, "not a string", FIRST, FIRST_END),
                        ],
                    )
                ]
            )
        ]
    )
    assert hits_from_response(response, max_results=5) == []


def test_non_citation_annotations_are_ignored():
    response = _Response(
        [
            _OutputMessage(
                [
                    _OutputText(
                        ANSWER,
                        [
                            _Annotation(
                                "https://x.example/f", "F", FIRST, FIRST_END, type="file_citation"
                            )
                        ],
                    )
                ]
            )
        ]
    )
    assert hits_from_response(response, max_results=5) == []


@pytest.mark.parametrize(
    "response",
    [
        None,
        "a bare string",
        object(),
        _Response([]),
        _Response([_WebSearchCall()]),                       # searched, never answered
        _Response([_OutputMessage([])]),
        _Response([_OutputMessage([_OutputText(None, [])])]),  # text is not a string
        {"output": "not a list"},
        {"output": [{"type": "message", "content": None}]},
        {"output": [{"type": "message", "content": [{"type": "output_text"}]}]},
    ],
)
def test_a_malformed_response_yields_no_hits_and_does_not_crash(response):
    assert hits_from_response(response, max_results=3) == []
    assert _client(response).search("q", max_results=3) == []


def test_an_api_error_yields_no_hits():
    client = _client(_two_citation_response(), errors=[RuntimeError("401 invalid api key")])
    assert client.search("q", max_results=3) == []
    assert len(client._fake.calls) == 1, "an auth error must not be retried"


# --------------------------------------------------------------------------- #
# Tool-type fallback
# --------------------------------------------------------------------------- #
def test_an_unsupported_tool_type_is_retried_with_the_preview_name():
    # `web_search` superseded `web_search_preview`, but which one an account accepts still
    # varies, and a silent [] on demo day would look like "no evidence exists".
    client = _client(
        _two_citation_response(),
        errors=[ValueError("400: Tool type 'web_search' is not supported with this model.")],
    )
    hits = client.search("q", max_results=3)

    assert len(hits) == 2
    assert [c["tools"] for c in client._fake.calls] == [
        [{"type": "web_search"}],
        [{"type": "web_search_preview"}],
    ]


def test_the_retry_is_not_infinite():
    client = _client(
        None,
        errors=[
            ValueError("Tool type 'web_search' is not supported"),
            ValueError("Tool type 'web_search_preview' is not supported"),
        ],
    )
    assert client.search("q", max_results=3) == []
    assert len(client._fake.calls) == 2


# --------------------------------------------------------------------------- #
# Factory wiring
# --------------------------------------------------------------------------- #
def test_mock_is_still_the_default_search_provider():
    assert _settings().search_provider == "mock"
    assert isinstance(make_search_client(_settings()), MockSearchClient)


def test_openai_search_is_registered_and_needs_only_the_openai_key():
    # The error names the SEARCH switch, not the LLM one, so a half-configured run says
    # which env var is missing. And no Tavily key appears anywhere on this path.
    with pytest.raises(RuntimeError, match="DASFAX_SEARCH_PROVIDER=openai but OPENAI_API_KEY"):
        make_search_client(_settings(search_provider="openai", openai_api_key=None))


def test_the_client_satisfies_the_search_protocol():
    # Drop-in with the mock and Tavily: the pipeline only ever sees SearchClient.
    assert isinstance(_client(_two_citation_response()), SearchClient)


def test_unknown_search_provider_names_all_three():
    with pytest.raises(ValueError, match="mock', 'tavily' or 'openai"):
        make_search_client(_settings(search_provider="bing"))
