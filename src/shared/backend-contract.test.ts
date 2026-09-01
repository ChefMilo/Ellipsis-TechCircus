/**
 * Contract-drift guard for the extension <-> backend seam.
 *
 * The fixtures in test/fixtures/analyze-*.json are REAL responses captured from
 * `POST /analyze` (see backend/app/pipeline/analyze.py), not hand-written shapes.
 * That is the point: the renderer rejects anything `isAnalysisResponse` doesn't
 * accept, so a backend field rename shows up to a user as "malformed analysis
 * response" and to a developer as nothing at all. These tests make it show up in CI.
 *
 * Re-capture with the backend running:
 *   curl -s -X POST localhost:8000/analyze -H 'Content-Type: application/json' \
 *        -d @payload.json > test/fixtures/analyze-escalated.json
 */
import { describe, expect, it } from "vitest";
import { isAnalysisResponse, type AnalysisResponse } from "./contract";

import notEscalated from "../../test/fixtures/analyze-not-escalated.json";
import escalated from "../../test/fixtures/analyze-escalated.json";

describe("real backend responses satisfy the client contract", () => {
  it("accepts a page that stopped at Tier 2", () => {
    expect(isAnalysisResponse(notEscalated)).toBe(true);
  });

  it("accepts a page that escalated to Tier 3", () => {
    expect(isAnalysisResponse(escalated)).toBe(true);
  });

  it("returns no claims when Tier 2 stops, and says so in the verdict", () => {
    const r = notEscalated as unknown as AnalysisResponse;
    expect(r.status).toBe("complete");
    expect(r.articleVerdict.level).toBe("ok");
    expect(r.verifiedClaims).toHaveLength(0);
    expect(r.tier2?.escalated).toBe(false);
  });

  it("returns claims when Tier 2 escalates", () => {
    const r = escalated as unknown as AnalysisResponse;
    expect(r.tier2?.escalated).toBe(true);
    expect(r.verifiedClaims.length).toBeGreaterThan(0);
    expect(r.articleVerdict.level).toMatch(/caution|high_risk/);
  });

  it("keeps `assessment` an explicit null while WS6 does not exist", () => {
    // isVerifiedClaim tests `assessment === null`, so omitting the key entirely
    // would make the guard reject every claim. This is why /analyze must NOT use
    // response_model_exclude_none.
    const r = escalated as unknown as AnalysisResponse;
    for (const vc of r.verifiedClaims) {
      expect(vc).toHaveProperty("assessment");
      expect(vc.assessment).toBeNull();
    }
  });

  it("carries the claim fields WS2 needs to anchor text in the DOM", () => {
    const r = escalated as unknown as AnalysisResponse;
    for (const { claim } of r.verifiedClaims) {
      expect(typeof claim.id).toBe("string");
      expect(claim.text.length).toBeGreaterThan(0);
      expect(claim.claim_type).toBe("factual");
    }
  });
});
