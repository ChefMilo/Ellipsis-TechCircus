"""Real WS6 assessor over an OpenAI-COMPATIBLE endpoint. OPTIONAL — the mock is the default.

Only imported when DASFAX_ASSESSOR_PROVIDER=openai. Same endpoint/model knobs as the
extractor (see openai_compat), so OpenAI, Gemini and Groq are a base-URL swap.

What this client is allowed to do is deliberately narrow. It sees ONE claim and the
NUMBERED evidence WS5 actually retrieved for it, and returns a status plus the INDICES of
the snippets that justify it. It never returns a URL, so it cannot invent a source:
services/assessment.py binds those indices to the claim's own evidence and enforces
proposal §2.5 (an affirmative verdict with no usable citation is downgraded to
needs_review). That invariant stays in the service and is NOT re-implemented here.

Everything that can go wrong on the wire — an API error, prose instead of JSON, a status
the contract doesn't know, indices that aren't integers — degrades to a needs_review
result. A confused model must not be able to take the request down with it.
"""
from __future__ import annotations

from typing import Any

from app.clients.base import AssessedClaim, AssessorClient
from app.clients.openai_compat import build_chat_client, chat_json, loads_object
from app.config import Settings
from app.models.contract import AssessmentStatus, Evidence

# The assessor may only return statuses that represent a judgement about evidence.
# OPINION is the service's call (non-factual claims never reach an assessor).
_ALLOWED_STATUSES = {
    AssessmentStatus.SUPPORTED.value,
    AssessmentStatus.PARTIALLY_SUPPORTED.value,
    AssessmentStatus.CONTRADICTED.value,
    AssessmentStatus.NEEDS_REVIEW.value,
}

UNPARSEABLE_EXPLANATION = (
    "The assessor did not return a usable verdict for this claim, so it is reported as "
    "unverified."
)

_SYSTEM = (
    "You are a careful fact-checking assistant. You judge ONE claim against a numbered "
    "list of evidence snippets and nothing else. "
    "Rules you must not break:\n"
    "1. Use ONLY the numbered evidence provided. Do not use your own background knowledge "
    "and do not mention any source that is not in the list.\n"
    "2. Cite evidence ONLY by its index number. Never output a URL, a publication name or "
    "an index that is not in the list.\n"
    "3. Cite only the snippets that actually justify your status. If none of them do, the "
    "status is needs_review with an empty list.\n"
    "4. 'supported' = the evidence states the claim; 'partially_supported' = it supports "
    "part of the claim, or the sources disagree; 'contradicted' = the evidence states the "
    "opposite; 'needs_review' = the evidence is off-topic or inconclusive.\n"
    "Return STRICT JSON only, with no commentary and no code fences."
)

_USER_TEMPLATE = (
    'Claim:\n"""\n{claim}\n"""\n\n'
    "Evidence:\n{evidence}\n\n"
    "Return JSON of exactly this form:\n"
    '{{"status": "supported|partially_supported|contradicted|needs_review", '
    '"evidence_indices": [<indices of the snippets above that justify the status>], '
    '"confidence": <0.0-1.0>, '
    '"explanation": "<one sentence, grounded in the snippets you cited>"}}'
)


def _format_evidence(evidence: list[Evidence]) -> str:
    lines: list[str] = []
    for i, item in enumerate(evidence):
        title = item.source_title or item.source_domain or "untitled source"
        lines.append(f"[{i}] {title}: {item.snippet}")
    return "\n".join(lines)


def _unparseable() -> AssessedClaim:
    """The safe result: the service renders this as needs_review with no citations."""
    return AssessedClaim(
        status=AssessmentStatus.NEEDS_REVIEW,
        evidence_indices=[],
        confidence=None,
        explanation=UNPARSEABLE_EXPLANATION,
    )


def _coerce_indices(value: Any) -> list[int]:
    """Keep the integer indices, drop everything else. Out-of-range values are the
    service's problem (it drops them, then §2.5 downgrades if nothing survives)."""
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for v in value:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out.append(v)
        elif isinstance(v, str) and v.strip().lstrip("-").isdigit():
            out.append(int(v.strip()))
    return out


def _coerce_confidence(value: Any) -> float | None:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


class OpenAIAssessorClient(AssessorClient):
    name = "openai-assessor"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        # `client` is an injection seam for tests: pass a stub and no key, SDK or network
        # is needed. Production always goes through build_chat_client.
        self._client = client if client is not None else build_chat_client(
            settings, env_var="DASFAX_ASSESSOR_PROVIDER"
        )
        self._model = settings.openai_model
        self.name = f"openai-assessor:{settings.openai_model}"

    def assess(self, claim_text: str, evidence: list[Evidence]) -> AssessedClaim:
        if not evidence:
            # The service already short-circuits this, but a Protocol impl should not
            # depend on its caller's ordering.
            return _unparseable()

        try:
            raw = chat_json(
                self._client,
                model=self._model,
                system=_SYSTEM,
                user=_USER_TEMPLATE.format(claim=claim_text, evidence=_format_evidence(evidence)),
            )
        except Exception:
            # A rate limit or a dropped connection on claim 3 of 5 must cost that claim a
            # verdict, not the whole article.
            return _unparseable()

        data = loads_object(raw)
        status = str(data.get("status", "")).strip().lower()
        if status not in _ALLOWED_STATUSES:
            return _unparseable()

        explanation = data.get("explanation")
        explanation = explanation.strip() if isinstance(explanation, str) and explanation.strip() else None

        return AssessedClaim(
            status=status,
            evidence_indices=_coerce_indices(data.get("evidence_indices")),
            confidence=_coerce_confidence(data.get("confidence")),
            explanation=explanation,
        )
