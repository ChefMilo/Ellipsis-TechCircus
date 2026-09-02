"""WS3-CONTRACT-AUDIT Task B.

!! THIS IS A HAND PORT OF THE TS GUARD, NOT THE REAL ONE. !!

The real guard is `isAnalysisResponse()` in src/shared/contract.ts (and the private
helpers above it: isRecord, isEvidence, isCitation, isClaim, isAssessment,
isVerifiedClaim). This repo has vitest configured (package.json) but no `node_modules/`
in this checkout, and installing one is out of scope for this task -- so the real guard
was NOT executed. A real vitest test that imports the actual guard and runs this exact
scenario was added instead at src/shared/contract.test.ts ("accepts two claims that both
carry a null assessment"); run `npm install && npm test` to execute the real thing.

Everything below is a line-for-line transliteration of the TS functions, kept in the
same order, so a reader can diff this file against src/shared/contract.ts by eye. Do
NOT treat a pass here as proof the TS guard passes -- it only proves this port does.
See docs/ws3/WS3-CONTRACT-AUDIT.md Task B for the side-by-side quote of both and the reasoning.
"""
from __future__ import annotations

from typing import Any

# --- mirrors the TS enum Sets (contract.ts:113-133) ------------------------------- #
CLAIM_TYPES = {"factual", "opinion", "prediction"}
ASSESSMENT_STATUSES = {"supported", "partially_supported", "contradicted", "needs_review", "opinion"}
ANALYSIS_STATUSES = {"complete", "processing", "failed", "skipped"}
VERDICT_LEVELS = {"trusted", "ok", "caution", "high_risk", "unrated"}


# --- mirrors contract.ts:135-137 --------------------------------------------------- #
def is_record(v: Any) -> bool:
    # TS: `typeof v === "object" && v !== null`. A Python dict is the only JSON shape
    # that corresponds; unlike TS, `typeof [] === "object"` too, but the guard never
    # calls isRecord() on something that could legally be an array, so dict-only is
    # the faithful port here.
    return isinstance(v, dict)


# --- mirrors contract.ts:139-141 --------------------------------------------------- #
def is_evidence(v: Any) -> bool:
    return is_record(v) and isinstance(v.get("snippet"), str) and isinstance(v.get("source_url"), str)


# --- mirrors contract.ts:143-145 --------------------------------------------------- #
def is_citation(v: Any) -> bool:
    return is_record(v) and isinstance(v.get("snippet"), str) and isinstance(v.get("source_url"), str)


# --- mirrors contract.ts:147-158 --------------------------------------------------- #
def is_claim(v: Any) -> bool:
    return (
        is_record(v)
        and isinstance(v.get("id"), str)
        and isinstance(v.get("text"), str)
        and isinstance(v.get("claim_type"), str)
        and v.get("claim_type") in CLAIM_TYPES
        and isinstance(v.get("rank"), int)
        and isinstance(v.get("evidence"), list)
        and all(is_evidence(e) for e in v["evidence"])
    )


# --- mirrors contract.ts:160-170 --------------------------------------------------- #
def is_assessment(v: Any) -> bool:
    return (
        is_record(v)
        and isinstance(v.get("claim_id"), str)
        and isinstance(v.get("status"), str)
        and v.get("status") in ASSESSMENT_STATUSES
        and isinstance(v.get("explanation"), str)
        and isinstance(v.get("citations"), list)
        and all(is_citation(c) for c in v["citations"])
    )


# --- mirrors contract.ts:172-178 --------------------------------------------------- #
def is_verified_claim(v: Any) -> bool:
    # NOTE ON THIS PORT: the naive Python transliteration `v.get("assessment") is None
    # or is_assessment(v.get("assessment"))` is WRONG -- dict.get() returns None both
    # when the key holds an explicit `null` AND when the key is absent, silently
    # collapsing a distinction JS's `v.assessment === null` makes on purpose (a missing
    # key reads as `undefined`, and `undefined === null` is false in JS). The naive
    # version was caught by test_missing_assessment_key_is_rejected_unlike_explicit_null
    # below (it initially failed -- see docs/ws3/WS3-CONTRACT-AUDIT.md Task B for the full story).
    # This corrected version checks key presence explicitly so it actually matches the
    # TS guard's behaviour instead of just this port's Python-idiomatic instinct.
    if not is_record(v) or not is_claim(v.get("claim")):
        return False
    has_key = "assessment" in v
    value = v.get("assessment")
    return (has_key and value is None) or is_assessment(value)


