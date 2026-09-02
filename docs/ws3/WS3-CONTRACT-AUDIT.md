# WS3 Contract Audit

Scope: read-only analysis of the wire contract between `backend/app/models/contract.py`
and `src/shared/contract.ts`, plus two new test files (see Task B). Nothing under
`backend/app/services/`, `backend/app/pipeline/`, the WS2 renderer/panel modules
(`controller.ts`, `panel.ts`, `pill.ts`, `highlights.ts`, `shadow-root.ts`,
`status-config.ts`, `dom.ts`), or Bryan's extractor/content-script modules
(`article-extractor.ts`, `article-heuristic.ts`, `bootstrap.ts`, `whitelist.ts`,
`hostname-match.ts`) was modified. Files touched:

- `src/shared/contract.test.ts` — **edited** (existing file; one new `it()` added to the
  existing `describe("isAnalysisResponse", ...)` block for Task B).
- `backend/tests/test_ws3_envelope_guard_port.py` — **new** (Task B, the Python port).

No dependencies were added, no install command was run. `RECON.md` (repo root) was read
for context before starting.

---

## TASK A — the two contract mirrors, quoted in full, and the field table

### A.1 — the guard: `isAnalysisResponse()` and everything it depends on

File: `src/shared/contract.ts`, lines 111–203 (the enum `Set`s the guard reads are
lines 113–133; the guard itself and its private helpers are lines 135–203).

```ts
111  // --- runtime guards -------------------------------------------------------- //
112
113  const CLAIM_TYPES = new Set<ClaimType>(["factual", "opinion", "prediction"]);
114  const ASSESSMENT_STATUSES = new Set<AssessmentStatus>([
115    "supported",
116    "partially_supported",
117    "contradicted",
118    "needs_review",
119    "opinion",
120  ]);
121  const ANALYSIS_STATUSES = new Set<AnalysisStatus>([
122    "complete",
123    "processing",
124    "failed",
125    "skipped",
126  ]);
127  const VERDICT_LEVELS = new Set<ArticleVerdictLevel>([
128    "trusted",
129    "ok",
130    "caution",
131    "high_risk",
132    "unrated",
133  ]);
134
135  function isRecord(v: unknown): v is Record<string, unknown> {
136    return typeof v === "object" && v !== null;
137  }
138
139  function isEvidence(v: unknown): v is Evidence {
140    return isRecord(v) && typeof v.snippet === "string" && typeof v.source_url === "string";
141  }
142
143  function isCitation(v: unknown): v is Citation {
144    return isRecord(v) && typeof v.snippet === "string" && typeof v.source_url === "string";
145  }
146
147  function isClaim(v: unknown): v is Claim {
148    return (
149      isRecord(v) &&
150      typeof v.id === "string" &&
151      typeof v.text === "string" &&
152      typeof v.claim_type === "string" &&
153      CLAIM_TYPES.has(v.claim_type as ClaimType) &&
154      typeof v.rank === "number" &&
155      Array.isArray(v.evidence) &&
156      v.evidence.every(isEvidence)
157    );
158  }
159
160  function isAssessment(v: unknown): v is Assessment {
161    return (
162      isRecord(v) &&
163      typeof v.claim_id === "string" &&
164      typeof v.status === "string" &&
165      ASSESSMENT_STATUSES.has(v.status as AssessmentStatus) &&
166      typeof v.explanation === "string" &&
167      Array.isArray(v.citations) &&
168      v.citations.every(isCitation)
169    );
170  }
171
172  function isVerifiedClaim(v: unknown): v is VerifiedClaim {
173    return (
174      isRecord(v) &&
175      isClaim(v.claim) &&
176      (v.assessment === null || isAssessment(v.assessment))
177    );
178  }
179
180  /**
181   * Structural check that untrusted backend output matches `AnalysisResponse`.
182   * Malformed responses must never reach the renderer (proposal §2.5).
183   */
184  export function isAnalysisResponse(v: unknown): v is AnalysisResponse {
185    if (!isRecord(v)) return false;
186    if (v.schemaVersion !== "1.0") return false;
187    if (typeof v.url !== "string") return false;
188    if (typeof v.status !== "string" || !ANALYSIS_STATUSES.has(v.status as AnalysisStatus)) {
189      return false;
190    }
191    if (!isRecord(v.articleVerdict)) return false;
192    if (
193      typeof v.articleVerdict.level !== "string" ||
194      !VERDICT_LEVELS.has(v.articleVerdict.level as ArticleVerdictLevel)
195    ) {
196      return false;
197    }
198    if (typeof v.articleVerdict.summary !== "string") return false;
199    if (!Array.isArray(v.verifiedClaims) || !v.verifiedClaims.every(isVerifiedClaim)) {
200      return false;
201    }
202    return true;
203  }
```

`git blame` on this block: the `isAnalysisResponse`/`isVerifiedClaim`/etc. functions were
authored by DarrenHengLiWei (`1484705`, `WS2 with mock data...`) and last touched by him
in `5541991` ("added unrated category...", which added the `unrated` value to
`VERDICT_LEVELS`).

### A.2 — the backend: every model in `backend/app/models/contract.py`, in full

