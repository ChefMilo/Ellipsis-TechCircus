/**
 * Hardcoded AnalysisResponse used when config.useFixture is true (the default). Lets
 * the whole extension <-> backend message hop, and Darren's DOM anchoring, be exercised
 * end to end with zero backend running.
 *
 * Every field name and shape below is copied from src/shared/contract.ts and from the
 * bundled dev-harness fixture at src/content/render/__mock__/mock-response.ts (WS2's
 * own MOCK_ANALYSIS) -- nothing here is invented.
 *
 * Unlike MOCK_ANALYSIS (which gives each claim a different assessed status, to exercise
 * every rendering treatment), every claim here has assessment: null and the article
 * verdict is "unrated". That's deliberate: it's the exact pre-WS6 shape
 * `to_analysis_response()` produces on the backend when it's called with no
 * `assessments` dict (backend/app/services/envelope.py; docs/ws3/WS3-CONTRACT-AUDIT.md, Task A
 * table row 9) -- so this fixture proves the message hop carries that shape correctly,
 * independent of whether WS6 assessment has run.
 *
 * f1 and f2 carry real, non-null char_start/char_end/prefix/suffix -- hand-derived
 * against test/fixtures/gnarly-article.html's DOM per src/content/anchor/text-index.ts's
 * flattening rules (full by-hand derivation, plus an executed proof, in
 * docs/ws3/WS3-MESSAGE-HOP.md and src/background/fixture.test.ts). f2 anchors via an exact
 * substring match. f1 deliberately does NOT: its sentence in gnarly-article.html
 * contains an inline `<sup><a>1</a></sup>` footnote marker mid-sentence
 * ("...artificial intelligence<sup>1</sup> was used...") that text-index.ts's raw DOM
 * flattening includes but this claim's `text` does not, so the exact-match path finds
 * zero hits and match-claim.ts's fuzzy Dice-coefficient fallback has to do the work
 * instead. f1's char_start/char_end/prefix/suffix are populated the same as f2's, but
 * (correctly) go unused by the matcher in this specific case: matchClaim() only ever
 * reads a claim's hints inside scoreExactCandidate(), which only runs when a claim's
 * exact text has *multiple* hits in the page to disambiguate between -- not on the
 * fuzzy path. f3 and f4 are left with null anchors (like every claim in MOCK_ANALYSIS)
 * so the fixture also still covers the no-hint path.
 */
import type { AnalysisResponse } from "../shared/contract";

export function buildFixtureResponse(url: string): AnalysisResponse {
  return {
    schemaVersion: "1.0",
    url,
    status: "complete",
    articleVerdict: {
      level: "unrated",
      summary:
        "Fixture data from the extension's local message-hop stub -- claims were extracted but not yet assessed.",
    },
    verifiedClaims: [
      {
        claim: {
          id: "f1",
          text:
            "In one widely reported case, a Singapore businessman lost S$4.9 million after joining a Zoom call in which artificial intelligence was used to fabricate the likenesses of senior officials.",
          claim_type: "factual",
          checkworthiness: 0.92,
          rank: 1,
          search_query: null,
          evidence: [],
          // Hand-derived against gnarly-article.html; see docs/ws3/WS3-MESSAGE-HOP.md.
          // Deliberately exercises the FUZZY anchoring path (see file doc comment).
          char_start: 317,
          char_end: 505,
          prefix: "S$72,229 per victim. ",
          suffix: " The Singapore Police Force",
        },
        assessment: null,
      },
      {
        claim: {
          id: "f2",
          text:
            "Victims lost a total of S$242.9 million to these scams last year, with an average loss of S$72,229 per victim.",
          claim_type: "factual",
          checkworthiness: 0.76,
          rank: 2,
          search_query: null,
          evidence: [],
          // Hand-derived against gnarly-article.html; see docs/ws3/WS3-MESSAGE-HOP.md.
          // Exercises the EXACT-match anchoring path.
          char_start: 206,
          char_end: 316,
          prefix: "1,504 cases in 2024. ",
          suffix: " In one widely reported case,",
        },
        assessment: null,
      },
      {
        claim: {
          id: "f3",
          text:
            "The Singapore Police Force warned that deepfake AI fabrications can be sophisticated and difficult to distinguish from authentic content.",
          claim_type: "factual",
          checkworthiness: 0.54,
          rank: 3,
          search_query: null,
          evidence: [],
          // No hint on purpose: also covers the pre-existing no-anchor-fields path
          // (this is what every claim in MOCK_ANALYSIS looks like).
          char_start: null,
          char_end: null,
          prefix: null,
          suffix: null,
        },
        assessment: null,
      },
      {
        claim: {
          id: "f4",
          text:
            "Honestly, this is the most alarming trend I have seen in years, and the authorities should do much more to stop it.",
          claim_type: "opinion",
          checkworthiness: 0.12,
          rank: 4,
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
}