# --- mirrors contract.ts:184-203 (isAnalysisResponse) ------------------------------ #
def is_analysis_response(v: Any) -> bool:
    if not is_record(v):
        return False
    if v.get("schemaVersion") != "1.0":
        return False
    if not isinstance(v.get("url"), str):
        return False
    if not isinstance(v.get("status"), str) or v.get("status") not in ANALYSIS_STATUSES:
        return False
    if not is_record(v.get("articleVerdict")):
        return False
    verdict = v["articleVerdict"]
    if not isinstance(verdict.get("level"), str) or verdict.get("level") not in VERDICT_LEVELS:
        return False
    if not isinstance(verdict.get("summary"), str):
        return False
    if not isinstance(v.get("verifiedClaims"), list) or not all(
        is_verified_claim(vc) for vc in v["verifiedClaims"]
    ):
        return False
    return True


# --------------------------------------------------------------------------- #
# The scenario from docs/ws3/WS3-CONTRACT-AUDIT.md Task B: schemaVersion "1.0", a real url,
# a status, a placeholder articleVerdict, and verifiedClaims with TWO claims whose
# assessment is null.
# --------------------------------------------------------------------------- #
TWO_NULL_ASSESSMENT_ENVELOPE: dict = {
    "schemaVersion": "1.0",
    "url": "https://news.example.org/sg/two-claims-story",
    "status": "complete",
    "articleVerdict": {
        "level": "unrated",
        "summary": "Placeholder verdict pending WS6.",
        "confidence": None,
    },
    "verifiedClaims": [
        {
            "claim": {
                "id": "c1",
                "text": "Singapore recorded 3,363 scam cases in 2025.",
                "claim_type": "factual",
                "checkworthiness": 0.82,
                "rank": 1,
                "search_query": None,
                "evidence": [],
                "char_start": None,
                "char_end": None,
                "prefix": None,
                "suffix": None,
            },
            "assessment": None,
        },
        {
            "claim": {
                "id": "c2",
                "text": "Victims lost S$242.9 million last year.",
                "claim_type": "factual",
                "checkworthiness": 0.77,
                "rank": 2,
                "search_query": None,
                "evidence": [],
                "char_start": None,
                "char_end": None,
                "prefix": None,
                "suffix": None,
            },
            "assessment": None,
        },
    ],
    "errors": [],
}


def test_two_null_assessment_claims_pass_the_ported_guard():
    """Per contract.ts:172-178, `v.assessment === null` is an explicit pass condition
    of isVerifiedClaim -- so this envelope is expected to PASS the port. If this
    assertion ever fails, the port (or the real guard, if it has changed) started
    rejecting an explicit null, which is a live regression: to_analysis_response()'s
    pre-WS6 default path (app/services/envelope.py) emits exactly this shape."""
    assert is_analysis_response(TWO_NULL_ASSESSMENT_ENVELOPE) is True


def test_missing_assessment_key_is_rejected_unlike_explicit_null():
    """Documents the failure mode the contract.py comment (contract.py:260-262) warns
    about: dropping the key entirely (e.g. via `exclude_none=True`) is NOT the same as
    an explicit null, and the guard/port correctly tells them apart."""
    envelope = TWO_NULL_ASSESSMENT_ENVELOPE.copy()
    envelope["verifiedClaims"] = [dict(TWO_NULL_ASSESSMENT_ENVELOPE["verifiedClaims"][0])]
    del envelope["verifiedClaims"][0]["assessment"]  # key absent, not null
    assert is_analysis_response(envelope) is False