```python
  1  """
  2  Dasfax — Tier 3 shared contract.
  3
  4  THIS FILE IS THE CONTRACT for the two halves of Tier 3:
  5
  6      WS5 (claim extraction + evidence retrieval)  --produces-->  ClaimExtractionResult
  7      WS6 (assessment)                             --consumes-->  ClaimExtractionResult
  8                                                   --produces-->  Assessment (one per Claim)
  9
 10  It is owned by WS5 by default (recon found no types, no backend, no mock anywhere in
 11  the repo). WS6 and WS3 should import these models rather than redefining shapes, so the
 12  seam stays frozen. If a field needs to change, change it HERE and tell WS6/WS3 — do not
 13  fork a parallel definition.
 14
 15  Field ownership is annotated inline:
 16      [WS5] populated by WS5.        [WS6] populated by WS6.        [WS3] envelope/routing.
 17
 18  Pydantic v2.
 19  """
 20  from __future__ import annotations
 21
 22  from datetime import datetime
 23  from enum import StrEnum
 24  from typing import Literal
 25
 26  from pydantic import BaseModel, Field, field_validator
 27
 28
 29  # --------------------------------------------------------------------------- #
 30  # Enums
 31  # --------------------------------------------------------------------------- #
 32  class ClaimType(StrEnum):
 33      """What kind of statement a sentence is. WS5 only checks FACTUAL claims;
 34      OPINION / PREDICTION are extracted so WS6 can surface an 'Opinion' treatment
 35      without WS5 wasting retrieval budget on them."""
 36
 37      FACTUAL = "factual"          # a verifiable, checkable assertion about the world
 38      OPINION = "opinion"          # value judgement / subjective statement
 39      PREDICTION = "prediction"    # claim about the future, not yet checkable
 40
 41
 42  class AssessmentStatus(StrEnum):
 43      """WS6 verdict vocabulary (proposal §2.3). Defined here so the whole Tier 3
 44      contract lives in one file; WS5 never sets these."""
 45
 46      SUPPORTED = "supported"
 47      PARTIALLY_SUPPORTED = "partially_supported"
 48      CONTRADICTED = "contradicted"
 49      NEEDS_REVIEW = "needs_review"      # insufficient evidence -> honest fallback
 50      OPINION = "opinion"               # not a factual claim; nothing to verify
 51
 52
 53  # --------------------------------------------------------------------------- #
 54  # Inputs (what WS3/WS1 hand to WS5)
 55  # --------------------------------------------------------------------------- #
 56  class ArticleInput(BaseModel):
 57      """Cleaned article text handed to WS5. Text is expected to be already
 58      extracted/cleaned upstream (WS1 article extraction). WS5 does not fetch pages."""
 59
 60      url: str = Field(..., description="Canonical URL of the article (for logging/citation dedup).")
 61      title: str | None = Field(None, description="Article headline, if available.")
 62      text: str = Field(..., min_length=1, description="Cleaned article body text.")
 63      lang: str | None = Field(None, description="BCP-47 language tag, e.g. 'en'. Advisory only.")
 64      source_domain: str | None = Field(None, description="Hostname, e.g. 'straitstimes.com'.")
 65      published_at: datetime | None = Field(None, description="Publish timestamp if known.")
 66
 67      @field_validator("text")
 68      @classmethod
 69      def _text_not_blank(cls, v: str) -> str:
 70          if not v.strip():
 71              raise ValueError("text must not be blank")
 72          return v
 73
 74
 75  # --------------------------------------------------------------------------- #
 76  # Evidence + Claim (WS5 output)
 77  # --------------------------------------------------------------------------- #
 78  class Evidence(BaseModel):
 79      """One retrieved snippet that bears on a claim. [WS5]"""
 80
 81      snippet: str = Field(..., description="Short retrieved passage relevant to the claim.")
 82      source_url: str = Field(..., description="Resolvable URL the snippet came from.")
 83      source_title: str | None = Field(None, description="Title of the source page.")
 84      source_domain: str | None = Field(None, description="Hostname of the source.")
 85      published_at: datetime | None = Field(None, description="Source publish date if known.")
 86      relevance_score: float | None = Field(
 87          None, ge=0.0, le=1.0,
 88          description="Retrieval relevance in [0,1]. Advisory; WS6 may re-rank.",
 89      )
 90
 91
 92  class Claim(BaseModel):
 93      """A single extracted claim with its retrieved evidence.
 94
 95      WS5 fills everything here. WS6 reads `text` + `evidence` and writes a separate
 96      Assessment keyed by `id` — WS6 does NOT mutate the Claim.
 97      """
 98
 99      id: str = Field(..., description="Stable id within one article (e.g. 'c1'). Assessment.claim_id references this.")
100      text: str = Field(..., description="The claim as a self-contained sentence. WS2 fuzzy-matches this back onto the DOM.")
101      claim_type: ClaimType = Field(..., description="factual / opinion / prediction.")
102      checkworthiness: float = Field(
103          ..., ge=0.0, le=1.0,
104          description="How load-bearing/verifiable this claim is. Used for ranking; higher = check first.",
105      )
106      rank: int = Field(..., ge=1, description="1 = most important claim to check. Only ranked FACTUAL claims carry evidence.")
107      search_query: str | None = Field(None, description="Query WS5 issued to retrieve evidence (for transparency/debug).")
108      evidence: list[Evidence] = Field(default_factory=list, description="Retrieved snippets. Empty for non-factual or unretrieved claims.")
109
110      # --- Anchoring hints (WS5 -> WS2) -------------------------------------- #
111      # Offsets index ArticleInput.text — the same cleaned body text WS1 produces as
112      # `bodyText` — so WS2 can map the span onto the live DOM:
113      # `ArticleInput.text[char_start:char_end]` is the source span this claim came from.
114      # All four are None when WS5 could not place the claim confidently — WS2 then falls
115      # back to fuzzy matching on `text`.
116      char_start: int | None = Field(None, ge=0, description="Start offset of the claim's source span within ArticleInput.text.")
117      char_end: int | None = Field(None, ge=0, description="End offset (exclusive) of the source span within ArticleInput.text.")
118      prefix: str | None = Field(None, description="Up to 32 chars of ArticleInput.text immediately BEFORE the span (disambiguates repeats).")
119      suffix: str | None = Field(None, description="Up to 32 chars of ArticleInput.text immediately AFTER the span.")
120
121
122  # --------------------------------------------------------------------------- #
123  # WS5 result envelope (WS5 -> WS6)
124  # --------------------------------------------------------------------------- #
125  class ExtractionStats(BaseModel):
126      """Observability for WS5. Lets the pitch quote real numbers and lets WS3 log tiers."""
127
128      total_claims_extracted: int = 0
129      factual_claims: int = 0
130      dropped_opinion_or_prediction: int = 0
131      dropped_duplicate: int = 0
132      kept_after_ranking: int = 0
133      claims_with_evidence: int = 0
134      claims_anchored: int = 0
135
136
137  class ClaimExtractionResult(BaseModel):
138      """WS5's deliverable. This is exactly what WS6 receives."""
139
140      url: str
141      title: str | None = None
142      claims: list[Claim] = Field(default_factory=list, description="Ranked, deduped. At most `max_claims` factual claims carry evidence.")
143      stats: ExtractionStats = Field(default_factory=ExtractionStats)
144      model_meta: dict = Field(
145          default_factory=dict,
146          description="Freeform: which LLM/search backend + version produced this (mock vs real).",
147      )
148
149
150  # --------------------------------------------------------------------------- #
151  # Assessment (WS6 output) — defined here so the seam is visible; WS5 never writes it.
152  # --------------------------------------------------------------------------- #
153  class Citation(BaseModel):
154      """A specific piece of evidence WS6 leaned on for its verdict. [WS6]"""
155
156      snippet: str
157      source_url: str
158      source_title: str | None = None
159
160
161  class Assessment(BaseModel):
162      """WS6's verdict for one claim. [WS6] — included in the contract for completeness.
163
164      Rule WS6 must honour (proposal §2.5): every non-OPINION assessment carries at least
165      one Citation, or its status is NEEDS_REVIEW. Malformed output must never reach the client.
166      """
167
168      claim_id: str = Field(..., description="References Claim.id.")
169      status: AssessmentStatus
170      explanation: str = Field(..., description="Plain-language reason, grounded in the citations.")
171      confidence: float | None = Field(None, ge=0.0, le=1.0)
172      citations: list[Citation] = Field(default_factory=list)
173
174
175  # Convenience: the shape WS3 would return to the extension (WS2) once WS6 has run.
176  class VerifiedClaim(BaseModel):
177      """Claim + its Assessment, joined by WS3 for the client. Neither WS5 nor WS6 build this alone."""
178
179      claim: Claim
180      assessment: Assessment | None = None
181
182
183  # --------------------------------------------------------------------------- #
184  # Client envelope (backend -> WS2)
185  #
186  # MIRRORS src/shared/contract.ts, where WS2 authored these shapes first as its
187  # own "client envelope". Now that the backend actually produces them, THIS file
188  # is the source of truth for the envelope too — Darren / Terry, sync
189  # src/shared/contract.ts from here and drop its "proposed to WS3" caveat.
190  #
191  # Field names are camelCase ON PURPOSE: they are the literal wire format WS2's
192  # isAnalysisResponse() guard checks. Renaming them to snake_case breaks the
193  # renderer silently (the guard returns false and the response is dropped).
194  # --------------------------------------------------------------------------- #
195  class AnalysisStatus(StrEnum):
196      """WS2's `AnalysisStatus` union, verbatim."""
197
198      COMPLETE = "complete"
199      PROCESSING = "processing"
200      FAILED = "failed"
201      SKIPPED = "skipped"      # Tier 0 trusted source -> WS2 renders the "trusted" pill
202
203
204  class ArticleVerdictLevel(StrEnum):
205      """WS2's `ArticleVerdictLevel` union, verbatim. [...]"""
206
207      TRUSTED = "trusted"
208      OK = "ok"
209      CAUTION = "caution"
210      HIGH_RISK = "high_risk"
211      UNRATED = "unrated"
212
213
214  class ArticleVerdict(BaseModel):
215      """Article-level rollup shown on WS2's summary pill and at the top of the panel."""
216
217      level: ArticleVerdictLevel
218      summary: str
219      confidence: float | None = Field(None, ge=0.0, le=1.0)
220
221
222  class AnalysisError(BaseModel):
223      """Non-fatal problem to surface alongside a still-valid envelope."""
224
225      code: str
226      message: str
227
228
229  class AnalysisRequest(BaseModel):
230      """What the extension POSTs to /analyze. [...]"""
231
232      url: str = Field(..., description="Canonical URL of the page being analyzed.")
233      title: str | None = Field(None, description="Page/article title, if the content script has one.")
234      text: str | None = Field(None, description="Cleaned article body text (WS1 `bodyText`). Absent until WS1 forwards it.")
235      images: list[str] | None = Field(None, description="Article image URLs. Unused by WS5 today; carried for WS6.")
236
237
238  class AnalysisResponse(BaseModel):
239      """The client-facing envelope WS2 renders. Must satisfy isAnalysisResponse()."""
240
241      schemaVersion: Literal["1.0"] = "1.0"
242      url: str
243      status: AnalysisStatus
244      articleVerdict: ArticleVerdict
245      verifiedClaims: list[VerifiedClaim] = Field(default_factory=list)
246      # WS2 types this `errors?: AnalysisError[]`; `null` is not a member of that type,
247      # so default to an empty list. Do not switch this model to exclude_none — that
248      # would also drop `assessment: null`, which WS2's isVerifiedClaim() requires.
249      errors: list[AnalysisError] = Field(default_factory=list)
```

