/**
 * The one fixed mock the dev harness (`?dasfaxMock=1`) and the panel/anchor
 * tests run against.
 *
 * The five `claim` objects are verbatim WS5 output — produced by
 * `cd backend && python run_demo.py` over `tests/fixtures/sample_article.txt`
 * (mock LLM + mock search). The `assessment` objects are hand-authored to cover
 * every rendering path:
 *
 *   c1  contradicted           (+ citations)
 *   c2  partially_supported    (+ citations)
 *   c3  supported              (+ citations)
 *   c5  assessment === null    -> "not yet verified" treatment
 *   c6  opinion                (real opinion sentence from the article)
 *   c7  needs_review + NOT present in the article text -> exercises the
 *       "unanchored, still shown" path
 *
 * `sample_article.txt` is the canonical fixture; every claim except c7 is a
 * verbatim sentence from it, so the fuzzy matcher can be tested end-to-end.
 */
import type { AnalysisResponse } from "../../../shared/contract";

export const MOCK_ANALYSIS: AnalysisResponse = {
  schemaVersion: "1.0",
  url: "https://news.example.org/sg/scam-losses-2025",
  status: "complete",
  articleVerdict: {
    level: "caution",
    summary:
      "This article mixes well-supported figures with at least one claim that retrieved sources dispute. Expand each highlight to see the evidence.",
    confidence: 0.61,
  },
  verifiedClaims: [
    {
      claim: {
        id: "c1",
        text: "In one widely reported case, a Singapore businessman lost S$4.9 million after joining a Zoom call in which artificial intelligence was used to fabricate the likenesses of senior officials.",
        claim_type: "factual",
        checkworthiness: 0.92,
        rank: 1,
        search_query:
          "In one widely reported case, a Singapore businessman lost S$4.9 million after joining a Zoom call in which artificial intelligence was used to fabricate the likenesses of senior officials.",
        evidence: [
          {
            snippet:
              "One analysis disputes that a Singapore businessman lost S$4.9 million after joining a deepfake Zoom call; the reported figure elsewhere is S$3.8 million.",
            source_url: "https://press.example.org/article/3435",
            source_title: "Regional Press Pool",
            source_domain: "press.example.org",
            relevance_score: 1.0,
          },
        ],
      },
      assessment: {
        claim_id: "c1",
        status: "contradicted",
        explanation:
          "Retrieved reporting describes the same incident but puts the loss at about S$3.8 million, not S$4.9 million, and does not confirm that the fabricated likenesses were of senior officials.",
        confidence: 0.58,
        citations: [
          {
            snippet:
              "One analysis disputes that a Singapore businessman lost S$4.9 million after joining a deepfake Zoom call; the reported figure elsewhere is S$3.8 million.",
            source_url: "https://press.example.org/article/3435",
            source_title: "Regional Press Pool",
          },
        ],
      },
    },
    {
      claim: {
        id: "c2",
        text: "According to a study, 79 per cent of Singaporeans are confident they can spot fake news.",
        claim_type: "factual",
        checkworthiness: 0.92,
        rank: 2,
        search_query:
          "According to a study, 79 per cent of Singaporeans are confident they can spot fake news.",
        evidence: [
          {
            snippet:
              "A 2018 study found 79% of Singaporeans were somewhat or very confident they could spot fake news, though 91% failed a subsequent test.",
            source_url: "https://archive.example.org/article/9301",
            source_title: "National Records Archive",
            source_domain: "archive.example.org",
            relevance_score: 1.0,
          },
        ],
      },
      assessment: {
        claim_id: "c2",
        status: "partially_supported",
        explanation:
          "The 79% confidence figure matches a 2018 study, but the article omits the same study's finding that most respondents still failed the test — so the sentence is accurate but missing important context.",
        confidence: 0.74,
        citations: [
          {
            snippet:
              "A 2018 study found 79% of Singaporeans were somewhat or very confident they could spot fake news, though 91% failed a subsequent test.",
            source_url: "https://archive.example.org/article/9301",
            source_title: "National Records Archive",
          },
        ],
      },
    },
    {
      claim: {
        id: "c3",
        text: "Victims lost a total of S$242.9 million to these scams last year, with an average loss of S$72,229 per victim.",
        claim_type: "factual",
        checkworthiness: 0.76,
        rank: 3,
        search_query:
          "Victims lost a total of S$242.9 million to these scams last year, with an average loss of S$72,229 per victim.",
        evidence: [
          {
            snippet:
              "Official figures put total losses to government-official-impersonation scams at S$242.9 million, averaging S$72,229 per victim.",
            source_url: "https://factcheck.example.org/article/2110",
            source_title: "Fact Check SG",
            source_domain: "factcheck.example.org",
            relevance_score: 0.94,
          },
        ],
      },
      assessment: {
        claim_id: "c3",
        status: "supported",
        explanation:
          "Both the total loss (S$242.9 million) and the per-victim average (S$72,229) match official figures in the retrieved sources.",
        confidence: 0.91,
        citations: [
          {
            snippet:
              "Official figures put total losses to government-official-impersonation scams at S$242.9 million, averaging S$72,229 per victim.",
            source_url: "https://factcheck.example.org/article/2110",
            source_title: "Fact Check SG",
          },
        ],
      },
    },
    {
      claim: {
        id: "c5",
        text: "The Singapore Police Force warned that deepfake AI fabrications can be sophisticated and difficult to distinguish from authentic content.",
        claim_type: "factual",
        checkworthiness: 0.54,
        rank: 5,
        search_query:
          "The Singapore Police Force warned that deepfake AI fabrications can be sophisticated and difficult to distinguish from authentic content.",
        evidence: [],
      },
      assessment: null,
    },
    {
      claim: {
        id: "c6",
        text: "Honestly, this is the most alarming trend I have seen in years, and the authorities should do much more to stop it.",
        claim_type: "opinion",
        checkworthiness: 0.12,
        rank: 6,
        search_query: null,
        evidence: [],
      },
      assessment: {
        claim_id: "c6",
        status: "opinion",
        explanation:
          "This sentence expresses the writer's personal view and a recommendation. There is no factual assertion here to verify.",
        citations: [],
      },
    },
    {
      claim: {
        id: "c7",
        text: "The Monetary Authority of Singapore froze more than 12,000 bank accounts linked to these syndicates in March 2025.",
        claim_type: "factual",
        checkworthiness: 0.83,
        rank: 4,
        search_query:
          "Monetary Authority of Singapore froze 12,000 bank accounts scam syndicates March 2025",
        evidence: [],
      },
      assessment: {
        claim_id: "c7",
        status: "needs_review",
        explanation:
          "No retrieved source confirms or denies that 12,000 accounts were frozen in March 2025. Treating this as unverified rather than guessing.",
        confidence: 0.2,
        citations: [],
      },
    },
  ],
};
