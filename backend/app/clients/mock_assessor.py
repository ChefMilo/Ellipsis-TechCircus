"""Deterministic mock assessor.

!! THIS IS A RENDERING FIXTURE, NOT REAL ASSESSMENT. !!

It does not reason about whether a claim is true. It reads the stance prefix that
`mock_search.MockSearchClient` stamps onto its own synthetic snippets ("Records indicate
that" / "Reporting corroborates that" -> supporting; "One analysis disputes that" ->
contradicting) and counts them. That is pattern-matching on WS5's mock strings, and it
exists for exactly one reason: to drive every branch of WS2's UI — all five status
colours, badges, citations and the article-level pill — end to end with no API key.

Do NOT quote its verdicts as results in the pitch. Against real Tavily snippets it will
find no stance prefix at all and return NEEDS_REVIEW for everything, which is the honest
answer for a fixture asked to do a job it cannot do. The real assessor is a drop-in
behind `AssessorClient`.
"""
from __future__ import annotations

from app.clients.base import AssessedClaim, AssessorClient
from app.models.contract import AssessmentStatus, Evidence

# Stamped by mock_search.MockSearchClient. Kept in sync by the tests, which assert the
# mock pipeline still produces recognisable stances end to end.
SUPPORTING_PREFIXES = ("Records indicate that", "Reporting corroborates that")
CONTRADICTING_PREFIXES = ("One analysis disputes that",)


def _stance_of(snippet: str) -> str | None:
    """"support" / "contradict" / None when no fixture prefix is recognised."""
    text = snippet.lstrip()
    if text.startswith(SUPPORTING_PREFIXES):
        return "support"
    if text.startswith(CONTRADICTING_PREFIXES):
        return "contradict"
    return None


class MockAssessorClient(AssessorClient):
    name = "mock-assessor-stance-fixture-v1"

    def assess(self, claim_text: str, evidence: list[Evidence]) -> AssessedClaim:
        supporting: list[int] = []
        contradicting: list[int] = []

        for i, item in enumerate(evidence):
            stance = _stance_of(item.snippet)
            if stance == "support":
                supporting.append(i)
            elif stance == "contradict":
                contradicting.append(i)

        # Majority wins; an even split is genuinely mixed. No recognisable stance at all
        # (i.e. real snippets) means this fixture has nothing to say.
        if not supporting and not contradicting:
            return AssessedClaim(
                status=AssessmentStatus.NEEDS_REVIEW,
                evidence_indices=[],
                explanation="No stance could be determined from the retrieved sources.",
            )

        total = len(supporting) + len(contradicting)
        if len(supporting) > len(contradicting):
            status, cited = AssessmentStatus.SUPPORTED, supporting
            confidence = len(supporting) / total
        elif len(contradicting) > len(supporting):
            status, cited = AssessmentStatus.CONTRADICTED, contradicting
            confidence = len(contradicting) / total
        else:
            status = AssessmentStatus.PARTIALLY_SUPPORTED
            cited = sorted(supporting + contradicting)
            confidence = 0.5

        return AssessedClaim(
            status=status,
            evidence_indices=cited,
            confidence=round(confidence, 2),
            explanation=None,  # the service writes the plain-language wording
        )