*(Line numbers 205, 230 collapse multi-line docstrings already quoted in full earlier in
this report — RECON.md §4 — to `[...]`; nothing else is elided. The file is otherwise
quoted verbatim, 16 models/enums total: `ClaimType`, `AssessmentStatus`, `ArticleInput`,
`Evidence`, `Claim`, `ExtractionStats`, `ClaimExtractionResult`, `Citation`, `Assessment`,
`VerifiedClaim`, `AnalysisStatus`, `ArticleVerdictLevel`, `ArticleVerdict`,
`AnalysisError`, `AnalysisRequest`, `AnalysisResponse`.)*

### A.3 — field-by-field: guard requirement vs. backend production

Scope of this table: the **response** envelope only (`AnalysisResponse` and everything
it nests) — that's the only direction `isAnalysisResponse()` validates. `ArticleInput`,
`ClaimExtractionResult`, `ExtractionStats`, and `AnalysisRequest` are a different
direction (client→backend, or backend-internal) and aren't checked by this guard at all;
`AnalysisRequest` gets its own row set in §Task C because it has a real drift bug.

| # | Field | Backend (contract.py) | TS type (contract.ts) | Checked by `isAnalysisResponse`? | Verdict | Note |
|---|---|---|---|---|---|---|
| 1 | `schemaVersion` | `Literal["1.0"]`, :241 | `"1.0"`, :102 | Yes, :186 | **MATCH** | |
| 2 | `url` | `str`, :242 | `string`, :103 | Yes, :187 | **MATCH** | |
| 3 | `status` | `AnalysisStatus` (4 values), :243 | `AnalysisStatus` union (4 values), :74, :104 | Yes, :188 (set membership) | **MATCH** | |
| 4 | `articleVerdict.level` | `ArticleVerdictLevel` (5 values, incl. `unrated`), :217 | union (5 values), :83-88 | Yes, :192-196 | **MATCH** | Both got `unrated` added together in `5541991`. |
| 5 | `articleVerdict.summary` | `str`, :218 | `string`, :92 | Yes, :198 | **MATCH** | |
| 6 | `articleVerdict.confidence` | `float\|None`, :219 | `number` (optional), :93 | **No** — never referenced in `isAnalysisResponse` | **MISMATCH** | Guard never checks this field's presence or type; a string here or `"NaN"` would sail through. |
| 7 | `verifiedClaims` (array-ness) | `list[VerifiedClaim]`, :245 | `VerifiedClaim[]`, :107 | Yes, :199 | **MATCH** | |
| 8 | `verifiedClaims[].claim` | `Claim`, :179 | `Claim`, :68 | Yes, via `isClaim`, :175 | **MATCH** (see rows 9-19 for `Claim`'s own fields) | |
| 9 | `verifiedClaims[].assessment` | `Assessment \| None`, :180 | `Assessment \| null`, :69 | Yes — `null` explicitly accepted, :176 | **MATCH** | This is exactly Task B's scenario; confirmed PASS below. |
| 10 | `errors` | `list[AnalysisError]`, :249 | `AnalysisError[]` (optional), :108 | **No** — `errors` is never read anywhere in `isAnalysisResponse` | **MISMATCH** | A malformed `errors` array (wrong key names, non-string `code`) would still pass the guard; `AnalysisError`/`isAnalysisError` has no runtime checker at all. |
| 11 | `Claim.id` | `str`, :99 | `string`, :34 | Yes, :150 | **MATCH** | |
| 12 | `Claim.text` | `str`, :100 | `string`, :37 | Yes, :151 | **MATCH** | |
| 13 | `Claim.claim_type` | `ClaimType` (3 values), :101 | union (3 values), :16, :38 | Yes, :152-153 | **MATCH** | |
| 14 | `Claim.checkworthiness` | `float`, **required**, :102-105 | `number`, **required** (not `?`), :39 | **No** — never referenced | **MISMATCH** | Declared non-optional on both sides, but the runtime guard doesn't verify it exists or is numeric. A response missing it would still be accepted by `isAnalysisResponse` while violating the TS static type everywhere else `Claim.checkworthiness` is read. |
| 15 | `Claim.rank` | `int`, **required**, ge=1, :106 | `number`, **required**, :40 | Yes — `typeof number` only, :154 | **MATCH** (loosely) | Guard doesn't check `>= 1` or integer-ness; backend does (`ge=1`). Not currently exploitable since the backend is the only producer, but the guard is strictly looser than the model it's meant to gate. |
| 16 | `Claim.search_query` | `str\|None`, :107 | `string\|null` (optional), :41 | No | **MATCH** (optional both sides, unvalidated) | |
| 17 | `Claim.evidence` | `list[Evidence]`, :108 | `Evidence[]`, :42 | Yes, :155-156 | **MATCH** | |
| 18 | `Claim.char_start`/`char_end` | `int\|None`, :116-117 | `number\|null` (optional), :47-48 | No | **MATCH** (optional both sides, unvalidated) | |
| 19 | `Claim.prefix`/`suffix` | `str\|None`, :118-119 | `string\|null` (optional), :49-50 | No | **MATCH** (optional both sides, unvalidated) | |
| 20 | `Evidence.snippet` | `str`, required, :81 | `string`, required, :26 | Yes, :140 | **MATCH** | |
| 21 | `Evidence.source_url` | `str`, required, :82 | `string`, required, :27 | Yes, :140 | **MATCH** | |
| 22 | `Evidence.source_title` | `str\|None`, :83 | optional, :28 | No | **MATCH** (unvalidated) | |
| 23 | `Evidence.source_domain` | `str\|None`, :84 | optional, :29 | No | **MATCH** (unvalidated) | |
| 24 | `Evidence.published_at` | `datetime\|None`, :85 | **absent** | No | **MISSING ON CLIENT** | Backend defines and can populate it (`services/retrieval.py:53`); the TS `Evidence` interface (`contract.ts:25-31`) has no such field at all — reading `evidence.published_at` in TS is a compile error even though it's on the wire. Not enforced by the runtime guard either way, so it fails silently rather than loudly. |
| 25 | `Evidence.relevance_score` | `float\|None`, :86-89 | optional, :30 | No | **MATCH** (unvalidated) | |
| 26 | `Assessment.claim_id` | `str`, required, :168 | `string`, required, :60 | Yes, :163 | **MATCH** | |
| 27 | `Assessment.status` | `AssessmentStatus` (5 values), :169 | union (5 values), :18-23, :61 | Yes, :164-165 | **MATCH** | |
| 28 | `Assessment.explanation` | `str`, required, :170 | `string`, required, :62 | Yes, :166 | **MATCH** | |
| 29 | `Assessment.confidence` | `float\|None`, :171 | optional, :63 | No | **MATCH** (unvalidated) | |
| 30 | `Assessment.citations` | `list[Citation]`, :172 | `Citation[]`, :64 | Yes, :167-168 | **MATCH** | |
| 31 | `Citation.snippet` | `str`, :156 | `string`, :54 | Yes (via `isCitation`), :144 | **MATCH** | |
| 32 | `Citation.source_url` | `str`, :157 | `string`, :55 | Yes, :144 | **MATCH** | |
| 33 | `Citation.source_title` | `str\|None`, :158 | optional, :56 | No | **MATCH** (unvalidated) | |

**Zero rows are `MISSING ON BACKEND`** — the backend never omits a field the guard
requires. **One row is `MISSING ON CLIENT`** (`Evidence.published_at`, row 24, already
flagged in `RECON.md` §4). **Four rows are `MISMATCH`** (rows 6, 10, 14, and the
looseness in row 15) — in every one of those cases the *backend* model is stricter than
what the *guard* actually enforces, meaning the guard would currently wave through a
malformed `confidence`, a malformed `errors[]` entry, or a claim missing
`checkworthiness` without ever telling the renderer something's wrong. None of these are
exploitable *today* because the backend is the only thing that ever produces this JSON —
but the guard's job (per its own doc comment, `contract.ts:180-183`, "malformed responses
must never reach the renderer") is only as strong as what it actually checks, and rows 6/10/14
are gaps in that promise, not the backend model.

---

## TASK B — the critical test

### Route taken, and why

`package.json:9` wires `vitest run` as `npm test`, so a JS/TS runner **is** configured —
per the task's own rule this means the honest route is "write a real test that imports
the actual guard," and that's what was done, in `src/shared/contract.test.ts` (existing
file, one `it()` added, quoted below). **I did not execute it.** This checkout has no
`node_modules/` (confirmed absent — `RECON.md` §2/§8 already noted this), and this task's
rules forbid installing anything, so `npm test` cannot run here. The real test is
therefore verified **by reading**, not by execution — run `npm install && npm test`
yourself to execute it for real.

