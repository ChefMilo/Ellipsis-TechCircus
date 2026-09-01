"""Resolve where a claim came from in the original article text.

WS2 re-anchors each claim onto the live DOM. When a claim is a verbatim slice of the
article it can do that itself, but the real LLM extractor rewrites claims into
self-contained sentences ("resolve pronouns"), which destroys the correspondence. So
WS5 resolves anchors CENTRALLY, here, at extraction time — while we still hold the
source text the claim was derived from.

Two strategies, in order:
  1. EXACT   — the claim occurs verbatim (or modulo whitespace) in the source.
  2. FUZZY   — score every source sentence against the claim by token Dice and take
               the best, provided it clears `min_similarity`.

Offsets ALWAYS index the ORIGINAL `source_text`, never a normalized copy, because that
is the string WS2 maps onto the DOM (WS1's `bodyText`). Pure, deterministic, offline:
no network, no model calls.
"""
from __future__ import annotations

import re

# How much surrounding text to hand WS2 as disambiguation context. Short enough to stay
# cheap on the wire, long enough to separate two copies of a repeated sentence.
CONTEXT_CHARS = 32

_TOKEN = re.compile(r"[a-z0-9]+")
# Split after sentence-ending punctuation. Deliberately the same shape as the mock
# extractor's splitter so the fuzzy path sees the same units the LLM path produces.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _dice(a: set[str], b: set[str]) -> float:
    """Sørensen-Dice over token sets. 1.0 = identical vocabulary, 0.0 = disjoint."""
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to single spaces, returning the normalized string plus a
    per-character map back to indices in `text`. This is what lets an exact match survive
    the paragraph breaks in the source while still reporting original offsets."""
    chars: list[str] = []
    index_map: list[int] = []
    prev_was_space = False

    for i, ch in enumerate(text):
        if ch.isspace():
            # Drop leading whitespace and collapse runs; one space stands for the run,
            # mapped to the first original character of that run.
            if prev_was_space or not chars:
                prev_was_space = True
                continue
            chars.append(" ")
            index_map.append(i)
            prev_was_space = True
        else:
            chars.append(ch)
            index_map.append(i)
            prev_was_space = False

    while chars and chars[-1] == " ":
        chars.pop()
        index_map.pop()

    return "".join(chars), index_map


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Shrink [start, end) so it excludes leading/trailing whitespace."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _exact_span(claim_text: str, source_text: str) -> tuple[int, int] | None:
    """Verbatim match, first trying the raw string and then a whitespace-normalized
    comparison. Returns offsets into the ORIGINAL source_text.

    A repeated sentence resolves to its FIRST occurrence — deterministic, and the
    prefix/suffix context is what disambiguates the copies for WS2."""
    if not claim_text:
        return None

    direct = source_text.find(claim_text)
    if direct >= 0:
        return direct, direct + len(claim_text)

    norm_source, index_map = _normalize_with_map(source_text)
    norm_claim, _ = _normalize_with_map(claim_text)
    if not norm_claim:
        return None

    found = norm_source.find(norm_claim)
    if found < 0:
        return None

    start = index_map[found]
    # The last normalized character maps to the first original char of its run, so the
    # exclusive end is one past it.
    end = index_map[found + len(norm_claim) - 1] + 1
    return start, end


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentence [start, end) offsets in `text`, whitespace-trimmed, empties dropped."""
    spans: list[tuple[int, int]] = []
    cursor = 0

    for boundary in _SENTENCE_BOUNDARY.finditer(text):
        start, end = _trim_span(text, cursor, boundary.start())
        if end > start:
            spans.append((start, end))
        cursor = boundary.end()

    start, end = _trim_span(text, cursor, len(text))
    if end > start:
        spans.append((start, end))

    return spans


def _fuzzy_span(claim_text: str, source_text: str, min_similarity: float) -> tuple[int, int] | None:
    """Best-scoring source sentence, or None if nothing clears the threshold.

    Ties go to the earliest sentence (strict `>` keeps the first winner), so a repeated
    sentence anchors the same way the exact path does."""
    claim_tokens = _tokens(claim_text)
    if not claim_tokens:
        return None

    best_span: tuple[int, int] | None = None
    best_score = 0.0

    for start, end in _sentence_spans(source_text):
        score = _dice(claim_tokens, _tokens(source_text[start:end]))
        if score > best_score:
            best_score = score
            best_span = (start, end)

    if best_span is None or best_score < min_similarity:
        return None
    return best_span


def _context(source_text: str, start: int, end: int) -> tuple[str | None, str | None]:
    """Up to CONTEXT_CHARS of original text on each side; None at the boundaries."""
    prefix = source_text[max(0, start - CONTEXT_CHARS):start]
    suffix = source_text[end:end + CONTEXT_CHARS]
    return (prefix or None), (suffix or None)


def locate_claim(
    claim_text: str,
    source_text: str,
    *,
    min_similarity: float,
) -> tuple[int, int, str | None, str | None] | None:
    """Locate `claim_text` within `source_text`.

    Returns `(char_start, char_end, prefix, suffix)` with offsets into `source_text`
    (so `source_text[char_start:char_end]` is the matched span), or None when the claim
    cannot be placed confidently — in which case WS5 emits null anchors and WS2 falls
    back to its own fuzzy matching.
    """
    if not claim_text.strip() or not source_text:
        return None

    span = _exact_span(claim_text, source_text) or _fuzzy_span(
        claim_text, source_text, min_similarity
    )
    if span is None:
        return None

    start, end = span
    prefix, suffix = _context(source_text, start, end)
    return start, end, prefix, suffix
