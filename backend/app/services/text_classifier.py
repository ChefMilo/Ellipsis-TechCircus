"""Heuristic fake-news risk scoring — the offline fallback for Tier 2 text screening.

This is NOT the model the proposal promises. §2.2 specifies a BERT classifier fine-tuned
for fake-news detection, and that lives in `app/clients/hf_text.py`. This module exists so
that tests, CI and an offline demo have a deterministic, dependency-free path, and so a
missing checkpoint degrades quality instead of breaking the tier.

Because it is a fallback, it must never be mistaken for the real thing: `ws4_eval.py`
refuses to report accuracy figures from this backend, and every degraded `ScreeningResult`
carries a `degraded_reason` naming the fallback.

Feature set (weights in _WEIGHTS, all features clipped to [0,1]):

  risk-raising    sensational lexicon, shouting, exclamation density, absolutist
                  language, share-bait, vague/unnamed sourcing
  risk-lowering   explicit attribution ("according to X", "X said"), factual
                  specificity (figures, dates, currency)

Combined as a logistic of a weighted sum, so the output is a calibratable
probability-like number rather than an arbitrary total.
"""
from __future__ import annotations

import math
import re

# --------------------------------------------------------------------------- #
# Lexicons — intentionally small, inspectable, and defensible in the pitch.
# --------------------------------------------------------------------------- #
_SENSATIONAL = (
    "shocking", "you won't believe", "you wont believe", "what happened next",
    "they don't want you to know", "they dont want you to know", "wake up",
    "exposed", "bombshell", "the truth about", "mainstream media", "cover-up",
    "coverup", "hoax", "outrageous", "banned", "censored",
    "big pharma", "deep state", "conspiracy", "miracle cure",
    "unbelievable", "jaw-dropping", "this is why",
)
_ABSOLUTIST = (
    "everyone knows", "no one is talking about", "nobody is talking about",
    "100%", "completely false", "totally fake", "proven beyond", "undeniable",
    "guaranteed", "without a doubt", "all of them",
)
_SHARE_BAIT = (
    "share this", "share before", "before it's deleted", "before its deleted",
    "forward this", "spread the word", "tell everyone", "must read", "repost",
    "warn your friends", "circulate this",
)
_VAGUE_SOURCING = (
    "sources say", "sources claim", "insiders say", "insiders claim", "it is said",
    "people are saying", "many believe", "some say", "rumour has it", "rumor has it",
    "an anonymous",
)
_ATTRIBUTION = (
    "according to", "said in a statement", "confirmed that",
    "announced that", "the ministry said", "police said", "a spokesperson",
    "the study found", "researchers found", "data from", "in a statement",
    "court documents", "official figures", "the agency said", "the authority said",
)

# Sensational words rendered in capitals. This is an INCLUSION list, not a general
# ALL-CAPS density measure, and that is deliberate: Singapore news prose is dense with
# legitimate three-letter acronyms (MOH, NEA, HDB, CPF, MRT, COE, GST), so a generic
# `[A-Z]{3,}` density feature scores ordinary local reporting as shouting. Matching a
# fixed list of clickbait words in caps cannot fire on an acronym at all.
# "BREAKING" is excluded on purpose — real newsrooms use it.
_SHOUTED = (
    "SHOCKING", "URGENT", "ALERT", "WARNING", "EXPOSED", "BANNED", "SCANDAL",
    "NOW", "MUST", "STOP", "NEVER", "ALWAYS", "EVERYONE", "TRUTH", "FAKE",
    "HATE", "REALLY", "PROOF", "SHARE",
)

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_EXCLAIM = re.compile(r"[!?]")
_NUMBER = re.compile(r"\b\d[\d,.]*\b")
_DATE = re.compile(
    r"\b(19|20)\d{2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.IGNORECASE,
)
_MONEY_PCT = re.compile(r"[$€£%]|\bs\$|\bper cent\b|\bpercent\b", re.IGNORECASE)
# "X said" / "said X" with a capitalised name — a *named* attribution, unlike "sources say".
_NAMED_ATTRIBUTION = re.compile(r"\b[A-Z][a-z]+\s+(?:said|told|confirmed|announced|reported)\b")

# feature name -> (weight, saturation point). A feature reaches 1.0 at its saturation
# count; weights are the numbers to defend when a judge asks "why this score".
_WEIGHTS: dict[str, tuple[float, float]] = {
    "sensational":    (2.6, 3.0),
    "shouting":       (1.4, 2.0),    # count of sensational words in caps
    "exclamation":    (1.2, 1.5),    # per 100 words
    "absolutist":     (1.0, 3.0),
    "share_bait":     (1.6, 1.0),
    "vague_sourcing": (1.0, 2.0),
    "attribution":    (-1.8, 3.0),
    "specificity":    (-0.9, 6.0),
}
_BIAS = -2.0

# Below this many words there is not enough signal to score; return the neutral prior
# instead of letting one stray exclamation mark dominate a two-sentence page. Shared with
# the real backend, which otherwise happily returns a confident score for six words.
MIN_SCOREABLE_WORDS = 25
NEUTRAL_PRIOR = 0.20

BACKEND_NAME = "heuristic-text-v1"


def _count_phrases(low: str, phrases: tuple[str, ...]) -> int:
    return sum(low.count(p) for p in phrases)


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def heuristic_features(text: str) -> dict[str, float]:
    """Extract the normalised [0,1] feature vector. Exposed for the eval script and tests."""
    n_words = word_count(text)
    if n_words == 0:
        return dict.fromkeys(_WEIGHTS, 0.0)

    low = text.lower()
    per_100 = 100.0 / n_words

    raw = {
        "sensational": float(_count_phrases(low, _SENSATIONAL)),
        "shouting": float(sum(text.count(w) for w in _SHOUTED)),
        "exclamation": len(_EXCLAIM.findall(text)) * per_100,
        "absolutist": float(_count_phrases(low, _ABSOLUTIST)),
        "share_bait": float(_count_phrases(low, _SHARE_BAIT)),
        "vague_sourcing": float(_count_phrases(low, _VAGUE_SOURCING)),
        "attribution": float(
            _count_phrases(low, _ATTRIBUTION) + len(_NAMED_ATTRIBUTION.findall(text))
        ),
        "specificity": float(
            len(_NUMBER.findall(text)) + len(_DATE.findall(text)) + len(_MONEY_PCT.findall(text))
        ),
    }
    return {name: min(1.0, raw[name] / sat) for name, (_, sat) in _WEIGHTS.items()}


def heuristic_score(title: str | None, text: str) -> tuple[float, dict[str, float]]:
    """Deterministic fake-news-likeness score in [0,1] plus its feature breakdown.

    The title is weighted alongside the body because clickbait concentrates in headlines;
    repeating it twice is a cheap way to give it roughly a paragraph's worth of influence.
    """
    blob = f"{title}. {title}. {text}" if title else text
    if word_count(blob) < MIN_SCOREABLE_WORDS:
        return NEUTRAL_PRIOR, dict.fromkeys(_WEIGHTS, 0.0)

    features = heuristic_features(blob)
    z = _BIAS + sum(weight * features[name] for name, (weight, _) in _WEIGHTS.items())
    return round(_sigmoid(z), 4), features