To get an actual, executed PASS/FAIL today without installing anything, I additionally
wrote a **hand port** of the guard logic into Python — `backend/tests/test_ws3_envelope_guard_port.py`
— and ran it with `pytest`, which is already present in this environment with no install
needed (`python -m pytest` — `fastapi 0.137.1`, `pytest 9.0.3` already on `PATH`). **This
port is not the real guard.** Both are quoted below so you can diff them yourself.

### B.1 — the real test (unexecuted; added to `src/shared/contract.test.ts`)

```ts
  // WS3-CONTRACT-AUDIT Task B: a hand-built envelope (not derived from MOCK_ANALYSIS)
  // with TWO verifiedClaims, both assessment: null -- the shape run_ws5()/to_analysis_response()
  // produce on the pre-WS6 path (no `assessments` dict passed in). See WS3-CONTRACT-AUDIT.md.
  it("accepts two claims that both carry a null assessment", () => {
    const envelope = {
      schemaVersion: "1.0",
      url: "https://news.example.org/sg/two-claims-story",
      status: "complete",
      articleVerdict: {
        level: "unrated",
        summary: "Placeholder verdict pending WS6.",
        confidence: null,
      },
      verifiedClaims: [
        {
          claim: {
            id: "c1",
            text: "Singapore recorded 3,363 scam cases in 2025.",
            claim_type: "factual",
            checkworthiness: 0.82,
            rank: 1,
            search_query: null,
            evidence: [],
            char_start: null,
            char_end: null,
            prefix: null,
            suffix: null,
          },
          assessment: null,
        },
        {
          claim: {
            id: "c2",
            text: "Victims lost S$242.9 million last year.",
            claim_type: "factual",
            checkworthiness: 0.77,
            rank: 2,
            search_query: null,
            evidence: [],
            char_start: null,
            char_end: null,
            prefix: null,
            suffix: null,
          },
          assessment: null,
        },
      ],
      errors: [],
    };
    expect(isAnalysisResponse(envelope)).toBe(true);
  });
```

