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

// --- mirror of screening.py (Tier 2, owned by WS4) ------------------------- //

/**
 * What WS3's background worker sends to `POST /tier2/screen`.
 *
 * `images` is the wire name the content script already uses; the backend accepts
 * both `images` and its own `image_urls` spelling. Mirrored here so the two names
 * cannot silently diverge again — an unaliased mismatch is invisible, because
 * Pydantic drops unknown keys and every page would then screen image-clean.
 *
 * `text` is optional on purpose: Readability fails on some pages, and Tier 2 can
 * still screen the images when it does.
 */
export interface ScreeningRequest {
  url: string;
  title?: string;
  text?: string;
  images?: string[];
  published_at?: string;
  source_domain?: string;
}

export interface ImageScreeningResult {
  image_url: string;
  is_synthetic_score: number;
  flagged: boolean;
  reason?: string | null;
}

/**
 * Tier 2 is a ROUTER, not a verdict. Nothing here is rendered to the reader —
 * WS3 routes on `escalate_to_tier3` and the user only ever sees Tier 3 output.
 */
export interface ScreeningResult {
  url: string;
  text_score: number;
  text_flagged: boolean;
  image_results: ImageScreeningResult[];
  max_image_score: number;
  image_flagged: boolean;
  escalate_to_tier3: boolean;
  reasons: string[];
  /** A component fell back or timed out; treat the decision as lower-confidence. */
  degraded: boolean;
  /** False when there was no usable body text and the text model never ran. */
  text_scored: boolean;
  images_timed_out: boolean;
  latency_ms: number;
  within_latency_budget: boolean;
  model_meta: Record<string, unknown>;
}

// --- WS2-owned client envelope (proposed to WS3) --------------------------- //

export type AnalysisStatus = "complete" | "processing" | "failed" | "skipped";
export type ArticleVerdictLevel = "trusted" | "ok" | "caution" | "high_risk";

export interface ArticleVerdict {
  level: ArticleVerdictLevel;
  summary: string;
  /** `null` when unset — the backend serialises nulls rather than omitting keys,
   * because `VerifiedClaim.assessment` must stay an explicit `null` for the guard. */
  confidence?: number | null;
}

export interface AnalysisError {
  code: string;
  message: string;
}

/** Why the tier cascade did what it did. Optional and advisory — useful for
 * demoing that most pages stop at Tier 2, which is the whole point of the
 * cascade and is otherwise invisible. Never required by `isAnalysisResponse`. */
export interface Tier2Summary {
  escalated: boolean;
  textScore: number;
  maxImageScore: number;
  reasons: string[];
  degraded: boolean;
  latencyMs: number;
}

export interface AnalysisResponse {
  schemaVersion: "1.0";
  url: string;
  /** `skipped` == Tier 0 trusted source; render the "trusted" pill, no claims.
   * NOTE: there is deliberately no status meaning "Tier 2 screened this and
   * stopped". A non-escalated page returns `complete` with
   * `articleVerdict.level: "ok"` and no claims — see backend/app/pipeline/analyze.py.
   * WS2/WS3 should ratify that or add a status. */
  status: AnalysisStatus;
  articleVerdict: ArticleVerdict;
  verifiedClaims: VerifiedClaim[];
  errors?: AnalysisError[] | null;
  tier2?: Tier2Summary | null;
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

function isImageScreeningResult(v: unknown): v is ImageScreeningResult {
  return (
    isRecord(v) &&
    typeof v.image_url === "string" &&
    typeof v.is_synthetic_score === "number" &&
    typeof v.flagged === "boolean"
  );
}

/**
 * Structural check for a Tier 2 response. WS3 routes on `escalate_to_tier3`, so a
 * malformed screen must fail loudly rather than be read as "nothing to see here" —
 * a missing boolean coerces to false, which would silently drop the article.
 */
export function isScreeningResult(v: unknown): v is ScreeningResult {
  return (
    isRecord(v) &&
    typeof v.url === "string" &&
    typeof v.text_score === "number" &&
    typeof v.text_flagged === "boolean" &&
    typeof v.image_flagged === "boolean" &&
    typeof v.escalate_to_tier3 === "boolean" &&
    typeof v.text_scored === "boolean" &&
    typeof v.degraded === "boolean" &&
    Array.isArray(v.reasons) &&
    v.reasons.every((r) => typeof r === "string") &&
    Array.isArray(v.image_results) &&
    v.image_results.every(isImageScreeningResult)
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
