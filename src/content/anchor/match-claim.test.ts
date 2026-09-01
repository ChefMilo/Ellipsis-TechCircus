// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import type { Claim } from "../../shared/contract";
import { buildTextIndex } from "./text-index";
import { matchClaim } from "./match-claim";

function claim(text: string, extra: Partial<Claim> = {}): Claim {
  return {
    id: "c1",
    text,
    claim_type: "factual",
    checkworthiness: 0.8,
    rank: 1,
    evidence: [],
    ...extra,
  };
}

function indexOf(html: string) {
  document.body.innerHTML = html;
  return buildTextIndex(document.body);
}

describe("matchClaim", () => {
  it("finds an exact sentence and returns its span", () => {
    const idx = indexOf(
      `<p>Intro sentence.</p><p>Victims lost S$242.9 million last year.</p>`,
    );
    const m = matchClaim(idx, claim("Victims lost S$242.9 million last year."));
    expect(m).not.toBeNull();
    expect(m!.exact).toBe(true);
    expect(idx.flat.slice(m!.startOffset, m!.endOffset)).toBe(
      "Victims lost S$242.9 million last year.",
    );
  });

  it("matches despite whitespace and smart-quote differences", () => {
    const idx = indexOf(
      `<p>The police said\n  “deepfake” video calls   drove the rise.</p>`,
    );
    const m = matchClaim(idx, claim('The police said "deepfake" video calls drove the rise.'));
    expect(m).not.toBeNull();
    expect(m!.exact).toBe(true);
  });

  it("matches a sentence split across an inline link", () => {
    const idx = indexOf(
      `<p>Victims lost <a href="/r">S$242.9 million</a> to these scams last year.</p>`,
    );
    const m = matchClaim(idx, claim("Victims lost S$242.9 million to these scams last year."));
    expect(m?.exact).toBe(true);
  });

  it("disambiguates a repeated sentence using the prefix hint", () => {
    const dup = "Singapore recorded 3,363 cases in 2025.";
    const idx = indexOf(
      `<p>Early on: ${dup} More detail follows.</p>` +
        `<p>To recap the headline figure: ${dup} That concludes it.</p>`,
    );
    const withHint = matchClaim(
      idx,
      claim(dup, { prefix: "To recap the headline figure: " }),
    );
    const firstAt = idx.flat.indexOf(dup);
    const secondAt = idx.flat.indexOf(dup, firstAt + 1);
    expect(withHint?.startOffset).toBe(secondAt);
  });

  it("falls back to fuzzy matching when the text is lightly reworded", () => {
    const idx = indexOf(
      `<p>The Singapore Police Force warned that deepfake AI fabrications can be very sophisticated and hard to distinguish from authentic content.</p>`,
    );
    const m = matchClaim(
      idx,
      claim(
        "The Singapore Police Force warned that deepfake AI fabrications can be sophisticated and difficult to distinguish from authentic content.",
      ),
    );
    expect(m).not.toBeNull();
    expect(m!.exact).toBe(false);
    expect(m!.score).toBeGreaterThanOrEqual(0.72);
  });

  it("returns null for a claim that is not on the page", () => {
    const idx = indexOf(`<p>An article about entirely unrelated matters.</p>`);
    const m = matchClaim(
      idx,
      claim("The Monetary Authority of Singapore froze 12,000 bank accounts in March 2025."),
    );
    expect(m).toBeNull();
  });
});