Worth noting: this exact scenario was *already* partially covered before this audit — the
pre-existing test right above it in the same file, `"accepts a null assessment"`
(`contract.test.ts:10-14`, unmodified), clones `MOCK_ANALYSIS` and asserts at least one
`assessment === null` claim passes. This audit's addition is the same claim but for a
**hand-built, minimal, two-claim** envelope rather than a mutated fixture, so it isn't
resting on whatever `MOCK_ANALYSIS` happens to contain.

### B.2 — the port (executed)

```python
CLAIM_TYPES = {"factual", "opinion", "prediction"}
ASSESSMENT_STATUSES = {"supported", "partially_supported", "contradicted", "needs_review", "opinion"}
ANALYSIS_STATUSES = {"complete", "processing", "failed", "skipped"}
VERDICT_LEVELS = {"trusted", "ok", "caution", "high_risk", "unrated"}

def is_record(v): return isinstance(v, dict)

def is_evidence(v):
    return is_record(v) and isinstance(v.get("snippet"), str) and isinstance(v.get("source_url"), str)

def is_citation(v):
    return is_record(v) and isinstance(v.get("snippet"), str) and isinstance(v.get("source_url"), str)

def is_claim(v):
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

def is_assessment(v):
    return (
        is_record(v)
        and isinstance(v.get("claim_id"), str)
        and isinstance(v.get("status"), str)
        and v.get("status") in ASSESSMENT_STATUSES
        and isinstance(v.get("explanation"), str)
        and isinstance(v.get("citations"), list)
        and all(is_citation(c) for c in v["citations"])
    )

def is_verified_claim(v):
    if not is_record(v) or not is_claim(v.get("claim")):
        return False
    has_key = "assessment" in v
    value = v.get("assessment")
    return (has_key and value is None) or is_assessment(value)

def is_analysis_response(v):
    if not is_record(v): return False
    if v.get("schemaVersion") != "1.0": return False
    if not isinstance(v.get("url"), str): return False
    if not isinstance(v.get("status"), str) or v.get("status") not in ANALYSIS_STATUSES: return False
    if not is_record(v.get("articleVerdict")): return False
    verdict = v["articleVerdict"]
    if not isinstance(verdict.get("level"), str) or verdict.get("level") not in VERDICT_LEVELS: return False
    if not isinstance(verdict.get("summary"), str): return False
    if not isinstance(v.get("verifiedClaims"), list) or not all(is_verified_claim(vc) for vc in v["verifiedClaims"]):
        return False
    return True
```

