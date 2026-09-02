import { describe, expect, it } from "vitest";
import { isAnalysisResponse } from "./contract";
import { MOCK_ANALYSIS } from "../content/render/__mock__/mock-response";

describe("isAnalysisResponse", () => {
  it("accepts the bundled mock", () => {
    expect(isAnalysisResponse(MOCK_ANALYSIS)).toBe(true);
  });

  it("accepts a null assessment", () => {
    const r = structuredClone(MOCK_ANALYSIS);
    expect(r.verifiedClaims.some((vc) => vc.assessment === null)).toBe(true);
    expect(isAnalysisResponse(r)).toBe(true);
  });

  // WS3-CONTRACT-AUDIT Task B: a hand-built envelope (not derived from MOCK_ANALYSIS)
  // with TWO verifiedClaims, both assessment: null -- the shape run_ws5()/to_analysis_response()
  // produce on the pre-WS6 path (no `assessments` dict passed in). See docs/ws3/WS3-CONTRACT-AUDIT.md.
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

  it("accepts the unrated verdict level", () => {
    const r = structuredClone(MOCK_ANALYSIS);
    r.articleVerdict = { level: "unrated", summary: "Nothing on this page was checked." };
    expect(isAnalysisResponse(r)).toBe(true);
  });

  it("rejects a wrong schemaVersion", () => {
    const r = { ...structuredClone(MOCK_ANALYSIS), schemaVersion: "2.0" };
    expect(isAnalysisResponse(r)).toBe(false);
  });

  it("rejects an unknown status enum", () => {
    const r = structuredClone(MOCK_ANALYSIS);
    (r.verifiedClaims[0]!.assessment as { status: string }).status = "true";
    expect(isAnalysisResponse(r)).toBe(false);
  });

  it("rejects a missing verifiedClaims array", () => {
    const r = structuredClone(MOCK_ANALYSIS) as unknown as Record<string, unknown>;
    delete r.verifiedClaims;
    expect(isAnalysisResponse(r)).toBe(false);
  });

  it("rejects non-objects", () => {
    expect(isAnalysisResponse(null)).toBe(false);
    expect(isAnalysisResponse("{}")).toBe(false);
    expect(isAnalysisResponse(42)).toBe(false);
  });
});
