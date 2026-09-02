"""WS6: turn WS5's claims + retrieved evidence into per-claim verdicts.

Provider-agnostic. The assessor client proposes a status and points at which of the
claim's OWN evidence items justify it; this module binds those indices to real
`Citation`s and enforces the contract's rules. That split is deliberate:

  * a provider cannot invent a source — it can only reference evidence WS5 retrieved;
  * proposal §2.5 ("every non-opinion verdict carries >=1 citation, or it is
    needs_review") is enforced HERE, so it holds for the mock, for the future real
    assessor, and for anything else that implements the Protocol.

WS6 never mutates a Claim. It returns `Assessment`s keyed by `claim.id`, which
services/envelope.py joins into `VerifiedClaim`s.
"""
from __future__ import annotations

from app.clients.base import AssessorClient
from app.clients.factory import make_assessor_client
from app.config import Settings
from app.models.contract import (
    Assessment,
    AssessmentStatus,
    Citation,
    Claim,
    ClaimType,
    Evidence,
)

# Statuses that make an affirmative judgement, and so must be backed by a citation.
CITATION_REQUIRED_STATUSES = frozenset(
    {
        AssessmentStatus.SUPPORTED,
        AssessmentStatus.PARTIALLY_SUPPORTED,
        AssessmentStatus.CONTRADICTED,
    }
)

OPINION_EXPLANATION = (
    "This is a value judgement or a prediction, not a checkable factual claim, "
    "so it was not verified."
)
NO_EVIDENCE_EXPLANATION = "No sources were retrieved for this claim, so it could not be verified."
DOWNGRADED_EXPLANATION = (
    "The assessor reached a verdict but cited no usable source, so it is reported as "
    "unverified rather than asserted without evidence."
)

_STATUS_WORDING = {
    AssessmentStatus.SUPPORTED: "Retrieved sources support this claim.",
    AssessmentStatus.PARTIALLY_SUPPORTED: "Retrieved sources are mixed: some support this claim and some dispute it.",
    AssessmentStatus.CONTRADICTED: "Retrieved sources dispute this claim.",
    AssessmentStatus.NEEDS_REVIEW: "The retrieved sources were not conclusive either way.",
    AssessmentStatus.OPINION: OPINION_EXPLANATION,
}


def _citations_for(evidence: list[Evidence], indices: list[int]) -> list[Citation]:
    """Bind evidence indices to Citations, copying from the claim's own evidence.

    Out-of-range indices are dropped rather than raising: a bad index from a provider
    should cost that citation, not the whole request. Dropping them all is exactly what
    the §2.5 downgrade below is for.
    """
    citations: list[Citation] = []
    seen: set[int] = set()

    for i in indices:
        if i < 0 or i >= len(evidence) or i in seen:
            continue
        seen.add(i)
        item = evidence[i]
        citations.append(
            Citation(
                snippet=item.snippet,
                source_url=item.source_url,
                source_title=item.source_title,
            )
        )
    return citations


def _opinion_assessment(claim: Claim) -> Assessment:
    """Non-factual claims are not sent to the assessor at all."""
    return Assessment(
        claim_id=claim.id,
        status=AssessmentStatus.OPINION,
        explanation=OPINION_EXPLANATION,
        citations=[],
    )


def assess_claim(claim: Claim, *, client: AssessorClient) -> Assessment:
    """Assess one claim. Always returns a contract-valid Assessment."""
    # Forward-compatible: WS5 only surfaces factual claims today, but if opinions or
    # predictions ever reach WS6 they get the "Opinion" treatment, never a verdict.
    if claim.claim_type != ClaimType.FACTUAL:
        return _opinion_assessment(claim)

    if not claim.evidence:
        return Assessment(
            claim_id=claim.id,
            status=AssessmentStatus.NEEDS_REVIEW,
            explanation=NO_EVIDENCE_EXPLANATION,
            citations=[],
        )

    raw = client.assess(claim.text, claim.evidence)
    status = AssessmentStatus(raw.status)
    citations = _citations_for(claim.evidence, raw.evidence_indices)
    explanation = raw.explanation or _STATUS_WORDING[status]
    confidence = raw.confidence

    # --- proposal §2.5, enforced for every provider ------------------------- #
    if status in CITATION_REQUIRED_STATUSES and not citations:
        status = AssessmentStatus.NEEDS_REVIEW
        explanation = DOWNGRADED_EXPLANATION
        confidence = None

    return Assessment(
        claim_id=claim.id,
        status=status,
        explanation=explanation,
        confidence=confidence,
        citations=citations,
    )


def assess_claims(
    claims: list[Claim],
    *,
    client: AssessorClient | None = None,
    settings: Settings | None = None,
) -> dict[str, Assessment]:
    """Assess every claim, keyed by `claim.id` for the envelope to join on."""
    client = client or make_assessor_client(settings)
    return {claim.id: assess_claim(claim, client=client) for claim in claims}