(Full file, with the same two-claim fixture as B.1 and two `pytest` test functions, is at
`backend/tests/test_ws3_envelope_guard_port.py`.)

### B.3 — a bug in the port itself, caught and fixed, worth reporting on its own

The **first** version of `is_verified_claim` I wrote was the "obvious" transliteration:

```python
def is_verified_claim(v):
    return (
        is_record(v)
        and is_claim(v.get("claim"))
        and (v.get("assessment") is None or is_assessment(v.get("assessment")))
    )
```

This is **wrong**, and running it caught the bug: Python's `dict.get("assessment")`
returns `None` both when the key holds an explicit `null` *and* when the key is absent
entirely. JS's `v.assessment === null` does not — a missing key reads as `undefined` in
JS, and `undefined === null` is `false`. So the naive port silently **accepts an envelope
with the `assessment` key missing altogether**, which the real TS guard would **reject**.
I added a second test (`test_missing_assessment_key_is_rejected_unlike_explicit_null`)
specifically to probe this, watched it fail against the naive port
(`AssertionError: assert True is False`), and fixed the port to check `"assessment" in v`
explicitly (shown above, B.2) before re-running. Both tests pass now (output below). This
is exactly the kind of drift the whole audit is about, just discovered inside my own port
instead of between the two real files — a good demonstration of why "porting a guard by
hand, in a different language, with no shared generator" is inherently risky, which is
also this task's Task C.

### B.4 — result

