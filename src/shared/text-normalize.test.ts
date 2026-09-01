import { describe, expect, it } from "vitest";
import { normalizeText, tokenize } from "./text-normalize";

describe("normalizeText", () => {
  it("collapses every run of whitespace to a single space", () => {
    expect(normalizeText("a\n\n  b\t c   d")).toBe("a b c d");
  });

  it("treats NBSP and other unicode spaces as whitespace", () => {
    expect(normalizeText("S$242.9 million per year")).toBe(
      "S$242.9 million per year",
    );
  });

  it("unifies smart quotes", () => {
    expect(normalizeText("“fake news” and ‘deepfakes’")).toBe(
      '"fake news" and \'deepfakes\'',
    );
  });

  it("unifies dashes to a hyphen", () => {
    expect(normalizeText("2024–2025 — up sharply")).toBe(
      "2024-2025 - up sharply",
    );
  });

  it("expands an ellipsis character to three dots", () => {
    expect(normalizeText("wait… what")).toBe("wait... what");
  });

  it("strips zero-width and soft-hyphen characters", () => {
    expect(normalizeText("de­ep​fake")).toBe("deepfake");
  });

  it("applies NFKC (compatibility forms fold together)", () => {
    // U+FB01 LATIN SMALL LIGATURE FI -> "fi"
    expect(normalizeText("ﬁnancial")).toBe("financial");
  });

  it("case-folds only when asked", () => {
    expect(normalizeText("Singapore Police Force")).toBe("Singapore Police Force");
    expect(normalizeText("Singapore Police Force", { caseFold: true })).toBe(
      "singapore police force",
    );
  });
});

describe("tokenize", () => {
  it("splits on non-alphanumerics and case-folds", () => {
    expect(tokenize("S$4.9 million — deepfake Zoom call")).toEqual([
      "s",
      "4",
      "9",
      "million",
      "deepfake",
      "zoom",
      "call",
    ]);
  });

  it("returns an empty array for punctuation-only input", () => {
    expect(tokenize("—  …  -")).toEqual([]);
  });
});
