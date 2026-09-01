/**
 * TS mirror of the frozen Tier 3 contract.
 *
 * SOURCE OF TRUTH: backend/app/models/contract.py (Pydantic v2, owned by WS5).
 * Keep these shapes in sync by hand — there is no codegen. Only the fields WS2
 * actually renders are mirrored here; the backend model has more (stats,
 * model_meta, search_query provenance, …) that the in-page UI does not need.
 *
 * `AnalysisResponse` at the bottom is WS2-owned: the thin client envelope that
 * wraps `VerifiedClaim[]` for the summary pill. WS3 will either ratify it or
 * hand us theirs; it must wrap `VerifiedClaim` unchanged.
 */

// --- mirror of contract.py -------------------------------------------------- //

export type ClaimType = "factual" | "opinion" | "prediction";

export type AssessmentStatus =
  | "supported"
  | "partially_supported"
  | "contradicted"
  | "needs_review"
  | "opinion";

export interface Evidence {
  snippet: string;
  source_url: string;
  source_title?: string | null;
  source_domain?: string | null;
  relevance_score?: number | null;
}

export interface Claim {
  id: string;
  /** A self-contained sentence. This is the ONLY handle WS2 has for re-anchoring
   * the claim onto the live DOM — see src/content/anchor/. */
  text: string;
  claim_type: ClaimType;
  checkworthiness: number;
  rank: number;
  search_query?: string | null;
  evidence: Evidence[];
  /** OPTIONAL anchoring hints. Not in the contract yet — requested from WS5.
   * `char_start`/`char_end` are offsets of `text` within the cleaned article
   * body; `prefix`/`suffix` are short context windows. The matcher uses them
   * when present and works without them. */
  char_start?: number | null;
  char_end?: number | null;
  prefix?: string | null;
  suffix?: string | null;
}

export interface Citation {
  snippet: string;
  source_url: string;
  source_title?: string | null;
}

export interface Assessment {
  claim_id: string;
  status: AssessmentStatus;
  explanation: string;
  confidence?: number | null;
  citations: Citation[];
}

export interface VerifiedClaim {
  claim: Claim;
  assessment: Assessment | null;
}

// --- WS2-owned client envelope (proposed to WS3) --------------------------- //

export type AnalysisStatus = "complete" | "processing" | "failed" | "skipped";
export type ArticleVerdictLevel = "trusted" | "ok" | "caution" | "high_risk";

export interface ArticleVerdict {
  level: ArticleVerdictLevel;
  summary: string;
  confidence?: number;
}

export interface AnalysisError {
  code: string;
  message: string;
}

export interface AnalysisResponse {
  schemaVersion: "1.0";
  url: string;
  /** `skipped` == Tier 0 trusted source; render the "trusted" pill, no claims. */
  status: AnalysisStatus;
  articleVerdict: ArticleVerdict;
  verifiedClaims: VerifiedClaim[];
  errors?: AnalysisError[];
}

// --- runtime guards -------------------------------------------------------- //

const CLAIM_TYPES = new Set<ClaimType>(["factual", "opinion", "prediction"]);
const ASSESSMENT_STATUSES = new Set<AssessmentStatus>([
  "supported",
  "partially_supported",
  "contradicted",
  "needs_review",
  "opinion",
]);
const ANALYSIS_STATUSES = new Set<AnalysisStatus>([
  "complete",
  "processing",
  "failed",
  "skipped",
]);
const VERDICT_LEVELS = new Set<ArticleVerdictLevel>([
  "trusted",
  "ok",
  "caution",
  "high_risk",
]);

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function isEvidence(v: unknown): v is Evidence {
  return isRecord(v) && typeof v.snippet === "string" && typeof v.source_url === "string";
}

function isCitation(v: unknown): v is Citation {
  return isRecord(v) && typeof v.snippet === "string" && typeof v.source_url === "string";
}

function isClaim(v: unknown): v is Claim {
  return (
    isRecord(v) &&
    typeof v.id === "string" &&
    typeof v.text === "string" &&
    typeof v.claim_type === "string" &&
    CLAIM_TYPES.has(v.claim_type as ClaimType) &&
    typeof v.rank === "number" &&
    Array.isArray(v.evidence) &&
    v.evidence.every(isEvidence)
  );
}

function isAssessment(v: unknown): v is Assessment {
  return (
    isRecord(v) &&
    typeof v.claim_id === "string" &&
    typeof v.status === "string" &&
    ASSESSMENT_STATUSES.has(v.status as AssessmentStatus) &&
    typeof v.explanation === "string" &&
    Array.isArray(v.citations) &&
    v.citations.every(isCitation)
  );
}

function isVerifiedClaim(v: unknown): v is VerifiedClaim {
  return (
    isRecord(v) &&
    isClaim(v.claim) &&
    (v.assessment === null || isAssessment(v.assessment))
  );
}

/**
 * Structural check that untrusted backend output matches `AnalysisResponse`.
 * Malformed responses must never reach the renderer (proposal §2.5).
 */
export function isAnalysisResponse(v: unknown): v is AnalysisResponse {
  if (!isRecord(v)) return false;
  if (v.schemaVersion !== "1.0") return false;
  if (typeof v.url !== "string") return false;
  if (typeof v.status !== "string" || !ANALYSIS_STATUSES.has(v.status as AnalysisStatus)) {
    return false;
  }
  if (!isRecord(v.articleVerdict)) return false;
  if (
    typeof v.articleVerdict.level !== "string" ||
    !VERDICT_LEVELS.has(v.articleVerdict.level as ArticleVerdictLevel)
  ) {
    return false;
  }
  if (typeof v.articleVerdict.summary !== "string") return false;
  if (!Array.isArray(v.verifiedClaims) || !v.verifiedClaims.every(isVerifiedClaim)) {
    return false;
  }
  return true;
}
