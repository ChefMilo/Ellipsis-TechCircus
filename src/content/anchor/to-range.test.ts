// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { VerifiedClaim } from "../../shared/contract";
import { buildTextIndex } from "./text-index";
import { buildBlockMap, spanToRange, rangesForClaims } from "./to-range";
import { matchClaim } from "./match-claim";

function vc(id: string, text: string): VerifiedClaim {
  return {
    claim: {
      id,
      text,
      claim_type: "factual",
      checkworthiness: 0.8,
      rank: 1,
      evidence: [],
    },
    assessment: null,
  };
}

describe("spanToRange", () => {
  it("produces a Range whose text equals the matched claim", () => {
    document.body.innerHTML = `<p>Lead in.</p><p>Victims lost S$242.9 million last year.</p>`;
    const idx = buildTextIndex(document.body);
    const blockMap = buildBlockMap(idx);
    const m = matchClaim(idx, vc("c1", "Victims lost S$242.9 million last year.").claim)!;
    const res = spanToRange(idx, blockMap, m.startOffset, m.endOffset);
    expect(res).not.toBeNull();
    expect(res!.range.toString().replace(/\s+/g, " ").trim()).toBe(
      "Victims lost S$242.9 million last year.",
    );
    expect(res!.clamped).toBe(false);
  });

  it("clamps a span that would cross a paragraph boundary", () => {
    document.body.innerHTML =
      `<p>end of the first paragraph here</p><p>start of the second paragraph here</p>`;
    const idx = buildTextIndex(document.body);
    const blockMap = buildBlockMap(idx);
    // Hand-craft a span straddling both <p> blocks.
    const start = idx.flat.indexOf("first paragraph here");
    const end = idx.flat.indexOf("start of") + "start of".length;
    const res = spanToRange(idx, blockMap, start, end);
    expect(res).not.toBeNull();
    expect(res!.clamped).toBe(true);
    // Result stays inside one paragraph.
    expect(res!.range.toString()).not.toContain("start of");
  });
});

describe("rangesForClaims", () => {
  it("anchors the claims it can and reports the rest as unanchored", () => {
    document.body.innerHTML = `
      <article>
        <p>Victims lost S$242.9 million last year.</p>
        <p>According to a study, 79 per cent of Singaporeans are confident.</p>
      </article>`;
    const claims = [
      vc("c1", "Victims lost S$242.9 million last year."),
      vc("c2", "According to a study, 79 per cent of Singaporeans are confident."),
      vc("c3", "A completely absent claim about frozen bank accounts."),
    ];
    const { ranges, unanchored } = rangesForClaims(claims, { root: document.body });
    expect([...ranges.keys()].sort()).toEqual(["c1", "c2"]);
    expect(unanchored).toEqual(["c3"]);
  });
});
