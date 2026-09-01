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
