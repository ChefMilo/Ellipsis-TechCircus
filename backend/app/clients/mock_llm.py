"""Deterministic mock LLM claim extractor.

Runs with no API key. Produces realistic-looking claim extraction on ANY article by
applying transparent heuristics, so WS2/WS6 can integrate against live-shaped data and
the demo works offline. Swap in the real OpenAI client (same interface) later.

Heuristics (intentionally simple and inspectable):
  * split into sentences
  * OPINION if it carries subjective markers ("best", "should", "I think", ...)
  * PREDICTION if it carries future markers ("will", "expected to", "by 2030", ...)
  * otherwise FACTUAL
  * checkworthiness rises with: numbers, %/$, dates, proper-noun density, attribution
    ("according to"), and falls for very short/very long sentences.
"""
from __future__ import annotations

import re

from app.clients.base import ExtractedClaim, LLMClient

_OPINION_MARKERS = {
    "should", "must", "best", "worst", "beautiful", "amazing", "terrible",
    "i think", "i believe", "in my opinion", "arguably", "clearly the",
    "deserve", "ought", "wonderful", "awful", "great", "disgusting",
}
_PREDICTION_MARKERS = {
    "will", "won't", "going to", "expected to", "is set to", "predicts",
    "forecast", "by 2027", "by 2028", "by 2029", "by 2030", "next year",
    "in the coming", "plans to", "aims to", "could soon",
}
_ATTRIBUTION_MARKERS = ("according to", "said", "reported", "announced", "confirmed", "study found", "data show")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_NUMBER = re.compile(r"\b\d[\d,\.]*\b")
_MONEY_PCT = re.compile(r"[$€£%]|\bper cent\b|\bpercent\b", re.IGNORECASE)
_DATE = re.compile(r"\b(19|20)\d{2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.IGNORECASE)
_PROPER = re.compile(r"\b([A-Z][a-z]{2,})\b")


def _split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = _SENTENCE_SPLIT.split(text)
    return [p.strip() for p in parts if len(p.strip()) > 0]


def _classify(sentence: str) -> str:
    low = sentence.lower()
    if any(m in low for m in _PREDICTION_MARKERS):
        return "prediction"
    if any(m in low for m in _OPINION_MARKERS):
        return "opinion"
    return "factual"


def _checkworthiness(sentence: str) -> float:
    words = sentence.split()
    n = len(words)
    if n < 5:
        return 0.05
    score = 0.30
    if _NUMBER.search(sentence):
        score += 0.25
    if _MONEY_PCT.search(sentence):
        score += 0.15
    if _DATE.search(sentence):
        score += 0.10
    proper = len(_PROPER.findall(sentence))
    score += min(proper, 4) * 0.06
    if any(m in sentence.lower() for m in _ATTRIBUTION_MARKERS):
        score += 0.10
    # Penalise unwieldy sentences (hard to check as one unit).
    if n > 45:
        score -= 0.15
    return max(0.0, min(1.0, round(score, 3)))


class MockLLMClient(LLMClient):
    name = "mock-llm-heuristic-v1"

    def extract_claims(self, *, title: str | None, text: str) -> list[ExtractedClaim]:
        out: list[ExtractedClaim] = []
        for sentence in _split_sentences(text):
            ctype = _classify(sentence)
            cw = _checkworthiness(sentence)
            # Non-factual statements get a low floor so ranking naturally deprioritises them.
            if ctype != "factual":
                cw = min(cw, 0.2)
            out.append(ExtractedClaim(text=sentence, claim_type=ctype, checkworthiness=cw))
        return out
