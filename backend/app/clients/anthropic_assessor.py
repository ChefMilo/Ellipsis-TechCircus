"""Native Anthropic assessor (WS6 "verify"). OPTIONAL — the mock is the default.

Only imported when DASFAX_ASSESSOR_PROVIDER=anthropic. Needs ANTHROPIC_API_KEY and nothing
else. Structurally a mirror of openai_assessor.py, using a forced `tool_use` for strict
output instead of OpenAI's JSON mode.

What this client is allowed to do is deliberately narrow. It sees ONE claim and the
NUMBERED evidence WS5 actually retrieved for it, and returns a status plus the INDICES of
the snippets that justify it. It never returns a URL, so it cannot invent a source:
services/assessment.py binds those indices to the claim's own evidence and enforces
proposal §2.5 (an affirmative verdict with no usable citation is downgraded to
needs_review). That invariant stays in the service and is NOT re-implemented here.

Everything that can go wrong — an API error, no tool call, a status the contract does not
know, indices that are not integers — degrades to a needs_review result the service
renders honestly. A confused model must not be able to take the request down with it.
"""
from __future__ import annotations

from typing import Any

from app.clients.anthropic_compat import build_anthropic_client, json_via_tool
from app.clients.base import AssessedClaim, AssessorClient
from app.config import Settings
from app.models.contract import AssessmentStatus, Evidence

_MAX_TOKENS = 1024

# The assessor may only return statuses that represent a judgement about evidence.
# OPINION is the service's call (non-factual claims never reach an assessor).
_ALLOWED_STATUSES = [
    AssessmentStatus.SUPPORTED.value,
    AssessmentStatus.PARTIALLY_SUPPORTED.value,
    AssessmentStatus.CONTRADICTED.value,
    AssessmentStatus.NEEDS_REVIEW.value,
]

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
    "Report your verdict by calling the record_verdict tool."
)

_USER_TEMPLATE = 'Claim:\n"""\n{claim}\n"""\n\nEvidence:\n{evidence}'

_ASSESS_TOOL: dict[str, Any] = {
    "name": "record_verdict",
    "description": "Record the verdict for this claim against the numbered evidence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": _ALLOWED_STATUSES},
            "evidence_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Indices of the numbered snippets that justify the status.",
            },
            "confidence": {"type": "number", "description": "0.0-1.0."},
            "explanation": {
                "type": "string",
                "description": "One sentence, grounded in the snippets you cited.",
            },
        },
        "required": ["status", "evidence_indices", "explanation"],
    },
}


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


class AnthropicAssessorClient(AssessorClient):
    name = "anthropic-assessor"

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        # `client` is an injection seam for tests: pass a stub and no key, SDK or network
        # is needed. Production always goes through build_anthropic_client.
        self._client = client if client is not None else build_anthropic_client(
            settings, env_var="DASFAX_ASSESSOR_PROVIDER"
        )
        self._model = settings.anthropic_model
        self.name = f"anthropic-assessor:{settings.anthropic_model}"

    def assess(self, claim_text: str, evidence: list[Evidence]) -> AssessedClaim:
        if not evidence:
            # The service already short-circuits this, but a Protocol impl should not
            # depend on its caller's ordering.
            return _unparseable()

        try:
            data = json_via_tool(
                self._client,
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM,
                user=_USER_TEMPLATE.format(
                    claim=claim_text, evidence=_format_evidence(evidence)
                ),
                tool=_ASSESS_TOOL,
            )
        except Exception:
            # A rate limit or a dropped connection on claim 3 of 5 must cost that claim a
            # verdict, not the whole article.
            return _unparseable()

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
