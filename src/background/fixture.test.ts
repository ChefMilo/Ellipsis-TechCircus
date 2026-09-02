// @vitest-environment jsdom
/**
 * Executed proof for the claims made in fixture.ts's doc comment and in
 * docs/ws3/WS3-MESSAGE-HOP.md's by-hand offset derivation -- don't just trust the hand
 * arithmetic, run it through Darren's real anchoring code (src/content/anchor/) against
 * the repo's own gnarly-article.html fixture, the same way
 * src/content/anchor/fixtures.test.ts already does for MOCK_ANALYSIS.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { isAnalysisResponse } from "../shared/contract";
import { rangesForClaims } from "../content/anchor/to-range";
import { buildFixtureResponse } from "./fixture";

const GNARLY_HTML = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../../test/fixtures/gnarly-article.html"),
  "utf-8",
);

const FIXTURE_URL = "https://example.com/some-real-article";
const fixture = buildFixtureResponse(FIXTURE_URL);

describe("buildFixtureResponse", () => {
  it("satisfies isAnalysisResponse()", () => {
    expect(isAnalysisResponse(fixture)).toBe(true);
  });

  it("echoes the requested url", () => {
    expect(fixture.url).toBe(FIXTURE_URL);
  });

  it("has status complete and articleVerdict.level unrated", () => {
    expect(fixture.status).toBe("complete");
    expect(fixture.articleVerdict.level).toBe("unrated");
  });

  it("has assessment: null on every claim", () => {
    expect(fixture.verifiedClaims.length).toBeGreaterThan(0);
    for (const vc of fixture.verifiedClaims) {
      expect(vc.assessment).toBeNull();
    }
  });

  it("has at least two claims with populated char_start/char_end/prefix/suffix", () => {
    const populated = fixture.verifiedClaims.filter(
      (vc) =>
        typeof vc.claim.char_start === "number" &&
        typeof vc.claim.char_end === "number" &&
        typeof vc.claim.prefix === "string" &&
        typeof vc.claim.suffix === "string",
    );
    expect(populated.length).toBeGreaterThanOrEqual(2);
  });

  it("f2's char_start/char_end are the exact span of its own claim text", () => {
    const f2 = fixture.verifiedClaims.find((vc) => vc.claim.id === "f2")!.claim;
    // Confirms the by-hand offsets in docs/ws3/WS3-MESSAGE-HOP.md are at minimum
    // self-consistent with the claim text they claim to span.
    expect(f2.char_end! - f2.char_start!).toBe(f2.text.length);
  });

  it("anchors f1 and f2 against gnarly-article.html -- f2 exactly, f1 via fuzzy fallback", () => {
    document.body.innerHTML = GNARLY_HTML;
    const { ranges, matches, unanchored } = rangesForClaims(fixture.verifiedClaims, {
      root: document.body,
    });

    expect(unanchored).not.toContain("f1");
    expect(unanchored).not.toContain("f2");
    expect(ranges.has("f1")).toBe(true);
    expect(ranges.has("f2")).toBe(true);

    // f2's sentence has no inline markup inside it in gnarly-article.html, so it must
    // resolve via the EXACT path (score === 1, exact === true).
    expect(matches.get("f2")?.exact).toBe(true);

    // f1's sentence contains gnarly-article.html's inline <sup><a>1</a></sup> footnote
    // marker between "intelligence" and "was" -- the live DOM text is
    // "...intelligence1 was..." but this claim's `text` says "...intelligence was...",
    // so there is NO exact substring match and the fuzzy path must be what found it.
    expect(matches.get("f1")?.exact).toBe(false);
    expect(matches.get("f1")!.score).toBeGreaterThanOrEqual(0.72); // match-claim.ts's DEFAULT_THRESHOLD
  });

  it("never anchors into the nav or the related-articles list", () => {
    document.body.innerHTML = GNARLY_HTML;
    const { ranges } = rangesForClaims(fixture.verifiedClaims, { root: document.body });
    for (const range of ranges.values()) {
      const container =
        range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
          ? (range.commonAncestorContainer as Element)
          : range.commonAncestorContainer.parentElement;
      expect(container?.closest("nav, aside.related")).toBeNull();
    }
  });
});
