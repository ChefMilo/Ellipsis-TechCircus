// @vitest-environment jsdom
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { MOCK_ANALYSIS } from "../render/__mock__/mock-response";
import { rangesForClaims } from "./to-range";

const FIXTURE_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../test/fixtures",
);

const htmlFiles = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith(".html"));

/**
 * Every claim in the mock except c7 (which is deliberately absent) is a sentence
 * that appears — in some split/whitespace-mangled form — in each fixture.
 */
const EXPECTED_ANCHORED = MOCK_ANALYSIS.verifiedClaims
  .map((vc) => vc.claim.id)
  .filter((id) => id !== "c7");

describe.each(htmlFiles)("anchoring against fixture %s", (file) => {
  const html = readFileSync(join(FIXTURE_DIR, file), "utf-8");

  it("anchors every present claim and reports the absent one", () => {
    document.body.innerHTML = html;
    const { ranges, unanchored } = rangesForClaims(MOCK_ANALYSIS.verifiedClaims, {
      root: document.body,
    });

    for (const id of EXPECTED_ANCHORED) {
      expect(ranges.has(id), `${file}: claim ${id} should anchor`).toBe(true);
    }
    expect(unanchored).toContain("c7");
  });

  it("never anchors into the nav or the related-articles list", () => {
    document.body.innerHTML = html;
    const { ranges } = rangesForClaims(MOCK_ANALYSIS.verifiedClaims, {
      root: document.body,
    });
    for (const range of ranges.values()) {
      const container =
        range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
          ? (range.commonAncestorContainer as Element)
          : range.commonAncestorContainer.parentElement;
      expect(container?.closest("nav, aside.related")).toBeNull();
    }
  });
});