```
$ cd backend && python -m pytest tests/test_ws3_envelope_guard_port.py -v
tests/test_ws3_envelope_guard_port.py::test_two_null_assessment_claims_pass_the_ported_guard PASSED
tests/test_ws3_envelope_guard_port.py::test_missing_assessment_key_is_rejected_unlike_explicit_null PASSED
2 passed in 0.03s

$ cd backend && python -m pytest -q     # full existing suite, unaffected
87 passed in 1.45s
```

**PASS.** The scenario in the task (`schemaVersion "1.0"`, a real `url`, a `status`, a
placeholder `articleVerdict`, and `verifiedClaims` with two claims whose `assessment` is
`null`) satisfies `isAnalysisResponse()` — both by reading the guard's source (§A.1,
line 176: `v.assessment === null || isAssessment(v.assessment)` is an *explicit* pass
condition, not an oversight) and by the ported/executed test. There is no predicate that
rejects this envelope; the task's premise ("if it fails, identify...") doesn't apply here
— **no fix is proposed because none is needed for this scenario.**

The one real gap adjacent to this — flagged by the contract.py comment itself
(`contract.py:246-248`, quoted in §A.2) — is that this only holds while the backend keeps
serializing with `null`s intact. If `AnalysisResponse.model_dump(...)` were ever called
with `exclude_none=True` (it currently is not — `main.py:84` uses the default
`response_model=AnalysisResponse` with no `response_model_exclude_none`), the
`assessment` key would be **dropped from the JSON entirely** rather than sent as `null`,
and — per B.3 — the *real* TS guard (not my buggy first port) would then reject it,
because a missing key is not `null`. This is a real, live footgun documented in the code
but enforced by nothing except that comment; see the Task C table for how it could be
turned into an actual regression test on the backend side without touching
`services/`.

---

## TASK C — drift risk: field names duplicated across Python and TS with no generator

`src/shared/contract.ts:1-12` states this outright: *"Keep these shapes in sync by hand —
there is no codegen."* Below is every place a contract field/enum-value name is
duplicated as a bare string/identifier across a `.py` and a `.ts` file. "No generator"
means every row is a manual-sync obligation; a rename on one side that isn't mirrored on
the other either (a) fails loudly — TS compile error / Pydantic validation error — or
(b) fails silently, which is the dangerous half and is called out explicitly.

| Field / value family | Appears in (Python) | Appears in (TypeScript) | What breaks on an un-mirrored rename |
|---|---|---|---|
| Envelope keys: `schemaVersion`, `url`, `status`, `articleVerdict`, `verifiedClaims`, `errors` | `contract.py:241-249`; hardcoded again in `backend/tests/test_analyze.py:46-56` (`assert_passes_ws2_guard`) and in this audit's `backend/tests/test_ws3_envelope_guard_port.py` | `contract.ts:101-109`; hardcoded again in `contract.ts:184-203` (the guard itself) and `contract.test.ts` | **Silent.** Pydantic has no alias here — the Python attribute *is* `schemaVersion` (camelCase, deliberately, per the comment at `contract.py:191-193`) — so a Python-side rename changes the JSON key automatically, but the guard's string literals (`v.schemaVersion`, `v.status`, …) don't move with it. `isAnalysisResponse` returns `false`, the whole envelope is dropped, and (per `bootstrap.ts`'s catch block) the failure surfaces to the user as a generic "analysis failed" — no error naming the actual field. |
| `ArticleVerdictLevel` values: `trusted`/`ok`/`caution`/`high_risk`/`unrated` | `contract.py:207-211`; consumed as enum members (not raw strings) in `services/envelope.py`'s `article_verdict_for()` | `contract.ts:83-88` union; `VERDICT_LEVELS` Set, `contract.ts:127-133`; **also** `src/content/render/status-config.ts` keys its `STATUS` record and `dasfax-pill[data-level="..."]` CSS selectors (`shadow-root.ts:89-93`) off the same 5 strings | **Silent + visual.** A value renamed on the Python `StrEnum` only breaks `VERDICT_LEVELS.has(...)` (guard rejects) — loud. But if a *new* level were added to the Python enum without adding the matching CSS rule in `shadow-root.ts`/`status-config.ts` (not touched by this audit — Darren's renderer, out of scope to fix here), the guard would still pass it through and the pill would render with no color/dot styling for that state. This exact failure mode already happened once and was fixed in `5541991` when `unrated` was added to both. |
| `AssessmentStatus` values: `supported`/`partially_supported`/`contradicted`/`needs_review`/`opinion` | `contract.py:46-50`; string-matched in `app/clients/mock_assessor.py:63-71` (`AssessmentStatus.SUPPORTED`, etc., via enum — safe) | `contract.ts:18-23`; `ASSESSMENT_STATUSES` Set, `contract.ts:114-120`; **also** `status-config.ts`'s `STATUS` record keys and `highlights.ts`'s `::highlight(dasfax-${k})` CSS rule generation | Same shape as the row above — Python side is enum-safe internally, but the *string values* are what's actually duplicated in TS, twice (once in the guard, once in the renderer's status table), with no single source. |
| `Claim` fields: `id`, `text`, `claim_type`, `checkworthiness`, `rank`, `search_query`, `evidence`, `char_start`, `char_end`, `prefix`, `suffix` | `contract.py:99-119` | `contract.ts:33-51`; consumed again by field name in `src/content/render/panel.ts` (`claim.text`, `claim.rank`, `claim.evidence`, `claim.id`), `src/content/anchor/match-claim.ts` and `to-range.ts` (`char_start`/`char_end`/`prefix`/`suffix`), and `mock-response.ts`'s hardcoded fixture objects | **Mixed.** A rename of `char_start`/`char_end`/`prefix`/`suffix` specifically is the worst case: they're all-optional on both sides (row 18 in §A.3), so the *guard* won't catch a rename at all — `isClaim()` never looks at them. The failure would be silent and functional, not structural: WS2's anchor matcher (`match-claim.ts`) would just stop finding the fields, silently fall back to fuzzy text matching for every claim (per its own documented fallback behavior), and nobody would see an error — just steadily worse highlight placement. |
| `Evidence`/`Citation` fields: `snippet`, `source_url`, `source_title`, `source_domain`, `relevance_score`, (`published_at`, backend-only, row 24 in §A.3) | `contract.py:78-89`, `:153-158` | `contract.ts:25-31`, `:53-57`; also read by field name in `panel.ts`'s `renderSources()` (`c.source_title`, `c.source_url`, `e.source_domain`) | `snippet`/`source_url` are guard-checked (rows 20-21, 31-32) — a rename there is **loud** (guard rejects, envelope dropped). `source_title`/`source_domain`/`relevance_score` are unchecked — **silent**: `panel.ts` would just render `undefined` or fall back to its own `hostnameOf()` helper without any error. |
| `AnalysisRequest` fields — the request direction, **not covered by `isAnalysisResponse`** at all | `contract.py:229-235` — only `url`, `title`, `text`, `images` | `src/content/analysis-client.ts:15-31` — `url`, `title`, `text`, `images`, **plus `published_at` and `source_domain`, which the backend model does not declare** | **This is not a hypothetical drift risk — it is a live, already-occurring one.** `bootstrap.ts:50-59` sends `published_at` and `source_domain` in every `requestAnalysis()` call. Pydantic v2's default `extra` behavior is `"ignore"` (no `model_config` override in `AnalysisRequest`), so both fields are **silently dropped on arrival** — confirmed by reading `main.py:113-121`, which builds `ArticleInput` from `request.url`/`.title`/`.text` plus a **server-derived** `source_domain` (`_domain_of(request.url)`, `main.py:76-81`) and never reads `request.published_at` at all. The consequence: `ArticleInput.published_at` (`contract.py:65`) — a real, typed field WS5 could use — is **structurally unreachable** from the extension today, even though Bryan's extractor computes it (`article-extractor.ts:88-94`, `normalizeToIso8601`) and sends it. `images` (`contract.py:235`) is at least documented as currently unused ("carried for WS6"); `published_at` is not documented as intentionally dropped anywhere — it reads as an oversight, not a decision. |

