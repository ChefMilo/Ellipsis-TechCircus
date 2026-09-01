"""Unit tests for source-text anchoring (app/services/anchoring.py).

The contract WS2 relies on is narrow and worth pinning precisely:
  * offsets index the ORIGINAL source text, so `source[start:end]` is the real span
  * a verbatim claim resolves exactly; a rewritten one resolves to its source sentence
  * an unrelated claim resolves to nothing rather than to a bad guess
  * prefix/suffix are literal slices of the source, and None at the boundaries
"""
from __future__ import annotations

from pathlib import Path

from app.services.anchoring import CONTEXT_CHARS, locate_claim

MIN_SIM = 0.5

# Two sentences, second one repeated later, plus a paragraph break — the same shape as a
# real article body (WS1 hands us `bodyText` with newlines intact).
SOURCE = (
    "Singapore recorded 3,363 cases of government-official-impersonation scams in 2025, "
    "up from 1,504 cases in 2024. Victims lost a total of S$242.9 million to these scams "
    "last year.\n\nThe police said many of the scams were driven by AI-generated deepfake "
    "video calls."
)

FIRST_SENTENCE = (
    "Singapore recorded 3,363 cases of government-official-impersonation scams in 2025, "
    "up from 1,504 cases in 2024."
)
LAST_SENTENCE = "The police said many of the scams were driven by AI-generated deepfake video calls."


def test_verbatim_claim_resolves_exactly():
    found = locate_claim(FIRST_SENTENCE, SOURCE, min_similarity=MIN_SIM)
    assert found is not None
    start, end, _prefix, _suffix = found
    assert SOURCE[start:end] == FIRST_SENTENCE


def test_offsets_are_in_range():
    found = locate_claim(LAST_SENTENCE, SOURCE, min_similarity=MIN_SIM)
    assert found is not None
    start, end, _p, _s = found
    assert 0 <= start < end <= len(SOURCE)


def test_prefix_and_suffix_are_literal_slices_of_source():
    claim = "Victims lost a total of S$242.9 million to these scams last year."
    found = locate_claim(claim, SOURCE, min_similarity=MIN_SIM)
    assert found is not None
    start, end, prefix, suffix = found

    assert SOURCE[start:end] == claim
    assert prefix == SOURCE[start - CONTEXT_CHARS:start]
    assert suffix == SOURCE[end:end + CONTEXT_CHARS]
    assert len(prefix) <= CONTEXT_CHARS
    assert len(suffix) <= CONTEXT_CHARS


def test_prefix_is_none_at_start_of_text():
    # FIRST_SENTENCE opens the source, so there is nothing before it.
    found = locate_claim(FIRST_SENTENCE, SOURCE, min_similarity=MIN_SIM)
    assert found is not None
    _start, _end, prefix, suffix = found
    assert prefix is None
    assert suffix is not None


def test_suffix_is_none_at_end_of_text():
    found = locate_claim(LAST_SENTENCE, SOURCE, min_similarity=MIN_SIM)
    assert found is not None
    _start, _end, prefix, suffix = found
    assert prefix is not None
    assert suffix is None


def test_claim_spanning_a_paragraph_break_still_maps_to_original_offsets():
    # A claim quoted with collapsed whitespace must still resolve against a source that
    # contains the original "\n\n" — and the offsets must index the ORIGINAL text.
    claim = "last year. The police said many of the scams"
    found = locate_claim(claim, SOURCE, min_similarity=MIN_SIM)
    assert found is not None
    start, end, _p, _s = found
    span = SOURCE[start:end]
    assert "\n\n" in span, "expected the original span to retain its paragraph break"
    assert span.split() == claim.split()


def test_paraphrased_claim_resolves_to_its_source_sentence():
    # What the real OpenAI extractor does: rewrite into a self-contained sentence.
    paraphrase = (
        "In 2025 Singapore recorded 3,363 government-official-impersonation scam cases, "
        "up from 1,504 in 2024."
    )
    found = locate_claim(paraphrase, SOURCE, min_similarity=MIN_SIM)
    assert found is not None
    start, end, _p, _s = found
    assert SOURCE[start:end] == FIRST_SENTENCE


def test_unrelated_claim_returns_none():
    found = locate_claim(
        "The Eiffel Tower is located in Paris, France.", SOURCE, min_similarity=MIN_SIM
    )
    assert found is None


def test_blank_inputs_return_none():
    assert locate_claim("   ", SOURCE, min_similarity=MIN_SIM) is None
    assert locate_claim(FIRST_SENTENCE, "", min_similarity=MIN_SIM) is None


def test_repeated_sentence_resolves_to_first_occurrence():
    doubled = FIRST_SENTENCE + " Filler in between. " + FIRST_SENTENCE
    found = locate_claim(FIRST_SENTENCE, doubled, min_similarity=MIN_SIM)
    assert found is not None
    start, end, _p, _s = found
    assert start == 0
    assert doubled[start:end] == FIRST_SENTENCE


def test_threshold_is_respected():
    # A weak overlap resolves at a permissive threshold and not at a strict one.
    weak = "Singapore scams 2025."
    assert locate_claim(weak, SOURCE, min_similarity=0.1) is not None
    assert locate_claim(weak, SOURCE, min_similarity=0.99) is None


def test_deterministic():
    a = locate_claim(FIRST_SENTENCE, SOURCE, min_similarity=MIN_SIM)
    b = locate_claim(FIRST_SENTENCE, SOURCE, min_similarity=MIN_SIM)
    assert a == b


def test_every_sentence_of_the_real_fixture_anchors():
    text = (Path(__file__).parent / "fixtures" / "sample_article.txt").read_text(
        encoding="utf-8"
    )
    body = text.split("\n", 1)[1].strip()

    for sentence in [s.strip() for s in body.replace("\n", " ").split(". ") if s.strip()]:
        found = locate_claim(sentence, body, min_similarity=MIN_SIM)
        assert found is not None, f"failed to anchor: {sentence[:60]!r}"
        start, end, _p, _s = found
        assert 0 <= start < end <= len(body)