### The general pattern

Every "silent" row above shares one root cause: **`isAnalysisResponse()` only validates
the fields WS2 actually reads today** (its own file header says so, `contract.ts:5-7`).
That's a reasonable scoping choice for a hand-written guard, but it means the guard is
not a substitute for a schema — it's a targeted trip-wire around today's renderer, and
every field outside that blast radius (confidence scores, citations' titles, anchor
offsets, the entire request direction) can drift with no automated signal on either side.
Nothing under `backend/app/services/` needs to change to fix this class of bug — the fix
is a build-time or CI-time step (e.g. generate `contract.ts` from `contract.py` via
`pydantic`'s JSON-schema export, or add a schema-diff test to CI, which per `RECON.md`
§8 does not exist yet at all) — which is why it's written up here rather than done.

---

## Recommended fixes (write-up only, per scope — none of these were applied)

1. **Wire `AnalysisRequest.published_at` / drop or document `source_domain`.** Either add
   `published_at: datetime | None` to `backend/app/models/contract.py`'s
   `AnalysisRequest` (Task C, last row) and read it in `main.py:116-121`, or, if the
   server-derived `source_domain` is intentionally authoritative over the client's guess,
   say so in a comment the way `images` already does — right now it just looks
   unfinished. This only touches `contract.py` and `main.py`, not `services/`.
2. **Add `published_at` to `src/shared/contract.ts`'s `Evidence` interface** (§A.3 row
   24) so the TS type isn't quietly incomplete relative to the wire format.
3. **Give the `isAnalysisResponse` guard a companion `isAnalysisError()` check** and call
   it for every entry of `errors[]` (§A.3 row 10), and check `articleVerdict.confidence`
   is `number | undefined` (row 6) and `Claim.checkworthiness` is present and numeric
   (row 14) — these are one-line additions each, all in `contract.ts`'s existing
   guard-helper style, and would close the gap between "the guard passes this" and "the
   TS types this codebase relies on elsewhere are actually satisfied."
4. **Add a backend regression test that would have caught B.4's footgun mechanically**:
   assert that `AnalysisResponse(...).model_dump(mode="json")` (built with at least one
   `assessment=None`) contains the literal key `"assessment"` mapped to `None`, not an
   absent key — i.e. lock in "never call this with `exclude_none=True`" as an executable
   check next to the comment at `contract.py:246-248`, rather than relying on the comment
   alone. This can live in `backend/tests/` without touching `services/`.
